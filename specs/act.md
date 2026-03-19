# Artha Action Layer — Definitive Implementation Specification

**Codename:** ACT  
**Version:** 1.3.0  
**Date:** 2026-03-18  
**Author:** Principal Architect  
**Status:** READY FOR IMPLEMENTATION (post-review revision 3)  
**Depends on:** PRD v7.0.6, Tech Spec v3.9.6, UX Spec v2.7.1  
**Supersedes:** PRD §9 (Autonomy Framework) skeleton; Tech Spec §7.4 stub

---

## §0 — Executive Summary

Artha today is a **read–reason–report** system. It ingests email, calendar, and
state data, synthesizes intelligence, and delivers briefings. Then the human
spends 45 minutes executing the recommendations manually — logging into bank
portals, replying to teachers, booking appointments, paying bills.

This spec transforms Artha into a **read–reason–act** system. Artha will be
able to send an email, create a calendar event, pay a bill, file a form, and
negotiate on the user's behalf — with the same safety, auditability, and
human control that govern the existing read path.

### Core Thesis

> **Every alert that Artha surfaces should resolve to an executable action.**
> The briefing is not a report — it is a decision interface where every item
> has an attached verb: pay, send, schedule, file, cancel, dispute, approve.

### Design Principles

1. **Propose, never presume.** Every action begins as a proposal. Execution
   requires explicit human approval (unless pre-approved at Trust Level 2+).
2. **Atomic and reversible.** Each action is a single, auditable operation.
   Where possible, include a reverse action (unsend window, cancel booking).
3. **Fail-open for reads, fail-closed for writes.** A broken connector
   degrades briefing quality. A broken action handler must NEVER execute a
   partial or incorrect action.
4. **The queue is the product.** Actions persist in an approval queue that
   survives sessions, is accessible via Telegram/terminal/web, and is the
   primary user interaction surface.
5. **Privacy is load-bearing.** PII redaction, audit logging, and the
   autonomy floor are structural — not policy. They cannot be bypassed
   by configuration, trust level, or user override.

---

## §1 — AS-IS Architecture (Discovery)

### 1.1 Current Write Capabilities

| Capability | Handler | Scope | Status |
|---|---|---|---|
| Send briefing email | `gmail_send.py` | Gmail API `messages.send` | **Live** |
| Sync open items → To Do | `todo_sync.py --push` | MS Graph Tasks | **Live** |
| Pull To Do completions | `todo_sync.py --pull` | MS Graph Tasks | **Live** |
| Push briefing to Telegram | `channel_push.py` | Telegram Bot API | **Live** |
| Interactive Telegram queries | `channel_listener.py` | Telegram Bot API | **Live** |
| Create calendar event | `calendar_writer.py` | **State file only** (no API write) | **Disabled** |
| Send WhatsApp message | URL scheme open | OS-level (macOS `open` / Windows `start`) | **Manual** |

### 1.2 Current Action Registry (`config/actions.yaml`)

Five actions declared in the YAML registry (`send_email`, `send_whatsapp`,
`add_calendar_event`, `todo_sync`, `run_pipeline`). Only three have working
handlers (`send_email`, `todo_sync`, `run_pipeline`). `add_calendar_event` is
disabled. The instruction-sheet actions (`cancel_subscription`,
`dispute_charge`) are described in prose but not registered as distinct
YAML entries — they do not execute.

### 1.3 Identified Gaps

| Gap | Impact |
|---|---|
| No persistent approval queue | Actions proposed in chat are lost if not approved immediately |
| No action executor framework | Each handler is an ad-hoc script; no common lifecycle |
| No calendar write | Cannot create/modify events (OAuth scope not requested) |
| No email compose/reply (non-briefing) | Cannot send arbitrary email on user's behalf |
| No financial transaction capability | Cannot pay bills, transfer funds, or dispute charges |
| No appointment booking | Cannot schedule healthcare, services, or government appointments |
| No form submission | Cannot file insurance claims, change addresses, or submit applications |
| Telegram has no inline action buttons | Push is one-way; no callback-driven approval UX |
| Trust Level elevation not enforced | `health-check.md` tracks criteria but no gate code exists |
| No rollback/undo capability | Executed actions cannot be reversed |

### 1.4 What We Keep (Zero Regression)

Everything below is **load-bearing infrastructure** that the action layer
must integrate with, not replace:

- **Middleware stack** (`scripts/middleware/`): PII → WriteGuard → Verify → Audit
- **Pipeline orchestrator** (`scripts/pipeline.py`): ConnectorHandler protocol, ThreadPoolExecutor
- **Preflight gate** (`scripts/preflight.py`): 24+ check P0/P1 health system (10 P0, 14+ P1)
- **Checkpoint system** (`scripts/checkpoint.py`): Step-level crash recovery
- **PII guard** (`scripts/pii_guard.py`): Regex-based PII filter on all outbound data
- **Safe CLI wrapper** (`scripts/safe_cli.py`): Outbound query sanitizer for external CLIs
- **Audit trail** (`state/audit.md`): Append-only action log
- **Rate limiter** (`scripts/middleware/rate_limiter.py`): Per-provider sliding-window token bucket
- **Skill runner** (`scripts/skill_runner.py`): Scheduled skill execution with cadence control
- **Channel bridge** (`scripts/channels/`): Telegram adapter with `send_message`, `poll`, `inline_keyboard`

---

## §2 — Architecture Design

### 2.1 Trade-off Analysis

| Option | Description | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A: Fat Actions in YAML** | Extend `actions.yaml` with full handler configs per action | Simple, declarative | Scales poorly; complex actions need code, not YAML | Reject |
| **B: Action Bus + Handler Modules** | SQLite-backed queue; pluggable `ActionHandler` protocol in `scripts/actions/` | Scalable, testable, persistent, auditable, mirrors ConnectorHandler pattern | More code; SQLite adds a dependency (stdlib) | **Selected** |
| **C: Event-Driven Pub/Sub** | Redis/NATS message queue; handlers subscribe to action events | Decoupled, async-native, horizontally scalable | Massive overengineering for single-user system; adds external deps | Reject |

**Decision: Option B — Action Bus with Handler Modules.**

Rationale: Mirrors the proven `ConnectorHandler` protocol pattern
(`scripts/connectors/base.py`), uses Python stdlib SQLite for queue
persistence (zero new dependencies), and fits the existing
skill-runner/pipeline execution model.

### 2.2 Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User Interfaces                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ Terminal  │  │ Telegram │  │  Email    │  │ Web Portal │ │
│  │ (Claude)  │  │ (Bot)    │  │ (digest) │  │ (Phase 2)  │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬──────┘ │
│       │              │             │               │        │
│       └──────────────┴──────┬──────┴───────────────┘        │
│                             │                               │
│                    ┌────────▼────────┐                      │
│                    │  Action Queue   │ ◄── SQLite            │
│                    │  (persistent)   │     state/actions.db  │
│                    └────────┬────────┘                      │
│                             │                               │
│              ┌──────────────┼──────────────┐                │
│              │              │              │                 │
│     ┌────────▼───┐  ┌──────▼─────┐ ┌──────▼──────┐        │
│     │  Approval  │  │  Trust     │ │  PII        │        │
│     │  Gate      │  │  Enforcer  │ │  Firewall   │        │
│     └────────┬───┘  └──────┬─────┘ └──────┬──────┘        │
│              └──────────────┼──────────────┘                │
│                             │                               │
│                    ┌────────▼────────┐                      │
│                    │ Action Executor │                      │
│                    └────────┬────────┘                      │
│                             │                               │
│     ┌───────────┬───────────┼───────────┬───────────┐      │
│     │           │           │           │           │      │
│  ┌──▼──┐  ┌────▼───┐  ┌───▼────┐  ┌───▼────┐ ┌───▼────┐ │
│  │Email│  │Calendar│  │Finance │  │Booking │ │Message │ │
│  │Send │  │Write   │  │Pay     │  │Reserve │ │Send    │ │
│  └──┬──┘  └────┬───┘  └───┬────┘  └───┬────┘ └───┬────┘ │
│     │          │           │           │           │      │
│     └──────────┴───────────┼───────────┴───────────┘      │
│                            │                               │
│                   ┌────────▼────────┐                      │
│                   │   Audit Log     │                      │
│                   │ state/audit.md  │                      │
│                   └─────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 Data Flow (Single Action Lifecycle)

```
1. DETECT    Briefing step / skill / user command identifies actionable signal
         │
2. COMPOSE   ActionComposer builds ActionProposal with all parameters
         │
3. VALIDATE  PII firewall scans all outbound fields; blocks if PII leaks
         │
4. ENQUEUE   Proposal inserted into SQLite queue (status: PENDING)
         │
5. PRESENT   Approval UX renders proposal on active channel(s)
         │        - Terminal: structured text with [Approve] [Modify] [Reject]
         │        - Telegram: inline keyboard callback buttons
         │        - Email: digest of pending actions (daily or on-demand)
         │
6. DECIDE    User approves / modifies / rejects / defers
         │        - Approve → status: APPROVED
         │        - Modify  → status: MODIFYING → user edits → re-validate → PENDING
         │        - Reject  → status: REJECTED, logged, no execution
         │        - Defer   → status: DEFERRED, re-surface at specified time
         │        - Timeout → configurable: auto-reject after 72h (default)
         │
7. ENFORCE   Trust Enforcer checks:
         │        - Current trust level ≥ action's min_trust
         │        - Action not on autonomy floor (or user explicitly approved)
         │        - Rate limit not exceeded for this action type
         │
8. EXECUTE   ActionExecutor calls handler.execute(proposal) in try/except
         │        - Handler returns ActionResult (success/failure/partial)
         │        - Timeout: per-handler configurable (default 30s)
         │        - Retry: configurable per action type (default: no retry for writes)
         │
9. CONFIRM   Result logged to audit; state files updated; user notified
         │        - Success: "✅ Email sent to Mrs. Chen" via active channel
         │        - Failure: "❌ Calendar event failed: auth expired" + remediation
         │        - Partial: "⚠️ Bill payment submitted; confirmation pending"
         │
10. LEARN    Update trust metrics (acceptance rate, false-positive count)
             Store result for autonomy elevation scoring
```

### 2.4 Canonical Lifecycle State Machine

The **ActionQueue** is the sole authoritative owner of action lifecycle
state. Workflow steps, channel adapters, and handlers may observe or
request transitions, but only `ActionQueue` commits lifecycle state.
All other sections of this spec conform to the state machine below.

```
PENDING ──┬──▶ APPROVED ──▶ EXECUTING ──▶ SUCCEEDED
          │                │           │
          │                │           └──▶ FAILED
          │                │
          │                └──▶ CANCELLED    (user cancels after approval but before execution)
          │
          ├──▶ MODIFYING ──▶ PENDING    (edit loop)
          │
          ├──▶ DEFERRED ──▶ PENDING     (re-surface at defer time)
          │
          ├──▶ REJECTED                 (terminal state; user declines proposal)
          │
          └──▶ EXPIRED                  (terminal state; 72h default)

SUCCEEDED ──▶ (undo within deadline) ──▶ reverse action queued as new PENDING
FAILED ──▶ (no auto-retry for writes; user may re-queue)

Terminal states: REJECTED, EXPIRED, SUCCEEDED, FAILED, CANCELLED
```

Valid transitions (exhaustive — any transition not listed here is a bug):

