#!/usr/bin/env python3
"""
scripts/briefing_adapter.py — Adaptive Briefing Intelligence (E5).

Analyses historical health-check.md catch_up_runs to detect user-behavior
patterns and adjusts BriefingConfig parameters AFTER Step 2b deterministic
format selection — never overriding explicit user commands (/catch-up deep).

Adaptive Rules (deterministic, not ML):
  R1. flash_override_ratio   — if user overrode to flash >60% of last 10 runs
                               → default_format = "flash"
  R2. low_signal_noise       — if signal_noise_ratio < 30% for ≥7 of last 10
                               → suppress info-tier domain items
  R3. calibration_skip_rate  — if skip rate > 80% over last 10 runs
                               → calibration_count = 0
  R4. coaching_dismiss_rate  — if dismissed > 70% of last 10 nudges
                               → coaching_enabled = False
  R5. consistent_domains     — if same ≤5 domains appear in last 10 runs
                               → priority_domains pre-heated (logged only)
  R6. weekend_planner_skip   — if skipped every time over last 10 runs
                               → suppress "weekend_planner" section

Activation gate: minimum 10 catch-up runs (cold-start safe).
Override: explicit user format args always win.

Output: BriefingConfig dataclass, plus writes adaptive_adjustments list
back to health-check.md for transparency (footer note).

Config flag: enhancements.briefing_adapter (default: true)

Ref: specs/act-reloaded.md Enhancement 5
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

_HEALTH_CHECK_FILE = _ROOT_DIR / "state" / "health-check.md"
_CATCH_UP_RUNS_FILE = _ROOT_DIR / "state" / "catch_up_runs.yaml"  # primary run history
_SKILLS_CACHE_FILE = _ROOT_DIR / "state" / "skills_cache.json"    # unified skill health

# Minimum number of catch-up runs before any adaptive rules activate
_MIN_RUNS_FOR_ADAPTATION = 10
# Rolling window size for all adaptive rules
_WINDOW = 10


# ---------------------------------------------------------------------------
# BriefingConfig
# ---------------------------------------------------------------------------

@dataclass
class BriefingConfig:
    """Adjusted briefing parameters returned by BriefingAdapter.recommend()."""

    # Primary format: flash | standard | digest | deep
    format: str = "standard"
    # Max domain items per domain section (None = no cap)
    domain_item_cap: int | None = None
    # Calibration question count (0 = skip)
    calibration_count: int = 2
    # Whether the coaching nudge is enabled
    coaching_enabled: bool = True
    # Sections to suppress (e.g. ["weekend_planner"])
    suppressed_sections: list[str] = field(default_factory=list)
    # Pre-heated domains (logged; no functional change currently)
    priority_domains: list[str] = field(default_factory=list)
    # Transparency log of all adaptations applied this run
    adaptive_adjustments: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Health-check run history parser
# ---------------------------------------------------------------------------

def _load_catch_up_runs(health_path: Path = _HEALTH_CHECK_FILE) -> list[dict]:
    """Load catch-up run history from state/catch_up_runs.yaml (primary).

    Falls back to parsing health-check.md YAML frontmatter if the dedicated
    runs file doesn't exist yet (cold-start / pre-Phase-2 compatibility).
    """
    # PRIMARY: state/catch_up_runs.yaml (structured, machine-parseable)
    if _CATCH_UP_RUNS_FILE.exists():
        try:
            raw = _CATCH_UP_RUNS_FILE.read_text(encoding="utf-8", errors="replace")
            data = yaml.safe_load(raw)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
        except Exception:
            pass

    # LEGACY FALLBACK: health-check.md frontmatter (pre-Phase-2 format)
    if not health_path.exists():
        return []
    try:
        text = health_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    # Parse YAML frontmatter between --- delimiters
    lines = text.splitlines()
    fm_lines: list[str] = []
    in_fm = False
    dash_count = 0
    for line in lines:
        if line.strip() == "---":
            dash_count += 1
            if dash_count == 1:
                in_fm = True
                continue
            if dash_count == 2:
                break
        elif in_fm:
            fm_lines.append(line)

    if not fm_lines:
        return []

    try:
        fm: dict = yaml.safe_load("\n".join(fm_lines)) or {}
    except yaml.YAMLError:
        return []

    runs = fm.get("catch_up_runs", fm.get("runs", []))
    if isinstance(runs, list):
        return [r for r in runs if isinstance(r, dict)]
    return []


def _last_n(runs: list[dict], n: int) -> list[dict]:
    """Return the most recent N runs (list is assumed chronological)."""
    return runs[-n:] if len(runs) >= n else runs


# ---------------------------------------------------------------------------
# Adaptive rule evaluators
# ---------------------------------------------------------------------------

def _r1_flash_override_ratio(runs: list[dict]) -> str | None:
    """R1: if >60% of last 10 runs used flash format → recommend flash."""
    window = _last_n(runs, _WINDOW)
    flash_count = sum(
        1 for r in window
        if str(r.get("briefing_format", "")).lower() == "flash"
    )
    if len(window) == 0:
        return None
    ratio = flash_count / len(window)
    if ratio > 0.60:
        return f"R1:flash_override_ratio={ratio:.0%}"
    return None


def _r2_low_signal_noise(runs: list[dict]) -> str | None:
    """R2: if engagement_rate < 30% in >=7 of last 10 runs -> suppress info tier.

    Reads 'engagement_rate' (new field) with 'signal_noise' / 'signal_noise_ratio'
    as legacy fallbacks. Null entries (items_surfaced==0) are skipped.

    Cold-start gate: requires at least 10 non-null data points in the window before
    R2 can activate (spec §3.7 safety rail: "R2 only activates after 10 catch-ups
    with engagement_rate data").
    """
    window = _last_n(runs, _WINDOW)
    low_count = 0
    total_with_data = 0
    for r in window:
        # Read engagement_rate; fall back to legacy signal_noise field names
        snr = r.get("engagement_rate", r.get("signal_noise", r.get("signal_noise_ratio")))
        if snr is None:
            continue  # skip null entries (no-signal catch-ups)
        total_with_data += 1
        try:
            val = float(str(snr).rstrip("%")) / 100 if "%" in str(snr) else float(snr)
            if val < 0.30:
                low_count += 1
        except (TypeError, ValueError):
            continue
    # Cold-start gate: need at least 10 non-null data points (spec §3.7)
    if total_with_data < 10:
        return None
    if low_count >= 7:
        return f"R2:low_signal_noise({low_count}/{total_with_data} runs)"
    return None


def _r3_calibration_skip_rate(runs: list[dict], health_fm: dict) -> str | None:
    """R3: if calibration_skip_rate > 80% → calibration_count = 0."""
    skip_rate = health_fm.get("calibration_skip_rate")
    if skip_rate is None:
        # Derive from window
        window = _last_n(runs, _WINDOW)
        skips = sum(1 for r in window if r.get("calibration_skipped", False))
        if not window:
            return None
        skip_rate = skips / len(window)
    else:
        try:
            skip_rate = float(str(skip_rate).rstrip("%")) / 100 if "%" in str(skip_rate) else float(skip_rate)
        except (TypeError, ValueError):
            return None
    if skip_rate > 0.80:
        return f"R3:calibration_skip_rate={skip_rate:.0%}"
    return None


def _r4_coaching_dismiss_rate(runs: list[dict]) -> str | None:
    """R4: if coaching nudge dismissed >70% of last 10 → coaching_enabled = False."""
    window = _last_n(runs, _WINDOW)
    fired = 0
    dismissed = 0
    for r in window:
        cn = r.get("coaching_nudge", r.get("coaching"))
        if cn is None:
            continue
        fired += 1
        if str(cn).lower() in ("dismissed", "skipped", "ignored", "false", "0"):
            dismissed += 1
    if fired == 0:
        return None
    rate = dismissed / fired
    if rate > 0.70:
        return f"R4:coaching_dismiss_rate={rate:.0%}({dismissed}/{fired})"
    return None


def _r5_consistent_domains(runs: list[dict]) -> list[str]:
    """R5: if same ≤5 domains appear in every run of last 10 → return them as priority."""
    window = _last_n(runs, _WINDOW)
    if not window:
        return []
    domain_sets: list[set[str]] = []
    for r in window:
        domains = r.get("domains_processed", r.get("domains_loaded", r.get("domains", [])))
        if isinstance(domains, (list, set)):
            domain_sets.append({str(d).lower() for d in domains})
        elif isinstance(domains, str):
            domain_sets.append({d.strip().lower() for d in domains.split(",")})
    if len(domain_sets) < _WINDOW:
        return []
    universe = set.union(*domain_sets)
    consistent = [d for d in universe if all(d in ds for ds in domain_sets)]
    if len(consistent) <= 5:
        return sorted(consistent)
    return []


def _r6_weekend_planner_skip(runs: list[dict]) -> str | None:
    """R6: if user skipped weekend_planner in all last 10 applicable runs → suppress it."""
    window = _last_n(runs, _WINDOW)
    applicable = [r for r in window if r.get("weekend_planner_shown", False)]
    if len(applicable) < 5:  # not enough data
        return None
    all_skipped = all(r.get("weekend_planner_skipped", False) for r in applicable)
    if all_skipped:
        return f"R6:weekend_planner_skip({len(applicable)} runs)"
    return None

def _r7_skill_health_footer() -> str | None:
    """R7 footer: disclose skills with auto-reduced cadence or broken state.

    Returns an internal adjustment string listing affected skills.
    Displayed to the user as human language (see §3.6.1 user-facing mapping).
    Never surfaces internal rule names (R7) to the user.
    """
    if not _SKILLS_CACHE_FILE.exists():
        return None
    try:
        import json
        cache = json.loads(_SKILLS_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    cadence_reduced: list[str] = []
    broken: list[str] = []
    for skill_name, entry in cache.items():
        if not isinstance(entry, dict):
            continue
        health = entry.get("health", {})
        if not health:
            continue
        maturity = health.get("maturity", "warming_up")
        if maturity == "warming_up":
            continue
        classification = health.get("classification", "")
        consecutive_zero = health.get("consecutive_zero", 0)
        if classification == "degraded" and consecutive_zero >= 10:
            cadence_reduced.append(f"{skill_name}(zeros={consecutive_zero})")
        elif classification == "broken":
            broken.append(skill_name)

    parts: list[str] = []
    if cadence_reduced:
        parts.append("cadence_reduced:" + ",".join(cadence_reduced))
    if broken:
        parts.append("broken:" + ",".join(broken))
    return "R7:footer=" + "|".join(parts) if parts else None


def _r8_meta_regression_alarm(runs: list[dict]) -> str | None:
    """R8: if engagement_rate < 15% for 7 of last 10 runs -> surface meta-alarm.

    Fires at most once per 14-day window. Null entries (no-signal catch-ups) are
    excluded from the count (a catch-up with no alerts is not low-engagement).
    """
    window = _last_n(runs, _WINDOW)
    low_count = 0
    total_with_data = 0
    for r in window:
        rate = r.get("engagement_rate")
        if rate is None:
            continue  # skip no-signal catch-ups
        total_with_data += 1
        try:
            if float(rate) < 0.15:
                low_count += 1
        except (TypeError, ValueError):
            continue

    if total_with_data < 7:
        return None  # Not enough data points yet
    if low_count < 7:
        return None

    return f"R8:meta_regression_alarm(low={low_count}/{total_with_data})"


def _r9_quality_regression(
    runs: list[dict],
    drop_threshold_pct: float = 20.0,
    min_runs: int = 7,
) -> str | None:
    """R9: Flag quality regression in last 7 runs.

    Reads state/briefing_scores.json via MetricStore and signals when the
    quality trend is 'regressing' with sufficient data.

    Args:
        runs: catch_up_runs list (used for guard only; MetricStore reads scores).
        drop_threshold_pct: Unused directly; MetricStore uses a fixed 3-pt threshold.
        min_runs: Minimum scored runs needed before firing.

    Returns:
        Adjustment string like "R9:quality_regression(trend=regressing,avg=52.1)"
        or None if no regression.
    """
    try:
        from lib.metric_store import MetricStore  # noqa: PLC0415
        ms = MetricStore(_ROOT_DIR)
        trend_data = ms.get_quality_trend(window=min_runs)
        if trend_data.get("trend") != "regressing":
            return None
        run_count = trend_data.get("run_count", 0)
        if run_count < min_runs:
            return None
        avg_q = trend_data.get("avg_quality", 0.0)
        return f"R9:quality_regression(trend=regressing,avg={avg_q:.1f},runs={run_count})"
    except Exception:  # noqa: BLE001
        return None



class BriefingAdapter:
    """Analyse health-check history and return adjusted BriefingConfig.

    Usage:
        adapter = BriefingAdapter()
        config = adapter.recommend(base_format="standard", hours_elapsed=14.5)
    """

    def __init__(self, health_path: Path | None = None) -> None:
        self._health_path = health_path or _HEALTH_CHECK_FILE

    def recommend(
        self,
        base_format: str = "standard",
        hours_elapsed: float = 0.0,
        user_forced: bool = False,
    ) -> BriefingConfig:
        """Return a BriefingConfig adjusted by adaptive rules.

        Args:
            base_format: Format already chosen by Step 2b deterministic logic.
            hours_elapsed: Hours since last catch-up (for context only).
            user_forced: True if user explicitly specified format (/catch-up deep).
                         When True, only non-format adjustments (coaching, calibration) apply.
        """
        cfg = BriefingConfig(format=base_format)

        if not _load_flag("enhancements.briefing_adapter", default=True):
            return cfg

        runs = _load_catch_up_runs(self._health_path)

        # Cold-start gate
        if len(runs) < _MIN_RUNS_FOR_ADAPTATION:
            return cfg

        # Parse full frontmatter for health-level fields (calibration_skip_rate)
        health_fm = self._load_frontmatter()

        # Evaluate rules
        r1 = _r1_flash_override_ratio(runs)
        r2 = _r2_low_signal_noise(runs)
        r3 = _r3_calibration_skip_rate(runs, health_fm)
        r4 = _r4_coaching_dismiss_rate(runs)
        consistent_domains = _r5_consistent_domains(runs)
        r6 = _r6_weekend_planner_skip(runs)
        r7_footer = _r7_skill_health_footer()
        r8 = _r8_meta_regression_alarm(runs)
        r9 = _r9_quality_regression(runs)

        # Apply R1 — format adjustment (only when user did NOT force format)
        if r1 and not user_forced:
            cfg.format = "flash"
            cfg.adaptive_adjustments.append(r1)

        # Apply R2 — suppress info-tier items
        if r2:
            cfg.domain_item_cap = 3
            cfg.adaptive_adjustments.append(r2)

        # Apply R3 — zero out calibration
        if r3:
            cfg.calibration_count = 0
            cfg.adaptive_adjustments.append(r3)

        # Apply R4 — disable coaching
        if r4:
            cfg.coaching_enabled = False
            cfg.adaptive_adjustments.append(r4)

        # Apply R5 — log priority domains (no functional change, just transparency)
        if consistent_domains:
            cfg.priority_domains = consistent_domains
            cfg.adaptive_adjustments.append(f"R5:priority_domains={consistent_domains}")

        # Apply R6 — suppress weekend_planner
        if r6:
            cfg.suppressed_sections.append("weekend_planner")
            cfg.adaptive_adjustments.append(r6)

        # R7 — skill health footer disclosure (display only, no format change)
        if r7_footer:
            cfg.adaptive_adjustments.append(r7_footer)

        # R8 — meta-regression alarm (surfaces once per 14-day window)
        if r8:
            cfg.adaptive_adjustments.append(r8)

        # R9 — quality regression signal (eval layer)
        if r9:
            cfg.adaptive_adjustments.append(r9)

        return cfg

    def _load_frontmatter(self) -> dict:
        """Return health-check.md frontmatter as a dict (best-effort)."""
        if not self._health_path.exists():
            return {}
        try:
            text = self._health_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {}
        lines = text.splitlines()
        fm_lines: list[str] = []
        in_fm = False
        dash_count = 0
        for line in lines:
            if line.strip() == "---":
                dash_count += 1
                if dash_count == 1:
                    in_fm = True
                    continue
                if dash_count == 2:
                    break
            elif in_fm:
                fm_lines.append(line)
        if not fm_lines:
            return {}
        try:
            return yaml.safe_load("\n".join(fm_lines)) or {}
        except yaml.YAMLError:
            return {}

    def format_footer(self, cfg: BriefingConfig) -> str:
        """Return the transparency footer line for briefings (empty if no adaptations)."""
        if not cfg.adaptive_adjustments:
            return ""
        reasons = "; ".join(cfg.adaptive_adjustments[:3])
        return f"📊 Adapted: {reasons}"


# ---------------------------------------------------------------------------
# AI Trend Radar briefing section (C9 — PR-3)
# ---------------------------------------------------------------------------

_CATEGORY_EMOJI: dict[str, str] = {
    "tool_release":     "🛠",
    "technique":        "💡",
    "model_release":    "🤖",
    "tutorial":         "📖",
    "framework_update": "🔧",
    "research":         "🔬",
}


def render_radar_section(
    signals_file: "Path | str | None" = None,
    *,
    max_items: int = 5,
    artha_dir: "Path | None" = None,
) -> str:
    """Render Markdown briefing section from tmp/ai_trend_signals.json.

    Returns empty string if the signals file is missing or contains no signals.
    Designed to be injected into the catch-up briefing between §Step7 and §Step9.

    Args:
        signals_file: Override path to the signals JSON (default: tmp/ai_trend_signals.json).
        max_items:    Maximum number of signals to render (default: 5).
        artha_dir:    Workspace root (used when signals_file is None).
    """
    import json
    from pathlib import Path as _Path

    if signals_file is None:
        base = _Path(artha_dir) if artha_dir else _Path(__file__).parent.parent
        signals_file = base / "tmp" / "ai_trend_signals.json"

    signals_path = _Path(signals_file)
    if not signals_path.exists():
        return ""

    try:
        data = json.loads(signals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    signals = data.get("signals") or []
    if not signals:
        return ""

    week_end = data.get("week_end", "")
    lines = ["## 🧠 AI Radar — This Week's Signals"]
    if week_end:
        lines[0] += f" _(week of {week_end})_"
    lines.append("")

    try_badge_shown = False
    for i, sig in enumerate(signals[:max_items], start=1):
        topic = sig.get("topic", "Unknown topic")
        category = sig.get("category", "technique")
        emoji = _CATEGORY_EMOJI.get(category, "📡")
        summary = sig.get("summary", "")[:160]
        sources = sig.get("sources") or []
        seen_in = sig.get("seen_in", len(sources))
        score = sig.get("relevance_score", 0.0)
        try_worthy = sig.get("try_worthy", False)
        topic_match = sig.get("topic_match")

        # Header line
        source_badge = f"_{seen_in} source{'s' if seen_in != 1 else ''}_" if seen_in > 1 else ""
        try_badge = " ✅ try-worthy" if try_worthy else ""
        topic_badge = f" [_{topic_match}_]" if topic_match else ""
        header = f"{i}. {emoji} **{topic}**{topic_badge}{try_badge}"
        if source_badge:
            header += f" — {source_badge}"
        lines.append(header)

        # Summary line
        if summary:
            lines.append(f"   > {summary}")

        # URL hint
        url = sig.get("best_source_url", "")
        if url and url.startswith("https://"):
            lines.append(f"   🔗 {url}")

        lines.append("")
        if try_worthy:
            try_badge_shown = True

    # Footer hint
    if try_badge_shown:
        lines.append("_✅ = try-worthy: add to experiments with `/try <topic>`_")
        lines.append("")

    return "\n".join(lines)


_NL_CATEGORY_EMOJI = {
    "tool_release": "🔧",
    "technique": "💡",
    "model_release": "🤖",
    "tutorial": "📖",
    "framework_update": "📦",
    "research": "🔬",
}


def render_newsletter_suggestions(
    signals_file: "Path | str | None" = None,
    *,
    artha_dir: "Path | None" = None,
) -> str:
    """Render newsletter contribution suggestions from radar signals.

    Reads newsletter_suggestions from the radar output and renders a Markdown
    section suggesting topics the user could contribute to newsletters.
    Returns empty string if no suggestions are available.
    """
    import json
    from pathlib import Path as _Path

    if signals_file is None:
        base = _Path(artha_dir) if artha_dir else _Path(__file__).parent.parent
        signals_file = base / "tmp" / "ai_trend_signals.json"

    signals_path = _Path(signals_file)
    if not signals_path.exists():
        return ""

    try:
        data = json.loads(signals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    suggestions = data.get("newsletter_suggestions") or []
    if not suggestions:
        return ""

    lines = ["## 📰 Newsletter Contribution Ideas"]
    lines.append("")

    current_nl = ""
    for sugg in suggestions:
        nl_name = sugg.get("newsletter", "")
        if nl_name != current_nl:
            current_nl = nl_name
            lines.append(f"**{nl_name}** _(monthly)_")
            lines.append("")

        topic = sugg.get("topic", "")
        category = sugg.get("category", "technique")
        emoji = _NL_CATEGORY_EMOJI.get(category, "📡")
        angle = sugg.get("angle", "")
        url = sugg.get("best_source_url", "")
        fit = sugg.get("fit_score", 0.0)

        lines.append(f"- {emoji} **{topic}**")
        if angle:
            lines.append(f"  _{angle}_")
        if url and url.startswith("https://"):
            lines.append(f"  🔗 {url}")
        lines.append("")

    lines.append("_💡 These topics align with the newsletter's editorial focus and your expertise._")
    lines.append("")

    return "\n".join(lines)


def render_linkedin_suggestions(
    signals_file: "Path | str | None" = None,
    *,
    artha_dir: "Path | None" = None,
) -> str:
    """Render LinkedIn content idea suggestions from radar signals.

    Reads linkedin_suggestions from the radar output and renders a Markdown
    section suggesting topics for LinkedIn posts/articles with voice angles.
    Returns empty string if no suggestions are available.
    """
    import json
    from pathlib import Path as _Path

    if signals_file is None:
        base = _Path(artha_dir) if artha_dir else _Path(__file__).parent.parent
        signals_file = base / "tmp" / "ai_trend_signals.json"

    signals_path = _Path(signals_file)
    if not signals_path.exists():
        return ""

    try:
        data = json.loads(signals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    suggestions = data.get("linkedin_suggestions") or []
    if not suggestions:
        return ""

    lines = ["## 💼 LinkedIn Content Ideas"]
    lines.append("")

    for sugg in suggestions:
        topic = sugg.get("topic", "")
        category = sugg.get("category", "technique")
        emoji = _NL_CATEGORY_EMOJI.get(category, "📡")
        angle = sugg.get("angle", "")
        url = sugg.get("best_source_url", "")
        seen_in = sugg.get("seen_in", 1)

        trending_badge = f" 🔥 _{seen_in} sources_" if seen_in >= 2 else ""
        lines.append(f"- {emoji} **{topic}**{trending_badge}")
        if angle:
            lines.append(f"  _{angle}_")
        if url and url.startswith("https://"):
            lines.append(f"  🔗 {url}")
        lines.append("")

    lines.append("_💼 These are trending topics where your practitioner voice adds unique value._")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    adapter = BriefingAdapter()
    cfg = adapter.recommend(base_format="standard", hours_elapsed=12.0)
    print(f"BriefingConfig: format={cfg.format!r} coaching={cfg.coaching_enabled} "
          f"calibration={cfg.calibration_count} suppressed={cfg.suppressed_sections}")
    if cfg.adaptive_adjustments:
        print(f"  Adjustments: {cfg.adaptive_adjustments}")
    else:
        print("  No adaptations applied (cold start or no patterns detected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
