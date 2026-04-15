# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/agent_router.py — External agent routing engine for AR-9 / EAR v2.

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

EAR extensions:
  - route_multi(query, top_n): returns multiple domain-independent candidates
    for fan-out (EAR-5).  Includes domain independence check.  (R-2)
  - Confidence margin logging: every route() call emits top1−top2 margin to
    ext-agent-metrics.jsonl as a routing_margin record.  (R-1, R-3)

Also handles:
  - Cache-hit shortcut: if knowledge cache has a fresh entry, return from
    cache without routing to the live agent
  - Conflict detection: tied candidates are logged to audit
  - Exclude-keyword suppression

Ref: specs/subagent-ext-agent.md §4.3, EA-2a/2b
     specs/ext-agent-reloaded.md §Phase 0 BLOCKING-2, BLOCKING-3
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
    routing_ambiguity: bool = False      # True when top-2 margin < 0.05 (DEBT-032)


# EAR-4: TF-IDF lexical fallback threshold (spec §EAR-4, §Phase 0 BLOCKING-3)
# When keyword confidence < this, attempt TF-IDF trigram routing.
_LEXICAL_FALLBACK_THRESHOLD: float = 0.4

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
            # EAR-4: No keyword match → try TF-IDF lexical fallback
            return self._tfidf_fallback(query, start)

        # Step 6: Sort by confidence DESC, then by agent priority ASC
        candidates.sort(
            key=lambda m: (-m.confidence,
                           self._registry.get(m.agent_name).routing.priority
                           if self._registry.get(m.agent_name) else 100)
        )

        best = candidates[0]

        # EAR-4: If best keyword confidence is below the lexical fallback
        # threshold, attempt TF-IDF to see if it finds a higher-confidence match.
        # Keyword result is preferred if confidence is equal.
        if best.confidence < _LEXICAL_FALLBACK_THRESHOLD:
            _lex_result = self._tfidf_fallback(query, start)
            if (
                _lex_result.match is not None
                and _lex_result.match.confidence > best.confidence
            ):
                _emit_routing_margin(candidates, elapsed_ms)
                return _lex_result

        # Step 7: Check knowledge cache for best match
        cache_hit = _check_knowledge_cache(
            agent_name=best.agent_name,
            query=query,
            cache_dir=self._cache_dir,
            cache_ttl_days=_get_cache_ttl(best.agent_name, self._registry),
        )

        # BLOCKING-3 (R-1): Emit confidence margin to metrics JSONL
        ambiguous = _emit_routing_margin(candidates, elapsed_ms)

        return RoutingResult(
            match=best,
            all_candidates=candidates,
            routing_ms=elapsed_ms,
            cache_hit=cache_hit,
            routing_ambiguity=ambiguous,
        )

    def _tfidf_fallback(
        self,
        query: str,
        start_time: float,
    ) -> RoutingResult:
        """EAR-4: TF-IDF lexical fallback when keyword routing yields no match
        or confidence < _LEXICAL_FALLBACK_THRESHOLD.

        Returns RoutingResult with best lexical match, or empty result.
        """
        elapsed_ms = (time.monotonic() - start_time) * 1000
        try:
            from lib.tfidf_router import TFIDFRouter  # noqa: PLC0415
            lexical = TFIDFRouter()
            matches = lexical.query(
                query=query,
                top_n=1,
                min_score=_LEXICAL_FALLBACK_THRESHOLD,
            )
            if not matches:
                return RoutingResult(match=None, all_candidates=[], routing_ms=elapsed_ms)

            best_lex = matches[0]
            # Convert LexicalMatch → RoutingMatch
            rm = RoutingMatch(
                agent_name=best_lex.agent_name,
                confidence=best_lex.score,
                keyword_hits=0,
                keyword_coverage=0.0,
                query_coverage=0.0,
                domain_bonus=0.0,
                recency_bonus=0.0,
                matched_keywords=[],
            )
            cache_hit = _check_knowledge_cache(
                agent_name=rm.agent_name,
                query=query,
                cache_dir=self._cache_dir,
                cache_ttl_days=_get_cache_ttl(rm.agent_name, self._registry),
            )
            return RoutingResult(
                match=rm,
                all_candidates=[rm],
                routing_ms=(time.monotonic() - start_time) * 1000,
                cache_hit=cache_hit,
            )
        except (ImportError, Exception):
            return RoutingResult(match=None, all_candidates=[], routing_ms=elapsed_ms)

    def route_multi(
        self,
        query: str,
        top_n: int = 3,
    ) -> list[RoutingMatch]:
        """Return up to top_n domain-independent candidates above min_confidence.

        Algorithm (BLOCKING-2, EAR-5, R-2):
          1. Run the base candidate enumeration (same as route()).
          2. Filter to candidates that individually clear min_confidence.
          3. Apply domain independence check: exclude agents whose domain set
             overlaps (non-empty intersection) with any already-selected agent.
          4. Sort by confidence DESC.
          5. Return first top_n candidates.

        Domain independence check: two agents are independent iff their
        domain sets are disjoint.  If agents A and B share any domain tag,
        only the higher-confidence one is included in the batch.

        Parameters:
            query:  The user's question.
            top_n:  Maximum candidates to return (default 3, per EAR-5 cap).

        Returns empty list if no qualified candidates.

        Ref: specs/ext-agent-reloaded.md §Phase 0 BLOCKING-2
        """
        start = time.monotonic()
        candidates: list[RoutingMatch] = []
        query_lower = query.lower()
        query_tokens = set(query_lower.split())

        for agent in self._registry.active_agents():
            if not agent.enabled:
                continue
            if any(
                re.search(r'\b' + re.escape(ex.lower()) + r'\b', query_lower)
                for ex in agent.routing.exclude_keywords
            ):
                continue
            weak_queries = getattr(agent.health, "weak_queries", None) or []
            if any(wq.lower() in query_lower for wq in weak_queries):
                continue

            matched_kws: list[str] = []
            for kw in agent.routing.keywords:
                pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                if re.search(pattern, query_lower):
                    matched_kws.append(kw)

            hits = len(matched_kws)
            if hits < agent.routing.min_keyword_hits:
                continue

            total_kws = max(len(agent.routing.keywords), 1)
            keyword_coverage = hits / total_kws
            query_coverage = hits / max(len(query_tokens), 3)
            base = sqrt(keyword_coverage * query_coverage)
            domain_bonus = 0.1 if _query_in_agent_domain(query_lower, agent) else 0.0
            recency_bonus = 0.05 if agent.health.last_success_within(hours=24) else 0.0
            confidence = min(1.0, base + domain_bonus + recency_bonus)

            effective_min = max(agent.routing.min_confidence, self._global_min)
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
            return []

        candidates.sort(
            key=lambda m: (-m.confidence,
                           self._registry.get(m.agent_name).routing.priority
                           if self._registry.get(m.agent_name) else 100)
        )

        # Domain independence check: greedily select non-overlapping agents
        selected: list[RoutingMatch] = []
        selected_domains: set[str] = set()

        for candidate in candidates:
            if len(selected) >= top_n:
                break
            agent_obj = self._registry.get(candidate.agent_name)
            agent_domains: set[str] = set()
            if agent_obj:
                agent_domains = {d.lower() for d in (agent_obj.routing.domains or [])}

            # If domains overlap with already-selected → skip
            if agent_domains and selected_domains and (agent_domains & selected_domains):
                continue

            selected.append(candidate)
            selected_domains |= agent_domains

        # Emit margin for the multi-result set
        _emit_routing_margin(candidates, elapsed_ms)

        return selected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AMBIGUITY_THRESHOLD: float = 0.05  # DEBT-032: margin below which route is ambiguous


