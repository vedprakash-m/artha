"""Full-state checkpoint and restore (WP-18; re-artha §7.6, §11.3, §12.5).

One encrypted, write-once file per generation, with ordinary file payloads written and read in bounded chunks::

    b"ACK2" | generation | escrow section | frame*       frame = u32 length | AE1 envelope

Frames are, in order: per member ``h`` (path/kind/mode), ``d``* (data chunks), ``e`` (size and hash); then the
manifest ``m`` and a terminator ``z``.  Each envelope's associated data binds generation, sequence number and kind,
so a frame cannot be dropped, reordered, duplicated or moved to another checkpoint.  Frames use the core
envelope key.  The escrow section holds key material and is protected only by the owner's passphrase (scrypt +
AES-256-GCM): the checkpoint plus the passphrase recovers the keys on a fresh host.

Creation is fail-closed: anything unreadable, linked, conflicted or changed during capture blocks it and publishes
nothing. Plaintext is never written to disk. Restore only writes into a new, empty directory outside the live tree.
A database whose main file is complete is streamed in chunks under one read transaction.  Only a database with
committed pages still in its log is copied through a bounded in-memory image (``_MAX_SQLITE_BYTES``).  The complete
manifest is held in memory (``_MAX_MANIFEST_BYTES``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, closing, contextmanager, nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from catchup_core.backup import atomic_bytes
from catchup_core.crypto import DecryptionError, EnvelopeCipher
from catchup_core.session_lock import session_lock, session_lock_reader

if TYPE_CHECKING:
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory

__all__ = [
    "CheckpointBlocked",
    "CheckpointError",
    "copy_to",
    "create",
    "diff",
    "read_escrow",
    "reconcile",
    "restore",
    "verify",
]

_MAGIC = b"ACK2"
_ESCROW_MAGIC = b"ES1"
_SQLITE_HEADER = b"SQLite format 3\x00"
_CONFLICT = re.compile(r"conflicted copy", re.I)
_COMPANIONS = ("-wal", "-shm", "-journal")
_CACHE_DIRS = {".git": "recorded by code revision, not copied", "__pycache__": "reproducible cache",
               ".pytest_cache": "reproducible cache", ".mypy_cache": "reproducible cache",
               ".ruff_cache": "reproducible cache", ".DS_Store": "reproducible platform metadata"}
_CHUNK = 4 << 20
_MAX_FRAME = _CHUNK + 4096
_MAX_ESCROW = 4 << 20
_MAX_MANIFEST_BYTES = 64 << 20
_MAX_SQLITE_BYTES = 256 << 20


class CheckpointError(RuntimeError):
    """The checkpoint is unreadable, inconsistent or unsafe to use."""


class CheckpointBlocked(CheckpointError):
    """Completeness cannot be proven; no checkpoint was published (exit 20)."""


@dataclass(frozen=True)
class Entry:
    path: str          # "<root>/<relative posix path>"
    size: int
    sha256: str        # of the captured bytes
    mode: int
    kind: str          # "file" or "sqlite"
    source_sha256: str = ""   # hash of the live bytes (files) or of the consistent image (sqlite) at first read


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with session_lock_reader(path) as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite(path: Path) -> bool:
    with session_lock_reader(path) as handle:
        return handle.read(16) == _SQLITE_HEADER


def _source_stream_problems(path: Path, key: str, *, directory: bool) -> list[str]:
    if sys.platform != 'win32':
        return []
    from catchup_core.quarantine_fs_windows import (  # noqa: PLC0415
        QuarantineFilesystemError,
        require_ordinary_stream,
    )

    try:
        require_ordinary_stream(path, directory=directory)
    except QuarantineFilesystemError:
        return [f'{key}: source stream inventory could not be verified']
    return []


def _walk(  # noqa: PLR0912 - explicit inventory refusal cases
    roots: Mapping[str, Path], exclude: Mapping[str, str],
) -> tuple[dict[str, Path], list[str], dict[str, str]]:
    """Every regular file under each root, the problems that block, and the excluded paths with reasons."""
    found: dict[str, Path] = {}
    problems: list[str] = []
    excluded: dict[str, str] = {}
    for name, root in roots.items():
        if root.is_symlink() or not (root.is_file() or root.is_dir()):
            problems.append(f"{name}: root is missing or a link")
            continue
        problems.extend(_source_stream_problems(root, name, directory=root.is_dir()))
        if root.is_file():
            found[f'{name}/{root.name}'] = root
            continue
        def unreadable(exc: OSError, root_name: str = name) -> None:
            problems.append(f'{root_name}: directory traversal failed ({type(exc).__name__})')

        for current, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
            base = Path(current)
            for entry in sorted([*dirs, *files]):
                key = f"{name}/{(base / entry).relative_to(root).as_posix()}"
                reason = _CACHE_DIRS.get(entry) or next(
                    (why for prefix, why in exclude.items() if key == prefix or key.startswith(prefix + "/")), None)
                if reason:
                    excluded[key] = reason
                    if entry in dirs:
                        dirs.remove(entry)
                    continue
                path = base / entry
                try:
                    mode = path.lstat().st_mode
                except OSError as exc:
                    problems.append(f'{name}: entry inaccessible ({type(exc).__name__})')
                    continue
                if stat.S_ISREG(mode) or stat.S_ISDIR(mode):
                    problems.extend(_source_stream_problems(path, key, directory=stat.S_ISDIR(mode)))
                if stat.S_ISLNK(mode):
                    problems.append(f"{key}: symbolic link")
                    if entry in dirs:
                        dirs.remove(entry)
                elif stat.S_ISDIR(mode):
                    continue
                elif not stat.S_ISREG(mode):
                    problems.append(f"{key}: not a regular file")
                elif _CONFLICT.search(entry):
                    problems.append(f"{key}: sync-conflict copy needs resolution")
                else:
                    found[key] = path
    _drop_companions(found, excluded)
    problems.extend(_portability_problems(found))
    return found, problems, excluded


def _drop_companions(found: dict[str, Path], excluded: dict[str, str]) -> None:
    """A database is captured through one consistent image; its journal files are never separate members."""
    for key in [k for k in found if k.endswith(_COMPANIONS) and k.rsplit("-", 1)[0] in found]:
        excluded[key] = "captured through the database image"
        del found[key]


def _check_passphrase(passphrase: str) -> None:
    if len(passphrase) < 12 or len(set(passphrase)) < 6:
        raise CheckpointBlocked("passphrase is too weak: use at least 12 characters with variety")


def _kdf(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def _seal_escrow(items: Mapping[str, bytes], passphrase: str) -> bytes:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    body = json.dumps({k: base64.b64encode(v).decode() for k, v in sorted(items.items())}).encode()
    if len(body) + 47 > _MAX_ESCROW:
        raise CheckpointBlocked('recovery keys exceed the escrow size limit')
    return _ESCROW_MAGIC + salt + nonce + AESGCM(_kdf(passphrase, salt)).encrypt(nonce, body, _ESCROW_MAGIC)


def _read_head(handle: BinaryIO) -> tuple[str, bytes]:
    if handle.read(4) != _MAGIC:
        raise CheckpointError("not a checkpoint file")
    sizes = handle.read(2)
    if len(sizes) != 2:
        raise CheckpointError("checkpoint is truncated")
    generation_size = struct.unpack('>H', sizes)[0]
    if not 4 <= generation_size <= 128:
        raise CheckpointError('checkpoint generation is malformed')
    generation = handle.read(generation_size)
    raw = handle.read(4)
    if len(generation) < 4 or len(raw) != 4:
        raise CheckpointError("checkpoint is truncated")
    escrow_size = struct.unpack('>I', raw)[0]
    if escrow_size > _MAX_ESCROW:
        raise CheckpointError('checkpoint escrow exceeds its size limit')
    escrow = handle.read(escrow_size)
    if len(escrow) != escrow_size:
        raise CheckpointError("checkpoint is truncated")
    try:
        return generation.decode("ascii"), escrow
    except UnicodeDecodeError:
        raise CheckpointError('checkpoint generation is malformed') from None


def read_escrow(path: Path, passphrase: str) -> dict[str, bytes]:
    """Recover key material with only the checkpoint file and the passphrase."""
    with path.open("rb") as handle:
        _, escrow = _read_head(handle)
    return _decrypt_escrow(escrow, passphrase)


def _decrypt_escrow(escrow: bytes, passphrase: str) -> dict[str, bytes]:
    if escrow[:3] != _ESCROW_MAGIC or len(escrow) < 31:
        raise CheckpointError("escrow section is malformed")
    salt, nonce, body = escrow[3:19], escrow[19:31], escrow[31:]
    try:
        raw = AESGCM(_kdf(passphrase, salt)).decrypt(nonce, body, _ESCROW_MAGIC)
    except InvalidTag:
        raise CheckpointError("wrong passphrase or damaged escrow section") from None
    try:
        values = json.loads(raw)
        if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
            raise ValueError
        return {k: base64.b64decode(v, validate=True) for k, v in values.items()}
    except (ValueError, TypeError):
        raise CheckpointError('checkpoint escrow is malformed') from None


class _Writer:
    def __init__(self, handle: BinaryIO, cipher: EnvelopeCipher, generation: str):
        self._h, self._cipher, self._gen, self._seq = handle, cipher, generation, 0

    def frame(self, kind: str, data: bytes) -> None:
        envelope, _ = self._cipher.encrypt(data, aad=f"ck|{self._gen}|{self._seq}|{kind}".encode())
        self._h.write(kind.encode() + struct.pack(">I", len(envelope)) + envelope)
        self._seq += 1


def _frames(handle: BinaryIO, cipher: EnvelopeCipher, generation: str,
            audit: Callable[[bytes], None] | None = None) -> Iterator[tuple[str, bytes]]:
    seq = 0
    while True:
        head = handle.read(5)
        if len(head) != 5:
            raise CheckpointError("checkpoint is truncated")
        kind = head[:1].decode("ascii", "replace")
        (length,) = struct.unpack(">I", head[1:])
        envelope = handle.read(length) if 0 < length <= _MAX_FRAME else b""
        if kind not in "hdemz" or len(envelope) != length or not length:
            raise CheckpointError("checkpoint is truncated or malformed")
        if audit:
            audit(hashlib.sha256(head + envelope).digest())
        try:
            data = cipher.decrypt(envelope, aad=f"ck|{generation}|{seq}|{kind}".encode())
        except DecryptionError:
            raise CheckpointError("checkpoint frame failed authentication") from None
        seq += 1
        yield kind, data
        if kind == "z":
            if handle.read(1):
                raise CheckpointError("data after the checkpoint terminator")
            return


def _object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise CheckpointError('checkpoint frame metadata is malformed') from None
    if not isinstance(value, dict):
        raise CheckpointError('checkpoint frame metadata must be an object')
    return value


def _parse_manifest(raw: bytes, generation: str, escrow: bytes) -> dict[str, Any]:
    manifest = _object(raw)
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'artha.checkpoint.v2'
            or manifest.get('generation') != generation
            or not isinstance(manifest.get('profile'), str)
            or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', manifest['profile'])):
        raise CheckpointError('checkpoint manifest generation or schema is invalid')
    if manifest.get('escrow_sha256') != hashlib.sha256(escrow).hexdigest():
        raise CheckpointError('checkpoint recovery escrow does not match its authenticated manifest')
    return manifest


def _members(path: Path | BinaryIO, cipher: EnvelopeCipher,
             audit: Callable[[bytes], None] | None = None) -> Iterator[tuple[str, dict[str, Any], Iterator[bytes]]]:
    """Stream (kind, header, chunks); 'end' carries the member's recorded size/hash, 'manifest' the manifest."""
    with path.open("rb") if isinstance(path, Path) else nullcontext(path) as handle:
        generation, escrow = _read_head(handle)
        if audit:
            audit(hashlib.sha256(_MAGIC + struct.pack('>H', len(generation)) + generation.encode()
                                 + struct.pack('>I', len(escrow)) + escrow).digest())
        current: dict[str, Any] | None = None
        manifest_parts: list[bytes] = []
        manifest_bytes = 0
        for kind, data in _frames(handle, cipher, generation, audit):
            if kind == "m":
                manifest_bytes += len(data)
                if manifest_bytes > _MAX_MANIFEST_BYTES:
                    raise CheckpointError("checkpoint manifest exceeds its size limit")
                manifest_parts.append(data)
                continue
            if manifest_parts:
                yield "manifest", _parse_manifest(b"".join(manifest_parts), generation, escrow), iter(())
                manifest_parts = []
            if kind == "h":
                current = _object(data)
                yield "member", current, iter(())
            elif kind == "d":
                yield "data", current or {}, iter((data,))
            elif kind == "e":
                yield "end", {**(current or {}), **_object(data)}, iter(())
                current = None
            else:
                yield "terminator", _object(data), iter(())


