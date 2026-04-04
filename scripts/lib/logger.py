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
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default log directory (machine-local, same convention as actions.db)
# ---------------------------------------------------------------------------
_DEFAULT_LOG_DIR = Path(os.path.expanduser("~")) / ".artha-local" / "logs"
_MAX_AGE_DAYS = 30

# ---------------------------------------------------------------------------
# Session trace context variable
# ---------------------------------------------------------------------------
# Stores an optional session-level trace ID that is propagated to every
# emitted event as ``session_trace_id`` (distinct from the per-event
# ``trace_id`` which is generated fresh for each emission).
# Set via begin_session_trace() / end_session_trace().
_SESSION_TRACE_ID: ContextVar[str | None] = ContextVar(
    "_SESSION_TRACE_ID", default=None
)


def begin_session_trace(trace_id: str | None = None) -> str:
    """Start a session-level trace and return the trace ID in use.

    Sets a ContextVar so that all subsequent log events emitted from the
    same async context carry a ``session_trace_id`` field.

    Parameters
    ----------
    trace_id:
        Explicit trace ID to use.  If None, a fresh ``uuid4().hex[:16]``
        is generated.  Useful for correlating log events across a
        user-visible session.

    Returns
    -------
    str
        The trace ID that was set (same value as ``trace_id`` if provided).
    """
    sid = trace_id if trace_id is not None else uuid.uuid4().hex[:16]
    _SESSION_TRACE_ID.set(sid)
    return sid


def end_session_trace() -> None:
    """Clear the session-level trace ID from the current context."""
    _SESSION_TRACE_ID.set(None)


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

    def __init__(self, name: str, handler: _JsonlHandler, defaults: dict | None = None) -> None:
        self._name = name
        self._handler = handler
        self._defaults = defaults or {}
        self._logger = logging.getLogger(f"artha.structured.{name}")
        self._logger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers if the same logger is fetched twice
        if not self._logger.handlers:
            self._logger.addHandler(handler)
        # Don't propagate to root logger (avoids duplicate output)
        self._logger.propagate = False

    def _emit(self, level: int, event: str, sensitive: bool = False, **kwargs: Any) -> None:
        # Sensitive events: honour trace_include_sensitive_data config gate.
        # When sensitive=True and the config flag is false, omit the event.
        if sensitive:
            try:
                from lib.config_loader import load_config  # noqa: PLC0415
                cfg = load_config("artha_config") or {}
                if not cfg.get("harness", {}).get("structured_output", {}).get(
                    "trace_include_sensitive_data", True
                ):
                    return  # Sensitive event suppressed by config
            except Exception:  # noqa: BLE001
                pass  # Config unavailable — emit the event

        span_id = kwargs.pop("span_id", None)
        parent_span_id = kwargs.pop("parent_span_id", None)
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": logging.getLevelName(level),
            "event": event,
            "module": self._name,
            "trace_id": uuid.uuid4().hex[:16],
        }
        # Session-level trace ID (set via begin_session_trace) — injected as
        # a separate field alongside the per-event trace_id above.
        session_tid = _SESSION_TRACE_ID.get()
        if session_tid is not None:
            payload["session_trace_id"] = session_tid
        payload.update(self._defaults)
        payload.update(kwargs)
        if span_id is not None:
            payload["span_id"] = span_id
        if parent_span_id is not None:
            payload["parent_span_id"] = parent_span_id
        payload = {k: v for k, v in payload.items() if v is not None}
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


def get_logger(
    name: str,
    logs_dir: Path | None = None,
    defaults: dict | None = None,
) -> StructuredLogger:
    """Return a StructuredLogger singleton for name.

    Parameters
    ----------
    name:
        Module name, e.g. ``"pipeline"``, ``"channel"``.
    logs_dir:
        Override log directory (default: ``~/.artha-local/logs/``).
        Pass a tmp_path in tests.
    defaults:
        Default key/value pairs merged into every log event emitted by
        this logger (e.g. ``{"session_id": sid, "config_hash": h}``).  
        Keys present in a specific ``log.*()`` call override these defaults.
    """
    cache_key = f"{name}::{logs_dir}::{json.dumps(defaults or {}, sort_keys=True)}"
    if cache_key not in _loggers:
        resolved_dir = logs_dir if logs_dir is not None else _DEFAULT_LOG_DIR
        handler = _JsonlHandler(resolved_dir)
        _loggers[cache_key] = StructuredLogger(name, handler, defaults=defaults)
    return _loggers[cache_key]
