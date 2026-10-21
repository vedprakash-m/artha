"""Encrypted, portable snapshots. No plaintext snapshot or private key touches disk."""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

from catchup_core.crypto import EnvelopeCipher
from catchup_core.ledger import KeyringSealedStore, LedgerError, LedgerLocked, purge_signal


def database_bytes(path: Path) -> bytes:
    with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(":memory:")) as target:
        src.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
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
    # Ciphertext only; private values must never enter an intermediate plaintext file.
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
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


def encrypted_snapshot(path: Path, ledger: Path, cipher: EnvelopeCipher, *,
                       blobs: Path | None = None, curated: Mapping[str, bytes] | None = None,
                       action_db: Path | None = None, idempotent: bool = False) -> None:
    if path.exists():
        if not idempotent:
            raise LedgerError("snapshot destination already exists; choose a new name")
        read_snapshot(path, cipher)  # idempotent finalize; never overwrite a generation
        return
    files: dict[str, str] = {"ledger.db": base64.b64encode(database_bytes(ledger)).decode()}
    if action_db is not None and action_db.exists():
        files["actions.db"] = base64.b64encode(database_bytes(action_db)).decode()
    if blobs is not None:
        for blob in sorted(blobs.glob("evd_*.blob")):
            files[f"blobs/{blob.name}"] = base64.b64encode(blob.read_bytes()).decode()
    for name, content in (curated or {}).items():
        if "/" in name or "\\" in name or name.startswith("."):
            raise LedgerError("curated snapshot names must be plain file names")
        files[f"curated/{name}"] = base64.b64encode(content).decode()
    payload = json.dumps({"schema": "artha.snapshot.v1", "dispatchable": False, "files": files},
                         sort_keys=True).encode()
    envelope, _ = cipher.encrypt(payload, aad=b"artha.snapshot.v1")
    # Verify before publishing the immutable snapshot.
    if cipher.decrypt(envelope, aad=b"artha.snapshot.v1") != payload:
        raise LedgerError("encrypted snapshot verification failed")
    atomic_bytes(path, envelope)


def read_snapshot(path: Path, cipher: EnvelopeCipher) -> dict[str, bytes]:
    data = json.loads(cipher.decrypt(path.read_bytes(), aad=b"artha.snapshot.v1"))
    if data.get("schema") != "artha.snapshot.v1" or data.get("dispatchable") is not False:
        raise LedgerError("unsupported or dispatchable snapshot")
    return {str(name): base64.b64decode(value, validate=True) for name, value in data["files"].items()}


def restore_encrypted_snapshot(path: Path, destination: Path, cipher: EnvelopeCipher,
                               privacy: KeyringSealedStore) -> None:
    """Restore to a new local directory, replaying current revocations and tombstones.

    Existing authority is never overwritten. Action snapshots remain quarantined;
    importing an old action snapshot cannot authorize a dispatch.
    """
    if destination.exists():
        raise LedgerError("restore needs a new empty destination")
    checkpoint = privacy.read()
    if checkpoint is None:
        raise LedgerLocked("restore requires the latest trusted privacy history")
    history = privacy.replay()
    files = read_snapshot(path, cipher)
    with closing(sqlite3.connect(":memory:")) as db:
        db.deserialize(files["ledger.db"])
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
                       tuple(entry[k] for k in tombstone_fields))
            sigs = db.execute("SELECT signal_id,evidence_blob_ref FROM signals WHERE "
                "(connector=? AND account_scope=? AND upstream_id=?) OR content_hmac=?",
                (entry["connector"], entry["account_scope"], entry["upstream_id"], entry["content_hmac"])).fetchall()
            for sid, ref in sigs:
                purge_signal(db, sid)
                files.pop(f"blobs/{ref}.blob", None)
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
        files["ledger.db"] = db.serialize()
    # Validate every path before publishing any file.
    for name in files:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise LedgerError("unsafe path in snapshot")
    _write_restore(destination, files, cipher)


def _write_restore(destination: Path, files: Mapping[str, bytes], cipher: EnvelopeCipher) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staged = Path(tempfile.mkdtemp(prefix=".artha-restore-", dir=destination.parent))
    try:
        for original_name, original_content in files.items():
            name = "actions.quarantined.db" if original_name == "actions.db" else original_name
            content = original_content
            if name.startswith("curated/"):
                # Keep private curated copies encrypted until separately reviewed for import.
                content = cipher.encrypt(content, aad=name.encode())[0]
                name += ".ae1"
            atomic_bytes(staged / name, content)
        if destination.exists():
            raise LedgerError("restore destination was created by another process")
        os.replace(staged, destination)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
