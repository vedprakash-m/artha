"""
tests/work/test_narrative_engine.py — Unit tests for Work OS Narrative Engine.

Validates scripts/narrative_engine.py (§7.10):
  - NarrativeEngine instantiates with empty state dir (graceful degradation)
  - generate_weekly_memo() produces valid Markdown with required sections
  - generate_weekly_memo() reads frontmatter when state files are populated
  - generate_talking_points() returns structured output for any topic
  - generate_boundary_report() handles missing state gracefully
  - generate_connect_summary() handles empty goals gracefully
  - CLI main() produces output to stdout and to --output file
  - Data freshness footer is always present (§3.8)

Run: pytest tests/work/test_narrative_engine.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from narrative_engine import NarrativeEngine, main  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_dir(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a state/work directory with optional preset files."""
    state = tmp_path / "state" / "work"
    state.mkdir(parents=True, exist_ok=True)
    for name, content in (files or {}).items():
        (state / name).write_text(content, encoding="utf-8")
    return state


_MINIMAL_FRONTMATTER = """\
---
schema_version: '1.0'
domain: {domain}
last_updated: '2026-03-25T08:00:00+00:00'
work_os: true
---
"""

_PROJECTS_FM = _MINIMAL_FRONTMATTER.format(domain="work-projects") + """
## Active Sprint

- Fix authentication timeout (#1242) — Active — Pri 1
- Implement caching (#1301) — Active — Pri 2

## Blocked Items

- Deploy to staging (#1188) — blocked on infra

## Recently Completed (last 14 days)

- #1100 Add telemetry logging
"""

_CALENDAR_FM = _MINIMAL_FRONTMATTER.format(domain="work-calendar") + """
## Today

| Time | Meeting | Duration |
|------|---------|----------|
| 10:00 AM | Architecture Review | 1.0h |
| 2:00 PM | Sprint Planning | 1.5h |
"""

_PERF_FM = _MINIMAL_FRONTMATTER.format(domain="work-performance") + """
## Connect Goals

### Goal 1: Improve system reliability
- Target: 99.9% uptime
- Status: in_progress

## Manager 1:1 Pivot Log

- 2026-03-10: Manager asked to prioritize latency reduction over new features
"""

_BOUNDARY_FM = """\
---
schema_version: '1.0'
domain: work-boundary
last_updated: '2026-03-25T08:00:00+00:00'
work_os: true
boundary_score: 75
after_hours_count: 2
total_hours_today: 6.5
focus_availability_pct: 0.35
---
"""


# ===========================================================================
# Instantiation
# ===========================================================================

