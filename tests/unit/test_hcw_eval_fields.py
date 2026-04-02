"""tests/unit/test_hcw_eval_fields.py — Unit tests for EV-3 health_check_writer fields.

Tests for _append_catch_up_run() EV-3 CLI flags, engagement_rate formula,
_acquire_lock() atomic create, and _compute_config_hash() determinism.

All fixtures are 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-3, T-EV-3-01 through T-EV-3-14
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def hcw():
    return _load_module("health_check_writer", _SCRIPTS_DIR / "health_check_writer.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_runs(runs_file: Path) -> list[dict]:
    import yaml
    raw = yaml.safe_load(runs_file.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else []


# ---------------------------------------------------------------------------
# T-EV-3-01: compliance_score persisted
# ---------------------------------------------------------------------------

def test_ev3_01_compliance_score(tmp_path, hcw):
    """T-EV-3-01: compliance_score arg is persisted to YAML entry."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T10:00:00Z",
            compliance_score=0.92,
            items_surfaced=10,
        )
    entry = _read_runs(runs_file)[-1]
    assert "compliance_score" in entry
    assert abs(entry["compliance_score"] - 0.92) < 1e-4


# ---------------------------------------------------------------------------
# T-EV-3-02: quality_score persisted
# ---------------------------------------------------------------------------

def test_ev3_02_quality_score(tmp_path, hcw):
    """T-EV-3-02: quality_score arg is persisted to YAML entry."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T11:00:00Z",
            quality_score=78.5,
            items_surfaced=10,
        )
    entry = _read_runs(runs_file)[-1]
    assert "quality_score" in entry
    assert abs(entry["quality_score"] - 78.5) < 1e-3


# ---------------------------------------------------------------------------
# T-EV-3-03: session_id persisted
# ---------------------------------------------------------------------------

def test_ev3_03_session_id(tmp_path, hcw):
    """T-EV-3-03: session_id arg is persisted to YAML entry."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T12:00:00Z",
            session_id="ses-test-123",
        )
    entry = _read_runs(runs_file)[-1]
    assert entry.get("session_id") == "ses-test-123"


# ---------------------------------------------------------------------------
# T-EV-3-04: model persisted
# ---------------------------------------------------------------------------

def test_ev3_04_model_persisted(tmp_path, hcw):
    """T-EV-3-04: model arg is persisted to YAML entry."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T13:00:00Z",
            model="claude-sonnet-4.5",
        )
    entry = _read_runs(runs_file)[-1]
    assert entry.get("model") == "claude-sonnet-4.5"


# ---------------------------------------------------------------------------
# T-EV-3-05: calibration_skipped persisted
# ---------------------------------------------------------------------------

def test_ev3_05_calibration_skipped(tmp_path, hcw):
    """T-EV-3-05: calibration_skipped=True is persisted as bool."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T14:00:00Z",
            calibration_skipped=True,
        )
    entry = _read_runs(runs_file)[-1]
    assert entry.get("calibration_skipped") is True


# ---------------------------------------------------------------------------
# T-EV-3-06: coaching_nudge persisted
# ---------------------------------------------------------------------------

def test_ev3_06_coaching_nudge(tmp_path, hcw):
    """T-EV-3-06: coaching_nudge='dismissed' is persisted."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T15:00:00Z",
            coaching_nudge="dismissed",
        )
    entry = _read_runs(runs_file)[-1]
    assert entry.get("coaching_nudge") == "dismissed"


# ---------------------------------------------------------------------------
# T-EV-3-07: entry without EV-3 flags has only base required keys
# ---------------------------------------------------------------------------

def test_ev3_07_minimal_entry_keys(tmp_path, hcw):
    """T-EV-3-07: entry without EV-3 flags has timestamp + schema_version but no extra keys."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run("2026-03-01T16:00:00Z")
    entry = _read_runs(runs_file)[-1]
    assert "timestamp" in entry
    assert "schema_version" in entry
    for ev3_field in ("compliance_score", "quality_score", "session_id", "model"):
        assert ev3_field not in entry, f"Field {ev3_field!r} should not be in minimal entry"


# ---------------------------------------------------------------------------
# T-EV-3-08: existing entry preserved when new entry added
# ---------------------------------------------------------------------------

