"""tests/ext_agents/test_agent_invoker.py — AR-9 AgentInvoker tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lib.agent_invoker import MockAgentProvider, AgentResult, InvocationError  # type: ignore
from .conftest import make_test_agent


@pytest.fixture()
def agent():
    return make_test_agent()


@pytest.fixture()
def good_provider() -> MockAgentProvider:
    provider = MockAgentProvider(_latency_ms=50)
    provider.add_response(
        "test-agent",
        "Deployment stuck: SDP stage 3 requires capacity approval.",
    )
    return provider


@pytest.fixture()
def failing_provider() -> MockAgentProvider:
    provider = MockAgentProvider()
    provider.set_failure(InvocationError("timeout", "timed out after 60s"))
    return provider


class TestAgentResult:
    def test_success_result(self):
        r = AgentResult(
            agent_name="test-agent",
            response="deployment response",
            invoked_at=datetime.now(timezone.utc),
            latency_ms=42,
        )
        assert r.response == "deployment response"
        assert r.latency_ms == 42

    def test_invocation_error_has_reason(self):
        err = InvocationError("timeout", "timed out")
        assert err.reason == "timeout"
        assert "timed out" in str(err)

    def test_invocation_error_retried_flag(self):
        err = InvocationError("timeout", "timed out", retried=True)
        assert err.retried is True


class TestMockAgentProvider:
    def test_invoke_returns_agent_result(self, good_provider: MockAgentProvider, agent):
        result = good_provider.invoke(prompt="test prompt", agent=agent)
        assert isinstance(result, AgentResult)

    def test_invoke_success_response_non_empty(self, good_provider: MockAgentProvider, agent):
        result = good_provider.invoke(prompt="test", agent=agent)
        assert len(result.response) > 0

    def test_invoke_fail_raises_invocation_error(self, failing_provider: MockAgentProvider, agent):
        with pytest.raises(InvocationError):
            failing_provider.invoke(prompt="test", agent=agent)

    def test_latency_populated(self, good_provider: MockAgentProvider, agent):
        result = good_provider.invoke(prompt="test", agent=agent)
        assert result.latency_ms >= 0

    def test_response_text_matches(self, good_provider: MockAgentProvider, agent):
        result = good_provider.invoke(prompt="test", agent=agent)
        assert "SDP" in result.response or "deployment" in result.response.lower()

    def test_agent_name_populated(self, good_provider: MockAgentProvider, agent):
        result = good_provider.invoke(prompt="test", agent=agent)
        assert result.agent_name == agent.name

    def test_custom_response(self, agent):
        custom = MockAgentProvider(_latency_ms=5)
        custom.add_response("test-agent", "Custom answer here.")
        result = custom.invoke(prompt="q", agent=agent)
        assert "Custom" in result.response

    def test_fallback_response_when_no_entry(self, agent):
        """Provider returns a default response when agent name has no specific entry."""
        provider = MockAgentProvider()
        result = provider.invoke(prompt="q", agent=agent)
        assert isinstance(result, AgentResult)

    def test_call_log_records_invocations(self, good_provider: MockAgentProvider, agent):
        good_provider.invoke(prompt="first", agent=agent)
        good_provider.invoke(prompt="second", agent=agent)
        assert len(good_provider.call_log) == 2
        assert good_provider.call_log[0][0] == "test-agent"

    def test_clear_failure_restores_success(self, failing_provider: MockAgentProvider, agent):
        failing_provider.add_response("test-agent", "back online")
        failing_provider.clear_failure()
        result = failing_provider.invoke(prompt="q", agent=agent)
        assert result.response == "back online"
