# Artha Multi-Machine Setup — JSON Drop-File Bridge Specification

**Codename:** DUAL  
**Version:** 1.2.0  
**Date:** 2026-03-21  
**Author:** Principal Architect  
**Reviewed by:** Lead Principal System Architect  
**Status:** READY FOR IMPLEMENTATION  
**Depends on:** ACT v1.3.0, Tech Spec v3.9.6, channels.yaml schema v1.0  
**Scope:** Dual-machine (Windows 24×7 + macOS intermittent) running complementary
Artha daemons that share state via OneDrive and exchange action data via
immutable JSON drop-files.

---

## §0 — Executive Summary

Artha currently assumes a single machine. The workspace lives in OneDrive and
syncs across a Windows desktop (`CPC-vemis-DJD0M`, 24×7) and a MacBook
(`Veds-MacBook-Pro-M3Max.local`, a few hours/day).  Each machine has
exclusive access to resources the other cannot reach:

| Resource | Windows | Mac |
|---|---|---|
| Telegram listener (24×7 uptime) | ✅ | ❌ |
| Home Assistant (LAN at 192.168.50.90) | ❌ | ✅ |
| iMessage local DB | ❌ | ✅ |
| WhatsApp local DB | ❌ | ✅ (if configured) |
| macOS Keychain | ❌ | ✅ |
| Windows Credential Manager | ✅ | ❌ |

**Goal:** Run role-divided daemons on both machines to produce a unified,
enriched state — without ever syncing SQLite databases over OneDrive.

**Solution:** Machine-local `actions.db` + an immutable JSON drop-file bridge
in the synced workspace for cross-machine action exchange.

---

## §1 — Architecture

### 1.1 Role Assignment

**Windows ("Telegram Brain", 24×7)**
- Runs `channel_listener.py` — sole Telegram polling consumer
- Runs `nudge_daemon.py` — proactive time-aware nudge checks
- Ingests proposals from `state/.action_bridge/proposals/`
- Presents proposals on Telegram for approval
- Executes approved actions and writes results to `state/.action_bridge/results/`
- Pushes briefings via `channel_push.py`

**Mac ("LAN Enricher", intermittent)**
- Runs catch-up sessions (interactive AI CLI)
- Runs Home Assistant connector → writes `state/home.md`
- Runs iMessage connector → writes messaging state
- Proposes actions to `state/.action_bridge/proposals/`
- Reads action results from `state/.action_bridge/results/`
- Runs vault encrypt/decrypt (age-based)
- Does NOT run `channel_listener.py` (blocked by `listener_host` check)

### 1.2 Data Flow

```
Mac (catch-up)                   OneDrive                   Windows (listener)
┌──────────────┐                                           ┌──────────────┐
│ Catch-up     │                                           │ Telegram     │
│ Step 8–11    │                                           │ Listener     │
│ proposes     │                                           │              │
│ actions      │                                           │ Ingests      │
│     │        │                                           │ proposals    │
│     ▼        │                                           │     │        │
│ Local        │   write-once JSON                         │ Local        │
│ actions.db ──┼──▶ proposals/*.json ─── sync ───▶ read ──▶│ actions.db   │
│              │                                           │     │        │
│              │                                           │     ▼        │
│              │                                           │ Present on   │
│              │                                           │ Telegram     │
│              │                                           │     │        │
│              │                                           │     ▼        │
│              │                       ◀── sync ◀── write  │ Approve/     │
│ Read results │◀── results/*.json ◀───────────────────────│ Reject       │
│     │        │                                           │     │        │
│     ▼        │                                           │     ▼        │
│ Update local │                                           │ Execute &    │
│ actions.db   │                                           │ record       │
└──────────────┘                                           └──────────────┘
```

### 1.3 Bridge Directory Layout

```
state/.action_bridge/
├── proposals/                  ← Mac writes, Windows reads & deletes
│   ├── 2026-03-21T09-15-00Z_a1b2c3d4.json
│   └── 2026-03-21T09-16-30Z_e5f6a7b8.json
├── results/                    ← Windows writes, Mac reads & deletes
│   ├── 2026-03-21T10-30-00Z_a1b2c3d4.json
│   └── 2026-03-21T11-00-00Z_e5f6a7b8.json
├── .bridge_health_mac.json     ← Written by Mac only; last-seen timestamp
└── .bridge_health_windows.json ← Written by Windows only; last-seen timestamp
```

**Naming convention:**
```
{ISO-8601-compact}_{action-uuid-first-8}.json
```
Example: `2026-03-21T09-15-00Z_a1b2c3d4.json`

---

## §2 — Bridge Protocol

### 2.1 Proposal Drop-File Schema

Written by Mac after durable local enqueue succeeds via the shared
`ActionExecutor` post-enqueue helper used by both `propose()` and
`propose_direct()`.

```json
{
  "bridge_version": "1.0",
  "action_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "created_at": "2026-03-21T09:15:00+00:00",
  "origin_host": "Veds-MacBook-Pro-M3Max.local",
  "action_type": "email_send",
  "domain": "kids",
  "preview_redacted": "Reply to school regarding field trip permission",
  "title": "age1:ZXlKaGJHY2lPaUpJVXpJMU5pSjkuLi4=",
  "description": "age1:ZXlKaGJHY2lPaUpJVXpJMU5pSjkuLi4=",
  "parameters": "age1:eyJyZWNpcGllbnRzIjpb...base64...",
  "sensitivity": "standard",
  "friction": "standard",
  "min_trust": 1,
  "reversible": false,
  "undo_window_sec": null,
  "expires_at": "2026-03-24T09:15:00+00:00",
  "source_step": "step_10_reason",
  "source_skill": "skill:email_action_detector",
  "linked_oi": "OI-042"
}
```

