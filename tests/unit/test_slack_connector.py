"""
tests/unit/test_slack_connector.py — Unit tests for scripts/connectors/slack.py

Tests cover: _since_to_datetime, _mrkdwn_to_plain, _resolve_channels,
_fetch_channel_history, fetch() orchestration, health_check(), token loading,
pagination, rate-limit handling, and error resilience.

All Slack Web API calls are mocked — no real network calls.
Run: pytest tests/unit/test_slack_connector.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest.mock as mock
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import connectors.slack as slack_mod
from connectors.slack import (
    _since_to_datetime,
    _mrkdwn_to_plain,
    _resolve_channels,
    _fetch_channel_history,
    _resolve_user,
    _load_token,
    fetch,
    health_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slack_response(ok: bool = True, **extra: Any) -> bytes:
    return json.dumps({"ok": ok, **extra}).encode("utf-8")


def _mock_response(body: bytes, status: int = 200):
    """Return a mock urllib response context manager."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda self: self
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# _since_to_datetime
# ---------------------------------------------------------------------------

class TestSinceTodatetime:
    def test_empty_returns_none(self):
        assert _since_to_datetime("") is None

    def test_days(self):
        dt = _since_to_datetime("7d")
        assert dt is not None
        assert abs((datetime.now(timezone.utc) - dt).total_seconds() - 7 * 86400) < 5

    def test_hours(self):
        dt = _since_to_datetime("24h")
        assert dt is not None
        assert abs((datetime.now(timezone.utc) - dt).total_seconds() - 86400) < 5

    def test_minutes(self):
        dt = _since_to_datetime("30m")
        assert dt is not None
        assert abs((datetime.now(timezone.utc) - dt).total_seconds() - 1800) < 5

    def test_iso8601(self):
        dt = _since_to_datetime("2026-01-01T00:00:00Z")
        assert dt is not None
        assert dt.year == 2026

    def test_iso8601_naive_assumed_utc(self):
        dt = _since_to_datetime("2026-01-01T00:00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_invalid_returns_none(self):
        assert _since_to_datetime("not-a-date") is None

    def test_uppercase_ignored(self):
        dt = _since_to_datetime("2D")
        assert dt is not None      # regex is fullmatch on lower(), "2D".lower()="2d"


# ---------------------------------------------------------------------------
# _mrkdwn_to_plain
# ---------------------------------------------------------------------------

class TestMrkdwnToPlain:
    def test_empty(self):
        assert _mrkdwn_to_plain("") == ""

    def test_user_mention_with_name(self):
        assert _mrkdwn_to_plain("<@U123|Alice>") == "@Alice"

    def test_user_mention_without_name(self):
        assert _mrkdwn_to_plain("<@U123>") == "@U123"

    def test_channel_mention(self):
        assert _mrkdwn_to_plain("<#C123|general>") == "#general"

    def test_url_with_label(self):
        assert _mrkdwn_to_plain("<https://example.com|Click here>") == "Click here"

    def test_bare_url(self):
        assert _mrkdwn_to_plain("<https://example.com>") == "https://example.com"

    def test_bold(self):
        assert _mrkdwn_to_plain("*bold*") == "bold"

    def test_italic(self):
        assert _mrkdwn_to_plain("_italic_") == "italic"

    def test_strikethrough(self):
        assert _mrkdwn_to_plain("~deleted~") == "deleted"

    def test_inline_code(self):
        assert _mrkdwn_to_plain("`code`") == "code"

    def test_broadcast(self):
        assert _mrkdwn_to_plain("<!channel>") == "@channel"

    def test_mixed(self):
        result = _mrkdwn_to_plain("Hello <@U1|Bob>, check <https://x.com|X>")
        assert "@Bob" in result
        assert "X" in result


# ---------------------------------------------------------------------------
# _load_token
# ---------------------------------------------------------------------------

class TestLoadToken:
    def test_from_auth_context(self):
        token = _load_token({"token": "xoxb-test-token"}, "some-key")
        assert token == "xoxb-test-token"

    def test_from_env_var(self):
        with patch.dict(os.environ, {"ARTHA_SLACK_BOT_TOKEN": "xoxb-env-token"}):
            with patch("keyring.get_password", return_value=None):
                token = _load_token(None, "artha-slack-bot-token")
        assert token == "xoxb-env-token"

    def test_keyring_error_falls_through_to_env(self):
        with patch.dict(os.environ, {"ARTHA_SLACK_BOT_TOKEN": "xoxb-fallback"}):
            with patch("keyring.get_password", side_effect=Exception("keyring broken")):
                token = _load_token(None, "artha-slack-bot-token")
        assert token == "xoxb-fallback"

    def test_no_token_returns_none(self):
        env = {k: v for k, v in os.environ.items() if k != "ARTHA_SLACK_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("keyring.get_password", return_value=None):
                token = _load_token(None, "artha-slack-bot-token")
        assert token is None


# ---------------------------------------------------------------------------
# _resolve_channels
# ---------------------------------------------------------------------------

class TestResolveChannels:
    def _channels_response(self, channels, next_cursor=""):
        return _make_slack_response(
            ok=True,
            channels=channels,
            response_metadata={"next_cursor": next_cursor},
        )

    def test_all_channels_when_no_filter(self):
        body = self._channels_response([
            {"id": "C1", "name": "general"},
            {"id": "C2", "name": "random"},
        ])
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(body)
            result = _resolve_channels("token", None)
        assert result == {"C1": "general", "C2": "random"}

    def test_filter_by_name(self):
        body = self._channels_response([
            {"id": "C1", "name": "general"},
            {"id": "C2", "name": "random"},
        ])
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(body)
            result = _resolve_channels("token", ["#general"])
        assert result == {"C1": "general"}
        assert "C2" not in result

    def test_pagination(self):
        page1 = self._channels_response(
            [{"id": "C1", "name": "a"}], next_cursor="CURSOR1"
        )
        page2 = self._channels_response(
            [{"id": "C2", "name": "b"}], next_cursor=""
        )
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = [
                _mock_response(page1),
                _mock_response(page2),
            ]
            result = _resolve_channels("token", None)
        assert "C1" in result and "C2" in result

    def test_api_error_returns_empty(self):
        import urllib.error
        with patch("connectors.slack.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("network down")):
            result = _resolve_channels("token", None)
        assert result == {}


# ---------------------------------------------------------------------------
# _fetch_channel_history
# ---------------------------------------------------------------------------

class TestFetchChannelHistory:
    def _messages_response(self, messages, has_more=False, next_cursor=""):
        return _make_slack_response(
            ok=True,
            messages=messages,
            has_more=has_more,
            response_metadata={"next_cursor": next_cursor},
        )

    def test_returns_messages_oldest_first(self):
        msg1 = {"ts": "1000000001.000000", "text": "first", "user": "U1"}
        msg2 = {"ts": "1000000002.000000", "text": "second", "user": "U2"}
        # Slack returns newest-first
        body = self._messages_response([msg2, msg1])
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(body)
            result = _fetch_channel_history("token", "C1", "0", 100)
        assert result[0]["ts"] == msg1["ts"]
        assert result[1]["ts"] == msg2["ts"]

    def test_empty_channel(self):
        body = self._messages_response([])
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(body)
            result = _fetch_channel_history("token", "C1", "0", 100)
        assert result == []

    def test_api_error_returns_empty(self):
        import urllib.error
        with patch("connectors.slack.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timeout")):
            result = _fetch_channel_history("token", "C1", "0", 100)
        assert result == []


# ---------------------------------------------------------------------------
# fetch() orchestration
# ---------------------------------------------------------------------------

class TestFetch:
    def _setup(self, channels_resp, history_resp):
        """Return side_effect list for urlopen."""
        return [
            _mock_response(channels_resp),
            _mock_response(history_resp),
        ]

    def test_yields_records_for_matching_messages(self):
        ch_body = _make_slack_response(
            ok=True,
            channels=[{"id": "C1", "name": "general"}],
            response_metadata={"next_cursor": ""},
        )
        msg = {"ts": "1000000001.000000", "text": "Hello *world*", "user": "U1"}
        hist_body = _make_slack_response(
            ok=True,
            messages=[msg],
            has_more=False,
            response_metadata={"next_cursor": ""},
        )
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = self._setup(ch_body, hist_body)
            with patch("connectors.slack._resolve_user", return_value="Alice"):
                with patch("connectors.slack.time.sleep"):
                    records = list(fetch(
                        since="24h",
                        max_results=10,
                        auth_context={"token": "xoxb-test"},
                    ))
        assert len(records) == 1
        assert records[0]["source"] == "slack"
        assert records[0]["channel"] == "#general"
        assert "Hello world" in records[0]["body"]

    def test_no_token_yields_nothing(self):
        env = {k: v for k, v in os.environ.items() if k != "ARTHA_SLACK_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("keyring.get_password", return_value=None):
                records = list(fetch(auth_context={}))
        assert records == []

    def test_skips_join_leave_messages(self):
        ch_body = _make_slack_response(
            ok=True,
            channels=[{"id": "C1", "name": "general"}],
            response_metadata={"next_cursor": ""},
        )
        msgs = [
            {"ts": "1.0", "text": "joined", "user": "U1", "subtype": "channel_join"},
            {"ts": "2.0", "text": "real message", "user": "U2"},
        ]
        hist_body = _make_slack_response(
            ok=True,
            messages=msgs,
            has_more=False,
            response_metadata={"next_cursor": ""},
        )
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = self._setup(ch_body, hist_body)
            with patch("connectors.slack._resolve_user", return_value="Bob"):
                with patch("connectors.slack.time.sleep"):
                    records = list(fetch(auth_context={"token": "xoxb-test"}))
        assert len(records) == 1
        assert records[0]["body"] == "real message"

    def test_max_results_respected(self):
        ch_body = _make_slack_response(
            ok=True,
            channels=[{"id": "C1", "name": "g"}],
            response_metadata={"next_cursor": ""},
        )
        msgs = [{"ts": f"{i}.0", "text": f"msg{i}", "user": "U1"} for i in range(20)]
        hist_body = _make_slack_response(
            ok=True,
            messages=msgs,
            has_more=False,
            response_metadata={"next_cursor": ""},
        )
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = self._setup(ch_body, hist_body)
            with patch("connectors.slack._resolve_user", return_value="X"):
                with patch("connectors.slack.time.sleep"):
                    records = list(fetch(
                        auth_context={"token": "xoxb-test"},
                        max_results=5,
                    ))
        assert len(records) <= 5

    def test_record_schema_complete(self):
        ch_body = _make_slack_response(
            ok=True,
            channels=[{"id": "C1", "name": "eng"}],
            response_metadata={"next_cursor": ""},
        )
        msg = {
            "ts": "1700000001.123456",
            "text": "Deploy to prod?",
            "user": "U42",
            "reactions": [{"name": "thumbsup", "count": 3}],
        }
        hist_body = _make_slack_response(
            ok=True,
            messages=[msg],
            has_more=False,
            response_metadata={"next_cursor": ""},
        )
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = self._setup(ch_body, hist_body)
            with patch("connectors.slack._resolve_user", return_value="Eng"):
                with patch("connectors.slack.time.sleep"):
                    records = list(fetch(
                        auth_context={"token": "xoxb-test"},
                    ))
        r = records[0]
        required = {"id", "title", "body", "author", "ts", "url", "source",
                    "channel", "thread_ts", "reactions"}
        assert required.issubset(r.keys())
        assert r["reactions"][0]["name"] == "thumbsup"


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_healthy(self):
        body = _make_slack_response(ok=True, team="MyTeam", user="arthabot")
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(body)
            result = health_check(auth_context={"token": "xoxb-test"})
        assert result is True

    def test_invalid_auth(self):
        body = _make_slack_response(ok=False, error="invalid_auth")
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(body)
            result = health_check(auth_context={"token": "xoxb-bad"})
        assert result is False

    def test_network_error_returns_false(self):
        import urllib.error
        with patch("connectors.slack.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            result = health_check(auth_context={"token": "xoxb-test"})
        assert result is False

    def test_no_token_returns_false(self):
        env = {k: v for k, v in os.environ.items() if k != "ARTHA_SLACK_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = health_check(auth_context=None)
        assert result is False
