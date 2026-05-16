# F-C1: merged signal_scorer.py + metrics_digest.py → metrics_analysis.py (re-artha.md). Shims removed after 2026-06-16.

#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/signal_scorer.py — Composite signal scoring for Artha.

Replaces flat signal enumeration in catch-up Step 8 with a composite score
based on urgency, impact, and freshness (a recency-decayed component).

Score formula::

    score = urgency_weight * (urgency / 5)
          + impact_weight  * (impact  / 5)
          + freshness_weight * max(0, 1.0 - decay_per_day * age_days)

Signals scoring below ``suppress_below`` are noise and filtered before
briefing assembly.  Signals scoring ``promote_above`` or higher are marked
for the briefing header with an alert emoji.

Default thresholds are intentionally conservative pending empirical
calibration against the ``briefings/`` archive.  Run calibration first and
record results in ``tmp/signal_scorer_calibration.json`` before tightening
the thresholds.

Spec: specs/agent-fw.md §AFW-9 — Composite Signal Scoring
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defaults — all overridable via config/artha_config.yaml → signal_scorer
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[str, float] = {
    "urgency_weight": 0.4,
    "impact_weight": 0.4,
    "freshness_weight": 0.2,
    "freshness_decay_per_day": 0.1,
}

# Thresholds calibrated against briefings/ archive (27 files, 712 signals).
# See specs/agent-fw.md §3.9.2 and tmp/signal_scorer_calibration.json.
# suppress_below=0.2 CONFIRMED — no critical signals suppressed at this level.
# promote_above=0.66 CALIBRATED from p90 of score distribution (3.2% reach 0.8).
_DEFAULT_SUPPRESS_BELOW: float = 0.2
_DEFAULT_PROMOTE_ABOVE: float = 0.66


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_scorer_config(artha_dir: Path | None = None) -> dict[str, Any]:
    """Load ``signal_scorer`` section from ``config/artha_config.yaml``.

    Returns the ``signal_scorer`` sub-dict, or ``{}`` on any failure.
    """
    try:
        import sys  # noqa: PLC0415

        _root = artha_dir if artha_dir is not None else Path(__file__).resolve().parent.parent.parent
        _scripts = str(_root / "scripts")
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from lib.config_loader import load_config  # noqa: PLC0415

        cfg = load_config("artha_config")
        return cfg.get("signal_scorer", {})
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_signal(
    signal: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> float:
    """Compute the composite score for a single signal dict.

    Args:
        signal: Dict with optional keys:
            ``urgency`` (int 1–5, default 1),
            ``impact``  (int 1–5, default 1),
            ``age_days`` (int ≥ 0, default 0).
        weights: Mapping that overrides one or more of the keys in
            :data:`_DEFAULT_WEIGHTS`.  ``None`` uses defaults only.

    Returns:
        Float in ``[0.0, 1.0]``.
    """
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}
    urgency = float(signal.get("urgency", 1)) / 5.0
    impact = float(signal.get("impact", 1)) / 5.0
    age_days = float(signal.get("age_days", 0))
    freshness = max(0.0, 1.0 - w["freshness_decay_per_day"] * age_days)
    return (
        w["urgency_weight"] * urgency
        + w["impact_weight"] * impact
        + w["freshness_weight"] * freshness
    )


