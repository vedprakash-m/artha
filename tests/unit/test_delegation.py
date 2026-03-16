"""tests/unit/test_delegation.py — Unit tests for scripts/delegation.py

AR-7 verification suite (specs/agentic-reloaded.md).

Coverage:
  - should_delegate() returns True at step threshold (≥5)
  - should_delegate() returns True for parallel tasks
  - should_delegate() returns True for isolated tasks
  - should_delegate() returns False for small, serial, shared tasks
  - compose_handoff() builds DelegationRequest with expected fields
  - compose_handoff() compresses long context to ≤500 chars
  - compose_handoff() includes relevant_state paths
  - compose_handoff() respects max_budget cap
  - DelegationRequest.to_prompt() renders all sections
  - evaluate_for_procedure() marks non-trivial tasks as candidates
  - evaluate_for_procedure() rejects trivial tasks
  - _compress_context() cuts at sentence boundary
  - is_delegation_enabled() respects config flag
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# conftest adds scripts/ to sys.path
from delegation import (
    DelegationRequest,
    DelegationResult,
    _compress_context,
    compose_handoff,
    evaluate_for_procedure,
    is_delegation_enabled,
    should_delegate,
)


# ---------------------------------------------------------------------------
# should_delegate()
# ---------------------------------------------------------------------------


class TestShouldDelegate:
    def test_at_threshold_is_true(self):
        assert should_delegate(5) is True

    def test_above_threshold_is_true(self):
        assert should_delegate(10) is True

    def test_below_threshold_is_false(self):
        assert should_delegate(4) is False

    def test_zero_steps_is_false(self):
        assert should_delegate(0) is False

    def test_parallel_overrides_step_count(self):
        assert should_delegate(2, is_parallel=True) is True

    def test_isolated_overrides_step_count(self):
        assert should_delegate(2, is_isolated=True) is True

    def test_serial_shared_below_threshold_is_false(self):
        assert should_delegate(3, is_parallel=False, is_isolated=False) is False


# ---------------------------------------------------------------------------
# compose_handoff()
# ---------------------------------------------------------------------------


class TestComposeHandoff:
    def test_returns_delegation_request(self):
        req = compose_handoff("Summarise immigration briefings", "User on H-1B.")
        assert isinstance(req, DelegationRequest)

    def test_task_preserved(self):
        task = "Summarise all immigration briefings from last month"
        req = compose_handoff(task, "Some context.")
        assert req.task == task

    def test_context_compressed_to_max(self):
        long_ctx = "This is a very long context sentence that goes on. " * 30
        req = compose_handoff("My task", long_ctx)
        assert len(req.context_excerpt) <= 500

    def test_short_context_not_truncated(self):
        ctx = "Short context."
        req = compose_handoff("My task", ctx)
        assert req.context_excerpt == ctx

    def test_relevant_state_included(self):
        req = compose_handoff("Task", "ctx", relevant_state=["state/immigration.yaml"])
        assert "state/immigration.yaml" in req.relevant_state

    def test_budget_capped_at_max(self):
        with patch("delegation._get_max_budget", return_value=10):
            req = compose_handoff("Task", "ctx", budget=100)
        assert req.budget <= 10

    def test_default_agent_is_explore(self):
        req = compose_handoff("Research task", "context")
        assert req.agent == "Explore"

    def test_custom_agent_accepted(self):
        req = compose_handoff("Task", "ctx", agent="DataAnalysisExpert")
        assert req.agent == "DataAnalysisExpert"


# ---------------------------------------------------------------------------
# DelegationRequest.to_prompt()
# ---------------------------------------------------------------------------


class TestToPrompt:
    def test_contains_task(self):
        req = DelegationRequest(task="Research immigration timelines")
        prompt = req.to_prompt()
        assert "Research immigration timelines" in prompt

    def test_contains_budget(self):
        req = DelegationRequest(task="Task", budget=8)
        prompt = req.to_prompt()
        assert "8" in prompt

    def test_contains_state_files(self):
        req = DelegationRequest(task="Task", relevant_state=["state/goals.yaml"])
        prompt = req.to_prompt()
        assert "state/goals.yaml" in prompt

    def test_contains_no_write_constraint(self):
        req = DelegationRequest(task="Task")
        prompt = req.to_prompt()
        assert "write" in prompt.lower() or "write" in prompt

    def test_context_section_only_when_non_empty(self):
        req_no_ctx = DelegationRequest(task="Task", context_excerpt="")
        prompt = req_no_ctx.to_prompt()
        assert "## Context" not in prompt

    def test_state_section_only_when_non_empty(self):
        req_no_state = DelegationRequest(task="Task", relevant_state=[])
        prompt = req_no_state.to_prompt()
        assert "State Files" not in prompt


# ---------------------------------------------------------------------------
# evaluate_for_procedure()
# ---------------------------------------------------------------------------


class TestEvaluateForProcedure:
    def test_rich_task_with_result_is_candidate(self):
        result = DelegationResult(
            summary="Found 3 briefings with upcoming form deadlines. I-131 renewal due 2026-04-01, I-485 interview scheduled 2026-05-15.",
            tool_calls_used=5,
        )
        task = "Summarise all immigration briefings from the last 90 days and flag form deadlines"
        assert evaluate_for_procedure(result, task) is True

    def test_short_task_not_candidate(self):
        result = DelegationResult(summary="Done")
        task = "List files"
        assert evaluate_for_procedure(result, task) is False

    def test_trivial_summary_not_candidate(self):
        result = DelegationResult(summary="ok")
        task = "Summarise all briefings from last quarter and identify recurring patterns in finance"
        assert evaluate_for_procedure(result, task) is False


# ---------------------------------------------------------------------------
# _compress_context()
# ---------------------------------------------------------------------------


class TestCompressContext:
    def test_short_text_unchanged(self):
        text = "Short text."
        assert _compress_context(text, max_chars=500) == text

    def test_long_text_truncated(self):
        text = "First sentence here. " * 30
        result = _compress_context(text, max_chars=100)
        assert len(result) <= 105  # small buffer for boundary detection

    def test_cuts_at_sentence_boundary(self):
        text = "Sentence one ends here. Sentence two starts here. Sentence three follows."
        result = _compress_context(text, max_chars=40)
        # Should end with a period, not mid-word
        assert result.endswith(".") or result.endswith("…")

    def test_empty_string_returned_unchanged(self):
        assert _compress_context("", max_chars=100) == ""


# ---------------------------------------------------------------------------
# is_delegation_enabled()
# ---------------------------------------------------------------------------


class TestIsDelegationEnabled:
    def test_returns_bool(self):
        result = is_delegation_enabled()
        assert isinstance(result, bool)

    def test_disabled_when_flag_false(self):
        with patch("delegation._load_harness_flag", return_value=False):
            assert is_delegation_enabled() is False

    def test_enabled_when_flag_true(self):
        with patch("delegation._load_harness_flag", return_value=True):
            assert is_delegation_enabled() is True
