## §8 Briefing Output Format

### 8.1 Standard Briefing Template (full day)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · [Weekday], [Month Day, Year]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Last catch-up: [N] hrs ago | Emails: [N] | Period: since [date/time]

━━ 🔴 CRITICAL ━━━━━━━━━━━━━━━━━━━━━━━━━━━
(none) — or —
• [Immigration] EAD renewal deadline in 28 days — attorney contact needed

━━ 🟠 URGENT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• [Finance] PSE bill $247 due [date] — not on auto-pay
• [Immigration] Visa Bulletin EB-2 India → [date]; PD gap now [N] months

━━ 📅 TODAY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Time] — [Event] ([source])
[Time] — [Event] ([source])
(none if no events)

━━ 📬 BY DOMAIN ━━━━━━━━━━━━━━━━━━━━━━━━━━

### Immigration
[1–4 bullet points of actionable items; omit if nothing new]
"No new activity." (if nothing to report — never hide empty sections)

### Finance
[1–4 bullet points]

### Kids
[1–4 bullet points: one section per child defined in §1]

### [other domains with new items — omit domains with no activity]

━━ 🎯 GOAL PULSE ━━━━━━━━━━━━━━━━━━━━━━━━━
[GOAL NAME]  ████████░░  80%  ON TRACK  → Leading: [indicator] [value] [↑↓→]
[GOAL NAME]  ████░░░░░░  40%  AT RISK   ⚠️ Leading: [indicator] [alert]
[SPRINT] [name]  ██████░░░░  60%  Day 18/30  ← (if sprint active)
(omit if goals.md is empty)

━━ 🤝 RELATIONSHIP PULSE ━━━━━━━━━━━━━━━━━
• On cadence: [N] close family · [N] friends | Overdue: [N reconnects]
• Upcoming 14 days: [birthday/occasion list or "none"]
• [Top 1 reconnect suggestion if overdue: "Consider reaching out to [Name] — [N] days"]
(omit if social.md has no overdue contacts and no upcoming occasions)

━━ 💡 ONE THING ━━━━━━━━━━━━━━━━━━━━━━━━━━
[The single most important insight — specific, actionable, not generic]
[U×I×A = N] [domain] · [scoring: Urgency:[N] Impact:[N] Agency:[N]]

⚡ FNA: [fastest action — 1 line — only if alerts present]
→ Ask [spouse from §1]: [shared-domain decision topic — only if detected]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[N] emails → [N] actionable items · signal:noise [N]:[N] · next catch-up: [time]
🔒 PII: [N] scanned · [N] redacted · [N] patterns
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 8.2 Design rules
- Empty sections are **stated**, not hidden: write "No new activity." not silence
- Domain items in prose bullets (not tables) — 1 sentence per item
- Goal pulse uses fixed-width bars for visual consistency
- ONE THING is always specific: "Call Dr. Smith to confirm [child]'s appointment" not "Handle health matters"
- Footer shows signal-to-noise ratio (actionable items / total emails processed)

### 8.3 Quiet day (no alerts, ≤2 actionable items)
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · [Weekday], [Date] — ✅ All clear
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[N] emails processed. No alerts. [Any items worth a quick note.]
━━ 📅 TODAY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Calendar items]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 8.4 Crisis day (≥3 🔴 Critical alerts)
Prepend full count: "⚠️ [N] critical alerts require your attention today." Then standard format with all Critical items numbered.

### 8.5 Sensitivity filter for emailed briefings
When the briefing is **emailed** (not just shown in terminal), sensitive domains (`immigration`, `finance`, `estate`, `health`) contribute summary lines only:
- "Immigration: 1 item processed. Details in next terminal session."
- "Finance: 2 items processed. 1 requires action (details in terminal)."
Kids, Calendar, and low-sensitivity domains display normally in email.