**Encryption rule:** All payload-bearing fields in bridge files are encrypted
at rest by default, regardless of action sensitivity. This includes `title`,
`description`, and `parameters` for proposals, and `result_message` and
`result_data` for results. This aligns the bridge with Artha's privacy rule:
PII must not be stored outside designated encrypted files, even in ephemeral
OneDrive-synced artifacts. The field names remain stable and encrypted values
are detected by their `"age1:"` prefix, matching the existing
`action_queue.py` convention. The consumer calls `_decrypt_field()`, which
detects the prefix and decrypts transparently.

**Plaintext routing envelope:** Only non-sensitive routing metadata remains
plaintext in bridge files: `bridge_version`, `action_id`, `created_at`,
`origin_host`, `action_type`, `domain`, `friction`, `min_trust`,
`sensitivity`, `expires_at`, `reversible`, `undo_window_sec`, `source_step`,
`source_skill`, and `linked_oi`.

**Optional redacted preview:** `preview_redacted` is optional and must contain
no PII. It exists only to support operator-friendly queue previews on the
executor machine without weakening encryption of the underlying payload.

### 2.2 Result Drop-File Schema

Written by Windows after `ActionQueue.record_result()` succeeds locally.

```json
{
  "bridge_version": "1.0",
  "action_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "decided_at": "2026-03-21T10:30:00+00:00",
  "decided_by": "user:telegram",
  "origin_host": "CPC-vemis-DJD0M",
  "final_status": "succeeded",
  "result_status": "success",
  "result_preview_redacted": "Email sent successfully",
  "result_message": "age1:ZXlKaGJHY2lPaUpJVXpJMU5pSjkuLi4=",
  "result_data": null,
  "executed_at": "2026-03-21T10:30:05+00:00"
}
```

For rejected/expired/deferred actions:

```json
{
  "bridge_version": "1.0",
  "action_id": "e5f6a7b8-...",
  "decided_at": "2026-03-21T11:00:00+00:00",
  "decided_by": "user:telegram",
  "origin_host": "CPC-vemis-DJD0M",
  "final_status": "rejected",
  "result_status": null,
  "result_message": null,
  "result_preview_redacted": "User rejected action",
  "result_data": null,
  "executed_at": null
}
```

**Encryption rule (results):** Same convention as proposals — the field names
remain stable, payload values carry the `"age1:"` prefix, and the consumer
uses `_decrypt_field()` for transparent detection and decryption.

### 2.3 File Lifecycle — Immutability Guarantees

1. **Write-once:** Every bridge file is created atomically via
   `tempfile.mkstemp()` + `os.replace()` (same pattern as
   `homeassistant.py` cache writes). Once written, the content never changes.

2. **Read-once-then-delete:** The consuming machine reads the file, ingests
   it into its local `actions.db`, then deletes the file. Deletion is
   best-effort — if it fails, the ingestion logic is idempotent (duplicate
   `action_id` is silently skipped).

3. **No in-place edits:** OneDrive conflict copies are impossible because
   no file is ever modified after creation. One machine writes, the other reads
   and deletes. There is no overlapping write window.

4. **TTL garbage collection:** Bridge files older than 7 days are pruned by
   either machine during its startup sequence. This handles edge cases where
   the consuming machine was offline for a week. **Ordering invariant:** GC
   MUST run AFTER ingestion in each cycle, never before. This prevents a
   race at the TTL boundary where a file ages past 7 days between the GC
   scan and the ingestion scan. Files are only GC-eligible if they have
   already been ingested (action_id exists in local DB) OR are older than
   TTL. This mirrors the "dequeue then delete" pattern from message queue
   systems.

5. **Delivery guarantee (outbox pattern):** Result bridge files use
   at-least-once delivery via an outbox column. The `actions` table gains
   a `bridge_synced INTEGER DEFAULT 0` column. After `record_result()`,
   `bridge.write_result()` is called and sets `bridge_synced = 1` on
   success. On each poll cycle, the listener scans for actions where
   `status IN ('succeeded','failed','rejected','expired','cancelled')
   AND bridge_synced = 0` and retries the bridge write. This handles
   crashes between `record_result()` and bridge file creation — without
   this, ~1% of results would silently never reach the proposing machine.

### 2.4 Atomic Write Procedure

```python
import tempfile, os, json
from pathlib import Path

def write_bridge_file(bridge_dir: Path, payload: dict) -> Path:
    """Atomically write a bridge JSON file.
    
    Uses mkstemp + os.replace for crash-safe, OneDrive-safe writes.
    The file appears in the directory only after it is fully written.
    """
    bridge_dir.mkdir(parents=True, exist_ok=True)
    ts = payload["created_at"].replace(":", "-").replace("+", "")[:19] + "Z"
    action_id_short = payload["action_id"][:8]
    filename = f"{ts}_{action_id_short}.json"
    target = bridge_dir / filename
    
    fd, tmp_path = tempfile.mkstemp(
        dir=str(bridge_dir), suffix=".tmp", prefix=".bridge_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(target))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target
```

---

## §3 — Local Database Placement

### 3.1 Database Path

Each machine maintains its own SQLite database outside the OneDrive-synced
workspace. The WAL, SHM, and main DB files are never synced.

| Platform | Local DB path |
|---|---|
| macOS | `~/.artha-local/actions.db` |
| Windows | `%LOCALAPPDATA%\Artha\actions.db` |

**Environment variable override:** `ARTHA_LOCAL_DB` — if set, used as the
full path to `actions.db`. Useful for testing or non-standard setups.

### 3.2 Migration from Current Location

The current `state/actions.db` (in the synced workspace) must be migrated:

1. Copy `state/actions.db` to the new local path.
2. Verify integrity: `PRAGMA integrity_check;`
3. Delete `state/actions.db`, `state/actions.db-wal`, `state/actions.db-shm`
   from the synced workspace.
4. Add `state/actions.db*` patterns to `.gitignore` (already present) and
   optionally to a OneDrive `.nosync` equivalent (OneDrive Settings →
   Exclude folders, if available on the platform).

