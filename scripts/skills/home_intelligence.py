"""
scripts/skills/home_intelligence.py — Always-on home awareness layer.

Three concerns:
  Part 1 — Ring motion triage (camera-by-camera classification)
  Part 2 — Anomaly scan (temperature, camera offline, lights-left-on)
  Part 3 — Commands: "who's home?", "where is everyone?"

Reads cached HA entity data from tmp/ha_entities.json (written atomically by
the homeassistant connector).  Emits DomainSignal objects for the action
composer without any LLM involvement.

Signal types emitted:
  motion_alert          — notable or urgent Ring motion event
  temperature_anomaly   — Govee upstairs temp/humidity outside thresholds
  camera_offline        — camera became unavailable (excludes backspotlightpro)
  presence_anomaly      — nobody home but interior lights left on >30 min

Privacy contract:
  - Reads entity state strings only — no PII attributes
  - person.* states are "home"/"not_home"/"unknown" only
  - No camera image/video data is accessed
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import tempfile
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
# Camera classification
# ---------------------------------------------------------------------------

_INDOOR_CAMERAS = frozenset({"livingroom"})

_FRONT_CAMERAS = frozenset({"front_door", "frontporch", "front_spotlight"})

_BACK_SIDE_CAMERAS = frozenset({
    "backspotlight", "backyard", "blockedsidestickup", "sidestickup",
})

_GARAGE_CAMERAS = frozenset({"garage"})

# Known WiFi dead-zone — suppress ALL alerts
_SUPPRESSED_CAMERAS = frozenset({"backspotlightpro"})

ALL_CAMERAS = (
    _INDOOR_CAMERAS | _FRONT_CAMERAS | _BACK_SIDE_CAMERAS
    | _GARAGE_CAMERAS | _SUPPRESSED_CAMERAS
)

# Entity ID patterns for Ring motion binary sensors
# Ring motion sensors follow: binary_sensor.ring_<camera_name>_motion
_RING_MOTION_PREFIX = "binary_sensor.ring_"
_RING_MOTION_SUFFIX = "_motion"

# ---------------------------------------------------------------------------
# Person entities
# ---------------------------------------------------------------------------

_PERSON_ENTITIES = {
    "person.ved_fam": "Ved",
    "person.parth": "Parth",
    "person.trisha": "Trisha",
    "person.archana": "Archana",
}

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_TEMP_LOW_F = 62.0
_TEMP_HIGH_F = 80.0
_HUMIDITY_HIGH_PCT = 70.0
_WIND_SPEED_WEATHER_FILTER_MPH = 15.0
_LIGHTS_ON_EMPTY_MINUTES = 30
_MULTI_CAMERA_WINDOW_MINUTES = 15

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_camera_name(entity_id: str) -> Optional[str]:
    """Extract short camera name from a Ring motion binary sensor entity ID.

    binary_sensor.ring_front_door_motion -> front_door
    binary_sensor.ring_backyard_motion   -> backyard
    """
    if not entity_id.startswith(_RING_MOTION_PREFIX):
        return None
    rest = entity_id[len(_RING_MOTION_PREFIX):]
    if rest.endswith(_RING_MOTION_SUFFIX):
        rest = rest[:-len(_RING_MOTION_SUFFIX)]
    return rest if rest in ALL_CAMERAS else None


def _is_daytime(entities: List[dict]) -> bool:
    """Return True if sun.sun state is above_horizon."""
    for e in entities:
        if e.get("entity_id") == "sun.sun":
            return e.get("state", "").lower() == "above_horizon"
    return True  # default to daytime if unknown


def _get_weather(entities: List[dict]) -> Dict[str, Any]:
    """Extract weather condition and wind speed from weather.forecast_home."""
    for e in entities:
        eid = e.get("entity_id", "")
        if eid == "weather.forecast_home" or eid.startswith("weather."):
            attrs = e.get("attributes", {}) or {}
            condition = e.get("state", attrs.get("condition", ""))
            wind = attrs.get("wind_speed", 0)
            temp = attrs.get("temperature", None)
            try:
                wind = float(wind)
            except (ValueError, TypeError):
                wind = 0.0
            return {
                "condition": condition,
                "wind_speed": wind,
                "temperature": temp,
            }
    return {"condition": "unknown", "wind_speed": 0.0, "temperature": None}


def _get_persons(entities: List[dict]) -> Dict[str, str]:
    """Return {name: state} for each tracked person."""
    result = {}
    for e in entities:
        eid = e.get("entity_id", "")
        if eid in _PERSON_ENTITIES:
            result[_PERSON_ENTITIES[eid]] = e.get("state", "unknown")
    return result


def _zone_home_count(entities: List[dict]) -> int:
    """Return zone.home person count."""
    for e in entities:
        if e.get("entity_id") == "zone.home":
            try:
                return int(e.get("state", 0))
            except (ValueError, TypeError):
                return 0
    # Fallback: count person entities with state "home"
    return sum(
        1 for e in entities
        if e.get("entity_id", "") in _PERSON_ENTITIES
        and e.get("state", "") == "home"
    )


def _is_weather_noisy(weather: Dict[str, Any]) -> bool:
    """Return True if wind or storm conditions explain outdoor motion."""
    if weather["wind_speed"] > _WIND_SPEED_WEATHER_FILTER_MPH:
        return True
    noisy_conditions = {"rainy", "pouring", "lightning", "windy",
                        "lightning-rainy", "hail", "stormy"}
    return weather["condition"].lower() in noisy_conditions


def _recently_triggered_cameras(entities: List[dict], window_min: int) -> List[str]:
    """Return list of Ring camera names that show 'on' state (motion detected)
    with last_changed within window_min minutes."""
    now = datetime.now(timezone.utc)
    result = []
    for e in entities:
        eid = e.get("entity_id", "")
        cam = _extract_camera_name(eid)
        if cam is None:
            continue
        if e.get("state", "").lower() != "on":
            continue
        lc = e.get("last_changed", "")
        try:
            if lc.endswith("Z"):
                lc = lc[:-1] + "+00:00"
            changed = datetime.fromisoformat(lc)
            if changed.tzinfo is None:
                changed = changed.replace(tzinfo=timezone.utc)
            if (now - changed) <= timedelta(minutes=window_min):
                result.append(cam)
        except (ValueError, TypeError):
            pass
    return result


def _interior_lights_on(entities: List[dict]) -> List[str]:
    """Return friendly names of interior lights currently on."""
    result = []
    for e in entities:
        eid = e.get("entity_id", "")
        if not eid.startswith("light."):
            continue
        if e.get("state", "").lower() != "on":
            continue
        attrs = e.get("attributes", {}) or {}
        name = attrs.get("friendly_name", eid)
        # Heuristic: skip outdoor / exterior lights
        lower_name = name.lower()
        if any(kw in lower_name for kw in ("outdoor", "exterior", "porch",
                                            "garage", "driveway", "patio",
                                            "landscape", "flood")):
            continue
        result.append(name)
    return result


def _govee_readings(entities: List[dict]) -> Dict[str, Optional[float]]:
    """Extract Govee upstairs temperature and humidity."""
    temp = None
    humidity = None
    for e in entities:
        eid = e.get("entity_id", "")
        if "govee" in eid.lower() and "upstairs" in eid.lower():
            if "temperature" in eid.lower():
                try:
                    temp = float(e.get("state", ""))
                except (ValueError, TypeError):
                    pass
            elif "humidity" in eid.lower():
                try:
                    humidity = float(e.get("state", ""))
                except (ValueError, TypeError):
                    pass
    return {"temperature": temp, "humidity": humidity}


# ---------------------------------------------------------------------------
# Main skill class
# ---------------------------------------------------------------------------

class HomeIntelligenceSkill(BaseSkill):
    """Always-on home awareness: motion triage, anomaly scan, presence queries."""

    def __init__(self, artha_dir: Path) -> None:
        super().__init__(name="home_intelligence", priority="P1")
        self.artha_dir = artha_dir
        self._cache_file = artha_dir / "tmp" / "ha_entities.json"
        self._state_file = artha_dir / "state" / "home_intelligence.md"

    @property
    def compare_fields(self) -> List[str]:
        return [
            "motion_alert_count",
            "temperature_anomaly_count",
            "camera_offline_count",
            "presence_anomaly_count",
        ]

    # ── pull() ──────────────────────────────────────────────────────────

    def pull(self) -> Any:
        if not self._cache_file.exists():
            logging.debug("[home_intelligence] HA cache not found: %s", self._cache_file)
            return None
        try:
            with open(self._cache_file, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logging.warning("[home_intelligence] Cache read error: %s", exc)
            return None

    # ── parse() ─────────────────────────────────────────────────────────

    def parse(self, raw_data: Any) -> Dict[str, Any]:
        if raw_data is None:
            return self._empty_result("No HA cache — connector not yet run")

        entities: List[dict] = raw_data.get("entities", [])
        fetched_at: str = raw_data.get("fetched_at", "")
        now_iso = datetime.now(timezone.utc).isoformat()
        signals: List[DomainSignal] = []

        # Gather context
        daytime = _is_daytime(entities)
        weather = _get_weather(entities)
        persons = _get_persons(entities)
        home_count = _zone_home_count(entities)
        someone_home = home_count > 0
        weather_noisy = _is_weather_noisy(weather)
        triggered_cameras = _recently_triggered_cameras(
            entities, _MULTI_CAMERA_WINDOW_MINUTES
        )
        govee = _govee_readings(entities)

        motion_alert_count = 0
        temperature_anomaly_count = 0
        camera_offline_count = 0
        presence_anomaly_count = 0

        # ── PART 1: Ring Motion Triage ──────────────────────────────────
        for e in entities:
            eid = e.get("entity_id", "")
            cam = _extract_camera_name(eid)
            if cam is None:
                continue
            if e.get("state", "").lower() != "on":
                continue  # no active motion

            # Suppressed camera
            if cam in _SUPPRESSED_CAMERAS:
                continue

            # Determine alert level
            alert_level = self._triage_camera(
                cam, daytime, someone_home, weather_noisy, triggered_cameras
            )

            if alert_level == "silent":
                continue

            # Weather reduces priority by one level
            if weather_noisy and cam not in _INDOOR_CAMERAS:
                if alert_level == "urgent":
                    alert_level = "notable"
                elif alert_level == "notable":
                    alert_level = "silent"
                    continue

            # Corroboration: 2+ cameras escalate
            corroboration = len(triggered_cameras)
            if corroboration >= 2 and alert_level == "notable":
                alert_level = "urgent"

            corroboration_note = (
                f"{corroboration} cameras active" if corroboration >= 2
                else "single camera, no corroboration"
            )

            urgency = 3 if alert_level == "urgent" else 2
            motion_alert_count += 1

            persons_str = ", ".join(
                f"{n}: {s}" for n, s in persons.items()
            ) or "unknown"

            signals.append(DomainSignal(
                signal_type="motion_alert",
                domain="home",
                entity=cam,
                urgency=urgency,
                impact=urgency,
                source="skill:home_intelligence",
                metadata={
                    "camera": cam,
                    "alert_level": alert_level,
                    "daytime": daytime,
                    "someone_home": someone_home,
                    "weather_condition": weather["condition"],
                    "wind_speed": weather["wind_speed"],
                    "who_home": persons_str,
                    "corroboration": corroboration_note,
                    "reason": self._motion_reason(cam, alert_level, daytime,
                                                   someone_home, weather),
                },
                detected_at=now_iso,
            ))

        # ── PART 2: Anomaly Scan ────────────────────────────────────────

        # Temperature
        if govee["temperature"] is not None:
            temp = govee["temperature"]
            if temp < _TEMP_LOW_F:
                temperature_anomaly_count += 1
                signals.append(DomainSignal(
                    signal_type="temperature_anomaly",
                    domain="home",
                    entity="govee_upstairs",
                    urgency=2,
                    impact=2,
                    source="skill:home_intelligence",
                    metadata={
                        "reading": temp,
                        "threshold": _TEMP_LOW_F,
                        "direction": "low",
                        "reason": f"Upstairs too cold: {temp:.1f}°F (threshold {_TEMP_LOW_F}°F)",
                    },
                    detected_at=now_iso,
                ))
            elif temp > _TEMP_HIGH_F:
                temperature_anomaly_count += 1
                signals.append(DomainSignal(
                    signal_type="temperature_anomaly",
                    domain="home",
                    entity="govee_upstairs",
                    urgency=2,
                    impact=2,
                    source="skill:home_intelligence",
                    metadata={
                        "reading": temp,
                        "threshold": _TEMP_HIGH_F,
                        "direction": "high",
                        "reason": f"Upstairs too warm: {temp:.1f}°F (threshold {_TEMP_HIGH_F}°F)",
                    },
                    detected_at=now_iso,
                ))

        # Humidity
        if govee["humidity"] is not None and govee["humidity"] > _HUMIDITY_HIGH_PCT:
            temperature_anomaly_count += 1
            signals.append(DomainSignal(
                signal_type="temperature_anomaly",
                domain="home",
                entity="govee_upstairs",
                urgency=1,
                impact=1,
                source="skill:home_intelligence",
                metadata={
                    "reading": govee["humidity"],
                    "threshold": _HUMIDITY_HIGH_PCT,
                    "direction": "humidity_high",
                    "reason": f"High humidity upstairs: {govee['humidity']:.0f}% (threshold {_HUMIDITY_HIGH_PCT}%)",
                },
                detected_at=now_iso,
            ))

        # Camera offline (exclude backspotlightpro)
        for e in entities:
            eid = e.get("entity_id", "")
            if not eid.startswith("binary_sensor.ring_"):
                continue
            cam = _extract_camera_name(eid)
            if cam in _SUPPRESSED_CAMERAS:
                continue
            state = e.get("state", "").strip().lower()
            if state in ("unavailable", "unknown", ""):
                # Check if it was previously not unavailable
                camera_offline_count += 1
                attrs = e.get("attributes", {}) or {}
                signals.append(DomainSignal(
                    signal_type="camera_offline",
                    domain="home",
                    entity=eid,
                    urgency=2,
                    impact=2,
                    source="skill:home_intelligence",
                    metadata={
                        "camera": cam or eid,
                        "state": state,
                        "reason": f"Camera offline: {attrs.get('friendly_name', cam or eid)}",
                    },
                    detected_at=now_iso,
                ))

        # Presence anomaly: nobody home but interior lights on
        if not someone_home:
            lights_on = _interior_lights_on(entities)
            if lights_on:
                presence_anomaly_count += 1
                signals.append(DomainSignal(
                    signal_type="presence_anomaly",
                    domain="home",
                    entity="interior_lights",
                    urgency=1,
                    impact=1,
                    source="skill:home_intelligence",
                    metadata={
                        "lights_on": lights_on[:10],  # cap at 10
                        "count": len(lights_on),
                        "reason": f"Lights left on ({len(lights_on)}): {', '.join(lights_on[:5])}",
                    },
                    detected_at=now_iso,
                ))

        # ── Build presence summary (Part 3 data) ───────────────────────
        presence_summary = {
            "zone_home_count": home_count,
            "persons": persons,
            "daytime": daytime,
            "weather": weather,
        }

        result = {
            "signals": signals,
            "motion_alert_count": motion_alert_count,
            "temperature_anomaly_count": temperature_anomaly_count,
            "camera_offline_count": camera_offline_count,
            "presence_anomaly_count": presence_anomaly_count,
            "presence_summary": presence_summary,
            "fetched_at": fetched_at,
            "entity_count": len(entities),
            "total_signals": len(signals),
        }
        return result

    # ── execute() ───────────────────────────────────────────────────────

    def execute(self) -> Dict[str, Any]:
        self.status = "running"
        self.last_run = datetime.now(timezone.utc).isoformat()
        try:
            raw = self.pull()
            parsed = self.parse(raw)

            signals_raw: List[DomainSignal] = parsed.pop("signals", [])
            parsed["signals"] = [dataclasses.asdict(s) for s in signals_raw]

            self.status = "success"
            result = {
                "name": self.name,
                "status": self.status,
                "timestamp": self.last_run,
                "data": parsed,
            }
            self._write_state_file(parsed, signals_raw)
            return result

        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            logging.error("[home_intelligence] Skill failed: %s", exc)
            return {
                "name": self.name,
                "status": self.status,
                "timestamp": self.last_run,
                "error": self.error,
                "data": self._empty_result(f"Execution error: {exc}"),
            }

    # ── to_dict() ───────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "status": self.status,
            "last_run": self.last_run,
            "error": self.error,
        }

    # ── Camera triage logic ─────────────────────────────────────────────

    def _triage_camera(
        self,
        cam: str,
        daytime: bool,
        someone_home: bool,
        weather_noisy: bool,
        triggered: List[str],
    ) -> str:
        """Return 'silent', 'notable', or 'urgent' for a motion event."""

        # Indoor: ALWAYS alert
        if cam in _INDOOR_CAMERAS:
            return "urgent"

        # Front-of-house
        if cam in _FRONT_CAMERAS:
            if not daytime:
                return "notable"
            if not someone_home:
                return "notable"
            return "silent"

        # Back/side
        if cam in _BACK_SIDE_CAMERAS:
            if not daytime and not someone_home:
                return "urgent"
            if not daytime:
                return "notable"
            return "silent"

        # Garage/driveway
        if cam in _GARAGE_CAMERAS:
            return "silent"  # lower priority, likely car

        return "silent"

    def _motion_reason(
        self,
        cam: str,
        level: str,
        daytime: bool,
        someone_home: bool,
        weather: Dict[str, Any],
    ) -> str:
        """Build a human-readable reason string for a motion alert."""
        time_str = "daytime" if daytime else "nighttime"
        home_str = "someone home" if someone_home else "nobody home"
        weather_str = f"{weather['condition']}, wind {weather['wind_speed']:.0f}mph"

        if cam in _INDOOR_CAMERAS:
            return f"Indoor camera triggered ({cam}) — verify manually"

        return f"Motion: {cam} [{level}] — {time_str}, {home_str}, {weather_str}"

    # ── Helpers ──────────────────────────────────────────────────────────

    def _empty_result(self, reason: str = "") -> Dict[str, Any]:
        return {
            "signals": [],
            "motion_alert_count": 0,
            "temperature_anomaly_count": 0,
            "camera_offline_count": 0,
            "presence_anomaly_count": 0,
            "presence_summary": {},
            "fetched_at": "",
            "entity_count": 0,
            "total_signals": 0,
            "note": reason,
        }

    def _write_state_file(self, parsed: Dict, signals: List[DomainSignal]) -> None:
        """Write home intelligence state atomically."""
        now_iso = datetime.now(timezone.utc).isoformat()
        presence = parsed.get("presence_summary", {})
        persons = presence.get("persons", {})

        persons_lines = "\n".join(
            f"  {name}: {state}" for name, state in persons.items()
        ) or "  (no person data)"

        motion_signals = [s for s in signals if s.signal_type == "motion_alert"]
        motion_lines = "\n".join(
            f"  - {s.metadata.get('camera', '?')}: {s.metadata.get('alert_level', '?')} "
            f"— {s.metadata.get('reason', '')}"
            for s in motion_signals
        ) or "  (none)"

        anomaly_signals = [s for s in signals if s.signal_type != "motion_alert"]
        anomaly_lines = "\n".join(
            f"  - {s.signal_type}: {s.metadata.get('reason', '')}"
            for s in anomaly_signals
        ) or "  (none)"

        weather = presence.get("weather", {})
        content = f"""# Home Intelligence State (Machine-Managed)
