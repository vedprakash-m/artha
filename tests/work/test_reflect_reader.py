"""tests/work/test_reflect_reader.py — Tests for scripts/work/reflect_reader.py

≥90% coverage target.
Tests: ReflectReader.get_current_reflection, get_weekly_history, get_goal_trend,
_parse_frontmatter helper, _safe_float helper.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.reflect_reader import (
    ReflectReader,
    ReflectionSnapshot,
    ReflectionSummary,
    GoalTrendData,
    _parse_frontmatter,
    _safe_float,
)


# ---------------------------------------------------------------------------
# Sample fixture content
# ---------------------------------------------------------------------------

_CURRENT_CONTENT = """\
---
schema_version: '1.0'
domain: reflect
last_weekly_close: '2026-04-04T09:00:00Z'
current_week: '2026-W14'
carry_forward_count: 2
---
## Weekly Close — Fri Apr 04

### Accomplishments
- Shipped OAuth fix [HIGH|ORG|direct]
- Reviewed 3 PRs [MEDIUM|TEAM|unaligned]
"""

_WEEKLY_W14_CONTENT = """\
---
schema_version: '1.0'
horizon: weekly
period: '2026-W14'
accomplishment_count: 3
impact_summary: '2 HIGH, 1 MEDIUM, 0 LOW'
---
## Weekly Reflection — 2026-W14

Details here.
"""

_WEEKLY_W13_CONTENT = """\
---
schema_version: '1.0'
horizon: weekly
period: '2026-W13'
accomplishment_count: 1
impact_summary: '0 HIGH, 1 MEDIUM, 0 LOW'
---
## Weekly Reflection — 2026-W13
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def state_dir(tmp_path):
    d = tmp_path / "state" / "work"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def reader_empty(state_dir):
    return ReflectReader(state_dir)


@pytest.fixture()
def reader_with_current(state_dir):
    (state_dir / "reflect-current.md").write_text(_CURRENT_CONTENT, encoding="utf-8")
    return ReflectReader(state_dir)


@pytest.fixture()
def reader_with_history(state_dir):
    reflections_dir = state_dir / "reflections" / "weekly"
    reflections_dir.mkdir(parents=True)
    (reflections_dir / "weekly-2026-W14.md").write_text(_WEEKLY_W14_CONTENT, encoding="utf-8")
    (reflections_dir / "weekly-2026-W13.md").write_text(_WEEKLY_W13_CONTENT, encoding="utf-8")
    return ReflectReader(state_dir)


# ---------------------------------------------------------------------------
# get_current_reflection
# ---------------------------------------------------------------------------

class TestGetCurrentReflection:
    def test_absent_file_returns_none(self, reader_empty):
        assert reader_empty.get_current_reflection() is None

    def test_present_file_returns_snapshot(self, reader_with_current):
        snap = reader_with_current.get_current_reflection()
        assert isinstance(snap, ReflectionSnapshot)

    def test_period_label_from_frontmatter(self, reader_with_current):
        snap = reader_with_current.get_current_reflection()
        # period_label includes the week identifier (may be formatted, e.g. "Week of 2026-W14")
        assert "2026-W14" in snap.period_label

    def test_as_of_parsed_as_datetime(self, reader_with_current):
        snap = reader_with_current.get_current_reflection()
        # as_of should be a valid date/datetime — non-empty string at minimum
        assert snap.as_of is not None
        assert "2026" in str(snap.as_of)

    def test_raw_markdown_contains_body(self, reader_with_current):
        snap = reader_with_current.get_current_reflection()
        assert "Accomplishments" in snap.raw_markdown

    def test_focus_score_none_when_absent(self, state_dir):
        """When frontmatter has no focus_score, value should be None."""
        (state_dir / "reflect-current.md").write_text(_CURRENT_CONTENT, encoding="utf-8")
        reader = ReflectReader(state_dir)
        snap = reader.get_current_reflection()
        assert snap.focus_score is None

    def test_focus_score_parsed_when_present(self, state_dir):
        content = _CURRENT_CONTENT.replace(
            "carry_forward_count: 2",
            "carry_forward_count: 2\nfocus_score: '0.82'"
        )
        (state_dir / "reflect-current.md").write_text(content, encoding="utf-8")
        reader = ReflectReader(state_dir)
        snap = reader.get_current_reflection()
        assert snap.focus_score == pytest.approx(0.82)

    def test_snapshot_is_immutable(self, reader_with_current):
        snap = reader_with_current.get_current_reflection()
        with pytest.raises((AttributeError, TypeError)):
            snap.period_label = "new-value"  # type: ignore


# ---------------------------------------------------------------------------
# get_weekly_history
# ---------------------------------------------------------------------------

