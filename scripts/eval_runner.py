#!/usr/bin/env python3
"""
scripts/eval_runner.py — Artha catch-up evaluation & performance analyzer.

Analyzes catch-up quality across multiple dimensions:
  1. Performance — response times, bottleneck identification, trend analysis
  2. Accuracy — action acceptance rates, correction frequency, domain coverage
  3. Signal quality — signal:noise ratio, suppression effectiveness
  4. Data freshness — domain staleness, connector health
  5. Quality — 5-dimension briefing quality score (EV-5 / eval_scorer.py)
  6. Log health — connector error budgets, anomaly detection (EV-6 / log_digest.py)
  7. Memory health — memory pipeline run statistics (EV-11c)
  8. External agents — AR-9 invocation metrics, routing audit, cache report

Usage:
    python scripts/eval_runner.py                   # Full eval report
    python scripts/eval_runner.py --perf            # Performance only
    python scripts/eval_runner.py --accuracy        # Accuracy only
    python scripts/eval_runner.py --skills          # Skill health table
    python scripts/eval_runner.py --quality         # Quality score analysis
    python scripts/eval_runner.py --summary         # Dashboard summary
    python scripts/eval_runner.py --log-health      # Log digest + anomaly report
    python scripts/eval_runner.py --memory          # Memory pipeline health
    python scripts/eval_runner.py --agents          # AR-9 external agent metrics
    python scripts/eval_runner.py --agent NAME      # AR-9 filter to specific agent
    python scripts/eval_runner.py --routing-audit   # AR-9 routing decision log
    python scripts/eval_runner.py --cache-report    # AR-9 ext-agent-cache report
    python scripts/eval_runner.py --json            # JSON output
    python scripts/eval_runner.py --trend 7         # 7-day trend analysis

Ref: audit item §metrics-gap, observability.md, specs/eval.md
"""
from __future__ import annotations

import argparse
import json
import sys

# Ensure UTF-8 output on Windows (PowerShell defaults to cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_ARTHA_DIR = Path(__file__).resolve().parent.parent
_HEALTH_CHECK = _ARTHA_DIR / "state" / "health-check.md"
_PIPELINE_METRICS = _ARTHA_DIR / "tmp" / "pipeline_metrics.json"
_SKILLS_METRICS = _ARTHA_DIR / "tmp" / "skills_metrics.json"  # deprecated; kept for fallback
_CATCHUP_METRICS = _ARTHA_DIR / "tmp" / "catchup_metrics.json"
_SKILLS_CACHE = _ARTHA_DIR / "state" / "skills_cache.json"   # unified persistent cache
_CATCH_UP_RUNS = _ARTHA_DIR / "state" / "catch_up_runs.yaml"  # structured run history
_BRIEFING_SCORES = _ARTHA_DIR / "state" / "briefing_scores.json"  # EV-5 quality history
_LOG_DIGEST = _ARTHA_DIR / "tmp" / "log_digest.json"              # EV-6 anomaly digest
_EVAL_ALERTS = _ARTHA_DIR / "state" / "eval_alerts.yaml"          # EV-16b alert queue
_EXT_AGENT_METRICS = _ARTHA_DIR / "tmp" / "ext-agent-metrics.jsonl"  # AR-9 agent inv. metrics
_EXT_AGENT_REGISTRY = _ARTHA_DIR / "config" / "agents" / "external-registry.yaml"  # AR-9 registry
_MEMORY_PIPELINE_RUNS = Path(
    __import__("os").path.expanduser("~")
) / ".artha-local" / "logs" / "memory_pipeline_runs.jsonl"


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


