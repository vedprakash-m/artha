"""Goal observation and assessment (re-artha RA-11 minimum, §7.5).

Authored Goal fields (owner, target, metric definition) are frozen: an observation
never edits them.  A derived :class:`GoalObservation` records what the evidence
supports and how fresh it is; **missing data is unknown, never fabricated
progress**.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from catchup_core.ledger import FactView, Ledger

METRIC_KINDS = frozenset({"on_time_obligations", "milestones", "outcome", "habit"})

__all__ = ["AuthoredGoal", "GoalAssessment", "GoalObservation", "LeadingIndicators", "SprintCalibration",
           "TradeoffCandidate", "WeeklyMovement", "assess", "calibrate_sprint", "goal_tradeoffs",
           "leading_indicators", "observe_on_time", "tradeoff_lines"]


@dataclass(frozen=True)
class AuthoredGoal:
    goal_id: str
    owner: str
    title: str
    predicate: str                 # deadline predicate whose obligations this goal tracks
    target: int | Decimal          # authored count or exact quantity
    period_days: int = 30
    unit: str = "obligations"


@dataclass(frozen=True)
class WeeklyMovement:
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date
    previous_value: int | Decimal | None
    delta: int | Decimal | None
    prior_fact_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class GoalObservation:
    goal_id: str
    value: int | Decimal | None    # None = unknown
    period_start: date
    period_end: date
    source_fact_ids: tuple[str, ...]
    observed_at: datetime
    freshness_hours: float | None
    confirmed: bool = False
    metric_kind: str = "on_time_obligations"
    unit: str | None = None
    target: int | Decimal | None = None
    baseline: Decimal | None = None
    progress_percent: Decimal | None = None
    weekly: WeeklyMovement | None = None
    indicators: LeadingIndicators | None = None


@dataclass(frozen=True)
class GoalAssessment:
    goal_id: str
    status: str                    # on_track | in_progress | at_risk | unknown
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
    start, end = today - timedelta(days=goal.period_days - 1), today
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


def observe_goals(ledger: Ledger, authored: Sequence[dict[str, Any]], viewer: str, now: datetime,  # noqa: PLR0912
                  *, required_sources: Sequence[str] = (), persist: bool = True,
                  dependencies: dict[str, str] | None = None, weekly: bool = False,
                  open_items: Sequence[Mapping[str, Any]] = (),
                  sprints: Sequence[Mapping[str, Any]] = (),
                  calibrations: dict[str, SprintCalibration] | None = None,
                  observed: dict[str, GoalObservation] | None = None) -> list[str]:
    """Evaluate authored obligation or milestone metrics without changing what a Goal means.

    A weekly view also shows leading indicators, Sprint calibration (only where the Sprint carries an authored
    tolerance) and proposal-only tradeoffs between Goals the user marked as competing.  A caller that evaluates
    Goals one at a time passes shared ``calibrations`` and ``observed`` maps and calls :func:`tradeoff_lines` itself.
    """
    from dataclasses import asdict  # noqa: PLC0415

    output: list[str] = []
    own_maps = calibrations is None or observed is None
    calibrations = {} if calibrations is None else calibrations
    observed = {} if observed is None else observed
    for row in authored:
        metric = row.get("metric")
        if not isinstance(metric, dict) or metric.get("kind") not in METRIC_KINDS:
            continue
        if metric.get('kind') in {'outcome', 'habit'}:
            from catchup_core.goal_metrics import observe_metric  # noqa: PLC0415
            goal, observation, assessment, facts = observe_metric(ledger, row, viewer, now,
                                                                  required_sources, weekly=weekly,
                                                                  open_items=open_items)
        elif metric.get('kind') == 'milestones':
            goal, observation, assessment, facts = _milestone_metric(ledger, row, viewer, now, open_items)
        else:
            goal, observation, assessment, facts = _on_time_metric(ledger, row, viewer, now, required_sources,
                                                                   open_items=open_items)
        if dependencies is not None:
            for fact in facts:
                if fact.fact_id in dependencies and dependencies[fact.fact_id] != fact.recorded_at:
                    raise ValueError('Goal evidence changed during evaluation')
                dependencies[fact.fact_id] = fact.recorded_at
        if persist:
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
                for fact in facts:
                    db.execute("INSERT INTO goal_observation_facts VALUES(?,?)", (goal.goal_id, fact.fact_id))
            ledger.record_capability("RA-11", goal.goal_id + ":" + now.isoformat())
        output.append(f"{goal.title}: {assessment.status.replace('_', ' ')} — {assessment.reason}")
        observed[goal.goal_id] = observation
        if weekly:
            reason = observation.weekly.reason if observation.weekly else 'weekly movement not verified for this metric'
            output.append(f'{goal.title}: {reason}')
            output.extend(_weekly_extras(goal, observation, sprints, now, calibrations, observed))
    if weekly and own_maps:
        output.extend(line for line, _ in tradeoff_lines(authored, calibrations, observed))
    return output


def tradeoff_lines(authored: Sequence[Mapping[str, Any]], calibrations: Mapping[str, SprintCalibration],
                   observed: Mapping[str, GoalObservation]) -> list[tuple[str, tuple[str, ...]]]:
    """Proposal-only tradeoff lines, each with the source Fact ids it rests on."""
    titles = {str(g.get('id')): str(g.get('title', g.get('id'))) for g in authored}
    return [(f'Tradeoff to consider (proposal only, nothing has been changed): {titles[found.behind_goal]} is '
             f'behind its Sprint while {titles[found.ahead_goal]} is ahead; you marked these Goals as competing',
             found.evidence_fact_ids)
            for found in goal_tradeoffs(authored, calibrations, observed)]


def _authored_tolerance(sprint: Mapping[str, Any]) -> Decimal:
    """The Sprint's authored tolerance as a share between 0 and 1; raises ValueError when present but unusable."""
    raw = sprint["tolerance"]
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ValueError("tolerance must be a number")
    try:
        value = Decimal(str(raw))
    except ArithmeticError:
        raise ValueError("tolerance must be a number") from None
    if not value.is_finite() or not 0 <= value <= 1:
        raise ValueError("tolerance must be between 0 and 1")
    return value


