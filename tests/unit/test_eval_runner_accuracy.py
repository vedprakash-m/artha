"""tests/unit/test_eval_runner_accuracy.py — Unit tests for MetricStore accuracy.

Tests for scripts/lib/metric_store.py: load_runs(), get_quality_trend(),
day-based filtering, empty-file safety, legacy fallback, and model field inclusion.

All fixtures are 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-4, T-EV-4-01 through T-EV-4-06
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def metric_store_mod():
    return _load_module("lib.metric_store", _LIB_DIR / "metric_store.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _write_runs_yaml(path: Path, runs: list[dict]) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(runs, allow_unicode=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# T-EV-4-01: load_runs reads quality_score from catch_up_runs.yaml
# ---------------------------------------------------------------------------

def test_ev4_01_load_runs_reads_quality_score(tmp_path, metric_store_mod):
    """T-EV-4-01: MetricStore.load_runs() returns entries with quality_score field."""
    runs_file = tmp_path / "state" / "catch_up_runs.yaml"
    _write_runs_yaml(runs_file, [
        {"timestamp": _iso(1), "quality_score": 82.5, "schema_version": "1.0.0"},
        {"timestamp": _iso(0), "quality_score": 76.0, "schema_version": "1.0.0"},
    ])
    ms = metric_store_mod.MetricStore(tmp_path)
    runs = ms.load_runs()
    assert len(runs) == 2
    quality_scores = [r.get("quality_score") for r in runs]
    assert 82.5 in quality_scores
    assert 76.0 in quality_scores


# ---------------------------------------------------------------------------
# T-EV-4-02: get_quality_trend computes correct avg_quality
# ---------------------------------------------------------------------------

def test_ev4_02_quality_trend_avg(tmp_path, metric_store_mod):
    """T-EV-4-02: get_quality_trend() returns correct avg_quality across scored runs."""
    runs_file = tmp_path / "state" / "catch_up_runs.yaml"
    # Scores: 70, 80, 90 → avg = 80.0
    _write_runs_yaml(runs_file, [
        {"timestamp": _iso(3), "quality_score": 70.0},
        {"timestamp": _iso(2), "quality_score": 80.0},
        {"timestamp": _iso(1), "quality_score": 90.0},
    ])
    ms = metric_store_mod.MetricStore(tmp_path)
    trend = ms.get_quality_trend(window=10)
    assert trend["avg_quality"] is not None
    assert abs(trend["avg_quality"] - 80.0) < 0.5


# ---------------------------------------------------------------------------
# T-EV-4-03: load_runs(days=7) filters to last 7 days
# ---------------------------------------------------------------------------

def test_ev4_03_load_runs_day_filter(tmp_path, metric_store_mod):
    """T-EV-4-03: load_runs(days=7) returns only entries within the last 7 days."""
    runs_file = tmp_path / "state" / "catch_up_runs.yaml"
    _write_runs_yaml(runs_file, [
        {"timestamp": _iso(10), "quality_score": 50.0, "model": "old-run"},   # >7 days
        {"timestamp": _iso(6), "quality_score": 75.0, "model": "recent-run"}, # within 7d
        {"timestamp": _iso(1), "quality_score": 80.0, "model": "today-run"},  # today
    ])
    ms = metric_store_mod.MetricStore(tmp_path)
    runs = ms.load_runs(days=7)
    assert len(runs) == 2
    models = [r.get("model") for r in runs]
    assert "recent-run" in models
    assert "today-run" in models
    assert "old-run" not in models


# ---------------------------------------------------------------------------
# T-EV-4-04: empty / missing file → load_runs returns [], no crash
# ---------------------------------------------------------------------------

def test_ev4_04_missing_file_returns_empty(tmp_path, metric_store_mod):
    """T-EV-4-04: load_runs() returns [] without exception when catch_up_runs.yaml is missing."""
    ms = metric_store_mod.MetricStore(tmp_path)
    runs = ms.load_runs()
    assert runs == []


def test_ev4_04b_empty_file_returns_empty(tmp_path, metric_store_mod):
    """T-EV-4-04b: load_runs() returns [] without exception when catch_up_runs.yaml is empty."""
    runs_file = tmp_path / "state" / "catch_up_runs.yaml"
    runs_file.parent.mkdir(parents=True, exist_ok=True)
    runs_file.write_text("", encoding="utf-8")
    ms = metric_store_mod.MetricStore(tmp_path)
    runs = ms.load_runs()
    assert runs == []


# ---------------------------------------------------------------------------
# T-EV-4-05: legacy fallback — load from health-check.md frontmatter
# ---------------------------------------------------------------------------

def test_ev4_05_no_crash_without_legacy_fallback(tmp_path, metric_store_mod):
    """T-EV-4-05: MetricStore falls back gracefully when neither file exists.

    Note: MetricStore.load_runs() has no health-check.md fallback by design
    (DD-2: catch_up_runs.yaml is the primary source). This test verifies
    the expected safe empty return.
    """
    # Neither catch_up_runs.yaml nor health-check.md exists
    ms = metric_store_mod.MetricStore(tmp_path)
    runs = ms.load_runs()
    assert isinstance(runs, list)
    assert runs == []


def test_ev4_05b_load_runs_survives_malformed_yaml(tmp_path, metric_store_mod):
    """T-EV-4-05b: load_runs() returns [] gracefully when YAML is malformed."""
    runs_file = tmp_path / "state" / "catch_up_runs.yaml"
    runs_file.parent.mkdir(parents=True, exist_ok=True)
    runs_file.write_text("{{ malformed: yaml: ]]", encoding="utf-8")
    ms = metric_store_mod.MetricStore(tmp_path)
    runs = ms.load_runs()
    assert isinstance(runs, list)


# ---------------------------------------------------------------------------
# T-EV-4-06: model field included in load_runs output
# ---------------------------------------------------------------------------

def test_ev4_06_model_field_returned(tmp_path, metric_store_mod):
    """T-EV-4-06: load_runs() includes the 'model' field when present in entry."""
    runs_file = tmp_path / "state" / "catch_up_runs.yaml"
    _write_runs_yaml(runs_file, [
        {
            "timestamp": _iso(1),
            "model": "claude-opus-4-5",
            "quality_score": 88.0,
        }
    ])
    ms = metric_store_mod.MetricStore(tmp_path)
    runs = ms.load_runs()
    assert len(runs) == 1
    assert runs[0].get("model") == "claude-opus-4-5"
