"""Triage: deadline horizon, deterministic Alerts, ranking and cross-Domain cases
(re-artha §6.2, §6.9, RA-03, RA-13, RA-17).

No model is involved.  Inputs are the ledger's current Facts, the Domain pack's
rules and deadline arithmetic, per-source freshness and the user's calendar.  The
output states **what is known and how fresh it is**: a stale or disputed input
yields "unknown"/"disputed", never reassurance.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from catchup_core.contracts import FactStatus, Severity
from catchup_core.domain_pack import DomainPack, Predicate
from catchup_core.ledger import FactView, Ledger

__all__ = [
    "AlertDraft", "CalendarEvent", "CrossDomainFinding", "TriageResult", "next_check_by", "triage",
]

NEXT_CHECK_MARGIN_DAYS = 2        # re-artha §12.3 default
HORIZON_DAYS = 14                 # re-artha §12.3 default


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    start: datetime | date
    end: datetime | date
    all_day: bool = False
    kind: str = "other"           # school | travel | out_of_office | other
    signal_id: str = ""


@dataclass(frozen=True)
class AlertDraft:
    obligation_key: str
    domain: str
    subject_id: str
    predicate: str
    fact_id: str
    severity: Severity | None
    deadline: date | None
    latest_useful_action: date | None
    days_until: int | None
    state: str                    # actionable | disputed | unknown_stale | legacy | no_deadline
    uncertainty: str = ""
    high_stakes: bool = False
    evidence_connectors: tuple[str, ...] = ()
    starts_at: datetime | None = None


@dataclass(frozen=True)
class CrossDomainFinding:
    case: str
    obligation_key: str
    detail: str
    suggested_date: date | None = None
    signal_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TriageResult:
    alerts: tuple[AlertDraft, ...]
    horizon: tuple[AlertDraft, ...]
    cross_domain: tuple[CrossDomainFinding, ...]
    next_check_by: date | None
    check_now: bool
    uncertain: tuple[str, ...] = field(default_factory=tuple)
    supporting: tuple[AlertDraft, ...] = ()


def _deadline_of(fact: FactView) -> date | None:
    value: Any = fact.value
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def next_check_by(horizon: Sequence[AlertDraft], today: date) -> date | None:
    """Nearest latest-useful-action date minus the margin (re-artha §12.3)."""
    candidates = [a.latest_useful_action for a in horizon if a.latest_useful_action is not None]
    if not candidates:
        return None
    return min(candidates) - timedelta(days=NEXT_CHECK_MARGIN_DAYS)


def _blocked_day(pack: DomainPack, day: date, calendar: Sequence[CalendarEvent]) -> str | None:
    if day.weekday() >= 5:
        return "a weekend"
    if day in pack.holidays:
        return "a school holiday"
    for ev in calendar:
        if _calendar_blocks(pack, ev, day):
            return f"a {ev.kind.replace('_', ' ')} day"
    return None


def _calendar_blocks(pack: DomainPack, event: CalendarEvent, day: date) -> bool:
    if not event.all_day or event.kind not in ("travel", "out_of_office"):
        return False
    start = event.start.date() if isinstance(event.start, datetime) else event.start
    end = event.end.date() if isinstance(event.end, datetime) else event.end
    return start <= day <= pack.last_inclusive_day(end)


def _previous_open_day(pack: DomainPack, day: date, calendar: Sequence[CalendarEvent]) -> date:
    probe = day - timedelta(days=1)
    for _ in range(30):
        if _blocked_day(pack, probe, calendar) is None:
            return probe
        probe -= timedelta(days=1)
    return day


def _cross_domain(pack: DomainPack, horizon: Sequence[AlertDraft], calendar: Sequence[CalendarEvent],
                  appointments: Sequence[AlertDraft]) -> list[CrossDomainFinding]:
    findings: list[CrossDomainFinding] = []
    cases = set(pack.cross_domain_cases)
    if "deadline_vs_calendar_conflict" in cases:
        for item in horizon:
            if item.latest_useful_action is None:
                continue
            why = _blocked_day(pack, item.latest_useful_action, calendar)
            if why:
                findings.append(CrossDomainFinding(
                    "deadline_vs_calendar_conflict", item.obligation_key,
                    f"latest useful action ({item.latest_useful_action.isoformat()}) falls on {why}",
                    _previous_open_day(pack, item.latest_useful_action, calendar),
                    signal_ids=tuple(ev.signal_id for ev in calendar if ev.signal_id
                                     and _calendar_blocks(pack, ev, item.latest_useful_action))))
    if "appointment_vs_school_schedule" in cases:
        for appt in appointments:
            if appt.deadline is None:
                continue
            for ev in calendar:
                if ev.kind != "school" or ev.all_day:
                    continue
                ev_start = ev.start if isinstance(ev.start, datetime) else None
                ev_end = ev.end if isinstance(ev.end, datetime) else None
                if (ev_start is not None and ev_end is not None and appt.starts_at is not None
                        and ev_start <= appt.starts_at < ev_end):
                    findings.append(CrossDomainFinding(
                        "appointment_vs_school_schedule", appt.obligation_key,
                        f"appointment on {appt.deadline.isoformat()} overlaps a school-day event",
                        signal_ids=(ev.signal_id,) if ev.signal_id else ()))
                    break
    return findings


def _in_horizon(a: AlertDraft, states: Mapping[str, str], horizon_days: int) -> bool:
    if a.days_until is None:
        return False
    if 0 <= a.days_until <= horizon_days:
        return True
    return a.days_until < 0 and states.get(a.obligation_key, "outstanding") == "outstanding"


def _evaluate_date_fact(
    pack: DomainPack, pred: Predicate, fact: FactView, key: str, deadline: date, connectors: tuple[str, ...],
    now: datetime, freshness: Mapping[str, datetime], limits: Mapping[str, int], states: Mapping[str, str],
    source_class_of: Any,
) -> AlertDraft:
    days = pack.days_until(datetime.combine(deadline, datetime.min.time(), tzinfo=pack.timezone), now)
    lua = pack.latest_useful_action(pred.name, deadline)
    stale_reason = ""
    for conn in connectors:
        seen = freshness.get(conn)
        limit = limits.get(conn)
        if limit is not None and (seen is None or now - seen > timedelta(hours=limit)):
            age = "never" if seen is None else f"{int((now - seen).total_seconds() // 3600)}h ago"
            stale_reason = f"{conn} last verified {age} (limit {limit}h)"
            break
    source_class = source_class_of(fact.evidence[0][0]) if (source_class_of and fact.evidence) else None
    result = pack.evaluate(pred.name, {
        "days_until": days, "value": fact.value, "obligation_state": states.get(key, "outstanding"),
        "fact_status": fact.status,
        "source_class": source_class, "now": now})
    legacy = fact.provenance_class == "legacy_unverified"
    state = "legacy" if legacy else ("unknown_stale" if stale_reason else "actionable")
    note = stale_reason or ("from your records; not re-verified from a source" if legacy else "")
    if result.uncertain:
        note = (note + "; " if note else "") + "a rule input is unknown"
    return AlertDraft(key, fact.domain, fact.primary_subject_id, pred.name, fact.fact_id, result.severity,
                      deadline, lua, days, state, note, pred.high_stakes, connectors)


def _suppressed(ledger: Ledger, fact: FactView, key: str,
                states: Mapping[str, str], now: datetime) -> bool:
    if states.get(key) == "resolved":
        return True
    saved = ledger.get_alert(key)
    if not saved or saved["deadline"] != str(fact.value):
        return False
    if saved["status"] == "resolved":
        return True
    return bool(saved["status"] == "snoozed" and saved["suppressed_until"]
                and datetime.fromisoformat(saved["suppressed_until"]) > now)


def triage(
    ledger: Ledger,
    pack: DomainPack,
    now: datetime,
    *,
    viewer: str,
    freshness: Mapping[str, datetime] | None = None,
    calendar: Sequence[CalendarEvent] = (),
    obligation_states: Mapping[str, str] | None = None,
    source_class_of: Any = None,
    connector_of: Any = None,
    goal_relevance: Mapping[str, float] | None = None,
    horizon_days: int = HORIZON_DAYS,
) -> TriageResult:
    """Evaluate current Facts against the pack and rank what needs attention."""
    freshness = freshness or {}
    states = obligation_states or {}
    relevance = goal_relevance or {}
    limits = {s.connector: s.freshness_hours for s in pack.sources}
    today = now.astimezone(pack.timezone).date()

    drafts: list[AlertDraft] = []
    appointment_drafts: list[AlertDraft] = []
    uncertain: list[str] = []

    for predicate, pred in pack.predicates.items():
        statuses = (FactStatus.CURRENT, FactStatus.DISPUTED, FactStatus.UNKNOWN)
        domain = pack.predicate_domains.get(predicate, pack.domain)
        for fact in ledger.facts_for(domain, predicate, viewer, statuses=statuses):
            key = f"{domain}:{fact.primary_subject_id}:{predicate}"
            if fact.obligation_key:
                key += f":{fact.obligation_key}"
            if _suppressed(ledger, fact, key, states, now):
                continue
            connectors = tuple(sorted({connector_of(sig) for sig, _s, _e in fact.evidence})) if connector_of else ()

            if fact.status == FactStatus.UNKNOWN.value:
                drafts.append(AlertDraft(key, domain, fact.primary_subject_id, predicate, fact.fact_id,
                    None, None, None, None, "unknown_stale", "source authority is unverified",
                    pred.high_stakes, connectors))
                uncertain.append(key)
                continue

            if pred.type == "datetime":
                try:
                    stamp = datetime.fromisoformat(str(fact.value)[:25])
                except ValueError:
                    continue
                appointment_drafts.append(AlertDraft(
                    key, domain, fact.primary_subject_id, predicate, fact.fact_id, None,
                    stamp.date(), None, None, "actionable", high_stakes=pred.high_stakes,
                    evidence_connectors=connectors, starts_at=stamp))
                continue
            if pred.type != "date":
                evaluated = pack.evaluate(predicate, {"value": fact.value, "fact_status": fact.status,
                    "obligation_state": states.get(key, "outstanding"), "now": now})
                if evaluated.severity is not None or fact.status == FactStatus.DISPUTED.value:
                    drafts.append(AlertDraft(key, domain, fact.primary_subject_id, predicate, fact.fact_id,
                        evaluated.severity, None, None, None,
                        "disputed" if fact.status == FactStatus.DISPUTED.value else "actionable",
                        str(fact.value), pred.high_stakes, connectors))
                continue

            deadline = _deadline_of(fact)
            if fact.status == FactStatus.DISPUTED.value:
                drafts.append(AlertDraft(
                    key, domain, fact.primary_subject_id, predicate, fact.fact_id, None, deadline, None, None,
                    "disputed", "sources disagree — confirm the date before acting", pred.high_stakes, connectors))
                uncertain.append(key)
                continue
            if deadline is None:
                drafts.append(AlertDraft(
                    key, domain, fact.primary_subject_id, predicate, fact.fact_id, None, None, None, None,
                    "no_deadline", "no usable date", pred.high_stakes, connectors))
                continue

            drafts.append(_evaluate_date_fact(
                pack, pred, fact, key, deadline, connectors, now, freshness, limits, states, source_class_of))
            if drafts[-1].uncertainty.endswith("a rule input is unknown"):
                uncertain.append(key)

    def sort_key(a: AlertDraft) -> tuple[Any, ...]:
        return (a.severity.rank if a.severity else 9, a.latest_useful_action or a.deadline or date.max,
                0 if a.state == "actionable" else 1, -relevance.get(a.obligation_key, 0.0), a.obligation_key)

    surfaced_states = ("disputed", "unknown_stale", "no_deadline")
    alerts = tuple(sorted((a for a in drafts if a.severity is not None or a.state in surfaced_states),
                          key=sort_key))
    horizon = tuple(sorted(
        (a for a in drafts if a.days_until is not None and _in_horizon(a, states, horizon_days)),
        key=lambda a: (a.latest_useful_action or date.max, a.obligation_key)))
    findings = tuple(_cross_domain(pack, horizon, calendar, appointment_drafts))
    nxt = next_check_by(horizon, today)
    return TriageResult(alerts, horizon, findings, nxt, check_now=nxt is not None and nxt <= today,
                        uncertain=tuple(sorted(set(uncertain))), supporting=tuple(appointment_drafts))
