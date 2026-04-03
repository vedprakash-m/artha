"""tests/ext_agents/test_agent_health.py — AR-9 AgentHealthTracker tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent_health import AgentHealthTracker, _CONSECUTIVE_FAILURES_DEGRADED  # type: ignore
from lib.agent_registry import AgentRegistry  # type: ignore
from .conftest import SAMPLE_AGENT_ENTRY, make_test_agent


@pytest.fixture()
def registry(tmp_registry_dir: Path) -> AgentRegistry:
    reg = AgentRegistry.load(tmp_registry_dir)
    agent = make_test_agent()
    reg.register(agent)
    return reg


@pytest.fixture()
def tracker(registry: AgentRegistry) -> AgentHealthTracker:
    return AgentHealthTracker(registry=registry)


class TestAgentHealthTracker:
    def test_record_success(self, tracker: AgentHealthTracker):
        tracker.record_invocation(
            agent_name="test-agent",
            success=True,
            latency_ms=100,
            quality_score=0.8,
        )
        agent = tracker._registry.get("test-agent")
        assert agent is not None
        assert agent.health.total_invocations == 1
        assert agent.health.successful_invocations == 1
        assert agent.health.consecutive_failures == 0

    def test_record_failure_increments(self, tracker: AgentHealthTracker):
        tracker.record_invocation(
            agent_name="test-agent",
            success=False,
            latency_ms=5000,
            quality_score=0.0,
        )
        agent = tracker._registry.get("test-agent")
        assert agent.health.consecutive_failures == 1

    def test_consecutive_failures_reset_on_success(self, tracker: AgentHealthTracker):
        tracker.record_invocation("test-agent", success=False, latency_ms=100, quality_score=0.0)
        tracker.record_invocation("test-agent", success=False, latency_ms=100, quality_score=0.0)
        tracker.record_invocation("test-agent", success=True, latency_ms=100, quality_score=0.8)
        agent = tracker._registry.get("test-agent")
        assert agent.health.consecutive_failures == 0

    def test_degraded_after_threshold_failures(self, tracker: AgentHealthTracker):
        """After _CONSECUTIVE_FAILURES_DEGRADED consecutive failures, agent status becomes 'degraded'."""
        for _ in range(_CONSECUTIVE_FAILURES_DEGRADED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        agent = tracker._registry.get("test-agent")
        assert agent is not None
        assert agent.health.status == "degraded" or agent.status == "degraded"

    def test_quality_score_averaged(self, tracker: AgentHealthTracker):
        tracker.record_invocation("test-agent", success=True, latency_ms=50, quality_score=0.8)
        tracker.record_invocation("test-agent", success=True, latency_ms=50, quality_score=0.6)
        agent = tracker._registry.get("test-agent")
        avg = agent.health.mean_quality_score
        if avg is not None:
            assert 0.6 <= avg <= 0.8

    def test_unknown_agent_ignored(self, tracker: AgentHealthTracker):
        """Recording for unknown agent should not raise."""
        tracker.record_invocation(
            "does-not-exist", success=True, latency_ms=50, quality_score=0.8
        )

    def test_total_invocations_increments(self, tracker: AgentHealthTracker):
        tracker.record_invocation("test-agent", success=True, latency_ms=50, quality_score=0.5)
        tracker.record_invocation("test-agent", success=True, latency_ms=50, quality_score=0.5)
        agent = tracker._registry.get("test-agent")
        assert agent.health.total_invocations == 2

    def test_last_invocation_set(self, tracker: AgentHealthTracker):
        tracker.record_invocation("test-agent", success=True, latency_ms=50, quality_score=0.7)
        agent = tracker._registry.get("test-agent")
        assert agent.health.last_invocation is not None

    def test_health_persisted_to_registry(self, tracker: AgentHealthTracker, registry: AgentRegistry):
        tracker.record_invocation("test-agent", success=True, latency_ms=50, quality_score=0.7)
        # Data should be accessible via registry
        agent = registry.get("test-agent")
        assert agent.health.total_invocations >= 1

    def test_degrade_threshold_positive(self):
        assert _CONSECUTIVE_FAILURES_DEGRADED > 0

    def test_recovery_from_degraded(self, tracker: AgentHealthTracker):
        """One success after degraded should restore to active."""
        for _ in range(_CONSECUTIVE_FAILURES_DEGRADED):
            tracker.record_invocation(
                "test-agent", success=False, latency_ms=100, quality_score=0.0
            )
        tracker.record_invocation("test-agent", success=True, latency_ms=50, quality_score=0.8)
        agent = tracker._registry.get("test-agent")
        assert agent.health.status == "active" or agent.status == "active"

    def test_low_quality_does_not_raise(self, tracker: AgentHealthTracker):
        """Consistently low quality should not cause exceptions."""
        for _ in range(5):
            tracker.record_invocation(
                "test-agent", success=True, latency_ms=50, quality_score=0.1
            )
        agent = tracker._registry.get("test-agent")
        assert agent is not None


# ---------------------------------------------------------------------------
# EA-12a — Keyword effectiveness tracking
# ---------------------------------------------------------------------------


class TestKeywordEffectiveness:
    def test_record_keyword_quality_stores_scores(self, tracker: AgentHealthTracker):
        """Quality score is stored under each keyword."""
        tracker.record_keyword_quality("test-agent", ["deploy", "SDP"], 0.75)
        agent = tracker._registry.get("test-agent")
        assert "deploy" in agent.health.keyword_quality
        assert 0.75 in agent.health.keyword_quality["deploy"]

    def test_keyword_quality_rolling_window_max_20(self, tracker: AgentHealthTracker):
        """Stored scores per keyword are capped at 20."""
        for i in range(25):
            tracker.record_keyword_quality("test-agent", ["deploy"], float(i) / 25)
        agent = tracker._registry.get("test-agent")
        assert len(agent.health.keyword_quality["deploy"]) <= 20

    def test_record_keyword_quality_multiple_keywords(self, tracker: AgentHealthTracker):
        """Multiple keywords in one call are all updated."""
        tracker.record_keyword_quality("test-agent", ["alpha", "beta", "gamma"], 0.5)
        agent = tracker._registry.get("test-agent")
        for kw in ["alpha", "beta", "gamma"]:
            assert kw in agent.health.keyword_quality

    def test_keyword_quality_unknown_agent_ignored(self, tracker: AgentHealthTracker):
        """Recording keyword quality for unknown agent should not raise."""
        tracker.record_keyword_quality("ghost-agent", ["test"], 0.9)


# ---------------------------------------------------------------------------
# EA-13a — Known weak area recording
# ---------------------------------------------------------------------------


class TestWeakAreaRecording:
    def test_record_weak_query_stores_pattern(self, tracker: AgentHealthTracker):
        """Explicit weak pattern is stored in agent.health.weak_queries."""
        tracker.record_weak_query("test-agent", "family deployment")
        agent = tracker._registry.get("test-agent")
        assert "family deployment" in agent.health.weak_queries

    def test_record_weak_query_no_duplicates(self, tracker: AgentHealthTracker):
        """Adding the same pattern twice only stores it once."""
        tracker.record_weak_query("test-agent", "family deployment")
        tracker.record_weak_query("test-agent", "family deployment")
        agent = tracker._registry.get("test-agent")
        assert agent.health.weak_queries.count("family deployment") == 1

    def test_maybe_record_weak_query_auto_records_below_threshold(
        self, tracker: AgentHealthTracker
    ):
        """maybe_record_weak_query stores pattern when quality_score < 0.3."""
        tracker.maybe_record_weak_query("test-agent", "confusing query text", quality_score=0.1)
        agent = tracker._registry.get("test-agent")
        assert "confusing query text" in agent.health.weak_queries

    def test_maybe_record_weak_query_skips_above_threshold(
        self, tracker: AgentHealthTracker
    ):
        """maybe_record_weak_query does NOT store pattern when quality_score >= 0.3."""
        tracker.maybe_record_weak_query("test-agent", "good query", quality_score=0.5)
        agent = tracker._registry.get("test-agent")
        assert "good query" not in agent.health.weak_queries


# ---------------------------------------------------------------------------
# EA-14a — Per-cluster cache hit metrics
# ---------------------------------------------------------------------------


class TestCacheHitCluster:
    def test_record_cache_hit_cluster_increments(self, tracker: AgentHealthTracker):
        """Cache hit counter for a cluster keyword increments on each call."""
        tracker.record_cache_hit_cluster("test-agent", "SDP")
        tracker.record_cache_hit_cluster("test-agent", "SDP")
        agent = tracker._registry.get("test-agent")
        assert agent.health.cache_hits_by_cluster.get("SDP", 0) == 2

    def test_record_cache_hit_cluster_multiple_clusters(self, tracker: AgentHealthTracker):
        """Different cluster keywords tracked independently."""
        tracker.record_cache_hit_cluster("test-agent", "SDP")
        tracker.record_cache_hit_cluster("test-agent", "canary")
        agent = tracker._registry.get("test-agent")
        assert agent.health.cache_hits_by_cluster.get("SDP", 0) == 1
        assert agent.health.cache_hits_by_cluster.get("canary", 0) == 1
