"""Encrypted portable snapshots using the bounded checkpoint frame format.

New snapshots are ACK2 checkpoints. The reader retains support for the former
single-envelope ``artha.snapshot.v1`` format; restoration of that legacy format
remains held by :func:`restore_encrypted_snapshot`.
"""
from __future__ import annotations

import base64
import gc
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import struct
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from catchup_core.crypto import EnvelopeCipher
from catchup_core.ledger import KeyringSealedStore, LedgerError, LedgerLocked, purge_signal
from catchup_core.session_lock import session_lock

_CHUNK = 4 << 20
_MIN_SNAPSHOT_RESERVE = 256 << 20
_MAX_MEMBER_BYTES = 4 << 30
_MAX_TOTAL_BYTES = 16 << 30
_MAX_MEMBERS = 65_536
_STAGING_PREFIX = ".artha-snapshot-stage"
_COMPAT_MAX_MEMBER_BYTES = 4 << 20
_COMPAT_MAX_TOTAL_BYTES = 16 << 20
_COMPAT_MAX_MEMBERS = 16_384


@dataclass(frozen=True, slots=True)
class SnapshotArtifact:
    """The exact staged member offered to snapshot authorization."""

    size: int
    sha256: str
    _chunks: Callable[[], Iterator[bytes]] = field(repr=False, compare=False)

    def chunks(self) -> Iterator[bytes]:
        digest, size = hashlib.sha256(), 0
        for chunk in self._chunks():
            size += len(chunk)
            digest.update(chunk)
            yield chunk
        if (size, digest.hexdigest()) != (self.size, self.sha256):
            raise LedgerError("snapshot artifact changed after capture")

    def read_bounded(self, max_bytes: int) -> bytes:
        if self.size > max_bytes:
            raise LedgerError("snapshot artifact exceeds the authorization byte bound")
        parts: list[bytes] = []
        size = 0
        for chunk in self.chunks():
            size += len(chunk)
            if size > max_bytes:
                raise LedgerError("snapshot artifact exceeds the authorization byte bound")
            parts.append(chunk)
        return b"".join(parts)


def _snapshot_artifact(path: Path, captured: tuple[int, str]) -> SnapshotArtifact:
    size, digest = captured

    def chunks() -> Iterator[bytes]:
        with path.open("rb") as handle:
            yield from iter(lambda: handle.read(_CHUNK), b"")

    return SnapshotArtifact(size, digest, chunks)


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


