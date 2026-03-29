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

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"
_GOALS_FILE = _STATE_DIR / "goals.md"
_WORK_GOALS_FILE = _STATE_DIR / "work" / "work-goals.md"

try:
    from work.reflect_reader import ReflectReader as _ReflectReader
    _REFLECT_READER_AVAILABLE = True
except ImportError:
    _ReflectReader = None  # type: ignore[assignment,misc]
    _REFLECT_READER_AVAILABLE = False

_WORK_STATE_DIR = _ARTHA_DIR / "state" / "work"

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


def _parse_goals_yaml(content: str, tag: str | None = None) -> list[dict]:
    """Parse the `goals` list from YAML frontmatter (schema v2.0).

    Falls back to empty list if no `goals` key found (schema v1.0 files).
    If `tag` is provided (e.g. '[work]'), adds a `_tag` key to every goal
    for display purposes.
    """
    goals: list[dict] = []
    try:
        # Extract only the frontmatter block to avoid Markdown table pipes
        # being misinterpreted as YAML block scalars.
        fm_m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
        if not fm_m:
            return []
        fm = yaml.safe_load(fm_m.group(1)) or {}
        raw = fm.get("goals", [])
        if isinstance(raw, list):
            goals = [g for g in raw if isinstance(g, dict)]
    except Exception:  # noqa: BLE001
        return []
    if tag:
        for g in goals:
            g["_tag"] = tag
    return goals


# Keep legacy alias so existing call-sites importing by name don't break.
_parse_goals_index = _parse_goals_yaml


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


