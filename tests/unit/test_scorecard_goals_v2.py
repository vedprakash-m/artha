"""
tests/unit/test_scorecard_goals_v2.py — Tests for scorecard_view.py goals v2.0 support

Coverage:
  - _parse_goals_yaml reads YAML frontmatter (3 goals)
  - _parse_goals_yaml returns empty list for v1.0 file
  - _parse_goals_yaml handles Markdown table pipes without crash (regression)
  - legacy _parse_goals_index alias works
  - parked goals included in parse result
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from scorecard_view import _parse_goals_yaml, _parse_goals_index  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

V2_CONTENT = textwrap.dedent("""\
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
    - id: G-002
      title: Lose 40 lbs
      type: outcome
      status: active
      metric:
        current: 183.0
        target: 160
        unit: lb
        direction: down
    - id: G-003
      title: Azure AI Cert
      type: milestone
      status: parked
      parked_reason: Schedule conflict
    ---

    # Goals

    | Goal | Status |
    |------|--------|
    | Summit Mailbox Peak | active |
""")

V1_CONTENT = textwrap.dedent("""\
    ---
    schema_version: '1.0'
    domain: goals
    last_updated: '2026-01-01'
    ---

    # Goals

    Nothing defined yet.
""")


class TestScorecardParseGoalsYaml:
    def test_reads_all_three_goals(self):
        goals = _parse_goals_yaml(V2_CONTENT)
        assert len(goals) == 3

    def test_parked_goal_included(self):
        goals = _parse_goals_yaml(V2_CONTENT)
        statuses = {g["id"]: g["status"] for g in goals}
        assert statuses["G-003"] == "parked"

    def test_metric_sub_block_present(self):
        goals = _parse_goals_yaml(V2_CONTENT)
        g2 = next(g for g in goals if g["id"] == "G-002")
        assert g2["metric"]["current"] == 183.0

    def test_v1_returns_empty(self):
        goals = _parse_goals_yaml(V1_CONTENT)
        assert goals == []

    def test_markdown_pipes_no_crash(self):
        """Regression: markdown table pipes must not cause YAML scanner error."""
        goals = _parse_goals_yaml(V2_CONTENT)
        assert len(goals) == 3

    def test_legacy_alias_works(self):
        goals = _parse_goals_index(V2_CONTENT)
        assert len(goals) == 3

    def test_empty_content_returns_empty(self):
        goals = _parse_goals_yaml("")
        assert goals == []
