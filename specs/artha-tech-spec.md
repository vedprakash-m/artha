# Artha — Technical Specification

> **Version**: 3.9.6 | **Status**: Active Development | **Date**: March 2026
> **Author**: [Author] | **Classification**: Personal & Confidential
> **Implements**: PRD v7.0.6

> **⚠ Note on Example Data:** Personal names (Raj, Priya, Arjun, Ananya)
> and other identifiers in examples throughout this document are **fictional**.
> Your actual family data is configured in `config/user_profile.yaml`.

| Version | Date | Summary |
|---------|------|----------|
| v3.9.5 | 2026-03 | Utilization uplift (specs/util.md U-1–U-9): `RelationshipPulseSkill` (`scripts/skills/relationship_pulse.py`) reads circle YAML frontmatter from `state/contacts.md`, computes days-since-contact per cadence, surfaces top-10 stale contacts sorted by `overdue_by`; `OccasionTrackerSkill` (`scripts/skills/occasion_tracker.py`) parses birthday/festival/anniversary/holiday tables, `_this_year_occurrence()` handles Feb-29 leap-year edge-case, generates WhatsApp greeting suggestions; `BillDueTrackerSkill` (`scripts/skills/bill_due_tracker.py`) `_parse_bill_date()` handles ISO + `Month Day, Year` + `Monthly (Nth)` + `Semi-annual (Mo & Mo)` formats; `CreditMonitorSkill` (`scripts/skills/credit_monitor.py`) 4 regex patterns, dedup by excerpt-key, graceful degradation; `SchoolCalendarSkill` (`scripts/skills/school_calendar.py`) `_SCHOOL_KEYWORDS` regex, `_GRADE_PATTERN`, dedup by `(date, event[:40])` key; `config/skills.yaml` 13 total; `config/connectors.yaml` `prefer_mcp: false` on all 8 personal connectors; `config/Artha.core.md` Step 21 non-blocking fact extraction; `tests/conftest.py` project-root sys.path fix; 27 unit tests. 1069 tests (+27). See §8.13. |
| v3.9.4 | 2026-03 |
| v3.9.4 | 2026-03 | Catch-up quality hardening: `scripts/email_classifier.py` (deterministic marketing tagger — whitelist-first, `_IMPORTANT_SENDER_DOMAINS` frozenset, `_IMPORTANT_SUBJECT_PATTERNS`, `_MARKETING_SENDER_PATTERNS`, `_MARKETING_SUBJECT_PATTERNS`, `_MARKETING_HEADERS`; tags `marketing: bool` + `marketing_category` per record; auto-wired into `pipeline.py` `_classify_email_lines()` post-fetch); `scripts/health_check_writer.py` (atomic POSIX-rename write, YAML frontmatter upsert, connector log rotation >7d to `tmp/connector_health_log.md`, vault lock guard, bootstrap stub detection); `scripts/calendar_writer.py` (`_is_calendar_record()`, `_event_dedup_key()` SHA-256 hash, `_rotate_connector_logs()`-style run into `state/calendar.md`, bootstrap stub auto-repair); `scripts/migrate_oi.py` (`_OI_PATTERN = r"\bOI-(\d{3,})\b"`, `_is_bootstrap_stub()` candidate, idempotent backfill, dry-run, highest-ID report); `scripts/preflight.py` `_is_bootstrap_stub()` + `check_state_templates()` stubs detection + 48h advance advisory for MS Graph + expired-token refresh path fix (was skipping `secs_left < 0`); `scripts/session_summarizer.py` `_auto_extract_facts_if_catchup()` auto-triggers `fact_extractor` for catch-up commands in `get_context_card()`; `config/workflow/finalize.md` Steps 11c + 16 updated to use script-backed writes; `config/workflow/process.md` Steps 5a + 5d context_offloader instruction. 1042 tests (+27). See §8.12. |
| v3.9.3 | 2026-03 | Agentic Reloaded (specs/agentic-reloaded.md AR-1–AR-8): `scripts/session_search.py` (grep-based cross-session recall, `SearchResult` dataclass, relevance = `match_count / √file_lines`, PII-safe excerpts); `scripts/procedure_index.py` (`ProcedureMatch` dataclass, 90-day confidence decay with 0.5 floor, `find_matching_procedures()`, `format_procedures_for_context()`); `scripts/delegation.py` (`DelegationRequest/DelegationResult` dataclasses, `should_delegate()` step≥5/parallel/isolated criteria, `compose_handoff()` context compression ≤500 chars, `evaluate_for_procedure()`, `is_delegation_enabled()`); `scripts/fact_extractor.py` `MAX_MEMORY_CHARS=3000` + `MAX_FACTS_COUNT=30` constants + `_consolidate_facts()` (TTL expiry → lowest-confidence eviction, protects `correction`/`preference` types) + `_load_harness_config()`; `scripts/session_summarizer.py` `should_flush_memory()` + `pre_flush_facts_persisted: int = 0` in both `SessionSummary` classes; `scripts/artha_context.py` `session_recall_available: bool = False`; `scripts/backup.py` `learned_procedures/` dynamic registry via `state/learned_procedures/*.md` scan; `scripts/generate_identity.py` `PROMPT STABILITY` frozen-layer comment; `scripts/audit_compliance.py` `_check_memory_capacity()` (weight 5, advisory-pass when absent) + `_check_prompt_stability()` (weight 5, advisory-pass when absent); `config/artha_config.yaml` 6 new `harness.agentic.*` flag blocks; `state/templates/self_model.md` (AR-2 schema); `state/learned_procedures/README.md`; `config/Artha.core.md` 5 new protocol sections; `config/workflow/finalize.md` Step 11c AR-1/2/5 instructions; `config/workflow/reason.md` Pre-OODA recall (AR-4) + procedure lookup (AR-5); `config/workflow/fetch.md` AR-8 connector root-cause protocol. 1015 tests (+72). See §8.11. |
| v3.9.2 | 2026-03 | Operational safety hardening: `vault.py do_health()` 3-exit-code model (0=clean, 1=hard fail, 2=soft warnings); `preflight.check_vault_health()` exit-2 path (P1, not P0) with correct `.bak` warning extraction + `python3` fix hint; `preflight.check_vault_lock()` unconditional stale-lock auto-clear + PID liveness check + actual lock path in errors; `detect_environment.detect_json()` TTY-aware compact/pretty output + `--pretty` CLI flag; `google_calendar._parse_event()` attendee email PII redaction; `config/Artha.core.md` + `config/Artha.md` `python3` consistency + Step 0 hard-vs-soft P0 distinction + `alias python=python3` note. 943 tests (+7). |
| v3.9.1 | 2026-03 | Patch: `_ComposedMiddleware.before_write` now accepts and forwards `ctx: Any | None = None` to all child middlewares. Test mocks updated to match Protocol contract. |
| v3.9 | 2026-03 | Agentic Intelligence (PRD F15.128–F15.132, specs/agentic-improve.md Phases 1–5): `scripts/artha_context.py` (ArthaContext Pydantic model, ContextPressure, build_context()); `scripts/checkpoint.py` (step checkpoints, 4h TTL, read/write/clear); `scripts/fact_extractor.py` (5 signal detectors, PII strip, SHA-256 dedup, memory.md v2.0 persist); `context_offloader.py` EvictionTier enum + `_ARTIFACT_TIERS`; `audit_compliance.py` `_check_ooda_protocol()` (weight=10); `middleware/__init__.py` `ctx` param; `config/workflow/` Step 0a + Step 11c + checkpoint writes at Steps 4/7/8; `config/artha_config.yaml` `harness.agentic:` 4 flags; `state/memory.md` v2.0 schema; `state/templates/memory.md` template. 936 tests (+120). See §8.10. |
| v3.7 | 2026-03 | Cowork VM & Operational Hardening (PRD F15.119–F15.123, specs/vm-hardening.md): `scripts/detect_environment.py` (7-probe manifest, 5-min TTL cache); `scripts/audit_compliance.py` (7-check compliance scorer, `--threshold`); `scripts/preflight.py` `--advisory` flag + `check_profile_completeness()` + 3-layer `check_msgraph_token()` rewrite; `scripts/setup_msgraph_oauth.py` `_last_refresh_success` tracking; `scripts/generate_identity.py` compact mode + `--no-compact`; 5 `config/workflow/*.md` files rewritten with canonical steps + ⛩️ gates; `state/templates/health-check.md`; `config/Artha.core.md` Read-Only Environment Protocol; 804 tests (+106). |
| v3.6 | 2026-03 | Deep Agents Option B — Core Harness Patterns (Phases 1–5, PRD F15.114–F15.118): `scripts/context_offloader.py`, `scripts/domain_index.py`, `scripts/session_summarizer.py`, `scripts/middleware/` (5 modules), `scripts/schemas/` (4 modules). `config/Artha.md`/`config/Artha.core.md` Steps 4b′/5/7/8h/11b/Session Protocol/harness_metrics/18a′. `config/artha_config.yaml` `harness:` namespace. `pydantic>=2.0.0` in requirements. 698 tests (+157). See §9.5. |
| v3.5 | 2026-03 | Intelligence expansion + platform parity (PRD F15.100–113): `financial_resilience` skill (burn rate/runway), gig income routing keywords, purchase interval observation, structured contact profiles, pre-meeting context injection, passive fact extraction, digital estate inventory, instruction-sheet actions, subscription action proposals, `setup.ps1` Windows parity, `artha.py --doctor` 11-point diagnostic, `apple_health` connector (iterparse/ZIP), longitudinal lab tracking; `passport_expiry` + `subscription_monitor` added to `_ALLOWED_SKILLS`; 541 tests |
| v3.4 | 2026-03 | OOBE polish audit (PRD F15.95–99): setup.sh brand mark + step counters, AI CLI auto-detection (`_detect_ai_clis()`/`_print_ai_cli_status()`), colorized demo briefing (yellow/green/red ANSI), README 624→142 lines, docs/backup.md, specs/README.md, make start; 485 tests |
| v3.3 | 2026-03 | Interactive setup wizard + first-run friction fixes (PRD F15.89–94): `artha.py` wizard, starter profile, no auto-preflight, advisory warnings, `--first-run` preflight, `setup.sh` wizard prompt; 485 tests |
| v3.2 | 2026-03 | 10-layer defense-in-depth (§8.5.1): advisory lock, sync fence, post-encrypt verify, deferred deletion, lockdown, mtime guard, net-negative override, prune protection, confirm gate, key health; 501 tests |
| v3.0 | 2026-03 | Novice UX hardening (PRD F15.72–F15.77): Step 6 restored to README, age key deletion order fixed, `<details>` OS blocks, Node.js prereq, System keyring prereq, `check_keyring_backend()` P0 preflight gate, `open_items.md` template + `--fix` auto-create, `_rel()` path masker for all preflight console output, example profile PII neutralized (King County WA → Springfield IL), demo footer fixed, Google OAuth deep-link doc, catchup alias note |
| v2.9 | 2026-03 | Distribution audit: 15-issue hardening — git history PII purge, connector defaults (Gmail+GCal only), jsonschema dedup, Python >=3.11 enforced, token path corrected (.tokens/ not ~/.artha-tokens/), settings.md legacy code removed, pre-commit hook activation documented, registry.md sanitized |
| v2.8 | 2026-03 | Phase 1b: domain registry, household types, renter mode, pets, passport/subscription skills, RSS connector, offline/degraded mode, performance telemetry, 4 view scripts (status/goals/items/scorecard), migrate_state.py DSL |
| v2.7 | 2026-03 | ACB v2.1: Multi-LLM Q&A, ensemble mode, HCI command redesign, write commands, /diff, /goals |
| v2.6 | 2026-03 | Three-module architecture: `foundation.py` + `backup.py` extracted from `vault.py`; `_config` dict pattern for test isolation; `backup.py` standalone CLI |
| v2.5 | 2026-03 | ZIP-per-snapshot backup architecture — root-level `backups/` dir, one ZIP per GFS tier-day, `vault.py install` command |
| v2.4 | 2026-03 | Comprehensive Backup Registry (§8.5.2) — all 31 state files + config files, fresh-install restore |
| v2.3 | 2026-03 | GFS Vault Backup (§8.5.2) — daily/weekly/monthly/yearly rotation with restore validation |
| v2.3 | 2026-03 | Channel Bridge (ACB v2.0): `scripts/channels/`, `channel_push.py`, `channel_listener.py` |
| v2.2 | 2026-03 | WorkIQ Calendar MCP, work calendar state schema |
| v2.1 | 2026-03 | Intelligence amplification (29 enhancements), Canvas LMS, `/diff` |
| v2.0 | 2026-02 | Supercharge: data integrity guard, bootstrap, coaching, dashboard |
| v1.9 | 2026-02 | Phase 2A: relationship graph, decisions, scenarios, tiered context |
| v1.8 | 2026-01 | MS Graph direct integration (email + calendar) |
| v1.7 | 2026-01 | Pre-flight gate, open items, To Do sync, email coverage |
| v1.6 | 2025-12 | Critical assessment hardening, safe_cli, contacts encryption |
| v1.5 | 2025-12 | Multi-LLM orchestration, action framework |
| v1.4 | 2025-11 | Governance framework |
| v1.3 | 2025-11 | PII guardrails, Claude Code capabilities |
| v1.2 | 2025-10 | OneDrive sync layer, encrypted state |

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
| View scripts | `~/OneDrive/Artha/scripts/*_view.py` | Script-backed deterministic renderers: `dashboard_view.py`, `domain_view.py`, `status_view.py`, `goals_view.py`, `items_view.py`, `scorecard_view.py`, `diff_view.py` |
| Migration scripts | `~/OneDrive/Artha/scripts/migrate_state.py` | YAML front-matter schema migration DSL for state files |
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

