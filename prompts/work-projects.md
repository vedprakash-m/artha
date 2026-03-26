---
schema_version: "1.0"
domain: work-projects
priority: P3
sensitivity: elevated
last_updated: 2026-03-15T00:00:00
---
# Work Projects Domain Prompt

## Purpose
Track Azure DevOps work items assigned to the user. Surface blockers,
overdue items, sprint progress, and newly assigned work.

## Data Source
Primary: ado_workitems connector (ADO REST API via Azure CLI bearer token)

## Extraction Rules
For each ADO work item, extract:
1. **ID** — ADO work item number (e.g. 123456)
2. **Title** — item title (apply `redact_keywords` from user_profile.yaml)
3. **Type** — Bug, User Story, Task, Feature, Epic
4. **State** — New, Active, Resolved, Closed
5. **Priority** — 0 (Critical), 1 (High), 2 (Medium), 3, 4
6. **Target Date** — target completion date if set (YYYY-MM-DD)
7. **Changed Date** — last update timestamp (YYYY-MM-DD)
8. **Sprint / Iteration** — current iteration path (leaf segment only)

## Alert Thresholds
🔴 CRITICAL: P0/P1 bug assigned to me; work item past target date >3d
🟠 URGENT: Sprint ends in <3 days with active/new items; item blocked >48h; P2 bug assigned
🟡 STANDARD: New items assigned to me; item state changed; sprint started
🔵 LOW: Item completed; PR merged; item state moved to Resolved

## Sprint Status Classification
Count items per state:
- **At Risk**: Active items past target date
- **In Progress**: Active/New items with target date in the future
- **Done this sprint**: Resolved/Closed in current iteration

## State File Update Protocol
Read `state/work/work-projects.md` first. Then update:
1. **Active Sprint** — sprint name and end date
2. **My Work Items** — full table sorted by (priority ASC, target_date ASC)
3. **Overdue** — items with target_date < today and state not Resolved/Closed
4. **Recently Changed (24h)** — items with changed_date = today

## PII Redaction
- Apply `integrations.workiq.redact_keywords` (project codenames) to all titles
- OK to store: item ID, redacted title, state, type, priority, dates, iteration path
- NEVER store: item description, comment body, attachments, or email threads
- Iteration path: use only the leaf segment (e.g. "Sprint 47", not full path)

## Briefing Format
```
### Work Projects
• X active items (Y new, Z bugs) — Sprint: [name] ends [date]
• 🔴 [overdue item ID]: "[title]" — [N]d past due
• 🟠 Sprint ends in [N] days — [N] items still active
• Recently assigned: [comma-separated IDs + titles]
• Completed today: [comma-separated IDs]
```
