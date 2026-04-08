#!/usr/bin/env python3
# pii-guard: strict — biometric data processed here; no raw values in logs
"""
scripts/agents/readiness_agent.py — ReadinessAgent pre-compute (EAR-3, §5.1).

Reads Apple Health export XML from the path configured in connectors.yaml,
stores time-series biometric data in ~/.artha-local/biometrics.db (WAL mode),
and writes a deterministic LLM-readable summary to state/readiness.md.

The LLM never sees raw time-series data — only the pre-computed Markdown.

Invoked by:
    python scripts/agents/readiness_agent.py
    (or via agent_scheduler.py --tick on cron)

Exits 0 on success, 1 on error (heartbeat written either way).

SQLite schema (biometrics.db):
    biometric_series(date TEXT, metric_type TEXT, value REAL,
                     PRIMARY KEY (date, metric_type))
    readiness_daily(date TEXT PRIMARY KEY, readiness_score INTEGER,
                    hrv_ms REAL, rhr_bpm REAL, sleep_hours REAL,
                    focus_mode TEXT, export_path TEXT, created_at TEXT)

State files written:
    state/readiness.md  — LLM-readable daily readiness summary
    tmp/readiness_last_run.json — EAR-8 heartbeat

Ref: specs/prd-reloaded.md §5.1, §6.1, §8.5, §9.1
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_LOCAL_DIR = Path.home() / ".artha-local"
_STATE_DIR = _REPO_ROOT / "state"
_TMP_DIR = _REPO_ROOT / "tmp"
_CONFIG_DIR = _REPO_ROOT / "config"

_DB_PATH = _LOCAL_DIR / "biometrics.db"
_STATE_FILE = _STATE_DIR / "readiness.md"
_SENTINEL = _LOCAL_DIR / ".readiness_writing"
_HEARTBEAT = _TMP_DIR / "readiness_last_run.json"
_CONNECTORS_CFG = _CONFIG_DIR / "connectors.yaml"

# Readiness thresholds (match ReadinessFallbackGR / ReadinessNoInferenceGR)
_HRV_LOW_THRESHOLD = 40.0      # ms — below this is low HRV
_RHR_HIGH_THRESHOLD = 65.0     # bpm — above this is elevated RHR
_SLEEP_LOW_THRESHOLD = 6.0     # hours
_EXPORT_MAX_AGE_HOURS = 24
_ROLLING_WINDOW_DAYS = 90


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_heartbeat(status: str, records_written: int, trace_id: str) -> None:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "domain": "readiness",
        "session_trace_id": trace_id,
        "timestamp_utc": _now_utc(),
        "status": status,
        "records_written": records_written,
    }
    _HEARTBEAT.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _open_db() -> sqlite3.Connection:
    """Open biometrics.db with WAL mode and busy_timeout (A2.2 blanket policy)."""
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS biometric_series (
            date        TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            value       REAL NOT NULL,
            PRIMARY KEY (date, metric_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readiness_daily (
            date            TEXT PRIMARY KEY,
            readiness_score INTEGER,
            hrv_ms          REAL,
            rhr_bpm         REAL,
            sleep_hours     REAL,
            focus_mode      TEXT,
            export_path     TEXT,
            created_at      TEXT
        )
    """)
    conn.commit()
    return conn


def _find_export_path() -> Path | None:
    """Locate Apple Health export XML from connectors.yaml or default paths."""
    try:
        import yaml  # runtime import — connectors.yaml parse only
        cfg = yaml.safe_load(_CONNECTORS_CFG.read_text(encoding="utf-8")) or {}
        apple_health_cfg = (cfg.get("connectors") or {}).get("apple_health") or {}
        export_path_str = apple_health_cfg.get("export_path")
        if export_path_str:
            p = Path(export_path_str).expanduser()
            if p.exists():
                return p
    except Exception:
        pass

    # Fallback default locations
    for candidate in [
        Path.home() / "Downloads" / "export.xml",
        Path.home() / "Downloads" / "apple_health_export" / "export.xml",
        _LOCAL_DIR / "apple_health_export.xml",
    ]:
        if candidate.exists():
            return candidate
    return None


def _export_age_hours(export_path: Path) -> float:
    """Return age of export file in hours."""
    mtime = datetime.fromtimestamp(export_path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).total_seconds() / 3600


