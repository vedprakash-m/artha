"""Goal observation and assessment (re-artha RA-11 minimum, §7.5).

Authored Goal fields (owner, target, metric definition) are frozen: an observation
never edits them.  A derived :class:`GoalObservation` records what the evidence
supports and how fresh it is; **missing data is unknown, never fabricated
progress**.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from catchup_core.ledger import FactView

__all__ = ["AuthoredGoal", "GoalAssessment", "GoalObservation", "assess", "observe_on_time"]


@dataclass(frozen=True)
class AuthoredGoal:
    goal_id: str
    owner: str
    title: str
    predicate: str                 # deadline predicate whose obligations this goal tracks
    target: int                    # number of obligations to meet on time in the period
    period_days: int = 30
    unit: str = "obligations"


@dataclass(frozen=True)
class GoalObservation:
    goal_id: str
    value: int | None              # None = unknown
    period_start: date
    period_end: date
    source_fact_ids: tuple[str, ...]
    observed_at: datetime
    freshness_hours: float | None
    confirmed: bool = False


@dataclass(frozen=True)
class GoalAssessment:
    goal_id: str
    status: str                    # on_track | at_risk | unknown
    reason: str


def observe_on_time(
    goal: AuthoredGoal,
    facts: Sequence[FactView],
    resolved: dict[str, date],
    now: datetime,
    *,
    last_verified: datetime | None,
) -> GoalObservation:
    """Count the goal's obligations resolved on or before their deadline in the period.

    ``resolved`` maps fact id → date the obligation was resolved (from resolution
    evidence).  With no verified source at all the value is unknown.
    """
    today = now.date()
    start, end = today - timedelta(days=goal.period_days), today
    relevant: list[tuple[FactView, date]] = []
    for fact in facts:
        if fact.predicate != goal.predicate:
            continue
        raw = fact.value["value"] if isinstance(fact.value, dict) and "value" in fact.value else fact.value
        try:
            deadline = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if start <= deadline <= end:
            relevant.append((fact, deadline))
    freshness = None if last_verified is None else max(0.0, (now - last_verified).total_seconds() / 3600)
    if last_verified is None:
        return GoalObservation(goal.goal_id, None, start, end, (), now, None)
    met = [f for f, d in relevant if f.fact_id in resolved and resolved[f.fact_id] <= d]
    return GoalObservation(goal.goal_id, len(met), start, end, tuple(f.fact_id for f, _ in relevant), now, freshness)


def assess(goal: AuthoredGoal, obs: GoalObservation, *, max_staleness_hours: float = 72.0) -> GoalAssessment:
    if obs.value is None:
        return GoalAssessment(goal.goal_id, "unknown", "no verified observation in the period")
    if obs.freshness_hours is not None and obs.freshness_hours > max_staleness_hours:
        return GoalAssessment(goal.goal_id, "unknown", f"evidence is {int(obs.freshness_hours)}h old")
    if obs.value >= goal.target:
        return GoalAssessment(goal.goal_id, "on_track", f"{obs.value} of {goal.target} {goal.unit} met")
    return GoalAssessment(goal.goal_id, "at_risk", f"{obs.value} of {goal.target} {goal.unit} met")
