"""tests/unit/test_memory_health.py — Unit tests for EV-11c memory health analysis.

Tests _analyze_memory_health() in scripts/eval_runner.py.
All data is synthetic (DD-5).
Ref: specs/eval.md EV-11c, T-EV-11c-01 to T-EV-11c-04
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
    return _load_module("eval_runner_mem", _SCRIPTS_DIR / "eval_runner.py")


def _recent_ts(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


# ===========================================================================
# T-EV-11c-01: 2 runs in JSONL → total_runs_30d = 2
# ===========================================================================

def test_memory_health_counts_recent_runs(runner, tmp_path, monkeypatch):
    """T-EV-11c-01: Two recent JSONL records must produce total_runs_30d=2."""
    log_file = tmp_path / "memory_pipeline_runs.jsonl"
    records = [
        {"timestamp": _recent_ts(1), "status": "ok", "duration_ms": 120},
        {"timestamp": _recent_ts(2), "status": "ok", "duration_ms": 95},
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    monkeypatch.setattr(runner, "_MEMORY_PIPELINE_RUNS", log_file)

    result = runner._analyze_memory_health()
    assert result.get("status") == "ok"
    assert result.get("total_runs_30d") == 2


# ===========================================================================
# T-EV-11c-02: one run with status=error → error_count_30d = 1
# ===========================================================================

def test_memory_health_counts_errors(runner, tmp_path, monkeypatch):
    """T-EV-11c-02: One error run must produce error_count_30d=1."""
    log_file = tmp_path / "memory_pipeline_runs.jsonl"
    records = [
        {"timestamp": _recent_ts(1), "status": "ok"},
        {"timestamp": _recent_ts(2), "status": "error", "error": "timeout"},
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    monkeypatch.setattr(runner, "_MEMORY_PIPELINE_RUNS", log_file)

    result = runner._analyze_memory_health()
    assert result.get("error_count_30d") == 1


# ===========================================================================
# T-EV-11c-03: missing JSONL → status = "no_data"
# ===========================================================================

def test_memory_health_missing_file(runner, tmp_path, monkeypatch):
    """T-EV-11c-03: Missing JSONL must return status='no_data'."""
    non_existent = tmp_path / "does_not_exist.jsonl"
    monkeypatch.setattr(runner, "_MEMORY_PIPELINE_RUNS", non_existent)

    result = runner._analyze_memory_health()
    assert result.get("status") == "no_data"


# ===========================================================================
# T-EV-11c-04: empty JSONL → status = "empty"
# ===========================================================================

def test_memory_health_empty_file(runner, tmp_path, monkeypatch):
    """T-EV-11c-04: Empty JSONL must return status='empty'."""
    log_file = tmp_path / "memory_pipeline_runs.jsonl"
    log_file.write_text("")
    monkeypatch.setattr(runner, "_MEMORY_PIPELINE_RUNS", log_file)

    result = runner._analyze_memory_health()
    assert result.get("status") == "empty"