def _weekly_extras(goal: AuthoredGoal, observation: GoalObservation, sprints: Sequence[Mapping[str, Any]],
                   now: datetime, calibrations: dict[str, SprintCalibration],
                   observed: dict[str, GoalObservation]) -> list[str]:
    lines: list[str] = []
    ind = observation.indicators
    if ind is not None and ind.known:
        lines.append(f'{goal.title}: leading indicators — {ind.ready} ready, {ind.blocked} blocked, '
                     f'{ind.overdue} overdue, {ind.due_soon} due within 7 days, {ind.unverified} unverified')
    sprint = next((s for s in sprints if s.get('goal_ref') == goal.goal_id and s.get('status') == 'active'), None)
    if sprint is None or 'tolerance' not in sprint:
        return lines            # no authored tolerance: nothing is assumed, so no calibration is shown
    try:
        tolerance = _authored_tolerance(sprint)
    except ValueError:
        return [*lines, f'{goal.title}: Sprint tolerance needs review; calibration not shown']
    measured = observation if observation.target is not None else replace(observation, target=goal.target)
    result = calibrate_sprint(sprint, measured, now.date(), tolerance=tolerance)
    calibrations[goal.goal_id], observed[goal.goal_id] = result, observation
    lines.append(f'{goal.title}: Sprint calibration unknown — {result.reason}' if result.status == 'unknown'
                 else f'{goal.title}: Sprint {result.status} — {result.reason}')
    return lines


