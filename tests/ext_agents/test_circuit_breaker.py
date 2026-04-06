"""tests/ext_agents/test_circuit_breaker.py — AR-9 circuit breaker logic (§11.3).

Tests:
  H-2: 5 consecutive failures → suspended
  H-6: Circuit recovery after probe success
  R-6: Suspended agent is never routed to
  Plus: injection-based suspension, stability under continued failures,
        manual reinstatement, and routing integration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent_health import (  # type: ignore
    AgentHealthTracker,
    _CONSECUTIVE_FAILURES_DEGRADED,
    _CONSECUTIVE_FAILURES_SUSPENDED,
)
from lib.agent_registry import AgentRegistry  # type: ignore
from lib.agent_router import AgentRouter  # type: ignore
from .conftest import make_test_agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry(tmp_registry_dir: Path) -> AgentRegistry:
    reg = AgentRegistry.load(tmp_registry_dir)
    agent = make_test_agent()
    reg.register(agent)
    return reg


@pytest.fixture()
def tracker(registry: AgentRegistry) -> AgentHealthTracker:
    return AgentHealthTracker(registry=registry)


# ---------------------------------------------------------------------------
# §11.3 — Circuit Breaker: state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreakerTransitions:
    """State machine transitions that implement the circuit breaker."""

    def test_3_failures_degrades_circuit_open(self, tracker: AgentHealthTracker):
        """3 consecutive failures → degraded (circuit OPEN — spec §11.3 first check)."""
        for _ in range(_CONSECUTIVE_FAILURES_DEGRADED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        agent = tracker._registry.get("test-agent")
        assert agent.health.status == "degraded"

    def test_5_failures_suspends(self, tracker: AgentHealthTracker):
        """H-2: 5 consecutive failures → suspended (auto-suspend — spec §11.3 second check)."""
        for _ in range(_CONSECUTIVE_FAILURES_SUSPENDED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        agent = tracker._registry.get("test-agent")
        assert agent.health.status == "suspended"
        assert agent.health.consecutive_failures == _CONSECUTIVE_FAILURES_SUSPENDED

    def test_degraded_before_suspended(self, tracker: AgentHealthTracker):
        """Agent goes active → degraded → suspended in sequence, not jumping straight."""
        statuses: list[str] = []
        for i in range(_CONSECUTIVE_FAILURES_SUSPENDED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
            agent = tracker._registry.get("test-agent")
            statuses.append(agent.health.status)

        # First 2 should be 'active', 3rd should be 'degraded', 5th should be 'suspended'
        assert statuses[_CONSECUTIVE_FAILURES_DEGRADED - 1] == "degraded"
        assert statuses[_CONSECUTIVE_FAILURES_SUSPENDED - 1] == "suspended"

    def test_recovery_from_degraded_closes_circuit(self, tracker: AgentHealthTracker):
        """1 success after degraded → active (circuit closes)."""
        for _ in range(_CONSECUTIVE_FAILURES_DEGRADED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        assert tracker._registry.get("test-agent").health.status == "degraded"

        tracker.record_invocation(
            "test-agent", success=True, latency_ms=50, quality_score=0.7
        )
        agent = tracker._registry.get("test-agent")
        assert agent.health.status == "active"
        assert agent.health.consecutive_failures == 0

    def test_injection_immediate_suspend(self, tracker: AgentHealthTracker):
        """Injection detection → suspended regardless of failure count."""
        tracker.record_injection("test-agent")
        agent = tracker._registry.get("test-agent")
        assert agent.health.status == "suspended"

    def test_suspended_stable_on_more_failures(self, tracker: AgentHealthTracker):
        """Further failures after suspension don't change the state (stays suspended)."""
        for _ in range(_CONSECUTIVE_FAILURES_SUSPENDED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        assert tracker._registry.get("test-agent").health.status == "suspended"

        # 5 more failures — should not crash or change state
        for _ in range(5):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        agent = tracker._registry.get("test-agent")
        assert agent.health.status == "suspended"

    def test_suspended_does_not_auto_recover_on_success(self, tracker: AgentHealthTracker):
        """Success while suspended does NOT auto-restore (manual reinstate only per spec)."""
        for _ in range(_CONSECUTIVE_FAILURES_SUSPENDED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        assert tracker._registry.get("test-agent").health.status == "suspended"

        # A success should NOT transition to active (only degraded→active is auto)
        tracker.record_invocation(
            "test-agent", success=True, latency_ms=50, quality_score=0.8
        )
        agent = tracker._registry.get("test-agent")
        assert agent.health.status == "suspended"


# ---------------------------------------------------------------------------
# §11.3 — Circuit Breaker: routing integration
# ---------------------------------------------------------------------------


class TestCircuitBreakerRouting:
    """Verify that degraded/suspended agents are handled correctly by the router."""

    def test_suspended_agent_not_routed(self, registry: AgentRegistry, tracker: AgentHealthTracker):
        """R-6: Suspended agent is never routed to, even with matching keywords."""
        # Suspend the agent via circuit breaker
        for _ in range(_CONSECUTIVE_FAILURES_SUSPENDED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        assert registry.get("test-agent").health.status == "suspended"

        router = AgentRouter(registry)
        result = router.route("deployment stuck SDP block canary")
        assert result.match is None

    def test_degraded_agent_still_routes(self, registry: AgentRegistry, tracker: AgentHealthTracker):
        """Degraded agents are still routable (circuit open = skip in fast path, but registry considers them active)."""
        for _ in range(_CONSECUTIVE_FAILURES_DEGRADED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        assert registry.get("test-agent").health.status == "degraded"

        # is_active() returns True for degraded status — the routing gate decides
        # whether to skip (circuit open) or probe
        router = AgentRouter(registry)
        result = router.route("deployment stuck SDP block canary")
        assert result.match is not None

    def test_active_agent_routes_normally(self, registry: AgentRegistry):
        """Baseline: active agent routes normally with matching keywords."""
        router = AgentRouter(registry)
        result = router.route("deployment stuck SDP block canary")
        assert result.match is not None
        assert result.match.agent_name == "test-agent"


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestCircuitBreakerConstants:
    """Verify circuit breaker thresholds are correctly configured."""

    def test_degraded_threshold_positive(self):
        assert _CONSECUTIVE_FAILURES_DEGRADED > 0

    def test_suspended_threshold_greater_than_degraded(self):
        assert _CONSECUTIVE_FAILURES_SUSPENDED > _CONSECUTIVE_FAILURES_DEGRADED

    def test_suspended_threshold_is_5(self):
        """Spec §11.3 requires exactly 5 consecutive failures for suspension."""
        assert _CONSECUTIVE_FAILURES_SUSPENDED == 5
