---
schema_version: "1.0"
domain: home
priority: P1
sensitivity: medium
last_updated: 2026-03-07T22:52:33
---
# Home Domain Prompt

## Purpose
Track home maintenance, repairs, HOA, utilities, and property-related matters.

## Sender Signatures (route here)
- HOA emails, property management company
- Contractors, repair services, home maintenance vendors
- Subject: maintenance, repair, inspection, service, plumber, HVAC, electrician
- Subject: HOA, homeowners association, dues, violation, meeting
- Subject: property tax, mortgage statement, escrow
- Utility: PSE (power), water, sewer, garbage (if separate from finance)
- Home insurance: claims, renewals (also route to insurance.md)
- Real estate alerts, Zillow, Redfin (if set up)

## Extraction Rules
1. **Item**: what is the maintenance issue or event?
2. **Status**: scheduled, in progress, completed, action needed
3. **Vendor** and contact (if vendor email)
4. **Date** (appointment, deadline, renewal)
5. **Cost** (estimate or invoice)
6. **Action**: schedule, pay, respond, approve?

## Alert Thresholds
🟠 **URGENT**:
- Active repair/maintenance requiring response within 48 hours
- HOA violation notice requiring response
- Property tax due within 14 days

🟡 **STANDARD**:
- Maintenance appointment this week
- HOA meeting announcement
- Utility bill (also in finance.md — note here if large/unusual)
- Service/warranty expiry within 30 days

## State File Update Protocol
Read `state/home.md` first. Then:
1. **Active Maintenance**: add/update items with vendor + date
2. **Upcoming**: add deadlines + due items
3. **Appliance Log**: update if warranty/service mentioned
4. **Vendors**: add new vendor contacts
5. Archive completed maintenance (keep for warranty/reference)

## Briefing Format
```
### Home
• [Maintenance item]: [status] — [date/action]
• [HOA/property item]: [notes]
```
Omit if nothing active or upcoming.

## Home Value Tracking (quarterly)
When home value update is due (>90 days since last check):
```
bash scripts/safe_cli.sh gemini "What is the current Zillow Zestimate for [address] [city] WA? Also provide Redfin estimate."
```
Update home.md with estimate + date. Note in finance.md net worth section.
