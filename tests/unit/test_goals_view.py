"""tests/unit/test_goals_view.py — Tests for scripts/goals_view.py

Covers:
- _format_leading: ReflectReader wiring (absent/present)
- --leading flag routes to _format_leading
- _format_leading handles scored and unscored trends
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

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
        monkeypatch.setattr(gv, "_WORK_GOALS_FILE", goals_file)
        monkeypatch.setattr(gv, "_WORK_STATE_DIR", tmp_path / "state" / "work")
        with patch.object(gv, "_REFLECT_READER_AVAILABLE", False), \
             patch("sys.argv", ["goals_view.py", "--leading", "--scope", "work"]):
            exit_code = gv.main()
        assert exit_code == 0

    def test_no_leading_flag_uses_format(self, tmp_path, monkeypatch):
        goals_file = self._make_goals_md(tmp_path)
        monkeypatch.setattr(gv, "_WORK_GOALS_FILE", goals_file)
        with patch("sys.argv", ["goals_view.py", "--format", "flash", "--scope", "work"]):
            exit_code = gv.main()
        assert exit_code == 0

    @pytest.mark.parametrize("scope", ["personal", "all"])
    def test_personal_goal_scopes_denied_before_file_read(self, monkeypatch, capsys, scope):
        monkeypatch.setattr(sys, "argv", ["goals_view.py", "--scope", scope])
        monkeypatch.setattr(gv, "_read_goals", lambda: pytest.fail("personal State was read"))
        assert gv.main() == 1
        assert "trusted personal-scope binding" in capsys.readouterr().err


_V2_GOALS_AND_SPRINTS = textwrap.dedent("""\
    ---
    schema_version: '2.0'
    domain: goals
    last_updated: '2026-03-28'
    goals:
    - id: G-001
      title: Summit Mailbox Peak
      type: milestone
      status: active
      last_progress: '2026-03-15'
      created: '2026-01-01'
      leading_indicators: []
    - id: G-002
      title: Lose 40 lbs
      type: outcome
      status: active
      last_progress: '2026-03-15'
      created: '2026-01-01'
      metric:
        current: 183.0
        target: 160
        unit: lb
        direction: down
    - id: G-003
      title: Azure AI Cert
      type: milestone
      status: parked
      last_progress: null
      parked_reason: Schedule stabilizes post ProgramX
      created: '2026-01-01'
    sprints:
    - id: SPR-001
      name: Hike Prep Sprint
      linked_goal: Summit Mailbox Peak
      target: Register and prepare gear
      sprint_start: '2026-03-28'
      sprint_end: '2026-04-27'
      duration_days: 30
      status: active
      progress_pct: 10
      calibrated_at_14d: false
      outcome: ''
    ---

    # Goals & Progress

    | Goal | Status |
    |------|--------|
    | Summit Mailbox Peak | active |
""")

_V1_SPRINTS_ONLY = textwrap.dedent("""\
    ---
    schema_version: '1.0'
    domain: goals
    sprints:
    - id: SPR-001
      name: Old Sprint
      status: active
    ---
    | Goal | Status |
    |------|--------|
""")

_WORK_GOALS_V2 = textwrap.dedent("""\
    ---
    schema_version: '2.0'
    domain: work-goals
    last_updated: '2026-03-28'
    goals:
    - id: G-W-001
      title: ProgramX Ramp P0/P1 Delivery
      type: milestone
      status: active
      last_progress: '2026-03-25'
      created: '2025-10-01'
    ---
    # Work Goals
""")


class TestGoalsSchemaV2AndSprintCompat:
    """goals_view.py schema v2.0 parsing, formatting, scope tags, and v1 sprint compat."""

    def test_parse_goals_v2_tags_and_sprints_coexistence(self):
        goals = gv._parse_goals_yaml(_V2_GOALS_AND_SPRINTS)
        assert [g["id"] for g in goals] == ["G-001", "G-002", "G-003"]
        g2 = next(g for g in goals if g["id"] == "G-002")
        assert g2["metric"]["current"] == 183.0
        assert not any("_tag" in g for g in goals)
        assert len(gv._parse_goals_index(_V2_GOALS_AND_SPRINTS)) == 3
        assert gv._parse_goals_yaml(_V1_SPRINTS_ONLY) == []

        meta = gv._extract_frontmatter(_V2_GOALS_AND_SPRINTS)
        assert meta.get("schema_version") == "2.0" and meta.get("domain") == "goals"

        fm = yaml.safe_load(re.match(r"---\s*\n(.*?)\n---", _V2_GOALS_AND_SPRINTS, re.DOTALL).group(1))
        assert fm["sprints"][0]["progress_pct"] == 10 and fm["sprints"][0]["calibrated_at_14d"] is False

    def test_format_standard_and_work_tagging(self):
        goals = gv._parse_goals_yaml(_V2_GOALS_AND_SPRINTS)
        out = gv._format_standard(goals, {"schema_version": "2.0", "domain": "goals"})
        assert "G-001" in out and "G-002" in out and "183" in out and "parked" in out.lower()

        work_goals = gv._parse_goals_yaml(_WORK_GOALS_V2, tag="[work]")
        assert len(work_goals) == 1 and work_goals[0]["_tag"] == "[work]"
        merged = goals + work_goals
        assert len(merged) == 4 and len([g for g in merged if g.get("_tag") == "[work]"]) == 1
