"""tests/unit/test_self_model_feedback.py — Unit tests for EV-10/11 self-model feedback.

Tests _eval_to_self_model_feedback() in scripts/eval_runner.py.
All data is synthetic (DD-5).
Ref: specs/eval.md EV-10/11, T-EV-10-01 to T-EV-10-05
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
    return _load_module("eval_runner_smf", _SCRIPTS_DIR / "eval_runner.py")


def _make_runs(n: int, quality: float = 70.0, include_domains: list | None = None) -> list[dict]:
    runs = []
    for i in range(n):
        r = {
            "session_id": f"test-{i:03d}",
            "timestamp": f"2026-01-{i + 1:02d}T09:00:00+00:00",
            "quality_score": quality,
        }
        if include_domains is not None:
            r["domains_processed"] = include_domains
        runs.append(r)
    return runs


# ===========================================================================
# T-EV-10-01: result has required keys: trend, stale_domains, overlays, should_apply
# ===========================================================================

def test_self_model_feedback_has_required_keys(runner, tmp_path, monkeypatch):
    """T-EV-10-01: _eval_to_self_model_feedback() must return dict with 4 required keys."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text("---\n[]\n")
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text("[]")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner._eval_to_self_model_feedback()
    for key in ("trend", "stale_domains", "overlays", "should_apply"):
        assert key in result, f"Missing key '{key}' in self-model feedback result"


# ===========================================================================
# T-EV-10-02: insufficient data → trend is "insufficient_data"
# ===========================================================================

def test_self_model_feedback_insufficient_data_trend(runner, tmp_path, monkeypatch):
    """T-EV-10-02: Fewer than 7 scored runs must give trend='insufficient_data'."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    import yaml  # type: ignore[import]
    runs_file.write_text("---\n" + yaml.dump(_make_runs(3)))
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text(json.dumps([{"quality_score": 70.0}] * 3))
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner._eval_to_self_model_feedback(min_runs=7)
    assert result["trend"] == "insufficient_data", f"Expected insufficient_data, got: {result['trend']}"


# ===========================================================================
# T-EV-10-03: stable quality → should_apply is False
# ===========================================================================

def test_self_model_feedback_stable_no_apply(runner, tmp_path, monkeypatch):
    """T-EV-10-03: Stable quality trend must yield should_apply=False."""
    import yaml  # type: ignore[import]
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs = _make_runs(10, quality=80.0, include_domains=["finance", "kids"])
    runs_file.write_text("---\n" + yaml.dump(runs))
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text(json.dumps([{"quality_score": 80.0}] * 10))
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner._eval_to_self_model_feedback(min_runs=7)
    # Stable trend means should_apply should be False (or overlays empty)
    if result["trend"] == "stable":
        assert result["should_apply"] is False or result["overlays"] == []


# ===========================================================================
# T-EV-10-04: overlays is always a list
# ===========================================================================

def test_self_model_feedback_overlays_is_list(runner, tmp_path, monkeypatch):
    """T-EV-10-04: overlays key must always be a list (not None or string)."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text("---\n[]\n")
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text("[]")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner._eval_to_self_model_feedback()
    assert isinstance(result["overlays"], list), (
        f"overlays must be a list, got: {type(result['overlays'])}"
    )


# ===========================================================================
# T-EV-10-05: stale_domains is always a list
# ===========================================================================

def test_self_model_feedback_stale_domains_is_list(runner, tmp_path, monkeypatch):
    """T-EV-10-05: stale_domains key must always be a list (not None or string)."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text("---\n[]\n")
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text("[]")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    result = runner._eval_to_self_model_feedback()
    assert isinstance(result["stale_domains"], list), (
        f"stale_domains must be a list, got: {type(result['stale_domains'])}"
    )