def _safe(key: str) -> str:
    parts = key.split("/")
    reserved = re.compile(r'(?i)^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$')
    if (len(parts) < 2 or key.startswith(("/", "\\")) or set(parts) & {'', '.', '..'}
            or any(character in key for character in '\\:<>"|?*')
            or any(ord(character) < 32 for character in key)
            or any(part.endswith(('.', ' ')) or reserved.fullmatch(part.split('.')[0].rstrip(' ')) for part in parts)
            or parts[0].casefold() in {'.artha-restored.json', '.artha-restored.json.tmp'}):
        raise CheckpointError("unsafe portable path in checkpoint")
    return key


class _PathNames:
    """Detects member paths that collide once case and Unicode normalization are ignored (restore-platform aliases)."""

    def __init__(self) -> None:
        self._names: set[str] = set()
        self._directories: dict[str, str] = {}

    def add(self, path: str) -> None:
        _safe(path)
        identity = unicodedata.normalize('NFC', path).casefold()
        if identity in self._names:
            raise CheckpointError('duplicate checkpoint member path')
        if identity in self._directories:
            raise CheckpointError('conflicting file/directory path in checkpoint')
        parts = path.split('/')
        for index in range(1, len(parts)):
            parent = '/'.join(parts[:index])
            normalized = unicodedata.normalize('NFC', parent).casefold()
            if normalized in self._names or self._directories.get(normalized, parent) != parent:
                raise CheckpointError('ambiguous directory path in checkpoint')
            self._directories[normalized] = parent
        self._names.add(identity)


