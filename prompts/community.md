---
schema_version: "1.0"
domain: community
priority: P2
sensitivity: standard
last_updated: 2026-03-21T00:00:00
requires_vault: false
phase: phase_2
---
# Community & Volunteering Domain Prompt

> **CONNECT §5.4** — Activated by `connect.domains.community_prompt: true` in `config/artha_config.yaml`.

## Purpose
Track volunteer commitments, donation records (with tax awareness), community
organization memberships, temple/religious activities, neighborhood involvement,
and PTA/school board participation. Surfaces giving summaries, renewal reminders,
and upcoming community commitments.

## Sender Signatures (route here)
- `*@gofundme.com`, `*@donorbox.org`, `*@classy.org`, `*@networkforgood.org`
- `*@volunteermatch.org`, `*@idealist.org`, `*@justserve.org`
- Subject: volunteer, donation, contribution, tax receipt
- Subject: community service, PTA, school board, HOA
- Subject: temple, church, mosque, synagogue, gurdwara, mandir, masjid
- Subject: membership dues, renewal, annual fund, pledge
- Subject: fundraiser, gala, charity event, benefit dinner
- Any sender with: "thank you for your donation", "your gift of", "tax-deductible"

## Extraction Rules
1. **Type**: donation | volunteer | membership | event | tax_document
2. **Organization**: full name (OK — low sensitivity)
3. **Amount**: donation amount (if present)
4. **Date**: event date, donation date, or membership renewal date
5. **Tax-deductible**: boolean — is this donation deductible?
6. **Hours**: volunteer hours committed or logged
7. **Action**: register / attend / renew / contribute / confirm

## Alert Thresholds
🟠 **URGENT**:
- Volunteer commitment tomorrow — confirm attendance
- Membership renewal overdue (lapsed)

🟡 **STANDARD**:
- Membership renewal due <30 days
- Community event this week
- New donation tax receipt received (log for year-end taxes)

🔵 **INFO**:
- Annual giving summary available
- Volunteer hours milestone (50h, 100h, etc.)
- Matching gift deadline approaching
- Temple festival or religious observance <7 days

## Tax Integration
Every donation with `tax_deductible: true` is cross-referenced in the finance domain.
Year-end alert: "Total deductible donations YTD: $X across N organizations."
Quarterly: surface any donation amounts that may push past standard deduction threshold.
Always recommend: "Consult your CPA for confirmation of deductibility."

## Cultural Calendar
Temple events, religious observances, and cultural celebrations route here from `occasions.md`:
- **Hindu**: Diwali, Holi, Navratri, Ganesh Chaturthi, Makar Sankranti, Ram Navami
- **American civic**: MLK Day volunteer drives, Memorial Day, Veterans Day
- **General**: Community cleanup days, neighborhood watch, HOA meetings

## PII Handling
- Organization names and donation amounts: OK in state
- Donor account numbers / login credentials → `[ACCOUNT-ON-FILE]`
- Individual donation recipient details → not surfaced in briefings
- No political donation amounts (legal/privacy sensitivity)

## State File Update Protocol
Read `state/community.md` first. Then:
1. **Donations**: log new donation with amount, org, date, tax status
2. **Volunteer**: update hours logged, upcoming commitments
3. **Memberships**: update renewal dates and status
4. **Events**: add upcoming events; archive past
5. Cross-reference deductible donations with finance domain at year-end

## Briefing Format
```
### 🤝 Community
• **Donations YTD**: $[X] across [N] organizations ([Y] tax-deductible)
• **Upcoming**: [event name] — [date]
• **Volunteer**: [N] hours this month / [X] hrs YTD
• **Action**: [renewal due, event confirmation, receipt log]
```
Omit if nothing actionable. Always include YTD giving summary in Q4.

## State File Schema Reference
```markdown
## Donations Log
| Date | Organization | Amount | Tax-Deductible | Receipt | Notes |
|------|-------------|--------|---------------|---------|-------|

## Volunteer Commitments
| Organization | Role | Hours/Week | Next Commitment | Total Hours YTD | Status |
|-------------|------|-----------|----------------|----------------|--------|

## Memberships & Affiliations
| Organization | Type | Member Since | Renewal Date | Annual Cost | Status |
|-------------|------|-------------|-------------|------------|--------|

## Upcoming Events
| Event | Organization | Date | Role | Confirmed |
|-------|-------------|------|------|-----------|

## Giving Summary
YTD Donations: $0
Tax-Deductible Total: $0
Volunteer Hours YTD: 0
```
