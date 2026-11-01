from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core.backup import apply_privacy_history, encrypted_snapshot, read_snapshot, restore_encrypted_snapshot
from catchup_core import crypto, ids, ledger as lg, views
from catchup_core.curated import records
from catchup_core.session_lock import SessionBusy, session_lock
from catchup_core.blobs import EncryptedBlobStore

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))


@pytest.fixture
def store(tmp_path):
    led = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER,
                    consent_subjects=frozenset({"adult"})).open()
    led.migrate()
    yield led
    led.close()


def fact(store, **kw):
    return store.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
        observed_at="2026-10-12T00:00:00Z", primary_subject_id="kid1", **kw))


def test_beliefs_filter_before_rendering(store):
    visible = fact(store)
    private = store.put_fact(lg.FactInput(domain="health", predicate="status", value="private",
        observed_at="2026-10-12T00:00:00Z", primary_subject_id="adult",
        confidentiality=lg.Confidentiality.RESTRICTED))
    assert [f["fact_id"] for f in views.beliefs(store, "owner")] == [visible]
    assert [f["fact_id"] for f in views.beliefs(store, "adult")] == [private]
    assert views.beliefs(store, "owner", domain="finance") == []
    text = views.render("beliefs", {"beliefs": views.beliefs(store, "owner")})
    assert "2026-10-14" in text and "private" not in text


def test_co_subject_requires_strictest_permission(store):
    fid = fact(store, co_subjects=(("adult", "co_subject"),))
    with pytest.raises(lg.PermissionDenied):
        store.get_fact(fid, "owner")
    grant = store.grant_consent("adult", "adult", "kids_school", ["view"], "owner")
    assert store.get_fact(fid, "owner")
    store.revoke_consent(grant)
    with pytest.raises(lg.PermissionDenied):
        store.get_fact(fid, "owner")


def test_correction_requires_reviewed_version(store):
    fid = fact(store)
    old = store.get_fact(fid, "owner")
    views.correct(store, "owner", fid, "2026-10-16", old.recorded_at)
    updated = store.get_fact(fid, "owner")
    assert updated.provenance_class == "user_confirmed" and updated.value == "2026-10-16"
    with pytest.raises(ValueError, match="changed"):
        views.correct(store, "owner", fid, "2026-10-17", old.recorded_at)


def test_historical_explanation_uses_the_selected_observation_time(store, tmp_path):
    from dataclasses import replace
    original = lg.FactInput(domain='kids_school', predicate='test_date', value='2026-10-20',
        primary_subject_id='kid1', observed_at='2026-10-12T00:00:00Z', occurred_at='2026-10-11T00:00:00Z',
        validity_unknown=True)
    fid = store.put_fact(original)
    first = store.get_fact(fid, 'owner').recorded_at
    store.put_fact(replace(original, value='2026-10-21', observed_at='2026-10-13T00:00:00Z',
                           occurred_at='2026-10-13T00:00:00Z', validity_unknown=False), fact_id=fid)
    blobs = EncryptedBlobStore(tmp_path / 'blobs', CIPHER, KEY)
    old = views.explain(store, blobs, 'owner', fid, as_of=first)
    assert old['value'] == '2026-10-20' and old['observed_at'] == original.observed_at
    assert old['occurred_at'] == original.occurred_at and old['recorded_at'] == first
    assert old['validity_unknown'] is True
    assert old['history'][-1]['validity_unknown'] is True
    # Lifecycle-only changes retain the same source time.
    store.set_fact_status(fid, lg.FactStatus.DISPUTED)
    current = views.explain(store, blobs, 'owner', fid)
    assert current['observed_at'] == '2026-10-13T00:00:00Z'
    assert current['occurred_at'] == '2026-10-13T00:00:00Z'
    assert current['validity_unknown'] is False
    assert [version['validity_unknown'] for version in current['history']] == [True, False, False]
    before = views.explain(store, blobs, 'owner', fid, as_of='2000-01-01T00:00:00Z')
    assert before['observed_at'] is None and before['occurred_at'] is None and before['recorded_at'] is None
    assert before['validity_unknown'] is True


def test_legacy_sensitive_values_are_reencrypted_without_rewriting_history(store):
    fid = fact(store, confidentiality=lg.Confidentiality.SENSITIVE)
    old = store.get_fact(fid, "owner")
    payload = json.dumps("SYNTHETIC-LEGACY-SECRET")
    store.conn.execute("UPDATE facts SET typed_value=?,value_encrypted=0,key_version=NULL WHERE fact_id=?", (payload, fid))
    store.conn.execute("UPDATE fact_versions SET typed_value=?,value_encrypted=0,key_version=NULL WHERE fact_id=?",
                       (payload, fid))
    assert store.protect_legacy_values() == 2
    assert store.protect_legacy_values() == 0
    current = store.get_fact(fid, "owner")
    assert current.value == "SYNTHETIC-LEGACY-SECRET" and current.recorded_at == old.recorded_at
    assert b"SYNTHETIC-LEGACY-SECRET" not in store._path.read_bytes()


def test_consent_expiry_compares_instants_across_offsets(tmp_path):
    with lg.Ledger(tmp_path / "expiry.db", id_key=KEY, cipher=CIPHER,
                   clock=lambda: "2026-10-12T23:00:00Z").open() as led:
        led.migrate()
        led.grant_consent("adult", "adult", "health", ["view"], "owner",
                          expires_at="2026-10-13T01:00:00+14:00")
        assert not led.consent_active("adult", "view", "owner", scope="health")
        with pytest.raises(ValueError, match="offset"):
            led.grant_consent("adult", "adult", "health", ["view"], "owner", expires_at="2026-10-14")


def test_terminal_projection_is_readable_and_inert():
    text = views.render("beliefs", {"beliefs": [{"fact_id": "f1", "predicate": "<b>date</b>",
                       "value": "2026-10-14", "status": "unknown"}]})
    assert "date" in text and "f1" not in text and "<b>" not in text
    assert "f1:" in views.render("beliefs", {"beliefs":[{"fact_id":"f1", "predicate":"date"}]},detail=True)
    assert views.render("beliefs", {"beliefs": []}) == "No beliefs recorded here."
    assert "deleted signals: 1" in views.render("forget", {"deleted_signals": 1})


@pytest.mark.parametrize("goals", [[], [{"id": "G-1", "title": "Practice", "status": "active"}]])
def test_terminal_goals_preserve_review_observations_and_gaps(goals, store, tmp_path):
    from catchup_core import commands

    state = tmp_path / "state"
    state.mkdir()
    (state / "goals.md").write_text(json.dumps({"goals": goals, "sprints": [
        {"id": "S-1", "goal_ref": "missing", "status": "active", "owner": "owner"}]}))
    store.grant_consent("adult", "adult", "curated:goals", ["ingest", "disclose", "view"], "owner")
    deps = SimpleNamespace(ledger=store, viewer="owner", subjects=(),
                           now=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc),
                           pack=SimpleNamespace(sources=()), validate_inputs=lambda: None)
    result = commands.execute(SimpleNamespace(command="goals"), deps, tmp_path)
    assert result["goals"] == goals
    assert result["observations"][0] == "Sprint review needed: Active Sprint needs an active Goal"
    text = views.render("goals", result)
    assert result["observations"][0] in text
    assert "Gap: Sprint commitments need review" in text
    assert ("G-1: Practice" if goals else "No goals recorded here.") in text


