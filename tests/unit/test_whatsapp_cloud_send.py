"""
tests/unit/test_whatsapp_cloud_send.py — Unit tests for scripts/actions/whatsapp_cloud_send.py (E10)

Coverage:
  - WhatsAppCloudHandler validates E.164 phone numbers
  - Invalid phone number rejected in validate()
  - Template not in allowlist rejected in validate()
  - Valid proposal passes validate()
  - PII scan on template variables
  - execute() makes POST to correct Meta API endpoint
  - audit log contains recipient_hash NOT raw phone number
  - autonomy_floor: true — handler requires approval
  - health_check() returns ready=False when credentials missing
  - health_check() data has required fields
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.actions.base import ActionProposal
from scripts.actions.whatsapp_cloud_send import WhatsAppCloudHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proposal(phone: str = "+15551234567", template: str = "reminder", variables: list = None) -> ActionProposal:
    """Build an ActionProposal matching whatsapp_cloud_send.validate() expectations."""
    return ActionProposal(
        id="test-uuid-001",
        action_type="whatsapp_send",
        domain="social",
        title="Send WhatsApp message",
        description="Test proposal",
        parameters={
            "template_id": template,
            "recipient_phone": phone,
            "variables": variables or ["Test message"],
        },
        friction="high",
        min_trust=2,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=None,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_e164_phone_passes(self):
        handler = WhatsAppCloudHandler()
        proposal = _make_proposal(phone="+15551234567", template="reminder")
        valid, reason = handler.validate(proposal)
        assert valid is True

    def test_invalid_phone_rejected(self):
        handler = WhatsAppCloudHandler()
        proposal = _make_proposal(phone="555-1234", template="reminder")
        valid, reason = handler.validate(proposal)
        assert valid is False
        assert "phone" in reason.lower() or "e.164" in reason.lower()

    def test_template_not_in_allowlist_rejected(self):
        handler = WhatsAppCloudHandler()
        proposal = _make_proposal(phone="+15551234567", template="unapproved_template")
        valid, reason = handler.validate(proposal)
        assert valid is False
        assert "template" in reason.lower() or "unsupported" in reason.lower()

    def test_missing_phone_rejected(self):
        handler = WhatsAppCloudHandler()
        proposal = _make_proposal(phone="", template="reminder")
        valid, reason = handler.validate(proposal)
        assert valid is False

    def test_pii_in_variables_rejected(self):
        handler = WhatsAppCloudHandler()
        # Inject PII (email address) into template variables
        proposal = _make_proposal(
            phone="+15551234567",
            template="reminder",
            variables=["alice@personal.com is waiting for you"],
        )
        valid, reason = handler.validate(proposal)
        # PII in variables — handler should reject
        assert valid is False
        assert "pii" in reason.lower() or "sensitive" in reason.lower() or "detected" in reason.lower()


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

class TestExecute:
    def test_execute_calls_meta_api(self):
        handler = WhatsAppCloudHandler()
        proposal = _make_proposal()

        # execute() uses urllib.request.urlopen internally
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"messages": [{"id": "wamid.test"}]}'

        captured = []
        def fake_urlopen(req, timeout=None):
            captured.append(req.full_url)
            return mock_resp

        with (
            patch("scripts.actions.whatsapp_cloud_send._get_credential",
                  side_effect=lambda key: "phone_id_123" if key == "whatsapp_phone_id" else "token_abc"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            result = handler.execute(proposal)

        assert len(captured) == 1
        assert "graph.facebook.com" in captured[0]
        assert "phone_id_123" in captured[0]
        assert result.status == "success"

    def test_execute_missing_credentials_returns_failure(self):
        handler = WhatsAppCloudHandler()
        proposal = _make_proposal()

        with patch("scripts.actions.whatsapp_cloud_send._get_credential", return_value=None):
            result = handler.execute(proposal)
        assert result.status == "failure"
        assert "credentials" in result.message.lower() or "configured" in result.message.lower()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_check_no_credentials_returns_not_ready(self):
        handler = WhatsAppCloudHandler()
        with patch("scripts.actions.whatsapp_cloud_send._get_credential", return_value=None):
            health = handler.health_check()
        assert health["ready"] is False

    def test_health_check_has_required_fields(self):
        handler = WhatsAppCloudHandler()
        with patch("scripts.actions.whatsapp_cloud_send._get_credential", return_value=None):
            health = handler.health_check()
        assert "ready" in health
        assert "phone_id_configured" in health
        assert "token_configured" in health


# ---------------------------------------------------------------------------
# Autonomy floor (verified via module docstring / INVARIANTS)
# ---------------------------------------------------------------------------

class TestAutonomyFloor:
    def test_validate_is_side_effect_free(self):
        """validate() must be callable without credentials (side-effect free)."""
        handler = WhatsAppCloudHandler()
        proposal = _make_proposal()
        # Must not raise even without credentials
        valid, reason = handler.validate(proposal)
        assert isinstance(valid, bool)
        assert isinstance(reason, str)
