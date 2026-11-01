"""tests/unit/test_scorecard_view.py — Tests for scripts/scorecard_view.py

Covers:
- _fetch_reflection_history (graceful absent/present)
- _compute_reflection_score (empty / unscored / scored)
- 6th Work Reflection dimension present in main() output
- _format_digest with reflect_history (trend table rendered)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import scorecard_view as sv


# ---------------------------------------------------------------------------
# _compute_reflection_score
# ---------------------------------------------------------------------------

class TestComputeReflectionScore:

    def test_empty_history_returns_low_score(self):
        score, note = sv._compute_reflection_score([])
        assert score == 2.0
        assert "no reflection" in note

    def test_unscored_history_returns_mid_score(self):
        snap = MagicMock()
        snap.focus_score = None
        score, note = sv._compute_reflection_score([snap, snap])
        assert score == 3.0
        assert "unscored" in note

    def test_full_score_history_returns_high(self):
        snap = MagicMock()
        snap.focus_score = 1.0
        score, note = sv._compute_reflection_score([snap, snap, snap])
        assert score == pytest.approx(5.0)

    def test_zero_score_history_returns_low(self):
        snap = MagicMock()
        snap.focus_score = 0.0
        score, note = sv._compute_reflection_score([snap])
        assert score == pytest.approx(1.0)

    def test_partial_score_clamped(self):
        snap = MagicMock()
        snap.focus_score = 0.5
        score, note = sv._compute_reflection_score([snap])
        assert 1.0 <= score <= 5.0

    def test_note_includes_week_count(self):
        snap = MagicMock()
        snap.focus_score = 0.8
        _, note = sv._compute_reflection_score([snap, snap])
        assert "2" in note


# ---------------------------------------------------------------------------
# _fetch_reflection_history
# ---------------------------------------------------------------------------

class TestFetchReflectionHistory:

    def test_returns_list_when_unavailable(self):
        with patch.object(sv, "_REFLECT_READER_AVAILABLE", False):
            result = sv._fetch_reflection_history(n=4)
        assert result == []

    def test_returns_list_from_reader(self):
        mock_reader = MagicMock()
        mock_reader.return_value.get_weekly_history.return_value = [MagicMock()]
        with patch.object(sv, "_REFLECT_READER_AVAILABLE", True), \
             patch.object(sv, "_ReflectReader", mock_reader):
            result = sv._fetch_reflection_history(n=4)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_exception_returns_empty_list(self):
        mock_reader = MagicMock()
        mock_reader.side_effect = RuntimeError("boom")
        with patch.object(sv, "_REFLECT_READER_AVAILABLE", True), \
             patch.object(sv, "_ReflectReader", mock_reader):
            result = sv._fetch_reflection_history(n=4)
        assert result == []


# ---------------------------------------------------------------------------
# _format_flash / _format_standard — 6th dimension included
# ---------------------------------------------------------------------------

_SAMPLE_DIMENSIONS = [
    ("System Health", 4.0, "ok", "health-check.md"),
    ("Goals Progress", 3.0, "2/5 on track", "goals.md"),
    ("Action Backlog", 4.0, "3 open (0 P0)", "open_items.md"),
    ("Physical Health", 3.0, "vault locked", "health-metrics.md"),
    ("Engagement Cadence", 4.0, "3 sessions", "catch-up frequency"),
    ("Work Reflection", 2.0, "no reflection data", "state/work/"),
]


class TestScorecardFormats:

    def test_flash_includes_work_reflection(self):
        out = sv._format_flash(_SAMPLE_DIMENSIONS)
        assert "Work Reflection" in out

    def test_standard_includes_work_reflection(self):
        out = sv._format_standard(_SAMPLE_DIMENSIONS, runs=[], goals=[])
        assert "Work Reflection" in out

    def test_digest_includes_work_reflection(self):
        out = sv._format_digest(_SAMPLE_DIMENSIONS, runs=[], goals=[])
        assert "Work Reflection" in out

    def test_digest_reflection_trend_section(self):
        snap = MagicMock()
        snap.week_key = "2026-W14"
        snap.primary_theme = "Shipped auth"
        snap.carry_forward_count = 2
        snap.focus_score = 0.8
        out = sv._format_digest(
            _SAMPLE_DIMENSIONS, runs=[], goals=[], reflect_history=[snap]
        )
        assert "Work Reflection Trend" in out
        assert "2026-W14" in out
        assert "80%" in out

    def test_digest_no_history_no_trend_section(self):
        out = sv._format_digest(
            _SAMPLE_DIMENSIONS, runs=[], goals=[], reflect_history=[]
        )
        assert "Work Reflection Trend" not in out