| From | To | Actor | Condition |
|---|---|---|---|
| PENDING | APPROVED | user:terminal, user:telegram, auto:L2 | Trust gate passes; not autonomy-floor (if auto) |
| PENDING | MODIFYING | user:telegram, user:terminal | User taps Edit |
| PENDING | REJECTED | user:*, system:pii | Explicit reject or PII firewall block |
| PENDING | DEFERRED | user:* | User defers; new `expires_at` set |
| PENDING | EXPIRED | system:expiry | `now > expires_at` |
| MODIFYING | PENDING | user:telegram, user:terminal, system:timeout | Edit submitted or 10-min timeout |
| DEFERRED | PENDING | system:scheduler | Defer time reached |
| APPROVED | EXECUTING | system:executor | Immediate; within same transaction |
| APPROVED | CANCELLED | user:terminal, user:telegram | User cancels after approving but before execution starts |
| EXECUTING | SUCCEEDED | system:executor | Handler returns success |
| EXECUTING | FAILED | system:executor | Handler returns failure or timeout |

### 2.5 Consistency Model

| Boundary | Consistency | Mechanism |
|---|---|---|
| Action queue (status transitions) | **Strong (ACID)** | SQLite WAL; single-writer; explicit transactions |
| Audit log (action_audit table) | **Strong (ACID)** | Written in same transaction as status transition |
| Trust metrics | **Strong (ACID)** | SQLite table; updated atomically on execution result |
| External side effects (Gmail, Calendar, Graph) | **Eventual** | Idempotency keys where available; compensating actions (undo) on failure |
| User notifications (Telegram, terminal) | **Best-effort eventual** | Delivery confirmation via Telegram API; terminal is synchronous |
| State file updates (health-check.md, audit.md) | **Eventual** | Written after DB commit; loss is non-critical (DB is source of truth) |

---

## §3 — The Action Queue

### 3.1 Why SQLite, Not State Files

State files (`state/*.md`) are markdown. They work for human-readable,
low-frequency, append-mostly data. An action queue requires:

- **Atomic status transitions** (PENDING → APPROVED → EXECUTED)
- **Concurrent access** (terminal + Telegram listener both read/write)
- **Indexing** (query by status, domain, timestamp, trust level)
- **Transactional integrity** (no partial writes on crash)

SQLite provides all of this with zero external dependencies (Python stdlib).

The queue is the **sole authoritative owner of action lifecycle state**.
Workflow steps, channel adapters, and handlers may observe or request
transitions, but only `ActionQueue` commits lifecycle state to the database.
The canonical state machine is defined in §2.4.

**Location:** `state/actions.db` (gitignored; included in backup rotation)

### 3.2 Schema

```sql
-- All times stored as ISO-8601 UTC strings.
-- IDs are UUIDv4 (no sequential autoincrement — avoids information leakage).

CREATE TABLE IF NOT EXISTS actions (
    id                TEXT PRIMARY KEY,           -- UUIDv4
    created_at        TEXT NOT NULL,              -- ISO-8601 UTC
    updated_at        TEXT NOT NULL,              -- ISO-8601 UTC

    -- Classification
    action_type       TEXT NOT NULL,              -- email_send | calendar_write | ...
    domain            TEXT NOT NULL,              -- finance | kids | health | ...
    friction          TEXT NOT NULL DEFAULT 'standard',  -- low | standard | high
    min_trust         INTEGER NOT NULL DEFAULT 1, -- 0 | 1 | 2

    -- Content (encrypted at rest if sensitivity = high/critical)
    title             TEXT NOT NULL,              -- Human-readable summary ≤120 chars
    description       TEXT,                       -- Extended context for approval UX
    parameters        TEXT NOT NULL,              -- JSON blob: handler-specific params
    sensitivity       TEXT NOT NULL DEFAULT 'standard', -- standard | high | critical

    -- Lifecycle
    status            TEXT NOT NULL DEFAULT 'pending',
        -- pending | modifying | approved | rejected | deferred | executing |
        -- succeeded | failed | expired | cancelled
    approved_at       TEXT,                       -- ISO-8601 UTC
    executed_at       TEXT,                       -- ISO-8601 UTC
    approved_by       TEXT,                       -- 'user:terminal' | 'user:telegram' | 'auto:L2'
    expires_at        TEXT,                       -- Auto-reject after this time

    -- Result
    result_status     TEXT,                       -- success | failure | partial
    result_message    TEXT,                       -- Human-readable outcome
    result_data       TEXT,                       -- JSON: handler-specific result

    -- Provenance
    source_step       TEXT,                       -- catch-up step that created this (e.g. 'step_12')
    source_skill      TEXT,                       -- skill name if skill-originated
    source_domain     TEXT,                       -- triggering domain
    linked_oi         TEXT,                       -- open_items.md OI-NNN reference if applicable

    -- Reversibility
    reversible        INTEGER NOT NULL DEFAULT 0, -- 1 if undo is possible
    reverse_action_id TEXT,                       -- points to the undo action if created
    undo_window_sec   INTEGER                     -- config: seconds; used to compute undo_deadline at execution time
);

CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_domain ON actions(domain);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);
CREATE INDEX IF NOT EXISTS idx_actions_type ON actions(action_type);

-- Audit log for all state transitions (immutable append-only)
CREATE TABLE IF NOT EXISTS action_audit (
    id                TEXT PRIMARY KEY,           -- UUIDv4
    action_id         TEXT NOT NULL REFERENCES actions(id),
    timestamp         TEXT NOT NULL,              -- ISO-8601 UTC
    from_status       TEXT NOT NULL,
    to_status         TEXT NOT NULL,
    actor             TEXT NOT NULL,              -- 'user:terminal' | 'user:telegram' | 'system:executor' | 'system:expiry'
    context           TEXT                        -- JSON: additional metadata (e.g. modification diff)
);

CREATE INDEX IF NOT EXISTS idx_audit_action ON action_audit(action_id);

-- Trust metrics (rolling window for elevation scoring)
CREATE TABLE IF NOT EXISTS trust_metrics (
    id                TEXT PRIMARY KEY,
    action_type       TEXT NOT NULL,
    domain            TEXT NOT NULL,
    proposed_at       TEXT NOT NULL,
    user_decision     TEXT NOT NULL,              -- approved | rejected | modified | deferred
    execution_result  TEXT,                       -- success | failure | null (if not executed)
    feedback          TEXT                        -- optional user feedback
);

CREATE INDEX IF NOT EXISTS idx_trust_type ON trust_metrics(action_type);
```

### 3.3 Encryption at Rest

Actions with `sensitivity = high | critical` have their `parameters`,
`result_data`, and `description` fields encrypted using the same `age`
public key from `config/settings.md → age_recipient`. The
`action_queue.py` module transparently encrypts on write and decrypts on
read using new string-oriented wrappers around the existing file-based
`foundation.age_encrypt()` / `foundation.age_decrypt()` functions.

**Implementation note:** The existing `foundation.age_encrypt(pubkey,
input_path, output_path)` and `foundation.age_decrypt(privkey, input_path,
output_path)` operate exclusively on file `Path` objects. Two new wrapper
functions are required (Phase 0, Step 0.1.5):

```python
# scripts/foundation.py — new functions
def age_encrypt_string(pubkey: str, plaintext: str) -> str:
    """Encrypt a string using age. Writes to temp files internally.
    Returns base64-encoded ciphertext string."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f_in:
        f_in.write(plaintext)
        in_path = Path(f_in.name)
    out_path = in_path.with_suffix('.age')
    try:
        age_encrypt(pubkey, in_path, out_path)
        return base64.b64encode(out_path.read_bytes()).decode('ascii')
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)

def age_decrypt_string(privkey: str, ciphertext_b64: str) -> str:
    """Decrypt a base64-encoded age ciphertext back to plaintext string."""
    raw = base64.b64decode(ciphertext_b64)
    with tempfile.NamedTemporaryFile(suffix='.age', delete=False) as f_in:
        f_in.write(raw)
        in_path = Path(f_in.name)
    out_path = in_path.with_suffix('.txt')
    try:
        age_decrypt(privkey, in_path, out_path)
        return out_path.read_text(encoding='utf-8')
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
```

**Rationale:** Action parameters may contain PII (email addresses, account
numbers, payment details). The same encryption standard used for
`state/*.md.age` files applies here.

### 3.4 SQLite Concurrency Protocol

Terminal (catch-up) and Telegram listener run as separate processes sharing
`state/actions.db`. The following connection protocol is **mandatory** for
all code paths that open the database:

```python
# Every connection must set these pragmas immediately after opening:
conn = sqlite3.connect(str(db_path), timeout=10.0)
conn.execute("PRAGMA journal_mode=WAL")       # Write-Ahead Log for concurrent readers
conn.execute("PRAGMA busy_timeout=5000")       # Wait up to 5s on writer contention
conn.execute("PRAGMA foreign_keys=ON")         # Enforce FK constraints

# Multi-step status transitions must use explicit transactions:
with conn:  # auto-commit on success, rollback on exception
    conn.execute("UPDATE actions SET status=? WHERE id=?", (new_status, action_id))
    conn.execute("INSERT INTO action_audit VALUES (?, ?, ?, ?, ?, ?, ?)", audit_row)
```

**Design rules:**
- WAL mode allows concurrent reads while a single writer holds the lock.
- `busy_timeout=5000` prevents `SQLITE_BUSY` errors under normal contention.
- All status transitions (PENDING → APPROVED → EXECUTING → SUCCEEDED) must
  be atomic: read + validate + update in a single `with conn:` block.
- Connection objects are **not shared across threads**; each thread/process
  opens its own connection with the pragma sequence above.

### 3.5 Expiry and Garbage Collection

- **Default expiry:** 72 hours from creation (configurable per action type).
- **Expiry sweep:** Runs at preflight (Step 0) each catch-up. Transitions
  `pending` → `expired`; logs to audit.
- **Retention:** Executed actions visible for 30 days, then archived to
  `actions_archive` table (same schema, separate table for query performance).
- **DB size guard:** If `actions.db` exceeds 50MB, archive compaction runs
  automatically (delete records > 90 days from archive).

---

## §4 — The ActionHandler Protocol

### 4.1 Interface Definition