def _on_time_metric(ledger: Ledger, row: dict[str, Any], viewer: str, now: datetime,
                    required_sources: Sequence[str], open_items: Sequence[Mapping[str, Any]] = ()
                    ) -> tuple[AuthoredGoal, GoalObservation, GoalAssessment, list[FactView]]:
    metric = row['metric']
    target, period, freshness = metric['target'], metric.get('period_days', 30), metric.get('freshness_hours', 48)
    if (type(target) is not int or type(period) is not int or isinstance(freshness, bool)
            or not isinstance(freshness, int | float) or not math.isfinite(freshness) or freshness <= 0):
        raise ValueError('Goal metric counts, freshness and source scopes need review')
    goal = AuthoredGoal(str(row["id"]), str(row.get("owner", viewer)), str(row.get("title", "")),
                       str(metric["predicate"]), target, period)
    if goal.target < 1 or not 1 <= goal.period_days <= 366:
        raise ValueError("Goal target and period must be positive and bounded")
    facts, verified = _metric_sources(ledger, metric, viewer, required_sources)
    if any(f.status != 'current' or f.provenance_class not in {'verified_source', 'user_confirmed'} for f in facts):
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
    if row.get("milestones"):
        from catchup_core.curated import completion_fact, open_item_links  # noqa: PLC0415
        from catchup_core.plans import evaluate_plans  # noqa: PLC0415

        linked_states, linked_facts = open_item_links(ledger, viewer, [row], open_items)

        def verified_fn(reference: Any, key: str) -> bool:
            fact = completion_fact(ledger, viewer, reference, key) or linked_facts.get(key)
            if fact is not None:
                facts.append(fact)
            return fact is not None

        states = evaluate_plans([row], now.date(), verify=verified_fn, linked_items=linked_states)
        observation = replace(observation, indicators=leading_indicators(goal.goal_id, states, now.date()))
    assessment = assess(goal, observation, max_staleness_hours=float(freshness))
    return goal, observation, assessment, facts


def _metric_sources(ledger: Ledger, metric: Mapping[str, Any], viewer: str,
                    required_sources: Sequence[str]) -> tuple[list[FactView], datetime | None]:
    """One permission/scoping boundary for every source-backed Goal metric."""
    from catchup_core.contracts import FactStatus  # noqa: PLC0415
    sources = metric.get('sources', required_sources)
    if not isinstance(sources, list | tuple) or any(not isinstance(s, str) or not s for s in sources):
        raise ValueError('Goal source scopes need review')
    domain, predicate = str(metric['domain']), str(metric['predicate'])
    facts = ledger.facts_for(domain, predicate, viewer,
                             statuses=(FactStatus.CURRENT, FactStatus.DISPUTED, FactStatus.UNKNOWN))
    if metric.get("subject"):
        facts = [f for f in facts if f.primary_subject_id == metric["subject"]]
    required = set(sources)
    def matches(source: Any) -> bool:
        return source['connector'] in required or f"{source['connector']}:{source['account_scope']}" in required
    scoped = ledger.conn.execute(
        "SELECT e.fact_id,s.connector,s.account_scope FROM fact_evidence e JOIN signals s USING(signal_id) "
        "JOIN facts f USING(fact_id) WHERE f.domain=? AND f.predicate=? UNION "
        "SELECT e.fact_id,s.connector,s.account_scope FROM fact_version_evidence e JOIN signals s USING(signal_id) "
        "JOIN facts f USING(fact_id) WHERE f.domain=? AND f.predicate=? AND f.provenance_class='user_confirmed'",
        (domain, predicate, domain, predicate)).fetchall()
    included = {e['fact_id'] for e in scoped if matches(e)}
    facts = [f for f in facts if f.fact_id in included]
    covered = [c for c in ledger.coverage_rows() if matches(c)]
    observed = {name for c in covered if c['status'] == 'complete'
                for name in (c['connector'], f"{c['connector']}:{c['account_scope']}")}
    complete = required <= observed
    verified = (min(datetime.fromisoformat(c["completed_at"].replace("Z", "+00:00")) for c in covered)
                if covered and complete and all(c["status"] == "complete" and c["completed_at"] for c in covered)
                else None)
    pending = [sid for sid in ledger.unresolved_signals()
               if (source := ledger.signal_row(sid)) is not None and matches(source)]
    return facts, None if pending else verified

