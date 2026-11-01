"""
scripts/skills/home_device_monitor.py — Home Assistant device monitoring skill.

Reads cached HA entity data (written atomically by the homeassistant connector
to tmp/ha_entities.json) and applies deterministic thresholds to emit DomainSignal
objects without LLM involvement.

Signal types emitted (see action_composer._SIGNAL_ROUTING for routing):
  security_device_offline  — Ring / lock / alarm offline >2h (CRITICAL, friction: high)
  device_offline           — Monitored device offline >2h  (STANDARD friction)
  energy_anomaly           — Power usage >30% above 7-day avg
  supply_low               — Printer toner/drum <20%
  spa_maintenance          — Swim spa temp variance >5°F or error state

Privacy contract (§3.1a):
  - Reads state string only from cache — no attributes that could contain PII
  - device_tracker entities already sanitised by connector (home/not_home/unknown)
  - Entity IDs in metadata are not displayed to user directly

Signal serialization (§3.7b):
  - execute() serializes DomainSignal objects via dataclasses.asdict() for JSON cache
  - Signals deserialized in skill_runner.py as DomainSignal(**signal_dict)

Ref: specs/iot.md §3.7, §3.7b, §3.10
"""
from __future__ import annotations

import dataclasses
import fnmatch
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_SKILLS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _SKILLS_DIR.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from .base_skill import BaseSkill
from actions.base import DomainSignal  # type: ignore[import]

# ---------------------------------------------------------------------------
# Device classification constants (CQ-HA6 RESOLVED 2026-03-20)
# ---------------------------------------------------------------------------
# Uses fnmatch glob patterns — matching via fnmatch.fnmatch(entity_id, pattern)

_CRITICAL_DEVICES: frozenset[str] = frozenset({
    "binary_sensor.ring_*",       # All Ring sensors (doorbell, motion, contact)
    "sensor.ring_*",              # All Ring attribute sensors
    "alarm_control_panel.*",      # Security system / alarm panels
    "lock.*",                     # Smart locks (front door, garage)
})

_MONITORED_DEVICES: frozenset[str] = frozenset({
    "light.*",                    # Smart lights (confirmed MONITORED 2026-03-20)
    "switch.*",                   # Smart switches/plugs
    "sensor.brother_*",           # Brother printer sensors (toner, drum)
    "climate.*",                  # HVAC / thermostat
    "water_heater.*",             # Water heater
    "sensor.gecko_*",             # Swim spa (Gecko protocol integration)
})

# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------
_OFFLINE_THRESHOLD_HOURS: int = 2    # Device unreachable > 2h → offline alert
_ENERGY_SPIKE_PCT: float = 30.0      # > 30% above 7-day avg → energy anomaly
_SUPPLY_LOW_PCT: float = 20.0        # Printer toner/drum < 20% → supply_low
_SPA_TEMP_VARIANCE_F: float = 5.0   # Swim spa > 5°F variance from set point

# States that indicate a device is offline / unavailable
_OFFLINE_STATES: frozenset[str] = frozenset({
    "unavailable", "unknown", "none", ""
})

# Cache file written by homeassistant connector
_CACHE_FILE = _REPO_ROOT / "tmp" / "ha_entities.json"

# State file written by this skill
_STATE_FILE = _REPO_ROOT / "state" / "home_iot.md"

# Skill cache for skill_runner.py delta detection
_SKILL_CACHE_FILE = _REPO_ROOT / "tmp" / "ha_skill_output.json"


# ---------------------------------------------------------------------------
# Device classification helpers
# ---------------------------------------------------------------------------

def _classify_entity(entity_id: str) -> str:
    """Return 'CRITICAL', 'MONITORED', or 'INFORMATIONAL' for an entity."""
    for pattern in _CRITICAL_DEVICES:
        if fnmatch.fnmatch(entity_id, pattern):
            return "CRITICAL"
    for pattern in _MONITORED_DEVICES:
        if fnmatch.fnmatch(entity_id, pattern):
            return "MONITORED"
    return "INFORMATIONAL"


