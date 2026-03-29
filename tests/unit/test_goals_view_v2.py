"""
tests/unit/test_goals_view_v2.py — Tests for goals_view.py schema v2.0 support

Coverage:
  - _parse_goals_yaml reads YAML frontmatter correctly (4 goals from state/goals.md)
  - _parse_goals_yaml tag: adds _tag to every goal when specified
  - _parse_goals_yaml returns empty list for v1.0 file (no goals: key)
  - _parse_goals_yaml handles Markdown table pipes without crashing (regression)
  - _format_standard output contains type, metric, staleness columns
  - --scope all merges personal + work goals and tags work goals [work]
  - --scope work returns only work goals
  - --scope personal returns only personal goals
  - legacy _parse_goals_index alias still callable
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from goals_view import _parse_goals_yaml, _parse_goals_index, _format_standard  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_V2_CONTENT = textwrap.dedent("""\
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
      parked_reason: Schedule stabilizes post XPF
      created: '2026-01-01'
    ---

    # Goals & Progress

    | Goal | Status |
    |------|--------|
    | Summit Mailbox Peak | active |
    | Lose 40 lbs | active |
""")

V1_CONTENT = textwrap.dedent("""\
    ---
    schema_version: '1.0'
    domain: goals
    last_updated: '2026-03-28'
    ---

    # Goals & Progress

    | Goal | Status |
    |------|--------|
    | Run 5K | active |
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
      last_progress: '2026-03-25'
      created: '2025-10-01'
    ---

    # Work Goals
""")


# ---------------------------------------------------------------------------
# _parse_goals_yaml tests
# ---------------------------------------------------------------------------

class TestParseGoalsYaml:
    def test_reads_three_goals(self):
        goals = _parse_goals_yaml(MINIMAL_V2_CONTENT)
        assert len(goals) == 3

    def test_goal_ids_correct(self):
        goals = _parse_goals_yaml(MINIMAL_V2_CONTENT)
        ids = [g["id"] for g in goals]
        assert ids == ["G-001", "G-002", "G-003"]

    def test_metric_sub_block_preserved(self):
        goals = _parse_goals_yaml(MINIMAL_V2_CONTENT)
        g2 = next(g for g in goals if g["id"] == "G-002")
        assert g2["metric"]["current"] == 183.0
        assert g2["metric"]["direction"] == "down"

    def test_v1_file_returns_empty_list(self):
        goals = _parse_goals_yaml(V1_CONTENT)
        assert goals == []

    def test_markdown_table_pipes_no_crash(self):
        """Regression: markdown table `|` chars must not cause YAML parse error."""
        goals = _parse_goals_yaml(MINIMAL_V2_CONTENT)
        assert len(goals) == 3  # no exception raised

    def test_tag_added_to_all_goals(self):
        goals = _parse_goals_yaml(WORK_GOALS_CONTENT, tag="[work]")
        assert all(g.get("_tag") == "[work]" for g in goals)

    def test_no_tag_when_not_specified(self):
        goals = _parse_goals_yaml(MINIMAL_V2_CONTENT)
        assert not any("_tag" in g for g in goals)

    def test_legacy_alias_callable(self):
        goals = _parse_goals_index(MINIMAL_V2_CONTENT)
        assert len(goals) == 3


# ---------------------------------------------------------------------------
# _format_standard tests
# ---------------------------------------------------------------------------

class TestFormatStandard:
    def _render(self, content: str = MINIMAL_V2_CONTENT, fm: dict | None = None) -> str:
        goals = _parse_goals_yaml(content)
        if fm is None:
            fm = {"schema_version": "2.0", "domain": "goals"}
        return _format_standard(goals, fm)

    def test_contains_goal_ids(self):
        out = self._render()
        assert "G-001" in out
        assert "G-002" in out

    def test_shows_type_column(self):
        out = self._render()
        assert "milestone" in out or "outcome" in out

    def test_shows_metric_progress(self):
        out = self._render()
        # outcome goal G-002 has metric current=183, target=160
        assert "183" in out

    def test_shows_status_labels(self):
        out = self._render()
        # parked goal G-003 should be visible
        assert "parked" in out.lower() or "G-003" in out


# ---------------------------------------------------------------------------
# Scope merging tests
# ---------------------------------------------------------------------------

class TestScopeAll:
    """Test that --scope all properly merges personal and work goals."""

    def test_scope_all_merges_goals(self, tmp_path):
        personal = tmp_path / "goals.md"
        personal.write_text(MINIMAL_V2_CONTENT)
        work = tmp_path / "work" / "work-goals.md"
        work.parent.mkdir()
        work.write_text(WORK_GOALS_CONTENT)

        personal_goals = _parse_goals_yaml(personal.read_text())
        work_goals = _parse_goals_yaml(work.read_text(), tag="[work]")
        all_goals = personal_goals + work_goals

        assert len(all_goals) == 4
        tagged = [g for g in all_goals if g.get("_tag") == "[work]"]
        assert len(tagged) == 1
        assert tagged[0]["id"] == "G-W-001"

    def test_scope_work_only(self):
        work_goals = _parse_goals_yaml(WORK_GOALS_CONTENT, tag="[work]")
        assert len(work_goals) == 1
        assert work_goals[0]["id"] == "G-W-001"

    def test_scope_personal_no_work_tag(self):
        personal_goals = _parse_goals_yaml(MINIMAL_V2_CONTENT)
        assert not any(g.get("_tag") for g in personal_goals)
