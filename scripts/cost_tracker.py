#!/usr/bin/env python3
"""
scripts/cost_tracker.py — Session-level API cost telemetry (E8).

Implements the /cost command by reading session history from health-check.md
and pipeline_metrics.json to produce token consumption and cost estimates.

All values are ESTIMATES (±50% accuracy). Token counts are derived from
empirically calibrated constants, NOT real-time API instrumentation.
Pricing constants are stored in config/artha_config.yaml.

Command output:
    /cost →  💰 Artha Cost Estimate
             Today: $0.42 | This week: $2.94 | This month: $12.60
             Breakdown: Claude / Gemini / Gmail / Telegram
             💡 Tip: cache-hit optimisation advice

Cost model:
  Input tokens ≈ context_pressure × window_tokens (default: 200,000)
  Output tokens ≈ briefing_format_constants (flash:500 | standard:2000 | deep:4000)
  Cache hit ≈ session timing regularity vs Anthropic 5-min TTL
  Claude cost = (input × input_rate + output × output_rate) × (1 - cache_hit × discount)

Config flag: enhancements.cost_tracker (default: true)
Pricing overrides: config/artha_config.yaml → cost_model section

Ref: specs/act-reloaded.md Enhancement 8
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_HEALTH_CHECK_FILE = _ROOT_DIR / "state" / "health-check.md"
_METRICS_FILE = _ROOT_DIR / "tmp" / "pipeline_metrics.json"

# ---------------------------------------------------------------------------
# Default pricing/estimation constants
# ---------------------------------------------------------------------------

_DEFAULT_COST_MODEL: dict[str, Any] = {
    # Claude per-token pricing (USD per 1K tokens) — update as pricing changes
    "claude_input_cost_per_1k": 0.003,       # Claude 3.5 Sonnet input
    "claude_output_cost_per_1k": 0.015,      # Claude 3.5 Sonnet output
    "claude_cache_discount_pct": 90,         # Anthropic cache read discount vs input
    # Context window size for token estimation
    "context_window_tokens": 200_000,
    # Tokens per context_pressure percent (e.g., 70% pressure ≈ 140K tokens)
    "tokens_per_pressure_pct": 2_000,
    # Output token estimates by briefing format
    "output_tokens_flash": 500,
    "output_tokens_standard": 2_000,
    "output_tokens_deep": 4_000,
    "output_tokens_digest": 1_200,
    # Gemini CLI: free tier (currently $0 for Gemini Flash)
    "gemini_cost_per_call": 0.0,
    # Gmail API: free within quota — negligble; flag if exceeded
    "gmail_cost_per_1k_calls": 0.0,
}

# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _load_cost_model() -> dict[str, Any]:
    """Load cost model overrides from artha_config.yaml (best-effort)."""
    model = dict(_DEFAULT_COST_MODEL)
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config")
        overrides = cfg.get("cost_model", {})
        if isinstance(overrides, dict):
            model.update(overrides)
    except Exception:
        pass
    return model


def _load_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a Markdown file."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
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


def _load_pipeline_metrics() -> dict:
    """Load pipeline_metrics.json for API call counts."""
    if not _METRICS_FILE.exists():
        return {}
    try:
        data = json.loads(_METRICS_FILE.read_text(encoding="utf-8")) or {}
        if isinstance(data, list):
            # Historical list format: wrap in a dict with a 'runs' key
            return {"runs": data}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _parse_catch_up_runs(fm: dict) -> list[dict]:
    """Extract catch_up_runs list from health-check.md frontmatter."""
    runs = fm.get("catch_up_runs", fm.get("runs", []))
    if isinstance(runs, list):
        return [r for r in runs if isinstance(r, dict)]
    return []


def _parse_run_date(run: dict) -> date | None:
    ts = run.get("timestamp", run.get("session_start", run.get("date")))
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------

def _estimate_session_cost(run: dict, model: dict) -> float:
    """Estimate USD cost of a single catch-up session from run metadata."""
    # Input tokens from context_pressure
    pressure_pct = float(run.get("context_pressure", 50))
    input_tokens = int(pressure_pct * model["tokens_per_pressure_pct"])

    # Output tokens by format
    fmt = str(run.get("briefing_format", "standard")).lower()
    output_map = {
        "flash": model["output_tokens_flash"],
        "standard": model["output_tokens_standard"],
        "deep": model["output_tokens_deep"],
        "digest": model["output_tokens_digest"],
    }
    output_tokens = int(output_map.get(fmt, model["output_tokens_standard"]))

    # Cache hit estimation: assume 60% baseline unless timing data is present
    cache_hit_pct = float(run.get("cache_hit_pct", 60)) / 100

    # Effective input cost accounting for cached tokens
    effective_input_rate = model["claude_input_cost_per_1k"] * (
        (1 - cache_hit_pct) + cache_hit_pct * (1 - model["claude_cache_discount_pct"] / 100)
    )

    input_cost = (input_tokens / 1_000) * effective_input_rate
    output_cost = (output_tokens / 1_000) * model["claude_output_cost_per_1k"]
    return round(input_cost + output_cost, 4)


def _aggregate_runs(runs: list[dict], model: dict) -> dict[str, Any]:
    """Aggregate cost estimates across time windows."""
    today = date.today()
    week_start = today - timedelta(days=7)
    month_start = today - timedelta(days=30)

    today_usd = 0.0
    week_usd = 0.0
    month_usd = 0.0
    today_sessions = 0
    week_sessions = 0
    month_sessions = 0

    # Accumulate formatting/API stats
    total_context_pressure = []
    total_cache_hits = []
    total_input_tokens = 0
    total_output_tokens = 0

    for run in runs:
        run_date = _parse_run_date(run)
        if run_date is None:
            continue
        cost = _estimate_session_cost(run, model)
        pressure = float(run.get("context_pressure", 50))
        input_tok = int(pressure * model["tokens_per_pressure_pct"])
        fmt = str(run.get("briefing_format", "standard")).lower()
        output_tok = int({
            "flash": model["output_tokens_flash"],
            "standard": model["output_tokens_standard"],
            "deep": model["output_tokens_deep"],
            "digest": model["output_tokens_digest"],
        }.get(fmt, model["output_tokens_standard"]))

        if run_date >= month_start:
            month_usd += cost
            month_sessions += 1
            total_context_pressure.append(pressure)
            total_cache_hits.append(float(run.get("cache_hit_pct", 60)))
            total_input_tokens += input_tok
            total_output_tokens += output_tok

        if run_date >= week_start:
            week_usd += cost
            week_sessions += 1

        if run_date == today:
            today_usd += cost
            today_sessions += 1

    avg_cache_hit = sum(total_cache_hits) / len(total_cache_hits) if total_cache_hits else 60.0

    return {
        "today_usd": round(today_usd, 4),
        "today_sessions": today_sessions,
        "week_usd": round(week_usd, 4),
        "week_sessions": week_sessions,
        "month_usd": round(month_usd, 4),
        "month_sessions": month_sessions,
        "avg_cache_hit_pct": round(avg_cache_hit, 1),
        "total_input_tokens_month": total_input_tokens,
        "total_output_tokens_month": total_output_tokens,
    }


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

class CostTracker:
    """Derives session cost estimates from health-check.md and pipeline_metrics.json.

    Usage:
        tracker = CostTracker()
        report = tracker.build_report()
        print(tracker.format_report(report))
    """

    def __init__(
        self,
        health_path: Path | None = None,
        metrics_path: Path | None = None,
    ) -> None:
        self._health_path = health_path or _HEALTH_CHECK_FILE
        self._metrics_path = metrics_path or _METRICS_FILE
        self._model = _load_cost_model()

    def build_report(self) -> dict[str, Any]:
        """Build a cost report dict from all available data sources."""
        fm = _load_frontmatter(self._health_path)
        runs = _parse_catch_up_runs(fm)
        metrics = _load_pipeline_metrics()

        aggregated = _aggregate_runs(runs, self._model)

        # API call counts from pipeline_metrics.json
        msgraph_calls = int(metrics.get("msgraph_calls", 0))
        gmail_calls = int(metrics.get("gmail_calls", metrics.get("email_count", 0)))
        telegram_messages = int(metrics.get("telegram_messages", 0))
        gemini_calls = int(metrics.get("gemini_calls", 0))

        # Micro-cost items
        gmail_cost = (gmail_calls / 1_000) * self._model["gmail_cost_per_1k_calls"]
        gemini_cost = gemini_calls * self._model["gemini_cost_per_call"]
        total_usd = round(
            aggregated["today_usd"] + gmail_cost + gemini_cost, 4
        )

        return {
            **aggregated,
            "msgraph_calls": msgraph_calls,
            "gmail_calls": gmail_calls,
            "telegram_messages": telegram_messages,
            "gemini_calls": gemini_calls,
            "gmail_cost_usd": round(gmail_cost, 4),
            "gemini_cost_usd": round(gemini_cost, 4),
            "total_today_usd": total_usd,
            "accuracy_disclaimer": "±50% — estimates only; not from live API billing",
        }

    def format_report(self, report: dict[str, Any]) -> str:
        """Format a human-readable /cost response."""
        today_usd = report.get("today_usd", 0)
        week_usd = report.get("week_usd", 0)
        month_usd = report.get("month_usd", 0)
        month_sessions = report.get("month_sessions", 0)
        week_sessions = report.get("week_sessions", 0)
        avg_cache = report.get("avg_cache_hit_pct", 60.0)
        input_tok = report.get("total_input_tokens_month", 0)
        output_tok = report.get("total_output_tokens_month", 0)
        gemini_usd = report.get("gemini_cost_usd", 0)
        gmail_usd = report.get("gmail_cost_usd", 0)
        gemini_calls = report.get("gemini_calls", 0)
        gmail_calls = report.get("gmail_calls", 0)
        tg_msgs = report.get("telegram_messages", 0)

        lines = [
            "💰 Artha Cost Estimate (est. ±50%)",
            "",
            f"Today:      ${today_usd:.2f}  ({report.get('today_sessions', 0)} session(s))",
            f"This week:  ${week_usd:.2f}  ({week_sessions} session(s))",
            f"This month: ${month_usd:.2f}  ({month_sessions} session(s))",
            "",
            "📊 Breakdown (this month):",
        ]

        # Claude
        claude_share = month_usd - gemini_usd - gmail_usd
        if month_usd > 0:
            claude_pct = (claude_share / month_usd) * 100
            lines.append(
                f"  Claude API:   ${claude_share:.2f}  ({claude_pct:.0f}%)  "
                f"| {input_tok:,} in / {output_tok:,} out tokens"
            )
        else:
            lines.append("  Claude API:  $0.00  (no sessions logged)")

        # Gemini
        gemini_str = f"${gemini_usd:.2f}  (free tier)" if gemini_usd == 0 else f"${gemini_usd:.2f}"
        lines.append(f"  Gemini CLI:   {gemini_str}  | {gemini_calls} call(s)")

        # Gmail
        gmail_str = f"${gmail_usd:.2f}  (free quota)" if gmail_usd == 0 else f"${gmail_usd:.2f}"
        lines.append(f"  Gmail API:    {gmail_str}  | {gmail_calls} emails processed")

        # Telegram (free)
        lines.append(f"  Telegram:     $0.00  (free)  | {tg_msgs} message(s)")

        # Tip
        lines.append("")
        if avg_cache < 50:
            tip = (
                f"💡 Cache hit rate: {avg_cache:.0f}%. "
                "Running catch-ups at consistent times improves cache efficiency "
                "(Anthropic 5-min TTL). More consistent timing could reduce costs ~30%."
            )
        elif avg_cache >= 70:
            tip = (
                f"💡 Cache hit rate: {avg_cache:.0f}% — excellent! "
                "Consistent catch-up timing is keeping costs low."
            )
        else:
            tip = (
                f"💡 Cache hit rate: {avg_cache:.0f}%. "
                "Tip: run catch-ups at consistent times to improve cache efficiency."
            )
        lines.append(tip)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    tracker = CostTracker()
    report = tracker.build_report()
    print(tracker.format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
