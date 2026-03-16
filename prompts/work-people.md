---
schema_version: "1.0"
domain: work-people
priority: P4
sensitivity: standard
last_updated: 2026-03-15T00:00:00
---
# Work People Domain Prompt

## Purpose
Provide meeting prep context: who you're meeting, their role, team, manager
reporting chain, and recent collaboration history. Loaded on-demand only.

## Data Source
Primary: WorkIQ people query (via workiq_bridge connector, mode=people)
Enrichment: Graph API /me/manager, /me/directReports, /groups/{id}/members
  (all confirmed working on corporate tenant — no Graph 403 blocks for org chart)

## Trigger Conditions
This domain is NOT loaded on every catch-up.  It is trigger-loaded when:
1. `work-calendar` detects a meeting with an attendee not in rolling cache
2. User asks: "who is [name]?" or "prep me for my meeting with [name]"
3. User explicitly uses `/domain work-people`
On a typical weekday, this triggers for 1–3 first-meetings per week.

## Extraction Rules
For each person, extract:
1. **Name** — display name (as returned by WorkIQ / Graph)
2. **Title** — job title
3. **Department** — org unit / team
4. **Manager** — who they report to (from Graph /me/manager or WorkIQ context)
5. **Relationship** — manager, peer, skip-level, cross-team, external
6. **Last Interaction** — date and type of last 1:1, email thread, or shared doc
7. **Collaboration Summary** — 1-2 line summary of recent shared context

## Rolling Cache Design
`state/work-people.md` stores a rolling cache of recently looked-up people.
- Max 20 entries (LRU eviction when full)
- Cache entry expires: 7 days (people change roles infrequently)
- On cache hit: load the cached entry; skip WorkIQ query

## Meeting Prep Format
When triggered by work-calendar for a specific meeting:
```
### Meeting Prep: [meeting title]
• [Name] — [Title], [Department] | Reports to: [Manager]
• Relationship: [manager / peer / skip-level / cross-team]
• Last interaction: [date] ([meeting / email thread])
• Context: [1-line collaboration summary]
```

## Alert Thresholds
🔴 CRITICAL: Meeting with new skip-level in <2 hours, no prep context available
🟠 URGENT: First meeting with someone new today — no prior interaction on record
🟡 STANDARD: Org change detected (manager change, team restructure)
🔵 LOW: Collaboration summary available for recurring 1:1

## State File Update Protocol
Read `state/work-people.md` first. Then:
1. If person already in cache and entry is <7 days old → use cached; do NOT re-query
2. If not in cache or stale → run WorkIQ people query → add to cache
3. LRU evict oldest entry when cache exceeds 20 entries

## PII Redaction
- OK to store: name, title, department, relationship type, last interaction date, 1-line summary
- NEVER store: email address, phone number, employee ID, home location
- Apply `integrations.workiq.redact_keywords` to all text before writing to state