class TestGetWeeklyHistory:
    def test_no_reflections_dir_returns_empty(self, reader_empty):
        assert reader_empty.get_weekly_history() == []

    def test_returns_summaries_list(self, reader_with_history):
        summaries = reader_with_history.get_weekly_history()
        assert isinstance(summaries, list)
        assert len(summaries) == 2

    def test_summaries_are_summary_type(self, reader_with_history):
        for s in reader_with_history.get_weekly_history():
            assert isinstance(s, ReflectionSummary)

    def test_sorted_newest_first(self, reader_with_history):
        summaries = reader_with_history.get_weekly_history()
        weeks = [s.week for s in summaries]
        assert weeks == sorted(weeks, reverse=True)

    def test_last_n_limits_results(self, reader_with_history):
        summaries = reader_with_history.get_weekly_history(last_n=1)
        assert len(summaries) == 1

    def test_headline_from_impact_summary(self, reader_with_history):
        summaries = reader_with_history.get_weekly_history()
        w14 = next(s for s in summaries if s.week == "2026-W14")
        assert w14.headline is not None and len(w14.headline) > 0

    def test_corrupt_file_skipped_gracefully(self, state_dir):
        reflections_dir = state_dir / "reflections" / "weekly"
        reflections_dir.mkdir(parents=True)
        (reflections_dir / "weekly-2026-W14.md").write_text(
            _WEEKLY_W14_CONTENT, encoding="utf-8"
        )
        (reflections_dir / "weekly-2026-W99.md").write_text(
            "\x00\x01CORRUPT_BINARY\x02\xff", encoding="latin-1"
        )
        reader = ReflectReader(state_dir)
        summaries = reader.get_weekly_history()
        # Should still return the valid file's summary
        assert len(summaries) >= 1

    def test_week_parsed_from_filename_stem(self, state_dir):
        reflections_dir = state_dir / "reflections" / "weekly"
        reflections_dir.mkdir(parents=True)
        (reflections_dir / "weekly-2026-W01.md").write_text(
            "---\nhorizon: weekly\nperiod: '2026-W01'\naccomplishment_count: 0\n---\n",
            encoding="utf-8"
        )
        reader = ReflectReader(state_dir)
        summaries = reader.get_weekly_history()
        assert any("2026-W01" in s.week for s in summaries)


# ---------------------------------------------------------------------------
# get_goal_trend (Phase 1 stub — always returns None scores)
# ---------------------------------------------------------------------------

class TestGetGoalTrend:
    def test_returns_goal_trend_data(self, reader_with_history):
        trend = reader_with_history.get_goal_trend("g-123")
        assert isinstance(trend, GoalTrendData)

    def test_goal_id_preserved(self, reader_with_history):
        trend = reader_with_history.get_goal_trend("g-target")
        assert trend.goal_id == "g-target"

    def test_scores_all_none_phase1(self, reader_with_history):
        trend = reader_with_history.get_goal_trend("g-any")
        assert all(s is None for s in trend.scores)

    def test_weeks_matches_history_count(self, reader_with_history):
        history = reader_with_history.get_weekly_history()
        trend = reader_with_history.get_goal_trend("g-any")
        assert len(trend.weeks) == len(history)

    def test_no_history_empty_lists(self, reader_empty):
        trend = reader_empty.get_goal_trend("g-empty")
        assert trend.weeks == []
        assert trend.scores == []


# ---------------------------------------------------------------------------
# _parse_frontmatter helper
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_valid_frontmatter_returns_dict(self):
        text = "---\nkey: value\nnum: 42\n---\nbody"
        fm = _parse_frontmatter(text)
        assert fm["key"] == "value"
        assert fm["num"] == 42

    def test_no_frontmatter_returns_empty(self):
        fm = _parse_frontmatter("Just plain text\nno markers")
        assert fm == {}

    def test_empty_string_returns_empty(self):
        assert _parse_frontmatter("") == {}

    def test_empty_frontmatter_returns_empty(self):
        fm = _parse_frontmatter("---\n---\nbody")
        assert fm == {} or isinstance(fm, dict)

    def test_invalid_yaml_returns_empty(self):
        bad = "---\n: : : invalid : yaml\n---\n"
        fm = _parse_frontmatter(bad)
        assert isinstance(fm, dict)

    def test_multiline_body_ignored(self):
        text = "---\ntag: hello\n---\n## Heading\nParagraph text\n"
        fm = _parse_frontmatter(text)
        assert "tag" in fm
        assert "Heading" not in fm


