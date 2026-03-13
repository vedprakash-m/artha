# Artha — UX Specification

> **Version**: 1.5 | **Status**: Draft | **Date**: March 2026
> **Author**: Vedprakash Mishra | **Classification**: Personal & Confidential
> **Implements**: PRD v4.1, Tech Spec v2.2

> **v1.5 Changes — WorkIQ Work Calendar Integration UX (PRD v4.1, Tech Spec v2.2):** §4.1: Updated 📅 TODAY section with merged personal+work calendar view (💼 prefix for work events, duration-based density footer). §4.1.1 NEW: Work calendar degradation display (Mac stale metadata, Windows auth failure). §6.1: Added work meeting conflict tiers (cross-domain 🔴 Impact=3, internal ⚠️ Impact=1). §9: Added Teams "Join" action proposal for imminent meetings. §14.1: Added WorkIQ-specific error patterns. Briefing footer updated with work meeting count+duration.
>
> **v1.4 Changes — Intelligence Amplification UX (PRD v4.0, Tech Spec v2.1):** 29 enhancements from deep expert review. §4.1: Added Week Ahead Preview section (Monday briefings), PII Detection Footer, post-briefing Calibration Questions block. §4.8 NEW: Weekend Planner output design. §8.7 NEW: Goal Sprint display with target calibration. §8.8 NEW: College Application Countdown dashboard display. §10.1: Added `/diff` command. §10.12 NEW: `/diff` output design — state delta visualization. §10.13 NEW: Monthly Retrospective display format. §10.14 NEW: Power Half Hour display. §10.15 NEW: Teach Me interaction pattern. §20.1: Added 6 new resolved UX gaps. §20.2: Added UX-OD-21 through UX-OD-25.
>
> **v1.3 Changes — Supercharge UX Patterns:** Eighteen enhancement UX patterns from expert review (PRD v3.9, Tech Spec v2.0). §4.6 NEW: Flash briefing template (≤30 sec, ≤8 lines) with consequence forecast line. §4.7 NEW: Consequence forecast display pattern ("IF YOU DON'T" alerts). §8.6 NEW: Coaching engine interaction design — accountability nudges, obstacle anticipation, celebration patterns. §10.1: Added `/bootstrap`, `/dashboard`, `/scorecard` commands. §10.9–10.11 NEW: Output designs for bootstrap interview, dashboard snapshot, and life scorecard. §14.1: Added data integrity guard error patterns (net-negative write, corrupt decrypt, bootstrap detection). §15.1: Enhanced bootstrap sequence with guided interview UX flow. §20.1: Added 8 new resolved gaps. §20.2: Added UX-OD-15 through UX-OD-20.

> **v1.2 Changes — Phase 2A Intelligence Workstreams:** Ten workstreams from expert review synthesis (PRD v3.8). §4.1: Added Relationship Pulse section and Leading Indicator column to daily briefing. §4.5 NEW: Digest mode briefing template for >48hr gaps (§tech-spec 5.1.1). §5.1: Added Accuracy Pulse section, leading indicator alerts, and relationship health summary to weekly summary. §8.5 NEW: Leading indicators display design. §9.1: Added friction level indicator to action proposals; friction-based batch approval. §10.1: Added `/decisions`, `/scenarios`, `/relationships` commands. §10.6–10.8 NEW: Output designs for new commands. §20: Added Phase 2A UX design decisions.

