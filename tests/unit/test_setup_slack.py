"""
tests/unit/test_setup_slack.py — Unit tests for scripts/setup_slack.py

Tests cover: _verify_bot_token, _verify_app_token, _send_test_message,
_enable_in_connectors_yaml, and --verify-only CLI mode.
All Slack API calls are mocked — no real network calls.

Run: pytest tests/unit/test_setup_slack.py -v
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call
from io import StringIO

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from setup_slack import (
    _verify_bot_token,
    _verify_app_token,
    _send_test_message,
    _enable_in_connectors_yaml,
    _run_verify_only,
)


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


def _slack_ok(**extra: Any) -> bytes:
    return json.dumps({"ok": True, **extra}).encode("utf-8")


def _slack_err(error: str) -> bytes:
    return json.dumps({"ok": False, "error": error}).encode("utf-8")


# ---------------------------------------------------------------------------
# _verify_bot_token()
# ---------------------------------------------------------------------------

class TestVerifyBotToken:
    def test_valid_token_returns_dict(self):
        body = _slack_ok(team="Acme", user="arthabot", user_id="U123")
        with patch("setup_slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(body)
            result = _verify_bot_token("xoxb-valid")
        assert result is not None
        assert result["team"] == "Acme"

    def test_invalid_token_returns_none(self, capsys):
        body = _slack_err("invalid_auth")
        with patch("setup_slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(body)
            result = _verify_bot_token("xoxb-bad")
        assert result is None

    def test_network_error_returns_none(self, capsys):
        import urllib.error
        with patch("setup_slack.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            result = _verify_bot_token("xoxb-test")
        assert result is None


# ---------------------------------------------------------------------------
# _verify_app_token()
# ---------------------------------------------------------------------------

class TestVerifyAppToken:
    def test_valid_token_returns_true(self):
        body = _slack_ok(url="wss://example.com/socket")
        with patch("setup_slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(body)
            result = _verify_app_token("xapp-valid")
        assert result is True

    def test_invalid_token_returns_false(self, capsys):
        body = _slack_err("invalid_auth")
        with patch("setup_slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(body)
            result = _verify_app_token("xapp-bad")
        assert result is False


# ---------------------------------------------------------------------------
# _send_test_message()
# ---------------------------------------------------------------------------

class TestSendTestMessage:
    def test_success_returns_true(self, capsys):
        body = _slack_ok(ts="123.0")
        with patch("setup_slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(body)
            result = _send_test_message("xoxb-test", "C123")
        assert result is True

    def test_api_error_returns_false(self, capsys):
        body = _slack_err("not_in_channel")
        with patch("setup_slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_urlopen(body)
            result = _send_test_message("xoxb-test", "C123")
        assert result is False


# ---------------------------------------------------------------------------
# _enable_in_connectors_yaml()
# ---------------------------------------------------------------------------

class TestEnableInConnectorsYaml:
    def _write_stub(self, tmp_path: Path) -> Path:
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = config_dir / "connectors.yaml"
        yaml_path.write_text(textwrap.dedent("""\
            connectors:
              slack:
                enabled: false
                fetch:
                  workspace_slug: ''
        """))
        return yaml_path

    def test_sets_enabled_true(self, tmp_path, monkeypatch):
        yaml_path = self._write_stub(tmp_path)
        monkeypatch.setattr("setup_slack._ARTHA_DIR", tmp_path)
        _enable_in_connectors_yaml("", [])
        content = yaml_path.read_text()
        assert "enabled: true" in content

    def test_updates_workspace_slug(self, tmp_path, monkeypatch):
        yaml_path = self._write_stub(tmp_path)
        monkeypatch.setattr("setup_slack._ARTHA_DIR", tmp_path)
        _enable_in_connectors_yaml("mycompany", [])
        content = yaml_path.read_text()
        assert "mycompany" in content

    def test_missing_file_does_not_raise(self, tmp_path, monkeypatch):
        # No connectors.yaml in tmp_path at all
        monkeypatch.setattr("setup_slack._ARTHA_DIR", tmp_path)
        _enable_in_connectors_yaml("", [])  # should not raise


# ---------------------------------------------------------------------------
# _run_verify_only()
# ---------------------------------------------------------------------------

class TestRunVerifyOnly:
    def test_no_token_exits_1(self, capsys):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch("setup_slack._get_keyring", return_value=mock_kr):
            code = _run_verify_only()
        assert code == 1

    def test_valid_token_exits_0(self, capsys):
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = lambda svc, key: (
            "xoxb-test" if key == "artha-slack-bot-token" else None
        )
        body = _slack_ok(team="Acme", user="arthabot")
        with patch("setup_slack._get_keyring", return_value=mock_kr):
            with patch("setup_slack.urllib.request.urlopen") as mock_open:
                mock_open.return_value = _mock_urlopen(body)
                code = _run_verify_only()
        assert code == 0

    def test_invalid_bot_token_exits_1(self, capsys):
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = lambda svc, key: (
            "xoxb-bad" if key == "artha-slack-bot-token" else None
        )
        body = _slack_err("invalid_auth")
        with patch("setup_slack._get_keyring", return_value=mock_kr):
            with patch("setup_slack.urllib.request.urlopen") as mock_open:
                mock_open.return_value = _mock_urlopen(body)
                code = _run_verify_only()
        assert code == 1
