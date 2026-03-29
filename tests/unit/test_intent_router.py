"""tests/unit/test_intent_router.py — golden test cases for _classify_intent.

15 golden NL→intent test cases (one per intent) + bridge-path encrypted-domain
blocking test for immigration-query.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.llm_bridge import _classify_intent, _INTENT_ENCRYPTED_DOMAINS


# ---------------------------------------------------------------------------
# Golden NL → intent routing
# ---------------------------------------------------------------------------

class TestClassifyIntent:
    """One golden case per intent in _INTENT_PATTERNS."""

    def test_brief(self):
        assert _classify_intent("catch me up") == "brief"

    def test_work(self):
        assert _classify_intent("what's happening at work") == "work"

    def test_items(self):
        assert _classify_intent("what's open") == "items"

    def test_goals(self):
        assert _classify_intent("how are my goals") == "goals"

    def test_status(self):
        assert _classify_intent("how's everything") == "status"

    def test_work_prep(self):
        assert _classify_intent("prep me for my 3pm") == "work-prep"

    def test_work_sprint(self):
        assert _classify_intent("how's the sprint") == "work-sprint"

    def test_work_connect_prep(self):
        assert _classify_intent("prepare for connect review") == "work-connect-prep"

    def test_content_draft(self):
        assert _classify_intent("write a LinkedIn post about AI") == "content-draft"

    def test_items_done(self):
        assert _classify_intent("mark OI-123 done") == "items-done"

    def test_items_quick(self):
        assert _classify_intent("anything quick I can knock out") == "items-quick"

    def test_immigration_query(self):
        assert _classify_intent("what's my visa status?") == "immigration-query"

    def test_teach(self):
        assert _classify_intent("teach me about OAuth tokens") == "teach"

    def test_dashboard(self):
        assert _classify_intent("show me everything") == "dashboard"

    def test_reconnect(self):
        assert _classify_intent("reconnect gmail") == "reconnect"

    def test_radar(self):
        assert _classify_intent("show radar") == "radar"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestClassifyIntentEdgeCases:
    def test_unrecognized_returns_none(self):
        assert _classify_intent("who won the 1988 world cup") is None

    def test_empty_string_returns_none(self):
        assert _classify_intent("") is None

    def test_case_insensitive(self):
        # Patterns use re.search on lower-cased input — check mixed case input
        assert _classify_intent("CATCH ME UP") == "brief"

    def test_reconnect_variants(self):
        assert _classify_intent("reconnect outlook") == "reconnect"
        assert _classify_intent("fix encryption") == "reconnect"
        assert _classify_intent("reconnect WorkIQ") == "reconnect"

    def test_brief_variants(self):
        assert _classify_intent("morning briefing") == "brief"
        assert _classify_intent("brief me") == "brief"

    def test_goals_variants(self):
        assert _classify_intent("goal progress") == "goals"

    def test_items_variants(self):
        assert _classify_intent("what's overdue") == "items"


# ---------------------------------------------------------------------------
# Bridge-path encrypted-domain blocking
# ---------------------------------------------------------------------------

class TestBridgePathEncryptedDomains:
    """immigration-query is the only intent currently blocked on bridge path."""

    def test_immigration_is_blocked_on_bridge(self):
        blocked = _INTENT_ENCRYPTED_DOMAINS.get("immigration-query", [])
        assert "immigration" in blocked

    def test_reconnect_not_blocked_on_bridge(self):
        # reconnect routes to setup scripts — no sensitive state read.
        blocked = _INTENT_ENCRYPTED_DOMAINS.get("reconnect", [])
        assert blocked == []

    def test_brief_not_blocked(self):
        blocked = _INTENT_ENCRYPTED_DOMAINS.get("brief", [])
        assert not blocked

    def test_goals_not_blocked(self):
        blocked = _INTENT_ENCRYPTED_DOMAINS.get("goals", [])
        assert not blocked
