#!/usr/bin/env python3
"""
scripts/career_search_view.py — Terminal pipeline viewer for career-ops (§12).

Reads state/career_search.md and renders a concise terminal summary:
  - Campaign status header (goal, start date, target)
  - Applications by status with velocity metrics (apps/week vs target)
  - Pending Pipeline matches (from ## Pipeline section)
  - Story bank count

Usage:
    python scripts/career_search_view.py
    python scripts/career_search_view.py --json   # machine-readable output

Ref: specs/career-ops.md §12.3, FR-CS-7
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_STATE_FILE = _REPO_ROOT / "state" / "career_search.md"
_AUDIT_FILE = _REPO_ROOT / "state" / "career_audit.jsonl"
_HEARTBEAT = _REPO_ROOT / "tmp" / "career_last_run.json"

# Velocity emoji thresholds (§12.3)
_VELOCITY_GREEN = 1.0   # rate/target >= 1.0
_VELOCITY_YELLOW = 0.7  # rate/target >= 0.7


def _velocity_emoji(rate: float, target: float) -> str:
    if target <= 0:
        return "⚪"
    ratio = rate / target
    if ratio >= _VELOCITY_GREEN:
        return "🟢"
    if ratio >= _VELOCITY_YELLOW:
        return "🟡"
    return "🔴"


def _weeks_since(date_str: str) -> float:
    """Return float weeks since a date string (YYYY-MM-DD or ISO)."""
    try:
        started = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - started).total_seconds()
        weeks = delta / (7 * 24 * 3600)
        return max(weeks, 0.001)  # avoid div-by-zero
    except (ValueError, TypeError):
        return 1.0


def _count_story_bank(content: str) -> int:
    """Count STAR story entries in ## Story Bank section."""
    if "## Story Bank" not in content:
        return 0
    story_section = content.split("## Story Bank", 1)[1]
    # Stories start with ### or **Story N:**
    return len(re.findall(r"^###\s+|^\*\*Story\s+\d+", story_section, re.MULTILINE))


def _parse_pipeline_pending(content: str) -> list[dict]:
    """Extract unchecked Pipeline entries from ## Pipeline section."""
    if "## Pipeline" not in content:
        return []

    pipeline_section = content.split("## Pipeline", 1)[1]
    # Stop at next ## section
    next_sec = re.search(r"\n##\s+", pipeline_section)
    if next_sec:
        pipeline_section = pipeline_section[: next_sec.start()]

    pending = []
    # Match metadata comments
    for m in re.finditer(r"<!-- PORTAL-MATCH: ({[^}]+}) -->", pipeline_section):
        try:
            meta = json.loads(m.group(1))
            pending.append(meta)
        except json.JSONDecodeError:
            pass

    # Also match plain unchecked checkboxes without metadata
    for m in re.finditer(r"^- \[ \] (.+)$", pipeline_section, re.MULTILINE):
        # Only include if not already captured by metadata
        line_text = m.group(1)
        if not any(p.get("role", "") in line_text or p.get("company", "") in line_text
                   for p in pending):
            pending.append({"raw": line_text})

    return pending