```markdown
# Artha — Personal Intelligence System

## Identity
You are Artha, a personal intelligence system for the family (defined in user_profile.yaml).
You serve the primary user and their household.
You run as an AI CLI session on the primary user's computer. You are not a chatbot —
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
4. Run `python scripts/pii_guard.py filter` on email batch (Layer 1 PII defense)
   ⚠ HALT if pii_guard.py exits non-zero
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
| County Tax | Public Lookup | None | `requests` | Property tax deadlines (Phase 1) |
| Canvas LMS | REST API | Developer Token | `requests` | School grades/assignments (Phase 2 Blocked) |
| OFX / FDX | Banking API | FI Credentials | `ofxtools` | Direct bank balance pull (Phase 2) |
| Microsoft Graph | REST API | OAuth2 | `msal` | Outlook Email + MS To Do sync |
| Home Assistant | Local API | LAN Token | `requests` | Smart home status (Phase 2) |
| Passport Expiry | `state/immigration.md` | vault-decrypted | stdlib | Alert at 180/90/60 days (Phase 1 — F15.66) |
| Subscription Monitor | `state/digital.md` | none | stdlib | Price change + trial-to-paid detection (Phase 1 — F15.67) |
| RSS Feeds | Public RSS/Atom URLs | None | `urllib` + `xml.etree` | Regulatory/news feeds (USCIS, etc.) — disabled by default (Phase 1 — F15.68) |
| Financial Resilience | `state/finance.md` | vault-decrypted | stdlib | Burn rate, emergency runway, single-income stress (Phase 1 — F15.100) |
| Apple Health | Local ZIP/XML export | None (local only) | `xml.etree.ElementTree.iterparse` | 16 HK quantity types, memory-efficient streaming parse, opt-in (Phase 1 — F15.111) |

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

### 4.13 Work Calendar State (`~/OneDrive/Artha/state/work-calendar.md`) *(v2.2)*

Metadata-only — no meeting titles, attendees, or bodies. Sections: Last Fetch (platform, version, event count, minutes), Weekly Density (rolling 13-week, per-day count+minutes, conflict count), Conflict History (count-only, no details). Updated only from Windows catch-ups; Mac catch-ups leave unchanged. Stale check: if last_fetch >12h and different platform, show metadata summary; if >12h, ignore.

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
- 3:30 PM — Arjun orthodontist appointment
- 6:00 PM — Ananya soccer practice pickup

## 📬 By Domain

### Immigration
- [immigration attorney] email: H-1B extension paperwork timeline confirmed for Q2

### Kids
- Arjun: AP Language essay returned (B+), cumulative grade now B+
- Ananya: Math quiz 92%, no action needed
- ParentSquare: Spring pictures March 12 (both schools)

### Finance
- Chase statement: February spending $X,XXX (within budget)
- Wells Fargo: Mortgage payment processed

### Home
- City Waste Services: Schedule change for next week (holiday)

## 🤝 Relationship Pulse *(v1.9)*
- Reconnect needed: Suresh Uncle (45 days, threshold 30)
- Upcoming: Rahul’s birthday in 5 days → [action proposal queued]
- Cultural: Holi celebration Mar 14 (temple community)

## 🎯 Goal Pulse
| Goal | Status | Trend | Leading Indicator *(v1.9)* |
|---|---|---|---|
| Net worth trajectory | On track | ↑ +2.1% YTD | Savings rate 18% (target 20%) |
| Immigration readiness | ⚠️ Action needed | EAD renewal due | [immigration attorney] response time: 3 days avg |
| Arjun GPA | On track | Stable at 3.7 | Assignment completion 95% ✔ |

## 💡 ONE THING
Your EAD renewal is 90 days out. Based on [immigration attorney]'s average processing time
from your last two renewals (45 days), initiate attorney contact within 2 weeks
to stay on the safe side.

## 📅 Week Ahead *(v2.1 — Monday only)*
> Shown only on Monday catch-ups. Previews the week's calendar, upcoming deadlines,
> and goal milestones to enable proactive planning.

| Day | Key Events | Deadlines |
|---|---|---|
| Mon | Team standup, Arjun ortho 3:30 PM | PSE bill due Wed |
| Tue | (clear) | |
| Wed | Ananya parent-teacher 4 PM | |
| Thu | Arjun SAT prep class 6 PM | |
| Fri | Family dinner 7 PM | College app milestone: rec letters |
| Sat–Sun | Soccer tournament (Ananya) | |

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
- Arjun: AP Language essay returned (B+), cumulative grade now B+
- Ananya: Math quiz 92%, no action needed
- ParentSquare: Spring pictures March 12 (both schools)
```

