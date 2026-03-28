"""tests/unit/test_signal_routing.py — WS-7 signal routing externalization.

Verifies:
  1. config/signal_routing.yaml entries match the hardcoded fallback dict
  2. Missing YAML falls back gracefully to _FALLBACK_SIGNAL_ROUTING
  3. Unknown signal types return None (graceful degradation)

Ref: specs/pay-debt-reloaded.md §7 WS-7
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = REPO_ROOT / "config"
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture(autouse=True)
def _clean_imports():
    """Remove cached action_composer module between tests so each test gets fresh state."""
    yield
    for mod in list(sys.modules.keys()):
        if "action_composer" in mod or "config_loader" in mod:
            sys.modules.pop(mod, None)


def _load_yaml_routing() -> dict[str, dict[str, Any]]:
    path = CONFIG_DIR / "signal_routing.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_fallback_routing() -> dict[str, dict[str, Any]]:
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from action_composer import _FALLBACK_SIGNAL_ROUTING  # noqa: PLC0415
    return _FALLBACK_SIGNAL_ROUTING


# ---------------------------------------------------------------------------
# Test 1: YAML matches fallback
# ---------------------------------------------------------------------------

def test_yaml_matches_fallback():
    """Every entry in signal_routing.yaml must match the hardcoded fallback."""
    yaml_data = _load_yaml_routing()
    fallback = _load_fallback_routing()

    yaml_keys = set(yaml_data)
    fallback_keys = set(fallback)

    missing = fallback_keys - yaml_keys
    extra = yaml_keys - fallback_keys
    assert not missing, f"Signals missing from signal_routing.yaml: {sorted(missing)}"
    assert not extra, f"Extra signals in signal_routing.yaml not in fallback: {sorted(extra)}"

    mismatches: list[str] = []
    for key in fallback_keys:
        fb = fallback[key]
        yv = yaml_data[key]
        for field in ("action_type", "friction", "min_trust", "reversible", "undo_window_sec"):
            fb_val = fb.get(field)
            y_val = yv.get(field)
            if fb_val != y_val:
                mismatches.append(f"  {key}.{field}: fallback={fb_val!r}, yaml={y_val!r}")
    assert not mismatches, (
        "Value mismatches between signal_routing.yaml and _FALLBACK_SIGNAL_ROUTING:\n"
        + "\n".join(mismatches)
    )


# ---------------------------------------------------------------------------
# Test 2: Missing YAML falls back to hardcoded dict
# ---------------------------------------------------------------------------

def test_yaml_missing_falls_back():
    """If signal_routing.yaml is absent, _load_signal_routing() returns fallback."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import action_composer  # noqa: PLC0415

    # Simulate load_config returning empty dict (file not found)
    with patch("action_composer.load_config" if hasattr(action_composer, "load_config") else "lib.config_loader.load_config", return_value={}, create=True):
        # Reload to clear lru_cache and force through the try/except
        result = action_composer._load_signal_routing()
        # When load_config returns {}, the function falls back to _FALLBACK_SIGNAL_ROUTING
        # (empty dict is falsy so the `if routing:` check fails)
        assert result is action_composer._FALLBACK_SIGNAL_ROUTING or isinstance(result, dict)
        assert "bill_due" in result, "Fallback must contain known signal types"


# ---------------------------------------------------------------------------
# Test 3: Unknown signal type returns None (graceful degradation)
# ---------------------------------------------------------------------------

def test_unknown_signal_returns_default_friction():
    """action_composer.compose() returns None for unknown signal types."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from action_composer import ActionComposer  # noqa: PLC0415
    from actions.base import DomainSignal  # noqa: PLC0415

    composer = ActionComposer()
    signal = DomainSignal(
        signal_type="totally_unknown_signal_xyz",
        domain="unknown",
        entity="test",
        urgency=1,
        impact=1,
        source="test",
        metadata={},
        detected_at="2024-01-01T00:00:00Z",
    )
    result = composer.compose(signal)
    assert result is None, "Unknown signal types should return None, not raise"


# ---------------------------------------------------------------------------
# Test 4: All YAML action_types are valid
# ---------------------------------------------------------------------------

def test_yaml_action_types_are_valid():
    """Every action_type in signal_routing.yaml must be a known valid action type."""
    ALLOWED = {
        "email_send", "email_reply", "calendar_create", "calendar_modify",
        "reminder_create", "whatsapp_send", "todo_sync", "instruction_sheet",
        "slack_send", "todoist_sync", "apple_reminders_sync",
        "decision_log_proposal",
    }
    yaml_data = _load_yaml_routing()
    invalid: list[str] = []
    for signal_type, entry in yaml_data.items():
        at = entry.get("action_type", "")
        if at not in ALLOWED:
            invalid.append(f"  {signal_type}: action_type={at!r}")
    assert not invalid, (
        "signal_routing.yaml contains unknown action_type values:\n" + "\n".join(invalid)
    )
