"""End-to-end Catch-up scenarios on synthetic data (re-artha §12.5 demonstrations)."""
from __future__ import annotations

import json
from dataclasses import replace
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
        return json.dumps({"complete": True, "candidates": items})
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


@pytest.mark.parametrize('calendar_complete', [False, True])
def test_default_coverage_header_uses_current_required_scopes(world, calendar_complete):
    _, _, make, _ = world
    calendar = cal() if calendar_complete else lambda: co.ConnectorFetch(
        'calendar', 'acct', (), CoverageStatus.UNAVAILABLE)
    deps = make({'school_email': email(), 'calendar': calendar})
    deps.coverage_line = lambda: ''
    result = co.run_catchup(req(), deps)
    count = 2 if calendar_complete else 1
    assert f'{count}/2 required source scopes complete this Catch-up' in result.briefing_text


def test_default_coverage_header_states_when_no_required_scopes_are_configured(world):
    from dataclasses import replace
    _, _, make, _ = world
    deps = make({})
    deps.coverage_line = lambda: ''
    deps.pack = replace(deps.pack, sources=tuple(replace(source, required=False) for source in deps.pack.sources))
    result = co.run_catchup(req(), deps)
    assert 'Coverage: no required source scopes configured.' in result.briefing_text


@pytest.mark.parametrize("fmt", list(BriefingFormat))
@pytest.mark.parametrize("calendar_complete", [False, True])
def test_format_does_not_change_fetched_coverage_or_hide_a_gap(world, fmt, calendar_complete):
    _, _, make, _ = world
    calendar = cal() if calendar_complete else cal(status=CoverageStatus.UNAVAILABLE)
    deps = make({"school_email": email(), "calendar": calendar})
    deps.coverage_line = lambda: ""
    result = co.run_catchup(req(briefing_format=fmt), deps)
    count = 2 if calendar_complete else 1
    assert f"{count}/2 required source scopes complete this Catch-up" in result.briefing_text
    assert len(result.coverage) == 2
    assert sum(entry.status is CoverageStatus.COMPLETE for entry in result.coverage) == count
    if not calendar_complete:
        assert result.status is RunStatus.DEGRADED
        assert "calendar" in result.briefing_text.lower()
        assert "all clear" not in result.briefing_text.lower()


def test_later_fetch_cannot_be_relabelled_as_a_paused_flash_runs_coverage(world):
    from catchup_core import exchange
    _, _, make, _ = world
    fetchers = {"school_email": email(), "calendar": cal()}
    flash = req("cu_paused_flash", briefing_format=BriefingFormat.FLASH)
    with pytest.raises(exchange.AwaitingExtraction) as pending:
        co.run_catchup(flash, make(fetchers, extractor=exchange.ExchangeExtractor()))
    later = co.run_catchup(req("cu_later_standard"), make(fetchers))
    assert later.status is RunStatus.COMPLETE
    responses = {request.request_id: EXTRACT(request.prompt) for request in pending.value.requests}
    deps = make(fetchers, extractor=exchange.ExchangeExtractor(responses))
    deps.coverage_line = lambda: ""
    resumed = co.run_catchup(flash, deps)
    assert resumed.status is RunStatus.DEGRADED
    assert "0/2 required source scopes complete this Catch-up" in resumed.briefing_text
    assert "all clear" not in resumed.briefing_text.lower()


def test_checkpoint_fence_blocks_catchup_before_fetch_or_journal_mutation(world, tmp_path):
    from catchup_core.session_lock import session_lock
    led, _, make, published = world
    lock = tmp_path / 'installation.lock'
    calls = []
    deps = make({'school_email': lambda: calls.append('fetch')}, maintenance=lambda: session_lock(lock))
    with session_lock(lock):
        result = co.run_catchup(req(), deps)
    assert result.status is RunStatus.BLOCKED and not calls and not published
    assert led.conn.execute('SELECT count(*) FROM stage_journal').fetchone()[0] == 0


@pytest.mark.parametrize('current_fetched', [False, True])
def test_replaced_account_history_is_not_a_current_required_scope(world, current_fetched):
    led, _, make, _ = world
    led.set_coverage('school_email', 'retired', 'complete', completed_at=NOW.isoformat(), catchup_id='old')
    led.set_source_subjects('school_email', 'retired', ['kid1'])
    fetchers = {'school_email': email(), 'calendar': cal()} if current_fetched else {}
    deps = make(fetchers)
    deps.coverage_line = lambda: ''
    deps.expected_scopes = {'school_email': 'acct', 'calendar': 'acct'}
    result = co.run_catchup(req(), deps)
    assert not any(entry.account_scope == 'retired' for entry in result.coverage)
    assert len([entry for entry in result.coverage if entry.required]) == 2
    assert f'{2 if current_fetched else 0}/2 required source scopes' in result.briefing_text
    assert any(row['account_scope'] == 'retired' for row in led.coverage_rows())


