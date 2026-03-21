"""
tests/unit/test_home_device_monitor.py — Unit tests for the home_device_monitor skill.

Tests cover: entity classification, offline detection, threshold checks, parse()
signal construction, execute() serialization, atomic state write, and factory.

Run: pytest tests/unit/test_home_device_monitor.py -v
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from skills.home_device_monitor import (
    HomeDeviceMonitorSkill,
    _OFFLINE_THRESHOLD_HOURS,
    _SPA_TEMP_VARIANCE_F,
    _SUPPLY_LOW_PCT,
    _classify_entity,
    _hours_offline,
    _is_offline,
    get_skill,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def artha_dir(tmp_path: Path) -> Path:
    """Return a minimal artha_dir structure for isolation."""
    (tmp_path / "tmp").mkdir()
    (tmp_path / "state").mkdir()
    return tmp_path


@pytest.fixture()
def skill(artha_dir: Path) -> HomeDeviceMonitorSkill:
    return HomeDeviceMonitorSkill(artha_dir)


def _stale_ts(hours_ago: float) -> str:
    """Return ISO-8601 timestamp `hours_ago` hours in the past."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.isoformat()


def _fresh_ts() -> str:
    """Return ISO-8601 timestamp 1 minute ago."""
    return _stale_ts(1 / 60)


def _make_cache(entities: List[dict], fetched_at: str | None = None) -> dict:
    return {
        "entity_count": len(entities),
        "entities": entities,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
    }


def _entity(entity_id: str, state: str = "on", attrs: dict | None = None, last_changed: str | None = None) -> dict:
    domain = entity_id.split(".")[0] if "." in entity_id else entity_id
    return {
        "entity_id": entity_id,
        "state": state,
        "domain": domain,
        "attributes": attrs or {},
        "last_changed": last_changed or _fresh_ts(),
    }


# ── _classify_entity ──────────────────────────────────────────────────────────

class TestClassifyEntity:
    def test_ring_sensor_is_critical(self):
        assert _classify_entity("binary_sensor.ring_front_door") == "CRITICAL"

    def test_ring_attribute_sensor_is_critical(self):
        assert _classify_entity("sensor.ring_doorbell_battery") == "CRITICAL"

    def test_lock_is_critical(self):
        assert _classify_entity("lock.front_door") == "CRITICAL"

    def test_alarm_is_critical(self):
        assert _classify_entity("alarm_control_panel.home") == "CRITICAL"

    def test_light_is_monitored(self):
        assert _classify_entity("light.kitchen") == "MONITORED"

    def test_switch_is_monitored(self):
        assert _classify_entity("switch.front_porch") == "MONITORED"

    def test_gecko_spa_is_monitored(self):
        assert _classify_entity("sensor.gecko_spa_temp") == "MONITORED"

    def test_climate_is_monitored(self):
        assert _classify_entity("climate.living_room") == "MONITORED"

    def test_brother_printer_is_monitored(self):
        assert _classify_entity("sensor.brother_hl_toner") == "MONITORED"

    def test_random_sensor_is_informational(self):
        assert _classify_entity("sensor.humidity") == "INFORMATIONAL"

    def test_device_tracker_is_informational(self):
        assert _classify_entity("device_tracker.phone") == "INFORMATIONAL"


# ── _is_offline ───────────────────────────────────────────────────────────────

class TestIsOffline:
    def test_unavailable(self):
        assert _is_offline("unavailable") is True

    def test_unknown(self):
        assert _is_offline("unknown") is True

    def test_empty_string(self):
        assert _is_offline("") is True

    def test_none_string(self):
        assert _is_offline("none") is True

    def test_on_is_online(self):
        assert _is_offline("on") is False

    def test_off_is_online(self):
        # "off" means device is contactable but switched off
        assert _is_offline("off") is False

    def test_numeric_state_is_online(self):
        assert _is_offline("22.5") is False

    def test_case_insensitive_unavailable(self):
        assert _is_offline("UNAVAILABLE") is True

    def test_whitespace_stripped(self):
        assert _is_offline("  unavailable  ") is True


# ── _hours_offline ────────────────────────────────────────────────────────────

