"""
tests/work/test_phase4_phase5.py — Unit tests for Phase 4 (items 2/5/6) and
Phase 5 (items 3/4/7/8) features.

Covers:
    Phase 4 item 2 — Provider tier assessment and degraded mode reporting
    Phase 4 item 5 — Prompt linter
    Phase 5 item 3 — ES Chat signal hierarchy (_extract_exec_visibility_signals)
    Phase 5 item 4 — Incidents / repos scaffold (cmd_incidents, cmd_repos)
    Phase 5 item 7 — /work graph (cmd_graph)
    Phase 5 item 8 — Pre-read tracking (cmd_mark_preread, _load_preread_markers)

Run: pytest tests/work/test_phase4_phase5.py -v
"""
from __future__ import annotations

import sys
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "tools"))

import work_reader  # type: ignore
from work_reader import (  # type: ignore
    _assess_provider_tier,
    _build_degraded_mode_report,
    _seniority_tier,
    _extract_exec_visibility_signals,
    cmd_health,
    cmd_graph,
    cmd_mark_preread,
    _load_preread_markers,
    cmd_incidents,
    cmd_repos,
    main,
    _SENIORITY_RANK,
    _EXEC_TIER_KEYWORDS,
    _PREREAD_SECTION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject_state_dir(state_dir: Path) -> None:
    work_reader._WORK_STATE_DIR = state_dir
    # Phase 3: patch submodule state dirs so re-exported functions see the temp dir.
    import work.health  # noqa: PLC0415
    work.health._WORK_STATE_DIR = state_dir
    import work.decisions  # noqa: PLC0415
    work.decisions._WORK_STATE_DIR = state_dir
    import work.discovery  # noqa: PLC0415
    work.discovery._WORK_STATE_DIR = state_dir
    import work.meetings  # noqa: PLC0415
    work.meetings._WORK_STATE_DIR = state_dir
    import work.career  # noqa: PLC0415
    work.career._WORK_STATE_DIR = state_dir
    import work.narrative  # noqa: PLC0415
    work.narrative._WORK_STATE_DIR = state_dir


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()


def _write_state(state_dir: Path, name: str, fm: dict, body: str = "") -> Path:
    import yaml  # type: ignore[import]
    p = state_dir / name
    content = "---\n" + yaml.dump(fm, default_flow_style=False) + "---\n\n" + body
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


# ===========================================================================
# Phase 4 item 2 — Provider tier assessment
# ===========================================================================


class TestAssessProviderTier:
    def test_microsoft_enhanced_tier(self):
        assert _assess_provider_tier(True, True, True, True) == "Microsoft Enhanced"

    def test_enterprise_tier_ado_only(self):
        result = _assess_provider_tier(True, True, False, False)
        assert result == "Enterprise"

    def test_enterprise_tier_agency_only(self):
        result = _assess_provider_tier(True, False, False, True)
        assert result == "Enterprise"

    def test_core_m365_tier(self):
        result = _assess_provider_tier(True, False, False, False)
        assert result == "Core M365"

    def test_offline_tier(self):
        result = _assess_provider_tier(False, False, False, False)
        assert "Offline" in result

    def test_workiq_alone_not_enhanced(self):
        # WorkIQ without agency is Enterprise, not Enhanced
        result = _assess_provider_tier(True, True, True, False)
        assert result == "Enterprise"


class TestBuildDegradedModeReport:
    def test_returns_list(self):
        result = _build_degraded_mode_report("Offline — no providers available", False, False, False)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_contains_provider_coverage_header(self):
        result = _build_degraded_mode_report("Core M365", True, False, False)
        joined = "\n".join(result)
        assert "Provider coverage:" in joined

    def test_degraded_graph_shows_warnings(self):
        result = _build_degraded_mode_report("Offline — no providers available", False, False, False)
        joined = "\n".join(result)
        assert "DEGRADED" in joined
        assert "/work" in joined

    def test_healthy_graph_shows_checkmarks(self):
        result = _build_degraded_mode_report("Microsoft Enhanced", True, True, True)
        joined = "\n".join(result)
        assert "✅" in joined
        assert "full briefing available" in joined

    def test_ado_degraded_message(self):
        result = _build_degraded_mode_report("Core M365", True, False, False)
        joined = "\n".join(result)
        assert "/work sprint" in joined
        assert "DEGRADED" in joined

    def test_workiq_not_available_message(self):
        result = _build_degraded_mode_report("Core M365", True, False, False)
        joined = "\n".join(result)
        assert "WorkIQ" in joined

    def test_tier_shown_in_report(self):
        tier = "Enterprise"
        result = _build_degraded_mode_report(tier, True, True, False)
        joined = "\n".join(result)
        assert tier in joined


class TestHealthProviderCoverage:
    """cmd_health() should now include the provider coverage section."""

    def test_provider_coverage_in_health_output(self, work_dir: Path):
        _inject_state_dir(work_dir)
        out = cmd_health()
        assert "Provider coverage:" in out

    def test_active_tier_shown(self, work_dir: Path):
        _inject_state_dir(work_dir)
        out = cmd_health()
        assert "Active tier:" in out


# ===========================================================================
# Phase 5 item 3 — ES Chat signal hierarchy / seniority
# ===========================================================================


class TestSeniorityTier:
    def test_cvp_returns_rank_5(self):
        rank, label = _seniority_tier("CVP of Engineering")
        assert rank == 5

    def test_partner_returns_rank_5(self):
        rank, label = _seniority_tier("Partner Software Engineer")
        assert rank == 5

    def test_vp_returns_rank_4(self):
        rank, label = _seniority_tier("VP Product")
        assert rank == 4

    def test_director_returns_rank_3(self):
        rank, label = _seniority_tier("Senior Director")
        assert rank == 3

    def test_manager_returns_rank_2(self):
        rank, label = _seniority_tier("Engineering Manager")
        assert rank == 2

    def test_ic_returns_rank_1(self):
        rank, label = _seniority_tier("Senior IC")
        assert rank == 1

    def test_unknown_returns_rank_0(self):
        rank, label = _seniority_tier("Random text with no seniority")
        assert rank == 0

    def test_higher_rank_wins(self):
        # Text mentioning both VP and Manager — VP should win
        rank, label = _seniority_tier("VP and Engineering Manager")
        assert rank >= 4

    def test_case_insensitive(self):
        rank_upper, _ = _seniority_tier("DIRECTOR level feedback")
        rank_lower, _ = _seniority_tier("director level feedback")
        assert rank_upper == rank_lower


class TestExtractExecVisibilitySignals:
    def test_no_people_no_comms_returns_empty(self):
        result = _extract_exec_visibility_signals("", "")
        assert result == []

    def test_known_director_in_comms_produces_signal(self):
        people_body = textwrap.dedent("""\
            ### Sarah Chen
            tier: Director
        """)
        comms_body = "| sarah chen | Re: Q2 Budget Review | 2026-03-25 |\n"
        result = _extract_exec_visibility_signals(comms_body, people_body)
        assert len(result) >= 1
        assert "Sarah Chen" in result[0] or "Q2 Budget" in result[0]

    def test_exec_keyword_in_line_triggers_signal(self):
        comms_body = "| VP Alice | Strategic alignment meeting | 2026-03-25 |\n"
        result = _extract_exec_visibility_signals(comms_body, "")
        assert len(result) >= 1

    def test_ic_in_comms_no_signal(self):
        people_body = "### Bob Smith\ntier: IC\n"
        comms_body = "| bob smith | Regular standup | 2026-03-25 |\n"
        result = _extract_exec_visibility_signals(comms_body, people_body)
        # IC not in exec signals
        assert len(result) == 0

    def test_non_table_lines_ignored(self):
        comms_body = textwrap.dedent("""\
            # Header
            Some narrative text mentioning Director
            ## Section
        """)
        result = _extract_exec_visibility_signals(comms_body, "")
        assert result == []


# ===========================================================================
# Phase 5 item 7 — /work graph
# ===========================================================================


class TestCmdGraph:
    def test_graph_empty_state_renders(self, work_dir: Path):
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-people.md", {"last_updated": _fresh_ts()}, "")
        out = cmd_graph()
        assert "STAKEHOLDER GRAPH" in out

    def test_graph_missing_file_graceful(self, work_dir: Path):
        _inject_state_dir(work_dir)
        out = cmd_graph()
        assert "STAKEHOLDER GRAPH" in out

    def test_graph_renders_single_entry(self, work_dir: Path):
        _inject_state_dir(work_dir)
        body = textwrap.dedent("""\
            ### Jane Doe
            tier: Director
            trajectory: warming
            last_interaction: 2026-03-20
        """)
        _write_state(work_dir, "work-people.md", {"last_updated": _fresh_ts()}, body)
        out = cmd_graph()
        assert "Jane Doe" in out
        assert "↑" in out  # warming icon

    def test_graph_groups_by_tier(self, work_dir: Path):
        _inject_state_dir(work_dir)
        body = textwrap.dedent("""\
            ### Alice VP
            tier: VP
            trajectory: stable

            ### Bob IC
            tier: IC
            trajectory: stable
        """)
        _write_state(work_dir, "work-people.md", {"last_updated": _fresh_ts()}, body)
        out = cmd_graph()
        assert "VP" in out
        assert "IC" in out

    def test_graph_shows_network_summary(self, work_dir: Path):
        _inject_state_dir(work_dir)
        body = "### Charlie\ntier: Manager\ntrajectory: stable\n"
        _write_state(work_dir, "work-people.md", {"last_updated": _fresh_ts()}, body)
        out = cmd_graph()
        assert "stakeholder" in out.lower()

    def test_graph_trajectory_icons_present(self, work_dir: Path):
        _inject_state_dir(work_dir)
        body = textwrap.dedent("""\
            ### Person A
            tier: Director
            trajectory: warming

            ### Person B
            tier: Manager
            trajectory: cooling
        """)
        _write_state(work_dir, "work-people.md", {"last_updated": _fresh_ts()}, body)
        out = cmd_graph()
        assert "↑" in out
        assert "↓" in out


# ===========================================================================
# Phase 5 item 8 — Pre-read tracking
# ===========================================================================


class TestPrereadTracking:
    def test_mark_preread_missing_notes(self, work_dir: Path):
        _inject_state_dir(work_dir)
        out = cmd_mark_preread("Q2 Planning sync")
        assert "Pre-read can only be marked" in out or "✅" in out

    def test_mark_preread_creates_section(self, work_dir: Path):
        _inject_state_dir(work_dir)
        notes_path = work_dir / "work-notes.md"
        notes_path.write_text(
            "---\nlast_updated: '2026-03-25'\n---\n\n## Post-meeting notes\n\nSome content.\n",
            encoding="utf-8",
        )
        out = cmd_mark_preread("Sprint Planning")
        assert "✅" in out
        assert "Sprint Planning" in out
        content = notes_path.read_text(encoding="utf-8")
        assert _PREREAD_SECTION in content
        assert "Sprint Planning" in content

    def test_mark_preread_appends_to_table(self, work_dir: Path):
        _inject_state_dir(work_dir)
        notes_path = work_dir / "work-notes.md"
        init = (
            "---\nlast_updated: '2026-03-25'\n---\n\n"
            "## Pre-Read Log\n"
            "<!-- Auto-managed by /work preread -->\n"
            "| Meeting | Marked | Artifacts |\n"
            "|---------|--------|-----------|\n"
            "| Old Meeting | 2026-03-24 10:00 | 0 |\n"
        )
        notes_path.write_text(init, encoding="utf-8")
        cmd_mark_preread("New Meeting")
        content = notes_path.read_text(encoding="utf-8")
        assert "New Meeting" in content
        assert "Old Meeting" in content

    def test_load_preread_markers_empty(self, work_dir: Path):
        _inject_state_dir(work_dir)
        markers = _load_preread_markers()
        assert isinstance(markers, dict)

    def test_load_preread_markers_reads_entries(self, work_dir: Path):
        _inject_state_dir(work_dir)
        notes_path = work_dir / "work-notes.md"
        content = (
            "---\nlast_updated: '2026-03-25'\n---\n\n"
            "## Pre-Read Log\n"
            "| Meeting | Marked | Artifacts |\n"
            "|---------|--------|-----------|\n"
            "| sprint planning | 2026-03-25 09:00 | 0 |\n"
        )
        notes_path.write_text(content, encoding="utf-8")
        markers = _load_preread_markers()
        assert "sprint planning" in markers
        assert "2026-03-25" in markers["sprint planning"]

    def test_mark_preread_empty_id_returns_usage(self, work_dir: Path):
        _inject_state_dir(work_dir)
        out = cmd_mark_preread("")
        assert "Usage" in out


# ===========================================================================
# Phase 5 item 4 — Incidents / repos scaffold
# ===========================================================================


class TestCmdIncidents:
    def test_missing_file_shows_setup_instructions(self, work_dir: Path):
        _inject_state_dir(work_dir)
        out = cmd_incidents()
        assert "WORK INCIDENTS" in out
        assert "Agency ICM MCP" in out or "agency mcp" in out.lower()

    def test_with_scaffold_file_renders(self, work_dir: Path):
        _inject_state_dir(work_dir)
        _write_state(
            work_dir,
            "work-incidents.md",
            {"last_updated": _fresh_ts(), "domain": "work-incidents"},
            "## Active Incidents\n\nNo active incidents.\n",
        )
        out = cmd_incidents()
        assert "WORK INCIDENTS" in out
        assert "Active Incidents" in out

    def test_stale_file_shows_warning(self, work_dir: Path):
        _inject_state_dir(work_dir)
        _write_state(
            work_dir,
            "work-incidents.md",
            {"last_updated": _stale_ts(), "domain": "work-incidents"},
            "## Active Incidents\n\nSome incident data.\n",
        )
        out = cmd_incidents()
        assert "stale" in out.lower() or "⚠" in out

    def test_empty_body_shows_no_incidents(self, work_dir: Path):
        _inject_state_dir(work_dir)
        _write_state(
            work_dir,
            "work-incidents.md",
            {"last_updated": _fresh_ts(), "domain": "work-incidents"},
            "",
        )
        out = cmd_incidents()
        assert "No active incidents" in out


class TestCmdRepos:
    def test_missing_file_shows_setup_instructions(self, work_dir: Path):
        _inject_state_dir(work_dir)
        out = cmd_repos()
        assert "WORK REPOS" in out
        assert "Agency Bluebird MCP" in out or "agency mcp" in out.lower()

    def test_with_scaffold_file_renders(self, work_dir: Path):
        _inject_state_dir(work_dir)
        _write_state(
            work_dir,
            "work-repos.md",
            {"last_updated": _fresh_ts(), "domain": "work-repos"},
            "## Active Pull Requests\n\nNo open PRs.\n",
        )
        out = cmd_repos()
        assert "WORK REPOS" in out
        assert "Active Pull Requests" in out

    def test_stale_file_shows_warning(self, work_dir: Path):
        _inject_state_dir(work_dir)
        _write_state(
            work_dir,
            "work-repos.md",
            {"last_updated": _stale_ts(), "domain": "work-repos"},
            "## Active Pull Requests\n\nSome PR data.\n",
        )
        out = cmd_repos()
        assert "stale" in out.lower() or "⚠" in out

    def test_empty_body_shows_no_repo_data(self, work_dir: Path):
        _inject_state_dir(work_dir)
        _write_state(
            work_dir,
            "work-repos.md",
            {"last_updated": _fresh_ts(), "domain": "work-repos"},
            "",
        )
        out = cmd_repos()
        assert "No repository data" in out


# ===========================================================================
# Phase 4 item 5 — Prompt linter
# ===========================================================================


class TestPromptLinter:
    def _run_linter(self, prompts_dir: Path):
        import prompt_linter  # type: ignore
        return prompt_linter.lint_all(prompts_dir)

    def test_clean_prompt_passes(self, tmp_path: Path):
        p = tmp_path / "work-test.md"
        p.write_text(
            "---\napplyTo: state/work/work-test.md\n---\n\nRead `state/work/work-test.md` first.\n",
            encoding="utf-8",
        )
        errors = self._run_linter(tmp_path)
        stale_errors = [e for e in errors if "stale" in e or "non-canonical" in e]
        assert len(stale_errors) == 0

    def test_stale_root_path_detected(self, tmp_path: Path):
        p = tmp_path / "work-comms.md"
        p.write_text(
            "---\n---\n\nRead `state/work-comms.md` first.\n",
            encoding="utf-8",
        )
        errors = self._run_linter(tmp_path)
        assert any("stale path" in e for e in errors)

    def test_non_canonical_path_detected(self, tmp_path: Path):
        p = tmp_path / "work-comms.md"
        p.write_text(
            "---\n---\n\nSee also state/work-comms.md for reference.\n",
            encoding="utf-8",
        )
        errors = self._run_linter(tmp_path)
        # Both stale-path and non-canonical checks should fire
        assert len([e for e in errors if "canonical" in e or "stale" in e]) > 0

    def test_non_work_file_skipped(self, tmp_path: Path):
        p = tmp_path / "finance.md"
        p.write_text("---\n---\n\nSome finance content.\n", encoding="utf-8")
        errors = self._run_linter(tmp_path)
        assert errors == []

    def test_missing_separator_detected(self, tmp_path: Path):
        p = tmp_path / "work-test.md"
        p.write_text("No frontmatter, no separator. Just text.\n", encoding="utf-8")
        errors = self._run_linter(tmp_path)
        assert any("separator" in e or "frontmatter" in e for e in errors)

    def test_empty_directory_passes(self, tmp_path: Path):
        errors = self._run_linter(tmp_path)
        assert errors == []


# ===========================================================================
# CLI dispatch — new commands should be in choices
# ===========================================================================


class TestMainDispatchNewCommands:
    def test_graph_in_choices(self, work_dir: Path):
        """main() should accept --command graph without error."""
        import io, contextlib

        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            rc = main(["--command", "graph", "--state-dir", str(work_dir)])
        assert rc == 0
        assert "STAKEHOLDER GRAPH" in f.getvalue()

    def test_incidents_in_choices(self, work_dir: Path):
        out_lines: list[str] = []
        import io, contextlib

        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            rc = main(["--command", "incidents", "--state-dir", str(work_dir)])
        assert rc == 0
        assert "WORK INCIDENTS" in f.getvalue()

    def test_repos_in_choices(self, work_dir: Path):
        out_lines: list[str] = []
        import io, contextlib

        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            rc = main(["--command", "repos", "--state-dir", str(work_dir)])
        assert rc == 0
        assert "WORK REPOS" in f.getvalue()

    def test_preread_id_arg_accepted(self, work_dir: Path):
        import io, contextlib

        notes_path = work_dir / "work-notes.md"
        notes_path.write_text(
            "---\nlast_updated: '2026-03-25'\n---\n\n# Notes\n",
            encoding="utf-8",
        )
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            rc = main(
                ["--command", "preread", "--preread-id", "My Meeting", "--state-dir", str(work_dir)]
            )
        assert rc == 0
        assert "My Meeting" in f.getvalue() or "Pre-read" in f.getvalue()