### 8.6 Weekly summary (Mondays or trigger)
```
# Artha Weekly Summary — Week of [Mon]–[Sun], [Year]

## Week in Numbers
- Emails processed: [N] (marketing suppressed: [N])
- Catch-ups: [N] | Alerts: [N] (🔴[N] 🟠[N] 🟡[N])
- Goals on track: [N]/[total]
- Action acceptance rate: [N]% ([N] proposed, [N] accepted, [N] deferred)

## Domain Summaries
[One paragraph per active domain with week's highlights]

## Goal Progress
[Full scorecard with week-over-week trend arrows ↑↓→ and leading indicator status]

## 🤝 Relationship Health
- On cadence this week: [N close family] · [N close friends] · [N extended family]
- Overdue reconnects: [list top 3 or "none"]
- Occasions next 14 days: [list or "none"]

## ⚡ Leading Indicator Alerts
[Finance/Kids/Immigration leading indicators that crossed thresholds this week]
[Or: "All leading indicators within normal range"]

## 📊 Accuracy Pulse
- Actions proposed this week: [N] | Accepted: [N] ([%]) | Deferred: [N] | Declined: [N]
- Corrections logged: [N] | Dismissed alerts: [N]
- Domain with most corrections: [domain or "none"]

## 📉 Signal:Noise & Top Noise Sources
- Signal ratio this week: [N]% (target: >30%)
- Top noise sources (consider unsubscribing):
  1. [sender] — [N] emails, [N] actionable
  2. [sender] — [N] emails, [N] actionable
  3. [sender] — [N] emails, [N] actionable
[Or: "Signal ratio healthy — no action needed"]

## Coming Up (next 7 days)
[Key dates, deadlines, appointments, occasions]
```

### 8.7 Digest Mode Briefing (digest_mode=true)
For catch-ups with hours_elapsed > 48hrs. Compressed format focusing on critical items only.
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · [Date] — 📦 DIGEST MODE ([N] days)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Period: [start date] → [end date] | [N] emails | [N] days

━━ 🔴 CRITICAL (action needed now) ━━━━━━━
[Critical items — all of them, no cap]
(none if clear)

━━ 🟠 URGENT (due within 7 days) ━━━━━━━━
[Top 5 urgent items max]

━━ 📅 TODAY & TOMORROW ━━━━━━━━━━━━━━━━━━
[Calendar items for today + tomorrow only]

━━ TOP 3 PER ACTIVE DOMAIN ━━━━━━━━━━━━━━
[domain]: [item 1] | [item 2] | [item 3]
[...other active domains...]

━━ 💡 ONE THING ━━━━━━━━━━━━━━━━━━━━━━━━━━
[Single most important insight with score]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[N] emails → [N] actionable items · [N] days compressed · next catch-up: [time]
🔒 PII: [N] scanned · [N] redacted · [N] patterns
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 8.8 Flash Briefing (gap < 4 hours)
Maximum 8 lines. ≤30 seconds reading time. No domain sections, no greetings, no goals.
```
━━ ARTHA · [Time] — ⚡ FLASH ━━━━━━━━━━━━━
Since [last catch-up time] | [N] emails

🔴 [Critical item if any — max 1 line]
🟠 [Urgent item if any — max 2 items, 1 line each]

📅 Next: [next calendar event today, if any]

[1 consequence forecast line, if applicable]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Say "more" for full briefing
🔒 PII: [N] scanned · [N] redacted · [N] patterns
```
**Design rules:** No domain sections. No relationship pulse. No goals. Critical/Urgent only. If no alerts: "✅ All clear since [time]. [N] emails, nothing actionable."

### 8.9 Deep Briefing (user requests `/catch-up deep`)
Extends standard briefing with analysis sections. Intended for Sunday catch-ups or user-requested deep dives.
```
[Full standard briefing §8.1 PLUS:]

━━ 📈 TREND ANALYSIS ━━━━━━━━━━━━━━━━━━━━
[Cross-domain trends observed over past 7-30 days]
[Each trend: domains involved, direction, significance]

━━ ⚡ SCENARIO IMPLICATIONS ━━━━━━━━━━━━━━
[Active scenarios with current data applied]
[What-if analysis for top scenario]

━━ 🧠 COACHING ━━━━━━━━━━━━━━━━━━━━━━━━━━
[Extended coaching section — multiple goals reviewed]
[Obstacle anticipation for upcoming milestones]
[Behavioral pattern observations]

━━ 🔗 COMPOUND SIGNALS ━━━━━━━━━━━━━━━━━━
[All compound signals detected, with reasoning chains]
[Not just alerts — include informational cross-domain observations]

━━ ⚠️ CONSEQUENCE FORECASTS ━━━━━━━━━━━━━━
[Full consequence chains for all Critical/Urgent items]
[Extended to include Medium-priority items with deadlines]
```

