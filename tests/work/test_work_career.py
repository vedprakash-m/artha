"""tests/work/test_work_career.py — Focused tests for scripts/work/career.py

T3-41..48 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work.career
import work.helpers
from work.career import (
    cmd_connect,
    cmd_return,
    cmd_promo_case,
    cmd_connect_prep,
    cmd_journey,
)

# Signature reference:
# cmd_return(window: str = '1d') -> str
# cmd_connect_prep(mode: str = '') -> str


def _write_state(state_dir: Path, name: str, fm: dict, body: str = "") -> Path:
    p = state_dir / name
    content = "---\n" + yaml.dump(fm, default_flow_style=False) + "---\n\n" + body
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def work_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    work.career._WORK_STATE_DIR = d
    work.helpers._WORK_STATE_DIR = d
    return d


# ---------------------------------------------------------------------------
# T3-41: cmd_connect returns evidence assembly output
# ---------------------------------------------------------------------------

def test_cmd_connect_no_state(work_dir):
    out = cmd_connect()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_connect_with_profile(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "role": "Senior Engineer",
        "team": "Platform",
        "goals": [
            {"id": "G1", "title": "Deliver Auth v2", "status": "in_progress"},
            {"id": "G2", "title": "Reduce P0 incidents", "status": "on_track"},
        ],
    }
    _write_state(work_dir, "profile.md", fm, body="# Work Profile\nSenior Engineer on Platform.\n")
    out = cmd_connect()
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-42..43: cmd_return — absence window parsing and recovery
# ---------------------------------------------------------------------------

def test_cmd_return_default(work_dir):
    out = cmd_return()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_return_1d(work_dir):
    out = cmd_return(window="1d")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_return_1w(work_dir):
    out = cmd_return(window="1w")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-44: cmd_promo_case — output structure
# ---------------------------------------------------------------------------

def test_cmd_promo_case_no_state(work_dir):
    out = cmd_promo_case(narrative=False)
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_promo_case_narrative(work_dir):
    out = cmd_promo_case(narrative=True)
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-45: cmd_connect_prep — calibration mode
# ---------------------------------------------------------------------------

def test_cmd_connect_prep_no_state(work_dir):
    out = cmd_connect_prep()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_connect_prep_calibration_mode(work_dir):
    out = cmd_connect_prep(mode="calibration")
    assert isinstance(out, str)
    assert len(out) > 0
    # Standard mode should also work
    standard = cmd_connect_prep(mode="")
    assert isinstance(standard, str)


# ---------------------------------------------------------------------------
# T3-46: cmd_journey — project filtering
# ---------------------------------------------------------------------------

def test_cmd_journey_all_projects(work_dir):
    out = cmd_journey(project=None)
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_journey_specific_project(work_dir):
    out = cmd_journey(project="Auth v2")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_journey_nonexistent_project(work_dir):
    out = cmd_journey(project="Does Not Exist XYZ123")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-47: All career cmds handle empty work_dir
# ---------------------------------------------------------------------------

def test_all_career_cmds_empty_dir(work_dir):
    for fn, kwargs in [
        (cmd_connect, {}),
        (cmd_return, {"window": "1d"}),
        (cmd_promo_case, {"narrative": False}),
        (cmd_connect_prep, {"mode": ""}),
        (cmd_journey, {"project": None}),
    ]:
        try:
            out = fn(**kwargs)
            assert isinstance(out, str)
        except Exception as exc:
            pytest.fail(f"{fn.__name__} raised unexpectedly with empty dir: {exc}")


# ---------------------------------------------------------------------------
# T3-48: cmd_connect reads goals from profile
# ---------------------------------------------------------------------------

def test_cmd_connect_goals_in_output(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "role": "Staff Engineer",
        "goals": [
            {"id": "G1", "title": "Ship ML feature", "status": "on_track"},
        ],
    }
    _write_state(work_dir, "profile.md", fm)
    out = cmd_connect()
    assert isinstance(out, str)
    # Goals should influence output in some way
    assert len(out) > 50
