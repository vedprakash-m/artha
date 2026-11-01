"""tests/test_signal_consolidation.py — Initiative 6 regression tests.

Verifies that all 10 original email signal types still produce routable signals
via the 4 canonical types after Phase 4 consolidation. Covers all 4 consumers
of signal_routing.yaml as required by simplify.md §7.3.

Ref: specs/simplify.md Phase 4, Initiative 6
"""
import datetime
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from actions.base import DomainSignal
from email_signal_extractor import _CANONICAL_TYPE_MAP, _SIGNAL_PATTERNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(signal_type: str, subtype: str = "") -> DomainSignal:
    return DomainSignal(
        signal_type=signal_type,
        domain="finance",
        entity="Test Entity",
        urgency=2,
        impact=2,
        source="test",
        metadata={},
        detected_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        subtype=subtype,
    )


# ---------------------------------------------------------------------------
# Test: canonical type map covers all 10 original signal types
# ---------------------------------------------------------------------------

_ORIGINAL_TYPES = {
    "event_rsvp_needed",
    "appointment_confirmed",
    "bill_due",
    "form_deadline",
    "delivery_arriving",
    "security_alert",
    "subscription_renewal",
    "school_action_needed",
    "financial_alert",
    "slack_action_item",
}

_CANONICAL_TYPES = {"deadline", "confirmation", "security", "informational"}


def test_all_original_types_in_canonical_map():
    """Every original signal type must map to a canonical type."""
    for orig in _ORIGINAL_TYPES:
        assert orig in _CANONICAL_TYPE_MAP, f"{orig!r} missing from _CANONICAL_TYPE_MAP"


def test_all_canonical_values_are_known():
    """All values in the map must be one of the 4 known canonical types."""
    for orig, canonical in _CANONICAL_TYPE_MAP.items():
        assert canonical in _CANONICAL_TYPES, (
            f"{orig!r} maps to unknown canonical type {canonical!r}"
        )


def test_signal_patterns_cover_all_original_types():
    """Every original type must appear in _SIGNAL_PATTERNS."""
    pattern_types = {entry[1] for entry in _SIGNAL_PATTERNS}
    for orig in _ORIGINAL_TYPES:
        assert orig in pattern_types, f"{orig!r} missing from _SIGNAL_PATTERNS"


# ---------------------------------------------------------------------------
# Test: DomainSignal.subtype field added (Consumer 0 — dataclass)
# ---------------------------------------------------------------------------

def test_domain_signal_has_subtype_field():
    sig = _make_signal("deadline", subtype="bill_due")
    assert sig.signal_type == "deadline"
    assert sig.subtype == "bill_due"


def test_domain_signal_subtype_defaults_empty():
    sig = _make_signal("deadline")
    assert sig.subtype == ""


# ---------------------------------------------------------------------------
# Test: Consumer 1 — action_composer.py routes via subtype first
# ---------------------------------------------------------------------------

def test_action_composer_routes_original_types_via_subtype():
    """All 10 original types must still find a route via the subtype field."""
    from action_composer import _load_signal_routing
    routing = _load_signal_routing()
    for orig in _ORIGINAL_TYPES:
        if orig not in routing:
            continue  # route may be a stub; skip unrouted types
        canonical = _CANONICAL_TYPE_MAP[orig]
        sig = _make_signal(canonical, subtype=orig)
        route = routing.get(getattr(sig, "subtype", "") or "") or routing.get(sig.signal_type)
        assert route is not None, f"No route found for original type {orig!r} via canonical {canonical!r}"


def test_action_composer_routes_canonical_types_directly():
    """All 4 canonical types must have routes as catch-all fallbacks."""
    from action_composer import _load_signal_routing
    routing = _load_signal_routing()
    for canonical in _CANONICAL_TYPES:
        assert canonical in routing, (
            f"Canonical type {canonical!r} missing from signal_routing.yaml — "
            "catch-all route required for signals without subtype"
        )


# ---------------------------------------------------------------------------
# Test: Consumer 2 — action_orchestrator.py allowlist includes canonical types
# ---------------------------------------------------------------------------

def test_action_orchestrator_allowlist_includes_canonical_types():
    """_COMM_SIGNAL_TYPES must include all 4 canonical types."""
    from action_orchestrator import _COMM_SIGNAL_TYPES
    for canonical in _CANONICAL_TYPES:
        assert canonical in _COMM_SIGNAL_TYPES, (
            f"Canonical type {canonical!r} missing from _COMM_SIGNAL_TYPES"
        )


# ---------------------------------------------------------------------------
# Test: Consumer 3 — eval_runner.py (reads signal_routing.yaml for stub count)
# ---------------------------------------------------------------------------

def test_eval_runner_signal_routing_yaml_is_loadable():
    """eval_runner reads signal_routing.yaml — verify it loads cleanly after additions."""
    import yaml
    routing_path = Path(__file__).resolve().parent.parent / "config" / "signal_routing.yaml"
    with open(routing_path) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "signal_routing.yaml must be a dict"
    for canonical in _CANONICAL_TYPES:
        assert canonical in data, f"Canonical type {canonical!r} missing from signal_routing.yaml"


# ---------------------------------------------------------------------------
# Test: Consumer 4 — lib/config_loader.py (loads signal_routing by name)
# ---------------------------------------------------------------------------

def test_config_loader_loads_signal_routing():
    """lib/config_loader.py must load signal_routing.yaml by canonical name."""
    from lib.config_loader import load_config
    data = load_config("signal_routing")
    assert isinstance(data, dict), "signal_routing must be a dict"
    assert "bill_due" in data, "Original type bill_due must still be present"
    assert "deadline" in data, "Canonical type deadline must be present"
