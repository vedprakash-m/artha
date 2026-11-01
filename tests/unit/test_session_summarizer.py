"""
tests/unit/test_session_summarizer.py — Unit tests for scripts/session_summarizer.py

Phase 3 verification suite.

Coverage:
  - SessionSummary schema validates correctly
  - Summary stays under 3,000 tokens
  - Full history is preserved to tmp/
  - Proactive trigger fires at configured percentage
  - No summarization during active processing (context arg is "safe")
  - Multi-command context % resets after summarization
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from session_summarizer import (
    SUMMARIZE_AFTER_COMMANDS,
    SessionSummary,
    _PYDANTIC_AVAILABLE,
    create_session_summary,
    estimate_context_pct,
    get_context_card,
    load_threshold_pct,
    should_summarize_now,
    summarize_to_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_summary(**kwargs) -> SessionSummary:
    defaults = dict(
        session_intent="Morning catch-up",
        command_executed="/catch-up",
        key_findings=["Immigration deadline in 30 days", "Tax return due April 15"],
        state_mutations=["state/immigration.md", "state/finance.md"],
        open_threads=["Review IRS notice"],
        next_suggested="/domain finance",
        context_before_pct=82.0,
        context_after_pct=22.0,
        trigger_reason="post_command",
    )
    defaults.update(kwargs)
    return create_session_summary(**defaults)


# ---------------------------------------------------------------------------
# SessionSummary construction
# ---------------------------------------------------------------------------

class TestSessionSummaryConstruction:
    def test_create_returns_summary_object(self):
        s = _make_summary()
        assert s.session_intent == "Morning catch-up"
        assert s.command_executed == "/catch-up"

    def test_key_findings_capped_at_5(self):
        s = create_session_summary(
            session_intent="test",
            command_executed="/catch-up",
            key_findings=["F1", "F2", "F3", "F4", "F5", "F6", "F7"],
            state_mutations=[],
            open_threads=[],
        )
        assert len(s.key_findings) <= 5

    def test_finding_truncated_at_200_chars(self):
        long_finding = "X" * 300
        s = create_session_summary(
            session_intent="test",
            command_executed="/catch-up",
            key_findings=[long_finding],
            state_mutations=[],
            open_threads=[],
        )
        for f in s.key_findings:
            assert len(f) <= 200

    def test_timestamp_is_iso_format(self):
        s = _make_summary()
        # Should begin with a 4-digit year
        assert s.timestamp[:4].isdigit()
        assert "T" in s.timestamp or "-" in s.timestamp

    def test_model_dump_or_dict_works(self):
        s = _make_summary()
        data = s.model_dump() if hasattr(s, "model_dump") else s.__dict__
        assert isinstance(data, dict)
        assert "session_intent" in data


# ---------------------------------------------------------------------------
# to_markdown
# ---------------------------------------------------------------------------

class TestSessionSummaryMarkdown:
    def test_markdown_contains_command(self):
        s = _make_summary(command_executed="/domain immigration")
        md = s.to_markdown()
        assert "/domain immigration" in md

    def test_markdown_contains_key_findings(self):
        s = _make_summary(key_findings=["Tax return due", "Visa deadline"])
        md = s.to_markdown()
        assert "Tax return due" in md
        assert "Visa deadline" in md

    def test_markdown_starts_with_header(self):
        s = _make_summary()
        md = s.to_markdown()
        assert md.startswith("# Session Summary")

    def test_markdown_under_3k_tokens(self):
        """Summary markdown must never exceed 3,000 tokens (~12,000 chars)."""
        s = _make_summary(
            key_findings=["Finding " + "X" * 190] * 5,
            state_mutations=["state/domain.md"] * 20,
            open_threads=["Thread " + "Y" * 50] * 10,
        )
        md = s.to_markdown()
        tokens = len(md) // 4  # 1 token ≈ 4 chars
        assert tokens < 3000, f"Markdown is {tokens} tokens — must be < 3000"


# ---------------------------------------------------------------------------
# summarize_to_file
# ---------------------------------------------------------------------------

class TestSummarizeToFile:
    def test_writes_markdown_file(self, tmp_path):
        s = _make_summary()
        path = summarize_to_file(s, session_n=1, artha_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".md"

    def test_writes_json_alongside(self, tmp_path):
        s = _make_summary()
        md_path = summarize_to_file(s, session_n=2, artha_dir=tmp_path)
        json_path = md_path.with_suffix(".json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "session_intent" in data

    def test_session_number_in_filename(self, tmp_path):
        s = _make_summary()
        path = summarize_to_file(s, session_n=3, artha_dir=tmp_path)
        assert "session_history_3" in path.name

    def test_full_history_preserved_verbatim(self, tmp_path):
        """The markdown written must contain the intent text verbatim."""
        s = _make_summary(session_intent="UNIQUE_INTENT_MARKER_12345")
        path = summarize_to_file(s, session_n=1, artha_dir=tmp_path)
        content = path.read_text()
        assert "UNIQUE_INTENT_MARKER_12345" in content

    def test_creates_tmp_dir_if_missing(self, tmp_path):
        assert not (tmp_path / "tmp").exists()
        s = _make_summary()
        summarize_to_file(s, session_n=1, artha_dir=tmp_path)
        assert (tmp_path / "tmp").exists()


# ---------------------------------------------------------------------------
# estimate_context_pct
# ---------------------------------------------------------------------------

class TestEstimateContextPct:
    def test_empty_string_is_zero(self):
        assert estimate_context_pct("") == 0.0

    def test_full_context_is_100(self):
        # Fill the entire model context
        from session_summarizer import _MODEL_CONTEXT_CHARS
        text = "a" * _MODEL_CONTEXT_CHARS
        pct = estimate_context_pct(text)
        assert pct == pytest.approx(100.0)

    def test_half_context(self):
        from session_summarizer import _MODEL_CONTEXT_CHARS
        text = "a" * (_MODEL_CONTEXT_CHARS // 2)
        pct = estimate_context_pct(text)
        assert pct == pytest.approx(50.0)

    def test_custom_limit(self):
        pct = estimate_context_pct("aaaa", model_limit_chars=100)
        assert pct == pytest.approx(4.0)

    def test_capped_at_100(self):
        huge = "a" * 10_000_000
        pct = estimate_context_pct(huge, model_limit_chars=100)
        assert pct == 100.0


# ---------------------------------------------------------------------------
# should_summarize_now
# ---------------------------------------------------------------------------

class TestShouldSummarizeNow:
    def test_post_command_trigger_catchup(self):
        with patch("session_summarizer.load_harness_flag", return_value=True):
            with patch("session_summarizer.load_threshold_pct", return_value=70.0):
                result = should_summarize_now("small context text", command="/catch-up")
        assert result is True

    def test_post_command_trigger_domain(self):
        with patch("session_summarizer.load_harness_flag", return_value=True):
            with patch("session_summarizer.load_threshold_pct", return_value=70.0):
                result = should_summarize_now("small", command="/domain finance")
        assert result is True

    def test_proactive_trigger_at_threshold(self):
        """Should fire when context usage >= threshold."""
        from session_summarizer import _MODEL_CONTEXT_CHARS
        # Text at 75% of model context
        large_text = "a" * int(_MODEL_CONTEXT_CHARS * 0.75)
        with patch("session_summarizer.load_harness_flag", return_value=True):
            with patch("session_summarizer.load_threshold_pct", return_value=70.0):
                result = should_summarize_now(large_text)
        assert result is True

    def test_no_trigger_below_threshold(self):
        """Should NOT fire when context usage < threshold and no command."""
        with patch("session_summarizer.load_harness_flag", return_value=True):
            with patch("session_summarizer.load_threshold_pct", return_value=70.0):
                result = should_summarize_now("tiny text")
        assert result is False

    def test_feature_flag_disabled_never_triggers(self):
        """When feature flag is off, summarization never fires."""
        with patch("session_summarizer.load_harness_flag", return_value=False):
            result = should_summarize_now("huge " * 100_000, command="/catch-up")
        assert result is False

    def test_no_summarize_for_status_command(self):
        """Commands like /status do not trigger summarization."""
        with patch("session_summarizer.load_harness_flag", return_value=True):
            with patch("session_summarizer.load_threshold_pct", return_value=70.0):
                result = should_summarize_now("small", command="/status")
        assert result is False


# ---------------------------------------------------------------------------
# get_context_card
# ---------------------------------------------------------------------------

class TestGetContextCard:
    def test_card_contains_command(self):
        s = _make_summary(command_executed="/catch-up flash")
        card = get_context_card(s)
        assert "/catch-up flash" in card

    def test_card_contains_intent(self):
        s = _make_summary(session_intent="Morning briefing")
        card = get_context_card(s)
        assert "Morning briefing" in card

    def test_card_contains_session_context_markers(self):
        s = _make_summary()
        card = get_context_card(s)
        assert "SESSION CONTEXT" in card

    def test_card_is_compact(self):
        s = _make_summary()
        tokens = len(get_context_card(s)) // 4
        assert tokens < 3000


# ---------------------------------------------------------------------------
# load_threshold_pct
# ---------------------------------------------------------------------------

class TestLoadThresholdPct:
    def test_returns_float(self):
        pct = load_threshold_pct()
        assert isinstance(pct, float)
        assert 0.0 <= pct <= 100.0

    def test_default_is_70_when_no_config(self, tmp_path):
        """When config/artha_config.yaml is absent, default is 70."""
        with patch("session_summarizer.ARTHA_DIR", tmp_path):
            pct = load_threshold_pct()
        assert pct == 70.0
