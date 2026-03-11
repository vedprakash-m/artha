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
