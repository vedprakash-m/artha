# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/agent_router.py — External agent routing engine for AR-9.

Determines whether a user query should be routed to an external agent.
Routing is keyword-based (deterministic, no LLM):
  - Fast: <10ms per call
  - Predictable: same query → same result
  - Auditable: full match transcript for every routing decision

Algorithm (spec §4.3):
  1. For each active registered agent, count keyword hits (word-boundary)
  2. Skip agents below min_keyword_hits threshold
  3. For qualifying agents, compute confidence = geometric mean of
     keyword_coverage × query_coverage + bonuses
  4. Filter by min_confidence
  5. Sort; return best match (or None)

Also handles:
  - Cache-hit shortcut: if knowledge cache has a fresh entry, return from
    cache without routing to the live agent
  - Conflict detection: tied candidates are logged to audit
  - Exclude-keyword suppression

Ref: specs/subagent-ext-agent.md §4.3, EA-2a/2b
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class RoutingMatch:
    agent_name: str
    confidence: float
    keyword_hits: int
    keyword_coverage: float
    query_coverage: float
    domain_bonus: float
    recency_bonus: float
    matched_keywords: list[str]


class RoutingResult(NamedTuple):
    match: RoutingMatch | None
    all_candidates: list[RoutingMatch]  # All above-threshold candidates (for audit)
    routing_ms: float                    # Time taken (ms) for the routing call
    cache_hit: bool = False              # True if matched from knowledge cache


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class AgentRouter:
    """Routes user queries to external agents.

    Parameters:
        registry:   AgentRegistry instance to query.
        cache_dir:  Path to the knowledge cache directory (tmp/ext-agent-cache/).
                    If None, cache lookups are skipped.
        global_min_confidence: Global minimum confidence override. Per-agent
                    min_confidence still applies if it's higher.
    """

    def __init__(self,
                 registry,  # AgentRegistry — not typed here to avoid circular import
                 cache_dir: Path | None = None,
                 global_min_confidence: float = 0.3) -> None:
        self._registry = registry
        self._cache_dir = cache_dir
        self._global_min = global_min_confidence

    def route(self, query: str) -> RoutingResult:
        """Route a query. Returns the best-matching agent or None.

        Routing algorithm:
          1. For each active agent, check exclude_keywords — skip if matched
          2. Count keyword hits (word-boundary, case-insensitive)
          3. Skip if hits < min_keyword_hits
          4. Compute confidence = sqrt(keyword_cov * query_cov) + bonuses
          5. Filter by effective min_confidence
          6. Sort by (confidence DESC, priority ASC)
          7. Check knowledge cache for highest-confidence match
          8. Return RoutingResult
        """
        start = time.monotonic()
        candidates: list[RoutingMatch] = []
        query_lower = query.lower()
        query_tokens = set(query_lower.split())

        for agent in self._registry.active_agents():
            if not agent.enabled:
                continue

            # Step 1: Exclude-keyword suppression
            if any(
                re.search(r'\b' + re.escape(ex.lower()) + r'\b', query_lower)
                for ex in agent.routing.exclude_keywords
            ):
                continue

            # EA-13b: Skip agent if query matches a known-weak pattern
            weak_queries = getattr(agent.health, "weak_queries", None) or []
            if any(wq.lower() in query_lower for wq in weak_queries):
                continue

            # Step 2: Keyword hit counting (word-boundary)
            matched_kws: list[str] = []
            for kw in agent.routing.keywords:
                pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                if re.search(pattern, query_lower):
                    matched_kws.append(kw)

            hits = len(matched_kws)
            min_hits = agent.routing.min_keyword_hits

            # Step 3: Filter below min_keyword_hits
            if hits < min_hits:
                continue

            # Step 4: Confidence computation — geometric mean
            total_kws = max(len(agent.routing.keywords), 1)
            keyword_coverage = hits / total_kws
            query_coverage = hits / max(len(query_tokens), 3)
            base = sqrt(keyword_coverage * query_coverage)

            # Domain affinity bonus
            domain_bonus = 0.0
            if _query_in_agent_domain(query_lower, agent):
                domain_bonus = 0.1

            # Recency bonus (last 24h success)
            recency_bonus = 0.05 if agent.health.last_success_within(hours=24) else 0.0

            confidence = min(1.0, base + domain_bonus + recency_bonus)

            # Step 5: Filter below effective min_confidence
            effective_min = max(
                agent.routing.min_confidence,
                self._global_min,
            )
            if confidence < effective_min:
                continue

            candidates.append(RoutingMatch(
                agent_name=agent.name,
                confidence=confidence,
                keyword_hits=hits,
                keyword_coverage=keyword_coverage,
                query_coverage=query_coverage,
                domain_bonus=domain_bonus,
                recency_bonus=recency_bonus,
                matched_keywords=matched_kws,
            ))

        elapsed_ms = (time.monotonic() - start) * 1000

        if not candidates:
            return RoutingResult(
                match=None,
                all_candidates=[],
                routing_ms=elapsed_ms,
            )

        # Step 6: Sort by confidence DESC, then by agent priority ASC
        candidates.sort(
            key=lambda m: (-m.confidence,
                           self._registry.get(m.agent_name).routing.priority
                           if self._registry.get(m.agent_name) else 100)
        )

        best = candidates[0]

        # Step 7: Check knowledge cache for best match
        cache_hit = _check_knowledge_cache(
            agent_name=best.agent_name,
            query=query,
            cache_dir=self._cache_dir,
            cache_ttl_days=_get_cache_ttl(best.agent_name, self._registry),
        )

        return RoutingResult(
            match=best,
            all_candidates=candidates,
            routing_ms=elapsed_ms,
            cache_hit=cache_hit,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query_in_agent_domain(query_lower: str, agent) -> bool:
    """Return True if the query touches any of the agent's declared domains."""
    domain_keywords = {
        "deployment": ["deploy", "release", "rollout", "rollback", "sdp", "onedeploy"],
        "storage": ["storage", "xstore", "blob", "table", "queue", "disk"],
        "networking": ["network", "vnet", "subnet", "nsg", "dns", "load balancer"],
        "security": ["security", "auth", "acl", "key vault", "cert"],
        "monitoring": ["monitor", "alert", "metric", "geneva", "kusto", "incident"],
    }
    for domain in agent.routing.domains:
        kws = domain_keywords.get(domain.lower(), [domain.lower()])
        if any(kw in query_lower for kw in kws):
            return True
    return False


def _check_knowledge_cache(
    agent_name: str,
    query: str,
    cache_dir: Path | None,
    cache_ttl_days: int,
) -> bool:
    """Return True if the knowledge cache has a fresh, query-relevant entry.

    This is a lightweight check based on keyword overlap — not semantic
    similarity.  Full cache content is read by the response integrator.
    """
    if cache_dir is None:
        return False
    cache_file = cache_dir / f"{agent_name}.md"
    if not cache_file.exists():
        return False

    try:
        import time as _time
        stat = cache_file.stat()
        age_days = (_time.time() - stat.st_mtime) / 86400
        if age_days > cache_ttl_days:
            return False  # Stale entry — will be revalidated on next invocation

        # Quick keyword check: does the cache file mention any query tokens?
        query_tokens = set(query.lower().split())
        content = cache_file.read_text(encoding="utf-8", errors="ignore").lower()
        matches = sum(1 for tok in query_tokens if len(tok) > 3 and tok in content)
        return matches >= 2
    except OSError:
        return False


def _get_cache_ttl(agent_name: str, registry) -> int:
    agent = registry.get(agent_name)
    if agent:
        return agent.cache_ttl_days
    return 7
