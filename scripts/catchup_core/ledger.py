"""Platform-local transactional ledger (re-artha §7, Appendix A, WP-05).

Owns Signals, Facts (with append-only versions), Alerts, Open Items, consent,
tombstones, the deletion journal and the stage journal.  Properties enforced here:

* every connection: ``foreign_keys=ON``, WAL, ``synchronous=FULL``;
* migrations are forward-only, numbered, transactional, and **refuse to run
  without a verified backup** of an existing database;
* restricted values are stored only as authenticated ciphertext; a missing key
  raises — there is no plaintext fallback;
* a forgotten source cannot come back: fetch, import and restore all consult the
  tombstones, and a restore older than the sealed deletion checkpoint stays
  locked until the missing tombstones are replayed.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from catchup_core.fact_access import FactMetadata

from catchup_core import ids
from catchup_core.contracts import (
    Confidentiality,
    CoverageStatus,
    FactStatus,
    ProcessingState,
    Provenance,
    StageCheckpoint,
)
from catchup_core.crypto import EnvelopeCipher
from catchup_core.privacy_history import PrivacyCapsules

__all__ = [
    "APPROVED_SOURCE_ENROLLMENT_NAMES",
    "FactInput",
    "FactView",
    "FileSealedStore",
    "ForgetReceipt",
    "Ledger",
    "LedgerError",
    "LedgerLocked",
    "MemorySealedStore",
    "MigrationRefused",
    "PermissionDenied",
    "PersonalScopeUnavailable",
    "SealedCheckpoint",
    "SealedStore",
    "SignalInput",
    "Suppressed",
    "require_approved_source_enrollment",
]

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
APPROVED_SOURCE_ENROLLMENT_NAMES = frozenset({"home_email"})


class LedgerError(RuntimeError):
    pass


class MigrationRefused(LedgerError):
    """A migration needs a verified backup and none was provided or it failed."""


class PermissionDenied(LedgerError):
    pass


class PersonalScopeUnavailable(PermissionDenied):
    """The trusted scope needed for this operation is not available."""


class Suppressed(LedgerError):
    """The input was forgotten; it must not be re-ingested."""


class LedgerLocked(LedgerError):
    """A restored ledger is older than the sealed deletion checkpoint."""


def require_approved_source_enrollment(source_name: str) -> None:
    if not isinstance(source_name, str) or source_name not in APPROVED_SOURCE_ENROLLMENT_NAMES:
        raise PermissionDenied(
            f"source enrollment is approved only for source_name 'home_email'; got {source_name!r}")


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _enable_wal(conn: sqlite3.Connection) -> None:
    """SQLite may return BUSY immediately when two connections convert the same rollback journal."""
    conn.execute('PRAGMA busy_timeout=50')  # Short attempts keep the retry budget responsive under contention.
    deadline = time.monotonic() + 5
    while True:
        try:
            row = conn.execute('PRAGMA journal_mode=WAL').fetchone()
            if row is None or row[0] != 'wal':
                raise LedgerError('ledger requires WAL journal mode')
            conn.execute('PRAGMA busy_timeout=5000')
            return
        except sqlite3.OperationalError as exc:
            remaining = deadline - time.monotonic()
            code = getattr(exc, 'sqlite_errorcode', 0) & 0xFF
            if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or remaining <= 0:
                raise
            time.sleep(min(0.05, remaining))


def _after(previous: str | None, candidate: str) -> str:
    """A version timestamp strictly later than the fact's previous one (history keys on time)."""
    current = _instant(candidate)
    if previous is None or current > _instant(previous):
        return candidate
    bumped = _instant(previous) + timedelta(microseconds=1)
    return bumped.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _instant(stamp: str) -> datetime:
    """Compare instants without rewriting persisted version IDs or losing microseconds."""
    try:
        value = datetime.fromisoformat(stamp)
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC)
    except ValueError:
        pass
    raise ValueError('timestamp needs a valid explicit UTC offset')


RETENTION_CONNECTOR = "retention"      # tombstone namespace for retention expiry of non-source artifacts
RETENTION_REPLAY_SCOPE = "replay"
_COVERAGE_HOLD_LIMIT = 1000
_COVERAGE_HOLD_CAP_SPOT = "coverage_hold_limit"

# ── sealed deletion checkpoint (outside the restorable set) ───────────────────

@dataclass(frozen=True)
class SealedCheckpoint:
    checkpoint_id: int
    tombstone_digest: str
    permission_generation: int


class SealedStore(Protocol):
    def read(self) -> SealedCheckpoint | None: ...

    def write(self, checkpoint: SealedCheckpoint) -> None: ...


class MemorySealedStore:
    def __init__(self) -> None:
        self._value: SealedCheckpoint | None = None

    def read(self) -> SealedCheckpoint | None:
        return self._value

    def write(self, checkpoint: SealedCheckpoint) -> None:
        self._value = checkpoint


