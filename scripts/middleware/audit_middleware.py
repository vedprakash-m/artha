#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/audit_middleware.py — Audit trail middleware.

Appends structured audit log entries to ``state/audit.md`` for every state
file write, providing an immutable record of all mutations.

This consolidates the scattered audit log calls previously embedded as inline
steps in Artha.core.md (Steps 17, 8b, 8c, etc.) into a single reusable
middleware.

Ref: specs/deep-agents.md Phase 4 | Artha.core.md Step 17
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditMiddleware:
    """Write a structured entry to state/audit.md for every state mutation.

    Entry format (matches existing Artha audit.md convention):
        [YYYY-MM-DDTHH:MM:SSZ] MIDDLEWARE_WRITE | domain: {domain} | \\
            pii_redacted: ? | guard_passed: true | verify_pending: true

    The ``pii_redacted`` field is populated heuristically: if the content
    changed between ``current_content`` and the approved ``proposed_content``,
    we assume PII was redacted.  For precise counts, defer to the PII guard's
    own stderr output.
    """

    def __init__(self, artha_dir: Path | None = None) -> None:
        from lib.common import ARTHA_DIR  # noqa: PLC0415

        self._audit_log = (artha_dir or ARTHA_DIR) / "state" / "audit.md"
        # Track per-domain state for before→after correlation
        self._pending: dict[str, dict[str, Any]] = {}

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
    ) -> str | None:
        """Record pre-write state for post-write correlation."""
        pii_may_have_been_redacted = proposed_content != current_content
        self._pending[domain] = {
            "pii_flag": pii_may_have_been_redacted,
            "current_len": len(current_content),
            "proposed_len": len(proposed_content),
        }
        return proposed_content

    def after_write(self, domain: str, file_path: Path) -> None:
        """Emit the audit log entry after a successful write."""
        pending = self._pending.pop(domain, {})
        pii_flag = pending.get("pii_flag", False)

        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = (
            f"[{ts}] MIDDLEWARE_WRITE | domain: {domain} "
            f"| file: {file_path.name} "
            f"| pii_redacted: {int(pii_flag)} "
            f"| guard_passed: true "
            f"| verify_passed: pending\n"
        )
        self._append(line)

    def log_event(self, event_type: str, details: str) -> None:
        """Log an arbitrary event to audit.md.

        Args:
            event_type: Uppercase event label (e.g. "CONTEXT_OFFLOAD").
            details: Key=value detail string.
        """
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._append(f"[{ts}] {event_type} | {details}\n")

    def _append(self, line: str) -> None:
        """Append a single line to the audit log, silently ignoring errors."""
        try:
            with open(self._audit_log, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