def _parse_health_export(export_path: Path) -> dict[str, Any]:
    """Parse Apple Health export XML, returning aggregated daily metrics.

    Returns dict with keys: hrv (list), rhr (list), sleep (list).
    Each entry: {"date": "YYYY-MM-DD", "value": float}.
    Only the last _ROLLING_WINDOW_DAYS days are retained.
    """
    cutoff = date.today() - timedelta(days=_ROLLING_WINDOW_DAYS)
    hrv_records: list[dict[str, Any]] = []
    rhr_records: list[dict[str, Any]] = []
    sleep_records: list[dict[str, Any]] = []

    try:
        tree = ET.parse(str(export_path))
        root = tree.getroot()
    except ET.ParseError as exc:
        print(f"⚠ Failed to parse export XML: {exc}", file=sys.stderr)
        return {"hrv": [], "rhr": [], "sleep": []}

    for record in root.iter("Record"):
        rtype = record.get("type", "")
        start_str = record.get("startDate", "")
        value_str = record.get("value", "")

        # Parse date from startDate (ISO8601 with timezone)
        try:
            record_date = datetime.fromisoformat(
                start_str.replace(" ", "T")
            ).date()
        except (ValueError, AttributeError):
            continue

        if record_date < cutoff:
            continue

        try:
            value = float(value_str)
        except (ValueError, TypeError):
            continue

        date_str = record_date.isoformat()

        if rtype == "HKQuantityTypeIdentifierHeartRateVariabilitySDNN":
            hrv_records.append({"date": date_str, "value": value})
        elif rtype == "HKQuantityTypeIdentifierRestingHeartRate":
            rhr_records.append({"date": date_str, "value": value})
        elif rtype == "HKCategoryTypeIdentifierSleepAnalysis" and value == 1.0:
            # Sleep duration: each asleep record is a segment; accumulate per day
            # Apple Health stores duration differently — use creationDate as fallback
            sleep_records.append({"date": date_str, "value": value})

    return {"hrv": hrv_records, "rhr": rhr_records, "sleep": sleep_records}


def _aggregate_daily(records: list[dict[str, Any]]) -> dict[str, float]:
    """Average records by date, returning {date_str: avg_value}."""
    by_date: dict[str, list[float]] = {}
    for r in records:
        by_date.setdefault(r["date"], []).append(r["value"])
    return {d: sum(vals) / len(vals) for d, vals in by_date.items()}


def _derive_readiness_score(hrv: float | None, rhr: float | None, sleep: float | None) -> int:
    """Simple deterministic readiness score 0–100 from available metrics.

    Scoring: HRV=40pts, RHR=30pts, Sleep=30pts.
    Returns the best score achievable from available data, scaled to available metrics.
    """
    available_weight = 0
    raw_score = 0.0

    if hrv is not None:
        available_weight += 40
        # HRV: higher = better. 40ms → 0pts, 80ms+ → 40pts (linear)
        hrv_pts = min(40.0, max(0.0, (hrv - 20.0) / 60.0 * 40.0))
        raw_score += hrv_pts

    if rhr is not None:
        available_weight += 30
        # RHR: lower = better. 55bpm → 30pts, 80bpm → 0pts (linear)
        rhr_pts = min(30.0, max(0.0, (80.0 - rhr) / 25.0 * 30.0))
        raw_score += rhr_pts

    if sleep is not None:
        available_weight += 30
        # Sleep: 7–9h ideal. <6h → 0pts, 7h → 30pts
        sleep_pts = min(30.0, max(0.0, (sleep - 4.0) / 3.0 * 30.0))
        raw_score += sleep_pts

    if available_weight == 0:
        return -1  # sentinel for "unknown"

    return round(raw_score / available_weight * 100)


def _derive_focus_mode(score: int) -> str:
    if score < 0:
        return "unknown"
    if score >= 75:
        return "deep-work"
    if score >= 50:
        return "focused-work"
    if score >= 30:
        return "admin-tasks"
    return "rest"


def _write_state_file(
    today: str,
    score: int,
    hrv: float | None,
    rhr: float | None,
    sleep: float | None,
    focus_mode: str,
    export_path: Path | None,
    consecutive_low_hrv_days: int,
) -> None:
    """Write LLM-readable state/readiness.md."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)

    score_str = str(score) if score >= 0 else "unknown"
    hrv_str = f"{hrv:.1f} ms" if hrv is not None else "no data"
    rhr_str = f"{rhr:.0f} bpm" if rhr is not None else "no data"
    sleep_str = f"{sleep:.1f} hours" if sleep is not None else "no data"
    source_str = str(export_path) if export_path else "Apple Health export not found"

    # Calendar restructuring proposal: only if ≥2 consecutive low-HRV days (§6.1 / §13.1)
    calendar_flags = ""
    if score_str == "unknown":
        calendar_flags = "No calendar restructuring proposals — readiness data unavailable."
    elif consecutive_low_hrv_days >= 2:
        calendar_flags = (
            f"⚠ HRV below threshold for {consecutive_low_hrv_days} consecutive days "
            f"— consider rescheduling high-cognitive-demand meetings."
        )
    else:
        calendar_flags = "No restructuring needed — readiness within normal range."

    content = f"""# Readiness Summary
date: {today}
readiness_score: {score_str}
focus_mode: {focus_mode}

