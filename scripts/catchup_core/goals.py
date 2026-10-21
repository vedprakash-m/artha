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
from typing import Any

from catchup_core.ledger import FactView, Ledger

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


def observe_goals(ledger: Ledger, authored: Sequence[dict[str, Any]], viewer: str, now: datetime,
                  *, required_sources: Sequence[str] = ()) -> list[str]:
    """Evaluate explicitly authored on-time metrics; no automatic invention of what a Goal means."""
    from dataclasses import asdict  # noqa: PLC0415

    from catchup_core.contracts import FactStatus  # noqa: PLC0415
    output = []
    for row in authored:
        metric = row.get("metric")
        if not isinstance(metric, dict) or metric.get("kind") != "on_time_obligations":
            continue
        goal = AuthoredGoal(str(row["id"]), str(row.get("owner", viewer)), str(row.get("title", "")),
                           str(metric["predicate"]), int(metric["target"]), int(metric.get("period_days", 30)))
        if goal.target < 1 or not 1 <= goal.period_days <= 366:
            raise ValueError("Goal target and period must be positive and bounded")
        domain = str(metric["domain"])
        facts = ledger.facts_for(domain, goal.predicate, viewer,
                                 statuses=(FactStatus.CURRENT, FactStatus.DISPUTED, FactStatus.UNKNOWN))
        if metric.get("subject"):
            facts = [f for f in facts if f.primary_subject_id == metric["subject"]]
        covered = ledger.coverage_rows()
        required = set(metric.get("sources", required_sources))
        complete = required <= {c["connector"] for c in covered if c["status"] == "complete"}
        verified = (min(datetime.fromisoformat(c["completed_at"].replace("Z", "+00:00")) for c in covered)
                    if covered and complete and all(c["status"] == "complete" and c["completed_at"] for c in covered)
                    else None)
        if any(f.status != "current" for f in facts) or ledger.unresolved_signals():
            verified = None
        if verified and facts:
            verified = min(verified, *(datetime.fromisoformat(f.observed_at.replace("Z", "+00:00")) for f in facts))
        resolutions = {}
        for fact in facts:
            resolved = ledger.conn.execute("SELECT oi.updated_at FROM open_items oi JOIN alert_facts af "
                "ON af.alert_id=oi.source_alert_id WHERE af.fact_id=? AND oi.state='done' "
                "AND oi.resolution_evidence IS NOT NULL ORDER BY oi.updated_at DESC LIMIT 1",
                (fact.fact_id,)).fetchone()
            if resolved:
                resolutions[fact.fact_id] = date.fromisoformat(resolved[0][:10])
        observation = observe_on_time(goal, facts, resolutions, now, last_verified=verified)
        assessment = assess(goal, observation, max_staleness_hours=float(metric.get("freshness_hours", 48)))
        if ledger._cipher is None:
            raise ValueError("Goal observations require encryption")
        import json  # noqa: PLC0415
        payload = {"observation": asdict(observation), "assessment": asdict(assessment),
                   "fact_versions": {f.fact_id: f.recorded_at for f in facts}}
        encrypted, version = ledger._cipher.encrypt_json(json.loads(json.dumps(payload, default=str)),
                                                          aad=f"goal:{goal.goal_id}".encode())
        with ledger.transaction() as db:
            db.execute("INSERT OR REPLACE INTO goal_observations VALUES(?,?,?,?)",
                       (goal.goal_id, encrypted, version, now.isoformat()))
        ledger.record_capability("RA-11", goal.goal_id + ":" + now.isoformat())
        output.append(f"{goal.title}: {assessment.status.replace('_', ' ')} — {assessment.reason}")
    return output
