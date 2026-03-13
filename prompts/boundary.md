---
schema_version: "1.0"
domain: boundary
priority: P1
sensitivity: standard
last_updated: 2026-03-07
---
# Work-Life Boundary Domain Prompt

> **FR-14 · Work-Life Balance Intelligence**
> Ref: PRD FR-14, TS §4, UX §14, OQ-4

## Purpose

Detect work encroachment on personal time. Monitor email timestamp patterns for
after-hours and weekend work signals. Surface boundary data in weekly summaries.
Support the goal of ≥10 hours protected family time per week.

This is a **passive signal domain** — it does not process inbound emails for
content. It analyzes the *metadata* (timestamps, senders) of emails sent/received
outside configured work hours.

**Work hours**: 08:00 – 18:00 America/Los_Angeles (OQ-4). Weekends are always
personal time.

---

## Sender Signatures (route here)

- **This domain does NOT route by sender.** It processes metadata from ALL emails.
- Triggered automatically during weekly summary generation.
- On-demand: `/domain boundary` at any time.

---

## Signal Detection Rules

### After-Hours Signals (Monday–Friday, outside 08:00–18:00 PST)
1. **Emails sent** from work account(s) after 18:00 or before 08:00
   - Note: 18:00–18:30 is buffer — not counted as a violation
   - 21:00+ is "late night" — higher weight
2. **Work calendar events** scheduled outside work hours
   - Exception: "commute" and "personal" tagged events
3. **Replies to work threads** during family dinner window (17:30–20:00)

### Weekend Signals (Saturday–Sunday)
1. Work emails sent on Saturday or Sunday
2. Work calendar events on Saturday or Sunday
3. Multi-message work threads started on weekends

### Family Time Signals (positive detection)
1. Calendar events tagged with family members (spouse, children — as defined in §1)
2. Events on personal calendar outside work hours (not work-related)
3. Estimated from calendar blocks ≥ 1 hour involving family

---

## Extraction Rules

For each weekly boundary analysis:

1. **After-hours email count**: work emails sent Mon–Fri outside 08:00–18:00
2. **Late-night count**: work emails sent after 21:00 any day
3. **Weekend work count**: work emails sent or received Sat/Sun
4. **Weekend calendar events**: work-tagged events on weekends
5. **Family time estimate**: calendar blocks with family members or personal
6. **Work-encroachment score**: computed score 0–10 (see formula below)

**Score formula:**
```
score = (after_hours_count * 1) + 
        (late_night_count * 2) + 
        (weekend_work_count * 1.5) + 
        (weekend_calendar_events * 2)
```
Score interpretation: 0 = healthy | 1–3 = mild | 4–6 = moderate | 7+ = alert

---

## Alert Thresholds

🔴 **CRITICAL**: None in this domain (work-life balance never an emergency)

🟠 **URGENT**:
- Work-encroachment score ≥ 7 in a single week
- ≥ 5 late-night sends (after 21:00) in a single week
- ≥ 3 consecutive weeks with score ≥ 4 (persistent pattern)

🟡 **STANDARD**:
- Work-encroachment score 4–6
- Estimated protected family time < 8 hours in a week (goal: ≥ 10)
- First occurrence of work email sent on a major family holiday

🔵 **INFORMATIONAL**: Weekly check-in during summary — total score, trend vs. prior 4 weeks

---

## State File Update Protocol

Read `state/boundary.md` first. Then:
1. Update `current_week` block with this week's metrics
2. Append a row to `Weekly Trend` table (keep rolling 8 weeks)
3. Add any notable context to `Notes` (crunch period, project launch, etc.)

---

## Briefing Contribution

**In daily briefings:** NO contribution unless score ≥ 7 or persistent pattern alert.

**In weekly summaries:** Always report, even if score is 0.

```
### Work-Life Boundary
Work-encroachment score: [N]/10 ([healthy/mild/moderate/alert])
After-hours activity: [N] emails | Late-night: [N] | Weekend work: [N] events
Family time (est.): [N] hrs | [⬆ improving / → stable / ⬇ declining]
```

If alert: add to 🟠 URGENT section with specific pattern observed.

---

## Weekly Summary Contribution

Format for weekly summary: include a paragraph analyzing the work-life balance
week. Note patterns, deviations from norms, and any goal-relevant observations.
Cross-reference the "Protected family time" goal in `state/goals.md`.

---

## PII Allowlist

```
## PII Allowlist
# No PII patterns to allowlist — this domain processes metadata only
```
