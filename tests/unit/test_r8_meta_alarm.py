"""
tests/unit/test_r8_meta_alarm.py — Unit tests for R8 meta-regression alarm
in scripts/briefing_adapter.py

Coverage:
  - R8 fires when engagement_rate < 0.15 for 7+ of last 10 non-null runs
  - R8 silent with < 7 non-null data points regardless of engagement values
  - R8 silent when fewer than 7 low-engagement runs in the window
  - R8 skips null entries (no-signal catch-ups)
  - R8 result appears in BriefingConfig.adaptive_adjustments when triggered
  - R8 does not fire when > 10 runs exist but only the last 10 are used

Ref: specs/skills-reloaded.md §3.7, §5 Risk R10
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ARTHA = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ARTHA / "scripts"))

from briefing_adapter import BriefingAdapter, _r8_meta_regression_alarm


# ---------------------------------------------------------------------------
# _r8_meta_regression_alarm() unit tests
# ---------------------------------------------------------------------------

class TestR8DirectFunction:
    def _runs(self, engagement_rates: list) -> list[dict]:
        return [{"engagement_rate": r, "items_surfaced": 0 if r is None else 5}
                for r in engagement_rates]

    def test_fires_with_7_low_of_10(self):
        """7 low-engagement runs out of 10 → R8 alarm fires."""
        rates = [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.30, 0.30, 0.30]
        assert _r8_meta_regression_alarm(self._runs(rates)) is not None

    def test_silent_with_only_6_low(self):
        """6 low-engagement runs out of 10 → R8 stays silent."""
        rates = [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.30, 0.30, 0.30, 0.30]
        assert _r8_meta_regression_alarm(self._runs(rates)) is None

    def test_silent_with_fewer_than_7_data_points(self):
        """Only 5 non-null runs → insufficient data, R8 stays silent."""
        rates = [0.05, 0.05, 0.05, 0.05, 0.05]
        assert _r8_meta_regression_alarm(self._runs(rates)) is None

    def test_null_entries_excluded_from_count(self):
        """Null entries are skipped; need 7 valid non-null low-engagement entries."""
        # 7 low + 3 null = 7 valid low of 7 non-null → fires
        rates = [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, None, None, None]
        result = _r8_meta_regression_alarm(self._runs(rates))
        assert result is not None

    def test_all_null_returns_none(self):
        """All null entries → 0 data points → R8 silent."""
        runs = [{"engagement_rate": None} for _ in range(10)]
        assert _r8_meta_regression_alarm(runs) is None

    def test_only_last_10_runs_used(self):
        """R8 uses a rolling window of 10; older runs are ignored."""
        # 20 runs: first 7 are very low, last 10 are all high → no alarm
        rates = [0.05] * 7 + [0.60] * 3 + [0.50] * 10
        runs = self._runs(rates)
        # Last 10 entries are all 0.50 → no alarm
        assert _r8_meta_regression_alarm(runs) is None


# ---------------------------------------------------------------------------
# R8 integration via BriefingAdapter.recommend()
# ---------------------------------------------------------------------------

class TestR8ViaAdapter:
    def test_r8_appears_in_adaptive_adjustments(self):
        """R8 alarm string is appended to adaptive_adjustments when triggered."""
        low_runs = [
            {"engagement_rate": 0.05, "items_surfaced": 5, "format": "standard"}
            for _ in range(10)
        ]
        adapter = BriefingAdapter()
        with patch("briefing_adapter._load_catch_up_runs", return_value=low_runs):
            cfg = adapter.recommend(base_format="standard", hours_elapsed=14)
        r8_present = any("R8" in adj or "meta_regression" in adj for adj in cfg.adaptive_adjustments)
        assert r8_present, f"Expected R8 in adjustments, got: {cfg.adaptive_adjustments}"

    def test_r8_silent_with_high_engagement(self):
        """High engagement runs → R8 never fires."""
        high_runs = [
            {"engagement_rate": 0.50, "items_surfaced": 8, "format": "standard"}
            for _ in range(10)
        ]
        adapter = BriefingAdapter()
        with patch("briefing_adapter._load_catch_up_runs", return_value=high_runs):
            cfg = adapter.recommend(base_format="standard", hours_elapsed=14)
        r8_present = any("R8" in adj or "meta_regression" in adj for adj in cfg.adaptive_adjustments)
        assert not r8_present, f"Unexpected R8 in adjustments: {cfg.adaptive_adjustments}"
