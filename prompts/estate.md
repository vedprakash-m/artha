---
schema_version: "1.0"
domain: estate
priority: P1
sensitivity: high
last_updated: 2026-03-07T22:52:33
---
# Estate Domain Prompt

## Purpose
Track estate planning documents, attorney communications, and beneficiary designations.
Low frequency but high stakes.

## Sender Signatures
- Estate attorney, trust attorney
- Subject: will, trust, estate plan, POA, advance directive, beneficiary
- Subject: executor, notarize, trust funding, probate

## Extraction Rules
1. **Document type**: will, trust, POA, AHCD, beneficiary designation
2. **Person(s) affected**
3. **Status**: draft, review, signed, filed, needs update
4. **Attorney notes**: any action items or responses needed
5. **Deadline**: if any

## Alert Thresholds
🟠 **URGENT**:
- Attorney requests response or signature within 14 days
- Beneficiary designation conflict identified
🟡 **STANDARD**:
- Document received / signed / filed confirmation
- Annual estate review reminder

## Briefing Format
Only include if there's an estate event or action item.
