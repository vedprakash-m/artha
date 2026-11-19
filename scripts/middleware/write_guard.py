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
from typing import Any

try:
    from lib.state_frontmatter import FrontmatterError, split_frontmatter
except ImportError:  # legacy callers put scripts/lib directly on sys.path
    from state_frontmatter import FrontmatterError, split_frontmatter

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
    try:
        parts = split_frontmatter(text)
    except FrontmatterError:
        return 0
    if parts is None:
        return 0
    return len(_YAML_FIELD_RE.findall(parts.metadata))


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
        ctx: Any | None = None,
    ) -> str | None:
        """Check field-count delta and block the write if loss exceeds threshold.

        Required frontmatter is always validated. Bootstrap metadata bypasses
        only the net-negative field threshold, not its registered schema.

        Returns:
            ``proposed_content`` if the write is safe, or ``None`` to block.
        """
        try:
            current_parts = split_frontmatter(current_content or "")
            if current_parts is not None:
                import yaml as _yaml
                current_frontmatter = _yaml.safe_load(current_parts.metadata) or {}
            else:
                current_frontmatter = {}
        except Exception:  # noqa: BLE001
            current_frontmatter = {}
        bootstrap = (
            isinstance(current_frontmatter, dict)
            and current_frontmatter.get("updated_by") == "bootstrap"
        )

        # No existing content — require the new file's registered shape.
        if not current_content or not current_content.strip():
            return proposed_content if self._check_schema(domain, proposed_content) else None

        current_fields = _count_yaml_fields(current_content)
        if current_fields == 0:
            return proposed_content if self._check_schema(domain, proposed_content) else None

        if bootstrap:
            return proposed_content if self._check_schema(domain, proposed_content) else None

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

        # Schema check — block if required frontmatter fields are missing
        if self._check_schema(domain, proposed_content) is False:
            return None

        return proposed_content

    def _check_schema(self, domain: str, content: str) -> bool:
        """Validate frontmatter of proposed content against registered schema.

        Returns True if valid (or no schema registered), False if blocked.
        Emits a warning to stderr when required fields are missing.
        """
        valid, detail = _validate_frontmatter(domain, content)
        if not valid:
            print(
                f"[write_guard] ⛔ SCHEMA VALIDATION FAILED — {domain}\n"
                f"  Detail: {detail}\n"
                f"  Add these fields before writing.",
                file=sys.stderr,
            )
            return False
        return True

    def after_write(self, domain: str, file_path: Path) -> None:
        pass  # Write guard is a before-write concern only

    def before_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def after_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def on_error(self, step_name: str, context: dict, error: Exception) -> None:
        pass


def count_yaml_fields(text: str) -> int:
    """Public alias for testing and external use."""
    return _count_yaml_fields(text)


def _validate_frontmatter(domain: str, content: str) -> tuple[bool, str]:
    try:
        parts = split_frontmatter(content)
        if parts is None:
            return False, "missing frontmatter"
        import yaml as _yaml
        frontmatter = _yaml.safe_load(parts.metadata)
        if not isinstance(frontmatter, dict):
            return False, "frontmatter root must be a mapping"
        from lib.state_schema import validate_frontmatter

        filename = domain if domain.endswith(".md") else f"{domain}.md"
        missing = validate_frontmatter(filename, frontmatter)
        return (not missing, f"missing required fields: {missing}" if missing else "")
    except (ImportError, FrontmatterError, ValueError, TypeError) as exc:
        return False, f"frontmatter unavailable: {type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001
        return False, f"frontmatter unavailable: {type(exc).__name__}"
