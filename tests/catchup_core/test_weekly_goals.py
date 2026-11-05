"""Weekly comparisons use dated permitted evidence, not fabricated historical Goal snapshots."""
from __future__ import annotations

from argparse import Namespace
from datetime import timedelta
from decimal import Decimal

import pytest
import yaml

from catchup_core import cli, commands, goal_metrics, goals, views
from catchup_core.contracts import ProcessingState, Provenance
from catchup_core.ledger import FactInput, SignalInput
from catchup_core.mcp import handle, tool_runner
from test_goal_metrics import coverage, evidence, metric, payload
from test_release_regressions import NOW
import test_goal_metrics


@pytest.fixture
def world(tmp_path):
    yield from test_goal_metrics.world.__wrapped__(tmp_path)


@pytest.mark.parametrize('old,new,change', [
    ('100.00', '125.25', '25.25'), ('130.50', '125.25', '-5.25'),
    ('99999999999999999999.9999999999', '99999999999999999999.9999999998', '-0.0000000001')])
def test_quantity_weekly_change_keeps_exact_units_dates_and_dependencies(world, old, new, change):
    led = world[0]
    coverage(led)
    prior = evidence(led, f'G1 2026-10-05 {old} USD')
    current = evidence(led, f'G1 2026-10-12 {new} USD')
    _, observation, assessment, used = goal_metrics.observe_metric(led, metric(), 'owner', NOW, (), weekly=True)
    assert observation.value == Decimal(new) and assessment.status != 'unknown'
    assert observation.weekly.delta == Decimal(change) and observation.weekly.previous_value == Decimal(old)
    assert observation.weekly.current_end.isoformat() == '2026-10-12'
    assert observation.weekly.previous_end.isoformat() == '2026-10-05'
    assert set(observation.source_fact_ids) == {current}
    assert set(observation.weekly.prior_fact_ids) == {prior} and {fact.fact_id for fact in used} == {prior, current}
    assert f'{Decimal(change):+f} USD' in observation.weekly.reason


@pytest.mark.parametrize('baseline', [None, 'G1 2026-10-04 100.00 USD', 'G1 2026-10-05 100.00 EUR'])
def test_missing_or_incomparable_baseline_does_not_erase_current_measurement(world, baseline):
    led = world[0]
    coverage(led)
    if baseline:
        evidence(led, baseline)
    evidence(led, 'G1 2026-10-12 125.25 USD')
    _, observation, assessment, _ = goal_metrics.observe_metric(led, metric(), 'owner', NOW, (), weekly=True)
    assert observation.value == Decimal('125.25') and assessment.status == 'in_progress'
    assert observation.weekly.delta is None and 'not verified' in observation.weekly.reason


def test_conflicting_baseline_and_unselected_account_remain_unknown(world):
    led = world[0]
    coverage(led)
    private = evidence(led, 'G1 2026-10-05 90.00 USD', scope='private')
    evidence(led, 'G1 2026-10-12 125.25 USD')
    _, observation, _, used = goal_metrics.observe_metric(led, metric(), 'owner', NOW, (), weekly=True)
    assert observation.weekly.delta is None and private not in {fact.fact_id for fact in used}
    evidence(led, 'G1 2026-10-05 100.00 USD')
    evidence(led, 'G1 2026-10-05 101.00 USD')
    _, observation, _, _ = goal_metrics.observe_metric(led, metric(), 'owner', NOW, (), weekly=True)
    assert observation.value == Decimal('125.25') and observation.weekly.delta is None


@pytest.mark.parametrize('period', [3, 7])
def test_habit_weekly_comparison_preserves_the_authored_window_and_denominator(world, period):
    led = world[0]
    coverage(led)
    for offset in list(range(period)) + list(range(7, 7 + period)):
        done = offset < period - 1 or offset == 7
        text = f"G1 {(NOW.date() - timedelta(days=offset)).isoformat()} {'done' if done else 'skipped'}"
        evidence(led, text, predicate='goal_checkin')
    _, observation, _, used = goal_metrics.observe_metric(led, metric('habit', period_days=period, target=2),
                                                          'owner', NOW, (), weekly=True)
    assert observation.value == period - 1 and observation.weekly.previous_value == 1
    assert observation.weekly.delta == period - 2 and len(used) == period * 2
    assert (observation.weekly.current_end - observation.weekly.current_start).days == period - 1
    assert observation.weekly.previous_end == NOW.date() - timedelta(days=7)


