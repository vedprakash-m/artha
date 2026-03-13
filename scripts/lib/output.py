# pii-guard: ignore-file — utility code only; no personal data
"""
scripts/lib/output.py — JSONL output formatting with consistent field ordering
and body truncation.

Provides a single emit_jsonl() function and shared truncation constants so that
all fetch scripts produce identical output schemas.

Usage:
    from scripts.lib.output import emit_jsonl, truncate_body

    emit_jsonl({"id": msg_id, "date": iso_date, "subject": subj, "body": body})

Ref: remediation.md §6.5, standardization.md §7.5.3
"""
from __future__ import annotations

import json
import sys
from io import TextIOWrapper
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BODY_LENGTH: int = 8_000
"""Maximum plain-text body characters kept per message/event."""

MAX_DESCRIPTION_LENGTH: int = 500
"""Maximum characters for short description fields (calendar events, etc.)."""

MAX_SUBJECT_LENGTH: int = 500
"""Subjects/titles are rarely longer but guard against pathological inputs."""

# Canonical field order for JSONL output — consistent across all fetch scripts.
# Fields not in this list are appended alphabetically after the ordered ones.
_FIELD_ORDER: list[str] = [
    "id",
    "thread_id",
    "date",
    "from",
    "to",
    "cc",
    "subject",
    "labels",
    "folder",
    "body",
    "snippet",
    # Calendar-specific
    "title",
    "start",
    "end",
    "all_day",
    "location",
    "description",
    "organizer",
    "attendees",
    "calendar",
    "status",
    "meeting_url",
    "recurring",
]

_FIELD_ORDER_INDEX: dict[str, int] = {f: i for i, f in enumerate(_FIELD_ORDER)}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def truncate_body(text: str, max_length: int = MAX_BODY_LENGTH) -> str:
    """Truncate *text* to *max_length* characters, appending an ellipsis marker.

    Args:
        text:       Input string (email body, note content, etc.)
        max_length: Maximum character count to retain.

    Returns:
        Original text if short enough; otherwise text[:max_length] + " [truncated]"
    """
    if not text or len(text) <= max_length:
        return text
    return text[:max_length] + " [truncated]"


def truncate_field(text: str, max_length: int = MAX_DESCRIPTION_LENGTH) -> str:
    """Like truncate_body but defaults to the shorter description limit."""
    return truncate_body(text, max_length)


def _sort_key(field_name: str) -> tuple[int, str]:
    """Sort key: ordered fields first (by index), remainder alphabetically."""
    idx = _FIELD_ORDER_INDEX.get(field_name)
    if idx is not None:
        return (0, str(idx).zfill(4))
    return (1, field_name)


def _apply_truncations(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *record* with long string fields trimmed."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, str):
            if key in ("body",):
                value = truncate_body(value, MAX_BODY_LENGTH)
            elif key in ("description",):
                value = truncate_field(value, MAX_DESCRIPTION_LENGTH)
            elif key in ("subject", "title"):
                value = truncate_field(value, MAX_SUBJECT_LENGTH)
        out[key] = value
    return out


def emit_jsonl(
    record: dict[str, Any],
    *,
    stream: Optional[TextIOWrapper] = None,
    truncate: bool = True,
) -> None:
    """Serialise *record* to a single JSONL line and write to *stream*.

    Field order follows the canonical _FIELD_ORDER list; any extra fields
    are appended alphabetically.

    Args:
        record:   Dict of fields to serialise. Values must be JSON-serialisable.
        stream:   Output stream (default: sys.stdout).
        truncate: If True (default), apply body/description truncation before
                  serialisation to keep output within token-friendly limits.

    Raises:
        TypeError: If any value in *record* is not JSON-serialisable.
    """
    if stream is None:
        stream = sys.stdout

    if truncate:
        record = _apply_truncations(record)

    # Re-order dict by canonical field order
    ordered: dict[str, Any] = dict(
        sorted(record.items(), key=lambda kv: _sort_key(kv[0]))
    )

    stream.write(json.dumps(ordered, ensure_ascii=False, default=str) + "\n")
    stream.flush()