def test_terminal_review_gaps_are_bounded_readable_and_inert():
    gap = "<b>Needs review</b>\n\x1b[31m [source](https://example.invalid) " + "x" * 600
    result = {"goals": [], "observations": [], "gaps": [gap] * 12}
    lines = views.render("goals", result).splitlines()
    gaps = [line for line in lines if line.startswith("Gap: ")]
    assert len(gaps) == 10
    assert all(len(line) <= 505 for line in gaps)
    assert "2 additional gaps not shown." in lines
    assert "Needs review" in gaps[0]
    assert not any(token in "\n".join(gaps) for token in ("<b>", "https://", "\x1b", "{'"))
    assert result["gaps"] == [gap] * 12


def test_terminal_unavailable_goals_remain_unknown():
    result = {"goals": [], "observations": [], "gaps": ["Goals unavailable; progress is unknown."]}
    assert "Gap: Goals unavailable; progress is unknown." in views.render("goals", result)


def test_terminal_health_preserves_degraded_read_surfaces():
    result = {"coverage": [{"connector": "calendar", "status": "degraded", "last_verified": None}],
              "capabilities": [{"id": "RA-11", "state": "degraded", "measured": "no"}]}
    text = views.render("health", result)
    assert '"status": "degraded"' in text
    assert '"state": "degraded"' in text
    assert '"last_verified": null' in text


def test_feedback_is_reviewed_not_automatically_gold(store):
    fid = fact(store)
    store.upsert_alert("exam", domain="kids_school", primary_subject_id="kid1", severity="P1", fact_ids=[fid])
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    views.feedback(store, "owner", "exam", "snooze", "already planned", now)
    assert store.get_alert("exam")["status"] == "snoozed"
    assert views.precision(store)[0]["review_status"] == "unreviewed"
    with pytest.raises(ValueError):
        views.feedback(store, "owner", "exam", "dismiss", "", now)


def test_open_items_are_read_without_losing_active_commitments():
    text = "## Active\n- id: OI-001\n  description: Register for exam\n  status: open\n\n" \
           "- id: OI-002\n  description: Resolved\n  status: done\n"
    assert [r["id"] for r in records(text, "open_items")] == ["OI-001"]
    assert records('---\ngoals:\n  - id: G-1\n    owner: owner\n---\n', "goals")[0]["id"] == "G-1"


def test_separate_process_cannot_take_a_live_session_lock(tmp_path):
    lock = tmp_path / "session.lock"
    module = Path(__file__).resolve().parents[2] / "scripts"
    script = ("import sys; sys.path.insert(0,sys.argv[1]); "
              "from catchup_core.session_lock import session_lock; from pathlib import Path; "
              "lock=session_lock(Path(sys.argv[2])); lock.__enter__()")
    with session_lock(lock):
        result = subprocess.run([sys.executable, "-c", script, str(module), str(lock)], capture_output=True)
    assert result.returncode != 0 and b"another Catch-up" in result.stderr
    with session_lock(lock):
        pass  # no stale marker can prevent safe recovery once the process exits


class Keyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