```python
"""scripts/actions/base.py — ActionHandler Protocol for Artha action execution."""

from __future__ import annotations
from typing import Any, Dict, Protocol, runtime_checkable
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainSignal:
    """A domain-level detection that may map to an action.

    Signals are produced by skills (deterministic) during Steps 8–11,
    not by LLM inference on raw email. This eliminates prompt injection
    risk at the composition layer.
    """
    signal_type: str                 # "bill_due" | "email_needs_reply" | "birthday_approaching" | ...
    domain: str                      # "finance" | "kids" | "social" | ...
    entity: str                      # "Metro Electric" | "Mrs. Chen" | "Rahul" | ...
    urgency: int                     # 0–3 (matches existing URGENCY scale)
    impact: int                      # 0–3 (matches existing IMPACT scale)
    source: str                      # "skill:bill_due_tracker" | "step_8:ooda" | ...
    metadata: Dict[str, Any]         # Signal-specific data (amounts, dates, thread_ids, etc.)
    detected_at: str                 # ISO-8601 UTC


@dataclass
class ActionProposal:
    """Immutable proposal for a user-reviewable action."""
    id: str                          # UUIDv4
    action_type: str                 # Registry key (e.g. 'email_send')
    domain: str                      # Originating domain
    title: str                       # ≤120 char human summary
    description: str                 # Extended context
    parameters: Dict[str, Any]       # Handler-specific params
    friction: str                    # low | standard | high
    min_trust: int                   # 0 | 1 | 2
    sensitivity: str                 # standard | high | critical
    reversible: bool                 # Can this be undone?
    undo_window_sec: int | None      # Seconds for undo (None = no undo)
    expires_at: str | None           # ISO-8601 UTC expiry
    linked_oi: str | None            # OI-NNN reference


@dataclass
class ActionResult:
    """Outcome of action execution."""
    status: str                      # success | failure | partial
    message: str                     # Human-readable outcome ≤300 chars
    data: Dict[str, Any] | None      # Handler-specific result data
    reversible: bool                 # Was this actually reversible?
    reverse_action: ActionProposal | None  # Pre-built undo proposal if applicable


@runtime_checkable
class ActionHandler(Protocol):
    """Protocol for action handler modules.

    Each module in scripts/actions/ must expose these functions.
    Structural subtyping applies (no subclass required).
    Mirrors ConnectorHandler pattern from scripts/connectors/base.py.
    """

    def validate(self, proposal: ActionProposal) -> tuple[bool, str]:
        """Pre-execution validation. Returns (ok, reason).

        Check that all required parameters are present, auth is valid,
        and the action is safe to execute. This runs BEFORE user approval
        to catch obviously invalid proposals early.

        Must be side-effect free. Must not call external APIs.
        """
        ...

    def dry_run(self, proposal: ActionProposal) -> ActionResult:
        """Simulate execution without side effects.

        Returns what WOULD happen if executed. Used for user preview
        and test suites. External API calls allowed only if they are
        explicitly read-only (e.g. Gmail draft creation, payment preview).
        """
        ...

    def execute(self, proposal: ActionProposal) -> ActionResult:
        """Execute the action. THIS IS THE WRITE PATH.

        Called ONLY after user approval + trust enforcement + PII check.
        Must be idempotent where possible (same proposal ID → same result).

        Timeout: enforced by ActionExecutor (default 30s).
        Exceptions: caught by ActionExecutor; logged as failure.
        """
        ...

    def health_check(self) -> bool:
        """Test that this handler's external dependencies are available.

        Called during preflight (Step 0). Returns True if handler is
        operational. False disables this action type for the session
        with a logged warning.
        """
        ...
```

### 4.2 Handler Registration

Handlers are registered in `config/actions.yaml` and loaded dynamically by
the `ActionExecutor`, mirroring `pipeline.py`'s `_HANDLER_MAP` pattern:

```yaml
# config/actions.yaml — extended schema (v2.0)
schema_version: "2.0"

actions:
  email_send:
    handler: "scripts/actions/email_send.py"
    enabled: true
    friction: standard           # individual review each time
    min_trust: 1                 # requires Trust Level 1+
    sensitivity: standard
    timeout_sec: 30
    retry: false                 # no retry for sent emails
    reversible: true
    undo_window_sec: 30          # 30-second "unsend" window (Gmail supports this)
    rate_limit:
      max_per_hour: 20
      max_per_day: 100
    pii_check: true
    pii_allowlist: ["to", "cc", "bcc", "recipient_name"]
    audit: true
    autonomy_floor: true         # ALWAYS requires human approval regardless of trust level
    description: "Send email via Gmail API on behalf of user"

  email_reply:
    handler: "scripts/actions/email_reply.py"
    enabled: true
    friction: standard
    min_trust: 1
    sensitivity: standard
    timeout_sec: 30
    retry: false
    reversible: true
    undo_window_sec: 30
    rate_limit:
      max_per_hour: 20
      max_per_day: 100
    pii_check: true
    pii_allowlist: ["to", "cc", "bcc", "recipient_name"]
    audit: true
    autonomy_floor: true
    description: "Reply to email thread via Gmail API"

  calendar_create:
    handler: "scripts/actions/calendar_create.py"
    enabled: true
    friction: low                # batch-approvable
    min_trust: 1
    sensitivity: standard
    timeout_sec: 15
    retry: true
    retry_max: 2
    reversible: true
    undo_window_sec: 3600        # 1 hour to undo (delete created event)
    rate_limit:
      max_per_hour: 30
      max_per_day: 100
    pii_check: false             # calendar events rarely contain PII
    # pii_allowlist: not required when pii_check is false
    audit: true
    autonomy_floor: false        # CAN be pre-approved at Trust Level 2
    description: "Create Google Calendar event"

  calendar_modify:
    handler: "scripts/actions/calendar_modify.py"
    enabled: true
    friction: standard
    min_trust: 1
    sensitivity: standard
    timeout_sec: 15
    retry: true
    retry_max: 2
    reversible: true
    undo_window_sec: 3600
    rate_limit:
      max_per_hour: 20
      max_per_day: 50
    pii_check: false
    audit: true
    autonomy_floor: false
    description: "Modify or reschedule existing Google Calendar event"

  reminder_create:
    handler: "scripts/actions/reminder_create.py"
    enabled: true
    friction: low
    min_trust: 1
    sensitivity: standard
    timeout_sec: 15
    retry: true
    retry_max: 2
    reversible: true
    undo_window_sec: 86400       # 24 hours
    rate_limit:
      max_per_hour: 30
      max_per_day: 200
    pii_check: false
    audit: true
    autonomy_floor: false
    description: "Create reminder in Microsoft To Do"

  whatsapp_send:
    handler: "scripts/actions/whatsapp_send.py"
    enabled: true
    friction: standard
    min_trust: 1
    sensitivity: standard
    timeout_sec: 15
    retry: false
    reversible: false            # WhatsApp messages cannot be recalled via API
    rate_limit:
      max_per_hour: 10
      max_per_day: 50
    pii_check: true
    pii_allowlist: ["phone_number", "recipient_name"]
    audit: true
    autonomy_floor: true
    description: "Send WhatsApp message via Cloud API"

  todo_sync:
    handler: "scripts/actions/todo_sync_action.py"
    enabled: true
    friction: low
    min_trust: 0                 # works at Trust Level 0 (automated sync)
    sensitivity: standard
    timeout_sec: 30
    retry: true
    retry_max: 2
    reversible: false
    rate_limit:
      max_per_hour: 10
      max_per_day: 50
    pii_check: false
    audit: true
    autonomy_floor: false
    description: "Bidirectional sync with Microsoft To Do"

  instruction_sheet:
    handler: "scripts/actions/instruction_sheet.py"
    enabled: true
    friction: low
    min_trust: 0                 # read-only; generates text only
    sensitivity: standard
    timeout_sec: 10
    retry: false
    reversible: false
    rate_limit:
      max_per_hour: 30
      max_per_day: 200
    pii_check: false
    audit: true
    autonomy_floor: false
    description: "Generate step-by-step instruction sheet (no external execution)"
```

**v2.0 Schema Additions (compared to v1.0):** The following fields are NEW
in v2.0 and do not exist in the current v1.0 schema: `handler` (path
format change), `min_trust`, `sensitivity`, `timeout_sec`, `retry`,
`retry_max`, `reversible`, `undo_window_sec`, `rate_limit`,
`autonomy_floor`, `pii_allowlist`. The `friction` vocabulary changes from
`low | medium | standard` to `low | standard | high`. The migration
script (Phase 0 Step 0.5.1) handles these additions and renames.

### 4.3 Handler Security Allowlist

Following the `pipeline.py` precedent, action handlers are loaded via an
explicit allowlist — never arbitrary module paths:

```python
# scripts/actions/__init__.py
_HANDLER_MAP: dict[str, str] = {
    "email_send":        "scripts.actions.email_send",
    "email_reply":       "scripts.actions.email_reply",
    "calendar_create":   "scripts.actions.calendar_create",
    "calendar_modify":   "scripts.actions.calendar_modify",
    "reminder_create":   "scripts.actions.reminder_create",
    "whatsapp_send":     "scripts.actions.whatsapp_send",
    "todo_sync":         "scripts.actions.todo_sync_action",
    "instruction_sheet": "scripts.actions.instruction_sheet",
}
```

User plugins from `~/.artha-plugins/actions/` are loaded with the same
structural-subtyping check used by `skill_runner.py` for plugin skills.

---

## §5 — The Action Executor

### 5.1 `scripts/action_executor.py` — Core Engine

```
ActionExecutor(artha_dir: Path)
    ├── .queue          → ActionQueue (SQLite wrapper)
    ├── .trust          → TrustEnforcer (reads health-check.md autonomy block)
    ├── .pii_firewall   → runs pii_scan (boolean block; never redacts — see §7.1)
    ├── .rate_limiter   → ActionRateLimiter (new; per-action-type limits)
    ├── .read_only      → bool from detect_environment.filesystem_writable
    ├── .handlers       → dict[str, ActionHandler] loaded from actions.yaml
    │
    ├── propose(action_type, domain, title, params, ...) → ActionProposal
    │       1. Build ActionProposal from parameters
    │       2. Validate via handler.validate()
    │       3. PII scan all string fields in params
    │       4. Enqueue with status=PENDING
    │       5. Return proposal (for immediate presentation)
    │
    ├── approve(action_id, approved_by) → ActionResult
    │       0. If read_only: block with "Read-only environment; execution disabled"
    │       1. Load proposal from queue
    │       2. TrustEnforcer.check(proposal, approved_by)
    │       3. PII firewall re-scan (params may have been modified)
    │       4. Transition to EXECUTING
    │       5. handler.execute(proposal) with timeout
    │       6. Transition to SUCCEEDED | FAILED
    │       7. Log to audit (both DB and state/audit.md)
    │       8. Update state files if needed (via middleware stack)
    │       9. Return ActionResult
    │
    ├── reject(action_id, reason) → None
    │       Transition to REJECTED; log reason to audit
    │
    ├── defer(action_id, until: str) → None
    │       Transition to DEFERRED; set new expires_at
    │
    ├── undo(action_id) → ActionResult
    │       1. Check `undo_deadline` (ISO-8601 UTC) has not passed
    │          (`undo_deadline` = `executed_at` + `undo_window_sec`;
    │           computed and stored at execution time)
    │       2. Load reverse_action from result
    │       3. Execute reverse action (same pipeline)
    │       4. Link reverse_action_id on original
    │
    ├── pending() → list[ActionProposal]
    │       Return all PENDING + DEFERRED (past defer time) proposals
    │
    ├── history(days=7) → list[ActionRecord]
    │       Return executed/rejected actions for review
    │
    └── expire_stale() → int
            Sweep PENDING actions past expires_at → EXPIRED; return count
```

### 5.2 Integration with Catch-Up Workflow

The action executor integrates at three points in the existing 19-step
catch-up workflow (defined in `config/workflow/`):

**Step 0c (Preflight — new sub-step):**
```
Run action handler health checks.
For each enabled action in actions.yaml:
    if handler.health_check() fails:
        disable action type for this session
        log P1 warning (non-blocking)
Sweep expired actions (expire_stale()).
```

**Step 12.5 (Finalize — Alerts — new sub-step, after existing Step 12):**
```
For each domain alert produced in Steps 8-11:
    If alert maps to a known action_type:
        composer = ActionComposer(alert, domain_state, user_profile)
        proposal = composer.build()
        executor.propose(proposal)
```

**Step 14 (Finalize — Briefing synthesis — extended with terminal approval UX):**
```
Append to briefing:
    "━━ ⚡ PENDING ACTIONS ━━━━━━"
    For each pending action:
        "[action.title] — [friction] — [Approve] [Reject]"
Include pending count in briefing header.
Terminal users review and approve/reject inline during catch-up.
```

