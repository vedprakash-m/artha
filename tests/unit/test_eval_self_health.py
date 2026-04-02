"""tests/unit/test_eval_self_health.py — Unit tests for EV-16b eval self-health.

Tests _check_eval_self_health() in scripts/eval_runner.py.
All data is synthetic (DD-5).
Ref: specs/eval.md EV-16b, T-EV-16b-01 to T-EV-16b-03
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone, timedelta
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
    return _load_module("eval_runner_sh", _SCRIPTS_DIR / "eval_runner.py")


def _recent_ts(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _old_ts(days_ago: int = 10) -> str:
    return _recent_ts(days_ago)


# ===========================================================================
# T-EV-16b-01: 5 consecutive null quality_scores → P1 alert EV-SH-01
# ===========================================================================

def test_null_streak_5_triggers_p1_alert(runner, tmp_path, monkeypatch):
    """T-EV-16b-01: 5+ consecutive null quality scores must trigger EV-SH-01 P1 alert."""
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text(json.dumps([
        {"quality_score": None} for _ in range(5)
    ]))
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)
    # Use an existing runs path to not trigger EV-SH-02
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text(f"---\n- timestamp: '{_recent_ts(1)}'\n  quality_score: null\n")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)

    alerts = runner._check_eval_self_health()
    codes = [a.get("code") for a in alerts]
    assert "EV-SH-01" in codes, f"Expected EV-SH-01 alert, got codes: {codes}"
    p1_alert = next(a for a in alerts if a.get("code") == "EV-SH-01")
    assert p1_alert["severity"] == "P1"


# ===========================================================================
# T-EV-16b-02: 4 null + 1 valid score → no EV-SH-01 alert
# ===========================================================================

def test_null_streak_4_does_not_trigger_alert(runner, tmp_path, monkeypatch):
    """T-EV-16b-02: 4 consecutive nulls (below threshold of 5) must not trigger EV-SH-01."""
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text(json.dumps([
        {"quality_score": None},
        {"quality_score": None},
        {"quality_score": None},
        {"quality_score": None},
        {"quality_score": 72.0},   # one valid score — breaks streak
    ]))
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)
    runs_file = tmp_path / "catch_up_runs.yaml"
    runs_file.write_text(f"---\n- timestamp: '{_recent_ts(1)}'\n  quality_score: 72.0\n")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)

    alerts = runner._check_eval_self_health()
    codes = [a.get("code") for a in alerts]
    assert "EV-SH-01" not in codes, f"Unexpected EV-SH-01 trigger: {alerts}"


# ===========================================================================
# T-EV-16b-03: last run >7 days ago → P2 alert EV-SH-02
# ===========================================================================

def test_stale_runs_triggers_p2_alert(runner, tmp_path, monkeypatch):
    """T-EV-16b-03: No catch-up runs in last 7 days must trigger EV-SH-02 P2 alert."""
    scores_file = tmp_path / "briefing_scores.json"
    scores_file.write_text(json.dumps([{"quality_score": 80.0}]))
    monkeypatch.setattr(runner, "_BRIEFING_SCORES", scores_file)

    runs_file = tmp_path / "catch_up_runs.yaml"
    old_ts = _old_ts(10)  # 10 days ago — stale
    runs_file.write_text(f"---\n- timestamp: '{old_ts}'\n  quality_score: 80.0\n")
    monkeypatch.setattr(runner, "_CATCH_UP_RUNS", runs_file)

    alerts = runner._check_eval_self_health()
    codes = [a.get("code") for a in alerts]
    assert "EV-SH-02" in codes, f"Expected EV-SH-02 alert for stale runs, got: {codes}"
    p2_alert = next(a for a in alerts if a.get("code") == "EV-SH-02")
    assert p2_alert["severity"] == "P2"
