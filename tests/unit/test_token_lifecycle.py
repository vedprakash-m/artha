"""
Unit tests for MS Graph token lifecycle management (Phase 2.4):
  - ensure_valid_token() writes _last_refresh_success on successful refresh
  - check_msgraph_token() warns at 60 days (clip at 90)
  - check_msgraph_token() proactively refreshes near-expiry tokens
  - Dual network+token failure message

Ref: specs/vm-hardening.md Phase 2.4, Appendix C
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import preflight as pf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token_file(tmp_path: Path, secs_left: float = 3600,
                     last_refresh_days_ago: int | None = None) -> Path:
    """Create a mock msgraph-token.json and return its path."""
    token_dir = tmp_path / ".tokens"
    token_dir.mkdir(exist_ok=True)
    token_file = token_dir / "msgraph-token.json"

    expiry = datetime.now(timezone.utc) + timedelta(seconds=secs_left)
    data: dict = {
        "access_token":  "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expiry":        expiry.isoformat(),
        "_artha_client_id": "fake-client-id",
    }
    if last_refresh_days_ago is not None:
        last_refresh = datetime.now(timezone.utc) - timedelta(days=last_refresh_days_ago)
        data["_last_refresh_success"] = last_refresh.isoformat()

    token_file.write_text(json.dumps(data))
    return token_file


# ---------------------------------------------------------------------------
# check_msgraph_token — refresh token age / 60-day warning
# ---------------------------------------------------------------------------

class TestMsGraphTokenAgWarning:
    def test_no_warning_under_60_days(self, tmp_path):
        """Token refreshed 30 days ago — no warning."""
        _make_token_file(tmp_path, secs_left=3600, last_refresh_days_ago=30)
        with patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")):
            result = pf.check_msgraph_token()
        assert result.passed
        assert "Refresh token" not in result.message

    def test_warning_at_61_days(self, tmp_path):
        """Token refreshed 61 days ago — warn about 90-day cliff."""
        _make_token_file(tmp_path, secs_left=3600, last_refresh_days_ago=61)
        with patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")):
            result = pf.check_msgraph_token()
        assert not result.passed
        assert "61d ago" in result.message or "Refresh token" in result.message
        assert "90d" in result.message or "cliff" in result.message.lower()

    def test_warning_at_80_days(self, tmp_path):
        """Token refreshed 80 days ago — warn escalation."""
        _make_token_file(tmp_path, secs_left=3600, last_refresh_days_ago=80)
        with patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")):
            result = pf.check_msgraph_token()
        assert not result.passed
        assert "80d ago" in result.message or "Refresh token" in result.message


# ---------------------------------------------------------------------------
# check_msgraph_token — proactive refresh
# ---------------------------------------------------------------------------

class TestMsGraphProactiveRefresh:
    def test_valid_token_returns_pass(self, tmp_path):
        """Token with 2h remaining returns pass without refresh."""
        _make_token_file(tmp_path, secs_left=7200)
        with patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")):
            result = pf.check_msgraph_token()
        assert result.passed
        assert "120m" in result.message or "Valid" in result.message

    def test_expired_token_attempts_refresh_and_reports(self, tmp_path):
        """Expired token with failing refresh returns failure with fix_hint."""
        _make_token_file(tmp_path, secs_left=-3600)  # expired 1h ago
        with patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")):
            with patch("setup_msgraph_oauth.ensure_valid_token",
                       side_effect=RuntimeError("No refresh token")):
                with patch("detect_environment.detect") as mock_env:
                    mock_env.return_value.capabilities = {"network_microsoft": True}
                    result = pf.check_msgraph_token()

        assert not result.passed
        assert result.fix_hint

    def test_expired_token_successfully_refreshed(self, tmp_path):
        """Expired token successfully refreshed → returns pass."""
        _make_token_file(tmp_path, secs_left=-60)  # just expired
        new_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        refreshed_token = {
            "access_token": "new-token",
            "expiry": new_expiry,
        }
        with (
            patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")),
        ):
            with patch("setup_msgraph_oauth.ensure_valid_token",
                       return_value=refreshed_token):
                result = pf.check_msgraph_token()

        assert result.passed
        assert "refreshed" in result.message.lower() or "valid" in result.message.lower()

    def test_missing_token_file_returns_failure(self, tmp_path):
        """No token file → P1 failure with setup hint."""
        token_dir = tmp_path / ".tokens"
        token_dir.mkdir()
        with patch.object(pf, "TOKEN_DIR", str(token_dir)):
            result = pf.check_msgraph_token()
        assert not result.passed
        assert "setup_msgraph_oauth.py" in result.fix_hint


# ---------------------------------------------------------------------------
# Dual network + token failure
# ---------------------------------------------------------------------------

class TestMsGraphDualFailure:
    def test_dual_failure_message_when_network_blocked(self, tmp_path):
        """Expired token + network block → dual message in result."""
        _make_token_file(tmp_path, secs_left=-3600)  # expired

        class _FakeManifest:
            capabilities = {"network_microsoft": False}

        with (
            patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")),
            patch("setup_msgraph_oauth.ensure_valid_token",
                  side_effect=RuntimeError("network unreachable")),
            patch("detect_environment.detect", return_value=_FakeManifest()),
        ):
            result = pf.check_msgraph_token()

        assert not result.passed
        # Both issues should be mentioned
        assert ("expired" in result.message.lower()
                or "network" in result.message.lower()
                or "blocked" in result.message.lower())

    def test_single_message_when_only_token_expired(self, tmp_path):
        """Expired token, network reachable → simple expiry message."""
        _make_token_file(tmp_path, secs_left=-3600)

        class _FakeManifest:
            capabilities = {"network_microsoft": True}

        with (
            patch.object(pf, "TOKEN_DIR", str(tmp_path / ".tokens")),
            patch("setup_msgraph_oauth.ensure_valid_token",
                  side_effect=RuntimeError("token expired")),
            patch("detect_environment.detect", return_value=_FakeManifest()),
        ):
            result = pf.check_msgraph_token()

        assert not result.passed
        assert "reauth" in result.fix_hint.lower() or "--reauth" in result.fix_hint


# ---------------------------------------------------------------------------
# ensure_valid_token — _last_refresh_success tracking
# ---------------------------------------------------------------------------

class TestEnsureValidTokenTracking:
    """ensure_valid_token() writes _last_refresh_success after successful refresh."""

    def test_refresh_success_writes_timestamp(self, tmp_path):
        """After a successful silent refresh, token file contains _last_refresh_success."""
        import setup_msgraph_oauth as mso

        token_dir = tmp_path / ".tokens"
        token_dir.mkdir()
        token_file = token_dir / "msgraph-token.json"

        # Token nearly expired — needs refresh
        expiry = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        token_data = {
            "access_token": "old",
            "refresh_token": "old-refresh",
            "expiry": expiry,
            "_artha_client_id": "fake-client-id",
        }
        token_file.write_text(json.dumps(token_data))

        new_token = {
            "access_token": "new",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }

        with (
            patch.object(mso, "TOKEN_FILE", str(token_file)),
            patch.object(mso, "_keychain_read", return_value="fake-client-id"),
            patch.object(mso, "_acquire_token_silent", return_value=new_token),
        ):
            result = mso.ensure_valid_token(warn_seconds=300)

        # Re-read the saved file
        saved = json.loads(token_file.read_text())
        assert "_last_refresh_success" in saved
        # Timestamp should be close to now
        saved_dt = datetime.fromisoformat(saved["_last_refresh_success"])
        age_secs = abs((datetime.now(timezone.utc) - saved_dt).total_seconds())
        assert age_secs < 10  # Written within the last 10 seconds

    def test_failed_refresh_does_not_write_timestamp(self, tmp_path):
        """Failed silent refresh does NOT update _last_refresh_success."""
        import setup_msgraph_oauth as mso

        token_dir = tmp_path / ".tokens"
        token_dir.mkdir()
        token_file = token_dir / "msgraph-token.json"

        expiry = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        old_refresh_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        token_data = {
            "access_token": "old",
            "refresh_token": "old-refresh",
            "expiry": expiry,
            "_artha_client_id": "fake-client-id",
            "_last_refresh_success": old_refresh_time,
        }
        token_file.write_text(json.dumps(token_data))

        with (
            patch.object(mso, "TOKEN_FILE", str(token_file)),
            patch.object(mso, "_keychain_read", return_value="fake-client-id"),
            patch.object(mso, "_acquire_token_silent", return_value=None),  # refresh fails
        ):
            result = mso.ensure_valid_token(warn_seconds=300)

        # The old timestamp should be preserved (not updated)
        saved = json.loads(token_file.read_text())
        assert saved.get("_last_refresh_success") == old_refresh_time
