---
schema_version: "1.0"
domain: calendar
priority: P0
sensitivity: medium
last_updated: 2026-03-07T22:52:33
---
# Calendar Domain Prompt

## Purpose
Surface Google Calendar events during catch-up. The calendar is fetched fresh via
Google Calendar MCP each catch-up — this prompt defines what to extract and surface.

## MCP Fetch Instructions
During catch-up Step 3 (Fetch), always call:
- Google Calendar MCP: `today` + `next 7 days`
- Include: all calendars (work, personal, family)

## What to Surface in Briefing
🔴 **CRITICAL** (surface immediately):
- Medical appointment TODAY with no confirmation
- Time-sensitive events starting within 2 hours

🟠 **URGENT** (Today section):
- All appointments and commitments TODAY
- Events requiring preparation (materials, transportation, RSVP)

🟡 **STANDARD** (Today section + This Week in summary):
- All events in next 7 days
- School events for Parth and Trisha
- Recurring events that parents need to manage (pickup, dropoff)

## Cross-Domain Awareness
- School events → note in kids.md if they require action
- Medical appointments → note in health.md
- Financial appointments, tax prep → note in finance.md
- Immigration appointments (USCIS biometrics) → note in immigration.md 🔴

## Briefing Format
```
━━ 📅 TODAY ━━━━━━
[Time] — [Event] ([calendar name])
[Time] — [Event]
```
List ALL events today, sorted by time. If none: "(no events today)"
