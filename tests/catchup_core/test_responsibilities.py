"""RA-25: a written duty is never permission; checks come due at the next Catch-up, not in the background."""
from __future__ import annotations

import pytest

from datetime import date
from pathlib import Path

from catchup_core import runtime as rt
from catchup_core.responsibilities import evaluate_responsibilities, responsibility_lines

TODAY = date(2026, 10, 20)


def rec(**kw):
    base = {"id": "R1", "outcome": "Renew registration", "allowed_operations": ["read", "draft"], "status": "active"}
    base.update(kw)
    return base


def only(records):
    return evaluate_responsibilities(records, TODAY)


def test_never_checked_cadence_elapsed_and_explicit_next_check_are_due():
    assert only([rec()])[0].state == "due" and only([rec()])[0].detail == "never checked"
    elapsed = only([rec(last_checked="2026-10-01", cadence_days=14)])[0]
    assert elapsed.state == "due" and elapsed.next_check == date(2026, 10, 15)
    assert only([rec(last_checked="2026-10-19", cadence_days=14, next_check="2026-10-20")])[0].state == "due"


def test_a_duty_that_is_not_yet_due_stays_quiet():
    states = only([rec(last_checked="2026-10-15", cadence_days=14)])
    assert states[0].state == "waiting" and responsibility_lines(states) == []


def test_operations_beyond_read_draft_propose_are_reported_and_never_granted():
    state = only([rec(allowed_operations=["read", "send", "pay"], last_checked="2026-10-19", cadence_days=30)])
    invalid = [s for s in state if s.state == "invalid"]
    assert invalid and "send, pay" in invalid[0].detail and invalid[0].allowed == ("read",)
    line = responsibility_lines(only([rec(allowed_operations=["send"])]))
    assert any("may only read, draft or propose" in item for item in line)
    assert not any("may send" in item for item in line)


def test_completion_needs_evidence_and_stopped_duties_vanish():
    assert evaluate_responsibilities([rec(status="completed", completion_evidence="confirmation #42")], TODAY,
                                     verify=lambda ref, key: ref == "confirmation #42") == []
    unverified = only([rec(status="completed")])[0]
    assert unverified.state == "unverified" and "no completion evidence" in unverified.detail
    assert only([rec(status="stopped")]) == []


def test_unusable_records_and_dates_never_raise():
    assert all(s.state == "invalid" for s in only([{"id": "", "outcome": "x"}, {"id": "R", "outcome": ""}, {}]))
    bad = only([rec(last_checked="yesterday", cadence_days="often")])[0]
    assert bad.state == "invalid" and "invalid check date or cadence" in bad.detail
    assert len(evaluate_responsibilities([rec(id=f"R{i}") for i in range(80)], TODAY)) == 51


def test_lines_put_due_first_and_name_what_is_allowed():
    states = only([rec(id="R2", status="completed"), rec(id="R1")])
    lines = responsibility_lines(states)
    assert lines[0].startswith("R1: check now") and "(may read, draft)" in lines[0] and lines[1].startswith("R2:")


def test_runtime_reads_the_responsibilities_file_through_the_reader():
    text = ("---\nresponsibilities:\n  - {id: R1, outcome: Pay the bill, allowed_operations: [read], "
            "last_checked: '2026-10-01', cadence_days: 7}\n---\n")
    assert rt._responsibility_lines(Path("."), reader=lambda _n: text, today=TODAY).lines[0].startswith("R1: check now")

    def broken(_n):
        raise OSError("locked")
    with pytest.raises(OSError, match="locked"):
        rt._responsibility_lines(Path("."), reader=broken, today=TODAY)
    assert rt._responsibility_lines(Path("."), reader=lambda _n: "", today=TODAY).lines == []


def test_named_sources_must_be_verified_before_a_due_check_is_reported_ready():
    duty = rec(sources=['school_email:family'])
    states = evaluate_responsibilities([duty], TODAY)
    assert states[0].state == 'unverified' and 'sources' in states[0].detail
    states = evaluate_responsibilities([duty], TODAY, check_source=lambda scope, hours: True)
    assert states[0].state == 'due'
    assert evaluate_responsibilities([rec(sources='email')], TODAY)[0].state == 'invalid'
    assert evaluate_responsibilities([rec(sources=['email'])], TODAY)[0].state == 'invalid'