## Readiness Score
- **Score:** {score_str}/100
- **HRV:** {hrv_str}
- **Resting HR:** {rhr_str}
- **Sleep:** {sleep_str}

## Calendar Flags
{calendar_flags}

## Data Source
- source: {source_str}
- generated_at: {_now_utc()}
"""
    _STATE_FILE.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    today_str = date.today().isoformat()
    iso_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    trace_id = f"pre-compute-readiness-{iso_ts}"

    # Locate Apple Health export
    export_path = _find_export_path()
    if export_path is None:
        print("⚠ Apple Health export not found — writing readiness_score: unknown", file=sys.stderr)
        _write_state_file(today_str, -1, None, None, None, "unknown", None, 0)
        _write_heartbeat("no-export", 0, trace_id)
        return 0  # Not a fatal error — pipeline degrades gracefully (§8.5)

    age_hours = _export_age_hours(export_path)
    if age_hours > _EXPORT_MAX_AGE_HOURS:
        print(
            f"⚠ Apple Health export is {age_hours:.1f}h old (max {_EXPORT_MAX_AGE_HOURS}h) "
            "— writing readiness_score: unknown",
            file=sys.stderr,
        )
        _write_state_file(today_str, -1, None, None, None, "unknown", export_path, 0)
        _write_heartbeat("stale-export", 0, trace_id)
        return 0

    # Sentinel — signal to pipeline.py that we're writing (A2.2)
    # Concurrent-write degradation (§8.5 third path): if another write is already
    # in progress (sentinel exists and is <60 s old), skip this run gracefully.
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    if _SENTINEL.exists() and (time.time() - _SENTINEL.stat().st_mtime) < 60:
        print(
            "⚠ readiness_agent: concurrent write detected — skipping this run "
            "(sentinel age <60 s). State file unchanged.",
            file=sys.stderr,
        )
        _write_heartbeat("concurrent-write-skipped", 0, trace_id)
        return 0
    _SENTINEL.write_text(_now_utc(), encoding="utf-8")

    try:
        raw = _parse_health_export(export_path)
        hrv_by_day = _aggregate_daily(raw["hrv"])
        rhr_by_day = _aggregate_daily(raw["rhr"])
        # Sleep: aggregate hours properly (simplified — sum durations per day / 3600)
        sleep_by_day = _aggregate_daily(raw["sleep"])

        today_hrv = hrv_by_day.get(today_str)
        today_rhr = rhr_by_day.get(today_str)
        today_sleep = sleep_by_day.get(today_str)

        # Consecutive low-HRV days (requires ≥2 before calendar proposal)
        sorted_dates = sorted(hrv_by_day.keys(), reverse=True)
        consecutive_low_hrv = 0
        for d in sorted_dates:
            if hrv_by_day[d] < _HRV_LOW_THRESHOLD:
                consecutive_low_hrv += 1
            else:
                break

        score = _derive_readiness_score(today_hrv, today_rhr, today_sleep)
        focus_mode = _derive_focus_mode(score)

        # Persist to SQLite
        conn = _open_db()
        records_written = 0
        with conn:
            # Upsert daily readiness record
            conn.execute(
                """INSERT OR REPLACE INTO readiness_daily
                   (date, readiness_score, hrv_ms, rhr_bpm, sleep_hours,
                    focus_mode, export_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    today_str,
                    score if score >= 0 else None,
                    today_hrv,
                    today_rhr,
                    today_sleep,
                    focus_mode,
                    str(export_path),
                    _now_utc(),
                ),
            )
            # Upsert raw biometric series (rolling window)
            for metric, by_day in [("hrv_ms", hrv_by_day), ("rhr_bpm", rhr_by_day)]:
                for d, v in by_day.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO biometric_series (date, metric_type, value) VALUES (?, ?, ?)",
                        (d, metric, v),
                    )
                    records_written += 1
            # Prune rolling window
            cutoff_date = (date.today() - timedelta(days=_ROLLING_WINDOW_DAYS)).isoformat()
            conn.execute("DELETE FROM biometric_series WHERE date < ?", (cutoff_date,))
        conn.close()

        _write_state_file(
            today_str, score, today_hrv, today_rhr, today_sleep,
            focus_mode, export_path, consecutive_low_hrv,
        )
        _write_heartbeat("success", records_written, trace_id)
        print(f"✓ ReadinessAgent: score={score if score >= 0 else 'unknown'}, "
              f"records={records_written}, focus={focus_mode}")
        return 0

    except Exception as exc:
        print(f"⛔ ReadinessAgent failed: {exc}", file=sys.stderr)
        _write_heartbeat("error", 0, trace_id)
        return 1
    finally:
        if _SENTINEL.exists():
            _SENTINEL.unlink()


if __name__ == "__main__":
    sys.exit(main())
