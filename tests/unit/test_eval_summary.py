"""tests/unit/test_eval_summary.py — Unit tests for EV-0d eval dashboard summary.

Tests print_summary(as_json=True) in scripts/eval_runner.py.
All data is synthetic (DD-5).
Ref: specs/eval.md EV-0d, T-EV-0d-01 to T-EV-0d-03
"""
from __future__ import annotations

import importlib.util
import json
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
    return _load_module("eval_runner_summary", _SCRIPTS_DIR / "eval_runner.py")


# ===========================================================================
# T-EV-0d-01: print_summary returns dict with session_count and avg_quality
# ===========================================================================

def test_print_summary_returns_expected_keys(runner, tmp_path, monkeypatch):
    """T-EV-0d-01: print_summary(as_json=True) must return session_count and avg_quality."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text(
        "---\n"
        "- timestamp: '2026-02-01T09:00:00+00:00'\n"
        "  quality_score: 80.0\n"
        "- timestamp: '2026-02-02T09:00:00+00:00'\n"
        "  quality_score: 75.0\n"
    )
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text(json.dumps([
        {"quality_score": 80.0, "compliance_score": 90.0},
        {"quality_score": 75.0, "compliance_score": 85.0},
    ]))
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner.print_summary(as_json=True)
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "session_count" in result, "Missing session_count"
    assert "avg_quality" in result, "Missing avg_quality"
    assert result["session_count"] == 2
    assert result["avg_quality"] == pytest.approx(77.5, abs=0.1)


# ===========================================================================
# T-EV-0d-02: no runs → session_count = 0
# ===========================================================================

def test_print_summary_no_runs_session_count_zero(runner, tmp_path, monkeypatch):
    """T-EV-0d-02: No runs in catch_up_runs.yaml → session_count must be 0."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text("---\n[]\n")
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text("[]")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner.print_summary(as_json=True)
    assert result["session_count"] == 0


# ===========================================================================
# T-EV-0d-03: alerts list is present in result
# ===========================================================================

def test_print_summary_includes_alerts_list(runner, tmp_path, monkeypatch):
    """T-EV-0d-03: print_summary result must include an 'alerts' key with a list."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text("---\n[]\n")
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text("[]")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner.print_summary(as_json=True)
    assert "alerts" in result, "Missing 'alerts' key in summary result"
    assert isinstance(result["alerts"], list), f"alerts must be a list, got: {type(result['alerts'])}"
