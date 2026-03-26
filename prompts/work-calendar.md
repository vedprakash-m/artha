---
schema_version: "1.0"
domain: work-calendar
priority: P2
sensitivity: standard
last_updated: 2026-03-15T00:00:00
---
# Work Calendar Domain Prompt

## Purpose
Surface work meetings, detect scheduling conflicts with personal calendar,
identify meeting prep needs, and flag overloaded work days.

## Data Source
Primary: WorkIQ calendar query (via workiq_bridge connector)
Fallback: outlookctl calendar list (Windows — Classic Outlook, Wave 2)

## Extraction Rules
For each work calendar event, extract:
1. **Date + Time** — start, end, duration (derive duration from start/end)
2. **Title** — meeting subject (apply `redact_keywords` from user_profile.yaml)
3. **Organizer** — who scheduled it
4. **Location** — office address, Teams, or hybrid
5. **Is Teams** — yes/no (detected from `is_teams` field or location string
   matching "Microsoft Teams Meeting", "Teams", "Online Meeting")
6. **Attendee context** — trigger work-people lookup for new/unfamiliar attendees
   (on-demand only — do not load work-people for every meeting)

## Alert Thresholds
🔴 CRITICAL: Meeting starts in <15 minutes with prep material unreviewed; double-booked with unmovable personal appointment
🟠 URGENT: Double-booked work slot; meeting with skip-level or VP+ today with no prep
🟡 STANDARD: 3+ hours of back-to-back meetings without a break; no lunch block
🔵 LOW: Meeting cancelled; new recurring series added to calendar

## Cross-Domain Triggers
- **calendar** (personal): conflict detection — work meeting overlaps personal appointment
  (use field-merge dedup: title ± variation + start ± 5 min → set source="both")
- **work-people**: trigger meeting prep for meetings with unfamiliar attendees
- **goals**: "deep work" goal tracking — count uninterrupted 90-min+ blocks today
- **employment**: block on PTO days fetched from employment domain

## Deduplication Rule
After merging Google Calendar and WorkIQ events:
- Match: (title ± minor variation) AND (start time ± 5 minutes)
- Keep personal Google event as primary; merge in Teams link from work event
- Set `merged: true` on deduped events; exclude from conflict detection

## Pre-Filter (Suppress from State)
- All-day non-working events (Out of Office, Holiday) → note count only
- Private events → note "Private event" without title
- Past events (ended >1h ago) → omit from today's briefing section

## State File Update Protocol
Read `state/work/work-calendar.md` first. Then update:
1. **Today's Meetings** — table with time, title, organizer, location, Teams flag
2. **This Week** — per-day summary row (date, meeting count, hours blocked, conflicts)
3. **Patterns** — rolling stats: avg meetings/day, focus block availability

## PII Redaction
- Apply `integrations.workiq.redact_keywords` from user_profile.yaml to all titles
- Preserve meeting type words (Review, Standup, Interview, Sync) for trigger classification
- NEVER store meeting body/description or attendee email addresses in state file

## Briefing Format
```
### Work Calendar
• X meetings today (Y hours blocked) [conflicts: N]
• 🟠 [conflict or alert description]
• Next: [title] at [time] with [organizer] — [Teams / Room / TBD]
• Focus block: [time range] ([N] hours uninterrupted)
```
