#!/usr/bin/env python3
"""
scripts/goals_writer.py — Deterministic YAML writer for goals state files.

Mutates ONLY the `goals` YAML frontmatter block in state/goals.md (or any
`--file` target). The Markdown body is never touched.

All writes use write_state_atomic() from scripts/work/helpers.py (Reflection
Loop Sprint 0) — same cross-platform atomic write utility used by /work reflect.

Usage:
  # Update a field on an existing goal
  python3 scripts/goals_writer.py --update G-002 --next-action "Weigh in Saturday"
  python3 scripts/goals_writer.py --update G-002 --metric-current 178 --last-progress 2026-04-05
  python3 scripts/goals_writer.py --update G-003 --status parked \
      --parked-reason "Schedule too busy" --parked-since 2026-04-01

  # Create a new goal
  python3 scripts/goals_writer.py --create --id G-005 --title "Save for college fund" \
      --type outcome --category finance --target-date 2030-06-01

  # Target work goals file
  python3 scripts/goals_writer.py --file state/work/work-goals.md --update G-W-001 \
      --status done

Exit codes:
  0 — success
  1 — goal ID not found (--update only)
  2 — validation error / missing required arg
  3 — write failure

Ref: specs/goals-reloaded.md §3.2, §3.6, Implementation Step 1.2
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_DEFAULT_GOALS_FILE = _ARTHA_DIR / "state" / "goals.md"

# Import write_state_atomic from Reflection Loop Sprint 0
sys.path.insert(0, str(_SCRIPTS_DIR))
from work.helpers import write_state_atomic  # noqa: E402

_VALID_STATUSES = {"active", "parked", "done", "dropped"}
_VALID_TYPES = {"outcome", "habit", "milestone"}


# ---------------------------------------------------------------------------
# YAML frontmatter helpers
# ---------------------------------------------------------------------------

def _split_frontmatter(content: str) -> tuple[dict, str]:
    """Split `---\n...\n---\n[body]` into (frontmatter_dict, body_str).

    Returns ({}, content) if no valid frontmatter found.
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 4:]  # skip \n---
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def _assemble(fm: dict, body: str) -> str:
    """Reconstruct the file from frontmatter dict + body string."""
    fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{fm_text}---\n{body}"


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def update_goal(goals_file: Path, goal_id: str, fields: dict[str, Any]) -> int:
    """Update mutable fields on an existing goal. Returns exit code."""
    content = goals_file.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(content)
    goals: list[dict] = fm.get("goals", [])

    idx = next((i for i, g in enumerate(goals) if g.get("id") == goal_id), None)
    if idx is None:
        print(f"error: goal {goal_id!r} not found in {goals_file}", file=sys.stderr)
        return 1

    goal = goals[idx]

    # Apply field updates
    if "status" in fields:
        status = fields["status"]
        if status not in _VALID_STATUSES:
            print(f"error: invalid status {status!r} — must be one of {_VALID_STATUSES}", file=sys.stderr)
            return 2
        goal["status"] = status
        # Auto-clear parked fields if reactivating
        if status != "parked":
            goal.pop("parked_reason", None)
            goal.pop("parked_since", None)
        elif "parked_since" not in fields:
            # Auto-set parked_since when parking a goal
            from datetime import date as _date
            goal["parked_since"] = str(_date.today())

    if "next_action" in fields:
        goal["next_action"] = fields["next_action"] or None

    if "next_action_date" in fields:
        goal["next_action_date"] = fields["next_action_date"] or None

    if "parked_reason" in fields:
        goal["parked_reason"] = fields["parked_reason"]

    if "parked_since" in fields:
        goal["parked_since"] = fields["parked_since"]

    if "last_progress" in fields:
        goal["last_progress"] = fields["last_progress"]

    if "metric_current" in fields:
        metric = goal.setdefault("metric", {})
        try:
            metric["current"] = float(fields["metric_current"])
        except (ValueError, TypeError):
            print(f"error: metric-current must be numeric", file=sys.stderr)
            return 2

    if "metric_baseline" in fields:
        metric = goal.setdefault("metric", {})
        try:
            metric["baseline"] = float(fields["metric_baseline"])
        except (ValueError, TypeError):
            print("error: metric-baseline must be numeric", file=sys.stderr)
            return 2

    if "review_date" in fields:
        goal["review_date"] = fields["review_date"]

    if "target_date" in fields:
        goal["target_date"] = fields["target_date"]

    fm["goals"][idx] = goal
    fm["last_updated"] = str(date.today())
    write_state_atomic(goals_file, _assemble(fm, body))
    print(f"updated {goal_id}: {list(fields.keys())}")
    return 0


