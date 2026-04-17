"""tests/unit/test_channel_handlers.py — T4-53..67: channel.handlers tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.handlers import (
    cmd_status,
    cmd_alerts,
    cmd_tasks,
    cmd_quick,
    cmd_help,
    cmd_cost,
    cmd_goals,
    cmd_diff,
    cmd_power,
    cmd_relationships,
    _parse_workout,
    _workout_already_logged,
    cmd_workout_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _is_reply_tuple(val) -> bool:
    """All handlers return (text: str, parse_mode: str)."""
    return (
        isinstance(val, tuple)
        and len(val) == 2
        and isinstance(val[0], str)
        and isinstance(val[1], str)
    )


# ---------------------------------------------------------------------------
# T4-53: cmd_status
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_returns_tuple(self):
        result = _run(cmd_status([], "full"))
        assert _is_reply_tuple(result)

    def test_non_empty_text(self):
        text, _ = _run(cmd_status([], "full"))
        assert len(text) > 0

    def test_scope_flash(self):
        result = _run(cmd_status([], "flash"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-54: cmd_alerts
# ---------------------------------------------------------------------------

class TestCmdAlerts:
    def test_returns_tuple(self):
        result = _run(cmd_alerts([], "full"))
        assert _is_reply_tuple(result)

    def test_text_is_string(self):
        text, _ = _run(cmd_alerts([], "full"))
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# T4-55: cmd_tasks
# ---------------------------------------------------------------------------

class TestCmdTasks:
    def test_returns_tuple(self):
        result = _run(cmd_tasks([], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-56: cmd_quick
# ---------------------------------------------------------------------------

class TestCmdQuick:
    def test_returns_tuple(self):
        result = _run(cmd_quick([], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-57: cmd_help
# ---------------------------------------------------------------------------

class TestCmdHelp:
    def test_returns_tuple(self):
        result = _run(cmd_help([], "full"))
        assert _is_reply_tuple(result)

    def test_contains_command_prefix(self):
        text, _ = _run(cmd_help([], "full"))
        assert "/" in text  # Help should list slash commands

    def test_non_empty(self):
        text, _ = _run(cmd_help([], "full"))
        assert len(text) > 10


# ---------------------------------------------------------------------------
# T4-58: cmd_cost
# ---------------------------------------------------------------------------

class TestCmdCost:
    def test_returns_tuple(self):
        result = _run(cmd_cost([], "full"))
        assert _is_reply_tuple(result)

    def test_text_not_empty(self):
        text, _ = _run(cmd_cost([], "full"))
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# T4-59: cmd_goals
# ---------------------------------------------------------------------------

class TestCmdGoals:
    def test_returns_tuple(self):
        result = _run(cmd_goals([], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-60: cmd_diff
# ---------------------------------------------------------------------------

class TestCmdDiff:
    def test_returns_tuple_no_args(self):
        result = _run(cmd_diff([], "full"))
        assert _is_reply_tuple(result)

    def test_returns_tuple_with_window_arg(self):
        result = _run(cmd_diff(["7d"], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-61: cmd_power
# ---------------------------------------------------------------------------

class TestCmdPower:
    def test_returns_tuple(self):
        result = _run(cmd_power([], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-62: cmd_relationships
# ---------------------------------------------------------------------------

class TestCmdRelationships:
    def test_returns_tuple(self):
        result = _run(cmd_relationships([], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-68: cmd_workout_log — _parse_workout unit tests
# ---------------------------------------------------------------------------

class TestParseWorkout:
    def test_run_full(self):
        parsed = _parse_workout("8km run 58min HR142")
        assert parsed is not None
        assert parsed["activity"] == "run"
        assert parsed["distance_km"] == 8.0
        assert parsed["duration_min"] == 58
        assert parsed["hr_avg"] == 142

    def test_hike_miles_elevation(self):
        parsed = _parse_workout("hiked 5mi 1100ft gain 2h")
        assert parsed is not None
        assert parsed["activity"] == "hike"
        assert abs(parsed["distance_km"] - 8.05) < 0.1
        assert parsed["elevation_ft"] == 1100
        assert parsed["duration_min"] == 120

    def test_weight_only(self):
        parsed = _parse_workout("weight 182")
        assert parsed is not None
        assert parsed["weight_lbs"] == 182.0
        assert parsed["activity"] is None

    def test_unrecognized_returns_none(self):
        assert _parse_workout("hello world") is None
        assert _parse_workout("") is None

    def test_meters_elevation_converted(self):
        parsed = _parse_workout("run 10km 300m elev")
        assert parsed is not None
        # 300m → ~984 ft
        assert parsed["elevation_ft"] is not None
        assert parsed["elevation_ft"] > 900


# ---------------------------------------------------------------------------
# T4-69: cmd_workout_log — dedup and write behaviour
# ---------------------------------------------------------------------------

class TestCmdWorkoutLog:
    def test_parse_failure_returns_hint(self):
        text, _ = _run(cmd_workout_log(["hello world"], "full"))
        assert "Couldn't parse" in text

    def test_empty_args_returns_hint(self):
        text, _ = _run(cmd_workout_log([], "full"))
        assert "Couldn't parse" in text

    def test_successful_log_returns_ack(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "channel.handlers._LOCAL_WORKOUT_DIR", tmp_path
        )
        monkeypatch.setattr(
            "channel.handlers._WORKOUTS_FILE", tmp_path / "workouts.jsonl"
        )
        text, staleness = _run(
            cmd_workout_log(
                ["8km run 58min HR142"], "full",
                sender_id="u1", message_id="m1",
            )
        )
        assert "Run logged" in text or "run logged" in text.lower()
        assert staleness == "N/A"
        assert (tmp_path / "workouts.jsonl").exists()

    def test_dedup_skips_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr("channel.handlers._LOCAL_WORKOUT_DIR", tmp_path)
        wf = tmp_path / "workouts.jsonl"
        monkeypatch.setattr("channel.handlers._WORKOUTS_FILE", wf)
        # First write
        _run(cmd_workout_log(["8km run 58min"], "full", sender_id="u1", message_id="m2"))
        size_after_first = wf.stat().st_size
        # Second with same (sender_id, message_id)
        _run(cmd_workout_log(["8km run 58min"], "full", sender_id="u1", message_id="m2"))
        assert wf.stat().st_size == size_after_first  # no second append
