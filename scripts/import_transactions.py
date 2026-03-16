#!/usr/bin/env python3
"""
import_transactions.py — Artha transaction importer

Parses all bank/card CSV and HTML export files from ~/Downloads/Transactions,
normalises them into a single ledger, aggregates into Artha budget categories,
and writes:

  1. state/transactions.md   — full normalised ledger (vault-encrypted)
  2. state/finance.md        — updated ## Transaction History section
                              + updated ## Monthly Burn Rate Summary

Usage:
  python scripts/import_transactions.py --dry-run     # preview only
  python scripts/import_transactions.py               # write to state

Safety:
  - Net-negative write guard: aborts if changes would remove >20% of finance.md
  - No full account numbers stored (****XXXX format only)
  - Idempotent: replaces existing ## Transaction History if present
  - Does NOT touch state/finance.md frontmatter or any other section
"""

from __future__ import annotations

import csv
import io
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ARTHA = Path(__file__).resolve().parent.parent
TRANSACTIONS_DIR = Path.home() / "Downloads" / "Transactions"
FINANCE_MD = ARTHA / "state" / "finance.md"
TRANSACTIONS_MD = ARTHA / "state" / "transactions.md"

# ---------------------------------------------------------------------------
# Unified transaction record
# ---------------------------------------------------------------------------
class Txn(NamedTuple):
    date: date
    description: str      # truncated to 50 chars, no PII
    amount: float         # positive = spend/debit, negative = payment/credit
    raw_category: str
    account_last4: str    # e.g. "7836"
    institution: str
    owner: str            # "Ved" or "Archana"
    txn_type: str         # "debit" | "credit" | "payment" | "transfer"
    source_file: str      # basename of source file

# ---------------------------------------------------------------------------
# Budget category mapping
# ---------------------------------------------------------------------------
BUDGET_CATEGORIES = [
    "housing", "transportation", "groceries", "dining",
    "healthcare", "education", "subscriptions", "savings",
    "immigration", "personal", "travel", "other",
]

# Keyword → category mapping (applied to description + raw_category, lowercased)
CATEGORY_RULES: list[tuple[list[str], str]] = [
    # Housing
    (["mortgage", "wells fargo", "hoa", "pse", "puget sound energy",
      "sammamish plateau water", "sammamish plateau wat", "sp water",
      "gas company", "republic services", "waste management",
      "home depot", "lowes", "ace hardware", "plumber", "electrician",
      "roofer", "rent", "zillow", "property tax",
      "king county", "county treasurer", "county tax"], "housing"),
    # Transportation
    (["kia", "toyota", "honda", "car payment", "auto loan", "insurance",
      "progressive", "geico", "state farm", "allstate",
      "exxon", "shell", "chevron", "bp ", "arco", "76 ", "costco gas",
      "parking", "toll", "uber", "lyft", "avis", "hertz", "enterprise",
      "national car", "zipcar", "metro", "orca", "tesla", "ev charge",
      "blink charging", "evgo", "electrify"], "transportation"),
    # Groceries
    (["costco", "amazon fresh", "whole foods", "trader joe",
      "safeway", "kroger", "fred meyer", "qfc", "winco",
      "sprouts", "hmart", "h-mart", "uwajimaya", "mayuri",
      "grocery", "supermarket", "supermarkets", "merchandise"], "groceries"),
    # Dining
    (["restaurant", "dining", "food & drink", "doordash", "ubereats",
      "grubhub", "instacart meals", "mcdonald", "starbucks", "chick-fil",
      "chipotle", "panera", "chuy", "culinary", "mami tran",
      "ihop", "olive garden", "sushi", "pizza", "burger", "cafe",
      "coffee", "ms cafe"], "dining"),
    # Healthcare
    (["pharmacy", "cvs", "walgreens", "rite aid",
      "hospital", "medical", "doctor", "dental", "vision",
      "optometry", "lab ", "quest diagnostics", "labcorp",
      "fsa", "hsa", "health", "ymca", "gym", "fitness",
      "planet fitness", "24 hour fitness", "la fitness",
      "great clips", "haircut", "massage"], "healthcare"),
    # Education
    (["school", "tuition", "tutoring", "sat prep", "act prep",
      "canvas", "college board", "university", "ets gre",
      "udemy", "coursera", "pluralsight", "linkedin learning",
      "amazon kindle", "book", "library"], "education"),
    # Subscriptions
    (["netflix", "spotify", "hulu", "disney", "hbo", "max ", "peacock",
      "paramount", "apple.com/bill", "apple tv", "apple one",
      "microsoft xbox", "xbox game", "nintendo", "amazon prime",
      "amazon.com", "1password", "dropbox", "google one",
      "youtube premium", "audible", "kindle unlimited",
      "t-mobile", "tmobile", "verizon", "att ", "at&t",
      "comcast", "xfinity", "spectrum", "subscription",
      "saas", "software", "cloud"], "subscriptions"),
    # Savings / transfers
    (["payment thank you", "internet payment", "autopay",
      "401k", "403b", "ira", "529", "brokerage transfer",
      "fidelity", "vanguard", "empower", "morgan stanley"], "savings"),
    # Immigration
    (["uscis", "immigration", "attorney", "visa bulletin",
      "biometrics", "filing fee", "i-765", "i-485", "naturalization"], "immigration"),
    # Travel
    (["airline", "united airlines", "delta ", "american air",
      "alaska air", "southwest", "spirit air", "jetblue",
      "hotel", "marriott", "hilton", "hyatt", "ihg", "airbnb",
      "vrbo", "expedia", "booking.com", "priceline",
      "travel", "trip", "vacation", "cruise"], "travel"),
    # Personal
    (["amazon mktpl", "target", "walmart", "nordstrom", "macy",
      "gap ", "old navy", "h&m ", "zara", "clothing", "apparel",
      "personal", "gift", "paypal", "venmo", "banking", "fee",
      "annual fee", "late fee", "foreign transaction"], "personal"),
]


