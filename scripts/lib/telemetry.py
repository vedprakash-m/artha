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

import hashlib
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

# ---------------------------------------------------------------------------
# Hash chain state (ST-01 — specs/steal.md §15.2.2)
# ---------------------------------------------------------------------------

_GENESIS_HASH: str = "0" * 64


def _load_prev_hash() -> str:
    """Read the last entry_hash from telemetry.jsonl to resume hash chain after restart."""
    if not _TELEMETRY_PATH.exists():
        return _GENESIS_HASH
    try:
        with open(_TELEMETRY_PATH, encoding="utf-8") as fh:
            last_hash = _GENESIS_HASH
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    h = entry.get("entry_hash")
                    if isinstance(h, str) and len(h) == 64:
                        last_hash = h
                except json.JSONDecodeError:
                    continue
            return last_hash
    except Exception:  # noqa: BLE001
        return _GENESIS_HASH


_prev_hash: str = _load_prev_hash()


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


def _compute_entry_hash(record: dict) -> str:
    """Return SHA-256 hex digest of record serialized with sorted keys (ST-01)."""
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


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

    Each emitted entry contains ``prev_hash`` (hash of the preceding entry) and
    ``entry_hash`` (SHA-256 of the current record excluding ``entry_hash``),
    forming a tamper-evident chain (ST-01).

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
    global _prev_hash
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

        # Hash chain (ST-01)
        record["prev_hash"] = _prev_hash
        entry_hash = _compute_entry_hash(record)
        record["entry_hash"] = entry_hash

        target = _path or _TELEMETRY_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
        _prev_hash = entry_hash
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


# ---------------------------------------------------------------------------
# Hash chain verification (ST-01)
# ---------------------------------------------------------------------------

def verify_integrity(path: Path | None = None) -> bool:
    """Verify the tamper-evident hash chain of the telemetry log.

    Reads every entry in telemetry.jsonl and checks that each entry's
    ``prev_hash`` matches the previous entry's ``entry_hash``, and that
    each entry's ``entry_hash`` matches the SHA-256 of the entry (minus the
    ``entry_hash`` field itself).

    Pre-ST-01 entries (missing ``entry_hash``/``prev_hash``) are skipped
    so that existing logs are not broken by the upgrade.

    Returns:
        True if the chain is intact (or log is empty / pre-ST-01 only).
        False if any tampering, corruption, or gap is detected.
    """
    target = path or _TELEMETRY_PATH
    if not target.exists():
        return True
    prev = _GENESIS_HASH
    chain_started = False
    try:
        with open(target, encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, 1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    print(f"[telemetry] WARNING: corrupt JSON at line {lineno}, chain broken", file=sys.stderr)
                    return False

                stored_hash = entry.get("entry_hash")
                stored_prev = entry.get("prev_hash")

                # Skip pre-ST-01 entries that have no hash fields
                if stored_hash is None and stored_prev is None:
                    continue

                if chain_started and stored_prev != prev:
                    print(f"[telemetry] WARNING: chain break at line {lineno} — prev_hash mismatch", file=sys.stderr)
                    return False

                # Recompute hash: strip entry_hash field, hash the rest
                check = {k: v for k, v in entry.items() if k != "entry_hash"}
                computed = _compute_entry_hash(check)
                if computed != stored_hash:
                    print(f"[telemetry] WARNING: hash mismatch at line {lineno}", file=sys.stderr)
                    return False

                prev = stored_hash
                chain_started = True
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[telemetry] WARNING: verify_integrity failed: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Trace ID generation and correlation queries (ST-06)
# ---------------------------------------------------------------------------


def generate_trace_id(
    session_id: str,
    agent_name: str,
    timestamp_ms: int,
    quantization_ms: int = 10,
) -> str:
    """Generate a deterministic trace ID for cross-event correlation.

    The trace ID is a 16-character hex string derived from a SHA-256 hash of
    the inputs. The timestamp is quantized so that events within the same
    quantization window share the same trace ID, reducing spurious splits.

    Args:
        session_id: Session identifier (e.g. "20260422_ab1c2d3e").
        agent_name: Agent/component name (e.g. "pipeline", "work_loop").
        timestamp_ms: Unix timestamp in milliseconds.
        quantization_ms: Quantization window in ms (default: 10ms).

    Returns:
        16-character lowercase hex string (64-bit trace ID).
    """
    quantized_ts = (timestamp_ms // quantization_ms) * quantization_ms
    raw = f"{session_id}:{agent_name}:{quantized_ts}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest[:16]


def query_events_by_trace(
    trace_id: str,
    path: Path | None = None,
) -> list[dict]:
    """Return all telemetry events matching the given trace_id.

    Scans the JSONL telemetry log and returns every entry whose ``trace_id``
    field equals *trace_id*.  Returns an empty list if the file does not
    exist or no matching entries are found.

    Args:
        trace_id: The trace ID to search for (16-char hex string).
        path: Path to the JSONL file. Defaults to the module-level path.

    Returns:
        List of event dicts (chronological order, as stored in the log).
    """
    target = path or _TELEMETRY_PATH
    if not target.exists():
        return []
    results: list[dict] = []
    try:
        with open(target, encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if entry.get("trace_id") == trace_id:
                    results.append(entry)
    except Exception:  # noqa: BLE001
        pass
    return results


def query_events_by_session(
    session_id: str,
    path: Path | None = None,
) -> list[dict]:
    """Return all telemetry events belonging to a session.

    Args:
        session_id: Session identifier to filter by.
        path: Path to the JSONL file. Defaults to the module-level path.

    Returns:
        List of matching event dicts in log order.
    """
    target = path or _TELEMETRY_PATH
    if not target.exists():
        return []
    results: list[dict] = []
    try:
        with open(target, encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if entry.get("session_id") == session_id:
                    results.append(entry)
    except Exception:  # noqa: BLE001
        pass
    return results