def _emit_routing_margin(
    candidates: list[RoutingMatch],
    routing_ms: float,
) -> bool:
    """Emit a routing_margin record. Fire-and-forget. (BLOCKING-3, R-1)

    Returns True when the route is ambiguous (top-2 margin < _AMBIGUITY_THRESHOLD).
    (DEBT-032)
    """
    try:
        from lib.metrics_writer import write_routing_margin  # noqa: PLC0415
        top1 = candidates[0] if len(candidates) >= 1 else None
        top2 = candidates[1] if len(candidates) >= 2 else None
        if top1 is None:
            return False
        margin = (top1.confidence - top2.confidence) if top2 else 1.0
        ambiguous = top2 is not None and margin < _AMBIGUITY_THRESHOLD
        # keyword_miss_rate = fraction of query tokens NOT matched (DEBT-020)
        keyword_miss_rate = max(0.0, 1.0 - top1.query_coverage)
        write_routing_margin(
            top1_agent=top1.agent_name,
            top1_confidence=top1.confidence,
            top2_agent=top2.agent_name if top2 else None,
            top2_confidence=top2.confidence if top2 else 0.0,
            confidence_margin=margin,
            routing_ms=routing_ms,
            keyword_miss_rate=keyword_miss_rate,
        )
        return ambiguous
    except Exception:   # noqa: BLE001
        pass  # Never block routing on metrics failure
    return False

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