def _format_leading(goals: list[dict], meta: dict, work_state_dir: Path) -> str:
    """Leading indicators: per-goal trend data from ReflectReader.get_goal_trend()."""
    lines = [
        "## Goal Leading Indicators",
        f"_State as of: {meta.get('last_updated', 'unknown')}_\n",
    ]
    if not _REFLECT_READER_AVAILABLE:
        lines.append(
            "_\u26a0 ReflectReader not available "
            "\u2014 run with PYTHONPATH=scripts_"
        )
        return "\n".join(lines)
    try:
        reader = _ReflectReader(work_state_dir)  # type: ignore[misc]
        lines += [
            "| Goal | ID | Status | Trend (8w) | Last Score |",
            "|------|----|--------|-----------|------------|"]
        for g in sorted(goals, key=lambda x: (
            int(x.get("priority", "P9")[1:]) if x.get("priority", "P9")[1:].isdigit() else 9,
        )):
            gid = g.get("id", "?")
            trend = reader.get_goal_trend(gid, last_n=8)
            scored = [s for s in trend.scores if s is not None]
            if scored:
                last_score = f"{scored[-1]:.0%}"
                bar = "".join(
                    "\u2593" if s is not None and s >= 0.7
                    else "\u2592" if s is not None and s >= 0.4
                    else "\u2591" if s is not None
                    else "\xb7"
                    for s in trend.scores
                )
                trend_str = f"`{bar}`"
            else:
                last_score = "\u2014"
                trend_str = "_(Phase 1 \u2014 scoring begins Phase 2)_"
            icon = _STATUS_ICONS.get(g.get("status", ""), "\u2b1c")
            title = g.get("title", "?")[:45]
            lines.append(
                f"| {title} | {gid} | {icon} | {trend_str} | {last_score} |"
            )
    except Exception as exc:
        lines.append(f"_Error loading trend data: {exc}_")
    lines.append(
        f"\n_Generated: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    )
    return "\n".join(lines)


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
        "| ID | Title | Type | Status | Next Action | Stale | Progress |",
        "|----|----|------|--------|-------------|-------|----------|",
    ]
    for g in sorted(goals, key=lambda x: (
        int(x.get("priority", "P9")[1:]) if x.get("priority", "P9")[1:].isdigit() else 9,
        _STATUS_PRIORITY.get(x.get("status", ""), 9),
    )):
        icon = _STATUS_ICONS.get(g.get("status", ""), "⬜")
        status = g.get("status", "?")
        gtype = g.get("type", "")
        tag = f" `{g['_tag']}`" if g.get("_tag") else ""
        # Next action with overdue flag
        na = g.get("next_action") or "—"
        na_date = g.get("next_action_date")
        if na_date:
            days_na = _days_until(na_date)
            if days_na is not None and days_na < 0:
                na = f"⚠️ {na} (overdue {abs(days_na)}d)"
        # Staleness
        lp = g.get("last_progress")
        if lp and lp not in (None, "null", ""):
            stale_n = (date.today() - date.fromisoformat(str(lp)[:10])).days
            stale = f"{stale_n}d ago"
        else:
            stale = "never"
        # Progress bar from metric (Outcome goals only)
        metric = g.get("metric")
        if metric and gtype == "outcome":
            cur = float(metric.get("current") or 0)
            tgt = float(metric.get("target") or 0)
            unit = metric.get("unit", "")
            direction = metric.get("direction", "up")
            if tgt:
                if direction == "down":
                    baseline = metric.get("baseline")
                    if baseline:
                        base_f = float(baseline)
                        total = base_f - tgt
                        remaining = cur - tgt
                        pct_done = max(0.0, min(1.0, 1.0 - (remaining / total if total else 0)))
                        filled = round(pct_done * 10)
                        bar = f"[{'█' * filled}{'░' * (10 - filled)}] {pct_done:.0%}"
                        progress = f"{bar} ({cur}{unit}→{tgt}{unit})"
                    else:
                        remaining = max(0.0, cur - tgt)
                        progress = f"({cur}{unit} → {tgt}{unit}, {remaining:.1f}{unit} left)"
                else:
                    pct_done = min(max(cur / tgt, 0.0), 1.0)
                    filled = round(pct_done * 10)
                    bar = f"[{'█' * filled}{'░' * (10 - filled)}] {pct_done:.0%}"
                    progress = f"{bar} ({cur}{unit}→{tgt}{unit})"
            else:
                progress = "—"
        else:
            progress = "—"
        title = (g.get("title", "?") + tag)[:55]
        lines.append(
            f"| {g.get('id', '?')} | {title} | {gtype} | {icon} {status} | {na[:40]} | {stale} | {progress} |"
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
    parser.add_argument(
        "--leading", action="store_true",
        help="Show per-goal leading indicators with trend data (uses ReflectReader)",
    )
    parser.add_argument(
        "--scope", choices=["personal", "work", "all"], default="personal",
        help="personal=state/goals.md only (default); work=state/work/work-goals.md only; "
             "all=merge both files (work goals tagged [work])",
    )
    args = parser.parse_args()

    # Load goals based on scope
    if args.scope == "work":
        content = _WORK_GOALS_FILE.read_text(encoding="utf-8") if _WORK_GOALS_FILE.exists() else ""
        if not content:
            print("\u26a0 state/work/work-goals.md not found. Run /work bootstrap.", file=sys.stderr)
            return 1
        meta = _extract_frontmatter(content)
        goals = _parse_goals_yaml(content)
    elif args.scope == "all":
        personal_content = _read_goals()
        work_content = _WORK_GOALS_FILE.read_text(encoding="utf-8") if _WORK_GOALS_FILE.exists() else ""
        if not personal_content and not work_content:
            print("\u26a0 No goals files found.", file=sys.stderr)
            return 1
        meta = _extract_frontmatter(personal_content or work_content)
        goals = _parse_goals_yaml(personal_content or "") + _parse_goals_yaml(work_content or "", tag="[work]")
    else:  # personal (default)
        content = _read_goals()
        if not content:
            print(
                "\u26a0 state/goals.md not found or empty. Run a catch-up or /bootstrap to populate.",
                file=sys.stderr,
            )
            return 1
        meta = _extract_frontmatter(content)
        goals = _parse_goals_yaml(content)

    if not goals:
        print("_No goals found. Use /goals to define goals._")
        return 0

    if args.leading:
        print(_format_leading(goals, meta, _WORK_STATE_DIR))
        return 0

    if args.format == "flash":
        print(_format_flash(goals, meta))
    elif args.format == "digest":
        print(_format_digest(content if args.scope != "all" else "", goals, meta))
    else:
        print(_format_standard(goals, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
