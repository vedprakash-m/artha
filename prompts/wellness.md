---
schema_version: "1.0"
domain: wellness
priority: P2
sensitivity: standard
last_updated: 2026-03-21T00:00:00
requires_vault: false
phase: phase_2
---
# Wellness & Fitness Domain Prompt

> **CONNECT §5.3** — Activated by `connect.domains.wellness_prompt: true` in `config/artha_config.yaml`.

## Purpose
Track fitness activities, nutrition awareness, sleep patterns, and wellness habits.
Bridges wearable data (Apple Health, Fitbit, Garmin, Strava) to Artha's goal
intelligence engine. Surfaces trends and alerts without diagnosing or prescribing.

## Sender Signatures (route here)
- `*@fitbit.com`, `*@garmin.com`, `*@strava.com`, `*@peloton.com`
- `*@classpass.com`, `*@24hourfitness.com`, `*@crunch.com`, `*@orangetheory.com`
- `*@noom.com`, `*@myfitnesspal.com`, `*@weightwatchers.com`, `*@nutrisystem.com`
- `*@whoop.com`, `*@oura.com` (wearable health trackers)
- Subject: workout summary, activity summary, weekly report
- Subject: exercise, fitness, gym, yoga, steps, calories, sleep score
- Subject: membership renewal, class booking, personal training

## Extraction Rules
1. **Activity type**: exercise | sleep | nutrition | weight | subscription
2. **Date**: when did the activity occur?
3. **Duration/Value**: minutes, steps, calories, weight, hours of sleep
4. **Source**: wearable | manual | email | app
5. **Goal linkage**: which active goal does this data support?
6. **Action**: renew / schedule / review / log

## Alert Thresholds
🟠 **URGENT**:
- Exercise goal behind by >2 sessions this week (when goal is active)

🟡 **STANDARD**:
- No activity logged in 3+ days (when fitness goal is active)
- Gym membership renewal due
- Wearable device battery low / sync failed
- Fitness class cancellation or schedule change

🔵 **INFO**:
- New personal record achieved
- Streak milestone reached (7, 30, 100 days)
- Weekly activity summary available
- Monthly progress toward annual goal

## Wearable Data Integration
Data from `apple_health`, `fitbit`, `garmin`, `strava` connectors routes here.
Connector outputs are aggregated daily: steps, active_minutes, sleep_hours, resting_hr.
**Raw data is ephemeral** — only daily summaries stored in state.

Daily aggregation schema (stored in `state/wellness.md`):
```
YYYY-MM-DD: steps=X, active_min=X, sleep_hr=X, resting_hr=X, weight_kg=X
```

Trend detection: if 7+ daily records, compute 7-day rolling averages and flag
significant deviations (>20% below baseline) for coaching engine input.

## Nutrition Awareness
- Track grocery delivery receipts (Instacart, Amazon Fresh, Whole Foods)
- Track meal kit subscriptions (HelloFresh, Blue Apron, Sunbasket)
- Surface dietary-relevant insurance EOBs (nutritionist visits, bariatric care)
- **Do NOT** track individual meals or calorie counts from email — that requires
  wearable/app data and is too granular for email-based extraction

## Goal Integration
All wellness metrics feed the Goal Intelligence Engine (FR-13):
- Exercise frequency → habit goal (e.g., "run 3x/week")
- Weight trend → outcome goal (e.g., "lose 10 lbs by June")
- Sleep consistency → habit goal (e.g., "8 hours nightly")

When a wellness metric falls behind a linked goal, surface in goal domain.

## PII Handling
- Weight, body measurements: OK in `state/wellness.md` (standard sensitivity)
- If user elevates sensitivity to `high`, the file becomes `.age` encrypted
- Heart rate, sleep data: OK in aggregated form (individual readings are ephemeral)
- No clinical interpretation of any metric

## State File Update Protocol
Read `state/wellness.md` first. Then:
1. **Activity log**: append new daily summary (from wearable connector or email)
2. **Subscriptions**: update gym/app membership status and renewal dates
3. **Goals**: cross-reference with `state/goals.md` for goal linkage
4. Archive entries older than 90 days to summary row

## Briefing Format
```
### 🏃 Wellness
• **This week**: [activity summary — sessions, steps, sleep avg]
• **Goal progress**: [habit/outcome goal progress vs. target]
• **Streak**: [active streak, if any]
• **Upcoming**: [class booking, renewal, scheduled workout]
• **Action**: [subscription renewal, missed goal alert]
```
Omit if nothing to surface. Include streak milestones with 🎉.

## State File Schema Reference
```markdown
## Fitness Activity Log
| Date | Steps | Active Min | Sleep Hr | Resting HR | Weight (kg) | Source | Notes |
|------|-------|-----------|---------|-----------|------------|--------|-------|

## Active Subscriptions
| Service | Plan | Status | Renewal Date | Monthly Cost | Auto-renew |
|---------|------|--------|-------------|-------------|-----------|

## Wearables & Apps
| Device/App | Last Sync | Battery | Notes |
|-----------|---------|---------|-------|

## Goal Linkages
| Goal (from goals.md) | Wellness Metric | Current | Target | Status |
|---------------------|----------------|---------|--------|--------|
```
