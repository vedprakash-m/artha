#!/usr/bin/env python3
"""
parse_contacts.py — Cross-reference Apple Contacts VCF with WhatsApp activity
to extract warm contacts for Artha contacts.md / social.md

Strategy:
  1. Parse Apple Contacts VCF export (skip huge PHOTO blobs)
  2. Query WhatsApp ChatStorage.sqlite for DM chats with recent activity
  3. Normalize phone numbers and match VCF ↔ WhatsApp
  4. Output warm contacts sorted by recency, grouped by tier

Usage:
  python scripts/parse_contacts.py \\
    --vcf "/Users/ved/Downloads/contacts/[Speaker Name] and 1,911 others.vcf" \\
    --warm-days 365 \\
    --output /tmp/warm_contacts.json

Output files:
  /tmp/warm_contacts.json      — machine-readable enriched contact list
  /tmp/warm_contacts_report.md — human-readable markdown for contacts.md
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Core Data epoch: seconds from Unix epoch to 2001-01-01
APPLE_EPOCH = 978307200

WA_CHAT_DB = Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
WA_CONTACTS_DB = Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ContactsV2.sqlite"
VCF_DEFAULT = Path.home() / "Downloads/contacts/[Speaker Name] and 1,911 others.vcf"


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

def norm(phone: str) -> str:
    """Return digits-only string from any phone format."""
    return re.sub(r"\D", "", phone or "")


def phones_match(vcf_phones: list, wa_jid_phone: str) -> bool:
    """
    Return True if any VCF phone matches the WhatsApp JID phone.
    Uses last-10-digit comparison to handle country code prefix differences.
    """
    b = norm(wa_jid_phone)
    if not b:
        return False
    b10 = b[-10:] if len(b) >= 10 else b
    for p in vcf_phones:
        a = norm(p)
        if not a:
            continue
        a10 = a[-10:] if len(a) >= 10 else a
        if a == b or a10 == b10:
            return True
    return False


# ---------------------------------------------------------------------------
# VCF parser
# ---------------------------------------------------------------------------

def parse_vcf(vcf_path: str) -> list:
    """
    Parse VCF 3.0 file efficiently, skipping embedded PHOTO blobs.
    Returns list of dicts: {name, phones[], emails[], birthday, note}
    """
    contacts = []
    current = None
    in_photo = False

    with open(vcf_path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\r\n")

            # Folded/continuation lines start with whitespace
            if line and (line[0] == " " or line[0] == "\t"):
                # Skip photo continuation — everything else we don't need to unfold
                # because our target fields (FN, TEL, EMAIL, BDAY) are never folded
                continue

            in_photo = False

            if line == "BEGIN:VCARD":
                current = {"name": "", "phones": [], "emails": [], "birthday": "", "note": ""}
                continue

            if line == "END:VCARD":
                if current and current["name"]:
                    contacts.append(current)
                current = None
                continue

            if current is None or ":" not in line:
                continue

            key_part, _, val = line.partition(":")
            key = key_part.upper().split(";")[0]
            val = val.strip()

            if key == "FN":
                current["name"] = val
            elif key == "BDAY":
                current["birthday"] = val
            elif key == "NOTE":
                current["note"] = val[:300]
            elif key == "TEL":
                if val:
                    current["phones"].append(val)
            elif key == "EMAIL":
                if val:
                    current["emails"].append(val.lower())
            elif key == "PHOTO":
                in_photo = True  # Next continuation lines will be skipped

    return contacts


# ---------------------------------------------------------------------------
# WhatsApp data loaders
# ---------------------------------------------------------------------------

def _open_db_copy(db_path: str) -> sqlite3.Connection:
    """Copy the DB to /tmp to avoid locking the live file, open read-only."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = tmp.name
    shutil.copy2(db_path, tmp_path)
    # Register cleanup
    import atexit
    atexit.register(lambda: os.unlink(tmp_path) if os.path.exists(tmp_path) else None)
    conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_wa_chats(chat_db: str, warm_days: int) -> dict:
    """
    Returns {jid_phone: {wa_name, last_wa_date, days_since_contact}}
    for individual (non-group) chats within warm_days.
    """
    cutoff = (datetime.now(timezone.utc).timestamp() - warm_days * 86400) - APPLE_EPOCH
    conn = _open_db_copy(chat_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ZPARTNERNAME, ZCONTACTJID, ZLASTMESSAGEDATE
            FROM ZWACHATSESSION
            WHERE ZCONTACTJID LIKE '%@s.whatsapp.net'
              AND ZLASTMESSAGEDATE > ?
            ORDER BY ZLASTMESSAGEDATE DESC
            """,
            (cutoff,),
        )
        result = {}
        for row in cur.fetchall():
            jid = row["ZCONTACTJID"]
            phone = jid.split("@")[0]  # "14257662814" or "919049121401"
            ts = row["ZLASTMESSAGEDATE"] + APPLE_EPOCH
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            days_ago = (datetime.now(timezone.utc) - dt).days
            result[phone] = {
                "wa_name": row["ZPARTNERNAME"] or "",
                "last_wa_date": dt.strftime("%Y-%m-%d"),
                "days_since_contact": days_ago,
            }
        return result
    finally:
        conn.close()


def load_wa_group_chats(chat_db: str, warm_days: int) -> list:
    """
    Returns list of active group chats: {group_name, last_date, days_ago}
    Useful for identifying family WhatsApp groups.
    """
    cutoff = (datetime.now(timezone.utc).timestamp() - warm_days * 86400) - APPLE_EPOCH
    conn = _open_db_copy(chat_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ZPARTNERNAME, ZUNREADCOUNT, ZLASTMESSAGEDATE
            FROM ZWACHATSESSION
            WHERE ZCONTACTJID LIKE '%@g.us'
              AND ZLASTMESSAGEDATE > ?
            ORDER BY ZLASTMESSAGEDATE DESC
            """,
            (cutoff,),
        )
        result = []
        for row in cur.fetchall():
            ts = row["ZLASTMESSAGEDATE"] + APPLE_EPOCH
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            result.append({
                "group_name": row["ZPARTNERNAME"] or "(unknown group)",
                "last_date": dt.strftime("%Y-%m-%d"),
                "days_ago": (datetime.now(timezone.utc) - dt).days,
            })
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cross-reference
# ---------------------------------------------------------------------------

