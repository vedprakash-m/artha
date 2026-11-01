"""Encrypted, portable snapshots. No plaintext snapshot or private key touches disk."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from catchup_core.crypto import EnvelopeCipher
from catchup_core.ledger import KeyringSealedStore, LedgerError, LedgerLocked, purge_signal


def database_bytes(path: Path) -> bytes:
    with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(":memory:")) as target:
        src.backup(target)
        if (target.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or target.execute('PRAGMA foreign_key_check').fetchone()):
            raise LedgerError("snapshot database failed integrity verification")
        # Online backup retains the source WAL header. This complete image has no
        # WAL file when deserialized; use SQLite's rollback-journal header instead.
        data = bytearray(target.serialize())
        data[18:20] = b"\x01\x01"
        with closing(sqlite3.connect(":memory:")) as check:
            check.deserialize(data)
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise LedgerError("portable snapshot failed integrity verification")
        return bytes(data)


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def encrypted_snapshot(path: Path, ledger: Path, cipher: EnvelopeCipher, *,  # noqa: PLR0912 - one guarded generation
                       blobs: Path | None = None, curated: Mapping[str, bytes] | None = None,
                       action_db: Path | None = None, idempotent: bool = False,
                       guard: Callable[[], bool] = lambda: True,
                       authorize: Callable[[str, bytes], None] | None = None) -> None:
    if path.exists():
        if not idempotent:
            raise LedgerError("snapshot destination already exists; choose a new name")
        read_snapshot(path, cipher)  # idempotent finalize; never overwrite a generation
        return
    ledger_image = database_bytes(ledger)
    files: dict[str, str] = {"ledger.db": base64.b64encode(ledger_image).decode()}
    action_image = None
    if action_db is not None and action_db.exists():
        action_image = database_bytes(action_db)
        files["actions.db"] = base64.b64encode(action_image).decode()
    if blobs is not None:
        for blob in sorted(blobs.glob("evd_*.blob")):
            files[f"blobs/{blob.name}"] = base64.b64encode(blob.read_bytes()).decode()
    artifacts = {}
    for directory, pattern in (("documents", "doc_*.ae1"), ("inbox", "cap_*.ae1"),
                               ("replay", "fbk_*.ae1"), ("corpus", "case_*.ae1")):
        for artifact in sorted((ledger.parent / directory).glob(pattern)):
            artifacts[artifact] = artifact.read_bytes()
            files[f"{directory}/{artifact.name}"] = base64.b64encode(artifacts[artifact]).decode()
    for name, content in (curated or {}).items():
        if "/" in name or "\\" in name or name.startswith("."):
            raise LedgerError("curated snapshot names must be plain file names")
        files[f"curated/{name}"] = base64.b64encode(content).decode()
    if authorize is not None:
        # Check the exact captured generation, including artifacts not yet represented by Facts or coverage.
        for name, encoded in files.items():
            authorize(name, base64.b64decode(encoded, validate=True))
    payload = json.dumps({"schema": "artha.snapshot.v1", "dispatchable": False, "files": files},
                         sort_keys=True).encode()
    envelope, _ = cipher.encrypt(payload, aad=b"artha.snapshot.v1")
    current_artifacts = {p for directory, pattern in (("documents", "doc_*.ae1"), ("inbox", "cap_*.ae1"),
                                                      ("replay", "fbk_*.ae1"), ("corpus", "case_*.ae1"))
                         for p in (ledger.parent / directory).glob(pattern)}
    if (database_bytes(ledger) != ledger_image or not guard() or current_artifacts != set(artifacts)
            or any(not p.exists() or p.read_bytes() != content for p, content in artifacts.items())
            or (action_image is not None and action_db is not None and database_bytes(action_db) != action_image)):
        raise LedgerError("snapshot inputs changed during capture; retry from a quiescent generation")
    # Verify before publishing the immutable snapshot.
    if cipher.decrypt(envelope, aad=b"artha.snapshot.v1") != payload:
        raise LedgerError("encrypted snapshot verification failed")
    atomic_bytes(path, envelope)


def read_snapshot(path: Path, cipher: EnvelopeCipher) -> dict[str, bytes]:
    return read_snapshot_bytes(path.read_bytes(), cipher)


def read_snapshot_bytes(envelope: bytes, cipher: EnvelopeCipher) -> dict[str, bytes]:
    """Decode the captured bytes whose digest was verified, without reopening a mutable path."""
    data = json.loads(cipher.decrypt(envelope, aad=b"artha.snapshot.v1"))
    if data.get("schema") != "artha.snapshot.v1" or data.get("dispatchable") is not False:
        raise LedgerError("unsupported or dispatchable snapshot")
    return {str(name): base64.b64decode(value, validate=True) for name, value in data["files"].items()}


def apply_privacy_history(  # noqa: PLR0912 - one ordered replay of consent then tombstones
        db: sqlite3.Connection, remove: Callable[[str], None], checkpoint: Any,
                          history: Mapping[str, Any],
                          purge_briefings: Callable[[list[str]], None] | None = None) -> None:
    """Replay current consent and tombstones into a restored ledger and delete the artifacts they erase.

    ``remove(name)`` receives store-relative artifact names (``blobs/evd_x.blob``, ``replay/fbk_y.ae1`` ...);
    ``purge_briefings`` receives the Catch-up ids whose archived Briefings cite an erased Signal.
    Raises ``LedgerLocked`` when the replayed history does not match the trusted checkpoint.
    """
    db.execute("PRAGMA foreign_keys=ON")
    for entry in history.get("consent", []):
        db.execute("DELETE FROM consent WHERE consent_id=?", (entry["consent_id"],))
        consent_fields = ("consent_id", "subject_id", "grantor_id", "scope", "operations", "audience",
                  "granted_at", "expires_at", "revoked_at", "revocation_version")
        db.execute("INSERT INTO consent VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(entry[k] for k in consent_fields))
    for entry in history.get("tombstones", []):
        tombstone_fields = ("tombstone_id", "connector", "account_scope", "upstream_id", "content_hmac",
                  "created_at", "reason")
        db.execute("INSERT OR IGNORE INTO tombstones VALUES(?,?,?,?,?,?,?)",
                   tuple(entry.get(k) for k in tombstone_fields))  # content-keyed tombstones carry NULL identity fields
        sigs = db.execute("SELECT signal_id,evidence_blob_ref FROM signals WHERE "
            "(connector=? AND account_scope=? AND upstream_id=?) OR content_hmac=?",
            (entry.get("connector"), entry.get("account_scope"), entry.get("upstream_id"),
             entry.get("content_hmac"))).fetchall()
        for sid, ref in sigs:
            if purge_briefings is not None and db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='briefing_signals'").fetchone():
                purge_briefings([r[0] for r in db.execute(
                    "SELECT DISTINCT catchup_id FROM briefing_signals WHERE signal_id=?", (sid,))])
            case_ids = {"case_" + str(sid).removeprefix("sig_")[:16]}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='corpus_cases'").fetchone():
                case_ids.update(r[0] for r in db.execute(
                    "SELECT case_id FROM corpus_cases WHERE signal_id=?", (sid,)))
            for case_id in case_ids:
                remove(f"corpus/{case_id}.ae1")
            for feedback in db.execute("SELECT feedback_id FROM replay_cases WHERE signal_id=?", (sid,)):
                remove(f"replay/{feedback[0]}.ae1")
            purge_signal(db, sid)
            remove(f"blobs/{ref}.blob")
        upstream = entry.get('upstream_id')
        if not isinstance(upstream, str) or not upstream:
            continue  # a content-keyed tombstone names no artifact to remove
        if entry.get('connector') == 'retention' and entry.get('account_scope') == 'replay':
            db.execute("DELETE FROM replay_cases WHERE feedback_id=?", (upstream,))
            remove(f"replay/{upstream}.ae1")
        if entry.get('connector') == 'documents':
            remove(f"documents/{upstream.split(':', 1)[0]}.ae1")
        if entry.get('connector') == 'capture':
            remove(f"inbox/{upstream}.ae1")
    db.execute("DELETE FROM briefing_drafts")
    db.execute("DELETE FROM alerts WHERE NOT EXISTS (SELECT 1 FROM alert_facts af "
               "WHERE af.alert_id=alerts.alert_id)")
    import hashlib  # noqa: PLC0415
    digest = hashlib.sha256("\n".join(r[0] for r in db.execute(
        "SELECT tombstone_id FROM tombstones ORDER BY tombstone_id")).encode()).hexdigest()
    generation = db.execute("SELECT COALESCE(SUM(revocation_version),0) FROM consent").fetchone()[0]
    if digest != checkpoint.tombstone_digest or generation != checkpoint.permission_generation:
        raise LedgerLocked("snapshot privacy history cannot be verified")
    db.execute("INSERT OR REPLACE INTO deletion_checkpoint VALUES(?,datetime('now'),?,?)",
               (checkpoint.checkpoint_id, digest, generation))
    db.commit()
    if (db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchone()):
        raise LedgerError("restored generation failed integrity verification")


def restore_encrypted_snapshot(path: Path, destination: Path, cipher: EnvelopeCipher,
                               privacy: KeyringSealedStore) -> None:
    """Hold legacy restoration until a governed inactive/privacy-guarded restore is available."""
    raise LedgerLocked(
        "Legacy snapshot restore is blocked: use the governed checkpoint/quarantine restore "
        "with current privacy history and an inactive installation fence."
    )
