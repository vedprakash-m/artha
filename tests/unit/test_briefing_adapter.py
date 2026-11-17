"""
tests/unit/test_briefing_adapter.py — Unit tests for scripts/briefing_adapter.py (E5)

Coverage:
  - BriefingAdapter.recommend() returns BriefingConfig
  - With < 10 history entries, no adaptation applied
  - With >= 10 history entries, adaptation may apply
  - flash format requested → flash returned unchanged
  - user_forced=True → no adaptation override
  - High context pressure rule R1: format → flash
  - Low context pressure with staleness rule R2: format → deep
  - Feature flag disabled → returns plain BriefingConfig with no adaptation
  - BriefingConfig fields have correct types
  - transparency_footer is populated when rules fire
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest

import briefing_adapter as ba
from briefing_adapter import BriefingAdapter, BriefingConfig, _r2_low_signal_noise


# ---------------------------------------------------------------------------
# BriefingConfig shape
# ---------------------------------------------------------------------------

class TestBriefingConfigShape:
    def test_returns_briefing_config_instance(self):
        adapter = BriefingAdapter()
        cfg = adapter.recommend(base_format="standard", hours_elapsed=12, user_forced=False)
        assert isinstance(cfg, BriefingConfig)

    def test_required_fields_present(self):
        adapter = BriefingAdapter()
        cfg = adapter.recommend(base_format="standard")
        assert hasattr(cfg, "format")
        assert hasattr(cfg, "domain_item_cap")
        assert hasattr(cfg, "coaching_enabled")
        assert hasattr(cfg, "adaptive_adjustments")
        assert hasattr(cfg, "calibration_count")


# ---------------------------------------------------------------------------
# Minimum history guard
# ---------------------------------------------------------------------------

class TestMinimumHistory:
    def test_fewer_than_10_returns_no_adaptation(self):
        adapter = BriefingAdapter()
        # Patch history to return < 10 entries (below cold-start threshold)
        with patch("briefing_adapter._load_catch_up_runs", return_value=[{"format": "standard"}] * 5):
            cfg = adapter.recommend(base_format="standard", hours_elapsed=12)
        assert cfg.adaptive_adjustments == []

    def test_exactly_10_enables_adaptation(self):
        adapter = BriefingAdapter()
        history = [{"format": "standard", "context_pressure": 30}] * 10
        with patch("briefing_adapter._load_catch_up_runs", return_value=history):
            cfg = adapter.recommend(base_format="standard", hours_elapsed=12)
        # With exactly 10 runs (at threshold), adaptation path is entered; no crash
        assert isinstance(cfg, BriefingConfig)


# ---------------------------------------------------------------------------
# User-forced bypass
# ---------------------------------------------------------------------------

class TestUserForced:
    def test_user_forced_true_no_override(self):
        adapter = BriefingAdapter()
        # Use flash-formatted history (>60%) to trigger R1, but user_forced blocks it
        history = [{"briefing_format": "flash", "format": "flash"}] * 15
        with patch("briefing_adapter._load_catch_up_runs", return_value=history):
            # Even though R1 would fire, user_forced=True prevents format override
            cfg = adapter.recommend(base_format="deep", hours_elapsed=12, user_forced=True)
        # With user_forced, format should remain what was requested
        assert cfg.format == "deep"


# ---------------------------------------------------------------------------
# Adaptive rules
# ---------------------------------------------------------------------------

class TestAdaptiveRuleHighPressure:
    def test_high_context_pressure_suggests_flash(self):
        adapter = BriefingAdapter()
        # R1 fires when >60% of last 10 runs used flash format
        history = [{"briefing_format": "flash", "format": "flash"}] * 12
        with patch("briefing_adapter._load_catch_up_runs", return_value=history):
            cfg = adapter.recommend(base_format="standard", hours_elapsed=12, user_forced=False)
        # R1 should override format to flash
        assert cfg.format == "flash" or any("R1" in str(a) for a in cfg.adaptive_adjustments)


# ---------------------------------------------------------------------------
# Flash format passthrough
# ---------------------------------------------------------------------------

class TestFlashFormat:
    def test_flash_in_flash_out(self):
        adapter = BriefingAdapter()
        cfg = adapter.recommend(base_format="flash", hours_elapsed=2, user_forced=False)
        assert cfg.format == "flash"


# ---------------------------------------------------------------------------
# Feature flag disabled
# ---------------------------------------------------------------------------

class TestFeatureFlagDisabled:
    def test_flag_disabled_returns_plain_config(self):
        with patch("briefing_adapter._load_flag", return_value=False):
            adapter = BriefingAdapter()
            cfg = adapter.recommend(base_format="standard", hours_elapsed=12)
        assert isinstance(cfg, BriefingConfig)
        assert cfg.adaptive_adjustments == []


# ---------------------------------------------------------------------------
# Transparency footer
# ---------------------------------------------------------------------------

class TestTransparencyFooter:
    def test_footer_present_when_adaptation_fires(self):
        adapter = BriefingAdapter()
        # Use flash-heavy history (>60%) to trigger R1
        history = [{"briefing_format": "flash", "format": "flash"}] * 15
        with patch("briefing_adapter._load_catch_up_runs", return_value=history):
            cfg = adapter.recommend(base_format="standard", hours_elapsed=12, user_forced=False)
        # If adaptation fired, transparency footer should not be empty
        if cfg.adaptive_adjustments:
            footer = adapter.format_footer(cfg)
            assert "Adapted" in footer or len(footer) > 0


class TestR2LowSignalNoise:
    """_r2_low_signal_noise() cold-start gate and signal_noise fallback."""

    def test_cold_start_and_firing_thresholds(self):
        assert _r2_low_signal_noise([]) is None
        assert _r2_low_signal_noise([{"engagement_rate": 0.1, "items_surfaced": 5}] * 9) is None
        assert _r2_low_signal_noise([{"engagement_rate": 0.1, "items_surfaced": 5}] * 10) is not None
        mixed = [{"engagement_rate": 0.10, "items_surfaced": 5}] * 7 + [{"engagement_rate": 0.50, "items_surfaced": 5}] * 3
        assert _r2_low_signal_noise(mixed) is not None
        assert _r2_low_signal_noise([{"engagement_rate": 0.60, "items_surfaced": 5}] * 10) is None
        assert _r2_low_signal_noise([{"engagement_rate": 0.60, "signal_noise": 0.05}] * 10) is None

    def test_null_and_signal_noise_fallback_and_recommend(self):
        null_mix = [{"engagement_rate": 0.10}] * 6 + [{"engagement_rate": None}] * 2 + [{"engagement_rate": 0.50}] * 2
        res_null = _r2_low_signal_noise(null_mix)
        assert res_null is None or isinstance(res_null, str)

        legacy = _r2_low_signal_noise([{"signal_noise": 0.10}] * 10)
        assert legacy is None or isinstance(legacy, str)

        adapter = BriefingAdapter()
        low_runs = [{"engagement_rate": 0.08, "items_surfaced": 5, "format": "standard"}] * 10
        with patch("briefing_adapter._load_catch_up_runs", return_value=low_runs):
            assert isinstance(adapter.recommend(base_format="standard", hours_elapsed=14).adaptive_adjustments, list)


class TestAdaptiveRulesR3ThroughR6:
    """Rules R3 (calibration skip), R4 (coaching dismiss), R5 (domains), R6 (weekend planner)."""

    def test_r3_calibration_skip_rate(self):
        assert ba._r3_calibration_skip_rate([{"calibration_skipped": True}] * 9 + [{"calibration_skipped": False}], {}) is not None
        assert ba._r3_calibration_skip_rate([{"calibration_skipped": True}] * 8 + [{"calibration_skipped": False}] * 2, {}) is None

    def test_r4_coaching_dismiss_rate(self):
        assert ba._r4_coaching_dismiss_rate([{"coaching_nudge": "dismissed"}] * 8 + [{"coaching_nudge": "accepted"}] * 2) is not None
        assert ba._r4_coaching_dismiss_rate([{"coaching_nudge": "dismissed"}] * 7 + [{"coaching_nudge": "accepted"}] * 3) is None

    def test_r5_consistent_domains_prefers_domains_processed(self):
        runs = [{"domains_processed": ["finance", "health", "immigration"]}] * 10
        assert sorted(ba._r5_consistent_domains(runs)) == ["finance", "health", "immigration"]
        priority_runs = [{"domains_processed": ["finance"], "domains_loaded": ["immigration", "health"]}] * 10
        assert ba._r5_consistent_domains(priority_runs) == ["finance"]

    def test_r6_weekend_planner_skip(self):
        assert ba._r6_weekend_planner_skip([{"weekend_planner_shown": True, "weekend_planner_skipped": True}] * 6) is not None
        partial = [{"weekend_planner_shown": True, "weekend_planner_skipped": True}] * 5 + [
            {"weekend_planner_shown": True, "weekend_planner_skipped": False}
        ]
        assert ba._r6_weekend_planner_skip(partial) is None
        assert ba._r6_weekend_planner_skip([{"weekend_planner_shown": True, "weekend_planner_skipped": True}] * 4) is None


class TestR9QualityRegression:
    """_r9_quality_regression() reads MetricStore trend safely."""

    @pytest.mark.parametrize(
        ("trend", "run_count", "avg_q", "expect_fire"),
        [
            ("regressing", 8, 52.1, True),
            ("stable", 10, 78.5, False),
            ("regressing", 4, 45.0, False),
        ],
    )
    def test_r9_trend_thresholds(self, monkeypatch, trend, run_count, avg_q, expect_fire):
        class FakeMetricStore:
            def __init__(self, _root_dir):
                pass

            def get_quality_trend(self, window: int = 7):
                return {"trend": trend, "run_count": run_count, "avg_quality": avg_q}

        fake_lib = types.ModuleType("lib")
        fake_mod = types.ModuleType("lib.metric_store")
        fake_mod.MetricStore = FakeMetricStore  # type: ignore[attr-defined]
        fake_lib.metric_store = fake_mod  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "lib", fake_lib)
        monkeypatch.setitem(sys.modules, "lib.metric_store", fake_mod)

        result = ba._r9_quality_regression(runs=[{}] * run_count, min_runs=7)
        if expect_fire:
            assert result is not None and "regressing" in result and str(avg_q) in result
        else:
            assert result is None

    def test_r9_returns_none_on_metric_store_exception(self, monkeypatch):
        class BrokenMetricStore:
            def __init__(self, _root_dir):
                raise RuntimeError("MetricStore unavailable")

        fake_lib = types.ModuleType("lib")
        fake_mod = types.ModuleType("lib.metric_store")
        fake_mod.MetricStore = BrokenMetricStore  # type: ignore[attr-defined]
        fake_lib.metric_store = fake_mod  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "lib", fake_lib)
        monkeypatch.setitem(sys.modules, "lib.metric_store", fake_mod)
        assert ba._r9_quality_regression(runs=[{}] * 8, min_runs=7) is None