def assign_tier(days: int, wa_phone: str) -> str:
    """Heuristic tier based on recency. User should review and adjust."""
    is_india = wa_phone.startswith("91") and len(wa_phone) == 12
    if days <= 30:
        return "close_friend"
    elif days <= 90:
        return "close_friend" if not is_india else "extended_family"
    elif days <= 180:
        return "extended_family"
    else:
        return "acquaintance"


def assign_timezone(wa_phone: str) -> str:
    if wa_phone.startswith("91"):
        return "Asia/Kolkata"
    # Rough US timezone by area code
    return "America/Los_Angeles"


def enrich_contacts(vcf_contacts: list, wa_chats: dict, warm_days: int) -> tuple:
    """
    Cross-reference VCF contacts with WhatsApp chat activity.
    Returns (warm_matched, wa_only) where:
      warm_matched = VCF contacts that appear in WA recent chats
      wa_only = WA recent chats with no VCF match (WA-only contacts)
    """
    wa_phones = list(wa_chats.keys())
    matched_wa_phones = set()

    warm_matched = []
    for c in vcf_contacts:
        if not c["phones"]:
            continue
        for wa_phone in wa_phones:
            if phones_match(c["phones"], wa_phone):
                wa_data = wa_chats[wa_phone]
                matched_wa_phones.add(wa_phone)
                warm_matched.append({
                    "name": c["name"],
                    "phones": c["phones"],
                    "emails": c["emails"],
                    "birthday": c["birthday"],
                    "note": c["note"],
                    "last_wa_date": wa_data["last_wa_date"],
                    "days_since_contact": wa_data["days_since_contact"],
                    "wa_phone": wa_phone,
                    "wa_name": wa_data["wa_name"],
                    "tier": assign_tier(wa_data["days_since_contact"], wa_phone),
                    "timezone": assign_timezone(wa_phone),
                    "source": "vcf+whatsapp",
                })
                break  # Match found, move on

    # WhatsApp-only contacts (not in Apple Contacts VCF)
    wa_only = []
    for wa_phone, wa_data in wa_chats.items():
        if wa_phone not in matched_wa_phones:
            wa_only.append({
                "name": wa_data["wa_name"] or f"+{wa_phone}",
                "phones": [f"+{wa_phone}"],
                "emails": [],
                "birthday": "",
                "note": "WhatsApp-only — not in Apple Contacts",
                "last_wa_date": wa_data["last_wa_date"],
                "days_since_contact": wa_data["days_since_contact"],
                "wa_phone": wa_phone,
                "wa_name": wa_data["wa_name"],
                "tier": assign_tier(wa_data["days_since_contact"], wa_phone),
                "timezone": assign_timezone(wa_phone),
                "source": "whatsapp_only",
            })

    warm_matched.sort(key=lambda x: x["days_since_contact"])
    wa_only.sort(key=lambda x: x["days_since_contact"])
    return warm_matched, wa_only


