"""
scripts/schemas/connector_record.py — JSONL connector record schema (DEBT-009)
===============================================================================
Minimal schema for records emitted by pipeline connectors.
All connectors MUST produce records with at least these three fields.

Design: dataclass (not Pydantic) for zero-cost import in hot paths.
Validation is schema-on-write: called after each connector.fetch() returns.

Usage:
    from schemas.connector_record import validate_record, ConnectorRecord

    records = connector.fetch()
    for raw in records:
        try:
            record = validate_record(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("Invalid record skipped: %s", exc)
            validation_errors += 1
            continue
        process(record)

RD-22: Extended raw field validation for email connectors.
       Missing 'subject' or 'body' in email records now raises ValueError
       at the schema boundary instead of producing silent regex misses in
       email_signal_extractor.py.

Calendar records (WP-02, re-artha D-21) are validated against their own
per-type schema: ``start`` is required and ``date_iso`` is derived from it, so
connectors keep their native fields.  The validator has no side effects; the
pipeline counts rejections into the per-run coverage record.

Ref: specs/debt.md DEBT-009
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# DEBT-SCHEMA-001: ISO-8601 date/datetime format gate (YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS][Z])
_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$"
)

# RD-22: Required raw fields per source type — missing required fields raise ValueError
_REQUIRED_RAW_FIELDS: dict[str, list[str]] = {
    "gmail": ["subject", "body"],
    "outlook_email": ["subject", "body"],
    "email": ["subject", "body"],
    "icloud_email": ["subject", "body"],
}

# Calendar sources: records carry native ``start``/``end`` and no ``date_iso``
# (re-artha D-21).  Detected by source name, with a shape fallback so a new
# calendar connector is still validated as a calendar rather than as email.
_CALENDAR_SOURCE_RE = re.compile(r"(^|_)calendar($|_)")


def _is_calendar_record(raw: dict[str, Any], source: Any) -> bool:
    if isinstance(source, str) and _CALENDAR_SOURCE_RE.search(source):
        return True
    return (
        "start" in raw
        and "summary" in raw
        and "subject" not in raw
        and "body" not in raw
        and "date_iso" not in raw
    )


@dataclass(frozen=True)
class ConnectorRecord:
    """Minimal validated connector record.

    Fields:
        id:         Unique record identifier (non-empty string).
        source:     Connector name / source identifier (non-empty string).
        date_iso:   ISO-8601 date or datetime string (e.g. "2026-04-14" or
                    "2026-04-14T09:30:00Z").  Not parsed — stored as-is.
        raw:        Original raw record dict (preserved for downstream use).
    """
    id: str
    source: str
    date_iso: str
    raw: dict[str, Any]


def validate_record(raw: dict[str, Any]) -> ConnectorRecord:
    """Validate a raw connector record dict and return a ConnectorRecord.

    Raises:
        TypeError:  If ``raw`` is not a dict.
        ValueError: If any required field is missing or has wrong type/value.

    All validation errors include the field name and actual value received
    so that the caller can log actionable diagnostics.

    RD-22: For email connectors (source in _REQUIRED_RAW_FIELDS), validates
    that required raw sub-fields (subject, body) are present and non-empty.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"Connector record must be a dict, got {type(raw).__name__!r}")

    # --- id ---
    rec_id = raw.get("id")
    if rec_id is None:
        raise ValueError(f"Connector record missing required field 'id' (keys present: {list(raw.keys())})")
    if not isinstance(rec_id, str):
        raise ValueError(f"Connector record 'id' must be str, got {type(rec_id).__name__!r}: {rec_id!r}")
    if not rec_id.strip():
        raise ValueError(f"Connector record 'id' must be non-empty string, got: {rec_id!r}")

    # --- source ---
    source = raw.get("source")
    if source is None:
        raise ValueError(f"Connector record missing required field 'source' (id={rec_id!r})")
    if not isinstance(source, str):
        raise ValueError(f"Connector record 'source' must be str, got {type(source).__name__!r}: {source!r}")
    if not source.strip():
        raise ValueError(f"Connector record 'source' must be non-empty string, got: {source!r}")

    # --- calendar records: native start/end; date_iso is derived (D-21) ---
    if _is_calendar_record(raw, source):
        start = raw.get("start")
        if (not isinstance(start, str) or not start.strip()) and isinstance(raw.get("date_iso"), str) \
                and _DATE_RE.match(raw["date_iso"].strip()):
            # Legacy shape: a calendar record that already carries a valid date_iso.
            return ConnectorRecord(id=rec_id, source=source, date_iso=raw["date_iso"].strip(), raw=raw)
        if not isinstance(start, str) or not start.strip():
            raise ValueError(
                f"Calendar record missing required field 'start' (id={rec_id!r}, source={source!r})"
            )
        if not _DATE_RE.match(start.strip()):
            raise ValueError(
                f"Calendar record 'start' does not match ISO-8601 format: {start!r} "
                f"(id={rec_id!r}, source={source!r})"
            )
        end = raw.get("end")
        if end not in (None, "") and (not isinstance(end, str) or not _DATE_RE.match(end.strip())):
            raise ValueError(
                f"Calendar record 'end' does not match ISO-8601 format: {end!r} "
                f"(id={rec_id!r}, source={source!r})"
            )
        return ConnectorRecord(id=rec_id, source=source, date_iso=start.strip(), raw=raw)

    # --- date_iso ---
    date_iso = raw.get("date_iso")
    if date_iso is None:
        raise ValueError(
            f"Connector record missing required field 'date_iso' (id={rec_id!r}, source={source!r})"
        )
    if not isinstance(date_iso, str):
        raise ValueError(
            f"Connector record 'date_iso' must be str, got {type(date_iso).__name__!r}: {date_iso!r}"
        )
    if not date_iso.strip():
        raise ValueError(f"Connector record 'date_iso' must be non-empty string, got: {date_iso!r}")
    # DEBT-SCHEMA-001: enforce ISO-8601 format (not just non-empty string)
    if not _DATE_RE.match(date_iso.strip()):
        raise ValueError(
            f"Connector record 'date_iso' does not match ISO-8601 format "
            f"(e.g. '2026-04-14' or '2026-04-14T09:30:00Z'): {date_iso!r}"
        )

    # --- raw sub-field validation (RD-22: email source required fields) ---
    # Email fields (subject, body) are top-level in the connector record dict.
    # Validate only when the source type requires them; non-email sources skip.
    required_raw_fields = _REQUIRED_RAW_FIELDS.get(source, [])
    for field_name in required_raw_fields:
        field_val = raw.get(field_name)
        if field_val is None:
            raise ValueError(
                f"Connector record raw['{field_name}'] missing for source={source!r} "
                f"(id={rec_id!r}). Email extractors rely on this field — "
                f"missing it causes silent regex misses."
            )
        if not isinstance(field_val, str):
            raise ValueError(
                f"Connector record raw['{field_name}'] must be str for source={source!r}, "
                f"got {type(field_val).__name__!r} (id={rec_id!r})"
            )

    return ConnectorRecord(id=rec_id, source=source, date_iso=date_iso, raw=raw)