def categorize(description: str, raw_category: str) -> str:
    """Map description + raw_category to an Artha budget category."""
    text = (description + " " + raw_category).lower()
    for keywords, cat in CATEGORY_RULES:
        for kw in keywords:
            if kw in text:
                return cat
    return "other"


def _is_payment(description: str, raw_category: str, txn_type: str) -> bool:
    """Return True for payment/credit rows that should be excluded from spend."""
    if txn_type in ("payment", "credit"):
        return True
    d = description.lower()
    c = raw_category.lower()
    payment_signals = [
        "internet payment", "payment thank you", "autopay payment",
        "payment received", "payments and credits", "refund", "return",
        "reversal", "bank deposit", "paypal buyer credit", "funding",
        "non reference credit",
        # ACH withdrawals from bank/checking accounts to credit cards / mortgage
        # These are interbank transfers — already counted in the credit card files
        "ach withdrawal", "ach transfer", "ach debit",
        "cardmember serv", "citi card online", "chase credit crd epay",
        "discover e-payment", "fidelity epay", "amex epayment",
        "e-payment", "online payment", "web pymt", "bill payment",
        "mortgage payment", "loan payment", "transfer to", "transfer from",
        # Large check payments and reversed ACH from bank accounts (transfers, not spending)
        "reverse ach deposit", "reverse ach",
        "paypal cashback mastercard",  # PayPal balance → card payment
        "zelle payment",               # person-to-person transfer
        "apple cash sent",             # Apple Cash transfer
    ]
    # Bank checks from debit accounts are typically large transfers, not spend
    if re.match(r'^check \d+$', d.strip()):
        return True
    return any(s in d or s in c for s in payment_signals)


