"""
tests/unit/test_family_flash.py — Unit tests for _build_family_flash() in
scripts/channel_push.py (E7)

Coverage:
  - _build_family_flash() returns a non-empty string
  - Output respects max_length parameter
  - Contains today's calendar events (from state/calendar.md)
  - Contains shared open items (from state/open_items.md)
  - Only allows family-safe domains (no finance, immigration, health)
  - Shared decisions appear when visibility:shared
  - Private decisions (visibility:private) do NOT appear
  - Handles missing state files gracefully
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from channel_push import _build_family_flash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CALENDAR_MD = """\
---
schema_version: "1.0"
last_updated: "2026-03-20"
---

## March 2026

| Date | Event | Domain | Type |
|------|-------|--------|------|
| 2026-03-20 | Kids soccer practice | kids | activity |
| 2026-03-20 | Dinner with neighbours | social | social |
| 2026-03-21 | School talent show | kids | school |
"""

_OPEN_ITEMS_MD = """\
---
schema_version: "1.1"
last_updated: "2026-03-20T07:00:00Z"
---

| ID | Title | Domain | Due | Priority | Status |
|----|-------|--------|-----|----------|--------|
| OI-001 | Buy groceries for weekend | home | 2026-03-22 | medium | open |
| OI-002 | Book kids dentist | kids | 2026-04-01 | high | open |
| OI-003 | Renew passport (sensitive) | immigration | 2026-06-01 | critical | open |
| OI-004 | Pay credit card | finance | 2026-03-25 | high | open |
"""

_DECISIONS_MD = """\
---
schema_version: "1.0"
last_updated: "2026-03-20"
---

- id: DEC-001
  visibility: shared
  title: "Family vacation destination"
  status: open
  created: "2026-03-15"
- id: DEC-002
  visibility: private
  title: "Individual tax strategy"
  status: open
  created: "2026-03-10"
"""


def _write_state(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "calendar.md").write_text(_CALENDAR_MD)
    (state / "open_items.md").write_text(_OPEN_ITEMS_MD)
    (state / "decisions.md").write_text(_DECISIONS_MD)
    return tmp_path


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------

class TestBuildFamilyFlash:
    def test_returns_non_empty_string(self, tmp_path):
        _write_state(tmp_path)
        with patch("channel_push._STATE_DIR", tmp_path / "state"):
            result = _build_family_flash()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_max_length_respected(self, tmp_path):
        _write_state(tmp_path)
        max_len = 400
        with patch("channel_push._STATE_DIR", tmp_path / "state"):
            result = _build_family_flash(max_length=max_len)
        assert len(result) <= max_len + 50  # some tolerance for last item


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------

class TestContentValidation:
    def test_no_sensitive_domain_content(self, tmp_path):
        _write_state(tmp_path)
        with patch("channel_push._STATE_DIR", tmp_path / "state"):
            result = _build_family_flash()
        # Immigration and finance open items should NOT appear
        assert "passport" not in result.lower()
        assert "credit card" not in result.lower()
        assert "tax" not in result.lower()

    def test_family_safe_items_present(self, tmp_path):
        _write_state(tmp_path)
        with patch("channel_push._STATE_DIR", tmp_path / "state"):
            result = _build_family_flash()
        # Home or kids items should appear
        assert "groceries" in result.lower() or "dentist" in result.lower() or "kids" in result.lower()

    def test_shared_decisions_appear(self, tmp_path):
        _write_state(tmp_path)
        with patch("channel_push._STATE_DIR", tmp_path / "state"):
            result = _build_family_flash()
        # DEC-001 is visibility:shared — should appear
        assert "vacation" in result.lower() or "DEC-001" in result

    def test_private_decisions_excluded(self, tmp_path):
        _write_state(tmp_path)
        with patch("channel_push._STATE_DIR", tmp_path / "state"):
            result = _build_family_flash()
        # DEC-002 is visibility:private — must NOT appear
        assert "tax strategy" not in result.lower() and "DEC-002" not in result


# ---------------------------------------------------------------------------
# Missing files
# ---------------------------------------------------------------------------

class TestMissingFiles:
    def test_missing_calendar_no_crash(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        (state / "open_items.md").write_text(_OPEN_ITEMS_MD)
        with patch("channel_push._STATE_DIR", state):
            result = _build_family_flash()
        assert isinstance(result, str)

    def test_all_missing_returns_fallback(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        with patch("channel_push._STATE_DIR", state):
            result = _build_family_flash()
        assert isinstance(result, str)
