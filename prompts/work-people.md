---
schema_version: "1.1"
domain: work-people
priority: P3
sensitivity: elevated
last_updated: 2026-03-24T00:00:00
---
# Work People Domain Prompt

## Purpose
Maintain a collaboration graph of colleagues — not a flat lookup cache.
Track relationship recency, org context, communication patterns, influence
weight, and relationship trajectory. Feeds meeting prep, career evidence,
and organizational intelligence. Supports the Manager + Connect OS (§7.6).

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

## Collaboration Graph Design (v1.1 — upgraded from flat cache)
`state/work/work-people.md` stores a rolling collaboration graph (single source of truth — shared by Work OS and personal catch-up).
- Max 50 entries (`people_cache_entries` from user_profile.yaml work section)
- Cache entry TTL: `people_cache_ttl_days` (default: 7 days)
- On cache hit and entry is fresh: load cached; skip provider query
- **Seniority tier tracked**: IC | manager | director | VP | CVP | Partner
- **Relationship trajectory**: warming (more interaction) | stable | cooling | stale
- **Stakeholder influence map**: for Connect cycle evidence, tracks whose observation
  proves impact on which goals (populated from work-performance)
- `is_manager: true` flag enables 1:1 gap detection (>14 days → alert) per §7.6

Note: `state/work/work-people.md` is the single canonical file — used by both Work OS and personal catch-up.
  The former `state/work-people.md` (state/ root) has been removed.

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
State file: `state/work/work-people.md` (ENCRYPTED: work-people.md.age via vault)
Read state first. Then:
1. If person in cache and entry is fresh (< TTL) → use cached; do NOT re-query
2. If not in cache or stale → run WorkIQ people query + Graph enrichment → add to cache
3. Update collaboration_frequency from calendar co-attendance counts
4. Update last_interaction from most recent calendar or comms event
5. Update recency: RECENT if last_interaction < 30d, STALE if 30-90d, COLD if >90d
6. LRU evict oldest entry when cache exceeds people_cache_entries limit
7. Post-update: refresh stakeholder influence map in work-performance state

## PII Handling
- ENCRYPTED at rest (work-people.md.age) — vault key: work
- Apply redact_keywords to all names and project references before writing
- OK to store: display name, title/org tier, relationship type, collaboration frequency,
  last interaction date, seniority tier, manager flag, influence-on-goals list, 1-line summary
- NEVER store: email address, phone number, employee ID, home location, personal details

## Manager 1:1 Gap Detection
On every work-people refresh:
- Find the stakeholder with `is_manager: true`
- Check work-calendar for last 1:1 instance with this stakeholder
- If gap > 14 days: emit 🟠 alert "Manager 1:1 gap: [N] days since last meeting"
- Surface in /work connect-prep and /work pulse (§7.6)
