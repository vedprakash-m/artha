"""scripts/narrative/reflect.py — Narrative templates for the Reflection Loop.

Provides four generate_* functions (daily, weekly, monthly, quarterly) that
follow the NarrativeEngineBase pattern used by all other narrative templates.

Design contract (specs/reflection-loop.md §3.4, §6):
  Each function accepts `base: NarrativeEngineBase` and returns a str.
  These functions are NOT called from _execute_reflection() directly —
  they are available for post-processing / enriched display of reflection
  artifacts.  The core pipeline (reflect.py) writes plain-Markdown Tier 1/2
  content; these templates produce presentation-layer summaries.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("artha.narrative.reflect")

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_WORK_STATE_DIR = _REPO_ROOT / "state" / "work"
_REFLECTIONS_DIR = _WORK_STATE_DIR / "reflections"

try:
    from work.reflect_reader import ReflectReader as _ReflectReader
    _REFLECT_READER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ReflectReader = None  # type: ignore[assignment,misc]
    _REFLECT_READER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_reflect_current(state_dir: Path) -> dict[str, Any]:
    """Return frontmatter dict from reflect-current.md via ReflectReader."""
    if _REFLECT_READER_AVAILABLE:
        return _ReflectReader(state_dir).get_current_frontmatter()  # type: ignore[misc]
    # Fallback: direct read (legacy environments without reflect_reader on path)
    current_path = state_dir / "reflect-current.md"
    if not current_path.exists():
        return {}
    try:
        import yaml
        text = current_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


def _read_reflect_body(state_dir: Path) -> str:
    """Return body text from reflect-current.md via ReflectReader."""
    if _REFLECT_READER_AVAILABLE:
        snap = _ReflectReader(state_dir).get_current_reflection()  # type: ignore[misc]
        if snap is None:
            return ""
        raw = snap.raw_markdown
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return raw.strip()
    # Fallback: direct read
    current_path = state_dir / "reflect-current.md"
    if not current_path.exists():
        return ""
    try:
        text = current_path.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text.strip()
    except Exception:
        return ""


def _latest_artifact(horizon: str, state_dir: Path) -> dict[str, Any]:
    """Return frontmatter + body of the most recent Tier 2 artifact via ReflectReader."""
    if _REFLECT_READER_AVAILABLE:
        result = _ReflectReader(state_dir).get_artifact_content(horizon)  # type: ignore[misc]
        return result if result is not None else {}
    # Fallback: direct read
    artifact_dir = state_dir / "reflections" / horizon
    if not artifact_dir.exists():
        return {}
    candidates = sorted(artifact_dir.glob("*.md"), reverse=True)
    if not candidates:
        return {}
    try:
        import yaml
        text = candidates[0].read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}
            return {}
        fm = yaml.safe_load(parts[1]) or {}
        fm["_body"] = parts[2].strip()
        return fm
    except Exception:
        return {}


def _impact_badge(impact_summary: str) -> str:
    """Convert 'N HIGH, M MEDIUM, K LOW' into a compact badge line."""
    if not impact_summary:
        return ""
    parts = []
    for segment in impact_summary.split(","):
        segment = segment.strip()
        if segment:
            parts.append(segment)
    return " · ".join(parts)


def _age_since(ts_str: str) -> str:
    """Return human-readable age since an ISO timestamp."""
    if not ts_str:
        return "unknown"
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        hrs = delta.seconds // 3600
        return f"{hrs}h ago"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# generate_daily_close
# ---------------------------------------------------------------------------

def generate_daily_close(base: Any) -> str:
    """Generate a daily close summary for the current working day.

    Reads reflect-current.md and formats a concise day-end summary.
    Suitable for the 'daily' horizon in /work reflect display.
    """
    state_dir = getattr(base, "state_dir", _WORK_STATE_DIR)
    fm = _read_reflect_current(state_dir)
    body = _read_reflect_body(state_dir)

    if not fm:
        return (
            "## Daily Close\n\n"
            "_No reflection data found. Run `/work reflect daily` to generate._"
        )

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %B %d %Y")

    last_close = fm.get("last_daily_close", "")
    cf_count = fm.get("carry_forward_count", 0)

    lines = [
        f"## Daily Close — {date_str}",
        "",
        f"Last daily close: {_age_since(str(last_close)) if last_close else 'never'}",
        f"Carry-forward items: {cf_count}",
        "",
    ]

    if body:
        # Extract Accomplishments section
        acc_lines = []
        in_accomplishments = False
        for line in body.splitlines():
            if line.startswith("### Accomplishments"):
                in_accomplishments = True
                acc_lines.append(line)
                continue
            if in_accomplishments and line.startswith("###"):
                break
            if in_accomplishments:
                acc_lines.append(line)

        if acc_lines:
            lines += acc_lines + [""]
        else:
            lines += ["### Accomplishments", "_(pending — run `/work reflect daily`)_", ""]

    lines += [base._freshness_footer() if hasattr(base, "_freshness_footer") else ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_weekly_reflection
# ---------------------------------------------------------------------------

def generate_weekly_reflection(base: Any) -> str:
    """Generate a weekly reflection summary from the most recent weekly artifact.

    Reads both reflect-current.md and the latest weekly Tier 2 artifact.
    Includes impact distribution, carry-forward table, and plan vs actual.
    """
    state_dir = getattr(base, "state_dir", _WORK_STATE_DIR)
    fm = _read_reflect_current(state_dir)
    weekly = _latest_artifact("weekly", state_dir)

    now = datetime.now(timezone.utc)
    y, w, _ = now.isocalendar()
    week_label = fm.get("current_week") or f"{y}-W{w:02d}"
    cf_count = fm.get("carry_forward_count", 0)

    impact = weekly.get("impact_summary", "")
    acc_count = weekly.get("accomplishment_count", "?")
    period = weekly.get("period", week_label)
    created = weekly.get("created", "")

    lines = [
        f"## Weekly Reflection — {period}",
        "",
        f"_Generated: {_age_since(str(created)) if created else 'not yet generated'}_",
        "",
        "### Impact Distribution",
        f"  {_impact_badge(impact) or '_(run /work reflect weekly to score)_'}",
        f"  {acc_count} accomplishments total",
        "",
        f"### Carry Forward: {cf_count} items",
    ]

    # Include carry-forward table from artifact body if available
    artifact_body = weekly.get("_body", "")
    if artifact_body:
        in_cf = False
        for line in artifact_body.splitlines():
            if line.startswith("### Carry Forward"):
                in_cf = True
                continue
            if in_cf and line.startswith("###"):
                break
            if in_cf and line.strip():
                lines.append(f"  {line}")
        lines.append("")

    lines += [
        "### Planned vs Actual",
    ]
    if artifact_body:
        in_pva = False
        for line in artifact_body.splitlines():
            if line.startswith("### Planned vs Actual"):
                in_pva = True
                continue
            if in_pva and line.startswith("###"):
                break
            if in_pva and line.strip():
                lines.append(f"  {line}")
    else:
        lines.append("  _(run `/work reflect weekly` to populate)_")

    lines += [
        "",
        base._freshness_footer() if hasattr(base, "_freshness_footer") else "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_monthly_retro
# ---------------------------------------------------------------------------

def generate_monthly_retro(base: Any) -> str:
    """Generate a monthly retrospective from the latest monthly artifact.

    Surfaces 4 key dimensions: outcomes vs goals, boundary health,
    career evidence, unplanned demand.
    """
    state_dir = getattr(base, "state_dir", _WORK_STATE_DIR)
    fm = _read_reflect_current(state_dir)
    monthly = _latest_artifact("monthly", state_dir)

    now = datetime.now(timezone.utc)
    month_label = now.strftime("%B %Y")
    period = monthly.get("period") or now.strftime("%Y-%m")
    created = monthly.get("created", "")
    impact = monthly.get("impact_summary", "")
    acc_count = monthly.get("accomplishment_count", "?")
    cf_count = monthly.get("carry_forward_count", fm.get("carry_forward_count", "?"))

    # Load goals for alignment check (best-effort)
    profile = getattr(base, "profile", {})
    goals: list[str] = []
    if isinstance(profile, dict):
        goals = profile.get("current_goals", []) or []

    lines = [
        f"## Monthly Retrospective — {month_label} ({period})",
        "",
        f"_Generated: {_age_since(str(created)) if created else 'not yet generated'}_",
        "",
        "### 1. Outcomes vs Goals",
        f"  Accomplishments: {acc_count}  |  Impact: {_impact_badge(impact) or '?'}",
    ]

    if goals:
        lines += [
            "",
            "  Active goals:",
            *[f"    • {g}" for g in goals[:5]],
        ]
    else:
        lines.append("  _(no goals configured — see user_profile.yaml)_")

    lines += [
        "",
        "### 2. Carry Forward & Debt",
        f"  Items deferred: {cf_count}",
    ]

    artifact_body = monthly.get("_body", "")
    if artifact_body:
        in_cf = False
        for line in artifact_body.splitlines():
            if line.startswith("### Carry Forward"):
                in_cf = True
                continue
            if in_cf and line.startswith("###"):
                break
            if in_cf and line.strip():
                lines.append(f"  {line}")

    lines += [
        "",
        "### 3. Boundary Health",
        "  _(Boundary scoring available in Phase 2 — see specs/reflection-loop.md §6)_",
        "",
        "### 4. Career Evidence Captured",
        "  _(Connect-cycle evidence wiring available in Phase 2)_",
        "",
        base._freshness_footer() if hasattr(base, "_freshness_footer") else "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# _render_goal_trends  (called by generate_quarterly_review)
# ---------------------------------------------------------------------------

def _render_goal_trends(
    state_dir: Path,
    profile: Any,
    reader_cls: Any = None,
    last_n: int = 12,
) -> list[str]:
    """Render per-goal trend lines using ReflectReader.get_goal_trend().

    Returns a list of lines ready for insertion into generate_quarterly_review().
    Phase 1: all scores are None, so each goal shows "(scoring begins Phase 2)".
    Phase 2+: sparkline of block characters plus a percentage average.
    """
    if reader_cls is None:
        return ["  _(ReflectReader not available — reflection integration not initialized)_"]
    goals: list[str] = []
    if isinstance(profile, dict):
        goals = list(profile.get("current_goals", []) or [])
    if not goals:
        return ["  _(no goals configured — see user_profile.yaml)_"]
    try:
        reader = reader_cls(state_dir)
        lines: list[str] = []
        for goal in goals[:5]:  # cap at 5 to avoid wall-of-text
            trend = reader.get_goal_trend(str(goal), last_n=last_n)
            scored = [s for s in trend.scores if s is not None]
            if scored:
                avg = sum(scored) / len(scored)
                bar = "".join(
                    "\u2593" if s is not None and s >= 0.7
                    else "\u2592" if s is not None and s >= 0.4
                    else "\u2591" if s is not None
                    else "\xb7"
                    for s in trend.scores[-8:]
                )
                lines.append(f"  \u2022 {str(goal)[:55]}: [{bar}] avg {avg:.0%}")
            else:
                lines.append(f"  \u2022 {str(goal)[:55]}: _(scoring begins Phase 2)_")
        return lines
    except Exception as exc:
        log.warning("_render_goal_trends failed: %s", exc)
        return ["  _(goal trend data unavailable)_"]


# ---------------------------------------------------------------------------
# generate_quarterly_review
# ---------------------------------------------------------------------------

def generate_quarterly_review(base: Any) -> str:
    """Generate a quarterly business review summary from the latest quarterly artifact.

    Surfaces themes across the 12-week period, goal completion rate,
    and promotion-case evidence density.
    """
    state_dir = getattr(base, "state_dir", _WORK_STATE_DIR)
    profile: Any = getattr(base, "profile", {})
    fm = _read_reflect_current(state_dir)
    quarterly = _latest_artifact("quarterly", state_dir)

    now = datetime.now(timezone.utc)
    q = (now.month - 1) // 3 + 1
    period = quarterly.get("period") or f"{now.year}-Q{q}"
    created = quarterly.get("created", "")
    impact = quarterly.get("impact_summary", "")
    acc_count = quarterly.get("accomplishment_count", "?")
    cf_count = quarterly.get("carry_forward_count", fm.get("carry_forward_count", "?"))

    # Count weekly artifacts in this quarter as "coverage depth"
    weekly_dir = state_dir / "reflections" / "weekly"
    weekly_count = len(list(weekly_dir.glob("*.md"))) if weekly_dir.exists() else 0

    lines = [
        f"## Quarterly Review — {period}",
        "",
        f"_Generated: {_age_since(str(created)) if created else 'not yet generated'}_",
        "",
        "### Quarter Summary",
        f"  Period: {period}  |  Weekly artifacts: {weekly_count}/13",
        f"  Total accomplishments: {acc_count}",
        f"  Impact: {_impact_badge(impact) or '?'}",
        f"  Carry-forward accumulated: {cf_count}",
        "",
        "### Goal Completion Rate",
        *_render_goal_trends(
            state_dir,
            profile,
            reader_cls=_ReflectReader if _REFLECT_READER_AVAILABLE else None,
        ),
        "",
        "### Promotion Evidence Density",
        "  _(Promo-case wiring available in Phase 2 - see /work promo-case)_",
        "",
        "### Top Accomplishments (HIGH impact)",
    ]

    artifact_body = quarterly.get("_body", "")
    if artifact_body:
        in_high = False
        for line in artifact_body.splitlines():
            if "#### HIGH Impact" in line:
                in_high = True
                continue
            if in_high and line.startswith("####"):
                break
            if in_high and line.strip():
                lines.append(f"  {line}")
    else:
        lines.append("  _(run `/work reflect quarterly` to populate)_")

    lines += [
        "",
        base._freshness_footer() if hasattr(base, "_freshness_footer") else "",
    ]
    return "\n".join(lines)
