"""
tests/unit/test_goals_writer.py — Tests for scripts/goals_writer.py

Coverage:
  - create_goal: adds new goal to empty goals list
  - create_goal: adds new goal to existing goals list
  - create_goal: assigns correct fields
  - create_goal: adds metric sub-block when --metric-current/target provided
  - create_goal: rejects duplicate ID
  - update_goal: updates a scalar field (status)
  - update_goal: updates metric.current
  - update_goal: parks a goal (sets status=parked, parked_since, parked_reason)
  - update_goal: returns False for unknown goal ID
  - write uses atomic write (file is valid YAML after write)
  - Schema preserved: other goals untouched when updating one
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# goals_writer imports work.helpers (gitignored) — skip entire module in CI
_work_available = importlib.util.find_spec("work") is not None
if _work_available:
    import goals_writer  # noqa: E402

pytestmark = pytest.mark.skipif(
    not _work_available,
    reason="work package not available (gitignored — private work module)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_goals_file(goals: list[dict], tmp_path: Path) -> Path:
    """Write a minimal goals.md with given goals list, return path."""
    fm = {
        "schema_version": "2.0",
        "domain": "goals",
        "last_updated": "2026-03-28",
        "goals": goals,
    }
    p = tmp_path / "goals.md"
    p.write_text("---\n" + yaml.dump(fm) + "---\n\n# Goals\n")
    return p


def _read_goals(p: Path) -> list[dict]:
    import re
    content = p.read_text()
    fm_m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
    if not fm_m:
        return []
    fm = yaml.safe_load(fm_m.group(1)) or {}
    return fm.get("goals", [])


BASE_GOAL = {
    "id": "G-001",
    "title": "Summit Mailbox Peak",
    "type": "milestone",
    "category": "fitness",
    "status": "active",
    "next_action": "Register for hike",
    "last_progress": "2026-03-15",
    "created": "2026-01-01",
    "target_date": "2026-08-15",
    "leading_indicators": [],
}


# ---------------------------------------------------------------------------
# create_goal tests
# ---------------------------------------------------------------------------

class TestCreateGoal:
    def test_creates_goal_in_empty_list(self, tmp_path):
        p = _write_goals_file([], tmp_path)
        result = goals_writer.create_goal(p, {
            "id": "G-001",
            "title": "New Goal",
            "type": "milestone",
            "category": "fitness",
            "status": "active",
            "created": "2026-03-28",
        })
        assert result == 0
        goals = _read_goals(p)
        assert len(goals) == 1
        assert goals[0]["id"] == "G-001"
        assert goals[0]["title"] == "New Goal"

    def test_creates_goal_alongside_existing(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.create_goal(p, {
            "id": "G-002",
            "title": "Lose Weight",
            "type": "outcome",
            "category": "health",
            "status": "active",
            "created": "2026-03-28",
        })
        assert result == 0
        goals = _read_goals(p)
        assert len(goals) == 2
        assert goals[1]["id"] == "G-002"

    def test_creates_goal_with_metric(self, tmp_path):
        p = _write_goals_file([], tmp_path)
        goals_writer.create_goal(p, {
            "id": "G-002",
            "title": "Lose Weight",
            "type": "outcome",
            "category": "health",
            "status": "active",
            "created": "2026-03-28",
            "metric_current": 183.0,
            "metric_target": 160.0,
            "metric_unit": "lb",
            "metric_direction": "down",
        })
        goals = _read_goals(p)
        assert goals[0]["metric"]["current"] == 183.0
        assert goals[0]["metric"]["target"] == 160.0
        assert goals[0]["metric"]["direction"] == "down"

    def test_rejects_duplicate_id(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.create_goal(p, {
            "id": "G-001",
            "title": "Duplicate",
            "type": "milestone",
            "category": "other",
            "status": "active",
            "created": "2026-03-28",
        })
        assert result != 0
        goals = _read_goals(p)
        assert len(goals) == 1  # unchanged


# ---------------------------------------------------------------------------
# update_goal tests
# ---------------------------------------------------------------------------

class TestUpdateGoal:
    def test_updates_status(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.update_goal(p, "G-001", {"status": "done"})
        assert result == 0
        goals = _read_goals(p)
        assert goals[0]["status"] == "done"

    def test_updates_metric_current(self, tmp_path):
        goal = dict(BASE_GOAL)
        goal["metric"] = {"current": 183.0, "target": 160, "unit": "lb", "direction": "down"}
        p = _write_goals_file([goal], tmp_path)
        result = goals_writer.update_goal(p, "G-001", {"metric_current": 179.5})
        assert result == 0
        goals = _read_goals(p)
        assert goals[0]["metric"]["current"] == 179.5

    def test_parks_goal_with_reason(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.update_goal(p, "G-001", {
            "status": "parked",
            "parked_reason": "Schedule conflict",
        })
        assert result == 0
        goals = _read_goals(p)
        assert goals[0]["status"] == "parked"
        assert goals[0]["parked_reason"] == "Schedule conflict"
        assert "parked_since" in goals[0]

    def test_returns_false_for_unknown_id(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.update_goal(p, "G-999", {"status": "done"})
        assert result != 0

    def test_other_goals_untouched(self, tmp_path):
        g2 = dict(BASE_GOAL)
        g2["id"] = "G-002"
        g2["title"] = "Other Goal"
        p = _write_goals_file([dict(BASE_GOAL), g2], tmp_path)
        goals_writer.update_goal(p, "G-001", {"status": "done"})
        goals = _read_goals(p)
        other = next(g for g in goals if g["id"] == "G-002")
        assert other["title"] == "Other Goal"
        assert other["status"] == "active"


# ---------------------------------------------------------------------------
# Atomic write test
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_file_valid_yaml_after_write(self, tmp_path):
        import re
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        goals_writer.update_goal(p, "G-001", {"status": "done"})
        content = p.read_text()
        fm_m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
        assert fm_m is not None, "File must have valid YAML frontmatter after write"
        fm = yaml.safe_load(fm_m.group(1))
        assert isinstance(fm, dict)
        assert isinstance(fm.get("goals"), list)
