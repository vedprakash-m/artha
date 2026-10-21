"""Goal plans: authored milestones evaluated at each Catch-up (re-artha RA-25, pull only).

A Goal may carry milestones the user wrote in ``state/goals.md``::

    milestones:
      - {id: m1, title: Gather documents, status: done, evidence: "receipt 2026-10-02"}
      - {id: m2, title: File the form, prerequisites: [m1], target_date: 2026-11-15}

Evaluation is deterministic and read-only: nothing advances between sessions, a plan never grants permission to act
or changes what the user values, and **completion needs evidence** (a milestone marked done without evidence is
reported as unverified and still blocks what depends on it).  ``ready`` items name the next step so the Catch-up can
prepare a proposal or instruction sheet for review.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

__all__ = ["MilestoneState", "evaluate_plans", "plan_lines"]

_MAX_MILESTONES = 50


@dataclass(frozen=True)
class MilestoneState:
    goal_id: str
    milestone_id: str
    title: str
    state: str                    # done | unverified | ready | blocked
    waiting_for: tuple[str, ...] = ()
    target_date: date | None = None
    overdue: bool = False


def _day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def evaluate_plans(goals: Sequence[Mapping[str, Any]], today: date) -> list[MilestoneState]:
    """Evaluate every active Goal's milestones; unreadable structure yields no claims rather than guesses."""
    out: list[MilestoneState] = []
    for goal in goals:
        raw = goal.get("milestones")
        if not isinstance(raw, list):
            continue
        items = [m for m in raw[:_MAX_MILESTONES] if isinstance(m, dict) and m.get("id")]
        titles = {str(m["id"]): str(m.get("title", m["id"])) for m in items}
        verified_done = {str(m["id"]) for m in items
                         if str(m.get("status", "todo")) == "done" and str(m.get("evidence", "")).strip()}
        for m in items:
            mid, goal_id = str(m["id"]), str(goal.get("id", "Goal"))
            target = _day(m.get("target_date"))
            overdue = target is not None and target < today
            if mid in verified_done:
                out.append(MilestoneState(goal_id, mid, titles[mid], "done", (), target, False))
                continue
            if str(m.get("status", "todo")) == "done":
                out.append(MilestoneState(goal_id, mid, titles[mid], "unverified", (), target, overdue))
                continue
            prereqs = [str(p) for p in (m.get("prerequisites") or [])]
            waiting = tuple(titles.get(p, p) for p in prereqs if p not in verified_done)
            out.append(MilestoneState(goal_id, mid, titles[mid], "blocked" if waiting else "ready", waiting,
                                      target, overdue))
    return out


def plan_lines(states: Sequence[MilestoneState]) -> list[str]:
    """One short line per actionable milestone: ready first, then blocked, then unverified; done stays quiet."""
    order = {"ready": 0, "blocked": 1, "unverified": 2}
    lines: list[str] = []
    for s in sorted((x for x in states if x.state != "done"), key=lambda x: (order[x.state], x.target_date or date.max,
                                                                         x.goal_id, x.milestone_id)):
        due = ""
        if s.target_date:
            due = f" (target {s.target_date:%b} {s.target_date.day}{', overdue' if s.overdue else ''})"
        if s.state == "ready":
            lines.append(f"{s.goal_id}: ready \u2014 next step: {s.title}{due}")
        elif s.state == "blocked":
            lines.append(f"{s.goal_id}: {s.title} waits for {', '.join(s.waiting_for)}{due}")
        else:
            lines.append(f"{s.goal_id}: {s.title} is marked done but has no evidence, so it is not counted{due}")
    return lines
