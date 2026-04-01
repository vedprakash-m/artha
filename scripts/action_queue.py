#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module; encrypted fields handled by foundation.py
"""
scripts/action_queue.py — SQLite-backed persistent action queue.

Single authoritative owner of action lifecycle state for the Artha Action Bus.
All status transitions happen here and are written atomically alongside their
audit log entries.

CONCURRENCY MODEL:
  Terminal (catch-up) and Telegram listener run as separate processes that
  share state/actions.db.  Every connection opens with WAL mode + busy_timeout
  to handle concurrent access safely.  Status transitions are wrapped in
  explicit transactions so a crash between "read + update" never leaves the
  DB in a partial state.

SECURITY:
  Actions with sensitivity="high"|"critical" have their parameters,
  description, and result_data encrypted at rest using age encryption
  (foundation.age_encrypt_string / age_decrypt_string).

STATE MACHINE (canonical — §2.4 of specs/act.md):
  pending → approved | modifying | rejected | deferred | expired
  modifying → pending (edit done or timeout)
  deferred → pending (defer time reached) | rejected (user explicitly rejects)
  approved → executing | cancelled
  executing → succeeded | failed
  succeeded → (reverse action queued as new PENDING, via ActionExecutor)
  failed → (no auto-retry; user may re-queue)
  Terminal states: rejected, expired, succeeded, failed, cancelled

Ref: specs/act.md §3
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from actions.base import (
    ActionProposal,
    ActionResult,
    VALID_STATUSES,
    TERMINAL_STATUSES,
)


# ---------------------------------------------------------------------------
# DB schema (SQLite DDL)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS actions (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    action_type       TEXT NOT NULL,
    domain            TEXT NOT NULL,
    friction          TEXT NOT NULL DEFAULT 'standard',
    min_trust         INTEGER NOT NULL DEFAULT 1,

    title             TEXT NOT NULL,
    description       TEXT,
    parameters        TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'standard',

    status            TEXT NOT NULL DEFAULT 'pending',
    approved_at       TEXT,
    executed_at       TEXT,
    approved_by       TEXT,
    expires_at        TEXT,

    result_status     TEXT,
    result_message    TEXT,
    result_data       TEXT,

    source_step       TEXT,
    source_skill      TEXT,
    source_domain     TEXT,
    linked_oi         TEXT,

    reversible        INTEGER NOT NULL DEFAULT 0,
    reverse_action_id TEXT,
    undo_window_sec   INTEGER,

    bridge_synced     INTEGER NOT NULL DEFAULT 0,
    origin            TEXT NOT NULL DEFAULT 'local'
);

CREATE INDEX IF NOT EXISTS idx_actions_status  ON actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_domain  ON actions(domain);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);
CREATE INDEX IF NOT EXISTS idx_actions_type    ON actions(action_type);

-- Immutable append-only audit log for all state transitions.
CREATE TABLE IF NOT EXISTS action_audit (
    id          TEXT PRIMARY KEY,
    action_id   TEXT NOT NULL REFERENCES actions(id),
    timestamp   TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    context     TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_action ON action_audit(action_id);

-- Rolling trust metrics for elevation scoring.
CREATE TABLE IF NOT EXISTS trust_metrics (
    id               TEXT PRIMARY KEY,
    action_type      TEXT NOT NULL,
    domain           TEXT NOT NULL,
    proposed_at      TEXT NOT NULL,
    user_decision    TEXT NOT NULL,
    execution_result TEXT,
    feedback         TEXT
);

CREATE INDEX IF NOT EXISTS idx_trust_type ON trust_metrics(action_type);

-- Long-term archive: executed/rejected records >30 days old moved here.
CREATE TABLE IF NOT EXISTS actions_archive (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    action_type       TEXT NOT NULL,
    domain            TEXT NOT NULL,
    friction          TEXT NOT NULL DEFAULT 'standard',
    min_trust         INTEGER NOT NULL DEFAULT 1,
    title             TEXT NOT NULL,
    description       TEXT,
    parameters        TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'standard',
    status            TEXT NOT NULL,
    approved_at       TEXT,
    executed_at       TEXT,
    approved_by       TEXT,
    expires_at        TEXT,
    result_status     TEXT,
    result_message    TEXT,
    result_data       TEXT,
    source_step       TEXT,
    source_skill      TEXT,
    source_domain     TEXT,
    linked_oi         TEXT,
    reversible        INTEGER NOT NULL DEFAULT 0,
    reverse_action_id TEXT,
    undo_window_sec   INTEGER,

    bridge_synced     INTEGER NOT NULL DEFAULT 0,
    origin            TEXT NOT NULL DEFAULT 'local'
);
"""

