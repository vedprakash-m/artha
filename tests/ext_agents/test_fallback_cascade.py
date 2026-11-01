"""tests/ext_agents/test_fallback_cascade.py — EA-5b fallback cascade tests."""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.agent_invoker import (  # type: ignore
    AgentResult,
    InvocationError,
    MockAgentProvider,
    invoke_with_fallback,
)
from .conftest import make_test_agent, SAMPLE_AGENT_ENTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_with_fallbacks(fallback_cascade):
    """Build a test agent with a custom fallback_cascade list."""
    from lib.agent_registry import _parse_agent  # type: ignore
    entry = dict(SAMPLE_AGENT_ENTRY)
    entry["fallback_cascade"] = fallback_cascade
    return _parse_agent("test-agent", entry)


def _failing_provider(reason: str = "timeout") -> MockAgentProvider:
    p = MockAgentProvider()
    p.set_failure(InvocationError(reason, f"agent failed: {reason}"))
    return p


def _good_provider(response: str = "Primary response.") -> MockAgentProvider:
    p = MockAgentProvider()
    p.add_response("test-agent", response)
    return p


# ---------------------------------------------------------------------------
# Primary-path tests (no fallback triggered)
# ---------------------------------------------------------------------------

class TestPrimaryPath:
    def test_returns_primary_result_on_success(self):
        agent = make_test_agent()
        provider = _good_provider("All good.")
        result = invoke_with_fallback(agent, "any prompt", provider)
        assert result.response == "All good."
        assert result.fallback_used is False
        assert result.fallback_type is None

    def test_primary_result_agent_name_populated(self):
        agent = make_test_agent()
        result = invoke_with_fallback(agent, "prompt", _good_provider())
        assert result.agent_name == "test-agent"


# ---------------------------------------------------------------------------
# KB fallback
# ---------------------------------------------------------------------------

class TestKbFallback:
    def test_kb_fallback_used_when_cache_exists(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "test-agent.md").write_text("KB summary content.", encoding="utf-8")

        agent = _make_agent_with_fallbacks([{"type": "kb"}])
        provider = _failing_provider()
        result = invoke_with_fallback(agent, "q", provider, cache_dir=str(cache_dir))
        assert result.fallback_used is True
        assert result.fallback_type == "kb"
        assert "KB summary content." in result.response

    def test_kb_fallback_skipped_when_cache_missing(self, tmp_path):
        empty_dir = tmp_path / "cache"
        empty_dir.mkdir()
        # No cache file — fallback should be exhausted → raise
        agent = _make_agent_with_fallbacks([{"type": "kb"}])
        provider = _failing_provider()
        with pytest.raises(InvocationError):
            invoke_with_fallback(agent, "q", provider, cache_dir=str(empty_dir))

    def test_kb_fallback_skipped_when_cache_dir_none(self):
        agent = _make_agent_with_fallbacks([{"type": "kb"}])
        provider = _failing_provider()
        with pytest.raises(InvocationError):
            invoke_with_fallback(agent, "q", provider, cache_dir=None)

    def test_kb_fallback_skipped_when_cache_empty_file(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "test-agent.md").write_text("", encoding="utf-8")  # empty
        agent = _make_agent_with_fallbacks([{"type": "kb"}])
        provider = _failing_provider()
        with pytest.raises(InvocationError):
            invoke_with_fallback(agent, "q", provider, cache_dir=str(cache_dir))


# ---------------------------------------------------------------------------
# Cowork fallback
# ---------------------------------------------------------------------------

class TestCoworkFallback:
    def test_cowork_fallback_returns_result(self):
        agent = _make_agent_with_fallbacks([{"type": "cowork"}])
        provider = _failing_provider()
        result = invoke_with_fallback(agent, "q", provider)
        assert result.fallback_used is True
        assert result.fallback_type == "cowork"
        assert "Copilot Cowork" in result.response or "m365" in result.response.lower()

    def test_cowork_response_contains_agent_name(self):
        agent = _make_agent_with_fallbacks([{"type": "cowork"}])
        provider = _failing_provider()
        result = invoke_with_fallback(agent, "q", provider)
        assert "test-agent" in result.response


# ---------------------------------------------------------------------------
# Investigation fallback
# ---------------------------------------------------------------------------

class TestInvestigationFallback:
    def test_investigation_fallback_returns_result(self):
        agent = _make_agent_with_fallbacks([{"type": "investigation"}])
        provider = _failing_provider()
        result = invoke_with_fallback(agent, "q", provider)
        assert result.fallback_used is True
        assert result.fallback_type == "investigation"
        assert "unavailable" in result.response.lower() or "investigate" in result.response.lower()


# ---------------------------------------------------------------------------
# Unknown fallback type — should be skipped, not crash
# ---------------------------------------------------------------------------

class TestUnknownFallbackType:
    def test_unknown_type_skipped_falls_through_to_raise(self):
        agent = _make_agent_with_fallbacks([{"type": "kusto"}])
        provider = _failing_provider()
        with pytest.raises(InvocationError):
            invoke_with_fallback(agent, "q", provider)

    def test_unknown_then_cowork_proceeds_to_cowork(self):
        agent = _make_agent_with_fallbacks([{"type": "kusto"}, {"type": "cowork"}])
        provider = _failing_provider()
        result = invoke_with_fallback(agent, "q", provider)
        assert result.fallback_type == "cowork"


# ---------------------------------------------------------------------------
# Cascade ordering — first successful fallback wins
# ---------------------------------------------------------------------------

class TestCascadeOrdering:
    def test_kb_first_then_cowork_uses_kb(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "test-agent.md").write_text("From KB.", encoding="utf-8")

        agent = _make_agent_with_fallbacks([{"type": "kb"}, {"type": "cowork"}])
        provider = _failing_provider()
        result = invoke_with_fallback(agent, "q", provider, cache_dir=str(cache_dir))
        assert result.fallback_type == "kb"
        assert "From KB." in result.response

    def test_empty_cascade_raises_primary_error(self):
        agent = _make_agent_with_fallbacks([])
        provider = _failing_provider()
        with pytest.raises(InvocationError) as exc_info:
            invoke_with_fallback(agent, "q", provider)
        assert exc_info.value.reason == "timeout"

    def test_no_fallback_cascade_attribute_raises(self):
        agent = make_test_agent()
        agent.fallback_cascade = []
        provider = _failing_provider("unavailable")
        with pytest.raises(InvocationError):
            invoke_with_fallback(agent, "q", provider)


# ---------------------------------------------------------------------------
# AgentResult fallback fields in primary success case
# ---------------------------------------------------------------------------

class TestAgentResultFallbackFields:
    def test_fallback_used_defaults_false(self):
        r = AgentResult(
            agent_name="x",
            response="resp",
            invoked_at=datetime.now(timezone.utc),
        )
        assert r.fallback_used is False
        assert r.fallback_type is None
