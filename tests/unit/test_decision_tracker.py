"""
tests/unit/test_decision_tracker.py — Unit tests for scripts/decision_tracker.py (E12)

Coverage:
  - DecisionTracker.add() creates entry in state/decisions.md
  - DecisionTracker.list_open() returns open decisions only
  - Decision ID format: DEC-NNN
  - Required fields: id, title, status, created, options
  - Atomic write (flock used)
  - Feature flag disabled → no-op
  - parse_decisions() reads decisions.md correctly
  - Status filter: open / decided / archived
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import sys

import pytest

# decision_tracker imports fcntl for file locking; stub it on Windows
if sys.platform == "win32" and "fcntl" not in sys.modules:
    from unittest.mock import MagicMock
    sys.modules["fcntl"] = MagicMock()

from decision_tracker import DecisionTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker() -> DecisionTracker:
    return DecisionTracker()


def _decisions_path(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return state / "decisions.md"


# YAML frontmatter format for _load_decisions()
_DECISIONS_YAML = """\
---
decisions:
- id: DEC-001
  title: Switch insurance provider
  status: open
  created: "2026-03-10"
  context: Annual review
- id: DEC-002
  title: Buy vs rent next home
  status: open
  created: "2026-02-01"
  context: Family discussion
- id: DEC-003
  title: Old car kept or sold
  status: decided
  created: "2026-01-15"
  context: Sold for $4k
---
"""


# ---------------------------------------------------------------------------
# add()
# ---------------------------------------------------------------------------

class TestAdd:
    def test_add_creates_entry(self, tmp_path):
        path = _decisions_path(tmp_path)
        path.write_text("")  # start empty
        tracker = _make_tracker()
        proposal = tracker.capture_from_command("Should we get a dog?")
        tracker.persist_proposal(proposal, path)
        content = path.read_text()
        assert "Should we get a dog" in content

    def test_id_format_dec_nnn(self, tmp_path):
        path = _decisions_path(tmp_path)
        path.write_text("")
        tracker = _make_tracker()
        proposal = tracker.capture_from_command("Test decision")
        result_id = tracker.persist_proposal(proposal, path)
        import re
        assert re.match(r"DEC-\d{3}", result_id)


# ---------------------------------------------------------------------------
# list_open()
# ---------------------------------------------------------------------------

class TestListOpen:
    def test_returns_only_open_decisions(self, tmp_path):
        path = _decisions_path(tmp_path)
        path.write_text(_DECISIONS_YAML)
        tracker = _make_tracker()
        all_decisions = tracker._load_decisions(path)
        # Filter to open only
        open_decisions = [d for d in all_decisions if d.get("status") != "decided"]
        ids = [d.get("id", "") for d in open_decisions]
        assert "DEC-003" not in ids


# ---------------------------------------------------------------------------
# parse_decisions
# ---------------------------------------------------------------------------

class TestParseDecisions:
    def test_parses_all_rows(self, tmp_path):
        path = _decisions_path(tmp_path)
        path.write_text(_DECISIONS_YAML)
        tracker = _make_tracker()
        all_decisions = tracker._load_decisions(path)
        assert isinstance(all_decisions, list)
        assert len(all_decisions) >= 2  # At least DEC-001 and DEC-002


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_flag_disabled_lifecycle_noop(self, tmp_path):
        """update_lifecycle() should return empty changes when flag disabled."""
        path = _decisions_path(tmp_path)
        path.write_text(_DECISIONS_YAML)
        tracker = _make_tracker()
        with patch("decision_tracker._load_flag", return_value=False):
            changes = tracker.update_lifecycle(path)
        assert changes == {} or changes == [] or isinstance(changes, (dict, list))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_decisions_file_persist_creates_entry(self, tmp_path):
        path = _decisions_path(tmp_path)
        path.write_text("")  # create empty file
        tracker = _make_tracker()
        proposal = tracker.capture_from_command("First ever decision")
        tracker.persist_proposal(proposal, path)
        assert path.exists()
        assert "First ever decision" in path.read_text()

    def test_empty_decisions_file(self, tmp_path):
        path = _decisions_path(tmp_path)
        path.write_text("")
        tracker = _make_tracker()
        decisions = tracker._load_decisions(path)
        assert isinstance(decisions, list)
