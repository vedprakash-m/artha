---
schema_version: "1.0"
domain: vehicle
priority: P1
sensitivity: high
last_updated: 2026-03-07T22:52:33
---
# Vehicle Domain Prompt

## Purpose
Track vehicle registrations, insurance, maintenance schedules, recalls, and service history.

## Sender Signatures
- `*@geico.com`, `*@pemco.com`, `*@progressive.com`, `*@statefarm.com`
- `*@dol.[state].gov` (state DMV/DOL registration — see §1 location.state)
- Dealership, auto service center emails
- Subject: registration renewal, vehicle registration, tabs
- Subject: oil change, service reminder, maintenance due
- Subject: recall notice, safety recall, NHTSA
- Subject: insurance renewal, auto insurance

## Extraction Rules
1. **Vehicle**: which vehicle? (VIN last 6 or plate)
2. **Event type**: registration / insurance / maintenance / recall
3. **Date**: renewal due, service date, recall notice date
4. **Cost**: renewal fee, service estimate
5. **Action**: schedule, pay, bring in for recall repair

## Alert Thresholds
🔴 **CRITICAL** (immediate):
- Safety recall requiring immediate repair (severity: do not drive)
🟠 **URGENT**:
- Registration expires within 30 days
- Insurance renewal within 14 days
- Safety recall with non-urgent repair window
🟡 **STANDARD**:
- Service reminder (oil change, tire rotation) due
- Regular recall notice (non-urgent)

## Recall Monitoring (monthly)
```
python scripts/safe_cli.py gemini "Check NHTSA recall database for VIN [last5-only] — any open safety recalls?"
```
Note: Use only last 5 of VIN in query for privacy.

## Briefing Format
```
### Vehicle
• [Vehicle]: [registration/insurance/maintenance] [date] — [action]
• Recall: [if any open recalls]
```
