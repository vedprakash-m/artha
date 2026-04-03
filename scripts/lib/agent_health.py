"""AR-9 External Agent Composition — Health Tracking (§4.8).

Implements the health state machine and auto-retirement logic for
external agents.

State machine:
  active → degraded  (3 consecutive failures OR sustained low quality)
  active → suspended (injection detected)
  degraded → active  (1 success recovery)
  suspended → active (manual reinstate only)
  any → retired      (user command OR auto-retirement criteria met)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.agent_registry import AgentHealth, AgentRegistry, ExternalAgent

# EA-10a / EA-11a — import writers at module level with graceful fallback
try:
    from lib.metrics_writer import write_invocation_metric as _write_invocation_metric
except ImportError:  # pragma: no cover
    _write_invocation_metric = None  # type: ignore[assignment]

try:
    from lib.ext_agent_audit import write_ext_agent_event as _write_audit_event
except ImportError:  # pragma: no cover
    _write_audit_event = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Thresholds (spec §4.8)
# ---------------------------------------------------------------------------

_CONSECUTIVE_FAILURES_DEGRADED = 3        # 3 consecutive failures → degraded
_QUALITY_DEGRADED_THRESHOLD = 0.4         # mean quality < 0.4 → flag for retirement
_QUALITY_DEGRADED_DAYS = 30               # ...sustained for 30 days
_CACHE_HIT_RATE_LOW = 0.05                # cache_hit_rate < 5%
_CACHE_HIT_RATE_MIN_INVOCATIONS = 20      # ...with ≥ 20 invocations
_SUSPENSION_AUTO_RETIRE_DAYS = 30         # suspended ≥ 30 days → auto-retire
_TRUST_PROMOTION_THRESHOLD = 10           # default successful invocations for promotion


# ---------------------------------------------------------------------------
# Trust tier ordering
# ---------------------------------------------------------------------------

_TRUST_ORDER = ["untrusted", "external", "verified", "trusted", "owned"]


def _promote_trust(current: str) -> str | None:
    """Return the next trust tier up, or None if already at top."""
    try:
        idx = _TRUST_ORDER.index(current)
    except ValueError:
        return None
    if idx + 1 < len(_TRUST_ORDER):
        return _TRUST_ORDER[idx + 1]
    return None


def _demote_trust(current: str) -> str | None:
    """Return the next trust tier down, or None if already at bottom."""
    try:
        idx = _TRUST_ORDER.index(current)
    except ValueError:
        return None
    if idx - 1 >= 0:
        return _TRUST_ORDER[idx - 1]
    return None


# ---------------------------------------------------------------------------
# AgentHealthTracker
# ---------------------------------------------------------------------------

class AgentHealthTracker:
    """Records invocation outcomes and manages agent health state transitions.

    Usage::

        tracker = AgentHealthTracker(registry=registry, config=config_dict)
        tracker.record_invocation(
            agent_name="storage-deployment-expert",
            success=True,
            latency_ms=4500,
            quality_score=0.82,
        )
    """

    def __init__(
        self,
        registry: "AgentRegistry",
        config: dict | None = None,
    ) -> None:
        self._registry = registry
        cfg = config or {}
        trust_cfg = cfg.get("trust", {})
        self._auto_promotion: bool = trust_cfg.get("auto_promotion", True)
        self._auto_demotion: bool = trust_cfg.get("auto_demotion", True)
        self._promotion_threshold: int = trust_cfg.get(
            "promotion_threshold", _TRUST_PROMOTION_THRESHOLD
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_invocation(
        self,
        agent_name: str,
        success: bool,
        latency_ms: float,
        quality_score: float | None = None,
        failure_reason: str | None = None,
        injection_detected: bool = False,
        user_rejected: bool = False,
        cache_hit: bool = False,
        fallback_level: int | None = None,
    ) -> None:
        """Record one invocation outcome and update health state.

        Triggers state machine transitions and auto-retirement checks.
        """
        agent = self._registry.get(agent_name)
        if agent is None:
            return

        health = agent.health
        now_iso = datetime.now(timezone.utc).isoformat()

        # ----------------------------------------------------------
        # Update counters
        # ----------------------------------------------------------
        health.total_invocations = (health.total_invocations or 0) + 1
        health.last_invocation = now_iso

        if success:
            health.successful_invocations = (health.successful_invocations or 0) + 1
            health.consecutive_failures = 0
            health.last_success = now_iso
            if quality_score is not None:
                health.mean_quality_score = _update_mean(
                    health.mean_quality_score or 0.0,
                    quality_score,
                    health.successful_invocations,
                )
        else:
            health.failed_invocations = (health.failed_invocations or 0) + 1
            health.consecutive_failures = (health.consecutive_failures or 0) + 1
            health.last_failure = now_iso
            if failure_reason:
                health.last_failure_reason = failure_reason

        if injection_detected:
            health.injection_detections = (health.injection_detections or 0) + 1

        if user_rejected:
            health.user_rejections = (health.user_rejections or 0) + 1

        # Latency rolling average
        if latency_ms > 0:
            n = health.total_invocations
            health.mean_latency_ms = _update_mean(
                health.mean_latency_ms or 0.0, latency_ms, n
            )
            # Crude P95 estimate: if this latency is > p95, update
            if latency_ms > (health.p95_latency_ms or 0.0):
                health.p95_latency_ms = latency_ms

        # Cache hit rate (rolling)
        if cache_hit:
            total = health.total_invocations
            prev_hits = round((health.cache_hit_rate or 0.0) * max(1, total - 1))
            health.cache_hit_rate = (prev_hits + 1) / total

        # ----------------------------------------------------------
        # State machine transitions
        # ----------------------------------------------------------
        current_status = health.status or "active"

        if injection_detected and current_status != "suspended":
            self._transition(agent, health, "suspended", "injection_detected")

        elif success and current_status == "degraded":
            # Recovery: 1 success restores active
            self._transition(agent, health, "active", "recovery")

        elif (
            not success
            and health.consecutive_failures >= _CONSECUTIVE_FAILURES_DEGRADED
            and current_status == "active"
        ):
            self._transition(agent, health, "degraded", "consecutive_failures")

        # ----------------------------------------------------------
        # Auto-retirement checks
        # ----------------------------------------------------------
        self._check_auto_retirement(agent, health)

        # ----------------------------------------------------------
        # Trust tier progression
        # ----------------------------------------------------------
        if success and self._auto_promotion and quality_score is not None:
            self._check_promotion(agent, health)
        if not success and self._auto_demotion:
            self._check_demotion(agent, health)

        # ----------------------------------------------------------
        # Persist changes
        # ----------------------------------------------------------
        self._registry.update_health(agent_name, health)

        # EA-10a: append invocation metric record
        if _write_invocation_metric is not None:
            try:
                _write_invocation_metric(
                    agent_name=agent_name,
                    success=success,
                    latency_ms=latency_ms,
                    quality_score=quality_score,
                    cache_hit=cache_hit,
                    fallback_level=fallback_level,
                    failure_reason=failure_reason,
                )
            except Exception:  # pragma: no cover
                pass

        # EA-11a: append audit event for non-cache invocations
        if _write_audit_event is not None and not cache_hit:
            try:
                _detail = (
                    f"latency={latency_ms:.0f}ms "
                    f"quality={quality_score or 0.0:.2f} "
                    f"success={success}"
                )
                _write_audit_event("EXT_AGENT_INVOKED", agent_name, _detail)
            except Exception:  # pragma: no cover
                pass

    def record_injection(self, agent_name: str) -> None:
        """Shortcut: record an injection detection event → suspend agent."""
        self.record_invocation(
            agent_name=agent_name,
            success=False,
            latency_ms=0,
            failure_reason="injection_detected",
            injection_detected=True,
        )

    def record_user_rejection(self, agent_name: str) -> None:
        """Record a user rejection (thumbs-down on agent output)."""
        self.record_invocation(
            agent_name=agent_name,
            success=False,
            latency_ms=0,
            failure_reason="user_rejected",
            user_rejected=True,
        )

    # EA-12a: keyword effectiveness tracking
    def record_keyword_quality(
        self,
        agent_name: str,
        keywords: list[str],
        quality_score: float,
    ) -> None:
        """Record a quality score for each keyword that matched this invocation.

        Builds a per-keyword rolling list of quality scores that can be used
        to identify high-signal vs low-signal routing keywords.
        """
        agent = self._registry.get(agent_name)
        if agent is None:
            return
        health = agent.health
        if health.keyword_quality is None:
            health.keyword_quality = {}
        for kw in keywords:
            scores = health.keyword_quality.setdefault(kw, [])
            scores.append(float(quality_score))
            # Keep rolling window of last 20 scores per keyword
            if len(scores) > 20:
                health.keyword_quality[kw] = scores[-20:]
        self._registry.update_health(agent_name, health)

    # EA-13a: known weak area recording
    _WEAK_QUALITY_THRESHOLD: float = 0.3

    def record_weak_query(
        self,
        agent_name: str,
        query_pattern: str,
    ) -> None:
        """Explicitly record a known weak query pattern for this agent.

        Patterns are stored verbatim and filtered against by the router
        (EA-13b) to avoid re-routing similar queries.
        """
        agent = self._registry.get(agent_name)
        if agent is None:
            return
        health = agent.health
        if health.weak_queries is None:
            health.weak_queries = []
        # Avoid duplicates
        if query_pattern not in health.weak_queries:
            health.weak_queries.append(query_pattern)
            # Keep a bounded list (at most 50 weak patterns)
            if len(health.weak_queries) > 50:
                health.weak_queries = health.weak_queries[-50:]
        self._registry.update_health(agent_name, health)

    def maybe_record_weak_query(
        self,
        agent_name: str,
        query: str,
        quality_score: float,
    ) -> None:
        """Auto-record a weak query when quality_score < _WEAK_QUALITY_THRESHOLD.

        Called by record_invocation callers that have a quality score available.
        Extracts a normalised pattern from the query (lowercased, trimmed).
        """
        if quality_score < self._WEAK_QUALITY_THRESHOLD:
            pattern = query.strip().lower()[:120]
            self.record_weak_query(agent_name, pattern)

    # EA-14a: per-cluster cache hit rate
    def record_cache_hit_cluster(
        self,
        agent_name: str,
        cluster_keyword: str,
    ) -> None:
        """Record a cache hit attributed to the given cluster keyword.

        The cluster keyword is the first matched routing keyword, which is
        used as a cheap proxy for query cluster.
        """
        agent = self._registry.get(agent_name)
        if agent is None:
            return
        health = agent.health
        if health.cache_hits_by_cluster is None:
            health.cache_hits_by_cluster = {}
        health.cache_hits_by_cluster[cluster_keyword] = (
            health.cache_hits_by_cluster.get(cluster_keyword, 0) + 1
        )
        self._registry.update_health(agent_name, health)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(
        self,
        agent: "ExternalAgent",
        health: "AgentHealth",
        new_status: str,
        reason: str,
    ) -> None:
        """Apply a status transition and log it."""
        old = health.status or "active"
        if old == new_status:
            return
        health.status = new_status
        # Also update the agent's top-level status
        agent.status = new_status
        # For retirement, disable the agent
        if new_status == "retired":
            agent.enabled = False

        # EA-11a: audit health state transition
        if _write_audit_event is not None:
            try:
                _write_audit_event(
                    "EXT_AGENT_HEALTH",
                    agent.name,
                    f"{old}\u2192{new_status} reason={reason}",
                )
            except Exception:  # pragma: no cover
                pass

    def _check_auto_retirement(
        self, agent: "ExternalAgent", health: "AgentHealth"
    ) -> None:
        """Check auto-retirement criteria (spec §4.8)."""
        if health.status == "retired":
            return

        # Criterion 1: sustained low quality for 30+ days
        if (
            (health.mean_quality_score or 1.0) < _QUALITY_DEGRADED_THRESHOLD
            and (health.total_invocations or 0) >= 10
        ):
            last_success = health.last_success
            if last_success:
                try:
                    ls_dt = datetime.fromisoformat(last_success.rstrip("Z"))
                    ls_dt = ls_dt.replace(tzinfo=timezone.utc)
                    days_since = (datetime.now(timezone.utc) - ls_dt).days
                    if days_since >= _QUALITY_DEGRADED_DAYS:
                        self._transition(agent, health, "retired", "sustained_low_quality")
                        return
                except ValueError:
                    pass

        # Criterion 2: near-zero cache utility
        if (
            (health.cache_hit_rate or 1.0) < _CACHE_HIT_RATE_LOW
            and (health.total_invocations or 0) >= _CACHE_HIT_RATE_MIN_INVOCATIONS
        ):
            # Flag only — not auto-retire (per spec: "flag for retirement; suggest disabling")
            pass

        # Criterion 3: extended suspension
        if health.status == "suspended" and health.last_failure:
            try:
                lf_dt = datetime.fromisoformat(health.last_failure.rstrip("Z"))
                lf_dt = lf_dt.replace(tzinfo=timezone.utc)
                days_suspended = (datetime.now(timezone.utc) - lf_dt).days
                if days_suspended >= _SUSPENSION_AUTO_RETIRE_DAYS:
                    self._transition(agent, health, "retired", "extended_suspension")
            except ValueError:
                pass

    def _check_promotion(
        self, agent: "ExternalAgent", health: "AgentHealth"
    ) -> None:
        """Auto-promote trust tier after reaching promotion threshold."""
        if not self._auto_promotion:
            return
        successful = health.successful_invocations or 0
        current_tier = agent.trust_tier or "external"
        if successful >= self._promotion_threshold:
            new_tier = _promote_trust(current_tier)
            if new_tier and new_tier != current_tier:
                old_tier = current_tier
                agent.trust_tier = new_tier
                # Reset threshold so next promotion requires another batch
                promotion_event = (
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}: "
                    f"{old_tier}→{new_tier}"
                )
                if health.trust_promotions is None:
                    health.trust_promotions = []
                health.trust_promotions.append(promotion_event)

    def _check_demotion(
        self, agent: "ExternalAgent", health: "AgentHealth"
    ) -> None:
        """Auto-demote trust tier on injection detection."""
        if not self._auto_demotion:
            return
        if (health.injection_detections or 0) > 0:
            current_tier = agent.trust_tier or "external"
            new_tier = _demote_trust(current_tier)
            if new_tier and new_tier != current_tier:
                old_tier = current_tier
                agent.trust_tier = new_tier
                demotion_event = (
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}: "
                    f"{old_tier}→{new_tier} (injection)"
                )
                if health.trust_demotions is None:
                    health.trust_demotions = []
                health.trust_demotions.append(demotion_event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_mean(current_mean: float, new_value: float, n: int) -> float:
    """Incremental rolling mean update."""
    if n <= 1:
        return new_value
    return current_mean + (new_value - current_mean) / n
