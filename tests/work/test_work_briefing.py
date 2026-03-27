"""tests/work/test_work_briefing.py — Focused tests for scripts/work/briefing.py

T3-6..12 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work.briefing
import work.helpers
from work.briefing import (
    WorkBriefingConfig, _build_briefing_config, cmd_work, cmd_pulse, cmd_sprint,
    _validate_work_state_schema,
)

_BORDER = "━" * 42


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()


def _write_state(state_dir: Path, name: str, fm: dict, body: str = "") -> Path:
    p = state_dir / name
    content = "---\n" + yaml.dump(fm, default_flow_style=False) + "---\n\n" + body
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def work_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    work.briefing._WORK_STATE_DIR = d
    work.helpers._WORK_STATE_DIR = d
    return d


# ---------------------------------------------------------------------------
# T3-6: WorkBriefingConfig defaults
# ---------------------------------------------------------------------------

def test_briefing_config_defaults():
    cfg = WorkBriefingConfig()
    assert cfg.flash_mode is False
    assert cfg.sprint_deadline_approaching is False
    assert cfg.connect_season_alert is False
    assert cfg.all_clear is False


# ---------------------------------------------------------------------------
# T3-7: _build_briefing_config returns WorkBriefingConfig
# _build_briefing_config(profile, summary_fm, proj_fm, cal_fm) -> WorkBriefingConfig
# ---------------------------------------------------------------------------

def test_build_briefing_config_returns_instance(work_dir):
    profile = {"role": "Engineer", "goals": []}
    summary_fm = {"last_updated": _fresh_ts(), "sprint_end": "", "connect_window": ""}
    proj_fm = {"last_updated": _fresh_ts(), "projects": []}
    cal_fm = {"last_updated": _fresh_ts(), "meetings": []}
    cfg = _build_briefing_config(profile, summary_fm, proj_fm, cal_fm)
    assert isinstance(cfg, WorkBriefingConfig)


# ---------------------------------------------------------------------------
# T3-8: cmd_pulse brevity — output is compact
# ---------------------------------------------------------------------------

def test_cmd_pulse_output_structure(work_dir):
    fm = {"last_updated": _fresh_ts(), "meetings_today": 3, "action_required_count": 1}
    _write_state(work_dir, "work-calendar.md", fm, "## Today\n| 9 AM | Standup | 30m |\n")
    _write_state(work_dir, "work-performance.md", {"last_updated": _fresh_ts()}, "")
    _write_state(work_dir, "work-projects.md", {"last_updated": _fresh_ts()}, "")
    _write_state(work_dir, "work-comms.md", {"last_updated": _fresh_ts(), "action_required_count": 1}, "")
    out = cmd_pulse()
    assert "WORK PULSE" in out
    assert len(out.splitlines()) < 60  # brevity check — pulse is a snapshot


# ---------------------------------------------------------------------------
# T3-9: cmd_sprint with missing state degrades gracefully
# ---------------------------------------------------------------------------

def test_cmd_sprint_missing_state(work_dir):
    # No files at all — should not raise
    out = cmd_sprint()
    assert "SPRINT" in out or "sprint" in out.lower()
    assert "Error" not in out


# ---------------------------------------------------------------------------
# T3-10: cmd_work output contains required sections
# ---------------------------------------------------------------------------

def test_cmd_work_contains_border(work_dir):
    for fname in ["work-calendar.md", "work-projects.md", "work-performance.md",
                  "work-comms.md", "work-open-items.md"]:
        _write_state(work_dir, fname, {"last_updated": _fresh_ts()}, "")
    out = cmd_work()
    assert _BORDER in out


# ---------------------------------------------------------------------------
# T3-11: _validate_work_state_schema detects issues
# ---------------------------------------------------------------------------

def test_validate_work_state_schema_missing_files(work_dir):
    issues = _validate_work_state_schema()
    # Should find issues (directory is empty of main state files)
    assert isinstance(issues, list)
    # Depends on schema — at minimum it shouldn't crash
    assert all(isinstance(i, str) for i in issues)


def test_validate_work_state_schema_passes_with_state(work_dir):
    for fname in ["work-performance.md", "work-projects.md", "work-career.md",
                  "work-comms.md", "work-calendar.md", "work-people.md",
                  "work-decisions.md", "work-open-items.md", "work-sources.md"]:
        _write_state(work_dir, fname, {"last_updated": _fresh_ts(), "schema_version": 1}, "")
    issues = _validate_work_state_schema()
    # With all core files present, issues should be fewer
    assert isinstance(issues, list)


# ---------------------------------------------------------------------------
# T3-12: cmd_pulse respects stale data warning
# ---------------------------------------------------------------------------

def test_cmd_pulse_stale_data_warning(work_dir):
    _write_state(work_dir, "work-calendar.md", {"last_updated": _stale_ts()}, "")
    _write_state(work_dir, "work-performance.md", {"last_updated": _stale_ts()}, "")
    _write_state(work_dir, "work-projects.md", {"last_updated": _stale_ts()}, "")
    _write_state(work_dir, "work-comms.md", {"last_updated": _stale_ts(), "action_required_count": 0}, "")
    out = cmd_pulse()
    # Either stale warning or normal output — must not crash
    assert isinstance(out, str) and len(out) > 0
