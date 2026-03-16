#!/usr/bin/env python3
"""
items_view.py — Script-backed /items command renderer
======================================================
Reads state/open_items.md (plaintext, no vault required).
Displays open action items grouped by priority and deadline,
with quick-task filtering support.

Usage:
  python scripts/items_view.py
  python scripts/items_view.py --format flash
  python scripts/items_view.py --format standard   (default)
  python scripts/items_view.py --format digest
  python scripts/items_view.py --quick             # show ≤5 min phone-ready tasks
  python scripts/items_view.py --domain <name>     # filter by source domain
  python scripts/items_view.py --status open       # filter by status (open|done|deferred)

Output: Markdown formatted action items list.

Exit codes:
  0 — success
  1 — open_items.md missing or unreadable

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
_ITEMS_FILE = _STATE_DIR / "open_items.md"

_PRIORITY_ICONS = {"P0": "🔴", "P1": "🟠", "P2": "🟡"}
_STATUS_ICONS = {"open": "🔲", "done": "✅", "deferred": "⏸"}

# Keywords that suggest a task is quick (≤5 min)
_QUICK_KEYWORDS = (
    "call", "email", "check", "verify", "confirm", "reply", "review",
    "send", "look up", "schedule", "book", "approve", "sign",
)


def _read_items() -> str:
    if not _ITEMS_FILE.exists():
        return ""
    try:
        return _ITEMS_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_items(content: str) -> list[dict]:
    """Parse YAML-like item blocks from open_items.md."""
    items: list[dict] = []
    # Each item starts with "- id: OI-NNN"
    raw_items = re.split(r"\n(?=- id: OI-)", content)
    for block in raw_items:
        if not re.match(r"- id: OI-\d+", block.strip()):
            continue
        item: dict = {}
        for line in block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- id:"):
                item["id"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("id:"):
                item["id"] = stripped.split(":", 1)[1].strip()
            elif ":" in stripped and not stripped.startswith("#"):
                k, _, v = stripped.partition(":")
                key = k.lstrip("- ").strip()
                val = v.strip().strip('"\'')
                if key and val:
                    item[key] = val
        if "id" in item:
            items.append(item)
    return items


def _days_until(deadline_str: str) -> int | None:
    if not deadline_str or deadline_str in ('""', ""):
        return None
    try:
        d = datetime.strptime(deadline_str.strip(), "%Y-%m-%d").date()
        return (d - date.today()).days
    except ValueError:
        return None


def _deadline_label(days: int | None) -> str:
    if days is None:
        return ""
    if days < 0:
        return f" ⚠ {abs(days)}d overdue"
    if days == 0:
        return " 🔴 due today"
    if days <= 7:
        return f" 🔴 {days}d"
    if days <= 30:
        return f" 🟠 {days}d"
    if days <= 90:
        return f" 🟡 {days}d"
    return f" · due {deadline_str}"


def _is_quick_task(item: dict) -> bool:
    """Heuristic: item has no deadline or short deadline + quick keyword in description."""
    desc = item.get("description", "").lower()
    days = _days_until(item.get("deadline", ""))
    has_quick_keyword = any(kw in desc for kw in _QUICK_KEYWORDS)
    short_or_no_deadline = days is None or 0 <= days <= 7
    return item.get("status") == "open" and has_quick_keyword and short_or_no_deadline


def _sort_key(item: dict) -> tuple:
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}.get(item.get("priority", "P2"), 3)
    days = _days_until(item.get("deadline", ""))
    deadline_rank = days if days is not None else 9999
    return (priority_rank, deadline_rank)


def _format_flash(items: list[dict], filter_status: str = "open") -> str:
    visible = [i for i in items if i.get("status", "open") == filter_status]
    open_items = [i for i in items if i.get("status") == "open"]
    p0 = [i for i in open_items if i.get("priority") == "P0"]
    lines = [
        f"## Open Items — Flash",
        f"Open: **{len(open_items)}** · P0 critical: **{len(p0)}**",
        "",
    ]
    for item in sorted(p0, key=_sort_key)[:5]:
        icon = _PRIORITY_ICONS.get(item.get("priority", "P2"), "⬜")
        days = _days_until(item.get("deadline", ""))
        dl = _deadline_label(days)
        desc = item.get("description", "?")[:80]
        lines.append(f"{icon} **{item['id']}** {desc}{dl}")
    if not p0:
        lines.append("✅ No P0 items.")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_standard(
    items: list[dict], filter_domain: str = "", filter_status: str = ""
) -> str:
    visible = items
    if filter_domain:
        visible = [i for i in visible if i.get("source_domain", "") == filter_domain]
    if filter_status:
        visible = [i for i in visible if i.get("status", "") == filter_status]
    else:
        visible = [i for i in visible if i.get("status", "open") != "done"]

    open_count = sum(1 for i in items if i.get("status") == "open")
    deferred_count = sum(1 for i in items if i.get("status") == "deferred")
    done_count = sum(1 for i in items if i.get("status") == "done")

    lines = [
        "## Open Items",
        f"Open: **{open_count}** · Deferred: {deferred_count} · Resolved: {done_count}",
        "",
    ]

    # Group by priority
    for priority in ("P0", "P1", "P2"):
        group = sorted(
            [i for i in visible if i.get("priority") == priority],
            key=_sort_key
        )
        if not group:
            continue
        icon = _PRIORITY_ICONS.get(priority, "⬜")
        lines.append(f"### {icon} {priority}")
        for item in group:
            status_icon = _STATUS_ICONS.get(item.get("status", "open"), "🔲")
            domain = item.get("source_domain", "?")
            days = _days_until(item.get("deadline", ""))
            dl = _deadline_label(days)
            desc = item.get("description", "?")[:100]
            lines.append(f"- {status_icon} **{item['id']}** `{domain}`{dl}  ")
            lines.append(f"  {desc}")
        lines.append("")

    lines.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_quick(items: list[dict]) -> str:
    """Phone-ready tasks: open items with quick keywords, sorted by priority."""
    quick = [i for i in items if _is_quick_task(i)]
    lines = [
        "## Quick Wins (≤5 min, phone-ready)",
        f"Found **{len(quick)}** quick tasks\n",
    ]
    for item in sorted(quick, key=_sort_key)[:10]:
        icon = _PRIORITY_ICONS.get(item.get("priority", "P2"), "⬜")
        domain = item.get("source_domain", "?")
        days = _days_until(item.get("deadline", ""))
        dl = _deadline_label(days)
        desc = item.get("description", "?")[:120]
        lines.append(f"- {icon} **{item['id']}** `{domain}`{dl}")
        lines.append(f"  {desc}")
    if not quick:
        lines.append("_No quick tasks found. All open items require more time or context._")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_digest(items: list[dict]) -> str:
    """Digest: all items including done, grouped by domain."""
    by_domain: dict[str, list[dict]] = {}
    for item in sorted(items, key=_sort_key):
        d = item.get("source_domain", "general")
        by_domain.setdefault(d, []).append(item)

    lines = [
        "## All Items by Domain",
        f"Total tracked: **{len(items)}**\n",
    ]
    for domain in sorted(by_domain.keys()):
        domain_items = by_domain[domain]
        open_n = sum(1 for i in domain_items if i.get("status") == "open")
        lines.append(f"### {domain.title()} ({open_n} open / {len(domain_items)} total)")
        for item in domain_items:
            priority = item.get("priority", "P2")
            status = item.get("status", "open")
            p_icon = _PRIORITY_ICONS.get(priority, "⬜")
            s_icon = _STATUS_ICONS.get(status, "🔲")
            days = _days_until(item.get("deadline", ""))
            dl = _deadline_label(days)
            desc = item.get("description", "?")[:100]
            lines.append(f"- {p_icon}{s_icon} **{item['id']}**{dl} {desc}")
        lines.append("")

    lines.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Artha Items Viewer")
    parser.add_argument(
        "--format", choices=["flash", "standard", "digest"], default="standard",
        help="Output density (default: standard)"
    )
    parser.add_argument("--quick", action="store_true", help="Show only quick (≤5 min) tasks")
    parser.add_argument("--domain", default="", help="Filter by source domain")
    parser.add_argument("--status", default="", choices=["open", "done", "deferred", ""],
                        help="Filter by status (default: all non-done)")
    args = parser.parse_args()

    content = _read_items()
    if not content:
        print(
            "⚠ state/open_items.md not found or empty. Run a catch-up to populate.",
            file=sys.stderr,
        )
        return 1

    items = _parse_items(content)
    if not items:
        print("_No open items found._")
        return 0

    if args.quick:
        print(_format_quick(items))
    elif args.format == "flash":
        print(_format_flash(items, filter_status=args.status or "open"))
    elif args.format == "digest":
        print(_format_digest(items))
    else:
        print(_format_standard(items, filter_domain=args.domain, filter_status=args.status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
