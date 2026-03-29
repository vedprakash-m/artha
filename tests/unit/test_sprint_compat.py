"""
tests/unit/test_sprint_compat.py — Sprint schema compatibility after goals v2.0

Coverage:
  - Top-level sprints: block still readable from state/goals.md after schema change
  - goals: and sprints: blocks coexist without collision
  - _extract_frontmatter still reads non-goals fields (schema_version, domain)
  - goals_view parses a file that has BOTH goals: and sprints: blocks
  - No crash when goals: is present but sprints: is absent
  - No crash when sprints: is present but goals: is absent (v1.0 compat)
"""
from __future__ import annotations

import sys
import re
import textwrap
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from goals_view import _parse_goals_yaml, _extract_frontmatter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOALS_AND_SPRINTS_CONTENT = textwrap.dedent("""\
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
    sprints:
    - id: SPR-001
      name: Hike Prep Sprint
      linked_goal: Summit Mailbox Peak
      target: Register and prepare gear for Nadaan Parinde hike
      sprint_start: '2026-03-28'
      sprint_end: '2026-04-27'
      duration_days: 30
      status: active
      progress_pct: 10
      calibrated_at_14d: false
      outcome: ''
    ---

    # Goals

    | Goal | Status |
    |------|--------|
    | Summit Mailbox Peak | active |
""")

GOALS_ONLY_CONTENT = textwrap.dedent("""\
    ---
    schema_version: '2.0'
    domain: goals
    goals:
    - id: G-001
      title: Test Goal
      type: milestone
      status: active
    ---
""")

SPRINTS_ONLY_V1_CONTENT = textwrap.dedent("""\
    ---
    schema_version: '1.0'
    domain: goals
    sprints:
    - id: SPR-001
      name: Old Sprint
      linked_goal: Some Goal
      status: active
      progress_pct: 50
    ---

    # Goals (v1.0 — no goals: YAML block)
""")


def _parse_fm(content: str) -> dict:
    fm_m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
    if not fm_m:
        return {}
    return yaml.safe_load(fm_m.group(1)) or {}


class TestSprintCompat:
    def test_goals_and_sprints_coexist(self):
        fm = _parse_fm(GOALS_AND_SPRINTS_CONTENT)
        assert isinstance(fm.get("goals"), list)
        assert isinstance(fm.get("sprints"), list)

    def test_goals_parsed_when_sprints_present(self):
        goals = _parse_goals_yaml(GOALS_AND_SPRINTS_CONTENT)
        assert len(goals) == 1
        assert goals[0]["id"] == "G-001"

    def test_sprints_intact_after_goals_parse(self):
        fm = _parse_fm(GOALS_AND_SPRINTS_CONTENT)
        sprints = fm.get("sprints", [])
        assert len(sprints) == 1
        assert sprints[0]["id"] == "SPR-001"
        assert sprints[0]["linked_goal"] == "Summit Mailbox Peak"

    def test_no_crash_goals_only(self):
        goals = _parse_goals_yaml(GOALS_ONLY_CONTENT)
        assert len(goals) == 1

    def test_no_crash_sprints_only_v1(self):
        """v1.0 file with sprints but no goals: block — must return empty, not crash."""
        goals = _parse_goals_yaml(SPRINTS_ONLY_V1_CONTENT)
        assert goals == []

    def test_extract_frontmatter_reads_schema_version(self):
        meta = _extract_frontmatter(GOALS_AND_SPRINTS_CONTENT)
        assert meta.get("schema_version") == "2.0"

    def test_extract_frontmatter_reads_domain(self):
        meta = _extract_frontmatter(GOALS_AND_SPRINTS_CONTENT)
        assert meta.get("domain") == "goals"

    def test_sprint_progress_pct_readable(self):
        """Step 3 calibration reads sprints[].progress_pct — must survive schema bump."""
        fm = _parse_fm(GOALS_AND_SPRINTS_CONTENT)
        sprint = fm["sprints"][0]
        assert sprint["progress_pct"] == 10
        assert sprint["calibrated_at_14d"] is False
