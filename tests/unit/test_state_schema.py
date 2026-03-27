"""tests/unit/test_state_schema.py — T7-1..6: state_schema + WriteGuardMiddleware tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
_LIB_DIR     = _SCRIPTS_DIR / "lib"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR), str(_LIB_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from state_schema import validate_frontmatter  # type: ignore[import]


# ---------------------------------------------------------------------------
# T7-1: valid frontmatter returns empty list
# ---------------------------------------------------------------------------

def test_t7_1_valid_frontmatter_returns_empty_list():
    """validate_frontmatter with all required fields returns []."""
    fm = {"schema_version": 1, "domain": "health", "last_updated": "2026-03-26"}
    missing = validate_frontmatter("health-check.md", fm)
    assert missing == []


# ---------------------------------------------------------------------------
# T7-2: empty frontmatter returns all required fields
# ---------------------------------------------------------------------------

def test_t7_2_empty_frontmatter_returns_all_required():
    """validate_frontmatter({}) returns all required fields for health-check.md."""
    missing = validate_frontmatter("health-check.md", {})
    assert "schema_version" in missing
    assert "domain" in missing
    assert "last_updated" in missing


# ---------------------------------------------------------------------------
# T7-3: unknown filename returns empty list (no validation)
# ---------------------------------------------------------------------------

def test_t7_3_unknown_filename_returns_empty():
    """validate_frontmatter for an unknown file always returns []."""
    missing = validate_frontmatter("some_random_file.md", {})
    assert missing == []

    missing2 = validate_frontmatter("work_briefing.md", {"x": 1})
    assert missing2 == []


# ---------------------------------------------------------------------------
# T7-4: partial fields returns only missing ones
# ---------------------------------------------------------------------------

def test_t7_4_partial_fields_returns_only_missing():
    """validate_frontmatter returns ONLY the missing fields, not the present ones."""
    fm = {"schema_version": 1, "domain": "goals"}  # missing last_updated
    missing = validate_frontmatter("goals.md", fm)
    assert "last_updated" in missing
    assert "schema_version" not in missing
    assert "domain" not in missing


# ---------------------------------------------------------------------------
# T7-5: WriteGuardMiddleware blocks write when required fields missing
# ---------------------------------------------------------------------------

def test_t7_5_write_guard_blocks_missing_schema_fields(capsys):
    """WriteGuardMiddleware.before_write returns None when frontmatter is incomplete."""
    sys.path.insert(0, str(_SCRIPTS_DIR / "middleware"))
    from write_guard import WriteGuardMiddleware

    guard = WriteGuardMiddleware()

    current = """---
schema_version: 1
domain: health
last_updated: 2026-01-01
---
# Health\n\nSome content with many fields to not trigger loss threshold:\n""" + "\n".join(
        [f"field_{i}: value_{i}" for i in range(30)]
    )

    proposed = """---
domain: health
---
# Health\n\nUpdated content""" + "\n".join(
        [f"field_{i}: value_{i}" for i in range(30)]
    )

    result = guard.before_write("health-check", current, proposed)
    assert result is None
    captured = capsys.readouterr()
    assert "SCHEMA VALIDATION FAILED" in captured.err or result is None


# ---------------------------------------------------------------------------
# T7-6: WriteGuardMiddleware allows write when all required fields present
# ---------------------------------------------------------------------------

def test_t7_6_write_guard_allows_valid_schema():
    """WriteGuardMiddleware.before_write returns proposed_content when schema is valid."""
    sys.path.insert(0, str(_SCRIPTS_DIR / "middleware"))
    from write_guard import WriteGuardMiddleware

    guard = WriteGuardMiddleware()

    base = """---
schema_version: 1
domain: health
last_updated: 2026-03-26
extra_field: some_value
another: value
yet_another: 42
---
# Content
""" + "\n".join([f"line_{i}: value" for i in range(25)])

    result = guard.before_write("health-check", base, base)
    assert result == base
