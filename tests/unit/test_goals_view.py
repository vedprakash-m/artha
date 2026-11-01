"""tests/unit/test_goals_view.py — Tests for scripts/goals_view.py

Covers:
- _format_leading: ReflectReader wiring (absent/present)
- --leading flag routes to _format_leading
- _format_leading handles scored and unscored trends
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import goals_view as gv


# ---------------------------------------------------------------------------
# Minimal sample goals list
# ---------------------------------------------------------------------------

_SAMPLE_GOALS = [
    {"id": "G-001", "title": "Ship feature X", "status": "on_track", "priority": "P1",
     "deadline": "", "current_value": "", "target_value": ""},
    {"id": "G-002", "title": "Improve health",  "status": "at_risk",  "priority": "P2",
     "deadline": "", "current_value": "", "target_value": ""},
]

_SAMPLE_META = {"last_updated": "2026-04-04"}


# ---------------------------------------------------------------------------
# _format_leading
# ---------------------------------------------------------------------------

class TestFormatLeading:

    def test_returns_string(self):
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", False):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        assert isinstance(out, str)

    def test_unavailable_reader_shows_warning(self):
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", False):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        assert "ReflectReader not available" in out or "not available" in out.lower()

    def test_heading_present(self):
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", False):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        assert "Leading Indicators" in out

    def test_table_generated_when_available(self):
        mock_trend = MagicMock()
        mock_trend.scores = [None, None, None, None, None, None, None, None]
        mock_reader_instance = MagicMock()
        mock_reader_instance.get_goal_trend.return_value = mock_trend
        mock_reader_cls = MagicMock(return_value=mock_reader_instance)
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", True), \
             patch.object(gv, "_ReflectReader", mock_reader_cls):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        assert "Goal" in out
        assert "Trend" in out

    def test_phase1_label_when_no_scores(self):
        mock_trend = MagicMock()
        mock_trend.scores = [None] * 8
        mock_reader_instance = MagicMock()
        mock_reader_instance.get_goal_trend.return_value = mock_trend
        mock_reader_cls = MagicMock(return_value=mock_reader_instance)
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", True), \
             patch.object(gv, "_ReflectReader", mock_reader_cls):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        assert "Phase 1" in out or "scoring begins" in out

    def test_scored_goals_show_percentage(self):
        mock_trend = MagicMock()
        mock_trend.scores = [0.8, 0.9, 1.0, 0.7, 0.8, 0.9, 1.0, 0.85]
        mock_reader_instance = MagicMock()
        mock_reader_instance.get_goal_trend.return_value = mock_trend
        mock_reader_cls = MagicMock(return_value=mock_reader_instance)
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", True), \
             patch.object(gv, "_ReflectReader", mock_reader_cls):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        # Last score is 85% — should appear in table
        assert "85%" in out

    def test_goal_ids_in_output(self):
        mock_trend = MagicMock()
        mock_trend.scores = [None] * 8
        mock_reader_instance = MagicMock()
        mock_reader_instance.get_goal_trend.return_value = mock_trend
        mock_reader_cls = MagicMock(return_value=mock_reader_instance)
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", True), \
             patch.object(gv, "_ReflectReader", mock_reader_cls):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        assert "G-001" in out
        assert "G-002" in out

    def test_exception_handled_gracefully(self):
        mock_reader_cls = MagicMock(side_effect=RuntimeError("reader exploded"))
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", True), \
             patch.object(gv, "_ReflectReader", mock_reader_cls):
            out = gv._format_leading(_SAMPLE_GOALS, _SAMPLE_META, Path("/tmp"))
        assert isinstance(out, str)
        assert "Error" in out or "error" in out.lower()

    def test_empty_goals_list(self):
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", True):
            out = gv._format_leading([], _SAMPLE_META, Path("/tmp"))
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# main() --leading flag routing
# ---------------------------------------------------------------------------

class TestMainLeadingFlag:

    def _make_goals_md(self, tmp_path: Path) -> Path:
        goals_file = tmp_path / "state" / "goals.md"
        goals_file.parent.mkdir(parents=True)
        goals_file.write_text(
            "---\nlast_updated: '2026-04-04'\n---\n"
            "```yaml\ngoals_index:\n- id: G-001\n  title: Test goal\n"
            "  status: on_track\n  priority: P1\n```\n",
            encoding="utf-8",
        )
        return goals_file

    def test_leading_flag_calls_format_leading(self, tmp_path, monkeypatch):
        goals_file = self._make_goals_md(tmp_path)
        monkeypatch.setattr(gv, "_GOALS_FILE", goals_file)
        monkeypatch.setattr(gv, "_WORK_STATE_DIR", tmp_path / "state" / "work")
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", False), \
             patch("sys.argv", ["goals_view.py", "--leading"]):
            exit_code = gv.main()
        assert exit_code == 0

    def test_no_leading_flag_uses_format(self, tmp_path, monkeypatch):
        goals_file = self._make_goals_md(tmp_path)
        monkeypatch.setattr(gv, "_GOALS_FILE", goals_file)
        with patch("sys.argv", ["goals_view.py", "--format", "flash"]):
            exit_code = gv.main()
        assert exit_code == 0