def _clean(s: str) -> str:
    """Normalise whitespace, strip HTML entities, cap at 50 chars."""
    s = re.sub(r'&[a-z]+;', ' ', s)
    s = ' '.join(s.split())
    return s[:50]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_chase_csv(path: Path) -> list[Txn]:
    """Chase Freedom, Freedom Flex, Prime Visa, Marriott.
    Columns: Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    Amount: negative = charge, positive = payment/refund
    """
    txns = []
    last4 = _last4_from_filename(path.name)
    institution = _institution_from_filename(path.name)
    with open(path, encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                raw_date = row.get('Transaction Date', '').strip()
                d = datetime.strptime(raw_date, '%m/%d/%Y').date()
                desc = _clean(row.get('Description', ''))
                cat = row.get('Category', '').strip()
                txn_type_raw = row.get('Type', '').strip().lower()
                amt_str = row.get('Amount', '0').strip()
                amt = float(amt_str)
                # Chase: negative = charge (spend), positive = payment
                if amt < 0:
                    ttype = 'debit'
                    amount = -amt  # positive spend
                else:
                    ttype = 'payment'
                    amount = amt
                txns.append(Txn(
                    date=d, description=desc, amount=amount,
                    raw_category=cat, account_last4=last4,
                    institution=institution, owner='Ved',
                    txn_type=ttype, source_file=path.name,
                ))
            except (ValueError, KeyError):
                continue
    return txns


def _parse_citi_csv(path: Path) -> list[Txn]:
    """Citi Costco, Citi Custom Cash.
    Header rows before the actual CSV. Columns: Date, Description, Debit, Credit, Category
    """
    txns = []
    last4 = _last4_from_filename(path.name)
    institution = _institution_from_filename(path.name)
    # Citi has metadata rows at the top — find the header row
    with open(path, encoding='utf-8-sig', errors='replace') as f:
        lines = f.readlines()
    # Find the data header row
    header_idx = None
    for i, line in enumerate(lines):
        if 'Date' in line and 'Description' in line and 'Debit' in line:
            header_idx = i
            break
    if header_idx is None:
        return []
    csv_content = ''.join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_content))
    for row in reader:
        try:
            raw_date = row.get('Date', '').strip().strip('"')
            if not raw_date:
                continue
            # "Apr 19, 2025" format
            d = datetime.strptime(raw_date, '%b %d, %Y').date()
            desc = _clean(row.get('Description', '').strip().strip('"'))
            cat = row.get('Category', '').strip().strip('"')
            debit_str = row.get('Debit', '').strip().strip('"')
            credit_str = row.get('Credit', '').strip().strip('"')
            if debit_str and debit_str not in ('', '0', '0.00'):
                amount = float(debit_str)
                ttype = 'debit'
            elif credit_str and credit_str not in ('', '0', '0.00'):
                amount = float(credit_str)
                ttype = 'credit'
            else:
                continue
            txns.append(Txn(
                date=d, description=desc, amount=amount,
                raw_category=cat, account_last4=last4,
                institution=institution, owner='Ved',
                txn_type=ttype, source_file=path.name,
            ))
        except (ValueError, KeyError):
            continue
    return txns


def _parse_discover_card_html(path: Path) -> list[Txn]:
    """Discover Card (****7836) HTML.
    Table row: Trans. date | Post date | Description | Amount | Category
    Amount: positive = charge, negative = payment/credit
    """
    txns = []
    last4 = '7836'
    with open(path, encoding='utf-8', errors='replace') as f:
        content = f.read()
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.DOTALL | re.IGNORECASE)
    in_data = False
    for row in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        cells = [' '.join(c.split()) for c in cells]
        if not cells:
            continue
        # Detect header row
        if cells[0] == 'Trans. date' or cells[0] == 'Trans.date':
            in_data = True
            continue
        if not in_data:
            continue
        if len(cells) < 4:
            continue
        try:
            d = datetime.strptime(cells[0], '%m/%d/%Y').date()
            desc = _clean(cells[2])
            amt = float(cells[3])
            cat = cells[4] if len(cells) > 4 else ''
            if amt > 0:
                ttype = 'debit'
                amount = amt
            else:
                ttype = 'payment'
                amount = abs(amt)
            txns.append(Txn(
                date=d, description=desc, amount=amount,
                raw_category=cat, account_last4=last4,
                institution='Discover', owner='Ved',
                txn_type=ttype, source_file=path.name,
            ))
        except (ValueError, IndexError):
            continue
    return txns