**Step 14.5 (Finalize — Telegram push — new sub-step, after briefing):**
```
Push pending actions to Telegram with inline keyboards.
Each action is a separate message with [Approve] [Edit] [Reject] [Defer] buttons.
```

### 5.3 Integration with Channel Listener

The Telegram channel listener (`scripts/channel_listener.py`) already
processes inbound messages. Extend it with callback query handling:

```python
# In channel_listener.py poll loop, handle callback_query updates:
if update.get("callback_query"):
    data = update["callback_query"]["data"]
    # data format: "act:APPROVE:action_id" | "act:REJECT:action_id" | "act:DEFER:action_id"
    parts = data.split(":", 2)
    if len(parts) == 3 and parts[0] == "act":
        verb, action_id = parts[1], parts[2]
        if verb == "APPROVE":
            result = executor.approve(action_id, approved_by="user:telegram")
            # Reply with result confirmation
        elif verb == "REJECT":
            executor.reject(action_id, reason="user:telegram:rejected")
        elif verb == "DEFER":
            executor.defer(action_id, until="+24h")
```

---

## §6 — Trust Enforcement

### 6.1 Trust Level Gate

The `autonomy:` block in `health-check.md` stores the current trust level
and elevation metrics. **This block does not exist yet** — it must be
created as part of Phase 0 (Step 0.3.5). Initial schema:

```yaml
# state/health-check.md — new section (created by Phase 0)
autonomy:
  trust_level: 0                    # 0 (observe) | 1 (propose) | 2 (pre-approve)
  trust_level_since: "2026-03-18"   # ISO date when current level was set
  days_at_level: 0
  acceptance_rate_90d: 0.0
  critical_false_positives: 0
  pre_approved_categories: []
  last_demotion: null
  last_elevation: null
```

The `TrustEnforcer` reads this block and enforces it:

```python
class TrustEnforcer:
    """Enforces trust level gates on action execution."""

    def check(self, proposal: ActionProposal, approved_by: str) -> tuple[bool, str]:
        """
        Returns (allowed, reason).

        Rules:
        1. If action is on autonomy_floor → require explicit human approval
           regardless of trust level. 'auto:L2' approval BLOCKED.
        2. If current_trust_level < proposal.min_trust → BLOCKED.
        3. If approved_by == 'auto:L2' and action.friction == 'high' → BLOCKED.
        4. Rate limit check: action type count in last hour/day ≤ limits.
        """
        ...
```

### 6.2 Autonomy Floor — Non-Negotiable

These actions **always** require explicit human approval. This cannot be
overridden by trust level, configuration, or any code path:

| Category | Examples | Rationale |
|---|---|---|
| **Communications sent as user** | email_send, email_reply, whatsapp_send | Identity impersonation risk |
| **Financial transactions** | bill_pay, transfer, dispute | Irreversible monetary loss |
| **Immigration actions** | document_submit, attorney_email | Visa status consequences |
| **Actions affecting others** | Send to spouse, modify shared calendar | Consent boundary |
| **Deletion of data** | Delete email, remove calendar event | Data loss risk |

**Implementation:** `autonomy_floor: true` in `actions.yaml` is read-only.
The `TrustEnforcer` hard-codes a check that `autonomy_floor` actions are
NEVER auto-approved:

```python
if action_config.get("autonomy_floor", False):
    if "auto:" in approved_by:
        return (False, "AUTONOMY_FLOOR: action requires explicit human approval")
```

### 6.3 Trust Elevation Enforcement

The existing criteria in `health-check.md` are now enforced in code:

```python
def evaluate_elevation(self) -> dict:
    """Check if trust level should be elevated.

    Returns dict with:
        eligible: bool
        current_level: int
        target_level: int
        criteria_met: dict[str, bool]
        blocker: str | None
    """
    metrics = self.queue.trust_metrics_summary()

    if self.current_level == 0:
        return {
            "eligible": (
                metrics["days_at_level"] >= 30
                and metrics["critical_false_positives"] == 0
                and metrics["briefing_accuracy_30d"] >= 0.95
                and metrics["all_recommendations_reviewed"]
            ),
            "target_level": 1,
            ...
        }
    elif self.current_level == 1:
        return {
            "eligible": (
                metrics["days_at_level"] >= 60
                and metrics["acceptance_rate_90d"] >= 0.90
                and len(metrics["pre_approved_categories"]) > 0
            ),
            "target_level": 2,
            ...
        }
```

### 6.4 Trust Demotion (Automatic)

```
IF any action execution causes:
    - Financial loss (confirmed by user feedback)
    - Wrong recipient on communication
    - Critical false positive (immigration/health alert that was wrong)
THEN:
    Immediately demote to Trust Level 0
    Disable all pre-approved categories
    Log incident to audit with full context
    Notify user: "Artha has been demoted to Observer mode due to [reason].
                  All actions now require manual approval."
    Reset elevation clock to zero
```

---

## §7 — PII Firewall for Actions

### 7.1 Outbound PII Scanning

Every action proposal passes through the PII firewall before enqueueing
AND again before execution (in case parameters were modified during
approval):

```python
def pii_check_proposal(proposal: ActionProposal) -> tuple[bool, list[str]]:
    """Scan all string fields in proposal.parameters for PII.

    Returns (clean, findings).
    - clean=True: no PII detected; safe to proceed.
    - clean=False: PII found; findings lists types (e.g. ['SSN', 'CC']).

    Does NOT redact — blocks the action entirely. Unlike state file writes
    (where PII is redacted and written), outbound actions with PII must be
    reviewed by the user before any data leaves the system.

    Exception: The action's INTENDED recipient fields (to_email, phone_number)
    are excluded from PII scanning since they are inherently personal data
    that the user explicitly provided.
    """
    ...
```

### 7.2 Allowlisted Fields

Some action parameters inherently contain personal data that is the
*purpose* of the action (e.g., the `to` field on an email). These are
allowlisted per action type:

```yaml
# In actions.yaml, per action:
pii_allowlist:
  - "to"              # email recipient
  - "recipient_name"  # greeting addressee
  - "phone_number"    # WhatsApp recipient
```

All other string fields are scanned. If PII is detected in non-allowlisted
fields, the action is blocked with a user-visible warning:

```
⚠️ Action blocked: PII detected in email body
   Found: SSN pattern in message content
   Please review and remove sensitive data before re-submitting.
```

---

## §8 — Phase 1 Action Handlers (Detailed Specifications)

### 8.1 `email_send` — Compose and Send Email

**Handler:** `scripts/actions/email_send.py`  
**API:** Gmail API `messages.send` (OAuth2 scope: `gmail.send` — already authorized)  
**Existing code:** Wraps and extends `scripts/gmail_send.py`

**Parameters:**
```json
{
    "to": "teacher@school.edu",
    "subject": "Re: Parent Conference March 25",
    "body": "Hi Mrs. Chen, Thursday at 3:30 works perfectly for us...",
    "cc": null,
    "bcc": null,
    "in_reply_to": "message_id_of_original_thread",
    "thread_id": "gmail_thread_id",
    "draft_first": true
}
```

**Execution flow:**
1. If `draft_first: true` (default for friction=standard): Create Gmail
   draft via `drafts.create`, return draft URL for user review.
2. On subsequent approval: Convert draft to send via `drafts.send`.
3. If `draft_first: false` (pre-approved template sends): `messages.send`
   directly.

**Undo:** Gmail supports `messages.modify` to move sent message to Trash
within 30 seconds. The handler stores `message_id` and `undo_deadline`
(ISO-8601 UTC) in `result_data`. The undo deadline is calculated as:
`undo_deadline = executed_at + undo_window_sec`, accounting for execution
and notification latency. The reverse action calls `messages.trash`.

**Undo UX:** The confirmation message includes a countdown:
`"✅ Email sent to Mrs. Chen — Undo available: 22s remaining"`
After the window: `"This action cannot be undone."`

**Dry run:** Creates Gmail draft via `drafts.create`; returns draft URL +
preview text in `ActionResult.data`. Does not send. The draft is visible in
the user's Gmail Drafts folder and must be manually deleted if not approved.

**Assumption A-8.1:** Gmail API `gmail.send` scope is already authorized
in the existing OAuth flow (`scripts/google_auth.py`).  
**Test:** `python scripts/google_auth.py --check-scopes` — verify `gmail.send`
is in the authorized scope list.

### 8.2 `email_reply` — Reply to Existing Thread

**Handler:** `scripts/actions/email_reply.py`  
**API:** Gmail API `messages.send` with `threadId` and `In-Reply-To` headers

**Parameters:**
```json
{
    "thread_id": "18e2abc...",
    "in_reply_to": "<original-message-id@mail.gmail.com>",
    "body": "Thanks for confirming. See you Thursday at 3:30.",
    "reply_all": false,
    "draft_first": true
}
```

**Key behavior:** The handler fetches the original thread to extract the
`To`, `Cc`, `Subject`, and `References` headers. These are auto-populated —
the user only provides the reply body.

**Dry run:** Same as `email_send` — creates Gmail draft in thread context
via `drafts.create` with proper `threadId` and `In-Reply-To` headers.
Returns draft URL. Does not send.

**Assumption A-8.2:** Thread context is available from the pipeline's
email fetch (stored in `tmp/pipeline_output.jsonl`).  
**Test:** Verify `thread_id` field exists in pipeline JSONL output for Gmail records.

### 8.3 `calendar_create` — Create Calendar Event

**Handler:** `scripts/actions/calendar_create.py`  
**API:** Google Calendar API `events.insert`  
**OAuth scope:** `calendar.events` (read-write) — **already authorized** in
`scripts/google_auth.py` via `CALENDAR_SCOPES`. The `config/connectors.yaml`
file only documents `calendar.readonly` and must be updated to reflect
the actual authorized scopes.

**Parameters:**
```json
{
    "summary": "Trisha Soccer Game",
    "start": "2026-03-22T10:00:00-07:00",
    "end": "2026-03-22T11:30:00-07:00",
    "location": "Sammamish Commons",
    "description": "Bring water bottles and camp chairs",
    "calendar_id": "primary",
    "reminders": {"useDefault": false, "overrides": [{"method": "popup", "minutes": 60}]},
    "attendees": []
}
```

**Undo:** `events.delete(eventId)` within `undo_window_sec` (1 hour).

**Dry run:** Returns preview of the event that would be created (summary,
start/end, location) without calling `events.insert`. No API call made.

**Verified (A-8.3):** `scripts/google_auth.py` already includes
`calendar.events` in `CALENDAR_SCOPES`. No re-consent flow is needed.
The only change is updating `config/connectors.yaml` to document the
write scope:

```yaml
# config/connectors.yaml — update (documentation only; no runtime impact)
google_calendar:
  auth:
    scopes:
      - "https://www.googleapis.com/auth/calendar.readonly"
      - "https://www.googleapis.com/auth/calendar.events"   # already authorized
```

### 8.4 `calendar_modify` — Reschedule or Update Event

**Handler:** `scripts/actions/calendar_modify.py`  
**API:** Google Calendar API `events.patch`

**Parameters:**
```json
{
    "event_id": "abc123def",
    "calendar_id": "primary",
    "updates": {
        "start": {"dateTime": "2026-03-25T15:30:00-07:00"},
        "end": {"dateTime": "2026-03-25T16:30:00-07:00"}
    }
}
```