def test_missing_prior_checkin_is_unknown_not_a_missed_day(world):
    led = world[0]
    coverage(led)
    for offset in range(13):  # One day missing from the prior seven-day window.
        evidence(led, f'G1 {(NOW.date() - timedelta(days=offset)).isoformat()} done', predicate='goal_checkin')
    _, observation, _, _ = goal_metrics.observe_metric(led, metric('habit'), 'owner', NOW, (), weekly=True)
    assert observation.value == 7 and observation.weekly.delta is None


@pytest.mark.parametrize('condition', ['stale', 'partial', 'missing', 'conflicting', 'future'])
def test_unverified_current_measurement_cannot_create_weekly_movement(world, condition):
    led = world[0]
    coverage(led, status='degraded' if condition == 'partial' else 'complete',
             completed_at=(NOW - timedelta(days=3)).isoformat() if condition == 'stale' else NOW.isoformat())
    evidence(led, 'G1 2026-10-05 100.00 USD')
    if condition != 'missing':
        evidence(led, 'G1 2026-10-12 125.25 USD')
    if condition in {'conflicting', 'future'}:
        evidence(led, f"G1 {'2026-10-13' if condition == 'future' else '2026-10-12'} 126.00 USD")
    _, observation, assessment, _ = goal_metrics.observe_metric(led, metric(), 'owner', NOW, (), weekly=True)
    assert observation.value is None and assessment.status == 'unknown'
    assert observation.weekly.delta is None and observation.weekly.previous_value is None


def test_unrequested_comparison_keeps_the_current_window_and_owner_confirmation_wins(world):
    led = world[0]
    coverage(led)
    prior = evidence(led, 'G1 2026-10-05 100.00 USD')
    evidence(led, 'G1 2026-10-12 120.00 USD', provenance=Provenance.MODEL_HYPOTHESIS)
    current = evidence(led, 'G1 2026-10-12 125.25 USD', provenance=Provenance.USER_CONFIRMED)
    _, observation, _, used = goal_metrics.observe_metric(led, metric(), 'owner', NOW, ())
    assert observation.value == Decimal('125.25') and observation.weekly is None
    assert prior not in {fact.fact_id for fact in used} and current in {fact.fact_id for fact in used}
    _, observation, _, _ = goal_metrics.observe_metric(led, metric(), 'owner', NOW, (), weekly=True)
    assert observation.weekly.delta == Decimal('25.25')


def test_correction_and_forgetting_recompute_weekly_input_versions(world):
    led = world[0]
    coverage(led)
    prior = evidence(led, 'G1 2026-10-05 100.00 USD')
    evidence(led, 'G1 2026-10-12 125.25 USD')
    assert any('+25.25 USD' in line for line in goals.observe_goals(led, [metric()], 'owner', NOW, weekly=True))
    old = led.get_fact(prior, 'owner')
    views.correct(led, 'owner', prior, 'G1 2026-10-05 110.00 USD', old.recorded_at)
    assert any('+15.25 USD' in line for line in goals.observe_goals(led, [metric()], 'owner', NOW, weekly=True))
    assert payload(led)['fact_versions'][prior] != old.recorded_at
    signal = led.signal_row(old.evidence[0][0])
    led.forget('capture', 'owner', signal['upstream_id'])
    assert not led.conn.execute('SELECT 1 FROM goal_observations').fetchone()
    lines = goals.observe_goals(led, [metric()], 'owner', NOW, persist=False, weekly=True)
    assert any('weekly movement not verified' in line for line in lines)


@pytest.mark.parametrize('provenance', [Provenance.LEGACY_UNVERIFIED, Provenance.MODEL_HYPOTHESIS, Provenance.DISPUTED])
def test_unverified_provenance_cannot_establish_current_or_weekly_progress(world, provenance):
    led = world[0]
    coverage(led)
    evidence(led, 'G1 2026-10-05 100.00 USD')
    evidence(led, 'G1 2026-10-12 125.25 USD', provenance=provenance)
    _, observation, assessment, _ = goal_metrics.observe_metric(led, metric(), 'owner', NOW, ())
    assert observation.value is None and assessment.status == 'unknown'
    _, observation, assessment, _ = goal_metrics.observe_metric(led, metric(), 'owner', NOW, (), weekly=True)
    assert observation.value is None and assessment.status == 'unknown' and observation.weekly.delta is None