> **v1.1 Changes**: Added pre-flight gate error patterns (§14.1), stale lock / quota / OAuth failure UX patterns (§14.1), `/items` slash command (§10.1–10.2).

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
15. [Onboarding & First-Run Experience](#15-onboarding--first-run-experience)
16. [Progressive Disclosure & Information Density](#16-progressive-disclosure--information-density)
17. [Voice & Accessibility](#17-voice--accessibility)
18. [Visual Message Generation — Creative UX](#18-visual-message-generation--creative-ux)
19. [Autonomy Progression — Trust UX](#19-autonomy-progression--trust-ux)
20. [UX Gaps & Design Decisions](#20-ux-gaps--design-decisions)

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
│     "What bills are due?" · "How's Parth doing?" · any query   │
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
| 10 | Estate | Review cycles (infrequent but critical) |
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

The catch-up briefing is Artha's signature output — the artifact that defines the system's value. Every design decision serves one goal: the user should know "where do I stand?" within 3 minutes.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · Wednesday, March 7, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Last catch-up: 23 hrs ago | Emails: 47 | Period: since yesterday 7:15 PM

━━ 🔴 CRITICAL ━━━━━━━━━━━━━━━━━━━━━━━━━━━
(none)

━━ 🟠 URGENT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• [Immigration] Visa Bulletin EB-2 India → 2019-01-15.
  Your PD (2019-04-15) is 3 months away. Monitor closely.
• [Finance] PSE bill: $247 due March 20. Not on auto-pay.

━━ 📅 TODAY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 8:00 AM — 💼 Enter Top5 Weekly (Shashidhar Joshi) [Teams]
• 9:05 AM — 💼 ⚠️ OVERLAP: Admin Office Hours + LT Review for XPF
• 11:00 AM — 💼 xInfraSWPM: XPF Weekly (you organize) [Teams]
• 1:00 PM — 💼 XPF Burn-In Discussion [Teams]
• 3:30 PM — Parth orthodontist appointment
• 3:35 PM — ⚠️ CONFLICT: 💼 DM & CPE Repair Sync ↔ 🏠 School Pickup
• 6:00 PM — Trisha soccer practice pickup
📊 Today: 13 meetings (6h15m) | Focus window: 11:30am–1:00pm
ℹ️ [Work calendar: Windows-only | not available this session]  ← only when unavailable

━━ � WEEK AHEAD ━━━━━━━━━━━━━━━━━━━━━━━━━ *(v1.4 — Monday only)*
  Day         Events                    Deadlines
  Tue Mar 8   Team standup 10am         —
  Wed Mar 9   Parth ortho 3:30pm        PSE bill due ($247)
  Thu Mar 10  —                         —
  Fri Mar 11  Trisha soccer 6pm         —
  Sat Mar 12  Spring pictures (both)    —
  Sun Mar 13  —                         Holi prep

━━ �📬 BY DOMAIN ━━━━━━━━━━━━━━━━━━━━━━━━━━

Immigration
  Fragomen: H-1B extension paperwork timeline confirmed for Q2.

Kids
  Parth: AP Language essay returned (B+). Cumulative: B+.
  Trisha: Math quiz 92%. No action needed.
  Both: Spring pictures March 12.

Finance
  Chase: Feb statement. Spending within budget.
  Wells Fargo: Mortgage payment processed.

Home
  Republic Services: Schedule change next week (holiday).

━━ 🤝 RELATIONSHIP PULSE ━━━━━━━━━━━━━━━━━━ *(v1.2)*
• Reconnect: Suresh Uncle (45 days, threshold 30)
• Upcoming: Rahul’s birthday in 5 days → [action queued]
• Cultural: Holi celebration Mar 14 (temple community)

━━ 🎯 GOAL PULSE ━━━━━━━━━━━━━━━━━━━━━━━━
                         Status       Trend         Leading *(v1.2)*
Net worth trajectory     ↑ On track    +2.1% YTD    Savings rate 18%
Immigration readiness    ⚠ Action due  EAD renewal  Fragomen 3d avg
Parth GPA ≥ 3.8          → Stable      3.7 current  Assignments 95%

━━ 💡 ONE THING ━━━━━━━━━━━━━━━━━━━━━━━━━━
Your EAD renewal is 90 days out. Based on Fragomen’s avg processing
time from your last two renewals (45 days), initiate attorney contact
within 2 weeks to stay safe.
─ Chosen: URGENCY 4 (90d window) × IMPACT 5 (legal) × AGENCY 5 = 100 *(v1.2)*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
47 emails → 6 actionable · 💼 13 work meetings (6h15m). Next recommended: tomorrow evening.
━━ 🛡️ PII GUARD ━━━━━━━━━━━━━━━━━━━━━━━━━━ *(v1.4)*
Scanned: 47 emails, 3 state files | Redacted: 2 | False positives: 0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━ 🎯 CALIBRATION ━━━━━━━━━━━━━━━━━━━━━━━━ *(v1.4 — post-briefing)*
1. Was anything in today's briefing surprising or wrong?
2. Is there anything on your mind that Artha didn't surface?
(Skip: just continue — no response needed)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 4.2 Briefing Design Rules

1. **Critical/Urgent → Top.** Always. No exceptions. Even if the rest of the briefing is empty.

2. **Empty sections are stated, not hidden.** If there are no critical alerts: `(none)`. If there are no calendar events today: `No events today.` Hiding empty sections makes the user wonder "did it miss something?" Stating emptiness builds trust.

3. **Domain items use prose, not tables.** Tables work for structured data (goals, accounts). Domain updates are prose — one line per item with context. "Parth: AP Language essay returned (B+)" is faster to scan than a 5-column table.

4. **Goal Pulse uses fixed-width alignment.** Goal names left-aligned, status right-aligned, trend far-right. The eye tracks the status column. If everything says "On track," the user is done in 2 seconds.

5. **ONE THING is never generic.** It is always specific, always actionable, always contextualized. "Stay on top of your finances" would never appear. "Your EAD renewal is 90 days out — initiate attorney contact within 2 weeks" would.

6. **Footer shows signal-to-noise ratio.** "47 emails → 6 actionable" — this single metric communicates Artha's filtering value. Over time, the user sees: Artha consistently reduces 50+ signals to <10 actionable items.

7. **Separator lines use box-drawing characters.** `━━━` for major sections, no characters for domain sub-items. The visual rhythm: heavy bar = new section, indent = sub-item, blank line = breathing room.

### 4.3 Quiet Day Briefing

When there's nothing notable, Artha says so explicitly and concisely:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · Thursday, March 8, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Last catch-up: 18 hrs ago | Emails: 12 | Period: since yesterday 9:45 PM

No alerts. No action items.

📅 Today: No events.
🎯 Goals: All on track. No changes.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
12 emails → 0 actionable. Quiet day.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

This brevity is a feature. The user learns that short briefings mean everything is fine. Trust is built through silence as much as through signal.

### 4.4 Crisis Day Briefing

When multiple high-severity items co-occur, Artha restructures:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · Monday, March 10, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠ 3 items need your attention.

━━ 🔴 CRITICAL ━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. [Immigration] RFE received on I-485. Fragomen requests
   documents within 30 days. See attorney email for details.
   → Recommended: /domain immigration for full context.

━━ 🟠 URGENT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2. [Finance] Chase fraud alert: Unrecognized $892 charge.
   → Recommended: Call Chase immediately. Card may be compromised.
3. [Kids] Parth marked absent today — unplanned.
   → Recommended: Confirm with Parth. May be school error.

[... rest of briefing follows standard format ...]
```

**Crisis design rules:**
- Lead with a count: "3 items need your attention" — sets cognitive budget.
- Number the critical items — enables reference in follow-up chat ("tell me more about #1").
- Include a recommended next action for each critical item.
- Keep the rest of the briefing in standard format — crisis doesn't eliminate routine.

### 4.5 Digest Mode Briefing *(v1.2 — Workstream H)*

When >48 hours have elapsed since the last catch-up, the briefing switches to digest format. Temporal ordering replaces domain ordering — helping the user reconstruct what happened during the gap.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA DIGEST · March 7–9, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Last catch-up: March 5, 7:00 PM | Gap: 48 hrs | Emails: 142

━━ ⚠️ WHAT YOU MISSED (2 days) ━━━━━━━━━━━━━━━

March 6 (Thursday)
  🔴 [Immigration] Fragomen sent updated EAD timeline — review required
  🟠 [Finance] PSE bill $312 due March 20
  📦 3 Amazon deliveries confirmed

March 7 (Friday)
  🟡 [Kids] Parth: quiz grade posted (91%)
  📅 Weekend: soccer tournament Saturday 9am
  📦 8 marketing emails suppressed (subject lines only)

━━ 📋 CONSOLIDATED ACTIONS ━━━━━━━━━━━━━━━━━━━
1. Review Fragomen EAD timeline email [P0]
2. Pay PSE bill by March 20 [P1]
3. Confirm soccer tournament logistics [P1]

━━ 🎯 GOAL PULSE ━━━━━━━━━━━━━━━━━━━━━━━━
[Same format as standard briefing]

━━ 💡 ONE THING ━━━━━━━━━━━━━━━━━━━━━━━━━━
[Highest URGENCY×IMPACT×AGENCY from gap period]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
142 emails → 3 actionable (12 marketing suppressed).
Gap: 48 hrs. Consider daily cadence for best coverage.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Digest mode design rules:**
1. **Group by day, not by domain.** Temporal ordering helps the user mentally reconstruct the gap period.
2. **Priority-tier filtering:** Only Critical and Urgent items get individual lines. FYI items are counted: "8 marketing emails suppressed."
3. **Consolidated actions:** Deduplicated across the gap period. Same format as standard briefing action proposals.
4. **Title says "DIGEST"** — the user immediately knows this is a catch-up-after-absence format, not a standard briefing. Different header signals a different reading strategy.
5. **Footer includes cadence nudge.** Gently suggests daily frequency after a gap, without being preachy.

### 4.6 Flash Briefing *(v1.3 — Workstream O)*

Ultra-compressed format for ≤30 second reading time. Triggered by "quick update", `/catch-up flash`, or auto-selected when <4 hours since last catch-up (see Tech Spec §7.13).

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ARTHA FLASH — March 7, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 None
🟠 EAD renewal 90d out — contact Fragomen within 2 weeks
📅 9am 💼Teams standup | 3:30 PM ortho (Parth) | 6:00 PM soccer (Trisha)
📊 13 work meetings (6h15m) — heavy day                    *(v1.5)*
⚠️ IF YOU DON'T: Start EAD renewal → 45-day processing
   gap before Nov 15 expiry. Impact: work authorization.

4/5 goals ✔ | 10% signal | 47 emails → 6 actions · 💼 13 mtgs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Flash briefing design rules:**
1. **Maximum 8 content lines** (excluding header/footer rules). If there are zero critical/urgent items, flash briefing is 4 lines.
2. **No domain sections.** Alerts are listed without domain grouping — the emoji severity is sufficient.
3. **Calendar on one line.** Pipe-separated, today only, first-name family member in parentheses.
4. **Consequence forecast: one line.** The single highest-priority "IF YOU DON'T" item. See §4.7.
5. **Footer is a single summary line.** Goals count + signal ratio + volume in compact format.
6. **No greeting, no sign-off.** The header IS the greeting. The footer IS the close.
7. **Always suggest deep mode.** If user says "tell me more" after flash, seamlessly expand to standard briefing.

### 4.7 Consequence Forecast Display *(v1.3 — Workstream K)*

"IF YOU DON'T" alerts surface downstream consequences of inaction. Appears in all briefing levels (see Tech Spec §7.10).

**Standard briefing format (2–3 consequence items):**
```
## ⚠️ Consequences of Inaction
- **Initiate EAD renewal within 2 weeks** → 45-day processing time
  creates gap before Nov 15 expiry. During gap: cannot work legally.
  Impact chain: employment → income → mortgage → all downstream.
- **Pay PSE bill by March 20** → $25 late fee + potential service flag.
  Auto-pay not enabled for this account.
```

**Flash briefing format (1 consequence item, condensed):**
```
⚠️ IF YOU DON'T: Start EAD renewal → 45-day processing
   gap before Nov 15 expiry. Impact: work authorization.
```

**Design rules:**
1. Only for items with >70% confidence in consequence chain. No speculation.
2. Standard briefing: up to 3 items, ranked by URGENCY×IMPACT×AGENCY score.
3. Flash briefing: exactly 1 item (highest score), max 2 lines.
4. Each consequence ends with the most impactful downstream effect, not the first-order effect.
5. Weekly summary includes "Consequences Averted" — items acted on in time (positive reinforcement).

---

### 4.8 Weekend Planner Display *(v1.4 — F8.7)*

Automatically appended to Friday afternoon catch-ups (after 12 PM). Also available on-demand: "plan my weekend."

```
── 🏖️ WEEKEND PLANNER — March 8–9 ─────────────

SATURDAY
  9:00 AM   Trisha soccer tournament (confirmed)
  1–3 PM    💡 Open window → Review Parth’s college list (45 min, open 12d)
  6:00 PM   Family dinner

SUNDAY
  Morning   💡 Power Half Hour: pay PSE bill, review insurance, update goals
  4:00 PM   Parth SAT prep

⚠️ WEEKEND DEADLINES
  • PSE bill due Monday → pay this weekend
  • College app: rec letters milestone in 10 days

📊 Weekend density: 4 events | 2 deadlines | 2 suggested tasks
```

**Design rules:**
- Show only committed events + 2–3 suggested tasks (no overloading)
- Suggestions come from open_items.md with effort ≤30 minutes
- Include family context (who’s available when)
- Never suggest tasks during committed family time

---

## 5. Weekly Summary — Output Design

### 5.1 Weekly Summary Template

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA WEEKLY · March 3–9, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Catch-ups: 4 | Emails: 312 | Alerts: 3 (0🔴 2🟠 1🟡)
Actions proposed: 2 | Approved: 1 | Declined: 1

━━ WEEK AT A GLANCE ━━━━━━━━━━━━━━━━━━━━━━
• Visa Bulletin moved — EB-2 India closer. Details below.
• Household spending within budget. PSE bill paid.
• Parth grades stable. SAT 5 days away.
• No insurance or vehicle actions needed.

━━ KIDS THIS WEEK ━━━━━━━━━━━━━━━━━━━━━━━━

Parth (11th — Tesla STEM)
  Grades: B+ AP Language (stable), A- AP Physics (↑)
  GPA: 3.7 (on track for 3.8 goal)
  SAT: March 13 — 5 days. Prep log: 12 hrs this week.
  Activities: Econ Club met Tuesday.

Trisha (7th — Inglewood MS)
  Grades: A Math (stable), A- Science
  Activities: Soccer practice 3x this week.
  Notes: Currently reading *Just Mercy* Ch 4–9 for AP Lang.

━━ FINANCE THIS WEEK ━━━━━━━━━━━━━━━━━━━━━
Spending: $X,XXX (budget: $X,XXX) — within target.
Bills paid: Mortgage (auto), Chase CC (auto).
Bills upcoming: PSE $247 due 3/20 (not auto-pay).
Anomalies: None.

━━ IMMIGRATION UPDATE ━━━━━━━━━━━━━━━━━━━━
Visa Bulletin: EB-2 India PD moved to 2019-01-15 (+15 days).
Your PD: 2019-04-15. Est. 20 months to current (trailing avg).
EAD renewal window: 87 days. Attorney contact by March 24.
H-1B extension: File by April 2027 (13 months).

━━ GOAL SCORECARD ━━━━━━━━━━━━━━━━━━━━━━━━
                              This Week    YTD      Status
Net Worth 2026 Target         +1.2%        +2.1%    → On Track
Monthly Amazon < $XXX         $XXX         78%      ⚠ At Risk
Parth GPA ≥ 3.8               3.7          3.7      → On Track
Quality family time ≥ 10h     8.5h         80%      → On Track
ByteByteGo course by Q2       22%          22%      ⚠ Behind
Exercise 4x/week              2.4x avg     60%      → On Track
Work-life balance             45%          45%      🔴 Behind

━━ COMING UP NEXT WEEK ━━━━━━━━━━━━━━━━━━━
1. Parth SAT — March 13 (Thursday). Good luck prep.
2. PSE bill $247 due March 20.
3. Spring pictures — March 12 (both schools).
4. Econ Club competition deadline — check with Parth.
5. ByteByteGo: Need 2.5 hrs to stay on track for Q2.

━━ 🤝 RELATIONSHIP HEALTH ━━━━━━━━━━━━━━━━━━ *(v1.2)*
Close family:  8/8 contacted within threshold  ✔
Close friends: 3/5 within threshold  ⚠ Amit (45d), Priya (60d)
Reciprocity:   Meera — 3 events attended, 0 reciprocated
Upcoming:      Rahul birthday (Mar 12), Holi (Mar 14)

━━ ⚡ LEADING INDICATOR ALERTS ━━━━━━━━━━━━━ *(v1.2)*
⚠ Parth assignment completion dropped 15% → GPA impact in 2–3 wks
✔ Savings rate on track (18.2%, target 20%)
✔ Immigration response time stable (3d avg)

━━ 📊 ACCURACY PULSE ━━━━━━━━━━━━━━━━━━━━━ *(v1.2)*
Actions: 8 proposed → 6 accepted (75%) / 1 declined / 1 deferred
Corrections: 2 (Finance: PSE amount $312 vs $312.47)
Alerts dismissed: 0
Domain accuracy: Immigration 100% | Finance 95% | Kids 90%

━━ ARTHA OBSERVATIONS ━━━━━━━━━━━━━━━━━━━━
1. Work-life balance has been 🔴 Behind for 3 consecutive weeks.
   Four late-night work sessions detected (Tue, Thu pattern).
   This is now a trend, not an outlier. Consider discussing with
   manager or blocking 6 PM hard cutoff.

2. Parth's AP Language grade has been stable at B+ for 4 weeks.
   The seminar format (*Just Mercy*) may need different study
   strategies than standard coursework. Worth a check-in.

3. EAD renewal + Parth's SAT week overlap. High-stress week
   ahead. Consider deferring non-urgent tasks.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 5.2 Weekly Summary Design Rules

1. **"Week at a Glance" is the executive summary.** 4–5 bullet points covering the entire week. If the user reads nothing else, they get the picture.

2. **Kids section comes before Finance.** Unlike the daily briefing domain priority order (immigration first), the weekly summary leads with Kids because the weekly format is the natural touchpoint for academic review. Parents check grades weekly.

3. **Goal Scorecard is the centerpiece.** The scorecard is the most data-dense section — it requires fixed-width alignment. Three columns: This Week (delta), YTD (cumulative), Status (emoji + text). The user should be able to scan all goals in 5 seconds.

4. **Coming Up is exactly 5 items.** Not 3, not 10. Five. This forces Artha to rank and omit. If there are fewer than 5, pad with lower-priority items. If there are more, only the top 5 appear.

5. **Artha Observations are numbered and specific.** Each observation is 2–4 lines: pattern detected, data evidence, suggested response. These are the "insight engine" outputs (PRD F15.11) — they must feel genuinely intelligent, not templated.

---

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
  CSPA (Parth): Age 16.4, age-out at 21. No immediate concern.
  CSPA (Trisha): Age 13.1. No concern.
  
  Last attorney contact: March 5 (Fragomen). No open items.
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
User: "What's happening with Parth?"
Artha: Parth (11th, Tesla STEM). This week:
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

Parth GPA ≥ 3.8
  Lagging: 3.7 current GPA                  → On Track
  Leading: Assignment completion 95%         ✔ Healthy
  Leading: Missing assignments 0             ✔ Healthy
  Leading: ⚠ Completion rate -15% this week  ⚠ Early warning

Immigration Readiness
  Lagging: EAD renewal 90 days out           ⚠ Action due
  Leading: Fragomen response time 3d avg     ✔ Normal
  Leading: Document checklist 60% ready      ⚠ Gap

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ 1 divergence alert: Parth assignment completion.
  "Lagging (GPA) still on track, but leading dropped.
  If trend continues, expect GPA impact in 2–3 weeks."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Design rules for leading indicators:**
1. **Always show lagging first, then leading.** Anchors the user in current reality before showing predictive signals.
2. **Divergence alerts appear at the bottom.** When a lagging indicator says "on track" but a leading indicator says "warning," Artha explains the discrepancy in plain language.
3. **Color/emoji coding:** ✔ healthy, ⚠ early warning, 🔴 critical divergence. Same taxonomy as alert system.
4. **Accessible via `/goals leading` or shown automatically** in the weekly Goal Scorecard when a divergence is detected.

### 8.6 Coaching Engine Interaction Design *(v1.3 — Workstream K)*

Artha evolves from passive tracker to active coach for goals. The coaching engine uses accountability patterns, obstacle anticipation, and nudge formatting from `memory.md` → `Coaching Preferences` (Tech Spec §4.7).

**Accountability nudge format (question-style, default):**

```
━━ 🎯 COACHING CHECK-IN ━━━━━━━━━━━━━━━━━

EAD Renewal (Immigration Readiness goal)
  Status: No attorney contact initiated.
  Deadline: 90 days out. Fragomen avg processing: 45 days.

  → What's blocking this — waiting on documents, or
    just haven't scheduled the call?
    [documents] [schedule] [defer 1 week] [not now]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Obstacle anticipation (surfaces before user encounters blockers):**

```
━━ 🛡️ PREDICTED BLOCKER ━━━━━━━━━━━━━━━━━

Parth SAT Prep (March 13)
  Based on past pattern: study schedule drops 40% in week
  before tests when sports events overlap.
  
  → This week: Soccer tournament Saturday + practice Wed/Thu.
    Study time may compress. Suggest: block 2 hours Tuesday
    and Thursday evening?
    [add blocks] [skip] [modify]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Celebration (milestone-level, per user preference):**

```
━━ 🎉 MILESTONE ━━━━━━━━━━━━━━━━━━━━━━━━━

Net Worth Trajectory: $XXX,XXX threshold crossed.
  YTD growth: +4.2%. Savings rate: 19.8%.
  
  This puts you ahead of schedule by ~2 months.
  Keep it up.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Coaching design rules:**
1. **Coaching check-ins appear after the briefing,** alongside action proposals (UX-OD-4). Never interrupt the briefing.
2. **Maximum 1 coaching nudge per catch-up.** Rotating across goals prevents nagging.
3. **Nudge format is configurable:** question (default), statement ("You haven't contacted Fragomen"), or metric ("Days since last attorney contact: 14").
4. **Obstacle anticipation is opt-in** (controlled by `obstacle_anticipation: true` in Coaching Preferences).
5. **Celebrations only at milestones** (not every positive data point). Configurable via `celebration_threshold`.
6. **Dismissal is always available** and never penalized. "Not now" suppresses that nudge for 7 days.

---

### 8.7 Goal Sprint Display *(v1.4 — F13.17)*

When a goal sprint is active, the Goal Pulse section includes sprint progress:

```
🎯 GOAL SPRINT — "March Savings Push" (12 days remaining)
──────────────────────────────────────────────

Target:    Save $1,500 by March 31
Progress:  $875 / $1,500 (58%) ██████░░░░
Pace:      $73/day needed (was $62/day at start)
Status:    ⚠️ Behind pace — reduce discretionary spending by $11/day

Sprint validation: target_value is mandatory, default_target calibrated
from domain baseline. Missing target → Artha prompts: "What does success
look like for this goal? Give me a number."
```

**Sprint rules:**
- Maximum 1 active sprint at a time
- Sprint duration: 7–30 days
- Sprint must have a quantifiable target_value
- Behind-pace nudge appears in daily briefing; on-pace gets checkmark only

### 8.8 College Application Countdown Display *(v1.4 — F4.11, P0)*

Appears in Kids section of briefing when any milestone is ≤90 days away:

```
🎓 PARTH — COLLEGE APPLICATION COUNTDOWN
──────────────────────────────────────────────

UPCOMING MILESTONES
  🔴  SAT final attempt           28 days   [needs scheduling]
  🟠  College list finalized       45 days   [in progress — 8/12 schools]
  🟡  Common App essays draft      67 days   [not started]
  ✅  Rec letters requested       85 days   [on track — 2/3 asked]

NEXT ACTION: Schedule SAT date before registration closes (14 days)

📊 Overall: 1/7 milestones complete | 2 at risk | Application year: 2026–2027
```

**Design rules:**
- Show only milestones ≤90 days away (full list via `/domain kids`)
- Color-code by urgency: 🔴 ≤14d, 🟠 ≤30d, 🟡 ≤90d, ✅ complete
- Always show NEXT ACTION — the single most urgent step
- Application year displayed as context anchor
- Appears in daily briefing during application season (Aug–Mar of senior year)

---

## 9. Action Proposals — Approval UX

### 9.1 Proposal Display Format

Every action proposal follows the same visual structure:

```
───────────────────────────────────────────
📋 ACTION #1 of 3
───────────────────────────────────────────
Send email to Fragomen — EAD renewal inquiry

To:       attorney@fragomen.com
Subject:  EAD Renewal Timeline — Mishra Family
Body:
  Hi [attorney name],

  The current EAD for Archana expires November 15, 2026.
  Based on our previous renewal timelines (~45 days processing),
  we should begin the renewal process by early June.

  Could you confirm the current recommended filing timeline
  and any documents we should prepare in advance?

  Best regards,
  Ved

Trust:    Level 1 (Advisor)
Friction: 🟠 Standard                           *(v1.2)*
Source:   Immigration domain — 90-day EAD renewal window

[approve] [edit] [skip] [skip all]
───────────────────────────────────────────
```

### 9.2 Approval Interaction Patterns

**Single action approval:**
```
User: "approve"
Artha: ✅ Sent. Logged to audit.md.
       Email delivered to attorney@fragomen.com at 8:47 PM.
```

**Edit before approval:**
```
User: "edit — change the subject to include our case number"
Artha: Updated subject: "EAD Renewal Timeline — Mishra Family — Case #IOE-XXXXXXXXX"
       
       [approve] [edit more] [skip]
```

**Batch approval for low-risk items:**
```
───────────────────────────────────────────
📋 ACTIONS #2–3 of 3 (batch)
───────────────────────────────────────────
2. Add "PSE bill due" to calendar — March 20
3. Add "Spring pictures" to calendar — March 12 (both schools)

These are calendar additions only (no messages sent).
Friction: 🟢 Low (batch-approvable)               *(v1.2)*
[approve all] [review individually] [skip all]
───────────────────────────────────────────
```

### 9.3 Action Proposal Sequencing

Actions are proposed in a deliberate order:

1. **Critical/urgent actions first** — immigration, finance.
2. **Communication actions** — emails, WhatsApp, grouped by recipient.
3. **Calendar actions** — batched together at the end.
4. **Informational actions** — visual generation, state updates.
5. **Teams Join actions** *(v1.5)* — imminent work meetings (≤15 min away):

```
───────────────────────────────────────────
📋 ACTION #5 of 5 — Teams Join *(v1.5)*
───────────────────────────────────────────
💼 "xInfraSWPM: XPF Weekly" starts in 12 minutes.
→ Join via Teams [open link]

Friction: 🟢 Low (link open only)
[open] [skip]
───────────────────────────────────────────
```

After the last action is processed, Artha summarizes:

```
Actions complete: 2 approved, 1 skipped.
  ✅ Email sent to Fragomen
  ✅ 2 calendar events added
  ⏭ Skipped: WhatsApp birthday greeting (user deferred)
```

### 9.4 WhatsApp Action UX

WhatsApp is the most distinctive action pattern because the OS enforces the final human gate:

```
───────────────────────────────────────────
📋 ACTION #4
───────────────────────────────────────────
WhatsApp message to Rahul — Birthday greeting

To:       Rahul (+1-XXX-XXX-XXXX)
Message:  Happy Birthday, Rahul! 🎂 Hope you have an amazing day.
          Wishing you and your family all the best. — Ved & family

Note: This will open WhatsApp with the message pre-filled.
      You'll need to tap Send in WhatsApp.

[open whatsapp] [edit message] [skip]
───────────────────────────────────────────
```

If approved:
```
Artha: Opening WhatsApp with message pre-filled for Rahul.
       ↳ WhatsApp opened. Tap Send when ready.
       (I can't confirm delivery — WhatsApp is outside my view.)
```

---

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
/cost            Monthly API cost vs. budget                 5 seconds
/health          System integrity — file checks, CLI health  10 seconds
/items           Open action items from open_items.md        5 seconds
                 Optional filters: /items kids
                                   /items P0
                                   /items overdue
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

### 10.2 /status Output Design

```
━━ ARTHA STATUS ━━━━━━━━━━━━━━━━━━━━━━━━━━
Last catch-up:  6 hours ago (today 2:15 PM)
Emails pending: ~30 (estimated)
Active alerts:  1 🟠 (EAD renewal — 87 days)

Domain freshness:
  Immigration  ✅ 6 hrs    Finance     ✅ 6 hrs
  Kids         ✅ 6 hrs    Calendar    ✅ 6 hrs
  Home         ✅ 6 hrs    Health      🟡 3 days
  Travel       ✅ 6 hrs    Goals       ✅ 6 hrs

MCP Tools:  Gmail ✅  Calendar ✅
CLIs:       Gemini ✅  Copilot ✅

Monthly cost: $18.50 / $50 budget (37%)

Everything nominal. Run /catch-up for latest.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.3 /domain Output Design

The `/domain` command provides a deep-dive into a single domain — more detail than the briefing, less than reading the state file directly.

```
User: /domain immigration

━━ IMMIGRATION · Deep Dive ━━━━━━━━━━━━━━━
Last updated: 6 hours ago

Family Status:
  Vedprakash  H-1B    Active    Expires 2027-10-15
  Archana     H-4     Active    Expires 2027-10-15
  Parth       H-4     Active    Expires 2027-10-15
  Trisha      H-4     Active    Expires 2027-10-15

EAD:          Archana    Active    Expires 2026-11-15
I-140:        Vedprakash Approved  EB-2
Priority Date: 2019-04-15

Active Deadlines:
  🟠 EAD Renewal     File by 2026-06-03 (87 days)
  🟡 H-1B Extension  File by 2027-04-15 (13 months)

Visa Bulletin (EB-2 India):
  Month       Date          Movement
  Mar 2026    2019-01-15    +15 days
  Feb 2026    2018-12-15    +30 days
  Jan 2026    2018-11-15    +45 days

  Trailing avg: +30 days/month. Est. 20 months to your PD.

CSPA Age-Out:
  Parth   Age 16.4   Out at 21   ~54 months   No concern
  Trisha  Age 13.1   Out at 21   ~95 months   No concern

Fragomen Contact:
  Last: March 5. No open items.
  Next expected: Q2 (H-1B extension paperwork).

Recent Activity (last 30 days):
  03/05 Fragomen — H-1B ext timeline confirmed for Q2
  03/01 Visa Bulletin — EB-2 India +15 days
  02/15 Fragomen — EAD renewal docs checklist sent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.4 /goals Output Design

```
User: /goals

━━ ARTHA GOAL SCORECARD ━━━━━━━━━━━━━━━━━━

FINANCIAL
  Net Worth Target 2026       ██████░░░░  62%  → On Track
  Monthly Amazon < $XXX       ████████░░  78%  ⚠ At Risk

FAMILY
  Parth GPA ≥ 3.8             █████░░░░░  54%  → On Track
  Quality family time ≥ 10h   ████████░░  80%  → On Track

LEARNING
  12 books in 2026            ███░░░░░░░  25%  → On Track (3/12)
  ByteByteGo course by Q2     ██░░░░░░░░  22%  ⚠ Behind

IMMIGRATION
  EAD Renewal Filed           ░░░░░░░░░░   0%  Pending (deadline: June)

HEALTH
  Exercise 4x/week            ██████░░░░  60%  → On Track

WORK-LIFE
  Protected time ≥ 20h/week   ████░░░░░░  45%  🔴 Behind

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
9 goals tracked. 6 on track, 2 at risk, 1 behind.
Use "/domain goals" for full detail and forecasts.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.5 /health Output Design

```
User: /health

━━ ARTHA SYSTEM HEALTH ━━━━━━━━━━━━━━━━━━━

File Integrity:
  Artha.md         ✅ Present    CLAUDE.md loader ✅
  registry.md      ✅ Present    17 components registered
  vault.sh         ✅ Present    pii_guard.sh ✅  safe_cli.sh ✅

State Files (18 expected):
  Found: 18/18     Schema version: all v1

Encrypted Tier:
  immigration.md.age ✅  finance.md.age ✅  health.md.age ✅
  insurance.md.age   ✅  estate.md.age  ✅  audit.md.age  ✅
  contacts.md.age    ✅

MCP Connections:
  Gmail MCP      ✅ Connected   Last success: 6 hrs ago
  Calendar MCP   ✅ Connected   Last success: 6 hrs ago

CLI Availability:
  claude         ✅ v4.2.1
  gemini         ✅ v1.3.0      Last used: 2 days ago
  copilot        ✅ v1.1.0      Last used: 5 days ago

Context Window:
  Last catch-up: 112K tokens (headroom: 44%)
  30-day avg:    98K tokens
  Peak:          145K tokens

OneDrive Sync:
  Last sync:     2 minutes ago
  Selective sync: active (state/*.md excluded from sync)

No issues detected. System healthy.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.5 /items Output Design

Reads `state/open_items.md` and renders open action items grouped by priority. Accepts optional filters.

```
User: /items

━━ OPEN ITEMS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4 open items  |  1 overdue  |  Last sync: 6 hrs ago

🔴 OVERDUE
  OI-005  [kids]        Parth SAT transport (Mar 13) — missed deadline
                        → Add to Microsoft To Do? [yes / no]

P0 — Today
  OI-004  [immigration] Sign I-485 checklist via DocuSign (due Mar 15)
  OI-006  [finance]     Confirm wire transfer received by escrow

P1 — This Week
  OI-003  [home]        Schedule HVAC filter replacement (due Mar 20)

Nothing at P2.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Filter behavior:**
- `/items kids` — shows only items for source_domain: kids
- `/items P0` — shows only P0-priority items
- `/items overdue` — shows only items past deadline with status: open
- `/items done` — shows the 5 most recently resolved items

### 10.6 /decisions Output Design *(v1.2 — Workstream C)*

```
User: /decisions

━━ DECISION LOG ━━━━━━━━━━━━━━━━━━━━━━━━━━━
2 active decisions | 3 resolved (last 90 days)

ACTIVE
  DEC-001  Feb 15  Wait on refinance until rates < 5.5%
           Domains: finance, home
           Review trigger: rates reach 5.5% OR 6 months
           Status: monitoring (current rate 5.8%)

  DEC-002  Mar 1   College savings strategy: 529 vs brokerage
           Domains: finance, kids
           Review trigger: Parth college decision (2027)
           Status: analyzing

RECENTLY RESOLVED
  DEC-000  Jan 10  Tesla STEM for Parth senior year → enrolled ✔

Use "/decisions DEC-001" for full alternatives analysis.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.7 /scenarios Output Design *(v1.2 — Workstream D)*

```
User: /scenarios

━━ SCENARIO ANALYSIS ━━━━━━━━━━━━━━━━━━━━━━
1 active scenario | 3 templates available

ACTIVE
  SCN-001  I-485 approval timeline (created Mar 1)
           Question: "What if approved in 6 months vs 18 months?"
           Domains: immigration, finance, goals
           Last evaluated: Mar 1

TEMPLATES
  • refinance_analysis  "What if we refinance at X%?"
  • college_cost        "What if Parth goes private vs. in-state?"
  • job_change          "What if Ved changes employers?"

Run a scenario: "/scenarios SCN-001" or "/scenarios new"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Scenario detail view (when user runs `/scenarios SCN-001`):**
```
━━ SCENARIO: I-485 Timeline ━━━━━━━━━━━━━━━

What if approved in 6 months?          What if 18 months?
────────────────────────────────────────────────────────
Immigration:                           Immigration:
  No EAD/AP renewal needed               Must file EAD renewal (Aug 26)
  No H-1B extension needed               H-1B extension (Apr 27)

Finance:                               Finance:
  Save ~$5K attorney fees                Budget $5K for renewals
  Employer flexibility                   Employer lock continues

Goals:                                 Goals:
  Career flex Q3 2026                    Career flex deferred to Q1 2028

━━ RECOMMENDATION ━━━━━━━━━━━━━━━━━━━━━━━━━━
Plan for 18-month timeline (conservative). If approved sooner,
the saved costs become a positive surprise rather than a scramble.

[update scenario] [archive] [back to list]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.8 /relationships Output Design *(v1.2 — Workstream A)*

```
User: /relationships

━━ RELATIONSHIP PULSE ━━━━━━━━━━━━━━━━━━━━━━

🔴 RECONNECT NEEDED
  Suresh Uncle — 45 days (threshold: 30)
  College friend group — 90 days (threshold: 60)

📅 UPCOMING (7 days)
  Mar 12  Rahul birthday → [action proposal ready]
  Mar 14  Holi — temple community gathering
  Mar 15  Meera’s daughter’s recital (RSVP’d)

📊 COMMUNICATION HEALTH
  Outbound this month: 15 | Inbound: 12
  Reciprocity: ⚠ Meera (3 events, 0 reciprocated)

👥 GROUP STATUS
  Temple community    12 members   Last: Feb 20   Next: Holi Mar 14
  Microsoft colleagues 8 members   Last: Mar 1    Next: none scheduled

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.9 /bootstrap Output Design *(v1.3 — Workstream L)*

Guided state population interview. See Tech Spec §7.5 for full workflow.

```
User: /bootstrap

━━ BOOTSTRAP — STATE POPULATION ━━━━━━━━━━

These domains have never been populated:

  1. 🔴 Immigration (critical)     — 0/15 fields
  2. 🔴 Finance (critical)         — 1/20 fields
  3. 🟠 Insurance (high)           — 0/12 fields
  4. 🟠 Estate (critical)          — 0/8 fields
  5. 🟡 Health (high)              — 0/10 fields

Start with which domain? [1–5 / all / skip]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Interview interaction pattern:**
```
User: 1

━━ IMMIGRATION — Question 1 of 12 ━━━━━━━━

What is your current visa type?
(e.g., H-1B, L-1, F-1, Green Card)

> H-1B

Got it. I'll record: Current visa type → H-1B.
Correct? [Y / N / skip this field]

> Y

━━ IMMIGRATION — Question 2 of 12 ━━━━━━━━

What is your EB-2 India priority date?
(Format: YYYY-MM-DD, or "skip" if unknown)

> 2019-04-15

Got it. Priority date → 2019-04-15.
Correct? [Y / N / skip]
```

**Completion summary:**
```
━━ BOOTSTRAP COMPLETE — Immigration ━━━━━━

  Fields populated: 12/15 (3 skipped)
  Sensitivity: critical — file encrypted ✔
  
  Skipped fields:
    - CSPA age-out date (calculated after dependents populated)
    - Visa Bulletin history (will populate from email)
    - Attorney retainer amount (manual-entry-only per privacy rules)

  Continue to Finance? [Y / N]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Design rules:**
1. One question at a time. Never present a form or ask multiple questions.
2. Always confirm before writing. Show exactly what will be stored.
3. Progress indicator in header: "Question X of Y."
4. "Skip" is always available and never judged. Skipped fields remain as TODO.
5. Sensitive fields get a redaction reminder: "I'll store this as [REDACTED-PASSPORT-Ved]. I won't need the actual number."

### 10.10 /dashboard Output Design *(v1.3 — Workstream K)*

Life-at-a-glance snapshot from `state/dashboard.md` (Tech Spec §4.12).

```
User: /dashboard

━━ ARTHA DASHBOARD ━━━━━━━━━━━━━━━━━━━━━━━
Last catch-up: 23 hours ago

LIFE PULSE
  Immigration    ⚠️ EAD renewal 90d     🟠
  Finance        ✅ Bills current         🟡 PSE due 13d
  Kids           ✅ On track              —
  Health         ✅ On track              —
  Home           ✅ On track              —
  Goals          ⚠️ 4/5 on track         🟡

ACTIVE ALERTS (3)
  1. 🟠 EAD renewal — Fragomen contact needed (score: 80)
  2. 🟡 PSE bill $312 due Mar 20 (score: 60)
  3. 🟡 Parth SAT Mar 13 — transport needed (score: 50)

OPEN ITEMS: 2 P0 | 4 P1 | 1 P2 | 0 overdue

SYSTEM: 10% signal | green pressure | OAuth ✅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 10.11 /scorecard Output Design *(v1.3 — Workstream S)*

Weekly life scorecard from `dashboard.md` (Tech Spec §7.9).

```
User: /scorecard

━━ LIFE SCORECARD — Week of Mar 3–9 ━━━━━━

DIMENSION              SCORE   TREND  KEY METRIC
──────────────────── ─────── ─────── ─────────────────────
Financial Health       7.5/10   ↑     Savings 18%, bills OK
Immigration            6.0/10   →     EAD pending, no RFE
Kids & Education       8.0/10   →     GPA 3.7 / 3.9
Physical Health        ?/10     ?     No data (run /bootstrap)
Social & Relationships 6.0/10   ↓     2 reconnects overdue
Career & Goals         7.0/10   ↑     4/5 on track
Home & Operations      8.0/10   →     No maintenance backlog

──────────────────── ─────── ─────── ─────────────────────
COMPOSITE LIFE SCORE   7.1/10   ↑     vs 6.8 last week

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
? = insufficient data — populate via /bootstrap
↑ improved ≥0.5 | → stable | ↓ declined ≥0.5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 10.12 /diff Output Design *(v1.4 — F15.51)*

```
── STATE CHANGES SINCE LAST CATCH-UP ────────
Last catch-up: March 6, 7:15 PM (18 hours ago)

IMMIGRATION
  + New alert: EAD renewal 90 days out
  Δ Priority date: 2019-01-01 → 2019-01-15

FINANCE
  + New bill: PSE $247 due March 20
  Δ Checking balance: $X,XXX → $X,XXX

KIDS
  + Parth: AP Language essay returned (B+)
  Δ Parth cumulative grade: B → B+

NO CHANGES: Home, Travel, Health, Learning

📊 3 domains changed | 5 deltas | 4 domains unchanged
```

**Design rules:**
- Read-only operation — no email fetch, no state writes
- Show only domains with actual changes
- Use `+` for additions, `Δ` for modifications, `−` for removals
- "NO CHANGES" section prevents anxiety about missing domains
- Summary footer gives density at a glance

### 10.13 Monthly Retrospective Display *(v1.4 — F15.50)*

Generated first catch-up after the 1st of each month. Saved to summaries/.

```
── MONTHLY RETROSPECTIVE — February 2026 ────

MONTH AT A GLANCE
  Catch-ups: 22  |  Emails: 1,847  |  Alerts: 12 (1🔴 4🟠 3🟡 4🔵)
  Goals on track: 4/6  |  Decisions made: 3  |  Actions proposed: 18

WHAT HAPPENED
  [Domain-by-domain narrative of significant events]

DECISIONS MADE THIS MONTH
  DEC-005: Chose in-state university list for Parth (Feb 12)
  DEC-006: Deferred refinance until rates <5.5% (Feb 15)

GOAL PROGRESS
  Net worth    $XXX,XXX → $XXX,XXX  ↑ +2.1%
  Parth GPA    3.65 → 3.70          ↑
  Savings      On pace               →

ARTHA SELF-ASSESSMENT
  Accuracy: 94%  |  Signal:noise: 68%  |  PII incidents: 0

LOOKING AHEAD
  [Key items for next month]
```

### 10.14 Power Half Hour Display *(v1.4 — F15.48)*

Shown when user asks "what should I do now?" or during a suggested Power Half Hour window:

```
── ⚡ POWER HALF HOUR ─────────────────────

3 tasks, ~25 minutes total:

  1. Pay PSE bill ($247)              ~3 min   [Finance]
  2. Reply to Parth’s teacher email   ~5 min   [Kids]
  3. Review Fidelity quarterly stmt    ~15 min  [Finance]

💡 Completing all 3 clears your top-priority items for this week.

Ready? Say "go" and I’ll guide you through each one.
```

### 10.15 Teach Me Interaction Pattern *(v1.4 — F15.54)*

Triggered by user asking "explain..." or "teach me about..." or "what does ... mean?"

```
User: "teach me about my immigration status"

── 📖 TEACH ME: YOUR IMMIGRATION STATUS ────

WHAT YOU HAVE
  H-1B visa (employment-based, sponsored by Microsoft)
  Valid until: [date] | Extensions possible: yes

WHAT YOU’RE PURSUING
  I-140 (Immigrant Petition): [status]
  I-485 (Adjustment of Status): [status]
  Priority Date: April 2019 (EB-2 India)

WHAT THIS MEANS
  Your priority date determines when you can file I-485.
  The Visa Bulletin publishes monthly cutoffs. When the
  cutoff passes your date, you can file. Currently the
  EB-2 India cutoff is [date], which is [X months] from
  your priority date.

WHAT COULD CHANGE
  • Visa Bulletin advancement (monthly, unpredictable)
  • H-1B extension (if I-140 approved, unlimited extensions)
  • CSPA age-out risk for Parth (age [X], threshold 21)

Want to go deeper on any of these? Ask me about:
  • "CSPA age calculation for Parth"
  • "What happens if my H-1B expires?"
  • "Priority date history for EB-2 India"
```

**Design rules:**
- Pull ALL context from current state files — no generic explanations
- Structure: What You Have → What You’re Pursuing → What It Means → What Could Change
- End with 2–3 "go deeper" suggestions
- Never give legal/medical/financial advice — explain the user’s own situation
- Works for any domain: "teach me about my insurance coverage", "explain Parth’s GPA"

---

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
| Vedprakash | Full admin | Mac terminal + email + Claude.ai | All 18 domains |
| Archana | Shared family | Email briefing + Claude.ai Project | Finance, Immigration, Kids, Home, Travel, Health, Calendar, Insurance, Estate, Social, Goals (shared) |
| Parth | Personal academic | Claude.ai Project (filtered) | His academic data, activities, college prep, his own goals |
| Trisha | Age-appropriate | Claude.ai Project (filtered) | Her academic data, activities, her own goals |

### 13.2 Archana's Experience

Archana receives a filtered briefing via email — same structure, fewer domains. Her email subject line:

```
Artha (Family) · March 7 — 🟠 1 urgent · 4 items
```

The `(Family)` tag differentiates her briefing from the full briefing. Her version:
- Includes: Kids, Finance (summary-level), Immigration, Calendar, Health, Home.
- Excludes: Work-life boundary (irrelevant to her), Digital Life, Learning (Ved-specific).
- Sensitivity filtering still applies — no financial details in email.

Her Claude.ai Project contains state files for shared domains only and responds within those domain boundaries.

### 13.3 Kids' Experience (Phase 2)

Parth's Claude.ai Project contains:
- His grades, attendance, and activity data from `kids.md`.
- His SAT prep and college prep timeline.
- His personal goals (set by him via conversation).
- Does NOT include: family finances, immigration details, other family member data.

Trisha's Project is similar but with age-appropriate scope (7th grade level — no college prep, no financial awareness).

### 13.4 Family Briefing Variant

For the weekly summary, Artha can generate a family-oriented version suitable for a family meeting:

```
User: "Generate a family briefing for this week."

━━ ARTHA FAMILY BRIEFING · March 3–9 ━━━━━

PARTH
  Grades: B+ AP Language, A- AP Physics. GPA 3.7.
  SAT: March 13 — 5 days away!
  Econ Club: Competition deadline coming up.
  Action: Review AP Language essay strategies.

TRISHA
  Grades: A Math, A- Science. Strong week.
  Soccer: 3 practices this week.
  Reading: *Just Mercy* Ch 4–9 for AP Lang seminar.
  Action: None needed.

FAMILY CALENDAR NEXT WEEK
  Mon — Regular school day
  Tue — Econ Club (Parth)
  Wed — Regular school day
  Thu — SAT (Parth) · Spring pictures (both schools)
  Fri — Soccer practice (Trisha)
  Sat — Open
  Sun — Open

FAMILY GOALS
  Quality family time: 8.5h this week (target: 10h)
  → Saturday is open — family activity?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

This format deliberately excludes finances, immigration, and work-life data. It is designed to be shared verbally at dinner or projected on a screen.

---

## 14. Error & Recovery UX

### 14.1 Error Display Patterns

Errors in Artha are not crashes — they are partial data conditions. The UX must communicate what data the user has and what's missing.

**MCP connection failure:**
```
⚠ Gmail MCP connection failed (OAuth token expired).
  Impact: Email data is from last catch-up (23 hours ago).
  Calendar data is current.
  
  Proceeding with stale email data + fresh calendar.
  To fix: Run "refresh gmail token" or check OAuth credentials.
  
  [continue with stale data] [abort catch-up]
```

**PII filter failure (critical — halts catch-up):**
```
🔴 CATCH-UP HALTED

pii_guard.sh returned error code 1.
Reason: Script not found at ~/OneDrive/Artha/scripts/pii_guard.sh

Email data was fetched but NOT processed. No state files were updated.
Sensitive data may be in the terminal scrollback for this session.

To resolve: Verify pii_guard.sh exists and is executable (chmod +x).
Then re-run /catch-up.
```

**Partial domain failure:**
```
Catch-up complete with 1 warning:
  ⚠ Kids domain: Canvas email parsing failed for 2 emails.
     (Possible format change in Canvas notification template.)
     Skipped. See audit.md for details.
  
  All other domains processed normally.
  These 2 emails will be retried on next catch-up.
```

**Pre-flight gate failure (halts before any data fetch):**
```
⛔ PRE-FLIGHT FAILED

Check: Gmail OAuth token
Error: Access token expired — refresh attempt failed (HTTP 401).

Catch-up aborted before any data was fetched.
No state files were modified. No emails were read.

To fix: python3 scripts/setup_google_oauth.py
Then re-run /catch-up.
```

**Stale lock file auto-cleared:**
```
⚠ Stale lock from previous session detected (age: 47 min).
  Previous session exited uncleanly — lock not released.
  Auto-clearing /tmp/artha.lock and proceeding.
  (If a catch-up is actively running elsewhere, abort now.)
```

**API quota exceeded (aborts catch-up):**
```
⛔ CATCH-UP HALTED

Gmail API quota exceeded after 3 retries (HTTP 429).
Partial data was fetched — 23 of ~60 estimated emails processed.

No briefing generated. Delivering partial data would be misleading.
Logged to health-check.md.

Try again in 60 minutes, or reduce catch-up frequency in config.yaml.
```

**OAuth refresh failure during catch-up:**
```
⛔ Gmail authentication failed

Access token expired and automatic refresh failed.
This may indicate the refresh token was revoked or expired.

Action required:
  python3 scripts/setup_google_oauth.py

No email data was processed this session. Calendar data is unaffected.
```

**Net-negative write guard triggered *(v1.3)*:**
```
⚠ NET-NEGATIVE WRITE DETECTED — finance.md

This catch-up would REMOVE 8 of 23 data fields (35% reduction).
This usually means email parsing missed recurring data, not that
the data is gone.

  Fields at risk: chase_balance, pse_bill, mortgage_payment, ...

  [show full diff]  [write anyway]  [skip finance this session]
  ```

  **Skill failure (P0 — halts catch-up):**
  ```
  🔴 CATCH-UP HALTED

  Skill "uscis_status" failed.
  Reason: USCIS portal structure changed (ParserError).

  Impact: Immigration status cannot be verified with zero-latency.
  Catch-up halted to prevent stale immigration state.

  To resolve: Run "artha /skill test uscis_status" to debug.
  Then re-run /catch-up.
  ```

  **Skill failure (P1/P2 — warning only):**
  ```
  Catch-up complete with 1 warning:
  ⚠ King County Tax skill failed (HTTP 503).
     Impact: Property tax due date not refreshed.
     Briefing will use last known date from config.

  All other skills and domains normally.
  ```

  **WorkIQ fetch failure (P1 — non-blocking) *(v1.5)*:**
  ```
  Catch-up complete with 1 warning:
  ⚠ WorkIQ calendar fetch failed (auth expired).
     Impact: Work calendar not included in today's briefing.
     Personal calendar events are unaffected.

  💼 Work calendar unavailable — showing personal events only.
  To fix: npx workiq logout && retry on Windows laptop.
  ```

  **WorkIQ unavailable on Mac *(v1.5)*:**
  ```
  📅 TODAY
  • 3:30 PM — Parth orthodontist appointment
  • 6:00 PM — Trisha soccer practice pickup
  💼 12 work meetings detected via Windows laptop (titles unavailable on this device).
  ```

  **WorkIQ parse format change *(v1.5)*:**
  ```
  ⚠ WorkIQ returned text but 0 events parsed.
     Possible format change in WorkIQ output.
     Retried with explicit format request — still 0 events.
     Skipping work calendar this session. Logged to audit.md.
  ```

  ### 14.2 Skill Intervention UX

  When a skill requires a token refresh or manual login (Phase 2 APIs), Artha surfaces a proactive nudge in the pre-flight check or briefing footer.

  **Token Expiry Nudge:**
  ```
  ⚠ ACTION REQUIRED: Canvas API token expires in 2 days.
  To refresh: Run "python3 scripts/setup_canvas_api.py"
  ```

Recommendation: Skip finance and retry next catch-up.
If the problem persists, run /bootstrap finance to re-populate.
```

**Corrupt decrypt detected *(v1.3)*:**
```
⛔ DECRYPT INTEGRITY FAILURE — immigration.md

Decrypted file failed validation:
  • Missing YAML frontmatter (expected ---)
  • File size: 43 bytes (expected ~2,400 bytes based on backup)

Restored from pre-decrypt backup (created 2 seconds ago).
The .age file may be corrupted. Original .md preserved.

  To investigate: Check state/immigration.md.age manually.
  To re-populate: Run /bootstrap immigration
```

**Bootstrap state detected during catch-up *(v1.3)*:**
```
⚠ UNPOPULATED STATE FILE — immigration.md

This file still contains bootstrap placeholder data
(updated_by: bootstrap, last_updated: 2026-03-08).
Email-derived updates may be incomplete without baseline data.

  Proceeding with best-effort email extraction.
  For complete data: Run /bootstrap immigration after this catch-up.
```

**OAuth token expiry warning (proactive) *(v1.3)*:**
```
⚠ Gmail OAuth token expires in ~3 days.

  No action needed now — this is a heads-up.
  If it expires, run: python3 scripts/setup_google_oauth.py
  
  Logged to health-check.md for tracking.
```

### 14.2 Automated Testing & Validation UX

When running the automated test suite (via `/health` or manual command), Artha provides developer-centric feedback that maintains the system's "scannable before readable" principle (UX-2).

**Test Suite Execution (Success):**
```
━━ ARTHA TEST SUITE ━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ [Unit] pii_guard.sh (8 categories)
  ✓ [Unit] vault.py (encryption logic)
  ✓ [Integration] Vault round-trip (Mac)
  ✓ [Extraction] Immigration prompt (4 samples)
  ✓ [Extraction] Finance prompt (3 samples)
  ✓ [Integrity] Net-negative write guard

✅ 15/15 tests passed (0.42s)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Test Suite Execution (Failure):**
```
━━ ARTHA TEST SUITE ━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ [Unit] pii_guard.sh
  ⛔ [Unit] vault.py: stale lock handling failed
  ✓ [Integration] Vault round-trip
  ⚠ [Extraction] Immigration prompt: status mismatch
  ✓ [Extraction] Finance prompt

❌ 2 tests failed · 3 passed
   • vault.py: Expected lock auto-clear after 31m, but session blocked.
   • immigration: "RFE Received" extracted as "Status Change".

To fix: Check scripts/vault.py L114 and prompts/immigration.md extraction rules.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 14.3 Recovery Guidance Design

Every error message follows the pattern:

```
[What happened] → [What it means for you] → [How to fix it]
```

Never show stack traces, error codes, or technical jargon unless the user asks "show me the details." The default error experience is human-readable.
### 14.3 Intelligent Alerting UX

To minimize user fatigue, Artha distinguishes between **Status Confirmation** (informational) and **Status Change** (alerting).

**Rule:** Briefings always show data for enabled skills, but only add the **🔴/🟠 Alert** marker if `changed: true` in the skill cache.

**Output (No Change):**
```
### Immigration
• USCIS Status: Case Was Received (since 2026-03-10)
• Visa Bulletin (EB-2): 01JAN22 (no change)
```

**Output (Status Change):**
```
━━ 🔴 CRITICAL ━━━━━━━━━━━━━━━━━━━━━━━━━━━
• [Immigration] USCIS Status Changed: Case Was Approved (IOE0123456789)
```

### 14.4 Weather Concierge UX

When an outdoor Open Item triggers the NOAA skill, the result is surfaced as a "go/no-go" unblocking recommendation.

**Concierge Alert:**
```
━━ 🟠 URGENT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• [Home] Mailbox Peak unblocked: Summit weather looks clear for Saturday.
   Forecast: 42°F, sunny, 5mph wind. (Conditions: GO)
```

  Chase balance: $XX,XXX (as of March 4)
  Upcoming bills: PSE $247 due March 20
```

The staleness indicator appears only when data is >24 hours old. Fresh data has no annotation — the absence of staleness markers means everything is current.

---

## 15. Onboarding & First-Run Experience

### 15.1 Bootstrap Sequence

The very first Artha session follows a guided sequence. Unlike later sessions where Artha waits silently for input, the first session initiates a structured conversation:

> **v1.3 Note:** The `/bootstrap` command (§10.9) can also be run at any time to populate or re-populate state files. The first-run sequence below covers initial setup; `/bootstrap` handles ongoing state population with the same guided interview UX.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Welcome to Artha.

I'm your personal intelligence system. I'll manage your family's
immigration, finances, kids' academics, and 15 other life domains
from this terminal. My job is to replace inbox scanning with a
daily 3-minute briefing.

Let's get set up.

━━ SETUP CHECKLIST ━━━━━━━━━━━━━━━━━━━━━━━

1. Directory structure
   Creating ~/OneDrive/Artha/ with prompts/, state/, briefings/,
   summaries/, config/, scripts/, visuals/...
   ✅ Done.

2. Gmail MCP
   I need OAuth credentials to read your email.
   Follow the setup guide: [link to tech spec §3.1]
   Status: ⏳ Waiting for configuration...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 15.2 First Catch-Up Experience

The first catch-up is special — it processes a potentially large email backlog and sets the baseline:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · First Catch-Up
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This is our first run. I'm processing the last 7 days of email
to build your initial world model.

Processing... (this may take 3–5 minutes for the first run)

[progress indicator: domain by domain]
  ✅ Immigration — 3 emails processed, 2 deadlines found
  ✅ Finance — 12 emails processed, 3 bills found
  ✅ Kids — 28 emails processed (school noise filtered)
  ⏳ Home — processing 4 emails...
  ⏳ Travel — processing 2 emails...
  ...

Initial world model built. 67 emails → 12 actionable items.

Below is your first briefing. It may be longer than usual
as I establish baselines for all domains.

[... standard briefing format follows ...]

━━ FIRST-RUN NOTES ━━━━━━━━━━━━━━━━━━━━━━━
• Some data may be incomplete (I only see 7 days of email).
• For immigration: manually verify the deadlines I found.
• For finance: confirm the account list matches your actual accounts.
• Correct me on anything wrong — I'll remember the corrections.

Ready for questions, or type "done" to end this session.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 15.3 Initial Goal Setup Prompt

After the first catch-up, Artha proposes the initial 5 goals from PRD OQ-3:

```
Now that I have baseline data, let's set up your first goals.
Based on your data, I recommend these 5 starting goals:

1. Net worth / savings trajectory
   → Metric: Aggregate balances from detected accounts
   
2. Immigration readiness
   → Metric: All deadlines known ≥90 days out
   
3. Parth GPA ≥ 3.8
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
Preference: Immigration deep-dives detailed (Ved wants full context).
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
- "Hey Siri, ask Artha how Parth is doing in school."
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

### 18.1 Visual Generation Workflow

```
User: "Create a Diwali greeting card for family."

Artha: Generating a Diwali card...
       Style: Traditional (diyas, rangoli, warm colors).
       Message: "Happy Diwali from the Mishra family 🪔✨"

       ⏳ Generating via Gemini Imagen...
       ✅ Saved to ~/OneDrive/Artha/visuals/diwali-2026.png

       Preview: [The card is saved at the path above.
                 Open in Finder: open ~/OneDrive/Artha/visuals/]

       Want to:
       1. Send to email list (Diwali contacts from contacts.md)
       2. Regenerate with different style
       3. Done — I'll use it manually

       [1 / 2 / 3]
```

### 18.2 Style Customization

If the user wants to customize:

```
User: "More modern style, less traditional."

Artha: Regenerating with a modern aesthetic...
       Style: Contemporary minimalist, geometric patterns,
              warm gold tones.
       
       ✅ Saved to ~/OneDrive/Artha/visuals/diwali-2026-v2.png
       (Previous version: diwali-2026-v1.png kept.)

       Use this version? [yes / try another style / keep both]
```

### 18.3 Occasion Calendar Integration

Events from `occasions.md` trigger proactive visual generation suggestions:

```
━━ 💬 ARTHA NOTE ━━━━━━━━━━━━━━━━━━━━━━━━━
Diwali is in 5 days (March 12).
Last year you sent Diwali greetings to 23 contacts.

Want me to:
1. Generate a card + compose emails to last year's list
2. Generate a card only (you'll send manually)
3. Skip this year

[1 / 2 / 3]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 19. Autonomy Progression — Trust UX

### 19.1 Trust Level Visibility

The current trust level is never hidden. It appears in `/status` output and whenever an action proposal shows the trust requirement:

```
Trust Level: 0 (Observer)
  → Artha reads and reports. No actions executed.
  → Elevation to Level 1 (Advisor): 24 of 30 days complete.
     Pending: Zero false positives in critical alerts (✅ met).
     Pending: Briefing accuracy ≥95% (current: 97% ✅).
     Pending: 6 more days of reliable operation.
```

### 19.2 Elevation Prompt

When all criteria are met:

```
━━ 🔑 TRUST ELEVATION AVAILABLE ━━━━━━━━━━

All criteria for Level 1 (Advisor) met:
  ✅ 30 days of Level 0 operation
  ✅ Zero false positives in critical alerts
  ✅ Briefing accuracy: 97%
  ✅ All recommended actions reviewed

At Level 1, I can propose specific actions:
  • Draft emails (you approve before send)
  • Add calendar events (you approve)
  • WhatsApp messages (you tap send)
  • Visual greeting cards

Approve elevation to Level 1? [yes / not yet / details]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 19.3 Demotion UX

Demotion is instant and transparent:

```
User: "Artha, go back to Level 0."

Artha: Trust level reset to Level 0 (Observer).
       All action proposals will now be queued as
       recommendations only (no execute option).
       The elevation clock resets — 30 days to re-earn Level 1.
       
       Logged to audit.md.
```

If an automatic demotion is triggered:

```
⚠ TRUST DEMOTION

Reason: Critical false positive detected — immigration alert
flagged an EAD deadline that was already resolved.

Trust level: Level 1 → Level 0 (automatic).
The elevation clock resets. 30 days to re-earn Level 1.

This is a safety measure. Review the false positive:
  /domain immigration → Recent Activity

Logged to audit.md.
```

---

## 20. UX Gaps & Design Decisions

### 20.1 Identified Gaps in PRD/Tech Spec (Resolved in This Spec)

| Gap | Source | Resolution |
|---|---|---|
| No briefing format for quiet days | PRD §6 defined busy-day format only | §4.3: Quiet day template — explicit "no alerts, no action items" in ≤10 lines |
| Email subject line convention undefined | PRD F15.4 (Briefing Synthesizer) mentions email but no subject line design | §12.2: Subject line includes `Artha`, date, alert severity summary, item count |
| No crisis-day briefing design | PRD §6 Mode 1 — single template | §4.4: Crisis day format — numbered critical items, recommended next actions, count header |
| Family briefing format for shared review | PRD OQ-2 (family access) mentioned but no output format | §13.4: Family briefing variant — excludes finances/immigration, designed for dinner table |
| Greeting behavior undefined | PRD says nothing about session start behavior | §2.4: Artha waits silently; >48hr nudge only if data justifies it |
| Session close behavior undefined | PRD/Tech Spec define encrypt-on-stop but no UX | §2.3: Closing message with signal-to-noise summary and next recommended catch-up |
| First-run experience undefined | PRD §13 Phase 1A mentions "first catch-up" but no UX | §15: Full onboarding sequence — bootstrap, guided first catch-up, initial goal setup, progressive feature discovery |
| On-demand chat format guidelines missing | PRD §6 Mode 2 lists example queries but no response format | §7.1: Format rules — factual (direct answer first), status (structured), comparison (tables), ambiguous (offer options) |
| Error UX undefined | Tech Spec §7.2 defines error handling but not user-facing messages | §14: Error display patterns — human-readable, always includes impact + resolution path |
| Alert deduplication UX missing | Tech Spec §6.1 defines dedup rules but not user-facing behavior | §6.2: First occurrence (full), repeat-no-change (suppressed), repeat-worsened (re-surface with delta), resolution (checkmark) |
| Domain ordering convention missing | PRD §6 Mode 1 lists domains but no display order | §3.2: Priority order by consequence asymmetry, with alert-based promotion |
| Staleness indication missing | Tech Spec §7.3 mentions staleness but no visual design | §14.3: Staleness annotation with clock emoji and age, only for >24hr data |
| Check-in fatigue prevention | PRD §6 Mode 6 describes check-ins but no suppression logic | §11.3: Max 3 items, defer option, 14-day suppression after 2 dismissals, no check-in on quiet days |
| Action batch approval missing | Tech Spec §7.4 defines individual proposals | §9.2: Batch approval for low-risk calendar-only actions |
| /health command output undefined | Tech Spec §3.6.1 defines the command but no output format | §10.5: Full system integrity report — files, encryption, MCPs, CLIs, context window, OneDrive sync |
| Data density guidelines missing | PRD mentions "<3 minutes reading time" but no line count targets | §3.3: Explicit line count and reading time targets for every output type |
| Visual generation iteration UX | PRD F15.21 defines generation but no regeneration/customization flow | §18.2: Style customization through conversation, version history preserved |
| Trust progression visibility | PRD §10 defines elevation criteria but no day-count tracking display | §19.1: Trust level always visible, with progress toward next level during /status |
| Relationship intelligence display | PRD v3.8 FR-11 expanded relationship graph but no UX pattern *(v1.2)* | §4.1: Relationship Pulse in daily briefing; §10.8: `/relationships` command; §5.1: Relationship Health in weekly |
| Leading indicators UX | PRD v3.8 §8.11 defines leading indicators but no visualization *(v1.2)* | §8.5: Leading indicators display with lagging-first ordering; divergence alerts in weekly summary |
| Digest mode briefing | PRD v3.8 F15.26 defines digest mode but no briefing template *(v1.2)* | §4.5: Full digest mode template with temporal ordering, priority-tier filtering, consolidated actions |
| Decision graph display | PRD v3.8 F15.24 defines decisions but no UX *(v1.2)* | §10.6: `/decisions` command with active/resolved split, detail view with alternatives |
| Scenario analysis UX | PRD v3.8 F15.25 defines scenarios but no interaction model *(v1.2)* | §10.7: `/scenarios` with side-by-side comparison, interactive editing, templates |
| Accuracy Pulse display | PRD v3.8 F15.27 defines accuracy tracking but no weekly format *(v1.2)* | §5.1: Weekly Accuracy Pulse section with acceptance rate, corrections, domain breakdown |
| Action friction display | PRD v3.8 #10 defines friction levels but no visual pattern *(v1.2)* | §9.1: Friction emoji (🟢/🟠/🔴) in proposal; batch approval for low-friction |
| Flash briefing format | PRD v3.9 F15.40 defines compression but no UX template *(v1.3)* | §4.6: Flash briefing template — ≤8 lines, ≤30 sec, consequence line, suggest-deep footer |
| Consequence forecasting display | PRD v3.9 F15.37 defines "IF YOU DON'T" alerts but no visual pattern *(v1.3)* | §4.7: Consequence forecast display — impact chains, flash vs. standard variants, confidence gate |
| Coaching engine interaction | PRD v3.9 F13.14–F13.16 defines coaching but no UX *(v1.3)* | §8.6: Coaching engine interaction design — nudge/obstacle/celebration patterns, max 1 per catch-up |
| Dashboard display format | PRD v3.9 F15.29 defines dashboard but no output design *(v1.3)* | §10.10: /dashboard output — life pulse table, active alerts with scores, system health |
| Life scorecard display | PRD v3.9 F15.44 defines scoring but no visual *(v1.3)* | §10.11: /scorecard output — 7-dimension table with score/trend/key metric, composite score |
| Bootstrap interview UX | Tech Spec v2.0 §7.5 defines interview flow but no interaction design *(v1.3)* | §10.9: /bootstrap output — domain selection, one-question-at-a-time interview, progress indicator |
| Session quick-start UX | Tech Spec v2.0 §7.12 defines routing but no display *(v1.3)* | §10.1: /catch-up flash and /catch-up deep commands; §4.6: auto-selection for ≤4hr gaps |
| Week Ahead preview UX | PRD v4.0 Mode 1 defines Monday preview but no template *(v1.4)* | §4.1: Week Ahead table in Monday briefings with day-by-day events + deadlines |
| PII detection footer UX | PRD v4.0 F15.47 defines detection stats but no display *(v1.4)* | §4.1: PII Guard footer with scan count, redactions, false positives |
| Calibration question UX | PRD v4.0 F15.46 defines post-briefing calibration but no interaction *(v1.4)* | §4.1: 2-question calibration block after briefing; skip-friendly |
| Weekend planner UX | PRD v4.0 F8.7 defines Friday planner but no output design *(v1.4)* | §4.8: Weekend Planner with confirmed events + suggested tasks + deadlines |
| College countdown UX | PRD v4.0 F4.11 defines countdown dashboard but no visual *(v1.4)* | §8.8: Milestone table with urgency colors, next action, overall progress |
| /diff command UX | PRD v4.0 F15.51 defines state diff but no output design *(v1.4)* | §10.12: Delta visualization with +/Δ/− markers per domain |
| Data integrity error patterns | Tech Spec v2.0 §8.5.1 defines guard but no error UX *(v1.3)* | §14.1: Net-negative write warning, corrupt decrypt restore, bootstrap detection, OAuth expiry warning |
| Session quick-start UX | Tech Spec v2.0 §7.12 defines routing but no display *(v1.3)* | §10.1: /catch-up flash and /catch-up deep commands; §4.6: auto-selection for ≤4hr gaps |

### 20.2 Open UX Decisions

| # | Decision | Options | Recommendation |
|---|---|---|---|
| UX-OD-9 | Where should Relationship Pulse appear in daily briefing? *(v1.2)* | After BY DOMAIN (contextual) / Before GOAL PULSE (visible) / New section | **Before GOAL PULSE.** Relationships are a goal-adjacent concern. The user sees relationships → goals → ONE THING as a coherent arc from "people" to "priorities" to "action." |
| UX-OD-10 | Should leading indicators show in daily briefing or weekly only? *(v1.2)* | Daily (immediate) / Weekly (less noise) / Both with different detail | **Both.** Daily shows one column in Goal Pulse table (compact). Weekly shows full `/goals leading` detail with divergence analysis. |
| UX-OD-11 | Should digest mode be automatic or user-triggered? *(v1.2)* | Auto (>48hr gap triggers) / Manual (/catch-up --digest) / Auto with opt-out | **Auto with opt-out.** The gap detection is precise and the format is strictly better for catch-up-after-absence. If the user prefers standard format after a gap, `/catch-up --standard` overrides. |
| UX-OD-12 | Should Accuracy Pulse appear in email briefings? *(v1.2)* | Yes (transparency) / No (internal metric) / Weekly email only | **Weekly email only.** Daily accuracy data is too granular for email. Weekly summary email includes the aggregate. |
| UX-OD-13 | How should high-friction actions be visually distinguished? *(v1.2)* | Color only (🔴/🟠/🟢) / Color + text / Color + text + confirmation prompt | **Color + text + confirmation prompt.** High-friction actions get an extra "Are you sure?" confirmation line. This friction is the point — it slows the user down for consequential actions. |
| UX-OD-14 | Should `/scenarios` support interactive what-if editing? *(v1.2)* | Yes (conversational) / No (read-only display, edit via chat) / Phase 3 | **Yes, conversational.** The user says "what if the rate is 5.2% instead?" and Artha re-runs the scenario. This is the natural interaction model — scenarios are inherently iterative. |
| UX-OD-1 | Should briefing include a "Reading time: ~X min" estimate? | Yes (calibrates attention) / No (unnecessary) | **Yes.** Add to header line after email count. Reduces anxiety about time commitment. |
| UX-OD-2 | Should Artha use Archana/Parth/Trisha's names or role labels? | Names (personal) / "your wife" / "your son" (relational) | **Names.** Always. Artha is family-aware; role labels feel clinical. |
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
| UX-OD-25 | Should college countdown appear in email briefings? *(v1.4)* | Yes (Archana sees it too) / No (terminal only, sensitive) / Yes but simplified | **Yes but simplified.** Email gets milestone count + next action only. Full countdown in terminal. |

### 20.3 Design Principles Summary Table

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

*Artha UX Spec v1.4 — End of Document*

*"The best interface is the one you forget you're using. Artha speaks when it matters, is silent when it doesn't, and always tells you where you stand — in under 3 minutes."*

---

**Cross-references:**
- PRD v4.0: §6 (Interaction Modes — Week Ahead, Fastest Next Action), §7 (FR-1 through FR-18 with v4.0 additions), §8 (Goal Intelligence), §8.11 (Leading Indicators + Auto-Discovery), §9 (Architecture), §9.8 (Tiered Context), §10 (Autonomy Framework), §11 (FR-11 Relationship Intelligence + India TZ), §12.6 (Privacy Surface), §13 Phase 2A (Workstreams T–X v4.0), Phase 2B (Canvas, Apple Health), Phase 3 (WhatsApp)
- Tech Spec v2.1: §2 (Artha.md), §3.5 (Canvas LMS API, Apple Health), §3.6 (Slash Commands + /diff), §4.4 (College Countdown schema), §4.10 (Decision Deadlines schema), §5.1 (Week Ahead, PII Footer, Calibration), §5.3 (Monthly Retrospective), §7.1 (Steps 7b–7c v2.1), §7.16–7.19 (/diff, Weekend Planner, Canvas fetch, Apple Health import), §8 (Security Model)
