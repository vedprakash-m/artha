#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/slo_engine.py — SLO Engine + Error Budget tracking (ST-04).

Registers and evaluates 4 SLOs against data from CatchUpMetrics and CostGuard.
All implementations use stdlib dataclasses (no Pydantic per §14.3 R8).

## Registered SLOs

| ID                    | Type           | Target | Window |
|-----------------------|----------------|--------|--------|
| briefing_accuracy     | ACCURACY       | 0.85   | 7d     |
| pipeline_latency      | RESPONSE_TIME  | 120s   | 24h    |
| cost_per_briefing     | COST_PER_INFER | $0.50  | 24h    |
| connector_success     | SAFETY         | 0.90   | 24h    |

## Design
- Consumes data from scripts/lib/metrics.py (CatchUpMetrics snapshots) and
  scripts/lib/cost_guard.py (CostGuard) via dependency injection.
- Persists SLO status to state/health-check.md SLO section after each run.
- preflight.py alerts if burn_rate > 1 for any SLO.

Ref: specs/steal.md §14.1 ST-04
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# SLO type identifiers
SLO_ACCURACY = "ACCURACY"
SLO_RESPONSE_TIME = "RESPONSE_TIME"
SLO_COST_PER_INFER = "COST_PER_INFER"
SLO_SAFETY = "SAFETY"  # repurposed for connector success rate


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SLODefinition:
    """Definition of a single SLO."""
    slo_id: str
    slo_type: str         # SLO_ACCURACY | SLO_RESPONSE_TIME | SLO_COST_PER_INFER | SLO_SAFETY
    target: float         # threshold to meet (direction depends on slo_type)
    window_seconds: float  # rolling window in seconds
    description: str = ""


@dataclass
class SLOStatus:
    """Evaluated status of a single SLO."""
    slo_id: str
    slo_type: str
    target: float
    current_value: float   # latest measured value (None → no data)
    met: bool              # True if SLO is currently met
    error_budget_remaining: float   # fraction remaining [0.0, 1.0]
    burn_rate: float       # >1 means budget exhausted before window ends
    sample_count: int      # number of data points in the window
    window_seconds: float


@dataclass
class SLOReport:
    """Full SLO report returned by SLOEngine.status()."""
    timestamp: str
    slos: dict[str, SLOStatus]  # keyed by slo_id


# ---------------------------------------------------------------------------
# Error budget helper
# ---------------------------------------------------------------------------


def compute_error_budget(
    values: list[float],
    target: float,
    slo_type: str,
) -> tuple[float, float]:
    """Compute (error_budget_remaining, burn_rate) from a list of measured values.

    For ACCURACY / SAFETY: values are success rates [0.0, 1.0].
      - error is: value < target
      - budget = fraction of window where SLO was MET
    For RESPONSE_TIME / COST_PER_INFER: values are durations/costs.
      - error is: value > target
      - budget = fraction of window where SLO was MET

    Returns:
        error_budget_remaining: float in [0.0, 1.0] (1.0 = full budget)
        burn_rate: >1 means budget will exhaust before window ends
    """
    if not values:
        return (1.0, 0.0)

    if slo_type in (SLO_ACCURACY, SLO_SAFETY):
        met = [v >= target for v in values]
    else:
        met = [v <= target for v in values]

    total = len(met)
    met_count = sum(met)
    error_count = total - met_count

    # error_budget_remaining: budget starts at (1 - target).
    # For ACCURACY target=0.85: allowed error fraction = 0.15 = 15% of window
    # Remaining = max(0, (allowed_errors - observed_errors) / allowed_errors)
    if slo_type in (SLO_ACCURACY, SLO_SAFETY):
        allowed_error_fraction = 1.0 - target
    else:
        # For latency/cost: treat "allowed miss rate" as 10% (i.e. 90% SLO on timing)
        allowed_error_fraction = 0.10

    if allowed_error_fraction <= 0:
        budget_remaining = 1.0 if error_count == 0 else 0.0
        burn_rate = 0.0 if error_count == 0 else float("inf")
        return (budget_remaining, burn_rate)

    allowed_errors = allowed_error_fraction * total
    remaining = max(0.0, (allowed_errors - error_count) / allowed_errors)
    # burn_rate: how fast are we burning? 1.0 = exactly on track to exhaust by end of window
    # burn_rate = error_rate / allowed_error_fraction
    observed_error_rate = error_count / total if total > 0 else 0.0
    burn_rate = round(observed_error_rate / allowed_error_fraction, 3) if allowed_error_fraction > 0 else 0.0

    return (round(remaining, 4), burn_rate)


