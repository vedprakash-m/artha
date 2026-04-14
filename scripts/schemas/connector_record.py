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

Ref: specs/debt.md DEBT-009
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    return ConnectorRecord(id=rec_id, source=source, date_iso=date_iso, raw=raw)
