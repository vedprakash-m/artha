#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/write_guard.py — Net-negative write guard middleware.

Blocks state file writes that would remove more than 20% of the existing
YAML fields, protecting against accidental context-degradation overwrites.

This is the middleware-extracted equivalent of Step 8b (NET-NEGATIVE WRITE
GUARD) in the catch-up workflow.

Ref: specs/deep-agents.md Phase 4 | Artha.core.md Step 8b
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Threshold: block if proposed content removes more than this % of fields
_MAX_LOSS_PCT = 20.0

# Regex that matches non-blank, non-comment YAML field lines (key: value)
_YAML_FIELD_RE = re.compile(r"^\s{0,4}[\w_-]+\s*:", re.MULTILINE)


def _count_yaml_fields(text: str) -> int:
    """Count the number of YAML key-value lines in ``text``.

    Excludes blank lines and comment lines (lines starting with #).
    Only counts lines with a colon at position ≤ 4 indentation levels to
    capture top-level and first-nested fields.
    """
    return len(_YAML_FIELD_RE.findall(text))


class WriteGuardMiddleware:
    """Block writes that reduce YAML field count by more than 20%.

    Files with ``updated_by: bootstrap`` are exempt — bootstrap files
    intentionally contain placeholder data.

    When a write is blocked, the guard emits a structured warning to stderr
    and returns ``None`` to prevent the write.  The caller is responsible
    for surfacing the options (show diff / write anyway / skip) to the user.
    """

    def __init__(self, max_loss_pct: float = _MAX_LOSS_PCT) -> None:
        self._max_loss_pct = max_loss_pct

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
    ) -> str | None:
        """Check field-count delta and block the write if loss exceeds threshold.

        Returns:
            ``proposed_content`` if the write is safe, or ``None`` to block.
        """
        # Exempt: bootstrap files have minimal data by design
        if "updated_by: bootstrap" in (current_content or ""):
            return proposed_content

        # No existing content — always allow (new file creation)
        if not current_content or not current_content.strip():
            return proposed_content

        current_fields = _count_yaml_fields(current_content)
        if current_fields == 0:
            return proposed_content  # Can't calculate loss — pass through

        proposed_fields = _count_yaml_fields(proposed_content)
        loss_pct = (
            (current_fields - proposed_fields) / current_fields * 100.0
        )

        if loss_pct > self._max_loss_pct:
            print(
                f"[write_guard] ⛔ NET-NEGATIVE WRITE BLOCKED — {domain}\n"
                f"  Current: {current_fields} fields | "
                f"Proposed: {proposed_fields} fields | "
                f"Loss: {loss_pct:.1f}%\n"
                f"  This write would remove >{self._max_loss_pct:.0f}% of existing data.\n"
                f"  Options: [show full diff] | [write anyway] | [skip domain this session]",
                file=sys.stderr,
            )
            return None  # Block the write

        return proposed_content

    def after_write(self, domain: str, file_path: Path) -> None:
        pass  # Write guard is a before-write concern only


def count_yaml_fields(text: str) -> int:
    """Public alias for testing and external use."""
    return _count_yaml_fields(text)
