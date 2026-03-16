---
schema_version: "1.0"
domain: insurance
priority: P1
sensitivity: high
last_updated: 2026-03-07T22:52:33
---
# Insurance Domain Prompt

## Purpose
Track insurance policy renewals, claims, EOBs, and coverage changes for all family insurance.

## Sender Signatures
- Health, dental, vision: `*@anthem.com`, `*@premera.com`, `*@regence.com`, `*@delta-dental.com`
- Auto: `*@geico.com`, `*@pemco.com`, `*@progressive.com` (also routes to vehicle.md)
- Home/renters: `*@statefarm.com`, `*@allstate.com`, property insurer
- Life: life insurer emails
- Subject: EOB, explanation of benefits, claim, renewal, policy, premium, open enrollment

## Extraction Rules
1. **Insurance type**: health / dental / vision / auto / home / life
2. **Event**: renewal / claim / EOB / policy change / payment
3. **Amount**: premium, claim amount, patient responsibility
4. **Date**: renewal date, claim date, payment due
5. **Action**: appeal, pay, acknowledge, call

## Alert Thresholds
🟠 **URGENT**:
- Policy renewal within 30 days (requires selection/action)
- Claim denied — appeal window open
- Open enrollment period (health insurance) starting/ending
🟡 **STANDARD**:
- EOB received — routine claim processed
- Policy renewal confirmed (no action needed)
- Payment received confirmation

## Briefing Format
```
### Insurance (if alerts only)
• [Type]: [event] — [action]
```

---

## Phase 2B Expansions

### Life Event Coverage Triggers (F16.5)
When certain life events are detected in any domain, cross-reference insurance coverage:
| Life Event | Insurance Check |
|---|---|
| New driver in family (teen driver added) | Auto insurance: add teen driver, get quotes |
| Home purchase/refinance | Homeowners: update replacement value, confirm coverage |
| New baby or adopted child | Health: add to plan; Life: update beneficiaries |
| Job change | Health: COBRA vs new employer; Life/disability continuity |
| Salary increase >20% | Life insurance: adequacy review (10× salary rule) |
| Travel >30 days or international | Travel insurance: check credit card coverage gap |
| Major purchase >$5,000 | Homeowners: personal property rider, or scheduled item endorsement |

Surface as 🟡 Standard suggestion when life event detected: "Life event [X] may affect your [Y] coverage — review recommended."

### Claims History Log (F16.7)
Track in `state/insurance.md → claims`:
```yaml
claims:
  - id: CLM-001
    type: auto|home|health|life|other
    provider: "[insurer]"
    date_filed: YYYY-MM-DD
    status: open|closed|denied|paid
    amount_claimed: XXXX
    amount_paid: XXXX
    notes: "[brief description]"
```
Update when claim-related emails arrive. Alert if open claim has no update >30 days → follow up.