### 3.3 ActionQueue Path Resolution

`ActionQueue.__init__()` gains a new path resolution method:

```python
def _resolve_db_path(self) -> Path:
    """Determine the local (non-synced) path for actions.db."""
    # 1. Explicit override
    env_path = os.environ.get("ARTHA_LOCAL_DB")
    if env_path:
        return Path(env_path)
    
    # 2. Platform-specific local path
    if sys.platform == "darwin":
        base = Path.home() / ".artha-local"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Artha"
    else:
        base = Path.home() / ".artha-local"
    
    base.mkdir(parents=True, exist_ok=True)
    return base / "actions.db"
```

### 3.4 Backward Compatibility

If the local DB does not exist but `state/actions.db` does (pre-migration),
`ActionQueue` automatically copies it to the local path and logs a warning.
This provides a seamless upgrade path without a manual migration step.

### 3.5 Schema Additions for Bridge Support

`ActionQueue.__init__()` calls `_migrate_schema_if_needed()` after opening the
local DB connection and before normal queue operations begin. The migration is
explicitly idempotent and safe for mixed-version rollout.

**Migration algorithm:**

```python
def _migrate_schema_if_needed(self) -> None:
  with self._conn:
    self._conn.execute("BEGIN IMMEDIATE")
    cols = {
      row[1] for row in self._conn.execute("PRAGMA table_info(actions)")
    }
    if "bridge_synced" not in cols:
      self._conn.execute(
        "ALTER TABLE actions ADD COLUMN bridge_synced INTEGER DEFAULT 0"
      )
    if "origin" not in cols:
      self._conn.execute(
        "ALTER TABLE actions ADD COLUMN origin TEXT DEFAULT 'local'"
      )
    self._conn.execute("PRAGMA user_version = 2")
```

**Why this is safe:**
- The DB is machine-local, so there is no cross-machine schema race.
- `BEGIN IMMEDIATE` serializes local concurrent startup on the same machine.
- Repeated startup is safe because the migration checks existing columns via
  `PRAGMA table_info(actions)` before altering.
- Older binaries continue to function because the migration is additive only:
  extra nullable/defaulted columns do not break older reads or inserts.

- `bridge_synced`: Set to `1` after a result bridge file is successfully
  written for a terminal action. The outbox retry scans for `bridge_synced = 0`
  on terminal actions and retries the write. Only relevant on the executor
  machine (Windows).
- `origin`: `'local'` for actions proposed on this machine, `'bridge'` for
  actions ingested via the bridge. Used in audit trail and `/queue` display.

**New method — `ActionQueue.ingest_remote()`:**

```python
def ingest_remote(self, proposal: ActionProposal, pubkey: str | None = None) -> bool:
    """Ingest a bridge-originated proposal.

    Unlike propose(), this method:
    - Deduplicates on action_id (UUID), NOT action_type+domain
    - Skips queue-size guard (proposing machine already enforced it)
    - Sets origin='bridge' for audit trail clarity

    Returns:
        True if ingested, False if duplicate (already exists by action_id).
    """
    existing = self._conn.execute(
        "SELECT id FROM actions WHERE id = ?", (str(proposal.id),)
    ).fetchone()
    if existing:
        return False  # idempotent skip

    row = self._proposal_to_row(proposal, pubkey, origin="bridge")
    with self._conn:
        self._conn.execute("INSERT INTO actions VALUES (...)", row)
        self._audit(self._conn, proposal.id, "", "pending", "system:bridge")
    return True
```

  ### 3.6 Mixed-Version Rollout Behavior

  The rollout is inherently machine-scoped because each machine owns its own
  local `actions.db`. Mixed-version deployment is therefore acceptable as long
  as these compatibility rules hold:

  1. **New code tolerates old schema:** If `bridge_synced` or `origin` are
    absent, startup migration adds them idempotently before use.
  2. **Old code tolerates new schema:** Existing binaries continue to work with
    additive columns present because their SQL does not require the new fields.
  3. **Bridge activation is config-gated:** `multi_machine.bridge_enabled`
    remains `false` until both machines have the new code and age keys.
  4. **Zero-downtime rollout order:** Upgrade either machine first, verify local
    startup and DB migration, then upgrade the second machine, then enable the
    bridge flag on both.

---

## §4 — Ingestion Logic

### 4.1 Proposal Ingestion (runs on Windows)

Triggered on every poll cycle of `channel_listener.py` (every ~30s), before
processing inbound Telegram messages:

```
1. Glob state/.action_bridge/proposals/*.json
2. For each file (sorted by filename = chronological):
   a. Parse JSON
   b. Validate bridge_version == "1.0"
   c. Check action_id not already in local actions.db (idempotent skip)
   d. Decrypt parameters if "age1:" prefix detected (via _decrypt_field())
   e. Build ActionProposal from bridge fields
   f. ActionQueue.ingest_remote(proposal) into local DB  ← NOT propose()
   g. Delete the bridge file (best-effort)
   h. Log: BRIDGE_INGEST event to state/audit.md
3. Run outbox retry: scan for bridge_synced=0 terminal actions, write result files
4. Run GC: prune bridge files older than TTL (AFTER ingestion, never before)
5. If any proposals were ingested, trigger approval UX refresh
```

**Why `ingest_remote()`, not `propose()`:** The existing `ActionQueue.propose()`
enforces a deduplication rule that rejects new proposals if a PENDING or
DEFERRED action with the same `action_type + domain` combination already
exists. This would silently drop valid bridge proposals (e.g., two separate
`email_send` proposals for domain `kids`). The new `ingest_remote()` method
deduplicates on `action_id` (globally unique UUID) instead, which matches
the bridge's idempotency model. The method also skips queue-size guards
(the proposing machine already enforced them) and marks the action with
`origin = 'bridge'` for audit trail clarity.

### 4.2 Result Ingestion (runs on Mac)