# ---------------------------------------------------------------------------
# SLO Engine
# ---------------------------------------------------------------------------


class SLOEngine:
    """SLO registry and evaluator.

    Usage:
        engine = SLOEngine(artha_dir=Path(...))
        engine.attach_cost_guard(cost_guard)   # optional
        report = engine.status(runs_data)
    """

    # Default SLO definitions per spec §14.1 ST-04
    _DEFAULT_SLOS: list[SLODefinition] = [
        SLODefinition(
            slo_id="briefing_accuracy",
            slo_type=SLO_ACCURACY,
            target=0.85,
            window_seconds=7 * 24 * 3600,  # 7 days
            description="Briefing accuracy: fraction of runs with correction_rate ≤ 0.15",
        ),
        SLODefinition(
            slo_id="pipeline_latency",
            slo_type=SLO_RESPONSE_TIME,
            target=120.0,  # seconds
            window_seconds=24 * 3600,  # 24h
            description="Pipeline latency: p100 total_elapsed_seconds ≤ 120s",
        ),
        SLODefinition(
            slo_id="cost_per_briefing",
            slo_type=SLO_COST_PER_INFER,
            target=0.50,  # USD
            window_seconds=24 * 3600,  # 24h
            description="Cost per briefing: ≤ $0.50",
        ),
        SLODefinition(
            slo_id="connector_success",
            slo_type=SLO_SAFETY,
            target=0.90,
            window_seconds=24 * 3600,  # 24h
            description="Connector success rate: ≥ 90% of connectors return OK",
        ),
    ]

    def __init__(self, artha_dir: Path | None = None):
        if artha_dir is None:
            artha_dir = _REPO_ROOT
        self._artha_dir = artha_dir
        self._cost_guard: Any = None  # injected via attach_cost_guard
        self._slos: dict[str, SLODefinition] = {
            s.slo_id: s for s in self._DEFAULT_SLOS
        }

    def attach_cost_guard(self, cost_guard: Any) -> None:
        """Wire a CostGuard instance for cost-per-briefing SLO data."""
        self._cost_guard = cost_guard

    def register_slo(self, slo_def: SLODefinition) -> None:
        """Add or replace an SLO definition."""
        self._slos[slo_def.slo_id] = slo_def

    # ── Data extraction ───────────────────────────────────────────────────

    def _load_runs(self, window_seconds: float) -> list[dict]:
        """Load catch_up_runs.yaml entries within the rolling window."""
        runs_path = self._artha_dir / "state" / "catch_up_runs.yaml"
        if not runs_path.exists():
            return []
        try:
            import yaml  # type: ignore[import-not-found]
            raw = yaml.safe_load(runs_path.read_text(encoding="utf-8")) or []
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        result = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            ts_str = entry.get("timestamp")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                if ts >= cutoff:
                    result.append(entry)
            except (ValueError, TypeError):
                continue
        return result

    def _load_metrics_snapshots(self, window_seconds: float) -> list[dict]:
        """Load catchup_metrics.json entries within the rolling window."""
        metrics_path = self._artha_dir / "tmp" / "catchup_metrics.json"
        if not metrics_path.exists():
            return []
        try:
            raw = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        result = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            ts_str = entry.get("timestamp")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                if ts >= cutoff:
                    result.append(entry)
            except (ValueError, TypeError):
                continue
        return result

    # ── SLO evaluation ────────────────────────────────────────────────────

    def _eval_briefing_accuracy(self, slo: SLODefinition) -> SLOStatus:
        """ACCURACY SLO: correction_rate ≤ 0.15 counts as a 'successful' run."""
        runs = self._load_runs(slo.window_seconds)
        values = []
        for r in runs:
            cr = r.get("correction_rate")
            if cr is not None:
                try:
                    # Convert correction_rate to an "accuracy" value
                    # accuracy = 1 - correction_rate; meets SLO if ≥ target (0.85)
                    values.append(float(1.0 - float(cr)))
                except (ValueError, TypeError):
                    pass
        if not values:
            current = 1.0  # no data → assume met
            met = True
        else:
            current = round(sum(values) / len(values), 4)
            met = current >= slo.target
        budget_remaining, burn_rate = compute_error_budget(values or [current], slo.target, slo.slo_type)
        return SLOStatus(
            slo_id=slo.slo_id,
            slo_type=slo.slo_type,
            target=slo.target,
            current_value=current,
            met=met,
            error_budget_remaining=budget_remaining,
            burn_rate=burn_rate,
            sample_count=len(values),
            window_seconds=slo.window_seconds,
        )

    def _eval_pipeline_latency(self, slo: SLODefinition) -> SLOStatus:
        """RESPONSE_TIME SLO: total_elapsed_seconds ≤ 120s."""
        snapshots = self._load_metrics_snapshots(slo.window_seconds)
        values = []
        for s in snapshots:
            elapsed = s.get("total_elapsed_seconds")
            if elapsed is not None:
                try:
                    values.append(float(elapsed))
                except (ValueError, TypeError):
                    pass
        if not values:
            current = 0.0
            met = True
        else:
            current = round(max(values), 3)  # worst case (p100)
            met = current <= slo.target
        budget_remaining, burn_rate = compute_error_budget(values or [0.0], slo.target, slo.slo_type)
        return SLOStatus(
            slo_id=slo.slo_id,
            slo_type=slo.slo_type,
            target=slo.target,
            current_value=current,
            met=met,
            error_budget_remaining=budget_remaining,
            burn_rate=burn_rate,
            sample_count=len(values),
            window_seconds=slo.window_seconds,
        )

    def _eval_cost_per_briefing(self, slo: SLODefinition) -> SLOStatus:
        """COST_PER_INFER SLO: cost per briefing ≤ $0.50.

        Data source: CostGuard (if attached) or catch_up_runs total_cost field.
        """
        values = []
        if self._cost_guard is not None:
            try:
                summary = self._cost_guard.agent_summary("pipeline_llm")
                today_cost = summary.get("daily_spent", 0.0)
                today_calls = summary.get("calls_today", 1)
                if today_calls > 0:
                    values = [today_cost / today_calls]
            except Exception:
                pass
        if not values:
            # Fall back to catch_up_runs total_cost
            runs = self._load_runs(slo.window_seconds)
            for r in runs:
                cost = r.get("total_cost_usd")
                if cost is not None:
                    try:
                        values.append(float(cost))
                    except (ValueError, TypeError):
                        pass
        if not values:
            current = 0.0
            met = True
        else:
            current = round(sum(values) / len(values), 4)
            met = current <= slo.target
        budget_remaining, burn_rate = compute_error_budget(values or [0.0], slo.target, slo.slo_type)
        return SLOStatus(
            slo_id=slo.slo_id,
            slo_type=slo.slo_type,
            target=slo.target,
            current_value=current,
            met=met,
            error_budget_remaining=budget_remaining,
            burn_rate=burn_rate,
            sample_count=len(values),
            window_seconds=slo.window_seconds,
        )

    def _eval_connector_success(self, slo: SLODefinition) -> SLOStatus:
        """SAFETY SLO: connector_success_rate ≥ 0.90.

        Data source: catch_up_runs connector_success_rate field OR
        snapshots counters['connectors_ok'] / counters['connectors_total'].
        """
        values = []
        runs = self._load_runs(slo.window_seconds)
        for r in runs:
            rate = r.get("connector_success_rate")
            if rate is not None:
                try:
                    values.append(float(rate))
                except (ValueError, TypeError):
                    pass
        if not values:
            # Try metrics snapshots counters
            snapshots = self._load_metrics_snapshots(slo.window_seconds)
            for s in snapshots:
                counters = s.get("counters", {})
                ok = counters.get("connectors_ok")
                total = counters.get("connectors_total")
                if ok is not None and total and int(total) > 0:
                    try:
                        values.append(float(ok) / float(total))
                    except (ValueError, TypeError):
                        pass
        if not values:
            current = 1.0
            met = True
        else:
            current = round(sum(values) / len(values), 4)
            met = current >= slo.target
        budget_remaining, burn_rate = compute_error_budget(values or [1.0], slo.target, slo.slo_type)
        return SLOStatus(
            slo_id=slo.slo_id,
            slo_type=slo.slo_type,
            target=slo.target,
            current_value=current,
            met=met,
            error_budget_remaining=budget_remaining,
            burn_rate=burn_rate,
            sample_count=len(values),
            window_seconds=slo.window_seconds,
        )

    # ── Main API ─────────────────────────────────────────────────────────

    def status(self) -> SLOReport:
        """Evaluate all registered SLOs and return a full report.

        Returns:
            SLOReport with timestamp and slos dict.
        """
        ts = datetime.now(timezone.utc).isoformat()
        results: dict[str, SLOStatus] = {}

        _evaluators = {
            "briefing_accuracy": self._eval_briefing_accuracy,
            "pipeline_latency": self._eval_pipeline_latency,
            "cost_per_briefing": self._eval_cost_per_briefing,
            "connector_success": self._eval_connector_success,
        }

        for slo_id, slo_def in self._slos.items():
            try:
                evaluator = _evaluators.get(slo_id)
                if evaluator is None:
                    # Generic fallback: no data → met
                    results[slo_id] = SLOStatus(
                        slo_id=slo_id,
                        slo_type=slo_def.slo_type,
                        target=slo_def.target,
                        current_value=slo_def.target,
                        met=True,
                        error_budget_remaining=1.0,
                        burn_rate=0.0,
                        sample_count=0,
                        window_seconds=slo_def.window_seconds,
                    )
                else:
                    results[slo_id] = evaluator(slo_def)
            except Exception:
                # Non-fatal: produce a safe "unknown" status
                results[slo_id] = SLOStatus(
                    slo_id=slo_id,
                    slo_type=slo_def.slo_type,
                    target=slo_def.target,
                    current_value=0.0,
                    met=True,  # fail-open for reads
                    error_budget_remaining=1.0,
                    burn_rate=0.0,
                    sample_count=0,
                    window_seconds=slo_def.window_seconds,
                )

        return SLOReport(timestamp=ts, slos=results)

    def status_as_dict(self) -> dict:
        """Return status() as a plain dict (JSON-serializable)."""
        report = self.status()
        out: dict = {"timestamp": report.timestamp, "slos": {}}
        for slo_id, s in report.slos.items():
            out["slos"][slo_id] = {
                "slo_type": s.slo_type,
                "target": s.target,
                "current_value": s.current_value,
                "met": s.met,
                "error_budget_remaining": s.error_budget_remaining,
                "burn_rate": s.burn_rate,
                "sample_count": s.sample_count,
                "window_seconds": s.window_seconds,
            }
        return out

    def persist_to_health_check(self, health_check_path: Path | None = None) -> None:
        """Write SLO status to state/health-check.md SLO section.

        Idempotent: rewrites the `## SLO Status` section if present,
        appends if absent.
        """
        path = health_check_path or (self._artha_dir / "state" / "health-check.md")
        report = self.status()

        # Build SLO section text
        lines = ["## SLO Status\n", f"_Updated: {report.timestamp}_\n\n"]
        lines.append("| SLO | Type | Target | Current | Met | Budget Remaining | Burn Rate |\n")
        lines.append("|-----|------|--------|---------|-----|------------------|-----------|\n")
        for slo_id, s in report.slos.items():
            met_str = "✓" if s.met else "⚠"
            alert = " 🚨" if s.burn_rate > 1.0 else ""
            lines.append(
                f"| {slo_id} | {s.slo_type} | {s.target} | "
                f"{s.current_value:.4f} | {met_str} | "
                f"{s.error_budget_remaining:.1%} | {s.burn_rate:.2f}{alert} |\n"
            )
        section_text = "".join(lines)

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(section_text, encoding="utf-8")
            return

        content = path.read_text(encoding="utf-8")

        # Replace existing SLO Status section if present
        import re
        pattern = re.compile(r"## SLO Status\n.*?(?=\n## |\Z)", re.DOTALL)
        if pattern.search(content):
            new_content = pattern.sub(section_text.rstrip("\n"), content)
        else:
            new_content = content.rstrip("\n") + "\n\n" + section_text

        # Atomic write
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".slo_")
        try:
            os.write(fd, new_content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp, path)
        except Exception:
            os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Module-level singleton factory
# ---------------------------------------------------------------------------


def get_engine(artha_dir: Path | None = None) -> SLOEngine:
    """Return a fresh SLOEngine instance."""
    return SLOEngine(artha_dir=artha_dir)
