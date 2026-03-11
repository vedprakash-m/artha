---
schema_version: "1.0"
domain: health
priority: P1
sensitivity: high
last_updated: 2026-03-07T22:52:33
---
# Health Domain Prompt

## Purpose
Track medical appointments, prescriptions, insurance claims, FSA/HSA balances,
and health-related actions for all family members.

## Sender Signatures (route here)
- `*@providence.org`, `*@uwmedicine.org`, `*@virginiamason.org`
- `*@zocdoc.com`, `*@myhealth.com`, `*@patientportal.*`
- Any sender with: appointment, prescription, refill, lab result, test result
- Subject: your appointment, appointment reminder, appointment confirmation
- Subject: prescription ready, refill reminder, medication
- Subject: EOB, explanation of benefits, health insurance, claim processed
- Subject: FSA, HSA, flexible spending, health savings account
- `*@anthem.com`, `*@premera.com`, `*@regence.com`, `*@kaiserpermanente.org`

## Extraction Rules
1. **Person**: who is this for? (Ved, Archana, Parth, Trisha)
2. **Type**: appointment / prescription / lab result / claim / insurance / FSA
3. **Date/time** (for appointments) or refill due date (for prescriptions)
4. **Provider/pharmacy** (first name + last initial only — no full last name in state)
5. **Action**: schedule, refill, contact insurance, review claim?
6. **Amount** (for EOBs, FSA claims): patient responsibility, FSA eligible?

## Alert Thresholds
🟠 **URGENT**:
- Appointment tomorrow — confirm attendance
- Prescription refill overdue (past refill date)
- Lab result received — needs to be reviewed with provider
- EOB showing unexpected patient responsibility >$100
- FSA deadline approaching (December) with unused balance

🟡 **STANDARD**:
- Appointment confirmed for next 7 days
- Prescription ready for pickup
- Insurance claim processed (payment info)
- Lab results received (routine, normal)
- Annual preventive care reminder

## State File Update Protocol
Read `state/health.md` first. Then:
1. **Appointments**: update per-person tables with upcoming appointments
2. **Prescriptions**: update refill due dates and status
3. **FSA/HSA**: update balance when EOBs received
4. Archive past appointments (keep last 3 months)

## PII Handling
- Diagnosis codes, medical record numbers → NEVER store full form; note "health event, details in portal"
- Medication names: OK to store
- Provider full last name: OK in state file (already protected by encryption)
- Insurance member IDs → `[MEMBER-ID-ON-FILE]` in state

## Briefing Format
```
### Health
• [Person]: [appointment/prescription/claim] — [date or action]
• FSA balance: $[X] (expires Dec 31)
```
Omit if nothing actionable.

## Important Context
- Health information is sensitive — state/health.md is encrypted
- FSA has use-it-or-lose-it deadline; alert in Q4
- All four family members have health events; track separately