Triggered at catch-up startup, in `artha.py` main entry point before
`briefing_adapter.py` is invoked (see §7 Phase 2, Step 7 for exact location):

```
1. Glob state/.action_bridge/results/*.json
2. For each file (sorted by filename = chronological):
   a. Parse JSON
   b. Validate bridge_version == "1.0"
   c. Look up action_id in local actions.db
   d. If found and not already terminal:
      - transition(action_id, final_status, actor=decided_by)
      - record_result() if final_status in (succeeded, failed)
   e. If already terminal: silently skip (idempotent)
   f. If not found: log warning (orphaned result), skip
   g. Delete the bridge file (best-effort)
   h. Log: BRIDGE_RESULT event to state/audit.md
3. Run GC: prune bridge files older than TTL (AFTER ingestion)
4. Continue with regular catch-up pipeline
```

**Additive-only invariant:** Result ingestion is strictly additive. It
transitions status and records result fields (`result_status`,
`result_message`, `result_data`, `executed_at`) but NEVER overwrites
existing non-null fields (`description`, `parameters`, `source_step`,
`source_skill`, `linked_oi`). The proposing machine's local DB retains
the richer proposal data; the bridge only supplements it with outcome
information. This preserves Artha's Net-Negative Write Guard principle
(Artha.md §Step 8b) — the result ingestion can only add information,
never erase it.

### 4.3 Idempotency

All ingestion is idempotent: duplicate `action_id` values are silently
skipped. This handles three scenarios:

- OneDrive syncs the same file twice (rare but possible during conflicts)
- Consumer fails to delete after successful ingestion
- Manual retry triggers

### 4.4 Proposal Export Ownership

Proposal export is owned by `ActionExecutor`, not `ActionComposer`. A shared
post-enqueue helper, e.g. `_enqueue_and_maybe_export(proposal)`, performs:

1. Durable local enqueue via `ActionQueue.propose()`
2. Bridge export via `bridge.write_proposal()` if bridge is enabled and role is
  `proposer`
3. Audit logging

Both `ActionExecutor.propose()` and `ActionExecutor.propose_direct()` MUST call
this same helper. `ActionComposer` remains responsible only for composing
`ActionProposal` instances, preserving clean architecture boundaries and
ensuring no Mac-originated proposal can bypass bridge export.

---

## §5 — Bridge Health & Observability

### 5.1 Health Files (Per-Machine)

Each machine writes its own health heartbeat file. This preserves the
bridge's core immutability invariant (§2.3) — no file is ever written by
both machines.

**Mac writes** `state/.action_bridge/.bridge_health_mac.json`:

```json
{
  "last_seen": "2026-03-21T09:00:00+00:00",
  "proposals_written": 3,
  "results_read": 2
}
```

**Windows writes** `state/.action_bridge/.bridge_health_windows.json`:

```json
{
  "last_seen": "2026-03-21T09:30:00+00:00",
  "proposals_read": 3,
  "results_written": 2,
  "outbox_pending": 0
}
```

**Why split files, not a shared JSON:** A single shared file would require
both machines to read-modify-write the same file, creating a race condition
at the OneDrive sync layer (not the filesystem layer). Even with atomic
local writes via `tempfile` + `os.replace()`, the sequence
Mac-reads → Windows-reads → Mac-writes → Windows-writes would cause
Windows to overwrite Mac's update with stale data. Split files eliminate
this class of conflict entirely — each machine owns exactly one file.

The staleness check reads the **peer's** file: Windows reads
`.bridge_health_mac.json`, Mac reads `.bridge_health_windows.json`.

### 5.2 Staleness Alerts

If `channel_listener.py` sees `mac.last_seen` older than 48 hours, it
appends a warning to the next Telegram status response:

> ⚠️ Mac not seen in 48h — HA data and messaging state may be stale.

If the Mac catch-up sees `windows.last_seen` older than 48 hours:

> ⚠️ Windows listener not seen in 48h — Telegram actions may not be processed.

### 5.3 Audit Events

| Event | Actor | Details |
|---|---|---|
| `BRIDGE_PROPOSAL_WRITE` | Mac | action_id, action_type, domain |
| `BRIDGE_PROPOSAL_INGEST` | Windows | action_id, action_type, origin_host |
| `BRIDGE_PROPOSAL_SKIP` | Windows | action_id (already exists — idempotent) |
| `BRIDGE_RESULT_WRITE` | Windows | action_id, final_status |
| `BRIDGE_RESULT_INGEST` | Mac | action_id, final_status, origin_host |
| `BRIDGE_RESULT_ORPHAN` | Mac | action_id (not found in local DB) |
| `BRIDGE_GC` | Either | files_pruned, oldest_file_age |
| `BRIDGE_HEALTH_STALE` | Either | stale_machine, hours_since_last_seen |

### 5.4 Metrics

Bridge metrics follow Artha's existing lightweight metrics pattern used by
`tmp/pipeline_metrics.json` and `tmp/skills_metrics.json`. No new dependency is
introduced in v1.2.0.

**Metrics sink:** `tmp/bridge_metrics.json`

**Counters:**
- `proposals_written`
- `proposals_ingested`
- `results_written`
- `results_ingested`
- `outbox_retry_count`
- `outbox_pending`
- `orphan_results`
- `gc_deleted_files`

**Latency histograms (stored as rolling lists or summarized buckets):**
- `proposal_sync_latency_ms`
- `result_sync_latency_ms`
- `outbox_retry_latency_ms`

These metrics support `/status`, troubleshooting, and future evaluation work
without requiring OpenTelemetry or an external collector.

### 5.5 Trace Boundaries

v1.2.0 does not add an OpenTelemetry dependency. Instead, the bridge defines a
minimal telemetry interface with no-op default implementation so tracing can be
added later without refactoring domain logic.

**Trace/span boundaries:**
- `bridge.write_proposal`
- `bridge.ingest_proposals`
- `bridge.write_result`
- `bridge.ingest_results`
- `bridge.retry_outbox`

