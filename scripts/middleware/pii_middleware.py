#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/pii_middleware.py — PII redaction middleware.

Intercepts state file writes and runs the Artha PII guard before persisting
any content.  This is the middleware-extracted equivalent of Step 5b in the
catch-up workflow.

Ref: specs/deep-agents.md Phase 4 | Artha.core.md Step 5b
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any
from pathlib import Path

from lib.common import ARTHA_DIR


class PIIMiddleware:
    """Run pii_guard.py filter on proposed content before any state write.

    If PII is detected:
    - The filtered (redacted) content is returned in place of the original.
    - A warning is emitted to stderr.
    - The event is NOT logged here — AuditMiddleware handles audit trail.

    The middleware never blocks a write due to PII presence alone: it
    redacts and continues.  This mirrors the existing Step 5b behaviour.
    """

    def __init__(self, artha_dir: Path | None = None) -> None:
        self._artha_dir = artha_dir or ARTHA_DIR
        self._pii_script = self._artha_dir / "scripts" / "pii_guard.py"

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
        ctx: Any | None = None,
    ) -> str | None:
        """Run PII filter on proposed_content.  Returns filtered content."""
        if not self._pii_script.exists():
            return proposed_content  # guard unavailable — pass through

        try:
            result = subprocess.run(
                [sys.executable, str(self._pii_script), "filter"],
                input=proposed_content.encode("utf-8"),
                capture_output=True,
                timeout=10,
            )
            stdout_text = result.stdout.decode("utf-8") if result.stdout else ""
            stderr_text = result.stderr.decode("utf-8") if result.stderr else ""
            if result.returncode == 1:
                # PII was found and filtered — return the filtered stdout
                print(
                    f"[pii_middleware] \u26a0 PII detected in {domain} write \u2014 "
                    f"redaction applied: {stderr_text.strip()}",
                    file=sys.stderr,
                )
                return stdout_text if stdout_text else proposed_content
            # Exit 0 = no PII found; stdout is the clean text
            return stdout_text if stdout_text.strip() else proposed_content
        except Exception as exc:  # noqa: BLE001
            print(
                f"[pii_middleware] ⚠ PII guard failed for {domain}: {exc} — write passes through",
                file=sys.stderr,
            )
            return proposed_content

    def after_write(self, domain: str, file_path: Path) -> None:
        pass  # PII guard is a before-write concern only

    def before_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def after_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def on_error(self, step_name: str, context: dict, error: Exception) -> None:
        pass
