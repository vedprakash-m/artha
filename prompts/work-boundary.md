---
schema_version: "1.0"
domain: work-boundary
priority: P2
sensitivity: standard
last_updated: 2026-03-24T00:00:00
---
# Work Boundary Domain Prompt

## Purpose
Track after-hours work load, meeting density, focus block erosion, and
work-life boundary health. Produce a weekly boundary score and surface trends
before burnout signals become critical. Absorbs the FR-14 boundary guardian
function (§4.4).

## Data Source
Primary: work-calendar state (meeting start/end times relative to work schedule)
Secondary: work-comms state (after-hours email/Teams activity)
Profile: `work.schedule` section in user_profile.yaml (work_start_time, work_end_time, weekend_days)

## Extraction Rules
For boundary analysis, derive:
1. **After-hours meeting count** — meetings starting or ending outside work_start_time/work_end_time
2. **Weekend work count** — any calendar activity on days in weekend_days
3. **Focus block availability** — hours without meetings as % of work hours per day
4. **Back-to-back meeting streak** — consecutive meetings with <15 min gap
5. **After-hours comms signals** — emails sent or Teams DMs initiated outside work hours
6. **Trend direction** — is load increasing, stable, or decreasing week-over-week?

## Boundary Score Algorithm
boundary_score = 1.0 (perfect) minus deductions:
- -0.1 per after-hours meeting (capped at -0.5)
- -0.1 per weekend work event (capped at -0.3)
- -0.05 per day with <30 min focus block (capped at -0.25)
- -0.1 if after-hours comms count > 3 per day (capped at -0.2)
Score range: 0.0 (no boundary) to 1.0 (fully protected)

## Alert Thresholds
🔴 CRITICAL: boundary_score < 0.3 for 3+ consecutive days; weekend work >2h
🟠 URGENT: boundary_score < 0.5 this week; after-hours meetings 3+ days in a row
🟡 STANDARD: boundary_score declining 3 consecutive weeks; meeting load >5h/day average
🔵 LOW: Weekly boundary check complete — score above threshold

## Cross-Domain Triggers
- **wellness** (personal — via bridge only): boundary data feeds work_load_pulse.json bridge artifact
  (§9.3): total_meeting_hours, after_hours_count, boundary_score, focus_availability_score
- **work-calendar**: source for meeting timing analysis
- **work-comms**: source for after-hours communication signals

## State File Update Protocol
State file: `state/work/work-boundary.md` (not encrypted — no PII, scores only)
Update weekly (via work_boundary_check skill, §22):
1. **This Week** — boundary_score, after_hours_count, focus_availability_score
2. **Trend** — rolling 4-week boundary_score history
3. **Flags** — specific boundary violations logged with date and type
4. **Bridge Update** — write work_load_pulse.json bridge artifact with current scores

## Bridge Artifact Update (§9.3)
On every boundary state update, write `state/bridge/work_load_pulse.json`:
```json
{
  "$schema": "artha/bridge/work_load_pulse/v1",
  "generated_at": "[ISO-8601]",
  "date": "[YYYY-MM-DD]",
  "total_meeting_hours": [float],
  "after_hours_count": [int],
  "boundary_score": [0.0-1.0],
  "focus_availability_score": [0.0-1.0]
}
```
Only these fields. No meeting titles. No person names. No project names. (§9.3)

## Briefing Format
```
### Work Boundary
- Boundary score this week: [X.X] ([healthy | at risk | critical])
- After-hours events: [N] this week (+/- vs last week)
- Focus availability: [N]% of work hours
- Trend: [improving | stable | declining] (last 4 weeks)
- 🟠 [specific flag if above threshold]
```

## Prompt Instructions
1. Never suggest the user "just work harder" or normalise boundary violations
2. Always frame alerts as system-observable facts, not performance judgements
3. Propose concrete calendar interventions (e.g. "protect 11:00-12:30 as focus block")
4. The boundary score feeds the personal briefing via bridge artifact — keep it honest
