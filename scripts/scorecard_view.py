#!/usr/bin/env python3
"""
scorecard_view.py — Script-backed /scorecard command renderer
=============================================================
Reads state/health-check.md and state/goals.md (both plaintext).
Produces a weekly life-quality scorecard across system + goal dimensions.
health-metrics.md is included if the vault is unlocked; gracefully skipped
if encrypted.

Usage:
  python scripts/scorecard_view.py
  python scripts/scorecard_view.py --format flash
  python scripts/scorecard_view.py --format standard   (default)
  python scripts/scorecard_view.py --format digest

Output: Markdown formatted weekly scorecard.

Exit codes:
  0 — success
  1 — insufficient data (both health-check.md and goals.md missing)

Ref: specs/enhance.md §1.13 / §10.0.1
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"
_LOCK_FILE = _ARTHA_DIR / ".artha-decrypted"

_HEALTH_FILE = _STATE_DIR / "health-check.md"
_GOALS_FILE = _STATE_DIR / "goals.md"
_METRICS_FILE = _STATE_DIR / "health-metrics.md"
_OPEN_ITEMS_FILE = _STATE_DIR / "open_items.md"

try:
    from work.reflect_reader import ReflectReader as _ReflectReader
    _REFLECT_READER_AVAILABLE = True
except ImportError:
    _ReflectReader = None  # type: ignore[assignment,misc]
    _REFLECT_READER_AVAILABLE = False

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]

_WORK_STATE_DIR = _ARTHA_DIR / "state" / "work"

_STATUS_ICONS = {
    "on_track": "\U0001f7e2", "in_progress": "\U0001f7e2", "at_risk": "\U0001f7e1",
    "urgent": "\U0001f7e0", "critical": "\U0001f534", "not_started": "\u2b1c",
    "done": "\u2705", "paused": "\u23f8",
}

_SCORE_ICONS = ["🔴", "🟠", "🟡", "🟢", "✅"]  # 1–5


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _is_vault_unlocked() -> bool:
    return _LOCK_FILE.exists()


def _score_bar(score: float, max_score: float = 5.0, width: int = 10) -> str:
    """Render a filled progress bar scaled to max_score."""
    pct = min(max(score / max_score, 0.0), 1.0)
    filled = round(pct * width)
    return f"{'█' * filled}{'░' * (width - filled)}"


def _extract_run_metrics(health_content: str, n: int = 7) -> list[dict]:
    """Extract the last n catch-up runs for weekly stats."""
    runs = []
    history_m = re.search(r"catch_up_runs:\n(.*?)(?:\n##|\Z)", health_content, re.DOTALL)
    if not history_m:
        return runs
    block = history_m.group(1)
    current: dict = {}
    for line in block.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- timestamp:"):
            if current:
                runs.append(current)
                if len(runs) >= n:
                    break
            current = {"timestamp": stripped.split(":", 1)[1].strip().strip("'\"")}
        elif current and ":" in stripped and not stripped.startswith("#"):
            k, _, v = stripped.partition(":")
            current[k.strip()] = v.strip().strip("'\"")
    if current and len(runs) < n:
        runs.append(current)
    return runs


def _parse_goals_yaml(content: str) -> list[dict]:
    """Parse the `goals` list from YAML frontmatter (schema v2.0).

    Falls back to empty list if `goals` key absent (schema v1.0 files).
    Extracts only the frontmatter block first to avoid Markdown table pipes
    being misinterpreted as YAML block scalars.
    """
    if _yaml is None:
        return []
    try:
        import re as _re
        fm_m = _re.match(r"---\s*\n(.*?)\n---", content, _re.DOTALL)
        if not fm_m:
            return []
        fm = _yaml.safe_load(fm_m.group(1)) or {}
        raw = fm.get("goals", [])
        if isinstance(raw, list):
            return [g for g in raw if isinstance(g, dict)]
    except Exception:  # noqa: BLE001
        pass
    return []


# Legacy alias — scorecard rendering call-sites use _parse_goals_index
_parse_goals_index = _parse_goals_yaml


def _count_open_items(items_content: str) -> dict:
    """Count open items by priority."""
    counts: dict = {"P0": 0, "P1": 0, "P2": 0, "total": 0}
    for m in re.finditer(r"status:\s*open", items_content):
        counts["total"] += 1
    for priority in ("P0", "P1", "P2"):
        counts[priority] = len(re.findall(
            rf"priority:\s*{priority}.*?status:\s*open|status:\s*open.*?priority:\s*{priority}",
            items_content[:items_content.rfind("status: open") + 1] if "status: open" in items_content else "",
            re.DOTALL
        ))
    # Simpler approach: scan item blocks
    counts = {"P0": 0, "P1": 0, "P2": 0, "total": 0}
    blocks = re.split(r"\n(?=- id: OI-)", items_content)
    for block in blocks:
        if "status: open" in block:
            counts["total"] += 1
            for p in ("P0", "P1", "P2"):
                if f"priority: {p}" in block:
                    counts[p] += 1
                    break
    return counts


def _compute_system_score(runs: list[dict]) -> tuple[float, str]:
    """Score system health 1–5 based on recent run quality."""
    if not runs:
        return 2.0, "insufficient data"
    latest = runs[0]
    pressure = latest.get("context_pressure", "green").lower()
    preflight = latest.get("preflight", "pass").lower()
    todo_sync = latest.get("todo_sync", "ok").lower()
    mode = latest.get("session_mode", "normal").lower()

    score = 5.0
    if pressure == "yellow":
        score -= 0.5
    elif pressure == "red":
        score -= 1.5
    elif pressure == "critical":
        score -= 2.0
    if preflight in ("warn",):
        score -= 0.5
    elif preflight == "fail":
        score -= 1.5
    if todo_sync == "failed":
        score -= 0.5
    if mode == "degraded":
        score -= 0.5
    elif mode == "offline":
        score -= 1.0

    score = max(1.0, min(5.0, score))
    note = f"last run: {latest.get('timestamp', '?')[:10]}"
    return score, note


def _compute_goals_score(goals: list[dict]) -> tuple[float, str]:
    """Score goal health 1–5 based on status distribution."""
    if not goals:
        return 3.0, "no goals defined"
    total = len(goals)
    status_counts: dict[str, int] = {}
    for g in goals:
        s = g.get("status", "not_started")
        status_counts[s] = status_counts.get(s, 0) + 1
    critical = status_counts.get("critical", 0)
    urgent = status_counts.get("urgent", 0)
    at_risk = status_counts.get("at_risk", 0)
    on_track = status_counts.get("on_track", 0) + status_counts.get("in_progress", 0)

    score = 5.0
    score -= (critical * 1.2)
    score -= (urgent * 0.7)
    score -= (at_risk * 0.4)
    score = max(1.0, min(5.0, score))
    note = f"{on_track}/{total} on track"
    return score, note


def _compute_items_score(item_counts: dict) -> tuple[float, str]:
    """Score open-items backlog 1–5 (lower items = higher score)."""
    total = item_counts.get("total", 0)
    p0 = item_counts.get("P0", 0)
    score = 5.0
    score -= p0 * 1.0
    score -= max(0, (total - 5)) * 0.2
    score = max(1.0, min(5.0, score))
    note = f"{total} open ({p0} P0)"
    return score, note


def _extract_health_signal(metrics_content: str) -> tuple[float, str]:
    """Extract a health score proxy from health-metrics.md if available."""
    if not metrics_content:
        return 3.0, "vault locked or no data"
    # Look for Weight trend, VO2 Max, HRV as proxy signals
    signals = []
    for pattern, good_fn in (
        (r"Weight.*?\*\*([\d.]+) lbs\*\*.*?goal.*?(\d+) lbs", None),
        (r"VO2 Max.*?\*\*([\d.]+)\*\*.*?>(\d+)", None),
    ):
        m = re.search(pattern, metrics_content)
        if m:
            signals.append("data found")
    score = 4.0 if signals else 3.0
    note = "from health-metrics.md" if signals else "no recent metrics"
    return score, note


def _fetch_reflection_history(n: int = 4) -> list:
    """Fetch recent weekly reflection history via ReflectReader."""
    if not _REFLECT_READER_AVAILABLE:
        return []
    try:
        return _ReflectReader(_WORK_STATE_DIR).get_weekly_history(last_n=n)  # type: ignore[misc]
    except Exception:
        return []


def _compute_reflection_score(history: list) -> tuple[float, str]:
    """Score Work Reflection health 1-5 from weekly summary list."""
    if not history:
        return 2.0, "no reflection data"
    scored = [s for s in history if getattr(s, "focus_score", None) is not None]
    if not scored:
        return 3.0, f"{len(history)} week(s) tracked (unscored)"
    avg = sum(s.focus_score for s in scored) / len(scored)
    score = 1.0 + avg * 4.0
    score = max(1.0, min(5.0, score))
    note = f"{len(history)} week(s) \u00b7 avg focus {avg:.0%}"
    return score, note


def _score_to_icon(score: float) -> str:
    idx = min(int(score) - 1, 4)
    return _SCORE_ICONS[max(0, idx)]


def _format_flash(dimensions: list[tuple]) -> str:
    avg = sum(s for _, s, _, _ in dimensions) / len(dimensions) if dimensions else 0
    overall_icon = _score_to_icon(avg)
    lines = [
        "## Life Scorecard — Flash",
        f"Overall: {overall_icon} **{avg:.1f}/5.0**\n",
    ]
    for name, score, note, _ in dimensions:
        icon = _score_to_icon(score)
        lines.append(f"- {icon} **{name}**: {score:.1f}/5 · {note}")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_standard(dimensions: list[tuple], runs: list[dict], goals: list[dict]) -> str:
    avg = sum(s for _, s, _, _ in dimensions) / len(dimensions) if dimensions else 0
    overall_icon = _score_to_icon(avg)

    lines = [
        "## Weekly Life Scorecard",
        f"**Overall: {overall_icon} {avg:.1f}/5.0** · {date.today().strftime('%B %d, %Y')}",
        "",
        "| Dimension | Score | Bar | Notes |",
        "|-----------|-------|-----|-------|",
    ]
    for name, score, note, _ in dimensions:
        icon = _score_to_icon(score)
        bar = _score_bar(score)
        lines.append(f"| {icon} {name} | {score:.1f}/5 | {bar} | {note} |")

    # Weekly activity summary
    if runs:
        total_emails = sum(int(r.get("emails_processed", 0)) for r in runs if r.get("emails_processed", "0").isdigit())
        total_alerts = sum(int(r.get("alerts_generated", 0)) for r in runs if r.get("alerts_generated", "0").isdigit())
        lines += [
            "",
            "### Weekly Activity (last 7 catch-ups)",
            f"- Emails processed: {total_emails}",
            f"- Alerts generated: {total_alerts}",
            f"- Catch-up sessions: {len(runs)}",
        ]

    # Goals at risk
    at_risk = [g for g in goals if g.get("status") in ("critical", "urgent", "at_risk")]
    if at_risk:
        lines += ["", "### Goals Needing Attention"]
        for g in at_risk:
            icon = _STATUS_ICONS.get(g.get("status", ""), "⬜")
            lines.append(f"- {icon} {g.get('id', '?')} — {g.get('title', '?')}")

    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_digest(
    dimensions: list[tuple],
    runs: list[dict],
    goals: list[dict],
    reflect_history: list | None = None,
) -> str:
    """Digest: standard + trend analysis + full goal list + reflection trend."""
    standard = _format_standard(dimensions, runs, goals)
    lines = [standard, "\n---\n", "### Full Goal Status"]
    for g in sorted(goals, key=lambda x: (
        {"P0": 0, "P1": 1, "P2": 2}.get(x.get("priority", "P2"), 3),
    )):
        icon = _STATUS_ICONS.get(g.get("status", "not_started"), "⬜")
        priority = g.get("priority", "?")
        owner = g.get("owner", "?")
        deadline = g.get("deadline") or "no deadline"
        lines.append(
            f"- {icon} **{g.get('id', '?')}** [{priority}] {g.get('title', '?')} "
            f"· {owner} · {deadline}"
        )

    # Run trend
    if len(runs) >= 3:
        lines += ["", "### Catch-Up Trend (last 7)"]
        lines.append("| Date | Emails | Alerts | Pressure | Mode |")
        lines.append("|------|--------|--------|----------|------|")
        for r in runs:
            ts = r.get("timestamp", "?")[:10]
            emails = r.get("emails_processed", "?")
            alerts = r.get("alerts_generated", "?")
            pressure = r.get("context_pressure", "?")
            mode = r.get("session_mode", "normal")
            lines.append(f"| {ts} | {emails} | {alerts} | {pressure} | {mode} |")

    # Reflection trend table (Sprint 2 integration)
    if reflect_history:
        lines += ["", "### Work Reflection Trend"]
        lines.append("| Week | Theme | Carry-Forward | Focus Score |")
        lines.append("|------|-------|---------------|-------------|")
        for snap in reflect_history[:8]:
            week = getattr(snap, "week_key", "?")
            theme = str(getattr(snap, "primary_theme", "") or "")[:35]
            cf = getattr(snap, "carry_forward_count", "?")
            fs = getattr(snap, "focus_score", None)
            fs_str = f"{fs:.0%}" if fs is not None else "\u2014"
            lines.append(f"| {week} | {theme} | {cf} | {fs_str} |")

    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Artha Scorecard Viewer")
    parser.add_argument(
        "--format", choices=["flash", "standard", "digest"], default="standard",
        help="Output density (default: standard)"
    )
    args = parser.parse_args()

    health_content = _read(_HEALTH_FILE)
    goals_content = _read(_GOALS_FILE)
    items_content = _read(_OPEN_ITEMS_FILE)
    metrics_content = _read(_METRICS_FILE) if _is_vault_unlocked() else ""

    if not health_content and not goals_content:
        print(
            "⚠ No state data found. Run a catch-up first to populate health-check.md and goals.md.",
            file=sys.stderr,
        )
        return 1

    runs = _extract_run_metrics(health_content, n=7)
    goals = _parse_goals_index(goals_content)
    item_counts = _count_open_items(items_content)

    system_score, system_note = _compute_system_score(runs)
    goals_score, goals_note = _compute_goals_score(goals)
    items_score, items_note = _compute_items_score(item_counts)
    health_score, health_note = _extract_health_signal(metrics_content)

    # Weekly catch-up cadence score (how many sessions in last 7 days)
    cadence_score = min(5.0, max(1.0, len(runs) * 1.2)) if runs else 1.0
    cadence_note = f"{len(runs)} session(s) this week"

    reflect_history = _fetch_reflection_history(n=4)
    reflect_score, reflect_note = _compute_reflection_score(reflect_history)

    dimensions = [
        ("System Health", system_score, system_note, "health-check.md"),
        ("Goals Progress", goals_score, goals_note, "goals.md"),
        ("Action Backlog", items_score, items_note, "open_items.md"),
        ("Physical Health", health_score, health_note, "health-metrics.md"),
        ("Engagement Cadence", cadence_score, cadence_note, "catch-up frequency"),
        ("Work Reflection", reflect_score, reflect_note, "state/work/"),
    ]

    if args.format == "flash":
        print(_format_flash(dimensions))
    elif args.format == "digest":
        print(_format_digest(dimensions, runs, goals, reflect_history=reflect_history))
    else:
        print(_format_standard(dimensions, runs, goals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
