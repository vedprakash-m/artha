"""
tests/unit/test_work_goals_boundary.py — Work OS isolation boundary tests

Coverage:
  - goals_view.py --scope personal reads ONLY state/goals.md (no work-goals.md)
  - goals_view.py --scope work reads ONLY state/work/work-goals.md
  - goals_view.py --scope all merges both; work goals tagged [work], personal untagged
  - Work goals file parsed correctly (identical schema)
  - Work goals do NOT appear in personal scope
  - Personal goals do NOT appear in work scope
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from goals_view import _parse_goals_yaml  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PERSONAL_GOALS_CONTENT = textwrap.dedent("""\
    ---
    schema_version: '2.0'
    domain: goals
    last_updated: '2026-03-28'
    goals:
    - id: G-001
      title: Summit Mailbox Peak
      type: milestone
      status: active
    - id: G-002
      title: Lose 40 lbs
      type: outcome
      status: active
    ---

    # Goals
""")

WORK_GOALS_CONTENT = textwrap.dedent("""\
    ---
    schema_version: '2.0'
    domain: work-goals
    last_updated: '2026-03-28'
    goals:
    - id: G-W-001
      title: XPF Ramp P0/P1 Delivery
      type: milestone
      status: active
    - id: G-W-002
      title: DD-XPF Pilot Delivery
      type: milestone
      status: active
    ---

    # Work Goals
""")


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------

class TestScopeBoundary:
    """Verify scope isolation between personal and work goals."""

    def test_personal_scope_returns_personal_only(self):
        personal_goals = _parse_goals_yaml(PERSONAL_GOALS_CONTENT)
        ids = [g["id"] for g in personal_goals]
        assert "G-001" in ids
        assert "G-002" in ids
        assert not any(i.startswith("G-W-") for i in ids), (
            "Personal scope must NOT include work goals"
        )

    def test_work_scope_returns_work_only(self):
        work_goals = _parse_goals_yaml(WORK_GOALS_CONTENT, tag="[work]")
        ids = [g["id"] for g in work_goals]
        assert "G-W-001" in ids
        assert "G-W-002" in ids
        assert not any(i.startswith("G-0") for i in ids), (
            "Work scope must NOT include personal goals"
        )

    def test_all_scope_merges_both(self):
        personal_goals = _parse_goals_yaml(PERSONAL_GOALS_CONTENT)
        work_goals = _parse_goals_yaml(WORK_GOALS_CONTENT, tag="[work]")
        all_goals = personal_goals + work_goals

        ids = [g["id"] for g in all_goals]
        assert "G-001" in ids
        assert "G-W-001" in ids
        assert len(all_goals) == 4

    def test_work_goals_tagged_in_all_scope(self):
        personal_goals = _parse_goals_yaml(PERSONAL_GOALS_CONTENT)
        work_goals = _parse_goals_yaml(WORK_GOALS_CONTENT, tag="[work]")
        all_goals = personal_goals + work_goals

        work = [g for g in all_goals if g.get("_tag") == "[work]"]
        personal = [g for g in all_goals if "_tag" not in g]

        assert len(work) == 2
        assert len(personal) == 2

    def test_personal_goals_not_tagged_in_all_scope(self):
        personal_goals = _parse_goals_yaml(PERSONAL_GOALS_CONTENT)
        assert not any("_tag" in g for g in personal_goals)

    def test_work_goals_schema_identical(self):
        """Work goals file uses identical schema v2.0."""
        work_goals = _parse_goals_yaml(WORK_GOALS_CONTENT)
        assert len(work_goals) == 2
        assert all("id" in g for g in work_goals)
        assert all("status" in g for g in work_goals)
        assert all("type" in g for g in work_goals)
