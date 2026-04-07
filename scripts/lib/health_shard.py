# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/health_shard.py — Append-only JSONL health shards (EAR-8, R-6, R-14).

Separates mutable health counters from the immutable agent configuration YAML
to eliminate the O(n) full-registry write on every invocation outcome.

Data model:
  tmp/ext-agent-health/<agent-name>.jsonl  — one line per invocation outcome
  (append-only during session, aggregated back via `agent_manager health sync`)

Each JSONL line encodes one InvocationOutcome:
  {
    "ts": "2026-04-06T10:00:00Z",
    "success": true,
    "latency_ms": 1234.5,
    "quality_score": 0.82,
    "injection_detected": false,
    "invocation_id": "uuid4-hex",
    "query_hash": "sha256[:12]"
  }

Aggregation produces an AgentHealthSummary matching the existing AgentHealth
dataclass field set (for registry sync via `health sync` command).

Thread safety:
  Per-agent file lock via threading.Lock keyed by (agent_name).
  Lock is held only for the append write — microseconds.

Ref: specs/ext-agent-reloaded.md §R-14, §EAR-8
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_log = logging.getLogger("artha.health_shard")

# ---------------------------------------------------------------------------
# Per-agent lock registry
# ---------------------------------------------------------------------------

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(agent_name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if agent_name not in _LOCKS:
            _LOCKS[agent_name] = threading.Lock()
        return _LOCKS[agent_name]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class AgentHealthSummary:
    """Aggregated health summary computed from the JSONL shard."""

    agent_name: str
    total_invocations: int = 0
    successful_invocations: int = 0
    failed_invocations: int = 0
    mean_quality_score: float = 0.0
    consecutive_failures: int = 0
    last_invocation: Optional[str] = None   # ISO timestamp
    last_success: Optional[str] = None       # ISO timestamp
    last_failure_reason: Optional[str] = None
    quality_scores: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HealthShard
# ---------------------------------------------------------------------------

_DEFAULT_SHARD_DIR = (
    Path(__file__).resolve().parent.parent.parent / "tmp" / "ext-agent-health"
)


class HealthShard:
    """Manages the append-only JSONL health shard for one or all agents.

    Parameters:
        shard_dir: Base directory for shard files. Defaults to
                   tmp/ext-agent-health/.
    """

    def __init__(self, shard_dir: Path | None = None) -> None:
        self._dir = shard_dir or _DEFAULT_SHARD_DIR

    def _shard_path(self, agent_name: str) -> Path:
        return self._dir / f"{agent_name}.jsonl"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(
        self,
        agent_name: str,
        *,
        success: bool,
        latency_ms: float,
        quality_score: float | None = None,
        injection_detected: bool = False,
        invocation_id: str | None = None,
        query: str | None = None,
    ) -> None:
        """Append one invocation outcome. Never raises.

        Thread-safe: uses per-agent lock for the write.
        """
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            query_hash = None
            if query:
                query_hash = hashlib.sha256(
                    query.encode("utf-8", errors="replace")
                ).hexdigest()[:12]

            record = {
                "ts": ts,
                "success": success,
                "latency_ms": float(latency_ms),
                "quality_score": quality_score,
                "injection_detected": injection_detected,
                "invocation_id": invocation_id,
                "query_hash": query_hash,
            }

            shard_path = self._shard_path(agent_name)
            lock = _get_lock(agent_name)

            with lock:
                shard_path.parent.mkdir(parents=True, exist_ok=True)
                with shard_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        except Exception as exc:  # noqa: BLE001
            _log.error("health_shard.append error for %s: %s", agent_name, exc)

    # ------------------------------------------------------------------
    # Read / aggregate
    # ------------------------------------------------------------------

    def aggregate(self, agent_name: str) -> AgentHealthSummary:
        """Aggregate all lines in the shard into a health summary.

        Returns a zero-baseline summary if the shard file does not exist.
        """
        summary = AgentHealthSummary(agent_name=agent_name)
        shard_path = self._shard_path(agent_name)

        if not shard_path.exists():
            return summary

        lines = []
        try:
            lock = _get_lock(agent_name)
            with lock:
                lines = shard_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as exc:
            _log.warning("health_shard.aggregate read error for %s: %s", agent_name, exc)
            return summary

        consecutive_fail = 0
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue

            summary.total_invocations += 1
            summary.last_invocation = rec.get("ts")

            if rec.get("success"):
                summary.successful_invocations += 1
                summary.last_success = rec.get("ts")
                consecutive_fail = 0
            else:
                summary.failed_invocations += 1
                consecutive_fail += 1

            qs = rec.get("quality_score")
            if qs is not None:
                summary.quality_scores.append(float(qs))

        summary.consecutive_failures = consecutive_fail

        if summary.quality_scores:
            summary.mean_quality_score = (
                sum(summary.quality_scores) / len(summary.quality_scores)
            )

        return summary

    def list_agents(self) -> list[str]:
        """Return all agent names that have a shard file."""
        if not self._dir.exists():
            return []
        return [
            p.stem
            for p in self._dir.glob("*.jsonl")
        ]