def _is_offline(state: str) -> bool:
    """Return True if the entity state indicates it is offline/unavailable."""
    return state.strip().lower() in _OFFLINE_STATES


def _hours_offline(last_changed: str) -> float:
    """Return hours since last_changed. Returns 0.0 on parse error."""
    try:
        if last_changed.endswith("Z"):
            last_changed = last_changed[:-1] + "+00:00"
        changed_at = datetime.fromisoformat(last_changed)
        if changed_at.tzinfo is None:
            changed_at = changed_at.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - changed_at
        return delta.total_seconds() / 3600
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Main skill class
# ---------------------------------------------------------------------------

class HomeDeviceMonitorSkill(BaseSkill):
    """Deterministic Home Assistant device health + energy monitoring skill.

    Reads from tmp/ha_entities.json (written atomically by the HA connector).
    Emits DomainSignal objects without any LLM call.
    """

    def __init__(self, artha_dir: Path) -> None:
        super().__init__(name="home_device_monitor", priority="P1")
        self.artha_dir = artha_dir
        self._cache_file = artha_dir / "tmp" / "ha_entities.json"
        self._state_file = artha_dir / "state" / "home_iot.md"

    @property
    def compare_fields(self) -> List[str]:
        """Fields used by skill_runner.py for delta detection."""
        return [
            "offline_count",
            "critical_offline_count",
            "energy_anomaly_count",
            "supply_low_count",
            "spa_maintenance_count",
            "automation_failure_count",
        ]

    # ── pull() ──────────────────────────────────────────────────────────

    def pull(self) -> Any:
        """Read cached HA entity snapshot from tmp/ha_entities.json.

        Returns the parsed dict on success.
        Returns None if cache is missing (connector hasn't run yet).
        Returns None if cache is structurally invalid (JSONDecodeError).
        This is a read-only operation — no network calls.
        """
        if not self._cache_file.exists():
            logging.debug("[home_device_monitor] Cache not found: %s", self._cache_file)
            return None

        try:
            with open(self._cache_file, encoding="utf-8") as fh:
                data = json.load(fh)
            return data
        except json.JSONDecodeError as exc:
            logging.warning("[home_device_monitor] Cache JSON corrupt: %s", exc)
            return None
        except OSError as exc:
            logging.warning("[home_device_monitor] Cannot read cache: %s", exc)
            return None

    # ── parse() ─────────────────────────────────────────────────────────

    def parse(self, raw_data: Any) -> Dict[str, Any]:
        """Apply deterministic thresholds to entity snapshot.

        Returns a dict with:
          signals: list[DomainSignal]          — emitted signals
          offline_count: int                   — total monitored devices offline
          critical_offline_count: int          — CRITICAL devices offline
          automation_failure_count: int        — broken or failed automations
          energy_anomaly_count: int
          supply_low_count: int
          spa_maintenance_count: int
          fetched_at: str                      — ISO-8601 cache timestamp
          entity_count: int
        """
        if raw_data is None:
            return self._empty_result("No HA cache — connector not yet run")

        entities: List[dict] = raw_data.get("entities", [])
        fetched_at: str = raw_data.get("fetched_at", "")
        signals: List[DomainSignal] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        offline_count = 0
        critical_offline_count = 0
        automation_failure_count = 0
        energy_anomaly_count = 0
        supply_low_count = 0
        spa_maintenance_count = 0

        # Track energy entities for spike detection
        power_values: List[float] = []
        energy_history: List[float] = []

        for entity in entities:
            entity_id: str = entity.get("entity_id", "")
            state: str = entity.get("state", "")
            attrs: dict = entity.get("attributes", {}) or {}
            last_changed: str = entity.get("last_changed", "")
            domain: str = entity.get("domain", entity_id.split(".")[0] if "." in entity_id else "")

            classification = _classify_entity(entity_id)

            # ── Automation Health ───────────────────────────────────────
            if domain == "automation":
                # Check for 'unavailable' (entity broken) or specific error markers
                if _is_offline(state):
                    automation_failure_count += 1
                    signals.append(DomainSignal(
                        signal_type="automation_failure",
                        domain="home",
                        entity=entity_id,
                        urgency=2,
                        impact=2,
                        source="skill:home_device_monitor",
                        metadata={
                            "entity_id": entity_id,
                            "friendly_name": attrs.get("friendly_name", entity_id),
                            "reason": f"Automation entity is in '{state}' state",
                        },
                        detected_at=now_iso,
                    ))

            # ── Offline detection ────────────────────────────────────────
            if classification in ("CRITICAL", "MONITORED") and _is_offline(state):
                hours = _hours_offline(last_changed)
                if hours >= _OFFLINE_THRESHOLD_HOURS:
                    offline_count += 1
                    if classification == "CRITICAL":
                        critical_offline_count += 1
                        signals.append(DomainSignal(
                            signal_type="security_device_offline",
                            domain="home",
                            entity=entity_id,
                            urgency=3,
                            impact=3,
                            source="skill:home_device_monitor",
                            metadata={
                                "entity_id": entity_id,
                                "classification": "CRITICAL",
                                "hours_offline": round(hours, 1),
                                "last_changed": last_changed,
                                "reason": f"Security device offline for {hours:.1f}h",
                            },
                            detected_at=now_iso,
                        ))
                    else:
                        signals.append(DomainSignal(
                            signal_type="device_offline",
                            domain="home",
                            entity=entity_id,
                            urgency=2,
                            impact=2,
                            source="skill:home_device_monitor",
                            metadata={
                                "entity_id": entity_id,
                                "classification": "MONITORED",
                                "hours_offline": round(hours, 1),
                                "last_changed": last_changed,
                                "reason": f"Device offline for {hours:.1f}h",
                            },
                            detected_at=now_iso,
                        ))

            # ── Supply level detection (Brother printer) ─────────────────
            if fnmatch.fnmatch(entity_id, "sensor.brother_*"):
                level = self._extract_supply_level(state, attrs)
                if level is not None and level < _SUPPLY_LOW_PCT:
                    supply_low_count += 1
                    signals.append(DomainSignal(
                        signal_type="supply_low",
                        domain="home",
                        entity=entity_id,
                        urgency=1,
                        impact=1,
                        source="skill:home_device_monitor",
                        metadata={
                            "entity_id": entity_id,
                            "level_pct": round(level, 1),
                            "reason": f"Supply level {level:.0f}% (threshold {_SUPPLY_LOW_PCT:.0f}%)",
                        },
                        detected_at=now_iso,
                    ))

            # ── Swim spa temperature variance (Gecko) ───────────────────
            if fnmatch.fnmatch(entity_id, "sensor.gecko_*") or domain in ("water_heater",):
                variance = self._extract_temp_variance(entity_id, state, attrs)
                if variance is not None and abs(variance) >= _SPA_TEMP_VARIANCE_F:
                    spa_maintenance_count += 1
                    signals.append(DomainSignal(
                        signal_type="spa_maintenance",
                        domain="home",
                        entity=entity_id,
                        urgency=1,
                        impact=1,
                        source="skill:home_device_monitor",
                        metadata={
                            "entity_id": entity_id,
                            "variance_f": round(variance, 1),
                            "reason": (
                                f"Spa temp {'+' if variance > 0 else ''}{variance:.1f}°F "
                                f"from set point (threshold ±{_SPA_TEMP_VARIANCE_F}°F)"
                            ),
                        },
                        detected_at=now_iso,
                    ))

            # ── Energy tracking (power sensors) ─────────────────────────
            if "power" in entity_id.lower() and domain == "sensor":
                try:
                    power_values.append(float(state))
                except (ValueError, TypeError):
                    pass

        # ── Energy spike detection (aggregate) ──────────────────────────
        if power_values:
            current_power = sum(power_values)
            # Use cached history from state file if available
            historical_avg = self._load_weekly_avg_power()
            if historical_avg and historical_avg > 0:
                spike_pct = ((current_power - historical_avg) / historical_avg) * 100
                if spike_pct > _ENERGY_SPIKE_PCT:
                    energy_anomaly_count += 1
                    signals.append(DomainSignal(
                        signal_type="energy_anomaly",
                        domain="home",
                        entity="iot_energy",
                        urgency=1,
                        impact=1,
                        source="skill:home_device_monitor",
                        metadata={
                            "current_power_w": round(current_power, 1),
                            "weekly_avg_w": round(historical_avg, 1),
                            "spike_pct": round(spike_pct, 1),
                            "reason": (
                                f"Energy usage {spike_pct:.0f}% above 7-day average "
                                f"({current_power:.0f}W vs {historical_avg:.0f}W avg)"
                            ),
                        },
                        detected_at=now_iso,
                    ))

        result = {
            "signals": signals,               # List[DomainSignal] — serialized in execute()
            "offline_count": offline_count,
            "critical_offline_count": critical_offline_count,
            "automation_failure_count": automation_failure_count,
            "energy_anomaly_count": energy_anomaly_count,
            "supply_low_count": supply_low_count,
            "spa_maintenance_count": spa_maintenance_count,
            "fetched_at": fetched_at,
            "entity_count": len(entities),
            "total_signals": len(signals),
        }
        return result

    # ── execute() ───────────────────────────────────────────────────────

    def execute(self) -> Dict[str, Any]:
        """Orchestrate pull + parse, serialize signals, update state file.

        Signal serialization: DomainSignal → dict via dataclasses.asdict().
        This guarantees JSON round-trip fidelity (§3.7b).
        """
        self.status = "running"
        self.last_run = datetime.now(timezone.utc).isoformat()

        try:
            raw = self.pull()
            parsed = self.parse(raw)

            # Serialize DomainSignal objects → plain dicts for JSON cache
            signals_raw: List[DomainSignal] = parsed.pop("signals", [])
            parsed["signals"] = [dataclasses.asdict(s) for s in signals_raw]

            self.status = "success"
            result = {
                "name": self.name,
                "status": self.status,
                "timestamp": self.last_run,
                "data": parsed,
            }

            # Write IoT state file
            self._write_state_file(parsed, signals_raw)

            return result

        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            logging.error("[home_device_monitor] Skill failed: %s", exc)
            return {
                "name": self.name,
                "status": self.status,
                "timestamp": self.last_run,
                "error": self.error,
                "data": self._empty_result(f"Execution error: {exc}"),
            }

    # ── to_dict() ───────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Return skill metadata dict."""
        return {
            "name": self.name,
            "priority": self.priority,
            "status": self.status,
            "last_run": self.last_run,
            "error": self.error,
        }

    # ── Private helpers ──────────────────────────────────────────────────

    def _empty_result(self, reason: str = "") -> Dict[str, Any]:
        """Return a zero-signal result dict."""
        return {
            "signals": [],
            "offline_count": 0,
            "critical_offline_count": 0,
            "automation_failure_count": 0,
            "energy_anomaly_count": 0,
            "supply_low_count": 0,
            "spa_maintenance_count": 0,
            "fetched_at": "",
            "entity_count": 0,
            "total_signals": 0,
            "note": reason,
        }

    def _extract_supply_level(self, state: str, attrs: dict) -> Optional[float]:
        """Extract supply percentage from printer sensor state or attributes."""
        # Try state directly (common pattern: state is "15 %")
        try:
            cleaned = state.replace("%", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            pass
        # Try attributes
        for key in ("toner_level", "drum_level", "ink_level", "supply_level", "level"):
            val = attrs.get(key)
            if val is not None:
                try:
                    return float(str(val).replace("%", "").strip())
                except (ValueError, TypeError):
                    pass
        return None

    def _extract_temp_variance(
        self, entity_id: str, state: str, attrs: dict
    ) -> Optional[float]:
        """Return actual - setpoint temperature (°F) for spa/climate sensors."""
        # Only check Gecko spa or climate entities
        if not (fnmatch.fnmatch(entity_id, "sensor.gecko_*") or "spa" in entity_id.lower()):
            return None
        try:
            current_temp = float(state)
        except (ValueError, TypeError):
            return None
        set_temp = attrs.get("target_temp_high") or attrs.get("temperature")
        if set_temp is not None:
            try:
                return current_temp - float(set_temp)
            except (ValueError, TypeError):
                pass
        return None

    def _load_weekly_avg_power(self) -> Optional[float]:
        """Load rolling weekly average power from state/home_iot.md."""
        if not self._state_file.exists():
            return None
        try:
            content = self._state_file.read_text(encoding="utf-8")
            # Parse YAML-ish line: "weekly_avg_kwh: 12.5"
            for line in content.splitlines():
                if "weekly_avg_kwh:" in line:
                    _, _, val = line.partition(":")
                    kwh = float(val.strip())
                    # Convert daily kWh to average watts: kWh * 1000 / 24
                    return (kwh * 1000) / 24
        except Exception:
            pass
        return None

    def _write_state_file(self, parsed: Dict, signals: List[DomainSignal]) -> None:
        """Write IoT device status to state/home_iot.md atomically."""
        import tempfile

        critical_offline = [
            s.metadata.get("entity_id", s.entity)
            for s in signals
            if s.signal_type == "security_device_offline"
        ]
        automation_failures = [
            s.metadata.get("friendly_name", s.entity)
            for s in signals
            if s.signal_type == "automation_failure"
        ]
        supply_alerts = [
            {
                "entity_id": s.metadata.get("entity_id", s.entity),
                "level_pct": s.metadata.get("level_pct"),
            }
            for s in signals
            if s.signal_type == "supply_low"
        ]
        spike_signal = next(
            (s for s in signals if s.signal_type == "energy_anomaly"), None
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        fetched_at = parsed.get("fetched_at", now_iso)

        content = f"""# Home IoT State (Machine-Managed)
> Auto-generated by home_device_monitor skill. Do not hand-edit.
> Last updated: {now_iso}

## IoT Devices
iot_devices:
  last_sync: {fetched_at}
  total_entities: {parsed.get('entity_count', 0)}
  online: {parsed.get('entity_count', 0) - parsed.get('offline_count', 0)}
  offline: {parsed.get('offline_count', 0)}
  critical_offline: {json.dumps(critical_offline)}
  automation_failures: {json.dumps(automation_failures)}
  supply_alerts: {json.dumps(supply_alerts)}

## Smart Home Energy
iot_energy:
  last_updated: {now_iso}
  spike_detected: {str(spike_signal is not None).lower()}
  spike_pct: {spike_signal.metadata.get('spike_pct', 0) if spike_signal else 0}
  current_power_w: {spike_signal.metadata.get('current_power_w', 0) if spike_signal else 0}
  weekly_avg_w: {spike_signal.metadata.get('weekly_avg_w', 0) if spike_signal else 0}

## Last Skill Run
skill_run:
  timestamp: {now_iso}
  total_signals: {parsed.get('total_signals', 0)}
  offline_count: {parsed.get('offline_count', 0)}
  critical_offline_count: {parsed.get('critical_offline_count', 0)}
  automation_failure_count: {parsed.get('automation_failure_count', 0)}
  energy_anomaly_count: {parsed.get('energy_anomaly_count', 0)}
  supply_low_count: {parsed.get('supply_low_count', 0)}
  spa_maintenance_count: {parsed.get('spa_maintenance_count', 0)}
"""
        state_dir = self._state_file.parent
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=state_dir, suffix=".tmp", prefix=".home_iot_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            os.replace(tmp_path, self._state_file)
        except Exception as exc:
            logging.warning("[home_device_monitor] Failed to write state file: %s", exc)


# ---------------------------------------------------------------------------
# Factory function (required by skill_runner.py)
# ---------------------------------------------------------------------------

def get_skill(artha_dir: Path) -> HomeDeviceMonitorSkill:
    """Factory — called by skill_runner.py to instantiate the skill."""
    return HomeDeviceMonitorSkill(artha_dir)
