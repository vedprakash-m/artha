#!/usr/bin/env python3
"""
scripts/email_classifier.py — Rule-based email marketing/importance classifier.

Tags each pipeline email record with:
    marketing: true | false
    category:  newsletter | promotional | auto-notification | transactional | personal | system

Philosophy
----------
- Whitelist-first: trusted domains and important subjects override marketing rules.
- Deterministic: no API calls, no ML models — just patterns and rules.
- Configurable: sender patterns and subjects can be added in artha_config.yaml
  under ``email_classifier`` without code changes.
- Additive: the original record is passed through unchanged; ``marketing`` and
  ``category`` fields are added/updated.

Expected to reduce context pressure for catch-up by 40–60% when >30% of
emails are marketing.

Usage
-----
    # Inline with pipeline output:
    python scripts/pipeline.py | python scripts/email_classifier.py

    # Process a saved JSONL file:
    python scripts/email_classifier.py --input tmp/pipeline_output.jsonl

    # Print summary statistics only:
    python scripts/email_classifier.py --stats

Exit codes
----------
    0   Success.
    1   Fatal error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent

try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Built-in classification tables
# ---------------------------------------------------------------------------

# Trusted senders — overrides ALL marketing classification.
# These domains send a mix of marketing + transactional; always keep.
_IMPORTANT_SENDER_DOMAINS: frozenset[str] = frozenset({
    # Government / immigration
    "uscis.gov", "egov.uscis.gov", "travel.state.gov", "irs.gov", "dol.gov",
    "consulate.gov", "immigration.gov",
    # Banks / financial
    "hdfc.com", "hdfcbank.com", "chase.com", "wellsfargo.com", "bankofamerica.com",
    "schwab.com", "fidelity.com", "vanguard.com", "capitalone.com",
    "paypal.com", "stripe.com", "ach.nacha.org",
    # Insurance / healthcare
    "anthem.com", "cigna.com", "aetna.com", "unitedhealthgroup.com",
    "bluecross.com", "regence.com", "premera.com",
    # Employer / HR
    "microsoft.com", "workday.com", "adp.com", "paychex.com",
    # Legal / immigration law firms
    "fragomen.com",
    # Utilities / essential services
    "usps.com", "ups.com", "fedex.com",
})

# Subject patterns that force a record to be kept as transactional/important
_IMPORTANT_SUBJECT_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(order|shipment|delivery|invoice|receipt|statement|payment|refund)\b",
        r"\b(account (alert|notice|update|statement))\b",
        r"\b(security|unauthorized|verification|OTP|2FA|one.time.password)\b",
        r"\b(case (update|status|notice|number|receipt))\b",
        r"\b(visa|i-485|i-130|i-140|i-131|EB-\d|priority date|noa)\b",
        r"\b(tax|w-2|1099|form (1040|1120|1065))\b",
        r"\b(appointment|schedule|confirm|reminder)\b",
        r"\b(urgent|action required|response needed)\b",
        r"\b(contract|offer letter|background check|onboarding)\b",
    ]
]

# Marketing sender domain patterns (partial match against sender domain)
_MARKETING_SENDER_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"@.*\.(substack\.com|beehiiv\.com|convertkit\.com|mailchimp\.com|sendgrid\.net)",
        r"@.*\.(klaviyo\.com|hubspot\.com|marketo\.com|salesforce\.com|pardot\.com)",
        r"@(noreply|no-reply|donotreply|news|newsletter|promotions|marketing|info)\.",
        r"@.*\.(bulk-mailer|mailer|blast|campaign|email-marketing)\.",
        r"@e\.(amazon|bestbuy|target|walmart|costco)\.",
        r"@.*promo\.",
        r"@.*newsletter\.",
    ]
]

# Marketing subject patterns
_MARKETING_SUBJECT_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(unsubscribe|opt.out)\b",
        r"\b(\d{1,3}%\s+off|save \$\d+|free shipping|limited time)\b",
        r"\b(flash sale|ends tonight|ends today|sale ends|last chance)\b",
        r"\b(weekly (digest|roundup|newsletter|update))\b",
        r"\b(daily (digest|briefing|newsletter))\b",
        r"(Newsletter|Digest|Roundup)\s*#?\d*$",
        r"\b(new episode|new post|new video|new release|new issue)\b",
        r"\[?(newsletter|digest|weekly|daily)\]?",
    ]
]

# Headers that indicate marketing/bulk email
_MARKETING_HEADERS: frozenset[str] = frozenset({
    "list-unsubscribe",
    "list-id",
    "x-mailchimp",
    "x-campaign",
    "x-bulk",
    "precedence: bulk",
    "precedence: list",
})

# Auto-notification patterns (not marketing, but low-priority)
_AUTO_NOTIFICATION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(GitHub|GitLab|Jira|Confluence|Slack)\b",
        r"\bnew (comment|mention|review|pull request|issue|commit)\b",
        r"\b(build (passed|failed|succeeded)|CI/CD|pipeline (passed|failed))\b",
        r"\b(calendar (invite|reminder)|meeting (request|reminder))\b",
    ]
]


# ---------------------------------------------------------------------------
# Classifier core
# ---------------------------------------------------------------------------

def _extract_sender_domain(sender: str) -> str:
    """Extract the domain from a sender string like 'Name <user@domain.com>'."""
    m = re.search(r"@([\w.\-]+)", sender)
    return m.group(1).lower() if m else sender.lower()


def classify_email(record: dict, custom_whitelist: list[str] | None = None) -> dict:
    """Classify a single email record in-place and return it.

    Adds/updates:
        marketing: bool
        marketing_category: str | None  — only set when marketing=True

    The original record is mutated and returned.
    """
    sender = record.get("sender", record.get("from", record.get("from_email", "")))
    subject = record.get("subject", record.get("title", ""))
    headers = record.get("headers", {})
    if isinstance(headers, str):
        headers = {}  # can't parse raw header string safely

    sender_domain = _extract_sender_domain(sender)

    # --- 1. Whitelist check (highest priority) ---
    if sender_domain in _IMPORTANT_SENDER_DOMAINS:
        record["marketing"] = False
        record.pop("marketing_category", None)
        return record

    if custom_whitelist:
        for domain in custom_whitelist:
            if domain.lower() in sender_domain:
                record["marketing"] = False
                record.pop("marketing_category", None)
                return record

    # --- 2. Important subject override ---
    for pat in _IMPORTANT_SUBJECT_PATTERNS:
        if pat.search(subject):
            record["marketing"] = False
            record.pop("marketing_category", None)
            return record

    # --- 3. Auto-notification (not marketing but not transactional) ---
    for pat in _AUTO_NOTIFICATION_PATTERNS:
        if pat.search(subject) or pat.search(sender):
            record["marketing"] = False
            record["marketing_category"] = "auto-notification"
            return record

    # --- 4. Marketing sender domain patterns ---
    for pat in _MARKETING_SENDER_PATTERNS:
        if pat.search(sender):
            record["marketing"] = True
            record["marketing_category"] = "newsletter"
            return record

    # --- 5. Marketing header presence ---
    header_str = json.dumps(headers).lower() if isinstance(headers, dict) else ""
    for h in _MARKETING_HEADERS:
        if h in header_str:
            record["marketing"] = True
            record["marketing_category"] = "newsletter"
            return record

    # --- 6. Marketing subject patterns ---
    for pat in _MARKETING_SUBJECT_PATTERNS:
        if pat.search(subject):
            record["marketing"] = True
            record["marketing_category"] = "promotional"
            return record

    # --- 7. Default: not marketing ---
    record["marketing"] = False
    record.pop("marketing_category", None)
    return record


def _load_custom_whitelist() -> list[str]:
    """Load custom whitelist from artha_config.yaml if present."""
    try:
        import yaml  # type: ignore[import]
        cfg_path = _REPO_ROOT / "config" / "artha_config.yaml"
        if not cfg_path.exists():
            return []
        with cfg_path.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return cfg.get("email_classifier", {}).get("whitelist_domains", [])
    except Exception:
        return []


def classify_records(records: list[dict]) -> list[dict]:
    """Classify a batch of pipeline records. Non-email records pass through."""
    whitelist = _load_custom_whitelist()
    for rec in records:
        rtype = rec.get("type", rec.get("record_type", ""))
        source = rec.get("source", rec.get("source_tag", ""))
        # Only classify email-type records
        if rtype in ("email", "message") or "email" in source or "mail" in source:
            classify_email(rec, custom_whitelist=whitelist)
    return records


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def print_stats(records: list[dict]) -> None:
    total = len(records)
    emails = [r for r in records if r.get("type", "") in ("email", "message") or "email" in r.get("source", "")]
    marketing = [r for r in emails if r.get("marketing")]
    non_marketing = [r for r in emails if not r.get("marketing")]
    categories: dict[str, int] = {}
    for r in marketing:
        cat = r.get("marketing_category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\n[email_classifier] Stats:")
    print(f"  Total records:   {total}")
    print(f"  Email records:   {len(emails)}")
    print(f"  Marketing:       {len(marketing)} ({100*len(marketing)//max(len(emails),1)}%)")
    print(f"  Non-marketing:   {len(non_marketing)}")
    if categories:
        print("  Marketing breakdown:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"    {cat}: {count}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="email_classifier.py",
        description="Tag pipeline email records as marketing=true/false",
    )
    p.add_argument("--input", "-i", metavar="JSONL", help="Input JSONL file (default: stdin)")
    p.add_argument("--output", "-o", metavar="JSONL", help="Output JSONL file (default: stdout)")
    p.add_argument("--stats", action="store_true", help="Print classification stats to stderr")
    p.add_argument("--stats-only", action="store_true", help="Print stats only, no output")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    # Read input
    records: list[dict] = []
    raw_lines: list[str] = []
    in_stream = open(args.input, encoding="utf-8") if args.input else sys.stdin  # noqa: WPS515
    try:
        for line in in_stream:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                records.append(json.loads(line))
                raw_lines.append(line)
            except json.JSONDecodeError:
                raw_lines.append(line)  # Pass through non-JSON lines
                records.append({})
    finally:
        if args.input:
            in_stream.close()

    # Classify
    classify_records(records)

    if args.stats or args.stats_only:
        print_stats(records)

    if args.stats_only:
        return 0

    # Write output
    out_stream = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout  # noqa: WPS515
    try:
        for rec, raw in zip(records, raw_lines):
            if rec:
                print(json.dumps(rec, ensure_ascii=False, default=str), file=out_stream)
            else:
                print(raw, file=out_stream)
    finally:
        if args.output:
            out_stream.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