This preserves maintainability while keeping the implementation stdlib-only.

---

## §6 — Changes Required

### 6.1 New Files

| File | LOC (est.) | Purpose |
|---|---|---|
| `scripts/action_bridge.py` | ~300 | Bridge read/write/ingest/gc logic |
| `tests/unit/test_action_bridge.py` | ~250 | Unit tests for bridge protocol |

### 6.2 Modified Files

| File | Change |
|---|---|
| `scripts/action_queue.py` | `_resolve_db_path()` — local path resolution; backward compat auto-copy; new `ingest_remote()` method; `bridge_synced` column migration |
| `scripts/action_executor.py` | Shared post-enqueue helper used by both `propose()` and `propose_direct()`; after `record_result()`, call `bridge.write_result()` + set `bridge_synced=1` if bridge enabled |
| `scripts/channel_listener.py` | Add `bridge.ingest_proposals()` call in poll loop; wire into approval UX |
| `scripts/nudge_daemon.py` | Fix `import fcntl` → cross-platform shim (see §8.4) |
| `config/channels.yaml` | Add `bridge.enabled: true` under `defaults` |
| `config/artha_config.yaml` | Add `multi_machine.bridge_enabled: true`, `multi_machine.role: auto` |
| `.gitignore` | Add `state/.action_bridge/` (bridge files are ephemeral, machine-specific) |
| `scripts/preflight.py` | Add P1 bridge health check (bridge dir exists, health file fresh) |

### 6.3 Configuration

New settings in `config/artha_config.yaml`:

```yaml
multi_machine:
  bridge_enabled: false          # Set true on both machines to activate
  role: auto                     # auto | proposer | executor
  bridge_dir: state/.action_bridge
  proposal_ttl_days: 7           # GC proposals older than this
  result_ttl_days: 7             # GC results older than this
  health_stale_hours: 48         # Warn if peer not seen in this many hours
```

`role: auto` means:
- If `listener_host` matches current hostname → `executor` (ingests proposals, writes results)
- If `listener_host` does NOT match → `proposer` (writes proposals, reads results)

---

## §7 — Implementation Plan

### Phase 1: Foundation (no behavioral changes)

1. **Create `scripts/action_bridge.py`** — write/read/ingest/gc functions.
   All I/O via atomic `tempfile` + `os.replace()`.
2. **Create `tests/unit/test_action_bridge.py`** — test atomic writes,
  idempotent ingestion, GC, schema migration, full-payload encryption,
  and telemetry output.
3. **Modify `scripts/action_queue.py`** — local DB path resolution plus
  `_migrate_schema_if_needed()` using `PRAGMA table_info(actions)` /
  `PRAGMA user_version`. If `state/actions.db` exists and local DB does not,
  auto-copy + warn.
4. **Fix `scripts/nudge_daemon.py`** — replace `import fcntl` with
   cross-platform shim (see §8.4).

### Phase 2: Wire the bridge

5. **Modify `scripts/action_executor.py`** — In the `approve()` method,
   after `self._queue.record_result()` succeeds (line ~418), call
   `bridge.write_result(action_id, result, executed_at)` and then
   `self._queue.set_bridge_synced(action_id)`. Wrap in try/except —
   bridge write failure must not block the execution pipeline. The
   outbox retry (Step 6) will catch any failures.
6. **Modify `scripts/channel_listener.py`** — In `run_listener()` at
   line ~2900, before the `asyncio.gather()` poll call, add:
   ```python
   if bridge_enabled:
       bridge.ingest_proposals(queue, bridge_dir / "proposals", privkey)
       bridge.retry_outbox(queue, bridge_dir / "results")
       bridge.gc(bridge_dir, proposal_ttl, result_ttl)
   ```
   Order matters: ingest first, then outbox retry, then GC (§2.3 item 4).
   Newly ingested proposals appear in the approval UX immediately.
  7. **Add proposal export hooks in `scripts/action_executor.py`** — Create a
     shared private helper, e.g. `_enqueue_and_maybe_export(proposal)`, and call
     it from both `propose()` and `propose_direct()`. The helper performs local
     enqueue first, then `bridge.write_proposal(proposal, bridge_dir /
     "proposals", pubkey)` if bridge is enabled. `ActionComposer` remains
     unchanged; it should never write bridge files directly.
  8. **Add catch-up hooks in `artha.py`** — Before briefing adapter invocation:
   - **Before briefing adapter invocation:** Call
     `bridge.ingest_results(queue, bridge_dir / "results", privkey)` then
     `bridge.gc(bridge_dir, proposal_ttl, result_ttl)`. This ensures
     action outcomes are reflected in the catch-up briefing.
  9. **Update preflight** — add P1 bridge health check in `scripts/preflight.py`.
   Create `check_bridge_health() -> CheckResult` that verifies:
   bridge dir exists, peer health file is fresh (< `health_stale_hours`),
   no stale `.tmp` files, no orphaned conflict copies.

### Phase 3: Operational polish

  10. **Bridge health heartbeats** — write `.bridge_health_windows.json` during
    listener poll loop (Windows) and `.bridge_health_mac.json` during
    catch-up startup (Mac). Each machine writes only its own file.
  11. **Bridge metrics + telemetry** — write `tmp/bridge_metrics.json`, surface
    counters in `/status`, and wrap bridge operations in the no-op telemetry
    interface defined in §5.5.
  12. **Staleness alerts** — surface in `/status` Telegram command and
    catch-up briefing header.
  13. **Config + docs** — update `config/artha_config.yaml`,
    `docs/channels.md` (multi-machine section), `README.md` quickstart.

---

## §8 — Risk Analysis

### 8.1 OneDrive Sync Latency

**Risk:** Proposals written by Mac may take 30s–2m to appear on Windows
(and vice versa for results). Actions are not real-time.

**Severity:** LOW