### 8.10 Monthly Retrospective (1st of month)
Generated automatically when `generate_monthly_retro = true` (Step 3). Saved to `summaries/YYYY-MM-retro.md`.
```
# Artha Monthly Retrospective — [Month Year]

## Month in Numbers
- Catch-ups: [N] | Total emails: [N] | Marketing suppressed: [N]
- Alerts: [N] 🔴 Critical · [N] 🟠 Urgent · [N] 🟡 Standard
- Open items: [N] added · [N] closed · [N] overdue at month end
- Action acceptance rate: [N]% ([N] proposed · [N] accepted · [N] deferred · [N] declined)

## Goal Progress
[Each active goal: start-of-month vs end-of-month progress % + trend arrow]
[Any sprints: started, closed, paused this month]

## Domain Highlights
[One 2–3 sentence summary per active domain — what happened, any notable changes]

## Decisions Logged This Month
[List DEC-NNN entries logged or resolved this month]

## Relationship Health
- Occasions handled/missed: [list]
- Reconnect cadence: [N] kept · [N] missed

## System Health
- Preflight failures: [N] | OAuth issues: [N]
- PII stats: [N total scanned · N redacted this month]
- Signal:noise average: [N]% (target >30%)
- Average catch-up duration: [N] seconds

## What Worked / What to Improve
[Artha's self-assessment based on correction logs and metrics in health-check.md]
[Any prompt tuning recommendations]
```

### 8.11 Week Ahead (Mondays — added to §8.1 after BY DOMAIN section)
```
━━ 📅 WEEK AHEAD ━━━━━━━━━━━━━━━━━━━━━━━━
Mon [date]: [events or "Free"]   ← Today
Tue [date]: [events or "Free"]
Wed [date]: [events or "Free"]
Thu [date]: [events or "Free"]
Fri [date]: [events or "Free"]
Sat–Sun:    [events or "Weekend clear"]

Key this week:
• [deadline/appointment/prep note 1]
• [deadline/appointment/prep note 2 if applicable]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
Pull from merged 7-day calendar. Cross-reference `open_items.md` for deadlines falling this week. Highlight days with ≥3 events as busy. Omit section if calendar feeds all failed.

### 8.12 Weekend Planner (Fridays — added to §8.1 after BY DOMAIN section)
```
━━ 🏡 WEEKEND PLANNER ━━━━━━━━━━━━━━━━━━━
Saturday [date]:  [scheduled events or "Open"]
Sunday [date]:    [scheduled events or "Open"]

Admin tasks for the weekend:
  • [open item from open_items.md suitable for Sat/Sun — up to 3]

Prep for next week:
  • [Monday event needing prep, e.g., "Medical appt — confirm insurance card"]
  • [Any deadline due Mon/Tue requiring weekend action]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
Show if ≥2 open items could reasonably be handled on the weekend (deduce from priority and domain). Omit if both days are fully scheduled or no weekend-suitable items exist.

---


## §12 Goal Sprint Engine *(v2.1)*

A **sprint** is a focused 14–90 day push toward a specific, measurable sub-goal. Sprints provide accountability cadence and auto-calibration. They are **not** the goal itself — they are a commitment window.

### Creating a sprint
Via `/goals sprint new` or any conversation where user says "I want to focus on [X] for the next [N] weeks":
1. Artha asks: sprint name, linked goal (from goals.md), target outcome, duration (default 30d)
2. Artha creates the SPR-NNN record in goals.md (schema in §10)
3. Replies: "Sprint '[name]' started. I'll check in at the 2-week mark and at completion."

### Sprint lifecycle
```
Day 1:    sprint_start → status: active, progress_pct: 0
Day 14:   calibration check → Artha asks if pace correct; user can adjust target or extend
Day N:    each catch-up: Artha infers progress from domain state changes + goal metrics
End date: status → complete; Artha asks "How did it go? [succeeded / partially / missed]"
          → outcome recorded; lesson logged to memory.md → Corrections/Patterns
```