def _portability_problems(found: Mapping[str, Path]) -> list[str]:
    """Live paths a restore on another platform would rename, merge or refuse; capture must block on them."""
    names, problems = _PathNames(), []
    for key in sorted(found):
        try:
            names.add(key)
        except CheckpointError as exc:
            kind = 'not portable to every restore platform' if 'unsafe' in str(exc) else 'path alias of another member'
            problems.append(f'{key}: {kind}')
    return problems


def _scan(  # noqa: PLR0912, PLR0915 - explicit stream state machine
          path: Path | BinaryIO, cipher: EnvelopeCipher, sink: Callable[[dict[str, Any], bytes], None] | None = None,
          opened: Callable[[dict[str, Any]], None] | None = None, *,
          audit: Callable[[bytes], None] | None = None,
          ended: Callable[[], None] | None = None, verify_ledgers: bool = False) -> dict[str, Any]:
    """Authenticate and re-hash the whole stream; returns the manifest. Raises on any inconsistency."""
    hashes: dict[str, Any] = {}
    open_member = False
    manifest: dict[str, Any] | None = None
    members = 0
    captured: list[dict[str, Any]] = []
    names = _PathNames()
    member: dict[str, Any] = {}
    ledger_image: bytearray | None = None
    stream = _members(path, cipher, audit) if audit else _members(path, cipher)
    for kind, header, chunks in stream:
        if kind == "member":
            if open_member or manifest is not None:
                raise CheckpointError('checkpoint member is out of order')
            if (not isinstance(header.get('path'), str) or header.get('kind') not in {'file', 'sqlite'}
                    or type(header.get('mode')) is not int or not 0 <= header['mode'] <= 0o7777):
                raise CheckpointError('checkpoint member header is malformed')
            names.add(header['path'])
            member = {key: header[key] for key in ('path', 'kind', 'mode')}
            hashes = {"digest": hashlib.sha256(), "size": 0}
            open_member = True
            ledger_image = bytearray() if verify_ledgers and _privacy_ledger(member) else None
            if opened:
                opened(header)
        elif kind in ("data", "end") and not open_member:
            raise CheckpointError("member content without a header")
        elif kind == "data":
            (chunk,) = chunks
            hashes["digest"].update(chunk)
            hashes["size"] += len(chunk)
            if ledger_image is not None:
                if hashes["size"] > _MAX_SQLITE_BYTES:
                    raise CheckpointBlocked("restored privacy ledger exceeds its bounded preflight")
                ledger_image.extend(chunk)
            if sink:
                sink(header, chunk)
        elif kind == "end":
            if (type(header.get('size')) is not int or hashes["digest"].hexdigest() != header.get("sha256")
                    or hashes["size"] != header["size"]):
                raise CheckpointError('member content does not match the manifest')
            if any(header.get(key) != value for key, value in member.items()):
                raise CheckpointError('checkpoint member identity changed')
            captured.append({**member, 'size': hashes['size'], 'sha256': header['sha256'],
                             'source_sha256': header['sha256']})
            if ledger_image is not None:
                _verify_ledger_image(ledger_image)
                ledger_image = None
            members += 1
            open_member = False
            if ended:
                ended()
        elif kind == "manifest":
            entries = header.get('entries')
            if (open_member or manifest is not None or entries != captured
                    or any(type(entry[key]) is not type(value) for entry, actual in zip(entries, captured, strict=True)
                           for key, value in actual.items())):
                raise CheckpointError('checkpoint members do not match its manifest')
            manifest = header
        elif open_member or manifest is None or type(header.get('members')) is not int or header['members'] != members:
            raise CheckpointError("checkpoint members do not match its manifest")
    if manifest is None:
        raise CheckpointError("checkpoint has no manifest")
    return manifest


def verify(path: Path, cipher: EnvelopeCipher) -> dict[str, Any]:
    """Decrypt and re-hash every member; any mismatch, missing, extra, reordered or truncated part fails."""
    manifest = _scan(path, cipher)
    return manifest


def _outside(candidate: Path, roots: Sequence[Path]) -> bool:
    target = candidate.resolve()
    protected = [root.resolve() for root in roots]
    return not any(target == root or root in target.parents or target in root.parents for root in protected)


def _chunks(data: bytes) -> Iterator[bytes]:
    for start in range(0, len(data), _CHUNK):
        yield data[start:start + _CHUNK]