def test_encrypted_export_privacy_replay_and_blocked_legacy_restore(tmp_path, monkeypatch):
    sealed = lg.KeyringSealedStore(Keyring(), "test")
    led = lg.Ledger(tmp_path / "original.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open()
    led.migrate()
    led.seal_privacy()
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    ref = blobs.put("m1", "SYNTHETIC-PRIVATE-DOCUMENT")
    sid, _ = led.upsert_signal(lg.SignalInput("mail", "a", "m1", "v1", "t", "h1", ref))
    led.put_fact(lg.FactInput(domain="health", predicate="value", value="SYNTHETIC-SECRET",
        primary_subject_id="adult", observed_at="t", evidence=((sid, 0, 10),)))
    grant = led.grant_consent("adult", "adult", "health", ["view"], "owner")
    export = tmp_path / "snapshot.ae1"
    encrypted_snapshot(export, tmp_path / "original.db", CIPHER, blobs=tmp_path / "blobs")
    ciphertext = export.read_bytes()
    assert b"SYNTHETIC" not in ciphertext
    snapshot = read_snapshot(export, CIPHER)
    assert "ledger.db" in snapshot and f"blobs/{ref}.blob" in snapshot
    led.revoke_consent(grant)
    led.forget("mail", "a", "m1")
    with closing(sqlite3.connect(":memory:")) as replay:
        replay.deserialize(snapshot["ledger.db"])
        removed = []
        apply_privacy_history(replay, removed.append, sealed.read(), led.privacy_history())
        assert replay.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
        assert replay.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
        assert replay.execute("SELECT revoked_at FROM consent WHERE consent_id=?", (grant,)).fetchone()[0]
        assert f"blobs/{ref}.blob" in removed
    destination = tmp_path / "restored"
    with pytest.raises(lg.LedgerLocked, match="governed checkpoint/quarantine"):
        restore_encrypted_snapshot(export, destination, CIPHER, sealed)
    assert not destination.exists() and export.read_bytes() == ciphertext
    led.close()
    from io import StringIO
    from catchup_core import cli, runtime
    monkeypatch.setattr(runtime, "load_runtime_config", lambda _: SimpleNamespace(viewer="owner"))
    monkeypatch.setattr(crypto, "KeyringKeyProvider", lambda *args, **kwargs: crypto.StaticKeyProvider({1: b"7" * 32}))
    monkeypatch.setattr(lg, "KeyringSealedStore", lambda *args, **kwargs: sealed)
    monkeypatch.setitem(sys.modules, "keyring", Keyring())
    stdout, stderr = StringIO(), StringIO()
    code = cli.main(["restore", str(export), str(destination), "--actor", "owner"],
                    out=stdout, err=stderr)
    assert code == 20 and stdout.getvalue() == ""
    assert "governed checkpoint/quarantine" in stderr.getvalue()
    assert "privacy history" in stderr.getvalue() and "inactive installation fence" in stderr.getvalue()
    assert not destination.exists() and export.read_bytes() == ciphertext


def test_feedback_becomes_an_encrypted_unreviewed_replay_case_that_forget_deletes(store, tmp_path):
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    store._blob_deleter = blobs.delete
    text = "Registration closes 2026-10-14 for kid1."
    ref = blobs.put("gmail|a|m1|v1", text)
    chmac = ids.content_hmac(KEY, text)
    sid, _ = store.upsert_signal(lg.SignalInput("gmail", "a", "m1", "v1", "2026-10-12T00:00:00Z", chmac, ref))
    fid = store.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
        observed_at="2026-10-12T00:00:00Z", primary_subject_id="kid1", evidence=((sid, 20, 30),)))
    store.upsert_alert("exam", domain="kids_school", primary_subject_id="kid1", severity="P1", fact_ids=[fid])
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    replay_dir = tmp_path / "replay"
    store._replay_deleter = lambda ids_: [(replay_dir / f"{i}.ae1").unlink(missing_ok=True) for i in ids_]
    feedback_id = views.feedback(store, "owner", "exam", "dismiss", "already handled", now)
    path = views.replay_case(store, blobs, "owner", "exam", "dismiss", "already handled", feedback_id, now, replay_dir)
    assert text.encode() not in path.read_bytes()                        # ciphertext only
    bundle = json.loads(CIPHER.decrypt(path.read_bytes(), aad=f"replay:{feedback_id}".encode()))
    assert bundle["expected_outcome"] == "pending_review" and bundle["inputs"][0]["text"] == text
    store.forget("gmail", "a", "m1")
    assert not path.exists()
    assert store.conn.execute("SELECT count(*) FROM replay_cases").fetchone()[0] == 0


def test_replay_case_for_unknown_alert_is_refused(store, tmp_path):
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    with pytest.raises(ValueError):
        views.replay_case(store, blobs, "owner", "nope", "dismiss", "x", "fbk_1",
                          datetime(2026, 10, 12, tzinfo=timezone.utc), tmp_path / "replay")
