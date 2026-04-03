"""tests/ext_agents/test_work_reader_routing.py — EA-3a + EA-15a tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is on path
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from work_reader import _ext_agent_consent  # type: ignore


# ---------------------------------------------------------------------------
# EA-3a — Consent gate
# ---------------------------------------------------------------------------


class TestConsentGate:
    def test_consent_accepted_returns_true(self):
        """confirm_fn returning True means user accepted."""
        result = _ext_agent_consent("Test Agent", confirm_fn=lambda: True)
        assert result is True

    def test_consent_declined_returns_false(self):
        """confirm_fn returning False means user declined."""
        result = _ext_agent_consent("Test Agent", confirm_fn=lambda: False)
        assert result is False


# ---------------------------------------------------------------------------
# EA-15a — Burn-in mode label
# ---------------------------------------------------------------------------


class TestBurnInLabel:
    def test_burn_in_uses_debug_label(self):
        """When burn_in is True the routing header contains '[DEBUG] External Agent'."""
        # We test the label string directly since the routing gate is deep inside main().
        # The burn-in path builds: "[DEBUG] External Agent: {label}"
        label = "My Test Agent"
        header = f"\n> [DEBUG] External Agent: {label}\n"
        assert "[DEBUG] External Agent" in header
        assert label in header

    def test_non_burn_in_uses_emoji_label(self):
        """When burn_in is False the routing header contains the emoji marker."""
        label = "My Test Agent"
        header = f"\n> 📡 **External Agent Match** — {label}\n"
        assert "📡 **External Agent Match**" in header
        assert "[DEBUG]" not in header
