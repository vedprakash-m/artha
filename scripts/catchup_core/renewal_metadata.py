"""Read-only release authority metadata; no runtime creation, migration or personal evidence reads.

Only consent/deletion authority history is decrypted through its existing capsule
broker. Signals, Facts, State Files, checkpoints and provider payloads are never opened.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from catchup_core.crypto import DecryptionError, EnvelopeCipher, KeyringKeyProvider
from catchup_core.ledger import KeyringSealedStore, Ledger, LedgerError
from catchup_core.privacy_history import ANCHOR_SCHEMA, PrivacyCapsules, checked_history, checkpoint_data
from catchup_core.quarantine_fs_windows import QuarantineFilesystemError
from catchup_core.renewal_authority import Authority, _time
from catchup_core.renewal_store import RenewalError, _chain, digest, private_directory, read_private
from catchup_core.schema_history import sources, verify

_LIMIT = 100_000


class _AnchorReader(KeyringSealedStore):
    record: dict[str, Any] | None = None

    def _record(self) -> dict[str, Any] | None:
        self.record = super()._record()
        return self.record


class AuthorityMetadata:
    def __init__(self, local: Path, backend: Any, host: str, *, cipher: EnvelopeCipher | None = None,
                 history_root: Path | None = None, now: Callable[[], datetime] | None = None):
        self.local, self.backend, self.host = local, backend, host
        self.cipher, self.history_root = cipher, history_root
        self.now = now or (lambda: datetime.now(UTC))
        self._capsule_key: tuple[Any, ...] = ()
        self._capsule: dict[str, Any] = {}

    def _anchor(self) -> tuple[dict[str, Any], _AnchorReader]:
        sealed = _AnchorReader(self.backend, 'authoritative', history_root=self.history_root)
        sealed.read()  # The established native adapter bounds arbitrary backend failures.
        record = sealed.record
        if record is None or record.get('schema') != ANCHOR_SCHEMA:
            raise RenewalError('current privacy authority continuity is unavailable', reason='authority')
        return record, sealed

    def _history(self, record: dict[str, Any], sealed: _AnchorReader) -> dict[str, Any]:
        directory = sealed._history_root / hashlib.sha256(sealed._service.encode()).hexdigest()
        path = directory / (record['capsule_sha256'] + '.ae1')
        private_directory(directory)
        _chain(path)
        info = path.lstat()
        key = (record['capsule_sha256'], info.st_dev, info.st_ino, info.st_size,
               info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_nlink, info.st_uid)
        if key != self._capsule_key or sys.platform == 'win32':
            cipher = self.cipher or EnvelopeCipher(
                KeyringKeyProvider('artha-ledger', backend=self.backend, create=False))
            raw = read_private(path, maximum=64 * 1024 * 1024 + 1)
            history = PrivacyCapsules(directory, sealed._service, cipher).decode(raw, record)
            after = path.lstat()
            if key[1:] != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
                           after.st_mode, after.st_nlink, after.st_uid):
                raise RenewalError('privacy authority changed during inspection', reason='authority')
            self._capsule, self._capsule_key = history, key
        return self._capsule

    def snapshot(self, *, connection: sqlite3.Connection | None = None) -> str:
        try:
            private_directory(self.local)
            path = self.local / 'ledger.db'
            _chain(path)
            before = path.lstat()
            record, sealed = self._anchor()
            if connection is None:
                # Immutable mode cannot create WAL/SHM files. A live WAL needs the
                # already-open runtime connection, never an implicit checkpoint/write.
                if any(side.exists() and side.stat().st_size for side in
                       (path.with_name(path.name + '-wal'), path.with_name(path.name + '-journal'))):
                    raise RenewalError('authority metadata needs a quiescent ledger', reason='authority')
                with closing(sqlite3.connect(
                        path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True, timeout=2)) as db:
                    db.execute('PRAGMA query_only=ON')
                    result = self._inspect(db, record, sealed)
            else:
                names = connection.execute('PRAGMA database_list').fetchall()
                if not any(row[1] == 'main' and Path(row[2]).resolve() == path.resolve() for row in names):
                    raise RenewalError('runtime authority connection differs', reason='authority')
                result = self._inspect(connection, record, sealed)
            after, _ = self._anchor()
            current = path.lstat()
            if after != record or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise RenewalError('privacy authority changed during inspection', reason='authority')
            return result
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, LedgerError,
                DecryptionError, QuarantineFilesystemError):
            raise RenewalError('current authority metadata is unavailable or inconsistent',
                               reason='authority') from None

    def _inspect(self, db: sqlite3.Connection, record: dict[str, Any], sealed: _AnchorReader) -> str:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        migrations = sources(Ledger.available_migrations())
        if version != len(migrations):
            raise RenewalError('authority ledger needs separately reviewed migration', reason='authority')
        verify(db, migrations, version)
        if db.execute("SELECT 1 FROM deletion_journal WHERE state<>'receipted' LIMIT 1").fetchone():
            raise RenewalError('pending erasure recovery holds renewal', reason='authority')
        authority = db.execute('SELECT generation,host,former_host_revoked_at FROM authority_generation '
                               'ORDER BY generation DESC LIMIT 1').fetchone()
        if authority is None or authority[1] != self.host or authority[2] is not None:
            raise RenewalError('current runtime authority host differs', reason='authority')
        checkpoint = db.execute('SELECT checkpoint_id,tombstone_digest,permission_generation FROM deletion_checkpoint '
                                'ORDER BY checkpoint_id DESC LIMIT 1').fetchone()
        if checkpoint is None or dict(zip(('checkpoint_id', 'tombstone_digest', 'permission_generation'),
                                         checkpoint, strict=True)) != checkpoint_data(record):
            raise RenewalError('privacy checkpoint is not current', reason='authority')
        groups = {}
        queries = {'tombstones': 'SELECT * FROM tombstones LIMIT 100001',
                   'consent': 'SELECT * FROM consent LIMIT 100001'}
        for name, query in queries.items():
            cursor = db.execute(query)
            columns = [column[0] for column in cursor.description or ()]
            groups[name] = [dict(zip(columns, row, strict=True)) for row in cursor]
            if len(groups[name]) > _LIMIT:
                raise RenewalError('authority history exceeds its inspection bound', reason='authority')
        current = checked_history(groups, checkpoint_data(record))
        native = self._history(record, sealed)
        for name, identity in (('tombstones', 'tombstone_id'), ('consent', 'consent_id')):
            if (sorted(current[name], key=lambda row: row[identity])
                    != sorted(native[name], key=lambda row: row[identity])):
                raise RenewalError('privacy history differs from its native authority', reason='authority')
        expired = [row['consent_id'] for row in current['consent']
                   if row.get('expires_at') and self.now() >= _time(row['expires_at'])]
        return digest({'anchor': record, 'authority': list(authority), 'schema': version, 'expired': sorted(expired)})


def metadata_for(authority: Authority) -> AuthorityMetadata:
    if authority.binding['profile'] != 'personal':
        raise RenewalError('renewal metadata profile is unsupported', reason='authority')
    if not isinstance(authority._metadata, AuthorityMetadata):
        authority._metadata = AuthorityMetadata(authority.local, authority.credentials._backend,
                                                authority.binding['host'], now=authority.now)
    return authority._metadata