def _load_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter between --- delimiters."""
    try:
        import yaml  # noqa: PLC0415
        m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if m:
            return yaml.safe_load(m.group(1)) or {}
    except Exception:
        pass
    return {}


def _load_last_scan() -> dict | None:
    """Load last scanner heartbeat."""
    try:
        return json.loads(_HEARTBEAT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def render(output_json: bool = False) -> int:
    """Render the career pipeline view. Returns exit code."""
    if not _STATE_FILE.exists():
        encrypted = _STATE_FILE.with_suffix(".md.age")
        if encrypted.exists():
            print("🔒 state/career_search.md is encrypted. Decrypt with vault to view.")
        else:
            print("⚠ state/career_search.md not found. Run /career eval <URL> to start.")
        return 1

    try:
        content = _STATE_FILE.read_text(encoding="utf-8")
    except OSError as e:
        print(f"⛔ Cannot read career_search.md: {e}", file=sys.stderr)
        return 1

    fm = _load_frontmatter(content)
    campaign = fm.get("campaign", {}) or {}
    summary = fm.get("summary", {}) or {}

    status = campaign.get("status", "unknown")
    started = campaign.get("started", "")
    goal_ref = campaign.get("goal_ref", "")
    tag_line = campaign.get("tag_line", "")

    by_status: dict = summary.get("by_status", {}) or {}
    total_applied = sum(v for v in by_status.values() if isinstance(v, int))
    avg_score = summary.get("average_score")

    # Velocity: apps/week (target: 2/week per spec default)
    weekly_target = fm.get("scoring_weights", {}).get("weekly_app_target", 2) if fm.get("scoring_weights") else 2
    weeks = _weeks_since(started) if started else 1.0
    apps_per_week = total_applied / weeks
    v_emoji = _velocity_emoji(apps_per_week, weekly_target)

    pending_pipeline = _parse_pipeline_pending(content)
    story_count = _count_story_bank(content)
    last_scan = _load_last_scan()

    if output_json:
        out = {
            "campaign": {"status": status, "started": started, "goal_ref": goal_ref},
            "applications": {"total": total_applied, "by_status": by_status, "avg_score": avg_score},
            "velocity": {"apps_per_week": round(apps_per_week, 2), "target": weekly_target},
            "pipeline": {"pending": len(pending_pipeline)},
            "story_bank": {"count": story_count},
            "last_scan": last_scan,
        }
        print(json.dumps(out, indent=2))
        return 0

    # Terminal render
    width = 60
    print("─" * width)
    print(f"  🎯 Career Campaign — {tag_line or status.upper()}")
    if started:
        print(f"     Started: {started}  ·  Goal: {goal_ref or 'none'}")
    print("─" * width)

    # Applications table
    print(f"\n  📋 Applications  (total: {total_applied})")
    status_order = ["Evaluated", "PartialEval", "Applied", "Responded", "Interview", "Offer",
                    "SKIP", "Rejected", "Discarded"]
    for s in status_order:
        count = by_status.get(s, 0)
        if count:
            bar = "█" * min(count, 20)
            print(f"     {s:<14} {bar} {count}")

    # Average score
    if avg_score is not None:
        score_bar = "★" * round(avg_score)
        print(f"\n  ⭐ Avg score: {avg_score:.1f}/7  {score_bar}")

    # Velocity
    print(f"\n  📈 Velocity: {apps_per_week:.1f} apps/week  {v_emoji}  (target: {weekly_target}/week)")

    # Pipeline
    print(f"\n  🔍 Pipeline — {len(pending_pipeline)} pending match(es)")
    for i, match in enumerate(pending_pipeline[:5], 1):
        if "raw" in match:
            print(f"     {i}. {match['raw'][:70]}")
        else:
            company = match.get("company", "?")
            role = match.get("role", "?")
            portal = match.get("portal", "")
            discovered = match.get("discovered", "")[:10]
            print(f"     {i}. {company} — {role[:40]}  [{portal} · {discovered}]")
    if len(pending_pipeline) > 5:
        print(f"     … and {len(pending_pipeline) - 5} more")

    # Story bank
    print(f"\n  📖 Story Bank: {story_count} STAR stories")

    # Last scan
    if last_scan:
        ts = last_scan.get("timestamp_utc", "")[:16]
        scan_status = last_scan.get("status", "unknown")
        new_matches = last_scan.get("records_written", 0)
        print(f"\n  🔄 Last scan: {ts} UTC  ·  {scan_status}  ·  {new_matches} new match(es)")

    print("─" * width)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="career_search_view.py",
        description="Career pipeline terminal view",
    )
    p.add_argument("--json", action="store_true", dest="output_json",
                   help="Output machine-readable JSON")
    args = p.parse_args(argv)
    return render(output_json=args.output_json)


if __name__ == "__main__":
    sys.exit(main())