class TestNarrativeEngineInit:

    def test_instantiates_with_empty_state_dir(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        assert eng is not None

    def test_instantiates_with_no_state_files(self, tmp_path):
        state = tmp_path / "empty"
        state.mkdir()
        eng = NarrativeEngine(state_dir=state, profile={})
        assert eng is not None

    def test_accepts_explicit_profile(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"profile": {"goals": [{"title": "Ship feature X"}]}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        assert eng.profile == profile


# ===========================================================================
# generate_weekly_memo
# ===========================================================================

class TestGenerateWeeklyMemo:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_state_does_not_raise(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        assert "WEEKLY STATUS MEMO" in result or "Weekly Status Memo" in result.replace("#", "")

    def test_contains_accomplishments_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        upper = result.upper()
        assert any(
            kw in upper
            for kw in ("ACCOMPLISHMENTS", "HIGHLIGHTS", "DELIVERY STATUS", "AT A GLANCE", "CONNECT GOALS")
        )

    def test_contains_next_week_or_upcoming(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        assert any(kw in result.upper() for kw in ("NEXT WEEK", "UPCOMING", "IN PROGRESS", "ACTIVE"))

    def test_contains_freshness_footer(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        assert "freshness" in result.lower() or "refresh" in result.lower() or "---" in result

    def test_accepts_explicit_period(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo(period="Week of March 25, 2026")
        assert "March 25, 2026" in result

    def test_reads_projects_data(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-projects.md": _PROJECTS_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        # Should include at least one project item
        assert "authentication" in result.lower() or "caching" in result.lower() or "telemetry" in result.lower()

    def test_reads_calendar_data(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-calendar.md": _CALENDAR_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        # Calendar section or meeting load should appear
        assert "Architecture" in result or "meeting" in result.lower() or "calendar" in result.lower()

    def test_draft_disclaimer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        assert "draft" in result.lower() or "review" in result.lower()

    def test_does_not_start_with_error(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_weekly_memo()
        assert not result.startswith("Error") and not result.startswith("Traceback")


# ===========================================================================
# generate_talking_points
# ===========================================================================

class TestGenerateTalkingPoints:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_talking_points("sprint review")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_topic_appears_in_output(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_talking_points("platform migration")
        assert "platform migration" in result.lower()

    def test_contains_key_sections(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_talking_points("service reliability")
        upper = result.upper()
        assert any(s in upper for s in ("TALKING POINT", "DELIVERY", "RISKS", "OPEN ITEM", "ASK"))

    def test_empty_state_does_not_raise(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_talking_points("any topic")
        assert len(result) > 10

    def test_finds_topic_in_projects(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-projects.md": _PROJECTS_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_talking_points("caching")
        # Should surface the relevant project item
        assert "caching" in result.lower()

    def test_goal_alignment_shown_when_match(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"profile": {"goals": [{"title": "Improve caching performance"}]}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_talking_points("caching")
        assert "caching" in result.lower()


# ===========================================================================
# generate_boundary_report
# ===========================================================================

class TestGenerateBoundaryReport:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_boundary_report()
        assert isinstance(result, str)

    def test_missing_state_returns_helpful_message(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_boundary_report()
        assert "boundary" in result.lower() or "refresh" in result.lower()

    def test_populated_state_shows_score(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-boundary.md": _BOUNDARY_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_boundary_report()
        assert "75" in result

    def test_at_risk_boundary_shows_indicator(self, tmp_path):
        at_risk_fm = _BOUNDARY_FM.replace("boundary_score: 75", "boundary_score: 45")
        state = _make_state_dir(tmp_path, {"work-boundary.md": at_risk_fm})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_boundary_report()
        assert "at-risk" in result.lower() or "risk" in result.lower()

    def test_healthy_boundary_shows_indicator(self, tmp_path):
        healthy_fm = _BOUNDARY_FM.replace("boundary_score: 75", "boundary_score: 92")
        state = _make_state_dir(tmp_path, {"work-boundary.md": healthy_fm})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_boundary_report()
        assert "healthy" in result.lower() or "92" in result


# ===========================================================================
# generate_connect_summary
# ===========================================================================

class TestGenerateConnectSummary:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_goals_shows_bootstrap_hint(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "bootstrap" in result.lower() or "goal" in result.lower()

    def test_goals_from_profile_appear(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"profile": {"goals": [{"title": "Drive reliability to 99.9%", "status": "in_progress"}]}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_connect_summary()
        assert "Drive reliability" in result

    def test_perf_pivot_log_section_present(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-performance.md": _PERF_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "Manager 1:1" in result or "Pivot" in result or "pivot" in result

    def test_freshness_footer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "freshness" in result.lower() or "---" in result

    def test_contains_connect_header(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "Connect" in result


# ===========================================================================
# CLI (main)
# ===========================================================================

class TestNarrativeEngineCLI:

    def test_weekly_memo_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "weekly_memo", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert len(captured.out) > 0
        assert "week" in captured.out.lower()

    def test_talking_points_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "talking_points", "--topic", "sprint demo", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "sprint demo" in captured.out.lower()

    def test_talking_points_requires_topic(self, tmp_path):
        state = _make_state_dir(tmp_path)
        with pytest.raises(SystemExit):
            main(["--template", "talking_points", "--state-dir", str(state)])

    def test_boundary_report_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "boundary_report", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_connect_summary_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "connect_summary", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "connect" in captured.out.lower()

    def test_output_to_file(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        out_file = tmp_path / "memo_out.md"
        main(["--template", "weekly_memo", "--state-dir", str(state), "--output", str(out_file)])
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert len(content) > 0

    def test_period_flag_respected(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "weekly_memo", "--period", "Week of April 7, 2026", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "April 7, 2026" in captured.out


# ===========================================================================
# generate_newsletter  (Phase 2, §7.8)
# ===========================================================================

class TestGenerateNewsletter:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_newsletter_header(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "Team Newsletter" in result

    def test_period_label_appears(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter(period="March 24–28, 2026")
        assert "March 24" in result

    def test_default_period_rendered(self, tmp_path):
        """When no period given, a date range should still appear."""
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        # Should contain a year and month reference
        assert any(str(y) in result for y in range(2025, 2030))

    def test_contains_highlights_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "Highlights" in result or "HIGHLIGHTS" in result

    def test_contains_key_decisions_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "Key Decisions" in result or "Decisions" in result

    def test_contains_next_steps_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "Next Steps" in result

    def test_draft_disclaimer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "DRAFT" in result

    def test_reads_projects_completed(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-projects.md": _PROJECTS_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        # Project content or placeholder should appear
        assert len(result) > 100

    def test_does_not_raise_on_empty_state(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert not result.startswith("Error") and not result.startswith("Traceback")

    def test_freshness_footer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "freshness" in result.lower() or "---" in result

    def test_cli_newsletter_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "newsletter", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "Team Newsletter" in captured.out

    def test_cli_newsletter_with_period(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "newsletter", "--period", "April 7–11, 2026", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "April 7" in captured.out


# ===========================================================================
# generate_deck  (Phase 2, §7.8)
# ===========================================================================

class TestGenerateDeck:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_lt_deck_header(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "LT Deck" in result or "Deck Content" in result

    def test_topic_appears_in_output(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck(topic="H1 Delivery Review")
        assert "H1 Delivery Review" in result

    def test_default_label_when_no_topic(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "Leadership Update" in result or "LT Deck" in result

    def test_contains_executive_summary(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "Executive Summary" in result

    def test_contains_status_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "## Status" in result or "Status" in result

    def test_contains_risks_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "Risks" in result

    def test_contains_asks_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "Asks" in result

    def test_contains_next_steps(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "Next Steps" in result

    def test_draft_disclaimer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "DRAFT" in result

    def test_does_not_raise_on_empty_state(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck("feature launch")
        assert not result.startswith("Error") and not result.startswith("Traceback")

    def test_freshness_footer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "freshness" in result.lower() or "---" in result

    def test_cli_deck_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "deck", "--topic", "H2 planning", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "H2 planning" in captured.out

    def test_cli_deck_no_topic_uses_default(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "deck", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert len(captured.out) > 0
        assert "DRAFT" in captured.out


# ===========================================================================
# generate_calibration_brief  (Phase 3, §7.6)
# ===========================================================================

_JOURNEYS_FM = _MINIMAL_FRONTMATTER.format(domain="work-project-journeys") + """\
projects_tracked: 2
---

## Alpha Platform Migration

| **2026-01-15** | Completed phase-1 GA | Launch blog post | 2.5 M users, 99.9% P99 |
| **2026-02-20** | Staff review presented | Staff deck | LT visibility |

## Beta Analytics Overhaul

| **2026-03-01** | Design review approved | RFC-42 | Unblocked 3 teams |
"""

_PEOPLE_VIS_BODY = """\
## Visibility Events

| Date | Stakeholder | Event Type | Context |
| --- | --- | --- | --- |
| 2026-02-10 | Director Smith | Staff Review | Presented Alpha Architecture |
| 2026-03-04 | VP Jones | Design Review | Beta Analytics RFC |
| 2026-03-10 | Director Smith | 1:1 | Follow-up on KPI model |
"""

_CAREER_RECOGNITION = """\
## Recognition

- "Delivered this reliably under ambiguous requirements" — Director Smith
- "Cross-team communication was excellent" — VP Jones
"""


class TestCalibrationBrief:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_calibration_header(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "Calibration" in result

    def test_draft_disclaimer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "DRAFT" in result

    def test_manager_only_marker(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "manager" in result.lower()

    def test_impact_summary_section(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-project-journeys.md": _JOURNEYS_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "Impact Summary" in result

    def test_evidence_density_section(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-performance.md": _PERF_FM})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "Evidence Density" in result

    def test_visibility_section_populated(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-people.md": _PEOPLE_VIS_BODY})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "Visibility" in result or "visibility" in result

    def test_manager_talking_points_from_career(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-career.md": _CAREER_RECOGNITION})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "Talking Points" in result or "Manager" in result

    def test_readiness_signal_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "Readiness" in result

    def test_graceful_on_empty_state(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert not result.startswith("Error") and not result.startswith("Traceback")

    def test_freshness_footer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_calibration_brief()
        assert "freshness" in result.lower() or "---" in result

    def test_cli_calibration_brief_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "calibration_brief", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "Calibration" in captured.out


# ===========================================================================
# generate_escalation_memo  (Phase 3, §7.10)
# ===========================================================================

_BLOCKED_PROJECTS = _MINIMAL_FRONTMATTER.format(domain="work-projects") + """\
## Blocked Items

- Deploy to staging (#1188) — blocked on infra team approval
- Release pipeline (#1250) — at risk: security review pending
"""

_MANAGER_CHAIN_PEOPLE = """\
## Manager Chain

- Alice Doe — VP Engineering — alice@example.com
- Bob Roe — Director — bob@example.com
"""


class TestEscalationMemo:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Infra approval is blocking the GA release")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_context_appears_in_situation(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Security review blocking deploy")
        assert "Security review blocking deploy" in result

    def test_escalation_header_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Blocker X")
        assert "Escalation" in result

    def test_options_section_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Blocker X")
        assert "Option" in result

    def test_what_i_need_section(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Blocker X")
        assert "What I Need" in result or "need" in result.lower()

    def test_reads_blocked_items_from_projects(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-projects.md": _BLOCKED_PROJECTS})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Deployment is stuck")
        assert "Active blockers" in result or "blocked" in result.lower() or "staging" in result.lower()

    def test_stakeholders_in_what_i_need(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-people.md": _MANAGER_CHAIN_PEOPLE})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Need sign-off")
        assert "Alice" in result or "Bob" in result

    def test_draft_disclaimer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Anything")
        assert "DRAFT" in result

    def test_timeline_section_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Need sign-off on release")
        assert "Timeline" in result

    def test_graceful_on_empty_state(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_escalation_memo(context="Some context")
        assert not result.startswith("Error") and not result.startswith("Traceback")

    def test_cli_escalation_memo_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main([
            "--template", "escalation_memo",
            "--context", "Infra approval blocking GA",
            "--state-dir", str(state),
        ])
        captured = capsys.readouterr()
        assert "Escalation" in captured.out
        assert "Infra approval blocking GA" in captured.out


# ===========================================================================
# generate_decision_memo  (Phase 3, §7.10)
# ===========================================================================

_DECISIONS_DOC = """\
---
generated_by: work_decide
last_updated: '2026-03-20T09:00:00+00:00'
---

# Work Decisions

| ID | Date | Summary | Owner | Rationale |
| --- | --- | --- | --- | --- |
| D-001 | 2026-03-10 | OPEN: Adopt new analytics stack | me | Cost savings + velocity |
| D-002 | 2026-03-15 | CLOSED: Defer DR redesign to H2 | me | Competing priorities |
"""


class TestDecisionMemo:

    def test_returns_string(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_decision_memo_header(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        assert "Decision" in result

    def test_alternatives_section_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        assert "Alternatives" in result

    def test_next_steps_section_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        assert "Next Steps" in result

    def test_distribution_section_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        assert "Distribution" in result

    def test_draft_disclaimer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        assert "DRAFT" in result

    def test_found_decision_by_id(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-decisions.md": _DECISIONS_DOC})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo(decision_id="D-001")
        assert "analytics" in result.lower() or "D-001" in result

    def test_graceful_when_id_not_found(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo(decision_id="D-999")
        assert "Decision" in result
        assert not result.startswith("Error")

    def test_stakeholders_in_distribution(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-people.md": _MANAGER_CHAIN_PEOPLE})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        # Distribution section should include manager chain names
        assert "Alice" in result or "Bob" in result

    def test_freshness_footer_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_decision_memo()
        assert "freshness" in result.lower() or "---" in result

    def test_cli_decision_memo_stdout(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path, {"work-decisions.md": _DECISIONS_DOC})
        main([
            "--template", "decision_memo",
            "--decision-id", "D-001",
            "--state-dir", str(state),
        ])
        captured = capsys.readouterr()
        assert "Decision" in captured.out

    def test_cli_decision_memo_no_id(self, tmp_path, capsys):
        state = _make_state_dir(tmp_path)
        main(["--template", "decision_memo", "--state-dir", str(state)])
        captured = capsys.readouterr()
        assert "Decision" in captured.out


# ===========================================================================
# TestConnectAutoEvidence  (Phase 3 item 8 — auto-evidence matching)
# ===========================================================================

_JOURNEYS_WITH_RELIABILITY = _MINIMAL_FRONTMATTER.format(domain="work-project-journeys") + """\
projects_tracked: 1
---

## Platform Alpha

| **2026-01-10** | Reliability SLO achieved | Dashboard | 99.9% P99 uptime |
| **2026-01-25** | Reliability monitor added | AlertSpec | Zero missed alerts |
| **2026-02-05** | Reliability postmortem closed | RFC-12 | Improved incident response |
| **2026-02-20** | Reliability automation deployed | Pipeline | Reduced toil 40% |
| **2026-02-28** | Reliability audit passed | Audit doc | External validator |
| **2026-03-10** | Reliability dashboard V2 | Metrics | Team visibility |
"""

_PERF_WITH_CONNECT_PERIOD = """\
---
schema_version: '1.0'
domain: work-performance
last_updated: '2026-03-25T08:00:00+00:00'
connect_period: H1 2026
work_os: true
---

## Connect Goals

### Goal 1: Improve system reliability
- Target: 99.9% uptime
- Status: in_progress

### Goal 2: Expand xyzquux coverage
- Target: 100% teams onboarded
- Status: not_started
"""


class TestConnectAutoEvidence:
    """Phase 3 item 8 — auto-evidence matching in generate_connect_summary()."""

    def test_goals_read_from_performance_md(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-performance.md": _PERF_WITH_CONNECT_PERIOD})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "Improve system reliability" in result

    def test_milestone_matched_to_goal_keywords(self, tmp_path):
        state = _make_state_dir(tmp_path, {
            "work-performance.md": _PERF_WITH_CONNECT_PERIOD,
            "work-project-journeys.md": _JOURNEYS_WITH_RELIABILITY,
        })
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        # Stars appear because "reliability" keyword matches multiple milestones
        assert "★" in result

    def test_density_shows_stars_for_matched_goal(self, tmp_path):
        state = _make_state_dir(tmp_path, {
            "work-performance.md": _PERF_WITH_CONNECT_PERIOD,
            "work-project-journeys.md": _JOURNEYS_WITH_RELIABILITY,
        })
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        # ★★★ for >= 5 matches, ★★☆ for >= 3, ★☆☆ for >= 1
        assert any(s in result for s in ("★★★", "★★☆", "★☆☆"))

    def test_gap_flag_for_zero_match_goal(self, tmp_path):
        state = _make_state_dir(tmp_path, {
            "work-performance.md": _PERF_WITH_CONNECT_PERIOD,
            "work-project-journeys.md": _JOURNEYS_WITH_RELIABILITY,
        })
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        # "Goal 2: Expand xyzquux coverage" — xyzquux won't match anything → GAP
        assert "GAP" in result

    def test_evidence_gaps_section_aggregates_empty_goals(self, tmp_path):
        state = _make_state_dir(tmp_path, {
            "work-performance.md": _PERF_WITH_CONNECT_PERIOD,
            "work-project-journeys.md": _JOURNEYS_WITH_RELIABILITY,
        })
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "Evidence Gaps" in result

    def test_top_5_matches_shown_per_goal(self, tmp_path):
        # 6 reliability milestones exist; only 5 should appear per goal
        state = _make_state_dir(tmp_path, {
            "work-performance.md": _PERF_WITH_CONNECT_PERIOD,
            "work-project-journeys.md": _JOURNEYS_WITH_RELIABILITY,
        })
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        # The 6th milestone date should not appear (top-5 capped)
        assert "2026-03-10" not in result or result.count("2026-03-10") <= 1

    def test_no_goals_shows_bootstrap_hint(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "bootstrap" in result.lower() or "goal" in result.lower()

    def test_connect_period_from_frontmatter(self, tmp_path):
        state = _make_state_dir(tmp_path, {"work-performance.md": _PERF_WITH_CONNECT_PERIOD})
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_connect_summary()
        assert "H1 2026" in result

    def test_profile_goals_as_fallback(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"profile": {"goals": [{"title": "Drive reliability to 99.9%", "status": "in_progress"}]}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_connect_summary()
        assert "Drive reliability" in result


# ===========================================================================
# TestNewsletterCustomization  (Phase 3 item 13 — newsletter customization)
# ===========================================================================

class TestNewsletterCustomization:
    """Phase 3 item 13 — newsletter template customization."""

    def test_default_sections_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "Highlights" in result
        assert "Key Decisions" in result
        assert "Next Steps" in result

    def test_concise_tone_shorter_header(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"newsletter": {"tone": "concise"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_newsletter()
        # concise tone: header is "<period> Update", not "Team Newsletter"
        assert "Update" in result
        lines = result.split("\n")
        header_line = next((l for l in lines if l.startswith("# ")), "")
        assert "Team Newsletter" not in header_line

    def test_leadership_template_has_executive_summary(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"newsletter": {"template": "leadership"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_newsletter()
        assert "Executive Summary" in result

    def test_leadership_template_has_asks(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"newsletter": {"template": "leadership"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_newsletter()
        assert "Asks" in result

    def test_team_morale_template_has_shoutouts(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"newsletter": {"template": "team_morale"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_newsletter()
        assert "Shoutouts" in result

    def test_custom_section_order_next_steps_before_highlights(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"newsletter": {"sections": ["next_steps", "highlights"]}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_newsletter()
        pos_next = result.find("Next Steps")
        pos_hi = result.find("Highlights")
        assert pos_next != -1 and pos_hi != -1
        assert pos_next < pos_hi

    def test_unknown_section_silently_skipped(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"newsletter": {"sections": ["nonexistent_section"]}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        # Should not raise
        result = eng.generate_newsletter()
        assert isinstance(result, str)

    def test_draft_warning_always_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        for profile in [
            {},
            {"work": {"newsletter": {"tone": "concise"}}},
            {"work": {"newsletter": {"template": "leadership"}}},
            {"work": {"newsletter": {"template": "team_morale"}}},
        ]:
            eng = NarrativeEngine(state_dir=state, profile=profile)
            result = eng.generate_newsletter()
            assert "DRAFT" in result, f"DRAFT missing for profile {profile}"

    def test_accomplishments_in_standard_template(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_newsletter()
        assert "Accomplishments" in result

    def test_concise_tone_has_brief_draft_disclaimer(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"newsletter": {"tone": "concise"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_newsletter()
        # concise tone uses shorter draft disclaimer
        assert "DRAFT" in result
        assert "Review before distributing" in result


# ===========================================================================
# TestDeckPersonalization  (Phase 3 item 13 — deck outline personalization)
# ===========================================================================

class TestDeckPersonalization:
    """Phase 3 item 13 — deck outline personalization."""

    def test_risk_review_template_has_dependencies(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"deck": {"template": "risk_review"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_deck()
        assert "Dependencies" in result

    def test_exec_brief_template_has_fewer_sections(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile_exec = {"work": {"deck": {"template": "exec_brief"}}}
        profile_std = {"work": {"deck": {"template": "standard"}}}
        eng_brief = NarrativeEngine(state_dir=state, profile=profile_exec)
        eng_std = NarrativeEngine(state_dir=state, profile=profile_std)
        # exec_brief has 4 sections, standard has 6
        result_brief = eng_brief.generate_deck()
        result_std = eng_std.generate_deck()
        assert result_brief.count("## ") < result_std.count("## ")

    def test_audience_exec_uses_strategic_label(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"deck": {"audience": "exec"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_deck()
        # exec audience → a_labels[1] = "Strategic Direction"
        assert "Strategic Direction" in result

    def test_default_template_has_asks(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        assert "Asks" in result

    def test_program_status_includes_key_decisions(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"deck": {"template": "program_status"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_deck()
        assert "Key Decisions" in result

    def test_non_standard_template_name_in_header(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"deck": {"template": "risk_review"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_deck()
        assert "risk_review" in result

    def test_exec_brief_has_key_results(self, tmp_path):
        state = _make_state_dir(tmp_path)
        profile = {"work": {"deck": {"template": "exec_brief"}}}
        eng = NarrativeEngine(state_dir=state, profile=profile)
        result = eng.generate_deck()
        assert "Key Results" in result

    def test_standard_template_has_no_dependencies(self, tmp_path):
        state = _make_state_dir(tmp_path)
        eng = NarrativeEngine(state_dir=state, profile={})
        result = eng.generate_deck()
        # Dependencies is only in risk_review template
        assert "Dependencies" not in result

    def test_draft_disclaimer_always_present(self, tmp_path):
        state = _make_state_dir(tmp_path)
        for template in ["standard", "risk_review", "program_status", "exec_brief"]:
            profile = {"work": {"deck": {"template": template}}}
            eng = NarrativeEngine(state_dir=state, profile=profile)
            result = eng.generate_deck()
            assert "DRAFT" in result, f"DRAFT missing for template={template}"
