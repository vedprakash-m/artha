"""Leading indicators, Sprint calibration and evidence-backed tradeoffs (RA-11). Unknown stays unknown."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from catchup_core.goals import (GoalObservation, calibrate_sprint, goal_tradeoffs, leading_indicators)
from catchup_core.plans import MilestoneState

TODAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, 16, tzinfo=timezone.utc)


def ms(state, target=None, overdue=False, goal="g1", mid="m"):
    return MilestoneState(goal, mid, "t", state, target_date=target, overdue=overdue)


def test_given_milestone_states_when_summarised_then_ready_blocked_overdue_and_due_soon_are_counted():
    states = [ms("done"), ms("ready"), ms("ready", date(2026, 10, 15)), ms("blocked", date(2026, 10, 30)),
              ms("blocked", date(2026, 10, 1), overdue=True), ms("unverified")]
    got = leading_indicators("g1", states, TODAY)
    assert (got.ready, got.blocked, got.overdue, got.due_soon, got.unverified) == (2, 2, 1, 1, 1)
    assert got.known is True


def test_given_an_invalid_plan_when_summarised_then_no_counts_are_claimed():
    got = leading_indicators("g1", [ms("ready"), ms("invalid")], TODAY)
    assert got.known is False and got.ready is None and got.overdue is None and "review" in got.reason


def test_given_no_milestones_for_the_goal_when_summarised_then_the_result_is_unknown():
    got = leading_indicators("g1", [ms("ready", goal="other")], TODAY)
    assert got.known is False and got.ready is None


def obs(value, *, kind="milestones", target=4, baseline=None, fresh=1.0, facts=("f1",)):
    return GoalObservation("g1", value, TODAY, TODAY, facts, NOW, fresh, metric_kind=kind, target=target,
                           baseline=baseline)


SPRINT = {"id": "s1", "goal_ref": "g1", "status": "active", "start": "2026-10-01", "end": "2026-10-20"}


@pytest.mark.parametrize(("value", "status"), [(3, "ahead"), (2, "aligned"), (1, "behind")])
def test_given_progress_against_elapsed_time_when_calibrated_then_it_is_ahead_aligned_or_behind(value, status):
    # 12 of 20 days elapsed (0.60); progress is value/4.
    assert calibrate_sprint(SPRINT, obs(value), TODAY, tolerance=Decimal("0.10")).status == status


def test_given_a_decreasing_outcome_when_calibrated_then_progress_runs_from_baseline_to_target():
    o = obs(Decimal("170"), kind="outcome", target=Decimal("150"), baseline=Decimal("200"))
    cal = calibrate_sprint(SPRINT, o, TODAY, tolerance=Decimal("0.10"))
    assert cal.progress == Decimal("0.6") and cal.status == "aligned"


@pytest.mark.parametrize("o", [obs(None), obs(2, fresh=100.0), obs(2, facts=()),
                               obs(Decimal("5"), kind="outcome", target=Decimal("9"), baseline=None),
                               obs(5, target=None)])
def test_given_missing_stale_unsupported_or_baseless_evidence_when_calibrated_then_unknown(o):
    assert calibrate_sprint(SPRINT, o, TODAY, tolerance=Decimal("0.10")).status == "unknown"


@pytest.mark.parametrize("today", [date(2026, 9, 30), date(2026, 10, 21)])
def test_given_today_outside_the_sprint_when_calibrated_then_unknown(today):
    assert calibrate_sprint(SPRINT, obs(2), today, tolerance=Decimal("0.10")).status == "unknown"


def test_given_a_non_active_or_malformed_sprint_when_calibrated_then_unknown():
    for bad in ({**SPRINT, "status": "done"}, {**SPRINT, "end": "bad"}, {**SPRINT, "end": "2026-09-01"}):
        assert calibrate_sprint(bad, obs(2), TODAY, tolerance=Decimal("0.10")).status == "unknown"


def test_given_target_equals_baseline_when_calibrated_then_unknown_not_a_division_error():
    o = obs(Decimal("5"), kind="outcome", target=Decimal("5"), baseline=Decimal("5"))
    assert calibrate_sprint(SPRINT, o, TODAY, tolerance=Decimal("0.10")).status == "unknown"


def cal(goal_id, status):
    from catchup_core.goals import SprintCalibration
    return SprintCalibration(goal_id, status, None, None, "")


GOALS = [{"id": "g1", "status": "active", "competes_with": ["g2"]}, {"id": "g2", "status": "active"},
         {"id": "g3", "status": "active"}]


def test_given_authored_competing_goals_with_opposite_calibration_when_compared_then_one_tradeoff_is_proposed():
    obs_by = {"g1": obs(1, facts=("f1",)), "g2": obs(3, facts=("f2", "f3"))}
    got = goal_tradeoffs(GOALS, {"g1": cal("g1", "behind"), "g2": cal("g2", "ahead")}, obs_by)
    assert len(got) == 1
    assert got[0].behind_goal == "g1" and got[0].ahead_goal == "g2"
    assert got[0].evidence_fact_ids == ("f1", "f2", "f3") and got[0].proposal_only is True


@pytest.mark.parametrize("calibrations", [
    {"g1": "behind", "g2": "unknown"}, {"g1": "behind", "g2": "aligned"}, {"g1": "ahead", "g2": "ahead"}])
def test_given_unknown_or_non_opposed_calibration_when_compared_then_no_tradeoff(calibrations):
    obs_by = {"g1": obs(1), "g2": obs(3, facts=("f2",))}
    assert goal_tradeoffs(GOALS, {k: cal(k, v) for k, v in calibrations.items()}, obs_by) == []


def test_given_no_authored_link_or_no_evidence_when_compared_then_none_is_inferred():
    unlinked = [{"id": "g1", "status": "active"}, {"id": "g2", "status": "active"}]
    both = {"g1": cal("g1", "behind"), "g2": cal("g2", "ahead")}
    assert goal_tradeoffs(unlinked, both, {"g1": obs(1), "g2": obs(3)}) == []
    assert goal_tradeoffs(GOALS, both, {"g1": obs(1, facts=()), "g2": obs(3)}) == []
    assert goal_tradeoffs(GOALS, both, {"g1": obs(1)}) == []                         # g2 has no observation


def test_given_a_link_to_an_inactive_or_missing_goal_when_compared_then_it_is_ignored():
    goals = [{"id": "g1", "status": "active", "competes_with": ["g2", "zz"]}, {"id": "g2", "status": "paused"}]
    both = {"g1": cal("g1", "behind"), "g2": cal("g2", "ahead")}
    assert goal_tradeoffs(goals, both, {"g1": obs(1), "g2": obs(3)}) == []
