"""
tests/unit/test_homeassistant_connector.py — Unit tests for the HA connector.

Tests cover: LAN detection, entity filtering, privacy sanitization,
fetch() orchestration, health_check(), atomic cache write, and auth
context handling.

Run: pytest tests/unit/test_homeassistant_connector.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from connectors.homeassistant import (
    ConnectorOffLAN,
    _domain_of,
    _is_private_address,
    _sanitize_attributes,
    _sanitize_tracker_state,
    _should_include,
    _write_entity_cache,
    fetch,
    health_check,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entity(entity_id: str, state: str = "on", attrs: dict | None = None, last_changed: str = "2026-03-20T10:00:00+00:00") -> dict:
    """Build a minimal HA entity dict."""
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attrs or {},
        "last_changed": last_changed,
    }


_VALID_AUTH = {"provider": "homeassistant", "method": "api_key", "api_key": "mock-token-abc123"}

# ── _domain_of ────────────────────────────────────────────────────────────────

class TestDomainOf:
    def test_standard(self):
        assert _domain_of("light.kitchen") == "light"

    def test_no_dot(self):
        assert _domain_of("orphan") == "orphan"

    def test_multiple_dots(self):
        assert _domain_of("sensor.gecko_spa.water_temp") == "sensor"


# ── _is_private_address ───────────────────────────────────────────────────────

class TestIsPrivateAddress:
    def test_rfc1918_192(self):
        assert _is_private_address("192.168.1.123") is True

    def test_rfc1918_10(self):
        assert _is_private_address("10.0.0.1") is True

    def test_rfc1918_172(self):
        assert _is_private_address("172.16.0.1") is True

    def test_public(self):
        assert _is_private_address("8.8.8.8") is False

    def test_localhost(self):
        assert _is_private_address("127.0.0.1") is True

    def test_invalid_string(self):
        # Cannot resolve → not private
        assert _is_private_address("not-a-valid-host.invalid") is False


# ── _sanitize_tracker_state ───────────────────────────────────────────────────

class TestSanitizeTrackerState:
    def test_home(self):
        assert _sanitize_tracker_state("home") == "home"

    def test_not_home(self):
        assert _sanitize_tracker_state("not_home") == "not_home"

    def test_away_collapses(self):
        # Location zones collapse to "unknown"
        assert _sanitize_tracker_state("work") == "unknown"
        assert _sanitize_tracker_state("school") == "unknown"

    def test_case_insensitive(self):
        assert _sanitize_tracker_state("HOME") == "home"


# ── _sanitize_attributes ──────────────────────────────────────────────────────

class TestSanitizeAttributes:
    def test_strips_ip_address(self):
        attrs = {"ip_address": "192.168.1.5", "friendly_name": "Ring Doorbell"}
        result = _sanitize_attributes("binary_sensor.ring_front", "binary_sensor", attrs)
        assert "ip_address" not in result
        assert result.get("friendly_name") == "Ring Doorbell"

    def test_strips_mac_address(self):
        attrs = {"mac_address": "aa:bb:cc:dd:ee:ff", "battery": 85}
        result = _sanitize_attributes("sensor.ring_battery", "sensor", attrs)
        assert "mac_address" not in result
        assert result["battery"] == 85

    def test_strips_large_blob(self):
        attrs = {"entity_picture": "A" * 3000, "unit_of_measurement": "W"}
        result = _sanitize_attributes("sensor.power", "sensor", attrs)
        assert "entity_picture" not in result
        assert result["unit_of_measurement"] == "W"

    def test_strips_token(self):
        attrs = {"access_token": "secret", "state_class": "measurement"}
        result = _sanitize_attributes("sensor.energy", "sensor", attrs)
        assert "access_token" not in result

    def test_preserves_safe_attrs(self):
        attrs = {"unit_of_measurement": "W", "device_class": "power", "battery": 95}
        result = _sanitize_attributes("sensor.ring_battery", "sensor", attrs)
        assert result == attrs


# ── _should_include ───────────────────────────────────────────────────────────

class TestShouldInclude:
    def test_hard_floor_camera(self):
        assert _should_include("camera.front_door", "camera", [], [], []) is False

    def test_hard_floor_media_player(self):
        assert _should_include("media_player.living_room", "media_player", [], [], []) is False

    def test_light_included_by_default(self):
        assert _should_include("light.kitchen", "light", [], [], []) is True

    def test_blocklist_excludes(self):
        assert _should_include("sensor.test", "sensor", [], ["sensor.test"], []) is False

    def test_blocklist_glob(self):
        assert _should_include("sensor.gecko_temp", "sensor", [], ["sensor.gecko_*"], []) is False

    def test_allowlist_restricts(self):
        # Only "light.*" in allowlist — sensor should be excluded
        result = _should_include("sensor.power", "sensor", ["light.*"], [], [])
        assert result is False

    def test_allowlist_permits(self):
        result = _should_include("light.bedroom", "light", ["light.*"], [], [])
        assert result is True

    def test_extra_exclude_domain(self):
        assert _should_include("automation.night_mode", "automation", [], [], ["automation"]) is False

    def test_blocked_before_allowed(self):
        # Blocklist wins over allowlist
        result = _should_include("light.kitchen", "light", ["light.*"], ["light.kitchen"], [])
        assert result is False


# ── fetch() ───────────────────────────────────────────────────────────────────

class TestFetch:
    def _api_response(self, entities: List[dict]) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = entities
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_basic_fetch_yields_records(self, mock_get, mock_lan):
        entities = [
            _make_entity("light.kitchen", "on"),
            _make_entity("sensor.temperature", "22.5"),
        ]
        mock_get.return_value = self._api_response(entities)

        results = list(fetch(
            since="2026-03-20T00:00:00Z",
            max_results=100,
            auth_context=_VALID_AUTH,
            ha_url="http://192.168.1.123:8123",
        ))
        assert len(results) == 2
        assert results[0]["entity_id"] == "light.kitchen"
        assert results[0]["source"] == "homeassistant"

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_hard_floor_excluded(self, mock_get, mock_lan):
        entities = [
            _make_entity("camera.front_door", "idle"),
            _make_entity("light.kitchen", "on"),
            _make_entity("media_player.tv", "idle"),
        ]
        mock_get.return_value = self._api_response(entities)

        results = list(fetch(
            since="", max_results=100, auth_context=_VALID_AUTH,
            ha_url="http://192.168.1.123:8123",
        ))
        entity_ids = [r["entity_id"] for r in results]
        assert "camera.front_door" not in entity_ids
        assert "media_player.tv" not in entity_ids
        assert "light.kitchen" in entity_ids

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_device_tracker_sanitized(self, mock_get, mock_lan):
        entities = [_make_entity("device_tracker.phone", "work")]
        mock_get.return_value = self._api_response(entities)

        results = list(fetch(
            since="", max_results=100, auth_context=_VALID_AUTH,
            ha_url="http://192.168.1.123:8123",
        ))
        assert results[0]["state"] == "unknown"

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_max_results_respected(self, mock_get, mock_lan):
        entities = [_make_entity(f"light.room{i}", "on") for i in range(50)]
        mock_get.return_value = self._api_response(entities)

        results = list(fetch(
            since="", max_results=10, auth_context=_VALID_AUTH,
            ha_url="http://192.168.1.123:8123",
        ))
        assert len(results) == 10

    def test_missing_ha_url_raises(self):
        with pytest.raises(RuntimeError, match="ha_url not configured"):
            list(fetch(since="", max_results=10, auth_context=_VALID_AUTH, ha_url=""))

    def test_missing_api_key_raises(self):
        with pytest.raises(RuntimeError, match="api_key not in auth_context"):
            list(fetch(
                since="", max_results=10,
                auth_context={"provider": "homeassistant", "method": "api_key"},
                ha_url="http://192.168.1.123:8123",
            ))

    @patch("connectors.homeassistant._check_lan_or_raise", side_effect=ConnectorOffLAN("off LAN"))
    def test_off_lan_raises_connectorofflan(self, mock_lan):
        with pytest.raises(ConnectorOffLAN):
            list(fetch(
                since="", max_results=10, auth_context=_VALID_AUTH,
                ha_url="http://192.168.1.123:8123",
            ))

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_401_raises_runtime_error(self, mock_get, mock_lan):
        import requests as req
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        err = req.exceptions.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = err
        mock_get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="Authentication failed"):
            list(fetch(
                since="", max_results=10, auth_context=_VALID_AUTH,
                ha_url="http://192.168.1.123:8123",
            ))

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_blocklist_applied(self, mock_get, mock_lan):
        entities = [
            _make_entity("sensor.gecko_spa", "38"),
            _make_entity("light.kitchen", "on"),
        ]
        mock_get.return_value = self._api_response(entities)

        results = list(fetch(
            since="", max_results=100, auth_context=_VALID_AUTH,
            ha_url="http://192.168.1.123:8123",
            entity_blocklist=["sensor.gecko_*"],
        ))
        entity_ids = [r["entity_id"] for r in results]
        assert "sensor.gecko_spa" not in entity_ids
        assert "light.kitchen" in entity_ids

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_cache_written_after_fetch(self, mock_get, mock_lan, tmp_path):
        """Verify atomic cache write is called after successful fetch."""
        entities = [_make_entity("light.kitchen", "on")]
        mock_get.return_value = self._api_response(entities)

        with patch("connectors.homeassistant._CACHE_FILE", tmp_path / "ha_entities.json"):
            list(fetch(
                since="", max_results=100, auth_context=_VALID_AUTH,
                ha_url="http://192.168.1.123:8123",
            ))
            cache_path = tmp_path / "ha_entities.json"
            assert cache_path.exists()
            data = json.loads(cache_path.read_text())
            assert data["entity_count"] == 1
            assert data["entities"][0]["entity_id"] == "light.kitchen"


# ── health_check() ────────────────────────────────────────────────────────────

class TestHealthCheck:
    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_happy_path(self, mock_get, mock_lan):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "API running."}
        mock_get.return_value = mock_resp

        result = health_check(_VALID_AUTH, ha_url="http://192.168.1.123:8123")
        assert result is True

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get")
    def test_wrong_message(self, mock_get, mock_lan):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "Something else"}
        mock_get.return_value = mock_resp

        result = health_check(_VALID_AUTH, ha_url="http://192.168.1.123:8123")
        assert result is False

    def test_missing_url_returns_false(self):
        assert health_check(_VALID_AUTH, ha_url="") is False

    def test_missing_token_returns_false(self):
        assert health_check({"provider": "homeassistant"}, ha_url="http://192.168.1.123:8123") is False

    @patch("connectors.homeassistant._check_lan_or_raise", side_effect=ConnectorOffLAN("off LAN"))
    def test_off_lan_returns_false(self, mock_lan):
        result = health_check(_VALID_AUTH, ha_url="http://192.168.1.123:8123")
        assert result is False

    @patch("connectors.homeassistant._check_lan_or_raise")
    @patch("connectors.homeassistant.requests.get", side_effect=Exception("network error"))
    def test_exception_returns_false(self, mock_get, mock_lan):
        result = health_check(_VALID_AUTH, ha_url="http://192.168.1.123:8123")
        assert result is False


# ── _write_entity_cache (atomic write) ───────────────────────────────────────

class TestWriteEntityCache:
    def test_atomic_write(self, tmp_path):
        records = [{"entity_id": "light.test", "state": "on", "domain": "light"}]
        cache_path = tmp_path / "ha_entities.json"
        with patch("connectors.homeassistant._CACHE_FILE", cache_path):
            _write_entity_cache(records)
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert data["entity_count"] == 1
        assert data["entities"][0]["entity_id"] == "light.test"

    def test_empty_records(self, tmp_path):
        cache_path = tmp_path / "ha_entities.json"
        with patch("connectors.homeassistant._CACHE_FILE", cache_path):
            _write_entity_cache([])
        data = json.loads(cache_path.read_text())
        assert data["entity_count"] == 0
        assert data["entities"] == []

    def test_no_temp_file_left_on_success(self, tmp_path):
        records = [{"entity_id": "light.x", "state": "on"}]
        cache_path = tmp_path / "ha_entities.json"
        with patch("connectors.homeassistant._CACHE_FILE", cache_path):
            _write_entity_cache(records)
        # After success: only ha_entities.json should exist, no .tmp files
        tmps = list(tmp_path.glob("*.tmp"))
        assert tmps == []


# ── auth.py G1 fix ────────────────────────────────────────────────────────────

class TestAuthG1Fix:
    """Verify load_auth_context() api_key branch loads actual credential from keyring."""

    def test_api_key_branch_calls_load_api_key(self):
        from lib.auth import load_auth_context
        connector_cfg = {
            "provider": "homeassistant",
            "auth": {
                "method": "api_key",
                "credential_key": "artha-ha-token",
            },
        }
        with patch("lib.auth.load_api_key", return_value="mock-ha-token-xyz") as mock_load:
            result = load_auth_context(connector_cfg)
        mock_load.assert_called_once_with("artha-ha-token")
        assert result["api_key"] == "mock-ha-token-xyz"
        assert result["method"] == "api_key"
        assert result["provider"] == "homeassistant"

    def test_api_key_branch_no_credential_key_returns_stub(self):
        """Canvas pattern: no credential_key → returns stub (old behavior preserved)."""
        from lib.auth import load_auth_context
        connector_cfg = {
            "provider": "canvas_lms",
            "auth": {"method": "api_key"},  # no credential_key
        }
        result = load_auth_context(connector_cfg)
        assert result["method"] == "api_key"
        assert "api_key" not in result  # Canvas still loads per-child keys itself