def _milestone_metric(ledger: Ledger, row: dict[str, Any], viewer: str, now: datetime,
                      open_items: Sequence[Mapping[str, Any]] = ()
                      ) -> tuple[AuthoredGoal, GoalObservation, GoalAssessment, list[FactView]]:
    from catchup_core.curated import completion_fact, open_item_links  # noqa: PLC0415
    from catchup_core.plans import evaluate_plans  # noqa: PLC0415
    facts: list[FactView] = []
    linked_states, linked_facts = open_item_links(ledger, viewer, [row], open_items)
    def verified(reference: Any, key: str) -> bool:
        fact = completion_fact(ledger, viewer, reference, key) or linked_facts.get(key)
        if fact is not None:
            facts.append(fact)
        return fact is not None
    states = evaluate_plans([row], now.date(), verify=verified, linked_items=linked_states)
    raw = row.get('milestones')
    total = len(raw) if isinstance(raw, list) else 0
    target = row['metric'].get('target', total)
    if type(target) is not int or not 1 <= target <= total:
        raise ValueError('milestone target must be a positive count within the authored plan')
    goal = AuthoredGoal(str(row['id']), str(row.get('owner', viewer)), str(row.get('title', '')),
                        'commitment_completed', target, unit='milestones')
    count = sum(s.state == 'done' for s in states)
    incomplete = any(s.state in {'invalid', 'unverified'} for s in states)
    value = None if incomplete else count
    observation = GoalObservation(goal.goal_id, value, now.date(), now.date(),
        tuple(dict.fromkeys(f.fact_id for f in facts)), now, None, confirmed=not incomplete,
        metric_kind='milestones', unit='milestones', target=target,
        indicators=leading_indicators(goal.goal_id, states, now.date()))
    assessment = GoalAssessment(goal.goal_id, 'unknown' if incomplete else
        'on_track' if count >= target else 'at_risk',
        f'{count} of {target} milestones confirmed'
        + ('; completion evidence or plan needs review' if incomplete else ''))
    return goal, observation, assessment, facts


def validate_sprints(goals: Sequence[Mapping[str, Any]], sprints: Sequence[Mapping[str, Any]]) -> list[str]:
    """Read validation never repairs authored Sprints or chooses one winner."""
    by_goal = {str(g.get('id')): g for g in goals}
    if len(by_goal) != len(goals) or any(not isinstance(g.get('id'), str) or not g['id'].strip() for g in goals):
        raise ValueError('Goal identities are missing or duplicated')
    active: set[str] = set()
    identities: set[str] = set()
    problems = []
    for sprint in sprints:
        sid, ref = sprint.get('id'), str(sprint.get('goal_ref', ''))
        if not isinstance(sid, str) or not sid or sid in identities:
            problems.append('Sprint identity is missing or duplicated')
            continue
        identities.add(sid)
        if sprint.get('status') != 'active':
            continue
        goal = by_goal.get(ref)
        if goal is None or goal.get('status') != 'active':
            problems.append('Active Sprint needs an active Goal')
            continue
        if ref in active:
            problems.append('A Goal has more than one active Sprint; choose one through the canonical writer')
        active.add(ref)
        try:
            start, end = (date.fromisoformat(str(sprint[k])) for k in ('start','end'))
            target = date.fromisoformat(str(goal['target_date'])) if goal.get('target_date') else None
            if end < start or (target is not None and end > target):
                raise ValueError('invalid Sprint interval')
        except (ValueError, KeyError, TypeError):
            problems.append('Sprint dates need review; its interval must fit the Goal target')
    return problems


# ── leading indicators, Sprint calibration and tradeoffs (re-artha RA-11) ──────────────────────────────────────────

@dataclass(frozen=True)
class LeadingIndicators:
    """Counts of plan movement that come before completion; ``None`` means the plan could not be read."""

    goal_id: str
    known: bool
    ready: int | None = None
    blocked: int | None = None
    overdue: int | None = None
    due_soon: int | None = None
    unverified: int | None = None
    reason: str = ""


def leading_indicators(goal_id: str, states: Sequence[Any], today: date, *, soon_days: int = 7) -> LeadingIndicators:
    """Summarise a Goal's evaluated milestones.  An invalid plan or no milestones yields no counts, never zeros."""
    own = [s for s in states if s.goal_id == goal_id]
    if not own:
        return LeadingIndicators(goal_id, False, reason="no milestones are authored for this Goal")
    if any(s.state == "invalid" for s in own):
        return LeadingIndicators(goal_id, False, reason="the milestone plan needs review")
    open_ = [s for s in own if s.state != "done"]
    horizon = today + timedelta(days=soon_days)
    return LeadingIndicators(
        goal_id, True,
        ready=sum(s.state == "ready" for s in open_), blocked=sum(s.state == "blocked" for s in open_),
        overdue=sum(bool(s.overdue) for s in open_),
        due_soon=sum(not s.overdue and s.target_date is not None and today <= s.target_date <= horizon
                     for s in open_),
        unverified=sum(s.state == "unverified" for s in open_))


