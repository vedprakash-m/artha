"""tests/unit/test_checkpoint.py — Unit tests for scripts/checkpoint.py

Phase 4 verification suite (specs/agentic-improve.md).

Coverage:
  - write_checkpoint() creates the file with correct schema
  - read_checkpoint() returns data for fresh checkpoints
  - read_checkpoint() returns None for stale (>4h) checkpoints
  - read_checkpoint() returns None when file is absent
  - read_checkpoint() returns None when file has invalid JSON
  - clear_checkpoint() removes the file
  - clear_checkpoint() is safe when the file does not exist
  - Metadata kwargs are stored and retrieved
  - Feature flag disabled: write is no-op, read returns None
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest adds scripts/ to sys.path
from checkpoint import (
    _CHECKPOINT_FILE,
    _MAX_AGE_HOURS,
    clear_checkpoint,
    read_checkpoint,
    write_checkpoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_raw(artha_dir: Path, content: str) -> None:
    """Write a raw string to the checkpoint file (bypasses write_checkpoint logic)."""
    path = artha_dir / _CHECKPOINT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _with_flag(enabled: bool):
    """Context manager: patch _is_enabled to return a fixed value."""
    return patch("checkpoint._is_enabled", return_value=enabled)


# ---------------------------------------------------------------------------
# write_checkpoint
# ---------------------------------------------------------------------------


class TestWriteCheckpoint:
    def test_creates_checkpoint_file(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4)
        assert (tmp_path / _CHECKPOINT_FILE).exists()

    def test_file_contains_last_step(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 7)
        data = json.loads((tmp_path / _CHECKPOINT_FILE).read_text())
        assert data["last_step"] == 7

    def test_file_contains_timestamp(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4)
        data = json.loads((tmp_path / _CHECKPOINT_FILE).read_text())
        assert "timestamp" in data
        # Should be parseable as ISO timestamp
        ts = datetime.fromisoformat(data["timestamp"])
        assert ts is not None

    def test_metadata_kwargs_stored(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4, email_count=42, domains=["finance", "immigration"])
        data = json.loads((tmp_path / _CHECKPOINT_FILE).read_text())
        assert data["email_count"] == 42
        assert data["domains"] == ["finance", "immigration"]

    def test_creates_tmp_dir_if_missing(self, tmp_path):
        assert not (tmp_path / "tmp").exists()
        with _with_flag(True):
            write_checkpoint(tmp_path, 0)
        assert (tmp_path / "tmp").exists()

    def test_overwrites_previous_checkpoint(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4)
            write_checkpoint(tmp_path, 7, ooda_completed=True)
        data = json.loads((tmp_path / _CHECKPOINT_FILE).read_text())
        assert data["last_step"] == 7
        assert data["ooda_completed"] is True

    def test_fractional_step_stored(self, tmp_path):
        """Step 4.5 (state load) is a valid fractional step."""
        with _with_flag(True):
            write_checkpoint(tmp_path, 4.5, domains_loaded=["finance"])
        data = json.loads((tmp_path / _CHECKPOINT_FILE).read_text())
        assert data["last_step"] == 4.5

    def test_flag_disabled_is_noop(self, tmp_path):
        with _with_flag(False):
            write_checkpoint(tmp_path, 4)
        assert not (tmp_path / _CHECKPOINT_FILE).exists()


# ---------------------------------------------------------------------------
# read_checkpoint
# ---------------------------------------------------------------------------


class TestReadCheckpoint:
    def test_returns_none_when_file_absent(self, tmp_path):
        with _with_flag(True):
            result = read_checkpoint(tmp_path)
        assert result is None

    def test_returns_data_for_fresh_checkpoint(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4, email_count=10)
            result = read_checkpoint(tmp_path)
        assert result is not None
        assert result["last_step"] == 4

    def test_returns_metadata(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 7, domains_processed=["finance"])
            result = read_checkpoint(tmp_path)
        assert result["domains_processed"] == ["finance"]

    def test_returns_none_for_stale_checkpoint(self, tmp_path):
        """Checkpoint older than _MAX_AGE_HOURS is ignored."""
        stale_ts = (
            datetime.now(timezone.utc) - timedelta(hours=_MAX_AGE_HOURS + 1)
        ).isoformat()
        _write_raw(tmp_path, json.dumps({"last_step": 4, "timestamp": stale_ts}))
        with _with_flag(True):
            result = read_checkpoint(tmp_path)
        assert result is None

    def test_returns_data_for_just_under_max_age(self, tmp_path):
        """Checkpoint just under the age limit is still valid."""
        fresh_ts = (
            datetime.now(timezone.utc) - timedelta(hours=_MAX_AGE_HOURS - 0.5)
        ).isoformat()
        _write_raw(tmp_path, json.dumps({"last_step": 4, "timestamp": fresh_ts}))
        with _with_flag(True):
            result = read_checkpoint(tmp_path)
        assert result is not None

    def test_returns_none_for_invalid_json(self, tmp_path):
        _write_raw(tmp_path, "THIS IS NOT JSON {{{")
        with _with_flag(True):
            result = read_checkpoint(tmp_path)
        assert result is None

    def test_returns_none_for_missing_timestamp(self, tmp_path):
        _write_raw(tmp_path, json.dumps({"last_step": 4}))  # no timestamp
        with _with_flag(True):
            result = read_checkpoint(tmp_path)
        assert result is None

    def test_returns_none_when_flag_disabled(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4)  # write with flag on
        with _with_flag(False):
            result = read_checkpoint(tmp_path)  # read with flag off
        assert result is None


# ---------------------------------------------------------------------------
# clear_checkpoint
# ---------------------------------------------------------------------------


class TestClearCheckpoint:
    def test_removes_checkpoint_file(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4)
        assert (tmp_path / _CHECKPOINT_FILE).exists()
        clear_checkpoint(tmp_path)
        assert not (tmp_path / _CHECKPOINT_FILE).exists()

    def test_safe_when_file_absent(self, tmp_path):
        """clear_checkpoint should not raise when the file does not exist."""
        # Should not raise
        clear_checkpoint(tmp_path)

    def test_safe_to_call_twice(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4)
        clear_checkpoint(tmp_path)
        clear_checkpoint(tmp_path)  # Should not raise

    def test_read_returns_none_after_clear(self, tmp_path):
        with _with_flag(True):
            write_checkpoint(tmp_path, 4)
            clear_checkpoint(tmp_path)
            result = read_checkpoint(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# Integration: write → read → clear cycle
# ---------------------------------------------------------------------------


class TestCheckpointCycle:
    def test_full_write_read_clear_cycle(self, tmp_path):
        """Simulate Steps 4 → 7 → 8 → 18 workflow."""
        with _with_flag(True):
            # Step 4 complete
            write_checkpoint(tmp_path, 4, email_count=35)
            cp = read_checkpoint(tmp_path)
            assert cp["last_step"] == 4

            # Step 7 complete (overwrites)
            write_checkpoint(tmp_path, 7, domains_processed=["finance", "immigration"])
            cp = read_checkpoint(tmp_path)
            assert cp["last_step"] == 7

            # Step 8 complete (OODA done)
            write_checkpoint(tmp_path, 8, ooda_completed=True)
            cp = read_checkpoint(tmp_path)
            assert cp["ooda_completed"] is True

            # Step 18 cleanup
            clear_checkpoint(tmp_path)
            cp = read_checkpoint(tmp_path)
            assert cp is None


# ---------------------------------------------------------------------------
# Session recap (S-03)
# ---------------------------------------------------------------------------


class TestSessionRecap:
    def test_write_session_recap_creates_yaml(self, tmp_path):
        """write_session_recap() creates tmp/_session_recap.yaml with correct fields."""
        import yaml
        from checkpoint import write_session_recap

        result = write_session_recap(
            tmp_path,
            worked_on=["immigration status", "finance review"],
            status_changes=["GC application filed"],
            decisions=["use attorney for I-485"],
            next_actions=["follow up with lawyer"],
        )
        assert result.exists()
        assert result.name == "_session_recap.yaml"
        data = yaml.safe_load(result.read_text(encoding="utf-8"))
        assert data["worked_on"] == ["immigration status", "finance review"]
        assert "written_at" in data

    def test_read_session_recap_returns_fresh(self, tmp_path):
        """read_session_recap() returns dict for a freshly written recap."""
        from checkpoint import write_session_recap, read_session_recap

        write_session_recap(
            tmp_path,
            worked_on=["goals sprint"],
            status_changes=[],
            decisions=[],
            next_actions=["review goals"],
        )
        result = read_session_recap(tmp_path)
        assert result is not None
        assert isinstance(result, dict)
        assert result["worked_on"] == ["goals sprint"]

    def test_read_session_recap_stale_returns_none(self, tmp_path):
        """read_session_recap() returns None when recap is older than 48h."""
        import yaml
        from datetime import datetime, timezone, timedelta
        from checkpoint import read_session_recap, _SESSION_RECAP_TTL_HOURS

        stale_ts = (
            datetime.now(timezone.utc) - timedelta(hours=_SESSION_RECAP_TTL_HOURS + 1)
        ).isoformat()
        recap = {
            "written_at": stale_ts,
            "worked_on": ["old work"],
            "status_changes": [],
            "decisions": [],
            "next_actions": [],
        }
        recap_path = tmp_path / "tmp" / "_session_recap.yaml"
        recap_path.parent.mkdir(parents=True, exist_ok=True)
        recap_path.write_text(yaml.dump(recap), encoding="utf-8")

        result = read_session_recap(tmp_path)
        assert result is None

    def test_session_recap_under_500_tokens(self, tmp_path):
        """A realistic session recap must serialize to ≤500 tokens (A-05)."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))
        from context_budget import estimate_token_count
        from checkpoint import write_session_recap

        path = write_session_recap(
            tmp_path,
            worked_on=["immigration status check", "finance review", "goals sprint planning"],
            status_changes=["Filed I-485", "Paid Q1 taxes"],
            decisions=["Use USCIS attorney", "Skip Europe trip in Q3"],
            next_actions=[
                "Follow up with lawyer by Friday",
                "Review open items list",
                "Check visa validity",
            ],
        )
        token_count = estimate_token_count(path.read_text(encoding="utf-8"))
        assert token_count <= 500, f"Session recap uses {token_count} tokens (max 500)"

    def test_session_recap_no_pii(self, tmp_path):
        """Session recap must not contain raw email addresses or phone numbers."""
        import re
        from checkpoint import write_session_recap

        path = write_session_recap(
            tmp_path,
            worked_on=["reviewed work items"],
            status_changes=["item closed"],
            decisions=["proceed with plan"],
            next_actions=["check email tomorrow"],
        )
        content = path.read_text(encoding="utf-8")
        email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        phone_re = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
        assert not email_re.search(content), "Email address found in session recap"
        assert not phone_re.search(content), "Phone number found in session recap"
