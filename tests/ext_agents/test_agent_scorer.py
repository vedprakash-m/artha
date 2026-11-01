"""tests/ext_agents/test_agent_scorer.py — AR-9 AgentScorer tests."""
from __future__ import annotations

import pytest

from lib.agent_scorer import score_agent_response  # type: ignore


GOOD_RESPONSE = (
    "The SDP pipeline is blocked at stage 3 due to capacity constraints in eastus. "
    "To resolve: (1) check quota in the Azure portal, (2) request capacity increase "
    "via SNOW ticket, (3) re-trigger deployment after approval. "
    "ETA for capacity release: 2-4 hours."
)

VAGUE_RESPONSE = "I'm not sure. It might be a deployment issue."

EMPTY_RESPONSE = ""

REFUSAL_RESPONSE = "I cannot help with that. Please consult documentation."

ACTIONABLE_RESPONSE = (
    "Step 1: Run `az deployment show --name foo`. "
    "Step 2: Check capacity: `az vm list-usage`. "
    "Step 3: Open SNOW ticket INC-12345."
)


class TestScoreAgentResponse:
    def test_returns_float(self):
        score = score_agent_response(GOOD_RESPONSE, "SDP block deployment stuck")
        assert isinstance(score, float)

    def test_score_in_range(self):
        score = score_agent_response(GOOD_RESPONSE, "SDP block deployment stuck")
        assert 0.0 <= score <= 1.0

    def test_good_response_higher_than_vague(self):
        good = score_agent_response(GOOD_RESPONSE, "SDP block deployment stuck")
        vague = score_agent_response(VAGUE_RESPONSE, "SDP block deployment stuck")
        assert good > vague

    def test_empty_response_low_score(self):
        score = score_agent_response(EMPTY_RESPONSE, "SDP block")
        assert score < 0.3

    def test_refusal_not_high_score(self):
        score = score_agent_response(REFUSAL_RESPONSE, "SDP block deployment stuck")
        assert score < 0.7

    def test_actionable_response_scores_well(self):
        score = score_agent_response(ACTIONABLE_RESPONSE, "what steps to fix deployment")
        assert score >= 0.3

    def test_on_topic_boosts_score(self):
        on = score_agent_response(
            "The SDP block requires capacity approval in eastus.",
            "SDP block deployment"
        )
        off = score_agent_response(
            "The weather in Seattle is rainy today.",
            "SDP block deployment"
        )
        assert on >= off

    def test_honesty_signal_not_penalized_too_harshly(self):
        honest = "I don't have specific information about this deployment, but here's what to check: az deployment show."
        score = score_agent_response(honest, "SDP block")
        assert score > 0.0

    def test_long_garbage_not_high_score(self):
        garbage = "Lorem ipsum " * 100
        score = score_agent_response(garbage, "SDP block")
        assert score < 0.8

    def test_query_keyword_match_affects_score(self):
        matching = "SDP block in canary stage is causing deployment stuck issue."
        nonmatching = "The cat sat on the mat."
        s1 = score_agent_response(matching, "SDP block deployment stuck canary")
        s2 = score_agent_response(nonmatching, "SDP block deployment stuck canary")
        assert s1 > s2

    def test_numeric_specificity_helps(self):
        specific = "Deployment failure rate: 12.3%. Affected regions: 3. MTTR: 45 minutes."
        vague = "There are some failures in some regions."
        s_specific = score_agent_response(specific, "deployment failure rate")
        s_vague = score_agent_response(vague, "deployment failure rate")
        assert s_specific >= s_vague

    def test_score_with_empty_query(self):
        score = score_agent_response(GOOD_RESPONSE, "")
        assert 0.0 <= score <= 1.0