**Undo:** The handler stores the original event state in `result_data`.
The reverse action patches back to the original values.

**Dry run:** Fetches the current event via `events.get` (read-only) and
returns a diff preview of proposed changes. No mutation.

### 8.5 `reminder_create` — Create To Do / Reminder

**Handler:** `scripts/actions/reminder_create.py`  
**API:** Microsoft Graph Tasks API (already authorized via `scripts/todo_sync.py`)  
**Existing code:** Extends `scripts/todo_sync.py` push path

**Parameters:**
```json
{
    "title": "Call Mom",
    "due_date": "2026-03-22",
    "list_name": "Artha",
    "priority": "normal",
    "body": "Ask about July visit dates",
    "reminder_datetime": "2026-03-22T10:00:00-07:00"
}
```

**Assumption A-8.5:** MS Graph `Tasks.ReadWrite` scope is already authorized.  
**Test:** `python scripts/todo_sync.py --status` verifies write access.

**Dry run:** Returns preview of the To Do item (title, due date, list) that
would be created. No API call made.

### 8.6 `whatsapp_send` — Send WhatsApp Message

**Handler:** `scripts/actions/whatsapp_send.py`  
**API:** WhatsApp Cloud API (Meta Business Platform)

**Parameters:**
```json
{
    "phone_number": "+1425XXXXXXX",
    "recipient_name": "Rahul",
    "message": "Happy Birthday, Rahul! 🎂 Hope you have an amazing day!",
    "message_type": "text"
}
```

**Pre-requisite:** WhatsApp Cloud API requires a Meta Business account and
approved phone number. This is a **Phase 2 action** — Phase 1 falls back to
the existing URL scheme approach with an improved UX:

**Phase 1 fallback (no API setup):**
```python
# Generate wa.me deep link with pre-filled message
url = f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"
# On macOS: subprocess.run(["open", url])
# On Windows: subprocess.run(["start", url], shell=True)
# Result: WhatsApp opens with message pre-filled; user taps Send
```

**Phase 2 (API setup):** Full programmatic send via Cloud API with delivery
confirmation. `autonomy_floor: true` regardless.

**Assumption A-8.6:** Phase 1 URL scheme works on macOS and Windows when
WhatsApp desktop is installed.  
**Test:** Generate URL, open in default browser, verify WhatsApp opens.

**Dry run:** Returns the pre-filled `wa.me` URL (Phase 1) or message preview
(Phase 2). Does not open URL or send message.

### 8.7 `instruction_sheet` — Step-by-Step Guide Generation

**Handler:** `scripts/actions/instruction_sheet.py`  
**API:** None (LLM-generated text, saved locally)

**Parameters:**
```json
{
    "task": "cancel_subscription",
    "service": "Netflix",
    "context": {
        "current_plan": "Premium $22.99/mo",
        "billing_date": "2026-04-01",
        "account_email": "user@example.com"
    }
}
```

**Output:** Generates a step-by-step markdown guide saved to
`tmp/instructions/{task}_{service}_{date}.md`. Delivered inline in
briefing and via Telegram if channel is active.

This handler has `min_trust: 0` and `autonomy_floor: false` — it can
be generated at any trust level without approval since it produces
only text (no external side effects).

**Dry run:** Returns a brief 2–3 line summary of what the instruction sheet
would cover. Does not invoke the LLM or generate the full guide.

---

## §9 — Phase 2 Action Handlers (Design Specifications)

These handlers require additional external API integrations. Each is
designed here for future implementation.

### 9.1 `bill_pay` — Pay a Bill

**API options (trade-off matrix):**

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Plaid Transfer API | Direct ACH; wide bank coverage | $0.30/transfer; requires Plaid commercial account | **Selected for Phase 2** |
| Bank bill-pay (per-bank API) | Free; native | Each bank is different; no standard | Reject (fragmentation) |
| Checkbook.io | Digital check sending | Niche; trust concerns | Reject |

**Parameters:**
```json
{
    "payee": "Metro Electric",
    "amount": 300.63,
    "currency": "USD",
    "from_account": "chase_checking_****4523",
    "due_date": "2026-03-21",
    "reference": "Account #XXXXX1234",
    "payment_method": "ach"
}
```

**Safety constraints:**
- `autonomy_floor: true` — ALWAYS requires human approval
- `friction: high` — NEVER batch-approvable
- **Amount confirmation:** User must see exact amount in approval UX
- **Duplicate detection:** If same payee + amount within 48 hours, flag as potential duplicate
- **Daily limit:** Configurable cap (default: $2,000/day aggregate)
- **Two-factor requirement:** If available, require 2FA before execution

**Assumption A-9.1:** Plaid Link flow requires a one-time web-based
OAuth setup to connect bank accounts. This is a user-initiated setup
step, not automated.  
**Test:** Plaid sandbox environment for transaction simulation.

### 9.2 `appointment_book` — Schedule an Appointment

**API options:**

| Provider | Coverage | API | Verdict |
|---|---|---|---|
| Zocdoc | Healthcare | REST API | **Phase 2A** |
| Calendly | Professional | REST API | Phase 2B |
| Yelp Reservations | Restaurants | REST API | Phase 2B |
| Email-based | Universal | Compose email with templated request | **Phase 1** (via email_send) |

**Phase 1 approach (email-based):**
```
Artha detects: "Vehicle oil change due (last: 2025-12-15, interval: 5000mi)"
Artha composes: Email to Jiffy Lube Issaquah with service request
User approves → email_send handler executes
```

This leverages the existing `email_send` handler — no new API required.
The intelligence is in the **detection and composition**, not the execution.

### 9.3 `address_change` — Multi-Provider Address Update

**The scenario:** User moves. Artha knows every institution with the old
address (from state files). Artha generates one action per institution:

```
Action Queue:
  1. ✉️  USPS Change of Address — [File form] [$1.10 identity verification]
  2. ✉️  Chase Bank — [Send secure message]
  3. ✉️  Allstate Insurance — [Send policy update email]
  4. ✉️  Tesla STEM HS — [Update emergency contact form]
  5. ✉️  Dr. Patel's Office — [Send update email]
  6. 📅  Vehicle Registration — [Schedule DMV appointment]
  7. ✉️  County Voter Registration — [Submit online form]
```

Each is a separate action in the queue. The user batch-approves low-friction
items and individually reviews high-friction items. Progress is tracked
per-item.

**Implementation:** This is a **composite action** — a workflow that
generates multiple atomic actions. The `ActionComposer` has a
`compose_workflow` method for multi-step scenarios.

### 9.4 `negotiate` — Advocacy on User's Behalf

**Phase 1 (instruction sheet + draft):**
```
Artha detects: Medical bill $3,200 from Overlake Medical Center
Artha pulls: CMS fair price data for CPT codes (public)
Artha generates:
  1. instruction_sheet: "How to Dispute Medical Bill"
  2. email_send: Draft dispute letter citing No Surprises Act + fair price data
```

**Phase 2 (direct submission):**
Submit dispute via provider's patient portal (MyChart FHIR write API).

**Phase 3 (AI negotiation agent):**
Telephony API (Twilio) + AI voice agent for bill negotiation calls.
This is the 100x feature — but Phase 3 with extensive safety testing.

---

## §10 — The Action Composer

### 10.1 Purpose

The Action Composer is the bridge between **domain intelligence** (what Artha
knows) and **action proposals** (what Artha can do). It maps detected signals
to appropriate actions:

```python
class ActionComposer:
    """Maps domain alerts and signals to ActionProposals.

    The Composer does NOT decide whether to execute — it only structures
    the proposal. The human (or L2 pre-approval) decides execution.

    TRIGGER MECHANISM: Signals are produced by skills (deterministic)
    during Steps 8–11, not by LLM inference on raw email. Each skill
    that detects an actionable condition returns a DomainSignal via
    its output dict. The pipeline collects these signals and passes
    them to the Composer at Step 12.5.

    This design eliminates prompt injection risk at the composition
    layer — signals are structured data from deterministic skills,
    not free-text parsed by an LLM.
    """

    def compose(self, signal: DomainSignal) -> ActionProposal | None:
        """Convert a domain signal into an action proposal.

        Returns None if no action is appropriate for this signal.
        """
        ...

    def compose_workflow(self, trigger: str, context: dict) -> list[ActionProposal]:
        """Generate a multi-action workflow (e.g. address change, tax prep).

        Returns a list of proposals that form a logical workflow.
        Each proposal is independent and individually approvable.
        """
        ...
```

### 10.2 Signal-to-Action Mapping

| Domain Signal | Action Type | Friction | Example |
|---|---|---|---|
| Bill due in ≤7 days | `bill_pay` (Phase 2) or `instruction_sheet` (Phase 1) | high | "Metro Electric $300.63 due Mar 21" |
| Email requiring response | `email_reply` | standard | "Mrs. Chen: parent conference confirmation" |
| Appointment needed | `email_send` (request) or `appointment_book` (Phase 2) | standard | "Oil change overdue by 500 miles" |
| Birthday in ≤7 days | `whatsapp_send` or `email_send` | standard | "Rahul's birthday Mar 22" |
| Calendar conflict detected | `calendar_modify` | standard | "Dentist overlaps with team standup" |
| Subscription renewing (unused) | `instruction_sheet` (cancel guide) | low | "Netflix renewing Apr 1, unused 3 months" |
| Missing school assignment | `email_send` (to child or teacher) | standard | "Trisha: 2 missing assignments in Math" |
| Medical bill above fair price | `email_send` (dispute letter) + `instruction_sheet` | standard | "Overlake bill $3,200 vs $1,800 fair" |
| Insurance renewal in 30 days | `instruction_sheet` (comparison) | low | "Auto insurance renewing May 1" |
| Open item aging >14 days | `reminder_create` (escalation) | low | "OI-034: schedule furnace inspection" |
| Immigration deadline approaching | `email_send` (attorney) | high | "H-4 EAD window opens in 90 days" |
| Vehicle recall detected | `instruction_sheet` + `appointment_book` | standard | "NHTSA recall for 2022 Model Y" |
| Property tax due | `instruction_sheet` (Phase 1) or `bill_pay` (Phase 2) | high | "King County $1,300 due Apr 30" |
| Prescription refill due | `instruction_sheet` (Phase 1) | standard | "Rx refill due in 5 days" |

### 10.3 Composition Rules

1. **One signal → at most one action.** A bill-due alert creates ONE
   payment proposal, not a payment + a reminder + an email.
2. **Deduplication:** If an action for the same (signal_type, entity, date)
   already exists in the queue with status `pending` or `deferred`, do not
   create a duplicate.
3. **Friction inheritance:** The composed action's friction is the MAXIMUM
   of (signal's domain sensitivity, action type's base friction).
4. **Cross-domain escalation:** If a signal touches immigration or finance,
   the composed action inherits `friction: high` regardless of action type.
5. **Sensitivity-friction interaction:** `effective_friction = max(
   action_type.friction, sensitivity_implied_friction)` where
   `high`/`critical` sensitivity implies `friction: standard` minimum.
   A `sensitivity: high` action is never batch-approvable even if the
   action type declares `friction: low`. The ActionComposer sets
   `sensitivity` per-instance based on the presence of sensitive data
   in the signal's metadata (e.g., financial amounts, medical codes).

---

## §11 — The Approval UX

### 11.1 Terminal (Claude Code / Claude CLI)

