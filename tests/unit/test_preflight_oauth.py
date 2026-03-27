"""tests/unit/test_preflight_oauth.py — T5-11..20: preflight.oauth_checks tests."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import preflight as pf


# ---------------------------------------------------------------------------
# T5-11..13: check_oauth_token
# ---------------------------------------------------------------------------

class TestCheckOauthToken:
    def test_missing_token_dir_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path / "nonexistent"))
        result = pf.check_oauth_token("google", "google_token.json")
        assert isinstance(result, pf.CheckResult)
        assert not result.passed
        assert result.severity == "P0"

    def test_valid_token_file_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        token_data = {
            "token": "ya29.xxxxxxxxxxx",
            "refresh_token": "1//xxxxxxxxxxx",
            "expiry": (time.time() + 3600),
        }
        (tmp_path / "google_token.json").write_text(json.dumps(token_data))
        result = pf.check_oauth_token("google", "google_token.json")
        assert isinstance(result, pf.CheckResult)

    def test_corrupted_json_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        (tmp_path / "google_token.json").write_text("{NOT VALID JSON {{}")
        result = pf.check_oauth_token("google", "google_token.json")
        assert isinstance(result, pf.CheckResult)
        assert not result.passed


# ---------------------------------------------------------------------------
# T5-14..15: check_token_freshness
# ---------------------------------------------------------------------------

class TestCheckTokenFreshness:
    def test_fresh_token_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        token_data = {
            "token": "ya29.xxxxxxxxxxx",
            "expiry": (time.time() + 3600),
        }
        (tmp_path / "google_token.json").write_text(json.dumps(token_data))
        result = pf.check_token_freshness("google", "google_token.json")
        assert isinstance(result, pf.CheckResult)

    def test_stale_token_returns_p1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        token_data = {
            "token": "ya29.xxxxxxxxxxx",
            "expiry": (time.time() - 7200),   # expired 2 h ago
        }
        (tmp_path / "google_token.json").write_text(json.dumps(token_data))
        result = pf.check_token_freshness("google", "google_token.json")
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")


# ---------------------------------------------------------------------------
# T5-16..17: check_msgraph_token
# ---------------------------------------------------------------------------

class TestCheckMsgraphToken:
    def test_missing_token_non_blocking(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        result = pf.check_msgraph_token()
        assert isinstance(result, pf.CheckResult)
        # Missing MS Graph token should NOT be P0 (it's optional)

    def test_valid_msgraph_token_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        token_data = {
            "access_token": "eyJ...",
            "expires_in": 3600,
            "refresh_token": "0.AXXXXXXXXX",
        }
        (tmp_path / "msgraph_token.json").write_text(json.dumps(token_data))
        result = pf.check_msgraph_token()
        assert isinstance(result, pf.CheckResult)
