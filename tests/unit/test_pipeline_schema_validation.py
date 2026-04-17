"""
tests/unit/test_pipeline_schema_validation.py
===============================================
DEBT-009: Verify connector record schema validation.
Tests the validate_record() function directly (fast, no pipeline required).
"""
from __future__ import annotations

import os
import sys

import pytest

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from schemas.connector_record import validate_record, ConnectorRecord


class TestConnectorRecordSchema:
    """A1–A3 from DEBT-009."""

    def test_valid_record_passes(self):
        """A1: A record with all required fields passes validation.

        RD-22: gmail source requires both subject and body top-level fields.
        """
        raw = {
            "id": "r1", "source": "gmail", "date_iso": "2026-04-14",
            "subject": "Test", "body": "Hello world",
        }
        rec = validate_record(raw)
        assert isinstance(rec, ConnectorRecord)
        assert rec.id == "r1"
        assert rec.source == "gmail"
        assert rec.date_iso == "2026-04-14"
        assert rec.raw == raw

    def test_missing_id_raises_value_error(self):
        """A2: Missing 'id' raises ValueError."""
        with pytest.raises(ValueError, match="missing required field 'id'"):
            validate_record({"source": "gmail", "date_iso": "2026-04-14"})

    def test_missing_source_raises_value_error(self):
        """A2: Missing 'source' raises ValueError."""
        with pytest.raises(ValueError, match="missing required field 'source'"):
            validate_record({"id": "x", "date_iso": "2026-04-14"})

    def test_missing_date_iso_raises_value_error(self):
        """A2: Missing 'date_iso' raises ValueError."""
        with pytest.raises(ValueError, match="missing required field 'date_iso'"):
            validate_record({"id": "x", "source": "gmail"})

    def test_non_dict_raises_type_error(self):
        """A2: Non-dict input raises TypeError."""
        with pytest.raises(TypeError, match="must be a dict"):
            validate_record("not a dict")

    def test_empty_id_raises_value_error(self):
        """Empty string 'id' is rejected."""
        with pytest.raises(ValueError, match="non-empty string"):
            validate_record({"id": "", "source": "gmail", "date_iso": "2026-04-14"})

    def test_non_string_id_raises_value_error(self):
        """Non-string 'id' is rejected."""
        with pytest.raises(ValueError, match="must be str"):
            validate_record({"id": 123, "source": "gmail", "date_iso": "2026-04-14"})

    def test_extra_fields_allowed(self):
        """A1: Extra fields beyond required are allowed (additive validation)."""
        raw = {
            "id": "x", "source": "gmail", "date_iso": "2026-04-14",
            "subject": "hello", "body": "world", "labels": ["inbox"],
        }
        rec = validate_record(raw)
        assert rec.raw == raw

    def test_validation_errors_in_pipeline_metrics(self):
        """pipeline_metrics.json schema must include validation_errors field (DEBT-009)."""
        pipeline_path = os.path.join(_SCRIPTS_DIR, "pipeline.py")
        with open(pipeline_path, encoding="utf-8") as f:
            src = f.read()
        assert '"validation_errors"' in src or "'validation_errors'" in src, \
            "DEBT-009: validation_errors field missing from _write_pipeline_metrics"

    def test_schema_validation_available_flag(self):
        """_SCHEMA_VALIDATION_AVAILABLE flag must be present in pipeline.py."""
        pipeline_path = os.path.join(_SCRIPTS_DIR, "pipeline.py")
        with open(pipeline_path, encoding="utf-8") as f:
            src = f.read()
        assert "_SCHEMA_VALIDATION_AVAILABLE" in src, \
            "DEBT-009: _SCHEMA_VALIDATION_AVAILABLE flag missing from pipeline.py"

    def test_connector_record_is_frozen_dataclass(self):
        """ConnectorRecord must be frozen (immutable) to prevent accidental mutation."""
        raw = {"id": "x", "source": "s", "date_iso": "2026-01-01"}
        rec = validate_record(raw)
        with pytest.raises((AttributeError, TypeError)):
            rec.id = "mutated"  # type: ignore[misc]
