"""Audit counterexamples: end-to-end release invariants on synthetic data."""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from catchup_core import coordinator as co, crypto, domain_pack as dp, ids, ledger as lg
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CatchupRequest, ExecutionMode, RunStatus
from catchup_core.extract import NormalizedSignal, parse_output
from catchup_core.runtime import _backup_for, make_file_publisher
from catchup_core.scorecard import score

NOW = datetime(2026, 10, 12, 16, tzinfo=timezone.utc)
PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs/kids_school.yaml")
KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))


def extract(prompt):
    text = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
    for value in ("2026-10-14", "2026-10-16"):
        if value in text:
            i = text.index(value)
            return json.dumps({"complete": True, "candidates": [{"predicate": "exam_registration_deadline",
                "subject_id": "kid1", "value": value, "quote": value, "span": [i, i + 10]}]})
    return '{"complete": true, "candidates": []}'


@pytest.fixture
def world(tmp_path):
    led = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER).open()
    led.migrate()
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    texts = []

    def deps(records=()):
        return co.Deps(ledger=led, pack=PACK, blobs=blobs, id_key=KEY,
            fetchers={"school_email": lambda: co.ConnectorFetch("school_email", "acct", tuple(records)),
                      "calendar": lambda: co.ConnectorFetch("calendar", "acct")},
            extractor=extract, subjects=["kid1"], viewer="owner", now=lambda: NOW,
            source_class={"school_email": "school_email", "calendar": "calendar"},
            publisher=lambda cid, text, version: texts.append(text))
    yield led, blobs, deps, texts
    led.close()


def request(cid="cu_a", **kw):
    return CatchupRequest(cid, "owner", "cli", host="test-host", **kw)


def test_unrelated_quote_cannot_support_a_date():
    raw = {"predicate": "exam_registration_deadline", "subject_id": "kid1",
           "value": "2026-10-14", "quote": "Lunch menu", "span": [0, 10]}
    result = parse_output(json.dumps({"complete": True, "candidates": [raw]}), PACK,
                          NormalizedSignal("s", "school_email", "Lunch menu"), ["kid1"])
    assert not result.candidates and result.rejections


def test_changed_content_same_provider_version_keeps_immutable_evidence(world):
    led, blobs, deps, _ = world
    co.run_catchup(request(), deps([co.ConnectorRecord("m1", "2026-10-14", "v1")]))
    original = led.conn.execute("SELECT evidence_blob_ref FROM signals").fetchone()[0]
    co.run_catchup(request("cu_b"), deps([co.ConnectorRecord("m1", "2026-10-16", "v1")]))
    assert led.conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 2
    assert blobs.get(original) == "2026-10-14"
    assert led.facts_matching("kids_school", "exam_registration_deadline", "kid1")[0].value == "2026-10-16"


def test_independent_obligations_are_not_disputed(world):
    led, _, deps, _ = world
    co.run_catchup(request(), deps([co.ConnectorRecord("exam_a", "2026-10-14"),
                                   co.ConnectorRecord("exam_b", "2026-10-16")]))
    facts = led.facts_matching("kids_school", "exam_registration_deadline", "kid1")
    assert len(facts) == 2 and all(f.status == "current" for f in facts)
    assert led.conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 2


def test_incomplete_interpretation_remains_visible_on_later_catchups(world):
    _, _, deps, texts = world
    incomplete = lambda prompt: '{"candidates": [], "complete": false}'  # noqa: E731
    d = deps([co.ConnectorRecord("m1", "x" * 8100 + " 2026-10-14")])
    d.extractor = incomplete
    first = co.run_catchup(request(), d)
    again = deps()
    again.extractor = incomplete                          # a retry that is still incomplete stays visible
    second = co.run_catchup(request("cu_b"), again)
    assert first.status is RunStatus.DEGRADED and second.status is RunStatus.DEGRADED
    assert all("Nothing urgent" not in text for text in texts)
    # A later retry that interprets the Signal completely resolves it (bounded retry of retry-eligible failures).
    third = co.run_catchup(request("cu_c"), deps())
    assert third.status is RunStatus.COMPLETE


