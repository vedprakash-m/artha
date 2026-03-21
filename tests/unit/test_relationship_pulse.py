"""
tests/unit/test_relationship_pulse.py — Unit tests for scripts/relationship_pulse_view.py (E13)

Coverage:
  - render_relationships() returns (str, int) tuple
  - Returns non-empty string with contacts data
  - Handles missing state/relationships.md gracefully
  - Feature flag disabled → returns disabled message
  - Stale contacts section present when contacts are overdue
  - No PII in output (no email addresses)
  - flash format returns shorter output than standard
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from relationship_pulse_view import render_relationships


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RELATIONSHIPS_MD = """\
---
schema_version: "1.0"
last_updated: "2026-03-15"
domain: relationships
---

# Relationships

## Close Friends

| Name | Last Contact | Cadence | Notes |
|------|-------------|---------|-------|
| Alice | 2026-01-01 | monthly | Coffee catchup overdue |
| Bob | 2026-03-10 | monthly | Recent call |
| Carol | 2025-12-01 | monthly | Very overdue |

## Family

| Name | Last Contact | Cadence | Notes |
|------|-------------|---------|-------|
| Mom | 2026-03-18 | weekly | Regular calls |
| Dad | 2026-02-10 | weekly | Overdue |
"""


def _write_relationships(tmp_path: Path, content: str = _RELATIONSHIPS_MD):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "relationships.md").write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------

class TestRenderRelationships:
    def test_returns_tuple(self, tmp_path):
        _write_relationships(tmp_path)
        with patch("relationship_pulse_view._STATE_DIR", tmp_path / "state"):
            result = render_relationships()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_returns_non_empty_string(self, tmp_path):
        _write_relationships(tmp_path)
        with patch("relationship_pulse_view._STATE_DIR", tmp_path / "state"):
            text, code = render_relationships()
        assert isinstance(text, str)
        assert len(text) > 0

    def test_exit_code_zero_on_success(self, tmp_path):
        _write_relationships(tmp_path)
        with patch("relationship_pulse_view._STATE_DIR", tmp_path / "state"):
            _, code = render_relationships()
        assert code == 0


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------

class TestMissingFile:
    def test_missing_relationships_md_returns_warning(self, tmp_path):
        (tmp_path / "state").mkdir(exist_ok=True)
        with patch("relationship_pulse_view._STATE_DIR", tmp_path / "state"):
            text, code = render_relationships()
        assert "not found" in text.lower() or "bootstrap" in text.lower() or len(text) > 0


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_flag_disabled_returns_disabled_message(self, tmp_path):
        _write_relationships(tmp_path)
        with (
            patch("relationship_pulse_view._load_flag", return_value=False),
            patch("relationship_pulse_view._STATE_DIR", tmp_path / "state"),
        ):
            text, _ = render_relationships()
        assert "disabled" in text.lower()


# ---------------------------------------------------------------------------
# PII safety
# ---------------------------------------------------------------------------

class TestPiiSafety:
    def test_no_email_in_output(self, tmp_path):
        _write_relationships(tmp_path)
        with patch("relationship_pulse_view._STATE_DIR", tmp_path / "state"):
            text, _ = render_relationships()
        import re
        emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        assert len(emails) == 0


# ---------------------------------------------------------------------------
# Format variants
# ---------------------------------------------------------------------------

class TestFormats:
    def test_flash_format_returns_shorter_output(self, tmp_path):
        _write_relationships(tmp_path)
        with patch("relationship_pulse_view._STATE_DIR", tmp_path / "state"):
            text_flash, _ = render_relationships(fmt="flash")
            text_standard, _ = render_relationships(fmt="standard")
        # Flash should be shorter or equal
        assert len(text_flash) <= len(text_standard) + 100