def test_typed_stop_condition_needs_a_current_verified_evidence_version():
    duty = rec(stop_condition={'fact': 'fact:status@version', 'equals': 'renewed'})
    assert evaluate_responsibilities([duty], TODAY)[0].state == 'unverified'
    result = evaluate_responsibilities([duty], TODAY, check_stop=lambda condition, hours: True)
    assert result[0].state == 'stopped' and 'stop condition verified' in result[0].detail
    assert 'stop condition verified' in responsibility_lines(result)[0]
    assert evaluate_responsibilities([duty], TODAY, check_stop=lambda condition, hours: False)[0].state == 'due'
    assert evaluate_responsibilities([rec(stop_condition={'equals': 'renewed'})], TODAY)[0].state == 'invalid'


def test_invalid_source_freshness_and_unrecognized_lifecycle_do_not_produce_checks():
    for changes in ({'freshness_hours': True}, {'freshness_hours': float('nan')},
                    {'freshness_hours': -1}, {'status': 'typo'}, {'sources': [4]}):
        assert evaluate_responsibilities([rec(**changes)], TODAY)[0].state == 'invalid'


@pytest.fixture
def world(tmp_path):
    import test_release_regressions as regressions
    yield from regressions.world.__wrapped__(tmp_path)


def test_runtime_checks_only_visible_fresh_scopes_and_pending_signals(world):
    import yaml
    from catchup_core.contracts import ProcessingState
    from catchup_core.ledger import SignalInput
    from test_release_regressions import NOW
    led, _, _, _ = world
    duty = rec(sources=['school_email:family'], freshness_hours=24)
    text = yaml.safe_dump({'responsibilities': [duty]})
    def section():
        return rt._responsibility_lines(Path('.'), reader=lambda _: text, today=NOW.date(), now=NOW, ledger=led)
    led.set_coverage('school_email', 'other', 'complete', completed_at=NOW.isoformat())
    led.set_source_subjects('school_email', 'other', ['owner'])
    assert section().gaps and 'sources not verified' in section().lines[0]
    led.set_coverage('school_email', 'family', 'complete', completed_at=NOW.isoformat())
    assert section().gaps  # unattributed source coverage is not authority
    led.set_source_subjects('school_email', 'family', ['owner'])
    assert not section().gaps and 'check now' in section().lines[0]
    for status, instant in [('partial', NOW.isoformat()), ('complete', '2000-01-01T00:00:00+00:00'),
                            ('complete', '2099-01-01T00:00:00+00:00'), ('complete', None), ('complete', 'invalid')]:
        led.set_coverage('school_email', 'family', status, completed_at=instant)
        assert section().gaps
    led.set_coverage('school_email', 'family', 'complete', completed_at=NOW.replace(tzinfo=None).isoformat())
    assert not section().gaps
    led.set_coverage('school_email', 'family', 'complete', completed_at=NOW.isoformat())
    sid, _ = led.upsert_signal(SignalInput('school_email', 'family', 'pending', 'v1', NOW.isoformat(), 'h', 'blob'))
    led.set_signal_subjects(sid, ['owner'])
    assert section().gaps
    led.set_processing_state(sid, ProcessingState.RECONCILED)
    assert not section().gaps


def test_stop_evidence_pins_exact_version_and_rechecks_permission(world):
    import yaml
    from catchup_core.contracts import FactStatus, Provenance
    from catchup_core.ledger import FactInput
    from test_release_regressions import NOW
    led, _, _, _ = world
    observed = FactInput('home', 'registration_status', 'renewed', 'owner', NOW.isoformat(),
                         provenance=Provenance.USER_CONFIRMED)
    fid = led.put_fact(observed)
    version = led.get_fact(fid, 'owner').recorded_at
    duty = rec(stop_condition={'fact': f'fact:{fid}@{version}', 'equals': 'renewed'})
    def section():
        text = yaml.safe_dump({'responsibilities': [duty]})
        return rt._responsibility_lines(Path('.'), reader=lambda _: text, today=NOW.date(), now=NOW, ledger=led)
    generation = led.fact_generation()
    result = section()
    assert 'stop condition verified' in result.lines[0] and not result.gaps
    assert result.fact_versions == {fid: version} and result.citations == {0: (fid,)}
    assert led.fact_generation() == generation  # loading context is read-only
    duty['stop_condition']['equals'] = 'not-renewed'
    assert 'check now' in section().lines[0] and section().fact_versions == {fid: version}
    duty['stop_condition']['equals'] = 'renewed'
    led.set_fact_status(fid, FactStatus.DISPUTED)
    assert section().gaps and 'stop condition evidence' in section().lines[0]
    duty['stop_condition']['fact'] = 'fact:unknown@version'
    assert section().gaps
    led._consent_subjects = frozenset({'adult'})
    adult = led.put_fact(FactInput('home', 'registration_status', 'renewed', 'adult', NOW.isoformat(),
                                  provenance=Provenance.USER_CONFIRMED))
    version = led.conn.execute('SELECT recorded_at FROM facts WHERE fact_id=?', (adult,)).fetchone()[0]
    duty['stop_condition']['fact'] = f'fact:{adult}@{version}'
    assert section().gaps and not section().fact_versions


