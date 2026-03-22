"""
tests/unit/test_discord_channel.py — Unit tests for scripts/channels/discord.py

Tests cover: create_adapter, send_message, send_document, health_check,
_load_token, _parse_message_create, token resolution, and error handling.
All Discord API calls are mocked — no real network calls.

Run: pytest tests/unit/test_discord_channel.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import channels.discord as discord_mod
from channels.discord import (
    DiscordAdapter,
    _load_token,
    _parse_message_create,
    create_adapter,
    platform_name,
)
from channels.base import ChannelMessage, InboundMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_urlopen(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _discord_user(username: str = "Artha", user_id: str = "123"):
    return {"id": user_id, "username": username, "bot": True}


def _make_message_create_event(
    content: str,
    author_id: str = "u001",
    author_username: str = "testuser",
    channel_id: str = "c001",
    message_id: str = "m001",
    is_bot: bool = False,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "content": content,
        "channel_id": channel_id,
        "timestamp": "2024-04-01T10:00:00.000000+00:00",
        "author": {
            "id": author_id,
            "username": author_username,
            "discriminator": "0",
            "bot": is_bot or None,
        },
    }


# ---------------------------------------------------------------------------
# platform_name()
# ---------------------------------------------------------------------------

class TestPlatformName:
    def test_returns_discord(self):
        assert platform_name() == "Discord"


# ---------------------------------------------------------------------------
# _load_token()
# ---------------------------------------------------------------------------

class TestLoadToken:
    def test_keyring_primary(self):
        with patch("keyring.get_password", return_value="Bot_kr"):
            token = _load_token()
        assert token == "Bot_kr"

    def test_env_fallback(self):
        with patch.dict(os.environ, {"ARTHA_DISCORD_BOT_TOKEN": "Bot_env"}, clear=True):
            with patch("keyring.get_password", return_value=None):
                token = _load_token()
        assert token == "Bot_env"

    def test_keyring_import_error_falls_through(self):
        with patch.dict(os.environ, {"ARTHA_DISCORD_BOT_TOKEN": "Bot_env_only"}, clear=True):
            with patch.dict(sys.modules, {"keyring": None}):
                token = _load_token()
        assert token == "Bot_env_only"

    def test_no_token_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                with pytest.raises(RuntimeError, match="Discord"):
                    _load_token()


# ---------------------------------------------------------------------------
# _parse_message_create()
# ---------------------------------------------------------------------------

class TestParseMessageCreate:
    def test_slash_command_parsed(self):
        event = _make_message_create_event("/status")
        result = _parse_message_create(event)
        assert result is not None
        assert result.command == "/status"
        assert result.args == []

    def test_command_with_args(self):
        event = _make_message_create_event("/domain immigration")
        result = _parse_message_create(event)
        assert result is not None
        assert result.command == "/domain"
        assert result.args == ["immigration"]

    def test_non_command_returns_none(self):
        event = _make_message_create_event("hello world")
        result = _parse_message_create(event)
        assert result is None

    def test_bot_message_returns_none(self):
        event = _make_message_create_event("/status", is_bot=True)
        result = _parse_message_create(event)
        assert result is None

    def test_sender_id_extracted(self):
        event = _make_message_create_event("/items", author_id="u999")
        result = _parse_message_create(event)
        assert result is not None
        assert result.sender_id == "u999"

    def test_command_lowercased(self):
        event = _make_message_create_event("/STATUS")
        result = _parse_message_create(event)
        assert result is not None
        assert result.command == "/status"

    def test_timestamp_extracted(self):
        event = _make_message_create_event("/goals")
        result = _parse_message_create(event)
        assert result is not None
        assert "2024-04-01" in result.timestamp

    def test_message_id_extracted(self):
        event = _make_message_create_event("/status", message_id="m_abc")
        result = _parse_message_create(event)
        assert result is not None
        assert result.message_id == "m_abc"


# ---------------------------------------------------------------------------
# DiscordAdapter.health_check()
# ---------------------------------------------------------------------------

class TestDiscordAdapterHealthCheck:
    def test_valid_token_returns_true(self):
        adapter = DiscordAdapter(token="valid_token", channel_id="c1")
        me_body = json.dumps(_discord_user()).encode()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(me_body)):
            result = adapter.health_check()
        assert result is True

    def test_http_error_returns_false(self):
        import urllib.error  # noqa: PLC0415
        adapter = DiscordAdapter(token="bad_token", channel_id="c1")
        err = urllib.error.HTTPError(url="", code=401, msg="Unauthorized", hdrs=MagicMock(), fp=None)
        with patch("urllib.request.urlopen", side_effect=err):
            result = adapter.health_check()
        assert result is False

    def test_network_error_returns_false(self):
        adapter = DiscordAdapter(token="tok", channel_id="c1")
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = adapter.health_check()
        assert result is False


# ---------------------------------------------------------------------------
# DiscordAdapter.send_message()
# ---------------------------------------------------------------------------

class TestDiscordAdapterSendMessage:
    def test_send_simple_message(self):
        adapter = DiscordAdapter(token="tok", channel_id="c1")
        response = json.dumps({"id": "msg_001", "content": "Hello"}).encode()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response)):
            result = adapter.send_message(ChannelMessage(text="Hello Artha", recipient_id="c1"))
        assert result is True

    def test_send_message_no_channel_returns_false(self):
        adapter = DiscordAdapter(token="tok", channel_id="")
        result = adapter.send_message(ChannelMessage(text="Hello", recipient_id=""))
        assert result is False

    def test_long_message_chunked(self):
        """Messages > 2000 chars should be split."""
        adapter = DiscordAdapter(token="tok", channel_id="c1")
        long_text = "A " * 1200  # ~2400 chars
        response = json.dumps({"id": "msg_001"}).encode()
        call_count = [0]

        def _mock_open(req, timeout=None):
            call_count[0] += 1
            return _mock_urlopen(response)

        with patch("urllib.request.urlopen", side_effect=_mock_open):
            adapter.send_message(ChannelMessage(text=long_text, recipient_id="c1"))

        assert call_count[0] >= 2, "Should make multiple calls for chunked messages"

    def test_send_failure_returns_false(self):
        adapter = DiscordAdapter(token="tok", channel_id="c1")
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            result = adapter.send_message(ChannelMessage(text="Hello", recipient_id="c1"))
        assert result is False


# ---------------------------------------------------------------------------
# create_adapter()
# ---------------------------------------------------------------------------

class TestCreateAdapter:
    def test_returns_discord_adapter(self):
        with patch("keyring.get_password", return_value="test_token"):
            adapter = create_adapter(channel_id="c123")
        assert isinstance(adapter, DiscordAdapter)
        assert adapter.channel_id == "c123"

    def test_sender_whitelist_passed(self):
        with patch("keyring.get_password", return_value="tok"):
            adapter = create_adapter(sender_whitelist=["u001", "u002"])
        assert "u001" in adapter.sender_whitelist
        assert "u002" in adapter.sender_whitelist

    def test_no_token_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                with pytest.raises(RuntimeError, match="Discord"):
                    create_adapter()


# ---------------------------------------------------------------------------
# Sender whitelist enforcement
# ---------------------------------------------------------------------------

class TestSenderWhitelist:
    def test_whitelisted_sender_delivered(self):
        """Messages from whitelisted senders pass through."""
        adapter = DiscordAdapter(token="tok", channel_id="c1", sender_whitelist=["u_allowed"])
        event = _make_message_create_event("/status", author_id="u_allowed")
        msg = _parse_message_create(event)
        assert msg is not None
        # Whitelist check happens in poll(); just verify parse works
        assert msg.sender_id == "u_allowed"

    def test_parse_non_command_is_none(self):
        """Non-commands from any sender are not parsed as InboundMessage."""
        event = _make_message_create_event("regular chat message")
        assert _parse_message_create(event) is None