**Mitigation:** Acceptable for the use case. Artha's personal-life domains
operate on hours/days timescales. The user's mental model is already
"Mac catch-up proposes, Telegram presents later." If latency is critical,
the user can force-sync OneDrive: `onedrive --synchronize` (Linux) or
right-click → Sync in Finder/Explorer.

**Assumption:** OneDrive sync interval is ≤5 minutes under normal conditions.
If OneDrive sync is paused or offline, proposals queue up and are ingested
on the next sync. No data loss.

### 8.2 OneDrive Conflict Copies

**Risk:** If both machines write to the same filename simultaneously,
OneDrive creates a conflict copy (e.g., `file-CPC-vemis-DJD0M.json`).

**Severity:** LOW (by design)

**Mitigation:** Bridge files are write-once by a single author (Mac writes
proposals, Windows writes results). Health files are split per-machine
(§5.1) — no shared-write files exist in the bridge directory. The
`.gitignore` already patterns `*conflicted*` and `*-CPC-vemis-DJD0M*`.
GC prunes any stale conflict copies.

### 8.3 State File Conflicts During Catch-Up

**Risk:** Mac catch-up writes `state/open_items.md`, `state/home.md`, etc.
while Windows listener gets a `/items add` Telegram command that also writes
to `state/open_items.md`. OneDrive may conflict.

**Severity:** MEDIUM

**Mitigation:**
- Minimize overlap window: avoid Telegram `/items add` commands during
  Mac catch-up (user discipline).
- Listener writes to `open_items.md` use `fcntl.flock()` for local
  process safety, but this doesn't help across machines.
- OneDrive conflict copies are detectable: add a `bridge.detect_conflicts()`
  check that globs for `*conflicted*` or `*-CPC-vemis-DJD0M*` in `state/`
  and alerts via Telegram or catch-up header.
- Long-term: move `open_items.md` writes through the bridge protocol too
  (future enhancement, not in v1.0).

**Assumption:** OneDrive sync delay is ~30s. If both machines write to the
same `.md` file within 30s, a conflict copy is created. The original file
retains the first-to-sync version; the conflict copy contains the other.
Neither version is lost, but manual merge may be needed.

### 8.4 `fcntl` Not Available on Windows

**Risk:** `nudge_daemon.py` imports `fcntl` at module level (line 38).
`fcntl` is Unix-only. The daemon crashes on Windows with `ImportError`.

**Severity:** MEDIUM (blocks nudge daemon on Windows)

**Mitigation:** Replace with cross-platform shim:

```python
# Replace:
import fcntl

# With:
try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

# In the locking code:
def _flock_write(path: Path, content: str) -> None:
    """Append to file with advisory lock (cross-platform)."""
    with open(path, "a", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        elif sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        fh.write(content)
        # Locks are released when the file handle is closed
```

Also applies to `pattern_engine.py` (line 240, 246) and
`channel_listener.py` (line 2233) inbox writes.

### 8.5 SQLite Database Divergence

**Risk:** The two local `actions.db` files will diverge over time. Actions
proposed on Mac exist in Mac's DB. Approvals on Windows exist in Windows' DB.
Neither machine has a complete picture.

**Severity:** MEDIUM

**Mitigation:** This is by design. Each machine has a partial view:

| Machine | Sees locally | Learns via bridge |
|---|---|---|
| Mac | Proposed actions (with parameters) | Approval/rejection outcomes |
| Windows | Ingested proposals + execution results | Nothing from Mac beyond proposals |

The **catch-up briefing** (Mac) can display: "3 actions proposed yesterday →
2 approved, 1 rejected" by reading results from the bridge. The **Telegram
`/queue` command** (Windows) shows all pending proposals including those
ingested from the bridge.

For a unified view, either machine can reconstruct the full picture from
its local DB + the bridge files. A future `bridge.reconcile()` function
could periodically compare and align.

**Assumption:** The user does not need real-time unified view. The Mac
sees outcomes at the next catch-up. The Windows sees proposals within
minutes of sync.

### 8.6 Bridge File Accumulation

**Risk:** If the consuming machine is offline for an extended period, bridge
files accumulate in OneDrive and are synced to both machines.

**Severity:** LOW

**Mitigation:** TTL-based garbage collection. Files older than
`proposal_ttl_days` (default: 7) are deleted by whichever machine runs
first. Expired proposals also have `expires_at` set (default: 72h per
ACT spec), so stale proposals are naturally invalid.

At 5 actions/day × 7 days = 35 files max (< 100 KB). Negligible.

### 8.7 Age Encryption Key Availability

**Risk:** All bridge payload-bearing fields (`title`, `description`,
`parameters`, `result_message`, `result_data`) are age-encrypted (with
`"age1:"` prefix). Windows needs the age private key to decrypt proposals
before presenting them for approval, and Mac needs it to decrypt result
payloads.

**Severity:** MEDIUM

**Mitigation:**
- The age recipient key is in `config/user_profile.yaml` (public key) —
  synced via OneDrive, available on both machines.
- The age private key lives in the system keyring (macOS Keychain /
  Windows Credential Manager). Both machines must have the private key
  stored in their respective keyrings.
- Optional `preview_redacted` / `result_preview_redacted` fields allow minimal
  plaintext operator context without storing raw PII in the bridge.
- `scripts/preflight.py` must treat age key availability as a bridge-blocking
  P0 dependency when `multi_machine.bridge_enabled = true`.

**Assumption:** The age private key has been stored in Windows Credential
Manager via `scripts/vault.py import-key` or equivalent setup step.

### 8.8 Proposal Without Matching Handler on Windows

**Risk:** Mac proposes an action (e.g., `imessage_send`) that requires a
macOS-only handler. Windows ingests the proposal but can't execute it.

**Severity:** LOW