def test_deadline_after_the_first_excerpt_is_processed(world):
    _, _, deps, texts = world
    result = co.run_catchup(request(), deps([co.ConnectorRecord("tail", "x" * 8100 + " 2026-10-14")]))
    assert result.status is RunStatus.COMPLETE and result.artifact_ids
    assert "ONE THING" in texts[0]


def test_unreadable_exclusion_never_allows_reassurance(world):
    _, _, deps, texts = world
    result = co.run_catchup(request(), deps([co.ConnectorRecord("big", "x" * 200001)]))
    assert result.status is RunStatus.DEGRADED
    assert "Nothing urgent" not in texts[0]


def test_failed_archive_is_retried_before_finalization(world):
    led, _, deps, _ = world
    d = deps([co.ConnectorRecord("m1", "2026-10-14")])
    attempts = []

    def publish(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise OSError("simulated disk full")
    d.publisher = publish
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.BLOCKED
    assert "FINALIZED" not in led.committed_stages("cu_a")
    assert co.run_catchup(request(), d).briefing_id
    assert len(attempts) == 2


def test_resume_rejects_changed_versions(world):
    _, _, deps, _ = world
    co.run_catchup(request(), deps())
    result = co.run_catchup(request(policy_version="changed", schema_version=999), deps())
    assert result.status is RunStatus.BLOCKED


def test_migration_backup_includes_wal(world, tmp_path):
    led, _, _, _ = world
    import sqlite3
    path = _backup_for(tmp_path)(tmp_path / "ledger.db")
    with closing(sqlite3.connect(path)) as restored:
        assert restored.execute("PRAGMA user_version").fetchone()[0] == led.schema_version()
        assert restored.execute("SELECT count(*) FROM signals").fetchone()[0] == 0


def test_publisher_preserves_two_catchup_identities(tmp_path):
    publish = make_file_publisher(tmp_path, lambda: NOW)
    publish("cu_a", "first", 1)
    publish("cu_b", "second", 1)
    assert len(list((tmp_path / "briefings").glob("*"))) == 2


def test_empty_scorecard_is_not_release_evidence():
    assert not score([]).passes


def test_forget_removes_the_encrypted_evidence_blob(world):
    led, blobs, deps, _ = world
    co.run_catchup(request(), deps([co.ConnectorRecord("m1", "2026-10-14")]))
    ref = led.conn.execute("SELECT evidence_blob_ref FROM signals").fetchone()[0]
    led.forget("school_email", "acct", "m1")
    from catchup_core.blobs import BlobMissing
    with pytest.raises(BlobMissing):
        blobs.get(ref)


def test_revocation_invalidates_older_permission_history(world):
    led, _, _, _ = world
    consent = led.grant_consent("adult", "adult", "school", ["view"], "owner")
    led.revoke_consent(consent)
    sealed = led._sealed.read()
    assert sealed is not None and sealed.permission_generation == 1


def test_one_scope_consent_cannot_authorize_another(world):
    led, _, _, _ = world
    led.grant_consent("adult", "adult", "school", ["view"], "owner")
    assert led.consent_active("adult", "view", "owner", scope="school")
    assert not led.consent_active("adult", "view", "owner", scope="medical")


def test_owner_cannot_consent_for_an_adult(tmp_path):
    led = lg.Ledger(tmp_path / "adult.db", id_key=KEY, cipher=CIPHER,
                    consent_subjects=frozenset({"adult"})).open()
    led.migrate()
    try:
        with pytest.raises(lg.PermissionDenied):
            led.grant_consent("adult", "owner", "school", ["view"], "owner")
    finally:
        led.close()


def test_publisher_encrypts_restricted_text(tmp_path):
    publish = make_file_publisher(tmp_path, lambda: NOW, cipher=CIPHER)
    publish("cu_a", "PRIVATE-SYNTHETIC-CONTENT", 1)
    path = next((tmp_path / "briefings").glob("*"))
    assert b"PRIVATE-SYNTHETIC-CONTENT" not in path.read_bytes()
    publish("cu_a", "PRIVATE-SYNTHETIC-CONTENT", 1)  # idempotent despite randomized encryption


def test_release_switch_cannot_bypass_the_evidence_gate():
    from catchup_core.policy import validate_release
    with pytest.raises(lg.PermissionDenied, match="release evidence"):
        validate_release(request(), {"cutover": True}, viewer="owner", domains=["kids_school"])


def test_no_adult_source_content_before_ingest_consent(world):
    from catchup_core.policy import authorize_source
    led, _, _, _ = world
    led.grant_consent("adult", "adult", "school:acct", ["view"], "owner")
    with pytest.raises(lg.PermissionDenied, match="ingest"):
        authorize_source(led, ["adult"], frozenset({"adult"}), "school:acct", "owner")


def test_evidence_locator_never_overwrites_an_earlier_version(world):
    _, blobs, _, _ = world
    ref = blobs.put("same", "original")
    assert blobs.put("same", "original") == ref
    with pytest.raises(ValueError, match="immutable"):
        blobs.put("same", "changed")
    assert blobs.get(ref) == "original"


def test_ambiguous_subject_is_a_durable_gap(world):
    led, _, deps, _ = world
    d = deps([co.ConnectorRecord("m", "2026-10-14")])
    d.subjects_for = lambda connector, text: []
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and led.unresolved_signals()
    assert "Nothing urgent" not in result.briefing_text


@pytest.mark.parametrize("references", [False, True])
def test_two_obligations_in_one_message_need_stable_identity(world, references):
    led, _, deps, _ = world
    text = "SAT closes 2026-10-14. AP closes 2026-10-16."
    def model(_):
        candidates = []
        for label, value in (("sat", "2026-10-14"), ("ap", "2026-10-16")):
            start = text.index(value)
            candidates.append({"predicate": "exam_registration_deadline", "subject_id": "kid1",
                "value": value, "quote": value, "span": [start, start + 10],
                "obligation_ref": label if references else ""})
        return json.dumps({"complete": True, "candidates": candidates})
    d = deps([co.ConnectorRecord("message", text)])
    d.extractor = model
    result = co.run_catchup(request(), d)
    if references:
        facts = led.facts_matching("kids_school", "exam_registration_deadline", "kid1")
        assert len(facts) == 2 and all(f.status == "current" for f in facts)
    else:
        assert result.status is RunStatus.DEGRADED and led.unresolved_signals()


def test_unlisted_source_cannot_establish_a_verified_deadline(world):
    led, _, deps, _ = world
    d = deps([co.ConnectorRecord("m", "2026-10-14")])
    d.source_class = {"school_email": "untrusted_forward"}
    result = co.run_catchup(request(), d)
    fact = led.conn.execute("SELECT status,provenance_class FROM facts").fetchone()
    assert tuple(fact) == ("unknown", "model_hypothesis") and result.status is RunStatus.DEGRADED


@pytest.mark.parametrize("value,observed,accepted", [("2029-10-14", "2026-10-12", False),
    ("2026-10-14", None, False), ("2026-10-14", "2026-10-12", True)])
def test_yearless_date_requires_a_matching_observation_year(value, observed, accepted):
    text = "Deadline October 14"
    raw = {"predicate": "exam_registration_deadline", "subject_id": "kid1", "value": value,
           "quote": text, "span": [0, len(text)]}
    got = parse_output(json.dumps({"complete": True, "candidates": [raw]}), PACK,
        NormalizedSignal("s", "school_email", text, observed_at=observed), ["kid1"])
    assert bool(got.candidates) is accepted
