"""
tests/unit/test_skill_health.py — Unit tests for scripts/lib/skill_health.py

Coverage:
  - is_zero_value(): None data, empty dict, error-masked, skill-specific overrides
  - is_stable_value(): None prev, identical data, timestamp-jitter stripped
  - update_health_counters(): counter increments, zero/stable streaks, recovery
  - classify_health(): warming_up, healthy, degraded, stable, broken
  - atomic_write_json(): round-trip write/read in temp dir

Ref: specs/skills-reloaded.md §3.3–3.8
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

import sys
_ARTHA = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ARTHA / "scripts"))

from lib.skill_health import (
    is_zero_value,
    is_stable_value,
    update_health_counters,
    classify_health,
    atomic_write_json,
    CADENCE_REDUCTION,
)


# ---------------------------------------------------------------------------
# is_zero_value()
# ---------------------------------------------------------------------------

class TestIsZeroValue:
    _cfg: dict = {"skills": {}}

    def test_none_data_is_zero(self):
        assert is_zero_value("noaa_weather", {"data": None}, None, self._cfg)

    def test_empty_dict_is_zero(self):
        assert is_zero_value("noaa_weather", {"data": {}}, None, self._cfg)

    def test_error_key_is_zero(self):
        assert is_zero_value("noaa_weather", {"data": {"error": "timeout"}}, None, self._cfg)

    def test_insufficient_data_status_is_zero(self):
        assert is_zero_value("noaa_weather", {"data": {"status": "insufficient_data"}}, None, self._cfg)

    def test_real_data_is_not_zero(self):
        assert not is_zero_value("noaa_weather", {"data": {"temp": 72}}, None, self._cfg)

    def test_zero_value_fields_override_all_present(self):
        """Skill with zero_value_fields: only empty if ALL specified fields are absent/falsy."""
        cfg = {"skills": {"uscis_status": {"zero_value_fields": ["case_status", "receipt_number"]}}}
        # Both fields absent → zero
        assert is_zero_value("uscis_status", {"data": {}}, None, cfg)

    def test_zero_value_fields_override_one_present(self):
        """If at least one of zero_value_fields has a truthy value → not zero."""
        cfg = {"skills": {"uscis_status": {"zero_value_fields": ["case_status", "receipt_number"]}}}
        assert not is_zero_value("uscis_status", {"data": {"case_status": "pending"}}, None, cfg)

    def test_zero_value_fields_override_tax_amount(self):
        cfg = {"skills": {"king_county_tax": {"zero_value_fields": ["tax_amount"]}}}
        assert is_zero_value("king_county_tax", {"data": {"tax_amount": None}}, None, cfg)
        assert not is_zero_value("king_county_tax", {"data": {"tax_amount": 5432.10}}, None, cfg)

    def test_no_data_key_is_zero(self):
        """Result with no 'data' key → data is None → zero."""
        assert is_zero_value("passport_expiry", {}, None, self._cfg)


# ---------------------------------------------------------------------------
# is_stable_value()
# ---------------------------------------------------------------------------

class TestIsStableValue:
    def test_none_prev_is_not_stable(self):
        assert not is_stable_value({"data": {"tax": 1000}}, None)

    def test_identical_data_is_stable(self):
        r = {"data": {"balance": 5000}}
        assert is_stable_value(r, r)

    def test_different_data_is_not_stable(self):
        r1 = {"data": {"balance": 5000}}
        r2 = {"data": {"balance": 4900}}
        assert not is_stable_value(r1, r2)

    def test_timestamp_jitter_stripped(self):
        """Data that differs only in timestamp keys → stable."""
        r1 = {"data": {"balance": 5000, "last_updated": "2026-03-27"}}
        r2 = {"data": {"balance": 5000, "last_updated": "2026-03-28"}}
        assert is_stable_value(r1, r2)

    def test_all_timestamp_keys_stripped(self):
        """All known jitter keys are stripped."""
        r1 = {"data": {"v": 1, "timestamp": "A", "fetched_at": "B", "as_of": "C", "checked_at": "D"}}
        r2 = {"data": {"v": 1, "timestamp": "X", "fetched_at": "Y", "as_of": "Z", "checked_at": "W"}}
        assert is_stable_value(r1, r2)


# ---------------------------------------------------------------------------
# update_health_counters()
# ---------------------------------------------------------------------------

class TestUpdateHealthCounters:
    def test_initial_entry_increments_total_runs(self):
        entry = {"current": {"status": "success"}}
        updated = update_health_counters(entry, is_zero=False, is_stable=False)
        assert updated["health"]["total_runs"] == 1

    def test_zero_value_increments_zero_count(self):
        entry = {"current": {"status": "success"}}
        updated = update_health_counters(entry, is_zero=True, is_stable=False)
        assert updated["health"]["zero_value_count"] == 1
        assert updated["health"]["consecutive_zero"] == 1

    def test_nonzero_resets_consecutive_zero(self):
        entry = {
            "current": {"status": "success"},
            "health": {"consecutive_zero": 5, "total_runs": 10, "success_count": 10,
                       "failure_count": 0, "zero_value_count": 5, "consecutive_stable": 0},
        }
        updated = update_health_counters(entry, is_zero=False, is_stable=False)
        assert updated["health"]["consecutive_zero"] == 0

    def test_stable_value_increments_consecutive_stable(self):
        entry = {"current": {"status": "success"}}
        updated = update_health_counters(entry, is_zero=False, is_stable=True)
        assert updated["health"]["consecutive_stable"] == 1

    def test_failure_increments_failure_count(self):
        entry = {"current": {"status": "failed"}}
        updated = update_health_counters(entry, is_zero=False, is_stable=False)
        assert updated["health"]["failure_count"] == 1
        assert updated["health"]["success_count"] == 0

    def test_wall_clock_stored(self):
        entry = {"current": {"status": "success"}}
        updated = update_health_counters(entry, is_zero=False, is_stable=False, last_wall_clock_ms=450)
        assert updated["health"]["last_wall_clock_ms"] == 450

    def test_does_not_mutate_original(self):
        entry = {"current": {"status": "success"}, "health": {"total_runs": 5}}
        _ = update_health_counters(entry, is_zero=False, is_stable=False)
        assert entry["health"]["total_runs"] == 5  # original unchanged

    def test_maturity_warming_up(self):
        entry = {"current": {"status": "success"}}
        updated = update_health_counters(entry, is_zero=False, is_stable=False)
        assert updated["health"]["maturity"] == "warming_up"  # total_runs=1 < 5

    def test_maturity_measuring(self):
        entry = {
            "current": {"status": "success"},
            "health": {"total_runs": 7, "success_count": 7, "failure_count": 0,
                       "zero_value_count": 0, "consecutive_zero": 0, "consecutive_stable": 0},
        }
        updated = update_health_counters(entry, is_zero=False, is_stable=False)
        assert updated["health"]["maturity"] == "measuring"  # total_runs=8, 5<=8<15

    def test_maturity_trusted(self):
        entry = {
            "current": {"status": "success"},
            "health": {"total_runs": 14, "success_count": 14, "failure_count": 0,
                       "zero_value_count": 0, "consecutive_zero": 0, "consecutive_stable": 0},
        }
        updated = update_health_counters(entry, is_zero=False, is_stable=False)
        assert updated["health"]["maturity"] == "trusted"  # total_runs=15


# ---------------------------------------------------------------------------
# classify_health()
# ---------------------------------------------------------------------------

class TestClassifyHealth:
    def test_warming_up(self):
        h = {"total_runs": 3, "success_count": 3, "failure_count": 0, "consecutive_zero": 0, "consecutive_stable": 0}
        assert classify_health(h) == "warming_up"

    def test_healthy(self):
        h = {"total_runs": 20, "success_count": 20, "failure_count": 0, "consecutive_zero": 0, "consecutive_stable": 5}
        assert classify_health(h) == "healthy"

    def test_degraded(self):
        h = {"total_runs": 20, "success_count": 20, "failure_count": 0, "consecutive_zero": 12, "consecutive_stable": 0}
        assert classify_health(h) == "degraded"

    def test_stable(self):
        h = {"total_runs": 20, "success_count": 20, "failure_count": 0, "consecutive_zero": 0, "consecutive_stable": 11}
        assert classify_health(h) == "stable"

    def test_broken(self):
        h = {"total_runs": 20, "success_count": 9, "failure_count": 11, "consecutive_zero": 0, "consecutive_stable": 0}
        assert classify_health(h) == "broken"

    def test_broken_on_all_failures(self):
        h = {"total_runs": 10, "success_count": 0, "failure_count": 10, "consecutive_zero": 0, "consecutive_stable": 0}
        assert classify_health(h) == "broken"

    def test_boundary_50pct_success_is_not_broken(self):
        # exactly 50% success → NOT broken (broken requires < 50%, i.e., success_rate < 0.50)
        h = {"total_runs": 10, "success_count": 5, "failure_count": 5, "consecutive_zero": 0, "consecutive_stable": 0}
        assert classify_health(h) != "broken"  # 0.50 is not < 0.50

    def test_49pct_success_is_broken(self):
        # 49% success rate → broken
        h = {"total_runs": 100, "success_count": 49, "failure_count": 51, "consecutive_zero": 0, "consecutive_stable": 0}
        assert classify_health(h) == "broken"


# ---------------------------------------------------------------------------
# CADENCE_REDUCTION mapping
# ---------------------------------------------------------------------------

class TestCadenceReduction:
    def test_every_run_reduces_to_daily(self):
        assert CADENCE_REDUCTION["every_run"] == "daily"

    def test_daily_reduces_to_weekly(self):
        assert CADENCE_REDUCTION["daily"] == "weekly"

    def test_weekly_has_no_reduction(self):
        assert "weekly" not in CADENCE_REDUCTION


# ---------------------------------------------------------------------------
# atomic_write_json()
# ---------------------------------------------------------------------------

class TestAtomicWriteJson:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "test_cache.json"
        data = {"skill": "passport_expiry", "health": {"total_runs": 5}}
        atomic_write_json(path, data)
        assert json.loads(path.read_text()) == data

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "cache.json"
        atomic_write_json(path, {"x": 1})
        assert path.exists()

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "cache.json"
        atomic_write_json(path, {"v": 1})
        atomic_write_json(path, {"v": 2})
        assert json.loads(path.read_text()) == {"v": 2}
