# Artha — Technical Specification
<!-- pii-guard: ignore-file -->

> **Version**: 3.29.0 | **Status**: Active Development | **Date**: April 2026
> **Author**: [Author] | **Classification**: Personal & Confidential
> **Implements**: PRD v7.14.0

> **⚠ Note on Example Data:** Personal names (Raj, Priya, Arjun, Ananya)
> and other identifiers in examples throughout this document are **fictional**.
> Your actual family data is configured in `config/user_profile.yaml`.

| Version | Date | Summary |
|---------|------|----------|
| v3.29.0 | 2026-04-09 | **OpenClaw Home Bridge (§29)**: 3-layer M2M transport (REST LAN / Telegram / file-buffer), HMAC-SHA256 security model, 4-command outbound + 4-event inbound contract, 7 new components, `bridge_health` observability block. Incorporated from `claw-bridge.md` v1.7.0 (archived). 61 bridge tests passing. Implements PRD v7.14.0. |
| v3.28.0 | 2026-04-08 | **Knowledge Graph v2.0 (§22)**: Second Brain Architecture. Five ingestion paths (knowledge/*.md, inbox/, SharePoint, state/work/*.md, ADO), eight governing principles, entity lifecycle stages, SQLite schema v4 (lifecycle_stage, excerpt_hash, change_source_ref, episodes table), source taxonomy with confidence contract, Leiden community clustering + Union-Find fallback, ghost entity detection & excerpt-hash staleness, 7 MCP tools surface, markdown stub contract, 950-token context budget (was 4,000). Implements PRD v7.13.0. |
| v3.27.0 | 2026-04-08 | EAR-3 SHIPPED: `scripts/agents/` with 4 domain agents (capital, logistics, readiness, tribe). `config/agents/schedules.yaml` cron registry (≤8 agent slots, §27 R13). `config/state_registry.yaml` state-file registry (§3.5 A2). 4 domain-specific guardrails added to §8 (CapitalAmountConfirmGR, LogisticsPIIBoundaryGR, ReadinessNoInferenceGR, TribeRateLimitGR). Anti-golden routing test suite added. Spec compaction: §5.1 briefing example, §8.7 safe_cli.sh, §11.1 setup.sh, §12.1 registry — all replaced with pointers to canonical files. |
| v3.26.0 | 2026-04-07 | SPEC CONSOLIDATION: Distilled 10 non-core spec files into core specs (§21–§28). §21.6–21.8 eval dimensions/scoring/SLAs from `eval.md`. §22.6–22.8 full entity model, API, performance targets from `kb-graph-design.md`. §23.6 AR-9 GA promotion from `ar9-completion-report.md`. §24.4–24.5 cross-reference rules and domain weight overrides from `data-quality-gate.md`. §25.11–25.14 baseline patterns, anti-patterns, context budgets, governance from `agent-fw.md`. NEW §27 EAR v2.0 Multi-Agent Composition (EAR-1–EAR-12) from `ext-agent-reloaded.md`. NEW §28 KB Population Strategy from `kb-population-plan.md`. Implements PRD v7.11.0. Originals archived to `.archive/specs/`. |
Full detailed changelog: see [CHANGELOG.md](../CHANGELOG.md)

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
| Domain prompts | `~/OneDrive/Artha/prompts/*.md` | Domain-specific extraction, alerting, and state update rules (24 domains; includes pets, renter overlay) |
| Domain registry | `~/OneDrive/Artha/config/domain_registry.yaml` | Authoritative manifest for all 24 domains — lazy-load flags, household filters, vault requirements |
| State files | `~/OneDrive/Artha/state/*.md` | Living world model — one Markdown file per domain |
| State templates | `~/OneDrive/Artha/state/templates/` | Blank starter files for new users (pets.md, and all domains) |
| Open items | `~/OneDrive/Artha/state/open_items.md` | Persistent action item list extracted from catch-ups; bridge to Microsoft To Do |
| Dashboard | `~/OneDrive/Artha/state/dashboard.md` | Life-at-a-glance snapshot — life pulse, active alerts, life scorecard; rebuilt each catch-up *(v2.0)* |
| Encrypted state | `~/OneDrive/Artha/state/*.md.age` | `age`-encrypted state for high/critical sensitivity domains |
| Briefing archive | `~/OneDrive/Artha/briefings/*.md` | Historical catch-up briefings (ISO-dated, sensitivity-filtered) |
| Summary archive | `~/OneDrive/Artha/summaries/*.md` | Historical weekly summaries |
| Entry point | `artha.py` | CLI entry point: `--setup` (interactive wizard), `--demo`, `--preflight`. Detects configured vs unconfigured state and routes accordingly. No auto-preflight on welcome. |
| Config | `config/user_profile.yaml` | Personal configuration — identity, family, integrations, encryption, system settings. Single source of truth. |
| Starter profile | `config/user_profile.starter.yaml` | Minimal 45-line first-run template (blank name/email forces fill-in). Used by wizard non-interactive path. |
| View scripts | `~/OneDrive/Artha/scripts/*_view.py` | Script-backed deterministic renderers: `dashboard_view.py`, `domain_view.py`, `status_view.py`, `goals_view.py` (`--scope personal/work/all`; direction:down metric bars), `items_view.py`, `scorecard_view.py`, `diff_view.py` |
| Goal state writer | `~/OneDrive/Artha/scripts/goals_writer.py` | Deterministic YAML writer for `state/goals.md` v2.0 schema; atomic writes via `write_state_atomic()`; CLI flags for all goal fields; exit codes 0–3 |
| Migration scripts | `~/OneDrive/Artha/scripts/migrate_state.py` | YAML front-matter schema migration DSL for state files |
| Action orchestrator | `~/OneDrive/Artha/scripts/action_orchestrator.py` | Single CLI integration point for the Action Layer: signal→compose→queue pipeline. Commands: `--run`, `--run --mcp`, `--approve`, `--reject`, `--defer`, `--approve-all-low`, `--show`, `--list`, `--expire`, `--health`. *(ACTIONS-RELOADED v1.3.0)* |
| Action queue | `~/.artha-local/actions.db` | Platform-local SQLite database for action proposal lifecycle (pending → approved → executed). NOT inside OneDrive workspace — avoids WAL/SHM sync corruption. Cross-machine propagation via the DUAL v1.3.0 bridge. |
| Scripts (if needed) | `~/OneDrive/Artha/scripts/` | Helper scripts: vault.py, backup.py, pipeline.py, etc. |
| OneDrive sync | Native OS integration | Cross-device state sync; Mac writes, iPhone/Windows read |
| Gmail MCP | Claude Code MCP config | Email access via OAuth (read-only) |
| Google Calendar MCP | Claude Code MCP config | Calendar access via OAuth (read-only) |

---

## 2. Instruction File Specification

Artha's behavior is defined by `Artha.md` — the primary instruction file. Claude Code auto-reads `CLAUDE.md` on session start, which delegates to `Artha.md`.

**Why two files:** Claude Code requires a file named `CLAUDE.md` in the project root. But Raj uses Claude Code for other projects too. A thin `CLAUDE.md` that loads `Artha.md` provides clean separation — the Artha-specific logic has its own named identity file, while Claude Code's auto-loading mechanism still works.

**`CLAUDE.md` (3-line loader):**
```markdown
# Artha Loader
Read and follow ALL instructions in Artha.md in this directory.
Do not proceed without reading Artha.md first.
```

**`Artha.md` (full instruction file):**

### 2.1 Structure

> See [config/Artha.md](../config/Artha.md) for the full instruction file loaded by Claude Code.
> Key sections: **Identity** · **Core Behavior** · **Catch-Up Workflow** (Steps 1–11) · **Routing Rules** (see §2.2) · **Output Format** (see §5) · **Domain Prompts list** · **Privacy Rules**.
> CLAUDE.md is a 3-line loader that auto-reads Artha.md. See §2.3 for design principles.


### 2.2 Routing Rules

The routing table maps email senders and subject patterns to domain prompts. Claude's native reasoning handles ambiguous cases — the table provides explicit routing for known senders.

| Pattern | Domain Prompt | Priority |
|---|---|---|
| From: `*@immigration-law.example.com` | `immigration.md` | 🔴 Critical |
| From: `*uscis.gov` | `immigration.md` | 🔴 Critical |
| Subject contains: `visa bulletin` | `immigration.md` | 🟠 Urgent |
| From: `*@parentsquare.com` | `kids.md` | Standard |
| From: `*@instructure.com` / Canvas | `kids.md` | 🟠 if grade alert |
| From: `*@pse.com` | `home.md` | Standard |
| From: `*@your-city*` | `home.md` | Standard |
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

**Purpose:** Read personal Gmail (configured in user_profile.yaml). Outlook/Hotmail email is fetched directly via MS Graph API — see §3.8. No forwarding required.

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
- **Fallback:** If package is deprecated/removed from npm, Artha degrades to "WorkIQ unavailable." Future alternative: direct MS Graph Calendar API via `scripts/connectors/msgraph_calendar.py`.

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
| `state/work/work-calendar.md` | Count+duration metadata only | ✅ (no titles/attendees) |

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
| County Tax | Public Lookup | None | `requests` | Property tax deadlines (Phase 1) |
| Canvas LMS | REST API | Developer Token | `requests` | School grades/assignments (Phase 2 Blocked) |
| OFX / FDX | Banking API | FI Credentials | `ofxtools` | Direct bank balance pull (Phase 2) |
| Microsoft Graph | REST API | OAuth2 | `msal` | Outlook Email + MS To Do sync |
| Home Assistant | Local REST API | LAN Token (keyring) | `requests` | Smart home status — **✅ LIVE (v8.2.0)** — 28 devices, 6 ecosystems. Connector: `scripts/connectors/homeassistant.py`. Skill: `scripts/skills/home_device_monitor.py`. Setup: `scripts/setup_ha_token.py`. LAN-gated (self-gating inside connector). Auth: `artha-ha-token` in macOS Keychain. Implements PRD F7.4, F12.5. |
| Passport Expiry | `state/immigration.md` | vault-decrypted | stdlib | Alert at 180/90/60 days (Phase 1 — F15.66) |
| Subscription Monitor | `state/digital.md` | none | stdlib | Price change + trial-to-paid detection (Phase 1 — F15.67) |
| RSS Feeds | Public RSS/Atom URLs | None | `urllib` + `xml.etree` | Regulatory/news feeds (USCIS, etc.) — disabled by default (Phase 1 — F15.68) |
| Financial Resilience | `state/finance.md` | vault-decrypted | stdlib | Burn rate, emergency runway, single-income stress (Phase 1 — F15.100) |
| Apple Health | Local ZIP/XML export | None (local only) | `xml.etree.ElementTree.iterparse` | 16 HK quantity types, memory-efficient streaming parse, opt-in (Phase 1 — F15.111) |

### 3.5b Home Assistant Connector Architecture *(v8.2.0 — ARTHA-IOT Waves 1+2)*

**Overview:** HA is a universal adapter layer. One connector, one token, one local REST endpoint covers all 28 devices across 6 ecosystems (Ring, Apple, Amazon, Google, Sonos, Gecko) without separate per-ecosystem integrations.

**Component map:**

| Component | File | Role |
|-----------|------|------|
| Connector | `scripts/connectors/homeassistant.py` | Fetch entity states via `/api/states`. LAN-gated internally. Writes `tmp/ha_entities.json` as side-effect for skill. |
| Skill | `scripts/skills/home_device_monitor.py` | Reads `tmp/ha_entities.json`. Deterministic threshold checks. Constructs `DomainSignal` objects directly (bypasses LLM mediation). |
| Setup wizard | `scripts/setup_ha_token.py` | 8-step interactive wizard: validate URL, store token in keyring, update `connectors.yaml`, create `tmp/.nosync`. |
| Preflight | `scripts/preflight.py` → `check_ha_connectivity()` | P1 non-blocking check. Warns if HA unreachable but does not halt catch-up. |
| State file | `state/home_iot.md` | Machine-owned companion to `state/home.md`. Refreshed each catch-up. Never edited manually. |

**Security & privacy model:**
- Token stored in macOS Keychain only (`artha-ha-token`). Never in YAML, logs, or JSONL.
- LAN-only by default (`requires_lan: true` in `connectors.yaml`). Connector self-gates in `fetch()` via TCP probe.
- Hard-excluded domains (code-enforced, not configurable): `camera`, `media_player`, `tts`, `stt`, `conversation`, `persistent_notification`, `update`.
- `device_tracker.*` entities stripped to `home`/`not_home`/`unknown` only — GPS coordinates never touch disk. Presence tracking opt-in via `user_profile.yaml → integrations.homeassistant.presence_tracking`.
- Entity `friendly_name` values pass through PII guard before state write.
- `tmp/ha_entities.json` is ephemeral (deleted Step 18). `tmp/.nosync` prevents OneDrive sync of the `tmp/` directory.

**Signal routing (deterministic — no LLM in critical path):**

| Signal Type | Trigger | Severity | Action Type |
|-------------|---------|----------|-------------|
| `security_device_offline` | Ring/lock/alarm offline >2h | 🔴 Critical | `instruction_sheet` (friction: high) |
| `device_offline` | Monitored device offline >2h | 🟠 Urgent | `instruction_sheet` (friction: standard) |
| `energy_anomaly` | Consumption >30% above 7-day avg | 🟠 Urgent | `instruction_sheet` (friction: low) |
| `supply_low` | Printer toner/drum <20% | 🟡 Heads-up | `instruction_sheet` (friction: low) |
| `spa_maintenance` | Spa temp deviation >5°F or error code | 🟠 Urgent | `instruction_sheet` (friction: standard) |

**Data flow per catch-up:**
1. `preflight.py` → `check_ha_connectivity()` (non-blocking)
2. `pipeline.py` → `homeassistant.fetch()` → stdout JSONL + `tmp/ha_entities.json`
3. `skill_runner.py` → `home_device_monitor.pull()` reads `tmp/ha_entities.json` → `parse()` → `DomainSignal` objects
4. `skill_runner.py` → `_route_deterministic_signals()` → `ActionComposer.compose()` (Python, no AI)
5. AI processes `state/home_iot.md` + `state/home.md` together during Step 7 (home domain)
6. Cleanup: `tmp/ha_entities.json` deleted

**Pipeline health check fix (v8.2.0):** `run_health_checks()` in `pipeline.py` now passes `fetch_cfg` kwargs (including `ha_url`) to `health_check()`, matching the same kwargs-passing pattern used in `run_fetch()`. Without this fix, health check returned `False` because `ha_url` was not passed.

**Phase 2 (device control — future):** Gated behind 30-day read-only production period. Requires explicit user opt-in (`phase2_device_control: true`) and separate security review. Action handler skeleton: `scripts/actions/homeassistant_service.py`. Service allowlist enforced in code (not YAML). Security signal `security_travel_conflict` (cross-domain: travel planned + security camera offline) planned for Wave 3 correlator skill.

### 3.6 Claude Code Capabilities Utilization's native capabilities beyond basic MCP tools.

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

Step 2 (SEQUENTIAL): pii_guard.py filter on email batch

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
| Examples | "Stop alerting about Spirit Week" | "Raj prefers briefings in bullet format, not prose" |
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
| County Tax Assessor | "Claude web fetch" | `gemini "county property tax assessment [parcel]"` | Annually |
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
gemini "Generate a beautiful Diwali greeting card image with diyas, rangoli, and the message 'Happy Diwali from our family'. Style: warm, traditional, festive." --output ~/OneDrive/Artha/visuals/diwali-2026.png

# Generate a birthday card
gemini "Generate a birthday card for a teenager who loves technology and basketball. Include 'Happy Birthday!' text." --output ~/OneDrive/Artha/visuals/birthday-2026.png
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
- Token: `.tokens/msgraph-token.json` (authenticated as configured in user_profile.yaml)
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
| `scripts/connectors/msgraph_email.py` | ✅ live | Fetch Outlook inbox via unified connector protocol (`fetch()` + `health_check()`; refactored from standalone `msgraph_fetch.py`) |
| `scripts/connectors/msgraph_calendar.py` | ✅ live | Fetch Outlook Calendar events via unified connector protocol (`fetch()` + `health_check()`; refactored from standalone `msgraph_calendar_fetch.py`) |
| `scripts/connectors/onenote.py` | ✅ live | Fetch OneNote pages via unified connector protocol (`fetch()` + `health_check()`; refactored from standalone `msgraph_onenote_fetch.py`) |

**Email fetch API pattern (`connectors/msgraph_email.py`):**
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

Raj actively uses OneNote for planning, notes, and reference material that is highly relevant to the Artha state layer. Examples:
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
- Content: HTML → plain text strip (same `_HTMLStripper` pattern as `connectors/msgraph_email.py`)
- Integrated into catch-up Step 4 as an optional parallel fetch (P1 — content enrichment, not blocking)

**To enable:** add `Notes.Read` to `_SCOPES` in `setup_msgraph_oauth.py`, then run `--reauth`.

**Catch-up Step 4 parallel fetch pattern:**
```
Step 4 (all 4 in parallel):
  gmail_fetch.py --since {last_run}                          → JSONL (source: implicit gmail)
  gcal_fetch.py --from {today} --to {today+7d}               → JSONL (source: implicit google_calendar)
  connectors/msgraph_email.fetch(since=last_run)              → JSONL (source: "outlook")
  msgraph_calendar_fetch.py --from {today} --to {today+7d}   → JSONL (source: "outlook_calendar")

Phase 2 (after T-1B.1.7):
  msgraph_onenote_fetch.py --modified-since {last_run}       → JSONL (source: "onenote")

Merge: all email JSONL feeds → unified email list (route by domain)
       all calendar JSONL feeds + WorkIQ events → unified event list (dedup by field-merge: summary from personal, Teams link from work, set source: "work+personal" for matches; unmatched work events tagged source: "work_calendar" with 💼 prefix. Merged events flagged merged=true — excluded from conflict detection.)
       onenote JSONL → state layer enrichment (domain routing by notebook name)
```

**Health check:** `python3 scripts/setup_msgraph_oauth.py --health`
**Preflight:** P1 checks in `preflight.py` — token valid + `connectors/msgraph_email.health_check()` + `connectors/msgraph_calendar.health_check()` + WorkIQ combined detection+auth (P1, non-blocking — see §3.2b)
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
| `scripts/connectors/imap_email.py` | ✅ live | Fetch iCloud/IMAP inbox via unified connector protocol (refactored from standalone `icloud_mail_fetch.py`) |
| `scripts/connectors/caldav_calendar.py` | ✅ live | Fetch iCloud Calendar via CalDAV unified connector protocol (refactored from standalone `icloud_calendar_fetch.py`) |

**Setup (one-time, ~5 min):**
```bash
# 1. Generate app-specific password at account.apple.com
# 2. Store in Keychain
python3 scripts/setup_icloud_auth.py
# 3. Verify
python3 scripts/setup_icloud_auth.py --health
python3 -c "from scripts.connectors.imap_email import health_check; print(health_check())"
python3 -c "from scripts.connectors.caldav_calendar import health_check; print(health_check())"
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
connectors/imap_email.fetch(since=last_run)            → JSONL (source: "icloud")
connectors/caldav_calendar.fetch(from=today, to=today+7d) → JSONL (source: "icloud_calendar")
```

**Preflight:** P1 checks in `preflight.py` — `setup_icloud_auth.py --health` (gating) + `connectors/imap_email.health_check()` + `connectors/caldav_calendar.health_check()` (only if auth check passes)

---

### 3.10 Channel Bridge (ACB v2.1) *(v2.4)*

**Purpose:** Platform-agnostic channel bridge with three layers: push notifications, interactive commands, and multi-LLM Q&A. Delivers Artha intelligence to Telegram (and future platforms) via a single adapter protocol. The listener is Artha's always-on mobile interface — no terminal required.

**Architecture:** `scripts/channels/` adapter package mirrors `scripts/connectors/`. The `channels.yaml` registry mirrors `connectors.yaml`. Each adapter implements the `ChannelAdapter` protocol: `send_message()`, `send_message_get_id()`, `send_document()`, `delete_message()`, `health_check()`, `poll()`.

**Layer 1 — Post-Catch-Up Push** (Step 20):
- Invoked by: `python scripts/channel_push.py` as Step 20 of catch-up workflow
- Format: flash briefing (≤500 chars, or `max_push_length` from config)
- Per-recipient access scope filter (`full / family / standard`) applied before send
- `pii_guard.filter_text()` on every outbound message — mandatory, no bypass
- Push deduplication: daily marker `state/.channel_push_marker_{YYYY-MM-DD}.json` prevents duplicate pushes across OneDrive-synced machines (12h window)
- Pending push queue: `state/.pending_pushes/` — retried on next catch-up if API unreachable; 24h expiry
- Non-blocking: failures log warnings to `state/audit.md`, catch-up continues
- Feature flag: `config/channels.yaml → defaults.push_enabled: false` (opt-in)

**Layer 2 — Interactive Commands:**
- Invoked by: `python scripts/channel_listener.py --channel telegram`
- On inbound message: sender whitelist → command normaliser → rate limit → scope filter → PII redact → send
- **Command normaliser** (`_normalise_command()`): 45+ aliases mapped to canonical commands via `_COMMAND_ALIASES` dict. Longest-match-first strategy. Single-letter shortcuts (`s/a/t/q/d/g/?`). Slash optional. Hyphens optional. Spaces OK.
- **Read commands:** `/status`, `/alerts`, `/tasks`, `/quick`, `/domain [name]`, `/goals`, `/diff [period]`, `/dashboard`, `/help`, `/catch-up`
- **Write commands** *(v2.4)*: `/items_add <description> [P0/P1/P2] [domain] [deadline]` — parses free-form text, finds next OI number, appends to `open_items.md`, audit logged. `/items_done OI-NNN [resolution]` — marks item as done, adds `date_resolved`, audit logged.
- **Domain listing** *(v2.4)*: `/domain` with no args shows categorised list of all 18 domains (direct-read vs. encrypted/AI-routed)
- **Encrypted domain routing** *(v2.4)*: `/domain finance` (and other encrypted domains) routes through `_ask_llm()` with context from prompt files. Vault decrypted in-process, auto-relocked after.
- **State diff** *(v2.4)*: `/diff [7d|24h|Nd]` compares state file mtimes against last catch-up time or custom period
- Staleness indicator appended to every response
- `listener_host` in `channels.yaml` prevents multi-instance conflicts on multi-machine setups
- Cross-platform asyncio with `threading.Event` shutdown (Windows-compatible)
- Auto-start: VBScript at Windows Startup folder, launchd plist on macOS

**Layer 3 — Multi-LLM Q&A** *(v2.4)*:
- **Free-form questions:** any message not matching a command alias is treated as a natural language question
- **LLM failover chain:** Claude Code CLI (~16.5s) → Gemini CLI (~26.1s) → Copilot CLI (~39.1s). Priority order based on benchmarked latency.
- **CLI configuration:** Claude `--model sonnet`, Gemini `--yolo`, Copilot `--model claude-sonnet-4 --yolo -s`
- **Workspace context:** CLIs invoked from `cwd=_ARTHA_DIR` (Artha workspace), giving them access to CLAUDE.md, skills, prompt files, and vault
- **Context assembly:** domain prompts (processing rules) + state file summaries + open items assembled into system prompt for each query
- **Vault safety:** `_vault_relock_if_needed()` called in `finally` block after every CLI call — checks for decrypted .md siblings of .age files, runs `vault.py encrypt` if found
- **Structured output:** system prompt requests numbered lists for ranked/sequential items, Unicode bullets (•) for unordered items, one-line direct answer first, no Markdown — plain text with Unicode only
- **Ensemble mode:** `aa <question>` or `ask all <question>` triggers all CLIs in parallel. Responses consolidated via Claude Haiku (`claude-haiku-4-5-20251001`). Fallback: primary CLI for consolidation if Haiku unavailable; longest individual response if consolidation fails.
- **Thinking ack:** "💭 Thinking…" message sent immediately via `send_message_get_id()`, then deleted via `delete_message()` after real response arrives

**Content Quality Pipeline:**
- `_extract_section_summaries()` — pulls key sections from state files
- `_filter_noise_bullets()` — removes stale/boilerplate lines, date-based auto-skip
- `_clean_for_telegram()` — universal cleanup: strips Markdown, normalises whitespace
- `_split_message()` — splits responses at paragraph boundaries for Telegram 4096-char limit

**Directory Structure:**
```
scripts/channels/
  __init__.py         — package init
  base.py             — ChannelAdapter Protocol + ChannelMessage + InboundMessage dataclasses
  registry.py         — load channels.yaml, instantiate adapters
  telegram.py         — reference implementation (push + interactive + health_check + send_message_get_id + delete_message)
scripts/channel_push.py      — Layer 1 push hook (Step 20, ~350 LOC)
scripts/channel_listener.py  — Layer 2+3 asyncio daemon (~2100 LOC)
scripts/setup_channel.py     — interactive setup wizard + service installer
scripts/service/
  artha-listener.xml                    — Windows Task Scheduler template
  com.artha.channel-listener.plist      — macOS launchd template
  artha-listener.service                — Linux systemd unit
config/channels.example.yaml            — distributable template (no PII)
```

**Security Model:** See `specs/conversational-bridge.md §6` for full threat model (T1–T14). Layer 3 (LLM Q&A) adds: prompt injection mitigation via command-first parsing (LLM only invoked for unrecognised commands), vault auto-relock, structured output constraints.

**Key Credentials:**
| Credential | Keyring Service | Key |
|-----------|----------------|-----|
| Telegram bot token | `artha` | `artha-telegram-bot-token` |
| Discord bot token | `artha` | `artha-discord-bot-token` |
| Channel PIN | `artha` | `artha-channel-pin` |

**Setup:** `python scripts/setup_channel.py --channel telegram`

**Preflight:** P1 checks — `check_channel_health()` in `preflight.py` verifies enabled channels are reachable. Non-blocking.

**0 new mandatory dependencies.** Adapter SDKs installed on demand via `pip install artha[channels]`.

---

### 3.11 Multi-Machine Action Bridge (DUAL v1.3.0) *(v3.10.0)*

**Purpose:** Synchronise action proposals and execution results between two machines that share an OneDrive folder — a Mac (proposer/enricher role) and a Windows machine (executor role). The Mac generates intelligence and proposes actions; the Windows machine (running 24/7 as the Telegram listener) executes them. The shared OneDrive serves as the transport medium.

**Architecture:**

```
Mac (proposer)                     OneDrive                  Windows (executor)
──────────────────────             ────────────              ────────────────────────
ActionQueue (local DB)  ──write──▶ tmp/bridge/proposals/  ──read──▶ ActionQueue (local DB)
                                                                    │  execute
ActionQueue (local DB)  ◀──read── tmp/bridge/results/    ◀──write── result
```

**Key functions (`scripts/action_bridge.py`):**

| Function | Runs on | Description |
|---|---|---|
| `write_proposal(queue, artha_dir)` | executor | Reads unsynced proposals from local DB, encrypts, writes to `tmp/bridge/proposals/{uuid}.json.enc` |
| `ingest_proposals(bridge_dir, queue, artha_dir)` | executor | Reads incoming proposal files, decrypts, calls `queue.ingest_remote()` |
| `ingest_results(bridge_dir, queue, artha_dir)` | proposer | Reads result files, decrypts, calls `queue.apply_remote_result()` (additive-only) |
| `retry_outbox(bridge_dir, queue, artha_dir)` | executor | Retries proposals that failed to execute |
| `write_result(action_id, status, message, data, artha_dir)` | executor | Encrypts result, writes to `tmp/bridge/results/{uuid}.json.enc` |
| `gc(bridge_dir, artha_dir, ttl_days=7)` | both | Prunes bridge files older than TTL |
| `detect_role(channels_config)` | both | Compares `listener_host` vs `socket.gethostname()` → `'executor'` or `'proposer'` |
| `detect_conflicts(artha_dir)` | both | Globs `state/` for OneDrive machine-suffix conflict copies |
| `is_bridge_enabled(artha_config)` | both | Reads `multi_machine.bridge_enabled` flag |

**Encryption:** Fernet symmetric encryption. Key derivation: Argon2id KDF (`time_cost=2, memory_cost=65536, parallelism=1`) from passphrase stored in OS keyring under service `artha`, key `artha-bridge-key`. Falls back to env var `ARTHA_BRIDGE_KEY`. All bridge files are encrypted at rest; plaintext never written to OneDrive.

**Atomic writes:** `_write_bridge_file()` uses `tempfile.NamedTemporaryFile(delete=False)` + `os.replace()` — crash-safe, no partial files visible to the other machine.

**DB isolation:** Each machine has its own local `ActionQueue` SQLite DB:
- macOS: `~/.artha-local/actions.db`
- Windows: `%LOCALAPPDATA%\Artha\actions.db`
- Override: `ARTHA_LOCAL_DB` env var

The shared OneDrive folder is never used as a database. Only bridge files (proposals/results) pass through it.

**Schema migration:** `_migrate_schema_if_needed()` adds `bridge_synced INTEGER DEFAULT 0` and `origin TEXT` columns to the existing `actions` table on first run. Idempotent.

**`ActionQueue` extensions:**
- `ingest_remote(proposal, pubkey=None)` — UUID-dedup ingestion (skips if `action_id` already in DB)
- `apply_remote_result(action_id, ...)` — additive-only: only fills in result fields; never overwrites existing non-null proposal fields (`description`, `parameters`, `source_step`, `source_skill`, `linked_oi`)
- `mark_bridge_synced(action_id)` — sets `bridge_synced = 1`
- `list_unsynced_results()` — returns terminal actions with `bridge_synced = 0`
- `update_defer_time(action_id, defer_time)` — updates `expires_at` for deferred actions

**Configuration (`config/artha_config.yaml`):**
```yaml
multi_machine:
  bridge_enabled: false        # set true on both machines to activate
  bridge_dir: tmp/bridge       # relative to artha_dir
  ttl_days: 7                  # gc retention
```

**Per-machine connector routing (`run_on:` field):**

`config/connectors.yaml` each connector entry has a `run_on:` field (`darwin` / `windows` / `all`, default `all`). `_enabled_connectors()` in `pipeline.py` gates on `platform.system().lower()` — non-matching connectors are silently skipped. `list_connectors()` displays a PLATFORM column.

Example override for a multi-machine setup:
```yaml
connectors:
  imessage_local:
    run_on: darwin           # only fetch on Mac
  whatsapp_local:
    run_on: windows          # only fetch on Windows
  gmail:
    run_on: all              # fetch on both (default)
```

**Nudge daemon host gating:** `_verify_nudge_host(channels_config)` in `nudge_daemon.py` compares `channels.yaml defaults.listener_host` vs `socket.gethostname()`. Returns `False` (skip) on mismatch — prevents duplicate nudges when daemon runs on multiple machines.

**Preflight check:** P1 advisory — `check_bridge_health()` verifies bridge directory is readable/writable and `artha-bridge-key` is present in keyring. Non-blocking.

**Metrics:** `class BridgeMetrics` — counters (`proposals_written`, `proposals_ingested`, `results_written`, `results_ingested`, `retries`, `gc_pruned`) + `record_latency()` method. Persisted to `tmp/bridge_metrics.json` via `save()`.

**Security:** Only encrypted cipher-text traverses OneDrive. Bridge key lives in OS keyring, not in any tracked file. `channels.yaml` and `artha_config.yaml` (which contain `listener_host` and `bridge_enabled`) are gitignored.

**Setup:** Set `multi_machine.bridge_enabled: true` in `config/artha_config.yaml`, add `artha-bridge-key` to keyring (`python -c "import keyring; keyring.set_password('artha','artha-bridge-key','<passphrase>')"`), configure `run_on:` for each connector, set `listener_host` in `config/channels.yaml`. Run preflight on both machines.

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

Sensitivity: **critical**, access: catch-up-only. Sections: Family Members (per-member visa status, validity dates, CSPA tracking), Active Deadlines (EAD renewal, H-1B extension, Visa Bulletin watch), Attorney Contact, Visa Bulletin History table, Recent Activity log. See actual `state/immigration.md` for live template.

### 4.3 Finance State (`~/OneDrive/Artha/state/finance.md`)

Sensitivity: **high**, access: catch-up-only. Sections: Accounts Overview (type, institution, balance, last updated), Upcoming Bills (amount, due date, auto-pay status), Monthly Budget Tracking (MTD vs target), Active Alerts, Recent Activity log.

### 4.4 Kids State (`~/OneDrive/Artha/state/kids.md`)

Sensitivity: **standard**, access: full. Per-child sections: Academics table (course, grade, trend), College Prep (SAT, college list, milestones), College Application Countdown (YAML milestones with 90d/14d auto-alerts), Activities & Events. Recent Activity log.

### 4.5 Health-Check State (`~/OneDrive/Artha/state/health-check.md`)

System observability file. Sections: Last Catch-Up (duration, emails, alerts, errors), MCP Tool Status, Run History (last 10), Estimated Monthly API Cost, Accuracy Pulse Data (per-catchup + 7-day rolling acceptance/corrections), Tiered Context Stats (tier assignments + token savings), Email Pre-Processing Stats, Signal:Noise Tracking (30-day rolling), Context Window Pressure (token usage + headroom), OAuth Token Health (per-provider status).

### 4.6 Audit Log (`~/OneDrive/Artha/state/audit.md`)

Append-only action log. Per-day entries: CATCH-UP, ALERT, BRIEFING (with sensitivity filter info), STATE_UPDATE (per domain), PROPOSED_ACTION (with user response). Document Access Log (Phase 2+): DOC_ACCESS with extracted fields, DOC_SKIPPED with reason.

### 4.7 Memory State (`~/OneDrive/Artha/state/memory.md`)

Persistent learning store. Sections: Preferences (alert timing, briefing style), Decisions (date + rationale), Corrections (date + fix), Patterns Learned (catch-up patterns, seasonal observations), Behavioral Baselines (catch-up/communication/financial/life rhythm patterns with 30-day moving averages), Coaching Preferences (style, accountability level, nudge format, celebration threshold).

---

### 4.8 Open Items State (`~/OneDrive/Artha/state/open_items.md`)

Action-item bridge between catch-ups and Microsoft To Do. Fields: id (OI-NNN), date_added, source_domain, description, deadline, priority (P0/P1/P2), status (open/done/deferred), todo_id (MS To Do task ID), date_resolved. Sensitivity: standard.

### 4.9 Social State (`~/OneDrive/Artha/state/social.md`) *(v1.9)*

Relationship graph for FR-11. Per-contact: name, tier, relationship, last_contact, frequency_target, preferred_channel, cultural_protocol, timezone, life_events. Group Health table. Communication Patterns (outbound/inbound counts, reciprocity alerts). Reconnect Queue (overdue contacts).

### 4.10 Decisions State (`~/OneDrive/Artha/state/decisions.md`) *(v1.9)*

Cross-domain decision tracking. Active Decisions: id, date, summary, context, domains_affected, alternatives_considered, review_trigger, deadline, deadline_source, status. Resolved Decisions: same fields + outcome.

### 4.11 Scenarios State (`~/OneDrive/Artha/state/scenarios.md`) *(v1.9)*

What-if analysis for F15.25. Active Scenarios: id, trigger, question, per-domain impacts (if_X vs if_Y), last_evaluated, status. Templates for common scenarios (refinance, college cost, immigration timeline, job change).

### 4.12 Dashboard State (`~/OneDrive/Artha/state/dashboard.md`) *(v2.0)*

Life-at-a-glance snapshot rebuilt each catch-up. Sections: Life Pulse (per-domain status/alert/trend table), Active Alerts (ranked by URGENCY×IMPACT×AGENCY), Open Items Summary, Life Scorecard (7 dimensions, 1-10 scale, week-over-week trends), System Health (last catch-up, signal:noise, context pressure, OAuth status, accuracy). Sensitivity: standard — safe for OneDrive sync.

### 4.13 Work Calendar State (`~/OneDrive/Artha/state/work/work-calendar.md`) *(v2.2)*

Metadata-only — no meeting titles, attendees, or bodies. Sections: Last Fetch (platform, version, event count, minutes), Weekly Density (rolling 13-week, per-day count+minutes, conflict count), Conflict History (count-only, no details). Updated only from Windows catch-ups; Mac catch-ups leave unchanged. Stale check: if last_fetch >12h and different platform, show metadata summary; if >12h, ignore.

### 4.14 Home IoT State (`~/OneDrive/Artha/state/home_iot.md`) *(v8.2.0)*

**Machine-owned** — do not edit manually; overwritten each catch-up. Companion to `state/home.md` (human-authored). Sensitivity: **standard**, access: full. Read by AI alongside `state/home.md` during Step 7 home domain processing.

Sections: `iot_devices` (last_sync, ha_version, total_entities, online, offline, critical_offline list, supply_alerts), `iot_energy` (last_updated, current_power_w, daily_kwh, weekly_avg_kwh, spike_detected, spike_pct, history — rolling 30-day daily totals). History truncated to last 30 entries each write to prevent unbounded growth.

Privacy: No IP addresses stored. Device names pass through PII guard. Presence state (if opted in) stored as `home`/`not_home`/`unknown` only — never GPS or zone data.

---

## 5. Briefing Format Specification

### 5.1 Catch-Up Briefing

```markdown
# Artha Catch-Up — [Date]
**Last run**: [timestamp] | **Emails processed**: N | **Period**: Xh

## 🔴 Critical Alerts  ← high-urgency items (PII stripped)
## 🟠 Urgent           ← time-sensitive across any domain
## 📅 Today's Calendar  ← next 3 meaningful events
## 📬 By Domain        ← Immigration · Kids · Finance · Home · [others]
## 🤝 Relationship Pulse *(v1.9)* ← reconnect radar + upcoming dates
## 🎯 Goal Pulse       ← goal table: Status · Trend · Leading Indicator
## 💡 ONE THING        ← single most important synthesis (2-3 sentences)
## 📅 Week Ahead *(v2.1 — Monday only)* ← day-by-day table Mon–Sun
## 🛡️ PII Detection Stats *(v2.1)* ← redaction count + false-positive rate
## ❓ Calibration Questions *(v2.1)* ← 2 post-briefing accuracy questions
---
*Artha catch-up complete. N emails → M actionable items.*
```

**Briefing sensitivity filter:** When the briefing is emailed, domains marked `sensitive: true` in `artha_config.yaml` are summarized as "✅ N items processed. No new alerts." The terminal output always shows full detail regardless of sensitivity setting.

#### 5.1.1 Digest Mode *(v1.9)*

Triggered when >48h gap since last catch-up. Groups by day (not domain), shows Critical/Urgent items upfront, then domain items grouped under each day.

#### 5.1.2 Flash Briefing *(v2.0)*

Triggered by "quick update", `/catch-up flash`, or <4h since last run. Max 8 lines: 🔴/🟠 alerts only, no calendar, no goal pulse.

**Compression levels:** Flash (≤30s, 8 lines), Standard (2-3 min, 40-60 lines), Deep (5-8 min, 80-120 lines).
### 5.2 Weekly Summary

Generated on Sunday catch-up (or first Monday catch-up if weekend skipped). Sections: Week in Numbers, Domain Summaries, Goal Progress, Accuracy Pulse *(v1.9)* (actions proposed/accepted/declined, domain accuracy), Leading Indicator Alerts *(v1.9)*, Artha Observations (cross-domain insights via extended thinking), Upcoming Week.

### 5.3 Monthly Retrospective *(v2.1)*

Generated first catch-up after 1st of month. Saved to `summaries/retro-YYYY-MM.md`. Sections: Month at a Glance (counts, goals), What Happened (domain narratives), Decisions Made (from decisions.md), Goals Progress (start vs end), Artha Self-Assessment (accuracy, signal:noise, PII), Looking Ahead.

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

### 6.2 Example: Immigration Domain Prompt (condensed)

**FR:** FR-2 (P0). **Extraction:** case_type, receipt_number, deadline, action_required, attorney_items, priority_date, status_change. **Alerts:** 🔴 deadline <30d / status change / RFE; 🟠 <90d / Visa Bulletin near PD; 🟡 <180d / attorney correspondence. **CSPA Monitoring:** Calculate CSPA age for dependents, alert at 36/24/12/6 months before age-out. **Dedup:** Match on receipt number or event type+date; update in-place, don't duplicate. **Visa Bulletin:** Extract EB-2 India date, compute movement delta and estimated months-to-current.

### 6.3 Example: Finance Domain Prompt (condensed)

**FR:** FR-3 (P0). **Extraction:** bill_type, amount, due_date, account, auto_pay, statement_period, balance, unusual_flag (>20% deviation). **Alerts:** 🔴 overdue / fraud / credit score drop; 🟠 due <3d / unusual >$500; 🟡 due <7d / over budget. **Dedup:** Match on provider + billing period; payment confirmation updates existing bill entry. **Cross-Domain:** Travel bookings → check credit card benefits; large expenses → check cash flow.

Full domain prompts live in `prompts/*.md`. Above are condensed schema references.

---

## 7. Catch-Up Workflow — Detailed Sequence

### 7.1 Step-by-Step Flow

**Pre-flight (Step 0):** OAuth tokens valid, fetch scripts healthy, lock file clear, vault operational, WorkIQ combined check (P1 non-blocking, 24h cache). HALT on any P0 failure.

**Step 0b:** Pull To Do completion status (Phase 1B).

**Step 1:** Decrypt → Data Integrity Guard (pre-decrypt backup, post-decrypt verify). Read health-check for last run time. Digest mode check (>48h gap).

**Step 2 [PARALLEL]:** Gmail fetch + Calendar fetch + MS Graph fetch + iCloud fetch (all since last run).

**Step 3:** PII pre-flight filter (HALT on failure). Email content pre-processing: strip HTML, remove footers, marketing suppression, thread collapse, 1,500 token cap per email, batch summarization (>50 emails).

**Step 4:** Read all state files. Parallel skill pull (USCIS, Tax, WorkIQ calendar). Tiered context loading (Always/Active/Reference/Archive — target 30-40% token savings). Build domain index card via `domain_index.py` (Step 4b′ — Phase 2 progressive disclosure): compact ~600-token frontmatter summary that gates which domain prompts load for this command.

**Step 5 [PARALLEL per domain]:** Route emails → apply domain prompt extraction → Layer 2 redaction → update state file via middleware stack (Phase 4: PII → WriteGuard → AuditLog → [write] → WriteVerify → AuditLog) → evaluate alerts → collect briefing contribution. Update open_items.md (fuzzy-match, re-surface overdue). Context offloading (Phase 1): after Steps 4-5 complete, offload pipeline JSONL + email batch to `tmp/`; keep only routing table counts in context.

**Step 6:** Cross-domain reasoning (immigration+travel, bill+cashflow, school+work calendar). WorkIQ conflict rules (cross-domain=3, internal=1, >300min=heavy load). ONE THING scoring (URGENCY×IMPACT×AGENCY). Decision graph check, scenario trigger. Context offloading (Phase 1): write full scoring analysis to `tmp/cross_domain_analysis.json`; keep ONE THING + top 5 alerts in context.

**Step 7:** Synthesize briefing per §5.1 format. Run structured output validation (Phase 5): populate `BriefingOutput` schema, write `tmp/briefing_structured.json`, log validation errors non-blocking. Calibration questions (post-briefing accuracy check). Decision deadline check (expired→🔴, ≤14d→nudge, open >14d→set deadline).

**Step 8:** Email briefing, save to briefings/, update health-check (include `harness_metrics` block), log to audit, push/pull To Do, rebuild dashboard.md, update work-calendar.md (count+duration only), meeting-triggered OIs.

**Step 8b:** Net-negative write guard (now part of `WriteGuardMiddleware` in Phase 4 middleware stack) — blocks write if state file would lose >20% of fields.

**Step 9:** Delete `tmp/work_calendar.json` (corporate data). Delete all harness offload artifacts: `tmp/pipeline_output.jsonl`, `tmp/processed_emails.json`, `tmp/domain_extractions/`, `tmp/cross_domain_analysis.json`, `tmp/briefing_structured.json`, `tmp/session_history_*.{md,json}` (Step 18a′). Then vault encrypt.

### 7.2 Error Handling

**Critical (HALT):** PII filter failure, pre-flight gate failure, OAuth refresh failure with retries exhausted (HTTP 429/503), fresh lock file (<30m), pre-decrypt backup failure, decrypt produces empty/corrupt file (restore from .md.bak).

**Graceful degradation:** Gmail/Calendar MCP failure → proceed with available data, note staleness. Single email parse failure → skip + log. Net-negative write → show diff, require user confirmation. Bootstrap template detected → warn, skip update. WorkIQ failure (any type) → skip silently, show footer note.

**Non-blocking:** To Do sync failure → log, retry next run. WorkIQ unavailable on Mac → silent skip. Stale lock (>30m) → auto-clear with warning.

**Context window:** If headroom <20% → 🟡 alert. If >80 emails → two-pass processing (P0 domains first). Log token usage to health-check.md.

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

### 7.4 Action Execution Framework *(ACTIONS-RELOADED v1.3.0)*

The Action Layer is Artha's write path — converting domain intelligence (signals) into actionable proposals that users can approve, reject, or defer.

#### 7.4.1 Action Proposal Schema

Every action proposal contains: Type, Recipient, Channel, Content Preview, Trust Required, Sensitivity, Friction level. Options: Approve / Modify / Reject.

**Trust Levels:** L0 = recommendations only. L1 = approve/modify/reject. L2 = pre-approved types auto-execute with post-hoc notification.

**Friction Classification:** Low (calendar adds, visual gen — batch-approvable), Standard (email compose, greetings — individual review), High (financial, immigration, actions affecting others — always individual, never pre-approved).

**Action Catalog:**

| Action | Channel | Min Trust | Human Gate |
|---|---|---|---|
| Send briefing | Gmail MCP | L0+ | Auto |
| Compose email | Gmail MCP | L1+ | Always (Autonomy Floor) |
| WhatsApp message | URL scheme (`open "https://wa.me/..."`) | L1+ | OS-enforced |
| Calendar event | Google Calendar MCP | L1+ | Approve |
| Generate visual | Gemini Imagen | L0+ | None |
| Archive email | Gmail MCP | L2 | Pre-approved |
| Draft attorney email | Gmail MCP | L1+ | Always (Autonomy Floor) |
| Reminder create | Apple Reminders / Todoist | L1+ | Approve |
| Instruction sheet | null (guidance prose only) | L0+ | None |

**Autonomy Floor (never auto-executed):** Communications sent on your behalf, financial transactions, immigration actions, actions affecting others' data.

**Email Composition:** OAuth `gmail.send` scope → Claude drafts → terminal preview → user approves → MCP sends → audit.md logs. Group emails read from `contacts.md`.

**WhatsApp:** URL scheme opens WhatsApp with pre-filled text; user must tap Send. Cannot confirm delivery or attach images programmatically.

**Visual Messaging:** Gemini Imagen generates visual → saved to `visuals/` → composed with email/WhatsApp actions → user approves. Occasions configured in `occasions.md`.

**Lifecycle:** PROPOSE → REVIEW → EXECUTE → LOG. All actions (approved, modified, rejected) logged to `state/audit.md` and `actions.db`.

#### 7.4.2 Orchestrator Architecture (Signal→Proposal Pipeline)

All action proposal generation flows through `scripts/action_orchestrator.py` — the single integration point that wires signal producers to the action queue.

```
pipeline.py ─→ JSONL to stdout + tee to tmp/pipeline_output.jsonl
                                  │
                         AI catch-up workflow (Steps 5–12)
                                  │
                         Step 12.5 (AI runs CLI)
                                  ▼
               ┌─────────────────────────────────────┐
               │  action_orchestrator.py --run        │
               │                                      │
               │  1. Load tmp/pipeline_output.jsonl   │
               │  2. email_signal_extractor.extract() │
               │  3. pattern_engine.evaluate()        │
               │  4. Deduplicate signals              │
               │  5. Pre-enqueue handler validation   │
               │  6. ActionComposer.compose() each    │
               │  7. ActionExecutor.propose_direct()  │
               │  8. Print summary to stdout          │
               │  9. Persist signals → tmp/signals.jsonl │
               └──────────────┬──────────────────────┘
                              │
                   AI embeds § PENDING ACTIONS in briefing
                   User says "approve 1" / "reject 2" / "approve all low"
                              │
               action_orchestrator.py --approve <id>
                   ActionExecutor.approve() → handler.execute()
                   Result logged to actions.db + state/audit.md
```

**Tier 1 (MCP data path):** When `artha_fetch_data()` is used in Step 4 (no pipeline JSONL), run with `--mcp` flag to skip email signal extraction — pattern engine still runs against state files:
```bash
python3 scripts/action_orchestrator.py --run --mcp
```

**Tier 2 (pipeline data path):** `pipeline.py --output tmp/pipeline_output.jsonl` writes a fresh snapshot per run via atomic `Path.replace()` (no partial reads; no stale accumulation). The orchestrator loads this file for email signal extraction.

#### 7.4.3 Signal Producers

| Producer | Module | Coverage |
|----------|--------|----------|
| Email signal extractor | `scripts/email_signal_extractor.py` | 9 categories: `bill_due`, `event_rsvp_needed`, `school_action_needed`, `delivery_arriving`, `form_deadline`, `financial_alert`, `security_alert`, `subscription_renewal`, `appointment_confirmed`. Scans `body` field (falls back to `body_preview`). Skips `marketing: true` records. |
| Pattern engine | `scripts/pattern_engine.py` | ~5 active types: `goal_stale`, `maintenance_due`, `document_expiring`, `review_pending`, `health_check_overdue`. Evaluates `config/patterns.yaml` against state files. Per-pattern cooldowns in `state/pattern_engine_state.yaml`. |

**Signal coverage gap (V1.0):** `_FALLBACK_SIGNAL_ROUTING` defines 50+ signal types but only ~13 have active producers in V1.0. The remaining types are pre-wired for future producer expansion — adding a pattern to `config/patterns.yaml` activates a signal type with no code changes.

#### 7.4.4 Signal Routing: YAML-Fallback Merge Invariant

`action_composer.py`'s `_load_signal_routing()` **merges** `config/signal_routing.yaml` entries **over** the hardcoded `_FALLBACK_SIGNAL_ROUTING` base. YAML entries override individual keys; they do NOT replace the full dict. This ensures all 50+ hardcoded signal types route correctly even when `signal_routing.yaml` has only a subset of entries.

```python
def _load_signal_routing() -> dict[str, dict]:
    base = dict(_FALLBACK_SIGNAL_ROUTING)
    yaml_routing = load_config("signal_routing")  # may be partial
    if yaml_routing:
        base.update(yaml_routing)  # YAML overrides, not replaces
    return base
```

#### 7.4.5 Handler–Action Type Alignment Invariant

`_ALLOWED_ACTION_TYPES` (in `action_composer.py`) and `_FALLBACK_ACTION_MAP` (in `action_executor.py`) MUST remain in sync. Every allowed type must have a handler module. Validate with:
```bash
python3 -c "from scripts.action_composer import _ALLOWED_ACTION_TYPES; \
from scripts.action_executor import _FALLBACK_ACTION_MAP; \
missing = _ALLOWED_ACTION_TYPES - set(_FALLBACK_ACTION_MAP); \
assert not missing, f'Missing handlers: {missing}'"
```

#### 7.4.6 Platform-Local Database

`actions.db` is stored outside the cloud-synced workspace to prevent SQLite WAL/SHM corruption from OneDrive sync:

| Platform | Path |
|----------|------|
| macOS | `~/.artha-local/actions.db` |
| Windows | `%LOCALAPPDATA%\Artha\actions.db` |
| Linux | `$XDG_DATA_HOME/artha/actions.db` |

The DB contains only action queue state — all intelligence (goals, domain state, financials) remains in Markdown. Bounded by `max_queue_size: 1000` and `archive_after_days: 30`. Human-readable audit trail in cloud-synced `state/audit.md`. Cross-machine propagation via the DUAL v1.3.0 bridge (§3.11).

#### 7.4.7 Orchestrator CLI Reference

| Command | Purpose |
|---------|--------|
| `--run` | Signal→compose→queue (core loop) |
| `--run --mcp` | Skip email signals; pattern engine only (MCP Tier 1) |
| `--approve <id>` | Execute an approved proposal |
| `--reject <id> [--reason]` | Reject a proposal |
| `--defer <id> [--until]` | Defer: `+1h` \| `+4h` \| `tomorrow` \| `next-session` (default +24h) |
| `--approve-all-low` | Batch-approve all low-friction proposals |
| `--show <id>` | Expanded preview — required before approving content-bearing actions (`email_send`, `email_reply`, `whatsapp_send`) |
| `--list` | Print all pending proposals |
| `--expire` | Remove proposals past `expires_at` |
| `--health` | Handler import check + queue stats |

**Exit codes:** 0=ok, 1=partial failure, 3=full failure. Always non-blocking — catch-up never fails due to action layer errors.

**Kill switch:** `harness.actions.enabled: false` in `config/artha_config.yaml` → instant full disable, no errors. Catch-up degrades to read-only intelligence behavior.

**Burn-in mode:** `harness.actions.burn_in: true` → proposals appear under `[DEBUG] Proposed Actions` at the end of the briefing (not the main `§ PENDING ACTIONS` section). Use for 5 sessions to validate signal quality before full integration.

#### 7.4.8 Reliability Hardening

| Mechanism | Detail |
|-----------|--------|
| Import-level handler check | `_handler_health_check()` at `--run` startup — unavailable action types suppressed for the session |
| Pre-enqueue validation | `_validate_proposal_handler()` runs `handler.validate(proposal)` before enqueue — catches structural errors before the user ever approves |
| Graceful degradation | No pipeline JSONL → pattern engine still runs; email signals=0. Catch-up never fails. |
| Rate limiting | `ActionRateLimiter` enforces per-type limits from `config/actions.yaml` (e.g., `email_send: max 20/hour`) |
| Timeout protection | 60s wall-clock limit on `--run`; per-handler timeout on `--approve` |
| SQLite WAL | `PRAGMA journal_mode=WAL` + `busy_timeout=5000` on `actions.db` |

#### 7.4.9 Security Invariants

| Invariant | Mechanism | Test |
|-----------|-----------|------|
| Handler allowlist | `_HANDLER_MAP` in `actions/__init__.py` | `test_handler_allowlist_security` |
| PII double-scan | `_pii_scan_params()` at enqueue AND execute | `test_pii_guard_blocks_at_enqueue` |
| Autonomy floor | `TrustEnforcer.check()` — hardcoded, not bypassable via config | `test_autonomy_floor_not_bypassable` |
| Human gate | All handlers require `approve()` before `execute()` | `test_no_auto_execute` |
| Audit trail | Every state transition logged to DB + `state/audit.md` | `test_audit_completeness` |
| Signal isolation | V1.0: deterministic code only (no AI-emitted signals). V1.1: AI signals gated by `harness.actions.ai_signals: false` default + mandatory `friction: high` escalation | Config flag + design invariant |
| Encrypted params at rest | Sensitive proposal parameters encrypted in `actions.db` with age | `test_encryption_at_rest` |
| Output path safety | `pipeline.py --output` validated to be inside `tmp/` only | `test_output_path_traversal_blocked` |
| No network in orchestrator | `--run` makes zero network calls; network only during handler `--approve` execution | Architecture invariant |

#### 7.4.10 Operational Telemetry

Every `--run` emits a structured log line to `state/audit.md`:
```
[2026-04-01T09:00:00Z] ACTION_ORCHESTRATOR | signals:4 suppressed:1 queued:3 expired:0 depth:5 errors:0
```

**`--health` output:**
```
═══ ACTION LAYER HEALTH ════════════════════════════════════
Queue: 5 pending, 2 deferred, 0 expired-uncleared
Handlers: 11/13 healthy (apple_reminders_sync: macOS only, todoist_sync: no API token)
Bridge: last sync 47m ago (healthy)
Config: actions.enabled=true, ai_signals=false, burn_in=false
DB: ~/.artha-local/actions.db (WAL, 4 tables, 847 rows)
Approval funnel (30d): 42 proposed → 31 approved → 28 succeeded → 3 failed → 8 expired → 3 rejected
════════════════════════════════════════════════════════════
```

**Key counters:** `signals_detected`, `proposals_queued`, `proposals_suppressed` (cross-session dedup), `proposals_expired`, `queue_depth`, `approvals_total`, `approvals_succeeded`, `approvals_failed`, `rejections_total`, `deferrals_total`.

### 7.5 Bootstrap Command Workflow *(v2.0)*

`/bootstrap` provides guided state population for domains with bootstrap templates. Detection: check each state file's `updated_by` field at Step 4. Interview: domain selection → structured questions derived from state file schema (one at a time, accepts "skip") → format validation → answer confirmation → full preview → write + verify + encrypt. Progress saved per-domain; re-running warns about overwrite.

### 7.6 Compound Signal Detection *(v2.0)*

Runs at Step 6. Detects correlations across domains: travel+credit card benefits, immigration deadline+leave planning, school event+work calendar, bill spike+seasonal pattern, health appointment+insurance coverage, goal stall+behavioral change. Surfaced in briefing under "🔗 Compound Signals" with action proposals. Ephemeral — not persisted, recurring patterns added to memory.md.

### 7.7 Pattern of Life Detection *(v2.0)*

Learns behavioral baselines from memory.md Behavioral Baselines section (30-day moving averages). Requires 10+ catch-ups before anomaly detection activates. Anomalies: unusual gap (>2× avg), email volume spike (>2×), spending deviation (>1.5×), communication drop (<50%), routine disruption (>3hr from typical time). Derived from Artha's own state data — no external tracking.

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

### 7.9 Life Scorecard *(v2.0)*

Weekly snapshot (Sunday catch-up) in dashboard.md. 7 dimensions scored 1-10: Financial Health, Immigration Progress, Kids & Education, Physical Health, Social & Relationships, Career & Goals, Home & Operations. Composite Life Score = average (equal weights). Trend: ↑ improved ≥0.5, → stable ±0.4, ↓ declined ≥0.5.

### 7.10 Consequence Forecasting *(v2.0)*

"IF YOU DON'T" alerts on 🔴/🟠 items with clear deadlines: state inaction → timeline → first-order consequence → cascade effects. Only >70% confidence consequences. Appears in ONE THING section (Standard) and as dedicated line (Flash). Weekly summary includes "Consequences Averted" section.

### 7.11 Pre-Decision Intelligence Packets *(v2.0)*

On-demand research compilation when a decision point is detected (Step 6) or user asks for analysis. Structure: summary, options (each with pros/cons/risks/domains), recommendation, information gaps, timeline.

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
| Passport numbers | Stored as `[REDACTED-PASSPORT-Raj]` in state files; full value in Keychain if needed |
| A-numbers (Alien Registration) | Stored as `[REDACTED-ANUM-Raj]` in state files |
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
| Beneficiary designations | Stored by relationship only: "Primary: Priya. Contingent: Arjun, Ananya." No account-specific amounts. |
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
| Meeting times/durations | Ephemeral in briefing. Count+duration aggregates persisted to `state/work/work-calendar.md`. |
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
| Microsoft Graph | OAuth token, To Do task sync, Outlook email, Outlook calendar | Microsoft API terms | `connectors/msgraph_email.py` reads only; task sync writes |

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
| goals.md | No — standard (goal names only, no financial targets) | `goals.md` (plaintext, schema v2.0: YAML `goals:` block with `type`/`status`/`metric{baseline,current,target,unit,direction}`/`sprint` sub-blocks) |
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

#### 8.5.1b 10-Layer Defense-in-Depth *(v3.2 — PRD F15.88, P0)*

Beyond the three-layer guard above, `vault.py` and `backup.py` implement 10 additional protections against identified data loss scenarios. All protections are implemented in Python, cross-platform (macOS/Windows/Linux), and covered by 501 tests.

| # | Protection | Module | Threat Mitigated |
|---|---|---|---|
| 1 | **Advisory file lock** | `vault.py` | Concurrent encrypt/decrypt (cron + manual, parallel terminals) |
| 2 | **Cloud sync fence** | `vault.py` | OneDrive/Dropbox/iCloud syncing mid-encrypt overwrites `.age` with stale version |
| 3 | **Post-encrypt verification** | `vault.py` | Truncated `.age` output from `age` CLI crash or disk-full |
| 4 | **Deferred plaintext deletion** | `vault.py` | Partial encrypt failure leaves some domains with neither `.md` nor valid `.age` |
| 5 | **Encrypt-failure lockdown** | `vault.py` | Cloud sync uploads unencrypted `.md` files after partial encrypt failure |
| 6 | **Auto-lock mtime guard** | `vault.py` | `auto-lock` encrypts while user/AI is actively writing state files |
| 7 | **Net-negative override** | `vault.py` | Legitimate large state file shrink blocked by 80% size guard (e.g., annual cleanup) |
| 8 | **GFS prune protection** | `backup.py` | Rotation deletes the only ZIP containing a domain's data |
| 9 | **Confirm gate** | `backup.py` | Accidental `restore` or `install` overwrites live state |
| 10 | **Key health monitoring** | `vault.py` | Invalid key format or never-exported key discovered only at disaster recovery time |

**Implementation details:**

**1. Advisory file lock** — `_acquire_op_lock()` / `_release_op_lock()` use `fcntl.flock(LOCK_EX | LOCK_NB)` on POSIX and `msvcrt.locking(LK_NBLCK)` on Windows. The `@_with_op_lock` decorator wraps `do_decrypt()` and `do_encrypt()`. Second caller gets an immediate error (non-blocking).

**2. Cloud sync fence** — `_is_cloud_synced()` checks if the workspace path contains OneDrive, Dropbox, or iCloud markers. `_check_sync_fence()` samples all `.age` mtimes, sleeps 2 seconds, re-checks. If any mtime changed, sync is in flight — operation is aborted. Automatically skipped for non-cloud paths.

**3. Post-encrypt verification** — After each `age -r` call, the new `.age.tmp` file size is compared to the plaintext `.md` size. If `.age.tmp < .md` size, the encrypt is considered truncated: the `.age.tmp` is removed, `_lockdown_plaintext()` is called, and the operation aborts.

**4. Deferred plaintext deletion** — During `do_encrypt()`, successfully encrypted domains are collected in an `encrypted_domains` list. Plaintext `.md` files are only deleted in a final sweep after all domains have encrypted successfully. If any domain fails, no plaintext is deleted.

**5. Encrypt-failure lockdown** — `_lockdown_plaintext()` sets `chmod 0o000` on all remaining plaintext `.md` files for sensitive domains. This prevents cloud sync from uploading unencrypted data. `_unlock_plaintext()` restores `0o644` at the start of the next `do_decrypt()`.

**6. Auto-lock mtime guard** — `do_auto_lock()` checks if any sensitive `.md` file has been modified within the last 60 seconds. If so, it refreshes the lock file TTL and returns exit code 2 (deferred), preventing `auto-lock` from encrypting while the user or AI CLI is actively writing.

**7. Net-negative override** — `is_integrity_safe()` supports the `ARTHA_FORCE_SHRINK` environment variable: set to `1` to override all domains, or to a specific domain name. When overridden, the old `.age` file is pinned to `.age.pre-shrink` for manual recovery.

**8. GFS prune protection** — `_prune_backups()` computes domain checksums for every retained snapshot (across all tiers). Before deleting a ZIP, it verifies that every domain checksum in that ZIP exists in at least one other retained snapshot. Sole-carrier ZIPs are pinned and not pruned, even if they exceed the retention limit.

**9. Confirm gate** — `_restore_from_zip()` requires either `--confirm` or `--dry-run`. Without either flag, it shows a preview and exits without writing. Before a confirmed restore, live state files are backed up to `backups/pre-restore/` for recovery.

**10. Key health monitoring** — `do_health()` validates the credential-store key starts with `AGE-SECRET-KEY-` (not a garbage value). It also checks the `last_key_export` timestamp in the backup manifest and warns if the key has never been exported.

### 8.5.2 GFS Vault Backup *(v2.6 — P0)*

Grandfather-Father-Son (GFS) rotation provides point-in-time recovery beyond the cycle-level `.md.bak` protection of §8.5.1. All backup data is `.age`-encrypted and syncs to OneDrive automatically.

**Three-module architecture (v2.6):**

| Module | Role |
|---|---|
| `scripts/foundation.py` | Shared leaf: `_config` dict, path constants, `log()`/`die()`, key management, `age_encrypt`/`age_decrypt` |
| `scripts/vault.py` | Session lifecycle: decrypt → catch-up → encrypt + GFS trigger |
| `scripts/backup.py` | GFS archive engine: snapshot, restore, validate, CLI (`backup.py snapshot/status/validate/restore/install/export-key/import-key`) |

**`_config` dict pattern:** All functions access paths via `_config["KEY"]` (e.g., `_config["STATE_DIR"]`), never via frozen module-level aliases. This makes test isolation a single `monkeypatch.setitem(foundation._config, "KEY", temp_path)` per test fixture.

**Trigger:** `vault.py encrypt` — after each successful encrypt cycle, calls `backup_snapshot(registry)` via lazy import from `backup.py`. If count == 0, `_mark_backup_failure()` writes a sentinel to `health-check.md` (non-fatal). Auto-validation triggers inside `backup_snapshot()` if `last_validate` is absent or > 7 days old (wrapped in `try/except SystemExit` — non-fatal).

**Backup scope — full state + config:**

The backup registry is declared in `config/user_profile.yaml → backup` section. See §8.5.2 v2.4 for the full registry. `load_backup_registry()` in `backup.py` reads this registry; falls back to `SENSITIVE_FILES` if absent.

| source_type | Live location | Backup handling |
|---|---|---|
| `state_encrypted` | `state/{name}.md.age` | Copied directly — already encrypted |
| `state_plain` | `state/{name}.md` | Encrypted on-the-fly by `age_encrypt` before storing |
| `config` | `config/{file}` | Encrypted on-the-fly by `age_encrypt` before storing |

Default registry (user-editable — users without certain domains simply remove entries):
- **9 encrypted state files**: immigration, finance, insurance, estate, health, vehicle, contacts, occasions, audit
- **22 plain state files**: boundary, calendar, comms, dashboard, decisions, digital, employment, goals, health-check, health-metrics, home, kids, learning, memory, onenote_progress, open_items, scenarios, shopping, social, travel, work-calendar
- **4 config files**: `config/user_profile.yaml`, `config/routing.yaml`, `config/connectors.yaml`, `config/artha_config.yaml`

**Storage layout (ZIP-per-snapshot):**
```
backups/                    ← at project root (gitignored, syncs via OneDrive)
  daily/
    2026-03-14.zip          ← one ZIP per day, contains ALL registered files
    2026-03-13.zip
  weekly/
    2026-03-08.zip
  monthly/
    2026-02-28.zip
  yearly/
    2025-12-31.zip
  manifest.json             ← outer catalog: ZIP keys → sha256, tier, date, file_count
```

Each ZIP is a self-contained portable snapshot:
```
2026-03-14.zip
  manifest.json             ← internal: sha256 per file, source_type, restore_path
  state/immigration.md.age  ← state_encrypted: copied as-is
  state/goals.md.age        ← state_plain: encrypted on-the-fly
  config/user_profile.yaml.age  ← config: encrypted on-the-fly
  ...
```

Internal archive paths:
- `state_encrypted`: same as live path (`state/{name}.md.age`)
- `state_plain` / `config`: live path + `.age` suffix (`state/goals.md.age`, `config/user_profile.yaml.age`)

**Tier promotion (priority — highest wins):**

| Date condition | Tier |
|---|---|
| December 31 | yearly |
| Last day of any month | monthly |
| Sunday | weekly |
| All other days | daily |

**Retention policy:**

| Tier | Keep per domain | Coverage |
|---|---|---|
| daily | last 7 | 1 rolling week |
| weekly | last 4 | 4 rolling weeks |
| monthly | last 12 | 12 rolling months |
| yearly | unlimited | Full history |

**Trigger:** `vault.py encrypt` — GFS snapshot taken automatically after every successful encrypt cycle. The ZIP itself is written atomically via `.tmp` sibling + `os.replace()`.

**Manifests (two tiers):**
- **Outer catalog** (`backups/manifest.json`): ZIP key → `{created, date, tier, sha256, size, file_count}`. Written atomically after each snapshot. `last_validate` tracks the most recent restore validation timestamp.
- **Internal manifest** (inside each ZIP as `manifest.json`): `{date, tier, files: {arc_path → {sha256, size, source_type, restore_path, name}}}`. Self-contained — validate and restore from a ZIP without the outer catalog.

**Restore validation (monthly, mandatory):**

```bash
python scripts/backup.py validate
```

Decrypts the most recent backup per domain to a temp directory (live state untouched). Checks in order:
1. SHA-256 matches manifest (bit-rot detection)
2. `age_decrypt` succeeds
3. Output non-empty
4. First line is `---` (YAML frontmatter) — for state files
5. Word count ≥ 30 (non-trivial content)

Result logged to `audit.md`. `vault.py health` warns if validation is overdue (> 35 days).

**Fresh-install restore (cold-start):**

```bash
# Import private key on new machine
python scripts/backup.py import-key

# Preview what would be restored
python scripts/backup.py restore --date 2026-03-14 --dry-run

# Restore all files from a specific snapshot in the local catalog
python scripts/backup.py restore --date 2026-03-14 --confirm

# Restore a single domain only
python scripts/backup.py restore --domain finance --confirm

# Install from an explicit ZIP (cold-start on a new machine)
python scripts/backup.py install /path/to/2026-03-14.zip --confirm
python scripts/backup.py install /path/to/2026-03-14.zip --data-only --confirm
```

**Key management:**

```bash
python scripts/backup.py export-key  # print to stdout — store in password manager
python scripts/backup.py import-key  # read from stdin — paste key, press Ctrl-D
```

`vault.py` forwards all backup commands for backward compatibility (e.g., `vault.py backup-status` → `backup.py status`).

Restore semantics:
- `state_encrypted`: backup `.age` file copied back to `state/`
- `state_plain`: backup decrypted, written as `.md` to `state/`
- `config`: backup decrypted, written back to `config/`

SHA-256 verified before every write. Existing files overwritten. Use `--dry-run` to preview.

**CLI commands:**

| Command | Description |
|---|---|
| `vault.py encrypt` | Triggers GFS snapshot after all files encrypted (calls `backup.py` internally) |
| `backup.py snapshot` | Run GFS snapshot manually without a full vault session |
| `backup.py status` | Show ZIP catalog, tier counts, last validation date |
| `backup.py validate [--domain X] [--date D]` | Open ZIP, decrypt & validate all files in-place |
| `backup.py restore [--date D] [--domain X] [--dry-run] [--data-only] [--confirm]` | Restore from a ZIP found in the local catalog; requires `--confirm` or `--dry-run` |
| `backup.py install <zipfile> [--dry-run] [--data-only] [--confirm]` | Restore from an explicit ZIP path (cold-start on new machine); requires `--confirm` or `--dry-run` |
| `backup.py export-key` | Print the age private key to stdout for secure storage |
| `backup.py import-key` | Read age key from stdin, store in system keychain |
| `backup.py preflight` | Verify age binary, keychain key, and backup directory are present |

> **Backward compatibility:** `vault.py backup-status`, `vault.py validate-backup`, `vault.py restore`, and `vault.py install` are retained as forwarding aliases — they lazy-import and call the corresponding `backup.py` function. New workflows should use `backup.py` directly.

**3-2-1 coverage:**

| Copy | Location | Notes |
|---|---|---|
| 1 | `state/*.md.age` + `state/*.md` (live) | Always-current encrypted and plain state |
| 2 | `backups/{tier}/YYYY-MM-DD.zip` | GFS rotation — up to 1 year of history, all files encrypted inside each ZIP |
| 3 | Both #1 and #2 sync to OneDrive | Cloud copy of all files |

Encryption keys are device-local only (macOS Keychain / Windows Credential Manager). Cloud possession alone is insufficient to decrypt.

**Audit trail entries:**
```
[ts] BACKUP_OK              | file: finance.md.age   | tier: daily  | sha256: abc123...
[ts] BACKUP_OK              | file: goals.md         | tier: daily  | sha256: def456...
[ts] BACKUP_OK              | file: cfg__config__...  | tier: daily  | sha256: ghi789...
[ts] BACKUP_PRUNED          | file: finance-2026-03-06.md.age | tier: daily
[ts] BACKUP_VALIDATE_OK     | key: daily/finance-... | words: 1842
[ts] BACKUP_VALIDATE_FAIL   | key: daily/finance-... | reason: checksum_mismatch
[ts] RESTORE_OK             | key: daily/finance-... | dest: state/finance.md.age
[ts] RESTORE_FAIL           | key: daily/finance-... | reason: checksum_mismatch
```

### 8.6 Pre-Flight PII Filter

The redaction rules in §8.2 govern what gets **written to state files**. But raw email content passes through the Claude API **before** those rules apply. This section specifies a mandatory device-local filter that intercepts PII **before** it enters the API call.

**Architecture:** `pii_guard.py` — pure Python (~460 lines), cross-platform (macOS, Windows, Linux). No third-party dependencies. Replaces the legacy `pii_guard.sh` (bash + Perl regex). Scans email content and replaces detected PII patterns with safe tokens.

**Data flow clarification:** Claude Code's MCP tool invocations return data directly into Claude's context. The `pii_guard.py` filter therefore operates as a **pre-persist** filter — Claude processes the PII-filtered content before writing to state files. The actual data flow is:

1. Claude calls Gmail MCP → raw email content enters Claude's context (Anthropic's ephemeral processing policy applies — not retained beyond request)
2. Claude extracts structured data from email content
3. Claude runs `python pii_guard.py scan` on the extracted data to verify no PII leaked through
4. If PII detected: replace with `[PII-FILTERED-*]` tokens before writing to state files
5. §8.2 redaction rules apply as Layer 2 when writing to state files

**Stretch goal (Option C):** Test whether Claude Code `PreToolUse` hooks can intercept Gmail MCP responses and pipe through `pii_guard.py` before Claude sees the content. If hooks support response modification, this upgrades the filter from pre-persist to true pre-flight. Track in TD-19.

**Defense-in-depth model:**
```
Gmail MCP → raw email content
       │
       ▼
  pii_guard.py filter    ←── Layer 1: Device-local regex. PII never leaves device.
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
4. ⭐ pii_guard.py filter on email batch               ← NEW STEP
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

`pii_guard.py` reads the allowlist from the relevant domain prompt and exempts matching patterns before applying PII filters.

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
- `pii_guard.py` is a **transit-time** control — it prevents PII from ever entering the API call.
- Together they form defense-in-depth: Layer 1 (regex, device-local, pre-API) + Layer 2 (LLM-based, semantic, post-API).
- Regex catches known patterns with near-zero false negatives for structured PII (SSN, CC). Claude catches unstructured sensitive information that regex cannot (e.g., "my password is hunter2").

**Failure mode:** If `pii_guard.py` fails (script error, regex timeout), the catch-up **halts** — it does not proceed with unfiltered content. Artha.md instructs: "If pii_guard.py exits with non-zero status, stop the catch-up and report the error. Do not process unfiltered emails."

### 8.9 Data Fidelity Skills *(v5.0)*

Targeted Python scripts that pull high-fidelity data from institutional portals or official APIs.

**1. Skill Runner Orchestrator (`scripts/skill_runner.py`)**
- **Venv Bootstrap:** Calls `reexec_in_venv()` from `_bootstrap.py` before third-party imports, ensuring correct venv when invoked directly from CLI agents (Gemini, Claude, shell scripts). *(v3.8 — F15.124)*
- **Entrypoint:** `if __name__ == "__main__": main()` — script is directly executable. *(v3.8 — F15.124)*
- **Discovery:** Discover active skills from `config/skills.yaml`.
- **Parallelism:** Execute each skill in parallel using `ThreadPoolExecutor`.
- **Dynamic Loader:** Use `importlib` (module-level import including `importlib.util`) to load modules from `scripts/skills/`. If a module is missing, log a warning and continue. *(v3.8 — importlib.util scope fix)*
- **Aggregation:** Aggregate results into a standard JSON format.
- **Change Detection:** Compare `current` fetch results against `previous` state in `state/skills_cache.json`.
- **Cache Persistence:** Write results to `state/skills_cache.json` (encrypted) using `atomic_write_json()` (fcntl.flock + tempfile + os.replace — crash-safe). *(v5.0 — SKILLS-RELOADED)*
- **Health Tracking:** After each skill run, calls `update_health_counters()` and `classify_health()` from `scripts/lib/skill_health.py` to update per-skill health counters. *(v5.0 — SKILLS-RELOADED)*
- **Adaptive Cadence (R7):** `should_run()` extended — if `health.consecutive_zero >= 10` and priority != P0, effective cadence is reduced one tier (`_CADENCE_REDUCTION` dict: `every_run → daily`, `daily → weekly`). R7 skips logged to `health.r7_skips`. *(v5.0 — SKILLS-RELOADED)*
- **Fail-safe Logic:**
    - **P0 (Immigration):** Halt catch-up on logical/parse errors. If status changes, alert P0 and continue catch-up. Transient errors warn and continue. P0 skills exempt from R7.
    - **P1/P2:** Log warning and continue catch-up.

**2. Skill Registry (`config/skills.yaml`)**
```yaml
skills:
  uscis_status:
    enabled: true
    priority: P0
    cadence: every_run
    class: background         # goal-bearing | operational | background
  visa_bulletin:
    enabled: true
    priority: P0
    cadence: weekly
    class: goal-bearing
    goal_refs: [G-IMM-001]    # links to state/goals.md (requires goals-reloaded v6.3)
  king_county_tax:
    enabled: true
    priority: P1
    cadence: daily
    class: background
    zero_value_fields: ["tax_amount"]  # per-skill zero-value detection override
  noaa_weather:
    enabled: true
    priority: P1
    cadence: every_run
    class: operational
    trigger_keywords: ["hike", "summit", "trail", "peak"]
  bill_due_tracker:
    enabled: true
    priority: P1
    cadence: every_run
    class: goal-bearing
    goal_refs: [G-FIN-001]
  mental_health_utilization:
    enabled: false            # disabled by R7 — re-enable when needed
    priority: P2
    cadence: daily
    class: background
    suppress_zero_prompt: false
```

**3. Skill Base Class (`scripts/skills/base_skill.py`)**
- All skills inherit from `BaseSkill`.
- **Required fields:** `compare_fields: list[str]` (e.g., `["status_type"]`).
- **Required methods:** `pull()` (network request), `parse()` (parser logic), `to_dict()`.

**4. Skill-Specific Parsers**
- **Visa Bulletin:** Parses `travel.state.gov` Table A & B. Determines authorized chart from USCIS "Adjustment of Status Filing Charts" page. Regex validation: `(\d{2}[A-Z]{3}\d{2}|C|U)`.
- **NOAA Weather:** Two-step call (`/points/{lat},{lon}` → extract forecast URL). Coordinates read from `user_profile.yaml` (location.lat, location.lon). Requires `owner_email` from `config/artha_config.yaml` in User-Agent. `get_skill()` raises `ValueError` when `lat==lon==0.0` (placeholder defaults) to surface misconfiguration early instead of silently fetching a 404. *(v3.8 — F15.126)*
- **USCIS Status:** Checks `https://egov.uscis.gov/csol-api/case-statuses/{receipt}`. HTTP 403 returns `{"blocked": True, "error": "...IP-blocked...check at egov.uscis.gov"}` — distinguishable from transient failures. Other non-200 responses truncate `response.text` to 500 chars. *(v3.8 — F15.127)*
- **NHTSA Recalls:** Prefers `/recalls/recallsByVIN/{vin}` with make/model fallback.

**5. Centralized Cache (`state/skills_cache.json`)** *(v5.0 — schema extended)*
- **Schema:**
  ```json
  {
    "uscis_status": {
      "last_run": "2026-03-26T17:00:00Z",
      "current": {"name": "uscis_status", "status": "success", "data": {}},
      "previous": null,
      "changed": false,
      "health": {
        "total_runs": 25,
        "success_count": 25,
        "failure_count": 0,
        "zero_value_count": 25,
        "last_success": "2026-03-26",
        "last_failure": null,
        "last_nonzero_value": null,
        "consecutive_zero": 25,
        "consecutive_stable": 0,
        "last_wall_clock_ms": 45,
        "r7_skips": 3,
        "last_r7_prompt": null,
        "maturity": "trusted",
        "classification": "degraded"
      }
    }
  }
  ```
- **Classification rules:**
  - `warming_up`: `total_runs < 5` — no adaptive rules fire
  - `healthy`: success_rate ≥ 80% AND had nonzero value in last 10 runs
  - `degraded`: success_rate ≥ 80% BUT `consecutive_zero >= 10`
  - `broken`: success_rate < 50% over last 10 runs
  - `disabled`: `enabled: false` in `config/skills.yaml`
- **Maturity tiers:** `warming_up` (< 5 runs), `measuring` (5–14 runs, R7 cadence reduction eligible), `trusted` (≥ 15 runs, full adaptive behavior)
- **Recovery:** Degraded/broken skills self-heal on non-zero runs; original cadence restored automatically.
- **Security:** Encrypted via `vault.py`. Decrypted before Skill Runner, re-encrypted after catch-up.

**6. Shared Health Library (`scripts/lib/skill_health.py`)** *(v5.0 — new)*

Pure-function library importable by both `skill_runner.py` and the MCP `artha_run_skills` handler. Ensures health counters are updated identically for both execution paths.

Public API:
```python
def is_zero_value(skill_name: str, result: dict, prev_result: dict | None,
                  skills_config: dict) -> bool: ...
def is_stable_value(result: dict, prev_result: dict | None) -> bool: ...
def update_health_counters(cache_entry: dict, is_zero: bool,
                           is_stable: bool) -> dict: ...
def classify_health(health: dict) -> str: ...
def atomic_write_json(path: Path, data: dict) -> None: ...
    # fcntl.flock + tempfile + os.replace. POSIX-only (macOS/Linux).
```

`_CADENCE_REDUCTION = {"every_run": "daily", "daily": "weekly"}` — used by both `skill_runner.py` R7 logic and MCP handler.

**7. Engagement Rate & Run History (`state/catch_up_runs.yaml`)** *(v5.0 — new)*

Every catch-up appends one entry. Read by `briefing_adapter.py` for R2 and R8 rule evaluation. Written by `health_check_writer.py` (same lock/atomic pattern):
```yaml
- timestamp: "2026-03-27T09:00:00Z"
  engagement_rate: 0.33     # null when items_surfaced == 0
  user_ois: 1
  system_ois: 3
  items_surfaced: 6
  correction_count: 1
  briefing_format: standard
  email_count: 30
  domains_processed: [kids, finance, calendar]
```

Retention: last 100 entries (~77 days). `briefing_adapter.py` `_load_catch_up_runs()` reads this file directly (reads `r.get("engagement_rate", r.get("signal_noise"))` for backward compatibility with any legacy entries).

### 8.7 Outbound PII Wrapper for External CLIs

When Artha delegates tasks to Gemini CLI or Copilot CLI (§3.7), a wrapper script ensures no PII leaks to external services.

**Script: `~/OneDrive/Artha/scripts/safe_cli.sh`** (~30 lines, bash)

> See `scripts/safe_cli.sh` for the full PII-safe wrapper script (~30 lines bash). Key steps: validate args, strip PII via `pii_guard.sh`, log call, exec CLI with timeout.
**Artha.md instruction:** "Never call `gemini` or `copilot` directly for queries that may contain user data. Always use `./scripts/safe_cli.sh gemini 'your query'` or `./scripts/safe_cli.sh copilot 'your query'`. The wrapper scans for PII patterns and blocks the call if any are detected."

**Scope:** Applies to all external CLI calls from §3.7 — web research, script validation, ensemble reasoning queries. Does NOT apply to Gemini Imagen calls (which receive only descriptive text prompts, not user data).

---

### 8.10 Agentic Intelligence Modules *(v3.9 — PRD F15.128–F15.132)*

Five tightly-coupled modules that elevate Artha from reactive summarizer to proactive cross-domain intelligence engine. All 4 feature flags default to `enabled: true` and can be individually reverted via `config/artha_config.yaml` under `harness.agentic:`.

---

#### 8.10.1 OODA Reasoning Protocol *(F15.128)*

**File:** `config/workflow/reason.md` (Step 8 rewrite) + `scripts/audit_compliance.py`

The complete Step 8 reasoning loop is restructured into the [Boyd OODA cycle](https://en.wikipedia.org/wiki/OODA_loop):

| Phase | Step | Action |
|-------|------|--------|
| **OBSERVE** | 8-O | Load all domain signals; read `state/memory.md` correction/pattern/threshold facts into active context |
| **ORIENT** | 8-Or | 8-domain cross-connection matrix (finance↔employment, immigration↔finance, health↔employment, etc.); compound-signal detection |
| **DECIDE** | 8-D | U×I×A priority matrix (Urgency × Impact × Agency, 1–3 scale each); rank all items; select ONE THING |
| **ACT** | 8-A | Validation, consequence forecasting (8-A-2), First-Next-After pipeline (8-A-3), dashboard rebuild (8-A-4), PII stats (8-A-5) |

`audit_compliance.py` gains `_check_ooda_protocol(text) → CheckResult` (weight=10). Passes if ≥3 of 4 markers (`[OBSERVE]`, `[ORIENT]`, `[DECIDE]`, `[ACT]`) are present in the briefing text.

Writer checkpoint written after Step 8 completes.

---

#### 8.10.2 Tiered Context Eviction *(F15.129)*

**File:** `scripts/context_offloader.py`

```python
class EvictionTier(IntEnum):
    PINNED       = 0   # Never offloaded (session_summary)
    CRITICAL     = 1   # Standard threshold × 1.0 (alert_list, one_thing, compound_signals)
    INTERMEDIATE = 2   # Standard threshold × 1.0 (predictions, domain_output)
    EPHEMERAL    = 3   # Standard threshold × 0.4 — aggressively evicted (pipeline_output, processed_emails)
```

`offload_artifact(name, content, tier=None)` selects the effective size threshold based on tier. Unknown artifacts default to `INTERMEDIATE`. Feature flag: `harness.agentic.tiered_eviction.enabled` — when `false`, flat threshold is used (backward compatible).

---

#### 8.10.3 ArthaContext Typed Runtime Context Carrier *(F15.130)*

**File:** `scripts/artha_context.py`

```python
class ContextPressure(str, Enum):
    GREEN    = "green"     # < 40% context window used
    YELLOW   = "yellow"    # 40–70%
    RED      = "red"       # 70–90%
    CRITICAL = "critical"  # > 90%

class ArthaContext(BaseModel):
    command: str
    artha_dir: Path
    pressure: ContextPressure = ContextPressure.GREEN
    connectors: list[ConnectorStatus] = []
    degradations: list[str] = []
    steps_executed: list[str] = []
    start_time: datetime
    env_manifest: dict | None = None
    preflight_results: dict | None = None
```

`build_context(command, artha_dir, env_manifest=None, preflight_results=None) → ArthaContext` integrates environment and preflight data into a single typed carrier passed through the middleware stack via `StateMiddleware.before_write(key, value, ctx=None)`. Backward compatible — `ctx` defaults to `None`.

Feature flag: `harness.agentic.context.enabled`.

---

#### 8.10.4 Implicit Step Checkpoints *(F15.131)*

**File:** `scripts/checkpoint.py`

```python
_CHECKPOINT_FILE = "tmp/.checkpoint.json"
_MAX_AGE_HOURS   = 4

def write_checkpoint(artha_dir, last_step: str, **metadata) → None
def read_checkpoint(artha_dir) → dict | None   # Returns None if missing or stale
def clear_checkpoint(artha_dir) → None
```

Checkpoint schema:
```json
{
  "last_step": "step_7_process",
  "timestamp": "2026-03-15T09:23:11.000Z",
  "session_id": "catch-up-2026-03-15T09:20:00",
  "command": "catch-up"
}
```

Written at Steps 4 (fetch), 7 (process), 8 (reason/OODA). Cleared in Step 18 (finalize cleanup). `config/workflow/preflight.md` Step 0a checks for a valid checkpoint and offers resume — auto-resumes in `--pipeline` mode. Feature flag: `harness.agentic.checkpoints.enabled`.

---

#### 8.10.5 Persistent Fact Extraction *(F15.132)*

**File:** `scripts/fact_extractor.py`

Five extraction signal types:

| Type | Example signal phrase |
|------|-----------------------|
| `correction` | "actually", "should be", "is wrong", "I said earlier" |
| `pattern` | "always happens", "every time", "typically", "usually" |
| `preference` | "prefer", "prefers", "preference", "would like", "likes to" |
| `threshold` | "alert me when", "above X", "below Y", "remind me" |
| `schedule` | "every Monday", "first of the month", cron-pattern |

Pipeline: `extract_facts_from_summary(path) → list[Fact]` → `deduplicate_facts(new, existing) → list[Fact]` → `persist_facts(facts, artha_dir) → int` (returns count of new facts written).

PII strip: phone (`\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`), email (`\S+@\S+\.\S+`), SSN (`\b\d{3}-\d{2}-\d{4}\b`) redacted to `[REDACTED]` before persistence.

Deduplication: SHA-256 of fact content (last 16 hex chars as ID). Duplicate = same ID already in `state/memory.md` frontmatter.

`state/memory.md` v2.0 schema:
```yaml
---
schema_version: '2.0'
last_updated: null
facts: []
---
```

Invoked at Step 11c of `config/workflow/finalize.md`. Facts read back into OODA 8-O OBSERVE phase for cross-session continuity. Feature flag: `harness.agentic.fact_extraction.enabled`.

---

### 8.11 Agentic Reloaded — Intelligence Amplification *(v3.9.3 — agentic-reloaded.md AR-1–AR-8)*

Eight targeted enhancements that complete the transition from reactive assistant to self-improving intelligence engine. All new modules integrate with the existing `harness.agentic` flag namespace and are backward compatible.

---

#### 8.11.1 Bounded Memory (AR-1)

**File:** `scripts/fact_extractor.py`

Hard capacity limits prevent unbounded growth of `state/memory.md`:

```python
MAX_MEMORY_CHARS  = 3000   # Total character ceiling for the facts block
MAX_FACTS_COUNT   = 30     # Maximum number of discrete fact entries
```

`_consolidate_facts(facts) → list[Fact]` is invoked when either limit is reached. Consolidation strategy: oldest facts with lowest signal weight are evicted first; facts of the same type with overlapping semantics are merged into a single entry. All existing extraction, deduplication, and PII-strip logic (`extract_facts_from_summary`, `deduplicate_facts`, `persist_facts`) is unchanged.

---

#### 8.11.2 Self-Model (AR-2)

**Files:** `state/templates/self_model.md`, `config/Artha.core.md`

Artha maintains a persistent self-model capturing its operational identity:

```markdown
## Self-Model
- capabilities: [...]
- limitations: [...]
- current_context: {...}
- adaptation_history: [...]
```

`state/templates/self_model.md` provides the starter schema. `config/Artha.core.md` contains the **Self-Model Protocol** sub-section specifying when the model is read and updated (Steps 8-Or and 11c). The self-model is never sent to external APIs — it is a local introspection layer only.

---

#### 8.11.3 Pre-Eviction Fact Flush (AR-3)

**File:** `scripts/session_summarizer.py`

`should_flush_memory(ctx: ArthaContext) → bool` triggers an early fact-extraction pass when context pressure reaches `RED` (≥70%), before eviction would discard valuable in-session observations. The `SessionSummary` schema gains a `pre_flush_facts_persisted: int` field (default `0`) tracking how many facts were saved in the pre-eviction pass. This ensures high-signal facts are never lost due to context window overflow.

---

#### 8.11.4 Session Search / Cross-Session Recall (AR-4)

**File:** `scripts/session_search.py`

Grep-based recall over `briefings/YYYY-MM-DD.md` without requiring vector embeddings:

```python
@dataclass
class SearchResult:
    date: str
    snippet: str
    relevance: float   # match_count / sqrt(line_count)

def search_sessions(query: str, artha_dir: Path,
                    max_results: int = 5,
                    days_back: int = 90) -> list[SearchResult]
```

Results surface in the OODA OBSERVE phase (Step 8-O) when relevant prior sessions exist. `ArthaContext` gains `session_recall_available: bool = False` (set to `True` once search completes with ≥1 result). Feature flag: `harness.agentic.session_recall.enabled`.

---

#### 8.11.5 Procedural Memory (AR-5)

**File:** `scripts/procedure_index.py`, **Dir:** `state/learned_procedures/`

Artha reads, indexes, and executes multi-step procedures it has learned across sessions:

```python
@dataclass
class ProcedureMatch:
    name: str
    path: Path
    confidence: float   # Decays by 50 % per 90 days since last_used
    relevance: float    # Keyword overlap score

def find_procedures(query: str, artha_dir: Path,
                    min_confidence: float = 0.3,
                    min_relevance: float = 0.1) -> list[ProcedureMatch]
```

Procedure files live in `state/learned_procedures/*.md`. `scripts/backup.py` dynamically scans this directory to include all procedure files in GFS backup rotation (slug prefix: `proc__`). `state/learned_procedures/README.md` explains the directory purpose and format. Pre-commit hook updated to allow `state/learned_procedures/` alongside `state/templates/`.

---

#### 8.11.6 Prompt Stability Layer (AR-6)

**Files:** `scripts/generate_identity.py`, `config/Artha.md`

The identity file header is partitioned into frozen vs. ephemeral layers via a stability marker:

```
# ── PROMPT STABILITY ──────────────────────────────────────────────────────
# Frozen layer  (do NOT regenerate from user_profile): lines above this block
# Ephemeral layer (regenerated each run): lines below this block
# ─────────────────────────────────────────────────────────────────────────
```

`generate_identity.py` writes this marker into the generated `config/Artha.md` header. `audit_compliance.py` gains `_check_prompt_stability(text) → CheckResult` (weight=5): passes if the `PROMPT STABILITY` string is present in the identity file. Advisory — does not block a passing audit.

---

#### 8.11.7 Delegation Protocol (AR-7)

**File:** `scripts/delegation.py`

Structured handoff mechanics when Artha escalates a task to an external agent or model:

```python
@dataclass
class DelegationRequest:
    task: str
    context_summary: str   # ≤500 chars compressed via compose_handoff()
    domain: str
    priority: str          # "P0" | "P1" | "P2"

@dataclass
class DelegationResult:
    accepted: bool
    agent_id: str | None
    response: str | None

def should_delegate(task: str, ctx: ArthaContext) -> bool
def compose_handoff(request: DelegationRequest) -> str   # ≤500 char payload
```

`config/Artha.core.md` contains the **Delegation Protocol** section specifying when `should_delegate()` is evaluated (after DECIDE phase, Step 8-D) and how handoff payloads are formatted.

---

#### 8.11.8 Root-Cause Analysis (AR-8)

**Files:** `config/Artha.core.md`, `config/workflow/fetch.md`

Structured root-cause reasoning is embedded in the OODA DECIDE phase and the fetch failure path:

- `config/Artha.core.md` — **Root-Cause Protocol** section: 5-Why template + resolution registry pattern, invoked whenever an alert repeats across ≥2 consecutive sessions.
- `config/workflow/fetch.md` — fetch failure path now includes a "Root-Cause" block distinguishing transient errors (retry) from systematic failures (log + defer) using structured cause categories (`auth`, `rate_limit`, `schema_change`, `network`, `config`).

---

### 8.12 Catch-Up Quality Hardening *(v3.9.4 — catch-up-quality-report 2026-03-15)*

Five targeted fixes that eliminate the most common failure modes observed in production catch-up runs.

---

#### 8.12.1 Email Marketing Classifier (`scripts/email_classifier.py`)

Rule-based, whitelist-first classifier applied inline by `pipeline.py` post-fetch. Zero API calls, zero latency overhead.

**Classification priority (highest → lowest):**
1. `_IMPORTANT_SENDER_DOMAINS` frozenset (government, banks, HR, legal) — always keep
2. `_IMPORTANT_SUBJECT_PATTERNS` regex list (order/shipment, security, immigration, tax) — always keep
3. Auto-notification patterns (GitHub, Jira, calendar invites) — keep, tag as `auto-notification`
4. `_MARKETING_SENDER_PATTERNS` (substack, mailchimp, noreply/newsletter prefixes) — tag `marketing: True`
5. `List-Unsubscribe` / bulk header presence — tag `marketing: True`
6. `_MARKETING_SUBJECT_PATTERNS` (sale, digest, roundup, newsletter) — tag `marketing: True`

Output fields added to each record: `marketing: bool`, `marketing_category: str | None`.

`pipeline.py` calls `_classify_email_lines(lines)` after each connector batch. Falls back silently if `email_classifier` is unavailable (pass-through). Custom domain whitelist configurable in `config/artha_config.yaml` under `email_classifier.whitelist_domains`.

---

#### 8.12.2 Atomic Health-Check Writer (`scripts/health_check_writer.py`)

Replaces the AI instruction-only Step 16 write with a deterministic script.

- **Atomic write**: POSIX `os.replace()` (temp file → rename) prevents partial writes.
- **Lock guard**: acquires `state/.artha-lock` (non-blocking, 3s timeout, fails safely).
- **YAML upsert**: `_update_frontmatter()` adds/updates keys without destroying unrecognized fields.
- **Bootstrap stub detection**: replaces `# Content\nsome: value` stubs with template content.
- **Log rotation**: moves `## Connector health —` blocks older than 7 days to `tmp/connector_health_log.md` (append-only archive), keeping `health-check.md` ≤ ~100 lines.
- **Run history**: `_append_catch_up_run()` appends one structured entry to `state/catch_up_runs.yaml` atomically (same lock + tempfile + os.replace). Retains last 100 entries. *(v5.0 — SKILLS-RELOADED)*
- **Skill management flags**: `--disable-skill` sets `enabled: false` in `config/skills.yaml` with comment; `--suppress-skill-prompt` sets `suppress_zero_prompt: true`. Both actions logged to `state/audit.md`. *(v5.0 — SKILLS-RELOADED)*

CLI: `python3 scripts/health_check_writer.py --last-catch-up ISO --email-count N --domains-processed a,b,c --mode normal|degraded|offline|read-only --briefing-format standard|flash|deep --engagement-rate FLOAT --user-ois N --system-ois N --items-surfaced N --correction-count N [--disable-skill SKILL_NAME] [--suppress-skill-prompt SKILL_NAME]`

| Flag | Notes |
|------|-------|
| `--last-catch-up` | ISO 8601 timestamp (existing) |
| `--email-count` | Count of emails processed (existing) |
| `--domains-processed` | Comma-separated domain list (existing) |
| `--mode` | System health mode: `normal\|degraded\|offline\|read-only` (existing) |
| `--briefing-format` | Briefing format this run: `standard\|flash\|deep` *(new v5.0)* |
| `--engagement-rate` | Float 0.0–1.0; omit or pass empty string when `items_surfaced == 0` *(new v5.0)* |
| `--user-ois` | OIs created by the user (`origin: user`) during this catch-up *(new v5.0)* |
| `--system-ois` | OIs auto-extracted by Step 7b (`origin: system`) *(new v5.0)* |
| `--items-surfaced` | Count of P0/P1/P2 alerts generated during domain processing *(new v5.0)* |
| `--correction-count` | User corrections captured at Step 19 calibration *(new v5.0)* |
| `--disable-skill` | Skill name to disable in `config/skills.yaml` (R7 user response "yes") *(new v5.0)* |
| `--suppress-skill-prompt` | Skill name to suppress R7 disable prompt (R7 user response "keep") *(new v5.0)* |

---

#### 8.12.3 Calendar Event Writer (`scripts/calendar_writer.py`)

Consumes pipeline JSONL output and appends structured calendar events to `state/calendar.md`.

- **Source detection**: `_is_calendar_record()` accepts `google_calendar`, `gcal`, `outlook_calendar`, `msgraph_calendar`, `caldav_calendar`, `workiq_calendar` source tags + `type=event|calendar_event`.
- **Deduplication**: SHA-256 of `(date, title)` → 16-hex key embedded as `<!-- dedup:KEY -->` comment; never writes the same event twice across multiple runs.
- **Bootstrap stub repair**: detects `# Content\nsome: value` and replaces with proper schema before appending.
- **Input**: stdin JSONL, `--input PATH`, or auto-reads `tmp/pipeline_output.jsonl`.

---

#### 8.12.4 OI Backfill Migration (`scripts/migrate_oi.py`)

Idempotent scanner that reconciles `open_items.md` against all domain state files.

- **Pattern**: `\bOI-(\d{3,})\b` at word boundary, skipping table separators.
- **Skip files**: `open_items.md`, `audit.md`, `memory.md`, `health-check.md` (to avoid circular refs).
- **Dedup**: existing IDs in `open_items.md` are never duplicated.
- **Output**: highest OI-NNN seen reported so `state/memory.md` next-ID can be updated.
- **Dry-run**: `--dry-run` flag shows what would be added without writing.

---

#### 8.12.5 Preflight Bootstrap Stub Detection + Token Fixes (`scripts/preflight.py`)

Three targeted improvements to `preflight.py`:

| Fix | Description |
|-----|-------------|
| `_is_bootstrap_stub(path)` | Detects `# Content\nsome: value` fingerprint; `check_state_templates()` now treats stubs as equivalent to missing files for `--fix` replacement |
| Expired token refresh | `check_msgraph_token()` now attempts `ensure_valid_token()` when `secs_left < 0` (already expired), not just within 300s window |
| 48h advance advisory | When MS Graph token is valid but expires within 48h, emits a P1 advisory "run --reauth before next session" to prevent mid-session expiry |

---

## 9. Progressive Fallback Strategy

### 9.1 Philosophy

Start with maximum Claude reliance. Add code only when Claude proves unreliable at a specific, identified step. Each script addresses exactly one failure point.

### 9.2 Anticipated Fallback Points

| Step | Risk of Claude Failure | Fallback Script | Estimated Size |
|---|---|---|---|
| PII pre-flight filtering | N/A — required from day 1 | `pii_guard.py` | ~460 lines |
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

## 9.5 Deep Agents Harness — Component Reference *(v3.6)*

The Deep Agents Harness (Option B, Phases 1–5) is a set of composable infrastructure modules that reduce context pressure, protect state integrity, and validate AI output — without changing the workflow or prompt structure. All phases are feature-flagged under `harness:` in `config/artha_config.yaml` and default to `enabled: true`.

### Phase 1 — Context Offloading (`scripts/context_offloader.py`)

**Purpose:** Writes large intermediate artifacts to `tmp/` when they exceed a token threshold; returns a compact summary card in their place.

| Symbol | Signature | Notes |
|--------|-----------|-------|
| `offload_artifact` | `(name, data, summary_fn, *, threshold_tokens=5_000, preview_lines=10, artha_dir=None) -> str` | Returns card string; writes file to `tmp/`. PII guard must run before calling. |
| `pipeline_summary` | `(data) -> str` | Built-in summary fn for pipeline JSONL output |
| `emails_summary` | `(data) -> str` | Summary fn for processed email batch |
| `domain_extraction_summary` | `(data) -> str` | Summary fn for per-domain extraction |
| `cross_domain_summary` | `(data) -> str` | Summary fn for cross-domain scoring |
| `load_harness_flag` | `(feature_path, default=True) -> bool` | Reads `config/artha_config.yaml` under `harness:` key |
| `OFFLOADED_FILES` | `list[str]` | Manifest of tmp/ files for Step 18a′ cleanup |
| `OFFLOADED_GLOB_PATTERNS` | `list[str]` | Glob patterns for wildcard cleanup |

**Config flag:** `harness.context_offloading.enabled` • **Token threshold:** 5,000 (configurable per call) • **Max card tokens:** 500

### Phase 2 — Progressive Domain Disclosure (`scripts/domain_index.py`)

**Purpose:** Reads YAML frontmatter from `state/*.md` to build a compact domain index, enabling command-aware prompt loading decisions.

| Symbol | Signature | Notes |
|--------|-----------|-------|
| `build_domain_index` | `(artha_dir=None) -> tuple[str, dict]` | Returns (card_text, index_data) |
| `should_load_prompt` | `(domain, index_data, command) -> bool` | True = load full prompt |
| `get_prompt_load_list` | `(index_data, command, routed_domains=None) -> list[str]` | Returns list of domain names to load |
| `_domain_status` | `(days_since_active) -> str` | ACTIVE≤30d, STALE≤180d, ARCHIVE>180d |

**Config flag:** `harness.progressive_disclosure.enabled` • **Index size:** ~600 tokens for 18 domains

### Phase 3 — Session Summarization (`scripts/session_summarizer.py`)

**Purpose:** Creates structured `SessionSummary` objects and writes them to `tmp/`, enabling context compression after heavy commands.

| Symbol | Signature | Notes |
|--------|-----------|-------|
| `SessionSummary` | Pydantic v2 BaseModel | Falls back to dataclass if pydantic absent |
| `create_session_summary` | `(...) -> SessionSummary` | Populates from command outputs |
| `summarize_to_file` | `(summary, session_n, artha_dir) -> Path` | Writes `.md` + `.json` to `tmp/` |
| `estimate_context_pct` | `(text, model_limit_chars=800_000) -> float` | 0.0–1.0 |
| `should_summarize_now` | `(context_text, command=None) -> bool` | True if above threshold or post-command |
| `get_context_card` | `(summary) -> str` | Compact card replacing full session history |
| `load_threshold_pct` | `() -> float` | Default 70.0 |

**Config flag:** `harness.session_summarization.enabled` • **Threshold:** `harness.session_summarization.threshold_pct` (default: 70)

### Phase 4 — Middleware Stack (`scripts/middleware/`)

**Purpose:** Composable `StateMiddleware` Protocol that all state file writes pass through.

**Protocol** (`scripts/middleware/__init__.py`):
```python
class StateMiddleware(Protocol):
    def before_write(
        self, domain: str, current_content: str, proposed_content: str,
        ctx: Any | None = None,  # ArthaContext carrier — forwarded through composed chains
    ) -> str | None: ...
    # Returns None to BLOCK the write; returns (possibly modified) content to allow
    def after_write(self, domain: str, file_path: str) -> None: ...

def compose_middleware(middlewares: list[StateMiddleware]) -> StateMiddleware: ...
# before_write: left-to-right chain, ctx forwarded to every child • after_write: right-to-left
```

`_ComposedMiddleware.before_write` accepts `ctx: Any | None = None` and passes it to every child middleware's `before_write` call. All concrete `StateMiddleware` implementations must accept `ctx=None` to be composable.

| Module | Class | Behaviour |
|--------|-------|-----------|
| `pii_middleware.py` | `PiiMiddleware` | Runs `pii_guard.py filter` subprocess; redacts and continues (never blocks) |
| `write_guard.py` | `WriteGuardMiddleware(max_loss_pct=20.0)` | Counts YAML fields via `r"^\s{0,4}[\w_-]+\s*:"`, blocks if loss > threshold. Bootstrap files (`updated_by: bootstrap`) exempt. |
| `write_verify.py` | `WriteVerifyMiddleware` | Post-write: checks file exists + >100B, starts with `---`, has `domain:` field, has `last_updated:` ISO-8601. Logs failures to `state/audit.md`. |
| `audit_middleware.py` | `AuditMiddleware` | Appends `MIDDLEWARE_WRITE` entries to `state/audit.md`. `log_event(event_type, details)` available for custom events. |
| `rate_limiter.py` | `RateLimiterMiddleware` | Sliding 60s window per provider. Reads limits from `config/connectors.yaml`. Defaults: Gmail 30/min, MS Graph 20/min, iCloud 10/min. Raises `RateLimitExceeded`. |

**Config flag:** `harness.middleware.enabled` (checked inside `compose_middleware` — returns passthrough when disabled)

### Phase 5 — Structured Output Validation (`scripts/schemas/`)

**Purpose:** Pydantic v2 schemas validate AI-generated output structures before downstream consumption.

| Schema | Module | Key Constraints |
|--------|--------|----------------|
| `BriefingOutput` | `schemas/briefing.py` | `one_thing` ≤300 chars, `briefing_format` Literal enum, requires `pii_footer` |
| `AlertItem` | `schemas/briefing.py` | `severity` Literal(`critical`/`urgent`/`standard`/`info`), `score` 0–27 |
| `DomainSummary` | `schemas/briefing.py` | `bullet_points` max_length=5, `alert_count` ≥0 |
| `FlashBriefingOutput` | `schemas/briefing.py` | Compact variant for flash mode |
| `SessionSummarySchema` | `schemas/session.py` | `key_findings` max_length=5 with `mode="before"` validator for graceful truncation |
| `DomainIndexCard` | `schemas/domain_index.py` | `from_index_data(card_text, index_data)` factory |

**Output artifact:** `tmp/briefing_structured.json` • **Config flag:** `harness.structured_output.enabled`
**Graceful degradation:** Validation failure → log to `state/audit.md` + increment `harness_metrics.structured_output.validation_errors` — never blocks briefing output.

### Harness Feature Flags (`config/artha_config.yaml`)

```yaml
harness:
  context_offloading:
    enabled: true
    threshold_tokens: 5000
  progressive_disclosure:
    enabled: true
  session_summarization:
    enabled: true
    threshold_pct: 70
  middleware:
    enabled: true
  structured_output:
    enabled: true
```

All flags default to `true` when absent. Set `enabled: false` to restore pre-harness behaviour for any phase independently.

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

> See [setup.sh](../setup.sh) and [setup.ps1](../setup.ps1) for the full cross-platform setup scripts.
> The scripts perform: directory scaffolding; config/Artha.md + CLAUDE.md creation; config/channels.yaml + connectors.yaml initialization; vault key generation; Gmail MCP configuration; first-run validation. Run ash setup.sh (Mac/Linux) or .\setup.ps1 (Windows).


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

**Email:** `scripts/connectors/msgraph_email.py` (refactored from standalone `msgraph_fetch.py`). Fetches `GET /me/mailFolders/inbox/messages?$filter=receivedDateTime ge {since}`. Output: same JSONL schema as `gmail_fetch.py` with `"source": "outlook"` field. Runs in parallel with `gmail_fetch.py` at catch-up Step 3.

**Calendar:** Build `scripts/msgraph_calendar_fetch.py` per T-1B.1.6. Fetches `/me/calendars/{id}/calendarView`. Output: same JSONL schema as `gcal_fetch.py` with `"source": "outlook_calendar"`. Runs in parallel with `gcal_fetch.py` at catch-up Step 3.

**Why direct API over forwarding:**
- No silent failure modes (forwarding can silently stop; API returns explicit HTTP errors)
- Source attribution built-in (`"source": "outlook"`) — routing rules can use it
- Microsoft email stays in Microsoft infrastructure — no cross-platform routing
- Single OAuth token already covers email + calendar + To Do

**Apple iCloud Mail:** No public API available — forwarding to Gmail is still required. See T-1B.1.2.

---

### 11.4 Entry Point & Interactive Setup Wizard *(v3.3 — PRD F15.89–F15.94)*

`artha.py` is the primary CLI entry point for both new and returning users.

#### Flags

| Flag | Behavior |
|---|---|
| *(no flag)* | Detects configured vs unconfigured state. Unconfigured → wizard prompt; Configured → `do_welcome()` only (no auto-preflight). |
| `--setup` | Run interactive setup wizard. |
| `--setup --no-wizard` | Copy `user_profile.starter.yaml` for manual editing; print next-step card. |
| `--demo` | Show demo briefing with fictional data (no accounts needed). |
| `--preflight` | Run preflight gate and exit. |

#### AI CLI Detection (`do_welcome()` + `do_setup()` completion)

`_AI_CLIS` constant: list of `(cmd, name, url)` tuples for known AI CLIs (`claude`, `gemini`).

`_detect_ai_clis() -> list[tuple[str, str, bool]]` — calls `shutil.which(cmd)` for each entry; returns `(name, url, is_installed)`. Stdlib-only, no new dependencies.

`_print_ai_cli_status()` — called after wizard completion and in `do_welcome()`:
- If any CLI detected (including `code` for VS Code / GitHub Copilot): lists detected names, shows tailored `Your next step: → Run: <cmd>  (then say: catch me up)`.
- If none found: shows install URLs for all known CLIs + VS Code/Copilot.

#### setup.sh Step Counters (v5.7)

`setup.sh` displays `[1/4] Checking prerequisites...` through `[4/4] Running demo briefing...` with a branded header `A R T H A  —  Personal Intelligence OS`. `pip install` uses `--disable-pip-version-check` to suppress internal path leakage in upgrade notices.

#### Setup Wizard (`do_setup()`)

Collects five inputs interactively:
1. **Name** — required; loops until non-empty
2. **Email** — validated for `@` presence; auto-detects type (gmail/outlook/icloud) to pre-configure integrations
3. **Timezone** — accepts shortcuts (`ET`→`America/New_York`, `PT`→`America/Los_Angeles`, `IST`→`Asia/Kolkata`, `CT`/`MT`/`UTC`/`GMT`/`BST`/`CET`/`JST`/`AEST`); falls back to full IANA string passthrough
4. **Household type** — `single` / `couple` / `family`
5. **Children** — up to 6 (name, age, grade); only if household = `family`

After inputs: writes `config/user_profile.yaml` as formatted YAML string (not `yaml.dump`), auto-runs `generate_identity.py`, prints success box with next steps.

#### Starter Profile (`config/user_profile.starter.yaml`)

- 45 lines; blank `name` and `email` fields force user to fill in real data
- `_validate()` rejects blank/missing name and any-empty emails — starter profile intentionally fails validation
- Used by `--no-wizard` path and `setup.sh` non-interactive (CI) path
- Full reference with all options remains in `config/user_profile.example.yaml` (234 lines)

#### Advisory Warnings (`generate_identity.py`)

`_collect_warnings(profile) -> list[str]`:
- Placeholder child names (`Child1`, `Child2`, `ChildName`, `Child`) → indexed advisory `family.children[0].name is still placeholder 'Child1'`
- Placeholder cities (`Springfield`, `Anytown`, `Your City`, `Exampleville`) → advisory
- Non-blocking: generation proceeds even with warnings

`_print_validate_summary(profile)`:
- Prints identity preview on `--validate` success: name (email), location, enabled domains
- Helps users confirm their profile was read correctly before committing to a full generate

#### First-Run Preflight (`preflight.py --first-run`)

`--first-run` flag activates a Setup Checklist display mode:
- Header: `━━ ARTHA SETUP CHECKLIST ━━━`
- OAuth/connector failures detected by `fix_hint` content (setup_google_oauth/setup_msgraph_oauth/setup_icloud_auth) displayed as `○ [P0] Gmail OAuth token: not yet configured`
- Exit 0 when only expected first-run setup items remain incomplete
- Truly unexpected P0 failures (broken PII guard, missing state dir) still cause exit 1

---

Artha is a living system: new domains, data sources, MCP servers, and AI capabilities will be added as trust and utility grow. This section defines the lifecycle processes that keep the system coherent, extensible, and self-improving.

### 12.1 Component Registry (
egistry.md)

~/OneDrive/Artha/config/registry.md is the system manifest — a single file listing every prompt, state file, script, MCP server, hook, slash command, external CLI, and action channel in the system.

> See config/registry.md for the full registry. Sections: Prompts · State Files · MCP Servers · Hooks · Scripts · Slash Commands · External CLIs · Action Channels · Config Files.

**Maintenance:** Update 
egistry.md whenever a component is added, updated, or removed. Registry drift = broken observability.
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

---

## 20. v5.1 Technical Implementation

*Source: `specs/ui-reloaded.md` Parts VI–VIII — implemented March 2026.*

### 20.1 Intent Router — Bridge Path Parity (Part VI)

The bridge path (`cmd_ask()` in `scripts/channel/llm_bridge.py`) gains an intent classification layer so it invokes the same structured pipelines as slash commands. **~145 new lines, no new dependencies.**

**Architecture:**

```python
# scripts/channel/llm_bridge.py additions

_INTENT_PATTERNS: dict[str, list[str]] = {
    "brief": [r"catch me up", r"morning briefing", r"sitrep", r"what did i miss", r"brief me"],
    "work": [r"work briefing", r"what'?s happening at work", r"work update", r"work catch.?up"],
    "items": [r"what'?s open", r"open items", r"show.*items", r"what'?s overdue", r"what'?s due"],
    "goals": [r"how are my goals", r"goal pulse", r"show.*goals", r"goal progress"],
    "status": [r"how'?s everything", r"quick status", r"artha status"],
    "work-prep": [r"prep me for", r"prepare for my meeting", r"meeting prep", r"ready for my", r"what should i know before"],
    "work-sprint": [r"sprint health", r"delivery health", r"how'?s the sprint"],
    "work-connect-prep": [r"prepare for.*connect", r"connect review", r"review prep", r"calibration"],
    "content-draft": [r"write a.*post", r"draft.*linkedin", r"draft.*post", r"write.*linkedin"],
    "items-done": [r"mark.*done", r"complete.*item", r"finished.*item", r"done with"],
    "items-quick": [r"anything quick", r"quick wins", r"what can i knock out", r"5.?min.*tasks?"],
    "immigration-query": [r"visa status", r"immigration", r"ead", r"green card", r"priority date"],
    "teach": [r"explain\b", r"teach me", r"what is.*\?", r"what does.*mean"],
    "dashboard": [r"show.*everything", r"full.*dashboard", r"life dashboard", r"big picture"],
    "radar": [r"what'?s new in ai", r"ai trends?", r"show radar", r"ai news"],
}

def _classify_intent(message: str) -> str | None:
    """Return the first matching intent key, or None if no match."""
    msg = message.lower()
    for intent, patterns in _INTENT_PATTERNS.items():
        if any(re.search(p, msg) for p in patterns):
            return intent
    return None

def _fuzzy_resolve_item(description: str, items_text: str) -> str | None:
    """Find the best matching OI-NNN for a natural language description."""
    # Parse items_text for OI-NNN lines
    # Score each by word overlap with description
    # Return OI-NNN if single strong match, None if ambiguous
```

**Intent → context mapping:**

| Intent | State files loaded | Pipeline equivalent |
|---|---|---|
| `brief` | All always-load state files (80K budget) | `/brief` |
| `work` | All `state/work/*` files | `/work` |
| `work-prep` | work-calendar + work-people + work-comms + work-notes | `/work prep` |
| `work-sprint` | work-projects + work-goals + work-performance | `/work sprint` |
| `items` | open_items.md only | `/items` |
| `goals` | goals.md only | `/goals` |
| `content-draft` | pr_manager.md + gallery.yaml + voice profile | `/content draft` |
| `immigration-query` | prompts/immigration.md + state/immigration.md | `/domain immigration` |
| `dashboard` | All always-load state files + domain summaries | `/brief everything` |
| `teach` | No state files — LLM uses Artha.md knowledge | NL direct |

**Security boundary — bridge path restriction:** Auto-decrypt for encrypted domains is **disabled on the bridge path** (Telegram, async). When a bridge request references a sensitive domain (finance, immigration, health, insurance, estate, vehicle, caregiving), Artha responds: *"That domain contains sensitive data. For security, I can only access it from your terminal session."* Non-encrypted domains (calendar, comms, goals, items, non-sensitive work files) are loaded normally.

### 20.2 Auto-Vault Module (Part VII)

New module: `scripts/lib/auto_vault.py` (~80 lines)

```python
"""Transparent encryption setup — user never sees this."""

def ensure_encryption_ready() -> bool:
    """
    Check if encryption is configured. If not, set it up silently.
    Returns True if encryption is available, False if it cannot be set up
    (in which case state files remain unencrypted with a logged warning).
    """
    # 1. Check if age binary is installed
    # 2. Check if keypair exists in keyring
    # 3. If not: generate keypair, store in keyring, write public to profile
    # 4. If age not installed: log warning, return False
    # 5. If keyring unavailable: log warning, return False
```

Called by `vault.py` before any encrypt/decrypt operation. Encrypted domains (finance, immigration, health, insurance, estate, vehicle, caregiving) remain blocked if encryption cannot be set up — they never fall back to plaintext.

### 20.3 Error Messages Module (Part VIII)

New module: `scripts/lib/error_messages.py` (~60 lines)

A mapping from internal error codes to user-facing messages with zero implementation internals exposed:

| Internal error | User sees |
|---|---|
| `gmail_401_expired` | "I couldn't reach your Gmail. Say 'reconnect Gmail' to fix it." |
| `outlook_401_expired` | "Your Outlook connection expired. Say 'reconnect Outlook' to fix it." |
| `vault_no_key` | "Your data vault needs a quick fix. Say 'fix encryption' and I'll walk you through it." |
| `vault_stale_lock` | *(auto-cleared silently)* |
| `connector_timeout` | "One of your data sources took too long to respond. I continued with available data." |
| `python_traceback` | "Something unexpected happened. I logged the details and continued with available data." |

**Self-healing token refresh** (Step 4 of catch-up pipeline): attempt token refresh on 401/403 before surfacing any error. Increment `consecutive_failure_count`; surface fix instruction only after 3 consecutive failures or 7-day first-failure bound. Silent skip on first failure.

### 20.4 Preflight Severity Changes (Part VII)

Three checks downgraded from P0 (blocking) to P1 (warn + skip):

| Check | Before | After | Effect |
|---|---|---|---|
| Gmail OAuth token | P0 | P1 | Briefing runs without Gmail data |
| Calendar OAuth token | P0 | P1 | Briefing runs without calendar events |
| Gmail API health | P0 | P1 | Briefing runs without Gmail data |

**Remaining P0 checks (unchanged):** keyring backend, vault health (if encrypted files exist), PII guard, state directory writable, vault lock state.

### 20.5 Context Budget Management

The bridge path uses an 80K character budget for full-briefing context loads:
- Always-load state files loaded first (goals, open_items, summaries)
- Domain state files loaded in priority order until budget is reached
- Encrypted domains skipped on bridge path regardless of budget

### 20.6 Cross-CLI Instruction File Design

The instruction files (CLAUDE.md, GEMINI.md, AGENTS.md) follow three rules:
1. **Self-sufficiency:** fallback routing skeleton works without Artha.md loaded
2. **No built-in collisions:** `/brief`, `/guide`, `/health` are collision-free on Claude Code, Gemini CLI, and VS Code Copilot
3. **Token economy:** AGENTS.md target ≤30 lines (Copilot injects it twice); CLAUDE.md/GEMINI.md ≤40 lines

Known CLI reserved command collisions: Claude Code (`/compact`, `/help`); Gemini CLI (`/help`, `/memory`, `/model`); VS Code Copilot (`/fix`, `/explain`, `/tests`, `/doc`, `/new`, `/clear`, `/help`).
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
├── PII filter ──────── [script: pii_guard.py] ── requires: Python 3.11+
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
| pii-scan | PreToolUse (Gmail) | Auto-run pii_guard.py before processing emails | Phase 2 |
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

### 7.16 `/diff` Command *(v2.1)*

Shows state file changes since last catch-up. Reads local state files (no email fetch). Sections per domain: additions, removals (marked with ~), value changes. Domains with no changes listed under "No Changes."

### 7.17 Weekend Planner *(v2.1)*

Triggered Friday ≥12PM. Generates Saturday/Sunday optimization: open time windows, upcoming deadlines, quick tasks from open_items. Weekend-specific deadlines section.

### 7.18 Canvas LMS API Fetch *(v2.1)*

`canvas_fetch.py`: REST API w/ developer token → JSONL (courses, assignments, grades, submissions). Per-student active courses with assignment detail. Integrated into catch-up Step 4 parallel fetch.

### 7.19 Apple Health XML Import *(v2.1)*

`parse_apple_health.py`: Parse Apple Health `export.xml` → `state/health-metrics.md`. Extracts steps, sleep, heart rate, workouts, weight (configurable metrics list). Manual export trigger via Apple Shortcut.

## 15. Phased Implementation Summary

See `specs/artha-prd.md` §13 for the detailed phased roadmap plus effort estimates. Summary:
- **Phase 1A** (Weeks 1–2): Core setup — Artha.md, MCP config, vault, PII guard, initial state files, end-to-end validation. ~21-25 hours.
- **Phase 1B** (Weeks 3–5): High-value domains (immigration, finance, kids, comms), Outlook forwarding, ensemble reasoning test.
- **Phase 1C** (Weeks 6–8): Goal Engine, finance expansion, conversation memory, Claude.ai Project.
- **Phase 2A**: 18 intelligence amplification workstreams (Data Integrity Guard P0, Bootstrap P0, then Pattern/Signal/Compression/Context/OAuth/Volume/Scorecard P1). v4.0 additions: Briefing Intelligence, Scheduling, Goal Expansion, Conversational Intelligence, Family & Cultural.
- **Phase 2B**: Canvas LMS API, Apple Health, Tax Season, Subscription ROI.
- **Phase 2C (implemented):** Work Intelligence OS — see §19 (Tech Spec) for full technical architecture.
- **Phase 3**: Estate, WhatsApp Bridge, Emergency Contact Wallet Card.

## 16. Open Design Decisions

**Resolved:** TD-3 (OneDrive sync), TD-5 (historyId for idempotency), TD-6 (per-domain redaction), TD-7 (age encryption), TD-9 (start strict, tune to <5% FP), TD-10 (quarterly governance), TD-11 (Claude-as-synthesizer), TD-16 (direct MCP send at Trust Level 1), TD-17 (pure CLAUDE.md, portability surface small).

**Open — resolve during Phase 1A:**

| # | Decision | Recommended |
|---|---|---|
| TD-1 | Gmail MCP server selection | Whichever supports OAuth + search + read reliably |
| TD-2 | Email sending mechanism | Simplest working option (MCP send preferred) |
| TD-4 | Keychain integration with MCP env vars | Test if MCP config supports $() |
| TD-8 | OneDrive path per platform | Configure in settings.md |
| TD-12 | WhatsApp contact management | Start with contacts.md |
| TD-13 | Gmail MCP write scope | `gmail.send` first; `gmail.modify` at Trust Level 2 |
| TD-14 | Gemini Imagen output format | PNG first, JPEG if size issue |
| TD-15 | Calendar write scope | Primary personal calendar only |
| TD-18 | Priya's email access | Determine forwarding vs. separate scope |
| TD-19 | pii_guard.sh interception | Test PreToolUse hook interception |
| TD-20–TD-30 | Various (scorecard weights, baselines, thresholds, Canvas token, Apple Health, calibration format, diff storage, college milestones) | Start with documented defaults; adjust based on real-world feedback |

## 17. Testing Architecture

To support the automated testing requirements in PRD §14.4, Artha employs a Python-native testing framework.

### 17.1 Framework & Tooling

- **Testing Library:** `pytest` (standard Python testing framework).
- **Mocking:** `pytest-mock` for isolating scripts from the filesystem, network, and credential store.
- **Snapshot Testing:** `pytest-snapshot` or custom logic for "Golden File" validation.
- **Test count:** 698 passed (personal surface), ~1,375 passed (Work OS — `tests/work/`), 7 skipped, 20 xfailed (post v3.16 Work OS implementation; Work OS count reflects Reflection Loop implementation — see §19.11).
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

## 19. Work OS — Technical Architecture

> **Status:** Phases 1–5 complete (v2.7.0). 1,060+ tests in `tests/work/` (28 test files). All design rules, privacy guarantees, workflow details, and interaction patterns are documented in this spec (§19) and UX Spec §23.

### 19.1 Hard Separation Model

The Work OS operates as a fully isolated intelligence surface. The separation is enforced at three levels:
1. **Storage:** `state/work/` (separate directory, separate vault key `work`)
2. **Code:** `scripts/work_loop.py` never imports personal surface scripts
3. **Bridge:** The only channel to the personal surface is the schema-validated `state/bridge/work_load_pulse.json` artifact (a single float `boundary_score`, counts, and timestamp — no text content)

### 19.2 Processing Pipeline

`scripts/work_loop.py` implements a 7-stage loop (§8.5). Stages 1–5 are user-visible; 6–7 run post-loop:

| Stage | Name | Budget | Description |
|---|---|---|---|
| 1 | **preflight** | <2s | Provider health check (Agency, Graph, ADO, Outlook, Kusto), token validation, state freshness computation, `ProviderAvailability` map |
| 2 | **fetch** | <2s (read) / <60s (refresh) | READ: load state files. REFRESH: delegate to Agency CLI or direct Graph/ADO/Kusto providers |
| 3 | **process** | <5s | REFRESH: call domain writers (`work_domain_writers.py`) with collected data, including Kusto metrics → `xpf-program-structure.md`. Both: load `work-summary.md` into `result.summary` |
| 4 | **reason** | <10s | REFRESH: generate `work-comms.md` prioritization and `work-boundary.md` scores (via Agency or fallback logic) |
| 5 | **finalize** | <1s | Build `result.summary`, write bridge pulse `work_load_pulse.json` |
| 6 | **audit** | <0.1s | Append run record to `state/work/work-audit.md` (non-blocking) |
| 7 | **learn** | <0.5s | REFRESH only: `_update_eval_metrics()` → `work-metrics.json`; `_update_learned_state()` → `work-learned.md` with `days_since_bootstrap`, `learning_phase`, `refresh_runs` |

**CLI:** `python scripts/work_loop.py --mode [read|refresh] [--quiet] [--state-dir <path>]`

### 19.3 Canonical Object Layer

`scripts/schemas/work_objects.py` defines 6 canonical dataclasses consumed by all stages:

| Class | Required fields | Key optional fields |
|---|---|---|
| `WorkMeeting` | event_id, title, start_dt, end_dt, duration_minutes | is_recurring, series_id, attendee_ids, readiness_score |
| `WorkDecision` | decision_id, context, decision_text, decided_at | outcome (default: PENDING), decided_by, pattern |
| `WorkCommitment` | commitment_id, title, made_at | due_date, status (default: OPEN), owner |
| `WorkStakeholder` | stakeholder_id, display_name | org_context, seniority_tier, is_manager, recency |
| `WorkArtifact` | artifact_id, title, artifact_type, last_modified | project_context, modified_by_self, link |
| `WorkSource` | source_id, title, url, answers | source_type, tags, linked_projects |

**Enums:** `ObjectStatus` (FRESH, STALE, ACTIVE, CLOSED, AT_RISK), `CommitmentStatus` (OPEN, DELIVERED, DEFERRED, DROPPED), `DecisionOutcome` (PENDING, POSITIVE, NEGATIVE, NEUTRAL), `StakeholderRecency` (RECENT, STALE, COLD).

All objects default to `source_domains: list[str]` and `last_updated: str` (ISO-8601 from `_utcnow()`). Fully serializable via `dataclasses.asdict()`.

### 19.4 Connector Error Protocol

`scripts/schemas/work_connector_protocol.py` implements §8.4. Every connector has a defined failure response:

| Principle | Implementation |
|---|---|
| No single failure blocks the workflow | `blocks_workflow: bool = False` on all PROTOCOL entries |
| Cached state over no output | `fallback_action` field specifies exactly what cached source to use |
| Actionable error messages | `user_signal` (status line) + `remediation` (what to do) per entry |
| Every failure is audited | `log_connector_failure()` → `state/work/work-audit.md` (silent on write failure) |

Covered connectors: `workiq_bridge`, `ado_workitems`, `msgraph_email`, `msgraph_calendar`, `outlookctl_bridge`, `kusto_metrics`, `*` (wildcard for ALL_DOWN).

### 19.4a Kusto Integration Architecture

`scripts/kusto_runner.py` provides the Kusto bridge for XPF program metrics:

| Component | Location | Purpose |
|---|---|---|
| `run_refresh_set()` | `kusto_runner.py:375` | Batch API — runs curated golden queries (GQ-001/002/010/012/050/051), returns `{qid: {rows, card, error}}` |
| `_check_kusto_available()` | `work_loop.py:389` | Preflight probe — runs `TenantCatalogSnapshot | take 1` against xdeployment |
| `_run_kusto_metrics()` | `work_loop.py:660` | Fetch provider — calls `run_refresh_set()`, stores in `connector_data["kusto"]` |
| `write_kusto_metrics_state()` | `work_domain_writers.py:958` | Domain writer — regex-updates metric values in `xpf-program-structure.md` in-place |
| `_load_program_metrics()` | `narrative_engine.py:110` | Read-path parser — extracts signal summary, risk posture, per-WS signals from program structure |

**Auth:** `DefaultAzureCredential` (Azure CLI login on Devbox). No separate token management.

**Golden query registry:** `state/work/golden-queries.md` — 73 queries across 12 Kusto clusters. 31/44 tested pass validation.

**Clusters verified on Devbox:** `xdeployment.westcentralus`, `apdmdata`, `azuredcm`, `icmcluster`, `1es`. DNS-blocked: `azcore`, `xsse` (non-regional URIs).

**PF cross-cluster join pattern:** 12 Env_Machines golden queries (GQ-021, 024, 041, 060, 062–066, 068, 091, 121) use a cross-cluster PF join: `let PF_Clusters = cluster('xdeployment.westcentralus.kusto.windows.net').database('Deployment').TenantCatalogSnapshot | where IsPF == true and ClusterName != '' | distinct ClusterName;` then filter with `| where AutoGen_Cluster in (PF_Clusters)`. Also uses `AutoGen_TimeStamp` (not `TIMESTAMP`) for all Env_Machines queries. Validated live (GQ-060 confirmed working).

### 19.5 Bridge Schema Enforcement

`scripts/schemas/bridge_schemas.py` validates bridge artifacts consumed by the personal surface:

**Artifact schemas:**
- `work_load_pulse` (work→personal): `{$schema, generated_at, date, total_meeting_hours, after_hours_count, boundary_score, focus_availability_score}` — all numeric except 3 metadata strings. `additionalProperties: false`.
- `personal_schedule_mask` (personal→work): `{$schema, generated_at, date, blocks: [{busy_start, busy_end, type}]}` — time blocks only, no event titles or names.

**Alert Isolation (§9.6):** `validate_alert_isolation(artifact, surface)` enforces that work→personal bridge carries only aggregate numeric metrics and personal→work carries only `{$schema, generated_at, date, blocks}`. Semantic check runs before schema validation. Raises `BridgeValidationError` with "isolation violation" on any string or collection in a non-metadata field.

**Prohibited fields** (enforced across both artifacts): `title`, `attendees`, `body`, `notes`, `summary`, `subject`, `people`, `names`, `meeting_names`, `description`, `project_names`.

~67 tests in `tests/work/test_bridge_enforcement.py` (55 original + 12 `TestAlertIsolation`).

### 19.6 Warm-Start Processor

`scripts/work_warm_start.py` populates all 6 state files from historical scrape data in one pass:

```
python scripts/work_warm_start.py --scrape-dir <path/to/scrapes> [--dry-run]
```

**`ScrapeParser`** handles two historical formats:
- Table format (2025+): `| # | Title | Organizer | Attendees | Start–End | Notes |`
- Bullet format (2024): `- **Title** *(VED organizer)*\n  Organizer: ...\n  Attendees: N`

**`WarmStartAggregator`** builds:
- People graph: `PersonRecord` with relationship score (meetings × 1 + chats × 2 + organized × 0.5)
- Projects: detected from meeting title patterns (e.g. Platform-Alpha, Platform-Beta) via _PROJECT_SIGNALS
- Career evidence: newsletter sends, LT decks, program ownership, recognition
- Recurring meetings: detected at configurable min_occurrences threshold

After a successful import, sets `config/user_profile.yaml → work.bootstrap.import_completed: true`.

### 19.7 State Files

| File | Purpose | Schema version |
|---|---|---|
| `state/work/work-people.md` | People graph with relationship scores and tiers | 1.0 |
| `state/work/work-projects.md` | Active project map with meeting frequency | 1.0 |
| `state/work/work-calendar.md` | Recurring meeting patterns and calendar norms | 1.0 |
| `state/work/work-career.md` | Career evidence events log | 1.0 |
| `state/work/work-sources.md` | Data source / dashboard catalog | 1.0 |
| `state/work/work-summary.md` | Aggregate warm-start / refresh summary | 1.0 |
| `state/work/work-audit.md` | Connector failure audit log (append-only) | — |
| `state/work/work-comms.md` | Communication patterns, email threads, key decisions via email | 1.0 |
| `state/work/work-boundary.md` | Work-life boundary state: hours, after-hours patterns, boundary score | 1.0 |
| `state/work/work-performance.md` | Performance signals, manager feedback, peer recognition | 1.0 |
| `state/work/work-notes.md` | Post-meeting decisions (D-NNN) and open items (OI-NNN) | 1.0 |
| `state/work/work-decisions.md` | Decision registry with outcomes and recurrence tracking | 1.0 |
| `state/work/work-open-items.md` | Open item tracker with OI-NNN IDs, owners, deadlines | 1.0 |
| `state/work/work-project-journeys.md` | Project timeline with milestone evidence and scope arc | 1.0 |
| `state/work/work-org-calendar.md` | Org dates: Connect deadlines, fiscal year, all-hands cadence | 1.0 |
| `state/work/work-promo-narrative.md` | Promotion readiness narrative and evidence inventory | 1.0 |
| `state/work/work-incidents.md` | ICM incident history (Microsoft Enhanced tier) | 1.0 |
| `state/work/work-repos.md` | Repository contribution signals (Microsoft Enhanced tier) | 1.0 |
| `state/work/work-learned.md` | Learning model: calibration/prediction/anticipation phases | 1.0 |
| `state/work/work-products.md` | Product knowledge index: taxonomy, per-product summary, deep file pointers | 1.0 |
| `state/work/work-goals.md` | Work OS goals: active, parked, done goals with v2.0 schema (identical to personal `state/goals.md`, `work_os: true`; read only by Work OS pipeline, never by personal catch-up) | 2.0 |
| `state/work/work-accomplishments.md` | Chronological accomplishment ledger — canonical source for Connect prep and promo docs | 1.0 |
| `state/work/products/*.md` | Per-product deep knowledge: architecture, components, dependencies, teams | 1.0 |
| `state/work/work-kusto-catalog.md` | KQL query catalog with "what question" annotations | — |
| `state/bridge/work_load_pulse.json` | Personal-surface boundary artifact (WorkLoadPulse schema v1) | 1 |
| `state/bridge/work_context.json` | Rich work context bridge artifact (BridgeArtifact schema) | 1 |

All files use YAML frontmatter with `schema_version`, `encrypted`, and `last_updated`. `work-people.md` and `work-career.md` carry `encrypted: true` (handled by the `work` vault key).

### 19.8 Agent Configurations

Three tier-specific Agency agent configs in `config/agents/`:
- `artha-work.md` — baseline tier (any M365); orchestrates Graph-only read path
- `artha-work-enterprise.md` — corporate tier; Graph + ADO MCP
- `artha-work-msft.md` — Microsoft Enhanced tier; Graph + ADO + WorkIQ + Bluebird MCPs

Tier selection happens in `_check_agency_available()` at preflight: probes `agency mcp list` output for `workiq`/`bluebird`→msft, `ado`→enterprise, else→baseline.

### 19.9 Test Coverage

| Test file | Tests | What it validates |
|---|---|---|
| `tests/work/test_work_objects.py` | 48 | All 6 dataclasses, enums, serialization, cross-object consistency |
| `tests/work/test_connector_protocol.py` | 33 | PROTOCOL table structure, get_protocol(), user_signal_for(), log_connector_failure() |
| `tests/work/test_work_warm_start.py` | 122 | ScrapeParser (both formats), WarmStartAggregator, state writers, atomic write, dry-run, full run |
| `tests/work/test_bridge_enforcement.py` | 73 | Bridge schema enforcement, PII rejection, cross-surface access control (§9.8), alert isolation (§9.6) |
| `tests/work/test_domain_writers.py` | 52 | Domain state writers: calendar, comms, projects, boundary, career, sources, notes, decisions, open-items |
| `tests/work/test_narrative_engine.py` | 127 | NE templates: weekly_memo, talking_points, boundary_report, connect_summary, newsletter, deck, calibration_brief, connect_evidence, escalation_memo, decision_memo |
| `tests/work/test_work_reader.py` | 225 | Read-path commands: /work, pulse, sprint, health, return, connect, connect-prep, people, docs, sources, prep, live, newsletter, deck, memo, talking-points, promo-case, promo-narrative, journey, day, decide, graph, preread, incidents, repos |
| `tests/work/test_work_notes.py` | 39 | Post-meeting capture, decision IDs (D-NNN), open item IDs (OI-NNN), /work remember micro-capture |
| `tests/work/test_work_bootstrap.py` | 60 | Bootstrap interview, 12 questions, dry-run, atomic writes, completion flag, cold-start path |
| `tests/work/test_work_loop.py` | 30 | WorkLoop CLI (main()), run_read_loop/run_refresh_loop, _update_learned_state, ADO org from config |
| `tests/work/test_post_work_refresh.py` | 15 | Post-refresh summarization, session history writer, run log appender |
| `tests/work/test_phase4_phase5.py` | 59 | Degraded mode reporting, prompt linter, phase 4/5 scaffolding (incidents/repos), /work graph, pre-read tracking |
| `tests/work/test_kusto_metrics_writer.py` | 19 | `write_kusto_metrics_state()` + metric extractors |
| `tests/work/test_program_metrics.py` | 10 | `_load_program_metrics()` parser |
| `tests/work/test_pulse_health.py` | 5 | `_program_health_oneliner()` |
| `tests/work/test_meeting_context.py` | 12 | `_xpf_meeting_context()` |
| `tests/work/test_kusto_refresh_set.py` | 9 | `run_refresh_set()` batch API |
| **Total** | **938** | |

**Note:** `tests/unit/test_bridge_schemas.py` (55 tests) covers the older `BridgeSchema` API that predates the v1.7.0 redesign. `tests/work/test_bridge_enforcement.py` covers the current `bridge_schemas.py` implementation.

### 19.10 Product Knowledge Domain

Product Knowledge Domain (FW-18) provides durable product/technology knowledge that persists across projects. Unlike project state (2-week staleness), product knowledge uses a 6-month staleness tier and is trigger-loaded rather than always-on.

#### File Structure

```
state/work/
├── work-products.md              # Index (~5–15 KB, trigger-loaded)
│   ├── Product taxonomy tree
│   ├── Per-product: 3-line summary + deep file pointer
│   └── Active project cross-references
│
└── products/                     # Deep files (loaded on demand)
    └── <slug>.md                 # One file per tracked product/service
```

#### Loading Model

Trigger-loaded, same tier as `work-people`:
- **Not** loaded on every briefing — preserves context window
- Loaded when a meeting title matches a product routing keyword → deep file injected into prep context
- Loaded when user runs `/work products` or `/work products <name>`
- Loaded when a narrative template explicitly references product data
- Loaded when the Reflection Loop (FW-19) tags accomplishments by product

#### Index Schema (`state/work/work-products.md`)

```yaml
---
schema_version: "1.0"
domain: work-products
last_updated: "<ISO timestamp>"
work_os: true
generated_by: work_domain_writers
encrypted: false
product_count: <N>
layer_summary: { data-plane: N, control-plane: N, offering: N, platform: N }
---
```

Markdown body contains the taxonomy tree and per-product summary rows. Routing keywords for each product are defined in `config/domain_registry.yaml` (`work-products` entry, `routing_keywords` list).

#### Deep File Schema (`state/work/products/<slug>.md`)

| Section | Content |
|---|---|
| Architecture Overview | 2–5 paragraphs describing the product’s role and scale |
| Components | Table: `name`, `role`, `owner` |
| Dependencies | Table: `type` (upstream/downstream), `interface` |
| Team & Stakeholders | Table: `name`, `role`, `relationship` |
| Data Sources & Observability | Table: `source`, `query_key`, `cadence` |
| Related Projects | Table linking to `work-projects.md` entries |
| Key Metrics | Table: `metric`, `source`, `target`, `current` |
| Knowledge Log | Append-only entries: `YYYY-MM-DD: note [from: source]` |

#### Products vs. Projects

| Dimension | Products | Projects |
|---|---|---|
| **Lifecycle** | Durable — persist indefinitely | Time-bound — start → close |
| **Content** | Architecture, components, dependencies, teams | Milestones, work items, deliverables |
| **Staleness tier** | 6 months | 2 weeks |
| **Update source** | Design reviews, architecture docs, onboarding | ADO, meetings, daily standup |

#### Integration Points

| Surface | Integration |
|---|---|
| **Meeting prep** (`/work prep`) | Keyword match → inject architecture summary, key contacts, latest metric |
| **Narrative engine** (FW-13) | Memo/newsletter/deck templates reference product context for richer output |
| **Reflection Loop** (FW-19) | Accomplishment Index gets `Product` column; items tagged by product |
| **Promo case** (FW-15) | Product scope evidence: "owns knowledge of N products across M layers" |
| **Bootstrap** (FW-12) | Product seed questions during `/work bootstrap` |

#### Meeting Context Injection

`_product_meeting_context(title: str) -> list[str]` generalizes single-product meeting context injection:

1. Read `work-products.md` index
2. For each product, check if any `routing_keyword` matches the meeting title
3. Load the matching product’s deep file
4. Extract: architecture summary, key contacts, latest metric, most recent Knowledge Log entry
5. Return up to 4 context lines (same format as existing injection)
6. **Fallback:** if product index does not exist or no keyword match, fall back to the prior single-product context function

Backward compatibility: existing single-product keyword list and context function continue to work; `_product_meeting_context()` is tried first.

#### Bootstrap Seed Questions

Added to the `/work bootstrap` interview (FW-12):
- "What products does your team own or contribute to?" — `type: multiline_list`, written to `work-products.md` index
- "What is the primary architecture layer you work on?" — `type: str`, optional

#### Privacy & Encryption

| Data | Encrypted | Notes |
|---|---|---|
| Product names, architecture | No | Not PII; architecture knowledge is referenceable |
| Team names in deep files | No | Use role/alias where possible |
| Product index | No | Lightweight taxonomy, no sensitive data |

`pii_guard.py` runs on all product file content before display. Product codenames can be added to `privacy.redact_keywords` in `config/artha_config.yaml` if needed. No external APIs required.

#### Planned Test Coverage

`tests/work/test_work_products.py`:
- `write_products_index()`: valid YAML frontmatter written; empty product list handled
- `write_product_deep(slug)`: correct `state/work/products/<slug>.md` structure; atomic write
- `cmd_products()`: index list rendered from valid state file
- `cmd_products("<name>")`: deep file loaded and rendered; graceful handling when slug not found
- `_product_meeting_context()`: keyword match returns context lines; falls back to prior function when product index absent
- Bootstrap seed questions → valid index entries with correct `product_count` frontmatter
- Schema validation: `schema_version`, `domain`, `last_updated` present and valid

---

### 19.11 Reflection Loop Technical Architecture (FW-19 v1.5.0)

The Reflection Loop is a multi-horizon planning & review engine that provides sweep→synthesize→score→reconcile→draft capabilities across daily, weekly, monthly, quarterly, and yearly horizons. It is a headless engine that powers existing commands (`/work memo`, `/work newsletter`, `/scorecard`, `/goals`) rather than creating a parallel surface.

#### Pipeline Architecture

8-step pipeline executed via `/work reflect`:

```
DETECT → SWEEP → EXTRACT → SCORE → RECONCILE → SYNTHESIZE → DRAFT → PERSIST
```

| Step | Module | Responsibility |
|---|---|---|
| 1. DETECT | `reflect.py` `_detect_horizons()` | Check last close times + day-gate (Thu/Fri for weekly+) |
| 2. SWEEP | `sweep.py` | 5-pass: WorkIQ, state diff, Kusto, calendar, goal/KPI |
| 3. EXTRACT | `reflect.py` | Structured item extraction (accomplishments, CFs, decisions, risks) |
| 4. SCORE | `scoring.py` `score_item()` | `(urgency × importance) + visibility_bonus + goal_alignment_bonus` |
| 5. RECONCILE | `reconcile.py` | Two-pass: deterministic ID match → injectable LLM semantic match |
| 6. SYNTHESIZE | `narrative/reflect.py` | Horizon-specific Markdown with YAML frontmatter |
| 7. DRAFT | `narrative/reflect.py` | Deliverable (memo/newsletter/deck) → `/stage` card |
| 8. PERSIST | `reflect.py` `_persist_reflection()` | Atomic write via `write_state_atomic()` |

**Failure semantics:** Minimum viable sweep requires ≥1 of passes 1–3 returning data. Partial-success: failed passes logged as `skipped` in frontmatter. No retry within same invocation — failures queued for next run.

#### Module Layout

```
scripts/work/
├── reflect.py            # Pipeline orchestrator (~400 LOC)
├── sweep.py              # 5-pass data collection (~200 LOC)
├── scoring.py            # Additive impact model (~150 LOC)
├── reconcile.py          # Two-pass plan-vs-actual (~200 LOC)
├── reflect_reader.py     # Typed read facade (~120 LOC)
├── compaction_manifest.py # Idempotent multi-file compaction
├── reflection_key.py     # Stable artifact identifier
└── helpers.py            # write_state_atomic() utility

scripts/narrative/
└── reflect.py            # 4 horizon templates

scripts/backfill/
├── scrape_parser.py      # 4-format family parser (~300 LOC)
├── cross_reference.py    # Enrichment from existing state (~100 LOC)
└── backfill_runner.py    # Orchestrator (~150 LOC)
```

#### Infrastructure (Sprint 0 Prerequisites)

| Component | Location | Contract |
|---|---|---|
| `write_state_atomic()` | `scripts/work/helpers.py` | Write → tmp file → validate YAML frontmatter → `os.replace()` (atomic on all platforms including Windows) |
| Concurrency guard | `state/work/.reflect-lock` | UUID4 + UTC timestamp JSON lock; stale after 30 min; no PID-based detection (Windows `os.kill()` sends SIGTERM, not signal-0) |
| `CompactionManifest` | `scripts/work/compaction_manifest.py` | JSON manifest for multi-file compaction; crash recovery via `check_stale_compaction()` on startup; `--repair-compaction` flag for manual resolution |
| `ReflectionKey` | `scripts/work/reflection_key.py` | Frozen dataclass `<horizon>/<iso-period>` (e.g., `weekly/2026-W14`); `already_exists()` prevents duplicate writes on retry |

#### Three-Tier Persistence

| Tier | File | Size | Retention |
|---|---|---|---|
| **1 (Live)** | `state/work/reflect-current.md` | ~5–10 KB | Overwritten each cycle; 15 KB size guard (blocks pipeline, no auto-compact) |
| **2 (Archive)** | `state/work/reflections/{weekly,monthly,quarterly}/*.md` | ~3–8 KB each | 90 days full; compacted to Tier 3 with 30-day grace period |
| **3 (History)** | `state/work/reflect-history.md` | ~20–40 KB/year | Indefinite; Accomplishment Index rows **never compacted** |
| **4 (Ledger)** | `state/work/work-accomplishments.md` | ~30–60 KB/year | Indefinite; canonical chronological record; entries tagged by impact/program/status; cross-referenced by weekly reflection `accomplishment_refs` |

**Compaction triggers:** Weekly reflection compacts dailies; monthly compacts weeklies >90 days; quarterly compacts monthlies >90 days. Grace period: `compacted_at` + 30 days before deletion. **Accomplishment Ledger** is never compacted — it is the permanent record consumed by `/work connect-prep` and `/work promo-case`.

#### Scoring Model

Additive visibility model (v1.3+ — eliminates Deep Work escape hatch):

```
score = (urgency × importance) + visibility_bonus + goal_alignment_bonus
```

| Dimension | Values | Role |
|---|---|---|
| Urgency | critical(1.0), high(0.8), medium(0.5), low(0.2) | Multiplicative base |
| Importance | strategic(1.0), operational(0.7), administrative(0.3) | Multiplicative |
| Visibility | ORG(+0.6), SKIP(+0.4), TEAM(+0.2), SELF(+0.0) | Additive bonus |
| Goal Alignment | direct(+0.5), tangential(+0.2), unaligned(0) | Additive bonus |

Score-to-label: ≥1.0 = HIGH, 0.3–0.99 = MEDIUM, <0.3 = LOW. Configurable in `config/artha_config.yaml` under `enhancements.work_os.reflect.scoring`.

#### Reconciliation (`reconcile.py`)

Two-pass strategy with injectable LLM boundary for testability:

| Pass | Method | Match Condition | Testable |
|---|---|---|---|
| 1 | `match_by_id()` | Exact CF_ID or task_id | Pure function — no LLM |
| 2 | `match_by_llm()` | Semantic title similarity | Mock-injectable `LLMMatcher` Protocol |

Unit test contract: ≥90% line coverage; Pass 2 tests MUST inject `MockLLMMatcher`.

#### Carry-Forward Policy

| Age | Action |
|---|---|
| 0–2 weeks | Normal — shown in reflection with priority |
| >2 weeks | Flagged `[STALE]` in all reflections |
| 3 carries | Auto-moved to Parking Lot → requires `RE-PRIORITIZE` or `DROP` at monthly retro |
| Active cap | Max 15 active CFs; new items force triage of oldest |

CF IDs: `CF-YYYYMMDD-NNN` (4-digit year, counter resets daily, derived by scanning existing CFs).

#### Backfill Pipeline

3-phase backfill from 82-week work-scrape corpus (Aug 2024 W4 → Mar 2026 W3):

| Phase | Method | Recall Target | API Calls |
|---|---|---|---|
| 1a | Parse scrape corpus (4 format families: A, B-early, B-mid, B-late) | ~85% | 0 |
| 1b | Cross-reference with project-journeys, career, performance state | ~92% | 0 |
| 2 | WorkIQ gap-fill (10 calls/session, ~3 sessions) | ~97% | ~30 |
| 3 | Interactive user validation (8 quarterly reviews) | ~99% | 0 |

Backfill CFs tagged `historical` — never surface as active in live system.

#### Observability

Pipeline step telemetry in `reflect-current.md` YAML frontmatter (`last_run` block with per-step status/duration/counts). Audit log at `state/work/work-audit.jsonl` (JSONL, append-only, 90-day rotation, 500 KB growth guard, sequence numbers, truncation guard).

#### Integration Points

| Surface | Integration |
|---|---|
| `/work` (briefing) | Friday footer: "Weekly review due — N unreconciled CF items" |
| `/work memo` | `ReflectReader.get_current_reflection()` for source data (fallback: direct WorkIQ) |
| `/work newsletter` | Weekly reflection accomplishments section |
| `/work connect-prep` | Quarterly reflections + Accomplishment Index + `work-accomplishments.md` ledger (filtered by program, impact, date range) |
| `/work promo-case` | `reflect-history.md` Accomplishment Index + `work-accomplishments.md` ledger (HIGH impact items across all programs) |
| `/scorecard`, `/goals` | `ReflectReader.get_weekly_history()` + `get_goal_trend()` |

**Fallback guarantee:** All commands fall back to current behavior (WorkIQ + state files directly) if no reflection data exists.

#### Test Coverage

| Module | Test File | Tests | Coverage Target |
|---|---|---|---|
| `reflect.py` | `tests/work/test_reflect.py` | Horizon detection, pipeline, state persistence, CF mechanics | >90% |
| `sweep.py` | `tests/work/test_sweep.py` | Each sweep pass, combined sweep, WorkIQ fallback | >85% |
| `scoring.py` | `tests/work/test_scoring.py` | Determinism, rubric, org-sensitivity, edge cases | >95% |
| `reconcile.py` | `tests/work/test_reconcile.py` | ID match, LLM mock match, partial overlap | >90% |
| `reflect_reader.py` | `tests/work/test_reflect_reader.py` | Fixture-based, no live I/O | >90% |
| `compaction_manifest.py` | `tests/work/test_compaction_manifest.py` | Create, record, complete, stale detection | >90% |
| `backfill/` | `tests/work/test_backfill_runner.py` + `test_scrape_parser.py` + `test_cross_reference.py` | Format families, cross-ref, orchestrator | >85% |

#### Configuration

```yaml
enhancements:
  work_os:
    reflect:
      enabled: false  # Feature flag — enable after bootstrap
      scoring:
        urgency_weights: { critical: 1.0, high: 0.8, medium: 0.5, low: 0.2 }
        importance_weights: { strategic: 1.0, operational: 0.7, administrative: 0.3 }
        visibility_bonus: { org: 0.6, skip: 0.4, team: 0.2, self: 0.0 }
        goal_alignment_bonus: { direct: 0.5, tangential: 0.2, unaligned: 0.0 }
        label_thresholds: { high: 1.0, medium: 0.3 }
      horizons:
        daily_close_after_hours: 6
        weekly_due_day: "friday"
        monthly_due_week: "last"
        compaction_age_days: 90
      backfill:
        scrape_path: "<local-path>/knowledge/work-scrape"
        workiq_gap_fill: true
        max_workiq_calls_per_session: 10
```

#### Privacy & Security

| Data | Classification | Storage |
|---|---|---|
| Reflection state files | Internal Use | `state/work/` local, encrypted via `.age` if configured |
| Scrape corpus | Personal & Confidential | External drive only — never copied to cloud-synced paths |
| Drafted deliverables | Internal Use | Staged via `/stage`; PII guard runs before display |
| Audit log | Internal Use | `state/work/work-audit.jsonl`; 90-day rotation |

Feature flag: `enhancements.work_os.reflect.enabled: false` by default. No new external APIs.

---

## 21. Observability & Evaluation Framework *(v1.4.0)*

Closes the feedback loop on briefing quality by measuring, scoring, and learning from every catch-up. Without this layer, Artha is a black box — smart prompts with no signal on whether they actually work.

### 21.1 Structural Gaps Addressed

Ten observable quality gaps (G1–G10) are tracked and closed:

| ID | Gap | Metric |
|----|-----|--------|
| G1 | Missed actionable emails | False-negative rate per domain |
| G2 | Over-alerting / noise | False-positive alert rate |
| G3 | State staleness | Time since last update per domain |
| G4 | Goal drift undetected | Leading indicator miss rate |
| G5 | Correction not learned | Re-occurrence rate after correction |
| G6 | Action proposals not acted | Approval / expiry ratio |
| G7 | Cross-domain signal missed | Compound signal detection rate |
| G8 | Briefing too long / overwhelming | Completion proxy (calibration answer rate) |
| G9 | Wrong compression level chosen | Flash/Standard/Deep mismatch flag |
| G10 | Skill degradation unpredicted | Skill health counter trend |

### 21.2 Architecture

```
catch-up run
    │
    ▼
scripts/eval_runner.py        ← orchestrates eval harness per run
    │   └─ structured briefing output (BriefingOutput schema)
    │   └─ calibration answers (accepted / corrected)
    │
    ▼
scripts/eval_scorer.py        ← computes per-domain accuracy scores
    │   accuracy = accepted / (accepted + corrected) per domain
    │   rolling 7-day + 30-day averages stored in MetricStore
    │
    ▼
scripts/lib/metric_store.py   ← SQLite-backed time-series store
    │   Tables: run_metrics, domain_scores, skill_health
    │   Location: ~/.artha-local/eval.db (platform-local, never committed)
    │
    ▼
scripts/log_digest.py         ← digest last-N runs into summary
    │   Outputs: accuracy table, gap trend, divergence warnings
    │
    ▼
scripts/correction_feeder.py  ← turns user corrections → memory.md entries
        Correction schema: {domain, field, old_value, new_value, source_step}
        Written to state/memory.md Corrections section
        Triggers re-evaluation of relevant G5 (correction retention) metric
```

### 21.3 Phases

| Phase | Scope | Status |
|-------|-------|--------|
| P1 | Structured output capture + schema validation | Complete |
| P2 | Calibration-answer accuracy scoring per domain | Complete |
| P3 | Correction→memory pipeline (G5 closure) | Complete |
| P4 | Skill health integration (G10) | Complete |
| P5 | Cross-run gap trend dashboard via `/eval` | In progress |

### 21.4 Key Invariants

- Eval DB (`~/.artha-local/eval.db`) is platform-local and never committed to git.
- `eval_runner.py` is non-blocking: catch-up never fails due to eval errors (all eval ops are wrapped in try/except).
- Accuracy scores feed into `state/health-check.md → Accuracy Pulse Data` section each run.
- `correction_feeder.py` writes only to `state/memory.md` — never modifies domain state files directly.
- `metric_store.py` prunes records older than 90 days automatically.

### 21.5 Test Coverage

127 tests across `tests/eval/` and `tests/unit/test_eval_*.py` files. Key test files:

| File | Tests | Coverage |
|------|-------|----------|
| `test_eval_runner_accuracy.py` | 18 | Accuracy math, rolling averages |
| `test_eval_scorer.py` | 15 | Scorer output schema, edge cases |
| `test_eval_workflow.py` | 22 | End-to-end harness flow |
| `test_correction_feeder.py` | 12 | Correction schema, memory write |
| `test_metric_store.py` (via `test_knowledge_graph.py`) | 14 | MetricStore CRUD, pruning |
| `test_log_digest.py` | 11 | Digest output, gap detection |
| `test_self_model_feedback.py` | 15 | Self-model update triggers |
| `test_outcome_signals.py` | 20 | Signal → metric mapping |

### 21.6 Evaluation Dimensions & Scoring Rubric

Quality score composite: $Q = w_A \times A + w_F \times F + w_C \times C$

**Accuracy (A)** — source reliability × conflict state × corroboration:
- Source weights: `ado_sync`=0.90, `kusto_query`=0.85, `kb_file`=0.80, `manual`=0.75, `workiq`=0.70, `llm_extract`=0.50
- Active provenance conflict → accuracy × 0.5 (halved)
- Corroboration bonus: accuracy × (1 + 0.05 × `corroborating_sources`)
- `providers_used` frontmatter stamps live-data provenance (→ A=0.90) vs. cached extractions (→ A=0.70)

**Freshness (F)**: $F = \max(0.0, 1.0 - \text{age\_days} / \text{staleness\_ttl\_days})$
- Domain TTLs: calendar/comms/incidents=1d, projects=7d, people=14d, decisions=14d, golden_queries=14d

**Completeness (C)** — binary proxy:
- Markdown files: C=1.0 if file >1000 bytes, else C=0.3
- KB entities: required fields per entity type (e.g., system needs name + summary + domain + ≥1 relationship)
- Placeholder detection: "TBD", "See notes", "TODO", "—", `null`, `""` all treated as empty

**Sub-dimensions** within response quality (used by `eval_scorer.py`): consistency, relevance, specificity, factuality — rolled into Accuracy scoring.

**Golden Dataset**: Synthetic briefing fixtures with hand-written quality references (≥4 high-quality baselines) + ≥5 anti-golden scenarios (known-bad briefings scoring <40). Located in `tests/eval/golden_set/fixtures.yaml`.

### 21.7 Execution SLAs

| Component | SLA |
|-----------|-----|
| `eval_scorer.py` | <100ms per briefing |
| `log_digest.py` | <500ms on 3-day logs |
| `eval_runner --accuracy` | <5s end-to-end |

### 21.8 Regression Detection

| Signal | Threshold | Action |
|--------|-----------|--------|
| Quality score drop | >20% over 7 days | Anomaly flag in briefing |
| Connector error rate | >10% | Budget violation alert |
| Consecutive null `quality_scores` | ≥5 | P1 self-diagnostic alert |

**Config hash correlation**: SHA-256 of `(Artha.md + Artha.core.md + finalize.md)` truncated to 12 hex chars, stored in `catch_up_runs.yaml` for prompt-version → quality regression tracking.

---

## 22. Knowledge Graph — Second Brain Architecture *(v2.0)*

The Work OS knowledge layer is a **second brain** — a machine-local SQLite property graph providing total recall of work entities: people, projects, systems, decisions, programs, and their relationships. `knowledge/*.md` files are the cross-platform source of truth; the SQLite graph (`knowledge/kb.sqlite`) is the derived query index providing FTS5 search, graph traversal, temporal queries, and MCP tool access. The graph is an enhancement layer — Artha degrades gracefully to markdown-only mode if unavailable.

### 22.1 Eight Governing Principles

**P1 — Markdown-First Invariant.** `knowledge/*.md` files are the permanent, human-readable source of truth. The SQLite graph is a derived index — always rebuildable from `.md` files via `kb_bootstrap.py --force`. Nothing important lives only in the graph.

**P2 — SQLite is machine-local.** `knowledge/kb.sqlite` MUST NOT be in any cloud-sync folder (OneDrive, iCloud, Dropbox). It is gitignored and never leaves the local machine. Cross-machine state lives in `knowledge/*.md` only.

**P3 — Graceful degradation.** `get_kb()` returns a `NullKnowledgeGraph` on failure. Pipeline, briefings, and all commands work without SQLite. The KB is an enhancement, never a hard dependency.

**P4 — Provenance on every entity.** Every upserted entity carries `source_type`, `source_ref`, and `source_episode_id`. The source is traceable to a specific document, connector, or inbox file.

**P5 — Deterministic queries.** Graph queries use SQL + structured traversal, not LLM inference — ensuring reproducibility and testability.

**P6 — PII guard before write.** `pii_guard.scan_content()` MUST run before `add_episode()`. No PII-bearing content enters the graph. There is no safe post-write PII deletion path from an append-only episode log.

**P7 — Bootstrap from existing state.** All populated entities are derived from `knowledge/*.md` and connected sources. No manual curation required.

**P8 — Token budget discipline.** `context_for()` enforces a hard 950-token budget. Drop order under pressure: god node summary → community summaries → historical relationships → recent episodes → entity attributes.

### 22.2 Five Ingestion Paths

```
Path 1: knowledge/*.md  ──→  kb_bootstrap.py     (heuristic extraction, SHA256 cache)
Path 2: inbox/work/     ──→  inbox_process.py    (drop-folder pipeline, PII-gated)
Path 3: SharePoint      ──→  sharepoint_kb_sync.py (Graph delta API, incremental)
Path 4: state/work/*.md ──→  KnowledgeEnricher   (via work_loop.py, NOT pipeline.py)
Path 5: ADO connector   ──→  work_loop.py        (structured data upsert)
```

All five paths terminate at `KnowledgeGraph.upsert_entity()` / `add_episode()`. `pipeline.py` MUST NOT have a direct SQLite dependency — it is a pure JSONL streaming orchestrator. KB ingestion via paths 2 and 3 runs as standalone scripts, orchestrated by `artha.py` before `pipeline.py`.

### 22.3 Entity Lifecycle Stages

Every entity carries a `lifecycle_stage` field tracking its canonical state:

| Stage | Meaning |
|-------|---------|
| `proposed` | Suggested; not yet confirmed |
| `approved` | Confirmed; in planning |
| `in_flight` | Under active execution |
| `shipped` | Completed and deployed |
| `cancelled` | Shut down |
| `superseded` | Replaced by another entity |
| `on_hold` | Paused |
| `unknown` | Default when stage cannot be determined |

### 22.4 SQLite Schema (v4)

```sql
CREATE TABLE entities (
    id                    TEXT PRIMARY KEY,
    type                  TEXT NOT NULL,
    name                  TEXT NOT NULL,
    domain                TEXT,
    current_state         TEXT DEFAULT 'active',
    lifecycle_stage       TEXT DEFAULT 'unknown',
    confidence            REAL DEFAULT 0.5,
    staleness_ttl         INTEGER DEFAULT 90,
    source_type           TEXT,
    source_ref            TEXT,
    source_episode_id     TEXT REFERENCES episodes(id),
    excerpt_hash          TEXT,      -- SHA256 of source text; change → staleness reset
    change_source_ref     TEXT,      -- URI of the document that caused last change
    corroborating_sources TEXT,      -- JSON: list of source_refs
    validation_method     TEXT,
    attrs                 TEXT,      -- JSON blob for type-specific fields
    created_at            TEXT,
    updated_at            TEXT,
    last_validated_at     TEXT
);

CREATE TABLE relationships (
    id           TEXT PRIMARY KEY,
    from_entity  TEXT REFERENCES entities(id),
    to_entity    TEXT REFERENCES entities(id),
    rel_type     TEXT NOT NULL,
    confidence   REAL DEFAULT 1.0,
    valid_from   TEXT,
    valid_to     TEXT,   -- NULL = active; set by _negative_sync()
    source       TEXT,
    created_at   TEXT
);

CREATE TABLE episodes (
    id           TEXT PRIMARY KEY,
    episode_key  TEXT NOT NULL,
    source_type  TEXT NOT NULL,  -- 'knowledge_md' | 'inbox' | 'sharepoint' | 'ado_sync' | 'state_md'
    raw_content  TEXT,           -- PII-scrubbed by pii_guard before write
    processed_at TEXT,
    entity_count INTEGER DEFAULT 0
);

CREATE TABLE community_summaries (
    community_id TEXT PRIMARY KEY,
    entity_ids   TEXT NOT NULL,  -- JSON list of entity IDs
    theme        TEXT,
    summary      TEXT,
    generated_at TEXT
);

CREATE VIRTUAL TABLE kb_search USING fts5(name, attrs, content=entities, content_rowid=rowid);
CREATE TABLE kb_meta (key TEXT PRIMARY KEY, value TEXT);
```

**Additional tables**: `entity_history` (temporal versioning), `entity_aliases` (name resolution), `entity_context_cache` (TTL-invalidated context), `documents` + `document_entities` (artifact registry), `kusto_queries`, `source_weights`, `research_archive`.

### 22.5 Source Taxonomy & Confidence Contract

| Source | `source_type` | Default confidence | Notes |
|--------|---------------|--------------------|-------|
| ADO work items | `ado_sync` | 0.90 | Structured API data |
| `knowledge/*.md` | `knowledge_md` | 0.85 | Curated by user |
| SharePoint docs | `sharepoint` | 0.75 | High-quality structured docs |
| Inbox markdown | `inbox` | 0.75 | Explicit user drop |
| `state/work/*.md` | `state_md` | 0.55 | LLM-synthesized |
| Inbox email (`.eml`) | `inbox_email` | 0.60 | Headers reliable; body noisy |
| Inbox plaintext | `inbox_txt` | 0.50 | No structural signals |

Entities with `confidence < 0.5` are excluded from `context_for()` output by default.

### 22.6 Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/lib/knowledge_graph.py` | `KnowledgeGraph` class — full property graph engine. `NullKnowledgeGraph` fallback. |
| `scripts/kb_bootstrap.py` | Reads `knowledge/*.md`; heuristic extraction; SHA256 incremental cache; `--force` bypasses cache. |
| `scripts/lib/document_extractor.py` | Shared extraction engine (markdown, plaintext, `.docx`). Used by bootstrap, inbox, and SharePoint sync. |
| `scripts/inbox_process.py` | Drop-folder ingestion: reads `inbox/work/` and `inbox/personal/`, PII-guards content, archives to `inbox/_processed/`. |
| `scripts/sharepoint_kb_sync.py` | SharePoint delta sync via Graph API. Requires `Sites.Read.All` scope. Delta links for incremental queries. |
| `scripts/connectors/msgraph_sharepoint.py` | SharePoint connector handler (same contract as `msgraph_email.py`). |
| `scripts/kg_visualize.py` | Generates standalone vis.js HTML at `visuals/knowledge_graph.html`. Domain colors, degree-based sizing, community grouping. |

### 22.7 Full Python API (`KnowledgeGraph` class)

| Method | Signature | Purpose |
|--------|-----------|---------|
| `get_entity` | `(id)` | Direct entity lookup |
| `resolve_entity` | `(name)` | Best-match name resolution |
| `resolve_entity_candidates` | `(name, limit=5)` | Top-N fuzzy name matches |
| `search` | `(query, domain=None, limit=10)` | FTS5 full-text search |
| `traverse` | `(entity_id, rel_types=None, direction='both', depth=1)` | Neighborhood walk |
| `find_path` | `(from_id, to_id, max_depth=4)` | Shortest path between entities |
| `context_for` | `(entity_id, token_budget=950, depth=2, session_focus=None)` | **Primary entry point** — pre-assembled context with staleness filtering; 950-token hard budget |
| `get_context_as_of` | `(entity_id, timestamp, token_budget=950)` | Point-in-time context reconstruction |
| `global_context_for` | `(question, max_tokens=800)` | Cross-entity question answering |
| `recent_episodes` | `(entity_mentions, since_days=30, limit=10)` | Recent ingestion events |
| `stale_entities` | `(domain=None)` | Entities past staleness TTL |
| `recent_changes` | `(domain=None, days=30)` | Recently modified entities |
| `upsert_entity` | `(entity, source, confidence=0.5, source_episode_id=None)` | Create or update entity |
| `add_relationship` | `(from_id, to_id, rel_type, **kwargs)` | Create directed edge |
| `deactivate_relationship` | `(rel_id, reason)` | Soft-delete with reason |
| `add_episode` | `(episode_key, source_type, raw_content=None)` | Record ingestion event |
| `add_alias` | `(alias, entity_id)` | Register alternative name |
| `invalidate_cache` | `(entity_id)` | Clear context cache for entity |
| `validate_integrity` | `()` | Referential integrity check |
| `get_stats` | `()` | Entity/relationship/cache counts |
| `rebuild_communities` | `()` | Leiden clustering (`graspologic>=3.4`) → `community_summaries`; falls back to Union-Find if not installed |
| `god_nodes` | `(limit=10)` | Highest-degree entities — structural hubs with degree and domain-spread |
| `vacuum` | `()` | SQLite VACUUM + FTS rebuild |
| `backup` | `(tier='daily')` | Tiered backup (daily/weekly/monthly) |

### 22.8 MCP Tools Surface

Seven MCP tools expose the knowledge graph to AI assistants (Copilot, Claude, Gemini):

| Tool | Purpose |
|------|---------|
| `artha_kb_search` | FTS5 full-text search across all entities |
| `artha_kb_context` | Per-entity context assembly (950-token budget, 2-hop BFS) |
| `artha_kb_query` | Structured property query (filter by domain, type, confidence) |
| `artha_kb_global` | Cross-entity question answering |
| `artha_kb_recent_changes` | Entities changed in last N days |
| `artha_kb_stale` | Entities past their staleness TTL |
| `artha_kb_episodes` | Recent ingestion events (audit what was just synced) |

All MCP tools call `get_kb()` and return graceful empty responses on unavailability.

### 22.9 Markdown Stub Contract

Connectors that generate new knowledge entries write stubs in this format:

```markdown
<!-- artha-kb-stub | source: sharepoint | id: <uuid> | synced: <ISO ts> -->
## <Entity Name>
- **Type**: <entity_type>
- **Domain**: <domain>
- **Status**: <lifecycle_stage>
- **Source**: <web_url or file path>
```

| Connector | Stub path |
|-----------|-----------|
| SharePoint | `knowledge/sharepoint_notes/` |
| Inbox | `knowledge/inbox_notes/` |
| ADO sync | `knowledge/ado_notes/` |
| Meeting notes | `knowledge/meeting_notes/` |

Bootstrap reads all subdirectories of `knowledge/` — no separate registration needed for new stub locations.

### 22.10 Ghost Entity Detection & Staleness

**Ghost entities**: entities previously authoritative but now absent from all sources. When a source file is reprocessed and an entity is missing from the new version, `_negative_sync()` sets `valid_to` on all its relationships. This prevents stale `in_flight` states for completed projects.

**Excerpt-level staleness**: each entity carries `excerpt_hash` (SHA256 of the source text chunk). If the source file changes but the excerpt hash is unchanged, the entity is not re-stamped as stale. Changed excerpt hash → `last_validated_at` updated, staleness TTL reset.

**Agent staleness check**: `KnowledgeEnricher.enrich_briefing()` automatically runs `stale_entities()` and surfaces a "⚠ N entities overdue for review" warning if any active entities exceed their TTL.

### 22.11 Performance Targets *(v2.0)*

| Metric | Target |
|--------|--------|
| `context_for()` cold start | <50ms |
| FTS5 search query | <100ms |
| 2-hop neighborhood walk | <200ms |
| Default context budget | **950 tokens** (hard limit; was 4,000 in v1.0) |
| God node summary | ≤150 tokens (first dropped under pressure) |
| Entity staleness TTL | Configurable per domain (default 90 days) |
| Community clustering | Leiden (`graspologic>=3.4`) optional; Union-Find fallback always available |
| Leiden trigger threshold | Skip if < 20 entities |
| `community_members` junction table | Replaces LIKE scan after KB exceeds ~5,000 entities |

**Relationship strength decay** (temporal validity):
- <30d since validation → strong
- 30–90d → moderate
- 90–180d → weak
- >180d → historical

Partial unique index enforces only 1 active edge per `(from_entity, to_entity, rel_type)` triple.

---

---
## 23. External Agent Composition (AR-9) *(v1.4.0)*

AR-9 extends AR-7's internal delegation protocol to support **externally-authored domain agents** — agents whose system prompts, tools, and behavior are owned by other teams or repositories. External agents are treated as **data sources**: discover → authenticate → fetch → validate → integrate → cache → observe.

### 23.1 Component Architecture

| Component | Module | Responsibility |
|-----------|--------|---------------|
| Agent Registry | `scripts/lib/agent_registry.py` | YAML-backed registry; drop-folder scan (`config/agents/external/`); content_hash dedup; shadow_mode flag; registered_at timestamp |
| Agent Router | `scripts/lib/agent_router.py` | Geometric-mean confidence routing; keyword + context-type scoring |
| Context Classifier | `scripts/lib/context_classifier.py` | Tags domain context as public / scoped / private |
| Context Scrubber | `scripts/lib/context_scrubber.py` | PII scrubbing per domain-scoped profile; zero raw PII to external agents |
| Injection Detector | `scripts/lib/injection_detector.py` | Recursive prompt injection decoder; confidence threshold filtering |
| Agent Invoker | `scripts/lib/agent_invoker.py` | `runSubagent`-based invocation; 60s timeout; stale-while-revalidate cache in `tmp/ext-agent-cache/` |
| Response Verifier | `scripts/lib/response_verifier.py` | Entity-based KB cross-check against local knowledge graph |
| Response Integrator | `scripts/lib/response_integrator.py` | Expert consensus format; fallback cascade (agent → KB → investigation → Cowork) |
| Knowledge Extractor | `scripts/lib/knowledge_extractor.py` | Structured extraction from agent responses → `tmp/ext-agent-cache/` |
| Agent Scorer | `scripts/lib/agent_scorer.py` | Quality scoring: `accuracy × freshness × honesty_bonus` |
| Health Tracker | `scripts/lib/agent_health.py` | Availability, latency, quality; auto-retirement at 5 consecutive failures |
| Audit Logger | `scripts/lib/ext_agent_audit.py` | Full JSONL audit trail per invocation |
| Metrics Writer | `scripts/lib/metrics_writer.py` | JSONL metrics emission for eval pipeline |
| Manager CLI | `scripts/agent_manager.py` | Commands: `list`, `register`, `unregister`, `health`, `archive`, `delete` |

### 23.2 Trust Tier Model

| Tier | Description | Context Access |
|------|-------------|---------------|
| `owned` | Artha-authored agents (`artha-work*.md`) | Full context |
| `trusted` | Verified internal team agents | Scoped context |
| `verified` | Audited external agents | Scoped context + extra verification |
| `external` | Unaudited external agents | Public context only |
| `untrusted` | Rejected / quarantined | Blocked |

### 23.3 File-Drop Update Model

External agents are registered by dropping `.agent.md` files into `config/agents/external/`. Artha scans on invocation, checks `content_hash`, and updates the registry on changes. No daemon, no watcher — consistent with Artha's pull-based architecture.

### 23.4 Security Invariants

1. External agents receive **zero unfiltered PII** — the scrubber runs before every invocation.
2. Injection detection is mandatory — all agent responses pass through the decoder.
3. All delegations are logged to the JSONL audit trail.
4. External agents cannot modify Artha state (read-only in V1).
5. Credential isolation — external agents inherit no credentials from the Artha session.

### 23.5 Safety Hardening *(v1.4.0)*

Deep audit of the 14-module AR-9 pipeline identified and resolved five safety issues:

| ID | Fix | Module |
|----|-----|--------|
| C-1 | Template injection defense — user queries with `{`/`}` are brace-escaped before `str.format()`; 8,000 char hard cap | `prompt_composer.py` |
| C-2 | Atomic cache writes — `tempfile.mkstemp()` + `os.replace()` prevents partial-write corruption | `knowledge_extractor.py` |
| C-3 | PII guard fail-safety — strict mode blocks delegation when PII guard is unavailable or raises | `context_scrubber.py` |
| M-1 | Dead import cleanup + silent-failure logging (malformed YAML files now warn) | `agent_registry.py` |
| M-4 | Quality score clamped to `[0.0, 1.0]` before health tracking; bare `except` narrowed to `OSError` | `agent_health.py` |

All five invariants enforced by 16 tests in `tests/ext_agents/test_safety_invariants.py`.

### 23.6 GA Promotion Status

AR-9 was promoted from burn-in to **General Availability** (EA-16a executed: `burn_in: true → false`).

| Metric | Value |
|--------|-------|
| Total tests | 4,482 (210 AR-9 suite + 4,272 codebase) |
| Failures | 0 |
| Live E2E quality score | 0.95 (`storage-deployment-expert` agent invocation) |
| Production config | `external_agents.enabled=true`, `burn_in=false`, `min_confidence=0.3` |
| Architecture compliance | All components pass Rule 4 (no direct config reads — use `lib.config_loader`); all import boundaries correct |

**Known V1 Limitations:**
- Sequential invocation only (`max_concurrent=1`)
- Live EA-15c burn-in requires interactive VS Code sessions (cannot fully automate)
- `cmd_health` contract extended (additional health reporting surface)

---

## 24. Data Quality Gate *(v4.0)*

Pull-based quality assessment for the Work OS KB layer, assessed at read time (not maintained by background jobs). Consistent with Artha's no-daemon principle.

**Priority:** Accuracy > Freshness > Completeness (non-negotiable).

### 24.1 Three-Dimension Model

| Dimension | Default Weight | Key Factor |
|-----------|---------------|-----------|
| Accuracy (A) | 0.50 | Source reliability × conflict state × corroborating_sources |
| Freshness (F) | 0.35 | Age relative to domain TTL |
| Completeness (C) | 0.15 | Binary proxy — required fields present |

### 24.2 Quality Verdicts

| Verdict | Q Score | Behavior |
|---------|---------|---------|
| PASS | ≥ 0.75 | Serve directly |
| WARN | 0.50–0.74 | Serve with caveat (no delay added) |
| STALE | 0.25–0.49 | Serve stale + background heal signal |
| REFUSE | < 0.25 | Decline with guidance |

### 24.3 Implementation

- `scripts/lib/dq_gate.py` — `_pre_answer_quality_gate()` pure function; `QualityVerdict(IntEnum)` with explicit values.
- `corroborating_sources` is a denormalized integer field on the `entities` table (updated by `kb_bootstrap.py` on each ingest run) — avoids N+1 queries at read time.
- Domain-aware weights configurable in `dq_gate.py` — Work OS uses higher Freshness weight than personal domains.

### 24.4 Cross-Reference Rules

Twelve entity-level completeness rules enforce referential integrity at quality-gate time:

| # | Rule |
|---|------|
| 1 | System must have ≥1 relationship (`hosts`, `depends_on`, etc.) |
| 2 | Component must have ≥1 `part_of` edge to parent system |
| 3 | Platform must have ≥1 entity hosted on it |
| 4 | Program must have `current_state` field populated (not "TBD") |
| 5 | Process must have ≥1 `used_by` or `applies_to` edge |
| 6 | Tool must reference ≥1 user team |
| 7 | Team must have ≥1 `owns`/`manages` edge OR ≥1 `works_on` person |
| 8 | Person must have ≥1 `reports_to` OR `works_on` edge |
| 9 | Artifact must have ≥1 `belongs_to` edge |
| 10 | Gap must have ≥1 `affects` edge |
| 11 | Event must have ≥1 `involves` edge |
| 12 | Decision must have non-empty `rationale` field |

### 24.5 Domain-Specific Weight Overrides

| Domain | w_A (Accuracy) | w_F (Freshness) | w_C (Completeness) |
|--------|:-:|:-:|:-:|
| calendar | 0.10 | 0.80 | 0.10 |
| comms | 0.10 | 0.80 | 0.10 |
| incidents | 0.20 | 0.70 | 0.10 |
| decisions | 0.70 | 0.10 | 0.20 |
| accomplishments | 0.60 | 0.10 | 0.30 |
| golden_queries | 0.60 | 0.20 | 0.20 |
| people | 0.50 | 0.30 | 0.20 |
| products | 0.60 | 0.10 | 0.30 |
| *default* | 0.50 | 0.30 | 0.20 |

**Additional implementation details:**
- `changed_by` predicate on conflict view distinguishes true provenance conflicts from sequential same-pipeline updates
- Per-section scoring for multi-domain commands (not min-based aggregation)
- Stale-while-revalidate pattern: serve immediately with caveat, heal on next `/work refresh` (not block for background heal)

---

## 25. Agent Framework v1 (AFW) *(v3.21)*

Artha's Agent Framework formalizes the runtime safety, memory, and observability infrastructure that makes production-grade AI workflows reliable across arbitrary conversation lengths and environmental conditions. Nine components shipped across implementation Waves 0–3 (see `specs/agent-fw.md` for the full design; this section is the canonical implementation reference).

### 25.1 Tripwire Guardrail System (AFW-1)

Seven runtime-enforced guardrails execute in **blocking** mode (before every tool call) or **parallel** mode (alongside every state write):

| Guardrail | Mode | Trigger |
|-----------|------|---------|
| `VaultAccessGuardrail` | blocking | Read attempt on encrypted file without prior decrypt |
| `RateLimitGuardrail` | blocking | Write operations exceed per-minute threshold |
| `ConnectorHealthGuardrail` | blocking | MCP connector reports degraded or down |
| `PIILeakGuardrail` | parallel | Pattern match on outbound data (SSN, A-number, CC, passport) |
| `NetNegativeWriteGuardrail` | parallel | Proposed write removes >20% of existing state fields |
| `PromptInjectionGuardrail` | blocking | Recursive decode + keyword scan on incoming text |
| `InjectionDetectGuardrail` | parallel | Deep-scan on all MCP tool outputs |

**Implementation:** `scripts/middleware/guardrails.py`, `scripts/middleware/guardrail_registry.py`.
**Config:** `config/guardrails.yaml` (guardrail class names, modes, thresholds — no personal data).
Guardrails are stateless and idempotent. Failed guardrail → write is blocked + `state/audit.md` entry appended.

### 25.2 Middleware Pipeline (AFW-3)

Every state write passes through a composed middleware chain created by `compose_middleware()`. Three hook points:

- `before_step(domain, content, ctx)` — pre-write validation + guardrails
- `after_step(domain, result, ctx)` — post-write audit + PII log
- `on_error(domain, error, ctx)` — rollback + error state capture

**Active components (compose order defined in `config/middleware.yaml`):**

| Component | Role |
|-----------|------|
| `PiiMiddleware` | Scans for PII patterns before and after each write |
| `WriteGuardMiddleware` | Enforces net-negative write protection (>20% field reduction) |
| `WriteVerifyMiddleware` | Post-write checksum verification |
| `AuditMiddleware` | Appends structured entry to `state/audit.md` on every write |
| `RateLimiterMiddleware` | Sliding-window rate limit enforcement |

Components are isolated and hot-composable. Adding a new component = add class + register in YAML. No existing components are modified.

### 25.3 Progressive Disclosure — Domain Lazy-Loading (AFW-2)

Six **always-load domains** load at session start: `health-check`, `open_items`, `memory`, `goals`, `self_model`, `audit`. All other domains load only when:

- A router signal routes an incoming item to them (catch-up), or
- The user issues `/domain <name>` or a natural-language query referencing them.

Domain load state is tracked by `scripts/domain_index.py`. The **index card** (~600 tokens for all 24 domains) is built from state-file YAML frontmatter and provides enough metadata to route without full prompt load.

**Token savings:** ~80% on `/status` and `/items` (zero domain prompts loaded); 35–50% on typical catch-up sessions (5–7 active of 24). Builds on the §9.8 Tiered Context Architecture — AFW-2 adds command-aware prompt gating on top of the tier model.

### 25.4 Context Compaction (AFW-4)

`scripts/session_summarizer.py` monitors context window pressure and triggers compaction when utilization exceeds `CompactionPolicy.threshold` (default: 70%). Compaction strategy:

1. **Phase output compaction** — replace verbose per-domain extraction output with structured summary
2. **Sliding window** — retain last N exchanges verbatim; summarize earlier exchanges into `tmp/session_history_N.md`
3. **Artifact offload** — intermediate results >5K tokens written to `tmp/` via `scripts/context_offloader.py`

Compaction is non-destructive — compacted content is preserved in `tmp/`. Disclosed to user via the `/health` harness metrics block (§10.5).

### 25.5 Workflow Checkpointing (AFW-5)

Long-running workflows (catch-up, bootstrap, eval) write checkpoint state to `tmp/checkpoint_<workflow>.json` after each major phase completes. Checkpoint TTL: **4 hours**. On session restart within TTL, Artha detects the checkpoint and offers resume:

```
Interrupted catch-up detected (phase 3/7 complete, 38 min ago).
Resume from Email Classification? [yes / start fresh]
```

**Checkpoint schema:** `{ "workflow": str, "phase": int, "phase_name": str, "completed_phases": list, "timestamp": ISO8601, "ttl_hours": 4 }`.

**Implementation:** `scripts/lib/state_snapshot.py` handles both checkpoint write and snapshot-before-write (see §25.6).

### 25.6 Session Rewind / Undo (AFW-6)

Before every state write, `scripts/lib/state_snapshot.py` captures a per-domain snapshot to `tmp/snapshots/<domain>_<timestamp>.md`. The `/undo` command restores the most recent pre-write snapshot:

```
/undo              # Restore all domains modified in this session
/undo immigration  # Restore only immigration.md to pre-session state
```

**Undo confirmation flow:** Shows a diff summary before applying — `"Restoring immigration.md to state from 14 min ago. 3 fields will revert. Confirm? [yes/no]"`.

**Safety rules:**
- Items with composite signal score ≥0.66 (§25.8) generate a suppress-on-undo warning before restore
- Snapshots are session-scoped and purged by `vault.py encrypt` at session close
- Undo of a multi-domain catch-up asks for domain confirmation to prevent bulk accidental rollback

### 25.7 Flat-File Memory System (AFW-7 / ADR-001)

**ADR-001 (binding):** The permanent memory backend is `FlatFileProvider` — YAML-frontmatter Markdown files in `state/`. No SQLite, no vector database, no external memory service.

**Implementation (`scripts/lib/memory_provider.py`):**

| Capability | Detail |
|-----------|--------|
| YAML frontmatter | Each memory entry stores `domain`, `tags`, `created_at`, `importance` in frontmatter; prose content follows the `---` separator |
| Synonym expansion | `_expand_query()` expands query terms via `_SYNONYMS` dict before search. E.g., `"money"` → `["finance", "budget", "salary", "investment"]`. Enables natural-language recall without embedding models. |
| Scoped recall | `remember("visa status")` scans `state/memory.md` + domain-specific files via frontmatter filters |
| Write deduplication | Before appending, provider checks normalized-content hash against existing entries for the same domain |
| Schema migrations | `config/memory.yaml` declares `schema_version` and `upgrade_trigger`. `artha.py --upgrade-memory` runs zero-downtime migration. |

### 25.8 Composite Signal Scoring (AFW-9)

Every candidate briefing item receives a composite score:

$$\text{score} = w_u \cdot \text{urgency} + w_i \cdot \text{impact} + w_f \cdot \text{freshness}$$

**Default weights:** urgency=0.50, impact=0.30, freshness=0.20. All inputs normalized 0.0–1.0.

**Routing thresholds:**

| Score | Behavior |
|-------|---------|
| < 0.20 | Suppress — excluded from briefing |
| 0.20–0.65 | Include normally |
| ≥ 0.66 | Promote — surfaced at top of briefing section |

Domain-specific weights configurable (e.g., immigration urgency=0.70). The ONE THING selection uses the highest composite score. Implementation: `scripts/lib/signal_scorer.py`.

### 25.9 Structured Tracing & Observability (AFW-11)

Every catch-up session generates a `trace_id` (UUID4) that propagates through all phases, log lines, and audit entries. Structured log format: JSONL, written to `tmp/trace_<trace_id>.jsonl`.

**Log entry schema:**
```json
{
  "trace_id": "uuid4",
  "session_id": "uuid4",
  "phase": "email_routing",
  "event": "domain_routed",
  "domain": "finance",
  "ts": "ISO8601",
  "duration_ms": 42,
  "level": "INFO"
}
```

`scripts/log_digest.py` aggregates trace files into daily summaries, detects gap trends (domains skipped, phases timing out), and feeds the eval pipeline (§21). Trace files are ephemeral — purged after `log_digest` runs (default: 7-day retention in `tmp/`).

### 25.10 Implementation Status

| AFW Item | Feature | Status | Primary File |
|---------|---------|--------|-------------|
| AFW-1 | Tripwire Guardrails | ✅ Complete | `scripts/middleware/guardrails.py` |
| AFW-2 | Progressive Disclosure | ✅ Complete | `scripts/domain_index.py` |
| AFW-3 | Middleware Pipeline | ✅ Complete | `scripts/middleware/` |
| AFW-4 | Context Compaction | ✅ Complete | `scripts/session_summarizer.py` |
| AFW-5 | Workflow Checkpointing | ✅ Complete | `scripts/lib/state_snapshot.py` |
| AFW-6 | Session Undo | ✅ Complete | `scripts/lib/state_snapshot.py` |
| AFW-7 | Flat-File Memory (ADR-001) | ✅ Complete | `scripts/lib/memory_provider.py` |
| AFW-8 | Plugin Safety Architecture | ⏳ Deferred | Requires ≥15 plugin components |
| AFW-9 | Composite Signal Scoring | ✅ Complete | `scripts/lib/signal_scorer.py` |
| AFW-10 | Domain Training & Feedback | 🔒 Gated on AFW-7 | Unblocked; not yet scheduled |
| AFW-11 | Structured Tracing | ✅ Complete | `scripts/log_digest.py` |
| AFW-12 | Declarative Agent Definitions | 🔒 Gated on AFW-3 | Middleware adoption required |

### 25.11 Baseline Agent Patterns

Six composable patterns (Anthropic 5 + Artha extension) used across all agent interactions:

| # | Pattern | Artha Usage |
|---|---------|-------------|
| 1 | **Routing** | Keyword-based single-agent selection via `agent_router.py` |
| 2 | **Parallelization** | Fan-out independent agents via `fan_out.py` (EAR-5) |
| 3 | **Orchestrator-Workers** | Artha is orchestrator; domain agents are specialist workers |
| 4 | **Evaluator-Optimizer** | Low-quality response → retry with dimension feedback (EAR-6) |
| 5 | **Prompt Chaining** | Agent output feeds next agent via `agent_chainer.py` (EAR-2) |
| 6 | **Hierarchical Scoped Memory** | Per-agent learning across invocations via `agent_memory.py` (EAR-1) |

### 25.12 Anti-Patterns (V2.1 Constraints)

Eleven architectural constraints enforced by code review and runtime guardrails:

1. **LLM-in-hot-path routing** — use keyword/TF-IDF fallback instead
2. **Unbounded context growth** — enforce memory caps + TTLs
3. **Silent feature bypass** — guardrails required; fail loud
4. **Concurrent unsynchronized cache writes** — `threading.Lock()` keyed on `(agent_name, cache_path)`
5. **Tool approval state not persisted** — sticky decisions via checkpoint
6. **Recursive scrubbing not enforced** — every inter-agent handoff must scrub per receiving trust tier
7. **Prompt-driven safety enforcement** — moved to runtime middleware
8. **No inference cost tracking** — budget cap required on evaluator-optimizer retries
9. **Dead middleware** — all 5 existing middleware must remain backward-compatible on new hooks
10. **Orphaned subprocess on CLI exit** — no async heal fire-and-forget
11. **Circular config import** — lazy import constraints: `dq_gate ← lib.knowledge_graph → work_reader`

### 25.13 Context Budgeting

| Scope | Budget |
|-------|--------|
| Default per-agent invocation | 12,000 tokens |
| KB neighborhood walk (`context_for`) | 4,000 tokens |
| Post-briefing sliding window | 6,000 tokens |
| Chain-level context cap | 6,000 chars across all steps |
| Memory injection per agent | 1,500 chars max (5 recent daily entries + full memory.md) |
| Blueprint override example | `icm-triage` = 12,000 (needs 10+ KB sections vs. global 6,000) |

### 25.14 Governance Rules

| Rule | Constraint |
|------|-----------|
| Personal domain access | No agent ever accesses personal domains (finance, immigration, health, kids) — work-scoped only |
| Write capability | All write-capable agents deferred to V2.1 (informational agents only in V2.0) |
| Max concurrent invocations | 3 (configurable) |
| Max chain steps | 5 |
| Per-agent timeout | 60s default (configurable) |
| Pool-level timeout | max(individual timeouts) + 10s synthesis overhead |
| Max retries per invocation | 1 (evaluator-optimizer) |
| Optimizer weekly budget | 50 retries (rolling 7-day cap, append-only JSONL counter) |
| PII scrubbing | Applied per agent based on trust tier (not global redact-all) |
| Injection detection | Mandatory on every agent prompt before dispatch |
| Response verification | Entity-based KB check required for all responses |

---

## 26. KB-LINT — Cross-Domain Data Health *(v3.23.0)*

**Script:** `scripts/kb_lint.py` | **Config:** `config/lint_rules.yaml` | **Tests:** `tests/test_kb_lint.py` (46), `tests/eval/test_lint_regression.py` (4)

### 26.1 Architecture

KB-LINT reuses existing data-quality infrastructure:

| Reuse | Source | Usage |
|-------|--------|-------|
| `_parse_frontmatter()` | `scripts/lib/dq_gate.py` | YAML frontmatter extraction in all passes |
| `domain_index.py` | `scripts/domain_index.py` | O(N) state-file scanning |
| `CheckResult` pattern | `scripts/preflight.py` | Pass output dataclass |
| `prompt_linter.py` | `scripts/tools/prompt_linter.py` | Precedent for deterministic linting |

Core dataclasses: `LintFinding(severity, pass_id, file_name, field, message, fix_hint)`, `PassResult(pass_id, findings, files_scanned, duration_ms)`, `LintResult(pass_results, files_scanned, duration_ms)`.

`health_pct = (files with zero P1 errors) / (total files scanned) × 100`

### 26.2 Six-Pass Pipeline

| Pass | Name | Checks | Severity |
|------|------|--------|----------|
| P1 | Frontmatter Gate | `schema_version`, `last_updated`, `sensitivity` present and valid | ERROR |
| P2 | Stale Date Detector | Date fields older than per-domain thresholds (90d high-sensitivity, 180d standard) | WARNING |
| P3 | Orphan Reference Checker | Every domain reference valid against `config/domain_registry.yaml` active slugs | WARNING |
| P4 | Contradiction Scanner | Cross-file pattern pairs from `contradiction_patterns` in `lint_rules.yaml` | WARNING |
| P5 | Cross-Domain Rules | 8 built-in declarative YAML rules (insurance↔vehicle, health↔kids, finance↔insurance, etc.) | per-rule |
| P6 | Custom Rules | User-defined extensions in `config/lint_rules.yaml` | per-rule |

### 26.3 CLI Interface

```
python scripts/kb_lint.py              # Full six-pass audit; interactive findings table
python scripts/kb_lint.py --fix        # Auto-remediate P1/P2 issues (with confirmation)
python scripts/kb_lint.py --json       # Machine-readable JSON output (CI integration)
python scripts/kb_lint.py --init       # Bootstrap missing state files from templates
python scripts/kb_lint.py --pass P1    # Run a single pass only
python scripts/kb_lint.py --brief-mode # Single-line Data Health output (briefing use)
```

### 26.4 Briefing Integration

`--brief-mode` outputs a single line injected by `Artha.core.md` Step 20b:

```
Data Health: 100% (24 files, OK, 312ms)           # clean
Data Health: 96% (24 files, 1 warning, 289ms)     # warnings only
⚠ Data Health: 83% (24 files, 2 errors, 341ms) — run `lint` for details  # escalated
Data Health: ⚠ lint error — run `lint` manually   # crash fallback
```

### 26.5 `config/lint_rules.yaml` Schema

```yaml
version: "1.0"
cross_domain_rules:
  - rule_id: kbl-p5-001
    description: "Insurance references vehicle not in vehicle.md"
    severity: WARNING          # ERROR | WARNING | INFO
    check_type: cross_reference
    source_domain: insurance
    target_domain: vehicle
    source_pattern: "vehicle|car|auto"
    target_pattern: "\\b(Honda|Toyota|Subaru)\\b"
```

### 26.6 Implementation Status

| Component | Status |
|-----------|--------|
| P1 Frontmatter Gate | ✅ Complete |
| P2 Stale Date Detector | ✅ Complete |
| P3 Orphan Reference Checker | ✅ Complete |
| P4 Contradiction Scanner | ✅ Complete |
| P5 Cross-Domain Rules (8 built-in) | ✅ Complete |
| P6 Custom Rules | ✅ Complete |
| `--brief-mode` briefing integration | ✅ Complete |
| `--fix` auto-remediation | ✅ Complete |
| `--json` machine output | ✅ Complete |
| `Artha.core.md` Step 20b hook | ✅ Complete |
| 46 unit tests + 4 regression tests | ✅ Complete |

---

## 27. External Agent Reloaded (EAR v2.0) *(v3.25)*

Multi-agent composition extending AR-9's single-agent invocation model to support chaining, scheduling, parallel fan-out, and compound learning. Twelve enhancements (EAR-1 through EAR-12) shipped as 13 new `scripts/lib/` modules. Canonical feature reference for the changelog entry in v3.25.0.

### 27.1 Feature Summary

| ID | Feature | Module | Key Spec |
|----|---------|--------|----------|
| EAR-1 | Agent Memory (Compound Learning) | `agent_memory.py` | Per-agent `memory.md` (max 4KB curated long-term) + `daily/` logs (auto-pruned >14d). Dedup on >0.85 TF-IDF similarity. Top 5 relevant entries loaded (1,500 char budget). Merge on contradiction: trust-tier-wins, else last-write-wins. |
| EAR-2 | Agent Chaining (DAG) | `agent_chainer.py` | YAML-defined chains with `feeds_from` + gate conditions. Output N → verify → score → integrate → input N+1. `ChainStepState` carries prose+entities+key_assertions. Final quality = geometric mean of step scores. Max 5 steps/chain, max 3 active chains. |
| EAR-3 | Scheduled Pre-Computation | `agent_scheduler.py` | Cron schedules in `schedules.yaml`, `--tick` runs past-due. Per-agent `staleness_tolerance_seconds` (default 3600, icm-triage=900, deployment=1800). Max 5 scheduled agents, max 4 runs/agent/day. 3 consecutive failures → suspend. |
| EAR-4 | Enhanced Lexical Routing (TF-IDF) | `tfidf_router.py` | Two-tier: keyword match (<10ms) → if confidence <0.4, TF-IDF character-trigram fallback. Vectors pre-computed to `tmp/ext-agent-route-vectors.json`. Confidence margin instrumented; median <0.10 over 7d → heartbeat alert. |
| EAR-5 | Parallel Fan-Out | `fan_out.py` | `ThreadPoolExecutor` max 3. Per-agent timeout; pool timeout = max(individual) + 10s. `threading.Lock()` per `(agent_name, cache_path)`. Degradation: pool timeout → return best single result. |
| EAR-6 | Evaluator-Optimizer Loop | `evaluator_optimizer.py` | Trigger: Q <0.6 AND any dimension <0.45 AND min_dimension ≥0.2. Max 1 retry, accept max(Q1, Q2). Weekly budget: 50 retries (JSONL counter). Budget exhausted or min_dimension <0.2 → fallback cascade. |
| EAR-7 | Agent Blueprints | `config/agents/blueprints/` | 8 templates: icm-triage, deployment-monitor, backlog-analyst, meeting-prep, knowledge-curator, escalation-drafter, fleet-health, code-reviewer. CLI: `agent_manager create --blueprint <name> --var <k=v>`. `capability_type`: informational/actionable (actionable deferred V2.1). |
| EAR-8 | Heartbeat Health Monitor | `agent_heartbeat.py` | Checks: stale cache (age > TTL × 1.5), declining quality trend (5-score window), idle >7d, ≥3 consecutive failures, approaching auto-retirement (suspended >20d). Recovered via `reinstate` (reset quality to 0.5). Briefing section: `§ Agent Fleet Health`. |
| EAR-9 | SOUL Principles | `soul_allowlist.py` | Per-agent declarative persona + guardrails injected into domain prompt. SOUL compliance checklist in response validator. |
| EAR-10 | Cross-Agent Knowledge Propagation | `knowledge_propagator.py` | High-confidence facts (≥0.8) propagate to other agents' `daily/` logs (not curated `memory.md`). Contradiction merge: trust-tier-wins, else newer wins. Audit log records propagation. |
| EAR-11 | Adaptive Context Budgeting | `adaptive_context.py` | Remaining token count → progressive domain loading (DAG topological sort). Pressure >0.8 → compress intermediates. Pressure >0.95 → skip low-priority domains. |
| EAR-12 | Agent Feedback Loop | `correction_tracker.py` | User Step 19 correction triggers: (1) agent memory update, (2) KB entity upsert, (3) quality score downgrade, (4) audit record. Repeated corrections → routing confidence degradation. |

### 27.2 Multi-Agent Composition Pipeline

```
Route → (Parallel fan-out if independent) → Compose → Invoke → Verify → Score
  → Integrate → Cross-propagate → Cache
```

Chaining enables sequential output→input pipelines with gate conditions for cost/quality control. Each inter-agent handoff applies recursive scrubbing per the receiving agent's trust tier.

### 27.3 Personal Domain Gates

All agents subject to `work_scoped_only: true` — no agent can invoke personal domain skills or read personal encryption keys. Shadow entities in KB allow work graph to reference personal domains (e.g., `shadow-passport` pointing to `artha://personal/immigration/passport`) without copying data.

### 27.4 Deferred to V2.1

- **Streaming architecture**: Agent responses streamed token-by-token (vs. batch); interactive mid-response feedback
- **Marketplace model**: Ed25519-signed agent packages for distribution/verification
- **Write-capable agents**: Actionable agents that can modify state (vs. informational-only in V2.0)

---

## 28. KB Population Strategy *(Reference)*

The Knowledge Base population strategy defines a 7-phase approach for bootstrapping KB entities from existing state files and connected data sources. The full operational procedure (17 deterministic parsers, 600+ target entities, 72 tasks / ~9 hours of work) is archived at `.archive/specs/kb-population-plan.md`.

### 28.1 Phase Overview

| Phase | Scope | Entity Target |
|-------|-------|:---:|
| 1 | Work state files (`state/work/*.md`) | ~200 |
| 2 | Knowledge Markdown files (`knowledge/*.md`) | ~150 |
| 3 | ADO/Kusto golden queries | ~50 |
| 4 | Document metadata (specs, docs, archives) | ~80 |
| 5 | People & team enrichment from meeting/comms data | ~60 |
| 6 | Decision & commitment extraction from notes | ~40 |
| 7 | Gap analysis & cross-reference integrity | ~20 |

### 28.2 Key Implementation Constraints

- All 17 parsers are **deterministic** (regex + structured parse, no LLM inference)
- `kb_bootstrap.py` is **idempotent** — safe to re-run; hash-based dedup prevents duplicates
- Entity confidence starts at 0.5 and converges via corroboration from multiple sources
- Validation: `validate_integrity()` runs after each phase; referential integrity violations block next phase
- Full plan archived at `.archive/specs/kb-population-plan.md` for implementation reference

---

## 29. OpenClaw Home Bridge *(v1.7.0)*

> **Source spec archived at:** `.archive/specs/claw-bridge.md`
> **Implementation status:** All phases complete; bridge gated by `config/claw_bridge.yaml` (`enabled: false` by default — requires Phase 0 keyring setup before enabling)

The OpenClaw (OC) Home Bridge connects Artha (Mac intelligence hub) to the OpenClaw home automation system (Home Assistant + Mac mini + Windows relay) via a hardened M2M channel. The bridge lets Artha's daily context drive home TTS announcements, kid-arrival presence buffers, and WhatsApp-gated drafts; home presence and energy events flow back into Artha's briefing pipeline. All communication is secured with HMAC-SHA256 and strictly role-scoped — Artha never controls HA devices; OC never writes Artha state files.

### 29.1 Role Clarity

| Responsibility | Owner |
|---|---|
| Email intel, open items, finance, goals, briefings | **Artha** |
| HA device control, presence detection, TTS playback, sensors | **OpenClaw** |
| WhatsApp delivery (wacli), energy real-time data | **OpenClaw** |
| TTS briefing content, kid-arrival context, energy alerts, WhatsApp draft text | **Collaborative** |
| Exec allowlist (which scripts OC may invoke), HMAC key rotation initiation | **Artha** |

Artha is **read-only** on the OC side. OC is **write-never** on Artha state files.

### 29.2 Transport Topology

Three-layer transport with automatic fallback:

| Layer | Transport | Latency | Availability |
|---|---|---|---|
| 2 (primary) | REST POST `http://192.168.50.90:18789/artha-context` | <500 ms | LAN (Mac home) |
| 1 (fallback) | Telegram M2M `@openclaw_home_bot` | 2–10 s | Universal |
| 0 (buffer) | `tmp/home_events_buffer.jsonl` → `state/home_events.md` | async | Offline survival |

LAN REST attempted first; failure triggers Telegram M2M; all inbound events also written to file buffer for DLQ recovery.

### 29.3 Security Model

- **HMAC-SHA256** on every message; envelope schema `claw-bridge/1.0`
- Keys stored in OS keyring only (`keyring` library) — never in config files or environment variables
- **Replay protection**: per-message `nonce` (uuid4) + `ts`; clock drift >2 min → message rejected
- **Injection filter**: all inbound `cmd` and `data` fields scrubbed before any eval path
- **PII on wire**: WhatsApp recipients encoded as TK-NNN tokens only; no phone numbers transmitted
- **Key rotation**: version-keyed (`v1`/`v2`), dual-accept 24-hour overlap window; rotation requires all machines reachable and clock drift <2 min

### 29.4 Message Contract

Envelope fields: `schema`, `src`, `cmd`, `data`, `sig`, `ts`, `nonce`, `trace_id`.

#### Outbound Commands (Artha → OC)

| Command | Purpose | Cardinality Limits |
|---|---|---|
| `load_context` | Push daily briefing context for TTS/WhatsApp drafts | max 5 `p1_items`, 5 `deadlines_7d`, 4 `kid_flags`, 3 `goals_active` |
| `announce` | Trigger TTS announcement immediately | ≤200 chars; requires presence buffer active |
| `whatsapp_draft` | Surface approval-gated draft in OC UI | Recipient as TK-NNN token; no phone on wire |
| `ping` | Health heartbeat | — |

#### Inbound Events (OC → Artha)

| Event | Purpose | Artha Handling |
|---|---|---|
| `presence_detected` | Person arrived home | Write to `state/home_events.md`; trigger kid-arrival buffer |
| `energy_event` | Anomalous energy reading | Write to `state/home_events.md`; surface in next briefing |
| `home_alert` | HA-generated sensor alert | Write to `state/home_events.md` |
| `pong` | Heartbeat response | Update `bridge_health.last_pong` in `state/health-check.md` |

`version_hash` is excluded from context envelopes (high churn → false diffs).

### 29.5 Component Inventory

#### New Components (7 files)

| File | Role |
|---|---|
| `scripts/export_bridge_context.py` | Serialises pipeline state → bridge envelope (cardinality + PII + injection filter) |
| `scripts/lib/hmac_signer.py` | HMAC-SHA256 sign/verify; keyring-only key storage; clock-drift guard |
| `scripts/channel/m2m_handler.py` | Telegram M2M send/receive; DLQ retry with exponential back-off |
| `config/claw_bridge.yaml` | Bridge configuration (`enabled: false` by default) |
| `config/agents/artha-bridge.skill.md` | OC-facing Artha skill definition |
| `state/home_events.md` | Inbound event log (home-local only; gitignored) |
| `tests/unit/test_bridge_hmac.py`, `test_bridge_export.py`, `test_bridge_m2m.py` | Test suite (61 tests total) |

#### Modified Components (4 files)

| File | Change |
|---|---|
| `scripts/pipeline.py` | Calls `export_bridge_context.py` after REVIEW_GATE phase |
| `scripts/router.py` | Routes `home_events` domain from `state/home_events.md` |
| `scripts/channel/channel_listener.py` | Delegates `@openclaw_home_bot` messages to `m2m_handler.py` |
| `scripts/nudge_daemon.py` | Checks bridge health; surfaces `bridge_health` staleness alerts |

### 29.6 Observability

A dedicated `bridge_health` block in `state/health-check.md` tracks:

| Metric | Warning | Critical |
|---|---|---|
| `last_push` (Artha → OC) | >24 h | >48 h |
| `last_pong` (OC heartbeat) | >2 h | >6 h |
| `clock_drift_s` | >120 s | >300 s |
| `dlq_depth` | >5 msgs | >20 msgs |

Additional fields: `bridge_version`, `uptime_s`, `msgs_sent_24h`, `msgs_recv_24h`, `key_version`.

### 29.7 Coexistence with `action_bridge.py`

The two bridges are independent with non-overlapping transports, auth, and state models:

| Dimension | `claw_bridge` (this section) | `action_bridge` (Work OS) |
|---|---|---|
| Transport | REST LAN + Telegram M2M | OneDrive file-based |
| Auth | HMAC-SHA256 + keyring | Azure AD / MSI |
| Participants | Artha ↔ Home Automation | Artha ↔ Work OS agents |
| State model | `state/home_events.md` | `state/bridge/` |
| Shared infrastructure | `audit.py`, `security.py`, `retry.py` | ← same |

### 29.8 Test Coverage

| Test File | Tests | Scope |
|---|---|---|
| `tests/unit/test_bridge_hmac.py` | 21 | Sign/verify, replay protection, clock drift, key rotation |
| `tests/unit/test_bridge_export.py` | 20 | Context serialisation, cardinality limits, PII filter, injection filter |
| `tests/unit/test_bridge_m2m.py` | 20 | Telegram send/recv, DLQ retry, fallback logic, offline buffer |

**Run:** `pytest tests/unit/test_bridge_*.py` — all 61 passing as of v1.7.0.

---

---

## 30. Career Search Intelligence *(FR-25, Phase 1)*

**Implements:** PRD FR-25 · **Shipped:** April 2026 · **Upstream credit:** `specs/career-ops.md` v1.3.0 (archived to `.archive/specs/career-ops.md`)

### 30.1 Architecture

Lazy-loaded domain with deferred skill execution. Career evaluation (~20–32K tokens) is explicitly invoked via `/career eval` — NEVER loaded during catch-up or briefing. Only `state/career_search.md` frontmatter `summary:` block is read during briefing generation.

**Component map:**

| Component | File | Role |
|-----------|------|------|
| Evaluation prompt | `prompts/career_search.md` | 7-block A–G framework, scoring rubric, archetype detection |
| Career state | `state/career_search.md` | Live application tracker, CV config, story bank |
| State template | `state/templates/career_search.md` | Bootstrap template for campaign activation |
| CV template | `templates/cv-template.html` | ATS-optimized HTML (Space Grotesk + DM Sans) |
| PDF skill | `scripts/skills/career_pdf_generator.py` | BaseSkill: HTML → Playwright → PDF |
| Portal scanner | `scripts/skills/portal_scanner.py` | BaseSkill: Greenhouse/Ashby/Lever scanning (Phase 2) |
| State helpers | `scripts/lib/career_state.py` | `reconcile_summary()`, `recompute_scores()`, `deep_freeze()`, fingerprints, story bank |
| Audit trace | `scripts/lib/career_trace.py` | JSONL audit trail, 90-day retention |
| Guardrails | `scripts/middleware/guardrails.py` | `CareerJDInjectionGR`, `CareerNoAutoSubmitGR`, `CareerPiiOutputGR` |

### 30.2 Python Helpers (`career_state.py`)

- `reconcile_summary(state_path) → bool` — recomputes `summary:` frontmatter from tracker table (Design B: briefing reads only summary block, not full tracker)
- `recompute_scores(report_path, state_path) → float` — deterministic weighted score from per-dimension integers; reads weights from `state/career_search.md` frontmatter `scoring_weights:` — NEVER hardcoded
- `deep_freeze(obj) → Any` — `MappingProxyType` + tuple recursion for `DomainSignal` metadata immutability
- `SCORED_STATUSES = frozenset({"Evaluated","PartialEval","Applied","Responded","Interview","Offer"})` — excludes SKIP/Rejected/Discarded from average_score computation
- `cross_tracker_dedup_match()` — Jaccard similarity ≥0.85 for portal re-posting deduplication
- `build_story_bank_index()` — 20-story cap, 5 pinned slots, closed tag vocabulary

### 30.3 PDF Generation (`career_pdf_generator.py`)

`BaseSkill` subclass following canonical `home_device_monitor.py` pattern. Does NOT override `execute()`.

- `pull()`: activation guard (`campaign.status` check), reads `cv.md` + evaluation report + HTML template
- `parse()`: HTML merge, keyword injection, Unicode normalization, delegates to `_render_pdf()`
- `_render_pdf()`: isolated Playwright boundary — retries once on failure, HTML fallback on second failure
- Fonts: Space Grotesk + DM Sans, self-hosted in `fonts/` (woff2), installed via `scripts/install_fonts.sh`
- Preflight: FR-CS-3 — `scripts/preflight.py` checks Playwright + Chromium binary in ms-playwright cache

### 30.4 Scoring System

Six dimensions; weights read from `state/career_search.md` frontmatter `scoring_weights:` — NEVER hardcoded in code. Fixed fallback weights apply when Block D (Compensation) unavailable — no runtime redistribution.

| Dimension | Default Weight | Fallback Weight (no comp data) |
|-----------|---------------|--------------------------------|
| CV Match | 0.30 | 0.35 |
| North Star | 0.20 | 0.24 |
| Compensation | 0.15 | — (omitted) |
| Culture | 0.15 | 0.18 |
| Level Fit | 0.10 | 0.12 |
| Red Flags | 0.10 | 0.11 |

Score = Σ(dimension_score × weight). Final score: 0.0–1.0 (two decimal places). Stored in `state/career_search.md` tracker `Score` column and `state/career_audit.jsonl`.

### 30.5 Security & Guardrails

Three career-specific guardrails in `guardrail_registry.py`:

| Guardrail | Class | Mode | Rule |
|-----------|-------|------|------|
| JD Injection | `CareerJDInjectionGR` | blocking | Trust boundary: discard injected LLM instructions from external JD URLs |
| No Auto-Submit | `CareerNoAutoSubmitGR` | blocking | Hard rule — NEVER auto-submit applications under any prompt |
| PII Output | `CareerPiiOutputGR` | blocking | SSN/tax-ID abort; phone number redaction before output |

Auth wall detection runs BEFORE `CareerJDInjectionGR`. `cv.md` and `article-digest.md` are gitignored and stored at `~/.artha-local/`. Career audit trail at `state/career_audit.jsonl` (JSONL, 90-day auto-trim, also gitignored).

### 30.6 State Files

| File | Repo | Role |
|------|------|------|
| `state/career_search.md` | tracked | Live tracker + CV config + story bank |
| `state/career_audit.jsonl` | gitignored | JSONL audit trail (90-day retention) |
| `~/.artha-local/career/` | local only | Dedup fingerprints + scan history (outside repo) |
| `briefings/career/{NNN}-{company}-{date}.md` | gitignored | Archived evaluation reports |
| `output/career/cv-{company}-{date}.pdf` | gitignored | Generated ATS-optimized PDFs |
| `output/career/.gitkeep` | tracked | Directory marker |
| `briefings/career/.gitkeep` | tracked | Directory marker |

### 30.7 Signal Routing

Six career signals registered in `config/signal_routing.yaml`:

| Signal | Trigger | Default Channel |
|--------|---------|-----------------|
| `career_eval_complete` | Evaluation report generated | `briefing` |
| `career_interview_scheduled` | Interview confirmed in calendar | `nudge` |
| `career_offer_received` | Offer letter detected | `nudge` |
| `career_offer_deadline` | Offer deadline T-48h | `nudge` |
| `career_application_rejected` | Rejection received | `briefing` |
| `new_portal_match` | Portal scan finds matching role (Phase 2) | `nudge` |

### 30.8 Test Coverage

| Test File | Tests | Scope |
|-----------|-------|-------|
| `tests/test_career_search.py` | 65 | Prompt blocks A–G, scoring, archetype detection, story bank |
| `tests/test_career_content.py` | 52 | State transitions, dedup, PDF skill, guardrails |
| `tests/unit/test_career_skills.py` | 10 | P0 AR-1: `_ALLOWED_SKILLS`, directory existence, skills.yaml |

**Run:** `python tests/test_career_search.py && python tests/test_career_content.py` — 65/65 + 52/52.  
`pytest tests/unit/test_career_skills.py` — 10/10.

### 30.9 Phase 2 Deferred Items

- `portal_scanner.py` skill: Greenhouse/Ashby/Lever OAuth scraping (registered in `_ALLOWED_SKILLS`, not invoked in Phase 1)
- `/career scan` command routing
- `/career stories` Story Bank review view
- Multi-portal dedup (fingerprint store at `~/.artha-local/career/`)

---

## 31. Safety & Governance Compendium *(v3.31.0)*

> These patterns were codified during the debt-reduction sprint (see archived `specs/debt.md`). All items are implemented and covered by tests.

### 31.1 Signal Routing Governance

Every entry in `config/signal_routing.yaml` carries a `status: active|stub` field.

- **`active`** — a Python producer emits this signal type in production code.
- **`stub`** — forward declaration (e.g., Phase 2 career signals). No producer exists yet.

`tests/unit/test_signal_routing_completeness.py` cross-references all signal emitters against the routing table and fails CI if any `status: active` entry has no Python producer, or if any entry is missing a `status` annotation. Stub entries are excluded from the producer-check but are verified to have the annotation.

Three previously orphaned signal types (`automation_failure`, `goal_autopark_candidate`, `slack_action_item`) were added with routing entries in this sprint.

### 31.2 Idempotency Windows — Domain-Qualified

`scripts/lib/idempotency.py: get_window(action_type, domain='')` resolves windows with the following priority:

1. `config/guardrails.yaml: idempotency_windows.<action_type>_<domain>` (most specific)
2. `config/guardrails.yaml: idempotency_windows.<action_type>`
3. Built-in default (24 hours)

Configured overrides:

| Key | Window |
|-----|--------|
| `instruction_sheet_immigration` | 30 days |
| `instruction_sheet_finance` | 30 days |
| `instruction_sheet_iot` | 4 hours |
| `instruction_sheet` | 24 hours |
| `financial` | 7 days |
| `scheduling` | 48 hours |
| `communication` | 24 hours |

### 31.3 Execution-Layer Idempotency (`instruction_sheet`)

`scripts/actions/instruction_sheet.execute()` calls `check_or_reserve` at entry and `mark_completed` after a successful file write. A duplicate call within the active window returns `ActionResult(status="skipped")` and logs `INSTRUCTION_SHEET_DUPLICATE` to `state/audit.md`. Same-day file overwrites emit a WARNING before writing. This is independent of the pre-proposal idempotency guard in `action_executor.py`.

### 31.4 Memory Writer — FIFO Cap

`scripts/lib/memory_writer.add_fact(fact, memory_path)` enforces the `config/memory.yaml: memory.max_facts` cap (default: 30) at write time:
- If `len(current_facts) >= max_facts`, the oldest fact line is evicted (FIFO) before appending.
- Eviction is logged to `state/audit.md` as `MEMORY_EVICTION`.
- All memory write paths use `add_fact()` — no direct file appends.

### 31.5 Skills Cache Size Governance

`scripts/skill_runner._enforce_cache_size_cap()` is called after each skill result is cached. If the `tmp/skills_cache.json` file exceeds 1 MB, the oldest entries are evicted until the file is under the limit. The cap and eviction strategy are documented in inline comments.

### 31.6 Connector Record Schema Validation

`scripts/schemas/connector_record.py` defines `validate_record(record: dict)` — a lightweight (non-Pydantic) schema check applied to every record returned by connector `fetch()` calls. Required fields: `id`, `source`, `date_iso`. Malformed records are skipped with a WARNING; `tmp/pipeline_metrics.json` tracks `validation_errors` per run. The `--strict` pipeline flag exits with code 2 if any record fails validation.

### 31.7 Routing Ambiguity Flag

`scripts/lib/agent_router.RoutingResult` carries a `routing_ambiguity: bool` field. When the TF-IDF router's top-2 routing scores are within 0.08 of each other (configurable), `routing_ambiguity` is set to `True`. Callers can surface this to the user as a clarification request rather than silently picking one route. The field is included in `tmp/ext-agent-metrics.jsonl`.

### 31.8 TF-IDF Routing Quality Metric

`scripts/lib/agent_router._emit_routing_margin()` writes `routing_quality` events to `tmp/ext-agent-metrics.jsonl`. `eval_runner.py --routing-audit` computes a rolling 7-day `keyword_miss_rate` (fraction of query tokens not matched by the TF-IDF vocabulary). When the rate exceeds the threshold in `config/memory.yaml: routing.keyword_miss_rate_threshold` (default: 0.15), an upgrade-trigger recommendation is emitted in the briefing.

### 31.9 KG Backup — WAL Safety

`scripts/lib/knowledge_graph.KnowledgeGraph.backup()` uses Python's `sqlite3.Connection.backup()` which produces a fully checkpointed, WAL-free snapshot per the SQLite C API specification. The resulting backup file in `backups/{daily|weekly|monthly}/kb-*.sqlite` has no associated `.sqlite-wal` sidecar, preventing OneDrive from indexing incomplete WAL state. The backup path check also calls `Path(db_path).resolve()` (following symlinks) before applying the cloud-sync safety pattern check.

### 31.10 Platform-Gated Connector Freshness

After each pipeline run, `pipeline.py` writes per-connector timestamps to `state/connectors/connector_freshness.json`:

```json
{ "outlook_email": { "last_fetch": "2026-04-13T09:30:00Z", "machine": "WINDOWS-PC" } }
```

On a machine where a platform-gated connector is skipped (e.g., `outlook_email` is Windows-only; `apple_reminders` is macOS-only), the pipeline reads the freshness file from OneDrive and logs the staleness age. If `last_fetch` is >72 hours stale, a CRITICAL warning is included in the Connector Health briefing block.

---

*Artha Tech Spec v3.31.0 — End of Document*

---

## 18. Revision History

| Version | Changes |
|---------|---------|
| v3.31.0 | **Safety & Governance Compendium (§31)**: Signal routing `status: active\|stub` field + completeness CI test (`test_signal_routing_completeness.py`); 3 orphaned signal routes added (`automation_failure`, `goal_autopark_candidate`, `slack_action_item`); domain-qualified idempotency windows (`get_window(action_type, domain)` — immigration 30d, iot 4h); execution-layer idempotency in `instruction_sheet.execute()` (`check_or_reserve`/`mark_completed`); memory writer FIFO cap at `max_facts` with `MEMORY_EVICTION` audit log; skills cache size governance (`_enforce_cache_size_cap()`, 1MB limit); connector record schema validation (`scripts/schemas/connector_record.py`); routing ambiguity flag (`RoutingResult.routing_ambiguity`); `keyword_miss_rate` metric + routing-audit eval mode; KG backup WAL-free via `Connection.backup()` + symlink-safe `Path.resolve()` in cloud-sync check; platform-gated connector freshness JSON (`state/connectors/connector_freshness.json`, 72h CRITICAL threshold). PRD §16.4 Security & Privacy Architecture added. `specs/debt.md` archived to `.archive/specs/debt.md`. |
| v3.30.0 | **Career Search Intelligence (§30, FR-25 Phase 1)**: 7-block A–G evaluation framework (`prompts/career_search.md`); application tracker + Story Bank (`state/career_search.md`); ATS PDF generation via Playwright (`scripts/skills/career_pdf_generator.py`); Python helpers: `reconcile_summary`, `recompute_scores`, `deep_freeze`, Jaccard dedup, story bank index (`scripts/lib/career_state.py`); JSONL audit trail 90-day retention (`scripts/lib/career_trace.py`); 3 career guardrails: `CareerJDInjectionGR`, `CareerNoAutoSubmitGR`, `CareerPiiOutputGR`; 6 career signal routes in `config/signal_routing.yaml`; `career_pdf_generator` and `portal_scanner` added to `_ALLOWED_SKILLS`; AR-1 P0 test suite (`tests/unit/test_career_skills.py`); `output/career/` + `briefings/career/` directories with `.gitkeep`; `.gitignore` hardened for career PII output; FR-CS-3 preflight check for Playwright/Chromium; specs: PRD v7.15.0 (FR-25), Tech Spec §30, UX Spec §12; `specs/career-ops.md` archived to `.archive/specs/career-ops.md`. 65+52+10 tests. |
| v3.29.0 | **OpenClaw Home Bridge (§29)**: M2M integration between Artha (intelligence hub) and OpenClaw (Home Assistant). 3-layer transport (REST LAN + Telegram M2M + file-buffer fallback). HMAC-SHA256 with keyring-only key storage, replay nonce, clock-drift guard, injection filter. Outbound: `load_context`, `announce`, `whatsapp_draft`, `ping`. Inbound: `presence_detected`, `energy_event`, `home_alert`, `pong`. 7 new files (`export_bridge_context.py`, `hmac_signer.py`, `m2m_handler.py`, `claw_bridge.yaml`, `artha-bridge.skill.md`, `state/home_events.md`); 4 modified (`pipeline.py`, `router.py`, `channel_listener.py`, `nudge_daemon.py`). `bridge_health` observability block in `state/health-check.md`. Feature-flagged (`enabled: false`). 61 tests passing. Implements PRD v7.14.0. |
| v3.26.0 | **SPEC CONSOLIDATION**: §21.6–21.8 (eval dimensions/scoring/SLAs), §22.6–22.8 (full entity model/API/performance), §23.6 (AR-9 GA status), §24.4–24.5 (cross-reference rules/domain weights), §25.11–25.14 (baseline patterns/anti-patterns/context budgets/governance), NEW §27 (EAR v2.0 multi-agent composition EAR-1–EAR-12), NEW §28 (KB Population Strategy reference). Implements PRD v7.11.0. |
| v3.24.0 | **AR-9 Safety Hardening (§23.5)**: template injection defense in `prompt_composer.py` (brace escaping + 8K query cap); atomic writes in `knowledge_extractor.py` (`tempfile.mkstemp` + `os.replace`); PII guard fail-safety in `context_scrubber.py` (strict mode blocks on guard failure); quality score clamping in `agent_health.py` (`[0,1]` enforcement); dead import cleanup in `agent_registry.py`. 16 safety invariant tests. 270 AR-9 tests passing. |
| v3.21.0 | **Agent Framework v1 — §25**: AFW-1 Tripwire Guardrails (7 guardrails, blocking + parallel modes, `guardrails.py` + `guardrail_registry.py`); AFW-3 Middleware Pipeline (`compose_middleware()`, 5 components, `config/middleware.yaml`); AFW-2 Progressive Disclosure (6 always-load domains, `domain_index.py`, ~80% token savings on `/status`/`/items`); AFW-4 Context Compaction (`session_summarizer.py`, `CompactionPolicy`, sliding window); AFW-5 Workflow Checkpointing (`state_snapshot.py`, 4h TTL, phase-resume); AFW-6 Session Undo (`/undo [domain]`, snapshot-before-write, diff confirm); AFW-7 Flat-File Memory / ADR-001 (`memory_provider.py`, YAML frontmatter, synonym expansion, scoped recall, dedup); AFW-9 Composite Signal Scoring (`signal_scorer.py`, urgency×impact×freshness, suppress<0.20/promote≥0.66); AFW-11 Structured Tracing (UUID4 trace_id propagation, JSONL, `log_digest.py`). `.gitignore` hardened: `.gemini_security/` + `.gemini/` added. Spec updates: PRD §9.10 + UX §10/§14/§16. |
| v3.20.0 | **AR-9 External Agent Composition (§23) + Data Quality Gate (§24)**: `scripts/lib/agent_registry.py` (YAML-backed registry, drop-folder scan, content_hash dedup, shadow_mode flag, registered_at timestamp); `scripts/lib/agent_router.py` (geometric-mean confidence routing); `scripts/lib/context_classifier.py` (domain-scoped public/scoped/private tagging); `scripts/lib/context_scrubber.py` (outbound PII scrubbing per domain profile); `scripts/lib/injection_detector.py` (recursive injection decoder, confidence threshold); `scripts/lib/agent_invoker.py` (`runSubagent`, 60s timeout, stale-while-revalidate cache in `tmp/ext-agent-cache/`); `scripts/lib/response_verifier.py` (entity-based KB cross-check); `scripts/lib/response_integrator.py` (expert consensus format, fallback cascade: agent→KB→investigation→Cowork); `scripts/lib/knowledge_extractor.py` (response → cache); `scripts/lib/agent_scorer.py` (accuracy × freshness × honesty_bonus); `scripts/lib/agent_health.py` (availability/latency/quality/auto-retirement at 5 consecutive failures); `scripts/lib/ext_agent_audit.py` (JSONL audit trail); `scripts/lib/metrics_writer.py` (eval pipeline integration); `scripts/agent_manager.py` (CLI). DQ Gate: `scripts/lib/dq_gate.py` — pull-based Accuracy×Freshness×Completeness model; QualityVerdict(PASS/WARN/STALE/REFUSE); domain-aware weights; corroborating_sources denormalized field. `.gitignore` hardened: `config/agents/external/`, `config/agents/external-registry.yaml`, `config/agents/*.agent.md` excluded. 4,515 tests: `tests/ext_agents/` (121) + `tests/test_dq_gate.py`. |
| v3.19.0 | **Observability & Eval Framework (§21) + Knowledge Graph Architecture (§22, Work OS)**: `scripts/eval_runner.py` (orchestrates eval harness; closes G1–G10 observability gaps); `scripts/eval_scorer.py` (briefing quality scorer — completeness, priority order, PII compliance, action accuracy; rolling 7-day+30-day averages); `scripts/lib/metric_store.py` (SQLite-backed time-series MetricStore in `~/.artha-local/eval.db`; 90-day auto-prune); `scripts/correction_feeder.py` (user correction → `state/memory.md` + domain confidence delta → `state/self_model.md`); `scripts/log_digest.py` (daily log aggregator; gap trend detection); `scripts/lib/knowledge_graph.py` (`KnowledgeGraph` class — `upsert_entity()`, `add_relationship()`, `query_neighbors()`, `find_path()`; transactional SQLite writes); `scripts/kb_bootstrap.py` (idempotent bootstrap from `knowledge/*.md` state files; hash-based dedup). 127 new eval tests in `tests/eval/` + `tests/unit/test_eval_*.py`. `.gitignore` hardened: machine-specific conflict-copy pattern replaced with generic `state/*.age` superset; OneDrive shadow `.git 2/` fix. |
| v3.8–v3.9 | **Work OS Phases 2–5** (PRD FR-19 FW-11–FW-17, v2.7.0): `scripts/work_bootstrap.py` (12-question guided setup interview, atomic writes, dry-run); `scripts/work_notes.py` (post-meeting capture, D-NNN/OI-NNN IDs, `/work remember` micro-capture); `scripts/work_reader.py` (25-command read-path CLI: work, pulse, sprint, health, return, connect, connect-prep, people, docs, sources, prep, live, newsletter, deck, memo, talking-points, promo-case, promo-narrative, journey, day, decide, graph, preread, incidents, repos); `scripts/work_domain_writers.py` (atomic writers for 11 domain files: calendar, comms, projects, boundary, career, sources, notes, decisions, open-items, people, performance); `scripts/narrative_engine.py` (10 Jinja2 templates: weekly_memo, talking_points, boundary_report, connect_summary, newsletter, deck, calibration_brief, connect_evidence, escalation_memo, decision_memo); `scripts/post_work_refresh.py` (session history writer, run log appender); `scripts/kusto_runner.py` (KQL bridge, ADO/Kusto query runner); `config/agents/` expanded to 3 tiers (artha-work.md baseline, artha-work-enterprise.md corporate ADO, artha-work-msft.md Microsoft Enhanced); `state/work/` expanded 6→20 domain files; `.github/workflows/work-tests.yml` CI matrix; 883 tests in `tests/work/` (12 test files); `specs/work.md` archived to `.archive/specs/work.md` — PRD/Tech/UX specs now canonical. §19.7 state files expanded 8→22 rows; §19.9 test coverage updated ~541→883. |
| v3.7 | Work Intelligence OS (Phase 2C — PRD FR-19, specs/work.md v1.7.0): `scripts/work_loop.py` (7-stage loop: fetch → enrich → filter → infer → score → write → bridge); `scripts/work_warm_start.py` (§15 warm-start processor — ScrapeParser, WarmStartAggregator, 6 state file writers, atomic write, dry-run mode); `scripts/schemas/work_objects.py` (6 canonical dataclasses: WorkMeeting, WorkDecision, WorkCommitment, WorkStakeholder, WorkArtifact, WorkSource; 5 enums: ObjectStatus, CommitmentStatus, DecisionOutcome, StakeholderRecency, plus shared types); `scripts/schemas/work_connector_protocol.py` (ConnectorFailureMode enum, ConnectorProtocolEntry frozen dataclass, PROTOCOL table, get_protocol(), user_signal_for(), log_connector_failure() — §8.4 connector error protocol); `scripts/schemas/bridge_schemas.py` (WorkLoadPulse, BridgeArtifact, BridgeManifest validators + 55 bridge enforcement tests); `config/agents/` (work_briefing_agent.md, work_enrich_agent.md, meeting_prep_agent.md); `state/work/` directory with 6 domain files; `state/bridge/work_load_pulse.json` boundary artifact; 166 new tests in `tests/work/` (test_work_objects.py — 29, test_connector_protocol.py — 31, test_work_warm_start.py — 33, test_bridge_schemas.py — previously 55); §19 Work OS Technical Architecture section added to this spec. PRD bumped to v4.2. UX Spec §23 Work OS Interaction Design added. |
| v3.6 | Deep Agents Option B — Core Harness Patterns (PRD F15.114–F15.118): `scripts/context_offloader.py` (`offload_artifact`, builtin summary fns, `OFFLOADED_FILES`/`OFFLOADED_GLOB_PATTERNS`); `scripts/domain_index.py` (`build_domain_index`, `get_prompt_load_list`, `_domain_status` ACTIVE/STALE/ARCHIVE); `scripts/session_summarizer.py` (`SessionSummary` Pydantic v2 + dataclass fallback, `estimate_context_pct`, `should_summarize_now`, `get_context_card`); `scripts/middleware/` package — `StateMiddleware` Protocol + `compose_middleware()`, `PiiMiddleware`, `WriteGuardMiddleware`, `WriteVerifyMiddleware`, `AuditMiddleware`, `RateLimiterMiddleware`; `scripts/schemas/` package — `BriefingOutput`, `AlertItem`, `DomainSummary`, `FlashBriefingOutput`, `SessionSummarySchema`, `DomainIndexCard`. `config/Artha.md` + `config/Artha.core.md` Steps 4b′/5/7/8h/11b/Session Protocol/harness_metrics/18a′. `config/artha_config.yaml` `harness:` namespace. `pydantic>=2.0.0` in requirements.txt. 698 tests (+157 from 541). |
| v3.5 | Intelligence expansion + platform parity (PRD F15.100–113): `scripts/skills/financial_resilience.py` (`FinancialResilienceSkill` — burn rate, emergency runway, single-income stress; regex parsers for `state/finance.md`; cadence weekly, requires_vault); gig income routing keywords added to `domain_registry.yaml` (Stripe, PayPal, Venmo, Upwork, Fiverr, Etsy, DoorDash, Uber, 1099-K/NEC); `prompts/shopping.md` purchase interval observation; `prompts/social.md` structured contact profiles (9-field) + pre-meeting context injection + passive fact extraction; `prompts/estate.md` digital estate inventory (5 tables); `config/actions.yaml` `cancel_subscription` + `dispute_charge` instruction-sheet actions; `prompts/digital.md` subscription action proposals; `setup.ps1` Windows onboarding parity script; `artha.py --doctor` 11-point diagnostic (`do_doctor()`); `scripts/connectors/apple_health.py` (iterparse streaming, 16 HK types, ZIP+XML, opt-in); `prompts/health.md` longitudinal lab results; `passport_expiry` + `subscription_monitor` added to `_ALLOWED_SKILLS` frozenset. 541 tests (+56 from 485 baseline). |
| v3.4 | OOBE polish audit (PRD F15.95–99): `setup.sh` brand mark + `[1/4]`–`[4/4]` step counters + `--disable-pip-version-check`; `artha.py` `_detect_ai_clis()` + `_print_ai_cli_status()` for tailored post-wizard / welcome next-step; `demo_catchup.py` ANSI colorized output (yellow ACTION, green good, red alert), removed dead footer; `README.md` 624→142 lines + `docs/backup.md` + `specs/README.md` disclaimer; `Makefile` `start` target. 485 tests. |
| v3.3 | Interactive setup wizard + first-run friction fixes: `artha.py` wizard (`do_setup()`), starter profile, no auto-preflight on welcome, `_collect_warnings()` + `_print_validate_summary()` in `generate_identity.py`, `--first-run` preflight mode, `setup.sh` wizard prompt. See §11.4 |
| v3.2 | 10-layer defense-in-depth (§8.5.1): advisory lock, sync fence, post-encrypt verify, deferred deletion, lockdown, mtime guard, net-negative override, prune protection, confirm gate, key health; 501 tests |
| v3.0 | Novice UX hardening: Step 6 restored, age key deletion order fixed, keyring check, open_items template, path PII masking, Node.js prereq, OS blocks |
| v2.9 | Clone-audit hardening (#1–#30): PII scrub in all spec/doc files; vault store-key command; state/templates/ directory with 18 starter files; user_profile.example.yaml extended to 24 domains; preflight P1 enforcement; plist placeholder; PII guard; requirements.txt reorganised; CHANGELOG.md; 429-test suite with xfail markers |
| v2.1 | Initial public release — `/diff`, Weekend Planner, Canvas LMS API Fetch, Apple Health XML Import |

---

## Appendix A — Architectural Hardening Blueprints

> Sourced from `specs/harden.md` v1.6 (archived to `.archive/specs/harden.md`). Blueprints fully implemented as of April 2026.

### A.1 Blueprint 1 — FSM Orchestrator & Lean-Context Instruction Partitioning

`pipeline.py` is the FSM controller. Three-tier hierarchy:

| Tier | Role | Invoked At |
| :--- | :--- | :--- |
| **Orchestrator** | Deterministic Python — routes, never reasons | Every session |
| **Planner** | Frontier model — cross-domain reasoning | CLASSIFY + REVIEW_GATE only |
| **Worker** | Frontier model — domain-scoped extraction, lean context | EXTRACT, once per active domain |

**FSM states:** `PREFLIGHT → FETCH → CLASSIFY → EXTRACT → RECONCILE → REVIEW_GATE → PRESENT → COMMIT`

**Worker context formula:** `[Core Identity] + [prompts/{domain}.md] + [state_delta] + [≤3 few-shot examples from state/audit.md]` — never the full 21-step workflow. Delivers 40–60% token reduction per turn.

**Checkpoint recovery:** On unclean shutdown, `state/checkpoint.json` records `last_completed_state` + `state_outputs`. PREFLIGHT offers resume or restart on next session.

**DEGRADED_MODE:** If planner API unavailable at CLASSIFY, fall back to static 21-step prompt and emit `DEGRADED_MODE` to `state/telemetry.jsonl`.

**Phase 3 gate:** Signal-gated fan-out (dispatch only to domains with active signals) and full FSM formalization are Phase 3 features gated on `harness.ear5.complete: true`. Do not implement earlier.

### A.2 Blueprint 2 — Version-Field OCC on Markdown

Every state file YAML frontmatter gains `version` (int), `last_written_by`, `last_written_at`. Implemented in `scripts/lib/state_writer.py` via `write_occ()`:

1. Read file → capture version N
2. Write tempfile with version N+1
3. `os.replace` (atomic) → re-read → verify version == N+1
4. On mismatch: surface both versions to user — **never auto-merge prose sections**

**Migration:** Files missing `version` treated as version 0. First OCC-aware write injects `version: 1`.

### A.3 Blueprint 3 — Multi-Key Idempotency via Atomic-Write JSON

Composite key: `SHA-256(recipient + intent + date_window)` stored in `state/idempotency_keys.json` (atomic write). Implemented in `scripts/lib/idempotency.py`.

**Per-type windows** (`config/guardrails.yaml → idempotency_windows`):

| Action Type | Window |
| :--- | :--- |
| `scheduling` | 7 days |
| `financial` | 30 days |
| `communications` | 48 hours |
| `default` | 24 hours |

**Protocol:** Write key PENDING → execute → update COMPLETED/FAILED. PENDING keys surfaced to user at PREFLIGHT for explicit resolution. Expired keys pruned at PREFLIGHT.

### A.4 Blueprint 4 — TF-IDF Routing & In-Process PII Hygiene

Three-tier routing: **Tier 1** explicit domain rules (zero tokens) → **Tier 2** TF-IDF cosine similarity (`tfidf_router.py`, threshold 0.4 tunable in `config/guardrails.yaml`) → **Tier 3** UNCLASSIFIED queue (never silently drop).

**PII constraint:** Signal data flows as Python objects in memory only — never written to intermediate files during processing. `action_orchestrator.py` is network-isolated; PII flows terminate at `pipeline.py` boundary.

**Threshold migration path:** Deploy UNCLASSIFIED queue at current 0.1 threshold first → collect ≥14 days telemetry → raise threshold to empirically supported value (≤0.4 target). Never raise before telemetry confirms safety.

### A.5 Blueprint 5 — Tool Boundary Contract & Deterministic Action Validation

**Tool boundary:** Workers call only `compose_draft(domain, payload)`. `execute_action` and `send_message` are Orchestrator-only. After every Worker/Planner response, `pipeline.py` (deterministic Python) inspects for prohibited tool patterns — discards full output and marks domain STALE on violation. >3 violations/session → `BOUNDARY_BREACH` + session termination.

**Deterministic Action Validator** runs before every `execute_action` (implemented in `scripts/actions/base.py` + `scripts/pii_guard.py`):

| Check | Failure Behavior |
| :--- | :--- |
| 1. PII scan (`pii_guard.scan_action_payload`) | Halt; notify user with PII type (not value); action rejected — no LLM retry |
| 2. Schema validation (`_ACTION_REQUIRED_FIELDS`) | Halt; surface missing fields; action returned to Proposed state |
| 3. Scope check | Halt; Worker domain must match action domain |
| 4. Friction floor | `high` friction or `autonomy_floor: true` → verify current-session approval |

**Per-type required fields:**

| Action Type | Required Fields |
| :--- | :--- |
| `scheduling` | `recipient`, `datetime` (ISO 8601), `intent` |
| `financial` | `payee`, `amount`, `category`, `due_date` (ISO 8601) |
| `communications` | `recipient`, `channel` (`email`/`teams`/`sms`), `subject`, `intent` |

### A.6 Observability & Telemetry

Single sink: `state/telemetry.jsonl` (plaintext, append-only, no PII — hashed identifiers only). Key event types:

| Event | Key Fields |
| :--- | :--- |
| Step trace | `step_name`, `ttft_ms`, `latency_ms`, `input_tokens`, `output_tokens`, `model_id`, `session_id` |
| Routing confidence | `signal_id`, `matched_domain`, `confidence`, `tier` |
| Action idempotency | `action_id`, `composite_key_hash`, `status` |
| Guardrail events | `guardrail_name`, `result`, `domain` |
| OCC events | `domain`, `file`, `version_expected`, `version_found`, `resolution` |
| Cost-per-domain | `domain`, `input_tokens`, `output_tokens`, `estimated_cost_usd`, `actions_accepted`, `actions_rejected` |

**Cost-to-intelligence ratio:** `value_score = actions_accepted / max(1, actions_proposed)`. If ratio < 0.5 for 14 consecutive sessions, surface recommendation to switch domain to Manual trigger — never autonomous demotion.

**Reasoning traces:** `state/traces/session_{session_id}.md` — PII-scrubbed before write, pruned after 30 days in PREFLIGHT.

**Baseline snapshot:** `state/telemetry_baseline.json` — computed after ≥7 days of Phase 0 collection. Denominator for all Phase 1–3 success metrics.

### A.7 Guardrails & Fallback Matrix (Abridged)

| Failure Mode | Primary | Fallback |
| :--- | :--- | :--- |
| OCC version conflict | Re-read both versions; surface to user | Never auto-merge prose |
| Context pressure | `context_offloader.py` tiered eviction (Tier 1 → 2 → 3) | Core Identity + action queue never dropped |
| Planner API unavailable | Detect at CLASSIFY | `DEGRADED_MODE` static prompt + telemetry event |
| Idempotency file unavailable | PREFLIGHT open failure | Block all write actions; deliver read-only briefing |
| Wave 0 gate open | `GateBlockedError` in TrustEnforcer | L2 elevation hard-blocked; override via `--force-wave0 --justification` only |
| Action validator PII detected | `pii_guard` payload scan | Halt; never re-invoke LLM to fix |
| Worker tool boundary violation | Orchestrator audit assertion | Discard output; mark domain STALE; log `TOOL_BOUNDARY_VIOLATION` |
| Checkpoint on startup | PREFLIGHT detects `state/checkpoint.json` | Offer resume or restart; prune checkpoint after successful resume |
| PENDING idempotency key on startup | PREFLIGHT key file scan | Surface to user; require explicit resolution before new actions |

*"The entire application is a well-written instruction file. The data layer lives where the user lives — always fresh, always accessible, always encrypted where it matters. Nothing sensitive leaves the device. Three LLMs work together — the right model for the right task at the right cost. Now it learns your patterns, guards your data, and shows you what matters before you ask."*
