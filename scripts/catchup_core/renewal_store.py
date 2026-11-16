"""Bounded encrypted, content-addressed artifacts for machine-local release metadata."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from catchup_core.crypto import DecryptionError, EnvelopeCipher, StaticKeyProvider
from catchup_core.quarantine_fs_windows import (
    QuarantineFilesystemError,
    WindowsQuarantineDirectory,
    ensure_private_directory,
    open_owner_private_path,
)
from catchup_core.session_lock import require_active_installation

MAX_BYTES = 1024 * 1024
REASONS = {
    'verification': 'required verification held; inspect the last completed stage',
    'inputs': 'delivery inputs changed; prepare and validate the exact candidate again',
    'authority': 'current native delegation is unavailable; review authority and expiry',
    'evidence': 'approved release evidence no longer applies; review the baseline',
    'gates': 'local validation failed; inspect make check and changed-line coverage',
    'ci': 'required CI is incomplete or held; resume in a user session or review its result',
    'budget': 'CI request budget exhausted; review this exact attempt',
    'journal': 'publication requires recovery; verify current inputs before recovering',
}


class RenewalError(RuntimeError):
    """Fixed technical diagnostic; never includes untrusted or private values."""
    def __init__(self, message: str, *, reason: str = 'verification'):
        super().__init__(message)
        self.reason = reason if reason in REASONS else 'verification'


def encoded(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("ascii")
    except (ValueError, TypeError, RecursionError):
        raise RenewalError("release metadata is invalid") from None
    if len(raw) > MAX_BYTES:
        raise RenewalError("release metadata exceeds its bound")
    return raw


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def is_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch("[0-9a-f]{64}", value))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RenewalError("release metadata has duplicate keys")
        result[key] = value
    return result


def decoded(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise RenewalError("release metadata exceeds its bound")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or encoded(value) != raw:
            raise RenewalError("release metadata is not canonical")
        return value
    except (ValueError, TypeError, RecursionError):
        raise RenewalError("release metadata cannot be decoded") from None


def _chain(path: Path) -> None:
    for current in reversed((path, *path.parents)):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RenewalError("release storage contains an alias")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise RenewalError("release storage ancestor is unsafe")



def private_directory(root: Path, *, create: bool = False) -> None:
    """Share native private-directory enforcement with the preserved evaluator cache."""
    require_active_installation(root)
    _chain(root)
    if sys.platform == "win32":
        if create:
            ensure_private_directory(root)
        with WindowsQuarantineDirectory.open(root):
            return
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise RenewalError("release storage is not owner-private")


def read_private(path: Path, *, maximum: int = MAX_BYTES + 64) -> bytes:
    private_directory(path.parent)
    _chain(path)
    if sys.platform == "win32":
        with open_owner_private_path(path) as handle:
            return handle.read(maximum)
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) & 0o077
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise RenewalError("release artifact is unsafe")
        raw = stream.read(maximum)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)):
        raise RenewalError("release artifact changed during read")
    return raw


class ArtifactStore:
    def __init__(self, root: Path, key: bytes):
        self.root = root.absolute()
        self.cipher = EnvelopeCipher(StaticKeyProvider({1: key}))

    def _directory(self, *, create: bool = False) -> None:
        private_directory(self.root, create=create)


    def _read(self, path: Path) -> bytes:
        self._directory()
        return read_private(path)

    def read(self, identity: str) -> dict[str, Any]:
        if not is_digest(identity):
            raise RenewalError("release artifact identity is invalid")
        try:
            raw = self._read(self.root / (identity + ".ae1"))
            plain = self.cipher.decrypt(raw, aad=("artha.renewal:" + identity).encode())
            value = decoded(plain)
            if digest(value) != identity:
                raise RenewalError("release artifact identity mismatch")
            return value
        except (OSError, DecryptionError, QuarantineFilesystemError):
            raise RenewalError("release artifact unavailable or unauthenticated") from None

    def put(self, value: dict[str, Any]) -> str:
        identity = digest(value)
        try:
            self._directory(create=True)
            path = self.root / (identity + ".ae1")
            if path.exists() or path.is_symlink():
                self.read(identity)
                return identity
            raw = self.cipher.encrypt(encoded(value), aad=("artha.renewal:" + identity).encode())[0]
            if sys.platform == "win32":
                with WindowsQuarantineDirectory.open(self.root) as directory:
                    temporary = identity + ".pending"
                    with directory.create_file(temporary) as staged:
                        staged.write_all(raw)
                        staged.flush()
                        pinned = staged.identity
                    directory.publish_write_once(temporary, path.name, expected_identity=pinned)
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=".renewal-", dir=self.root)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, path)
                finally:
                    os.unlink(temporary)
                descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            if self.read(identity) != value:
                raise RenewalError("release artifact readback failed")
            return identity
        except (OSError, QuarantineFilesystemError):
            raise RenewalError("release artifact publication unavailable") from None
