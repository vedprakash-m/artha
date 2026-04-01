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

**Goal Heartbeat** (≤2 lines, non-weekly briefings only — omit when all goals healthy):
⚡ [G-NNN: issue description] · [G-NNN: issue description]
Show ONLY if any active goal has: `next_action_date` in the past, OR `last_progress` > 14 days, OR metric pace deviation > 20%. Silent when all goals are on track (UX-1). This prevents the 5-day blindspot between weekly reviews. On weekly summary days, fold into § Goal Review instead.

━━ 🤝 RELATIONSHIP PULSE ━━━━━━━━━━━━━━━━━
• On cadence: [N] close family · [N] friends | Overdue: [N reconnects]
• [Top 1 reconnect suggestion if overdue: "Consider reaching out to [Name] — [N] days"]
(omit if social.md has no overdue contacts and no upcoming occasions)

━━ 🎂 OCCASIONS & WISHES ━━━━━━━━━━━━━━━━━
(omit entire section if no occasions within 14 days)

**🔴 Within 3 days:**
• [Person] birthday — [Date], turning [age]. Circle: [circle]. Last WA: [date or "never"].
  → "[WhatsApp greeting suggestion]" [Send ↗]

**🟠 Within 7 days:**
• [Person or Festival] — [Date]. [action if applicable]

**🟡 Within 14 days:**
• [N] occasions coming up — check occasions.md

(Source: occasion_tracker + relationship_pulse skills)

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