def _parse_discover_account_html(path: Path) -> list[Txn]:
    """Discover Savings (****7641) and Debit (****1977) HTML.
    Columns: Transaction Date | Transaction Type | Description | Debit | Credit | Balance
    Discover Savings is excluded from spend aggregation (pure savings/transfer account).
    """
    # Skip savings account entirely — it's a savings vehicle, all outflows are transfers
    if 'Avings' in path.name or 'avings' in path.name:
        return []
    txns = []
    last4 = _last4_from_filename(path.name)
    # Determine owner by account number in the file header
    with open(path, encoding='utf-8', errors='replace') as f:
        content = f.read()

    # Grab account number from "Account Ending in" row for last4 override
    acct_match = re.search(r'Account Ending in[^<]*</td[^>]*>\s*<td[^>]*>(\d+)', content, re.IGNORECASE)
    if not acct_match:
        acct_match = re.search(r'Cashback Debit \((\d+)\)|Online Savings \((\d+)\)', content)
    if acct_match:
        last4 = (acct_match.group(1) or acct_match.group(2) or last4)[-4:]

    owner = 'Archana'  # Both Discover accounts belong to Archana

    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.DOTALL | re.IGNORECASE)
    in_data = False
    for row in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        cells = [' '.join(c.split()) for c in cells]
        if not cells:
            continue
        if cells[0] == 'Transaction Date':
            in_data = True
            continue
        if not in_data:
            continue
        if len(cells) < 4:
            continue
        try:
            d = datetime.strptime(cells[0], '%m/%d/%Y').date()
            txn_type_raw = cells[1].lower()
            desc = _clean(cells[2])
            debit_str = cells[3] if len(cells) > 3 else ''
            credit_str = cells[4] if len(cells) > 4 else ''
            # Debit = money out (spending), Credit = money in (deposit/payment)
            if debit_str and debit_str not in ('', '0.00', '0'):
                cleaned = debit_str.replace(',', '').replace('$', '')
                amount = float(cleaned)
                ttype = 'debit'
            elif credit_str and credit_str not in ('', '0.00', '0'):
                cleaned = credit_str.replace(',', '').replace('$', '')
                amount = float(cleaned)
                ttype = 'credit'
            else:
                continue
            txns.append(Txn(
                date=d, description=desc, amount=amount,
                raw_category='',  account_last4=last4,
                institution='Discover', owner=owner,
                txn_type=ttype, source_file=path.name,
            ))
        except (ValueError, IndexError):
            continue
    return txns


