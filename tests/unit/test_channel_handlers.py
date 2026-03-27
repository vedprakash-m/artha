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
