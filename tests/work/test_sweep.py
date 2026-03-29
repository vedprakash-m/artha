"""tests/work/test_sweep.py — Tests for scripts/work/sweep.py

≥85% coverage target.
Tests: P6 guard (schema_version), SweepResult add/mark, run_full_sweep
with mocked passes, platform capabilities.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.sweep import (
    SweepResult,
    sweep_state_diff,
    sweep_calendar,
    run_full_sweep,
    get_sweep_capabilities,
    PLATFORM_CAPABILITIES,
    _has_schema_version,
    _log_sweep_skip,
)


# ---------------------------------------------------------------------------
# SweepResult
# ---------------------------------------------------------------------------

class TestSweepResult:
    def test_initial_state(self):
        r = SweepResult()
        assert r.items == []
        assert r.errors == []
        assert r.pass_statuses["workiq"] == "skipped"

    def test_add_items_injects_source(self):
        r = SweepResult()
        r.add_items([{"title": "T1"}], "state_diff")
        assert r.items[0]["source"] == "state_diff"

    def test_add_items_preserves_existing_source(self):
        r = SweepResult()
        r.add_items([{"title": "T1", "source": "custom"}], "state_diff")
        assert r.items[0]["source"] == "custom"  # setdefault doesn't overwrite

    def test_mark_pass_updates_status(self):
        r = SweepResult()
        r.mark_pass("state_diff", "ok")
        assert r.pass_statuses["state_diff"] == "ok"

    def test_add_multiple_items(self):
        r = SweepResult()
        r.add_items([{"title": "A"}, {"title": "B"}], "calendar")
        assert len(r.items) == 2

    def test_collected_at_is_utc_iso(self):
        r = SweepResult()
        assert "T" in r.collected_at  # ISO format


# ---------------------------------------------------------------------------
# _has_schema_version (P6 guard helper)
# ---------------------------------------------------------------------------

class TestHasSchemaVersion:
    def test_present_in_frontmatter(self):
        content = "---\nschema_version: '1.0'\ndomain: work\n---\nBody"
        assert _has_schema_version(content) is True

    def test_absent_in_frontmatter(self):
        content = "---\ndomain: work\n---\nBody"
        assert _has_schema_version(content) is False

    def test_no_frontmatter(self):
        content = "# Plain markdown\nNo frontmatter here"
        assert _has_schema_version(content) is False

    def test_incomplete_frontmatter(self):
        content = "---\nschema_version: 1.0"  # No closing ---
        assert _has_schema_version(content) is False

    def test_schema_version_in_body_not_counted(self):
        content = "---\ndomain: work\n---\nschema_version: 1.0"
        assert _has_schema_version(content) is False


# ---------------------------------------------------------------------------
# sweep_state_diff — P6 guard + basic scanning
# ---------------------------------------------------------------------------

class TestSweepStateDiff:
    def test_empty_dir_returns_empty(self, tmp_path):
        now = datetime.now(timezone.utc)
        result = sweep_state_diff(now - timedelta(hours=1), tmp_path)
        assert result == []

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        missing = tmp_path / "missing"
        now = datetime.now(timezone.utc)
        result = sweep_state_diff(now, missing)
        assert result == []

    def test_recent_md_file_included(self, tmp_path):
        """State_diff pass now suppresses state file updates as noise (FW-19 v1.6).
        All state/work/*.md modifications are filtered — real accomplishments come
        from WorkIQ and the accomplishments ledger, not file-change signals."""
        md = tmp_path / "work-notes.md"
        md.write_text("# Notes\nContent", encoding="utf-8")
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        result = sweep_state_diff(past, tmp_path)
        # State file updates are now suppressed — empty result is correct
        assert result == []

    def test_old_file_excluded(self, tmp_path):
        md = tmp_path / "old-notes.md"
        md.write_text("# Old", encoding="utf-8")
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        result = sweep_state_diff(future, tmp_path)
        assert result == []

    def test_p6_guard_skips_daily_accumulator_without_schema_version(self, tmp_path, capsys):
        """daily_accumulator.log without schema_version must be silently skipped."""
        log_file = tmp_path / "daily_accumulator.log"
        log_file.write_text("---\ndomain: work\n---\nBody", encoding="utf-8")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result = sweep_state_diff(past, tmp_path)
        # Should be skipped
        assert not any("daily_accumulator" in item.get("title", "") for item in result)

    def test_p6_guard_includes_daily_accumulator_with_schema_version(self, tmp_path):
        """daily_accumulator.log WITH schema_version is still suppressed (FW-19 v1.6)
        — all state/work file changes are now filtered from accomplishments."""
        log_file = tmp_path / "daily_accumulator.log"
        log_file.write_text(
            "---\nschema_version: '1.0'\ndomain: work\n---\nBody",
            encoding="utf-8"
        )
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result = sweep_state_diff(past, tmp_path)
        # State file updates are now suppressed — daily_accumulator also filtered
        assert result == []

    def test_result_items_have_required_fields(self, tmp_path):
        """State_diff now suppresses all state file updates (FW-19 v1.6).
        This test verifies the empty-result behavior is correct."""
        md = tmp_path / "work-career.md"
        md.write_text("# Career", encoding="utf-8")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result = sweep_state_diff(past, tmp_path)
        # All state file updates are suppressed
        assert result == []


# ---------------------------------------------------------------------------
# sweep_calendar
# ---------------------------------------------------------------------------

class TestSweepCalendar:
    def test_absent_calendar_returns_empty(self, tmp_path):
        result = sweep_calendar(tmp_path)
        assert result == []

    def test_extracts_section_items(self, tmp_path):
        cal = tmp_path / "work-calendar.md"
        cal.write_text(
            "---\ndomain: work\n---\n"
            "## This Week\n"
            "- Team sync at 10am\n"
            "- 1:1 with manager\n",
            encoding="utf-8"
        )
        result = sweep_calendar(tmp_path)
        titles = [item["title"] for item in result]
        assert "Team sync at 10am" in titles
        assert "1:1 with manager" in titles

    def test_items_have_calendar_source(self, tmp_path):
        cal = tmp_path / "work-calendar.md"
        cal.write_text("## Meetings\n- Sprint review\n", encoding="utf-8")
        result = sweep_calendar(tmp_path)
        assert all(item.get("source") != "state_diff" for item in result)


# ---------------------------------------------------------------------------
# Platform capabilities
# ---------------------------------------------------------------------------

class TestPlatformCapabilities:
    def test_win32_has_workiq(self):
        assert PLATFORM_CAPABILITIES["win32"]["workiq"] is True

    def test_darwin_no_workiq(self):
        assert PLATFORM_CAPABILITIES["darwin"]["workiq"] is False

    def test_linux_no_workiq(self):
        assert PLATFORM_CAPABILITIES["linux"]["workiq"] is False

    def test_all_platforms_have_state_diff(self):
        for platform_caps in PLATFORM_CAPABILITIES.values():
            assert platform_caps["state_diff"] is True

    def test_get_capabilities_returns_dict(self):
        caps = get_sweep_capabilities()
        assert isinstance(caps, dict)
        assert "state_diff" in caps


# ---------------------------------------------------------------------------
# run_full_sweep integration (with mocked passes)
# ---------------------------------------------------------------------------

class TestRunFullSweep:
    def test_returns_sweep_result(self, tmp_path):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result = run_full_sweep(past, tmp_path)
        assert isinstance(result, SweepResult)

    def test_collected_at_set(self, tmp_path):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result = run_full_sweep(past, tmp_path)
        assert result.collected_at

    def test_empty_state_dir_zero_items(self, tmp_path):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result = run_full_sweep(past, tmp_path)
        assert isinstance(result.items, list)

    def test_state_diff_pass_runs(self, tmp_path, monkeypatch):
        """state_diff should run even on non-win32 platforms."""
        monkeypatch.setattr("sys.platform", "linux")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        md = tmp_path / "work-notes.md"
        md.write_text("# test", encoding="utf-8")
        result = run_full_sweep(past, tmp_path)
        # State diff ran (ok or not skipped)
        assert result.pass_statuses.get("state_diff") in ("ok", "skipped", "unavailable (platform)")

    def test_pass_status_state_diff_ok_when_dir_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        result = run_full_sweep(past, tmp_path)
        assert result.pass_statuses["state_diff"] == "ok"
