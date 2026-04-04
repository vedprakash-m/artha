"""
tests/unit/test_logger_tracing.py — Unit tests for AFW-11 session tracing.

Coverage:
  - begin_session_trace returns auto-generated 16-char hex ID
  - begin_session_trace accepts custom trace ID
  - end_session_trace clears the ContextVar
  - session_trace_id injected into log payload when active
  - session_trace_id absent from payload when not set
  - per-event trace_id still present (unrelated to session ID)
  - sensitive=True event suppressed when trace_include_sensitive_data=False
  - sensitive=True event emitted when trace_include_sensitive_data=True
  - sensitive=False event always emitted regardless of config

Run from workspace root: pytest tests/unit/test_logger_tracing.py

Ref: specs/agent-fw.md §4.1 (AFW-11)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.logger import begin_session_trace, end_session_trace, get_logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_log_file(log_dir: Path) -> Path | None:
    """Find the JSONL log file written by _JsonlHandler in log_dir."""
    files = list(log_dir.glob("artha.*.log.jsonl"))
    return files[0] if files else None


def _read_last_log_line(log_dir: Path, logger_name: str) -> dict:
    """Read the most recently written log line for a given logger."""
    log_file = _find_log_file(log_dir)
    assert log_file is not None and log_file.exists(), (
        f"Log file not created in {log_dir} (logger={logger_name!r})"
    )
    lines = [l.strip() for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Filter by module name to isolate this logger's lines
    matching = [
        json.loads(l) for l in lines
        if json.loads(l).get("module") == logger_name
    ]
    assert matching, f"No log lines found for module={logger_name!r} in {log_file}"
    return matching[-1]


def _count_log_lines(log_dir: Path, logger_name: str) -> int:
    log_file = _find_log_file(log_dir)
    if log_file is None or not log_file.exists():
        return 0
    return sum(
        1 for l in log_file.read_text(encoding="utf-8").splitlines()
        if l.strip() and json.loads(l).get("module") == logger_name
    )


# ---------------------------------------------------------------------------
# T-LT1: begin_session_trace
# ---------------------------------------------------------------------------

class TestBeginSessionTrace:
    def setup_method(self):
        end_session_trace()  # ensure clean state before each test

    def teardown_method(self):
        end_session_trace()  # clean up after each test

    def test_returns_16_char_hex_id(self):
        sid = begin_session_trace()
        assert isinstance(sid, str)
        assert len(sid) == 16
        assert all(c in "0123456789abcdef" for c in sid)

    def test_returns_custom_id(self):
        sid = begin_session_trace("my_custom_id")
        assert sid == "my_custom_id"

    def test_returns_the_set_id(self):
        sid = begin_session_trace("abc123")
        assert sid == "abc123"

    def test_none_triggers_auto_generation(self):
        sid = begin_session_trace(None)
        assert len(sid) == 16


# ---------------------------------------------------------------------------
# T-LT2: end_session_trace
# ---------------------------------------------------------------------------

class TestEndSessionTrace:
    def test_clears_active_trace(self):
        begin_session_trace("will_be_cleared")
        end_session_trace()
        # Verify no session_trace_id appears in subsequent logs
        # (tested more thoroughly via payload reading below)

    def test_double_end_is_safe(self):
        end_session_trace()
        end_session_trace()  # must not raise


# ---------------------------------------------------------------------------
# T-LT3: session_trace_id in log payload
# ---------------------------------------------------------------------------

class TestSessionTraceIdInPayload:
    def setup_method(self):
        end_session_trace()

    def teardown_method(self):
        end_session_trace()

    def test_session_trace_id_present_when_active(self, tmp_path):
        sid = begin_session_trace("session42")
        log = get_logger("trace_active", logs_dir=tmp_path)
        log.info("test.event")
        payload = _read_last_log_line(tmp_path, "trace_active")
        assert payload.get("session_trace_id") == "session42"

    def test_session_trace_id_absent_when_not_set(self, tmp_path):
        end_session_trace()  # ensure not set
        log = get_logger("trace_absent", logs_dir=tmp_path)
        log.info("test.event")
        payload = _read_last_log_line(tmp_path, "trace_absent")
        assert "session_trace_id" not in payload

    def test_per_event_trace_id_still_present(self, tmp_path):
        log = get_logger("trace_per_event", logs_dir=tmp_path)
        log.info("test.event")
        payload = _read_last_log_line(tmp_path, "trace_per_event")
        assert "trace_id" in payload
        assert len(payload["trace_id"]) == 16

    def test_multiple_events_carry_same_session_id(self, tmp_path):
        sid = begin_session_trace("persistent_sid")
        log = get_logger("trace_multi", logs_dir=tmp_path)
        log.info("event.one")
        log.info("event.two")
        log.info("event.three")
        log_file = _find_log_file(tmp_path)
        assert log_file is not None
        lines = [
            json.loads(l)
            for l in log_file.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("module") == "trace_multi"
        ]
        assert len(lines) == 3
        for line in lines:
            assert line.get("session_trace_id") == "persistent_sid"

    def test_per_event_trace_ids_differ_across_events(self, tmp_path):
        log = get_logger("trace_unique_ids", logs_dir=tmp_path)
        log.info("event.a")
        log.info("event.b")
        log_file = _find_log_file(tmp_path)
        assert log_file is not None
        lines = [
            json.loads(l)
            for l in log_file.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("module") == "trace_unique_ids"
        ]
        assert len(lines) == 2
        tids = [l["trace_id"] for l in lines]
        # Per-event trace IDs are random UUIDs → almost certainly different
        assert len(set(tids)) == 2, "Per-event trace_ids should differ across events"

    def test_after_end_no_session_id(self, tmp_path):
        begin_session_trace("will_end")
        end_session_trace()
        log = get_logger("trace_after_end", logs_dir=tmp_path)
        log.info("post_end.event")
        payload = _read_last_log_line(tmp_path, "trace_after_end")
        assert "session_trace_id" not in payload


# ---------------------------------------------------------------------------
# T-LT4: sensitive event suppression
# ---------------------------------------------------------------------------

class TestSensitiveEventSuppression:
    def setup_method(self):
        end_session_trace()

    def teardown_method(self):
        end_session_trace()

    def _cfg_with_sensitive(self, include: bool) -> dict:
        return {
            "harness": {
                "structured_output": {
                    "trace_include_sensitive_data": include,
                }
            }
        }

    def test_sensitive_suppressed_when_config_false(self, tmp_path):
        log = get_logger("sens_off", logs_dir=tmp_path)
        before_count = _count_log_lines(tmp_path, "sens_off")
        with patch("lib.config_loader.load_config", return_value=self._cfg_with_sensitive(False)):
            log.info("sensitive.event", sensitive=True)
        after_count = _count_log_lines(tmp_path, "sens_off")
        assert after_count == before_count, "Sensitive event should be suppressed"

    def test_sensitive_emitted_when_config_true(self, tmp_path):
        log = get_logger("sens_on", logs_dir=tmp_path)
        before_count = _count_log_lines(tmp_path, "sens_on")
        with patch("lib.config_loader.load_config", return_value=self._cfg_with_sensitive(True)):
            log.info("sensitive.event", sensitive=True)
        after_count = _count_log_lines(tmp_path, "sens_on")
        assert after_count == before_count + 1, "Sensitive event should be emitted"

    def test_non_sensitive_always_emitted_regardless_of_config(self, tmp_path):
        log = get_logger("non_sens", logs_dir=tmp_path)
        before_count = _count_log_lines(tmp_path, "non_sens")
        with patch("lib.config_loader.load_config", return_value=self._cfg_with_sensitive(False)):
            log.info("normal.event", sensitive=False)
        after_count = _count_log_lines(tmp_path, "non_sens")
        assert after_count == before_count + 1

    def test_sensitive_emitted_when_config_missing(self, tmp_path):
        """Default (fail open) — config key absent means emit sensitive events."""
        log = get_logger("sens_missing", logs_dir=tmp_path)
        before_count = _count_log_lines(tmp_path, "sens_missing")
        with patch("lib.config_loader.load_config", return_value={}):
            log.info("sensitive.event", sensitive=True)
        after_count = _count_log_lines(tmp_path, "sens_missing")
        assert after_count == before_count + 1, "Missing config should fail open (emit)"

    def test_sensitive_emitted_when_config_load_fails(self, tmp_path):
        """Import/load failure → fail open → emit."""
        log = get_logger("sens_load_fail", logs_dir=tmp_path)
        before_count = _count_log_lines(tmp_path, "sens_load_fail")
        with patch("lib.config_loader.load_config", side_effect=Exception("load error")):
            log.info("sensitive.event", sensitive=True)
        after_count = _count_log_lines(tmp_path, "sens_load_fail")
        assert after_count == before_count + 1, "Config load failure should fail open"
