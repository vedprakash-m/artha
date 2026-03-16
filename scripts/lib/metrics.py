# pii-guard: ignore-file — shared infrastructure; no personal data
"""
scripts/lib/metrics.py — Catch-up performance metrics collector.

Collects timing, counts, and quality metrics for each catch-up phase.
Persists to tmp/catchup_metrics.json (ephemeral, not synced) and writes
summary to state/health-check.md (Step 16).

Usage:
    from lib.metrics import CatchUpMetrics

    metrics = CatchUpMetrics()
    with metrics.phase("preflight"):
        run_preflight()
    with metrics.step("fetch.gmail"):
        fetch_gmail()
    metrics.record("emails_processed", 42)
    metrics.save()

Design:
    - Thread-safe: uses threading.Lock for concurrent step recording
    - Phase/step hierarchy: phases contain steps
    - JSON-serializable: all values are primitives
    - Append-only log: keeps last 30 runs in catchup_metrics.json

Ref: audit item §metrics-gap, observability.md
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator


class CatchUpMetrics:
    """Collects hierarchical timing + quality metrics for a catch-up run."""

    def __init__(self, artha_dir: Path | None = None):
        if artha_dir is None:
            artha_dir = Path(__file__).resolve().parent.parent.parent
        self._artha_dir = artha_dir
        self._metrics_path = artha_dir / "tmp" / "catchup_metrics.json"
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._start_iso = datetime.now(timezone.utc).isoformat()

        # Timing data
        self._phases: dict[str, dict[str, Any]] = {}
        self._steps: dict[str, dict[str, Any]] = {}

        # Counter data
        self._counters: dict[str, int | float] = {}

        # Quality / eval data
        self._eval: dict[str, Any] = {}

    # ── Phase timing ──────────────────────────────────────────────────────

    @contextmanager
    def phase(self, name: str) -> Generator[None, None, None]:
        """Time a top-level workflow phase (e.g., 'preflight', 'fetch')."""
        t0 = time.monotonic()
        error: str | None = None
        try:
            yield
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            elapsed = round(time.monotonic() - t0, 3)
            with self._lock:
                self._phases[name] = {
                    "elapsed_seconds": elapsed,
                    "error": error,
                }

    # ── Step timing ───────────────────────────────────────────────────────

    @contextmanager
    def step(self, name: str) -> Generator[None, None, None]:
        """Time a sub-step within a phase (e.g., 'fetch.gmail', 'process.immigration')."""
        t0 = time.monotonic()
        error: str | None = None
        try:
            yield
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            elapsed = round(time.monotonic() - t0, 3)
            with self._lock:
                self._steps[name] = {
                    "elapsed_seconds": elapsed,
                    "error": error,
                }

    def record_step(self, name: str, elapsed: float, error: str | None = None) -> None:
        """Record a step timing without using the context manager."""
        with self._lock:
            self._steps[name] = {
                "elapsed_seconds": round(elapsed, 3),
                "error": error,
            }

    # ── Counters ──────────────────────────────────────────────────────────

    def record(self, key: str, value: int | float) -> None:
        """Record a counter or gauge metric."""
        with self._lock:
            self._counters[key] = value

    def increment(self, key: str, delta: int = 1) -> None:
        """Increment a counter."""
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + delta

    # ── Eval / quality ────────────────────────────────────────────────────

    def record_eval(self, key: str, value: Any) -> None:
        """Record an eval/quality metric (e.g., accuracy, signal:noise)."""
        with self._lock:
            self._eval[key] = value

    # ── Snapshot & persistence ────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all collected metrics."""
        total_elapsed = round(time.monotonic() - self._start_time, 3)
        with self._lock:
            return {
                "timestamp": self._start_iso,
                "total_elapsed_seconds": total_elapsed,
                "phases": dict(self._phases),
                "steps": dict(self._steps),
                "counters": dict(self._counters),
                "eval": dict(self._eval),
            }

    def save(self) -> Path:
        """Persist metrics to tmp/catchup_metrics.json. Returns path written."""
        self._metrics_path.parent.mkdir(exist_ok=True)
        entry = self.snapshot()

        existing: list[dict] = []
        if self._metrics_path.exists():
            try:
                raw = json.loads(self._metrics_path.read_text())
                if isinstance(raw, list):
                    existing = raw
            except (json.JSONDecodeError, OSError):
                pass

        existing.insert(0, entry)
        existing = existing[:30]  # keep last 30 runs

        self._metrics_path.write_text(json.dumps(existing, indent=2))
        return self._metrics_path

    # ── Health-check integration ──────────────────────────────────────────

    def health_check_summary(self) -> str:
        """Return a YAML-formatted summary for embedding in health-check.md.

        Suitable for appending to the 'per_step_timing' field.
        """
        snap = self.snapshot()
        lines = [f"  total_seconds: {snap['total_elapsed_seconds']}"]

        if snap["phases"]:
            lines.append("  phases:")
            for name, data in sorted(snap["phases"].items()):
                err = f" (error: {data['error']})" if data.get("error") else ""
                lines.append(f"    {name}: {data['elapsed_seconds']}s{err}")

        if snap["steps"]:
            lines.append("  steps:")
            for name, data in sorted(snap["steps"].items()):
                err = f" (error: {data['error']})" if data.get("error") else ""
                lines.append(f"    {name}: {data['elapsed_seconds']}s{err}")

        return "\n".join(lines)

    # ── Convenience: timing report ────────────────────────────────────────

    def timing_report(self) -> str:
        """Human-readable timing report for terminal output."""
        snap = self.snapshot()
        lines = [
            f"⏱ Catch-up completed in {snap['total_elapsed_seconds']:.1f}s",
        ]
        if snap["phases"]:
            for name, data in snap["phases"].items():
                pct = (data["elapsed_seconds"] / max(snap["total_elapsed_seconds"], 0.001)) * 100
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                lines.append(f"  {name:<12} {bar} {data['elapsed_seconds']:6.1f}s ({pct:4.1f}%)")
        return "\n".join(lines)
