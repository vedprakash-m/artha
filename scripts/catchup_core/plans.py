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

from catchup_core.curated import CompletionVerifier, completion_key, has_completion_evidence, milestone_record

__all__ = ["MilestoneState", "evaluate_plans", "plan_lines"]

_MAX_MILESTONES = 50


@dataclass(frozen=True)
class MilestoneState:
    goal_id: str
    milestone_id: str
    title: str
    state: str                    # done | unverified | ready | blocked | invalid
    waiting_for: tuple[str, ...] = ()
    target_date: date | None = None
    overdue: bool = False
    owner: str = "unassigned"
    instructions: tuple[str, ...] = ()
    review_reason: str = ''


def _day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def evaluate_plans(goals: Sequence[Mapping[str, Any]], today: date, *,
                   verify: CompletionVerifier | None = None,
                   linked_items: Mapping[str, bool | None] | None = None) -> list[MilestoneState]:
    """Evaluate every active Goal's milestones; unreadable structure yields no claims rather than guesses."""
    out: list[MilestoneState] = []
    for goal in goals:
        raw = goal.get("milestones")
        if raw is None:
            continue
        if not isinstance(raw, list):
            out.append(MilestoneState(str(goal.get("id", "Goal")), "invalid", "invalid milestone list", "invalid"))
            continue
        items = [m for m in raw[:_MAX_MILESTONES] if isinstance(m, dict) and m.get("id")]
        problem = (_graph_problem(items) if len(items) == len(raw[:_MAX_MILESTONES])
                   else "milestone record missing a valid identity")
        if problem:
            out.append(MilestoneState(str(goal.get("id", "Goal")), "invalid", problem, "invalid"))
            continue
        if len(raw) > _MAX_MILESTONES:
            out.append(MilestoneState(str(goal.get("id", "Goal")), "deferred",
                f"{len(raw) - _MAX_MILESTONES} milestones deferred; review the complete plan", "invalid"))
        titles = {str(m["id"]): str(m.get("title", m["id"])) for m in items}
        verified_done = {str(m["id"]) for m in items
                         if (m.get("target_date") is None or _day(m["target_date"]) is not None)
                         and has_completion_evidence(m.get("evidence"),
                             completion_key("milestone", milestone_record(goal, m)), verify)}
        for m in items:
            mid, goal_id = str(m["id"]), str(goal.get("id", "Goal"))
            target = _day(m.get("target_date"))
            if m.get("target_date") is not None and target is None:
                out.append(MilestoneState(goal_id, mid, f"{titles[mid]} has an invalid target date", "invalid"))
                continue
            overdue = target is not None and target < today
            if mid in verified_done:
                out.append(MilestoneState(goal_id, mid, titles[mid], "done", (), target, False))
                continue
            key = completion_key('milestone', milestone_record(goal, m))
            if 'open_item_ref' in m and (linked_items or {}).get(key) is None:
                out.append(MilestoneState(goal_id, mid, titles[mid], 'unverified', (), target, overdue,
                                         review_reason='linked Open Item completion needs review'))
                continue
            if str(m.get("status", "todo")) == "done":
                out.append(MilestoneState(goal_id, mid, titles[mid], "unverified", (), target, overdue))
                continue
            prereqs = [str(p) for p in (m.get("prerequisites") or [])]
            waiting = tuple(titles.get(p, p) for p in prereqs if p not in verified_done)
            out.append(MilestoneState(goal_id, mid, titles[mid], "blocked" if waiting else "ready", waiting,
                                      target, overdue, str(goal.get('owner') or 'unassigned'),
                                      tuple(m.get('instructions') or [titles[mid]]) if not waiting else ()))
    return out


def _graph_problem(items: Sequence[Mapping[str, Any]]) -> str:
    links = [m['open_item_ref'] for m in items if 'open_item_ref' in m]
    if (any(not isinstance(m['id'], str) or not m['id'].strip() for m in items)
            or any(not isinstance(link, str) or not link.strip() or len(link) > 128 for link in links)):
        return 'milestone IDs and Open Item references need valid nonempty strings'
    identities = [str(m["id"]) for m in items]
    if len(set(identities)) != len(identities) or len(set(links)) != len(links):
        return 'duplicate milestone IDs or Open Item links; completion and dependencies need review'
    edges: dict[str, set[str]] = {}
    for item in items:
        instructions = item.get('instructions')
        if instructions is not None and (not isinstance(instructions, list) or not 1 <= len(instructions) <= 8
                or any(not isinstance(step, str) or not step.strip() or len(step) > 160 for step in instructions)):
            return 'instructions need one to eight nonempty steps of at most 160 characters'
        prerequisites = item.get("prerequisites", [])
        if not isinstance(prerequisites, list) or any(not isinstance(p, str) or not p for p in prerequisites):
            return "invalid prerequisites; use a list of milestone IDs"
        edges[str(item["id"])] = set(prerequisites)
    # Kahn's algorithm avoids recursion limits and keeps unknown references blocked.
    remaining = dict(edges)
    while remaining:
        ready = {mid for mid, refs in remaining.items() if not refs.intersection(remaining)}
        if not ready:
            return "cyclic milestone prerequisites; completion and dependencies need review"
        remaining = {mid: refs for mid, refs in remaining.items() if mid not in ready}
    return ""


def plan_lines(states: Sequence[MilestoneState]) -> list[str]:
    """One short line per actionable milestone: ready first, then blocked, then unverified; done stays quiet."""
    order = {"invalid": -1, "ready": 0, "blocked": 1, "unverified": 2}
    lines: list[str] = []
    for s in sorted((x for x in states if x.state != "done"), key=lambda x: (order[x.state], x.target_date or date.max,
                                                                         x.goal_id, x.milestone_id)):
        due = ""
        if s.target_date:
            due = f" (target {s.target_date:%b} {s.target_date.day}{', overdue' if s.overdue else ''})"
        if s.state == "ready":
            lines.append(f"{s.goal_id}: ready \u2014 next step: {s.title}{due}")
            lines.append(f"{s.goal_id}/{s.milestone_id}: instruction sheet for {s.owner}; review needed")
            lines.extend(f"Step {i}: {step}" for i, step in enumerate(s.instructions, 1))
        elif s.state == "blocked":
            lines.append(f"{s.goal_id}: {s.title} waits for {', '.join(s.waiting_for)}{due}")
        elif s.state == "invalid":
            lines.append(f"{s.goal_id}: plan needs review — {s.title}")
        else:
            reason = s.review_reason or 'is marked done but has no evidence, so it is not counted'
            lines.append(f'{s.goal_id}: {s.title} {reason}{due}')
    return lines
