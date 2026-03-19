"""
tests/unit/test_trust_enforcer.py — Unit tests for TrustEnforcer.

Tests trust level gate enforcement, autonomy floor, elevation/demotion logic,
and health-check.md read/write.

Ref: specs/act.md §6
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from trust_enforcer import TrustEnforcer
from actions.base import ActionProposal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def health_check_path(tmp_path):
    """Create a health-check.md with a known autonomy block."""
    # TrustEnforcer expects artha_dir / "state" / "health-check.md"
    (tmp_path / "state").mkdir()
    p = tmp_path / "state" / "health-check.md"
    p.write_text(
        "# Health Check\n\n"
        "## Autonomy State\n\n"
        "```yaml\n"
        "autonomy:\n"
        "  trust_level: 1\n"
        "  trust_level_since: '2026-03-01'\n"
        "  days_at_level: 15\n"
        "  acceptance_rate_90d: 0.88\n"
        "  critical_false_positives: 0\n"
        "  pre_approved_categories: []\n"
        "  last_demotion: null\n"
        "  last_elevation: null\n"
        "```\n"
    )
    return p


@pytest.fixture
def enforcer(tmp_path, health_check_path):
    # health_check_path created state/health-check.md inside tmp_path
    return TrustEnforcer(tmp_path)


def _make_proposal(
    action_type: str = "email_send",
    friction: str = "standard",
    min_trust: int = 1,
    sensitivity: str = "standard",
    autonomy_floor: bool = False,
) -> ActionProposal:
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type=action_type,
        domain="test",
        title="Test",
        description="",
        parameters={},
        friction=friction,
        min_trust=min_trust,
        sensitivity=sensitivity,
        reversible=False,
        undo_window_sec=None,
        expires_at=None,
        source_step=None,
        source_skill=None,
        linked_oi=None,
    )


# ---------------------------------------------------------------------------
# Autonomy floor tests
# ---------------------------------------------------------------------------

class TestAutonomyFloor:
    def test_autonomy_floor_blocks_auto_approver(self, enforcer):
        """autonomy_floor=true + auto: approver → BLOCKED regardless of trust level."""
        proposal = _make_proposal(action_type="email_send", min_trust=0)
        action_cfg = {"autonomy_floor": True}
        allowed, reason = enforcer.check(proposal, "auto:L2", action_cfg)
        assert allowed is False
        assert "floor" in reason.lower() or "autonomy" in reason.lower()

    def test_autonomy_floor_allows_human_approver(self, enforcer):
        """autonomy_floor=true + user: approver → ALLOWED (trust=1, min_trust=1)."""
        proposal = _make_proposal(action_type="email_send", min_trust=1)
        action_cfg = {"autonomy_floor": True}
        allowed, reason = enforcer.check(proposal, "user:telegram", action_cfg)
        assert allowed is True

    def test_no_autonomy_floor_auto_approver_allowed_at_l2(self, enforcer):
        """autonomy_floor=false + auto:L2 + trust=1 + min_trust=0 → ALLOWED."""
        proposal = _make_proposal(action_type="reminder_create", min_trust=0, friction="low")
        action_cfg = {"autonomy_floor": False}
        allowed, reason = enforcer.check(proposal, "auto:L2", action_cfg)
        assert allowed is True


# ---------------------------------------------------------------------------
# Trust level threshold tests
# ---------------------------------------------------------------------------

class TestTrustLevelThreshold:
    def test_insufficient_trust_blocks(self, tmp_path):
        """trust_level=0, min_trust=1 → BLOCKED."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        hc = state_dir / "health-check.md"
        hc.write_text(
            "# Health Check\n\n"
            "## Autonomy State\n\n"
            "```yaml\n"
            "autonomy:\n"
            "  trust_level: 0\n"
            "  days_at_level: 0\n"
            "  acceptance_rate_90d: 0.0\n"
            "  critical_false_positives: 0\n"
            "  pre_approved_categories: []\n"
            "```\n"
        )
        enforcer = TrustEnforcer(tmp_path)
        proposal = _make_proposal(min_trust=1)
        allowed, reason = enforcer.check(proposal, "user:terminal", {})
        assert allowed is False
        assert "trust" in reason.lower()

    def test_sufficient_trust_allows(self, enforcer):
        """trust_level=1, min_trust=1, human approver → ALLOWED."""
        proposal = _make_proposal(min_trust=1)
        allowed, reason = enforcer.check(proposal, "user:terminal", {})
        assert allowed is True

    def test_l0_auto_blocked(self, enforcer):
        """L0 (observe) + auto: approver → BLOCKED."""
        proposal = _make_proposal(min_trust=0)
        action_cfg = {"autonomy_floor": False}
        # Write trust_level=0 to the health-check file
        hc = enforcer._health_check_path
        hc.write_text(
            "# Health Check\n\n"
            "## Autonomy State\n\n"
            "```yaml\n"
            "autonomy:\n"
            "  trust_level: 0\n"
            "  days_at_level: 0\n"
            "  acceptance_rate_90d: 0.0\n"
            "  critical_false_positives: 0\n"
            "  pre_approved_categories: []\n"
            "```\n"
        )
        enforcer_l0 = TrustEnforcer(enforcer._artha_dir)
        allowed, reason = enforcer_l0.check(proposal, "auto:L2", action_cfg)
        assert allowed is False


