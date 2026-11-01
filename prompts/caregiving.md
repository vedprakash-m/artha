---
schema_version: "1.0"
domain: caregiving
priority: P1
sensitivity: high
last_updated: 2026-03-21T00:00:00
requires_vault: true
phase: phase_2
---
# Caregiving & Elder Care Domain Prompt

> **CONNECT §5.2** — Activated by `connect.domains.caregiving_prompt: true` in `config/artha_config.yaml`.

## Purpose
Track care responsibilities for aging parents or dependents: medications,
appointments, legal coordination, communication cadence, and caregiver wellness.
Designed for anyone managing remote or in-person care for family members.

## Sender Signatures (route here)
- `*@medicare.gov`, `*@cms.gov`, `*@mybenefits.*`
- `*@caringbridge.org`, `*@caring.com`
- Subject: caregiver, elder care, assisted living, nursing home, nursing facility
- Subject: hospice, Medicare, Medicaid, adult day care, home health aide
- Subject: power of attorney, guardianship, conservatorship
- Subject: memory care, dementia, Alzheimer, skilled nursing
- Subjects: respite care, home care, personal care attendant
- Any sender with: "your loved one", "care recipient", "care plan"

## Extraction Rules
1. **Person** (care recipient): which family member is the care recipient?
2. **Type**: medication | appointment | legal | facility | communication | emergency
3. **Date/time**: appointment date, medication schedule, legal deadline
4. **Provider/Facility**: name is OK; credentials not required
5. **Action**: schedule / refill / renew / contact / review / file
6. **Urgency**: routine vs. urgent (missed dose, ER visit, facility incident)
7. **Insurance**: Medicare, Medicaid, long-term care policy, supplement plan

## Alert Thresholds
🔴 **CRITICAL**:
- Medication missed >48 hours
- Emergency contact notification received (fall, hospitalization)
- Facility incident report

🟠 **URGENT**:
- Appointment tomorrow — confirm
- Medication refill due in <7 days
- Power of attorney or guardianship renewal due
- Medicaid redetermination deadline

🟡 **STANDARD**:
- Communication gap >configured threshold (default: 5 days)
- Annual care plan review window
- Long-term care insurance premium due
- Facility billing statement received

🔵 **INFO**:
- Care plan update received
- New facility staff introduction
- Transportation arrangement confirmation

## Cross-Domain Links
- **Finance**: Care costs → monthly expense tracking in finance domain
- **Insurance**: Long-term care policy, Medicare supplement, Medicaid eligibility
- **Estate**: Power of attorney, guardianship, healthcare directive, living will
- **Calendar**: Appointment sync, medication reminders, care coordination calls
- **Health**: Caregiver stress signals → behavioral health section

## Cultural Awareness
- **Time zone scheduling**: Remote care coordination often spans US ↔ India
  (10.5-13.5 hour difference). Surface time zone explicitly for care calls.
- **Cultural care obligations**: South Asian families often have strong duty-to-parents
  expectations and joint family care models. Respect these in framing.
- **Festival coordination**: Major festivals (Diwali, Holi, Eid, Christmas) may
  require advance care coverage planning.
- **Language**: Care recipients may be more comfortable in their native language;
  note language preferences in care recipient profile.

## PII Handling
- Care recipient's medical details → NEVER store full diagnosis; note "health event, details in portal"
- Medication names: OK to store (these are logistics, not clinical interpretation)
- Facility names: OK
- Medicare/Medicaid member numbers → `[MEMBER-ID-ON-FILE]`
- Social Security numbers → NEVER store
- Bank routing numbers (for facility billing auto-pay) → `[ACCOUNT-ON-FILE]`
- Emergency contact full name + phone: OK in encrypted state

## State File Update Protocol
Read `state/caregiving.md.age` first. Then:
1. **Medications**: update per-person medication tables (dose, schedule, refill date)
2. **Appointments**: add upcoming, archive past (keep last 3 months)
3. **Legal documents**: update status (current / expiring / expired)
4. **Communication log**: record last contact date with care recipient
5. **Facility**: update any communication, billing, or staff changes

## Briefing Format
```
### 👴 Caregiving — [Care Recipient Name]
• **Urgent**: [critical/urgent items]
• **Appointments**: [upcoming]
• **Medications**: [refills needed]
• **Last contact**: [X days ago — ⚠ if >threshold]
• **Action**: [any required action items]
```
Omit if nothing actionable. Include per-recipient blocks for multiple care recipients.

## State File Schema Reference
```markdown
## Care Recipients
### [Recipient Name]
**Relationship**: [relationship]
**Location**: [city, state] or [facility name]
**Primary Care Contact**: [first name + last initial], [facility name], [phone last 4]
**Emergency Contact**: [relationship, first name, phone last 4]

#### Medications
| Medication | Dosage | Schedule | Prescriber | Refill Due | Pharmacy | Notes |
|-----------|--------|----------|------------|-----------|----------|-------|

#### Appointments
| Person | Provider | Type | Date | Location | Confirmed | Action |
|--------|----------|------|------|----------|-----------|--------|

#### Legal Documents
| Document | Status | Holder | Renewal/Review Date | Notes |
|---------|--------|--------|---------------------|-------|
| Healthcare Directive | current | [name] | review annually | |
| Power of Attorney | current | [name] | [date] | |

#### Insurance & Benefits
| Plan | Type | ID | Premium | Notes |
|-----|------|----|---------|-------|
| Medicare Part A/B | primary | [MEMBER-ID-ON-FILE] | $0/$XXX | |
| Supplement | secondary | [MEMBER-ID-ON-FILE] | $XXX/mo | |

#### Communication Log
| Date | Method | Notes |
|------|--------|-------|
Last contact: [date] ([N] days ago)
```
