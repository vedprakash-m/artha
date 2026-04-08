#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/telemetry.py — Append-only JSONL telemetry sink for Artha.

Single point of truth for all structured telemetry events emitted by
pipeline.py and its sub-components.  All writes are atomic (tempfile +
os.replace is NOT used here because appending to JSONL does not require
atomic overwrite — each line is self-contained).  The file grows
without bound; rotation is handled via log_digest.py on demand.

## Event Schema (all fields)

| Field          | Type   | Always | Notes                                         |
|----------------|--------|--------|-----------------------------------------------|
| ts             | str    | ✅     | ISO-8601 UTC timestamp                        |
| event          | str    | ✅     | dot-namespaced event type (see EVENTS below)  |
| session_id     | str    | ✅     | Format: {YYYYMMDD}_{uuid4().hex[:8]}          |
| domain         | str    | ❌     | Artha domain name (e.g. "finance")            |
| step           | str    | ❌     | FSM state name (e.g. "FETCH", "CLASSIFY")     |
| connector      | str    | ❌     | Connector identifier                          |
| ttft_ms        | int    | ❌     | Time-to-first-token in milliseconds           |
| latency_ms     | int    | ❌     | Total latency in milliseconds                 |
| input_tokens   | int    | ❌     | LLM input token count                        |
| output_tokens  | int    | ❌     | LLM output token count                       |
| model_id       | str    | ❌     | Model identifier                              |
| signal_id      | str    | ❌     | Signal/record identifier                      |
| matched_domain | str    | ❌     | Domain routed to                              |
| confidence     | float  | ❌     | Routing confidence score [0.0–1.0]            |
| tier           | str    | ❌     | Routing tier: "explicit"|"tfidf"|"unclassified"|
| elapsed_ms     | int    | ❌     | Elapsed time for a worker/step                |
| error          | str    | ❌     | Error message (no PII in this field)          |
| justification  | str    | ❌     | Override justification (--force-wave0)        |
| extra          | dict   | ❌     | Arbitrary additional metadata                 |

## Canonical Event Names

    pipeline.session_start          — session begins (PREFLIGHT entry)
    pipeline.step_enter             — FSM state entered
    pipeline.step_exit              — FSM state completed (exit condition met)
    pipeline.step_timeout           — step timed out
    pipeline.interrupted            — unclean shutdown / exception
    pipeline.degraded_mode          — fallback to legacy 21-step prompt
    connector.fetch                 — connector fetch completed
    connector.stale                 — connector data exceeds TTL
    routing.classified              — signal successfully classified (tier 1/2)
    routing.unclassified            — signal sent to UNCLASSIFIED queue
    worker.start                    — domain worker invoked
    worker.complete                 — domain worker returned
    worker.timeout                  — domain worker timed out (30s)
    worker.rejected                 — worker output rejected (boundary violation)
    tool_boundary_violation         — prohibited tool call pattern detected
    wave0_override                  — --force-wave0 flag used
    occ.conflict                    — version mismatch detected on state write
    occ.migrated                    — version field injected into state file
    idempotency.blocked             — duplicate action blocked
    idempotency.pending_crash       — PENDING key found at PREFLIGHT (crash recovery)
    action.validated                — deterministic validator passed
    action.rejected                 — deterministic validator rejected
    pii.scan_hit                    — PII pattern detected in action payload
    staleness.warn                  — data staleness warning (4h threshold)
    staleness.critical              — critical staleness (8h threshold)

## Design Constraints (from specs/harden.md)
- NO PII in any log entry — event metadata only
- Append-only — never edit or truncate
- Plaintext JSONL — not encrypted (no sensitive data)
- stdlib-only — no third-party dependencies
- Must not raise exceptions in callers (non-fatal by design)

Ref: specs/harden.md §3 Phase 0, §2.1.3 FSM State Graph
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TELEMETRY_PATH = _REPO_ROOT / "state" / "telemetry.jsonl"

# Module-level session_id: set once per process, reused for all events.
# Callers may override via set_session_id() (e.g. pipeline.py at startup).
_session_id: str = ""


