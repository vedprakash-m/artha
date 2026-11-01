"""tests/unit/test_logger_session_id.py — Unit tests for EV-1 logger session_id / span fields.

All fixtures are 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-1, T-EV-1-01 through T-EV-1-07
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def logger_mod():
    return _load_module("lib.logger", _LIB_DIR / "logger.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_last_jsonl(logs_dir: Path) -> dict:
    """Read the last line from the first *.log.jsonl file found in logs_dir."""
    files = list(logs_dir.glob("artha.*.log.jsonl"))
    assert files, f"No JSONL log files found in {logs_dir}"
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert lines, "Log file is empty"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# T-EV-1-01: session_id from defaults appears in payload
# ---------------------------------------------------------------------------

def test_ev1_01_session_id_in_defaults(tmp_path, logger_mod):
    """T-EV-1-01: session_id passed via defaults appears in every emitted event."""
    # Use a unique name so the singleton cache doesn't collide with other tests
    log = logger_mod.get_logger(
        "test_ev1_01",
        logs_dir=tmp_path,
        defaults={"session_id": "ses-abc-001"},
    )
    log.info("test.event")
    payload = _read_last_jsonl(tmp_path)
    assert payload.get("session_id") == "ses-abc-001"


# ---------------------------------------------------------------------------
# T-EV-1-02: no session_id when defaults not provided
# ---------------------------------------------------------------------------

def test_ev1_02_no_session_id_without_defaults(tmp_path, logger_mod):
    """T-EV-1-02: logger without session_id defaults must NOT emit session_id."""
    log = logger_mod.get_logger("test_ev1_02", logs_dir=tmp_path)
    log.info("test.event.no.session")
    payload = _read_last_jsonl(tmp_path)
    # Required standard fields must be present
    assert "ts" in payload
    assert "level" in payload
    assert "event" in payload
    assert "module" in payload
    assert "trace_id" in payload
    # session_id must NOT be present when no defaults given
    assert "session_id" not in payload


# ---------------------------------------------------------------------------
# T-EV-1-03: caller kwarg overrides defaults
# ---------------------------------------------------------------------------

def test_ev1_03_caller_overrides_defaults(tmp_path, logger_mod):
    """T-EV-1-03: session_id passed in log call overrides defaults value."""
    log = logger_mod.get_logger(
        "test_ev1_03",
        logs_dir=tmp_path,
        defaults={"session_id": "ses-default"},
    )
    log.info("test.override", session_id="ses-override-xyz")
    payload = _read_last_jsonl(tmp_path)
    assert payload.get("session_id") == "ses-override-xyz"


# ---------------------------------------------------------------------------
# T-EV-1-04: singleton cache — same object for same name+dir+defaults
# ---------------------------------------------------------------------------

def test_ev1_04_singleton_cache(tmp_path, logger_mod):
    """T-EV-1-04: Two calls to get_logger with identical args return the same object."""
    log_a = logger_mod.get_logger(
        "test_ev1_04_singleton",
        logs_dir=tmp_path,
        defaults={"session_id": "ses-single"},
    )
    log_b = logger_mod.get_logger(
        "test_ev1_04_singleton",
        logs_dir=tmp_path,
        defaults={"session_id": "ses-single"},
    )
    assert log_a is log_b


# ---------------------------------------------------------------------------
# T-EV-1-05: span_id persisted when provided
# ---------------------------------------------------------------------------

def test_ev1_05_span_id_persisted(tmp_path, logger_mod):
    """T-EV-1-05: span_id kwarg appears in the emitted JSONL payload."""
    log = logger_mod.get_logger("test_ev1_05", logs_dir=tmp_path)
    log.info("test.span", span_id="sp-001")
    payload = _read_last_jsonl(tmp_path)
    assert payload.get("span_id") == "sp-001"


# ---------------------------------------------------------------------------
# T-EV-1-06: span_id + parent_span_id both persisted
# ---------------------------------------------------------------------------

def test_ev1_06_parent_span_id_persisted(tmp_path, logger_mod):
    """T-EV-1-06: Both span_id and parent_span_id appear in JSONL payload."""
    log = logger_mod.get_logger("test_ev1_06", logs_dir=tmp_path)
    log.info("test.parent.span", span_id="sp-child", parent_span_id="sp-parent")
    payload = _read_last_jsonl(tmp_path)
    assert payload.get("span_id") == "sp-child"
    assert payload.get("parent_span_id") == "sp-parent"


# ---------------------------------------------------------------------------
# T-EV-1-07: span_id absent when not provided (None not serialised)
# ---------------------------------------------------------------------------

def test_ev1_07_span_id_absent_when_not_set(tmp_path, logger_mod):
    """T-EV-1-07: span_id must NOT appear in output if not passed by caller."""
    log = logger_mod.get_logger("test_ev1_07", logs_dir=tmp_path)
    log.info("test.no.span")
    payload = _read_last_jsonl(tmp_path)
    assert "span_id" not in payload