# ---------------------------------------------------------------------------
# DEBT-ROUTE-001: Ambient routing ambiguity tracking
# ---------------------------------------------------------------------------

def compute_ambiguity_rate(routing_log_path: "Path | None" = None) -> dict:
    """Read the routing audit log and compute the ambient routing ambiguity rate.

    Returns a dict with:
      - total_decisions:  int  — total routing decisions logged
      - ambiguous_count:  int  — decisions flagged routing_ambiguity=True
      - ambiguity_rate:   float — ambiguous_count / total (0.0 if no data)
      - alert:            str | None — human-readable alert if rate > 0.20

    Writes the result to state/eval_metrics.yaml under the key
    'routing_ambiguity_rate' so the eval runner and briefing system can surface it.

    Never raises — returns empty structure on any error.
    """
    from pathlib import Path as _Path
    import json as _json_ar
    import time as _time_ar

    _ALERT_THRESHOLD = 0.20   # warn if >20% of decisions are ambiguous

    result: dict = {
        "total_decisions": 0,
        "ambiguous_count": 0,
        "ambiguity_rate": 0.0,
        "alert": None,
        "computed_at": _time_ar.strftime("%Y-%m-%dT%H:%M:%SZ", _time_ar.gmtime()),
    }

    try:
        _artha_dir = _Path(__file__).resolve().parent.parent.parent
        log_path = routing_log_path or (_artha_dir / "state" / "routing_audit.jsonl")

        if not log_path.exists():
            return result

        total = 0
        ambiguous = 0
        with log_path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json_ar.loads(line)
                    total += 1
                    if rec.get("routing_ambiguity"):
                        ambiguous += 1
                except Exception:
                    continue

        result["total_decisions"] = total
        result["ambiguous_count"] = ambiguous
        if total > 0:
            rate = ambiguous / total
            result["ambiguity_rate"] = round(rate, 4)
            if rate > _ALERT_THRESHOLD:
                result["alert"] = (
                    f"Routing ambiguity rate {rate:.1%} exceeds threshold {_ALERT_THRESHOLD:.0%} "
                    f"({ambiguous}/{total} decisions ambiguous) — review routing config."
                )

        # Write to eval_metrics.yaml for visibility in briefing footer
        _write_ambiguity_metric(_artha_dir, result)
    except Exception:  # noqa: BLE001
        pass

    return result


def _write_ambiguity_metric(artha_dir: "Path", data: dict) -> None:
    """Upsert routing_ambiguity_rate into state/eval_metrics.yaml (DEBT-ROUTE-001)."""
    from pathlib import Path as _Path
    import yaml as _yaml_rm  # type: ignore[import]
    metrics_path = artha_dir / "state" / "eval_metrics.yaml"
    try:
        existing: dict = {}
        if metrics_path.exists():
            existing = _yaml_rm.safe_load(metrics_path.read_text(encoding="utf-8")) or {}
        existing["routing_ambiguity_rate"] = data
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(
            _yaml_rm.dump(existing, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass
