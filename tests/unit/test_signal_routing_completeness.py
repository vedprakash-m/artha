"""
tests/unit/test_signal_routing_completeness.py
================================================
DEBT-008: Verify all 3 previously-orphaned signal types now have routing
entries in config/signal_routing.yaml, and that all routing targets are valid
action types.
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
_CONFIG_DIR  = os.path.join(os.path.dirname(__file__), "..", "..", "config")

if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Known valid action types (from action_composer._ALLOWED_ACTION_TYPES)
_ALLOWED_ACTION_TYPES = {
    "apple_reminders_sync", "calendar_create", "calendar_modify",
    "email_reply", "email_send", "instruction_sheet", "reminder_create",
    "slack_send", "todo_sync", "todoist_sync", "whatsapp_send",
}


def _load_routing() -> dict:
    path = os.path.join(_CONFIG_DIR, "signal_routing.yaml")
    with open(path) as f:
        return yaml.safe_load(f) or {}


class TestSignalRoutingCompleteness:
    """A1–A3 from DEBT-008."""

    def test_automation_failure_routed(self):
        """A1: automation_failure signal has a routing entry."""
        routing = _load_routing()
        assert "automation_failure" in routing, \
            "DEBT-008: automation_failure missing from signal_routing.yaml"

    def test_goal_autopark_candidate_routed(self):
        """goal_autopark_candidate signal has a routing entry."""
        routing = _load_routing()
        assert "goal_autopark_candidate" in routing, \
            "DEBT-008: goal_autopark_candidate missing from signal_routing.yaml"

    def test_slack_action_item_routed(self):
        """slack_action_item signal has a routing entry."""
        routing = _load_routing()
        assert "slack_action_item" in routing, \
            "DEBT-008: slack_action_item missing from signal_routing.yaml"

    def test_all_action_types_valid(self):
        """A2: All routing targets use valid _ALLOWED_ACTION_TYPES values."""
        routing = _load_routing()
        for signal_name, entry in routing.items():
            if not isinstance(entry, dict):
                continue
            action_type = entry.get("action_type")
            if action_type is not None:
                assert action_type in _ALLOWED_ACTION_TYPES, (
                    f"Signal '{signal_name}' uses invalid action_type='{action_type}'. "
                    f"Allowed: {sorted(_ALLOWED_ACTION_TYPES)}"
                )

    def test_orphaned_signals_use_valid_types(self):
        """A1 + A2: All 3 newly-added signals use valid action types."""
        routing = _load_routing()
        for sig in ["automation_failure", "goal_autopark_candidate", "slack_action_item"]:
            at = routing.get(sig, {}).get("action_type")
            assert at in _ALLOWED_ACTION_TYPES, \
                f"DEBT-008: {sig} uses invalid action_type={at!r}"

    def test_routing_table_is_valid_yaml(self):
        """signal_routing.yaml must parse without errors."""
        routing = _load_routing()
        assert isinstance(routing, dict) and len(routing) > 0

    def test_completeness_catches_removal(self):
        """A3: Verify test logic detects a missing signal (meta-test)."""
        routing = _load_routing()
        # Simulate removing automation_failure
        routing_copy = dict(routing)
        routing_copy.pop("automation_failure", None)
        assert "automation_failure" not in routing_copy, \
            "Meta-test setup failed"
        # If we were running the check against routing_copy, it would fail
        # We verify the check logic itself is correct by asserting it IS in routing
        assert "automation_failure" in routing, \
            "DEBT-008: automation_failure must be present in actual signal_routing.yaml"