def _file_digest(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _source_bytes(*paths: Path) -> int:
    total = 0
    for path in paths:
        for candidate in (path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            if candidate.exists():
                total += candidate.stat().st_size
    return total


def _nearest_directory(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return Path.cwd()
        candidate = parent
    return candidate if candidate.is_dir() else candidate.parent


def _require_free_space(location: Path, source_bytes: int) -> None:
    if shutil.disk_usage(_nearest_directory(location)).free < 2 * source_bytes + _MIN_SNAPSHOT_RESERVE:
        raise LedgerError("insufficient free space for encrypted snapshot staging and publication")


@contextmanager
def _snapshot_stage(ledger: Path, source_bytes: int) -> Iterator[Path]:
    root = ledger.parent / _STAGING_PREFIX
    lock = ledger.parent / ".artha-snapshot.lock"
    with session_lock(lock):
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise LedgerError("snapshot staging path is not a private directory")
            shutil.rmtree(root)
        _require_free_space(ledger.parent, source_bytes)
        root.mkdir(mode=0o700, parents=False)
        if os.name != "nt":
            root.chmod(0o700)
        try:
            yield root
        finally:
            shutil.rmtree(root, ignore_errors=True)


def sweep_snapshot_stage(ledger: Path) -> int:
    """Remove only the owned orphan staging directory after a prior process death."""
    root = ledger.parent / _STAGING_PREFIX
    lock = ledger.parent / ".artha-snapshot.lock"
    removed = 0
    with session_lock(lock):
        if not root.exists() and not root.is_symlink():
            return 0
        if root.is_symlink() or not root.is_dir():
            raise LedgerError("snapshot staging path is not a private directory")
        for child in root.iterdir():
            removed += 1 if child.is_file() or child.is_symlink() else sum(
                1 for _ in child.rglob("*"))
        shutil.rmtree(root)
    return removed


def _stage_database(source: Path, target: Path, account_bytes: int) -> tuple[int, str]:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with (
            closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(target)) as dst,
        ):
            def progress(_status: int, _remaining: int, pages: int) -> None:
                page_size = int(dst.execute("PRAGMA page_size").fetchone()[0])
                if pages * page_size > _MAX_MEMBER_BYTES:
                    raise LedgerError("snapshot database exceeds the member size bound")
                _require_free_space(target.parent, account_bytes)

            src.backup(dst, pages=256, progress=progress)
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.commit()
            if (dst.execute("PRAGMA integrity_check").fetchone() != ("ok",)
                    or dst.execute("PRAGMA foreign_key_check").fetchone()):
                raise LedgerError("snapshot database failed integrity verification")
        if os.name != "nt":
            target.chmod(0o600)
        return _file_digest(target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _stage_file(source: Path, target: Path, account_bytes: int) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with source.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            for chunk in iter(lambda: incoming.read(_CHUNK), b""):
                size += len(chunk)
                if size > _MAX_MEMBER_BYTES:
                    raise LedgerError("snapshot member exceeds the member size bound")
                _require_free_space(target.parent, account_bytes)
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        target.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def _frame_member(writer: Any, name: str, source: Path, kind: str, mode: int) -> dict[str, Any]:
    writer.frame("h", json.dumps({"path": name, "kind": kind, "mode": mode}).encode())
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            size += len(chunk)
            if size > _MAX_MEMBER_BYTES:
                raise LedgerError("snapshot member exceeds the member size bound")
            digest.update(chunk)
            writer.frame("d", chunk)
    sha256 = digest.hexdigest()
    writer.frame("e", json.dumps({"size": size, "sha256": sha256}).encode())
    return {"path": name, "kind": kind, "mode": mode, "size": size, "sha256": sha256,
            "source_sha256": sha256}


def _write_checkpoint(path: Path, sources: Mapping[str, tuple[Path, str]], cipher: EnvelopeCipher) -> None:
    from catchup_core import checkpoint  # noqa: PLC0415 - checkpoint imports atomic_bytes from this module

    generation = f"snap_{secrets.token_hex(16)}"
    encoded = generation.encode("ascii")
    part = path.parent / f".{path.name}.{secrets.token_hex(6)}.part"
    entries: list[dict[str, Any]] = []
    published = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            escrow = b""
            handle.write(checkpoint._MAGIC + struct.pack(">H", len(encoded)) + encoded
                         + struct.pack(">I", len(escrow)) + escrow)
            writer = checkpoint._Writer(handle, cipher, generation)
            for name, (source, kind) in sorted(sources.items()):
                entry = _frame_member(writer, f"snapshot/{name}", source, kind, 0o600)
                entries.append(entry)
            manifest = {
                "schema": "artha.checkpoint.v2",
                "generation": generation,
                "profile": "personal",
                "escrow_sha256": hashlib.sha256(escrow).hexdigest(),
                "created_at": datetime.now(UTC).isoformat(),
                "entries": entries,
                "excluded": {},
                "context": {"snapshot": "artha.snapshot.v1"},
                "escrow_items": [],
            }
            raw = json.dumps(manifest, sort_keys=True).encode()
            if len(raw) > checkpoint._MAX_MANIFEST_BYTES:
                raise LedgerError("snapshot manifest exceeds its size limit")
            for offset in range(0, len(raw), _CHUNK):
                writer.frame("m", raw[offset:offset + _CHUNK])
            writer.frame("z", json.dumps({"members": len(entries)}).encode())
            handle.flush()
            os.fsync(handle.fileno())
        checkpoint.verify(part, cipher)
        try:
            os.link(part, path)
        except FileExistsError:
            raise LedgerError("snapshot destination already exists; choose a new name") from None
        published = True
        part.unlink()
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        gc.collect()
        part.unlink(missing_ok=True)
        if published:
            path.unlink(missing_ok=True)
        raise


def encrypted_snapshot(path: Path, ledger: Path, cipher: EnvelopeCipher, *,  # noqa: PLR0912,PLR0915 - one generation
                       blobs: Path | None = None, curated: Mapping[str, bytes] | None = None,
                       action_db: Path | None = None, idempotent: bool = False,
                       guard: Callable[[], bool] = lambda: True,
                       authorize: Callable[[str, SnapshotArtifact], None] | None = None) -> None:
    if path.exists():
        if not idempotent:
            raise LedgerError("snapshot destination already exists; choose a new name")
        verify_snapshot(path, cipher)  # idempotent finalize; never overwrite a generation
        return
    def artifact_inventory() -> dict[str, Path]:
        found: dict[str, Path] = {}
        if blobs is not None:
            found.update({f"blobs/{entry.name}": entry for entry in sorted(blobs.glob("evd_*.blob"))})
        for directory, pattern in (("documents", "doc_*.ae1"), ("inbox", "cap_*.ae1"),
                                   ("replay", "fbk_*.ae1"), ("corpus", "case_*.ae1")):
            found.update({f"{directory}/{entry.name}": entry
                          for entry in sorted((ledger.parent / directory).glob(pattern))})
        return found

    inventory = artifact_inventory()
    if len(inventory) + 2 > _MAX_MEMBERS:
        raise LedgerError("snapshot member count exceeds its bound")
    for name in inventory:
        from catchup_core import checkpoint  # noqa: PLC0415 - avoid the framing/atomic-write import cycle
        checkpoint._safe(name)
    source_databases = [ledger]
    if action_db is not None and action_db.exists():
        source_databases.append(action_db)
    curated_items = sorted((curated or {}).items())
    curated_bytes = 0
    for name, content in curated_items:
        if "/" in name or "\\" in name or name.startswith("."):
            raise LedgerError("curated snapshot names must be plain file names")
        if not isinstance(content, bytes) or len(content) > _MAX_MEMBER_BYTES:
            raise LedgerError("curated snapshot content is invalid or too large")
        curated_bytes += len(content)
    source_bytes = _source_bytes(*source_databases, *inventory.values()) + curated_bytes
    if source_bytes > _MAX_TOTAL_BYTES:
        raise LedgerError("snapshot source exceeds the total byte bound")
    _require_free_space(ledger.parent, source_bytes)
    if path.parent != ledger.parent:
        _require_free_space(path.parent, source_bytes)
    with _snapshot_stage(ledger, source_bytes) as stage:
        staged: dict[str, tuple[Path, str]] = {}
        staged_digests: dict[str, tuple[int, str]] = {}
        originals: dict[str, tuple[Path, tuple[int, str]]] = {}
        stage_index = 0

        def authorize_staged(name: str, target: Path, captured: tuple[int, str]) -> None:
            staged_digests[name] = captured
            if authorize is not None:
                authorize(name, _snapshot_artifact(target, captured))
                if _file_digest(target) != captured:
                    raise LedgerError("snapshot inputs changed during authorization; retry from a quiescent generation")

        def staged_database(name: str, source: Path) -> None:
            nonlocal stage_index
            target = stage / f"database-{stage_index}.db"
            stage_index += 1
            captured = _stage_database(source, target, source_bytes)
            staged[name] = (target, "sqlite")
            authorize_staged(name, target, captured)

        staged_database("ledger.db", ledger)
        if action_db is not None and action_db.exists():
            staged_database("actions.db", action_db)
        for name, source in sorted(inventory.items()):
            target = stage / f"file-{len(originals):06d}.bin"
            captured = _stage_file(source, target, source_bytes)
            staged[name] = (target, "file")
            originals[name] = (source, captured)
            authorize_staged(name, target, captured)
        for name, content in curated_items:
            target = stage / f"curated-{len(staged):06d}.bin"
            _require_free_space(stage, source_bytes)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _require_free_space(stage, source_bytes)
            staged[f"curated/{name}"] = (target, "file")
            authorize_staged(f"curated/{name}", target, (len(content), hashlib.sha256(content).hexdigest()))
        if (len(staged) > _MAX_MEMBERS or not staged
                or sum(source.stat().st_size for source, _ in staged.values()) > _MAX_TOTAL_BYTES):
            raise LedgerError("snapshot member count or byte bound is invalid")
        if not guard():
            raise LedgerError("snapshot inputs changed during capture; retry from a quiescent generation")
        for source, captured in originals.values():
            if not source.exists() or _file_digest(source) != captured:
                raise LedgerError("snapshot inputs changed during capture; retry from a quiescent generation")
        for name, source in (("ledger.db", ledger), *((("actions.db", action_db),)
                                                      if action_db is not None and action_db.exists() else ())):
            check = stage / f"verify-{stage_index}.db"
            stage_index += 1
            original = _stage_database(source, check, source_bytes)
            if original != _file_digest(staged[name][0]):
                raise LedgerError("snapshot inputs changed during capture; retry from a quiescent generation")
        if artifact_inventory() != inventory:
            raise LedgerError("snapshot inputs changed during capture; retry from a quiescent generation")
        for name, (source, _kind) in staged.items():
            if _file_digest(source) != staged_digests[name]:
                raise LedgerError("snapshot inputs changed during authorization; retry from a quiescent generation")
        _require_free_space(path.parent, source_bytes)
        _write_checkpoint(path, staged, cipher)


def read_snapshot(path: Path, cipher: EnvelopeCipher, *, max_member_bytes: int = _COMPAT_MAX_MEMBER_BYTES,
                  max_total_bytes: int = _COMPAT_MAX_TOTAL_BYTES,
                  max_members: int = _COMPAT_MAX_MEMBERS) -> dict[str, bytes]:
    """Compatibility reader for bounded historical/synthetic payloads.

    New production verification and extraction must use :func:`stream_snapshot`.
    """
    size = path.stat().st_size
    with path.open("rb") as handle:
        magic = handle.read(4)
        if magic == b"ACK2":
            handle.seek(0)
            return _read_checkpoint_files(
                handle, cipher, max_member_bytes=max_member_bytes, max_total_bytes=max_total_bytes,
                max_members=max_members)
        if size > max_total_bytes:
            raise LedgerError("legacy snapshot exceeds its aggregate compatibility bound")
        handle.seek(0)
        return read_snapshot_bytes(handle.read(), cipher, max_member_bytes=max_member_bytes,
                                   max_total_bytes=max_total_bytes, max_members=max_members)


def read_snapshot_bytes(envelope: bytes, cipher: EnvelopeCipher, *,
                        max_member_bytes: int = _COMPAT_MAX_MEMBER_BYTES,
                        max_total_bytes: int = _COMPAT_MAX_TOTAL_BYTES,
                        max_members: int = _COMPAT_MAX_MEMBERS) -> dict[str, bytes]:
    """Decode the captured bytes whose digest was verified, without reopening a mutable path."""
    if len(envelope) > max_total_bytes:
        raise LedgerError("snapshot exceeds its aggregate compatibility bound")
    if envelope.startswith(b"ACK2"):
        return _read_checkpoint_files(
            io.BytesIO(envelope), cipher, max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes, max_members=max_members)
    data = json.loads(cipher.decrypt(envelope, aad=b"artha.snapshot.v1"))
    if data.get("schema") != "artha.snapshot.v1" or data.get("dispatchable") is not False:
        raise LedgerError("unsupported or dispatchable snapshot")
    encoded_files = data.get("files")
    if not isinstance(encoded_files, dict) or len(encoded_files) > max_members:
        raise LedgerError("legacy snapshot member count exceeds its compatibility bound")
    files: dict[str, bytes] = {}
    total = 0
    for name, value in encoded_files.items():
        if not isinstance(value, str):
            raise LedgerError("legacy snapshot member is malformed")
        content = base64.b64decode(value, validate=True)
        total += len(content)
        if len(content) > max_member_bytes:
            raise LedgerError("legacy snapshot member exceeds its compatibility bound")
        if total > max_total_bytes:
            raise LedgerError("legacy snapshot exceeds its aggregate compatibility bound")
        files[str(name)] = content
    return files


def _logical_snapshot_name(path: str) -> str:
    prefix = "snapshot/"
    if not path.startswith(prefix) or path == prefix:
        raise LedgerError("checkpoint is not a portable snapshot")
    return path.removeprefix(prefix)


def _bounded_checkpoint_scan(source: BinaryIO, cipher: EnvelopeCipher, *args: Any,
                             **kwargs: Any) -> dict[str, Any]:
    from catchup_core import checkpoint  # noqa: PLC0415 - checkpoint imports atomic_bytes from this module

    try:
        return checkpoint._scan(source, cipher, *args, **kwargs)
    except checkpoint.CheckpointError as exc:
        if "bound" in str(exc):
            raise LedgerError(str(exc)) from None
        raise


def verify_snapshot(path: Path, cipher: EnvelopeCipher, *,
                    max_member_bytes: int = _MAX_MEMBER_BYTES,
                    max_total_bytes: int = _MAX_TOTAL_BYTES,
                    max_members: int = _MAX_MEMBERS) -> dict[str, Any]:
    """Authenticate a snapshot in bounded chunks without materializing member bytes."""
    with path.open("rb") as handle:
        magic = handle.read(4)
        if magic != b"ACK2":
            read_snapshot(
                path, cipher, max_member_bytes=min(max_member_bytes, _COMPAT_MAX_MEMBER_BYTES),
                max_total_bytes=min(max_total_bytes, _COMPAT_MAX_TOTAL_BYTES),
                max_members=min(max_members, _COMPAT_MAX_MEMBERS))
            return {"schema": "artha.snapshot.v1", "profile": "legacy"}
        handle.seek(0)
        manifest = _bounded_checkpoint_scan(
            handle, cipher, max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes, max_members=max_members)
    return _snapshot_manifest(
        manifest, max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes, max_members=max_members)


def _snapshot_manifest(manifest: Mapping[str, Any], *, max_member_bytes: int,
                       max_total_bytes: int, max_members: int) -> dict[str, Any]:
    entries = manifest.get("entries")
    context = manifest.get("context")
    if (manifest.get("profile") != "personal" or not isinstance(context, dict)
            or context.get("snapshot") != "artha.snapshot.v1" or not isinstance(entries, list)):
        raise LedgerError("checkpoint is not a portable snapshot")
    if len(entries) > max_members:
        raise LedgerError("checkpoint member count exceeds its bound")
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise LedgerError("checkpoint snapshot manifest is malformed")
        size = entry.get("size")
        if type(size) is not int or size < 0:
            raise LedgerError("checkpoint snapshot member size is malformed")
        _logical_snapshot_name(str(entry.get("path")))
        if size > max_member_bytes:
            raise LedgerError("checkpoint member exceeds its byte bound")
        total += size
        if total > max_total_bytes:
            raise LedgerError("checkpoint aggregate exceeds its byte bound")
    return dict(manifest)


def stream_snapshot(path: Path, cipher: EnvelopeCipher, sink: Callable[[str, bytes], None], *,
                    max_member_bytes: int = _MAX_MEMBER_BYTES,
                    max_total_bytes: int = _MAX_TOTAL_BYTES,
                    max_members: int = _MAX_MEMBERS) -> dict[str, Any]:
    """Verify the entire checkpoint before streaming authenticated members to ``sink``.

    ``sink`` receives logical member names and bounded chunks. It must stage its own output; this
    function does not activate or restore an installation.
    """
    if not callable(sink):
        raise TypeError("snapshot sink must be callable")
    with path.open("rb") as handle:
        if handle.read(4) != b"ACK2":
            raise LedgerError("streaming extraction requires ACK2 checkpoint framing")
        handle.seek(0)
        manifest = _bounded_checkpoint_scan(
            handle, cipher, max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes, max_members=max_members)
        manifest = _snapshot_manifest(
            manifest, max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes, max_members=max_members)
        handle.seek(0)

        def collect(header: Mapping[str, Any], body: bytes) -> None:
            sink(_logical_snapshot_name(str(header["path"])), body)

        streamed = _bounded_checkpoint_scan(
            handle, cipher, collect, max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes, max_members=max_members)
        if streamed != manifest:
            raise LedgerError("checkpoint changed during snapshot extraction")
    return manifest


def extract_snapshot(path: Path, cipher: EnvelopeCipher, sink: Callable[[str, bytes], None], *,
                     max_member_bytes: int = _MAX_MEMBER_BYTES,
                     max_total_bytes: int = _MAX_TOTAL_BYTES,
                     max_members: int = _MAX_MEMBERS) -> dict[str, Any]:
    """Compatibility spelling for bounded streaming snapshot extraction."""
    return stream_snapshot(
        path, cipher, sink, max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes, max_members=max_members)


def _read_checkpoint_files(source: BinaryIO, cipher: EnvelopeCipher, *, max_member_bytes: int,
                           max_total_bytes: int, max_members: int) -> dict[str, bytes]:
    manifest = _bounded_checkpoint_scan(
        source, cipher, max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes, max_members=max_members)
    _snapshot_manifest(
        manifest, max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes, max_members=max_members)
    source.seek(0)
    files: dict[str, bytearray] = {}

    def opened(header: dict[str, Any]) -> None:
        name = _logical_snapshot_name(header["path"])
        if name in files:
            raise LedgerError("checkpoint contains a duplicate snapshot member")
        files[name] = bytearray()

    def collect(header: dict[str, Any], body: bytes) -> None:
        files[_logical_snapshot_name(header["path"])].extend(body)

    streamed = _bounded_checkpoint_scan(
        source, cipher, collect, opened, max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes, max_members=max_members)
    if streamed != manifest:
        raise LedgerError("checkpoint changed during compatibility read")
    return {name: bytes(body) for name, body in files.items()}


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
    from catchup_core.ledger import invalidate_restored_scope_admission  # noqa: PLC0415
    invalidate_restored_scope_admission(db)
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