@pytest.mark.parametrize('change', [dict(status='unknown'), dict(validity_unknown=True),
    dict(provenance='model_hypothesis'), dict(observed_at='2000-01-01T00:00:00+00:00'),
    dict(observed_at='2099-01-01T00:00:00+00:00'), dict(valid_from='2099-01-01'), dict(valid_to='2000-01-01')])
def test_stop_condition_cannot_use_stale_uncertain_or_out_of_interval_facts(world, change):
    from dataclasses import replace
    from catchup_core.contracts import FactStatus, Provenance
    from catchup_core.ledger import FactInput
    from test_release_regressions import NOW
    led, _, _, _ = world
    if 'status' in change:
        change = {**change, 'status': FactStatus(change['status'])}
    if 'provenance' in change:
        change = {**change, 'provenance': Provenance(change['provenance'])}
    fact = replace(FactInput('home', 'registration_status', 'renewed', 'owner', NOW.isoformat()), **change)
    fid = led.put_fact(fact)
    version = led.get_fact(fid, 'owner').recorded_at
    versions = {}
    _, stop = rt._responsibility_checkers(led, 'owner', NOW, versions)
    assert stop({'fact': f'fact:{fid}@{version}', 'equals': 'renewed'}, 72) is None and not versions


def test_duplicate_responsibilities_and_unscheduled_checked_duties_need_review():
    assert all(s.state == 'invalid' for s in only([rec(), rec()]))
    assert only([rec(last_checked='2026-10-01')])[0].state == 'invalid'


def test_missing_ledger_never_verifies_a_stop_condition():
    from test_release_regressions import NOW
    _, stop = rt._responsibility_checkers(None, 'owner', NOW, {})
    assert stop({'fact': 'fact:unknown@version', 'equals': True}, 72) is None


def test_verified_stop_is_informational_and_correction_removes_the_quiet_claim(world, tmp_path):
    import yaml
    from catchup_core import coordinator
    from catchup_core.contracts import FactStatus, Provenance
    from catchup_core.ledger import FactInput
    from test_release_regressions import NOW, request
    led, _, dependency_factory, _ = world
    fid = led.put_fact(FactInput('home', 'registration_status', 'renewed', 'owner', NOW.isoformat(),
                                 provenance=Provenance.USER_CONFIRMED))
    version = led.get_fact(fid, 'owner').recorded_at
    text = yaml.safe_dump({'responsibilities': [rec(stop_condition={'fact': f'fact:{fid}@{version}', 'equals': 'renewed'})]})
    def dependencies():
        deps = dependency_factory()
        deps.responsibility_lines = lambda: rt._responsibility_lines(tmp_path, reader=lambda _: text,
            today=NOW.date(), now=NOW, ledger=led)
        return deps
    first = coordinator.run_catchup(request('stopped'), dependencies())
    assert 'Nothing urgent' in first.briefing_text and 'stop condition verified' in first.briefing_text
    led.set_fact_status(fid, FactStatus.UNKNOWN)
    corrected = coordinator.run_catchup(request('changed'), dependencies())
    assert 'Nothing urgent' not in corrected.briefing_text and 'stop condition evidence' in corrected.briefing_text


def test_last_checked_cannot_be_in_the_future():
    result = only([rec(last_checked='9999-12-31', cadence_days=14)])
    assert result[0].state == 'invalid'