def test_health_uses_current_inventory_without_deleting_retained_coverage(world, tmp_path):
    from argparse import Namespace
    from catchup_core.commands import execute
    led, _, make, _ = world
    for scope in ('retired', 'acct'):
        led.set_coverage('school_email', scope, 'complete', completed_at=NOW.isoformat())
        led.set_source_subjects('school_email', scope, ['kid1'])
    deps = make({})
    deps.expected_scopes = {'school_email': 'acct'}
    assert len(execute(Namespace(command='health'), deps, tmp_path)['coverage']) == 1
    assert len(led.coverage_rows()) == 2


def req(cid="cu_1", mode=ExecutionMode.AUTHORITATIVE, **kw):
    return CatchupRequest(cid, "ved", "cli", mode=mode, host="mac", **kw)


def email(records=(co.ConnectorRecord("m1", MSG),), status=CoverageStatus.COMPLETE, error=None):
    """Dated synthetic mail; missing-occurrence cases inject their own fetcher."""
    dated = tuple(replace(record, occurred_at=record.occurred_at or NOW.isoformat()) for record in records)
    return lambda: co.ConnectorFetch("school_email", "acct", dated, status, error)


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


@pytest.mark.parametrize("occurred_at,explicit_year,accepted", [
    (None, False, False), (None, True, True),
    ("2025-10-01T00:00:00Z", False, False), ("2026-10-01T00:00:00Z", False, True),
])
def test_source_occurrence_alone_anchors_omitted_years(world, occurred_at, explicit_year, accepted):
    ledger, _, make, _ = world
    quote = "registration closes October 14" + (", 2026" if explicit_year else "")
    record = co.ConnectorRecord("dated", quote, occurred_at=occurred_at)
    fetch = lambda: co.ConnectorFetch("school_email", "acct", (record,), CoverageStatus.COMPLETE)
    extractor = model_for({quote: ("exam_registration_deadline", "2026-10-14", "kid1")})
    co.run_catchup(req(), make({"school_email": fetch, "calendar": cal()}, extractor=extractor))
    facts = ledger.conn.execute("SELECT * FROM facts").fetchall()
    assert bool(facts) is accepted
    if accepted:
        source = ledger.conn.execute("SELECT * FROM signals").fetchone()
        assert facts[0]["observed_at"] == source["observed_at"]
        assert facts[0]["occurred_at"] == occurred_at
        assert bool(facts[0]["validity_unknown"]) is True
    else:
        assert ledger.failed_signals()


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
        return json.dumps({"complete": True, "candidates": [good, bad]})
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


def test_a_briefing_that_fails_validation_is_blocked_not_published(world, monkeypatch):
    ledger, _b, make, published = world
    d = make({"school_email": email(), "calendar": cal()})
    from dataclasses import replace
    compose = co.compose
    def invalid(*args, **kwargs):
        result = compose(*args, **kwargs)
        return replace(result, text=result.text + 'Unresolvable generated citation [9]\n')
    monkeypatch.setattr(co, 'compose', invalid)      # a malformed composed artifact can never publish
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


def test_every_run_records_stage_seconds_and_model_requests_without_content(world):
    ledger, _b, make, _p = world
    seen = []
    ticks = iter(range(0, 10_000))
    d = make({"school_email": email(), "calendar": cal()})
    d.metrics = seen.append
    d.perf = lambda: float(next(ticks))
    result = co.run_catchup(req("cu_metrics"), d)
    entry = seen[0]
    assert entry["catchup_id"] == "cu_metrics" and entry["status"] == result.status.value and entry["mode"] == "authoritative"
    assert {"FETCH", "EXTRACT", "COMMIT"} <= set(entry["stages"]) and entry["total_seconds"] > 0
    assert entry["model_requests"] >= 1 and entry["resumed"] is False
    assert entry["prompt_chars"] > 100 and entry["answer_chars"] >= 0
    assert "Registration" not in json.dumps(entry)


