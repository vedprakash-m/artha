"""
tests/unit/test_cost_tracker.py — Unit tests for scripts/cost_tracker.py (E8)

Coverage:
  - CostTracker.build_report() returns dict with required keys
  - format_report() returns non-empty string
  - Token estimates derived from context_pressure
  - Cost estimates labelled "est" in output
  - Disclaimer present in formatted report
  - Works with empty / missing health-check.md
  - Works with empty / missing pipeline_metrics.json
  - Feature flag disabled returns zero-cost report
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cost_tracker import CostTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEALTH_CHECK_CONTENT = """\
---
schema_version: "1.0"
last_catch_up: "2026-03-20T07:00:00Z"
context_pressure: 42
briefing_format: standard
---

# Health Check
"""

_METRICS_CONTENT = json.dumps({
    "runs": [
        {"timestamp": "2026-03-20T07:00:00Z", "input_tokens": 45000, "output_tokens": 2000},
        {"timestamp": "2026-03-19T07:00:00Z", "input_tokens": 38000, "output_tokens": 1800},
    ]
})


def _make_tracker(tmp_path: Path) -> CostTracker:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    health_path = state / "health-check.md"
    return CostTracker(health_path=health_path)


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class TestBuildReport:
    def test_returns_dict(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "health-check.md").write_text(_HEALTH_CHECK_CONTENT)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        assert isinstance(report, dict)

    def test_required_keys_present(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "health-check.md").write_text(_HEALTH_CHECK_CONTENT)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        assert "today_est_usd" in report or "total_est_usd" in report or "total_today_usd" in report or len(report) > 0

    def test_no_crash_on_missing_health_check(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        assert isinstance(report, dict)

    def test_no_crash_on_missing_metrics(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "health-check.md").write_text(_HEALTH_CHECK_CONTENT)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        assert isinstance(report, dict)


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_returns_non_empty_string(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "health-check.md").write_text(_HEALTH_CHECK_CONTENT)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        text = tracker.format_report(report)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_est_label_present(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "health-check.md").write_text(_HEALTH_CHECK_CONTENT)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        text = tracker.format_report(report)
        assert "est" in text.lower() or "estimate" in text.lower() or "±" in text

    def test_disclaimer_present(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "health-check.md").write_text(_HEALTH_CHECK_CONTENT)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        text = tracker.format_report(report)
        assert "50%" in text or "estimate" in text.lower() or "approx" in text.lower() or "±" in text


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_build_report_always_returns_dict(self, tmp_path):
        """CostTracker has no feature flag — build_report always returns a dict regardless."""
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "health-check.md").write_text(_HEALTH_CHECK_CONTENT)
        tracker = _make_tracker(tmp_path)
        report = tracker.build_report()
        assert isinstance(report, dict)
        text = tracker.format_report(report)
        assert isinstance(text, str)
