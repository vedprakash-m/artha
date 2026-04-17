# Artha — UX Specification
<!-- pii-guard: ignore-file -->

> **Version**: 3.17 | **Status**: Active Development | **Date**: April 2026
> **Author**: [Author] | **Classification**: Personal & Confidential
> **Implements**: PRD v7.17.0, Tech Spec v3.34.0

> **⚠ Note on Example Data:** All personal names, schools, account numbers,
> and addresses in this document are **fictional examples** used to illustrate
> Artha's capabilities for a representative family. They do not represent
> real individuals. Your actual data is configured in `config/user_profile.yaml`.

| Version | Date | Summary |
|---------|------|----------|
| v3.17 | 2026-04-16 | Artha Channel Integration UX — §28: workout logging trigger patterns + acknowledgement format with goal progress, watch alert notification design (immediate Telegram alert vs. daily digest vs. weekly digest), brief request via Claw stale-while-revalidate UX, query relay interaction design (question → 120s timeout expectation → answer). Implements PRD v7.17.0 + Tech Spec v3.34.0. |
| v3.15 | 2026-04-09 | OpenClaw Home Bridge UX — §27: Home Events briefing section, WhatsApp approval workflow in Telegram, bridge health in `/status` output, kid-arrival presence notification, TTS announcement patterns. Implements PRD v7.14.0 + Tech Spec v3.29.0. |
| v3.14 | 2026-04-08 | Knowledge Graph v2.0 — §23 Work OS preamble updated to note KB-powered context (7 MCP tools, 950-token budget). Implements PRD v7.13.0 + Tech Spec v3.28.0. |
| v3.12 | 2026-04-07 | SPEC CONSOLIDATION — Implements PRD v7.11.0 + Tech Spec v3.26.0. §3.2: 4 new domains added (Readiness, Logistics, Tribe, Capital) to domain presentation order (15→19). NEW §26: New Domain UX — interaction design patterns for all 4 new domains (Readiness daily score + energy flags, Logistics warranty/renewal alerts + shopping deeplinks, Tribe reconnect radar + outreach staging, Capital cash flow projection + amount-confirm gate). |
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
23. [Work OS — Interaction Design](#23-work-os--interaction-design)
24. [Artha Channel Integration — ACI Interaction Design](#28-artha-channel-integration--aci-interaction-design)

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
| 16 | Readiness | Energy management, context-switching |
| 17 | Logistics | Warranty/renewal lifecycle |
| 18 | Tribe | Relationship health, reconnect cadence |
| 19 | Capital | Cash flow, investment pulse |

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

### 4.1a Smart Home Subsection (Home Domain) *(v8.2.0)*

When the HA connector is active and on home LAN, the **Home** domain section includes an IoT subsection. Format:

```
### Home
• [domain items as usual: utilities, mortgage, maintenance]

• 🏠 Smart Home: [N] devices online, [M] offline
  • 🔴 Ring Floodlight (front) offline since [time] — check power/WiFi
  • 🟡 Brother printer: toner 40% (~500 pages remaining)
• ♨️ Swim spa: 102°F (set: 104°F), pump running, no errors
• ⚡ Energy: 45 kWh today (avg: 38 kWh) — +18% [within normal range]
```

When **off home LAN** (traveling, cowork VM, etc.):
```
• 🏠 Smart home data unavailable (not on home LAN)
  Last sync: [time]. [N] devices reported online as of last sync.
```

**Promotion rule:** If any IoT signal is 🔴 Critical (security device offline) or 🟠 Urgent (energy spike, non-security device offline), the Home domain is promoted to the top of the 🔴/🟠 section regardless of its normal position in domain order.

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

  IoT / Smart Home *(v8.2)*
                  🔴       Security device (Ring camera, lock, alarm) offline >2h  Top of briefing (promotes Home domain)
                           HA system itself unreachable >1h during monitoring     Action: check power/WiFi
                           Swim spa error code present                            Action: check spa panel
                  🟠       Non-security monitored device offline >2h              In Home domain section
                           Energy consumption spike >30% above 7-day average      Action proposal queued
                           Swim spa water temp deviation >5°F                    Informational + spa status
                  🟡       Printer consumable (toner/drum) <20%                   In Home domain section
                           Automation not triggered on expected schedule           Informational only

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

### 6.4 Connector Health — Cross-Platform Freshness Display

Platform-gated connectors (e.g., `outlook_email` runs only on Windows; `apple_reminders` runs only on macOS) show their last-fetched timestamp in the Connector Health briefing block when they are skipped on the current machine. Data is sourced from `state/connectors/connector_freshness.json` (OneDrive-synced).

```
📡 Connector Health
   outlook_email   last fetched 14h ago on WINDOWS-PC   ✓
   gmail           fetched this session                  ✓
   apple_reminders fetched this session                  ✓
   kusto           last fetched 3h ago on WINDOWS-PC    ✓
   ⚠ outlook_calendar: last fetched 78h ago on WINDOWS-PC — consider syncing from Windows
```

UX rules:
- Staleness > 18 hours: 🟡 in domain section
- Staleness > 72 hours: 🟠 CRITICAL warning promoted to top of briefing
- Never shown when the connector ran this session (no staleness concern)

### 6.5 Routing Disambiguation

When the TF-IDF router's top-2 confidence scores are within 0.08 of each other (`RoutingResult.routing_ambiguity == True`), Artha surfaces a clarification request instead of silently routing to one domain:

```
I found two possible interpretations of your request:
  1. Finance — "mortgage payment update"
  2. Home — "property tax next payment"
Which did you mean? (or say both to address each)
```

UX rules:
- Ambiguity prompt is shown only when confidence gap is < 0.08 — not on every near-tie
- User can reply with the number, the domain name, or "both"
- The chosen route is remembered for the session and influences future routing scores

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

**Architecture (v2.0):** The LLM owns the human-readable Markdown goals table in the briefing and on-demand `/goals` output — it renders status, progress bars, and commentary there. `goals_writer.py` owns the YAML frontmatter block in `state/goals.md` — all structured writes (create, update field, change status) go exclusively through this script. The LLM never edits YAML in-place.

**Work OS boundary:** Personal catch-up pipeline reads only `state/goals.md`. Work OS pipeline (`/work` commands) reads only `state/work/work-goals.md`. These scopes never overlap; `/goals --scope all` is the only view that merges both.

**Status labels:** `ON TRACK` · `NEEDS_ACTION` · `STALE` (no progress >14d) · `OFF PACE` (metric behind trajectory) · `PARKED`

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

### 8.1a Goal Heartbeat *(v2.0 — Goals Reloaded)*

A conditional ≤2-line goal signal injected into non-weekly briefings (flash, standard) when the Goal Evaluation Protocol (Step 8-Or) detects any flag:

```
GOALS  NET WORTH: OFF PACE ($7k behind pace)  ·  EXERCISE: STALE (11d no progress)
```

Design rules:
- Shown only when at least one goal has status ≠ `ON TRACK` — silence when all goals are on track.
- Max 2 goals surfaced; remaining flagged count appended: `(+1 more — /goals)`.
- Never shown in weekly briefing (Goal Review section covers this).
- Always precedes the Action Proposals block so it reads as a signal, not a summary.

### 8.2 Goal Progress Visualization

Terminal-native progress bars using Unicode block characters:

```
Net Worth 2026 Target       ██████░░░░  62%  → On Track
Monthly Amazon < $XXX       ████████░░  78%  ⚠ At Risk
ByteByteGo course by Q2     ██░░░░░░░░  22%  ⚠ Behind
Exercise 4x/week            ██████░░░░  60%  → On Track
Weight Goal (185→160 lb)    ████░░░░░░  43%  → On Track   ← direction:down normalized
```

**Bar interpretation:**
- `█` = progress achieved (filled)
- `░` = remaining (empty)
- `→` prefix = stable/on track
- `⚠` prefix = at risk or behind
- `🔴` prefix = significantly behind
- `✓` = achieved (100% filled)

**Direction:down formula:** For goals where lower is better (e.g. weight, spending), progress = `1 - (current - target) / (baseline - target)`. Requires `baseline:` field in the metric block.

### 8.2a Goal Review Section *(v2.0 — Goals Reloaded)*

Appears in every weekly summary as a dedicated subsection (gated by `goal_review_enabled: true` in `config/artha_config.yaml`):

```
## Goal Review · Week of March 24–30, 2026

PERSONAL
  Net Worth 2026         ██████░░░░  62%  → ON TRACK      next: Review Q1 statement
  Weight to 160 lb       ████░░░░░░  43%  → ON TRACK      last update: 2d ago
  Exercise 4x/week       ██░░░░░░░░  22%  ⚠ NEEDS_ACTION  last: 4d ago
  ByteByteGo course      ██░░░░░░░░  20%  🔴 OFF PACE     next: Schedule 2h session

WORK  [from /goals --scope work]
  ...

1 goal flagged. Run /goals --scope all for full detail.
```

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

### 8.6 Coaching Engine Interaction Design *(v1.3; v2.0 — Goals Reloaded)*

Coaching engine runs at Step 8 via `coaching_engine.py --format json`; output presented at briefing end (Step 19b). JSON contract: `{goal_id, nudge_type, suppressed, reason}`. Nudge types: `next_small_win`, `accountability`, `momentum`, `insight`, `challenge`. Suppressed when all active goals are on-pace — no nudge shown if nothing needs nudging.

Three interaction types: **Accountability nudge** (question format — "What's blocking X?"), **Obstacle anticipation** (predicts blockers based on calendar + behavioral patterns, suggests time blocks), **Celebration** (milestone-level achievements only). Design rules: appears after briefing alongside action proposals, max 1 nudge per catch-up (rotating across goals), nudge format configurable, dismissal always available (suppress 7 days).

### 8.7 Goal Sprint Display *(v1.4; v2.0 — Goals Reloaded)*

Active sprint shown in Goal Pulse: target, progress bar, pace (needed vs actual), status. Sprint sub-block in YAML (`start`, `end`, `target`, `label`) coexists with the goal's primary `metric` block. Sprint validation: target mandatory. Max 1 active sprint, 7–30 day duration. Behind-pace nudge in daily briefing; on-pace gets checkmark only.

### 8.8 College Application Countdown Display *(v1.4)*

Appears in Kids section when any milestone ≤90 days away. Milestones color-coded: 🔴 ≤14d, 🟠 ≤30d, 🟡 ≤90d, ✅ complete. Always shows NEXT ACTION. Application year as context anchor. Displayed during application season (Aug-Mar senior year).

## 9. Action Proposals — Approval UX *(ACTIONS-RELOADED v1.3.0)*

### 9.1 Briefing Embed — `§ PENDING ACTIONS`

The orchestrator (`scripts/action_orchestrator.py --list`) emits a numbered
block the AI embeds verbatim in the briefing at catch-up Step 12.5:

```
═══ ACTION ORCHESTRATOR ═══════════════════════════════════
Signals detected: 4 (email: 3, pattern: 1)
Proposals queued: 3 (1 duplicate suppressed)
Expired: 0

─── PENDING ACTIONS (3) ───────────────────────────────────
1. [abc12345] 🟠 email_reply | finance | Reply: Property tax notice
   Friction: high | Trust: 1 | Expires: 2026-04-03T17:00Z
2. [def67890] 🟢 calendar_create | calendar | Add: Parent-teacher meeting
   Friction: low | Trust: 1 | Expires: 2026-04-03T17:00Z
3. [ghi11223] 🟢 reminder_create | shopping | Reminder: Amazon delivery Tue
   Friction: low | Trust: 0 | Expires: 2026-04-03T17:00Z

Commands: approve <id>, reject <id>, approve-all-low, defer <id> [--until "+1h"|"tomorrow"|"next-session"]
════════════════════════════════════════════════════════════
```

Context-window guard: truncate display at 10 pending proposals; show count
of additional hidden proposals with `--list --limit 10` and note "N more —
use `--list --all` to see all."

### 9.2 Non-Content vs Content-Bearing Actions

**Non-content actions** (`calendar_create`, `reminder_create`,
`todoist_sync`, etc.) may be approved from the compact numbered summary —
the title / domain / friction tuple provides sufficient review context.

**Content-bearing actions** (`email_send`, `email_reply`, `whatsapp_send`,
and any future messaging handler) require full expanded preview before
approval. Before presenting these for approval the AI MUST:

1. Run `python3 scripts/action_orchestrator.py --show <id>` and display
   the full expanded preview:

```
═══ ACTION DETAIL ══════════════════════════════════════════════
ID:       abc12345
Type:     email_reply
Domain:   finance
Friction: high
Trust:    1
Expires:  2026-04-03T17:00:00+00:00

Title:    Reply: Property tax notice from County Assessor

─── CONTENT PREVIEW ──────────────────────────────────────────
To:       assessor@county.gov
Subject:  Re: Property Tax Assessment Notice
Body:
  Dear Assessor,

  Thank you for the notice regarding...
  [body text, max 80 lines; truncated with "... (truncated)" if longer]
════════════════════════════════════════════════════════════
```

2. Offer: `"approve 1"` (as-is), `"approve 1 with edits"` (AI applies
   edits then re-queues), or `"reject 1"`.

If decryption fails (key unavailable), print:
`"🔒 Parameters encrypted — decrypt from Mac terminal to preview."`

### 9.3 Approval Command Reference

| User says | Artha runs |
|-----------|-----------|
| "approve 1" or "approve abc12345" | `--approve abc12345` |
| "reject 2" or "reject def67890" | `--reject def67890` |
| "approve all low" | `--approve-all-low` |
| "skip" or "next" | Continue without acting; proposals remain pending |
| "defer 3" | `--defer ghi11223 --until "next-session"` (default = +24h) |
| "defer 3 until +1h" | `--defer ghi11223 --until "+1h"` |
| "defer 3 until tomorrow" | `--defer ghi11223 --until "tomorrow"` |

**Defer horizons:**

| Horizon | Resolves to |
|---------|------------|
| `+1h` | 1 hour from now |
| `+4h` | 4 hours from now |
| `tomorrow` | Next day 09:00 local |
| `next-session` | +24h (default when bare "defer" is used) |

After each approval show the execution result. If no response, continue
to Step 14 — proposals remain pending for the next session or Telegram
approval.

### 9.4 Sequencing

High-friction proposals first (require explicit review), then standard,
then low-friction (batch-approvable). Within each tier: oldest first
(earliest `created_at`). Low-friction proposals may be batch-approved with
"approve all low" (`--approve-all-low`).

Previous ordering (*v1.5*): Critical/urgent → Communications → Calendar →
Informational → Teams Join. The friction-tier ordering supersedes this for
queue-backed proposals; domain-priority remains a signal-weighting input,
not a display-sort.

### 9.5 WhatsApp Action UX

WhatsApp uses OS URL scheme — opens with pre-filled message, user must tap
Send. Note displayed: "This will open WhatsApp with pre-filled message.
You'll need to tap Send." Cannot confirm delivery (outside Artha's view).

### 9.6 Instruction-Sheet Action Type *(v1.9)*

Two actions in `config/actions.yaml` use `type: instruction_sheet` — they
generate guidance prose rather than executing code. Handler is null; no
network calls, no file writes.

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

### 9.7 Ad-hoc Actions (Human-Initiated)

When the user explicitly requests an action not from the queue (e.g.,
"send email to X about Y"), the action bypass the orchestrator queue and
is presented inline using the legacy `━━ ACTION PROPOSAL ━━` format,
then executed via the appropriate handler script directly.

This format is **for human-initiated ad-hoc requests only** — system-
detected proposals always flow through `actions.db` and appear in
`§ PENDING ACTIONS`.

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
/undo            Revert all session state writes to pre-session  2 seconds   *(AFW-6)*
                 /undo <domain>   Revert a single domain only
                 Shows diff summary; requires confirmation before applying
                 Safe: read-only ops and non-state commands unaffected
/pr              Content calendar — moments, threads, quota   5 seconds   *(v1.4)*
                 Backed by scripts/pr_manager.py --view
                 /pr threads   Narrative thread progress
                 /pr voice     Active voice profile + learnings
                 /pr draft linkedin [topic]   Generate draft (Phase 3)
/stage           Content Stage — card lifecycle management    5 seconds   *(v2.0)*
                 Backed by scripts/pr_stage/ (PR-2)
                 Requires: enhancements.pr_manager.stage: true
                 /stage preview <ID>   Show card + draft content
                 /stage approve <ID>   Emit copy-ready post text
                 /stage draft <ID>     Trigger draft generation (Phase 2)
                 /stage posted <ID> <platform>   Log published post
                 /stage dismiss <ID>   Archive without posting
                 /stage history [year] Browse cross-year archive (Phase 4)
/radar           AI Trend Radar — top scored AI signals       5 seconds   *(v2.9)*
                 Backed by scripts/skills/ai_trend_radar.py
                 Requires: enhancements.pr_manager.ai_trend_radar.enabled: true
                 /try <signal_id>   Mark signal for experimentation
                 /skip <signal_id>  Dismiss signal from radar
/catch-up flash  Flash briefing (≤30 sec reading time)        1 minute    *(v1.3)*
/catch-up deep   Deep analysis with extended reasoning        5–8 minutes *(v1.3)*
/eval            Catch-up evaluation report (full)            10 seconds  *(v2.5)*
                 Backed by scripts/eval_runner.py
                 /eval perf          Performance trends only
                 /eval accuracy      Acceptance rate, signal:noise
                 /eval freshness     Domain staleness, OAuth health
                 /eval skills        Skill health table (broken-first) *(v5.0)*
                 /eval effectiveness Engagement rate trends, R2/R8 status *(v5.0)*
/lint            Cross-domain data health audit (six-pass)      5-15 seconds  *(v7.9)*
                 Backed by scripts/kb_lint.py
                 lint --fix          Auto-remediate P1/P2 issues (confirmation required)
                 lint --json         Machine-readable findings output
                 lint --pass P1      Run a single pass only
                 lint --init         Bootstrap missing state templates
/career          Career Search Intelligence *(FR-25, Phase 1)*    varies
                 Backed by prompts/career_search.md + scripts/skills/career_pdf_generator.py
                 /career eval <URL|JD>   Full A–G evaluation (~20–32K tokens, explicit invoke only)
                                         NEVER loaded during catch-up or briefing
                 /career tracker         Pipeline status view (from state/career_search.md)
                 /career pdf <NNN>       Generate ATS-optimized CV PDF via Playwright
                                         Output: output/career/cv-{company}-{date}.pdf
                 /career stories         Story Bank review (20-story cap, 5 pinned)  *(Phase 2)*
                 /career scan            Portal scan — Greenhouse/Ashby/Lever           *(Phase 2)*
                 /career prep <company>  Interview prep from tracker + story bank       *(Phase 2)*
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

**Harness metrics block *(v2.0)*:** When `harness:` is enabled, `/health` appends a `DEEP AGENTS HARNESS` section showing last-session metrics:

```
── DEEP AGENTS HARNESS ──
Context offloading  ✔  3 artifacts | ~18K tokens freed
Progressive disclose ✔  4 prompts loaded · 14 skipped (≈12K saved)
Session summary     ✔  triggered (threshold: 70%) → tmp/session_history_1.md
Middleware          ✔  0 blocks · 0 verify failures · 12 writes audited
Structured output   ✔  validated → tmp/briefing_structured.json
```

All values sourced from `state/health-check.md → harness_metrics`. If any phase is disabled, its line shows `— disabled`. If a phase recorded errors, the line shows 🟡 with error count.

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

### 10.17 `/eval skills` and `/eval effectiveness` Output Design *(v5.0)*

**`/eval skills`** — runs `eval_runner.py --skills`; reads `state/skills_cache.json`. Sorted: broken first, then degraded, then healthy. Shows only non-`warming_up` skills unless `--verbose`.

```
## Skill Health — 18 Skills

| Skill                      | Health     | Success% | Zero% | Last Value | Wall Clock | Cadence                    |
|----------------------------|------------|---------|-------|-----------|-----------|----------------------------|
| mental_health_utilization  | 🔴 broken  | 0%       | —     | Never      | —          | daily                      |
| uscis_status               | ⚠️ degraded | 100%    | 100%  | Never      | 45ms       | every_run → suggest daily  |
| financial_resilience       | ⚠️ degraded | 100%    | 100%  | Never      | 32ms       | every_run → suggest daily  |
| bill_due_tracker           | ✅ healthy  | 100%    | 18%   | Mar 26     | 12ms       | every_run                  |
| noaa_weather               | ✅ healthy  | 95%      | 5%    | Mar 27     | 88ms       | every_run                  |

Recommendations:
• Disable mental_health_utilization — 100% failure rate
• Disable uscis_status — no active case; re-enable when filing
• Reduce financial_resilience cadence to daily — data unchanged 25 sessions
```

**`/eval effectiveness`** — Claude-rendered from `state/catch_up_runs.yaml` + `state/skills_cache.json`. No Python script required — Claude narrates small data better than formatted output.

```
## Artha Effectiveness — Last 10 Catch-ups

| Date  | Format   | Engagement | User OIs | Corrections | Items Surfaced | Skills Broken |
|-------|----------|-----------|----------|-------------|----------------|---------------|
| 3/27  | standard | 0.33       | 1        | 0           | 3              | 1             |
| 3/26  | flash    | 0.50       | 2        | 1           | 6              | 1             |
| 3/25  | standard | 0.40       | 2        | 0           | 5              | 1             |
| 3/20  | standard | null       | 0        | 0           | 0              | 1             |
| ...

Trends:
• Mean engagement rate: 0.41 (target: 0.25–0.50) ✅
• R2 compression: NOT ACTIVE (need 3 more runs with engagement_rate)
• R8 alarm: NOT ACTIVE (mean rate above threshold)

Recommendations:
• 1 skill broken (mental_health_utilization) — see /eval skills
• Engagement healthy — no format changes needed
```

**Design rules:**
- Internal rule names (R2, R7, R8) appear in `/eval` output only — never in catch-up briefings
- `engagement_rate: null` entries are counted in the window but excluded from mean calculation
- "Target: 0.25–0.50" is shown as context; rates above 0.50 are noted but not flagged — over-engagement is not a problem

### 10.18 `lint` Output Design *(v7.9)*

**Standard lint output** — runs `kb_lint.py` six-pass pipeline; interactive findings table:

```
━━ DATA HEALTH AUDIT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pass P1 — Frontmatter Gate (24 files)
  ✓ No issues found

Pass P2 — Stale Dates (24 files)
  ⚠ finance.md › last_updated: 2025-11-01 (157 days — threshold 90d)
  ⚠ insurance.md › policy_review_date: 2025-10-15 (172 days)

Pass P3 — Orphan References (24 files)
  ✓ No issues found

Pass P4 — Contradiction Scanner
  ✓ No issues found

Pass P5 — Cross-Domain Rules (8 rules)
  ✓ No issues found

Pass P6 — Custom Rules (2 rules)
  ✓ No issues found

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Data Health: 100% · 0 errors · 2 warnings · 24 files · 312ms
Run `lint --fix` to auto-remediate P2 stale-date warnings.
```

**Briefing line (embedded, --brief-mode):**

```
# Clean
Data Health: 100% (24 files, OK, 312ms)

# Warnings only
Data Health: 96% (24 files, 1 warning, 289ms)

# Errors present — prefixed ⚠ to surface urgency
⚠ Data Health: 83% (24 files, 2 errors · 3 warnings, 341ms) — run `lint` for details
```

**`lint --fix` confirmation UX:**

```
The following fixes will be applied:
  1. finance.md › last_updated → today's date (2026-04-05)
  2. insurance.md › policy_review_date → today's date (2026-04-05)

Apply 2 fixes? [yes / no]
```

**Design rules:**
- `Data Health` line always appears in briefing output (Step 20b), even on first-run with no issues
- Errors escalate with `⚠` prefix; warnings display silently (no prefix)
- `--fix` never auto-applies without explicit confirmation — shows diff before any write
- `lint --json` output is machine-readable; used by CI and eval pipeline
- Findings table sorted: errors first, then warnings, then info; within each severity: by file name

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

### 14.7 Checkpoint Resume & Session Undo UX *(AFW-5/6)*

#### 14.7.1 Checkpoint Resume Prompt

When a prior session checkpoint exists (created < 4h ago) and the user runs a new catch-up, Artha surfaces a resume prompt before fetching any data:

```
━━ RESUME CHECKPOINT ━━
A catch-up from <time> was interrupted at <phase>.
Completed: email scan, finance, immigration
Remaining: health, kids, home, work
  [R] Resume from checkpoint   [N] Start fresh   [X] Discard checkpoint
```

Default: **R** (resume). Typing any other letter or pressing Enter on `N`/`X` starts fresh or discards. After 4 hours the checkpoint expires and no prompt is shown.

#### 14.7.2 /undo Output Design *(AFW-6)*

Running `/undo` (or `/undo <domain>`) shows a diff summary before applying:

```
━━ SESSION UNDO ━━
These writes will be reverted:
  finance.md      3 changes  (+2 lines, -1 line)
  open_items.md   1 change   (+1 item)

Type YES to confirm, or press Enter to cancel:
```

**Safety rules applied silently:**
- Read-only operations (briefings, `/status`, `/diff`, queries) are never included in the undo scope
- If the undo snapshot predates a manual user edit, a `⚠ Manual edit detected` warning is shown and the domain is excluded from the undo set unless the user explicitly adds it
- `/undo` is not available after `git commit` (the snapshot is cleared on commit)

#### 14.7.3 High-Score Item Undo Warning

When any item in the current session has a composite signal score ≥ 0.85 (urgency × impact × freshness), a gentle warning appears before undo confirmation:

```
⚠ HIGH-PRIORITY ITEM: "Visa appointment — 48h to respond"
   This item scored 0.92. Undoing will remove it from open_items.md.
   Include in undo? [Y/n]:
```

Default: **Y** (include). This pattern prevents accidental loss of critical action items.

---

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

> **Harness Layer 0 — Domain Index *(v2.0)*:** Before any domain data is loaded, `domain_index.py` builds a compact index card from state file frontmatter. This is an **invisible pre-layer** — the user never sees it, but it determines which domain prompts load. `/status` and `/items` load zero prompts; `/catch-up` loads only routed-domain prompts. The user experiences this as faster responses and more focused briefings.

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

## 16a. Adaptive Briefing Behavior *(v5.0 — SKILLS-RELOADED)*

After 10 catch-ups, `BriefingAdapter` silently adjusts format and content based on historical behavior. All adaptations are disclosed in the briefing footer using human language — internal rule names (R1–R8) never appear in user-facing text.

### 16a.1 Adaptive Rules Summary

| Rule | Trigger | User-facing disclosure |
|------|---------|----------------------|
| R1 | User has used flash format in ≥7 of last 10 catch-ups | "Briefing format: flash (matching your usual preference)" |
| R2 | `engagement_rate < 30%` in ≥7 of last 10 non-null runs | "Briefing simplified — recent sessions show low engagement" |
| R3 | Quiet period detected (no alerts for 5+ catch-ups) | *(no disclosure — just fewer items, handled by quiet-day logic)* |
| R4 | User has dismissed coaching nudge in ≥8 of last 10 catch-ups | "Coaching tip skipped (you prefer it off)" |
| R7 | Skill `consecutive_zero >= 10` (P1/P2 only) | "[Skill description] now checks less often (no new data recently)" |
| R7-prompt | Skill `consecutive_zero >= 20`, prompt not yet sent | "[Skill description] has been quiet for a while — disable it? [yes / keep]" |
| R8 | `engagement_rate < 15%` for 7 of last 10 runs, once per 14 days | "Recent briefings are generating little action — want to narrow focus? [Narrow focus / Trim quiet domains / Switch to flash / Keep current]" |

**Safety rails (all rules):**
- P0/P1 domains (`immigration`, `health`, critical `finance`) are NEVER suppressed by R2
- R7 and R8 prompts fire at most once per occurrence window — never nag
- `/catch-up deep` always shows full output regardless of active rules
- Cold-start gate: R2 and R8 require ≥10 catch-ups with valid `engagement_rate` data

### 16a.2 Skill Health in Briefing

The briefing body shows skill health only for degraded/broken skills. Healthy skills are silent (UX-1).

**Standard and deep briefing** — "Skill Health" line in briefing footer when any broken/degraded skills exist:
```
[System] 2 skills need attention — run /eval skills for details.
  • mental_health_utilization: broken (100% failure rate)
  • uscis_status: now checks less often (empty 25 sessions)
```

**Flash briefing** — only broken skills (degraded suppressed):
```
[System] 1 skill broken — run /eval skills.
```

**`/health` command** — full skill health table (all skills, all states):
```
### 🔧 Skill Health

| Skill                     | Health      | Last Value | Consecutive Zeros | Action                         |
|---------------------------|-------------|-----------|-------------------|--------------------------------|
| mental_health_utilization | 🔴 broken   | Never      | N/A (all failures)| Fix or disable                 |
| uscis_status              | ⚠️ degraded  | Never      | 25                | Now checks less often          |
| financial_resilience      | ⚠️ degraded  | Never      | 25                | Needs vault/config fix         |
| king_county_tax           | 🔄 stable   | Mar 26     | 0 (stable×15)     | Consider cadence: weekly       |
| bill_due_tracker          | ✅ healthy   | Mar 26     | 0                 | —                              |
```

### 16a.3 Briefing Footer Transparency Block

All active adaptations are shown in a compact footer block after every briefing section where rules fired. The footer is invisible when no rules are active:

```
━━ Artha adapted this briefing:
• Briefing simplified — recent sessions show low engagement
• uscis_status now checks less often (no new data recently)
Run /eval effectiveness for detailed engagement history.
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

---

## 24. v5.1 UX Patterns — Simplification Relaunch

*Source: `specs/ui-reloaded.md` — implemented March 2026. These patterns supersede conflicting guidance in earlier sections.*

### 24.1 Headline Briefing Format

**Headline** is the new default format for 4–48h gaps (replaces `standard`). It is a **priority filter** — runs the full 21-step pipeline and shows only critical/urgent items — not a diff view.

**Ten format rules:**

1. Only 🔴 critical and 🟠 urgent items appear. Standard/low items are in "show everything."
2. Empty domains are **invisible** — "No new activity" is noise.
3. **ONE THING is the opening line** — the highest urgency×impact×actionability item.
4. Calendar is exactly one line: "6 events (dentist 3pm · standup 10am)."
5. Items and goals are summary stats, not expanded lists.
6. Relationships are one line — top overdue reconnect only.
7. Developer metrics (signal:noise, PII stats, email count) are absent — they live in `/health`.
8. Machine IDs are hidden — "OI-023" becomes "the tax return"; system resolves IDs when user refers by description.
9. Dates are human — "April 15 (17 days)" not "2026-04-15."
10. Drill-down prompt is always present: *"Say 'show everything' for the full briefing, or ask about any domain."*

**Quiet day pattern** (no critical/urgent items):

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTHA · Sunday, March 29 — ✅ All Clear
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Nothing urgent today.
📅 2 events  📋 12 open · none overdue  🎯 All goals on track
Say "show everything" for the full picture.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Format ladder:** flash (<4h) → headline (4–48h) → digest (>48h). Monday auto-selects weekly (headline + week-ahead). "Show everything" always available.

### 24.2 Colon-Menu Progressive Disclosure

Appending `:` to any command lists its sub-commands without requiring `/guide` or `/help`:

```
/work:    → shows work sub-commands (briefing, prep, sprint, connect-prep, memo, people, remember…)
/brief:   → shows briefing format options (default, headline, flash, deep, everything, digest, weekly)
/content: → shows content actions (draft, approve, posted, dismiss, history, voice, threads)
/items:   → shows item actions (add, done, defer, quick)
/goals:   → shows goal actions (sprint, pause, check-in)
/domain:  → shows all 20 domain names
/health:  → shows health sections (connections, quality, cost, privacy, reconnect)
```

**Colon menu design rules:**
- Numbered, not IDed — user types `2` or the name
- One line per option (name + short description)
- Always ends with an NL hint: "Or just say what you want."
- Prompt-only implementation — no Python code needed

### 24.3 `/guide` — Contextual Discovery

`/guide` with no arguments generates a **contextually relevant** command menu based on current state, time of day, and recent activity. It is not a static page.

**Contextual hints table:**

| State signal | What `/guide` promotes |
|---|---|
| Monday morning | "Week ahead" + weekly summary trigger |
| Friday afternoon | Weekend planner + quick-win items |
| Items overdue > 3 | "Power half hour" for rapid clearing |
| Goal stale > 14 days | Sprint suggestion for stalled goal |
| Connect cycle approaching | `/work connect-prep` prominently |
| Token expired | "reconnect [service]" at top |
| No user profile | Setup wizard prominently |

Command graduation into `/guide`: commands appear **only after reaching Graduated status** (>30 days production, <5% error rate). Beta commands are labeled "(beta)". This prevents cluttering discovery with unreliable features.

---

## 25. External Agent Interaction UX (AR-9) *(v1.0)*

### 25.1 Agent Discovery

`agent list` — shows all registered external agents with trust tier, health status, and last-used timestamp. Terminal-native tabular output:

```
AGENT                          TIER       HEALTH   LAST USED
storage-deployment-expert      verified   ✅ OK    2h ago
kusto-query-assistant          verified   ✅ OK    1d ago
fleet-diagnostics              external   ⚠ WARN  5d ago
```

### 25.2 Routing Transparency

When an external agent is invoked, Artha announces it before showing the answer:

```
🔍 Consulting storage-deployment-expert (verified · last used 2h ago)…
```

Fallback cascade shown if agent is unavailable:

```
⚠️ Agent unavailable — answering from local KB (may be less current)
```

### 25.3 Expert Consensus Format

Agent responses are integrated into Artha's own voice. Source citation appended inline:

```
[Source: storage-deployment-expert · confidence 0.87 · verified 2026-04-02]
```

### 25.4 Data Quality Verdict Indicators

KB quality verdicts appear inline next to data-backed sections in briefing output:

- ✅ **PASS** — data is current and accurate; no caveat added
- ⚠️ **WARN** — usable but potentially stale; caveat appended inline
- 🕐 **STALE** — stale data served; background refresh signaled at end of response
- ❌ **REFUSE** — data too low-confidence; investigation guidance provided instead

### 25.5 Agent Health Dashboard

`agent health` tabular output per agent: availability %, avg latency, quality score, consecutive failure count. Retired agents shown with ⛔ prefix.

### 24.4 `/content` — Unified Content UX

One namespace replaces `/pr` and `/stage`. The key UX improvement is **fuzzy topic matching** replacing machine IDs:

- `/content approve holi` searches active cards for "holi" in title/topic
- Single match → proceeds immediately
- Zero/multiple matches → disambiguation prompt
- Machine IDs (`CARD-SEED-HOLI-2026`) still accepted for precision

**Edge case:** `/content expand <topic>` with no matching card: *"No card found for that topic — say `/content draft [platform] [topic]` to create one."*

### 24.5 `/health` — Consolidated System View

`/health` with no arguments shows all sections in one view: Connections, Domain Health, Quality (7-day), Cost, Privacy, State Changes. Scoped views available: `/health connections`, `/health quality`, `/health cost`, `/health privacy`.

`/health reconnect <service>` provides guided OAuth re-auth — the same flow triggered by NL ("reconnect Gmail"). It appears in the `/health:` colon menu.

### 24.6 Error UX — Errors That Respect Humans

**Error philosophy:**
1. **Self-heal first** — attempt token refresh before surfacing any error
2. **One sentence, one action** — no script names, no file paths, no technical concepts
3. **Partial data > no data** — missing source is a footnote, not a blocker
4. **No tracebacks, ever** — caught at output boundary, logged, replaced with human message

**Examples:**
- Old: `"⚠️ Outlook data unavailable — rerun setup_msgraph_oauth.py on Mac"`
- New: `"I couldn't reach your Outlook. Say 'reconnect Outlook' to fix it."`

First failure → silent skip. Third consecutive failure → one-line fix instruction. Never a blocker until 7-day bound is crossed.

### 24.7 First-Run UX

**Demo-first pattern:** Before any setup, show a demo briefing on synthetic data. Motivation before friction. Then: "Want to set up your real data? Just tell me your name and email."

**Onboarding staircase:**
1. Demo briefing (zero config, 0 seconds)
2. Name + email → archetype auto-detected → first real briefing
3. First catch-up → Artha suggests: "Want to add your Google Calendar?"
4. Artha notices gaps over time and suggests relevant setup conversationally

Encryption is **automatic and invisible** — user never sees "age", "keypair", "keyring", or "recipient."

### 24.8 Success Metrics (v5.1)

| Metric | Target |
|---|---|
| Commands needed to get value | ≤ 2 (7-command surface) |
| First value time (new user) | < 90 seconds |
| Headline comprehension | >80% users understand "say 'show everything'" |
| Bridge path quality parity | >90% of primary CLI intents handled correctly |
| NL routing accuracy | >95% of recognized intents route correctly |
| Preflight GO rate | >90% on first run without manual intervention |
| Error self-heal rate | >70% of token failures resolve without user action |

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

| UX-9 | Harness is transparent by default | Session summarization, structured validation, and context offloading are invisible to the user. Only `/health` surfaces harness metrics. |
| UX-10 | Graceful degradation is always silent | If any harness phase fails or is disabled, the catch-up continues unchanged. No user-visible error for internal infrastructure failures. |

---

*"The best interface is the one you forget you're using. Artha speaks when it matters, is silent when it doesn't, and always tells you where you stand — in under 3 minutes."*

---

## 23. Work OS — Interaction Design

> This section documents the UX patterns for the Work Intelligence OS (PRD FR-19, Tech Spec §19). The Work OS is a separate surface — separate vault, separate connectors, separate slash command namespace. No work content appears in personal briefings beyond the scalar boundary score. Full implementation: 25-command read-path CLI (`work_reader.py`), 9 Work OS scripts, 20 domain state files, 883 tests.
>
> **Knowledge Graph context.** The Work OS knowledge graph (Tech Spec §22) provides entity-aware responses: `artha_kb_context` assembles 950-token context packages for `/work prep` cards; `artha_kb_search` powers keyword lookups in `/work` commands; `artha_kb_episodes` surfaces recently synced data for `/work return`. Entity lifecycle stages (`in_flight`, `shipped`, `proposed`) surface in meeting prep cards and sprint views.

### 23.1 Command Palette

| Command | Category | What it does |
|---|---|---|
| `/work` | Core | Daily work briefing — calendar load, prep status, commitment summary, boundary signal |
| `/work pulse` | Core | 30-second work status snapshot — meetings, comms, boundary score, DFS, program health (risk posture + R/Y/G signal counts from Kusto-validated metrics) |
| `/work sprint` | Core | Sprint-focused view — ADO work items, active commitments, blockers |
| `/work prep <title>` | Meeting | On-demand meeting prep card for a named meeting (readiness score, open threads, key people, XPF program context for relevant meetings) |
| `/work live <id>` | Meeting | Live meeting assist — active action capture, decision tracking during a meeting |
| `/work mark-preread <id>` | Meeting | Mark a meeting as pre-read; updates readiness score |
| `/work notes [id]` | Capture | Post-meeting captures, weekly summaries; meeting-id search; WorkIQ fallback for transcripts |
| `/work remember <text>` | Capture | Instant micro-capture — appended to work-notes with timestamp |
| `/work decide <context>` | Capture | Structured decision support — records D-NNN decision with context, options, outcome |
| `/work connect-prep` | Career | Connect cycle evidence assembly — goal alignment, key contributions, GAP flags, Kusto-validated quantitative evidence |
| `/work connect-prep --calibration` | Career | Calibration defense brief — rating justification with evidence density |
| `/work promo-case` | Career | Promotion readiness assessment — scope arc, evidence density stars, visibility events |
| `/work promo-case --narrative` | Career | Full promotion narrative Markdown draft |
| `/work career` | Career | Career timeline with roles, key projects, growth arc |
| `/work journey [project]` | Career | Project timeline with milestone evidence and scope arc |
| `/work memo` | Content | Status memo via Narrative Engine (escalation_memo or decision_memo template) |
| `/work memo --weekly` | Content | Auto-drafted weekly status (weekly_memo template) |
| `/work newsletter [period]` | Content | Team newsletter draft with Program Health section (per-workstream signals, risk posture, red metrics from Kusto) |
| `/work deck [topic]` | Content | LT deck content assembly with program metrics in exec summary, per-WS status in metrics, red items in risks |
| `/work talking-points <topic>` | Content | Meeting-ready talking points (talking_points template) |
| `/work people [query]` | Org | People graph — top collaborators by tier, recency, org context; WorkIQ hint when person not found locally |
| `/work graph` | Org | Full org relationship graph — tier distribution, collaboration strength |
| `/work projects` | Org | Project portfolio view — meetings-per-project, ADO items, status |
| `/work sources [query]` | Org | Data source registry lookup |
| `/work sources add <url>` | Org | Register a new data source with context |
| `/work products` | Org | Product knowledge index — taxonomy, layers, active projects |
| `/work products <name>` | Org | Deep product knowledge — architecture, components, dependencies, teams |
| `/work products add <name>` | Org | Interactively create new product entry (index + deep file) |
| `/work reflect` | Intel | Reflection Loop — auto-detect due horizon, sweep + synthesize + draft |
| `/work reflect daily` | Intel | Force daily close — day's accomplishments + carry-forward |
| `/work reflect weekly` | Intel | Force weekly reflection with accomplishments, carry-forwards, reconciliation |
| `/work reflect monthly` | Intel | Force monthly retrospective aggregating weekly reflections |
| `/work reflect quarterly` | Intel | Force quarterly review aggregating monthly retros |
| `/work reflect --status` | Intel | Show last close times and which horizons are due |
| `/work reflect --tune` | Intel | Interactive scoring calibration — 15 pairwise comparisons |
| `/work reflect --backfill` | Intel | Backfill from 82-week work-scrape corpus |
| `/work reflect --audit [N]` | Intel | Show last N audit log entries (default: 20) |
| `/work accomplishments` | Intel | Browse accomplishment ledger — filter by program, impact, date range |
| `/work accomplishments add` | Intel | Append new accomplishment to ledger with next sequential A-NNN ID |
| `/work return [window]` | Intel | Absence recovery — summarizes what changed while away (default 3d, e.g. `4d`) |
| `/work boundary` | Intel | Boundary intelligence report — load trends, after-hours patterns, recommendations |
| `/work connect` | Intel | Review-cycle evidence by goal area |
| `/work incidents` | Intel | ICM/incident timeline — on-call history, resolution evidence |
| `/work repos` | Intel | Repository activity — commit velocity, PR cadence |
| `/work day` | Intel | Day-ahead schedule synthesis — conflicts, prep gaps, boundary forecast |
| `/work health` | System | Work connector health — per-connector status, degraded fallbacks, audit log tail |
| `/work refresh` | System | Explicit live connector refresh — re-run fetch + enrich stages |
| `/work bootstrap` | System | Guided 12-question setup interview to populate state/work/ |
| `/work warm-start` | System | Run or re-run historical import from scrape data |

### 23.2 Daily Work Briefing Format

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WORK BRIEFING — Mon Mar 24, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📅 CALENDAR  8 meetings today · 4h 45m meeting load
  🟩 Platform Alpha Weekly  10:35 AM  (recurring · 80% ready)
  ⬜ LT Review Dry Run  2:00 PM  (8 ppl · prep needed)
  [+6 more]

📋 COMMITMENTS  3 open · 1 overdue
  🔴 Platform Alpha signoff doc → Alex M.  due Mar 22 (2d over)
  🟡 Update deployment slides → Jane K.  due Mar 26
  ⬜ DeployFlow requirements review  due Apr 1

🎯 PROJECTS  Platform Alpha · Platform Beta · Platform-A-DD
  Platform Alpha: 4 meetings this week · ADO 12 items

🛡 BOUNDARY  Score 0.72 / 1.0  (Moderate load)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connectors: workiq_bridge ✓ · ado_workitems ✓ · msgraph_calendar ✓
```

**Format rules:**
- Calendar section: show next 2 high-priority meetings explicitly, then `[+N more]`
- Commitment section: 🔴 for overdue, 🟡 for due within 3 days, ⬜ for future
- Projects section: top 3 by meeting frequency this week
- Boundary score: sourced from `state/bridge/work_load_pulse.json`
- Connector health line: always rendered — proves data is fresh

### 23.3 Meeting Prep Card Format

Triggered via `/work prep <title>` or automatically at briefing time for top-priority meetings:

```
📋 MEETING PREP
  Platform Alpha Weekly — Mon 10:35 AM (recurring, instance #80)
  Organizer: you · 10 attendees

  Readiness: 72/100 ⚠ Below threshold

  Open threads from last week:
  - "Will you be updating the deployment slides?" (from Alex M.)
  - Platform Alpha blocker — status update needed

  Key people: Alex Morgan (mgr) · Jane Kim (skip) · Sam Rodriguez
  
  Prep suggested:
  → Update deployment slides before 10:35 AM
  → Reply to Alex M. re: slides status
```

### 23.4 Degraded Mode UX

Work OS follows the **no-blocking-failure** principle (Tech Spec §19.4). When connectors fail, the briefing still runs with visible status:

```
⚠ Work OS — Degraded Mode

  workiq_bridge: ⚠ unavailable — using cached calendar (8h old)
  ado_workitems: ✓ current
  msgraph_calendar: ✓ current

  [Briefing follows with cached WorkIQ data]
```

**Design rules:**
- Degraded mode is always transparent — the connector status footer never disappears
- Stale data is explicitly labeled with age: `(cached, 8h old)`
- Remediation text is surfaced in `/work health`, not in the main briefing (keeps briefing readable)
- If ALL connectors are down, show: `⚠ Work OS offline — showing last known state from [timestamp]`

### 23.5 Warm-Start UX

First-run onboarding automatically detects `work.bootstrap.import_completed: false` and prompts:

```
Work OS: No historical data found. 

  To bootstrap your people graph, projects, and career evidence from
  your work-scrape archive, run:

    python scripts/work_warm_start.py --scrape-dir <path> --dry-run
  
  This will show a preview before writing. Once confirmed:
    python scripts/work_warm_start.py --scrape-dir <path>
  
  See docs/work-warm-start.md for details.
```

After warm-start completion, the briefing transitions from "bootstrapping" to full mode:
```
✓ Work OS warm-start complete
  Imported 81 weeks · 284 people · 12 projects · 47 career events
  
  Top relationships: Alex Morgan (tier-0) · Jane Kim (tier-0) · Sam Rodriguez (tier-1)
  Recurring meetings detected: Platform-A-DD Daily Standup (60×) · Platform Alpha Weekly (80×) · [+4 more]
```

### 23.6 UX Design Rules (Work Surface)

| Rule | Rationale |
|---|---|
| Work content never leaks to personal briefing | Hard separation — `work_load_pulse.json` contains only scalar score + counts |
| Connector health is always visible | User must know when data is stale — no silent degradation |
| Names are unredacted on the work surface | Personal surface strips names; work surface shows real names (user's own data) |
| Meeting titles use title-case, never ALL CAPS | Same terminal typography as personal surface |
| All outputs fit in 80-column terminal | Work surface follows UX-6 terminal-native typography |
| Prep cards are on-demand, not auto-surfaced | Avoids information overload; user pulls prep when needed |

---

### 23.7 Narrative Engine UX

Triggered via `/work memo`, `/work memo --weekly`, `/work newsletter`, `/work deck`, `/work talking-points`, `/work connect-prep`, and `/work promo-case --narrative`.

**Template picker** (when ambiguous):
```
Select a template:
  1. weekly_memo      — Weekly status update
  2. talking_points   — Meeting-ready talking points
  3. boundary_report  — Boundary intelligence summary
  4. connect_summary  — Review-cycle evidence
  5. newsletter       — Team newsletter
  6. deck             — LT deck content
  7. calibration_brief — Calibration defense
  8. connect_evidence  — Goal-area evidence blocks
  9. escalation_memo  — Escalation memo
 10. decision_memo    — Decision record memo
```

**Weekly memo output format:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WEEKLY STATUS — Week of Mar 24, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏆 KEY WINS
  • Platform Alpha: signoff doc delivered (Mar 22)
  • DeployFlow: requirements review complete

🚧 IN PROGRESS
  • Platform Beta: LT deck content assembly (60%)
  • Platform-A-DD: scope arc documentation

⚠ BLOCKERS / RISKS
  • Deployment slides dependency on Alex M. approval

📋 NEXT WEEK
  • [auto-synthesized from ADO items + calendar load]
```

**Calibration brief format:**
```
━━━━ CALIBRATION BRIEF ━━━━
Rating Justification: [LEVEL]

Evidence Density: ★★★★☆ (4/5)

Key Contributions:
  [1] Platform Alpha — scope: team-wide, outcome: shipped
  [2] Connect cycle evidence — 4 goal areas covered

GAP Flags:
  ⚠ Visibility: 0 cross-org speaking events last quarter
  ⚠ Leadership: no direct report feedback entries
```

---

### 23.8 Promotion OS UX

Triggered via `/work promo-case` and `/work promo-case --narrative`.

**Readiness assessment format:**
```
━━━━ PROMOTION READINESS ━━━━

Scope Arc:     ★★★★☆  (4/5)  Individual → Team → Multi-team
Evidence:      ★★★☆☆  (3/5)  12 contribution events on file
Visibility:    ★★☆☆☆  (2/5)  Limited cross-org events
Impact:        ★★★★☆  (4/5)  Shipped 2 major milestones

Overall:       ★★★☆☆  (3.25/5)  Not yet ready

Top gaps to close:
  1. Add 2+ cross-org visibility events (talks, reviews, demos)
  2. Collect peer feedback entries (currently 0)
  3. Document scope expansion arc in work-career.md

Run `/work promo-case --narrative` for full Markdown draft.
```

---

### 23.9 Connect Cycle UX

Triggered via `/work connect-prep` and `/work connect-prep --calibration`.

**Evidence assembly format:**
```
━━━━ CONNECT CYCLE PREP ━━━━

  Goal Area Coverage:
    🟢 Impact       ████████░░  80%  (8 events)
    🟡 Velocity     █████░░░░░  50%  (5 events)
    🔴 Leadership   ██░░░░░░░░  20%  (2 events)
    🟢 Collaboration ███████░░░  70%  (7 events)

  GAP Flags:
    ⚠ Leadership evidence sparse — add 2+ events before review
    ⚠ No stretch project documented

  Key evidence blocks ready:
    → Platform Alpha delivery (Mar 2026) — scope: org-wide
    → DeployFlow requirements (Feb 2026) — team impact
    → [+6 more]
```

---

### 23.10 Quick Capture + Decision Support UX

**`/work remember <text>`** — Inline micro-capture:
```
✓ Captured → work-notes.md
  [2026-03-24 10:42] Platform Alpha signoff blocked on legal review
  (run `/work notes` to promote to structured item)
```

**`/work decide <context>`** — Decision record:
```
━━━━ DECISION RECORD ━━━━
ID: D-042
Context: <text provided>

Options considered:
  A. [user fills]
  B. [user fills]

Outcome: [pending]
→ Saved to state/work/work-decisions.md
→ Referenced as D-042 in work-notes.md
```

**D-NNN / OI-NNN ID format rules:**
- `D-NNN` — Decision record (3-digit zero-padded, sequential)
- `OI-NNN` — Open item (3-digit zero-padded, sequential)
- Both formats are cross-referenced between work-notes.md, work-decisions.md, work-open-items.md
- IDs survive across sessions — never reused

---

### 23.11 Bootstrap Interview UX

Triggered via `/work bootstrap`. Walks through a 12-question interview to populate `state/work/` files from scratch.

**Flow format:**
```
━━━━ WORK OS BOOTSTRAP ━━━━
This interview takes ~5 minutes and sets up your work intelligence base.

[1/12] What is your current role/title?
> [user input]

[2/12] Who is your manager? (first name, last name or alias)
> [user input]

... (questions 3–12 covering: org context, top 3 projects,
     key stakeholders, current sprint/milestone, career goals,
     connect cycle timeline, ADO org/project, time zone,
     working hours, after-hours boundary preference)

─────────────────────────────────────────────
✓ Bootstrap complete (12/12 questions answered)

Files written:
  state/work/work-career.md        ✓
  state/work/work-people.md        ✓
  state/work/work-projects.md      ✓
  state/work/work-boundary.md      ✓
  state/work/work-summary.md       ✓

Run `/work` to see your first briefing.
```

**Design rules:**
- Each answer immediately written atomically; partial bootstrap is safe to resume
- `--dry-run` flag previews what would be written without touching state files
- If a state file already exists, bootstrap asks: `Overwrite existing [file]? (y/N)`

---

### 23.12 Learning & Adaptive Behavior UX

Work OS adapts over time through three phases:

| Phase | Trigger | UX Effect |
|---|---|---|
| **Calibration** | First 10 briefings | Baseline metrics collected silently; no visible change |
| **Prediction** | After 10 briefings | `/work pulse` shows trend arrows (↑ ↓ →) for boundary score, meeting load, commitment completion |
| **Anticipation** | After 30 briefings | Proactive alerts surface: "Meeting load spike predicted Thursday" · "Commitment cluster forming Mar 27" |

**`state/work/work-learned.md`** stores learned patterns:
- Typical meeting load by day-of-week
- Recurring commitment patterns (pre-meeting prep time, signoff timing)
- Boundary score trend line
- Calibration predictions and accuracy delta

**Transparency rule:** All predictions show their basis: `(based on 12 similar weeks)`. If prediction accuracy drops below 60%, the system silently recalibrates — no user-visible disruption.

### 23.13 Product Knowledge Domain UX

Product Knowledge Domain (FW-18) adds three commands to the Work OS surface and injects product context into meeting prep cards.

#### `/work products` — Index View

Displays all tracked products from `work-products.md`:

```
### Product Knowledge Index
Products tracked: 4 | Last updated: YYYY-MM-DD

| Product    | Layer         | Team           | Active Projects | Status |
|------------|---------------|----------------|-----------------|--------|
| EngineA    | data-plane    | Core Team      | Project-Alpha   | active |
| ServiceB   | control-plane | Platform Team  | Project-Alpha   | active |
| Offering-C | offering      | Services Team  | —               | active |
| Platform-D | platform      | Infra Team     | Project-Beta    | active |
```

Graceful degradation: if `work-products.md` is absent, shows: `Product index not found. Run \`/work products add <name>\` to create your first entry, or add products during \`/work bootstrap\`.`

#### `/work products <name>` — Deep Knowledge Card

```
### EngineA — Product Knowledge
Layer: data-plane | Team: Core Team | Status: active
Last updated: YYYY-MM-DD

Architecture:
  [2–3 sentence description from the Architecture Overview section]

Components: 5 tracked | Dependencies: 2 upstream, 3 downstream
Active Projects: Project-Alpha, Project-Beta

Recent Knowledge:
  YYYY-MM-DD: [entry from Knowledge Log] [from: design review]
  YYYY-MM-DD: [entry from Knowledge Log] [from: team standup]
```

If the deep file for the named product does not exist: `Deep file not found for 'EngineA'. Run \`/work products add EngineA\` to create it.`

#### `/work products add <name>` — Interactive Creation Flow

Guided creation of a new product entry:

```
Creating new product entry: EngineA

> Architecture layer (e.g., data-plane, control-plane, offering, platform): data-plane
> Team that owns this product: Core Team
> One-sentence architecture summary: [user input]
> Active projects referencing this product (comma-separated, or leave blank):
> Routing keywords for meeting title matching (comma-separated):

✓ Index updated: state/work/work-products.md
✓ Deep file created: state/work/products/enginea.md
  Fill in Components, Dependencies, and Teams sections for full context.
```

Two artifacts written atomically:
1. `work-products.md` index — new row with 3-line summary + deep file pointer
2. `state/work/products/<slug>.md` deep file — pre-populated from answers + empty section scaffolding

#### Meeting Prep Context Injection

When `/work prep` runs for a meeting whose title matches a product routing keyword, product context is injected into the prep card:

```
### Meeting Prep: Team Sync — EngineA Architecture Review
Readiness: 72/100 | Attendees: 4

📦 Product Context (EngineA)
  Layer: data-plane | Team: Core Team
  [Architecture summary sentence from deep file]
  Components: 5 | Recent: YYYY-MM-DD: [latest Knowledge Log entry]

Open threads: 2 | Key people: [from work-people.md]
Prep actions: Review architecture overview before meeting
```

Context injection is limited to 4 lines. If multiple products match the meeting title, the highest-confidence match (most routing keywords hit) is selected. If no product index exists, the prior single-product context logic is used as a fallback.

#### Staleness Indicators

Product files surface staleness warnings at the 6-month threshold (vs. 2-week for projects):

```
⚠️ EngineA knowledge is 7 months old. Run `/work products add EngineA` to update.
```

Staleness warnings appear in deep knowledge card output and in `/work health`.

---

### 23.14 Reflection Loop UX (FW-19 v1.5.0)

Triggered via `/work reflect`, `/work reflect weekly`, `/work reflect monthly`, `/work reflect quarterly`, `/work reflect --status`, `/work reflect --tune`, `/work reflect --backfill`, `/work reflect --audit`.

#### Horizon Auto-Detection Prompt

When the user runs `/work reflect` (no horizon specified), the system checks what's due:

```
Reflection status:
  Daily close:    8h since last close ✓ (due)
  Weekly review:  6d since last close ✓ (due — Thu/Fri)
  Monthly retro:  28d since last close ✓ (due)

→ Weekly review is due. Run it now? [Y/n]
```

If nothing is due: `All horizons current. Next weekly due: Fri Mar 27.`

#### Weekly Reflection Output Format

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WEEKLY REFLECTION — Week 13 (Mar 24-28, 2026)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏆 ACCOMPLISHMENTS (14 items · 3 HIGH, 7 MEDIUM, 4 LOW)
  📋 Ledger refs: A-179 → A-187 (see work-accomplishments.md)

  By Goal:
  G2: Fleet Automation (+5 items, +23% progress)
    HIGH  Kusto live data pipeline — GQ-001/010/012/050 auto-refresh
    HIGH  Fleet source migration — xdataanalytics (90 clusters)
    MED   Dashboard widget for cluster health
    ...

  G3: Work OS (+2 items)
    MED   /work notes command + WorkIQ enrichment
    LOW   Test coverage expansion

🔄 RECONCILIATION (Planned vs Actual)
  ✅ Fix PF queries — done (all 12)
  ✅ Test coverage for new code — done (55 tests)
  ⏭ Expand refresh clusters — deferred to W14
  🆕 Fleet migration — unplanned (emerged from LT review)

⏭ CARRY FORWARD (3 items)
  ┌────────────────────┬──────┬──────────┬─────┐
  │ Item               │ Pri  │ Due      │ Age │
  ├────────────────────┼──────┼──────────┼─────┤
  │ Validate 11 PF qry │ HIGH │ W14-Mon  │ new │
  │ LT deck fleet nums │ MED  │ W14-Wed  │ new │
  │ Connect-prep refr  │ HIGH │ Apr 4    │ new │
  └────────────────────┴──────┴──────────┴─────┘

📝 DRAFT STAGED
  Card: CARD-W13-MEMO-001 (weekly_memo)
  Use `/stage preview CARD-W13-MEMO-001` to review
```

#### Carry-Forward Staleness

Items carried forward 2+ weeks are flagged:

```
  ⚠ [STALE] Review parking lot scope — carried 3 weeks, 2 cycles
    → Will move to Parking Lot at next monthly retro unless resolved
```

#### `/work reflect --status` Output

```
━━━━ REFLECTION STATUS ━━━━

| Horizon   | Last Close          | Due In  | Status     |
|-----------|---------------------|---------|------------|
| Daily     | Today 17:00 UTC     | 6h      | ✓ current  |
| Weekly    | Fri Mar 21 17:30    | now     | ⚡ due     |
| Monthly   | Feb 28 18:00        | 3d      | ⚡ due     |
| Quarterly | Dec 31 17:00        | Apr     | ✓ current  |

Active carry-forwards: 3 (0 stale)
Reflection files: 82 weeks in history
```

#### `/work reflect --tune` Calibration Session

Presents 15 pairwise comparisons:

```
━━━━ SCORING CALIBRATION ━━━━
Comparison 1/15 (adjacent-rank):

  A: Kusto pipeline integration [HIGH|TEAM|G2] — score: 1.06
  B: Newsletter auto-draft [MED|SELF|G3]      — score: 0.55

  Is A actually more impactful than B? [Y/n/skip]
```

After 15 comparisons: suggested weight adjustments displayed with before/after score distributions.

#### Backfill Progress Display

```
━━━━ BACKFILL — Phase 1a ━━━━
Parsing work-scrape corpus...

  [████████████████████░░░░] 68/82 files (83%)
  Current: 2025/07-w2.md (format: B-mid)
  Extracted: 847 accomplishments, 312 meetings, 94 decisions

  ✓ 2024-W34 → reflections/weekly/2024-W34.md
  ✓ 2024-W35 → reflections/weekly/2024-W35.md
  ...
```

#### Friday Briefing Reflection Footer

When weekly reflection is due, the daily briefing footer includes:

```
───────────────────────────────────────
📋 REFLECTION DUE
  Weekly review due — 3 unreconciled carry-forward items
  Run: /work reflect weekly
───────────────────────────────────────
```

#### Re-Run Behavior

| Scenario | UX |
|---|---|
| Daily close already done today | Prompt: "Daily close exists. Append new items or overwrite?" Default: append |
| Weekly already done this week | Prompt: "W13 reflection exists. Re-generate?" Default: skip (show existing) |
| `--force` flag | Bypass duplicate check, overwrite silently |

#### Design Rules (Reflection Surface)

| Rule | Rationale |
|---|---|
| Raw scores shown for first 4 weeks | Build calibration intuition before relying on labels |
| Draft deliverables go through `/stage` | Consistent review lifecycle; PII guard before share |
| Reflection never auto-runs | PULL contract — user triggers, system suggests |
| Carry-forward dedup by ID | Re-runs never duplicate items |

---

---

## 26. New Domain UX — Readiness, Logistics, Tribe, Capital *(v3.12)*

> Implements PRD v7.11.0 FR-20–FR-23. All four domains start at **L1 — Propose** autonomy. No domain writes to external systems without explicit user approval.

### 26.1 Readiness Intelligence UX

**Briefing integration:** Readiness surfaces as a morning-briefing section when actionable (not every day). Tone shifts between two modes based on the daily readiness score:

```
# High-energy day (score ≥ 0.7)
⚡ READINESS: Execution Mode
  Today: 3 deep-work blocks available (9am–11am, 1pm–3pm, 4pm–5pm)
  Energy: high — front-load complex tasks

# Low-energy day (score < 0.5)
🔋 READINESS: Recovery Mode
  Today: 1 deep-work block (10am–11:30am), 4 meetings
  Energy: low — reschedule non-critical 1:1s?
  ⚠ Context switches: 6 (threshold: 4) — consider batching
```

**Calendar energy flags:** Meetings flagged with energy cost indicators:
- `[deep]` — requires sustained focus
- `[admin]` — low-cognitive, batch-friendly
- `[social]` — relationship-building, flexible timing

**Command:** `domain readiness` — shows weekly energy trend, context-switching index, and deep-work block availability.

**Design rules:**
- Readiness NEVER auto-reschedules calendar events — proposes only
- Recovery mode is a tone shift, not an alert severity — no 🔴/🟠 prefix
- Score source: calendar density + meeting clustering + recent sleep/wellness signals (if available)
- Autonomy gate: ≥80% proposal acceptance over 14 shadow-mode days to advance to L2

### 26.2 Logistics Intelligence UX

**Briefing integration:** Logistics alerts surface only when deadlines approach (warranty expiry, renewal windows, maintenance cycles):

```
📦 LOGISTICS
  ⚠ HVAC filter replacement due (MERV-13, 20×25×4) — last changed 87 days ago
    → Shopping list: [Amazon deeplink]
  ℹ Progressive home insurance renewal: May 15
    → Comparison table ready — run `domain logistics --compare insurance`
```

**Comparison table format** (insurance renewal):

```
━━ INSURANCE COMPARISON (Home) ━━━━━━━━━━━━━━━━━━━━━
| Provider     | Annual | Deductible | Coverage  | Source       |
|──────────────|────────|────────────|───────────|──────────────|
| Progressive  | $1,840 | $1,000     | $350K     | current      |
| State Farm   | $1,620 | $1,500     | $350K     | web estimate |
| Allstate     | $1,710 | $1,000     | $350K     | web estimate |
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Note: Web estimates are approximate. Click deeplinks to get binding quotes.
```

**Command:** `domain logistics` — shows warranty tracker, upcoming renewals, maintenance schedule.

**Design rules:**
- Shopping lists include deeplinks (Amazon, manufacturer) — never auto-purchase
- Insurance comparisons sourced from public web search — clearly labeled "web estimate"
- Receipt parsing (Vision AI) populates `state/logistics.yaml` — user confirms before save
- Phase 1-2: No autonomous cart creation or broker API calls

### 26.3 Tribe Intelligence UX

**Briefing integration:** Tribe surfaces a reconnect radar when relationship decay scores cross thresholds:

```
👥 TRIBE
  Reconnect radar (3 contacts past threshold):
  • Rahul M. — last contact 47 days ago (close friend, threshold 30d)
  • Priya S. — last contact 62 days ago (extended family, threshold 45d)
  • Dev K. — last contact 91 days ago (professional, threshold 60d)
  Draft outreach? [yes / skip]
```

**Outreach draft staging:** Drafts are placed in Content Stage (`state/content_stage.md`) — never auto-sent:

```
━━ OUTREACH DRAFT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
To:      Rahul M. (WhatsApp)
Context: Last spoke about his new role at Google; your kids' birthdays
         are 2 weeks apart
Draft:   "Hey Rahul! Been thinking about you — how's the new gig
         treating you? We should get the kids together soon."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[send / edit / dismiss]
```

**Pre-meeting context brief:** Before meetings with known contacts, Artha injects relationship context:

```
📋 PRE-FLIGHT: Meeting with Rahul M. (2pm)
  Relationship: close friend, 12 years
  Last contact: 47 days ago (coffee at Starbucks, Jan 15)
  Recent context: Started new role at Google Cloud (from LinkedIn)
  Shared interests: kids' activities, hiking, tech career
```

**Command:** `domain tribe` — shows reconnect queue, relationship health scores, outreach history.

**Design rules:**
- Hard cap: **5 outreach drafts per catch-up run** — 6th and beyond silently dropped
- Drafts are NEVER auto-sent — staged in Content Stage for manual send
- Decay scoring is keyword-based over `social.md` interaction log
- WhatsApp send deferred pending Business API evaluation
- Autonomy gate: ≥80% draft acceptance to advance to L2

### 26.4 Capital Intelligence UX

**Briefing integration:** Capital surfaces cash flow projections and liquidity alerts:

```
💰 CAPITAL
  90-day cash flow projection:
    April: -$2,340 (property tax $8,059 due Apr 30)
    May:   +$1,820
    June:  +$2,150
  ⚠ April shortfall projected — proposal ready
```

**Amount-confirm gate** (mandatory for proposals >$200):

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Action:    Pause April Vanguard VTSAX purchase
Amount:    $500.00
Source:    state/finance.md line 47 — "auto-invest: $500 on 15th"
Rationale: Property tax $8,059 due April 30; projected shortfall $1,240
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Confirm amount to approve (type 500 or 'skip' to dismiss):
```

**Amount-confirmation mismatch behavior:**
- If user types amount that does NOT match proposal → proposal **rejected**
- Response: "Amount does not match proposal. Type CANCEL or the correct amount: $[amount]."
- Close-but-not-exact values (e.g., `$499` for `$500`) trigger re-prompt — no partial acceptance

**Low-confidence projection display:**

```
⚠ Low-confidence projection — 4 of 6 required months of data for 'dining'
  Variable categories (dining, shopping, entertainment): ≥6 months required
  Fixed categories (mortgage, utilities): ≥3 months required
```

**Command:** `domain capital` — shows 90-day projection, liquidity buffer status, investment pulse.

**Design rules:**
- >$200 proposals require user to re-type the exact dollar amount (not just `Y`) — anti-hallucination control
- Source citation mandatory on every proposal (state file + line number)
- If source cannot be cited, proposal is NOT generated
- Low-confidence warnings displayed prominently (not as footnotes)
- No account writes — L1 proposes only, user executes externally
- Autonomy gate: ≥80% acceptance over 30 days + no false positives on liquidity alerts to advance to L2

---

**Cross-references:**
- PRD v7.11.0: FR-20 Readiness Intelligence, FR-21 Logistics Intelligence, FR-22 Tribe Intelligence, FR-23 Capital Intelligence
- PRD v7.11.0: FR-19 Work Intelligence OS (FW-1–FW-26), Phase 2C
- Tech Spec v3.26.0: §19 Work OS Technical Architecture, §21 Evaluation Framework, §22 Knowledge Architecture, §25 Agent Framework
- Tech Spec v3.16.0: §19 Work OS (§19.1 overview, §19.4 connector protocol, §19.7 state files, §19.9 test coverage, §19.10 Product Knowledge Domain, §19.11 Reflection Loop)
- Tech Spec v3.6: §2 (Artha.md), §3.5 (Canvas LMS, Apple Health connector), §3.6 (Slash Commands + /diff), §4.4 (College Countdown schema), §4.10 (Decision Deadlines schema), §5.1 (Week Ahead, PII Footer, Calibration), §5.3 (Monthly Retrospective), §7.1–7.19 (pipeline steps), §8 (Security Model), §9.5 (Deep Agents Harness component reference), §18 (revision history)

---

## Appendix A — Approval UX Hardening

> Sourced from `specs/harden.md` v1.6 (archived). These UX behaviors apply to the action approval flow (§6, §7 of this spec) and are implemented in `scripts/action_executor.py` + `scripts/trust_enforcer.py`.

### A.1 Friction Floor Re-verification

Actions with `friction: high` or `autonomy_floor: true` are re-verified for explicit current-session approval at every execution attempt — regardless of prior session approvals. The approval prompt must clearly state:

> *"This action requires explicit approval each session. Please confirm."*

Rationale: High-friction actions (financial, communications to external parties) must never execute on stale approval carry-over. Session boundary is the trust unit.

### A.2 Wave 0 Gate UX

When `harness.wave0.complete: false`, any L2 autonomy elevation attempt surfaces a non-dismissable modal:

> *"This domain has not passed Wave 0 validation. L2 autonomy elevation is blocked until Wave 0 is closed. Use `--force-wave0 --justification '<reason>'` only for testing or emergency use — this override is session-scoped and leaves an audit trail."*

The override (`--force-wave0`) is visible only in CLI mode. In interactive/briefing mode, the gate is a hard block with no UI override option. Users are directed to complete Wave 0 validation to unlock elevation.

### A.3 UNCLASSIFIED Signal Queue

Signals that score below the TF-IDF threshold (target: 0.4) are never silently dropped. They appear in a dedicated section at the bottom of every briefing:

```
── UNCLASSIFIED SIGNALS (3) ──────────────────────────────────
These signals could not be automatically routed to a domain.

[1] Signal #abc123 (confidence: 0.27) — assign domain or dismiss
    > assign finance | assign immigration | dismiss
[2] Signal #def456 (confidence: 0.31) — assign domain or dismiss
    > assign health | assign work | dismiss
──────────────────────────────────────────────────────────────
```

UX rules:
- Confidence score always displayed — helps user calibrate trust in routing
- All valid domain names available as inline assign options
- Dismiss option always present — user explicitly acknowledges, never auto-discarded after X days
- Section is suppressed if queue is empty — never shows a "0 unclassified" placeholder

### A.4 Action Validator Error Messages

When the deterministic validator rejects an action, the UX message is deterministic (not LLM-generated):

| Rejection Cause | User-Facing Message |
| :--- | :--- |
| PII detected in payload | *"Action blocked: potential PII detected in parameters ([PII type], not value). Please re-propose with redacted parameters."* |
| Missing required fields | *"Action blocked: required fields missing for [action_type]: [field1], [field2]. Please re-propose with complete parameters."* |
| Scope violation | *"Action blocked: [Worker domain] cannot propose a [action domain] action. This is a routing error — please report."* |
| Friction floor (no current-session approval) | *"Action blocked: high-friction actions require explicit approval each session. Please review and confirm."* |

**Rule:** Never include the rejected payload content in the error message. Never ask the LLM to fix a validation failure — the action must be re-proposed from scratch.

---

## 27. OpenClaw Home Bridge — Interaction Design *(v3.15)*

The OpenClaw bridge produces three surfaces in the Artha UX: home events appearing in the catch-up briefing, WhatsApp draft approval in Telegram, and bridge health in `/status` output. All three surfaces follow Artha's core UX invariants: brief, actionable, and dismissable.

### 27.1 Home Events in the Catch-Up Briefing

When `state/home_events.md` contains unacknowledged events, they are surfaced as a `## 🏠 Home Events` section in the standard catch-up output, slotted after the `## 🔔 Alerts` block and before `## 📋 Open Items`:

```
## 🏠 Home Events

▸ [Yesterday 6:42 PM] Arjun arrived home — presence confirmed by HA
▸ [Yesterday 8:14 PM] Energy anomaly: HVAC drawing +40% above baseline
  > Dismiss | Investigate

No further home events in 24-hour window.
```

**UX rules:**
- Home events are suppressed from briefing if `state/home_events.md` is empty or all events are >48h old
- Each event shows relative time and source device/zone when available
- `energy_event` entries always include the delta from baseline
- Presence events show the `person` field if present in the OC envelope
- Events are not auto-dismissed — user explicitly dismisses or they age out at 48h
- Section is never shown if the bridge is disabled (`enabled: false` in `config/claw_bridge.yaml`)

### 27.2 Kid-Arrival Notification Pattern

When a `presence_detected` event fired while the user was away (e.g., during work), the event is promoted to the `## 🔔 Alerts` block rather than `## 🏠 Home Events`:

```
🟢 Arjun home since 3:17 PM — school day complete
   > Dismiss
```

**UX rules:**
- Promotion occurs when `person` field matches a configured kid name AND event time is within school-day window (configurable in `config/claw_bridge.yaml`)
- Alert is green (informational), never amber/red
- No action proposals are generated from presence events — information only
- Dismissed automatically after user reads next non-promoted briefing

### 27.3 WhatsApp Approval Workflow — Telegram Surface

WhatsApp drafts created by Artha (`whatsapp_draft` command) are surfaced for human approval in the OpenClaw Telegram bot UI before wacli delivery. Artha's role ends at draft creation; the approval UX lives in OC's Telegram interface, not in Artha's chat surface.

Artha-side representation (in briefing, if draft is pending >6h without action on OC side):

```
⏳ WhatsApp draft pending approval in OpenClaw
   To: [Family Group]   Created: 6h 12m ago
   Preview: "Flight confirmed — arrives Terminal 2, 4:15 PM..."
   > Resend reminder | Cancel draft
```

**UX rules:**
- Artha never sends WhatsApp directly — all delivery is through OC's wacli approval gate
- `To:` field always displays a descriptive label (never a phone number)
- Draft preview is truncated to 80 chars in briefing; full text available on OC side
- If draft is cancelled by Artha (`Cancel draft`), OC is notified via `whatsapp_draft` with `action: cancel`
- If >24h passes without OC action, draft is auto-expired and surfaced as a stale-draft alert

### 27.4 Bridge Health in `/status` Output

`/status` (or `status` in VS Code agent mode) includes a `Bridge` section when the OpenClaw bridge is enabled:

```
## Bridge

OpenClaw Home Bridge    ✅ healthy
  Last push:   14m ago          Last pong:  8m ago
  Clock drift: 0.4s             DLQ depth:  0
  Key version: v1               Messages:   ↑23 ↓11 (24h)
```

**Warning state** (any threshold exceeded):

```
OpenClaw Home Bridge    ⚠️ degraded
  Last push:   26h ago  ⚠️ >24h threshold
  Last pong:   3m ago
  Clock drift: 0.2s
  > Check bridge | Dismiss warning
```

**Critical / offline state**:

```
OpenClaw Home Bridge    🔴 offline
  Last push:   52h ago  🔴 >48h threshold
  Last pong:   unknown
  > Re-enable bridge | View logs
```

**UX rules:**
- Bridge section is suppressed from `/status` if `enabled: false` in `config/claw_bridge.yaml`
- Staleness warnings do not block or delay the main briefing
- `Check bridge` opens a brief diagnostic rundown (last error, retry count, DLQ contents summary)
- DLQ depth warning always includes `> Flush DLQ | Inspect items` inline options

### 27.5 TTS Announcement Confirmation

When Artha queues a TTS announcement via the `announce` command, the briefing includes a confirmation line:

```
📢 Announced via home TTS: "Raj, 3 P1 items and a dentist appointment today."
   Delivered 8:07 AM — presence confirmed
```

If announcement delivery failed (OC pong timed out before push):

```
📢 TTS announcement queued but not confirmed:
   "Raj, 3 P1 items and a dentist appointment today."
   Reason: No presence detected — sent to DLQ for retry
   > Cancel announcement | Retry now
```

**UX rules:**
- Announcement confirms are shown in briefing on the NEXT catch-up after delivery (not blocking)
- Failed announcements surface immediately as a non-blocking alert
- Text of announced content is shown verbatim (≤200 chars) — no summarization
- Artha never announces if bridge is disabled or last pong is >2h old (condition checked before `announce` command fires)

---

## 28. Artha Channel Integration — ACI Interaction Design

This section defines the UX patterns for all Artha Channel Integration (ACI) surfaces: workout logging via Telegram, watch alert delivery, brief request via OpenClaw, and query relay.

### 28.1 Workout Logging — Trigger Patterns & Acknowledgement

**Trigger:** User sends a workout message to `artha_ved_bot` Telegram bot.

**Accepted formats (examples):**
```
Ran 5.2 miles in 48 min, HR 155
Biked 12km, 1h 15m
Gym: squats 3×8 @185lb, bench 3×6 @145lb
Hiked 8 miles, 1400ft elevation, 3h 20m
```

**Parser behaviour:** Regex-based — zero LLM calls. Extracts: distance, duration, HR, elevation, weight/reps. Appends to `~/.artha-local/workouts.jsonl`. Dedup on `(sender_id, message_id)`.

**Acknowledgement format:**
```
✅ Workout logged
   Run · 5.2 mi · 48 min · HR 155

🎯 Fitness goal: Exercise 4×/week
   This week: 3 of 4 ✓ · Streak: 6 days
```

**Rules:**
- Ack always sent, even on partial parse (unparsed fields silently omitted — not errored)
- Goal progress lookup fails gracefully if `state/goals.md` is unavailable (ack without goal section)
- Duplicate messages (same `message_id`) silently dropped — no duplicate ack sent
- Workout data never stored in OneDrive-synced directories

### 28.2 Watch Alert Notification Design

**Delivery tiers (from watch_monitor.py urgency routing):**

**Tier 1 — Immediate Telegram Alert** (`urgency: high` + score > 50):
```
🚨 WATCH ALERT · [Topic Name]
──────────────────────────────
r/[subreddit] · score 847 · 234 comments

[Post title truncated to 80 chars]

▶ reddit.com/r/.../
```

**Tier 2 — Daily Digest** (`urgency: high` + score 20–50): Batched into the morning briefing under a `## 👁 Watch Alerts` section. Not sent as a standalone Telegram message.

**Tier 3 — Weekly Digest** (`urgency: medium` or `low`): Appears in Sunday summary only.

**Rules:**
- Tier 1 alerts fire immediately via `urllib` + keyring (not via bot polling loop)
- Score displayed is the Reddit score at time of fetch
- No LLM summarisation — title shown as-is (sanitised: 80-char cap, HTML stripped)
- Same `post_id` not repeated across digest cycles

### 28.3 Brief Request via Claw — Stale-While-Revalidate UX

When OpenClaw sends a `brief_request` M2M command, Artha applies stale-while-revalidate semantics.

**Fast path (< 5s):** Last briefing served immediately. Claw displays it with a `🔄 Refreshing...` footer while the pipeline runs asynchronously.

**Next brief_request:** Returns the refreshed briefing.

**Error states:**

| Condition | Response |
|-----------|----------|
| HMAC failure | Hard reject — no briefing served |
| No prior briefing | "No briefing available yet. Pipeline running..." + triggers fresh run |
| Pipeline timeout | Last briefing served with `⚠️ Stale — pipeline did not complete` footer |

**Rules:**
- HMAC always required — unauthenticated callers receive rejection with no data
- Stale threshold: serve if last briefing < 24h old; always trigger refresh
- Full catch-up briefing output delivered (not summarised)

### 28.4 Query Relay — Interaction Design

Claw sends a natural-language question; Artha synthesises an answer from local state files.

**Flow from user perspective (OpenClaw side):**
1. User types domain question in Claw interface
2. Claw sends `query_artha` M2M command
3. Claw shows: `🔍 Asking Artha...` (up to 120s wait)
4. Artha synthesises answer via LLM from local state
5. Answer appears in Claw (≤ 600 chars)

**Example interaction:**
```
User in Claw:
  "What are my active goals and which ones are at risk?"

[~15s later]

Artha:
  "3 active goals: Net Worth (on track, 62%), Exercise 4×/week
  (at risk — 2/4 this week), ByteByteGo course (behind — need
  2h this week). Exercise streak: 6 days."
```

**Blocked domain response:**
```
❌ That question touches health/finance data which isn't available
via the bridge. Ask directly in an Artha session.
```

**Timeout response** (> 120s elapsed):
```
⏱ Artha is taking longer than expected. Try again in a moment.
```

**Rules:**
- Questions touching ONLY vault-gated domains return the blocked response immediately (0 LLM calls)
- Mixed questions (some allowed + some blocked): allowed domains answered, blocked domains noted
- Answer always ≤ 600 chars — truncated with `...` if needed
- Never includes raw YAML or file contents — synthesised prose only

### 28.5 Error & Degradation Patterns

| Scenario | User-facing message | Recovery |
|----------|--------------------|-----------|
| HMAC key not provisioned | `🔴 Bridge auth not configured. Contact setup.` | Provision `artha-claw-bridge-hmac` in Credential Manager |
| artha_engine.py not running | No response to M2M commands | Check Task Scheduler; manual: `python scripts/artha_engine.py` |
| Reddit connector dead | `⚠️ Watch Monitor: Reddit down 2+ days. Signals paused.` | Auto-recovers on next successful fetch |
| All LLM providers timeout | `⚠️ Query timed out. Try again later.` | Retry; verify API keys |
| workouts.jsonl write failure | `⚠️ Workout logged but storage error — check ~/.artha-local/` | Verify disk space + directory permissions |

---

*Artha UX Spec v3.17 — End of Document*
