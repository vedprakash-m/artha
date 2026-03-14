#!/usr/bin/env python3
"""
goals_view.py — Script-backed /goals command renderer
======================================================
Reads state/goals.md (plaintext, no vault required).
Displays goal scorecard with progress bars, status indicators,
and priority grouping.

Usage:
  python scripts/goals_view.py
  python scripts/goals_view.py --format flash
  python scripts/goals_view.py --format standard   (default)
  python scripts/goals_view.py --format digest

Output: Markdown formatted goals scorecard.

Exit codes:
  0 — success
  1 — goals.md missing or unreadable

Ref: specs/enhance.md §1.13 / §10.0.1
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"
_GOALS_FILE = _STATE_DIR / "goals.md"

_STATUS_ICONS = {
    "on_track": "🟢",
    "in_progress": "🟢",
    "at_risk": "🟡",
    "urgent": "🟠",
    "critical": "🔴",
    "not_started": "⬜",
    "done": "✅",
    "paused": "⏸",
}

_STATUS_PRIORITY = {
    "critical": 0, "urgent": 1, "at_risk": 2, "in_progress": 3,
    "on_track": 4, "not_started": 5, "done": 6, "paused": 7,
}


def _read_goals() -> str:
    if not _GOALS_FILE.exists():
        return ""
    try:
        return _GOALS_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_frontmatter(content: str) -> dict:
    """Extract key values from YAML frontmatter."""
    result: dict = {}
    fm_m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
    if not fm_m:
        return result
    for line in fm_m.group(1).split("\n"):
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip().strip("'\"")
    return result


def _parse_goals_index(content: str) -> list[dict]:
    """Parse the goals_index YAML block into a list of goal dicts."""
    goals: list[dict] = []
    # Find the goals_index block
    m = re.search(r"goals_index:\n(.*?)(?:\n```|\Z)", content, re.DOTALL)
    if not m:
        return goals

    current: dict = {}
    for line in m.group(1).split("\n"):
        stripped = line.strip()
        if stripped.startswith("- id:"):
            if current:
                goals.append(current)
            current = {"id": stripped.split(":", 1)[1].strip()}
        elif current and ":" in stripped and not stripped.startswith("#"):
            k, _, v = stripped.partition(":")
            current[k.strip()] = v.strip().strip("'\"")
    if current:
        goals.append(current)
    return goals


def _days_until(deadline_str: str) -> int | None:
    """Return days until deadline, or None if no deadline."""
    if not deadline_str or deadline_str == '""':
        return None
    try:
        d = datetime.strptime(deadline_str.strip(), "%Y-%m-%d").date()
        return (d - date.today()).days
    except ValueError:
        return None


def _progress_bar(current: str, target: str, width: int = 10) -> str:
    """Render a simple ASCII progress bar."""
    try:
        cur = float(current)
        tgt = float(target)
        if tgt == 0:
            return ""
        pct = min(max(cur / tgt, 0.0), 1.0)
        filled = round(pct * width)
        return f"[{'█' * filled}{'░' * (width - filled)}] {pct:.0%}"
    except (ValueError, TypeError):
        return ""


def _format_deadline(days: int | None) -> str:
    if days is None:
        return "no deadline"
    if days < 0:
        return f"⚠ {abs(days)}d overdue"
    if days == 0:
        return "🔴 due today"
    if days <= 7:
        return f"🔴 {days}d"
    if days <= 30:
        return f"🟠 {days}d"
    if days <= 90:
        return f"🟡 {days}d"
    return f"{days}d"


def _format_flash(goals: list[dict], meta: dict) -> str:
    attention = [g for g in goals if g.get("status", "") in ("critical", "urgent", "at_risk")]
    lines = [
        "## Goals — Flash",
        f"Total: **{len(goals)}** goals · Needs attention: **{len(attention)}**",
        "",
    ]
    for g in sorted(attention, key=lambda x: _STATUS_PRIORITY.get(x.get("status", ""), 9)):
        icon = _STATUS_ICONS.get(g.get("status", ""), "⬜")
        days = _days_until(g.get("deadline", ""))
        dl = f" · {_format_deadline(days)}" if days is not None else ""
        lines.append(f"{icon} **{g.get('id', '?')}** {g.get('title', '?')}{dl}")
    if not attention:
        lines.append("✅ All goals on track — no urgent items")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_standard(goals: list[dict], meta: dict) -> str:
    last_updated = meta.get("last_updated", "unknown")
    lines = [
        "## Goal Scorecard",
        f"_State as of: {last_updated}_\n",
        "| ID | Title | Status | Priority | Deadline | Progress |",
        "|----|-------|--------|----------|----------|----------|",
    ]
    for g in sorted(goals, key=lambda x: (
        int(x.get("priority", "P9")[1:]) if x.get("priority", "P9")[1:].isdigit() else 9,
        _STATUS_PRIORITY.get(x.get("status", ""), 9),
    )):
        icon = _STATUS_ICONS.get(g.get("status", ""), "⬜")
        status = g.get("status", "?")
        priority = g.get("priority", "?")
        days = _days_until(g.get("deadline", ""))
        dl = _format_deadline(days)
        # Build progress bar if metric exists
        cur = g.get("current_value", "")
        tgt = g.get("target_value", "")
        progress = _progress_bar(cur, tgt) if cur and tgt else "—"
        title = g.get("title", "?")[:55]
        lines.append(
            f"| {g.get('id', '?')} | {title} | {icon} {status} | {priority} | {dl} | {progress} |"
        )
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_digest(content: str, goals: list[dict], meta: dict) -> str:
    """Digest: standard table + full narrative per goal from the state file."""
    standard = _format_standard(goals, meta)
    lines = [standard, "\n---\n", "## Goal Details\n"]

    # Extract narrative sections from the free-text part of goals.md
    # Sections are "### Goal N — <title>"
    sections = re.split(r"\n---\n", content)
    narrative_parts = []
    for sec in sections:
        if re.search(r"^### Goal \d+", sec, re.MULTILINE):
            narrative_parts.append(sec.strip())

    if narrative_parts:
        lines.extend(narrative_parts)
    else:
        lines.append("_No goal detail narratives found in goals.md._")

    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Artha Goals Viewer")
    parser.add_argument(
        "--format", choices=["flash", "standard", "digest"], default="standard",
        help="Output density (default: standard)"
    )
    args = parser.parse_args()

    content = _read_goals()
    if not content:
        print(
            "⚠ state/goals.md not found or empty. Run a catch-up or /bootstrap to populate.",
            file=sys.stderr,
        )
        return 1

    meta = _extract_frontmatter(content)
    goals = _parse_goals_index(content)

    if not goals:
        print("_No goals found in goals.md. Use /goals to define goals._")
        return 0

    if args.format == "flash":
        print(_format_flash(goals, meta))
    elif args.format == "digest":
        print(_format_digest(content, goals, meta))
    else:
        print(_format_standard(goals, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
