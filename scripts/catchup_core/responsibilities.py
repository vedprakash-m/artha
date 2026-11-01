"""Standing responsibilities: authored duties evaluated at each Catch-up (re-artha RA-25, pull only).

The user writes them in ``state/responsibilities.md``::

    responsibilities:
      - id: R1
        owner: owner
        outcome: Renew the vehicle registration
        sources: [email:personal]
        allowed_operations: [read, draft, propose]
        cadence_days: 14
        last_checked: 2026-10-01
        completion_evidence: ""
        stop_condition: registration renewed
        status: active

Evaluation is deterministic and read-only.  A responsibility may only ever ``read``, ``draft`` or ``propose``;
any other operation in the record is reported and ignored, never honoured, because a written duty must not become
permission to act.  Nothing runs between sessions: a due check simply appears in the next Catch-up.  Marking a duty
completed needs evidence; without it the duty keeps coming up as unverified.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from catchup_core.curated import CompletionVerifier, completion_key, has_completion_evidence

__all__ = ["ALLOWED_OPERATIONS", "ResponsibilityState", "evaluate_responsibilities", "responsibility_lines"]

ALLOWED_OPERATIONS = frozenset({"read", "draft", "propose"})
_MAX_RECORDS = 50


@dataclass(frozen=True)
class ResponsibilityState:
    responsibility_id: str
    outcome: str
    state: str                       # due | waiting | unverified | invalid | stopped
    detail: str = ""
    allowed: tuple[str, ...] = ()
    next_check: date | None = None


def _day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def evaluate_responsibilities(records: Sequence[Mapping[str, Any]], today: date, *,
                              verify: CompletionVerifier | None = None,
                              check_source: Callable[[str, float], bool] | None = None,
                              check_stop: Callable[[Mapping[str, Any], float], bool | None] | None = None,
                              ) -> list[ResponsibilityState]:
    out: list[ResponsibilityState] = []
    identities = Counter(record['id'].strip() for record in records[:_MAX_RECORDS] if isinstance(record.get('id'), str))
    for record in records[:_MAX_RECORDS]:
        rid = str(record.get("id", "")).strip()
        outcome = str(record.get("outcome", "")).strip()
        operations = record.get("allowed_operations", ["read"])
        if (not rid or not outcome or not isinstance(record.get("id"), str)
                or not isinstance(record.get("outcome"), str) or not isinstance(operations, list)
                or not operations or any(not isinstance(op, str) for op in operations)):
            out.append(ResponsibilityState(rid or "invalid", outcome or "Responsibility", "invalid",
                                           "needs an identity, outcome and list of allowed operations"))
            continue
        asked = operations
        refused = [op for op in asked if op not in ALLOWED_OPERATIONS]
        allowed = tuple(op for op in asked if op in ALLOWED_OPERATIONS)
        status = str(record.get("status", "active"))
        problem = _check_definition(record)
        if identities[rid] > 1:
            problem = 'duplicate responsibility identity; ownership and evidence need review'
        if status not in {"active", "stopped", "completed"} or problem:
            out.append(ResponsibilityState(rid, outcome, "invalid", problem or "has an unknown status", allowed))
            continue
        if status == "stopped":
            continue
        if has_completion_evidence(record.get("completion_evidence"), completion_key("responsibility", record), verify):
            continue
        if status == "completed":
            detail = "marked completed but has no completion evidence, so it stays open"
            out.append(ResponsibilityState(rid, outcome, "unverified", detail, allowed))
            continue
        if refused:
            detail = f"asks for {', '.join(refused)}; a responsibility may only read, draft or propose"
            out.append(ResponsibilityState(rid, outcome, "invalid", detail, allowed))
            continue
        state, detail, due_on = _schedule(record, today)
        if state == 'due':
            checked, check_detail = _checks(record, check_source, check_stop)
            if checked:
                state, detail = checked, check_detail
        out.append(ResponsibilityState(rid, outcome, state, detail, allowed, due_on))
    if len(records) > _MAX_RECORDS:
        out.append(ResponsibilityState("deferred", "Responsibilities", "invalid",
            f"{len(records) - _MAX_RECORDS} records deferred; coverage is incomplete"))
    return out


def _schedule(record: Mapping[str, Any], today: date) -> tuple[str, str, date | None]:
    last, explicit = _day(record.get('last_checked')), _day(record.get('next_check'))
    cadence = record.get('cadence_days')
    if ((record.get('last_checked') is not None and last is None)
            or (last is not None and last > today)
            or (record.get('next_check') is not None and explicit is None)
            or (cadence is not None and (type(cadence) is not int or not 1 <= cadence <= 3660))):
        return 'invalid', 'has an invalid check date or cadence', None
    due = explicit or (last + timedelta(days=cadence) if last and type(cadence) is int else None)
    if last is None and explicit is None:
        return 'due', 'never checked', None
    if due is None:
        return 'invalid', 'needs a cadence or next check date', None
    if due <= today:
        return 'due', f'was due {due:%b} {due.day}', due
    return 'waiting', '', due


def _check_definition(record: Mapping[str, Any]) -> str:
    sources = record.get('sources', [])
    if (not isinstance(sources, list) or len(sources) > 20 or any(
            not isinstance(s, str) or len(s) > 256 or not re.fullmatch(r'[^\s:]+:[^\s]+', s) for s in sources)):
        return 'sources need explicit connector:account scopes'
    hours = record.get('freshness_hours', 72)
    if (isinstance(hours, bool) or not isinstance(hours, int | float)
            or not math.isfinite(hours) or not 0 < hours <= 8760):
        return 'source freshness must be a positive bounded number of hours'
    stop = record.get('stop_condition')
    if stop is not None and not isinstance(stop, str) and (
            not isinstance(stop, dict) or set(stop) != {'fact', 'equals'}
            or not isinstance(stop.get('fact'), str) or not re.fullmatch(r'fact:[^\s@]+@[^\s]+', stop['fact'])):
        return 'a typed stop condition needs an exact fact:identity@version and expected value'
    return ''


def _checks(record: Mapping[str, Any], check_source: Callable[[str, float], bool] | None,
            check_stop: Callable[[Mapping[str, Any], float], bool | None] | None) -> tuple[str, str]:
    hours = float(record.get('freshness_hours', 72))
    missing = [scope for scope in record.get('sources', []) if check_source is None or not check_source(scope, hours)]
    if missing:
        return 'unverified', 'sources not verified: ' + ', '.join(missing)
    stop = record.get('stop_condition')
    if isinstance(stop, dict):
        outcome = check_stop(stop, hours) if check_stop else None
        if outcome is None:
            return 'unverified', 'stop condition evidence is unavailable, stale or changed'
        if outcome:
            return 'stopped', 'stop condition verified; no further check is needed'
    return '', ''


def responsibility_lines(states: Sequence[ResponsibilityState]) -> list[str]:
    """Due first, then problems; waiting duties stay quiet until their date."""
    order = {"due": 0, "invalid": 1, "unverified": 2, "stopped": 3}
    lines: list[str] = []
    for s in sorted((x for x in states if x.state != "waiting"), key=lambda x: (order[x.state], x.responsibility_id)):
        ops = f" (may {', '.join(s.allowed)})" if s.allowed else ""
        if s.state == "due":
            lines.append(f"{s.responsibility_id}: check now \u2014 {s.outcome}; {s.detail}{ops}")
        else:
            lines.append(f"{s.responsibility_id}: {s.outcome} {s.detail}")
    return lines
