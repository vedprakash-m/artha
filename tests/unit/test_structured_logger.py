"""tests/unit/test_structured_logger.py — Phase 2: JSONL observability layer tests.

Ref: specs/pay-debt.md §6.3 T2-1 to T2-12
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


def _get_logger(name: str, tmp_path: Path):
    """Import get_logger with a fresh module to avoid singleton cache bleed."""
    import importlib
    # Clear the cache for fresh test isolation
    import lib.logger as lg_mod
    # Purge any cached entry for this name
    keys_to_remove = [k for k in lg_mod._loggers if k.startswith(f"{name}::")]
    for k in keys_to_remove:
        del lg_mod._loggers[k]
    return lg_mod.get_logger(name, logs_dir=tmp_path)


# ---------------------------------------------------------------------------
# T2-1: get_logger() returns a StructuredLogger
# ---------------------------------------------------------------------------
def test_get_logger_returns_structured_logger(tmp_path):
    import lib.logger as lg
    log = _get_logger("test_t2_1", tmp_path)
    assert isinstance(log, lg.StructuredLogger)


# ---------------------------------------------------------------------------
# T2-2: Logging an event writes one JSON line to the correct file
# ---------------------------------------------------------------------------
def test_logging_writes_one_json_line(tmp_path):
    log = _get_logger("test_t2_2", tmp_path)
    log.info("test.event.written")

    jsonl_files = list(tmp_path.glob("artha.*.log.jsonl"))
    assert len(jsonl_files) == 1, f"Expected 1 JSONL file, got {jsonl_files}"
    lines = [l for l in jsonl_files[0].read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["event"] == "test.event.written"


# ---------------------------------------------------------------------------
# T2-3: JSON line contains ts, level, event, module, trace_id
# ---------------------------------------------------------------------------
def test_json_line_contains_required_keys(tmp_path):
    log = _get_logger("test_t2_3", tmp_path)
    log.info("required.keys.check")

    jsonl_file = list(tmp_path.glob("artha.*.log.jsonl"))[0]
    obj = json.loads(jsonl_file.read_text().strip())
    for key in ("ts", "level", "event", "module", "trace_id"):
        assert key in obj, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# T2-4: correlation_id and numeric kwargs appear in JSON output
# ---------------------------------------------------------------------------
def test_extra_kwargs_appear_in_output(tmp_path):
    log = _get_logger("test_t2_4", tmp_path)
    log.info("extra.kwargs", correlation_id="abc123", records=42, ms=1500)

    jsonl_file = list(tmp_path.glob("artha.*.log.jsonl"))[0]
    obj = json.loads(jsonl_file.read_text().strip())
    assert obj["correlation_id"] == "abc123"
    assert obj["records"] == 42
    assert obj["ms"] == 1500


# ---------------------------------------------------------------------------
# T2-5: Log file path uses YYYY-MM-DD date suffix
# ---------------------------------------------------------------------------
def test_log_file_path_uses_date_suffix(tmp_path):
    log = _get_logger("test_t2_5", tmp_path)
    log.info("date.suffix.check")

    jsonl_files = list(tmp_path.glob("artha.*.log.jsonl"))
    assert len(jsonl_files) >= 1
    # File name matches artha.YYYY-MM-DD.log.jsonl
    import re
    for f in jsonl_files:
        assert re.match(r"artha\.\d{4}-\d{2}-\d{2}\.log\.jsonl", f.name), (
            f"File name doesn't match expected pattern: {f.name}"
        )


# ---------------------------------------------------------------------------
# T2-6: _prune_old_logs removes files older than max_age_days
# ---------------------------------------------------------------------------
def test_prune_old_logs_removes_stale_files(tmp_path):
    from lib.logger import _prune_old_logs

    # Create a file with mtime in the past (35 days ago)
    old_file = tmp_path / "artha.2020-01-01.log.jsonl"
    old_file.write_text('{"event": "old"}\n')
    old_time = time.time() - 35 * 86400
    import os
    os.utime(old_file, (old_time, old_time))

    _prune_old_logs(tmp_path, max_age_days=30)
    assert not old_file.exists(), "Stale file should have been pruned"


# ---------------------------------------------------------------------------
# T2-7: _prune_old_logs preserves files within max_age_days
# ---------------------------------------------------------------------------
def test_prune_old_logs_preserves_recent_files(tmp_path):
    from lib.logger import _prune_old_logs

    recent_file = tmp_path / "artha.2099-01-01.log.jsonl"
    recent_file.write_text('{"event": "recent"}\n')

    _prune_old_logs(tmp_path, max_age_days=30)
    assert recent_file.exists(), "Recent file should NOT be pruned"


# ---------------------------------------------------------------------------
# T2-8: Logger falls back to stderr if logs dir is unwritable
# ---------------------------------------------------------------------------
def test_logger_falls_back_to_stderr_if_unwritable(tmp_path, capsys, monkeypatch):
    import lib.logger as _logger_mod
    from lib.logger import _JsonlHandler, StructuredLogger

    # Simulate unwritable dir by patching _ensure_log_dir to return False.
    # os.chmod(0o444) is unreliable on Windows (doesn't block subdirectory creation).
    monkeypatch.setattr(_logger_mod, "_ensure_log_dir", lambda _dir: False)

    unwritable_dir = tmp_path / "simulated_unwritable"
    handler = _JsonlHandler(unwritable_dir)
    log = StructuredLogger("stderr_test", handler)
    log.info("fallback.test")

    captured = capsys.readouterr()
    assert "[STRUCTURED]" in captured.err


# ---------------------------------------------------------------------------
# T2-9: Fallback to stderr does not raise
# ---------------------------------------------------------------------------
def test_fallback_to_stderr_does_not_raise(tmp_path):
    from lib.logger import _JsonlHandler, StructuredLogger

    unwritable_dir = tmp_path / "unwritable_deep" / "x"
    handler = _JsonlHandler(unwritable_dir)
    log = StructuredLogger("no_raise_test", handler)
    # Must not raise even if the dir write fails
    log.info("no.raise.test")


# ---------------------------------------------------------------------------
# T2-10: Multiple get_logger() calls with same name return same logger
# ---------------------------------------------------------------------------
def test_get_logger_singleton(tmp_path):
    import lib.logger as lg

    key = f"singleton_test::{tmp_path}"
    if key in lg._loggers:
        del lg._loggers[key]

    log1 = lg.get_logger("singleton_test", logs_dir=tmp_path)
    log2 = lg.get_logger("singleton_test", logs_dir=tmp_path)
    assert log1 is log2


# ---------------------------------------------------------------------------
# T2-11: AuditMiddleware emits structured log on OSError
# ---------------------------------------------------------------------------
def test_audit_middleware_emits_structured_on_oserror(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "am_phase2", "scripts/middleware/audit_middleware.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Write to a non-existent state/audit.md path
    am = mod.AuditMiddleware(artha_dir=tmp_path / "missing_dir")
    am._append("structured test\n")

    captured = capsys.readouterr()
    assert "[WARN] audit write failed" in captured.err


# ---------------------------------------------------------------------------
# T2-12: Pipeline integration — connector.fetch event logged after fetch
# ---------------------------------------------------------------------------
def test_pipeline_log_event_format(tmp_path):
    """Verify the connector.fetch log event has the expected fields."""
    import lib.logger as lg

    log = _get_logger("pipeline_test", tmp_path)
    # Simulate what pipeline.py emits after a successful connector fetch
    log.info("connector.fetch", connector="gmail", records=10, ms=500, error=None)

    jsonl_file = list(tmp_path.glob("artha.*.log.jsonl"))[0]
    obj = json.loads(jsonl_file.read_text().strip())
    assert obj["event"] == "connector.fetch"
    assert obj["connector"] == "gmail"
    assert isinstance(obj["records"], int)
    assert isinstance(obj["ms"], (int, float))
