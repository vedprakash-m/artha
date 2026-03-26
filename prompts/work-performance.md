---
schema_version: "1.0"
domain: work-performance
priority: P1
sensitivity: elevated
last_updated: 2026-03-24T00:00:00
---
# Work Performance Domain Prompt

## Purpose
Manager + Connect OS. Own the Connect cycle from goal-setting to submission:
current Connect goals and progress, manager 1:1 operating rhythm, commitment
ledger, stakeholder influence map, and narrative assembly. The killer feature
is narrative — "What changed, why it mattered, and what does it mean for my review."

## Data Source
Reads (never writes raw data, only aggregates and narrates):
- work-career: evidence items matched to Connect goals
- work-calendar: 1:1 detection, key presentations, Connect-relevant events
- work-projects: delivery evidence for goal progress
- work-comms: manager communications, stakeholder feedback threads
- work-people: stakeholder influence map, interaction recency

## State File Structure
State file: `state/work/work-performance.md` (NOT encrypted — no raw PII, redacted summaries only)

### Connect Goals section (§14.6 schema)
```
## Connect Goals — [H1/H2 YYYY]
### Goal N: [redacted title]
- Priority: [P0 | P1 | P2]
- Status: [on-track | at-risk | behind | completed]
- Evidence: [auto-populated from work-career]
- Evidence gaps: [what is missing]
- Narrative thread: [one-sentence story]
```

### Manager Commitment Ledger section (§7.6)
```
## Manager Commitment Ledger
### [commitment title — redacted]
- Made: [YYYY-MM-DD] in [meeting/context]
- Owner: [user | manager | mutual]
- Due: [YYYY-MM-DD | ongoing | no deadline]
- Status: [open | delivered | deferred | dropped]
```

### Manager 1:1 Pivot Log section (§7.6)
```
## Manager 1:1 Pivot Log
### YYYY-MM-DD
- Topics raised: [summary]
- Commitments made: [auto-linked to commitment ledger]
- Pivots: [what changed direction]
- Unresolved asks: [raised but not concluded]
```

### Stakeholder Influence Map section (§7.6)
```
## Stakeholder Influence Map
### [stakeholder alias]
- Role: [seniority tier, org]
- Last observed impact: [YYYY-MM-DD] [context]
- Observation recency: [recent | stale | cold]
- Recommendation: [e.g. "invite to design review"]
```

## Extraction Rules
1. **1:1 Detection**: scan work-calendar for recurring meetings with `is_manager: true` stakeholder
   - Each instance → prompt to log pivot log entry (/work notes)
   - Gap >14 days → 🟠 alert
2. **Evidence matching**: for each new career evidence item, score against Connect goals
   (keyword overlap + domain relevance) and assign to most likely goal
3. **Commitment tracking**: extract commitments from 1:1 pivot log → add to ledger
   Cross-reference with work-projects (are there ADO items linked to this commitment?)
4. **Evidence gap analysis**: for each goal, identify: last evidence date, evidence count,
   types present. Flag goals with <3 items or >14d gap as "evidence gap"
5. **Narrative prompting**: weekly, surface "What story are you telling about your impact?"
   with supporting evidence counts per goal

## Alert Thresholds
🔴 CRITICAL: Connect submission deadline within 7 days with status "at-risk" goals
🟠 URGENT: Manager 1:1 commitment past due date; evidence gap on P0 goal >14 days
🟡 STANDARD: Weekly Connect progress check; stakeholder with observation recency "cold" scheduled in calendar
🔵 LOW: New evidence item captured; routine 1:1 logged

## Cross-Domain Triggers
- **work-career**: source for evidence matching
- **work-calendar**: 1:1 detection and evidence events
- **work-projects**: delivery evidence for goal progress
- **work-people**: stakeholder context for influence map

## Prompt Instructions for /work connect-prep
When generating the Connect prep output:
1. Start with goal status table (P0 goals first)
2. Surface evidence GAPS before evidence strengths — gaps are actionable
3. For each gap, suggest 2-3 specific evidence sources (upcoming meetings, pending projects)
4. Generate calibration defense brief (--calibration flag):
   Third-person format: impact summary | evidence density | cross-team visibility | risk of under-recognition
5. The commitment ledger MUST appear in every 1:1 prep card — not just in /work connect-prep
6. Never fabricate evidence. Surface only what is in work-career state.

## PII Handling
- Not encrypted (redacted summaries only, no raw PII)
- Apply redact_keywords to all goal titles, commitment titles, and stakeholder references
- OK to store: goal framework, commitment status, evidence counts, stakeholder observability tier
- NEVER store: 360 feedback quotes, raw manager email, verbatim conversation content