def add_sprint(goals_file: Path, fields: dict[str, Any]) -> int:
    """Append a sprint block to goals.md under the top-level `sprints:` key.

    Validates:
    - goal_ref exists and is not parked
    - no active sprint already exists for that goal
    - goal.target_date >= sprint.end (sprint must finish before goal deadline)
    - sprint id is unique

    Returns exit code (0 = success, 1 = goal not found, 2 = validation error, 3 = write failure).
    """
    required = {"id", "goal_ref", "start", "end", "target"}
    missing = required - fields.keys()
    if missing:
        print(f"error: missing required fields: {missing}", file=sys.stderr)
        return 2

    content = goals_file.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(content)
    goals: list[dict] = fm.get("goals", [])

    # Validate goal_ref exists
    goal_ref = fields["goal_ref"]
    goal = next((g for g in goals if g.get("id") == goal_ref), None)
    if goal is None:
        print(f"error: goal {goal_ref!r} not found in {goals_file}", file=sys.stderr)
        return 1

    # Parked goals must not get sprints (§4.4 Archetype 5)
    if goal.get("status") == "parked":
        print(f"error: goal {goal_ref!r} is parked — cannot create sprint for a parked goal",
              file=sys.stderr)
        return 2

    # Validate sprint end <= goal target_date
    goal_target = str(goal.get("target_date") or "")
    sprint_end = str(fields["end"])
    if goal_target and sprint_end > goal_target:
        print(
            f"error: sprint end ({sprint_end}) is after goal target_date ({goal_target}) "
            f"— shorten sprint or extend goal deadline",
            file=sys.stderr,
        )
        return 2

    sprints: list[dict] = fm.setdefault("sprints", [])

    # Duplicate sprint ID guard
    if any(s.get("id") == fields["id"] for s in sprints):
        print(f"error: sprint {fields['id']!r} already exists", file=sys.stderr)
        return 2

    # Existing active sprint guard for this goal
    active = [s for s in sprints if s.get("goal_ref") == goal_ref and s.get("status") == "active"]
    if active:
        print(
            f"error: goal {goal_ref!r} already has active sprint {active[0]['id']!r} "
            f"— close it before adding a new one",
            file=sys.stderr,
        )
        return 2

    new_sprint: dict[str, Any] = {
        "id": fields["id"],
        "goal_ref": goal_ref,
        "start": fields["start"],
        "end": sprint_end,
        "target": fields["target"],
        "status": "active",
        "check_in_cadence": fields.get("check_in_cadence", "weekly"),
    }
    if fields.get("signal_ref"):
        new_sprint["signal_ref"] = fields["signal_ref"]

    sprints.append(new_sprint)
    fm["last_updated"] = str(date.today())
    try:
        write_state_atomic(goals_file, _assemble(fm, body))
    except OSError as exc:
        print(f"error: write failed: {exc}", file=sys.stderr)
        return 3
    print(f"added sprint {fields['id']!r} for goal {goal_ref!r}: {fields['target']!r}")
    return 0


