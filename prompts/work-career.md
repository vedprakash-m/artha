---
schema_version: "1.0"
domain: work-career
priority: P2
sensitivity: elevated
last_updated: 2026-03-24T00:00:00
---
# Work Career Domain Prompt

## Purpose
Continuous evidence capture for review cycles. Maintain an auto-accumulating
record of accomplishments, shipped work, stakeholder observations, and
collaboration breadth. The career domain is the memory layer for Connect;
work-performance (§7.6) is the narrative and submission layer.

## Data Source
Auto-collected from:
- work-projects: completed work items, shipped features, closed PRs
- work-comms: recognition messages, stakeholder feedback, praise threads
- work-calendar: key presentations, design reviews, cross-team meetings attended
- work-people: collaboration breadth (who the user has worked with)
Manual input via /work notes <meeting-id> (post-meeting evidence capture)

## Extraction Rules
For each evidence item, record:
1. **Date** — YYYY-MM-DD
2. **Type** — shipped | recognized | collaborated | presented | decided | unblocked
3. **Summary** — one-sentence description (MUST apply redact_keywords)
4. **Source domain** — which domain produced this evidence
5. **Relevance tags** — which Connect goals this supports (populated by work-performance)
6. **Visibility** — who observed it (stakeholder IDs from work-people)

## Auto-Collection Triggers
- PR merged or work item Resolved/Closed → "shipped" evidence entry
- Email with positive sentiment from a stakeholder → "recognized" evidence entry
- Meeting attended with Director+ → "presented" or "collaborated" entry
- Cross-team project completion → "shipped" + "collaborated" entry
- Post-meeting /work notes capture → all types depending on content

## Evidence Quality Filters
Include only:
- User's direct deliverables and contributions (not team's collective output)
- Observations from stakeholders who are NOT the user themselves
- Cross-team events (same-team events have lower Connect value)
Exclude:
- Routine recurring meetings (weekly syncs, standups)
- Automated build/pipeline events unless user authored the change
- CC'd communications where user had no active role

## State File Update Protocol
State file: `state/work/work-career.md` (ENCRYPTED: work-career.md.age via vault)
Vault key: work (separate from personal domain vault)
Update: on every work refresh that produces new evidence
1. **Evidence Log** — append new items (never overwrite; only add)
2. **Evidence Summary** — rolling count by type and Connect period
3. **Coverage Gaps** — which Connect goals have sparse evidence (fed from work-performance)

## Alert Thresholds
🟡 STANDARD: >7 days since last evidence item captured (signal check)
🔵 LOW: Connect period checkpoint — evidence summary generated

## Cross-Domain Triggers
- **work-projects**: shipped evidence (completed items, PRs)
- **work-comms**: recognized evidence (praise/feedback threads)
- **work-calendar**: presented/collaborated evidence (high-seniority meetings)
- **work-people**: collaboration breadth (network metric for Connect)
- **work-performance**: evidence matching review (which goal does each item support)

## PII Handling
- Encrypted at rest (work-career.md.age)
- Apply redact_keywords to all summaries
- OK to store: evidence type, date, brief summary, stakeholder-tier (not name)
- NEVER store: email body content, meeting transcript, personal details

## Briefing Format (used by /work connect)
```
### Career Evidence — [H1/H2 YYYY]
- Total evidence items: [N] | Last captured: [date]
- By type: shipped [N], recognized [N], collaborated [N], presented [N]
- Evidence gaps: [goals with <3 items in last 30 days]
- Strongest evidence area: [type with highest count]
```

## Prompt Instructions
1. This domain NEVER generates advice or recommendations — it captures evidence only
2. Evidence items must be grounded in actual data from source domains, never fabricated
3. The "recognized" type requires an explicit external signal — do not self-tag as recognized
4. Cross-team collaboration breadth is a key Connect metric — capture names of orgs, not just people