# ---------------------------------------------------------------------------
# Friction-based blocking
# ---------------------------------------------------------------------------

class TestFrictionBlocking:
    def test_auto_approver_high_friction_blocked(self, enforcer):
        """auto: + friction=high → BLOCKED regardless of trust level."""
        proposal = _make_proposal(friction="high", min_trust=0)
        action_cfg = {"autonomy_floor": False}
        allowed, reason = enforcer.check(proposal, "auto:L2", action_cfg)
        assert allowed is False
        assert "friction" in reason.lower()


# ---------------------------------------------------------------------------
# Demotion tests
# ---------------------------------------------------------------------------

class TestDemotion:
    def test_apply_demotion_resets_trust_level(self, enforcer, health_check_path):
        """apply_demotion() sets trust_level to 0 and increments false_positives."""
        enforcer.apply_demotion(reason="test demotion")
        content = health_check_path.read_text()
        assert "trust_level: 0" in content

    def test_apply_demotion_increments_critical_false_positives(
        self, enforcer, health_check_path
    ):
        enforcer.apply_demotion(reason="test demotion")
        content = health_check_path.read_text()
        assert "critical_false_positives: 1" in content


# ---------------------------------------------------------------------------
# Elevation evaluation
# ---------------------------------------------------------------------------

class TestElevationEvaluation:
    def test_evaluate_elevation_high_acceptance_rate(self, tmp_path):
        """High acceptance rate + 30 days at level → eligible for elevation."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        hc = state_dir / "health-check.md"
        hc.write_text(
            "# Health Check\n\n"
            "## Autonomy State\n\n"
            "```yaml\n"
            "autonomy:\n"
            "  trust_level: 0\n"
            "  days_at_level: 31\n"
            "  acceptance_rate_90d: 0.95\n"
            "  critical_false_positives: 0\n"
            "  pre_approved_categories: []\n"
            "```\n"
        )
        enforcer = TrustEnforcer(tmp_path)
        result = enforcer.evaluate_elevation()
        assert result["eligible"] is True

    def test_evaluate_elevation_not_enough_days(self, tmp_path):
        """Only 5 days at level → not eligible for elevation."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        hc = state_dir / "health-check.md"
        hc.write_text(
            "# Health Check\n\n"
            "## Autonomy State\n\n"
            "```yaml\n"
            "autonomy:\n"
            "  trust_level: 0\n"
            "  days_at_level: 5\n"
            "  acceptance_rate_90d: 0.95\n"
            "  critical_false_positives: 0\n"
            "  pre_approved_categories: []\n"
            "```\n"
        )
        enforcer = TrustEnforcer(tmp_path)
        result = enforcer.evaluate_elevation()
        assert result["eligible"] is False

    def test_evaluate_elevation_critical_false_positives_blocks(self, tmp_path):
        """Critical false positives block elevation."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        hc = state_dir / "health-check.md"
        hc.write_text(
            "# Health Check\n\n"
            "## Autonomy State\n\n"
            "```yaml\n"
            "autonomy:\n"
            "  trust_level: 0\n"
            "  days_at_level: 45\n"
            "  acceptance_rate_90d: 0.98\n"
            "  critical_false_positives: 1\n"
            "  pre_approved_categories: []\n"
            "```\n"
        )
        enforcer = TrustEnforcer(tmp_path)
        result = enforcer.evaluate_elevation()
        assert result["eligible"] is False
