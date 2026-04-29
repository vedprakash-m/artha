#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/migrate_actions_quality.py — Idempotent schema migration for Phase 2-4.

Adds new columns and tables to actions.db required by specs/action-convert.md.
Safe to run multiple times — uses try/except on OperationalError for each ALTER TABLE.

Changes:
  - trust_metrics.signal_subtype TEXT
  - trust_metrics.rejection_category TEXT
  - trust_metrics.normalized_entity TEXT
  - actions.confidence REAL
  - actions.normalized_entity TEXT
  - actions.signal_subtype TEXT  (written at propose time; read by _write_rejection_category)
  - NEW TABLE: signal_suppression (signal_subtype, domain, reason, created_at, source_action_id)
  - NEW TABLE: policy_suggestions (id, type, value, signal_type, source_action_id, created_at, applied_at)
  - UNIQUE INDEX: idx_ps_active on policy_suggestions

Ref: specs/action-convert.md §6 + CONSTRAINT 3

Usage:
    python3 scripts/migrate_actions_quality.py [--db-path <path>] [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _resolve_db_path(artha_dir: Path) -> Path:
    """Resolve DB path using the same logic as ActionQueue._resolve_db_path."""
    import os
    import platform

    env_override = os.environ.get("ARTHA_LOCAL_DB", "").strip()
    if env_override:
        return Path(env_override)

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

    return artha_dir / "state" / "actions.db"


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open actions.db with the mandatory pragma sequence (mirrors action_queue._open_db)."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
    dry_run: bool = False,
) -> bool:
    """Add a column to a table if it does not exist. Returns True if added."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column in existing:
        return False

    sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
    if dry_run:
        print(f"[migrate] DRY-RUN: Would execute: {sql}")
        return True

    try:
        conn.execute(sql)
        return True
    except sqlite3.OperationalError as exc:
        if "duplicate column" in str(exc).lower():
            return False  # race: another process beat us here
        raise


def main(artha_dir: Path | None = None, db_path: Path | None = None, dry_run: bool = False) -> bool:
    """Run all quality-layer schema migrations idempotently.

    Args:
        artha_dir: Artha workspace root. Used to resolve db_path if not given.
        db_path:   Explicit DB path override (for testing).
        dry_run:   If True, print actions without executing them.

    Returns:
        True on success, False on failure.
    """
    if artha_dir is None:
        artha_dir = _ARTHA_DIR

    if db_path is None:
        db_path = _resolve_db_path(artha_dir)

    if not db_path.exists():
        print(f"[migrate] DB not found at {db_path} — nothing to migrate.")
        return True  # Not an error: DB may not exist yet

    print(f"[migrate] Migrating {db_path}")

    try:
        conn = _open_db(db_path)
    except Exception as exc:
        print(f"[migrate] ERROR: Cannot open DB: {exc}", file=sys.stderr)
        return False

    try:
        any_added = False

        # --- trust_metrics additions ---
        if _add_column_if_missing(conn, "trust_metrics", "signal_subtype", "TEXT", dry_run):
            print("[migrate] Adding signal_subtype column to trust_metrics...")
            any_added = True

        if _add_column_if_missing(conn, "trust_metrics", "rejection_category", "TEXT", dry_run):
            print("[migrate] Adding rejection_category column to trust_metrics...")
            any_added = True

        if _add_column_if_missing(conn, "trust_metrics", "normalized_entity", "TEXT", dry_run):
            print("[migrate] Adding normalized_entity column to trust_metrics...")
            any_added = True

        # --- actions table additions ---
        if _add_column_if_missing(conn, "actions", "confidence", "REAL", dry_run):
            print("[migrate] Adding confidence column to actions...")
            any_added = True

        if _add_column_if_missing(conn, "actions", "normalized_entity", "TEXT", dry_run):
            print("[migrate] Adding normalized_entity column to actions...")
            any_added = True

        if _add_column_if_missing(conn, "actions", "signal_subtype", "TEXT", dry_run):
            print("[migrate] Adding signal_subtype column to actions...")
            any_added = True

        # --- signal_suppression table ---
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_suppression'"
        )
        if not cur.fetchone():
            sql = """
                CREATE TABLE IF NOT EXISTS signal_suppression (
                    signal_subtype  TEXT NOT NULL,
                    domain          TEXT NOT NULL,
                    reason          TEXT,
                    created_at      TEXT NOT NULL,
                    source_action_id TEXT,
                    PRIMARY KEY (signal_subtype, domain)
                )
            """
            if dry_run:
                print("[migrate] DRY-RUN: Would create signal_suppression table")
            else:
                conn.execute(sql)
                print("[migrate] Creating signal_suppression table...")
            any_added = True

        # --- policy_suggestions table ---
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='policy_suggestions'"
        )
        if not cur.fetchone():
            sql = """
                CREATE TABLE IF NOT EXISTS policy_suggestions (
                    id              TEXT PRIMARY KEY,
                    type            TEXT NOT NULL,
                    value           TEXT NOT NULL,
                    signal_type     TEXT,
                    source_action_id TEXT,
                    created_at      TEXT NOT NULL,
                    applied_at      TEXT
                )
            """
            if dry_run:
                print("[migrate] DRY-RUN: Would create policy_suggestions table")
            else:
                conn.execute(sql)
                print("[migrate] Creating policy_suggestions table...")
            any_added = True

        # --- Unique partial index on policy_suggestions ---
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ps_active'"
        )
        if not cur.fetchone():
            sql = (
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ps_active "
                "ON policy_suggestions (type, value, COALESCE(signal_type, '')) "
                "WHERE applied_at IS NULL"
            )
            if dry_run:
                print("[migrate] DRY-RUN: Would create idx_ps_active index")
            else:
                conn.execute(sql)
                print("[migrate] Creating idx_ps_active index on policy_suggestions...")
            any_added = True

        if not any_added:
            print("[migrate] Already migrated.")
        else:
            if not dry_run:
                conn.commit()
            print("[migrate] Done.")

        return True

    except Exception as exc:
        print(f"[migrate] ERROR during migration: {exc}", file=sys.stderr)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Idempotent schema migration for Artha action quality layer."
    )
    parser.add_argument("--db-path", metavar="PATH", help="Explicit DB path override")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print actions without executing them"
    )
    parser.add_argument(
        "--artha-dir", metavar="PATH", default=str(_ARTHA_DIR), help="Artha workspace root"
    )
    args = parser.parse_args()

    artha_dir = Path(args.artha_dir).resolve()
    db_path = Path(args.db_path).resolve() if args.db_path else None

    success = main(artha_dir=artha_dir, db_path=db_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
