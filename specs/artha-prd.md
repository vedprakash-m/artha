# Artha — Personal Intelligence OS
## Product Requirements Document · v4.1

**Author:** Vedprakash Mishra
**Date:** March 10, 2026
**Status:** Active Development — Pull Model Architecture + OneDrive Sync Layer + PII Guardrails + Governance Framework + Multi-LLM & Action Framework + Operational Robustness + Task Integration + Phase 2A Intelligence + Supercharge Package + v4.0 Intelligence Amplification + **v4.1 WorkIQ Work Calendar Integration**
**Classification:** Personal & Confidential

> **v4.1 Changes — WorkIQ Work Calendar Integration:** FR-8 (Calendar & Time Intelligence) enhanced with 6 new features (F8.8–F8.13) for Microsoft corporate calendar integration via WorkIQ MCP. Employment domain activated with live meeting signals. New features: F8.8 Work Calendar Merge (unified personal + corporate calendar view), F8.9 Cross-Domain Conflict Detection (work↔personal with tiered Impact scoring), F8.10 Duration-Based Meeting Load Analysis (minutes-weighted, not count-based), F8.11 Partial Redaction Engine (sensitive codenames redacted locally before LLM transit), F8.12 Teams Meeting Join Actions (low-friction join proposals for imminent meetings), F8.13 Meeting-Triggered Employment OIs (critical meetings auto-create prep items). §11 Data Sources: added WorkIQ Calendar. §12 Privacy Model: added §12.7 WorkIQ privacy rules. §14 Success Criteria: added WorkIQ metrics. Platform constraint: WorkIQ available on Windows work laptop only; Mac catch-ups degrade gracefully. Compliance confirmed. Cross-references tech spec v2.2, UX spec v1.5.
>
> **v4.0 Changes — Intelligence Amplification (29 enhancements from deep expert review):** **Tier 1 — Goal Sprint & Alert Enhancement:** F13.17 Goal Sprint with real targets (mandatory target_value, default_target calibration per goal type), F13.18 Goal Auto-Detection (infer implicit goals from email/calendar patterns), new "Fastest Next Action" field on every alert (Mode 3 enhancement), targeted post-briefing calibration questions (F15.46), PII detection stats in briefing footer (F15.47). **Tier 2 — Weekly Intelligence & Scheduling:** Week Ahead Preview in Monday briefings (Mode 1 enhancement), calendar-aware task scheduling with suggested time slots on open items (F8.6), effort estimates + Power Half Hour micro-task batches (F15.48), decision deadlines with countdown on decisions.md entries (F15.24 enhancement), leading indicator auto-discovery (§8.11 enhancement). **Tier 3 — External Integrations:** Canvas LMS API direct integration (F4.10), Apple Health/HealthKit integration (F6.9), quarterly privacy audit report (F15.49), monthly retrospective auto-generation (F15.50). **Bonus Features:** WhatsApp Business bridge for message context (F1.7), India time zone alerts for family calls (F11.11), Parth college application countdown dashboard (F4.11), `/diff` command for state change visibility (F15.51), emergency contact wallet card generator (F18.8), "Ask Archana" delegation routing (F15.52), subscription ROI tracker (F12.6), tax season automation workflow (F3.13), "If You Have 5 Minutes" micro-task suggestions (F15.53), weekend planner (F8.7), "Teach Me" explainer mode (F15.54), natural language state queries (F15.55). §6 Interaction Modes: updated Mode 1 with Week Ahead Preview, Mode 3 with Fastest Next Action. §7: New feature items across FR-1, FR-3, FR-4, FR-6, FR-8, FR-11, FR-12, FR-13, FR-15, FR-18. §8.11: Leading indicator auto-discovery. §13 Roadmap: updated Phase 2A/2B/3 with new items. §14: Added success criteria for all new features. Cross-references tech spec v2.1, UX spec v1.4.
>
> **v3.9 Changes — Supercharge Package (18 enhancements from expert review):** **Data Integrity Guard (P0):** Added F15.28 — vault.sh pre-decrypt backup, write verification, net-negative write guard. Prevents data loss from session crashes overwriting modified state files. **Life Dashboard Snapshot:** Added F15.29 — `state/dashboard.md` auto-generated summary file providing single-glance family status across all domains, refreshed each catch-up. **Compound Signal Detection:** Added F15.30 — cross-domain signal correlation engine that detects convergent patterns (e.g., immigration deadline + financial pressure + work stress appearing simultaneously). **Proactive Calendar Intelligence:** Added F15.31 — forward-looking calendar analysis with logistical conflict detection and preparation recommendations. **Goal Engine → Coaching Engine:** FR-13 elevated from tracking to coaching — added F13.14 (implementation planning), F13.15 (obstacle anticipation), F13.16 (accountability patterns). **Bootstrap Command:** Added F15.33 — `/bootstrap` slash command for guided cold-start population of empty state files through structured interview. **Pattern of Life Detection:** Added F15.34 — 30-day behavioral baseline detection for spend patterns, communication rhythms, energy cycles, and calendar density. **Signal:Noise Ratio Tracking:** Added F15.35 — explicit tracking of items surfaced vs. acted upon, with per-domain noise scoring. **Open Items Enhancement:** F15.22 expanded with decision queue and delegation tracking. **Stale State Detection:** Added F15.36 — automated detection of state files that haven't been updated despite expected data flow. **Consequence Forecasting:** Added F15.37 — for each surfaced alert, project the consequence of inaction at 7/30/90 days. **Pre-Decision Intelligence Packets:** Added F15.38 — auto-generated research packets when Artha detects an upcoming decision point. **Session Quick-Start:** Added F15.39 — session-type detection and optimized context loading based on user's likely intent. **Briefing Compression Levels:** Added F15.40 — three briefing modes (full/standard/flash) selectable by user or auto-detected from context. **Context Window Pressure Management:** Added F15.41 — active monitoring and graceful degradation when approaching context limits. **OAuth Token Resilience:** Added F15.42 — proactive token health monitoring, pre-expiry refresh, and guided re-auth flow. **Email Volume Scaling:** Added F15.43 — progressive strategies for handling 2x–10x email volume without degradation. **Life Scorecard:** Added F15.44 — quarterly/annual life scorecard aggregating goal progress, domain health, and family well-being metrics. §13 Roadmap: updated Phase 2A with new items. §14 Success Criteria: added metrics for all new features. Cross-references tech spec v2.0, UX spec v1.3.
>
> **v3.8 Changes — Phase 2A Intelligence Workstreams:** Ten workstreams from expert review synthesis. **A: Relationship Intelligence** — FR-11 elevated from P2 to P1, renamed to "Relationship Intelligence & Social Fabric", expanded with F11.5–F11.10 (relationship graph model, communication pattern analysis, reciprocity ledger, cultural protocol intelligence, life event awareness, group dynamics tracking). **B: Goal Engine Leading Indicators** — Added §8.11 with leading indicator extraction per goal (not just lagging metrics). **C: Decision Graphs** — Added F15.24, new `state/decisions.md` for cross-domain decision tracking with `/decisions` command. **D: Life Scenarios** — Added F15.25, new `state/scenarios.md` for what-if analysis on high-stakes goals. **E: Email Pre-Processing Enhancement** — §9.4 step 5 upgraded with marketing/newsletter suppression, per-email token budget (1500 cap), batch summarization for >50 email batches. **F: Tiered Context Architecture** — Added §9.8 with Always/Active/Reference/Archive tiers and last_activity-based loading for 30–40% token savings. **G: ONE THING Reasoning Chain** — §9.4 step 8 enhanced with explicit URGENCY × IMPACT × AGENCY scoring protocol. **H: Digest Mode** — Added F15.26, §9.4 conditional for >48hr catch-up gaps with priority-tier grouping and "What You Missed" header. **I: Accuracy Pulse** — Added F15.27 with weekly self-assessment (proposed/accepted/declined/deferred actions, corrections, alert dismissals). **J: Privacy Surface Acknowledgment** — Added §12.6 documenting Claude API privacy surface. **#10: Action Friction** — Added `friction: low|standard|high` field to Action Framework (F15.20). §13 Roadmap: inserted Phase 2A between Phase 1C and Phase 2; Social/FR-11 moved from Phase 3 to Phase 2A. §14 Success Criteria: added metrics for relationship intelligence, leading indicators, decision graphs, accuracy pulse, digest mode. Cross-references tech spec v1.9, UX spec v1.2.
>
> **v3.7 Changes — Operational Robustness + Task Integration + Email Coverage:** Added from operational experience after first two live catch-ups. §9.4: Added Step 0 pre-catch-up go/no-go gate (pre-flight) — halts before fetching data if any critical integration is unhealthy; prevents silent-omission briefings. Added Step 6b: update open_items.md after domain processing. §9.4 now has 19 steps. Added §F15.22 (Persistent Open Items Tracking), §F15.23 (Briefing Archive Pipeline). Updated §11 Data Sources: formalised hub-and-spoke email forwarding model; added Apple iCloud forwarding; added Proton Mail integration decision (excluded/Bridge); added Microsoft To Do via Graph API as task synchronisation channel; documented email coverage matrix. Updated §9.4 to reference open_items.md in workflow. Added operational reliability NFRs: OAuth token auto-refresh validation, stale lock auto-cleanup, API quota guard with exponential backoff. Cross-references tech spec v1.7.
>
> **v3.6 Changes — Critical Assessment Hardening:** Incorporated 18 actionable items from independent critical assessment. Renamed CLAUDE.md references to Artha.md (primary instruction file loaded via thin CLAUDE.md). Fixed Mode 4 weekly summary trigger to pull-based model. Added `safe_cli.sh` outbound PII wrapper for Gemini/Copilot calls (§12.2). Added `contacts.md` to encrypted tier. Added extraction verification requirements for immigration and finance domains. Added `/health` slash command for system integrity checks. Updated Phase 1A roadmap with Gmail MCP validation budget (3–5 hrs), vault.sh crash recovery (OneDrive selective sync + LaunchAgent watchdog), Archana email access resolution (TD-18). Clarified `pii_guard.sh` as pre-persist filter (not MCP interceptor). Updated directory structure to show both CLAUDE.md (loader) and Artha.md (instructions). Cross-references tech spec v1.6.
>
> **v3.5.1 Changes — Gemini Review Hardening:** Updated §9.4 catch-up workflow with email content pre-processing step (HTML stripping, thread truncation, footer removal) and explicit deduplication instruction during domain processing. Expanded §9.7 Context Window Management with email pre-filtering strategy to prevent token bloat from HTML-heavy emails and long threads. Cross-references tech spec §6.1 dedup rules, §7.1 step 3b, TD-16, TD-17.
>
> **v3.5 Changes — Multi-LLM Orchestration & Action Execution Framework:** Added P9 design principle (Multi-model for cost and capability). Added F15.19 (Multi-LLM Orchestration), F15.20 (Action Execution Framework), F15.21 (Visual Message Generation). Updated §9.4 catch-up workflow with Gemini web research delegation and ensemble reasoning for high-stakes decisions. Expanded §10 Autonomy Framework with action types per trust level (email composition, WhatsApp messaging, calendar event creation, visual generation). Updated §11 Data Sources: WhatsApp changed from "privacy boundary" to "outbound via URL scheme (human-gated)"; web research sources now use Gemini CLI instead of "Claude web fetch". Added multi-LLM setup and action framework tasks to Phase 1A/1B roadmap. Added success criteria: multi-LLM cost savings, action proposal acceptance rate, visual generation count. Updated §15.7 Cost with Gemini/Copilot free-tier savings. Full implementation specified in tech spec §3.7 and §7.4.
>
> **v3.4 Changes — Governance & Evolution Framework:** Added P8 design principle (Self-improving and extensible). Added F15.16 (Component Registry), F15.17 (Self-Assessment Dashboard), F15.18 (Extensibility Wizard). Expanded §10 Autonomy Framework with elevation/demotion process and self-improvement-via-trust model. Added governance setup tasks to Phase 1A roadmap (registry.md, CLAUDE.md versioning, governance baseline). Added success criteria: domain addition time (<1 hour), per-domain accuracy (≥90%), AI feature adoption lag (<90 days). Full governance implementation specified in tech spec §12 (component registry, CLAUDE.md change management, domain lifecycle, MCP onboarding, data source addition, schema evolution, script lifecycle, feedback loop, AI feature adoption, hook/command governance, autonomy elevation).
>
> **v3.3 Changes — Pre-Flight PII Guardrails & Claude Code Capabilities:** Added device-local pre-flight PII filter (`pii_guard.sh`) that scans all email content before it reaches the Claude API — detects and replaces SSN, credit card numbers, bank routing/account numbers, passport numbers, A-numbers, ITIN, and driver's license numbers with safe `[PII-FILTERED-*]` tokens. Forms Layer 1 of defense-in-depth with existing LLM-based redaction rules (Layer 2). Added parallel email + calendar fetch using Claude Code's parallel tool invocation for faster catch-up. Specified utilization of Claude Code capabilities: custom slash commands (`/catch-up`, `/status`, `/goals`, `/domain`, `/cost`), hooks (auto-decrypt on start, auto-encrypt on stop), sub-agents (Phase 2 domain-specialized processing), and built-in memory (complements `memory.md`). Updated catch-up workflow, privacy model, Phase 1A roadmap, NFRs, and success criteria.

> **v3.2 Changes — OneDrive Sync Layer for Cross-Device State:** State files now live in a configurable OneDrive folder (`~/OneDrive/Artha`) synced across Mac, iPhone, and Windows. Eliminates device-specific state, manual snapshot uploads, and staleness. Sensitive state files (`sensitivity: high` or `critical`) are `age`-encrypted before sync — OneDrive stores `.age` files, encryption keys stay in device-local credential stores (macOS Keychain, Windows Credential Manager), never synced. Mac remains the sole writer (catch-up runs); iPhone and Windows are read-only consumers. The encrypted state tier (previously Phase 2 optional) is now a Phase 1 requirement. Updated architecture diagrams, directory structure, backup strategy, iPhone access pattern, and NFRs.
>
> **v3.1 Changes — Data Sensitivity & Document Repository Model:** Added data sensitivity classification to state file schema (`sensitivity: standard|high|critical`, `access_scope: full|catch-up-only|terminal-only`). Added document processing policy: extract-and-discard for sensitive documents (tax returns, brokerage statements, legal docs) — raw content never stored in state files. Expanded redaction rules from immigration-only to all sensitive domains (finance, tax, estate, insurance). Added briefing sensitivity filter: high/critical domains contribute summary-only lines in emailed briefings. Added iPhone snapshot exclusion for sensitive state files. Added encrypted state tier as Phase 2 item. Updated NFRs for sensitive document handling.
>
> **v3.0 Changes — Architectural Pivot to Pull Model:** Replaced push-based daemon architecture with a human-triggered pull model. Artha now runs as a Claude Code session with MCP tool connectors — no background daemon, no LaunchAgent, no always-on infrastructure. User triggers "catch me up" from Mac; outputs delivered via email for cross-device access (iPhone, Windows). All state remains local on Mac. Eliminated: F15.1 (daemon), F15.13 (daemon runtime), F15.14 (daemon observability), event-driven push processing, FastAPI approval dashboard. Simplified: Mode 3 (batch alerting on pull), Mode 6 (check-in integrated into catch-up). Architecture section fully rewritten (Section 9). Roadmap simplified. NFRs updated for pull cadence.
>
> **v2.2 Changes:** Incorporated architectural robustness and coverage feedback. Added F2.7 (Dependent Age-Out Sentinel / CSPA — P0), F3.12 (Credit Card Benefit Optimizer), F17.9 (Lease & Lifecycle Manager), F17.10 (TCO Calculator). Enhanced F15.1 with deterministic pre-processor pipeline, F15.6 with batch approval dashboard, F15.13 with local mail cache fallback. Added event sourcing pattern to state store, integration adapter pattern (Section 9.6), context window management & RAG strategy (Section 9.7), and geographic portability NFR (Section 15.6).
>
> **v2.1 Changes:** Full household coverage audit. Added FR-16 (Insurance & Risk Management), FR-17 (Vehicle Management), FR-18 (Estate Planning & Legal Readiness). Expanded FR-3 (tax preparation, insurance premiums), FR-4 (paid enrichment, activity costs), FR-6 (open enrollment, employer benefits), FR-7 (telecom, trash/recycling, HOA, landscaping, property tax, emergency preparedness). Updated Life Data Map with four new domains. Updated architecture, data sources, roadmap, and success criteria.
>
> **v2.0 Changes:** Incorporated feedback from two independent expert reviews. Major additions: AI-Native Intelligence Principles, Semantic Reasoning Layer, expanded Goal Intelligence Engine (conflict detection, forecasting, behavioral nudges, dynamic replanning, seasonal awareness, conversational creation), Proactive Check-in mode, Non-Functional Requirements, model tiering strategy, ambient daemon runtime specification, layered data source strategy, and resolved Open Questions.

---

## Table of Contents

