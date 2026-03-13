---
schema_version: "1.0"
domain: immigration
priority: P0
sensitivity: critical
last_updated: 2026-03-07T22:52:33
---
# Immigration Domain Prompt

## Purpose
Track all immigration-related matters for the family (defined in §1): visa status, USCIS cases,
deadline monitoring, EAD/AP renewals, and Visa Bulletin priority date tracking.
This is the **highest-stakes domain** — missed deadlines can have multi-year consequences.

## Sender Signatures (route here)
- `*@uscis.gov` (any USCIS notice)
- Subject contains: receipt notice, approval notice, RFE, biometrics, case status
- Subject contains: I-485, I-539, I-765, I-131, I-140, I-90, I-539A, N-400
- Subject contains: H-1B, H-4, EAD, advance parole, priority date, Visa Bulletin, EB-2, EB-3
- Subject contains: green card, LPR, naturalization, citizenship
- From immigration attorney: match against contacts.md

## Extraction Rules
For each immigration email, extract:
1. **Document type** — what form/notice is this?
2. **Receipt number** (IOE/SRC/LIN/EAC/WAC/NBC/MSC/ZLA + 10 digits) — unique key for dedup
3. **Case status** — what changed?
4. **Person affected** — which family member?
5. **Deadline/date** — any date mentioned? (expiry, filing window, appointment)
6. **Action required** — what does this mean for the family?

## Alert Thresholds
🔴 **CRITICAL** — alert immediately:
- Any document expiry within 90 days (EAD, AP, H-1B, H-4, I-94)
- RFE (Request for Evidence) received — response deadline applies
- NOID (Notice of Intent to Deny) — response deadline critical
- Biometrics appointment scheduled
- Interview notice received
- Any USCIS denial notice

🟠 **URGENT** — alert in briefing:
- Document expiry within 180 days
- Priority Date moved within 3 months of family's priority date
- Case status change (especially approval, transfer, reopen)
- Attorney communication referencing a deadline

🟡 **STANDARD** — note in briefing:
- Monthly Visa Bulletin priority date movement (regardless of gap)
- Case receipt / acknowledgment
- Case in production (GC/EAD printing)

## Deduplication
- Unique key: receipt number (IOE/LIN/EAC/WAC/SRC/NBC/MSC/ZLA + 10 digits)
- If same receipt number appears twice, update existing entry's status — do not duplicate
- USCIS sends duplicate emails for system reasons; check receipt + status before creating new entry

## State File Update Protocol
Read `state/immigration.md` first. Then:
1. For new cases: add row to "Active Cases" table
2. For status updates: update existing row in-place
3. For deadline changes: update "Key Dates & Deadlines" table
4. For Visa Bulletin: update "Visa Bulletin Tracking" section
5. Archive completed/approved cases to "Archive" section
6. Never delete — move to archive

## PII Allowlist
The following patterns appear in immigration documents and are NOT PII — do not redact:
- USCIS receipt numbers: `IOE\d{10}`, `SRC\d{10}`, `LIN\d{10}`, `EAC\d{10}`, `WAC\d{10}`, `NBC\d{10}`, `MSC\d{10}`, `ZLA\d{10}`
- Court-assigned alien registration numbers ARE PII — redact as `[PII-FILTERED-ANUM]`
- Form numbers (I-485, I-765, etc.) are NOT PII — keep as-is

## Visa Bulletin Monitoring (monthly)
Each catch-up, call:
```
python scripts/safe_cli.py gemini "What is the current USCIS Visa Bulletin EB-2 India priority date cutoff? Also provide EB-3 India. State the bulletin month."
```
Update `state/immigration.md` Visa Bulletin section with result.
Calculate gap between family priority date and current cutoff. If gap < 12 months, alert 🟠. If gap < 3 months, alert 🔴.

## Briefing Format
```
### Immigration
• [Case/form]: [status change] — [action if any]
• EAD expiry: [date] ([N] days) — [🔴 if <90 days]
• Visa Bulletin EB-2 India: [date] (PD gap: [N] months)
```

## Important Context
- Family is on employment-based immigration path (category and origin country defined in §1)
- Employment-based backlogs for some national-origin categories are measured in years
- Travel outside US may require advance parole if I-485 pending
- EAD and AP must be physically received BEFORE travel or employment change
- Receipt notices confirm USCIS received filing — not approval
- "Transferred" status means case moved to different USCIS office — no action needed unless deadline affected

---

## Leading Indicators

> **Purpose (TS §6.1):** Forward-looking immigration signals that predict timeline risk or action windows *before* deadlines hit. High-stakes domain — err on the side of early alerting.

```yaml
leading_indicators:

  attorney_response_time:
    description: "Days since last response to an email or case question sent to immigration attorney"
    source: comms.md — email threads with Fragomen or immigration attorney
    target: "Response within 5 business days"
    alert_yellow: "No response in 5–10 business days"
    alert_red: "No response in > 10 business days OR deadline approaching without confirmation"
    briefing_trigger: "yellow or red + any pending deadline"

  document_expiry_proximity:
    description: "Days until next immigration document expires (EAD, AP, H-1B, passport, visa stamp)"
    source: immigration.md — document_status[].expiry_date
    target: "Renewal filed ≥ 6 months before expiry"
    alert_yellow: "Expiry within 180 days AND renewal not yet filed"
    alert_red: "Expiry within 90 days AND renewal not filed"
    critical: "Expiry within 30 days — 🔴 CRITICAL regardless of filing status"
    briefing_trigger: "always surface if any document within 180-day window"

  priority_date_gap_trend:
    description: "Months between family priority date and current Visa Bulletin cutoff date; track month-over-month movement"
    source: immigration.md — priority_date, visa_bulletin.eb2_india_current
    target: "Gap shrinking month-over-month"
    alert_yellow: "Gap < 24 months → begin I-485 package preparation"
    alert_red: "Gap < 12 months → attorney consultation urgently needed; I-485 readiness"
    alert_critical: "Gap < 3 months → file I-485 immediately if eligible"
    briefing_trigger: "monthly — always show current gap and movement direction"

  ead_renewal_runway:
    description: "Expected EAD validity remaining after current renewal is approved"
    source: immigration.md — ead_renewal_filed_date, processing_time_estimate
    target: "EAD valid continuously; no gap in work authorization"
    alert_yellow: "EAD gap risk: processing time estimate + filed date may exceed current EAD expiry"
    alert_red: "Projected gap in work authorization > 0 days"
    briefing_trigger: "yellow or red; critical for spouse's employment continuity"

  i485_readiness:
    description: "Whether family is ready to file I-485 when priority date becomes current"
    source: immigration.md — i485_checklist[]
    target: "All I-485 package items prepared before PD becomes current"
    alert_yellow: "PD projected current < 90 days AND checklist items incomplete"
    alert_red: "PD current AND not filed"
    briefing_trigger: "yellow or red; surface in every catch-up when PD gap < 24 months"
```

**Leading indicator summary line (in briefing):**
```
🛂 Immigration Leading: PD gap [N months, ▲▼] | Next expiry [doc] in [N days] | Attorney [responsive/⚠ N days silent] | I-485 readiness [✓/⚠]
```
