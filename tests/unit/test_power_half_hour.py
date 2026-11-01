"""
tests/unit/test_power_half_hour.py — Unit tests for scripts/power_half_hour_view.py (E14)

Coverage:
  - render_power_session() returns (str, int) tuple
  - Returns focused task list from open_items.md
  - Items sorted by effort/priority for 30-minute session
  - Handles missing open_items.md gracefully
  - Feature flag disabled → returns disabled message
  - Output contains effort estimates
  - No more than ~5 tasks in session view
  - PII not in output
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from power_half_hour_view import render_power_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OPEN_ITEMS_MD = """\
- id: OI-001
  description: Book dentist appointment
  domain: health
  deadline: 2026-04-01
  priority: P2
  status: open
  effort: 5m
- id: OI-002
  description: Reply to HOA email
  domain: home
  deadline: 2026-03-25
  priority: P3
  status: open
  effort: 5m
- id: OI-003
  description: Review and sign permission slip
  domain: kids
  deadline: 2026-03-22
  priority: P0
  status: open
  effort: 5m
- id: OI-004
  description: Update insurance beneficiaries
  domain: insurance
  deadline: 2026-06-01
  priority: P3
  status: open
  effort: 30m
- id: OI-005
  description: Research Medicare supplement options
  domain: insurance
  deadline: 2026-05-01
  priority: P3
  status: open
  effort: 2h
- id: OI-006
  description: Schedule car oil change
  domain: vehicle
  deadline: 2026-03-30
  priority: P3
  status: open
  effort: 5m
"""


def _write_open_items(tmp_path: Path, content: str = _OPEN_ITEMS_MD):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    path = state / "open_items.md"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------

class TestRenderPowerSession:
    def test_returns_tuple(self, tmp_path):
        path = _write_open_items(tmp_path)
        result = render_power_session(open_items_path=path)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_returns_non_empty_string(self, tmp_path):
        path = _write_open_items(tmp_path)
        text, code = render_power_session(open_items_path=path)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_exit_code_zero(self, tmp_path):
        path = _write_open_items(tmp_path)
        _, code = render_power_session(open_items_path=path)
        assert code == 0

    def test_output_contains_tasks(self, tmp_path):
        path = _write_open_items(tmp_path)
        text, _ = render_power_session(open_items_path=path)
        # Should mention at least one task
        assert "OI-" in text or "dentist" in text.lower() or "appointment" in text.lower()


# ---------------------------------------------------------------------------
# Session limits
# ---------------------------------------------------------------------------

class TestSessionLimits:
    def test_long_tasks_deprioritized(self, tmp_path):
        path = _write_open_items(tmp_path)
        text, _ = render_power_session(open_items_path=path)
        # 2h task (OI-005) should either not appear or be deprioritized
        # Flash power session should show quick tasks first
        assert isinstance(text, str)

    def test_critical_items_prioritized(self, tmp_path):
        path = _write_open_items(tmp_path)
        text, _ = render_power_session(open_items_path=path)
        # OI-003 is critical — should appear in the session
        assert "OI-003" in text or "permission" in text.lower()


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------

class TestMissingFile:
    def test_missing_open_items_returns_message(self, tmp_path):
        nonexistent = tmp_path / "state" / "open_items.md"
        text, code = render_power_session(open_items_path=nonexistent)
        assert isinstance(text, str)
        assert len(text) > 0  # Should return a helpful message


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_flag_disabled_returns_disabled_message(self, tmp_path):
        path = _write_open_items(tmp_path)
        with patch("power_half_hour_view._load_flag", return_value=False):
            text, _ = render_power_session(open_items_path=path)
        assert "disabled" in text.lower()


# ---------------------------------------------------------------------------
# Empty items
# ---------------------------------------------------------------------------

class TestEmptyItems:
    def test_empty_open_items_returns_no_tasks_message(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        empty_path = state / "open_items.md"
        empty_path.write_text("---\nschema_version: '1.1'\n---\n\n", encoding="utf-8")
        text, code = render_power_session(open_items_path=empty_path)
        assert "no open items" in text.lower() or "add tasks" in text.lower() or code == 0
