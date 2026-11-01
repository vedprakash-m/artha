"""Verify installed migration bytes and canonical schema without inventing legacy provenance."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from catchup_core.ledger import MigrationRefused, _split_statements

JOURNAL_VERSION = 15


class Migration(NamedTuple):
    number: int
    filename: str
    script: str
    sha256: str


def sources(paths: list[tuple[int, Path]]) -> tuple[Migration, ...]:
    if [number for number, _ in paths] != list(range(1, len(paths) + 1)):
        raise MigrationRefused('migration history must be nonempty, unique and contiguous from one')
    if not paths:
        raise MigrationRefused('migration history is unavailable')
    try:
        result = []
        for number, path in paths:
            raw = path.read_bytes()
            result.append(Migration(number, path.name, raw.decode('utf-8'), hashlib.sha256(raw).hexdigest()))
        return tuple(result)
    except (OSError, UnicodeError):
        raise MigrationRefused('migration history source is unreadable') from None


def _objects(db: sqlite3.Connection) -> dict[str, tuple[str, str, str]]:
    return {row[0]: (row[1], row[2], row[3]) for row in db.execute(
        "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}


@lru_cache(maxsize=16)
def _expected(installed: tuple[Migration, ...]) -> dict[str, tuple[str, str, str]]:
    # Cache public code only. No ledger values, paths, identities or credentials enter this cache.
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('PRAGMA foreign_keys=ON')
        try:
            for migration in installed:
                for statement in _split_statements(migration.script):
                    db.execute(statement)
        except sqlite3.Error:
            raise MigrationRefused('canonical migration schema cannot be verified') from None
        return _objects(db)


def verify(db: sqlite3.Connection, installed: tuple[Migration, ...], current: int) -> None:
    if not 0 <= current <= len(installed):
        raise MigrationRefused('ledger schema version has no installed migration history')
    expected = _expected(installed[:current])
    actual = _objects(db)
    tables = {name for name, value in expected.items() if value[0] == 'table'}
    if (any(actual.get(name) != value for name, value in expected.items())
            or any(name not in expected and value[1] in tables for name, value in actual.items())):
        raise MigrationRefused('ledger schema differs from canonical migration history; review required')
    journal = actual.get('schema_history')
    if current < JOURNAL_VERSION:
        if journal:
            raise MigrationRefused('ledger migration history is inconsistent with its schema version')
        return  # Provenance is explicitly unknown until verified adoption; do not write on a read.
    rows = db.execute('SELECT number,filename,sha256,verified_at,provenance '
                      'FROM schema_history ORDER BY number').fetchall()
    if len(rows) != current:
        raise MigrationRefused('ledger migration history is incomplete')
    for row, migration in zip(rows, installed[:current], strict=True):
        if (tuple(row[:3]) != (migration.number, migration.filename, migration.sha256) or not row[3]
                or row[4] not in {'applied', 'legacy_schema_verified'}
                or (migration.number >= JOURNAL_VERSION and row[4] != 'applied')):
            raise MigrationRefused('ledger migration history differs from installed sources')


def record(db: sqlite3.Connection, installed: tuple[Migration, ...], number: int,
           executed: set[int], verified_at: str) -> None:
    """Called inside the same DDL/version transaction; older legacy steps are explicitly adopted, not fabricated."""
    if number < JOURNAL_VERSION:
        return
    for migration in installed[:number]:
        db.execute('INSERT INTO schema_history(number,filename,sha256,verified_at,provenance) VALUES(?,?,?,?,?) '
            'ON CONFLICT(number) DO NOTHING', (migration.number, migration.filename, migration.sha256, verified_at,
                'applied' if migration.number in executed else 'legacy_schema_verified'))
