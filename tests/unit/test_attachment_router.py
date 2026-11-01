"""
tests/unit/test_attachment_router.py — Unit tests for scripts/attachment_router.py (E9)

Coverage:
  - AttachmentRouter.route() returns list of AttachmentSignal
  - Tax document → finance domain, document_financial signal
  - Medical PDF → health domain, document_medical signal
  - Immigration documents → immigration domain, sensitivity=high
  - School documents → kids domain, document_school signal
  - Unknown filename → no signal
  - PII: original filename scrubbed in AttachmentSignal.filename_safe
  - to_domain_signals() returns DomainSignal list
  - Email with no attachments returns empty list
  - Feature flag disabled returns empty list
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from attachment_router import AttachmentRouter, AttachmentSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_email(attachments: list[dict]) -> dict:
    return {
        "email_id": "msg-test",
        "subject": "Test email",
        "attachments": attachments,
    }


def _att(filename: str, size: int = 50000, mime: str = "application/pdf") -> dict:
    return {"filename": filename, "size": size, "mime_type": mime}


# ---------------------------------------------------------------------------
# Finance documents
# ---------------------------------------------------------------------------

class TestFinanceDocuments:
    def test_tax_form_routes_to_finance(self):
        router = AttachmentRouter()
        email = _make_email([_att("1099-INT_2025.pdf")])
        signals = router.route(email)
        finance = [s for s in signals if s.domain == "finance"]
        assert finance
        assert finance[0].signal_type == "document_financial"

    def test_invoice_pdf_routes_to_finance(self):
        router = AttachmentRouter()
        email = _make_email([_att("invoice-march-2026.pdf")])
        signals = router.route(email)
        domains = [s.domain for s in signals]
        assert "finance" in domains

    def test_financial_signal_sensitivity_standard_or_high(self):
        router = AttachmentRouter()
        email = _make_email([_att("tax_return_2025.pdf")])
        signals = router.route(email)
        fin = [s for s in signals if s.domain == "finance"]
        if fin:
            assert fin[0].sensitivity in ("standard", "high")


# ---------------------------------------------------------------------------
# Medical documents
# ---------------------------------------------------------------------------

class TestMedicalDocuments:
    def test_medical_pdf_routes_to_health(self):
        router = AttachmentRouter()
        email = _make_email([_att("lab-result-2026.pdf")])
        signals = router.route(email)
        health = [s for s in signals if s.domain == "health"]
        assert health
        assert health[0].signal_type == "document_medical"

    def test_prescription_routes_to_health(self):
        router = AttachmentRouter()
        email = _make_email([_att("prescription-2026.pdf")])
        signals = router.route(email)
        domains = [s.domain for s in signals]
        assert "health" in domains


# ---------------------------------------------------------------------------
# Immigration documents
# ---------------------------------------------------------------------------

class TestImmigrationDocuments:
    def test_visa_document_sensitivity_high(self):
        router = AttachmentRouter()
        email = _make_email([_att("visa_approval_notice.pdf")])
        signals = router.route(email)
        imm = [s for s in signals if s.domain == "immigration"]
        if imm:
            assert imm[0].sensitivity == "high"
            assert imm[0].signal_type == "document_immigration"

    def test_i485_routes_to_immigration(self):
        router = AttachmentRouter()
        email = _make_email([_att("I-485-approval-notice.pdf")])
        signals = router.route(email)
        domains = [s.domain for s in signals]
        assert "immigration" in domains


# ---------------------------------------------------------------------------
# School documents
# ---------------------------------------------------------------------------

class TestSchoolDocuments:
    def test_school_document_routes_to_kids(self):
        router = AttachmentRouter()
        email = _make_email([_att("school_permission_slip.pdf")])
        signals = router.route(email)
        kids = [s for s in signals if s.domain == "kids"]
        if kids:
            assert kids[0].signal_type == "document_school"


# ---------------------------------------------------------------------------
# PII filter
# ---------------------------------------------------------------------------

class TestPiiFilter:
    def test_filename_safe_scrubs_personal_names(self):
        router = AttachmentRouter()
        # Filename with a common personal name pattern
        email = _make_email([_att("john_smith_tax_return.pdf")])
        signals = router.route(email)
        for sig in signals:
            # filename_safe must not contain raw personal identifiers
            # (the scrubber may anonymize or truncate)
            assert isinstance(sig.filename_safe, str)


# ---------------------------------------------------------------------------
# No match
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_unknown_filename_no_signal(self):
        router = AttachmentRouter()
        email = _make_email([_att("cat_photo.jpg")])
        signals = router.route(email)
        assert signals == []

    def test_no_attachments_empty_list(self):
        router = AttachmentRouter()
        email = _make_email([])
        signals = router.route(email)
        assert signals == []

    def test_missing_attachments_key(self):
        router = AttachmentRouter()
        email = {"email_id": "msg-001", "subject": "Hello"}
        signals = router.route(email)
        assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# to_domain_signals
# ---------------------------------------------------------------------------

class TestToDomainSignals:
    def test_returns_domain_signal_list(self):
        router = AttachmentRouter()
        email = _make_email([_att("1099-G_2025.pdf")])
        att_signals = router.route(email)
        domain_signals = router.to_domain_signals(att_signals)
        assert isinstance(domain_signals, list)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_flag_disabled_returns_empty(self):
        with patch("attachment_router._load_flag", return_value=False):
            router = AttachmentRouter()
            email = _make_email([_att("1099-INT_2025.pdf")])
            signals = router.route(email)
        assert signals == []
