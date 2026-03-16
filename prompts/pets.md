---
schema_version: "1.0"
domain: pets
priority: P2
sensitivity: low
last_updated: 2026-03-14T00:00:00
requires_vault: false
phase: phase_1b
---
# Pets & Animal Care Domain Prompt

## Purpose
Track pet health records, vaccination schedules, medications, vet appointments,
grooming, boarding, and animal-related reminders. This domain is **date-driven**:
most alerts are triggered by calendar-computed deadlines (vaccine due dates,
medication refills, license renewals), not email routing.

## Activation
Enable this domain when `domains.pets.animals` is non-empty in `config/user_profile.yaml`.
If no pets are configured, this domain produces no briefing output.

## Sender Signatures (route here)
- Veterinary clinic appointment confirmations and reminders
- Pet pharmacy: prescription refill reminders, shipping notifications
- Subject: vaccination, rabies, DHPP, bordetella, feline, canine, heartworm
- Subject: flea, tick, parasite prevention (Frontline, NexGard, Bravecto, Revolution)
- Subject: pet insurance, Trupanion, Healthy Paws, Embrace, Nationwide
- Subject: grooming appointment, boarding reservation, daycare
- City/county license renewal notices
- Pet food subscription (Chewy, PetSmart) — only if health-relevant (prescription diet)

## Extraction Rules
For each pet-related email:
1. **Pet name**: which animal does this concern?
2. **Event type**: vaccination | medication | appointment | insurance | grooming | boarding | license
3. **Date**: when is the appointment, when is something due, when was it administered?
4. **Action required**: schedule | pay | refill | renew | confirm?
5. **Urgency**: how many days until due/overdue?

## Alert Thresholds
🔴 **CRITICAL**:
- Vaccination overdue (past due date)
- Medication (flea/tick/heartworm) not administered and overdue >2 weeks
- Pet insurance lapsed without renewal

🟠 **URGENT**:
- Vaccination due within 30 days
- Medication refill needed (current supply runs out within 14 days)
- City/county pet license renewal due within 30 days

🟡 **STANDARD**:
- Annual wellness exam due (>11 months since last exam)
- Dental cleaning overdue (>12 months for dogs, veterinary recommendation)
- Grooming or boarding confirmations
- Pet insurance premium change notification

🔵 **INFORMATIONAL**:
- Appointment reminders (already scheduled)
- Boarding/daycare confirmations

## Date-Driven Reminders (no email required)
Compute the following on every catch-up from state file dates, WITHOUT waiting for an email:
1. **Vaccination due**: today >= `due_date` (each vaccine tracked separately)
2. **Medication due**: today >= `next_dose_date`
3. **Annual exam due**: today >= `last_exam_date` + 365 days
4. **License renewal due**: today >= `license_renewal_date` - 30 days
5. **Dental due**: today >= `last_dental_date` + 365 days (dogs only, check breed guidelines)

## State File Update Protocol
Read `state/pets.md` first. Then:
1. **Vaccination records**: update date administered + compute next due date
2. **Medication log**: update last administered date + next due date
3. **Appointment history**: add new appointments with outcome notes
4. **Insurance**: update premium, coverage, renewal date if changed
5. **License**: update renewal date and status

## Briefing Format
```
### Pets
• [Pet name]: [vaccination/medication due description] — [date]
• [Pet name]: Annual exam due [month year]
• [Pet name]: [License/insurance item] — [action]
```
Omit if all vaccines/medications current and no upcoming appointments.
Show "All pets current — no action needed" if nothing is due.

---

## Phase 2 Expansions

### Multi-Pet Support
The state schema supports multiple animals. Each animal in the `animals` array
has its own vaccination and medication schedule. Alerts are generated per animal.

### Breed-Specific Health Protocols
For known breed health risks (hip dysplasia in large dogs, dental issues in small breeds,
FIV testing for cats), adjust reminder cadences based on `breed` field. (Phase 2 feature.)

### Emergency Contacts
Maintain 24-hour emergency vet contact in state for quick access during briefings.
