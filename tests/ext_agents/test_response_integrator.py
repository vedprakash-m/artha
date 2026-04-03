"""tests/ext_agents/test_response_integrator.py -- AR-9 ResponseIntegrator tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lib.agent_invoker import AgentResult  # type: ignore
from lib.response_integrator import ResponseIntegrator, IntegrationResult  # type: ignore
from lib.response_verifier import KBCheckResult  # type: ignore
from .conftest import make_test_agent


@pytest.fixture()
def integrator() -> ResponseIntegrator:
    return ResponseIntegrator()


@pytest.fixture()
def agent():
    return make_test_agent()


_KB_NONE = KBCheckResult(
    agreement_ratio=0.5,
    contradictions=[],
    corroborations=[],
    confidence_label="NONE",
)

_KB_HIGH = KBCheckResult(
    agreement_ratio=0.9,
    contradictions=[],
    corroborations=["SDP", "deployment"],
    confidence_label="HIGH",
)


def _make_result(response: str, latency_ms: float = 50) -> AgentResult:
    return AgentResult(
        agent_name="test-agent",
        response=response,
        invoked_at=datetime.now(timezone.utc),
        latency_ms=latency_ms,
    )


GOOD_RESULT = _make_result(
    "SDP block at stage 3 due to capacity. Open SNOW ticket for quota increase."
)
VAGUE_RESULT = _make_result("Not sure, might be capacity.")


class TestIntegrationResult:
    def test_has_required_fields(self):
        r = IntegrationResult(
            unified_prose="combined response",
            quality_score=0.8,
            confidence_label="HIGH",
            attribution="Based on deployment guidance",
            expert_consensus_block="",
        )
        assert r.unified_prose == "combined response"
        assert r.quality_score == pytest.approx(0.8)

    def test_kb_list_fields_default_empty(self):
        r = IntegrationResult(
            unified_prose="x",
            quality_score=0.5,
            confidence_label="NONE",
            attribution="Based on analysis",
            expert_consensus_block="",
        )
        assert isinstance(r.kb_corroborations, list)
        assert isinstance(r.kb_contradictions, list)


class TestResponseIntegrator:
    def test_integrate_returns_integration_result(self, integrator, agent):
        result = integrator.integrate(
            agent=agent,
            agent_result=GOOD_RESULT,
            kb_check=_KB_NONE,
        )
        assert isinstance(result, IntegrationResult)

    def test_integrate_prose_non_empty(self, integrator, agent):
        result = integrator.integrate(
            agent=agent,
            agent_result=GOOD_RESULT,
            kb_check=_KB_NONE,
        )
        assert len(result.unified_prose) > 0

    def test_integrate_with_kb_corroborations(self, integrator, agent):
        result = integrator.integrate(
            agent=agent,
            agent_result=GOOD_RESULT,
            kb_check=_KB_HIGH,
        )
        assert result.confidence_label == "HIGH"

    def test_quality_score_in_range(self, integrator, agent):
        result = integrator.integrate(
            agent=agent,
            agent_result=GOOD_RESULT,
            kb_check=_KB_NONE,
        )
        assert 0.0 <= result.quality_score <= 1.0

    def test_private_enrichment_appears_in_prose(self, integrator, agent):
        result = integrator.integrate(
            agent=agent,
            agent_result=GOOD_RESULT,
            kb_check=_KB_NONE,
            private_enrichment=["Blocks PBI-99999, due Friday"],
        )
        assert "PBI-99999" in result.unified_prose

    def test_attribution_field_populated(self, integrator, agent):
        result = integrator.integrate(
            agent=agent,
            agent_result=GOOD_RESULT,
            kb_check=_KB_NONE,
        )
        assert isinstance(result.attribution, str) and len(result.attribution) > 0

    def test_good_response_scores_higher_than_vague(self, integrator, agent):
        good = integrator.integrate(agent=agent, agent_result=GOOD_RESULT, kb_check=_KB_NONE)
        vague = integrator.integrate(agent=agent, agent_result=VAGUE_RESULT, kb_check=_KB_NONE)
        assert good.quality_score >= vague.quality_score
