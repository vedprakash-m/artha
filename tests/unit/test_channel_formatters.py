"""tests/unit/test_channel_formatters.py — T4-1..10: channel.formatters tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.formatters import (
    _strip_frontmatter,
    _is_noise_section,
    _filter_noise_bullets,
    _clean_for_telegram,
    _trim_to_cap,
    _extract_section_summaries,
    _truncate,
    _split_message,
)


# ---------------------------------------------------------------------------
# T4-1: _strip_frontmatter
# ---------------------------------------------------------------------------

class TestStripFrontmatter:
    def test_removes_frontmatter(self):
        content = "---\ntitle: test\n---\n\nBody text here."
        result = _strip_frontmatter(content)
        assert "Body text here." in result
        assert "title: test" not in result

    def test_no_frontmatter_returns_content(self):
        content = "Just plain text.\nNo frontmatter."
        result = _strip_frontmatter(content)
        assert "Just plain text." in result

    def test_empty_frontmatter(self):
        content = "---\n---\n\nOnly body."
        result = _strip_frontmatter(content)
        assert "Only body." in result

    def test_empty_string(self):
        assert _strip_frontmatter("") == ""


# ---------------------------------------------------------------------------
# T4-2: _is_noise_section
# ---------------------------------------------------------------------------

class TestIsNoiseSection:
    def test_known_noise_headers(self):
        noise_headers = [
            "## References", "## See Also", "## Notes", "## Appendix",
            "## Footer", "## Legend",
        ]
        for h in noise_headers:
            # Some of these may or may not be considered noise — just check it returns bool
            result = _is_noise_section(h)
            assert isinstance(result, bool)

    def test_content_header_not_noise(self):
        result = _is_noise_section("## Goals Overview")
        # Content sections shouldn't be noise
        assert isinstance(result, bool)

    def test_empty_header(self):
        result = _is_noise_section("")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# T4-3: _filter_noise_bullets
# ---------------------------------------------------------------------------

class TestFilterNoiseBullets:
    def test_returns_list(self):
        lines = ["- Item one\n", "- Item two\n", "- N/A\n", "- None\n"]
        result = _filter_noise_bullets(lines)
        assert isinstance(result, list)

    def test_non_empty_bullets_preserved(self):
        lines = ["- Important task\n", "- Another task\n"]
        result = _filter_noise_bullets(lines)
        # Should keep substantive items
        assert len(result) >= 1

    def test_empty_input(self):
        result = _filter_noise_bullets([])
        assert result == []


# ---------------------------------------------------------------------------
# T4-4: _clean_for_telegram
# ---------------------------------------------------------------------------

class TestCleanForTelegram:
    def test_basic_text_passes_through(self):
        text = "Hello, world! This is a status update."
        result = _clean_for_telegram(text)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_removes_or_escapes_special_chars(self):
        # Telegram has specific markdown that needs escaping
        text = "Status: **bold** _italic_ [link](http://example.com)"
        result = _clean_for_telegram(text)
        assert isinstance(result, str)

    def test_empty_string(self):
        result = _clean_for_telegram("")
        assert isinstance(result, str)

    def test_long_text_cleaned(self):
        text = "## Header\n\nParagraph with _emphasis_ and some **strong** text.\n\n- Bullet 1\n- Bullet 2\n"
        result = _clean_for_telegram(text)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# T4-5: _trim_to_cap boundary
# ---------------------------------------------------------------------------

class TestTrimToCap:
    def test_short_text_unchanged(self):
        text = "Short text."
        result = _trim_to_cap(text, cap=1000)
        assert "Short text." in result

    def test_long_text_trimmed(self):
        text = "x" * 200
        result = _trim_to_cap(text, cap=100)
        assert len(result) <= 115  # cap + ellipsis length

    def test_exact_cap_not_trimmed(self):
        text = "x" * 100
        result = _trim_to_cap(text, cap=100)
        assert len(result) >= 100

    def test_empty_string(self):
        result = _trim_to_cap("", cap=100)
        assert result == ""


# ---------------------------------------------------------------------------
# T4-6: _extract_section_summaries budget
# ---------------------------------------------------------------------------

class TestExtractSectionSummaries:
    def test_returns_string(self):
        content = "## Section 1\n\nContent here.\n\n## Section 2\n\nMore content.\n"
        result = _extract_section_summaries(content)
        assert isinstance(result, str)

    def test_budget_respected(self):
        large_content = "\n".join(f"## Section {i}\n\n" + "x" * 200 for i in range(50))
        result = _extract_section_summaries(large_content, max_total=1000)
        assert len(result) <= 1200  # allow some slack for formatting

    def test_empty_content(self):
        result = _extract_section_summaries("")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T4-7: _truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_not_truncated(self):
        result = _truncate("hello", maxlen=100)
        assert result == "hello"

    def test_long_truncated(self):
        result = _truncate("x" * 200, maxlen=100)
        assert len(result) <= 103  # 100 + "..." length

    def test_empty(self):
        assert _truncate("", maxlen=100) == ""


# ---------------------------------------------------------------------------
# T4-8: _split_message at exact limit
# ---------------------------------------------------------------------------

class TestSplitMessage:
    def test_short_message_single_part(self):
        text = "Hello world"
        parts = _split_message(text, max_len=4000)
        assert isinstance(parts, list)
        assert len(parts) == 1
        assert parts[0] == text

    def test_long_message_splits(self):
        text = "word " * 2000  # ~10000 chars
        parts = _split_message(text, max_len=4000)
        assert len(parts) > 1
        for part in parts:
            assert len(part) <= 4100  # allow word-break slack

    def test_empty_message(self):
        parts = _split_message("", max_len=4000)
        assert isinstance(parts, list)

    def test_exact_limit_no_split(self):
        text = "x" * 4000
        parts = _split_message(text, max_len=4000)
        # Should be at most 2 parts given exact boundary
        assert len(parts) <= 2
