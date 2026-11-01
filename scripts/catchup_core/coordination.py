"""Household coordination: shared obligations with one accountable owner (re-artha RA-19, pull only).

Recorded by the household in ``state/household_tasks.md``::

    household_tasks:
      - {id: H1, obligation: School RSVP, subject: kid1, owner: spouse, status: proposed, assigned_on: 2026-10-15,
         escalate_after_days: 3, visible_to: [owner, spouse]}

A task has one identity (``obligation`` plus ``subject``), one accountable owner and, to be closed, completion
evidence. The owner's 2026-10-22 decision treats assignments as accepted by household convention, without a separate
member enrollment or acceptance workflow. Evaluation is deterministic, read-only and never contacts anyone:

* assigned work is accepted by convention; old assignment dates never imply a missing member response;
* a recorded refusal needs review/new ownership; ``done`` without evidence stays open as unverified;
* two open tasks with the same identity are reported as a duplicate so the household is not reminded twice;
* a task is shown only to the people in ``visible_to`` (default: its owner and the primary user), so a Briefing for
  one person never lists another person's task.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from catchup_core.curated import CompletionVerifier, completion_key, has_completion_evidence

__all__ = ["TaskState", "coordination_lines", "evaluate_tasks"]

_MAX_TASKS = 100


@dataclass(frozen=True)
class TaskState:
    task_id: str
    obligation: str
    owner: str
    state: str          # awaiting | refused | unverified | duplicate | accepted
    detail: str = ""


def _visible(record: Mapping[str, Any], viewer: str, primary: str) -> bool:
    if 'visible_to' in record:
        listed = record['visible_to']
        if not isinstance(listed, list) or any(not isinstance(v, str) or not v.strip() for v in listed):
            raise ValueError('household task visibility needs review')
        return viewer in listed
    return viewer in {str(record.get("owner", "")), primary}


def evaluate_tasks(records: Sequence[Mapping[str, Any]], today: date, *, viewer: str = "owner",
                   primary: str = "owner", verify: CompletionVerifier | None = None) -> list[TaskState]:
    out: list[TaskState] = []
    seen: dict[tuple[str, str], str] = {}
    visible = [record for record in records if _visible(record, viewer, primary)]
    for record in visible[:_MAX_TASKS]:
        tid, obligation = str(record.get("id", "")).strip(), str(record.get("obligation", "")).strip()
        owner = str(record.get("owner", "")).strip()
        status = str(record.get("status", "proposed"))
        if not tid or not obligation:
            continue
        if has_completion_evidence(record.get("completion_evidence"), completion_key("household", record), verify):
            continue
        if status == "done":
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
            out.append(TaskState(tid, obligation, owner, "refused", f"reported refusal for {owner}; review its owner"))
        elif status in {"proposed", "accepted"}:
            out.append(TaskState(tid, obligation, owner, "accepted", "accepted by household convention"))
        else:
            out.append(TaskState(tid, obligation, owner, "unverified", "assignment status needs review"))
    if len(visible) > _MAX_TASKS:
        out.append(TaskState("deferred", "Household tasks", "", "unverified",
            "some tasks were deferred; coverage is incomplete"))
    return out


def coordination_lines(states: Sequence[TaskState]) -> list[str]:
    """Reported refusals and missing owners first; assigned work needs no acceptance reminder."""
    order = {"refused": 0, "awaiting": 1, "unverified": 2, "duplicate": 3}
    shown = sorted((x for x in states if x.state in order), key=lambda x: (order[x.state], x.task_id))
    return [f"{s.task_id}: {s.obligation} \u2014 {s.detail}" for s in shown]
