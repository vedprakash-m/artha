"""tests/unit/test_channel_stage.py — T4-76..80: channel.stage tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.stage import (
    cmd_stage,
    cmd_radar,
    cmd_radar_try,
    cmd_radar_skip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _is_reply_tuple(val) -> bool:
    return (
        isinstance(val, tuple)
        and len(val) == 2
        and isinstance(val[0], str)
        and isinstance(val[1], str)
    )


# ---------------------------------------------------------------------------
# T4-76: cmd_stage — list/preview/approve modes all return reply tuple
# ---------------------------------------------------------------------------

class TestCmdStage:
    def test_list_returns_tuple(self):
        result = _run(cmd_stage([], "full"))
        assert _is_reply_tuple(result)

    def test_preview_returns_tuple(self):
        result = _run(cmd_stage(["preview", "CARD-001"], "full"))
        assert _is_reply_tuple(result)

    def test_approve_invalid_id(self):
        result = _run(cmd_stage(["approve", "CARD-FAKE"], "full"))
        assert _is_reply_tuple(result)

    def test_text_is_non_empty(self):
        text, _ = _run(cmd_stage([], "full"))
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# T4-77: cmd_radar
# ---------------------------------------------------------------------------

class TestCmdRadar:
    def test_returns_tuple(self):
        result = _run(cmd_radar([], "full"))
        assert _is_reply_tuple(result)

    def test_text_non_empty(self):
        text, _ = _run(cmd_radar([], "full"))
        assert isinstance(text, str)

    def test_topic_subcommand(self):
        result = _run(cmd_radar(["topic", "list"], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-78: cmd_radar_try
# ---------------------------------------------------------------------------

class TestCmdRadarTry:
    def test_returns_tuple_no_args(self):
        result = _run(cmd_radar_try([], "full"))
        assert _is_reply_tuple(result)

    def test_with_signal_id(self):
        result = _run(cmd_radar_try(["SIG-001"], "full"))
        assert _is_reply_tuple(result)


# ---------------------------------------------------------------------------
# T4-79: cmd_radar_skip
# ---------------------------------------------------------------------------

class TestCmdRadarSkip:
    def test_returns_tuple_no_args(self):
        result = _run(cmd_radar_skip([], "full"))
        assert _is_reply_tuple(result)

    def test_with_signal_id(self):
        result = _run(cmd_radar_skip(["SIG-001"], "full"))
        assert _is_reply_tuple(result)