**Mitigation:** Action handlers have a `health_check()` method. Before
execution, `ActionExecutor` calls `handler.health_check()`. If the handler
reports unhealthy (e.g., iMessage unavailable on Windows), execution fails
with a clear message: "Handler unavailable on this platform."

Better: add a `requires_platform` field to the handler allowlist. During
ingestion, skip proposals whose handler requires a platform not available
on the current machine. Leave the bridge file in place for potential
ingestion by the correct machine.

### 8.9 Credential Store Divergence

**Risk:** OAuth tokens for Gmail/Outlook are stored in `.tokens/` (synced
via OneDrive). But keyring entries (Telegram bot token, age key, HA token)
are per-machine and NOT synced.

**Severity:** LOW (natural segmentation)

**Mitigation:** Already naturally partitioned:

| Credential | Stored in | Needed on |
|---|---|---|
| Telegram bot token | Windows keyring | Windows only |
| HA long-lived token | macOS Keychain | Mac only |
| Age private key | Both keyrings | Both |
| Gmail OAuth token | `.tokens/gmail-token.json` (OneDrive) | Both |
| MS Graph OAuth token | `.tokens/msgraph-token.json` (OneDrive) | Both |

No action required. Each machine has what it needs. The age key is the only
credential that must be manually set up on both machines.

### 8.10 PID File Sync Pollution

**Risk:** `state/.channel_listener.pid` is written by Windows. OneDrive
syncs it to Mac. Mac's singleton check could misread it.

**Severity:** NONE (already mitigated)

**Mitigation:** Mac never reaches the singleton check because
`verify_listener_host()` returns `False` first (hostname mismatch).
The PID file is also in `.gitignore`.

### 8.11 Clock Skew Between Machines

**Risk:** If Windows and Mac system clocks differ, timestamps in bridge
files, expiration checks, and audit logs will be inconsistent.

**Severity:** LOW

**Mitigation:** Both machines should use NTP. Artha's `expires_at` checks
use UTC timestamps with ISO-8601 format. A few seconds of skew is harmless.
If clocks differ by minutes, the worst case is a slightly early/late
`expires_at` enforcement — non-critical for 72h-window actions.

**Assumption:** Both machines have NTP enabled (default on macOS and Windows).

### 8.12 Concurrent Catch-Up on Both Machines

**Risk:** User starts catch-up on Mac and also triggers `/catchup` on
Telegram (which runs a lightweight catch-up on Windows).

**Severity:** LOW

**Mitigation:** The Telegram `/catchup` command reads existing state files
and summarizes them — it does NOT run the full 21-step pipeline. Only the
Mac terminal catch-up writes state files. No conflict.

---

## §9 — Assumptions

1. **OneDrive is the sole sync layer.** Both machines share the Artha
   workspace at the same OneDrive path. No Git push/pull is used for
   runtime state sync (Git is for code, not state).

2. **One machine per role.** Only one machine runs `channel_listener.py`
   at a time (enforced by `listener_host` in `channels.yaml`). Only one
   machine runs the catch-up pipeline at a time (enforced by user behavior
   — catch-up is interactive).

3. **Mac is intermittent.** The Mac may be offline for days. Windows must
   function independently (Telegram, nudges, approval UX) using only the
   state files synced via OneDrive.

4. **Low action throughput.** ~5 actions/day. Bridge files are small
   (< 5 KB each). Storage and sync overhead is negligible.

5. **No real-time requirement.** Minutes of latency between proposal
   creation and Telegram presentation is acceptable.

6. **Age keys on both machines.** The user has run the vault setup on
  both machines so that age encrypt/decrypt works on both. This is
  mandatory because all bridge payload-bearing fields are encrypted.

7. **OneDrive sync is reliable.** Files written in a directory will sync
   to the other machine within 5 minutes under normal conditions. If
   OneDrive sync is broken, both machines degrade to independent operation
   (no data loss, just no cross-machine actions).

8. **State `.md` file conflicts are rare.** The Windows listener writes
   to `state/*.md` only for a few commands (`/items add`, `/items done`,
   `/remember`). The Mac catch-up writes to all state files during Steps
   5–17. The overlap window is small because the Mac is on for only a few
   hours and the user is unlikely to issue Telegram write commands
   simultaneously.

9. **Python stdlib only.** No new external dependencies. `tempfile`,
   `json`, `os`, `pathlib`, `sqlite3` — all stdlib.

---

## §10 — Non-Goals (v1.0)

- **Bi-directional action execution:** Only Windows executes actions
  in v1.0. Mac proposes, Windows executes. A future version could let
  Mac execute HA-specific or iMessage-specific actions locally.
- **Automatic conflict resolution:** If OneDrive creates a conflict copy
  of a `.md` state file, v1.0 only detects and alerts. Manual merge is
  required.
- **Trust metric synchronization:** Trust metrics live in each machine's
  local DB. They may diverge (Windows sees execution outcomes, Mac doesn't
  update trust). Acceptable because trust elevation is conservative and
  human-approved.
- **Bridge for non-action data:** Only action proposals and results
  use the bridge. Other cross-machine data (HA cache, messaging state)
  flows through regular OneDrive-synced state files.
- **Cloud database:** Explicitly rejected in favor of local-first, no
  new dependencies, no privacy exposure.

---

## §11 — Testing Strategy

### Unit Tests (`tests/unit/test_action_bridge.py`)