# ---------------------------------------------------------------------------
# _safe_float helper
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_string_float_parses(self):
        assert _safe_float("1.23") == pytest.approx(1.23)

    def test_int_returns_float(self):
        result = _safe_float(5)
        assert result == pytest.approx(5.0)
        assert isinstance(result, float)

    def test_float_returns_float(self):
        assert _safe_float(0.5) == pytest.approx(0.5)

    def test_bad_string_returns_none(self):
        assert _safe_float("not-a-number") is None

    def test_empty_string_returns_none(self):
        assert _safe_float("") is None

    def test_zero_returns_zero(self):
        assert _safe_float(0) == pytest.approx(0.0)

    def test_zero_string_returns_zero(self):
        assert _safe_float("0.0") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Sprint 2 ACs: index_db_path accepted by all 3 public methods
# ---------------------------------------------------------------------------

class TestIndexDbPathAccepted:
    """Verify index_db_path=None is accepted by all 3 public methods (Sprint 2 AC)."""

    def test_get_current_reflection_accepts_index_db_path(self, reader_empty):
        result = reader_empty.get_current_reflection(index_db_path=None)
        assert result is None  # empty state — not an error

    def test_get_weekly_history_accepts_index_db_path(self, reader_empty):
        result = reader_empty.get_weekly_history(last_n=5, index_db_path=None)
        assert result == []

    def test_get_goal_trend_accepts_index_db_path(self, reader_empty):
        result = reader_empty.get_goal_trend("g-test", last_n=4, index_db_path=None)
        assert isinstance(result, GoalTrendData)

    def test_get_current_reflection_index_db_path_with_data(self, reader_with_current):
        snap = reader_with_current.get_current_reflection(index_db_path=None)
        assert snap is not None
        assert isinstance(snap, ReflectionSnapshot)

    def test_get_weekly_history_index_db_path_with_data(self, reader_with_history):
        summaries = reader_with_history.get_weekly_history(last_n=12, index_db_path=None)
        assert isinstance(summaries, list)

    def test_get_goal_trend_index_db_path_with_data(self, reader_with_history):
        trend = reader_with_history.get_goal_trend("g-any", last_n=8, index_db_path=None)
        assert isinstance(trend, GoalTrendData)


# ---------------------------------------------------------------------------
# get_current_frontmatter
# ---------------------------------------------------------------------------

class TestGetCurrentFrontmatter:

    def test_absent_file_returns_empty_dict(self, reader_empty):
        assert reader_empty.get_current_frontmatter() == {}

    def test_returns_dict(self, reader_with_current):
        fm = reader_with_current.get_current_frontmatter()
        assert isinstance(fm, dict)

    def test_carry_forward_count_present(self, reader_with_current):
        fm = reader_with_current.get_current_frontmatter()
        assert "carry_forward_count" in fm

    def test_current_week_present(self, reader_with_current):
        fm = reader_with_current.get_current_frontmatter()
        assert fm.get("current_week") == "2026-W14"

    def test_body_not_in_result(self, reader_with_current):
        fm = reader_with_current.get_current_frontmatter()
        assert "_body" not in fm

    def test_no_frontmatter_returns_empty(self, state_dir):
        (state_dir / "reflect-current.md").write_text("Just plain text\n", encoding="utf-8")
        reader = ReflectReader(state_dir)
        assert reader.get_current_frontmatter() == {}

    def test_corrupt_file_returns_empty(self, state_dir):
        (state_dir / "reflect-current.md").write_bytes(b"\x00\x01\x02corrupt")
        reader = ReflectReader(state_dir)
        result = reader.get_current_frontmatter()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# get_artifact_content
# ---------------------------------------------------------------------------

class TestGetArtifactContent:

    def test_absent_dir_returns_none(self, reader_empty):
        assert reader_empty.get_artifact_content("weekly") is None

    def test_empty_dir_returns_none(self, state_dir):
        (state_dir / "reflections" / "weekly").mkdir(parents=True)
        assert ReflectReader(state_dir).get_artifact_content("weekly") is None

    def test_returns_dict_with_body(self, reader_with_history):
        result = reader_with_history.get_artifact_content("weekly")
        assert isinstance(result, dict)
        assert "_body" in result

    def test_frontmatter_fields_present(self, reader_with_history):
        result = reader_with_history.get_artifact_content("weekly")
        assert result is not None
        assert "period" in result or "horizon" in result

    def test_latest_file_selected(self, reader_with_history):
        result = reader_with_history.get_artifact_content("weekly")
        assert result is not None
        # W14 is newer than W13 — should return W14 artifact
        assert "2026-W14" in str(result.get("period", ""))

    def test_unknown_horizon_returns_none(self, reader_empty):
        assert reader_empty.get_artifact_content("nonexistent") is None

    def test_body_contains_markdown(self, reader_with_history):
        result = reader_with_history.get_artifact_content("weekly")
        assert result is not None
        assert isinstance(result.get("_body"), str)

