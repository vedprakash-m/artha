#!/usr/bin/env python3
"""
Temporary script: deep mail review for state enrichment.
Reads gmail_deep.jsonl and outlook_deep.jsonl, categorizes emails by domain,
filters noise, and outputs a structured summary of state-worthy items.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime

ARTHA_DIR = r"C:\Users\vemishra\OneDrive\Artha"

# Domain keywords for classification
DOMAIN_KEYWORDS = {
    "immigration": [
        "uscis", "i-140", "i-485", "i-130", "i-765", "ead", "green card",
        "fragomen", "visa", "h-1b", "h1b", "immigration", "petition",
        "biometric", "nis", "national interest", "priority date",
        "receipt number", "approval notice", "rfe", "noid"
    ],
    "finance": [
        "bank", "chase", "wells fargo", "fidelity", "vanguard", "schwab",
        "401k", "ira", "investment", "dividend", "tax", "w-2", "1099",
        "mortgage", "loan", "payment due", "statement", "credit card",
        "credit score", "balance", "transfer", "zelle", "venmo", "paypal",
        "turbotax", "intuit", "irs", "refund", "capital one", "amex",
        "american express", "discover", "citi"
    ],
    "insurance": [
        "progressive", "insurance", "policy", "coverage", "claim",
        "premium", "deductible", "goosehead", "jason flagg", "renewal",
        "declaration"
    ],
    "vehicle": [
        "kia", "mazda", "ev6", "cx-50", "cx50", "lease", "registration",
        "dmv", "license plate", "chargepoint", "ev charger", "kia finance",
        "acar leasing", "oil change", "tire", "maintenance", "recall"
    ],
    "home": [
        "pse", "puget sound energy", "electricity", "gas bill", "hoa",
        "homeowner", "property tax", "king county", "sammamish",
        "mortgage", "wells fargo home", "repair", "plumber", "electrical",
        "hvac", "lawn", "roof", "gutter"
    ],
    "health": [
        "premera", "dental", "vision", "pharmacy", "prescription",
        "doctor", "hospital", "clinic", "lab", "medical", "health",
        "kaiser", "appointment", "vaccine", "flu shot", "pediatric",
        "dermatolog", "optometr", "urgent care", "copay", "eob",
        "explanation of benefits", "delta dental", "vsp"
    ],
    "kids": [
        "school", "skyline", "pine lake", "issaquah", "student",
        "grade", "report card", "teacher", "conference", "pta",
        "sat", "psat", "act", "college", "university", "scholarship",
        "tutoring", "kumon", "music", "sport", "swim", "soccer",
        "dance", "camp", "field trip"
    ],
    "employment": [
        "microsoft", "myhr", "benefits", "payroll", "rsu", "espp",
        "stock", "performance", "review", "promotion", "l6", "l7",
        "principal", "manager", "team", "org change"
    ],
    "travel": [
        "flight", "airline", "hotel", "booking", "reservation",
        "airbnb", "vrbo", "passport", "tsa", "global entry",
        "delta", "alaska", "united", "southwest", "marriott", "hilton",
        "expedia", "kayak"
    ],
    "digital": [
        "password", "security alert", "two-factor", "2fa", "mfa",
        "subscription", "renewal", "apple", "google", "microsoft account",
        "storage", "icloud", "onedrive", "dropbox", "domain", "registrar",
        "ssl", "certificate", "github", "netflix", "spotify", "youtube",
        "disney", "hulu", "prime video", "adobe"
    ],
    "shopping": [
        "amazon", "costco", "target", "walmart", "order",
        "shipping", "delivery", "tracking", "return", "refund"
    ],
}

# Noise patterns to suppress
NOISE_SENDERS = [
    "noreply", "no-reply", "donotreply", "notifications@",
    "marketing@", "promo@", "deals@", "newsletter",
    "info@linkedin.com", "messages-noreply@linkedin.com",
    "notification@facebookmail.com", "security@facebookmail.com",
    "digest-noreply@quora.com", "member@linkedin.com",
    "invitations@linkedin.com",
]

NOISE_SUBJECTS = [
    r"(?i)unsubscribe", r"(?i)weekly digest", r"(?i)daily digest",
    r"(?i)sale\b.*off", r"(?i)limited time", r"(?i)promo code",
    r"(?i)your weekly", r"(?i)trending now", r"(?i)new connection",
    r"(?i)congratulated you", r"(?i)endorsed you",
    r"(?i)people also viewed", r"(?i)jobs you might",
    r"(?i)invitation to connect", r"(?i)your daily",
]

def is_noise(email_dict):
    """Return True if the email is likely marketing/noise."""
    sender = email_dict.get("from", "").lower()
    subject = email_dict.get("subject", "").lower()
    labels = email_dict.get("labels", [])

    # Check noise senders
    for ns in NOISE_SENDERS:
        if ns in sender:
            return True

    # Check noise subjects
    for pattern in NOISE_SUBJECTS:
        if re.search(pattern, subject):
            return True

    # Gmail label-based noise
    if "CATEGORY_PROMOTIONS" in labels or "CATEGORY_SOCIAL" in labels:
        return True

    return False


def classify_email(email_dict):
    """Return list of matching domains for an email."""
    text = (
        email_dict.get("subject", "") + " " +
        email_dict.get("from", "") + " " +
        email_dict.get("body", "")[:2000]  # first 2000 chars of body
    ).lower()

    matches = []
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                matches.append(domain)
                break
    return matches if matches else ["uncategorized"]


def extract_summary(email_dict, max_body=500):
    """Extract a brief summary of the email."""
    subject = email_dict.get("subject", "(no subject)")
    sender = email_dict.get("from", "unknown")
    date = email_dict.get("date_iso", email_dict.get("date", "unknown"))
    snippet = email_dict.get("snippet", "")
    body = email_dict.get("body", "")[:max_body]

    return {
        "subject": subject,
        "from": sender,
        "date": date,
        "snippet": snippet[:200],
        "body_preview": body,
    }


def main():
    all_emails = []

    for fname, source in [("gmail_deep.jsonl", "gmail"), ("outlook_deep.jsonl", "outlook")]:
        fpath = os.path.join(ARTHA_DIR, fname)
        if not os.path.exists(fpath):
            print(f"SKIP: {fname} not found")
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    email = json.loads(line)
                    email["_source"] = source
                    all_emails.append(email)
                except json.JSONDecodeError:
                    continue

    print(f"Total emails loaded: {len(all_emails)}")

    # Filter noise
    signal_emails = [e for e in all_emails if not is_noise(e)]
    noise_count = len(all_emails) - len(signal_emails)
    print(f"After noise filter: {len(signal_emails)} signal, {noise_count} noise suppressed")

    # Classify
    domain_emails = defaultdict(list)
    for email in signal_emails:
        domains = classify_email(email)
        for d in domains:
            domain_emails[d].append(email)

    # Print domain summary
    print("\n" + "=" * 80)
    print("DOMAIN BREAKDOWN")
    print("=" * 80)
    for domain in sorted(domain_emails.keys(), key=lambda d: -len(domain_emails[d])):
        print(f"\n  {domain.upper()}: {len(domain_emails[d])} emails")

    # For each high-value domain, print the emails
    HIGH_VALUE_DOMAINS = [
        "immigration", "finance", "insurance", "vehicle", "home",
        "health", "kids", "employment", "travel", "digital"
    ]

    for domain in HIGH_VALUE_DOMAINS:
        emails = domain_emails.get(domain, [])
        if not emails:
            continue

        print(f"\n{'='*80}")
        print(f"DOMAIN: {domain.upper()} ({len(emails)} emails)")
        print(f"{'='*80}")

        # Sort by date descending
        emails.sort(key=lambda e: e.get("date_iso", ""), reverse=True)

        for i, email in enumerate(emails):
            summary = extract_summary(email)
            print(f"\n--- [{i+1}] [{email['_source']}] {summary['date'][:10]} ---")
            print(f"  From: {summary['from']}")
            print(f"  Subject: {summary['subject']}")
            print(f"  Snippet: {summary['snippet']}")
            # For key domains, include more body
            if domain in ("immigration", "health", "insurance", "finance", "employment"):
                body = email.get("body", "")[:1500]
                if body:
                    # Clean up for readability
                    body = re.sub(r'\n{3,}', '\n\n', body)
                    body = re.sub(r' {2,}', ' ', body)
                    print(f"  Body: {body[:1000]}")

    # Also print uncategorized for review
    uncategorized = domain_emails.get("uncategorized", [])
    if uncategorized:
        print(f"\n{'='*80}")
        print(f"UNCATEGORIZED ({len(uncategorized)} emails) - Quick scan:")
        print(f"{'='*80}")
        uncategorized.sort(key=lambda e: e.get("date_iso", ""), reverse=True)
        for i, email in enumerate(uncategorized[:50]):  # Show first 50
            summary = extract_summary(email)
            print(f"  [{i+1}] [{email['_source']}] {summary['date'][:10]} | {summary['from'][:40]} | {summary['subject'][:80]}")


if __name__ == "__main__":
    main()