# ---------------------------------------------------------------------------
# Markdown report generator
# ---------------------------------------------------------------------------

def bucket_label(days: int) -> str:
    if days <= 7:
        return "This week"
    elif days <= 30:
        return "This month (0–30d)"
    elif days <= 90:
        return "Last 3 months (31–90d)"
    elif days <= 180:
        return "Last 6 months (91–180d)"
    else:
        return "Last year (181–365d)"


def generate_markdown_report(warm_matched: list, wa_only: list, groups: list, warm_days: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# Warm Contacts Report — {now}",
        f"> Generated by parse_contacts.py | Warm window: {warm_days} days",
        f"> Total warm contacts: {len(warm_matched)} matched + {len(wa_only)} WhatsApp-only",
        "",
    ]

    # Active WhatsApp Groups
    lines += [
        "## Active WhatsApp Groups",
        "| Group | Last Activity | Days Ago |",
        "|-------|--------------|----------|",
    ]
    for g in groups[:20]:
        lines.append(f"| {g['group_name']} | {g['last_date']} | {g['days_ago']}d |")
    lines.append("")

    # Warm Contacts by recency bucket
    lines += ["## Warm Contacts (VCF + WhatsApp Match)", ""]
    bucket_order = ["This week", "This month (0–30d)", "Last 3 months (31–90d)",
                    "Last 6 months (91–180d)", "Last year (181–365d)"]
    by_bucket: dict = {b: [] for b in bucket_order}
    for c in warm_matched:
        b = bucket_label(c["days_since_contact"])
        by_bucket[b].append(c)

    for b in bucket_order:
        contacts = by_bucket[b]
        if not contacts:
            continue
        lines.append(f"### {b} ({len(contacts)} contacts)")
        lines.append("| Name | Phone | Last WA | Birthday | Tier | TZ |")
        lines.append("|------|-------|---------|----------|------|----|")
        for c in contacts:
            phone = c["phones"][0] if c["phones"] else c.get("wa_phone", "")
            bday = c.get("birthday", "") or "—"
            lines.append(
                f"| {c['name']} | {phone} | {c['last_wa_date']} | {bday} | {c['tier']} | {c['timezone']} |"
            )
        lines.append("")

    # WhatsApp-only contacts
    if wa_only:
        lines += [
            f"## WhatsApp-Only Contacts (not in Apple Contacts) — {len(wa_only)} total",
            "| WA Name | Phone | Last WA | Days Ago | Tier |",
            "|---------|-------|---------|----------|------|",
        ]
        for c in wa_only[:50]:
            lines.append(
                f"| {c['name']} | +{c['wa_phone']} | {c['last_wa_date']} | {c['days_since_contact']}d | {c['tier']} |"
            )
        if len(wa_only) > 50:
            lines.append(f"| *(and {len(wa_only) - 50} more)* | | | | |")
        lines.append("")

    # contacts.md ready tables
    lines += [
        "---",
        "## contacts.md — Ready-to-Paste Sections",
        "",
        "### Family (fill in manually from top of report)",
        "| Name | Relationship | Phone | Email | Notes |",
        "|---|---|---|---|---|",
        "| Vedprakash (Ved) | Self | +1 (415) 952-8201 | vedprakash.m@outlook.com | |",
        "| Archana | Spouse | +1 (425) 504-2375 | | |",
        "| Parth | Son (17) | +1 (425) 504-3183 | | School: Tesla STEM HS |",
        "| Trisha | Daughter (12) | +1 (425) 766-2814 | | |",
        "",
    ]

    # India extended family (India numbers in warm matched)
    india_contacts = [c for c in warm_matched if c.get("wa_phone", "").startswith("91")]
    if india_contacts:
        lines += [
            "### Extended Family / India Contacts (from warm WA matches)",
            "| Name | Relationship | Phone | Location | Last Contact | Notes |",
            "|---|---|---|---|---|---|",
        ]
        for c in india_contacts:
            phone = c["phones"][0] if c["phones"] else f"+{c['wa_phone']}"
            lines.append(f"| {c['name']} | [TBD] | {phone} | India | {c['last_wa_date']} | |")
        lines.append("")

    # US close friends
    us_contacts = [c for c in warm_matched
                   if not c.get("wa_phone", "").startswith("91")
                   and c["days_since_contact"] <= 90]
    if us_contacts:
        lines += [
            "### Close Friends / US Contacts (messaged in last 90 days)",
            "| Name | Relationship | Phone | Email | Last Contact | Notes |",
            "|---|---|---|---|---|---|",
        ]
        for c in us_contacts:
            phone = c["phones"][0] if c["phones"] else f"+{c['wa_phone']}"
            email = c["emails"][0] if c["emails"] else ""
            lines.append(f"| {c['name']} | friend | {phone} | {email} | {c['last_wa_date']} | |")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Cross-ref Apple VCF + WhatsApp for warm contacts")
    ap.add_argument("--vcf", default=str(VCF_DEFAULT), help="Path to .vcf export file")
    ap.add_argument("--wa-chat-db", default=str(WA_CHAT_DB), help="Path to WhatsApp ChatStorage.sqlite")
    ap.add_argument("--wa-contacts-db", default=str(WA_CONTACTS_DB), help="Path to WhatsApp ContactsV2.sqlite")
    ap.add_argument("--warm-days", type=int, default=365, help="Days window for 'warm' contacts")
    ap.add_argument("--output", default="/tmp/warm_contacts.json", help="JSON output path")
    ap.add_argument("--report", default="/tmp/warm_contacts_report.md", help="Markdown report output path")
    args = ap.parse_args()

    # Validate paths
    for label, path in [("VCF", args.vcf), ("WA chat DB", args.wa_chat_db), ("WA contacts DB", args.wa_contacts_db)]:
        if not Path(path).exists():
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            sys.exit(1)

    print(f"[1/5] Parsing VCF: {args.vcf}")
    vcf_contacts = parse_vcf(args.vcf)
    print(f"      → {len(vcf_contacts)} contacts parsed")

    print(f"[2/5] Loading WhatsApp DM chats (last {args.warm_days} days)...")
    wa_chats = load_wa_chats(args.wa_chat_db, args.warm_days)
    print(f"      → {len(wa_chats)} warm DM chats found")

    print(f"[3/5] Loading active WhatsApp groups...")
    wa_groups = load_wa_group_chats(args.wa_chat_db, args.warm_days)
    print(f"      → {len(wa_groups)} active groups")

    print("[4/5] Cross-referencing phone numbers...")
    warm_matched, wa_only = enrich_contacts(vcf_contacts, wa_chats, args.warm_days)
    print(f"      → {len(warm_matched)} VCF contacts matched to WA activity")
    print(f"      → {len(wa_only)} WhatsApp-only contacts (not in Apple Contacts)")

    print("[5/5] Writing output files...")
    output = {
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "warm_days": args.warm_days,
        "stats": {
            "vcf_total": len(vcf_contacts),
            "wa_warm_chats": len(wa_chats),
            "va_active_groups": len(wa_groups),
            "matched": len(warm_matched),
            "wa_only": len(wa_only),
        },
        "groups": wa_groups,
        "warm_contacts": warm_matched,
        "wa_only_contacts": wa_only,
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    report = generate_markdown_report(warm_matched, wa_only, wa_groups, args.warm_days)
    with open(args.report, "w") as f:
        f.write(report)

    print(f"\nDone.")
    print(f"  JSON   → {args.output}")
    print(f"  Report → {args.report}")

    # Print quick summary
    print("\n--- Summary by recency ---")
    buckets = {"0-7d": 0, "8-30d": 0, "31-90d": 0, "91-180d": 0, "181-365d": 0}
    for c in warm_matched:
        d = c["days_since_contact"]
        if d <= 7:
            buckets["0-7d"] += 1
        elif d <= 30:
            buckets["8-30d"] += 1
        elif d <= 90:
            buckets["31-90d"] += 1
        elif d <= 180:
            buckets["91-180d"] += 1
        else:
            buckets["181-365d"] += 1
    for b, n in buckets.items():
        print(f"  {b}: {n} contacts")

    print("\n--- Active WhatsApp Groups ---")
    for g in wa_groups[:10]:
        print(f"  [{g['days_ago']:3d}d] {g['group_name']}")


if __name__ == "__main__":
    main()
