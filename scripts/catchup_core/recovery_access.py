"""Current-host authority for inert personal recovery, never key enrollment.

Unknown file lineage cannot be scoped to one household subject. Content access
therefore needs every protected subject's current recovery view/export consent;
human review cannot grant that consent or prove arbitrary historical prose clean.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from keyring.errors import KeyringError

from . import checkpoint as ck
from . import policy, runtime
from .backup import apply_privacy_history
from .crypto import EnvelopeCipher, KeyringKeyProvider, KeyUnavailableError
from .fact_access import AccessPolicy, consent_active
from .ledger import KeyringSealedStore, SealedCheckpoint
from .privacy_history import checked_history, checkpoint_data
from .quarantine_fs_windows import (
    QuarantineFilesystemError,
    WindowsQuarantineDirectory,
)
from .recovery_quarantine import QuarantineError, QuarantineStore
from .session_lock import SessionBusy, default_installation_dir
from .work_privacy import WorkPrivacyBlocked


class _MetadataStore(QuarantineStore):
    def iter_member(self, member_id: str) -> Iterator[bytes]:
        raise QuarantineError('content requires separately authorized recovery export')


class _CaptureStore(QuarantineStore):
    def __init__(self, *args: Any, capture_affinity: Callable[[], Mapping[str, Any]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._capture_affinity = capture_affinity

    def seal(self) -> dict[str, Any]:
        self.set_recovery_affinity(self._capture_affinity())
        return super().seal()


class _ContentStore(QuarantineStore):
    def __init__(self, *args: Any, affinity: Mapping[str, Any], denied: set[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._affinity = affinity
        self._denied = frozenset(denied)

    def iter_member(self, member_id: str) -> Iterator[bytes]:
        catalog = self.load_catalog()
        if catalog.get('recovery_affinity') != self._affinity:
            raise QuarantineError('original recovery provenance changed')
        member = next((entry for entry in catalog['entries'] if entry['member_id'] == member_id), None)
        if member is None or member['path'] in self._denied:
            raise QuarantineError('current erasure forbids this historical member')
        yield from super().iter_member(member_id)


class RecoveryAccess:
    def __init__(self, repo: Path, backend: Any, cipher: EnvelopeCipher, *,
                 sealed_for: Callable[[str], Any] | None = None, actor: str | None = None,
                 now: Callable[[], datetime] | None = None) -> None:
        self.repo = repo.absolute()
        self.backend = backend
        self.cipher = cipher
        self.sealed_for = sealed_for
        self.actor = actor
        self.now = now or (lambda: datetime.now(UTC))

    def authorize(self) -> None:
        self._configuration()

    @staticmethod
    def _mode(ledger: Path) -> str:
        return 'shadow' if ledger.parent.name.startswith('shadow') else 'authoritative'

    def _sealed(self, mode: str) -> Any:
        return (self.sealed_for(mode) if self.sealed_for is not None else
                KeyringSealedStore(self.backend, mode, cipher=self.cipher))

    def _heads(self, modes: set[str]) -> dict[str, Any]:
        heads = {}
        for mode in sorted(modes):
            checkpoint = self._sealed(mode).read()
            if checkpoint is None:
                raise ck.CheckpointBlocked('current trusted privacy history is required')
            heads[mode] = checkpoint_data(vars(checkpoint))
        return heads

    def history_digest(self, modes: set[str]) -> str:
        """SHA-256 of the verified current privacy history, pinned against the trusted head, for recovery receipts."""
        histories = self._history(modes, self._heads(modes))
        return hashlib.sha256(json.dumps({mode: histories[mode] for mode in sorted(modes)}, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def _configuration(self) -> tuple[runtime.RuntimeConfig, dict[str, str]]:
        config = runtime.load_runtime_config(self.repo / 'config/catchup_core.yaml')
        if self.actor is not None and self.actor != config.viewer:
            raise ck.CheckpointBlocked('current resolved local owner is required')
        cohort = policy.evaluation_cohort(config.policy, config.domains,
                                         extractor_command=config.extractor_cmd, workspace=self.repo)
        if config.policy.get('household_from_profile') is True:
            provider = KeyringKeyProvider('artha-ledger-ids', backend=self.backend, create=False)
            pack = runtime._load_packs(config.domains)
            runtime._bind_household(config, self.repo, runtime._id_key(provider),
                                    self.now().astimezone(pack.timezone).date(), enroll=False)
        if config.viewer not in config.subjects or not config.subjects:
            raise ck.CheckpointBlocked('current resolved local owner is required')
        cohort['recovery_permissions'] = hashlib.sha256(json.dumps({
            'owner': config.viewer, 'subjects': sorted(config.subjects),
            'consent_required': sorted(config.consent_required)}, sort_keys=True).encode()).hexdigest()
        return config, cohort

    def _base(self, modes: set[str]) -> tuple[dict[str, Any], runtime.RuntimeConfig]:
        config, cohort = self._configuration()
        return {'actor': config.viewer, 'owner': config.viewer, 'profile': 'personal',
                'privacy': self._heads(modes), 'policy': cohort}, config

    @staticmethod
    def _safe(root: Path, path: Path, *, directory: bool = False) -> None:
        if not path.is_relative_to(root) or '..' in path.parts:
            raise ck.CheckpointBlocked('recovery path escapes its inactive destination')
        for parent in (path, *path.parents):
            info = parent.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                    or ((parent != path or directory) and not stat.S_ISDIR(info.st_mode))):
                raise ck.CheckpointBlocked('linked or unsafe recovery path')
        info = path.lstat()
        if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ck.CheckpointBlocked('linked or unsafe recovery file')

    def _fence(self, dest: Path) -> None:
        if (dest.resolve().is_relative_to(self.repo.resolve())
                or self.repo.resolve().is_relative_to(dest.resolve())):
            raise ck.CheckpointBlocked('recovery destination overlaps the live repository')
        self._safe(dest, dest, directory=True)
        marker = dest / '.artha-restored.json'
        self._safe(dest, marker)
        if marker.stat().st_size > 65536:
            raise ck.CheckpointBlocked('invalid restored-installation fence')
        value = json.loads(marker.read_bytes())
        if not isinstance(value, dict):
            raise ck.CheckpointBlocked('invalid restored-installation fence')
        for field in ('ingestion', 'effects'):
            if ((value.get(field) != 'disabled' and value.get(field + '_disabled') is not True)
                    or (field in value and value[field] != 'disabled')
                    or (field + '_disabled' in value and value[field + '_disabled'] is not True)):
                raise ck.CheckpointBlocked('restored ingestion and effects must remain disabled')

    def _ledgers(self, dest: Path) -> list[Path]:
        self._fence(dest)
        ledgers = sorted(dest.rglob('ledger.db'))
        if not ledgers:
            raise ck.CheckpointBlocked('a restored personal privacy ledger is required')
        for ledger in ledgers:
            if not ck._privacy_ledger({'path': ledger.relative_to(dest).as_posix(), 'kind': 'sqlite'}):
                raise ck.CheckpointBlocked('unexpected plaintext recovery ledger')
            self._safe(dest, ledger)
        allowed = {*ledgers, dest / '.artha-restored.json', dest / '.quarantine-clean-file-review.lock'}
        quarantine = dest / '.artha-quarantine'
        for current, directories, files in os.walk(dest, followlinks=False):
            for name in directories:
                self._safe(dest, Path(current) / name, directory=True)
            for name in files:
                path = Path(current) / name
                self._safe(dest, path)
                if path.is_relative_to(quarantine) or path in allowed:
                    continue
                if path.parent == dest / 'reviewed-exports' and path.suffix == '.ae1':
                    with path.open('rb') as handle:
                        if handle.read(3) == b'AE1':
                            continue
                raise ck.CheckpointBlocked('unexpected plaintext recovery copy; lineage remains unproven')
        return ledgers

    @contextmanager
    def _windows_database(self, ledger: Path, *, update: bool,
                          expected_sha256: str | None) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(':memory:')) as db, \
                WindowsQuarantineDirectory.open(ledger.parent) as parent, \
                parent.open_file(ledger.name, writable=update) as handle, \
                handle.lock():
            before = handle.snapshot()
            if before.size > ck._MAX_SQLITE_BYTES:
                raise ck.CheckpointBlocked('restored privacy ledger exceeds its bound')
            raw = handle.read(ck._MAX_SQLITE_BYTES + 1)
            if len(raw) != before.size:
                raise ck.CheckpointBlocked('restored privacy ledger changed during read')
            if (expected_sha256 is not None
                    and hashlib.sha256(raw).hexdigest() != expected_sha256):
                raise ck.CheckpointBlocked(
                    'original recovery ledger differs from the authenticated checkpoint'
                )
            db.deserialize(raw)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA secure_delete=ON')
            if (db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                    or db.execute('PRAGMA foreign_key_check').fetchone()):
                raise ck.CheckpointBlocked('restored privacy ledger is invalid')
            yield db
            after = handle.snapshot()
            if (before != after
                    or handle.check_path(expected_identity=before.identity) != before.identity):
                raise ck.CheckpointBlocked('restored privacy ledger changed during replay')
            if update:
                serialized = db.serialize()
                if len(serialized) > ck._MAX_SQLITE_BYTES:
                    raise ck.CheckpointBlocked('restored privacy ledger exceeds its bound')
                handle.seek(0)
                handle.write_all(serialized)
                handle.truncate(len(serialized))
                handle.flush()
                if (handle.read(ck._MAX_SQLITE_BYTES + 1) != serialized
                        or handle.check_path(expected_identity=before.identity) != before.identity):
                    raise ck.CheckpointBlocked('restored privacy ledger changed during replay')

    @contextmanager
    def _database(self, dest: Path, ledger: Path, *, update: bool = False,
                  expected_sha256: str | None = None) -> Iterator[sqlite3.Connection]:
        self._safe(dest, ledger)
        if os.name == 'nt':
            try:
                with self._windows_database(
                        ledger, update=update, expected_sha256=expected_sha256) as db:
                    yield db
            except QuarantineFilesystemError:
                raise ck.CheckpointBlocked('unsafe restored privacy ledger') from None
            return
        no_follow = getattr(os, 'O_NOFOLLOW', None)
        nonblock = getattr(os, 'O_NONBLOCK', None)
        uid_reader = getattr(os, 'getuid', None)
        if not isinstance(no_follow, int) or not isinstance(nonblock, int) or not callable(uid_reader):
            raise ck.CheckpointBlocked('secure recovery ledger IO is unavailable')
        parent = QuarantineStore._directory(ledger.parent.absolute())
        try:
            descriptor = os.open(ledger.name, (os.O_RDWR if update else os.O_RDONLY)
                                 | no_follow | nonblock, dir_fd=parent)
            with os.fdopen(descriptor, 'r+b' if update else 'rb') as handle, closing(sqlite3.connect(':memory:')) as db:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != uid_reader():
                    raise ck.CheckpointBlocked('unsafe restored privacy ledger')
                if before.st_size > ck._MAX_SQLITE_BYTES:
                    raise ck.CheckpointBlocked('restored privacy ledger exceeds its bound')
                raw = handle.read(ck._MAX_SQLITE_BYTES + 1)
                if len(raw) != before.st_size:
                    raise ck.CheckpointBlocked('restored privacy ledger changed during read')
                if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
                    raise ck.CheckpointBlocked('original recovery ledger differs from the authenticated checkpoint')
                db.deserialize(raw)
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA secure_delete=ON')
                if (db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                        or db.execute('PRAGMA foreign_key_check').fetchone()):
                    raise ck.CheckpointBlocked('restored privacy ledger is invalid')
                yield db
                after = os.fstat(handle.fileno())
                current = os.stat(ledger.name, dir_fd=parent, follow_symlinks=False)
                if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                        or current.st_nlink != 1):
                    raise ck.CheckpointBlocked('restored privacy ledger changed during replay')
                if update:
                    serialized = db.serialize()
                    if len(serialized) > ck._MAX_SQLITE_BYTES:
                        raise ck.CheckpointBlocked('restored privacy ledger exceeds its bound')
                    handle.seek(0)
                    handle.write(serialized)
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
        finally:
            os.close(parent)

    @staticmethod
    def _checkpoint(db: sqlite3.Connection, head: Mapping[str, Any]) -> None:
        row = db.execute('SELECT checkpoint_id,tombstone_digest,permission_generation FROM '
                         'deletion_checkpoint ORDER BY checkpoint_id DESC LIMIT 1').fetchone()
        digest = hashlib.sha256('\n'.join(row[0] for row in db.execute(
            'SELECT tombstone_id FROM tombstones ORDER BY tombstone_id')).encode()).hexdigest()
        generation = db.execute('SELECT COALESCE(SUM(revocation_version),0) FROM consent').fetchone()[0]
        if row is None or dict(row) != head or digest != head['tombstone_digest'] or generation != head[
                'permission_generation']:
            raise ck.CheckpointBlocked('restored privacy checkpoint is stale; fresh replay required')

    def _guard(self, dest: Path, ledgers: list[Path], pinned: dict[str, Any], *,
               restored: bool, export: bool = False) -> Callable[[], dict[str, Any]]:
        def authority() -> dict[str, Any]:
            self._fence(dest)
            current, config = self._base(set(pinned['privacy']))
            if current != pinned:
                raise ck.CheckpointBlocked('recovery authority changed; fresh replay required')
            if restored:
                if self._ledgers(dest) != ledgers:
                    raise ck.CheckpointBlocked('restored privacy ledger inventory changed')
                histories = self._history(set(pinned['privacy']), pinned['privacy']) if export else {}
                for ledger in ledgers:
                    with self._database(dest, ledger) as db:
                        self._checkpoint(db, pinned['privacy'][self._mode(ledger)])
                        if export:
                            self._export(db, config, histories[self._mode(ledger)])
            if self._base(set(pinned['privacy']))[0] != pinned:
                raise ck.CheckpointBlocked('recovery authority changed; fresh replay required')
            # Role is authenticated inside review/export bindings. A metadata-only
            # store must not decrypt an existing self-contained content export.
            return {**current, 'access': 'content' if export else 'metadata'}
        return authority

    def factory(self, dest: Path, manifest: Mapping[str, Any], checkpoint_sha256: str) -> QuarantineStore:
        if manifest.get('profile') != 'personal':
            raise ck.CheckpointBlocked('only personal recovery quarantine is authorized')
        entries = manifest.get('entries', [])
        ledgers = [dest / entry['path'] for entry in entries if ck._privacy_ledger(entry)]
        if not ledgers:
            raise ck.CheckpointBlocked('manifest requires at least one personal privacy ledger')
        self._fence(dest)
        modes = {self._mode(ledger) for ledger in ledgers}
        pinned, _ = self._base(modes)
        self._history(modes, pinned['privacy'])
        frozen = json.loads(json.dumps(dict(manifest), allow_nan=False))
        store = _CaptureStore(dest / '.artha-quarantine', self.cipher,
                              authority=self._guard(dest, ledgers, pinned, restored=False),
                              capture_affinity=lambda: self._capture_affinity(dest, frozen))
        store.begin(dict(manifest, entries=[entry for entry in entries if not ck._privacy_ledger(entry)]),
                    checkpoint_sha256)
        return store

    def _history(self, modes: set[str], heads: dict[str, Any]) -> dict[str, Any]:
        if self._heads(modes) != heads:
            raise ck.CheckpointBlocked('privacy head changed before replay')
        histories = {mode: checked_history(self._sealed(mode).replay(), heads[mode]) for mode in sorted(modes)}
        if self._heads(modes) != heads:
            raise ck.CheckpointBlocked('privacy head changed while reading history')
        return histories

    @staticmethod
    def _artifact(dest: Path, ledger: Path, name: str) -> Path:
        relative = Path(name)
        if (not name or relative.is_absolute() or '\\' in name or ':' in name
                or any(part in {'', '.', '..'} for part in name.split('/'))):
            raise ck.CheckpointBlocked('unsafe artifact name in privacy history')
        return ledger.parent / relative

    def _capture_affinity(self, dest: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Capture immutable artifact lineage before any restored Signal can be erased."""
        entries = manifest['entries']
        known = {entry['path'] for entry in entries if not ck._privacy_ledger(entry)}
        original: dict[str, Any] = {}
        for entry in entries:
            if not ck._privacy_ledger(entry):
                continue
            ledger = dest / entry['path']
            sources: dict[tuple[str, ...], set[str]] = {}
            with self._database(dest, ledger, expected_sha256=entry['sha256']) as db:
                for row in db.execute('SELECT signal_id,connector,account_scope,upstream_id,content_hmac,'
                                      'evidence_blob_ref FROM signals'):
                    sid = row['signal_id']
                    paths = {f"blobs/{row['evidence_blob_ref']}.blob",
                             f"corpus/case_{sid.removeprefix('sig_')[:16]}.ae1"}
                    paths.update(f"corpus/{case[0]}.ae1" for case in db.execute(
                        'SELECT case_id FROM corpus_cases WHERE signal_id=?', (sid,)))
                    paths.update(f"replay/{case[0]}.ae1" for case in db.execute(
                        'SELECT feedback_id FROM replay_cases WHERE signal_id=?', (sid,)))
                    artifacts = {self._artifact(dest, ledger, path).relative_to(dest).as_posix() for path in paths}
                    prefix = (ledger.parent.relative_to(dest) / 'briefings').as_posix() + '/'
                    for briefing in db.execute('SELECT DISTINCT catchup_id FROM briefing_signals WHERE signal_id=?',
                                               (sid,)):
                        identity = briefing[0]
                        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identity):
                            raise ck.CheckpointBlocked('invalid original recovery archive identity')
                        pattern = re.compile(r'.*-' + re.escape(identity) + r'\.v[^/]*\.ae1')
                        artifacts.update(name for name in known if name.startswith(prefix)
                                         and pattern.fullmatch(name[len(prefix):]))
                    identity = tuple(row[field] for field in (
                        'connector', 'account_scope', 'upstream_id', 'content_hmac'))
                    sources.setdefault(identity, set()).update(artifacts & known)
            original[entry['path']] = {'sha256': entry['sha256'], 'sources': [
                dict(zip(('connector', 'account_scope', 'upstream_id', 'content_hmac'), identity, strict=True),
                     artifacts=sorted(paths)) for identity, paths in sorted(sources.items()) if paths]}
        return {'schema': 'artha.recovery-affinity.v1', 'ledgers': original}

    @staticmethod
    def _affinity(catalog: Mapping[str, Any], dest: Path, ledgers: list[Path]) -> dict[str, Any]:
        """Only original capture provenance is admissible; never backfill after privacy purge."""
        value = catalog.get('recovery_affinity')
        expected = {ledger.relative_to(dest).as_posix() for ledger in ledgers}
        if (not isinstance(value, dict) or set(value) != {'schema', 'ledgers'}
                or value['schema'] != 'artha.recovery-affinity.v1' or not isinstance(value['ledgers'], dict)
                or set(value['ledgers']) != expected):
            raise ck.CheckpointBlocked(
                'original recovery provenance is missing or invalid; restore original checkpoint')
        known = {entry['path'] for entry in catalog['entries']}
        for record in value['ledgers'].values():
            if (not isinstance(record, dict) or set(record) != {'sha256', 'sources'}
                    or not isinstance(record['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', record['sha256'])
                    or not isinstance(record['sources'], list)):
                raise ck.CheckpointBlocked('invalid original recovery ledger provenance')
            identities = set()
            for source in record['sources']:
                fields = ('connector', 'account_scope', 'upstream_id', 'content_hmac')
                if (not isinstance(source, dict) or set(source) != {*fields, 'artifacts'}
                        or any(not isinstance(source[field], str) or not source[field] for field in fields)
                        or not isinstance(source['artifacts'], list)
                        or any(not isinstance(path, str) or path not in known for path in source['artifacts'])
                        or len(source['artifacts']) != len(set(source['artifacts']))):
                    raise ck.CheckpointBlocked('invalid original recovery source provenance')
                identity = tuple(source[field] for field in fields)
                if identity in identities:
                    raise ck.CheckpointBlocked('duplicate original recovery source provenance')
                identities.add(identity)
        return value

    @staticmethod
    def _denied_affinity(affinity: Mapping[str, Any], histories: Mapping[str, Any],
                         known: set[str]) -> set[str]:
        denied: set[str] = set()
        for name, record in affinity['ledgers'].items():
            mode = RecoveryAccess._mode(Path(name))
            for tombstone in histories[mode]['tombstones']:
                upstream = tombstone.get('upstream_id')
                if not isinstance(upstream, str) or not upstream:
                    continue
                artifact = None
                if tombstone.get('connector') == 'documents':
                    artifact = f"documents/{upstream.split(':', 1)[0]}.ae1"
                elif tombstone.get('connector') == 'capture':
                    artifact = f'inbox/{upstream}.ae1'
                elif (tombstone.get('connector'), tombstone.get('account_scope')) == ('retention', 'replay'):
                    artifact = f'replay/{upstream}.ae1'
                if artifact is not None:
                    relative = RecoveryAccess._artifact(Path('.'), Path(name), artifact).as_posix()
                    if relative in known:
                        denied.add(relative)
            for source in record['sources']:
                for tombstone in histories[mode]['tombstones']:
                    if ((tombstone.get('connector'), tombstone.get('account_scope'), tombstone.get('upstream_id'))
                            == (source['connector'], source['account_scope'], source['upstream_id'])
                            or (tombstone.get('content_hmac') is not None
                                and tombstone['content_hmac'] == source['content_hmac'])):
                        denied.update(source['artifacts'])
                        break
        return denied

    def replay(self, dest: Path) -> None:
        ledgers = self._ledgers(dest)
        modes = {self._mode(ledger) for ledger in ledgers}
        pinned, _ = self._base(modes)
        histories = self._history(modes, pinned['privacy'])
        guard = self._guard(dest, ledgers, pinned, restored=False)
        store = QuarantineStore(dest / '.artha-quarantine', self.cipher, authority=guard)
        catalog = store.load_catalog()
        if catalog['profile'] != 'personal':
            raise ck.CheckpointBlocked('only personal recovery quarantine is authorized')
        affinity = self._affinity(catalog, dest, ledgers)
        known = {entry['path'] for entry in catalog['entries']}
        denied = self._denied_affinity(affinity, histories, known)
        if denied:
            store.block_paths(sorted(denied), 'current erasure')
        for ledger in ledgers:
            with self._database(dest, ledger, update=True) as db:
                blocked: set[str] = set()

                def remove(name: str, ledger: Path = ledger, blocked: set[str] = blocked) -> None:
                    path = self._artifact(dest, ledger, name)
                    relative = path.relative_to(dest).as_posix()
                    if relative in known:
                        blocked.add(relative)
                    if os.path.lexists(path):
                        self._safe(dest, path)
                        path.unlink()

                def purge(catchup_ids: list[str], ledger: Path = ledger, blocked: set[str] = blocked,
                          db: sqlite3.Connection = db, remove: Callable[[str], None] = remove) -> None:
                    prefix = (ledger.parent.relative_to(dest) / 'briefings').as_posix() + '/'
                    for catchup_id in catchup_ids:
                        if not re.fullmatch(r'[A-Za-z0-9_-]+', catchup_id):
                            raise ck.CheckpointBlocked('invalid archive identity in privacy history')
                        pattern = re.compile(r'.*-' + re.escape(catchup_id) + r'\.v[^/]*\.ae1')
                        blocked.update(name for name in known if name.startswith(prefix)
                                       and pattern.fullmatch(name[len(prefix):]))
                        for archive in (ledger.parent / 'briefings').glob(f'*-{catchup_id}.v*.ae1'):
                            remove(archive.relative_to(ledger.parent).as_posix())
                        db.execute("UPDATE briefings SET status='superseded' WHERE catchup_id=?", (catchup_id,))

                head = pinned['privacy'][self._mode(ledger)]
                apply_privacy_history(db, remove, SealedCheckpoint(**head), histories[self._mode(ledger)], purge)
                self._checkpoint(db, head)
                if blocked:
                    store.block_paths(sorted(blocked), 'current erasure')
                guard()
            guard()
        self._guard(dest, ledgers, pinned, restored=True)()

    def _export(self, db: sqlite3.Connection, config: runtime.RuntimeConfig, history: Mapping[str, Any]) -> None:
        # Checkpoint numbers bind erasures/revocations, not the full content of a grant.
        # Historical or modified zero-revocation rows cannot supply current permission.
        trusted = {entry['consent_id']: entry for entry in history['consent']}
        restored = {row['consent_id']: dict(row) for row in db.execute('SELECT * FROM consent')}
        if restored != trusted:
            raise ck.CheckpointBlocked('recovery consent differs from independently current trusted history')
        historical = {row[0] for row in db.execute('SELECT DISTINCT subject_id FROM consent')}
        observed = historical | {row[0] for row in db.execute('SELECT primary_subject_id FROM facts')}
        observed.update(row[0] for row in db.execute('SELECT subject_id FROM fact_subjects'))
        known = frozenset(config.subjects)
        if not observed <= known:
            raise ck.CheckpointBlocked('unknown household subjects prevent recovery export')
        protected = (config.consent_required | historical) - {config.viewer}
        access = AccessPolicy(config.viewer, frozenset(protected), known)
        for subject in sorted(protected):
            if not all(consent_active(db, access, subject, operation, config.viewer, 'recovery',
                                      self.now().isoformat()) for operation in ('view', 'export')):
                raise ck.CheckpointBlocked('each protected subject must grant current recovery view and export')

    def open_store(self, dest: Path, *, export: bool = False) -> QuarantineStore:
        """Metadata access grants no content; export consent does not replace per-file review."""
        self.replay(dest)
        ledgers = self._ledgers(dest)
        pinned, _ = self._base({self._mode(ledger) for ledger in ledgers})
        guard = self._guard(dest, ledgers, pinned, restored=True, export=export)
        guard()
        root = dest / '.artha-quarantine'
        catalog = QuarantineStore(root, self.cipher, authority=guard).load_catalog()
        affinity = self._affinity(catalog, dest, ledgers)
        if export:
            histories = self._history(set(pinned['privacy']), pinned['privacy'])
            known = {entry['path'] for entry in catalog['entries']}
            store: QuarantineStore = _ContentStore(root, self.cipher, authority=guard, affinity=affinity,
                                                   denied=self._denied_affinity(affinity, histories, known))
        else:
            store = _MetadataStore(root, self.cipher, authority=guard)
        store.load_catalog()
        return store


class _WorkCaptureStore(QuarantineStore):
    def __init__(self, *args: Any, binding: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._binding = dict(binding)

    def seal(self) -> dict[str, Any]:
        self.set_recovery_affinity({
            'schema': 'artha.recovery-affinity.v1',
            'work_binding': self._binding,
        })
        return super().seal()


class _WorkContentStore(QuarantineStore):
    def __init__(self, *args: Any, binding: Mapping[str, Any], denied: set[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._binding = dict(binding)
        self._denied = frozenset(denied)

    def iter_member(self, member_id: str) -> Iterator[bytes]:
        catalog = self.load_catalog()
        if catalog.get('recovery_affinity') != {
                'schema': 'artha.recovery-affinity.v1', 'work_binding': self._binding}:
            raise QuarantineError('original Work checkpoint provenance changed')
        member = next((entry for entry in catalog['entries'] if entry['member_id'] == member_id), None)
        if member is None or member['path'] in self._denied:
            raise QuarantineError('current Work history forbids this historical member')
        yield from super().iter_member(member_id)


class WorkRecoveryAccess:
    """Same-installation Work authority for encrypted quarantine and per-file review."""

    def __init__(self, repo: Path, protocol: Any, cipher: EnvelopeCipher, *, actor: str | None = None):
        self.repo = repo.absolute()
        self.protocol = protocol
        self.cipher = cipher
        self.requested_actor = actor
        self.actor: str | None = None
        self.binding: dict[str, Any] | None = None
        self.pinned: dict[str, Any] | None = None
        self.denied: set[str] = set()
        self.checkpoint_id: str | None = None
        self.checkpoint_sha256: str | None = None
        self.entries: dict[str, dict[str, Any]] | None = None
        self.capture_store: QuarantineStore | None = None

    @staticmethod
    def _digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).hexdigest()

    def _resolve_actor(self) -> str:
        try:
            identity = dict(self.protocol.native_identity())
        except (WorkPrivacyBlocked, QuarantineFilesystemError, KeyUnavailableError,
                SessionBusy, KeyringError, OSError) as exc:
            raise ck.CheckpointBlocked('current native Work user identity is unavailable') from exc
        actor = identity.get('user')
        if (identity.get('platform') != 'win32' or not isinstance(actor, str)
                or not re.fullmatch(r'S-[0-9]+(?:-[0-9]+)+', actor)
                or (self.requested_actor is not None and self.requested_actor != actor)):
            raise ck.CheckpointBlocked('review actor must match the current Windows user')
        return actor

    def _destination(self, dest: Path) -> None:
        sync_roots = [Path(value).expanduser() for name in ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')
                      if (value := os.environ.get(name))]
        sync_roots.extend(parent for parent in self.repo.resolve().parents
                          if parent.name.casefold().startswith('onedrive') or parent.name == 'CloudStorage')
        if not ck._outside(dest, [self.repo, self.protocol.local, default_installation_dir(), *sync_roots]):
            raise ck.CheckpointBlocked('Work recovery destination overlaps personal or synchronized storage')

    def _same_head(self, current: Mapping[str, Any]) -> bool:
        return (self.pinned is not None and current.get('identity') == self.pinned.get('identity')
                and current.get('head') == self.pinned.get('head')
                and current.get('generation') == self.pinned.get('generation'))

    def _pin(self, current: dict[str, Any]) -> None:
        if self.pinned is not None and not self._same_head(current):
            raise ck.CheckpointBlocked('current Work privacy head changed during recovery')
        self.pinned = current

    def _authority(self) -> dict[str, Any]:
        if self.pinned is None or self.actor is None:
            raise ck.CheckpointBlocked('same-host Work recovery authority is required')
        try:
            current = self.protocol.current()
        except (WorkPrivacyBlocked, QuarantineFilesystemError, KeyUnavailableError,
                SessionBusy, KeyringError, OSError) as exc:
            raise ck.CheckpointBlocked('current Work privacy history is unavailable') from exc
        if not self._same_head(current):
            raise ck.CheckpointBlocked('current Work privacy head changed during recovery')
        return {
            'actor': self.actor,
            'owner': self.actor,
            'profile': 'work',
            'work': {
                'identity': current['identity'],
                'generation': current['generation'],
                'head_sha256': self._digest(current['head']),
            },
        }

    @staticmethod
    def _manifest_binding(manifest: Mapping[str, Any]) -> dict[str, Any]:
        context = manifest.get('context')
        binding = context.get('work_binding') if isinstance(context, Mapping) else None
        if not isinstance(binding, dict):
            raise ck.CheckpointBlocked('unbound Work checkpoints are inspection-only')
        return binding

    @staticmethod
    def _member_entries(entries: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(entry['path']): dict(entry) for entry in entries}

    @staticmethod
    def _work_paths(entries: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        return {name: entry for name, entry in entries.items()
                if name.startswith(('work_state/', 'work_local/'))}

    def _validate_member_binding(self, binding: Mapping[str, Any],
                                 entries: Mapping[str, Mapping[str, Any]]) -> set[str]:
        members = binding.get('members')
        if not isinstance(members, Mapping):
            raise ck.CheckpointBlocked('Work checkpoint member binding is invalid')
        checkpoint_members = self._work_paths(entries)
        if set(checkpoint_members) != set(members):
            raise ck.CheckpointBlocked('Work checkpoint differs from its journaled member inventory')
        for name, member in members.items():
            entry = checkpoint_members[name]
            if (not isinstance(member, Mapping) or entry.get('kind') != 'file'
                    or entry.get('size') != member.get('size')
                    or entry.get('source_sha256', entry.get('sha256')) != member.get('sha256')):
                raise ck.CheckpointBlocked('Work checkpoint member differs from its journaled bytes')
        external = set(entries) - set(members)
        external.update(name for name in checkpoint_members if name.startswith('work_local/cache/'))
        return external

    def authorize_checkpoint(self, manifest: Mapping[str, Any]) -> None:
        if manifest.get('profile') != 'work':
            raise ck.CheckpointBlocked('Work recovery requires a Work-profile checkpoint')
        binding = self._manifest_binding(manifest)
        entries = self._member_entries(manifest.get('entries', []))
        external = self._validate_member_binding(binding, entries)
        try:
            current = self.protocol.check_checkpoint(binding)
            denied = self.protocol.denied_members(binding, list(binding['members']))
            confirmed = self.protocol.current()
        except (WorkPrivacyBlocked, QuarantineFilesystemError, KeyUnavailableError,
                SessionBusy, KeyringError, OSError) as exc:
            raise ck.CheckpointBlocked('original Work identity and current member history are required') from exc
        if current.get('head') != confirmed.get('head'):
            raise ck.CheckpointBlocked('current Work privacy head changed during recovery authorization')
        self._pin(confirmed)
        self.actor = self._resolve_actor()
        self.binding = binding
        self.denied = set(denied) | external
        self.checkpoint_id = str(manifest.get('generation', ''))
        self.entries = entries

    def factory(self, dest: Path, manifest: Mapping[str, Any], checkpoint_sha256: str) -> QuarantineStore:
        self._destination(dest)
        binding = self._manifest_binding(manifest)
        if (self.binding != binding or self.checkpoint_id != manifest.get('generation')
                or self.pinned is None or self.actor is None):
            raise ck.CheckpointBlocked('Work checkpoint authority was not verified before extraction')
        entries = self._member_entries(manifest.get('entries', []))
        if self.entries != entries:
            raise ck.CheckpointBlocked('Work checkpoint changed after authority verification')
        self._validate_member_binding(binding, entries)
        self._authority()
        store = _WorkCaptureStore(
            dest / '.artha-quarantine', self.cipher, authority=self._authority, binding=binding,
        )
        store.begin(dict(manifest), checkpoint_sha256)
        self.capture_store = store
        self.checkpoint_sha256 = checkpoint_sha256
        return store

    def _catalog_binding(self, catalog: Mapping[str, Any]) -> dict[str, Any]:
        affinity = catalog.get('recovery_affinity')
        binding_value = affinity.get('work_binding') if isinstance(affinity, dict) else None
        if (not isinstance(affinity, dict) or set(affinity) != {'schema', 'work_binding'}
                or affinity.get('schema') != 'artha.recovery-affinity.v1'
                or not isinstance(binding_value, dict)):
            raise ck.CheckpointBlocked('Work quarantine lacks its authenticated checkpoint binding')
        binding: dict[str, Any] = {}
        for key, value in binding_value.items():
            if not isinstance(key, str):
                raise ck.CheckpointBlocked('Work quarantine binding fields are malformed')
            binding[key] = value
        return binding

    def _validate_catalog(self, catalog: Mapping[str, Any], binding: Mapping[str, Any]) -> set[str]:
        if (catalog.get('profile') != 'work' or catalog.get('checkpoint_id') != self.checkpoint_id
                or (self.checkpoint_sha256 is not None
                    and catalog.get('checkpoint_sha256') != self.checkpoint_sha256)):
            raise ck.CheckpointBlocked('Work quarantine checkpoint identity changed')
        entries = {entry['path']: entry for entry in catalog.get('entries', [])}
        external = self._validate_member_binding(binding, entries)
        if self.entries is not None:
            expected = {name: {key: value for key, value in entry.items()
                               if key not in {'member_id', 'state', 'chunks', 'reason'}}
                        for name, entry in self.entries.items()}
            actual = {name: {key: value for key, value in entry.items()
                             if key not in {'member_id', 'state', 'chunks', 'reason'}}
                      for name, entry in entries.items()}
            if actual != expected:
                raise ck.CheckpointBlocked('Work quarantine inventory differs from the verified checkpoint')
        return external

    def replay(self, dest: Path) -> None:
        if self.capture_store is None or self.binding is None:
            raise ck.CheckpointBlocked('Work quarantine capture is unavailable')
        self._authority()
        catalog = self.capture_store.load_catalog()
        external = self._validate_catalog(catalog, self.binding)
        denied = self.denied | external
        if denied:
            self._block_denied(self.capture_store, catalog, denied)
        self._authority()
        self._check_fence(dest, self.capture_store, catalog)

    @staticmethod
    def _block_denied(store: QuarantineStore, catalog: Mapping[str, Any], denied: set[str]) -> None:
        needs_blocking = {entry['path'] for entry in catalog.get('entries', [])
                          if entry.get('path') in denied and entry.get('state') != 'blocked'}
        if needs_blocking:
            store.block_paths(sorted(needs_blocking),
                              'current Work invalidation or unjournaled dependency')

    def _check_fence(self, dest: Path, store: QuarantineStore, catalog: Mapping[str, Any]) -> None:  # noqa: PLR0912
        self._destination(dest)
        marker = dest / '.artha-restored.json'
        try:
            root_info = dest.lstat()
            if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
                    or getattr(root_info, 'st_file_attributes', 0) & 0x400):
                raise ck.CheckpointBlocked('restored Work destination is unsafe')
            if ck._object(marker.read_bytes()) != ck._restore_marker(str(catalog['checkpoint_id'])):
                raise ck.CheckpointBlocked('restored Work installation fence changed')
            for current, directories, files in os.walk(dest, followlinks=False):
                for name in [*directories, *files]:
                    path = Path(current) / name
                    info = path.lstat()
                    relative = path.relative_to(dest).as_posix()
                    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                            or (not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode))
                            or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                        raise ck.CheckpointBlocked('restored Work destination contains unsafe files')
                    if stat.S_ISDIR(info.st_mode):
                        if relative not in {'.artha-quarantine', 'reviewed-exports'}:
                            raise ck.CheckpointBlocked('restored Work destination contains an unexpected directory')
                    elif (relative in {'.artha-restored.json', '.quarantine-clean-file-review.lock'}
                          or path.is_relative_to(store.root)):
                        continue
                    elif path.parent == dest / 'reviewed-exports' and path.suffix == '.ae1':
                        if not re.fullmatch(
                                r'[a-z0-9][a-z0-9._-]{0,119}\.(?:md|txt|pdf|csv)\.ae1', path.name):
                            raise ck.CheckpointBlocked('reviewed Work export name is invalid')
                        if os.name == 'nt':
                            with WindowsQuarantineDirectory.open(path.parent) as exports, \
                                    exports.open_file(path.name) as handle:
                                before = handle.snapshot()
                                encrypted = (0 < before.size <= 32 * 1024 * 1024
                                             and handle.read(before.size).startswith(b'AE1'))
                                if (not encrypted or handle.snapshot() != before
                                        or handle.check_path(expected_identity=before.identity) != before.identity):
                                    raise ck.CheckpointBlocked('reviewed Work export is not safe encrypted data')
                        else:
                            if info.st_size > 32 * 1024 * 1024:
                                raise ck.CheckpointBlocked('reviewed Work export is not encrypted')
                            with path.open('rb') as handle:
                                if handle.read(3) != b'AE1':
                                    raise ck.CheckpointBlocked('reviewed Work export is not encrypted')
                    else:
                        raise ck.CheckpointBlocked('restored Work destination contains plaintext members')
            store.verify()
        except QuarantineFilesystemError:
            raise ck.CheckpointBlocked('restored Work quarantine contains an unsafe reviewed export') from None
        except OSError:
            raise ck.CheckpointBlocked('restored Work installation fence is unavailable') from None

    def open_store(self, dest: Path, *, export: bool = False) -> QuarantineStore:
        self._destination(dest)
        try:
            current = self.protocol.current()
        except (WorkPrivacyBlocked, QuarantineFilesystemError, KeyUnavailableError,
                SessionBusy, KeyringError, OSError) as exc:
            raise ck.CheckpointBlocked('current Work privacy history is unavailable') from exc
        self._pin(current)
        self.actor = self._resolve_actor()
        root = dest / '.artha-quarantine'
        store = QuarantineStore(root, self.cipher, authority=self._authority)
        catalog = store.load_catalog()
        if catalog.get('profile') != 'work':
            raise ck.CheckpointBlocked('only Work recovery quarantine is authorized')
        self.checkpoint_id = str(catalog.get('checkpoint_id', ''))
        self.checkpoint_sha256 = str(catalog.get('checkpoint_sha256', ''))
        binding = self._catalog_binding(catalog)
        external = self._validate_catalog(catalog, binding)
        try:
            checked = self.protocol.check_checkpoint(binding)
            denied = self.protocol.denied_members(binding, list(binding['members']))
            confirmed = self.protocol.current()
        except (WorkPrivacyBlocked, QuarantineFilesystemError, KeyUnavailableError,
                SessionBusy, KeyringError, OSError) as exc:
            raise ck.CheckpointBlocked('original Work identity and current member history are required') from exc
        if checked.get('head') != confirmed.get('head') or not self._same_head(confirmed):
            raise ck.CheckpointBlocked('current Work privacy head changed during quarantine access')
        self.binding = binding
        self.denied = set(denied) | external
        self.entries = {entry['path']: {key: value for key, value in entry.items()
                                        if key not in {'member_id', 'state', 'chunks', 'reason'}}
                        for entry in catalog['entries']}
        if self.denied:
            self._block_denied(store, catalog, self.denied)
        self._check_fence(dest, store, store.load_catalog())
        if export:
            result: QuarantineStore = _WorkContentStore(
                root, self.cipher, authority=self._authority, binding=binding, denied=self.denied,
            )
        else:
            result = _MetadataStore(root, self.cipher, authority=self._authority)
        result.load_catalog()
        return result