def _load_catch_up_runs() -> list[dict]:
    """Load structured run history from state/catch_up_runs.yaml (EV-4 / DD-2)."""
    if not _CATCH_UP_RUNS.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        data = yaml.safe_load(_CATCH_UP_RUNS.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    except Exception:
        pass
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

    # Per-skill breakdown — prefer unified cache (last_wall_clock_ms), fall back to metrics file
    skill_breakdown = _skill_breakdown_from_cache()
    if skill_breakdown:
        result["skill_breakdown"] = skill_breakdown
    elif skills_runs:
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


def _skill_breakdown_from_cache() -> dict[str, Any] | None:
    """Read per-skill wall-clock timing from the unified state/skills_cache.json.

    Returns a dict keyed by skill name with timing + health summary, or None
    if the cache doesn't exist or has no health data yet.
    """
    if not _SKILLS_CACHE.exists():
        return None
    try:
        cache = json.loads(_SKILLS_CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    breakdown: dict[str, Any] = {}
    for name, entry in cache.items():
        if not isinstance(entry, dict):
            continue
        health = entry.get("health", {})
        if not health:
            continue
        wall_ms = health.get("last_wall_clock_ms")
        breakdown[name] = {
            "last_wall_clock_ms": wall_ms,
            "avg_seconds": round(wall_ms / 1000, 3) if wall_ms is not None else None,
            "total_runs": health.get("total_runs", 0),
            "health_classification": health.get("classification", "warming_up"),
            "consecutive_zero": health.get("consecutive_zero", 0),
        }
    return breakdown if breakdown else None


# ---------------------------------------------------------------------------
# Skill health analysis  (Phase 3)
# ---------------------------------------------------------------------------

def analyze_skills() -> dict[str, Any]:
    """Read state/skills_cache.json and state/skills.yaml to produce skill health table.

    Returns a summary dict with per-skill health details and aggregate counts.
    Triggered by the --skills CLI flag.
    """
    # Resolve CADENCE_REDUCTION from shared lib (with fallback for standalone runs)
    try:
        if str(_ARTHA_DIR) not in sys.path:
            sys.path.insert(0, str(_ARTHA_DIR))
        from lib.skill_health import CADENCE_REDUCTION  # type: ignore[import]
    except ImportError:
        CADENCE_REDUCTION = {"every_run": "daily", "daily": "weekly"}

    # Load cache
    if not _SKILLS_CACHE.exists():
        return {"error": "state/skills_cache.json not found — run skills first"}
    try:
        cache: dict[str, Any] = json.loads(_SKILLS_CACHE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return {"error": f"Cannot read skills cache: {exc}"}

    # Load skills config for cadence and enabled status
    _skills_cfg_path = _ARTHA_DIR / "config" / "skills.yaml"
    skills_cfg: dict[str, Any] = {}
    if _skills_cfg_path.exists():
        try:
            import yaml as _yaml
            raw = _yaml.safe_load(_skills_cfg_path.read_text()) or {}
            skills_cfg = raw.get("skills", raw) if isinstance(raw, dict) else {}
        except Exception:
            pass

    classification_icons = {
        "warming_up": "🌱",
        "healthy": "✅",
        "degraded": "⚠️",
        "stable": "📦",
        "broken": "🔴",
    }

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {"warming_up": 0, "healthy": 0, "degraded": 0, "stable": 0, "broken": 0}

    for skill_name, entry in sorted(cache.items()):
        if not isinstance(entry, dict):
            continue
        health = entry.get("health", {})
        total = health.get("total_runs", 0)
        success = health.get("success_count", 0)
        zero = health.get("zero_value_count", 0)
        wall_ms = health.get("last_wall_clock_ms")
        classification = health.get("classification", "warming_up")
        counts[classification] = counts.get(classification, 0) + 1

        # Success% and Zero% — guard against 0 total_runs
        success_pct = f"{round(success / total * 100)}%" if total else "—"
        zero_pct = f"{round(zero / total * 100)}%" if total else "—"

        # Wall clock display
        wall_display = f"{wall_ms}ms" if wall_ms is not None else "—"

        # Last value timestamp — read from health.last_nonzero_value
        # (the ISO timestamp of the most recent run that returned non-zero data)
        last_val = health.get("last_nonzero_value")
        if last_val and isinstance(last_val, str) and len(last_val) >= 10:
            try:
                dt = datetime.fromisoformat(last_val.replace("Z", "+00:00"))
                last_val_display = f"{dt.month}/{dt.day}"
            except ValueError:
                last_val_display = last_val[:10]
        else:
            last_val_display = "Never"

        # Cadence display — suggest reduction if degraded/stable
        cfg_entry = skills_cfg.get(skill_name, {})
        cadence = cfg_entry.get("cadence", "?")
        cadence_display = str(cadence)
        if classification in ("degraded", "stable") and cadence in CADENCE_REDUCTION:
            cadence_display = f"{cadence} → suggest {CADENCE_REDUCTION[cadence]}"

        rows.append({
            "skill": skill_name,
            "classification": classification,
            "icon": classification_icons.get(classification, "?"),
            "success_pct": success_pct,
            "zero_pct": zero_pct,
            "last_value": last_val_display,
            "wall_clock": wall_display,
            "cadence": cadence_display,
            "total_runs": total,
            "consecutive_zero": health.get("consecutive_zero", 0),
            "r7_skips": health.get("r7_skips", 0),
        })

    # Sort: broken first, then degraded, then stable, healthy, warming_up
    _order = {"broken": 0, "degraded": 1, "stable": 2, "healthy": 3, "warming_up": 4}
    rows.sort(key=lambda r: (_order.get(r["classification"], 5), r["skill"]))

    return {
        "total": len(rows),
        "broken": counts.get("broken", 0),
        "degraded": counts.get("degraded", 0),
        "stable": counts.get("stable", 0),
        "healthy": counts.get("healthy", 0),
        "warming_up": counts.get("warming_up", 0),
        "skills": rows,
    }


def render_skills_table(skills_data: dict[str, Any]) -> str:
    """Render skill health as a human-readable text table."""
    if "error" in skills_data:
        return f"⚠ Skill health: {skills_data['error']}"

    rows = skills_data.get("skills", [])
    total = skills_data.get("total", len(rows))

    lines = [
        "━━ SKILL HEALTH ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"## Skill Health — {total} Skills",
        "",
        f"  {'Skill':<32} {'Health':<12} {'Success%':>8} {'Zero%':>6} {'Last Val':>9} {'Wall':>7}  Cadence",
        f"  {'─'*32} {'─'*12} {'─'*8} {'─'*6} {'─'*9} {'─'*7}  {'─'*20}",
    ]
    for r in rows:
        icon = r["icon"]
        cls = r["classification"]
        lines.append(
            f"  {r['skill']:<32} {icon} {cls:<10} {r['success_pct']:>8} {r['zero_pct']:>6} "
            f"{r['last_value']:>9} {r['wall_clock']:>7}  {r['cadence']}"
        )

    broken = skills_data.get("broken", 0)
    degraded = skills_data.get("degraded", 0)
    stable = skills_data.get("stable", 0)
    healthy = skills_data.get("healthy", 0)
    warming = skills_data.get("warming_up", 0)

    lines += [
        "",
        f"  Summary: {broken} broken  {degraded} degraded  {stable} stable  "
        f"{healthy} healthy  {warming} warming up",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Accuracy analysis (EV-4 / G1 fix — now reads catch_up_runs.yaml)
# ---------------------------------------------------------------------------

def analyze_accuracy() -> dict[str, Any]:
    """Analyze catch-up accuracy from catch_up_runs.yaml (G1 fix: correct source)."""
    runs = _load_catch_up_runs()

    if not runs:
        # Legacy fallback: parse health-check.md yaml blocks
        hc = _load_health_check_yaml_blocks()
        accuracy = hc.get("accuracy_pulse", {})
        domain_accuracy = hc.get("per_domain_accuracy", {})
        legacy_runs = hc.get("catch_up_runs", [])
        if not isinstance(legacy_runs, list):
            legacy_runs = []
        total_proposed = sum(
            r.get("actions_proposed_this_session", 0)
            for r in legacy_runs if isinstance(r, dict)
        )
        result: dict[str, Any] = {
            "total_catch_ups": len(legacy_runs),
            "actions_proposed": total_proposed,
            "source": "legacy_health_check",
        }
        if accuracy:
            result["rolling_7d"] = {
                "acceptance_rate_pct": accuracy.get("acceptance_rate_pct"),
                "actions_proposed": accuracy.get("actions_proposed", 0),
            }
        if domain_accuracy:
            result["per_domain"] = domain_accuracy
        return result

    # EV-4: read from catch_up_runs.yaml
    total = len(runs)
    # Engagement rate stats
    rates = [r["engagement_rate"] for r in runs if r.get("engagement_rate") is not None]
    avg_rate = round(sum(rates) / len(rates), 4) if rates else None

    # Correction stats
    corrections = [r.get("correction_count", 0) or 0 for r in runs]
    avg_corrections = round(sum(corrections) / len(corrections), 2) if corrections else 0

    # Compliance / quality stats (EV-3 new fields)
    compliance_scores = [r["compliance_score"] for r in runs if r.get("compliance_score") is not None]
    quality_scores = [r["quality_score"] for r in runs if r.get("quality_score") is not None]

    # Rolling 7-day window
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent = [r for r in runs if r.get("timestamp", "") >= cutoff]
    recent_rates = [r["engagement_rate"] for r in recent if r.get("engagement_rate") is not None]

    result = {
        "total_catch_ups": total,
        "avg_engagement_rate": avg_rate,
        "avg_corrections_per_session": avg_corrections,
        "source": "catch_up_runs.yaml",
    }
    if compliance_scores:
        result["avg_compliance_score"] = round(sum(compliance_scores) / len(compliance_scores), 1)
    if quality_scores:
        result["avg_quality_score"] = round(sum(quality_scores) / len(quality_scores), 1)
    if recent:
        result["rolling_7d"] = {
            "runs": len(recent),
            "avg_engagement_rate": round(sum(recent_rates) / len(recent_rates), 4) if recent_rates else None,
        }
    return result


# ---------------------------------------------------------------------------
# Quality analysis (EV-4 / G2 — reads state/briefing_scores.json)
# ---------------------------------------------------------------------------

def analyze_quality(days: int = 30) -> dict[str, Any]:
    """Analyze briefing quality trend from state/briefing_scores.json.

    Returns per-dimension averages, trend, and regression detection.
    Requires eval_scorer.py to have been run at least once.
    """
    scores = _load_json(_BRIEFING_SCORES)
    if not scores:
        return {"error": "state/briefing_scores.json not found — run eval_scorer.py first"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [s for s in scores if s.get("timestamp", "") >= cutoff]
    if not recent:
        recent = scores[-10:]  # fall back to last 10 entries

    quality_vals = [s["quality_score"] for s in recent if s.get("quality_score") is not None]
    if not quality_vals:
        return {"error": "No quality_score values in briefing_scores.json"}

    avg_q = round(sum(quality_vals) / len(quality_vals), 1)
    trend = "insufficient_data"

    if len(quality_vals) >= 4:
        mid = len(quality_vals) // 2
        newer_avg = sum(quality_vals[mid:]) / len(quality_vals[mid:])
        older_avg = sum(quality_vals[:mid]) / mid
        if newer_avg < older_avg * 0.80:
            trend = "regressing"
        elif newer_avg > older_avg * 1.10:
            trend = "improving"
        else:
            trend = "stable"

    # Per-dimension averages
    dim_keys = ["actionability", "specificity", "completeness", "signal_purity", "calibration"]
    dimension_avgs: dict[str, float] = {}
    for k in dim_keys:
        vals = [s.get("dimensions", {}).get(k) for s in recent if s.get("dimensions", {}).get(k) is not None]
        if vals:
            dimension_avgs[k] = round(sum(vals) / len(vals), 1)

    # Compliance
    comp_vals = [s["compliance_score"] for s in recent if s.get("compliance_score") is not None]
    avg_compliance = round(sum(comp_vals) / len(comp_vals), 1) if comp_vals else None

    return {
        "scored_sessions": len(recent),
        "avg_quality_score": avg_q,
        "trend": trend,
        "dimension_averages": dimension_avgs,
        "avg_compliance_score": avg_compliance,
        "min_quality": min(quality_vals),
        "max_quality": max(quality_vals),
    }


# ---------------------------------------------------------------------------
# Log health analysis (EV-4 / G5 — reads tmp/log_digest.json)
# ---------------------------------------------------------------------------

def analyze_log_health() -> dict[str, Any]:
    """Read tmp/log_digest.json and surface connector error budgets + anomalies.

    Requires log_digest.py to have been run at least once.
    """
    if not _LOG_DIGEST.exists():
        return {"error": "tmp/log_digest.json not found — run log_digest.py first"}
    try:
        data = json.loads(_LOG_DIGEST.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return {"error": f"Cannot read log_digest.json: {exc}"}
    return data


# ---------------------------------------------------------------------------
# Memory health analysis (EV-11c — reads memory_pipeline_runs.jsonl)
# ---------------------------------------------------------------------------

def analyze_agent_metrics(agent_filter: str | None = None, days: int = 30) -> dict[str, Any]:
    """AR-9 — Read tmp/ext-agent-metrics.jsonl for agent invocation metrics."""
    records: list[dict] = []
    if _EXT_AGENT_METRICS.exists():
        try:
            for line in _EXT_AGENT_METRICS.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError as exc:
            return {"error": str(exc)}

    if not records:
        return {"agents": {}, "total_invocations": 0, "routing_decisions": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [r for r in records if r.get("timestamp", "") >= cutoff]

    by_agent: dict[str, dict] = {}
    routing_total = 0
    for r in recent:
        rtype = r.get("record_type", "invocation")
        if rtype == "routing_decision":
            routing_total += 1
            continue
        name = r.get("agent_name", "unknown")
        if agent_filter and name != agent_filter:
            continue
        a = by_agent.setdefault(name, {
            "invocations": 0, "successes": 0, "failures": 0,
            "total_latency_ms": 0, "quality_scores": [], "cache_hits": 0,
        })
        a["invocations"] += 1
        if r.get("success"):
            a["successes"] += 1
        else:
            a["failures"] += 1
        a["total_latency_ms"] += r.get("latency_ms", 0)
        if r.get("quality_score") is not None:
            a["quality_scores"].append(r["quality_score"])
        if r.get("cache_hit"):
            a["cache_hits"] += 1

    summary: dict[str, Any] = {}
    for name, a in sorted(by_agent.items()):
        inv = a["invocations"]
        qs = a["quality_scores"]
        summary[name] = {
            "invocations": inv,
            "success_rate": round(a["successes"] / inv, 3) if inv else 0.0,
            "avg_latency_ms": round(a["total_latency_ms"] / inv) if inv else 0,
            "avg_quality": round(sum(qs) / len(qs), 2) if qs else None,
            "cache_hit_rate": round(a["cache_hits"] / inv, 3) if inv else 0.0,
        }

    return {
        "agents": summary,
        "total_invocations": sum(a["invocations"] for a in summary.values()),
        "routing_decisions": routing_total,
        "period_days": days,
    }


def analyze_routing_audit(days: int = 7) -> dict[str, Any]:
    """AR-9 — Summarise routing decisions from tmp/ext-agent-metrics.jsonl."""
    records: list[dict] = []
    if _EXT_AGENT_METRICS.exists():
        try:
            for line in _EXT_AGENT_METRICS.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    decisions = [
        r for r in records
        if r.get("record_type") == "routing_decision"
        and r.get("timestamp", "") >= cutoff
    ]

    if not decisions:
        # Still compute keyword_miss_rate even with no routing_decision records
        margin_records_early = [
            r for r in records
            if r.get("record_type") == "routing_margin"
            and r.get("timestamp", "") >= cutoff
        ]
        miss_rates_early = []
        for r in margin_records_early:
            rq = r.get("routing_quality") or {}
            kmr = rq.get("keyword_miss_rate")
            if kmr is not None:
                miss_rates_early.append(float(kmr))
        kmr_val: float | None = None
        kmr_alert: str | None = None
        if miss_rates_early:
            kmr_val = sum(miss_rates_early) / len(miss_rates_early)
            try:
                import yaml as _yaml2  # type: ignore[import]
                _mem_yaml2 = _ARTHA_DIR / "config" / "memory.yaml"
                _mem_cfg2 = _yaml2.safe_load(_mem_yaml2.read_text(encoding="utf-8")) if _mem_yaml2.exists() else {}
                threshold2 = float(
                    (_mem_cfg2.get("upgrade_trigger") or {}).get("keyword_miss_rate_threshold", 0.10)
                )
            except Exception:  # noqa: BLE001
                threshold2 = 0.10
            if kmr_val > threshold2:
                kmr_alert = (
                    f"ALERT: keyword_miss_rate={kmr_val:.2%} exceeds threshold "
                    f"{threshold2:.2%} — consider TF-IDF vocabulary upgrade (EAR-4)"
                )
        return {
            "decisions": [],
            "total": 0,
            "keyword_miss_rate": kmr_val,
            "keyword_miss_rate_alert": kmr_alert,
        }

    by_agent: dict[str, int] = {}
    dispatched = 0
    declined = 0
    for d in decisions:
        agent = d.get("matched_agent", "none")
        by_agent[agent] = by_agent.get(agent, 0) + 1
        if d.get("dispatched"):
            dispatched += 1
        else:
            declined += 1

    # DEBT-020: compute 7-day rolling keyword_miss_rate from routing_margin records
    margin_records = [
        r for r in records
        if r.get("record_type") == "routing_margin"
        and r.get("timestamp", "") >= cutoff
    ]
    miss_rates = []
    for r in margin_records:
        rq = r.get("routing_quality") or {}
        kmr = rq.get("keyword_miss_rate")
        if kmr is not None:
            miss_rates.append(float(kmr))

    keyword_miss_rate: float | None = None
    miss_rate_alert: str | None = None
    if miss_rates:
        keyword_miss_rate = sum(miss_rates) / len(miss_rates)
        # Load threshold from memory.yaml (default 0.10)
        try:
            import yaml as _yaml  # type: ignore[import]
            _mem_yaml = _ARTHA_DIR / "config" / "memory.yaml"
            _mem_cfg = _yaml.safe_load(_mem_yaml.read_text(encoding="utf-8")) if _mem_yaml.exists() else {}
            threshold = float(
                (_mem_cfg.get("upgrade_trigger") or {}).get("keyword_miss_rate_threshold", 0.10)
            )
        except Exception:  # noqa: BLE001
            threshold = 0.10
        if keyword_miss_rate > threshold:
            miss_rate_alert = (
                f"ALERT: keyword_miss_rate={keyword_miss_rate:.2%} exceeds threshold "
                f"{threshold:.2%} — consider TF-IDF vocabulary upgrade (EAR-4)"
            )

    return {
        "total": len(decisions),
        "dispatched": dispatched,
        "declined": declined,
        "by_agent": dict(sorted(by_agent.items(), key=lambda x: x[1], reverse=True)),
        "recent": decisions[-10:],
        "period_days": days,
        "keyword_miss_rate": keyword_miss_rate,
        "keyword_miss_rate_alert": miss_rate_alert,
    }


def analyze_cache_report() -> dict[str, Any]:
    """AR-9 — Report cache sizes and hit rates from ext-agent-cache directory."""
    cache_dir = _ARTHA_DIR / "tmp" / "ext-agent-cache"
    result: dict[str, Any] = {"agents": {}, "total_files": 0, "total_bytes": 0}
    if not cache_dir.exists():
        return result
    for p in cache_dir.iterdir():
        if p.name == ".gitkeep":
            continue
        if p.is_dir():
            files = list(p.rglob("*"))
            size = sum(f.stat().st_size for f in files if f.is_file())
            result["agents"][p.name] = {"files": len(files), "bytes": size}
            result["total_files"] += len(files)
            result["total_bytes"] += size
        elif p.is_file():
            size = p.stat().st_size
            result["agents"][p.stem] = {"files": 1, "bytes": size}
            result["total_files"] += 1
            result["total_bytes"] += size

    # Merge hit rates from metrics file
    inv_data = analyze_agent_metrics()
    for name, stats in inv_data.get("agents", {}).items():
        if name in result["agents"]:
            result["agents"][name]["cache_hit_rate"] = stats.get("cache_hit_rate")

    return result


def _render_agent_metrics_report(
    data: dict[str, Any],
    agent_filter: str | None = None,
) -> str:
    """Render AR-9 agent metrics as text."""
    lines = ["━━ EXTERNAL AGENT METRICS (AR-9) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    agents = data.get("agents", {})
    if not agents:
        lines.append("  No agent invocation records found.")
        lines.append(f"  Metrics file: {_EXT_AGENT_METRICS}")
    else:
        lines.append(
            f"  {'Agent':<30} {'Inv':>5} {'Succ%':>7} {'AvgMs':>7} {'Qual':>6} {'Cache%':>7}"
        )
        lines.append("  " + "-" * 66)
        for name, s in sorted(agents.items()):
            qual = f"{s['avg_quality']:.1f}" if s["avg_quality"] is not None else "  N/A"
            lines.append(
                f"  {name:<30} {s['invocations']:>5} "
                f"{s['success_rate']*100:>6.1f}% "
                f"{s['avg_latency_ms']:>6}ms "
                f"{qual:>6} "
                f"{s['cache_hit_rate']*100:>6.1f}%"
            )
        lines.append("")
        lines.append(f"  Total invocations : {data['total_invocations']}")
        lines.append(f"  Routing decisions : {data['routing_decisions']}")
        lines.append(f"  Period            : {data.get('period_days', 30)} days")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _analyze_memory_health() -> dict[str, Any]:
    """Read ~/.artha-local/logs/memory_pipeline_runs.jsonl for memory pipeline stats."""
    if not _MEMORY_PIPELINE_RUNS.exists():
        return {"status": "no_data", "message": "memory_pipeline_runs.jsonl not found"}

    runs: list[dict] = []
    try:
        for line in _MEMORY_PIPELINE_RUNS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError as exc:
        return {"status": "error", "message": str(exc)}

    if not runs:
        return {"status": "empty", "message": "No records in memory_pipeline_runs.jsonl"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = [r for r in runs if r.get("timestamp", "") >= cutoff]
    total = len(recent)
    errors = sum(1 for r in recent if r.get("status") == "error")
    error_rate = round(errors / total, 4) if total else 0.0

    return {
        "status": "ok",
        "total_runs_30d": total,
        "error_count_30d": errors,
        "error_rate": error_rate,
        "last_run": runs[-1].get("timestamp") if runs else None,
    }


# ---------------------------------------------------------------------------
# Eval self-health check (EV-16b — checks for null score streaks)
# ---------------------------------------------------------------------------

def _check_eval_self_health() -> list[dict[str, Any]]:
    """Detect null quality_score streaks (EV-16b) and stale data conditions.

    Returns list of alert dicts. Empty list = healthy.
    """
    alerts: list[dict[str, Any]] = []

    # Check for null quality score streak
    scores = _load_json(_BRIEFING_SCORES)
    if scores:
        recent_10 = scores[-10:]
        null_streak = sum(1 for s in recent_10 if s.get("quality_score") is None)
        try:
            from lib.config_loader import load_config
            raw_cfg = load_config("artha_config.yaml")
            null_threshold = (
                raw_cfg.get("harness", {})
                .get("eval", {})
                .get("self_health", {})
                .get("null_score_threshold", 5)
            )
        except Exception:
            null_threshold = 5
        if null_streak >= null_threshold:
            alerts.append({
                "code": "EV-SH-01",
                "severity": "P1",
                "message": f"Quality score null for {null_streak} consecutive sessions",
            })

    # Check for stale catch_up_runs (no runs in 7 days)
    runs = _load_catch_up_runs()
    if runs:
        last_ts = sorted(r.get("timestamp", "") for r in runs if r.get("timestamp"))
        if last_ts:
            age_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            if last_ts[-1] < age_cutoff:
                alerts.append({
                    "code": "EV-SH-02",
                    "severity": "P2",
                    "message": f"No catch-up runs recorded in last 7 days (last: {last_ts[-1][:10]})",
                })

    return alerts


# ---------------------------------------------------------------------------
# Trust evidence (EV-11b)
# ---------------------------------------------------------------------------

def compute_trust_evidence(runs: list[dict] | None = None) -> dict[str, Any]:
    """Compute trust evidence score from outcome signals in catch_up_runs.yaml.

    Trust evidence = (items_resolved_24h + positive_coaching) /
                     max(sessions_with_signals, 1)
    Range 0.0–1.0. Requires outcome signal fields (EV-11a).
    """
    if runs is None:
        runs = _load_catch_up_runs()
    if not runs:
        return {"score": None, "message": "no_data"}

    total_resolved = sum(r.get("outcome_items_resolved_24h", 0) or 0 for r in runs)
    total_surfaced = sum(r.get("items_surfaced", 0) or 0 for r in runs)
    sessions_with_signals = sum(
        1 for r in runs
        if r.get("outcome_items_resolved_24h") is not None
    )

    score = round(total_resolved / max(total_surfaced, 1), 4) if total_surfaced else None
    return {
        "score": score,
        "total_resolved": total_resolved,
        "total_surfaced": total_surfaced,
        "sessions_with_signals": sessions_with_signals,
    }


# ---------------------------------------------------------------------------
# Self-model feedback (EV-10/11 — stale domain auto-downgrade)
# ---------------------------------------------------------------------------

def _eval_to_self_model_feedback(
    min_runs: int = 7,
    stale_streak: int = 3,
) -> dict[str, Any]:
    """Detect quality regressions and stale domains; return overlay suggestions.

    Called by briefing_adapter R9 and Step 6d of Artha.md.

    Returns a dict with:
      - trend: quality trend string ('regressing'|'improving'|'stable'|'insufficient_data')
      - stale_domains: list of domain names missing from last ``stale_streak`` runs
      - overlays: list of self-model overlay strings to prepend to domain context
      - should_apply: bool — True when caller should inject the overlays
    """
    runs = _load_catch_up_runs()
    quality = analyze_quality()
    trend = quality.get("trend", "insufficient_data")
    run_count = len(runs)

    stale_domains: list[str] = []
    if run_count >= stale_streak:
        # Reuse _detect_stale_domains from retrospective_view if available
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "retrospective_view",
                Path(__file__).resolve().parent / "retrospective_view.py",
            )
            _mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
            _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
            stale_domains = getattr(_mod, "_detect_stale_domains")(runs, streak_threshold=stale_streak)
        except Exception:
            pass

    overlays: list[str] = []

    # Quality regression overlay
    if trend == "regressing" and run_count >= min_runs:
        dim_avgs = quality.get("dimension_averages", {})
        worst_dims = sorted(dim_avgs, key=lambda k: dim_avgs[k])[:2]
        for dim in worst_dims:
            score = dim_avgs[dim]
            if score < 60:
                overlays.append(
                    f"[self-model overlay] {dim} quality is low ({score:.0f}/20) — "
                    f"increase specificity and concrete details."
                )

    # Stale domain overlay
    for domain in stale_domains:
        overlays.append(
            f"[self-model overlay] domain '{domain}' has been absent from the "
            f"last {stale_streak} briefings — proactively check {domain} state."
        )

    should_apply = bool(overlays) and (trend == "regressing" or bool(stale_domains))

    return {
        "trend": trend,
        "stale_domains": stale_domains,
        "overlays": overlays,
        "should_apply": should_apply,
    }


# ---------------------------------------------------------------------------
# Dashboard summary (EV-0d)
# ---------------------------------------------------------------------------

def print_summary(as_json: bool = False) -> dict[str, Any]:
    """Print a human-readable eval dashboard across all dimensions.

    When as_json=True, returns a compact dict and does not print.
    """
    runs = _load_catch_up_runs()
    scores = _load_json(_BRIEFING_SCORES)
    alerts = _check_eval_self_health()

    # Compute summary metrics
    quality_vals = [s["quality_score"] for s in scores[-10:] if s.get("quality_score") is not None]
    avg_quality = round(sum(quality_vals) / len(quality_vals), 1) if quality_vals else None
    session_count = len(runs)
    alert_count = len(alerts)

    compliance_vals = [s["compliance_score"] for s in scores[-10:] if s.get("compliance_score") is not None]
    avg_compliance = round(sum(compliance_vals) / len(compliance_vals), 1) if compliance_vals else None

    eng_rates = [r["engagement_rate"] for r in runs[-10:] if r.get("engagement_rate") is not None]
    avg_engagement = round(sum(eng_rates) / len(eng_rates), 3) if eng_rates else None

    summary = {
        "session_count": session_count,
        "avg_quality": avg_quality,
        "avg_compliance": avg_compliance,
        "avg_engagement_rate": avg_engagement,
        "alert_count": alert_count,
        "alerts": alerts,
    }

    if as_json:
        return summary

    # Human-readable dashboard
    lines = [
        "== ARTHA EVAL DASHBOARD ==================================",
        "",
        f"  Sessions tracked : {session_count}",
        f"  Avg quality score: {avg_quality if avg_quality is not None else 'N/A'} / 100",
        f"  Avg compliance   : {avg_compliance if avg_compliance is not None else 'N/A'} / 100",
        f"  Avg engagement   : {f'{avg_engagement:.1%}' if avg_engagement is not None else 'N/A'}",
        "",
    ]
    if alerts:
        lines.append(f"  [!] {alert_count} alert(s):")
        for a in alerts:
            lines.append(f"    [{a['severity']}] {a['code']}: {a['message']}")
    else:
        lines.append("  [OK] No eval alerts")
    lines += ["", "=========================================================="]
    print("\n".join(lines))
    return summary


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


def _count_stub_signals() -> tuple[int, int]:
    """RD-04: Count stub signals in config/signal_routing.yaml.

    Returns (stub_count, p0_count) where:
      - stub_count: total signals with status == "stub"
      - p0_count:   stub signals also flagged as priority P0
    """
    try:
        import yaml as _yaml  # noqa: PLC0415
        _routing_path = Path(__file__).resolve().parent.parent / "config" / "signal_routing.yaml"
        if not _routing_path.exists():
            return 0, 0
        routing = _yaml.safe_load(_routing_path.read_text(encoding="utf-8")) or {}
        # Signal types are top-level keys (not nested under a 'signals' sub-key)
        stub_count = 0
        p0_count = 0
        for _name, cfg in routing.items():
            if not isinstance(cfg, dict):
                continue
            if cfg.get("status") == "stub":
                stub_count += 1
                if str(cfg.get("priority", "")).upper() == "P0":
                    p0_count += 1
        return stub_count, p0_count
    except Exception:  # noqa: BLE001
        return 0, 0


def analyze_pipeline() -> dict[str, Any]:
    """RD-43: Analyze signal pipeline funnel metrics.

    Loads tmp/signal_metrics.json and tmp/orchestrator_metrics.json written
    by email_signal_extractor.py and action_orchestrator.py respectively.

    Returns:
        Dict with conversion_rate, idempotency_hit_rate, signals_by_type,
        orphan_alert (True if any signal type has 100% orphan rate).
    """
    import json as _json  # noqa: PLC0415
    _artha_dir = Path(__file__).resolve().parent.parent
    _tmp = _artha_dir / "tmp"

    sig_metrics: dict[str, Any] = {}
    orch_metrics: dict[str, Any] = {}

    try:
        sig_path = _tmp / "signal_metrics.json"
        if sig_path.exists():
            sig_metrics = _json.loads(sig_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    try:
        orch_path = _tmp / "orchestrator_metrics.json"
        if orch_path.exists():
            orch_metrics = _json.loads(orch_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    if not sig_metrics and not orch_metrics:
        return {"status": "no_data", "note": "Run pipeline first to generate metrics"}

    signals_in = int(orch_metrics.get("signals_in", sig_metrics.get("signals_extracted", 0)))
    proposals_queued = int(orch_metrics.get("proposals_queued", 0))
    suppressed = int(orch_metrics.get("proposals_suppressed_duplicates", 0))

    conversion_rate = round(proposals_queued / signals_in, 3) if signals_in > 0 else 0.0
    orphan_rate = round(1.0 - conversion_rate, 3) if signals_in > 0 else 1.0

    signals_by_type: dict[str, int] = sig_metrics.get("signals_by_type", {})

    result: dict[str, Any] = {
        "run_at": orch_metrics.get("run_at") or sig_metrics.get("run_at"),
        "emails_processed": sig_metrics.get("emails_processed", 0),
        "signals_extracted": sig_metrics.get("signals_extracted", 0),
        "signals_in_orchestrator": signals_in,
        "proposals_queued": proposals_queued,
        "proposals_suppressed": suppressed,
        "conversion_rate": conversion_rate,
        "orphan_rate": orphan_rate,
        "signals_by_type": signals_by_type,
        # Alert: 100% orphan rate means zero proposals for any extracted signals
        "orphan_alert": signals_in > 0 and proposals_queued == 0,
    }

    # RD-04: Stub signal inventory — track unimplemented signal coverage gaps
    stub_count, p0_count = _count_stub_signals()
    result["stub_signal_count"] = stub_count
    result["stub_p0_count"] = p0_count

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

def _run_sla_assertion(as_json: bool = False) -> int:
    """DEBT-EVAL-002: Load config/sla.yaml, evaluate metrics, exit 2 if any SLA breached.

    Returns:
        0 — all SLA targets met
        2 — one or more targets breached
        1 — sla.yaml missing or unreadable
    """
    _ARTHA_DIR_LOCAL = Path(__file__).resolve().parent.parent
    sla_path = _ARTHA_DIR_LOCAL / "config" / "sla.yaml"
    if not sla_path.exists():
        msg = {"status": "ERROR", "error": f"config/sla.yaml not found at {sla_path}"}
        if as_json:
            print(json.dumps(msg, indent=2))
        else:
            print(f"[SLA] ERROR: config/sla.yaml not found — cannot assert SLA (DEBT-EVAL-002)")
        return 1
    try:
        import yaml as _yaml_sla
        sla_cfg = _yaml_sla.safe_load(sla_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[SLA] ERROR: failed to parse config/sla.yaml: {exc}")
        return 1

    targets = sla_cfg.get("targets", {})
    breaches: list[dict] = []
    results: list[dict] = []

    # Evaluate each defined target
    for metric_name, target in targets.items():
        threshold   = target.get("threshold")
        comparator  = target.get("comparator", "lte")  # lte | gte | lt | gt | eq
        description = target.get("description", metric_name)
        source      = target.get("source")     # which analyze_* function to call
        field       = target.get("field")      # dotted path into result dict

        if threshold is None or not source or not field:
            continue

        try:
            # Map source → function
            _SOURCE_MAP = {
                "performance": lambda: analyze_performance(7),
                "accuracy":    analyze_accuracy,
                "freshness":   analyze_freshness,
            }
            fn = _SOURCE_MAP.get(source)
            if fn is None:
                continue
            data = fn()
            # Traverse dotted field path
            val: object = data
            for part in field.split("."):
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break
            if val is None:
                results.append({"metric": metric_name, "status": "NO_DATA", "description": description})
                continue

            actual = float(val)
            _CMP = {
                "lte": actual <= threshold,
                "gte": actual >= threshold,
                "lt":  actual < threshold,
                "gt":  actual > threshold,
                "eq":  actual == threshold,
            }
            passed = _CMP.get(comparator, True)
            entry = {
                "metric": metric_name,
                "status": "PASS" if passed else "BREACH",
                "actual": actual,
                "threshold": threshold,
                "comparator": comparator,
                "description": description,
            }
            results.append(entry)
            if not passed:
                breaches.append(entry)
        except Exception as exc:
            results.append({"metric": metric_name, "status": "ERROR", "error": str(exc)})

    report = {"sla_results": results, "breaches": len(breaches), "overall": "PASS" if not breaches else "BREACH"}
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        status_icon = "✓" if not breaches else "✗"
        print(f"[SLA] {status_icon} Overall: {report['overall']} — {len(results)} metrics checked, {len(breaches)} breach(es)")
        for r in results:
            icon = {"PASS": "  ✓", "BREACH": "  ✗", "NO_DATA": "  ?", "ERROR": "  !"}.get(r["status"], "  ?")
            if r["status"] in ("PASS", "BREACH"):
                print(f"{icon} {r['metric']}: actual={r.get('actual')} {r.get('comparator')} {r.get('threshold')} [{r['status']}]")
            else:
                print(f"{icon} {r['metric']}: {r.get('error', r['status'])}")

    return 2 if breaches else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Artha catch-up evaluation & performance analyzer"
    )
    parser.add_argument("--perf", action="store_true", help="Performance analysis only")
    parser.add_argument("--accuracy", action="store_true", help="Accuracy analysis only")
    parser.add_argument("--freshness", action="store_true", help="Freshness analysis only")
    parser.add_argument("--skills", action="store_true", help="Skill health table (reads state/skills_cache.json)")
    parser.add_argument("--quality", action="store_true", help="Quality score analysis (reads state/briefing_scores.json)")
    parser.add_argument("--log-health", action="store_true", help="Log digest + anomaly report (reads tmp/log_digest.json)")
    parser.add_argument("--memory", action="store_true", help="Memory pipeline health analysis")
    parser.add_argument("--pipeline", action="store_true", help="RD-43: Signal pipeline funnel metrics (conversion rate, orphan rate)")
    parser.add_argument("--summary", action="store_true", help="Dashboard summary across all dimensions")
    parser.add_argument("--agents", action="store_true", help="AR-9 external agent invocation metrics")
    parser.add_argument("--agent", metavar="NAME", help="AR-9 filter metrics to a specific agent")
    parser.add_argument("--routing-audit", action="store_true", help="AR-9 routing decision log summary")
    parser.add_argument("--cache-report", action="store_true", help="AR-9 ext-agent-cache size and hit rates")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--trend", type=int, default=7, metavar="DAYS", help="Trend window (default: 7)")
    parser.add_argument(
        "--assert-sla",
        action="store_true",
        help="DEBT-EVAL-002: enforce SLA targets from config/sla.yaml; exit 2 if any target breached",
    )
    args = parser.parse_args(argv)

    # --assert-sla: enforce SLA targets (DEBT-EVAL-002)
    if args.assert_sla:
        exit_code = _run_sla_assertion(as_json=args.json)
        return exit_code

    # --agents / --agent: AR-9 external agent invocation metrics
    if args.agents or args.agent:
        data = analyze_agent_metrics(agent_filter=args.agent, days=args.trend)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(_render_agent_metrics_report(data, agent_filter=args.agent))
        return 0

    # --routing-audit: AR-9 routing decision log
    if args.routing_audit:
        data = analyze_routing_audit(days=args.trend)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            lines = ["━━ ROUTING AUDIT (AR-9) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            if not data["total"]:
                lines.append("  No routing decisions recorded.")
            else:
                lines.append(f"  Total decisions : {data['total']}  (last {data['period_days']}d)")
                lines.append(f"  Dispatched      : {data['dispatched']}")
                lines.append(f"  Declined        : {data['declined']}")
                kmr = data.get("keyword_miss_rate")
                if kmr is not None:
                    lines.append(f"  Keyword miss rt : {kmr:.2%}")
                alert = data.get("keyword_miss_rate_alert")
                if alert:
                    lines.append(f"  ⚠  {alert}")
                for agent, count in data.get("by_agent", {}).items():
                    lines.append(f"    {agent:<32}: {count}")
                if data.get("recent"):
                    lines.append("")
                    lines.append("  Recent decisions (last 10):")
                    for d in data["recent"]:
                        ts = d.get("timestamp", "?")[:16]
                        agent = d.get("matched_agent", "?")[:28]
                        conf = d.get("confidence", 0)
                        act = "DISPATCH" if d.get("dispatched") else "declined"
                        lines.append(f"    [{ts}] {agent:<28} conf={conf:.2f}  {act}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("\n".join(lines))
        return 0

    # --cache-report: AR-9 cache sizes and hit rates
    if args.cache_report:
        data = analyze_cache_report()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            lines = ["━━ AGENT CACHE REPORT (AR-9) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            agents = data.get("agents", {})
            if not agents:
                lines.append("  No cached data found.")
            else:
                lines.append(f"  {'Agent':<30} {'Files':>6} {'Size':>10} {'Cache%':>8}")
                lines.append("  " + "-" * 56)
                for name, info in sorted(agents.items()):
                    size_kb = round(info["bytes"] / 1024, 1)
                    hit = info.get("cache_hit_rate")
                    hit_str = f"{hit*100:.1f}%" if hit is not None else "   N/A"
                    lines.append(f"  {name:<30} {info['files']:>6} {size_kb:>8}KB {hit_str:>8}")
                lines.append("")
                lines.append(f"  Total: {data['total_files']} files, {round(data['total_bytes']/1024,1)}KB")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("\n".join(lines))
        return 0

    # --skills is a standalone mode
    if args.skills:
        data = analyze_skills()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(render_skills_table(data))
        return 0

    # --summary: dashboard mode
    if args.summary:
        result = print_summary(as_json=args.json)
        if args.json:
            print(json.dumps(result, indent=2))
        return 0

    # --quality: briefing quality trend
    if args.quality:
        data = analyze_quality(days=args.trend)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            lines = ["━━ QUALITY ANALYSIS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            if "error" in data:
                lines.append(f"  ⚠ {data['error']}")
            else:
                lines.append(f"  Sessions scored  : {data['scored_sessions']}")
                lines.append(f"  Avg quality score: {data['avg_quality_score']} / 100  ({data['trend']})")
                if data.get("avg_compliance_score") is not None:
                    lines.append(f"  Avg compliance   : {data['avg_compliance_score']} / 100")
                lines.append(f"  Range            : {data['min_quality']}–{data['max_quality']}")
                if data.get("dimension_averages"):
                    lines.append("")
                    lines.append("  Dimension averages:")
                    for k, v in data["dimension_averages"].items():
                        lines.append(f"    {k:<16}: {v}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("\n".join(lines))
        return 0

    # --log-health: log digest anomaly report
    if args.log_health:
        data = analyze_log_health()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            lines = ["━━ LOG HEALTH ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            if "error" in data:
                lines.append(f"  ⚠ {data['error']}")
            else:
                anomalies = data.get("anomalies", [])
                connectors = data.get("connectors", {})
                lines.append(f"  Error budget: {data.get('error_budget_pct', '?')}% (target <5%)")
                lines.append(f"  Anomalies   : {len(anomalies)}")
                for a in anomalies:
                    lines.append(f"    ⚠ [{a.get('type', 'unknown')}] {a.get('connector', '?')}: {a.get('message', '')}")
                if connectors:
                    lines.append("")
                    lines.append("  Connector stats (p95 latency / error rate):")
                    for cname, stats in sorted(connectors.items()):
                        lines.append(f"    {cname:<24}: p95={stats.get('p95_ms', '?')}ms  err={stats.get('error_rate_pct', '?')}%")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("\n".join(lines))
        return 0

    # --memory: memory pipeline health
    if args.memory:
        data = _analyze_memory_health()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            lines = ["━━ MEMORY HEALTH ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            lines.append(f"  Status       : {data.get('status', 'unknown')}")
            if data.get("total_runs_30d") is not None:
                lines.append(f"  Runs (30d)   : {data['total_runs_30d']}")
                lines.append(f"  Errors (30d) : {data['error_count_30d']}  ({data['error_rate']:.1%})")
                lines.append(f"  Last run     : {data.get('last_run', 'N/A')}")
            else:
                lines.append(f"  {data.get('message', '')}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("\n".join(lines))
        return 0

    perf = analyze_performance(args.trend) if not args.accuracy and not args.freshness else {}
    accuracy = analyze_accuracy() if not args.perf and not args.freshness else {}
    freshness = analyze_freshness() if not args.perf and not args.accuracy else {}

    # --pipeline: RD-43 signal funnel metrics
    if args.pipeline:
        data = analyze_pipeline()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            lines = ["━━ SIGNAL PIPELINE METRICS (RD-43) ━━━━━━━━━━━━━━━━━━━━━━━━"]
            if data.get("status") == "no_data":
                lines.append("  No metrics data — run pipeline first.")
            else:
                lines.append(f"  Emails processed : {data.get('emails_processed', '?')}")
                lines.append(f"  Signals extracted: {data.get('signals_extracted', '?')}")
                lines.append(f"  Proposals queued : {data.get('proposals_queued', '?')}")
                lines.append(f"  Suppressed dupes : {data.get('proposals_suppressed', '?')}")
                lines.append(f"  Conversion rate  : {data.get('conversion_rate', 0):.1%}")
                lines.append(f"  Orphan rate      : {data.get('orphan_rate', 0):.1%}")
                if data.get("orphan_alert"):
                    lines.append("  ⚠  ORPHAN ALERT: All signals produced zero proposals — check signal routing")
                by_type = data.get("signals_by_type", {})
                if by_type:
                    lines.append("")
                    lines.append("  Signals by type:")
                    for stype, count in sorted(by_type.items()):
                        lines.append(f"    {stype:<32}: {count}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("\n".join(lines))
        return 0

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