class FileSealedStore:
    """Sealed record in a directory that is not part of any snapshot or sync root."""

    def __init__(self, path: Path):
        self._path = Path(path)

    def read(self) -> SealedCheckpoint | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return SealedCheckpoint(int(data["checkpoint_id"]), str(data["tombstone_digest"]),
                                    int(data["permission_generation"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LedgerLocked("sealed deletion checkpoint is unreadable; failing safe") from exc

    def write(self, checkpoint: SealedCheckpoint) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"checkpoint_id": checkpoint.checkpoint_id,
                       "tombstone_digest": checkpoint.tombstone_digest,
                       "permission_generation": checkpoint.permission_generation}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)


class KeyringSealedStore:
    """Bounded OS trust anchor; encrypted replay history is outside runtime snapshots."""

    def __init__(self, backend: Any, namespace: str, *, cipher: EnvelopeCipher | None = None,
                 history_root: Path | None = None):
        self._backend = backend
        self._service = f"artha-privacy-{namespace}"
        self._cipher = cipher
        self._history_root = history_root or Path.home() / '.artha-local' / 'privacy'
        self.history: Callable[[], dict[str, Any]] = lambda: {}

    def _record(self) -> dict[str, Any] | None:
        from catchup_core.privacy_history import ANCHOR_SCHEMA, capsule_digest, checkpoint_data  # noqa: PLC0415
        raw = self._backend.get_password(self._service, 'checkpoint')
        if raw is None:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema') not in (None, ANCHOR_SCHEMA):
            raise ValueError('unsupported privacy anchor')
        checkpoint_data(data)
        if data.get('schema') == ANCHOR_SCHEMA:
            capsule_digest(data)
        return data

    def _capsules(self) -> PrivacyCapsules:
        if self._cipher is None:
            raise LedgerLocked('privacy replay encryption key is unavailable')
        directory = self._history_root / hashlib.sha256(self._service.encode()).hexdigest()
        return PrivacyCapsules(directory, self._service, self._cipher)

    def _replay(self, record: dict[str, Any]) -> dict[str, Any]:
        from catchup_core.privacy_history import ANCHOR_SCHEMA, checked_history, checkpoint_data  # noqa: PLC0415
        if record.get('schema') == ANCHOR_SCHEMA:
            return dict(self._capsules().load(record))
        return checked_history(record.get('history', {}), checkpoint_data(record))

    def read(self) -> SealedCheckpoint | None:
        try:
            data = self._record()
            return (SealedCheckpoint(data['checkpoint_id'], data['tombstone_digest'], data['permission_generation'])
                    if data is not None else None)
        except Exception as exc:
            raise LedgerLocked('credential-store privacy checkpoint is unavailable') from exc

    def write(self, checkpoint: SealedCheckpoint) -> None:
        from catchup_core.privacy_history import (  # noqa: PLC0415
            ANCHOR_SCHEMA,
            checked_history,
            checkpoint_data,
            require_continuity,
        )
        from catchup_core.recovery_registry import has_pending, mutation  # noqa: PLC0415
        from catchup_core.session_lock import installation_fence, session_lock  # noqa: PLC0415
        lock = self._history_root / 'locks' / (hashlib.sha256(self._service.encode()).hexdigest() + '.lock')
        local_root = Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
        try:
            with installation_fence(local_root), session_lock(lock):
                incoming = checkpoint_data({'checkpoint_id': checkpoint.checkpoint_id,
                    'tombstone_digest': checkpoint.tombstone_digest,
                    'permission_generation': checkpoint.permission_generation})
                history = checked_history(self.history(), incoming)
                prior = self._record()
                if prior is not None:
                    prior_checkpoint = checkpoint_data(prior)
                    if (incoming['checkpoint_id'] < prior_checkpoint['checkpoint_id']
                            or incoming['permission_generation'] < prior_checkpoint['permission_generation']
                            or (incoming['checkpoint_id'] == prior_checkpoint['checkpoint_id']
                                and incoming != prior_checkpoint)):
                        raise ValueError('privacy history cannot move backwards or reinterpret a checkpoint')
                    previous_history = self._replay(prior)
                    require_continuity(previous_history, history)
                    if incoming == prior_checkpoint:
                        if history != previous_history:
                            raise ValueError('privacy history changed without a new checkpoint')
                        if prior.get('schema') == ANCHOR_SCHEMA:
                            generation = str(checkpoint.checkpoint_id)
                            if has_pending('personal', 'privacy', self._service, 'checkpoint', generation):
                                with mutation('personal', 'privacy', self._service, 'checkpoint', generation):
                                    if self._backend.get_password(self._service, 'checkpoint') != json.dumps(
                                            prior, sort_keys=True):
                                        raise ValueError('privacy checkpoint changed before acknowledgement')
                            return
                capsules = self._capsules()
                record = capsules.save(incoming, history)
                if prior is not None and prior.get('schema') == ANCHOR_SCHEMA:
                    record['previous_capsule_sha256'] = prior['capsule_sha256']
                text = json.dumps(record, sort_keys=True)
                with mutation('personal', 'privacy', self._service, 'checkpoint', str(checkpoint.checkpoint_id)):
                    self._backend.set_password(self._service, 'checkpoint', text)
                    if self._backend.get_password(self._service, 'checkpoint') != text:
                        raise ValueError('privacy checkpoint was not durably saved')
                capsules.prune(record, prior)
        except Exception as exc:
            raise LedgerLocked('privacy checkpoint publication could not be verified') from exc

    def replay(self) -> dict[str, Any]:
        try:
            record = self._record()
            if record is None:
                raise ValueError('trusted privacy history is missing')
            return self._replay(record)
        except Exception as exc:
            raise LedgerLocked('trusted privacy replay history is unavailable') from exc


# ── inputs and views ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalInput:
    connector: str
    account_scope: str
    upstream_id: str
    upstream_version: str
    observed_at: str
    content_hmac: str
    evidence_blob_ref: str
    confidentiality: Confidentiality = Confidentiality.SENSITIVE
    occurred_at: str | None = None


@dataclass(frozen=True)
class FactInput:
    domain: str
    predicate: str
    value: Any
    primary_subject_id: str
    observed_at: str
    confidentiality: Confidentiality = Confidentiality.SENSITIVE
    provenance: Provenance = Provenance.VERIFIED_SOURCE
    status: FactStatus = FactStatus.CURRENT
    valid_from: str | None = None
    valid_to: str | None = None
    validity_unknown: bool = False
    occurred_at: str | None = None
    rule_version: str | None = None
    co_subjects: tuple[tuple[str, str], ...] = ()
    viewers: tuple[str, ...] = ()
    evidence: tuple[tuple[str, int, int], ...] = ()
    obligation_key: str = ""


@dataclass(frozen=True)
class FactView:
    fact_id: str
    domain: str
    predicate: str
    value: Any
    primary_subject_id: str
    confidentiality: str
    provenance_class: str
    status: str
    valid_from: str | None
    valid_to: str | None
    validity_unknown: bool
    observed_at: str
    recorded_at: str
    evidence: tuple[tuple[str, int, int], ...] = ()
    obligation_key: str = ""


@dataclass(frozen=True)
class ForgetReceipt:
    tombstone_id: str
    deleted_signals: int
    deleted_facts: int
    deleted_alerts: int
    minimal_audit: str
    backup_copies_pending_expiry: bool
    journal_seq: int
    blob_refs_deleted: tuple[str, ...] = field(default_factory=tuple)


# ── ledger ────────────────────────────────────────────────────────────────────

class Ledger:
    def __init__(
        self,
        path: Path | str,
        *,
        id_key: ids.HmacKey,
        cipher: EnvelopeCipher | None = None,
        sealed: SealedStore | None = None,
        owner_id: str = "owner",
        consent_subjects: frozenset[str] = frozenset(),
        known_subjects: frozenset[str] | None = None,
        clock: Callable[[], str] = _utc_now,
        blob_deleter: Callable[[str], None] | None = None,
        artifact_deleter: Callable[[Sequence[str]], None] | None = None,
    ):
        self._path = Path(path)
        self.path_identity = ids.content_hmac(id_key, str(self._path.resolve()))
        self._id_key = id_key
        self._cipher = cipher
        self._sealed = sealed or MemorySealedStore()
        self._owner = owner_id
        self.scope_withheld: Counter[str] = Counter()  # objects a collection read left out for scope
        # Adults other than the owner: restricted data about them needs their recorded consent
        # (RA-12 v0).  Minor children are governed by the household owner's authority.
        self._consent_subjects = frozenset(consent_subjects)
        self._known_subjects = known_subjects
        self._clock = clock
        self._blob_deleter = blob_deleter
        self._artifact_deleter = artifact_deleter
        self._replay_deleter: Callable[[Sequence[str]], None] | None = None
        self._conn: sqlite3.Connection | None = None
        self._read_only = False
        self.locked = False
        self._guard = ids.CollisionGuard()
        self._source_enrollment_expectations: dict[str, dict[str, str | None]] = {}
        if isinstance(self._sealed, KeyringSealedStore):
            self._sealed.history = self.privacy_history
            if self._sealed._cipher is None:
                self._sealed._cipher = cipher

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self, *, read_only: bool = False) -> Ledger:
        if not read_only:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(self._path.parent, 0o700)
        target = self._path.resolve().as_uri() + "?mode=ro" if read_only else str(self._path)
        conn = sqlite3.connect(target, uri=read_only, timeout=10.0, isolation_level=None)
        self._conn = conn
        self._read_only = read_only
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA busy_timeout=5000')
            conn.execute("PRAGMA foreign_keys=ON")
            if read_only:
                conn.execute("PRAGMA query_only=ON")
            else:
                _enable_wal(conn)
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA secure_delete=ON")
            from catchup_core.schema_history import JOURNAL_VERSION, sources, verify  # noqa: PLC0415
            current = self.schema_version()
            if current >= JOURNAL_VERSION or conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='schema_history'").fetchone():
                verify(conn, sources(self.available_migrations()), current)
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Ledger:
        return self.open() if self._conn is None else self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise LedgerError("ledger is not open")
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.conn.in_transaction:
            self._savepoint_counter = getattr(self, '_savepoint_counter', 0) + 1
            name = f'artha_nested_{self._savepoint_counter}'
            self.conn.execute(f'SAVEPOINT {name}')
            try:
                yield self.conn
            except BaseException:
                self.conn.execute(f'ROLLBACK TO {name}')
                self.conn.execute(f'RELEASE {name}')
                raise
            else:
                self.conn.execute(f'RELEASE {name}')
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _require_unlocked(self) -> None:
        if self.locked:
            raise LedgerLocked("ledger is older than the sealed deletion checkpoint; replay tombstones first")

    def record_capability(self, capability: str, operation: str, *, succeeded: bool = True) -> None:
        with self.transaction() as db:
            db.execute("INSERT INTO capability_evidence(capability,operation_id,state,recorded_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(capability,operation_id) DO UPDATE SET "
                       "state=excluded.state,recorded_at=excluded.recorded_at",
                       (capability, operation, "last succeeded" if succeeded else "degraded", self._clock()))

    def correction_notice(self, fact_id: str, reason: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO correction_notices SELECT catchup_id,?,?,? FROM briefing_facts "
                       "WHERE fact_id=?", (fact_id, self._clock(), reason, fact_id))

    # ── migrations ───────────────────────────────────────────────────────────
    def fact_generation(self) -> int:
        """A persisted mutation counter also detects Facts that were absent during composition."""
        return int(self.conn.execute('SELECT generation FROM fact_generation WHERE singleton=1').fetchone()[0])

    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def available_migrations() -> list[tuple[int, Path]]:
        found: list[tuple[int, Path]] = []
        for path in sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            found.append((int(path.name[:4]), path))
        return found

    def migrate(self, backup: Callable[[Path], Path] | None = None) -> int:
        """Apply pending migrations.  Existing data requires a verified backup first."""
        from catchup_core.schema_history import record, sources, verify  # noqa: PLC0415
        installed = sources(self.available_migrations())
        current = self.schema_version()
        verify(self.conn, installed, current)
        pending = [migration for migration in installed if migration.number > current]
        if not pending:
            return current
        has_data = current > 0 or bool(self.conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone())
        if has_data:
            if backup is None:
                raise MigrationRefused("migration of an existing ledger requires a backup callback")
            try:
                snapshot = backup(self._path)
            except Exception as exc:
                raise MigrationRefused(f"backup failed: {type(exc).__name__}") from exc
            if not Path(snapshot).is_file() or Path(snapshot).stat().st_size == 0:
                raise MigrationRefused("backup file missing or empty; not migrating")
        executed: set[int] = set()
        for migration in pending:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                observed = self.schema_version()
                if observed != current:
                    verify(self.conn, installed, observed)
                    current = observed
                if migration.number > current:
                    if migration.number != current + 1:
                        raise MigrationRefused('migration history advanced inconsistently; retry')
                    for statement in _split_statements(migration.script):
                        self.conn.execute(statement)
                    executed.add(migration.number)
                    record(self.conn, installed, migration.number, executed, self._clock())
                    self.conn.execute(f"PRAGMA user_version={migration.number}")
                    current = migration.number
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")
        return self.schema_version()

    # ── signals ──────────────────────────────────────────────────────────────
    def is_tombstoned(self, connector: str, account_scope: str, upstream_id: str,
                      content_hmac: str | None = None) -> bool:
        if upstream_id.startswith('calendar:'):
            try:
                calendar, native = json.loads(upstream_id[9:])
                if isinstance(calendar, str) and isinstance(native, str) and self.conn.execute(
                        "SELECT 1 FROM tombstones WHERE connector=? AND account_scope=? AND upstream_id=?",
                        (connector, account_scope, native)).fetchone():
                    return True  # A pre-scope erasure continues to deny the provider object after upgrade.
            except (ValueError, TypeError):
                pass
        row = self.conn.execute(
            "SELECT 1 FROM tombstones WHERE connector=? AND account_scope=? AND upstream_id=? LIMIT 1",
            (connector, account_scope, upstream_id)).fetchone()
        if row:
            return True
        if content_hmac:
            return bool(self.conn.execute(
                "SELECT 1 FROM tombstones WHERE content_hmac=? LIMIT 1", (content_hmac,)).fetchone())
        return False

    def upsert_signal(self, sig: SignalInput) -> tuple[str, bool]:
        """Insert a Signal version; a replay returns the existing ID.  Forgotten input raises."""
        self._require_unlocked()
        if self.is_tombstoned(sig.connector, sig.account_scope, sig.upstream_id, sig.content_hmac):
            raise Suppressed(f"{sig.connector}/{sig.upstream_id} was forgotten")
        signal_id = ids.signal_id(self._id_key, sig.connector, sig.account_scope, sig.upstream_id,
                                  sig.upstream_version)
        self._guard.check(signal_id, (sig.connector, sig.account_scope, sig.upstream_id, sig.upstream_version))
        with self.transaction() as c:
            row = c.execute("SELECT signal_id FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
            if row:
                return signal_id, False
            c.execute(
                "INSERT INTO signals(signal_id,connector,account_scope,upstream_id,upstream_version,"
                "occurred_at,observed_at,confidentiality,content_hmac,evidence_blob_ref,processing_state,span_unit)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,'utf8-byte')",
                (signal_id, sig.connector, sig.account_scope, sig.upstream_id, sig.upstream_version,
                 sig.occurred_at, sig.observed_at, sig.confidentiality.value, sig.content_hmac,
                 sig.evidence_blob_ref, ProcessingState.FETCHED.value))
        return signal_id, True

    @staticmethod
    def _scope_binding_proof(
        key: ids.HmacKey, object_type: str, object_id: str, object_version: str,
        source_id: str, account_id: str, content_hmac: str, scope: str,
        provenance: str, permission_generation: int,
    ) -> str:
        material = json.dumps({
            "object_type": object_type, "object_id": object_id, "object_version": object_version,
            "source_id": source_id, "account_id": account_id, "content_hmac": content_hmac,
            "scope": scope, "provenance": provenance, "permission_generation": permission_generation,
        }, sort_keys=True, separators=(",", ":"))
        return ids.content_hmac(key, material)

    def _record_personal_binding(
        self, db: sqlite3.Connection, object_type: str, object_id: str, object_version: str,
        source_id: str, account_id: str, content_hmac: str, *,
        provenance: str = "trusted_writer", scope: str = "personal",
    ) -> None:
        generation = self._permission_generation()
        proof = self._scope_binding_proof(
            self._id_key, object_type, object_id, object_version, source_id, account_id,
            content_hmac, scope, provenance, generation)
        db.execute(
            "INSERT INTO scope_bindings(object_type,object_id,object_version,source_id,account_id,"
            "content_hmac,scope,provenance,permission_generation,proof,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (object_type, object_id, object_version, source_id, account_id, content_hmac, scope,
             provenance, generation, proof, self._clock()))

    def _has_personal_binding(
        self, object_type: str, object_id: str, object_version: str,
        source_id: str, account_id: str, content_hmac: str,
    ) -> bool:
        generation = self._permission_generation()
        rows = self.conn.execute(
            "SELECT scope,provenance,permission_generation,proof,source_id,account_id,content_hmac "
            "FROM scope_bindings WHERE object_type=? AND object_id=? AND object_version=? "
            "AND permission_generation=?",
            (object_type, object_id, object_version, generation)).fetchall()
        if len(rows) != 1:
            return False
        row = rows[0]
        if (row["scope"] != "personal" or row["provenance"] not in ("source_review", "trusted_writer")
                or row["source_id"] != source_id or row["account_id"] != account_id
                or row["content_hmac"] != content_hmac
                or type(row["permission_generation"]) is not int):
            return False
        expected = self._scope_binding_proof(
            self._id_key, object_type, object_id, object_version, source_id, account_id,
            content_hmac, row["scope"], row["provenance"], generation)
        return hmac.compare_digest(str(row["proof"]), expected)

    # ── owner scope review (specs/refix-artha.md §B.1 A-1) ─────────────
    # A v2 proof also binds a digest of the rows that decide who may read the object, so a later change to
    # subjects, viewers, evidence or source access invalidates the review.

    _SCOPES = ("personal", "work", "mixed", "unknown")

    @staticmethod
    def _authz_inputs(db: sqlite3.Connection, object_type: str, object_id: str,
                      object_version: str) -> dict[str, Any]:
        if object_type == "signal":
            row = db.execute("SELECT connector,account_scope FROM signals WHERE signal_id=?",
                             (object_id,)).fetchone()
            if row is None:
                raise LedgerError("unknown object")
            access = db.execute("SELECT subjects FROM signal_access WHERE signal_id=?", (object_id,)).fetchone()
            source = db.execute("SELECT subjects FROM source_access WHERE connector=? AND account_scope=?",
                                (row["connector"], row["account_scope"])).fetchone()
            enrollment = db.execute(
                "SELECT enrollment_id FROM signal_source_enrollments WHERE signal_id=?",
                (object_id,)).fetchone()
            return {"subjects": sorted(json.loads(access[0])) if access else None,
                    "source_subjects": sorted(json.loads(source[0])) if source else None,
                    "enrollment_id": int(enrollment[0]) if enrollment else None}
        fact = db.execute("SELECT primary_subject_id,domain FROM facts WHERE fact_id=?", (object_id,)).fetchone()
        if fact is None or object_type not in ("fact", "fact_version"):
            raise LedgerError("unknown object")
        if object_type == "fact":
            evidence = db.execute("SELECT signal_id FROM fact_evidence WHERE fact_id=?", (object_id,))
        else:
            evidence = db.execute("SELECT signal_id FROM fact_version_evidence WHERE fact_id=? AND recorded_at=?",
                                  (object_id, object_version))
        return {"primary_subject": fact["primary_subject_id"], "domain": fact["domain"],
                "co_subjects": sorted([str(r[0]), str(r[1] or "")] for r in db.execute(
                    "SELECT subject_id,role FROM fact_subjects WHERE fact_id=?", (object_id,))),
                "viewers": sorted(str(r[0]) for r in db.execute(
                    "SELECT viewer_id FROM fact_viewers WHERE fact_id=?", (object_id,))),
                "evidence": sorted(str(r[0]) for r in evidence)}

    @staticmethod
    def _authz_digest(inputs: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _reviewed_proof(key: ids.HmacKey, object_type: str, object_id: str, object_version: str, source_id: str,
                        account_id: str, content_hmac: str, scope: str, permission_generation: int,
                        authz_digest: str) -> str:
        material = json.dumps({
            "v": 2, "object_type": object_type, "object_id": object_id, "object_version": object_version,
            "source_id": source_id, "account_id": account_id, "content_hmac": content_hmac, "scope": scope,
            "provenance": "source_review", "permission_generation": permission_generation, "authz": authz_digest,
        }, sort_keys=True, separators=(",", ":"))
        return ids.content_hmac(key, material)

    def _review_identity(self, db: sqlite3.Connection, object_type: str,
                         object_id: str) -> tuple[str, str, str, str]:
        """(version, source, account, content marker) of the object as currently stored."""
        if object_type == "signal":
            row = db.execute("SELECT connector,account_scope,upstream_id,upstream_version,content_hmac "
                             "FROM signals WHERE signal_id=?", (object_id,)).fetchone()
            if row is None or not self._signal_identity_matches(
                    object_id, row["connector"], row["account_scope"], row["upstream_id"], row["upstream_version"]):
                raise LedgerError("unknown or inconsistent Signal")
            return (str(row["upstream_version"]), str(row["connector"]), str(row["account_scope"]),
                    str(row["content_hmac"]))
        if object_type == "fact":
            row = db.execute("SELECT recorded_at,scope_hmac FROM facts WHERE fact_id=?", (object_id,)).fetchone()
            if row is None or not row["scope_hmac"]:
                raise LedgerError("unknown or unbound Fact")
            return str(row["recorded_at"]), "ledger", self._owner, str(row["scope_hmac"])
        raise ValueError("scope review covers Signals and Facts only")

    def _source_enrollment_current(
            self, db: sqlite3.Connection, enrollment_id: object, generation: int) -> bool:
        source = db.execute(
            "SELECT source_name FROM source_enrollments WHERE enrollment_id=?",
            (enrollment_id,)).fetchone()
        if source is None or str(source["source_name"]) not in APPROVED_SOURCE_ENROLLMENT_NAMES:
            return False
        latest = db.execute(
            "SELECT * FROM source_enrollments "
            "WHERE source_name=(SELECT source_name FROM source_enrollments WHERE enrollment_id=?) "
            "ORDER BY enrollment_id DESC LIMIT 1", (enrollment_id,)).fetchone()
        if (latest is None or latest["enrollment_id"] != enrollment_id
                or latest["action"] != "enroll"
                or latest["permission_generation"] != generation
                or latest["scope_epoch"] != self._scope_epoch()):
            return False
        enrollment_expectation = self._source_enrollment_expectations.get(
            str(source["source_name"]))
        return (
            enrollment_expectation is not None
            and enrollment_expectation["provider_account_id"] is not None
            and all(latest[key] == value for key, value in enrollment_expectation.items())
        )

    def _has_reviewed_binding(self, object_type: str, object_id: str, object_version: str, source_id: str,
                              account_id: str, content_hmac: str, *, generation: int | None = None,
                              db: sqlite3.Connection | None = None) -> bool:
        db = self.conn if db is None else db
        generation = self._permission_generation() if generation is None else generation
        rows = db.execute(
            "SELECT scope,provenance,proof,source_id,account_id,content_hmac FROM scope_bindings "
            "WHERE object_type=? AND object_id=? AND object_version=? AND permission_generation=?",
            (object_type, object_id, object_version, generation)).fetchall()
        if len(rows) != 1:
            return False
        row = rows[0]
        if (row["scope"] != "personal" or row["provenance"] != "source_review" or row["source_id"] != source_id
                or row["account_id"] != account_id or row["content_hmac"] != content_hmac):
            return False
        try:
            inputs = self._authz_inputs(db, object_type, object_id, object_version)
        except LedgerError:
            return False
        enrollment_id = inputs.get("enrollment_id") if object_type == "signal" else None
        if enrollment_id is not None and not self._source_enrollment_current(
                db, enrollment_id, generation):
            return False
        expected_proof = self._reviewed_proof(
            self._id_key, object_type, object_id, object_version, source_id,
            account_id, content_hmac, "personal", generation, self._authz_digest(inputs))
        return hmac.compare_digest(str(row["proof"]), expected_proof)

    def _signal_reviewed(self, db: sqlite3.Connection, signal_id: str) -> bool:
        row = db.execute("SELECT upstream_version,connector,account_scope,content_hmac FROM signals "
                         "WHERE signal_id=?", (signal_id,)).fetchone()
        return row is not None and self._has_reviewed_binding(
            "signal", signal_id, str(row["upstream_version"]), str(row["connector"]), str(row["account_scope"]),
            str(row["content_hmac"]), db=db)

    def _fact_reviewed(self, db: sqlite3.Connection, fact_id: str) -> bool:
        row = db.execute("SELECT recorded_at,scope_hmac FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        return row is not None and bool(row["scope_hmac"]) and self._has_reviewed_binding(
            "fact", fact_id, str(row["recorded_at"]), "ledger", self._owner, str(row["scope_hmac"]), db=db)

    def _write_reviewed_binding(self, db: sqlite3.Connection, object_type: str, object_id: str,
                                object_version: str, source_id: str, account_id: str, content_hmac: str,
                                scope: str) -> tuple[str, str | None]:
        generation = self._permission_generation()
        digest = self._authz_digest(self._authz_inputs(db, object_type, object_id, object_version))
        old = db.execute("SELECT scope FROM scope_bindings WHERE object_type=? AND object_id=? AND object_version=? "
                         "AND permission_generation=?", (object_type, object_id, object_version, generation)).fetchall()
        old_scope = str(old[0][0]) if len(old) == 1 else ("conflict" if old else None)
        db.execute("DELETE FROM scope_bindings WHERE object_type=? AND object_id=? AND object_version=? "
                   "AND permission_generation=?", (object_type, object_id, object_version, generation))
        proof = self._reviewed_proof(self._id_key, object_type, object_id, object_version, source_id, account_id,
                                     content_hmac, scope, generation, digest)
        db.execute(
            "INSERT INTO scope_bindings(object_type,object_id,object_version,source_id,account_id,"
            "content_hmac,scope,provenance,permission_generation,proof,created_at) "
            "VALUES(?,?,?,?,?,?,?,'source_review',?,?,?)",
            (object_type, object_id, object_version, source_id, account_id, content_hmac, scope, generation,
             proof, self._clock()))
        return digest, old_scope

    def scope_review_item(self, object_type: str, object_id: str) -> dict[str, Any]:
        """Metadata and authorization inputs for owner review; never reads or decrypts a payload."""
        self._require_unlocked()
        version, source, account, content = self._review_identity(self.conn, object_type, object_id)
        inputs = self._authz_inputs(self.conn, object_type, object_id, version)
        reviewed = (self._signal_reviewed(self.conn, object_id) if object_type == "signal"
                    else self._fact_reviewed(self.conn, object_id))
        current = self.conn.execute(
            "SELECT scope FROM scope_bindings WHERE object_type=? AND object_id=? AND object_version=? "
            "AND permission_generation=? AND provenance='source_review'",
            (object_type, object_id, version, self._permission_generation())).fetchall()
        descriptor = {
            "ledger_path": self.path_identity,
            "object_type": object_type,
            "object_id": object_id,
            "object_version": version,
            "source_id": source,
            "account_id": account,
            "content_hmac": content,
            "authz_digest": self._authz_digest(inputs),
            "permission_generation": self._permission_generation(),
        }
        return {
            **descriptor, "review_descriptor": descriptor, "authz": inputs,
            "current_scope": "personal" if reviewed else (
                str(current[0][0]) if len(current) == 1 and current[0][0] != "personal" else None),
        }

    def review_scope(self, object_type: str, object_id: str, scope: str, *, reviewer: str,
                     expected_descriptor: Mapping[str, Any]) -> dict[str, Any]:
        """Bind the owner's reviewed scope to the exact object and authorization inputs they saw."""
        self._require_unlocked()
        if scope not in self._SCOPES:
            raise ValueError("scope must be personal, work, mixed or unknown")
        if reviewer != self._owner:
            raise PermissionDenied("only the owner can review personal scope")
        with self.transaction() as db:
            version, source, account, content = self._review_identity(db, object_type, object_id)
            inputs = self._authz_inputs(db, object_type, object_id, version)
            expected = {
                "ledger_path": self.path_identity,
                "object_type": object_type,
                "object_id": object_id,
                "object_version": version,
                "source_id": source,
                "account_id": account,
                "content_hmac": content,
                "authz_digest": self._authz_digest(inputs),
                "permission_generation": self._permission_generation(),
            }
            if (not isinstance(expected_descriptor, Mapping)
                    or set(expected_descriptor) != set(expected)
                    or any(expected_descriptor[key] != value for key, value in expected.items())):
                raise LedgerError("object changed since review")
            written = [(object_type, *self._write_reviewed_binding(
                db, object_type, object_id, version, source, account, content, scope))]
            if object_type == "fact":
                written.append(("fact_version", *self._write_reviewed_binding(
                    db, "fact_version", object_id, version, source, account, content, scope)))
            generation = self._permission_generation()
            for kind, digest, old_scope in written:
                db.execute("INSERT INTO scope_reviews(object_type,object_id,object_version,old_scope,new_scope,"
                           "permission_generation,reviewer,reviewed_at,authz_digest) VALUES(?,?,?,?,?,?,?,?,?)",
                           (kind, object_id, version, old_scope, scope, generation, reviewer, self._clock(), digest))
        return {"object_type": object_type, "object_id": object_id, "object_version": version, "scope": scope}

    def _prior_review(self, db: sqlite3.Connection, fact_id: str,
                      previous: str | None) -> dict[str, Any] | None:
        """Authorization inputs of the owner-reviewed version a write replaces; None when it was not reviewed."""
        return self._authz_inputs(db, "fact", fact_id, previous or "") if self._fact_reviewed(db, fact_id) else None

    def _rebind_fact_after_write(self, db: sqlite3.Connection, fact_id: str, version: str, content_hmac: str,
                                 provenance: str, prior_review: Mapping[str, Any] | None) -> None:
        if prior_review is not None:
            self._carry_fact_review(db, fact_id, version, content_hmac, prior_review)
        if not self._fact_reviewed(db, fact_id):
            self._bind_fact_after_write(db, fact_id, version, content_hmac, provenance)

    def _carry_fact_review(self, db: sqlite3.Connection, fact_id: str, version: str, content_hmac: str,
                           previous: Mapping[str, Any]) -> None:
        """R-5: restate an owner review for a writer's new version when nothing that authorizes it changed."""
        current = self._authz_inputs(db, "fact", fact_id, version)
        if current is None or any(current[name] != previous[name]
                                  for name in ("primary_subject", "domain", "co_subjects", "viewers")):
            return
        if not current["evidence"] or not all(self._signal_reviewed(db, sid) for sid in current["evidence"]):
            return
        for kind in ("fact", "fact_version"):
            digest, old_scope = self._write_reviewed_binding(
                db, kind, fact_id, version, "ledger", self._owner, content_hmac, "personal")
            db.execute("INSERT INTO scope_reviews(object_type,object_id,object_version,old_scope,new_scope,"
                       "permission_generation,reviewer,reviewed_at,authz_digest) VALUES(?,?,?,?,?,?,?,?,?)",
                       (kind, fact_id, version, old_scope, "personal", self._permission_generation(),
                        "system:carry_forward", self._clock(), digest))

    def _signal_identity_matches(
        self, signal_id: object, connector: object, account_scope: object,
        upstream_id: object, upstream_version: object,
    ) -> bool:
        if (not isinstance(signal_id, str) or not signal_id
                or not isinstance(connector, str) or not connector
                or not isinstance(account_scope, str) or not account_scope
                or not isinstance(upstream_id, str) or not upstream_id
                or not isinstance(upstream_version, str) or not upstream_version):
            return False
        return ids.signal_id(self._id_key, connector, account_scope, upstream_id, upstream_version) == signal_id

    def _scope_binding_matches_object(self, db: sqlite3.Connection, binding: sqlite3.Row) -> bool:
        object_type = str(binding["object_type"])
        if object_type == "signal":
            row = db.execute(
                "SELECT connector,account_scope,upstream_id,upstream_version,content_hmac "
                "FROM signals WHERE signal_id=?",
                (binding["object_id"],)).fetchone()
            return bool(row and row["upstream_version"] == binding["object_version"]
                        and row["connector"] == binding["source_id"]
                        and row["account_scope"] == binding["account_id"]
                        and row["content_hmac"] == binding["content_hmac"]
                        and self._signal_identity_matches(
                            binding["object_id"], row["connector"], row["account_scope"],
                            row["upstream_id"], row["upstream_version"]))
        if object_type == "fact":
            row = db.execute(
                "SELECT recorded_at,scope_hmac FROM facts WHERE fact_id=?", (binding["object_id"],)).fetchone()
        elif object_type == "fact_version":
            row = db.execute(
                "SELECT recorded_at,scope_hmac FROM fact_versions WHERE fact_id=? AND recorded_at=?",
                (binding["object_id"], binding["object_version"])).fetchone()
        else:
            return False
        return bool(row and row["recorded_at"] == binding["object_version"]
                    and binding["source_id"] == "ledger" and binding["account_id"] == self._owner
                    and row["scope_hmac"] == binding["content_hmac"])

    def _refresh_personal_scope_bindings(self, db: sqlite3.Connection, previous_generation: int) -> None:
        bindings = db.execute(
            "SELECT object_type,object_id,object_version,source_id,account_id,content_hmac,scope,"
            "provenance,permission_generation,proof FROM scope_bindings WHERE permission_generation=?",
            (previous_generation,)).fetchall()
        counts: dict[tuple[str, str, str], int] = {}
        for binding in bindings:
            key = (str(binding["object_type"]), str(binding["object_id"]), str(binding["object_version"]))
            counts[key] = counts.get(key, 0) + 1
        for binding in bindings:
            key = (str(binding["object_type"]), str(binding["object_id"]), str(binding["object_version"]))
            try:
                reviewed_inputs = self._authz_inputs(db, *key)
                reviewed_object_exists = self._scope_binding_matches_object(db, binding)
            except LedgerError:
                reviewed_inputs = None
                reviewed_object_exists = False
            if (counts[key] == 1
                    and binding["provenance"] == "source_review"
                    and str(binding["scope"]) in self._SCOPES
                    and reviewed_inputs is not None
                    and reviewed_object_exists
                    and hmac.compare_digest(
                        str(binding["proof"]),
                        self._reviewed_proof(
                            self._id_key, *key, str(binding["source_id"]), str(binding["account_id"]),
                            str(binding["content_hmac"]), str(binding["scope"]), previous_generation,
                            self._authz_digest(reviewed_inputs)))):
                self._write_reviewed_binding(db, *key, str(binding["source_id"]), str(binding["account_id"]),
                                             str(binding["content_hmac"]), str(binding["scope"]))
                continue
            if (counts[key] != 1 or binding["scope"] != "personal"
                    or binding["provenance"] not in ("source_review", "trusted_writer")
                    or type(binding["permission_generation"]) is not int):
                continue
            expected = self._scope_binding_proof(
                self._id_key, *key, str(binding["source_id"]), str(binding["account_id"]),
                str(binding["content_hmac"]), str(binding["scope"]), str(binding["provenance"]),
                previous_generation)
            if (hmac.compare_digest(str(binding["proof"]), expected)
                    and self._scope_binding_matches_object(db, binding)):
                self._record_personal_binding(
                    db, *key, str(binding["source_id"]), str(binding["account_id"]),
                    str(binding["content_hmac"]), provenance=str(binding["provenance"]))

    def require_scope_review_discovery(self, object_type: str, object_id: str, viewer: str) -> None:
        """Authorize the existence and metadata of an object before scope-review display."""
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        if object_type == "signal":
            row = self.conn.execute(
                "SELECT connector,account_scope FROM signals WHERE signal_id=?", (object_id,)).fetchone()
            if row is None:
                raise LedgerError("unknown object")
            signal = self.conn.execute(
                "SELECT subjects FROM signal_access WHERE signal_id=?", (object_id,)).fetchone()
            source = self.conn.execute(
                "SELECT subjects FROM source_access WHERE connector=? AND account_scope=?",
                (row["connector"], row["account_scope"])).fetchone()
            subjects = set(json.loads(signal[0])) if signal else set()
            if source:
                subjects.update(json.loads(source[0]))
            if not subjects:
                raise PermissionDenied("source subject provenance is unknown")
            authorize_source(self, sorted(subjects), self._consent_subjects,
                             f"{row['connector']}:{row['account_scope']}", viewer, operations=("view",))
            return
        if object_type == "fact":
            refs = self.conn.execute(
                "SELECT signal_id FROM fact_evidence WHERE fact_id=?", (object_id,)).fetchall()
            if not refs:
                raise PermissionDenied("Fact evidence provenance is unknown")
            for ref in refs:
                self.require_scope_review_discovery("signal", str(ref["signal_id"]), viewer)
            return
        raise ValueError("scope review covers Signals and Facts only")

    def require_personal_signal(self, signal_id: str, *, allow_obsolete: bool = False) -> sqlite3.Row:
        """Validate source-side scope metadata without opening the evidence blob."""
        self._require_unlocked()
        reviewed = self._signal_reviewed(self.conn, signal_id)
        if not reviewed:
            self.require_personal_signal_admission()
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT signal_id,connector,account_scope,upstream_id,upstream_version,content_hmac "
            "FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
        if (row is None or not self._signal_identity_matches(
                    signal_id, row["connector"], row["account_scope"], row["upstream_id"], row["upstream_version"])
                or (not allow_obsolete and self.source_is_obsolete(signal_id))
                or self.is_tombstoned(row["connector"], row["account_scope"],
                                      row["upstream_id"], row["content_hmac"])
                or not (reviewed or self._has_personal_binding(
                    "signal", signal_id, row["upstream_version"], row["connector"],
                    row["account_scope"], row["content_hmac"]))):
            raise PermissionDenied("personal scope unavailable")
        return row

    def require_personal_connector_admission(self) -> None:
        # Shared Connector scope cannot be authenticated before requesting payloads.
        raise PersonalScopeUnavailable("personal Connector scope unavailable")

    def require_personal_signal_admission(self) -> None:
        # Current Signal proofs do not authenticate the subjects used for authorization.
        raise PersonalScopeUnavailable("personal Signal scope unavailable")

    def require_personal_fact_admission(self) -> None:
        # Current Fact proofs do not authenticate the subjects and evidence used for authorization.
        raise PersonalScopeUnavailable("personal Fact scope unavailable")

    def report_withheld(self, surface: str, withheld: int, returned: int, kind: str = "Fact") -> None:
        """Count objects a collection read left out for scope; a read that kept nothing is unavailable, not empty."""
        if withheld:
            self.scope_withheld[surface] += withheld
            if not returned:
                raise PersonalScopeUnavailable(f"personal {kind} scope unavailable")

    def _require_fact_admitted(self, fact_id: str) -> None:
        """Per-object stop: only an owner-reviewed (v2) Fact passes the Fact hard stop."""
        if not self._fact_reviewed(self.conn, fact_id):
            self.require_personal_fact_admission()

    def _require_signal_admitted(self, signal_id: str) -> None:
        """Per-object stop: only an owner-reviewed (v2) Signal passes the Signal hard stop."""
        if not self._signal_reviewed(self.conn, signal_id):
            self.require_personal_signal_admission()

    def require_personal_calendar_admission(self) -> None:
        # Existing calendar projections lack authenticated dependency bindings.
        raise PersonalScopeUnavailable("personal calendar scope unavailable")

    def _require_personal_fact_version(self, fact_id: str, version: str, content_hmac: str,
                                       evidence_table: str) -> None:
        object_type = "fact_version" if evidence_table == "fact_version_evidence" else "fact"
        reviewed = self._has_reviewed_binding(object_type, fact_id, version, "ledger", self._owner, content_hmac)
        if not reviewed:
            self.require_personal_fact_admission()
        if not (reviewed or self._has_personal_binding(
                object_type, fact_id, version, "ledger", self._owner, content_hmac)):
            raise PermissionDenied("personal scope unavailable")
        if evidence_table not in ("fact_evidence", "fact_version_evidence"):
            raise LedgerError("invalid Fact evidence table")
        if evidence_table == "fact_version_evidence":
            rows = self.conn.execute(
                "SELECT signal_id FROM fact_version_evidence WHERE fact_id=? AND recorded_at=?",
                (fact_id, version)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT signal_id FROM fact_evidence WHERE fact_id=?", (fact_id,)).fetchall()
        for row in rows:
            self.require_personal_signal(str(row["signal_id"]), allow_obsolete=True)

    def require_personal_curated(self, name: str, version: str, content_hmac: str) -> None:
        if not self._has_personal_binding(
                "curated", name, version, "state", self._owner, content_hmac):
            raise PermissionDenied("personal scope unavailable")

    def _bind_fact_after_write(self, db: sqlite3.Connection, fact_id: str, version: str,
                               content_hmac: str, provenance: str) -> None:
        evidence_ids = [str(row[0]) for row in db.execute(
            "SELECT signal_id FROM fact_evidence WHERE fact_id=?", (fact_id,))]
        signals = [db.execute(
            "SELECT signal_id,connector,account_scope,upstream_version,content_hmac FROM signals "
            "WHERE signal_id=?", (signal_id,)).fetchone() for signal_id in evidence_ids]
        admitted = bool(evidence_ids) and len(signals) == len(evidence_ids) and all(
            signal is not None and self._has_personal_binding(
                "signal", str(signal["signal_id"]), str(signal["upstream_version"]),
                str(signal["connector"]), str(signal["account_scope"]), str(signal["content_hmac"]))
            for signal in signals)
        reviewed = bool(evidence_ids) and len(signals) == len(evidence_ids) and all(
            signal is not None and self._has_reviewed_binding(
                "signal", str(signal["signal_id"]), str(signal["upstream_version"]),
                str(signal["connector"]), str(signal["account_scope"]), str(signal["content_hmac"]), db=db)
            for signal in signals)
        if reviewed:
            for kind in ("fact", "fact_version"):
                digest, old_scope = self._write_reviewed_binding(
                    db, kind, fact_id, version, "ledger", self._owner, content_hmac, "personal")
                db.execute(
                    "INSERT INTO scope_reviews(object_type,object_id,object_version,old_scope,new_scope,"
                    "permission_generation,reviewer,reviewed_at,authz_digest) VALUES(?,?,?,?,?,?,?,?,?)",
                    (kind, fact_id, version, old_scope, "personal", self._permission_generation(),
                     "system:new_fact", self._clock(), digest))
        elif admitted or (provenance == Provenance.USER_CONFIRMED.value and not evidence_ids):
            self._record_personal_binding(
                db, "fact", fact_id, version, "ledger", self._owner, content_hmac)
            self._record_personal_binding(
                db, "fact_version", fact_id, version, "ledger", self._owner, content_hmac)

    @staticmethod
    def _source_enrollment_material(
            source_name: str, connector: str, provider_account_id: str, account_scope: str,
            query_digest: str, policy_digest: str, subjects_json: str, action: str,
            permission_generation: int, scope_epoch: int) -> dict[str, Any]:
        return {
            "v": 1,
            "source_name": source_name,
            "connector": connector,
            "provider_account_id": provider_account_id,
            "account_scope": account_scope,
            "query_digest": query_digest,
            "policy_digest": policy_digest,
            "subjects": subjects_json,
            "action": action,
            "permission_generation": permission_generation,
            "scope_epoch": scope_epoch,
        }

    def _scope_epoch(self) -> int:
        row = self.conn.execute(
            "SELECT epoch FROM scope_admission_state WHERE singleton=1").fetchone()
        if row is None:
            raise LedgerError("scope admission epoch is unavailable")
        return int(row[0])

    @staticmethod
    def _canonical_source_definition(
            source_name: str, connector: str, account_scope: str, query: str,
            subjects: Sequence[str], policy: Mapping[str, Any]) -> tuple[str, str, str]:
        if (not isinstance(source_name, str) or not source_name
                or not isinstance(connector, str) or not connector
                or not isinstance(account_scope, str) or not account_scope
                or not isinstance(query, str) or not query
                or len(source_name) > 256 or len(connector) > 256
                or len(account_scope) > 256 or len(query) > 4096):
            raise LedgerError("source enrollment requires a configured source identity and query")
        resolved = sorted(set(subjects))
        if not resolved or any(not isinstance(subject, str) or not subject for subject in resolved):
            raise LedgerError("source enrollment requires resolved subjects")
        if not isinstance(policy, Mapping):
            raise LedgerError("source enrollment requires a classification policy")
        canonical_policy = json.dumps(dict(policy), sort_keys=True, separators=(",", ":"))
        if len(canonical_policy.encode("utf-8")) > 65_536 or len(resolved) > 100 \
                or any(len(subject) > 256 for subject in resolved):
            raise LedgerError("source enrollment policy or subject set exceeds its bound")
        return (
            hashlib.sha256(query.encode()).hexdigest(),
            hashlib.sha256(canonical_policy.encode()).hexdigest(),
            json.dumps(resolved, separators=(",", ":")),
        )

    def _provider_account_marker(self, connector: str, provider_account: str) -> str:
        if (not isinstance(provider_account, str) or not provider_account.strip()
                or len(provider_account.strip()) > 320 or "@" not in provider_account):
            raise LedgerError("source enrollment requires the authenticated provider account")
        return ids.content_hmac(self._id_key, f"provider-account:{connector}:{provider_account.strip()}")

    def source_enrollment_item(
            self, *, source_name: str, connector: str, provider_account: str,
            account_scope: str, query: str, subjects: Sequence[str], policy: Mapping[str, Any]) -> dict[str, Any]:
        require_approved_source_enrollment(source_name)
        query_digest, policy_digest, subjects_json = self._canonical_source_definition(
            source_name, connector, account_scope, query, subjects, policy)
        unknown_subjects = (
            set(subjects) - self._known_subjects if self._known_subjects is not None else set())
        if unknown_subjects:
            raise LedgerError("source enrollment contains unknown subjects")
        return {
            "source_name": source_name,
            "connector": connector,
            "provider_account_id": self._provider_account_marker(connector, provider_account),
            "account_scope": account_scope,
            "query": query,
            "query_digest": query_digest,
            "policy": dict(policy),
            "policy_digest": policy_digest,
            "subjects": sorted(set(subjects)),
            "subjects_json": subjects_json,
            "permission_generation": self._permission_generation(),
        }

    def expect_source_enrollment(
            self, *, source_name: str, connector: str, account_scope: str, query: str,
            subjects: Sequence[str], policy: Mapping[str, Any],
            provider_account: str | None = None) -> None:
        query_digest, policy_digest, subjects_json = self._canonical_source_definition(
            source_name, connector, account_scope, query, subjects, policy)
        self._source_enrollment_expectations[source_name] = {
            "connector": connector,
            "account_scope": account_scope,
            "query_digest": query_digest,
            "policy_digest": policy_digest,
            "subjects_json": subjects_json,
            "provider_account_id": (
                self._provider_account_marker(connector, provider_account)
                if provider_account is not None else None),
        }

    def _write_source_enrollment(
            self, item: Mapping[str, Any], *, action: str, reviewer: str) -> dict[str, Any]:
        if reviewer != self._owner:
            raise PermissionDenied("only the owner can enroll a personal source")
        generation = self._permission_generation()
        scope_epoch = self._scope_epoch()
        material = self._source_enrollment_material(
            str(item["source_name"]), str(item["connector"]), str(item["provider_account_id"]),
            str(item["account_scope"]), str(item["query_digest"]), str(item["policy_digest"]),
            str(item["subjects_json"]), action, generation, scope_epoch)
        proof = ids.content_hmac(
            self._id_key, "enroll:v1:" + json.dumps(material, sort_keys=True, separators=(",", ":")))
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO source_enrollments(source_name,connector,provider_account_id,account_scope,"
                "query_digest,policy_digest,subjects_json,action,permission_generation,scope_epoch,"
                "proof,reviewer,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["source_name"], item["connector"], item["provider_account_id"], item["account_scope"],
                 item["query_digest"], item["policy_digest"], item["subjects_json"], action, generation, scope_epoch,
                 proof, reviewer, self._clock()))
            if cursor.lastrowid is None:
                raise LedgerError("source enrollment row was not created")
            enrollment_id = int(cursor.lastrowid)
        return {**item, "action": action, "enrollment_id": enrollment_id, "proof": proof}

    def enroll_source(
            self, *, source_name: str, connector: str, provider_account: str, account_scope: str,
            query: str, subjects: Sequence[str], policy: Mapping[str, Any], reviewer: str) -> dict[str, Any]:
        self._require_unlocked()
        require_approved_source_enrollment(source_name)
        item = self.source_enrollment_item(
            source_name=source_name, connector=connector, provider_account=provider_account,
            account_scope=account_scope, query=query, subjects=subjects, policy=policy)
        enrolled = self._write_source_enrollment(item, action="enroll", reviewer=reviewer)
        self.expect_source_enrollment(
            source_name=source_name, connector=connector, account_scope=account_scope,
            query=query, subjects=subjects, policy=policy, provider_account=provider_account)
        return enrolled

    def revoke_source(self, source_name: str, *, reviewer: str) -> dict[str, Any]:
        self._require_unlocked()
        if reviewer != self._owner:
            raise PermissionDenied("only the owner can revoke a personal source")
        rows = self.source_enrollments(source_name)
        if not rows:
            raise LedgerError("source is not enrolled")
        item = dict(rows[-1])
        return self._write_source_enrollment(item, action="revoke", reviewer=reviewer)

    def source_enrollments(self, source_name: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM source_enrollments WHERE source_name=? ORDER BY enrollment_id", (source_name,)
        ).fetchall()
        return [dict(row) for row in rows]

    def require_source_enrollment(
            self, *, source_name: str, connector: str, provider_account: str, account_scope: str,
            query: str, subjects: Sequence[str], policy: Mapping[str, Any]) -> dict[str, Any]:
        item = self.source_enrollment_item(
            source_name=source_name, connector=connector, provider_account=provider_account,
            account_scope=account_scope, query=query, subjects=subjects, policy=policy)
        rows = self.source_enrollments(source_name)
        if not rows:
            raise PersonalScopeUnavailable("personal source enrollment unavailable")
        row = rows[-1]
        if row["action"] != "enroll" or any(
                row[key] != item[key] for key in (
                    "connector", "provider_account_id", "account_scope", "query_digest",
                    "policy_digest", "subjects_json", "permission_generation")):
            raise PersonalScopeUnavailable("personal source enrollment is stale or revoked")
        if row["scope_epoch"] != self._scope_epoch():
            raise PersonalScopeUnavailable("personal source enrollment predates the current restore epoch")
        self.expect_source_enrollment(
            source_name=source_name, connector=connector, account_scope=account_scope,
            query=query, subjects=subjects, policy=policy, provider_account=provider_account)
        expected = self._source_enrollment_material(
            source_name, row["connector"], row["provider_account_id"], row["account_scope"],
            row["query_digest"], row["policy_digest"], row["subjects_json"], "enroll",
            int(row["permission_generation"]), int(row["scope_epoch"]))
        proof = ids.content_hmac(
            self._id_key, "enroll:v1:" + json.dumps(expected, sort_keys=True, separators=(",", ":")))
        if not hmac.compare_digest(str(row["proof"]), proof):
            raise PersonalScopeUnavailable("personal source enrollment proof is invalid")
        return row

    def require_active_source_enrollment(self, source_name: str) -> dict[str, Any]:
        require_approved_source_enrollment(source_name)
        rows = self.source_enrollments(source_name)
        if not rows:
            raise PersonalScopeUnavailable("personal source enrollment unavailable")
        row = rows[-1]
        if (row["action"] != "enroll"
                or row["permission_generation"] != self._permission_generation()
                or row["scope_epoch"] != self._scope_epoch()):
            raise PersonalScopeUnavailable("personal source enrollment is stale or revoked")
        expected = self._source_enrollment_expectations.get(source_name)
        if expected is None:
            raise PersonalScopeUnavailable("personal source enrollment expectation is unavailable")
        if any(row[key] != value for key, value in expected.items() if value is not None):
            raise PersonalScopeUnavailable("personal source enrollment no longer matches its configuration")
        expected = self._source_enrollment_material(
            source_name, row["connector"], row["provider_account_id"], row["account_scope"],
            row["query_digest"], row["policy_digest"], row["subjects_json"], "enroll",
            int(row["permission_generation"]), int(row["scope_epoch"]))
        proof = ids.content_hmac(
            self._id_key, "enroll:v1:" + json.dumps(expected, sort_keys=True, separators=(",", ":")))
        if not hmac.compare_digest(str(row["proof"]), proof):
            raise PersonalScopeUnavailable("personal source enrollment proof is invalid")
        return row

    def bind_enrolled_signal(
            self, signal_id: str, *, source_name: str, enrollment_id: int,
            metadata_marker: str) -> None:
        self._require_unlocked()
        require_approved_source_enrollment(source_name)
        if not isinstance(metadata_marker, str) or not metadata_marker:
            raise LedgerError("Signal admission needs a metadata marker")
        with self.transaction() as db:
            enrollment = db.execute(
                "SELECT * FROM source_enrollments WHERE enrollment_id=? AND source_name=? "
                "ORDER BY enrollment_id DESC", (enrollment_id, source_name)).fetchone()
            latest = db.execute(
                "SELECT * FROM source_enrollments WHERE source_name=? ORDER BY enrollment_id DESC LIMIT 1",
                (source_name,)).fetchone()
            if (enrollment is None or latest is None or enrollment["enrollment_id"] != latest["enrollment_id"]
                    or enrollment["action"] != "enroll"
                    or enrollment["permission_generation"] != self._permission_generation()
                    or enrollment["scope_epoch"] != self._scope_epoch()):
                raise PersonalScopeUnavailable("personal source enrollment is stale or revoked")
            signal = db.execute(
                "SELECT connector,account_scope,upstream_version,content_hmac FROM signals WHERE signal_id=?",
                (signal_id,)).fetchone()
            if (signal is None
                    or signal["connector"] not in (source_name, enrollment["connector"])
                    or signal["account_scope"] != enrollment["account_scope"]):
                raise LedgerError("Signal does not belong to the enrolled source")
            db.execute(
                "INSERT INTO signal_source_enrollments(signal_id,enrollment_id,metadata_marker) VALUES(?,?,?) "
                "ON CONFLICT(signal_id) DO UPDATE SET enrollment_id=excluded.enrollment_id,"
                "metadata_marker=excluded.metadata_marker",
                (signal_id, enrollment_id, metadata_marker))
            digest, old_scope = self._write_reviewed_binding(
                db, "signal", signal_id, str(signal["upstream_version"]), str(signal["connector"]),
                str(signal["account_scope"]), str(signal["content_hmac"]), "personal")
            db.execute(
                "INSERT INTO scope_reviews(object_type,object_id,object_version,old_scope,new_scope,"
                "permission_generation,reviewer,reviewed_at,authz_digest) VALUES(?,?,?,?,?,?,?,?,?)",
                ("signal", signal_id, signal["upstream_version"], old_scope, "personal",
                 self._permission_generation(), "system:source_enrollment", self._clock(), digest))

    def record_coverage_hold(
            self, *, source_name: str, connector: str, account_scope: str, provider_account_id: str,
            upstream_id: str, upstream_version: str, detail: str, state: str = "held") -> bool:
        if state != "held":
            raise LedgerError("new coverage holds must start held")
        if any(not isinstance(value, str) or not value for value in (
                source_name, connector, account_scope, provider_account_id, upstream_id, upstream_version)):
            raise LedgerError("coverage hold identity is incomplete")
        if (not isinstance(detail, str) or len(source_name) > 256 or len(connector) > 256
                or len(account_scope) > 256
                or len(provider_account_id) > 256 or len(upstream_id) > 512
                or len(upstream_version) > 512 or len(detail) > 512
                or any(ord(char) < 32 for char in detail)):
            raise LedgerError("coverage hold identity or detail exceeds its bound")
        with self.transaction() as db:
            held = db.execute(
                "SELECT COUNT(*) FROM coverage_holds "
                "WHERE source_name=? AND state='held'",
                (source_name,)).fetchone()[0]
            prior = db.execute(
                "SELECT hold_id FROM coverage_holds WHERE source_name=? AND provider_account_id=? "
                "AND upstream_id=? AND upstream_version=? AND permission_generation=?",
                (source_name, provider_account_id, upstream_id, upstream_version,
                 self._permission_generation())).fetchone()
            if prior is None and held >= _COVERAGE_HOLD_LIMIT:
                self._record_coverage_hold_cap(db, source_name, connector, account_scope)
                return False
            now = self._clock()
            db.execute(
                "INSERT INTO coverage_holds(source_name,connector,account_scope,provider_account_id,"
                "upstream_id,upstream_version,permission_generation,state,first_seen,last_seen,detail) "
                "VALUES(?,?,?,?,?,?,?,'held',?,?,?) "
                "ON CONFLICT(source_name,provider_account_id,upstream_id,upstream_version,"
                "permission_generation) DO UPDATE SET state='held',last_seen=excluded.last_seen,"
                "detail=excluded.detail",
                (source_name, connector, account_scope, provider_account_id, upstream_id, upstream_version,
                 self._permission_generation(), now, now, detail))
        return True

    @staticmethod
    def _coverage_hold_cap_spot(source_name: str) -> str:
        return f"{_COVERAGE_HOLD_CAP_SPOT}:{source_name}:{_COVERAGE_HOLD_LIMIT}"

    def _record_coverage_hold_cap(
            self, db: sqlite3.Connection, source_name: str, connector: str, account_scope: str) -> None:
        row = db.execute(
            "SELECT blind_spots FROM connector_coverage WHERE connector=? AND account_scope=?",
            (connector, account_scope)).fetchone()
        try:
            blind_spots = list(json.loads(row[0])) if row is not None else []
        except (TypeError, ValueError):
            blind_spots = []
        marker = self._coverage_hold_cap_spot(source_name)
        if marker not in blind_spots:
            blind_spots.append(marker)
        db.execute(
            "INSERT INTO connector_coverage("
            "connector,account_scope,status,blind_spots,checked_at,record_count) "
            "VALUES(?,?,'degraded',?,?,0) "
            "ON CONFLICT(connector,account_scope) DO UPDATE SET status='degraded',"
            "blind_spots=excluded.blind_spots,checked_at=excluded.checked_at",
            (connector, account_scope, json.dumps(blind_spots), self._clock()))

    def _coverage_hold_capped(self, db: sqlite3.Connection, source_name: str) -> bool:
        if db.execute(
                "SELECT COUNT(*) FROM coverage_holds WHERE source_name=? AND state='held'",
                (source_name,)).fetchone()[0] < _COVERAGE_HOLD_LIMIT:
            return False
        marker = self._coverage_hold_cap_spot(source_name)
        for row in db.execute(
                "SELECT blind_spots FROM connector_coverage WHERE connector=?", (source_name,)):
            try:
                if marker in json.loads(row[0]):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def resolve_coverage_hold(
            self, source_name: str, provider_account_id: str, upstream_id: str,
            upstream_version: str, state: str, *, required: bool = True) -> bool:
        if state not in ("admitted", "excluded", "tombstoned", "deleted"):
            raise LedgerError("invalid coverage hold resolution")
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE coverage_holds SET state=?,last_seen=? WHERE source_name=? AND provider_account_id=? "
                "AND upstream_id=? AND upstream_version=?",
                (state, self._clock(), source_name, provider_account_id, upstream_id, upstream_version)).rowcount
            if changed < 1:
                if required:
                    raise LedgerError("coverage hold is unknown or belongs to another permission generation")
                return False
        return True

    def coverage_holds(
            self, source_name: str, *, state: str | None = None,
            permission_generation: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM coverage_holds WHERE source_name=?"
        params: list[Any] = [source_name]
        if state is not None:
            sql += " AND state=?"
            params.append(state)
        if permission_generation is not None:
            sql += " AND permission_generation=?"
            params.append(permission_generation)
        sql += " ORDER BY first_seen,hold_id"
        rows = [dict(row) for row in self.conn.execute(sql, params).fetchall()]
        if (state == "held" and permission_generation is not None and not rows
                and self._coverage_hold_capped(self.conn, source_name)):
            return [{
                "hold_id": 0, "source_name": source_name, "connector": source_name,
                "account_scope": "", "provider_account_id": "", "upstream_id": "",
                "upstream_version": "", "permission_generation": permission_generation,
                "state": "held", "first_seen": "", "last_seen": "",
                "detail": self._coverage_hold_cap_spot(source_name), "capped": True,
            }]
        return rows

    def set_processing_state(self, signal_id: str, state: ProcessingState, reason: str | None = None) -> None:
        self._require_unlocked()
        if state is ProcessingState.EXCLUDED and not reason:
            raise LedgerError("an excluded Signal states why")
        with self.transaction() as c:
            cur = c.execute(
                "UPDATE signals SET processing_state=?, exclusion_reason=? WHERE signal_id=?",
                (state.value, reason, signal_id))
            if cur.rowcount != 1:
                raise LedgerError(f"unknown signal {signal_id}")

    def signal_state(self, signal_id: str) -> str | None:
        row = self.conn.execute("SELECT processing_state FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
        return str(row[0]) if row else None

    def unresolved_signals(self) -> list[str]:
        """Fetched-but-not-reconciled Signals (quiet-claim closure, §6.7)."""
        rows = self.conn.execute(
            "SELECT signal_id FROM signals WHERE processing_state NOT IN ('reconciled','excluded') "
            "OR signal_id IN (SELECT signal_id FROM signal_failures) "
            "ORDER BY signal_id").fetchall()
        return [str(r[0]) for r in rows]

    # ── facts ────────────────────────────────────────────────────────────────
    def _encode_value(self, fact_id: str, value: Any, confidentiality: Confidentiality) -> tuple[str, int, int | None]:
        if confidentiality is not Confidentiality.ORDINARY:
            if self._cipher is None:
                raise LedgerError("sensitive and restricted values need an encryption key; refusing plaintext")
            text, version = self._cipher.encrypt_json(value, aad=fact_id.encode())
            return text, 1, version
        return json.dumps(value, sort_keys=True), 0, None

    def protect_legacy_values(self) -> int:
        """Refuse automatic legacy processing until its Personal scope is reviewed."""
        self._require_unlocked()
        if (self.conn.execute("SELECT 1 FROM facts WHERE value_encrypted=0 "
                              "AND confidentiality<>'ordinary' LIMIT 1").fetchone()
                or self.conn.execute("SELECT 1 FROM fact_versions v JOIN facts f USING(fact_id) "
                                     "WHERE v.value_encrypted=0 AND f.confidentiality<>'ordinary' LIMIT 1").fetchone()
                or self.conn.execute("SELECT 1 FROM alerts WHERE dates_encrypted IS NULL LIMIT 1").fetchone()):
            raise PermissionDenied("legacy personal data requires reviewed migration")
        return 0

    @staticmethod
    def _last_version(conn: sqlite3.Connection, fact_id: str) -> str | None:
        # The current projection names the last committed version. Text MAX misorders offsets and fractions.
        row = conn.execute("SELECT recorded_at FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        return str(row[0]) if row and row[0] else None

    def put_fact(self, fact: FactInput, fact_id: str | None = None, *, expected_recorded_at: str | None = None,
                 expected_permission_generation: int | None = None) -> str:
        """Create a Fact, or add a new version of an existing one (history is append-only)."""
        self._require_unlocked()
        if fact_id is not None and not self._fact_reviewed(self.conn, fact_id):
            self.require_personal_fact_admission()
        is_new = fact_id is None
        fid = fact_id or ids.new_id("fct")
        text, encrypted, key_version = self._encode_value(fid, fact.value, fact.confidentiality)
        with self.transaction() as c:
            if (expected_permission_generation is not None
                    and self._permission_generation() != expected_permission_generation):
                raise PermissionDenied('permissions changed since review')
            previous = self._last_version(c, fid)
            if expected_recorded_at is not None and previous != expected_recorded_at:
                raise LedgerError('Fact changed since review')
            now = _after(previous, self._clock())
            prior_review = None if is_new else self._prior_review(c, fid, previous)
            if not is_new:
                identity = c.execute("SELECT domain,predicate,primary_subject_id FROM facts WHERE fact_id=?",
                                     (fid,)).fetchone()
                if identity is None:
                    raise LedgerError(f"unknown fact {fid}")
                if tuple(identity) != (fact.domain, fact.predicate, fact.primary_subject_id):
                    raise LedgerError("a Fact version cannot change its Domain, predicate or subject")
                c.execute("DELETE FROM fact_evidence WHERE fact_id=?", (fid,))
                c.execute(
                    "UPDATE facts SET typed_value=?,value_encrypted=?,key_version=?,confidentiality=?,"
                    "valid_from=?,valid_to=?,validity_unknown=?,occurred_at=?,observed_at=?,recorded_at=?,"
                    "provenance_class=?,status=?,rule_version=? WHERE fact_id=?",
                    (text, encrypted, key_version, fact.confidentiality.value, fact.valid_from, fact.valid_to,
                     int(fact.validity_unknown), fact.occurred_at, fact.observed_at, now,
                     fact.provenance.value, fact.status.value, fact.rule_version, fid))
            else:
                c.execute(
                    "INSERT INTO facts(fact_id,domain,predicate,typed_value,value_encrypted,key_version,"
                    "primary_subject_id,confidentiality,valid_from,valid_to,validity_unknown,occurred_at,"
                    "observed_at,recorded_at,provenance_class,status,rule_version)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, fact.domain, fact.predicate, text, encrypted, key_version, fact.primary_subject_id,
                     fact.confidentiality.value, fact.valid_from, fact.valid_to, int(fact.validity_unknown),
                     fact.occurred_at, fact.observed_at, now, fact.provenance.value, fact.status.value,
                     fact.rule_version))
            digest = hashlib.sha256(
                "|".join(f"{s}:{a}:{b}" for s, a, b in sorted(fact.evidence)).encode()).hexdigest()
            c.execute(
                "INSERT INTO fact_versions(fact_id,recorded_at,value_encrypted,key_version,valid_from,valid_to,"
                "validity_unknown,provenance_class,evidence_digest,typed_value,status,rule_version,observed_at,occurred_at,"
                "scope_hmac)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, now, encrypted, key_version, fact.valid_from, fact.valid_to, int(fact.validity_unknown),
                 fact.provenance.value, digest, text, fact.status.value, fact.rule_version,
                 fact.observed_at, fact.occurred_at, ids.content_hmac(self._id_key, text)))
            scope_hmac = ids.content_hmac(self._id_key, text)
            c.execute("UPDATE facts SET scope_hmac=? WHERE fact_id=?", (scope_hmac, fid))
            for subject, role in fact.co_subjects:
                c.execute("INSERT OR REPLACE INTO fact_subjects(fact_id,subject_id,role) VALUES(?,?,?)",
                          (fid, subject, role))
            for viewer in fact.viewers:
                c.execute("INSERT OR IGNORE INTO fact_viewers(fact_id,viewer_id) VALUES(?,?)", (fid, viewer))
            for signal_id, start, end in fact.evidence:
                c.execute("INSERT OR REPLACE INTO fact_evidence(fact_id,signal_id,span_start,span_end)"
                          " VALUES(?,?,?,?)", (fid, signal_id, start, end))
                c.execute("INSERT OR IGNORE INTO fact_version_evidence VALUES(?,?,?,?,?)",
                          (fid, now, signal_id, start, end))
            c.execute("UPDATE facts SET obligation_key=? WHERE fact_id=?", (fact.obligation_key, fid))
            self._rebind_fact_after_write(c, fid, now, scope_hmac, fact.provenance.value, prior_review)
        return fid

    def set_document_method(self, signal_id: str, method: str) -> None:
        self._require_unlocked()
        row = self.signal_row(signal_id)
        if row is None or row['connector'] != 'documents' or method not in ('text', 'ocr', 'unknown'):
            raise LedgerError('invalid document provenance')
        with self.transaction() as db:
            previous = db.execute('SELECT method FROM document_sources WHERE signal_id=?', (signal_id,)).fetchone()
            if previous is not None and previous[0] != method:
                method = 'unknown'
            db.execute('INSERT INTO document_sources VALUES(?,?) ON CONFLICT(signal_id) '
                       'DO UPDATE SET method=excluded.method', (signal_id, method))

    def document_method(self, signal_id: str) -> str:
        row = self.conn.execute('SELECT method FROM document_sources WHERE signal_id=?', (signal_id,)).fetchone()
        return str(row[0]) if row else 'unknown'

    def mark_document_field_fact(self, fact_id: str) -> None:
        row = self.conn.execute('SELECT provenance_class FROM facts WHERE fact_id=?', (fact_id,)).fetchone()
        if row is None or row[0] == 'user_confirmed':
            return
        sources = self.conn.execute('SELECT s.signal_id FROM fact_evidence e JOIN signals s USING(signal_id) '
                                    "WHERE e.fact_id=? AND s.connector='documents'", (fact_id,)).fetchall()
        if any(self.document_method(source[0]) != 'text' for source in sources):
            self.conn.execute('INSERT OR IGNORE INTO document_field_facts VALUES(?)', (fact_id,))

    def _may_view(self, row: sqlite3.Row, viewer_id: str) -> bool:
        from catchup_core.fact_access import AccessPolicy, may_view  # noqa: PLC0415
        return may_view(self.conn, AccessPolicy(self._owner, self._consent_subjects, self._known_subjects),
                        row, viewer_id, self._clock())

    def get_fact_metadata(self, fact_id: str, viewer_id: str, *, operation: str = 'view') -> FactMetadata:
        from catchup_core.fact_access import AccessPolicy, require_metadata  # noqa: PLC0415
        self._require_unlocked()
        return require_metadata(self.conn, AccessPolicy(self._owner, self._consent_subjects, self._known_subjects),
            fact_id, viewer_id, self._clock(), operation=operation,
            source_operation=(operation in ('export', 'emergency')), authority=self)

    def _may_view_sources(self, fact_id: str, subjects: set[str], viewer: str) -> bool:
        required = (subjects & self._consent_subjects) - {viewer}
        if not required:
            return True
        scopes = self.conn.execute('SELECT DISTINCT s.connector,s.account_scope FROM signals s JOIN '
            '(SELECT signal_id FROM fact_evidence WHERE fact_id=? UNION '
            'SELECT signal_id FROM fact_version_evidence WHERE fact_id=?) e USING(signal_id)',
            (fact_id, fact_id)).fetchall()
        return all(self.consent_active(subject, 'view', viewer, scope=f"{source[0]}:{source[1]}")
                   for source in scopes for subject in required)

    def require_fact_operation(self, fact_id: str, viewer_id: str, operation: str) -> FactView:
        """Viewing a claim never grants permission to change or export another adult's claim."""
        fact = self.get_fact(fact_id, viewer_id)
        subjects = {fact.primary_subject_id}
        subjects.update(r[0] for r in self.conn.execute(
            "SELECT subject_id FROM fact_subjects WHERE fact_id=?", (fact_id,)))
        if any(subject in self._consent_subjects and subject != viewer_id
               and not self.consent_active(subject, operation, viewer_id, scope=fact.domain)
               for subject in subjects):
            raise PermissionDenied(f"{operation} requires each adult subject's scoped consent")
        return fact

    def get_fact(self, fact_id: str, viewer_id: str) -> FactView:
        self._require_unlocked()
        scope = self.conn.execute(
            "SELECT recorded_at,scope_hmac FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        if scope is None:
            raise LedgerError(f"unknown fact {fact_id}")
        self._require_personal_fact_version(
            fact_id, str(scope["recorded_at"]), str(scope["scope_hmac"] or ""), "fact_evidence")
        row = self.conn.execute(
            "SELECT fact_id,domain,predicate,primary_subject_id,confidentiality,valid_from,valid_to,"
            "validity_unknown,occurred_at,observed_at,recorded_at,provenance_class,status,obligation_key,"
            "scope_hmac FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown fact {fact_id}")
        if (row["recorded_at"], row["scope_hmac"]) != tuple(scope):
            raise PermissionDenied("personal scope changed during retrieval")
        if not self._may_view(row, viewer_id):
            raise PermissionDenied(f"{viewer_id} may not view {fact_id}")
        self._require_personal_fact_version(fact_id, str(row["recorded_at"]), str(row["scope_hmac"] or ""),
                                            "fact_evidence")
        value_row = self.conn.execute(
            "SELECT typed_value,value_encrypted,scope_hmac,recorded_at FROM facts WHERE fact_id=?",
            (fact_id,)).fetchone()
        if value_row is None:
            raise LedgerError("Fact changed during retrieval")
        if (value_row["recorded_at"] != row["recorded_at"]
                or value_row["scope_hmac"] != row["scope_hmac"]
                or not hmac.compare_digest(str(row["scope_hmac"] or ""),
                                           ids.content_hmac(self._id_key, value_row["typed_value"]))):
            raise PermissionDenied("personal scope changed during retrieval")
        self._require_personal_fact_version(
            fact_id, str(row["recorded_at"]), str(row["scope_hmac"] or ""), "fact_evidence")
        value = self._decode_value(fact_id, value_row["typed_value"], bool(value_row["value_encrypted"]))
        evidence = tuple(
            (str(e["signal_id"]), int(e["span_start"]), int(e["span_end"]))
            for e in self.conn.execute(
                "SELECT signal_id,span_start,span_end FROM fact_evidence WHERE fact_id=? ORDER BY span_start",
                (fact_id,)))
        from catchup_core.fact_access import AccessPolicy, document_fact_state  # noqa: PLC0415
        status, provenance = document_fact_state(self.conn,
            AccessPolicy(self._owner, self._consent_subjects, self._known_subjects), row, self._clock(), self)
        return FactView(
            fact_id=fact_id, domain=row["domain"], predicate=row["predicate"], value=value,
            primary_subject_id=row["primary_subject_id"], confidentiality=row["confidentiality"],
            provenance_class=provenance, status=status, valid_from=row["valid_from"],
            valid_to=row["valid_to"], validity_unknown=bool(row["validity_unknown"]),
            observed_at=row["observed_at"], recorded_at=row["recorded_at"], evidence=evidence,
            obligation_key=row["obligation_key"])

    def _decode_value(self, fact_id: str, text: str, encrypted: bool) -> Any:
        if not encrypted:
            return json.loads(text)
        if self._cipher is None:
            raise LedgerError("encrypted value but no key available")
        return self._cipher.decrypt_json(text, aad=fact_id.encode())

    def fact_history(self, fact_id: str, viewer_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
        """What Artha believed over time ("what did Artha know then?")."""
        self._require_unlocked()
        scope = self.conn.execute(
            "SELECT recorded_at,scope_hmac FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        if scope is None:
            raise LedgerError("unknown Fact")
        self._require_personal_fact_version(
            fact_id, str(scope["recorded_at"]), str(scope["scope_hmac"] or ""), "fact_evidence")
        current = self.conn.execute(
            "SELECT fact_id,domain,primary_subject_id FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        if current is None:
            raise LedgerError("unknown Fact")
        if not self._may_view(current, viewer_id):
            raise PermissionDenied(f"{viewer_id} may not view {fact_id}")
        cutoff = _instant(as_of) if as_of is not None else None
        # Legacy versions retain their exact keys. SQLite text ordering and julianday's millisecond precision
        # cannot provide this microsecond/offset-aware comparison; order the one Fact's history in memory.
        versions = sorted(self.conn.execute(
            'SELECT fact_id,recorded_at,status,provenance_class,valid_from,valid_to,key_version,validity_unknown,'
            'observed_at,occurred_at,scope_hmac FROM fact_versions WHERE fact_id=?', (fact_id,)),
                          key=lambda row: _instant(row['recorded_at']))
        if cutoff is not None:
            versions = [row for row in versions if _instant(row["recorded_at"]) <= cutoff]
        for row in versions:
            self._require_personal_fact_version(
                fact_id, str(row["recorded_at"]), str(row["scope_hmac"] or ""), "fact_version_evidence")
        self.get_fact_metadata(fact_id, viewer_id)
        version_ids = [str(row["recorded_at"]) for row in versions]
        payloads = {}
        for version in version_ids:
            payload = self.conn.execute(
                "SELECT typed_value,value_encrypted,scope_hmac FROM fact_versions "
                "WHERE fact_id=? AND recorded_at=?",
                (fact_id, version)).fetchone()
            if payload is None:
                raise LedgerError("Fact history changed during retrieval")
            payloads[version] = payload
        for row in versions:
            version = str(row["recorded_at"])
            if (payloads[version]["scope_hmac"] != row["scope_hmac"]
                    or not hmac.compare_digest(str(row["scope_hmac"] or ""),
                        ids.content_hmac(self._id_key, payloads[version]["typed_value"]))):
                raise PermissionDenied("personal scope changed during retrieval")
            self._require_personal_fact_version(
                fact_id, version, str(row["scope_hmac"] or ""), "fact_version_evidence")
        out: list[dict[str, Any]] = []
        for row in versions:
            payload = payloads[str(row["recorded_at"])]
            out.append({
                "recorded_at": row["recorded_at"], "status": row["status"],
                "value": self._decode_value(fact_id, payload["typed_value"], bool(payload["value_encrypted"])),
                "provenance_class": row["provenance_class"], "valid_from": row["valid_from"],
                "valid_to": row["valid_to"], "key_version": row["key_version"],
                "validity_unknown": bool(row["validity_unknown"]),
                "observed_at": row["observed_at"], "occurred_at": row["occurred_at"],
                "evidence": [dict(e) for e in self.conn.execute(
                    "SELECT signal_id,span_start,span_end FROM fact_version_evidence "
                    "WHERE fact_id=? AND recorded_at=?", (fact_id, row["recorded_at"]))]})
        return out

    def facts_for(self, domain: str, predicate: str, viewer_id: str,
                  statuses: Sequence[FactStatus] = (FactStatus.CURRENT,)) -> list[FactView]:
        marks = ",".join("?" for _ in statuses)
        rows = self.conn.execute(  # `marks` is only "?" placeholders; values are bound
            f"SELECT fact_id FROM facts WHERE domain=? AND predicate=? AND status IN ({marks}) "  # noqa: S608
            "ORDER BY recorded_at, fact_id", (domain, predicate, *[s.value for s in statuses])).fetchall()
        views: list[FactView] = []
        withheld = 0
        for r in rows:
            try:
                views.append(self.get_fact(r["fact_id"], viewer_id))
            except PersonalScopeUnavailable:
                withheld += 1
            except PermissionDenied:
                continue
        self.report_withheld("facts_for", withheld, len(views))
        return views

    def add_evidence(self, fact_id: str, signal_id: str, span_start: int, span_end: int) -> None:
        """Corroboration changes current knowledge, never a past version's evidence."""
        self._require_unlocked()
        self._require_fact_admitted(fact_id)
        if self.conn.execute("SELECT 1 FROM fact_evidence WHERE fact_id=? AND signal_id=? "
                             "AND span_start=? AND span_end=?", (fact_id, signal_id, span_start, span_end)).fetchone():
            return
        with self.transaction() as c:
            row = c.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
            if row is None:
                raise LedgerError("unknown Fact")
            previous = self._last_version(c, fact_id)
            now = _after(previous, self._clock())
            prior_review = self._prior_review(c, fact_id, previous)
            c.execute("INSERT OR REPLACE INTO fact_evidence(fact_id,signal_id,span_start,span_end) VALUES(?,?,?,?)",
                      (fact_id, signal_id, span_start, span_end))
            digest = hashlib.sha256("|".join(f"{r[0]}:{r[1]}:{r[2]}" for r in c.execute(
                "SELECT signal_id,span_start,span_end FROM fact_evidence WHERE fact_id=? ORDER BY signal_id,span_start",
                (fact_id,))).encode()).hexdigest()
            c.execute("INSERT INTO fact_versions SELECT fact_id,?,value_encrypted,key_version,valid_from,valid_to,"
                      "validity_unknown,provenance_class,?,typed_value,status,rule_version,observed_at,occurred_at,"
                      "scope_hmac "
                      "FROM facts WHERE fact_id=?",
                      (now, digest, fact_id))
            c.execute("INSERT INTO fact_version_evidence SELECT fact_id,?,signal_id,span_start,span_end "
                      "FROM fact_evidence WHERE fact_id=?", (now, fact_id))
            c.execute("UPDATE facts SET recorded_at=? WHERE fact_id=?", (now, fact_id))
            self._rebind_fact_after_write(c, fact_id, now, str(row["scope_hmac"] or ""),
                                          str(row["provenance_class"]), prior_review)

    def set_fact_status(self, fact_id: str, status: FactStatus, provenance: Provenance | None = None, *,
                        withdraw_signals: Sequence[str] = ()) -> None:
        """Change a Fact's lifecycle status; the previous state stays in ``fact_versions``."""
        self._require_unlocked()
        self._require_fact_admitted(fact_id)
        with self.transaction() as c:
            previous = self._last_version(c, fact_id)
            now = _after(previous, self._clock())
            row = c.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
            if row is None:
                raise LedgerError(f"unknown fact {fact_id}")
            prior_review = self._prior_review(c, fact_id, previous)
            c.executemany("DELETE FROM fact_evidence WHERE fact_id=? AND signal_id=?",
                          ((fact_id, sid) for sid in withdraw_signals))
            prov = provenance.value if provenance else row["provenance_class"]
            c.execute("UPDATE facts SET status=?, provenance_class=?, recorded_at=? WHERE fact_id=?",
                      (status.value, prov, now, fact_id))
            digest = hashlib.sha256("|".join(
                f"{r[0]}:{r[1]}:{r[2]}" for r in c.execute(
                    "SELECT signal_id,span_start,span_end FROM fact_evidence WHERE fact_id=? ORDER BY signal_id",
                    (fact_id,)).fetchall()).encode()).hexdigest()
            c.execute(
                "INSERT INTO fact_versions(fact_id,recorded_at,value_encrypted,key_version,valid_from,valid_to,"
                "validity_unknown,provenance_class,evidence_digest,typed_value,status,rule_version,observed_at,occurred_at,"
                "scope_hmac)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fact_id, now, row["value_encrypted"], row["key_version"], row["valid_from"], row["valid_to"],
                 row["validity_unknown"], prov, digest, row["typed_value"], status.value, row["rule_version"],
                 row['observed_at'], row['occurred_at'], row["scope_hmac"]))
            c.execute("INSERT INTO fact_version_evidence SELECT fact_id,?,signal_id,span_start,span_end "
                      "FROM fact_evidence WHERE fact_id=?", (now, fact_id))
            self._rebind_fact_after_write(c, fact_id, now, str(row["scope_hmac"] or ""), prov, prior_review)
        self.correction_notice(fact_id, "Source belief changed; inspect the current evidence before acting.")

    def facts_matching(self, domain: str, predicate: str, subject_id: str,
                       statuses: Sequence[FactStatus] = (FactStatus.CURRENT, FactStatus.DISPUTED),
                       obligation_key: str | None = None) -> list[FactView]:
        """System-level read (reconciliation): Facts for one subject and predicate.

        The obligation filter is applied in SQL (``facts_obligation`` index) so only the matching Facts are decoded and
        decrypted; loading every Fact of the predicate made reconciliation quadratic in the number of obligations.
        Each match passes the per-object scope gate in ``get_fact``; one unreviewed match fails the whole read.
        """
        marks = ",".join("?" for _ in statuses)
        extra, bound = ("", ()) if obligation_key is None else (" AND obligation_key=?", (obligation_key,))
        rows = self.conn.execute(  # `marks` is only "?" placeholders; values are bound
            f"SELECT fact_id FROM facts WHERE domain=? AND predicate=? AND primary_subject_id=? "  # noqa: S608
            f"AND status IN ({marks}){extra} ORDER BY recorded_at, fact_id",
            (domain, predicate, subject_id, *[st.value for st in statuses], *bound)).fetchall()
        return [self.get_fact(str(r["fact_id"]), self._owner) for r in rows]

    def observe_source_versions(self, signal_ids: Sequence[str], *, complete: bool) -> None:
        """Select observed candidates without ordering opaque versions; incomplete observations cannot withdraw."""
        versions: dict[tuple[str, str, str], set[str]] = {}
        for sid in signal_ids:
            row = self.signal_row(sid)
            if row is None:
                raise LedgerError("unknown Signal in source observation")
            versions.setdefault((row["connector"], row["account_scope"], row["upstream_id"]), set()).add(sid)
        for source, peers in versions.items():
            if len(peers) > 1:
                with self.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO source_heads VALUES(?,?,?,NULL,0)", source)
                for sid in peers:
                    self.record_failure(sid, "version", "extract")
            else:
                head = next(iter(peers))
                previous = self.conn.execute("SELECT signal_id,complete FROM source_heads "
                    "WHERE connector=? AND account_scope=? AND upstream_id=?", source).fetchone()
                ambiguous = self.conn.execute("SELECT s.signal_id FROM signals s JOIN signal_failures f "
                    "ON f.signal_id=s.signal_id WHERE s.connector=? AND s.account_scope=? AND s.upstream_id=? "
                    "AND f.stage='version'", source).fetchall()
                if ambiguous and not complete:
                    continue  # A partial view cannot settle a previously ambiguous source.
                with self.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO source_heads VALUES(?,?,?,?,?)", (*source, head, int(complete)))
                for row in ambiguous:
                    self.clear_failure(row[0], "version")
                    self.set_processing_state(row[0], ProcessingState.RECONCILED)
                if ambiguous or (previous and (previous[0] != head or (not previous[1] and complete))):
                    self.set_processing_state(head, ProcessingState.FETCHED)

    def source_head(self, signal_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM source_heads WHERE signal_id=? AND complete=1",
                                 (signal_id,)).fetchone() is not None

    def source_is_obsolete(self, signal_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM signals s JOIN source_heads h ON s.connector=h.connector "
            "AND s.account_scope=h.account_scope AND s.upstream_id=h.upstream_id "
            "WHERE s.signal_id=? AND h.signal_id IS NOT NULL AND h.signal_id<>s.signal_id",
            (signal_id,)).fetchone() is not None

    def replace_source_support(self, signal_id: str) -> None:
        """Withdraw obsolete versions only after complete interpretation; retain history."""
        row = self.signal_row(signal_id)
        if row is None:
            raise LedgerError("unknown Signal")
        self.require_personal_signal(signal_id, allow_obsolete=True)
        if not self.source_head(signal_id):
            return
        older = self.conn.execute("SELECT signal_id FROM signals WHERE connector=? AND account_scope=? "
            "AND upstream_id=? AND signal_id<>?",
            (row["connector"], row["account_scope"], row["upstream_id"], signal_id)).fetchall()
        for old in older:
            facts = self.conn.execute("SELECT f.fact_id,f.provenance_class FROM facts f JOIN fact_evidence e "
                "ON f.fact_id=e.fact_id WHERE e.signal_id=? AND f.status IN ('current','disputed','unknown') "
                "AND NOT EXISTS(SELECT 1 FROM fact_evidence n WHERE n.fact_id=f.fact_id AND n.signal_id=?)",
                (old[0], signal_id)).fetchall()
            for fact in facts:
                if fact['provenance_class'] == Provenance.USER_CONFIRMED.value:
                    continue
                independent = self.conn.execute("SELECT 1 FROM fact_evidence e JOIN signals s "
                    "ON e.signal_id=s.signal_id WHERE e.fact_id=? AND NOT "
                    "(s.connector=? AND s.account_scope=? AND s.upstream_id=?)", (fact[0], row['connector'],
                     row['account_scope'], row['upstream_id'])).fetchone()
                if not independent:
                    self.set_fact_status(fact[0], FactStatus.RETRACTED)
                else:
                    self.set_fact_status(fact[0], FactStatus.CURRENT, withdraw_signals=(old[0],))
            self.clear_failure(old[0], 'extract')
            self.clear_failure(old[0], 'content')

    def source_content(self, connector: str, scope: str, upstream_id: str, version: str) -> str | None:
        row = self.conn.execute(
            "SELECT content_hmac FROM signals WHERE connector=? AND account_scope=? AND upstream_id=? "
            "AND upstream_version=?", (connector, scope, upstream_id, version)).fetchone()
        return str(row[0]) if row else None

    def put_calendar(self, connector: str, scope: str, upstream: str, signal_id: str, event: Any) -> None:
        self._require_unlocked()
        if self._cipher is None:
            raise LedgerError("calendar storage requires encryption")
        aad = f"calendar:{connector}:{scope}:{upstream}".encode()
        payload, version = self._cipher.encrypt_json({"id": event.event_id, "start": event.start.isoformat(),
            "end": event.end.isoformat(), "all_day": event.all_day, "kind": event.kind}, aad=aad)
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO calendar_records VALUES(?,?,?,?,?,?)",
                       (connector, scope, upstream, signal_id, payload, version))

    def cancel_calendar(self, connector: str, scope: str, upstream: str) -> None:
        """Journal withdrawal before changing projections; every step is safe to replay after process death."""
        self._require_unlocked()
        self.require_personal_fact_admission()
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO calendar_cancellations VALUES(?,?,?,1)", (connector, scope, upstream))
        sources = tuple(r[0] for r in self.conn.execute(
            "SELECT signal_id FROM signals WHERE connector=? AND account_scope=? AND upstream_id=?",
            (connector, scope, upstream)))
        facts = self.conn.execute(
            "SELECT DISTINCT f.fact_id,f.provenance_class,f.status FROM facts f JOIN fact_evidence e "
            "ON f.fact_id=e.fact_id JOIN signals s ON s.signal_id=e.signal_id "
            "WHERE s.connector=? AND s.account_scope=? AND s.upstream_id=?", (connector, scope, upstream)).fetchall()
        for fact in facts:
            if fact['provenance_class'] == Provenance.USER_CONFIRMED.value:
                continue
            independent = self.conn.execute(
                "SELECT 1 FROM fact_evidence e JOIN signals s ON s.signal_id=e.signal_id WHERE e.fact_id=? "
                "AND NOT (s.connector=? AND s.account_scope=? AND s.upstream_id=?)",
                (fact['fact_id'], connector, scope, upstream)).fetchone()
            if independent:
                self.set_fact_status(fact['fact_id'], FactStatus(fact['status']), withdraw_signals=sources)
            elif fact['status'] != FactStatus.UNKNOWN.value:
                # Retain the last evidence for explanation, but never equate cancellation with completion.
                self.set_fact_status(fact['fact_id'], FactStatus.UNKNOWN)
        for sid in sources:
            self.set_processing_state(sid, ProcessingState.EXCLUDED, "provider calendar event cancelled")
        with self.transaction() as db:
            db.execute("DELETE FROM calendar_records WHERE connector=? AND account_scope=? AND upstream_id=?",
                       (connector, scope, upstream))
            db.execute("INSERT OR REPLACE INTO source_heads VALUES(?,?,?,NULL,1)", (connector, scope, upstream))
            db.execute("UPDATE calendar_cancellations SET pending=0 "
                       "WHERE connector=? AND account_scope=? AND upstream_id=?",
                       (connector, scope, upstream))

    def resume_pending_cancellations(self) -> None:
        for row in self.conn.execute("SELECT connector,account_scope,upstream_id FROM calendar_cancellations "
                                     "WHERE pending=1").fetchall():
            self.cancel_calendar(*row)

    def calendar_for(self, viewer: str) -> list[Any]:
        self._require_unlocked()
        from datetime import date  # noqa: PLC0415

        from catchup_core.policy import authorize_source  # noqa: PLC0415
        from catchup_core.triage import CalendarEvent  # noqa: PLC0415
        output: list[CalendarEvent] = []
        if viewer != self._owner or self._cipher is None:
            return output
        self.require_personal_calendar_admission()
        if not self.conn.execute("SELECT 1 FROM calendar_records r WHERE NOT EXISTS "
            "(SELECT 1 FROM calendar_cancellations c WHERE c.connector=r.connector "
            "AND c.account_scope=r.account_scope AND c.upstream_id=r.upstream_id AND c.pending=1) "
            "LIMIT 1").fetchone():
            return output
        for row in self.conn.execute("SELECT r.* FROM calendar_records r WHERE NOT EXISTS "
            "(SELECT 1 FROM calendar_cancellations c WHERE c.connector=r.connector "
            "AND c.account_scope=r.account_scope AND c.upstream_id=r.upstream_id AND c.pending=1)"):
            try:
                authorize_source(self, tuple(self._consent_subjects), self._consent_subjects,
                                 f"{row['connector']}:{row['account_scope']}", viewer)
            except PermissionDenied:
                continue
            data = self._cipher.decrypt_json(row["payload_encrypted"],
                aad=f"calendar:{row['connector']}:{row['account_scope']}:{row['upstream_id']}".encode())
            parse = date.fromisoformat if data["all_day"] else datetime.fromisoformat
            output.append(CalendarEvent(data["id"], parse(data["start"]), parse(data["end"]),
                                        data["all_day"], data["kind"], row["signal_id"]))
        return output

    def next_metric_sequence(self, catchup_id: str, *, implementation_version: str = '') -> int:
        """Reserve a telemetry sequence before work, so a lost append remains detectable on resume."""
        with self.transaction() as conn:
            conn.execute('INSERT INTO metric_sequences VALUES(?,?,1,?) '
                         'ON CONFLICT(catchup_id,implementation_version) DO UPDATE '
                         'SET last_sequence=last_sequence+1,updated_at=excluded.updated_at',
                         (catchup_id, implementation_version, self._clock()))
            return int(conn.execute('SELECT last_sequence FROM metric_sequences '
                                    'WHERE catchup_id=? AND implementation_version=?',
                                    (catchup_id, implementation_version)).fetchone()[0])

    def draft(self, catchup_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._require_unlocked()
        if self._cipher is None:
            raise LedgerError("Briefing drafts require encryption")
        row = self.conn.execute("SELECT payload_encrypted FROM briefing_drafts WHERE catchup_id=?",
                                (catchup_id,)).fetchone()
        if row:
            return dict(self._cipher.decrypt_json(row[0], aad=f"briefing:{catchup_id}".encode()))
        if payload is not None:
            text, version = self._cipher.encrypt_json(payload, aad=f"briefing:{catchup_id}".encode())
            with self.transaction() as c:
                c.execute("INSERT INTO briefing_drafts VALUES(?,?,?,?)",
                          (catchup_id, text, version, self._clock()))
        return payload

    # ── pipeline working set ──────────────────────────────────────────────────
    def signals_in_state(self, state: ProcessingState) -> list[sqlite3.Row]:
        # Gate on identity first: a full row names the evidence blob, so only admitted rows are read in full.
        admitted: list[str] = []
        withheld = 0
        for (signal_id,) in self.conn.execute(
                "SELECT signal_id FROM signals WHERE processing_state=? ORDER BY signal_id", (state.value,)).fetchall():
            try:
                self._require_signal_admitted(str(signal_id))
            except PersonalScopeUnavailable:
                withheld += 1
                continue
            admitted.append(str(signal_id))
        self.report_withheld("signals_in_state", withheld, len(admitted), "Signal")
        return self.conn.execute(
            "SELECT * FROM signals WHERE processing_state=? AND signal_id IN (SELECT value FROM json_each(?)) "
            "ORDER BY signal_id", (state.value, json.dumps(admitted))).fetchall()

    def signal_row(self, signal_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
        return row

    def put_candidates(self, signal_id: str, payloads: Sequence[dict[str, Any]]) -> None:
        """Persist extracted candidates (encrypted) so RECONCILE can resume after a crash."""
        self._require_unlocked()
        if self._cipher is None:
            raise LedgerError("candidates are stored encrypted; no key available")
        with self.transaction() as c:
            c.execute("DELETE FROM candidates WHERE signal_id=?", (signal_id,))
            for idx, payload in enumerate(payloads):
                text, version = self._cipher.encrypt_json(payload, aad=f"cand:{signal_id}:{idx}".encode())
                c.execute("INSERT INTO candidates(signal_id,idx,payload_encrypted,key_version) VALUES(?,?,?,?)",
                          (signal_id, idx, text, version))

    def candidates_for(self, signal_id: str) -> list[dict[str, Any]]:
        self._require_signal_admitted(signal_id)
        if self._cipher is None:
            raise LedgerError("candidates are stored encrypted; no key available")
        out: list[dict[str, Any]] = []
        for row in self.conn.execute(
                "SELECT idx, payload_encrypted FROM candidates WHERE signal_id=? ORDER BY idx", (signal_id,)):
            payload = self._cipher.decrypt_json(row["payload_encrypted"],
                                                aad=f"cand:{signal_id}:{row['idx']}".encode())
            out.append(dict(payload))
        return out

    def record_failure(self, signal_id: str, stage: str, error_class: str) -> None:
        now = self._clock()
        with self.transaction() as c:
            c.execute(
                "INSERT INTO signal_failures(signal_id,stage,error_class,first_attempt,last_attempt) "
                "VALUES(?,?,?,?,?) ON CONFLICT(signal_id,stage) DO UPDATE SET attempts=attempts+1, "
                "last_attempt=excluded.last_attempt, error_class=excluded.error_class",
                (signal_id, stage, error_class, now, now))

    def failed_signals(self) -> list[str]:
        return [str(r[0]) for r in self.conn.execute("SELECT DISTINCT signal_id FROM signal_failures")]

    def clear_failure(self, signal_id: str, stage: str) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM signal_failures WHERE signal_id=? AND stage=?", (signal_id, stage))

    def set_coverage(self, connector: str, account_scope: str, status: str, blind_spots: Sequence[str] = (),
                     completed_at: str | None = None, catchup_id: str | None = None,
                     requested_from: str | None = None, requested_to: str | None = None,
                     cursor_generation: str | None = None, reset_cursor: bool = False,
                     record_count: int = 0) -> None:
        if type(record_count) is not int or record_count < 0:
            raise LedgerError("coverage record_count requires a nonnegative integer")
        with self.transaction() as c:
            spots = list(blind_spots)
            cap_marker = self._coverage_hold_cap_spot(connector)
            existing = c.execute(
                "SELECT blind_spots FROM connector_coverage WHERE connector=? AND account_scope=?",
                (connector, account_scope)).fetchone()
            try:
                recorded = list(json.loads(existing[0])) if existing is not None else []
            except (TypeError, ValueError):
                recorded = []
            capped = (
                cap_marker in recorded
                and c.execute(
                    "SELECT COUNT(*) FROM coverage_holds WHERE source_name=? AND state='held'",
                    (connector,)).fetchone()[0] >= _COVERAGE_HOLD_LIMIT
            )
            if capped:
                if cap_marker not in spots:
                    spots.append(cap_marker)
                status = CoverageStatus.DEGRADED.value
                cursor_generation = None
            if reset_cursor:
                cursor_clause = "NULL"
                cursor_val = None
            else:
                cursor_clause = "COALESCE(excluded.cursor_generation, connector_coverage.cursor_generation)"
                cursor_val = cursor_generation
            c.execute(
                f"INSERT INTO connector_coverage(connector,account_scope,status,blind_spots,completed_at,"
                f"catchup_id,checked_at,requested_from,requested_to,cursor_generation,record_count) "
                f"VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                f"ON CONFLICT(connector,account_scope) DO UPDATE SET status=excluded.status, "
                f"blind_spots=excluded.blind_spots, completed_at=COALESCE(excluded.completed_at,"
                f"connector_coverage.completed_at),catchup_id=excluded.catchup_id,checked_at=excluded.checked_at,"
                f"requested_from=excluded.requested_from,requested_to=excluded.requested_to,"
                f"cursor_generation={cursor_clause},record_count=excluded.record_count",
                (connector, account_scope, status, json.dumps(spots), completed_at,
                 catchup_id, self._clock(), requested_from, requested_to, cursor_val, record_count))

    def coverage_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM connector_coverage ORDER BY connector, account_scope").fetchall()

    def get_coverage(self, connector: str, account_scope: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM connector_coverage WHERE connector=? AND account_scope=?",
            (connector, account_scope)).fetchone()
        return row

    def get_cursor(self, connector: str, account_scope: str) -> str | None:
        row = self.get_coverage(connector, account_scope)
        return str(row["cursor_generation"]) if row and row["cursor_generation"] is not None else None

    def cancel_calendar_collection(self, connector: str, account_scope: str, collection_id: str) -> int:
        """Purge and withdraw records from a deleted calendar or collection."""
        # Match the exact JSON-encoded collection element (no SQL wildcards), then withdraw each event
        # through the same journaled path as a single cancellation so Facts and Signals follow.
        encoded = json.dumps(str(collection_id))
        upstream_ids = [r[0] for r in self.conn.execute(
            "SELECT upstream_id FROM signals WHERE connector=? AND account_scope=? AND upstream_id LIKE 'calendar:[%' "
            "UNION SELECT upstream_id FROM calendar_records WHERE connector=? AND account_scope=?",
            (connector, account_scope, connector, account_scope))]
        matching = [u for u in upstream_ids if u.startswith("calendar:[" + encoded + ",")]
        for upstream in matching:
            self.cancel_calendar(connector, account_scope, upstream)
        return len(matching)

    def _unlisted_calendar_upstreams(
            self, connector: str, account_scope: str, collection_id: str, listed: Collection[str]) -> list[str]:
        prefix = "calendar:[" + json.dumps(str(collection_id)) + ","
        return [str(row[0]) for row in self.conn.execute(
            "SELECT upstream_id FROM calendar_records WHERE connector=? AND account_scope=?",
            (connector, account_scope)) if str(row[0]).startswith(prefix) and row[0] not in listed]

    def withdraw_unlisted_calendar_occurrences(
            self, connector: str, account_scope: str, collection_id: str, listed: Collection[str],
            window_start: str | None = None, window_end: str | None = None) -> int:
        """Drop calendar occurrences a COMPLETE full listing did not contain.

        Only the encrypted ``calendar_records`` row goes. Signals, Facts and obligations are untouched: omission is
        not evidence that anything was completed or cancelled, and an event the provider lists again is restored
        by the normal path.  With a window, only occurrences wholly inside it are considered (all-day ones with a
        day of margin), so a window listing never judges events it could not have shown.
        """
        self._require_unlocked()
        if self._cipher is None:
            return 0
        self.require_personal_calendar_admission()
        if not self._unlisted_calendar_upstreams(connector, account_scope, collection_id, listed):
            return 0
        from datetime import date as _date  # noqa: PLC0415
        prefix = "calendar:[" + json.dumps(str(collection_id)) + ","
        lower = datetime.fromisoformat(window_start) if window_start else None
        upper = datetime.fromisoformat(window_end) if window_end else None
        withdrawn = 0
        for row in self.conn.execute("SELECT upstream_id,payload_encrypted FROM calendar_records "
                                     "WHERE connector=? AND account_scope=?", (connector, account_scope)).fetchall():
            upstream = str(row["upstream_id"])
            if not upstream.startswith(prefix) or upstream in listed:
                continue
            data = self._cipher.decrypt_json(
                row["payload_encrypted"], aad=f"calendar:{connector}:{account_scope}:{upstream}".encode())
            if lower is not None or upper is not None:
                if data["all_day"]:
                    start_day, end_day = _date.fromisoformat(data["start"]), _date.fromisoformat(data["end"])
                    inside = ((lower is None or start_day > lower.date())
                              and (upper is None or end_day < upper.date()))
                else:
                    start, end = datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
                    inside = (lower is None or start >= lower) and (upper is None or end <= upper)
                if not inside:
                    continue
            with self.transaction() as db:
                db.execute("DELETE FROM calendar_records WHERE connector=? AND account_scope=? AND upstream_id=?",
                           (connector, account_scope, upstream))
            withdrawn += 1
        return withdrawn

    def set_signal_subjects(self, signal_id: str, subjects: Sequence[str]) -> None:
        """Retain the strictest resolved attribution for an immutable Signal version; this grants no access."""
        if any(not isinstance(subject, str) or not subject for subject in subjects):
            raise ValueError("Signal subjects need resolved identities")
        with self.transaction() as db:
            old = db.execute("SELECT subjects FROM signal_access WHERE signal_id=?", (signal_id,)).fetchone()
            combined = sorted(set(subjects) | (set(json.loads(old[0])) if old else set()))
            db.execute("INSERT OR REPLACE INTO signal_access VALUES(?,?)", (signal_id, json.dumps(combined)))

    def set_source_subjects(self, connector: str, scope: str, subjects: Sequence[str]) -> None:
        """Bind a trusted source scope, including empty fetches, without assuming household-wide consent."""
        if any(not isinstance(subject, str) or not subject for subject in subjects):
            raise ValueError("Source subjects need resolved identities")
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO source_access VALUES(?,?,?)",
                       (connector, scope, json.dumps(sorted(set(subjects)))))

    def require_signal_operation(self, signal_id: str, viewer: str, operation: str = "view") -> None:
        """Authorize raw evidence and evaluations before disclosure, including legacy Fact provenance."""
        if not self._signal_reviewed(self.conn, signal_id):
            self.require_personal_signal_admission()
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        source = self.signal_row(signal_id)
        if (source is None or source['exclusion_reason'] == 'forgotten'
                or self.is_tombstoned(source['connector'], source['account_scope'], source['upstream_id'],
                                      source['content_hmac'])):
            raise PermissionDenied("source evidence is unavailable")
        row = self.conn.execute("SELECT subjects FROM signal_access WHERE signal_id=?", (signal_id,)).fetchone()
        subjects = set(json.loads(row[0])) if row else set()
        refs = self.conn.execute("SELECT fact_id FROM fact_evidence WHERE signal_id=? UNION "
                                 "SELECT fact_id FROM fact_version_evidence WHERE signal_id=?",
                                 (signal_id, signal_id)).fetchall()
        for ref in refs:
            fact = self.require_fact_operation(ref[0], viewer, operation)
            subjects.add(fact.primary_subject_id)
            subjects.update(r[0] for r in self.conn.execute("SELECT subject_id FROM fact_subjects WHERE fact_id=?",
                                                           (ref[0],)))
        if not subjects:
            raise PermissionDenied("source subject provenance is unknown")
        authorize_source(self, sorted(subjects), self._consent_subjects,
                         f"{source['connector']}:{source['account_scope']}", viewer, operations=(operation,))

    def visible_coverage(self, viewer: str) -> list[sqlite3.Row]:
        """No source identity, failure, count or timing escapes a current source-view check."""
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        visible = []
        for coverage in self.coverage_rows():
            row = self.conn.execute("SELECT subjects FROM source_access WHERE connector=? AND account_scope=?",
                                    (coverage['connector'], coverage['account_scope'])).fetchone()
            if not row or not json.loads(row[0]):
                continue
            try:
                authorize_source(self, json.loads(row[0]), self._consent_subjects,
                    f"{coverage['connector']}:{coverage['account_scope']}", viewer, operations=('view',))
                for signal in self.conn.execute("SELECT signal_id FROM signals WHERE connector=? AND account_scope=?",
                                                 (coverage['connector'], coverage['account_scope'])):
                    self.require_signal_operation(signal[0], viewer)
            except PersonalScopeUnavailable:
                raise
            except PermissionDenied:
                continue
            visible.append(coverage)
        return visible

    def purge_corpus(self, case_ids: Sequence[str]) -> None:
        """Delete only journaled encrypted case identities; safe to repeat after a crash."""
        import re  # noqa: PLC0415
        for case_id in case_ids:
            if not re.fullmatch(r"case_[A-Za-z0-9_-]{1,64}", case_id):
                raise LedgerError("invalid corpus identity in erasure journal")
            (self._path.parent / 'corpus' / f'{case_id}.ae1').unlink(missing_ok=True)
            for result in (self._path.parent / 'corpus' / 'evaluations').glob(f'{case_id}_*.ae1'):
                result.unlink(missing_ok=True)

    def publish_briefing(self, catchup_id: str, text: str, *, correction: bool = False) -> tuple[int, bool]:
        """One logical Briefing per Catch-up ID.

        A repeat of identical text returns the existing version; different text is
        refused unless it is an explicit versioned correction.
        """
        self._require_unlocked()
        digest = ids.content_hmac(self._id_key, text)
        with self.transaction() as c:
            row = c.execute("SELECT version, text_hmac FROM briefings WHERE catchup_id=? AND status='published'",
                            (catchup_id,)).fetchone()
            if row is not None:
                if row["text_hmac"] == digest:
                    return int(row["version"]), False
                if not correction:
                    raise LedgerError(f"{catchup_id} already has a published Briefing; "
                                      "a different text needs an explicit correction")
                c.execute("UPDATE briefings SET status='superseded' WHERE catchup_id=? AND version=?",
                          (catchup_id, row["version"]))
                version = int(row["version"]) + 1
            else:
                if c.execute("SELECT 1 FROM briefings WHERE catchup_id=?", (catchup_id,)).fetchone():
                    raise LedgerError("Briefing was invalidated; start a new Catch-up ID")
                version = 1
            c.execute("INSERT INTO briefings(catchup_id,version,text_hmac,published_at,status) "
                      "VALUES(?,?,?,?,'published')", (catchup_id, version, digest, self._clock()))
        return version, True

    # ── alerts ───────────────────────────────────────────────────────────────
    def upsert_alert(
        self, obligation_key: str, *, domain: str, primary_subject_id: str, severity: str,
        deadline: str | None = None, latest_useful_action: str | None = None, fact_ids: Sequence[str] = (),
        viewers: Sequence[str] = (),
    ) -> tuple[str, bool]:
        """One Alert per obligation: repeats update it instead of duplicating."""
        self._require_unlocked()
        now = self._clock()
        with self.transaction() as c:
            row = c.execute("SELECT alert_id FROM alerts WHERE obligation_key=?", (obligation_key,)).fetchone()
            created = row is None
            alert_id = ids.new_id("alt") if row is None else str(row["alert_id"])
            previous = self.get_alert(obligation_key) if row else None
            if (deadline or latest_useful_action) and self._cipher is None:
                raise LedgerError("Alert dates require encryption")
            protected, key_version = (self._cipher.encrypt_json(
                {"deadline": deadline, "latest_useful_action": latest_useful_action},
                aad=f"alert:{alert_id}".encode()) if self._cipher else (None, None))
            if created:
                c.execute(
                    "INSERT INTO alerts(alert_id,obligation_key,domain,primary_subject_id,severity,deadline,"
                    "latest_useful_action,status,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,'open',?,?)",
                    (alert_id, obligation_key, domain, primary_subject_id, severity, None,
                     None, now, now))
            else:
                if previous and previous["deadline"] != deadline:
                    c.execute("UPDATE alerts SET status='open',suppressed_until=NULL,resolution_ref=NULL "
                              "WHERE alert_id=?", (alert_id,))
                c.execute("UPDATE alerts SET severity=?,deadline=?,latest_useful_action=?,last_seen=? "
                          "WHERE alert_id=?", (severity, None, None, now, alert_id))
            c.execute("UPDATE alerts SET dates_encrypted=?,dates_key_version=? WHERE alert_id=?",
                      (protected, key_version, alert_id))
            for fid in fact_ids:
                c.execute("INSERT OR IGNORE INTO alert_facts(alert_id,fact_id) VALUES(?,?)", (alert_id, fid))
            if fact_ids:
                c.execute("UPDATE alerts SET evidence_version=(SELECT recorded_at FROM facts WHERE fact_id=?) "
                          "WHERE alert_id=?", (fact_ids[0], alert_id))
            for viewer in viewers:
                c.execute("INSERT OR IGNORE INTO alert_viewers(alert_id,viewer_id) VALUES(?,?)",
                          (alert_id, viewer))
        return alert_id, created

    def resolve_alert(self, obligation_key: str, resolution_ref: str) -> None:
        self._require_unlocked()
        with self.transaction() as c:
            cur = c.execute("UPDATE alerts SET status='resolved', resolution_ref=?, last_seen=? "
                            "WHERE obligation_key=?", (resolution_ref, self._clock(), obligation_key))
            if cur.rowcount != 1:
                raise LedgerError(f"unknown obligation {obligation_key}")

    def get_alert(self, obligation_key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM alerts WHERE obligation_key=?", (obligation_key,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        if out.get("dates_encrypted"):
            if self._cipher is None:
                raise LedgerError("Alert dates require encryption")
            out.update(self._cipher.decrypt_json(out.pop("dates_encrypted"), aad=f"alert:{out['alert_id']}".encode()))
        return out

    # ── open items (first record type with structured authority, D-07) ─────────
    def put_open_item(
        self, item_id: str, *, owner: str, description: str, deadline: str | None = None,
        state: str = "open", source_alert_id: str | None = None, legacy_source_ref: str | None = None,
    ) -> None:
        self._require_unlocked()
        if self._cipher is None:
            raise LedgerError("open item text is stored encrypted; no key available")
        text, version = self._cipher.encrypt_json(description, aad=f"oi:{item_id}".encode())
        now = self._clock()
        with self.transaction() as c:
            c.execute(
                "INSERT INTO open_items(item_id,owner,deadline,state,description_encrypted,key_version,"
                "source_alert_id,created_at,updated_at,legacy_source_ref) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(item_id) DO UPDATE SET owner=excluded.owner, deadline=excluded.deadline, "
                "state=excluded.state, description_encrypted=excluded.description_encrypted, "
                "key_version=excluded.key_version, updated_at=excluded.updated_at",
                (item_id, owner, deadline, state, text, version, source_alert_id, now, now, legacy_source_ref))

    def get_open_item(self, item_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM open_items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        if self._cipher is None:
            raise LedgerError("open item text is encrypted; no key available")
        out["description"] = self._cipher.decrypt_json(out.pop("description_encrypted"),
                                                       aad=f"oi:{item_id}".encode())
        return out

    # ── consent ──────────────────────────────────────────────────────────────
    def grant_consent(self, subject_id: str, grantor_id: str, scope: str, operations: Sequence[str],
                      audience: str, expires_at: str | None = None) -> str:
        self._require_unlocked()
        if subject_id in self._consent_subjects and grantor_id != subject_id:
            raise PermissionDenied("an adult subject must grant their own consent")
        if grantor_id not in (subject_id, self._owner):
            raise PermissionDenied("only the subject or the household owner may grant consent")
        if expires_at:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise ValueError("consent expiry requires a UTC offset")
            expires_at = expiry.astimezone(UTC).isoformat()
        consent_id = ids.new_id("con")
        with self.transaction() as c:
            c.execute(
                "INSERT INTO consent(consent_id,subject_id,grantor_id,scope,operations,audience,granted_at,"
                "expires_at) VALUES(?,?,?,?,?,?,?,?)",
                (consent_id, subject_id, grantor_id, scope, json.dumps(sorted(operations)), audience,
                 self._clock(), expires_at))
        return consent_id

    def revoke_consent(self, consent_id: str) -> None:
        self._require_unlocked()
        with self.transaction() as c:
            previous_generation = self._permission_generation()
            cur = c.execute(
                "UPDATE consent SET revoked_at=?, revocation_version=revocation_version+1 "
                "WHERE consent_id=? AND revoked_at IS NULL", (self._clock(), consent_id))
            if cur.rowcount != 1:
                raise LedgerError(f"unknown or already revoked consent {consent_id}")
            self._refresh_personal_scope_bindings(c, previous_generation)
        self.seal_privacy()

    def privacy_history(self) -> dict[str, Any]:
        return {"tombstones": self.tombstones(),
                "consent": [dict(r) for r in self.conn.execute("SELECT * FROM consent")]}

    def seal_privacy(self) -> SealedCheckpoint:
        self._require_unlocked()
        with self.transaction() as c:
            seq = int(c.execute("SELECT COALESCE(MAX(checkpoint_id),0)+1 FROM deletion_checkpoint").fetchone()[0])
            checkpoint = SealedCheckpoint(seq, self._tombstone_digest(c), self._permission_generation())
            c.execute("INSERT INTO deletion_checkpoint VALUES(?,?,?,?)",
                      (seq, self._clock(), checkpoint.tombstone_digest, checkpoint.permission_generation))
        self._sealed.write(checkpoint)
        return checkpoint

    def consent_active(self, subject_id: str, operation: str, audience: str, scope: str | None = None) -> bool:
        self._require_unlocked()
        from catchup_core.fact_access import AccessPolicy, consent_active  # noqa: PLC0415
        return consent_active(self.conn, AccessPolicy(self._owner, self._consent_subjects, self._known_subjects),
                              subject_id, operation, audience, scope, self._clock())

    # ── forgetting (anti-resurrection, §7.9) ──────────────────────────────────
    def _tombstone_digest(self, conn: sqlite3.Connection) -> str:
        rows = conn.execute("SELECT tombstone_id FROM tombstones ORDER BY tombstone_id").fetchall()
        return hashlib.sha256("\n".join(str(r[0]) for r in rows).encode()).hexdigest()

    def _permission_generation(self) -> int:
        row = self.conn.execute("SELECT COALESCE(SUM(revocation_version),0) FROM consent").fetchone()
        return int(row[0])

    def forget(self, connector: str, account_scope: str, upstream_id: str, *,
               content_hmac: str | None = None, reason: str = "user request") -> ForgetReceipt:
        """Journal → seal → deny → delete → receipt; an interrupted run resumes from the journal."""
        self._require_unlocked()
        tombstone_id = ids.new_id("tmb")
        with self.transaction() as c:
            c.execute("INSERT INTO tombstones(tombstone_id,connector,account_scope,upstream_id,content_hmac,"
                      "created_at,reason) VALUES(?,?,?,?,?,?,?)",
                      (tombstone_id, connector, account_scope, upstream_id, content_hmac, self._clock(), reason))
            cur = c.execute("INSERT INTO deletion_journal(tombstone_id,requested_at,state) VALUES(?,?,'requested')",
                            (tombstone_id, self._clock()))
            seq = int(cur.lastrowid or 0)
        return self._complete_forget(seq, tombstone_id)

    def expire_content(self, content_hmac: str) -> ForgetReceipt:
        """Retention expiry of one exact content version through the deletion journal (re-artha §7.9).

        The tombstone carries the keyed content hash only, so identical content cannot return while a
        changed version of the same upstream object stays admissible. Old restores replay it like a forget.
        """
        return self._expire("retention:source", None, None, None, content_hmac)

    def expire_replay(self, feedback_id: str) -> ForgetReceipt:
        """Retention expiry of one replay bundle; an old restore cannot bring it back."""
        return self._expire("retention:replay", RETENTION_CONNECTOR, RETENTION_REPLAY_SCOPE, feedback_id, None)

    def _expire(self, reason: str, connector: str | None, scope: str | None, upstream: str | None,
                content_hmac: str | None) -> ForgetReceipt:
        self._require_unlocked()
        if self.conn.execute("SELECT 1 FROM tombstones WHERE reason=? AND connector IS ? AND account_scope IS ? "
                             "AND upstream_id IS ? AND content_hmac IS ?",
                             (reason, connector, scope, upstream, content_hmac)).fetchone():
            raise LedgerError("this retention expiry is already recorded; resume pending erasure instead")
        tombstone_id = ids.new_id("tmb")
        with self.transaction() as c:
            c.execute("INSERT INTO tombstones(tombstone_id,connector,account_scope,upstream_id,content_hmac,"
                      "created_at,reason) VALUES(?,?,?,?,?,?,?)",
                      (tombstone_id, connector, scope, upstream, content_hmac, self._clock(), reason))
            cur = c.execute("INSERT INTO deletion_journal(tombstone_id,requested_at,state) VALUES(?,?,'requested')",
                            (tombstone_id, self._clock()))
            seq = int(cur.lastrowid or 0)
        return self._complete_forget(seq, tombstone_id)

    def _complete_forget(self, seq: int, tombstone_id: str) -> ForgetReceipt:
        row = self.conn.execute("SELECT * FROM tombstones WHERE tombstone_id=?", (tombstone_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown tombstone {tombstone_id}")
        state = self.conn.execute("SELECT state FROM deletion_journal WHERE seq=?", (seq,)).fetchone()[0]
        order = ["requested", "sealed", "denied", "deleted", "receipted"]
        stage = order.index(state)

        if stage < 1:   # seal the checkpoint outside the restorable set
            with self.transaction() as c:
                checkpoint_id = int(c.execute("SELECT COALESCE(MAX(checkpoint_id),0)+1 FROM deletion_checkpoint")
                                    .fetchone()[0])
                digest = self._tombstone_digest(c)
                generation = self._permission_generation()
                c.execute("INSERT INTO deletion_checkpoint VALUES(?,?,?,?)",
                          (checkpoint_id, self._clock(), digest, generation))
            self._sealed.write(SealedCheckpoint(checkpoint_id, digest, generation))
            self._set_journal(seq, "sealed")
        if stage < 2:   # deny: matching signals can no longer be used
            self.conn.execute(
                "UPDATE signals SET processing_state='excluded', exclusion_reason='forgotten' "
                "WHERE (connector=? AND account_scope=? AND upstream_id=?) "
                "OR (content_hmac IS NOT NULL AND content_hmac=?)",
                (row["connector"], row["account_scope"], row["upstream_id"], row["content_hmac"]))
            self._set_journal(seq, "denied")
        deleted_signals = deleted_facts = deleted_alerts = 0
        blob_refs: list[str] = []
        if stage < 3:   # physical deletion, including derived Facts and Alerts
            detail = self.conn.execute("SELECT detail FROM deletion_journal WHERE seq=?", (seq,)).fetchone()[0]
            pending = json.loads(detail) if detail else {}
            if isinstance(pending, list):  # recovery of the first journal format
                pending = {"blobs": pending}
            if not pending:
                signals = list(self.conn.execute(
                    "SELECT signal_id,evidence_blob_ref FROM signals WHERE "
                    "(connector=? AND account_scope=? AND upstream_id=?) OR content_hmac=?",
                    (row["connector"], row["account_scope"], row["upstream_id"], row["content_hmac"])))
                briefings = {r[0] for signal in signals for r in self.conn.execute(
                    "SELECT DISTINCT bf.catchup_id FROM briefing_facts bf JOIN fact_evidence fe "
                    "ON bf.fact_id=fe.fact_id WHERE fe.signal_id=? UNION "
                    "SELECT catchup_id FROM briefing_signals WHERE signal_id=?", (signal[0], signal[0]))}
                replay = sorted({r[0] for signal in signals for r in self.conn.execute(
                    "SELECT feedback_id FROM replay_cases WHERE signal_id=?", (signal[0],))})
                corpus = sorted({"case_" + str(signal[0]).removeprefix("sig_")[:16] for signal in signals}
                    | {r[0] for signal in signals for r in self.conn.execute(
                        "SELECT case_id FROM corpus_cases WHERE signal_id=?", (signal[0],))})
                if row["connector"] == RETENTION_CONNECTOR and row["account_scope"] == RETENTION_REPLAY_SCOPE:
                    replay = sorted({*replay, str(row["upstream_id"])})
                pending = {"blobs": [s[1] for s in signals], "briefings": sorted(briefings), "replay": replay,
                           "corpus": corpus}
                self.conn.execute("UPDATE deletion_journal SET detail=? WHERE seq=?", (json.dumps(pending), seq))
            deleted_signals, deleted_facts, deleted_alerts, blob_refs = self._delete_forgotten(row)
            if self._blob_deleter is not None:
                for ref in pending["blobs"]:
                    self._blob_deleter(ref)
            if self._artifact_deleter is not None:
                self._artifact_deleter(pending.get("briefings", []))
            if self._replay_deleter is not None:
                self._replay_deleter(pending.get("replay", []))
            for feedback_id in pending.get("replay", []):
                self.conn.execute("DELETE FROM replay_cases WHERE feedback_id=?", (feedback_id,))
            self.purge_corpus(pending.get("corpus", []))
            self._set_journal(seq, "deleted")
        self._set_journal(seq, "receipted")
        return ForgetReceipt(
            tombstone_id=tombstone_id, deleted_signals=deleted_signals, deleted_facts=deleted_facts,
            deleted_alerts=deleted_alerts,
            minimal_audit="tombstone key only (connector, scope, upstream id, keyed content hash)",
            backup_copies_pending_expiry=True, journal_seq=seq, blob_refs_deleted=tuple(blob_refs))

    def _set_journal(self, seq: int, state: str) -> None:
        self.conn.execute("UPDATE deletion_journal SET state=? WHERE seq=?", (state, seq))

    def _delete_forgotten(self, row: sqlite3.Row) -> tuple[int, int, int, list[str]]:
        with self.transaction() as c:
            query = ("SELECT signal_id, evidence_blob_ref FROM signals "
                     "WHERE (connector=? AND account_scope=? AND upstream_id=?)")
            params: list[Any] = [row["connector"], row["account_scope"], row["upstream_id"]]
            if row["content_hmac"]:
                query += " OR content_hmac=?"
                params.append(row["content_hmac"])
            sigs = c.execute(query, params).fetchall()
            signal_ids = [str(s["signal_id"]) for s in sigs]
            blob_refs = [str(s["evidence_blob_ref"]) for s in sigs]
            removed_facts = deleted_alerts = 0
            for sid in signal_ids:
                facts, alerts = purge_signal(c, sid)
                removed_facts += facts
                deleted_alerts += alerts
            c.execute("DELETE FROM briefing_drafts")  # cached claims may contain forgotten material
        return len(signal_ids), removed_facts, deleted_alerts, blob_refs

    def resume_pending_forgets(self) -> list[ForgetReceipt]:
        """Complete interrupted forget requests from the durable journal."""
        receipts: list[ForgetReceipt] = []
        rows = self.conn.execute(
            "SELECT seq, tombstone_id FROM deletion_journal WHERE state<>'receipted' ORDER BY seq").fetchall()
        for r in rows:
            receipts.append(self._complete_forget(int(r["seq"]), str(r["tombstone_id"])))
        return receipts

    # ── restore guard ────────────────────────────────────────────────────────
    def check_restore(self) -> bool:
        """Lock the ledger when it is older than the sealed checkpoint; True when current."""
        sealed = self._sealed.read()
        row = self.conn.execute("SELECT checkpoint_id, tombstone_digest FROM deletion_checkpoint "
                                "ORDER BY checkpoint_id DESC LIMIT 1").fetchone()
        have = int(row["checkpoint_id"]) if row else 0
        if sealed is not None and (sealed.checkpoint_id > have
                or sealed.permission_generation > self._permission_generation()
                or sealed.tombstone_digest != self._tombstone_digest(self.conn)):
            self.locked = True
            return False
        if isinstance(self._sealed, KeyringSealedStore) and sealed is None:
            self.locked = True
            return False
        self.locked = False
        return True

    def replay_tombstones(self, entries: Sequence[dict[str, Any]]) -> bool:
        """Apply tombstones from the replay journal to an older restore; unlock when it matches."""
        with self.transaction() as c:
            for e in entries:
                c.execute("INSERT OR IGNORE INTO tombstones(tombstone_id,connector,account_scope,upstream_id,"
                          "content_hmac,created_at,reason) VALUES(?,?,?,?,?,?,?)",
                          (e["tombstone_id"], e.get("connector"), e.get("account_scope"), e.get("upstream_id"),
                           e.get("content_hmac"), e.get("created_at", self._clock()), e.get("reason")))
        for r in self.conn.execute("SELECT * FROM tombstones").fetchall():
            _, _, _, blob_refs = self._delete_forgotten(r)
            if self._blob_deleter is not None:
                for ref in blob_refs:
                    self._blob_deleter(ref)
            if r["connector"] == RETENTION_CONNECTOR and r["account_scope"] == RETENTION_REPLAY_SCOPE:
                self.conn.execute("DELETE FROM replay_cases WHERE feedback_id=?", (r["upstream_id"],))
                if self._replay_deleter is not None:
                    self._replay_deleter([r["upstream_id"]])
        sealed = self._sealed.read()
        digest = self._tombstone_digest(self.conn)
        if sealed is not None and sealed.tombstone_digest != digest:
            self.locked = True          # unverifiable deletion history fails safe
            return False
        with self.transaction() as c:
            next_id = int(c.execute("SELECT COALESCE(MAX(checkpoint_id),0)+1 FROM deletion_checkpoint").fetchone()[0])
            if sealed is not None:
                next_id = max(next_id, sealed.checkpoint_id)
            c.execute("INSERT OR REPLACE INTO deletion_checkpoint VALUES(?,?,?,?)",
                      (next_id, self._clock(), digest, self._permission_generation()))
        self.locked = False
        return True

    def tombstones(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM tombstones ORDER BY tombstone_id")]

    # ── stage journal (§6.15) ────────────────────────────────────────────────
    def record_stage(self, cp: StageCheckpoint) -> None:
        self._require_unlocked()
        with self.transaction() as c:
            c.execute(
                "INSERT INTO stage_journal(catchup_id,stage,input_versions,status,artifact_ids,failure_class,"
                "updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(catchup_id,stage) DO UPDATE SET "
                "input_versions=excluded.input_versions, status=excluded.status, "
                "artifact_ids=excluded.artifact_ids, failure_class=excluded.failure_class, "
                "updated_at=excluded.updated_at",
                (cp.catchup_id, cp.stage.value, json.dumps(dict(cp.input_versions), sort_keys=True), cp.status,
                 json.dumps(list(cp.artifact_ids)), cp.failure_class.value if cp.failure_class else None,
                 self._clock()))

    def committed_stages(self, catchup_id: str) -> list[str]:
        return [str(r[0]) for r in self.conn.execute(
            "SELECT stage FROM stage_journal WHERE catchup_id=? AND status='committed'", (catchup_id,))]

    def integrity_check(self) -> bool:
        from catchup_core.schema_history import sources, verify  # noqa: PLC0415
        try:
            verify(self.conn, sources(self.available_migrations()), self.schema_version())
        except MigrationRefused:
            return False
        ok = self.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        fk = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        return ok and not fk

    # ── authority generation & transfer (§6.9, §12.13, RA-09, RA-20) ─────────
    def authority_state(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT generation, host, promoted_at, former_host_revoked_at "
            "FROM authority_generation ORDER BY generation DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"generation": 1, "host": None, "promoted_at": None, "former_host_revoked_at": None}
        return {
            "generation": int(row["generation"]),
            "host": str(row["host"]),
            "promoted_at": str(row["promoted_at"]),
            "former_host_revoked_at": row["former_host_revoked_at"],
        }

    def ensure_authority_host(self, host: str) -> dict[str, Any]:
        clean = host.strip()
        if not clean:
            raise PermissionDenied("authority host is required")
        state = self.authority_state()
        if state["host"] is None:
            if not self._read_only:
                with self.transaction() as c:
                    c.execute(
                        "INSERT OR IGNORE INTO authority_generation(generation, host, promoted_at, "
                        "former_host_revoked_at) VALUES(1, ?, ?, NULL)",
                        (clean, self._clock()),
                    )
                state = self.authority_state()
                if state["host"] != clean:
                    raise PermissionDenied("another host established the authority generation")
                return state
            return {"generation": 1, "host": clean, "promoted_at": None, "former_host_revoked_at": None}
        if state["host"] != clean:
            raise PermissionDenied(
                f"authority generation {state['generation']} belongs to {state['host']!r}, not {clean!r}"
            )
        return state


def invalidate_restored_scope_admission(db: sqlite3.Connection) -> None:
    """Invalidate admission authority restored from an older ledger image."""
    tables = {str(row[0]) for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "scope_bindings" in tables:
        db.execute("DELETE FROM scope_bindings WHERE provenance='source_review'")
    if "signal_source_enrollments" in tables:
        db.execute("DELETE FROM signal_source_enrollments")
    if "scope_admission_state" in tables:
        changed = db.execute(
            "UPDATE scope_admission_state SET epoch=epoch+1 WHERE singleton=1").rowcount
        if changed != 1:
            raise LedgerError("restored scope admission epoch is invalid")


def _split_statements(script: str) -> list[str]:
    """Use SQLite's statement parser so triggers and quoted semicolons remain one atomic statement."""
    statements: list[str] = []
    pending = ''
    for character in script:
        pending += character
        if character == ';' and sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ''
    if pending.strip():
        statements.append(pending.strip())
    return statements


def purge_signal(db: sqlite3.Connection, signal_id: str) -> tuple[int, int]:
    """Remove current and historical source claims while retaining independent support."""
    fact_ids = [r[0] for r in db.execute(
        "SELECT fact_id FROM fact_evidence WHERE signal_id=? UNION "
        "SELECT fact_id FROM fact_version_evidence WHERE signal_id=?", (signal_id, signal_id))]
    for fid in fact_ids:
        db.execute("DELETE FROM goal_observations WHERE goal_id IN "
                   "(SELECT goal_id FROM goal_observation_facts WHERE fact_id=?)", (fid,))
    db.execute("DELETE FROM fact_evidence WHERE signal_id=?", (signal_id,))
    db.execute("DELETE FROM fact_version_evidence WHERE signal_id=?", (signal_id,))
    removed = 0
    for fid in fact_ids:
        db.execute("DELETE FROM fact_versions WHERE fact_id=? AND provenance_class<>'user_confirmed' "
                   "AND NOT EXISTS(SELECT 1 FROM fact_version_evidence e WHERE e.fact_id=fact_versions.fact_id "
                   "AND e.recorded_at=fact_versions.recorded_at)", (fid,))
        removed += db.execute("DELETE FROM facts WHERE fact_id=? AND provenance_class<>'user_confirmed' "
                              "AND NOT EXISTS(SELECT 1 FROM fact_evidence e WHERE e.fact_id=facts.fact_id)",
                              (fid,)).rowcount
    db.execute("DELETE FROM feedback WHERE obligation_key IN (SELECT obligation_key FROM alerts "
               "WHERE NOT EXISTS(SELECT 1 FROM alert_facts af WHERE af.alert_id=alerts.alert_id))")
    alerts = db.execute("DELETE FROM alerts WHERE NOT EXISTS(SELECT 1 FROM alert_facts af "
                        "WHERE af.alert_id=alerts.alert_id)").rowcount
    db.execute("DELETE FROM signal_failures WHERE signal_id=?", (signal_id,))
    db.execute("DELETE FROM signals WHERE signal_id=?", (signal_id,))
    return removed, alerts