During catch-up, pending actions are presented after the briefing:

```
━━ ⚡ PENDING ACTIONS (3) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 📧 Reply to Mrs. Chen — Parent Conference     [standard]
   Re: confirming Thursday 3:30pm availability
   ────
   "Hi Mrs. Chen, Thursday at 3:30 works perfectly for our
   family. Looking forward to discussing Trisha's progress.
   Best regards, Ved"
   ────
   Say "approve 1" · "edit 1" · "reject 1" · "defer 1"

2. 📅 Add Trisha Soccer Game                     [low]
   Saturday Mar 22, 10:00–11:30 AM @ Sammamish Commons
   Say "approve 2" · "reject 2"

3. 💊 Rx Refill Reminder                         [low]
   Create To Do: "Refill Lisinopril — due Mar 25"
   Say "approve 3" · "reject 3"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Batch: "approve all low" approves items 2,3 (low-friction only).
Review: "show queue" to see full pending queue.
```

### 11.2 Telegram (Inline Keyboards)

Each pending action is delivered as a separate Telegram message with
inline keyboard buttons:

```
📧 Reply to Mrs. Chen — Parent Conference

"Hi Mrs. Chen, Thursday at 3:30 works perfectly for our family.
Looking forward to discussing Trisha's progress. Best regards, Ved"

[✅ Send] [✏️ Edit] [❌ Reject] [⏰ Defer]
```

Button callbacks route to the action executor via the channel listener:

```
callback_data: "act:APPROVE:{action_id}"
callback_data: "act:REJECT:{action_id}"
callback_data: "act:DEFER:{action_id}"
```

For "Edit": Artha transitions the action to `status: MODIFYING` in the
queue and replies "Send your edited version:".

**Modify flow state machine:**
```
User taps [✏️ Edit]
    │
    ├── Action transitions: PENDING → MODIFYING
    ├── Artha replies: "Send your edited version:"
    ├── 10-minute timeout starts
    │
    ├── User sends text message:
    │   ├── Replace body in proposal parameters
    │   ├── Re-run PII firewall on modified params
    │   ├── MODIFYING → PENDING (with updated params)
    │   └── Re-present with [✅ Send Edited] [❌ Cancel]
    │
    ├── User sends non-text command (e.g. /status):
    │   ├── Cancel edit: MODIFYING → PENDING (original params)
    │   └── Process the command normally
    │
    └── Timeout (10 minutes):
        ├── MODIFYING → PENDING (original params)
        └── Artha replies: "Edit timed out. Action remains pending."
```

**Constraint:** Cross-channel modification is not supported in Phase 1.
The edit must complete on the same channel where it was initiated.

### 11.3 Email Digest (Phase 2)

For users who prefer email-based approval, a daily "Pending Actions" email
is sent 30 minutes after the briefing email:

```
Subject: Artha · 3 Actions Pending Your Approval

You have 3 pending actions. Reply with the number to approve:

1. Reply to Mrs. Chen — "approve 1" or "reject 1"
2. Add Trisha Soccer Game — "approve 2"
3. Rx Refill Reminder — "approve 3"

Reply "approve all low" to batch-approve low-friction items.
```

**Implementation:** Inbound email parsing via Gmail API `messages.list` with
subject filter. This reuses the existing Gmail connector's read path.

---

## §12 — Workflow Integration Points

### 12.1 Modified Catch-Up Workflow

The existing 19-step workflow gains four new sub-steps (no existing steps
removed or reordered):

```
Phase 1 — Preflight
  Step 0:  Environment detection
  Step 0a: Vault decryption
  Step 0b: To Do pull
  Step 0c: ◀ NEW — Action handler health checks + queue expiry sweep
           (checkpoint: not checkpointed; runs pre-pipeline)
  Step 1:  Health-check read
  Step 2:  Digest mode check

Phase 2 — Fetch
  Steps 3–4e: (unchanged)

Phase 3 — Process
  Steps 5–7b: (unchanged)

Phase 4 — Reason
  Steps 8–11: (unchanged)

Phase 5 — Finalize
  Step 12:  Alert synthesis
  Step 12.5: ◀ NEW — Action composition (signals → proposals)
  Step 13:  Action proposals (existing — now uses ActionComposer)
  Step 14:  Briefing synthesis + terminal approval UX (pending actions section)
  Step 14a: Email send
  Step 14b: Spouse briefing
  Step 14.5: ◀ NEW — Pending actions push to Telegram (inline keyboards)
  Step 15–19: (unchanged)
  Step 19.5: ◀ NEW — Action queue status snapshot to health-check.md
```

### 12.2 New Catch-Up Checkpoint Steps

The checkpoint system (`scripts/checkpoint.py`) is extended with action
steps. **Note:** `write_checkpoint()` accepts `last_step: int | float`,
not string keys. Sub-steps use float values to preserve ordering without
breaking the existing numeric type contract:

```python
# Checkpoint step mapping (float sub-steps within existing integer sequence)
# 12.5 = action composition (between step 12 and 13)
# 14.5 = action push (between step 14 and 15)
# 19.5 = action health snapshot (after step 19)
ACTION_CHECKPOINT_STEPS = {
    12.5: "action_composition",
    14.5: "action_push",
    19.5: "action_health_snapshot",
}
```

If a crash occurs after Step 12.5, recovery skips action composition (actions
already in queue). If a crash occurs after Step 14.5, the push is skipped
(actions already delivered).

### 12.3 Channel Listener Command Extensions

New commands for the interactive Telegram listener:

| Command | Response |
|---|---|
| `/queue` | List all pending actions with inline keyboards |
| `/queue history` | Last 10 executed/rejected actions with results |
| `/approve {id}` | Approve specific action (text fallback for inline) |
| `/reject {id}` | Reject specific action |
| `/approve all low` | Batch-approve all pending low-friction actions |
| `/undo {id}` | Undo last executed action (within window) |

**Integration requirement:** These commands must be added to
`_COMMAND_ALIASES` and `ALLOWED_COMMANDS` in `scripts/channel_listener.py`.
The callback verbs (`act:APPROVE`, `act:REJECT`, `act:DEFER`) route through
the `callback_query` path and bypass the command whitelist, but text
commands `/queue`, `/approve`, `/reject`, `/undo` must be explicitly
allowlisted or they will be silently rejected by the security layer.

---

## §13 — Security Architecture

### 13.1 Threat Model

| Threat | Vector | Mitigation |
|---|---|---|
| **Prompt injection via email** | Malicious email body triggers AI to compose harmful action | PII firewall + human approval gate; AI cannot bypass approval |
| **Queue poisoning** | Attacker gains write access to SQLite DB | DB file is 0600 permissions; inside OneDrive sync (Microsoft auth); encrypted fields for sensitive params |
| **Replay attack** | Re-execute a previously approved action | UUIDv4 IDs; idempotency check in handlers; duplicate detection |
| **Privilege escalation** | Auto-approve high-friction action | Trust enforcer hard-codes autonomy floor; cannot be overridden by config |
| **PII exfiltration via action** | Compose email whose body contains SSN/CC from state files | PII firewall scans all non-allowlisted fields before enqueue AND before execute |
| **Rate limiting bypass** | Flood action queue to overwhelm rate limits | Per-type hourly+daily caps enforced in TrustEnforcer; queue size hard limit (1000) |
| **Stale token execution** | Handler uses expired OAuth token | `health_check()` at preflight; token refresh before execution; fail-closed on auth error |
| **Social engineering via Telegram** | Attacker sends commands to Telegram bot | Chat ID allowlist in channels.yaml; unknown senders rejected at Layer 2 |
| **Man-in-the-middle** | Intercept action parameters in transit | All API calls over HTTPS; Telegram Bot API uses TLS; SQLite is local |

### 13.2 Defense-in-Depth Layers

```
Layer 1: PII Firewall         — Blocks PII in outbound action params
Layer 2: Trust Enforcer        — Validates trust level + autonomy floor
Layer 3: Human Approval Gate   — Explicit approve/reject/modify
Layer 4: Handler Validation    — handler.validate() pre-checks
Layer 5: Rate Limiter          — Per-type hourly/daily caps
Layer 6: Idempotency Guard     — Duplicate detection by proposal hash
Layer 7: Audit Trail           — Immutable log of all transitions
Layer 8: Undo Window           — Reversible actions within time limit
```

**Layer 6 contract:** Idempotency operates at two levels:
1. **Proposal deduplication (queue layer):** `ActionQueue.propose()` checks
   for an existing PENDING or DEFERRED action matching the same
   `(action_type, domain, entity)` tuple from the `DomainSignal` source —
   this is the §10.3 Rule 2 deduplication. Duplicates are silently skipped.
2. **Execution idempotency (handler layer):** Handlers must use
   `proposal.id` as the external API idempotency key where the API
   supports it (e.g., Gmail `X-Gm-Message-State` header, Plaid
   `idempotency_key`). Where the external API does not support
   idempotency keys, the executor checks for an existing SUCCEEDED
   action with the same `proposal.id` and returns the cached
   `result_data` instead of re-executing.

### 13.3 Principle of Least Privilege

Each action handler requests only the minimum API scopes needed:

| Handler | Required Scope | Existing? |
|---|---|---|
| email_send | `gmail.send` | ✅ Yes |
| email_reply | `gmail.send` + `gmail.readonly` | ✅ Yes |
| calendar_create | `calendar.events` | ✅ Yes (already in `google_auth.py CALENDAR_SCOPES`) |
| calendar_modify | `calendar.events` | ✅ Yes (already in `google_auth.py CALENDAR_SCOPES`) |
| reminder_create | `Tasks.ReadWrite` | ✅ Yes |
| whatsapp_send | None (URL scheme) or Cloud API | N/A |
| todo_sync | `Tasks.ReadWrite` | ✅ Yes |
| instruction_sheet | None | ✅ N/A |

**Action item:** Update `config/connectors.yaml` to document the
`calendar.events` scope that is already authorized in `google_auth.py`.
All required scopes are already authorized — no re-consent flow needed.

---

## §14 — Testing Strategy

### 14.1 Unit Tests

Each handler must have:

| Test Category | What It Validates |
|---|---|
| `test_validate_*` | Parameter validation catches missing/invalid fields |
| `test_dry_run_*` | Dry run returns expected preview without side effects |
| `test_execute_*` | Mocked API call; verify request params match proposal |
| `test_execute_failure_*` | API error → ActionResult with status=failure |
| `test_health_check_*` | Health check returns True/False correctly |
| `test_pii_block_*` | PII in non-allowlisted field → blocked |
| `test_pii_allow_*` | PII in allowlisted field (e.g. `to`) → passes |
| `test_idempotency_*` | Same proposal ID → same result (no double-send) |
| `test_undo_*` | Undo within window succeeds; undo after window fails; deadline-based check |

### 14.1.1 Additional Unit Tests for New Components

| Test Category | What It Validates |
|---|---|
| `test_domain_signal_*` | DomainSignal construction, frozen immutability |
| `test_action_rate_limiter_*` | Per-action-type hourly/daily caps enforced independently |
| `test_age_encrypt_string_*` | Round-trip string encryption/decryption; temp file cleanup |
| `test_modify_flow_*` | PENDING → MODIFYING → PENDING state transitions; 10-min timeout |

### 14.2 Integration Tests