def _parse_fidelity_csv(path: Path) -> list[Txn]:
    """Fidelity Visa / Fidelity Rewards Visa.
    Columns: Date, Transaction, Name, Memo, Amount
    Amount: negative = charge, positive = credit/payment
    """
    txns = []
    with open(path, encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                raw_date = row.get('Date', '').strip().strip('"')
                d = datetime.strptime(raw_date, '%Y-%m-%d').date()
                desc = _clean(row.get('Name', '').strip())
                txn_type_raw = row.get('Transaction', '').strip().upper()
                amt_str = row.get('Amount', '0').strip().strip('"').replace(',', '')
                amt = float(amt_str)
                if amt < 0:
                    ttype = 'debit'
                    amount = -amt
                elif txn_type_raw == 'CREDIT':
                    ttype = 'payment'
                    amount = amt
                else:
                    ttype = 'debit'
                    amount = amt
                txns.append(Txn(
                    date=d, description=desc, amount=amount,
                    raw_category=txn_type_raw, account_last4='9024',
                    institution='Fidelity', owner='Ved',
                    txn_type=ttype, source_file=path.name,
                ))
            except (ValueError, KeyError):
                continue
    return txns


def _parse_paypal_csv(path: Path) -> list[Txn]:
    """PayPal CSV exports (multiple years).
    Relevant columns: Date, Name, Type, Status, Currency, Amount, Fees, Total
    Only include: USD, Status=Completed, exclude internal transfers/funding
    """
    txns = []
    SKIP_TYPES = {
        'paypal buyer credit payment funding',
        'bank deposit to pp account',
        'general credit card deposit',
        'paypal visa debit card transaction',
        'transfer from paypal account to bank',
        'hold on balance for dispute',
        'hold release',
        'update to hold',
        'update to reversal',
    }
    with open(path, encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Strip BOM and extra whitespace from keys
                row = {k.strip().lstrip('\ufeff'): v for k, v in row.items()}
                status = row.get('Status', '').strip()
                if status != 'Completed':
                    continue
                currency = row.get('Currency', '').strip()
                if currency != 'USD':
                    continue
                txn_type_raw = row.get('Type', '').strip().lower()
                if any(skip in txn_type_raw for skip in SKIP_TYPES):
                    continue
                raw_date = row.get('Date', '').strip()
                d = datetime.strptime(raw_date, '%m/%d/%Y').date()
                name = _clean(row.get('Name', '').strip())
                amt_str = row.get('Amount', '0').strip().replace(',', '')
                amt = float(amt_str)
                if amt < 0:
                    ttype = 'debit'
                    amount = -amt
                elif amt > 0:
                    ttype = 'credit'
                    amount = amt
                else:
                    continue
                txns.append(Txn(
                    date=d, description=name or 'PayPal',
                    amount=amount, raw_category='paypal',
                    account_last4='PPBL', institution='PayPal',
                    owner='Ved', txn_type=ttype,
                    source_file=path.name,
                ))
            except (ValueError, KeyError):
                continue
    return txns


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def _last4_from_filename(name: str) -> str:
    """Extract a recognisable account tag from filename."""
    n = name.lower()
    if 'freedom_flex' in n: return '99FF'
    if 'freedom_card' in n: return '99CF'
    if 'costco' in n: return '3319'
    if 'custom_cash' in n: return '4357'
    if 'savings' in n and 'discover' in n: return '7641'
    if 'debit' in n and 'discover' in n: return '1977'
    if 'discover_card' in n: return '7836'
    if 'fidelity' in n: return '9024'
    if 'marriott' in n: return 'MARR'
    if 'prime_visa' in n: return '1526'
    if 'paypal' in n: return 'PPBL'
    return 'XXXX'


def _institution_from_filename(name: str) -> str:
    n = name.lower()
    if 'chase' in n: return 'Chase'
    if 'citi' in n: return 'Citi'
    if 'discover' in n: return 'Discover'
    if 'fidelity' in n: return 'Fidelity'
    if 'paypal' in n: return 'PayPal'
    if 'marriott' in n: return 'Chase'
    if 'prime' in n: return 'Chase'
    return 'Unknown'


# ---------------------------------------------------------------------------
# Main parser dispatcher
# ---------------------------------------------------------------------------

def parse_all_files(txn_dir: Path) -> list[Txn]:
    all_txns: list[Txn] = []
    parsers = {
        'Chase_Freedom_Card':       _parse_chase_csv,
        'Chase_Freedom_Flex_Card':  _parse_chase_csv,
        'Prime_Visa_Card':          _parse_chase_csv,
        'Marriott_Card':            _parse_chase_csv,
        'Citi_Costco_Card':         _parse_citi_csv,
        'Citi_Custom_Cash_Card':    _parse_citi_csv,
        'Discover_Card':            _parse_discover_card_html,
        'Discover_Avings_AC':       _parse_discover_account_html,
        'Discover_Debit_AC':        _parse_discover_account_html,
        'Fidelity_Card':            _parse_fidelity_csv,
    }
    paypal_files = []
    for xls_file in sorted(txn_dir.glob('*.xls')):
        matched = False
        for prefix, parser in parsers.items():
            if xls_file.name.startswith(prefix):
                try:
                    parsed = parser(xls_file)
                    all_txns.extend(parsed)
                    print(f"  Parsed {xls_file.name}: {len(parsed)} rows")
                except Exception as e:
                    print(f"  WARNING: {xls_file.name}: {e}", file=sys.stderr)
                matched = True
                break
        if not matched and xls_file.name.startswith('Paypal_'):
            paypal_files.append(xls_file)

    for pf in sorted(paypal_files):
        try:
            parsed = _parse_paypal_csv(pf)
            all_txns.extend(parsed)
            print(f"  Parsed {pf.name}: {len(parsed)} rows")
        except Exception as e:
            print(f"  WARNING: {pf.name}: {e}", file=sys.stderr)

    return all_txns


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(txns: list[Txn]) -> dict:
    """Return monthly_spend[YYYY-MM][category] and paypal_yearly[YYYY]."""
    monthly_spend: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    paypal_yearly: dict[int, float] = defaultdict(float)

    for t in txns:
        if _is_payment(t.description, t.raw_category, t.txn_type):
            continue
        ym = t.date.strftime('%Y-%m')
        cat = categorize(t.description, t.raw_category)
        monthly_spend[ym][cat] += t.amount
        if t.institution == 'PayPal' and t.txn_type == 'debit':
            paypal_yearly[t.date.year] += t.amount

    return {
        'monthly_spend': dict(monthly_spend),
        'paypal_yearly': dict(paypal_yearly),
    }


def rolling_avg(monthly_spend: dict, months: int = 3) -> dict[str, float]:
    """3-month rolling average for the most recent N months with data."""
    sorted_months = sorted(monthly_spend.keys())
    recent = sorted_months[-months:] if len(sorted_months) >= months else sorted_months
    totals: dict[str, float] = defaultdict(float)
    for ym in recent:
        for cat, amt in monthly_spend[ym].items():
            totals[cat] += amt
    n = len(recent)
    return {cat: round(v / n, 2) for cat, v in totals.items()}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_monthly_table(monthly_spend: dict) -> str:
    """Markdown table of monthly spending by category (last 24 months)."""
    sorted_months = sorted(monthly_spend.keys())
    recent_24 = sorted_months[-24:]

    # Find categories that appear in at least one month
    active_cats = sorted({
        cat for ym in recent_24 for cat in monthly_spend.get(ym, {})
    })

    header = "| Month | " + " | ".join(c.capitalize() for c in active_cats) + " | Total |"
    sep = "|---|" + "---|" * (len(active_cats) + 1)
    rows_md = [header, sep]

    for ym in reversed(recent_24):
        data = monthly_spend.get(ym, {})
        total = sum(data.get(c, 0) for c in active_cats)
        cells = [f"${data.get(c, 0):,.0f}" for c in active_cats]
        rows_md.append(f"| {ym} | " + " | ".join(cells) + f" | ${total:,.0f} |")

    return "\n".join(rows_md)


def render_transactions_md(txns: list[Txn]) -> str:
    """Full normalised ledger as Markdown."""
    lines = [
        "---",
        'schema_version: "1.0"',
        f'last_updated: "{date.today()}"',
        "updated_by: import_transactions",
        "sensitivity: high",
        "encrypted: true",
        "---",
        "# Transaction Ledger",
        "",
        "> Normalised from bank/card CSV+HTML exports. Covers all accounts.",
        "> Payments and internal transfers excluded from spend columns.",
        "> Encrypted at rest — do not commit plaintext.",
        "",
        "## All Transactions",
        "",
        "| Date | Description | Amount | Category | Account | Institution | Owner | Type |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in sorted(txns, key=lambda x: x.date, reverse=True):
        cat = categorize(t.description, t.raw_category)
        is_pay = _is_payment(t.description, t.raw_category, t.txn_type)
        amt_str = f"-${t.amount:,.2f}" if is_pay else f"${t.amount:,.2f}"
        lines.append(
            f"| {t.date} | {t.description} | {amt_str} | {cat} "
            f"| ****{t.account_last4} | {t.institution} | {t.owner} | {t.txn_type} |"
        )
    return "\n".join(lines) + "\n"


def render_finance_section(monthly_spend: dict, avg: dict[str, float], paypal_yearly: dict) -> str:
    """Markdown block to insert/replace in finance.md."""
    today = date.today()
    lines = [
        f"## Transaction History",
        "",
        f"> Imported {today} from bank/card exports (Apr 2020 – Mar 2026).",
        "> Payments and credits excluded. All amounts in USD.",
        "",
        "### Monthly Spending by Category (last 24 months)",
        "",
        render_monthly_table(monthly_spend),
        "",
        "### 3-Month Rolling Averages (most recent 3 months)",
        "",
        "| Category | Avg/Month |",
        "|---|---|",
    ]
    total_avg = 0.0
    for cat in BUDGET_CATEGORIES:
        v = avg.get(cat, 0)
        if v > 0:
            lines.append(f"| {cat.capitalize()} | ${v:,.0f} |")
            total_avg += v
    lines.append(f"| **Total** | **${total_avg:,.0f}** |")
    lines.append("")

    # PayPal 1099-K tracking
    lines.append("### PayPal / Gig Income — 1099-K Tracking")
    lines.append("")
    lines.append("| Year | PayPal Volume (USD) | 1099-K Threshold | Status |")
    lines.append("|---|---|---|---|")
    for yr in sorted(paypal_yearly.keys(), reverse=True):
        vol = paypal_yearly[yr]
        threshold = 5000
        if vol > 20000:
            status = "🔴 HIGH — likely 1099-K issued"
        elif vol > 5000:
            status = "🟠 Monitor — approaching threshold"
        elif vol > 0:
            status = "🟡 Below threshold"
        else:
            status = "🔵 No activity"
        lines.append(f"| {yr} | ${vol:,.0f} | ${threshold:,} | {status} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State file update
# ---------------------------------------------------------------------------

NET_NEGATIVE_GUARD = 0.20   # abort if replacing section removes >20% of file

def update_finance_md(new_section: str, dry_run: bool) -> None:
    original = FINANCE_MD.read_text(encoding='utf-8')
    original_len = len(original)

    # Replace existing ## Transaction History section, or append before ## Archive
    section_pattern = re.compile(
        r'^## Transaction History\n.*?(?=^## |\Z)',
        re.MULTILINE | re.DOTALL,
    )
    if section_pattern.search(original):
        updated = section_pattern.sub(new_section + '\n', original)
    else:
        # Insert before ## Archive (last section)
        archive_pos = original.rfind('\n## Archive')
        if archive_pos != -1:
            updated = original[:archive_pos] + '\n\n' + new_section + '\n' + original[archive_pos:]
        else:
            updated = original.rstrip() + '\n\n' + new_section + '\n'

    # Also update ## Monthly Burn Rate Summary with real burn rate
    avg_section_start = updated.find('\n## Monthly Burn Rate Summary\n')
    if avg_section_start != -1:
        # Find total line
        total_match = re.search(r'^\*\*Total\*\* \| \*\*\$[\d,]+\*\*', new_section, re.MULTILINE)
        if total_match:
            total_str = re.search(r'\$[\d,]+', total_match.group()).group()
            # Replace placeholder burn rate line if it exists
            updated = re.sub(
                r'(Monthly estimated burn rate[^\n]*)',
                f'Monthly estimated burn rate: {total_str}/mo (3-month rolling avg, import {date.today()})',
                updated,
            )

    # Net-negative guard
    removed = original_len - len(updated)
    if removed > 0 and removed / original_len > NET_NEGATIVE_GUARD:
        print(f"\n⛔ NET-NEGATIVE GUARD: write would remove {removed/original_len:.1%} of finance.md. Aborting.")
        sys.exit(1)

    if dry_run:
        print(f"\n  [DRY RUN] finance.md: +{len(updated) - original_len:+,} bytes")
        print(f"  [DRY RUN] Would insert ## Transaction History ({len(new_section)} chars)")
    else:
        FINANCE_MD.write_text(updated, encoding='utf-8')
        print(f"  ✓ finance.md updated (+{len(updated) - original_len:+,} bytes)")


def write_transactions_md(content: str, dry_run: bool) -> None:
    if dry_run:
        lines = content.count('\n')
        print(f"  [DRY RUN] transactions.md: would create ({lines} lines, {len(content):,} bytes)")
    else:
        TRANSACTIONS_MD.write_text(content, encoding='utf-8')
        print(f"  ✓ transactions.md written ({TRANSACTIONS_MD.stat().st_size:,} bytes)")


# ---------------------------------------------------------------------------
# Preview / dry-run display
# ---------------------------------------------------------------------------

def print_preview(txns: list[Txn], agg: dict) -> None:
    monthly_spend = agg['monthly_spend']
    paypal_yearly = agg['paypal_yearly']
    avg = rolling_avg(monthly_spend)

    spend_txns = [t for t in txns if not _is_payment(t.description, t.raw_category, t.txn_type)]
    all_months = sorted(monthly_spend.keys())

    print("\n" + "━" * 62)
    print("  ARTHA TRANSACTION IMPORT — PREVIEW")
    print("━" * 62)
    print(f"  Files parsed:     {len(set(t.source_file for t in txns))} source files")
    print(f"  Total rows:       {len(txns):,} transactions")
    print(f"  Spend rows:       {len(spend_txns):,} (payments/credits excluded)")
    print(f"  Date range:       {min(t.date for t in txns)} → {max(t.date for t in txns)}")
    print(f"  Months covered:   {len(all_months)} ({all_months[0]} to {all_months[-1]})")
    print()

    # Recent 6 months summary
    recent_months = sorted(monthly_spend.keys())[-6:]
    cats_shown = [c for c in BUDGET_CATEGORIES if any(monthly_spend.get(m, {}).get(c, 0) > 0 for m in recent_months)]
    print("  Monthly spend — last 6 months:")
    col_w = 11
    header = f"  {'Month':<9}" + "".join(f"{c.capitalize()[:9]:>{col_w}}" for c in cats_shown) + f"{'Total':>{col_w}}"
    print(header)
    print("  " + "-" * (9 + col_w * (len(cats_shown) + 1)))
    for ym in reversed(recent_months):
        data = monthly_spend.get(ym, {})
        total = sum(data.get(c, 0) for c in cats_shown)
        row = f"  {ym:<9}" + "".join(f"${data.get(c,0):>9,.0f}" for c in cats_shown) + f"${total:>9,.0f}"
        print(row)
    print()

    # Rolling averages
    total_avg = sum(avg.values())
    print(f"  3-month rolling avg burn rate:  ${total_avg:,.0f}/mo")
    top = sorted(avg.items(), key=lambda x: x[1], reverse=True)[:5]
    for cat, v in top:
        print(f"    {cat.capitalize():<18} ${v:>7,.0f}/mo")
    print()

    # PayPal 1099-K
    print("  PayPal volume (USD, debit spend only):")
    for yr in sorted(paypal_yearly.keys(), reverse=True):
        vol = paypal_yearly[yr]
        flag = " 🟠 >$5K" if vol > 5000 else ""
        if vol > 20000: flag = " 🔴 >$20K"
        print(f"    {yr}: ${vol:,.0f}{flag}")
    print()

    # Top merchants
    from collections import Counter
    top_merchants = Counter(t.description for t in spend_txns
                            if t.description.lower() not in ('', 'paypal', 'payment'))
    print("  Top 10 merchants by transaction count:")
    for merchant, count in top_merchants.most_common(10):
        print(f"    {count:3d}x  {merchant}")
    print()
    print("━" * 62)
    print(f"  Output: state/transactions.md  (~{len(spend_txns):,} rows)")
    print(f"  Output: state/finance.md       (## Transaction History added/updated)")
    print("━" * 62)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv

    if dry_run:
        print("  Mode: DRY RUN (no files will be modified)")
    else:
        print("  Mode: WRITE (state files will be updated)")

    # Ensure vault is decrypted
    if not FINANCE_MD.exists():
        print("\n⛔ state/finance.md not found. Run: python scripts/vault.py decrypt")
        sys.exit(1)

    print(f"\nParsing files in {TRANSACTIONS_DIR} ...")
    txns = parse_all_files(TRANSACTIONS_DIR)

    if not txns:
        print("⛔ No transactions parsed. Check that files exist in ~/Downloads/Transactions")
        sys.exit(1)

    print(f"\nTotal transactions parsed: {len(txns):,}")

    agg = aggregate(txns)
    monthly_spend = agg['monthly_spend']
    paypal_yearly = agg['paypal_yearly']
    avg = rolling_avg(monthly_spend)

    print_preview(txns, agg)

    if dry_run:
        print("\n  Dry run complete. Re-run without --dry-run to write state.")
        return

    # Confirm before writing
    print("\nProceed with writing to state? [y/N] ", end='', flush=True)
    answer = input().strip().lower()
    if answer != 'y':
        print("Aborted.")
        sys.exit(0)

    print("\nWriting state files...")
    finance_section = render_finance_section(monthly_spend, avg, paypal_yearly)
    txns_content = render_transactions_md(txns)

    update_finance_md(finance_section, dry_run=False)
    write_transactions_md(txns_content, dry_run=False)

    print("\n✓ Import complete.")
    print("  Next steps:")
    print("    python scripts/vault.py encrypt   ← re-encrypt both files")
    print("    python scripts/backup.py snapshot ← add transactions.md to GFS backup")


if __name__ == '__main__':
    main()
