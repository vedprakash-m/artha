"""
tests/unit/test_email_classifier.py — Unit tests for scripts/email_classifier.py

Coverage:
  - classify_email marks non-marketing email as marketing=False
  - classify_email marks promotional email as marketing=True + marketing_category
  - classify_email marks newsletter sender as marketing=True
  - classify_email: trusted domain (whitelist) overrides marketing patterns
  - classify_email: important subject (invoice) overrides marketing sender
  - classify_email: custom whitelist parameter works
  - classify_email: auto-notification subject is not marketing
  - classify_email: marketing header triggers classification
  - classify_email preserves all original record fields
  - classify_records: non-email records pass through unclassified
  - classify_records: email records get classified
  - classify_records: mixed batch processes only email-type records
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from email_classifier import classify_email, classify_records


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    sender: str = "alice@example.com",
    subject: str = "Hello",
    body: str = "Here's a message",
    rtype: str = "email",
) -> dict:
    return {
        "id": "msg-001",
        "from": sender,
        "subject": subject,
        "body": body,
        "date": "2025-01-01",
        "type": rtype,
    }


# ---------------------------------------------------------------------------
# Non-marketing classification
# ---------------------------------------------------------------------------

class TestNonMarketing:
    def test_personal_sender_not_marketing(self):
        record = _make_record(sender="alice@gmail.com", subject="Are you free Tuesday?")
        result = classify_email(record)
        assert result["marketing"] is False

    def test_marketing_field_added_to_record(self):
        record = _make_record()
        result = classify_email(record)
        assert "marketing" in result

    def test_original_fields_preserved(self):
        record = _make_record(sender="bob@domain.com", subject="Happy holidays!")
        result = classify_email(record)
        assert result["id"] == "msg-001"
        assert result["from"] == "bob@domain.com"
        assert result["date"] == "2025-01-01"

    def test_important_subject_invoice_not_marketing(self):
        """Invoice subject overrides marketing sender."""
        record = _make_record(
            sender="newsletter@deals.example.com",
            subject="Your invoice #12345 is ready",
        )
        result = classify_email(record)
        assert result["marketing"] is False

    def test_important_subject_shipment_not_marketing(self):
        record = _make_record(
            sender="noreply@bigstore.example.com",
            subject="Your shipment has been dispatched",
        )
        result = classify_email(record)
        assert result["marketing"] is False

    def test_trusted_domain_not_marketing(self):
        """chase.com is in _IMPORTANT_SENDER_DOMAINS."""
        record = _make_record(sender="alerts@chase.com", subject="Account alert")
        result = classify_email(record)
        assert result["marketing"] is False

    def test_trusted_domain_no_marketing_category(self):
        record = _make_record(sender="statements@irs.gov", subject="Notice")
        result = classify_email(record)
        assert result.get("marketing_category") is None

    def test_custom_whitelist_overrides_marketing(self):
        """Custom whitelist entry prevents marketing classification."""
        record = _make_record(
            sender="news@mycompany.com",
            subject="Weekly newsletter digest",
        )
        result = classify_email(record, custom_whitelist=["mycompany.com"])
        assert result["marketing"] is False

    def test_auto_notification_github_not_marketing(self):
        record = _make_record(
            sender="noreply@github.com",
            subject="New comment on PR #42",
        )
        result = classify_email(record)
        assert result["marketing"] is False


# ---------------------------------------------------------------------------
# Marketing classification
# ---------------------------------------------------------------------------

class TestMarketing:
    def test_newsletter_sender_pattern_is_marketing(self):
        """Sender matching Mailchimp subdomain pattern → marketing=True."""
        # Pattern requires subdomain: @<anything>.mailchimp.com
        record = _make_record(sender="updates@e.mailchimp.com")
        result = classify_email(record)
        assert result["marketing"] is True

    def test_promotional_subject_is_marketing(self):
        """Flash sale subject triggers promotional category."""
        record = _make_record(
            sender="info@randomretailer.example.com",
            subject="Flash sale ends tonight — 50% off everything",
        )
        result = classify_email(record)
        assert result["marketing"] is True

    def test_marketing_category_set_on_marketing(self):
        record = _make_record(
            sender="info@randomretailer.example.com",
            subject="Flash sale ends tonight",
        )
        result = classify_email(record)
        assert "marketing_category" in result
        assert isinstance(result["marketing_category"], str)

    def test_newsletter_subject_pattern(self):
        record = _make_record(
            sender="digest@someone.example.com",
            subject="Weekly Digest #47",
        )
        result = classify_email(record)
        assert result["marketing"] is True

    def test_unsubscribe_in_subject_is_marketing(self):
        record = _make_record(
            sender="deals@promo.example.com",
            subject="Click here to unsubscribe",
        )
        result = classify_email(record)
        assert result["marketing"] is True

    def test_marketing_header_triggers_classification(self):
        """List-Unsubscribe header → marketing."""
        record = {
            "id": "msg-002",
            "from": "info@brandco.example.com",
            "subject": "Check out our store",
            "body": "",
            "date": "2025-01-01",
            "headers": {"list-unsubscribe": "<mailto:unsubscribe@brandco.example.com>"},
        }
        result = classify_email(record)
        assert result["marketing"] is True

    def test_noreply_sender_is_marketing(self):
        """noreply@ pattern matches _MARKETING_SENDER_PATTERNS."""
        record = _make_record(
            sender="noreply.updates@news.example.com",
            subject="This week's roundup",
        )
        result = classify_email(record)
        # Sender matches "noreply" pattern OR subject matches newsletter
        assert result["marketing"] is True


# ---------------------------------------------------------------------------
# classify_records batch function
# ---------------------------------------------------------------------------

class TestClassifyRecords:
    def test_email_records_get_classified(self):
        records = [
            _make_record(sender="alice@gmail.com", subject="Hello", rtype="email"),
        ]
        result = classify_records(records)
        assert "marketing" in result[0]

    def test_non_email_records_pass_through_without_marketing_field(self):
        records = [
            {"id": "cal-001", "type": "calendar", "title": "Team meeting"},
        ]
        result = classify_records(records)
        assert "marketing" not in result[0]

    def test_mixed_batch_only_classifies_email_type(self):
        records = [
            _make_record(rtype="email"),
            {"id": "sys-1", "type": "system_status", "message": "OK"},
        ]
        result = classify_records(records)
        assert "marketing" in result[0]
        assert "marketing" not in result[1]

    def test_records_with_source_email_get_classified(self):
        """Records with 'email' in source field also get classified."""
        record = {
            "id": "msg-003",
            "source": "gmail_email",
            "from": "news@bigretailer.example.com",
            "subject": "Sale ends today",
            "body": "",
        }
        result = classify_records([record])
        assert "marketing" in result[0]

    def test_returns_same_list_object(self):
        records = [_make_record()]
        result = classify_records(records)
        assert result is records