| Test | Scope | Environment |
|---|---|---|
| Queue lifecycle | Propose → approve → execute → audit | Local SQLite |
| Trust enforcement | L0 user attempts L1 action → blocked | Local |
| Autonomy floor | L2 auto-approve on floor action → blocked | Local |
| Telegram callback | Inline keyboard → action execution | Telegram test bot |
| Email send e2e | Compose → draft → send → verify receipt | Gmail sandbox |
| Calendar create e2e | Create → verify → undo → verify deletion | Google Calendar sandbox |
| Expiry sweep | Create pending; advance clock; verify expired | Local |
| Crash recovery | Create action at Step 12.5; crash; resume; verify queue intact | Local |

### 14.3 Safety Tests (Red Team)

| Test | Expected Outcome |
|---|---|
| Inject `SSN: 123-45-6789` into email body params | PII firewall blocks; action not queued |
| Submit 200 email_send actions in 1 hour | Rate limiter blocks after 20; remaining rejected |
| Auto-approve `email_send` at Trust Level 2 | Blocked by autonomy floor |
| Queue 1001 actions | 1001st rejected with QUEUE_FULL error |
| Telegram message from unknown chat_id with `/approve` | Rejected at Layer 2 sender validation |
| Modify action params to include malicious URL | PII firewall doesn't catch this (not PII); but human approval gate protects — the user sees the URL before approving |
| Execute with expired OAuth token | `health_check()` returns False; action blocked before execution; user sees auth remediation |

---

## §15 — Observability

### 15.1 Metrics for `health-check.md`

New section appended to `state/health-check.md`:

```yaml
action_queue:
  total_pending: 3
  total_approved_today: 7
  total_rejected_today: 1
  total_failed_today: 0
  total_executed_today: 7
  queue_size: 3
  oldest_pending_hours: 2.5
  handlers_healthy:
    email_send: true
    calendar_create: true
    reminder_create: true
    whatsapp_send: false          # WhatsApp API not configured
  trust_level: 1
  trust_level_days: 42
  elevation_eligible: false
  elevation_blocker: "acceptance_rate_90d: 0.87 (need ≥0.90)"
```

### 15.2 Audit Trail Entries

All action state transitions logged to `state/audit.md`:

```
[2026-03-18T08:30:00Z] ACTION_PROPOSED | id:a1b2c3 | type:email_reply | domain:kids | title:"Reply to Mrs. Chen" | friction:standard
[2026-03-18T08:31:15Z] ACTION_APPROVED | id:a1b2c3 | by:user:telegram | latency:75s
[2026-03-18T08:31:17Z] ACTION_EXECUTED | id:a1b2c3 | result:success | message_id:18e2def | undo_until:2026-03-18T08:31:47Z
```

### 15.3 Action Queue in Briefing

Pending actions appear in every briefing (standard, flash, digest):

```
━━ ⚡ ACTION QUEUE ━━━━━━━━
Pending: 3 | Executed today: 7 | Failed: 0
Oldest: "Reply to Mrs. Chen" (2.5 hours ago)
```

### 15.4 Distributed Tracing (OpenTelemetry)

The action write path is instrumented with OpenTelemetry spans to enable
end-to-end tracing from signal detection through execution. This is
gated behind a feature flag (`harness.actions.tracing.enabled`).

**Span model:**

| Span Name | Parent | When |
|---|---|---|
| `action.detect` | (root) | Signal detected during domain reasoning |
| `action.compose` | `action.detect` | ActionComposer builds proposal |
| `action.propose` | `action.compose` | Proposal validated + enqueued |
| `action.approve` | (root) | User or auto-approve triggers execution |
| `action.execute` | `action.approve` | Handler.execute() call |
| `action.undo` | (root) | User triggers undo within deadline |
| `action.expire` | (root) | Expiry sweep transitions action |

**Required span attributes:**

| Attribute | Type | Example |
|---|---|---|
| `action.id` | string | `"a1b2c3d4-..."` |
| `action.type` | string | `"email_send"` |
| `action.domain` | string | `"kids"` |
| `action.friction` | string | `"standard"` |
| `action.status_from` | string | `"pending"` |
| `action.status_to` | string | `"approved"` |
| `approval.channel` | string | `"telegram"` |
| `handler.name` | string | `"scripts.actions.email_send"` |
| `handler.duration_ms` | float | `1250.0` |
| `action.success` | bool | `true` |

**Trace linkage:** The `action.propose` span ID is persisted into the
`action_audit.context` JSON field, enabling trace reconstruction from
the audit log. When `action.approve` fires (potentially hours later), it
creates a span link back to the original `action.propose` span.

**Exporter configuration:**

```yaml
# config/artha_config.yaml
harness:
  actions:
    tracing:
      enabled: false               # Off by default; opt-in
      exporter: "otlp"             # "otlp" | "console" | "none"
      endpoint: "http://localhost:4317"  # OTLP gRPC endpoint
      service_name: "artha-actions"
```

**Sampling policy:**
- **Failures:** Always sampled (`action.success = false`)
- **Success:** Probabilistic 10% sampling (configurable)
- **High-friction actions:** Always sampled regardless of outcome

**Dependencies:** `opentelemetry-api` and `opentelemetry-sdk` are optional
dependencies. If not installed, tracing is a no-op (the feature flag gates
import). This is not a runtime requirement.

---

## §16 — Migration Plan (Strangler Fig)

### Phase 0: Foundation (This sprint — no user-visible changes)

| Step | Deliverable | Risk | Mitigation |
|---|---|---|---|
| 0.1 | `scripts/actions/base.py` — Protocol + dataclasses (incl. `DomainSignal`) | None | Pure Python; no imports |
| 0.1.5 | `scripts/foundation.py` — Add `age_encrypt_string()` / `age_decrypt_string()` wrappers | Low: uses existing age functions | Unit test: round-trip encrypt/decrypt on known string |
| 0.2 | `scripts/action_queue.py` — SQLite queue wrapper | Low: new file, no integrations | Comprehensive unit tests; DB is gitignored |
| 0.3 | `scripts/trust_enforcer.py` — Trust gate logic | Low: reads health-check.md only | Unit tests with mock health-check data |
| 0.3.5 | Create `autonomy:` block in `state/health-check.md` | None: additive YAML section | Schema defined in §6.1; write-guarded by existing middleware |
| 0.4 | `scripts/action_executor.py` — Core engine | Medium: ties queue + trust + handlers | Integration tests with mock handlers |
| 0.5 | Migrate `config/actions.yaml` schema v1.0 → v2.0 | Low: additive schema change | Backward-compatible; v1 actions still parse |
| 0.5.1 | `scripts/migrate_actions_yaml.py` — v1→v2 auto-migrator | Low | Reads v1.0, writes v2.0 with defaults for new fields; maps `send_email`→`email_send` name normalization; maps `friction: "medium"`→`"standard"`; preserves user customizations. Run once; idempotent. |
| 0.6 | Add `actions.db` to backup rotation in `config/user_profile.yaml` | None | Single line addition |
| 0.7 | Add `state/*.db` pattern to `.gitignore` | None | Covers actions.db + future SQLite files |

### Phase 1A: First Handlers (Next sprint — user sees actions)

| Step | Deliverable | Risk | Mitigation |
|---|---|---|---|
| 1.1 | `scripts/actions/email_send.py` | Low: wraps existing gmail_send.py | Test with draft mode first |
| 1.2 | `scripts/actions/email_reply.py` | Low: extends email_send with thread context | Thread ID validation in validate() |
| 1.3 | `scripts/actions/reminder_create.py` | Low: wraps existing todo_sync.py push | Test with existing To Do lists |
| 1.4 | `scripts/actions/instruction_sheet.py` | None: text-only output | No external APIs |
| 1.5 | Terminal approval UX in catch-up Step 14 | Medium: modifies workflow | Feature-flagged; disable reverts to current |
| 1.6 | Telegram inline keyboard approval (Step 14.5) | Medium: extends channel_listener.py | Feature-flagged; callback handling isolated |
| 1.6.1 | Add `/queue`, `/approve`, `/reject`, `/undo` to `_COMMAND_ALIASES` + `ALLOWED_COMMANDS` in `channel_listener.py` | Low | Required for text-based action commands; callback verbs bypass whitelist |
| 1.6.2 | Update `config/workflow/finalize.md` Step 13 — replace legacy proposal format with `ActionComposer` invocation | Low | Documentation change; Step 13 currently uses old format from Artha.md §9 |

### Phase 1AB: Calendar Write (Config update only — merged into Phase 1A)

| Step | Deliverable | Risk | Mitigation |
|---|---|---|---|
| 1.7 | Update `config/connectors.yaml` to document existing `calendar.events` scope | **None: scope already authorized** | Verify via `scripts/google_auth.py --check-scopes` |
| 1.8 | `scripts/actions/calendar_create.py` | Low: scope already authorized | Undo via events.delete |
| 1.9 | `scripts/actions/calendar_modify.py` | Low | Store original event for undo |

**Note:** `scripts/google_auth.py` already requests `calendar.events` in
`CALENDAR_SCOPES`. No re-consent flow is needed. Phase 1B was originally
scoped as a separate phase due to assumed scope change risk; this has been
validated as a config-only update and merged into Phase 1A.

### Phase 2: Financial & External Actions

| Step | Deliverable | Risk | Mitigation |
|---|---|---|---|
| 2.1 | Plaid Link integration + bill_pay handler | **High: financial transactions** | Sandbox testing; daily limits; always human-gated |
| 2.2 | WhatsApp Cloud API integration | Medium: Meta Business setup | Fallback to URL scheme if not configured |
| 2.3 | `ActionComposer` signal-to-action mapping | Medium: reliability of mapping | Conservative: only high-confidence signals |
| 2.4 | Composite workflows (address change, tax prep) | Medium: multi-action coordination | Each sub-action is independent and individually approvable |

### Phase 3: Advocacy & Autonomy

| Step | Deliverable | Risk | Mitigation |
|---|---|---|---|
| 3.1 | Medical bill dispute (CMS fair price lookup + draft) | Low: text generation + email_send | Instruction sheet + email; no direct submission |
| 3.2 | Insurance appeal generation | Low: text generation + email_send | Same pattern as 3.1 |
| 3.3 | Trust Level 2 pre-approval engine | **High: autonomous execution** | Extensive trust metric validation; demotion guards; 90-day L1 minimum |
| 3.4 | Negotiation letter generation | Low: text generation | No external execution in Phase 3 |

---

## §17 — Assumptions Register

| ID | Assumption | Validation Method | Status |
|---|---|---|---|
| A-8.1 | Gmail `gmail.send` scope is already authorized | `python scripts/google_auth.py --check-scopes` | **Verified ✅** — `gmail.send` is in `GMAIL_SCOPES` |
| A-8.2 | Pipeline JSONL includes `thread_id` for Gmail records | `grep thread_id tmp/pipeline_output.jsonl` | **To verify** |
| A-8.3 | Google Calendar `calendar.events` scope already authorized | `python scripts/google_auth.py --check-scopes` | **Verified ✅** — `calendar.events` is in `CALENDAR_SCOPES`; connectors.yaml doc-only update needed |
| A-8.5 | MS Graph `Tasks.ReadWrite` scope is authorized | `python scripts/todo_sync.py --status` | **To verify** |
| A-8.6 | WhatsApp URL scheme opens on macOS + Windows | Manual test on both platforms | **To verify** |
| A-9.1 | Plaid sandbox supports ACH transfer simulation | Plaid API docs review | **To verify** |
| A-SQL | Python `sqlite3` stdlib supports concurrent R/W with WAL mode | `sqlite3.connect(db, isolation_level=None); PRAGMA journal_mode=WAL` | **High confidence** — stdlib guarantee |
| A-TG | Telegram inline keyboards work with Artha bot | Test callback_query handling with existing bot token | **Verified ✅** — `poll()` already enables `callback_query` in `allowed_updates` |
| A-ENC | `foundation.age_encrypt()` works on arbitrary strings (not just files) | Review implementation | **Verified ❌** — file-only; string wrappers added to Phase 0 Step 0.1.5 |
| A-MID | Existing middleware stack can be reused for action audit logging | Middleware operates on state files, not SQLite; need separate audit path | **Confirmed: need parallel audit, not reuse** |
| A-CHK | Checkpoint system supports string sub-step keys | Review `write_checkpoint()` signature | **Verified ❌** — `int \| float` only; using float sub-steps (12.5, 14.5, 19.5) |

