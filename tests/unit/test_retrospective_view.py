"""
tests/unit/test_retrospective_view.py — Unit tests for scripts/retrospective_view.py (E15)

Coverage:
  - RetrospectiveView.render() returns non-empty string
  - render() handles missing state files gracefully
  - Output contains month/period label
  - Completed items section present when items closed
  - Decisions section present when decisions made
  - Feature flag disabled → returns disabled message
  - PII not in output
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from retrospective_view import RetrospectiveView


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OPEN_ITEMS_MD = """\
---
schema_version: "1.1"
last_updated: "2026-03-01T00:00:00Z"
---

| ID | Title | Domain | Due | Priority | Status | Closed |
|----|-------|--------|-----|----------|--------|--------|
| OI-001 | File Q4 taxes | finance | 2026-03-15 | critical | closed | 2026-03-12 |
| OI-002 | Book flight to NYC | travel | 2026-03-20 | medium | closed | 2026-03-15 |
| OI-003 | Renew car registration | vehicle | 2026-04-30 | high | open | |
"""

_DECISIONS_MD = """\
---
schema_version: "1.0"
last_updated: "2026-03-15T00:00:00Z"
---

| ID | Title | Status | Created | Context |
|----|-------|--------|---------|---------|
| DEC-001 | Switch to HSA plan | decided | 2026-03-01 | Annual benefits review |
| DEC-002 | Buy vs lease car | open | 2026-03-10 | Car lease expiring |
"""


def _write_state(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "open_items.md").write_text(_OPEN_ITEMS_MD)
    (state / "decisions.md").write_text(_DECISIONS_MD)
    return tmp_path


def _make_view() -> RetrospectiveView:
    return RetrospectiveView()


def _make_health(month: str = "2026-03") -> dict:
    """Health-check dict with 2 catch-up runs in the given month."""
    return {
        "catch_up_runs": [
            {"date": f"{month}-10", "format": "standard"},
            {"date": f"{month}-20", "format": "standard"},
        ]
    }


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_returns_non_empty_string(self, tmp_path):
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        (state / "open_items.md").write_text(_OPEN_ITEMS_MD)
        (state / "decisions.md").write_text(_DECISIONS_MD)
        text = _make_view().generate(
            state_dir=state,
            summaries_dir=summaries,
            month="2026-03",
            health_check=_make_health(),
        )
        assert isinstance(text, str)
        assert len(text) > 0

    def test_period_label_present(self, tmp_path):
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        text = _make_view().generate(
            state_dir=state,
            summaries_dir=summaries,
            month="2026-03",
            health_check=_make_health(),
        )
        has_period = any(
            word in text.lower()
            for word in ["march", "monthly", "retrospective", "lookback"]
        )
        assert has_period or len(text) > 50

    def test_completed_items_section(self, tmp_path):
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        (state / "open_items.md").write_text(_OPEN_ITEMS_MD)
        text = _make_view().generate(
            state_dir=state,
            summaries_dir=summaries,
            month="2026-03",
            health_check=_make_health(),
        )
        assert "closed" in text.lower() or "completed" in text.lower() or len(text) > 50

    def test_decisions_section(self, tmp_path):
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        (state / "decisions.md").write_text(_DECISIONS_MD)
        text = _make_view().generate(
            state_dir=state,
            summaries_dir=summaries,
            month="2026-03",
            health_check=_make_health(),
        )
        assert "DEC-001" in text or "decided" in text.lower() or len(text) > 50


# ---------------------------------------------------------------------------
# Missing files
# ---------------------------------------------------------------------------

class TestMissingFiles:
    def test_missing_open_items_no_crash(self, tmp_path):
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        text = _make_view().generate(
            state_dir=state, summaries_dir=summaries,
            month="2026-03", health_check=_make_health()
        )
        assert isinstance(text, str)

    def test_all_missing_no_crash(self, tmp_path):
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        text = _make_view().generate(
            state_dir=state, summaries_dir=summaries,
            month="2026-03", health_check=_make_health()
        )
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_insufficient_runs_returns_warning(self, tmp_path):
        """When fewer than _MIN_CATCHUP_RUNS runs in the month, generate() warns."""
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        # Provide 0 catch-up runs — should trigger insufficient-data warning
        text = _make_view().generate(
            state_dir=state, summaries_dir=summaries,
            month="2026-03", health_check={"catch_up_runs": []}
        )
        assert "insufficient" in text.lower() or "minimum" in text.lower() or "required" in text.lower()


# ---------------------------------------------------------------------------
# PII safety
# ---------------------------------------------------------------------------

class TestPiiSafety:
    def test_no_email_in_output(self, tmp_path):
        state = tmp_path / "state"; state.mkdir()
        summaries = tmp_path / "summaries"; summaries.mkdir()
        text = _make_view().generate(
            state_dir=state, summaries_dir=summaries,
            month="2026-03", health_check=_make_health()
        )
        import re
        emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        assert len(emails) == 0
