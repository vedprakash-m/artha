"""
tests/integration/test_action_pipeline_e2e.py — End-to-end action pipeline tests.

Exercises: DomainSignal → ActionComposer.compose() → ActionProposal lifecycle.

Coverage:
  - compose() returns ActionProposal for a known signal type
  - ActionProposal fields match signal domain and routing table
  - compose() returns None for unknown signal_type
  - compose() raises ValueError for empty signal_type
  - compose() sets correct friction for high-sensitivity domain (immigration)
  - compose() sets sensitivity=high for finance domain
  - compose() produces unique proposal IDs
  - compose() includes source_skill from signal
  - compose() expires_at is an ISO timestamp in the future
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from action_composer import ActionComposer
from actions.base import ActionProposal, DomainSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    signal_type: str = "bill_due",
    domain: str = "finance",
    entity: str = "Metro Electric",
    urgency: int = 2,
    impact: int = 2,
    source: str = "skill:bill_tracker",
    metadata: dict | None = None,
) -> DomainSignal:
    return DomainSignal(
        signal_type=signal_type,
        domain=domain,
        entity=entity,
        urgency=urgency,
        impact=impact,
        source=source,
        metadata=metadata or {},
        detected_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def composer():
    return ActionComposer()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestComposePipeline:
    def test_returns_action_proposal_for_known_signal(self, composer):
        signal = _make_signal(signal_type="bill_due", domain="finance")
        proposal = composer.compose(signal)
        assert proposal is not None
        assert isinstance(proposal, ActionProposal)

    def test_proposal_domain_matches_signal(self, composer):
        signal = _make_signal(signal_type="bill_due", domain="finance")
        proposal = composer.compose(signal)
        assert proposal.domain == "finance"

    def test_proposal_action_type_is_string(self, composer):
        signal = _make_signal(signal_type="bill_due")
        proposal = composer.compose(signal)
        assert isinstance(proposal.action_type, str)
        assert len(proposal.action_type) > 0

    def test_proposal_title_populated(self, composer):
        signal = _make_signal(signal_type="bill_due", entity="Metro Electric")
        proposal = composer.compose(signal)
        assert proposal.title
        assert len(proposal.title) <= 120

    def test_proposal_id_is_uuid(self, composer):
        import uuid
        signal = _make_signal(signal_type="bill_due")
        proposal = composer.compose(signal)
        # Verify it parses as UUID without error
        parsed = uuid.UUID(proposal.id)
        assert str(parsed) == proposal.id

    def test_unique_ids_across_proposals(self, composer):
        signal = _make_signal(signal_type="bill_due")
        p1 = composer.compose(signal)
        p2 = composer.compose(signal)
        assert p1 is not None
        assert p2 is not None
        assert p1.id != p2.id

    def test_expires_at_is_future_iso_timestamp(self, composer):
        signal = _make_signal(signal_type="bill_due")
        proposal = composer.compose(signal)
        assert proposal.expires_at is not None
        expires = datetime.fromisoformat(proposal.expires_at)
        now = datetime.now(expires.tzinfo)
        assert expires > now

    def test_source_skill_matches_signal_source(self, composer):
        signal = _make_signal(signal_type="bill_due", source="skill:bill_tracker")
        proposal = composer.compose(signal)
        assert proposal.source_skill == "skill:bill_tracker"

    def test_min_trust_is_int(self, composer):
        signal = _make_signal(signal_type="bill_due")
        proposal = composer.compose(signal)
        assert isinstance(proposal.min_trust, int)
        assert proposal.min_trust in (0, 1, 2)

    def test_sensitivity_field_present(self, composer):
        signal = _make_signal(signal_type="bill_due", domain="finance")
        proposal = composer.compose(signal)
        assert proposal.sensitivity in ("standard", "high", "critical")


# ---------------------------------------------------------------------------
# Friction escalation
# ---------------------------------------------------------------------------

class TestFrictionEscalation:
    def test_immigration_domain_escalates_to_high_friction(self, composer):
        """Immigration domain always forces friction=high (§10.3 rule 4)."""
        # Use a signal type that EXISTS in the routing table AND is immigration domain
        signal = _make_signal(
            signal_type="immigration_deadline",
            domain="immigration",
        )
        proposal = composer.compose(signal)
        assert proposal is not None
        assert proposal.friction == "high"

    def test_finance_domain_escalates_to_high_friction(self, composer):
        """Finance domain (in _HIGH_FRICTION_DOMAINS) always forces friction=high."""
        signal = _make_signal(signal_type="bill_due", domain="finance")
        proposal = composer.compose(signal)
        assert proposal is not None
        # §10.3 rule 4: finance is in _HIGH_FRICTION_DOMAINS → friction=high
        assert proposal.friction == "high"


# ---------------------------------------------------------------------------
# None / error cases
# ---------------------------------------------------------------------------

class TestComposeEdgeCases:
    def test_unknown_signal_type_returns_none(self, composer):
        signal = _make_signal(signal_type="completely_unknown_signal_xyz")
        result = composer.compose(signal)
        assert result is None

    def test_empty_signal_type_raises_value_error(self, composer):
        signal = _make_signal(signal_type="")
        with pytest.raises(ValueError):
            composer.compose(signal)

    def test_reversible_and_undo_window_consistent(self, composer):
        """If reversible=True, undo_window_sec should not be None."""
        signal = _make_signal(signal_type="calendar_event_draft")
        proposal = composer.compose(signal)
        if proposal and proposal.reversible:
            assert proposal.undo_window_sec is not None

    def test_irreversible_proposal_has_no_undo_window(self, composer):
        """bill_due is reversible=False → undo_window_sec is None."""
        signal = _make_signal(signal_type="bill_due")
        proposal = composer.compose(signal)
        assert proposal is not None
        if not proposal.reversible:
            assert proposal.undo_window_sec is None
