# ACT Reloaded — Artha Enhancement Implementation Specification

**Codename:** ACT-RELOADED  
**Version:** 1.3.0  
**Date:** 2026-03-21  
**Author:** Lead Principal System Architect  
**Status:** DRAFT — ARCHITECTURE REVIEW INCORPORATED  
**Depends on:** ACT v1.3.0, PRD v7.0.6, Tech Spec v3.9.7, UX Spec v2.7.1  
**Baseline:** 1,243 tests passing, 18 core modules, 8 action handlers, 13 domains, 15 skills

---

## §0 — Executive Summary

### Thesis

Artha v1.x is a mature **read–reason–act** system with encrypted state, trust-enforced
execution, and multi-channel UX. This spec evolves Artha into a **sense–reason–act–learn**
system by adding six capabilities the current architecture lacks:

1. **Deterministic signal extraction from email content** (bridging the gap between
   domain routing and the action layer)
2. **Async knowledge capture** (Telegram → persistent inbox → triage)
3. **Proactive time-aware nudging** (between-session push without catch-up)
4. **Deterministic cross-domain pattern detection** (codify Step 8 prose into engine)
5. **Adaptive briefing learning** (feedback loop from user behavior)
6. **Subscription lifecycle management** (full lifecycle beyond price-change detection)

Plus four targeted improvements:
7. **Household-scoped shared view** (aggregated family dashboard)
8. **Session-level cost telemetry** (token/API spend tracking)
9. **Document signal extraction** (attachment metadata → domain routing)
10. **WhatsApp bidirectional messaging** (Cloud API Phase 2)

Plus six **activation enhancements** — existing infrastructure that is built but
non-functional due to missing wiring, empty state, or absent view scripts:
11. **Memory & self-model activation** (wire fact_extractor into pipeline; create self-model writer)
12. **Decision & scenario lifecycle** (capture logic + lifecycle engine for existing stubs)
13. **Relationship state & command** (create missing state file; implement registered skill)
14. **Power half hour view** (create backing script for registered /power command)
15. **Monthly retrospective generator** (create generation code for existing Step 3 trigger)
16. **Coaching nudge automation** (deterministic engine + move from Step 19 to Step 8)

### Why These Sixteen (and Not Others)

Each enhancement was selected against three filters:

| Filter | Question |
|--------|----------|
| **Architecture Fit** | Does it extend existing patterns (skills, signals, actions, channels) without introducing new architectural paradigms? |
| **Privacy Compliance** | Does it maintain Artha's five privacy layers (PII guard, semantic redaction, vault encryption, safe_cli, audit trail)? |
| **Value Density** | Does it address a use case reported by ≥100 users in the OpenClaw community or map to a recurring user friction in Artha briefings? |

Enhancements 11–16 were added based on a gap analysis that identified built-but-unused
infrastructure. These passed the same three filters — they extend existing patterns
(activation, not invention), maintain all privacy layers, and address the highest-ROI
user frictions (context loss across sessions, broken commands, empty state files).

Rejected candidates (from prior analysis):
- RAG/semantic memory → State files fit in 200K context window; complexity not justified.
  Note: Enhancement 11 activates the *existing* fact_extractor — this is not RAG.
- Meeting transcription → Already handled by WorkIQ bridge
- Voice memo pipeline → Already possible via Telegram voice messages
- Apple Health live sync → No public API; offline ZIP parser exists in connectors/apple_health.py
  and is opt-in via `connectors.yaml: enabled: true`. Not included here as it works already.

### Design Principles (inherited from ACT v1.3.0, extended)

1. **Propose, never presume** — extended to cover proactive nudges and pattern alerts
2. **Deterministic before heuristic** — new engines use regex/rule-based extraction first; LLM only for ambiguous cases
3. **Fail-open for reads, fail-closed for writes** — unchanged
4. **The queue is the product** — extended to include inbox items and nudge queue
5. **Privacy is load-bearing** — all new modules pass through PII guard; all new state encrypted per domain sensitivity
6. **Incremental delivery** — Strangler Fig pattern; each enhancement ships independently; no Big Bang

---

## §1 — AS-IS Architecture (Discovery Summary)

### 1.1 Architecture Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│  DATA SOURCES                                                          │
│  Gmail · Outlook · iCloud · Google Cal · Canvas LMS · WorkIQ · ADO    │
└────────────────────┬────────────────────────────────────────────────────┘
                     │ JSONL (pipeline.py, ThreadPoolExecutor×8)
                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PROCESS LAYER                                                         │
│  email_classifier.py → routing.yaml → domain prompts → state files    │
│  pii_guard.py (Layer 1) → semantic redaction (Layer 2)                │
│  skill_runner.py (15 skills, parallel×5)                              │
└────────────────────┬────────────────────────────────────────────────────┘
                     │ DomainSignal objects (from skills only)
                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ACTION LAYER (ACT v1.3.0)                                             │
│  ActionComposer → ActionQueue (SQLite) → TrustEnforcer → Executor     │
│  18 signal types · 8 handlers · 10-state machine · age encryption     │
└────────────────────┬────────────────────────────────────────────────────┘
                     │ ActionProposal (pending approval)
                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  CHANNEL LAYER                                                         │
│  Terminal (primary) · channel_push.py (Telegram L1) ·                 │
│  channel_listener.py (Telegram L2, 17 commands, 10-layer security)    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Verified Pain Points

| # | Pain Point | Evidence | Current Workaround |
|---|-----------|----------|-------------------|
| 1 | **Email signals don't reach action layer** | `action_composer.py` line 7: "Signals come from deterministic skills, never from LLM inference on raw email text" | AI manually proposes actions during Step 8 reasoning; not deterministic; not queued |
| 2 | **No safe async knowledge capture** | `channel_listener.py` has no `/remember` or `/note` command, while existing write-capable commands (`/items_add`, `/items_done`) bypass middleware entirely | User must wait for next catch-up session to share information; existing channel writes are not reusable as a safe pattern |
| 3 | **No proactive nudges** | `channel_push.py` fires ONLY at Step 20 post-catch-up; no cron/scheduled push | User must run catch-up to receive any notification |
| 4 | **Cross-domain patterns are prompt-based** | No `pattern_engine.py`; Step 8 rules are prose in `config/Artha.md` | AI applies rules during reasoning; inconsistent across sessions |
| 5 | **Briefing format is static** | Step 2b: `hours_elapsed`-based rules only; no feedback loop | User manually overrides with `/catch-up flash` etc. |
| 6 | **Subscription tracking is passive** | `subscription_monitor.py`: state-file-only; no external API; detects price changes post-hoc | User discovers price increase only after billing |
| 7 | **No family aggregation** | Scope filtering is per-recipient subtraction; no multi-perspective merge | Ved sees everything; Archana sees filtered subset; no shared view |
| 8 | **No cost tracking** | `/cost` command listed in domain_index.py but no implementation script | User has no visibility into API spend |
| 9 | **Attachments ignored** | Email processing truncates to 1500 tokens; no PDF/attachment metadata extraction | Invoices, confirmations, documents lost in email noise |
| 10 | **WhatsApp is read-only** | `whatsapp_local.py` reads ChatStorage.sqlite; `whatsapp_send.py` uses URL scheme only | User must manually open WhatsApp to send messages |
| 11 | **Memory never populated** | `state/memory.md` has `facts: []`; `fact_extractor.py` exists but never invoked | Artha re-learns every session; corrections/preferences lost |
| 12 | **Self-model never updated** | `state/self_model.md` is empty template; Step 11c never reached | Artha cannot adapt to user patterns or track blind spots |
| 13 | **Decisions/scenarios are stubs** | `state/decisions.md` and `state/scenarios.md` have empty tables; Steps 3/8d/8e reference them | Active decisions (insurance, lease, tax) go untracked |
| 14 | **Relationships state missing** | `state/relationships.md` does not exist; `/relationships` command broken; skill registered but unimplemented | No relationship health tracking despite command and skill registry |
| 15 | **Power half hour has no script** | `/power` registered in `domain_index.py`; UX spec §10.15 defines format; no `power_half_hour_view.py` | Command registered but returns nothing |
| 16 | **Monthly retro never generates** | Step 3 flags `generate_monthly_retro`; no generation code; no retro files in `summaries/` | Monthly review never produced despite trigger being wired |
| 17 | **Coaching nudges rarely fire** | Step 19b spec is thorough; execution is inline AI logic at a step rarely reached | Coaching framework exists but session context exhaustion prevents it |

### 1.3 What We Keep (Zero Regression Contract)

Every component listed below is **load-bearing infrastructure**. The enhancements
integrate with these; never replace them.

- **Middleware stack** (`scripts/middleware/`): PII → WriteGuard → Verify → Audit → RateLimiter
- **Action Layer** (ACT v1.3.0): ActionQueue (SQLite), ActionExecutor, TrustEnforcer, ActionComposer
- **Pipeline** (`scripts/pipeline.py`): 13-connector ThreadPoolExecutor orchestrator
- **Preflight** (`scripts/preflight.py`): 24+ P0/P1 health checks; remains load-bearing, but this spec extends it into a lightweight validation platform via module-registered checks
- **PII Guard** (`scripts/pii_guard.py`): 20+ regex patterns, scan/filter modes
- **Channel Bridge**: `channel_push.py` (Layer 1) + `channel_listener.py` (Layer 2, 10-layer security)
- **Skill Runner** (`scripts/skill_runner.py`): cadence-controlled parallel execution
- **Fact Extractor** (`scripts/fact_extractor.py`): 6 fact types, bounded memory (30 facts, 3K chars)
- **Vault** (`scripts/vault.py`): age encryption at rest
- **Checkpoint** (`scripts/checkpoint.py`): step-level crash recovery

---

## §2 — Enhancement Assessments

### Enhancement 1: Email Signal Extractor

**Problem Statement:** The action layer receives signals exclusively from deterministic
skills. Emails containing actionable content (RSVP deadlines, appointment confirmations,
payment notices, form submission deadlines) are processed by the AI during Step 7 domain
extraction but never emit `DomainSignal` objects. This means the ActionComposer never
sees these signals, and no ActionProposals are queued automatically.

**Architecture Fit:** HIGH — This is a new skill-like module that emits `DomainSignal`
objects into the existing `ActionComposer.compose()` pipeline. No new architectural
patterns required.

**Current State:**
- Step 5: email_classifier.py tags marketing vs. non-marketing (7-step deterministic logic)
- Step 6: routing.yaml routes emails to domains by sender/subject patterns
- Step 7: AI reads domain prompts and extracts structured data to state files
- **GAP:** Between Step 6 (routing) and Step 7 (AI extraction), no deterministic
  signal extraction occurs. The AI may propose actions during Step 8, but these
  are not queued via ActionComposer.

**Proposed Solution:**

New module: `scripts/email_signal_extractor.py`

Runs at **Step 6.5** (after routing, before domain processing). Operates on the
same JSONL email records that Step 6 routes to domains. Uses regex/keyword patterns
(same philosophy as email_classifier.py) to detect actionable signals.

**Signal Detection Rules (deterministic, no LLM):**

| Pattern Category | Regex/Keyword | Emitted Signal | Domain |
|-----------------|---------------|----------------|--------|
| RSVP deadline | `RSVP by \w+ \d{1,2}`, `please respond by`, `deadline to reply` | `event_rsvp_needed` | calendar |
| Appointment confirmation | `appointment confirmed`, `scheduled for`, `your visit on` | `appointment_confirmed` | health, calendar |
| Payment notice | `payment due`, `amount due`, `balance of \$[\d,]+`, `pay by` | `bill_due` | finance |
| Form deadline | `submit by`, `form due`, `application deadline`, `filing deadline` | `form_deadline` | depends on routing |
| Shipment arrival | `out for delivery`, `delivered to`, `arriving today` | `delivery_arriving` | shopping |
| Account security | `unusual sign-in`, `password reset`, `security alert` | `security_alert` | digital |
| Renewal notice | `renewal on`, `subscription renewing`, `auto-renew` | `subscription_renewal` | finance, digital |
| School action needed | `action required`, `parent signature`, `field trip permission` | `school_action_needed` | kids |

**Interface Contract:**
```python
# scripts/email_signal_extractor.py
class EmailSignalExtractor:
    def extract(self, email_records: list[dict], routing_table: dict) -> list[DomainSignal]:
        """Deterministic regex scan of routed emails → DomainSignal objects.
        
        Each signal includes:
          - source: "email_signal_extractor"
          - domain: from routing table
          - signal_type: from pattern match
          - entity: email subject + sender (truncated)
          - urgency: inferred from deadline proximity
          - metadata: {email_id, deadline_date, amount, sender}
        
        PII: sender email addresses are NOT included in signal metadata.
              Only first-name or organization name extracted.
        """
```

**Integration Points:**
1. Called by the AI during Step 6.5 or programmatically by pipeline.py
2. Emitted signals fed to `ActionComposer.compose()` in Step 8
3. Resulting ActionProposals queued in ActionQueue (SQLite)
4. User approves via Terminal or Telegram inline buttons

**New `_SIGNAL_ROUTING` entries required in `action_composer.py`:**
```python
"event_rsvp_needed":     {"action": "email_reply",     "friction": "standard", "min_trust": 1, ...},
"appointment_confirmed": {"action": "calendar_create",  "friction": "low",      "min_trust": 1, ...},
"delivery_arriving":     {"action": "reminder_create",  "friction": "low",      "min_trust": 0, ...},
"security_alert":        {"action": "instruction_sheet","friction": "high",     "min_trust": 1, ...},
"school_action_needed":  {"action": "email_reply",      "friction": "standard", "min_trust": 1, ...},
"form_deadline":         {"action": "instruction_sheet","friction": "high",     "min_trust": 1, ...},
```

