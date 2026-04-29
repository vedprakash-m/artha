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

# Env-gate: ARTHA_LLM_TRACE=1 enables tracing (opt-in via env).
# RD-49: artha_config.yaml → observability.llm_trace_enabled also enables it (opt-out default).
_TRACE_ENV = "ARTHA_LLM_TRACE"


def _is_trace_enabled() -> bool:
    """Return True if LLM tracing is enabled (env var OR config flag)."""
    if os.environ.get(_TRACE_ENV, "").strip():
        return True
    # Config-based opt-out (RD-49): enabled by default unless explicitly disabled
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config")
        return bool((cfg.get("pipeline") or {}).get("observability", {}).get("llm_trace_enabled", True))
    except Exception:  # noqa: BLE001
        return False

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
    if not _is_trace_enabled():
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


def semantic_verify_trace(
    *,
    sender_domain: str,
    signal_type: str,
    subject_template: str,
    model: str,
    cache_hit: bool,
    decision: str | None,
    latency_ms: float | None = None,
    fallback_reason: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    trace_file: "Path | None" = None,
) -> None:
    """Convenience wrapper for semantic verification LLM call tracing.

    Emits a trace record via llm_trace() with structured metadata covering
    all fields required for post-deployment calibration (§4.4.1):
    cache_hit, fallback_reason, decision, and the hash-based cache key.

    Args:
        sender_domain:    Sender domain used as part of the cache key.
        signal_type:      Signal type being verified (e.g. "bill_due").
        subject_template: Normalised subject hash component.
        model:            Model identifier used for the call.
        cache_hit:        True if result came from tmp/semantic_cache.json.
        decision:         "YES", "NO", "timeout", or None on error.
        latency_ms:       Wall-clock time of the LLM call in milliseconds.
        fallback_reason:  Reason for fallback (e.g. "timeout", "model_unavailable").
        prompt_tokens:    Input token count if known.
        completion_tokens: Output token count if known.
        trace_file:       Override default trace file path (for testing).
    """
    llm_trace(
        caller="email_signal_extractor.semantic_verify",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        error=fallback_reason if decision is None else None,
        metadata={
            "cache_hit": cache_hit,
            "fallback_reason": fallback_reason,
            # G-7: Calibration correlation — decision + signal_type here are the "predicted" label.
            # When trust_metrics records the actual user_decision, correlate offline via timestamp
            # window or proposed_action_id. Direct DB write requires a trust_metrics migration
            # (add semantic_prediction column). For now, this trace is the calibration record.
            # Ref: specs/action-convert.md §4.4.1 step 4e — "implemented via trace correlation".
            "decision": decision,
            "sender_domain": sender_domain,
            "signal_type": signal_type,
            "subject_template_fragment": subject_template[:20],
        },
        trace_file=trace_file,
    )
