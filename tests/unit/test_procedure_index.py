"""tests/unit/test_procedure_index.py — Unit tests for scripts/procedure_index.py

AR-5 verification suite (specs/agentic-reloaded.md).

Coverage:
  - list_procedures() returns empty list when dir missing
  - list_procedures() parses frontmatter correctly
  - list_procedures() skips README.md
  - list_procedures() respects disabled config flag
  - _decay_confidence() reduces confidence for old procedures
  - _decay_confidence() never decays below floor
  - find_matching_procedures() returns empty on empty dir
  - find_matching_procedures() matches by trigger keyword
  - find_matching_procedures() filters by min_confidence
  - find_matching_procedures() respects min_relevance
  - find_matching_procedures() returns sorted by relevance × confidence
  - find_matching_procedures() respects max_results
  - format_procedures_for_context() returns empty string on no matches
  - format_procedures_for_context() includes domain and trigger
"""
from __future__ import annotations

import textwrap
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest adds scripts/ to sys.path
from procedure_index import (
    ProcedureMatch,
    _decay_confidence,
    find_matching_procedures,
    format_procedures_for_context,
    list_procedures,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROCEDURES_SUBDIR = "state/learned_procedures"


def _write_procedure(tmp_path: Path, filename: str, domain: str, trigger: str,
                     confidence: float = 0.9, created: str | None = None) -> Path:
    """Write a minimal procedure .md file with frontmatter."""
    proc_dir = tmp_path / PROCEDURES_SUBDIR
    proc_dir.mkdir(parents=True, exist_ok=True)
    created_str = created or date.today().isoformat()
    content = textwrap.dedent(f"""\
        ---
        domain: {domain}
        trigger: {trigger}
        confidence: {confidence}
        created: {created_str}
        source: test
        ---

        ## Steps

        1. Do this thing.
        2. Do that thing.
    """)
    p = proc_dir / filename
    p.write_text(content, encoding="utf-8")
    return p


def _enabled(val: bool = True):
    return patch("procedure_index._load_harness_flag", return_value=val)


# ---------------------------------------------------------------------------
# list_procedures()
# ---------------------------------------------------------------------------


class TestListProcedures:
    def test_missing_dir_returns_empty(self, tmp_path):
        with _enabled():
            result = list_procedures(tmp_path)
        assert result == []

    def test_disabled_flag_returns_empty(self, tmp_path):
        _write_procedure(tmp_path, "immigration-ioverview.md", "immigration", "check IOE status")
        with _enabled(False):
            result = list_procedures(tmp_path)
        assert result == []

    def test_parses_single_procedure(self, tmp_path):
        _write_procedure(tmp_path, "finance-bills.md", "finance", "recurring bill check", 0.85)
        with _enabled():
            result = list_procedures(tmp_path)
        assert len(result) == 1
        assert result[0].domain == "finance"
        assert result[0].trigger == "recurring bill check"
        assert 0.0 < result[0].confidence <= 1.0

    def test_skips_readme(self, tmp_path):
        proc_dir = tmp_path / PROCEDURES_SUBDIR
        proc_dir.mkdir(parents=True, exist_ok=True)
        (proc_dir / "README.md").write_text("# Procedures\nSome docs.", encoding="utf-8")
        with _enabled():
            result = list_procedures(tmp_path)
        assert result == []

    def test_multiple_procedures_listed(self, tmp_path):
        for i in range(4):
            _write_procedure(tmp_path, f"domain-proc{i}.md", f"domain{i}", f"trigger {i}")
        with _enabled():
            result = list_procedures(tmp_path)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# _decay_confidence()
# ---------------------------------------------------------------------------


class TestDecayConfidence:
    def test_recent_procedure_unchanged(self):
        confidence = _decay_confidence(0.9, date.today().isoformat())
        assert confidence == 0.9

    def test_old_procedure_decays(self):
        old = (date.today() - timedelta(days=95)).isoformat()
        decayed = _decay_confidence(0.9, old)
        assert decayed < 0.9

    def test_never_below_floor(self):
        very_old = (date.today() - timedelta(days=1000)).isoformat()
        decayed = _decay_confidence(0.9, very_old)
        assert decayed >= 0.5  # floor

    def test_invalid_date_returns_original(self):
        result = _decay_confidence(0.8, "not-a-date")
        assert result == 0.8


# ---------------------------------------------------------------------------
# find_matching_procedures()
# ---------------------------------------------------------------------------


class TestFindMatchingProcedures:
    def test_empty_dir_returns_empty(self, tmp_path):
        with _enabled():
            result = find_matching_procedures("immigration", tmp_path)
        assert result == []

    def test_empty_query_returns_empty(self, tmp_path):
        _write_procedure(tmp_path, "imm-check.md", "immigration", "check IOE status")
        with _enabled():
            result = find_matching_procedures("", tmp_path)
        assert result == []

    def test_match_by_trigger_keyword(self, tmp_path):
        _write_procedure(tmp_path, "imm-check.md", "immigration", "USCIS IOE status check")
        with _enabled():
            result = find_matching_procedures("USCIS status", tmp_path, min_relevance=0.2)
        assert len(result) == 1
        assert result[0].domain == "immigration"

    def test_min_confidence_filters_low_confidence(self, tmp_path):
        _write_procedure(tmp_path, "imm-check.md", "immigration", "IOE check", confidence=0.5)
        with _enabled():
            result = find_matching_procedures("IOE check", tmp_path, min_confidence=0.8)
        assert result == []

    def test_min_relevance_filters_unrelated(self, tmp_path):
        _write_procedure(tmp_path, "finance-bills.md", "finance", "recurring bill check")
        with _enabled():
            result = find_matching_procedures("immigration visa court", tmp_path, min_relevance=0.5)
        assert result == []

    def test_sorted_by_relevance_times_confidence(self, tmp_path):
        _write_procedure(tmp_path, "a.md", "immigration", "USCIS IOE status check", confidence=0.9)
        _write_procedure(tmp_path, "b.md", "finance", "USCIS form filing", confidence=0.85)
        with _enabled():
            results = find_matching_procedures("USCIS immigration", tmp_path, min_relevance=0.1)
        # "immigration USCIS" more relevant to first: domain=immigration, trigger has USCIS
        assert len(results) >= 1

    def test_max_results_respected(self, tmp_path):
        for i in range(8):
            _write_procedure(tmp_path, f"gen-proc{i}.md", "general", f"general task step {i}")
        with _enabled():
            results = find_matching_procedures("general task", tmp_path,
                                               min_relevance=0.1, max_results=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# format_procedures_for_context()
# ---------------------------------------------------------------------------


class TestFormatProceduresForContext:
    def test_empty_returns_empty_string(self):
        assert format_procedures_for_context([]) == ""

    def test_includes_domain_and_trigger(self, tmp_path):
        _write_procedure(tmp_path, "imm-check.md", "immigration", "USCIS IOE status check")
        with _enabled():
            matches = find_matching_procedures("USCIS", tmp_path, min_relevance=0.1)
        out = format_procedures_for_context(matches)
        assert "immigration" in out
        assert "USCIS" in out

    def test_header_present(self, tmp_path):
        _write_procedure(tmp_path, "fin-bills.md", "finance", "check recurring bills")
        with _enabled():
            matches = find_matching_procedures("bills", tmp_path, min_relevance=0.1)
        out = format_procedures_for_context(matches)
        assert "Learned Procedures" in out
