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
import json
import os
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from catchup_core import ids
from catchup_core.contracts import (
    Confidentiality,
    FactStatus,
    ProcessingState,
    Provenance,
    StageCheckpoint,
)
from catchup_core.crypto import EnvelopeCipher

__all__ = [
    "FactInput", "FactView", "FileSealedStore", "ForgetReceipt", "Ledger", "LedgerError", "LedgerLocked",
    "MemorySealedStore", "MigrationRefused", "PermissionDenied", "SealedCheckpoint", "SealedStore",
    "SignalInput", "Suppressed",
]

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class LedgerError(RuntimeError):
    pass


class MigrationRefused(LedgerError):
    """A migration needs a verified backup and none was provided or it failed."""


class PermissionDenied(LedgerError):
    pass


class Suppressed(LedgerError):
    """The input was forgotten; it must not be re-ingested."""


class LedgerLocked(LedgerError):
    """A restored ledger is older than the sealed deletion checkpoint."""


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        clock: Callable[[], str] = _utc_now,
        blob_deleter: Callable[[str], None] | None = None,
    ):
        self._path = Path(path)
        self._id_key = id_key
        self._cipher = cipher
        self._sealed = sealed or MemorySealedStore()
        self._owner = owner_id
        # Adults other than the owner: restricted data about them needs their recorded consent
        # (RA-12 v0).  Minor children are governed by the household owner's authority.
        self._consent_subjects = frozenset(consent_subjects)
        self._clock = clock
        self._blob_deleter = blob_deleter
        self._conn: sqlite3.Connection | None = None
        self.locked = False
        self._guard = ids.CollisionGuard()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self) -> Ledger:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._path.parent, 0o700)
        conn = sqlite3.connect(str(self._path), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
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

    # ── migrations ───────────────────────────────────────────────────────────
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
        current = self.schema_version()
        pending = [(n, p) for n, p in self.available_migrations() if n > current]
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
        for number, path in pending:
            script = path.read_text(encoding="utf-8")
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split_statements(script):
                    self.conn.execute(statement)
                self.conn.execute(f"PRAGMA user_version={number}")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")
        return self.schema_version()

    # ── signals ──────────────────────────────────────────────────────────────
    def is_tombstoned(self, connector: str, account_scope: str, upstream_id: str,
                      content_hmac: str | None = None) -> bool:
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
                "occurred_at,observed_at,confidentiality,content_hmac,evidence_blob_ref,processing_state)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (signal_id, sig.connector, sig.account_scope, sig.upstream_id, sig.upstream_version,
                 sig.occurred_at, sig.observed_at, sig.confidentiality.value, sig.content_hmac,
                 sig.evidence_blob_ref, ProcessingState.FETCHED.value))
        return signal_id, True

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
            "ORDER BY signal_id").fetchall()
        return [str(r[0]) for r in rows]

    # ── facts ────────────────────────────────────────────────────────────────
    def _encode_value(self, fact_id: str, value: Any, confidentiality: Confidentiality) -> tuple[str, int, int | None]:
        if confidentiality is Confidentiality.RESTRICTED:
            if self._cipher is None:
                raise LedgerError("restricted values need an encryption key; refusing plaintext")
            text, version = self._cipher.encrypt_json(value, aad=fact_id.encode())
            return text, 1, version
        return json.dumps(value, sort_keys=True), 0, None

    def put_fact(self, fact: FactInput, fact_id: str | None = None) -> str:
        """Create a Fact, or add a new version of an existing one (history is append-only)."""
        self._require_unlocked()
        is_new = fact_id is None
        fid = fact_id or ids.new_id("fct")
        text, encrypted, key_version = self._encode_value(fid, fact.value, fact.confidentiality)
        now = self._clock()
        with self.transaction() as c:
            if not is_new:
                if not c.execute("SELECT 1 FROM facts WHERE fact_id=?", (fid,)).fetchone():
                    raise LedgerError(f"unknown fact {fid}")
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
                "validity_unknown,provenance_class,evidence_digest,typed_value,status,rule_version)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, now, encrypted, key_version, fact.valid_from, fact.valid_to, int(fact.validity_unknown),
                 fact.provenance.value, digest, text, fact.status.value, fact.rule_version))
            for subject, role in fact.co_subjects:
                c.execute("INSERT OR REPLACE INTO fact_subjects(fact_id,subject_id,role) VALUES(?,?,?)",
                          (fid, subject, role))
            for viewer in fact.viewers:
                c.execute("INSERT OR IGNORE INTO fact_viewers(fact_id,viewer_id) VALUES(?,?)", (fid, viewer))
            for signal_id, start, end in fact.evidence:
                c.execute("INSERT OR REPLACE INTO fact_evidence(fact_id,signal_id,span_start,span_end)"
                          " VALUES(?,?,?,?)", (fid, signal_id, start, end))
        return fid

    def _may_view(self, row: sqlite3.Row, viewer_id: str) -> bool:
        fact_id = row["fact_id"]
        allowed = (
            viewer_id == self._owner
            or viewer_id == row["primary_subject_id"]
            or bool(self.conn.execute(
                "SELECT 1 FROM fact_viewers WHERE fact_id=? AND viewer_id=?", (fact_id, viewer_id)).fetchone())
        )
        if not allowed:
            return False
        # Restricted data about a consent-required adult needs that person's recorded consent
        # for anyone other than that person.
        if (row["confidentiality"] == Confidentiality.RESTRICTED.value
                and row["primary_subject_id"] in self._consent_subjects
                and viewer_id != row["primary_subject_id"]):
            return self.consent_active(row["primary_subject_id"], "view", viewer_id)
        return True

    def get_fact(self, fact_id: str, viewer_id: str) -> FactView:
        self._require_unlocked()
        row = self.conn.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown fact {fact_id}")
        if not self._may_view(row, viewer_id):
            raise PermissionDenied(f"{viewer_id} may not view {fact_id}")
        value = self._decode_value(fact_id, row["typed_value"], bool(row["value_encrypted"]))
        evidence = tuple(
            (str(e["signal_id"]), int(e["span_start"]), int(e["span_end"]))
            for e in self.conn.execute(
                "SELECT signal_id,span_start,span_end FROM fact_evidence WHERE fact_id=? ORDER BY span_start",
                (fact_id,)))
        return FactView(
            fact_id=fact_id, domain=row["domain"], predicate=row["predicate"], value=value,
            primary_subject_id=row["primary_subject_id"], confidentiality=row["confidentiality"],
            provenance_class=row["provenance_class"], status=row["status"], valid_from=row["valid_from"],
            valid_to=row["valid_to"], validity_unknown=bool(row["validity_unknown"]),
            observed_at=row["observed_at"], recorded_at=row["recorded_at"], evidence=evidence)

    def _decode_value(self, fact_id: str, text: str, encrypted: bool) -> Any:
        if not encrypted:
            return json.loads(text)
        if self._cipher is None:
            raise LedgerError("encrypted value but no key available")
        return self._cipher.decrypt_json(text, aad=fact_id.encode())

    def fact_history(self, fact_id: str, viewer_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
        """What Artha believed over time ("what did Artha know then?")."""
        self.get_fact(fact_id, viewer_id)         # permission and existence
        query = "SELECT * FROM fact_versions WHERE fact_id=?"
        params: list[Any] = [fact_id]
        if as_of is not None:
            query += " AND recorded_at<=?"
            params.append(as_of)
        out: list[dict[str, Any]] = []
        for row in self.conn.execute(query + " ORDER BY recorded_at", params):
            out.append({
                "recorded_at": row["recorded_at"], "status": row["status"],
                "value": self._decode_value(fact_id, row["typed_value"], bool(row["value_encrypted"])),
                "provenance_class": row["provenance_class"], "valid_from": row["valid_from"],
                "valid_to": row["valid_to"], "key_version": row["key_version"]})
        return out

    def facts_for(self, domain: str, predicate: str, viewer_id: str,
                  statuses: Sequence[FactStatus] = (FactStatus.CURRENT,)) -> list[FactView]:
        marks = ",".join("?" for _ in statuses)
        rows = self.conn.execute(  # `marks` is only "?" placeholders; values are bound
            f"SELECT fact_id FROM facts WHERE domain=? AND predicate=? AND status IN ({marks}) "  # noqa: S608
            "ORDER BY recorded_at, fact_id", (domain, predicate, *[s.value for s in statuses])).fetchall()
        views: list[FactView] = []
        for r in rows:
            try:
                views.append(self.get_fact(r["fact_id"], viewer_id))
            except PermissionDenied:
                continue
        return views

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
            if created:
                c.execute(
                    "INSERT INTO alerts(alert_id,obligation_key,domain,primary_subject_id,severity,deadline,"
                    "latest_useful_action,status,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,'open',?,?)",
                    (alert_id, obligation_key, domain, primary_subject_id, severity, deadline,
                     latest_useful_action, now, now))
            else:
                c.execute("UPDATE alerts SET severity=?,deadline=?,latest_useful_action=?,last_seen=? "
                          "WHERE alert_id=?", (severity, deadline, latest_useful_action, now, alert_id))
            for fid in fact_ids:
                c.execute("INSERT OR IGNORE INTO alert_facts(alert_id,fact_id) VALUES(?,?)", (alert_id, fid))
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
        return dict(row) if row else None

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
        if grantor_id not in (subject_id, self._owner):
            raise PermissionDenied("only the subject or the household owner may grant consent")
        consent_id = ids.new_id("con")
        with self.transaction() as c:
            c.execute(
                "INSERT INTO consent(consent_id,subject_id,grantor_id,scope,operations,audience,granted_at,"
                "expires_at) VALUES(?,?,?,?,?,?,?,?)",
                (consent_id, subject_id, grantor_id, scope, json.dumps(sorted(operations)), audience,
                 self._clock(), expires_at))
        return consent_id

    def revoke_consent(self, consent_id: str) -> None:
        with self.transaction() as c:
            cur = c.execute(
                "UPDATE consent SET revoked_at=?, revocation_version=revocation_version+1 "
                "WHERE consent_id=? AND revoked_at IS NULL", (self._clock(), consent_id))
            if cur.rowcount != 1:
                raise LedgerError(f"unknown or already revoked consent {consent_id}")

    def consent_active(self, subject_id: str, operation: str, audience: str) -> bool:
        now = self._clock()
        for row in self.conn.execute(
                "SELECT operations, audience, expires_at FROM consent WHERE subject_id=? AND revoked_at IS NULL",
                (subject_id,)):
            if row["expires_at"] and row["expires_at"] <= now:
                continue
            if operation in json.loads(row["operations"]) and row["audience"] in (audience, "*"):
                return True
        return False

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
                "WHERE connector=? AND account_scope=? AND upstream_id=?",
                (row["connector"], row["account_scope"], row["upstream_id"]))
            self._set_journal(seq, "denied")
        deleted_signals = deleted_facts = deleted_alerts = 0
        blob_refs: list[str] = []
        if stage < 3:   # physical deletion, including derived Facts and Alerts
            deleted_signals, deleted_facts, deleted_alerts, blob_refs = self._delete_forgotten(row)
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
            fact_ids: set[str] = set()
            for sid in signal_ids:
                fact_ids.update(str(r[0]) for r in c.execute(
                    "SELECT DISTINCT fact_id FROM fact_evidence WHERE signal_id=?", (sid,)))
            alert_ids: set[str] = set()
            for fid in fact_ids:
                alert_ids.update(str(r[0]) for r in c.execute(
                    "SELECT alert_id FROM alert_facts WHERE fact_id=?", (fid,)))
            for fid in fact_ids:
                c.execute("DELETE FROM facts WHERE fact_id=?", (fid,))        # cascades versions/evidence
            deleted_alerts = 0
            for aid in alert_ids:
                remaining = c.execute("SELECT COUNT(*) FROM alert_facts WHERE alert_id=?", (aid,)).fetchone()[0]
                if remaining == 0:
                    c.execute("DELETE FROM alerts WHERE alert_id=?", (aid,))
                    deleted_alerts += 1
            for sid in signal_ids:
                c.execute("DELETE FROM signals WHERE signal_id=?", (sid,))
        if self._blob_deleter is not None:
            for ref in blob_refs:
                self._blob_deleter(ref)
        return len(signal_ids), len(fact_ids), deleted_alerts, blob_refs

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
        if sealed is not None and sealed.checkpoint_id > have:
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
            self._delete_forgotten(r)
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
        ok = self.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        fk = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        return ok and not fk


def _split_statements(script: str) -> list[str]:
    """Split a migration script into statements (no triggers/BEGIN blocks are used)."""
    cleaned = "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in cleaned.split(";") if s.strip()]
