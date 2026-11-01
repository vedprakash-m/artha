#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/write_verify.py — Post-write integrity verification middleware.

After every state file write, verifies that the file:
  1. Exists and is non-empty (> 100 bytes)
  2. Starts with ``---`` (valid YAML frontmatter delimiter)
  3. Contains a ``domain:`` field in the frontmatter
  4. Contains a ``last_updated:`` field with a valid ISO-8601 timestamp

If any check fails, logs the failure and emits a warning.  The encrypted
vault step (Step 18) skips failed files.

This is the middleware-extracted equivalent of Step 8c in the catch-up
workflow.

Ref: specs/deep-agents.md Phase 4 | Artha.core.md Step 8c
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MIN_SIZE_BYTES = 100

# Matches ISO-8601 datetime or date strings (loose check)
_ISO8601_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"  # date part
    r"(T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?",  # optional time
)


def _check_file(domain: str, file_path: Path) -> list[str]:
    """Run all integrity checks on ``file_path``.

    Returns:
        List of failure reason strings.  Empty list = all checks pass.
    """
    failures: list[str] = []

    # Check 1: file exists and is non-empty
    if not file_path.exists():
        failures.append("file_does_not_exist")
        return failures  # No point checking further

    if file_path.stat().st_size < _MIN_SIZE_BYTES:
        failures.append(f"file_too_small ({file_path.stat().st_size} bytes < {_MIN_SIZE_BYTES})")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        failures.append(f"unreadable: {exc}")
        return failures

    # Check 2: valid YAML frontmatter delimiter
    if not content.startswith("---"):
        failures.append("missing_frontmatter_delimiter")
        return failures  # Subsequent checks require frontmatter

    # Extract frontmatter
    end = content.find("\n---", 3)
    frontmatter = content[3:end].strip() if end != -1 else content[3:].strip()

    # Check 3: domain: field present
    if not re.search(r"^\s*domain\s*:", frontmatter, re.MULTILINE):
        failures.append("missing_domain_field")

    # Check 4: last_updated: field present with valid ISO-8601 value
    lu_match = re.search(r"^\s*last_updated\s*:\s*(.+)$", frontmatter, re.MULTILINE)
    if not lu_match:
        failures.append("missing_last_updated_field")
    else:
        ts_value = lu_match.group(1).strip().strip("'\"")
        if not _ISO8601_RE.match(ts_value):
            failures.append(f"invalid_last_updated_timestamp: '{ts_value}'")

    return failures


class WriteVerifyMiddleware:
    """Run post-write integrity checks after each state file write.

    Failures are logged to stderr and optionally to ``state/audit.md``.
    The middleware never raises an exception — it logs and continues.
    """

    def __init__(self, artha_dir: Path | None = None) -> None:
        from lib.common import ARTHA_DIR  # noqa: PLC0415

        self._audit_log = (artha_dir or ARTHA_DIR) / "state" / "audit.md"

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
        ctx: Any | None = None,
    ) -> str | None:
        return proposed_content  # Verification happens post-write

    def after_write(self, domain: str, file_path: Path) -> None:
        """Verify file integrity after the write completes."""
        failures = _check_file(domain, file_path)

        if not failures:
            return  # All good

        reason = ", ".join(failures)
        print(
            f"[write_verify] ⚠ INTEGRITY_VERIFY_FAIL | file: {file_path.name} "
            f"| checks: {reason} | layer: 2",
            file=sys.stderr,
        )

        self._log_audit(domain, file_path, reason)

    def _log_audit(self, domain: str, file_path: Path, reason: str) -> None:
        """Append a single-line entry to state/audit.md."""
        try:
            ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            line = (
                f"[{ts}] INTEGRITY_VERIFY_FAIL | file: {file_path.name} "
                f"| checks: {reason} | layer: 2\n"
            )
            with open(self._audit_log, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # Audit log unavailable — don't fail on failure-to-log

    def before_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def after_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def on_error(self, step_name: str, context: dict, error: Exception) -> None:
        pass


def verify_file(domain: str, file_path: Path) -> list[str]:
    """Public utility for testing: run integrity checks and return failures."""
    return _check_file(domain, file_path)