---

## §18 — Configuration

### 18.1 Feature Flags

All action layer features gate behind the existing `harness` config pattern
in `config/artha_config.yaml`:

```yaml
harness:
  # ... existing flags ...

  # Phase ACT: Action execution layer
  actions:
    enabled: true                # Master kill switch for entire action layer
    queue:
      enabled: true
      db_path: "state/actions.db"
      max_queue_size: 1000
      default_expiry_hours: 72
      archive_after_days: 30
    approval:
      terminal: true             # Show pending actions in catch-up
      telegram: true             # Push pending actions with inline keyboards
      email_digest: false        # Phase 2: email-based approval
      batch_low_friction: true   # Allow "approve all low" command
    trust:
      enforce: true              # Enable trust level gates
      auto_demotion: true        # Auto-demote on critical failure
    tracing:
      enabled: false             # OpenTelemetry tracing (opt-in; see §15.4)
      exporter: "otlp"           # otlp | console | none
      endpoint: "http://localhost:4317"
      service_name: "artha-actions"
    handlers:
      email_send: true
      email_reply: true
      calendar_create: true      # calendar.events scope already authorized
      calendar_modify: true       # calendar.events scope already authorized
      reminder_create: true
      whatsapp_send: true
      todo_sync: true
      instruction_sheet: true
      bill_pay: false            # Phase 2
      appointment_book: false    # Phase 2
```

### 18.2 Per-User Configuration (`config/user_profile.yaml`)

```yaml
# New section in user_profile.yaml
actions:
  daily_financial_limit_usd: 2000
  pre_approved_categories: []    # Populated at Trust Level 2
  preferred_approval_channel: "telegram"  # telegram | terminal | email
  auto_reject_after_hours: 72
  notification_on_execute: true  # Notify via preferred channel after execution
```

---

## §19 — Risks and Mitigations

| # | Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|---|
| R-1 | **Financial loss from incorrect payment** | Critical | Low (human-gated) | Autonomy floor; amount confirmation; daily limits; duplicate detection |
| R-2 | **Wrong recipient on email** | High | Medium | Draft-first default; full preview in approval UX; PII scan |
| R-3 | **OAuth token expiry during execution** | Medium | Medium | Health check at preflight; token refresh before execute; fail-closed |
| R-4 | **SQLite corruption from concurrent access** | Medium | Low | WAL journal mode; single-writer pattern; backup rotation |
| R-5 | **Telegram API rate limiting** | Low | Medium | Exponential backoff (existing); batch pending actions in single message if >10 |
| R-6 | **User overwhelmed by action proposals** | Medium | Medium | Signal-to-action mapping is conservative (high-confidence only); batch-approve for low-friction; configurable: disable per domain |
| R-7 | **Trust Level 2 executes wrong action** | High | Low | 90-day L1 minimum; 90% acceptance rate required; auto-demotion on failure; autonomy floor for communications/finance |
| R-8 | **Prompt injection triggers unintended action** | High | Low | AI composes but cannot execute; human must approve; PII firewall catches data exfil |
| R-9 | **Action queue grows unbounded (spam)** | Low | Low | max_queue_size: 1000; expiry sweep at every preflight |
| R-10 | **Backward incompatibility with existing workflow** | Medium | Low | All new steps are additive sub-steps; feature-flagged; disable reverts to current behavior |
| R-11 | **connectors.yaml scope documentation outdated** | Low | Low | Update to match actual `google_auth.py` scopes; no runtime impact |
| R-12 | **Undo window creates false sense of security** | Low | Medium | Clear UX: countdown display ("Undo: 22s"); after window: "This action cannot be undone"; deadline stored as `undo_deadline` in `result_data` |

---

## §20 — File Manifest

### New Files

| Path | Purpose |
|---|---|
| `specs/act.md` | This specification |
| `scripts/actions/base.py` | ActionHandler protocol + dataclasses + DomainSignal |
| `scripts/actions/email_send.py` | Gmail send handler |
| `scripts/actions/email_reply.py` | Gmail thread reply handler |
| `scripts/actions/calendar_create.py` | Google Calendar event creation |
| `scripts/actions/calendar_modify.py` | Google Calendar event modification |
| `scripts/actions/reminder_create.py` | MS To Do reminder creation |
| `scripts/actions/whatsapp_send.py` | WhatsApp message send (URL scheme + Cloud API) |
| `scripts/actions/todo_sync_action.py` | To Do sync as ActionHandler (wraps todo_sync.py) |
| `scripts/actions/instruction_sheet.py` | Step-by-step guide generator |
| `scripts/action_queue.py` | SQLite-backed persistent action queue (with concurrency protocol) |
| `scripts/action_executor.py` | Core action execution engine |
| `scripts/action_composer.py` | Signal-to-action mapping logic (DomainSignal → ActionProposal) |
| `scripts/trust_enforcer.py` | Trust level gate enforcement |
| `scripts/action_rate_limiter.py` | Per-action-type rate limiting (separate from per-provider middleware) |
| `scripts/migrate_actions_yaml.py` | One-time v1.0 → v2.0 actions.yaml schema migrator |
| `scripts/schemas/action.py` | Pydantic schemas for ActionProposal, ActionResult |
| `tests/test_action_queue.py` | Queue lifecycle unit tests |
| `tests/test_action_executor.py` | Executor integration tests |
| `tests/test_trust_enforcer.py` | Trust gate unit tests |
| `tests/test_email_send_handler.py` | Email send handler tests |
| `tests/test_calendar_handler.py` | Calendar handler tests |
| `tests/test_pii_firewall_actions.py` | PII scanning for action params |
| `tests/test_safety_redteam.py` | Red team safety tests |

### Modified Files

| Path | Change |
|---|---|
| `config/actions.yaml` | Schema v2.0 with handler configs, friction, trust, rate limits |
| `config/artha_config.yaml` | Add `harness.actions` feature flag block |
| `config/user_profile.yaml` | Add `actions:` config section |
| `config/user_profile.schema.json` | Add action config schema validation |
| `config/workflow/finalize.md` | Document Steps 12.5, 14.5, 19.5 |
| `config/workflow/preflight.md` | Document Step 0c |
| `config/implementation_status.yaml` | Add action handler feature entries |
| `scripts/preflight.py` | Add Step 0c: action health checks + expiry sweep |
| `scripts/checkpoint.py` | Add float checkpoint steps 12.5, 14.5, 19.5 |
| `scripts/channel_listener.py` | Add callback_query handling for action approval |
| `scripts/channel_push.py` | Add pending actions section to push output |
| `scripts/actions/__init__.py` | Add _HANDLER_MAP allowlist |
| `scripts/google_auth.py` | Verify `calendar.events` scope (already present; no code change) |
| `scripts/connectors/google_calendar.py` | Update connector docs to note write scope |
| `scripts/foundation.py` | Add `age_encrypt_string()` / `age_decrypt_string()` wrappers |
| `state/health-check.md` | Add `autonomy:` block (trust level, elevation metrics) |
| `.gitignore` | Add `state/*.db` pattern |
| `config/user_profile.yaml` → `backup.state_files` | Add `actions.db` |

---

## §21 — Success Criteria

### Phase 1 (Handlers Live)

- [ ] User can approve/reject email action from Telegram with inline keyboard
- [ ] User can approve/reject email action from terminal during catch-up
- [ ] `email_send` handler creates Gmail draft on approve; sends on confirm
- [ ] `email_reply` handler threads correctly (In-Reply-To, References headers)
- [ ] `reminder_create` handler creates To Do item in correct list
- [ ] `instruction_sheet` handler generates actionable cancel/dispute guides
- [ ] PII firewall blocks SSN/CC in email body parameters
- [ ] Autonomy floor prevents auto-approval of email actions at any trust level
- [ ] Action queue survives process crash and session restart
- [ ] All handler health checks run at preflight without blocking catch-up
- [ ] Audit trail captures all state transitions (propose → approve → execute)

### Phase 1B (Calendar Write — Merged into Phase 1A)

- [ ] `config/connectors.yaml` updated to document `calendar.events` scope
- [ ] `calendar_create` handler creates event visible in Google Calendar
- [ ] `calendar_modify` handler reschedules event correctly
- [ ] Undo within 1 hour deletes/reverts created/modified event
- [ ] Existing calendar read path (connector) is unaffected

### Phase 2 (Financial + External)

- [ ] Bill payment via Plaid sandbox succeeds with correct amount and payee
- [ ] Daily financial limit enforced; 21st dollar over limit rejected
- [ ] WhatsApp Cloud API sends message with delivery confirmation
- [ ] Address change workflow generates correct multi-action queue
- [ ] Composite workflows are individually approvable (no forced bundling)

### Phase 3 (Advocacy + Autonomy)

- [ ] Medical bill dispute letter includes correct CPT codes and fair pricing
- [ ] Trust Level 2 auto-approves pre-approved categories only
- [ ] Trust Level 2 correctly blocked by autonomy floor for email/finance
- [ ] Auto-demotion triggers on critical failure within 1 catch-up cycle
- [ ] 90-day elevation requirement enforced; premature elevation blocked

---

## §22 — Glossary

| Term | Definition |
|---|---|
| **Action** | A discrete operation that modifies state in an external system (send email, create event, pay bill) |
| **Action Bus** | The complete propose → queue → approve → execute → audit pipeline |
| **Action Composer** | Maps domain signals to action proposals |
| **Action Handler** | Module implementing the ActionHandler protocol for a specific action type |
| **Approval Queue** | SQLite-backed persistent store of pending action proposals |
| **Autonomy Floor** | Set of action categories that ALWAYS require human approval, regardless of trust level |
| **Friction** | Classification of how much review an action requires: low (batch), standard (individual), high (always individual + never pre-approved) |
| **Trust Level** | User's current autonomy tier: L0 (observe), L1 (propose + approve), L2 (pre-approved execute) |
| **Pre-approved** | At Trust Level 2, specific action types can auto-execute without per-instance approval |
| **MODIFYING** | Transient queue status during user edit; times out to PENDING after 10 minutes (see §2.4, §11.2) |
| **Undo Deadline** | `undo_deadline = executed_at + undo_window_sec` (ISO-8601 UTC). The single source of truth for undo eligibility. Stored in `result_data`. |
| **Reverse Action** | A pre-built undo proposal stored with the executed action's result |
| **Signal** | A domain-level detection (bill due, birthday approaching, assignment missing) that may map to an action. Represented by the `DomainSignal` dataclass. Produced by deterministic skills, not LLM inference. |

---

*End of specification.*