def test_a_paused_run_is_recorded_and_a_failing_sink_changes_nothing(world):
    ledger, _b, make, _p = world
    seen = []
    from catchup_core.exchange import AwaitingExtraction, ExtractionPending, PendingRequest
    d = make({"school_email": email(), "calendar": cal()})

    def pause(prompt):
        raise ExtractionPending(PendingRequest("r1", prompt))
    d.extractor = pause
    d.metrics = seen.append
    import pytest
    with pytest.raises(AwaitingExtraction):
        co.run_catchup(req("cu_paused"), d)
    assert seen[0]["status"] == "paused"

    def broken(_entry):
        raise OSError("disk full")
    d2 = make({"school_email": email(), "calendar": cal()})
    d2.metrics = broken
    assert co.run_catchup(req("cu_ok"), d2).briefing_id                     # the Briefing is unaffected


def test_a_run_emits_a_trace_with_the_same_entry_and_a_failing_trace_sink_changes_nothing(world):
    ledger, _b, make, _p = world
    traces = []
    d = make({"school_email": email(), "calendar": cal()})
    d.trace = traces.append
    co.run_catchup(req("cu_trace"), d)
    assert traces[0]["catchup_id"] == "cu_trace" and "stages" in traces[0]
    d2 = make({"school_email": email(), "calendar": cal()})

    def broken(_entry):
        raise OSError("disk full")
    d2.trace = broken
    assert co.run_catchup(req("cu_trace_ok"), d2).briefing_id


def test_a_failing_metrics_sink_does_not_skip_the_trace(world):
    ledger, _b, make, _p = world
    traces = []

    def broken(_entry):
        raise OSError("disk full")
    d = make({"school_email": email(), "calendar": cal()})
    d.metrics, d.trace = broken, traces.append
    co.run_catchup(req("cu_independent"), d)
    assert traces


def test_a_source_stamp_without_an_offset_is_read_as_utc_in_freshness_comparisons():
    from datetime import datetime  # noqa: PLC0415

    from catchup_core.coordinator import _aware  # noqa: PLC0415
    naive = datetime(2026, 10, 5, 12, 0)
    assert _aware(naive).utcoffset().total_seconds() == 0
    assert min(_aware(naive), _aware(datetime.fromisoformat("2026-10-06T00:00:00+00:00"))) == _aware(naive)


def test_storage_refusal_records_a_blocked_sample_instead_of_disappearing(world):
    ledger, _b, make, _p = world
    seen = []
    deps = make({'school_email': email(), 'calendar': cal()})
    deps.metrics = seen.append
    ledger.close()
    outcome = co.run_catchup(req('storage_failed'), deps)
    assert outcome.status.value == 'blocked'
    assert len(seen) == 1 and seen[0]['status'] == 'blocked'
    assert seen[0]['measurement_complete'] is False


def test_pause_metrics_include_attempted_stage_and_invocation_identity(world):
    from catchup_core.exchange import AwaitingExtraction, ExtractionPending, PendingRequest
    ledger, _b, make, _p = world
    entries = []
    deps = make({'school_email': email(), 'calendar': cal()})
    def paused(prompt):
        raise ExtractionPending(PendingRequest('pause', prompt))
    deps.extractor = paused
    deps.metrics = entries.append
    import pytest
    with pytest.raises(AwaitingExtraction):
        co.run_catchup(req('paused_metrics'), deps)
    assert entries[0]['invocation_id'] and entries[0]['ended_at']
    assert 'EXTRACT' in entries[0]['stages']


def test_metrics_sequences_survive_reopen_and_are_separate_per_catchup(world):
    ledger, _b, _make, _p = world
    assert ledger.next_metric_sequence('a') == 1
    assert ledger.next_metric_sequence('a') == 2
    assert ledger.next_metric_sequence('b') == 1
    ledger.close()
    ledger.open()
    assert ledger.next_metric_sequence('a') == 3


def test_health_detects_an_invocation_whose_entire_telemetry_append_was_lost(world):
    from catchup_core.commands import _performance
    ledger, _b, _make, _p = world
    ledger.next_metric_sequence('lost', implementation_version='current')
    report = _performance(ledger, ledger._path.parent, implementation_version='current')
    assert report['unknown_runs'] == 1 and report['runs'] == 0
    assert report['cost']['history_complete'] is False