def create_goal(goals_file: Path, fields: dict[str, Any]) -> int:
    """Append a new goal to the goals list. Returns exit code."""
    required = {"id", "title", "type", "category"}
    missing = required - fields.keys()
    if missing:
        print(f"error: missing required fields: {missing}", file=sys.stderr)
        return 2

    gtype = fields["type"]
    if gtype not in _VALID_TYPES:
        print(f"error: invalid type {gtype!r} — must be one of {_VALID_TYPES}", file=sys.stderr)
        return 2

    content = goals_file.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(content)
    goals: list[dict] = fm.setdefault("goals", [])

    # Prevent duplicate IDs
    if any(g.get("id") == fields["id"] for g in goals):
        print(f"error: goal {fields['id']!r} already exists", file=sys.stderr)
        return 2

    new_goal: dict[str, Any] = {
        "id": fields["id"],
        "title": fields["title"],
        "type": gtype,
        "category": fields["category"],
        "status": "active",
        "next_action": fields.get("next_action"),
        "next_action_date": fields.get("next_action_date"),
        "review_date": fields.get("review_date"),
        "last_progress": None,
        "created": str(date.today()),
        "target_date": fields.get("target_date"),
        "leading_indicators": [],
    }

    if fields.get("metric_target") is not None:
        try:
            new_goal["metric"] = {
                "current": float(fields["metric_current"]) if fields.get("metric_current") is not None else 0.0,
                "target": float(fields["metric_target"]),
                "unit": fields.get("metric_unit") or "",
                "direction": fields.get("metric_direction") or "up",
            }
            if fields.get("metric_baseline") is not None:
                new_goal["metric"]["baseline"] = float(fields["metric_baseline"])
        except (ValueError, TypeError):
            print("error: metric values must be numeric", file=sys.stderr)
            return 2

    goals.append(new_goal)
    fm["last_updated"] = str(date.today())
    write_state_atomic(goals_file, _assemble(fm, body))
    print(f"created {fields['id']!r}: {fields['title']!r}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Artha Goals Writer — deterministic YAML mutations on goals state files",
    )
    parser.add_argument("--file", default=str(_DEFAULT_GOALS_FILE),
                        help="Path to goals state file (default: state/goals.md)")

    sub = parser.add_subparsers(dest="command")

    # -- update sub-command --
    upd = sub.add_parser("--update", help="Update fields on an existing goal")
    upd.add_argument("goal_id", metavar="GOAL_ID")
    upd.add_argument("--status", choices=list(_VALID_STATUSES))
    upd.add_argument("--next-action", dest="next_action")
    upd.add_argument("--next-action-date", dest="next_action_date")
    upd.add_argument("--parked-reason", dest="parked_reason")
    upd.add_argument("--parked-since", dest="parked_since")
    upd.add_argument("--last-progress", dest="last_progress")
    upd.add_argument("--metric-current", dest="metric_current")
    upd.add_argument("--metric-baseline", dest="metric_baseline")
    upd.add_argument("--review-date", dest="review_date")
    upd.add_argument("--target-date", dest="target_date")

    # -- create sub-command --
    crt = sub.add_parser("--create", help="Create a new goal")
    crt.add_argument("--id", required=True)
    crt.add_argument("--title", required=True)
    crt.add_argument("--type", dest="type", required=True, choices=list(_VALID_TYPES))
    crt.add_argument("--category", required=True)
    crt.add_argument("--target-date", dest="target_date")
    crt.add_argument("--next-action", dest="next_action")
    crt.add_argument("--next-action-date", dest="next_action_date")
    crt.add_argument("--review-date", dest="review_date")
    crt.add_argument("--metric-target", dest="metric_target")
    crt.add_argument("--metric-current", dest="metric_current")
    crt.add_argument("--metric-baseline", dest="metric_baseline")
    crt.add_argument("--metric-unit", dest="metric_unit")
    crt.add_argument("--metric-direction", dest="metric_direction", choices=["up", "down"])

    # argparse doesn't handle `--update G-002` as a subcommand naturally
    # Reparse manually to handle the `--update ID` pattern from the spec
    argv = sys.argv[1:]
    goals_file = Path(_DEFAULT_GOALS_FILE)

    # Extract --file first
    if "--file" in argv:
        fi = argv.index("--file")
        goals_file = Path(argv[fi + 1])
        argv = argv[:fi] + argv[fi + 2:]

    if not argv:
        parser.print_help()
        return 2

    if argv[0] == "--update" and len(argv) >= 2:
        goal_id = argv[1]
        # Parse remaining flags
        rem = argparse.ArgumentParser(add_help=False)
        rem.add_argument("--status")
        rem.add_argument("--next-action", dest="next_action")
        rem.add_argument("--next-action-date", dest="next_action_date")
        rem.add_argument("--parked-reason", dest="parked_reason")
        rem.add_argument("--parked-since", dest="parked_since")
        rem.add_argument("--last-progress", dest="last_progress")
        rem.add_argument("--metric-current", dest="metric_current")
        rem.add_argument("--metric-baseline", dest="metric_baseline")
        rem.add_argument("--review-date", dest="review_date")
        rem.add_argument("--target-date", dest="target_date")
        ns, _ = rem.parse_known_args(argv[2:])
        fields = {k: v for k, v in vars(ns).items() if v is not None}
        if not fields:
            print("error: --update requires at least one field to change", file=sys.stderr)
            return 2
        return update_goal(goals_file, goal_id, fields)

    elif argv[0] == "--create":
        rem = argparse.ArgumentParser(add_help=False)
        rem.add_argument("--id", required=True)
        rem.add_argument("--title", required=True)
        rem.add_argument("--type", dest="type", required=True)
        rem.add_argument("--category", required=True)
        rem.add_argument("--target-date", dest="target_date")
        rem.add_argument("--next-action", dest="next_action")
        rem.add_argument("--next-action-date", dest="next_action_date")
        rem.add_argument("--review-date", dest="review_date")
        rem.add_argument("--metric-target", dest="metric_target")
        rem.add_argument("--metric-current", dest="metric_current")
        rem.add_argument("--metric-baseline", dest="metric_baseline")
        rem.add_argument("--metric-unit", dest="metric_unit")
        rem.add_argument("--metric-direction", dest="metric_direction")
        ns, _ = rem.parse_known_args(argv[1:])
        fields = {k: v for k, v in vars(ns).items() if v is not None}
        return create_goal(goals_file, fields)

    elif argv[0] == "--add-sprint":
        rem = argparse.ArgumentParser(
            prog="goals_writer.py --add-sprint",
            description="Append a validated sprint block to goals.md",
        )
        rem.add_argument("--id", required=True)
        rem.add_argument("--goal-ref", dest="goal_ref", required=True)
        rem.add_argument("--start", required=True)
        rem.add_argument("--end", required=True)
        rem.add_argument("--target", required=True)
        rem.add_argument("--signal-ref", dest="signal_ref")
        rem.add_argument("--check-in-cadence", dest="check_in_cadence", default="weekly")
        ns, _ = rem.parse_known_args(argv[1:])
        fields = {k: v for k, v in vars(ns).items() if v is not None}
        return add_sprint(goals_file, fields)

    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
