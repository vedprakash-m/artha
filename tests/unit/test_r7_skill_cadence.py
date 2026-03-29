"""
tests/unit/test_r7_skill_cadence.py — Unit tests for R7 cadence reduction in skill_runner.py

Coverage:
  - should_run() returns False when consecutive_zero >= 10 and reduced cadence not elapsed
  - should_run() returns True when consecutive_zero < 10 (below threshold)
  - P0 skills (uscis_status, visa_bulletin) are NEVER auto-reduced by R7
  - R7 increments r7_skips counter in the cache dict
  - Recovery: non-zero run resets consecutive_zero to 0 (via update_health_counters)
  - Weekly cadence (cadence floor) is NOT reduced further by R7
  - Maturity 'warming_up' (total_runs < 5) is exempt from R7

Ref: specs/skills-reloaded.md §3.5, §3.9
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_ARTHA = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ARTHA / "scripts"))

from skill_runner import should_run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recent_ts() -> str:
    """A last_run timestamp 30 minutes ago (recently run — cadence not elapsed for daily)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()


def _old_ts() -> str:
    """A last_run timestamp 2 days ago (daily/every_run cadence definitely elapsed)."""
    return (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()


def _p1_config(cadence: str = "every_run") -> dict:
    return {"skills": {"passport_expiry": {"enabled": True, "priority": "P1", "cadence": cadence}}}


def _p0_config(cadence: str = "every_run") -> dict:
    return {"skills": {"uscis_status": {"enabled": True, "priority": "P0", "cadence": cadence}}}


def _p2_config(cadence: str = "daily") -> dict:
    return {"skills": {"ai_trend_radar": {"enabled": True, "priority": "P2", "cadence": cadence}}}


# ---------------------------------------------------------------------------
# Basic cadence behavior (existing behavior, non-R7)
# ---------------------------------------------------------------------------

class TestBasicCadence:
    def test_every_run_always_runs(self):
        cfg = _p1_config("every_run")
        cache = {}
        assert should_run("passport_expiry", cfg, cache) is True

    def test_disabled_skill_does_not_run(self):
        cfg = {"skills": {"passport_expiry": {"enabled": False, "priority": "P1", "cadence": "every_run"}}}
        assert should_run("passport_expiry", cfg, {}) is False

    def test_daily_skips_when_recently_run(self):
        cfg = _p1_config("daily")
        cache = {"passport_expiry": {"last_run": _recent_ts()}}
        assert should_run("passport_expiry", cfg, cache) is False

    def test_daily_runs_after_1_day(self):
        cfg = _p1_config("daily")
        cache = {"passport_expiry": {"last_run": _old_ts()}}
        assert should_run("passport_expiry", cfg, cache) is True


# ---------------------------------------------------------------------------
# R7 cadence reduction
# ---------------------------------------------------------------------------

class TestR7CadenceReduction:
    def _cache_with_zeros(self, skill: str, cadence_last_run: str, consecutive_zero: int,
                          last_run: str | None = None) -> dict:
        return {
            skill: {
                "last_run": last_run or _old_ts(),
                "health": {
                    "consecutive_zero": consecutive_zero,
                    "maturity": "measuring",
                    "r7_skips": 0,
                },
            }
        }

    def test_r7_fires_at_10_consecutive_zeros_daily_cadence(self):
        """every_run skill with 10 consecutive zeros → R7 reduces to daily cadence.
        If the daily cadence hasn't elapsed (last_run was recent), should_run() returns False."""
        cfg = _p1_config("every_run")
        # Set last_run to 30 min ago so every_run cadence is due but daily is NOT
        cache = {
            "passport_expiry": {
                "last_run": _recent_ts(),  # 30 min ago — daily not elapsed
                "health": {"consecutive_zero": 10, "maturity": "measuring", "r7_skips": 0},
            }
        }
        result = should_run("passport_expiry", cfg, cache)
        assert result is False

    def test_r7_does_not_fire_below_10_zeros(self):
        """consecutive_zero < 10 → R7 doesn't fire, every_run skill runs normally."""
        cfg = _p1_config("every_run")
        cache = {
            "passport_expiry": {
                "last_run": _old_ts(),
                "health": {"consecutive_zero": 9, "maturity": "measuring", "r7_skips": 0},
            }
        }
        assert should_run("passport_expiry", cfg, cache) is True

    def test_r7_does_not_fire_for_warming_up_maturity(self):
        """warming_up maturity (total_runs < 5) is exempt from R7."""
        cfg = _p1_config("every_run")
        cache = {
            "passport_expiry": {
                "last_run": _recent_ts(),
                "health": {"consecutive_zero": 15, "maturity": "warming_up", "r7_skips": 0},
            }
        }
        # warming_up → R7 exempt; but every_run cadence always trips, so last_run doesn't block
        # With warming_up, R7 block is skipped → falls through to True
        assert should_run("passport_expiry", cfg, cache) is True

    def test_r7_increments_r7_skips_counter(self):
        """When R7 fires, r7_skips is incremented in the cache dict."""
        cfg = _p1_config("every_run")
        cache = {
            "passport_expiry": {
                "last_run": _recent_ts(),  # daily not elapsed
                "health": {"consecutive_zero": 12, "maturity": "measuring", "r7_skips": 3},
            }
        }
        should_run("passport_expiry", cfg, cache)
        assert cache["passport_expiry"]["health"]["r7_skips"] == 4

    def test_r7_weekly_cadence_is_floor_no_further_reduction(self):
        """A weekly skill with consecutive_zero >= 10 — no cadence below weekly, R7 doesn't skip."""
        cfg = _p2_config("weekly")
        eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        cache = {
            "ai_trend_radar": {
                "last_run": eight_days_ago,  # weekly elapsed (8 days > 7 days)
                "health": {"consecutive_zero": 20, "maturity": "measuring", "r7_skips": 0},
            }
        }
        # CADENCE_REDUCTION has no entry for 'weekly' → R7 can't reduce → returns True
        assert should_run("ai_trend_radar", cfg, cache) is True


# ---------------------------------------------------------------------------
# P0 exemption
# ---------------------------------------------------------------------------

class TestP0Exemption:
    def test_p0_skill_runs_regardless_of_consecutive_zeros(self):
        """P0 skills are NEVER reduced by R7."""
        cfg = _p0_config("every_run")
        cache = {
            "uscis_status": {
                "last_run": _old_ts(),
                "health": {"consecutive_zero": 25, "maturity": "trusted", "r7_skips": 0},
            }
        }
        assert should_run("uscis_status", cfg, cache) is True

    def test_p0_daily_cadence_not_elapsed_still_skips(self):
        """P0 skills still respect their configured cadence; R7 just can't intervene."""
        cfg = {"skills": {"uscis_status": {"enabled": True, "priority": "P0", "cadence": "daily"}}}
        cache = {"uscis_status": {"last_run": _recent_ts()}}
        assert should_run("uscis_status", cfg, cache) is False


# ---------------------------------------------------------------------------
# Recovery (health counter reset)
# ---------------------------------------------------------------------------

class TestR7Recovery:
    def test_nonzero_result_resets_consecutive_zero(self):
        """A non-zero run resets consecutive_zero so R7 no longer fires."""
        from lib.skill_health import update_health_counters

        entry = {
            "current": {"status": "success"},
            "health": {
                "consecutive_zero": 12, "total_runs": 12, "success_count": 12,
                "failure_count": 0, "zero_value_count": 12, "consecutive_stable": 0,
            },
        }
        updated = update_health_counters(entry, is_zero=False, is_stable=False)
        assert updated["health"]["consecutive_zero"] == 0
        assert updated["health"]["classification"] == "healthy"
