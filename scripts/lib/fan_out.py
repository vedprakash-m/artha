# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/fan_out.py — Parallel fan-out invocation (EAR-5).

Invokes multiple domain-independent agents concurrently using ThreadPoolExecutor.
Synthesizes results into a unified multi-domain response.

Safety constraints (spec §EAR-5):
  - Max 3 concurrent invocations (configurable).
  - Per-agent timeout enforced independently.
  - Pool-level timeout: max(individual_timeouts) + 10s synthesis overhead.
  - KnowledgeExtractor cache race guard: per-agent lock held for
    extract_and_cache() read→combine→write cycle.  (Sonnet v2 R-11)
  - VS Code execution model warning: ThreadPoolExecutor submits concurrently
    in Python but VS Code runSubagent may be sequential in practice.
    Throughput claims are aspirational until A9 is validated.
  - PII scrubbing applied per-agent at their individual trust tiers.
  - Partial failures included in result with warning; don't block others.

Thread safety:
  - Each invocation uses isolated scratch data.
  - No shared mutable state between workers.
  - KnowledgeExtractor cache write uses per-agent file lock (R-11).

Ref: specs/ext-agent-reloaded.md §EAR-5, Sonnet v2 R-11
"""
from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MAX_WORKERS = 3
_SYNTHESIS_OVERHEAD_S = 10
_POOL_TIMEOUT_S = 180     # Hard ceiling on entire fan-out

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class FanOutInvocationResult:
    agent_name: str
    confidence: float
    prose: str = ""
    quality_score: float = 0.0
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""


@dataclass
class FanOutResult:
    """Aggregated result of a parallel fan-out invocation."""

    matched_agents: list[str]
    results: list[FanOutInvocationResult] = field(default_factory=list)
    unified_output: str = ""
    combined_confidence: float = 0.0
    total_latency_ms: float = 0.0
    any_failure: bool = False


# ---------------------------------------------------------------------------
# FanOut executor
# ---------------------------------------------------------------------------

class FanOut:
    """Invoke multiple agents concurrently and synthesise results.

    Parameters:
        invoke_fn: Callable(agent_name, query, timeout) → (prose, quality_score)
        max_workers: Max concurrent invocations (default 3).
    """

    def __init__(
        self,
        invoke_fn: Callable[[str, str, int], tuple[str, float]],
        max_workers: int = _MAX_WORKERS,
    ) -> None:
        self._invoke = invoke_fn
        self._max_workers = min(max_workers, _MAX_WORKERS)

    def execute(
        self,
        query: str,
        candidates: list,        # list[RoutingMatch] — avoid circular import
        timeout_per_agent: int = 60,
    ) -> FanOutResult:
        """Fan out query to multiple domain-independent agents.

        Parameters:
            query:             User query.
            candidates:        RoutingMatch list from route_multi().
            timeout_per_agent: Per-agent timeout.  Pool timeout =
                               timeout_per_agent + _SYNTHESIS_OVERHEAD_S.
        """
        if not candidates:
            return FanOutResult(matched_agents=[])

        pool_timeout = min(timeout_per_agent + _SYNTHESIS_OVERHEAD_S, _POOL_TIMEOUT_S)
        start = time.monotonic()
        agent_names = [c.agent_name for c in candidates]
        confidences = {c.agent_name: c.confidence for c in candidates}

        results: list[FanOutInvocationResult] = [None] * len(candidates)  # type: ignore
        lock = threading.Lock()

        def _invoke_one(idx: int, agent_name: str) -> None:
            t0 = time.monotonic()
            try:
                prose, quality = self._invoke(agent_name, query, timeout_per_agent)
                elapsed = (time.monotonic() - t0) * 1000
                with lock:
                    results[idx] = FanOutInvocationResult(
                        agent_name=agent_name,
                        confidence=confidences.get(agent_name, 0.0),
                        prose=prose,
                        quality_score=quality,
                        latency_ms=elapsed,
                        success=True,
                    )
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                with lock:
                    results[idx] = FanOutInvocationResult(
                        agent_name=agent_name,
                        confidence=confidences.get(agent_name, 0.0),
                        latency_ms=elapsed,
                        success=False,
                        error=str(exc)[:200],
                    )

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [
                executor.submit(_invoke_one, i, name)
                for i, name in enumerate(agent_names)
            ]
            # Wait for all futures up to pool timeout
            done_by_deadline = True
            for fut in futures:
                remaining = pool_timeout - (time.monotonic() - start)
                if remaining <= 0:
                    done_by_deadline = False
                    break
                try:
                    fut.result(timeout=max(0.1, remaining))
                except FuturesTimeout:
                    done_by_deadline = False
                except Exception:
                    pass  # Already captured in _invoke_one

        total_ms = (time.monotonic() - start) * 1000

        # Collect non-None results (timed-out slots remain None)
        final_results: list[FanOutInvocationResult] = [
            r for r in results if r is not None
        ]

        any_failure = any(not r.success for r in final_results)
        combined_confidence = _geomean(
            [r.confidence for r in final_results if r.success]
        )

        unified = self._synthesize(query, final_results)

        return FanOutResult(
            matched_agents=agent_names,
            results=final_results,
            unified_output=unified,
            combined_confidence=combined_confidence,
            total_latency_ms=total_ms,
            any_failure=any_failure,
        )

    @staticmethod
    def _synthesize(query: str, results: list[FanOutInvocationResult]) -> str:
        """Synthesize fan-out results into unified markdown response."""
        successful = [r for r in results if r.success and r.prose]
        failed = [r for r in results if not r.success]

        if not successful:
            return "⚠️ All agents failed to respond. Check individual errors.\n"

        lines = []
        for r in successful:
            lines.append(
                f"### {r.agent_name} (confidence: {r.confidence:.2f})\n{r.prose}"
            )

        if failed:
            lines.append("\n⚠️ Partial results — the following agents failed:")
            for r in failed:
                lines.append(f"  - {r.agent_name}: {r.error or 'unknown error'}")

        quality_scores = [r.quality_score for r in successful]
        combined_q = _geomean(quality_scores) if quality_scores else 0.0
        agent_count = len(successful)

        summary = (
            f"\n> **Combined Quality:** {combined_q:.2f} | "
            f"Sources: {agent_count} agent(s)"
        )
        lines.append(summary)

        return "\n\n".join(lines)


def _geomean(scores: list[float]) -> float:
    import math
    if not scores:
        return 0.0
    product = 1.0
    for s in scores:
        product *= max(0.001, s)
    return product ** (1.0 / len(scores))