### § Goal Review (fold into weekly summary when `generate_weekly_summary == true`)
```
━━ 🎯 GOAL REVIEW — Week of [Mon]–[Sun] ━━━━━━━━━━━━━━
[G-NNN] [GOAL NAME]         [STATUS LABEL]   [brief note]
[G-NNN] [GOAL NAME]         [STATUS LABEL]   [brief note]
[COACHING NUDGE from Step 8s — fold here on weekly days]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
Status label definitions:
- **ON TRACK** — metric pace ≥ expected, or milestone `next_action` not overdue
- **NEEDS_ACTION** — `next_action_date` is in the past
- **STALE** — `last_progress` > 14 days
- **OFF PACE** — outcome goal: actual progress pace < required pace to hit `target_date`
- **STREAK BROKEN** — habit goal: `last_completed` > cadence window (Phase 2)
- **PARKED** — `status: parked` (dim display, show parked_reason)

Flash mode: omit Goal Review entirely (§8.8 flash briefings have no goals section).

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

### 8.14 ThriveSync (Mondays — added after §8.11 Week Ahead)

Triggered when `thrivesync_due = true` (Monday AND `work.thrivesync.enabled` in user_profile.yaml).

**What ThriveSync IS:** A concise list of outcomes and deliverables you will drive this week.
**What ThriveSync is NOT:** A meeting list, a status report, or an internal context dump.

**Core Principles (learned from iterative refinement):**

1. **Outcome-oriented, not meeting-oriented.** Each item answers "what will I deliver/unblock/drive?"
   — not "what meetings will I attend." Meetings are inputs that inform priority ranking,
   but they do NOT appear as line items. Exception: LT reviews where you present are
   deliverables (you prepare and present work product).
2. **Audience-safe.** ThriveSync is read by the broader team. Do NOT include:
   - Internal prep context (CVP count, attendee seniority, skip-level prep notes)
   - IcM numbers (say "NMAgent Sev2.5 incident" not "IcM 767614860")
   - Names of senior attendees to signal importance
   - Prep tasks for meetings (those belong in `work prep`, not ThriveSync)
3. **Milestone-first framing.** Lead with the objective/milestone, follow with tactical unblocks.
   Good: "Drive 1P Pilot readiness for late April; unblock RDMA regression"
   Bad: "RDMA perf regression unblock; pilot re-confirmation"
4. **P0 consolidation.** A P0 program (e.g. XPF) gets ONE top-level numbered item
   with sub-items for all its workstreams — never split across multiple numbered
   entries. Sub-items carry the detail: standups, MAP gaps, rollout tracking, etc.
   Lower-priority programs (P1/P2) may share a top-level entry (e.g. Armada / Rubik)
   when co-scoped.
5. **Scope attribution by impact.** Items belong under the program they IMPACT,
   not the vertical that technically owns the component. Example: BIOS key expiry
   affects XPF ramp → sub-item under XPF, not xSSE.
6. **Consolidate related items.** Newsletter + skip-level update = same artifact, one line.
   Related programs (Armada + Rubik) can share a line when both have the same
   meeting/deliverable.
7. **Side projects: include only with real deliverables.** Admin deadlines (awards
   nominations, HR tasks, training due dates) are excluded. But side projects with
   tangible output (app enhancements, security hardening, demo prep) DO get a line.
   The test: "Is there a deliverable my team can see?" If yes, include.
8. **Selective name attribution.** Program co-owners get `(w/ Name)` on the top-level
   item. Ad-hoc collaborators on individual sub-items do NOT get named.
9. **No stale metrics.** If a number comes from state files, verify the data age.
   If older than 3 days, either refresh or drop the specific number.
10. **Workstream-first, not metric-first.** Name the deployment stage and scope,
    not the metric. Good: "STG rollout with fixes for Autotuning, LSO, kRDMA".
    Bad: "OS compliance ~71%, LSO+kRDMA rollout decision".
11. **Syncs are meetings, not deliverables.** Named syncs ("Repair Support Sync",
    "OneDeploy/DM Sync") are meetings — do NOT list as sub-items. Only include
    what comes OUT of the sync (the workstream or decision).
12. **LT review gets the date.** Always include the day: "LT review (3/31)".
13. **Verticals state their cross-program scope.** Verticals like xDeployment serve
    multiple programs — state which: "cross-program deployment execution for
    XPF/DD-PF/Armada".
14. **Concise.** Target 5–7 items. Every word must earn its place.

**Generation Algorithm:**

**Phase 0 — Freshness Gate** (MANDATORY — blocks generation on failure):
Run `work refresh` to ensure state files have THIS week's data.
Check `state/work/work-calendar.md` → `last_updated` AND date coverage.
Check all state files used → `last_updated`. Flag any >3 days old.

| Calendar State | Action |
|---|---|
| Covers this week with work meetings | Proceed to Phase 1 |
| Refresh succeeded but no work meetings (Agency/WorkIQ failed) | **ASK USER** for this week's key meetings |
| Refresh failed (token expired, network error) | Fix root cause, re-run. If still no calendar, **ASK USER** |
| Last week's data still in file | **BLOCK. NEVER generate with last week's calendar.** |

If ANY state file is >3 days old, append to output:
```
⚠️ DATA FRESHNESS WARNING:
  [file]: last refreshed [date] ([N] days ago) — metrics may be outdated
  Verify current numbers before posting.
