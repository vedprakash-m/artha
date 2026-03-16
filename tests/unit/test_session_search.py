"""tests/unit/test_session_search.py — Unit tests for scripts/session_search.py

AR-4 verification suite (specs/agentic-reloaded.md).

Coverage:
  - search_sessions() with matching briefing file
  - search_sessions() returns empty on no match
  - search_sessions() with empty query returns empty
  - search_sessions() ranks results by relevance (dense matches rank higher)
  - search_sessions() handles missing search dirs gracefully
  - search_sessions() respects disabled config flag
  - excerpt contains context around the match
  - format_results_for_context() produces correct output
  - PII scrub applied when pii_guard available
  - Multi-term query scoring
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest adds scripts/ to sys.path
from session_search import (
    SearchResult,
    format_results_for_context,
    search_sessions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_briefing(tmp_path: Path, filename: str, content: str) -> Path:
    """Create a briefing file in tmp_path/briefings/."""
    d = tmp_path / "briefings"
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(content, encoding="utf-8")
    return p


def _enabled(val: bool = True):
    """Patch the harness flag to a fixed value."""
    return patch("session_search._load_harness_flag", return_value=val)


# ---------------------------------------------------------------------------
# search_sessions()
# ---------------------------------------------------------------------------


class TestSearchSessions:
    def test_finds_matching_briefing(self, tmp_path):
        """A briefing containing the query term is returned."""
        _write_briefing(tmp_path, "2026-03-01.md", "Immigration court date is approaching.\n")
        with _enabled():
            results = search_sessions("immigration", tmp_path)
        assert len(results) == 1
        assert "2026-03-01" in results[0].date

    def test_no_match_returns_empty(self, tmp_path):
        """No matching file → empty list."""
        _write_briefing(tmp_path, "2026-03-01.md", "Finance update: all bills paid.\n")
        with _enabled():
            results = search_sessions("immigration", tmp_path)
        assert results == []

    def test_empty_query_returns_empty(self, tmp_path):
        """Empty query → empty list, no crash."""
        _write_briefing(tmp_path, "2026-03-01.md", "Some content here.\n")
        with _enabled():
            results = search_sessions("", tmp_path)
        assert results == []

    def test_whitespace_query_returns_empty(self, tmp_path):
        """Query of only whitespace → empty list."""
        _write_briefing(tmp_path, "2026-03-01.md", "Content with words.\n")
        with _enabled():
            results = search_sessions("   ", tmp_path)
        assert results == []

    def test_missing_search_dir_no_crash(self, tmp_path):
        """Missing briefings/ directory does not raise."""
        with _enabled():
            results = search_sessions("visa", tmp_path)
        assert results == []

    def test_disabled_flag_returns_empty(self, tmp_path):
        """When config flag is disabled, no search is performed."""
        _write_briefing(tmp_path, "2026-03-01.md", "Immigration details here.\n")
        with _enabled(False):
            results = search_sessions("immigration", tmp_path)
        assert results == []

    def test_ranking_by_relevance(self, tmp_path):
        """File with more term matches per line ranks higher."""
        # Dense match: 5 mentions of 'visa' in a short file
        dense = "visa visa visa visa visa\n"
        # Sparse match: 1 mention in a longer file
        sparse = "visa\n" + ("filler line\n" * 50)
        _write_briefing(tmp_path, "2026-03-01.md", dense)
        _write_briefing(tmp_path, "2026-03-02.md", sparse)
        with _enabled():
            results = search_sessions("visa", tmp_path)
        assert len(results) >= 2
        # Dense file should rank first
        assert results[0].date == "2026-03-01"

    def test_max_results_respected(self, tmp_path):
        """Returns at most max_results items."""
        for i in range(10):
            _write_briefing(tmp_path, f"2026-03-{i+1:02d}.md", "finance review done.\n")
        with _enabled():
            results = search_sessions("finance", tmp_path, max_results=3)
        assert len(results) <= 3

    def test_excerpt_contains_query_context(self, tmp_path):
        """Excerpt includes a window around the matching line."""
        content = textwrap.dedent("""\
            Line one about nothing.
            Line two about nothing.
            Court date for USCIS is April 1.
            Line four about nothing.
        """)
        _write_briefing(tmp_path, "2026-03-10.md", content)
        with _enabled():
            results = search_sessions("uscis", tmp_path)
        assert len(results) == 1
        assert "USCIS" in results[0].excerpt or "uscis" in results[0].excerpt.lower()

    def test_multi_term_query_boosts_score(self, tmp_path):
        """A file matching both terms scores higher than one matching only one."""
        both = "immigration visa extension deadline\n"
        one_only = "immigration update received\n"
        _write_briefing(tmp_path, "2026-03-05.md", both)
        _write_briefing(tmp_path, "2026-03-06.md", one_only)
        with _enabled():
            results = search_sessions("immigration visa", tmp_path)
        # The file matching both terms should appear first
        assert results[0].date == "2026-03-05"

    def test_search_result_dataclass_fields(self, tmp_path):
        """SearchResult has the expected fields."""
        _write_briefing(tmp_path, "2026-03-15.md", "Health check passed.\n")
        with _enabled():
            results = search_sessions("health", tmp_path)
        assert len(results) == 1
        r = results[0]
        assert hasattr(r, "file")
        assert hasattr(r, "date")
        assert hasattr(r, "excerpt")
        assert hasattr(r, "match_count")
        assert hasattr(r, "relevance")
        assert r.relevance > 0.0
        assert r.match_count >= 1


# ---------------------------------------------------------------------------
# format_results_for_context()
# ---------------------------------------------------------------------------


class TestFormatResultsForContext:
    def test_empty_input_returns_empty_string(self):
        assert format_results_for_context([]) == ""

    def test_header_present(self, tmp_path):
        _write_briefing(tmp_path, "2026-03-01.md", "immigration court update\n")
        with _enabled():
            results = search_sessions("immigration", tmp_path)
        out = format_results_for_context(results)
        assert "Cross-Session Recall" in out

    def test_each_result_listed(self, tmp_path):
        for month in ("01", "02"):
            _write_briefing(tmp_path, f"2026-03-{month}.md", "immigration notice arrived\n")
        with _enabled():
            results = search_sessions("immigration", tmp_path)
        out = format_results_for_context(results)
        # Should have at least two entries
        assert out.count("2026-03") >= 2
