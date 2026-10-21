"""Household coordination: shared obligations with one accountable owner (re-artha RA-19, pull only).

Recorded by the household in ``state/household_tasks.md``::

    household_tasks:
      - {id: H1, obligation: School RSVP, subject: kid1, owner: spouse, status: proposed, assigned_on: 2026-10-15,
         escalate_after_days: 3, visible_to: [owner, spouse]}

A task has one identity (``obligation`` plus ``subject``), one accountable owner, an acceptance state
(``proposed`` -> ``accepted`` | ``refused`` -> ``done``) and, to be closed, completion evidence.  Evaluation is
deterministic and read-only and never contacts anyone:

* an unaccepted assignment is shown as *awaiting acceptance* and, after ``escalate_after_days``, as needing escalation;
* a refusal needs a new owner; ``done`` without evidence stays open as unverified;
* two open tasks with the same identity are reported as a duplicate so the household is not reminded twice;
* a task is shown only to the people in ``visible_to`` (default: its owner and the primary user), so a Briefing for
  one person never lists another person's task.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

__all__ = ["TaskState", "coordination_lines", "evaluate_tasks"]

_MAX_TASKS = 100
_DEFAULT_ESCALATION_DAYS = 3


@dataclass(frozen=True)
class TaskState:
    task_id: str
    obligation: str
    owner: str
    state: str          # awaiting | escalate | refused | unverified | duplicate | accepted
    detail: str = ""


def _day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def _visible(record: Mapping[str, Any], viewer: str, primary: str) -> bool:
    listed = record.get("visible_to")
    if isinstance(listed, list) and listed:
        return viewer in {str(v) for v in listed}
    return viewer in {str(record.get("owner", "")), primary}


def evaluate_tasks(records: Sequence[Mapping[str, Any]], today: date, *, viewer: str = "owner",
                   primary: str = "owner") -> list[TaskState]:
    out: list[TaskState] = []
    seen: dict[tuple[str, str], str] = {}
    for record in records[:_MAX_TASKS]:
        tid, obligation = str(record.get("id", "")).strip(), str(record.get("obligation", "")).strip()
        owner = str(record.get("owner", "")).strip()
        status = str(record.get("status", "proposed"))
        if not tid or not obligation or not _visible(record, viewer, primary):
            continue
        if status == "done":
            if not str(record.get("completion_evidence", "")).strip():
                out.append(TaskState(tid, obligation, owner, "unverified", "marked done without completion evidence"))
            continue
        identity = (obligation.casefold(), str(record.get("subject", "")).casefold())
        if identity in seen:
            detail = f"same obligation as {seen[identity]}; remind once"
            out.append(TaskState(tid, obligation, owner, "duplicate", detail))
            continue
        seen[identity] = tid
        if not owner:
            out.append(TaskState(tid, obligation, owner, "awaiting", "has no accountable owner yet"))
        elif status == "refused":
            out.append(TaskState(tid, obligation, owner, "refused", f"{owner} declined; it needs a new owner"))
        elif status == "accepted":
            out.append(TaskState(tid, obligation, owner, "accepted", ""))
        else:
            assigned = _day(record.get("assigned_on"))
            limit = record.get("escalate_after_days")
            limit = limit if isinstance(limit, int) and limit > 0 else _DEFAULT_ESCALATION_DAYS
            waited = (today - assigned).days if assigned else None
            if waited is not None and waited >= limit:
                detail = f"{owner} has not accepted it for {waited} days"
                out.append(TaskState(tid, obligation, owner, "escalate", detail))
            else:
                out.append(TaskState(tid, obligation, owner, "awaiting", f"waiting for {owner} to accept"))
    return out


def coordination_lines(states: Sequence[TaskState]) -> list[str]:
    """Escalations and refusals first; accepted tasks stay quiet (they are in hand)."""
    order = {"escalate": 0, "refused": 1, "awaiting": 2, "unverified": 3, "duplicate": 4}
    shown = sorted((x for x in states if x.state in order), key=lambda x: (order[x.state], x.task_id))
    return [f"{s.task_id}: {s.obligation} \u2014 {s.detail}" for s in shown]
