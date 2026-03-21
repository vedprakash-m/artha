#!/usr/bin/env python3
# pii-guard: ignore-file — retrospective contains aggregate metrics, no raw PII
"""
scripts/retrospective_view.py — Monthly retrospective generator (E15).

Generates a monthly retrospective when Step 3 sets generate_monthly_retro=true
(triggered on 1st of each month when last_retro > 28 days ago).

Sections (per §8.10 briefing format):
  1. Month at a Glance — headline metrics
  2. Goals Progress — delta vs. start of month (goals.md)
  3. Domain Activity — which domains updated (state file timestamps)
  4. Decisions Made — from decisions.md archive
  5. Open Items Velocity — created vs. completed vs. aged
  6. Pattern Insights — from memory.md facts added this month
  7. Next Month Preview — upcoming deadlines (open_items.md)

SCOPE LIMITATION (§15): Domain Activity reports WHICH domains were updated
and how many times (from last_updated timestamps), NOT what changed semantically.
Semantic diffing requires LLM processing and is deferred to Phase 2.

Output saved to: summaries/YYYY-MM-retro.md

Usage:
  python scripts/retrospective_view.py                        # auto-detect month
  python scripts/retrospective_view.py --month 2026-03       # explicit month
  python scripts/retrospective_view.py --dry-run              # print to stdout

Config flag: enhancements.monthly_retrospective (default: true)
Min threshold: ≥2 catch-up runs in the target month (configurable)

Ref: specs/act-reloaded.md Enhancement 15, config/briefing-formats.md §8.10
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
_SUMMARIES_DIR = _ARTHA_DIR / "summaries"

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

_MIN_CATCHUP_RUNS = 2  # Minimum runs in month for meaningful retro


# --- Helpers ----------------------------------------------------------------

def _load_frontmatter(path: Path) -> dict:
    """Load YAML frontmatter from a state file."""
    if not path.exists() or not _YAML_AVAILABLE:
        return {}
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_open_items_stats(open_items_path: Path, month_start: date, month_end: date) -> dict:
    """Compute created, completed, overdue counts for open items in month range."""
    if not open_items_path.exists():
        return {"created": 0, "completed": 0, "overdue": 0, "active": 0}

    if not _YAML_AVAILABLE:
        return {"created": 0, "completed": 0, "overdue": 0, "active": 0}

    content = open_items_path.read_text(encoding="utf-8")
    created = completed = overdue = active = 0
    today = date.today()

    item_blocks = re.split(r"\n(?=- id:)", content)
    for block in item_blocks:
        block = block.strip()
        if not block.startswith("- id:"):
            continue
        block_stripped = re.sub(r"^- ", "", block)
        try:
            item = yaml.safe_load(block_stripped)
            if not isinstance(item, dict):
                continue

            date_added_str = str(item.get("date_added", "") or "")
            deadline_str = str(item.get("deadline", "") or "")
            status = str(item.get("status", "open"))

            try:
                da = date.fromisoformat(date_added_str) if date_added_str else None
            except ValueError:
                da = None

            if da and month_start <= da <= month_end:
                created += 1
                if status == "closed" or status == "done":
                    completed += 1

            if status == "open":
                active += 1
                try:
                    if deadline_str and date.fromisoformat(deadline_str) < today:
                        overdue += 1
                except ValueError:
                    pass
        except Exception:
            pass

    return {"created": created, "completed": completed, "overdue": overdue, "active": active}


def _domain_activity(state_dir: Path, month_start: date, month_end: date) -> list[tuple[str, str]]:
    """List domains updated during the month (by last_updated timestamp)."""
    updated = []
    for state_file in sorted(state_dir.glob("*.md")):
        if state_file.name.startswith("."):
            continue
        fm = _load_frontmatter(state_file)
        last_updated = str(fm.get("last_updated", "") or "")
        domain = str(fm.get("domain", state_file.stem))
        try:
            lu = date.fromisoformat(last_updated[:10]) if last_updated else None
        except ValueError:
            lu = None
        if lu and month_start <= lu <= month_end:
            updated.append((domain, last_updated[:10]))

    return updated


def _goals_summary(goals_path: Path) -> str:
    """Return a brief goals status line."""
    if not goals_path.exists():
        return ""
    content = goals_path.read_text(encoding="utf-8")
    # Count emoji status indicators
    in_progress = content.count("🟡 In Progress")
    completed = content.count("🟢") + content.count("✅")
    not_started = content.count("🔴 Not Started")
    return f"{in_progress} in progress · {completed} completed · {not_started} not started"


def _decisions_resolved(decisions_path: Path, month_start: date, month_end: date) -> list[str]:
    """Return list of decision titles resolved/archived during the month."""
    if not decisions_path.exists() or not _YAML_AVAILABLE:
        return []
    content = decisions_path.read_text(encoding="utf-8")
    resolved = []
    # Parse archive table rows
    in_archive = False
    for line in content.splitlines():
        if "## Recent Decisions" in line or "Archive" in line:
            in_archive = True
            continue
        if line.startswith("##") and in_archive:
            break
        if in_archive and line.startswith("|") and "---" not in line:
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 3 and parts[0] and parts[0].lower() not in ("decision", ""):
                decision_title = parts[0]
                date_str = parts[1] if len(parts) > 1 else ""
                try:
                    dm = date.fromisoformat(date_str[:10]) if date_str else None
                    if dm and month_start <= dm <= month_end:
                        resolved.append(decision_title)
                except ValueError:
                    pass

    return resolved


def _memory_facts_this_month(memory_path: Path, month_start: date, month_end: date) -> list[str]:
    """Return statements of facts added during this month."""
    fm = _load_frontmatter(memory_path)
    if not isinstance(fm, dict):
        return []
    facts = fm.get("facts", [])
    if not isinstance(facts, list):
        return []

    this_month: list[str] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        date_added = str(fact.get("date_added", "") or "")
        try:
            da = date.fromisoformat(str(date_added)[:10]) if date_added else None
        except ValueError:
            da = None
        if da and month_start <= da <= month_end:
            stmt = str(fact.get("statement", ""))
            if stmt:
                this_month.append(stmt[:100])

    return this_month[:8]  # Cap at 8


def _upcoming_deadlines(open_items_path: Path, from_date: date, days: int = 30) -> list[str]:
    """Return top upcoming deadlines in the next N days."""
    if not open_items_path.exists() or not _YAML_AVAILABLE:
        return []

    content = open_items_path.read_text(encoding="utf-8")
    upcoming = []
    cutoff = from_date
    horizon = date(from_date.year + (1 if from_date.month == 12 else 0),
                   (from_date.month % 12) + 1, 1)

    item_blocks = re.split(r"\n(?=- id:)", content)
    for block in item_blocks:
        block = block.strip()
        if not block.startswith("- id:"):
            continue
        block_stripped = re.sub(r"^- ", "", block)
        try:
            item = yaml.safe_load(block_stripped)
            if not isinstance(item, dict) or item.get("status") != "open":
                continue
            deadline_str = str(item.get("deadline", "") or "")
            if not deadline_str:
                continue
            dl = date.fromisoformat(deadline_str)
            if cutoff <= dl <= horizon:
                desc = str(item.get("description", "")).split("\n")[0][:60]
                upcoming.append(f"{dl.strftime('%b %d')}: {desc}")
        except (ValueError, Exception):
            pass

    return sorted(upcoming)[:5]


# --- Main generator ---------------------------------------------------------

class RetrospectiveView:
    """Generates monthly retrospective reports."""

    def generate(
        self,
        state_dir: Path,
        summaries_dir: Path,
        month: str,
        health_check: dict,
    ) -> str:
        """Generate monthly retrospective for the given YYYY-MM month.

        Returns the retrospective text as a Markdown string.
        """
        # Parse month
        try:
            month_start = date.fromisoformat(f"{month}-01")
            # End of month = first day of next month - 1 day
            next_month_year = month_start.year + (1 if month_start.month == 12 else 0)
            next_month = (month_start.month % 12) + 1
            month_end = date(next_month_year, next_month, 1)
        except ValueError:
            return f"❌ Invalid month format: {month} (expected YYYY-MM)"

        month_label = month_start.strftime("%B %Y")

        # Check catch-up run count for this month
        catchup_runs = health_check.get("catch_up_runs", []) or []
        runs_this_month = [
            r for r in catchup_runs
            if isinstance(r, dict)
            and str(r.get("date", ""))[:7] == month
        ]
        if len(runs_this_month) < _MIN_CATCHUP_RUNS:
            return (
                f"# Monthly Retrospective — {month_label}\n\n"
                f"⚠️ *Insufficient data: {len(runs_this_month)} catch-up run(s) "
                f"in {month_label}. Minimum {_MIN_CATCHUP_RUNS} required.*\n\n"
                "Run more catch-ups during the month for a meaningful retrospective."
            )

        # Gather data
        items_stats = _parse_open_items_stats(
            state_dir / "open_items.md", month_start, month_end
        )
        domain_updates = _domain_activity(state_dir, month_start, month_end)
        goals_line = _goals_summary(state_dir / "goals.md")
        resolved_decisions = _decisions_resolved(
            state_dir / "decisions.md", month_start, month_end
        )
        memory_insights = _memory_facts_this_month(
            state_dir / "memory.md", month_start, month_end
        )
        next_deadlines = _upcoming_deadlines(state_dir / "open_items.md", month_end)

        # Build retrospective
        lines = [
            f"# Monthly Retrospective — {month_label}",
            f"_Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}_",
            "",
            "---",
            "",
            "## 📊 Month at a Glance",
            "",
            f"- **Catch-up runs:** {len(runs_this_month)}",
            f"- **Domains updated:** {len(domain_updates)}",
            f"- **Open items created:** {items_stats['created']}",
            f"- **Items completed:** {items_stats['completed']}",
            f"- **Currently active items:** {items_stats['active']}",
            f"- **Overdue items:** {items_stats['overdue']}",
        ]

        if goals_line:
            lines += ["", "## 🎯 Goals Progress", "", goals_line]

        if domain_updates:
            lines += ["", "## 📁 Domain Activity", ""]
            for domain, updated in sorted(domain_updates, key=lambda x: x[1], reverse=True)[:8]:
                lines.append(f"- **{domain}** — updated {updated}")

        if resolved_decisions:
            lines += ["", "## 📋 Decisions Made", ""]
            for d in resolved_decisions[:5]:
                lines.append(f"- {d}")
        else:
            lines += ["", "## 📋 Decisions Made", "", "_No decisions recorded this month._"]

        lines += ["", "## 📈 Open Items Velocity", ""]
        lines.append(
            f"Created: **{items_stats['created']}** | "
            f"Completed: **{items_stats['completed']}** | "
            f"Aged (overdue): **{items_stats['overdue']}**"
        )
        if items_stats["created"] > 0:
            completion_rate = (
                items_stats["completed"] / items_stats["created"] * 100
            )
            lines.append(f"\nMonth completion rate: {completion_rate:.0f}%")

        if memory_insights:
            lines += ["", "## 🧠 Pattern Insights (from memory)", ""]
            for insight in memory_insights:
                lines.append(f"- {insight}")

        if next_deadlines:
            lines += ["", "## 🔭 Next Month Preview", ""]
            for deadline in next_deadlines:
                lines.append(f"- {deadline}")
        else:
            lines += [
                "", "## 🔭 Next Month Preview",
                "", "_No upcoming deadlines in the next 30 days._",
            ]

        lines += ["", "---", f"_Retrospective for {month_label} | Artha v1.3.0_"]

        return "\n".join(lines)

    def save(self, content: str, month: str, summaries_dir: Path) -> Path:
        """Save retrospective to summaries/YYYY-MM-retro.md."""
        summaries_dir.mkdir(parents=True, exist_ok=True)
        retro_path = summaries_dir / f"{month}-retro.md"
        retro_path.write_text(content, encoding="utf-8")
        return retro_path


# --- CLI entry point --------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monthly retrospective generator")
    parser.add_argument(
        "--month",
        default=None,
        help="Month to generate for (YYYY-MM). Defaults to previous month.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print to stdout instead of saving to file.",
    )
    args = parser.parse_args(argv)

    if not _load_flag("enhancements.monthly_retrospective", default=True):
        print("ℹ️  Monthly retrospective is disabled.")
        return 0

    # Default: previous month
    if args.month is None:
        today = date.today()
        if today.month == 1:
            prev_year, prev_month = today.year - 1, 12
        else:
            prev_year, prev_month = today.year, today.month - 1
        args.month = f"{prev_year}-{prev_month:02d}"

    # Load health-check data
    health_path = _STATE_DIR / "health-check.md"
    health_data: dict = {}
    if health_path.exists() and _YAML_AVAILABLE:
        content = health_path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if m:
            try:
                health_data = yaml.safe_load(m.group(1)) or {}
            except Exception:
                health_data = {}

    view = RetrospectiveView()
    text = view.generate(
        state_dir=_STATE_DIR,
        summaries_dir=_SUMMARIES_DIR,
        month=args.month,
        health_check=health_data,
    )

    if args.dry_run:
        print(text)
        return 0

    saved_path = view.save(text, args.month, _SUMMARIES_DIR)
    print(f"✅ Retrospective saved to {saved_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
