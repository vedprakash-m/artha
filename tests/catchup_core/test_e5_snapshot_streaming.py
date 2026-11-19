"""E5 snapshot/accounting boundaries; all inputs are synthetic."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tracemalloc
from contextlib import closing
from pathlib import Path

import pytest

from catchup_core import backup, checkpoint
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ledger import LedgerError


CIPHER = EnvelopeCipher(StaticKeyProvider({1: b"e" * 32}))


def _ledger(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE records(value TEXT)")
        db.execute("INSERT INTO records VALUES('synthetic')")


def test_snapshot_uses_checkpoint_streaming_without_whole_database_serialization(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    action = tmp_path / "actions.db"
    with closing(sqlite3.connect(action)) as db, db:
        db.execute("CREATE TABLE records(value)")
        db.execute("INSERT INTO records VALUES(7)")
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    (blobs / "evd_one.blob").write_bytes(b"encrypted blob")
    target = tmp_path / "snapshot.ack"

    monkeypatch.setattr(backup, "database_bytes", lambda *_args: pytest.fail("whole database serialization"))
    backup.encrypted_snapshot(target, ledger, CIPHER, action_db=action, blobs=blobs)

    assert target.read_bytes().startswith(b"ACK2")
    manifest = checkpoint.verify(target, CIPHER)
    assert manifest["schema"] == "artha.checkpoint.v2"
    files = backup.read_snapshot(target, CIPHER)
    assert set(files) == {"ledger.db", "actions.db", "blobs/evd_one.blob"}
    with closing(sqlite3.connect(":memory:")) as db:
        db.deserialize(files["ledger.db"])
        assert db.execute("SELECT value FROM records").fetchone() == ("synthetic",)
    assert not list(tmp_path.glob(".artha-snapshot-stage*"))


def test_snapshot_authorization_does_not_whole_buffer_large_file(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    large = blobs / "evd_large.blob"
    piece = b"s" * (1 << 20)
    expected = hashlib.sha256()
    with large.open("wb") as handle:
        for _ in range(12):
            handle.write(piece)
            expected.update(piece)
    authorized = []

    def authorize(name, artifact):
        if name == "blobs/evd_large.blob":
            authorized.append((artifact.size, artifact.sha256))

    monkeypatch.setattr(
        Path, "read_bytes", lambda _path: pytest.fail("whole artifact buffered for authorization")
    )
    backup.encrypted_snapshot(
        tmp_path / "snapshot.ack", ledger, CIPHER, blobs=blobs, authorize=authorize
    )

    assert authorized == [(12 << 20, expected.hexdigest())]


def test_snapshot_artifact_refuses_changed_or_oversized_chunks():
    correct = backup.SnapshotArtifact(
        3, hashlib.sha256(b"old").hexdigest(), lambda: iter((b"new",)))
    with pytest.raises(LedgerError, match="changed after capture"):
        correct.read_bounded(3)

    known_oversize = backup.SnapshotArtifact(
        4, hashlib.sha256(b"four").hexdigest(), lambda: iter((b"four",)))
    with pytest.raises(LedgerError, match="authorization byte bound"):
        known_oversize.read_bounded(3)

    growing = backup.SnapshotArtifact(
        3, hashlib.sha256(b"abc").hexdigest(), lambda: iter((b"ab", b"cd")))
    with pytest.raises(LedgerError, match="authorization byte bound"):
        growing.read_bounded(3)


def test_snapshot_refuses_low_space_before_staging_and_publishing(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    target = tmp_path / "snapshot.ack"
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _path: type(
        "Usage", (), {"free": backup._MIN_SNAPSHOT_RESERVE - 1})())

    with pytest.raises(LedgerError, match="free space"):
        backup.encrypted_snapshot(target, ledger, CIPHER)
    assert not target.exists()
    assert not list(tmp_path.glob(".artha-snapshot-stage*"))


def test_snapshot_failure_removes_plaintext_staging_and_partial_output(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    target = tmp_path / "snapshot.ack"

    def fail(_writer, _name, _source, _kind, _mode):
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(backup, "_frame_member", fail)
    with pytest.raises(RuntimeError, match="interruption"):
        backup.encrypted_snapshot(target, ledger, CIPHER)
    assert not target.exists()
    assert not list(tmp_path.glob(".artha-snapshot-stage*"))
    assert not list(tmp_path.glob(f".{target.name}.*.part"))


def test_streaming_snapshot_verification_and_extraction_stay_constant_memory(tmp_path):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    large = blobs / "evd_large.blob"
    piece = b"z" * (1 << 20)
    expected = hashlib.sha256()
    with large.open("wb") as handle:
        for _ in range(20):
            handle.write(piece)
            expected.update(piece)
    target = tmp_path / "snapshot.ack"
    backup.encrypted_snapshot(target, ledger, CIPHER, blobs=blobs)
    actual, chunks = hashlib.sha256(), []
    tracemalloc.start()
    try:
        manifest = backup.stream_snapshot(
            target,
            CIPHER,
            lambda name, chunk: (
                actual.update(chunk),
                chunks.append(len(chunk)),
            )[-1] if name == "blobs/evd_large.blob" else None,
            max_member_bytes=32 << 20,
            max_total_bytes=40 << 20,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert manifest["schema"] == "artha.checkpoint.v2"
    assert actual.hexdigest() == expected.hexdigest()
    assert len(chunks) >= 5 and max(chunks) <= backup._CHUNK
    assert peak < 24 << 20
    with pytest.raises(LedgerError, match="member.*bound"):
        backup.read_snapshot(target, CIPHER)


def test_compatibility_reader_refuses_member_and_aggregate_overflow(tmp_path):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    (blobs / "evd_one.blob").write_bytes(b"x" * 2048)
    (blobs / "evd_two.blob").write_bytes(b"y" * 2048)
    target = tmp_path / "snapshot.ack"
    backup.encrypted_snapshot(target, ledger, CIPHER, blobs=blobs)

    with pytest.raises(LedgerError, match="member.*bound"):
        backup.read_snapshot(target, CIPHER, max_member_bytes=1024, max_total_bytes=100_000)
    with pytest.raises(LedgerError, match="aggregate.*bound"):
        backup.read_snapshot(target, CIPHER, max_member_bytes=100_000,
                             max_total_bytes=3000)
    with pytest.raises(checkpoint.CheckpointError, match="member.*bound"):
        checkpoint.verify(target, CIPHER, max_member_bytes=1024)
    with pytest.raises(checkpoint.CheckpointError, match="aggregate.*bound"):
        checkpoint.verify(target, CIPHER, max_total_bytes=3000)


def test_stream_extraction_does_not_receive_chunks_before_full_verification(tmp_path):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    target = tmp_path / "snapshot.ack"
    backup.encrypted_snapshot(target, ledger, CIPHER)
    original = target.read_bytes()
    target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    released = []
    with pytest.raises(checkpoint.CheckpointError):
        backup.stream_snapshot(target, CIPHER, lambda name, chunk: released.append((name, chunk)))
    assert released == []


def test_idempotent_snapshot_verification_does_not_materialize_members(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    target = tmp_path / "snapshot.ack"
    backup.encrypted_snapshot(target, ledger, CIPHER)
    monkeypatch.setattr(
        backup, "read_snapshot",
        lambda *_args, **_kwargs: pytest.fail("whole snapshot materialized for idempotent verification"),
    )
    backup.encrypted_snapshot(target, ledger, CIPHER, idempotent=True)


def test_orphan_staging_is_swept_only_at_owned_location(tmp_path):
    staging = tmp_path / backup._STAGING_PREFIX
    staging.mkdir()
    (staging / "plain.db").write_bytes(b"synthetic")
    other = tmp_path / "other-stage"
    other.mkdir()
    assert backup.sweep_snapshot_stage(tmp_path / "ledger.db") == 1
    assert not staging.exists() and other.exists()


def test_old_single_envelope_reader_remains_compatible(tmp_path):
    files = {"ledger.db": base64.b64encode(b"old-image").decode()}
    payload = json.dumps({"schema": "artha.snapshot.v1", "dispatchable": False, "files": files},
                         sort_keys=True).encode()
    envelope, _ = CIPHER.encrypt(payload, aad=b"artha.snapshot.v1")
    target = tmp_path / "old.ae1"
    target.write_bytes(envelope)
    assert backup.read_snapshot(target, CIPHER) == {"ledger.db": b"old-image"}


def legacy_snapshot(payload):
    envelope, _ = CIPHER.encrypt(
        json.dumps(payload, sort_keys=True).encode(), aad=b"artha.snapshot.v1")
    return envelope


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"schema": "other", "dispatchable": False, "files": {}},
         "unsupported or dispatchable"),
        ({"schema": "artha.snapshot.v1", "dispatchable": True, "files": {}},
         "unsupported or dispatchable"),
        ({"schema": "artha.snapshot.v1", "dispatchable": False, "files": []},
         "member count"),
        ({"schema": "artha.snapshot.v1", "dispatchable": False, "files": {"x": 4}},
         "member is malformed"),
    ],
)
def test_legacy_snapshot_reader_rejects_invalid_manifest_boundaries(payload, match):
    with pytest.raises(LedgerError, match=match):
        backup.read_snapshot_bytes(legacy_snapshot(payload), CIPHER)


def test_legacy_snapshot_reader_enforces_member_and_total_limits():
    payload = {
        "schema": "artha.snapshot.v1",
        "dispatchable": False,
        "files": {"first": base64.b64encode(b"ab").decode()},
    }
    envelope = legacy_snapshot(payload)
    with pytest.raises(LedgerError, match="aggregate compatibility"):
        backup.read_snapshot_bytes(envelope, CIPHER, max_total_bytes=len(envelope) - 1)
    with pytest.raises(LedgerError, match="member exceeds"):
        backup.read_snapshot_bytes(envelope, CIPHER, max_member_bytes=1)

    payload["files"]["second"] = base64.b64encode(b"cd").decode()
    with pytest.raises(LedgerError, match="aggregate compatibility"):
        backup.read_snapshot_bytes(
            legacy_snapshot(payload), CIPHER,
            max_member_bytes=2, max_total_bytes=3)


@pytest.mark.parametrize(
    ("manifest", "match"),
    [
        ({"profile": "work", "context": {}, "entries": []}, "portable snapshot"),
        ({
            "profile": "personal", "context": {"snapshot": "artha.snapshot.v1"},
            "entries": [None],
        }, "manifest is malformed"),
        ({
            "profile": "personal", "context": {"snapshot": "artha.snapshot.v1"},
            "entries": [{"path": "snapshot/x", "size": True}],
        }, "member size is malformed"),
        ({
            "profile": "personal", "context": {"snapshot": "artha.snapshot.v1"},
            "entries": [{"path": "other/x", "size": 1}],
        }, "portable snapshot"),
        ({
            "profile": "personal", "context": {"snapshot": "artha.snapshot.v1"},
            "entries": [{"path": "snapshot/x", "size": 2}],
        }, "member exceeds"),
        ({
            "profile": "personal", "context": {"snapshot": "artha.snapshot.v1"},
            "entries": [
                {"path": "snapshot/x", "size": 1},
                {"path": "snapshot/y", "size": 1},
            ],
        }, "aggregate exceeds"),
    ],
)
def test_snapshot_manifest_enforces_portable_backup_boundaries(manifest, match):
    with pytest.raises(LedgerError, match=match):
        backup._snapshot_manifest(
            manifest, max_member_bytes=1, max_total_bytes=1, max_members=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode assertion")
def test_snapshot_staging_directory_is_owner_private(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    _ledger(ledger)
    observed: list[int] = []
    original = backup._frame_member

    def observe(writer, name, source, kind, mode):
        if kind == "sqlite":
            observed.append(source.stat().st_mode & 0o777)
        return original(writer, name, source, kind, mode)

    monkeypatch.setattr(backup, "_frame_member", observe)
    backup.encrypted_snapshot(tmp_path / "snapshot.ack", ledger, CIPHER)
    assert observed and all(mode == 0o600 for mode in observed)
