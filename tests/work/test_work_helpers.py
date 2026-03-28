"""tests/work/test_work_helpers.py — Focused tests for scripts/work/helpers.py

T3-1..5 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.helpers import (
    _parse_dt, _age_str, _age_hours,
    _read_frontmatter, _read_body, _extract_section,
    _staleness_header, _freshness_footer, _load_profile,
    _WORK_STATE_DIR, _DEFAULT_STALENESS_HOURS,
)


# ---------------------------------------------------------------------------
# T3-1: _parse_dt edge cases
# ---------------------------------------------------------------------------

class TestParseDt:
    def test_iso_with_offset(self):
        dt = _parse_dt("2026-03-24T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_z_suffix(self):
        dt = _parse_dt("2026-03-24T10:00:00Z")
        assert dt is not None
        assert dt.year == 2026

    def test_none_input(self):
        assert _parse_dt(None) is None

    def test_empty_string(self):
        assert _parse_dt("") is None

    def test_invalid_format_returns_none(self):
        assert _parse_dt("not-a-date") is None

    def test_preserves_timezone(self):
        dt = _parse_dt("2026-03-24T08:30:00-05:00")
        assert dt is not None
        # Convert to UTC — should be 13:30
        utc = dt.astimezone(timezone.utc)
        assert utc.hour == 13


# ---------------------------------------------------------------------------
# T3-2: _read_frontmatter missing file
# ---------------------------------------------------------------------------

class TestReadFrontmatter:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = _read_frontmatter(tmp_path / "nonexistent.md")
        assert result == {}

    def test_reads_valid_frontmatter(self, tmp_path):
        p = tmp_path / "test.md"
        p.write_text("---\ndomain: health\nlast_updated: 2026-03-24\n---\n\nbody text", encoding="utf-8")
        result = _read_frontmatter(p)
        assert result["domain"] == "health"
        assert "last_updated" in result

    def test_no_frontmatter_returns_empty(self, tmp_path):
        p = tmp_path / "plain.md"
        p.write_text("Just plain text, no frontmatter.", encoding="utf-8")
        result = _read_frontmatter(p)
        assert result == {}

    def test_malformed_frontmatter_safe(self, tmp_path):
        p = tmp_path / "bad.md"
        p.write_text("---\n: invalid: yaml: :\n---\nbody", encoding="utf-8")
        # Should not raise; returns empty or partial
        result = _read_frontmatter(p)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# T3-3: _read_body
# ---------------------------------------------------------------------------

class TestReadBody:
    def test_missing_file_returns_empty(self, tmp_path):
        result = _read_body(tmp_path / "nonexistent.md")
        assert result == ""

    def test_strips_frontmatter(self, tmp_path):
        p = tmp_path / "test.md"
        p.write_text("---\ndomain: test\n---\n\nActual body content.", encoding="utf-8")
        body = _read_body(p)
        assert "Actual body content." in body
        assert "domain" not in body

    def test_no_frontmatter_returns_full_content(self, tmp_path):
        p = tmp_path / "plain.md"
        p.write_text("Just plain text.", encoding="utf-8")
        body = _read_body(p)
        assert "Just plain text." in body


# ---------------------------------------------------------------------------
# T3-4: _freshness_footer multi-domain
# ---------------------------------------------------------------------------

class TestFreshnessFooter:
    def _write_domain(self, state_dir: Path, domain: str) -> None:
        """Write a state file with last_updated so _freshness_footer can pick it up."""
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        p = state_dir / f"{domain}.md"
        p.write_text(f"---\nlast_updated: {ts}\n---\n\nBody.\n", encoding="utf-8")

    def test_single_domain(self, tmp_path):
        import work.helpers
        orig = work.helpers._WORK_STATE_DIR
        work.helpers._WORK_STATE_DIR = tmp_path
        self._write_domain(tmp_path, "work-calendar")
        try:
            footer = _freshness_footer(["work-calendar"])
            # domain.split('-')[1] = "calendar" — check for that token
            assert "calendar" in footer
        finally:
            work.helpers._WORK_STATE_DIR = orig

    def test_multi_domain_all_included(self, tmp_path):
        import work.helpers
        orig = work.helpers._WORK_STATE_DIR
        work.helpers._WORK_STATE_DIR = tmp_path
        for domain in ["work-calendar", "work-projects", "work-people"]:
            self._write_domain(tmp_path, domain)
        try:
            footer = _freshness_footer(["work-calendar", "work-projects", "work-people"])
            assert "calendar" in footer
            assert "projects" in footer
        finally:
            work.helpers._WORK_STATE_DIR = orig

    def test_empty_domain_list(self, tmp_path):
        import work.helpers
        orig = work.helpers._WORK_STATE_DIR
        work.helpers._WORK_STATE_DIR = tmp_path
        try:
            footer = _freshness_footer([])
            assert isinstance(footer, str)
        finally:
            work.helpers._WORK_STATE_DIR = orig


# ---------------------------------------------------------------------------
# T3-5: _extract_section
# ---------------------------------------------------------------------------

class TestExtractSection:
    def test_extracts_section_content(self):
        body = "## Alpha\nContent A\n## Beta\nContent B\n"
        result = _extract_section(body, "Alpha")
        assert "Content A" in result
        assert "Content B" not in result

    def test_missing_section_returns_empty(self):
        body = "## Alpha\nContent A\n"
        result = _extract_section(body, "Gamma")
        assert result == ""

    def test_section_at_end_of_file(self):
        body = "## Alpha\nLine 1\n## Beta\nLast section body\n"
        result = _extract_section(body, "Beta")
        assert "Last section body" in result