The terminal output during the Mac session shows full detail for all domains regardless of sensitivity. This ensures sensitive financial and immigration data is never in transit via email.

#### 5.1.1 Digest Mode *(v1.9)*

Triggered when >48h gap since last catch-up. Groups by day (not domain), shows Critical/Urgent items individually, counts FYI items, consolidates and deduplicates action items across gap period. Standard sections (Goal Pulse, ONE THING) appear after gap summary.

#### 5.1.2 Flash Briefing *(v2.0)*

Triggered by "quick update", `/catch-up flash`, or <4h since last run. Max 8 lines: 🔴/🟠 alerts only, today's calendar (one line), "IF YOU DON'T" consequence line, footer (goal count + signal ratio + volume).

**Compression levels:** Flash (≤30s, 8 lines), Standard (2-3 min, 40-60 lines), Deep (5-8 min, 80-120 lines with cross-domain analysis).

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

### 7.4 Action Execution Framework

**Action Proposal Schema:** Every action displays: Type, Recipient, Channel, Content Preview, Trust Required, Sensitivity, Friction level. Options: Approve / Modify / Reject.

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

**Autonomy Floor (never auto-executed):** Communications sent on your behalf, financial transactions, immigration actions, actions affecting others' data.

**Email Composition:** OAuth `gmail.send` scope → Claude drafts → terminal preview → user approves → MCP sends → audit.md logs. Group emails read from `contacts.md`.

