"""Graph readiness must validate the credential, not just its expiry field."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import preflight
from preflight import oauth_checks
import setup_msgraph_oauth as graph


@pytest.mark.parametrize("check", [preflight.check_msgraph_token, oauth_checks.check_msgraph_token])
@pytest.mark.parametrize("access", [None, "", 42])
def test_future_expiry_without_access_token_cannot_pass(monkeypatch, check, access):
    token = {"access_token": access, "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    monkeypatch.setattr(graph, "_load_token", lambda: token)
    monkeypatch.setattr(graph, "ensure_valid_token", Mock(side_effect=RuntimeError("synthetic unavailable")))
    import detect_environment
    monkeypatch.setattr(detect_environment, "detect", lambda **_: Mock(capabilities={"network_microsoft": True}))
    result = check()
    assert not result.passed and result.severity == "P1"
    assert "unverified" in result.message


@pytest.mark.parametrize("access", [None, "", 42])
def test_expiry_only_refresh_response_cannot_pass(monkeypatch, access):
    expired = {"access_token": "synthetic-old", "expiry": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    monkeypatch.setattr(graph, "_load_token", lambda: expired)
    monkeypatch.setattr(graph, "ensure_valid_token", lambda **_: {
        "access_token": access, "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    })
    import detect_environment
    monkeypatch.setattr(detect_environment, "detect", lambda **_: Mock(capabilities={"network_microsoft": True}))
    assert not preflight.check_msgraph_token().passed


@pytest.mark.parametrize("expiry", [None, "invalid"])
def test_unknown_expiry_attempts_existing_refresh(monkeypatch, expiry):
    token = {"access_token": "synthetic", "expiry": expiry}
    monkeypatch.setattr(graph, "_load_token", lambda: token)
    refresh = Mock(return_value={
        "access_token": "synthetic-refreshed",
        "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    })
    monkeypatch.setattr(graph, "ensure_valid_token", refresh)
    result = preflight.check_msgraph_token()
    assert result.passed and "refreshed" in result.message
    refresh.assert_called_once()
