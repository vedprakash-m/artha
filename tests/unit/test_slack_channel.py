"""
tests/unit/test_slack_channel.py — Unit tests for scripts/channels/slack.py

Tests cover: SlackAdapter.send_message, send_document, health_check,
poll() Socket Mode parsing, create_adapter factory, and platform_name().
All Slack API calls are mocked — no real network calls.

Run: pytest tests/unit/test_slack_channel.py -v
"""
from __future__ import annotations

import json
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from channels.slack import (
    SlackAdapter,
    create_adapter,
    platform_name,
    _slack_api,
    _load_token,
)
from channels.base import ChannelMessage, InboundMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slack_ok(**data: Any) -> bytes:
    return json.dumps({"ok": True, **data}).encode("utf-8")


def _slack_err(error: str) -> bytes:
    return json.dumps({"ok": False, "error": error}).encode("utf-8")


def _mock_urlopen(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_adapter(token: str = "xoxb-test", **kwargs: Any) -> SlackAdapter:
    return SlackAdapter(token=token, recipient_id="C123", **kwargs)


# ---------------------------------------------------------------------------
# platform_name()
# ---------------------------------------------------------------------------

def test_platform_name():
    assert platform_name() == "Slack"


# ---------------------------------------------------------------------------
# send_message()
# ---------------------------------------------------------------------------

class TestSendMessage:
    def test_success(self):
        adapter = _make_adapter()
        msg = ChannelMessage(text="Hello!", recipient_id="C456")
        with patch("channels.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(_slack_ok(ts="12345.0"))
            result = adapter.send_message(msg)
        assert result is True

    def test_returns_false_on_api_error(self):
        adapter = _make_adapter()
        msg = ChannelMessage(text="Hi", recipient_id="C456")
        with patch("channels.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(_slack_err("channel_not_found"))
            result = adapter.send_message(msg)
        assert result is False

    def test_returns_false_on_network_error(self):
        import urllib.error
        adapter = _make_adapter()
        msg = ChannelMessage(text="Hi", recipient_id="C456")
        with patch("channels.slack.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timeout")):
            result = adapter.send_message(msg)
        assert result is False

    def test_no_recipient_returns_false(self):
        adapter = SlackAdapter(token="xoxb-test")  # no recipient_id
        msg = ChannelMessage(text="Hi", recipient_id="")
        result = adapter.send_message(msg)
        assert result is False

    def test_text_truncated_to_max(self):
        adapter = _make_adapter()
        long_text = "x" * 50_000
        msg = ChannelMessage(text=long_text, recipient_id="C1")
        captured_payload: dict = {}

        def _capture_call(url, **kwargs):
            import urllib.request as ur
            req = url
            if hasattr(req, "data"):
                captured_payload.update(json.loads(req.data.decode("utf-8")))
            resp = MagicMock()
            resp.read.return_value = _slack_ok(ts="1.0")
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("channels.slack.urllib.request.urlopen", side_effect=_capture_call):
            adapter.send_message(msg)
        assert len(captured_payload.get("text", "")) <= 40_000

    def test_buttons_become_block_kit(self):
        adapter = _make_adapter()
        msg = ChannelMessage(
            text="Choose:",
            recipient_id="C1",
            buttons=[{"label": "Yes", "command": "/yes"}, {"label": "No", "command": "/no"}],
        )
        captured: dict = {}

        def _capture(url, **_):
            if hasattr(url, "data"):
                captured.update(json.loads(url.data.decode()))
            resp = MagicMock()
            resp.read.return_value = _slack_ok()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("channels.slack.urllib.request.urlopen", side_effect=_capture):
            adapter.send_message(msg)
        assert "blocks" in captured


# ---------------------------------------------------------------------------
# send_document()
# ---------------------------------------------------------------------------

class TestSendDocument:
    def test_file_not_found_returns_false(self, tmp_path):
        adapter = _make_adapter()
        result = adapter.send_document(
            recipient_id="C1",
            file_path=str(tmp_path / "nonexistent.txt"),
        )
        assert result is False

    def test_successful_upload(self, tmp_path):
        fpath = tmp_path / "test.txt"
        fpath.write_text("hello content")
        adapter = _make_adapter()

        ok_upload_url = _slack_ok(
            upload_url="https://files.example.com/upload123",
            file_id="F123",
        )
        ok_complete = _slack_ok()

        call_count = [0]

        def _urlopen_side_effect(req, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            if call_count[0] == 1:
                # getUploadURLExternal
                resp.read.return_value = ok_upload_url
            elif call_count[0] == 2:
                # PUT upload
                resp.status = 200
                resp.read.return_value = b""
            else:
                # completeUploadExternal
                resp.read.return_value = ok_complete
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("channels.slack.urllib.request.urlopen",
                   side_effect=_urlopen_side_effect):
            result = adapter.send_document(
                recipient_id="C1",
                file_path=str(fpath),
                caption="Test file",
            )
        assert result is True

    def test_api_error_returns_false(self, tmp_path):
        fpath = tmp_path / "test.txt"
        fpath.write_text("content")
        adapter = _make_adapter()
        with patch("channels.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(_slack_err("not_allowed"))
            result = adapter.send_document(recipient_id="C1", file_path=str(fpath))
        assert result is False


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_healthy(self):
        adapter = _make_adapter()
        with patch("channels.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(
                _slack_ok(team="Acme", user="arthabot")
            )
            result = adapter.health_check()
        assert result is True

    def test_unhealthy_api_error(self):
        adapter = _make_adapter()
        with patch("channels.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(_slack_err("account_inactive"))
            result = adapter.health_check()
        assert result is False

    def test_network_error_returns_false(self):
        import urllib.error
        adapter = _make_adapter()
        with patch("channels.slack.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            result = adapter.health_check()
        assert result is False


# ---------------------------------------------------------------------------
# poll() — Socket Mode
# ---------------------------------------------------------------------------

class TestPoll:
    def test_no_app_token_returns_empty(self):
        adapter = SlackAdapter(token="xoxb-test", app_token="")
        result = adapter.poll(timeout=1)
        assert result == []

    def test_parses_events_api_envelope(self):
        adapter = SlackAdapter(
            token="xoxb-test",
            app_token="xapp-test",
        )
        envelope = {
            "type": "events_api",
            "envelope_id": "env123",
            "payload": {
                "event": {
                    "type": "message",
                    "user": "U999",
                    "text": "/status",
                    "ts": "1700000001.000000",
                    "channel": "C1",
                }
            },
        }
        mock_ws = MagicMock()
        mock_ws.poll.return_value = [envelope]
        mock_ws.send.return_value = True
        adapter._ws_client = mock_ws

        result = adapter.poll(timeout=5)
        assert len(result) == 1
        msg = result[0]
        assert msg.command == "/status"
        assert msg.sender_id == "U999"
        mock_ws.send.assert_called_once_with({"envelope_id": "env123"})

    def test_non_events_api_type_skipped(self):
        adapter = SlackAdapter(token="xoxb-test", app_token="xapp-test")
        envelope = {"type": "hello", "envelope_id": "e1"}
        mock_ws = MagicMock()
        mock_ws.poll.return_value = [envelope]
        mock_ws.send.return_value = True
        adapter._ws_client = mock_ws

        result = adapter.poll(timeout=5)
        assert result == []

    def test_sender_whitelist_enforced(self):
        adapter = SlackAdapter(
            token="xoxb-test",
            app_token="xapp-test",
            sender_whitelist=["U_ALLOWED"],
        )
        envelopes = [
            {
                "type": "events_api",
                "envelope_id": "e1",
                "payload": {
                    "event": {
                        "type": "message",
                        "user": "U_BLOCKED",
                        "text": "/status",
                        "ts": "1.0",
                        "channel": "C1",
                    }
                },
            },
            {
                "type": "events_api",
                "envelope_id": "e2",
                "payload": {
                    "event": {
                        "type": "message",
                        "user": "U_ALLOWED",
                        "text": "/goals",
                        "ts": "2.0",
                        "channel": "C1",
                    }
                },
            },
        ]
        mock_ws = MagicMock()
        mock_ws.poll.return_value = envelopes
        mock_ws.send.return_value = True
        adapter._ws_client = mock_ws

        result = adapter.poll(timeout=5)
        assert len(result) == 1
        assert result[0].sender_id == "U_ALLOWED"

    def test_bot_messages_skipped(self):
        adapter = SlackAdapter(token="xoxb-test", app_token="xapp-test")
        envelope = {
            "type": "events_api",
            "envelope_id": "e1",
            "payload": {
                "event": {
                    "type": "message",
                    "bot_id": "B123",
                    "user": "U1",
                    "text": "/status",
                    "ts": "1.0",
                    "channel": "C1",
                }
            },
        }
        mock_ws = MagicMock()
        mock_ws.poll.return_value = [envelope]
        mock_ws.send.return_value = True
        adapter._ws_client = mock_ws

        result = adapter.poll(timeout=5)
        assert result == []


# ---------------------------------------------------------------------------
# create_adapter()
# ---------------------------------------------------------------------------

class TestCreateAdapter:
    def test_raises_without_token(self):
        env = {k: v for k, v in os.environ.items() if k != "ARTHA_SLACK_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("keyring.get_password", return_value=None):
                with pytest.raises(RuntimeError, match="bot token not found"):
                    create_adapter()

    def test_creates_adapter_with_token_from_env(self):
        with patch.dict(os.environ, {"ARTHA_SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("keyring.get_password", return_value=None):
                adapter = create_adapter()
        assert isinstance(adapter, SlackAdapter)
        assert adapter.token == "xoxb-test"

    def test_app_token_optional(self):
        with patch.dict(os.environ, {
            "ARTHA_SLACK_BOT_TOKEN": "xoxb-bot",
            "ARTHA_SLACK_APP_TOKEN": "xapp-app",
        }):
            with patch("keyring.get_password", return_value=None):
                adapter = create_adapter()
        assert adapter.app_token == "xapp-app"

    def test_sender_whitelist_passed_through(self):
        with patch.dict(os.environ, {"ARTHA_SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("keyring.get_password", return_value=None):
                adapter = create_adapter(sender_whitelist=["U1", "U2"])
        assert adapter.sender_whitelist == ["U1", "U2"]
