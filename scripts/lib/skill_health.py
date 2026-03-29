# pii-guard: ignore-file — shared infrastructure; no personal data
"""
scripts/lib/skill_health.py — Shared skill health tracking library.

Pure-function library importable by both skill_runner.py and the MCP handler.
Provides: zero-value detection, stable-value detection, health counter updates,
health classification, and atomic JSON write.

Concurrent write safety: atomic_write_json() uses fcntl.flock on POSIX
(macOS/Linux). On Windows, falls back to direct write with retry.

Ref: specs/skills-reloaded.md §3.3–3.9
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Timestamp keys stripped during stable-value comparison to suppress jitter.
_TIMESTAMP_KEYS: frozenset[str] = frozenset({
    "last_updated", "timestamp", "fetched_at", "as_of", "checked_at",
})

# R7 cadence reduction mapping (consecutive_zero >= 10 → reduce to next cadence).
# "weekly" is intentionally absent — weekly is the cadence floor.
# A weekly skill with 10+ consecutive zeros: reduced_cadence = None,
# the R7 block doesn't fire, and should_run() falls through to return True.
CADENCE_REDUCTION: dict[str, str] = {
    "every_run": "daily",
    "daily": "weekly",
}


# ---------------------------------------------------------------------------
# Zero-value detection
# ---------------------------------------------------------------------------

def is_zero_value(
    skill_name: str,
    result: dict[str, Any],
    prev_result: dict[str, Any] | None,
    skills_config: dict[str, Any],
) -> bool:
    """Return True if the skill produced no actionable data.

    Checks skill-specific zero_value_fields override first, then falls back
    to generic detection (empty data dict or error-masked data).

    Args:
        skill_name:    Canonical skill name (e.g. "uscis_status").
        result:        Current skill result dict (with "data" key).
        prev_result:   Previous skill result dict (unused but kept for API parity).
        skills_config: Full config dict (config.get("skills", {}) will be used).
    """
    data = result.get("data")
    if data is None or data == {}:
        return True
    if isinstance(data, dict):
        if data.get("error"):
            return True
        if data.get("status") in ("insufficient_data", "error"):
            return True

    # Per-skill override: only check specified fields for value existence.
    skill_cfg = skills_config.get("skills", {}).get(skill_name, {})
    zero_fields = skill_cfg.get("zero_value_fields")
    if zero_fields and isinstance(data, dict):
        return all(not data.get(f) for f in zero_fields)

    return False


# ---------------------------------------------------------------------------
# Stable-value detection
# ---------------------------------------------------------------------------

def _normalize(data: Any) -> str:
    """Canonical JSON string for comparison, stripping timestamp jitter fields."""
    if not isinstance(data, dict):
        return json.dumps(data, sort_keys=True, default=str)
    cleaned = {k: v for k, v in data.items() if k not in _TIMESTAMP_KEYS}
    return json.dumps(cleaned, sort_keys=True, default=str)


def is_stable_value(
    result: dict[str, Any],
    prev_result: dict[str, Any] | None,
) -> bool:
    """Return True if data is semantically identical to the previous run.

    Strips timestamp jitter fields before comparison so a skill that only
    updates `last_checked` doesn't register as "changed".
    """
    if prev_result is None:
        return False
    return _normalize(result.get("data")) == _normalize(prev_result.get("data"))


# ---------------------------------------------------------------------------
# Health counter updates
# ---------------------------------------------------------------------------

def update_health_counters(
    cache_entry: dict[str, Any],
    is_zero: bool,
    is_stable: bool,
    last_wall_clock_ms: int | None = None,
) -> dict[str, Any]:
    """Return a deep-copied cache_entry with updated 'health' sub-dict.

    Reads the existing 'health' sub-dict from cache_entry (if present) and
    increments counters accordingly. Does NOT modify the original dict.

    The 'health' sub-dict schema:
        total_runs:           int — total times this skill has been executed
        success_count:        int — successful executions
        failure_count:        int — failed executions
        zero_value_count:     int — runs that returned no actionable data
        consecutive_zero:     int — current streak of zero-value runs
        consecutive_stable:   int — current streak of stable (unchanged) runs
        last_success:         str | null — ISO timestamp of last success
        last_failure:         str | null — ISO timestamp of last failure
        last_nonzero_value:   str | null — ISO timestamp of last non-zero-value run
        last_wall_clock_ms:   int | null — wall-clock time of most recent run (ms)
        r7_skips:             int — count of runs skipped due to R7 cadence reduction
        last_r7_prompt:       str | null — ISO timestamp of last R7 disable-prompt
        maturity:             str — warming_up | measuring | trusted
        classification:       str — warming_up | healthy | degraded | stable | broken
    """
    entry = copy.deepcopy(cache_entry)
    health: dict[str, Any] = entry.get("health") or {}

    # Ensure all fields are present (defensive init for cold-start entries)
    health.setdefault("total_runs", 0)
    health.setdefault("success_count", 0)
    health.setdefault("failure_count", 0)
    health.setdefault("zero_value_count", 0)
    health.setdefault("consecutive_zero", 0)
    health.setdefault("consecutive_stable", 0)
    health.setdefault("last_success", None)
    health.setdefault("last_failure", None)
    health.setdefault("last_nonzero_value", None)
    health.setdefault("last_wall_clock_ms", None)
    health.setdefault("r7_skips", 0)
    health.setdefault("last_r7_prompt", None)

    # Increment total run counter
    health["total_runs"] += 1
    total_runs = health["total_runs"]

    # Determine success/failure from current result
    current = entry.get("current", {})
    if isinstance(current, dict):
        status = current.get("status", "success")
    else:
        status = "success"

    is_failure = status in ("failed", "error")

    if is_failure:
        health["failure_count"] += 1
        health["last_failure"] = _now_iso()
        # Failures don't reset or advance zero/stable streaks
    else:
        health["success_count"] += 1
        health["last_success"] = _now_iso()

        if is_zero:
            health["zero_value_count"] += 1
            health["consecutive_zero"] += 1
            health["consecutive_stable"] = 0
        else:
            # Non-zero: reset zero streak, update last_nonzero_value
            health["consecutive_zero"] = 0
            health["last_nonzero_value"] = _now_iso()
            if is_stable:
                health["consecutive_stable"] += 1
            else:
                health["consecutive_stable"] = 0

    # Timing
    if last_wall_clock_ms is not None:
        health["last_wall_clock_ms"] = last_wall_clock_ms

    # Maturity and classification
    health["maturity"] = _maturity(total_runs)
    health["classification"] = classify_health(health)

    entry["health"] = health
    return entry


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _maturity(total_runs: int) -> str:
    """Return maturity tier based on total run count."""
    if total_runs < 5:
        return "warming_up"
    if total_runs < 15:
        return "measuring"
    return "trusted"


# ---------------------------------------------------------------------------
# Health classification
# ---------------------------------------------------------------------------

def classify_health(health: dict[str, Any]) -> str:
    """Return health classification string.

    Classification rules:
        warming_up: total_runs < 5 (insufficient sample — no rules fire)
        broken:     success_rate < 50% over all recorded runs
        degraded:   success_rate >= 80% BUT consecutive_zero >= 10
        stable:     success_rate >= 80% AND consecutive_stable >= 10 (no zeros)
        healthy:    success_rate >= 80% AND had nonzero value recently
    """
    total_runs = health.get("total_runs", 0)
    if total_runs < 5:
        return "warming_up"

    success_count = health.get("success_count", 0)
    failure_count = health.get("failure_count", 0)
    consecutive_zero = health.get("consecutive_zero", 0)
    consecutive_stable = health.get("consecutive_stable", 0)

    total = success_count + failure_count
    success_rate = success_count / total if total > 0 else 1.0

    if success_rate < 0.50:
        return "broken"
    if success_rate >= 0.80 and consecutive_zero >= 10:
        return "degraded"
    if success_rate >= 0.80 and consecutive_stable >= 10:
        return "stable"
    return "healthy"


# ---------------------------------------------------------------------------
# Atomic JSON write (shared write primitive)
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON to path via fcntl.flock + tempfile + os.replace.

    POSIX (macOS/Linux): uses fcntl.flock for concurrent write safety.
    Windows: falls back to tempfile + os.replace with retry on PermissionError.
    Thread-safe; crash-safe (no partial writes).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32":
        _posix_atomic_write(path, data)
    else:
        _win_atomic_write(path, data)


def _posix_atomic_write(path: Path, data: Any) -> None:
    """POSIX atomic write using fcntl.flock + tempfile + os.replace."""
    import fcntl
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            _write_via_tempfile(path, data)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _win_atomic_write(path: Path, data: Any, max_retries: int = 3) -> None:
    """Windows atomic write: tempfile + os.replace with retry on PermissionError."""
    for attempt in range(max_retries):
        try:
            _write_via_tempfile(path, data)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
            else:
                raise


def _write_via_tempfile(path: Path, data: Any) -> None:
    """Write JSON via tempfile + os.replace (atomic on POSIX)."""
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
