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
1. **Person**: who is this for? (primary user, spouse, or which child — as defined in §1)
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

---

## Phase 2B Expansions

### EOB Monitor (F6.4)
For each Explanation of Benefits received:
1. Extract: service date, provider, amount billed, insurance adjustment, patient responsibility
2. Flag discrepancies: if patient responsibility is unexpected (>$200 for routine visit or >$500 for specialist)
3. Track deductible progress: update `state/health.md → deductible_progress` section
4. Alert if out-of-pocket max is >70% reached (surface before Q4 heavy use)
5. Store format: `EOB-YYYY-MM-DD-[person]-[provider]: $[billed] → $[patient_owes]`

Schema for state/health.md:
```yaml
insurance:
  plan_name: "[plan name]"
  individual_deductible: {annual: XXXX, used: XXXX, remaining: XXXX}
  family_deductible: {annual: XXXX, used: XXXX, remaining: XXXX}
  out_of_pocket_max: {individual: XXXX, family: XXXX, used: XXXX}
  eob_log:
    - date: YYYY-MM-DD
      person: [person]
      provider: "[provider first + last initial]"
      billed: XXXX
      adjustment: XXXX
      patient_owes: XXXX
      status: paid|pending|disputed
```

### Open Enrollment Decision Support (F6.7)
Trigger: any email containing "open enrollment", "benefits election", or similar during Oct–Dec.
1. Extract enrollment window dates
2. Surface comparison prompt: current plan vs. offered plans
3. Check HSA/FSA contribution changes suggested
4. Alert if enrollment deadline is ≤7 days
5. Cross-reference with finance.md for budget impact

### Employer Benefits Inventory (F6.8)
Maintain in `state/health.md → employer_benefits`:
```yaml
employer_benefits:
  medical:
    plan: "[plan name]"
    employee_premium_monthly: XXXX
    employer_contributes: XXXX
    hsa_eligible: true|false
  dental:
    plan: "[plan name]"
    annual_max: XXXX
    orthodontia_lifetime_max: XXXX  # important for children with orthodontia
  vision:
    plan: "[plan name]"
    exam_frequency: "annual"
    allowance_frames: XXXX
  life_insurance:
    coverage: XXXX
    employee_paid: true|false
  fsa_hsa:
    type: FSA|HSA
    annual_limit: XXXX
    employer_contribution: XXXX
    balance: XXXX
    deadline: YYYY-MM-DD
  supplemental:
    - name: "[benefit name]"
      monthly_cost: XXXX
      notes: "[brief description]"
```
Update when benefits confirmation emails arrive. Alert in October for enrollment review.

---

## Longitudinal Lab Results (I-14)

> **Ref: specs/improve.md I-14** — Structured lab history tracking for trend detection.
> Populated from Apple Health imports, patient portal emails, or user-provided data.

When lab results are available from any source (Apple Health import, patient portal email,
or user-provided data), maintain a chronological table in `state/health.md`:

```markdown
#### Lab History — [Person Name]
| Date       | Test                | Result | Unit   | Reference Range | Flag |
|------------|---------------------|--------|--------|----------------|------|
| YYYY-MM-DD | Total Cholesterol   | 205    | mg/dL  | <200           | 🟡   |
| YYYY-MM-DD | LDL                 | 130    | mg/dL  | <100           | 🟠   |
| YYYY-MM-DD | Fasting Glucose     | 95     | mg/dL  | 70–99          | ✅   |
| YYYY-MM-DD | HbA1c               | 5.4    | %      | <5.7           | ✅   |
| YYYY-MM-DD | Weight              | 82.5   | kg     | (tracked)      | —    |
```

**Flag codes:** ✅ normal | 🟡 borderline | 🟠 out of range | 🔴 critically abnormal | — (informational)

### Trend Detection

If a lab value has ≥ 3 data points, note the trend in the quarterly scorecard:
- **↑ Increasing** — latest value > average of previous 2
- **↓ Decreasing** — latest value < average of previous 2
- **→ Stable** — within 5% of previous 2 average

**Surfacing trends:** Include in quarterly health summary:
`"[Test] ↑ trending up over [N] months — [N data points]"`

**Important boundary:** Report trends factually. Do NOT interpret medical significance,
diagnose conditions, or recommend treatment changes. That is medical advice and is
outside Artha's scope. Always suggest discussing trends with a healthcare provider.

### Apple Health Import Processing

When `apple_health` connector output is available in the JSONL stream:
1. Map record types to human-readable names:
   - `BodyMass` → Weight
   - `BloodPressureSystolic` / `BloodPressureDiastolic` → Blood Pressure
   - `HeartRate` → Heart Rate
   - `BloodGlucose` → Blood Glucose
   - `OxygenSaturation` → SpO2
2. Update the Lab History table for the appropriate person.
3. For blood pressure: record as a pair "SYS/DIA mmHg" on the same date.
4. Archive older entries (keep last 24 months inline; note total record count).

**Privacy note:** `state/health.md.age` is encrypted. Lab result details must never
appear in plain state files, briefings sent to unencrypted channels, or audit logs.