1. [Vision & Philosophy](#1-vision--philosophy)
2. [The Problem Artha Solves](#2-the-problem-artha-solves)
3. [Design Principles](#3-design-principles)
4. [How Artha Relates to Vega](#4-how-artha-relates-to-vega)
5. [Life Data Map](#5-life-data-map)
6. [The Six Interaction Modes](#6-the-six-interaction-modes)
7. [Functional Requirements](#7-functional-requirements)
   - FR-1: Communications Intelligence
   - FR-2: Immigration Sentinel
   - FR-3: Financial Command Center
   - FR-4: Kids & School Intelligence
   - FR-5: Travel & Loyalty Management
   - FR-6: Health & Wellness Radar
   - FR-7: Home & Property Management
   - FR-8: Calendar & Time Intelligence
   - FR-9: Shopping & Commerce Intelligence
   - FR-10: Learning & Development Tracker
   - FR-11: Relationships & Social Fabric
   - FR-12: Digital Life Management
   - FR-13: Goal Intelligence Engine
   - FR-14: Work-Life Boundary Guardian
   - FR-15: Artha OS Core
   - FR-16: Insurance & Risk Management
   - FR-17: Vehicle Management
   - FR-18: Estate Planning & Legal Readiness
8. [Goal Intelligence Engine — Deep Dive](#8-goal-intelligence-engine--deep-dive)
9. [Architecture](#9-architecture)
10. [Autonomy Framework](#10-autonomy-framework)
11. [Data Sources & Integrations](#11-data-sources--integrations)
12. [Privacy Model](#12-privacy-model)
13. [Phased Roadmap](#13-phased-roadmap)
14. [Success Criteria](#14-success-criteria)
15. [Non-Functional Requirements](#15-non-functional-requirements)
16. [Open Questions — Resolved](#16-open-questions--resolved)

---

## 1. Vision & Philosophy

**Artha** (Sanskrit: अर्थ — *purpose, wealth, meaning*) is your personal intelligence operating system.

In the Purusharthas — the four aims of human life in Sanskrit philosophy — Artha is one of the four pillars: *Dharma* (duty), **Artha** (purpose and material well-being), *Kama* (pleasure and fulfillment), and *Moksha* (liberation). Artha is chosen deliberately: this system serves your material life — finances, family, home, health — while keeping the larger picture of what you're building toward in focus.

Modeled on the ambition of Vega, which manages your professional life as a Senior TPM at Microsoft, Artha manages everything outside of work: your family, finances, immigration journey, health, home, learning, and personal goals.

Where Vega makes you a more effective engineer and program manager, Artha makes you a more present husband, an engaged father, an informed investor, and an intentional learner. Together, they cover the full spectrum of who you are.

Artha is not a dashboard. It is not a to-do app. It is not a notification router or an email summarizer. It is a **family-aware, privacy-first, goal-centered personal operating system** that continuously converts fragmented life data into prioritized decisions, forward-looking guidance, and friction-reducing actions.

---

## 2. The Problem Artha Solves

Your personal life is fragmented across more than 15 services, 2 email accounts, 5+ financial institutions, 3 school communication channels, immigration attorneys, utility providers, and dozens of subscriptions. There is no single place that knows all of it.

**The three dominant pain patterns, discovered from data:**

### Pattern 1 — High-Volume Noise
ParentSquare generates 90–100 emails per month across three simultaneous streams: Tesla STEM High School (Parth), Inglewood Middle School (Trisha), and Lake Washington School District. Mixed in are teacher emails, attendance alerts, missing assignment notices, and grade reports. There is no consolidation, no triage, no filter between "Parth was marked absent" (urgent) and "Spirit Week reminder" (not urgent).

### Pattern 2 — High-Stakes Silence
Immigration deadlines, passport expiry windows, H4-EAD renewal timelines, and GC milestone dates are tracked nowhere. They live in email threads, PDFs, and attorney correspondence — none of which proactively alerts you when a 90-day action window opens. The cost of missing one of these is measured in months or years.

### Pattern 3 — Fragmented Finance
Bills, subscriptions, investments, loans, and credit monitoring are spread across 8+ institutions: Chase, Fidelity, Vanguard, Morgan Stanley, E*Trade, Wells Fargo, Discover, and HDFC NRI. Assembling a complete financial picture requires logging into all of them. No single signal tells you whether you are on track toward your goals.

**What this costs today:**
- Time spent triaging 112+ unread emails that span urgent and trivial in the same inbox
- Anxiety from not knowing where key deadlines sit
- Financial opacity across a complex multi-institution portfolio
- No measurement of progress toward any personal goal
- Family coordination happening reactively (Parth missed? Find out at dinner) rather than proactively

---

## 3. Design Principles

**P1 — Clarity over noise.** Artha defaults to silence. It speaks only when it has something worth saying. The signal-to-noise ratio is a product commitment, not an afterthought. An alert system that cries wolf becomes invisible.

**P2 — Human-gated by default.** Artha reads everything but writes nothing without permission. It observes all your data, synthesizes across domains, and recommends actions — but the humans in this family retain all control over what actually happens.

**P3 — Goals above tasks.** Every feature in Artha ultimately serves a goal you have defined. An energy bill alert matters because it serves a financial goal. A school attendance flag matters because it serves a parenting goal. Features without a goal connection are deprioritized.

**P4 — Family-aware.** Artha understands that you are not alone. Archana, Parth, and Trisha are first-class citizens in Artha's world model. Artha tracks what matters to each family member, not just to you.

**P5 — Privacy by architecture.** Your data never leaves your devices or trusted cloud storage without your explicit consent. No third-party analytics. No training on your data. Local-first state storage.

**P6 — Earned autonomy.** Like Vega, Artha starts at Trust Level 0 (observe and report) and earns the right to act through demonstrated reliability. It does not rush toward autonomy; it earns it. See Section 10 for the full autonomy framework.

**P7 — AI-native intelligence.** Artha is not a dashboard with an AI add-on. It is an AI-first system that reasons, infers, predicts, and remembers. Every signal passes through a semantic reasoning layer before surfacing. Artha infers intent from communication patterns, reasons across domains to discover non-obvious connections, forecasts future risk before thresholds are crossed, and remembers prior decisions and preferences to improve over time. The difference between a notification system and an intelligence system is reasoning — Artha reasons.

**P8 — Self-improving and extensible.** Artha measures its own accuracy, learns from corrections, and adopts new AI capabilities as they become available. Adding a new data source, domain, or integration follows a documented checklist — not a code rewrite. The system is designed to grow as trust and utility grow: new email accounts, document repositories, MCP servers, and AI features can be absorbed without architectural changes. Governance processes ensure this growth is deliberate, tested, and reversible.

**P9 — Multi-model for cost and capability.** Artha uses the right LLM for the right task at the right cost. Claude handles orchestration, state management, and MCP tool access. Gemini CLI provides free web research, URL summarization, and AI visual generation. Copilot CLI provides free code/config validation. For high-stakes decisions (immigration, finance, estate), all three models generate responses and Claude synthesizes the best answer. No single-vendor lock-in — the multi-LLM layer maximizes capability while minimizing cost.

---

## 4. How Artha Relates to Vega

Artha and Vega are complementary systems, not competing ones. Together they cover your full life.

| Dimension | Vega (Work OS) | Artha (Personal OS) |
|---|---|---|
| Domain | Microsoft engineering work | All personal life domains |
| Primary data | ADO, IcM, Teams, GitHub, SharePoint | Gmail, Outlook, OneDrive, banks, schools, utilities |
| Users | Vedprakash | Family: Vedprakash, Archana, Parth, Trisha |
| Urgency model | Incident and deadline-driven | Deadline, goal, and family-driven |
| Autonomy model | Earned trust model | Same — mirror Vega exactly |
| Interaction | VS Code + Chat | Morning briefing, chat, alerts |
| Success metric | Engineering velocity | Life quality, goals achieved |

**The boundary between them is strict.** Artha does not read work emails, Teams messages, ADO items, or SharePoint documents. It has exactly one signal from Vega's world: a work-health indicator that surfaces when work hours are encroaching on personal time (see FR-14). That signal flows one way — from Vega's domain into Artha's awareness — and it never flows back.

---

## 5. Life Data Map

*Based on analysis of OneDrive, Gmail (38,671 messages), and Outlook.com.*

| Domain | Key People | Key Services | Friction Level |
|---|---|---|---|
| Communications | All family | Gmail, Outlook, ParentSquare | 🔴 High |
| Immigration | Vedprakash, Archana, Parth, Trisha | Fragomen, USCIS, Microsoft Immigration | 🔴 High |
| Finance | Vedprakash, Archana | Chase, Fidelity, Vanguard, Morgan Stanley, E*Trade, Wells Fargo, Discover, HDFC NRI | 🔴 High |
| Kids & School | Parth (11th, Tesla STEM), Trisha (7th, Inglewood MS) | LWSD, ParentSquare, Canvas | 🔴 High |
| Travel | All family | Alaska Airlines, Marriott Bonvoy, Avis, Expedia | 🟡 Medium |
| Health | All family | Regence/BCBS, HSA, Providence | 🟡 Medium |
| Home | Vedprakash, Archana | Wells Fargo (mortgage), PSE Energy, Sammamish Plateau Water, King County, Home Assistant, ISP, Republic Services | 🟡 Medium |
| Calendar | All family | Google Calendar, Outlook Calendar | 🟡 Medium |
| Shopping | All family | Amazon, Costco, local | 🟢 Low |
| Learning | Vedprakash | ByteByteGo, Kaggle, Obsidian, UW Foster (alumni) | 🟡 Medium |
| Social | Vedprakash, Archana | Friends, family, temple | 🟢 Low |
| Digital Life | Vedprakash | 40+ subscriptions, Home Assistant, passwords | 🟡 Medium |
| Insurance | Vedprakash, Archana | Auto insurer, homeowners insurer, umbrella, Microsoft benefits (life/disability) | 🟡 Medium |
| Vehicles | Vedprakash, Archana | WA DOL, service providers, NHTSA recalls | 🟡 Medium |
| Estate Planning | Vedprakash, Archana | Estate attorney, financial account beneficiaries, guardianship docs | 🔴 High |
| Emergency Prep | All family | FEMA, King County Emergency Management, Cascadia Subduction Zone readiness | 🟡 Medium |
| Goals | All family | (Artha-native — no existing tool) | 🔴 High |

---

## 6. The Six Interaction Modes

Artha operates in six modes simultaneously. They are not separate features — they are six windows into the same intelligence layer.

---

### Mode 1 — Morning Briefing

**Trigger:** User-initiated via `catch me up` command in Claude Code on Mac, or on first interaction of the day. Output emailed for cross-device access.
**Format:** Structured Markdown brief, delivered to terminal and emailed to configured address
**Duration:** Designed to be read in under 3 minutes

**Briefing structure:**

```
ARTHA · [Day], [Date]

TODAY
  • [Urgent items — bills due, appointments, deadlines]
  • [Immigration: if any window is <90 days]
  • [Kids: attendance flags, tests today, deadlines]

THIS WEEK
  • [Top 3 items that need a decision or action]
  • [Upcoming key dates]

WEEK AHEAD  *(v4.0 — Monday briefings only)*
  • [5 most complex logistics items for the coming week]
  • [Calendar density: N events across M days — light/normal/heavy]
  • [Preparation items needed before Thursday]

GOALS
  • [2-3 goal progress signals — on track / at risk / behind]

ONE THING
  • [The single most important thing Artha wants you to know today]

PII GUARD  *(v4.0 — footer)*
  • [PII filter stats: N items scanned, M detections, 0 leaks]
```

Artha does not pad the briefing. If nothing is urgent, the "Today" section says so. The WEEK AHEAD section appears only on Monday briefings (or the first catch-up of a new week) and surfaces the logistics items that need advance coordination. The PII GUARD footer provides transparency into the pre-flight PII filter's operation.

---

### Mode 2 — On-Demand Chat

**Trigger:** Any time during a Claude Code session on Mac, or via Claude iOS app with cached state in a Claude Project
**Latency:** <10 seconds for state-file queries, <30 seconds for queries requiring email fetch
**Knowledge:** Artha answers from its local Markdown state files, updated on each catch-up run. iPhone access via Claude Project with uploaded state snapshots (read-only, periodically refreshed).

**Example queries Artha can answer:**
- "What bills are due this week?"
- "How many days until Archana's H4-EAD expires?"
- "What did we spend at Amazon last month?"
- "Is Parth's SAT registration confirmed?"
- "How much is left on our Wells Fargo mortgage?"
- "Am I on track for my savings goal this year?"
- "What's happening with my GC case?"
- "How many learning hours have I logged this month?"
- "When's the next family trip and what's booked?"
- "What subscriptions renewed this month?"

---

### Mode 3 — Batch Alert Review

**Trigger:** Runs as part of each catch-up session — not continuously in the background
**Output:** Alerts surfaced in the catch-up briefing and emailed if critical

On each catch-up, Artha processes all emails since the last run and evaluates threshold crossings in batch. Because personal life obligations operate on days/weeks/months timescales (not minutes), batch alerting on a daily or every-other-day pull is sufficient for every domain in Artha.

**Alert severity levels (unchanged — evaluated in batch):**
- 🔴 **Critical** — Immigration deadline <30 days, bill overdue, document expiring, attendance emergency. Emailed immediately during catch-up.
- 🟠 **Urgent** — Immigration window <90 days, bill due within 3 days, low assignment score, unusual spend
- 🟡 **Heads-up** — Upcoming renewal, passport <6 months, learning goal behind schedule
- 🔵 **Info** — Weekly goal summary, monthly financial snapshot

**Fastest Next Action** *(v4.0)*: Every alert at 🔴 or 🟠 severity includes a concrete next action — not just what's wrong, but the single fastest thing you can do about it right now. Example: "🔴 PSE bill $300.63 overdue → **Pay now:** log into pse.com or call 1-888-225-5773." "🟠 Parth missing AP Language assignment → **Next action:** ask Parth tonight, email teacher Zebrack-Smith if unresolved by tomorrow."

**Alert latency:** Worst-case delay = time between catch-up runs. At daily cadence, all alerts surface within 24 hours of the triggering email. For immigration and finance (deadlines measured in weeks/months), this is more than sufficient.

---

### Mode 4 — Weekly Summary

**Trigger:** Generated when the user runs catch-up on Sunday, or during the first catch-up after Sunday 8:00 PM Pacific. If no catch-up occurs over the weekend, the weekly summary is prepended to Monday's first catch-up.
**Format:** Structured Markdown brief, longer than the daily briefing

**Summary structure:**
- **Week in Review:** What happened across domains — highlights only
- **Kids This Week:** Academic, attendance, activity summary per child
- **Finance This Week:** Spending vs. budget, any anomalies, account changes
- **Goals Progress:** Each active goal — this week's movement, trend, status
- **Coming Up:** The 5 most important items in the week ahead
- **Artha Observations:** Patterns Artha noticed that you should know about

---

### Mode 5 — Goal Intelligence Engine

**Trigger:** Always on; powers sections of both briefing and weekly summary
**The most distinctive feature of Artha** — see Section 8 for full specification.

At its core: you define goals. Artha attaches metrics to them. Artha tracks progress automatically from connected data sources. Artha reports weekly and flags when you're off track.

---

### Mode 6 — Proactive Check-in

**Trigger:** Integrated into the catch-up flow — when data suggests intervention would help, Artha surfaces check-in questions at the end of the briefing. The user can also explicitly ask "check in with me" during any Claude Code session.
**Format:** Short conversational micro-interaction (2–3 targeted questions)
**Duration:** Designed to take <2 minutes to respond

Check-ins are interactive micro-conversations surfaced during catch-up sessions when data shows drift from goals or emerging patterns. Because Artha runs interactively (not as a background process), the check-in IS part of the conversation — no separate trigger needed.

**Example check-in:**

```
ARTHA CHECK-IN · Friday 6:00 PM

Hey — quick check-in on your week:

1. No exercise logged this week, and your learning goal is 3 days behind.
   Work hours ran long Tuesday and Thursday (detected late emails both nights).
   Want me to block Saturday morning for a workout and a ByteByteGo session?

2. Parth has 2 overdue assignments in AP Language.
   Want me to surface this in tomorrow's briefing for Archana too?

3. Your Amazon spend is already at 85% of monthly target with 10 days left.
   Flag before next checkout? [Yes / No / Adjust target]
```

**Check-in intelligence:**
- Only triggered when data shows drift from goals or emerging patterns
- Cross-references calendar availability before suggesting time blocks
- Remembers prior responses (“stop asking about Spirit Week”)
- Adapts timing to your response patterns

---

## 7. Functional Requirements

---

### FR-1 · Communications Intelligence

**Priority:** P0
**Summary:** Reduce inbox noise, surface what requires action, and route messages intelligently.

**The problem:** 112 unread emails in Outlook alone. Gmail has 38,671 messages. ParentSquare generates 90–100/month across 3 streams. Learning newsletters arrive daily. The urgent and trivial arrive in the same place with the same visual weight.

**Data sources:**
- Gmail (mi.vedprakash@gmail.com)
- Outlook.com (vedprakash.m@outlook.com)
- ParentSquare (via email digest)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F1.1 | **School Digest Consolidator** — Merge all ParentSquare + LWSD + Canvas emails into one daily summary per child. Parth summary. Trisha summary. District summary. Suppress individual delivery emails. | P0 |
| F1.2 | **Action Item Extractor** — Scan all incoming email for deadlines, required actions, registration links, and RSVP requests. Surface in Morning Briefing. | P0 |
| F1.3 | **Sender Intelligence** — Classify senders by domain: immigration, finance, school, utility, shopping, learning, social. Weight alerts by domain priority. | P1 |
| F1.4 | **Newsletter Digest** — Consolidate learning newsletters (ByteByteGo, System Design, Big Technology, TED Recommends) into a weekly reading digest. Suppress individual deliveries. | P1 |
| F1.5 | **Subscription Radar** — Detect renewal notices, price change notifications, and new subscription activations from email. Flag for review. | P1 |
| F1.6 | **USPS Informed Delivery Integration** — Parse daily mail scans. Flag important physical mail (legal documents, checks, government notices). | P2 |
| F1.7 | **WhatsApp Business Bridge** *(v4.0)* — Integration with WhatsApp Business API or web bridge to pull message context into Artha's relationship intelligence. Enables: awareness of WhatsApp group activity (temple community, school parent groups, family groups), detection of messages requiring response, and threading WhatsApp context into the reconnect radar (F11.3). Privacy: message content is processed and discarded; only metadata (sender, timestamp, group, has_response) is stored in state. Respects existing human-gated outbound pattern (URL scheme). | P2 |

---

### FR-2 · Immigration Sentinel

**Priority:** P0
**Summary:** Track every immigration deadline, document expiry, and case milestone for all four family members — proactively.

**The problem:** The family is in a complex, multi-year immigration process. Vedprakash holds an H-1B (transferred to Microsoft, June 2024, case #032100006948). Archana and the children hold H-4 visas (approved July 2024). Archana holds an H-4 EAD. I-140 is approved. PERM/GC process is active via Fragomen. Missing a single deadline in this chain can have consequences measured in months or years.

**Data sources:**
- Outlook.com (Microsoft Immigration team emails — usimmig@microsoft.com)
- Gmail (Fragomen correspondence)
- OneDrive (immigration documents folder)
- Manual input (document expiry dates, attorney updates)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F2.1 | **Family Immigration Dashboard** — Single view of all active documents, their expiry dates, and status for all four family members. | P0 |
| F2.2 | **Deadline Alert Engine** — Proactive alerts at 180, 90, 60, 30, and 14 days before any document expiry or filing deadline. Never miss a window. | P0 |
| F2.3 | **Case Timeline Tracker** — Track GC process milestones: PERM filing → PERM approval → I-140 filing → I-140 approval (done) → Priority Date → I-485 filing eligibility. Current priority date vs. Visa Bulletin. | P0 |
| F2.4 | **Document Vault Index** — Index all immigration documents stored in OneDrive. Know what exists, where it is, and when it expires. | P1 |
| F2.5 | **Attorney Correspondence Log** — Parse Fragomen and Microsoft Immigration emails into a structured log. Summarize latest status in Morning Briefing when anything changes. | P1 |
| F2.6 | **Visa Bulletin Monitor** — Monthly monitoring of the USCIS Visa Bulletin for EB-2/EB-3 India priority date movements. Alert when the date advances. | P2 |
| F2.7 | **Dependent Age-Out Sentinel (CSPA)** — Track Child Status Protection Act (CSPA) age calculations for Parth and Trisha. H-4 dependents "age out" at 21 and lose derivative status unless protected by CSPA. Parth is approaching this window. Calculate CSPA age = biological age minus time I-140 was pending. Monitor continuously. If CSPA protection is insufficient, trigger F-1 student visa transition planning well before age-out. Alert at 36, 24, 12, and 6 months before projected age-out date. This is the highest-stakes derived deadline in the immigration domain. | P0 |

**Document registry (initial):**

| Document | Holder | Status | Action Window |
|---|---|---|---|
| H-1B | Vedprakash | Active (Microsoft, June 2024) | Track expiry |
| H-4 | Archana | Approved July 2024 | Track expiry |
| H-4 | Parth | Approved July 2024 | Track expiry |
| H-4 | Trisha | Approved July 2024 | Track expiry |
| H-4 EAD | Archana | Active | Track expiry — renewal lead time: 6 months |
| I-140 | Vedprakash | Status requires verification — PRD and state file conflict. Run `/bootstrap` to confirm with user. | Monitor priority date |
| Passports | All four | Verify expiry dates | Alert at 6 months |

---

### FR-3 · Financial Command Center

**Priority:** P0
**Summary:** Unified visibility across all financial accounts, proactive bill management, net worth tracking, and goal-linked budget awareness.

**The problem:** Financial life spans 8+ institutions with no unified view. Bills arrive by email with no consolidation. Investment performance requires separate logins per account. Net worth is never known in real time.

**Data sources:**
- Gmail/Outlook (bill notifications, account alerts from all institutions)
- Manual input (account balances, loan amounts)
- Connected APIs where available (Fidelity, Chase — read-only)

**Account inventory:**

| Institution | Type | Email Source |
|---|---|---|
| Chase | Checking / Savings / Credit | alerts@chase.com |
| Fidelity | Investment + Credit Card | Fidelity emails |
| Vanguard | Retirement (401k/IRA) | Vanguard alerts |
| Morgan Stanley | Investment | MS alerts |
| E*Trade | Brokerage | ETrade alerts |
| Wells Fargo | Mortgage + FICO monitoring | alerts@wellsfargo.com |
| Discover | Credit card | Discover alerts |
| HDFC NRI | NRI banking (India) | HDFC alerts |
| HSA | Health Savings Account | HSA provider alerts |

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F3.1 | **Bill Calendar** — Parse all bill notification emails (PSE Energy, Sammamish Water, King County, all credit cards, mortgage) into a unified bill calendar. Alert 5 days before due date. Current: PSE $300.63 due 3/26. | P0 |
| F3.2 | **Net Worth Snapshot** — Aggregate estimated net worth across all accounts on demand and in weekly summary. Manual update with auto-prompt from account statements. | P0 |
| F3.3 | **Unusual Spend Alert** — Flag transactions that deviate significantly from category baselines (e.g., Amazon spend 2x monthly average). | P0 |
| F3.4 | **Subscription Ledger** — Maintain a living list of all active subscriptions with amount, renewal date, and category. Detect new subscriptions from email. | P1 |
| F3.5 | **Credit Health Monitor** — Parse Wells Fargo FICO alerts (monthly). Track score trend. Alert on significant drops. | P1 |
| F3.6 | **Tax Document Tracker** — During Jan–April, track which tax documents have arrived per institution (1099, W-2, mortgage interest, etc.). Alert when all expected docs are received. | P1 |
| F3.7 | **Mortgage Tracker** — Track Wells Fargo mortgage balance, monthly payment, and payoff timeline. Annual check-in with banker (BRAND, DEANNA) trigger. | P1 |
| F3.8 | **HDFC NRI Monitor** — Track HDFC NRI account for balance alerts and transaction notifications. Currency conversion aware (USD/INR). | P2 |
| F3.9 | **Predictive Spend Forecasting** — Project monthly and annual spending by category based on historical patterns. Alert when current trajectory will exceed budget. Account for seasonal spikes (holiday spending, back-to-school, tax season). Example: “Amazon spend is 40% above YoY average through February. At this rate, annual discretionary budget will be exceeded by August.” | P1 |
| F3.10 | **Tax Preparation Manager** — Beyond document tracking (F3.6): maintain CPA/tax preparer contact and engagement schedule, track estimated quarterly tax payments (federal + WA has no state income tax — but track if applicable for other states), surface tax optimization prompts (“Max out 401k by year-end: $X remaining of $23,500 limit”, “HSA contribution gap: $Y remaining”, “529 contribution for WA state benefit”), and track filing status and refund/payment outcome. | P1 |
| F3.11 | **Insurance Premium Aggregator** — Pull total annual insurance cost from FR-16 into the financial picture. Surface in net worth and monthly expense views. “Total annual insurance spend: $X (auto: $A, home: $B, umbrella: $C). Up 8% from last year.” | P1 |
| F3.12 | **Credit Card Benefit Optimizer** — Map embedded card benefits (rental car damage waivers, purchase protection, extended warranty, travel insurance, lounge access, price protection) to each card in the account inventory. When a booking or purchase confirmation is detected, proactively surface the best card to use. Example: "You booked an Avis rental. Your Chase Sapphire provides primary rental car damage waiver — use it to decline Avis CDW and save ~$25/day." Also surface quarterly rotating category bonuses and annual benefit deadlines (airline credits, hotel credits). | P1 |
| F3.13 | **Tax Season Automation** *(v4.0)* — Automated tax preparation workflow during Jan–April. Goes beyond document tracking (F3.6) with an active checklist: (1) Track document arrival with expected-vs-received matrix (W-2, all 1099s, mortgage interest, property tax, charitable donations, HSA contributions), (2) Auto-generate CPA submission packet when all docs received, (3) Surface tax optimization actions with deadlines ("Last day for prior-year IRA contribution: April 15"), (4) Track estimated quarterly tax payment schedule and amounts, (5) Monitor filing status and refund/payment outcome. Integrates with F3.10 (Tax Preparation Manager) but adds active workflow automation. | P1 |

---

### FR-4 · Kids & School Intelligence

**Priority:** P0
**Summary:** Real-time awareness of both children's academic standing, attendance, upcoming tests and deadlines, and extracurricular activities — consolidated and actionable.

**The problem:** Parth (11th grade, Tesla STEM HS) and Trisha (7th grade, Inglewood MS) generate a high volume of school communications with no triage. Missing assignments, attendance issues, and test registrations (SAT) get buried in the same stream as routine newsletters.

**Data sources:**
- Outlook.com (ParentSquare emails — all three streams)
- Gmail (Canvas, LWSD, teacher emails)
- Manual input (extracurricular schedules)

**Parth profile (Tesla STEM HS, 11th grade):**
- SAT scheduled: March 13, 2026
- Extracurriculars: Tesla Economics Club (National Personal Finance Challenge)
- Courses: AP-level curriculum, Economics (teacher: Zebrack-Smith among others)
- Known alerts received: Low assignment score, missing assignment

**Trisha profile (Inglewood MS, 7th grade):**
- Courses: Science (teacher: Niles), AP Language & Composition (advanced)
- Currently reading: *Just Mercy* (AP Language seminar, chapters 4–9)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F4.1 | **Daily School Brief** — One consolidated morning summary per child: attendance status, assignments due today, upcoming tests, any school alerts. Replaces ParentSquare inbox flood. | P0 |
| F4.2 | **Grade & Assignment Alert** — Immediately flag missing assignments and low scores (both children). Parse LWSD automated alert emails. Surface in Morning Briefing. | P0 |
| F4.3 | **Test & Deadline Calendar** — Track standardized tests (SAT, PSAT, AP exams), enrollment deadlines, and school calendar events. Parth's SAT 3/13 already registered — track as standing item. | P0 |
| F4.4 | **Attendance Tracker** — Log attendance notifications. Alert on absence patterns. Track cumulative absences per school year per child. | P1 |
| F4.5 | **College Prep Tracker (Parth)** — Track SAT scores, college research milestones, application deadlines, and financial aid timelines as Parth approaches senior year. | P1 |
| F4.6 | **Extracurricular Tracker** — Track club meetings, competitions, and deadlines for Parth (Economics Club, National Personal Finance Challenge) and Trisha. | P1 |
| F4.7 | **Teacher Communication Log** — Log emails from specific teachers for each child. Make them searchable and summarizable. | P2 |
| F4.8 | **Paid Enrichment Tracker** — Track paid extracurricular activities, tutoring, sports leagues, music lessons, and summer camps for both children. Include: enrollment dates, costs (linked to FR-3 spend tracking), schedule, provider contact. Alert on registration windows: "Summer camp registration typically opens in March. Last year you enrolled Trisha in [camp]. Register again?" | P1 |
| F4.9 | **Activity Cost Summary** — Aggregate per-child annual cost of all school-adjacent activities (clubs, sports, camps, tutoring, test prep, college counseling for Parth). Part of FR-3 financial picture: "Total kids enrichment spend YTD: $X (Parth: $A, Trisha: $B)." | P2 |
| F4.10 | **Canvas LMS API Integration** *(v4.0)* — Direct Canvas (Instructure) API integration for real-time grade and assignment data instead of relying on email parsing. Canvas REST API provides: current grades per course, assignment scores with submission status, missing/late assignment list, upcoming due dates, and teacher comments. This replaces the delayed, incomplete email-parsed school data with structured, real-time academic intelligence. Canvas API uses OAuth2; parents can generate API tokens from their parent portal. Enables: "Parth has a 94% in AP Physics, 87% in AP Language (down 3% this week), and 2 assignments due tomorrow." | P1 |
| F4.11 | **College Application Countdown Dashboard** *(v4.0)* — Comprehensive countdown tracker for Parth's college application process (senior year 2026–2027). Structured timeline with reverse-scheduled milestones: SAT scores (track and assess retake need), college list finalization (reach/match/safety by June 2026), campus visits (summer 2026 window), Common App essay drafts (start July, finalize September), letters of recommendation (request by September, track receipt), Early Decision/Early Action deadlines (November 1/15, 2026), Regular Decision deadlines (January 1–15, 2027), FAFSA/CSS Profile (October 2026 opens), financial aid comparison (March–April 2027). Each milestone has: target date, status, dependencies, and Artha-generated preparation prompts. Surface in weekly summary during active application season. | P0 |

---

### FR-5 · Travel & Loyalty Management

**Priority:** P1
**Summary:** Unified view of upcoming travel, loyalty point balances, and trip planning intelligence for the whole family.

**Data sources:**
- Gmail (Alaska Airlines, Expedia, Avis, Marriott Bonvoy booking confirmations)
- Outlook (travel-related emails)
- Manual input (trip plans)

**Known loyalty programs:**
- Alaska Airlines (whole family — MVP/Mileage Plan)
- Marriott Bonvoy
- Avis (car rental)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F5.1 | **Trip Dashboard** — Upcoming trips with all booking details (flights, hotel, car) in one place, parsed from confirmation emails. | P1 |
| F5.2 | **Loyalty Points Aggregator** — Current balance for Alaska, Bonvoy, Avis. Expiry alerts. Opportunity alerts when miles are close to a reward threshold. | P1 |
| F5.3 | **Travel Document Checker** — Before any family trip, verify: passports valid for 6+ months beyond return date (all four), H-4/H-1B status valid, any visa required for destination. | P0 |
| F5.4 | **Flight Alert** — Parse flight confirmation emails. Alert on check-in window, gate changes, and day-of reminders. | P1 |
| F5.5 | **Expedia/Booking History** — Maintain a structured log of past and upcoming bookings. Answer "when did we last go to [destination]?" | P2 |
| F5.6 | **India Trip Planner** — Given the family's ties to India, specific pre-trip checklist: OCI cards, passport validity for all four, airline booking lead time, currency. | P1 |

---

### FR-6 · Health & Wellness Radar

**Priority:** P1
**Summary:** Track health appointments, insurance utilization, HSA balance, and wellness goals for the family.

**Data sources:**
- Gmail/Outlook (appointment confirmations, insurance EOBs, HSA statements)
- Manual input (appointment dates, medications, wellness goals)

**Known providers:**
- Health insurance: Regence BCBS (Microsoft employee plan)
- HSA account: Active
- Primary care and specialists: To be indexed from past appointment emails

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F6.1 | **Family Appointment Calendar** — Upcoming medical, dental, and vision appointments for all four family members parsed from confirmation emails. | P1 |
| F6.2 | **HSA Balance & Utilization Tracker** — Current HSA balance. Estimated remaining balance after known upcoming expenses. Annual contribution vs. limit. | P1 |
| F6.3 | **Annual Preventive Care Tracker** — Track whether each family member has completed their annual physical, dental cleaning, and vision check for the current year. Alert if overdue by September. | P1 |
| F6.4 | **Insurance EOB Monitor** — Parse Explanation of Benefits emails from Regence. Flag unexpected charges or claim denials. | P2 |
| F6.5 | **Prescription Refill Tracker** — Alert when a recurring prescription is due for refill based on fill date + days supply. | P2 |
| F6.6 | **Wellness Goal Integration** — Connect to Goal Engine (FR-13). Wellness goals (exercise frequency, sleep, weight) get Artha's tracking and weekly reporting. | P1 |
| F6.7 | **Open Enrollment Decision Support** — During Microsoft's annual benefits open enrollment window (typically October–November): surface current plan details, prompt review of health plan options (Regence BCBS tiers), compare FSA vs. HSA election, review life insurance and disability coverage adequacy (cross-reference FR-16). Checklist-driven with deadline countdown. | P1 |
| F6.8 | **Employer Benefits Inventory** — Maintain awareness of all Microsoft employee benefits beyond health: life insurance (basic + supplemental), short-term and long-term disability, AD&D, legal plan, EAP, employee stock purchase plan (ESPP) enrollment windows, 401k match optimization, and any dependent care FSA. Surface relevant benefits at decision points. | P1 |
| F6.9 | **Apple Health Integration** *(v4.0)* — Import Apple Health data via automated HealthKit export (XML or CSV) to power wellness goals with real biometric data. Data sources: step count, active calories, exercise minutes, resting heart rate, sleep analysis, weight (if tracked). Processing: daily export from iPhone via Shortcuts automation to `~/OneDrive/Artha/health_export/`, parsed by `parse_apple_health.py` during catch-up. Enables wellness goals with real metrics: "Exercise goal: 4x/week → Apple Health shows 3 workout sessions logged this week (Mon run 32min, Wed gym 45min, Fri walk 28min)." Privacy: raw health data processed and discarded; only aggregated daily/weekly metrics stored in `state/health.md`. | P1 |

---

### FR-7 · Home & Property Management

**Priority:** P1
**Summary:** Track utilities, mortgage, maintenance schedules, home value signals, and smart home integration.

**The problem:** The Sammamish home generates bills across multiple utilities with no consolidated view. Maintenance tasks are tracked nowhere. The mortgage balance is unknown without logging into Wells Fargo.

**Property profile:**
- Address: Sammamish, WA 98074
- Mortgage: Wells Fargo (banker: BRAND, DEANNA — annual check-in)
- Utilities: PSE Energy (Account: 220032218574), Sammamish Plateau Water, King County
- Smart home: Home Assistant (local API)
- Internet: TBD (Comcast/Xfinity, Ziply Fiber, or other)
- Mobile: TBD (carrier, family plan)
- Waste: TBD (Republic Services or Sammamish contracted provider)
- Property tax: King County (semi-annual: April 30, October 31)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F7.1 | **Utility Bill Calendar** — All utility bills (PSE, Water, King County) parsed from email. Consolidated view, 5-day due date alerts. PSE enrolled in autopay — confirm each month. | P0 |
| F7.2 | **Mortgage Tracker** — Outstanding balance, monthly payment, interest rate, payoff date. Annual refinance check trigger. FICO trend connected. | P1 |
| F7.3 | **Home Maintenance Scheduler** — Annual and seasonal maintenance calendar: HVAC filter (quarterly), gutter cleaning (fall), furnace service (fall), exterior painting (estimate cycle), smoke detectors (annual test). | P1 |
| F7.4 | **Home Assistant Integration** — Read device status, energy usage, and automation logs from Home Assistant local API. Surface anomalies (device offline, unusual energy consumption) in daily briefing. | P1 |
| F7.5 | **Energy Usage Tracker** — Track PSE bills month-over-month. Alert on unusual spikes. Compare against Sammamish seasonal averages. | P2 |
| F7.6 | **Home Value Signal** — Periodic Zillow/Redfin estimate for 98074 comparable sales. Not investment advice — context for net worth calculation in FR-3. | P2 |
| F7.7 | **Service Provider Rolodex** — Maintain a curated list of trusted service providers (plumber, electrician, HVAC, landscaper) with last-used dates and notes. | P2 |
| F7.8 | **Telecom & Internet Tracker** — Track ISP (Comcast/Xfinity, Ziply Fiber, or other), mobile phone plan (carrier, plan, monthly cost for family), and home phone/VoIP if applicable. Parse bill emails for monthly cost. Alert on price increases or contract renewal dates. Surface in subscription ledger (FR-3 F3.4). | P1 |
| F7.9 | **Waste & Recycling Services** — Track trash, recycling, and yard waste service (Republic Services or Sammamish's contracted provider). Payment schedule, pickup schedule, and any service changes. Holiday schedule adjustments (pickup delayed by 1 day). | P2 |
| F7.10 | **HOA / Community Dues** — If applicable to 98074 property: track HOA dues, payment schedule, assessment notices, and community meeting dates. Parse HOA correspondence from email. | P2 |
| F7.11 | **Lawn & Landscaping Schedule** — Seasonal yard maintenance calendar specific to Pacific Northwest: spring fertilization, summer watering schedule, fall leaf cleanup and aeration, winter moss treatment. Track landscaping service visits and costs if using a service. | P2 |
| F7.12 | **Property Tax Tracker** — King County property tax is paid semi-annually (April 30 and October 31). Track assessed value, tax amount, payment due dates, and payment confirmation. Alert 30 days before due date. Compare assessed value to market estimate (FR-7 F7.6). "King County property tax: $X due April 30. Assessed value: $Y vs. Zillow estimate: $Z." | P1 |
| F7.13 | **Emergency Preparedness** — Sammamish is in a seismic zone (Cascadia Subduction Zone). Track: earthquake emergency kit contents and expiry dates (water, food, batteries, medications), family emergency plan (meeting point, out-of-area contact), FEMA/King County emergency alerts integration, annual family emergency drill reminder. Checklist with annual review prompt. | P1 |

---

### FR-8 · Calendar & Time Intelligence

**Priority:** P1
**Summary:** A unified, intelligent view of the family calendar with conflict detection, context-aware scheduling, and time-budget awareness.

**The problem:** Important dates — Parth's SAT 3/13, H-4 expiry windows, school events, annual appointments — live in separate systems. The Google Calendar is primarily used for birthday tracking. Outlook Calendar tracks work. Neither is connected to Artha's broader knowledge.

**Data sources:**
- Google Calendar (mi.vedprakash@gmail.com)
- Outlook Calendar (vedprakash.m@outlook.com — work-life boundary signal only)
- **Microsoft Work Calendar via WorkIQ MCP** *(v4.1)* — corporate Teams meetings, 1:1s, standups, org events. Available on Windows work laptop only (M365 Copilot license). Graceful degradation on Mac.
- Artha's internal calendar (built from all FR data sources)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F8.1 | **Artha Unified Calendar** — Merge events from Google Calendar + dates discovered by all Artha agents (immigration deadlines, bill due dates, school tests, travel) into a single queryable calendar. | P1 |
| F8.2 | **Conflict Detector** — Identify scheduling conflicts. Alert when two family members need to be in different places simultaneously with one car, or when a school event conflicts with a planned trip. | P1 |
| F8.3 | **Time Budget Awareness** — Track how Vedprakash's personal time is actually allocated vs. intended. Weekly: how much family time, learning time, and personal time? Surface the gap. | P2 |
| F8.4 | **Important Date Vault** — Store all family important dates (birthdays, anniversaries, citizenship milestones, school milestones) with multi-week advance reminders. | P1 |
| F8.5 | **Upcoming Week Briefing** — Every Sunday, Artha loads the week's calendar and surfaces the 5 most complex logistics items that need coordination in advance. | P1 |
| F8.8 | **Work Calendar Merge** *(v4.1)* — Integrate Microsoft corporate calendar via WorkIQ MCP as 7th data source. Merge with personal calendars using field-enrichment dedup (summary from personal, Teams link from work). Tag work-only events with 💼 prefix. Platform-gated: Windows only; Mac catch-ups show personal calendar + stale metadata footer. | P1 |
| F8.9 | **Cross-Domain Conflict Detection** *(v4.1)* — Detect work↔personal event overlaps (±15 min). Score cross-domain conflicts at Impact=3 (lifestyle trade-off) vs. internal work conflicts at Impact=1 (self-resolvable). Deduplicated events excluded from conflict detection. | P1 |
| F8.10 | **Duration-Based Meeting Load** *(v4.1)* — Analyze daily meeting burden by total minutes (not count). Triggers: >300 min → "Heavy load"; largest focus gap <60 min → "Context switching fatigue"; <120 min → "Light day, good for deep work." Persist count+duration metadata to `state/work-calendar.md` (13-week rolling window). | P1 |
| F8.11 | **Partial Redaction Engine** *(v4.1)* — Before work meeting titles transit to Claude API, redact sensitive codenames locally via configurable keyword list in `config/settings.md`. Only matched substrings are replaced (e.g., "Project Cobalt Review" → "[REDACTED] Review"), preserving meeting-type context for trigger classification. | P0 |
| F8.12 | **Teams Meeting Join Actions** *(v4.1)* — If a Teams meeting starts within 15 minutes of catch-up, surface a low-friction join action: "→ Join [Meeting] (Teams) [Y/n]". Opens Teams link on approval. | P2 |
| F8.13 | **Meeting-Triggered Employment OIs** *(v4.1)* — Critical meeting types (Interview, Performance Review, Calibration) auto-create Employment domain Open Items for prep. Temporal filter: future-dated only (no stale OIs in digest mode). Configurable trigger list in `config/settings.md`. | P1 |

---

### FR-9 · Shopping & Commerce Intelligence

**Priority:** P2
**Summary:** Track spending patterns, subscriptions, and purchase history across major retailers.

**Data sources:**
- Gmail (Amazon, Costco, order confirmation emails)
- Outlook (purchase receipts)
- Credit card alert emails (Chase, Fidelity, Discover)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F9.1 | **Monthly Spend Summary** — By category: Amazon, groceries, restaurants, subscriptions, kids, travel. Trend vs. prior month. Part of weekly summary. | P2 |
| F9.2 | **Amazon Order Tracker** — Parse order confirmation and delivery emails. Answer "when does X arrive?" and "what did I order last month?" | P2 |
| F9.3 | **Return Window Alert** — For major purchases, track return window expiry. Alert 3 days before window closes if item not yet reviewed. | P2 |
| F9.4 | **Costco Membership Renewal** — Track annual membership renewal date. Alert 30 days in advance. | P2 |
| F9.5 | **Price Drop Tracker** — For saved items or recent purchases, monitor for price drops and alert if a significant drop occurs within return window. | P2 |

---

### FR-10 · Learning & Development Tracker

**Priority:** P1
**Summary:** Track learning activity across all channels, measure progress toward learning goals, and surface the right content at the right time.

**The problem:** Learning is happening across many channels (newsletters, courses, Kaggle, Obsidian notes) with no measurement of cumulative progress. There's no way to know if you're on track toward a learning goal.

**Learning inventory (discovered):**
- Newsletters: ByteByteGo, System Design One, Big Technology (Kantrowitz), TED Recommends, Product newsletters
- Active learning: Kaggle (ML/AI), Obsidian vault (personal knowledge base)
- Education: UW Foster MBA (completed Spring 2023 — alumnus)
- Professional: Microsoft TPM role generates continuous learning

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F10.1 | **Learning Goal Tracker** — Set explicit learning goals ("complete X course by Q2," "read 12 books this year") and track progress. Connected to Goal Engine. | P1 |
| F10.2 | **Newsletter Digest** — Weekly aggregation of ByteByteGo, System Design, Big Technology, and others. Key insights only, with links to full content. Reduces inbox noise. | P1 |
| F10.3 | **Obsidian Vault Signals** — Monitor Obsidian vault activity (notes created, topics covered). Detect learning streaks. Surface forgotten notes relevant to current context. | P2 |
| F10.4 | **Course Progress Tracker** — For active online courses (Kaggle, Coursera, etc.), track completion percentage and time since last session. Alert on stalled courses. | P1 |
| F10.5 | **Reading Tracker** — Track books started, in progress, and completed. Connect to annual reading goal. | P2 |
| F10.6 | **UW Foster Alumni Tracker** — Monitor relevant UW Foster events, networking opportunities, and State of Economy Forum (noted in Outlook). Relevant to professional development. | P2 |

---

### FR-11 · Relationship Intelligence & Social Fabric

**Priority:** P1 *(elevated from P2 in v3.8 — relationships are a core life domain, not a nice-to-have)*
**Summary:** Build and maintain a relationship graph that tracks communication patterns, reciprocity, cultural protocols, life events, and group dynamics across the Mishra family's social network. Surfaces reconnect intelligence, occasion awareness, and relationship health signals in briefings.

**The problem:** Relationships decay silently. There is no system that tracks who you last contacted, whether reciprocity is balanced, which cultural protocols apply to which relationships, or which life events (births, graduations, bereavements) need acknowledgment. The result: missed birthdays, lapsed friendships, unbalanced social investments, and cultural protocol violations — all preventable with structured awareness.

**Data sources:**
- Google Calendar (birthdays, cultural events)
- Gmail/Outlook (personal correspondence patterns, frequency analysis)
- `contacts.md` (relationship groups, cultural protocol metadata)
- `occasions.md` (festival calendar, occasion types)
- Manual input (relationship context, group membership)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F11.1 | **Birthday & Anniversary Engine** — All family birthdays and anniversaries with 2-week advance alerts. Gift suggestion prompt 1 week out. Currently: Google Calendar is the primary birthday tracker — consolidate here. | P1 |
| F11.2 | **Family Cultural Calendar** — Track temple events, religious observances (Tulsidevi Bhakti Gita — already on calendar), and cultural milestones. | P1 |
| F11.3 | **Reconnect Radar** — For important relationships where correspondence has gone quiet for more than a configured period, surface a gentle prompt to reconnect. Configurable silence thresholds per relationship tier: close family (14 days), close friends (30 days), extended (90 days). | P1 |
| F11.4 | **India Family Connections** — Track correspondence and call patterns with family in India. Flag when it's been too long since contact with key people. IST timezone-aware for suggesting contact windows. | P1 |
| F11.5 | **Relationship Graph Model** — Structured graph of all tracked relationships with attributes: tier (close family / close friend / extended family / colleague / community), last_contact date, contact_frequency target, preferred_channel (email / WhatsApp / phone), cultural_protocol (e.g., "touch feet at Diwali", "Rakhi sender"), and life_events history. Stored in `state/social.md`. | P1 |
| F11.6 | **Communication Pattern Analysis** — Parse email metadata (sender, recipient, frequency, response time) to build communication cadence profiles. Detect: declining frequency (relationship cooling), one-sided communication (you always initiate), sudden silence after regular contact (potential issue). Surface in weekly summary. | P1 |
| F11.7 | **Reciprocity Ledger** — Track directional communication and gesture balance per relationship. "You've sent 5 messages to Rahul since his last reply" is a signal. "Meera has invited your family to 3 events; you've reciprocated once" is actionable. Not a score — a gentle awareness surface. | P2 |
| F11.8 | **Cultural Protocol Intelligence** — For relationships with cultural context (Indian family, temple community), track protocol obligations: Rakhi (sister → brother), Diwali greetings order (elders first), festival-specific gift norms, bereavement protocols (13-day period awareness). Sourced from `contacts.md` cultural metadata. | P1 |
| F11.9 | **Life Event Awareness** — When Artha detects a life event in a contact's sphere (graduation, new job, bereavement, birth, wedding — via email parsing or manual input), surface a prompt: "Priya had a baby last week (detected from email). Send congratulations?" Track acknowledged events to prevent re-prompting. | P1 |
| F11.10 | **Group Dynamics Tracking** — Track relationship groups (Microsoft colleagues, temple community, Parth's friends' parents, Sammamish neighbors) with group-level health metrics: last group interaction, upcoming group occasions, group communication balance. Enables: "You haven't attended a temple community event in 3 months." | P2 |
| F11.11 | **India Time Zone Scheduling** *(v4.0)* — IST-aware scheduling intelligence for India family communications. Automatically calculates optimal call windows considering Pacific Time ↔ IST conversion (IST = PT + 13.5 hours), Indian family members' typical availability (morning 8–10 AM IST → 6:30–8:30 PM PT previous day), Indian public holidays and festival dates, and the user's own calendar availability. Surfaces in briefing when a reconnect is overdue: "Mom hasn't been called in 18 days. Best window: tonight 7:30 PM PT (tomorrow 9 AM IST, no Indian holidays)." Also flags when Indian festivals approach: "Holi is in 5 days — schedule family video call? Best window: Saturday 8 PM PT (Sunday 9:30 AM IST)." | P1 |

---

### FR-12 · Digital Life Management

**Priority:** P2
**Summary:** Manage the complexity of 40+ digital subscriptions, accounts, and services that form the infrastructure of modern life.

**Data sources:**
- Gmail/Outlook (subscription confirmations, renewal notices, account alerts)
- Manual input (password manager audit)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F12.1 | **Subscription Audit** — Master list of all active subscriptions with monthly cost, renewal date, and last-used-assessed date. Detect unused or duplicate subscriptions. Flag for cancellation review. | P1 |
| F12.2 | **Subscription Cost Dashboard** — Total monthly and annual cost of all subscriptions. Category breakdown. Year-over-year trend. | P2 |
| F12.3 | **Account Security Monitor** — Parse security alert emails (Chase device login, Equifax credit alerts). Flag unusual activity. Equifax credit monitoring is already active — integrate alerts. | P1 |
| F12.4 | **Domain & Hosting Tracker** — Track any personal domains or web hosting (if applicable) for renewal dates. | P2 |
| F12.5 | **Home Assistant Health Monitor** — Track Home Assistant system uptime, device offline alerts, and automation failures. Surface in morning briefing if any critical device is offline. | P2 |
| F12.6 | **Subscription ROI Tracker** *(v4.0)* — For each active subscription (from F12.1/F3.4), track usage signals and calculate a value-per-dollar score. Usage signals: email engagement (newsletter open/click patterns), login frequency (detected from authentication emails), content consumption (learning platform progress from F10.x). Categories: High ROI (used frequently, clear value), Medium ROI (periodic use), Low ROI (paying but rarely using), Zero ROI (no usage signals in 60+ days). Quarterly report: "You're paying $167/month across 12 subscriptions. 3 subscriptions ($42/month) show zero usage in 60 days: [list]. Cancel or justify?" Integrated into F3.4 subscription ledger and quarterly life scorecard (F15.44). | P1 |

---

### FR-13 · Goal Intelligence Engine

**Priority:** P0
**Summary:** Define, track, and drive progress on personal goals across all life domains. The most distinctive feature of Artha.

*See Section 8 for the full deep-dive specification.*

**Core features (summary):**

| Feature ID | Feature | Priority |
|---|---|---|
| F13.1 | **Conversational Goal Creation** — Define goals through natural language conversation with Claude. User says "I want to make sure we're saving enough for Parth's college" — Artha infers the goal type, suggests metrics, identifies data sources, and proposes the structured schema for confirmation. The structured schema (Section 8.1) is the storage format; the creation experience is conversational. | P0 |
| F13.2 | **Automatic Metric Collection** — For each goal, Artha identifies the data source that proves progress and pulls it automatically (e.g., finance goal → Fidelity balance; learning goal → Obsidian notes created). | P0 |
| F13.3 | **Goal Progress in Morning Briefing** — Every morning briefing includes 2–3 goal signals: on track, at risk, or behind. | P0 |
| F13.4 | **Weekly Goal Review** — Every Sunday summary includes a full goal scorecard: all active goals, this week's movement, cumulative progress, and trend. | P0 |
| F13.5 | **Goal Cascade View** — Show how sub-goals support parent goals. Financial savings goal ← Monthly budget goal ← Amazon spend target. | P1 |
| F13.6 | **Goal-Linked Alerts** — When an ambient alert fires (e.g., Fidelity balance drop), it links to the relevant goal and shows impact on goal trajectory. | P1 |
| F13.7 | **Recommendation Engine** — When a goal is at risk or behind, Artha surfaces one specific recommended action. Not generic advice — specific and contextual. | P1 |
| F13.8 | **Annual Goal Retrospective** — At year-end, generate a structured review: goals set, goals achieved, goals missed, what contributed to each outcome. | P2 |
| F13.9 | **Goal Conflict Detection** — Detect when two active goals have metrics moving in opposing directions and surface the trade-off explicitly. Example: "Your savings goal is on track but your family travel goal shows zero progress. These may be in tension — do you want to adjust either target?" Also detect resource conflicts: Parth's SAT prep time vs. Economics Club commitment, or extra work hours (via Vega signal) conflicting with protected family time. | P1 |
| F13.10 | **Goal Trajectory Forecasting** — For Outcome goals, project the current trend line forward and compare to the target. When projected outcome deviates by >10% from target, proactively suggest adjustment options with specific numbers: (1) Increase effort, (2) Extend deadline, (3) Revise target. Example: "Your net worth goal assumed 8% returns. YTD returns are 3%. At current trajectory, you'll miss by $X. Options: increase monthly savings by $Y, extend to March 2027, or adjust target." | P1 |
| F13.11 | **Behavioral Nudge Engine** — For each habit goal, suggest implementation intentions (specific time, place, trigger), offer to create supporting calendar blocks, track streaks with positive reinforcement, and proactively reduce friction by cross-referencing calendar availability. Turn "exercise 4x/week" into "best open slot is Tuesday 6 PM after Parth's pickup; schedule it?" | P1 |
| F13.12 | **Dynamic Goal Replanning** — When a goal is persistently behind, propose structured adjustment options rather than just reporting "Behind." Options: keep target and increase effort, keep effort and extend deadline, or revise target based on updated reality. Prevents the "dead goal" problem where behind-status goals are ignored indefinitely. | P1 |
| F13.13 | **Seasonal Pattern Awareness** — After one full year of data, automatically detect cyclical patterns (school year rhythm, holiday spending spikes, tax season, summer travel, visa bulletin cycles) and incorporate them into goal trajectory forecasting. Example: "Amazon spending typically spikes 50% in Nov–Dec. You need to run under budget by $X/month in Q1–Q3 to absorb the holiday spike." | P2 |
| F13.14 | **Implementation Planning** *(v3.9 — Coaching Engine)* — When a goal is created or falls behind, Artha generates a concrete implementation plan: specific next actions, time blocks needed, resources required, and potential obstacles. Not generic advice — contextual to the user's calendar, current obligations, and behavioral patterns. Example: "To hit your exercise 4x/week goal, here's a plan: Mon 6AM (gym opens early, no school drop-off), Wed 6PM (after Parth pickup), Fri 6AM, Sat 9AM (family schedule clear). First 2 weeks: 3x/week to build habit, then increase." | P1 |
| F13.15 | **Obstacle Anticipation** *(v3.9 — Coaching Engine)* — For each active goal, identify the most likely obstacles based on historical patterns, upcoming calendar events, and seasonal factors. Surface proactively: "Your learning goal is at risk next week — you have 14 calendar events (highest in a month) and Parth's SAT prep may consume evening time. Consider front-loading a learning session this weekend." Uses pattern of life data (F15.34) and calendar intelligence (F15.31). | P1 |
| F13.16 | **Accountability Patterns** *(v3.9 — Coaching Engine)* — Learn what accountability style works for the user over time. Track which nudge types lead to action (gentle reminder vs. consequence framing vs. streak tracking vs. commitment device). Adapt: if streak messaging drives exercise compliance but deadline framing drives learning, use the right pattern for each goal. Initial mode: try all styles equally for the first 30 days, then weight toward what works. Stored in `memory.md` under `## Coaching Preferences`. | P1 |
| F13.17 | **Goal Sprint with Real Targets** *(v4.0)* — Enforce that every goal has a concrete, measurable `target_value` — no goals with `target_value: 0` or empty targets are allowed in active status. When a goal is created without a target, Artha prompts for one using calibrated defaults per goal type: financial ("What is your target net worth by end of 2026?"), academic ("What GPA is Parth targeting? Default: 3.5+"), habit ("How many times per week? Default: 3x"), milestone ("What is the specific milestone and target date?"). For existing goals with missing targets, `/goals` command surfaces them: "⚠️ 2 goals have no target: [list]. Set targets now?" Goals without targets cannot contribute to the weekly scorecard or trajectory forecasting. | P0 |
| F13.18 | **Goal Auto-Detection** *(v4.0)* — Infer implicit goals from email patterns, calendar activity, and user behavior — even when the user hasn't explicitly defined them. Detection signals: recurring calendar blocks suggest habits ("You have 'gym' on your calendar 3x/week — is this a fitness goal?"), repeated email searches suggest tracking interest ("You've checked Zillow 4 times this month — house-related goal?"), spending patterns suggest budget goals ("Amazon spend has been decreasing for 3 months — are you targeting a spending reduction?"). Auto-detected goals are proposed as suggestions, never auto-created: "Artha noticed: [pattern]. Would you like to create a goal for this?" If accepted, Artha creates the goal with appropriate metrics and target. If dismissed, adds to `memory.md` dismissed patterns to prevent re-prompting. | P1 |

---

### FR-14 · Work-Life Boundary Guardian

**Priority:** P1
**Summary:** Detect when work is encroaching on personal time and surface that signal to Artha without exposing work content.

**The boundary:** Artha never reads work emails, Teams messages, or ADO items. It receives exactly one signal: a work-health indicator. This is the only channel between Vega and Artha.

**How it works:**
- Artha monitors email timestamps to detect work-hours patterns bleeding into personal time
- Artha detects when personal calendar slots are consumed by work (no specifics — just the signal)
- Artha integrates the signal into weekly summaries and goal tracking for work-life balance goals

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F14.1 | **After-Hours Work Signal** — Detect work-related email activity (Microsoft domains, Outlook work account) occurring outside configured work hours (default: before 8am or after 7pm on weekdays, or on weekends). Surface weekly — not per-incident. | P1 |
| F14.2 | **Personal Time Protection** — If a personal calendar block (family dinner, school event, temple) is overridden by a work meeting, flag this in Artha's weekly summary. | P1 |
| F14.3 | **Work-Life Balance Goal** — Create a default goal in the Goal Engine: "Protected personal time ≥ X hours/week." Artha tracks and reports against it. | P1 |

---

### FR-15 · Artha OS Core

**Priority:** P0
**Summary:** The cross-cutting infrastructure that powers all other FRs — the ambient engine, state management, routing, and Artha's identity as a system.

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F15.1 | **Catch-Up Workflow** — User-triggered pull that fetches all new emails, calendar events, and data source updates since the last run. Claude reads each source via MCP tools, processes in batch, updates local Markdown state files, evaluates alert thresholds, and synthesizes a briefing. The entire workflow is orchestrated by Artha.md instructions (loaded via CLAUDE.md) — no custom orchestration code. On each run: (1) decrypt sensitive state files via `vault.sh`, (2) fetch unprocessed emails from Gmail/Outlook via MCP AND calendar events in PARALLEL, (3) run `pii_guard.sh` pre-flight filter on extracted data before state writes — halt if filter fails, (4) route each item to the appropriate domain prompt, (5) apply §8.2 redaction rules (Layer 2), (6) update state files, (7) evaluate thresholds and generate alerts, (8) synthesize briefing, (9) email briefing to configured address, (10) encrypt sensitive state files via `vault.sh`. | P0 |
| F15.2 | **Local State Files** — Markdown files in `~/OneDrive/Artha/state/` storing Artha's world model: one file per domain (immigration.md, finance.md, education.md, etc.) containing structured frontmatter (YAML) and prose sections. All entities, relationships, and current state are captured. State files contain PII-filtered content only — raw PII is replaced by `[PII-FILTERED-*]` tokens before processing (Layer 1) and redacted by LLM rules before writing (Layer 2). Synced via OneDrive with `age` encryption for sensitive domains. Fits within Claude's 200K context window for single-session reasoning across all domains. | P0 |
| F15.3 | **Domain Prompt Library** — Each FR is backed by a domain prompt file in `~/OneDrive/Artha/prompts/`. Artha.md routes incoming data to the correct prompt based on sender/subject/content patterns. Prompts define extraction rules, alert thresholds, state update patterns, and briefing contribution format. Adding a new domain = adding a new prompt file — no code changes. | P0 |
| F15.4 | **Briefing Synthesizer** — Triggered as part of each catch-up run (not scheduled). Collects signals from all domain state files and synthesizes into the structured briefing format. Output delivered to terminal and emailed for cross-device access. | P0 |
| F15.5 | **Weekly Summary** — Generated when the user requests it or as part of a Sunday/weekend catch-up. Collects the week's state changes across all domains and synthesizes the weekly review. | P0 |
| F15.6 | **Human Gate Layer** — All write operations (sending an email, paying a bill, adding a calendar event) require explicit user approval within the Claude Code conversation. Claude proposes the action with full details; user confirms or modifies before execution. Approval history is logged in `~/OneDrive/Artha/state/audit.md`. At Trust Level 2, pre-approved action categories can execute with post-hoc notification. | P0 |
| F15.7 | **Audit Log** — Every action Artha takes or recommends is logged with timestamp, data source, and outcome. Artha's track record is the basis for autonomy elevation. | P1 |
| F15.8 | **Configuration Interface** — Set alert thresholds, delivery channels, work hours, goal parameters, and data source connections. | P1 |
| F15.9 | **Semantic Reasoning Layer** — All alerts, briefings, and recommendations pass through a Claude-powered reasoning step that considers full context before surfacing. This is the difference between a notification system and an intelligence system. Instead of static severity levels, the LLM dynamically assesses priority based on cross-domain context. Example: a low assignment score is informational if the larger pattern shows stable GPA; it becomes meaningful if it follows three recent weak signals. A PSE bill alert on the day you’re flying to India with passport concerns is lower priority than on a normal Tuesday. | P0 |
| F15.10 | **Conversation Memory** — Artha maintains a structured memory of all interactions, preferences, corrections, and decisions. Remembers: questions asked and their resolutions, preferences expressed (“stop alerting me about Spirit Week”), decisions made and rationale (“we decided to refinance if rates drop below X%”), corrections to understanding (“Parth’s club meeting is biweekly, not weekly”). Feeds into future briefings, alerts, and recommendations. | P1 |
| F15.11 | **Insight Engine** — Runs weekly (or on significant data changes) using Claude’s extended thinking to reason across all domain data and surface 3–5 non-obvious observations. This powers the “ONE THING” in the morning briefing and the “Artha Observations” in the weekly summary. Examples: “Your H-4 EAD renewal is 5 months out. Based on Fragomen’s average processing time from your last two renewals, initiate attorney contact within 3 weeks.” “Parth’s grade trajectory in AP Language has declined for 3 consecutive grading periods — the seminar format may need different study strategies.” | P1 |
| F15.12 | **Model Tiering Strategy** — Claude Code handles model selection internally. Artha.md specifies intent: use extended thinking for weekly summaries and cross-domain insight generation, standard processing for email parsing and state updates. Prompt caching (system prompts cached across the session) reduces costs. Target: <$50/month at daily catch-up cadence with ~100 emails/day across all accounts. | P1 |
| F15.13 | **[ELIMINATED — v3.0]** Daemon runtime specification removed. Artha runs as an interactive Claude Code session, not a background process. No LaunchAgent, no crash recovery, no heartbeat file. The user triggers each session explicitly. | — |
| F15.14 | **Self-Health Check** — On request ("Artha, are you healthy?"), Artha reports: last catch-up timestamp, state file freshness per domain, any MCP tool connection failures, estimated API cost for the current billing period, and number of unprocessed items. Logged in `~/OneDrive/Artha/state/health.md`. | P1 |
| F15.15 | **Predictive Calendar** — After 6+ months of data, model recurring events and obligations. Proactively add predictions to the calendar with confidence levels. | P2 |
| F15.16 | **Component Registry** — Machine-readable manifest (`registry.md`) of all deployed components: MCP servers, domain prompts, state files, scripts, hooks, slash commands, CLI tools, and action channels. Enables Artha to reason about its own capabilities and detect configuration drift. | P0 |
| F15.17 | **Self-Assessment Dashboard** — Artha tracks its own per-domain accuracy (≥90% target), false positive rate, and tracks which domains need attention. Surfaced via `/status` slash command or on-demand query. | P1 |
| F15.18 | **Extensibility Wizard** — When the user wants to add a new data source, domain, or integration, Artha walks through the appropriate governance checklist (tech spec §12) and sets up the new component with correct registry entries. | P1 |
| F15.19 | **Multi-LLM Orchestration** — Leverages Gemini CLI (free web search, URL summarization) and Copilot CLI (free code/config validation) alongside Claude for cost-aware task routing. Web research tasks (Visa Bulletin, property values, recall checks) are delegated to Gemini at $0 cost. Script/config validation delegated to Copilot at $0 cost. For high-stakes decisions (immigration, finance, estate), all three models generate independent responses and Claude synthesizes the best answer (ensemble reasoning). CLI health monitored in health-check.md with automatic fallback chain. Implementation: tech spec §3.7. | P0 |
| F15.20 | **Action Execution Framework** — Full lifecycle for actions beyond read-only: email composition (general-purpose, not just briefing delivery), WhatsApp messaging via URL scheme (human-gated by OS — user taps send), calendar event creation, email archiving. Every action follows a structured proposal → review → execute → log lifecycle. Action catalog defines trust levels per action type. Autonomy Floor rules are enforced regardless of trust level. Contacts and occasions managed via config files. **Each proposal includes a `friction` field (`low|standard|high`)** — low-friction actions (calendar add, archive) can batch-approve; high-friction actions (financial, immigration) require individual review regardless of trust level. Implementation: tech spec §7.4. | P0 |
| F15.21 | **Visual Message Generation** — AI-generated images via Gemini Imagen CLI for festival greetings (Diwali, Holi, Christmas, New Year), birthday cards, anniversary wishes, and occasion-specific messages. Generated visuals saved to `~/OneDrive/Artha/visuals/` for cross-device access. Can be attached to emails or manually attached to WhatsApp messages. Occasion calendar and visual styles configured in `occasions.md`. | P1 |
| F15.24 | **Decision Graphs** *(v3.8, enhanced v4.0)* — Track cross-domain decisions with full context: what was decided, when, why, what alternatives were considered, and which domains were affected. Auto-generated during cross-domain reasoning (§9.4 step 10). Stored in `state/decisions.md`. Queryable via `/decisions` slash command. Enables: “When did we decide to refinance?” “What were the alternatives we considered for Parth’s SAT prep?” Prevents re-deliberation of settled questions. **v4.0 — Decision Deadlines:** Every pending decision gets an explicit `deadline` field with countdown in briefings. Decisions without deadlines get a nudge: “This decision has been open for 14 days with no deadline — set one or mark as resolved.” Expired deadlines auto-escalate to 🔴 alerts. | P1 |
| F15.25 | **Life Scenarios** *(v3.8)* — What-if analysis for high-stakes goals and life decisions. Auto-suggested when Artha detects a major decision point (home purchase, job change, immigration status change). Templates: "What if we refinance at X%?", "What if Parth attends private university vs. in-state?", "What if I-485 is approved in 6 months vs. 18 months?" Scenarios run through affected domain prompts and surface projected impacts. Stored in `state/scenarios.md`. | P1 |
| F15.26 | **Digest Mode** *(v3.8)* — When >48 hours have elapsed since the last catch-up, Artha automatically switches from standard briefing to digest format: priority-tier grouping (Critical → Warning → Notable → FYI), “What You Missed” header with day-by-day summary, and action item consolidation. Prevents information overload after gaps. Triggered automatically; user can also request: “give me the digest.” | P1 |
| F15.27 | **Accuracy Pulse** *(v3.8)* — Weekly self-assessment in the weekly summary: actions proposed vs. accepted vs. declined vs. deferred, corrections logged by user (via memory.md), alerts dismissed without action, domains where extraction accuracy dropped. Enables the user to see whether Artha is getting smarter or drifting. Not an implicit measurement — explicit metadata tracking. | P1 |
| F15.28 | **Data Integrity Guard** *(v3.9)* — Three-layer protection against state file data loss. **Layer 1 — Pre-decrypt backup:** Before `vault.sh decrypt` overwrites a .md file with decrypted .age content, back up the existing .md to `.md.bak` if it exists and is newer than the .age file. Prevents data loss when a session modifies .md but crashes before encrypt (leaving stale .age). **Layer 2 — Write verification:** After any state file write, immediately re-read the file and verify the write contains the expected content (at minimum, more data than what existed before). **Layer 3 — Net-negative write guard:** Before writing a state file, compare new content against existing. If the new version has fewer structured entries (YAML keys, table rows, list items) than the old version, HALT the write, log to audit.md, and surface a warning: "⚠️ State write blocked: [domain].md would lose N entries. Review before proceeding." Override: explicit user confirmation. This guard prevents catch-up sessions from accidentally overwriting populated state with templates. | P0 |
| F15.29 | **Life Dashboard Snapshot** *(v3.9)* — Auto-generated `state/dashboard.md` providing a single-glance family status across all domains. Refreshed at the end of each catch-up (step 15b). Structure: one row per domain showing domain name, alert level (🔴🟠🟡🟢), last activity date, key metric (e.g., "GPA 2.7", "H-1B valid 18mo", "$X net worth"), and next action. Also includes family member status rows (one per person: Ved, Archana, Parth, Trisha) showing their highest-priority item. Dashboard.md is always-load tier and is the first thing read during session quick-start. Enables rapid "where do things stand?" queries without loading all 18 domain files. | P1 |
| F15.30 | **Compound Signal Detection** *(v3.9)* — Cross-domain signal correlation engine that detects convergent patterns. When signals from 2+ domains converge on the same time window or entity, Artha synthesizes them into a compound alert. Examples: immigration deadline + financial pressure + work stress = "⚠️ Compound: You have 3 high-stress domains active simultaneously — consider deferring non-essential commitments." Parth SAT week + missing assignments + low sleep signals = "⚠️ Compound: Parth has converging academic pressure — SAT in 3 days with 2 missing assignments." Implementation: during step 10 (cross-domain insights), run a convergence check across all domains with active alerts. Compounds scored higher than individual signals. | P1 |
| F15.31 | **Proactive Calendar Intelligence** *(v3.9)* — Forward-looking calendar analysis beyond simple conflict detection (F8.2). Three capabilities: (1) **Logistics analysis:** For events requiring travel/coordination, proactively surface: drive time, pickup conflicts for kids, weather impact, concurrent family obligations. "Parth's SAT is at 8 AM Friday — departure by 7:15. Trisha needs drop-off by 7:45. Both parents needed." (2) **Preparation detection:** For events that need advance action (doctor visit = fasting?, travel = passport check?, school event = volunteer sign-up?), surface preparation items 3–5 days ahead. (3) **Energy/load balancing:** Detect weeks with unusually high calendar density and flag: "Next week has 14 events across 5 days — highest in 30 days. Consider rescheduling non-essential items." | P1 |
| F15.33 | **Bootstrap Command** *(v3.9)* — `/bootstrap` slash command for guided cold-start population of empty or template-only state files. When invoked, Artha scans all state files for those still showing `updated_by: bootstrap` or with >50% TODO fields. For each, initiates a structured interview: "I see immigration.md is mostly empty. Let me ask you the key questions: (1) What is your current visa type? (2) When does it expire? (3) Is I-140 filed/approved? ..." Answers are written directly to the state file with `updated_by: user_bootstrap`. Also prompts for high-value data: "Do you have documents in OneDrive I should scan? Any email threads with Fragomen I should search for?" Prevents the "cold start" problem where state files stay empty because catch-ups have no emails to parse for a domain. | P0 |
| F15.34 | **Pattern of Life Detection** *(v3.9)* — After 30 days of catch-up data, Artha builds behavioral baselines: (1) **Spend patterns:** Average daily/weekly/monthly by category, with day-of-week and seasonal components. Enables: "Your Amazon spending is 2.3x your 30-day average this week." (2) **Communication rhythms:** Response times, initiation patterns, contact frequency by tier. Enables: "You typically respond to Fragomen emails within 2 hours — this one has been pending 3 days." (3) **Calendar density:** Normal vs. overloaded weeks, preferred meeting-free blocks. (4) **Goal behavior:** Time-of-week when goal activities typically happen. Patterns are stored in `state/memory.md` under `## Behavioral Baselines` and used by the coaching engine, compound signal detection, and briefing ONE THING selection. | P1 |
| F15.35 | **Signal:Noise Ratio Tracking** *(v3.9)* — For every catch-up, track: emails processed, items surfaced in briefing, items acted upon by user, items dismissed. Calculate per-domain signal:noise ratio (items acted upon / items surfaced). Store in `health-check.md` under `signal_noise:`. When a domain's ratio drops below 40% over a rolling 14-day window: (1) Log the decline, (2) Suggest suppression rule adjustments, (3) Consider auto-demoting that domain's briefing contribution to summary-only. Prevents briefing fatigue where too many low-value items erode trust in high-value ones. Surfaced in `/status` output and Accuracy Pulse. | P1 |
| F15.36 | **Stale State Detection** *(v3.9)* — Automated monitoring of state file freshness relative to expected data flow. For each domain, maintain an `expected_cadence` (e.g., immigration: monthly, finance: weekly, kids: daily during school). When a state file hasn't been updated for 2x its expected cadence and there IS email/calendar data that should have routed there, flag: "⚠️ finance.md hasn't been updated in 21 days but 8 Chase/Fidelity emails were received. Possible routing failure." Auto-heal attempt: re-process the unrouted emails through the domain prompt. Prevents silent domain death where a routing change or email filter causes a domain to stop receiving updates without anyone noticing. | P1 |
| F15.37 | **Consequence Forecasting** *(v3.9)* — For each alert surfaced in the briefing, add a "consequence of inaction" projection at 7, 30, and 90 days. Example: "PSE bill $300.63 due in 3 days → if ignored: late fee $25 (7d), service disruption warning (30d), credit impact (90d)." "H-1B expires in 180 days → if ignored: 90-day filing window missed (90d), status lapse risk." Not every alert needs all three horizons — only critical and urgent alerts get the full projection. Consequence data sourced from domain prompt knowledge (known fee structures, regulatory timelines). Drives the ONE THING scoring: items with severe 90-day consequences score higher. | P1 |
| F15.38 | **Pre-Decision Intelligence Packets** *(v3.9)* — When Artha detects an upcoming decision point (via email analysis, calendar events, goal milestones, or user query), auto-generate a structured research packet. Triggers: mortgage renewal approaching, insurance renewal, college application timeline, large purchase consideration, job change signals. Packet contents: (1) Current state summary from relevant state files, (2) Options with pros/cons (from Gemini web research at $0), (3) Financial impact projection, (4) Timeline and deadlines, (5) Questions to ask (doctor/lawyer/advisor). Packets saved to `summaries/decision-[topic]-[date].md` and referenced in briefings. Prevents reactive decision-making by preparing you before the deadline pressure hits. | P1 |
| F15.39 | **Session Quick-Start** *(v3.9)* — When a new Claude Code session starts, detect the likely session type from the user's first message and optimize context loading accordingly. Three modes: (1) **Catch-up** ("catch me up", "briefing", "SITREP") — full catch-up workflow. (2) **Query** ("how much do I owe on mortgage?", "when is Parth's SAT?") — load only the relevant domain state file + dashboard.md, skip email fetch. (3) **Action** ("send birthday wish to Rahul", "add event to calendar") — load contacts.md + occasions.md + relevant domain, skip full catch-up. Quick-start reduces time-to-first-response for non-catch-up sessions from ~60s to <10s by avoiding unnecessary context loading. Auto-detected from first message; overridable with explicit slash commands. | P1 |
| F15.40 | **Briefing Compression Levels** *(v3.9)* — Three briefing modes beyond standard and digest: (1) **Full** — current standard briefing with all sections. Default for first catch-up of the day. (2) **Standard** — current default. (3) **Flash** — ultra-compressed 30-second briefing: only 🔴 Critical and 🟠 Urgent items + ONE THING. No domain sections. For second/third catch-ups in a day or when user says "quick update." Auto-selection: first catch-up = Full, subsequent same-day = Flash, >48hr gap = Digest. User override: "give me the full briefing" / "flash briefing" / "just the critical stuff." | P1 |
| F15.41 | **Context Window Pressure Management** *(v3.9)* — Active monitoring of context window utilization during catch-up with graceful degradation. Thresholds: <50% = green (full processing), 50–70% = yellow (compress email bodies, load reference-tier domains as summary only), 70–85% = orange (batch-summarize remaining emails, skip archive-tier domains entirely, compress briefing), >85% = red (save progress, generate partial briefing, recommend re-running for remaining domains). Pressure level displayed in health-check.md. Session quick-start (F15.39) uses pressure-aware loading: if previous session ended at orange/red, next session pre-loads less aggressively. | P1 |
| F15.42 | **OAuth Token Resilience Framework** *(v3.9)* — Proactive token health monitoring and recovery. Three capabilities: (1) **Pre-expiry refresh:** Check token expiry on every catch-up pre-flight (step 0). If any token expires within 7 days, attempt automatic refresh. Log result. (2) **Graceful degradation path:** If a token fails, the catch-up continues with available sources and clearly labels what's missing in the briefing. Never hard-fail on a single token. (3) **Guided re-auth flow:** When re-authentication is needed, provide step-by-step terminal commands with context: "Gmail token expired. Run: `python3 scripts/setup_google_oauth.py` — this will open a browser for OAuth consent. Takes ~2 minutes." Track token health history in health-check.md to detect patterns (e.g., tokens that expire every 7 days vs. 90 days). | P1 |
| F15.43 | **Email Volume Scaling** *(v3.9)* — Progressive strategies for handling email volume increases without catch-up degradation. Tier 1 (≤100 emails): Current individual processing. Tier 2 (100–300 emails): Batch-summarize by sender domain in groups of 20, expand only flagged items. Tier 3 (300–500 emails): Pre-classify by importance (sender reputation + subject keywords), process top 30% individually, batch-summarize bottom 70%. Tier 4 (>500 emails): Digest-only mode — one paragraph per domain, no individual email processing, flag count of unprocessed. Volume tier detected automatically during email fetch (step 4). Each tier's thresholds are configurable in settings.md. Prevents catch-up failures when returning from vacation or after extended gaps. | P1 |
| F15.44 | **Life Scorecard** *(v3.9)* — Quarterly and annual comprehensive life assessment aggregating data across all domains. Generated at end of each quarter (March, June, September, December) and annually. Sections: (1) **Goal Performance** — each active goal with trend line, (2) **Domain Health Matrix** — 18 domains rated 🟢🟡🔴 with key metric, (3) **Family Well-being** — per-member status summary, (4) **Financial Position** — net worth trajectory, savings rate, debt trajectory, (5) **Time Allocation** — where time went (work vs. family vs. personal vs. learning), (6) **Relationship Health** — contact frequency trends, reconnects overdue, (7) **Risk Dashboard** — immigration timeline, insurance adequacy, estate readiness, (8) **Year-over-Year Comparison** (annual only). Saved to `summaries/scorecard-YYYY-QN.md`. Designed as the "annual physical for your life." | P1 |
| F15.46 | **Post-Briefing Calibration Questions** *(v4.0)* — After each briefing, Artha asks 1–2 targeted calibration questions to improve accuracy and learn preferences. Questions are specific, not generic: "I surfaced 3 ParentSquare items today. Were any of them actually useful, or should I suppress that sender?" "I rated the PSE bill as 🟠 Urgent. Was that the right severity, or is it on autopay and should be 🔵 Info?" "I didn't surface anything from your learning domain this week. Is that because nothing happened, or because I'm missing data?" Questions selected based on: domains with lowest signal:noise ratio, new senders being classified for the first time, alerts that were dismissed in prior sessions, and domains where the user has made corrections. Answers feed directly into `memory.md` corrections and routing rules. Limited to 2 questions per session to avoid fatigue. | P1 |
| F15.47 | **PII Detection Footer** *(v4.0)* — Every briefing includes a footer section showing PII guard statistics for the current catch-up run. Displays: total items scanned, PII patterns detected (by type: SSN, CC, account numbers, passport, etc.), items filtered, zero leaks confirmed. Provides transparency into the pre-flight PII defense layer (Layer 1). Example: "🛡️ PII Guard: 47 emails scanned · 3 detections (2 CC, 1 routing#) · 0 leaks · Layer 2 LLM redaction: clean." If any PII detection occurs, the specific state file and domain are noted (without revealing the PII itself). Builds user trust in the privacy model. | P1 |
| F15.48 | **Effort Estimates & Power Half Hour** *(v4.0)* — Every open item in `open_items.md` gets an estimated effort level: ⚡ Quick (≤5 min), 🔨 Medium (5–30 min), 🏗️ Deep (30+ min). Effort estimated by Artha based on task type and historical completion patterns. Powers the **"Power Half Hour"** concept: when Artha detects a 30-minute calendar gap (or user asks "what can I knock out?"), it assembles a batch of ⚡ Quick items that can be completed in that window. Example: "You have 30 minutes before your 3 PM. Power Half Hour: (1) ⚡ Reply to school fundraiser email [2 min], (2) ⚡ Confirm Parth's dentist appointment [3 min], (3) ⚡ Review Costco membership renewal notice [5 min], (4) 🔨 Update HSA contribution election [15 min]. Total: ~25 min." Effort data also feeds into calendar-aware task scheduling (F8.6). | P1 |
| F15.49 | **Quarterly Privacy Audit Report** *(v4.0)* — Automated quarterly self-assessment of Artha's privacy posture. Report covers: (1) **Data inventory** — what state files exist, what data types they contain, encryption status, (2) **Access audit** — which OAuth tokens are active, when last refreshed, what scopes granted, (3) **PII filter effectiveness** — rolling stats from F15.47, detection accuracy, false positive rate, (4) **Data minimization check** — any state files storing more raw data than necessary? Any email bodies persisted when they should be extract-and-discard?, (5) **Encryption audit** — all high/critical state files encrypted? Keys in Keychain? No plaintext PII anywhere?, (6) **Third-party surface** — what data flows to Claude API, Gemini CLI, Copilot CLI? What mitigations are active? Saved to `summaries/privacy-audit-YYYY-QN.md`. Surfaced in quarterly life scorecard. | P1 |
| F15.50 | **Monthly Retrospective** *(v4.0)* — Auto-generated monthly summary synthesizing what happened across all domains. Generated during the first catch-up after the 1st of each month. Format: (1) **Month in Review** — 5 most significant events/decisions per domain, (2) **Goals Progress** — each goal's monthly trajectory with delta from prior month, (3) **Financial Summary** — income, expenses, net worth change, budget adherence, (4) **Family Highlights** — per-member notable events, (5) **Artha Performance** — accuracy pulse monthly roll-up, signal:noise trends, domains that improved/degraded, (6) **Next Month Preview** — known deadlines, renewals, and preparation items. Saved to `summaries/retro-YYYY-MM.md`. Shorter and more actionable than the quarterly scorecard; complements the weekly summary by providing a longer-horizon view. | P1 |
| F15.51 | **State Diff Command** *(v4.0)* — `/diff` slash command that shows what changed in state files since the last catch-up. For each modified state file, displays: fields added, fields changed (with before/after), fields removed, and new alerts generated. Enables the user to see exactly what Artha learned during the most recent catch-up without reading full state files. Example output: "Since last catch-up (2h ago): immigration.md: no changes · finance.md: PSE bill added ($300.63 due 3/26) · kids.md: Parth missing assignment flagged (AP Language) · goals.md: exercise goal moved from 🟢 to 🟡 (missed 2 sessions)." Diff data computed by comparing state files before and after catch-up processing. | P1 |
| F15.52 | **Ask Archana Delegation** *(v4.0)* — When Artha encounters a question or decision that requires Archana's input (shared domain: finance, kids, home, health, social, travel), route the question to Archana with full context via email or WhatsApp. Format: "Hey Archana — Artha has a question for you: [context summary]. [Specific question]. Reply to this message and Artha will incorporate your answer." Triggers: during catch-up when a shared-domain decision needs both spouses' input, during goal reviews for family goals, when a calendar conflict affects both parents. User must approve each delegation (human gate). Response processing: Archana's reply is parsed and incorporated into the relevant state file on the next catch-up. | P1 |
| F15.53 | **If You Have 5 Minutes** *(v4.0)* — Opportunistic micro-task suggestions surfaced when the user has a brief window. Triggered by: (1) User explicitly asks "what can I do in 5 minutes?", (2) Calendar shows a short gap between events, (3) End of a catch-up session with remaining time. Suggestions drawn from: ⚡ Quick open items (F15.48), overdue reconnects that need just a text/WhatsApp (F11.3), quick approvals pending in audit queue, brief calibration questions. Presented as a prioritized list: "5-minute wins: (1) Text happy birthday to Rahul (overdue 2 days), (2) Approve Costco membership auto-renewal, (3) Quick reply to school fundraiser ask. Time: ~4 min total." Different from Power Half Hour (F15.48) which batches for 30 min; this is micro-optimized for 5-minute opportunistic windows. | P1 |
| F15.54 | **Teach Me Mode** *(v4.0)* — When user says "teach me about [domain]" or "explain my [domain] situation", Artha synthesizes an explainer from state data, domain prompts, and prior decisions. Not a generic explanation — personalized to the user's actual data. Examples: "Teach me about my immigration situation" → timeline visualization of the GC process, where the family stands, what's next, what the risks are, explained in plain language. "Teach me about my insurance coverage" → what each policy covers, known gaps, how they interrelate. "Explain my mortgage" → current balance, payment structure, interest vs. principal, payoff timeline, refinance math. Designed for moments when Ved or Archana need to understand a complex domain they don't interact with daily. Uses extended thinking for synthesis. | P1 |
| F15.55 | **Natural Language State Queries** *(v4.0)* — Enhanced conversational access to state files using natural language questions that Artha resolves against structured state data. Goes beyond simple lookups to support: temporal queries ("What happened with immigration last month?"), comparison queries ("How does this month's spending compare to January?"), aggregation queries ("Total we've spent on kids' activities this year"), conditional queries ("What bills are due if I'm traveling next week?"), and cross-domain queries ("Show me everything related to Parth right now"). Artha decomposes the query, identifies relevant state files, extracts the data, and synthesizes a coherent answer. Replaces the need for users to know which state file contains which data. | P1 |

---

### FR-16 · Insurance & Risk Management

**Priority:** P1
**Summary:** Unified tracking of all insurance policies (auto, home, umbrella, life, disability), premium costs, renewal dates, coverage adequacy review, and life-event triggered reassessment.

**The problem:** Insurance policies are scattered across multiple carriers with no consolidated view of coverage, cost, or renewal timing. Annual renewal is the highest-leverage moment to review coverage and compare rates — but it slips by without proactive tracking. Critical life events (teen driver, home renovation, asset growth) should trigger coverage reviews but don't.

**Data sources:**
- Gmail/Outlook (policy renewal notices, premium payment confirmations, EOB/claims correspondence)
- Manual input (policy details, coverage limits, deductibles, agent contact info)
- Connected APIs (insurance carrier portals — future)

**Known/expected policies:**

| Policy | Type | Carrier | Notes |
|---|---|---|---|
| Auto Insurance | Vehicle liability + comprehensive | TBD | Covers family vehicles |
| Homeowners Insurance | Property + liability | TBD | Required by Wells Fargo mortgage |
| Umbrella / Liability | Excess liability | TBD | Recommended given asset profile |
| Life Insurance | Term life | Microsoft benefits (likely) | Verify coverage amount and beneficiaries |
| Long-Term Disability | LTD | Microsoft benefits (likely) | Verify coverage details |
| Short-Term Disability | STD | Microsoft benefits (likely) | Verify coverage details |

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F16.1 | **Insurance Policy Registry** — Master list of all active insurance policies with carrier, policy number, coverage limits, deductibles, premium amount, payment frequency, and renewal date. Linked to net worth calculation in FR-3. | P1 |
| F16.2 | **Premium Tracker** — Track total annual insurance cost across all policies. Parse premium payment confirmation emails. Alert on premium increases at renewal. Year-over-year trend. | P1 |
| F16.3 | **Renewal Calendar** — Alert 60 and 30 days before each policy renewal. Prompt: "Auto insurance renews in 30 days. Current premium: $X/6mo. Review coverage or shop rates?" | P0 |
| F16.4 | **Coverage Adequacy Review** — Annual prompted review: Does homeowners coverage match current home value (from FR-7 Zillow estimate)? Does auto coverage reflect current vehicles? Does umbrella policy cover total asset exposure? Surface gaps. | P1 |
| F16.5 | **Life Event Coverage Trigger** — When a life event is detected that should trigger an insurance review, proactively prompt. Triggers: Parth gets driver’s license (add to auto policy), home renovation (update homeowners), significant asset growth (umbrella review), new vehicle purchase, family member turns 26 (health plan change). | P1 |
| F16.6 | **Teen Driver Prep (Parth)** — Parth is approaching driving age. Track: learner’s permit timeline, driver’s ed completion, license eligibility date. Alert on auto insurance impact: "Adding a teen driver typically increases auto premiums 50–100%. Get quotes before Parth’s license date." | P1 |
| F16.7 | **Claims History Log** — Track all insurance claims filed, status, and outcomes. Maintain a history for rate negotiation context. | P2 |
| F16.8 | **Microsoft Benefits Optimizer** — During annual open enrollment, surface a benefits review checklist: life insurance coverage vs. needs, disability coverage adequacy, FSA/HSA election optimization, dental/vision plan comparison. Cross-reference with FR-6 (Health). | P1 |

---

### FR-17 · Vehicle Management

**Priority:** P1
**Summary:** Track vehicle registration, maintenance schedules, warranty status, fuel/charging costs, and driving milestones for the family.

**The problem:** Vehicle ownership generates recurring obligations (annual registration, emissions testing, oil changes, tire rotations, warranty expirations) that are tracked nowhere. With Parth approaching driving age, the complexity will increase (learner’s permit, driver’s ed, license, added vehicle/insurance).

**Data sources:**
- Gmail/Outlook (registration renewal notices from WA DOL, service reminders, warranty correspondence)
- Manual input (vehicle details, mileage, service history)

**Vehicle inventory:**

| Vehicle | Owner | Notes |
|---|---|---|
| TBD — Vehicle 1 | Family | Capture make, model, year, VIN, license plate |
| TBD — Vehicle 2 | Family | Capture make, model, year, VIN, license plate |

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F17.1 | **Vehicle Registry** — All family vehicles with make, model, year, VIN, license plate, purchase date, mileage, and linked insurance policy (FR-16). | P1 |
| F17.2 | **Registration Renewal Tracker** — WA annual registration renewal for each vehicle. Alert 60 and 30 days before expiry. Parse DOL renewal notices from email. Include emissions testing requirement if applicable. | P0 |
| F17.3 | **Maintenance Schedule** — Track manufacturer-recommended maintenance intervals: oil change (every 5K–7.5K miles or 6 months), tire rotation (every 7.5K miles), brake inspection, coolant flush, transmission service, air filter. Alert when due based on mileage or time. | P1 |
| F17.4 | **Service History Log** — Record all vehicle service visits with date, mileage, service performed, cost, and provider. Answer: "When was the last oil change on [vehicle]?" | P1 |
| F17.5 | **Warranty Tracker** — Track manufacturer warranty and any extended warranty coverage. Alert when warranty is approaching expiry: "Factory warranty on [vehicle] expires in 3 months / 2,000 miles. Consider extended warranty?" | P1 |
| F17.6 | **Fuel / Charging Cost Tracker** — Track monthly fuel or EV charging costs per vehicle. Detect anomalies (sudden increase may indicate maintenance need). Part of FR-3 spend tracking by category. | P2 |
| F17.7 | **Teen Driver Program (Parth)** — Structured milestone tracker for Parth’s driving journey: WA learner’s permit (age 15.5 eligible), driver’s ed enrollment and completion, required supervised driving hours (50 hours in WA), intermediate license (age 16), full license (age 17). Tied to FR-16 insurance impact. | P1 |
| F17.8 | **Recall Monitor** — Periodically check NHTSA recall database for active recalls on family vehicles by VIN. Alert immediately on safety recalls. | P2 |
| F17.9 | **Lease & Lifecycle Manager** — For leased vehicles, track lease term, residual value, mileage allowance vs. actual, and lease-end date. Reverse-schedule end-of-lease actions: pre-return inspection (90 days out), equity check — purchase vs. return analysis (120 days out), replacement vehicle research window (150 days out), lease-end cleaning and repair. For owned vehicles, track estimated remaining useful life and replacement planning horizon. | P1 |
| F17.10 | **Total Cost of Ownership (TCO) Calculator** — For each vehicle, calculate annualized total cost: lease/loan payment + insurance (from FR-16) + registration + maintenance + fuel/charging + depreciation estimate. Compare across vehicles. Use TCO data when evaluating replacement options: "Current vehicle TCO: $X/year. Comparable new lease: $Y/year. EV alternative: $Z/year (including fuel savings)." | P2 |

---

### FR-18 · Estate Planning & Legal Readiness

**Priority:** P1
**Summary:** Track estate planning documents, beneficiary designations, legal readiness, and ensure the family is protected in the event of an emergency.

**The problem:** Estate planning documents (wills, trusts, powers of attorney, guardianship designations) are critical but easy to neglect. Beneficiary designations across financial accounts may be outdated or inconsistent. As an immigrant family with complex financial and immigration status, legal readiness is especially important.

**Data sources:**
- OneDrive (legal document storage)
- Manual input (document dates, attorney info, beneficiary designations)
- Gmail/Outlook (attorney correspondence)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F18.1 | **Estate Document Registry** — Track existence, location, date, and review status of: Will (Vedprakash), Will (Archana), Revocable Living Trust (if applicable), Power of Attorney — Financial (both spouses), Power of Attorney — Healthcare / Advance Directive (both spouses), Guardianship designation for Parth and Trisha. | P1 |
| F18.2 | **Beneficiary Audit** — Annually prompt a review of beneficiary designations across all financial accounts (Fidelity, Vanguard, Morgan Stanley, E*Trade, Wells Fargo, life insurance, 401k, HSA). Flag inconsistencies: "Your Vanguard IRA lists a beneficiary last updated in 2019. Verify it matches your current wishes." | P1 |
| F18.3 | **Document Expiry & Review Cycle** — Estate documents should be reviewed every 3–5 years or on major life events. Track last review date and prompt: "Your will was last updated 4 years ago. Life changes since then: home purchase, job change, children's ages. Schedule attorney review?" | P1 |
| F18.4 | **Life Event Legal Trigger** — When Artha detects a major life event (home purchase, new job, child turning 18, immigration status change), prompt legal document review. Example: "Parth turns 18 in [date]. Guardianship designation will no longer apply. Update healthcare POA to include him as adult?" | P1 |
| F18.5 | **Emergency Access Guide** — Maintain a structured "In Case of Emergency" document: location of all critical documents, list of all financial accounts with institution and contact, insurance policies, attorney contact info, key passwords vault reference. Updated automatically as Artha’s knowledge graph grows. Stored encrypted. | P0 |
| F18.6 | **Attorney & Legal Provider Rolodex** — Track estate planning attorney, tax CPA, immigration attorney (Fragomen), and any other legal contacts with last engagement date and notes. | P2 |
| F18.7 | **Guardianship & Minor Children Planning** — Explicitly track: Who is the designated guardian for Parth and Trisha if both parents are incapacitated? Is this documented? Does the guardian know? When do the children age out (18)? | P1 |
| F18.8 | **Emergency Contact Wallet Card** *(v4.0)* — Generate a printable/digital emergency contact card for each family member. Contents: name, emergency contacts (2–3 prioritized), primary care physician, insurance policy number, blood type (if known), allergies, current medications, immigration status (generic — e.g., "valid work authorization" without sensitive details), attorney contact, and location of the Emergency Access Guide (F18.5). Output formats: PDF (printable wallet card), Apple Wallet pass, or plain text for phone lock screen. Auto-regenerated when any source data changes. Each family member gets a personalized card. | P1 |

---

## 8. Goal Intelligence Engine — Deep Dive

The Goal Intelligence Engine (FR-13) is the feature that most distinguishes Artha from a monitoring tool. Most personal finance and productivity apps track metrics. Artha connects metrics to meaning.

### 8.1 — Goal Model

Every goal in Artha has a consistent structure:

```
Goal {
  id:           unique identifier
  name:         human-readable label
  domain:       one of the life domains (18 FRs)
  type:         Outcome | Habit | Milestone
  objective:    what you want to achieve (free text)
  metric:       what Artha will measure (quantifiable)
  current:      current measured value
  target:       target value
  deadline:     by when
  cadence:      how often to measure (daily / weekly / monthly)
  data_source:  which Artha agent provides the metric automatically
  status:       On Track | At Risk | Behind | Achieved | Paused
}
```

### 8.2 — Goal Types

**Outcome Goals** — A specific end state you want to reach by a date.
Example: *"Net worth of $X by December 31, 2026"*
Artha measures: Monthly net worth snapshot from FR-3. Reports trajectory. Alerts if trend line will miss target.

**Habit Goals** — A recurring behavior you want to sustain over time.
Example: *"Exercise at least 4 days per week"*
Artha measures: Manual check-in (or future wearable integration). Tracks streak and weekly completion rate. Morning briefing includes habit status.

**Milestone Goals** — A defined event or achievement to reach.
Example: *"Complete ByteByteGo system design course by Q2 2026"*
Artha measures: Course completion percentage (from FR-10). Alerts when pace is insufficient to hit deadline.

### 8.3 — Automatic Metric Wiring

For each goal, Artha identifies the data source that can prove progress without manual entry. This is the key to sustainability — goals that require manual updates die. Goals that update themselves persist.

| Goal example | Auto data source | Artha agent |
|---|---|---|
| Net worth target | Fidelity + Vanguard + Wells Fargo balance emails | FR-3 Finance |
| Learning hours/month | Obsidian vault activity + course logins | FR-10 Learning |
| Amazon spend < $X/month | Amazon order emails + credit card alerts | FR-9 Shopping |
| Parth's GPA target | Canvas grade emails, LWSD alerts | FR-4 Kids |
| Immigration milestone | Case status emails from Fragomen/USCIS | FR-2 Immigration |
| Reading goal | Manual check-in (with Obsidian book note detection) | FR-10 Learning |
| Work-life balance | Work email timestamp analysis | FR-14 Boundary |
| PSE energy bill < $X | PSE bill emails | FR-7 Home |

### 8.4 — The Weekly Goal Scorecard

Every Sunday summary includes a full goal scorecard. Example format:

```
ARTHA GOAL SCORECARD · Week of March 3–9, 2026

FINANCIAL
  Net Worth 2026 Target          ██████░░░░  62%  → On Track
  Monthly Amazon Spend < $X      ████████░░  78%  ⚠ At Risk ($X over budget)

FAMILY
  Parth GPA ≥ 3.8               █████░░░░░  54%  ⚠ Missing assignments flagged
  Quality family time ≥ 10h/wk  ████████░░  80%  → On Track

LEARNING
  12 books in 2026               ███░░░░░░░  25%  → On Track (3/12)
  ByteByteGo course complete Q2  ██░░░░░░░░  22%  ⚠ Behind — 2h/week needed

IMMIGRATION
  H-4 EAD Renewal Filed         ██████████ 100%  ✓ Achieved

HEALTH
  Exercise 4x/week               ██████░░░░  60%  → On Track (avg 2.4x)

WORK-LIFE
  Protected personal time ≥ 20h  ████░░░░░░  45%  🔴 Behind — 4 late work nights
```

### 8.5 — Recommendation Engine

When a goal is At Risk or Behind, Artha surfaces one specific, contextual recommendation — not generic advice.

| Goal status | Artha recommendation example |
|---|---|
| Amazon spend over budget | "2 pending Amazon orders totaling $X. Review before checkout." |
| Parth GPA at risk | "3 missing assignments in AP Language. Parent portal login needed." |
| Exercise goal behind | "No workouts logged Wed–Sun. Nearest gap in calendar: Tuesday 6pm." |
| ByteByteGo course behind | "Last session: 12 days ago. You need 2.5h this week to stay on track." |
| Work-life goal behind | "4 late-night work email sessions this week. Tuesday and Thursday after 10pm." |

### 8.6 — Conversational Goal Creation

Goals are defined through natural language conversation, not structured forms. The structured schema (8.1) is the internal storage format; the creation experience is conversational.

**Example interaction:**

> **User:** "I want to make sure we're saving enough for Parth's college."
> **Artha:** "I can help track that. Based on current Fidelity 529 balance and Parth's graduation timeline (Spring 2028), I'd suggest tracking:
> - Net 529 contributions per month (Habit goal)
> - Total 529 balance target by enrollment (Outcome goal)
> - Scholarship application milestones (Milestone goal)
>
> Should I set this up? I'll wire the 529 metric to your Fidelity balance emails automatically."

Claude infers the goal type, suggests metrics, identifies data sources, and proposes the structured schema for confirmation. One conversation creates a fully wired goal — no manual schema filling.

### 8.7 — Goal Conflict Detection

Goals can compete for the same resources (time, money, attention). Artha explicitly models goal tension:

| Conflict type | Example | Artha response |
|---|---|---|
| Financial trade-off | Savings goal vs. family travel goal | "Your savings goal is on track but travel goal shows zero progress. These may be in tension — adjust either target?" |
| Time trade-off | Parth SAT prep vs. Economics Club | "SAT prep target requires 8 hrs/week. Economics Club competition is the same weekend. Prioritize?" |
| Work-life trade-off | Career growth (via Vega signal) vs. protected family time | "Work hours exceeded boundary 3 of 5 days this week. Protected time goal is at risk." |
| Parent attention split | Parth college prep vs. Trisha academic support | "Parth college prep consumed 6 planning hours this week. Trisha's last 2 grade alerts were unaddressed." |

When two active goals have metrics moving in opposing directions, Artha surfaces the trade-off explicitly rather than reporting both as independent items.

### 8.8 — Goal Trajectory Forecasting

For Outcome goals, Artha doesn't just report current status — it projects forward.

**Forecasting model:**
1. Calculate current trend line from historical data points
2. Project to deadline
3. Compare projected outcome to target
4. When deviation exceeds 10%, trigger replanning prompt

**Example forecast:**

```
NET WORTH GOAL · Forecast as of March 7, 2026

Target:        $X by December 31, 2026
Current:       $Y (62% of target)
Trend:         +$Z/month (last 3 months average)
Projected:     $W by December 31 (92% of target)
Gap:           -$V from target

⚠ Projected to miss target by $V
OPTIONS:
  1. Increase monthly savings by $A → closes gap
  2. Extend deadline to March 2027 → current pace sufficient
  3. Adjust target to $W → matches current trajectory
```

### 8.9 — Behavioral Nudge Engine

Informed by behavioral science research (implementation intentions, commitment devices, streak psychology, friction reduction):

- **Implementation intentions:** Not just "exercise 4x/week" but "I will exercise on Mon/Wed/Fri/Sat mornings at 6 AM at the gym"
- **Calendar integration:** "Your exercise goal is behind. Best open slot is Tuesday 6 PM after Parth's pickup — schedule it?"
- **Streak tracking:** All habit goals track current streak length with positive reinforcement at milestones
- **Friction reduction:** Cross-reference calendar, location, and family logistics to find lowest-friction moments for goal activities
- **Commitment devices:** "Would you like me to schedule a calendar block for your ByteByteGo session this Saturday?"

### 8.10 — Seasonal & Cyclical Awareness

Personal life is deeply cyclical. After one full year of data, Artha automatically detects and models seasonal patterns:

| Cycle | Pattern | Goal impact |
|---|---|---|
| School year | Grades dip in Q3 (AP exam pressure) | Adjust academic goal expectations seasonally |
| Holiday spending | Amazon/retail spikes 50% in Nov–Dec | Budget goals need Q1–Q3 under-spending buffer |
| Tax season | Jan–Apr document tracking, refund income | Financial goal metrics temporarily distorted |
| Summer travel | Jun–Aug spending spike, schedule disruption | Habit goals need adjusted cadence |
| Visa Bulletin | Monthly priority date movements | Immigration milestones shift with bulletin |
| School enrollment | Feb–Mar registration windows | Milestone goals for SAT, AP exams cluster here |

### 8.11 — Leading Indicators *(v3.8)*

Lagging metrics tell you what already happened. Leading indicators tell you what's about to happen. For every goal, Artha identifies and tracks leading indicators alongside the goal's primary metric.

**Principle:** A goal's primary metric (e.g., net worth) is a lagging indicator — it reflects past decisions. Leading indicators (e.g., savings rate, spending trajectory, upcoming large expenses) predict whether the lagging metric will move in the right direction. Artha surfaces both.

**Leading indicator extraction rules:**

| Goal type | Lagging metric (existing) | Leading indicators (new) |
|---|---|---|
| Financial | Net worth, savings balance | Savings rate trend, upcoming bills, discretionary spend trajectory, income changes |
| Academic | GPA, assignment scores | Assignment completion rate, missing assignments, teacher feedback frequency, study hours |
| Health | Weight, A1C, blood pressure | Exercise frequency, appointment adherence, medication compliance, sleep quality signals |
| Immigration | Case status, approval dates | Processing time estimates, Visa Bulletin movements, attorney communication frequency, document expiry proximity |
| Habit | Streak length, completion % | Session frequency trend, time-of-day patterns, skip-day clustering, friction signals |
| Relationship *(v3.8)* | Contact recency, reciprocity balance | Communication frequency trend, response time changes, group participation rate |

**How it works:**
1. Each domain prompt's `leading_indicators` extraction block defines what to track (see tech spec §6.1)
2. During catch-up, leading indicators are extracted alongside standard state updates
3. Goal scorecard shows leading + lagging side by side: "Net worth: $X (↑2% this month). Leading: savings rate 18% (target 20%), no large expenses next 30 days, bonus expected Q2"
4. Weekly summary highlights leading indicator divergence: "Parth's assignment completion rate dropped 15% this week — GPA impact likely in 2–3 weeks"

**Alert triggers:** When a leading indicator diverges from the trajectory needed to hit the goal target, Artha surfaces an early warning — before the lagging metric moves. This is the difference between "your GPA dropped" (too late) and "your assignment completion rate is declining — GPA risk in 2 weeks" (actionable).

**Leading Indicator Auto-Discovery** *(v4.0)*: In addition to the manually-defined extraction blocks in domain prompts, Artha automatically discovers new leading indicators by analyzing cross-domain correlations. After 30+ days of data, the coaching engine identifies patterns: "When your calendar density exceeds 12 events/week, your exercise goal completion drops 40% the following week." "When Parth has 3+ assignments due on the same day, next-day scores average 15% lower." Auto-discovered indicators are proposed for confirmation: "Artha discovered: your spend increases ~30% in weeks with no meal-prep calendar block. Track 'meal-prep frequency' as a leading indicator for your food budget goal?" Confirmed indicators are added to the domain prompt's `leading_indicators` block and contribute to goal trajectory forecasting.

---

## 9. Architecture

Artha is a **pull-based personal intelligence system** built on Claude Code as the runtime. There is no custom daemon, no background process, and no always-on infrastructure. The user triggers Artha by opening a Claude Code session and saying "catch me up."

> **v3.0 Architectural Pivot:** The v2.2 architecture assumed an always-on Mac with a macOS LaunchAgent daemon. In practice, the user's Mac is used only some weekday evenings and weekends. Combined with a hard privacy requirement (no cloud VMs, no cloud state storage), a pull-based model was adopted. Personal life obligations operate on days/weeks/months timescales — no personal domain in Artha requires sub-hour alerting. A daily or every-other-day pull cadence is architecturally sufficient for ALL 18 Functional Requirements.

### 9.1 — System Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        ARTHA OS                              │
│          (Claude Code + Artha.md + MCP Tools)                │
│                                                              │
│  ┌──────────────┐    ┌───────────────────────────────────┐  │
│  │  CATCH-UP    │    │     SEMANTIC REASONING LAYER       │  │
│  │  WORKFLOW    │    │  (Claude-native context-aware       │  │
│  │              │    │   prioritization & insight)         │  │
│  │  1. Fetch    │    └───────────────┬───────────────────┘  │
│  │  2. Route    │                    │                       │
│  │  3. Update   │    ┌───────────────▼───────────────────┐  │
│  │  4. Alert    │    │      DOMAIN PROMPT LIBRARY         │  │
│  │  5. Brief    │    │                                   │  │
│  │  6. Email    │◄───│  ~/OneDrive/Artha/prompts/         │  │
│  │              │    │  comms.md · immigration.md         │  │
│  │  Triggered   │    │  finance.md · kids.md              │  │
│  │  by user:    │    │  travel.md · health.md             │  │
│  │  "catch me   │    │  home.md · calendar.md             │  │
│  │   up"        │    │  + 10 more domain prompts          │  │
│  └──────────────┘    └───────────────┬───────────────────┘  │
│                                      │                       │
│  ┌──────────────┐    ┌───────────────▼───────────────────┐  │
│  │ HUMAN GATE   │    │          MCP TOOL LAYER            │  │
│  │              │    │                                   │  │
│  │  All writes  │    │  Gmail MCP (OAuth)                │  │
│  │  proposed in │    │  Google Calendar MCP               │  │
│  │  conversation│    │  Filesystem (read/write state)     │  │
│  │  User        │    │  Email sending (briefing delivery) │  │
│  │  confirms    │    └───────────────┬───────────────────┘  │
│  └──────────────┘                    │                       │
│                      ┌───────────────▼───────────────────┐  │
│  ┌──────────────┐    │      LOCAL STATE FILES             │  │
│  │ CONVERSATION │    │                                   │  │
│  │ MEMORY       │    │  ~/OneDrive/Artha/state/           │  │
│  │              │    │  Markdown files (one per domain)   │  │
│  │  Artha.md  │    │  YAML frontmatter + prose          │  │
│  │  instructions│    │  Goal definitions + progress       │  │
│  │  + state dir │    │  Conversation memory               │  │
│  │  + audit.md  │    │  Audit log                         │  │
│  └──────────────┘    └───────────────────────────────────┘  │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  OUTPUT CHANNELS                       │  │
│  │                                                       │  │
│  │  Terminal (Mac)  ·  Email (iPhone, Windows, any)      │  │
│  │  Claude iOS App (Project with cached state snapshots) │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 9.2 — Domain Prompt Library

Each Functional Requirement is backed by a **domain prompt file** in `~/OneDrive/Artha/prompts/`. These are plain Markdown files containing:
- Domain-specific extraction rules and patterns
- Alert threshold definitions
- State file update instructions
- Briefing contribution format
- Known sender/subject patterns for routing

**Adding a new domain = adding a new prompt file. No code changes required.**

CLAUDE.md (thin loader that delegates to Artha.md, the primary instruction file) references all domain prompts and routes incoming data to the correct one based on sender/subject/content patterns. Claude's native reasoning handles the routing — no hardcoded router logic.

| Domain Prompt | FR | Primary MCP Tool | Briefing Contribution |
|---|---|---|---|
| `comms.md` | FR-1 | Gmail MCP | Actionable emails summary |
| `immigration.md` | FR-2 | Gmail MCP (Outlook via forwarding) | Timeline & deadline status |
| `finance.md` | FR-3 | Gmail MCP | Bills, spending, alerts |
| `kids.md` | FR-4 | Gmail MCP | Grades, activities, deadlines |
| `travel.md` | FR-5 | Gmail MCP, Calendar MCP | Upcoming trips, prep tasks |
| `health.md` | FR-6 | Gmail MCP | Appointments, Rx refills |
| `home.md` | FR-7 | Gmail MCP | Maintenance, utilities |
| `calendar.md` | FR-8 | Google Calendar MCP | Today's schedule, conflicts |
| `shopping.md` | FR-9 | Gmail MCP | Deliveries, price alerts |
| `learning.md` | FR-10 | Gmail MCP | Course progress, reading |
| `social.md` | FR-11 | Calendar MCP | Upcoming events, follow-ups |
| `digital.md` | FR-12 | Gmail MCP | Subscriptions, renewals |
| `goals.md` | FR-13 | All state files | Goal progress, forecasts |
| `boundary.md` | FR-14 | Gmail MCP (Outlook timestamps) | Work-life balance check |
| `insurance.md` | FR-16 | Gmail MCP | Policy status, renewals |
| `vehicle.md` | FR-17 | Gmail MCP | Registration, maintenance |
| `estate.md` | FR-18 | Filesystem | Document inventory |

### 9.3 — OneDrive-Synced State Store

Artha's world model lives in a **OneDrive-synced folder** accessible from Mac, iPhone, and Windows. **Markdown-only initially** — no SQLite, no vector database, no event sourcing.

**Why OneDrive sync:**
- All devices (Mac, iPhone, Windows) see the same state — zero staleness
- Eliminates the manual snapshot upload pattern for iPhone access
- OneDrive provides built-in versioning and soft-delete recovery
- Sensitive state files are `age`-encrypted before sync — OneDrive stores opaque `.age` files
- Mac is the sole writer (runs catch-up); iPhone and Windows are read-only consumers
- No custom sync code — OneDrive's native sync handles everything

**Why Markdown-only:**
- All active state across 18 domains fits comfortably within Claude's 200K context window (~36K tokens for all domain state files)
- Markdown is human-readable, git-diffable, and trivially debuggable
- YAML frontmatter provides structured queryable data within the Markdown format
- If the LLM misinterprets something, the user can open the file and correct it directly
- SQLite/RAG can be added in Phase 2 if state volume exceeds context limits

**Directory structure (synced via OneDrive):**
```
~/OneDrive/Artha/                # Configurable path — synced across all devices
├── CLAUDE.md                    # Thin loader (3 lines — delegates to Artha.md)
├── Artha.md                     # Primary instruction file (identity, workflow, routing, rules)
├── prompts/                     # Domain prompt library
│   ├── comms.md
│   ├── immigration.md
│   ├── finance.md
│   └── ... (one per FR)
├── state/                       # Living world model
│   ├── calendar.md              # Standard — synced as plaintext
│   ├── kids.md                  # Standard — synced as plaintext
│   ├── shopping.md              # Standard — synced as plaintext
│   ├── goals.md                 # Standard — synced as plaintext
│   ├── memory.md                # Standard — synced as plaintext
│   ├── health-check.md          # Standard — synced as plaintext
│   ├── finance.md.age           # High — synced ENCRYPTED
│   ├── health.md.age            # High — synced ENCRYPTED
│   ├── insurance.md.age         # High — synced ENCRYPTED
│   ├── immigration.md.age       # Critical — synced ENCRYPTED
│   ├── estate.md.age            # Critical — synced ENCRYPTED
│   ├── audit.md.age             # Encrypted — contains sensitive refs
│   └── ... (one per domain)
├── briefings/                   # Archive of past catch-up briefings
│   └── 2026-03-07.md
├── summaries/                   # Archive of weekly summaries
│   └── 2026-W10.md
├── config/                      # Settings
│   └── settings.md              # Alert thresholds, email targets, sync path
└── scripts/                     # Helper scripts
    └── vault.sh                 # Encrypt/decrypt sensitive state files
```

**Sync path configuration** (in `settings.md`):
```yaml
sync:
  provider: onedrive
  path: ~/OneDrive/Artha
  # Mac: ~/OneDrive/Artha
  # Windows: C:\Users\ved\OneDrive\Artha
  # iPhone: OneDrive app → Artha folder (read-only)
  encrypt_before_sync: true
  encryption_key_location: keychain  # macOS Keychain / Windows Credential Manager
```

**Write model:** Mac is the **sole writer**. Catch-up runs on Mac → updates state files → OneDrive syncs to all devices. iPhone and Windows only read. This eliminates sync conflicts entirely — there is always exactly one writer.

**Backup strategy:**
- **Primary:** OneDrive built-in versioning (30-day version history, soft-delete recovery)
- **Secondary:** Time Machine (encrypted local backup on Mac)
- **Encryption keys:** Stored in device-local credential stores (macOS Keychain, Windows Credential Manager) — never on OneDrive. If Mac is lost, keys must be re-provisioned on new device.

**Data sensitivity classification:** Each state file declares a `sensitivity` level and `access_scope` that controls how its data flows through output channels:

| Sensitivity | Examples | Emailed Briefing | iPhone Snapshot | Terminal |
|---|---|---|---|---|
| `standard` | calendar, kids school events, shopping | Full detail | Included | Full detail |
| `high` | finance (balances, bills), health | Summary only (e.g., "Finance: 2 items, no alerts") | Excluded | Full detail |
| `critical` | immigration (case numbers), tax data, estate | Summary only | Excluded | Full detail |

**Document repository access (Phase 2+):** When Artha gains access to the document repository (tax returns, financial statements, legal documents), it follows an **extract-and-discard** policy:
- Claude reads the document content via filesystem MCP
- Extracts structured data (e.g., AGI, filing date, refund amount) into the domain state file
- Raw document content is **never** stored in state files — the document stays in its original repository location untouched
- State files contain summaries and extracted fields, not wholesale document text
- Sensitive identifiers (SSN, bank routing numbers, EINs) are redacted per the domain prompt's redaction rules

**State file format example:**
```markdown
---
domain: immigration
last_updated: 2026-03-07T18:30:00-08:00
last_catch_up: 2026-03-07T18:30:00-08:00
alert_level: yellow
sensitivity: critical
access_scope: catch-up-only
---

## Current Status
- H-1B: Active, valid through 2027-10-15
- I-140: Approved (EB-2), PD: 2019-04-15
- AOS (I-485): Pending, filed 2024-01-20

## Active Deadlines
- **H-1B Extension**: File by 2027-04-15 (6 months before expiry)
- **EAD Renewal**: File by 2026-08-01 (current EAD expires 2026-11-15)

## Recent Activity
- 2026-03-05: Email from Fragomen re: I-140 priority date advancement
- 2026-03-01: Visa Bulletin shows EB-2 India PD: 2019-01-01

## Parth (Dependent)
- Age-out date: 2030-06-15 (CSPA calculation)
- Current status: H-4
```

### 9.4 — Catch-Up Workflow

The catch-up workflow is Artha's primary operation. It replaces the v2.2 daemon entirely.

**Trigger:** User opens Claude Code in `~/OneDrive/Artha/` and says "catch me up" (or any equivalent — "what did I miss", "morning briefing", "SITREP").

**Workflow steps (orchestrated by Artha.md instructions, no custom code beyond `vault.sh` + `pii_guard.sh` + `safe_cli.sh`):**

0. **⭐ Pre-flight go/no-go gate** — before touching any data, verify: OAuth token files exist at `~/.artha-tokens/`, `gmail_fetch.py --health` exits 0, `gcal_fetch.py --health` exits 0, no active lock file (or stale lock >30 min → auto-clear with warning), vault operational. ⚠ HALT with `⛔ Pre-flight failed: [check] — [error]` if any check fails. Log gate result to `health-check.md`. This prevents silent-omission briefings (e.g., catch-up #1 showed 0 calendar events when Family calendar wasn't configured). See tech spec §7.1 step 0.
1. **Decrypt sensitive state files** via `./scripts/vault.sh decrypt` (automated via Claude Code PreToolUse hook)
2. **Read last run timestamp** from `~/OneDrive/Artha/state/health-check.md`
2b. **Digest mode check** *(v3.8 — Workstream H)* — if >48 hours since last catch-up, set `digest_mode: true` for this session. This triggers priority-tier grouping in step 8 and the “What You Missed” briefing variant (see F15.26, tech spec §5.1).
3. **Fetch new emails + calendar events IN PARALLEL** — Gmail MCP (all emails since last run, across configured accounts) + Google Calendar MCP (today + next 7 days) — executed simultaneously via Claude Code parallel tool invocation
4. **⭐ Pre-flight PII filter** — run `./scripts/pii_guard.sh filter` on extracted data before state file writes. Detects SSN, credit card numbers, bank routing/account numbers, passport numbers, A-numbers, ITIN, driver's license patterns. Replaces with `[PII-FILTERED-*]` tokens. ⚠ HALT catch-up if filter fails (non-zero exit). **Note:** Claude sees raw email content via MCP (unavoidable — MCP returns data directly into context). The filter validates extracted data *before* it is persisted to state files. See tech spec §8.6 for data flow details.
5. **Email content pre-processing** *(enhanced v3.8 — Workstream E)* — for each email: strip HTML tags (retain text only), remove footers/disclaimers/unsubscribe blocks, collapse intermediate quoted replies in threads (keep latest reply + original), **suppress marketing/newsletter emails** (apply sender allowlist; unrecognized marketing senders get subject-line-only extraction), enforce **per-email token budget of 1,500 tokens** (hard cap — truncate with "[TRUNCATED]" marker), **batch summarization for >50 emails** (group by sender pattern, summarize groups instead of individual processing). Prevents context window bloat from marketing HTML and thread repetition. Handled by Claude inline; falls back to `email_prefilter.sh` script only if >20% of catch-ups hit context window limits (see tech spec §9.2).
5b. **Tiered context loading** *(v3.8 — Workstream F)* — load state files using the tiered context architecture (§9.8): Always tier (health-check, open_items, memory — always loaded), Active tier (domains with new data in this catch-up — full load), Reference tier (domains with no new data but referenced by active domains — summary load), Archive tier (domains inactive >30 days — skip unless explicitly queried). Reduces context window usage by 30–40% on typical catch-ups. See §9.8 for tier definitions.
6. **Route each item** to the appropriate domain prompt based on sender/subject/content
7. **For each domain with new data:**
   - Apply the domain prompt's extraction rules
   - Apply §12 redaction rules (Layer 2 PII defense)
   - **Apply deduplication rules** — before creating new state entries, check for existing entries from the same source for the same item (receipt number, bill ID, event date + source). Update in-place rather than duplicating. See tech spec §6.1 for domain-specific dedup patterns.
   - Update the domain's state file in `~/OneDrive/Artha/state/`
   - Evaluate alert thresholds defined in the domain prompt
7b. **Update open_items.md** — for each actionable item extracted across all domains, check `~/OneDrive/Artha/state/open_items.md` for an existing matching entry (fuzzy match on description + deadline). Add only new items; set `todo_id: ""` for items not yet synced to Microsoft To Do. Re-surface any items with `status: open` and `deadline < today` as overdue. See tech spec §4.7 for schema.
8. **Synthesize briefing** *(enhanced v3.8 — Workstream G)* — aggregate all domain updates into the structured briefing format. **Apply the ONE THING reasoning chain:** score every candidate insight/alert using URGENCY (× time-sensitivity and deadline proximity) × IMPACT (consequence magnitude if ignored) × AGENCY (can the user actually act on this today?). The highest-scoring item becomes the “ONE THING” featured at the top of the briefing. If `digest_mode: true` (step 2b), use priority-tier grouping (Critical → Warning → Notable → FYI) with “What You Missed” header instead of standard format.
9. **Web research via Gemini CLI** — for domains that need external data (Visa Bulletin, property values, recall checks), delegate web research to Gemini CLI at $0 cost instead of consuming Claude API tokens (see tech spec §3.7.4)
10. **Evaluate cross-domain insights** — check for patterns that span domains (e.g., immigration deadline + travel plan conflict). **Auto-generate decision graph entries** *(v3.8 — Workstream C)* when cross-domain reasoning produces a decision point — log to `state/decisions.md` with context, alternatives considered, and affected domains.
11. **Ensemble reasoning (if triggered)** — for high-stakes decisions in critical domains (immigration, finance, estate), generate responses from all three LLMs (Claude, Gemini, Copilot) and synthesize the best answer (see tech spec §3.7.3)
12. **Surface alerts** — present any threshold crossings with severity level
13. **Propose actions** — if any write actions are recommended (send email, add calendar event, WhatsApp message), present as structured Action Proposals for user approval (see tech spec §7.4.1)
14. **Email briefing** — send the briefing to the configured email address for cross-device access
14b. **Sync to Microsoft To Do** (Phase 1B, T-1B.6.3) — run `python3 scripts/todo_sync.py` to push new `open_items.md` entries (those with `todo_id: ""`) to the appropriate domain-tagged Microsoft To Do list. Pull completion status of previously synced tasks to close resolved items. This is the bridge between Artha's catch-up and the user's daily task manager on iPhone.
15. **Update health-check** — write current timestamp + run statistics + CLI health status to `~/OneDrive/Artha/state/health-check.md`
16. **Archive briefing** — save to `~/OneDrive/Artha/briefings/YYYY-MM-DD.md`
17. **Log PII filter stats** — append detection summary to `~/OneDrive/Artha/state/audit.md`
18. **Encrypt sensitive state files** via `./scripts/vault.sh encrypt` (automated via Claude Code Stop hook)

**Progressive fallback:** If Claude proves unreliable at any specific step (e.g., OAuth token refresh, email sending), that step — and only that step — gets extracted into a minimal Python helper script. Target: zero custom code at launch beyond `vault.sh` and `pii_guard.sh`.

### 9.5 — Model Tiering Strategy

Claude Code handles model selection internally. Artha.md specifies **intent** rather than explicit model routing:

- **Standard processing:** Email parsing, state file updates, on-demand chat, calendar review
- **Extended thinking:** Weekly summary synthesis, cross-domain insight generation, immigration timeline reasoning, goal conflict analysis

**Multi-LLM routing (cost optimization):**
- **Web research → Gemini CLI:** $0 (free quota). Visa Bulletin, property values, recalls, price comparisons.
- **Script/config validation → Copilot CLI:** $0 (free quota). vault.sh review, Artha.md validation.
- **Visual generation → Gemini Imagen:** $0 (free quota). Festival cards, birthday greetings.
- **All reasoning, state management, MCP tools → Claude:** Paid API. Core orchestration capability.
- **High-stakes decisions → Ensemble (all 3):** Extra Gemini/Copilot calls at $0; enriches Claude's reasoning.

**Cost optimization:**
- **Prompt caching:** Artha.md system instructions + all state files are cached across the session. Domain prompts loaded on-demand.
- **Batch processing:** All emails processed in a single catch-up session rather than individually, amortizing context loading costs
- **Multi-LLM routing:** ~$3–6/month savings by delegating research tasks to free-quota CLIs
- **Target:** <$50/month at daily catch-up cadence with ~100 emails/day across all accounts
- **Cost validation:** Track actual API cost per catch-up via Claude API usage dashboard + Gemini/Copilot quota usage. Adjust domain prompt verbosity if costs exceed target.

### 9.6 — Integration Adapter Pattern

Domain prompts serve as the adapter layer. Each prompt defines the extraction schema for its domain — the prompt IS the adapter.

**Pattern:**
- **Domain prompt** defines the expected output format (e.g., the immigration prompt specifies: extract case type, receipt number, deadline, attorney action items)
- **When providers change** (e.g., switching utilities, changing lawyers), the domain prompt is updated with new sender patterns and extraction rules — no code changes
- **LLM-native adaptation:** Claude naturally handles format variations in emails from different senders. The prompt defines WHAT to extract, not HOW to parse.
- **Geographic portability:** If the family relocates, domain prompts are updated with new provider patterns. State files are archived and fresh ones created for the new location.

### 9.7 — Context Window Management

At initial scale, all of Artha's state fits within a single Claude context window:

- 18 domain state files × ~2K tokens each = ~36K tokens
- Artha.md + active domain prompts = ~10K tokens
- Email batch (100 emails × ~500 tokens) = ~50K tokens
- Conversation history = ~10K tokens
- **Total per catch-up: ~106K tokens** — well within 200K limit

**Email pre-filtering strategy (token management):**
The 50K email token estimate assumes pre-processed content. Raw HTML emails can be 5–10× larger than their text equivalent. Step 5 of the catch-up workflow (§9.4) applies pre-processing before domain routing:
- HTML → text conversion eliminates markup bloat (typical 3–5× reduction)
- Thread truncation collapses quoted reply chains (prevents exponential growth in long threads)
- Footer/disclaimer removal strips legal boilerplate present in most corporate emails
- Per-email truncation cap (~2K tokens) provides a hard ceiling

Without pre-filtering, a batch of 100 HTML-heavy emails could consume 250–500K tokens — exceeding the context window. Pre-filtering is handled inline by Claude (no script) unless catch-up failures trigger the `email_prefilter.sh` progressive fallback (tech spec §9.2).

**When state outgrows context (Phase 2+):**
- State file compression: Archive old entries, keep only current + last 30 days
- Selective loading: On-demand chat loads only the relevant domain state, not all 18
- SQLite introduction: Move historical data to SQLite, keep active state in Markdown
- RAG: Vector indexing for conversation memory and historical briefings

### 9.8 — Tiered Context Architecture *(v3.8 — Workstream F)*

Rather than loading all 18 domain state files on every catch-up (consuming ~36K tokens even when most domains have no new data), Artha uses a tiered loading strategy based on domain activity.

**Tier definitions:**

| Tier | Load behavior | Criteria | Token impact |
|---|---|---|---|
| **Always** | Full load every catch-up | `health-check.md`, `open_items.md`, `memory.md`, `goals.md` | ~8K tokens (fixed) |
| **Active** | Full load | Domain received new data in this catch-up (email routed to it, calendar event matched) | Variable — only active domains |
| **Reference** | Summary load (YAML frontmatter + alerts section only) | Domain referenced by an active domain's cross-domain rules but has no new data itself | ~500 tokens per domain |
| **Archive** | Skip entirely | Domain with `last_activity` >30 days and no pending alerts or open items | 0 tokens |

**Implementation:**
- Each state file's YAML frontmatter includes `last_activity: <ISO timestamp>` (updated on every state write)
- Step 5b of the catch-up workflow (§9.4) applies tier classification before domain processing
- On-demand queries (`/domain immigration`) always load the requested domain at full resolution regardless of tier
- Tier assignment is logged in `health-check.md` for observability

**Expected savings:** On a typical catch-up where 5–7 of 18 domains have new data, tiered loading reduces state file token consumption from ~36K to ~20K (30–40% reduction). Combined with the email pre-processing enhancements (Workstream E), total context consumption drops from ~106K to ~75K — providing significant headroom for the relationship graph (Workstream A) and decision/scenario state files (Workstreams C, D).

---

## 10. Autonomy Framework

Artha mirrors Vega's earned autonomy model exactly. There is no shortcut to autonomy — it is earned through demonstrated reliability.

### Trust Level 0 — Observer (Default at Launch)

Artha **reads everything, writes nothing**. It observes all connected data sources, builds its world model, generates briefings and summaries, and makes recommendations. All of this happens without taking any action in the world.

**What Artha does at Level 0:**
- Delivers morning briefings and weekly summaries
- Answers on-demand queries
- Fires ambient alerts
- Tracks goal progress
- Queues recommended actions for human review

**What Artha does NOT do at Level 0:**
- Send any email or message on your behalf
- Create calendar events
- Initiate any financial transaction
- File any form or submit any application

**What Artha CAN do at Level 0 (no human gate needed):**
- Generate AI visuals via Gemini Imagen (saved locally — not sent)
- Delegate web research to Gemini CLI (read-only)
- Run script validation via Copilot CLI (read-only)

### Trust Level 1 — Advisor (Earned after 30 days of reliable Level 0)

Artha **proposes specific actions with one-tap approval**. When it detects something that needs action, it drafts the action and presents it for your approval. One tap to execute, one tap to dismiss.

Examples at Level 1:
- "PSE bill of $300.63 due in 3 days. [Pay now] [Dismiss]"
- "Parth has 2 missing assignments. [Open Parent Portal] [Dismiss]"
- "H-4 EAD window opens in 90 days. [Draft attorney email] [Dismiss]"
- "Today is Rahul's birthday. [Send WhatsApp greeting] [Send email greeting] [Dismiss]"
- "Diwali is in 5 days. [Generate greeting card + compose group email] [Dismiss]"
- "SAT registration deadline in 2 weeks. [Add to calendar] [Dismiss]"

**Criteria for Level 1 elevation:**
- 30 days of Level 0 operation
- Zero false positives in critical domain alerts (Immigration, Finance)
- Morning briefing accuracy ≥ 95% (confirmed by user feedback)
- All recommended actions reviewed (even if dismissed)

### Trust Level 2 — Executor (Earned after 60 days of reliable Level 1)

Artha **acts autonomously on pre-approved action types**, with post-hoc notification. The set of pre-approved actions is defined explicitly by the user — never assumed.

Example pre-approved actions at Level 2:
- Auto-add confirmed bill due dates to calendar
- Auto-archive school newsletters after generating digest
- Auto-log Amazon delivery confirmations to shopping tracker
- Auto-generate visual greeting cards for upcoming occasions (saved locally, not sent)

**Criteria for Level 2 elevation:**
- 60 days of Level 1 operation
- All Level 1 proposed actions accepted ≥ 90% for the specific action type
- Explicit user confirmation of which action types are pre-approved
- Revocable at any time with immediate effect

### Autonomy Floor Rules (Cannot Be Overridden)

Regardless of trust level, the following actions always require explicit human confirmation:
- Any financial transaction or payment
- Any communication sent on your behalf (email, message)
- Any immigration-related document submission or application
- Any deletion of data
- Any action affecting another person's data (Archana, Parth, Trisha)

### Elevation & Demotion Process

**Elevation:** Artha tracks elevation criteria in `health-check.md` (see tech spec §12.11). When all criteria for the next level are met, Artha surfaces a recommendation during catch-up: "All Level 1 criteria met over the past 30 days. Recommend elevation to Advisor level." The user approves or defers. Elevation is logged to `audit.md`.

**Demotion:** Trust can be revoked at any time. Automatic demotion triggers:
- Any critical false positive (immigration, finance) → immediate demotion to Level 0
- Action acceptance rate drops below 70% at Level 2 → alert + recommend demotion to Level 1
- User command: "Artha, go back to Level 0" → immediate demotion

Demotion resets the elevation clock — criteria must be re-met from scratch. This ensures trust is genuinely earned, not accumulated through inertia.

### Self-Improvement via Trust

As Artha earns higher trust levels, it gains access to self-improvement capabilities:
- **Level 0:** Corrections and preferences are logged to `memory.md` for future reference
- **Level 1:** Artha can propose routing rule and domain prompt improvements (user approves)
- **Level 2:** Artha can auto-fix minor extraction errors (e.g., update a sender pattern) with post-hoc notification

---

## 11. Data Sources & Integrations

Artha uses a **pull-based data source strategy** — all sources are fetched in batch during each catch-up run:
1. **MCP tool connectors** for email and calendar (Gmail MCP, Google Calendar MCP)
2. **LLM-based email parsing** for all other sources (bills, school notifications, financial alerts arrive via email)
3. **Manual input** for edge cases and initial bootstrapping
4. **Microsoft Graph API** for task management (Microsoft To Do sync — Phase 1B)

### Email Coverage — Hub-and-Spoke Model

Gmail is the single Artha integration point for all email accounts. All other accounts forward to Gmail; Artha does not need separate OAuth flows per account. Gmail filters apply labels (`from-outlook`, `from-apple`, etc.) to preserve source identity for routing.

| Account | Integration Method | Status | Primary Domains | Gmail Label |
|---|---|---|---|---|
| Gmail (mi.vedprakash@gmail.com) | Direct — Gmail MCP (OAuth) | ✅ Active | All | — |
| Outlook.com (vedprakash.m@outlook.com) | Auto-forward → Gmail (T-1B.1.1) | Phase 1B | Immigration, Finance, Comms | `from-outlook` |
| Apple iCloud (icloud.com) | Auto-forward → Gmail (T-1B.1.2) | Phase 1B | Finance, Digital Life | `from-apple` |
| Yahoo | Auto-forward → Gmail (T-1B.1.3, if active) | Phase 1B — evaluate | Finance, Comms | `from-yahoo` |
| Proton Mail | Proton Bridge → IMAP → Gmail (T-1B.1.4) OR excluded | Phase 2 / excluded | Personal (boundary) | `from-proton` |

**Email coverage gap acknowledgment:** Until Outlook and Apple forwarding are configured (Phase 1B), Fragomen/Microsoft HR immigration emails and Apple receipts may not reach Artha. The `/health` command surfaces the `email_coverage` matrix so gaps are visible, not silent.

### All Data Sources

| Source | Access Method | Data Available | Fetch Pattern |
|---|---|---|---|
| Gmail (primary) | Gmail MCP (OAuth) | All email — read only | Batch pull on catch-up |
| Outlook.com | Forward to Gmail (T-1B.1.1) | All email — read only | Via Gmail batch |
| Apple iCloud | Forward to Gmail (T-1B.1.2) | App Store receipts, Apple account alerts | Via Gmail batch |
| Yahoo | Forward to Gmail (T-1B.1.3, if active) | Legacy email (if active) | Via Gmail batch |
| Proton Mail | Excluded (personal comms boundary) OR Proton Bridge (Phase 2) | Personal comms (by design, excluded) | Phase 2 |
| Google Calendar | Google Calendar MCP | All events — read only | Batch pull on catch-up |
| Microsoft To Do | Microsoft Graph API (T-1B.6.x) | Task lists (read + write) | Sync after catch-up |
| **Microsoft Work Calendar** *(v4.1)* | **WorkIQ MCP** (`@microsoft/workiq` — pinned version) | Work meetings: title, time, duration, organizer, Teams link. Calendar only — no email/chat. | Batch pull on catch-up (Windows only) |
| Home Assistant | Local API (LAN only) | Device status, energy | Pull on catch-up (if Mac on LAN) |
| Fidelity | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Balance, transaction alerts | Via email batch |
| Chase | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Balance, transaction alerts | Via email batch |
| Wells Fargo | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Mortgage, FICO score | Via email batch |
| Vanguard | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Statement alerts, balances | Via email batch |
| PSE Energy | Email parsing (LLM-based) | Bill amount, due date | Via email batch |
| Sammamish Water | Email parsing (LLM-based) | Bill amount, due date | Via email batch |
| USCIS / Fragomen | Email parsing (LLM-based) + **USCIS Status Skill** | Case status updates | Via email batch + **direct HTTP lookup** |
| ParentSquare | Email parsing (LLM-based) | School notifications | Via email batch |
| Canvas (Instructure) | Email parsing (LLM-based) → **Phase 2 (Blocked)**: Canvas REST API | Grade, attendance, assignment details | Via email batch → **API pull** |
| USPS Informed Delivery | Email parsing (LLM-based) | Physical mail preview | Via email batch |
| Marriott Bonvoy | Email parsing (LLM-based) | Points balance, bookings | Via email batch |
| Alaska Airlines | Email parsing (LLM-based) | Booking confirmations, miles | Via email batch |
| Equifax | Email parsing (LLM-based) | Credit monitoring signals | Via email batch |
| USCIS Visa Bulletin | Gemini CLI web search (monthly) | Priority date movements | On catch-up (monthly check) |
| Zillow/Redfin | Gemini CLI web search (quarterly) | Comparable sales, home value | On catch-up (quarterly check) |
| Auto Insurance Carrier | Email parsing (LLM-based) | Policy renewal, premium, claims | Via email batch |
| Homeowners Insurance Carrier | Email parsing (LLM-based) | Policy renewal, premium, claims | Via email batch |
| WA DOL (Vehicle Registration) | Email parsing (LLM-based) | Registration renewal notices | Via email batch |
| NHTSA Recall Database | Gemini CLI web search (monthly per VIN) | Active vehicle recalls | On catch-up (monthly check) |
| ISP / Telecom Provider | Email parsing (LLM-based) | Bill amount, service changes | Via email batch |
| Republic Services (Waste) | Email parsing (LLM-based) | Pickup schedule, billing | Via email batch |
| King County Assessor | **King County Tax Skill** | Property tax assessment, due dates | **Direct HTTP lookup (Phase 1)** |
| Microsoft Benefits Portal | Manual input (annual open enrollment) | Benefits elections, coverage details | Manual |
| Apple Health (HealthKit) | HealthKit XML export (manual or Shortcuts-automated) | Steps, sleep, heart rate, workouts, weight | Import on catch-up (weekly cadence) |

### 11.x Data Fidelity Skills *(v4.0)*

To enhance data fidelity beyond email parsing, Artha uses targeted **"Skills"** — small, lightweight lookups that query institutional portals or official APIs directly.

**1. Compliance & Stability Philosophy**
- **Public Data Only:** Scrapers are permitted ONLY for public, non-authenticated portals (e.g., USCIS, King County Tax, NOAA).
- **Authorized APIs Only:** Authenticated access is restricted to documented, provider-supported APIs (e.g., Canvas LMS, OFX, MS Graph, AirNow).
- **No Reverse Engineering:** Unofficial clients or unauthorized scraping of private portals is strictly forbidden to prevent account bans or legal risk.

**2. Fail-Safe Logic**
- **P0 (Immigration):** 
    - **Logical Error/Parse Error:** Halts catch-up (data integrity unknown).
    - **Status Change:** Alerts user immediately (P0); catch-up continues to ensure briefing delivery.
    - **Transient Error (503/timeout):** Warns and continues.
- **P1/P2 (Finance/Tax/Safety):** Skill failures log a warning and continue the catch-up flow.

**3. Intelligence Foundation**
- **Change Detection:** Skills track their own previous state in `state/skills_cache.json`. Alerts only fire when a meaningful field (e.g., USCIS status) changes.
- **Execution Cadence:** Skills support per-run, daily, or weekly cadences to minimize network traffic and rate-limit risk.

**4. Roadmap**
- **Phase 1.1 (Infra):** Centralized state, dynamic loader, and cadence control.
- **Phase 1.2 (Immigration):** USCIS Visa Bulletin parser (EB-2 India, Table A & B, Authorized Chart).
- **Phase 1.3 (Safety/Property):** NHTSA Recall checks (Kia/Mazda) and King County Assessed Value extension.
- **Phase 1.4 (Concierge):** NOAA Weather unblocking outdoor Open Items.
- **Phase 2.0 (Credentialed):** OFX Bank direct download (Chase) and AirNow AQI (EPA).

**All sources are pull-based.** There are no push notifications, no webhooks, no event-driven triggers. Every data source is queried in batch during each catch-up session. Because all non-API sources arrive via email, Gmail MCP is the single integration point for ~80% of data sources.

**Microsoft To Do integration:** `todo_sync.py` pushes action items extracted by Artha (stored in `open_items.md`) to domain-tagged Microsoft To Do lists. Users manage and complete tasks on iPhone; completion status is pulled back to `open_items.md` on the next catch-up. Microsoft Graph API covers both To Do and Outlook with a single OAuth flow. See tech spec §11.4.

**Parsing strategy:** All email parsing uses LLM-based extraction (send email body to Claude for structured output) rather than regex/template-based parsing. Claude naturally handles format variations from ParentSquare, financial institutions, and other sources.

**Phase 2 data upgrade path:** Financial institutions (Chase, Fidelity, Vanguard, Wells Fargo) will upgrade from email parsing to Plaid API integration (read-only) in Phase 2. This provides real-time balance and transaction data, enabling the "net worth on demand" target.

**Not in scope (by design):**
- Microsoft work email (vedprakash.m@microsoft.com) — Vega's domain
- Teams, ADO, SharePoint, GitHub — Vega's domain
- WhatsApp inbound messages — no API access; outbound messaging only via URL scheme (human-gated, tech spec §7.4.4)
- iMessage — privacy boundary; no API access
- Proton Mail (unless Proton Bridge configured in Phase 2) — personal comms boundary; E2E encryption prevents standard forwarding

---

## 12. Privacy Model

Artha handles deeply personal data across all domains of your life. Privacy is not a feature — it is a foundational constraint.

### 12.1 — Data Residency

All Artha data lives in a OneDrive-synced folder (`~/OneDrive/Artha/`) accessible from Mac, iPhone, and Windows. State files (Markdown), briefing archives, and all parsed data are stored there. Sensitive state files (high/critical sensitivity) are `age`-encrypted before sync — OneDrive stores opaque `.age` files. Encryption keys are stored in device-local credential stores (macOS Keychain, Windows Credential Manager) and never synced to OneDrive. Mac is the sole writer; iPhone and Windows are read-only consumers. A device-local PII filter (`pii_guard.sh`) validates all extracted data before it is written to state files, replacing detected SSN, credit card, bank routing/account numbers, passport numbers, A-numbers, ITIN, and driver's license patterns with safe `[PII-FILTERED-*]` tokens. An outbound PII wrapper (`safe_cli.sh`) strips PII tokens before delegating queries to external CLIs (Gemini, Copilot). The only external data flow beyond OneDrive sync is to the Claude API for processing (ephemeral — Anthropic does not retain API inputs/outputs for training). Backup via OneDrive versioning (primary) and Time Machine (secondary).

### 12.2 — Data Minimization

Artha reads email subjects, senders, and key extracted entities (amounts, dates, names, case numbers). It does not read or store full email body text unless specifically needed for a feature (e.g., immigration case status extraction). Full email content, once parsed, is not retained. Enforcement: `pii_guard.sh` pre-persist filter (Layer 1 — device-local regex, validates extracted data before state file writes) + Claude redaction rules (Layer 2 — LLM-based semantic, prevents PII from persisting in state files) + `safe_cli.sh` outbound wrapper (Layer 3 — strips PII before delegating to Gemini/Copilot CLIs). Together they form defense-in-depth. See tech spec §8.6–8.7.

### 12.3 — Family Data Governance

Artha handles data about Archana, Parth, and Trisha. The governing principle: Artha tracks events and statuses that affect the family's well-being (school grades, immigration status, health appointments). It does not monitor personal communications, social activity, or private exchanges.

Specifically:
- ✅ Parth's grade alerts, SAT dates, club activities — tracked
- ✅ Archana's immigration status, appointment calendar — tracked
- ❌ Parth's personal messages, friend communications — not tracked
- ❌ Archana's personal email content — not tracked (only bill/immigration/scheduling signals)

### 12.4 — Immigration Data Special Handling

Immigration data is the most sensitive category in Artha. Special handling rules:
- Immigration documents (passport numbers, A-numbers, receipt numbers) are stored encrypted in local state
- Case numbers and document expiry dates are indexed; full document content is not
- Attorney correspondence is parsed for status updates only; legal advice content is not stored

### 12.5 — Audit Rights

The user can at any time:
- View everything Artha has stored (full state dump)
- Delete any data category
- Revoke any data source connection
- Export the full Artha state in portable format

### 12.6 — Privacy Surface Acknowledgment *(v3.8 — Workstream J)*

Artha's primary runtime is Claude Code, which sends all context (state files, email content, conversation) to the Anthropic API for processing. This is an inherent privacy surface that cannot be eliminated without abandoning the architecture.

**What is acknowledged:**
- All data processed during a catch-up session (emails, calendar events, state file contents) is sent to Anthropic's API
- Anthropic's API data retention policy (as of March 2026): inputs and outputs are **not** retained for model training; ephemeral processing only
- The `pii_guard.sh` filter and `safe_cli.sh` wrapper protect against PII in *persisted* state files and *outbound* CLI calls — they do **not** prevent Claude from seeing raw email content via MCP
- The Gemini CLI and Copilot CLI also receive data for their respective tasks (web search queries, validation inputs) — outbound PII wrapper (`safe_cli.sh`) strips sensitive tokens before delegation

**Mitigation posture:**
- Defense-in-depth: three independent PII layers (device regex → LLM redaction → outbound wrapper)
- Minimize data exposure: email pre-processing (Workstream E) reduces what Claude sees; tiered context loading (Workstream F) reduces what state files are loaded
- Audit trail: all data flows logged in `audit.md`
- User control: data source connections are revocable at any time (§12.5)

**Documentation requirement:** Artha.md must include a `§4.3 Privacy Surface` section that states this acknowledgment in plain language. This ensures the privacy posture is visible in the primary instruction file, not buried in spec documents.

### 12.7 WorkIQ Privacy Rules *(v4.1)*

WorkIQ introduces Microsoft corporate calendar data into Artha's processing pipeline. Special privacy rules apply:

| Data type | Enters Claude API? | Persisted to state? | Mitigation |
|-----------|-------------------|-------------------|-----------|
| Work meeting titles | ✅ After **partial** local redaction | ❌ NO — ephemeral | Sensitive codenames substring-replaced per `config/settings.md` redaction list before API transit. Meeting type preserved. |
| Work meeting attendee names | ✅ During conflict detection | ❌ NO | Not persisted anywhere. |
| Meeting bodies/agendas | ❌ NOT requested | ❌ NO | WorkIQ query requests titles + times only. |
| Teams chat / work email | ❌ NOT requested | ❌ NO | Explicitly out of scope. |
| Meeting count + duration | ✅ Yes | ✅ YES — `work-calendar.md` | Safe metadata; powers density analysis. 13-week rolling window. |
| Raw WorkIQ response | N/A | ❌ `tmp/work_calendar.json` deleted at Step 18 | Ephemeral; explicitly cleaned up before vault encrypt. |

**Platform constraint:** WorkIQ data is available on Windows work laptop only (M365 Copilot enterprise license). Mac catch-ups produce identical personal-only briefings with a one-line footer: "ℹ️ Work calendar: available on Windows laptop only."

**Corporate compliance:** Ved has confirmed Microsoft IT/compliance approval for routing WorkIQ calendar metadata through Claude API (Anthropic) with local partial redaction and ephemeral-only processing. No corporate email or chat content enters the pipeline.

---

## 13. Phased Roadmap

### Phase 1 — Foundation (Months 1–2)
*Objective: Deliver immediate daily value in the highest-friction domains with zero custom code*

> **v3.0 Note:** Phase 1 is dramatically simplified compared to v2.2. No daemon infrastructure, no SQLite, no event sourcing, no pre-processor pipeline. Just: Artha.md + domain prompts + MCP tools + local Markdown state files. The entire "infrastructure" is a well-written instruction file.

#### Phase 1A — Core Setup (Weeks 1–2)
- **Artha.md authoring:** Primary instruction file defining Artha's identity, catch-up workflow, domain routing rules, and multi-LLM routing directives. Thin CLAUDE.md loader (3 lines) delegates to Artha.md for clean project separation.
- **Gmail MCP setup:** OAuth credentials, `gmail.readonly` + `gmail.send` scope, connect to Claude Code. **Budget 3–5 hours for OAuth validation and MCP troubleshooting** — MCP connectors are community-maintained and may have version-specific issues.
- **Google Calendar MCP setup:** OAuth credentials, read + write scope (event creation)
- **Directory structure:** Create `~/OneDrive/Artha/` with `prompts/`, `state/`, `briefings/`, `summaries/`, `config/`, `scripts/`, `visuals/` directories
- **`age` encryption setup:** Install `age`, generate keypair, store key in macOS Keychain, create `vault.sh`
- **vault.sh crash recovery:** Configure OneDrive selective sync to exclude `state/` during active sessions. Add LaunchAgent watchdog that checks for `.artha-decrypted` lock file and triggers re-encrypt if stale >30 minutes. See tech spec §8.5.
- **`pii_guard.sh` creation:** Pre-persist PII filter per tech spec §8.6 — regex-based scanner for SSN, CC, routing/account numbers, passport, A-number, ITIN, DL. Validates extracted data before state writes (not MCP interceptor).
- **`safe_cli.sh` creation:** Outbound PII wrapper for Gemini/Copilot CLI calls per tech spec §8.7 — strips PII tokens before external delegation.
- **PII allowlists in domain prompts:** Define allowlist sections (e.g., USCIS receipt numbers, Amazon order numbers) per domain
- **Claude Code hooks setup:** PreToolUse hook for auto-decrypt, Stop hook for auto-encrypt
- **Custom slash commands in Artha.md:** Define `/catch-up`, `/status`, `/goals`, `/domain [name]`, `/cost`, `/health`
- **Multi-LLM setup:** Verify Gemini CLI and Copilot CLI are operational, add routing rules to Artha.md, test web search and validation queries
- **OneDrive sync verification:** Confirm state files sync to iPhone and Windows within minutes
- **Initial state files:** Bootstrap `immigration.md`, `finance.md`, `kids.md` with current known state (manual input)
- **Config files:** Create `contacts.md` (contact groups for messaging — encrypted, see tech spec §8.5) and `occasions.md` (festival/occasion calendar)
- **Email delivery:** Configure SMTP sending from Claude Code for briefing delivery
- **Archana email access:** Resolve whether Archana's Outlook.com emails can be forwarded to Ved's Gmail or require separate OAuth. See tech spec TD-18.
- **Action framework setup:** Test WhatsApp URL scheme on Mac, test Gmail MCP send capability, test calendar event creation
- **Visual generation test:** Generate a test image via Gemini Imagen, verify output to `~/OneDrive/Artha/visuals/`
- **First catch-up:** Run "catch me up" end-to-end, validate output including PII filter + encrypt/decrypt + multi-LLM routing, iterate on Artha.md
- **Component registry:** Create `registry.md` per tech spec §12.1 — manifest of all deployed components (including CLI tools and action channels)
- **Artha.md versioning:** Add version field and changelog to Artha.md per tech spec §12.2
- **Governance baseline:** Document initial routing rules, encryption decisions, and PII patterns in audit.md
- **Data Fidelity Skills (Phase 1):** Implement `scripts/skill_runner.py` and initial public skills for USCIS Status and King County Property Tax. Ensure zero ToS risk and fail-safe logic integration.

#### Phase 1B — High-Value Domains (Weeks 3–5)
- **Communications prompt (FR-1):** School digest consolidator (F1.1), action item extractor (F1.2)
- **Immigration prompt (FR-2):** Family dashboard (F2.1), deadline alerts (F2.2), case timeline tracker (F2.3), Visa Bulletin monitor via Gemini CLI web search (F2.6), **dependent age-out sentinel with CSPA tracking (F2.7)**
- **Kids prompt (FR-4):** Daily school brief per child (F4.1), grade/assignment alerts (F4.2)
- **Outlook forwarding:** Set up auto-forward from Outlook.com to Gmail for unified email ingestion
- **Ensemble reasoning test:** Run first ensemble query (all 3 LLMs) on a real immigration question, evaluate quality improvement
- **Contact population:** Populate `contacts.md` with family, friends, and colleague groups
- **End-to-end visual greeting test:** Generate festival card + compose email + send to test recipient

#### Phase 1C — Goal Engine + Finance (Weeks 6–8)
- **Goal Engine prompt (FR-13):** Conversational goal creation (F13.1), automatic metric wiring for first 5 goals, weekly scorecard, goal conflict detection (F13.9)
- **Finance prompt (FR-3):** Bill calendar (F3.1), unusual spend alert (F3.3), predictive spend forecasting (F3.9)
- **Conversation Memory:** Store preferences, corrections, decisions in `~/OneDrive/Artha/state/memory.md`
- **Claude.ai Project setup:** Create "Artha" project on Claude iOS app, use OneDrive state files for always-fresh mobile queries

**Phase 1 initial goals (resolved from OQ-3):**
1. Net worth / savings trajectory
2. Immigration readiness (all documents current, deadlines known)
3. Parth academic trajectory (GPA target)
4. Protected family time (≥ X hours/week)
5. Learning consistency (hours/month target)

**Phase 1 success criteria:**
- Catch-up briefing delivered on each run with ≥95% accuracy (user-rated)
- School email noise reduced by ≥ 70%
- All four family immigration documents tracked with correct expiry dates
- ≥ 5 active goals with automatic metric collection
- Average catch-up run time <3 minutes
- Monthly Claude API cost <$50 (validated)
- Zero missed Critical alerts in Immigration or Finance domains
- **Intelligent Alerting:** Briefings must prioritize semantic deltas. If a data skill (USCIS, Tax) returns the same status as the previous run, the alert marker (🔴/🟠) must be suppressed to minimize user fatigue.
- Zero custom code deployed (all logic in Artha.md + domain prompts)

---

### Phase 2A — Intelligence Deepening *(v3.8, expanded v3.9)*
*Objective: Deepen intelligence with relationship awareness, leading indicators, decision tracking, operational improvements, and supercharge enhancements — all spec-driven, no new infrastructure*

**Build (10 original workstreams + 18 supercharge items):**

**A: Relationship Intelligence (FR-11 elevation)**
- Social domain prompt authoring (F11.1–F11.10): relationship graph model, communication pattern analysis, reconnect radar with configurable thresholds, cultural protocol intelligence, life event awareness, group dynamics
- Expand `state/social.md` schema for relationship graph storage
- Populate `contacts.md` with relationship tiers, cultural protocol metadata, preferred channels

**B: Goal Engine Leading Indicators (§8.11)**
- Add `leading_indicators` extraction block to all active domain prompts
- Goal scorecard shows leading + lagging side by side
- Weekly summary highlights leading indicator divergence as early warnings

**C: Decision Graphs (F15.24)**
- Create `state/decisions.md` schema (decision ID, date, context, alternatives, affected domains, outcome)
- Auto-generate entries during cross-domain reasoning (§9.4 step 10)
- `/decisions` slash command for querying decision history

**D: Life Scenarios (F15.25)**
- Create `state/scenarios.md` schema (scenario ID, trigger, template, projected impacts per domain)
- Auto-suggest when major decision points detected
- Template library for common scenarios (refinance, college choice, immigration status change)

**E: Email Pre-Processing Enhancement (§9.4 step 5)**
- Marketing/newsletter sender allowlist with subject-line-only extraction for unrecognized senders
- Per-email token budget enforcement (1,500 token cap)
- Batch summarization logic for >50 email batches

**F: Tiered Context Architecture (§9.8)**
- Add `last_activity` timestamp to all state file YAML frontmatter
- Implement Always/Active/Reference/Archive tier classification in catch-up workflow (step 5b)
- Validate 30–40% token savings on real catch-ups

**G: ONE THING Reasoning Chain (§9.4 step 8)**
- URGENCY × IMPACT × AGENCY scoring protocol in Artha.md
- Scoring rubric documented in social prompt template for reuse across domains
- Briefing synthesis step selects highest-scoring item as "ONE THING"

**H: Digest Mode (F15.26)**
- >48hr gap detection in step 2b of catch-up workflow
- Priority-tier grouping (Critical → Warning → Notable → FYI) briefing variant
- "What You Missed" header with day-by-day summary
- Action item consolidation across the gap period

**I: Accuracy Pulse (F15.27)**
- Track in `health-check.md`: proposed/accepted/declined/deferred action counts per catch-up
- Track in `memory.md`: corrections logged by domain
- Weekly summary section: Accuracy Pulse with trends
- Per-domain accuracy tracking enhancement in `/status` output

**J: Privacy Surface Acknowledgment (§12.6)**
- Add `§4.3 Privacy Surface` section to Artha.md
- Document Claude API privacy surface, Gemini/Copilot data flows, mitigation posture

**#10: Action Friction Field**
- Add `friction: low|standard|high` to action proposal schema in Artha.md
- Friction classification rules in action catalog
- Batch approval enabled for low-friction actions; individual review for high-friction

**K: Data Integrity Guard (F15.28) — P0**
- vault.sh pre-decrypt backup (.md.bak before overwrite)
- Write verification step after state file writes
- Net-negative write guard (block writes that would reduce data)
- Audit logging for all write guard interventions

**L: Life Dashboard Snapshot (F15.29)**
- Create `state/dashboard.md` schema (domain rows + family member rows)
- Auto-generate at end of each catch-up (step 15b)
- Add to always-load tier for session quick-start

**M: Compound Signal Detection (F15.30)**
- Cross-domain convergence check during step 10
- Compound scoring (higher than individual signals)
- Surface in briefing with compound alert formatting

**N: Proactive Calendar Intelligence (F15.31)**
- Logistics analysis (travel time, kid conflicts, weather)
- Preparation detection (advance action items)
- Energy/load balancing (dense week detection)

**O: Goal Engine → Coaching Engine (F13.14–F13.16)**
- Implementation planning with contextual next actions
- Obstacle anticipation from patterns + calendar + seasons
- Accountability pattern learning (which nudge styles work)

**P: Bootstrap Command (F15.33) — P0**
- `/bootstrap` slash command with structured interview per domain
- Scan for `updated_by: bootstrap` and TODO-heavy state files
- Direct writes to state files with `updated_by: user_bootstrap`

**Q: Pattern of Life Detection (F15.34)**
- 30-day behavioral baselines (spend, communication, calendar, goals)
- Store in `memory.md` under `## Behavioral Baselines`
- Feed into coaching engine, compound signals, ONE THING

**R: Operational Resilience Suite (F15.35–F15.43)**
- Signal:Noise ratio tracking per domain (F15.35)
- Stale state detection with auto-heal (F15.36)
- Consequence forecasting at 7/30/90 days (F15.37)
- Pre-decision intelligence packets (F15.38)
- Session quick-start with intent detection (F15.39)
- Briefing compression levels — full/standard/flash (F15.40)
- Context window pressure management (F15.41)
- OAuth token resilience framework (F15.42)
- Email volume scaling tiers (F15.43)

**S: Life Scorecard (F15.44)**
- Quarterly scorecard generation with all sections
- Annual comprehensive assessment with YoY comparison
- Save to `summaries/scorecard-YYYY-QN.md`

**T: Briefing Intelligence Amplification *(v4.0)***
- Week Ahead Preview in Monday briefings (Mode 1 enhancement)
- Post-briefing calibration questions for accuracy tuning (F15.46)
- PII detection stats footer on every briefing (F15.47)
- Monthly retrospective auto-generation (F15.50)
- Quarterly privacy audit report (F15.49)

**U: Scheduling & Task Intelligence *(v4.0)***
- Calendar-aware task scheduling with suggested open slots on open items (F8.6)
- Weekend Planner — Friday afternoon optimization of family weekend (F8.7)
- Effort estimates + Power Half Hour micro-task batches (F15.48)
- “If You Have 5 Minutes” opportunistic micro-tasks (F15.53)

**V: Goal Engine Expansion *(v4.0)***
- Goal Sprint with real targets — mandatory `target_value`, `default_target` calibration per goal type (F13.17)
- Goal Auto-Detection — infer implicit goals from email/calendar patterns (F13.18)
- Decision Deadlines enhancement on decisions.md entries (F15.24 v4.0)

**W: Conversational Intelligence *(v4.0)***
- `/diff` command showing state changes since last catch-up (F15.51)
- “Ask Archana” delegation routing with context packets (F15.52)
- “Teach Me” mode — explain domain concepts from state data (F15.54)
- Natural language state queries — conversational access to any state file (F15.55)

**X: Family & Cultural Intelligence *(v4.0)***
- India time zone scheduling alerts for family calls (F11.11)
- Parth college application countdown dashboard (F4.11, P0 — urgent, application year is 2026–2027)

**Phase 2A success criteria:**
- FR-11 relationship intelligence operational with ≥20 tracked relationships
- Leading indicators extracted for ≥5 active goals
- Decision graph populated with ≥10 cross-domain decisions after 30 days
- Digest mode triggers correctly on >48hr gaps
- Accuracy Pulse appears in weekly summary with meaningful data after 4 weeks
- Email pre-processing reduces average per-email token consumption by ≥40%
- Tiered context loading reduces state file token consumption by ≥30%
- ONE THING consistently appears at the top of briefings with demonstrable relevance
- Privacy Surface section present in Artha.md
- Action friction field operational with correct classification
- Data integrity guard prevents zero data-loss incidents (F15.28)
- Bootstrap command successfully populates ≥3 previously empty state files (F15.33)
- Life dashboard snapshot refreshed on every catch-up (F15.29)
- Compound signals detected and surfaced when 2+ domains converge (F15.30)
- Coaching engine generates implementation plans for ≥5 goals (F13.14)
- Pattern of life baselines established after 30 days of data (F15.34)
- Signal:noise ratio tracked per domain, noise domains identified (F15.35)
- Session quick-start reduces non-catch-up response time to <10s (F15.39)
- Briefing compression modes all functional (F15.40)
- Context window pressure stays below orange on routine catch-ups (F15.41)
- OAuth tokens proactively refreshed with zero surprise expirations (F15.42)
- First quarterly life scorecard generated (F15.44)
- Calibration questions appear post-briefing; user accuracy feedback improves over 30 days (F15.46) *(v4.0)*
- PII detection footer present on 100% of briefings (F15.47) *(v4.0)*
- Week Ahead Preview appears on every Monday briefing (Mode 1) *(v4.0)*
- Calendar-aware scheduling suggests ≥75% viable time slots (F8.6) *(v4.0)*
- Effort estimates populated on ≥80% of open items (F15.48) *(v4.0)*
- Decision deadlines set on ≥80% of pending decisions within 7 days (F15.24) *(v4.0)*
- Goal Sprint targets calibrated for all active goals (F13.17) *(v4.0)*
- At least 2 goals auto-detected from patterns after 30 days (F13.18) *(v4.0)*
- `/diff` command operational with meaningful deltas (F15.51) *(v4.0)*
- First monthly retrospective generated (F15.50) *(v4.0)*
- Parth college countdown dashboard active with sub-milestones (F4.11) *(v4.0)*

---

### Phase 2B — Domain Expansion (Months 3–5)
*Objective: Expand domain coverage, deepen intelligence, add helper scripts only where Claude proves insufficient*

**Build:**
- Travel prompt (FR-5): Trip dashboard + travel document checker
- Health prompt (FR-6): Family appointment calendar + HSA tracker + open enrollment support (F6.7, F6.8)
- Home prompt (FR-7): Utility bill calendar + mortgage tracker + telecom tracker (F7.8) + property tax tracker (F7.12) + emergency preparedness (F7.13)
- Calendar prompt (FR-8): Unified calendar + conflict detector
- Learning prompt (FR-10): Newsletter digest + course progress tracker
- Boundary prompt (FR-14): After-hours work signal + personal time protection
- **Insurance prompt (FR-16):** Policy registry (F16.1), premium tracker (F16.2), renewal calendar (F16.3), coverage adequacy review (F16.4), teen driver prep (F16.6), Microsoft benefits optimizer (F16.8)
- **Vehicle prompt (FR-17):** Vehicle registry (F17.1), registration renewal tracker (F17.2), maintenance schedule (F17.3), service history (F17.4), warranty tracker (F17.5), teen driver program for Parth (F17.7)
- **Finance prompt expansion:** Tax preparation manager (F3.10), insurance premium aggregator (F3.11), **credit card benefit optimizer (F3.12)**, **tax season automation workflow (F3.13) *(v4.0)***
- **Vehicle prompt expansion:** **Lease & lifecycle manager (F17.9)**
- **Kids prompt expansion:** Paid enrichment tracker (F4.8), activity cost summary (F4.9), **Canvas LMS direct API integration for grades/assignments/analytics (F4.10) *(v4.0)***
- **Health prompt expansion *(v4.0)*:** Apple Health/HealthKit integration — parse XML export for steps, sleep, heart rate, workouts, weight; wire to wellness goals (F6.9)
- **Digital prompt expansion *(v4.0)*:** Subscription ROI tracker — cost vs. usage frequency analysis with cancel/keep recommendations (F12.6)
- **Goal Engine expansion:** Goal cascade view (F13.5), recommendation engine (F13.7), trajectory forecasting (F13.10), behavioral nudge engine (F13.11), dynamic replanning (F13.12)
- **Insight Engine (F15.11):** Extended thinking for weekly deep reasoning across all domain state
- **Proactive Check-in (Mode 6):** Integrated into catch-up flow when data warrants
- *~~Plaid integration~~* — **Deferred.** Direct financial data API integration (FDX/Section 1033 or Plaid) deferred beyond Phase 3. Email-based parsing continues for financial institutions.
- **Family access model:** Tiered access for Archana (shared domains), Parth (academic view), Trisha (age-appropriate view) — via separate Claude.ai Projects with filtered state
- **State volume check:** If state files exceed 150K tokens total, introduce SQLite for historical data

**Phase 2B success criteria:**
- All 17 domains have at least basic prompt coverage
- Weekly summary is comprehensive and actionable
- Goal Engine tracks ≥ 10 active goals with auto-metric collection, conflict detection, and trajectory forecasting
- ~~Financial data sourced via Plaid API~~ **Deferred** — email-based parsing continues; direct financial API evaluated post-Phase 3
- Archana has active access to shared family domains via Claude.ai Project
- All insurance policies registered with renewal alerts active
- All family vehicles registered with maintenance schedules active
- Helper scripts deployed only where Claude proved unreliable (target: ≤ 3 scripts total)
- Canvas LMS API operational with direct grade/assignment pull for both kids (F4.10) *(v4.0)*
- Apple Health data imported and wired to ≥1 wellness goal (F6.9) *(v4.0)*
- Subscription ROI report generated with cost vs. usage analysis (F12.6) *(v4.0)*
- Tax season workflow activated with automated document checklist (F3.13) *(v4.0)*

---

### Phase 3 — Autonomy & Prediction (Months 6–9)
*Objective: Elevate predictive intelligence, voice access, and begin earning execution autonomy*

**Build:**
- Digital prompt (FR-12): Subscription audit + account security monitor
- Shopping prompt (FR-9): Monthly spend summary + return window tracker
- **Estate prompt (FR-18):** Estate document registry (F18.1), beneficiary audit (F18.2), document review cycle (F18.3), life event legal triggers (F18.4), emergency access guide (F18.5), guardianship planning (F18.7), **emergency contact wallet card generator (F18.8) *(v4.0)***
- **WhatsApp Business Bridge (F1.7) *(v4.0)*:** WhatsApp Business API or web bridge for message context ingestion — school groups, family groups, activity coordination. Human-gated send via URL scheme.
- **Home prompt expansion:** Waste & recycling (F7.9), HOA/community dues (F7.10), lawn & landscaping schedule (F7.11)
- **Insurance prompt expansion:** Life event coverage triggers (F16.5), claims history log (F16.7)
- **Vehicle prompt expansion:** Fuel/charging cost tracker (F17.6), recall monitor (F17.8), **TCO calculator (F17.10)**
- Goal Engine: Annual retrospective (F13.8), seasonal pattern awareness (F13.13), full cascade view
- **Predictive Calendar (F15.15):** Model recurring events, proactive predictions with confidence levels
- **Voice interface:** Apple Shortcuts + Whisper/Claude pipeline for voice queries
- Autonomy Layer: Pre-approved action categories execute with post-hoc notification
- Artha Memory: Longitudinal pattern recognition across all domains
- **State scaling:** SQLite for historical data, RAG for conversation memory/briefing archives if needed

**Phase 3 success criteria:**
- All 18 FRs have full prompt coverage
- Goal Engine tracks full family goal hierarchy with seasonal awareness
- Pre-approved actions operational for at least 3 action types
- First annual goal retrospective completed
- Artha surfaces at least one non-obvious insight per week from pattern recognition
- Voice queries functional for common questions
- Predictive calendar generating predictions with ≥ 70% accuracy
- Estate planning documents inventoried and review cycle active
- Emergency preparedness checklist complete and annual review scheduled
- Beneficiary audit completed across all financial + insurance accounts

---

## 14. Success Criteria

Artha succeeds when it materially changes how you navigate your life. The following metrics will be tracked by Artha itself — as its own primary goal:

| Metric | Baseline | Target (6 months) | How Artha measures it |
|---|---|---|---|
| Critical alerts missed | Unknown | Zero | Immigration, Finance, Kids alert audit log |
| School email inbox volume | 90–100/month | ≤ 10/month (digests only) | Gmail email count by sender |
| Catch-up briefing accuracy | 0% (no briefing) | ≥ 95% | User feedback on each briefing |
| Goal tracking coverage | 0 goals tracked | ≥ 10 active goals with auto-metrics | Goal Engine state |
| Net worth visibility | Requires 5+ manual logins | On-demand in catch-up | Finance state file |
| Immigration deadline lead time | Unknown | 100% of deadlines known ≥ 90 days out | Immigration state file |
| Weekly summary utility rating | N/A | ≥ 4/5 average | Weekly user rating |
| Work encroachment on personal time | Unknown baseline | Measured and trending down | Boundary state file |
| Monthly AI cost | N/A | <$50/month | Claude API usage dashboard |
| Catch-up run reliability | N/A | ≥ 95% complete without errors | Health-check state file |
| Average catch-up run time | N/A | <3 minutes | Health-check state file |
| Non-obvious insights surfaced | 0 | ≥ 1/week | Insight Engine weekly output |
| Goal conflicts detected | 0 | All active conflicts surfaced | Goal conflict detection audit |
| Insurance renewal alerts | 0 | 100% of renewals alerted ≥30 days out | Insurance state file |
| Vehicle registration alerts | 0 | 100% of registrations alerted ≥30 days out | Vehicle state file |
| Estate document coverage | Unknown | All critical docs inventoried + review cycle active | Estate state file |
| Household domain coverage | ~13 domains | All 18 FRs with active tracking | Domain prompt count |
| Dependent age-out tracking | Not tracked | CSPA age calculated and monitored for both children | Immigration state file |
| Custom code deployed | N/A | Minimal (target: ≤ 3 helper scripts + vault.sh + pii_guard.sh) | File count in ~/OneDrive/Artha/scripts/ |
| Domain addition time | N/A | <1 hour decision-to-first-catch-up | §12.3 checklist completion time |
| Per-domain accuracy | 0% (no tracking) | ≥90% per domain, ≥95% overall (30-day rolling) | health-check.md accuracy metrics |
| AI feature adoption lag | N/A | <90 days from Claude feature release to evaluation | registry.md review log |
| Multi-LLM cost savings | N/A | ≥$3/month savings via Gemini/Copilot routing | Cost model tracking (tech spec §10.3) |
| Action proposal acceptance rate | N/A | ≥80% of proposed actions approved (remainder modified or deferred) | audit.md action log |
| Visual messages generated | 0 | ≥5 occasions with AI-generated visuals in first 6 months | visuals/ directory count |
| Ensemble reasoning usage | 0 | Used for all high-stakes decisions (immigration, finance >$5K) | audit.md ensemble log |
| Tracked relationships *(v3.8)* | 0 | ≥20 relationships with tier, last_contact, protocol | state/social.md relationship graph |
| Leading indicators per goal *(v3.8)* | 0 | ≥1 leading indicator tracked per active goal | Goal Engine state + domain prompts |
| Decision graph entries *(v3.8)* | 0 | ≥10 cross-domain decisions logged after 30 days | state/decisions.md entry count |
| Digest mode accuracy *(v3.8)* | N/A | Triggers on 100% of >48hr gaps; user rates ≥4/5 | health-check.md gap detection + user rating |
| Accuracy Pulse completeness *(v3.8)* | N/A | Weekly summary includes Accuracy Pulse with all metrics | Weekly summary audit |
| Email token savings *(v3.8)* | ~500 tokens/email avg | ≤300 tokens/email avg (≥40% reduction) | health-check.md pre-processing stats |
| Context tier savings *(v3.8)* | ~36K tokens (all state) | ≤22K tokens (≥30% reduction) | health-check.md tier loading stats |
| ONE THING relevance *(v3.8)* | N/A | User confirms relevance ≥80% of catch-ups | User feedback on briefings |
| Data integrity incidents *(v3.9)* | Unknown | Zero data-loss events | audit.md write guard log |
| Dashboard freshness *(v3.9)* | N/A | Updated on 100% of catch-ups | dashboard.md timestamp vs. health-check.md |
| Compound signals detected *(v3.9)* | 0 | ≥1/week when multi-domain alerts active | Briefing compound alert count |
| Coaching plans generated *(v3.9)* | 0 | Implementation plan for every behind/at-risk goal | Goal state coaching field |
| Bootstrap coverage *(v3.9)* | 9 files at template | ≤2 files at template after first month | State file updated_by audit |
| Pattern baselines established *(v3.9)* | 0 | All 4 baseline types (spend, comms, calendar, goals) after 30 days | memory.md behavioral baselines |
| Signal:noise ratio *(v3.9)* | Unknown | ≥60% per domain (items acted upon / items surfaced) | health-check.md signal_noise |
| Stale domains detected *(v3.9)* | Unknown | Zero undetected stale domains | health-check.md staleness audit |
| Session quick-start latency *(v3.9)* | ~60s | <10s for query/action sessions | Time-to-first-response measurement |
| OAuth surprise expirations *(v3.9)* | Unknown | Zero | health-check.md token health |
| Quarterly life scorecard *(v3.9)* | 0 | Generated every quarter | summaries/scorecard-* file count |
| Calibration question response rate *(v4.0)* | N/A | ≥50% of post-briefing calibration Qs answered | health-check.md calibration log |
| PII footer presence *(v4.0)* | N/A | 100% of briefings include PII detection stats | Briefing content audit |
| Week Ahead accuracy *(v4.0)* | N/A | Monday preview covers ≥90% of week’s actual events | Calendar comparison after each week |
| Calendar-aware scheduling hit rate *(v4.0)* | N/A | ≥75% of suggested time slots are viable | User acceptance of slot suggestions |
| Decision deadline coverage *(v4.0)* | N/A | ≥80% of pending decisions have deadlines within 7 days | decisions.md field audit |
| Goal Sprint target calibration *(v4.0)* | 0 | All active goals have calibrated target_value | Goal Engine state audit |
| Goals auto-detected *(v4.0)* | 0 | ≥2 goals detected from patterns after 30 days | Goal Engine auto-detection log |
| /diff command usage *(v4.0)* | N/A | Operational with meaningful deltas on each invocation | health-check.md command log |
| Monthly retrospective *(v4.0)* | 0 | Generated every month | summaries/retro-YYYY-MM.md file count |
| Privacy audit *(v4.0)* | 0 | Generated every quarter | summaries/privacy-audit-* file count |
| College countdown accuracy *(v4.0)* | N/A | All deadlines tracked with ≥90-day lead time | kids.md college section audit |
| Canvas API data freshness *(v4.0)* | N/A | Grade/assignment data ≤24h stale | health-check.md API pull timestamp |
| Apple Health data integration *(v4.0)* | 0 | Weekly import wired to ≥1 wellness goal | health.md HealthKit data presence |
| WhatsApp context coverage *(v4.0)* | 0 | Key group messages ingested for relevant domains | comms.md WhatsApp source count |
| Subscription ROI report *(v4.0)* | 0 | Quarterly report with cost vs. usage analysis | digital.md subscription audit |
| Effort estimates coverage *(v4.0)* | 0 | ≥80% of open items have effort estimates | open_items.md field audit |
| Work calendar merge accuracy *(v4.1)* | N/A | Personal+work events merged with ≤5% dedup errors | Manual audit of merged briefing output |
| Cross-domain conflict detection *(v4.1)* | 0 | All work↔personal overlaps detected and scored correctly | Catch-up output review against actual calendar |
| Partial redaction coverage *(v4.1)* | N/A | 100% of configured keywords redacted before API transit | audit.md redaction stats |
| Mac graceful degradation *(v4.1)* | N/A | Mac catch-up produces identical personal-only briefing (no errors, no regression) | Mac catch-up test |
| Work calendar fetch reliability *(v4.1)* | N/A | ≥90% success rate on Windows catch-ups (WorkIQ fetch + parse) | health-check.md workiq success rate |
| Meeting-triggered OI accuracy *(v4.1)* | 0 | Critical meetings create future-dated OIs only; no stale OIs in digest mode | open_items.md audit after digest catch-up |

### 14.4 Automated Testing Requirements

To ensure the long-term stability and security of the Artha OS, the following automated testing requirements are mandatory:

**TR-1: Security-Critical Script Validation (P0)**
- All security-critical scripts (`pii_guard.sh`, `vault.py`, `preflight.py`) must have 100% test coverage for their core logic.
- Tests must verify PII detection across all 8+ categories (SSN, CC, ITIN, etc.) and ensure zero leakage.
- Vault tests must verify the "Net-Negative Write Guard" to prevent data loss >20%.

**TR-2: "Golden File" Extraction Regression (P1)**
- Domain extraction prompts must be validated against "Golden File" snapshots.
- A library of mock email inputs (JSONL) and expected state outputs (Markdown) must be maintained.
- Tests must verify that prompt updates do not degrade extraction accuracy for historically handled patterns.

**TR-3: Cross-Platform Consistency (P1)**
- Tests must pass identically on macOS and Windows (or clearly skip OS-specific features with a warning).
- Script health checks (`--health`) must be validated by the testing framework.

---

## 15. Non-Functional Requirements

Artha runs on personal data with no ops team — Vedprakash is the sole operator. NFRs must be self-enforcing.

### 15.1 — Performance

| Metric | Target | Measurement |
|---|---|---|
| On-demand chat (state-file query) | <10 seconds | Query to response time |
| On-demand chat (requires email fetch) | <30 seconds | Query to response time |
| Full catch-up run | <3 minutes | Start to briefing delivery |
| Parallel fetch speedup | Email + calendar fetched simultaneously | Claude Code parallel tool invocation |
| Weekly summary generation | <60 seconds | Within catch-up session |
| Goal scorecard generation | <30 seconds | Within catch-up session |

### 15.2 — Reliability

| Metric | Target | Notes |
|---|---|---|
| Catch-up completion rate | ≥ 95% | Percentage of catch-ups that complete without errors |
| MCP tool connection success | ≥ 99% | Gmail + Calendar OAuth working on each run |
| State file integrity | Zero corruption | Markdown files validated on each write |
| API unavailability handling | Graceful degradation | Note which sources failed, proceed with available data |
| Recovery from partial failure | Resume from last successful step | Health-check file tracks progress |

### 15.3 — Storage & Data

| Metric | Target | Notes |
|---|---|---|
| State file total size (year 1) | <5 MB projected | ~18 Markdown files × ~50 KB each |
| Data retention: parsed email metadata | Indefinite | Needed for seasonal pattern detection |
| Data retention: full email body | Not stored | Parse and discard — privacy by design |
| Data retention: raw document content | Not stored | Extract-and-discard — document stays in repository |
| Sensitive data in state files | Redacted per domain rules | SSN: never stored. Passport, A-number, routing numbers: `[REDACTED]` |
| Briefing email sensitivity filter | High/critical domains: summary only | Prevents sensitive data in transit via email |
| Data retention: briefings/summaries | Indefinite archive | Human-readable Markdown files |
| Backup strategy | OneDrive versioning (primary) + Time Machine (secondary) | OneDrive: 30-day version history. Time Machine: encrypted local. |
| Backup location | OneDrive (synced) + local Mac | Encryption keys in device-local credential stores only |

### 15.4 — Observability

Artha monitors its own health. Accessible via on-demand chat ("Artha, are you healthy?") or by reading `~/OneDrive/Artha/state/health-check.md`:

| Signal | Method | Alert threshold |
|---|---|---|
| Last catch-up timestamp | Timestamp in health-check.md | >48 hours since last run |
| State file freshness per domain | Last-updated timestamp in each state file | >7 days stale for active domain |
| MCP tool connection status | Tested on each catch-up run | Any tool failing to connect |
| LLM API cost (monthly) | Tracked from API usage dashboard | Monthly >$50 |
| Briefing email delivery | Confirmed via email send status | Failed delivery on catch-up |
| Unprocessed email backlog | Count of emails since last run | >500 (suggests catch-up frequency too low) |

### 15.5 — Security

| Requirement | Implementation |
|---|---|
| Immigration data encryption | AES-256 at rest for passport numbers, A-numbers, receipt numbers |
| API credentials | macOS Keychain, never in plaintext config files |
| OAuth tokens | Stored in Keychain, auto-refreshed |
| Audit trail | Immutable append-only log of all Artha actions and recommendations |
| Data export | User can export full state in portable format at any time |
| Data deletion | User can delete any data category immediately |
| Pre-flight PII detection | ≥99% for structured PII (SSN, CC, routing numbers) at device boundary | `pii_guard.sh` regex filter |
| PII defense-in-depth | Two independent layers: regex pre-flight (Layer 1) + LLM redaction (Layer 2) |

### 15.6 — Geographic Portability

Artha must not hardcode assumptions about US-centric systems. As an immigrant family, geographic transitions (domestic relocation, cross-border move) are plausible life events.

| Requirement | Implementation |
|---|---|
| Provider-agnostic agent logic | Integration Adapter Pattern (Section 9.6) — agents consume generic interfaces, not provider-specific formats |
| Jurisdiction-aware rules | Tax rules, registration requirements, school systems, and utility providers are configuration, not code |
| Overlapping jurisdiction support | During a transition, Artha can track obligations in two jurisdictions simultaneously (e.g., two state tax systems, overlapping utility accounts) |
| Data migration | All state is portable — OneDrive folder can be pointed at from any new machine. Encryption keys re-provisioned on new device via Keychain export or manual re-entry. |

**Not a current priority** — but the prompt-based adapter pattern and configuration-driven rules ensure that a geographic transition does not require rewriting prompt logic.

### 15.7 — Cost

| Component | Target | Control mechanism |
|---|---|---|
| Claude API (monthly) | <$50 | Prompt caching + batch processing + usage dashboard alerts |
| Gmail API | Free tier (15,000 units/day) | Well within limits for personal email volume |
| Google Calendar API | Free tier | Well within limits |
| Gemini CLI | Free tier quota | Web research, URL summarization, Imagen visual generation — $0 |
| Copilot CLI (GitHub) | Free tier | Script/config validation — $0 |
| ~~Plaid API~~ FDX/Financial API | **Deferred** | Direct financial data integration deferred beyond Phase 3 |
| Canvas LMS API | Free tier (institutional) | Covered under school’s Canvas license — $0 |
| Apple Health export | $0 | Local XML export, no API cost |
| Custom infrastructure | $0 | No VMs, no servers — OneDrive is existing subscription |
| OneDrive storage | $0 incremental | Artha state + visuals < 50 MB — negligible within existing OneDrive plan |
| Total | <$55/month | Self-monitored via health-check. Multi-LLM routing saves ~$3-6/month vs. Claude-only |

---

## 16. Open Questions — Resolved

These items were flagged in v1.0 for resolution. Based on expert review feedback, recommended resolutions are provided.

| # | Question | Resolution |
|---|---|---|
| OQ-1 | What is the preferred morning briefing delivery channel? | **Email (formatted Markdown).** Most portable, works on all devices (iPhone, Windows work laptop, Mac), searchable, no custom UI needed. Terminal output on Mac for immediate reading during catch-up session. |
| OQ-2 | Should Archana have her own Artha interface? | **Yes — tiered family access model.** Vedprakash: full access to all domains. Archana: full access to shared domains (finance, immigration, kids, home, travel, health) + her own personal domains. Parth: read-only view of his academic/activity data + his own personal goals. Trisha: age-appropriate view of her academic data. |
| OQ-3 | What are the 5 most important goals to define first? | **1.** Net worth / savings trajectory. **2.** Immigration readiness (all documents current, deadlines known). **3.** Parth academic trajectory (GPA target). **4.** Protected family time (≥ X hours/week). **5.** Learning consistency (hours/month). These span the highest-friction domains and have clearly automatable metrics. |
| OQ-4 | What are the configured "work hours"? | **8 AM – 6 PM weekdays** (not 7 PM — if working past 6, that IS the signal). Include "focus time" blocks on personal calendar as protected. |
| OQ-5 | Should Artha track Parth's college prep as a formal project? | **Yes — track as a formal Milestone goal with sub-milestones.** Parth enters 12th grade Fall 2026. The 2026–2027 school year is the application year. Sub-milestones: SAT score tracking, college list building, essay timeline, recommendation letter tracking, financial aid deadline calendar. Start in Phase 1. |
| OQ-6 | What is the immigration priority date and I-485 window? | **Critical to resolve immediately.** This data exists in Fragomen correspondence — the ImmigrationAgent should extract it during initial data ingestion. Without it, FR-2.3 (Case Timeline Tracker) cannot function. |
| OQ-7 | Are there financial accounts not listed? | **Ask Archana directly.** If she holds any accounts independently (savings, credit card, investment), those must be in the net worth calculation. Incomplete data is worse than no data for a financial goal. |
| OQ-8 | What is the Home Assistant URL and API token? | **Obtain from Home Assistant settings page.** Typically `http://homeassistant.local:8123` with a long-lived access token from Profile > Security. |
| OQ-9 | What are the actual financial goal target values? | **Define during conversational goal creation wizard.** Don't put specific dollar values in the PRD. The wizard will elicit: "What's your net worth target for end of 2026? What monthly savings rate supports that?" |
| OQ-10 | Should Artha have India-specific intelligence? | **Yes, include with P2 priority.** HDFC NRI, India travel patterns, and family connections are real and recurring. The India Trip Planner (F5.6) is a natural fit. |

---

*Artha PRD v4.0 — End of Document*

*"Artha is not about having more. It is about knowing where you stand, so you can decide where to go. Nothing sensitive leaves the device. Three LLMs work together — the right model for the right task at the right cost. The system reads, reasons, proposes, and acts — but only with your approval."*

---

**Next steps:**
1. Resolve OQ-6 (immigration priority date) and OQ-7 (Archana's accounts) — required for Phase 1
2. Resolve TD-18 (Archana email access — forward to Gmail or separate OAuth?)
3. Capture vehicle details (make, model, year, VIN), insurance carriers/policy numbers, ISP/telecom provider, and estate planning document status
4. Proceed to **Artha Tech Spec v1.6** — Artha.md specification, directory layout, MCP tool setup, state file schemas, catch-up workflow, prompt file samples, OAuth setup, email delivery, security model
5. Proceed to **Artha UX Spec v1** — briefing format, alert design, chat patterns, goal tracking, family access views
6. Begin **Phase 1A Implementation** — Author Artha.md + CLAUDE.md loader, set up Gmail MCP + Calendar MCP, create directory structure, bootstrap initial state files, run first catch-up
