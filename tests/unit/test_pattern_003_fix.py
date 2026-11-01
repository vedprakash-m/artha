"""
tests/unit/test_pattern_003_fix.py — Tests for PAT-003/PAT-003b and is_null operator

Coverage:
  - PAT-003 fires for stale active goal (last_progress > 14 days)
  - PAT-003 does NOT fire for parked goal (status != active)
  - PAT-003 does NOT fire for recently-updated goal
  - PAT-003b fires for active goal with no progress AND no next_action AND created >30 days ago
  - PAT-003b does NOT fire when next_action is present
  - is_null operator: True for None, "", "null", missing key
  - is_null operator: False for non-null value
  - is_null: false: True when value is non-null
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pattern_engine import PatternEngine  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _goals_file(goals: list[dict], tmp_path: Path) -> Path:
    """Write a minimal state/goals.md with given goals list.
    Creates tmp_path/state/goals.md so PatternEngine (root_dir=tmp_path) can find it.
    """
    content = {
        "schema_version": "2.0",
        "domain": "goals",
        "last_updated": str(date.today()),
        "goals": goals,
    }
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    p = state_dir / "goals.md"
    p.write_text("---\n" + yaml.dump(content) + "---\n")
    return p


def _pat003_pattern_yaml() -> dict:
    """PAT-003 using the v2 pattern schema (source_path + condition + output_signal)."""
    return {
        "id": "PAT-003",
        "description": "Active goal overdue for progress update (>14 days)",
        "enabled": True,
        "cooldown_hours": 72,
        "source_file": "state/goals.md",
        "source_path": "goals",
        "condition": {
            "all_of": [
                {"field": "status", "eq": "active"},
                {"field": "last_progress", "stale_days": 14},
            ]
        },
        "output_signal": {
            "signal_type": "goal_stale",
            "domain": "goals",
            "urgency": 1,
            "impact": 2,
            "entity_field": "title",
            "metadata": {},
        },
    }


def _pat003b_pattern_yaml() -> dict:
    """PAT-003b auto-park candidate (v2 schema)."""
    return {
        "id": "PAT-003b",
        "description": "Active goal has no progress or next_action >30d — auto-park candidate",
        "enabled": True,
        "cooldown_hours": 168,
        "source_file": "state/goals.md",
        "source_path": "goals",
        "condition": {
            "all_of": [
                {"field": "status", "eq": "active"},
                {"field": "last_progress", "is_null": True},
                {"field": "next_action", "is_null": True},
                {"field": "created", "stale_days": 30},
            ]
        },
        "output_signal": {
            "signal_type": "goal_autopark_candidate",
            "domain": "goals",
            "urgency": 2,
            "impact": 2,
            "entity_field": "title",
            "metadata": {},
        },
    }


def _make_engine(patterns: list[dict], goals_file: Path, tmp_path: Path) -> PatternEngine:
    """Build a PatternEngine with given patterns rooted at tmp_path."""
    patterns_path = tmp_path / "config" / "patterns.yaml"
    patterns_path.parent.mkdir(exist_ok=True)
    patterns_path.write_text(yaml.dump({"patterns": patterns}))
    # PatternEngine looks for state files under root_dir/state/
    return PatternEngine(
        patterns_file=patterns_path,
        root_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# PAT-003 tests
# ---------------------------------------------------------------------------

class TestPAT003:
    """PAT-003: goal_stale fires for active goals with last_progress > 14 days."""

    def test_fires_for_stale_active_goal(self, tmp_path):
        stale_date = str(date.today() - timedelta(days=20))
        goals = [
            {
                "id": "G-001",
                "title": "Summit Mailbox Peak",
                "type": "milestone",
                "status": "active",
                "last_progress": stale_date,
                "created": "2026-01-01",
            }
        ]
        f = _goals_file(goals, tmp_path)
        engine = _make_engine([_pat003_pattern_yaml()], f, tmp_path)
        signals = engine.evaluate()
        assert any(s.signal_type == "goal_stale" for s in signals), (
            "PAT-003 should fire for stale active goal"
        )

    def test_does_not_fire_for_parked_goal(self, tmp_path):
        stale_date = str(date.today() - timedelta(days=20))
        goals = [
            {
                "id": "G-003",
                "title": "Azure AI Cert",
                "type": "milestone",
                "status": "parked",
                "last_progress": stale_date,
                "created": "2026-01-01",
            }
        ]
        f = _goals_file(goals, tmp_path)
        engine = _make_engine([_pat003_pattern_yaml()], f, tmp_path)
        signals = engine.evaluate()
        assert not any(s.signal_type == "goal_stale" for s in signals), (
            "PAT-003 must NOT fire for parked goal"
        )

    def test_does_not_fire_for_recent_progress(self, tmp_path):
        recent_date = str(date.today() - timedelta(days=5))
        goals = [
            {
                "id": "G-002",
                "title": "Lose 40 lbs",
                "type": "outcome",
                "status": "active",
                "last_progress": recent_date,
                "created": "2026-01-01",
            }
        ]
        f = _goals_file(goals, tmp_path)
        engine = _make_engine([_pat003_pattern_yaml()], f, tmp_path)
        signals = engine.evaluate()
        assert not any(s.signal_type == "goal_stale" for s in signals), (
            "PAT-003 must NOT fire when last_progress is recent"
        )

    def test_fires_at_exactly_15_days(self, tmp_path):
        edge_date = str(date.today() - timedelta(days=15))
        goals = [
            {
                "id": "G-001",
                "title": "Goal",
                "type": "milestone",
                "status": "active",
                "last_progress": edge_date,
                "created": "2026-01-01",
            }
        ]
        f = _goals_file(goals, tmp_path)
        engine = _make_engine([_pat003_pattern_yaml()], f, tmp_path)
        signals = engine.evaluate()
        assert any(s.signal_type == "goal_stale" for s in signals), (
            "PAT-003 should fire at exactly 15 days"
        )


# ---------------------------------------------------------------------------
# PAT-003b tests
# ---------------------------------------------------------------------------

class TestPAT003b:
    """PAT-003b: auto-park candidate fires when active + no progress + no next_action + >30d."""

    def test_fires_for_autopark_candidate(self, tmp_path):
        old_date = str(date.today() - timedelta(days=40))
        goals = [
            {
                "id": "G-001",
                "title": "Abandoned Goal",
                "type": "milestone",
                "status": "active",
                "last_progress": None,
                "next_action": None,
                "created": old_date,
            }
        ]
        f = _goals_file(goals, tmp_path)
        engine = _make_engine([_pat003b_pattern_yaml()], f, tmp_path)
        signals = engine.evaluate()
        assert any(s.signal_type == "goal_autopark_candidate" for s in signals), (
            "PAT-003b should fire for auto-park candidate"
        )

    def test_does_not_fire_when_next_action_present(self, tmp_path):
        old_date = str(date.today() - timedelta(days=40))
        goals = [
            {
                "id": "G-001",
                "title": "Goal With Action",
                "type": "milestone",
                "status": "active",
                "last_progress": None,
                "next_action": "Register for hike",
                "created": old_date,
            }
        ]
        f = _goals_file(goals, tmp_path)
        engine = _make_engine([_pat003b_pattern_yaml()], f, tmp_path)
        signals = engine.evaluate()
        assert not any(s.signal_type == "goal_autopark_candidate" for s in signals), (
            "PAT-003b must NOT fire when next_action is present"
        )

    def test_does_not_fire_for_new_goal(self, tmp_path):
        recent_date = str(date.today() - timedelta(days=5))
        goals = [
            {
                "id": "G-001",
                "title": "New Goal",
                "type": "milestone",
                "status": "active",
                "last_progress": None,
                "next_action": None,
                "created": recent_date,
            }
        ]
        f = _goals_file(goals, tmp_path)
        engine = _make_engine([_pat003b_pattern_yaml()], f, tmp_path)
        signals = engine.evaluate()
        assert not any(s.signal_type == "goal_autopark_candidate" for s in signals), (
            "PAT-003b must NOT fire for newly created goal (<30 days)"
        )


# ---------------------------------------------------------------------------
# is_null operator tests
# ---------------------------------------------------------------------------

class TestIsNullOperator:
    """Direct tests for _evaluate_operator is_null branch (module-level function)."""

    def _eval(self, value, want_null: bool) -> bool:
        from pattern_engine import _evaluate_operator
        # condition needs a "field" key; doc provides the value at that field
        condition = {"field": "last_progress", "is_null": want_null}
        return _evaluate_operator(condition, {"last_progress": value})

    def test_none_is_null(self):
        assert self._eval(None, True)

    def test_empty_string_is_null(self):
        assert self._eval("", True)

    def test_string_null_is_null(self):
        assert self._eval("null", True)

    def test_actual_value_is_not_null(self):
        assert not self._eval("Register for hike", True)

    def test_non_null_value_passes_is_null_false(self):
        assert self._eval("2026-03-15", False)

    def test_none_fails_is_null_false(self):
        assert not self._eval(None, False)
