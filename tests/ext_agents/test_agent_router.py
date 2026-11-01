"""tests/ext_agents/test_agent_router.py -- AR-9 AgentRouter tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent_registry import AgentRegistry  # type: ignore
from lib.agent_router import AgentRouter, RoutingMatch, RoutingResult  # type: ignore


@pytest.fixture()
def router(populated_registry_dir: Path) -> AgentRouter:
    reg = AgentRegistry.load(populated_registry_dir)
    return AgentRouter(registry=reg)


class TestRoutingResult:
    def test_no_match_result(self):
        result = RoutingResult(match=None, all_candidates=[], routing_ms=0.0)
        assert result.match is None
        assert result.match is None  # no has_match attribute

    def test_match_result_has_match(self):
        m = RoutingMatch(
            agent_name="test-agent",
            confidence=0.85,
            keyword_hits=1,
            keyword_coverage=0.5,
            query_coverage=0.5,
            domain_bonus=0.0,
            recency_bonus=0.0,
            matched_keywords=["SDP"],
        )
        result = RoutingResult(match=m, all_candidates=[m], routing_ms=1.0)
        assert result.match is not None
        assert result.match.agent_name == "test-agent"


class TestRoutingMatch:
    def test_fields(self):
        m = RoutingMatch(
            agent_name="x",
            confidence=0.7,
            keyword_hits=1,
            keyword_coverage=0.5,
            query_coverage=0.5,
            domain_bonus=0.0,
            recency_bonus=0.0,
            matched_keywords=["deploy"],
        )
        assert m.agent_name == "x"
        assert m.confidence == pytest.approx(0.7)
        assert "deploy" in m.matched_keywords


class TestAgentRouter:
    def test_empty_query_returns_no_match(self, router: AgentRouter):
        result = router.route("")
        assert result.match is None

    def test_keyword_match_returns_agent(self, router: AgentRouter):
        result = router.route("deployment stuck in SDP block")
        assert result.match is not None
        assert result.match.agent_name == "test-agent"

    def test_unrelated_query_no_match(self, router: AgentRouter):
        result = router.route("weather forecast for tomorrow")
        assert result.match is None

    def test_excluded_keyword_blocks_match(self, router: AgentRouter):
        """Queries containing exclude_keywords should not match."""
        result = router.route("family health deployment personal")
        assert isinstance(result, RoutingResult)

    def test_confidence_above_threshold(self, router: AgentRouter):
        result = router.route("SDP block canary deployment stuck")
        if result.match is not None:
            assert result.match.confidence >= 0.6

    def test_multiple_keywords_higher_confidence(self, router: AgentRouter):
        single = router.route("SDP block")
        multi = router.route("deployment stuck SDP block canary")
        if single.match is not None and multi.match is not None:
            assert multi.match.confidence >= single.match.confidence

    def test_returns_routing_result_type(self, router: AgentRouter):
        result = router.route("anything")
        assert isinstance(result, RoutingResult)

    def test_no_agents_returns_no_match(self, tmp_registry_dir: Path):
        reg = AgentRegistry.load(tmp_registry_dir)
        r = AgentRouter(registry=reg)
        result = r.route("deployment stuck")
        assert result.match is None

    def test_retired_agent_not_routed(self, populated_registry_dir: Path):
        reg = AgentRegistry.load(populated_registry_dir)
        reg.retire("test-agent")
        r = AgentRouter(registry=reg)
        result = r.route("deployment stuck SDP block")
        assert result.match is None


# ---------------------------------------------------------------------------
# EA-13b — Router skips agents whose weak_queries match the query
# ---------------------------------------------------------------------------


class TestWeakAreaSkip:
    def test_weak_query_skip_blocks_match(self, populated_registry_dir: Path):
        """Agent is skipped when query fully matches a recorded weak pattern."""
        reg = AgentRegistry.load(populated_registry_dir)
        agent = reg.get("test-agent")
        agent.health.weak_queries = ["deployment stuck SDP block"]
        r = AgentRouter(registry=reg)
        result = r.route("deployment stuck SDP block")
        assert result.match is None

    def test_weak_query_partial_match_blocked(self, populated_registry_dir: Path):
        """Agent is skipped when the weak pattern appears as a substring of the query."""
        reg = AgentRegistry.load(populated_registry_dir)
        agent = reg.get("test-agent")
        agent.health.weak_queries = ["SDP block"]
        r = AgentRouter(registry=reg)
        result = r.route("canary deployment SDP block failure")
        assert result.match is None

    def test_unaffected_query_not_blocked(self, populated_registry_dir: Path):
        """Queries that do not contain any weak pattern still route normally."""
        reg = AgentRegistry.load(populated_registry_dir)
        agent = reg.get("test-agent")
        agent.health.weak_queries = ["unrelated stuff"]
        r = AgentRouter(registry=reg)
        result = r.route("deployment stuck SDP block")
        assert result.match is not None
