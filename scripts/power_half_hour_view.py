#!/usr/bin/env python3
# pii-guard: ignore-file — task descriptions may contain names; pii_guard applied on output
"""
scripts/power_half_hour_view.py — /power command renderer (E14).

Assembles a focused 30-minute task session from state/open_items.md.
Surfaces quick-win items (⚡Quick ≤5 min) and at most 1 medium item.

Selection logic (deterministic):
  1. Filter open items where effort tag is ⚡ (Quick, ≤5 min)
  2. Sort by: overdue first → deadline proximity → age
  3. Pack into 30-minute window (6× ⚡Quick items max)
  4. If <3 quick items, include 1× 🔨Medium item to fill window
  5. Show estimated total time and completion likelihood

Output format registered in domain_index.py under /power command.

Usage:
  python scripts/power_half_hour_view.py
  python scripts/power_half_hour_view.py --format compact

Config flag: enhancements.power_half_hour (default: true)

Ref: specs/act-reloaded.md Enhancement 14, specs/artha-ux-spec.md §10.15
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

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

try:
    from context_offloader import load_harness_flag as _load_flag  # type: ignore[import]
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# PII filter for outbound
_PII_STRIP = [
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "[SSN]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
]


def _pii_filter(text: str) -> str:
    for pat, repl in _PII_STRIP:
        text = pat.sub(repl, text)
    return text


# Effort tag → time estimate in minutes
_EFFORT_TIMES = {
    "⚡": 5,
    "quick": 5,
    "🔨": 15,
    "medium": 15,
    "🏗️": 30,
    "deep": 30,
}

_WINDOW_MINUTES = 30
_MAX_QUICK_ITEMS = 6


def _parse_open_items(open_items_path: Path) -> list[dict]:
    """Parse active items from state/open_items.md."""
    if not open_items_path.exists():
        return []

    content = open_items_path.read_text(encoding="utf-8")
    # Use YAML parsing for structured items
    if not _YAML_AVAILABLE:
        return []

    items = []
    # Items are YAML blocks delimited by "- id:" lines
    # Split on list item boundaries
    item_blocks = re.split(r"\n(?=- id:)", content)
    for block in item_blocks:
        block = block.strip()
        if not block.startswith("- id:"):
            continue
        # Parse the YAML list item directly (it's already a valid YAML list entry)
        try:
            parsed = yaml.safe_load(block)
            if not isinstance(parsed, list) or not parsed:
                continue
            item = parsed[0]
            if isinstance(item, dict) and item.get("status") == "open":
                items.append(item)
        except Exception:
            pass

    return items


def _classify_effort(item: dict) -> tuple[str, int]:
    """Return (effort_tag, minutes) for an item.

    Looks at description for effort tags. Defaults to 10 min untagged.
    """
    desc = str(item.get("description", ""))
    for tag, mins in _EFFORT_TIMES.items():
        if tag in desc:
            return tag, mins
    # Also check a dedicated effort field if present
    effort = str(item.get("effort", "")).lower()
    for tag, mins in _EFFORT_TIMES.items():
        if tag.lower() in effort:
            return tag, mins
    return "untagged", 10  # default 10 min untagged


def _sort_key(item: dict, today: date) -> tuple:
    """Sort key: overdue first, then deadline proximity, then age."""
    deadline_str = str(item.get("deadline", "") or "")
    date_added_str = str(item.get("date_added", "") or "")
    priority = str(item.get("priority", "P3"))

    try:
        dl = date.fromisoformat(deadline_str) if deadline_str else date(9999, 12, 31)
    except ValueError:
        dl = date(9999, 12, 31)

    try:
        da = date.fromisoformat(date_added_str) if date_added_str else today
    except ValueError:
        da = today

    is_overdue = 0 if dl < today else 1
    days_until_deadline = (dl - today).days
    priority_n = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 3)

    return (is_overdue, days_until_deadline, priority_n, da)


def render_power_session(
    open_items_path: Path | None = None,
    fmt: str = "standard",
) -> tuple[str, int]:
    """Render a 30-minute power session. Returns (text, exit_code)."""
    if not _load_flag("enhancements.power_half_hour", default=True):
        return "ℹ️  Power Half Hour is disabled (enhancements.power_half_hour: false)", 0

    if open_items_path is None:
        open_items_path = _STATE_DIR / "open_items.md"

    items = _parse_open_items(open_items_path)

    if not items:
        return (
            "⚡ *POWER HALF HOUR*\n\n"
            "📋 No open items found. Add tasks via `/items add` or during catch-up.",
            0,
        )

    today = date.today()
    items_sorted = sorted(items, key=lambda i: _sort_key(i, today))

    # Classify effort
    classified = [(i, *_classify_effort(i)) for i in items_sorted]

    # Select items for 30-minute window
    selected: list[tuple[dict, str, int]] = []
    total_min = 0
    quick_count = 0
    medium_added = False

    for item, tag, mins in classified:
        if total_min >= _WINDOW_MINUTES:
            break
        if tag in ("⚡", "quick"):
            if quick_count >= _MAX_QUICK_ITEMS:
                continue
            if total_min + mins <= _WINDOW_MINUTES:
                selected.append((item, tag, mins))
                total_min += mins
                quick_count += 1
        elif tag in ("🔨", "medium") and not medium_added:
            # Only add medium if we have room and fewer than 3 quick items
            if quick_count < 3 and total_min + mins <= _WINDOW_MINUTES:
                selected.append((item, tag, mins))
                total_min += mins
                medium_added = True

    # If no quick-tagged items, show top untagged items
    if not selected:
        for item, tag, mins in classified[:4]:
            if total_min + mins <= _WINDOW_MINUTES:
                selected.append((item, tag, mins))
                total_min += mins

    # Calculate completion likelihood
    if len(selected) <= 3:
        likelihood = "High 🎯"
    elif len(selected) <= 5:
        likelihood = "Medium 📊"
    else:
        likelihood = "Optimistic 🤞"

    # Build output
    lines = [
        f"⚡ POWER HALF HOUR ({_WINDOW_MINUTES} min)",
        "─" * 33,
    ]

    if not selected:
        lines.append("📋 No quick-win items found. Try adding effort tags (⚡) to open items.")
    else:
        untagged_note = False
        for i, (item, tag, mins) in enumerate(selected, 1):
            item_id = item.get("id", "OI-???")
            desc = str(item.get("description", "No description")).split("\n")[0]
            desc = desc[:80]  # truncate for display
            deadline_str = str(item.get("deadline", "") or "")
            overdue_flag = ""
            try:
                if deadline_str and date.fromisoformat(deadline_str) < today:
                    overdue_flag = " 🔴"
            except ValueError:
                pass
            if tag == "untagged":
                untagged_note = True
            lines.append(f"{i}. [{mins} min] {item_id}: {_pii_filter(desc)}{overdue_flag}")

        lines.append("─" * 33)
        lines.append(
            f"Est. {total_min} min | {len(selected)} item{'s' if len(selected) != 1 else ''}"
            f" | Completion: {likelihood}"
        )
        if untagged_note:
            lines.append("")
            lines.append("_💡 Tip: Add ⚡ to item descriptions for better selection._")

    return "\n".join(lines), 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Power Half Hour view")
    parser.add_argument("--format", choices=["standard", "compact"], default="standard")
    args = parser.parse_args(argv)

    text, code = render_power_session(fmt=args.format)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
