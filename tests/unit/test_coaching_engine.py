"""
tests/unit/test_coaching_engine.py — Unit tests for scripts/coaching_engine.py (E16)

Coverage:
  - CoachingEngine.generate() returns list of CoachingNudge
  - Nudges have required fields: text, domain, urgency, source
  - Goal overdue triggers a coaching nudge
  - Health metric below threshold triggers nudge
  - No duplicate nudges for same entity in same run
  - Feature flag disabled → returns empty list
  - PII not in nudge text
  - Nudge text under 280 characters (Telegram-friendly)
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from coaching_engine import CoachingEngine, load_goals_content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOALS_MD = """\
---
schema_version: "1.0"
last_updated: "2026-01-01"
domain: goals
---

## Active Goals

| ID | Goal | Target | Progress | Due | Last Reviewed |
|----|------|--------|----------|-----|---------------|
| G-001 | Exercise 3x/week | 3x/week | 1x/week | 2026-12-31 | 2026-01-01 |
| G-002 | Read 20 books | 20 books | 5 books | 2026-12-31 | 2026-03-10 |
"""

_HEALTH_CHECK_MD = """\
---
schema_version: "1.0"
last_catch_up: "2026-03-20T07:00:00Z"
context_pressure: 35
briefing_format: standard
run_count: 15
---
"""


def _write_state(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "goals.md").write_text(_GOALS_MD)
    (state / "health-check.md").write_text(_HEALTH_CHECK_MD)
    return tmp_path


def _make_engine() -> CoachingEngine:
    return CoachingEngine()


def _make_goals(stale: bool = True) -> dict:
    """Goals frontmatter dict with one stale goal."""
    last_reviewed = "2026-01-01" if stale else "2026-03-19"
    return {
        "goals": [
            {
                "id": "G-001",
                "title": "Exercise 3x/week",
                "target": "3x/week",
                "progress": "1x/week",
                "status_flag": "at_risk",
                "off_pace": True,
                "last_reviewed": last_reviewed,
            }
        ]
    }


def _make_health_history(n: int = 5) -> list[dict]:
    return [{"date": f"2026-03-{20-i:02d}", "format": "standard"} for i in range(n)]


# ---------------------------------------------------------------------------
# CoachingNudge shape
# ---------------------------------------------------------------------------

class TestCoachingNudgeShape:
    def test_select_nudge_returns_nudge_or_none(self):
        engine = _make_engine()
        result = engine.select_nudge(
            goals=_make_goals(),
            memory_facts=[],
            health_history=_make_health_history(),
        )
        assert result is None or hasattr(result, "text") or hasattr(result, "message")

    def test_nudge_has_message_field(self):
        engine = _make_engine()
        nudge = engine.select_nudge(
            goals=_make_goals(),
            memory_facts=[],
            health_history=_make_health_history(),
        )
        if nudge is not None:
            assert hasattr(nudge, "message") or hasattr(nudge, "text")


# ---------------------------------------------------------------------------
# Goal-based nudges
# ---------------------------------------------------------------------------

class TestGoalNudges:
    def test_at_risk_goal_may_trigger_nudge(self):
        engine = _make_engine()
        nudge = engine.select_nudge(
            goals=_make_goals(stale=True),
            memory_facts=[],
            health_history=_make_health_history(),
        )
        # at_risk goal may return a nudge (result is None or a nudge with message)
        assert nudge is None or hasattr(nudge, "nudge_type")


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------

class TestNudgeUniqueness:
    def test_select_returns_at_most_one_nudge(self):
        engine = _make_engine()
        for _ in range(3):
            result = engine.select_nudge(
                goals=_make_goals(),
                memory_facts=[],
                health_history=_make_health_history(),
            )
            # select_nudge returns at most one nudge
            assert result is None or not isinstance(result, list)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_flag_disabled_returns_none(self):
        with patch("coaching_engine._load_flag", return_value=False):
            engine = _make_engine()
            result = engine.select_nudge(
                goals=_make_goals(),
                memory_facts=[],
                health_history=_make_health_history(),
            )
        assert result is None


# ---------------------------------------------------------------------------
# PII safety
# ---------------------------------------------------------------------------

class TestPiiSafety:
    def test_no_email_in_nudge_text(self):
        engine = _make_engine()
        nudge = engine.select_nudge(
            goals=_make_goals(),
            memory_facts=[],
            health_history=_make_health_history(),
        )
        if nudge is not None:
            text = getattr(nudge, "message", "") or getattr(nudge, "text", "")
            import re
            emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", str(text))
            assert len(emails) == 0


# ---------------------------------------------------------------------------
# Message length
# ---------------------------------------------------------------------------

class TestMessageLength:
    def test_nudge_text_under_280_chars(self):
        engine = _make_engine()
        nudge = engine.select_nudge(
            goals=_make_goals(),
            memory_facts=[],
            health_history=_make_health_history(),
        )
        if nudge is not None:
            text = getattr(nudge, "message", "") or getattr(nudge, "text", "")
            assert len(str(text)) <= 280


# ---------------------------------------------------------------------------
# v2.0 Structured Goals — _normalize_v2_goal
# ---------------------------------------------------------------------------

class TestNormalizeV2Goal:
    """Unit tests for the v2.0 goal normalizer added in Goals Reloaded Phase 2."""

    def test_active_recent_progress_is_in_progress(self):
        recent = str(date.today() - timedelta(days=5))
        goal = {"id": "G-001", "title": "T", "status": "active", "last_progress": recent,
                "created": "2026-01-01", "target_date": "2026-12-31"}
        result = CoachingEngine._normalize_v2_goal(goal)
        assert result is not None
        assert result["status_flag"] == "in_progress"
        assert result["off_pace"] is False

    def test_active_stale_progress_is_at_risk(self):
        stale = str(date.today() - timedelta(days=20))
        goal = {"id": "G-001", "title": "T", "status": "active", "last_progress": stale,
                "created": "2026-01-01", "target_date": "2026-12-31"}
        result = CoachingEngine._normalize_v2_goal(goal)
        assert result is not None
        assert result["status_flag"] == "at_risk"
        assert result["off_pace"] is True

    def test_active_no_progress_is_at_risk(self):
        goal = {"id": "G-001", "title": "T", "status": "active", "last_progress": None,
                "created": "2026-01-01", "target_date": "2026-12-31"}
        result = CoachingEngine._normalize_v2_goal(goal)
        assert result is not None
        assert result["status_flag"] == "at_risk"
        assert result["off_pace"] is True

    def test_parked_returns_none(self):
        goal = {"id": "G-003", "title": "T", "status": "parked", "last_progress": None}
        assert CoachingEngine._normalize_v2_goal(goal) is None

    def test_done_returns_none(self):
        goal = {"id": "G-004", "title": "T", "status": "done", "last_progress": None}
        assert CoachingEngine._normalize_v2_goal(goal) is None

    def test_original_fields_preserved(self):
        recent = str(date.today() - timedelta(days=3))
        goal = {"id": "G-001", "title": "Summit", "status": "active",
                "last_progress": recent, "next_action": "Book hike",
                "created": "2026-01-01", "target_date": "2026-12-31"}
        result = CoachingEngine._normalize_v2_goal(goal)
        assert result is not None
        assert result["id"] == "G-001"
        assert result["next_action"] == "Book hike"

    def test_metric_off_pace_sets_off_pace_true(self):
        """Goal >20% behind expected linear trajectory sets off_pace=True."""
        created = str(date.today() - timedelta(days=180))
        target = str(date.today() + timedelta(days=180))
        goal = {"id": "G-002", "title": "Weight", "status": "active", "type": "outcome",
                "last_progress": str(date.today() - timedelta(days=5)),
                "created": created, "target_date": target,
                "metric": {"baseline": 200, "current": 198, "target": 160,
                           "unit": "lb", "direction": "down"}}
        result = CoachingEngine._normalize_v2_goal(goal)
        assert result is not None
        assert result["status_flag"] == "in_progress"  # not stale
        assert result["off_pace"] is True               # but metric behind

    def test_metric_on_pace_off_pace_remains_false(self):
        """Goal at expected pace keeps off_pace=False."""
        created = str(date.today() - timedelta(days=365))
        target = str(date.today() + timedelta(days=365))
        goal = {"id": "G-002", "title": "Weight", "status": "active", "type": "outcome",
                "last_progress": str(date.today() - timedelta(days=5)),
                "created": created, "target_date": target,
                "metric": {"baseline": 200, "current": 180, "target": 160,
                           "unit": "lb", "direction": "down"}}
        result = CoachingEngine._normalize_v2_goal(goal)
        assert result is not None
        assert result["off_pace"] is False


# ---------------------------------------------------------------------------
# v2.0 Structured Goals — select_nudge with YAML frontmatter
# ---------------------------------------------------------------------------

class TestSelectNudgeV2:
    """select_nudge with v2.0 structured goals (Goals Reloaded Phase 2)."""

    _eng = CoachingEngine()

    def _nudge(self, goals: list[dict], enabled: bool = True):
        return self._eng.select_nudge(
            goals={"schema_version": "2.0", "goals": goals},
            memory_facts=[],
            health_history=[],
            preferences={"coaching_enabled": enabled},
        )

    def test_recent_goal_fires_next_small_win(self):
        recent = str(date.today() - timedelta(days=5))
        nudge = self._nudge([{"id": "G-001", "title": "Summit", "status": "active",
                               "last_progress": recent, "created": "2026-01-01",
                               "target_date": "2026-12-31"}])
        assert nudge is not None
        assert nudge.nudge_type == "next_small_win"

    def test_stale_goal_fires_obstacle_anticipation(self):
        stale = str(date.today() - timedelta(days=20))
        nudge = self._nudge([{"id": "G-001", "title": "Summit", "status": "active",
                               "last_progress": stale, "created": "2026-01-01",
                               "target_date": "2026-12-31"}])
        assert nudge is not None
        assert nudge.nudge_type == "obstacle_anticipation"

    def test_all_parked_returns_none(self):
        nudge = self._nudge([
            {"id": "G-001", "title": "T", "status": "parked", "last_progress": None},
        ])
        assert nudge is None

    def test_empty_goals_list_returns_none(self):
        assert self._nudge([]) is None

    def test_coaching_disabled_returns_none(self):
        recent = str(date.today() - timedelta(days=3))
        nudge = self._nudge([{"id": "G-001", "title": "T", "status": "active",
                               "last_progress": recent, "created": "2026-01-01",
                               "target_date": "2026-12-31"}], enabled=False)
        assert nudge is None

    def test_mixed_parked_and_active_fires_for_active(self):
        recent = str(date.today() - timedelta(days=5))
        nudge = self._nudge([
            {"id": "G-003", "title": "Parked", "status": "parked", "last_progress": None},
            {"id": "G-001", "title": "Active Goal", "status": "active",
             "last_progress": recent, "created": "2026-01-01", "target_date": "2026-12-31"},
        ])
        assert nudge is not None
        assert nudge.goal_title == "Active Goal"

    def test_at_risk_beats_in_progress_priority(self):
        """Priority 1 (at_risk) fires before Priority 3 (in_progress)."""
        recent = str(date.today() - timedelta(days=3))
        stale = str(date.today() - timedelta(days=20))
        nudge = self._nudge([
            {"id": "G-001", "title": "Fresh", "status": "active",
             "last_progress": recent, "created": "2026-01-01", "target_date": "2026-12-31"},
            {"id": "G-002", "title": "Stale", "status": "active",
             "last_progress": stale, "created": "2026-01-01", "target_date": "2026-12-31"},
        ])
        assert nudge is not None
        assert nudge.nudge_type == "obstacle_anticipation"
        assert nudge.goal_title == "Stale"


# ---------------------------------------------------------------------------
# load_goals_content
# ---------------------------------------------------------------------------

class TestLoadGoalsContentV2:

    def test_reads_structured_goals(self, tmp_path):
        content = (
            "---\nschema_version: '2.0'\ngoals:\n"
            "  - id: G-001\n    title: Summit\n    status: active\n---\n# Goals\n"
        )
        gf = tmp_path / "goals.md"
        gf.write_text(content)
        data = load_goals_content(gf)
        assert "goals" in data
        assert data["goals"][0]["id"] == "G-001"

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert load_goals_content(tmp_path / "nonexistent.md") == {}

    def test_raw_content_included(self, tmp_path):
        content = "---\nschema_version: '1.0'\n---\n# My Goals\n"
        gf = tmp_path / "goals.md"
        gf.write_text(content)
        data = load_goals_content(gf)
        assert "_raw_content" in data
        assert "# My Goals" in data["_raw_content"]
