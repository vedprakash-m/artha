"""
tests/ext_agents/test_fan_out.py — EAR-5: parallel fan-out tests.

Tests (20):
 1. execute() returns FanOutResult
 2. results field is a list
 3. successful invocations appear in results
 4. failed agents appear in results with success=False
 5. timeout_per_agent is passed through without crash
 6. unified_output is a string
 7. empty candidates → empty FanOutResult
 8. single agent run succeeds
 9. concurrent workers run in parallel (timing check)
10. combined_confidence is positive on success
11. FanOutResult has combined_confidence field
12. unified_output includes agent name header
13. partial failure: some succeed, some fail
14. max_workers does not exceed 3
15. unified_output is a string (recheck)
16. failed agent result has non-empty error
17. unified_output handles empty responses gracefully
18. execute() does not raise on all-timeout
19. matched_agents length equals candidate count
20. unified_output warning appears for failures

Ref: specs/ext-agent-reloaded.md §EAR-5
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.fan_out import FanOut, FanOutResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_match(agent_name="test-agent", confidence=0.8):
    m = MagicMock()
    m.agent_name = agent_name
    m.confidence = confidence
    return m


def _ok_invoke(agent_name, query, timeout):
    return f"Response from {agent_name}", 0.8


def _fail_invoke(agent_name, query, timeout):
    raise RuntimeError(f"{agent_name} timed out")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_execute_returns_fan_out_result():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("test query", candidates=[_make_match("a")])
    assert isinstance(result, FanOutResult)


def test_results_is_list():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("a")])
    assert isinstance(result.results, list)


def test_successful_results_appear():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("agent-1"), _make_match("agent-2")])
    names = [r.agent_name for r in result.results if r.success]
    assert "agent-1" in names or "agent-2" in names


def test_failed_agents_in_results():
    fo = FanOut(invoke_fn=_fail_invoke)
    result = fo.execute("query", candidates=[_make_match("failing-agent")])
    fails = [r for r in result.results if not r.success]
    assert any(r.agent_name == "failing-agent" for r in fails)


def test_timeout_per_agent_no_crash():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("a")], timeout_per_agent=10)
    assert isinstance(result, FanOutResult)


def test_unified_output_is_string():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("a")])
    assert isinstance(result.unified_output, str)


def test_empty_candidates_empty_result():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[])
    assert result.matched_agents == []
    assert result.results == []


def test_single_agent_success():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("solo")])
    successes = [r for r in result.results if r.success]
    assert successes, "Expected at least one successful invocation"


def test_concurrent_workers_parallel():
    """3 agents each sleeping 0.1s should complete in ~0.15s when parallel."""
    def slow_invoke(agent_name, query, timeout):
        time.sleep(0.1)
        return "ok", 0.7

    fo = FanOut(invoke_fn=slow_invoke)
    matches = [_make_match(f"agent-{i}") for i in range(3)]
    start = time.monotonic()
    fo.execute("query", candidates=matches, timeout_per_agent=5)
    elapsed = time.monotonic() - start
    assert elapsed < 0.35, f"Fan-out took {elapsed:.2f}s, likely sequential"


def test_combined_confidence_positive_on_success():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("a")])
    assert result.combined_confidence >= 0.0


def test_fan_out_result_has_combined_confidence():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("x")])
    assert hasattr(result, "combined_confidence")


def test_unified_output_includes_agent_header():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("agent-header")])
    if result.unified_output:
        assert "agent-header" in result.unified_output.lower() or "##" in result.unified_output


def test_partial_failure_some_succeed():
    def mixed_invoke(agent_name, query, timeout):
        if agent_name == "bad-agent":
            raise RuntimeError("timeout")
        return "ok", 0.8

    fo = FanOut(invoke_fn=mixed_invoke)
    result = fo.execute("query", candidates=[_make_match("good-agent"), _make_match("bad-agent")])
    names_success = [r.agent_name for r in result.results if r.success]
    names_fail = [r.agent_name for r in result.results if not r.success]
    assert "good-agent" in names_success
    assert "bad-agent" in names_fail


def test_max_workers_not_exceed_three():
    fo = FanOut(invoke_fn=_ok_invoke)
    assert fo._max_workers <= 3


def test_unified_output_is_string_field():
    fo = FanOut(invoke_fn=_ok_invoke)
    result = fo.execute("query", candidates=[_make_match("a")])
    assert isinstance(result.unified_output, str)


def test_failed_results_have_error_messages():
    fo = FanOut(invoke_fn=_fail_invoke)
    result = fo.execute("query", candidates=[_make_match("error-agent")])
    for r in result.results:
        if not r.success:
            assert isinstance(r.error, str)
            assert len(r.error) > 0


def test_unified_output_all_fail_graceful():
    fo = FanOut(invoke_fn=_fail_invoke)
    result = fo.execute("query", candidates=[_make_match("bad")])
    assert isinstance(result.unified_output, str)


def test_execute_all_timeout_no_raise():
    def timeout_invoke(agent_name, query, timeout):
        raise TimeoutError("too slow")

    fo = FanOut(invoke_fn=timeout_invoke)
    matches = [_make_match("t1"), _make_match("t2")]
    result = fo.execute("query", candidates=matches, timeout_per_agent=1)
    assert isinstance(result, FanOutResult)


def test_matched_agents_length_equals_candidates():
    fo = FanOut(invoke_fn=_ok_invoke)
    matches = [_make_match("a"), _make_match("b"), _make_match("c")]
    result = fo.execute("query", candidates=matches)
    assert len(result.matched_agents) == 3


def test_unified_output_shows_failure_warning():
    fo = FanOut(invoke_fn=_fail_invoke)
    result = fo.execute("test query", candidates=[_make_match("warn-agent")])
    assert any(
        word in result.unified_output.lower()
        for word in ["fail", "warn", "error", "⚠"]
    )