class TestHoursOffline:
    def test_just_now_returns_near_zero(self):
        ts = datetime.now(timezone.utc).isoformat()
        assert _hours_offline(ts) < 0.01

    def test_three_hours_ago(self):
        ts = _stale_ts(3.0)
        hours = _hours_offline(ts)
        assert 2.9 < hours < 3.1

    def test_invalid_string_returns_zero(self):
        assert _hours_offline("not-a-date") == 0.0

    def test_empty_string_returns_zero(self):
        assert _hours_offline("") == 0.0

    def test_z_suffix_handled(self):
        ts = "2026-03-20T10:00:00Z"
        # Just verify it doesn't raise and returns a positive number
        # (Actual value depends on test execution time)
        hours = _hours_offline(ts)
        assert hours >= 0


# ── HomeDeviceMonitorSkill.pull() ─────────────────────────────────────────────

class TestPull:
    def test_returns_none_when_no_cache(self, skill: HomeDeviceMonitorSkill):
        assert skill.pull() is None

    def test_returns_data_when_cache_exists(self, skill: HomeDeviceMonitorSkill):
        entities = [_entity("light.kitchen")]
        data = _make_cache(entities)
        skill._cache_file.write_text(json.dumps(data))
        result = skill.pull()
        assert result is not None
        assert result["entity_count"] == 1

    def test_returns_none_on_corrupt_json(self, skill: HomeDeviceMonitorSkill):
        skill._cache_file.write_text("{corrupt json{{")
        result = skill.pull()
        assert result is None

    def test_returns_none_on_empty_cache(self, skill: HomeDeviceMonitorSkill):
        skill._cache_file.write_text("")
        result = skill.pull()
        assert result is None


# ── HomeDeviceMonitorSkill.parse() ────────────────────────────────────────────

class TestParse:
    def test_none_input_returns_empty_result(self, skill: HomeDeviceMonitorSkill):
        result = skill.parse(None)
        assert result["offline_count"] == 0
        assert result["signals"] == []
        assert "note" in result

    def test_empty_entities_no_signals(self, skill: HomeDeviceMonitorSkill):
        result = skill.parse(_make_cache([]))
        assert result["total_signals"] == 0
        assert result["signals"] == []

    def test_online_ring_no_signal(self, skill: HomeDeviceMonitorSkill):
        ring = _entity("binary_sensor.ring_front_door", "on", last_changed=_fresh_ts())
        result = skill.parse(_make_cache([ring]))
        assert result["critical_offline_count"] == 0
        assert result["signals"] == []

    def test_ring_offline_long_emits_security_signal(self, skill: HomeDeviceMonitorSkill):
        ring = _entity(
            "binary_sensor.ring_front_door", "unavailable",
            last_changed=_stale_ts(_OFFLINE_THRESHOLD_HOURS + 1),
        )
        result = skill.parse(_make_cache([ring]))
        assert result["critical_offline_count"] == 1
        assert result["offline_count"] == 1
        signals = result["signals"]
        assert len(signals) == 1
        s = signals[0]
        assert s.signal_type == "security_device_offline"
        assert s.urgency == 3
        assert s.domain == "home"

    def test_ring_offline_short_no_signal(self, skill: HomeDeviceMonitorSkill):
        """Ring offline < 2h threshold → no signal."""
        ring = _entity(
            "binary_sensor.ring_front_door", "unavailable",
            last_changed=_stale_ts(_OFFLINE_THRESHOLD_HOURS * 0.5),
        )
        result = skill.parse(_make_cache([ring]))
        assert result["critical_offline_count"] == 0

    def test_light_offline_emits_device_offline(self, skill: HomeDeviceMonitorSkill):
        light = _entity(
            "light.kitchen", "unavailable",
            last_changed=_stale_ts(_OFFLINE_THRESHOLD_HOURS + 1),
        )
        result = skill.parse(_make_cache([light]))
        assert result["offline_count"] == 1
        assert result["critical_offline_count"] == 0
        assert result["signals"][0].signal_type == "device_offline"
        assert result["signals"][0].urgency == 2

    def test_informational_offline_no_signal(self, skill: HomeDeviceMonitorSkill):
        """INFORMATIONAL entities do not emit offline signals."""
        sensor = _entity(
            "sensor.humidity", "unavailable",
            last_changed=_stale_ts(10),
        )
        result = skill.parse(_make_cache([sensor]))
        assert result["offline_count"] == 0
        assert result["signals"] == []

    def test_supply_low_emits_signal(self, skill: HomeDeviceMonitorSkill):
        printer = _entity("sensor.brother_hl_toner", "15")
        result = skill.parse(_make_cache([printer]))
        supply_signals = [s for s in result["signals"] if s.signal_type == "supply_low"]
        assert len(supply_signals) == 1
        assert supply_signals[0].metadata["level_pct"] == 15.0

    def test_supply_above_threshold_no_signal(self, skill: HomeDeviceMonitorSkill):
        printer = _entity("sensor.brother_hl_toner", "85")
        result = skill.parse(_make_cache([printer]))
        supply_signals = [s for s in result["signals"] if s.signal_type == "supply_low"]
        assert supply_signals == []

    def test_supply_with_percent_sign_in_state(self, skill: HomeDeviceMonitorSkill):
        printer = _entity("sensor.brother_hl_toner", "10 %")
        result = skill.parse(_make_cache([printer]))
        supply_signals = [s for s in result["signals"] if s.signal_type == "supply_low"]
        assert len(supply_signals) == 1

    def test_spa_variance_emits_signal(self, skill: HomeDeviceMonitorSkill):
        # Current 104°F, setpoint 99°F → variance 5°F = threshold → emits
        spa = _entity("sensor.gecko_spa_temp", "104", attrs={"temperature": "99"})
        result = skill.parse(_make_cache([spa]))
        spa_signals = [s for s in result["signals"] if s.signal_type == "spa_maintenance"]
        assert len(spa_signals) == 1
        assert spa_signals[0].metadata["variance_f"] == 5.0

    def test_spa_within_tolerance_no_signal(self, skill: HomeDeviceMonitorSkill):
        # _SPA_TEMP_VARIANCE_F = 5°F; 3°F variance is fine
        spa = _entity("sensor.gecko_spa_temp", "102", attrs={"temperature": "100"})
        result = skill.parse(_make_cache([spa]))
        spa_signals = [s for s in result["signals"] if s.signal_type == "spa_maintenance"]
        assert spa_signals == []

    def test_multiple_signals_from_mixed_entities(self, skill: HomeDeviceMonitorSkill):
        entities = [
            _entity("binary_sensor.ring_front_door", "unavailable", last_changed=_stale_ts(5)),
            _entity("light.kitchen", "unavailable", last_changed=_stale_ts(3)),
            _entity("sensor.brother_hl_toner", "12"),
        ]
        result = skill.parse(_make_cache(entities))
        assert result["critical_offline_count"] == 1
        assert result["offline_count"] == 2
        assert result["supply_low_count"] == 1
        assert result["total_signals"] == 3

    def test_entity_count_matches_input(self, skill: HomeDeviceMonitorSkill):
        entities = [_entity(f"light.room{i}") for i in range(5)]
        result = skill.parse(_make_cache(entities))
        assert result["entity_count"] == 5


