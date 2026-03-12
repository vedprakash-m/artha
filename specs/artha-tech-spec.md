# Artha — Technical Specification

> **Version**: 2.2.0 | **Status**: Active Development | **Date**: March 12, 2026
> **Author**: Vedprakash Mishra | **Classification**: Personal & Confidential
> **Implements**: PRD v4.1
>
> **v2.2 Changes — WorkIQ Work Calendar Integration (PRD v4.1):** §3.10 NEW: WorkIQ Calendar MCP configuration — `@microsoft/workiq` (Microsoft-published npm), pinned version, pipe-delimited structured query format, combined detection+auth with 24h cache. §4.13 NEW: Work Calendar State schema (`state/work-calendar.md`) — count+duration metadata, 13-week rolling density window, conflict history. §7.1: Step 0 updated with WorkIQ combined preflight check (P1, non-blocking); Step 4.3 NEW parallel WorkIQ calendar fetch with explicit date-range query and context pressure integration (7-day at green, 2-day at red); Step 6 enhanced with Rules 7a (cross-domain conflict, Impact=3), 7b (internal work conflict, Impact=1), 8 (duration-based load analysis); Step 7 updated with merged calendar briefing + Teams Join action; Step 8h adds work-calendar.md update; Step 9 adds explicit `rm tmp/work_calendar.json` cleanup. §8.1: WorkIQ M365 credential added. §8.2.6 NEW: Work Calendar domain redaction rules (partial keyword replacement). Cross-references PRD v4.1, UX spec v1.5.
>
> **v2.1 Changes — Intelligence Amplification (PRD v4.0):** 29 enhancements from deep expert review. §3.5: Added Canvas LMS API; Plaid/FDX deferred. §3.6.1: Added `/diff` slash command. §4.4: Added college application countdown schema to Kids State. §4.10: Added `deadline` field and auto-escalation to Decisions State schema. §5.1: Added Week Ahead Preview section (Monday briefings), PII Detection Footer, post-briefing Calibration Questions block. §5.3 NEW: Monthly Retrospective format specification. §7.1: Added Step 7b (calibration questions), Step 7c (stale decision deadline check). §7.16 NEW: `/diff` command workflow — compute and display state deltas since last catch-up. §7.17 NEW: Weekend Planner workflow — Friday afternoon family weekend optimization. §7.18 NEW: Canvas LMS API fetch specification. §7.19 NEW: Apple Health XML import specification. §15: Updated Phase 2A with workstreams T–X (v4.0 items); Phase 2B with Canvas API, Apple Health, Tax Automation, Sub ROI; Phase 3 with WhatsApp Bridge. §16: Added TD-26 through TD-30 for v4.0 design decisions. Cross-references PRD v4.0, UX spec v1.4.
>
> **v2.0 Changes — Supercharge: Data Integrity, Intelligence Amplification & Operational Resilience:** Eighteen enhancement workstreams from expert review (PRD v3.9). §3.6.1: Added `/bootstrap` slash command for guided state population interview. §4.5: Enhanced health-check schema with signal:noise tracking, context window pressure stats, OAuth token health monitoring. §4.7: Added behavioral baselines and coaching preferences sections to memory.md schema. §4.12 NEW: Dashboard state file schema (`state/dashboard.md`) for life-at-a-glance snapshot. §5.1.2 NEW: Flash briefing variant (≤30 seconds reading time) with consequence forecasting. §7.1: Added data integrity guard (pre-decrypt backup, post-write verification, net-negative write guard) and session quick-start routing. §7.5 NEW: Bootstrap command workflow spec (guided interview → state population). §7.6 NEW: Compound signal detection engine (cross-domain pattern recognition, temporal correlation). §7.7 NEW: Pattern of life detection (behavioral baselines, anomaly surfacing). §7.8 NEW: Email volume scaling tiers (adaptive processing at 50/200/500+ emails). §7.9 NEW: Life scorecard format (weekly quantified life snapshot). §7.10 NEW: Consequence forecasting engine (if-you-don't alerts). §7.11 NEW: Pre-decision intelligence packets (research-on-demand). §7.12 NEW: Session quick-start routing specification. §7.13 NEW: Briefing compression levels (flash/standard/deep). §7.14 NEW: Context window pressure management. §7.15 NEW: OAuth token resilience specification. §8.5: Added three-layer data integrity guard to vault.sh spec. §15: Updated Phase 2A with workstreams K–S covering all supercharge items. §16: Added TD-20 through TD-25 for new design decisions. Cross-references PRD v3.9, UX spec v1.3.
>
> **v1.9 Changes — Phase 2A Intelligence Workstreams:** Ten workstreams from expert review synthesis (PRD v3.8). §4.9–4.11: Added `state/social.md` expanded schema (relationship graph model with tiers, protocols, contact patterns), `state/decisions.md` schema (cross-domain decision tracking), `state/scenarios.md` schema (what-if analysis). §4.1: Added `last_activity` timestamp to common state file format (tiered context — Workstream F). §4.5: Enhanced `health-check.md` with accuracy pulse fields (proposed/accepted/declined/deferred action counts, tier loading stats, email pre-processing stats). §5.1: Added digest mode briefing variant for >48hr gaps, relationship intelligence section in BY DOMAIN area, leading indicators in Goal Pulse. §5.2: Added Accuracy Pulse section and leading indicator divergence alerts to weekly summary. §6.1: Added `leading_indicators` extraction block to domain prompt template. §7.1: Enhanced step 5 (email pre-processing — marketing suppression, 1500-token cap, batch summarization), added step 5b (tiered context loading), updated step 2 with digest mode check (step 2b), enhanced step 8 (ONE THING URGENCY×IMPACT×AGENCY scoring). §7.4.1: Added `friction: low|standard|high` field to action proposal schema. §8.8 NEW: Privacy Surface Acknowledgment technical spec. Cross-references PRD v3.8, UX spec v1.2.
>
> **v1.8 Changes — Microsoft Graph Direct Integration (Email + Calendar):** Replaced hub-and-spoke forwarding model with direct MS Graph API reads for Outlook email and Outlook Calendar. §3.1: Updated Gmail MCP purpose (Outlook fetch is now §3.8, not forwarding). §3.5: Marked Outlook MCP row as superseded by MS Graph. §3.8 NEW: Full MS Graph integration spec — OAuth (live: vedprakash.m@outlook.com), `msgraph_fetch.py` (T-1B.1.1), `msgraph_calendar_fetch.py` (T-1B.1.6), token auto-refresh, catch-up Step 3 parallel fetch pattern, health check. §11.3: Replaced Outlook Forwarding Setup with Microsoft Graph Direct Fetch reference. Apple Mail remains forwarding-based (no API). artha-tasks.md: T-1B.1.1 rewritten as `msgraph_fetch.py` build task; T-1B.1.6 added for calendar; Phase 1B objective updated; Group 1 renamed.
>
> **v1.7 Changes — Operational Robustness + Task Integration + Email Coverage:** Added from operational experience after first two live catch-ups. §7.1: Added Step 0 pre-flight go/no-go gate before decrypt — checks OAuth token presence, script health (--health flags), lock file state, vault readiness; halts with named error on any failure; prevents silent-omission briefings (T-1A.11.3). §7.1 Step 8b: added open_items.md update — per-item deduplication, overdue re-surfacing, `todo_id` field for To Do sync. §7.2: Added 6 new failure modes — stale lock (auto-cleared >30 min), OAuth refresh failure (surfaces auth error not silent 0), API quota exceeded (hard halt), To Do sync failure (non-blocking warning). §4.7: Added `open_items.md` state file schema. §1.3: Added `open_items.md`, `todo_sync.py` to Component Summary. §11.4: Added Microsoft Graph API / Microsoft To Do integration spec. §11 email coverage: formalised hub-and-spoke model across Gmail (primary), Outlook (forward), Apple (forward), Yahoo (evaluate), Proton (excluded/Bridge Phase 2); documented Gmail label strategy for source attribution. Added §3.8 Microsoft Graph API OAuth spec (single token for To Do + Outlook). Operational reliability additions: OAuth token auto-refresh test protocol, stale lock detection logic in vault.sh, exponential backoff on 429/503 in gmail_fetch.py and gcal_fetch.py, --dry-run mode in gmail_send.py. Cross-references PRD v3.7.
>
> **v1.6 Changes — Critical Assessment Hardening:** Renamed instruction file from `CLAUDE.md` to `Artha.md` with thin `CLAUDE.md` loader for clean separation from other Claude Code projects (§2). Fixed pii_guard.sh data flow: clarified as pre-persist filter with Option C hook interception as stretch goal (§8.6). Fixed Gmail MCP scope to `gmail.readonly` + `gmail.send` (§3.1). Fixed Gemini CLI install reference (§3.7.1). Fixed "Eastlake High School" → "Tesla STEM HS" (§4.4). Fixed Privacy Rules contradiction with OneDrive sync (§2.1). Fixed §13 subsection numbering. Added per-domain `last_email_processed_id` for crash-resilient idempotency (§4.1). Added outbound PII wrapper `safe_cli.sh` for Gemini/Copilot calls (§3.7.7, §8.7). Added `contacts.md` to encrypted tier (§8.5). Added verification/confidence fields to critical domain prompts (§6.2, §6.3). Added vault.sh crash recovery via OneDrive selective sync (§8.5). Added `/health` slash command (§3.6.1). Added Gmail `historyId` recommendation to TD-5. Added Archana email blind spot to Phase 1A + OQ. Fixed weekly summary trigger (§5.2). Added context window monitoring metric (§7.2). Updated Phase 1A tasks. Added TD-18 (Archana email access), TD-19 (pii_guard.sh interception layer). 18 issues from independent critical assessment addressed.
>
> **v1.5.1 Changes — Gemini Review Hardening:** Added deduplication rules to §6.1 domain prompt template and §6.2/§6.3 examples — prevents duplicate state entries from follow-up emails. Added step 3b (email content pre-processing) to §7.1 catch-up workflow — HTML stripping, thread truncation, footer removal to manage context window token budget. Added email content pre-processing to §9.2 fallback points. Added EB-2 India Visa Bulletin parsing instruction to §6.2 immigration prompt. Added cross-domain trigger pattern to §6.3 finance prompt (travel booking → credit card benefit surfacing). Strengthened §2.3 idempotency principle with dedup rule reference. Added TD-16 (Gmail send safety mechanism) and TD-17 (Claude Code portability strategy) to §16.
>
> **v1.5 Changes — Multi-LLM Orchestration & Action Execution Framework:** Added §3.7 Multi-LLM Orchestration Layer — leverages Gemini CLI (free web search, URL summarization, Imagen visual generation) and Copilot CLI (free code/config validation) alongside Claude for cost-aware routing and ensemble reasoning on high-stakes decisions. Added §7.4 Action Execution Framework — full action lifecycle (propose → approve → execute → log) with action catalog, proposal schema, email composition (general-purpose beyond briefing delivery), WhatsApp messaging via URL scheme (human-gated), calendar event creation, and AI visual generation via Gemini Imagen for festival greetings and occasion cards. Updated §8.1 credentials, §10 cost model with multi-LLM routing, §12.1 registry with CLI tools and action channels, §15 Phase 1A/1B with setup tasks, §16 with new open decisions.
>
> **v1.4 Changes — Governance & Evolution Framework:** Added §12 Governance & Evolution Framework — comprehensive lifecycle management for all Artha components. Includes: component registry (`registry.md`) as the system manifest, CLAUDE.md change management with versioning and canary runs, domain lifecycle checklist (add/update/split/retire), MCP server onboarding process with security review, data source addition procedures (new email accounts, document repos, APIs), state file schema evolution strategy, script lifecycle with quantitative removal criteria, feedback & learning loop with accuracy tracking and memory pruning, AI feature adoption process with quarterly review cadence, hook & slash command governance. Addresses extensibility for adding email accounts, document repositories, new MCP servers, and future AI capabilities. Renumbered §12–§15 to §13–§16.
>
> **v1.3 Changes — Pre-Flight PII Guardrails & Claude Code Capabilities:** Added §8.6 Pre-Flight PII Filter — a mandatory device-local regex scanner (`pii_guard.sh`) that intercepts SSN, credit card, bank routing/account numbers, passport numbers, A-numbers, and ITINs **before** email content enters the Claude API context. Defense-in-depth: §8.2 redaction rules remain as the second layer for state files; the PII filter is the first layer at the API boundary. Added §3.6 Claude Code Capabilities Utilization — specifies custom slash commands (`/catch-up`, `/status`, `/goals`), Claude Code hooks for automatic vault.sh encrypt/decrypt, parallel MCP tool invocation for Gmail+Calendar, sub-agent pattern for Phase 2 domain parallelism, and built-in memory complementing `memory.md`. Updated catch-up workflow (§7.1) with PII filter step and parallel fetch. Updated fallback points, bootstrap script, validation checklist, and Phase 1A tasks.
>
> **v1.2 Changes — OneDrive Sync Layer:** State files now live in `~/OneDrive/Artha/` (configurable), synced across Mac, iPhone, and Windows. Mac is the sole writer; other devices are read-only consumers. Sensitive state files (`high`/`critical`) are `age`-encrypted before sync — encryption keys in device-local credential stores, never on OneDrive. Encrypted state tier promoted from Phase 2 optional to Phase 1 requirement. iPhone access simplified — OneDrive replaces the snapshot generation/upload pattern. Updated architecture diagram, filesystem paths, bootstrap script, and resolved TD-3/TD-7.

---

## 1. Architecture Overview

### 1.1 Design Philosophy

Artha is a **pull-based personal intelligence system** built on four principles:

1. **Claude Code IS the application.** There is no custom daemon, no web server, no background process. The user opens a Claude Code session, says "catch me up," and Claude — guided by CLAUDE.md — orchestrates the entire workflow using MCP tools. The instruction file is the application.

2. **Prompts are the logic layer.** Domain-specific behavior (what to extract from immigration emails, when to alert about a bill, how to score goal progress) lives in Markdown prompt files — not in code. Adding a new life domain = adding a new `.md` file. No compilation, no deployment, no restart.

3. **State lives in Markdown.** All of Artha's world model — immigration status, financial state, school grades, goal progress — is stored in plain Markdown files with YAML frontmatter. Human-readable, human-editable, git-diffable, and small enough to fit entirely in Claude's 200K context window.

4. **Zero custom code is the target.** Start with nothing but CLAUDE.md + prompt files + MCP tools. Add helper scripts only when Claude proves unreliable at a specific step (e.g., OAuth token refresh, SMTP delivery). Each script addresses exactly one failure point — no orchestration frameworks, no application scaffolding.

### 1.2 System Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                     USER'S MAC                                  │
│                                                                  │
│   Terminal: claude                                               │
│   User: "catch me up"                                            │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │                    CLAUDE CODE                            │  │
│   │                                                          │  │
│   │   ┌──────────────────────────────────────────────────┐  │  │
│   │   │               CLAUDE.md                           │  │  │
│   │   │                                                   │  │  │
│   │   │  Identity · Catch-Up Workflow · Routing Rules     │  │  │
│   │   │  Domain Prompt Registry · Output Format           │  │  │
│   │   │  Alert Thresholds · Privacy Rules                 │  │  │
│   │   └──────────────────┬───────────────────────────────┘  │  │
│   │                      │                                    │  │
│   │   ┌──────────────────▼───────────────────────────────┐  │  │
│   │   │          DOMAIN PROMPT FILES                      │  │  │
│   │   │  ~/OneDrive/Artha/prompts/*.md                       │  │  │
│   │   │                                                   │  │  │
│   │   │  immigration.md · finance.md · kids.md            │  │  │
│   │   │  comms.md · health.md · calendar.md               │  │  │
│   │   │  travel.md · home.md · goals.md                   │  │  │
│   │   │  + 9 more domain prompts                          │  │  │
│   │   └──────────────────┬───────────────────────────────┘  │  │
│   │                      │                                    │  │
│   │   ┌──────────┐  ┌───▼──────┐  ┌──────────┐             │  │
│   │   │ Gmail    │  │ Calendar │  │ File     │              │  │
│   │   │ MCP      │  │ MCP      │  │ System   │              │  │
│   │   │          │  │          │  │ (native) │              │  │
│   │   │ OAuth    │  │ OAuth    │  │          │              │  │
│   │   │ Read     │  │ Read     │  │ Read +   │              │  │
│   │   │ Only     │  │ Only     │  │ Write    │              │  │
│   │   └────┬─────┘  └────┬─────┘  └────┬─────┘             │  │
│   │        │             │             │                     │  │
│   └────────┼─────────────┼─────────────┼─────────────────────┘  │
│            │             │             │                         │
│   ┌────────▼─────────────▼─────────────▼─────────────────────┐  │
│   │         ~/OneDrive/Artha/state/*.md                       │  │
│   │                                                          │  │
│   │  calendar.md · kids.md · goals.md · memory.md            │  │
│   │  finance.md.age · health.md.age · immigration.md.age     │  │
│   │  estate.md.age · insurance.md.age · audit.md.age         │  │
│   └────────────────────────┬─────────────────────────────────┘  │
│                            │                                     │
│   ┌────────────────────────▼─────────────────────────────────┐  │
│   │              ONEDRIVE SYNC LAYER                          │  │
│   │                                                          │  │
│   │  Mac writes → OneDrive syncs → iPhone/Windows read       │  │
│   │  Standard files: synced as plaintext                     │  │
│   │  High/Critical files: synced as .age encrypted           │  │
│   │  Encryption keys: macOS Keychain (never on OneDrive)     │  │
│   └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │              OUTPUT CHANNELS                              │  │
│   │                                                          │  │
│   │  Terminal ──── immediate feedback during session          │  │
│   │  Email ─────── briefing sent for quick mobile access     │  │
│   │  OneDrive ──── always-fresh state on all devices         │  │
│   │  Claude.ai ─── Project with OneDrive state (iPhone)      │  │
│   └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### 1.3 Component Summary

| Component | Location | Purpose |
|---|---|---|
| CLAUDE.md | `~/OneDrive/Artha/CLAUDE.md` | Thin loader — auto-read by Claude Code, delegates to `Artha.md` |
| Artha.md | `~/OneDrive/Artha/Artha.md` | Primary instruction file — Artha's identity, workflow, routing, and rules |
| Domain prompts | `~/OneDrive/Artha/prompts/*.md` | Domain-specific extraction, alerting, and state update rules |
| State files | `~/OneDrive/Artha/state/*.md` | Living world model — one Markdown file per domain |
| Open items | `~/OneDrive/Artha/state/open_items.md` | Persistent action item list extracted from catch-ups; bridge to Microsoft To Do |
| Dashboard | `~/OneDrive/Artha/state/dashboard.md` | Life-at-a-glance snapshot — life pulse, active alerts, life scorecard; rebuilt each catch-up *(v2.0)* |
| Encrypted state | `~/OneDrive/Artha/state/*.md.age` | `age`-encrypted state for high/critical sensitivity domains |
| Briefing archive | `~/OneDrive/Artha/briefings/*.md` | Historical catch-up briefings (ISO-dated, sensitivity-filtered) |
| Summary archive | `~/OneDrive/Artha/summaries/*.md` | Historical weekly summaries |
| Config | `~/OneDrive/Artha/config/settings.md` | Alert thresholds, email targets, sync path, account lists, To Do list IDs |
| Scripts (if needed) | `~/OneDrive/Artha/scripts/` | Minimal helper scripts (`vault.sh`, `gmail_fetch.py`, `gcal_fetch.py`, `gmail_send.py`, `todo_sync.py`) |
| OneDrive sync | Native OS integration | Cross-device state sync; Mac writes, iPhone/Windows read |
| Gmail MCP | Claude Code MCP config | Email access via OAuth (read-only) |
| Google Calendar MCP | Claude Code MCP config | Calendar access via OAuth (read-only) |

---

## 2. Instruction File Specification

Artha's behavior is defined by `Artha.md` — the primary instruction file. Claude Code auto-reads `CLAUDE.md` on session start, which delegates to `Artha.md`.

**Why two files:** Claude Code requires a file named `CLAUDE.md` in the project root. But Ved uses Claude Code for other projects too. A thin `CLAUDE.md` that loads `Artha.md` provides clean separation — the Artha-specific logic has its own named identity file, while Claude Code's auto-loading mechanism still works.

**`CLAUDE.md` (3-line loader):**
```markdown
# Artha Loader
Read and follow ALL instructions in Artha.md in this directory.
Do not proceed without reading Artha.md first.
```

**`Artha.md` (full instruction file):**

### 2.1 Structure

```markdown
# Artha — Personal Intelligence System

## Identity
You are Artha, a personal intelligence system for the Mishra family.
You serve Vedprakash ("Ved"), Archana, Parth (17), and Trisha (12).
You run as a Claude Code session on Ved's Mac. You are not a chatbot —
you are an operating system for personal life management.

## Core Behavior
- Be direct, specific, and actionable — not conversational
- Surface what matters; suppress noise
- When in doubt about urgency, err on the side of alerting
- Never fabricate data — if a state file is empty, say so
- All write actions (send email, add calendar event) require explicit user approval
- Log all actions and recommendations to ~/OneDrive/Artha/state/audit.md

## Catch-Up Workflow
When the user says "catch me up" (or equivalent: "what did I miss",
"morning briefing", "SITREP"):

1. Run ./scripts/vault.sh decrypt (unlock sensitive state files)
2. Read ~/OneDrive/Artha/state/health-check.md for last run timestamp
3. Fetch emails + calendar IN PARALLEL:
   - Gmail MCP: gmail_search(after:last_timestamp) + gmail_get_message per result
   - Calendar MCP: calendar_list_events(today, +7 days)
4. Run ./scripts/pii_guard.sh filter on email batch (Layer 1 PII defense)
   ⚠ HALT if pii_guard.sh exits non-zero
5. For each email/event, route to the appropriate domain prompt:
   - Match sender/subject against routing rules below
   - Apply the domain prompt's extraction rules
   - Apply §8.2 redaction rules (Layer 2 PII defense)
   - Update the domain's state file
   - Evaluate alert thresholds
6. After all items processed, synthesize the briefing:
   - 🔴 Critical alerts first
   - 🟠 Urgent items
   - 📅 Today's calendar
   - 📬 Actionable emails by domain
   - 🎯 Goal pulse (if weekly check-in due)
   - 💡 ONE THING — the single most important insight
7. Email the briefing to [configured address]
8. Save briefing to ~/OneDrive/Artha/briefings/YYYY-MM-DD.md
9. Update ~/OneDrive/Artha/state/health-check.md with current timestamp
10. Log PII filter stats to audit.md
11. Run ./scripts/vault.sh encrypt (re-lock sensitive state files)

## Routing Rules
[See Section 2.2 for complete routing table]

## Output Format
[See Section 5 for briefing format specification]

## Domain Prompts
The following prompt files define domain-specific behavior:
- ~/OneDrive/Artha/prompts/immigration.md
- ~/OneDrive/Artha/prompts/finance.md
- ~/OneDrive/Artha/prompts/kids.md
- [... one per FR]
Read each prompt file when processing items for that domain.

## Privacy Rules
- Never store full email body text — extract structured data only
- Immigration documents (passport numbers, A-numbers) use [REDACTED] in logs
- Never send state files to any external service other than Claude API and OneDrive (the user's own cloud storage)
- State files reside in the OneDrive-synced Artha directory. Sensitive state files (high/critical) are age-encrypted before sync — OneDrive only sees `.age` files for those domains
- Never call external CLIs (Gemini, Copilot) with raw PII — always use `./scripts/safe_cli.sh` wrapper
- Log every action to audit.md for traceability
```

### 2.2 Routing Rules

The routing table maps email senders and subject patterns to domain prompts. Claude's native reasoning handles ambiguous cases — the table provides explicit routing for known senders.

| Pattern | Domain Prompt | Priority |
|---|---|---|
| From: `*@fragomen.com` | `immigration.md` | 🔴 Critical |
| From: `*uscis.gov` | `immigration.md` | 🔴 Critical |
| Subject contains: `visa bulletin` | `immigration.md` | 🟠 Urgent |
| From: `*@parentsquare.com` | `kids.md` | Standard |
| From: `*@instructure.com` / Canvas | `kids.md` | 🟠 if grade alert |
| From: `*@pse.com` | `home.md` | Standard |
| From: `*@sammamish*` | `home.md` | Standard |
| From: `*@chase.com` | `finance.md` | Standard |
| From: `*@fidelity.com` | `finance.md` | Standard |
| From: `*@wellsfargo.com` | `finance.md` | Standard |
| From: `*@vanguard.com` | `finance.md` | Standard |
| Subject contains: `bill`, `payment due`, `statement` | `finance.md` | Standard |
| From: `*@alaskaair.com` | `travel.md` | Standard |
| From: `*@marriott.com` | `travel.md` | Standard |
| Subject contains: `appointment`, `prescription` | `health.md` | Standard |
| From: `*@usps.com` | `shopping.md` | Low |
| Subject contains: `delivery`, `shipped`, `tracking` | `shopping.md` | Low |
| From: `*@equifax.com` | `finance.md` | 🟠 Urgent |
| Calendar event | `calendar.md` | Standard |
| Unknown sender — actionable content | `comms.md` | Standard |
| Unknown sender — marketing/spam | Skip | — |

### 2.3 CLAUDE.md Design Principles

1. **Declarative, not procedural.** CLAUDE.md describes WHAT Artha does, not HOW to implement it. Claude's reasoning handles the implementation.
2. **Routing rules are hints, not gates.** If an email from an unknown sender contains immigration content, Claude routes it to `immigration.md` regardless of the routing table.
3. **Fail-open for alerts.** If Claude is unsure whether something is alert-worthy, it surfaces it. False positives are acceptable; false negatives are not.
4. **Idempotent catch-up.** Running catch-up twice in quick succession should not duplicate state entries or briefing content. Claude checks state file timestamps and applies domain-specific deduplication rules (§6.1) to detect entries from the same source for the same item before creating new state entries.

---

## 3. MCP Tool Configuration

### 3.1 Gmail MCP

**Purpose:** Read personal Gmail (`mi.vedprakash@gmail.com`). Outlook/Hotmail email is fetched directly via MS Graph API — see §3.8. No forwarding required.

**Setup:**
1. Create Google Cloud project
2. Enable Gmail API
3. Create OAuth 2.0 credentials (Desktop application type)
4. Scopes required: `gmail.readonly` + `gmail.send` (send needed for briefing delivery and Trust Level 1 email composition; `gmail.modify` deferred to Phase 2 pending Trust Level 2 elevation)
5. Configure in Claude Code MCP settings:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "npx",
      "args": ["@anthropic/gmail-mcp"],
      "env": {
        "GMAIL_OAUTH_CLIENT_ID": "${GMAIL_CLIENT_ID}",
        "GMAIL_OAUTH_CLIENT_SECRET": "${GMAIL_CLIENT_SECRET}",
        "GMAIL_OAUTH_REFRESH_TOKEN": "${GMAIL_REFRESH_TOKEN}"
      }
    }
  }
}
```

**Available operations (read-only):**
- `gmail_search` — Search emails by query (sender, subject, date range, label)
- `gmail_get_message` — Get full message content by ID
- `gmail_list_messages` — List messages matching criteria
- `gmail_get_thread` — Get full email thread

**Catch-up usage pattern:**
```
1. gmail_search(query="after:{last_run_timestamp}")
2. For each message: gmail_get_message(id) → route to domain prompt
```

**OAuth token management:**
- Refresh token stored in macOS Keychain
- If MCP handles token refresh automatically: no custom code needed
- If MCP token refresh fails: create `~/OneDrive/Artha/scripts/refresh_gmail_token.sh` as fallback

### 3.2 Google Calendar MCP

**Purpose:** Read calendar events for scheduling intelligence.

**Setup:**
1. Use same Google Cloud project as Gmail
2. Enable Google Calendar API
3. Scopes required: `calendar.readonly`
4. Configure alongside Gmail in Claude Code MCP settings:

```json
{
  "mcpServers": {
    "calendar": {
      "command": "npx",
      "args": ["@anthropic/google-calendar-mcp"],
      "env": {
        "GCAL_OAUTH_CLIENT_ID": "${GCAL_CLIENT_ID}",
        "GCAL_OAUTH_CLIENT_SECRET": "${GCAL_CLIENT_SECRET}",
        "GCAL_OAUTH_REFRESH_TOKEN": "${GCAL_REFRESH_TOKEN}"
      }
    }
  }
}
```

**Available operations (read-only):**
- `calendar_list_events` — List events for a date range
- `calendar_get_event` — Get event details by ID
- `calendar_list_calendars` — List available calendars

**Catch-up usage pattern:**
```
1. calendar_list_events(timeMin=today, timeMax=today+7d)
2. Update ~/OneDrive/Artha/state/calendar.md with upcoming events
3. Check for conflicts across family members
```

### 3.2b WorkIQ Calendar MCP *(v2.2 — PRD F8.8)*

**Purpose:** Fetch Microsoft corporate work calendar events. Available on Windows work laptop only (M365 Copilot enterprise license). Graceful degradation on Mac (silent skip).

**Package:** `@microsoft/workiq` (Microsoft-published, `@microsoft` npm scope). Pinned version stored in `config/settings.md` under `workiq_version`.

**Provenance:**
- **Publisher:** Microsoft
- **Auth:** M365 Copilot license via Entra ID (enterprise tenant only)
- **Stability:** Active development — API surface may change between versions
- **Fallback:** If package is deprecated/removed from npm, Artha degrades to "WorkIQ unavailable." Future alternative: direct MS Graph Calendar API via `scripts/msgraph_fetch.py`.

**Combined detection + auth (preflight):**
```python
# Single npx call validates availability + auth in one shot.
# Cache result for 24h in tmp/.workiq_cache.json (ephemeral, not in state/).
npx -y @microsoft/workiq@{PINNED_VERSION} ask -q "What is my name?"
# Success → available=true, auth_valid=true
# Auth failure → available=true, auth_valid=false
# npx not found → available=false (Mac)
```

**Catch-up fetch — explicit date-range, structured output:**
```bash
# {START_DATE}/{END_DATE} computed at runtime as YYYY-MM-DD
# Context pressure GREEN/YELLOW: 7-day window (Mon→Sun)
# Context pressure RED/CRITICAL: 2-day window (today+tomorrow)
npx -y @microsoft/workiq@{PINNED_VERSION} ask \
  -q "List all my calendar events from {START_DATE} through {END_DATE}.
      For each event return EXACTLY this format, one per line:
      DATE | START_TIME | END_TIME | TITLE | ORGANIZER | LOCATION | TEAMS(yes/no)
      Do not add headers, footers, or commentary."
```

**Output parsing:** Pipe-delimited response parsed by `parse_workiq_response()` into Artha calendar schema. Handles: extra whitespace, missing fields, non-conforming lines (skipped). See §7.1 Step 4.3 for parser specification.

**Partial redaction (pre-API):** Before parsed events enter Claude context, apply `redact_work_events()` using `config/settings.md` keyword list. Only matched substrings replaced (e.g., "Project Cobalt Review" → "[REDACTED] Review"). See §8.2.6.

**Error handling:**
| Failure | Behavior |
|---|---|
| Mac (npx not found) | Silent skip. Footer: "ℹ️ Work calendar: available on Windows laptop only" |
| Auth expired | Skip. Footer: "⚠️ WorkIQ auth expired — run `workiq logout` then retry" |
| Timeout (>30s) | Skip, log to audit.md. Footer: "⚠️ Work calendar timeout" |
| 0 events parsed from non-empty response | Log warning: format may have changed. Retry with explicit prompt. |
| Empty response | Log, continue (may be a light day or weekend) |

**File layout:**
| File | Purpose | Persisted? |
|---|---|---|
| `tmp/work_calendar.json` | Raw WorkIQ response | ❌ Deleted at Step 9 |
| `tmp/.workiq_cache.json` | Detection+auth cache (24h TTL) | ❌ Ephemeral |
| `state/work-calendar.md` | Count+duration metadata only | ✅ (no titles/attendees) |

**Purpose:** Read and write Markdown state files in the OneDrive-synced Artha directory.

**Access method:** Claude Code has native filesystem access — no MCP server needed.

**Operations:**
- Decrypt sensitive state files: `./scripts/vault.sh decrypt` (before reading)
- Read state files: `cat ~/OneDrive/Artha/state/*.md`
- Write state files: Direct file writes via Claude Code
- Read prompt files: `cat ~/OneDrive/Artha/prompts/*.md`
- Write briefings: Save to `~/OneDrive/Artha/briefings/`
- Write summaries: Save to `~/OneDrive/Artha/summaries/`
- Re-encrypt sensitive state files: `./scripts/vault.sh encrypt` (after writing)

**OneDrive sync:** Files saved to `~/OneDrive/Artha/` are automatically synced to all devices by OneDrive. Mac is the sole writer — iPhone and Windows are read-only consumers. No sync conflict handling needed.

**Security:** Claude Code runs with the user's filesystem permissions. Sensitive state files exist as plaintext only during an active catch-up session (decrypted at start, re-encrypted at end). OneDrive only ever sees `.age` files for high/critical domains.

### 3.4 Email Sending (Briefing Delivery)

**Purpose:** Email the catch-up briefing for cross-device access.

**Options (in order of preference):**

1. **Gmail MCP send** — If the Gmail MCP supports sending (with `gmail.send` scope), use it directly. Simplest option, no custom code.

2. **Python SMTP script** — If the Gmail MCP is read-only:
   ```
   ~/OneDrive/Artha/scripts/send_briefing.py
   - Takes briefing Markdown as stdin
   - Converts to HTML email
   - Sends via Gmail SMTP (app password in Keychain)
   - Target: <50 lines of Python
   ```

3. **Apple Mail automation** — Use `osascript` to create and send via Mail.app. No credential management needed but less reliable.

**Configuration:**
- Recipient email stored in `~/OneDrive/Artha/config/settings.md`
- Sender credentials in macOS Keychain (never in plaintext files)

### 3.5 External Integrations & Data Skills

Artha uses targeted **"Skills"** (scripts) to complement MCP tools for high-fidelity data extraction.

| Source | Access Method | Credentials | Stack | Purpose |
|---|---|---|---|---|
| USCIS Status | Public Lookup | None | `requests` | Zero-latency immigration updates (Phase 1) |
| King County Tax | Public Lookup | None | `requests` | Property tax deadlines (Phase 1) |
| Canvas LMS | REST API | Developer Token | `requests` | School grades/assignments (Phase 2 Blocked) |
| OFX / FDX | Banking API | FI Credentials | `ofxtools` | Direct bank balance pull (Phase 2) |
| Microsoft Graph | REST API | OAuth2 | `msal` | Outlook Email + MS To Do sync |
| Home Assistant | Local API | LAN Token | `requests` | Smart home status (Phase 2) |

### 3.6 Claude Code Capabilities Utilization

Artha explicitly leverages Claude Code's native capabilities beyond basic MCP tools.

#### 3.6.1 Custom Slash Commands

Defined in CLAUDE.md under `## Custom Commands`:

| Command | Behavior |
|---|---|
| `/catch-up` | Full catch-up workflow (equivalent to "catch me up") |
| `/status` | Quick health check — last run time, stale domains, alert count |
| `/goals` | Goal scorecard only, no email fetch |
| `/domain <name>` | Deep-dive into a single domain's state and recent activity |
| `/cost` | Current month API cost estimate vs. budget |
| `/health` | System integrity check — verify all files in registry.md exist, state file schema versions match prompt expectations, CLI availability, report any drift |
| `/bootstrap` | Guided state population interview — walk through each domain with structured questions, validate answers, populate state files. See §7.5 for full workflow. *(v2.0 — Workstream L)* |
| `/diff` | Show state file changes since last catch-up. Computes delta between current state and previous snapshot, displays additions/removals/modifications per domain. See §7.16 for workflow. *(v2.1 — F15.51)* |

Purpose: Faster interaction for frequent operations. Avoids natural language parsing overhead for known intents.

#### 3.6.2 Hooks (Automatic Encrypt/Decrypt)

Claude Code hooks automate `vault.sh` without relying on CLAUDE.md text instructions (which Claude might skip):

| Hook | Trigger | Action |
|---|---|---|
| `PreToolUse` (file read) | Before reading any file in `state/` | Run `vault.sh decrypt` if `.age` files exist and plaintext versions don't |
| `Stop` | Session ending (catch-up complete or user exits) | Run `vault.sh encrypt` to re-lock sensitive files |

This makes encryption/decryption **automatic and guaranteed** rather than instruction-dependent. If hooks are not available in the Claude Code version deployed, fall back to CLAUDE.md instructions (belt-and-suspenders).

#### 3.6.3 Parallel Tool Invocation

Claude Code can invoke multiple MCP tools simultaneously when there are no data dependencies. The catch-up workflow exploits this:

```
Step 0: vault.sh decrypt

Step 1 (PARALLEL):
  ├── Gmail MCP: fetch emails since last run
  └── Calendar MCP: fetch events for next 7 days

Step 2 (SEQUENTIAL): pii_guard.sh filter on email batch

Step 3 (PARALLEL per domain — independent state files):
  ├── Process immigration emails → update immigration.md
  ├── Process finance emails → update finance.md
  ├── Process kids emails → update kids.md
  └── Process remaining domains...

Step 4 (SEQUENTIAL): Cross-domain reasoning + briefing synthesis

Step 5: vault.sh encrypt
```

Steps 1 and 3 can run in parallel because they have no inter-dependencies — each domain prompt reads only its own state file and writes only its own state file. This reduces catch-up wall-clock time by up to 40%.

#### 3.6.4 Sub-Agents via `claude --print` (Phase 2)

If domain processing becomes too complex for a single context window:

- Spawn sub-agent: `claude --print -p "Process these finance emails and return structured updates" < finance_emails.txt`
- Main agent collects sub-agent outputs and applies to state files
- Enables domain-specialized processing without consuming the main context window
- **Not needed in Phase 1** — all 18 domains fit comfortably in one 200K context window

#### 3.6.5 Built-in Memory

Claude Code has a persistent memory feature (`/memory`) that persists across sessions. This **complements** the `memory.md` state file:

| Concern | `memory.md` (state file) | Claude Code `/memory` |
|---|---|---|
| Content | Domain-specific preferences, corrections, decisions | Session-level behavioral adjustments |
| Scope | Shared across devices via OneDrive | Mac-only (local to Claude Code installation) |
| Examples | "Stop alerting about Spirit Week" | "Ved prefers briefings in bullet format, not prose" |
| Persistence | Synced, versioned, human-editable | Claude Code internal, opaque |

CLAUDE.md instructs: "For domain-specific learnings, update `memory.md`. For interaction style preferences, use `/memory`."

### 3.7 Multi-LLM Orchestration Layer

Artha leverages multiple LLM CLIs beyond Claude to optimize cost, expand capabilities, and enable ensemble reasoning for high-stakes decisions.

#### 3.7.1 Available CLIs

| CLI | Model | Cost | Capabilities | Use Cases |
|---|---|---|---|---|
| `claude` | Claude (Sonnet/Opus) | Paid (API) | Full reasoning, MCP tools, state management, hooks, memory | Orchestration, domain processing, briefing synthesis |
| `gemini` | Gemini (via Google CLI) | Free quota | Web search, URL summarization, Imagen image generation | Research, Visa Bulletin, property values, festival visuals |
| `copilot` | GitHub Copilot CLI | Free quota | Code analysis, config validation, second opinions | Script review, CLAUDE.md validation, config checks |

**Installation requirements:**
- Gemini CLI: Install via Google's official channel (`npm install -g @anthropic/gemini-cli` was a placeholder — verify current package name at https://ai.google.dev/gemini-api/docs/cli before installing). Authenticate via `gemini auth`.
- Copilot CLI: Already installed. Authenticate via `gh auth login` (GitHub CLI).
- Claude Code: Primary runtime — already installed and configured.

#### 3.7.2 Cost-Aware Routing

CLAUDE.md includes a routing directive that delegates tasks to the cheapest capable model:

```markdown
## Multi-LLM Routing Rules

When performing tasks, use the most cost-effective CLI:

1. **Web research** (Visa Bulletin, property values, recall checks, price comparisons):
   → Use `gemini` CLI — free quota, real-time web access
   → Command: `gemini "Search for USCIS Visa Bulletin EB-2 India priority date March 2026"`

2. **URL summarization** (reading a specific webpage):
   → Use `gemini` CLI — free quota, can read and summarize URLs
   → Command: `gemini "Summarize the content at https://travel.state.gov/..."`

3. **Config/script validation** (reviewing vault.sh, pii_guard.sh, CLAUDE.md changes):
   → Use `copilot` CLI — free quota, code-specialized
   → Command: `echo "review this script for security issues" | copilot`

4. **Domain reasoning, state updates, briefing synthesis, MCP tool calls**:
   → Use Claude (self) — only Claude has MCP tool access and state file context

5. **Visual generation** (festival greetings, occasion cards):
   → Use `gemini` CLI with Imagen — free quota
   → See §3.7.5 for details
```

**Estimated monthly savings:** Web research tasks (~10–15/month) that would consume Claude API tokens are routed to Gemini at $0. Estimated savings: $3–5/month.

#### 3.7.3 Ensemble Reasoning for High-Stakes Decisions

For critical decisions in high-sensitivity domains (immigration, finance, estate), Artha generates responses from all three LLMs and synthesizes the best answer:

**Trigger criteria (CLAUDE.md instruction):**
```markdown
## Ensemble Reasoning

Use ensemble reasoning (all 3 LLMs) when the decision involves:
- Immigration filing deadlines or eligibility windows
- Financial decisions exceeding $5,000
- Tax strategy or optimization
- Estate planning recommendations
- Any action where being wrong has multi-month or multi-year consequences

Process:
1. Formulate the question clearly (without PII — use redacted references)
2. Send to Gemini CLI: `gemini "Given [redacted context], what is the recommended approach for [question]?"`
3. Send to Copilot CLI (if applicable): `echo "[question]" | copilot`
4. Generate Claude's own answer
5. Compare all responses. Note agreements and disagreements.
6. Present synthesized recommendation to user with attribution:
   "All three models agree: [recommendation]"
   OR "Claude and Gemini agree on X, Copilot suggests Y. My recommendation: [synthesis with reasoning]"
```

**PII safety:** Ensemble queries are formulated WITHOUT raw PII. Use redacted references (e.g., "family member with EB-2 priority date of [DATE]" instead of names/A-numbers). The `pii_guard.sh` pre-flight filter does NOT apply to Gemini/Copilot CLI calls since those are spawned from Claude's shell — instead, CLAUDE.md redaction instructions govern what context is shared.

#### 3.7.4 Web Research via Gemini CLI

Replaces vague "Claude web fetch" entries in data sources with explicit Gemini CLI calls:

| Research Task | Current Method | New Method | Frequency |
|---|---|---|---|
| USCIS Visa Bulletin | "Claude web fetch" | `gemini "USCIS Visa Bulletin EB-2 India current month"` | Monthly |
| Zillow/Redfin home value | "Claude web search" | `gemini "Zillow estimate for [address] [city] WA"` | Quarterly |
| NHTSA recall check | "Claude web fetch" | `gemini "NHTSA recall lookup VIN [last5]"` | Monthly per VIN |
| King County Assessor | "Claude web fetch" | `gemini "King County property tax assessment [parcel]"` | Annually |
| Price comparisons | N/A (not supported) | `gemini "Compare prices for [item] across major retailers"` | On-demand |
| News/regulatory changes | N/A (not supported) | `gemini "Latest H-1B/EB-2 immigration policy changes 2026"` | Monthly |

**Output handling:** Gemini CLI returns text to stdout. Claude captures the output, validates it against known state, and incorporates into the relevant domain state file. Gemini output is treated as **untrusted external data** — Claude cross-references with existing state before updating.

#### 3.7.5 AI Visual Generation via Gemini Imagen

Gemini CLI provides access to Imagen for generating images. Artha uses this for:

**Use cases:**
- Festival greeting cards (Diwali, Holi, Eid, Christmas, New Year)
- Birthday wishes with personalized visuals
- Occasion-specific messages (anniversaries, congratulations)
- Custom visual summaries (e.g., a visual goal progress card)

**Generation workflow:**
```bash
# Generate a festival greeting image
gemini "Generate a beautiful Diwali greeting card image with diyas, rangoli, and the message 'Happy Diwali from the Mishra family'. Style: warm, traditional, festive." --output ~/OneDrive/Artha/visuals/diwali-2026.png

# Generate a birthday card
gemini "Generate a birthday card for a 17-year-old boy who loves technology and basketball. Include 'Happy Birthday Parth!' text." --output ~/OneDrive/Artha/visuals/parth-birthday-2026.png
```

**Output storage:** Generated visuals are saved to `~/OneDrive/Artha/visuals/` — synced via OneDrive for cross-device access.

**Integration with actions:** Generated visuals can be attached to:
- Email messages (via Gmail MCP `gmail.send` with attachment)
- WhatsApp messages (user manually attaches after opening via URL scheme)

#### 3.7.6 Fallback Chain

If the primary CLI for a task is unavailable:

| Task | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| Web research | Gemini CLI | Claude (if web-capable) | Report unavailability, skip research task |
| Script validation | Copilot CLI | Claude (self-review) | Skip validation, note in audit.md |
| Visual generation | Gemini Imagen | Skip (text-only greeting) | N/A |
| Domain processing | Claude | N/A (Claude is required) | HALT — report to user |
| Ensemble reasoning | All 3 CLIs | 2-of-3 (reduced confidence) | Claude-only (note reduced confidence) |

**Health monitoring:** CLI availability is tracked in `health-check.md`:

```markdown
## CLI Health
| CLI | Last Checked | Status | Last Error |
|---|---|---|---|
| claude | 2026-03-07T08:30:00 | ✅ operational | — |
| gemini | 2026-03-07T08:30:00 | ✅ operational | — |
| copilot | 2026-03-07T08:30:00 | ✅ operational | — |
```

CLAUDE.md instructs: "At the start of each catch-up, verify CLI availability with quick health checks (`gemini --version`, `copilot --version`). Update health-check.md. If a CLI is down, proceed with fallback chain."

#### 3.7.7 PII Boundary for External CLIs

When delegating to Gemini or Copilot, strict PII rules apply:

1. **Never send** to external CLIs: SSN, passport numbers, A-numbers, bank account numbers, credit card numbers, ITIN, DOB, full names of minor children
2. **Safe to send:** Redacted references, general questions, publicly available information, anonymized scenarios
3. **Enforcement:** Outbound PII wrapper script `safe_cli.sh` scans all queries before forwarding to external CLIs. See §8.7.
4. **Audit:** All external CLI calls are logged to `audit.md` with the query sent (for PII review)

---

### 3.8 Microsoft Graph API (Outlook Email, Outlook Calendar, To Do)

**Purpose:** Direct-read of Outlook/Hotmail email, Outlook Calendar events, and Microsoft To Do tasks via a single OAuth 2.0 token. Does not require email forwarding. Runs in parallel with Gmail/Google Calendar at catch-up Step 3.

**OAuth — Status: ✅ Live (2026-03-08)**
- Registered app: "Artha Personal Assistant" (Azure portal, personal accounts only, `http://localhost:8400` redirect)
- Client ID: macOS Keychain (`artha/msgraph-client-id`)
- Token: `~/.artha-tokens/msgraph-token.json` (authenticated as `vedprakash.m@outlook.com`)
- Auth script: `scripts/setup_msgraph_oauth.py` (`--health`, `--reauth` flags)
- Auto-refresh: `ensure_valid_token()` in `setup_msgraph_oauth.py`

**Scopes:**
| Scope | Purpose | Status |
|---|---|---|
| `Tasks.ReadWrite` | Create, read, update, complete MS To Do tasks | ✅ granted |
| `Tasks.ReadWrite.Shared` | Access shared task lists | ✅ granted |
| `Mail.Read` | Read Outlook/Hotmail inbox and folders | ✅ granted |
| `User.Read` | Authenticate user identity | ✅ granted |
| `Notes.Read` | Read OneNote notebooks, sections, pages | 🔧 Phase 2 — add via `--reauth` (T-1B.1.7) |
| `Calendars.Read` | Read Outlook Calendar events | ✅ granted — added Session 3, `--reauth` completed |
| `offline_access` | Refresh token — **reserved/automatic**, never pass explicitly to MSAL | ✅ auto |

**Scripts:**
| Script | Status | Purpose |
|---|---|---|
| `scripts/setup_msgraph_oauth.py` | ✅ live | One-time OAuth flow; `--health`/`--reauth` flags |
| `scripts/setup_todo_lists.py` | ✅ live | Creates 7 domain-tagged To Do lists idempotently; IDs in `config/artha_config.yaml` |
| `scripts/todo_sync.py` | ✅ live | Push `open_items.md` → To Do; pull completions back — Step 15 of catch-up |
| `scripts/msgraph_fetch.py` | ✅ live | Fetch Outlook inbox (mirrors `gmail_fetch.py` interface; `--since`, `--folder`, `--health`, `--dry-run`) |
| `scripts/msgraph_calendar_fetch.py` | ✅ live | Fetch Outlook Calendar events (mirrors `gcal_fetch.py`; `--from`, `--to`, `--today-plus-days`, `--list-calendars`, `--health`) |
| `scripts/msgraph_onenote_fetch.py` | 🔧 T-1B.1.7 | Fetch OneNote pages as plain text (requires `Notes.Read` scope via `--reauth`) |

**Email fetch API pattern (`msgraph_fetch.py`):**
```
GET /me/mailFolders/inbox/messages
  ?$filter=receivedDateTime ge {since_iso}
  &$select=id,subject,from,receivedDateTime,body,isRead
  &$top=200
  &$orderby=receivedDateTime desc
```
Output JSONL schema (matches `gmail_fetch.py` + `"source": "outlook"` field):
```json
{"id": "...", "thread_id": "...", "subject": "...", "from": "Name <addr>",
 "to": "...", "cc": "...", "date": "...", "date_iso": "...",
 "body": "...", "snippet": "...", "labels": ["inbox", "unread"],
 "source": "outlook"}
```

**Calendar fetch API pattern (`msgraph_calendar_fetch.py`):**
```
GET /me/calendars                          <- enumerate all Outlook calendars
GET /me/calendars/{id}/calendarView
  ?startDateTime={from}&endDateTime={to}
  &$select=id,subject,start,end,location,organizer,attendees
```
Output JSONL schema (matches `gcal_fetch.py` + `"source": "outlook_calendar"` field):
```json
{"id": "...", "calendar": "Calendar Name", "summary": "...",
 "start": "2026-03-07T15:30:00Z", "end": "2026-03-07T16:30:00Z",
 "all_day": false, "location": "...", "description": "...",
 "organizer": "Name <email>", "attendees": [{"email": "...", "name": "...", "self": false}],
 "status": "confirmed", "visibility": "default", "recurring": false,
 "is_online_meeting": false, "source": "outlook_calendar"}
```

All datetimes from `msgraph_calendar_fetch.py` are UTC (`Prefer: outlook.timezone="UTC"` header).

**Microsoft OneNote as Artha State Source (T-1B.1.7):**

Ved actively uses OneNote for planning, notes, and reference material that is highly relevant to the Artha state layer. Examples:
- Financial tracking notebooks (account details, budget plans, investment notes)
- Immigration checklists and correspondence logs
- Home / vehicle maintenance logs
- Kids’ school and activity notes
- Learning and goal-setting notebooks

OneNote content is accessible via the MS Graph `Notes.Read` scope:
```
GET /me/onenote/notebooks                         <- list all notebooks
GET /me/onenote/notebooks/{id}/sectionGroups      <- section groups
GET /me/onenote/notebooks/{id}/sections           <- sections
GET /me/onenote/sections/{id}/pages               <- page list
GET /me/onenote/pages/{id}/content                <- page HTML (strip to plain text)
```

Implementation (`scripts/msgraph_onenote_fetch.py`):
- `--notebook NAME` or `--section NAME` — filter to specific notebook/section
- `--modified-since TIMESTAMP` — fetch only pages modified after timestamp
- `--health` flag
- Output: JSONL with fields: `{notebook, section, page_title, modified, content_text, source: "onenote"}`
- Content: HTML → plain text strip (same `_HTMLStripper` pattern as `msgraph_fetch.py`)
- Integrated into catch-up Step 4 as an optional parallel fetch (P1 — content enrichment, not blocking)

**To enable:** add `Notes.Read` to `_SCOPES` in `setup_msgraph_oauth.py`, then run `--reauth`.

**Catch-up Step 4 parallel fetch pattern:**
```
Step 4 (all 4 in parallel):
  gmail_fetch.py --since {last_run}                          → JSONL (source: implicit gmail)
  gcal_fetch.py --from {today} --to {today+7d}               → JSONL (source: implicit google_calendar)
  msgraph_fetch.py --since {last_run}                        → JSONL (source: "outlook")
  msgraph_calendar_fetch.py --from {today} --to {today+7d}   → JSONL (source: "outlook_calendar")

Phase 2 (after T-1B.1.7):
  msgraph_onenote_fetch.py --modified-since {last_run}       → JSONL (source: "onenote")

Merge: all email JSONL feeds → unified email list (route by domain)
       all calendar JSONL feeds + WorkIQ events → unified event list (dedup by field-merge: summary from personal, Teams link from work, set source: "work+personal" for matches; unmatched work events tagged source: "work_calendar" with 💼 prefix. Merged events flagged merged=true — excluded from conflict detection.)
       onenote JSONL → state layer enrichment (domain routing by notebook name)
```

**Health check:** `python3 scripts/setup_msgraph_oauth.py --health`
**Preflight:** P1 checks in `preflight.py` — token valid + `msgraph_fetch.py --health` + `msgraph_calendar_fetch.py --health` + WorkIQ combined detection+auth (P1, non-blocking — see §3.2b)
**OneNote (Phase 2):** `python3 scripts/msgraph_onenote_fetch.py --health` — requires `Notes.Read` scope added via `--reauth`

### 3.9 Apple iCloud (Mail + Calendar)

**Purpose:** Direct-read of @icloud.com / @me.com email via IMAP, and iCloud-only Calendar events via CalDAV. Replaces the forwarding model originally specced as T-1B.1.2.

**Why IMAP/CalDAV (not OAuth REST):** Apple does not provide an OAuth 2.0 REST API for third-party programmatic access to iCloud Mail or Calendar. The supported protocols are the open internet standards IMAP (RFC 3501) and CalDAV (RFC 4791), authenticated with an app-specific password.

#### Auth Model

- Auth type: app-specific password (16-char `xxxx-xxxx-xxxx-xxxx` token)
- Generate at: `account.apple.com` → Sign-In and Security → App-Specific Passwords
- Static credential — does NOT expire unless manually revoked or Apple ID password changes
- Storage: macOS Keychain via `scripts/setup_icloud_auth.py`
  - Apple ID:       service `icloud-apple-id`, account `artha`
  - App password:   service `icloud-app-password`, account `artha`
- No refresh token needed; `ensure_valid_credentials()` reads from Keychain

#### Scripts

| Script | Status | Purpose |
|--------|--------|---------|
| `scripts/setup_icloud_auth.py` | 🔧 pending setup | Credential setup; `--setup`, `--reauth`, `--health` flags |
| `scripts/icloud_mail_fetch.py` | ✅ built | Fetch @icloud.com inbox via IMAP (mirrors `gmail_fetch.py` interface) |
| `scripts/icloud_calendar_fetch.py` | ✅ built | Fetch iCloud Calendar via CalDAV (mirrors `gcal_fetch.py` interface) |

**Setup (one-time, ~5 min):**
```bash
# 1. Generate app-specific password at account.apple.com
# 2. Store in Keychain
python3 scripts/setup_icloud_auth.py
# 3. Verify
python3 scripts/setup_icloud_auth.py --health
python3 scripts/icloud_mail_fetch.py --health
python3 scripts/icloud_calendar_fetch.py --health
```

**Email fetch (IMAP):**
```
Server: imap.mail.me.com:993 (SSL)
Fetch:  SEARCH SINCE {date} → UID FETCH RFC822 in chunks of 50
Filter: exact datetime in Python (IMAP SINCE is date-granular)
Output: {id, thread_id, subject, from, to, cc, date, date_iso, body, snippet,
         labels, source: "icloud"}
```

**Calendar fetch (CalDAV):**
```
Server: https://caldav.icloud.com (principal discovery automatic)
Lib:    caldav 3.0.1 (pip install caldav)
Fetch:  cal.search(start, end, event=True, expand=True) per calendar
Output: {id, calendar, summary, start, end, all_day, location, description,
         organizer, attendees, status, visibility, recurring,
         is_online_meeting, online_meeting_url, source: "icloud_calendar"}
```

**Catch-up Step 4 pattern (after setup):**
```bash
icloud_mail_fetch.py --since {last_run}               → JSONL (source: "icloud")
icloud_calendar_fetch.py --from {today} --to {today+7d} → JSONL (source: "icloud_calendar")
```

**Preflight:** P1 checks in `preflight.py` — `setup_icloud_auth.py --health` (gating) + `icloud_mail_fetch.py --health` + `icloud_calendar_fetch.py --health` (only if auth check passes)

---

### 4.1 Common Format

Every state file follows the same structure:

```markdown
---
domain: <domain_name>
last_updated: <ISO 8601 timestamp>
last_catch_up: <ISO 8601 timestamp>
last_email_processed_id: <Gmail message ID of last processed email for this domain>
last_activity: <ISO 8601 timestamp of last meaningful state change>  # v1.9 — tiered context loading
alert_level: <none|info|yellow|orange|red>
sensitivity: <standard|high|critical>
access_scope: <full|catch-up-only|terminal-only>
version: 1
---

## Current Status
<prose summary of current state>

## Active Alerts
<any threshold crossings, ordered by severity>

## Recent Activity
<timestamped log of recent changes, newest first>
```

**Sensitivity levels:**

| Level | Description | Examples |
|---|---|---|
| `standard` | No special handling. Flows to all output channels. | calendar, kids school events, shopping, social |
| `high` | Redacted in emailed briefings. Excluded from iPhone snapshots. | finance (balances, bills), health (appointments, Rx), insurance |
| `critical` | Redacted in emailed briefings. Excluded from iPhone snapshots. Extended redaction rules applied. | immigration (case numbers), tax data, estate (beneficiaries, POA), document repository |

**Access scope:**

| Scope | Description |
|---|---|
| `full` | State file content included in all output channels (subject to sensitivity filtering) |
| `catch-up-only` | State file read during catch-up workflow but never sent to external channels. Briefing shows summary line only. |
| `terminal-only` | State file content ONLY shown in Mac terminal. Never emailed, never in snapshots. For the most sensitive domains when document repository is connected. |

### 4.2 Immigration State (`~/OneDrive/Artha/state/immigration.md`)

```markdown
---
domain: immigration
last_updated: 2026-03-07T18:30:00-08:00
last_catch_up: 2026-03-07T18:30:00-08:00
alert_level: yellow
sensitivity: critical
access_scope: catch-up-only
version: 1
---

## Family Members

### Vedprakash (Primary)
- Status: H-1B
- Valid through: 2027-10-15
- Employer: Microsoft
- I-140: Approved (EB-2)
- Priority Date: 2019-04-15
- I-485 (AOS): Pending, filed 2024-01-20
- EAD: Valid through 2026-11-15
- AP (Advance Parole): Valid through 2026-11-15

### Archana (Dependent)
- Status: H-4
- Valid through: 2027-10-15
- H-4 EAD: Valid through 2026-11-15

### Parth (Dependent)
- Status: H-4
- DOB: [REDACTED]
- CSPA Age-Out Date: [calculated]
- CSPA Age: [calculated] (biological age minus I-140 pending time)

### Trisha (Dependent)
- Status: H-4
- DOB: [REDACTED]
- CSPA Age-Out Date: [calculated]

## Active Deadlines
- **🔴 EAD Renewal**: File by 2026-08-01 (current EAD expires 2026-11-15)
- **🟠 H-1B Extension**: File by 2027-04-15 (6 months before expiry)
- **🟡 Visa Bulletin Watch**: EB-2 India PD currently at 2019-01-01

## Attorney (Fragomen) Contact
- Last communication: [date]
- Next expected: [date]
- Open items: [list]

## Visa Bulletin History
| Month | EB-2 India PD | Movement |
|---|---|---|
| March 2026 | 2019-01-01 | +15 days |
| February 2026 | 2018-12-15 | +30 days |

## Recent Activity
- 2026-03-05: Email from Fragomen re: priority date advancement
- 2026-03-01: Visa Bulletin published — EB-2 India PD: 2019-01-01
```

### 4.3 Finance State (`~/OneDrive/Artha/state/finance.md`)

```markdown
---
domain: finance
last_updated: 2026-03-07T18:30:00-08:00
last_catch_up: 2026-03-07T18:30:00-08:00
alert_level: none
sensitivity: high
access_scope: catch-up-only
version: 1
---

## Accounts Overview
| Account | Type | Institution | Balance (last known) | Last Updated |
|---|---|---|---|---|
| Checking | Bank | Chase | $X,XXX | 2026-03-05 |
| Savings | Bank | Chase | $XX,XXX | 2026-03-01 |
| 401(k) | Retirement | Fidelity | $XXX,XXX | 2026-03-01 |
| Brokerage | Investment | Vanguard | $XX,XXX | 2026-03-01 |
| Mortgage | Loan | Wells Fargo | -$XXX,XXX | 2026-03-01 |

## Upcoming Bills
| Bill | Amount | Due Date | Status | Auto-Pay |
|---|---|---|---|---|
| Mortgage | $X,XXX | 2026-03-15 | Upcoming | Yes |
| PSE Energy | $XXX | 2026-03-20 | Upcoming | No |
| Water | $XX | 2026-03-25 | Upcoming | No |

## Monthly Budget Tracking
- Month-to-date spend: $X,XXX
- Budget target: $X,XXX
- Variance: +/- $XXX

## Active Alerts
(none)

## Recent Activity
- 2026-03-07: Chase statement email received
- 2026-03-05: PSE bill email: $XXX due 2026-03-20
```

### 4.4 Kids State (`~/OneDrive/Artha/state/kids.md`)

```markdown
---
domain: kids
last_updated: 2026-03-07T18:30:00-08:00
last_catch_up: 2026-03-07T18:30:00-08:00
alert_level: none
sensitivity: standard
access_scope: full
version: 1
---

## Parth (Grade 11 — Tesla STEM High School)
### Academics
| Course | Current Grade | Trend | Last Update |
|---|---|---|---|
| AP Language | B+ | Stable | 2026-03-05 |
| AP Physics | A- | ↑ Improving | 2026-03-04 |
| ... | ... | ... | ... |

- GPA (current semester): X.XX
- GPA (cumulative): X.XX
- SAT: [score or "not yet taken"]

### College Prep
- Status: 11th grade, application year starts Fall 2026
- SAT prep: [status]
- College list: [count] schools researched
- Next milestone: [e.g., "SAT registration by April 2026"]


#### College Application Countdown *(v2.1 — F4.11, P0)*
```yaml
countdown_active: true
application_year: "2026–2027"
milestones:
  - name: "SAT final attempt"
    date: "TBD"
    status: upcoming
    days_remaining: TBD
  - name: "College list finalized"
    date: "TBD"
    status: not_started
  - name: "Common App essays draft"
    date: "TBD"
    status: not_started
  - name: "Recommendation letters requested"
    date: "TBD"
    status: not_started
  - name: "Early application deadline"
    date: "TBD"
    status: not_started
  - name: "Regular decision deadline"
    date: "TBD"
    status: not_started
  - name: "FAFSA/CSS Profile submitted"
    date: "TBD"
    status: not_started
```
Auto-surfaced in daily briefing when any milestone is ≤90 days away. 🔴 alert when any milestone is ≤14 days away and status is not `completed`.
### Activities & Events
- [Club name]: Meets [schedule]
- [Event]: [date]

## Trisha (Grade 7 — Inglewood Middle School)
### Academics
| Course | Current Grade | Trend | Last Update |
|---|---|---|---|
| Math | A | Stable | 2026-03-06 |
| ... | ... | ... | ... |

### Activities & Events
- [Activity]: [schedule]

## Recent Activity
- 2026-03-07: ParentSquare: Spirit Week schedule for next week
- 2026-03-06: Canvas: Trisha Math quiz grade posted: 92%
- 2026-03-05: Canvas: Parth AP Language essay returned: B+
```

### 4.5 Health-Check State (`~/OneDrive/Artha/state/health-check.md`)

```markdown
---
domain: health-check
last_updated: 2026-03-07T18:30:00-08:00
version: 1
---

## Last Catch-Up
- Timestamp: 2026-03-07T18:30:00-08:00
- Duration: 2m 14s
- Emails processed: 47
- Calendar events fetched: 12
- State files updated: 5
- Alerts generated: 1 (🟠 EAD renewal reminder)
- Briefing emailed: Yes
- Errors: None

## MCP Tool Status
| Tool | Status | Last Successful |
|---|---|---|
| Gmail MCP | ✅ Connected | 2026-03-07T18:30:00-08:00 |
| Calendar MCP | ✅ Connected | 2026-03-07T18:30:00-08:00 |

## Run History (last 10)
| Date | Emails | Duration | Errors |
|---|---|---|---|
| 2026-03-07 | 47 | 2m 14s | 0 |
| 2026-03-06 | 62 | 2m 48s | 0 |
| 2026-03-04 | 89 | 3m 02s | 1 (Gmail timeout, retried) |
| ... | ... | ... | ... |

## Estimated Monthly API Cost
- Current month (March): ~$18.50
- Daily average: $2.64
- Projected month-end: ~$42

## Accuracy Pulse Data *(v1.9 — Workstream I)*
### This Catch-Up
- actions_proposed: 3
- actions_accepted: 2
- actions_declined: 0
- actions_deferred: 1
- corrections_logged: 0
- alerts_dismissed: 0

### Rolling 7-Day
- total_proposed: 18
- acceptance_rate: 78%
- corrections: 3
- domain_accuracy:
  immigration: 100%
  finance: 95%
  kids: 90%
  home: 100%

## Tiered Context Stats *(v1.9 — Workstream F)*
### This Catch-Up
- always_tier: ["health-check", "open_items", "memory", "goals"]
- active_tier: ["immigration", "finance", "kids"]  # had new data
- reference_tier: ["home", "calendar"]  # referenced but no new data
- archive_tier: ["travel", "shopping", "estate", "digital", "learning", "boundary", "insurance", "vehicle"]
- tokens_loaded: 21,400 (vs 36,000 if all loaded — 41% savings)

## Email Pre-Processing Stats *(v1.9 — Workstream E)*
- emails_received: 47
- marketing_suppressed: 12
- avg_tokens_per_email: 280 (post-processing)
- truncated_emails: 3 (exceeded 1500 token cap)
- batch_summarized: false (under 50 threshold)

## Signal:Noise Tracking *(v2.0 — Workstream N)*
### This Catch-Up
- total_items_ingested: 59  # emails + calendar events
- actionable_items: 6
- signal_ratio: 10.2%  # actionable / total
- alerts_generated: 1
- alerts_dismissed: 0
- false_positive_alerts: 0

### Rolling 30-Day
- avg_signal_ratio: 12.4%
- trend: stable  # improving | stable | degrading
- top_noise_sources: ["Amazon order updates", "LinkedIn notifications", "marketing newsletters"]
- noise_suppression_rate: 78%  # marketing_suppressed / total_marketing

## Context Window Pressure *(v2.0 — Workstream P)*
### This Catch-Up
- tokens_used: 112000
- tokens_available: 200000
- headroom_pct: 44%
- pressure_level: green  # green (>30%) | yellow (20-30%) | red (<20%)
- mitigation_applied: none  # none | tier_escalation | batch_split | domain_skip
- state_tokens: 21400
- email_tokens: 45000
- prompt_tokens: 12000
- reasoning_tokens: 33600

### Rolling 7-Day
- avg_tokens_used: 98000
- peak_tokens: 145000
- pressure_events: 0  # times headroom <20%
- batch_splits: 0  # times email batch was split due to pressure

## OAuth Token Health *(v2.0 — Workstream Q)*
| Provider | Token Status | Last Refresh | Refresh Failures (7d) | Expiry |
|---|---|---|---|---|
| Gmail | ✅ Valid | 2026-03-07T18:30:00 | 0 | N/A (refresh token) |
| Google Calendar | ✅ Valid | 2026-03-07T18:30:00 | 0 | N/A (refresh token) |
| MS Graph | ✅ Valid | 2026-03-07T18:30:00 | 0 | 2026-04-07 |
| iCloud | ⚠️ App-specific | N/A | 0 | N/A |
```

### 4.6 Audit Log (`~/OneDrive/Artha/state/audit.md`)

```markdown
---
domain: audit
last_updated: 2026-03-07T18:30:00-08:00
version: 1
---

## Action Log

### 2026-03-07
- 18:30 | CATCH-UP | Processed 47 emails, 12 calendar events
- 18:30 | ALERT | 🟠 EAD renewal reminder (90 days out)
- 18:31 | BRIEFING | Emailed to ved@gmail.com (sensitivity filter applied: 2 domains redacted)
- 18:31 | STATE_UPDATE | immigration.md — Visa Bulletin update
- 18:31 | STATE_UPDATE | finance.md — PSE bill added
- 18:31 | STATE_UPDATE | kids.md — Parth AP Language grade update

### 2026-03-06
- 19:15 | CATCH-UP | Processed 62 emails, 8 calendar events
- 19:15 | PROPOSED_ACTION | Send email to Fragomen re: EAD timeline → USER DECLINED
- 19:16 | BRIEFING | Emailed to ved@gmail.com (sensitivity filter applied: 2 domains redacted)

### Document Access Log (Phase 2+)
- 2026-03-05 | DOC_ACCESS | ~/Documents/Tax/2025-Federal-1040.pdf | Extracted: AGI, refund, filing date → state/finance.md
- 2026-03-05 | DOC_ACCESS | ~/Documents/Insurance/Auto-Policy-2026.pdf | Extracted: policy number, premium, coverage limits → state/insurance.md
- 2026-03-05 | DOC_SKIPPED | ~/Documents/Legal/Trust-Agreement.pdf | Reason: marked manual-entry-only in estate.md prompt
```

### 4.7 Memory State (`~/OneDrive/Artha/state/memory.md`)

```markdown
---
domain: memory
last_updated: 2026-03-07T18:30:00-08:00
version: 1
---

## Preferences
- Stop alerting about: ParentSquare Spirit Week reminders
- Bill alert timing: 5 days before due date (not 3)
- Morning briefing: Prefer concise (< 2 minutes reading time)

## Decisions
- 2026-02-15: Decided to refinance if mortgage rates drop below 5.5%
- 2026-02-10: Decided to track Parth's SAT prep as a formal milestone goal

## Corrections
- 2026-03-01: Correction — Parth's club meeting is biweekly, not weekly
- 2026-02-20: Correction — Archana's EAD expiry is Nov 15, not Nov 1

## Patterns Learned
- Ved typically does catch-up on Mon/Wed/Fri evenings + Sunday morning
- Immigration emails from Fragomen always warrant 🔴 Critical alert
- PSE bills are ~30% higher in Jan due to heating

## Behavioral Baselines *(v2.0 — Workstream M)*
### Catch-Up Patterns
- typical_days: [Mon, Wed, Fri, Sun]
- typical_times: ["18:00-20:00", "08:00-10:00"]  # weekday evenings, Sunday morning
- avg_gap_hours: 36
- longest_gap_hours: 72
- catch_up_duration_avg: "2m 30s"

### Communication Patterns
- avg_emails_per_day: 25
- peak_email_day: Monday
- avg_response_time_hours: 4  # for emails requiring action
- typical_first_action_domain: immigration  # what Ved checks first

### Financial Patterns
- avg_monthly_spend: 8500
- typical_bill_pay_timing: "3-5 days before due"
- auto_pay_coverage: 85%  # percentage of bills on auto-pay
- unusual_spend_threshold: 500  # derived from historical patterns

### Life Rhythm
- work_hours: "08:00-18:00"
- family_time: "18:00-21:00"
- weekend_pattern: "soccer/activities Saturday, planning Sunday"
- seasonal_patterns:
  - "Jan: high utility bills, tax prep starts"
  - "Mar-Apr: immigration renewal season"
  - "Aug-Sep: back to school"

## Coaching Preferences *(v2.0 — Workstream K)*
- coaching_style: direct  # direct | supportive | analytical
- accountability_level: moderate  # light | moderate | intensive
- goal_check_frequency: weekly  # daily | weekly | monthly
- preferred_nudge_format: question  # question | statement | metric
- obstacle_anticipation: true  # surface predicted blockers
- celebration_threshold: milestone  # every_win | milestone | major_only
- example_nudges:
  - "What's blocking the EAD renewal — waiting on documents or scheduling?"
  - "Savings rate dipped to 15% this month. Adjust budget or accept?"
  - "Parth's assignment completion dropped 10% — check in this week?"
```

---

### 4.8 Open Items State (`~/OneDrive/Artha/state/open_items.md`)

Persistent action-item bridge between catch-up sessions and Microsoft To Do (Phase 1B).  
Sensitivity: **standard** — included in full briefing; not redacted.

```markdown
---
domain: open_items
last_updated: 2026-03-08T07:00:00-08:00
sensitivity: standard
access_scope: full
---

## Open Items

- id: OI-001
  date_added: 2026-03-08
  source_domain: kids
  description: "Parth SAT 3/13 — arrange transport to Eastlake HS by 8:30am"
  deadline: 2026-03-13
  priority: P0
  status: open
  todo_id: ""

- id: OI-002
  date_added: 2026-03-07
  source_domain: immigration
  description: "Send signed I-485 checklist to Fragomen via DocuSign"
  deadline: 2026-03-15
  priority: P0
  status: open
  todo_id: "MSTo-abc123"    # populated after Phase 1B todo_sync.py push

## Resolved

- id: OI-000
  date_added: 2026-03-01
  source_domain: finance
  description: "Pay PSE bill ($247 due March 5)"
  deadline: 2026-03-05
  priority: P1
  status: done
  date_resolved: 2026-03-04
  todo_id: "MSTo-xyz789"
```

**Field reference:**

| Field | Type | Description |
|---|---|---|
| id | string | Sequential `OI-NNN`, never reused |
| date_added | ISO date | Date item was first extracted |
| source_domain | string | Domain that generated item (kids, immigration, finance, health, home, comms, general) |
| description | string | Human-readable action description |
| deadline | ISO date or "" | Hard deadline if known |
| priority | P0/P1/P2 | P0=must-do-today, P1=this-week, P2=someday |
| status | open/done/deferred | `done` set by todo_sync.py pull or manual edit |
| todo_id | string or "" | Microsoft To Do task ID; "" until pushed (Phase 1B) |
| date_resolved | ISO date | Set when status transitions to done |

### 4.9 Social State (`~/OneDrive/Artha/state/social.md`) *(v1.9 — Workstream A)*

Relationship graph model for FR-11 Relationship Intelligence.

```markdown
---
domain: social
last_updated: 2026-03-08T07:00:00-08:00
last_activity: 2026-03-08T07:00:00-08:00
last_catch_up: 2026-03-08T07:00:00-08:00
alert_level: none
sensitivity: standard
access_scope: full
version: 1
---

## Relationship Graph

### Close Family
- name: "Rahul Mishra"
  tier: close_family
  relationship: "brother"
  last_contact: 2026-02-28
  contact_frequency_target: 14  # days
  preferred_channel: whatsapp
  cultural_protocol: ["Rakhi (receiver)", "Diwali greetings (peer)"]
  timezone: "Asia/Kolkata"
  life_events:
    - event: "daughter born"
      date: 2025-08-15
      acknowledged: true

### Close Friends
- name: "Amit Patel"
  tier: close_friend
  last_contact: 2026-01-15
  contact_frequency_target: 30
  preferred_channel: email
  cultural_protocol: []
  group_membership: ["Microsoft colleagues"]

## Group Health
| Group | Members | Last Group Interaction | Upcoming |
|---|---|---|---|
| Temple community | 12 | 2026-02-20 | Holi celebration Mar 14 |
| Microsoft colleagues | 8 | 2026-03-01 | None scheduled |

## Communication Patterns
- outbound_this_month: 15
- inbound_this_month: 12
- reciprocity_alerts: ["Meera: 3 events attended, 0 reciprocated"]

## Reconnect Queue
- "Suresh Uncle — last contact 45 days ago (threshold: 30)"
- "College friend group — last interaction 90 days ago (threshold: 60)"
```

### 4.10 Decisions State (`~/OneDrive/Artha/state/decisions.md`) *(v1.9 — Workstream C)*

Cross-domain decision tracking for F15.24 Decision Graphs.

```markdown
---
domain: decisions
last_updated: 2026-03-08T07:00:00-08:00
last_activity: 2026-03-08T07:00:00-08:00
sensitivity: standard
access_scope: full
version: 1
---

## Active Decisions

- id: DEC-001
  date: 2026-02-15
  summary: "Decided to wait on refinance until rates drop below 5.5%"
  context: "Current rate 6.1%, market trending down. Break-even analysis shows 18-month payback at 5.5%."
  domains_affected: [finance, home]
  alternatives_considered:
    - "Refinance now at 5.8% — rejected (break-even too long)"
    - "ARM option — rejected (rate risk)"
  review_trigger: "Rates reach 5.5% OR 6 months elapsed"
  deadline: "2026-08-15"  # v2.1 — explicit decision deadline
  deadline_source: "self-set"  # self-set | external | auto-suggested
  status: active

## Resolved Decisions

- id: DEC-000
  date: 2026-01-10
  summary: "Selected Tesla STEM HS for Parth's senior year"
  domains_affected: [kids, finance]
  outcome: "Enrolled, no issues"
  status: resolved
```

### 4.11 Scenarios State (`~/OneDrive/Artha/state/scenarios.md`) *(v1.9 — Workstream D)*

What-if analysis for F15.25 Life Scenarios.

```markdown
---
domain: scenarios
last_updated: 2026-03-08T07:00:00-08:00
last_activity: 2026-03-08T07:00:00-08:00
sensitivity: standard
access_scope: full
version: 1
---

## Active Scenarios

- id: SCN-001
  created: 2026-03-01
  trigger: "I-485 approval timeline uncertainty"
  question: "What if I-485 is approved in 6 months vs. 18 months?"
  impacts:
    - domain: immigration
      if_6mo: "EAD/AP renewal unnecessary; H-1B extension unnecessary"
      if_18mo: "Must file EAD renewal (Aug 2026) and H-1B extension (Apr 2027)"
    - domain: finance
      if_6mo: "Save ~$5K in attorney fees; can change employers freely"
      if_18mo: "Budget $5K for renewals; employer lock continues"
    - domain: goals
      if_6mo: "Career flexibility goal achievable Q3 2026"
      if_18mo: "Career flexibility goal deferred to Q1 2028"
  last_evaluated: 2026-03-01
  status: active

## Templates
- refinance_analysis: "What if we refinance at X%?"
- college_cost: "What if Parth attends private university vs. in-state?"
- immigration_timeline: "What if I-485 is approved in N months?"
- job_change: "What if Ved changes employers?"
```

### 4.12 Dashboard State (`~/OneDrive/Artha/state/dashboard.md`) *(v2.0 — Workstream K)*

Life-at-a-glance snapshot updated at end of each catch-up. Powers the `/status` command and provides a single-file summary readable on any device.

```markdown
---
domain: dashboard
last_updated: 2026-03-07T18:30:00-08:00
last_catch_up: 2026-03-07T18:30:00-08:00
version: 1
---

## Life Pulse
| Domain | Status | Alert Level | Last Activity | Trend |
|---|---|---|---|---|
| Immigration | ⚠️ Action needed | 🟠 | 2026-03-07 | EAD renewal 90d |
| Finance | ✅ On track | 🟡 | 2026-03-07 | PSE bill due 13d |
| Kids | ✅ On track | none | 2026-03-07 | Stable |
| Health | ✅ On track | none | 2026-03-05 | Stable |
| Home | ✅ On track | none | 2026-03-06 | Stable |
| Goals | ⚠️ Mixed | 🟡 | 2026-03-07 | 4/5 on track |

## Active Alerts (ranked by URGENCY×IMPACT×AGENCY)
1. 🟠 EAD renewal — 90 days out, initiate Fragomen contact (score: 80)
2. 🟡 PSE bill $312 — due March 20, not on auto-pay (score: 60)
3. 🟡 Parth SAT March 13 — transport logistics needed (score: 50)

## Open Items Summary
- P0: 2 items (oldest: 3 days)
- P1: 4 items (oldest: 8 days)
- P2: 1 item (oldest: 15 days)
- Overdue: 0

## Life Scorecard *(v2.0 — Workstream S)*
| Dimension | Score | Trend | Key Metric |
|---|---|---|---|
| Financial Health | 7.5/10 | ↑ | Savings rate 18%, bills current |
| Immigration Progress | 6/10 | → | EAD renewal pending, no RFE |
| Kids & Education | 8/10 | → | GPA 3.7 (Parth), 3.9 (Trisha) |
| Physical Health | ?/10 | ? | No data — bootstrap needed |
| Social & Relationships | 6/10 | ↓ | 2 reconnect overdue |
| Career & Goals | 7/10 | ↑ | 4/5 goals on track |
| Home & Operations | 8/10 | → | No deferred maintenance |

## System Health
- Last catch-up: 2026-03-07T18:30:00 (23h ago)
- Signal:noise ratio: 10.2%
- Context pressure: green (44% headroom)
- OAuth tokens: all valid
- Artha accuracy (7d): 78% action acceptance
```

**Update rules:**
- Dashboard is rebuilt (not incrementally updated) at the end of each catch-up Step 8
- Life Pulse table is derived from each domain's state file frontmatter (`alert_level`, `last_activity`)
- Active Alerts are the top 5 from the current briefing, scored by ONE THING formula
- Life Scorecard is updated weekly (Sunday catch-up) using domain-specific scoring rubrics
- Dashboard is `sensitivity: standard` — safe to sync via OneDrive for iPhone access

---

### 4.13 Work Calendar State (`~/OneDrive/Artha/state/work-calendar.md`) *(v2.2 — PRD F8.8)*

Minimal metadata-only state file for work calendar. **No meeting titles, no attendee names, no bodies.** Only counts, durations, and timestamps.

```markdown
---
domain: work-calendar
last_updated: "2026-03-12T05:00:00-07:00"
last_activity: "2026-03-12T05:00:00-07:00"
sensitivity: standard
encrypted: false
schema_version: "1.0"
---
# Work Calendar Metadata

## Last Fetch
last_fetch: "2026-03-12T05:00:00-07:00"
platform: windows
workiq_version: "1.2.0"
events_returned: 13
total_minutes: 375
fetch_duration_seconds: 8

## Weekly Density (rolling — 13-week max)
# Updated each catch-up when WorkIQ is available.
# Counts AND minutes — no titles, no attendees, no bodies.
# Rolling 13-week window (one quarter). Entries older than 13 weeks pruned.
density:
  - week_of: "2026-03-09"
    mon: { count: 13, minutes: 375 }
    tue: { count: 8, minutes: 240 }
    wed: { count: 6, minutes: 210 }
    thu: { count: 9, minutes: 315 }
    fri: { count: 5, minutes: 150 }
    total_count: 41
    total_minutes: 1290
    conflicts_detected: 2
    avg_daily_minutes: 258
    max_focus_gap_minutes: 90

## Conflict History
# Count-only log. No event details persisted.
conflicts:
  - date: "2026-03-10"
    count: 2
    cross_domain: 1
    internal: 1
```

**Update rules:**
- Updated only when WorkIQ is available (Windows catch-ups). Mac catch-ups leave the file unchanged.
- Stale check: if `platform` in last fetch ≠ current platform AND last_fetch < 12 hours, briefing shows: "💼 [N] work meetings detected via Windows laptop (titles unavailable on this device)." If > 12 hours, ignore stale metadata entirely.

---

## 5. Briefing Format Specification

### 5.1 Catch-Up Briefing

```markdown
# Artha Catch-Up — March 7, 2026

**Last run**: March 6, 2026 at 7:15 PM | **Emails processed**: 47 | **Period**: 23 hours

---

## 🔴 Critical Alerts
(none — or items like "EAD renewal deadline in 28 days")

## 🟠 Urgent
- **[Immigration]** Visa Bulletin EB-2 India moved to 2019-01-15 — your PD (2019-04-15) is 3 months away
- **[Finance]** PSE bill: $247 due March 20 (not on auto-pay)

## 📅 Today's Calendar
- 9:00 AM — Team standup (work)
- 3:30 PM — Parth orthodontist appointment
- 6:00 PM — Trisha soccer practice pickup

## 📬 By Domain

### Immigration
- Fragomen email: H-1B extension paperwork timeline confirmed for Q2

### Kids
- Parth: AP Language essay returned (B+), cumulative grade now B+
- Trisha: Math quiz 92%, no action needed
- ParentSquare: Spring pictures March 12 (both schools)

### Finance
- Chase statement: February spending $X,XXX (within budget)
- Wells Fargo: Mortgage payment processed

### Home
- Republic Services: Schedule change for next week (holiday)

## 🤝 Relationship Pulse *(v1.9)*
- Reconnect needed: Suresh Uncle (45 days, threshold 30)
- Upcoming: Rahul’s birthday in 5 days → [action proposal queued]
- Cultural: Holi celebration Mar 14 (temple community)

## 🎯 Goal Pulse
| Goal | Status | Trend | Leading Indicator *(v1.9)* |
|---|---|---|---|
| Net worth trajectory | On track | ↑ +2.1% YTD | Savings rate 18% (target 20%) |
| Immigration readiness | ⚠️ Action needed | EAD renewal due | Fragomen response time: 3 days avg |
| Parth GPA | On track | Stable at 3.7 | Assignment completion 95% ✔ |

## 💡 ONE THING
Your EAD renewal is 90 days out. Based on Fragomen's average processing time
from your last two renewals (45 days), initiate attorney contact within 2 weeks
to stay on the safe side.

## 📅 Week Ahead *(v2.1 — Monday only)*
> Shown only on Monday catch-ups. Previews the week's calendar, upcoming deadlines,
> and goal milestones to enable proactive planning.

| Day | Key Events | Deadlines |
|---|---|---|
| Mon | Team standup, Parth ortho 3:30 PM | PSE bill due Wed |
| Tue | (clear) | |
| Wed | Trisha parent-teacher 4 PM | |
| Thu | Parth SAT prep class 6 PM | |
| Fri | Family dinner 7 PM | College app milestone: rec letters |
| Sat–Sun | Soccer tournament (Trisha) | |

**⚠️ This week:** 3 deadlines, 1 goal milestone. Consider scheduling bill payment today.

---

## 🛡️ PII Detection Stats *(v2.1)*
> PII Guard: 47 emails scanned | 3 tokens redacted (2 account numbers, 1 SSN fragment) | 0 false positives
> Coverage: 100% of emails pre-filtered | Last false positive: none

---

## ❓ Calibration Questions *(v2.1)*
> Post-briefing accuracy check. Answer 1–2 to help Artha improve.

1. Was the ONE THING above actually the most important item today? [Y/N/Other: ___]
2. Did I miss anything critical from your emails? [Y/N/What: ___]

---
*Artha catch-up complete. 47 emails → 6 actionable items. Next recommended catch-up: tomorrow evening.*
```

**Briefing sensitivity filter:** When the briefing is emailed (as opposed to displayed in terminal), domains with `sensitivity: high` or `critical` contribute **summary lines only**:

```markdown
## 📬 By Domain

### Immigration
✅ 1 item processed. No new alerts. (Details in terminal or next catch-up.)

### Finance
✅ 2 items processed. No new alerts. (Details in terminal or next catch-up.)

### Kids
- Parth: AP Language essay returned (B+), cumulative grade now B+
- Trisha: Math quiz 92%, no action needed
- ParentSquare: Spring pictures March 12 (both schools)
```

The terminal output during the Mac session shows full detail for all domains regardless of sensitivity. This ensures sensitive financial and immigration data is never in transit via email.

#### 5.1.1 Digest Mode Briefing Variant *(v1.9 — Workstream H)*

When >48 hours have elapsed since the last catch-up (detected in step 2b of §7.1), the briefing switches to digest format:

```markdown
# Artha Digest — March 7–9, 2026

**Last run**: March 5, 2026 at 7:00 PM | **Gap**: 48 hours | **Emails processed**: 142

---

## ⚠️ What You Missed (2 days)

### March 6 (Thursday)
- 🔴 **[Immigration]** Fragomen sent updated EAD timeline — review required
- 🟠 **[Finance]** PSE bill $312 due March 20
- 📦 3 Amazon deliveries confirmed

### March 7 (Friday)
- 🟡 **[Kids]** Parth: quiz grade posted (91%)
- 📅 Weekend calendar: soccer tournament Saturday 9am

## 📋 Consolidated Action Items
1. Review Fragomen EAD timeline email [P0]
2. Pay PSE bill by March 20 [P1]
3. Confirm soccer tournament logistics [P1]

## 🎯 Goal Pulse
[Same format as standard briefing]

## 💡 ONE THING
[Highest URGENCY×IMPACT×AGENCY item from the gap period]
```

**Design rules for digest mode:**
1. Group by day, not by domain — temporal ordering helps the user reconstruct what happened
2. Priority-tier filtering: only Critical and Urgent items get individual lines; Notable/FYI items are counted ("12 FYI items processed")
3. Action items are consolidated and deduplicated across the gap period
4. Standard briefing sections (Goal Pulse, ONE THING) appear after the gap summary

#### 5.1.2 Flash Briefing Variant *(v2.0 — Workstream O)*

When the user says "quick update" or time is constrained, the briefing compresses to ≤30 seconds reading time. See §7.13 for compression level selection logic.

```markdown
# Artha Flash — March 7, 2026

🔴 None
🟠 EAD renewal 90d out — contact Fragomen within 2 weeks
📅 3:30 PM Parth orthodontist | 6:00 PM Trisha soccer pickup
⚠️ IF YOU DON'T: Initiate EAD renewal → risk 45-day processing gap before expiry

4/5 goals on track | Signal ratio 10% | 47 emails → 6 actions
```

**Flash briefing rules:**
1. Maximum 8 lines including header
2. Only 🔴 Critical and 🟠 Urgent alerts — everything else suppressed
3. Calendar: today only, one line, pipe-separated
4. Consequence forecast: single "IF YOU DON'T" line for highest-priority item (see §7.10)
5. Footer: goal count + signal ratio + volume summary in one line
6. No domain sections, no Goal Pulse table, no Artha Observations
7. Full briefing details available via `/catch-up deep` or on next standard run

**Compression levels** (selected automatically or by user):
| Level | Trigger | Max Lines | Reading Time | Sections Included |
|---|---|---|---|---|
| Flash | "quick update", `/catch-up flash`, or <4h since last run | 8 | ≤30 sec | Alerts, today's calendar, consequence, footer |
| Standard | Default catch-up | ~40–60 | 2–3 min | Full §5.1 format |
| Deep | `/catch-up deep` or user requests "full analysis" | ~80–120 | 5–8 min | Standard + cross-domain analysis, trend charts, scenario updates |

### 5.2 Weekly Summary

Generated when the user runs a catch-up on Sunday, or during the first catch-up after Sunday 8 PM Pacific. If no catch-up occurs over the weekend, the weekly summary is prepended to Monday's first catch-up. Artha.md instructs: "If today is Monday and the last weekly summary was >8 days ago, generate the weekly summary as part of this catch-up."

Structure:

```markdown
# Artha Weekly Summary — Week of March 3–9, 2026

## Week in Numbers
- Emails processed: 312
- Catch-ups completed: 4
- Alerts generated: 3 (0 🔴, 2 🟠, 1 🟡)
- Goals on track: 4/5
- Actions proposed: 2 | Approved: 1 | Declined: 1

## Domain Summaries
[One paragraph per active domain with week's highlights]

## Goal Progress
[Full scorecard with week-over-week trends]

## Accuracy Pulse *(v1.9 — Workstream I)*
| Metric | This Week | Trend |
|---|---|---|
| Actions proposed | 8 | ↑ vs 5 last week |
| Actions accepted | 6 (75%) | ↓ vs 80% last week |
| Actions declined | 1 | Steady |
| Actions deferred | 1 | New |
| Corrections logged | 2 | ↓ vs 4 last week |
| Alerts dismissed | 0 | ✔ |
| Domain accuracy | Immigration 100%, Finance 95%, Kids 90% | Stable |

**Notable:** Finance accuracy dropped from 100% to 95% (PSE bill amount parsed as $312 instead of $312.47).

## Leading Indicator Alerts *(v1.9 — Workstream B)*
- ⚠ Parth assignment completion rate dropped 15% this week — GPA impact likely in 2–3 weeks
- ✔ Savings rate on track (18.2%, target 20%)

## Artha Observations
[3-5 cross-domain insights using extended thinking]

## Upcoming Week
[Key dates, deadlines, and anticipated items]
```

### 5.3 Monthly Retrospective *(v2.1 — F15.50)*

Generated during the first catch-up after the 1st of each month. Saved to `summaries/retro-YYYY-MM.md`.

```markdown
# Artha Monthly Retrospective — February 2026

## Month at a Glance
- Catch-ups completed: 22
- Total emails processed: 1,847
- Alerts: 12 (1 🔴, 4 🟠, 3 🟡, 4 🔵)
- Goals on track: 4/6 | Behind: 1 | New: 1

## What Happened
[Domain-by-domain narrative summary of the month's significant events]

## Decisions Made
[Auto-populated from decisions.md entries created/resolved this month]

## Goals Progress
| Goal | Start of Month | End of Month | Trend |
|---|---|---|---|
| Net worth | $XXX,XXX | $XXX,XXX | ↑ +X.X% |
| Parth GPA | 3.65 | 3.70 | ↑ |

## Artha Self-Assessment
- Accuracy: XX% (user-confirmed)
- Signal:noise: XX% average across domains
- Calibration adjustments applied: N
- PII incidents: 0

## Looking Ahead
[Key items for next month based on calendar, deadlines, and goal trajectories]
```

---

## 6. Domain Prompt Specification

### 6.1 Prompt File Structure

Each domain prompt file follows a consistent structure:

```markdown
# Domain: [Name]
# FR: [FR number]
# Priority: [P0/P1/P2]

## Purpose
[One-sentence description of this domain's scope]

## Extraction Rules
When processing emails for this domain, extract:
- [field 1]: [description and format]
- [field 2]: [description and format]
- ...

## Alert Thresholds
- 🔴 Critical: [condition]
- 🟠 Urgent: [condition]
- 🟡 Heads-up: [condition]
- 🔵 Info: [condition]

## State File Update Rules
When updating ~/OneDrive/Artha/state/[domain].md:
- [what to update and how]
- [what to archive and when]

## Briefing Contribution
Format for this domain's section in the catch-up briefing:
- [format specification]

## Leading Indicators *(v1.9 — Workstream B)*
For goals connected to this domain, extract these leading indicators:
- [indicator 1]: [description, how to extract, threshold for divergence alert]
- [indicator 2]: [description, how to extract, threshold for divergence alert]
Example: Finance domain extracts "savings rate trend" as leading indicator for net worth goal.

## Sensitivity & Redaction
- sensitivity: <standard|high|critical>
- access_scope: <full|catch-up-only|terminal-only>
- Redacted fields: [list of fields that must be redacted in state files]
- Document processing: <email-only|extract-and-discard|manual-entry-only>

## Known Senders
[List of email addresses/patterns that route to this domain]

## Deduplication Rules
Before creating a new entry in the state file:
- [How to detect if this item already exists — match on receipt number, bill ID, event date + source, etc.]
- [Default: update existing entry in-place rather than creating a duplicate]
- [How to handle repeated notifications for the same deadline/event — keep most recent metadata]
```

### 6.2 Example: Immigration Domain Prompt

```markdown
# Domain: Immigration
# FR: FR-2
# Priority: P0

## Purpose
Track immigration status, deadlines, and case progress for all four Mishra
family members (Vedprakash, Archana, Parth, Trisha).

## Extraction Rules
When processing immigration-related emails, extract:
- case_type: H-1B, I-140, I-485 (AOS), EAD, AP, H-4
- receipt_number: USCIS receipt number (format: XXX-XXX-XXXXX)
- deadline: Any deadline or expiry date mentioned
- action_required: What the recipient needs to do
- attorney_items: Action items from Fragomen
- priority_date: Any mention of EB-2 India priority date
- status_change: Any case status change (approved, denied, RFE, etc.)

## Alert Thresholds
- 🔴 Critical: Deadline < 30 days away | Status change (any) | RFE received
- 🟠 Urgent: Deadline < 90 days away | Visa Bulletin movement within 6 months of PD
- 🟡 Heads-up: Deadline < 180 days away | Attorney correspondence received
- 🔵 Info: Visa Bulletin published | General USCIS policy updates

## CSPA Age-Out Monitoring
For Parth and Trisha:
- Calculate CSPA age = biological age - (time I-140 was pending)
- Alert at 36, 24, 12, and 6 months before projected age-out (21st birthday adjusted)
- If CSPA protection insufficient: trigger F-1 student visa planning alert
- This is the highest-stakes derived deadline in Artha

## State File Update Rules
When updating ~/OneDrive/Artha/state/immigration.md:
- Update the relevant family member's section
- Add to Active Deadlines if new deadline found
- Add to Recent Activity log with timestamp
- Update Visa Bulletin History table when new bulletin processed
- Update alert_level in frontmatter based on highest active alert
- Update `last_email_processed_id` in frontmatter with the Gmail message ID of the last processed immigration email

## Extraction Verification
- When extracting dates (deadlines, expiry): verify the date appears literally in the email text. If the date is inferred rather than stated, append `[VERIFY]` to the entry.
- When extracting receipt numbers: verify format matches `XXX-XXX-XXXXX`. If confidence < 95%, append `[VERIFY]`.
- When attributing case updates to a family member: verify the name or specific case detail appears in the email. If ambiguous, flag: "Unclear whether this applies to [member A] or [member B] — [VERIFY]."

## Briefing Contribution
In the catch-up briefing, immigration contributes:
- Any 🔴/🟠 alerts go to the top-level alert sections
- Sub-section under "By Domain" with:
  - Status changes
  - Attorney correspondence summary
  - Upcoming deadlines (next 90 days)
  - Visa Bulletin movement (if published this period)

## Known Senders
- *@fragomen.com → Always 🟡+ (attorney correspondence)
- *@uscis.gov → Always 🟠+ (government communication)
- *@travel.state.gov → 🟡 (Visa Bulletin)
- Subject: "visa bulletin" → 🟡 (monthly bulletin)

## Deduplication Rules
- Before adding a new deadline: check Active Deadlines for existing entry with same receipt number or event type + date
- Follow-up emails from Fragomen re: same case: UPDATE the existing case entry, do not create a new one
- Visa Bulletin updates: replace previous month's row in History table, do not append if already processed
- USCIS status update for existing case: update case status in-place, log previous status to Recent Activity

## Visa Bulletin Parsing (EB-2 India)
When processing a Visa Bulletin email:
- Extract EB-2 India final action date and compare to family priority date stored in state
- Calculate estimated months-to-current based on trailing 6-month average movement
- Compare with prior month to detect direction: forward movement, retrogression, or unchanged
- Trigger 🟠 alert if projected current within 24 months
- Update Visa Bulletin History table: month, EB-2 India date, movement delta, projected months-to-current
```

### 6.3 Example: Finance Domain Prompt

```markdown
# Domain: Finance
# FR: FR-3
# Priority: P0

## Purpose
Track bills, spending, account balances, and financial health for the
Mishra household.

## Extraction Rules
When processing finance-related emails, extract:
- bill_type: utility, mortgage, credit card, insurance, subscription
- amount: Dollar amount due
- due_date: Payment due date
- account: Which account/provider
- auto_pay: Whether auto-pay is enabled
- statement_period: Statement date range
- balance: Account balance if mentioned
- unusual_flag: True if amount is >20% different from typical

## Alert Thresholds
- 🔴 Critical: Bill overdue | Fraud alert | Credit score drop > 20 points
- 🟠 Urgent: Bill due within 3 days (non-auto-pay) | Unusual spend > $500
- 🟡 Heads-up: Bill due within 7 days | Monthly spend above budget
- 🔵 Info: Statement received | Balance update

## State File Update Rules
When updating ~/OneDrive/Artha/state/finance.md:
- Update Accounts Overview table with latest balance
- Add/update Upcoming Bills table
- Update Monthly Budget Tracking section
- Add to Recent Activity log
- Flag bills not on auto-pay for special attention
- Update `last_email_processed_id` in frontmatter

## Extraction Verification
- When extracting bill amounts: verify the dollar amount matches the regex `\$[\d,]+\.\d{2}` in the email text. If ambiguous (multiple amounts, unclear which is the total), append `[VERIFY]`.
- When extracting due dates: verify the date appears explicitly. If inferred from "due in X days," calculate and note the inference: "Due 2026-03-20 (inferred from 'due in 3 days' as of email date)."

## Briefing Contribution
- Any 🔴/🟠 alerts go to top-level alert sections
- Sub-section with: upcoming bills (next 7 days), spending pulse, notable transactions

## Known Senders
- *@chase.com, *@fidelity.com, *@wellsfargo.com, *@vanguard.com
- *@pse.com, *@sammamish*
- *@equifax.com → 🟠 (credit monitoring)
- Subject: "statement", "bill", "payment due", "payment received"

## Deduplication Rules
- Before adding a bill entry: check Upcoming Bills for existing entry with same provider + billing period
- Payment confirmation following a bill notification: update existing entry status to "paid", do not create separate entry
- Statement emails for same account + same period: keep only the most recent version
- Recurring bills (auto-pay): confirm amount matches expected; if same amount and period, skip state update

## Cross-Domain Triggers
- Travel booking, flight confirmation, or hotel receipt detected → flag for cross-referencing with credit card benefits (F3.12). Surface in briefing: "This booking may qualify for [card name] [specific perk — lounge access, travel credit, trip insurance]."
- Large expense detected (>$1,000) → check against Monthly Budget Tracking and current cash flow
```

---

## 7. Catch-Up Workflow — Detailed Sequence

### 7.1 Step-by-Step Flow

```
User: "catch me up" (or /catch-up)
  │
  ▼
┌─────────────────────────────────────────┐
│ 0. ⭐ PRE-FLIGHT GO/NO-GO GATE         │
│    Run before touching any data:        │
│    a. OAuth tokens present:             │
│       ~/.artha-tokens/gmail-oauth-      │
│       token.json + gcal-oauth-token.json│
│    b. gmail_fetch.py --health exits 0   │
│    c. gcal_fetch.py --health exits 0    │
│    d. Lock file absent OR stale (>30m   │
│       old → auto-clear with warning)    │
│    e. Vault operational (.age file      │
│       present)                          │
│    f. ⭐ WorkIQ combined check (v2.2): │
│       Single npx call → detection+auth  │
│       P1 NON-BLOCKING (never halts).    │
│       Sets workiq_available +           │
│       workiq_auth_valid flags.           │
│       Uses 24h cache (tmp/).            │
│    ⚠ HALT on any P0 failure (a–e):    │
│    "⛔ Pre-flight failed: [check] —    │
│    [error]". Log to health-check.md.    │
│    Prevents silent-omission briefings.  │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 0b. PULL TODO COMPLETION STATUS         │
│    (Phase 1B — after T-1B.6.4)         │
│    python3 scripts/todo_sync.py --pull  │
│    For each open_items.md entry with    │
│    todo_id != "": check Graph API for   │
│    completion. Mark done if completed.  │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 1. DECRYPT SENSITIVE STATE              │
│    vault.sh decrypt                     │
│    (automated via Claude Code hook      │
│     or explicit CLAUDE.md instruction)  │
│                                         │
│    ⭐ DATA INTEGRITY GUARD (v2.0):     │
│    a. PRE-DECRYPT BACKUP: Before        │
│       overwriting .md with decrypted    │
│       .age, check if .md already exists │
│       and has content beyond bootstrap. │
│       If yes: backup to .md.bak before  │
│       decrypt overwrites.               │
│    b. POST-DECRYPT VERIFY: After        │
│       decrypt, verify .md is non-empty  │
│       and has valid YAML frontmatter.   │
│       If empty/corrupt: restore .md.bak │
│       and HALT with error.              │
│    See §8.5 for full three-layer spec. │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 1. READ HEALTH-CHECK                    │
│    Read ~/OneDrive/Artha/state/         │
│    health-check.md                      │
│    Extract: last_catch_up timestamp     │
│    Calculate: hours since last run      │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 1b. ⭐ DIGEST MODE CHECK (v1.9)        │
│    If hours_since_last_run > 48:        │
│      digest_mode = true                 │
│      Log: "Digest mode: {hours}h gap"   │
│    Effect on downstream steps:          │
│    - Step 6: priority-tier grouping     │
│    - Step 7: use §5.1.1 digest format   │
│    (Workstream H)                       │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 2. FETCH EMAILS + CALENDAR (PARALLEL)  │
│    ┌──────────────┐ ┌────────────────┐  │
│    │ Gmail MCP    │ │ Calendar MCP   │  │
│    │ gmail_search │ │ list_events    │  │
│    │ (after:last) │ │ (today, +7d)   │  │
│    └──────┬───────┘ └───────┬────────┘  │
│           └────────┬────────┘           │
│                    ▼                    │
│    Emails + Events ready                │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 3. ⭐ PII PRE-FLIGHT FILTER            │
│    pii_guard.sh filter < email_batch    │
│    Scan for: SSN, CC, routing numbers,  │
│    account numbers, passport, A-number, │
│    ITIN, driver's license               │
│    Replace with [PII-FILTERED-*] tokens │
│    Log detections to audit.md           │
│    ⚠ HALT if pii_guard.sh fails        │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 3b. EMAIL CONTENT PRE-PROCESSING       │
│    (v1.9 enhanced — Workstream E)       │
│    For each email in the filtered batch:│
│    a. Strip HTML tags, retain text only │
│    b. Remove email footers/disclaimers  │
│       (unsubscribe blocks, legal text,  │
│        confidentiality notices)         │
│    c. ⭐ Marketing suppression (v1.9): │
│       - Match sender against marketing  │
│         sender list (newsletters, promo)│
│       - Unrecognized marketing: extract │
│         subject line only, skip body    │
│       - Log suppression to health-check │
│    d. For threads: keep latest reply +  │
│       original message; collapse        │
│       intermediate quoted replies       │
│    e. ⭐ Per-email token cap: 1,500    │
│       tokens hard limit (v1.9).         │
│       Truncate with "[truncated]" tag.  │
│    f. ⭐ Batch summarization (v1.9):   │
│       If >50 emails in batch, group     │
│       by sender pattern and summarize   │
│       low-priority clusters (e.g.,      │
│       "12 Amazon order updates")         │
│    Purpose: Prevent context bloat from  │
│    marketing HTML and thread repetition │
│    ⚡ Steps a-d: Claude inline. Steps   │
│    e-f: Claude inline unless >20% of    │
│    catch-ups hit context limits (§9.2)  │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 4. READ ALL STATE FILES                 │
│    Read ~/OneDrive/Artha/state/*.md     │
│    Load current world model into        │
│    context for delta detection          │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 4.2 ⭐ PARALLEL SKILL PULL (v4.0)       │
│    Launch scripts/skill_runner.py       │
│    a. Discover enabled skills from      │
│       config/skills.yaml                │
│    b. Execute USCIS, Tax, etc.          │
│    c. Write to tmp/skills_cache.json    │
│    d. Enforce P0 halt vs P1 warn logic  │
│    e. ⭐ WorkIQ Calendar (v2.2):       │
│       IF workiq_available AND            │
│          workiq_auth_valid:              │
│       - Fetch via pinned npx            │
│         (explicit date-range query)     │
│       - Parse pipe-delimited response   │
│       - Apply partial redaction from    │
│         config/settings.md              │
│       - Merge into unified event list   │
│         (field-merge dedup per §3.2b)   │
│       - Save raw to tmp/work_calendar   │
│         .json (deleted at Step 9)       │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 4b. ⭐ TIERED CONTEXT LOADING (v1.9)   │
│    (Workstream F)                       │
│    Classify state files into tiers:     │
│    ┌─────────────────────────────────┐  │
│    │ ALWAYS: health-check, open_items│  │
│    │   memory, goals — always full   │  │
│    │ ACTIVE: domains with new data   │  │
│    │   in this batch — full load     │  │
│    │ REFERENCE: domains referenced   │  │
│    │   but no new data — frontmatter │  │
│    │   + alerts only                 │  │
│    │ ARCHIVE: domains dormant >30d   │  │
│    │   — skip entirely               │  │
│    └─────────────────────────────────┘  │
│    Uses last_activity timestamp from    │
│    common format (§4.1) + incoming      │
│    email routing matches.               │
│    Target: 30–40% token savings.        │
│    Log tier assignments + tokens saved  │
│    to health-check.md §Tiered Context.  │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 5. ROUTE + PROCESS (parallel per domain)│
│    For each email/event:                │
│    a. Match to domain via routing rules │
│    b. Read domain prompt file           │
│    c. Apply extraction rules            │
│    d. Apply §8.2 redaction (Layer 2)    │
│    e. Update domain state file          │
│    f. Evaluate alert thresholds         │
│    g. Collect briefing contribution     │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 5b. UPDATE OPEN ITEMS                   │
│    Read state/open_items.md             │
│    For each actionable item extracted:  │
│    a. Fuzzy-match vs existing entries   │
│       (description + deadline)          │
│    b. Add if new: id, date_added,       │
│       source_domain, description,       │
│       deadline, priority, status:open,  │
│       todo_id: ""                       │
│    c. Re-surface overdue items          │
│       (deadline < today AND             │
│        status: open) → 🔴 OVERDUE      │
│    Purpose: persistent action tracking  │
│    across sessions (§4.7)              │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 6. CROSS-DOMAIN REASONING              │
│    Check for patterns spanning domains: │
│    - Immigration deadline + travel plan │
│    - Bill due + cash flow timing        │
│    - School event + work calendar       │
│    ⭐ WorkIQ conflict rules (v2.2):    │
│    Rule 7a: Cross-domain (work↔personal)│
│      Impact=3 (lifestyle trade-off)     │
│    Rule 7b: Internal (work↔work)        │
│      Impact=1 (self-resolvable)         │
│    Rule 8: Duration-based load          │
│      >300 min → Heavy load              │
│      <60 min gap → Context switch warn  │
│    Dedup-excludes-conflict: merged      │
│    events cannot self-conflict.         │
│    ⭐ ONE THING scoring (v1.9 — G):    │
│    Score = URGENCY × IMPACT × AGENCY    │
│    - URGENCY: time pressure (0–5)       │
│    - IMPACT: consequence of inaction    │
│      (0–5)                              │
│    - AGENCY: can Mishra family act on   │
│      it today? (0–5)                    │
│    Show reasoning chain in briefing:    │
│    "Chosen because: URGENCY 5 (due      │
│     tomorrow) × IMPACT 4 (legal) ×      │
│     AGENCY 5 (action clear) = 100"      │
│    If digest_mode: pick top item from   │
│    entire gap period, not just today.   │
│    ⭐ Decision graph check (v1.9 — C): │
│    If cross-domain reasoning reveals    │
│    a decision point, auto-create entry  │
│    in state/decisions.md.               │
│    ⭐ Scenario trigger (v1.9 — D):     │
│    If a high-stakes goal has new data,  │
│    check if a scenario template applies │
│    and offer to run what-if analysis.   │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 7. SYNTHESIZE BRIEFING                  │
│    Assemble briefing per format spec    │
│    (Section 5.1)                        │
│    Include Week Ahead on Mondays (§5.1) │
│    Include PII detection footer (§5.1)  │
│    Display in terminal                  │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 7b. ⭐ CALIBRATION QUESTIONS (v2.1)     │
│    After displaying briefing,           │
│    present 1–2 calibration questions:   │
│    a. "Was the ONE THING the most       │
│       important item today? [Y/N]"      │
│    b. "Did I miss anything critical     │
│       from your emails? [Y/N/What]"    │
│    Store responses in health-check.md   │
│    under calibration_log section.       │
│    Use responses to adjust:             │
│    - ONE THING scoring weights          │
│    - Domain routing accuracy            │
│    - Alert threshold sensitivity        │
│    Skip if user says "skip" or doesn't  │
│    respond within the session.          │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 7c. ⭐ DECISION DEADLINE CHECK (v2.1)   │
│    Scan decisions.md for entries with   │
│    deadline field:                      │
│    a. Expired deadlines → auto-escalate │
│       to 🔴 alert in next briefing      │
│    b. Deadlines ≤14 days → surface     │
│       nudge in briefing                │
│    c. No deadline + open >14 days →    │
│       nudge: "Set deadline or resolve" │
│    Update decisions.md status field     │
│    as needed.                          │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 8. DELIVER + ARCHIVE                    │
│    a. Email briefing to configured addr │
│    b. Save to ~/OneDrive/Artha/         │
│       briefings/YYYY-MM-DD.md           │
│    c. Update health-check.md            │
│    d. Log to audit.md                   │
│    e. Log PII filter stats to audit.md  │
│    f. (Phase 1B) Run todo_sync.py:      │
│       Push open_items (status:open,     │
│       todo_id:"") → To Do lists;        │
│       Pull completions back to          │
│       open_items.md (status:done)       │
│    g. ⭐ Update dashboard.md (v2.0):   │
│       Rebuild life pulse, active alerts,│
│       open items summary, system health │
│       from current state. See §4.12.    │
│    h. ⭐ Update work-calendar.md (v2.2):│
│       Write count+duration metadata     │
│       only. No titles/attendees. Prune  │
│       density entries >13 weeks old.    │
│    i. ⭐ Meeting-triggered OIs (v2.2): │
│       If critical meeting detected      │
│       (Interview, Perf Review, etc.)    │
│       AND future-dated → auto-create    │
│       Employment domain OI.             │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 8b. ⭐ NET-NEGATIVE WRITE GUARD (v2.0) │
│    Before finalizing state file writes: │
│    For each modified state file:        │
│    a. Count non-empty fields BEFORE     │
│    b. Count non-empty fields AFTER      │
│    c. If AFTER < BEFORE by >20%:        │
│       ⛔ HALT write for that file.      │
│       Display diff to user.             │
│       "State file [domain] would lose   │
│        [N] fields. Proceed? [Y/N]"      │
│    d. If user confirms: proceed.        │
│    e. If user denies: skip write,       │
│       log to audit.md as WRITE_BLOCKED. │
│    Purpose: prevents data loss from     │
│    accidental overwrites or stale       │
│    decryption. See §8.5 Layer 3.        │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 9. ENCRYPT SENSITIVE STATE              │
│    a. ⭐ Delete tmp/work_calendar.json │
│       (v2.2 — corporate data cleanup   │
│       BEFORE encrypt. Critical.)        │
│    b. vault.sh encrypt                  │
│    (automated via Claude Code Stop hook │
│     or explicit CLAUDE.md instruction)  │
└─────────────────────────────────────────┘
```

### 7.2 Error Handling

| Failure | Behavior |
|---|---|
| Gmail MCP connection fails | Report error, proceed with calendar-only briefing. Note stale email data. |
| Calendar MCP connection fails | Report error, proceed with email-only briefing. Note missing calendar. |
| Both MCP tools fail | Report error, offer to do on-demand chat using existing state files. |
| Single email parse fails | Skip email, log to audit.md, continue with remaining emails. |
| State file write fails | Report error, display briefing in terminal only. |
| Email delivery fails | Display full briefing in terminal. Offer to retry or copy to clipboard. |
| Context window approaching limit | Process highest-priority emails first, summarize remainder. |
| PII filter fails (pii_guard.sh non-zero) | HALT catch-up immediately. Do NOT process unfiltered emails. Report error to terminal. |
| Pre-flight gate failure | HALT before any data is fetched. Display `⛔ Pre-flight failed: [check name] — [error]`. Log failed check to health-check.md. Never proceed with partial integration. |
| OAuth access token expired, refresh fails | Surface auth error immediately. Never return silent 0-result fetch. Log token refresh failure to health-check.md. Prompt user to re-run setup_google_oauth.py. |
| API quota exceeded (HTTP 429/503, retries exhausted) | HALT catch-up. Display `⛔ Gmail API quota exceeded — partial data, aborting`. Never deliver briefing with silently partial data. Log to health-check.md. |
| Stale lock file detected (age >30 min) | Auto-clear with `⚠ Stale lock detected (age: Xm) — clearing`. Log to health-check.md. Continue normally. |
| Fresh lock file detected (age <30 min) | HALT with `⛔ Active session detected. Run 'rm /tmp/artha.lock' if this is an error`. |
| Microsoft To Do sync failure (Phase 1B) | Log warning to audit.md. Catch-up continues — To Do sync is non-blocking. Items remain in open_items.md with todo_id: "" for retry on next run. |
| Pre-decrypt backup failure | HALT decrypt. Display `⛔ Cannot create backup of [file].md before decrypt — disk full or permissions error`. Do not overwrite existing state with decrypted .age content. |
| Net-negative write detected | HALT write for affected file. Display diff showing field loss count. Require explicit user confirmation to proceed. Log decision to audit.md as `WRITE_GUARD_TRIGGERED`. |
| Decrypt produces empty/corrupt file | Restore from .md.bak. Display `⛔ Decrypt of [file].md.age produced invalid output — restored from backup`. Log to audit.md as `DECRYPT_CORRUPT_RESTORE`. |
| Bootstrap state file detected during catch-up | Surface `⚠️ [domain] state file has never been populated (still shows bootstrap template). Run /bootstrap to populate.` Do not attempt to update bootstrap-template files with extracted data — risk of partial state. |
| WorkIQ fetch failure (Windows) *(v2.2)* | Log to audit.md. Catch-up continues — WorkIQ is P1 non-blocking. Briefing shows personal calendar only with footer: "⚠️ Work calendar unavailable — [reason]". |
| WorkIQ auth expired *(v2.2)* | Set `workiq_auth_valid=false`. Skip work calendar fetch. Briefing footer: "⚠️ WorkIQ auth expired — run: npx workiq logout && retry". |
| WorkIQ parse returns 0 events from non-empty response *(v2.2)* | Log warning: "WorkIQ returned text but 0 events parsed — format may have changed." Retry once with more explicit prompt. If still fails, skip with footer note. |
| WorkIQ unavailable on Mac *(v2.2)* | Silent. No error, no warning. Footer only if stale work-calendar.md exists (<12h): "💼 [N] work meetings detected via Windows (titles unavailable on this device)". |

**Context window monitoring:** After each catch-up, log actual token usage to health-check.md:
```yaml
context_window:
  last_catch_up_tokens: 112000
  avg_tokens_30d: 98000
  peak_tokens_30d: 145000
  headroom_pct: 27%
```
If `headroom_pct` drops below 20%, Artha surfaces a 🟡 alert: "Context window headroom low. Consider reducing email batch size or increasing catch-up frequency." If email batch exceeds 80 messages, process in two passes: high-priority domains first (P0: immigration, finance, kids), then remainder.

### 7.3 On-Demand Chat (Between Catch-Ups)

When the user asks a question outside of a catch-up:

1. Claude reads the relevant state file(s)
2. Answers from state data — no email fetch
3. If the user asks about something not in state: offer to run a targeted email search via Gmail MCP
4. State files are NOT updated during on-demand chat (only during catch-up)

**iPhone access via Claude.ai Project:**
- User manually uploads state file snapshots to a Claude.ai Project named "Artha"
- Or: catch-up workflow copies key state files to a shared location for Project upload
- iPhone queries are read-only, answered from cached state
- Staleness = time since last state snapshot upload

### 7.4 Action Execution Framework

Artha's data ingestion is well-defined (§7.1 catch-up workflow). This section defines the **action execution** side — how Artha takes actions in the world beyond reading data and generating briefings.

#### 7.4.1 Action Proposal Schema

Every action Artha proposes follows a structured format displayed in the terminal:

```markdown
───────────────────────────────────────────
📋 ACTION PROPOSAL #[sequence]
───────────────────────────────────────────
Type:           [action_type from catalog]
Recipient:      [who receives the action]
Channel:        [email | whatsapp | calendar | filesystem]
Content Preview:
  [Full preview of the message/event/action]
Trust Required: [Level 0 | Level 1 | Level 2]
Current Trust:  [Level N]
Sensitivity:    [standard | high | critical]
Friction:       [low | standard | high]        # v1.9 — Workstream #10
───────────────────────────────────────────
[✅ Approve] [✏️ Modify] [❌ Reject]
───────────────────────────────────────────
```

**Processing rules:**
- At Trust Level 0: All actions are queued as recommendations only (no execute option)
- At Trust Level 1: Actions are proposed with approve/modify/reject options
- At Trust Level 2: Pre-approved action types execute automatically with post-hoc notification
- All actions — approved, modified, or rejected — are logged to `audit.md`

**Friction classification** *(v1.9 — Workstream #10):*
| Friction | Criteria | Approval behavior |
|---|---|---|
| `low` | Calendar adds, email archiving, visual generation | Batch-approvable ("✅ Approve all low-friction") |
| `standard` | General email composition, WhatsApp greetings, reminders | Individual review, approve/modify/reject |
| `high` | Financial actions, immigration correspondence, actions affecting others | Individual review required regardless of trust level; cannot be pre-approved at Level 2 |

#### 7.4.2 Action Catalog

| Action Type | Channel | Trust Level | Human Gate | Notes |
|---|---|---|---|---|
| **Send briefing email** | Gmail MCP (`gmail.send`) | Level 0+ | Auto (catch-up workflow) | Existing — briefing delivery |
| **Compose & send email** | Gmail MCP (`gmail.send`) | Level 1+ | Always (Autonomy Floor) | General email composition |
| **Send group email** | Gmail MCP (`gmail.send`) | Level 1+ | Always (Autonomy Floor) | Festival/occasion greetings to contact list |
| **WhatsApp message** | URL scheme (`open`) | Level 1+ | Always (OS-enforced) | Opens WhatsApp with pre-filled message; user taps send |
| **Create calendar event** | Google Calendar MCP | Level 1+ | Approve | Requires calendar write scope |
| **Generate visual** | Gemini Imagen CLI | Level 0+ | None (generation only) | Saved to `~/OneDrive/Artha/visuals/` |
| **Archive email** | Gmail MCP | Level 2 | Pre-approved | Auto-archive processed newsletters |
| **Update state file** | Filesystem | Level 0+ | Auto (catch-up workflow) | Existing — state file writes |
| **Draft attorney email** | Gmail MCP (`gmail.send`) | Level 1+ | Always (Autonomy Floor) | Immigration correspondence |

**Autonomy Floor (never auto-executed regardless of trust level):**
- Any communication sent on your behalf (email, WhatsApp)
- Any financial transaction
- Any immigration-related action
- Any action affecting another person's data

#### 7.4.3 Email Composition

Expands Gmail MCP usage from briefing-only delivery to general-purpose email composition.

**Scope expansion required:** Gmail MCP OAuth scope must include `gmail.send` (not just `gmail.readonly`). This is a Phase 1A setup task.

**Email composition workflow:**
1. **Trigger:** User requests ("send Archana's parents a Diwali greeting") OR Artha recommends ("Attorney email draft ready for your review")
2. **Draft:** Claude composes email with subject, body, recipients, and optional attachment references
3. **Preview:** Full email displayed in terminal within Action Proposal
4. **Approve:** User approves, modifies, or rejects
5. **Send:** Gmail MCP `gmail.send` executes
6. **Log:** Sent email logged to `audit.md` with timestamp, recipients, subject

**Group email pattern (festivals/occasions):**
```markdown
## Contact Groups (in ~/OneDrive/Artha/config/contacts.md)
### Diwali Greetings
- family_india: [list of email addresses]
- friends_us: [list of email addresses]
- colleagues: [list of email addresses]

### Birthday Reminders
- [name]: [email] | [phone] | [birthday date]
```

Claude reads `contacts.md`, composes personalized messages per recipient/group, and proposes each as an Action Proposal. User can approve individually or batch-approve.

#### 7.4.4 WhatsApp Messaging via URL Scheme

WhatsApp messaging uses the OS-native URL scheme — **no WhatsApp API, no Business account, no custom integration**. The message opens in WhatsApp with pre-filled text; the user must manually tap "Send."

**Mechanism:**
```bash
# Open WhatsApp with pre-filled message to a specific number
open "https://wa.me/1XXXXXXXXXX?text=Happy%20Diwali%21%20Wishing%20you%20and%20your%20family%20a%20wonderful%20celebration.%20%F0%9F%AA%94%E2%9C%A8"

# Open WhatsApp with pre-filled message (no specific recipient — user picks)
open "https://wa.me/?text=Happy%20Diwali%21%20..."
```

**Workflow:**
1. **Trigger:** User requests ("wish Rahul happy birthday on WhatsApp") OR Artha recommends during catch-up ("Today is Rahul's birthday — compose WhatsApp greeting?")
2. **Compose:** Claude drafts the message text
3. **Preview:** Message displayed in terminal within Action Proposal
4. **Approve:** User approves or modifies
5. **Execute:** `open "https://wa.me/..."` command opens WhatsApp with pre-filled text
6. **Human gate (OS-enforced):** User sees the message in WhatsApp and must tap Send — Artha cannot send without user action
7. **Log:** Proposed message logged to `audit.md` (actual send is not confirmable)

**Contact management:** Phone numbers stored in `~/OneDrive/Artha/config/contacts.md` with sensitivity `standard`. Numbers are URL-encoded in the `wa.me` URL.

**Limitations:**
- Cannot confirm whether user actually tapped Send
- Cannot attach images via URL scheme (user must manually attach generated visuals)
- Cannot read incoming WhatsApp messages (outbound only)
- macOS `open` command required — works on Mac only (iPhone users can use WhatsApp directly)

#### 7.4.5 Calendar Event Creation

Expands Google Calendar MCP from read-only to read-write.

**Scope expansion required:** Google Calendar MCP OAuth scope must include `calendar.events` write permission. This is a Phase 1A setup task.

**Event creation workflow:**
1. **Trigger:** User requests ("add Parth's SAT date to calendar") OR Artha detects from email ("PSE bill due date detected — add to calendar?")
2. **Draft:** Claude composes event with title, date/time, location, description, reminders
3. **Preview:** Event details displayed in terminal within Action Proposal
4. **Approve:** User approves, modifies, or rejects
5. **Create:** Google Calendar MCP creates the event
6. **Log:** Created event logged to `audit.md`

**Pre-approved at Level 2 (example):** Auto-add confirmed bill due dates to calendar (user explicitly opts in).

#### 7.4.6 Visual Message Generation

Combines Gemini Imagen (§3.7.5) with email/WhatsApp actions for end-to-end visual messaging.

**Complete workflow example — Diwali greetings:**
1. User: "Send Diwali greetings to family and friends with a custom card"
2. Artha generates visual: `gemini "Generate a Diwali greeting card..."` → saves to `~/OneDrive/Artha/visuals/diwali-2026.png`
3. Artha reads `contacts.md` for Diwali greeting list
4. **For email contacts:** Compose personalized email with Diwali visual attached → Action Proposal per recipient/group
5. **For WhatsApp contacts:** Compose text message → Action Proposal → `open "https://wa.me/..."` (user manually attaches the visual from OneDrive)
6. User approves each action (or batch-approves email group)

**Supported occasions (configured in `~/OneDrive/Artha/config/occasions.md`):**
| Occasion | Type | Date Source | Visual Style |
|---|---|---|---|
| Diwali | Festival | Fixed calendar | Traditional, diyas, rangoli |
| Holi | Festival | Fixed calendar | Colorful, playful |
| Christmas | Festival | Fixed calendar | Warm, festive |
| New Year | Festival | Fixed calendar | Celebratory |
| Birthdays | Personal | `contacts.md` | Age-appropriate, personalized |
| Anniversaries | Personal | `contacts.md` | Elegant, warm |
| Congratulations | Ad-hoc | User-triggered | Achievement-themed |

#### 7.4.7 Action Lifecycle

Every action follows a consistent lifecycle:

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ PROPOSE  │───▶│ REVIEW   │───▶│ EXECUTE  │───▶│  LOG     │
│          │    │          │    │          │    │          │
│ Claude   │    │ User     │    │ CLI/MCP  │    │ audit.md │
│ drafts   │    │ approves │    │ performs │    │ records  │
│ action   │    │ modifies │    │ action   │    │ outcome  │
│          │    │ rejects  │    │          │    │          │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                     │
                     ▼ (if rejected)
               ┌──────────┐
               │  LOG     │
               │ rejection│
               │ + reason │
               └──────────┘
```

**Audit log entry format:**
```markdown
## Action Log Entry
- timestamp: 2026-03-07T08:45:00-08:00
- action_type: compose_send_email
- channel: gmail
- recipient: family_india group (12 addresses)
- subject: "Happy Diwali from the Mishra family 🪔"
- attachment: visuals/diwali-2026.png
- status: approved_and_sent
- proposed_by: catch-up workflow (social domain)
- approved_by: user (manual)
```

### 7.5 Bootstrap Command Workflow *(v2.0 — Workstream L, P0)*

The `/bootstrap` command provides guided state population for domains that still contain bootstrap templates (all fields showing TODO). This addresses the cold-start problem where state files are created but never populated with actual data.

**Trigger detection:**
```
At catch-up Step 4 (READ ALL STATE FILES):
  For each state file loaded:
    if frontmatter.updated_by == "bootstrap":
      bootstrap_needed.append(domain)
  
  If bootstrap_needed is not empty:
    Display: "⚠️ [N] domains have never been populated: [list].
             Run /bootstrap to populate them now, or continue catch-up with partial state."
```

**Interview workflow (when `/bootstrap` is invoked):**

```
┌─────────────────────────────────────────┐
│ 1. DOMAIN SELECTION                     │
│    Show unpopulated domains:            │
│    "These domains need data:            │
│     1. Immigration (critical)           │
│     2. Finance (high)                   │
│     3. Insurance (high)                 │
│     4. Estate (critical)               │
│     5. Health (high)                    │
│    Start with? [1-5 or 'all']"         │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 2. GUIDED INTERVIEW (per domain)        │
│    Ask structured questions derived     │
│    from the domain's state file schema: │
│                                         │
│    Immigration example:                 │
│    Q1: "Current visa type? (H-1B, L-1, │
│         etc.)"                          │
│    Q2: "Priority date? (YYYY-MM-DD)"   │
│    Q3: "I-140 status? (Filed/Approved/ │
│         Pending/Not filed)"            │
│    Q4: "EAD expiry date?"              │
│    Q5: "AP expiry date?"               │
│    Q6: "Dependent names + visa types?" │
│    Q7: "Attorney contact info?"        │
│    ...                                 │
│                                         │
│    Rules:                               │
│    - Ask ONE question at a time.        │
│    - Accept "skip" or "don't know" —    │
│      mark field as TODO, don't block.   │
│    - Validate format (dates, numbers).  │
│    - Confirm each answer: "I'll record  │
│      PD as 2019-04-15. Correct? [Y/N]" │
│    - After all questions: show full     │
│      preview of state file.             │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 3. WRITE + VERIFY                       │
│    a. Write state file with answers     │
│    b. Update frontmatter:               │
│       updated_by: bootstrap_interview   │
│       last_updated: [now]               │
│    c. Apply Layer 2 verification (§8.5) │
│    d. If encrypted domain: encrypt      │
│    e. Log to audit.md:                  │
│       BOOTSTRAP_COMPLETE | domain |     │
│       fields_populated: N/M             │
└──────────────┬──────────────────────────┘
               │
               ▼
│ 4. NEXT DOMAIN or DONE                  │
│    "Immigration populated (12/15 fields)│
│     Continue to Finance? [Y/N]"         │
└─────────────────────────────────────────┘
```

**Design rules:**
1. The interview derives questions from the state file schema (§4.2–§4.11) — no hardcoded question list
2. Fields marked `sensitivity: critical` in §8.2 trigger redaction reminders: "I'll store this as [REDACTED-PASSPORT-Ved]. I won't need the actual number."
3. `/bootstrap finance` targets a single domain; `/bootstrap` targets all unpopulated domains
4. Progress is saved after each domain — if the user stops mid-way, completed domains are preserved
5. Re-running `/bootstrap` on a populated domain warns: "Immigration already has data (last updated 2026-03-08). Re-bootstrap will overwrite. Proceed? [Y/N]"

### 7.6 Compound Signal Detection Engine *(v2.0 — Workstream K)*

Cross-domain pattern recognition that identifies meaningful correlations between independently-captured signals.

**Detection runs at:** Step 6 (Cross-Domain Reasoning) of the catch-up workflow.

**Signal correlation rules:**

| Pattern | Domains | Detection Logic | Alert |
|---|---|---|---|
| Travel + Credit Card benefit | travel + finance | Flight/hotel booking detected → match card benefits | "This booking may qualify for [card] [perk]" |
| Immigration deadline + leave planning | immigration + calendar + employment | EAD/H-1B deadline within 90 days + no attorney meeting scheduled | "Attorney meeting needed before [deadline] — no appointment found on calendar" |
| School event + work calendar | kids + calendar | School event detected on a work day, no calendar block | "Parth's [event] on [date] — no calendar block yet" |
| Bill spike + seasonal pattern | finance + memory | Bill amount >20% above baseline + seasonal pattern in memory.md | "PSE bill $312 — 30% higher than avg, but typical for January (heating)" |
| Health appointment + insurance | health + insurance | Doctor appointment → check insurance coverage status | "Dr. visit March 15 — verify in-network status under [plan]" |
| Goal stall + behavioral change | goals + memory | Goal metric flat for 2+ weeks + behavioral baseline deviation | "Net worth goal stalled — savings rate dropped from 20% to 15%" |

**Compound signal format in briefing:**

```markdown
## 🔗 Compound Signals
- **Immigration + Calendar**: EAD renewal 90 days out — no Fragomen meeting on calendar.
  Recommended: Schedule attorney call this week. [Action Proposal queued]
- **Finance + Travel**: United booking $1,200 — Chase Sapphire 3x points applies.
  Note: Trip insurance auto-included for bookings over $500.
```

**Storage:** Compound signals are not persisted to a separate state file — they are ephemeral, surfaced in the briefing only. Recurring compound patterns are added to `memory.md` → `Patterns Learned` for future reference.

### 7.7 Pattern of Life Detection *(v2.0 — Workstream M)*

Learns behavioral baselines from accumulated state data and surfaces anomalies.

**Data collection:** After each catch-up, update `memory.md` → `Behavioral Baselines` (§4.7) with:
- Catch-up timestamp (builds typical_days/typical_times)
- Email volume (builds avg_emails_per_day)
- Domain activity distribution (builds typical_first_action_domain)
- Session duration (builds catch_up_duration_avg)

**Baseline calculation:** After 10+ catch-ups, compute moving averages with 30-day windows. Before 10 catch-ups, baselines are marked as `insufficient_data` and anomaly detection is disabled.

**Anomaly surfacing rules:**

| Anomaly | Detection | Alert Priority |
|---|---|---|
| Unusual gap | Gap >2× avg_gap_hours | 🟡 "Longer than usual gap — digest mode activated" |
| Email volume spike | Emails >2× avg_emails_per_day | 🟡 "Unusually high email volume (142 vs avg 50)" |
| Spending deviation | Monthly spend >1.5× avg_monthly_spend | 🟠 "March spending on pace for $12,750 (avg $8,500)" |
| Communication drop | Outbound messages <50% of monthly avg | 🟡 "Communication frequency down — 8 vs avg 20" |
| Routine disruption | Catch-up at unusual time (>3hr from typical) | 🔵 "Off-schedule catch-up — unusual for Wednesday 3pm" |

**Privacy note:** Behavioral baselines are derived from Artha's own state data — no external tracking, no GPS, no app usage monitoring. All baselines stored in `memory.md` (sensitivity: standard).

### 7.8 Email Volume Scaling Tiers *(v2.0 — Workstream R)*

Adaptive processing strategy based on email batch size to maintain quality within context window constraints.

| Tier | Email Count | Processing Strategy | Token Budget | Quality |
|---|---|---|---|---|
| Standard | ≤50 | Full processing per §7.1 Steps 3b–5 | Unlimited | Full extraction |
| Medium | 51–200 | Marketing suppression aggressive + per-email cap 1000 tokens + cluster summarization for ≤3 senders with >5 emails | ~80K tokens | Slightly reduced detail on low-priority |
| High | 201–500 | Two-pass: Pass 1 — P0 domains only (immigration, finance, kids) with full extraction. Pass 2 — remaining domains with summary-only extraction (subject + sender + 1-line summary) | ~120K tokens (split) | P0 domains full, others summary |
| Extreme | 500+ | Three-pass: Pass 1 — P0 domains. Pass 2 — P1 domains (health, home, insurance). Pass 3 — P2 domains (count + cluster only). User warned: "625 emails in backlog — processing P0/P1 in detail, P2 as counts only." | ~150K tokens (split) | Priority-tiered |

**Tier selection:** Automatic based on email count from Step 2 fetch. Logged to `health-check.md` → `Email Pre-Processing Stats`.

**Trigger:** Volume scaling activates only when emails exceed the Standard tier (>50). For most daily catch-ups, Standard tier applies with no behavioral change.

### 7.9 Life Scorecard *(v2.0 — Workstream S)*

Quantified weekly life snapshot generated during Sunday catch-up (or first Monday catch-up). Stored in `dashboard.md` (§4.12).

**Scoring rubric per dimension:**

| Dimension | Score 1–3 (Red) | Score 4–6 (Yellow) | Score 7–9 (Green) | Score 10 (Exceptional) |
|---|---|---|---|---|
| Financial Health | Bills overdue, spending >150% budget | Some bills pending, spending 100–130% | Bills current, spending < budget, saving | All auto-pay, saving >20%, growing NW |
| Immigration | Missed deadline, RFE received | Deadline < 60 days, no attorney contact | All cases current, next deadline >90 days | Green card approved |
| Kids & Education | GPA drop >0.3, missed assignments | GPA stable, some activities missed | GPA >3.5, activities on track | Academic awards, all goals exceeded |
| Physical Health | Overdue appointments, no exercise data | Some appointments pending | All appointments current, exercise regular | Comprehensive tracking, improving metrics |
| Social & Relationships | >3 reconnects overdue, no outbound | 1–2 reconnects overdue | All contacts within frequency targets | Active engagement, reciprocity balanced |
| Career & Goals | >2 goals off-track, stalled 30+ days | 1 goal off-track | All goals on-track or ahead | Breakthrough progress, goals exceeded |
| Home & Operations | Deferred maintenance, overdue tasks | Minor tasks pending | All maintenance current | Proactive improvements, ahead of schedule |

**Composite Life Score:** Average of all dimensions, weighted equally. Displayed as "Life Score: 7.1/10 (↑ vs 6.8 last week)".

**Trend detection:** Week-over-week comparison. Arrow indicators: ↑ improved ≥0.5, → stable (±0.4), ↓ declined ≥0.5.

### 7.10 Consequence Forecasting Engine *(v2.0 — Workstream K)*

Adds "IF YOU DON'T" alerts to briefings — surfaces the downstream consequences of inaction on high-priority items.

**Trigger:** Any 🔴 Critical or 🟠 Urgent alert with a clear deadline and identifiable consequence chain.

**Forecast structure:**
```markdown
⚠️ IF YOU DON'T:
- **Initiate EAD renewal within 2 weeks** → 45-day processing time means gap between current EAD expiry (Nov 15) and renewal. During gap: cannot work legally. Impact: employment, income, all downstream finances.
- **Pay PSE bill by March 20** → $25 late fee + potential service disruption flag. Auto-pay not enabled for this account.
```

**Reasoning chain requirements:**
1. State the inaction clearly: "If you don't [specific action]..."
2. State the timeline: "...by [date/timeframe]..."
3. State the first-order consequence: "...then [immediate effect]..."
4. State cascade effects if applicable: "...which leads to [downstream impact]..."
5. Only include consequences with >70% confidence — do not speculate

**Integration:** Appears in the ONE THING section of Standard briefings and as a dedicated line in Flash briefings (§5.1.2). Weekly summary includes a "Consequences Averted" section showing items that were acted on in time.

### 7.11 Pre-Decision Intelligence Packets *(v2.0 — Workstream K)*

On-demand research compilation when a decision point is detected or user asks for analysis.

**Trigger:**
1. Cross-domain reasoning (Step 6) detects a decision point → auto-creates entry in `decisions.md` → offers intelligence packet
2. User explicitly asks: "Should I refinance?" or "Compare college options for Parth"

**Packet structure:**
```markdown
# Intelligence Packet: [Decision Summary]
**Generated:** [timestamp]
**Confidence:** [low | medium | high]

## Context
[What triggered this decision point]

## Key Data Points (from Artha state)
- [Relevant data extracted from state files]

## External Research (via Gemini CLI)
- [Web search results for current rates/prices/policies]

## Options Analysis
| Option | Pros | Cons | Risk Level | Cost Impact |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

## Artha Recommendation
[Synthesized recommendation with reasoning chain]
**Ensemble consensus:** [If high-stakes, include Gemini + Copilot perspectives per §3.7.3]

## What Artha Doesn't Know
[Explicitly state information gaps that might change the recommendation]
```

**Delivery:** Displayed in terminal. If the user requests it via iPhone/Claude.ai, provide a summary with "Run `/catch-up deep` on Mac for full analysis."

### 7.12 Session Quick-Start Routing *(v2.0 — Workstream K)*

Detects session context and routes to the most appropriate workflow instead of always starting with a full catch-up.

**Detection logic (runs before Step 0):**

```
When user opens a session:
  last_catch_up = read health-check.md → last_catch_up timestamp
  hours_since = now - last_catch_up
  
  if hours_since < 4:
    # Recent catch-up — likely a follow-up question or quick check
    Display: "Last catch-up was [hours_since]h ago. What would you like?
             1. Quick update (flash briefing — new items only)
             2. Deep dive into [most active domain]  
             3. Something else"
  
  elif hours_since >= 4 AND hours_since < 48:
    # Normal gap — standard catch-up
    Proceed with standard §7.1 workflow
  
  elif hours_since >= 48:
    # Extended gap — digest mode
    Proceed with digest mode (§5.1.1)
    
  # Bootstrap detection (any gap):
  if any state file has updated_by == "bootstrap":
    Prepend: "⚠️ [N] domains need data. Run /bootstrap first? [Y/N]"
```

**Stale state detection:**
```
For each state file in the ALWAYS tier:
  if last_activity > 7 days AND domain has known email senders:
    stale_domains.append(domain)

If stale_domains:
  Display: "⚠️ [N] domains have gone stale (no activity in 7+ days): [list].
           This may indicate missed emails or data source issues."
```

### 7.13 Briefing Compression Levels *(v2.0 — Workstream O)*

Specification for the three compression levels referenced in §5.1.2.

**Selection logic:**

```
compression_level = STANDARD  # default

if user_said("quick" OR "flash" OR "brief"):
  compression_level = FLASH
elif user_said("deep" OR "full" OR "analysis" OR "detailed"):
  compression_level = DEEP
elif hours_since_last_run < 4:
  compression_level = FLASH  # auto-select for very recent catch-ups
elif context_pressure_level == "red":
  compression_level = FLASH  # forced compression to save tokens
```

**Content inclusion matrix:**

| Section | Flash | Standard | Deep |
|---|---|---|---|
| Critical/Urgent alerts | ✅ (condensed) | ✅ (full) | ✅ (full + history) |
| Today's calendar | ✅ (one line) | ✅ (full) | ✅ (full + 3-day lookahead) |
| By Domain sections | ❌ | ✅ | ✅ (with trend analysis) |
| Relationship Pulse | ❌ | ✅ | ✅ (with full reconnect queue) |
| Goal Pulse | ❌ (count only) | ✅ (table) | ✅ (table + leading indicators + coaching) |
| ONE THING | ❌ | ✅ | ✅ (with consequence chain) |
| Consequence Forecast | ✅ (one line) | ✅ | ✅ (full chain per alert) |
| Compound Signals | ❌ | ✅ | ✅ (with recommendations) |
| Cross-Domain Analysis | ❌ | ❌ | ✅ (extended thinking) |
| Scenario Updates | ❌ | ❌ | ✅ |
| Life Scorecard | ❌ | ❌ | ✅ (full table) |
| Footer stats | ✅ (one line) | ✅ | ✅ (detailed) |

### 7.14 Context Window Pressure Management *(v2.0 — Workstream P)*

Active management of context window utilization to prevent silent truncation or quality degradation.

**Pressure levels:**

| Level | Headroom | Trigger | Mitigation |
|---|---|---|---|
| Green | >30% | Normal operation | None — full processing |
| Yellow | 20–30% | After Step 4b (state file loading) | Escalate tier classification: move ACTIVE → REFERENCE for lowest-priority domains. Log tier escalation. |
| Red | <20% | After Step 5 (domain processing) | Force FLASH briefing compression. Skip REFERENCE tier entirely. Split remaining email batch into second pass. Display: "⚠️ Context pressure high — briefing compressed." |
| Critical | <10% | After any step | HALT additional processing. Generate minimal briefing from data already processed. Display: "⛔ Context limit reached — partial briefing. [N] emails unprocessed." Log to health-check.md. |

**Monitoring integration:**
- After each step in §7.1, Artha checks approximate token usage
- Token estimates: state files (~500 tokens per file), emails (~300 tokens avg per processed email), prompts (~800 tokens each)
- Logged to `health-check.md` → `Context Window Pressure` section (§4.5)
- If `pressure_events` exceeds 3 in a 7-day rolling window, surface a 🟡 alert recommending increased catch-up frequency or email volume scaling

### 7.15 OAuth Token Resilience *(v2.0 — Workstream Q)*

Proactive monitoring and self-healing for OAuth tokens across all providers.

**Pre-flight token check (enhanced Step 0):**

```
For each configured provider (Gmail, GCal, MS Graph, iCloud):
  a. Verify token file exists at expected path
  b. Attempt lightweight API call (e.g., Gmail: list 1 label; Graph: /me)
  c. If 401: attempt automatic token refresh
  d. If refresh fails: 
     - Log to health-check.md → OAuth Token Health
     - Increment refresh_failures_7d counter
     - Display: "⚠️ [Provider] token expired. Run [setup script] to re-authenticate."
     - Continue catch-up WITHOUT that provider (partial data, not halt)
  e. If 403 (scope revoked): HALT and display clear remediation steps
```

**Proactive expiry warnings:**
- If MS Graph token expiry < 7 days: surface 🟡 alert in briefing
- If any provider has refresh_failures_7d > 0: include in System Health section of dashboard.md
- Track token refresh history for anomaly detection (sudden failures may indicate revoked consent)

**Self-healing:** If token refresh succeeds but took >5 seconds, log as `TOKEN_REFRESH_SLOW` for monitoring. If token refresh fails for the same provider 3+ times in 7 days, escalate to 🟠 alert.

---

## 8. Security Model

### 8.1 Credential Management

| Credential | Storage | Access |
|---|---|---|
| Gmail OAuth client ID/secret | macOS Keychain | MCP server reads at startup |
| Gmail OAuth refresh token | macOS Keychain | MCP server reads for token refresh |
| Calendar OAuth tokens | macOS Keychain | MCP server reads at startup |
| SMTP credentials (if needed) | macOS Keychain | send_briefing.py reads at runtime |
| Gemini CLI API key | macOS Keychain | Gemini CLI reads via env var `GEMINI_API_KEY` |
| GitHub CLI token (Copilot) | macOS Keychain | `gh auth` manages token lifecycle |
| WorkIQ M365 Copilot token *(v2.2)* | Managed by `@microsoft/workiq` package (Entra ID auth) | WorkIQ CLI reads at invocation. Windows work laptop only. |

**No credentials in plaintext files.** CLAUDE.md, domain prompts, and state files never contain credentials. MCP config references environment variables that are populated from Keychain. Gemini and Copilot CLIs manage their own authentication flows — keys are stored in their native credential stores or exported via Keychain environment variables.

### 8.2 Sensitive Data Redaction Rules

State files are processed by Claude API (ephemeral processing) and may be visible in terminal scrollback. Redaction rules prevent the most sensitive identifiers from persisting in state while keeping operationally needed data accessible.

#### 8.2.1 Immigration Domain (`sensitivity: critical`)

| Data Element | Handling |
|---|---|
| Passport numbers | Stored as `[REDACTED-PASSPORT-Ved]` in state files; full value in Keychain if needed |
| A-numbers (Alien Registration) | Stored as `[REDACTED-ANUM-Ved]` in state files |
| Receipt numbers (USCIS) | Stored in full — needed for case tracking queries |
| SSN | Never stored anywhere in Artha |
| DOB | Stored for CSPA age calculation — not redacted |

#### 8.2.2 Finance & Tax Domain (`sensitivity: high` / `critical` for tax)

| Data Element | Handling |
|---|---|
| SSN / ITIN | Never stored — `[REDACTED]` |
| Bank account numbers | Last 4 digits only: `****1234` |
| Bank routing numbers | `[REDACTED]` |
| Credit card numbers | Last 4 digits only: `****5678` |
| AGI / taxable income | Stored in state (needed for goal tracking) — excluded from emailed briefings |
| EIN (employer) | Stored in full — low sensitivity, needed for document matching |
| Tax refund/owed amounts | Stored in state (needed for financial planning) |
| Investment account numbers | Last 4 digits only |

#### 8.2.3 Estate & Legal Domain (`sensitivity: critical`)

| Data Element | Handling |
|---|---|
| SSN (in estate docs) | Never stored — `[REDACTED]` |
| Trust identification numbers | `[REDACTED-TRUST]` |
| Attorney-client details | Stored: contact info, engagement dates. Not stored: fee structures, retainer amounts. |
| Beneficiary designations | Stored by relationship only: "Primary: Archana. Contingent: Parth, Trisha." No account-specific amounts. |
| Guardian designations | Stored in full — operationally critical |

#### 8.2.4 Insurance Domain (`sensitivity: high`)

| Data Element | Handling |
|---|---|
| Policy numbers | Stored in full — needed for claims and renewals |
| Premium amounts | Stored in state (needed for budget tracking) |
| Coverage limits | Stored in state (needed for adequacy review) |
| Claims details | Summary only: date, type, status. No medical details in health/auto claims. |

#### 8.2.5 Document Repository Access (Phase 2+)

When Artha gains access to the document repository (tax returns, brokerage statements, trust documents, legal correspondence):

**Extract-and-discard policy:**
- Claude reads the document via filesystem access
- Extracts structured data fields per the domain prompt's extraction rules
- Raw document content is **never copied into state files**
- State files contain summaries: "2025 Federal Return: AGI $XXX,XXX, refund $X,XXX, filed 2026-02-15"
- The source documents remain in their original repository location, untouched
- Audit log records which documents were accessed, what was extracted, and which state file was updated

**API exposure minimization:**
- Document content passes through Claude API for processing (ephemeral, per Anthropic's data policy)
- To minimize exposure: process one document at a time, don't batch entire repository
- If a document type is too sensitive for any API processing, note it in the domain prompt as "manual-entry only"

#### 8.2.6 Work Calendar Domain (`sensitivity: standard` for metadata; `high` for ephemeral titles) *(v2.2)*

Work calendar data follows a **partial redaction** model: sensitive codenames are substring-replaced locally before meeting titles enter the Claude API context, but meeting type context (e.g., "Review", "Planning") is preserved.

| Data Element | Handling |
|---|---|
| Meeting titles | **Partial redaction** — sensitive keywords in `config/settings.md` → `workiq_redaction` list replaced with `[REDACTED]`. Only matched substrings replaced. e.g., "Project Cobalt Review" → "[REDACTED] Review" |
| Meeting attendee names | Passed to Claude for conflict detection (ephemeral). NOT persisted to any state file. |
| Meeting times/durations | Ephemeral in briefing. Count+duration aggregates persisted to `state/work-calendar.md`. |
| Teams meeting links | Passed to Claude for Join action proposals. NOT persisted. |
| Meeting bodies/agendas | NOT requested from WorkIQ. Query explicitly asks for titles + times only. |
| Raw WorkIQ response | Saved to `tmp/work_calendar.json`. **Explicitly deleted** at Step 9 before vault encrypt. |

**Redaction configuration** (`config/settings.md`):
```yaml
workiq_redaction:
  keywords:
    - "Project Cobalt"
    - "ITAR"
    - "Confidential"
  patterns:
    - "CVE-\\d+-\\d+"
    - "MSRC-\\d+"
  replace_with: "[REDACTED]"
```

**Implementation note:** Redaction uses substring replacement (`re.subn`), not full-title replacement. This is critical because meeting-trigger classification (§4.9 in work-int spec) needs to see "Review" and "Interview" tokens in the title to auto-create Employment OIs.


### 8.8 Privacy Surface Acknowledgment *(v1.9 — Workstream J)*

Technical documentation of the Claude API privacy surface, referenced by PRD §12.6.

**Data flows to external APIs:**

| API | Data sent | Retention policy | Mitigation |
|---|---|---|---|
| Claude (Anthropic) | State files, email content, prompts, conversation | Ephemeral — not retained for training | `pii_guard.sh` pre-persist filter, domain redaction rules |
| Gemini CLI (Google) | Web search queries, URL fetch requests | Google’s standard API terms | `safe_cli.sh` strips PII tokens before delegation |
| Copilot CLI (GitHub) | Script content, config validation queries | GitHub’s Copilot terms | `safe_cli.sh` strips PII tokens before delegation |
| Gmail MCP | OAuth token, email queries | Google API terms | `gmail.readonly` + `gmail.send` scope only |
| Google Calendar MCP | OAuth token, calendar queries | Google API terms | Calendar read + event write scope |
| Microsoft Graph | OAuth token, To Do task sync, Outlook email, Outlook calendar | Microsoft API terms | `msgraph_fetch.py` reads only; task sync writes |

**What cannot be mitigated:**
- Claude sees raw email content via MCP before `pii_guard.sh` can filter (MCP returns data directly into context)
- All state files loaded during catch-up are sent to Anthropic API for processing
- Gemini/Copilot CLI calls send query text to Google/GitHub servers

**Artha.md requirement:** Include a `§4.3 Privacy Surface` section in Artha.md that states: "All data processed during catch-up sessions is sent to the Anthropic Claude API for ephemeral processing. Anthropic does not retain API inputs/outputs for model training. External CLIs (Gemini, Copilot) receive sanitized queries only (PII stripped by safe_cli.sh). See tech spec §8.8 for full privacy surface documentation."

### 8.3 API Data Flow

```
User's Mac ──── Claude API (Anthropic) ──── Response
                     │
                     │  Data sent: state files, email content,
                     │  prompt instructions
                     │
                     │  Anthropic's API data policy:
                     │  - Not used for model training
                     │  - Not retained beyond request processing
                     │  - Ephemeral processing only
                     │
                     └── This is the ONLY external data flow
```

### 8.4 Local Security

| Measure | Implementation |
|---|---|
| Disk encryption | macOS FileVault (full-disk encryption) — assumed enabled |
| Screen lock | Standard macOS auto-lock — protects against physical access |
| Backup encryption | Time Machine encryption enabled |
| OneDrive encryption | Standard state files: synced as plaintext. High/critical state files: synced as `.age` encrypted. Encryption keys in macOS Keychain — never on OneDrive. |
| Terminal scrollback | Sensitive data redacted in state files; terminal scrollback is session-local |
| Git tracking (optional) | If state files are git-tracked: use `.gitignore` for any files with sensitive data |

### 8.5 Encrypted State Tier (Phase 1 — Required)

With OneDrive sync enabled, `age` encryption is **mandatory** for all `sensitivity: high` and `critical` state files. OneDrive stores only `.age` files for these domains — plaintext versions exist only during an active catch-up session on Mac.

**Implementation: `age` encryption** (https://age-encryption.org/) — Modern, simple, no GPG keyring complexity:

```bash
# Decrypt at catch-up start
age -d -i <(security find-generic-password -a artha -s age-key -w) \
  ~/OneDrive/Artha/state/finance.md.age > ~/OneDrive/Artha/state/finance.md

# Encrypt after catch-up
age -r age1... ~/OneDrive/Artha/state/finance.md > ~/OneDrive/Artha/state/finance.md.age
rm ~/OneDrive/Artha/state/finance.md
```
- Key stored in **macOS Keychain** (Mac) / **Windows Credential Manager** (Windows) — never on OneDrive
- ~30-line helper script: `~/OneDrive/Artha/scripts/vault.sh`
- CLAUDE.md instructs: "Before reading state files, run `./scripts/vault.sh decrypt`. After catch-up, run `./scripts/vault.sh encrypt`."
- If catch-up crashes before re-encrypting, OneDrive may briefly sync plaintext. Mitigation: `vault.sh encrypt` also runs as a macOS LaunchAgent on any file change (belt-and-suspenders).

**Which state files get encrypted:**

| State File | Encrypted at Rest | Synced to OneDrive as |
|---|---|---|
| immigration.md | Yes — critical | `immigration.md.age` |
| finance.md | Yes — high | `finance.md.age` |
| insurance.md | Yes — high | `insurance.md.age` |
| estate.md | Yes — critical | `estate.md.age` |
| health.md | Yes — high | `health.md.age` |
| audit.md | Yes — contains references to sensitive actions | `audit.md.age` |
| contacts.md | Yes — contains phone numbers (PII) | `contacts.md.age` |
| calendar.md | No — standard | `calendar.md` (plaintext) |
| kids.md | No — standard | `kids.md` (plaintext) |
| goals.md | No — standard (goal names only, no financial targets) | `goals.md` (plaintext) |
| memory.md | No — standard | `memory.md` (plaintext) |
| health-check.md | No — standard | `health-check.md` (plaintext) |

**Key management:**
- `age` identity (private key) generated once: `age-keygen -o /dev/stdout | security add-generic-password -a artha -s age-key -w`
- `age` recipient (public key) stored in `~/OneDrive/Artha/config/settings.md` (public, safe to sync)
- Mac and Windows can both decrypt if the `age` identity is provisioned in both credential stores
- iPhone cannot decrypt `.age` files — by design (high/critical data is terminal-only)

This is a **progressive** addition — does not change the data model, catch-up workflow, or prompt structure. It adds a decrypt/encrypt step at the beginning and end of each catch-up.

**Crash recovery:** If the catch-up session terminates unexpectedly (Mac sleep, network drop, session timeout), sensitive state files may remain as plaintext on disk and briefly sync to OneDrive. Mitigations:
1. **OneDrive selective sync:** Configure OneDrive to exclude `state/*.md` for files that also have `.age` equivalents. Only `*.md.age` files sync. Plaintext exists only on Mac's local disk during catch-up.
2. **LaunchAgent watchdog:** A macOS LaunchAgent checks every 5 minutes: if `.artha-decrypted` lock file exists (created by `vault.sh decrypt`) and no `claude` process is running, execute `vault.sh encrypt` and remove the lock file.
3. **vault.sh creates lock file:** `vault.sh decrypt` creates `~/OneDrive/Artha/.artha-decrypted`. `vault.sh encrypt` removes it. The lock file itself never contains sensitive data — it is a signal file only.

#### 8.5.1 Data Integrity Guard *(v2.0 — Workstream K, P0)*

Three-layer protection against silent data loss during vault encrypt/decrypt cycles and state file updates. This addresses the vulnerability where `vault.sh decrypt` overwrites existing `.md` with `.age` content, potentially discarding session modifications.

**Layer 1: Pre-Decrypt Backup**

Before `vault.sh decrypt` overwrites any `.md` file:

```bash
# In vault.sh do_decrypt:
for f in "${SENSITIVE_FILES[@]}"; do
  if [[ -f "$STATE_DIR/$f.md" ]]; then
    # Check if .md has content beyond bootstrap template
    if grep -q "updated_by: bootstrap" "$STATE_DIR/$f.md" 2>/dev/null; then
      : # Bootstrap template — no backup needed
    else
      cp "$STATE_DIR/$f.md" "$STATE_DIR/$f.md.bak"
      echo "[$(date -Iseconds)] BACKUP_CREATED | $f.md → $f.md.bak" >> "$STATE_DIR/audit.md"
    fi
  fi
  # Now safe to decrypt
  age -d ... "$STATE_DIR/$f.md.age" > "$STATE_DIR/$f.md"
done
```

**Layer 2: Post-Write Verification**

After each state file write during catch-up processing:

```
For each state file modified in Step 5:
  a. Verify YAML frontmatter is valid (domain field present, last_updated parseable)
  b. Verify file size > 0
  c. Verify at least one ## section header exists
  d. If any check fails: revert to pre-write content, log to audit.md as WRITE_VERIFY_FAIL
```

This is implemented in Artha.md as an operating rule (not in vault.sh).

**Layer 3: Net-Negative Write Guard**

Before committing state file updates (Step 8b in §7.1):

```
For each modified state file:
  before_fields = count non-empty, non-TODO fields in original
  after_fields = count non-empty, non-TODO fields in modified
  if after_fields < before_fields * 0.8:
    ⛔ HALT write
    Display: "Writing [domain].md would reduce populated fields from [N] to [M].
             This looks like data loss. Show diff? [Y/N]"
    Require explicit user confirmation to proceed
    Log to audit.md: WRITE_GUARD_TRIGGERED | domain | before_fields | after_fields | user_decision
```

**Audit trail format:**
```
[timestamp] BACKUP_CREATED | finance.md → finance.md.bak
[timestamp] DECRYPT_OK | finance.md | 2.3KB | frontmatter_valid
[timestamp] WRITE_VERIFY_OK | finance.md | 15 fields updated
[timestamp] WRITE_GUARD_TRIGGERED | immigration.md | before: 42 | after: 5 | decision: BLOCKED
[timestamp] BACKUP_RESTORED | immigration.md | restored from immigration.md.bak
```

**Backup retention:** `.md.bak` files are kept until the next successful encrypt cycle. After `vault.sh encrypt` completes without error, `.bak` files are removed. If the backup is older than 7 days, log a warning.

### 8.6 Pre-Flight PII Filter

The redaction rules in §8.2 govern what gets **written to state files**. But raw email content passes through the Claude API **before** those rules apply. This section specifies a mandatory device-local filter that intercepts PII **before** it enters the API call.

**Architecture:** A `pii_guard.sh` script (~80 lines, bash + grep -P) that scans email content and replaces detected PII patterns with safe tokens.

**Data flow clarification:** Claude Code's MCP tool invocations return data directly into Claude's context. The `pii_guard.sh` filter therefore operates as a **pre-persist** filter — Claude processes the PII-filtered content before writing to state files. The actual data flow is:

1. Claude calls Gmail MCP → raw email content enters Claude's context (Anthropic's ephemeral processing policy applies — not retained beyond request)
2. Claude extracts structured data from email content
3. Claude runs `pii_guard.sh scan` on the extracted data to verify no PII leaked through
4. If PII detected: replace with `[PII-FILTERED-*]` tokens before writing to state files
5. §8.2 redaction rules apply as Layer 2 when writing to state files

**Stretch goal (Option C):** Test whether Claude Code `PreToolUse` hooks can intercept Gmail MCP responses and pipe through `pii_guard.sh` before Claude sees the content. If hooks support response modification, this upgrades the filter from pre-persist to true pre-flight. Track in TD-19.

**Defense-in-depth model:**
```
Gmail MCP → raw email content
       │
       ▼
  pii_guard.sh filter    ←── Layer 1: Device-local regex. PII never leaves Mac.
       │
       ▼
  Claude API (ephemeral) ←── Sees pre-filtered content only.
       │
       ▼
  §8.2 redaction rules    ←── Layer 2: Claude applies domain-specific redaction
       │                         when writing to state files.
       ▼
  State files (Markdown)  ←── Double-filtered. PII removed at both layers.
```

**Detection patterns:**

| PII Type | Regex Pattern | Replacement Token | Notes |
|---|---|---|---|
| SSN | `\b\d{3}-\d{2}-\d{4}\b` | `[PII-FILTERED-SSN]` | Strictest — zero tolerance |
| SSN (no dashes) | `\b\d{9}\b` near "SSN", "social security", "tax id" | `[PII-FILTERED-SSN]` | Context-aware to reduce false positives |
| Credit card (Visa) | `\b4\d{3}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b` | `[PII-FILTERED-CC]` | Luhn validation if feasible |
| Credit card (MC) | `\b5[1-5]\d{2}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b` | `[PII-FILTERED-CC]` | |
| Credit card (Amex) | `\b3[47]\d{2}[- ]?\d{6}[- ]?\d{5}\b` | `[PII-FILTERED-CC]` | |
| Credit card (Discover) | `\b6(?:011\|5\d{2})[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b` | `[PII-FILTERED-CC]` | |
| Bank routing number | `\b\d{9}\b` near "routing", "ABA", "transit" | `[PII-FILTERED-ROUTING]` | Context-required to avoid false positives |
| Bank account number | `\b\d{8,17}\b` near "account number", "acct" | `[PII-FILTERED-ACCT]` | Context-required |
| US Passport | `\b[A-Z]\d{8}\b` near "passport" | `[PII-FILTERED-PASSPORT]` | |
| A-number | `\bA\d{8,9}\b` | `[PII-FILTERED-ANUM]` | USCIS Alien Registration Number |
| ITIN | `\b9\d{2}-[7-9]\d-\d{4}\b` | `[PII-FILTERED-ITIN]` | IRS Individual Taxpayer ID |
| Driver's license | State-specific patterns (WA: `WDL[A-Z0-9]{9}`) | `[PII-FILTERED-DL]` | Add patterns as needed |

**Two modes:**
- `scan` — detect and report (dry run). Outputs count and type of PII found. Used for testing.
- `filter` — detect and replace. Returns filtered content on stdout. Used in production.

**Integration with catch-up workflow:**
```
1. vault.sh decrypt
2. Read health-check.md for last run timestamp
3. Fetch emails (Gmail MCP) + calendar (Calendar MCP) — PARALLEL
4. ⭐ pii_guard.sh filter on email batch              ← NEW STEP
5. Route each item to domain prompt (with pre-filtered content)
6. Process domains, update state files
7. Cross-domain reasoning + briefing synthesis
8. Deliver + archive
9. vault.sh encrypt
10. Log PII filter statistics to audit.md             ← NEW STEP
```

**PII allowlists (per domain prompt):**

Some legitimate data patterns overlap with PII patterns. Each domain prompt includes a `pii_allowlist` section:

```markdown
## PII Allowlist
# These patterns are NOT PII for this domain — do not filter.
- USCIS receipt numbers: IOE-\d{10}, SRC\d{10}, LIN\d{10}
- Case numbers: [A-Z]{3}\d{7,10}
- Order confirmation numbers: \d{3}-\d{7}-\d{7} (Amazon format)
```

`pii_guard.sh` reads the allowlist from the relevant domain prompt and exempts matching patterns before applying PII filters.

**Audit logging:**

Every PII detection is logged to `audit.md`:
```
[2026-03-07T19:32:15-08:00] PII_FILTER | email_id: 18e3a2b | type: SSN | domain: finance | action: filtered
[2026-03-07T19:32:15-08:00] PII_FILTER | email_id: 18e3a2c | type: CC | domain: finance | action: filtered
[2026-03-07T19:32:16-08:00] PII_FILTER | batch_summary | emails_scanned: 47 | pii_found: 3 | pii_filtered: 3
```

No actual PII values are logged — only the type, source email ID, and action taken.

**Why not rely on Claude alone?**
- Claude's §8.2 redaction rules are **write-time** controls — they prevent PII from being stored in state files. But Claude must **read** the PII to know to redact it, meaning the PII has already been sent to the API.
- `pii_guard.sh` is a **transit-time** control — it prevents PII from ever entering the API call.
- Together they form defense-in-depth: Layer 1 (regex, device-local, pre-API) + Layer 2 (LLM-based, semantic, post-API).
- Regex catches known patterns with near-zero false negatives for structured PII (SSN, CC). Claude catches unstructured sensitive information that regex cannot (e.g., "my password is hunter2").

**Failure mode:** If `pii_guard.sh` fails (script error, regex timeout), the catch-up **halts** — it does not proceed with unfiltered content. Artha.md instructs: "If pii_guard.sh exits with non-zero status, stop the catch-up and report the error. Do not process unfiltered emails."

### 8.9 Data Fidelity Skills *(v4.0)*

Targeted Python scripts that pull high-fidelity data from institutional portals or official APIs.

**1. Skill Runner Orchestrator (`scripts/skill_runner.py`)**
- **Discovery:** Discover active skills from `config/skills.yaml`. 
- **Parallelism:** Execute each skill in parallel using `ThreadPoolExecutor`.
- **Dynamic Loader:** Use `importlib` to load modules from `scripts/skills/`. If a module is missing, log a warning and continue.
- **Aggregation:** Aggregate results into a standard JSON format.
- **Change Detection:** Compare `current` fetch results against `previous` state in `state/skills_cache.json`.
- **Cache Persistence:** Write results to `state/skills_cache.json` (encrypted).
- **Fail-safe Logic:**
    - **P0 (Immigration):** Halt catch-up on logical/parse errors. If status changes, alert P0 and continue catch-up. Transient errors warn and continue.
    - **P1/P2:** Log warning and continue catch-up.

**2. Skill Registry (`config/skills.yaml`)**
```yaml
skills:
  uscis_status:
    enabled: true
    priority: P0
    cadence: every_run
  visa_bulletin:
    enabled: true
    priority: P0
    cadence: weekly
  king_county_tax:
    enabled: true
    priority: P1
    cadence: daily
  noaa_weather:
    enabled: true
    priority: P1
    trigger_keywords: ["hike", "summit", "trail", "peak"]
```

**3. Skill Base Class (`scripts/skills/base_skill.py`)**
- All skills inherit from `BaseSkill`.
- **Required fields:** `compare_fields: list[str]` (e.g., `["status_type"]`).
- **Required methods:** `pull()` (network request), `parse()` (parser logic), `to_dict()`.

**4. Skill-Specific Parsers**
- **Visa Bulletin:** Parses `travel.state.gov` Table A & B. Determines authorized chart from USCIS "Adjustment of Status Filing Charts" page. Regex validation: `(\d{2}[A-Z]{3}\d{2}|C|U)`.
- **NOAA Weather:** Two-step call (`/points/{lat},{lon}` → extract forecast URL). Coordinates for Sammamish: `47.6162, -122.0355`. Requires `owner_email` from `config/artha_config.yaml` in User-Agent.
- **NHTSA Recalls:** Prefers `/recalls/recallsByVIN/{vin}` with make/model fallback.

**5. Centralized Cache (`state/skills_cache.json`)**
- **Schema:** 
  ```json
  {
    "uscis_status": {
      "last_run": "...",
      "current": { ... },
      "previous": { ... },
      "changed": true
    }
  }
  ```
- **Security:** Encrypted via `vault.py`. Decrypted before Skill Runner, re-encrypted after catch-up.

### 8.7 Outbound PII Wrapper for External CLIs

When Artha delegates tasks to Gemini CLI or Copilot CLI (§3.7), a wrapper script ensures no PII leaks to external services.

**Script: `~/OneDrive/Artha/scripts/safe_cli.sh`** (~30 lines, bash)

```bash
#!/bin/bash
# safe_cli.sh — PII-safe wrapper for external CLI calls
# Usage: safe_cli.sh <cli> "<query>"
# Example: safe_cli.sh gemini "What is the current EB-2 India priority date?"
set -euo pipefail

CLI="$1"
QUERY="$2"

# Scan query for PII patterns (reuses pii_guard.sh detection)
DETECTED=$(echo "$QUERY" | ./scripts/pii_guard.sh scan 2>&1) || {
  echo "ERROR: PII detected in outbound query. Blocked." >&2
  echo "Detected: $DETECTED" >&2
  echo "[$(date -Iseconds)] OUTBOUND_PII_BLOCK | cli: $CLI | pii_types: $DETECTED" \
    >> ~/OneDrive/Artha/state/audit.md
  exit 1
}

# Log the outbound call (no PII present — safe to log query)
echo "[$(date -Iseconds)] CLI_CALL | cli: $CLI | query_length: ${#QUERY}" \
  >> ~/OneDrive/Artha/state/audit.md

# Execute the CLI
$CLI "$QUERY"
```

**Artha.md instruction:** "Never call `gemini` or `copilot` directly for queries that may contain user data. Always use `./scripts/safe_cli.sh gemini 'your query'` or `./scripts/safe_cli.sh copilot 'your query'`. The wrapper scans for PII patterns and blocks the call if any are detected."

**Scope:** Applies to all external CLI calls from §3.7 — web research, script validation, ensemble reasoning queries. Does NOT apply to Gemini Imagen calls (which receive only descriptive text prompts, not user data).

---

## 9. Progressive Fallback Strategy

### 9.1 Philosophy

Start with maximum Claude reliance. Add code only when Claude proves unreliable at a specific, identified step. Each script addresses exactly one failure point.

### 9.2 Anticipated Fallback Points

| Step | Risk of Claude Failure | Fallback Script | Estimated Size |
|---|---|---|---|
| PII pre-flight filtering | N/A — required from day 1 | `pii_guard.sh` | ~80 lines |
| OAuth token refresh | Medium — depends on MCP implementation | `refresh_gmail_token.sh` | ~20 lines |
| Email sending (SMTP) | Medium — if Gmail MCP is read-only | `send_briefing.py` | ~50 lines |
| State file encrypt/decrypt | N/A — required for OneDrive sync | `vault.sh` | ~30 lines |
| State file date parsing | Low — Claude handles dates well | None expected | — |
| Email classification | Very low — Claude's strength | None expected | — |
| Structured extraction | Very low — Claude's strength | None expected | — |
| Alert threshold evaluation | Very low — simple comparisons | None expected | — |
| Briefing synthesis | Very low — Claude's primary capability | None expected | — |
| Email content pre-processing | Low — Claude handles HTML/threads inline | `email_prefilter.sh` | ~40 lines |

### 9.3 Fallback Decision Process

```
1. Run catch-up 5+ times relying solely on Claude
2. If a specific step fails > 20% of the time:
   a. Document the failure mode
   b. Write a minimal script addressing ONLY that step
   c. Update CLAUDE.md to call the script at that step
   d. Log the decision in ~/OneDrive/Artha/state/audit.md
3. If Claude eventually becomes reliable at that step:
   a. Remove the script
   b. Revert to Claude-native behavior
```

### 9.4 Script Conventions (If Needed)

- Location: `~/OneDrive/Artha/scripts/`
- Language: Python 3 (installed on macOS by default)
- Dependencies: Standard library only (no pip installs if possible)
- Credentials: Read from macOS Keychain via `security` CLI
- Input: stdin or command-line arguments
- Output: stdout (for Claude to read) or direct file write
- Logging: Append to `~/OneDrive/Artha/state/audit.md`
- Error handling: Print error message and exit with non-zero code

---

## 10. Cost Model

### 10.1 Per Catch-Up Estimated Cost

| Component | Tokens (est.) | Cost (est.) |
|---|---|---|
| CLAUDE.md + prompts (cached) | ~10K input | ~$0.01 (90% cache hit) |
| State files (read) | ~36K input | ~$0.04 (90% cache hit) |
| Email batch (~50 emails) | ~25K input | ~$0.25 |
| Calendar events | ~2K input | ~$0.02 |
| Processing + reasoning | ~20K output | ~$0.30 |
| Briefing generation | ~3K output | ~$0.05 |
| **Total per catch-up** | | **~$0.67** |

### 10.2 Monthly Projection

| Cadence | Catch-ups/month | Monthly cost |
|---|---|---|
| Daily | ~30 | ~$20 |
| Every other day | ~15 | ~$10 |
| 3x/week + weekends | ~18 | ~$12 |
| Weekly summaries (Opus extended thinking) | ~4 | ~$8 |
| On-demand queries (~10/month) | ~10 | ~$5 |
| **Projected total** | | **~$25–35** |

Well under the $50/month budget target.

### 10.3 Multi-LLM Cost Routing

Tasks delegated to Gemini CLI and Copilot CLI consume their free quotas instead of Claude API tokens:

| Task Category | CLI Used | Monthly Volume | Claude Cost (if not routed) | Actual Cost |
|---|---|---|---|---|
| Web research (Visa Bulletin, Zillow, NHTSA, etc.) | Gemini | ~10–15 calls | ~$2–3 | $0 (free quota) |
| URL summarization | Gemini | ~5–10 calls | ~$1–2 | $0 (free quota) |
| Script/config validation | Copilot | ~3–5 calls | ~$0.50–1 | $0 (free quota) |
| Visual generation (Imagen) | Gemini | ~2–5 calls | N/A (Claude can't do this) | $0 (free quota) |
| Ensemble reasoning (extra queries) | Gemini + Copilot | ~2–4 calls | N/A (new capability) | $0 (free quota) |
| **Monthly savings from routing** | | | **~$3–6** | **$0** |

**Effective monthly projection with multi-LLM routing:** ~$22–30 (vs. ~$25–35 Claude-only).

**Quota monitoring:** If Gemini or Copilot free quotas are exhausted, those tasks automatically fall back to Claude (cost increases but functionality preserved). Track quota usage in `health-check.md`.

### 10.4 Cost Monitoring

- Track actual cost via Anthropic API usage dashboard
- `~/OneDrive/Artha/state/health-check.md` maintains estimated cost per run
- CLAUDE.md instructs Artha to alert if daily cost exceeds $5 or monthly cost exceeds $50
- If costs exceed target: reduce domain prompt verbosity, increase catch-up interval, or limit email batch size
- Track Gemini/Copilot quota usage to ensure free tier is not exhausted

---

## 11. Directory Bootstrap

### 11.1 Initial Setup Script

This is the one-time setup sequence (run manually):

```bash
# Create Artha directory structure in OneDrive
mkdir -p ~/OneDrive/Artha/{prompts,state,briefings,summaries,config,scripts,visuals}

# Create CLAUDE.md loader (thin — Claude Code auto-reads this)
cat > ~/OneDrive/Artha/CLAUDE.md << 'EOF'
# Artha Loader
Read and follow ALL instructions in Artha.md in this directory.
Do not proceed without reading Artha.md first.
EOF

# Create Artha.md (content from Section 2)
# [Author manually based on Section 2 specification]

# Create domain prompt files (content from Section 6)
touch ~/OneDrive/Artha/prompts/{comms,immigration,finance,kids,travel,health,home,calendar,shopping,learning,social,digital,goals,boundary,insurance,vehicle,estate}.md

# Create initial state files with empty frontmatter
for domain in immigration finance kids health home calendar goals memory audit health-check; do
  cat > ~/OneDrive/Artha/state/$domain.md << 'EOF'
---
domain: DOMAIN_NAME
last_updated: 1970-01-01T00:00:00-08:00
last_catch_up: 1970-01-01T00:00:00-08:00
alert_level: none
version: 1
---

## Current Status
Not yet initialized. Run first catch-up to populate.

## Recent Activity
(none)
EOF
done

# Create settings file with OneDrive sync config
cat > ~/OneDrive/Artha/config/settings.md << 'EOF'
---
briefing_email: ved@gmail.com
alert_email: ved@gmail.com
work_hours_start: "08:00"
work_hours_end: "18:00"
work_days: [Mon, Tue, Wed, Thu, Fri]
timezone: America/Los_Angeles
sync:
  provider: onedrive
  path: ~/OneDrive/Artha
  encrypt_before_sync: true
  encryption_key_location: keychain
---

## Email Accounts
- ved@gmail.com / mi.vedprakash@gmail.com (primary — Gmail API)
- vedprakash.m@outlook.com (direct — MS Graph API, no forwarding needed)
- icloud.com (forwarding to Gmail — T-1B.1.2, Apple has no public API)
EOF

# Install age encryption
brew install age

# Generate age keypair and store in macOS Keychain
AGE_KEY=$(age-keygen 2>/dev/null)
AGE_PUBKEY=$(echo "$AGE_KEY" | grep 'public key:' | awk '{print $NF}')
AGE_PRIVKEY=$(echo "$AGE_KEY" | grep -v '^#')
echo "$AGE_PRIVKEY" | security add-generic-password -a artha -s age-key -w
echo "age recipient (public key): $AGE_PUBKEY"
echo "Add this to ~/OneDrive/Artha/config/settings.md under age_recipient"

# Create pii_guard.sh pre-flight PII filter (see §8.6)
cat > ~/OneDrive/Artha/scripts/pii_guard.sh << 'PIIEOF'
#!/bin/bash
# pii_guard.sh — pre-flight PII filter for Artha
# Usage: pii_guard.sh [scan|filter] < input
# scan  — detect only, exit 1 if PII found
# filter — detect and replace with [PII-FILTERED-*] tokens
# See §8.6 of artha-tech-spec.md for full specification
set -euo pipefail
# [Implementation per §8.6 detection patterns table]
PIIEOF
chmod +x ~/OneDrive/Artha/scripts/pii_guard.sh

# Create vault.sh helper script
cat > ~/OneDrive/Artha/scripts/vault.sh << 'VAULTEOF'
#!/bin/bash
# vault.sh — encrypt/decrypt sensitive state files for OneDrive sync
set -euo pipefail
STATE_DIR="$(dirname "$0")/../state"
AGE_KEY=$(security find-generic-password -a artha -s age-key -w 2>/dev/null)
SENSITIVE_FILES=(immigration finance insurance estate health audit)

case "${1:-}" in
  decrypt)
    for f in "${SENSITIVE_FILES[@]}"; do
      [[ -f "$STATE_DIR/$f.md.age" ]] && \
        echo "$AGE_KEY" | age -d -i /dev/stdin "$STATE_DIR/$f.md.age" > "$STATE_DIR/$f.md"
    done
    echo "Decrypted ${#SENSITIVE_FILES[@]} sensitive state files."
    ;;
  encrypt)
    PUBKEY=$(grep 'age_recipient:' "$(dirname "$0")/../config/settings.md" | awk '{print $2}')
    for f in "${SENSITIVE_FILES[@]}"; do
      [[ -f "$STATE_DIR/$f.md" ]] && \
        age -r "$PUBKEY" "$STATE_DIR/$f.md" > "$STATE_DIR/$f.md.age" && \
        rm "$STATE_DIR/$f.md"
    done
    echo "Encrypted ${#SENSITIVE_FILES[@]} sensitive state files."
    ;;
  *) echo "Usage: vault.sh [decrypt|encrypt]"; exit 1 ;;
esac
VAULTEOF
chmod +x ~/OneDrive/Artha/scripts/vault.sh

echo "Artha directory structure created at ~/OneDrive/Artha/"
echo "OneDrive will sync to all connected devices."
echo "Next: Author Artha.md, configure Gmail MCP, run first catch-up"
```

### 11.2 Gmail MCP Setup

1. Go to https://console.cloud.google.com/
2. Create project: "Artha Personal"
3. Enable Gmail API + Google Calendar API
4. Create OAuth 2.0 credentials (Desktop application)
5. Download client credentials JSON
6. Run OAuth flow to get refresh token:
   ```bash
   # Use the MCP tool's built-in OAuth flow, or:
   # https://developers.google.com/gmail/api/quickstart/python
   ```
7. Store credentials in macOS Keychain:
   ```bash
   security add-generic-password -a "artha" -s "gmail-client-id" -w "$CLIENT_ID"
   security add-generic-password -a "artha" -s "gmail-client-secret" -w "$CLIENT_SECRET"
   security add-generic-password -a "artha" -s "gmail-refresh-token" -w "$REFRESH_TOKEN"
   ```
8. Configure Claude Code MCP (in `~/.claude/mcp.json` or project-level):
   ```json
   {
     "mcpServers": {
       "gmail": {
         "command": "npx",
         "args": ["@anthropic/gmail-mcp"],
         "env": {
           "GMAIL_OAUTH_CLIENT_ID": "$(security find-generic-password -a artha -s gmail-client-id -w)",
           "GMAIL_OAUTH_CLIENT_SECRET": "$(security find-generic-password -a artha -s gmail-client-secret -w)",
           "GMAIL_OAUTH_REFRESH_TOKEN": "$(security find-generic-password -a artha -s gmail-refresh-token -w)"
         }
       }
     }
   }
   ```
   > ⚠️ Note: Shell expansion in MCP config may not be supported. If not, use a wrapper script that reads from Keychain and exports env vars before launching the MCP server.

### 11.3 Microsoft Graph Direct Email & Calendar Fetch

Direct API read via MS Graph — no forwarding required. Token is live as of 2026-03-08.

**Email:** Build `scripts/msgraph_fetch.py` per T-1B.1.1. Fetches `GET /me/mailFolders/inbox/messages?$filter=receivedDateTime ge {since}`. Output: same JSONL schema as `gmail_fetch.py` with `"source": "outlook"` field. Runs in parallel with `gmail_fetch.py` at catch-up Step 3.

**Calendar:** Build `scripts/msgraph_calendar_fetch.py` per T-1B.1.6. Fetches `/me/calendars/{id}/calendarView`. Output: same JSONL schema as `gcal_fetch.py` with `"source": "outlook_calendar"`. Runs in parallel with `gcal_fetch.py` at catch-up Step 3.

**Why direct API over forwarding:**
- No silent failure modes (forwarding can silently stop; API returns explicit HTTP errors)
- Source attribution built-in (`"source": "outlook"`) — routing rules can use it
- Microsoft email stays in Microsoft infrastructure — no cross-platform routing
- Single OAuth token already covers email + calendar + To Do

**Apple iCloud Mail:** No public API available — forwarding to Gmail is still required. See T-1B.1.2.

---

## 12. Governance & Evolution Framework

Artha is a living system: new domains, data sources, MCP servers, and AI capabilities will be added as trust and utility grow. This section defines the lifecycle processes that keep the system coherent, extensible, and self-improving.

### 12.1 Component Registry (`registry.md`)

`~/OneDrive/Artha/config/registry.md` is the system manifest — a single source of truth for what Artha consists of. Artha reads it at startup to understand its own topology.

```markdown
---
registry_version: 1
last_reviewed: 2026-03-07
next_review: 2026-06-07
---

## Prompts
| File | Domain | FR | Version | Status | Sensitivity | Last Updated |
|---|---|---|---|---|---|---|
| immigration.md | Immigration | FR-2 | 1.0 | active | critical | 2026-03-07 |
| finance.md | Finance | FR-3 | 1.0 | active | high | 2026-03-07 |
| kids.md | Kids & Education | FR-4 | 1.0 | active | standard | 2026-03-07 |
| comms.md | Communications | FR-1 | 1.0 | active | standard | 2026-03-07 |

## State Files
| File | Encrypted | Sensitivity | Schema Version | Last Migrated |
|---|---|---|---|---|
| immigration.md | .age | critical | 1 | — |
| finance.md | .age | high | 1 | — |
| kids.md | no | standard | 1 | — |
| health-check.md | no | standard | 1 | — |
| memory.md | no | standard | 1 | — |
| audit.md | .age | high | 1 | — |

## MCP Servers
| Server | Version | OAuth Scope | Status | Health Check | Added |
|---|---|---|---|---|---|
| Gmail MCP | [per TD-1] | gmail.readonly | active | connection test on each catch-up | Phase 1A |
| Calendar MCP | [per setup] | calendar.readonly | active | connection test on each catch-up | Phase 1A |

## Hooks
| Hook | Trigger | Script/Action | Fallback | Status |
|---|---|---|---|---|
| vault-decrypt | PreToolUse (state/*) | vault.sh decrypt | CLAUDE.md instruction | active |
| vault-encrypt | Stop | vault.sh encrypt | CLAUDE.md instruction | active |

## Scripts
| Script | Purpose | Version | Lines | Added | Removal Criteria |
|---|---|---|---|---|---|
| vault.sh | encrypt/decrypt sensitive state | 1.0 | ~30 | Phase 1A | N/A — permanent |
| pii_guard.sh | pre-flight PII filter | 1.0 | ~80 | Phase 1A | N/A — permanent |

## Slash Commands
| Command | Function | Defined In |
|---|---|---|
| /catch-up | Full catch-up workflow | CLAUDE.md |
| /status | Show health-check + per-domain freshness | CLAUDE.md |
| /goals | Goal scorecard | CLAUDE.md |
| /domain [name] | Deep-dive into single domain | CLAUDE.md |
| /cost | API cost summary for current billing period | CLAUDE.md |

## External CLIs
| CLI | Purpose | Cost | Version | Status |
|---|---|---|---|---|
| gemini | Web research, URL summarization, Imagen visuals | Free quota | — | active |
| copilot (gh copilot) | Script/config validation, code review | Free quota | — | active |

## Action Channels
| Channel | Mechanism | Human Gate | Trust Level |
|---|---|---|---|
| Email (briefing) | Gmail MCP `gmail.send` | Auto (catch-up) | Level 0+ |
| Email (compose) | Gmail MCP `gmail.send` | Always | Level 1+ |
| WhatsApp | URL scheme (`open "https://wa.me/..."`) | OS-enforced (user taps send) | Level 1+ |
| Calendar (create) | Google Calendar MCP (write scope) | Approve | Level 1+ |
| Visual (generate) | Gemini Imagen CLI | None (generation only) | Level 0+ |

## Config Files
| File | Purpose | Location |
|---|---|---|
| contacts.md | Contact lists for messaging/greetings | ~/OneDrive/Artha/config/ |
| occasions.md | Festival/occasion calendar + visual styles | ~/OneDrive/Artha/config/ |
```

**Maintenance:** Update `registry.md` whenever a component is added, updated, or retired. The `next_review` date triggers a quarterly self-audit.

### 12.2 CLAUDE.md Change Management

CLAUDE.md is the most critical file in the system — changes must be deliberate.

**Versioning:**
```markdown
# In CLAUDE.md header:
---
claude_md_version: 1.0
last_modified: 2026-03-07
changelog:
  - v1.0 (2026-03-07): Initial authoring — identity, workflow, routing, privacy rules
  - v1.1 (2026-03-14): Added /status slash command, expanded immigration routing
---
```

**Change process:**
1. **Document the change** — what's changing and why (in the changelog entry)
2. **Canary run** — after modifying CLAUDE.md, run a catch-up on a known email set and compare output to previous run's briefing. Verify: correct routing, no regressions, alert thresholds still fire
3. **Rollback** — if the canary run produces incorrect results, revert via OneDrive version history (30-day retention) or `git` if version-controlled
4. **Log** — append the change to `audit.md`: `[timestamp] CLAUDE_MD_UPDATE | v1.0 → v1.1 | reason: added /status command`

**Separation of concerns:**
- **CLAUDE.md:** Identity, workflow orchestration, routing rules, privacy rules, slash commands, output format
- **Domain prompts:** Domain-specific extraction, alert thresholds, briefing contribution, PII allowlists
- **Rule of thumb:** If it applies to ALL domains, it goes in CLAUDE.md. If it applies to ONE domain, it goes in the domain prompt.

### 12.3 Domain Lifecycle

#### Adding a New Domain

Checklist (target: <1 hour from decision to first catch-up):

```
☐ 1. Create prompt file: ~/OneDrive/Artha/prompts/{domain}.md
      Use §6.1 template: Purpose, Extraction Rules, Alert Thresholds,
      State Update Rules, Briefing Contribution, Sensitivity & Redaction,
      Known Senders, PII Allowlist
☐ 2. Create state file: ~/OneDrive/Artha/state/{domain}.md
      Include YAML frontmatter with domain, sensitivity, access_scope,
      version: 1
☐ 3. Determine encryption: if sensitivity is high/critical,
      add to SENSITIVE_FILES array in vault.sh
☐ 4. Update CLAUDE.md routing table: add sender/subject patterns
      that route to this domain
☐ 5. Update pii_guard.sh: if this domain has unique PII-like patterns
      that should be allowlisted, add to the domain prompt's PII Allowlist
☐ 6. Update registry.md: add rows to Prompts and State Files tables
☐ 7. Bootstrap state: manually populate known current state or run
      a targeted email search to seed the state file
☐ 8. Canary run: trigger a catch-up and verify the new domain
      receives at least one email routing correctly
☐ 9. Log: append to audit.md — DOMAIN_ADDED | {domain} | {date}
```

#### Updating a Domain Prompt

1. Bump version in `registry.md`
2. Edit the prompt file
3. Canary run on known emails that should route to this domain
4. Log change to `audit.md`

#### Splitting a Domain

When a domain becomes too large (>50KB state file or >20 extraction rules):

1. Create two new domain prompts with focused scopes
2. Create two new state files, migrating relevant data from the original
3. Update routing rules in CLAUDE.md (split the sender patterns)
4. Mark original domain as `retired` in `registry.md`
5. Keep original state file as archive for 90 days, then delete

#### Retiring a Domain

1. Mark as `status: retired` in `registry.md`
2. Remove routing rules from CLAUDE.md
3. Archive state file to `~/OneDrive/Artha/archive/{domain}.md`
4. Keep prompt file for reference but rename to `{domain}.md.retired`
5. Log to `audit.md`

### 12.4 MCP Server Onboarding

When adding a new MCP server (e.g., Outlook MCP, Plaid, Home Assistant):

#### Evaluation Criteria

| Question | Requirement |
|---|---|
| Does this data source justify real-time MCP vs. email-based parsing? | Email parsing costs $0 in infrastructure. MCP adds OAuth maintenance, credential storage, connection monitoring. Add MCP only if email parsing is ≥30% unreliable or the data source has no email signal. |
| What OAuth scopes are required? | Minimum necessary. Read-only preferred. Document exact scopes. |
| What data does this MCP send to the Claude API? | All data from MCP flows through Claude API (ephemeral). Ensure no raw PII passes without `pii_guard.sh` coverage. |
| Cost impact? | Free tier vs. paid API. Monthly projection at planned query frequency. Must fit within $55/month total budget. |
| Community vs. Anthropic-provided? | Prefer Anthropic-published MCP servers. Community servers require: source code review, no phone-home / telemetry, pinned version. |

#### Onboarding Steps

```
☐ 1. Evaluate per criteria table above — document decision in audit.md
☐ 2. Install MCP server and configure credentials in macOS Keychain
☐ 3. Add MCP config to Claude Code's MCP configuration file
☐ 4. Test connection: verify tool availability in Claude Code session
☐ 5. Update pii_guard.sh if the new data source introduces new PII patterns
☐ 6. Update affected domain prompts — add extraction rules for new data format
☐ 7. Add to registry.md MCP Servers table
☐ 8. Update CLAUDE.md catch-up workflow if the MCP adds a new fetch step
☐ 9. Canary run: verify data flows correctly through catch-up
☐ 10. Monitor: track connection success rate in health-check.md for 2 weeks
```

#### MCP Health Monitoring

Each MCP server gets a health entry in `health-check.md`:
```yaml
mcp_servers:
  gmail:
    status: healthy
    last_success: 2026-03-07T19:32:00-08:00
    failure_count_30d: 0
    avg_response_ms: 1200
  calendar:
    status: healthy
    last_success: 2026-03-07T19:32:02-08:00
    failure_count_30d: 1
    avg_response_ms: 800
```

### 12.5 Data Source Addition Procedures

#### Adding a New Email Account

Gmail accounts are the primary data ingestion pathway. Adding a new account:

```
☐ 1. Determine integration pattern:
      a. Forward to primary Gmail (simplest — no MCP change)
      b. Add as additional query scope in existing Gmail MCP (if supported)
      c. Add a second Gmail MCP instance (if needed for separate OAuth)
☐ 2. If forwarding: set up auto-forward from new account to primary Gmail
      Update CLAUDE.md or domain prompts with new sender patterns
☐ 3. If new MCP instance: follow §12.4 MCP Onboarding
      Configure OAuth for the new account
      Add to MCP config as a named tool (e.g., gmail_work, gmail_personal)
☐ 4. Update routing rules in CLAUDE.md — new sender patterns
☐ 5. Update pii_guard.sh if new account introduces new PII patterns
☐ 6. Update registry.md
☐ 7. Update settings.md Email Accounts section
☐ 8. Canary run: verify emails from new account are fetched and routed
```

**Recommended pattern for most cases:** Forward to primary Gmail. This requires zero infrastructure changes — only a routing rule update in CLAUDE.md.

#### Adding a Document Repository

For Phase 2 document access (tax returns, brokerage statements, legal docs):

```
☐ 1. Choose access method:
      a. Local folder (~/OneDrive/Artha/documents/) — simplest
      b. OneDrive folder via filesystem MCP — requires MCP
      c. External service API (e.g., DocuSign) — requires MCP
☐ 2. Define document processing policy per §8.2.5:
      Extract-and-discard? Full storage? Summary only?
☐ 3. Map documents to domains — which domain prompt handles which doc type
☐ 4. Update domain prompts with document extraction rules
☐ 5. Update pii_guard.sh — documents often contain dense PII
      May need document-specific PII patterns
☐ 6. Update registry.md
☐ 7. Test with a sample document — verify extraction, PII filtering,
      and state file update
```

#### Adding a New API Integration (Phase 2+)

For direct API access (e.g., Plaid for bank data, Home Assistant for IoT):

```
☐ 1. Evaluate: Does this replace unreliable email parsing? (§12.4 criteria)
☐ 2. Check for existing MCP server — prefer existing implementations
☐ 3. If MCP exists: follow §12.4 MCP Onboarding
☐ 4. If no MCP: evaluate custom MCP server vs. script-based alternative
      Custom MCP = significant maintenance burden — avoid if possible
☐ 5. Define data freshness requirements — how often to poll?
☐ 6. Update cost model (§10) with new API costs
☐ 7. Update catch-up workflow if new API adds a fetch step
```

### 12.6 State File Schema Evolution

State files carry `version: N` in their frontmatter. When the schema must change:

**When to bump schema version:**
- Adding a required frontmatter field
- Renaming a section
- Restructuring the prose format
- Changing sensitivity or encryption classification

**Migration strategy (Claude-assisted):**

```
Claude reads the old state file and rewrites it in the new format.
No custom migration scripts — Claude IS the migration engine.

CLAUDE.md instruction:
"When you encounter a state file with version < {current_version},
 migrate it to the current schema before processing.
 Preserve all data. Log migration to audit.md."
```

**Migration steps:**
1. Update the domain prompt with new schema expectations
2. Bump `version` expectation in the domain prompt (e.g., `Expected schema version: 2`)
3. Add migration instruction to CLAUDE.md: what changed, how to migrate
4. On next catch-up, Claude reads the old-version state file, applies migration, writes new-version state file
5. Log: `[timestamp] SCHEMA_MIGRATION | {domain} | v1 → v2 | fields_added: [...] | data_preserved: true`
6. Update `registry.md` with new schema version

**Backward compatibility:** Claude can read any version — the migration instruction tells it how to upgrade. No breaking changes possible because Claude interprets the format, not a rigid parser.

### 12.7 Script Lifecycle

#### Script Addition

When the progressive fallback strategy (§9) triggers a new script:

1. Document failure mode in `audit.md` (>20% failure rate over 5+ runs)
2. Write minimal script per §9.4 conventions
3. Add to `registry.md` Scripts table with version, removal criteria
4. Update CLAUDE.md to call the script at the specific step
5. Update bootstrap script (§11.1) to create the script on new installs
6. Update validation checklist (§14.1)

#### Script Updates

When a script needs modification (e.g., new PII pattern for `pii_guard.sh`):

1. Document the change reason
2. Bump version in `registry.md`
3. Test: for `pii_guard.sh`, run `scan` mode against synthetic test data
4. Log: `[timestamp] SCRIPT_UPDATE | pii_guard.sh | v1.0 → v1.1 | reason: added ITIN-W pattern`

#### Script Removal

Quantitative criteria for removing a fallback script:

```
A fallback script MAY be removed when:
  - Claude succeeds at the equivalent step ≥95% over ≥20 consecutive runs
  - No critical failures (data loss, PII leak) in the last 30 days
  - The script's functionality can be replicated by CLAUDE.md instruction

A fallback script MUST NOT be removed if:
  - It handles security-critical functions (vault.sh, pii_guard.sh)
  - It handles credential operations (refresh_gmail_token.sh)
```

### 12.8 Feedback & Learning Loop

Artha improves through structured feedback, not ad hoc corrections.

#### Correction Protocol

When Artha makes an error, the user says one of:
- "Artha, that was wrong — [correct information]"
- "Artha, stop alerting me about [topic]"
- "Artha, [entity] is actually [correction]"

**Claude processes the correction:**
1. Acknowledge the error
2. Update the relevant state file immediately
3. Log to `memory.md` → Corrections section with timestamp, domain, what was wrong, what's correct
4. If it's a routing error: propose a CLAUDE.md routing rule update
5. If it's an extraction error: propose a domain prompt update

#### Accuracy Tracking

`health-check.md` maintains accuracy metrics per domain:

```yaml
accuracy:
  overall_30d: 94%
  by_domain:
    immigration:
      correct: 47
      incorrect: 2
      accuracy: 95.9%
      last_error: "missed EAD receipt number update"
    finance:
      correct: 120
      incorrect: 8
      accuracy: 93.8%
      last_error: "classified insurance premium as subscription"
    kids:
      correct: 85
      incorrect: 3
      accuracy: 96.6%
      last_error: "wrong child attributed to grade alert"
```

**How accuracy is tracked:** After each catch-up, CLAUDE.md instructs Claude to ask: "Anything I got wrong in this briefing?" If the user provides a correction, decrement the domain's accuracy. If no correction, increment. This is lightweight — no external tool, no database. Just YAML in health-check.md.

#### Prompt Improvement Cycle

Monthly (or when domain accuracy drops below 90%):

1. Review `memory.md` → Corrections for the domain
2. Identify patterns in errors (common misclassifications, missed extractions)
3. Update the domain prompt's extraction rules, routing patterns, or alert thresholds
4. Bump prompt version in `registry.md`
5. Canary run to validate improvement
6. Clear resolved corrections from `memory.md` (move to `memory.md` → Archived Corrections)

#### Memory Pruning

`memory.md` grows over time. Quarterly review:

- **Preferences** — keep indefinitely (they don't expire)
- **Decisions** — keep for 1 year, then archive decisions whose context is no longer relevant
- **Corrections** — archive after the prompt/routing fix is confirmed working (see cycle above)
- **Patterns Learned** — keep indefinitely (they compound)

**Archive destination:** `~/OneDrive/Artha/archive/memory-{year}-{quarter}.md`

### 12.9 AI Feature Adoption

Claude Code evolves rapidly. Artha should absorb useful new capabilities without requiring spec rewrites.

#### Quarterly Review Process

Every 3 months (aligned with `registry.md` `next_review` date):

```
1. SCAN — Review Claude Code release notes since last review
   Check for: new tool types, MCP protocol changes, new hook triggers,
   model improvements, cost changes, new capabilities (computer use,
   multi-modal, structured output, etc.)

2. EVALUATE — For each relevant new feature:
   | Question | Answer |
   |---|---|
   | What Artha capability does this improve? | [map to FR or workflow step] |
   | Does it replace an existing workaround? | [yes/no — which?] |
   | Cost impact? | [cheaper/same/more expensive] |
   | Migration effort? | [trivial/moderate/significant] |
   | Fallback if feature is removed? | [CLAUDE.md instruction / script] |

3. ADOPT — For features worth adopting:
   a. Update CLAUDE.md or domain prompts to use the new feature
   b. Add fallback instruction for when the feature isn't available
   c. Update registry.md
   d. Canary run
   e. Log: FEATURE_ADOPTED | {feature} | {date} | {reason}

4. DEFER — For features not yet worth the migration cost:
   Log in registry.md: "Evaluated {feature} on {date}. Deferred: {reason}."
```

#### Feature Flags via CLAUDE.md

CLAUDE.md includes a capabilities section that acts as feature flags:

```markdown
## Capabilities (Feature Flags)
# Set to `enabled` to use, `disabled` to fall back to alternative.
# Set to `disabled` if your Claude Code version doesn't support the feature.
parallel_tool_invocation: enabled    # fallback: sequential fetch
hooks: enabled                       # fallback: CLAUDE.md instructions
sub_agents: disabled                 # Phase 2 — enable when ready
built_in_memory: enabled             # fallback: memory.md only
extended_thinking: enabled           # fallback: standard reasoning
```

Claude reads this section and adapts its behavior accordingly. This decouples feature availability from CLAUDE.md instruction rewrites.

#### Capability Dependency Graph

```
/catch-up workflow
├── vault.sh decrypt ─── [hook: PreToolUse] OR [CLAUDE.md instruction]
├── Gmail fetch ─────── [MCP: Gmail] ── requires: OAuth, gmail.readonly
├── Calendar fetch ──── [MCP: Calendar] ── requires: OAuth, calendar.readonly
│   └── (parallel with Gmail) ── requires: parallel_tool_invocation: enabled
├── PII filter ──────── [script: pii_guard.sh] ── requires: bash, age
├── Domain routing ──── [CLAUDE.md routing table] + [domain prompts]
│   ├── State file read ── [filesystem] ── requires: OneDrive sync
│   ├── State file write ── [filesystem] ── requires: OneDrive sync
│   └── Redaction (Layer 2) ── [CLAUDE.md §8.2 rules]
├── Briefing synthesis ── [CLAUDE.md format spec] + [extended_thinking for weekly]
├── Email delivery ────── [MCP: Gmail send] OR [script: send_briefing.py]
└── vault.sh encrypt ──── [hook: Stop] OR [CLAUDE.md instruction]
```

### 12.10 Hook & Slash Command Governance

#### Adding a New Hook

1. Identify the trigger point (PreToolUse, PostToolUse, Stop, etc.)
2. Define the action (script execution, CLAUDE.md instruction, or both)
3. Define the fallback (in case the hook mechanism isn't available)
4. Test: verify hook fires correctly on the trigger
5. Add to `registry.md` Hooks table
6. Add fallback instruction to CLAUDE.md

**Future hook candidates:**
| Hook | Trigger | Purpose | Phase |
|---|---|---|---|
| pii-scan | PreToolUse (Gmail) | Auto-run pii_guard.sh before processing emails | Phase 2 |
| cost-check | PostToolUse (any API) | Track token usage per catch-up | Phase 2 |
| backup-state | Stop | Copy state files to secondary backup | Phase 3 |

#### Adding a New Slash Command

1. Define the command, its purpose, and expected output format
2. Add to CLAUDE.md under the Slash Commands section
3. Add to `registry.md` Slash Commands table
4. Test: verify the command produces correct output
5. Log to `audit.md`

**Future slash command candidates:**
| Command | Purpose | Phase |
|---|---|---|
| /add-domain [name] | Interactive domain creation wizard | Phase 2 |
| /review | Trigger quarterly governance review | Phase 2 |
| /accuracy | Show per-domain accuracy metrics | Phase 1B |
| /migrate [domain] | Force schema migration on a state file | Phase 2 |

### 12.11 Autonomy Elevation Implementation

The PRD defines three trust levels (§10). This section specifies the technical implementation.

**Tracking mechanism in `health-check.md`:**

```yaml
autonomy:
  current_level: 0
  level_0_start: 2026-03-07
  days_at_current_level: 0
  elevation_criteria:
    level_1:
      required_days: 30
      critical_alert_false_positives: 0      # must stay 0
      briefing_accuracy_30d: null             # must reach ≥95%
      all_recommendations_reviewed: false     # must be true
      eligible: false
    level_2:
      required_days: 60                       # 60 days at Level 1
      action_acceptance_rate: null            # must reach ≥90%
      pre_approved_categories: []             # user defines explicitly
      eligible: false
```

**Elevation process:**
1. CLAUDE.md instructs Claude to evaluate elevation criteria on each catch-up
2. When all criteria are met, Claude surfaces: "Artha has met all Level 1 criteria over the past 30 days. Recommend elevation to Advisor level. [Approve] [Defer]"
3. User approves → Claude updates `autonomy.current_level` in health-check.md, logs to audit.md
4. User defers → log deferral, re-check in 7 days

**Demotion process:**
1. If a critical false positive occurs at Level 1+ → auto-demote to Level 0
2. If action acceptance rate drops below 70% at Level 2 → alert and recommend demotion
3. User can manually demote at any time: "Artha, go back to Level 0"
4. Demotion resets the elevation clock — criteria must be re-met from scratch

---

## 13. Cross-Device Access

### 13.1 OneDrive as the Primary Cross-Device Layer

With state files stored in `~/OneDrive/Artha/`, all devices with OneDrive installed see the same state automatically:

| Device | Access | Writable | Encrypted State |
|---|---|---|---|
| Mac (primary) | Full — Claude Code runs here | Yes (sole writer) | Decrypted during catch-up, encrypted at rest |
| iPhone | OneDrive app → browse standard state files | Read-only | Cannot decrypt `.age` files — by design |
| Windows | OneDrive native sync → full folder access | Read-only (by convention) | Can decrypt `.age` if `age` key provisioned |

**Conflict prevention:** Mac is the sole writer. iPhone and Windows never modify state files. OneDrive conflict detection is unnecessary because there is exactly one writer.

### 13.2 Passive Access (Email)

Every catch-up emails the briefing to the configured address. This email is accessible on:
- iPhone Mail app
- Windows Outlook (work laptop)
- Any device with email access

No setup needed beyond configuring the briefing email address.

### 13.3 Interactive Access (Claude.ai Project)

For on-demand queries from iPhone:

1. Create a Claude.ai Project named "Artha"
2. Upload system instructions (simplified version of CLAUDE.md for read-only queries)
3. State is always fresh — OneDrive syncs after every catch-up:
   - **Standard state files** (calendar, kids, goals, etc.) are readable directly from OneDrive on iPhone
   - Copy relevant state content into the Claude.ai Project knowledge base periodically
   - Or: manually paste current state into the Claude.ai chat when querying
4. Query from iPhone: "What's my immigration status?" → Claude answers from state

**Sensitivity filter:**
- Only `sensitivity: standard` state files are readable on iPhone (plaintext in OneDrive)
- `sensitivity: high` or `critical` files are `.age` encrypted — unreadable on iPhone by design
- iPhone queries for immigration or finance get: "This information is only available during a Mac catch-up session."

**Advantages over previous snapshot approach:**
- State is always current (no manual snapshot generation/upload)
- No stale data risk — OneDrive syncs within minutes of catch-up completion
- No custom snapshot generation script needed
- Sensitivity enforcement is structural (encryption), not procedural (exclusion lists)

---

## 14. Testing & Validation

### 14.1 Phase 1A Validation Checklist

Before declaring Phase 1A complete:

- [ ] `claude` opens successfully in `~/OneDrive/Artha/` and reads CLAUDE.md
- [ ] "catch me up" triggers the full workflow
- [ ] `vault.sh decrypt` successfully decrypts sensitive state files
- [ ] Gmail MCP connects and fetches emails
- [ ] Calendar MCP connects and fetches events
- [ ] At least one email is correctly routed to a domain prompt
- [ ] At least one state file is correctly updated
- [ ] `vault.sh encrypt` successfully re-encrypts sensitive state files
- [ ] `pii_guard.sh scan` detects synthetic SSN in test email body
- [ ] `pii_guard.sh scan` detects synthetic credit card number in test email
- [ ] `pii_guard.sh filter` replaces SSN/CC with `[PII-FILTERED-*]` tokens
- [ ] PII allowlist correctly exempts USCIS receipt numbers (IOE-*, SRC*, LIN*)
- [ ] Catch-up halts cleanly when `pii_guard.sh` returns non-zero
- [ ] PII filter audit entries appear in audit.md with correct format
- [ ] OneDrive syncs updated files within 5 minutes
- [ ] Standard state files readable on iPhone via OneDrive app
- [ ] Encrypted `.age` files visible but unreadable on iPhone (expected)
- [ ] Briefing is synthesized and displayed in terminal
- [ ] Briefing is emailed to configured address
- [ ] health-check.md is updated with run timestamp
- [ ] audit.md logs all actions
- [ ] Second catch-up only processes NEW emails (no duplicates)
- [ ] On-demand query ("What's my immigration status?") answers from state files
- [ ] Total catch-up time < 3 minutes
- [ ] No custom code deployed beyond vault.sh + pii_guard.sh (all logic in CLAUDE.md + prompts)
- [ ] `registry.md` accurately reflects all deployed components
- [ ] CLAUDE.md has version field and changelog
- [ ] Gemini CLI responds to web search queries (test with Visa Bulletin)
- [ ] Copilot CLI responds to validation queries (test with vault.sh review)
- [ ] Multi-LLM routing rules in CLAUDE.md correctly delegate research to Gemini
- [ ] CLI health status tracked in health-check.md
- [ ] WhatsApp URL scheme opens WhatsApp with pre-filled message on Mac
- [ ] Gmail MCP can send composed emails (not just briefings)
- [ ] Action proposals display in terminal with correct schema format
- [ ] Action approval/rejection logged to audit.md
- [ ] contacts.md and occasions.md are created and populated
- [ ] Gemini Imagen generates a test visual successfully
- [ ] Generated visuals saved to ~/OneDrive/Artha/visuals/ and sync via OneDrive

### 14.2 Ongoing Validation

After each catch-up, mentally verify:
1. **Accuracy:** Did the briefing match reality? Were emails correctly classified?
2. **Completeness:** Were any important emails missed or miscategorized?
3. **Alerts:** Were all threshold crossings detected? Any false positives?
4. **State:** Are state files accurate and up-to-date?

Log discrepancies in `~/OneDrive/Artha/state/memory.md` under Corrections. This trains Artha's behavior over time via CLAUDE.md context.

---

## 7.16 `/diff` Command Workflow *(v2.1 — F15.51)*

When user invokes `/diff`:

1. Read `health-check.md` → get `last_catch_up` timestamp
2. For each state file in Always + Active tiers:
   a. Compare current content against briefing archive from last catch-up date
   b. Extract structured deltas: new entries, removed entries, changed fields
3. Display formatted diff:

```markdown
# State Changes Since Last Catch-Up (March 6, 7:15 PM)

## Immigration
+ New alert: EAD renewal 90 days out (added March 7)
Δ Priority date: moved from 2019-01-01 to 2019-01-15

## Finance
+ New bill: PSE $247 due March 20
Δ Checking balance: $X,XXX → $X,XXX (−$XXX)

## Kids
+ Parth: AP Language essay returned (B+)
Δ Parth cumulative grade: B → B+

## No Changes: Home, Travel, Health, Learning
```

4. If no changes: "No state changes since last catch-up."
5. Does NOT trigger a full catch-up — reads only local state files (no email fetch).

---

## 7.17 Weekend Planner Workflow *(v2.1 — F8.7)*

Triggered automatically on Friday catch-ups (after 12 PM) or manually via user request.

1. Read calendar for Saturday–Sunday
2. Read open_items.md for items with effort ≤30 minutes
3. Read weather forecast (if available via Gemini web search)
4. Generate weekend optimization:

```markdown
## 🏖️ Weekend Planner — March 8–9, 2026

### Saturday
- ✅ Trisha soccer tournament 9 AM–12 PM (confirmed)
- 💡 Good window: 1–3 PM (nothing scheduled) → Suggested: review Parth’s college list (45 min, open 12 days)
- 6 PM: Family dinner at home

### Sunday
- Morning: open
- 💡 Power Half Hour: 3 quick tasks (pay PSE bill, review insurance doc, update goals)
- 4 PM: Parth SAT prep

### ⚠️ Weekend Deadlines
- PSE bill due Monday → pay this weekend
```

---

## 7.18 Canvas LMS API Fetch Specification *(v2.1 — F4.10)*

**Phase 2B integration.** Replaces email-only parsing for school grades and assignments.

**Auth:** Canvas REST API with institutional access token (per-student). Token stored in macOS Keychain (`canvas-token-parth`, `canvas-token-trisha`).

**Endpoints:**
| Endpoint | Data | Used By |
|---|---|---|
| `GET /api/v1/courses` | Active courses list | Kids domain |
| `GET /api/v1/courses/:id/assignments` | Assignment list with due dates, scores | Kids domain → open_items |
| `GET /api/v1/courses/:id/enrollments` | Current grades per course | Kids domain → GPA tracking |
| `GET /api/v1/users/:id/missing_submissions` | Missing/late assignments | Kids domain → 🔴 alerts |

**Fetch pattern:** Pull on each catch-up. Store per-child summary in `state/kids.md` under `### Academics` section.

**Script:** `scripts/canvas_fetch.py` — accepts `--student parth|trisha`, outputs JSON to stdout for Claude ingestion.

**Error handling:** If Canvas API is unavailable, fall back to email-only parsing with health-check warning.

---

## 7.19 Apple Health XML Import Specification *(v2.1 — F6.9)*

**Phase 2B integration.** Parses HealthKit export for wellness goal tracking.

**Data flow:**
1. User exports Health data from iPhone → `Export.zip` → unzip → `apple_health_export/export.xml`
2. Alternatively: Apple Shortcut automates weekly export to `~/OneDrive/Artha/data/health-export.xml`
3. `scripts/parse_apple_health.py` extracts summary metrics:
   - Steps (daily average, 7-day trend)
   - Sleep (duration, quality if available)
   - Heart rate (resting average)
   - Workouts (count, type, duration)
   - Weight (latest, 30-day trend)
4. Output written to `state/health-metrics.md` for Goal Engine consumption

**Script:** `scripts/parse_apple_health.py` (already exists in workspace — enhance with structured output)

**Privacy:** Raw health XML is NOT synced to OneDrive. Only aggregated metrics in `health-metrics.md` are persisted. XML file processed locally and deleted after parsing.

**Fetch pattern:** Weekly cadence. If export file is >7 days old, surface reminder in briefing.

---

## 15. Phased Implementation Summary

### Phase 1A — Core Setup (Weeks 1–2)

| Task | Deliverable | Effort |
|---|---|---|
| Author CLAUDE.md + Artha.md | `~/OneDrive/Artha/CLAUDE.md` (loader) + `~/OneDrive/Artha/Artha.md` (full instructions) | 2–3 hours |
| Create directory structure | `~/OneDrive/Artha/*` (including `visuals/`) | 15 minutes |
| Install `age` + generate keypair | Keychain entry + vault.sh | 30 minutes |
| Configure OneDrive sync | Verify sync on all devices | 30 minutes |
| Set up Google Cloud OAuth | Client credentials (include `gmail.send` + calendar write scope) | 1 hour |
| Configure Gmail MCP | Working email fetch + send capability | 1–2 hours |
| Configure Calendar MCP | Working calendar fetch + event creation | 30 minutes |
| Verify Gemini CLI | `gemini --version` + test web search query | 15 minutes |
| Verify Copilot CLI | `gh copilot --version` + test validation query | 15 minutes |
| Add multi-LLM routing rules to CLAUDE.md | Cost-aware routing per §3.7.2 | 30 minutes |
| Author immigration prompt | `~/OneDrive/Artha/prompts/immigration.md` | 1 hour |
| Author finance prompt | `~/OneDrive/Artha/prompts/finance.md` | 1 hour |
| Author kids prompt | `~/OneDrive/Artha/prompts/kids.md` | 1 hour |
| Author comms prompt | `~/OneDrive/Artha/prompts/comms.md` | 30 minutes |
| Create `pii_guard.sh` | Pre-flight PII filter per §8.6 | 1 hour |
| Create `safe_cli.sh` | Outbound PII wrapper per §8.7 | 30 minutes |
| Define PII allowlists in domain prompts | Allowlist section per domain prompt | 30 minutes |
| Configure Claude Code hooks | PreToolUse (decrypt) + Stop (encrypt) | 30 minutes |
| Define custom slash commands in CLAUDE.md | /catch-up, /status, /goals, /domain, /cost, /health | 30 minutes |
| Create `registry.md` | Component registry per §12.1 (including CLI tools + action channels) | 30 minutes |
| Add CLAUDE.md versioning | Version field + changelog per §12.2 | 15 minutes |
| Create `contacts.md` | Contact groups for messaging per §7.4.3 | 30 minutes |
| Create `occasions.md` | Festival/occasion calendar per §7.4.6 | 15 minutes |
| Bootstrap initial state files | `~/OneDrive/Artha/state/*.md` with known data | 1–2 hours |
| Configure email delivery | Briefing emails working | 1 hour |
| Test WhatsApp URL scheme | Verify `open "https://wa.me/..."` opens WhatsApp with pre-filled text | 15 minutes |
| Test Gemini Imagen | Generate a test visual and verify output | 15 minutes |
| First catch-up run | End-to-end validation including encrypt/decrypt + PII filter + multi-LLM routing | 1 hour |
| Validate Gmail MCP | Verify OAuth, search, read, send all work. Budget 3–5 hours if MCP needs debugging. Have Python fallback ready (§9.2). | 1–3 hours |
| Test pii_guard.sh interception | Test TD-19: can PreToolUse hooks filter MCP responses? | 1 hour |
| Configure OneDrive selective sync | Exclude plaintext sensitive state files from sync (only .age syncs) per §8.5 | 30 minutes |
| Resolve TD-18 (Archana's email) | Determine if Archana's email needs forwarding | 30 minutes |
| Iterate on CLAUDE.md + Artha.md | Refine based on first runs | 2–3 hours |
| **Total Phase 1A** | | **~21–25 hours** |

### Phase 1B — High-Value Domains (Weeks 3–5)

| Task | Deliverable |
|---|---|
| Refine immigration prompt based on real emails | Improved extraction accuracy |
| Set up Outlook forwarding to Gmail | Unified email ingestion |
| Author remaining Phase 1 domain prompts | travel.md, health.md, home.md |
| Bootstrap state files with real data | Populated world model |
| Validate alert thresholds | Tuned to real signal patterns |
| Set up Claude.ai Project for iPhone | Mobile read-only access |
| Test ensemble reasoning on immigration question | Verify 3-LLM ensemble produces higher-quality answer |
| Populate contacts.md with family/friend contacts | Ready for festival greetings |
| Test end-to-end visual greeting workflow | Generate Diwali card + compose email + send |

### Phase 1C — Goals + Expansion (Weeks 6–8)

| Task | Deliverable |
|---|---|
| Author goals prompt | `~/OneDrive/Artha/prompts/goals.md` |
| Define first 5 goals in state file | `~/OneDrive/Artha/state/goals.md` |
| Author boundary prompt | `~/OneDrive/Artha/prompts/boundary.md` |
| Weekly summary format | First weekly summary generated |
| Cost validation | Actual vs. projected API costs |

### Phase 2A — Supercharge: Intelligence Amplification *(v2.0)*

Eighteen enhancement workstreams implementing PRD v3.9 accepted items, ordered by dependency. See `artha-tasks.md` for detailed task breakdown.

#### Workstream K — Data Integrity Guard (P0, Week 1)

| Task | Deliverable | Effort |
|---|---|---|
| Implement pre-decrypt backup in vault.sh | Layer 1: `.md.bak` creation before decrypt overwrites | 1 hour |
| Implement post-write verification in Artha.md | Layer 2: YAML frontmatter + size checks after each write | 1 hour |
| Implement net-negative write guard in Artha.md | Layer 3: Field count comparison + user confirmation on data loss | 2 hours |
| Add backup retention and cleanup logic | Auto-remove `.bak` after successful encrypt | 30 min |
| Test data integrity with simulated scenarios | Crash mid-session, stale .age, corrupt decrypt | 1 hour |

#### Workstream L — Bootstrap Command (P0, Week 1–2)

| Task | Deliverable | Effort |
|---|---|---|
| Add `/bootstrap` to Artha.md slash commands | Route to guided interview workflow | 30 min |
| Implement domain interview question generation | Derive questions from state file schemas §4.2–§4.11 | 2 hours |
| Implement answer validation + state file writing | Format validation, redaction reminders, write + verify | 2 hours |
| Add bootstrap detection to catch-up Step 4 | Surface warning when bootstrap-template files detected | 30 min |
| Create `state/dashboard.md` template | Dashboard state file per §4.12 | 30 min |

#### Workstream M — Pattern of Life Detection (P1, Week 2–3)

| Task | Deliverable | Effort |
|---|---|---|
| Add Behavioral Baselines section to memory.md | Schema per §4.7 | 30 min |
| Implement baseline data collection at end of catch-up | Update behavioral baselines after each run | 1 hour |
| Implement anomaly detection rules | 5 anomaly types per §7.7 | 2 hours |
| Add anomaly alerts to briefing synthesis | Surface deviations in briefing | 1 hour |

#### Workstream N — Signal:Noise Tracking (P1, Week 2)

| Task | Deliverable | Effort |
|---|---|---|
| Add Signal:Noise section to health-check.md | Schema per §4.5 | 30 min |
| Implement signal ratio calculation in catch-up | Count actionable vs total items | 1 hour |
| Implement noise source tracking | Identify and log top noise sources | 1 hour |
| Add marketing suppression rate to health-check | Track suppression effectiveness | 30 min |

#### Workstream O — Briefing Compression + Flash Briefing (P1, Week 3)

| Task | Deliverable | Effort |
|---|---|---|
| Implement flash briefing format | §5.1.2 format, ≤8 lines | 1 hour |
| Implement compression level selection logic | Auto-select based on user input + time gap + context pressure | 1 hour |
| Implement deep briefing with extended analysis | Cross-domain analysis, trend charts, scenarios | 1.5 hours |
| Update Artha.md with compression routing | Route "quick update" → flash, "full analysis" → deep | 30 min |

#### Workstream P — Context Window Pressure Management (P1, Week 3)

| Task | Deliverable | Effort |
|---|---|---|
| Add Context Window Pressure section to health-check.md | Schema per §4.5 | 30 min |
| Implement token estimation after each workflow step | Approximate usage tracking | 1.5 hours |
| Implement pressure-level mitigation actions | Green/yellow/red/critical responses per §7.14 | 2 hours |
| Add pressure event counter to weekly trends | 7-day rolling window analysis | 30 min |

#### Workstream Q — OAuth Token Resilience (P1, Week 2)

| Task | Deliverable | Effort |
|---|---|---|
| Add OAuth Token Health section to health-check.md | Schema per §4.5 | 30 min |
| Enhance Pre-flight step 0 with per-provider token check | Lightweight API call per provider | 1 hour |
| Implement proactive expiry warnings | MS Graph <7 days, refresh failure tracking | 1 hour |
| Add self-healing with logging for slow/failed refreshes | Automatic retry + escalation | 1 hour |

#### Workstream R — Email Volume Scaling (P1, Week 3–4)

| Task | Deliverable | Effort |
|---|---|---|
| Implement email volume tier detection | Count-based tier selection per §7.8 | 1 hour |
| Implement Medium tier processing | Aggressive marketing suppression + reduced token cap | 1.5 hours |
| Implement High tier two-pass processing | P0 domains first, then rest with summary extraction | 2 hours |
| Implement Extreme tier three-pass processing | P0 → P1 → P2 count-only | 1.5 hours |
| Add volume tier logging to health-check.md | Track which tiers are triggered | 30 min |

#### Workstream S — Life Scorecard (P1, Week 4)

| Task | Deliverable | Effort |
|---|---|---|
| Define scoring rubric per dimension | 7 dimensions with 1–10 scales per §7.9 | 1 hour |
| Implement scorecard generation in Sunday catch-up | Calculate scores from state data | 2 hours |
| Add Life Scorecard section to dashboard.md | Weekly snapshot | 30 min |
| Implement week-over-week trend detection | Arrow indicators for movement | 30 min |

#### Additional Phase 2A Workstreams

| Workstream | Key Deliverable | Effort |
|---|---|---|
| Compound Signal Detection (§7.6) | Cross-domain correlation rules in catch-up Step 6 | 3 hours |
| Consequence Forecasting (§7.10) | "IF YOU DON’T" alerts in briefing | 2 hours |
| Pre-Decision Intelligence Packets (§7.11) | On-demand research compilation | 3 hours |
| Session Quick-Start Routing (§7.12) | Context-aware session routing | 2 hours |
| Stale State Detection (§7.12) | 7-day inactivity warning | 1 hour |
| Coaching Engine (goals prompt) | Accountability patterns, obstacle anticipation, nudge format | 3 hours |

#### v4.0 Phase 2A Workstreams *(v2.1)*

| Workstream | Key Deliverable | Effort |
|---|---|---|
| **T: Briefing Intelligence Amplification** | Week Ahead Preview, calibration questions (§5.1), PII footer, monthly retro (§5.3), privacy audit | 6 hours |
| **U: Scheduling & Task Intelligence** | Calendar-aware slot suggestions (F8.6), Weekend Planner (§7.17), effort estimates + Power Half Hour (F15.48), micro-tasks (F15.53) | 8 hours |
| **V: Goal Engine Expansion** | Goal Sprint targets (F13.17), Goal Auto-Detection (F13.18), Decision Deadlines (§7.1 Step 7c) | 5 hours |
| **W: Conversational Intelligence** | `/diff` command (§7.16), Ask Archana delegation (F15.52), Teach Me mode (F15.54), NL state queries (F15.55) | 8 hours |
| **X: Family & Cultural Intelligence** | India TZ scheduling (F11.11), College Application Countdown — P0 (§4.4, F4.11) | 4 hours |

#### v4.0 Phase 2B Additions *(v2.1)*

| Item | Key Deliverable | Effort |
|---|---|---|
| Canvas LMS API (F4.10) | `canvas_fetch.py` + Kids state integration (§7.18) | 4 hours |
| Apple Health Import (F6.9) | Enhanced `parse_apple_health.py` + health-metrics state (§7.19) | 3 hours |
| Tax Season Automation (F3.13) | Tax prep workflow + document checklist in Finance prompt | 3 hours |
| Subscription ROI Tracker (F12.6) | Cost vs. usage analysis in Digital prompt | 2 hours |

#### v4.0 Phase 3 Additions *(v2.1)*

| Item | Key Deliverable | Effort |
|---|---|---|
| WhatsApp Business Bridge (F1.7) | Message context ingestion + URL-scheme send | 6 hours |
| Emergency Contact Wallet Card (F18.8) | PDF/image generator from contacts.md | 2 hours |

---

## 16. Open Design Decisions

These decisions will be resolved during Phase 1A implementation:

| # | Decision | Options | Resolution Criteria |
|---|---|---|---|
| TD-1 | Gmail MCP server selection | @anthropic/gmail-mcp vs. community MCP vs. custom | Whichever supports OAuth + search + read reliably |
| TD-2 | Email sending mechanism | Gmail MCP send vs. SMTP script vs. Apple Mail | First option that works; simplest wins |
| TD-3 | State snapshot for iPhone | ~~Manual upload vs. iCloud Drive vs. AirDrop~~ | **Resolved (v1.2):** OneDrive sync replaces manual snapshot upload. Standard state files are always current on all devices. Encrypted files unreadable on iPhone by design. |
| TD-4 | Keychain integration with MCP env vars | Direct shell expansion vs. wrapper script | Test if MCP config supports $() |
| TD-5 | Catch-up idempotency | Gmail `historyId` tracking vs. message ID tracking vs. timestamp only | **Recommended:** Use Gmail's `historyId` (each catch-up records the `historyId` after fetching; next catch-up starts from that point). This is Gmail's recommended approach for incremental sync. If the MCP server doesn't expose `historyId`, fall back to combined approach: timestamp-based search + per-domain deduplication by message ID in state file frontmatter (`last_email_processed_id`). |
| TD-6 | Immigration data redaction | All state files vs. immigration.md only | **Resolved (v1.1):** Expanded to all sensitive domains. Each domain has its own redaction rules (§8.2). Sensitivity classification in frontmatter controls output channel filtering. |
| TD-7 | Encrypted state tier | ~~`age` encryption vs. encrypted `.dmg` vs. FileVault-only~~ | **Resolved (v1.2):** `age` encryption required with OneDrive sync. Implemented as Phase 1 requirement. vault.sh handles encrypt/decrypt cycle. |
| TD-8 | OneDrive path per platform | `~/OneDrive/Artha` (Mac) vs. `C:\Users\ved\OneDrive\Artha` (Win) | Configure in settings.md. Mac path used for Claude Code; Windows path for manual access only. |
| TD-9 | PII filter false positive tuning | Strict (flag all matches) vs. Contextual (use allowlists + heuristics) | Start strict (Phase 1A). Track false positive rate in audit.md. If >5% of detections are false positives, expand allowlists or add contextual heuristics. |
| TD-10 | Governance review cadence | Quarterly vs. monthly vs. event-driven | Start quarterly (§12.9). If system changes rapidly in Phase 1, increase to monthly until stable. |
| TD-11 | Ensemble reasoning voting threshold | Unanimous agreement required vs. 2-of-3 majority vs. Claude tie-breaks | Start with Claude-as-synthesizer (§3.7.3). If disagreements are frequent, formalize voting weights per domain. |
| TD-12 | WhatsApp contact management | contacts.md only vs. macOS Contacts integration vs. vCard import | Start with contacts.md (simple, human-editable). Evaluate macOS Contacts API if contact list exceeds 50 entries. |
| TD-13 | Gmail MCP write scope | `gmail.send` only vs. `gmail.modify` (archive, label) | Start with `gmail.send` (briefing + compose). Add `gmail.modify` in Phase 2 if auto-archive is pre-approved at Trust Level 2. |
| TD-14 | Gemini Imagen output format | PNG vs. JPEG vs. WebP | Test PNG first (lossless, good for text overlays). Switch to JPEG if file sizes impact OneDrive sync. |
| TD-15 | Calendar write scope | Single calendar vs. multi-calendar | Start with primary personal calendar only. Add family/shared calendars if needed. |
| TD-16 | Gmail send safety mechanism | Direct MCP send vs. structured JSON payload (`outbox/action.json` → user review → send script) | If MCP send with trust level gates (Autonomy Framework) provides sufficient safety, use direct send. If prompt injection or accidental send risk is a concern, implement staged payload: Claude writes structured JSON to `outbox/`, user reviews, then a minimal script sends. Start with direct MCP send at Trust Level 1 (human-approved). |
| TD-17 | Claude Code portability strategy | Pure CLAUDE.md + Claude Code hooks (current) vs. thin model-agnostic orchestrator script | Current design intentionally couples to Claude Code for zero-custom-code simplicity (P1). All prompts and state files are already model-agnostic — only the execution shell (hooks, slash commands, `/memory`) is Claude Code-specific. Monitor Claude Code stability quarterly. If breaking changes occur or a superior CLI emerges, the portability surface is small: replace hook definitions, slash command mappings, and `/memory` references. No state or prompt migration needed. |
| TD-18 | Archana's email access | Forward Archana's email to Ved's Gmail vs. separate Gmail MCP query scope vs. shared inbox | Determine which of Archana's accounts (if any) send immigration, health, or school emails relevant to Artha. If she receives attorney correspondence, USCIS notices, or medical appointment confirmations at her own address, set up forwarding to Ved's Gmail (simplest) or add a second Gmail MCP query scope. Resolve during Phase 1A. |
| TD-19 | pii_guard.sh interception layer | Pre-persist validation (current) vs. PreToolUse hook response interception (ideal) vs. MCP proxy wrapper | Test whether Claude Code `PreToolUse` hooks can intercept and modify MCP tool responses before Claude ingests them. If yes, upgrade `pii_guard.sh` from pre-persist to true pre-flight. If not, current pre-persist approach is acceptable since Anthropic's ephemeral processing policy covers API transit. |
| TD-20 | Life Scorecard dimension weighting | Equal weights vs. user-configurable weights vs. adaptive weights | Start with equal weights (v2.0). If user consistently overrides or questions scores, add configurable weights in `memory.md` → `Coaching Preferences`. |
| TD-21 | Behavioral baseline minimum sample size | 10 catch-ups vs. 20 catch-ups vs. time-based (14 days) | Start with 10 catch-ups (§7.7). If baselines are too noisy, increase to 20 or add time-based minimum. |
| TD-22 | Net-negative write guard threshold | 20% field loss vs. 10% vs. any reduction | Start with 20% (§8.5.1 Layer 3). Track false positive rate — if guard triggers >3 times in 30 days on legitimate updates, adjust threshold upward. |
| TD-23 | Context pressure token estimation method | Approximate heuristic (tokens per file/email) vs. actual tokenizer count | Start with heuristic (§7.14). If pressure level misclassifications occur frequently, consider integrating `tiktoken` for actual counts. |
| TD-24 | Flash briefing auto-selection threshold | <4h since last run vs. <2h vs. configurable | Start with <4h (§7.13). Add to `memory.md` → `Preferences` if user wants to adjust. |
| TD-25 | Compound signal persistence | Ephemeral (briefing only) vs. persisted to dedicated state file | Start ephemeral (§7.6). If users want to review historical compound signals, add `state/signals.md` in a future release. |
| TD-26 | Canvas API token provisioning | Institutional SSO vs. personal access token vs. parent portal API *(v2.1)* | Depends on school district’s Canvas configuration. Test personal token first; if blocked, explore parent portal integration. |
| TD-27 | Apple Health export automation | Manual export vs. Apple Shortcut vs. `healthkit-to-csv` tool *(v2.1)* | Start with manual export. If cadence >2 weeks between exports, implement Shortcut automation. |
| TD-28 | Calibration question format | Free-text vs. structured [Y/N] vs. 1–5 scale *(v2.1)* | Start with Y/N + optional free text (§5.1). If response rate <30%, simplify to thumbs up/down. |
| TD-29 | /diff snapshot storage | State file hash vs. full copy vs. frontmatter timestamp only *(v2.1)* | Start with frontmatter timestamp comparison (§7.16). If insufficient, add per-field hashing. |
| TD-30 | College countdown milestone sources | Manual input vs. Common App calendar scrape vs. per-school deadlines *(v2.1)* | Start with manual input during /bootstrap. Automate with Gemini web search for Common App deadlines in Phase 2B. |

---

## 17. Testing Architecture

To support the automated testing requirements in PRD §14.4, Artha employs a Python-native testing framework.

### 17.1 Framework & Tooling

- **Testing Library:** `pytest` (standard Python testing framework).
- **Mocking:** `pytest-mock` for isolating scripts from the filesystem, network, and credential store.
- **Snapshot Testing:** `pytest-snapshot` or custom logic for "Golden File" validation.
- **Data Diffing:** `datadiﬀ` for granular comparison of extracted Markdown vs. expected snapshots.

### 17.2 Test Categories

| Category | Target | Method |
|---|---|---|
| **Unit** | `pii_guard.sh`, `vault.py`, `preflight.py` | Subprocess calls with varied inputs; mock environment variables and file paths. |
| **Integration** | `vault.py` round-trip | Full encrypt/decrypt cycle using a temporary directory and mock keyring. |
| **Extraction** | Domain Prompts (`prompts/*.md`) | "Golden File" snapshots: input JSONL → extraction engine → compare Markdown result. |
| **Integrity** | Net-Negative Write Guard | Verify that writes are blocked when proposed state loss exceeds 20%. |

### 17.3 "Golden File" Implementation

For each domain prompt:
1. **Fixture:** A `.jsonl` file containing representative mock emails.
2. **Reference:** A `.md` file containing the manually verified "correct" extraction result.
3. **Execution:** The test runner invokes the extraction logic (Claude API via safe wrapper) and compares the result to the reference.
4. **Failure:** Any deviation triggers a diff report; the developer must either fix the prompt or update the reference.

### 17.4 Security Regression Suite

The PII Guard test suite includes:
- **Positive Tests:** Strings containing real-format PII must be redacted.
- **Negative Tests:** Allowlisted patterns (USCIS receipts, Amazon orders) must NOT be redacted.
- **Boundary Tests:** PII at the start/end of strings, in headers, and in malformed contexts.

---

*Artha Tech Spec v2.1 — End of Document*

*"The entire application is a well-written instruction file. The data layer lives where the user lives — always fresh, always accessible, always encrypted where it matters. Nothing sensitive leaves the device. Three LLMs work together — the right model for the right task at the right cost. Now it learns your patterns, guards your data, and shows you what matters before you ask."*
