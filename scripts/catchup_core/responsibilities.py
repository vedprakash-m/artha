"""Standing responsibilities: authored duties evaluated at each Catch-up (re-artha RA-25, pull only).

The user writes them in ``state/responsibilities.md``::

    responsibilities:
      - id: R1
        owner: owner
        outcome: Renew the vehicle registration
        sources: [email]
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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

__all__ = ["ALLOWED_OPERATIONS", "ResponsibilityState", "evaluate_responsibilities", "responsibility_lines"]

ALLOWED_OPERATIONS = frozenset({"read", "draft", "propose"})
_MAX_RECORDS = 50


@dataclass(frozen=True)
class ResponsibilityState:
    responsibility_id: str
    outcome: str
    state: str                       # due | waiting | unverified | invalid
    detail: str = ""
    allowed: tuple[str, ...] = ()
    next_check: date | None = None


def _day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def evaluate_responsibilities(records: Sequence[Mapping[str, Any]], today: date) -> list[ResponsibilityState]:
    out: list[ResponsibilityState] = []
    for record in records[:_MAX_RECORDS]:
        rid = str(record.get("id", "")).strip()
        outcome = str(record.get("outcome", "")).strip()
        if not rid or not outcome:
            continue
        asked = [str(op) for op in (record.get("allowed_operations") or ["read"])]
        refused = [op for op in asked if op not in ALLOWED_OPERATIONS]
        allowed = tuple(op for op in asked if op in ALLOWED_OPERATIONS)
        status = str(record.get("status", "active"))
        if status == "stopped":
            continue
        if status == "completed":
            if str(record.get("completion_evidence", "")).strip():
                continue
            detail = "marked completed but has no completion evidence, so it stays open"
            out.append(ResponsibilityState(rid, outcome, "unverified", detail, allowed))
            continue
        if refused:
            detail = f"asks for {', '.join(refused)}; a responsibility may only read, draft or propose"
            out.append(ResponsibilityState(rid, outcome, "invalid", detail, allowed))
        last, explicit = _day(record.get("last_checked")), _day(record.get("next_check"))
        cadence = record.get("cadence_days")
        due_on = explicit or (last + timedelta(days=int(cadence)) if last and isinstance(cadence, int) and cadence > 0
                              else None)
        if last is None and explicit is None:
            out.append(ResponsibilityState(rid, outcome, "due", "never checked", allowed))
        elif due_on is not None and due_on <= today:
            out.append(ResponsibilityState(rid, outcome, "due", f"was due {due_on:%b} {due_on.day}", allowed, due_on))
        else:
            out.append(ResponsibilityState(rid, outcome, "waiting", "", allowed, due_on))
    return out


def responsibility_lines(states: Sequence[ResponsibilityState]) -> list[str]:
    """Due first, then problems; waiting duties stay quiet until their date."""
    order = {"due": 0, "invalid": 1, "unverified": 2}
    lines: list[str] = []
    for s in sorted((x for x in states if x.state != "waiting"), key=lambda x: (order[x.state], x.responsibility_id)):
        ops = f" (may {', '.join(s.allowed)})" if s.allowed else ""
        if s.state == "due":
            lines.append(f"{s.responsibility_id}: check now \u2014 {s.outcome}; {s.detail}{ops}")
        else:
            lines.append(f"{s.responsibility_id}: {s.outcome} {s.detail}")
    return lines
