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

Ref: specs/action.md
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import sys
import uuid
import hashlib
import re
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
# M15: M365 write action types — all permanently L1 (confirm-before-execute)
# autonomy_cap L1_permanent means user confirmation is ALWAYS required;
# trust counters never unlock auto-execution (FR-19, G1, F11).
# ---------------------------------------------------------------------------
M15_ACTION_TYPES = [
    {"action_type": "m365_flag",        "autonomy_cap": "L1_permanent"},
    {"action_type": "m365_reply",       "autonomy_cap": "L1_permanent"},
    {"action_type": "m365_decline",     "autonomy_cap": "L1_permanent"},
    {"action_type": "m365_accept",      "autonomy_cap": "L1_permanent"},
    {"action_type": "m365_teams_reply", "autonomy_cap": "L1_permanent"},
]

_ACTION_TYPE_WINDOW_BUCKET: dict[str, str] = {
    "calendar_create": "scheduling",
    "calendar_modify": "scheduling",
    "email_send": "communication",
    "email_reply": "communication",
    "whatsapp_send": "communication",
    "slack_send": "communication",
    "instruction_sheet": "instruction_sheet",
}

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_action_text(value: Any) -> str:
    """Normalize text for deduplication and audit-safe comparisons."""
    text = str(value or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.strip().lower()
    return _WHITESPACE_RE.sub(" ", text)


def _coerce_iso_minute(value: Any) -> str:
    """Return UTC ISO minute precision for a datetime-like value."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        if "T" not in raw and len(raw) >= 10:
            return raw[:10]
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return ""
    else:
        return ""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _derive_target_window_iso(proposal: ActionProposal) -> str:
    """Return the canonical target window string used in idempotency keys."""
    params = proposal.parameters or {}
    if isinstance(params.get("target_window_iso"), str) and params["target_window_iso"].strip():
        return params["target_window_iso"].strip()

    date_only = (
        params.get("date")
        or params.get("due_date")
        or params.get("day")
    )
    if isinstance(date_only, str) and date_only.strip() and "T" not in date_only:
        return date_only.strip()[:10]

    start = (
        params.get("start_at")
        or params.get("starts_at")
        or params.get("start")
        or params.get("due_at")
        or params.get("datetime")
    )
    end = params.get("end_at") or params.get("ends_at") or params.get("end")
    start_iso = _coerce_iso_minute(start)
    end_iso = _coerce_iso_minute(end)

    if start_iso and end_iso and "T" in start_iso and "T" in end_iso:
        return f"{start_iso}/{end_iso}"
    if start_iso:
        return start_iso
    return "none"


def _derive_target_resource_id(proposal: ActionProposal) -> str:
    """Return the stable target resource identifier used in idempotency keys."""
    params = proposal.parameters or {}
    for key in (
        "target_resource_id",
        "event_id",
        "calendar_id",
        "thread_id",
        "message_id",
        "conversation_id",
        "task_id",
        "list_id",
        "channel",
        "recipient",
        "to",
        "phone_number",
        "payee",
        "service",
    ):
        value = params.get(key)
        if value:
            return f"{_normalize_action_text(proposal.domain)}:{_normalize_action_text(value)}"
    return _normalize_action_text(proposal.domain)


def _derive_normalized_entity(proposal: ActionProposal) -> str:
    """Return the canonical entity string used for routing and idempotency."""
    if proposal.normalized_entity:
        return _normalize_action_text(proposal.normalized_entity)
    params = proposal.parameters or {}
    for key in (
        "normalized_entity",
        "entity",
        "recipient",
        "to",
        "phone_number",
        "payee",
        "service",
        "subject",
        "title",
    ):
        value = params.get(key)
        if value:
            return _normalize_action_text(value)
    return _normalize_action_text(proposal.domain)


def _derive_normalized_summary(proposal: ActionProposal) -> str:
    """Return the canonical summary string used for deduplication."""
    if proposal.normalized_summary:
        return _normalize_action_text(proposal.normalized_summary)
    params = proposal.parameters or {}
    for key in ("normalized_summary", "summary", "subject", "title"):
        value = params.get(key)
        if value:
            return _normalize_action_text(value)
    return _normalize_action_text(proposal.title)


def _idempotency_window_for(proposal: ActionProposal) -> timedelta:
    """Return the configured idempotency window for a proposal."""
    try:
        from lib.idempotency import get_window  # noqa: PLC0415

        bucket = _ACTION_TYPE_WINDOW_BUCKET.get(proposal.action_type, proposal.action_type)
        return get_window(bucket, proposal.domain)
    except Exception:
        return timedelta(hours=24)


def _compute_idempotency_fields(
    proposal: ActionProposal,
    now: datetime | None = None,
) -> tuple[str, str, str, str, str]:
    """Return normalized entity, summary, key, window iso, expiry timestamp."""
    ts = now or datetime.now(timezone.utc)
    normalized_entity = _derive_normalized_entity(proposal)
    normalized_summary = _derive_normalized_summary(proposal)
    target_window_iso = _derive_target_window_iso(proposal)
    target_resource_id = _derive_target_resource_id(proposal)
    material = "||".join(
        [
            proposal.action_type,
            normalized_entity,
            normalized_summary,
            target_window_iso,
            target_resource_id,
        ]
    )
    key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    expires_at = proposal.idempotency_expires_at
    if not expires_at:
        expires_at = (ts + _idempotency_window_for(proposal)).isoformat(timespec="seconds")
    return normalized_entity, normalized_summary, key, target_window_iso, expires_at


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
    signal_subtype    TEXT,
    confidence        REAL NOT NULL DEFAULT 0.0,
    normalized_entity TEXT,
    normalized_summary TEXT,
    idempotency_key   TEXT,
    idempotency_expires_at TEXT,
    preview_required  INTEGER NOT NULL DEFAULT 0,
    preview_shown_at  TEXT,
    preview_shown_by  TEXT,
    last_notified_at  TEXT,
    last_notified_channel TEXT,

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
    feedback         TEXT,
    signal_subtype   TEXT,
    proposal_confidence REAL,
    source_origin    TEXT,
    rejection_category TEXT,
    normalized_entity TEXT
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
    signal_subtype    TEXT,
    confidence        REAL NOT NULL DEFAULT 0.0,
    normalized_entity TEXT,
    normalized_summary TEXT,
    idempotency_key   TEXT,
    idempotency_expires_at TEXT,
    preview_required  INTEGER NOT NULL DEFAULT 0,
    preview_shown_at  TEXT,
    preview_shown_by  TEXT,
    last_notified_at  TEXT,
    last_notified_channel TEXT,
    reversible        INTEGER NOT NULL DEFAULT 0,
    reverse_action_id TEXT,
    undo_window_sec   INTEGER,

    bridge_synced     INTEGER NOT NULL DEFAULT 0,
    origin            TEXT NOT NULL DEFAULT 'local'
);

CREATE TABLE IF NOT EXISTS trust_state (
    singleton_key            INTEGER PRIMARY KEY CHECK (singleton_key = 1),
    trust_level              INTEGER NOT NULL DEFAULT 0,
    entered_level_at         TEXT NOT NULL,
    days_at_level            INTEGER NOT NULL DEFAULT 0,
    acceptance_rate_90d      REAL NOT NULL DEFAULT 0.0,
    critical_false_positives INTEGER NOT NULL DEFAULT 0,
    degraded_mode            INTEGER NOT NULL DEFAULT 0,
    degraded_reason          TEXT,
    last_demotion            TEXT,
    last_elevation           TEXT,
    updated_at               TEXT NOT NULL,
    updated_by               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trust_preapproved_categories (
    action_type  TEXT PRIMARY KEY,
    l2_enabled   INTEGER NOT NULL DEFAULT 0,
    enabled_at   TEXT,
    disabled_at  TEXT,
    updated_at   TEXT NOT NULL,
    updated_by   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_trust_mutations (
    id            TEXT PRIMARY KEY,
    timestamp     TEXT NOT NULL,
    actor         TEXT NOT NULL,
    mutation_type TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    justification TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS suppressed_proposals (
    id                 TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    signal_subtype     TEXT,
    domain             TEXT,
    action_type        TEXT,
    reason             TEXT NOT NULL,
    normalized_entity  TEXT,
    normalized_summary TEXT,
    shadow_title       TEXT,
    shadow_description TEXT
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
        primary_path = self._resolve_db_path(artha_dir)
        db_candidates = [primary_path]

        last_error: Exception | None = None
        for candidate in db_candidates:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                legacy_path = artha_dir / "state" / "actions.db"
                if (
                    candidate != legacy_path
                    and not candidate.exists()
                    and legacy_path.exists()
                ):
                    shutil.copy2(str(legacy_path), str(candidate))
                self._db_path = candidate
                self._conn = _open_db(self._db_path)
                break
            except (OSError, sqlite3.OperationalError) as exc:
                last_error = exc
                continue
        else:
            raise sqlite3.OperationalError(
                f"unable to open database file ({last_error})"
            )
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
        if artha_dir.suffix == ".db":
            return artha_dir

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
        """Apply additive schema migrations idempotently."""
        table_columns = {
            "actions": {
                "bridge_synced": "INTEGER NOT NULL DEFAULT 0",
                "origin": "TEXT NOT NULL DEFAULT 'local'",
                "signal_subtype": "TEXT",
                "confidence": "REAL NOT NULL DEFAULT 0.0",
                "normalized_entity": "TEXT",
                "normalized_summary": "TEXT",
                "idempotency_key": "TEXT",
                "idempotency_expires_at": "TEXT",
                "preview_required": "INTEGER NOT NULL DEFAULT 0",
                "preview_shown_at": "TEXT",
                "preview_shown_by": "TEXT",
                "last_notified_at": "TEXT",
                "last_notified_channel": "TEXT",
            },
            "actions_archive": {
                "bridge_synced": "INTEGER NOT NULL DEFAULT 0",
                "origin": "TEXT NOT NULL DEFAULT 'local'",
                "signal_subtype": "TEXT",
                "confidence": "REAL NOT NULL DEFAULT 0.0",
                "normalized_entity": "TEXT",
                "normalized_summary": "TEXT",
                "idempotency_key": "TEXT",
                "idempotency_expires_at": "TEXT",
                "preview_required": "INTEGER NOT NULL DEFAULT 0",
                "preview_shown_at": "TEXT",
                "preview_shown_by": "TEXT",
                "last_notified_at": "TEXT",
                "last_notified_channel": "TEXT",
            },
            "trust_metrics": {
                "signal_subtype": "TEXT",
                "proposal_confidence": "REAL",
                "source_origin": "TEXT",
                "rejection_category": "TEXT",
                "normalized_entity": "TEXT",
            },
        }

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for table, columns in table_columns.items():
                existing_cols = {
                    row[1]
                    for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for column, col_type in columns.items():
                    if column in existing_cols:
                        continue
                    try:
                        self._conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                        )
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" not in str(exc).lower():
                            raise

            # C4: rename trust_state columns if old names exist (SQLite 3.25+)
            ts_cols = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(trust_state)").fetchall()
            }
            if "id" in ts_cols and "singleton_key" not in ts_cols:
                self._conn.execute(
                    "ALTER TABLE trust_state RENAME COLUMN id TO singleton_key"
                )
            if "trust_level_since" in ts_cols and "entered_level_at" not in ts_cols:
                self._conn.execute(
                    "ALTER TABLE trust_state RENAME COLUMN trust_level_since TO entered_level_at"
                )

            # C5: add new columns to trust_preapproved_categories if missing
            tpc_cols = {
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(trust_preapproved_categories)"
                ).fetchall()
            }
            if "enabled_at" not in tpc_cols:
                self._conn.execute(
                    "ALTER TABLE trust_preapproved_categories ADD COLUMN enabled_at TEXT"
                )
            if "disabled_at" not in tpc_cols:
                self._conn.execute(
                    "ALTER TABLE trust_preapproved_categories ADD COLUMN disabled_at TEXT"
                )

            # C6: rename audit_trust_mutations.reason → justification if old name exists
            atm_cols = {
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(audit_trust_mutations)"
                ).fetchall()
            }
            if "reason" in atm_cols and "justification" not in atm_cols:
                self._conn.execute(
                    "ALTER TABLE audit_trust_mutations RENAME COLUMN reason TO justification"
                )

            self._conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_actions_signal_subtype ON actions(signal_subtype, domain);
                CREATE INDEX IF NOT EXISTS idx_actions_idempotency_key ON actions(idempotency_key);
                CREATE INDEX IF NOT EXISTS idx_actions_idempotency_live ON actions(idempotency_key, idempotency_expires_at, status);
                CREATE INDEX IF NOT EXISTS idx_actions_preview_required ON actions(preview_required, status);
                CREATE TABLE IF NOT EXISTS trust_state (
                    singleton_key            INTEGER PRIMARY KEY CHECK (singleton_key = 1),
                    trust_level              INTEGER NOT NULL DEFAULT 0,
                    entered_level_at         TEXT NOT NULL,
                    days_at_level            INTEGER NOT NULL DEFAULT 0,
                    acceptance_rate_90d      REAL NOT NULL DEFAULT 0.0,
                    critical_false_positives INTEGER NOT NULL DEFAULT 0,
                    degraded_mode            INTEGER NOT NULL DEFAULT 0,
                    degraded_reason          TEXT,
                    last_demotion            TEXT,
                    last_elevation           TEXT,
                    updated_at               TEXT NOT NULL,
                    updated_by               TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trust_preapproved_categories (
                    action_type TEXT PRIMARY KEY,
                    l2_enabled  INTEGER NOT NULL DEFAULT 0,
                    enabled_at  TEXT,
                    disabled_at TEXT,
                    updated_at  TEXT NOT NULL,
                    updated_by  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trust_preapproved_enabled
                    ON trust_preapproved_categories(l2_enabled, action_type);
                CREATE TABLE IF NOT EXISTS audit_trust_mutations (
                    id            TEXT PRIMARY KEY,
                    timestamp     TEXT NOT NULL,
                    actor         TEXT NOT NULL,
                    mutation_type TEXT NOT NULL,
                    old_value     TEXT,
                    new_value     TEXT,
                    justification TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS suppressed_proposals (
                    id                 TEXT PRIMARY KEY,
                    created_at         TEXT NOT NULL,
                    signal_subtype     TEXT,
                    domain             TEXT,
                    action_type        TEXT,
                    reason             TEXT NOT NULL,
                    normalized_entity  TEXT,
                    normalized_summary TEXT,
                    shadow_title       TEXT,
                    shadow_description TEXT
                );
                """
            )

            now = self._now_utc()
            self._conn.execute(
                """
                INSERT OR IGNORE INTO trust_state
                    (singleton_key, trust_level, entered_level_at, days_at_level,
                     acceptance_rate_90d, critical_false_positives,
                     degraded_mode, degraded_reason, last_demotion,
                     last_elevation, updated_at, updated_by)
                VALUES (1, 0, ?, 0, 0.0, 0, 0, NULL, NULL, NULL, ?, 'system:init')
                """,
                (datetime.now(timezone.utc).date().isoformat(), now),
            )
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
        """Serialise an ActionProposal to a DB row tuple."""
        now = self._now_utc()
        params_json = json.dumps(proposal.parameters)
        description = proposal.description or ""
        normalized_entity, normalized_summary, idempotency_key, _, idem_expires_at = (
            _compute_idempotency_fields(proposal)
        )
        signal_subtype = proposal.signal_subtype or ""
        confidence = float(proposal.confidence or 0.0)

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
            signal_subtype,
            confidence,
            normalized_entity,
            normalized_summary,
            proposal.idempotency_key or idempotency_key,
            idem_expires_at,
            1 if proposal.preview_required else 0,
            proposal.preview_shown_at,
            proposal.preview_shown_by,
            proposal.last_notified_at,
            proposal.last_notified_channel,
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
            signal_subtype=row["signal_subtype"] or "",
            confidence=float(row["confidence"] or 0.0),
            normalized_entity=row["normalized_entity"] or "",
            normalized_summary=row["normalized_summary"] or "",
            idempotency_key=row["idempotency_key"] or "",
            idempotency_expires_at=row["idempotency_expires_at"],
            preview_required=bool(row["preview_required"]),
            preview_shown_at=row["preview_shown_at"],
            preview_shown_by=row["preview_shown_by"],
            last_notified_at=row["last_notified_at"],
            last_notified_channel=row["last_notified_channel"],
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
        _, _, idempotency_key, _, _ = _compute_idempotency_fields(proposal)
        existing = self._conn.execute(
            """SELECT id, status FROM actions
               WHERE idempotency_key = ?
                 AND (
                     idempotency_expires_at IS NULL
                     OR idempotency_expires_at > ?
                 )
               LIMIT 1""",
            (proposal.idempotency_key or idempotency_key, self._now_utc()),
        ).fetchone()
        if existing:
            raise ValueError(
                f"Duplicate: live action with matching idempotency key already exists "
                f"(id={existing['id']}, status={existing['status']})"
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
                """INSERT INTO actions (
                    id, created_at, updated_at,
                    action_type, domain, friction, min_trust,
                    title, description, parameters, sensitivity,
                    status, approved_at, executed_at, approved_by, expires_at,
                    result_status, result_message, result_data,
                    source_step, source_skill, source_domain, linked_oi,
                    signal_subtype, confidence, normalized_entity, normalized_summary,
                    idempotency_key, idempotency_expires_at,
                    preview_required, preview_shown_at, preview_shown_by,
                    last_notified_at, last_notified_channel,
                    reversible, reverse_action_id, undo_window_sec,
                    bridge_synced, origin
                ) VALUES (
                    ?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?,
                    ?,?,?,?, ?,?, ?,?,?, ?,?, ?,?,?, ?,?
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
            if to_status in ("rejected", "expired", "cancelled", "failed"):
                updates["idempotency_expires_at"] = now

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

    def find_live_duplicate(
        self,
        proposal: ActionProposal,
    ) -> dict[str, Any] | None:
        """Return a live duplicate row for a proposal's idempotency key, if any."""
        _, _, key, _, _ = _compute_idempotency_fields(proposal)
        row = self._conn.execute(
            """SELECT * FROM actions
               WHERE idempotency_key = ?
                 AND (idempotency_expires_at IS NULL OR idempotency_expires_at > ?)
               LIMIT 1""",
            (proposal.idempotency_key or key, self._now_utc()),
        ).fetchone()
        return dict(row) if row else None

    def mark_preview_shown(
        self,
        action_id: str,
        shown_by: str,
        *,
        notified_channel: str | None = None,
    ) -> None:
        """Record a preview receipt for approval-gated actions."""
        now = self._now_utc()
        with self._conn:
            self._conn.execute(
                """UPDATE actions
                   SET preview_shown_at = COALESCE(preview_shown_at, ?),
                       preview_shown_by = COALESCE(preview_shown_by, ?),
                       last_notified_at = COALESCE(?, last_notified_at),
                       last_notified_channel = COALESCE(?, last_notified_channel),
                       updated_at = ?
                   WHERE id = ?""",
                (now, shown_by, now if notified_channel else None, notified_channel, now, action_id),
            )

    def update_notification_state(self, action_id: str, channel: str) -> None:
        """Persist the last notification timestamp/channel for a proposal."""
        now = self._now_utc()
        with self._conn:
            self._conn.execute(
                """UPDATE actions
                   SET last_notified_at = ?, last_notified_channel = ?, updated_at = ?
                   WHERE id = ?""",
                (now, channel, now, action_id),
            )

    def get_trust_state(self) -> dict[str, Any]:
        """Return trust authority from actions.db."""
        row = self._conn.execute(
            "SELECT * FROM trust_state WHERE singleton_key = 1"
        ).fetchone()
        state = dict(row) if row else {}
        cats = self._conn.execute(
            """SELECT action_type FROM trust_preapproved_categories
               WHERE l2_enabled = 1 ORDER BY action_type"""
        ).fetchall()
        state["pre_approved_categories"] = [r["action_type"] for r in cats]
        state["degraded_mode"] = bool(state.get("degraded_mode", 0))
        return state

    def upsert_trust_state(
        self,
        updates: dict[str, Any],
        *,
        actor: str,
        mutation_type: str,
        justification: str = "",
    ) -> dict[str, Any]:
        """Merge updates into trust_state and append an audit row."""
        current = self.get_trust_state()
        merged = dict(current)
        merged.update(updates)
        merged.setdefault("trust_level", 0)
        merged.setdefault("entered_level_at", datetime.now(timezone.utc).date().isoformat())
        merged.setdefault("days_at_level", 0)
        merged.setdefault("acceptance_rate_90d", 0.0)
        merged.setdefault("critical_false_positives", 0)
        merged.setdefault("degraded_mode", False)
        merged.setdefault("degraded_reason", None)
        merged.setdefault("last_demotion", None)
        merged.setdefault("last_elevation", None)
        now = self._now_utc()
        old_json = json.dumps(current, sort_keys=True)
        new_json = json.dumps({k: v for k, v in merged.items() if k != "pre_approved_categories"}, sort_keys=True)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO trust_state (
                    singleton_key, trust_level, entered_level_at, days_at_level,
                    acceptance_rate_90d, critical_false_positives,
                    degraded_mode, degraded_reason, last_demotion,
                    last_elevation, updated_at, updated_by
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_key) DO UPDATE SET
                    trust_level = excluded.trust_level,
                    entered_level_at = excluded.entered_level_at,
                    days_at_level = excluded.days_at_level,
                    acceptance_rate_90d = excluded.acceptance_rate_90d,
                    critical_false_positives = excluded.critical_false_positives,
                    degraded_mode = excluded.degraded_mode,
                    degraded_reason = excluded.degraded_reason,
                    last_demotion = excluded.last_demotion,
                    last_elevation = excluded.last_elevation,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (
                    int(merged["trust_level"]),
                    str(merged["entered_level_at"]),
                    int(merged.get("days_at_level", 0)),
                    float(merged.get("acceptance_rate_90d", 0.0)),
                    int(merged.get("critical_false_positives", 0)),
                    1 if merged.get("degraded_mode") else 0,
                    merged.get("degraded_reason"),
                    merged.get("last_demotion"),
                    merged.get("last_elevation"),
                    now,
                    actor,
                ),
            )
            self._conn.execute(
                """INSERT INTO audit_trust_mutations
                   (id, timestamp, actor, mutation_type, old_value, new_value, justification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self._new_id(), now, actor, mutation_type, old_json, new_json, justification or ""),
            )
        return self.get_trust_state()

    def set_preapproved_category(
        self,
        action_type: str,
        *,
        enabled: bool,
        actor: str,
        justification: str = "",
    ) -> None:
        """Enable or disable L2 eligibility for a category in trust authority."""
        now = self._now_utc()
        old = self._conn.execute(
            "SELECT l2_enabled FROM trust_preapproved_categories WHERE action_type = ?",
            (action_type,),
        ).fetchone()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO trust_preapproved_categories
                    (action_type, l2_enabled, enabled_at, disabled_at, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_type) DO UPDATE SET
                    l2_enabled = excluded.l2_enabled,
                    enabled_at = CASE WHEN excluded.l2_enabled = 1 THEN excluded.enabled_at ELSE enabled_at END,
                    disabled_at = CASE WHEN excluded.l2_enabled = 0 THEN excluded.disabled_at ELSE disabled_at END,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (action_type, 1 if enabled else 0, now if enabled else None, now if not enabled else None, now, actor),
            )
            self._conn.execute(
                """INSERT INTO audit_trust_mutations
                   (id, timestamp, actor, mutation_type, old_value, new_value, justification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._new_id(),
                    now,
                    actor,
                    "set_preapproved_category",
                    json.dumps({"action_type": action_type, "enabled": int(old["l2_enabled"])}) if old else None,
                    json.dumps({"action_type": action_type, "enabled": int(enabled)}),
                    justification or "",
                ),
            )

    def record_suppressed_proposal(
        self,
        *,
        signal_subtype: str,
        domain: str,
        action_type: str,
        reason: str,
        normalized_entity: str = "",
        normalized_summary: str = "",
        shadow_title: str = "",
        shadow_description: str = "",
    ) -> None:
        """Persist a short-lived shadow proposal for suppression analysis."""
        with self._conn:
            self._conn.execute(
                """INSERT INTO suppressed_proposals
                   (id, created_at, signal_subtype, domain, action_type, reason,
                    normalized_entity, normalized_summary, shadow_title, shadow_description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._new_id(),
                    self._now_utc(),
                    signal_subtype or None,
                    domain or None,
                    action_type or None,
                    reason,
                    normalized_entity or None,
                    normalized_summary or None,
                    shadow_title or None,
                    shadow_description or None,
                ),
            )

    def report_summary(self) -> dict[str, Any]:
        """Return an operator report for queue, trust, and decision health."""
        cutoff_30d = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat(timespec="seconds")
        counts = self._conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM actions GROUP BY status"
        ).fetchall()
        trust = self.get_trust_state()
        summary = {row["status"]: row["cnt"] for row in counts}
        metrics = self._conn.execute(
            """SELECT user_decision, COUNT(*) AS cnt
               FROM trust_metrics
               WHERE proposed_at >= ?
               GROUP BY user_decision""",
            (cutoff_30d,),
        ).fetchall()
        decisions = {row["user_decision"]: row["cnt"] for row in metrics}
        total_decisions = sum(decisions.values())
        approved = decisions.get("approved", 0)
        suppression_rows = self._conn.execute(
            """SELECT action_type, COUNT(*) AS cnt
               FROM suppressed_proposals
               WHERE created_at >= ?
               GROUP BY action_type
               ORDER BY cnt DESC""",
            (cutoff_30d,),
        ).fetchall()
        reason_rows = self._conn.execute(
            """SELECT reason, COUNT(*) AS cnt
               FROM suppressed_proposals
               WHERE created_at >= ?
               GROUP BY reason
               ORDER BY cnt DESC""",
            (cutoff_30d,),
        ).fetchall()
        suppression_total = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM suppressed_proposals WHERE created_at >= ?",
            (cutoff_30d,),
        ).fetchone()
        return {
            "queue": summary,
            "decisions_30d": decisions,
            "acceptance_rate_30d": round((approved / total_decisions), 4) if total_decisions else 0.0,
            "trust": trust,
            "suppressed_30d": {
                "total": suppression_total["cnt"] if suppression_total else 0,
                "by_action_type": {row["action_type"]: row["cnt"] for row in suppression_rows},
                "by_reason": {row["reason"]: row["cnt"] for row in reason_rows},
            },
        }

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

    def list_approved(self, privkey: str | None = None) -> list[ActionProposal]:
        """Return all APPROVED actions awaiting execution (cancellable)."""
        rows = self._conn.execute(
            """SELECT * FROM actions
               WHERE status = 'approved'
               ORDER BY approved_at ASC""",
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
        *,
        signal_subtype: str | None = None,
        proposal_confidence: float | None = None,
        source_origin: str | None = None,
        rejection_category: str | None = None,
        normalized_entity: str | None = None,
    ) -> None:
        """Append a trust metric row for elevation scoring."""
        with self._conn:
            self._conn.execute(
                """INSERT INTO trust_metrics
                   (id, action_type, domain, proposed_at, user_decision,
                    execution_result, feedback, signal_subtype,
                    proposal_confidence, source_origin, rejection_category,
                    normalized_entity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._new_id(), action_type, domain,
                    self._now_utc(), user_decision,
                    execution_result, feedback,
                    signal_subtype,
                    proposal_confidence,
                    source_origin,
                    rejection_category,
                    normalized_entity,
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
                """INSERT INTO actions (
                    id, created_at, updated_at,
                    action_type, domain, friction, min_trust,
                    title, description, parameters, sensitivity,
                    status, approved_at, executed_at, approved_by, expires_at,
                    result_status, result_message, result_data,
                    source_step, source_skill, source_domain, linked_oi,
                    signal_subtype, confidence, normalized_entity, normalized_summary,
                    idempotency_key, idempotency_expires_at,
                    preview_required, preview_shown_at, preview_shown_by,
                    last_notified_at, last_notified_channel,
                    reversible, reverse_action_id, undo_window_sec,
                    bridge_synced, origin
                ) VALUES (
                    ?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?,
                    ?,?,?,?, ?,?, ?,?,?, ?,?, ?,?,?, ?,?
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