**Privacy & Security:**
- PII guard runs on email body BEFORE signal extraction (Step 5b is upstream)
- Signal metadata contains only: signal_type, domain, deadline_date, amount (if financial), sender_org_name
- No raw email body stored in ActionQueue
- Financial signals (`bill_due`, `subscription_renewal`) get `sensitivity: "high"` → age-encrypted in SQLite

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| False positives (marketing email matches "payment due") | Medium | Low — bad ActionProposal queued but not executed without approval | Apply extractor AFTER marketing suppression (Step 5a); whitelist trusted sender domains |
| Missing signals (regex doesn't cover all patterns) | High | Medium — same as today (AI catches it in Step 8) | Start conservative; add patterns based on `health-check.md → email_signal_extractor.miss_rate` tracking |
| Signal duplication (skill AND email both emit same signal) | Medium | Low — ActionQueue dedup prevents double-enqueue | Dedup key: `(signal_type, domain, deadline_date)` with 24h window |
| Performance on high-volume email batches (500+ emails) | Low | Medium — delays Step 6.5 | Regex-only; no LLM calls; benchmark target <100ms for 500 emails |

**Estimated Scope:** ~350 LOC (extractor) + ~50 LOC (composer additions) + ~200 LOC (tests)

**Dependencies:** None new. Uses existing `DomainSignal`, `ActionComposer`, `email_classifier` output.

---

### Enhancement 2: Telegram Knowledge Capture (`/remember`)

**Problem Statement:** Between catch-up sessions, users encounter information they want
Artha to incorporate (overheard conversation, quick phone note, price seen in store).
The Channel Listener is read-only — 17 commands, all read-only. Users must wait for the
next session to share this context.

**Architecture Fit:** HIGH — Extends `channel_listener.py` with one new command and adds
`state/inbox.md` as a new state file. Follows existing command dispatch pattern.

**Current State:**
- `channel_listener.py`: 17 commands in `_COMMAND_ALIASES`, documented as read-only
- `_SESSION_TOKEN_STORE`: PIN-based session tokens (15-min expiry) exist for sensitive ops
- `/items_add` command exists — **already writes** to `state/open_items.md` via `open(oi_path, "a")`
- `/items_done` command exists — **already rewrites** `state/open_items.md` via `Path.write_text()`
- **Both bypass the middleware stack entirely** (no PII guard, no WriteGuard, no NET-NEGATIVE
  check, no write verification). Only `_audit_log()` is called.
- The documented read-only invariant (security layer 8) is **already violated** by these two commands
- Security model: 10-layer (whitelist, dedup, timestamp, rate limit, command whitelist, scope, PII, read-only, staleness, host check)

**Proposed Solution:**

New command: `/remember <text>` (aliases: `/note`, `/inbox`)

**Write Protocol (breaks read-only invariant — requires careful gating):**

```
User sends: /remember Pick up Trisha's science project materials from Staples
                      
channel_listener.py:
  1. Validate sender (existing whitelist check)
  2. Rate limit check (existing, capped at 10/min)
  3. PII scan on <text> → pii_guard.filter_text()
  4. Write to state/inbox.md (append-only, sequential INB-NNN IDs)
  5. Respond: "📥 Noted: INB-042 — Pick up Trisha's science project materials from Staples"
  6. Audit: CHANNEL_REMEMBER | sender | INB-042 | timestamp

Triage during next catch-up (Step 7b extension):
  1. Read state/inbox.md
  2. For each untriaged item:
     a. Route to domain (using existing routing.yaml keywords)
     b. Create OI-NNN in open_items.md if actionable
     c. Persist to state/memory.md if it's a fact/correction
     d. Mark triaged in inbox.md
  3. Surface in briefing: "📥 [N] inbox items triaged since last catch-up"
```

**state/inbox.md Schema:**
```yaml
---
domain: inbox
last_updated: 2026-03-20T10:30:00Z
---

## Inbox Items

- id: INB-042
  text: "Pick up Trisha's science project materials from Staples"
  source: telegram
  sender_id: "12345678"  # Telegram user ID (raw numeric; not PII per se, but correlatable)
  timestamp: 2026-03-20T10:30:00Z
  triaged: false
  routed_to: null
  created_oi: null
```

**Security Model Changes:**

| Layer | Change | Rationale |
|-------|--------|-----------|
| Command whitelist | Add `/remember`, `/note`, `/inbox` | Required for new feature |
| Read-only invariant | **Follows existing precedent** (`/items_add` already writes) | Mitigated by: append-only writes to a new file; no existing state mutation; PII scan pre-write; middleware-routed (unlike `/items_add`) |
| Rate limit | **Tighter for writes**: 5 writes/hour (vs 10 reads/min) | Prevent spam/abuse; apply same write rate limit to existing `/items_add`/`/items_done` for consistency |
| Scope filter | Only `full` scope users can use `/remember` | `family`/`standard` users cannot write state |
| Audit | New `CHANNEL_REMEMBER` audit event type | Full traceability |
| Encryption | `state/inbox.md` is NOT encrypted (standard sensitivity; no financial/immigration data) | If user sends sensitive data, PII guard redacts it |

**⚠️ PRECEDENT NOTE: The read-only invariant is already broken.**

`channel_listener.py` header states "never writes state files" as a security property,
but `/items_add` and `/items_done` **already violate this invariant**. Critically, the
existing write paths are *less secure* than what `/remember` proposes:

| Property | `/items_add` (existing) | `/remember` (proposed) |
|----------|------------------------|------------------------|
| PII guard pre-write | ❌ No | ✅ Yes |
| WriteGuard / NET-NEGATIVE | ❌ No | ✅ Yes (middleware-routed) |
| Post-write verification | ❌ No | ✅ Yes |
| Write rate limit | ❌ Only global 10/min | ✅ 5 writes/hour |
| Scope restriction | ❌ Any whitelisted user | ✅ Full-scope only |
| Audit trail | ✅ `_audit_log()` | ✅ `CHANNEL_REMEMBER` event |

`/remember` therefore follows an existing precedent with **strictly stronger safeguards**.
The documented read-only invariant should be updated to reflect reality: the listener
performs guarded writes for specific commands under audit.

**⚠️ TECH DEBT:** `/items_add` and `/items_done` should be retrofitted to route through
the middleware stack (PII guard + WriteGuard + write verification) before Wave 3 ships.
See §2.X Write Integration Contract.

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Read-only invariant precedent | Accepted | Low — follows existing `/items_add` precedent | Append-only to new file; PII guard; rate limit; scope gate; audit trail; middleware-routed (stronger than existing writes) |
| Inbox bloat (user sends too much) | Low | Low — file growth | Cap at 50 untriaged items; oldest auto-expire after 7 days |
| PII in user-composed messages | Medium | High — raw PII in state file | pii_guard.filter_text() runs on every inbound /remember text |
| Triage misrouting | Medium | Low — wrong domain assignment | Use existing routing.yaml; surface untriaged items in briefing for manual correction |

**Estimated Scope:** ~120 LOC (listener command) + ~80 LOC (inbox schema) + ~60 LOC (triage step) + ~150 LOC (tests)

**Dependencies:** None new. Uses existing `pii_guard`, `channel_listener` dispatch, YAML state file pattern.

---

### Enhancement 3: Cross-Domain Pattern Engine

**Problem Statement:** Step 8 cross-domain reasoning relies on prose rules in `config/Artha.md`
interpreted by the AI at runtime. This causes three problems:
1. **Inconsistency** — different sessions may detect different patterns
2. **Opacity** — no audit trail of which patterns fired and which didn't
3. **Extensibility** — adding new correlation rules requires editing the core prompt

**Architecture Fit:** HIGH — Replaces prose with a deterministic YAML-configured engine
that runs at Step 8, emitting signals that feed into the existing ActionComposer.

**Current State:**
- Step 8f `config/Artha.md`: 6 compound signal rules defined in prose
- Step 8o: WorkIQ cross-domain conflict detection (calendar overlap) — partially deterministic
- No `pattern_engine.py` in codebase (confirmed by search)

**Proposed Solution:**

New module: `scripts/pattern_engine.py`
New config: `config/patterns.yaml`

**Pattern Definition Schema:**

```yaml
# config/patterns.yaml
schema_version: "1.0"
patterns:
  - id: PAT-001
    name: immigration_travel_conflict
    description: "Immigration deadline within 90 days + upcoming travel"
    conditions:
      - domain: immigration
        field: "next_deadline"
        operator: "days_until_lte"
        value: 90
      - domain: travel
        field: "next_trip_start"
        operator: "exists"
    signal_type: immigration_travel_conflict
    alert_level: critical
    action_suggestion: "Review travel plans against immigration deadlines"
    enabled: true

  - id: PAT-002
    name: bill_cash_timing
    description: "Bill due + low cash balance"
    conditions:
      - domain: finance
        field: "next_bill_due_days"
        operator: "days_until_lte"
        value: 7
      - domain: finance
        field: "checking_balance"
        operator: "lt"
        threshold_field: "next_bill_amount"
    signal_type: bill_cash_timing_risk
    alert_level: urgent
    enabled: true

  - id: PAT-003
    name: school_work_conflict
    description: "School event + work calendar conflict"
    conditions:
      - domain: kids
        field: "upcoming_events"
        operator: "has_item_within_days"
        value: 7
      - domain: calendar
        field: "work_meetings"
        operator: "overlaps_with"
        ref_domain: kids
        ref_field: "upcoming_events"
    signal_type: school_work_conflict
    alert_level: urgent
    enabled: true
```

**Engine Interface:**

```python
# scripts/pattern_engine.py
class PatternEngine:
    def __init__(self, patterns_config: Path, state_dir: Path):
        """Load pattern definitions; index state file frontmatter."""
    
    def evaluate(self, state_snapshots: dict[str, dict]) -> list[PatternMatch]:
        """Evaluate all enabled patterns against current state.
        
        Args:
            state_snapshots: {domain_name: frontmatter_dict} from state files
        
        Returns:
            List of PatternMatch(pattern_id, pattern_name, alert_level,
                                 signal_type, matched_conditions, action_suggestion)
        """
    
    def emit_signals(self, matches: list[PatternMatch]) -> list[DomainSignal]:
        """Convert PatternMatch → DomainSignal for ActionComposer."""
```

**Integration Point:** Called at Step 8 BEFORE AI cross-domain reasoning. The engine's
deterministic output is presented to the AI as context, which then applies judgment
(confidence >70% filter, consequence forecasting, FNA scoring).

**Operator Library:**

| Operator | Description | Example |
|----------|-------------|---------|
| `days_until_lte` | Days until field date ≤ N | next_deadline within 90 days |
| `lt` / `gt` / `eq` | Numeric comparison | balance < bill_amount |
| `exists` | Field is non-null and non-empty | travel booked |
| `has_item_within_days` | List field has item with date within N days | school events next 7 days |
| `contains` | String field contains substring | status contains "pending" |
| `stale_days` | Field's last_updated is >N days old | health last checkup >180 days |

**Phase 2 Operators (deferred):**

| Operator | Description | Deferred Reason |
|----------|-------------|----------------|
| `overlaps_with` | Time overlap between two domain event lists | Requires timezone-aware time-range comparison, calendar event format parsing, and quadratic N×M event comparison. Non-trivial implementation (~100-150 LOC alone) hidden as a single "operator". Step 8o WorkIQ already partially covers calendar overlap detection. |

Phase 1 ships with 6 operators covering 5 of the 6 Step 8f correlation rules. The
calendar overlap rule (school event + work conflict) is partially handled by existing
Step 8o WorkIQ cross-domain conflict detection.

**Privacy & Security:**
- Pattern engine reads state file frontmatter only (no encrypted body unless domain is decrypted)
- **Encrypted domain limitation:** Patterns referencing vault-encrypted domains
  (immigration, finance, insurance, estate, health, audit, vehicle, contacts) evaluate
  only during active catch-up sessions when the vault is open. Between sessions (e.g.,
  when called by the nudge daemon in Enhancement 4), these patterns silently return
  no match. Non-encrypted domains (calendar, kids, home, shopping, social) evaluate
  at any time.
- **Vault error vs. expected lock distinction:** The `evaluate()` method accepts a
  `vault_open: bool` parameter from the session context. When `vault_open=True` (during
  an active catch-up) and a vault-encrypted domain pattern returns no data due to a read
  error, the engine logs a `pattern_engine_vault_miss` event to `health-check.md` instead
  of silently returning no match. Silent no-match is only acceptable when `vault_open=False`
  (between-session invocations where the vault is locked by design). This prevents a vault
  unlock failure during a catch-up from silently suppressing all financial/immigration patterns.
- Pattern definitions in `config/patterns.yaml` contain no PII
- PatternMatch output contains signal_type and matched_conditions — no raw data
- Compound signals remain ephemeral (briefing only, per existing Step 8f rule)

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| State file schema drift (patterns reference fields that change) | Medium | High — silent pattern failures | Validate pattern conditions against state file schema at preflight; warn on missing fields |
| Over-alerting (too many patterns fire) | Medium | Medium — noise overwhelms signal | Existing `max 3 compound signals per briefing` cap; pattern-level disable |
| Operator complexity creep | Low | Medium — becomes mini query language | Fixed operator set; no user-defined operators; new operators require code change |
| Performance on 20+ domains × 20+ patterns | Low | Low — frontmatter only, microseconds | Benchmark target <50ms for 30 patterns × 20 domains |

**Estimated Scope:** ~400 LOC (engine) + ~120 LOC (patterns.yaml) + ~300 LOC (tests)

**Dependencies:** None new. Reads state file frontmatter (YAML parsing already used everywhere).

---

### Enhancement 4: Proactive Time-Aware Nudges

**Problem Statement:** `channel_push.py` fires ONLY after catch-up (Step 20). If a user
doesn't run catch-up on a day when a bill is due or an appointment is in 2 hours,
no notification is sent. The vault-watchdog daemon (`com.artha.vault-watchdog.plist`)
runs every 5 minutes but only handles encryption — it has no notification capability.

**Architecture Fit:** MEDIUM — Requires a new daemon or extending the existing vault-watchdog.
This is the most architecturally significant change because it introduces a persistent
background process that reads state and sends messages.

**Current State:**
- `com.artha.vault-watchdog.plist`: macOS LaunchAgent, runs every 5 minutes, handles vault encryption
- `channel_push.py`: Step 20 only; daily dedup marker; pending queue retry
- `skill_runner.py`: runs during catch-up only (not standalone daemon)
- No cron/scheduled notification capability exists

**Proposed Solution:**

**Option A — Reuse Vault Watchdog Cadence (Recommended)**

Reuse the existing launchd cadence from vault-watchdog, but invoke a standalone Python
`nudge_daemon.py` from the watchdog shell wrapper. The LaunchAgent already runs every
5 minutes. Adding a lightweight state check costs <100ms per run.

```python
# Extension to scripts/vault_watchdog.py (or new scripts/nudge_daemon.py)
def check_nudges(artha_dir: Path) -> list[NudgeItem]:
    """Lightweight state file scan for time-sensitive items.
    
    Reads ONLY state file frontmatter (not full files, not encrypted files).
    Checks:
      1. open_items.md: items with deadline == today and status == open
      2. calendar.md: events starting within 2 hours
      3. health-check.md: last_catch_up >24 hours ago → gentle reminder
    
    Returns NudgeItem(type, message, urgency, domain, deadline) or empty list.
    """

def send_nudge(nudge: NudgeItem, channels_config: dict) -> bool:
    """Send a single nudge via channel_push.py's adapter (reuse Telegram connection).
    
    Constraints:
      - Max 3 nudges per day (prevent notification fatigue)
      - Min 2 hours between nudges (no rapid-fire)
      - PII guard on every outbound message
      - No encrypted state file access (no vault decrypt)
      - Nudge dedup: same (type, domain, deadline) = skip
    """
```

**Option B — Standalone Nudge Daemon**

New LaunchAgent: `com.artha.nudge-daemon.plist` (separate from vault-watchdog).
Runs every 15 minutes. Cleaner separation but more infrastructure to manage.

**Recommendation:** Option A (reuse watchdog cadence + Python bridge). Rationale:
- Single daemon is simpler to manage, monitor, and debug
- Vault-watchdog already has the correct permissions and launch context
- Adding a nudge check to a 5-minute cycle is negligible overhead
- If nudge check fails, vault encryption still works (fail-isolated)

**⚠️ IMPLEMENTATION NOTE:** The vault-watchdog is a **pure bash script** inside a
LaunchAgent plist — it cannot directly import Python modules or run middleware.
The recommended integration is a Python invocation bridge:

```bash
# In vault-watchdog bash script, after lock/session check:
if [[ "${ARTHA_SESSION_ACTIVE}" == "false" ]]; then
    python3 "${ARTHA_DIR}/scripts/nudge_daemon.py" --check-once 2>/dev/null || true
fi
```

This keeps nudge logic in Python (testable, middleware-aware) and limits the bash
integration to a single `python3` invocation with `|| true` for failure isolation.
The nudge daemon is a standalone Python script, not a bash extension.

**⚠️ ENCRYPTED DOMAIN LIMITATION:** The nudge daemon runs between sessions when the
vault is typically locked. Patterns referencing vault-encrypted domains (immigration,
finance, insurance, estate, health, audit, vehicle, contacts) **will silently fail**
when the vault is locked. The nudge daemon can only evaluate patterns against
non-encrypted state files (calendar, kids, home, shopping, social, open_items,
health-check).

**Nudge Types:**

| Nudge | Trigger | Urgency | Frequency Cap |
|-------|---------|---------|--------------|
| Overdue item | `OI-NNN.deadline < today AND status == open` | 🔴 Critical | Once per item per day |
| Today deadline | `OI-NNN.deadline == today AND status == open` | 🟠 Urgent | Once per item |
| Imminent event | `calendar event starts within 2 hours` | 🟡 Standard | Once per event |
| Catch-up reminder | `last_catch_up > 24 hours ago` | 🔵 Info | Once per day max |
| Bill due today | `bill_due_tracker: due_date == today` | 🟠 Urgent | Once per bill |

**Privacy & Security:**
- Reads ONLY frontmatter from non-encrypted state files (open_items.md, calendar.md, health-check.md)
- **NEVER** decrypts vault files (no immigration/finance/health data in nudges)
- PII guard runs on every outbound nudge message
- Nudge content is generic: "You have 2 items due today" — NOT "Your Chase Visa bill of $3,421 is due"
- Audit: `NUDGE_SENT` events logged to `state/audit.md`

**⚠️ CONCURRENT WRITE SAFETY:** The nudge daemon writes `NUDGE_SENT` audit events to
`state/audit.md` via `_audit_log()`, which uses raw `open(path, "a")` with no file locking.
The main catch-up session also writes to the same file. On macOS APFS, small single-line
appends are atomic, but multi-line audit entries can interleave under concurrent writes.
**Pre-requisite for Wave 2:** Add `fcntl.flock(fd, LOCK_EX)` to `_audit_log()` (reuse
the locking pattern from `vault.py` lines 332-344). Additionally, `cmd_items_add` has
a read-then-write race on `state/open_items.md` (read max OI-NNN, then append) — if the
nudge daemon triggers a triage write between the read and append, a duplicate OI-ID is
produced. All write paths must acquire an advisory file lock before the read-compute-write
cycle. See §2.X Write Integration Contract.

**Nudge Dedup Store:** Frequency caps ("once per item per day") require persistent
tracking across daemon restarts. Following the `channel_push.py` marker file pattern
(`_PUSH_DEDUP_HOURS = 12`), the nudge daemon writes one marker file per sent nudge:
`tmp/nudge_{signal_type}_{domain}_{YYYYMMDD}.marker`. At startup, the daemon scans
`tmp/nudge_*.marker` files and prunes any older than 24 hours. This ensures dedup
survives OS reboots and daemon crashes without requiring a database.

**⚠️ KEY ASSUMPTION: The existing watchdog cadence can supervise the Python nudge worker without affecting encryption duties.**

**CLARIFICATION REQUESTED:** Is reusing the existing watchdog cadence with a Python bridge
the preferred approach, or should nudges be a fully separate LaunchAgent? The trade-off is
operational simplicity (shared cadence, one supervision point) vs. stricter separation of
concerns (completely independent daemon lifecycle).

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Notification fatigue | Medium | High — user disables notifications | 3/day cap; 2h minimum gap; disable via `config/channels.yaml: nudge_enabled: false` |
| Daemon crashes | Low | Medium — encryption also affected if Option A | Isolate nudge check in try/catch; vault encryption runs regardless |
| Stale state (nudge based on outdated data) | Medium | Low — false positive nudge | Staleness marker on every nudge: "Based on data from Xh ago" |
| Cross-platform parity | Medium | Medium — macOS LaunchAgent only; Windows needs Task Scheduler | Phase 1: macOS only; Phase 2: Windows Task Scheduler via setup.ps1 |

**Estimated Scope:** ~200 LOC (nudge check) + ~50 LOC (LaunchAgent update) + ~150 LOC (tests)

**Dependencies:** Existing `channel_push.py` Telegram adapter infrastructure.

---

### Enhancement 5: Adaptive Briefing Intelligence

**Problem Statement:** Briefing format selection is purely rule-based (Step 2b: flash if <4h,
digest if >48h, standard otherwise). There's no feedback loop. The user's behavior signals
(skipping domains, always overriding to flash, dismissing coaching nudges) are tracked in
`health-check.md` but never fed back into format selection.

**Architecture Fit:** HIGH — Uses existing telemetry from `health-check.md` (already tracked:
`calibration_skip_rate`, `coaching_nudge: fired|dismissed`, `domains_loaded`, `signal_noise_ratio`)
to adjust format parameters. No new infrastructure required.

**Current State:**
- Step 2b: Deterministic format selection based on `hours_elapsed`
- `health-check.md → catch_up_runs`: tracks `briefing_format`, `context_pressure`, `signal_noise`, `coaching_nudge`
- `health-check.md → calibration_skip_rate`: tracked but not acted upon
- Fact extractor: captures `preference` type facts but doesn't influence briefing format

**Proposed Solution:**

New module: `scripts/briefing_adapter.py`

**Adaptive Rules (deterministic, not ML):**

```python
def recommend_format(health_data: dict, hours_elapsed: float, user_prefs: dict) -> BriefingConfig:
    """Recommend briefing format based on historical behavior.
    
    Inputs: last 30 catch-up runs from health-check.md
    
    Rules (all thresholds use session-count windows for consistency with
    catch_up_runs data model — health-check.md stores a session list, not a
    time-series):
    1. If user overrode to flash >60% of last 10 runs → default to flash
    2. If signal_noise_ratio < 30% for ≥7 sessions → aggressively suppress info-tier items
    3. If calibration_skip_rate > 80% over last 10 sessions → reduce calibration questions to 0
    4. If coaching_nudge dismissed >70% of last 10 → suppress coaching nudges
    5. If domains_loaded is consistently [same 4 domains] over last 10 sessions → pre-load those as pseudo-Tier-A
    6. If user always skips weekend_planner over last 10 sessions → suppress it
    
    Output: BriefingConfig with adjusted format, domain_item_cap, calibration_count,
            coaching_enabled, suppressed_sections
    
    Override: User explicit commands (/catch-up deep) always win.
    """
```

**Integration Points:**
- Called at Step 2b AFTER deterministic format selection
- Adjusts `BriefingConfig` parameters (not format itself — keeps user in control)
- Writes `adaptive_adjustments: [list]` to health-check.md for transparency
- New fact type: when user corrects adaptation, extract as `preference` fact

**Privacy & Security:**
- Reads only `health-check.md → catch_up_runs` (aggregate stats, no PII)
- Never reads email content or state file details
- All adaptations are transparent (logged to health-check.md)

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Over-adapting (suppresses something user suddenly needs) | Medium | Medium — missed alert | Tier-A domains NEVER suppressed by adaptation; only Tier-B and formatting affected |
| Cold start (new user, no history) | Low | None — falls through to Step 2b defaults | Minimum 10 catch-ups before adaptive rules activate |
| User confusion ("why did format change?") | Medium | Low — cosmetic | Transparent: briefing footer shows "📊 Adapted: [reason]" when adaptations applied |

**Estimated Scope:** ~200 LOC (adapter) + ~80 LOC (config schema) + ~200 LOC (tests)

**Dependencies:** None new. Reads existing `health-check.md` telemetry.

---

### Enhancement 6: Subscription Lifecycle Manager

**Problem Statement:** `subscription_monitor.py` detects price changes and trial expirations
by reading `state/digital.md`. It doesn't monitor upcoming renewals, track cancellation
deadlines, or alert on trial-to-paid conversions proactively. Users discover charges only
after they hit the credit card.

**Architecture Fit:** HIGH — Extends existing skill with additional signal types.
No new infrastructure; same skill_runner pipeline.

**Current State:**
- `subscription_monitor.py`: reads `state/digital.md → subscriptions` section
- Detects: price increases (≥1% delta), trial end within 7 days, reappearing cancelled
- `state/digital.md`: tracks subscription name, amount, billing_cycle, status, trial_end
- `_SIGNAL_ROUTING`: has `subscription_renewal` signal type already defined

**Proposed Solution:**

Extend `scripts/skills/subscription_monitor.py` with additional lifecycle stages:

**New Signal Types:**

| Signal | Trigger | Action |
|--------|---------|--------|
| `subscription_trial_ending` | trial_end within 7 days (existing) | instruction_sheet: "Cancel or keep [service]?" |
| `subscription_renewal_upcoming` | next_renewal within 3 days | instruction_sheet: "Review [service] renewal" |
| `subscription_cancellation_deadline` | cancel_by date within 3 days | instruction_sheet: "Cancel before [date] to avoid charge" |
| `subscription_annual_review` | annual plan renewed within 30 days | instruction_sheet: "Annual review: still using [service]?" |
| `subscription_free_tier_available` | (manual flag in digital.md) | instruction_sheet: "Free tier may suffice for [service]" |

**New state/digital.md fields per subscription:**

```yaml
subscriptions:
  - name: Netflix
    amount: 22.99
    billing_cycle: monthly
    status: active
    next_renewal: 2026-04-01
    cancel_by: 2026-03-29         # NEW: cancellation deadline
    annual_review_date: 2026-12-01 # NEW: when to review annual plan
    usage_indicator: low           # NEW: self-assessed (low/medium/high)
    notes: "Rarely used since March"
```

**Privacy & Security:**
- No external API calls (state-file-only, matching existing design)
- Subscription names are NOT PII (Netflix, Spotify, etc.)
- Dollar amounts are in standard sensitivity (digital.md is not vault-encrypted)

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Stale subscription data (user doesn't update digital.md) | High | Medium — missed renewals | Alert when last_updated >90 days: "Review subscriptions — data may be stale" |
| Over-alerting on renewals | Low | Low — noise | Only alert 3 days before (not 30 days); max 2 subscription alerts per briefing |

**Estimated Scope:** ~150 LOC (skill extension) + ~30 LOC (signal routing additions) + ~120 LOC (tests)

**Dependencies:** None new.

---

### Enhancement 7: Household-Scoped Shared View

**Problem Statement:** Artha has per-recipient scope filtering (full/family/standard) but no
aggregated family view. Archana receives a filtered-down version of Ved's briefing, not a
briefing composed from her perspective. There's no shared calendar intelligence or
coordinated task view.

**Architecture Fit:** MEDIUM — Requires new aggregation logic in `channel_push.py` and a
new `/family` command in `channel_listener.py`. Does NOT require multi-user state files.

**Current State:**
- `config/channels.yaml`: Archana configured with `access_scope: family`
- `channel_push.py`: strips sensitive domains before sending to family-scope recipients
- `channel_listener.py`: scope filter removes immigration/finance/estate/insurance/employment/digital
- No multi-perspective briefing composition

**Proposed Solution:**

**Phase 1 — Family Flash Summary (low effort, high visibility)**

New section in `channel_push.py` for `family` scope recipients:

```
📋 Family Flash — Friday, March 20

📅 FAMILY CALENDAR
  - Today: Parth orthodontist 2:30 PM, Trisha soccer 4:00 PM
  - Tomorrow: Family dinner with Sharmas 6:00 PM
  
📝 SHARED TASKS
  - OI-038: Parth's science project due Monday
  - OI-041: Grocery pickup (Costco list in Notes)
  
🏠 HOME
  - Landscaper coming Saturday 9 AM
  
💬 ASK VED
  - "[No shared decisions pending]" or "[Decision: summer camp deadline March 25]"
```

**Phase 2 — Family Dashboard Command (future)**

New command: `/family` in `channel_listener.py`

Available to `family` scope users. Returns a pre-composed family view:
- Today's family events (from calendar.md — already standard sensitivity)
- Open items tagged with shared domains (kids, home, shopping, social)
- Any pending decisions in shared domains where `visibility: shared` (from decisions.md)

**⚠️ DECISION VISIBILITY:** Decisions in shared domains (kids, home, shopping, social) are
not automatically surfaced in the family flash. Each decision has an optional
`visibility: private | shared` field (default: `private`). Only decisions explicitly
marked `visibility: shared` appear in the family view. This prevents in-progress
deliberations (e.g., "Evaluate switching primary schools for Parth") from surfacing
before the user is ready to share them. The `/decision` command and AI signal capture
both default to `private`; the user must explicitly mark a decision as shared.

**Privacy & Security:**
- Family view ONLY shows standard-sensitivity domains (calendar, kids, home, shopping, social)
- NEVER includes: immigration, finance, estate, insurance, employment, digital, health
- PII guard runs on all content before delivery
- No new state files needed — reads existing state files with scope filter

**⚠️ KEY ASSUMPTION: Archana's Telegram channel is already configured and operational.**

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Scope leak (sensitive data reaches family view) | Low | High — privacy violation | Scope filter BEFORE content composition (not after); same `_apply_scope_filter()` with stricter allowlist |
| Information asymmetry confusion | Medium | Low — social friction | "ASK VED" section explicitly flags that more information exists |
| Stale family data (no catch-up since yesterday) | Medium | Low — staleness marker | Every family push includes staleness: "Updated Xh ago" |

**Estimated Scope:** ~150 LOC (family flash builder) + ~80 LOC (listener command) + ~100 LOC (tests)

**Dependencies:** Existing `channel_push.py` adapter, `channel_listener.py` dispatch.

---

### Enhancement 8: Session-Level Cost Telemetry

**Problem Statement:** The `/cost` command exists in the command registry but has no
implementation. Users have no visibility into API token consumption, LLM costs, or
optimization opportunities.

**Architecture Fit:** HIGH — Pure instrumentation; no write-path changes. Extends existing
`health-check.md` telemetry.

**Current State:**
- `/cost` listed in `domain_index.py` no-prompt commands
- No `cost_tracker.py` or token accounting script
- `session_summarizer.py` mentions "token cost" but doesn't implement tracking
- `health-check.md → context_pressure` tracks estimated context window % but not dollar cost

**Proposed Solution:**

New module: `scripts/cost_tracker.py`

**Instrumentation Points:**

| API | Metric | Estimation Method |
|-----|--------|-------------------|
| Claude API (primary) | Input tokens, output tokens, cache hits | Approximate from context_pressure × 200K window size |
| Gemini CLI | Calls per session, estimated tokens | Count `safe_cli.py gemini` invocations |
| MS Graph | API calls | Count from `pipeline_metrics.json` |
| Gmail API | API calls | Count from `pipeline_metrics.json` |
| Telegram Bot API | Messages sent/received | Count from `channel_push.py` + `channel_listener.py` |

**Cost Model (approximate, per-session):**

```yaml
# Written to health-check.md → cost_telemetry
cost_telemetry:
  session_date: 2026-03-20
  claude_input_tokens_est: 85000
  claude_output_tokens_est: 12000
  claude_cache_hit_pct: 65
  claude_cost_est_usd: 0.42
  gemini_calls: 2
  gemini_cost_est_usd: 0.00  # free tier
  msgraph_calls: 8
  gmail_calls: 12
  telegram_messages: 3
  total_est_usd: 0.42
  rolling_30d_usd: 12.60
```

**`/cost` Command Output:**

```
💰 Artha Cost Estimate

Today:     $0.42  (85K input + 12K output, 65% cached)
This week: $2.94  (7 sessions)
This month: $12.60 (30 sessions)

📊 Breakdown:
  Claude API:  $12.48 (99.0%)
  Gemini CLI:  $0.00  (free tier)
  MS Graph:    $0.00  (within free allotment)
  Gmail API:   $0.12  (1.0%)

💡 Tip: Cache hit rate at 65%. Running catch-ups at consistent
   times improves cache hit rate (Anthropic 5-min TTL).
```

**Privacy & Security:**
- Cost data contains no PII (token counts and dollar estimates only)
- Written to non-encrypted section of health-check.md

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Inaccurate token estimation | High | Low — estimates, not billing | Label all values as "est"; document formula source; accuracy disclaimer: ±50% |
| API pricing changes | Medium | Low — estimates become stale | Pricing constants in `config/artha_config.yaml` with `cost_per_1k_input_tokens` etc. |
| Obsessive cost monitoring | Low | Low — behavioral risk | Only show on `/cost` command; don't surface in every briefing |

**⚠️ ESTIMATION METHOD:** Token estimates are derived from **empirically calibrated
constants**, not real-time API instrumentation. The Anthropic API response headers
containing actual token counts are not accessible through Artha's `safe_cli` proxy.

**Calibration procedure (one-time setup):**
1. Run 5 representative sessions (flash, standard, deep, multi-source, weekend)
2. Record `context_pressure` from each session's `health-check.md` entry
3. Compare against actual token usage from the Anthropic dashboard/invoice
4. Derive `tokens_per_context_pressure_pct` coefficient
5. Store coefficient in `config/artha_config.yaml` alongside pricing constants
6. Re-calibrate quarterly or when Claude model version changes

Output tokens are estimated per briefing format: flash ~500, standard ~2,000, deep ~4,000.
Cache hit percentage is estimated from session timing regularity (Anthropic 5-min TTL).
All estimates carry an explicit ±50% accuracy disclaimer in `/cost` output.

**Estimated Scope:** ~180 LOC (tracker) + ~80 LOC (command handler) + ~120 LOC (tests)

**Dependencies:** None new. Reads existing `pipeline_metrics.json`, `health-check.md`.

---

### Enhancement 9: Document Signal Extraction

**Problem Statement:** Email attachments (PDFs, invoices, confirmations, scanned documents)
are completely ignored by the email pipeline. Step 5c truncates body to 1,500 tokens and
strips HTML. Attachment metadata (filename, mime-type, size) is available in the JSONL
records but not extracted or routed.

**Architecture Fit:** HIGH — Extends email processing pipeline with a metadata extraction
step. Does NOT require OCR or document parsing (Phase 1 is metadata-only).

**Current State:**
- Gmail API connector: fetches email metadata including `attachments` array
- Pipeline JSONL: includes attachment metadata in `parts` or `attachments` field
- Step 5c: processes body text; ignores attachments
- No PDF/OCR libraries in requirements

**Proposed Solution:**

**Phase 1 — Attachment Metadata Routing (no OCR, no content extraction)**

New step: Step 5e (after 5c content preparation, before Step 6 routing)

```python
# scripts/attachment_router.py
class AttachmentRouter:
    def route(self, email_record: dict) -> list[AttachmentSignal]:
        """Extract attachment metadata and route to domains.
        
        Reads: email_record["attachments"] = [
            {"filename": "invoice_march.pdf", "mime_type": "application/pdf", 
             "size_bytes": 45000}
        ]
        
        Routing rules (filename pattern → domain):
          - *invoice*, *bill*, *statement* → finance
          - *prescription*, *lab_result*, *eob* → health
          - *report_card*, *transcript*, *iep* → kids
          - *w2*, *1099*, *tax_return* → finance (high sensitivity)
          - *passport*, *visa*, *i-485* → immigration (high sensitivity)
          - *insurance*, *policy*, *claim* → insurance
          - *lease*, *deed*, *closing* → home/estate
        
        Emits: AttachmentSignal(email_id, filename, domain, sensitivity, signal_type)
        Does NOT download or parse attachment content.
        """
```

**Signal Types:**

| Signal | Action | Friction |
|--------|--------|---------|
| `document_financial` | instruction_sheet: "Financial document received: [filename]. Download and review." | standard |
| `document_medical` | instruction_sheet: "Medical document received: [filename]. File in health records." | standard |
| `document_immigration` | instruction_sheet: "Immigration document received: [filename]. Download immediately." | high |
| `document_school` | instruction_sheet: "School document received: [filename]." | low |

**Phase 2 — Content Extraction (future, requires user consent)**

Would add OCR/PDF parsing using `pdfplumber` or `pymupdf` to extract text from
attachments. Requires explicit user opt-in due to LLM processing of potentially
sensitive document content. Not in scope for Phase 1.

**Privacy & Security:**
- Phase 1: metadata-only (filename, mime-type, size) — no content extraction
- Filenames may contain PII — run through `pii_guard.filter_text()`
- Immigration/financial documents flagged as `sensitivity: high` → encrypted in ActionQueue

**⚠️ KEY ASSUMPTION: Gmail API connector already includes attachment metadata in JSONL output.**

**CLARIFICATION REQUESTED:** Does the Gmail connector (`scripts/connectors/google_email.py`)
currently include attachment metadata (filename, mime_type, size) in its JSONL output?
If not, the connector needs extension first.

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Filename doesn't match content (user renames files) | Medium | Low — false routing | Route to comms as fallback; user corrects via triage |
| Sensitive filenames in clear text | Medium | Medium — PII exposure | pii_guard on filenames; immigration/finance signals get high sensitivity |
| Phase 2 scope creep (OCR/parsing is a rabbit hole) | High | High — delays Phase 1 | Strict Phase 1 = metadata-only. Phase 2 requires separate spec approval |

**Estimated Scope:** ~180 LOC (router) + ~40 LOC (signal routing) + ~150 LOC (tests)

**Dependencies:** Requires verification that Gmail connector outputs attachment metadata.

---

### Enhancement 10: WhatsApp Cloud API (Phase 2)

**Problem Statement:** WhatsApp messaging is currently URL-scheme only (`wa.me/PHONE?text=`).
The user must manually tap Send. The `whatsapp_local.py` connector reads
ChatStorage.sqlite for contact warmth but cannot send messages programmatically.

**Architecture Fit:** MEDIUM — Requires Meta Business account setup, webhook registration,
and a new handler module. The action handler pattern already exists; this adds a new
transport.

**Current State:**
- `actions/whatsapp_send.py`: URL scheme handler (Phase 1) — opens browser
- `connectors/whatsapp_local.py`: reads ChatStorage.sqlite (read-only, macOS)
- `skills/whatsapp_last_contact.py`: contact warmth/nudge from database
- `_SIGNAL_ROUTING`: `birthday_approaching` → `whatsapp_send` already defined
- `actions.yaml`: `whatsapp_send` has `autonomy_floor: true` (always human approval)

**Proposed Solution:**

**Phase 2A — Meta Cloud API Integration**

New handler: `scripts/actions/whatsapp_cloud_send.py`

```python
# scripts/actions/whatsapp_cloud_send.py
class WhatsAppCloudHandler:
    """Send WhatsApp messages via Meta Cloud API.
    
    Requirements:
      - Meta Business account with WhatsApp Business API access
      - Approved message templates (for business-initiated messages)
      - Phone number ID and access token in keyring
    
    Message Types:
      - Template messages (business-initiated, pre-approved by Meta)
      - Session messages (reply within 24h window to user-initiated message)
    
    Constraints:
      - autonomy_floor: true — ALWAYS requires human approval
      - Rate limit: 10/hour, 50/day (existing from actions.yaml)
      - PII check: phone_number allowlisted, message body scanned
      - Undo: NOT possible (WhatsApp messages cannot be recalled via API)
    """
    
    def validate(self, proposal: ActionProposal) -> tuple[bool, str]:
        """Template validation + phone number format check."""
    
    def execute(self, proposal: ActionProposal) -> ActionResult:
        """Send via POST /v18.0/{phone_number_id}/messages."""
```

**Template Types Needed:**

| Template | Use Case | Variables |
|----------|----------|-----------|
| `birthday_greeting` | Birthday approaching signal | `{{name}}`, `{{wish}}` |
| `reminder` | Generic reminder | `{{subject}}`, `{{deadline}}` |
| `family_update` | Family coordination | `{{event}}`, `{{time}}` |

**Privacy & Security:**
- Meta Cloud API requires phone number — stored in keyring (not files)
- Access token stored in keyring (not `.tokens/` — aligns with credential store pattern)
- Message content scanned by PII guard before send
- `autonomy_floor: true` — every message requires explicit human approval
- Audit: `ACTION_EXECUTED | whatsapp_cloud_send | recipient_hash | template_id`
- Message body NEVER stored in ActionQueue (only template_id + variable hashes)

**⚠️ KEY ASSUMPTION: User is willing to set up a Meta Business account (requires Facebook business verification).**

**CLARIFICATION REQUESTED:** Is Meta Business account setup acceptable? It requires:
1. Facebook Business verification (~2-3 days)
2. WhatsApp Business API access request
3. Phone number registration (can be the same personal number)
4. Message template pre-approval by Meta (1-3 business days per template)

If not acceptable, the URL scheme approach (Phase 1) is the permanent ceiling.

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Meta Business account setup friction | High | High — blocks entire feature | Phase 1 URL scheme remains as fallback; Phase 2 is opt-in |
| Template approval delays | Medium | Medium — blocked use cases | Submit templates early; start with generic `reminder` template |
| API rate limits (Meta limits: 1K/day for new accounts) | Low | Low — Artha sends ~5/day | Well within limits |
| Message delivery failures (number not on WhatsApp) | Low | Low — handler returns failure | Validate phone number before send; fallback to URL scheme |

**Estimated Scope:** ~250 LOC (handler) + ~100 LOC (setup script) + ~200 LOC (tests)

**Dependencies:** Meta Business account, WhatsApp Business API access, template approvals.

---

### Enhancement 11: Memory & Self-Model Activation

**Problem Statement:** `state/memory.md` and `state/self_model.md` both exist with full
schemas but contain zero content (`facts: []`, empty body). The `fact_extractor.py` script
is fully functional but is never invoked because no catch-up session has completed the
post-session summarization loop that triggers it. Meanwhile, `self_model.md` has no
dedicated writer — it relies on inline AI reasoning at Step 11c, which never fires because
the step is never reached in an incomplete session. The result: Artha re-learns everything
from scratch every session, losing all corrections, preferences, and patterns.

**Architecture Fit:** HIGH — All infrastructure exists. This is a wiring and activation
enhancement, not a new module. `fact_extractor.py` (Phase 5 of Agentic Intelligence
Improvement Plan) is production-ready.

**Current State:**
- `state/memory.md`: schema v2.0, `facts: []`, `last_updated: never`
- `state/self_model.md`: schema v1.0, empty body, `last_updated: never`, originally designed
  around a 1,500 char cap (**raised to 3,000 in this spec** — see Phase 2 below)
- `scripts/fact_extractor.py`: 200+ LOC, fully tested, reads `tmp/session_history_*.md`
- Fact schema: 6 types (correction, pattern, preference, contact, schedule, threshold)
- Step 11c (Artha.md): self-model update step — documented but never executed
- Config flag: `harness.agentic.fact_extraction.enabled: true` (default)
- **Existing wiring discovered:** `session_summarizer.py` already contains
  `_auto_extract_facts_if_catchup()` which imports and invokes `fact_extractor` after
  catch-up sessions, gated on the config flag. This is called from `get_context_card()`.

**⚠️ INVESTIGATION REQUIRED BEFORE IMPLEMENTATION:**

The fact extraction pipeline wiring **may already exist**. Before writing new wiring code,
debug why the existing chain doesn't fire:

1. Is `harness.agentic.fact_extraction.enabled` actually `true` in the running environment?
2. Does `get_context_card()` get called at session end?
3. Are `tmp/session_history_*.md` files present when `_auto_extract_facts_if_catchup()` runs?
4. Does the glob `sorted(summ_dir.glob("*session_history*.md"))` return non-empty results?

Enhancement 11's scope may reduce from "wire into pipeline" to "fix the existing invocation
chain" — potentially a 10 LOC fix instead of 30 LOC.

**⚠️ SPIKE EXIT CRITERION:** If the Phase 1 investigation reveals that the root cause
requires >50 LOC modifications to existing load-bearing modules (`session_summarizer.py`,
`artha.py`, or `pipeline.py`), this becomes a separately scoped task. In that case,
Wave 0 ships Enhancement 11 Phase 1 as an "investigate only" deliverable with findings
documented but no state mutation code merged. Phase 2 (self-model writer) can proceed
independently since it only depends on `memory.md` having been populated by prior sessions.

**Proposed Solution:**

**Phase 1 — Debug and fix existing fact extraction chain:**

1. Investigate why `_auto_extract_facts_if_catchup()` in `session_summarizer.py` doesn't fire
2. Fix the broken invocation chain (likely: `get_context_card()` not called, or
   `tmp/session_history_*.md` files not present at invocation time)
3. Ensure `scripts/session_summarizer.py` writes `tmp/session_history_*.md` at session end
4. Verify `fact_extractor.extract_facts_from_summary()` runs after summarization
5. Gate on config flag `harness.agentic.fact_extraction.enabled`

**Phase 2 — Wire self-model updates:**

1. Create `scripts/self_model_writer.py` — a minimal writer that:
   - Reads latest `health-check.md → catch_up_runs` (domain accuracy, user corrections)
   - Reads `state/memory.md → facts` (corrections = blind spots, preferences = strategies)
   - Synthesizes into three self-model sections: Domain Confidence, Effective Strategies, Known Blind Spots
   - Writes to `state/self_model.md` (max 3,000 chars — raised from AR-2's 1,500)
2. Invoke at Step 11c of catch-up workflow (after calibration check)
3. Minimum activation threshold: 5 catch-ups before first self-model write (need data)

**Interface Contract:**
```python
# scripts/self_model_writer.py
class SelfModelWriter:
    def update(self, memory_path: Path, health_check_path: Path, self_model_path: Path) -> bool:
        """Update self-model from accumulated memory and health-check data.
        Returns True if self-model was modified. Respects 3,000 char cap
        (raised from 1,500 to accommodate 20+ domains across three sections)."""
```

**Note:** The self-model char cap is raised from 1,500 to 3,000. At ~500 chars per section,
1,500 chars allows only 5-6 bullet points each — extremely tight for 20+ domains once the
system is running. 3,000 chars keeps the file well within context window budget while
allowing meaningful coverage. The writer implements a rolling top-K approach: only the
most relevant domains per section are included.

**Privacy & Security:**
- Memory facts contain domain-level statements, not raw PII (e.g., "Ved prefers PEMCO" not SSN)
- PII guard runs on extracted facts before persistence (existing in fact_extractor.py)
- Self-model contains behavioral observations only — no financial/immigration/health data
- Both files are standard sensitivity (not vault-encrypted)

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Fact extraction produces low-quality facts | Medium | Low — noise in memory, not harmful | Confidence scoring (corrections=1.0, inferred=0.6); TTL expiry for stale facts |
| Self-model exceeds 3,000 char cap | Low | Low — truncation already specified | Writer enforces cap; rolling top-K approach includes most relevant domains only |
| Memory file grows without bound | Low | Medium — context window pressure | Bounded to 30 facts / 3K chars (existing fact_extractor limits) |
| Stale facts persist after correction | Medium | Medium — Artha repeats mistakes | `last_seen` tracking; facts not re-confirmed in 90 days get TTL=30 |

**Estimated Scope:** ~120 LOC (self_model_writer) + ~10-30 LOC (pipeline wiring — may be
a fix to existing chain, not new wiring) + ~100 LOC (tests)

**Observability safeguard:** Add a P1 preflight check: "If catch-up count ≥ 10 AND
`memory.md` facts == 0 AND `fact_extraction.enabled == true`, warn: 'Fact extraction
appears non-functional — investigate session_summarizer invocation chain.'"

**Dependencies:** None new. All infrastructure exists.

---

### Enhancement 12: Decision & Scenario Lifecycle

**Problem Statement:** `state/decisions.md` and `state/scenarios.md` both exist as stubs
with empty tables. The catch-up workflow references decisions extensively (Step 3: deadline
detection, Step 8d: detection & logging, Step 8i: expired marking, Step 19: calibration),
and scenarios (Step 3: periodic triggers, Step 8e: evaluation). But no runtime logic
captures decisions from session conversations or evaluates scenario conditions. Users
have active multi-option decisions (insurance comparison, lease return, tax strategy)
that go untracked.

**Architecture Fit:** HIGH — The state file schemas exist. The workflow steps reference
them. This enhancement adds the capture and lifecycle logic that connects the existing
references to actual state mutations.

**Current State:**
- `state/decisions.md`: schema v1.0, empty tables (Active Decisions, Archive, Scenario Analyses)
- `state/scenarios.md`: minimal stub (`some: value`)
- Step 3: `if decision has deadline ≤14 days → flag decision_deadline_warning`
- Step 8d: "If user mentions a decision with ≥2 options → propose structured logging"
- Step 8e: "Evaluate `status: watching` scenarios against current state"
- Step 8i: "Mark expired decisions where deadline has passed"
- `config/implementation_status.yaml`: `decisions: partial`, `scenarios: partial`
- No `scripts/decision_*.py` or `scripts/scenario_*.py` exists

**Proposed Solution:**

**Phase 1 — Decision capture during catch-up:**

New module: `scripts/decision_tracker.py`

**⚠️ DESIGN NOTE:** Decision detection does NOT scan free-form session text for keywords
like "should I" or "deciding between". Session text is AI-generated and non-reproducible —
scanning it violates principle §0.2 ("Deterministic before heuristic"). Instead, decisions
are captured via two explicit channels:

1. **AI-emitted structured signal** — during Step 8d, the AI proposes a decision via
   `DomainSignal(signal_type="decision_detected", ...)` with structured fields. The
   `decision_tracker` validates the signal and presents it to the user for confirmation.
2. **Explicit user command** — `/decision "Switch insurance: PEMCO vs Allstate" --deadline 2026-04-15`
   (new Telegram command, follows `/remember` pattern).

```python
class DecisionTracker:
    def capture_from_signal(self, signal: DomainSignal, existing_decisions: list) -> DecisionProposal | None:
        """Validate a decision_detected signal from Step 8d AI reasoning.
        Returns a proposal for user confirmation, never auto-writes.
        Deduplicates against existing_decisions by title similarity."""

    def capture_from_command(self, text: str, deadline: str | None) -> DecisionProposal:
        """Create a decision proposal from explicit /decision command.
        PII guard runs on text before persistence."""

    def update_lifecycle(self, decisions_path: Path, today: date) -> list[str]:
        """Deterministic lifecycle: mark expired, flag deadline warnings.
        Returns list of status changes for briefing display."""
```

  **Action Layer Contract:** `decision_detected` does not map to a normal executor-backed
  side effect such as email, calendar, or reminder creation. It maps to a new queue-only
  proposal type: `decision_log_proposal`.

  ```python
  # New _SIGNAL_ROUTING entry
  "decision_detected": {
    "action": "decision_log_proposal",
    "friction": "standard",
    "min_trust": 1,
    "reversible": false,
  }
  ```

  `decision_log_proposal` creates an `ActionProposal` that surfaces for human confirmation
  in Terminal/Telegram, but has **no executor side effect** until approved. On approval,
  the confirmed data is handed to `decision_tracker.capture_from_signal()` and persisted to
  `state/decisions.md` through the middleware stack. This keeps the decision flow inside
  the existing queue/approval model without inventing a hidden channel-local side path.

**Phase 2 — Scenario evaluation engine:**

Extend `state/scenarios.md` with proper schema and add scenario evaluation to Step 8e:

**⚠️ NOTE:** Replacing the `state/scenarios.md` stub (`some: value`) is a full file
overwrite. The WriteGuard's NET-NEGATIVE check will flag this as 100% field loss.
**Mitigation:** Use atomic file replacement via POSIX rename semantics: write the new
content to `state/scenarios.md.tmp`, then `os.rename("scenarios.md.tmp", "scenarios.md")`.
`os.rename()` is atomic on POSIX, so no intermediate inconsistent state can persist.
Additionally, tag with `updated_by: bootstrap` so the WriteGuard exempts it from
NET-NEGATIVE checks on subsequent updates.

```yaml
# state/scenarios.md (proper schema)
---
schema_version: "1.0"
domain: scenarios
last_updated: "2026-03-20"
---

## Active Scenarios

- id: SCN-001
  title: "Insurance switch: PEMCO vs Allstate vs Progressive"
  status: watching  # watching | resolved | archived
  trigger: "state/insurance.md → renewal_date within 30 days"
  variables:
    - name: pemco_annual
      source: manual
      value: null
    - name: allstate_annual
      source: "state/insurance.md → current_premium"
      value: null
  last_evaluated: null
  created: 2026-03-20
```

**Integration Points:**
- Step 8d: AI emits `decision_detected` signal → `decision_tracker.capture_from_signal()` validates and proposes
- `/decision` command: user-initiated capture via Telegram (follows `/remember` pattern)
- Step 8e: `decision_tracker.evaluate_scenarios()` checks trigger conditions
- Step 8i: `decision_tracker.update_lifecycle()` handles expiry/warnings
- Step 3: Periodic trigger when `decision.deadline ≤ 14 days`
- Briefing display: "📋 2 active decisions, 1 deadline in 5 days"
- New `_SIGNAL_ROUTING` entry: `decision_detected` → `decision_log_proposal` (queue-only proposal type, no direct executor)

**Decision Schema Fields (per entry in `state/decisions.md`):**

Each decision entry includes an optional `visibility` field:
- `visibility: private` (default) — visible only to `full` scope users
- `visibility: shared` — also surfaced in family flash and `/family` command output

The `/decision` command and AI signal capture both default to `private`. Users must
explicitly mark a decision as `shared` to surface it in household views. This prevents
in-progress deliberations from reaching family-scope recipients prematurely.

**Privacy & Security:**
- Decision titles may reference sensitive topics (insurance, immigration)
- `decisions.md` is `sensitivity: medium`, NOT vault-encrypted (consistent with current schema)
- **Sensitivity rationale:** `decisions.md` stores decision titles, option labels, and
  deadline dates — NOT financial amounts, account numbers, or immigration case IDs.
  Those details remain in their source domain state files (which are vault-encrypted).
  This is an **implementation constraint**: the `DecisionTracker.capture_from_signal()`
  and `capture_from_command()` methods must strip numeric values and reference source
  domains by name only (e.g., "Review insurance options" not "PEMCO $2,400 vs Allstate $2,100").
  If a user includes financial specifics via `/decision`, PII guard intercepts them.
- Scenario variables that reference vault-encrypted state files read frontmatter only
- PII guard scans decision text captured from session conversation

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| AI fails to emit decision signal at Step 8d | Medium | Low — `/decision` command provides explicit fallback | `/decision` command as primary capture; AI signal as secondary |
| Scenario trigger evaluation on stale state | Medium | Low — stale alert shown | Staleness marker: "Based on data from X days ago" |
| Decision table grows unbounded | Low | Low — archive section exists | Auto-archive resolved decisions after 30 days |

**Estimated Scope:** ~200 LOC (decision_tracker) + ~80 LOC (scenario schema) + ~40 LOC (/decision command) + ~180 LOC (tests)

**Dependencies:** None new. Reads existing state files.

---

### Enhancement 13: Relationship State & Command

**Problem Statement:** `state/relationships.md` does not exist despite being referenced
in `config/commands.md` (`/relationships` command), `scripts/skill_runner.py`
(`relationship_pulse` skill allowlisted), and `prompts/social.md` (relationship tracking
within social domain). The `/relationships` command is broken — registered but with no
backing state file or implementation. This is the only gap item where the state file
itself is missing.

**Architecture Fit:** HIGH — Creates a new state file following existing patterns and
wires the pre-registered skill. No new architectural patterns.

**Current State:**
- `state/relationships.md`: **DOES NOT EXIST**
- `scripts/skill_runner.py`: `relationship_pulse` in allowlist — but no backing code
- `config/commands.md`: `/relationships` described (close family on-cadence, overdue reconnects, upcoming occasions, life events)
- `prompts/social.md`: relationship tracking section exists
- `state/social.md`: exists, tracks social interactions — but no structured relationship graph
- `connectors/whatsapp_local.py`: reads ChatStorage.sqlite for contact warmth — data source exists

**Proposed Solution:**

**Phase 1 — State file and skill implementation:**

Create `state/relationships.md`:
```yaml
---
schema_version: "1.0"
domain: relationships
last_updated: ""
sensitivity: sensitive
encrypted: false
---

## Inner Circle (monthly cadence)
| Name | Relation | Last Contact | Channel | Cadence | Status |
|------|----------|-------------|---------|---------|--------|

## Extended (quarterly cadence)
| Name | Relation | Last Contact | Next Outreach | Notes |
|------|----------|-------------|---------------|-------|

## Upcoming Occasions
| Person | Event | Date | Gift/Action | Status |
|--------|-------|------|-------------|--------|

## Life Events Log
| Person | Event | Date | Follow-up |
|--------|-------|------|-----------|
```

**Privacy decision (explicit):** `state/relationships.md` is classified as `sensitivity: sensitive`
because it contains relationship PII: names, relations, contact cadences, and life events.
It remains unencrypted for parity with the broader social domain, but all reads, rendering,
and outbound delivery paths must run through PII-aware handling.

**Phase 2 — Skill implementation:**

New module: `scripts/skills/relationship_pulse.py`

```python
class RelationshipPulseSkill:
    def run(self, state_dir: Path, social_state: dict, whatsapp_warmth: dict | None) -> list[DomainSignal]:
        """Deterministic relationship health check.
        Signals:
        - overdue_contact: last_contact > cadence period
        - upcoming_occasion: event within 14 days
        - life_event_followup: life event logged, no follow-up recorded
        Data sources: state/relationships.md, state/social.md, whatsapp_local warmth data."""
```

**Phase 3 — Wire /relationships command:**

Add `relationship_pulse_view.py` to `scripts/` (following `dashboard_view.py` pattern):
- Reads `state/relationships.md`
- Renders: overdue contacts, upcoming occasions, relationship health summary
- Registers in `domain_index.py` with existing `/relationships` slot

**Integration Points:**
- `skill_runner.py`: wire `relationship_pulse` to actual skill module
- `channel_listener.py`: `/relationships` dispatches to view script
- Step 7: social domain processing populates `state/relationships.md` from email/calendar data
- WhatsApp connector: contact warmth enriches `Last Contact` data

**Privacy & Security:**
- Names and relationship data are PII — PII guard scans on all outputs
- Sensitive classification (not vault-encrypted) — consistent with keeping the social domain
  readable while still flagging relationship PII explicitly
- Family-scope users can see relationship data (social is a shared domain)
- WhatsApp warmth data stays local (ChatStorage.sqlite, never transmitted)

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Bootstrap burden (user must populate manually) | High | Medium — empty file adds no value | Offer `/bootstrap relationships` interview; seed from social.md + occasions skill |
| Stale contact data | Medium | Low — cadence alerts fire on staleness | Flag overdue contacts; user confirms during catch-up |
| PII in relationship names reaches family flash | Low | Low — names are expected in social context | PII guard scans; social domain is in family-scope allowlist |

**Estimated Scope:** ~20 LOC (state file) + ~120 LOC (skill) + ~80 LOC (view script) + ~150 LOC (tests)

**Dependencies:** Existing `skill_runner.py` framework, `whatsapp_local.py` warmth data (optional).

---

### Enhancement 14: Power Half Hour View

**Problem Statement:** The `/power` command (aliases: `power half hour`) is registered in
`scripts/domain_index.py` and fully specified in the UX spec (§10.15), but has no backing
view script. The command should assemble quick-win tasks from `state/open_items.md` that
fit within a 30-minute focused session. The effort estimation framework exists (⚡Quick
≤5min, 🔨Medium 5–30min, 🏗️Deep 30+min) but the rendering script does not.

**Architecture Fit:** HIGH — Pure view script following `dashboard_view.py` and
`scorecard_view.py` patterns. Reads existing data, renders formatted output. No new
infrastructure.

**Current State:**
- `scripts/domain_index.py`: `/power` registered as no-prompt command
- `specs/artha-ux-spec.md` §10.15: full display specification
- `state/open_items.md`: tracks active items with effort estimates (⚡/🔨/🏗️ tags)
- No `scripts/power_half_hour_view.py` or equivalent exists
- `config/implementation_status.yaml`: `power: partial`

**Proposed Solution:**

New module: `scripts/power_half_hour_view.py`

```python
class PowerHalfHourView:
    def render(self, open_items_path: Path, calendar_path: Path | None,
               format: str = "standard") -> str:
        """Render a focused 30-minute task session.

        Selection logic (deterministic):
        1. Filter open items where effort == '⚡' (Quick, ≤5 min)
        2. Sort by: overdue first, then deadline proximity, then age
        3. Pack into 30-minute window (6× ⚡Quick items max)
        4. If <3 quick items, include 1× 🔨Medium item
        5. Show estimated total time and completion likelihood

        Output format:
        ⚡ POWER HALF HOUR (30 min)
        ─────────────────────────
        1. [5 min] OI-038: Reply to school field trip form
        2. [5 min] OI-041: Confirm Costco pickup slot
        3. [5 min] OI-044: Forward insurance quote to Archana
        4. [15 min] OI-039: Review Parth's algebra progress
        ─────────────────────────
        Est. 30 min | 4 items | 🎯 Completion: High
        """
```

**Integration Points:**
- `domain_index.py`: `/power` calls `power_half_hour_view.py`
- `channel_listener.py`: `/items quick` alias also uses this view (≤5 min items only)
- Enhancement 4 (Proactive Nudges): when calendar gap detected, nudge can reference power half hour
- Reads `state/open_items.md` effort tags; falls back to "unestimated" if no tags

**Privacy & Security:**
- Reads only `state/open_items.md` (standard sensitivity)
- Output may contain task descriptions with names — PII guard on outbound

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| No effort-tagged items exist | Medium | Low — degrade gracefully | Show all open items sorted by age; note "Add effort tags for better selection" |
| Incorrect time estimates | Low | Low — user adjusts | Conservative defaults; items without tags estimated at 10 min |

**Estimated Scope:** ~100 LOC (view script) + ~80 LOC (tests)

**Dependencies:** None new. Reads existing `state/open_items.md`.

---

### Enhancement 15: Monthly Retrospective Generator

**Problem Statement:** Step 3 of the catch-up workflow flags `generate_monthly_retro = true`
on the 1st of each month, and Steps 8h/11 reference retrospective generation. The briefing
formats spec (§8.10) defines the retrospective format. But no generation code exists — the
`summaries/` directory contains daily briefings only, no monthly retrospective files
(`YYYY-MM-retro.md`). The trigger fires but no action follows.

**Architecture Fit:** HIGH — Pure rendering + state aggregation following existing patterns.
Reads the last 30 days of `health-check.md` catch-up history and state file diffs.

**Current State:**
- Step 3: `if day_of_month == 1 AND last_retro > 28 days ago → generate_monthly_retro = true`
- Steps 8h/11: reference retrospective output
- `summaries/` directory: contains daily briefings (2026-03-XX.md), no retro files
- `config/briefing-formats.md` §8.10: retrospective display format documented
- `config/implementation_status.yaml`: `monthly_retrospective: partial`
- No `scripts/retrospective_*.py` or similar exists

**Proposed Solution:**

New module: `scripts/retrospective_view.py`

```python
class RetrospectiveView:
    def generate(self, state_dir: Path, summaries_dir: Path,
                 month: str, health_check: dict) -> str:
        """Generate monthly retrospective.

        Sections (per §8.10 briefing format):
        1. Month at a Glance — headline events, key metrics
        2. Goals Progress — delta vs. start of month
        3. Domain Activity — which domains changed this month (deterministic:
           state file last_updated timestamps; does NOT attempt semantic diffing)
        4. Decisions Made — from decisions.md archive
        5. Open Items Velocity — created vs. completed vs. aged
        6. Pattern Insights — from memory.md facts added this month
        7. Next Month Preview — upcoming deadlines, renewals, events

        ⚠️ SCOPE LIMITATION: Section 3 (Domain Activity) reports WHICH domains
        were updated and how many times, NOT what changed semantically. State
        files are overwritten each session with no snapshot history. Semantic
        change summarization would require LLM processing of daily briefings
        from summaries/*.md — deferred to Phase 2 (requires active session,
        violates deterministic-before-heuristic for a scheduled generation).

        Data sources:
        - health-check.md → catch_up_runs (last 30 days)
        - state/*.md frontmatter → last_updated timestamps (domain activity)
        - state/goals.md → progress metrics
        - state/open_items.md → created/completed counts
        - state/memory.md → facts added this month
        - state/decisions.md → decisions resolved this month
        """
```

**Integration Points:**
- Step 3 flag `generate_monthly_retro = true` triggers generation
- Output saved to `summaries/YYYY-MM-retro.md`
- Surfaced in the first catch-up of the month (appended after standard briefing)
- Optional Telegram push for family-scope users (month highlights only)

**Privacy & Security:**
- Reads state file frontmatter (standard + domain summaries, not raw data)
- Retrospective may aggregate sensitive domains — vault-encrypted state is NOT read
- PII guard on all output
- Retrospective file saved unencrypted (aggregate metrics, not raw PII)

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Insufficient data for meaningful retro | High (early months) | Low — thin output | Minimum threshold: ≥10 catch-ups in month; otherwise "Insufficient data" |
| First-of-month catch-up takes too long | Medium | Low — additive content | Retrospective is appended section, not replacing briefing; can be deferred |

**Estimated Scope:** ~180 LOC (retrospective view) + ~120 LOC (tests)

**Dependencies:** Enhancement 12 (Decision Lifecycle) for decisions section; degrades gracefully without it.
**⚠️ Delivery order dependency:** Enhancement 15 MUST ship after Enhancement 12 within
Wave 0. If parallelized across contributors, the retrospective's "Decisions Made" section
will be empty. The dependency is soft (graceful degradation) but the output quality is
materially worse without it.

---

### Enhancement 16: Coaching Nudge Automation

**Problem Statement:** Step 19b defines a coaching nudge framework: select ONE coaching
element per catch-up (progress reflection, obstacle anticipation, next small win,
cross-domain insight) based on `state/goals.md` and `state/memory.md → coaching_preferences`.
The specification is thorough (coaching style: question/direct/cheerleader, dismissal
tracking, 7-day cooldown). But execution is entirely manual — the AI must remember to
invoke this at the correct step with the correct parameters. In practice, Step 19b
is often skipped because the session reaches its context limit before Step 19.

**Architecture Fit:** HIGH — Deterministic nudge selection that integrates with existing
coaching preference infrastructure and Enhancement 4 (Proactive Nudges).

**Current State:**
- Step 19b (Artha.md): coaching nudge specification (fire conditions, selection logic, dismissal rules)
- `state/memory.md → coaching_preferences`: `coaching_enabled`, `coaching_style` fields
- `health-check.md → catch_up_runs`: tracks `coaching_nudge: fired|skipped|dismissed`
- No dedicated coaching script — logic is inline Step 19b prose
- Nudges tracked but rarely fire (Step 19 often not reached)

**Proposed Solution:**

New module: `scripts/coaching_engine.py`

```python
class CoachingEngine:
    def select_nudge(self, goals: dict, memory_facts: list, health_history: list,
                     preferences: dict) -> CoachingNudge | None:
        """Deterministic coaching nudge selection.

        Selection priority (pick first that applies):
        1. Progress reflection — goal pace deviation >20% from plan
        2. Obstacle anticipation — goal has 'blocked' or 'at_risk' status
        3. Next small win — momentum score <0.5 (from health-check)
        4. Cross-domain insight — pattern engine found relevant correlation

        Suppression rules:
        - coaching_enabled == false → return None
        - Same nudge type fired and dismissed within 7 days → skip
        - >2 nudges dismissed in a row → pause all nudges for 14 days

        Returns None if no nudge is appropriate (suppressed or no trigger)."""

    def format_nudge(self, nudge: CoachingNudge, style: str) -> str:
        """Format nudge per coaching_style preference.
        - 'question': Socratic prompt (e.g., "What's the one thing blocking your weight goal?")
        - 'direct': Action statement (e.g., "Your weight goal is 15% behind pace. Consider tracking meals this week.")
        - 'cheerleader': Encouragement (e.g., "You've lost 3 lbs this month! Keep the momentum — one more week of tracking.")
        """
```

**Integration Points:**
- **Primary:** Called at Step 8 (early in session, before context exhaustion) instead of Step 19b
- **Secondary:** Enhancement 4 (Proactive Nudges) can deliver coaching nudges between sessions
- Dismissal tracking writes to `health-check.md → coaching_nudge`
- Reads `state/goals.md`, `state/memory.md`, pattern engine output (Enhancement 3)

**Key Change:** Move coaching nudge from Step 19 to Step 8. Step 19 is aspirational — by
Step 8, the AI has domain context loaded and context budget remaining. This is the single
highest-impact change to make coaching nudges actually fire.

**Step 8 Sub-Step Ordering:**
- **Step 8.1:** Pattern engine evaluates cross-domain patterns (Enhancement 3)
- **Step 8.2:** Coaching engine runs with pattern output as optional input
- If Enhancement 3 (Pattern Engine) is not yet deployed (Wave 0 ships coaching before
  Wave 1 ships patterns), the coaching engine degrades gracefully to goals-and-memory-only
  inputs. Priority 4 (cross-domain insight) is skipped.

**Privacy & Security:**
- Coaching nudges reference goal titles and metrics — not PII
- Nudge content is generic (no financial amounts, no health specifics)
- Same PII guard as all other briefing content

**Risks & Mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Premature nudge (Step 8 lacks full domain context) | Medium | Low — nudge is informational, not action | Nudge is based on state file data (pre-loaded), not session conversation |
| Coaching fatigue (nudge every session) | Medium | Medium — user disables | Frequency cap: max 1 per catch-up; max 5 per week; dismissal cooldown |
| Style mismatch (wrong coaching approach) | Low | Low — user can change style | Preference stored in memory.md; `/teach coaching` explains options |

**Estimated Scope:** ~150 LOC (coaching engine) + ~120 LOC (tests)

**Dependencies:** Enhancement 3 (Pattern Engine) for cross-domain insights (degrades gracefully without it).

---

### §2.X — Write Integration Contract

**All state file writes introduced by ACT Reloaded MUST route through the middleware stack.**

The existing middleware order is: PII → WriteGuard → Audit → [write] → WriteVerify → Audit
(as defined in `scripts/middleware/`). New write operations must not use direct `open()`
or `Path.write_text()` calls — they must call through the middleware pipeline.

**Per-enhancement write operations and required middleware layers:**

| Enhancement | Write Operation | PII Guard | WriteGuard | Audit | WriteVerify |
|-------------|----------------|-----------|------------|-------|-------------|
| 2 (`/remember`) | Append to `state/inbox.md` | ✅ | ✅ (append-only exempt from NET-NEGATIVE) | ✅ | ✅ |
| 11 (memory) | Update `state/memory.md` | ✅ (existing in fact_extractor) | ✅ | ✅ | ✅ |
| 11 (self-model) | Update `state/self_model.md` | ✅ | ✅ | ✅ | ✅ |
| 12 (decisions) | Update `state/decisions.md` | ✅ | ✅ | ✅ | ✅ |
| 12 (scenarios) | Replace `state/scenarios.md` | ✅ | ⚠️ Bootstrap exempt | ✅ | ✅ |
| 12 (`/decision`) | Append to `state/decisions.md` | ✅ | ✅ (append-only exempt) | ✅ | ✅ |
| 13 (relationships) | Populate `state/relationships.md` | ✅ | ✅ | ✅ | ✅ |
| Pre-existing tech debt | `/items_add` writes to `state/open_items.md` | ❌ → ✅ | ❌ → ✅ | ✅ (audit_log exists) | ❌ → ✅ |
| Pre-existing tech debt | `/items_done` rewrites `state/open_items.md` | ❌ → ✅ | ❌ → ✅ | ✅ (audit_log exists) | ❌ → ✅ |

**Pre-requisite for Wave 3:** Retrofit `/items_add` and `/items_done` through the middleware
stack. This validates the write-through-middleware pattern that `/remember` and `/decision`
will follow, and eliminates the pre-existing vulnerability (full-file rewrite from remote
Telegram command without guards).

### §2.Y — Feature Flag Dependency Map

16 feature flags in `artha_config.yaml` creates operational complexity. The following
dependency map documents which flags depend on others:

```
coaching_engine ──depends on──▶ pattern_engine (optional: degrades to goals-only)
coaching_engine ──depends on──▶ memory_activation (reads memory.md facts)
monthly_retrospective ──depends on──▶ decision_tracker (optional: decisions section empty)
briefing_adapter ──depends on──▶ memory_activation (reads preference facts)
```

**Incompatible combinations (warn at preflight):**
- `coaching_engine: true` + `memory_activation: false` → coaching engine has no memory
  input; degrades but should warn
- `monthly_retrospective: true` + `decision_tracker: false` → retro decisions section
  always empty; degrades but should warn

**Default-on set (recommended for Wave 0→4 progressive rollout):**
- Wave 0: `memory_activation`, `decision_tracker`, `relationship_pulse`,
  `power_half_hour`, `monthly_retrospective`, `coaching_engine`
- Wave 1: `email_signal_extractor`, `pattern_engine`, `briefing_adapter`
- Wave 2: `nudge_daemon`, `subscription_lifecycle`
- Wave 3: `knowledge_capture`, `family_flash`, `cost_telemetry`
- Wave 4: `attachment_router`, `whatsapp_cloud_api`

Add a P1 preflight check that warns on incompatible flag combinations.

---

## §3 — Trade-Off Matrix

### Option A: Full Implementation (All 16 Enhancements)

| Dimension | Assessment |
|-----------|-----------|
| **Effort** | ~3,180 LOC production code + ~2,320 LOC tests |
| **Risk** | Medium-High — 3 architectural changes (read-only invariant, daemon extension, external API) |
| **Value** | Maximum — covers all identified gaps + activates dormant infrastructure |
| **Time-to-Market** | 5 waves over estimated implementation cycles |

### Option B: Core Ten (Enhancements 1-6 + 11-14)

| Dimension | Assessment |
|-----------|-----------|
| **Effort** | ~2,270 LOC production code + ~1,660 LOC tests |
| **Risk** | Medium — 1 architectural change (read-only invariant for /remember) |
| **Value** | High — covers 85% of identified value; activates all dormant infrastructure |
| **Time-to-Market** | 4 waves |

### Option C: Quick Wins First (Enhancements 11-16 as Wave 0, then 1-10)

| Dimension | Assessment |
|-----------|-----------|
| **Effort** | Same as Option A but front-loads ~770 LOC low-risk activation work |
| **Risk** | Low for Wave 0 (no architectural changes; wiring only); then medium-high for Waves 1-4 |
| **Value** | Fastest user-visible impact — 6 dormant features activated before any new infrastructure |
| **Time-to-Market** | Wave 0 fastest; total same as Option A |

**Recommendation:** Option C (Quick Wins First). Rationale: Enhancements 11-16 are
low-effort, low-risk, and deliver immediate value by activating infrastructure that is
already built and tested. Shipping these as Wave 0 builds confidence and provides data
(memory facts, decision history, coaching signals) that makes Waves 1-4 more effective.

---

## §4 — Implementation Waves (Strangler Fig Migration)

### Wave 0 — Activation (Enhancements 11, 12, 13, 14, 15, 16)

**Theme:** Activate dormant infrastructure. Zero new architectural patterns.

**Delivery Order:**
1. `scripts/self_model_writer.py` + pipeline wiring for `fact_extractor.py` — memory activation
2. `scripts/decision_tracker.py` + `state/scenarios.md` schema fix — decision/scenario lifecycle
3. `state/relationships.md` + `scripts/skills/relationship_pulse.py` + view script — relationships
4. `scripts/power_half_hour_view.py` — /power command
5. `scripts/retrospective_view.py` — monthly retrospective
6. `scripts/coaching_engine.py` — coaching nudge automation (moved to Step 8)

**Integration Test:** Run catch-up → fact_extractor saves ≥1 fact to memory.md →
self_model_writer updates self_model.md → next catch-up reads both → briefing includes
"🧠 Memory: 3 facts active" and "💡 Coaching: [nudge text]".

**⚠️ COACHING TEST SCOPE:** Wave 0 integration tests cover coaching nudge priorities 1-3
(progress reflection, obstacle anticipation, next small win) only. Priority 4 (cross-domain
insight) depends on the pattern engine (Wave 1) and is NOT testable in Wave 0. Add a
separate Wave 1 integration test: `pattern_engine.evaluate()` → `coaching_engine.select_nudge(
pattern_output=...)` → nudge includes cross-domain insight.

**Regression Boundary:** No existing modules modified in ways that change behavior when
feature flags are off. All new modules are additive. Existing 1,243 tests must pass.

**Rollback:** Feature-flagged via `config/artha_config.yaml`:
```yaml
enhancements:
  memory_activation: true        # false = skip fact extraction and self-model update
  decision_tracker: true         # false = decisions.md remains manual
  relationship_pulse: true       # false = skill skipped in skill_runner
  power_half_hour: true          # false = /power shows "not yet available"
  monthly_retrospective: true    # false = retro flag ignored
  coaching_engine: true          # false = Step 19b prose only (current behavior)
```

### Wave 1 — Signal Pipeline (Enhancements 1, 3, 5)

**Theme:** Make Artha smarter at detecting signals and adapting behavior.

**Delivery Order:**
1. `scripts/email_signal_extractor.py` — enables email → action pipeline
2. `config/patterns.yaml` + `scripts/pattern_engine.py` — codifies cross-domain detection
3. `scripts/briefing_adapter.py` — closes the feedback loop

**Integration Test:** End-to-end: email with "RSVP by March 25" → EmailSignalExtractor
→ DomainSignal → ActionComposer → ActionProposal in queue → Telegram inline button →
user approves → email_reply handler sends RSVP.

**Regression Boundary:** Existing skill_runner pipeline unchanged. Email classifier unchanged.
Domain routing unchanged. All 1,243 existing tests must pass.

**Rollback:** Feature-flagged via `config/artha_config.yaml`:
```yaml
enhancements:
  email_signal_extractor: true   # false = skip Step 6.5
  pattern_engine: true           # false = Step 8 uses prose rules only
  briefing_adapter: true         # false = Step 2b uses static rules only
```

### Wave 2 — Proactive Intelligence (Enhancements 4, 6)

**Theme:** Make Artha proactive between sessions.

**Delivery Order:**
1. `scripts/nudge_daemon.py` (or vault-watchdog extension) — proactive notifications
2. `subscription_monitor.py` extension — full lifecycle tracking

**Integration Test:** Nudge daemon detects `OI-NNN.deadline == today` at 8 AM →
sends Telegram nudge → user runs catch-up → briefing shows same item at top.

**Regression Boundary:** Vault-watchdog encryption duty unchanged (if Option A).
Subscription monitor existing signals unchanged; new signals additive.

### Wave 3 — Channel Expansion (Enhancements 2, 7, 8)

**Theme:** Make Artha more accessible and transparent.

**Delivery Order:**
1. `/remember` command + `state/inbox.md` — async knowledge capture
2. Family flash summary in `channel_push.py` — household shared view
3. `scripts/cost_tracker.py` + `/cost` implementation — cost transparency

**Integration Test:** User sends `/remember Buy flowers for anniversary` via Telegram →
INB-043 created → next catch-up triages to social domain → creates OI-NNN →
Archana's family flash mentions "shared decision: anniversary plans".

**Regression Boundary:** `/remember` follows the existing write precedent set by
`/items_add` and `/items_done`, with stronger safeguards (middleware-routed).
**Pre-requisite:** Retrofit `/items_add`/`/items_done` through middleware stack
before shipping `/remember` (see §2.X Write Integration Contract). Existing scope
filtering for family recipients unchanged.

### Wave 4 — Advanced (Enhancements 9, 10)

**Theme:** Extend data sources and output channels.

**Delivery Order:**
1. `scripts/attachment_router.py` — document metadata routing
2. `scripts/actions/whatsapp_cloud_send.py` — Cloud API handler

**Integration Test:** Email with `invoice_march_2026.pdf` attachment →
attachment_router detects "invoice" → routes to finance → creates ActionProposal
("Financial document received: invoice_march_2026.pdf. Download and review.")

**Regression Boundary:** Email processing pipeline unchanged for emails without
attachments. WhatsApp URL scheme (Phase 1) remains as fallback.

---

## §5 — Assumptions Registry

### Verified Assumptions (tested against codebase)

| ID | Assumption | Verification Method | Status |
|----|-----------|-------------------|--------|
| A-01 | Signals come only from deterministic skills, not from email body AI reasoning | Read `action_composer.py` line 7-14; docstring explicit | ✅ VERIFIED |
| A-02 | `channel_listener.py` has no `/remember` command; read-only invariant is claimed but **already violated** by `/items_add` and `/items_done` | Searched full file; `/items_add` writes via `open(oi_path, "a")`; `/items_done` rewrites via `Path.write_text()` | ✅ VERIFIED (invariant claim is false) |
| A-03 | No `pattern_engine.py` exists in codebase | Full file search returned 0 results | ✅ VERIFIED |
| A-04 | `channel_push.py` fires only at Step 20 post-catch-up | Header line 1-25; no cron/scheduled mechanism found | ✅ VERIFIED |
| A-05 | Briefing format selection is purely rule-based (Step 2b) | `hours_elapsed` logic only; no feedback loop | ✅ VERIFIED |
| A-06 | `subscription_monitor.py` is state-file-only (no external APIs) | Code header: "non-blocking and works without network access" | ✅ VERIFIED |
| A-07 | No multi-user aggregation exists; only per-recipient scope filtering | `channels.yaml` + `channel_push.py`; 3 scopes (full/family/standard) | ✅ VERIFIED |
| A-08 | `/cost` command has no implementation | Listed in `domain_index.py` no-prompt commands; no script found | ✅ VERIFIED |
| A-09 | No attachment/document processing in email pipeline | Step 5c processes body text only; no PDF/OCR libraries | ✅ VERIFIED |
| A-10 | WhatsApp send is URL-scheme only; read is ChatStorage.sqlite only | `whatsapp_send.py` uses `open` command; `whatsapp_local.py` is read-only | ✅ VERIFIED |
| A-11 | ActionQueue dedup exists (prevents double-enqueue of same signal) | `action_queue.py` `propose()` has dedup check | ✅ VERIFIED |
| A-12 | All 1,243 existing tests pass on current codebase | `pytest tests/ --tb=no -q` → "1243 passed, 5 skipped, 20 xfailed" | ✅ VERIFIED (2026-03-20) |
| A-13 | `_SIGNAL_ROUTING` in action_composer.py has 18 signal types | Full table read at lines 43-77 | ✅ VERIFIED |
| A-14 | TrustEnforcer has 3 levels (0=observe, 1=propose, 2=pre-approve) | `trust_enforcer.py` lines 1-100; §6.2 specs/act.md | ✅ VERIFIED |
| A-15 | PII guard runs before email processing (Step 5b) | `config/Artha.md` Step 5b; `config/workflow/process.md` | ✅ VERIFIED |

### Unverified Assumptions (require clarification)

| ID | Assumption | Risk if Wrong | Owner |
|----|-----------|---------------|-------|
| A-16 | `/remember` follows existing write precedent set by `/items_add` and `/items_done` with stronger safeguards | N/A — invariant already broken | ✅ RESOLVED (see CQ-1) |
| A-17 | Reusing the existing watchdog cadence with a Python nudge worker is preferred over a fully separate LaunchAgent | Operational simplicity vs. stricter separation of concerns | **USER DECISION** |
| A-18 | Gmail connector includes attachment metadata in JSONL output | Enhancement 9 Phase 1 blocked if not; connector needs extension (+~100 LOC) | **VALIDATE IN WAVE 0** (5-min code read of `scripts/connectors/google_email.py`) |
| A-19 | User is willing to set up Meta Business account for WhatsApp Cloud API | Enhancement 10 blocked; URL scheme remains ceiling | **USER DECISION** |
| A-20 | `DomainSignal` dataclass is importable from `scripts/actions/base.py` by new modules | If import path differs, all signal-emitting modules need adjustment | ✅ VERIFIED — `class DomainSignal` at `scripts/actions/base.py:37`; exported in `__all__` at line 309 |
| A-21 | `health-check.md → catch_up_runs` stores ≥30 historical entries | Enhancement 5 adaptive rules need historical data; if rolled over, adaptation window shrinks | ⚠️ PARTIAL — `catch_up_count: 7` as of 2026-03-21. The 10-session minimum window for briefing adapter (Enhancement 5) will not be met until ~3 more sessions. Graceful degradation is already specified (static rules until threshold). No code blocker. |
| A-22 | `state/inbox.md` as a new state file does not conflict with existing domain_registry | No `inbox` domain currently registered; needs to be added to domain_registry.yaml | ✅ CONFIRMED — no `inbox` entry exists in `config/domain_registry.yaml`. Addition is already captured as a required §12 change (~25 LOC additive). No conflict; additive-only registration. |

---

## §6 — Clarification Requests

Before proceeding to implementation, the following decisions need user input:

### CQ-1: Read-Only Invariant for /remember (Enhancement 2) — **RESOLVED**

**Resolution:** The read-only invariant is **already broken** by `/items_add` and
`/items_done`, which write directly to `state/open_items.md` without any middleware
protection (no PII guard, no WriteGuard, no NET-NEGATIVE check). The `/remember`
command follows this existing precedent with **strictly stronger safeguards**
(PII scan, middleware-routed, write-rate-limited, full-scope-only).

**Decision:** Proceed with Option A. No user clarification needed.

**Required tech debt:** Retrofit `/items_add` and `/items_done` to route through
the middleware stack before Wave 3 ships `/remember`. See §2.X Write Integration Contract.

### CQ-2: Nudge Daemon Architecture (Enhancement 4)

**Question:** Reuse existing watchdog cadence with a Python bridge, or create a fully separate daemon?

**Options:**
- **Option A (recommended):** Reuse the existing watchdog shell wrapper and launchd cadence,
  but invoke standalone `nudge_daemon.py` as a supervised Python worker.
  Simpler operations, testable Python logic, nudge failure isolated via `|| true`.
- **Option B:** Separate `com.artha.nudge-daemon.plist`. Cleaner separation;
  nudge bugs can't affect encryption.

### CQ-3: Gmail Attachment Metadata (Enhancement 9)

**Question:** Does `scripts/connectors/google_email.py` currently include attachment
metadata (filename, mime_type, size) in its JSONL output?

**Action needed:** Read the connector and verify. If not present, the connector
needs extension before Enhancement 9 can begin.

### CQ-4: Meta Business Account (Enhancement 10)

**Question:** Is the Meta Business account setup acceptable for WhatsApp Cloud API?

**Context:** Requires Facebook business verification (2-3 days), WhatsApp Business
API access request, phone number registration, and message template pre-approval
(1-3 business days per template).

**If NO:** Enhancement 10 is deferred; URL scheme (Phase 1) remains permanent.

### CQ-5: Implementation Priority Confirmation

**Question:** Does the recommended Wave ordering (Wave 1: signals, Wave 2: proactive,
Wave 3: channels, Wave 4: advanced) align with user priorities?

**Alternative orderings:**
- If proactive nudges are highest priority: swap Wave 1 and Wave 2
- If cost visibility is urgent: pull Enhancement 8 into Wave 1
- If WhatsApp Cloud API is blocking a near-term need: pull into Wave 2

---

## §7 — Testing Strategy

### Unit Test Requirements (per enhancement)

Each enhancement must ship with:
1. **Happy path tests** — normal operation produces expected signals/output
2. **Empty input tests** — no emails, no state, no history → graceful degradation
3. **PII exposure tests** — ensure no PII leaks through new code paths
4. **Boundary tests** — rate limits, queue caps, field caps
5. **Regression tests** — existing functionality unchanged after integration

### Integration Test Requirements

| Wave | Integration Test |
|------|-----------------|
| Wave 0 | Catch-up → fact_extractor saves facts → self_model_writer updates → next session reads both; /power renders tasks; /relationships returns data |
| Wave 1 | Email → signal extractor → composer → queue → approval → handler execution |
| Wave 2 | Daemon detects deadline → sends nudge → catch-up shows same item |
| Wave 3 | /remember → inbox → triage → open_item → family flash |
| Wave 4 | Attachment metadata → routing → signal → action proposal |

### Red Team Tests (Security)

| Test | Enhancement | What We're Testing |
|------|------------|-------------------|
| PII in /remember text | 2 | pii_guard.filter_text() on user-composed messages |
| Nudge content leaks sensitive domain data | 4 | Nudge reads only non-encrypted frontmatter |
| Pattern engine reveals encrypted state | 3 | Engine reads frontmatter only; no vault access |
| Attachment filename PII | 9 | pii_guard on filenames (e.g., "tax_return_SSN_123-45-6789.pdf") |
| WhatsApp message body PII | 10 | PII guard pre-send; body NOT stored in ActionQueue |

### Performance Benchmarks

| Module | Target | Method |
|--------|--------|--------|
| `email_signal_extractor.py` | <100ms for 500 emails | Regex only; no LLM calls |
| `pattern_engine.py` | <50ms for 30 patterns × 20 domains | Frontmatter YAML parse only |
| `briefing_adapter.py` | <10ms | Reads aggregate stats from memory |
| `nudge_daemon.py` (per check cycle) | <200ms | Frontmatter scan of 3-5 files |
| `attachment_router.py` | <50ms for 100 attachments | Filename regex only |
| `cost_tracker.py` | <5ms | Reads pre-computed metrics |

---

## §8 — Operational Constraints

### Backward Compatibility

- **Zero-downtime:** Each enhancement is feature-flagged. Disabling a flag reverts to
  pre-enhancement behavior with no data loss.
- **State file compatibility:** New state files (`inbox.md`) are additive. No existing
  state file schemas are modified.
- **Config compatibility:** New config files (`patterns.yaml`) are separate files.
  Existing `actions.yaml`, `skills.yaml`, `connectors.yaml` receive only additive
  entries (new signal types, new skill entries).

### Preflight Registration

`scripts/preflight.py` currently uses hardcoded check registration — each check function
is called inline in `run_preflight()` with no pluggable discovery mechanism. New modules
from ACT Reloaded cannot self-register health checks.

**Required change (Wave 0 or Wave 1 prerequisite):** Add a lightweight check registration
mechanism to `preflight.py`:

```python
# Decorator-based registration
_REGISTERED_CHECKS: list[tuple[str, Callable, str]] = []  # (name, fn, severity)

def register_check(name: str, severity: str = "P1"):
    def decorator(fn):
        if fn not in {f for _, f, _ in _REGISTERED_CHECKS}:  # dedup guard for test reloads
            _REGISTERED_CHECKS.append((name, fn, severity))
        return fn
    return decorator

# In run_preflight() — sort registered checks by severity before execution:
for name, fn, severity in sorted(_REGISTERED_CHECKS, key=lambda x: x[2]):
    checks.append(fn())
```

**⚠️ DEDUP GUARD:** The `if fn not in ...` check prevents double-registration when
test suites use `importlib.reload()` to reset module state. Without it, reloaded modules
re-register their checks and preflight produces duplicate check output.

**⚠️ PRIORITY ORDERING:** Registered checks are sorted by severity (`P0` before `P1`)
before execution. Without this sort, a P0 check registered by a new module would run
after all hardcoded P1 checks, violating the P0/P1 ordering contract.

This enables new modules to self-register their health checks without modifying
`run_preflight()` directly. Add to §12 Modified Files Inventory.

### Observability

Each enhancement logs to `health-check.md → catch_up_runs` or its own telemetry section:

```yaml
enhancements:
  email_signal_extractor:
    signals_emitted: N
    false_positives_reported: N  # from user corrections via calibration
    miss_rate_est: N             # from manual actions taken without signal
  pattern_engine:
    patterns_evaluated: N
    patterns_matched: N
    evaluation_time_ms: N
  briefing_adapter:
    adaptations_applied: [list]
    user_overrides: N
  nudge_daemon:
    nudges_sent: N
    nudge_cap_hit: N
    last_nudge_time: ISO-8601
  knowledge_capture:
    items_captured: N
    items_triaged: N
    items_expired: N
  memory_activation:
    facts_total: N
    facts_added_this_session: N
    self_model_updated: true|false
    self_model_chars: N
  decision_tracker:
    active_decisions: N
    decisions_captured: N
    scenarios_evaluated: N
    expired_this_session: N
  relationship_pulse:
    contacts_overdue: N
    occasions_upcoming: N
    last_populated: ISO-8601
  power_half_hour:
    invocations: N
    items_presented: N
    items_completed_after: N
  monthly_retrospective:
    last_generated: ISO-8601
    month_covered: YYYY-MM
  coaching_engine:
    nudges_fired: N
    nudges_dismissed: N
    nudge_types: [list]
    cooldown_active: true|false
```

### Security-by-Design Checklist (per enhancement)

| Control | Applied To | Enforcement |
|---------|-----------|-------------|
| PII guard pre-scan | All new input paths (email signals, /remember text, attachment filenames) | Code: `pii_guard.filter_text()` before any write |
| Encryption at rest | High-sensitivity signal metadata in ActionQueue | Code: `sensitivity: "high"` → age encryption |
| Audit trail | All new write operations | Code: append to `state/audit.md` |
| Rate limiting | /remember writes, nudge sends | Code: per-operation cap in config |
| Autonomy floor | All new action handlers | Code: `autonomy_floor: true` in actions.yaml |
| Scope filtering | Family flash view | Code: `_apply_scope_filter()` before composition |
| Command whitelist | New Telegram commands | Code: add to `_COMMAND_ALIASES` allowlist |
| Signal routing coverage | All signal-emitting modules | Code: every emitted signal type MUST have a `_SIGNAL_ROUTING` entry; `compose()` logs a warning + increments `signal_dropped` counter for unrouted signals; P1 preflight check validates coverage |
| Middleware-routed writes | All new channel_listener write commands | Code: route through PII → WriteGuard → Audit → [write] → WriteVerify → Audit (not direct `open()`) |
| File locking for concurrent writes | All write paths (`_audit_log`, `cmd_items_add`, `cmd_items_done`, nudge daemon) | Code: `fcntl.flock(fd, LOCK_EX)` before read-compute-write; reuse `vault.py` pattern (lines 332-344). Pre-requisite for Wave 2. |

---

## §9 — Risk Registry (Consolidated)

### Critical Risks

| ID | Risk | Enhancement | Likelihood | Impact | Mitigation | Residual Risk |
|----|------|------------|-----------|--------|------------|---------------|
| R-01 | Channel write commands bypass middleware | Pre-existing + 2 | Certain | Medium | `/remember` routes through middleware (PII, WriteGuard, audit); retrofit `/items_add`/`/items_done` to match | Low (after retrofit) |
| R-02 | Daemon extension affects vault encryption reliability | 4 (nudge) | Low | High | Try/catch isolation; vault runs independently of nudge code path | Low |
| R-03 | Meta Business account setup blocks WhatsApp Cloud API | 10 | Medium | Medium | Phase 1 URL scheme remains permanent fallback | Accepted |
| R-03a | Concurrent write safety — no file locking on state file writes | Pre-existing + 4 | Certain | Medium | `_audit_log()` uses raw `open(path, "a")` with no `fcntl.flock()`; `cmd_items_add` has read-then-write race. Add `fcntl.flock(fd, LOCK_EX)` to all write paths before Wave 2; reuse `vault.py` locking pattern (lines 332-344). | Low (after locking) |

### Moderate Risks

| ID | Risk | Enhancement | Likelihood | Impact | Mitigation |
|----|------|------------|-----------|--------|------------|
| R-04 | Email signal false positives create noise | 1 | Medium | Low | Post-marketing-suppression; dedup; user correction loop |
| R-05 | Pattern engine field references break on schema drift | 3 | Medium | High | Preflight validation of pattern conditions |
| R-06 | Adaptive briefing suppresses needed information | 5 | Medium | Medium | Tier-A domains never suppressed; 10-session minimum |
| R-07 | Stale subscription data leads to missed renewals | 6 | High | Medium | Staleness alert when last_updated >90 days |
| R-08 | Family flash leaks sensitive domain data | 7 | Low | High | Allowlist-based composition (not blocklist) |
| R-09 | Attachment filename PII in signal metadata | 9 | Medium | Medium | pii_guard on filenames before routing |

### Low Risks

| ID | Risk | Enhancement | Likelihood | Impact | Mitigation |
|----|------|------------|-----------|--------|------------|
| R-10 | Cost estimation inaccuracy | 8 | High | Low | Label as "est"; document formula |
| R-11 | Cross-platform nudge parity | 4 | Medium | Medium | Phase 1 macOS only; Windows Task Scheduler in Phase 2 |
| R-12 | Performance degradation on high-volume email batches | 1, 9 | Low | Medium | Regex-only; benchmark targets defined |

---

## §10 — Success Metrics

### Per-Enhancement KPIs

| Enhancement | KPI | Target | Measurement |
|------------|-----|--------|-------------|
| 1. Email Signal Extractor | % of actionable emails that generate signals | ≥40% within 30 days | `signals_emitted / actionable_emails_in_briefing` |
| 2. /remember | Items captured between sessions | ≥5/week (usage adoption) | `state/inbox.md → items_captured` monthly |
| 3. Pattern Engine | Patterns that correctly fire vs. false positives | ≥80% precision | `patterns_matched / user_corrections` |
| 4. Proactive Nudges | Nudges that prevent missed deadlines | ≥1/week prevents a miss | User calibration: "Did this nudge help?" |
| 5. Adaptive Briefing | Reduction in user format overrides | ≥30% fewer overrides after 30 catch-ups | `health-check.md → user_overrides` trend |
| 6. Subscription Lifecycle | Renewals surfaced before charge | ≥90% of known subscriptions | Renewal signals vs. billing emails |
| 7. Family Flash | Archana Telegram engagement | ≥3 interactions/week | `channel_listener.py` family-scope command count |
| 8. Cost Telemetry | User runs /cost at least monthly | ≥1/month | Command usage tracking |
| 9. Document Signals | Attachment-routed signals vs. total attachments | ≥30% route correctly | Signal emit rate vs. attachment count |
| 10. WhatsApp Cloud API | Messages sent via Cloud API vs. URL scheme | ≥50% adoption if set up | Handler execution count vs. URL scheme fallback |
| 11. Memory Activation | Facts accumulated in memory.md | ≥10 facts after 10 catch-ups | `memory.md → facts` count |
| 12. Decision Lifecycle | Active decisions tracked vs. observed | ≥80% capture rate | Decisions in state vs. decision-like email threads |
| 13. Relationships | /relationships command returns meaningful data | Non-empty after 5 catch-ups | Populated rows in relationships.md |
| 14. Power Half Hour | /power invoked and items completed | ≥1/week usage adoption | Command frequency + item completion rate |
| 15. Monthly Retrospective | Retrospective generated on 1st of month | 100% (deterministic trigger) | Presence of `summaries/YYYY-MM-retro.md` |
| 16. Coaching Nudge | Nudges that fire vs. sessions run | ≥60% of catch-ups surface a nudge | `health-check.md → coaching_nudge: fired` rate |

### System-Level KPIs

| KPI | Current Baseline | Target (post-Wave 4) |
|-----|-----------------|---------------------|
| Signals per catch-up (from skills + email extractor) | ~5 (skills only) | ~15 (skills + email) |
| Action proposals per catch-up | ~2 (manual by AI) | ~8 (deterministic + AI combined) |
| Time between signal and user action | ~12h (next catch-up) | ~2h (nudge) or ~30s (Telegram inline) |
| Briefing format override rate | ~15% (estimated) | <5% (adaptive intelligence) |
| Cross-domain patterns detected per week | ~3 (AI inconsistent) | ~8 (engine consistent) |

---

## §11 — Appendix: File Inventory (New Files)

| File | Enhancement | Type | Lines (est) |
|------|------------|------|-------------|
| `scripts/email_signal_extractor.py` | 1 | Production | 350 |
| `scripts/pattern_engine.py` | 3 | Production | 400 |
| `scripts/briefing_adapter.py` | 5 | Production | 200 |
| `scripts/nudge_daemon.py` | 4 | Production | 200 |
| `scripts/attachment_router.py` | 9 | Production | 180 |
| `scripts/cost_tracker.py` | 8 | Production | 180 |
| `scripts/actions/whatsapp_cloud_send.py` | 10 | Production | 250 |
| `scripts/self_model_writer.py` | 11 | Production | 120 |
| `scripts/decision_tracker.py` | 12 | Production | 200 |
| `scripts/skills/relationship_pulse.py` | 13 | Production | 120 |
| `scripts/relationship_pulse_view.py` | 13 | Production | 80 |
| `scripts/power_half_hour_view.py` | 14 | Production | 100 |
| `scripts/retrospective_view.py` | 15 | Production | 180 |
| `scripts/coaching_engine.py` | 16 | Production | 150 |
| `config/patterns.yaml` | 3 | Config | 120 |
| `state/inbox.md` | 2 | State (template) | 20 |
| `state/relationships.md` | 13 | State (template) | 30 |
| `tests/unit/test_email_signal_extractor.py` | 1 | Test | 350 |
| `tests/unit/test_pattern_engine.py` | 3 | Test | 500 |
| `tests/unit/test_briefing_adapter.py` | 5 | Test | 200 |
| `tests/unit/test_nudge_daemon.py` | 4 | Test | 150 |
| `tests/unit/test_attachment_router.py` | 9 | Test | 150 |
| `tests/unit/test_cost_tracker.py` | 8 | Test | 120 |
| `tests/unit/test_whatsapp_cloud_send.py` | 10 | Test | 200 |
| `tests/unit/test_knowledge_capture.py` | 2 | Test | 200 |
| `tests/unit/test_family_flash.py` | 7 | Test | 100 |
| `tests/unit/test_self_model_writer.py` | 11 | Test | 100 |
| `tests/unit/test_decision_tracker.py` | 12 | Test | 180 |
| `tests/unit/test_relationship_pulse.py` | 13 | Test | 150 |
| `tests/unit/test_power_half_hour.py` | 14 | Test | 80 |
| `tests/unit/test_retrospective_view.py` | 15 | Test | 120 |
| `tests/unit/test_coaching_engine.py` | 16 | Test | 120 |
| **Total New Files** | | | **32 files** |
| **Total New Production LOC** | | | **~2,710** |
| **Total New Test LOC** | | | **~2,670** |

**Note:** Test LOC estimates are lower bounds. The testing strategy in §7 (requiring happy
path, empty input, PII exposure, boundary, and regression tests per module) may push
actual test LOC 20-40% higher for complex modules like `pattern_engine.py` (7 operators,
compound conditions, YAML config parsing, edge cases) and `email_signal_extractor.py`
(8 pattern categories, false positive tests, dedup tests, performance benchmarks).

---

## §12 — Appendix: Modified Files Inventory

| File | Enhancement(s) | Change Type | Scope |
|------|---------------|-------------|-------|
| `scripts/action_composer.py` | 1, 3, 6, 12 | Add signal routing entries + signal-drop logging | ~50 LOC additive |
| `scripts/channel_listener.py` | 2, 7, 12 | Add /remember + /family + /decision commands | ~255 LOC additive (3 handlers at ~75 LOC each + family scope filter) |
| `scripts/channel_push.py` | 7 | Add family flash builder | ~80 LOC additive |
| `scripts/skills/subscription_monitor.py` | 6 | Extend lifecycle signals | ~100 LOC additive |
| `scripts/skill_runner.py` | 13 | Wire relationship_pulse to actual module | ~10 LOC additive |
| `scripts/domain_index.py` | 13, 14 | Wire /relationships, /power, /items quick to view scripts | ~20 LOC additive |
| `scripts/session_summarizer.py` | 11 | Fix/verify fact_extractor invocation chain | ~10 LOC fix |
| `scripts/preflight.py` | All | Add check registration mechanism + new P1 checks | ~60 LOC additive |
| `state/scenarios.md` | 12 | Replace stub with proper schema | ~30 LOC replacement |
| `config/actions.yaml` | 10 | Add whatsapp_cloud_send action | ~25 LOC additive |
| `config/skills.yaml` | 13 | Add relationship_pulse skill entry | ~10 LOC additive |
| `config/implementation_status.yaml` | All | Register new features | ~80 LOC additive |
| `config/domain_registry.yaml` | 2, 13 | Register inbox + relationships domains | ~25 LOC additive |
| `config/artha_config.yaml` | All | Feature flags for all 16 enhancements | ~35 LOC additive |
| `scripts/channel_listener.py` (retrofit) | Pre-existing tech debt | Route `/items_add`/`/items_done` through middleware | ~80 LOC refactor |

---

*ACT Reloaded v1.3.0 — specs/act-reloaded.md — 2026-03-21*