def _stream_signature(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    log = path.with_name(path.name + '-wal')
    return info.st_size, info.st_mtime_ns, info.st_ino, log.stat().st_size if log.exists() else 0


def _has_pending_log(path: Path) -> bool:
    """Committed pages that exist only in a write-ahead log or a hot journal cannot be read from the main file."""
    for suffix in ('-wal', '-journal'):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            return True
    return False


def _stream_sqlite(path: Path) -> Iterator[bytes]:
    """Stream a database whose main file is complete, holding one read transaction; memory stays one chunk.

    A rollback-journal writer is blocked by the read lock.  A write-ahead-log writer is not, so any change to the
    file or its log between the start and the end of the stream refuses the capture.
    """
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as src:
        src.execute('BEGIN')
        src.execute('SELECT count(*) FROM sqlite_master').fetchone()
        if _has_pending_log(path):
            raise CheckpointBlocked('state changed during capture')
        if (
            src.execute('PRAGMA integrity_check').fetchone() != ('ok',)
            or src.execute('PRAGMA foreign_key_check').fetchone()
        ):
            raise sqlite3.DatabaseError('database integrity or foreign keys are invalid')
        before = _stream_signature(path)
        first = True
        with path.open('rb') as handle:
            for piece in iter(lambda: handle.read(_CHUNK), b''):
                header = first and len(piece) >= 100 and piece[:16] == _SQLITE_HEADER
                first = False
                # The restored copy never needs a log: write and read versions are forced to rollback-journal mode.
                yield piece[:18] + b'\x01\x01' + piece[20:] if header else piece
        if _stream_signature(path) != before or _has_pending_log(path):
            raise CheckpointBlocked('state changed during capture')


def _memory_sqlite_chunks(path: Path) -> Iterator[bytes]:
    """Consistent in-memory online backup of a database with pages only in its log; bounded by the image limit."""
    if path.stat().st_size > _MAX_SQLITE_BYTES:
        raise CheckpointBlocked("database exceeds the in-memory online backup limit")
    with (
        closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(":memory:")) as dst,
    ):
        page_size = int(src.execute('PRAGMA page_size').fetchone()[0])

        def bounded(_status: int, _remaining: int, pages: int) -> None:
            if pages * page_size > _MAX_SQLITE_BYTES:
                raise CheckpointBlocked('database exceeds the in-memory online backup limit')

        # Main-file size omits committed WAL pages. Check before allocation and during a growing backup.
        bounded(0, 0, int(src.execute('PRAGMA page_count').fetchone()[0]))
        src.backup(dst, pages=1024, progress=bounded)
        if (
            dst.execute("PRAGMA integrity_check").fetchone() != ("ok",)
            or dst.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise sqlite3.DatabaseError("database integrity or foreign keys are invalid")
        dst.execute("PRAGMA journal_mode=DELETE")
        raw = bytearray(dst.serialize())
        if len(raw) >= 100 and raw[:16] == _SQLITE_HEADER:
            raw[18] = 1
            raw[19] = 1
        image = bytes(raw)
    if len(image) > _MAX_SQLITE_BYTES:
        raise CheckpointBlocked("database exceeds the in-memory online backup limit")
    yield from _chunks(image)


def _sqlite_chunks(path: Path) -> Iterator[bytes]:
    """A consistent database image in bounded chunks, never written to disk as plaintext."""
    if _has_pending_log(path):
        yield from _memory_sqlite_chunks(path)
    else:
        yield from _stream_sqlite(path)


def _sqlite_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for chunk in _sqlite_chunks(path):
        digest.update(chunk)
    return digest.hexdigest()


def _image(path: Path) -> bytes:
    return b"".join(_sqlite_chunks(path))


def _stream_member(writer: _Writer, key: str, path: Path, source: str, mode: int) -> Entry:
    """Write one member, hashing as it goes; the hash must equal the first-pass hash or the state moved."""
    sqlite = _is_sqlite(path)
    writer.frame("h", json.dumps({"path": key, "kind": "sqlite" if sqlite else "file", "mode": mode}).encode())
    digest, size = hashlib.sha256(), 0
    if sqlite:
        for piece in _sqlite_chunks(path):
            digest.update(piece)
            size += len(piece)
            writer.frame("d", piece)
    else:
        with session_lock_reader(path) as handle:
            for piece in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(piece)
                size += len(piece)
                writer.frame("d", piece)
    if digest.hexdigest() != source:
        raise CheckpointBlocked("state changed during capture")
    writer.frame("e", json.dumps({"size": size, "sha256": source}).encode())
    return Entry(key, size, source, mode, "sqlite" if sqlite else "file", source)


def _first_pass(found: Mapping[str, Path]) -> tuple[dict[str, str], dict[str, tuple[int, int]], list[str]]:
    source: dict[str, str] = {}
    stats: dict[str, tuple[int, int]] = {}
    problems: list[str] = []
    for key, path in sorted(found.items()):
        try:
            if _is_sqlite(path):
                source[key] = _sqlite_sha(path)
            else:
                source[key] = _file_sha(path)
            info = path.stat()
            stats[key] = (info.st_size, info.st_mtime_ns)
        except Exception as exc:  # any failure must block, reporting only its class
            problems.append(f"{key}: unreadable ({type(exc).__name__})")
    return source, stats, problems


def _unchanged(roots: Mapping[str, Path], exclude: Mapping[str, str], source: Mapping[str, str],
               stats: Mapping[str, tuple[int, int]]) -> None:
    found, problems, _ = _walk(roots, exclude)
    if problems or set(found) != set(source):
        raise CheckpointBlocked('state changed during capture')
    hashes, current_stats, problems = _first_pass(found)
    if problems or hashes != source or any(current_stats[k] != stats[k] for k in stats):
        raise CheckpointBlocked('state changed during capture')


def _sync_directory(path: Path) -> None:
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def create(  # noqa: PLR0912, PLR0915 - one fail-closed capture transaction
           profile: str, roots: Mapping[str, Path], dest_dir: Path, cipher: EnvelopeCipher,
           escrow: Mapping[str, bytes], passphrase: str, *, now: datetime, lock_path: Path,
           exclude: Mapping[str, str] | None = None, context: Mapping[str, Any] | None = None,
           quiesce: Callable[[], Sequence[str]] = lambda: (),
           before_publish: Callable[[], None] = lambda: None) -> Path:
    """Capture one coherent generation, or raise :class:`CheckpointBlocked` and publish nothing."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", profile):
        raise CheckpointError("invalid profile name")
    _check_passphrase(passphrase)
    if not _outside(dest_dir, list(roots.values())):
        raise CheckpointBlocked("destination is inside the checkpointed tree")
    with session_lock(lock_path):
        if dest_dir.exists() and any(dest_dir.glob('.*.ack.part')):
            raise CheckpointBlocked('interrupted checkpoint needs recovery review; partial ciphertext was preserved')
        busy = list(quiesce())
        if busy:
            raise CheckpointBlocked("not quiesced: " + "; ".join(busy))
        found, problems, excluded = _walk(roots, exclude or {})
        source, stats, more = _first_pass(found)
        problems += more
        if problems:
            reasons = sorted({problem.rsplit(': ', 1)[-1] for problem in problems})
            raise CheckpointBlocked(f'inventory incomplete: {len(problems)} item(s); ' + '; '.join(reasons))
        generation = f"ck_{now.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
        target = dest_dir / f"artha-{profile}-{generation}.ack"
        if target.exists():
            raise CheckpointBlocked("checkpoint generations are write-once")
        dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        part = dest_dir / f".{target.name}.part"
        sealed = _seal_escrow(escrow, passphrase)
        entries: list[Entry] = []
        published = False
        owned_part = False
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            owned_part = True
            with os.fdopen(fd, "wb") as handle:
                gen = generation.encode()
                handle.write(_MAGIC + struct.pack(">H", len(gen)) + gen + struct.pack(">I", len(sealed)) + sealed)
                writer = _Writer(handle, cipher, generation)
                for key in sorted(found):
                    entries.append(_stream_member(writer, key, found[key], source[key],
                                                  stat.S_IMODE(found[key].stat().st_mode)))
                manifest = {"schema": "artha.checkpoint.v2", "generation": generation, "profile": profile,
                            'escrow_sha256': hashlib.sha256(sealed).hexdigest(),
                            "created_at": now.isoformat(), "entries": [asdict(e) for e in entries],
                            "excluded": excluded, "context": dict(context or {}), "escrow_items": sorted(escrow)}
                manifest_raw = json.dumps(manifest, sort_keys=True).encode()
                if len(manifest_raw) > _MAX_MANIFEST_BYTES:
                    raise CheckpointBlocked("checkpoint manifest exceeds its size limit")
                for piece in _chunks(manifest_raw):
                    writer.frame("m", piece)
                writer.frame("z", json.dumps({"members": len(entries)}).encode())
                handle.flush()
                os.fsync(handle.fileno())
            before_publish()
            _unchanged(roots, exclude or {}, source, stats)
            verify(part, cipher)
            try:
                os.link(part, target)
            except FileExistsError:
                raise CheckpointBlocked('checkpoint generations are write-once') from None
            published = True
            part.unlink()
            _sync_directory(dest_dir)
        except BaseException:
            if owned_part:
                part.unlink(missing_ok=True)
            if published:                      # only a checkpoint this run wrote, never a competitor's file
                target.unlink(missing_ok=True)
            raise
        return target


def copy_to(source: Path, destination: Path, roots: Sequence[Path], *, cloud_synced: bool = False) -> Path:
    """Write-once copy to an independent location, verified by re-hash after writing."""
    if not _outside(destination, roots):
        raise CheckpointBlocked("destination is inside the checkpointed tree")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not cloud_synced and destination.stat().st_dev == source.parent.stat().st_dev:
        raise CheckpointBlocked("destination is on the same device; pass cloud_synced for a synced folder")
    target = destination / source.name
    if target.exists():
        raise CheckpointBlocked("checkpoint generations are write-once")
    part = destination / f".{source.name}.part"
    owned_part = False
    try:
        with source.open("rb") as src, part.open("xb") as dst:
            owned_part = True
            shutil.copyfileobj(src, dst, 1 << 20)
            dst.flush()
            os.fsync(dst.fileno())
        os.chmod(part, 0o600)
        if _file_sha(part) != _file_sha(source):
            raise CheckpointError("copy does not match the original")
        try:
            os.link(part, target)
        except FileExistsError:
            raise CheckpointBlocked('checkpoint generations are write-once') from None
        _sync_directory(destination)
    finally:
        if owned_part:
            part.unlink(missing_ok=True)
    return target


def _restore_destination(dest: Path, live_roots: Sequence[Path]) -> None:
    for parent in (dest, *dest.parents):
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise CheckpointBlocked('restore destination has linked or reparse ancestry')
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CheckpointBlocked('restore needs a new or empty directory')
    target = dest.resolve()
    for root in live_roots:
        protected = (root.parent if root.is_file() else root).resolve()
        if target.is_relative_to(protected) or protected.is_relative_to(target):
            raise CheckpointBlocked('restore cannot overlap the live tree')


def _restore_marker(generation: str) -> dict[str, str]:
    return {'restored_from': generation, 'ingestion': 'disabled', 'effects': 'disabled',
            'actions': 'quarantined', 'schedulers': 'disabled'}


@contextmanager
def _restore_tree(dest: Path, live_roots: Sequence[Path], marker: dict[str, Any]) -> Iterator[None]:
    _restore_destination(dest, live_roots)  # Recheck after preflight, before claiming this directory.
    if sys.platform == 'win32':
        with _native_restore_tree(dest, marker):
            yield
        return
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(dest / '.artha-restored.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CheckpointBlocked('another restore owns the destination') from None
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(json.dumps(marker).encode())
            handle.flush()
            os.fsync(handle.fileno())
        dest.chmod(0o700)
        _sync_directory(dest)
        yield
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


@contextmanager
def _native_restore_tree(dest: Path, marker: dict[str, Any]) -> Iterator[None]:
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory  # noqa: PLC0415
    try:
        (dest / '.artha-restored.json').lstat()
    except FileNotFoundError:
        pass
    else:
        raise CheckpointBlocked('another restore owns the destination')
    dest.parent.mkdir(parents=True, exist_ok=True)
    directory = (WindowsQuarantineDirectory.open(dest) if dest.exists() else
                 WindowsQuarantineDirectory.create(dest, lock_file=False))
    owned = False
    try:
        with directory:
            with directory.create_file('.artha-restored.json') as handle:
                owned = True
                handle.write_all(json.dumps(marker).encode())
                handle.flush()
            yield
    except BaseException:
        if owned:
            shutil.rmtree(dest, ignore_errors=True)
        raise


def _native_restore_parent(dest: Path, parent: Path) -> WindowsQuarantineDirectory:
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory  # noqa: PLC0415
    current = dest
    for part in parent.relative_to(dest).parts:
        current = current / part
        if not current.exists():
            with WindowsQuarantineDirectory.create(current, lock_file=False):
                pass
    return WindowsQuarantineDirectory.open(parent)


@contextmanager
def _restore_file(dest: Path, target: Path) -> Iterator[Callable[[bytes], object]]:
    if sys.platform == 'win32':
        with _native_restore_parent(dest, target.parent) as parent, parent.create_file(target.name) as native:
            try:
                yield native.write_all
            finally:
                native.flush()
    else:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            try:
                yield handle.write
            finally:
                handle.flush()
                os.fsync(handle.fileno())


def _extract(source: BinaryIO, cipher: EnvelopeCipher, dest: Path,
             audit: Callable[[bytes], None], *, quarantine: Any = None,
             manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    current: Callable[[bytes], object] | None = None
    output = ExitStack()
    encrypted = False
    entries = {entry['path']: entry for entry in manifest['entries']} if manifest is not None else {}

    def finish() -> None:
        nonlocal current
        if current is not None:
            current = None
            output.close()

    def opened(header: dict[str, Any]) -> None:
        nonlocal current, encrypted
        finish()
        encrypted = quarantine is not None and not _privacy_ledger(header)
        if encrypted:
            quarantine.start(entries[header['path']])
            return
        target = dest / header['path']
        current = output.enter_context(_restore_file(dest, target))

    def sink(_header: dict[str, Any], chunk: bytes) -> None:
        if encrypted:
            from catchup_core.recovery_quarantine import CHUNK_BYTES  # noqa: PLC0415
            for offset in range(0, len(chunk), CHUNK_BYTES):
                quarantine.write(chunk[offset:offset + CHUNK_BYTES])
            return
        if current is None:
            raise CheckpointError('checkpoint member data lacks an open target file')
        current(chunk)

    def ended() -> None:
        nonlocal encrypted
        if encrypted:
            encrypted = False
            quarantine.finish()
        else:
            finish()

    try:
        if quarantine is None:
            return _scan(source, cipher, sink, opened, audit=audit)
        restored = _scan(source, cipher, sink, opened, audit=audit, ended=ended)
        quarantine.seal()
        return restored
    finally:
        finish()


def _privacy_ledger(entry: Mapping[str, Any]) -> bool:
    return entry['kind'] == 'sqlite' and entry['path'] in {
        'local/catchup_core/ledger.db', 'local/catchup_core/shadow/ledger.db'}


def _verify_ledger_database(db: sqlite3.Connection) -> None:
    """A filename cannot turn an arbitrary historical SQLite image into proven privacy-ledger storage."""
    from catchup_core.ledger import Ledger, MigrationRefused  # noqa: PLC0415
    from catchup_core.schema_history import _expected, sources, verify  # noqa: PLC0415
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        installed = sources(Ledger.available_migrations())
        version = int(db.execute('PRAGMA user_version').fetchone()[0])
        if not 1 <= version <= len(installed):
            raise CheckpointError('restored privacy ledger database has an unsupported schema version')
        verify(db, installed, version)
        objects = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
        if objects != set(_expected(installed[:version])):
            raise CheckpointError('restored privacy ledger contains unproven database objects')
        if (db.execute('PRAGMA integrity_check').fetchone() != ('ok',)
                or db.execute('PRAGMA foreign_key_check').fetchone()):
            raise CheckpointError('restored privacy ledger database integrity or foreign keys are invalid')
    except (sqlite3.Error, MigrationRefused):
        raise CheckpointError('restored privacy ledger database schema or integrity cannot be verified') from None


def _verify_ledger_image(image: bytearray) -> None:
    try:
        with closing(sqlite3.connect(':memory:')) as db:
            db.deserialize(image)
            _verify_ledger_database(db)
            pages = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
            if len(image) != pages:
                raise CheckpointError('restored privacy ledger database has unaccounted trailing bytes')
    except sqlite3.Error:
        raise CheckpointError('restored privacy ledger database image is invalid') from None


def _verify_replayed_ledgers(dest: Path, manifest: Mapping[str, Any]) -> None:
    for entry in manifest['entries']:
        if not _privacy_ledger(entry):
            continue
        path = dest / entry['path']
        if not _is_sqlite(path):
            raise CheckpointError('restored privacy ledger database header is invalid after replay')
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                _verify_ledger_database(db)
                size = path.stat().st_size
                pages = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
                if size > _MAX_SQLITE_BYTES or size != pages:
                    raise CheckpointError('replayed privacy ledger database has unaccounted or oversized bytes')
        except sqlite3.Error:
            raise CheckpointError('restored privacy ledger database cannot be verified after replay') from None


def _restored_inventory(dest: Path, expected: Mapping[str, Any], quarantine: Any = None) -> None:
    found: set[str] = set()
    directories_expected = {parent.as_posix() for name in expected for parent in Path(name).parents
                            if parent != Path('.')}
    if quarantine is not None:
        if Path(quarantine.root) != Path(os.path.abspath(dest / '.artha-quarantine')):
            raise CheckpointError('restored quarantine root is outside its owned directory')
        directories_expected.add('.artha-quarantine')

    def unreadable(_error: OSError) -> None:
        raise CheckpointError('restored inventory cannot be read') from None

    for current, directories, files in os.walk(dest, followlinks=False, onerror=unreadable):
        for name in [*directories, *files]:
            path = Path(current) / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                raise CheckpointError('restored inventory contains links or reparse points')
            relative = path.relative_to(dest).as_posix()
            if stat.S_ISDIR(metadata.st_mode):
                if quarantine is not None and relative not in directories_expected:
                    raise CheckpointError('restored inventory contains unexpected directories')
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise CheckpointError('restored inventory contains linked or special files')
            if quarantine is not None and path.is_relative_to(quarantine.root):
                continue
            if relative != '.artha-restored.json':
                found.add(relative)
    if found != set(expected):
        raise CheckpointError('restored inventory differs from its authenticated manifest')


def _verify_quarantine(quarantine: Any, manifest: Mapping[str, Any], *, replayed: bool = False) -> None:
    marker = Path(quarantine.root).parent / '.artha-restored.json'
    if _object(marker.read_bytes()) != _restore_marker(manifest['generation']):
        raise CheckpointError('restored installation marker changed')
    quarantine.verify()
    inventory = quarantine.inventory()
    expected = {entry['path']: entry for entry in manifest['entries'] if not _privacy_ledger(entry)}
    captured = {entry['path']: {key: value for key, value in entry.items()
                              if key not in {'member_id', 'state', 'chunks', 'reason'}} for entry in inventory}
    states = {'quarantined', 'blocked'} if replayed else {'quarantined'}
    if (len(inventory) != len(expected) or captured != expected
            or any(entry.get('state') not in states for entry in inventory)):
        raise CheckpointError('restored quarantine catalog differs from its authenticated manifest')


def _verify_restored(dest: Path, manifest: dict[str, Any], *, quarantine: Any = None) -> tuple[int, int]:
    expected = {entry['path']: entry for entry in manifest['entries']
                if quarantine is None or _privacy_ledger(entry)}
    _restored_inventory(dest, expected, quarantine)
    if quarantine is not None:
        _verify_quarantine(quarantine, manifest)
    databases = 0
    for name, entry in expected.items():
        path = dest / name
        if path.stat().st_size != entry['size'] or _file_sha(path) != entry['sha256']:
            raise CheckpointError('restored file differs from its authenticated manifest')
        is_database = _is_sqlite(path)
        if is_database != (entry['kind'] == 'sqlite'):
            raise CheckpointError('restored database kind or header is invalid')
        if is_database:
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                    if (db.execute('PRAGMA integrity_check').fetchone() != ('ok',)
                            or db.execute('PRAGMA foreign_key_check').fetchone()):
                        raise CheckpointError('restored database integrity or foreign keys are invalid')
            except sqlite3.Error:
                raise CheckpointError('restored database integrity cannot be verified') from None
            databases += 1
    return sum(entry['size'] for entry in manifest['entries']), databases


class _DigestReader:
    def __init__(self, source: BinaryIO):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        raw = self.source.read(size)
        self.digest.update(raw)
        return raw


def restore(path: Path, dest: Path, cipher: EnvelopeCipher, live_roots: Sequence[Path], *,
            replay_privacy: Callable[[Path], None] | None = None,
            expected_profile: str = 'personal',
            quarantine_factory: Callable[[Path, Mapping[str, Any], str], Any] | None = None) -> dict[str, Any]:
    """Authenticate and verify output before invoking the injected privacy policy.

    The hook must enforce current privacy for the entire returned tree, not only ledgers. This generic
    primitive cannot prove external-copy lineage; the CLI supplies a fail-closed policy. A hook refusal
    removes the staged tree and leaves the original ciphertext unchanged for owner-reviewed handling.
    """
    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError  # noqa: PLC0415
    try:
        return _restore(path, dest, cipher, live_roots, replay_privacy, expected_profile,
                quarantine_factory=quarantine_factory)
    except OSError as exc:
        raise CheckpointError(f'restore input or output is unavailable ({type(exc).__name__})') from None
    except QuarantineFilesystemError:
        raise CheckpointBlocked('native restore filesystem is unavailable or unsafe') from None


def _restore(  # noqa: PLR0912, PLR0915 - ordered authenticated restore and privacy replay
             path: Path, dest: Path, cipher: EnvelopeCipher, live_roots: Sequence[Path],
             replay_privacy: Callable[[Path], None] | None, expected_profile: str, *,
             quarantine_factory: Callable[[Path, Mapping[str, Any], str], Any] | None = None) -> dict[str, Any]:
    if expected_profile == 'work' and quarantine_factory is None:
        raise CheckpointBlocked('Work restoration requires encrypted quarantine and current local authority')
    if path.resolve().is_relative_to(dest.resolve()):
        raise CheckpointBlocked('restore destination cannot contain its input checkpoint')
    _restore_destination(dest, live_roots)
    if replay_privacy is None:
        raise CheckpointBlocked('restore requires the current privacy history to be replayed')
    if not stat.S_ISREG(path.stat().st_mode):
        raise CheckpointBlocked('restore input must be a regular checkpoint file')
    with path.open('rb') as source, tempfile.TemporaryFile(mode='w+b') as fingerprints:
        def record(digest: bytes) -> None:
            fingerprints.write(digest)
        reader = _DigestReader(source) if quarantine_factory is not None else None
        manifest = _scan(cast(BinaryIO, reader) if reader is not None else source, cipher, audit=record,
                         verify_ledgers=quarantine_factory is not None)
        if manifest['profile'] != expected_profile:
            raise CheckpointBlocked('checkpoint profile does not match the restoration privacy policy')
        if expected_profile == 'work':
            context = manifest.get('context')
            binding = context.get('work_binding') if isinstance(context, dict) else None
            if (not isinstance(binding, dict)
                    or binding.get('schema') != 'artha.work-checkpoint-binding.v1'
                    or binding.get('profile') != 'work' or binding.get('plaintext_allowed') is not False):
                raise CheckpointBlocked('unbound Work checkpoints are inspection-only')
        checkpoint_sha256 = reader.digest.hexdigest() if reader is not None else ''
        marker = _restore_marker(manifest['generation'])
        fingerprints.seek(0)
        source.seek(0)
        def unchanged(digest: bytes) -> None:
            if fingerprints.read(32) != digest:
                raise CheckpointError('checkpoint changed after authenticated preflight')
        with _restore_tree(dest, live_roots, marker):
            quarantine = None
            if quarantine_factory is not None:
                quarantine = quarantine_factory(dest, manifest, checkpoint_sha256)
                if quarantine is None or Path(quarantine.root) != Path(os.path.abspath(dest / '.artha-quarantine')):
                    raise CheckpointError('restored quarantine root is outside its owned directory')
            restored = (_extract(source, cipher, dest, unchanged) if quarantine is None else
                        _extract(source, cipher, dest, unchanged, quarantine=quarantine, manifest=manifest))
            if fingerprints.read(1) or restored != manifest:
                raise CheckpointError('checkpoint changed after authenticated preflight')
            verified_bytes, verified_sqlite = _verify_restored(dest, manifest, quarantine=quarantine)
            transformed = []
            for entry in manifest['entries']:
                if quarantine is not None and not _privacy_ledger(entry):
                    continue
                mode = entry['mode'] & 0o777 or 0o600
                os.chmod(dest / entry['path'], mode)
                if mode != entry['mode']:
                    transformed.append({'path': entry['path'], 'reason': 'permissions normalized'})
            transformed.extend(_quarantine(dest))
            quarantined = [entry for entry in manifest['entries'] if not _privacy_ledger(entry)] if quarantine else []
            quarantined_bytes = sum(entry['size'] for entry in quarantined)
            if quarantined:
                transformed.append({'path': '.artha-quarantine', 'reason':
                                    f'{len(quarantined)} historical members ({quarantined_bytes} bytes) encrypted'})
            replay_privacy(dest)
            if quarantine is not None:
                _restored_inventory(dest, {entry['path']: entry for entry in manifest['entries']
                                           if _privacy_ledger(entry)}, quarantine)
                _verify_quarantine(quarantine, manifest, replayed=True)
                _verify_replayed_ledgers(dest, manifest)
    result = {'generation': manifest['generation'], 'files': len(manifest['entries']),
            'verified_bytes': verified_bytes, 'verified_sqlite': verified_sqlite,
            'excluded': manifest['excluded'], 'transformed': transformed, 'marker': marker}
    if quarantine_factory is not None:
        result.update(quarantined_files=len(quarantined), quarantined_bytes=quarantined_bytes)
    return result


def _quarantine(dest: Path) -> list[dict[str, str]]:
    """Keep action history under a non-runtime name and disable configured cutover and dispatch."""
    import yaml  # noqa: PLC0415
    transformed = []
    for database in sorted(dest.rglob('actions.db')):
        target = database.with_name('actions.quarantined.db')
        if target.exists():
            raise CheckpointBlocked('restored action quarantine destination already exists')
        database.rename(target)
        transformed.append({'path': database.relative_to(dest).as_posix(), 'reason': 'action history quarantined'})
    for configuration in sorted(dest.rglob('config/catchup_core.yaml')):
        policy = yaml.safe_load(configuration.read_bytes())
        if not isinstance(policy, dict):
            raise CheckpointBlocked('restored runtime configuration is invalid')
        policy['cutover'] = False
        atomic_bytes(configuration, yaml.safe_dump(policy, sort_keys=False).encode())
        atomic_bytes(configuration.parent / 'actions.paused', b'Restored installation: effects disabled.\n')
        transformed.append({'path': configuration.relative_to(dest).as_posix(),
                            'reason': 'cutover and effects disabled'})
    return transformed


def _table_rows(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    escaped = table.replace('"', '""')
    columns = conn.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    pks = [str(row[1]) for row in sorted((row for row in columns if row[5] > 0), key=lambda row: int(row[5]))]
    col_names = [str(row[1]) for row in columns]
    if pks:
        pk_indices = [col_names.index(pk) for pk in pks]
        rows = conn.execute(f'SELECT * FROM "{escaped}"').fetchall()  # noqa: S608
        return {
            json.dumps([row[idx] for idx in pk_indices], default=str, sort_keys=True): hashlib.sha256(
                json.dumps(list(row), default=str, sort_keys=True).encode()
            ).hexdigest()
            for row in rows
        }
    rows = conn.execute(f'SELECT rowid, * FROM "{escaped}"').fetchall()  # noqa: S608
    return {
        str(row[0]): hashlib.sha256(
            json.dumps(list(row[1:]), default=str, sort_keys=True).encode()
        ).hexdigest()
        for row in rows
    }


def _sqlite_table_diff(saved_image: bytes, live_path: Path) -> dict[str, dict[str, int]]:
    live_image = _image(live_path)
    with closing(sqlite3.connect(":memory:")) as saved_db, closing(sqlite3.connect(":memory:")) as live_db:
        saved_db.deserialize(saved_image)
        live_db.deserialize(live_image)
        query = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        saved_tables = {str(r[0]) for r in saved_db.execute(query).fetchall()}
        live_tables = {str(r[0]) for r in live_db.execute(query).fetchall()}
        summary: dict[str, dict[str, int]] = {}
        for table in sorted(saved_tables | live_tables):
            before = _table_rows(saved_db, table) if table in saved_tables else {}
            after = _table_rows(live_db, table) if table in live_tables else {}
            added = len(set(after) - set(before))
            removed = len(set(before) - set(after))
            modified = sum(1 for key in (set(before) & set(after)) if before[key] != after[key])
            if added or removed or modified:
                summary[table] = {"added": added, "removed": removed, "changed": modified}
        return summary


def diff(path: Path, cipher: EnvelopeCipher, roots: Mapping[str, Path],
         exclude: Mapping[str, str] | None = None, *,
         include_tables: bool = False) -> dict[str, Any]:
    """Paths added, removed or changed in the live tree since the checkpoint (read-only)."""
    manifest = verify(path, cipher)
    found, problems, _ = _walk(roots, exclude or {})
    saved = {e["path"]: e for e in manifest["entries"]}

    def current(file: Path) -> str:
        return _sqlite_sha(file) if _is_sqlite(file) else _file_sha(file)
    changed = sorted(k for k, p in found.items() if k in saved and saved[k]["source_sha256"] != current(p))
    out: dict[str, Any] = {
        "added": sorted(set(found) - set(saved)),
        "removed": sorted(set(saved) - set(found)),
        "changed": changed,
        "unreadable": sorted(problems),
    }
    if include_tables:
        sqlite_changed = {k for k in changed if saved[k].get("kind") == "sqlite" and _is_sqlite(found[k])}
        # Table comparison needs both whole images in memory; larger databases are reported, never called clean.
        oversized = {k for k in sqlite_changed
                     if saved[k]['size'] > _MAX_SQLITE_BYTES or found[k].stat().st_size > _MAX_SQLITE_BYTES}
        sqlite_changed -= oversized
        blobs: dict[str, list[bytes]] = {k: [] for k in sqlite_changed}
        active_key: str | None = None

        def opened(header: dict[str, Any]) -> None:
            nonlocal active_key
            key = str(header["path"])
            active_key = key if key in blobs else None

        def sink(_header: dict[str, Any], chunk: bytes) -> None:
            if active_key is not None:
                blobs[active_key].append(chunk)

        if sqlite_changed:
            _scan(path, cipher, sink, opened)
        out["tables"] = {
            key: _sqlite_table_diff(b"".join(blobs[key]), found[key])
            for key in sorted(sqlite_changed)
        }
        out["tables_unavailable"] = sorted(oversized)
    return out


def reconcile(path: Path, cipher: EnvelopeCipher, roots: Mapping[str, Path],
              exclude: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read-only file and SQLite table-level reconciliation against a verified checkpoint."""
    return diff(path, cipher, roots, exclude, include_tables=True)