**WhatsApp:** URL scheme opens WhatsApp with pre-filled text; user must tap Send. Cannot confirm delivery or attach images programmatically.

**Visual Messaging:** Gemini Imagen generates visual → saved to `visuals/` → composed with email/WhatsApp actions → user approves. Occasions configured in `occasions.md`.

**Lifecycle:** PROPOSE → REVIEW → EXECUTE → LOG. All actions (approved, modified, rejected) logged to audit.md.

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

### 8.9 Data Fidelity Skills *(v4.0)*

Targeted Python scripts that pull high-fidelity data from institutional portals or official APIs.

**1. Skill Runner Orchestrator (`scripts/skill_runner.py`)**
- **Venv Bootstrap:** Calls `reexec_in_venv()` from `_bootstrap.py` before third-party imports, ensuring correct venv when invoked directly from CLI agents (Gemini, Claude, shell scripts). *(v3.8 — F15.124)*
- **Entrypoint:** `if __name__ == "__main__": main()` — script is directly executable. *(v3.8 — F15.124)*
- **Discovery:** Discover active skills from `config/skills.yaml`.
- **Parallelism:** Execute each skill in parallel using `ThreadPoolExecutor`.
- **Dynamic Loader:** Use `importlib` (module-level import including `importlib.util`) to load modules from `scripts/skills/`. If a module is missing, log a warning and continue. *(v3.8 — importlib.util scope fix)*
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
- **NOAA Weather:** Two-step call (`/points/{lat},{lon}` → extract forecast URL). Coordinates read from `user_profile.yaml` (location.lat, location.lon). Requires `owner_email` from `config/artha_config.yaml` in User-Agent. `get_skill()` raises `ValueError` when `lat==lon==0.0` (placeholder defaults) to surface misconfiguration early instead of silently fetching a 404. *(v3.8 — F15.126)*
- **USCIS Status:** Checks `https://egov.uscis.gov/csol-api/case-statuses/{receipt}`. HTTP 403 returns `{"blocked": True, "error": "...IP-blocked...check at egov.uscis.gov"}` — distinguishable from transient failures. Other non-200 responses truncate `response.text` to 500 chars. *(v3.8 — F15.127)*
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

