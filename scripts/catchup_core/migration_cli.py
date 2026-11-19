"""Owner-reviewed ledger migration preview, verified backup and resumable receipt."""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from catchup_core.ledger import Ledger, LedgerError

_HEX = re.compile(r"[0-9a-f]{64}")


def plan_digest(migrations: list[tuple[int, Path]]) -> str:
    digest = hashlib.sha256()
    for number, path in migrations:
        content = path.read_bytes()
        digest.update(f"{number}\0{path.name}\0".encode())
        digest.update(hashlib.sha256(content).hexdigest().encode() + b"\0")
    return digest.hexdigest()


def _duplicate_bindings(ledger: Ledger) -> tuple[int, int]:
    if ledger.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scope_bindings'").fetchone() is None:
        return 0, 0
    groups, rows = ledger.conn.execute(
        "SELECT COUNT(*),COALESCE(SUM(items),0) FROM ("
        "SELECT COUNT(*) items FROM scope_bindings "
        "GROUP BY object_type,object_id,object_version,permission_generation HAVING COUNT(*)>1)"
    ).fetchone()
    return int(groups or 0), int(rows or 0)


def preview(ledger: Ledger) -> dict[str, Any]:
    current = ledger.schema_version()
    migrations = ledger.available_migrations()
    target = max(number for number, _path in migrations) if migrations else current
    duplicate_groups, duplicate_rows = _duplicate_bindings(ledger)
    disk = shutil.disk_usage(ledger._path.parent)
    return {
        "schema": "artha.ledger-migration.v1",
        "ledger_path": str(ledger._path.resolve()),
        "digest": plan_digest(migrations),
        "current_schema": current,
        "target_schema": target,
        "ledger_bytes": ledger._path.stat().st_size if ledger._path.exists() else 0,
        "free_bytes": int(disk.free),
        "duplicate_binding_groups": duplicate_groups,
        "duplicate_binding_rows": duplicate_rows,
        "pending_migrations": [
            path.name for number, path in migrations if number > current],
    }


def verify_sqlite_backup(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise LedgerError("migration backup is missing or empty")
    with closing(sqlite3.connect(path)) as restored:
        if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise LedgerError("migration backup integrity check failed")


def apply(
        ledger: Ledger, expected_digest: str, *,
        backup: Callable[[Path], Path],
        verify_backup: Callable[[Path], None] = verify_sqlite_backup) -> dict[str, Any]:
    if not isinstance(expected_digest, str) or not _HEX.fullmatch(expected_digest):
        raise LedgerError("migration apply needs the exact preview digest")
    plan = preview(ledger)
    if plan["digest"] != expected_digest:
        raise LedgerError("migration sources changed after preview")
    target = int(plan["target_schema"])
    before_bindings = ledger.conn.execute("SELECT COUNT(*) FROM scope_bindings").fetchone()[0]

    if plan["current_schema"] < target:
        def verified(path: Path) -> Path:
            result = Path(backup(path))
            verify_backup(result)
            return result

        ledger.migrate(backup=verified)
        already_applied = False
    else:
        already_applied = True
    integrity = str(ledger.conn.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity != "ok":
        raise LedgerError("ledger integrity check failed after migration")
    if ledger.schema_version() != target:
        raise LedgerError("migration did not reach its planned schema")
    after_bindings = ledger.conn.execute("SELECT COUNT(*) FROM scope_bindings").fetchone()[0]
    expected_after = before_bindings - int(plan["duplicate_binding_rows"])
    if not already_applied and after_bindings != expected_after:
        raise LedgerError("migration changed scope bindings beyond reviewed duplicate groups")
    for table in ("scope_reviews", "source_enrollments", "signal_source_enrollments", "coverage_holds"):
        if ledger.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
            raise LedgerError(f"migration did not create {table}")
    if (not already_applied
            and ledger.conn.execute("SELECT COUNT(*) FROM source_enrollments").fetchone()[0] != 0):
        raise LedgerError("migration unexpectedly copied source enrollment state")
    receipt = ledger.conn.execute(
        "SELECT verified_migration,receipt_digest FROM scope_admission_state WHERE singleton=1"
    ).fetchone()
    if receipt is None:
        raise LedgerError("migration verification receipt state is unavailable")
    if receipt["verified_migration"] != target or receipt["receipt_digest"] != expected_digest:
        ledger.seal_privacy()
        with ledger.transaction() as db:
            db.execute(
                "UPDATE scope_admission_state SET verified_migration=?,receipt_digest=? WHERE singleton=1",
                (target, expected_digest))
    return {
        **plan, "integrity": integrity, "schema": target, "already_applied": already_applied,
        "scope_bindings_before": before_bindings, "scope_bindings_after": after_bindings,
    }
