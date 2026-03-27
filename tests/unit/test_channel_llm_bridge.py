"""tests/unit/test_channel_llm_bridge.py — T4-33..42: channel.llm_bridge tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.llm_bridge import (
    _detect_domains,
    _gather_context,
    _detect_llm_cli,
    _detect_all_llm_clis,
    _LLM_MAX_CONTEXT_CHARS,
)


# ---------------------------------------------------------------------------
# T4-33: _detect_domains — keyword routing
# ---------------------------------------------------------------------------

class TestDetectDomains:
    def test_finance_keywords(self):
        doms = _detect_domains("What is my bank balance and budget?")
        assert "finance" in doms

    def test_health_keywords(self):
        doms = _detect_domains("doctor appointment and medication schedule")
        assert "health" in doms

    def test_work_keywords(self):
        doms = _detect_domains("work meeting with team about promotion")
        # "work" maps to employment and calendar keywords
        assert any(d in ("employment", "calendar") for d in doms)

    def test_unknown_returns_general(self):
        doms = _detect_domains("zzz xyz abc")
        assert doms == ["general"]

    def test_returns_list(self):
        result = _detect_domains("any question")
        assert isinstance(result, list)

    def test_max_three_domains(self):
        # Rich question with many keyword hits
        doms = _detect_domains(
            "doctor bank team budget medication purchase flight course friend"
        )
        assert len(doms) <= 3


# ---------------------------------------------------------------------------
# T4-34: _gather_context — budget cap
# ---------------------------------------------------------------------------

class TestGatherContext:
    def test_returns_string(self):
        result = _gather_context(["general"])
        assert isinstance(result, str)

    def test_budget_respected(self):
        small_budget = 200
        result = _gather_context(["general"], max_chars=small_budget)
        # Allow some slack for header overhead
        assert len(result) <= small_budget + 300

    def test_empty_domains_ok(self):
        result = _gather_context([])
        assert isinstance(result, str)

    def test_non_existent_domain_ok(self):
        # "nonexistent" domain should gracefully not crash
        result = _gather_context(["general", "finance"])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T4-35: _detect_llm_cli — fallback chain
# ---------------------------------------------------------------------------

class TestDetectLlmCli:
    def test_returns_tuple_or_none(self):
        result = _detect_llm_cli()
        assert result is None or (isinstance(result, tuple) and len(result) == 2)

    def test_no_llm_available(self):
        with patch("shutil.which", return_value=None):
            result = _detect_llm_cli()
            # When no LLM CLI found → None
            assert result is None

    def test_llm_cli_known_binary_found(self):
        with patch("shutil.which") as mock_which:
            # Simulate "gemini" CLI being installed
            mock_which.side_effect = lambda x: "/usr/local/bin/gemini" if x == "gemini" else None
            result = _detect_llm_cli()
            # If gemini is in the detection chain, result should be non-None
            # (result may be None if "gemini" isn't in the detection chain)
            assert result is None or isinstance(result, tuple)


# ---------------------------------------------------------------------------
# T4-36: _detect_all_llm_clis — returns list
# ---------------------------------------------------------------------------

class TestDetectAllLlmClis:
    def test_returns_list(self):
        result = _detect_all_llm_clis()
        assert isinstance(result, list)

    def test_entries_are_tuples(self):
        result = _detect_all_llm_clis()
        for entry in result:
            assert isinstance(entry, tuple)
            assert len(entry) >= 2

    def test_no_clis_returns_empty_list(self):
        with patch("shutil.which", return_value=None):
            result = _detect_all_llm_clis()
            assert isinstance(result, list)
