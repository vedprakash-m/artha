# Artha — UX Specification

> **Version**: 1.9 | **Status**: Draft | **Date**: March 2026
> **Author**: [Author] | **Classification**: Personal & Confidential
> **Implements**: PRD v5.8, Tech Spec v3.5

| Version | Date | Summary |
|---------|------|---------|
| v1.9 | 2026-03 | Intelligence expansion + platform parity: financial_resilience briefing block, gig income 1099-K alert thresholds, purchase interval observation format, structured contact profiles UX, pre-meeting context injection briefing block, digital estate inventory UX, instruction-sheet action type, subscription action proposals format, setup.ps1 Windows onboarding, --doctor diagnostic UX, Apple Health connector entry point, longitudinal lab tracking (implements PRD v5.8 F15.100–F15.113) |
| v1.8 | 2026-03 | Phase 1b UX: /domains command, household-aware briefings, renter mode, offline/degraded mode banners, script-backed view commands (status/goals/items/scorecard), pet reminders format, domain selection in onboarding |
| v1.7 | 2026-03 | ACB v2.1 UX: Multi-LLM Q&A, HCI command redesign, write commands, thinking ack, structured output |
| v1.6 | 2026-03 | Backup & Restore UX: `backup.py` CLI output format, session backup confirmation, cold-start workflow, key management UX, §14.5 |
| v1.6 | 2026-03 | Channel Bridge UX: push format, interactive commands, scope-filtered output |
| v1.5 | 2026-03 | WorkIQ work calendar UX, merged calendar view, Teams join actions |
| v1.4 | 2026-03 | Intelligence amplification UX (29 enhancements), `/diff`, weekend planner |
| v1.3 | 2026-02 | Supercharge UX: flash briefing, coaching engine, bootstrap, dashboard |
| v1.2 | 2026-02 | Phase 2A: digest mode, relationship pulse, leading indicators |
| v1.1 | 2026-01 | Pre-flight gate errors, `/items` command |

Full detailed changelog: see [CHANGELOG.md](../CHANGELOG.md)

---

## Table of Contents

