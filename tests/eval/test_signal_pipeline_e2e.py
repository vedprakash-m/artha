"""tests/eval/test_signal_pipeline_e2e.py — DEBT-021: Signal pipeline fidelity.

Fixture-based end-to-end test: synthetic email JSONL → expected DomainSignal objects.
Confirms each pattern category fires correctly and produces the right domain/type.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

from email_signal_extractor import EmailSignalExtractor  # type: ignore[import]


# ---------------------------------------------------------------------------
# Synthetic email fixtures (no real PII — org names are fictional/public)
# ---------------------------------------------------------------------------

_FIXTURES: list[tuple[str, dict, str, str]] = [
    # (test_id, email_record, expected_signal_type, expected_domain)
    (
        "bill_due_payment_notice",
        {
            "id": "f001",
            "subject": "Your payment is due — Action required",
            "from": "billing@utilityco.example",
            "snippet": "Your payment due amount is $87.45. Please pay by January 15.",
        },
        "bill_due",
        "finance",
    ),
    (
        "appointment_confirmed",
        {
            "id": "f002",
            "subject": "Appointment confirmed for Monday",
            "from": "noreply@clinic.example",
            "snippet": "Your appointment confirmed for January 20 at 10am.",
        },
        "appointment_confirmed",
        "calendar",
    ),
    (
        "event_rsvp_needed",
        {
            "id": "f003",
            "subject": "Please respond by December 5",
            "from": "events@community.example",
            "snippet": "We need a headcount. Please respond by December 5.",
        },
        "event_rsvp_needed",
        "calendar",
    ),
    (
        "security_alert",
        {
            "id": "f004",
            "subject": "Unusual sign-in detected",
            "from": "security@authprovider.example",
            "snippet": "We detected an unusual sign-in to your account. Was this you?",
        },
        "security_alert",
        "digital",
    ),
    (
        "subscription_renewal",
        {
            "id": "f005",
            "subject": "Your subscription is renewing next week",
            "from": "billing@saas.example",
            "snippet": "Your plan will renew on January 30. No action needed.",
        },
        "subscription_renewal",
        "digital",
    ),
    (
        "delivery_arriving",
        {
            "id": "f006",
            "subject": "Your package is out for delivery",
            "from": "tracking@courier.example",
            "snippet": "Your package is out for delivery and should arrive today.",
        },
        "delivery_arriving",
        "shopping",
    ),
    (
        "form_deadline",
        {
            "id": "f007",
            "subject": "Application deadline reminder",
            "from": "admissions@school.example",
            "snippet": "Reminder: application deadline is January 31. Submit by then.",
        },
        "form_deadline",
        "comms",
    ),
]


class TestSignalPipelineE2E:
    @pytest.fixture(autouse=True)
    def extractor(self):
        self._extractor = EmailSignalExtractor()

    @pytest.mark.parametrize("test_id,email,expected_type,expected_domain", _FIXTURES, ids=[f[0] for f in _FIXTURES])
    def test_pattern_fires(self, test_id, email, expected_type, expected_domain):
        """Each synthetic email must produce at least one signal of the expected type."""
        signals = self._extractor.extract([email])
        signal_types = [s.signal_type for s in signals]
        assert expected_type in signal_types, (
            f"[{test_id}] Expected signal_type='{expected_type}' but got {signal_types}"
        )

    @pytest.mark.parametrize("test_id,email,expected_type,expected_domain", _FIXTURES, ids=[f[0] for f in _FIXTURES])
    def test_domain_correct(self, test_id, email, expected_type, expected_domain):
        """Signals must have the correct domain."""
        signals = self._extractor.extract([email])
        matching = [s for s in signals if s.signal_type == expected_type]
        assert matching, f"[{test_id}] No signals of type '{expected_type}' found"
        assert matching[0].domain == expected_domain, (
            f"[{test_id}] Expected domain='{expected_domain}', got '{matching[0].domain}'"
        )

    @pytest.mark.parametrize("test_id,email,expected_type,expected_domain", _FIXTURES, ids=[f[0] for f in _FIXTURES])
    def test_signal_fields_valid(self, test_id, email, expected_type, expected_domain):
        """DomainSignal fields must pass structural invariants."""
        signals = self._extractor.extract([email])
        matching = [s for s in signals if s.signal_type == expected_type]
        assert matching
        sig = matching[0]
        assert 0 <= sig.urgency <= 3, f"[{test_id}] urgency out of range: {sig.urgency}"
        assert 0 <= sig.impact <= 3, f"[{test_id}] impact out of range: {sig.impact}"
        assert sig.source == "email_signal_extractor"
        assert isinstance(sig.metadata, dict)

    def test_no_signals_for_generic_email(self):
        """A generic newsletter produces no actionable signals."""
        email = {
            "id": "f999",
            "subject": "Weekly digest: top articles this week",
            "from": "newsletter@mediaco.example",
            "snippet": "Here are this week's top articles for you to read.",
        }
        signals = self._extractor.extract([email])
        assert signals == [], f"Expected no signals, got {[s.signal_type for s in signals]}"

    def test_dedup_across_identical_records(self):
        """Two identical email records produce only one signal (dedup)."""
        email = {
            "id": "f100",
            "subject": "Payment due now",
            "from": "billing@corp.example",
            "snippet": "Amount due is $50. Please pay by March 1.",
        }
        signals = self._extractor.extract([email, email])
        bill_signals = [s for s in signals if s.signal_type == "bill_due"]
        assert len(bill_signals) == 1, f"Expected 1 deduped signal, got {len(bill_signals)}"
