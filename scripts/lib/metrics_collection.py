# F-C1: merged metrics.py + metrics_writer.py → metrics_collection.py (re-artha.md). Shims removed after 2026-06-16.

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


# ---------------------------------------------------------------------------
# Merged from metrics_writer.py
# ---------------------------------------------------------------------------

"""AR-9 External Agent Composition — Metrics JSONL Writer (EA-10a).

Appends structured records to ``tmp/ext-agent-metrics.jsonl``.
All operations are fire-and-forget — this module NEVER raises.

Record types
------------
invocation
    One record per agent invocation attempt (success or failure).
routing_decision
    One record per routing evaluation that produces a match.

Record schemas (consumed by eval_runner.py)
-------------------------------------------
invocation::

    {
        "timestamp": "2026-04-02T10:30:00Z",
        "record_type": "invocation",
        "agent_name": "storage-deployment-expert",
        "success": true,
        "latency_ms": 4500.0,
        "quality_score": 0.82,
        "cache_hit": false,
        "fallback_level": null,   # int or null
        "failure_reason": null    # str or null
    }

routing_decision::

    {
        "timestamp": "2026-04-02T10:30:00Z",
        "record_type": "routing_decision",
        "matched_agent": "storage-deployment-expert",
        "dispatched": true,
        "confidence": 0.72,
        "matched_keywords": ["deployment", "SDP"],
        "routing_ms": 5.2
    }
"""

# pii-guard: ignore-file

from typing import Optional

_DEFAULT_METRICS_FILE: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "tmp"
    / "ext-agent-metrics.jsonl"
)


def _append(record: dict, metrics_file: Optional[Path]) -> None:
    """Internal: serialize ``record`` as JSONL to *metrics_file*. Never raises."""
    target: Path = metrics_file if metrics_file is not None else _DEFAULT_METRICS_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def write_invocation_metric(
    agent_name: str,
    success: bool,
    latency_ms: float,
    quality_score: float | None = None,
    cache_hit: bool = False,
    fallback_level: int | None = None,
    failure_reason: str | None = None,
    metrics_file: Path | None = None,
) -> None:
    """Append an ``invocation`` record to the metrics JSONL file.

    Fire-and-forget — never raises.

    Parameters
    ----------
    agent_name:
        Registered agent slug.
    success:
        Whether the invocation succeeded.
    latency_ms:
        Wall-clock latency in milliseconds.
    quality_score:
        Optional 0–1 quality score from the response verifier.
    cache_hit:
        Whether the response was served from the knowledge cache.
    fallback_level:
        0-based index of the fallback entry used, or ``None`` if primary.
    failure_reason:
        Reason string from ``InvocationError.reason`` on failure.
    metrics_file:
        Override target file (used in tests for isolation).
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "timestamp": ts,
        "record_type": "invocation",
        "agent_name": agent_name,
        "success": success,
        "latency_ms": float(latency_ms),
        "quality_score": quality_score,
        "cache_hit": cache_hit,
        "fallback_level": fallback_level,
        "failure_reason": failure_reason,
    }
    _append(record, metrics_file)


def write_routing_decision(
    matched_agent: str,
    dispatched: bool,
    confidence: float = 0.0,
    matched_keywords: list[str] | None = None,
    routing_ms: float = 0.0,
    metrics_file: Path | None = None,
) -> None:
    """Append a ``routing_decision`` record to the metrics JSONL file.

    Fire-and-forget — never raises.

    Parameters
    ----------
    matched_agent:
        Name of the agent that was matched.
    dispatched:
        Whether the agent was actually dispatched (auto_dispatch or manual).
    confidence:
        Router confidence score (0–1).
    matched_keywords:
        List of keywords that triggered the match.
    routing_ms:
        Time spent in the router in milliseconds.
    metrics_file:
        Override target file (used in tests for isolation).
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "timestamp": ts,
        "record_type": "routing_decision",
        "matched_agent": matched_agent,
        "dispatched": dispatched,
        "confidence": float(confidence),
        "matched_keywords": matched_keywords or [],
        "routing_ms": float(routing_ms),
    }
    _append(record, metrics_file)


def write_routing_margin(
    top1_agent: str,
    top1_confidence: float,
    top2_agent: Optional[str],
    top2_confidence: float,
    confidence_margin: float,
    routing_ms: float = 0.0,
    keyword_miss_rate: Optional[float] = None,
    metrics_file: Path | None = None,
) -> None:
    """Append a ``routing_margin`` record to the metrics JSONL file.

    Confidence margin = top-1 score - top-2 score.  Used to detect when
    routing is becoming ambiguous (median margin < 0.10 over 7 days triggers
    EAR-4 TF-IDF fallback upgrade recommendation).

    Thresholds (from EAR-4 spec):
      - Healthy:   margin > 0.15
      - Degrading: margin 0.10–0.15
      - Upgrade trigger: margin < 0.10 (surface heartbeat alert)

    Fire-and-forget — never raises.

    Ref: specs/ext-agent-reloaded.md §EAR-4, Sonnet v2 R-1
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "timestamp": ts,
        "record_type": "routing_margin",
        "top1_agent": top1_agent,
        "top1_confidence": float(top1_confidence),
        "top2_agent": top2_agent,
        "top2_confidence": float(top2_confidence),
        "confidence_margin": float(confidence_margin),
        "routing_ms": float(routing_ms),
        "routing_quality": {
            "keyword_miss_rate": float(keyword_miss_rate) if keyword_miss_rate is not None else None,
        },
    }
    _append(record, metrics_file)


def write_invocation_trace(
    invocation_id: str,
    agent_name: str,
    query_hash: str,
    routing_confidence: float,
    quality_score: Optional[float],
    latency_ms: float,
    metrics_file: Path | None = None,
    trace_file: Path | None = None,
) -> None:
    """Write a structured trace record to a per-session JSON file.

    Writes to ``tmp/invocation-{invocation_id}.json`` (§15.8 requirement:
    one file per session, not a shared append-only JSONL).  Also appends to
    the legacy ``tmp/ext-agent-trace.jsonl`` for backwards-compat tooling.

    Fire-and-forget — never raises.

    Ref: specs/ext-agent-reloaded.md §Phase 0 BLOCKING-1, Sonnet v2 R-12, §15.8
    """
    _LEGACY_TRACE_FILE: Path = (
        Path(__file__).resolve().parent.parent.parent
        / "tmp"
        / "ext-agent-trace.jsonl"
    )
    _tmp_dir = Path(__file__).resolve().parent.parent.parent / "tmp"
    _per_session_file: Path = _tmp_dir / f"invocation-{invocation_id}.json"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "timestamp": ts,
        "record_type": "pipeline_trace",
        "invocation_id": invocation_id,
        "agent_name": agent_name,
        "query_hash": query_hash,
        "routing_confidence": float(routing_confidence),
        "quality_score": quality_score,
        "latency_ms": float(latency_ms),
    }
    try:
        _per_session_file.parent.mkdir(parents=True, exist_ok=True)
        with _per_session_file.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass
    # Also write to legacy shared JSONL for backwards-compat tooling
    legacy = trace_file or _LEGACY_TRACE_FILE
    try:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        with legacy.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass
