#!/usr/bin/env python3
"""
historical_mail_review.py — Fetch & analyze emails year-by-year (H1/H2).
Writes per-period JSONL files and a consolidated analysis report.

Usage:
  python scripts/historical_mail_review.py --year 2025
  python scripts/historical_mail_review.py --year 2025 --half H1
  python scripts/historical_mail_review.py --all   # 2021-2025
"""
from __future__ import annotations

import sys, os
_ARTHA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ARTHA_DIR, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# Ensure venv
if os.name == "nt":
    _VENV_PY = os.path.join(os.path.expanduser("~"), ".artha-venvs", ".venv-win", "Scripts", "python.exe")
    _VENV_PREFIX = os.path.realpath(os.path.join(os.path.expanduser("~"), ".artha-venvs", ".venv-win"))
    if os.path.exists(_VENV_PY) and os.path.realpath(sys.prefix) != _VENV_PREFIX:
        import subprocess as _sp
        raise SystemExit(_sp.call([_VENV_PY] + sys.argv))

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Date ranges for each half-year period
# ---------------------------------------------------------------------------
PERIODS = {
    "2025-H1": ("2025-01-01T00:00:00-08:00", "2025-07-01T00:00:00-08:00"),
    "2025-H2": ("2025-07-01T00:00:00-08:00", "2026-01-01T00:00:00-08:00"),
    "2024-H1": ("2024-01-01T00:00:00-08:00", "2024-07-01T00:00:00-08:00"),
    "2024-H2": ("2024-07-01T00:00:00-08:00", "2025-01-01T00:00:00-08:00"),
    "2023-H1": ("2023-01-01T00:00:00-08:00", "2023-07-01T00:00:00-08:00"),
    "2023-H2": ("2023-07-01T00:00:00-08:00", "2024-01-01T00:00:00-08:00"),
    "2022-H1": ("2022-01-01T00:00:00-08:00", "2022-07-01T00:00:00-08:00"),
    "2022-H2": ("2022-07-01T00:00:00-08:00", "2023-01-01T00:00:00-08:00"),
    "2021-H1": ("2021-01-01T00:00:00-08:00", "2021-07-01T00:00:00-08:00"),
    "2021-H2": ("2021-07-01T00:00:00-08:00", "2022-01-01T00:00:00-08:00"),
}

MAX_PER_SOURCE = 2000  # max emails per source per half-year


def fetch_gmail(since: str, before: str, max_results: int) -> list[dict]:
    """Fetch Gmail emails for a date range."""
    try:
        from gmail_fetch import fetch_emails
        emails = fetch_emails(since_iso=since, max_results=max_results, before_iso=before)
        for e in emails:
            e["_source"] = "gmail"
        return emails
    except Exception as exc:
        print(f"  [WARN] Gmail fetch failed: {exc}", file=sys.stderr)
        return []