def test_ev3_08_existing_entry_preserved(tmp_path, hcw):
    """T-EV-3-08: Pre-existing YAML entries are retained when a new entry is appended."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    # Write two entries
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run("2026-03-01T08:00:00Z", model="claude-opus-1")
        hcw._append_catch_up_run("2026-03-02T08:00:00Z", model="claude-sonnet-2")
    runs = _read_runs(runs_file)
    assert len(runs) == 2
    assert runs[0].get("model") == "claude-opus-1"
    assert runs[1].get("model") == "claude-sonnet-2"


# ---------------------------------------------------------------------------
# T-EV-3-09: engagement_rate formula correctness
# ---------------------------------------------------------------------------

def test_ev3_09_engagement_rate_formula(tmp_path, hcw):
    """T-EV-3-09: engagement_rate = (user_ois + correction_count) / items_surfaced."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T09:00:00Z",
            user_ois=3,
            correction_count=2,
            items_surfaced=10,
        )
    entry = _read_runs(runs_file)[-1]
    # (3 + 2) / 10 = 0.5
    assert abs(entry.get("engagement_rate", -1) - 0.5) < 1e-4


# ---------------------------------------------------------------------------
# T-EV-3-10: items_surfaced=None → engagement_rate is None
# ---------------------------------------------------------------------------

def test_ev3_10_engagement_rate_none_when_no_surfaced(tmp_path, hcw):
    """T-EV-3-10: When items_surfaced is None, engagement_rate must be None (YAML null)."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T10:30:00Z",
            user_ois=3,
            correction_count=2,
            items_surfaced=None,
        )
    entry = _read_runs(runs_file)[-1]
    assert entry.get("engagement_rate") is None


# ---------------------------------------------------------------------------
# T-EV-3-11: lock uses O_CREAT | O_EXCL
# ---------------------------------------------------------------------------

def test_ev3_11_lock_uses_o_creat_o_excl(tmp_path, hcw):
    """T-EV-3-11: _acquire_lock() calls os.open with O_CREAT|O_EXCL flag."""
    lock_file = tmp_path / ".artha-lock"
    open_calls: list[tuple] = []

    original_open = os.open

    def recording_open(path: str, flags: int, *args, **kwargs) -> int:
        open_calls.append((path, flags))
        if str(path) == str(lock_file):
            return original_open(path, flags, *args, **kwargs)
        return original_open(path, flags, *args, **kwargs)

    with patch.object(hcw, "LOCK_FILE", lock_file), \
         patch("os.open", side_effect=recording_open):
        result = hcw._acquire_lock(timeout_secs=1.0, stale_secs=60.0)

    assert result is True
    lock_file_calls = [
        (path, flags) for path, flags in open_calls
        if str(path) == str(lock_file)
    ]
    assert lock_file_calls, "No os.open call targeting lock file found"
    _, flags = lock_file_calls[0]
    assert flags & os.O_CREAT, "O_CREAT must be set"
    assert flags & os.O_EXCL, "O_EXCL must be set"

    # Cleanup
    try:
        lock_file.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# T-EV-3-12: config_hash persisted
# ---------------------------------------------------------------------------

def test_ev3_12_config_hash_persisted(tmp_path, hcw):
    """T-EV-3-12: config_hash arg is persisted in catch_up_runs entry."""
    runs_file = tmp_path / "catch_up_runs.yaml"
    with patch.object(hcw, "CATCH_UP_RUNS_FILE", runs_file), \
         patch.object(hcw, "STATE_DIR", tmp_path):
        hcw._append_catch_up_run(
            "2026-03-01T17:00:00Z",
            config_hash="abc123def456",
        )
    entry = _read_runs(runs_file)[-1]
    assert entry.get("config_hash") == "abc123def456"


# ---------------------------------------------------------------------------
# T-EV-3-13: _compute_config_hash returns 12-char hex
# ---------------------------------------------------------------------------

def test_ev3_13_config_hash_is_12_char_hex(hcw):
    """T-EV-3-13: _compute_config_hash() return value is exactly 12 hex characters."""
    result = hcw._compute_config_hash()
    assert isinstance(result, str), f"Expected str, got {type(result)}"
    assert len(result) == 12, f"Expected 12 chars, got {len(result)}: {result!r}"
    # Must be valid hex
    int(result, 16)  # raises ValueError if not hex


# ---------------------------------------------------------------------------
# T-EV-3-14: stale lock removed and lock acquired
# ---------------------------------------------------------------------------

def test_ev3_14_stale_lock_removed_and_acquired(tmp_path, hcw):
    """T-EV-3-14: Lock file older than stale_secs is removed and lock is acquired."""
    lock_file = tmp_path / ".artha-lock"
    lock_file.touch()

    # Make the lock appear old by backdating its mtime
    old_time = time.time() - 120  # 120 seconds ago → stale (threshold 60s)
    os.utime(lock_file, (old_time, old_time))

    with patch.object(hcw, "LOCK_FILE", lock_file):
        result = hcw._acquire_lock(timeout_secs=2.0, stale_secs=60.0)

    assert result is True, "Should have acquired lock after removing stale one"

    # Cleanup
    try:
        lock_file.unlink()
    except OSError:
        pass
