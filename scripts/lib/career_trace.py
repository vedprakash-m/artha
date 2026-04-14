"""scripts/lib/career_trace.py — JSONL trace write utility for career search intelligence.

Writes structured evaluation trace entries to state/career_audit.jsonl.
Enforces 90-day retention cleanup on each write (O-F8).

Each trace entry records:
  - Evaluation metadata (report number, company, role, score, blocks_completed)
  - Token estimates (suffixed with _estimate per SM-5)
  - Block D routing (Claude inline vs Gemini CLI)
  - Guardrail events (CareerJDInjectionGR, auth wall detections)
  - Story Bank mutations (appended / updated)
  - Fingerprints (posting_fingerprint, cv_content_hash — audit trail)

Usage:
    from lib.career_trace import CareerTrace
    trace = CareerTrace()
    trace.write_eval_entry(company="Acme", role="Sr AI Eng", ...)
    trace.write_guardrail_event(guardrail="CareerJDInjectionGR", ...)
    trace.write_story_mutation(op="append", story_num=3, ...)

Ref: specs/career-ops.md §5.2, §16.4, §9.3
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

log = logging.getLogger(__name__)

_TRACE_FILE = _REPO_ROOT / "state" / "career_audit.jsonl"
_RETENTION_DAYS = 90  # O-F8: trim entries older than 90 days


# ---------------------------------------------------------------------------
# Schema constants for CAREER-GUARDRAIL trace entries (N-F4)
# ---------------------------------------------------------------------------

GUARDRAIL_TRACE_SCHEMA_VERSION = "1.0"
EVAL_TRACE_SCHEMA_VERSION = "1.0"


class CareerTrace:
    """Append-only JSONL trace writer for career search audit trail.

    Thread safety: not designed for concurrent use (single-session write pattern).
    Each write() call triggers retention cleanup to enforce 90-day cap.
    """

    def __init__(self, trace_path: Optional[Path] = None) -> None:
        self._path = trace_path or _TRACE_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_eval_entry(
        self,
        *,
        report_number: str,
        company: str,
        role: str,
        archetype: str,
        score: Optional[float],
        blocks_completed: list[str],
        posting_fingerprint: str,
        cv_content_hash: str,
        block_d_source: str,          # "claude_inline" | "gemini_cli" | "unavailable"
        input_tokens_estimate: int,
        output_tokens_estimate: int,
        block_d_tokens_estimate: int = 0,
        immigration_enrichment: str = "unavailable",  # "applied" | "skipped" | "unavailable"
        finance_enrichment: str = "unavailable",
        calendar_enrichment: str = "unavailable",
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Write a full evaluation trace entry."""
        entry: dict[str, Any] = {
            "schema_version": EVAL_TRACE_SCHEMA_VERSION,
            "event": "career_eval",
            "timestamp": _utcnow(),
            "report_number": report_number,
            "company": company,
            "role": role,
            "archetype": archetype,
            "score": score,
            "blocks_completed": blocks_completed,
            "posting_fingerprint": posting_fingerprint,
            "cv_content_hash": cv_content_hash,
            "block_d_source": block_d_source,
            "enrichments": {
                "immigration": immigration_enrichment,
                "finance": finance_enrichment,
                "calendar": calendar_enrichment,
            },
            # SM-5: token fields renamed to _estimate suffix
            "input_tokens_estimate": input_tokens_estimate,
            "output_tokens_estimate": output_tokens_estimate,
            "block_d_tokens_estimate": block_d_tokens_estimate,
        }
        if extra:
            entry.update(extra)
        self._append(entry)

    def write_guardrail_event(
        self,
        *,
        guardrail: str,
        triggered: bool,
        action: str,                  # "blocked" | "flagged" | "passed"
        detail: str = "",
        report_number: Optional[str] = None,
        jd_url: Optional[str] = None,
    ) -> None:
        """Write a CAREER-GUARDRAIL trace entry (N-F4 contract).

        Schema is the adversarial test contract — do not rename fields.
        """
        entry: dict[str, Any] = {
            "schema_version": GUARDRAIL_TRACE_SCHEMA_VERSION,
            "event": "CAREER-GUARDRAIL",
            "timestamp": _utcnow(),
            "guardrail": guardrail,
            "triggered": triggered,
            "action": action,
            "detail": detail,
        }
        if report_number is not None:
            entry["report_number"] = report_number
        if jd_url is not None:
            # Never store full JD content — only URL (PII guard)
            entry["jd_url"] = jd_url
        self._append(entry)

    def write_story_mutation(
        self,
        *,
        op: str,                      # "append" | "update_used_for" | "pin" | "evict"
        story_num: int,
        story_title: str,
        archetype_tag: str,
        capability_tag: str,
        report_number: str,
        tag_warnings: Optional[list[str]] = None,
    ) -> None:
        """Write a Story Bank mutation trace (N-F10, Story Bank operations)."""
        entry: dict[str, Any] = {
            "schema_version": EVAL_TRACE_SCHEMA_VERSION,
            "event": "story_bank_mutation",
            "timestamp": _utcnow(),
            "op": op,
            "story_num": story_num,
            "story_title": story_title,
            "archetype_tag": archetype_tag,
            "capability_tag": capability_tag,
            "report_number": report_number,
        }
        if tag_warnings:
            entry["tag_warnings"] = tag_warnings
        self._append(entry)

    def write_pdf_event(
        self,
        *,
        op: str,                      # "generated" | "fallback_html" | "failed"
        report_number: str,
        output_path: str,
        error: Optional[str] = None,
    ) -> None:
        """Write a PDF generation trace entry."""
        entry: dict[str, Any] = {
            "schema_version": EVAL_TRACE_SCHEMA_VERSION,
            "event": "career_pdf",
            "timestamp": _utcnow(),
            "op": op,
            "report_number": report_number,
            "output_path": output_path,
        }
        if error:
            entry["error"] = error
        self._append(entry)

    def write_portal_scan_event(
        self,
        *,
        portal: str,
        new_matches: int,
        skipped_dupes: int,
        reposted_cross_linked: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Write a portal scan trace entry (Phase 2)."""
        entry: dict[str, Any] = {
            "schema_version": EVAL_TRACE_SCHEMA_VERSION,
            "event": "portal_scan",
            "timestamp": _utcnow(),
            "portal": portal,
            "new_matches": new_matches,
            "skipped_dupes": skipped_dupes,
            "reposted_cross_linked": reposted_cross_linked,
        }
        if error:
            entry["error"] = error
        self._append(entry)

    def write_block_d_gemini_query(
        self,
        *,
        report_number: str,
        query_template: str,          # Full parameterized query (no PII)
        response_tokens_estimate: int,
        truncated: bool,
    ) -> None:
        """Log Block D Gemini query template and response metadata (§8.4).

        The full parameterized query is stored here (verified PII-free by template).
        safe_cli.py logs only query length (not content) to state/audit.md.
        """
        entry: dict[str, Any] = {
            "schema_version": EVAL_TRACE_SCHEMA_VERSION,
            "event": "block_d_gemini_query",
            "timestamp": _utcnow(),
            "report_number": report_number,
            "query_template": query_template,
            "response_tokens_estimate": response_tokens_estimate,
            "truncated": truncated,
        }
        self._append(entry)

    def write_heartbeat(self, *, agent: str, status: str, error: Optional[str] = None) -> None:
        """Write agent heartbeat (CareerSearchAgent EAR-3 pattern — N-F7)."""
        entry: dict[str, Any] = {
            "schema_version": EVAL_TRACE_SCHEMA_VERSION,
            "event": "agent_heartbeat",
            "timestamp": _utcnow(),
            "agent": agent,
            "status": status,
        }
        if error:
            entry["error"] = error
        self._append(entry)

    # ------------------------------------------------------------------
    # Read utilities
    # ------------------------------------------------------------------

    def read_all(self) -> list[dict[str, Any]]:
        """Read all trace entries (for testing / view commands)."""
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    log.warning("career_trace: malformed JSONL entry: %s", e)
        return entries

    def recent_events(self, event_type: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return most recent N entries of a given event type."""
        all_entries = self.read_all()
        filtered = [e for e in all_entries if e.get("event") == event_type]
        return filtered[-limit:]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _append(self, entry: dict[str, Any]) -> None:
        """Append a single JSON entry to the trace file, then trim old entries."""
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._trim_old_entries()
        except Exception as e:
            # Non-fatal — SM-3: trace write is a degradation path, not a hard failure
            log.warning("career_trace: failed to write trace entry (non-fatal): %s", e)

    def _trim_old_entries(self) -> None:
        """Remove entries older than _RETENTION_DAYS (O-F8 retention cleanup)."""
        if not self._path.exists():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            kept: list[str] = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("timestamp", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts >= cutoff:
                            kept.append(line)
                    else:
                        kept.append(line)  # Keep entries with no timestamp
                except (json.JSONDecodeError, ValueError):
                    kept.append(line)  # Keep malformed entries (don't silently delete)

            if len(kept) < len(lines):
                log.info(
                    "career_trace: trimmed %d entries older than %d days",
                    len(lines) - len(kept),
                    _RETENTION_DAYS,
                )
                self._path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except Exception as e:
            log.warning("career_trace: retention trim failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Module-level convenience instance
# ---------------------------------------------------------------------------

_default_trace: Optional[CareerTrace] = None


def get_trace() -> CareerTrace:
    """Return the module-level default CareerTrace instance (lazy init)."""
    global _default_trace
    if _default_trace is None:
        _default_trace = CareerTrace()
    return _default_trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
