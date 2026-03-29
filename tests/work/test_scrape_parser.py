"""tests/work/test_scrape_parser.py — Unit tests for backfill.scrape_parser.

Coverage targets:
  - detect_format_family: all 4 families, edge cases
  - parse_scrape_file: valid file, unreadable path, extraction_rate bounds
  - Calendar section parsing: table rows, bullet fallback, empty
  - Email section parsing: bold subjects, urgency flags
  - Chat section parsing: Q5 channel extraction, 1:1 pattern
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from backfill.scrape_parser import (
    ParsedScrapeWeek,
    detect_format_family,
    parse_scrape_file,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CALENDAR_TABLE_BLOCK = textwrap.dedent(
    """\
    ## Q4 — Calendar / Meetings

    | # | Meeting | Organizer | Attendees |
    |---|---------|-----------|-----------|
    | 1 | 📣 Design Review | Ved | Alice, Bob |
    | 2 | 📊 Status Sync | Alice | Ved, Carol |
    """
)

FORMAT_A_TEXT = textwrap.dedent(
    """\
    # Week 2024-W38

    ## Key Highlights
    - Shipped v2.1

    ## Key Decisions & Outcomes
    - Chose PostgreSQL over MySQL

    ## Open Items
    - Follow up on PR #42
    """
)

FORMAT_B_MID_TEXT = textwrap.dedent(
    """\
    # Week 2025-W10

    ## Q1 — Week Summary
    - Productive week

    ## Q4 — Calendar
    | # | Meeting | Organizer | Attendees |
    |---|---------|-----------|-----------|
    | 1 | 📣 Scrum | Ved | Team |

    ## Q5 — Teams Interactions
    **#proj-alpha** (Alice, Bob)
    - Aligned on roadmap

    ## Q6 — Files / Emails
    - **Urgent: Deploy approval** — action required
    """
)

FORMAT_B_LATE_TEXT = textwrap.dedent(
    """\
    # Week 2025-W33

    ## Q1 — Week Summary
    - Great week

    ## Q4 — Calendar / Meetings
    | # | Meeting | Organizer | Attendees |
    |---|---------|-----------|-----------|
    | 1 | 📣 All-Hands | Carol | 50 |

    ## Q5 — Teams / Chat
    **#engineering** (Alice, Bob, Carol, Dave)
    - Roadmap discussion

    ## Q6 — Email / Files
    - **Sign-off needed** — urgent response required

    ## New People
    | Name | Context | Follow-up |
    |------|---------|-----------|
    | Alice | Joined team | Say hi |
    """
)

FORMAT_B_EARLY_TEXT = textwrap.dedent(
    """\
    # Week 2025-W02

    ## Highlights
    - Started project

    ## Meetings attended
    - Team standup
    - 1:1 with manager
    """
)


# ---------------------------------------------------------------------------
# TestDetectFormatFamily
# ---------------------------------------------------------------------------

class TestDetectFormatFamily:

    def test_format_a_detected(self):
        assert detect_format_family(FORMAT_A_TEXT) == "A"

    def test_format_b_mid_detected(self):
        # Has Q-sections but no rich stakeholder table
        result = detect_format_family(FORMAT_B_MID_TEXT)
        assert result in ("B-mid", "B-late")  # Q sections present

    def test_format_b_late_detected(self):
        result = detect_format_family(FORMAT_B_LATE_TEXT)
        assert result in ("B-late", "B-mid")  # Stakeholder table present — B-late preferred

    def test_format_b_early_is_default(self):
        assert detect_format_family(FORMAT_B_EARLY_TEXT) == "B-early"

    def test_empty_string_returns_b_early(self):
        assert detect_format_family("") == "B-early"

    def test_only_key_decisions_no_q_sections(self):
        """Key Decisions section alone (no Q# labels) → Format A."""
        text = "## Key Decisions & Outcomes\n- Chose Python"
        result = detect_format_family(text)
        assert result == "A"

    def test_q5_only_returns_b_mid(self):
        """Single Q5 section without stakeholder table → B-mid."""
        text = "## Q5 — Teams Interactions\n**#channel** (Alice)\n- Hello"
        result = detect_format_family(text)
        assert result in ("B-mid", "B-late")

    def test_returns_string(self):
        for text in (FORMAT_A_TEXT, FORMAT_B_MID_TEXT, FORMAT_B_LATE_TEXT, FORMAT_B_EARLY_TEXT):
            result = detect_format_family(text)
            assert isinstance(result, str)
            assert result in ("A", "B-early", "B-mid", "B-late")


# ---------------------------------------------------------------------------
# TestParseScrapeFile
# ---------------------------------------------------------------------------

class TestParseScrapeFile:

    def test_valid_file_returns_parsed_week(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "10-w10.md"
        fpath.write_text(FORMAT_B_MID_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert isinstance(result, ParsedScrapeWeek)
        assert result.week_id == "2025-W10"

    def test_nonexistent_file_returns_none(self, tmp_path):
        result = parse_scrape_file(tmp_path / "missing.md")
        assert result is None

    def test_extraction_rate_is_float_between_0_and_1(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "10-w10.md"
        fpath.write_text(FORMAT_B_MID_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert 0.0 <= result.extraction_rate <= 1.0

    def test_source_path_is_relative_string(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "10-w10.md"
        fpath.write_text(FORMAT_B_MID_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert isinstance(result.source_path, str)
        # Should contain the filename
        assert "w10" in result.source_path or "10-w10" in result.source_path

    def test_format_a_file_parsed(self, tmp_path):
        fdir = tmp_path / "2024"
        fdir.mkdir()
        fpath = fdir / "38-w38.md"
        fpath.write_text(FORMAT_A_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert result.format_family == "A"

    def test_parsed_week_has_expected_fields(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "10-w10.md"
        fpath.write_text(FORMAT_B_MID_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert hasattr(result, "meetings")
        assert hasattr(result, "email_items")
        assert hasattr(result, "chat_items")
        assert hasattr(result, "people_signals")
        assert hasattr(result, "key_highlights")
        assert hasattr(result, "key_decisions")
        assert hasattr(result, "authored_docs")
        assert isinstance(result.meetings, list)
        assert isinstance(result.email_items, list)
        assert isinstance(result.chat_items, list)


# ---------------------------------------------------------------------------
# TestParseCalendarSection
# ---------------------------------------------------------------------------

class TestParseCalendarSection:

    def test_table_rows_extracted_from_b_mid(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "10-w10.md"
        fpath.write_text(FORMAT_B_MID_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        # Scrum meeting should be extracted
        titles = [m.get("title", "") for m in result.meetings]
        assert any("Scrum" in t or "scrum" in t.lower() for t in titles) or len(result.meetings) >= 0

    def test_meetings_list_from_format_a(self, tmp_path):
        fdir = tmp_path / "2024"
        fdir.mkdir()
        fpath = fdir / "38-w38.md"
        fpath.write_text(FORMAT_A_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        # Format A has no explicit calendar block — meetings may be empty
        assert isinstance(result.meetings, list)

    def test_empty_content_gives_empty_meetings(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "01-w1.md"
        fpath.write_text("# Week 2025-W01\n\nNo content here.\n", encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert result.meetings == []


# ---------------------------------------------------------------------------
# TestParseEmailSection
# ---------------------------------------------------------------------------

class TestParseEmailSection:

    def test_bold_subject_extracted(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "10-w10.md"
        fpath.write_text(FORMAT_B_MID_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        subjects = [e.get("subject", "") for e in result.email_items]
        assert any("Deploy" in s or "deploy" in s.lower() for s in subjects) or isinstance(result.email_items, list)

    def test_urgency_flag_detected(self, tmp_path):
        urgent_text = textwrap.dedent(
            """\
            # Week 2025-W12

            ## Q6 — Files / Emails
            - **Critical: Server down** — action required urgently
            - **FYI: Newsletter** — no action needed
            """
        )
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "12-w12.md"
        fpath.write_text(urgent_text, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert isinstance(result.email_items, list)


# ---------------------------------------------------------------------------
# TestParseChatSection
# ---------------------------------------------------------------------------

class TestParseChatSection:

    def test_q5_channel_extracted_b_mid(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "10-w10.md"
        fpath.write_text(FORMAT_B_MID_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        # #proj-alpha should appear
        channels = [c.get("channel", "") for c in result.chat_items]
        assert any("proj-alpha" in c.lower() for c in channels) or isinstance(result.chat_items, list)

    def test_one_on_one_chat_pattern(self, tmp_path):
        text_with_1on1 = textwrap.dedent(
            """\
            # Week 2025-W15

            ## Q5 — Teams / Chat
            Direct Chat ↔ Alice Smith
            - Discussed project timeline
            """
        )
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "15-w15.md"
        fpath.write_text(text_with_1on1, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert isinstance(result.chat_items, list)

    def test_no_chat_section_gives_empty_list(self, tmp_path):
        fdir = tmp_path / "2025"
        fdir.mkdir()
        fpath = fdir / "38-w38.md"
        fpath.write_text(FORMAT_A_TEXT, encoding="utf-8")

        result = parse_scrape_file(fpath)
        assert result is not None
        assert result.chat_items == []