| Test | Validates |
|---|---|
| `test_write_proposal_creates_atomic_file` | Atomic write via tempfile + os.replace |
| `test_write_proposal_encrypted_fields` | `title`, `description`, and `parameters` always carry `"age1:"` prefix |
| `test_ingest_proposal_new` | New proposal ingested via `ingest_remote()` into local DB |
| `test_ingest_proposal_duplicate_skipped` | Existing action_id silently skipped (UUID dedup) |
| `test_ingest_proposal_bypasses_type_domain_dedup` | Two proposals with same action_type+domain but different action_id both ingested |
| `test_propose_and_propose_direct_share_export_helper` | Both ActionExecutor enqueue paths export through the same post-enqueue bridge helper |
| `test_ingest_proposal_invalid_schema` | Malformed JSON logged and skipped |
| `test_ingest_proposal_deletes_file` | Bridge file deleted after successful ingestion |
| `test_write_result_creates_atomic_file` | Result file written atomically |
| `test_ingest_result_updates_local_db` | Local DB status updated from result |
| `test_ingest_result_orphan_logged` | Missing action_id logged as warning |
| `test_gc_prunes_old_files` | Files > TTL deleted |
| `test_gc_preserves_fresh_files` | Recent files not deleted |
| `test_health_file_per_machine_isolation` | Mac writes only `_mac.json`, Windows writes only `_windows.json` |
| `test_schema_migration_idempotent` | Repeated startup does not fail when bridge columns already exist |
| `test_old_binary_compat_after_additive_columns` | Older code path tolerates `bridge_synced` and `origin` columns present |
| `test_outbox_retry_writes_missing_result` | Terminal action with `bridge_synced=0` gets result file written |
| `test_outbox_sets_synced_flag` | `bridge_synced=1` after successful bridge write |
| `test_ingest_result_additive_only` | Result ingestion never overwrites existing non-null proposal fields |
| `test_bridge_metrics_written` | Counters and latency metrics are emitted to `tmp/bridge_metrics.json` |
| `test_bridge_disabled_noop` | No bridge files created when disabled |
| `test_cross_platform_path_resolution` | Local DB path correct per platform |
| `test_backward_compat_auto_copy` | Pre-migration state/actions.db auto-copied |

### Integration Tests (manual, documented)

1. Propose action on Mac → verify file appears in `state/.action_bridge/proposals/`
2. Start Windows listener → verify proposal ingested and appears in `/queue`
3. Approve on Telegram → verify result file appears in `state/.action_bridge/results/`
4. Run Mac catch-up → verify result ingested and local DB updated
5. Verify GC: create old bridge files → confirm pruned on next startup

---

## §12 — Rollback Plan

If the bridge causes problems:

1. Set `multi_machine.bridge_enabled: false` in `config/artha_config.yaml`
2. Delete `state/.action_bridge/` directory
3. Move `actions.db` back from local path to `state/` (if reverting fully)
4. All existing functionality continues unchanged — bridge is additive

The bridge is a pure addition. It does not modify the existing
single-machine code path. When `bridge_enabled: false` (default), all
existing behavior is preserved exactly.

---

## §13 — Architectural Review Changelog

### v1.2.0 (2026-03-21) — Privacy, Migration, and Telemetry Revision

Incorporates selected second-pass review findings.

| # | Finding | Severity | Resolution |
|---|---------|----------|------------|
| D.1 | Bridge export attached to only one proposal API misses the dominant `propose_direct()` path | HIGH | Proposal export is now owned by a shared `ActionExecutor` post-enqueue helper used by both `propose()` and `propose_direct()` (§4.4, §7 step 7). |
| D.2 | Bridge permits plaintext synced payloads, expanding Artha's privacy surface | HIGH | All payload-bearing fields are encrypted at rest in bridge files by default; only a minimal routing envelope remains plaintext, with optional redacted previews (§2.1, §2.2, §8.7). |
| D.3 | Raw `ALTER TABLE` plan is not idempotent and not safe for mixed-version rollout | MEDIUM | Added `_migrate_schema_if_needed()` using `PRAGMA table_info(actions)` / `PRAGMA user_version`, plus explicit mixed-version rollout rules (§3.5, §3.6). |
| D.4 | Observability was logging-only | MEDIUM | Added bridge metrics, latency tracking, and no-op trace boundaries aligned with existing lightweight metrics conventions and no new dependency requirement (§5.4, §5.5). |

### v1.1.0 (2026-03-21) — Post-Review Revision

Incorporates findings from Lead Principal System Architect review.

| # | Finding | Severity | Resolution |
|---|---------|----------|------------|
| A.1 | Bridge result write has no delivery guarantee — crash between `record_result()` and bridge write silently loses the result | CRITICAL | Added outbox pattern: `bridge_synced` column (§3.5), retry scan in listener poll loop (§4.1 step 3), outbox-aware write in `action_executor.py` (§7 Phase 2 step 5). At-least-once delivery guaranteed. |
| A.2 | `ActionQueue.propose()` dedup on `action_type+domain` silently drops valid bridge proposals with same type+domain | CRITICAL | Created dedicated `ingest_remote()` method (§3.5) that deduplicates on `action_id` (UUID) instead. §4.1 updated to call `ingest_remote()`, not `propose()`. |
| A.3 | `.bridge_health.json` shared-write violates §2.3 immutability invariant — OneDrive sync-layer race causes data loss | CRITICAL | Split into per-machine files: `.bridge_health_mac.json` and `.bridge_health_windows.json` (§5.1). Each machine owns exactly one file. |
| B.1 | Result ingestion could overwrite richer local proposal data during future reconciliation | MEDIUM | Added additive-only invariant to §4.2: result ingestion only writes outcome fields, never overwrites `description`, `parameters`, or other proposal-originated fields. |
| B.2 | `parameters_encrypted` field-name substitution diverges from `action_queue.py` convention (`"age1:"` prefix detection) | MEDIUM | Unified to single `parameters` field name with `"age1:"` prefix for encrypted values (§2.1, §2.2). Matches existing `_encrypt_field()` / `_decrypt_field()` pattern. |
| C.1 | Catch-up hook points reference conceptual step numbers, not code locations | LOW | §7 Phase 2 steps 5–8 now specify exact files, methods, and line numbers for each hook insertion point. |
| C.2 | GC can race with inflight ingestion at TTL boundary — deletes file before ingestion reads it | LOW | §2.3 item 4 now mandates "ingest first, GC second" ordering. §4.1 and §4.2 both sequence GC after ingestion. |
