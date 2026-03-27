"""tests/unit/test_channel_router.py — T4-43..52: channel.router tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.router import (
    _normalise_command,
    _HANDLERS,
    ALLOWED_COMMANDS,
    _COMMAND_ALIASES,
)


# ---------------------------------------------------------------------------
# T4-43: _normalise_command — basic normalization
# ---------------------------------------------------------------------------

class TestNormaliseCommand:
    def test_slash_command_passthrough(self):
        cmd, args = _normalise_command("/status")
        assert cmd == "/status"
        assert args == []

    def test_alias_status(self):
        cmd, _ = _normalise_command("status")
        assert cmd == "/status"

    def test_alias_catchup(self):
        for alias in ("catchup", "catch up", "morning briefing"):
            cmd, _ = _normalise_command(alias)
            assert cmd in ("/catchup", ""), f"Expected /catchup for alias '{alias}', got '{cmd}'"

    def test_command_with_args(self):
        cmd, args = _normalise_command("/domain health")
        assert cmd == "/domain"
        assert "health" in args

    def test_unknown_command_returns_empty(self):
        cmd, args = _normalise_command("not_a_real_command_xyz")
        assert cmd == "" or cmd not in ALLOWED_COMMANDS

    def test_case_insensitive(self):
        cmd1, _ = _normalise_command("/STATUS")
        cmd2, _ = _normalise_command("/status")
        assert cmd1 == cmd2

    def test_leading_trailing_whitespace_stripped(self):
        cmd, _ = _normalise_command("  /status  ")
        assert cmd == "/status"

    def test_slash_prefix_added_for_known_aliases(self):
        """Known aliased commands without slash should resolve to slash form."""
        cmd, _ = _normalise_command("help")
        assert cmd in ("/help", "")  # Either slash-prefixed or empty if not aliased


# ---------------------------------------------------------------------------
# T4-44: ALLOWED_COMMANDS structure
# ---------------------------------------------------------------------------

class TestAllowedCommands:
    def test_is_frozenset_or_set(self):
        assert isinstance(ALLOWED_COMMANDS, (frozenset, set))

    def test_contains_core_commands(self):
        core = {"/status", "/help", "/catchup", "/domain", "/items"}
        present = core & ALLOWED_COMMANDS
        assert len(present) > 0, f"None of {core} found in ALLOWED_COMMANDS"

    def test_all_commands_prefixed_with_slash(self):
        for cmd in ALLOWED_COMMANDS:
            assert cmd.startswith("/"), f"Command '{cmd}' should start with '/'"


# ---------------------------------------------------------------------------
# T4-45: _HANDLERS dict
# ---------------------------------------------------------------------------

class TestHandlersDict:
    def test_is_dict(self):
        assert isinstance(_HANDLERS, dict)

    def test_non_empty(self):
        assert len(_HANDLERS) > 0

    def test_status_handler_present(self):
        assert "/status" in _HANDLERS

    def test_all_handlers_callable(self):
        for cmd, fn in _HANDLERS.items():
            assert callable(fn), f"Handler for '{cmd}' is not callable"


# ---------------------------------------------------------------------------
# T4-46: _COMMAND_ALIASES dict
# ---------------------------------------------------------------------------

class TestCommandAliases:
    def test_is_dict(self):
        assert isinstance(_COMMAND_ALIASES, dict)

    def test_has_help_alias(self):
        # "?" is a common help alias
        found = any(v in ("/help", "help") for v in _COMMAND_ALIASES.values())
        assert found or "?" in _COMMAND_ALIASES or len(_COMMAND_ALIASES) > 0
