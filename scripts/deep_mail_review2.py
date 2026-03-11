#!/usr/bin/env python3
"""
Targeted deep mail review — strict keyword matching for state enrichment.
"""
import json, os, re
from collections import defaultdict

ARTHA_DIR = r"C:\Users\vemishra\OneDrive\Artha"

def load_emails():
    all_emails = []
    for fname, source in [("gmail_deep.jsonl", "gmail"), ("outlook_deep.jsonl", "outlook")]:
        fpath = os.path.join(ARTHA_DIR, fname)
        if not os.path.exists(fpath):
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
    return all_emails

def match_strict(email, sender_patterns=None, subject_patterns=None):
    """Match email by sender AND/OR subject patterns (strict)."""
    sender = email.get("from", "").lower()
    subject = email.get("subject", "").lower()
    
    sender_match = False
    subject_match = False
    
    if sender_patterns:
        for p in sender_patterns:
            if p.lower() in sender:
                sender_match = True
                break
    
    if subject_patterns:
        for p in subject_patterns:
            if re.search(p, subject, re.IGNORECASE):
                subject_match = True
                break
    
    if sender_patterns and subject_patterns:
        return sender_match or subject_match
    elif sender_patterns:
        return sender_match
    elif subject_patterns:
        return subject_match
    return False

def print_email(email, show_body=False, body_len=800):
    date = email.get("date_iso", email.get("date", ""))[:16]
    src = email.get("_source", "?")
    sender = email.get("from", "?")[:60]
    subject = email.get("subject", "(no subject)")[:100]
    print(f"  [{src}] {date} | {sender}")
    print(f"    Subject: {subject}")
    snippet = email.get("snippet", "")[:200]
    if snippet:
        print(f"    Snippet: {snippet}")
    if show_body:
        body = email.get("body", "")[:body_len]
        body = re.sub(r'\n{3,}', '\n\n', body)
        body = re.sub(r'[ \t]{2,}', ' ', body)
        if body:
            print(f"    Body: {body}")
    print()

