"""
tests/ext_agents/test_evaluator_optimizer.py — EAR-6: evaluator-optimizer loop tests.

Tests (15):
 1. maybe_retry returns OptimizeResult
 2. does not retry when quality above threshold
 3. retries when quality below threshold
 4. never downgrades quality (final = max)
 5. budget is consumed when retry occurs
 6. budget exhausted → skip retry
 7. invoke_fn=None → skip retry
 8. retry reason is populated
 9. budget_remaining decrements correctly
10. dim score below dim_threshold triggers retry
11. feedback preamble contains weak dimension
12. build_feedback_preamble empty for passing dims
13. invoke error → return initial result
14. retried=True on successful retry
15. weekly_cap is respected across calls

Ref: specs/ext-agent-reloaded.md §EAR-6
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.evaluator_optimizer import (
    EvaluatorOptimizer,
    OptimizeResult,
    _build_feedback_preamble,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def opt(tmp_path, monkeypatch):
    """EvaluatorOptimizer with isolated in-memory budget state (no file I/O)."""
    instance_state: dict = {}

    def mock_load() -> dict:
        return dict(instance_state)

    def mock_save(state: dict) -> None:
        instance_state.clear()
        instance_state.update(state)

    monkeypatch.setattr("lib.evaluator_optimizer._load_budget_state", mock_load)
    monkeypatch.setattr("lib.evaluator_optimizer._save_budget_state", mock_save)

    eo = EvaluatorOptimizer(
        quality_threshold=0.6,
        dim_threshold=0.45,
        weekly_cap=10,
    )
    eo._state = {}
    return eo


def _invoke_good(query, feedback):
    return "Better response.", 0.85


def _invoke_bad(query, feedback):
    raise RuntimeError("Invoke failed")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_maybe_retry_returns_result(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="test",
        initial_response="ok",
        initial_quality=0.9,
        invoke_fn=None,
    )
    assert isinstance(result, OptimizeResult)


def test_no_retry_above_threshold(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="ok",
        initial_quality=0.8,
        dim_scores={"factual": 0.9, "clarity": 0.9},
        invoke_fn=_invoke_good,
    )
    assert not result.retried
    assert result.final_quality == 0.8


def test_retry_below_threshold(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="poor",
        initial_quality=0.4,
        invoke_fn=_invoke_good,
    )
    assert result.retried
    assert result.final_quality >= 0.4


def test_never_downgrades_quality(opt):
    def bad_retry(query, feedback):
        return "Worse response.", 0.1

    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="decent",
        initial_quality=0.55,
        invoke_fn=bad_retry,
    )
    assert result.final_quality >= 0.55


def test_budget_consumed_on_retry(opt):
    initial_budget = opt.weekly_cap
    opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="poor",
        initial_quality=0.4,
        invoke_fn=_invoke_good,
    )
    remaining = opt._budget_remaining_internal()
    # Budget should have decreased
    assert remaining < initial_budget


def test_budget_exhausted_skips_retry(monkeypatch):
    state: dict = {}
    monkeypatch.setattr("lib.evaluator_optimizer._load_budget_state", lambda: dict(state))
    monkeypatch.setattr("lib.evaluator_optimizer._save_budget_state", lambda s: state.update(s))
    eo = EvaluatorOptimizer(weekly_cap=0)
    eo._state = {}
    result = eo.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="poor",
        initial_quality=0.3,
        invoke_fn=_invoke_good,
    )
    assert not result.retried
    assert result.budget_remaining == 0


def test_invoke_fn_none_skips_retry(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="poor",
        initial_quality=0.3,
        invoke_fn=None,
    )
    assert not result.retried


def test_retry_reason_populated(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="poor",
        initial_quality=0.4,
        invoke_fn=_invoke_good,
    )
    assert result.retried
    assert result.retry_reason != ""


def test_budget_remaining_decrements(opt):
    for _ in range(3):
        opt.maybe_retry(
            agent_name="a",
            query="q",
            initial_response="poor",
            initial_quality=0.4,
            invoke_fn=_invoke_good,
        )
    assert opt._budget_remaining_internal() <= opt.weekly_cap - 3


def test_dim_score_below_threshold_triggers_retry(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="mediocre",
        initial_quality=0.65,  # above overall threshold
        dim_scores={"factual": 0.3, "clarity": 0.9},  # factual below 0.45
        invoke_fn=_invoke_good,
    )
    assert result.retried


def test_feedback_preamble_contains_weak_dim():
    preamble = _build_feedback_preamble({"factual": 0.3, "clarity": 0.9}, threshold=0.45)
    assert "FACTUAL" in preamble.upper()
    assert "clarity" not in preamble.upper().split("FACTUAL")[0]  # clarity not mentioned first


def test_build_feedback_preamble_empty_for_passing():
    preamble = _build_feedback_preamble({"factual": 0.9, "clarity": 0.95}, threshold=0.45)
    assert preamble == ""


def test_invoke_error_returns_initial(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="initial",
        initial_quality=0.4,
        invoke_fn=_invoke_bad,
    )
    assert result.final_response == "initial"
    assert result.final_quality == 0.4
    assert not result.retried


def test_retried_true_on_success(opt):
    result = opt.maybe_retry(
        agent_name="a",
        query="q",
        initial_response="poor",
        initial_quality=0.4,
        invoke_fn=_invoke_good,
    )
    assert result.retried


def test_weekly_cap_respected(monkeypatch):
    state: dict = {}
    monkeypatch.setattr("lib.evaluator_optimizer._load_budget_state", lambda: dict(state))
    monkeypatch.setattr("lib.evaluator_optimizer._save_budget_state", lambda s: state.update(s))
    eo = EvaluatorOptimizer(weekly_cap=2)
    eo._state = {}

    for i in range(5):
        eo.maybe_retry(
            agent_name="a",
            query="q",
            initial_response="poor",
            initial_quality=0.3,
            invoke_fn=_invoke_good,
        )

    # After 2 retries, budget should be 0
    remaining = eo._budget_remaining_internal()
    assert remaining == 0


# ---------------------------------------------------------------------------
# Helper method needed on EvaluatorOptimizer for tests
# ---------------------------------------------------------------------------

def _budget_remaining_internal(self):
    from lib.evaluator_optimizer import _budget_remaining, _current_week
    return _budget_remaining(self._state, self.weekly_cap)

EvaluatorOptimizer._budget_remaining_internal = _budget_remaining_internal
