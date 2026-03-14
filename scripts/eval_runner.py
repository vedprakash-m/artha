#!/usr/bin/env python3
"""
scripts/eval_runner.py — Artha catch-up evaluation & performance analyzer.

Analyzes catch-up quality across multiple dimensions:
  1. Performance — response times, bottleneck identification, trend analysis
  2. Accuracy — action acceptance rates, correction frequency, domain coverage
  3. Signal quality — signal:noise ratio, suppression effectiveness
  4. Data freshness — domain staleness, connector health

Usage:
    python scripts/eval_runner.py                   # Full eval report
    python scripts/eval_runner.py --perf            # Performance only
    python scripts/eval_runner.py --accuracy        # Accuracy only
    python scripts/eval_runner.py --json            # JSON output
    python scripts/eval_runner.py --trend 7         # 7-day trend analysis

Ref: audit item §metrics-gap, observability.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_ARTHA_DIR = Path(__file__).resolve().parent.parent
_HEALTH_CHECK = _ARTHA_DIR / "state" / "health-check.md"
_PIPELINE_METRICS = _ARTHA_DIR / "tmp" / "pipeline_metrics.json"
_SKILLS_METRICS = _ARTHA_DIR / "tmp" / "skills_metrics.json"
_CATCHUP_METRICS = _ARTHA_DIR / "tmp" / "catchup_metrics.json"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _load_health_check() -> dict:
    """Parse health-check.md YAML frontmatter."""
    if not _HEALTH_CHECK.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    text = _HEALTH_CHECK.read_text()
    # Extract frontmatter between --- markers
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            try:
                return yaml.safe_load(text[3:end]) or {}
            except Exception:
                pass
    return {}


def _load_health_check_yaml_blocks() -> dict[str, Any]:
    """Extract all ```yaml code blocks from health-check.md."""
    if not _HEALTH_CHECK.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    text = _HEALTH_CHECK.read_text()
    result: dict[str, Any] = {}
    parts = text.split("```yaml")
    for part in parts[1:]:
        end = part.find("```")
        if end > 0:
            try:
                parsed = yaml.safe_load(part[:end])
                if isinstance(parsed, dict):
                    result.update(parsed)
            except Exception:
                continue
    return result


# ---------------------------------------------------------------------------
# Performance analysis
# ---------------------------------------------------------------------------

def analyze_performance(days: int = 7) -> dict[str, Any]:
    """Analyze pipeline + skill + catch-up performance metrics."""
    pipeline_runs = _load_json(_PIPELINE_METRICS)
    skills_runs = _load_json(_SKILLS_METRICS)
    catchup_runs = _load_json(_CATCHUP_METRICS)

    result: dict[str, Any] = {
        "pipeline": _analyze_series(pipeline_runs, "wall_clock_seconds", days),
        "skills": _analyze_series(skills_runs, "wall_clock_seconds", days),
        "catchup": _analyze_series(catchup_runs, "total_elapsed_seconds", days),
    }

    # Per-connector breakdown from pipeline metrics
    if pipeline_runs:
        connector_times: dict[str, list[float]] = {}
        for run in pipeline_runs:
            for cname, t in run.get("connector_timing", {}).items():
                connector_times.setdefault(cname, []).append(t)
        result["connector_breakdown"] = {
            name: {
                "avg_seconds": round(sum(times) / len(times), 2),
                "max_seconds": round(max(times), 2),
                "min_seconds": round(min(times), 2),
                "runs": len(times),
            }
            for name, times in sorted(connector_times.items())
        }

    # Per-skill breakdown
    if skills_runs:
        skill_times: dict[str, list[float]] = {}
        for run in skills_runs:
            for sname, t in run.get("skill_timing", {}).items():
                skill_times.setdefault(sname, []).append(t)
        result["skill_breakdown"] = {
            name: {
                "avg_seconds": round(sum(times) / len(times), 2),
                "max_seconds": round(max(times), 2),
                "runs": len(times),
            }
            for name, times in sorted(skill_times.items())
        }

    # Per-phase breakdown from catch-up metrics
    if catchup_runs:
        phase_times: dict[str, list[float]] = {}
        for run in catchup_runs:
            for pname, pdata in run.get("phases", {}).items():
                phase_times.setdefault(pname, []).append(pdata.get("elapsed_seconds", 0))
        result["phase_breakdown"] = {
            name: {
                "avg_seconds": round(sum(times) / len(times), 2),
                "max_seconds": round(max(times), 2),
                "runs": len(times),
            }
            for name, times in sorted(phase_times.items())
        }

    return result