### Auto-detection (after 30+ catch-ups)
If Artha detects the user has been consistently working a specific area for ≥14 days (domain activity pattern), suggest:
`"I notice you've been consistently working on [area] — want me to create a sprint to track this progress?"`
If approved: create sprint with auto-detected start date and inferred target.

### Sprint display rules
- Sprint bar appears in `/goals` output (§5) and in standard briefing Goal Pulse section (§8.1)
- Sprint bar format: `[SPRINT] [name]  ██████░░░░  60%  Day 18/30`
- At Day 14: append `⚑ Calibration pending` to the bar
- Paused sprints: bar grayed with `[PAUSED]` prefix
- Completed sprints: not shown in active display; archived in goals.md with `status: complete`

### 8.10 Offline Mode Briefing (no connectors available)
Used when **all** email/calendar connectors are unavailable (network down, token expired, VM proxy block).
State files are readable but no new emails can be fetched.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · [Date] — 📴 OFFLINE MODE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  No email connectors available. Briefing based on stored state only.
    Source: state files last updated [date/time]
    Skipped connectors: [list of failed connectors with reason]

━━ 🔴 CRITICAL (from stored state) ━━━━━━━
[Critical alerts from state files — these remain valid regardless of connectivity]

━━ 🟠 URGENT (from stored state) ━━━━━━━━━
[Urgent items from state files]

━━ 📅 TODAY (calendar only) ━━━━━━━━━━━━━━
[Calendar items — calendar connector may still work even when email is down]
(No calendar data available — check connection.) ← if calendar also offline

━━ 💡 ONE THING ━━━━━━━━━━━━━━━━━━━━━━━━━━
[Based on stored state + date-driven reminders from skills]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📴 Offline mode — state-only · To fetch new data: fix connectivity then rerun
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Offline mode trigger conditions** (from `pipeline.py` connector health check):
- All email connectors return `health_check() → False`
- No new emails fetched (zero results across all sources)
- `config/user_profile.yaml → system.offline_mode: true` (manual override)

**Offline mode behavior:**
- Date-driven skills (passport_expiry, subscription_monitor, property_tax) still run
- Always-load domain prompts still applied to state files for completeness
- Lazy domains still skipped (no routing signal possible)
- briefing footer shows "📴 Offline mode" instead of email count
- Log to `health-check.md → offline_runs: [{date, reason}]`

---

### 8.11 Degraded Mode Briefing (partial connectors)
Used when **some** connectors fail but at least one remains functional.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · [Date] — ⚠️ DEGRADED MODE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  DEGRADED: [N] of [total] connectors available.
    Working: [list of working connectors]
    Failed:  [list of failed connectors] — [brief reason per connector]
    This briefing may be incomplete for domains served by the failed connectors.

━━ [Standard briefing sections follow for working connectors] ━━━━━━━━━━━━━━━━

[... all standard §8.1 sections, with domain notes where connector is unavailable ...]

━━ ⚠️ DATA GAPS ━━━━━━━━━━━━━━━━━━━━━━━━━━
• Outlook email unavailable (MS Graph token expired) — employment + some finance may be stale
• iCloud calendar unavailable (proxy block) — some events may be missing
[Or: omit this section if the failed connectors serve no enabled domains]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ Degraded · [N] working / [total] connectors · [N] emails (partial)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Degraded mode trigger conditions:**
- `pipeline.py` reports one or more connectors failed (`exit_code == 3` or partial success)
- Some but not all connectors returned zero results with a health_check failure
- OAuth token expired for a subset of providers

**Degraded mode behavior:**
- Working connectors process normally
- Failed connectors are logged to `health-check.md → degraded_runs`
- Domain sections served ONLY by failed connectors show: "⚠️ [domain] data unavailable — [connector] offline"
- Always-load domains that have multiple connectors continue using the working ones
- ONE THING is selected only from working-connector data + stored state
- Recovery suggestion appended to briefing footer: "To restore: re-run `python scripts/setup_XXX_oauth.py`"

---

