# ACTIONS-RELOADED — Closing the Action Layer Gap
<!-- pii-guard: ignore-file -->

> **Version:** 1.3.0 | **Status:** Approved for Implementation | **Date:** March 31, 2026
> **Implements:** Action Layer fixes identified in deep dive analysis (2026-03-31)
> **Revised:** v1.3.0 — Architect review: 16 codebase cross-validation findings (2026-03-31)
> **Prerequisite reads:** `config/actions.yaml`, `config/workflow/finalize.md §12.5`,
> `scripts/action_composer.py`, `scripts/action_executor.py`, `scripts/email_signal_extractor.py`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Goal State](#2-goal-state)
3. [Root Cause Analysis](#3-root-cause-analysis)
4. [Design Principles](#4-design-principles)
5. [Architecture](#5-architecture)
6. [Implementation Plan](#6-implementation-plan)
   - [Phase 0: Validate Assumptions](#phase-0-validate-assumptions)
   - [Phase 1: Wire the Backbone (WB-1–WB-5)](#phase-1-wire-the-backbone)
   - [Phase 2: Signal Producers (SP-1–SP-4)](#phase-2-signal-producers)
   - [Phase 3: Prompt Integration (PI-1–PI-4)](#phase-3-prompt-integration)
   - [Phase 4: Reliability Hardening (RH-1–RH-5)](#phase-4-reliability-hardening)
   - [Phase 5: Verification & Burn-In (VB-1–VB-3)](#phase-5-verification--burn-in)
7. [Security & Privacy](#7-security--privacy)
8. [Risks & Mitigations](#8-risks--mitigations)
9. [Assumptions (Validated)](#9-assumptions-validated)
10. [Test Plan](#10-test-plan)
11. [Rollback Plan](#11-rollback-plan)
12. [Success Criteria](#12-success-criteria)

---

## 1. Executive Summary

The Action Layer was designed as Artha's write path — the ability to propose,
approve, and execute real-world actions (send email, create calendar event,
WhatsApp message) from domain intelligence.  Every component exists and passes
unit tests, but **zero actions have ever been produced in production**.

**Root causes (5 structural gaps):**
1. Step 12.5 (signal→compose→queue) exists only in `finalize.md` — not in
   `Artha.md` or `Artha.core.md` where the AI reads during catch-up
2. `pipeline.py` is read-only: never calls `email_signal_extractor` or `pattern_engine`
3. Handler import paths failed on the only health check ever run (March 19)
4. No component wires signal producers to action consumers in production
5. Prompt-driven §9 format and programmatic SQLite queue are parallel, unreconciled systems

**Evidence:** `actions.db` (platform-local, via `ActionQueue._resolve_db_path()`) has
0 rows across all 4 tables. No briefing out of 30+ ever contained an action proposal.
The audit log has 0 signal/action entries (only 8 health-check failure lines from
2026-03-19).

This spec fixes these gaps with a single orchestrator script (`scripts/action_orchestrator.py`)
and prompt-layer edits, using zero new dependencies and preserving every existing
security invariant.

---

## 2. Goal State

After this spec is fully implemented, the following must be true:

### G-1: Signals Produced Every Catch-Up
Every `brief` / catch-up session that processes at least 1 email runs the
email signal extractor and pattern engine. Signals are persisted to
`tmp/signals.jsonl` for auditability.

### G-2: Signals Converted to Proposals
Every valid signal routes through `ActionComposer.compose()` and produces an
`ActionProposal` in `actions.db` (platform-local path resolved by
`ActionQueue._resolve_db_path()`). Proposals appear in the briefing under a
`§ PENDING ACTIONS` section.

### G-3: User Can Approve/Reject Inline
The user can say "approve 1", "reject 2", "approve all low" in the same
session. Approved actions execute immediately. This replaces the never-used
§9 format with a queue-backed interaction.

### G-4: Actions Execute Reliably
Approved actions run their handler (`email_send`, `calendar_create`, etc.),
record the result in `actions.db`, and log to `state/audit.md`.
Failures are logged but never crash the session.

### G-5: Cross-Platform & Cloud-Folder Safe
The entire action layer works identically on macOS, Windows, and Linux.
`actions.db` is stored at a **platform-local** path (resolved by
`ActionQueue._resolve_db_path()`) — NOT inside the cloud-synced workspace.
Paths: `~/.artha-local/actions.db` (macOS), `%LOCALAPPDATA%\Artha\actions.db`
(Windows), `$XDG_DATA_HOME/artha/actions.db` (Linux). This avoids SQLite
corruption from cloud sync (WAL + SHM files don't survive OneDrive sync).
No platform-specific code outside existing `platform.system()` guards.

### G-6: Full Audit Trail
Every signal detection, proposal creation, approval/rejection, execution
attempt, and result is logged to both `actions.db` (structured, platform-local)
and `state/audit.md` (append-only text, cloud-synced).

### G-7: Kill Switch Instant
Setting `harness.actions.enabled: false` in `config/artha_config.yaml`
disables the entire layer cleanly. The catch-up degrades to its current
read-only intelligence behavior with zero errors.

---

## 3. Root Cause Analysis

### GAP-1: Step 12.5 Invisible to the AI Engine

**What exists:** `config/workflow/finalize.md` defines Step 12.5 with
correct Python code for signal→compose→queue.

**What's broken:** `config/Artha.md` (the AI system prompt) jumps directly
from Step 12 ("Surface active alerts") to Step 13 ("Propose write actions").
Neither `Artha.md` nor `Artha.core.md` references Step 12.5, `finalize.md`,
`ActionComposer`, or `ActionExecutor`. The AI faithfully executes Steps 1–21
as written in `Artha.md` — and actions are simply not in the script.

**Fix:** Add Step 12.5 to `Artha.md` AND `Artha.core.md`. Make it invoke
the orchestrator script (not inline Python — the AI can't reliably run
multi-module Python in a single terminal command).

### GAP-2: No Signal Extraction in the Pipeline

**What exists:** `scripts/email_signal_extractor.py` (8 signal categories,
deterministic regex), `scripts/pattern_engine.py` (YAML-driven state pattern
evaluator). Both produce `DomainSignal` objects.

**What's broken:** `pipeline.py` fetches email JSONL to stdout and applies
marketing classification. It never calls either signal producer. The JSONL
goes to stdout, is consumed by the AI for domain processing, and then
discarded — no file persists for signal extraction.

**Fix:** Create `scripts/action_orchestrator.py` that reads pipeline JSONL
from a file (the pipeline already has `--output` capability we add or we tee
stdout), runs signal extraction + pattern engine + composer + queue.
Invoked by the AI as a single CLI command in Step 12.5.

### GAP-3: Handler Import Path Failure

**What happened:** On March 19, the only health check recorded
`ACTION_HEALTH_CHECK_FAILED` for all 8 handlers with error
`No module named 'scripts.actions'`. The `_HANDLER_MAP` was using
`scripts.actions.*` instead of `actions.*`.

**Current state:** CHANGELOG says this was fixed (`_HANDLER_MAP` paths
corrected to `actions.*`). The fix is in `__init__.py` and `action_executor.py`.
**Verified:** `_FALLBACK_ACTION_MAP` in `action_executor.py` now uses
`"actions.email_send"` etc. (correct).

**Risk:** The fix was never exercised in production (no health check ran
since March 19). The orchestrator must run its own handler health check
on first invocation.

### GAP-4: No Wiring Between Signal Producers and Action Consumers

**What exists:**
- `email_signal_extractor.extract(emails)` → `list[DomainSignal]`
- `pattern_engine.evaluate()` → `list[DomainSignal]`
- `ActionComposer.compose(signal)` → `ActionProposal | None`
- `ActionExecutor.propose_direct(proposal)` → action_id

**What's broken:** No production code path calls them in sequence. The only
callers are unit tests and the CLI `__main__` blocks of individual scripts.

**Fix:** The orchestrator script is the single integration point.

### GAP-5: Two Unreconciled Action Systems

**What exists:**
1. **Prompt-driven (§9):** AI manually formats `━━ ACTION PROPOSAL ━━` text
   blocks. User says "yes". AI runs a script. Never happened in 30+ briefings.
2. **Programmatic (Step 12.5):** Python generates proposals → SQLite queue →
   Telegram inline keyboards. Never ran in production.

**Fix:** Retire the prompt-driven §9 as the primary path. The AI reads from
the SQLite queue (via orchestrator CLI output) and presents proposals in a
structured, numbered format. Approval commands route through the executor.
§9 format preserved as fallback for ad-hoc user-requested actions only.

---

## 4. Design Principles

These are Artha's core constraints — every decision in this spec respects them:

| # | Principle | How This Spec Honors It |
|---|-----------|------------------------|
| 1 | **LLM/CLI is the engine** | Orchestrator is a CLI script the AI calls; no daemon, no server |
| 2 | **Pull > Push** | The AI pulls action state on demand; no background push except existing Telegram path |
| 3 | **Cloud folder safe** | DB is platform-local (avoids sync corruption); audit.md is cloud-synced (human-readable append-only) |
| 4 | **Cross-platform** | Pure Python; no bash-only commands; `pathlib.Path` throughout |
| 5 | **No new dependencies** | Uses only stdlib + packages already in `pyproject.toml` |
| 6 | **Security-first** | PII firewall double-scan; autonomy floor; handler allowlist; AI signals default-off |
| 7 | **Fail-soft** | Every action step is non-blocking; catch-up never crashes due to action failure |
| 8 | **Human-gated writes** | No action executes without explicit approval; existing trust model preserved |
| 9 | **Observable** | Every step emits structured counters (Appendix E); `health` command reports layer status; OTLP spans deferred to V1.1 |

### 4.1 Design Decisions & Known Trade-offs

**File-drop data exchange (A-1):** The orchestrator communicates with the
pipeline and the AI via JSONL files in `tmp/` rather than pipes, sockets,
or in-process calls. This is deliberate — Artha's architecture requires
the LLM to invoke CLI scripts sequentially. File exchange provides:
(a) auditability (files persist for debugging), (b) resilience (partial
writes visible), (c) compatibility with both MCP and pipeline tiers.

**Cross-run idempotency (A-2):** If the user runs `brief` twice without
approving proposals, the second run may detect the same signals and attempt
to re-propose them. This is handled by `ActionQueue.propose()`'s status-based
dedup (see WB-3) — if a pending/deferred proposal for the same
`(action_type, source_domain)` exists, the duplicate raises `ValueError`.
The orchestrator's compose loop catches all exceptions and logs to stderr,
making the suppression externally invisible — but direct callers of
`ActionQueue.propose()` must handle `ValueError` for duplicate rejection.
Proposals that were approved/rejected/expired do NOT block re-detection.
This is acceptable: signals may recur across sessions if the underlying
condition hasn't been resolved. **Note:** the dedup key is coarse
(`action_type + source_domain`, not entity-level) — see the V1.1 `dedup_key`
enhancement in WB-3 for planned improvement.

**Platform-dependent handler availability (A-4):** Some handlers only work on
specific platforms (e.g., `apple_reminders_sync` requires macOS,
`todoist_sync` requires API token configuration). When `--approve` targets a
handler that fails due to platform unavailability, the executor records the
failure in the DB and logs to audit. The user sees:
`"✗ failed: handler unavailable on this platform"`. RH-1 handler health
check at startup marks unavailable handlers so the composer can suppress
proposals for action types that cannot execute on the current platform.

---

## 5. Architecture

### 5.1 Current State (Broken)

```
pipeline.py ─→ JSONL stdout ─→ AI reads for briefing
                                  │
                                  ╳ (no connection)
                                  │
email_signal_extractor.py ──╳── (never called)
pattern_engine.py ──────────╳── (never called)
action_composer.py ─────────╳── (never called outside tests)
action_executor.py ─────────╳── (never called outside tests)
action_queue.py (SQLite) ──╳── (0 rows, 4 empty tables)
handlers (8 modules) ──────╳── (never invoked)
```

### 5.2 Goal State

```
pipeline.py ─→ JSONL to stdout + tee to tmp/pipeline_output.jsonl
                                  │
                            AI catch-up workflow
                                  │
                         Step 12.5 (AI runs CLI)
                                  │
                                  ▼
               ┌─────────────────────────────────────┐
               │  action_orchestrator.py --run        │
               │                                      │
               │  1. Load tmp/pipeline_output.jsonl   │
               │  2. email_signal_extractor.extract() │
               │  3. pattern_engine.evaluate()        │
               │  4. Deduplicate signals              │
               │  5. ActionComposer.compose() each    │
               │  6. ActionExecutor.propose_direct()  │
               │  7. Print summary to stdout          │
               │  8. Persist signals → tmp/signals.jsonl │
               └──────────────┬──────────────────────┘
                              │
                     stdout summary
                              │
                              ▼
                    AI reads proposal summary
                    Embeds in briefing (§ PENDING ACTIONS)
                    User says "approve 1"
                              │
                              ▼
               action_orchestrator.py --approve <id>
                              │
                    ActionExecutor.approve()
                              │
                    Handler.execute()
                              │
                    Result logged to DB + audit.md
                              │
                    AI reads result, confirms to user
```

### 5.2.1 MCP Tier 1 Data Path

`Artha.md` Step 4 defines TWO data fetch tiers:
- **Tier 1 (preferred):** MCP — `artha_fetch_data()` returns records directly
  in the LLM context. No JSONL file is written; `pipeline.py` is never invoked.
- **Tier 2 (fallback):** `pipeline.py` — writes JSONL to stdout (and now to
  `tmp/pipeline_output.jsonl` via `--output`).

When Tier 1 is active, the orchestrator has no JSONL file to read. It must
still run — the pattern engine operates on state files, not email data. To
handle Tier 1, the orchestrator accepts an optional `--mcp` flag that
instructs it to skip email signal extraction and only run the pattern engine:

```
python3 scripts/action_orchestrator.py --run --mcp
```

**`--mcp` is the only supported Tier 1 path.** A previous version of this
spec offered a "fallback" where the AI would write email records to
`tmp/pipeline_output.jsonl` itself from in-context MCP data. That option
is **removed** — it is fragile (no defined JSONL schema, no validation,
prompt-injection risk from email bodies, and zero test coverage). If email
signal extraction on MCP-sourced data is needed, V1.1 will define a
stable JSONL schema with a version field and a validation step in the
orchestrator before accepting AI-written data.

This ensures the action layer works regardless of which data tier is active.

### 5.2.2 Signal Routing Table: YAML vs Hardcoded Fallback (CRITICAL)

`action_composer.py`'s `_load_signal_routing()` tries `config/signal_routing.yaml`
first. **`config/signal_routing.yaml` currently exists** with only ~9 entries
(bill_due, property_tax_due, subscription_renewal, medical_bill_high,
calendar_conflict, appointment_needed, email_needs_reply, birthday_approaching,
birthday_in_7d). When the YAML file loads successfully, it **completely replaces**
the hardcoded `_FALLBACK_SIGNAL_ROUTING` dict — no merging, no union.

This means 42+ signal types in the hardcoded fallback (appointment_confirmed,
delivery_arriving, school_action_needed, security_alert, form_deadline, ALL
pattern engine signals like goal_stale, maintenance_due, etc.) are **silently
suppressed in production**. Signals from the email extractor and pattern
engine that fire for these types will never route to proposals.

**Required fix (Phase 1, before first deployment):**

Change `_load_signal_routing()` to **merge** YAML entries over the hardcoded
fallback (YAML additions/overrides, not full replacement):

```python
def _load_signal_routing() -> dict[str, dict]:
    """Load signal routing: hardcoded base + YAML overrides (merged)."""
    base = dict(_FALLBACK_SIGNAL_ROUTING)
    try:
        from lib.config_loader import load_config
        yaml_routing = load_config("signal_routing")
        if yaml_routing:
            base.update(yaml_routing)  # YAML entries override, not replace
    except Exception:
        pass
    return base
```

**Phase 0 validation addition:** Add A-11b: verify that the merged routing
table in production includes entries for all 8 email signal types
(`event_rsvp_needed`, `appointment_confirmed`, `bill_due`, `form_deadline`,
`delivery_arriving`, `security_alert`, `subscription_renewal`,
`school_action_needed`) and at least the core pattern engine types
(`goal_stale`, `maintenance_due`).

### 5.2.3 Handler–Routing Table Alignment (CRITICAL)

**Known gap:** `action_composer.py` routes `decision_detected` signals to
`action_type: "decision_log_proposal"` and includes `"decision_log_proposal"`
in `_ALLOWED_ACTION_TYPES` (so `_validate_routing_table()` emits no warning).
However, `action_executor.py`'s `_FALLBACK_ACTION_MAP` has **no entry** for
`"decision_log_proposal"`, and no handler module `actions/decision_log_proposal.py`
exists.

**What happens at runtime:** A `decision_detected` signal → `compose()` →
`decision_log_proposal` proposal → `propose_direct()` succeeds (no handler
check at enqueue). User says "approve" → `_get_handler("decision_log_proposal")`
→ `_HANDLER_MAP.get()` returns `None` → `raise ValueError("Unknown action type")`
→ approval fails. The user sees an error after explicitly approving — bad UX.

**Required fix (Phase 1): Designated → Option (b).**
Remove `"decision_detected"` from `_FALLBACK_SIGNAL_ROUTING` and
`"decision_log_proposal"` from `_ALLOWED_ACTION_TYPES` until V1.1.
Rationale: creating a functional handler is V1.1 scope; clean removal is a
2-line change that immediately prevents the approval-→-ValueError failure,
keeps `_ALLOWED_ACTION_TYPES` and `_FALLBACK_ACTION_MAP` in sync, and makes
A-13 passable with zero new code. Option (a) (stub handler) is deferred to V1.1.

~~Option (a): Create `scripts/actions/decision_log.py` → deferred to V1.1.~~

**Invariant to enforce:** `_ALLOWED_ACTION_TYPES` (in `action_composer.py`)
and `_FALLBACK_ACTION_MAP` (in `action_executor.py`) MUST remain in sync.
Add a cross-reference comment to both and a Phase 0 validation:
```
python3 -c "from scripts.action_composer import _ALLOWED_ACTION_TYPES; \\
from scripts.action_executor import _FALLBACK_ACTION_MAP; \\
missing = _ALLOWED_ACTION_TYPES - set(_FALLBACK_ACTION_MAP); \\
assert not missing, f'Types in composer but not executor: {missing}'"
```

### 5.3 Data Flow (Single Catch-Up Session)

```
Step 4:   pipeline.py --output tmp/pipeline_output.jsonl  ← NEW flag
Step 5–12: AI processes domains, builds briefing
Step 12.5: python3 scripts/action_orchestrator.py --run
             reads: tmp/pipeline_output.jsonl
                    state/*.md (pattern engine)
             writes: actions.db (proposals, platform-local)
                     tmp/signals.jsonl (audit)
                     state/audit.md (log lines)
             stdout: proposal summary for AI consumption
Step 13:   AI presents proposals from orchestrator output
           User approves/rejects
Step 13b:  python3 scripts/action_orchestrator.py --approve <id>
             or: --reject <id> --reason "..."
             or: --defer <id> [--until "+1h"|"tomorrow"|"next-session"]
             or: --approve-all-low
Step 14:   Normal briefing email continues
Step 14.5: Telegram push of pending actions (existing, in finalize.md)
           Reads from actions.db — unapproved proposals from Step 13
           sent as inline keyboards for async mobile approval
```

**Step 14.5 compatibility note:** `config/workflow/finalize.md` defines a
Step 14.5 that pushes pending actions to Telegram as inline keyboard buttons.
This is a downstream consumer of the action queue — it reads proposals that
were NOT approved in Step 13. The orchestrator's proposals will automatically
appear in Telegram if the user skips in-session approval. No changes needed
to the Telegram path; it already reads from `actions.db` via `ActionQueue`.

### 5.4 File Inventory (New/Modified)

| File | Action | Purpose |
|------|--------|---------|
| `scripts/action_orchestrator.py` | **CREATE** | Single CLI entry point for signal→compose→queue |
| `scripts/pipeline.py` | **MODIFY** | Add `--output PATH` flag to tee JSONL |
| `config/Artha.md` | **MODIFY** | Add Step 12.5 and Step 13 rewrite |
| `config/Artha.core.md` | **MODIFY** | Mirror Step 12.5 addition |
| `config/workflow/finalize.md` | **MODIFY** | Update Step 12.5 to use orchestrator CLI |
| `tests/integration/test_action_orchestrator.py` | **CREATE** | Integration tests |
| `tests/unit/test_action_orchestrator.py` | **CREATE** | Unit tests |

---

## 6. Implementation Plan

### Phase 0: Validate Assumptions

Before writing any code, verify these assumptions are still true. Mark each
PASS/FAIL before proceeding.

| ID | Assumption | Validation Command | Expected |
|----|------------|--------------------|----------|
| A-1 | `ActionComposer.compose()` works | `python3 -c "...compose(bill_due signal)..."` | Returns `ActionProposal` |
| A-2 | `ActionQueue.propose()` works | `python3 -c "...propose to temp DB..."` | Row inserted |
| A-3 | `EmailSignalExtractor.extract()` works | `python3 -c "...extract([rsvp email])..."` | Returns ≥1 `DomainSignal` |
| A-4 | Handler import paths correct | `python3 -c "import actions.email_send"` | No ImportError |
| A-5 | `PatternEngine` evaluates without crash | `python3 -c "PatternEngine(root_dir=Path('.')).evaluate()"` | Returns list (may be empty) |
| A-6 | `pii_guard.scan()` importable | `python3 -c "from pii_guard import scan"` | No ImportError |
| A-7 | `actions.db` writable (platform-local) | `python3 -c "from scripts.action_queue import ActionQueue; q = ActionQueue(); print(q._db_path)"` | Prints resolved path; no error |
| A-8 | No new dependencies needed | `pip list \| grep -E "yaml\|sqlite"` | Already installed |

**Validation results (2026-03-31):**
- A-1: **PASS** — `compose()` returns `ActionProposal(action_type='instruction_sheet', friction='high')`
- A-2: **PASS** — verified via temp DB test
- A-3: **PASS** — `extract()` returns `DomainSignal(signal_type='event_rsvp_needed')`
- A-4: **PASS** — `_FALLBACK_ACTION_MAP` uses `actions.email_send` (corrected per CHANGELOG)
- A-5: **PASS** — `PatternEngine(root_dir=Path('.')).evaluate()` returns `[]` (no patterns fired)
- A-6: **DEFERRED** — requires venv activation; works when `scripts/` is on sys.path.
  **Note:** The orchestrator must run inside the same venv as the pipeline —
  `python3` resolves to the venv's interpreter when Artha.md Step 1 activates
  the venv. Without the venv, `pii_guard` falls through to subprocess mode
  (~100ms slower per proposal) and may fail on Windows without the venv path.
- A-7: **PASS** — DB exists at platform-local path, tables present, 0 rows
- A-8: **PASS** — pyyaml, sqlite3 (builtin) already available

**Additional Phase 0 validations (v1.3.0):**

| ID | Assumption | Validation Command | Expected |
|----|------------|--------------------|----------|
| A-11b | Signal routing covers email + pattern types (merged) | See §5.2.2 — verify merged routing includes all 8 email signal types + core pattern types | All 10+ types present |
| A-13 | `_ALLOWED_ACTION_TYPES` and `_FALLBACK_ACTION_MAP` in sync | See §5.2.3 validation command | No missing types (empty set) |
| A-14 | `queue.db_path` has deprecation comment | `grep -n 'db_path.*DEPRECATED\\|db_path.*deprecated' config/artha_config.yaml` | At least 1 match |

---

### Phase 1: Wire the Backbone

#### WB-1: Create `scripts/action_orchestrator.py`

The single integration point. CLI script — no class, no daemon. Follows the
same pattern as `pipeline.py` and `skill_runner.py`: argparse + main().

```
python3 scripts/action_orchestrator.py --run
python3 scripts/action_orchestrator.py --run --mcp
python3 scripts/action_orchestrator.py --approve <action_id>
python3 scripts/action_orchestrator.py --reject <action_id> [--reason "..."]
python3 scripts/action_orchestrator.py --defer <action_id> [--until "+1h"|"tomorrow"|"next-session"]
python3 scripts/action_orchestrator.py --approve-all-low
python3 scripts/action_orchestrator.py --show <action_id>
python3 scripts/action_orchestrator.py --list
python3 scripts/action_orchestrator.py --health
python3 scripts/action_orchestrator.py --expire
```

**`--run` behavior (the core loop):**

```python
def run(artha_dir: Path, emails_path: Path | None, verbose: bool) -> int:
    """Signal extraction → composition → queue. Returns proposal count."""

    # 0. Guard: actions enabled?
    if not _actions_enabled(artha_dir):
        print("[action_orchestrator] actions disabled — skipping", file=sys.stderr)
        return 0

    # 0b. Guard: read-only mode? (uses detect_environment.py)
    if _is_read_only(artha_dir):
        print("[action_orchestrator] read-only mode — skipping", file=sys.stderr)
        return 0

    # 1. Collect signals from email extractor
    email_signals: list[DomainSignal] = []
    if emails_path and emails_path.exists():
        emails = _load_jsonl(emails_path)
        extractor = EmailSignalExtractor()
        email_signals = extractor.extract(emails)
    else:
        if verbose:
            print("[action_orchestrator] no email JSONL found — skipping email signals", file=sys.stderr)

    # 2. Collect signals from pattern engine
    pattern_signals: list[DomainSignal] = []
    try:
        engine = PatternEngine(root_dir=artha_dir)
        pattern_signals = engine.evaluate()
    except Exception as exc:
        print(f"[action_orchestrator] pattern engine error: {exc}", file=sys.stderr)

    # 3. Merge + deduplicate
    all_signals = _deduplicate(email_signals + pattern_signals)

    # 4. Persist signals for audit
    _persist_signals(artha_dir / "tmp" / "signals.jsonl", all_signals)

    # 5. Compose → propose
    composer = ActionComposer(artha_dir=artha_dir)
    executor = ActionExecutor(artha_dir)
    proposed = 0
    for signal in all_signals:
        try:
            proposal = composer.compose(signal)
            if proposal is not None:
                # Validate via handler before enqueue (propose_direct skips this)
                _validate_proposal_handler(executor, proposal)
                executor.propose_direct(proposal)
                proposed += 1
        except Exception as exc:
            print(f"[action_orchestrator] compose/propose failed: {exc}", file=sys.stderr)

    # 6. Expire stale proposals
    expired = executor.expire_stale()

    # 7. Print summary for AI consumption
    pending = executor.list_pending()
    _print_summary(all_signals, proposed, expired, pending)

    executor.close()
    return proposed
```

**`_validate_proposal_handler()` — pre-enqueue handler validation:**

`ActionExecutor.propose_direct()` intentionally skips handler validation
(it enqueues pre-built proposals from the composer without re-validation).
This means malformed proposals can be enqueued and only fail at approval
time — after the user has already approved. To prevent this, the
orchestrator validates each proposal through its handler before enqueue:

```python
def _validate_proposal_handler(executor: ActionExecutor, proposal: ActionProposal) -> None:
    """Run handler.validate() before enqueue. Raises ValueError on failure."""
    handler = executor._get_handler(proposal.action_type)
    ok, reason = handler.validate(proposal)
    if not ok:
        raise ValueError(f"Handler validation: {reason}")
```

This catches structural issues (e.g., `email_send` with no `to` field)
before the proposal enters the queue, ensuring the user never approves
a proposal that immediately fails.

**`--show <id>` behavior:**

Retrieves a single proposal from the queue and prints an expanded human-
readable preview. Used by the AI in Step 13 before approving content-
bearing actions (email_send, email_reply, whatsapp_send).

```python
def show(artha_dir: Path, action_id: str) -> int:
    executor = ActionExecutor(artha_dir)
    try:
        proposal = executor.get_action(action_id)
        if not proposal:
            print(f"[action] Action {action_id} not found.", file=sys.stderr)
            return 1
        _print_expanded_preview(proposal)
        return 0
    finally:
        executor.close()
```

**`--show` output format:**

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

For non-content actions (`calendar_create`, `reminder_create`, etc.) the
output shows the title, domain, friction, and key parameters (event date,
reminder text) but no "CONTENT PREVIEW" section.

Encrypted parameters are decrypted via `executor.get_action()` (which uses
the age private key). If decryption fails (key unavailable), print
`"🔒 Parameters encrypted — decrypt from Mac terminal to preview."`

**`--defer <id> [--until HORIZON]` behavior:**

Defers a proposal so it re-surfaces later. The live `ActionExecutor.defer()`
requires an explicit `until` argument — the orchestrator resolves preset
horizons before calling it:

| User says | `--until` value | Resolved to |
|-----------|----------------|-------------|
| `defer 3` (bare) | `next-session` (default) | `+24h` |
| `defer 3 until +1h` | `+1h` | 1 hour from now |
| `defer 3 until +4h` | `+4h` | 4 hours from now |
| `defer 3 until tomorrow` | `tomorrow` | next day 09:00 local |
| `defer 3 until next-session` | `next-session` | `+24h` |

The orchestrator's `_resolve_defer_preset()` translates these to ISO
timestamps before calling `executor.defer(action_id, until=resolved_ts)`.
The `defer_until` timestamp is stored separately from the proposal's
`expires_at` — a deferred proposal is hidden from `list_pending()` until
its `defer_until` time passes, but still expires at its original expiry.

> **Implementation note:** `_resolve_defer_preset()` in the orchestrator
> MUST fully resolve every named preset to an ISO-8601 string before
> calling `executor.defer()`. The executor's existing `_resolve_defer_time()`
> handles only `+Nh`/`+Nm` offsets and ISO strings — it does NOT understand
> named presets like `"tomorrow"` or `"next-session"`. Passing named
> presets directly to the executor triggers its 24h fallback silently
> (no error, wrong behavior). The orchestrator is the translation layer
> between human-friendly presets and the executor's offset/ISO interface.

**`--list` behavior:** Query `actions.db` and print pending proposals in
a numbered format the AI can embed directly in the briefing.

**Output format (stdout, consumed by AI):**

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

**`--approve <id>` behavior:**

```python
def approve(artha_dir: Path, action_id: str) -> int:
    executor = ActionExecutor(artha_dir)
    try:
        result = executor.approve(action_id, approved_by="user:terminal")
        print(f"[action] ✓ {result.status}: {result.message}")
        return 0 if result.status == "success" else 1
    except Exception as exc:
        print(f"[action] ✗ approve failed: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()
```

**Design constraints:**
- Pure Python, no new dependencies
- All output to stdout (for AI consumption) or stderr (for logging)
- Non-blocking: never raises/crashes — returns exit code 0 on success, 1 on partial, 3 on full failure
- Respects `harness.actions.enabled` kill switch
- Respects read-only mode (`detect_environment.py` — must be importable;
  add `scripts/` to `sys.path` if needed, following the sibling pattern)
- Uses existing `sys.path` manipulation pattern from `scripts/` siblings

#### WB-2: Add `--output` Flag to `pipeline.py`

Modify `run_pipeline()` to write JSONL output as a **fresh snapshot** per run
(not append) in addition to stdout. This is how the orchestrator gets email data
without re-fetching. The file is disposable session input — stale records from
prior runs must never accumulate or be reprocessed.

```python
# In _parse_args():
parser.add_argument(
    "--output", "-o",
    help="Write JSONL output to this file path (fresh snapshot, in addition to stdout)",
    default=None,
)

# In run_pipeline(), after classified_lines are built:
if output_path:
    tmp_path = output_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for line in classified_lines:
            f.write(line + "\n")
    tmp_path.replace(output_path)  # atomic rename — no partial reads
```

**Fresh snapshot semantics:** Each `--output` invocation writes a temp file
then does an atomic rename (`Path.replace()`). This guarantees:
1. No stale records from prior runs accumulate in the file
2. The orchestrator never reads a partially-written file (atomic rename)
3. The file is treated as disposable — deleted in Step 18 (`tmp/` cleanup)

**Safety:** The `--output` path is relative to `artha_dir`. Validated
to be inside `tmp/` (no arbitrary path writes).

**Cross-platform:** Uses `pathlib.Path` — works on Windows and macOS.
`Path.replace()` is atomic on POSIX; on Windows it overwrites the target
(acceptable — single-writer discipline).

#### WB-3: Signal Deduplication Logic

Signals can overlap (email extractor + pattern engine may flag the same issue).
Deduplicate by `(signal_type, domain, entity)` within the same orchestrator run.

Additionally, `ActionQueue.propose()` has a **status-based** dedup guard:
it checks `WHERE status IN ('pending', 'deferred')` for the same
`(action_type, source_domain)` pair. If a matching active proposal already
exists, `propose()` raises `ValueError` (caught by the orchestrator's
try/except — see §4.1). This is NOT time-windowed — it prevents duplicates
as long as the prior proposal is still pending or deferred.

> **Known limitation (V1.0):** The queue-level dedup key is
> `(action_type, source_domain)` only — it has no entity-level granularity.
> This means two legitimate actions of the same type in the same domain
> (e.g., two different `bill_due` signals from `finance`) will collide:
> the second is silently suppressed while the first is still pending.
>
> **V1.1 enhancement:** Introduce a stable `dedup_key` column to the queue
> schema, derived from entity-level identity: `thread_id` for email actions,
> `event_id` for calendar, `recipient` for messaging, `biller` for bills,
> or an explicit signal hash. The orchestrator would compute this key at
> propose time and pass it to `ActionQueue.propose()`:
> ```sql
> WHERE action_type = ? AND source_domain = ? AND dedup_key = ?
>       AND status IN ('pending', 'deferred')
> ```
>
> **V1.0 workaround:** The within-run `_deduplicate()` function below uses
> the finer-grained `(signal_type, domain, entity)` key, which catches
> same-entity duplicates within a single run. Cross-run collisions at the
> queue level are an accepted trade-off — in practice, most domains produce
> at most one actionable signal per session.

```python
def _deduplicate(signals: list[DomainSignal]) -> list[DomainSignal]:
    """Remove duplicate signals by (signal_type, domain, entity) key.

    Within the same orchestrator run, only the first signal for each key
    is kept. Cross-session dedup is handled by ActionQueue.propose().
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[DomainSignal] = []
    for s in signals:
        key = (s.signal_type, s.domain, s.entity)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique
```

#### WB-4: Signal Persistence

Write all signals to `tmp/signals.jsonl` (append mode per session,
overwritten at start of each run). This is the audit trail for
"what did Artha detect?"

```python
def _persist_signals(path: Path, signals: list[DomainSignal]) -> None:
    """Write signals to JSONL for auditability."""
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for s in signals:
            record = {
                "signal_type": s.signal_type,
                "domain": s.domain,
                "entity": s.entity,
                "urgency": s.urgency,
                "impact": s.impact,
                "source": s.source,
                "detected_at": s.detected_at,
                # metadata intentionally excluded — may contain PII
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

**Privacy note:** Signal metadata (which may contain email addresses,
phone numbers, amounts) is NOT written to the signals file. Only the
signal envelope (type, domain, entity, urgency, scores). The
full proposal with metadata lives only in the encrypted `actions.db`.

#### WB-5: Audit Integration

Every orchestrator run appends to `state/audit.md`:

```
[2026-03-31T17:00:00Z] ACTION_ORCHESTRATOR | signals: 4 | proposals: 3 | expired: 0 | errors: 0
```

Every proposal enqueue appends:

```
[2026-03-31T17:00:01Z] ACTION_PROPOSED | id:abc12345 | type:email_reply | domain:finance | friction:high
```

Every approval/rejection appends:

```
[2026-03-31T17:05:00Z] ACTION_APPROVED | id:abc12345 | by:user:terminal
[2026-03-31T17:05:01Z] ACTION_EXECUTED | id:abc12345 | status:success | handler:email_reply
```

This uses the existing `_audit_log()` function in `action_executor.py`.

---

### Phase 2: Signal Producers

#### SP-1: Wire Email Signal Extractor Into Orchestrator

The `EmailSignalExtractor` expects a `list[dict]` of email records (parsed
JSONL). The orchestrator loads `tmp/pipeline_output.jsonl` and hands it over.

**Filtering:** Only non-marketing emails are processed. The pipeline's
`email_classifier` already tags `"marketing": true` — the orchestrator skips
those.

```python
def _load_emails(path: Path) -> list[dict]:
    """Load email records from pipeline JSONL, excluding marketing."""
    emails = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                if record.get("marketing"):
                    continue
                emails.append(record)
            except json.JSONDecodeError:
                continue
    return emails
```

**Edge case:** The pipeline may output non-email records (calendar events,
OneNote, RSS). The email signal extractor should skip records without
`subject` and `body` fields — it already does this internally.

#### SP-2: Wire Pattern Engine Into Orchestrator

The `PatternEngine` evaluates YAML patterns against state files. It produces
`DomainSignal` objects for conditions like "goal stale for 14+ days" or
"maintenance due".

```python
engine = PatternEngine(root_dir=artha_dir)
pattern_signals = engine.evaluate()
```

**Constructor note (verified):** `PatternEngine.__init__` takes
`patterns_file: Path | None = None, root_dir: Path | None = None`.
Pass `root_dir=artha_dir` for production. The patterns file defaults to
`config/patterns.yaml`.

**Cooldown:** `PatternEngine` already tracks last-fired timestamps in
`state/pattern_engine_state.yaml` per pattern ID. Patterns have built-in
cooldowns to prevent re-firing every session. No additional dedup needed.

#### SP-2.1: Signal Producer Coverage Audit

> **Known gap:** `action_composer.py` defines 50+ signal types in
> `_FALLBACK_SIGNAL_ROUTING`, but only ~13 have active producers:
> - **Email extractor** (8): `bill_due`, `event_rsvp_needed`,
>   `renewal_notice`, `appointment_confirmation`, `deadline_approaching`,
>   `shipping_notification`, `payment_request`, `subscription_renewal`
> - **Pattern engine** (~5): `goal_stale`, `maintenance_due`,
>   `document_expiring`, `review_pending`, `health_check_overdue`
>
> The remaining ~37 signal types (e.g., `visa_status_change`,
> `insurance_renewal`, `tax_deadline`) have routing rules but no producer
> code. This is acceptable for V1.0 — signals fire only when a producer
> exists. The coverage gap is documented here to track V1.1 expansion
> priorities. Pattern definitions in `config/patterns.yaml` can be
> extended to produce additional signal types without code changes.
>
> **`decision_log_proposal` gap:** The `decision_detected` signal routes to
> `action_type: "decision_log_proposal"` which has no handler — see §5.2.3.

#### SP-3: Calendar Signal Generation *(V1.1 — Deferred)*

> **Deferred to V1.1.** AI-emitted signals introduce a prompt injection
> surface that violates the core security invariant: "Signals from
> deterministic code only, never LLM on raw email" (§7.1). The V1.0
> orchestrator uses only deterministic signal producers (email regex +
> pattern engine). AI signal support requires the hardening in SP-4 below.

Calendar conflicts and upcoming events are detected during Step 8 by the AI.
When the AI identifies a conflict, it should emit a signal as structured
data rather than prose. This is not automatable via the orchestrator (the AI
does the reasoning), but the **Step 12.5 prompt** should instruct the AI:

> "For each calendar conflict or scheduling need identified in Step 8, write
> a signal line to tmp/ai_signals.jsonl in the format:
> `{"signal_type": "calendar_conflict", "domain": "calendar", "entity": "...", "urgency": N, "impact": N, "source": "ai"}`
> The orchestrator will pick these up automatically."

The orchestrator reads `tmp/ai_signals.jsonl` as a third signal source.

#### SP-4: AI-Emitted Signal File *(V1.1 — Deferred)*

> **Deferred to V1.1.** Requires the security hardening below before
> enabling in production. V1.0 ships with `harness.actions.ai_signals: false`
> in `config/artha_config.yaml` as the default.

Create a lightweight protocol for the AI to emit signals during Steps 7–8
that the orchestrator picks up:

```python
# In orchestrator --run:
if not _ai_signals_enabled(artha_dir):
    if verbose:
        print("[action_orchestrator] AI signals disabled — skipping", file=sys.stderr)
else:
    ai_signal_path = artha_dir / "tmp" / "ai_signals.jsonl"
    if ai_signal_path.exists():
        ai_signals = _load_ai_signals(ai_signal_path)
        all_signals.extend(ai_signals)
```

The AI writes JSONL lines during domain processing. The orchestrator
validates each line against the `DomainSignal` schema (required fields:
`signal_type`, `domain`, `entity`, `urgency`, `impact`, `source`).

**V1.1 Security Hardening (required before enabling):**

1. **Mandatory `source` field:** AI-emitted signals MUST include
   `"source": "ai"`. The orchestrator tags them distinctly from
   deterministic signals (`"source": "email_extractor"` or
   `"source": "pattern_engine"`).

2. **Friction escalation:** All proposals originating from AI signals
   are escalated to `friction: high` regardless of the routing table's
   default. This ensures human review even for normally low-friction
   action types.

3. **Default-off config flag:** `config/artha_config.yaml` must include:
   ```yaml
   harness:
     actions:
       ai_signals: false  # V1.1: Set to true after burn-in validates safety
   ```
   The orchestrator checks `harness.actions.ai_signals` before loading
   `tmp/ai_signals.jsonl`.

4. **Audit differentiation:** AI-originated proposals are logged with
   `[AI-SIGNAL]` prefix in `state/audit.md` for easy filtering during
   burn-in review.

**Security:** The AI-emitted signals go through the same PII firewall and
composer pipeline as deterministic signals. No special trust elevation.

---

### Phase 3: Prompt Integration

#### PI-1: Add Step 12.5 to `config/Artha.md`

Insert between the existing Step 12 and Step 13:

```markdown
### Step 12.5 — Generate action proposals *(Action Layer)*

**SKIP if** `harness.actions.enabled: false` in `config/artha_config.yaml`
→ log `⏭️ Step 12.5 skipped — actions disabled`
**SKIP in read-only mode** → log `⏭️ Step 12.5 skipped — read-only mode`

Run the action orchestrator to convert domain signals into actionable proposals:

\```bash
python3 scripts/action_orchestrator.py --run
\```

**If you used MCP (`artha_fetch_data`) in Step 4 instead of `pipeline.py`,**
run with `--mcp` to skip email signal extraction (pattern engine still runs):
\```bash
python3 scripts/action_orchestrator.py --run --mcp
\```

The orchestrator:
1. Reads `tmp/pipeline_output.jsonl` (written by Step 4)
2. Reads `tmp/ai_signals.jsonl` (written by you during Steps 7–8, if any)
3. Runs deterministic signal extraction (email patterns + state patterns)
4. Composes ActionProposals and queues them in `actions.db` (platform-local)
5. Prints a numbered summary of pending actions to stdout

Embed the orchestrator output in the briefing under `§ PENDING ACTIONS`.

**Context window guard:** If the orchestrator summary exceeds 40 lines (e.g.,
20+ pending proposals), truncate to the 10 highest-friction proposals (then
oldest first within the same friction tier) and
append: `"... and N more. Run 'items' or 'python3 scripts/action_orchestrator.py --list' to see all."` This prevents the action section from consuming
excessive context window budget in the briefing. The sort order matches
`ActionQueue.list_pending()` which orders by friction DESC then created_at ASC
— there is no separate urgency field on proposals.

If the orchestrator reports 0 pending actions, omit the section entirely.
If the orchestrator fails or is unavailable, log the error and continue
— action proposals are never blocking.

**Burn-in mode:** If `harness.actions.burn_in: true` in `config/artha_config.yaml`,
embed the orchestrator output under `[DEBUG] Proposed Actions` at the end
of the briefing (not in the main `§ PENDING ACTIONS` section). This allows
validating signal quality for 5 sessions before full integration.
```

#### PI-2: Rewrite Step 13 in `config/Artha.md`

Replace the current Step 13 (which tells the AI to format §9 text blocks)
with a queue-integrated version. The current Step 13 begins with:

> `### Step 13 — Propose write actions`
> `If any domain processing in Steps 7–12 identified...`

Replace the entire step with:

```markdown
### Step 13 — Present and approve actions

If Step 12.5 produced pending actions, present them using the orchestrator
output format (numbered list with IDs, friction indicators, and domains).

**Content-bearing action preview (human-gate contract):** For action types
that send content to external recipients — `email_send`, `email_reply`,
`whatsapp_send`, and any future messaging handler — the compact numbered
list is **not sufficient** for informed approval. The UX spec (§9) requires
full content preview and editability for write actions.

Before the user approves a content-bearing action, the AI MUST:
1. Retrieve the full proposal parameters from the queue
   (`python3 scripts/action_orchestrator.py --show <id>`)
2. Present an **expanded preview** showing: recipient, subject, full body
   text (or message content), and any attachments
3. Offer the user the ability to edit before approving:
   `"approve 1"` (as-is), `"approve 1 with edits"` (AI applies edits
   then re-queues), or `"reject 1"`

Non-content actions (`calendar_create`, `reminder_create`, `todoist_sync`,
etc.) can be approved from the compact summary — the title/domain/friction
tuple provides sufficient review context.

Wait for user input:
- "approve 1" or "approve abc12345" → run:
  `python3 scripts/action_orchestrator.py --approve abc12345`
- "reject 2" or "reject def67890" → run:
  `python3 scripts/action_orchestrator.py --reject def67890`
- "approve all low" → run:
  `python3 scripts/action_orchestrator.py --approve-all-low`
- "skip" or "next" → continue to Step 14 without acting on proposals
- "defer 3" or "defer 3 until tomorrow" → run:
  `python3 scripts/action_orchestrator.py --defer ghi11223 --until "tomorrow"`
  Preset horizons: `+1h`, `+4h`, `tomorrow`, `next-session` (default).
  Maps to `ActionExecutor.defer(action_id, until=...)` which requires an
  explicit `until` value. `next-session` resolves to `+24h` as a sensible
  default when the user says bare "defer" without a horizon.

After each approval, show the execution result.
If the user does not respond to proposals, continue after presenting them
— proposals remain pending in the queue for the next session or Telegram
approval.

**Ad-hoc actions:** If the user explicitly requests an action not from the
queue (e.g., "send email to X about Y"), present it using the §9 Action
Proposal Format and execute via the appropriate handler script directly.
The §9 format is for human-initiated ad-hoc requests only.
```

#### PI-3: Add Step 12.5 to `config/Artha.core.md`

Mirror the PI-1 edit with identical content. `Artha.core.md` is the base
template from which `Artha.md` is generated.

#### PI-4: Update `config/workflow/finalize.md`

Replace the existing Step 12.5 inline Python code with the CLI invocation:

```markdown
### Step 12.5 — Compose action proposals from domain signals *(Action Layer)*

**SKIP if** `actions.enabled: false` → log `⏭️ Step 12.5 skipped`
**SKIP in read-only mode** → log `⏭️ Step 12.5 skipped — read-only mode`

\```bash
python3 scripts/action_orchestrator.py --run
\```

The orchestrator reads:
- `tmp/pipeline_output.jsonl` (from Step 4)
- `tmp/ai_signals.jsonl` (from Steps 7–8, if AI wrote any)
- `state/*.md` (pattern engine evaluates state files)

Output: numbered pending action list on stdout for the AI to embed in the
briefing. Errors to stderr. Exit code: 0=ok, 1=partial, 3=failure.
```

#### PI-5: Add `--output` to Step 4 Pipeline Command in `config/Artha.md`

Modify the Step 4 pipeline invocation to include the `--output` flag:

```markdown
### Step 4 — Fetch external data

\```bash
python3 scripts/pipeline.py --since 7d --verbose --output tmp/pipeline_output.jsonl
\```
```

This is a one-line change to the existing Step 4 command.

---

### Phase 4: Reliability Hardening

#### RH-1: Startup Handler Health Check

On `--run`, the orchestrator performs a quick **import-level** handler check
before processing signals. This catches the March 19 failure proactively.

> **Why not use `ActionExecutor.run_health_checks()`?** The executor's
> `run_health_checks()` calls `handler.health_check()` on each handler,
> which may perform full functional checks including API connectivity
> (slow, network-dependent). The orchestrator's startup must be fast.
> Import-level verification (~10ms) is sufficient to catch broken module
> paths without the latency of full health probes. The `--health` command
> delegates to `executor.run_health_checks()` for full functional status
> (see Appendix E.2).

```python
def _handler_health_check() -> list[str]:
    """Verify all handler modules import cleanly. Returns list of failures."""
    from actions import _HANDLER_MAP
    failures = []
    for action_type, module_path in _HANDLER_MAP.items():
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            failures.append(f"{action_type}: {exc}")
    return failures
```

If any handlers fail to import, the orchestrator:
1. Logs the failures to stderr and `state/audit.md`
2. Continues with the remaining healthy handlers
3. Marks the failed action types as unavailable in the composer
   (proposals for those types are suppressed this session)

#### RH-2: Graceful Degradation on Missing Pipeline File

If `tmp/pipeline_output.jsonl` doesn't exist (e.g., pipeline was skipped,
MCP Tier 1 was used without `--mcp` flag, or user ran catch-up without
`--output`), the orchestrator:
1. Logs a warning to stderr
2. Still runs the pattern engine (which doesn't need email data)
3. Still processes `tmp/ai_signals.jsonl` if present
4. Reports "email signals: 0 (no pipeline data)" in summary

This ensures catch-up never fails due to a missing file.

#### RH-3: SQLite Safety (Platform-Local DB)

`actions.db` is stored outside the cloud-synced workspace at a platform-local
path resolved by `ActionQueue._resolve_db_path()` (e.g.,
`~/.artha-local/actions.db` on macOS). This **deliberately avoids** SQLite
corruption from cloud sync — WAL and SHM files do not survive OneDrive/iCloud
file-level sync.

SQLite WAL mode is still enabled (already configured in `action_queue.py`)
for its performance benefits:
1. Only one writer process at a time (enforced by single-session discipline)
2. WAL + SHM files are alongside the DB at the platform-local path
3. `PRAGMA busy_timeout=5000` for graceful retry

**Additional guard:** The orchestrator wraps its entire propose loop in a
single connection context. If the session is interrupted, SQLite's journal
ensures atomicity — partial proposals never persist.

**Cross-machine consistency (DUAL v1.3 bridge):** Since the DB is platform-local,
each machine maintains its own SQLite queue. However, proposals are NOT
independent — the existing DUAL v1.3 bridge (`ActionQueue.ingest_remote()`,
`ActionQueue.apply_remote_result()`) provides **eventual consistency** across
machines. The Mac (proposer) creates proposals locally and the bridge
propagates them to the Windows queue (executor) via an encrypted OneDrive
exchange folder. Results flow back the same way.

The consistency model is:
- **Strong consistency** within the local SQLite queue (WAL, single writer)
- **Eventual consistency** across the multi-machine bridge (encrypted file
  exchange, configurable sync interval in `artha_config.yaml` under
  `multi_machine.bridge`)

The audit trail (`state/audit.md`) is cloud-synced and provides immediate
cross-machine visibility for human review. Proposals that expire before
bridge propagation completes are harmless — the receiving queue's
`ingest_remote()` silently drops expired records.

#### RH-4: Rate Limiting Enforcement

The existing `ActionRateLimiter` enforces per-action-type rate limits from
`config/actions.yaml` (e.g., `email_send: max 20/hour, 100/day`).

The orchestrator respects rate limits during the propose phase. If a rate
limit would be exceeded, the signal is logged but no proposal is created:

```
[action_orchestrator] rate limit reached for email_reply (20/hour) — signal suppressed
```

#### RH-5: Timeout Protection

The orchestrator sets a 60-second wall-clock timeout for the entire `--run`
operation. If signal extraction + composition takes longer (shouldn't — it's
all deterministic regex + YAML), the orchestrator exits with partial results.

Individual handler execution during `--approve` has a per-handler timeout
from `config/actions.yaml` (`timeout_sec`, default 30s). This is already
enforced by `ActionExecutor.approve()` via `_execute_with_timeout()`.

---

### Phase 5: Verification & Burn-In

#### VB-1: Smoke Test (Post-Implementation)

Run a complete catch-up with the new pipeline and verify:

```bash
# 1. Pipeline with output
python3 scripts/pipeline.py --since 7d --output tmp/pipeline_output.jsonl

# 2. Orchestrator
python3 scripts/action_orchestrator.py --run

# 3. Verify proposals were created (use Python API — DB is platform-local)
python3 -c "from scripts.action_queue import ActionQueue; q = ActionQueue(); print('rows:', len(q.list_pending()))"  # should be > 0

# 4. Verify signals persisted
wc -l tmp/signals.jsonl  # should be > 0

# 5. Verify audit logged
grep "ACTION_ORCHESTRATOR\|ACTION_PROPOSED" state/audit.md | tail -5

# 6. List pending
python3 scripts/action_orchestrator.py --list

# 7. Approve one (dry run first)
python3 scripts/action_orchestrator.py --approve <id>
```

#### VB-2: Kill Switch Verification

```bash
# Disable actions
# Set harness.actions.enabled: false in config/artha_config.yaml

# Run orchestrator
python3 scripts/action_orchestrator.py --run
# Expected: "[action_orchestrator] actions disabled — skipping" and exit 0

# Re-enable
# Set harness.actions.enabled: true in config/artha_config.yaml
```

#### VB-3: Burn-In Period (5 Catch-Ups)

For the first 5 catch-up sessions after deployment:
1. Run the orchestrator but do NOT auto-embed proposals in the briefing
2. Instead, append them as a `[DEBUG] Action Proposals` section at the end
3. Manually validate: Are the proposals reasonable? Signal-to-noise ratio?
4. After 5 sessions with <20% false positive rate, enable full integration

**Burn-in flag:** `harness.actions.burn_in: true` in `config/artha_config.yaml`.
When true, the orchestrator adds `[BURN-IN]` prefix to all output and the AI
embeds proposals in a debug section rather than the main briefing body.

---

## 7. Security & Privacy

### 7.1 Preserved Security Invariants

All existing security contracts are maintained:

| Invariant | Mechanism | Verified By |
|-----------|-----------|-------------|
| Handler allowlist | `_HANDLER_MAP` in `actions/__init__.py` | `test_handler_allowlist_security` |
| PII double-scan | `_pii_scan_params()` at enqueue AND execute | `test_pii_guard_blocks_at_enqueue` |
| Autonomy floor | `TrustEnforcer.check()` — hardcoded, not bypassable | `test_autonomy_floor_not_bypassable` |
| Human gate | All handlers require `approve()` before `execute()` | `test_no_auto_execute` |
| Audit trail | Every state transition logged to DB + audit.md | `test_audit_completeness` |
| Signal isolation | V1.0: deterministic code only. V1.1: AI signals gated by `ai_signals: false` default + friction escalation | Design invariant + config flag |
| Encrypted params at rest | `actions.db` encrypts sensitive params with age | `test_encryption_at_rest` |

### 7.2 New Security Considerations

#### 7.2.1 AI-Emitted Signals (`tmp/ai_signals.jsonl`) — V1.1

> **Disabled by default in V1.0.** `harness.actions.ai_signals: false` in
> `config/artha_config.yaml`. Enabled only after burn-in validates safety (SP-4).

**Risk:** The AI writes signals during Steps 7–8. If the AI is influenced by
prompt injection in an email, it could emit a malicious signal (e.g.,
`signal_type: "email_send"` with a phishing body).

**Mitigation (defense in depth):**
1. **Default-off:** AI signals are not processed in V1.0. Must be explicitly
   enabled via config flag after security burn-in validates safety.
2. **Source tagging:** All AI signals carry `"source": "ai"` and are logged
   with `[AI-SIGNAL]` prefix for audit differentiation.
3. **Friction escalation:** All proposals from AI signals are forced to
   `friction: high` — no batch-approve via `--approve-all-low`.
4. AI signals go through the SAME `ActionComposer.compose()` → PII firewall
   → human approval gate as deterministic signals. No shortcut.
5. The composer maps `signal_type` through the routing table — unknown signal
   types are silently dropped. An attacker cannot invent new action types.
6. All proposals require human approval (`autonomy_floor: true` for email/messaging).
7. The AI writes signal JSONL but does NOT write proposals directly. The
   orchestrator is the only writer to `actions.db` (platform-local).
8. AI signals are validated against a strict JSON schema before processing.

#### 7.2.2 Signal JSONL Files Are Ephemeral

`tmp/pipeline_output.jsonl`, `tmp/signals.jsonl`, and `tmp/ai_signals.jsonl`
are in the `tmp/` directory which is:
- Excluded from encryption (tmp/ is not in the backup manifest)
- Cleaned up in Step 18 (vault re-encrypt + tmp cleanup)
- Never committed to git (`.gitignore` covers `tmp/`)

**Privacy:** `pipeline_output.jsonl` contains email/calendar/connector content. It is deleted
in Step 18 (`tmp/` cleanup) and never persists across sessions. The signals
file intentionally excludes metadata (see WB-4).

#### 7.2.3 `--output` Path Validation

The `--output` flag in `pipeline.py` is validated to:
1. Be a relative path under `tmp/` (no `../../etc/passwd` attacks)
2. Use `pathlib.Path.resolve()` to prevent symlink traversal
3. Create parent directories only under the Artha workspace root

```python
def _validate_output_path(raw: str, artha_dir: Path) -> Path:
    """Validate --output path is safe (inside tmp/)."""
    target = (artha_dir / raw).resolve()
    allowed = (artha_dir / "tmp").resolve()
    if not str(target).startswith(str(allowed)):
        raise ValueError(f"--output must be inside tmp/: got {raw}")
    return target
```

#### 7.2.4 No Network Calls in the Orchestrator

The orchestrator `--run` command makes ZERO network calls. It reads local
files and writes to local SQLite. Network calls only happen during
`--approve` when a handler executes (e.g., Gmail API for `email_send`).
This is the existing handler behavior — no change.

---

## 8. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R-1 | Email signal extractor produces too many false positives | Medium | Low | Already filtered by marketing classifier. Add a confidence threshold (default: 0.7). Burn-in period validates before full integration. Signal cooldown dedup prevents repeat spam. |
| R-2 | SQLite contention between catch-up and Telegram listener | Low | Medium | WAL mode allows concurrent reads. The listener only reads (for poll checks) — writes go through orchestrator. Add `PRAGMA busy_timeout=5000` for graceful retry. |
| R-3 | `pipeline_output.jsonl` too large (500+ records → large file) | Low | Low | Typical pipeline run: 200–400 records × ~2KB each = ~800KB. Well within disk/IO budgets. File is ephemeral — deleted in Step 18. |
| R-4 | ~~Cloud sync corrupts `actions.db`~~ | ~~Low~~ | ~~High~~ | **Mitigated by design:** `actions.db` is platform-local (not cloud-synced). WAL + SHM files never traverse OneDrive. Cross-machine propagation uses the DUAL v1.3 bridge (`ingest_remote`/`apply_remote_result`) over an encrypted exchange folder — not raw DB sync. Audit trail (`state/audit.md`) is cloud-synced for immediate cross-machine visibility. |
| R-5 | User never approves proposals → queue grows unbounded | Medium | Low | `default_expiry_hours: 72` auto-expires old proposals. `archive_after_days: 30` moves expired to archive. `max_queue_size: 1000` hard cap. `--expire` CLI flag for manual cleanup. |
| R-6 | Pattern engine state evaluation is slow (many state files) | Low | Low | Pattern engine is regex-only on YAML frontmatter. Benchmark: <500ms for 20 state files. Timeout guard (RH-5) catches slowness. |
| R-7 | Handler execution fails (API token expired, network down) | Medium | Medium | `ActionExecutor.approve()` catches all exceptions, records failure in DB, logs to audit. User sees "✗ failed: [reason]" and can retry. Token refresh is user-driven (existing auth flow). |
| R-8 | AI doesn't read Step 12.5 correctly or skips it | Low | High | Add Step 12.5 to BOTH `Artha.md` and `Artha.core.md`. Use explicit step numbering. Add "DO NOT SKIP" guard. Burn-in period validates. |
| R-9 | New orchestrator script introduces regressions | Low | Medium | 90%+ of logic reuses existing tested modules. Orchestrator is thin glue. Comprehensive test suite (§10). No changes to existing module internals. |
| R-10 | Cross-platform path issues (Windows backslashes) | Low | Medium | All paths use `pathlib.Path`. Existing `pipeline.py` and `action_queue.py` already work cross-platform. No raw string path manipulation. |
| R-11 | Cross-run signal re-detection produces duplicate proposals | Low | Low | `ActionQueue.propose()` status-based dedup prevents duplicates for pending/deferred proposals. Resolved proposals (approved/rejected/expired) intentionally allow re-detection if the underlying condition persists. Acceptable trade-off (§4.1). |
| R-12 | Platform-dependent handler unavailable on current OS | Low | Medium | RH-1 startup health check marks unavailable handlers. Composer suppresses proposals for unavailable action types. If `--approve` still hits one, executor logs failure cleanly: `"✗ handler unavailable on this platform"`. |
| R-13 | `signal_routing.yaml` and `_FALLBACK_SIGNAL_ROUTING` drift out of sync | Medium | High | §5.2.2 merge behavior ensures YAML overrides (not replaces) hardcoded base. A-11b Phase 0 validation confirms merged routing covers all producer signal types. |
| R-14 | `_ALLOWED_ACTION_TYPES` and `_FALLBACK_ACTION_MAP` drift out of sync | Low | High | §5.2.3 cross-reference invariant + A-13 Phase 0 validation catches mismatches at implementation time. |

---

## 9. Assumptions (Validated)

| # | Assumption | Status | Evidence |
|---|------------|--------|----------|
| A-1 | `ActionComposer.compose()` works end-to-end | ✅ VERIFIED | Tested with `bill_due` signal → `ActionProposal(instruction_sheet, friction=high)` |
| A-2 | `ActionQueue.propose()` inserts to SQLite | ✅ VERIFIED | Tested with temp DB; row inserted with status=pending |
| A-3 | `EmailSignalExtractor.extract()` produces signals | ✅ VERIFIED | RSVP email → `DomainSignal(event_rsvp_needed)` |
| A-4 | Handler import paths are correct (`actions.*`) | ✅ VERIFIED | `_FALLBACK_ACTION_MAP` uses `actions.email_send` etc. |
| A-5 | `PatternEngine` runs without crash | ✅ VERIFIED | `PatternEngine(root_dir=Path('.')).evaluate()` → `[]` |
| A-6 | `actions.db` exists and is writable (platform-local) | ✅ VERIFIED | 4 tables exist, all empty, WAL mode active. Path resolved by `_resolve_db_path()` |
| A-7 | Pipeline outputs sufficient email data for extraction | ✅ VERIFIED | Latest run: 413 records, 26.5s wall-clock |
| A-8 | No new Python dependencies required | ✅ VERIFIED | Uses stdlib + yaml + sqlite3 (builtin) |
| A-9 | `config/artha_config.yaml` has `actions.enabled: true` | ✅ VERIFIED | Line 101: `enabled: true` |
| A-10 | Existing tests pass (no regression baseline) | ⏳ VERIFY AT IMPL | Run `pytest tests/` before starting |
| A-11 | Signal routing table covers common email patterns | ⚠️ PARTIAL | 50+ signal types in `_FALLBACK_SIGNAL_ROUTING` (hardcoded). **However**, `config/signal_routing.yaml` exists with only ~9 entries and `_load_signal_routing()` loads it as a complete replacement (not merge). In production, only the YAML entries route — 42+ hardcoded routes are shadowed. See §5.2.2 for the required fix. |
| A-12 | Telegram approval path works (channel_listener.py) | ⚠️ ASSUMED | Marked "implemented" in `implementation_status.yaml`; not independently tested here |

---

## 10. Test Plan

### 10.1 Unit Tests (`tests/unit/test_action_orchestrator.py`)

| ID | Test | Validates |
|----|------|-----------|
| T-U-01 | `test_run_with_no_emails_file` | Returns 0 proposals; no crash; pattern engine still runs |
| T-U-02 | `test_run_with_empty_emails_file` | Returns 0 signals; exits cleanly |
| T-U-03 | `test_run_with_marketing_only_emails` | All emails filtered; 0 signals |
| T-U-04 | `test_run_with_one_rsvp_email` | 1 email signal → 1 proposal queued |
| T-U-05 | `test_run_with_multiple_signals` | Email + pattern signals merged correctly |
| T-U-06 | `test_deduplication_same_signal_type` | Two `bill_due` signals for same entity → 1 kept |
| T-U-07 | `test_deduplication_different_entities` | Two `bill_due` for different entities → both kept |
| T-U-08 | `test_signal_persistence_to_jsonl` | `tmp/signals.jsonl` written correctly |
| T-U-09 | `test_signal_persistence_excludes_metadata` | No PII in signals file |
| T-U-10 | `test_actions_disabled_returns_zero` | Kill switch → exit 0, no proposals |
| T-U-11 | `test_read_only_mode_returns_zero` | Read-only → exit 0, no DB writes |
| T-U-12 | `test_handler_health_check_reports_failures` | Monkeypatch import error → logged, not crashed |
| T-U-13 | `test_handler_health_check_all_healthy` | No failures → all action types available |
| T-U-14 | `test_compose_failure_does_not_crash_loop` | One bad signal doesn't block others |
| T-U-15 | `test_propose_failure_does_not_crash_loop` | DB error on one proposal doesn't block others |
| T-U-16 | `test_approve_existing_proposal` | `--approve <id>` → handler executes |
| T-U-17 | `test_approve_nonexistent_id` | `--approve bad_id` → error message, exit 1 |
| T-U-18 | `test_reject_existing_proposal` | `--reject <id>` → status=rejected in DB |
| T-U-19 | `test_approve_all_low_only_approves_low` | High-friction proposals untouched |
| T-U-20 | `test_list_output_format` | `--list` output matches expected format |
| T-U-21 | `test_expire_removes_old_proposals` | `--expire` clears proposals past `expires_at` |
| T-U-22 | `test_health_command_reports_status` | `--health` shows handler status + queue stats |
| T-U-23 | `test_summary_output_format` | `--run` stdout matches expected structured format |
| T-U-24 | `test_audit_log_written_on_run` | `state/audit.md` has `ACTION_ORCHESTRATOR` entry |
| T-U-25 | `test_audit_log_written_on_approve` | `state/audit.md` has `ACTION_APPROVED` entry |
| T-U-26 | `test_handler_validation_before_enqueue` | Proposal with missing `to` field for `email_send` → `_validate_proposal_handler()` raises before enqueue |
| T-U-27 | `test_show_content_bearing_proposal` | `--show` on `email_reply` proposal → prints To, Subject, Body fields |
| T-U-28 | `test_show_non_content_proposal` | `--show` on `calendar_create` proposal → prints title + params, no CONTENT PREVIEW section |
| T-U-29 | `test_show_encrypted_params_no_key` | `--show` on encrypted proposal without key → prints locked 🔒 message |
| T-U-30 | `test_defer_preset_tomorrow` | `_resolve_defer_preset("tomorrow")` → next day 09:00 local ISO string |
| T-U-31 | `test_defer_preset_next_session` | `_resolve_defer_preset("next-session")` → +24h ISO string |
| T-U-32 | `test_defer_preset_plus_offset` | `_resolve_defer_preset("+4h")` → +4h ISO string |
| T-U-33 | `test_signal_routing_merge_yaml_over_fallback` | YAML with 3 entries + fallback with 50+ → merged dict has all entries, YAML overrides take precedence |
| T-U-34 | `test_allowed_action_types_matches_handler_map` | `_ALLOWED_ACTION_TYPES - set(_FALLBACK_ACTION_MAP)` → empty set |

### 10.2 Integration Tests (`tests/integration/test_action_orchestrator.py`)

| ID | Test | Validates |
|----|------|-----------|
| T-I-01 | `test_full_pipeline_to_orchestrator` | `pipeline.py --output` → `orchestrator.py --run` → proposals in DB |
| T-I-02 | `test_email_signal_to_proposal_e2e` | Fake RSVP email JSONL → `event_rsvp_needed` signal → `email_reply` proposal |
| T-I-03 | `test_pattern_engine_to_proposal_e2e` | State file with overdue goal → `goal_stale` signal → `instruction_sheet` proposal |
| T-I-04 | `test_approve_email_send_dry_run` | Propose `email_send` → approve → handler creates Gmail draft (mocked) |
| T-I-05 | `test_approve_calendar_create_dry_run` | Propose `calendar_create` → approve → handler creates event (mocked) |
| T-I-06 | `test_cross_session_dedup` | Run orchestrator twice with same emails → no duplicate proposals |
| T-I-07 | `test_kill_switch_full_session` | Disable actions → run pipeline + orchestrator → 0 proposals |
| T-I-09 | `test_concurrent_read_during_write` | Orchestrator writes while `--list` reads → no crash (WAL) |
| T-I-10 | `test_pipeline_output_flag` | `pipeline.py --output tmp/test.jsonl` writes file + stdout |
| T-I-11 | `test_show_expanded_preview` | `--show <id>` prints full content for content-bearing proposals |
| T-I-12 | `test_fresh_snapshot_no_append` | Run pipeline twice with `--output` → file contains only latest run's records |
| T-I-13 | `test_routing_merge_produces_proposals_for_all_email_signals` | All 8 email signal types → at least 8 proposals (with YAML + fallback merge active) |

### 10.3 Security Tests (`tests/unit/test_action_orchestrator_security.py`)

| ID | Test | Validates |
|----|------|-----------|
| T-S-01 | `test_pii_blocked_at_propose` | Signal with SSN in metadata → proposal blocked |
| T-S-02 | `test_pii_allowed_in_allowlisted_fields` | `to` field with email address → proposal allowed |
| T-S-03 | `test_unknown_signal_type_dropped` | Signal with `signal_type: "rm_rf_root"` → no proposal |
| T-S-04 | `test_unknown_action_type_dropped` | Routing table entry with `action_type: "shell_exec"` → blocked |
| T-S-05 | `test_output_path_traversal_blocked` | `--output ../../etc/passwd` → ValueError |
| T-S-06 | `test_output_path_must_be_in_tmp` | `--output config/evil.yaml` → ValueError |
| T-S-10 | `test_autonomy_floor_enforced_on_approve` | `email_send` with `autonomy_floor: true` → requires human `approve()` |
| T-S-11 | `test_handler_not_in_allowlist_blocked` | Modified `_HANDLER_MAP` entry → security log, blocked |
| T-S-12 | `test_encrypted_params_at_rest` | High-sensitivity proposal → params encrypted in DB |
| T-S-13 | `test_signals_file_excludes_metadata` | `tmp/signals.jsonl` has no email bodies or PII |
| T-S-14 | `test_rate_limit_enforced` | 21st `email_send` in 1 hour → rejected |
| T-S-15 | `test_content_preview_required_for_email` | `email_send` proposal → `--show` includes full body text |

### 10.3.1 V1.1 — AI Signal Security Tests *(Gated: `ai_signals: true`)*

> **These tests are scoped to V1.1.** They validate the AI-emitted signal
> path (SP-3/SP-4) which is disabled by default in V1.0. Do NOT include
> in V1.0 CI/CD gates — run only when `ai_signals: true` is enabled.

| ID | Test | Validates |
|----|------|-----------|
| T-S-07 | `test_ai_signals_malformed_json_skipped` | Corrupt JSONL line → skipped, not crashed |
| T-S-08 | `test_ai_signals_missing_required_fields` | Signal without `signal_type` → skipped |
| T-S-09 | `test_ai_signals_injection_signal_type` | `signal_type: "email_send"` not in routing → dropped |
| T-I-08 | `test_orchestrator_with_ai_signals_file` | Write `tmp/ai_signals.jsonl` → orchestrator picks them up |

### 10.4 Cross-Platform Tests

| ID | Test | Validates |
|----|------|-----------|
| T-P-01 | `test_pathlib_paths_no_hardcoded_slash` | Grep orchestrator for hardcoded `/` or `\\` → 0 matches |
| T-P-02 | `test_sqlite_wal_mode_on_open` | DB opens with `PRAGMA journal_mode` → `wal` |
| T-P-03 | `test_output_path_uses_pathlib` | `--output` uses `Path` not `os.path.join` |
| T-P-04 | `test_python3_and_python_compatible` | Script works with both `python3` and `python` executables |

### 10.5 Regression Tests (Existing Suites, Must Pass)

| Suite | File | Tests |
|-------|------|-------|
| Action Queue | `tests/unit/test_action_queue.py` | 313 lines |
| Action Executor | `tests/unit/test_action_executor.py` | 298 lines |
| Signal Routing | `tests/unit/test_signal_routing.py` | 147 lines |
| Safety Red-team | `tests/unit/test_safety_redteam.py` | 311 lines |
| Action Bridge | `tests/unit/test_action_bridge.py` | 809 lines |
| E2E Pipeline | `tests/integration/test_action_pipeline_e2e.py` | 185 lines |

Total existing: ~2,063 lines of action-related tests. **All must pass before
and after implementation.**

### 10.6 Manual Acceptance Tests

| ID | Test | Steps | Expected |
|----|------|-------|----------|
| T-M-01 | First real catch-up with actions | Run `brief`, observe Step 12.5 output | Orchestrator runs, ≥1 signal detected, proposals in briefing |
| T-M-02 | Approve an email draft | Say "approve 1" to an email_send proposal | Gmail draft created, confirmation shown |
| T-M-03 | Reject a proposal | Say "reject 2" | Status changes to rejected in DB, audit logged |
| T-M-04 | Approve all low-friction | Say "approve all low" | Only low-friction proposals execute |
| T-M-05 | Kill switch test | Set `enabled: false`, run catch-up | No proposals, clean skip message |
| T-M-06 | Cross-session persistence | Run catch-up, don't approve. Next session: pending shows prior proposals | Proposals survive across sessions in SQLite |
| T-M-07 | Proposal expiration | Create proposals, wait 72h | `--expire` removes them |
| T-M-08 | Signal audit trail | After catch-up, check `tmp/signals.jsonl` | Signals present with correct types |
| T-M-09 | Action audit trail | After approval, check `state/audit.md` | `ACTION_APPROVED` and `ACTION_EXECUTED` entries |
| T-M-10 | Empty catch-up (no new email) | Run catch-up with `--since 0h` | 0 email signals, pattern engine may still fire, no crash |

---

## 11. Rollback Plan

Every change is independently reversible:

| Change | Rollback |
|--------|----------|
| `scripts/action_orchestrator.py` | Delete the file. Step 12.5 fails gracefully ("orchestrator not found"), catch-up continues |
| `pipeline.py --output` flag | Remove the flag. Orchestrator degrades to "no email data" mode |
| `Artha.md` Step 12.5 | Remove the step. AI skips to Step 13 |
| `artha_config.yaml` | Set `harness.actions.enabled: false` — instant kill switch |
| `actions.db` (platform-local) | Delete the file at the resolved path. Recreated automatically on next run (empty) |

**Nuclear rollback:** Set `harness.actions.enabled: false` and the entire
action layer goes dormant. Zero impact on catch-up quality. This is the
exact state Artha has been in for all 30+ briefings — proven stable.

---

## 12. Success Criteria

### Must-Have (Definition of Done)

- [ ] `action_orchestrator.py --run` produces ≥1 proposal from a real catch-up
- [ ] `action_orchestrator.py --approve <id>` successfully executes a handler
- [ ] `action_orchestrator.py --approve-all-low` batch-approves low-friction only
- [ ] `action_orchestrator.py --defer <id> [--until ...]` defers with preset horizons (`+1h`, `tomorrow`, `next-session`)
- [ ] `actions.db` (platform-local) has >0 rows after a catch-up
- [ ] `state/audit.md` has `ACTION_ORCHESTRATOR` and `ACTION_PROPOSED` entries
- [ ] Kill switch (`enabled: false`) cleanly disables all action behavior
- [ ] All existing tests pass (2,063+ lines of action-related tests)
- [ ] All new tests pass (T-U-01 through T-S-14)
- [ ] Cross-platform: orchestrator runs on both macOS and Windows
- [ ] No new dependencies added to `pyproject.toml`
- [ ] `pipeline.py --output` flag works without breaking existing `--source` / `--since` flags
- [ ] MCP Tier 1 path: orchestrator works with `--mcp` flag (pattern engine only)

### Nice-to-Have (V1.1)

- [ ] AI-emitted signals (`tmp/ai_signals.jsonl`) protocol working (SP-3/SP-4 with security hardening)
- [ ] `compose_workflow()` multi-step workflow — implemented in `action_composer.py` (address_change, tax_prep triggers) but never called in production and has no integration tests
- [ ] `--health` command integrated into `health` Artha command
- [ ] Telegram approval path tested with real proposals
- [ ] Burn-in period completed (5 sessions, <20% false positive rate)

### Measurable Targets (30-Day Post-Launch)

| Metric | Target |
|--------|--------|
| Signals detected per catch-up | ≥2 average |
| Proposals generated per catch-up | ≥1 average |
| False positive rate (proposals rejected / total) | <25% |
| Approval-to-execution success rate | >90% |
| Action layer crash rate (crashes / sessions) | 0% |
| Catch-up time impact (added latency) | <3 seconds |

---

## Appendix A: Implementation Sequence

Strict dependency order — each item depends on the prior:

```
Phase 0:  Validate assumptions (A-1 through A-10)         [0.5 session]
  ↓
Phase 1:  WB-1 (orchestrator.py)                          [1 session]
          WB-2 (pipeline --output, fresh snapshot)          │
          WB-3 (dedup logic — part of WB-1)                 │
          WB-4 (signal persistence — part of WB-1)          │
          WB-5 (audit integration — part of WB-1)           │
  ↓
Phase 2:  SP-1 + SP-2 (wire extractors — part of WB-1)    [already done in WB-1]
  ↓
Phase 3:  PI-1 (Artha.md Step 12.5)                        [1 session]
          PI-2 (Artha.md Step 13 rewrite + preview)         │
          PI-3 (Artha.core.md mirror)                       │
          PI-4 (finalize.md update)                         │
          PI-5 (pipeline --output in Step 4)                │
  ↓
Phase 4:  RH-1 through RH-5 (part of WB-1 implementation) [already done in WB-1]
  ↓
Phase 5:  VB-1 (smoke test)                                [0.5 session]
          VB-2 (kill switch test)                           │
          VB-3 (burn-in period)                            [5 sessions]

--- V1.0 scope boundary ---

Phase 6 (V1.1):  SP-3 + SP-4 (AI signal protocol)        [0.5 session]
                  Requires ai_signals: true + security hardening
                  Tests: T-S-07, T-S-08, T-S-09, T-I-08
```

Total V1.0 implementation: ~3 focused sessions + 5 burn-in sessions.
SP-3/SP-4 (AI signals) deferred to V1.1 — not in the V1.0 delivery path.

---

## Appendix B: Config Changes Summary

### `config/artha_config.yaml` — New flags

```yaml
  harness:
    actions:
      # ...existing flags...
      burn_in: false             # When true, proposals shown in debug section only
      ai_signals: false          # V1.1: AI-emitted signal support (default-off for security)
```

**Path:** `harness.actions.burn_in` and `harness.actions.ai_signals` —
consistent with the existing `harness.actions.enabled` hierarchy.

### `config/implementation_status.yaml` — Update

```yaml
  action_orchestrator:
    spec: "specs/actions-reloaded.md"
    status: implemented          # After Phase 1
    confidence: high
```

---

## Appendix C: File Changes Checklist

- [ ] `scripts/action_orchestrator.py` — CREATE (~250 lines)
- [ ] `scripts/pipeline.py` — MODIFY (add `--output` flag with fresh snapshot semantics, ~20 lines changed)
- [ ] `config/Artha.md` — MODIFY (add Step 12.5, rewrite Step 13 with content preview)
- [ ] `config/Artha.core.md` — MODIFY (add Step 12.5, rewrite Step 13 with content preview)
- [ ] `config/workflow/finalize.md` — MODIFY (update Step 12.5 to CLI)
- [ ] `config/artha_config.yaml` — MODIFY (add `burn_in` flag, deprecate `queue.db_path`)
- [ ] `config/implementation_status.yaml` — MODIFY (add `action_orchestrator`)
- [ ] `tests/unit/test_action_orchestrator.py` — CREATE (~400 lines)
- [ ] `tests/integration/test_action_orchestrator.py` — CREATE (~250 lines)
- [ ] `tests/unit/test_action_orchestrator_security.py` — CREATE (~200 lines)

**Documentation cleanup (post-implementation):**
- [ ] `README.md` — Remove/update "No database" claim; acknowledge bounded local SQLite for write orchestration
- [ ] `specs/artha-tech-spec.md` — Update "Zero custom code" framing; action layer is now a real subsystem
- [ ] `config/artha_config.yaml` — Deprecate `queue.db_path: "state/actions.db"` (not honored by runtime; add deprecation comment)

**Total new code:** ~1,100 lines (orchestrator + tests)
**Total modified code:** ~60 lines across 5 existing files + 3 documentation files
**New dependencies:** 0

---

## Appendix D: ADR — Bounded Local Control-Plane Database

**Status:** Accepted
**Date:** 2026-03-31
**Context:**

Artha's original architecture explicitly forbade databases: "No database,
no daemon, no server — just Markdown files and Python scripts." This was
correct for the read path (domain state, briefings, goals). However, the
action layer requires durable write orchestration with ACID guarantees that
Markdown files cannot provide: proposal lifecycle (pending → approved →
executed), atomic dedup, expiry, cross-session persistence, and bridge
synchronization.

**Decision:**

Allow a bounded local SQLite database (`actions.db`) as a **control-plane
store** for write orchestration only. This database:
1. Lives at a platform-local path (`~/.artha-local/actions.db` on macOS,
   `%LOCALAPPDATA%\Artha\actions.db` on Windows) — NOT inside the
   cloud-synced workspace
2. Contains only action queue state: proposals, execution results, audit
   records, and bridge sync metadata
3. Is not a general-purpose store — domain state, goals, items, and all
   intelligence remain in Markdown files
4. Is fully ephemeral: deleting the file resets the action queue to empty.
   No data loss — the cloud-synced `state/audit.md` preserves the
   human-readable trail
5. Is bounded by `max_queue_size: 1000` and `archive_after_days: 30`

**Consequences:**
- `README.md` should be updated to say "No database for intelligence storage"
  rather than "No database" — the action queue is a bounded exception
- `specs/artha-tech-spec.md` should acknowledge the action layer as a real
  subsystem, not frame it as "zero custom code"
- `config/artha_config.yaml` `queue.db_path: "state/actions.db"` is
  **deprecated** — the runtime ignores this value and uses
  `_resolve_db_path()` instead. Add a deprecation comment and plan removal
  in the next config schema version

**Alternatives considered:**
- JSONL-file queue: rejected — no atomic dedup, no ACID, append-only
  corruption risk under cloud sync
- In-memory only: rejected — proposals must survive across sessions
- Cloud-synced DB: rejected — SQLite WAL+SHM corruption under OneDrive

---

## Appendix E: Operational Telemetry & Metrics

### E.1 Structured Counters

The orchestrator and executor MUST emit structured log lines (to
`state/audit.md` and stderr) with machine-parseable counters. These
supplement the human-readable audit trail with operational metrics:

| Counter | Source | Scope | Description |
|---------|--------|-------|-------------|
| `signals_detected` | orchestrator `--run` | V1.0 | Total signals from all producers |
| `signals_suppressed` | orchestrator `--run` | V1.0 | Signals dropped by within-run dedup |
| `proposals_queued` | orchestrator `--run` | V1.0 | Proposals successfully written to DB |
| `proposals_suppressed` | `ActionQueue.propose()` | V1.0 | Cross-session dedup rejections |
| `proposals_expired` | `expire_stale()` | V1.0 | Past-expiry proposals cleaned up |
| `queue_depth` | `--list` / `--run` | V1.0 | Current pending + deferred count |
| `approvals_total` | parsed from `state/audit.md` | V1.0 | Total `ACTION_APPROVED` entries |
| `approvals_succeeded` | parsed from `state/audit.md` | V1.0 | Total `ACTION_EXECUTED` with `status:success` |
| `approvals_failed` | parsed from `state/audit.md` | V1.0 | Total `ACTION_EXECUTED` with `status:failure` |
| `rejections_total` | parsed from `state/audit.md` | V1.0 | Total `ACTION_REJECTED` entries |
| `deferrals_total` | parsed from `state/audit.md` | V1.0 | Total `ACTION_DEFERRED` entries |
| `handler_latency_ms` | executor `approve()` | V1.1 | Per-handler execution time (requires executor code change to emit) |
| `bridge_lag_s` | bridge sync | V1.1 | Seconds since last successful bridge exchange |
| `health_check_failures` | `--health` | V1.0 | Handlers that failed import check |

**Scope note:** Orchestrator-side counters (rows marked V1.0) are emitted
directly in the `--run` summary. Executor-side approval/rejection/deferral
counters are derived by parsing existing `state/audit.md` log lines
(`ACTION_APPROVED`, `ACTION_EXECUTED`, `ACTION_REJECTED`, `ACTION_DEFERRED`)
rather than requiring code changes to the executor. The `--health` command's
Approval funnel (Appendix E.2) aggregates these parsed counters.
`handler_latency_ms` and `bridge_lag_s` require executor/bridge code changes
and are deferred to V1.1.

**Format:** Each `--run` summary line includes counters as structured
key-value pairs for downstream parsing:

```
[2026-04-01T09:00:00Z] ACTION_ORCHESTRATOR | signals:4 suppressed:1 queued:3 expired:0 depth:5 errors:0
```

### E.2 `--health` Extended Output

The `--health` command reports:

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

### E.3 Optional OTLP Spans (V1.1)

`config/artha_config.yaml` already reserves tracing configuration:

```yaml
tracing:
  enabled: false
  endpoint: "http://localhost:4317"
  service_name: "artha-actions"
```

V1.0 does **not** implement OTLP spans — the structured counters above are
sufficient for initial operational visibility. V1.1 may add optional
OpenTelemetry spans around:
- `orchestrator.run` (full cycle)
- `executor.approve` (per-action)
- `bridge.sync` (per-exchange)

This is documented here to prevent the architecture from claiming
observability features that don't exist yet — V1.0 is logs-and-counters
only, and that is an acceptable starting point.

---

*specs/actions-reloaded.md v1.3.0 — March 31, 2026*

---

## Revision History

| Version | Date | Changes |
|---------|------|---------- |
| 1.0.0 | 2026-03-31 | Initial spec |
| 1.1.0 | 2026-03-31 | Architectural review incorporation: C-1 (DB path fix to platform-local), C-2 (MCP Tier 1 support), C-3 (`--defer` CLI), C-4 (status-based dedup), C-5 (AI signal security hardening + default-off), C-6 (`compose_workflow` V1.1 scope), C-7 (output filename accuracy), G-1 (Step 14.5 Telegram acknowledgment), G-2 (signal coverage audit), G-3 (context window guard), G-4 (`--approve-all-low` promoted to Must-Have), G-5 (`detect_environment` dependency), A-1/A-2/A-4 (design decisions and risk documentation) |
| 1.2.0 | 2026-03-31 | Cross-model review (9 findings): F-1 (defer contract with `--until` presets and `_resolve_defer_horizon()`), F-2 (cross-machine bridge rewrite — strong-local/eventual-bridge consistency), F-3 (pipeline `--output` fresh snapshot via atomic rename, not append), F-4 (content-bearing action expanded preview + `--show` CLI), F-5 (AI-signal tests split to V1.1 gated section; SP-3/SP-4 out of main sequence), F-6 (context truncation: highest-friction then oldest, not urgency), F-7 (dedup coarseness documented + V1.1 `dedup_key` plan), F-8 (Appendix D: ADR for local DB + documentation cleanup checklist), F-9 (Appendix E: operational telemetry counters + extended `--health` + OTLP V1.1 deferral) |
| 1.3.0 | 2026-03-31 | Architect codebase cross-validation (16 findings): F-1 CRITICAL (`decision_log_proposal` handler dead-end — §5.2.3), F-2 CRITICAL (`signal_routing.yaml` shadows 42+ hardcoded routes — §5.2.2 merge fix), F-3 HIGH (`--show <id>` implementation spec added to WB-1), F-4 HIGH (MCP Tier 1 AI-writes-JSONL removed — `--mcp` is only path), F-5 HIGH (defer preset safety — `_resolve_defer_preset()` must fully resolve before executor call), F-6 HIGH (`propose_direct()` validation gap — `_validate_proposal_handler()` added to orchestrator), F-7 MEDIUM (defer resolver renamed `_resolve_defer_preset()` to avoid collision with executor's `_resolve_defer_time()`), F-8 MEDIUM (RH-1 rationale vs existing `run_health_checks()` documented), F-9 MEDIUM (`compose_workflow()` relabeled "implemented, untested" not "dead code"), F-10 MEDIUM (`propose()` raises `ValueError` not "silently dropped" — §4.1 + WB-3 corrected), F-11 MEDIUM (signal count 45+ → 50+), F-12 MEDIUM (burn_in flag explicit path `harness.actions.burn_in` + PI-1 burn-in instruction), F-13 LOW (A-6 venv dependency note for `pii_guard`), F-14 LOW (Phase 0 A-14 deprecated config check), F-15 LOW (PI-2 "before" text quoted for implementers), F-16 LOW (Appendix E counters scoped — executor-side derived from audit parsing, `handler_latency_ms`/`bridge_lag_s` deferred to V1.1) |