def main():
    emails = load_emails()
    print(f"Loaded {len(emails)} emails\n")
    
    # Sort all emails by date descending
    emails.sort(key=lambda e: e.get("date_iso", ""), reverse=True)
    
    # ===== IMMIGRATION =====
    print("=" * 80)
    print("1. IMMIGRATION (USCIS, Fragomen, visa, green card)")
    print("=" * 80)
    imm = [e for e in emails if match_strict(e,
        sender_patterns=["fragomen", "uscis", "ceac.state.gov", "nvc.state.gov", "travel.state.gov"],
        subject_patterns=[r"\buscis\b", r"\bi-140\b", r"\bi-485\b", r"\bi-765\b", r"\bgreen.?card\b",
                          r"\bvisa\b", r"\bh-1b\b", r"\bimmigration\b", r"\bpetition\b",
                          r"\bbiometric\b", r"\bead\b.*card", r"\bpriority date\b",
                          r"\bfragomen\b", r"\bapproval notice\b"]
    )]
    print(f"Found: {len(imm)} emails\n")
    for e in imm:
        print_email(e, show_body=True, body_len=1200)
    
    # ===== FINANCE (specific senders) =====
    print("=" * 80)
    print("2. FINANCE (banks, investments, tax, mortgage)")
    print("=" * 80)
    fin = [e for e in emails if match_strict(e,
        sender_patterns=["chase.com", "wellsfargo", "wells fargo", "fidelity", "vanguard",
                         "schwab", "capitalone", "capital one", "amex", "american express",
                         "discover", "citi", "irs.gov", "turbotax", "intuit",
                         "venmo", "paypal", "zelle", "robinhood", "etrade",
                         "mint.com", "creditkarma", "experian", "equifax", "transunion",
                         "morgan stanley", "espp", "fidelity.com", "merrilledge",
                         "wealthfront", "betterment"],
        subject_patterns=[r"\b(tax|w-?2|1099|refund)\b.*\b(20[0-9]{2})\b",
                          r"\bmortgage\b.*statement", r"\bloan payment\b",
                          r"\baccount.*alert\b", r"\bstatement.*ready\b",
                          r"\b401k\b", r"\brsu\b.*vest", r"\bdividend\b"]
    )]
    print(f"Found: {len(fin)} emails\n")
    for e in fin:
        print_email(e, show_body=True, body_len=1000)
    
    # ===== INSURANCE =====
    print("=" * 80)
    print("3. INSURANCE (Progressive, Goosehead, policy changes)")
    print("=" * 80)
    ins = [e for e in emails if match_strict(e,
        sender_patterns=["progressive", "goosehead", "jason.flagg"],
        subject_patterns=[r"\bpolicy\b.*\b(renewal|change|cancel)\b",
                          r"\binsurance\b.*\b(update|renewal|premium)\b",
                          r"\bcoverage\b.*\b(change|update)\b"]
    )]
    print(f"Found: {len(ins)} emails\n")
    for e in ins:
        print_email(e, show_body=True, body_len=1000)
    
    # ===== VEHICLE =====
    print("=" * 80)
    print("4. VEHICLE (Kia, Mazda, lease, registration, recall)")
    print("=" * 80)
    veh = [e for e in emails if match_strict(e,
        sender_patterns=["kia", "mazda", "chargepoint", "dmv", "dol.wa.gov",
                         "carfax", "carvana", "autozone", "acar"],
        subject_patterns=[r"\bkia\b", r"\bmazda\b", r"\bev6\b", r"\bcx-?50\b",
                          r"\blease\b.*\b(payment|end|return)\b",
                          r"\bregistration\b", r"\brecall\b", r"\bchargepoint\b",
                          r"\bvehicle\b.*\b(service|maintenance)\b"]
    )]
    print(f"Found: {len(veh)} emails\n")
    for e in veh:
        print_email(e, show_body=True, body_len=1000)
    
    # ===== HOME =====
    print("=" * 80)
    print("5. HOME (PSE, HOA, property tax, mortgage, repairs)")
    print("=" * 80)
    home = [e for e in emails if match_strict(e,
        sender_patterns=["pse.com", "puget sound", "king county", "sammamish",
                         "talus", "bob heating", "wesley electric", "wright connection",
                         "aquaquip"],
        subject_patterns=[r"\bproperty tax\b", r"\bhoa\b.*\b(dues|meeting|assessment)\b",
                          r"\butility\b.*bill", r"\belectric\b.*bill",
                          r"\bgas\b.*bill", r"\bhomeowner\b"]
    )]
    print(f"Found: {len(home)} emails\n")
    for e in home:
        print_email(e, show_body=True, body_len=1000)
    
    # ===== HEALTH =====
    print("=" * 80)
    print("6. HEALTH (medical, dental, vision, pharmacy)")
    print("=" * 80)
    hlth = [e for e in emails if match_strict(e,
        sender_patterns=["premera", "delta dental", "vsp", "kaiser", "evergreen",
                         "overlake", "swedish", "multicare", "labcorp", "quest",
                         "cvs", "walgreens", "rite aid", "zocdoc", "mychart",
                         "healthsparq"],
        subject_patterns=[r"\bappointment\b.*\b(confirm|remind|schedul)\b",
                          r"\blab results\b", r"\bprescription\b.*ready",
                          r"\beob\b", r"\bexplanation of benefits\b",
                          r"\bimmunization\b", r"\bvaccin\b",
                          r"\bclaim\b.*\b(process|paid|denied)\b"]
    )]
    print(f"Found: {len(hlth)} emails\n")
    for e in hlth:
        print_email(e, show_body=True, body_len=1000)
    
    # ===== KIDS/SCHOOL =====
    print("=" * 80)
    print("7. KIDS/SCHOOL (Skyline, Pine Lake, ISD, college)")
    print("=" * 80)
    kids = [e for e in emails if match_strict(e,
        sender_patterns=["skyline", "pine lake", "issaquah.wednet", "isd411",
                         "collegeboard", "commonapp", "naviance", "parchment",
                         "peachjar"],
        subject_patterns=[r"\breport card\b", r"\bconference\b.*parent",
                          r"\bsat\b.*score", r"\bpsat\b", r"\bact\b.*score",
                          r"\bcollege\b.*\b(admit|accept|decision|application)\b",
                          r"\bscholarship\b", r"\bgraduation\b"]
    )]
    print(f"Found: {len(kids)} emails\n")
    for e in kids:
        print_email(e, show_body=True, body_len=800)
    
    # ===== EMPLOYMENT (Microsoft-specific) =====
    print("=" * 80)
    print("8. EMPLOYMENT (Microsoft HR, benefits, stock)")
    print("=" * 80)
    emp = [e for e in emails if match_strict(e,
        sender_patterns=["@microsoft.com", "myhr", "benefitfocus", "fidelity",
                         "morganstanley", "stockplanconnect"],
        subject_patterns=[r"\brsu\b", r"\bespp\b", r"\bperformance\b.*review",
                          r"\bbenefits\b.*\b(enrollment|change|update)\b",
                          r"\bpayroll\b", r"\bcompensation\b",
                          r"\borg\b.*\b(change|announce)\b"]
    )]
    print(f"Found: {len(emp)} emails\n")
    for e in emp:
        print_email(e, show_body=True, body_len=1000)
    
    # ===== TRAVEL =====
    print("=" * 80)
    print("9. TRAVEL (flights, hotels, reservations)")
    print("=" * 80)
    travel = [e for e in emails if match_strict(e,
        sender_patterns=["delta.com", "alaskaair", "united.com", "southwest",
                         "marriott", "hilton", "hyatt", "airbnb", "vrbo",
                         "expedia", "booking.com", "kayak", "google.com/travel",
                         "tsa.gov", "cbp.dhs.gov"],
        subject_patterns=[r"\bflight\b.*\b(confirm|itinerary|book|cancel)\b",
                          r"\bhotel\b.*\b(confirm|reserv|book)\b",
                          r"\btrip\b.*\b(confirm|itinerary)\b",
                          r"\bglobal entry\b", r"\bpassport\b.*\b(renew|expire)\b",
                          r"\bboarding pass\b"]
    )]
    print(f"Found: {len(travel)} emails\n")
    for e in travel:
        print_email(e, show_body=True, body_len=800)
    
    # ===== SUBSCRIPTIONS / DIGITAL SERVICES =====
    print("=" * 80)
    print("10. SUBSCRIPTIONS & DIGITAL (renewals, accounts, security)")
    print("=" * 80)
    digital = [e for e in emails if match_strict(e,
        sender_patterns=[],
        subject_patterns=[r"\bsubscription\b.*\b(renew|cancel|expir|charged)\b",
                          r"\bsecurity alert\b", r"\bunusual sign.?in\b",
                          r"\baccount\b.*\b(locked|suspended|compromised)\b",
                          r"\bdomain\b.*\b(renew|expir)\b",
                          r"\bpassword\b.*\b(reset|change|expire)\b"]
    )]
    print(f"Found: {len(digital)} emails\n")
    for e in digital:
        print_email(e, show_body=True, body_len=600)
    
    # ===== LEGAL / IMPORTANT NOTICES =====
    print("=" * 80)
    print("11. LEGAL / IMPORTANT NOTICES")
    print("=" * 80)
    legal = [e for e in emails if match_strict(e,
        sender_patterns=["@court", "@law", "attorney", "legal"],
        subject_patterns=[r"\blegal notice\b", r"\bsubpoena\b", r"\bjury duty\b",
                          r"\bclass action\b", r"\bsettlement\b",
                          r"\bwarranty\b.*\b(expir|claim)\b"]
    )]
    print(f"Found: {len(legal)} emails\n")
    for e in legal:
        print_email(e, show_body=True, body_len=800)
    
    # ===== QUICK SCAN: All unique senders =====
    print("=" * 80)
    print("12. ALL UNIQUE SENDERS (for manual review)")
    print("=" * 80)
    senders = defaultdict(int)
    for e in emails:
        sender = e.get("from", "unknown")
        # Extract just the email address
        m = re.search(r'<([^>]+)>', sender)
        if m:
            sender = m.group(1).lower()
        else:
            sender = sender.lower().strip('"')
        senders[sender] += 1
    
    for sender, count in sorted(senders.items(), key=lambda x: -x[1])[:100]:
        print(f"  {count:3d}x  {sender}")

if __name__ == "__main__":
    main()
