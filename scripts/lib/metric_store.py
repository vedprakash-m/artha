#!/usr/bin/env python3
"""lib/metric_store.py — Read-only accessor for state/catch_up_runs.yaml.

Provides a clean API over the growing run history written by
health_check_writer.py.  All methods return plain Python structures
(no mutation of state files).

Design decisions:
    DD-2: Primary data source is catch_up_runs.yaml (not health-check.md).
    DD-6: engagement_rate formula = (user_ois + correction_count) / items_surfaced.
    DD-11: This module lives at scripts/lib/metric_store.py.

Usage:
    from lib.metric_store import MetricStore
    ms = MetricStore(artha_dir)
    runs = ms.load_runs(days=30)
    trend = ms.get_quality_trend(window=10)
    budget = ms.get_error_budget()
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = "1.0.0"
_RETENTION_LIMIT = 100


class MetricStore:
    """Read-only accessor for catch_up_runs.yaml run history."""

    def __init__(self, artha_dir: Path | str) -> None:
        self._artha_dir = Path(artha_dir)
        self._runs_file = self._artha_dir / "state" / "catch_up_runs.yaml"
        self._log_digest = self._artha_dir / "tmp" / "log_digest.json"

    # ------------------------------------------------------------------
    # Core loader
    # ------------------------------------------------------------------

    def load_runs(self, days: int | None = None) -> list[dict[str, Any]]:
        """Return all (or recent *days*) catch-up run records as list of dicts.

        Returns empty list if file missing or unparseable.
        Optionally filters to entries whose ``timestamp`` falls within the
        last *days* days (UTC).
        """
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            return []

        if not self._runs_file.exists():
            return []

        try:
            raw = yaml.safe_load(self._runs_file.read_text(encoding="utf-8"))
        except Exception:
            return []

        if not isinstance(raw, list):
            return []

        runs: list[dict[str, Any]] = [r for r in raw if isinstance(r, dict)]

        if days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            filtered: list[dict[str, Any]] = []
            for r in runs:
                ts_raw = r.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    if ts >= cutoff:
                        filtered.append(r)
                except (ValueError, TypeError):
                    filtered.append(r)  # Keep unparseable entries
            return filtered

        return runs

    # ------------------------------------------------------------------
    # Quality trend
    # ------------------------------------------------------------------

    def get_quality_trend(self, window: int = 10) -> dict[str, Any]:
        """Return quality trend over the last *window* runs.

        Returns:
            avg_quality         float | None  — average quality_score (0-100)
            trend               str           — "improving" | "regressing" | "stable" | "insufficient_data"
            dimension_averages  dict          — per-dimension averages (if present)
            run_count           int           — number of runs with quality_score
        """
        runs = self.load_runs()
        scored = [r for r in runs if r.get("quality_score") is not None]
        if not scored:
            return {"avg_quality": None, "trend": "insufficient_data", "dimension_averages": {}, "run_count": 0}

        recent = scored[-window:]
        scores = [r["quality_score"] for r in recent if isinstance(r.get("quality_score"), (int, float))]

        avg_quality = sum(scores) / len(scores) if scores else None

        # Simple linear trend: compare first half vs second half
        trend = "insufficient_data"
        if len(scores) >= 4:
            mid = len(scores) // 2
            first_half = sum(scores[:mid]) / mid
            second_half = sum(scores[mid:]) / (len(scores) - mid)
            delta = second_half - first_half
            if delta > 3.0:
                trend = "improving"
            elif delta < -3.0:
                trend = "regressing"
            else:
                trend = "stable"
        elif len(scores) >= 2:
            trend = "stable"

        # Aggregate dimension averages if present
        dim_accumulator: dict[str, list[float]] = {}
        for r in recent:
            for dim, val in r.get("dimensions", {}).items():
                if isinstance(val, (int, float)):
                    dim_accumulator.setdefault(dim, []).append(float(val))
        dimension_averages = {k: round(sum(v) / len(v), 2) for k, v in dim_accumulator.items()}

        return {
            "avg_quality": round(avg_quality, 2) if avg_quality is not None else None,
            "trend": trend,
            "dimension_averages": dimension_averages,
            "run_count": len(scores),
        }

    # ------------------------------------------------------------------
    # Error budget
    # ------------------------------------------------------------------

    def get_error_budget(self, target_pct: float = 5.0) -> dict[str, Any]:
        """Return error-budget consumed percentage from log_digest.json.

        Returns:
            consumed_pct    float   — error_budget_pct from log_digest
            target_pct      float   — the target threshold (default 5.0)
            over_budget     bool    — True if consumed_pct > target_pct
            available       bool    — True if log_digest.json exists
        """
        if not self._log_digest.exists():
            return {"consumed_pct": None, "target_pct": target_pct, "over_budget": False, "available": False}

        try:
            data = json.loads(self._log_digest.read_text(encoding="utf-8"))
            consumed = float(data.get("error_budget_pct", 0.0))
            return {
                "consumed_pct": round(consumed, 2),
                "target_pct": target_pct,
                "over_budget": consumed > target_pct,
                "available": True,
            }
        except Exception:
            return {"consumed_pct": None, "target_pct": target_pct, "over_budget": False, "available": False}

    # ------------------------------------------------------------------
    # Idempotent backfill
    # ------------------------------------------------------------------

    def backfill_run(self, timestamp: str, **fields: Any) -> bool:
        """Upsert a run entry by timestamp.

        If a run with the given *timestamp* already exists, merges *fields*
        into it (existing keys are overwritten).  Otherwise appends a new
        entry.  Writes atomically via tempfile + os.replace.

        Returns True on success, False on I/O failure.
        """
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            return False

        existing: list[dict[str, Any]] = []
        if self._runs_file.exists():
            try:
                raw = yaml.safe_load(self._runs_file.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    existing = [r for r in raw if isinstance(r, dict)]
            except Exception:
                existing = []

        # Find and update or append
        found = False
        updated: list[dict[str, Any]] = []
        for r in existing:
            if r.get("timestamp") == timestamp:
                merged = dict(r)
                merged.update(fields)
                merged.setdefault("schema_version", _SCHEMA_VERSION)
                updated.append(merged)
                found = True
            else:
                updated.append(r)

        if not found:
            entry = {"timestamp": timestamp, "schema_version": _SCHEMA_VERSION}
            entry.update(fields)
            updated.append(entry)

        # Retention limit
        if len(updated) > _RETENTION_LIMIT:
            updated = updated[-_RETENTION_LIMIT:]

        # Atomic write
        state_dir = self._artha_dir / "state"
        state_dir.mkdir(exist_ok=True)
        try:
            import tempfile
            fd, tmp_path = tempfile.mkstemp(dir=state_dir, prefix=".catch_up_runs-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(
                        "# state/catch_up_runs.yaml\n"
                        "# Machine-parseable append-only run history.\n"
                        "# Written by health_check_writer.py; read by briefing_adapter.py + metric_store.py.\n"
                        "---\n"
                    )
                    yaml.dump(updated, fh, allow_unicode=True, default_flow_style=False)
                os.replace(tmp_path, self._runs_file)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception:
            return False

        return True
