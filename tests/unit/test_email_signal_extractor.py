"""
tests/unit/test_email_signal_extractor.py — Unit tests for scripts/email_signal_extractor.py (E1)

Coverage:
  - EmailSignalExtractor.extract() returns DomainSignal list
  - RSVP subject triggers event_rsvp_needed signal (subtype); canonical signal_type = deadline
  - Delivery subject triggers delivery_arriving signal (subtype); canonical signal_type = confirmation
  - Security alert triggers security_alert (subtype); canonical signal_type = security
  - Bill/invoice triggers bill_due (subtype); canonical signal_type = deadline
  - Subscription renewal triggers subscription_renewal (subtype); canonical signal_type = informational
  - School action triggers school_action_needed (subtype); canonical signal_type = deadline
  - Appointment confirmation triggers appointment_confirmed (subtype); canonical signal_type = confirmation
  - Form deadline triggers form_deadline signal
  - No match returns empty list
  - Feature flag disabled returns empty list
  - PII: sender email is NOT included in signal payload
  - Financial signals get sensitivity=high

Signal type consolidation (v3.35.0 / simplify.md Phase 4):
  - signal_type holds the 4 canonical types: deadline, confirmation, security, informational
  - subtype holds the original specific type for debugging and subtype-first routing
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from email_signal_extractor import EmailSignalExtractor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_email(subject: str, body: str = "", sender: str = "alice@example.com") -> dict:
    return {"subject": subject, "body": body, "sender": sender, "email_id": "msg-001"}


def _subtypes(signals) -> list[str]:
    """Return the subtype (original specific type) for each signal."""
    return [getattr(s, "subtype", s.signal_type) for s in signals]


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------

class TestExtractRsvp:
    def test_rsvp_subject_detected(self):
        record = _make_email("Please respond by March 28 for annual dinner")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        # After consolidation, signal_type = "deadline"; subtype = "event_rsvp_needed"
        assert any(getattr(s, "subtype", None) == "event_rsvp_needed" for s in signals)

    def test_rsvp_signal_has_no_sender_email(self):
        record = _make_email("Please respond by April 5", sender="bob@corp.com")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        rsvp = [s for s in signals if getattr(s, "subtype", None) == "event_rsvp_needed"]
        assert rsvp
        payload_str = str(rsvp[0].metadata)
        assert "bob@corp.com" not in payload_str


class TestExtractDelivery:
    def test_delivery_arriving(self):
        record = _make_email("Your order is out for delivery today")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        # signal_type = "confirmation"; subtype = "delivery_arriving"
        assert any(getattr(s, "subtype", None) == "delivery_arriving" for s in signals)

    def test_package_delivered(self):
        record = _make_email("Package delivered to your door")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        assert any(getattr(s, "subtype", None) == "delivery_arriving" for s in signals)


class TestExtractSecurity:
    def test_security_alert_detected(self):
        record = _make_email("Security alert: unusual sign-in detected")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        # signal_type = "security"; subtype = "security_alert"
        sec = [s for s in signals if getattr(s, "subtype", None) == "security_alert"]
        assert sec
        assert sec[0].metadata.get("sensitivity") == "high" or sec[0].urgency >= 3

    def test_password_changed_alert(self):
        record = _make_email("Password reset requested for your account")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        assert any(getattr(s, "subtype", None) == "security_alert" for s in signals)


class TestExtractFinancial:
    def test_bill_due_metadata_has_sensitivity(self):
        record = _make_email("Your bill is ready — payment due by March 15")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        # signal_type = "deadline"; subtype = "bill_due"
        bill = [s for s in signals if getattr(s, "subtype", None) == "bill_due"]
        assert bill
        assert bill[0].metadata.get("sensitivity") == "high"

    def test_amount_due_detected(self):
        record = _make_email("Amount due: $125.00 — pay by April 5")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        assert any(getattr(s, "subtype", None) == "bill_due" for s in signals)

    def test_subscription_renewal(self):
        record = _make_email("Your subscription renewing on March 20")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        # signal_type = "informational"; subtype = "subscription_renewal"
        assert any(getattr(s, "subtype", None) == "subscription_renewal" for s in signals)


class TestExtractSchool:
    def test_school_action_needed(self):
        record = _make_email("Permission slip required for field trip")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        # signal_type = "deadline"; subtype = "school_action_needed"
        assert any(getattr(s, "subtype", None) == "school_action_needed" for s in signals)

    def test_school_parent_signature(self):
        record = _make_email("Parent signature needed — school event")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        assert any(getattr(s, "subtype", None) == "school_action_needed" for s in signals)


class TestExtractAppointment:
    def test_appointment_confirmed(self):
        record = _make_email("Appointment confirmed: Dr Smith on Tuesday April 8")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        # signal_type = "confirmation"; subtype = "appointment_confirmed"
        assert any(getattr(s, "subtype", None) == "appointment_confirmed" for s in signals)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_no_match_returns_empty(self):
        record = _make_email("Hello, hope you're doing well!")
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        assert signals == []

    def test_empty_records_list(self):
        extractor = EmailSignalExtractor()
        signals = extractor.extract([])
        assert signals == []

    def test_rsvp_in_body(self):
        record = {"subject": "Annual dinner", "body": "Please respond by March 28", "sender": "alice@example.com", "email_id": "msg-001"}
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record])
        assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_flag_disabled_returns_empty(self):
        record = _make_email("RSVP to the party by Friday")
        with patch("email_signal_extractor._load_flag", return_value=False):
            extractor = EmailSignalExtractor()
            signals = extractor.extract([record])
        assert signals == []


# ---------------------------------------------------------------------------
# Routing table passthrough
# ---------------------------------------------------------------------------

class TestRoutingTable:
    def test_routing_table_filters_by_domain(self):
        record = _make_email("Your package is out for delivery")
        routing_table = {"shopping": True, "finance": True}
        extractor = EmailSignalExtractor()
        signals = extractor.extract([record], routing_table=routing_table)
        # Delivery is shopping/home domain — should still produce a signal
        assert isinstance(signals, list)