# ── HomeDeviceMonitorSkill.execute() ─────────────────────────────────────────

class TestExecute:
    def test_execute_no_cache_returns_success_with_empty_data(self, skill: HomeDeviceMonitorSkill):
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["offline_count"] == 0
        assert result["data"]["signals"] == []

    def test_execute_serializes_signals_to_dicts(self, artha_dir: Path):
        """DomainSignal objects must be serialized via dataclasses.asdict()."""
        skill = HomeDeviceMonitorSkill(artha_dir)
        entities = [
            _entity("binary_sensor.ring_front_door", "unavailable", last_changed=_stale_ts(5)),
        ]
        skill._cache_file.write_text(json.dumps(_make_cache(entities)))

        result = skill.execute()
        signals = result["data"]["signals"]
        assert len(signals) == 1
        # Must be a plain dict, not a DomainSignal
        assert isinstance(signals[0], dict)
        assert signals[0]["signal_type"] == "security_device_offline"

    def test_execute_signals_json_serializable(self, artha_dir: Path):
        """All signal dicts from execute() must be JSON-serializable."""
        skill = HomeDeviceMonitorSkill(artha_dir)
        entities = [
            _entity("lock.front_door", "unavailable", last_changed=_stale_ts(4)),
        ]
        skill._cache_file.write_text(json.dumps(_make_cache(entities)))
        result = skill.execute()
        # Should not raise json.JSONDecodeError
        payload = json.dumps(result)
        assert "security_device_offline" in payload

    def test_execute_writes_state_file(self, artha_dir: Path):
        skill = HomeDeviceMonitorSkill(artha_dir)
        skill._cache_file.write_text(json.dumps(_make_cache([])))
        skill.execute()
        assert skill._state_file.exists()
        content = skill._state_file.read_text()
        assert "iot_devices:" in content
        assert "skill_run:" in content

    def test_execute_status_is_success(self, skill: HomeDeviceMonitorSkill):
        result = skill.execute()
        assert result["status"] == "success"
        assert result["name"] == "home_device_monitor"

    def test_execute_corrupt_cache_still_succeeds(self, artha_dir: Path):
        """pull() returns None on corrupt JSON → execute() should handle gracefully."""
        skill = HomeDeviceMonitorSkill(artha_dir)
        skill._cache_file.write_text("BAD JSON{{}}}")
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["offline_count"] == 0