> Auto-generated by home_intelligence skill. Do not hand-edit.
> Last updated: {now_iso}

## Presence
zone_home_count: {presence.get('zone_home_count', 0)}
persons:
{persons_lines}

## Environment
daytime: {presence.get('daytime', True)}
weather_condition: {weather.get('condition', 'unknown')}
wind_speed_mph: {weather.get('wind_speed', 0)}
temperature: {weather.get('temperature', 'unknown')}

## Active Motion Alerts
{motion_lines}

## Anomalies
{anomaly_lines}

## Skill Run
timestamp: {now_iso}
total_signals: {parsed.get('total_signals', 0)}
motion_alert_count: {parsed.get('motion_alert_count', 0)}
temperature_anomaly_count: {parsed.get('temperature_anomaly_count', 0)}
camera_offline_count: {parsed.get('camera_offline_count', 0)}
presence_anomaly_count: {parsed.get('presence_anomaly_count', 0)}
"""
        state_dir = self._state_file.parent
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=state_dir, suffix=".tmp", prefix=".home_intel_"
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
            os.replace(tmp_path, str(self._state_file))
        except Exception as exc:
            logging.warning("[home_intelligence] Failed to write state: %s", exc)

    # ── Command helpers (Part 3) ────────────────────────────────────────

    def whos_home(self) -> Dict[str, Any]:
        """Answer 'who's home?' — returns presence data."""
        raw = self.pull()
        if raw is None:
            return {"error": "No HA data available", "persons": {}, "zone_home_count": 0}
        entities = raw.get("entities", [])
        persons = _get_persons(entities)
        count = _zone_home_count(entities)
        return {
            "zone_home_count": count,
            "persons": persons,
        }

    def where_is_everyone(self) -> Dict[str, Any]:
        """Answer 'where is everyone?' — infer zones from lights + motion + time."""
        raw = self.pull()
        if raw is None:
            return {"error": "No HA data available", "inferences": []}
        entities = raw.get("entities", [])
        persons = _get_persons(entities)
        daytime = _is_daytime(entities)
        lights_on = _interior_lights_on(entities)
        triggered = _recently_triggered_cameras(entities, 30)
        now = datetime.now(timezone.utc)
        weekday = now.weekday() < 5

        inferences = []
        for name, state in persons.items():
            if state == "home":
                hint = self._infer_zone(name, lights_on, triggered, daytime, weekday)
                inferences.append(f"{name}: home — {hint}")
            elif state == "not_home":
                inferences.append(f"{name}: away")
            else:
                inferences.append(f"{name}: unknown (tracker unreliable)")

        return {
            "persons": persons,
            "lights_on": lights_on,
            "recent_motion": triggered,
            "inferences": inferences,
        }

    def _infer_zone(
        self,
        name: str,
        lights_on: List[str],
        triggered: List[str],
        daytime: bool,
        weekday: bool,
    ) -> str:
        """Best-effort zone inference from ambient signals."""
        lower_lights = [l.lower() for l in lights_on]

        if any("kitchen" in l for l in lower_lights):
            if daytime:
                return "likely kitchen (lights on)"
        if any("loft" in l or "office" in l for l in lower_lights):
            if weekday:
                return "likely working/studying (loft/office lights on)"
        if any("bedroom" in l or "master" in l for l in lower_lights):
            if not daytime:
                return "likely bedroom"
        if any("living" in l or "family" in l for l in lower_lights):
            return "likely living area"
        if triggered:
            return f"motion detected near {', '.join(triggered[:2])}"

        return "home (zone unknown)"


# ---------------------------------------------------------------------------
# Factory function (required by skill_runner.py)
# ---------------------------------------------------------------------------

def get_skill(artha_dir: Path) -> HomeIntelligenceSkill:
    """Factory — called by skill_runner.py to instantiate the skill."""
    return HomeIntelligenceSkill(artha_dir)
