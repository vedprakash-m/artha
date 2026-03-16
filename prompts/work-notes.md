---
schema_version: "1.0"
domain: work-notes
priority: P4
sensitivity: standard
last_updated: 2026-03-15T00:00:00
---
# Work Notes Domain Prompt

## Purpose
Track recently edited documents, shared Loop pages, and collaborative
artifacts that are relevant to current work. Provides a rolling 7-day
document activity digest.

## Data Source
Primary: WorkIQ document query (via workiq_bridge connector, mode=documents)
Coverage: SharePoint files, OneDrive files, Loop pages, OneNote sections
  that were accessed or edited in the last 7 days
Note: Documents mode tested — response latency ~60s, content varies by tenant

## Trigger Conditions
This domain is loaded on:
1. Weekly digest runs (Sunday evening or Monday morning)
2. On-demand: user asks "what have I been working on?" or "show recent docs"
3. `/domain work-notes` explicit command
NOT loaded on every catch-up (too noisy, low urgency).

## Extraction Rules
For each document, extract:
1. **Title** — document name / page title
2. **Type** — SharePoint / OneDrive / Loop / OneNote
3. **Last Modified** — ISO date YYYY-MM-DD of last edit
4. **Modified By** — who last touched the document (could be you or collaborator)
5. **Link** — URL (extract if available; omit if not provided by WorkIQ)
6. **Context** — which project or work stream this belongs to (infer from title if possible)

## Rolling 7-Day Log Design
`state/work-notes.md` stores the last 7 days of document activity.
- Deduplicate: if same document appears multiple times, keep only most recent entry
- Evict entries older than 7 days on each refresh
- Trim to max 30 entries if list grows very large

## Weekly Digest Format
```
### Recent Document Activity (last 7 days)
| Title | Type | Modified | By | Context |
|------|------|----------|----|---------|
| [title] | [type] | [date] | [person] | [project] |
```

## Alert Thresholds
🟠 URGENT: Shared document modified by collaborator that awaits your review/sign-off
🟡 STANDARD: Document you own was edited by someone else in last 24h
🔵 LOW: Weekly digest of doc activity — no items requiring action

## State File Update Protocol
Read `state/work-notes.md` first, then:
1. Run WorkIQ documents query for last 7 days
2. Merge with existing state: update modified dates, add new docs
3. Evict entries older than 7 days
4. Write merged state back

## PII Redaction
- OK to store: document title, type, modification date, modifier display name, URL
- NEVER store: file contents, email metadata embedded in Loop pages, home-folder paths
- Apply `integrations.workiq.redact_keywords` list before writing to state
