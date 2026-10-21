"""End-to-end Catch-up scenarios on synthetic data (re-artha §12.5 demonstrations)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from catchup_core import coordinator as co
from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.blobs import BlobMissing, EncryptedBlobStore
from catchup_core.contracts import (
    BriefingFormat,
    CatchupRequest, CoverageStatus, ExecutionMode, FailureClass, RunStatus, Stage,
)
from catchup_core.incumbent import (
    GuardedIncumbent, IncumbentConclusion, IncumbentOutput, PilotWriteRefused, ProposedChange, route_mixed,
)

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "kids_school.yaml")
MSG = "Families: SAT registration closes October 14. Please register online."
QUOTE = "registration closes October 14"


def model_for(text_to_value):
    """A deterministic stand-in for the extractor model: finds the quote in the prompt message."""
    def run(prompt: str) -> str:
        message = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
        items = []
        for quote, (pred, value, subject) in text_to_value.items():
            if quote in message:
                i = message.index(quote)
                items.append({"predicate": pred, "value": value, "subject_id": subject, "quote": quote,
                              "span": [i, i + len(quote)]})
        return json.dumps({"candidates": items})
    return run


EXTRACT = model_for({QUOTE: ("exam_registration_deadline", "2026-10-14", "kid1")})


@pytest.fixture
def world(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    published: list[tuple[str, int]] = []

    def make(fetchers, extractor=EXTRACT, **kw):
        return co.Deps(
            ledger=ledger, pack=PACK, blobs=blobs, id_key=KEY, fetchers=fetchers, extractor=extractor,
            subjects=["kid1", "kid2"], viewer="ved", now=lambda: NOW,
            source_class={"school_email": "school_email", "calendar": "calendar"},
            publisher=lambda cid, text, v: published.append((cid, v)), coverage_line=lambda: "Coverage: test.",
            **kw)

    yield ledger, blobs, make, published
    ledger.close()


def req(cid="cu_1", mode=ExecutionMode.AUTHORITATIVE, **kw):
    return CatchupRequest(cid, "ved", "cli", mode=mode, host="mac", **kw)


def email(records=(co.ConnectorRecord("m1", MSG),), status=CoverageStatus.COMPLETE, error=None):
    return lambda: co.ConnectorFetch("school_email", "acct", tuple(records), status, error)


def cal(status=CoverageStatus.COMPLETE, error=None, records=()):
    return lambda: co.ConnectorFetch("calendar", "acct", tuple(records), status, error)


def test_happy_path_publishes_one_cited_briefing(world):
    ledger, _b, make, published = world
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": cal()}))
    assert result.status is RunStatus.COMPLETE and result.exit_code() == 0
    assert result.briefing_id == "brf_cu_1" and result.briefing_version == 1
    assert published == [("cu_1", 1)]
    alerts = result.artifact_ids
    assert len(alerts) == 1
    assert ledger.unresolved_signals() == []
    assert sorted(ledger.committed_stages("cu_1")) == sorted(s.value for s in Stage)


def test_failed_calendar_still_surfaces_the_verified_email_alert_without_reassurance(world):
    ledger, _b, make, _p = world
    d = make({"school_email": email(), "calendar": cal(CoverageStatus.UNAVAILABLE, FailureClass.AUTH)})
    result = co.run_catchup(req(), d)
    assert result.status is RunStatus.DEGRADED and result.exit_code() == 10
    assert any(c.connector == "calendar" and c.status is CoverageStatus.UNAVAILABLE for c in result.coverage)
    assert result.artifact_ids                          # the email Alert is still there
    text = _briefing_text(ledger, d)
    assert "ONE THING" in text and "Nothing urgent" not in text


def _briefing_text(ledger, deps):
    # compose again from the ledger for assertions on the rendered text
    from catchup_core.compose import compose
    from catchup_core.triage import triage
    t = triage(ledger, deps.pack, NOW, viewer="ved",
               freshness={"school_email": NOW, "calendar": NOW},
               connector_of=lambda s: ledger.signal_row(s)["connector"])
    return compose(NOW, t, evidence_for={}, coverage_line="x", coverage_gaps=["calendar unavailable"]).text


def test_valid_empty_sources_allow_a_scoped_quiet_claim(world):
    _l, _b, make, _p = world
    result = co.run_catchup(req(), make({"school_email": email(records=()), "calendar": cal()}))
    assert result.status is RunStatus.COMPLETE and result.artifact_ids == ()


def test_unreachable_required_source_is_degraded_even_with_no_alerts(world):
    _l, _b, make, _p = world
    result = co.run_catchup(req(), make({"school_email": email(records=())}))      # calendar never reported
    assert result.status is RunStatus.DEGRADED
    assert any(c.connector == "calendar" and c.account_scope == "unconnected" for c in result.coverage)


def test_connector_exception_degrades_only_that_scope(world):
    _l, _b, make, _p = world

    def boom():
        raise RuntimeError("provider 500")
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": boom}))
    assert result.status is RunStatus.DEGRADED and FailureClass.PROVIDER in result.failures
    assert result.artifact_ids


def test_poison_signal_is_isolated_and_blocks_a_quiet_claim(world):
    ledger, _b, make, _p = world
    records = (co.ConnectorRecord("good", MSG), co.ConnectorRecord("bad", "unparseable newsletter"))

    def flaky(prompt: str) -> str:
        return "not json at all" if "unparseable newsletter" in prompt else EXTRACT(prompt)
    result = co.run_catchup(req(), make({"school_email": email(records), "calendar": cal()}, extractor=flaky))
    assert result.artifact_ids                                   # the good message produced its Alert
    assert FailureClass.EXTRACT in result.failures
    assert result.status is RunStatus.DEGRADED                  # never "complete" with an uninterpreted Signal
    assert len(ledger.unresolved_signals()) == 1 and len(ledger.failed_signals()) == 1


def test_model_exception_is_a_per_signal_failure(world):
    _l, _b, make, _p = world

    def down(prompt):
        raise ConnectionError("model offline")
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": cal()}, extractor=down))
    assert result.status is RunStatus.DEGRADED and result.artifact_ids == ()


def test_restart_resumes_without_repeating_committed_work(world):
    ledger, _b, make, published = world
    calls = {"extract": 0}

    def counting(prompt):
        calls["extract"] += 1
        return EXTRACT(prompt)

    def crash_before_commit(stage):
        if stage is Stage.COMMIT:
            raise co.StageFault("crash")
    d = make({"school_email": email(), "calendar": cal()}, extractor=counting, fault=crash_before_commit)
    with pytest.raises(co.StageFault):
        co.run_catchup(req(), d)
    assert "COMMIT" not in ledger.committed_stages("cu_1") and "RECONCILE" in ledger.committed_stages("cu_1")
    assert published == []
    # restart: same Catch-up ID, no fault
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": cal()}, extractor=counting))
    assert calls["extract"] == 1, "EXTRACT must not be repeated after it committed"
    assert result.briefing_version == 1 and published == [("cu_1", 1)]
    assert ledger.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    assert ledger.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1


def test_rerun_after_finalize_publishes_no_second_briefing(world):
    _l, _b, make, published = world
    d = make({"school_email": email(), "calendar": cal()})
    co.run_catchup(req(), d)
    again = co.run_catchup(req(), d)
    assert published == [("cu_1", 1)] and again.briefing_version == 1


def test_different_text_for_same_catchup_needs_explicit_correction(world):
    ledger, *_ = world
    ledger.publish_briefing("cu_9", "first")
    with pytest.raises(lg.LedgerError):
        ledger.publish_briefing("cu_9", "second")
    assert ledger.publish_briefing("cu_9", "second", correction=True) == (2, True)
    assert ledger.publish_briefing("cu_9", "second") == (2, False)      # identical repeat is idempotent


def test_shadow_mode_never_publishes(world):
    ledger, _b, make, published = world
    result = co.run_catchup(req("cu_s", ExecutionMode.SHADOW), make({"school_email": email(), "calendar": cal()}))
    assert result.briefing_id is None and published == []
    assert ledger.conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0
    assert result.artifact_ids                      # comparison data is still produced


def test_forgotten_source_is_not_reingested(world):
    ledger, blobs, make, _p = world
    d = make({"school_email": email(), "calendar": cal()})
    co.run_catchup(req("cu_a"), d)
    receipt = ledger.forget("school_email", "acct", "m1")
    assert receipt.deleted_signals == 1 and receipt.deleted_facts == 1
    result = co.run_catchup(req("cu_b"), d)          # the same message is fetched again
    assert ledger.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
    assert result.artifact_ids == ()


def test_corrected_message_reprocesses_and_supersedes(world):
    ledger, _b, make, _p = world
    corrected = MSG.replace("October 14", "October 16")
    ex2 = model_for({QUOTE: ("exam_registration_deadline", "2026-10-14", "kid1"),
                     "registration closes October 16": ("exam_registration_deadline", "2026-10-16", "kid1")})
    co.run_catchup(req("cu_1"), make({"school_email": email(), "calendar": cal()}, extractor=ex2))
    co.run_catchup(req("cu_2"), make(
        {"school_email": email((co.ConnectorRecord("m1", corrected),)), "calendar": cal()}, extractor=ex2))
    current = ledger.facts_matching("kids_school", "exam_registration_deadline", "kid1",
                                    (lg.FactStatus.CURRENT,))
    assert [f.value for f in current] == ["2026-10-16"]


def test_blob_store_encrypts_and_sweeps(tmp_path):
    store = EncryptedBlobStore(tmp_path / "b", CIPHER, KEY)
    ref = store.put("loc", "very private text")
    assert b"private" not in (tmp_path / "b" / f"{ref}.blob").read_bytes()
    assert store.get(ref) == "very private text"
    other = store.put("loc2", "x")
    assert store.sweep([ref]) == [other]
    with pytest.raises(BlobMissing):
        store.get(other)
    with pytest.raises(ValueError):
        store.get("../etc/passwd")
    store.delete(ref)
    assert store.sweep([]) == []


# ── incumbent adapter ──────────────────────────────────────────────────────

class _Adapter:
    def __init__(self, out=None, exc=None, slow=0.0, clock=None):
        self.out, self.exc, self.slow, self.clock = out or IncumbentOutput(), exc, slow, clock

    def run(self, signals, *, budget_seconds):
        if self.exc:
            raise self.exc
        if self.clock is not None:
            self.clock.t += self.slow
        return self.out


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_mixed_signal_fans_out_once_and_loses_nothing():
    pilot, other = route_mixed("sig_1", [("o1", "kids_school"), ("o2", "finance"), ("o3", "kids_school")],
                               ["kids_school"])
    assert [p.obligation_ref for p in pilot] == ["o1", "o3"] and [o.obligation_ref for o in other] == ["o2"]
    assert {i.obligation_ref for i in pilot + other} == {"o1", "o2", "o3"}


def test_incumbent_crash_degrades_never_aborts():
    out = GuardedIncumbent(_Adapter(exc=RuntimeError("workflow failed"))).run(["s"])
    assert out.coverage[0].status is CoverageStatus.UNAVAILABLE and FailureClass.UNKNOWN in out.failures


def test_incumbent_timeout_is_degraded_coverage():
    clock = _Clock()
    out = GuardedIncumbent(_Adapter(slow=100, clock=clock), clock=clock).run(["s"], budget_seconds=10)
    assert FailureClass.TIMEOUT in out.failures and out.coverage[-1].status is CoverageStatus.DEGRADED


def test_incumbent_cannot_write_pilot_records_or_claim_verified():
    bad = IncumbentOutput(proposed_changes=(ProposedChange("fact", "f1"),))
    with pytest.raises(PilotWriteRefused):
        GuardedIncumbent(_Adapter(bad)).run(["s"])
    spoof = IncumbentOutput(conclusions=(IncumbentConclusion("finance", "x", "v1", provenance="verified_source"),))
    with pytest.raises(PilotWriteRefused):
        GuardedIncumbent(_Adapter(spoof)).run(["s"])
    ok = IncumbentOutput(conclusions=(IncumbentConclusion("finance", "bill due", "v1"),),
                         proposed_changes=(ProposedChange("open_item", "OI-1"),))
    assert GuardedIncumbent(_Adapter(ok)).run(["s"]).conclusions
    assert GuardedIncumbent(None).run(["s"]) == IncumbentOutput()


def test_incumbent_failure_degrades_authoritative_result_but_keeps_pilot_alert(world):
    _l, _b, make, _p = world
    d = make({"school_email": email(), "calendar": cal()},
             incumbent=GuardedIncumbent(_Adapter(exc=RuntimeError("down"))))
    result = co.run_catchup(req(), d)
    assert result.briefing_id and result.artifact_ids
    assert any(c.connector == "incumbent" and c.status is CoverageStatus.UNAVAILABLE for c in result.coverage)


def test_normalize_excludes_empty_oversized_and_unreadable(world):
    ledger, blobs, make, _p = world
    big = "x" * (co.MAX_SIGNAL_CHARS + 1)
    recs = (co.ConnectorRecord("empty", "   "), co.ConnectorRecord("big", big), co.ConnectorRecord("ok", MSG))
    result = co.run_catchup(req(), make({"school_email": email(recs), "calendar": cal()}))
    states = {r["upstream_id"]: (r["processing_state"], r["exclusion_reason"])
              for r in ledger.conn.execute("SELECT upstream_id, processing_state, exclusion_reason FROM signals")}
    assert states["empty"][0] == "excluded" and states["big"][1].startswith("oversized")
    assert states["ok"][0] == "reconciled"
    assert FailureClass.DECODE in result.failures


def test_unreadable_evidence_is_excluded_with_decode_failure(world):
    ledger, blobs, make, _p = world

    class _Broken(EncryptedBlobStore):
        def get(self, ref):
            raise BlobMissing(ref)
    d = make({"school_email": email(), "calendar": cal()})
    d.blobs = _Broken(blobs._root, CIPHER, KEY)
    d.blobs.put("loc", "x")     # ensure dir exists
    result = co.run_catchup(req(), d)
    assert FailureClass.DECODE in result.failures and result.status is RunStatus.DEGRADED


def test_partially_invalid_extraction_keeps_valid_candidates(world):
    ledger, _b, make, _p = world

    def mixed(prompt):
        i = prompt.rsplit("<untrusted>\n", 1)[1].index(QUOTE)
        good = {"predicate": "exam_registration_deadline", "value": "2026-10-14", "subject_id": "kid1",
                "quote": QUOTE, "span": [i, i + len(QUOTE)]}
        bad = dict(good, predicate="wire_money")
        return json.dumps({"candidates": [good, bad]})
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": cal()}, extractor=mixed))
    assert FailureClass.SCHEMA_REJECTED in result.failures and result.artifact_ids
    assert result.status is RunStatus.DEGRADED


def test_datetime_predicates_flow_through_the_pipeline(world):
    ledger, _b, make, _p = world
    text = "Dentist appointment confirmed for 2026-10-14T15:30:00-07:00 for the family."
    q = "2026-10-14T15:30:00-07:00"
    ex2 = model_for({q: ("appointment_time", q, "kid2")})
    result = co.run_catchup(req(), make(
        {"school_email": email(()), "calendar": cal(records=(co.ConnectorRecord("a1", text),))}, extractor=ex2))
    fact = ledger.facts_matching("kids_school", "appointment_time", "kid2")[0]
    assert fact.value.startswith("2026-10-14T15:30:00")
    assert result.status is RunStatus.COMPLETE


def test_a_briefing_that_fails_validation_is_blocked_not_published(world):
    ledger, _b, make, published = world
    d = make({"school_email": email(), "calendar": cal()})
    d.coverage_line = lambda: "Coverage: see note [9]"      # an unresolvable citation can never publish
    result = co.run_catchup(req(), d)
    assert result.status is RunStatus.BLOCKED and result.exit_code() == 20
    assert result.briefing_id is None and published == []
    assert FailureClass.POLICY in result.failures and "validation" in result.reason_unpublished


def test_only_requested_connector_scopes_are_fetched(world):
    ledger, _b, make, _p = world
    called = []

    def tracked(name, fn):
        def run():
            called.append(name)
            return fn()
        return run
    d = make({"school_email": tracked("school_email", email()), "calendar": tracked("calendar", cal())})
    co.run_catchup(req(connector_scopes=("calendar",)), d)
    assert called == ["calendar"]


def test_incumbent_conclusions_appear_labelled_and_the_adapter_runs_once(world):
    ledger, _b, make, _p = world
    calls = {"n": 0}

    class Counting:
        def run(self, signals, *, budget_seconds):
            calls["n"] += 1
            return IncumbentOutput(conclusions=(
                IncumbentConclusion("finance", "finance: from your records, dated 2026-10-01 — not re-verified",
                                    "abc123"),))
    texts = []
    d = make({"school_email": email(), "calendar": cal()}, incumbent=GuardedIncumbent(Counting()))
    d.publisher = lambda cid, text, v: texts.append(text)
    result = co.run_catchup(req(), d)
    assert calls["n"] == 1 and result.briefing_id
    assert "1 other State File(s) are from your records and not re-verified" in texts[0]
    assert "finance: from your records" not in texts[0]          # collapsed in the standard format


def _incumbent_with_items():
    class WithItems:
        def run(self, signals, *, budget_seconds):
            return IncumbentOutput(conclusions=(
                IncumbentConclusion("finance", "finance: from your records, dated 2026-10-01 — not re-verified", "v1"),
                IncumbentConclusion("inbox", "OI-2: renew passport; due 2026-12-01; from your records [src: OI-2]",
                                    "v2", kind="open_item", due="2026-12-01"),
                IncumbentConclusion("inbox", "OI-1: file form; due 2026-10-01; from your records [src: OI-1]",
                                    "v2", kind="open_item", due="2026-10-01")))
    return GuardedIncumbent(WithItems())


def _run_format(world, fmt, cid=None):
    ledger, _b, make, _p = world
    texts = []
    d = make({"school_email": email(), "calendar": cal()}, incumbent=_incumbent_with_items())
    d.publisher = lambda cid, text, v: texts.append(text)
    co.run_catchup(req(cid or f"cu_{fmt.value}", briefing_format=fmt), d)
    return texts[0]


def test_standard_shows_open_items_overdue_first_and_calendar_line(world):
    text = _run_format(world, BriefingFormat.STANDARD)
    assert "OPEN ITEMS (2, 1 overdue)" in text
    assert text.index("OI-1:") < text.index("OI-2:")
    assert "Calendar: nothing in the next 7 days." in text


def test_deep_lists_every_record_and_flash_is_short(world):
    deep = _run_format(world, BriefingFormat.DEEP)
    assert "FROM YOUR RECORDS (not re-verified; 1)" in deep and "finance: from your records" in deep
    flash = _run_format(world, BriefingFormat.FLASH)
    assert "ONE THING" in flash and "GOALS" not in flash and "Calendar:" not in flash
    assert "OPEN ITEMS (2, 1 overdue) — `items` lists them" in flash and "OI-2:" not in flash
    assert len(flash.splitlines()) < len(deep.splitlines())


def test_digest_is_capped_and_never_drops_the_coverage_line(world):
    digest = _run_format(world, BriefingFormat.DIGEST)
    assert "Coverage:" in digest and "Next check" in digest
    assert len(digest.split()) < 300


def test_every_model_request_leaves_a_disclosure_record_without_the_prompt(world):
    ledger, _b, make, _p = world
    seen = []
    d = make({"school_email": email(), "calendar": cal()})
    d.model_log = seen.append
    d.provider_label = "session-assistant"
    co.run_catchup(req("cu_log"), d)
    assert seen and all(e["purpose"] == "extract" and e["provider"] == "session-assistant" for e in seen)
    assert all(e["request_id"].startswith("evd_") and "Registration" not in json.dumps(e) for e in seen)
    assert {"data_class", "retention", "catchup_id", "at"} <= set(seen[0])
