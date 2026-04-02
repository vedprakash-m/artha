"""tests/unit/test_briefing_adapter_fields.py — Unit tests for EV-3b adaptive rules R3-R6.

Tests for briefing_adapter.py _r3_calibration_skip_rate, _r4_coaching_dismiss_rate,
_r5_consistent_domains, and _r6_weekend_planner_skip.

All fixtures are 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-3b, T-EV-3b-01 through T-EV-3b-04
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def ba():
    return _load_module("briefing_adapter_fields", _SCRIPTS_DIR / "briefing_adapter.py")


# ---------------------------------------------------------------------------
# T-EV-3b-01: calibration_skip_rate fires when >80% of last 10 runs skipped
# ---------------------------------------------------------------------------

def test_ev3b_01_calibration_skip_rate_fires(ba):
    """T-EV-3b-01: R3 fires (returns non-None) when 9/10 runs have calibration_skipped=True."""
    # Build 10 runs: 9 skipped, 1 not
    runs = [{"calibration_skipped": True}] * 9 + [{"calibration_skipped": False}]
    result = ba._r3_calibration_skip_rate(runs, {})
    assert result is not None, (
        "_r3_calibration_skip_rate should fire when 9/10 runs skip calibration (90% > 80%)"
    )


def test_ev3b_01_calibration_skip_rate_no_fire(ba):
    """T-EV-3b-01 (inverse): R3 does NOT fire when skip rate ≤80%."""
    # 8/10 = 80% — exactly at threshold, should NOT fire (>80% required)
    runs = [{"calibration_skipped": True}] * 8 + [{"calibration_skipped": False}] * 2
    result = ba._r3_calibration_skip_rate(runs, {})
    assert result is None, (
        "_r3_calibration_skip_rate should NOT fire at exactly 80% (threshold is >80%)"
    )


# ---------------------------------------------------------------------------
# T-EV-3b-02: coaching_dismiss_rate fires when >70% dismissed over last 10
# ---------------------------------------------------------------------------

def test_ev3b_02_coaching_dismiss_rate_fires(ba):
    """T-EV-3b-02: R4 fires when 8/10 coaching nudges are dismissed."""
    runs = [{"coaching_nudge": "dismissed"}] * 8 + [{"coaching_nudge": "accepted"}] * 2
    result = ba._r4_coaching_dismiss_rate(runs)
    assert result is not None, (
        "_r4_coaching_dismiss_rate should fire when 8/10 nudges dismissed (80% > 70%)"
    )


def test_ev3b_02_coaching_dismiss_rate_no_fire(ba):
    """T-EV-3b-02 (inverse): R4 does NOT fire when dismiss rate ≤70%."""
    runs = [{"coaching_nudge": "dismissed"}] * 7 + [{"coaching_nudge": "accepted"}] * 3
    result = ba._r4_coaching_dismiss_rate(runs)
    assert result is None, (
        "_r4_coaching_dismiss_rate should NOT fire at exactly 70% (threshold is >70%)"
    )


# ---------------------------------------------------------------------------
# T-EV-3b-03: _r5_consistent_domains reads 'domains_processed' field
# ---------------------------------------------------------------------------

def test_ev3b_03_r5_reads_domains_processed(ba):
    """T-EV-3b-03: R5 reads domains_processed (not domains_loaded) field from runs."""
    # 10 runs all with same 3 domains in domains_processed
    consistent_domains = ["finance", "health", "immigration"]
    runs = [{"domains_processed": consistent_domains}] * 10
    result = ba._r5_consistent_domains(runs)
    # R5 should return the consistent domain list (≤5 domains appearing in all runs)
    assert isinstance(result, list)
    assert sorted(result) == sorted(consistent_domains), (
        f"Expected consistent domains {consistent_domains!r}, got {result!r}"
    )


def test_ev3b_03_r5_does_not_use_domains_loaded_when_domains_processed_present(ba):
    """T-EV-3b-03 (clarity): domains_processed takes priority over domains_loaded."""
    # domains_processed has one consistent domain, domains_loaded has different
    runs = [
        {"domains_processed": ["finance"], "domains_loaded": ["immigration", "health"]}
    ] * 10
    result = ba._r5_consistent_domains(runs)
    # Should use domains_processed only
    assert result == ["finance"]


# ---------------------------------------------------------------------------
# T-EV-3b-04: _r6_weekend_planner_skip fires when all applicable runs skipped
# ---------------------------------------------------------------------------

def test_ev3b_04_weekend_planner_skip_fires(ba):
    """T-EV-3b-04: R6 fires when weekend_planner_shown=True in ≥5 runs and all skipped."""
    # 6 runs where planner was shown AND skipped
    runs = [
        {"weekend_planner_shown": True, "weekend_planner_skipped": True}
    ] * 6
    result = ba._r6_weekend_planner_skip(runs)
    assert result is not None, (
        "_r6_weekend_planner_skip should fire when all 6 applicable runs skipped"
    )


def test_ev3b_04_weekend_planner_skip_no_fire_when_not_all_skipped(ba):
    """T-EV-3b-04 (inverse): R6 does NOT fire if any applicable run was not skipped."""
    runs = [
        {"weekend_planner_shown": True, "weekend_planner_skipped": True},
        {"weekend_planner_shown": True, "weekend_planner_skipped": True},
        {"weekend_planner_shown": True, "weekend_planner_skipped": True},
        {"weekend_planner_shown": True, "weekend_planner_skipped": True},
        {"weekend_planner_shown": True, "weekend_planner_skipped": False},  # Not skipped
        {"weekend_planner_shown": True, "weekend_planner_skipped": True},
    ]
    result = ba._r6_weekend_planner_skip(runs)
    assert result is None, (
        "_r6_weekend_planner_skip should NOT fire when at least one run was not skipped"
    )


def test_ev3b_04_weekend_planner_skip_no_fire_insufficient_data(ba):
    """T-EV-3b-04 (insufficient data): R6 does NOT fire with fewer than 5 applicable runs."""
    runs = [
        {"weekend_planner_shown": True, "weekend_planner_skipped": True}
    ] * 4  # Only 4 applicable — below threshold of 5
    result = ba._r6_weekend_planner_skip(runs)
    assert result is None, (
        "_r6_weekend_planner_skip should NOT fire with only 4 applicable runs (< 5 threshold)"
    )