def test_unverified_deadline_cannot_establish_on_time_progress_or_fabricate_weekly_history(world):
    led = world[0]
    coverage(led)
    sid, _ = led.upsert_signal(SignalInput('capture', 'owner', 'deadline', 'v1', NOW.isoformat(), 'h', 'synthetic'))
    led.put_fact(FactInput('kids_school', 'exam_registration_deadline', '2026-10-12', 'owner', NOW.isoformat(),
                          provenance=Provenance.MODEL_HYPOTHESIS, evidence=((sid, 0, 10),)))
    led.set_processing_state(sid, ProcessingState.RECONCILED)
    row = metric('on_time_obligations', domain='kids_school', predicate='exam_registration_deadline', target=1)
    _, observation, assessment, _ = goals._on_time_metric(led, row, 'owner', NOW, ())
    assert observation.value is None and assessment.status == 'unknown'
    lines = goals.observe_goals(led, [row], 'owner', NOW, persist=False, weekly=True)
    assert any('weekly movement not verified for this metric' in line for line in lines)


@pytest.mark.parametrize("timing", ["future_observation", "future_coverage", "mixed_coverage"])
def test_temporally_unverified_deadline_cannot_establish_on_time_progress(world, timing):
    led = world[0]
    coverage(led, completed_at=(NOW + timedelta(seconds=1)).isoformat()
             if timing == "future_coverage" else NOW.isoformat())
    if timing == "mixed_coverage":
        led.set_coverage("capture", "second", "complete", catchup_id="c",
                         completed_at=(NOW + timedelta(seconds=1)).isoformat())
    sid, _ = led.upsert_signal(SignalInput("capture", "owner", "deadline", "v1", NOW.isoformat(), "h", "synthetic"))
    led.put_fact(FactInput("kids_school", "exam_registration_deadline", "2026-10-12", "owner",
                          (NOW + timedelta(seconds=1)).isoformat() if timing == "future_observation"
                          else NOW.isoformat(),
                          evidence=((sid, 0, 10),)))
    led.set_processing_state(sid, ProcessingState.RECONCILED)
    row = metric("on_time_obligations", domain="kids_school", predicate="exam_registration_deadline", target=1)
    if timing == "mixed_coverage":
        row["metric"]["sources"] = ["capture:owner", "capture:second"]
    _, observation, assessment, _ = goals._on_time_metric(led, row, "owner", NOW, ())
    assert observation.value is None and assessment.status == "unknown"


def test_future_prior_window_keeps_current_progress_but_not_weekly_delta(world):
    led = world[0]
    coverage(led)
    evidence(led, "G1 2026-10-05 100.00 USD", observed_at=(NOW + timedelta(seconds=1)).isoformat())
    evidence(led, "G1 2026-10-12 150.00 USD")
    _, observation, assessment, _ = goal_metrics.observe_metric(led, metric(), "owner", NOW, (), weekly=True)
    assert assessment.status == "on_track" and observation.value == Decimal("150")
    assert observation.weekly.previous_value is None and observation.weekly.delta is None


def test_weekly_command_and_mcp_share_the_read_only_goal_model(world, tmp_path):
    led = world[0]
    coverage(led)
    evidence(led, 'G1 2026-10-05 100.00 USD')
    evidence(led, 'G1 2026-10-12 125.25 USD')
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'goals.md').write_text(yaml.safe_dump({'goals': [metric()]}))
    deps = world[2]()
    before = led.conn.total_changes
    led.conn.execute('PRAGMA query_only=ON')
    result = commands.execute(Namespace(command='goals', weekly=True), deps, tmp_path)
    assert any('+25.25 USD' in line for line in result['observations'])
    response = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                       'params': {'name': 'goals', 'arguments': {'weekly': True}}},
                      tool_runner(lambda args: commands.execute(args, deps, tmp_path)))
    assert response['result']['isError'] is False
    assert '+25.25 USD' in response['result']['content'][0]['text'] and led.conn.total_changes == before
    assert cli.parse_args(['goals', '--weekly', '--shadow']).weekly is True


@pytest.mark.parametrize('weekly', ['true', 'false', 1, 0, None, []])
def test_mcp_weekly_option_requires_a_literal_boolean(weekly):
    called = []
    response = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                       'params': {'name': 'goals', 'arguments': {'weekly': weekly}}},
                      lambda *args: called.append(args))
    assert response['result']['isError'] is True and not called


def test_mcp_weekly_false_is_an_explicit_literal_boolean():
    called = []
    response = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                       'params': {'name': 'goals', 'arguments': {'weekly': False}}},
                      lambda name, args: called.append(args) or {})
    assert response['result']['isError'] is False and called == [{'weekly': False}]
