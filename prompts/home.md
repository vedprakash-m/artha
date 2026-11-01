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
  county: "[user's county — see §1]"
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

---

## Renter-Overlay
> **Active when:** `household.tenure = renter` in `config/user_profile.yaml`
>
> When renter mode is active, **replace** the default owner-centric rules below
> with the renter-specific rules in this section. Suppress any rules marked
> ~~strikethrough~~ entirely — do not surface them in briefings.

### Suppressed (Owner-Only) Topics
When `household.tenure = renter`, **do not track or surface**:
- ~~Mortgage payment tracking or escrow analysis~~
- ~~Property tax tracker (F7.12) — owner obligation, not renter~~
- ~~HOA dues tracker (F7.10) — unless property manager explicitly bills renter for it~~
- ~~Home value / Zillow Zestimate checks~~
- ~~Lawn & landscaping ownership tasks~~ (report maintenance requests only)

### Renter-Specific Routing (replaces default sender signatures)
Route to home domain when:
- Property manager / landlord communications
- Subject: rent, lease, move-out, move-in, security deposit
- Subject: maintenance request, repair request, work order
- Subject: lease renewal, notice to vacate, rent increase
- Renter's insurance: policy renewals, claims, premium changes
- Utility bills (electric, gas, water, internet) if billed directly to tenant

### Renter Extraction Rules (replaces §Extraction Rules for renters)
1. **Type**: rent payment | maintenance request | lease event | renter's insurance | utility
2. **Amount**: dollar amount (rent, utility, insurance premium)
3. **Due date**: when payment or response is required
4. **Lease details**: expiry date, renewal deadline, notice period
5. **Action**: pay | respond to landlord | file maintenance request | renew?

### Renter Alert Thresholds
🔴 **CRITICAL** (renter mode):
- Lease expires within 30 days with no renewal signed
- Eviction notice or "notice to vacate" received
- Security deposit dispute or improper deduction notice

🟠 **URGENT** (renter mode):
- Rent due within 5 days
- Lease renewal decision required within 14 days
- Maintenance request pending >7 days with no landlord response
- Renter's insurance expires within 30 days

🟡 **STANDARD** (renter mode):
- Rent due within 14 days (standard reminder)
- Lease up for renewal within 60 days
- Utility bill received (route for amount tracking)

### Renter State Schema (state/home.md when tenure=renter)
```yaml
tenure: renter
rent:
  monthly_amount: XXXX
  due_day: 1  # day of month
  auto_pay: true|false
  payment_method: "[bank/Venmo/Zelle/check]"
  late_fee_after_day: 5
  last_paid_date: YYYY-MM-DD
  last_paid_amount: XXXX

lease:
  start_date: YYYY-MM-DD
  end_date: YYYY-MM-DD
  renewal_deadline: YYYY-MM-DD  # typically 30-60 days before end
  notice_required_days: 30
  renewal_offered: null|true|false
  renewal_amount: null|XXXX
  renewal_decision: null|accept|decline|negotiating

renter_insurance:
  provider: "[insurance company]"
  policy_number: ""
  premium_monthly: XXXX
  coverage_amount: XXXX
  renewal_date: YYYY-MM-DD
  auto_renew: true|false

landlord:
  name: ""
  email: ""
  phone: ""
  property_manager: ""  # if different from landlord

maintenance_requests:
  - id: MR-001
    description: ""
    submitted_date: YYYY-MM-DD
    status: pending|scheduled|completed
    landlord_response_date: null|YYYY-MM-DD
    resolved_date: null|YYYY-MM-DD
```

### Renter Briefing Format
```
### Home (Renter)
• Rent: $X,XXX due [date] — [paid/pending]
• Lease: expires [date] — [renewing/no decision yet]
• [Maintenance item]: [status] — [days since submitted if pending]
• [Renter insurance]: [renewal info if relevant]
```

---

## Smart Home / IoT (ARTHA-IOT — Home Assistant Integration)

> **Active when:** `scripts/connectors/homeassistant.py` enabled in `connectors.yaml`
> and `config/user_profile.yaml → integrations.homeassistant.device_monitoring: true`.
>
> IoT state is machine-managed in `state/home_iot.md` (never merged into state/home.md).
> The `home_device_monitor` skill emits `DomainSignal` objects directly — no LLM
> inference on raw HA data.

### When to Surface IoT Data in Briefings

IoT data surfaces ONLY when there is an actionable signal.  Suppress entirely if
all devices online and no anomalies.

**Always surface (CRITICAL class):**
- Any Ring doorbell/camera/sensor offline > 2 hours → 🔴 CRITICAL
- Any smart lock or alarm panel offline        → 🔴 CRITICAL
- Security device offline + travel mode active → 🔴 CRITICAL (security_travel_conflict)

**Surface when present (MONITORED class):**
- Smart lights/switches offline > 2h    → 🟠 URGENT
- Brother printer toner/drum < 20%      → 🟡 STANDARD
- HVAC or water heater offline          → 🟠 URGENT
- Swim spa (Gecko) temp variance > 5°F  → 🟡 STANDARD

**Energy anomalies (if energy_monitoring: true):**
- Power usage > 30% above 7-day average → 🟡 STANDARD

### IoT Briefing Format
```
### Smart Home
🔴 CRITICAL: Ring [entity] offline for Xh — check security system
🟠 ALERT: [device] offline for Xh
🟡 NOTICE: Printer toner low (X%) — Brother [model]
🟡 NOTICE: Energy spike detected (+X% above 7-day avg)
```
Omit section entirely if no active alerts.

### IoT State Reference
Read `state/home_iot.md` for current device status.  This file is written
exclusively by `scripts/skills/home_device_monitor.py` — never hand-edit.
Key fields:
```yaml
iot_devices:
  last_sync: ISO-8601        # when connector last ran
  total_entities: N
  online: N
  offline: N
  critical_offline: []       # entity_ids of CRITICAL devices offline
  supply_alerts: []          # supply-low events

iot_energy:
  current_power_w: N
  daily_kwh: N
  weekly_avg_kwh: N
  spike_detected: true|false
  spike_pct: N
```

### Signal-to-Action Routing (IoT domain)
| Signal type              | Friction   | Trust | Explanation                         |
|--------------------------|------------|-------|-------------------------------------|
| `security_device_offline`| high       | 0     | Never auto-act; always propose      |
| `security_travel_conflict`| high      | 0     | Never auto-act; always propose      |
| `device_offline`         | standard   | 1     | Regular device; propose repair step |
| `energy_anomaly`         | low        | 1     | Energy spike; low-friction info     |
| `supply_low`             | low        | 1     | Printer/consumable; order reminder  |
| `spa_maintenance`        | standard   | 1     | Gecko spa variance; check spa       |

### Privacy Notes (IoT)
- `device_tracker` entities: state collapsed to `home | not_home | unknown` only
- No GPS coordinates, zone names, or movement history are stored
- Presence tracking is **off by default** — requires explicit opt-in in
  `config/user_profile.yaml → integrations.homeassistant.presence_tracking: true`
- Camera, media_player, TTS, STT domains are **never fetched**

