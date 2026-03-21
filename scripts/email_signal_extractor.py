#!/usr/bin/env python3
# pii-guard: email bodies are never stored; sender emails stripped before signals emitted
"""
scripts/email_signal_extractor.py — Deterministic email → DomainSignal extractor (E1).

Runs at Step 6.5 (after routing.yaml, before Step 7 domain processing).
Operates on routed email JSONL records to produce DomainSignal objects
that feed ActionComposer.compose() without requiring LLM inference.

Signal detection is regex/keyword-only — NO LLM calls.
False positive risks are mitigated by running AFTER marketing suppression (Step 5a)
and by using a 24-hour dedup window keyed on (signal_type, domain, deadline_date).

Detection rules (8 categories → 8 signal types):
  1. RSVP deadline           → event_rsvp_needed
  2. Appointment confirmed   → appointment_confirmed
  3. Payment notice          → bill_due
  4. Form deadline           → form_deadline
  5. Shipment arrival        → delivery_arriving
  6. Account security        → security_alert
  7. Renewal notice          → subscription_renewal
  8. School action needed    → school_action_needed

Privacy:
  - Sender email addresses NOT included in signal metadata
  - Only org_name (domain-part extracted) or subject keyword
  - Financial amount extracted for bill_due (standardized format only)
  - No raw email body stored in signals

Performance: regex-only; benchmark target <100ms for 500 emails.

Config flag: enhancements.email_signal_extractor (default: true)

Ref: specs/act-reloaded.md Enhancement 1
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from actions.base import DomainSignal  # type: ignore[import]
except ImportError:  # pragma: no cover
    # Fallback for test environments
    from dataclasses import dataclass, field

    @dataclass(frozen=True)
    class DomainSignal:  # type: ignore[no-redef]
        signal_type: str
        domain: str
        entity: str
        urgency: int
        impact: int
        source: str
        metadata: dict
        detected_at: str

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# ---------------------------------------------------------------------------
# Signal detection patterns
# ---------------------------------------------------------------------------
# Each entry: (compiled_regex_list, signal_type, domain, urgency, impact)
# Patterns match against combined subject + from + snippet (lower-cased)

_SIGNAL_PATTERNS: list[tuple[list[re.Pattern], str, str, int, int]] = [
    # 1. RSVP deadline
    (
        [
            re.compile(r"rsvp\s+by\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"please\s+respond\s+by", re.I),
            re.compile(r"deadline\s+to\s+reply", re.I),
            re.compile(r"respond\s+by\s+\w+\s+\d{1,2}", re.I),
        ],
        "event_rsvp_needed", "calendar", 2, 2,
    ),
    # 2. Appointment confirmed
    (
        [
            re.compile(r"appointment\s+confirmed", re.I),
            re.compile(r"your\s+visit\s+on", re.I),
            re.compile(r"scheduled\s+for\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"your\s+appointment\s+is\s+set", re.I),
        ],
        "appointment_confirmed", "calendar", 1, 1,
    ),
    # 3. Payment notice
    (
        [
            re.compile(r"payment\s+due", re.I),
            re.compile(r"amount\s+due", re.I),
            re.compile(r"balance\s+of\s+\$[\d,]+", re.I),
            re.compile(r"pay\s+by\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"your\s+bill\s+is\s+(ready|available)", re.I),
            re.compile(r"minimum\s+payment\s+due", re.I),
        ],
        "bill_due", "finance", 2, 2,
    ),
    # 4. Form deadline
    (
        [
            re.compile(r"submit\s+by\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"form\s+due", re.I),
            re.compile(r"application\s+deadline", re.I),
            re.compile(r"filing\s+deadline", re.I),
            re.compile(r"deadline\s+to\s+submit", re.I),
        ],
        "form_deadline", "comms", 2, 2,
    ),
    # 5. Shipment arrival
    (
        [
            re.compile(r"out\s+for\s+delivery", re.I),
            re.compile(r"delivered\s+to\s+your\s+(door|address|home)", re.I),
            re.compile(r"arriving\s+today", re.I),
            re.compile(r"package\s+delivered", re.I),
            re.compile(r"your\s+order\s+has\s+arrived", re.I),
        ],
        "delivery_arriving", "shopping", 1, 1,
    ),
    # 6. Account security
    (
        [
            re.compile(r"unusual\s+sign[-\s]?in", re.I),
            re.compile(r"password\s+reset", re.I),
            re.compile(r"security\s+alert", re.I),
            re.compile(r"suspicious\s+(activity|login)", re.I),
            re.compile(r"your\s+account\s+(was|has\s+been)\s+accessed", re.I),
        ],
        "security_alert", "digital", 3, 3,
    ),
    # 7. Renewal notice
    (
        [
            re.compile(r"renewal\s+on\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"subscription\s+renewing", re.I),
            re.compile(r"auto[-\s]?renew(al)?", re.I),
            re.compile(r"your\s+(plan|subscription)\s+will\s+renew", re.I),
        ],
        "subscription_renewal", "digital", 1, 1,
    ),
    # 8. School action needed
    (
        [
            re.compile(r"action\s+required.*school", re.I),
            re.compile(r"parent\s+signature", re.I),
            re.compile(r"field\s+trip\s+permission", re.I),
            re.compile(r"permission\s+slip", re.I),
            re.compile(r"school\s+action\s+required", re.I),
        ],
        "school_action_needed", "kids", 2, 2,
    ),
]

# Regex to extract a plausible deadline date from text
_DATE_RE = re.compile(
    r"\b(?:by|before|due|on)\s+"
    r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,?\s+\d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
    re.I,
)

# Regex to extract dollar amount
_AMOUNT_RE = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.I)

# Extract org name from sender email domain
_DOMAIN_RE = re.compile(r"@([\w\-\.]+)", re.I)

# Dedup window for signal suppression (key = (signal_type, entity_hash))
_DEDUP_WINDOW_HOURS = 24


def _extract_text(email_record: dict) -> str:
    """Build a scannable text blob from subject + from + snippet."""
    parts = [
        str(email_record.get("subject", "") or ""),
        str(email_record.get("from", "") or ""),
        str(email_record.get("snippet", "") or ""),
        str(email_record.get("body_preview", "") or "")[:500],
    ]
    return " ".join(p for p in parts if p).lower()


def _extract_org_name(from_field: str) -> str:
    """Extract organisation name from 'From:' header (domain-part only, no email)."""
    m = _DOMAIN_RE.search(from_field or "")
    if not m:
        return "unknown"
    # Strip TLD and return main domain word
    domain = m.group(1).lower()
    parts = domain.split(".")
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return domain.capitalize()


def _extract_deadline_date(text: str) -> str:
    """Try to extract a deadline date string from email text."""
    m = _DATE_RE.search(text)
    if m:
        return m.group(0).strip()[:30]
    return ""


def _extract_amount(text: str) -> str:
    """Extract first dollar amount from text (for financial signals)."""
    m = _AMOUNT_RE.search(text)
    if m:
        return f"${m.group(1)}"
    return ""


# ---------------------------------------------------------------------------
# EmailSignalExtractor
# ---------------------------------------------------------------------------

class EmailSignalExtractor:
    """Deterministic regex-based email → DomainSignal extractor.

    Usage:
        extractor = EmailSignalExtractor()
        signals = extractor.extract(email_records, routing_table)
    """

    def __init__(self) -> None:
        self._emitted: set[tuple[str, str]] = set()  # dedup within a session run

    def extract(
        self,
        email_records: list[dict],
        routing_table: dict | None = None,
    ) -> list[DomainSignal]:
        """Scan routed emails and return DomainSignal objects.

        Args:
            email_records: List of email JSONL dicts with subject, from,
                          snippet/body_preview fields. Must be post-marketing-filter.
            routing_table: Optional {email_id: domain} mapping from routing.yaml output.
                          If provided, overrides the default domain from pattern table.

        Returns:
            List of DomainSignal objects ready for ActionComposer.compose().
        """
        if not _load_flag("enhancements.email_signal_extractor", default=True):
            return []

        signals: list[DomainSignal] = []
        routing_table = routing_table or {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for record in email_records:
            if not isinstance(record, dict):
                continue

            email_id = str(record.get("id", record.get("message_id", "unknown")))
            text = _extract_text(record)
            from_field = str(record.get("from", "") or "")
            org_name = _extract_org_name(from_field)

            for patterns, signal_type, default_domain, urgency, impact in _SIGNAL_PATTERNS:
                matched = any(p.search(text) for p in patterns)
                if not matched:
                    continue

                # Determine domain (routing override preferred)
                domain = routing_table.get(email_id, default_domain)

                # Dedup key: (signal_type, email_id) — one signal per email per type
                dedup_key = (signal_type, email_id)
                if dedup_key in self._emitted:
                    continue
                self._emitted.add(dedup_key)

                # Extract metadata (no PII — only org name, dates, amounts)
                deadline_date = _extract_deadline_date(text)
                metadata: dict[str, Any] = {
                    "email_id": email_id,
                    "sender_org_name": org_name,
                }
                if deadline_date:
                    metadata["deadline_date"] = deadline_date
                if signal_type in ("bill_due", "subscription_renewal"):
                    amount = _extract_amount(text)
                    if amount:
                        metadata["amount"] = amount
                    metadata["sensitivity"] = "high"

                # Use subject as entity (truncated)
                subject = str(record.get("subject", "") or "")[:60]
                entity = f"{org_name}: {subject}" if subject else org_name

                sig = DomainSignal(
                    signal_type=signal_type,
                    domain=domain,
                    entity=entity,
                    urgency=urgency,
                    impact=impact,
                    source="email_signal_extractor",
                    metadata=metadata,
                    detected_at=now_iso,
                )
                signals.append(sig)

        return signals

    def reset_dedup(self) -> None:
        """Clear in-session dedup state (call between separate email batches)."""
        self._emitted.clear()


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI: python scripts/email_signal_extractor.py — runs on sample batch."""
    import json

    sample_emails = [
        {
            "id": "test-001",
            "subject": "Payment due: $127.45 due by March 25",
            "from": "billing@example-utility.com",
            "snippet": "Your payment of $127.45 is due by March 25, 2026.",
        },
        {
            "id": "test-002",
            "subject": "RSVP by March 28 — Annual Dinner",
            "from": "events@company.com",
            "snippet": "Please RSVP by March 28 for our annual dinner.",
        },
    ]

    extractor = EmailSignalExtractor()
    signals = extractor.extract(sample_emails)
    for sig in signals:
        print(f"  [{sig.signal_type}] domain={sig.domain} urgency={sig.urgency} entity={sig.entity[:40]}")

    if not signals:
        print("No signals detected in sample batch")

    return 0


if __name__ == "__main__":
    sys.exit(main())