@dataclass(frozen=True)
class SprintCalibration:
    """Progress compared with elapsed Sprint time.  A comparison of two measured fractions, not a forecast."""

    goal_id: str
    status: str                     # ahead | aligned | behind | unknown
    progress: Decimal | None
    elapsed: Decimal | None
    reason: str


def _unknown(goal_id: str, reason: str) -> SprintCalibration:
    return SprintCalibration(goal_id, "unknown", None, None, reason)


def calibrate_sprint(sprint: Mapping[str, Any], observation: GoalObservation, today: date, *,  # noqa: PLR0911
                     tolerance: Decimal, max_staleness_hours: float = 72.0) -> SprintCalibration:
    """Compare the share of the target reached with the share of the Sprint elapsed (inclusive of both ends).

    Each early return names a distinct reason the answer stays unknown.
    """
    goal_id = observation.goal_id
    try:
        start, end = (date.fromisoformat(str(sprint[k])) for k in ("start", "end"))
    except (KeyError, ValueError):
        return _unknown(goal_id, "Sprint dates need review")
    if sprint.get("status") != "active" or end < start or sprint.get("goal_ref") != goal_id:
        return _unknown(goal_id, "Sprint is not an active Sprint of this Goal")
    if not start <= today <= end:
        return _unknown(goal_id, "today is outside the Sprint")
    if observation.value is None or observation.target is None or not observation.source_fact_ids:
        return _unknown(goal_id, "no verified measurement")
    if observation.freshness_hours is not None and observation.freshness_hours > max_staleness_hours:
        return _unknown(goal_id, f"evidence is {int(observation.freshness_hours)}h old")
    baseline = observation.baseline
    if baseline is None and observation.metric_kind in {"milestones", "on_time_obligations", "habit"}:
        baseline = Decimal(0)
    if baseline is None:
        return _unknown(goal_id, "no baseline for this outcome")
    span = Decimal(observation.target) - baseline
    if span == 0:
        return _unknown(goal_id, "target equals baseline")
    progress = (Decimal(observation.value) - baseline) / span
    elapsed = Decimal((today - start).days + 1) / Decimal((end - start).days + 1)
    gap = progress - elapsed
    status = "ahead" if gap > tolerance else "behind" if gap < -tolerance else "aligned"
    return SprintCalibration(goal_id, status, progress, elapsed,
                             f"{progress:.0%} of the target reached with {elapsed:.0%} of the Sprint elapsed")


@dataclass(frozen=True)
class TradeoffCandidate:
    """A proposal for a Decision: one authored-competing Goal is ahead while the other is behind."""

    behind_goal: str
    ahead_goal: str
    evidence_fact_ids: tuple[str, ...]
    proposal_only: bool = True


def goal_tradeoffs(goals: Sequence[Mapping[str, Any]], calibrations: Mapping[str, SprintCalibration],
                   observations: Mapping[str, GoalObservation]) -> list[TradeoffCandidate]:
    """Pair Goals only where the user authored ``competes_with``, both are active, both are calibrated and both
    cite source Facts.  Nothing is inferred from timing or topic, and nothing here changes a Goal."""
    active = {str(g.get("id")) for g in goals if g.get("status") == "active"}
    found: dict[frozenset[str], TradeoffCandidate] = {}
    for goal in goals:
        a = str(goal.get("id"))
        links = goal.get("competes_with")
        for b in links if isinstance(links, list) else ():
            pair = frozenset((a, str(b)))
            if a not in active or str(b) not in active or len(pair) != 2 or pair in found:
                continue
            statuses = {k: calibrations[k].status for k in pair if k in calibrations}
            facts = {k: observations[k].source_fact_ids for k in pair if k in observations}
            if sorted(statuses.values()) != ["ahead", "behind"] or len(facts) != 2 or not all(facts.values()):
                continue
            behind = next(k for k, v in statuses.items() if v == "behind")
            ahead = next(k for k in pair if k != behind)
            found[pair] = TradeoffCandidate(behind, ahead, tuple(dict.fromkeys(facts[behind] + facts[ahead])))
    return list(found.values())
