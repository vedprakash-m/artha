"""
tests/unit/test_slack_action.py — Unit tests for scripts/actions/slack_send.py

Tests cover: validate(), dry_run(), execute(), health_check().
All Slack Web API calls are mocked — no real network calls.

Run: pytest tests/unit/test_slack_action.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import actions.slack_send as slack_send
from actions.base import ActionProposal, ActionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proposal(**param_overrides: Any) -> ActionProposal:
    params: dict[str, Any] = {
        "channel": "C123456",
        "text": "Hello from Artha!",
    }
    params.update(param_overrides)
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="slack_send",
        domain="work",
        title="Send Slack message",
        description="Test description",
        parameters=params,
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=None,
        source_step=None,
        source_skill=None,
        linked_oi=None,
    )


def _mock_urlopen(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _slack_ok(**extra: Any) -> bytes:
    return json.dumps({"ok": True, **extra}).encode("utf-8")


def _slack_err(error: str) -> bytes:
    return json.dumps({"ok": False, "error": error}).encode("utf-8")


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_passes(self):
        ok, reason = slack_send.validate(_make_proposal())
        assert ok is True
        assert reason == ""

    def test_missing_channel_fails(self):
        ok, reason = slack_send.validate(_make_proposal(channel=""))
        assert ok is False
        assert "channel" in reason.lower()

    def test_missing_text_fails(self):
        ok, reason = slack_send.validate(_make_proposal(text=""))
        assert ok is False
        assert "text" in reason.lower()

    def test_text_too_long_fails(self):
        ok, reason = slack_send.validate(_make_proposal(text="x" * 50_000))
        assert ok is False
        assert "40" in reason or "limit" in reason.lower()

    def test_invalid_blocks_json_fails(self):
        ok, reason = slack_send.validate(_make_proposal(blocks="not-json"))
        assert ok is False
        assert "json" in reason.lower() or "blocks" in reason.lower()

    def test_valid_blocks_json_passes(self):
        ok, reason = slack_send.validate(
            _make_proposal(blocks='[{"type":"section","text":{"type":"mrkdwn","text":"hi"}}]')
        )
        assert ok is True

    def test_blocks_not_array_fails(self):
        ok, reason = slack_send.validate(_make_proposal(blocks='{"type":"section"}'))
        assert ok is False


# ---------------------------------------------------------------------------
# dry_run()
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_returns_success_status(self):
        result = slack_send.dry_run(_make_proposal())
        assert result.status == "success"
        assert result.data.get("dry_run") is True

    def test_preview_contains_channel_and_text(self):
        result = slack_send.dry_run(_make_proposal(
            channel="#general", text="Test message"
        ))
        assert "#general" in result.message
        assert "Test message" in result.message

    def test_long_text_truncated_in_preview(self):
        long_text = "A" * 300
        result = slack_send.dry_run(_make_proposal(text=long_text))
        assert "…" in result.message

    def test_thread_ts_shown_when_present(self):
        result = slack_send.dry_run(_make_proposal(thread_ts="12345.0"))
        assert "12345.0" in result.message

    def test_no_api_call_on_dry_run(self):
        with patch("actions.slack_send.urllib.request.urlopen") as mock_open:
            slack_send.dry_run(_make_proposal())
            mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------

class TestExecute:
    def test_success(self):
        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen") as mock_open:
                mock_open.return_value = _mock_urlopen(_slack_ok(ts="99.0"))
                result = slack_send.execute(_make_proposal())
        assert result.status == "success"
        assert "99.0" in result.message
        assert result.data.get("ts") == "99.0"

    def test_no_token_returns_failure(self):
        env = {k: v for k, v in os.environ.items() if k != "ARTHA_SLACK_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = slack_send.execute(_make_proposal())
        assert result.status == "failure"
        assert "setup_slack" in result.message.lower() or "token" in result.message.lower()

    def test_api_error_returns_failure(self):
        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen") as mock_open:
                mock_open.return_value = _mock_urlopen(_slack_err("channel_not_found"))
                result = slack_send.execute(_make_proposal())
        assert result.status == "failure"
        assert "channel_not_found" in result.message

    def test_network_error_returns_failure(self):
        import urllib.error
        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen",
                       side_effect=urllib.error.URLError("timeout")):
                result = slack_send.execute(_make_proposal())
        assert result.status == "failure"

    def test_blocks_sent_when_provided(self):
        blocks = '[{"type":"section","text":{"type":"mrkdwn","text":"hi"}}]'
        captured: dict = {}

        def _capture(req, **_):
            if hasattr(req, "data"):
                captured.update(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.read.return_value = _slack_ok(ts="1.0")
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen", side_effect=_capture):
                slack_send.execute(_make_proposal(blocks=blocks))
        assert "blocks" in captured

    def test_thread_ts_sent_when_provided(self):
        captured: dict = {}

        def _capture(req, **_):
            if hasattr(req, "data"):
                captured.update(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.read.return_value = _slack_ok(ts="1.0")
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen", side_effect=_capture):
                slack_send.execute(_make_proposal(thread_ts="12345.0"))
        assert captured.get("thread_ts") == "12345.0"


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_healthy(self):
        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen") as mock_open:
                mock_open.return_value = _mock_urlopen(
                    _slack_ok(ok=True, team="Acme", user="arthabot")
                )
                result = slack_send.health_check()
        assert result is True

    def test_no_token_returns_false(self):
        env = {k: v for k, v in os.environ.items() if k != "ARTHA_SLACK_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = slack_send.health_check()
        assert result is False

    def test_api_error_returns_false(self):
        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen") as mock_open:
                mock_open.return_value = _mock_urlopen(_slack_err("invalid_auth"))
                result = slack_send.health_check()
        assert result is False

    def test_network_error_returns_false(self):
        import urllib.error
        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen",
                       side_effect=urllib.error.URLError("no route")):
                result = slack_send.health_check()
        assert result is False

    def test_health_check_takes_no_arguments(self):
        """health_check() must conform to ActionHandler protocol (no args)."""
        import inspect
        sig = inspect.signature(slack_send.health_check)
        assert len(sig.parameters) == 0, (
            "health_check() must take no arguments (ActionHandler protocol)"
        )