def rank_signals(
    signals: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
    artha_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Score and sort signals highest-first.

    Each output dict is a shallow copy of the input with a ``_score`` key
    added.  Original dicts are not mutated.

    Args:
        signals: List of signal dicts (see :func:`score_signal`).
        weights: Optional weight overrides (merged on top of config).
        artha_dir: Artha project root for config loading (``None`` → auto).

    Returns:
        New list sorted by ``_score`` descending.
    """
    cfg = _load_scorer_config(artha_dir)
    merged_weights = {**_DEFAULT_WEIGHTS, **cfg.get("weights", {}), **(weights or {})}

    scored: list[dict[str, Any]] = []
    for sig in signals:
        entry = dict(sig)
        entry["_score"] = score_signal(sig, merged_weights)
        scored.append(entry)
    scored.sort(key=lambda s: s["_score"], reverse=True)
    return scored


def partition_signals(
    signals: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
    suppress_below: float | None = None,
    promote_above: float | None = None,
    artha_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition signals into (promoted, normal, suppressed) buckets.

    * **promoted** — ``_score >= promote_above`` → briefing header alert.
    * **normal**   — ``suppress_below <= _score < promote_above`` → standard.
    * **suppressed** — ``_score < suppress_below`` → noise, excluded from briefing.

    Each returned list is sorted by ``_score`` descending.

    Args:
        signals: Input signal dicts.
        weights: Optional weight overrides.
        suppress_below: Threshold below which signals are suppressed.
            ``None`` reads from config, falls back to :data:`_DEFAULT_SUPPRESS_BELOW`.
        promote_above: Threshold at or above which signals are promoted.
            ``None`` reads from config, falls back to :data:`_DEFAULT_PROMOTE_ABOVE`.
        artha_dir: Artha project root.

    Returns:
        Three lists: ``(promoted, normal, suppressed)``.
    """
    cfg = _load_scorer_config(artha_dir)
    supp = (
        suppress_below
        if suppress_below is not None
        else cfg.get("suppress_below", _DEFAULT_SUPPRESS_BELOW)
    )
    prom = (
        promote_above
        if promote_above is not None
        else cfg.get("promote_above", _DEFAULT_PROMOTE_ABOVE)
    )

    ranked = rank_signals(signals, weights=weights, artha_dir=artha_dir)

    promoted: list[dict[str, Any]] = []
    normal: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    for sig in ranked:
        s = sig["_score"]
        if s >= prom:
            promoted.append(sig)
        elif s >= supp:
            normal.append(sig)
        else:
            suppressed.append(sig)

    return promoted, normal, suppressed


# ---------------------------------------------------------------------------
# Merged from metrics_digest.py
# ---------------------------------------------------------------------------

# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/metrics_digest.py — Weekly JSONL aggregation for agent fleet (EAR digest).

Aggregates metrics from:
  - tmp/ext-agent-metrics.jsonl        (invocation records)
  - tmp/ext-agent-trace.jsonl          (full invocation traces)
  - tmp/ext-agent-health/*.jsonl       (agent health shards)

Output:
  - state/work/ext-agent-health-digest.md   (human-readable Markdown)
  - tmp/ext-agent-digest-<YYYY-WNN>.json    (machine-readable JSON)

Called by precompute.py on cron schedule, or standalone:
  python scripts/lib/metrics_digest.py [--weeks 1]

Design:
  - Pure stdlib, no pandas.
  - Reads only lines written in the target week window.
  - All I/O is append-friendly; digest is not idempotent (re-run re-generates).
  - Output is safe to publish in a morning briefing.

Ref: specs/ext-agent-reloaded.md §EAR digest
"""

import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent.parent
_METRICS_FILE = _REPO_ROOT / "tmp" / "ext-agent-metrics.jsonl"
_TRACE_FILE = _REPO_ROOT / "tmp" / "ext-agent-trace.jsonl"
_HEALTH_DIR = _REPO_ROOT / "tmp" / "ext-agent-health"
_OUT_MD = _REPO_ROOT / "state" / "work" / "ext-agent-health-digest.md"
_OUT_JSON_DIR = _REPO_ROOT / "tmp"

# ---------------------------------------------------------------------------
# JSON-L reader (generator, memory-efficient)
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path, since: datetime) -> list[dict]:
    """Read JSONL records written on or after `since` (UTC)."""
    records = []
    if not path.exists():
        return records
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts_str = rec.get("ts") or rec.get("timestamp", "")
                    if ts_str:
                        ts_str = ts_str.replace("Z", "+00:00")
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= since:
                            records.append(rec)
                    else:
                        records.append(rec)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return records


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------

def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


# ---------------------------------------------------------------------------
# Core digest computation
# ---------------------------------------------------------------------------

def _compute_digest(weeks: int = 1) -> dict:
    """Build the digest dict for the last `weeks` ISO weeks."""
    since = datetime.now(timezone.utc) - timedelta(weeks=weeks)

    # --- Metrics: quality + latency per agent ---
    metrics_records = _read_jsonl(_METRICS_FILE, since)
    quality_by_agent: dict[str, list[float]] = defaultdict(list)
    latency_by_agent: dict[str, list[float]] = defaultdict(list)
    error_count: dict[str, int] = defaultdict(int)
    invocation_count: dict[str, int] = defaultdict(int)

    for rec in metrics_records:
        agent = rec.get("agent_name", "")
        if not agent:
            continue
        invocation_count[agent] += 1
        quality = rec.get("quality_score")
        latency = rec.get("latency_ms")
        success = rec.get("success", True)
        if not success:
            error_count[agent] += 1
        if isinstance(quality, (int, float)):
            quality_by_agent[agent].append(float(quality))
        if isinstance(latency, (int, float)):
            latency_by_agent[agent].append(float(latency))

    # --- Routing margin distribution ---
    margin_bins = {"0.0-0.1": 0, "0.1-0.3": 0, "0.3-0.6": 0, "0.6-1.0": 0}
    for rec in metrics_records:
        if rec.get("record_type") == "routing_margin":
            margin = rec.get("confidence_margin", 0.0)
            if margin < 0.1:
                margin_bins["0.0-0.1"] += 1
            elif margin < 0.3:
                margin_bins["0.1-0.3"] += 1
            elif margin < 0.6:
                margin_bins["0.3-0.6"] += 1
            else:
                margin_bins["0.6-1.0"] += 1

    # --- Aggregate per-agent stats ---
    agents_data: dict[str, dict] = {}
    all_agents = set(invocation_count) | set(quality_by_agent)
    for agent in all_agents:
        q_list = sorted(quality_by_agent[agent])
        l_list = sorted(latency_by_agent[agent])
        count = invocation_count[agent]
        errs = error_count[agent]
        agents_data[agent] = {
            "invocations": count,
            "errors": errs,
            "error_rate": round(errs / max(count, 1), 3),
            "avg_quality": round(sum(q_list) / len(q_list), 3) if q_list else 0.0,
            "avg_latency_ms": round(sum(l_list) / len(l_list), 1) if l_list else 0.0,
            "p95_latency_ms": round(_percentile(l_list, 95), 1) if l_list else 0.0,
        }

    # Fleet-wide summaries
    all_q = [v for lst in quality_by_agent.values() for v in lst]
    all_l = sorted([v for lst in latency_by_agent.values() for v in lst])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_weeks": weeks,
        "fleet": {
            "total_invocations": sum(invocation_count.values()),
            "total_errors": sum(error_count.values()),
            "fleet_avg_quality": round(sum(all_q) / len(all_q), 3) if all_q else 0.0,
            "fleet_p95_latency_ms": round(_percentile(all_l, 95), 1) if all_l else 0.0,
        },
        "routing": {
            "confidence_margin_distribution": margin_bins,
        },
        "agents": agents_data,
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _render_markdown(digest: dict) -> str:
    now = digest.get("generated_at", "")[:19].replace("T", " ")
    weeks = digest.get("window_weeks", 1)
    fleet = digest.get("fleet", {})
    agents = digest.get("agents", {})
    routing = digest.get("routing", {})

    lines = [
        f"# Agent Fleet Health Digest",
        f"_Generated: {now} UTC | Window: last {weeks} week(s)_",
        "",
        "## Fleet Summary",
        f"- **Total invocations:** {fleet.get('total_invocations', 0)}",
        f"- **Total errors:** {fleet.get('total_errors', 0)}",
        f"- **Fleet avg quality:** {fleet.get('fleet_avg_quality', 0):.3f}",
        f"- **Fleet p95 latency:** {fleet.get('fleet_p95_latency_ms', 0):.0f} ms",
        "",
    ]

    # Routing margin distribution
    margin_bins = routing.get("confidence_margin_distribution", {})
    if any(v > 0 for v in margin_bins.values()):
        lines.append("## Routing Confidence Margin Distribution")
        for bucket, count in margin_bins.items():
            lines.append(f"  - {bucket}: {count}")
        lines.append("")

    # Per-agent table
    if agents:
        lines.append("## Per-Agent Metrics")
        lines.append("")
        lines.append(f"| {'Agent':<35} | {'Inv':>5} | {'Err':>5} | {'Err%':>6} | {'AvgQ':>6} | {'AvgLat':>8} | {'p95Lat':>8} |")
        lines.append(f"|{'-'*37}|{'-'*7}|{'-'*7}|{'-'*8}|{'-'*8}|{'-'*10}|{'-'*10}|")
        for agent, stats in sorted(agents.items()):
            lines.append(
                f"| {agent:<35} "
                f"| {stats.get('invocations', 0):>5} "
                f"| {stats.get('errors', 0):>5} "
                f"| {stats.get('error_rate', 0)*100:>5.1f}% "
                f"| {stats.get('avg_quality', 0):>6.3f} "
                f"| {stats.get('avg_latency_ms', 0):>7.0f}ms "
                f"| {stats.get('p95_latency_ms', 0):>7.0f}ms |"
            )
        lines.append("")

    lines.append("---")
    lines.append("_Digest produced by scripts/lib/metrics_digest.py (EAR fleet monitor)_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write outputs
# ---------------------------------------------------------------------------

def run_digest(weeks: int = 1) -> dict:
    """Compute and write the fleet digest.  Returns the digest dict."""
    digest = _compute_digest(weeks=weeks)

    # Write JSON
    week_label = datetime.now(timezone.utc).strftime("%G-W%V")
    json_out = _OUT_JSON_DIR / f"ext-agent-digest-{week_label}.json"
    try:
        _OUT_JSON_DIR.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=_OUT_JSON_DIR, prefix=".digest_tmp_", suffix=".json"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(digest, fh, indent=2)
        os.replace(tmp_path, json_out)
    except Exception:
        pass

    # Write Markdown
    md_content = _render_markdown(digest)
    try:
        _OUT_MD.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=_OUT_MD.parent, prefix=".digest_tmp_", suffix=".md"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(md_content)
        os.replace(tmp_path, _OUT_MD)
    except Exception:
        pass

    return digest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate agent fleet metrics digest")
    parser.add_argument("--weeks", type=int, default=1,
                        help="Number of weeks to include (default: 1)")
    args = parser.parse_args()

    digest = run_digest(weeks=args.weeks)
    print(f"Digest written →  {_OUT_MD}")
    print(f"  Fleet invocations: {digest['fleet'].get('total_invocations', 0)}")
    print(f"  Fleet avg quality: {digest['fleet'].get('fleet_avg_quality', 0):.3f}")
