"""
scripts/lib/observability.py — LLM call tracing (DEBT-OBSERV-001)
==================================================================
When ARTHA_LLM_TRACE=1 is set, any code that invokes an LLM API
should call `llm_trace()` to append a JSONL record to tmp/llm_trace.jsonl.

This module is deliberately import-safe: it never imports anthropic/openai
itself and never raises — all errors are silently suppressed so tracing
never impacts pipeline correctness.

Usage::
    from lib.observability import llm_trace

    llm_trace(
        caller="artha.generate_brief",
        model="claude-opus-4-5",
        prompt_tokens=1200,
        completion_tokens=320,
        latency_ms=1840,
        metadata={"domain": "finance", "trace_id": "abc123"},
    )
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# Env-gate: tracing is opt-in.  Set ARTHA_LLM_TRACE=1 to enable.
_TRACE_ENV = "ARTHA_LLM_TRACE"

# Resolved once at module import — immutable thereafter.
def _default_trace_path() -> Path:
    _artha_dir = Path(__file__).resolve().parent.parent.parent
    return _artha_dir / "tmp" / "llm_trace.jsonl"


def llm_trace(
    caller: str,
    model: str,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: float | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    trace_file: Path | None = None,
) -> None:
    """Append one LLM call record to tmp/llm_trace.jsonl (DEBT-OBSERV-001).

    Silently no-ops when ARTHA_LLM_TRACE is not set to a truthy value.
    Never raises — tracing must never affect caller correctness.

    Args:
        caller:             Dotted name of the calling function, e.g. "pipeline.generate_brief".
        model:              Model identifier, e.g. "claude-opus-4-5".
        prompt_tokens:      Input token count (optional).
        completion_tokens:  Output token count (optional).
        latency_ms:         Wall-clock time of the LLM call in milliseconds (optional).
        error:              Error string if the call failed (optional).
        metadata:           Additional key-value pairs to include in the record.
        trace_file:         Override default trace file path (for testing).
    """
    if not os.environ.get(_TRACE_ENV, "").strip():
        return

    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "caller": caller,
        "model": model,
    }
    if prompt_tokens is not None:
        record["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        record["completion_tokens"] = completion_tokens
    if latency_ms is not None:
        record["latency_ms"] = round(latency_ms, 1)
    if error is not None:
        record["error"] = error
    if metadata:
        # Shallow copy — do not mutate caller's dict
        record["metadata"] = {str(k): v for k, v in metadata.items()}

    try:
        target = trace_file or _default_trace_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass  # tracing is best-effort
