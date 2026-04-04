#!/usr/bin/env python3
"""scripts/domain_training.py — AFW-10 Domain Training & Feedback Loop.

Tracks per-domain accuracy over successive catch-up runs, detects which
domains need more correction data, reports training effectiveness, and
integrates with the self-model writer to update domain confidence.

The training loop:
  1. Read catch_up_runs.yaml for per-run domain data
  2. Read state/memory.md for corrections applied per domain
  3. Calculate per-domain accuracy trends (acceptance, correction frequency)
  4. Detect domains that are improving (corrections compounding) vs. stagnant
  5. Generate training suggestions for underperforming domains
  6. Write training state to state/domain_training.yaml

Config gate: harness.agentic.domain_training.enabled (default: true)
Activation: minimum 5 catch-up runs before first analysis.

Usage:
    python scripts/domain_training.py              # Full training report
    python scripts/domain_training.py --json       # JSON output
    python scripts/domain_training.py --domain X   # Single domain report

Ref: specs/agent-fw.md §3.10, AFW-10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover

    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default


# --- Paths -------------------------------------------------------------------

_CATCH_UP_RUNS = _ARTHA_DIR / "state" / "catch_up_runs.yaml"
_MEMORY_FILE = _ARTHA_DIR / "state" / "memory.md"
_TRAINING_STATE = _ARTHA_DIR / "state" / "domain_training.yaml"
_CONFIG_FILE = _ARTHA_DIR / "config" / "artha_config.yaml"

# --- Constants ---------------------------------------------------------------

_MIN_RUNS = 5  # minimum catch-up runs before first analysis
_TREND_WINDOW = 7  # days for trend calculation
_STALE_THRESHOLD = 3  # consecutive absences before domain is flagged stale
_LOW_ACCURACY_THRESHOLD = 0.6  # engagement rate below this → needs training
_HIGH_CORRECTION_THRESHOLD = 2.0  # avg corrections/session above this → struggling


# --- Data Loaders ------------------------------------------------------------


def _load_catch_up_runs() -> list[dict]:
    """Load structured run history from state/catch_up_runs.yaml."""
    if not _CATCH_UP_RUNS.exists() or not _YAML_AVAILABLE:
        return []
    try:
        data = yaml.safe_load(_CATCH_UP_RUNS.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    except Exception:  # noqa: BLE001
        pass
    return []


def _load_memory_corrections() -> list[dict]:
    """Load correction facts from state/memory.md YAML frontmatter."""
    if not _MEMORY_FILE.exists() or not _YAML_AVAILABLE:
        return []
    try:
        text = _MEMORY_FILE.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return []
        end = text.find("---", 3)
        if end < 0:
            return []
        fm = yaml.safe_load(text[3:end]) or {}
        facts = fm.get("facts", [])
        if not isinstance(facts, list):
            return []
        return [
            f
            for f in facts
            if isinstance(f, dict) and f.get("type") in ("correction", "threshold")
        ]
    except Exception:  # noqa: BLE001
        return []


# --- Per-Domain Analysis -----------------------------------------------------


def _extract_domain_stats(
    runs: list[dict],
) -> dict[str, dict[str, Any]]:
    """Build per-domain stats from catch-up run history.

    Returns {domain: {runs, engagement_rates, correction_counts, last_seen}}.
    """
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "runs": 0,
            "engagement_rates": [],
            "correction_counts": [],
            "last_seen": "",
            "timestamps": [],
        }
    )

    for run in runs:
        ts = run.get("timestamp", "")
        domains_covered = run.get("domains_covered", [])
        if not isinstance(domains_covered, list):
            domains_covered = []

        # Per-domain corrections (if available)
        domain_corrections = run.get("domain_corrections", {})
        if not isinstance(domain_corrections, dict):
            domain_corrections = {}

        # Global engagement rate is a proxy when per-domain isn't available
        global_rate = run.get("engagement_rate")

        for domain in domains_covered:
            d = stats[domain]
            d["runs"] += 1
            d["timestamps"].append(ts)
            d["last_seen"] = max(d["last_seen"], ts) if d["last_seen"] else ts

            # Per-domain engagement rate or fall back to global
            domain_rate = domain_corrections.get(domain, {}).get(
                "engagement_rate", global_rate
            )
            if domain_rate is not None:
                d["engagement_rates"].append(domain_rate)

            # Per-domain correction count
            corr_count = domain_corrections.get(domain, {}).get("correction_count", 0)
            d["correction_counts"].append(corr_count)

    return dict(stats)


def _compute_trends(
    stats: dict[str, dict[str, Any]],
    corrections: list[dict],
) -> dict[str, dict[str, Any]]:
    """Compute trends and training suggestions per domain.

    Returns {domain: {avg_engagement, avg_corrections, trend, suggestion, ...}}.
    """
    results: dict[str, dict[str, Any]] = {}

    # Count stored corrections per domain
    correction_counts: dict[str, int] = defaultdict(int)
    for c in corrections:
        d = c.get("domain", "general")
        correction_counts[d] += 1

    for domain, ds in stats.items():
        rates = ds["engagement_rates"]
        corrs = ds["correction_counts"]

        avg_rate = round(sum(rates) / len(rates), 4) if rates else None
        avg_corr = round(sum(corrs) / len(corrs), 2) if corrs else 0.0

        # Trend: compare first half vs second half
        trend = "stable"
        if len(rates) >= 4:
            mid = len(rates) // 2
            first_half = sum(rates[:mid]) / mid
            second_half = sum(rates[mid:]) / (len(rates) - mid)
            delta = second_half - first_half
            if delta > 0.05:
                trend = "improving"
            elif delta < -0.05:
                trend = "regressing"

        # Staleness check
        stale = False
        if ds["last_seen"]:
            try:
                last = datetime.fromisoformat(
                    ds["last_seen"].replace("Z", "+00:00")
                )
                days_since = (datetime.now(timezone.utc) - last).days
                stale = days_since > _TREND_WINDOW
            except (ValueError, TypeError):
                pass

        # Training suggestion
        suggestion = None
        if avg_rate is not None and avg_rate < _LOW_ACCURACY_THRESHOLD:
            suggestion = (
                f"Low engagement ({avg_rate:.0%}) — add domain-specific "
                f"few-shot examples or correction facts."
            )
        elif avg_corr > _HIGH_CORRECTION_THRESHOLD:
            suggestion = (
                f"High correction rate ({avg_corr:.1f}/session) — review "
                f"extraction rules in prompts/{domain}.md."
            )
        elif trend == "regressing":
            suggestion = (
                f"Quality regressing — check for stale data or prompt drift "
                f"in prompts/{domain}.md."
            )
        elif stale:
            suggestion = (
                f"Domain absent from recent briefings — verify routing rules "
                f"and data sources."
            )

        results[domain] = {
            "total_runs": ds["runs"],
            "avg_engagement_rate": avg_rate,
            "avg_corrections_per_session": avg_corr,
            "stored_corrections": correction_counts.get(domain, 0),
            "trend": trend,
            "stale": stale,
            "last_seen": ds["last_seen"],
            "suggestion": suggestion,
        }

    return results


# --- Public API --------------------------------------------------------------


def analyze(domain_filter: str | None = None) -> dict[str, Any]:
    """Run the full domain training analysis.

    Returns a dict with:
      - total_runs: int
      - domains: {domain: {avg_engagement_rate, trend, suggestion, ...}}
      - underperforming: list of domain names needing attention
      - effective_corrections: domains where corrections are compounding
    """
    if not _load_flag("agentic.domain_training.enabled"):
        return {"skipped": True, "reason": "domain_training disabled"}

    runs = _load_catch_up_runs()
    if len(runs) < _MIN_RUNS:
        return {
            "skipped": True,
            "reason": f"insufficient data ({len(runs)} < {_MIN_RUNS} runs)",
        }

    corrections = _load_memory_corrections()
    stats = _extract_domain_stats(runs)
    trends = _compute_trends(stats, corrections)

    # Classify domains
    underperforming = []
    effective_corrections = []
    for domain, t in trends.items():
        if t["suggestion"] is not None:
            underperforming.append(domain)
        if t["trend"] == "improving" and t["stored_corrections"] > 0:
            effective_corrections.append(domain)

    result = {
        "total_runs": len(runs),
        "total_corrections_stored": len(corrections),
        "domains": trends,
        "underperforming": sorted(underperforming),
        "effective_corrections": sorted(effective_corrections),
        "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Filter to single domain if requested
    if domain_filter:
        if domain_filter in trends:
            result["domains"] = {domain_filter: trends[domain_filter]}
        else:
            result["domains"] = {}
            result["note"] = f"domain '{domain_filter}' not found in run history"

    return result


def save_training_state(analysis: dict[str, Any]) -> None:
    """Persist training state to state/domain_training.yaml."""
    if not _YAML_AVAILABLE or analysis.get("skipped"):
        return
    try:
        _TRAINING_STATE.write_text(
            yaml.dump(analysis, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


# --- CLI ---------------------------------------------------------------------


def _print_report(analysis: dict[str, Any]) -> None:
    """Print human-readable training report."""
    if analysis.get("skipped"):
        print(f"⏭️  Domain training skipped: {analysis['reason']}")
        return

    print(f"📊 Domain Training Report ({analysis['total_runs']} catch-up runs)")
    print(f"   Stored corrections: {analysis['total_corrections_stored']}")
    print()

    domains = analysis.get("domains", {})
    for domain, info in sorted(domains.items()):
        trend_icon = {"improving": "📈", "regressing": "📉", "stable": "➡️"}.get(
            info["trend"], "❓"
        )
        stale_tag = " [STALE]" if info.get("stale") else ""
        rate_str = (
            f"{info['avg_engagement_rate']:.0%}"
            if info["avg_engagement_rate"] is not None
            else "n/a"
        )
        print(
            f"  {trend_icon} {domain:<20} engagement={rate_str}  "
            f"corrections={info['avg_corrections_per_session']:.1f}/run  "
            f"stored={info['stored_corrections']}{stale_tag}"
        )
        if info.get("suggestion"):
            print(f"     💡 {info['suggestion']}")

    if analysis.get("underperforming"):
        print(f"\n⚠️  Underperforming: {', '.join(analysis['underperforming'])}")
    if analysis.get("effective_corrections"):
        print(
            f"✅ Corrections compounding: {', '.join(analysis['effective_corrections'])}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="AFW-10 Domain Training")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--domain", type=str, help="Filter to single domain")
    parser.add_argument(
        "--save", action="store_true", help="Save state to domain_training.yaml"
    )
    args = parser.parse_args()

    result = analyze(domain_filter=args.domain)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_report(result)

    if args.save or not args.json:
        save_training_state(result)


if __name__ == "__main__":
    main()