1. [Design Philosophy & Principles](#1-design-philosophy--principles)
2. [Interaction Model](#2-interaction-model)
3. [Information Architecture](#3-information-architecture)
4. [Catch-Up Briefing — Output Design](#4-catch-up-briefing--output-design)
5. [Weekly Summary — Output Design](#5-weekly-summary--output-design)
6. [Alert System Design](#6-alert-system-design)
7. [On-Demand Chat — Conversational Patterns](#7-on-demand-chat--conversational-patterns)
8. [Goal Intelligence — Interaction Design](#8-goal-intelligence--interaction-design)
9. [Action Proposals — Approval UX](#9-action-proposals--approval-ux)
10. [Slash Commands — Command Palette](#10-slash-commands--command-palette)
11. [Proactive Check-In — Conversational Design](#11-proactive-check-in--conversational-design)
12. [Email Briefing — Cross-Device Design](#12-email-briefing--cross-device-design)
13. [Family Access Model — Multi-User UX](#13-family-access-model--multi-user-ux)
14. [Error & Recovery UX](#14-error--recovery-ux)
    - [14.5 Backup & Restore UX](#145-backup--restore-ux)
15. [Onboarding & First-Run Experience](#15-onboarding--first-run-experience)
16. [Progressive Disclosure & Information Density](#16-progressive-disclosure--information-density)
17. [Voice & Accessibility](#17-voice--accessibility)
18. [Visual Message Generation — Creative UX](#18-visual-message-generation--creative-ux)
19. [Autonomy Progression — Trust UX](#19-autonomy-progression--trust-ux)
20. [Channel Bridge — Mobile Output Design](#20-channel-bridge--mobile-output-design)
21. [Structured Contact Profiles & Pre-Meeting Context UX](#21-structured-contact-profiles--pre-meeting-context-ux)
22. [UX Gaps & Design Decisions](#22-ux-gaps--design-decisions)

---

## 1. Design Philosophy & Principles

Artha's UX is defined by a constraint unique in personal AI: **the primary interface is a terminal**. There is no GUI, no web dashboard, no mobile app (beyond email delivery and Claude.ai Projects for iPhone). This constraint is not a limitation — it is a feature. The terminal forces extreme information density, eliminates visual distraction, and ensures the system's output earns its screen time through pure signal quality.

### 1.1 UX Principles

**UX-1 — Silence is the default state.** Artha does not speak unless it has something worth saying. Every line of output must justify its presence. If nothing is urgent, say so in one line — don't pad. The user's scarcest resource is attention, not computing power.

**UX-2 — Scannable before readable.** All output is designed to be scanned in seconds and read in minutes. Severity emojis, consistent section headers, and tabular data allow the user to triage visually before engaging cognitively. The user should know "how bad is it?" within 3 seconds of looking at the briefing.

**UX-3 — Progressive disclosure everywhere.** The daily briefing is the summary. The `/domain` command is the detail. The state file is the source of truth. Three layers, always consistent, never redundant. The user should never need to ask "where is the full detail?" — they always know where to go.

**UX-4 — Consistent information architecture.** Every domain, every alert, every action proposal follows the same structural pattern. Once you learn how immigration alerts look, you know how finance alerts look. Consistency reduces cognitive load to near zero for familiar interactions.

**UX-5 — Conversation, not configuration.** Users should never fill out forms, edit YAML, or learn a schema. Goals are created through natural conversation. Preferences are expressed in natural language and stored in `memory.md`. Configuration changes happen through dialogue, not file editing.

**UX-6 — Terminal-native typography.** All formatting uses Markdown rendered in terminal. Emoji as semantic markers (🔴🟠🟡🔵), Unicode box drawing for tables, indentation for hierarchy. No ANSI escape codes for color (they break in email delivery). Formatting must work identically in terminal and in a plain-text email client.

**UX-7 — Respect the user's time budget.** The morning briefing targets <3 minutes reading time. On-demand chat targets <10 second response. Weekly summaries target <5 minutes. The user has defined these budgets (PRD §6); every UX decision honors them.

**UX-8 — Family-aware output.** When output mentions family members, use first names consistently (never "your child" or "dependent"). Cross-family items are grouped by person, not by domain, when the user is triaging family logistics.

### 1.2 Design Constraints

| Constraint | Impact | Mitigation |
|---|---|---|
| Terminal-only primary interface | No rich media, no interactive elements, no hover states | Markdown formatting, emoji semantics, slash commands as "buttons" |
| Email as cross-device channel | Must render in any email client (plain text + HTML) | Dual-format delivery: Markdown in terminal, converted HTML in email |
| No persistent UI state | No session memory beyond `memory.md` + Claude `/memory` | Slash commands provide instant re-entry points |
| Pull-based (no push notifications) | User must initiate every interaction | Make catch-up so valuable that daily usage is self-reinforcing |
| Single user operator | No multi-user real-time collaboration | Family access via separate Claude.ai Projects with filtered state |

---

## 2. Interaction Model

### 2.1 Entry Points

Artha has exactly four entry points. Every user interaction begins at one of these:

```
┌─────────────────────────────────────────────────────────────────┐
│                     ARTHA ENTRY POINTS                          │
│                                                                 │
│  1. CATCH-UP (Mac Terminal)                                     │
│     "catch me up" · /catch-up · "morning briefing" · "SITREP"  │
│     → Full workflow: fetch + process + brief + email            │
│                                                                 │
│  2. ON-DEMAND CHAT (Mac Terminal)                               │
│     "What bills are due?" · "How's Arjun doing?" · any query   │
│     → State-file lookup, no email fetch                         │
│                                                                 │
│  3. SLASH COMMAND (Mac Terminal)                                │
│     /status · /goals · /domain · /cost · /health               │
│     → Structured output, no natural language parsing            │
│                                                                 │
│  4. MOBILE READ (iPhone — Claude.ai Project or Email)           │
│     Read briefing email · Query cached state in Claude Project  │
│     → Read-only, staleness = hours since last catch-up          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Offline/local data sources** (no entry-point session required):
- **Apple Health** *(v1.9)*: Export ZIP parsed locally by `scripts/connectors/apple_health.py` during catch-up processing. No network required. Tracks steps, heart rate, weight, sleep, blood pressure, and 11 other HKQuantityTypeIdentifier types. Enabled via `config/connectors.yaml` (`enabled: true`).
- **Longitudinal lab results** *(v1.9)*: Manually entered in `state/health.md` using structured lab history table. Apple Health import handles compatible metrics. Multi-year trend arrows (↑↓→) surfaced in Health domain briefing block.
```

### 2.2 Session Flow Model

A typical Artha session follows one of two patterns:

**Pattern A — Morning Catch-Up (3–5 minutes)**
```
User opens terminal → cd ~/OneDrive/Artha → claude
  │
  ├── "catch me up"
  │     └── [2-3 min processing] → Briefing in terminal
  │           └── Scans briefing (30 seconds)
  │                 └── If alerts: asks follow-up questions
  │                       └── If actions proposed: approve/modify/reject
  │                             └── "Thanks, see you tonight" → session ends
  │
  └── Session includes: decrypt → fetch → process → brief → email → encrypt
```

**Pattern B — Quick Check (30 seconds – 2 minutes)**
```
User opens terminal → cd ~/OneDrive/Artha → claude
  │
  ├── /status (quick health check — 5 seconds)
  │     OR
  ├── "When is the PSE bill due?" (state lookup — 10 seconds)
  │     OR
  ├── /goals (goal scorecard — 10 seconds)
  │     OR
  ├── /domain immigration (deep dive — 30 seconds)
  │
  └── Session does NOT fetch new emails or run full catch-up
```

### 2.3 Session Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│ SESSION START                                                   │
│ Claude reads CLAUDE.md → loads Artha.md → recognizes identity   │
│ Artha says nothing until the user speaks (UX-1: silence)        │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│ SESSION ACTIVE                                                  │
│ User can: catch-up, chat, run commands, approve actions         │
│ Artha maintains context of current session                      │
│ State files are decrypted and accessible                        │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│ SESSION END                                                     │
│ Triggered by: user saying "done" / "thanks" / closing terminal  │
│ Artha: encrypts sensitive state → confirms → session closes     │
│                                                                 │
│ Closing message (if catch-up was run):                          │
│ "Caught up. 47 emails → 6 items. Next recommended: tonight."   │
│   GFS backup: 9 file(s) → daily/2026-03-14.zip                 │
│                                                                 │
│ Closing message (backup failed):                                │
│ "Caught up. 47 emails → 6 items. Next recommended: tonight."   │
│   ⚠ GFS backup FAILED — no files archived.                     │
│                                                                 │
│ Closing message (if quick check only):                          │
│ "Got it. Last full catch-up: 6 hours ago."                     │
└─────────────────────────────────────────────────────────────────┘
```

### 2.4 Greeting Behavior

Artha does NOT greet the user unprompted. When the user starts a session, Artha waits for input. This is deliberate — Artha is an instrument, not a companion.

**Exception:** If the last catch-up was >48 hours ago, Artha appends a one-line nudge to its first response:

```
(Last catch-up: 52 hours ago. 187 unread emails. Consider running /catch-up.)
```

This is a data-driven nudge, not a greeting. It appears only when there is actionable information to surface.

---

## 3. Information Architecture

### 3.1 Content Hierarchy

All Artha output follows a consistent three-tier information hierarchy:

```
TIER 1 — Alerts (what needs attention NOW)
  → 🔴 Critical: Immigration deadline <30 days, bill overdue
  → 🟠 Urgent: Deadline <90 days, bill due <3 days
  → Appears at the TOP of every briefing, every session

TIER 2 — Status (what changed since last time)
  → Domain summaries: Immigration, Finance, Kids, etc.
  → Goal pulse: on track / at risk / behind
  → Appears in the BODY of briefings and on-demand queries

TIER 3 — Insight (what you should know but didn't ask)
  → ONE THING: The single most important observation
  → Cross-domain patterns
  → Artha Observations (weekly)
  → Appears at the BOTTOM of briefings — reward for reading through
```

This hierarchy is invariant across all output modes (catch-up, weekly summary, on-demand chat, slash commands). The user learns it once and can navigate any Artha output by structural intuition.

### 3.2 Domain Presentation Order

Domains are never presented alphabetically. They follow a consistent priority order based on consequence asymmetry (cost of missing something):

| Order | Domain | Rationale |
|---|---|---|
| 1 | Immigration | Multi-year consequences for missed deadlines |
| 2 | Finance | Bill overdue = credit damage |
| 3 | Kids & School | Time-sensitive academic signals |
| 4 | Health | Appointment/Rx windows |
| 5 | Calendar | Today's schedule context |
| 6 | Travel | Upcoming trip logistics |
| 7 | Home | Utility/maintenance cycles |
| 8 | Insurance | Renewal windows |
| 9 | Vehicle | Registration/recall safety |
| 10 | Estate | Review cycles; digital estate inventory (legal docs, passwords, beneficiaries) |
| 11 | Work-Life Boundary | Weekly pattern signal |
| 12 | Learning | Personal development |
| 13 | Social | Relationships |
| 14 | Shopping | Low consequence |
| 15 | Digital Life | Subscriptions |

**Exception:** If a lower-priority domain has a 🔴 or 🟠 alert, it promotes to the top of the list for that briefing. Domain order is *default* order — alerts override it.

### 3.3 Data Density Guidelines

| Output Type | Target Length | Lines (approx.) | Reading Time |
|---|---|---|---|
| Catch-up briefing (quiet day) | Minimal | 15–25 lines | 1 minute |
| Catch-up briefing (busy day) | Standard | 40–60 lines | 2–3 minutes |
| Catch-up briefing (crisis) | Extended | 60–80 lines | 3–4 minutes |
| Weekly summary | Comprehensive | 80–120 lines | 4–5 minutes |
| Slash command output | Compact | 10–30 lines | 30 seconds |
| On-demand chat answer | Direct | 3–15 lines | 15 seconds |
| Action proposal | Structured | 8–12 lines | 20 seconds |
| Check-in | Conversational | 10–20 lines | 1 minute |

---

## 4. Catch-Up Briefing — Output Design

### 4.1 Full Briefing Template

**Visual structure** (ASCII box art with section dividers):

1. **Header:** `ARTHA · [Day], [Date]` with last catch-up time, email count, period
2. **🔴 CRITICAL / 🟠 URGENT:** Top-priority alerts with domain tags
3. **📅 TODAY:** Calendar events (work=💼 prefix, conflicts=⚠️, overlap=⚠️ OVERLAP). If a calendar event references a known contact (≥3 profile fields populated), inject a 📅 pre-meeting context block (relationship summary, last discussed topics, suggested talking points). Footer: meeting count, total duration, focus windows. Work calendar unavailability note when applicable.
4. **📅 WEEK AHEAD** *(Monday only, v1.4)*: Day-by-day events + deadlines table, week complexity note
5. **📬 BY DOMAIN:** Per-domain summaries (immigration, kids, finance, home, etc.)
6. **🤝 RELATIONSHIP PULSE** *(v1.2)*: Reconnect needs, upcoming events, cultural occasions
7. **🎯 GOAL PULSE:** Status/Trend/Leading Indicator table per goal
8. **💡 ONE THING:** Single most important insight with URGENCY×IMPACT×AGENCY scoring
9. **Footer:** Email→action ratio, work meeting summary, next recommended catch-up
10. **🛡️ PII GUARD** *(v1.4)*: Scan/redaction stats
11. **🎯 CALIBRATION** *(v1.4)*: 2 post-briefing accuracy questions (skip-friendly)

### 4.2 Briefing Design Rules

1. **Critical/Urgent → Top.** Always. No exceptions. Even if the rest of the briefing is empty.

2. **Empty sections are stated, not hidden.** If there are no critical alerts: `(none)`. If there are no calendar events today: `No events today.` Hiding empty sections makes the user wonder "did it miss something?" Stating emptiness builds trust.

3. **Domain items use prose, not tables.** Tables work for structured data (goals, accounts). Domain updates are prose — one line per item with context. "Arjun: AP Language essay returned (B+)" is faster to scan than a 5-column table.

4. **Goal Pulse uses fixed-width alignment.** Goal names left-aligned, status right-aligned, trend far-right. The eye tracks the status column. If everything says "On track," the user is done in 2 seconds.

5. **ONE THING is never generic.** It is always specific, always actionable, always contextualized. "Stay on top of your finances" would never appear. "Your EAD renewal is 90 days out — initiate attorney contact within 2 weeks" would.

6. **Footer shows signal-to-noise ratio.** "47 emails → 6 actionable" — this single metric communicates Artha's filtering value. Over time, the user sees: Artha consistently reduces 50+ signals to <10 actionable items.

7. **Separator lines use box-drawing characters.** `━━━` for major sections, no characters for domain sub-items. The visual rhythm: heavy bar = new section, indent = sub-item, blank line = breathing room.

### 4.3 Quiet Day Briefing

When nothing is urgent, Artha says so explicitly — no padding. Short format: "Nothing urgent" header, only 🟡/🔵 items as bullets, goal pulse (brief), next recommended catch-up. Design rule: empty sections are omitted entirely, not shown with "(none)".

### 4.4 Crisis Day Briefing

Multiple 🔴 Critical alerts. Structure: all critical items in numbered list with action proposals, then "EVERYTHING ELSE" as compressed bullets for lower-priority items. Goal pulse omitted (focus on crisis). Design: emphasize consequence, offer concrete next steps for each critical item.

### 4.5 Digest Mode Briefing *(v1.2)*

Triggered when gap >48h. Groups by day (not domain). Per-day: Critical/Urgent get individual lines, lower-priority items counted ("8 FYI items"). Action items consolidated and deduped across gap period. Standard sections (Goal Pulse, ONE THING) appear after gap summary. Header shows gap duration.

### 4.6 Flash Briefing *(v1.3)*

≤8 lines, ≤30s reading. Structure: 🔴/🟠 alerts (1 line each), calendar (today only, pipe-separated), "IF YOU DON'T" consequence for top item, footer (goal count + signal ratio + volume). No domain sections or Goal Pulse table. Auto-selected when <4h since last run or user says "quick update."

### 4.7 Consequence Forecast Display *(v1.3)*

"IF YOU DON'T" section appears after ONE THING for items with 🔴/🟠 severity and clear deadlines. Format: numbered list, each stating inaction → timeline → first-order consequence → cascade. Max 3 items. Reasoning chain must be specific and >70% confidence.

### 4.8 Weekend Planner Display *(v1.4)*

Triggered Friday ≥12PM. Two-column layout: Saturday/Sunday with open time windows and suggested activities (from open_items + goals). Weekend deadlines section. Quick-action suggestions from open_items (≤15min effort).

### 4.9 Financial Resilience Output Block *(v1.9)*

The `financial_resilience` skill produces a compact block surfaced in the **Finance** domain section of the daily briefing when the skill cache has changed deltas:

```
  💰 Financial Resilience
     Burn rate:      $X,XXX/mo
     Emergency fund: N.N months runway
     Single-income:  N.N months (stress scenario)
     ↑ Runway improved +0.4 mo vs. last week
```

**Design rules:**
- Only shown when `compare_fields` delta is non-zero (i.e., something changed)
- On quiet weeks (no delta): omitted from daily, included in weekly summary
- Alarm threshold: runway <3 months → 🟠 Urgent; runway <1.5 months → 🔴 Critical

### 4.10 Purchase Interval Observation Format *(v1.9)*

Shopping domain may include purchase interval observations as 🔵 informational notes:

```
  🔵 Purchase Interval — Paper Towels
     Purchased: 6 times in 12 months (~every 60 days)
     Observation: Bulk buying could reduce cost ~15%
     Observed: 2026-03
```

**Design rules:** 🔵 severity (never actionable alert); one observation per briefing maximum; only surfaces after ≥3 observed purchase cycles.

---

## 5. Weekly Summary — Output Design

### 5.1 Weekly Summary Template

**Structure:** Header (catch-up count, emails, alerts, actions) → WEEK AT A GLANCE (4-5 executive summary bullets) → KIDS THIS WEEK (per-child: grades, GPA, activities, notes) → FINANCE THIS WEEK (spending vs budget, bills, anomalies) → IMMIGRATION UPDATE (Visa Bulletin, EAD status, deadlines) → GOAL SCORECARD (This Week / YTD / Status columns with fixed-width alignment) → COMING UP NEXT WEEK (exactly 5 items) → 🤝 RELATIONSHIP HEALTH *(v1.2)* (per-tier contact status, reciprocity, upcoming) → ⚡ LEADING INDICATOR ALERTS *(v1.2)* → 📊 ACCURACY PULSE *(v1.2)* (actions, corrections, domain accuracy) → ARTHA OBSERVATIONS (3-5 numbered cross-domain insights, 2-4 lines each).

### 5.2 Weekly Summary Design Rules

1. Kids before Finance (parents check grades weekly). 2. Goal Scorecard is centerpiece — fixed-width, 3 columns (This Week, YTD, Status). 3. Coming Up is exactly 5 items (forces ranking). 4. Artha Observations are numbered, specific, evidence-backed (2-4 lines each). 5. "Week at a Glance" is the executive summary — if user reads nothing else, they get the picture.

## 6. Alert System Design

### 6.1 Alert Severity Taxonomy

```
  SEVERITY        EMOJI    TRIGGER EXAMPLES                          UX BEHAVIOR
  ─────────────── ──────── ──────────────────────────────────────── ──────────────────────────
  Critical        🔴       Immigration deadline <30 days             Top of briefing, always
                           Bill overdue                              Included in emailed briefing
                           Document expiring                         Numbered for follow-up ref
                           RFE received                              Recommended action shown

  Urgent          🟠       Deadline <90 days                         Top of briefing, always
                           Bill due <3 days (non-auto-pay)           Included in emailed briefing
                           Low assignment score                      Action hint shown
                           Unusual spend > $500

  Heads-up        🟡       Upcoming renewal                          In domain section
                           Passport <6 months                        No action proposal
                           Learning goal behind                      Informational

  Info            🔵       Weekly goal summary                       In weekly summary only
                           Monthly financial snapshot                Not in daily briefing
                           Visa Bulletin published                   unless requested

  Work Conflict   ⚠️/🔴    *(v1.5 — WorkIQ integration)*
                  🔴       Cross-domain (work↔personal)              Top of briefing (Impact=3)
                           e.g., Teams call ↔ school pickup          Action: reschedule proposal
                  ⚠️       Internal work (work↔work)                 Info tier (Impact=1)
                           e.g., back-to-back Teams calls            Self-resolvable noise
                  📊       Heavy meeting load (>300 min/day)         In 📅 TODAY footer
                           Context switching fatigue (<60m gap)       Focus window suggestion

  Gig Income      🟡/🟠/🔴  *(v1.9 — 1099-K tracking)*
                  🟡       Single platform YTD >$5K                  Finance domain section
                           e.g., "Stripe earnings $5,200 YTD"        "Track for 1099-K threshold"
                  🟠       Cumulative gig income >$20K               Finance domain section
                           approaching IRS reporting threshold        "Confirm withholding + tracking"
                  🔴       Q4 year-end 1099-K risk                   Top of briefing (Finance)
                           high probability of 1099-K issuance       "Prepare for tax reporting"
```

### 6.2 Alert Deduplication UX

The same alert should not appear in consecutive briefings unless the underlying condition has changed. Artha tracks alert history in state files:

- **First occurrence**: Full alert with context.
- **Repeat (no change)**: Suppressed from briefing. If queried via `/domain`, shows as "ongoing."
- **Repeat (condition worsened)**: Re-surfaces with updated urgency and delta. Example: "PSE bill $247 — now 2 days overdue (was due March 20)."
- **Resolution**: Alert replaced with resolution line. "✅ PSE bill paid March 19."

### 6.3 Alert Cross-Reference

When an alert intersects with a goal, show the connection:

```
🟠 [Finance] Amazon spend at 85% of monthly target with 10 days left.
   ↳ Impacts: Monthly Amazon < $XXX goal (currently ⚠ At Risk)
```

This makes alerts meaningful rather than mechanical. The user doesn't just see a number — they see why it matters.

---

## 7. On-Demand Chat — Conversational Patterns

### 7.1 Response Format Guidelines

On-demand chat responses follow a strict format discipline:

**Factual queries → Direct answer first, context second:**
```
User: "When is the PSE bill due?"
Artha: March 20. $247. Not on auto-pay.
       Last paid: Feb 18 ($234, +5.5% month-over-month).
```

**Status queries → Structured overview:**
```
User: "How's immigration looking?"
Artha: 
  EAD renewal: 87 days until filing deadline (2026-06-03).
  H-1B extension: 13 months until filing deadline (2027-04-15).
  Visa Bulletin: EB-2 India at 2019-01-15. Your PD: 2019-04-15.
  Est. 20 months to current.
  CSPA (Arjun): Age 16.4, age-out at 21. No immediate concern.
  CSPA (Ananya): Age 13.1. No concern.
  
  Last attorney contact: March 5 ([immigration attorney]). No open items.
```

**Comparison queries → Tables:**
```
User: "What did we spend last month vs this month?"
Artha:
  Category          February     March (MTD)    Δ
  ───────────────── ──────────── ────────────── ──────
  Amazon            $XXX         $XXX           +12%
  Groceries         $XXX         $XXX           -3%
  Dining            $XXX         $XXX           +28% ⚠
  Subscriptions     $XXX         $XXX           ±0%
  Total             $X,XXX       $X,XXX         +8%
```

**Ambiguous queries → Clarify by offering options, not asking open questions:**
```
User: "What's happening with Arjun?"
Artha: Arjun (11th, Lincoln High). This week:
  Academics: B+ AP Language, A- AP Physics. GPA 3.7.
  SAT: March 13 — 5 days. Prep log: 12 hrs this week.
  Activities: Econ Club met Tuesday.
  Alerts: None.
  
  Want detail on: academics, SAT prep, or college prep timeline?
```

### 7.2 Conversational Memory in Action

Artha uses `memory.md` to personalize chat responses:

```
User: "Alert me about Spirit Week again."
Artha: Noted. Removing Spirit Week from the suppressed alerts list.
       (Previously suppressed on Feb 15.)
       Future Spirit Week notifications will appear in briefings.
```

```
User: "Actually, don't start alerting about Spirit Week."
Artha: Understood. Spirit Week remains suppressed.
```

This demonstrates that Artha remembers past decisions (reads from `memory.md`) and allows the user to modify them through conversation. No settings UI needed.

### 7.3 "I Don't Know" Design

When Artha lacks the data for a query, it says so explicitly and suggests how to get the answer:

```
User: "What's our total insurance premium?"
Artha: I don't have insurance policy details yet.
       To set this up: provide carrier names, policy numbers,
       and premium amounts — or forward a recent premium
       statement to your configured Gmail address and I'll extract
       the details on next catch-up.
```

Never hallucinate. Never guess. "I don't know" with a resolution path is infinitely more valuable than a plausible-sounding wrong answer.

---

## 8. Goal Intelligence — Interaction Design

### 8.1 Goal Creation Flow

Goals are created through conversation, not forms. The UX must feel like talking to a financial advisor, not filling out a spreadsheet.

```
User: "I want to track our savings progress."

Artha: Let's set that up. A few questions:

  1. What's your net worth target?
     (Or I can suggest one based on your current trajectory.)

User: "Let's aim for $X by end of 2026."

Artha: Got it. I'll track:
  Goal: Net worth ≥ $X by December 31, 2026
  Type: Outcome
  Metric: Aggregate balance across Chase, Fidelity, Vanguard,
          Morgan Stanley, E*Trade, Wells Fargo (minus mortgage)
  Data source: Statement emails (Phase 1), Plaid API (Phase 2)
  Cadence: Monthly snapshot in weekly summary
  
  I'll alert you if the trend line projects a miss by >10%.
  
  Create this goal? [Yes / Adjust / Skip]
```

**Design rules for goal creation:**
- Artha always proposes the full structured goal for confirmation — never creates silently.
- The user can say "yes" to accept, or modify any field via conversation.
- Auto-wired metrics are highlighted — the user sees which data sources will feed the goal automatically.
- If a metric requires manual input, Artha says so upfront: "This will need manual check-ins — I'll prompt you weekly."

### 8.2 Goal Progress Visualization

Terminal-native progress bars using Unicode block characters:

```
Net Worth 2026 Target       ██████░░░░  62%  → On Track
Monthly Amazon < $XXX       ████████░░  78%  ⚠ At Risk
ByteByteGo course by Q2     ██░░░░░░░░  22%  ⚠ Behind
Exercise 4x/week            ██████░░░░  60%  → On Track
```

**Bar interpretation:**
- `█` = progress achieved (filled)
- `░` = remaining (empty)
- `→` prefix = stable/on track
- `⚠` prefix = at risk or behind
- `🔴` prefix = significantly behind
- `✓` = achieved (100% filled)

### 8.3 Goal Trajectory Forecast Display

When a goal is at risk, Artha shows projected outcome alongside alternatives:

```
━━ GOAL FORECAST ━━━━━━━━━━━━━━━━━━━━━━━━━

Net Worth · 2026 Target

  Target:     $XXX,XXX by Dec 31, 2026
  Current:    $XXX,XXX (62%)
  Trend:      +$X,XXX/month (3-month avg)
  Projected:  $XXX,XXX by Dec 31 (92% of target)

  ⚠ Projected to miss by $XX,XXX

  Options:
  1. Increase monthly savings by $X,XXX → closes gap
  2. Extend deadline to March 2027 → current pace sufficient
  3. Adjust target to $XXX,XXX → matches trajectory

  Which option, or keep monitoring? [1/2/3/monitor]
```

### 8.4 Goal Conflict Surfacing

When two goals are in tension, Artha surfaces the trade-off explicitly — never buries it in separate domain reports:

```
━━ ⚡ GOAL TENSION DETECTED ━━━━━━━━━━━━━━

Your savings goal and family travel goal may be in conflict.

  Savings:  +$X,XXX/month needed to hit target.
  Travel:   India trip estimated $X,XXX (no progress booked).

  If you book the trip, savings goal projects to miss by $XX,XXX.
  If you defer the trip, savings goal stays on track.

  This is a trade-off, not an error. Want to:
  1. Adjust savings timeline (extend 3 months)
  2. Budget for trip from a specific account
  3. Defer trip to 2027
  4. Keep both goals unchanged — I'll flag monthly
```

### 8.5 Leading Indicators Display *(v1.2 — Workstream B)*

Leading indicators show early-warning signals for each goal, before lagging metrics confirm problems:

```
User: /goals leading

━━ LEADING INDICATORS ━━━━━━━━━━━━━━━━━━━━

Net Worth 2026 Target
  Lagging: +2.1% YTD                        → On Track
  Leading: Savings rate 18.2% (target 20%)   ⚠ Slightly below
  Leading: Monthly spend trend ↑ +3%        → Nominal

Arjun GPA ≥ 3.8
  Lagging: 3.7 current GPA                  → On Track
  Leading: Assignment completion 95%         ✔ Healthy
  Leading: Missing assignments 0             ✔ Healthy
  Leading: ⚠ Completion rate -15% this week  ⚠ Early warning

Immigration Readiness
  Lagging: EAD renewal 90 days out           ⚠ Action due
  Leading: [immigration attorney] response time 3d avg     ✔ Normal
  Leading: Document checklist 60% ready      ⚠ Gap

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ 1 divergence alert: Arjun assignment completion.
  "Lagging (GPA) still on track, but leading dropped.
  If trend continues, expect GPA impact in 2–3 weeks."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Design rules for leading indicators:**
1. **Always show lagging first, then leading.** Anchors the user in current reality before showing predictive signals.
2. **Divergence alerts appear at the bottom.** When a lagging indicator says "on track" but a leading indicator says "warning," Artha explains the discrepancy in plain language.
3. **Color/emoji coding:** ✔ healthy, ⚠ early warning, 🔴 critical divergence. Same taxonomy as alert system.
4. **Accessible via `/goals leading` or shown automatically** in the weekly Goal Scorecard when a divergence is detected.

### 8.6 Coaching Engine Interaction Design *(v1.3)*

Three interaction types: **Accountability nudge** (question format by default — "What's blocking X?", with quick-response buttons), **Obstacle anticipation** (predicts blockers based on calendar + behavioral patterns, suggests time blocks), **Celebration** (milestone-level achievements only). Design rules: appears after briefing alongside action proposals, max 1 nudge per catch-up (rotating across goals), nudge format configurable (question/statement/metric), obstacle anticipation opt-in, dismissal always available (suppress 7 days).

### 8.7 Goal Sprint Display *(v1.4)*

Active sprint shown in Goal Pulse: target, progress bar, pace (needed vs actual), status. Sprint validation: target_value mandatory. Max 1 active sprint, 7-30 day duration. Behind-pace nudge in daily briefing; on-pace gets checkmark only.

### 8.8 College Application Countdown Display *(v1.4)*

Appears in Kids section when any milestone ≤90 days away. Milestones color-coded: 🔴 ≤14d, 🟠 ≤30d, 🟡 ≤90d, ✅ complete. Always shows NEXT ACTION. Application year as context anchor. Displayed during application season (Aug-Mar senior year).

## 9. Action Proposals — Approval UX

### 9.1 Proposal Display Format

Every action shows: type, recipient, channel, full content preview, trust level, friction classification *(v1.2)*, source domain. Options: [approve] [edit] [skip] [skip all].

### 9.2 Approval Patterns

Single action: approve → "✅ Sent. Logged to audit.md." Edit: user specifies change → Artha shows updated preview → approve/edit more/skip. Batch approval for low-friction items: "approve all" for calendar additions, visual generation.

### 9.3 Sequencing

Order: 1. Critical/urgent (immigration, finance), 2. Communications (emails, WhatsApp by recipient), 3. Calendar (batched), 4. Informational, 5. Teams Join *(v1.5)* (≤15min imminent meetings). After last action: summary of approved/skipped.

### 9.4 WhatsApp Action UX

WhatsApp uses OS URL scheme — opens with pre-filled message, user must tap Send. Note displayed: "This will open WhatsApp with pre-filled message. You'll need to tap Send." Cannot confirm delivery (outside Artha's view).

### 9.5 Instruction-Sheet Action Type *(v1.9)*

Two actions in `config/actions.yaml` use `type: instruction_sheet` — they generate guidance prose rather than executing code. Handler is null; no network calls, no file writes.

```
  ┌──────────────────────────────────────────────────────────────┐
  │ ACTION: Cancel Subscription                                  │
  │ Type: instruction_sheet                                      │
  │                                                              │
  │ Step 1. Confirm subscription in state/digital.md             │
  │ Step 2. Locate cancellation method (account → billing)       │
  │ Step 3. Cancel before next renewal date: [date]              │
  │ Step 4. Confirm cancellation email received                  │
  │ Step 5. Update state/digital.md → mark as cancelled         │
  │                                                              │
  │ [done — I cancelled it]  [remind me next catch-up]          │
  └──────────────────────────────────────────────────────────────┘
```

**Subscription action proposal formats** (from `prompts/digital.md`, v1.9):
- Price increase detected → "Propose: cancel or justify continued value"
- Trial-to-paid conversion upcoming → "Propose: cancel or upgrade decision"
- Trial already converted without decision → "Flag for immediate review" (🟠 Urgent)

## 10. Slash Commands — Command Palette

### 10.1 Command Reference

Slash commands are Artha's "keyboard shortcuts" — structured operations that bypass natural language parsing for known intents.

```
COMMAND          BEHAVIOR                                    RESPONSE TIME
──────────────── ─────────────────────────────────────────── ─────────────
/catch-up        Full catch-up workflow                      2–3 minutes
/status          Health check — last run, stale domains      5 seconds
/goals           Goal scorecard only                         10 seconds
/domain <name>   Deep-dive into one domain                  10 seconds
/domains         List all 24 domains with enable/disable     5 seconds   *(v1.8)*
                 /domains enable <name>
                 /domains disable <name>
                 /domains info <name>
/cost            Monthly API cost vs. budget                 5 seconds
/health          System integrity — file checks, CLI health  10 seconds
/items           Open action items from open_items.md        5 seconds
                 Optional filters: /items kids
                                   /items P0
                                   /items overdue
                                   /items quick (≤5 min phone-ready)
/decisions       Decision log — active and resolved           10 seconds  *(v1.2)*
/scenarios       What-if analysis — run or review             15 seconds  *(v1.2)*
/relationships   Relationship pulse — reconnects, upcoming    10 seconds  *(v1.2)*
/bootstrap       Guided state population interview            3–10 min    *(v1.3)*
                 Optional: /bootstrap finance (single domain)
/dashboard       Life-at-a-glance snapshot from dashboard.md  5 seconds   *(v1.3)*
/scorecard       Life scorecard with dimension scores         5 seconds   *(v1.3)*
/diff            State changes since last catch-up            5 seconds   *(v1.4)*
                 Shows additions/removals/modifications
                 per domain. No email fetch.
/catch-up flash  Flash briefing (≤30 sec reading time)        1 minute    *(v1.3)*
/catch-up deep   Deep analysis with extended reasoning        5–8 minutes *(v1.3)*
```

**CLI diagnostic flag (not a slash command):**

```
artha.py --doctor   Unified diagnostic — runs 11 system checks    *(v1.9)*
                    (Python version, venv, packages, age binary,
                     age key, OAuth tokens, state directory,
                     PII hook, last catch-up recency)
                    Output: ━━ ARTHA DOCTOR ━━ banner + per-check
                    icons (✓ / ⚠ / ✗) + summary line
                    Exit 0 = all pass, exit 1 = any failure
                    Run after setup or when something feels wrong.
```

### 10.2 /status Output Design

Header (last run time, gap), system health summary (MCP status, OAuth, context pressure), stale domain warnings (>7 days), active alerts count. Quick snapshot — no email fetch.

### 10.3 /domain Output Design

Deep-dive: domain header → current status (full state file content) → recent activity (last 10 entries) → active alerts → related goals. Shows sensitivity level. "Tell me more" / "when was the last..." for drill-down.

### 10.4 /goals Output Design

Scorecard table (all goals: status, trend, metric, target, pace). Per-goal detail on request. Sprint overlay if active. Color-coded status (on track/at risk/behind/exceeded). Leading indicators shown alongside lagging metrics.

### 10.5 /health Output Design

File integrity (registry.md check), MCP health, CLI availability, OAuth token status, state file freshness, context window stats, catch-up history stats (avg duration, reliability, costs). Green/yellow/red per component.

### 10.6 /items Output Design

Open items grouped by priority (P0→P1→P2). Per item: domain tag, description, deadline, age. Filters: `/items kids`, `/items P0`, `/items overdue`. To Do sync status shown.

### 10.7 /decisions Output Design *(v1.2)*

Active decisions: summary, domains affected, review trigger, deadline countdown. Resolved decisions: condensed single-line each. "Describe a decision" to add new entry.

### 10.8 /scenarios Output Design *(v1.2)*

Active scenarios with per-domain impact comparison (side-by-side columns). "What if..." to create new scenario. Templates available for common patterns (refinance, college, immigration timeline, job change).

### 10.9 /relationships Output Design *(v1.2)*

Per-tier relationship status (close family, close friends, extended). Reconnect queue. Cultural calendar (upcoming occasions). Communication patterns (outbound trend). Group health.

### 10.10 /bootstrap Output Design *(v1.3)*

Domain selection (unpopulated domains listed with sensitivity). Guided interview: one question at a time, validation, confirmation. Progress saved per-domain. Completion report: fields populated vs total.

### 10.11 /dashboard Output Design *(v1.3)*

Life Pulse table (per-domain status), Active Alerts (top 5), Open Items summary, Life Scorecard (7 dimensions with 1-10 scores + trends), System Health. Single-file readable on any device.

### 10.12 /scorecard Output Design *(v1.3)*

7-dimension assessment (Financial, Immigration, Kids, Health, Social, Career, Home). Per-dimension: score, trend arrow, key metric, improvement suggestion if <7. Composite Life Score with week-over-week delta.

### 10.13 /diff Output Design *(v1.4)*

Per-domain changes since last catch-up: additions (new entries), removals (~ prefix), value changes (old→new). Unchanged domains listed under "No Changes."

### 10.14 Monthly Retrospective Display *(v1.4)*

Month at a glance → domain narratives → decisions made/resolved → goals progress table (start vs end) → Artha self-assessment (accuracy, signal:noise) → looking ahead.

### 10.15 Power Half Hour Display *(v1.4)*

Top 3 highest-impact 30-min tasks from open_items + goal blockers. Per task: effort estimate, impact, domain. Timer-style countdown. Completion → mark items done + celebrate progress.

### 10.16 Teach Me Interaction Pattern *(v1.4)*

Domain-aware explanations calibrated to user's knowledge level. Structure: "What you know" (from state) → "What matters" (context-specific) → "What to do" (actionable). Sources cited. Follow-up questions suggested. Immigration/finance domains get legal/regulatory context.

## 11. Proactive Check-In — Conversational Design

### 11.1 Check-In Trigger Logic

Check-ins are NOT scheduled — they are data-driven. Artha surfaces a check-in at the end of a catch-up briefing when specific conditions are met:

| Condition | Check-in type |
|---|---|
| 2+ goals are ⚠ At Risk or 🔴 Behind | Goal drift check-in |
| Work-life balance behind for 2+ weeks | Work pattern check-in |
| No exercise/learning logged in >5 days | Habit lapse check-in |
| Cross-domain conflict detected | Priority check-in |
| Spending exceeds monthly budget by >20% | Financial check-in |

### 11.2 Check-In Format

Check-ins appear after the briefing, separated by a clear divider:

```
━━ 💬 ARTHA CHECK-IN ━━━━━━━━━━━━━━━━━━━━━

Quick check on 2 things:

1. Exercise and learning both dropped off this week.
   Work ran late Tuesday and Thursday (detected from email
   timestamps). This is the 3rd week in a row.
   
   Want me to block Saturday morning for a workout +
   ByteByteGo session? [yes / no / not now]

2. Amazon spend is at 85% of monthly target with 10 days left.
   Three pending orders total $XXX.
   
   Flag before next checkout? [yes / adjust target / no]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 11.3 Check-In Design Rules

1. **Max 3 items per check-in.** More than 3 creates fatigue. If 5 items qualify, show the top 3 by consequence severity.

2. **Always offer choices, not open-ended questions.** "Want me to block Saturday morning?" not "What would you like to do about exercise?" Choices reduce cognitive load.

3. **Reference the data, not the feeling.** "Work ran late Tuesday and Thursday (detected from email timestamps)" — not "Are you feeling overworked?" Artha deals in facts.

4. **Allow deferral.** Every check-in option includes "not now" or equivalent. Artha does not nag. If the user dismisses something twice, Artha adds it to `memory.md` and stops asking for 14 days.

5. **Never check in on quiet days.** If the briefing shows no alerts and all goals on track, there is no check-in. UX-1 (silence is default) overrides check-in eagerness.

---

## 12. Email Briefing — Cross-Device Design

### 12.1 Email Delivery Format

The same briefing delivered to terminal is also emailed. The email version has two critical modifications:

**Modification 1 — Sensitivity filter:** Domains with `sensitivity: high` or `critical` (immigration, finance, health, insurance, estate, audit) show only a summary line in the email:

```
Immigration
  ✅ 1 item processed. No new alerts. (Full detail in terminal.)

Finance
  ✅ 2 items processed. No new alerts. (Full detail in terminal.)
```

**Modification 2 — HTML rendering:** The email includes both a plain-text version (the Markdown) and an HTML rendered version with:
- Monospace font for tables and progress bars
- Emoji rendered natively
- Section headers with subtle background shading
- Responsive width (readable on iPhone SE through 27" monitor)

### 12.2 Email Subject Line Convention

```
Artha · March 7 — 🟠 1 urgent · 6 items
Artha · March 8 — ✅ quiet day
Artha · March 9 — 🔴 2 critical · 🟠 1 urgent · 8 items
Artha Weekly · March 3–9 — 5 on track · 2 at risk
```

**Subject line design rules:**
- Always starts with `Artha` — enables email filtering.
- Date follows — enables chronological sorting.
- Alert severity summary — the user sees urgency before opening the email.
- Item count — calibrates expected reading time.

### 12.3 Email Rendering Guidelines

| Element | Terminal | Email (HTML) |
|---|---|---|
| Section headers | `━━ SECTION ━━━━━` | `<h2>` with border-bottom |
| Progress bars | `██████░░░░` | Unicode (monospace font) |
| Emoji | Native terminal | Native email client |
| Tables | Markdown table syntax | HTML `<table>` |
| Code/data | Indented text | `<pre>` block |
| Links | N/A (terminal) | Clickable `<a>` tags |

### 12.4 iPhone Reading Experience

On iPhone, the user reads the emailed briefing in their preferred mail app. The design optimizes for:

- **Single-column layout** — no side-scrolling.
- **Large touch targets** — links are padded for finger tapping (if any links included).
- **Dark mode compatibility** — no hardcoded colors; uses semantic markup.
- **5-minute reading window** — the briefing is designed to be consumed during a commute, before coffee, or while waiting.

For deeper queries, the user opens Claude.ai's iOS app with the "Artha" Project (containing uploaded state snapshots) and asks questions in natural language.

---

## 13. Family Access Model — Multi-User UX

### 13.1 Access Tiers

| User | Access Level | Interface | Domains Visible |
|---|---|---|---|
| Raj | Full admin | Mac terminal + email + Claude.ai | All 18 domains |
| Priya | Shared family | Email briefing + Claude.ai Project | Finance, Immigration, Kids, Home, Travel, Health, Calendar, Insurance, Estate, Social, Goals (shared) |
| Arjun | Personal academic | Claude.ai Project (filtered) | His academic data, activities, college prep, his own goals |
| Ananya | Age-appropriate | Claude.ai Project (filtered) | Her academic data, activities, her own goals |

### 13.2 Priya's Experience

Priya receives a filtered briefing via email — same structure, fewer domains. Her email subject line:

```
Artha (Family) · March 7 — 🟠 1 urgent · 4 items
```

The `(Family)` tag differentiates her briefing from the full briefing. Her version:
- Includes: Kids, Finance (summary-level), Immigration, Calendar, Health, Home.
- Excludes: Work-life boundary (irrelevant to her), Digital Life, Learning (Raj-specific).
- Sensitivity filtering still applies — no financial details in email.

Her Claude.ai Project contains state files for shared domains only and responds within those domain boundaries.

### 13.3 Kids' Experience (Phase 2)

Arjun's Claude.ai Project contains:
- His grades, attendance, and activity data from `kids.md`.
- His SAT prep and college prep timeline.
- His personal goals (set by him via conversation).
- Does NOT include: family finances, immigration details, other family member data.

Ananya's Project is similar but with age-appropriate scope (7th grade level — no college prep, no financial awareness).

### 13.4 Family Briefing Variant

Simplified briefing sent to spouse email (if enabled in config). Shared domains only (kids full, home full, travel full, calendar full, finance summary-only, immigration milestone-only, health appointments-only). Excluded: finance details, estate, insurance, employment. Subject: "Artha Family · [Day], [Date]". Extra sensitivity filter: immigration shows "1 item — review with [primary user]". Sent after main briefing at Step 14.

## 14. Error & Recovery UX

### 14.1 Error Display Patterns

Every error follows: **[What happened] → [What it means] → [How to fix it]**. No stack traces or error codes by default.

**Error types and UX:**

| Error | Severity | UX |
|---|---|---|
| MCP connection failure | ⚠ Warning | Show stale data age, proceed with available data, offer fix command |
| PII filter failure | 🔴 HALT | "Emails fetched but NOT processed. Sensitive data may be in scrollback." Fix: verify pii_guard.sh |
| Partial domain failure | ⚠ Warning | "N domains processed normally. 2 emails retried next run." |
| Pre-flight gate failure | ⛔ HALT | "Catch-up aborted before any data fetch. No files modified." Fix: specific setup command |
| Stale lock (>30m) | ⚠ Auto-clear | "Previous session exited uncleanly. Auto-clearing." |
| API quota exceeded | ⛔ HALT | "Partial data misleading — no briefing generated. Retry in 60 min." |
| OAuth refresh failure | ⛔ HALT | "Access token expired, refresh failed." Fix: re-run setup script |
| Net-negative write | ⚠ Guard | "Would REMOVE N of M fields (X% reduction)." Options: show diff / write anyway / skip |
| Skill failure (P0) | 🔴 HALT | "Cannot verify immigration status. Catch-up halted." Fix: test skill |
| Skill failure (P1/P2) | ⚠ Warning | "Property tax date not refreshed. Using last known." |
| WorkIQ failure | ⚠ Non-blocking | "Work calendar unavailable — personal events only." |
| Corrupt decrypt | ⛔ HALT | "File failed validation. Restored from pre-decrypt backup." |
| GFS backup failed (0 files) | ⚠ Warning | "GFS backup FAILED — no files archived." State files are intact; backup not created for this cycle. Fix: check age binary and keychain key with `backup.py preflight`. |
| Backup validation overdue | ⚠ Proactive | "Last backup validation: N days ago. Run: `python scripts/backup.py validate`" — surfaced in `/health` output and weekly catch-up footer when >35 days. |
| Age key not found in keychain | ⛔ HALT | "Cannot encrypt backup — age key missing from keychain." Fix: `python scripts/backup.py import-key`. Does not block vault encrypt (state files still encrypted); only backup blocked. |
| Backup ZIP corrupt on install | ⛔ HALT | "ZIP failed integrity check. Cannot restore." Fix: try an earlier ZIP or retrieve from OneDrive cloud copy. |
| Bootstrap template detected | ⚠ Info | "State file has placeholder data. Run /bootstrap to populate." |
| OAuth expiry warning | ⚠ Proactive | "Token expires in ~3 days. No action needed now." |

### 14.2 Automated Testing & Validation UX

Test suite via `/health` or manual command. Success: per-test checkmarks with category (Unit/Integration/Extraction/Integrity), total count, duration. Failure: failed tests with specific error description and fix suggestion (file + line reference). "Scannable before readable" principle.

### 14.3 Intelligent Alerting UX

Distinguishes **Status Confirmation** (informational) from **Status Change** (alerting). Rule: briefings show data for enabled skills but only add 🔴/🟠 alert marker when `changed: true` in skill cache. No-change data shown without alert prefix. Staleness indicator only when data >24h old.

### 14.4 Weather Concierge UX

Outdoor Open Items trigger NOAA skill → "go/no-go" recommendation. Shows forecast summary (temp, conditions, wind) with GO/NO-GO verdict. Surfaced as 🟠 URGENT when conditions are favorable for time-sensitive items.

### 14.5 Backup & Restore UX

#### 14.5.1 Post-Encrypt Backup Confirmation

Every successful `vault.py encrypt` (catch-up close) appends a single-line backup status to the terminal immediately after encryption. This line is never suppressed — it is the user's signal that their data is durably archived:

```
  GFS backup: 9 file(s) → daily/2026-03-14.zip
```

If the backup attempt produces no files (empty snapshot):
```
  ⚠ GFS backup FAILED — no files archived.
```

Rules: one line only, indented 2 spaces (visually subordinate to the closing message), never interrupts the briefing body.

#### 14.5.2 `backup.py status` Output Format

Running `python scripts/backup.py status` produces a compact catalog box:

```
━━━ Artha Backup Status ━━━━━━━━━━━━━━━━━━━━━━━━━━
  daily    7 ZIPs   latest: 2026-03-14   size: 2.3 MB
  weekly   4 ZIPs   latest: 2026-03-09   size: 9.1 MB
  monthly  3 ZIPs   latest: 2026-03-01   size: 26.4 MB
  yearly   1 ZIP    latest: 2025-12-31   size: 87.2 MB
  total: 15 ZIPs · 125.0 MB
  last validated: 2026-03-10 (4 days ago) ✓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Validation overdue warning (>35 days since last validate):
```
  last validated: 2026-01-28 (45 days ago) ⚠ OVERDUE
  Run: python scripts/backup.py validate
```

#### 14.5.3 `backup.py validate` Output Format

Validation shows per-file results, capped to avoid noise:

```
Validating daily/2026-03-14.zip (9 files)...
  ✓ finance.md.age     1842 words  sha256: abc123
  ✓ goals.md           312 words   sha256: def456
  ✓ immigration.md.age 519 words   sha256: ghi789
  ... (6 more — all OK)
Validation complete: 9/9 passed. Logged to audit.md.
```

On failure:
```
  ✗ health.md.age   FAIL: checksum_mismatch (expected: aaa111, got: bbb222)
Validation complete: 8/9 passed. 1 FAILED. Logged to audit.md.
```

#### 14.5.4 Key Management UX

**Annual reminder (surfaced in `/health` output):**
```
  🔑 Key backup: last exported 2025-12-31 (74 days ago)
     Run: python scripts/backup.py export-key | pbcopy
     Store the key in your password manager or a fire-safe printout.
```

**Export flow** (`backup.py export-key`):
- Prints `AGE-SECRET-KEY-…` to stdout only. No file written.
- One-line prompt precedes key output: `# Age private key — store securely:`
- User is responsible for routing (e.g., `| pbcopy` or `> key.txt`).

**Import flow** (`backup.py import-key`):
```
Paste your age private key and press Ctrl-D:
AGE-SECRET-KEY-…
Key stored in macOS Keychain (service: artha-age-key). Verified.
```

Error if key already exists: `Key already in keychain. Use --force to overwrite.`

#### 14.5.5 Cold-Start Workflow UX

Step-by-step terminal output for fresh-machine restore:

```
Step 1 — Install age:
  brew install age        # macOS
  choco install age       # Windows

Step 2 — Import your private key:
  python scripts/backup.py import-key
  (paste key from password manager, then Ctrl-D)

Step 3 — Verify environment:
  python scripts/backup.py preflight
  ✓ age binary: /opt/homebrew/bin/age
  ✓ age-keygen binary: /opt/homebrew/bin/age-keygen
  ✓ keychain key: found (artha-age-key)
  ✓ backup directory: backups/ (15 ZIPs)

Step 4 — Restore from backup ZIP:
  python scripts/backup.py install backups/daily/2026-03-14.zip --dry-run
  (review output, then re-run without --dry-run)
```

`preflight` exits non-zero and prints a specific fix command on any check failure.

#### 14.5.6 `vault.py health` Backup Section

The `vault.py health` output (or `/health` command) includes a GFS section:

```
GFS Backup
  ZIPs:       15 (daily: 7, weekly: 4, monthly: 3, yearly: 1)
  Last backup: 2026-03-14 (today) ✓
  Last validate: 2026-03-10 (4 days ago) ✓
  Key: in keychain ✓
```

Degraded states surfaced in health output:
- `Last backup: 2026-03-12 (2 days ago) ⚠` — suggests checking for session interruptions
- `Last validate: NEVER ⚠` — prompts immediate `backup.py validate`
- `Last validate: 45 days ago ⚠ OVERDUE` — same prompt
- `Key: NOT FOUND ✗` — shows `backup.py import-key` fix command

---

## 15. Onboarding & First-Run Experience

### 15.1 Bootstrap Sequence

First-run detects unpopulated state files → domain selection (priority-ordered) → guided interview (one question at a time, skip-friendly, format validation) → state file preview → confirmation → write. Progress saved per-domain. Resume with `/bootstrap` if interrupted.

**Platform setup entry points** *(v1.9)*:

| Platform | Setup script | Venv path |
|----------|-------------|-----------|
| macOS / Linux | `bash setup.sh` | `~/.artha-venvs/.venv` |
| Windows | `.\setup.ps1` | `$HOME\.artha-venvs\.venv-win` |

After setup completes on any platform, run `python artha.py --doctor` to validate all 11 system checks. The `--doctor` output confirms the environment is healthy before the first catch-up.

### 15.5 Post-Setup Diagnostic UX *(v1.9)*

`artha.py --doctor` is the recommended first command after setup or after any environment change (e.g., OS upgrade, Python reinstall, new machine):

```
━━ ARTHA DOCTOR ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ Python 3.12.2 (≥3.11 required)
  ✓ Virtual environment active
  ✓ Core packages: PyYAML, keyring, jsonschema
  ✓ age binary: 1.1.1 (/opt/homebrew/bin/age)
  ✓ Age encryption key in keyring
  ✓ age_recipient configured
  ✓ Gmail OAuth token: valid
  ⚠ Outlook OAuth token: not configured (optional)
  ✓ State directory: 12 files
  ✓ PII git hook installed
  ✓ Last catch-up: 14 hours ago
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
10 passed · 1 warning · 0 failed
```

**Design rules:**
- `✓` = pass, `⚠` = non-blocking warning, `✗` = failure (exits 1)
- Each failure shows a one-line fix command immediately below it
- ⚠ warnings are informational (Outlook OAuth not set up = intentional for Gmail-only users)
- All 11 checks complete in <3 seconds; no network calls except OAuth token validation

### 15.2 First Catch-Up Experience

First catch-up has special handling: longer processing time warning, progress indicators per step, reduced expectations ("Your first briefing will be basic — it gets smarter each run"), post-briefing micro-survey, celebration on completion.

### 15.3 Initial Goal Setup Prompt

After the first catch-up, Artha proposes the initial 5 goals from PRD OQ-3:

```
Now that I have baseline data, let's set up your first goals.
Based on your data, I recommend these 5 starting goals:

1. Net worth / savings trajectory
   → Metric: Aggregate balances from detected accounts
   
2. Immigration readiness
   → Metric: All deadlines known ≥90 days out
   
3. Arjun GPA ≥ 3.8
   → Metric: Auto-tracked from Canvas grade emails
   
4. Protected family time ≥ 10h/week
   → Metric: Calendar analysis + work-hour detection
   
5. Learning consistency (target: X hrs/month)
   → Metric: Obsidian + course activity detection

Set up all 5 now? Or customize first? [set up all / customize / later]
```

### 15.4 Progressive Feature Discovery

Artha does not explain all features at once. Features are introduced contextually:

| Feature | Discovered when |
|---|---|
| On-demand chat | User asks a question outside catch-up (naturally) |
| Slash commands | After 3rd catch-up: "Tip: Use /status for a quick health check." |
| Goal creation | After first catch-up: initial goal setup prompt |
| Check-ins | After 7 days: first data-driven check-in appears |
| Action proposals | After 14 days: first action proposed (if Trust Level 1 criteria met early) |
| Weekly summary | First Sunday after setup |
| Visual generation | First occasion detected in `occasions.md` |

**Feature tips appear at most once per week, at the end of a briefing, and only for features the user hasn't used yet.**

---

## 16. Progressive Disclosure & Information Density

### 16.1 Three-Layer Model

Every piece of information in Artha exists at three levels of detail, and the user can traverse between them:

```
LAYER 1 — Briefing Line
  "Immigration: Visa Bulletin moved. EAD 87 days."

    ↓ /domain immigration

LAYER 2 — Domain Deep-Dive
  Full status table, deadlines, Visa Bulletin history,
  CSPA calculations, attorney contact log.

    ↓ "Show me the immigration state file"

LAYER 3 — Raw State File
  ~/OneDrive/Artha/state/immigration.md
  Full Markdown with YAML frontmatter.
  Human-editable. Git-diffable.
```

The user never needs to go to Layer 3 — but they can. This transparency builds trust. "I can always go check the source" is a powerful confidence builder.

### 16.2 Information Density by Context

| Context | Density | Example |
|---|---|---|
| Briefing alert | Minimal — one line | `🟠 PSE bill $247 due March 20. Not on auto-pay.` |
| Domain section | Summary — 2-3 lines per item | `Chase: Feb statement received. Spending $X,XXX (within budget).` |
| /domain deep-dive | Full — tables, history, projections | Full account table, bill calendar, spending trends |
| On-demand chat | Adaptive — matches query specificity | Short answer for "when is PSE due?" Full overview for "how are finances?" |
| Weekly summary | Moderate — weekly delta focus | Spending vs. budget, anomalies, week-over-week changes |

### 16.3 "More" and "Less" Conventions

```
User: "Tell me more about the Visa Bulletin movement."
Artha: [Provides 6-month history, trend analysis, projection]

User: "Just the headline."
Artha: EB-2 India moved to 2019-01-15. You're 3 months out.
       ~20 months to current at trailing average.
```

Artha adapts verbosity to the user's implicit request. "Tell me more" → expand. "Just the..." → condense. These are patterns Claude naturally handles, but `memory.md` records the user's baseline preference:

```
Preference: Morning briefings concise (<2 minutes reading time).
Preference: Immigration deep-dives detailed (Raj wants full context).
```

---

## 17. Voice & Accessibility

### 17.1 Voice Access (Phase 3)

Voice interaction uses Apple Shortcuts as the bridge between spoken language and Claude Code:

```
                  Siri / Shortcut trigger
                         │
                         ▼
┌──────────────────────────────────────────┐
│ Apple Shortcut: "Ask Artha"              │
│                                          │
│ 1. Capture voice → text (Whisper/Siri)   │
│ 2. Pass to: claude --print -p "[query]"  │
│ 3. Return response as spoken text        │
│    or notification                       │
└──────────────────────────────────────────┘
```

**Voice-appropriate queries:**
- "Hey Siri, ask Artha when the PSE bill is due."
- "Hey Siri, ask Artha how Arjun is doing in school."
- "Hey Siri, ask Artha for a quick status."

**Not voice-appropriate:**
- Full catch-up (too long for spoken output)
- Action approvals (requires reading + confirming)
- Goal creation (requires conversational back-and-forth)

### 17.2 Accessibility Considerations

| Concern | Design Decision |
|---|---|
| Screen reader | All output is plain text with semantic structure — screen readers handle it natively |
| Color blindness | Emoji severity markers (🔴🟠🟡🔵) are distinguishable by shape + color. Text labels ("Critical", "Urgent") always accompany emoji. |
| Low vision | Terminal font size controlled by user. No small-print footnotes. |
| Motor impairment | Slash commands reduce typing. "approve" / "skip" are short. |
| Cognitive load | Consistent structure, progressive disclosure, max 3 check-in items, max 5 coming-up items. |

---

## 18. Visual Message Generation — Creative UX

### 18.1 Workflow

User requests → Artha generates prompt → Gemini Imagen creates visual → saved to `visuals/` → preview shown → user approves. Retry: "try another style" regenerates. Privacy: no PII in image prompts.

### 18.2 Style & Occasions

Culturally aware visual generation: warm/festive for Diwali/Holi/Christmas, personalized for birthdays/anniversaries. Style configurable in memory.md (traditional, modern, minimalist). Occasions calendar in `occasions.md` triggers proactive suggestions. Batch generation for group occasions.

## 19. Autonomy Progression — Trust UX

### 19.1 Trust Level Visibility

Current trust level shown in `/status` and `/autonomy`. Progress bar toward next level with criteria (catch-ups completed, false positive rate, acceptance rate, corrections per session).

### 19.2 Elevation Prompt

When all criteria met: briefing footer "🎓 Autonomy Level 2 eligible — use `/autonomy review`". Review shows detailed criteria status (met/unmet). User must explicitly confirm `/autonomy elevate`. Never auto-elevates.

### 19.3 Demotion UX

Triggered by: 2+ missed critical alerts, financial error >$100, immigration deadline missed, or user-initiated. Immediate notification with explanation. Affected action types listed. Recovery path shown with specific criteria. User can also demote manually.

## 20. Channel Bridge — Mobile Output Design

### 20.1 Push Message Design (Layer 1)

Post-catch-up push uses the **flash briefing** format truncated to `max_push_length` (default 500 chars). Two flavors:

**Full scope (`full`):**
```
ARTHA · Friday, Mar 13

3 alerts today.
🔴 EAD renewal deadline in 28 days — start I-765 prep
🟡 Arjun: 2 missing Canvas assignments (AP Physics, AP CS)
🟢 PSE bill paid ✓ | Fidelity 401k rebalance window open

/status · /tasks · /alerts
```

**Family scope (`family`):**
```
ARTHA · Friday, Mar 13

Family update
📅 Ananya's orchestra concert Thursday 6 PM
📚 Arjun: 2 assignments due this week
🏠 PSE bill paid ✓

/tasks for action items
```

**Design rules:**
- No immigration data, no financial details in `family` scope — scope filter is a content gate, not a redaction pass
- Staleness is implicit — push fires only during catch-up, so data is always fresh
- If channel API unreachable: message queued in `state/.pending_pushes/`, delivered next run

### 20.2 HCI Command Design (Layer 2) *(v1.7)*

The command interface is designed for **one-thumb phone operation with minimal cognitive load**. Every command has multiple entry paths — the user never needs to remember exact syntax.

**Command normaliser:** 45+ aliases mapped to canonical commands. Longest-match-first strategy handles multi-word aliases (`items add`, `catch up`) before single-word fallback.

| Shortcut | Aliases | Response | Source |
|----------|---------|----------|--------|
| `s` | `status`, `/status` | System health + alerts + goal pulse | `health-check.md`, `dashboard.md` |
| `a` | `alerts`, `/alerts` | Active alerts by severity | Latest briefing, `health-check.md` |
| `t` | `tasks`, `items`, `/tasks` | Open items (OI-NNN, ≤10 items) | `open_items.md` |
| `q` | `quick`, `/quick` | Tasks ≤5 min (phone-ready) | `open_items.md` |
| `d` | `domain`, `/domain` | Domain list (no args) or domain deep-read | State files |
| `d <name>` | `domain <name>` | Single domain detail; encrypted domains route through LLM | State file or LLM |
| `g` | `goals`, `goal`, `/goals` | Goal scorecard | `goals.md` |
| `diff` | `/diff`, `diff 7d` | State changes since last catch-up (or custom period) | State file mtimes |
| `dash` | `dashboard`, `db` | HTML dashboard | `dashboard.md` |
| `?` | `help`, `h`, `/help` | Command list (READ/WRITE/OTHER sections) | Static |
| `catchup` | `catch-up`, `catch up`, `briefing` | Full catch-up pipeline | Pipeline |

**Write commands** *(v1.7)*:

| Command | Example | Action |
|---------|---------|--------|
| `items add` | `items add Call estate attorney P0 estate 2026-03-20` | Parses description, priority, domain, deadline; appends OI-NNN |
| `add item` | `add item Buy groceries` | Alternate word order — same result |
| `done` | `done OI-005` or `done 5` | Marks item as done, adds `date_resolved` and `resolution` |

**Design principles:**
- **Slash optional** — `status` and `/status` are identical. Users coming from Telegram bot culture can use slashes; others don't need to.
- **Hyphens optional** — `catchup`, `catch-up`, `catch up` all work.
- **Case insensitive** — all input lowercased before matching.
- **Single-letter shortcuts** — `s`, `a`, `t`, `q`, `d`, `g`, `?` for the most common commands. Designed for speed on a phone keyboard.
- **Unknown input → LLM Q&A** — anything not matching a command alias is routed to the multi-LLM Q&A pipeline (Layer 3). No "Unknown command" dead ends for conversational questions.

### 20.3 Multi-LLM Q&A UX (Layer 3) *(v1.7)*

Free-form questions get the full power of Artha's multi-LLM stack from a Telegram chat.

**Interaction flow:**
```
User: which credit card should I use for grocery shopping?
  │
  ├─ Artha sends: "💭 Thinking…" (immediate ack)
  ├─ Assembles context: prompts/finance.md + state/finance.md.age (decrypted) + open_items
  ├─ Calls Claude CLI (~16.5s) with workspace context
  ├─ Vault auto-relocked
  ├─ Deletes "💭 Thinking…" message
  └─ Sends structured answer:
       1. Chase Freedom Flex — 5% grocery (Q1 rotating)
       2. Amex Gold — 4x points on groceries (always)
       • Use Freedom Flex this quarter, Amex Gold otherwise
```

**Ensemble mode:**
```
User: aa what are the best 529 plan options for college savings?
  │
  ├─ Artha sends: "💭 Thinking…" (immediate ack)
  ├─ All 3 CLIs called in parallel (~40s total)
  ├─ Responses consolidated via Claude Haiku
  ├─ Deletes "💭 Thinking…" message
  └─ Sends consolidated answer
```

**Structured output rules:**
- Numbered lists (1. 2. 3.) for ranked/sequential items
- Unicode bullets (•) for unordered items
- One-line direct answer first, then supporting detail
- No Markdown (`**`, `##`, `` ` ``) — plain text with Unicode only
- Blank lines between sections

**Thinking ack UX:**
- "💭 Thinking…" sent immediately via `send_message_get_id()` for all long-running commands (catch-up, domain deep-dive, LLM Q&A, diff, goals)
- Deleted via `delete_message()` after real response arrives
- If deletion fails (API error), ack remains — harmless
- Provides instant feedback that Artha received the message and is working

**Response format:**
```
ARTHA Status · Friday, Mar 13

3 alerts · 7 open tasks · Goals: 4/5 on track

🔴 EAD renewal in 28 days
🟡 Arjun: 2 missing assignments
🔵 PSE bill due March 20 ($247)

_Last updated: 2h 14m ago_
```

**Staleness indicator:** Every response ends with `_Last updated: {age} ago_`. If data is >12h old, prefixed with ⚠️.

**Domain list response** *(v1.7)*:
```
ARTHA Domains

📖 Direct read:
  • calendar • kids • goals • shopping • social
  • learning • digital • boundary • comms

🤖 AI-routed (encrypted):
  • finance • health • immigration
  • estate • insurance • vehicle

Use: d <name>
```

**Message splitting:** Responses exceeding Telegram's 4096-char limit are split at paragraph boundaries. Each chunk sent as a separate message with minimal delay.

### 20.4 Help Response Design *(v1.7)*

Help output is organized into three sections for quick scanning:

```
ARTHA Commands

📚 READ
  s  status    a  alerts    t  tasks
  q  quick     d  domain    g  goals
  diff         dash
  catchup

✏️ WRITE
  items add <desc> [P0/P1/P2] [domain] [deadline]
  done <OI-NNN>

💡 OTHER
  aa <question>   → ask all LLMs
  any text        → AI-powered Q&A
  ?               → this help
```

### 20.5 Service Management UX

```bash
# Set up Telegram and start using it
python scripts/setup_channel.py --channel telegram

# Install as background service (Layer 2+3)
python scripts/setup_channel.py --install-service

# Change designated listener to another machine
python scripts/setup_channel.py --set-listener-host
```

Service is installed as a background process that restarts on failure:
- **Windows:** VBScript at Startup folder + Task Scheduler task (runs at login, RestartOnFailure)
- **macOS:** launchd plist (KeepAlive: true)
- **Linux:** systemd user unit (Restart=on-failure)

---

## 21. Structured Contact Profiles & Pre-Meeting Context UX *(v1.9)*

### 21.1 Contact Profile Model

Structured contact profiles (from `prompts/social.md`, F15.103) use a 9-field template stored in `state/social.md`:

```
## [Full Name]
relationship:        [e.g., friend / colleague / family]
last_contact:        YYYY-MM-DD
next_action:         [e.g., "coffee catch-up in April"]
location:            [city, state]
birthday:            MM-DD (year optional)
key_facts:           [bullet list of significant facts]
shared_history:      [brief shared context]
communication_style: [e.g., prefers brief texts, responds slowly]
```

**Passive fact extraction rules** (F15.105 — protecting profile integrity):
- Extract only when high-confidence (direct statement in email or calendar)
- Only update *existing* contacts — no auto-creation from thin context
- Annotate every extracted fact with ISO date: `key_facts: "promoted to VP [2026-02-12]"`
- Skip uncertain or inferential data (never assume)

### 21.2 Pre-Meeting Context Injection Format

When a calendar event references a known contact (name match in `state/social.md`), and that contact has ≥3 populated profile fields, Artha injects a pre-meeting block inline in the 📅 TODAY section:

```
  📅 1:1 w/ Daniel Rosen — 2:00 PM (Teams)  *(pre-meeting context)*
     Relationship: colleague (VP Product, same team)
     Last contact: 2026-02-28 (15 days ago) — Q1 planning discussion
     Shared history: Co-led the mobile checkout project (2024)
     Key facts: Moving to Austin in June; daughter starts college 2027
     Talking points:
       • Follow up on Q1 planning decision
       • Ask about Austin move timeline
     Pending items: OI-014 (send him the draft doc)
```

**Design rules:**
- Block uses *(pre-meeting context)* label to distinguish from regular calendar entries
- Shown only when contact has ≥3 profile fields populated (prevents sparse/noisy injections)
- Talking points are inferred from recent email/calendar context + pending open items
- Maximum 1 pre-meeting block per briefing (highest-priority meeting takes precedence if multiple)
- If no calendar match: pre-meeting context absent (never forced)

### 21.3 Relationship Pulse Integration

Pre-meeting context complements (does not replace) the 🤝 RELATIONSHIP PULSE section. The pulse shows general reconnection needs; pre-meeting context shows actionable prep for today's specific meeting.

---

## 22. UX Gaps & Design Decisions

### 22.1 Identified Gaps (Resolved in This Spec)

11 gaps identified and resolved: UX-OD-1 (greeting behavior → §2.4), UX-OD-2 (quiet day → §4.3), UX-OD-3 (error recovery → §14), UX-OD-4 (action sequencing → §9.3), UX-OD-5 (context pressure → §16.2 adaptive density), UX-OD-6 (onboarding cliff → §15 bootstrap), UX-OD-7 (mobile constraints → §12 email-first), UX-OD-8 (multi-person disambiguation → Artha confirms), UX-OD-9 (long-running catch-up → progress bar), UX-OD-10 (calibration → §4.1 post-briefing questions), UX-OD-11 (privacy transparency → PII footer).

### 22.2 Open UX Decisions

| # | Decision | Options | Recommendation |
|---|---|---|---|
| UX-OD-9 | Where should Relationship Pulse appear in daily briefing? *(v1.2)* | After BY DOMAIN (contextual) / Before GOAL PULSE (visible) / New section | **Before GOAL PULSE.** Relationships are a goal-adjacent concern. The user sees relationships → goals → ONE THING as a coherent arc from "people" to "priorities" to "action." |
| UX-OD-10 | Should leading indicators show in daily briefing or weekly only? *(v1.2)* | Daily (immediate) / Weekly (less noise) / Both with different detail | **Both.** Daily shows one column in Goal Pulse table (compact). Weekly shows full `/goals leading` detail with divergence analysis. |
| UX-OD-11 | Should digest mode be automatic or user-triggered? *(v1.2)* | Auto (>48hr gap triggers) / Manual (/catch-up --digest) / Auto with opt-out | **Auto with opt-out.** The gap detection is precise and the format is strictly better for catch-up-after-absence. If the user prefers standard format after a gap, `/catch-up --standard` overrides. |
| UX-OD-12 | Should Accuracy Pulse appear in email briefings? *(v1.2)* | Yes (transparency) / No (internal metric) / Weekly email only | **Weekly email only.** Daily accuracy data is too granular for email. Weekly summary email includes the aggregate. |
| UX-OD-13 | How should high-friction actions be visually distinguished? *(v1.2)* | Color only (🔴/🟠/🟢) / Color + text / Color + text + confirmation prompt | **Color + text + confirmation prompt.** High-friction actions get an extra "Are you sure?" confirmation line. This friction is the point — it slows the user down for consequential actions. |
| UX-OD-14 | Should `/scenarios` support interactive what-if editing? *(v1.2)* | Yes (conversational) / No (read-only display, edit via chat) / Phase 3 | **Yes, conversational.** The user says "what if the rate is 5.2% instead?" and Artha re-runs the scenario. This is the natural interaction model — scenarios are inherently iterative. |
| UX-OD-1 | Should briefing include a "Reading time: ~X min" estimate? | Yes (calibrates attention) / No (unnecessary) | **Yes.** Add to header line after email count. Reduces anxiety about time commitment. |
| UX-OD-2 | Should Artha use Priya/Arjun/Ananya's names or role labels? | Names (personal) / "your wife" / "your son" (relational) | **Names.** Always. Artha is family-aware; role labels feel clinical. |
| UX-OD-3 | Should the quiet-day briefing be auto-emailed? | Yes (consistency) / No (why email "nothing happened"?) | **Yes.** Consistency builds trust. The user expects an email every morning. A missing email creates anxiety ("did Artha break?"). Even "quiet day" is a signal. |
| UX-OD-4 | Should action proposals appear inline during catch-up or after? | During (natural) / After (batch review once briefing complete) | **After.** Let the user absorb the briefing first, then review actions. Context-first, actions-second. |
| UX-OD-5 | Should weekly summary be a separate email or appended to Sunday's daily? | Separate (clear purpose) / Appended (fewer emails) | **Separate.** The weekly summary has a different purpose (reflection vs. triage). Different email = different mental frame. |
| UX-OD-6 | Maximum time between catch-ups before Artha escalates nudge severity? | 48 hrs (one-line note) → 96 hrs (more prominent) → 7 days (🟠 alert) | **Yes, graduated.** After 7 days, the email backlog may exceed context window limits — the nudge becomes a practical necessity, not nagging. |
| UX-OD-7 | Should Artha announce processing progress during catch-up? | Silent (show briefing only) / Progress dots / Domain-by-domain | **Domain-by-domain during first 5 catch-ups** (builds confidence), then **silent** (reduce noise). Preference stored in `memory.md`. |
| UX-OD-8 | Should email briefing include a "Reply to ask Artha" feature? | Yes (conversational email) / No (email is read-only, ask on terminal) | **No.** Email is delivery-only. Mixing interfaces creates confusion. Terminal is the interaction surface; email is the archive. |
| UX-OD-15 | Should flash briefing auto-trigger for gaps <4 hours? *(v1.3)* | Auto (gap-based) / Manual (/catch-up flash only) / Auto with escape hatch | **Auto with escape hatch.** For <4hr gaps, flash is strictly better. User can say "give me the full version" to override. |
| UX-OD-16 | Should consequence forecasts show confidence percentages? *(v1.3)* | Yes (transparency) / No (false precision) / Only when >90% | **No.** Showing "73% confidence" implies actuarial precision Artha doesn't have. The confidence gate (>70%) is internal; the user sees the consequence or doesn't. |
| UX-OD-17 | Should coaching nudges appear in email briefings? *(v1.3)* | Yes (reach user where they are) / No (terminal-only, feels personal) / Weekly email only | **Weekly email only.** Daily coaching in email feels nagging. Weekly summary email includes one coaching insight — feels reflective, not pushy. |
| UX-OD-18 | Should the dashboard show all 17 domains or only active ones? *(v1.3)* | All (complete picture) / Active only (reduce noise) / All with inactive collapsed | **All with inactive collapsed.** The user should see the full picture, but quiet domains compress to one line. Active domains expand with details. |
| UX-OD-19 | Should /bootstrap allow skipping domains entirely? *(v1.3)* | Yes (user autonomy) / No (complete population) / Yes with warning | **Yes with warning.** The user says "skip estate" and Artha responds: "Skipping estate. You can populate later with /bootstrap estate." No forced completion. |
| UX-OD-20 | Should net-negative write guard threshold be user-configurable? *(v1.3)* | Yes (in config.yaml) / No (20% is universal) / Yes but only via /config command | **No.** The 20% threshold is a safety net, not a preference. Making it configurable invites the user to weaken their own protection. If 20% proves wrong, we change the default. |
| UX-OD-21 | Should Week Ahead show on non-Monday catch-ups if requested? *(v1.4)* | Monday only / Any day on request / Auto if >5 events ahead | **Any day on request.** Auto on Monday; user can ask "show me the week ahead" on any day. |
| UX-OD-22 | Should calibration questions be skippable silently or with ack? *(v1.4)* | Silent timeout / "skip" required / Auto-skip after 3 consecutive skips | **Silent timeout.** If user doesn’t answer within the session, skip silently. No nagging. |
| UX-OD-23 | Should Power Half Hour be proactively suggested or on-demand only? *(v1.4)* | Proactive (detect idle windows) / On-demand ("what should I do?") / Both | **Both.** Proactive during detected open calendar slots; also available on-demand. Max 1 suggestion per catch-up. |
| UX-OD-24 | Should Teach Me mode cite sources within state files? *(v1.4)* | Yes (transparency) / No (cleaner) / Footnotes | **Footnotes.** Add [1], [2] markers with "Source: immigration.md, line 15" at bottom. Transparency without clutter. |
| UX-OD-25 | Should college countdown appear in email briefings? *(v1.4)* | Yes (Priya sees it too) / No (terminal only, sensitive) / Yes but simplified | **Yes but simplified.** Email gets milestone count + next action only. Full countdown in terminal. |

### 22.3 Design Principles Summary Table

| # | Principle | Enforced By |
|---|---|---|
| UX-1 | Silence is the default state | Quiet-day briefing template, no unprompted greetings, no check-in on quiet days |
| UX-2 | Scannable before readable | Emoji severity, section headers, tabular goals, consistent layout |
| UX-3 | Progressive disclosure | Three-layer model (briefing → /domain → state file) |
| UX-4 | Consistent information architecture | Domain ordering, alert taxonomy, proposal format — all invariant |
| UX-5 | Conversation, not configuration | Goal creation via chat, preferences via memory.md, no YAML editing required |
| UX-6 | Terminal-native typography | Markdown, Unicode box drawing, no ANSI colors, works in email too |
| UX-7 | Respect the user's time budget | Line count targets, reading time estimates, max limits on check-in items |
| UX-8 | Family-aware output | First names, per-child sections, family briefing variant |

---

*Artha UX Spec v1.9 — End of Document*

*"The best interface is the one you forget you're using. Artha speaks when it matters, is silent when it doesn't, and always tells you where you stand — in under 3 minutes."*

---

**Cross-references:**
- PRD v5.8: §6 (Interaction Modes), §7 (FR-1 through FR-18 + F15.100–F15.113), §8 (Goal Intelligence), §9 (Architecture), §10 (Autonomy Framework), §11 (Relationship Intelligence), §12.6 (Privacy Surface), Phase 2A–B (Canvas, Apple Health)
- Tech Spec v3.5: §2 (Artha.md), §3.5 (Canvas LMS, Apple Health connector), §3.6 (Slash Commands + /diff), §4.4 (College Countdown schema), §4.10 (Decision Deadlines schema), §5.1 (Week Ahead, PII Footer, Calibration), §5.3 (Monthly Retrospective), §7.1–7.19 (pipeline steps), §8 (Security Model), §18 (revision history)
