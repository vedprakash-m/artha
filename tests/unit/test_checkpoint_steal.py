"""
tests/unit/test_checkpoint_steal.py — Tests for S-03 session recap additions.
specs/steal.md §15.2.4
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

# Ensure scripts/ is on path (mirrors existing checkpoint test setup)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

from checkpoint import write_session_recap, read_session_recap


# ---------------------------------------------------------------------------
# write_session_recap tests
# ---------------------------------------------------------------------------

def test_write_creates_file(tmp_path):
    p = write_session_recap(
        tmp_path,
        worked_on=["task A"],
        status_changes=["item X: done"],
        decisions=["use YAML"],
        next_actions=["run tests"],
    )
    assert p.exists()
    assert p.name == "_session_recap.yaml"


def test_write_content_correct(tmp_path):
    write_session_recap(
        tmp_path,
        worked_on=["task A", "task B"],
        status_changes=["X: done"],
        decisions=["use YAML"],
        next_actions=["deploy"],
    )
    data = yaml.safe_load((tmp_path / "tmp" / "_session_recap.yaml").read_text())
    assert data["worked_on"] == ["task A", "task B"]
    assert data["decisions"] == ["use YAML"]
    assert "written_at" in data


# ---------------------------------------------------------------------------
# read_session_recap tests
# ---------------------------------------------------------------------------

def test_read_returns_data_when_fresh(tmp_path):
    write_session_recap(
        tmp_path,
        worked_on=["thing"],
        status_changes=[],
        decisions=[],
        next_actions=["next"],
    )
    result = read_session_recap(tmp_path)
    assert result is not None
    assert result["worked_on"] == ["thing"]


def test_read_returns_none_when_absent(tmp_path):
    assert read_session_recap(tmp_path) is None


def test_read_returns_none_when_stale(tmp_path):
    write_session_recap(
        tmp_path,
        worked_on=["old work"],
        status_changes=[],
        decisions=[],
        next_actions=[],
    )
    # Overwrite written_at with a timestamp >48h ago
    recap_path = tmp_path / "tmp" / "_session_recap.yaml"
    data = yaml.safe_load(recap_path.read_text())
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    data["written_at"] = old_ts
    recap_path.write_text(yaml.dump(data))

    assert read_session_recap(tmp_path) is None