# ── compare_fields ────────────────────────────────────────────────────────────

class TestCompareFields:
    def test_expected_fields_present(self, skill: HomeDeviceMonitorSkill):
        fields = skill.compare_fields
        assert "offline_count" in fields
        assert "critical_offline_count" in fields
        assert "energy_anomaly_count" in fields
        assert "supply_low_count" in fields
        assert "spa_maintenance_count" in fields


# ── get_skill factory ─────────────────────────────────────────────────────────

class TestGetSkill:
    def test_returns_skill_instance(self, artha_dir: Path):
        skill = get_skill(artha_dir)
        assert isinstance(skill, HomeDeviceMonitorSkill)

    def test_skill_has_correct_name(self, artha_dir: Path):
        skill = get_skill(artha_dir)
        assert skill.name == "home_device_monitor"

    def test_skill_artha_dir_set(self, artha_dir: Path):
        skill = get_skill(artha_dir)
        assert skill.artha_dir == artha_dir


# ── _write_state_file (atomic write) ─────────────────────────────────────────

class TestWriteStateFile:
    def test_state_file_created(self, skill: HomeDeviceMonitorSkill):
        parsed = {
            "entity_count": 3, "offline_count": 1, "critical_offline_count": 0,
            "energy_anomaly_count": 0, "supply_low_count": 0, "spa_maintenance_count": 0,
            "total_signals": 1, "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        skill._write_state_file(parsed, [])
        assert skill._state_file.exists()

    def test_no_tmp_left_after_write(self, skill: HomeDeviceMonitorSkill):
        parsed = {
            "entity_count": 0, "offline_count": 0, "critical_offline_count": 0,
            "energy_anomaly_count": 0, "supply_low_count": 0, "spa_maintenance_count": 0,
            "total_signals": 0, "fetched_at": "",
        }
        skill._write_state_file(parsed, [])
        tmps = list(skill._state_file.parent.glob(".home_iot_*.tmp"))
        assert tmps == []

    def test_content_contains_expected_sections(self, skill: HomeDeviceMonitorSkill):
        parsed = {
            "entity_count": 10, "offline_count": 2, "critical_offline_count": 1,
            "energy_anomaly_count": 0, "supply_low_count": 0, "spa_maintenance_count": 0,
            "total_signals": 2, "fetched_at": "2026-03-20T12:00:00+00:00",
        }
        skill._write_state_file(parsed, [])
        content = skill._state_file.read_text()
        assert "iot_devices:" in content
        assert "iot_energy:" in content
        assert "skill_run:" in content
        assert "critical_offline: [" in content


# ── DomainSignal contract (§3.7b, round-trip) ────────────────────────────────

class TestDomainSignalRoundTrip:
    """Verify that DomainSignal can be serialized and deserialized correctly."""

    def test_asdict_round_trip(self):
        from actions.base import DomainSignal
        sig = DomainSignal(
            signal_type="security_device_offline",
            domain="home",
            entity="binary_sensor.ring_front_door",
            urgency=3,
            impact=3,
            source="skill:home_device_monitor",
            metadata={"hours_offline": 4.0},
            detected_at="2026-03-20T13:00:00+00:00",
        )
        as_dict = dataclasses.asdict(sig)
        restored = DomainSignal(**as_dict)
        assert restored.signal_type == sig.signal_type
        assert restored.urgency == sig.urgency
        assert restored.metadata["hours_offline"] == 4.0

    def test_json_serializable_via_asdict(self):
        from actions.base import DomainSignal
        sig = DomainSignal(
            signal_type="device_offline",
            domain="home",
            entity="light.kitchen",
            urgency=2,
            impact=2,
            source="skill:home_device_monitor",
            metadata={"entity_id": "light.kitchen"},
            detected_at="2026-03-20T13:00:00+00:00",
        )
        payload = json.dumps(dataclasses.asdict(sig))
        assert "device_offline" in payload