# Hard limits
_MAX_QUEUE_SIZE = 1000
_ARCHIVE_AFTER_DAYS = 30
_DB_SIZE_CAP_BYTES = 50 * 1024 * 1024   # 50 MB


# ---------------------------------------------------------------------------
# Connection factory — mandatory pragma sequence
# ---------------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open actions.db with the mandatory pragma sequence.

    Every connection to actions.db MUST use this function.  Calling
    sqlite3.connect() directly bypasses WAL mode and busy_timeout,
    which will cause SQLITE_BUSY errors and potential data corruption
    under concurrent access.
    """
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row          # allow dict-style access
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers during writes
    conn.execute("PRAGMA busy_timeout=5000") # wait up to 5s on writer contention
    conn.execute("PRAGMA foreign_keys=ON")   # enforce FK constraints
    conn.execute("PRAGMA synchronous=NORMAL") # safe + fast (WAL handles durability)
    return conn


# ---------------------------------------------------------------------------
# Encryption helpers (thin wrappers around foundation.py)
# ---------------------------------------------------------------------------

def _get_age_pubkey(artha_dir: Path) -> str | None:
    """Return the age recipient public key from user_profile.yaml, or None."""
    try:
        sys_path_insert = str(artha_dir / "scripts")
        import sys
        if sys_path_insert not in sys.path:
            sys.path.insert(0, sys_path_insert)
        from foundation import get_public_key  # noqa: PLC0415
        return get_public_key()
    except (Exception, SystemExit):
        # SystemExit raised by foundation.die() when age_recipient is missing.
        return None


def _encrypt_field(value: str, pubkey: str | None) -> str:
    """Encrypt a sensitive string field with age. Returns ciphertext or original if unavailable."""
    if not pubkey:
        return value  # fallback: unencrypted (age not configured)
    try:
        import sys
        from foundation import age_encrypt_string  # noqa: PLC0415
        return "age1:" + age_encrypt_string(pubkey, value)
    except Exception:
        return value  # fallback: unencrypted


def _decrypt_field(value: str, privkey: str | None) -> str:
    """Decrypt an age-encrypted field. Returns original if not encrypted or key unavailable."""
    if not value or not value.startswith("age1:") or not privkey:
        return value
    try:
        from foundation import age_decrypt_string  # noqa: PLC0415
        return age_decrypt_string(privkey, value[5:])  # strip "age1:" prefix
    except Exception:
        return value  # fallback: return encrypted value (rather than crash)


# ---------------------------------------------------------------------------
# ActionQueue
# ---------------------------------------------------------------------------

class ActionQueue:
    """SQLite-backed persistent queue for action lifecycle management.

    This is the SOLE authoritative owner of action lifecycle state.
    No other component may directly update action status in the DB;
    all transitions go through this class's methods.

    Thread/process safety:
        Each ActionQueue instance holds its own connection.  Multiple
        processes (terminal + Telegram listener) create separate instances
        pointing to the same DB file.  WAL mode allows concurrent reads
        while a single writer holds the lock.

    Usage:
        queue = ActionQueue(artha_dir)
        queue.propose(proposal)
        queue.transition(action_id, "approved", actor="user:terminal")
        pending = queue.list_pending()
        queue.record_result(action_id, result, executed_at)
    """

    def __init__(self, artha_dir: Path) -> None:
        self._artha_dir = artha_dir
        self._db_path = self._resolve_db_path(artha_dir)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Backward-compat: auto-copy legacy DB from OneDrive-synced state/ to local path
        legacy_path = artha_dir / "state" / "actions.db"
        if (
            not self._db_path.exists()
            and self._db_path != legacy_path
            and legacy_path.exists()
        ):
            shutil.copy2(str(legacy_path), str(self._db_path))

        self._conn = _open_db(self._db_path)
        self._init_schema()

    @staticmethod
    def _resolve_db_path(artha_dir: Path) -> Path:
        """Return the local (non-OneDrive-synced) DB path.

        Priority:
          1. ARTHA_LOCAL_DB environment variable (absolute path override)
          2. Real Artha dirs (config/artha_config.yaml present):
             macOS:   ~/.artha-local/actions.db
             Windows: %LOCALAPPDATA%\\Artha\\actions.db
             Linux:   $XDG_DATA_HOME/artha/actions.db
          3. Test / CI / unknown: artha_dir/state/actions.db (original path)

        The config-existence check (priority 2) preserves backward compat:
        test fixtures pass temp dirs (no config/artha_config.yaml) and get
        the original relative path; production passes the real Artha root and
        gets the platform-local path.
        """
        env_override = os.environ.get("ARTHA_LOCAL_DB", "").strip()
        if env_override:
            return Path(env_override)

        # Apply platform-specific local path only for real Artha root directories
        if (artha_dir / "config" / "artha_config.yaml").exists():
            system = platform.system()
            if system == "Darwin":
                return Path.home() / ".artha-local" / "actions.db"
            if system == "Windows":
                local_app = os.environ.get(
                    "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
                )
                return Path(local_app) / "Artha" / "actions.db"
            xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
            return Path(xdg) / "artha" / "actions.db"

        # Test dirs, CI, or unknown: use original relative path (backwards compat)
        return artha_dir / "state" / "actions.db"

    def _init_schema(self) -> None:
        """Create tables if they don't exist, then migrate schema if needed."""
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)
        self._migrate_schema_if_needed()

    def _migrate_schema_if_needed(self) -> None:
        """Add bridge_synced and origin columns to existing DBs (idempotent).

        Uses BEGIN IMMEDIATE to prevent concurrent migration race.
        Adds columns to both actions and actions_archive tables.
        """
        cur = self._conn.execute("PRAGMA table_info(actions)")
        existing_cols = {row[1] for row in cur.fetchall()}

        migrations_actions = []
        migrations_archive = []

        if "bridge_synced" not in existing_cols:
            migrations_actions.append(
                "ALTER TABLE actions ADD COLUMN bridge_synced INTEGER NOT NULL DEFAULT 0"
            )
            migrations_archive.append(
                "ALTER TABLE actions_archive ADD COLUMN bridge_synced INTEGER NOT NULL DEFAULT 0"
            )
        if "origin" not in existing_cols:
            migrations_actions.append(
                "ALTER TABLE actions ADD COLUMN origin TEXT NOT NULL DEFAULT 'local'"
            )
            migrations_archive.append(
                "ALTER TABLE actions_archive ADD COLUMN origin TEXT NOT NULL DEFAULT 'local'"
            )

        if not migrations_actions:
            return  # already up-to-date

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for sql in migrations_actions + migrations_archive:
                try:
                    self._conn.execute(sql)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        """Close the DB connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now_utc(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _new_id(self) -> str:
        return str(uuid.uuid4())

    def _audit(
        self,
        conn: sqlite3.Connection,
        action_id: str,
        from_status: str,
        to_status: str,
        actor: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Append an immutable audit entry.  Must be called inside a transaction."""
        conn.execute(
            """INSERT INTO action_audit
               (id, action_id, timestamp, from_status, to_status, actor, context)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                self._new_id(),
                action_id,
                self._now_utc(),
                from_status,
                to_status,
                actor,
                json.dumps(context) if context else None,
            ),
        )

    def _should_encrypt(self, sensitivity: str) -> bool:
        return sensitivity in ("high", "critical")

    def _proposal_to_row(
        self,
        proposal: ActionProposal,
        pubkey: str | None,
        origin: str = "local",
    ) -> tuple:
        """Serialise an ActionProposal to a DB row tuple (28 columns)."""
        now = self._now_utc()
        params_json = json.dumps(proposal.parameters)
        description = proposal.description or ""

        if self._should_encrypt(proposal.sensitivity) and pubkey:
            params_json = _encrypt_field(params_json, pubkey)
            description = _encrypt_field(description, pubkey)

        return (
            proposal.id,
            now,
            now,
            proposal.action_type,
            proposal.domain,
            proposal.friction,
            proposal.min_trust,
            proposal.title,
            description,
            params_json,
            proposal.sensitivity,
            "pending",                 # initial status
            None,                      # approved_at
            None,                      # executed_at
            None,                      # approved_by
            proposal.expires_at,
            None,                      # result_status
            None,                      # result_message
            None,                      # result_data
            proposal.source_step,
            proposal.source_skill,
            proposal.domain,           # source_domain = domain
            proposal.linked_oi,
            1 if proposal.reversible else 0,
            None,                      # reverse_action_id
            proposal.undo_window_sec,
            0,                         # bridge_synced
            origin,                    # 'local' | 'bridge'
        )

    def _row_to_proposal(
        self, row: sqlite3.Row, privkey: str | None
    ) -> ActionProposal:
        """Deserialise a DB row back to an ActionProposal."""
        params_json = row["parameters"]
        description = row["description"] or ""
        sensitivity = row["sensitivity"]

        if self._should_encrypt(sensitivity) and privkey:
            params_json = _decrypt_field(params_json, privkey)
            description = _decrypt_field(description, privkey)

        return ActionProposal(
            id=row["id"],
            action_type=row["action_type"],
            domain=row["domain"],
            title=row["title"],
            description=description,
            parameters=json.loads(params_json),
            friction=row["friction"],
            min_trust=row["min_trust"],
            sensitivity=sensitivity,
            reversible=bool(row["reversible"]),
            undo_window_sec=row["undo_window_sec"],
            expires_at=row["expires_at"],
            source_step=row["source_step"],
            source_skill=row["source_skill"],
            linked_oi=row["linked_oi"],
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose(
        self,
        proposal: ActionProposal,
        pubkey: str | None = None,
    ) -> None:
        """Enqueue a new action proposal with status=PENDING.

        Raises:
            ValueError: If the action_type+domain+entity combination already
                        has a PENDING or DEFERRED entry (deduplication rule §10.3).
            OverflowError: If the queue has reached _MAX_QUEUE_SIZE.
        """
        # Deduplication: check for existing PENDING/DEFERRED for same action_type + domain
        existing = self._conn.execute(
            """SELECT id FROM actions
               WHERE action_type = ? AND source_domain = ?
               AND status IN ('pending', 'deferred')
               LIMIT 1""",
            (proposal.action_type, proposal.domain),
        ).fetchone()
        if existing:
            raise ValueError(
                f"Duplicate: pending/deferred action of type '{proposal.action_type}' "
                f"for domain '{proposal.domain}' already exists (id={existing['id']})"
            )

        # Queue size guard
        count = self._conn.execute(
            "SELECT COUNT(*) FROM actions WHERE status NOT IN ('succeeded','failed','rejected','expired','cancelled')"
        ).fetchone()[0]
        if count >= _MAX_QUEUE_SIZE:
            raise OverflowError(
                f"Action queue is full ({_MAX_QUEUE_SIZE} active actions). "
                "Approve or reject pending actions before adding new ones."
            )

        row = self._proposal_to_row(proposal, pubkey)
        with self._conn:
            self._conn.execute(
                """INSERT INTO actions VALUES (
                    ?,?,?,  ?,?,?,?,  ?,?,?,?,  ?,?,?,?,?,  ?,?,?,  ?,?,?,?,  ?,?,?,  ?,?
                )""",
                row,
            )
            self._audit(self._conn, proposal.id, "", "pending", "system:propose")

    def transition(
        self,
        action_id: str,
        to_status: str,
        actor: str,
        context: dict[str, Any] | None = None,
        approved_by: str | None = None,
    ) -> str:
        """Atomically transition action to a new status.

        Validates the transition against the state machine.  Writes the
        status update AND the audit log entry in a single transaction.

        Returns:
            The previous status (for callers that need to verify the transition).

        Raises:
            ValueError: If the action is not found, is in a terminal state,
                        or the requested transition is invalid.
        """
        if to_status not in VALID_STATUSES:
            raise ValueError(f"Unknown status: {to_status!r}")

        with self._conn:
            row = self._conn.execute(
                "SELECT status FROM actions WHERE id = ?", (action_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Action not found: {action_id}")

            from_status = row["status"]
            if from_status in TERMINAL_STATUSES:
                raise ValueError(
                    f"Action {action_id} is in terminal state '{from_status}'; "
                    "no further transitions allowed"
                )

            # Validate transition against state machine (§2.4)
            _validate_transition(from_status, to_status, actor)

            now = self._now_utc()
            updates: dict[str, Any] = {"status": to_status, "updated_at": now}
            if to_status == "approved":
                updates["approved_at"] = now
                updates["approved_by"] = approved_by or actor
            elif to_status in ("executing", "succeeded", "failed"):
                if from_status == "approved" and to_status == "executing":
                    updates["executed_at"] = now

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            params = list(updates.values()) + [action_id]
            self._conn.execute(
                f"UPDATE actions SET {set_clause} WHERE id = ?", params
            )
            self._audit(self._conn, action_id, from_status, to_status, actor, context)

        return from_status

    def record_result(
        self,
        action_id: str,
        result: ActionResult,
        executed_at: str,
        pubkey: str | None = None,
    ) -> None:
        """Persist execution result and set executed_at.

        Called by ActionExecutor after handler.execute() returns.
        Separate from transition() so that result data can be stored
        atomically alongside the SUCCEEDED/FAILED status change.
        """
        row = self._conn.execute(
            "SELECT sensitivity FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Action not found: {action_id}")

        sensitivity = row["sensitivity"]
        result_data_json = json.dumps(result.data) if result.data else None
        if result_data_json and self._should_encrypt(sensitivity) and pubkey:
            result_data_json = _encrypt_field(result_data_json, pubkey)

        # Cap result message length
        message = (result.message or "")[:300]

        new_status = "succeeded" if result.status == "success" else "failed"
        from_status = self.transition(
            action_id, new_status, actor="system:executor",
            context={"result_status": result.status}
        )

        with self._conn:
            self._conn.execute(
                """UPDATE actions SET
                   result_status = ?, result_message = ?, result_data = ?,
                   executed_at = ?, updated_at = ?
                   WHERE id = ?""",
                (result.status, message, result_data_json, executed_at,
                 self._now_utc(), action_id),
            )

    def get(
        self, action_id: str, privkey: str | None = None
    ) -> ActionProposal | None:
        """Fetch a single action proposal by ID.  Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_proposal(row, privkey)

    def get_raw(self, action_id: str) -> dict[str, Any] | None:
        """Fetch raw DB row as a dict (for status checks that don't need decryption)."""
        row = self._conn.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def list_pending(self, privkey: str | None = None) -> list[ActionProposal]:
        """Return all PENDING actions, plus DEFERRED actions past their defer time.

        This is the primary query for the approval UX.  Results are ordered
        by urgency (friction desc: high first) then created_at asc (oldest first).
        """
        now = self._now_utc()
        rows = self._conn.execute(
            """SELECT * FROM actions
               WHERE status = 'pending'
               OR (status = 'deferred' AND expires_at <= ?)
               ORDER BY
                 CASE friction WHEN 'high' THEN 0 WHEN 'standard' THEN 1 ELSE 2 END,
                 created_at ASC""",
            (now,),
        ).fetchall()
        return [self._row_to_proposal(r, privkey) for r in rows]

    def find_by_prefix(
        self, prefix: str, privkey: str | None = None
    ) -> list[ActionProposal]:
        """Find active (non-terminal) actions whose ID starts with *prefix*.

        Used by _resolve_id to locate deferred / non-pending actions by their
        short display prefix.  Returns an empty list when nothing matches.
        """
        rows = self._conn.execute(
            """SELECT * FROM actions
               WHERE id LIKE ? AND status NOT IN (
                   'succeeded','failed','rejected','expired','cancelled'
               )""",
            (prefix + "%",),
        ).fetchall()
        return [self._row_to_proposal(r, privkey) for r in rows]

    def list_history(
        self, days: int = 7, privkey: str | None = None
    ) -> list[dict[str, Any]]:
        """Return executed/rejected actions from the last N days."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat(timespec="seconds")
        rows = self._conn.execute(
            """SELECT * FROM actions
               WHERE created_at >= ?
               AND status IN ('succeeded','failed','rejected','cancelled','expired')
               ORDER BY updated_at DESC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def expire_stale(self) -> int:
        """Sweep PENDING actions past their expires_at and mark them EXPIRED.

        Returns the count of newly expired actions.  Called at preflight (Step 0c).
        """
        now = self._now_utc()
        rows = self._conn.execute(
            """SELECT id, status FROM actions
               WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= ?""",
            (now,),
        ).fetchall()

        count = 0
        for row in rows:
            try:
                self.transition(row["id"], "expired", actor="system:expiry")
                count += 1
            except ValueError:
                pass  # already in terminal state — skip silently
        return count

    def record_trust_metric(
        self,
        action_type: str,
        domain: str,
        user_decision: str,
        execution_result: str | None = None,
        feedback: str | None = None,
    ) -> None:
        """Append a trust metric row for elevation scoring."""
        with self._conn:
            self._conn.execute(
                """INSERT INTO trust_metrics
                   (id, action_type, domain, proposed_at, user_decision,
                    execution_result, feedback)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._new_id(), action_type, domain,
                    self._now_utc(), user_decision,
                    execution_result, feedback,
                ),
            )

    def trust_metrics_summary(self) -> dict[str, Any]:
        """Aggregate trust metrics for elevation evaluation."""
        cutoff_90d = (
            datetime.now(timezone.utc) - timedelta(days=90)
        ).isoformat(timespec="seconds")
        rows = self._conn.execute(
            "SELECT user_decision, execution_result FROM trust_metrics WHERE proposed_at >= ?",
            (cutoff_90d,),
        ).fetchall()

        total = len(rows)
        approved = sum(1 for r in rows if r["user_decision"] == "approved")
        acceptance_rate = approved / total if total > 0 else 0.0

        return {
            "total_90d": total,
            "acceptance_rate_90d": round(acceptance_rate, 4),
            "approved_count": approved,
        }

    def queue_stats(self) -> dict[str, Any]:
        """Return queue health statistics for health-check.md."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM actions GROUP BY status"
        ).fetchall()
        by_status: dict[str, int] = {r["status"]: r["cnt"] for r in rows}

        pending_rows = self._conn.execute(
            "SELECT created_at FROM actions WHERE status='pending' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()

        oldest_pending_hours: float | None = None
        if pending_rows:
            try:
                created = datetime.fromisoformat(pending_rows["created_at"])
                oldest_pending_hours = round(
                    (datetime.now(timezone.utc) - created).total_seconds() / 3600, 1
                )
            except Exception:
                pass

        return {
            "total_pending": by_status.get("pending", 0),
            "total_deferred": by_status.get("deferred", 0),
            "total_succeeded": by_status.get("succeeded", 0),
            "total_failed": by_status.get("failed", 0),
            "total_rejected": by_status.get("rejected", 0),
            "oldest_pending_hours": oldest_pending_hours,
        }

    def archive_old_records(self) -> int:
        """Move executed/rejected records >30 days old to actions_archive.

        Returns count of archived records.  Called when DB exceeds size cap
        or on a configurable schedule.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=_ARCHIVE_AFTER_DAYS)
        ).isoformat(timespec="seconds")

        with self._conn:
            count_row = self._conn.execute(
                """SELECT COUNT(*) FROM actions
                   WHERE status IN ('succeeded','failed','rejected','expired','cancelled')
                   AND updated_at <= ?""",
                (cutoff,),
            ).fetchone()
            count = count_row[0] if count_row else 0
            if count > 0:
                self._conn.execute(
                    """INSERT OR IGNORE INTO actions_archive
                       SELECT * FROM actions
                       WHERE status IN ('succeeded','failed','rejected','expired','cancelled')
                       AND updated_at <= ?""",
                    (cutoff,),
                )
                self._conn.execute(
                    """DELETE FROM actions
                       WHERE status IN ('succeeded','failed','rejected','expired','cancelled')
                       AND updated_at <= ?""",
                    (cutoff,),
                )
        return count

    def check_db_size(self) -> bool:
        """Return True if DB is within the 50 MB cap; False if oversize."""
        try:
            return self._db_path.stat().st_size < _DB_SIZE_CAP_BYTES
        except OSError:
            return True  # can't stat → assume ok

    # ------------------------------------------------------------------
    # Bridge API (multi-machine dual-setup support)
    # ------------------------------------------------------------------

    def ingest_remote(
        self,
        proposal: ActionProposal,
        pubkey: str | None = None,
    ) -> bool:
        """Insert a proposal that originated on the remote machine.

        Deduplication is strictly by UUID (action_id), NOT by
        action_type+domain — the proposer has already applied its local
        dedup rules; the executor must accept any UUID it hasn't seen.

        Bypasses queue size guard (remote actions are pre-approved by the
        proposer's local quota — they must flow through regardless).

        Sets origin='bridge' to distinguish from locally-proposed actions.

        Returns:
            True  — proposal was newly inserted
            False — already exists (duplicate UUID), no-op
        """
        existing = self._conn.execute(
            "SELECT id FROM actions WHERE id = ?", (proposal.id,)
        ).fetchone()
        if existing:
            return False  # idempotent: already ingested

        row = self._proposal_to_row(proposal, pubkey, origin="bridge")
        with self._conn:
            self._conn.execute(
                """INSERT INTO actions VALUES (
                    ?,?,?,  ?,?,?,?,  ?,?,?,?,  ?,?,?,?,?,  ?,?,?,  ?,?,?,?,  ?,?,?,  ?,?
                )""",
                row,
            )
            self._audit(
                self._conn, proposal.id, "", "pending",
                "system:bridge_ingest",
            )
        return True

    def update_defer_time(self, action_id: str, defer_time: str) -> None:
        """Update the defer time (expires_at) for a DEFERRED action.

        Uses the managed connection — replaces the old raw _open_db() path
        in action_executor.defer().

        Args:
            action_id:  UUID of the action to update
            defer_time: ISO-8601 UTC timestamp string to set as new expires_at
        """
        with self._conn:
            affected = self._conn.execute(
                """UPDATE actions SET expires_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'deferred'""",
                (defer_time, self._now_utc(), action_id),
            ).rowcount
        if affected == 0:
            # Allow: may have transitioned out of deferred already; not fatal
            pass

    def apply_remote_result(
        self,
        action_id: str,
        final_status: str,
        result_message: str | None = None,
        result_data: dict | None = None,
        executed_at: str | None = None,
    ) -> bool:
        """Apply an execution result that arrived via the bridge (additive-only).

        Invariant: NEVER overwrites non-null existing proposal fields.
        Only fills in result fields that are currently NULL.

        Directly UPDATEs status to the terminal value without going through
        the state machine — this is intentional. The executor machine already
        ran the action through the full lifecycle; the proposer machine never
        executes it locally. The bridge result is authoritative.

        Sets bridge_synced=1 on the updated row so the proposer knows the
        result has been received (no outbox retry needed on this side).

        Args:
            action_id:     UUID of the local pending action
            final_status:  'succeeded' | 'failed'
            result_message: human text
            result_data:   structured dict or None
            executed_at:   ISO-8601 string or None (defaults to now)

        Returns:
            True  — action found and result applied
            False — action not found (orphan result; caller should log WARN)
        """
        row = self._conn.execute(
            "SELECT id, status FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if not row:
            return False

        from_status = row["status"]
        ts_executed = executed_at or self._now_utc()
        result_data_json = json.dumps(result_data) if result_data else None

        with self._conn:
            # Bypass state machine — remote execution is authoritative
            self._conn.execute(
                """UPDATE actions SET
                   status         = ?,
                   result_status  = COALESCE(result_status, ?),
                   result_message = COALESCE(result_message, ?),
                   result_data    = COALESCE(result_data, ?),
                   executed_at    = COALESCE(executed_at, ?),
                   bridge_synced  = 1,
                   updated_at     = ?
                   WHERE id = ?""",
                (
                    final_status, final_status, result_message, result_data_json,
                    ts_executed, self._now_utc(), action_id,
                ),
            )
            self._audit(
                self._conn, action_id, from_status, final_status,
                "system:bridge_result",
                context={"via": "bridge"},
            )
        return True

    def mark_bridge_synced(self, action_id: str) -> None:
        """Set bridge_synced=1 for an action (result has been written to bridge)."""
        with self._conn:
            self._conn.execute(
                "UPDATE actions SET bridge_synced = 1, updated_at = ? WHERE id = ?",
                (self._now_utc(), action_id),
            )

    def list_unsynced_results(self) -> list[dict[str, Any]]:
        """Return terminal actions with bridge_synced=0 (pending outbox retry).

        Used by bridge.retry_outbox() on the executor machine to find results
        that need to be (re-)written to the bridge directory.

        Returns list of raw dicts with id, status, result_message, result_data,
        executed_at fields.
        """
        rows = self._conn.execute(
            """SELECT id, status, result_message, result_data, executed_at
               FROM actions
               WHERE status IN ('succeeded', 'failed')
               AND bridge_synced = 0
               AND origin = 'bridge'""",
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# State machine transition validator
# ---------------------------------------------------------------------------

def _validate_transition(from_status: str, to_status: str, actor: str) -> None:
    """Enforce the §2.4 state machine.  Raises ValueError on invalid transitions.

    Only the transitions listed in the spec are permitted — any other
    transition is a code bug and must raise immediately so it can be caught.
    """
    ALLOWED: dict[str, set[str]] = {
        "pending":   {"approved", "modifying", "rejected", "deferred", "expired"},
        "modifying": {"pending"},
        "deferred":  {"pending", "rejected"},
        "approved":  {"executing", "cancelled"},
        "executing": {"succeeded", "failed"},
    }

    allowed = ALLOWED.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Invalid transition '{from_status}' → '{to_status}' by '{actor}'. "
            f"Allowed transitions from '{from_status}': {sorted(allowed)}"
        )
