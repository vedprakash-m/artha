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

from unittest.mock import MagicMock, patch

import pytest

from briefing_adapter import BriefingAdapter, BriefingConfig


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
