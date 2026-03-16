#!/usr/bin/env python3
"""
demo_catchup.py — Artha Tier 1 demo / onboarding mode
=======================================================
Runs a simulated catch-up using bundled fictional fixture emails so new
users can preview Artha's output format before connecting any real accounts.

All data is fictional:
  - Family: Alex & Sam Smith  (primary: alex.smith@example.com)
  - Immigration: H-1B extension pending; I-485 adjustment of status filed
  - Finance: mortgage, 401k, one credit card
  - Health: annual checkup scheduled; one prescription refill due
  - Kids: Ella (age 8, grade 3 at Maple Elementary)

Usage:
  python scripts/demo_catchup.py           — print sample briefing to stdout
  python scripts/demo_catchup.py --json    — raw JSONL extraction to stdout
  python scripts/demo_catchup.py --verify  — assert no real PII in fixture data

Ref: standardization.md §8 (Phase 3)
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from datetime import date
from pathlib import Path

_ARTHA_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Fictional Smith family fixture emails
# ---------------------------------------------------------------------------

FIXTURE_EMAILS = [
    {
        "id": "demo-001",
        "subject": "Receipt Notice — Form I-485 Application",
        "sender": "uscis-noreply@uscis.dhs.gov",
        "date": "2026-03-10T10:00:00Z",
        "body_text": (
            "Dear Alex Smith, we have received your Form I-485, Application to Register "
            "Permanent Residence. Receipt number: IOE0987654321. Priority date: "
            "2021-01-15. You will receive a biometrics appointment notice within 60 days."
        ),
        "source": "demo",
    },
    {
        "id": "demo-002",
        "subject": "H-1B Extension Approved — Case WAC2612345678",
        "sender": "noreply@immigration-law.example.com",
        "date": "2026-03-11T09:30:00Z",
        "body_text": (
            "Great news! The USCIS has approved your H-1B extension petition. "
            "Case WAC2612345678. New authorized period of admission: through 2028-09-30. "
            "Please ensure your I-94 reflects the new date upon entry."
        ),
        "source": "demo",
    },
    {
        "id": "demo-003",
        "subject": "Mortgage Statement — March 2026",
        "sender": "statements@fictional-bank.example.com",
        "date": "2026-03-08T07:00:00Z",
        "body_text": (
            "Your Fictional Bank mortgage statement for March 2026. "
            "Principal balance: $412,500. Monthly payment due: $2,340 on 2026-04-01. "
            "Escrow: $680 (taxes + insurance). No late fees."
        ),
        "source": "demo",
    },
    {
        "id": "demo-004",
        "subject": "Annual Wellness Checkup Reminder — Alex Smith",
        "sender": "reminders@fictional-clinic.example.com",
        "date": "2026-03-12T08:00:00Z",
        "body_text": (
            "This is a reminder that your annual wellness exam is scheduled for "
            "2026-03-25 at 10:00 AM with Dr. Emily Chen at Fictional Family Clinic. "
            "Please bring your insurance card and complete the pre-visit questionnaire."
        ),
        "source": "demo",
    },
    {
        "id": "demo-005",
        "subject": "Ella's Progress Report — Q3 2025-26",
        "sender": "noreply@maple-elementary.example.edu",
        "date": "2026-03-09T15:00:00Z",
        "body_text": (
            "Dear Alex and Sam Smith, Ella's Q3 progress report is now available. "
            "Overall grade: Exceeds Expectations. Reading: 4/4. Math: 3/4. "
            "Teacher note: Ella shows strong initiative in group projects. "
            "Parent-teacher conference: 2026-03-20 at 4:30 PM."
        ),
        "source": "demo",
    },
    {
        "id": "demo-006",
        "subject": "Prescription Refill Ready — Lisinopril 10mg",
        "sender": "pharmacy@fictional-pharmacy.example.com",
        "date": "2026-03-13T11:00:00Z",
        "body_text": (
            "Your prescription for Lisinopril 10mg (30-day supply) is ready for pickup "
            "at Fictional Pharmacy, 123 Main St. Pickup by 2026-03-17 or it will be "
            "returned to stock. Cost: $4.00 with insurance."
        ),
        "source": "demo",
    },
]

# ---------------------------------------------------------------------------
# Sample briefing renderer
# ---------------------------------------------------------------------------

def render_briefing(emails: list[dict]) -> str:
    today = date.today().isoformat()
    tty = sys.stdout.isatty()
    # ANSI colors — only when writing to a real terminal
    BOLD    = "\033[1m"  if tty else ""
    YELLOW  = "\033[33m" if tty else ""
    GREEN   = "\033[32m" if tty else ""
    RED     = "\033[31m" if tty else ""
    DIM     = "\033[2m"  if tty else ""
    RST     = "\033[0m"  if tty else ""

    def action(text: str) -> str:
        return f"  {YELLOW}ACTION:{RST} {text}"

    def good(text: str) -> str:
        return f"  {GREEN}•{RST} {text}"

    def alert(text: str) -> str:
        return f"  {RED}•{RST} {text}"

    def bullet(text: str) -> str:
        return f"  • {text}"

    lines = [
        f"{BOLD}━━ ARTHA DEMO BRIEFING — {today} ━━━━━━━━━━━━━━━━━━━━━━━━━━━{RST}",
        f"{DIM}⚠  DEMO MODE — All data is fictional. No real accounts connected.{RST}",
        "",
        f"{BOLD}## IMMIGRATION{RST}",
        bullet("I-485 received (IOE0987654321) — biometrics notice expected within 60 days"),
        good("H-1B extension approved through 2028-09-30 (WAC2612345678)"),
        action("Verify I-94 reflects new H-1B validity on next US entry"),
        "",
        f"{BOLD}## FINANCE{RST}",
        bullet("Mortgage payment $2,340 due 2026-04-01 (balance $412,500)"),
        action("Schedule April 1 mortgage payment"),
        "",
        f"{BOLD}## HEALTH{RST}",
        bullet("Annual wellness exam 2026-03-25 @ 10:00 AM with Dr. Emily Chen"),
        alert("Lisinopril 10mg refill ready — pickup by 2026-03-17"),
        action("Pick up prescription; complete pre-visit questionnaire"),
        "",
        f"{BOLD}## KIDS{RST}",
        good("Ella Q3 report: Exceeds Expectations (Reading 4/4, Math 3/4)"),
        bullet("Parent-teacher conference 2026-03-20 @ 4:30 PM"),
        action("Confirm conference attendance"),
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{BOLD}4 ACTION ITEMS  |  2 UPCOMING DATES  |  0 ALERTS{RST}",
        "",
        f"💡 {BOLD}Imagine waking up to a briefing like this — but for YOUR life.{RST}",
        "   Your real email. Your family's calendar. Your financial deadlines.",
        "   Your immigration status. Your kids' parent-teacher conferences.",
        "   Artha surfaces what matters, every morning, before you open your inbox.",
        "",
        f"{DIM}🔒  Your data stays on this machine. Artha never phones home.{RST}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def _check_no_real_pii(emails: list[dict]) -> None:
    """Assert fixture data contains no real PII patterns."""
    import re

    real_pii_patterns = [
        r"Patel",
        r"rajpatel",
        r"rpatel",
        r"Springfield",
        r"Shelbyville",
        r"Anytown",
        r"40\.7128",
        r"\-74\.0060",
        r"family\d{10,}@group\.calendar",
    ]
    issues = []
    for email in emails:
        text = json.dumps(email)
        for pat in real_pii_patterns:
            if re.search(pat, text, re.IGNORECASE):
                issues.append(f"  Pattern '{pat}' found in {email['id']}")

    if issues:
        print("FAIL — Real PII detected in demo fixtures:", file=sys.stderr)
        for issue in issues:
            print(issue, file=sys.stderr)
        sys.exit(1)
    else:
        print("PASS — No real PII found in demo fixtures.")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Artha demo catch-up using fictional Smith family fixtures."
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw fixture JSONL instead of rendered briefing",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify fixture data contains no real PII and exit",
    )
    args = parser.parse_args()

    if args.verify:
        _check_no_real_pii(FIXTURE_EMAILS)
        return

    if args.json:
        for email in FIXTURE_EMAILS:
            print(json.dumps(email))
        return

    print(render_briefing(FIXTURE_EMAILS))


if __name__ == "__main__":
    main()