CLI: `python3 scripts/health_check_writer.py --last-catch-up ISO --email-count N --domains-processed a,b,c --mode normal|degraded|offline|read-only`

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
briefing_email: raj.patel@example.com
alert_email: raj.patel@example.com
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
- Primary Gmail (configured in user_profile.yaml — Gmail API)
- Outlook email (configured in user_profile.yaml — MS Graph API, no forwarding needed)
- iCloud (forwarding to Gmail — T-1B.1.2, Apple has no public API)
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
| pii_guard.py | pre-flight PII filter (cross-platform Python) | 1.0.0 | ~460 | Phase 1A | N/A — permanent |

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
- **Test count:** 698 passed, 5 skipped, 20 xfailed (post v3.6 Deep Agents harness implementation).
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

*Artha Tech Spec v3.5 — End of Document*

---

## 18. Revision History

| Version | Changes |
|---------|---------|
| v3.6 | Deep Agents Option B — Core Harness Patterns (PRD F15.114–F15.118): `scripts/context_offloader.py` (`offload_artifact`, builtin summary fns, `OFFLOADED_FILES`/`OFFLOADED_GLOB_PATTERNS`); `scripts/domain_index.py` (`build_domain_index`, `get_prompt_load_list`, `_domain_status` ACTIVE/STALE/ARCHIVE); `scripts/session_summarizer.py` (`SessionSummary` Pydantic v2 + dataclass fallback, `estimate_context_pct`, `should_summarize_now`, `get_context_card`); `scripts/middleware/` package — `StateMiddleware` Protocol + `compose_middleware()`, `PiiMiddleware`, `WriteGuardMiddleware`, `WriteVerifyMiddleware`, `AuditMiddleware`, `RateLimiterMiddleware`; `scripts/schemas/` package — `BriefingOutput`, `AlertItem`, `DomainSummary`, `FlashBriefingOutput`, `SessionSummarySchema`, `DomainIndexCard`. `config/Artha.md` + `config/Artha.core.md` Steps 4b′/5/7/8h/11b/Session Protocol/harness_metrics/18a′. `config/artha_config.yaml` `harness:` namespace. `pydantic>=2.0.0` in requirements.txt. 698 tests (+157 from 541). |
| v3.5 | Intelligence expansion + platform parity (PRD F15.100–113): `scripts/skills/financial_resilience.py` (`FinancialResilienceSkill` — burn rate, emergency runway, single-income stress; regex parsers for `state/finance.md`; cadence weekly, requires_vault); gig income routing keywords added to `domain_registry.yaml` (Stripe, PayPal, Venmo, Upwork, Fiverr, Etsy, DoorDash, Uber, 1099-K/NEC); `prompts/shopping.md` purchase interval observation; `prompts/social.md` structured contact profiles (9-field) + pre-meeting context injection + passive fact extraction; `prompts/estate.md` digital estate inventory (5 tables); `config/actions.yaml` `cancel_subscription` + `dispute_charge` instruction-sheet actions; `prompts/digital.md` subscription action proposals; `setup.ps1` Windows onboarding parity script; `artha.py --doctor` 11-point diagnostic (`do_doctor()`); `scripts/connectors/apple_health.py` (iterparse streaming, 16 HK types, ZIP+XML, opt-in); `prompts/health.md` longitudinal lab results; `passport_expiry` + `subscription_monitor` added to `_ALLOWED_SKILLS` frozenset. 541 tests (+56 from 485 baseline). |
| v3.4 | OOBE polish audit (PRD F15.95–99): `setup.sh` brand mark + `[1/4]`–`[4/4]` step counters + `--disable-pip-version-check`; `artha.py` `_detect_ai_clis()` + `_print_ai_cli_status()` for tailored post-wizard / welcome next-step; `demo_catchup.py` ANSI colorized output (yellow ACTION, green good, red alert), removed dead footer; `README.md` 624→142 lines + `docs/backup.md` + `specs/README.md` disclaimer; `Makefile` `start` target. 485 tests. |
| v3.3 | Interactive setup wizard + first-run friction fixes: `artha.py` wizard (`do_setup()`), starter profile, no auto-preflight on welcome, `_collect_warnings()` + `_print_validate_summary()` in `generate_identity.py`, `--first-run` preflight mode, `setup.sh` wizard prompt. See §11.4 |
| v3.2 | 10-layer defense-in-depth (§8.5.1): advisory lock, sync fence, post-encrypt verify, deferred deletion, lockdown, mtime guard, net-negative override, prune protection, confirm gate, key health; 501 tests |
| v3.0 | Novice UX hardening: Step 6 restored, age key deletion order fixed, keyring check, open_items template, path PII masking, Node.js prereq, OS blocks |
| v2.9 | Clone-audit hardening (#1–#30): PII scrub in all spec/doc files; vault store-key command; state/templates/ directory with 18 starter files; user_profile.example.yaml extended to 24 domains; preflight P1 enforcement; plist placeholder; PII guard; requirements.txt reorganised; CHANGELOG.md; 429-test suite with xfail markers |
| v2.1 | Initial public release — `/diff`, Weekend Planner, Canvas LMS API Fetch, Apple Health XML Import |

*"The entire application is a well-written instruction file. The data layer lives where the user lives — always fresh, always accessible, always encrypted where it matters. Nothing sensitive leaves the device. Three LLMs work together — the right model for the right task at the right cost. Now it learns your patterns, guards your data, and shows you what matters before you ask."*
