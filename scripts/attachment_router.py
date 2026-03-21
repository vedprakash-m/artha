#!/usr/bin/env python3
# pii-guard: filenames may contain PII — pii_guard applied to all signal metadata
"""
scripts/attachment_router.py — Email attachment metadata router (E9, Phase 1).

Phase 1: Metadata-only routing (filename + mime-type → domain + signal_type).
Does NOT download, open, or parse attachment content.

Runs at Step 5e (after 5c body truncation, before Step 6 routing).
Emits AttachmentSignal objects that downstream code converts to DomainSignal.

Routing rules (filename patterns → domain, sensitivity, signal_type):
  invoice|bill|statement                  → finance     / standard   / document_financial
  w2|w-2|1099|tax_return|tax_form        → finance     / high        / document_financial
  prescription|lab_result|eob|lab_report → health      / high        / document_medical
  report_card|transcript|iep|school      → kids        / standard    / document_school
  passport|visa|i-485|i-765|i-140|ead    → immigration / high        / document_immigration
  insurance|policy|claim|eob             → insurance   / standard    / document_insurance
  lease|deed|closing|mortgage            → home        / standard    / document_property
  pay_stub|paystub|offer_letter|w4       → employment  / high        / document_financial

Privacy:
  - Phase 1: filenames only — no content extraction
  - Filenames PII-filtered before metadata stored
  - Immigration/financial signals get sensitivity: high (encrypted in ActionQueue)
  - Email addresses never stored in signals

Config flag: enhancements.attachment_router (default: true)

Ref: specs/act-reloaded.md Enhancement 9
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AttachmentSignal:
    email_id: str
    filename_safe: str        # PII-filtered filename (no raw email addresses)
    domain: str
    sensitivity: str          # standard | high
    signal_type: str
    mime_type: str
    size_bytes: int
    detected_at: str


# ---------------------------------------------------------------------------
# Routing rules (compiled regexes → (domain, sensitivity, signal_type))
# ---------------------------------------------------------------------------

_ROUTING_RULES: list[tuple[re.Pattern, str, str, str]] = [
    # pattern, domain, sensitivity, signal_type
    # Tax / payroll (high sensitivity)
    (re.compile(r"\b(w[-_]?2|1099|tax.return|tax.form|w[-_]?4|paystub|pay.stub|offer.letter)\b", re.I),
     "finance", "high", "document_financial"),
    # Generic finance
    (re.compile(r"\b(invoice|bill|statement|receipt|payment)\b", re.I),
     "finance", "standard", "document_financial"),
    # Immigration (all high sensitivity)
    (re.compile(r"\b(passport|visa|i-?485|i-?765|i-?140|ead|lca|naturali[sz]ation|i-?131)\b", re.I),
     "immigration", "high", "document_immigration"),
    # Medical
    (re.compile(r"\b(prescription|lab.result|lab.report|eob|explanation.of.benefit|medical.record|mri|x.ray)\b", re.I),
     "health", "high", "document_medical"),
    # School / kids
    (re.compile(r"\b(report.card|transcript|iep|school|enrollment|permission.slip)\b", re.I),
     "kids", "standard", "document_school"),
    # Insurance
    (re.compile(r"\b(insurance|policy|claim|renewal|coverage|premium|deductible)\b", re.I),
     "insurance", "standard", "document_insurance"),
    # Property / home
    (re.compile(r"\b(lease|deed|closing|mortgage|hoa|property)\b", re.I),
     "home", "standard", "document_property"),
    # Employment
    (re.compile(r"\b(pay.stub|paystub|offer.letter|employment.contract|nda|severance)\b", re.I),
     "employment", "high", "document_financial"),
]

# Mime types that indicate actual document attachments (not inline images)
_DOCUMENT_MIME_TYPES: frozenset[str] = frozenset([
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/jpeg",           # scanned docs
    "image/png",
    "image/tiff",
    "text/plain",
    "application/zip",
    "application/octet-stream",
])

# Minimum file size to consider a real attachment (ignore 1-pixel tracking images)
_MIN_SIZE_BYTES = 1_000

# PII patterns to remove from filenames before storing
_PII_FILENAME_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[a-z]{2,}|"   # email address
    r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b|"  # SSN pattern
    r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b",    # phone
    re.I,
)


def _filter_filename(filename: str) -> str:
    """Remove PII-like patterns from filenames before storing in signals."""
    safe = _PII_FILENAME_RE.sub("[REDACTED]", filename)
    # Keep only the base name (no path traversal risk)
    return Path(safe).name[:120]


def _classify_filename(filename: str) -> tuple[str, str, str] | None:
    """Return (domain, sensitivity, signal_type) for a filename, or None if no match."""
    fn_lower = filename.lower()
    for pattern, domain, sensitivity, signal_type in _ROUTING_RULES:
        if pattern.search(fn_lower):
            return domain, sensitivity, signal_type
    return None


# ---------------------------------------------------------------------------
# AttachmentRouter
# ---------------------------------------------------------------------------

class AttachmentRouter:
    """Routes email attachment metadata to domain signals.

    Usage:
        router = AttachmentRouter()
        signals = router.route(email_record)
    """

    def route(self, email_record: dict) -> list[AttachmentSignal]:
        """Extract attachment metadata from an email JSONL record and classify it.

        Args:
            email_record: Email dict from the Gmail pipeline. Expected structure:
                {
                  "id": "...",
                  "attachments": [
                    {"filename": "invoice_march.pdf", "mime_type": "application/pdf",
                     "size_bytes": 45_000}
                  ]
                }

        Returns:
            List of AttachmentSignal objects for any matched attachments.
            Empty list if no attachments or no rule matches.
        """
        if not _load_flag("enhancements.attachment_router", default=True):
            return []

        if not isinstance(email_record, dict):
            return []

        email_id = str(email_record.get("id", email_record.get("message_id", "unknown")))
        attachments = email_record.get("attachments", email_record.get("parts", []))
        if not isinstance(attachments, list):
            return []

        signals: list[AttachmentSignal] = []
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for att in attachments:
            if not isinstance(att, dict):
                continue

            filename = str(att.get("filename", att.get("name", "")) or "").strip()
            if not filename:
                continue

            mime_type = str(att.get("mime_type", att.get("mimeType", "")) or "").strip().lower()
            size_bytes = int(att.get("size_bytes", att.get("size", att.get("body_size", 0))) or 0)

            # Skip tiny files (tracking pixels, etc.)
            if size_bytes < _MIN_SIZE_BYTES and mime_type.startswith("image/"):
                continue

            # Only process document mime types or unknown (allow unknown to catch PDFs sent as octet-stream)
            if mime_type and mime_type not in _DOCUMENT_MIME_TYPES and not mime_type.startswith("application/"):
                continue

            match = _classify_filename(filename)
            if match is None:
                continue

            domain, sensitivity, signal_type = match
            filename_safe = _filter_filename(filename)

            signals.append(AttachmentSignal(
                email_id=email_id,
                filename_safe=filename_safe,
                domain=domain,
                sensitivity=sensitivity,
                signal_type=signal_type,
                mime_type=mime_type,
                size_bytes=size_bytes,
                detected_at=now_iso,
            ))

        return signals

    def to_domain_signals(
        self,
        attachment_signals: list[AttachmentSignal],
    ) -> list[object]:
        """Convert AttachmentSignals to DomainSignal objects for ActionComposer.

        Returns DomainSignal instances (imported dynamically to avoid hard dep).
        """
        try:
            from actions.base import DomainSignal  # type: ignore[import]
        except ImportError:
            return []  # pragma: no cover

        result = []
        for att in attachment_signals:
            meta = {
                "filename": att.filename_safe,
                "mime_type": att.mime_type,
                "size_bytes": att.size_bytes,
            }
            if att.sensitivity == "high":
                meta["sensitivity"] = "high"

            urgency = 2 if att.sensitivity == "high" else 1
            result.append(DomainSignal(
                signal_type=att.signal_type,
                domain=att.domain,
                entity=att.filename_safe,
                urgency=urgency,
                impact=urgency,
                source="attachment_router",
                metadata=meta,
                detected_at=att.detected_at,
            ))

        return result


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    sample = {
        "id": "test-001",
        "attachments": [
            {"filename": "invoice_march_2026.pdf", "mime_type": "application/pdf", "size_bytes": 45_000},
            {"filename": "I-485_receipt_notice.pdf", "mime_type": "application/pdf", "size_bytes": 120_000},
            {"filename": "tracking.gif", "mime_type": "image/gif", "size_bytes": 43},
        ],
    }
    router = AttachmentRouter()
    signals = router.route(sample)
    print(f"AttachmentRouter: {len(signals)} signal(s)")
    for sig in signals:
        print(f"  [{sig.signal_type}] domain={sig.domain} sensitivity={sig.sensitivity} file={sig.filename_safe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