def _analyze_series(
    runs: list[dict], time_key: str, days: int
) -> dict[str, Any]:
    """Compute stats for a time series of runs."""
    if not runs:
        return {"runs": 0, "avg_seconds": None, "trend": "no_data"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [r for r in runs if r.get("timestamp", "") >= cutoff]
    times = [r.get(time_key, 0) for r in recent if r.get(time_key)]

    if not times:
        return {"runs": 0, "avg_seconds": None, "trend": "no_data"}

    avg = round(sum(times) / len(times), 2)

    # Trend detection: compare first half to second half
    trend = "stable"
    if len(times) >= 4:
        mid = len(times) // 2
        recent_half = sum(times[:mid]) / mid
        older_half = sum(times[mid:]) / (len(times) - mid)
        if recent_half > older_half * 1.2:
            trend = "degrading"
        elif recent_half < older_half * 0.8:
            trend = "improving"

    return {
        "runs": len(times),
        "avg_seconds": avg,
        "min_seconds": round(min(times), 2),
        "max_seconds": round(max(times), 2),
        "p95_seconds": round(sorted(times)[int(len(times) * 0.95)], 2) if len(times) >= 5 else None,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# Accuracy analysis
# ---------------------------------------------------------------------------

def analyze_accuracy() -> dict[str, Any]:
    """Analyze catch-up accuracy from health-check data."""
    hc = _load_health_check_yaml_blocks()

    # Extract accuracy pulse data
    accuracy = hc.get("accuracy_pulse", {})
    domain_accuracy = hc.get("per_domain_accuracy", {})

    # Extract catch-up run history for acceptance rates
    runs = hc.get("catch_up_runs", [])
    if not isinstance(runs, list):
        runs = []

    total_proposed = 0
    total_accepted = 0
    for run in runs:
        if isinstance(run, dict):
            total_proposed += run.get("actions_proposed_this_session", 0)

    result: dict[str, Any] = {
        "total_catch_ups": len(runs),
        "actions_proposed": total_proposed,
    }

    if accuracy:
        result["rolling_7d"] = {
            "acceptance_rate_pct": accuracy.get("acceptance_rate_pct"),
            "actions_proposed": accuracy.get("actions_proposed", 0),
            "actions_accepted": accuracy.get("actions_accepted", 0),
        }

    if domain_accuracy:
        result["per_domain"] = domain_accuracy

    # Signal quality
    signal_noise = hc.get("signal_noise_tracking", {})
    if signal_noise:
        result["signal_noise"] = {
            "current_ratio_pct": signal_noise.get("current", {}).get("ratio_pct"),
            "rolling_30d_pct": signal_noise.get("rolling_30d_avg", {}).get("ratio_pct"),
            "alert_threshold_pct": signal_noise.get("alert_threshold_pct", 30),
        }

    return result


# ---------------------------------------------------------------------------
# Data freshness analysis
# ---------------------------------------------------------------------------

def analyze_freshness() -> dict[str, Any]:
    """Analyze domain data freshness and connector health."""
    hc = _load_health_check_yaml_blocks()

    domain_staleness = hc.get("domain_staleness", {})
    email_coverage = hc.get("email_coverage", {})
    oauth_health = hc.get("oauth_token_health", {})

    result: dict[str, Any] = {}

    if domain_staleness:
        stale_domains = []
        for domain, last_updated in domain_staleness.items():
            if last_updated == "never" or not last_updated:
                stale_domains.append({"domain": domain, "status": "never_populated"})
            else:
                try:
                    dt = datetime.fromisoformat(str(last_updated).replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - dt).days
                    if age_days > 7:
                        stale_domains.append({"domain": domain, "status": f"stale_{age_days}d"})
                except (ValueError, TypeError):
                    stale_domains.append({"domain": domain, "status": "unknown"})
        result["stale_domains"] = stale_domains

    if oauth_health:
        result["oauth"] = {
            name: {
                "status": data.get("token_status", "unknown"),
                "consecutive_failures": data.get("consecutive_failures", 0),
            }
            for name, data in oauth_health.items()
            if isinstance(data, dict)
        }

    return result


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_text_report(
    perf: dict, accuracy: dict, freshness: dict
) -> str:
    """Render a human-readable eval report."""
    lines = [
        "━━ ARTHA EVAL REPORT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # Performance
    lines.append("## Performance")
    for component in ["pipeline", "skills", "catchup"]:
        data = perf.get(component, {})
        if data.get("runs", 0) == 0:
            lines.append(f"  {component}: no data")
            continue
        trend_icon = {"improving": "📈", "degrading": "📉", "stable": "➡️"}.get(data.get("trend", ""), "❓")
        lines.append(
            f"  {component}: avg {data['avg_seconds']}s "
            f"(min {data.get('min_seconds', '?')}s / max {data.get('max_seconds', '?')}s) "
            f"over {data['runs']} runs {trend_icon} {data.get('trend', '')}"
        )

    # Connector breakdown
    if perf.get("connector_breakdown"):
        lines.append("")
        lines.append("  Connector timings:")
        for name, stats in perf["connector_breakdown"].items():
            lines.append(f"    {name}: avg {stats['avg_seconds']}s (max {stats['max_seconds']}s)")

    # Phase breakdown
    if perf.get("phase_breakdown"):
        lines.append("")
        lines.append("  Phase timings:")
        for name, stats in perf["phase_breakdown"].items():
            lines.append(f"    {name}: avg {stats['avg_seconds']}s (max {stats['max_seconds']}s)")

    lines.append("")

    # Accuracy
    lines.append("## Accuracy & Signal Quality")
    total = accuracy.get("total_catch_ups", 0)
    lines.append(f"  Total catch-ups: {total}")
    lines.append(f"  Actions proposed: {accuracy.get('actions_proposed', 0)}")
    r7d = accuracy.get("rolling_7d", {})
    if r7d.get("acceptance_rate_pct") is not None:
        lines.append(f"  7-day acceptance rate: {r7d['acceptance_rate_pct']}%")
    sn = accuracy.get("signal_noise", {})
    if sn.get("current_ratio_pct") is not None:
        lines.append(f"  Signal:noise ratio: {sn['current_ratio_pct']}%")

    lines.append("")

    # Freshness
    lines.append("## Data Freshness")
    stale = freshness.get("stale_domains", [])
    if stale:
        lines.append(f"  ⚠ {len(stale)} stale domain(s):")
        for d in stale:
            lines.append(f"    - {d['domain']}: {d['status']}")
    else:
        lines.append("  ✓ All domains fresh")

    oauth = freshness.get("oauth", {})
    if oauth:
        for name, data in oauth.items():
            status_icon = "✓" if data["status"] == "ok" else "⚠"
            lines.append(f"  {status_icon} {name}: {data['status']}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Artha catch-up evaluation & performance analyzer"
    )
    parser.add_argument("--perf", action="store_true", help="Performance analysis only")
    parser.add_argument("--accuracy", action="store_true", help="Accuracy analysis only")
    parser.add_argument("--freshness", action="store_true", help="Freshness analysis only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--trend", type=int, default=7, metavar="DAYS", help="Trend window (default: 7)")
    args = parser.parse_args(argv)

    perf = analyze_performance(args.trend) if not args.accuracy and not args.freshness else {}
    accuracy = analyze_accuracy() if not args.perf and not args.freshness else {}
    freshness = analyze_freshness() if not args.perf and not args.accuracy else {}

    if args.json:
        output = {}
        if perf:
            output["performance"] = perf
        if accuracy:
            output["accuracy"] = accuracy
        if freshness:
            output["freshness"] = freshness
        print(json.dumps(output, indent=2))
    else:
        print(render_text_report(perf, accuracy, freshness))

    return 0


if __name__ == "__main__":
    sys.exit(main())