def _default_session_id() -> str:
    """Generate a session_id in the canonical format: {YYYYMMDD}_{hex8}."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    hex_part = uuid.uuid4().hex[:8]
    return f"{date_part}_{hex_part}"


def get_session_id() -> str:
    """Return the current session_id, generating one if not yet set."""
    global _session_id
    if not _session_id:
        _session_id = _default_session_id()
    return _session_id


def set_session_id(sid: str) -> None:
    """Override the module-level session_id.

    Call this at the start of pipeline.py before emitting any events.

    Args:
        sid: Session ID in the canonical format {YYYYMMDD}_{hex8}.
             Validated format — raises ValueError on bad format.
    """
    global _session_id
    if not sid or len(sid) < 10:
        raise ValueError(f"Invalid session_id format: {sid!r}")
    _session_id = sid


# ---------------------------------------------------------------------------
# Core emit
# ---------------------------------------------------------------------------

def emit(
    event: str,
    *,
    session_id: str | None = None,
    domain: str | None = None,
    step: str | None = None,
    connector: str | None = None,
    ttft_ms: int | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model_id: str | None = None,
    signal_id: str | None = None,
    matched_domain: str | None = None,
    confidence: float | None = None,
    tier: str | None = None,
    elapsed_ms: int | None = None,
    error: str | None = None,
    justification: str | None = None,
    extra: dict[str, Any] | None = None,
    _path: Path | None = None,
) -> None:
    """Append a structured telemetry event to state/telemetry.jsonl.

    Non-fatal: swallows all exceptions. If the write fails, a warning is
    printed to stderr but the caller is not interrupted.

    Args:
        event:        Dot-namespaced event name (see EVENTS above).
        session_id:   Override session_id (default: module-level _session_id).
        domain:       Artha domain name.
        step:         FSM state name.
        connector:    Connector identifier.
        ttft_ms:      Time-to-first-token (ms).
        latency_ms:   Total latency (ms).
        input_tokens: LLM input token count.
        output_tokens: LLM output token count.
        model_id:     Model identifier (e.g. "claude-sonnet-4-6").
        signal_id:    Signal/record identifier.
        matched_domain: Domain a signal was routed to.
        confidence:   Routing confidence score.
        tier:         Routing tier ("explicit" | "tfidf" | "unclassified").
        elapsed_ms:   Elapsed time for a worker/step.
        error:        Error message (must NOT contain PII).
        justification: Override justification (--force-wave0).
        extra:        Additional arbitrary metadata (must be JSON-serializable).
        _path:        Override telemetry file path (test use only).
    """
    try:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "session_id": session_id or get_session_id(),
        }
        # Only include non-None scalar fields
        _optionals = {
            "domain": domain,
            "step": step,
            "connector": connector,
            "ttft_ms": ttft_ms,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model_id": model_id,
            "signal_id": signal_id,
            "matched_domain": matched_domain,
            "confidence": confidence,
            "tier": tier,
            "elapsed_ms": elapsed_ms,
            "error": error,
            "justification": justification,
        }
        for key, val in _optionals.items():
            if val is not None:
                record[key] = val
        if extra:
            record["extra"] = extra

        target = _path or _TELEMETRY_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:  # noqa: BLE001
        # Telemetry must never crash callers
        print(f"[telemetry] WARNING: emit failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def emit_step_enter(step: str, **kwargs: Any) -> None:
    """Emit pipeline.step_enter event."""
    emit("pipeline.step_enter", step=step, **kwargs)


def emit_step_exit(step: str, elapsed_ms: int | None = None, **kwargs: Any) -> None:
    """Emit pipeline.step_exit event."""
    emit("pipeline.step_exit", step=step, elapsed_ms=elapsed_ms, **kwargs)


def emit_step_timeout(step: str, elapsed_ms: int | None = None, domain: str | None = None, **kwargs: Any) -> None:
    """Emit pipeline.step_timeout event (worker or step timed out)."""
    emit("pipeline.step_timeout", step=step, elapsed_ms=elapsed_ms, domain=domain, **kwargs)


def emit_routing(
    signal_id: str,
    matched_domain: str,
    confidence: float,
    tier: str,
    **kwargs: Any,
) -> None:
    """Emit a routing event (routing.classified or routing.unclassified)."""
    event = "routing.unclassified" if tier == "unclassified" else "routing.classified"
    emit(
        event,
        signal_id=signal_id,
        matched_domain=matched_domain,
        confidence=confidence,
        tier=tier,
        **kwargs,
    )


def emit_staleness(connector: str, age_hours: float, critical: bool = False, **kwargs: Any) -> None:
    """Emit staleness.warn or staleness.critical event."""
    event = "staleness.critical" if critical else "staleness.warn"
    emit(event, connector=connector, extra={"age_hours": round(age_hours, 2)}, **kwargs)


def emit_occ_conflict(path: str, expected_version: int, found_version: int, **kwargs: Any) -> None:
    """Emit occ.conflict event when version mismatch is detected."""
    emit(
        "occ.conflict",
        error=f"version mismatch — expected {expected_version}, found {found_version}",
        extra={"path": path, "expected_version": expected_version, "found_version": found_version},
        **kwargs,
    )


def emit_idempotency_blocked(action_type: str, composite_key: str, **kwargs: Any) -> None:
    """Emit idempotency.blocked when duplicate action is suppressed."""
    emit(
        "idempotency.blocked",
        extra={"action_type": action_type, "key_prefix": composite_key[:12]},
        **kwargs,
    )


def emit_wave0_override(justification: str, **kwargs: Any) -> None:
    """Emit wave0_override event when --force-wave0 flag is used."""
    emit("wave0_override", justification=justification, **kwargs)


def emit_tool_boundary_violation(domain: str, tool_name: str, **kwargs: Any) -> None:
    """Emit tool_boundary_violation event."""
    emit(
        "tool_boundary_violation",
        domain=domain,
        error=f"prohibited tool call: {tool_name}",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Baseline snapshot helper
# ---------------------------------------------------------------------------

def compute_baseline_stats(_path: Path | None = None) -> dict[str, Any]:
    """Compute summary statistics from telemetry.jsonl for baseline snapshot.

    Returns a dict suitable for writing to state/telemetry_baseline.json.
    Called after ≥7 days of telemetry to establish Phase 0 baseline.

    Returns:
        Baseline stats dict with:
          - total_events: int
          - session_count: int
          - routing_confidence_p10/p50/p90: float
          - unclassified_rate: float (0–1)
          - error_rate: float (0–1)
          - date_range: {from, to}
    """
    target = _path or _TELEMETRY_PATH
    if not target.exists():
        return {"error": "telemetry.jsonl not found"}

    events: list[dict] = []
    try:
        with open(target, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError as exc:
        return {"error": str(exc)}

    if not events:
        return {"error": "no events found"}

    sessions = {e.get("session_id") for e in events if e.get("session_id")}
    routing_events = [e for e in events if e.get("event") in ("routing.classified", "routing.unclassified")]
    confidences = [e["confidence"] for e in routing_events if "confidence" in e]
    confidences.sort()

    unclassified = sum(1 for e in routing_events if e.get("event") == "routing.unclassified")
    error_events = [e for e in events if e.get("error")]

    def percentile(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        idx = max(0, int(len(data) * p / 100) - 1)
        return round(data[idx], 4)

    timestamps = sorted(e["ts"] for e in events if "ts" in e)

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "session_count": len(sessions),
        "routing_events": len(routing_events),
        "routing_confidence_p10": percentile(confidences, 10),
        "routing_confidence_p50": percentile(confidences, 50),
        "routing_confidence_p90": percentile(confidences, 90),
        "unclassified_rate": round(unclassified / max(len(routing_events), 1), 4),
        "error_rate": round(len(error_events) / max(len(events), 1), 4),
        "date_range": {
            "from": timestamps[0] if timestamps else None,
            "to": timestamps[-1] if timestamps else None,
        },
    }