```
**NEVER silently use stale data.** Failures must be loud.

**Phase 1 — Scope Sweep** (identify candidates from each ownership area):
1. Read `state/work/work-scope.md` — for each active area, extract:
   - Current next action (the deliverable, not the meeting)
   - Priority tier (P0/P1/P2)
   - Co-owner (for parenthetical attribution — program co-owners only)
2. Read `state/work/work-open-items.md` — extract blocked/escalated items
3. Read `state/work/work-goals.md` — active goals with this-week next_action_date
4. Read `state/work/work-projects.md` — hot issues and this-week deliverables
5. For P0 programs: collect ALL workstreams into sub-item candidates (standups,
   MAP gaps, deployment, rollout decisions, syncs, operational cadences)

**Phase 2 — Calendar Urgency Overlay** (meetings inform ranking, not line items):
5. Read `state/work/work-calendar.md` OR user-provided meeting list
6. LT reviews where you present → elevate that program to #1 AND include date: "LT review (3/31)"
7. Meetings with VP+/skip-level/CVP attendees → elevate that program's ranking
8. Daily standups you drive → mention in the program line ("daily standups"), not as separate item
9. Named syncs (Repair Support Sync, OneDeploy/DM Sync) → DO NOT list. Extract the workstream outcome instead.
10. Skip-level monthlies, 1:1s, sync meetings → DO NOT list as separate items

**Phase 3 — Comms Signal Boost** (when data is fresh):
11. Read `state/work/work-comms.md` — only if <3 days old
12. Active Sev-2+ incidents get mentioned by description (no IcM numbers)
13. Escalation threads inform sub-items under the relevant program

**Phase 4 — Rank & Synthesize**:
14. Apply priority hierarchy:
    1. **P0 program as single entry with sub-items** — XPF gets #1 when LT review/newsletter
       is scheduled. Cover: standups, MAP gaps, rollout scope (name the stage, not the
       metric), cross-cutting items by impact.
    2. **P1 partner programs** — DD-PF (#2 typically)
    3. **Critical blockers** (describe impact, not ticket numbers)
    4. **Verticals supporting P0** — xDeployment, xSSE rank ABOVE lower-tier programs
       because they directly enable P0 execution. State cross-program scope.
    5. **Lower-tier programs** — Armada / Rubik (club when co-scoped)
    6. **Side projects with real deliverables** — Shiproom AI (app work, security hardening,
       demo prep). Include only when there's tangible output, not admin deadlines.
    7. **OMIT:** Admin deadlines, awards nominations, HR tasks, training due dates
15. For each item: milestone/objective first, then tactical actions
16. Name program co-owners: "(w/ Yasser)", "(w/ Isaiah)", "(w/ Nikita)" —
    do NOT name ad-hoc collaborators on sub-items
17. Club related programs when they share a deliverable this week

**Output format:**
```
━━ 🎯 THRIVESYNC — Week of [Mon date] ━━━━

Top [N]:
    1. [P0 Program] — [LT review (date), newsletter]; Critical topics:
    - [Workstream: standups with scope]
    - [Workstream: gap area]
    - [Workstream: rollout stage with fixes/scope]
    - [Cross-cutting item attributed by impact]
    2. [P1 Program] (w/ [co-owner]) — [milestone first]; [unblock second]
    3. [Vertical] (w/ [co-owner]) — [delivery scope]; cross-program execution for [X/Y/Z]
    4. [Vertical] (w/ [co-owner]) — [scope execution]
    5. [Program / Program] — [milestone]; [shared deliverable]; [roadmap]
    6. [Side project with real deliverable] — [app work]; [demo/event prep]
    ...

Docs:
    [Document being written/published this week, if any]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Data sources: [list files used with freshness]
  work-scope.md (today) | work-projects.md ([date]) | work-open-items.md ([date])
  work-calendar: [source — live refresh / user-provided / Agency]
  ⚠️ [any staleness warnings]
