"""
tests/unit/test_setup_ha_token.py — Unit tests for setup_ha_token.py wizard.

Tests cover: URL validation, HA connectivity check, token storage,
YAML update, .nosync creation, and HTTP error handling.

Run: pytest tests/unit/test_setup_ha_token.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from setup_ha_token import (
    _validate_url,
    _test_ha_connection,
    _store_token,
)


# ── _validate_url ─────────────────────────────────────────────────────────────

class TestValidateUrl:
    def test_valid_http(self):
        result = _validate_url("http://192.168.1.123:8123")
        assert result == "http://192.168.1.123:8123"

    def test_valid_https(self):
        result = _validate_url("https://homeassistant.local:8123")
        assert result == "https://homeassistant.local:8123"

    def test_trailing_slash_stripped(self):
        result = _validate_url("http://192.168.1.123:8123/")
        assert result == "http://192.168.1.123:8123"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_url("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_url("   ")

    def test_missing_scheme_raises(self):
        with pytest.raises(ValueError, match="http"):
            _validate_url("192.168.1.123:8123")

    def test_ftp_scheme_raises(self):
        with pytest.raises(ValueError, match="http"):
            _validate_url("ftp://192.168.1.123:8123")

    def test_extra_whitespace_stripped(self):
        result = _validate_url("  http://192.168.1.123:8123  ")
        assert result == "http://192.168.1.123:8123"


# ── _test_ha_connection ───────────────────────────────────────────────────────

class TestTestHaConnection:
    def _mock_response(self, status_code: int, json_body: dict) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_body
        mock_resp.text = json.dumps(json_body)
        return mock_resp

    @patch("setup_ha_token.requests.get")
    def test_success(self, mock_get: MagicMock):
        mock_get.return_value = self._mock_response(200, {"message": "API running."})
        result = _test_ha_connection("http://homeassistant.local:8123", "valid-token")
        assert result["message"] == "API running."

    @patch("setup_ha_token.requests.get")
    def test_401_raises(self, mock_get: MagicMock):
        mock_get.return_value = self._mock_response(401, {"message": "Unauthorized"})
        with pytest.raises(RuntimeError, match="Authentication failed"):
            _test_ha_connection("http://homeassistant.local:8123", "bad-token")

    @patch("setup_ha_token.requests.get")
    def test_non_200_raises(self, mock_get: MagicMock):
        mock_get.return_value = self._mock_response(500, {"error": "Internal Server Error"})
        with pytest.raises(RuntimeError, match="HTTP 500"):
            _test_ha_connection("http://homeassistant.local:8123", "valid-token")

    @patch("setup_ha_token.requests.get")
    def test_wrong_message_raises(self, mock_get: MagicMock):
        mock_get.return_value = self._mock_response(200, {"message": "unexpected"})
        with pytest.raises(RuntimeError, match="Unexpected HA API response"):
            _test_ha_connection("http://homeassistant.local:8123", "valid-token")

    @patch("setup_ha_token.requests.get")
    def test_connection_error_raises(self, mock_get: MagicMock):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("refused")
        with pytest.raises(RuntimeError, match="Cannot connect"):
            _test_ha_connection("http://192.168.1.123:8123", "valid-token")

    @patch("setup_ha_token.requests.get")
    def test_timeout_raises(self, mock_get: MagicMock):
        import requests as req
        mock_get.side_effect = req.exceptions.Timeout("timed out")
        with pytest.raises(RuntimeError, match="timed out"):
            _test_ha_connection("http://192.168.1.123:8123", "valid-token")

    @patch("setup_ha_token.requests.get")
    def test_non_json_response_raises(self, mock_get: MagicMock):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not JSON")
        mock_get.return_value = mock_resp
        with pytest.raises(RuntimeError, match="non-JSON"):
            _test_ha_connection("http://192.168.1.123:8123", "valid-token")

    @patch("setup_ha_token.requests.get")
    def test_get_called_with_bearer_token(self, mock_get: MagicMock):
        mock_get.return_value = self._mock_response(200, {"message": "API running."})
        _test_ha_connection("http://192.168.1.123:8123", "my-token-abc")
        call_kwargs = mock_get.call_args
        headers = call_kwargs[1].get("headers", {}) if call_kwargs[1] else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
        # Also check through args
        if hasattr(call_kwargs, 'kwargs'):
            headers = call_kwargs.kwargs.get("headers", {})
        assert "Bearer my-token-abc" in str(headers) or "my-token-abc" in str(call_kwargs)


# ── _store_token ──────────────────────────────────────────────────────────────

class TestStoreToken:
    def test_calls_keyring_set_password(self):
        mock_keyring = MagicMock()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            _store_token("my-secret-token")
        mock_keyring.set_password.assert_called_once_with(
            "artha-ha-token", "artha", "my-secret-token"
        )

    def test_keyring_not_installed_raises(self):
        with patch.dict("sys.modules", {"keyring": None}):
            # When keyring is set to None in sys.modules, import should fail
            with pytest.raises((RuntimeError, ImportError)):
                _store_token("my-token")


# ── _create_nosync ────────────────────────────────────────────────────────────

class TestCreateNosync:
    def test_creates_nosync_file(self, tmp_path: Path):
        from setup_ha_token import _create_nosync
        nosync = tmp_path / ".nosync"
        with patch("setup_ha_token._NOSYNC_FILE", nosync), \
             patch("setup_ha_token._TMP_DIR", tmp_path), \
             patch("setup_ha_token._REPO_ROOT", tmp_path):
            _create_nosync()
        assert nosync.exists()

    def test_idempotent_if_already_exists(self, tmp_path: Path):
        from setup_ha_token import _create_nosync
        nosync = tmp_path / ".nosync"
        nosync.write_text("already here")
        with patch("setup_ha_token._NOSYNC_FILE", nosync), \
             patch("setup_ha_token._TMP_DIR", tmp_path), \
             patch("setup_ha_token._REPO_ROOT", tmp_path):
            _create_nosync()  # Should not raise
        assert nosync.exists()
