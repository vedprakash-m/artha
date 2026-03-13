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
python scripts/safe_cli.py gemini "What is the current Zillow Zestimate for [address] [city] [state]? Also provide Redfin estimate."
```
Update home.md with estimate + date. Note in finance.md net worth section.

---

## Phase 2B Expansions

### Service Provider Rolodex (F7.7)
Maintain in `state/home.md → service_providers`:
```yaml
service_providers:
  - role: "[plumber, electrician, HVAC, etc]"
    name: "[business or person name]"
    phone: "[CONTACT-ON-FILE]"
    last_used: YYYY-MM-DD
    rating: 1-5
    notes: ""
```
Routing: when a "service complete" or "estimate" email arrives from a home service provider,
update or add entry. Surface relevant provider when home-related alerts arise.

### Waste & Recycling Calendar (F7.9)
Track in `state/home.md → waste_schedule`:
```yaml
waste_schedule:
  regular_trash: "[day of week, e.g., Tuesday]"
  recycling: "[day and frequency, e.g., Tuesday biweekly (odd weeks)]"
  yard_waste: "[day and season, e.g., Tuesday Apr-Nov]"
  bulk_item_pickup: "[month or as-scheduled]"
  next_recycling_pickup: YYYY-MM-DD  # updated each catch-up
```
Surface as reminder in briefing the day before pickup (from calendar event or computed schedule).

### HOA / Community Dues (F7.10)
```yaml
hoa:
  monthly_dues: XXXX
  due_date: "[day of month]"
  auto_pay: true|false
  contact_email: "[CONTACT-ON-FILE]"
  next_meeting: YYYY-MM-DD
  outstanding_balance: 0
```
Alert: HOA dues overdue or special assessment notice received.

### Property Tax Tracker (F7.12)
```yaml
property_tax:
  annual_amount: XXXX
  county: "[King County]"
  first_half_due: YYYY-04-30
  second_half_due: YYYY-10-31
  first_half_paid: false
  second_half_paid: false
  auto_pay: false
  notes: ""
```
🟠 URGENT: Property tax due ≤14 days and `paid: false`.

### Lawn & Landscaping (F7.11)
```yaml
landscaping:
  service_provider: "[name]"
  schedule: "[weekly Apr–Oct, biweekly Nov–Mar]"
  next_service: YYYY-MM-DD
  monthly_cost: XXXX
  seasonal_tasks:
    - task: "Spring cleanup"
      target_month: 3
      scheduled: false
    - task: "Fall leaf cleanup"
      target_month: 10
      scheduled: false
    - task: "Irrigation winterization"
      target_month: 10
      scheduled: false
```

### Emergency Preparedness (F7.13)
Track in `state/home.md → emergency_kit`:
```yaml
emergency_kit:
  last_reviewed: YYYY-MM-DD
  next_review_due: YYYY-MM-DD  # annual review
  items:
    - name: "Water (1 gallon/person/day × 14 days)"
      status: stocked|needs_refresh|missing
    - name: "Food (14-day supply)"
      status: stocked|needs_refresh|missing
    - name: "First aid kit"
      status: stocked|needs_refresh|missing
    - name: "Flashlights + batteries"
      status: stocked|needs_refresh|missing
    - name: "Emergency radio"
      status: stocked|needs_refresh|missing
    - name: "Important documents copy"
      status: stocked|needs_refresh|missing
    - name: "Cash ($200)"
      status: stocked|needs_refresh|missing
```
Alert: `next_review_due` passed without update → 🟡 Standard "Emergency kit overdue for annual review."