```

**Validation rules:**
- Target 5–7 top-level items (minimum 4, maximum 8)
- P0 programs MUST be a single entry with sub-items (never split)
- LT review always includes the date: "LT review (3/31)"
- Every P0 scope area MUST appear
- Verticals supporting P0 rank ABOVE lower-tier programs
- Verticals state cross-program scope ("for XPF/DD-PF/Armada")
- Cross-cutting items attributed to the program they impact, not the owning vertical
- No meetings as standalone line items (meetings inform, not populate)
- No named syncs as sub-items (extract the workstream/outcome instead)
- No internal context (seniority, CVP count, IcM numbers)
- Side projects included only when real deliverable exists; admin tasks excluded
- Workstream-first framing, not metric-first
- No stale metrics without freshness warning
- Milestone/objective framing before tactical details
- Program co-owners named; ad-hoc collaborators not named
- Draft is always human-reviewed — never auto-posted

**Staleness check:**
If `state/work/work-thrivesync.md` has a `last_posted` date within 6 days, show:
`"✅ ThriveSync already posted this week ([date]). Run 'work thrivesync' to regenerate."`

After user approves, update `state/work/work-thrivesync.md` with posted content and date.

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
## §8.13 Headline Briefing (New Default: 4–48h Gap)

**Trigger:** Gap between 4h and 48h since last briefing (overridden by
day-of-week rules when Monday, Friday, or 1st of month). This is the new
default for normal morning/evening cadence — replaces `standard` as the
4–48h auto-selection.

**Template:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · [Day, Month Day]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ [ONE THING — highest U×I×A item, plain English, no machine IDs]
   → [Optional: one-line action appended if item creates an action item]

🔴 [Critical item 2, if present]
🟠 [Urgent item 3, if present — max 2 urgent items in headline]

📅 [N] events ([event 1 time · event 2 time · ...])
📋 [N] overdue · [N total] open
🎯 [Goal 1]: [status one word] · [Goal 2]: [status] [if relevant]
🤝 Overdue: [name] ([N] days) [if present]

Say "show everything" for the full briefing, or ask about any domain.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Quiet Day Template (when no critical or urgent items exist):**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · [Day, Month Day] — ✅ All Clear
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Nothing urgent today.

📅 [N] events ([event 1] · [event 2] [if present])
📋 [N] items open · none overdue
🎯 [Summary of goal status if any goals active]

Say "show everything" for the full picture.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Format Rules:**

1. **Only critical (🔴) and urgent (🟠) items appear.** Standard and low
   items live in "show everything."

2. **Empty domains are invisible.** "No new activity" is noise. If a domain
   has nothing, it does not appear in the headline.

3. **ONE THING is the opening line.** The highest U×I×A item is the first
   thing the user reads. If no critical/urgent item, opens with "All clear"
   (quiet day format).

4. **Calendar is one line.** "[N] events (event 1 time · event 2 time)" —
   not a full event list. "Show everything" expands to full calendar block.

5. **Items and goals are summary stats.** "4 overdue · 12 open" and
   "Weight: on track · Azure cert: stalled" — enough to know if action is
   needed, not the full pulse.

6. **Relationships are one line.** Top overdue reconnect only.

7. **Developer metrics are gone.** Signal:noise, PII stats, and email count
   move to `/health`. The briefing footer is the "show everything" prompt.

8. **Machine IDs are hidden.** "OI-023" → "the tax return." "G-001" →
   "Summit Mailbox Peak." Artha resolves IDs internally when the user
   refers to items by description.

9. **Dates are human.** "April 15 (17 days)" not "2026-04-15." "3 days ago"
   not "2026-03-26."

10. **The drill-down prompt is always present.** Every headline briefing
    ends with "say 'show everything' for the full briefing, or ask about
    any domain." This teaches progressive disclosure through repetition.

**Flash vs Headline — The Distinction:**

| | Flash (§8.8) | Headline (§8.13) |
|---|---|---|
| **Trigger** | <4h since last briefing | 4–48h since last briefing |
| **Question it answers** | "Anything change since I last checked?" | "What's the ONE thing I need to know?" |
| **Data source** | State diffs only — no pipeline rerun | Full 21-step pipeline, critical/urgent filter |
| **Max length** | 8 lines | 5–15 lines |
| **Empty behavior** | "Nothing new" (2 words) | "All clear" + calendar + stats |
| **Critical alerts** | Shown if present | Always the opening line |

**Format Selection Priority Table:**

| Gap | Day | Priority | Format Selected |
|-----|-----|----------|----------------|
| Any | Monday (1st run) | 1 (highest) | `weekly` (§8.6 + week-ahead) |
| Any | 1st of month | 1 | `monthly-retro` (§8.10) |
| Any | Friday | 2 | `headline` + weekend-planner section |
| < 4h | Any | 3 | `flash` (§8.8) |
| 4–48h | Any | 4 | `headline` (§8.13) ← **new default** |
| > 48h | Any | 5 | `digest` (§8.7) |
| User: "deep" | Any | User | `deep` |
| User: "everything" | Any | User | `standard` (§8.1) |

**Note:** Day-of-week and calendar rules take priority over gap rules. A
Monday with a 73-hour gap runs `weekly`, not `digest`. The 1st of the
month on a Friday runs monthly-retro, not the Friday weekend-planner.

**Backward compatibility:** `/brief standard` and "show everything" still
produce the full §8.1 format unchanged. `/brief deep` produces §8.1 plus
trend analysis and coaching. This section adds `headline` as a new format;
it does not modify or deprecate any existing format.

---
