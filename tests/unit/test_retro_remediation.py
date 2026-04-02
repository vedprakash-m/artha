"""tests/unit/test_retro_remediation.py — Unit tests for EV-11 stale domain detection.

Tests _detect_stale_domains() in scripts/retrospective_view.py.
All data is synthetic (DD-5).
Ref: specs/eval.md EV-11, T-EV-11-01 to T-EV-11-02
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
def retro():
    return _load_module("retrospective_view_retro", _SCRIPTS_DIR / "retrospective_view.py")


def _run(domains: list, base_date: str = "2026-01") -> dict:
    return {"timestamp": f"{base_date}-15T09:00:00+00:00", "domains_processed": domains}


# ===========================================================================
# T-EV-11-01: domain absent from last 3 runs → appears in stale list
# ===========================================================================

def test_domain_absent_last_3_runs_is_stale(retro):
    """T-EV-11-01: Domain seen in early runs but absent from last 3 must be stale."""
    runs = [
        _run(["finance", "immigration"]),      # run 1 — immigration present
        _run(["finance"]),                       # run 2 — immigration absent
        _run(["finance"]),                       # run 3 — immigration absent
        _run(["finance"]),                       # run 4 — immigration absent (3 consecutive)
    ]
    stale = retro._detect_stale_domains(runs, streak_threshold=3)
    assert "immigration" in stale, (
        f"Expected 'immigration' in stale list, got: {stale}"
    )


# ===========================================================================
# T-EV-11-02: domain present in last run → not stale
# ===========================================================================

def test_domain_in_last_run_not_stale(retro):
    """T-EV-11-02: Domain appearing in the last run must not be flagged as stale."""
    runs = [
        _run(["finance", "health"]),   # run 1
        _run(["finance"]),              # run 2
        _run(["finance"]),              # run 3
        _run(["finance", "health"]),   # run 4 — health appears here
    ]
    stale = retro._detect_stale_domains(runs, streak_threshold=3)
    assert "health" not in stale, (
        f"'health' should not be stale (present in last run), got stale: {stale}"
    )