def fetch_outlook(since: str, before: str, max_results: int) -> list[dict]:
    """Fetch Outlook emails for a date range."""
    try:
        from msgraph_fetch import fetch_emails
        emails = fetch_emails(since_iso=since, max_results=max_results, before_iso=before)
        for e in emails:
            e["_source"] = "outlook"
        return emails
    except Exception as exc:
        print(f"  [WARN] Outlook fetch failed: {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Domain matchers (same as deep_mail_review2.py)
# ---------------------------------------------------------------------------

def match_strict(email, sender_patterns=None, subject_patterns=None):
    sender = email.get("from", "").lower()
    subject = email.get("subject", "").lower()
    sender_match = any(p.lower() in sender for p in (sender_patterns or []))
    subject_match = any(re.search(p, subject, re.IGNORECASE) for p in (subject_patterns or []))
    if sender_patterns and subject_patterns:
        return sender_match or subject_match
    elif sender_patterns:
        return sender_match
    elif subject_patterns:
        return subject_match
    return False


DOMAINS = {
    "IMMIGRATION": {
        "sender_patterns": ["fragomen", "uscis", "ceac.state.gov", "nvc.state.gov", "travel.state.gov"],
        "subject_patterns": [r"\buscis\b", r"\bi-140\b", r"\bi-485\b", r"\bi-765\b", r"\bgreen.?card\b",
                             r"\bvisa\b", r"\bh-1b\b", r"\bimmigration\b", r"\bpetition\b",
                             r"\bbiometric\b", r"\bead\b.*card", r"\bpriority date\b",
                             r"\bfragomen\b", r"\bapproval notice\b"],
    },
    "FINANCE": {
        "sender_patterns": ["chase.com", "wellsfargo", "wells fargo", "fidelity", "vanguard",
                            "schwab", "capitalone", "capital one", "amex", "american express",
                            "discover", "citi", "irs.gov", "turbotax", "intuit",
                            "venmo", "paypal", "zelle", "robinhood", "etrade",
                            "mint.com", "creditkarma", "experian", "equifax", "transunion",
                            "morgan stanley", "espp", "fidelity.com", "merrilledge",
                            "wealthfront", "betterment", "bofa", "bankofamerica"],
        "subject_patterns": [r"\b(tax|w-?2|1099|refund)\b.*\b(20[0-9]{2})\b",
                             r"\bmortgage\b.*statement", r"\bloan payment\b",
                             r"\baccount.*alert\b", r"\bstatement.*ready\b",
                             r"\b401k\b", r"\brsu\b.*vest", r"\bdividend\b"],
    },
    "INSURANCE": {
        "sender_patterns": ["progressive", "goosehead", "jason.flagg", "amfam", "american family",
                            "liberty mutual", "state farm", "allstate", "geico", "usaa"],
        "subject_patterns": [r"\bpolicy\b.*\b(renewal|change|cancel)\b",
                             r"\binsurance\b.*\b(update|renewal|premium)\b",
                             r"\bcoverage\b.*\b(change|update)\b",
                             r"\bauto insurance\b", r"\bhome insurance\b"],
    },
    "VEHICLE": {
        "sender_patterns": ["kia", "mazda", "chargepoint", "dmv", "dol.wa.gov",
                            "carfax", "carvana", "autozone", "acar", "nissan",
                            "goodtogo", "good to go", "wsdot"],
        "subject_patterns": [r"\bkia\b", r"\bmazda\b", r"\bev6\b", r"\bcx-?50\b",
                             r"\blease\b.*\b(payment|end|return)\b",
                             r"\bregistration\b", r"\brecall\b", r"\bchargepoint\b",
                             r"\bvehicle\b.*\b(service|maintenance)\b",
                             r"\bnissan\b", r"\bpathfinder\b"],
    },
    "HOME": {
        "sender_patterns": ["pse.com", "puget sound", "king county", "sammamish",
                            "talus", "bob heating", "wesley electric", "wright connection",
                            "aquaquip", "sp water", "water district", "xfinity", "comcast",
                            "ziply"],
        "subject_patterns": [r"\bproperty tax\b", r"\bhoa\b.*\b(dues|meeting|assessment)\b",
                             r"\butility\b.*bill", r"\belectric\b.*bill",
                             r"\bgas\b.*bill", r"\bhomeowner\b",
                             r"\binternet\b.*\b(bill|statement)\b"],
    },
    "HEALTH": {
        "sender_patterns": ["premera", "delta dental", "vsp", "kaiser", "evergreen",
                            "overlake", "swedish", "multicare", "labcorp", "quest",
                            "cvs", "walgreens", "rite aid", "zocdoc", "mychart",
                            "healthsparq", "allegro"],
        "subject_patterns": [r"\bappointment\b.*\b(confirm|remind|schedul)\b",
                             r"\blab results\b", r"\bprescription\b.*ready",
                             r"\beob\b", r"\bexplanation of benefits\b",
                             r"\bimmunization\b", r"\bvaccin\b",
                             r"\bclaim\b.*\b(process|paid|denied)\b"],
    },
    "KIDS": {
        "sender_patterns": ["skyline", "pine lake", "issaquah.wednet", "isd411",
                            "collegeboard", "commonapp", "naviance", "parchment",
                            "peachjar", "eastlake", "lwsd", "kumon", "mathnasium"],
        "subject_patterns": [r"\breport card\b", r"\bconference\b.*parent",
                             r"\bsat\b.*score", r"\bpsat\b", r"\bact\b.*score",
                             r"\bcollege\b.*\b(admit|accept|decision|application)\b",
                             r"\bscholarship\b", r"\bgraduation\b",
                             r"\bap exam\b", r"\bschool\b.*\b(enrollment|register)\b"],
    },
    "EMPLOYMENT": {
        "sender_patterns": ["@microsoft.com", "myhr", "benefitfocus", "fidelity",
                            "morganstanley", "stockplanconnect", "opendoor",
                            "pearsonvue", "certmetrics"],
        "subject_patterns": [r"\brsu\b", r"\bespp\b", r"\bperformance\b.*review",
                             r"\bbenefits\b.*\b(enrollment|change|update)\b",
                             r"\bpayroll\b", r"\bcompensation\b",
                             r"\borg\b.*\b(change|announce)\b",
                             r"\bcertification\b.*exam"],
    },
    "TRAVEL": {
        "sender_patterns": ["delta.com", "alaskaair", "united.com", "southwest",
                            "marriott", "hilton", "hyatt", "airbnb", "vrbo",
                            "expedia", "booking.com", "kayak", "google.com/travel",
                            "tsa.gov", "cbp.dhs.gov", "hertz", "avis"],
        "subject_patterns": [r"\bflight\b.*\b(confirm|itinerary|book|cancel)\b",
                             r"\bhotel\b.*\b(confirm|reserv|book)\b",
                             r"\btrip\b.*\b(confirm|itinerary)\b",
                             r"\bglobal entry\b", r"\bpassport\b.*\b(renew|expire)\b",
                             r"\bboarding pass\b"],
    },
    "DIGITAL": {
        "sender_patterns": ["t-mobile", "tmobile", "netflix", "hulu", "disney",
                            "apple.com", "spotify", "anthropic", "openai",
                            "amazon.com/prime", "warp.dev", "ollama",
                            "educative", "coursera", "udemy"],
        "subject_patterns": [r"\bsubscription\b.*\b(renew|cancel|expir|charged)\b",
                             r"\bsecurity alert\b", r"\bunusual sign.?in\b",
                             r"\baccount\b.*\b(locked|suspended|compromised)\b",
                             r"\bdomain\b.*\b(renew|expir)\b",
                             r"\bpassword\b.*\b(reset|change|expire)\b"],
    },
    "LEGAL": {
        "sender_patterns": ["@court", "@law", "attorney", "legal"],
        "subject_patterns": [r"\blegal notice\b", r"\bsubpoena\b", r"\bjury duty\b",
                             r"\bclass action\b", r"\bsettlement\b",
                             r"\bwarranty\b.*\b(expir|claim)\b"],
    },
    "ESTATE": {
        "sender_patterns": ["redfin", "zillow", "county assessor", "title company",
                            "escrow"],
        "subject_patterns": [r"\bhome value\b", r"\bproperty\b.*\b(assess|value)\b",
                             r"\bmortgage\b.*\b(rate|refinan)\b",
                             r"\btitle\b.*\b(insurance|search)\b"],
    },
}


def fmt_email(email, show_body=False, body_len=600):
    """Format one email for the report."""
    date = email.get("date_iso", email.get("date", ""))[:16]
    src = email.get("_source", "?")
    sender = email.get("from", "?")[:60]
    subject = email.get("subject", "(no subject)")[:100]
    lines = [f"  [{src}] {date} | {sender}", f"    Subject: {subject}"]
    snippet = email.get("snippet", "")[:200]
    if snippet:
        lines.append(f"    Snippet: {snippet}")
    if show_body:
        body = email.get("body", "")[:body_len]
        body = re.sub(r'\n{3,}', '\n\n', body)
        body = re.sub(r'[ \t]{2,}', ' ', body)
        if body:
            lines.append(f"    Body: {body}")
    return "\n".join(lines) + "\n"


def analyze_period(emails: list[dict], period_label: str) -> str:
    """Run domain analysis on a batch of emails, return report text."""
    emails.sort(key=lambda e: e.get("date_iso", ""), reverse=True)
    report_lines = []
    report_lines.append(f"\n{'#' * 80}")
    report_lines.append(f"# PERIOD: {period_label}  ({len(emails)} emails)")
    report_lines.append(f"{'#' * 80}\n")

    for domain_name, matchers in DOMAINS.items():
        matched = [e for e in emails if match_strict(e, **matchers)]
        report_lines.append(f"{'=' * 70}")
        report_lines.append(f"  {domain_name} — {len(matched)} emails")
        report_lines.append(f"{'=' * 70}")
        if matched:
            for e in matched:
                report_lines.append(fmt_email(e, show_body=True, body_len=600))
        else:
            report_lines.append("  (none)\n")

    # Top senders summary
    report_lines.append(f"\n{'=' * 70}")
    report_lines.append(f"  TOP SENDERS ({period_label})")
    report_lines.append(f"{'=' * 70}")
    senders = defaultdict(int)
    for e in emails:
        sender = e.get("from", "unknown")
        m = re.search(r'<([^>]+)>', sender)
        sender = m.group(1).lower() if m else sender.lower().strip('"')
        senders[sender] += 1
    for sender, count in sorted(senders.items(), key=lambda x: -x[1])[:50]:
        report_lines.append(f"  {count:3d}x  {sender}")

    return "\n".join(report_lines)


def process_period(period_key: str, since: str, before: str, out_dir: str) -> str:
    """Fetch, save, analyze one half-year period. Returns report text."""
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  Processing {period_key}: {since} → {before}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    # Fetch from both sources
    gmail_emails = fetch_gmail(since, before, MAX_PER_SOURCE)
    outlook_emails = fetch_outlook(since, before, MAX_PER_SOURCE)
    all_emails = gmail_emails + outlook_emails

    print(f"  {period_key}: Gmail={len(gmail_emails)}, Outlook={len(outlook_emails)}, Total={len(all_emails)}",
          file=sys.stderr)

    # Save JSONL
    jsonl_path = os.path.join(out_dir, f"emails_{period_key}.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for e in all_emails:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"  Saved: {jsonl_path} ({len(all_emails)} emails)", file=sys.stderr)

    # Analyze
    report = analyze_period(all_emails, period_key)
    return report


def main():
    parser = argparse.ArgumentParser(description="Historical email review by year/half-year")
    parser.add_argument("--year", type=int, help="Year to process (e.g. 2025)")
    parser.add_argument("--half", type=str, choices=["H1", "H2"], help="Half year (H1=Jan-Jun, H2=Jul-Dec)")
    parser.add_argument("--all", action="store_true", help="Process all years 2021-2025")
    args = parser.parse_args()

    out_dir = _ARTHA_DIR

    if args.all:
        periods_to_process = [
            "2025-H2", "2025-H1",
            "2024-H2", "2024-H1",
            "2023-H2", "2023-H1",
            "2022-H2", "2022-H1",
            "2021-H2", "2021-H1",
        ]
    elif args.year:
        if args.half:
            periods_to_process = [f"{args.year}-{args.half}"]
        else:
            periods_to_process = [f"{args.year}-H2", f"{args.year}-H1"]
    else:
        parser.error("Specify --year YYYY [--half H1|H2] or --all")
        return

    full_report = []
    for pkey in periods_to_process:
        since, before = PERIODS[pkey]
        report = process_period(pkey, since, before, out_dir)
        full_report.append(report)

        # Write incremental report per period
        period_report_path = os.path.join(out_dir, f"review_{pkey}.txt")
        with open(period_report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"  Report written: {period_report_path}", file=sys.stderr)

    # Write consolidated report
    consolidated_path = os.path.join(out_dir, "review_all.txt")
    with open(consolidated_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(full_report))
    print(f"\nConsolidated report: {consolidated_path}", file=sys.stderr)
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
