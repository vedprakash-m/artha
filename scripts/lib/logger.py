#!/usr/bin/env python3
"""lib/logger.py — Structured JSONL logging for Artha.

Usage:
    from lib.logger import get_logger
    log = get_logger("pipeline")
    log.info("connector.fetch", connector="gmail", records=142, ms=3200)

Output:
    One JSON line per event to ~/.artha-local/logs/artha.YYYY-MM-DD.log.jsonl
    Rotation: one file per day; stale files auto-pruned on logger init.

Observability contract:
    Every top-level operation emits a trace_id (uuid4 hex, first 16 chars).
    Child events may emit correlation_id / parent_span_id.
    Numeric fields (ms, count, records, errors) are part of the contract so
    connector latency, command latency, and failure-rate summaries can be
    derived from JSONL without a dedicated metrics backend.

PII policy (N3):
    Event names and numeric values only; no user content in kwargs.
    log.info("email.classified", count=5)     # OK
    log.info("email.subject", subject="...")  # NEVER — code-review contract

N4 compliance:
    Log dir: ~/.artha-local/logs/ (machine-local, not cloud-synced).
    Fallback: if dir unwritable → emit to stderr with [STRUCTURED] prefix.

Ref: specs/pay-debt.md Phase 2 §6.2.1
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default log directory (machine-local, same convention as actions.db)
# ---------------------------------------------------------------------------
_DEFAULT_LOG_DIR = Path(os.path.expanduser("~")) / ".artha-local" / "logs"
_MAX_AGE_DAYS = 30

# ---------------------------------------------------------------------------
# Module-level singleton cache: name → Logger
# ---------------------------------------------------------------------------
_loggers: dict[str, "StructuredLogger"] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prune_old_logs(logs_dir: Path, max_age_days: int = _MAX_AGE_DAYS) -> None:
    """Remove JSONL log files older than max_age_days in logs_dir."""
    if not logs_dir.exists():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
    for f in logs_dir.glob("artha.*.log.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass  # Non-critical


def _ensure_log_dir(logs_dir: Path) -> bool:
    """Try to create logs_dir. Return True if writable, False otherwise."""
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        # Quick writability check
        test_file = logs_dir / ".write_test"
        test_file.touch()
        test_file.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Custom handler — writes structured JSON lines
# ---------------------------------------------------------------------------

class _JsonlHandler(logging.Handler):
    """Write one JSON line per record to a daily log file.

    Falls back to stderr (with [STRUCTURED] prefix) if the log dir is
    unwritable — never crashes the caller.
    """

    def __init__(self, logs_dir: Path) -> None:
        super().__init__()
        self._logs_dir = logs_dir
        self._writable = _ensure_log_dir(logs_dir)
        if self._writable:
            _prune_old_logs(logs_dir)

    def _log_path(self) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._logs_dir / f"artha.{date_str}.log.jsonl"

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        # The structured payload was attached by StructuredLogger.log()
        payload = getattr(record, "_structured_payload", None)
        if payload is None:
            return

        line = json.dumps(payload, ensure_ascii=False, default=str)

        if self._writable:
            try:
                with self._log_path().open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                return
            except OSError:
                self._writable = False  # Demote to stderr fallback

        # Fallback: stderr with [STRUCTURED] prefix
        print(f"[STRUCTURED] {line}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class StructuredLogger:
    """Thin wrapper that adds structured context to stdlib Logger."""

    def __init__(self, name: str, handler: _JsonlHandler) -> None:
        self._name = name
        self._handler = handler
        self._logger = logging.getLogger(f"artha.structured.{name}")
        self._logger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers if the same logger is fetched twice
        if not self._logger.handlers:
            self._logger.addHandler(handler)
        # Don't propagate to root logger (avoids duplicate output)
        self._logger.propagate = False

    def _emit(self, level: int, event: str, **kwargs: Any) -> None:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": logging.getLevelName(level),
            "event": event,
            "module": self._name,
            "trace_id": uuid.uuid4().hex[:16],
        }
        payload.update(kwargs)
        record = logging.LogRecord(
            name=self._logger.name,
            level=level,
            pathname="",
            lineno=0,
            msg=event,
            args=(),
            exc_info=None,
        )
        record._structured_payload = payload  # type: ignore[attr-defined]
        self._handler.emit(record)

    def info(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, event, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, event, **kwargs)


def get_logger(name: str, logs_dir: Path | None = None) -> StructuredLogger:
    """Return a StructuredLogger singleton for name.

    Parameters
    ----------
    name:
        Module name, e.g. ``"pipeline"``, ``"channel"``.
    logs_dir:
        Override log directory (default: ``~/.artha-local/logs/``).
        Pass a tmp_path in tests.
    """
    cache_key = f"{name}::{logs_dir}"
    if cache_key not in _loggers:
        resolved_dir = logs_dir if logs_dir is not None else _DEFAULT_LOG_DIR
        handler = _JsonlHandler(resolved_dir)
        _loggers[cache_key] = StructuredLogger(name, handler)
    return _loggers[cache_key]
