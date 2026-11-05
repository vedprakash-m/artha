"""Dated source-backed quantity and habit observations; no inferred units or forecasts."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from typing import Any

from catchup_core.goals import AuthoredGoal, GoalAssessment, GoalObservation, WeeklyMovement, _metric_sources
from catchup_core.ledger import FactView, Ledger, _instant

_NUMBER = r"[+-]?\d{1,20}(?:\.\d{1,10})?"
_UNIT = r"[A-Za-z%][A-Za-z0-9_%/-]{0,15}"
_ENTRY = re.compile(rf"([A-Za-z0-9_-]{{1,64}}) (\d{{4}}-\d{{2}}-\d{{2}}) (done|skipped|{_NUMBER} {_UNIT})")


def _number(raw: Any) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, str | int | float) or not re.fullmatch(_NUMBER, str(raw)):
        raise ValueError("Goal quantity needs a finite bounded decimal")
    return Decimal(str(raw))


def _definition(row: Mapping[str, Any], viewer: str) -> tuple[AuthoredGoal, Decimal | None, float]:
    metric = row["metric"]
    period, freshness = (
        metric.get("period_days", 7 if metric["kind"] == "habit" else 30),
        metric.get("freshness_hours", 72),
    )
    if (
        type(period) is not int
        or not 1 <= period <= 366
        or isinstance(freshness, bool)
        or not isinstance(freshness, int | float)
        or not math.isfinite(freshness)
        or freshness <= 0
    ):
        raise ValueError("Goal window and freshness need review")
    if metric["kind"] == "habit":
        target = metric["target"]
        if type(target) is not int or not 1 <= target <= period or metric.get("unit", "days") != "days":
            raise ValueError("Habit target needs a positive count within its window, measured in days")
        unit, baseline = "days", None
    else:
        unit = metric.get("unit")
        if (
            not isinstance(unit, str)
            or not re.fullmatch(_UNIT, unit)
            or metric.get("comparison") not in {"at_least", "at_most"}
        ):
            raise ValueError("Outcome unit and comparison need review")
        target = _number(metric["target"])
        baseline = _number(metric["baseline"]) if "baseline" in metric else None
        if baseline is not None and not (
            baseline < target if metric["comparison"] == "at_least" else baseline > target
        ):
            raise ValueError("Outcome baseline must define movement toward its target")
    goal = AuthoredGoal(
        str(row["id"]),
        str(row.get("owner", viewer)),
        str(row.get("title", "")),
        str(metric["predicate"]),
        target,
        period,
        unit,
    )
    return goal, baseline, float(freshness)


def validate_definition(row: Mapping[str, Any]) -> None:
    """Validate the whole authored contract before the canonical writer persists it."""
    metric = row["metric"]
    if not isinstance(row.get("owner"), str) or not row["owner"].strip():
        raise ValueError("Source-backed Goal needs an explicit accountable owner")
    if metric.get("kind") not in {"outcome", "habit"}:
        raise ValueError("Unsupported source metric kind")
    for field in ("domain", "predicate", "subject"):
        if not isinstance(metric.get(field), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", metric[field]):
            raise ValueError(f"Goal {field} needs a resolved identifier")
    sources = metric.get("sources")
    if (not isinstance(sources, list) or not 1 <= len(sources) <= 32
            or any(not isinstance(source, str) or not source.strip() or len(source) > 256 for source in sources)):
        raise ValueError("Goal requires explicit source scopes")
    _definition(row, row["owner"])


def _entries(
    facts: Sequence[FactView], goal: AuthoredGoal, start: date, end: date, *, window_end: date | None = None
) -> tuple[dict[date, list[tuple[Any, FactView]]], list[FactView], bool]:
    days: dict[date, list[tuple[Any, FactView]]] = {}
    used: list[FactView] = []
    invalid = False
    for fact in facts:
        raw = (
            fact.value["value"]
            if isinstance(fact.value, dict) and set(fact.value) == {"value", "lineage"}
            else fact.value
        )
        if not isinstance(raw, str) or not raw.startswith(goal.goal_id + " "):
            continue
        match = _ENTRY.fullmatch(raw)
        try:
            if match is None:
                raise ValueError("invalid dated entry")
            day = date.fromisoformat(match[2])
            if day > end:
                raise ValueError("future entry")
            if day < start or day > (window_end or end):
                continue
            value = match[3]
            if value not in {"done", "skipped"}:
                amount, unit = value.split(" ")
                if unit != goal.unit:
                    raise ValueError("unit mismatch")
                value = _number(amount)
            days.setdefault(day, []).append((value, fact))
        except ValueError:
            invalid = True
        used.append(fact)
    return days, used, invalid


def _value(entries: Sequence[tuple[Any, FactView]], now: datetime) -> Any:
    confirmed = [entry for entry in entries if entry[1].provenance_class == "user_confirmed"]
    selected = confirmed or entries
    values = {entry[0] for entry in selected}
    if len(values) != 1 or any(entry[1].status != 'current'
            or _instant(entry[1].observed_at) > now
            or entry[1].provenance_class not in {'verified_source', 'user_confirmed'} for entry in selected):
        return None
    return next(iter(values))


def observe_metric(
    ledger: Ledger, row: Mapping[str, Any], viewer: str, now: datetime, required_sources: Sequence[str],
    *, weekly: bool = False, open_items: Sequence[Mapping[str, Any]] = (),
) -> tuple[AuthoredGoal, GoalObservation, GoalAssessment, list[FactView]]:
    goal, baseline, max_age = _definition(row, viewer)
    metric = row["metric"]
    start, end = now.date() - timedelta(days=goal.period_days - 1), now.date()
    facts, verified = _metric_sources(ledger, metric, viewer, required_sources, now)
    days, used, invalid = _entries(facts, goal, start, end)
    latest = max(days) if days else None
    age = (
        max(
            (now - verified).total_seconds() / 3600,
            (now - datetime.combine(latest, time(), tzinfo=now.tzinfo)).total_seconds() / 3600,
        )
        if verified and latest and verified <= now
        else None
    )
    value: int | Decimal | None = None
    progress = None
    reason, status = "dated observation is missing, stale, conflicting or not verified", "unknown"
    if age is not None and 0 <= age <= max_age and not invalid:
        if metric["kind"] == "habit":
            values = [_value(days.get(start + timedelta(days=i), ()), now) for i in range(goal.period_days)]
            if all(v in {"done", "skipped"} for v in values):
                value = sum(v == "done" for v in values)
                status = "on_track" if value >= goal.target else "at_risk"
                reason = f"{value} of {goal.target} days confirmed; all {goal.period_days} days checked"
        elif latest is not None:
            candidate = _value(days[latest], now)
            if isinstance(candidate, Decimal):
                value = candidate
                met = value >= goal.target if metric["comparison"] == "at_least" else value <= goal.target
                target_date = date.fromisoformat(str(row["target_date"])) if row.get("target_date") else None
                status = "on_track" if met else "at_risk" if target_date and target_date < end else "in_progress"
                reason = (f"{value} {goal.unit} verified for {latest.isoformat()}; "
                          f"target {metric['comparison'].replace('_', ' ')} {goal.target} {goal.unit}")
                if baseline is not None:
                    with localcontext() as context:
                        context.prec = 50
                        progress = (value - baseline) / (goal.target - baseline) * 100
                    reason += f"; {progress:.1f}% from authored baseline"
    indicators = None
    if row.get("milestones"):
        from catchup_core.curated import completion_fact, open_item_links  # noqa: PLC0415
        from catchup_core.goals import leading_indicators  # noqa: PLC0415
        from catchup_core.plans import evaluate_plans  # noqa: PLC0415

        linked_states, linked_facts = open_item_links(ledger, viewer, [row], open_items)

        def verified_fn(reference: Any, key: str) -> bool:
            fact = completion_fact(ledger, viewer, reference, key) or linked_facts.get(key)
            if fact is not None:
                used.append(fact)
            return fact is not None

        states = evaluate_plans([row], now.date(), verify=verified_fn, linked_items=linked_states)
        indicators = leading_indicators(goal.goal_id, states, now.date())
    observation = GoalObservation(
        goal.goal_id,
        value,
        start,
        end,
        tuple(f.fact_id for f in used),
        now,
        age,
        confirmed=value is not None,
        metric_kind=metric["kind"],
        unit=goal.unit,
        target=goal.target,
        baseline=baseline,
        progress_percent=progress,
        indicators=indicators,
    )
    if weekly:
        movement, prior = _weekly_movement(goal, metric['kind'], observation, facts, latest)
        observation = replace(observation, weekly=movement)
        used = list({fact.fact_id: fact for fact in [*used, *prior]}.values())
    return goal, observation, GoalAssessment(goal.goal_id, status, reason), used


def _weekly_movement(goal: AuthoredGoal, kind: str, current: GoalObservation,
                     facts: Sequence[FactView], latest: date | None) -> tuple[WeeklyMovement, list[FactView]]:
    start, end = current.period_start, current.period_end
    if kind == 'outcome' and latest is not None:
        start = end = latest
    prior_start, prior_end = start - timedelta(days=7), end - timedelta(days=7)
    previous: int | Decimal | None = None
    delta: int | Decimal | None = None
    used: list[FactView] = []
    reason = 'weekly movement not verified: current observation is not verified'
    if current.value is not None:
        days, used, invalid = _entries(facts, goal, prior_start, current.period_end, window_end=prior_end)
        if not invalid:
            if kind == 'outcome':
                candidate = _value(days.get(prior_end, ()), current.observed_at)
                if isinstance(candidate, Decimal):
                    previous = candidate
            else:
                values = [_value(days.get(prior_start + timedelta(days=i), ()), current.observed_at)
                          for i in range(goal.period_days)]
                if all(value in {'done', 'skipped'} for value in values):
                    previous = sum(value == 'done' for value in values)
        reason = 'weekly movement not verified: prior window is missing, conflicting or unverified'
        if previous is not None:
            with localcontext() as context:
                context.prec = 50
                delta = current.value - previous
            signed = f'{delta:+f}' if isinstance(delta, Decimal) else f'{delta:+d}'
            reason = (f'weekly change {signed} {goal.unit}; '
                      f'{prior_start} to {prior_end} versus {start} to {end}')
    return WeeklyMovement(start, end, prior_start, prior_end, previous, delta,
                          tuple(fact.fact_id for fact in used), reason), used
