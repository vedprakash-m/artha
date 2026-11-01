"""tests/unit/test_trust_evidence.py — Unit tests for EV-11b trust evidence computation.

Tests compute_trust_evidence() in scripts/eval_runner.py.
All data is synthetic (DD-5).
Ref: specs/eval.md EV-11b, T-EV-11b-01 to T-EV-11b-04
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def runner():
    return _load_module("eval_runner_trust", _SCRIPTS_DIR / "eval_runner.py")


# ===========================================================================
# T-EV-11b-01: runs with outcome_items_resolved_24h > 0 → score > 0
# ===========================================================================

def test_trust_score_positive_when_items_resolved(runner):
    """T-EV-11b-01: Runs with items_resolved_24h and items_surfaced must yield score > 0."""
    runs = [
        {"outcome_items_resolved_24h": 2, "items_surfaced": 5},
        {"outcome_items_resolved_24h": 1, "items_surfaced": 3},
    ]
    result = runner.compute_trust_evidence(runs=runs)
    assert result.get("score") is not None, "Expected a non-None score"
    assert result["score"] > 0, f"Expected score > 0, got {result['score']}"


# ===========================================================================
# T-EV-11b-02: no runs → score is None and message is "no_data"
# ===========================================================================

def test_trust_score_no_data_when_no_runs(runner):
    """T-EV-11b-02: Empty runs list must return score=None and message='no_data'."""
    result = runner.compute_trust_evidence(runs=[])
    assert result.get("score") is None
    assert result.get("message") == "no_data"


# ===========================================================================
# T-EV-11b-03: runs with no outcome fields → score is None (no items_surfaced)
# ===========================================================================

def test_trust_score_none_when_no_surfaced_items(runner):
    """T-EV-11b-03: Runs with no items_surfaced must yield score=None."""
    runs = [
        {"outcome_items_resolved_24h": 0},
        {"outcome_items_resolved_24h": 0},
    ]
    result = runner.compute_trust_evidence(runs=runs)
    # No items_surfaced means no denominator → score is None
    assert result.get("score") is None, (
        f"Expected None score when no items_surfaced, got: {result.get('score')}"
    )


# ===========================================================================
# T-EV-11b-04: sessions_with_signals count is correct
# ===========================================================================

def test_sessions_with_signals_count(runner):
    """T-EV-11b-04: sessions_with_signals must count only runs with non-None outcome field."""
    runs = [
        {"outcome_items_resolved_24h": 2, "items_surfaced": 5},
        {"quality_score": 80},                      # no outcome field → not counted
        {"outcome_items_resolved_24h": None},         # explicit None → not counted
        {"outcome_items_resolved_24h": 0, "items_surfaced": 2},  # 0 is not None → counted
    ]
    result = runner.compute_trust_evidence(runs=runs)
    assert result.get("sessions_with_signals") == 2, (
        f"Expected 2 sessions with signals, got: {result.get('sessions_with_signals')}"
    )
