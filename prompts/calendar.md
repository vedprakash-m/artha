---
schema_version: "1.0"
domain: calendar
priority: P0
sensitivity: medium
last_updated: 2026-03-07T22:52:33
---
# Calendar Domain Prompt

## Purpose
Surface all scheduled appointments and events during catch-up, from ALL sources:
Google Calendar, Outlook Calendar, and — critically — iMessage appointment reminders.

## Data Sources (check ALL three every catch-up)

### Source 1 — Google Calendar MCP
During catch-up Step 3 (Fetch), always call:
- Google Calendar MCP: `today` + `next 7 days`
- Include: all calendars (work, personal, family)

### Source 2 — iMessage Appointments Skill (P0 — MANDATORY)
Always read `tmp/skills_cache.json → imessage_appointments` after Step 4.
This skill scans ~/Library/Messages/chat.db for appointment reminder/confirmation
texts from medical, dental, therapy, and school providers (14-day lookback).

**When imessage_appointments returns data:**
- Merge all extracted appointments with GCal/Outlook events (deduplicate on date+provider)
- Any appointment within 48h that has no matching OI → create OI immediately (Step 7b)
- Surface in TODAY or THIS WEEK sections exactly like a calendar event

**If imessage_appointments skill is absent or failed:**
- Surface a ⚠️ warning: "iMessage appointment scan unavailable — check manually"
- Do NOT silently omit this warning

### Source 3 — Outlook Calendar (Windows/WorkIQ)
Available on Windows only. On Mac: use stale `state/work/work-calendar.md` if <12h old.

## What to Surface in Briefing
🔴 **CRITICAL** (surface immediately):
- Any appointment TODAY (from any source — GCal, Outlook, or iMessage)
- Time-sensitive events starting within 2 hours

🟠 **URGENT** (Today section):
- All appointments and commitments TODAY
- Events requiring preparation (materials, transportation, RSVP)
- iMessage appointments for tomorrow or day-after

🟡 **STANDARD** (Today section + This Week in summary):
- All events in next 7 days
- School events for children (as defined in §1)
- Recurring events that parents need to manage (pickup, dropoff)

## OI Creation Rules (Step 7b)
For each appointment extracted from iMessage:
1. Check open_items.md for an existing OI matching (provider substring + date ± 1 day)
2. If no match: create OI with schema:
   ```yaml
   - id: OI-NNN
     date_added: YYYY-MM-DD
     source_domain: calendar
     description: "[Person] appointment at [Provider] — [Date] at [Time]. Source: iMessage reminder."
     deadline: YYYY-MM-DD
     priority: P1   # P0 if TODAY
     status: open
   ```
3. If appointment is TODAY and no OI exists: priority P0, surface in 🔴 CRITICAL block

## Cross-Domain Awareness
- School events → note in kids.md if they require action
- Medical appointments → note in health.md
- Financial appointments, tax prep → note in finance.md
- Immigration appointments (USCIS biometrics) → note in immigration.md 🔴
- Orthodontics/dental → note in health.md (kids section)

## Briefing Format
```
━━ 📅 TODAY ━━━━━━
[Time] — [Event] ([source: gcal/outlook/imessage])
[Time] — [Event]
```
List ALL events today from ALL sources, sorted by time. If none: "(no events today)"

## Calendar Gap Warning
If Google Calendar connector returns 0 events for next 7 days AND iMessage
appointments skill also returns 0: surface this warning prominently:
```
⚠️ CALENDAR BLIND SPOT: No calendar data available from any source.
   Check your calendar manually before relying on this briefing.
   (GCal stale, Outlook unavailable on Mac, iMessage scan returned 0)
```
This warning was the root cause of a missed appointment (Parth, Personalized
Orthodontics, Apr 21 2026, 6:10 PM). It must never be silently suppressed.