def test_health_reconciles_a_lost_tail_but_does_not_reopen_a_completed_catchup(world):
    from catchup_core.commands import _performance
    ledger, _b, _make, _p = world
    ledger._clock = lambda: '2026-10-22T10:00:01Z'
    directory = ledger._path.parent / 'metrics'
    directory.mkdir()
    first = {'catchup_id': 'recorded', 'mode': 'authoritative', 'implementation_version': 'current',
             'metric_sequence': 1, 'status': 'paused', 'resumed': False, 'total_seconds': 1,
             'at': '2026-10-22T10:00:00Z', 'ended_at': '2026-10-22T10:00:01Z', 'stages': {}}
    ledger.next_metric_sequence('recorded', implementation_version='current')
    path = directory / '2026-10.jsonl'
    path.write_text(json.dumps(first) + '\n')
    ledger.next_metric_sequence('recorded', implementation_version='current')
    lost = _performance(ledger, ledger._path.parent, implementation_version='current')
    assert lost['unknown_runs'] == 1 and lost['paused_runs'] == 0 and lost['cost']['history_complete'] is False
    complete = {**first, 'metric_sequence': 2, 'status': 'complete', 'resumed': True, 'total_seconds': 2,
                'at': '2026-10-22T10:01:00Z', 'ended_at': '2026-10-22T10:01:02Z'}
    path.write_text(json.dumps(first) + '\n' + json.dumps(complete) + '\n')
    ledger.next_metric_sequence('recorded', implementation_version='current')
    final = _performance(ledger, ledger._path.parent, implementation_version='current')
    assert final['runs'] == 1 and final['unknown_runs'] == 0 and final['p95_seconds'] == 62


def test_unreadable_metrics_do_not_crash_health_or_claim_an_empty_verified_history(world, monkeypatch):
    from catchup_core.commands import _performance
    from catchup_core import metrics
    ledger, _b, _make, _p = world
    def unreadable(*args, **kwargs):
        raise OSError('private path detail')
    monkeypatch.setattr(metrics, 'read_runs', unreadable)
    report = _performance(ledger, ledger._path.parent, implementation_version='current')
    assert report['verdict'].startswith('metrics unavailable') and report['cost']['history_complete'] is False
    assert 'private' not in json.dumps(report)


# ── RA-02: a rejected sibling must not discard the valid items (synthetic) ────────────────────

def _items_for(prompt, extra_bad):
    message = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
    i = message.index(QUOTE)
    good = {"predicate": "exam_registration_deadline", "value": "2026-10-14", "subject_id": "kid1",
            "quote": QUOTE, "span": [i, i + len(QUOTE)]}
    return good, message, extra_bad


def test_many_signals_with_rejected_siblings_all_keep_their_valid_facts(world):
    """Given 17 requests where 8 carry a rejected sibling and a malformed other_obligations entry, then every
    signal still yields its valid candidate and the rejection reasons are counted."""
    ledger, _b, make, _p = world
    records = tuple(co.ConnectorRecord(f"m{n}", f"{MSG} Note {n}.") for n in range(17))

    def mixed(prompt):
        good, message, _ = _items_for(prompt, None)
        n = int(message.rsplit("Note ", 1)[1].rstrip("."))
        out = {"complete": True, "candidates": [good]}
        if n % 2 == 0 and n < 16:
            out["candidates"].append(dict(good, predicate="wire_money"))
            out["other_obligations"] = [{"domain": ["finance"], "subject_id": {"a": 1}, "span": [0, 1], "quote": "F"}]
        return json.dumps(out)
    metrics = []
    result = co.run_catchup(req(), make({"school_email": email(records), "calendar": cal()}, extractor=mixed,
                                        metrics=metrics.append))
    rows = ledger.conn.execute("SELECT COUNT(DISTINCT signal_id) FROM candidates").fetchone()[0] \
        if _has_table(ledger, "candidates") else None
    assert result.artifact_ids
    assert rows in (None, 17)
    assert FailureClass.SCHEMA_REJECTED in result.failures
    counts = metrics[-1]["extraction_rejections"]
    assert counts["unknown predicate"] == 8 and counts["invalid non-pilot obligation"] == 8


def _has_table(ledger, name):
    return ledger.conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def test_unexpected_parser_error_in_one_signal_does_not_stop_the_other_signals(world, monkeypatch):
    ledger, _b, make, _p = world
    records = (co.ConnectorRecord("good", MSG), co.ConnectorRecord("boom", "Families: boom newsletter."))
    real = co.ex.parse_output

    def flaky(output, pack, signal, subjects):
        if "boom newsletter" in signal.text:
            raise RuntimeError("unexpected")
        return real(output, pack, signal, subjects)
    monkeypatch.setattr(co.ex, "parse_output", flaky)
    result = co.run_catchup(req(), make({"school_email": email(records), "calendar": cal()}))
    assert result.artifact_ids and FailureClass.EXTRACT in result.failures
    assert len(ledger.failed_signals()) == 1
