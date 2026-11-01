"""Offline resource admission at the coordinator's extraction boundary."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as tc

from catchup_core import coordinator as co
from catchup_core import extract as ex
from catchup_core import exchange as xc
from catchup_core.contracts import ProcessingState, RunStatus
from catchup_core.resources import AdmissionStore, ResourcePolicy

world = tc.world
EMPTY = json.dumps({'complete': True, 'candidates': []})


def configured(make, ledger, *, records=None, extractor=tc.EXTRACT, **limits):
    policy = ResourcePolicy(**{
        'max_requests_per_bundle': 10, 'max_prompt_bytes_per_bundle': 1000000,
        'max_requests_per_catchup': 100, 'max_requests_per_month': 100,
        'max_attempts_per_signal': 2, 'max_pending_signals': 10,
        'max_priority_streak': 2, **limits})
    fetch = tc.email() if records is None else tc.email(records)
    return make({'school_email': fetch, 'calendar': tc.cal()}, extractor=extractor,
                admissions=AdmissionStore(ledger, policy), require_resource_policy=True)


def runner(deps, sequence=1):
    run = co._Run(tc.req(), deps, sequence)
    if not deps.ledger.conn.execute('SELECT 1 FROM signals').fetchone():
        run._stage_fetch()
        run._stage_normalize()
    return run


def test_missing_required_policy_preserves_backlog_without_disclosure(world):
    ledger, _, make, _ = world
    calls = []
    deps = make({'school_email': tc.email(), 'calendar': tc.cal()},
                extractor=lambda prompt: calls.append(prompt), require_resource_policy=True)
    result = co.run_catchup(tc.req(), deps)
    assert result.status is RunStatus.DEGRADED and not calls
    assert ledger.signals_in_state(ProcessingState.NORMALIZED)
    assert ledger.conn.execute("SELECT stage FROM signal_failures").fetchone()[0] == 'resource'
    assert not ledger.conn.execute("SELECT 1 FROM signal_failures WHERE stage='extract'").fetchone()


def test_bundle_limit_is_shared_across_signals_and_preserves_verified_alert(world):
    ledger, _, make, _ = world
    records = [co.ConnectorRecord(str(index), tc.MSG) for index in range(3)]
    deps = configured(make, ledger, records=records, max_requests_per_bundle=1)
    result = co.run_catchup(tc.req(), deps)
    assert result.status is RunStatus.DEGRADED and result.artifact_ids
    assert len(ledger.signals_in_state(ProcessingState.NORMALIZED)) == 2
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 1
    assert 'Nothing urgent' not in result.briefing_text


def test_utf8_bytes_not_characters_control_admission(world):
    ledger, _, make, _ = world
    text = '\u00e9' * 80
    signal = ex.NormalizedSignal('unused', 'school_email', text, 'school_email', co._iso(tc.NOW))
    prompt = ex.build_request(tc.PACK, signal, ['kid1', 'kid2']).prompt
    calls = []
    deps = configured(make, ledger, records=[co.ConnectorRecord('unicode', text)],
                      extractor=lambda prompt: calls.append(prompt) or EMPTY,
                      max_prompt_bytes_per_bundle=len(prompt))
    runner(deps)._stage_extract()
    assert not calls and ledger.signals_in_state(ProcessingState.NORMALIZED)


def test_completed_chunks_are_cached_and_not_repeated_across_invocations(world):
    ledger, _, make, _ = world
    calls = []
    deps = configured(make, ledger, records=[co.ConnectorRecord('long', 'a' * 7600 + 'b' * 7600 + 'c')],
                      extractor=lambda prompt: calls.append(prompt) or EMPTY,
                      max_requests_per_bundle=1, max_attempts_per_signal=1)
    for sequence in (1, 2, 3):
        runner(deps, sequence)._stage_extract()
    assert len(calls) == 3 and len(set(calls)) == 3
    assert len(ledger.signals_in_state(ProcessingState.EXTRACTED)) == 1
    assert ledger.conn.execute('SELECT count(*) FROM resource_cache').fetchone()[0] == 3
    assert not ledger.failed_signals()


def test_uncertain_prior_command_effect_is_not_rerun(world):
    ledger, _, make, _ = world
    calls = []
    deps = configured(make, ledger, extractor=lambda prompt: calls.append(prompt) or (_ for _ in ()).throw(
        ConnectionError('uncertain command effect')))
    runner(deps)._stage_extract()
    runner(deps, 2)._stage_extract()
    assert len(calls) == 1
    assert not ledger.conn.execute('SELECT 1 FROM resource_cache').fetchone()
    assert ledger.signals_in_state(ProcessingState.NORMALIZED)


def test_exchange_metadata_lookup_does_not_call_extractor():
    exchange = xc.ExchangeExtractor({xc.request_id('one'): EMPTY})
    assert exchange.has_response('one') is True
    assert exchange.has_response('two') is False


def test_pending_exchange_is_bounded_and_returned_answers_are_not_disclosures(world):
    ledger, _, make, _ = world
    records = [co.ConnectorRecord(str(index), tc.MSG) for index in range(3)]
    deps = configured(make, ledger, records=records, extractor=xc.ExchangeExtractor(),
                      max_requests_per_bundle=1)
    run = runner(deps)
    with pytest.raises(xc.AwaitingExtraction) as pending:
        run._stage_extract()
    assert len(pending.value.requests) == 1 and run.model_requests == 1
    request = pending.value.requests[0]
    deps.extractor = xc.ExchangeExtractor({request.request_id: tc.EXTRACT(request.prompt)})
    resumed = runner(deps, 2)
    resumed._stage_extract()
    assert resumed.model_requests == 0
    assert ledger.conn.execute('SELECT count(*) FROM resource_cache').fetchone()[0] == 2


@pytest.mark.parametrize('output', ['not JSON', '{"complete":false,"candidates":[]}',
    '{"complete":true,"candidates":[{"predicate":"invented"}]}'])
def test_unacceptable_output_is_not_cached(world, output):
    ledger, _, make, _ = world
    deps = configured(make, ledger, extractor=lambda prompt: output)
    runner(deps)._stage_extract()
    assert not ledger.conn.execute('SELECT 1 FROM resource_cache').fetchone()
    assert ledger.failed_signals()


def test_cancelled_catchup_does_not_consume_poison_attempts(world):
    ledger, _, make, _ = world
    calls = []
    deps = configured(make, ledger, extractor=lambda prompt: calls.append(prompt) or EMPTY)
    run = runner(deps)
    deps.admissions.cancel('cu_1', 'ved')
    run._stage_extract()
    assert not calls and ledger.signals_in_state(ProcessingState.NORMALIZED)
    assert not ledger.conn.execute("SELECT 1 FROM signal_failures WHERE stage='extract'").fetchone()


def test_cancel_after_reservation_but_before_disclosure_sends_nothing_and_keeps_the_reservation(world):
    ledger, _, make, _ = world
    calls = []
    deps = configured(make, ledger, extractor=lambda prompt: calls.append(prompt) or EMPTY)
    run = runner(deps)
    original = deps.admissions.admit

    def admit_then_cancel(*args, **kwargs):
        admitted = original(*args, **kwargs)
        deps.admissions.cancel(args[0], 'ved')
        return admitted

    deps.admissions.admit = admit_then_cancel
    run._stage_extract()
    assert not calls and ledger.signals_in_state(ProcessingState.NORMALIZED)
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] >= 1
    assert deps.admissions.uncertain_requests('cu_1')


def consent(ledger):
    ledger._consent_subjects = frozenset({'kid1'})
    return ledger.grant_consent('kid1', 'kid1', 'school_email:acct', ['ingest', 'disclose', 'view'], 'ved')


def test_revoked_permission_prevents_even_cache_lookup(world, monkeypatch):
    ledger, _, make, _ = world
    grant = consent(ledger)
    deps = configured(make, ledger)
    run = runner(deps)
    row = ledger.signals_in_state(ProcessingState.NORMALIZED)[0]
    signal = ex.NormalizedSignal(row['signal_id'], row['connector'], tc.MSG, 'school_email', row['observed_at'])
    run._extract_chunk(signal, deps.subjects)
    ledger.revoke_consent(grant)
    monkeypatch.setattr(deps.admissions, 'cached_output', lambda *args: pytest.fail('unauthorized cache lookup'))
    assert run._extract_one(row) is False
    assert ledger.signals_in_state(ProcessingState.NORMALIZED)


def test_permission_is_rechecked_before_using_cached_output(world, monkeypatch):
    ledger, _, make, _ = world
    grant = consent(ledger)
    deps = configured(make, ledger)
    run = runner(deps)
    row = ledger.signals_in_state(ProcessingState.NORMALIZED)[0]
    signal = ex.NormalizedSignal(row['signal_id'], row['connector'], tc.MSG, 'school_email', row['observed_at'])
    run._extract_chunk(signal, deps.subjects)
    cached = deps.admissions.cached_output
    def revoke_after_read(*args):
        output = cached(*args)
        ledger.revoke_consent(grant)
        return output
    monkeypatch.setattr(deps.admissions, 'cached_output', revoke_after_read)
    assert run._extract_one(row) is False
    assert not ledger.candidates_for(row['signal_id'])
    assert ledger.signals_in_state(ProcessingState.NORMALIZED)


def test_superseded_source_is_not_read_from_cache(world, monkeypatch):
    ledger, _, make, _ = world
    deps = configured(make, ledger)
    run = runner(deps)
    row = ledger.signals_in_state(ProcessingState.NORMALIZED)[0]
    monkeypatch.setattr(ledger, 'source_is_obsolete', lambda sid: True)
    monkeypatch.setattr(deps.admissions, 'cached_output', lambda *args: pytest.fail('obsolete cache lookup'))
    assert run._extract_one(row) is False


def test_cached_partial_survives_crash_after_checkpoint(world, monkeypatch):
    ledger, _, make, _ = world
    calls = []
    deps = configured(make, ledger, records=[co.ConnectorRecord('long', 'a' * 7600 + 'b')],
                      extractor=lambda prompt: calls.append(prompt) or EMPTY)
    checkpoint = deps.admissions.cache_output
    def crash_after_checkpoint(*args):
        checkpoint(*args)
        raise co.StageFault('crash after durable chunk')
    monkeypatch.setattr(deps.admissions, 'cache_output', crash_after_checkpoint)
    with pytest.raises(co.StageFault):
        runner(deps)._stage_extract()
    monkeypatch.setattr(deps.admissions, 'cache_output', checkpoint)
    runner(deps, 2)._stage_extract()
    assert len(calls) == 2 and len(set(calls)) == 2
    assert ledger.signals_in_state(ProcessingState.EXTRACTED)


@pytest.mark.parametrize('change', ['cohort', 'prompt', 'permission'])
def test_exact_key_binds_current_context(world, change):
    from dataclasses import replace
    ledger, _, make, _ = world
    calls = []
    deps = configured(make, ledger, extractor=lambda prompt: calls.append(prompt) or EMPTY)
    run = runner(deps)
    row = ledger.signals_in_state(ProcessingState.NORMALIZED)[0]
    signal = ex.NormalizedSignal(row['signal_id'], row['connector'], tc.MSG, 'school_email', row['observed_at'])
    run._extract_chunk(signal, deps.subjects)
    if change == 'cohort':
        deps.evaluation_cohort = {'model': 'different'}
    elif change == 'prompt':
        signal = replace(signal, text=tc.MSG + ' New instructions from school.')
    else:
        grant = ledger.grant_consent('kid2', 'ved', 'school_email:acct', ['view'], 'ved')
        ledger.revoke_consent(grant)
    run._extract_chunk(signal, deps.subjects)
    assert len(calls) == 2
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 2


def test_pending_scheduler_is_used_with_current_catchup_and_no_extracted_replay(world, monkeypatch):
    ledger, _, make, _ = world
    records = [co.ConnectorRecord(str(index), tc.MSG) for index in range(3)]
    deps = configured(make, ledger, records=records, max_pending_signals=1)
    run = runner(deps)
    select = deps.admissions.select_pending
    seen = []
    def scheduled(*args, **kwargs):
        seen.append(kwargs)
        return select(*args, **kwargs)
    monkeypatch.setattr(deps.admissions, 'select_pending', scheduled)
    run._stage_extract()
    assert seen == [{'current_catchup_id': 'cu_1'}]
    assert len(ledger.signals_in_state(ProcessingState.EXTRACTED)) == 1
    assert len(ledger.signals_in_state(ProcessingState.NORMALIZED)) == 2


def test_repeated_pending_requests_are_bounded_even_without_new_reservations(world):
    ledger, _, make, _ = world
    deps = configured(make, ledger, records=[co.ConnectorRecord('long', 'a' * 7600 + 'b' * 7600 + 'c')],
                      extractor=xc.ExchangeExtractor(), max_requests_per_bundle=1)
    for sequence in (1, 2):
        run = runner(deps, sequence)
        with pytest.raises(xc.AwaitingExtraction) as pending:
            run._stage_extract()
        assert len(pending.value.requests) == 1 and run.model_requests == 1
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 1

def _exchange_deps(make, ledger, seal=None, **limits):
    deps = configured(make, ledger, extractor=xc.ExchangeExtractor(), **limits)
    if seal is not None:
        deps.admissions = AdmissionStore(ledger, deps.admissions.policy, seal)
    return deps


def test_failed_seal_then_retry_never_discloses_unsealed_spend(world):
    """Given a reservation whose seal write failed, when the Catch-up is retried, then it re-seals first
    and only then emits the request; while the seal still fails nothing is emitted."""
    from catchup_core.resources import MemorySpendSeal, SpendSealError

    class FlakySeal(MemorySpendSeal):
        failing = True

        def advance(self, record):
            if self.failing:
                raise SpendSealError('disk full')
            super().advance(record)

    ledger, _, make, _ = world
    seal = FlakySeal()
    deps = _exchange_deps(make, ledger, seal)
    first = runner(deps, 1)
    first._stage_extract()                       # seal failure: resource failure recorded, nothing emitted
    assert first.model_requests == 0 and not first._pending and seal.read() is None
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 1
    retry_still_failing = runner(deps, 2)
    retry_still_failing._stage_extract()
    assert retry_still_failing.model_requests == 0 and not retry_still_failing._pending
    seal.failing = False
    retry = runner(deps, 3)
    with pytest.raises(xc.AwaitingExtraction) as pending:
        retry._stage_extract()
    assert len(pending.value.requests) == 1 and retry.model_requests == 1
    assert seal.read() is not None and seal.read().requests == 1


def test_supplied_response_is_used_even_when_new_admission_would_exceed_a_cap(world):
    """Given an answer already supplied for a request, when the month cap is full, then the answer is kept."""
    ledger, _, make, _ = world
    deps = _exchange_deps(make, ledger, max_requests_per_month=1)
    with pytest.raises(xc.AwaitingExtraction) as pending:
        runner(deps, 1)._stage_extract()
    request = pending.value.requests[0]
    ledger.conn.execute("UPDATE resource_admissions SET catchup_id='earlier-catchup'")
    ledger.conn.commit()
    deps.extractor = xc.ExchangeExtractor({request.request_id: tc.EXTRACT(request.prompt)})
    resumed = runner(deps, 2)
    resumed._stage_extract()
    assert resumed.model_requests == 0
    assert len(ledger.signals_in_state(ProcessingState.EXTRACTED)) == 1
    assert not ledger.conn.execute("SELECT 1 FROM signal_failures WHERE stage='resource'").fetchone()


def test_missing_required_policy_is_visible_as_policy_needed_in_the_briefing(world):
    """Given no resource limits, when a Catch-up runs, then the coverage line and gaps say policy_needed."""
    ledger, _, make, _ = world
    deps = make({'school_email': tc.email(), 'calendar': tc.cal()}, require_resource_policy=True)
    result = co.run_catchup(tc.req(), deps)
    text = result.briefing_text or ''
    assert 'policy_needed' in text and 'Nothing urgent' not in text
