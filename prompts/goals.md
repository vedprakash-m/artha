---
schema_version: "1.0"
domain: goals
priority: P1
sensitivity: medium
last_updated: 2026-03-07T22:52:33
---
# Goals Domain Prompt

## Purpose
Track progress on the family's explicitly stated goals. Update goal progress when
relevant emails are processed. Generate goal pulse for briefings.

## How Goals Work
Goals are defined and maintained in `state/goals.md` by the user.
This prompt tells Artha how to update them automatically from email signals.

## Goal Progress Signals
During catch-up, scan all processed emails for goal-relevant signals:
- Finance goal (e.g., "save $X/month"): bill payment confirmations, payroll deposits → update savings estimate
- Health goal (e.g., "complete annual physicals"): appointment confirmation → mark action done
- Immigration goal (e.g., "get Green Card"): case status update → note milestone
- Kids goal (e.g., "[child] test score target"): test result emails → update progress
- Home goal (e.g., "pay off mortgage early"): mortgage statements → update principal

## Alert Thresholds
🟡 **STANDARD**:
- Goal milestone reached (e.g., savings target hit)
- Goal at risk (e.g., unexpected expense threatens savings goal)
- Goal deadline approaching (within 30 days)

## Briefing Format (Goal Pulse)
```
━━ 🎯 GOAL PULSE ━━━━━━━━━━
[GOAL NAME]    ████████░░  80%  ON TRACK  ↑
[GOAL NAME]    ████░░░░░░  40%  AT RISK   →
```
Frequency: include in every briefing. If no goals defined, show "(no goals set — use 'show me how to set goals')".

## Bar Scale
- `░░░░░░░░░░` = 0%
- `█████░░░░░` = 50%
- `███████████` = 100%
- Use 10-character bars with filled blocks proportional to progress
