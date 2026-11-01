"""Encrypted evidence blobs (re-artha §7.6 evidence commit order, §7.7).

Evidence excerpts are written as AE1 envelopes under a machine-local directory and
referenced from the ledger by an opaque ref.  Order of operations is *blob first,
then ledger reference*; :meth:`sweep` removes orphans.  The ref is an HMAC of the
locator, so it reveals nothing about the source.
"""
from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterable
from pathlib import Path

from catchup_core import ids
from catchup_core.crypto import EnvelopeCipher

__all__ = ["BlobMissing", "EncryptedBlobStore"]


def private_atomic_bytes(path: Path, data: bytes) -> None:
    """Publish bytes privately; existing Windows targets must already be private."""
    if sys.platform != "win32":
        from catchup_core.backup import atomic_bytes  # noqa: PLC0415
        atomic_bytes(path, data)
        return
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory  # noqa: PLC0415
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with WindowsQuarantineDirectory.open(path.parent, owner_private=False, writable=True) as parent:
        try:
            path.lstat()
        except FileNotFoundError:
            replaced = None
        else:
            with parent.open_file(path.name) as current:
                replaced = current.identity
        stage = f".{uuid.uuid4().hex}.tmp"
        with parent.create_file(stage) as opened:
            identity = opened.identity
            try:
                opened.write_all(data)
                opened.flush()
            except BaseException:
                opened.close()
                parent.delete_file(stage, expected_identity=identity)
                raise
        try:
            if replaced is None:
                parent.publish_write_once(stage, path.name, expected_identity=identity)
            else:
                parent.replace_file(stage, path.name, expected_identity=identity, replaced_identity=replaced)
        except BaseException:
            try:
                (path.parent / stage).lstat()
            except FileNotFoundError:
                pass
            else:
                parent.delete_file(stage, expected_identity=identity)
            raise


class BlobMissing(KeyError):
    pass


class EncryptedBlobStore:
    def __init__(self, root: Path | str, cipher: EnvelopeCipher, key: ids.HmacKey):
        self._root = Path(root)
        self._cipher = cipher
        self._key = key

    def _path(self, ref: str) -> Path:
        if not ref.startswith("evd_") or not ref[4:].isalnum():
            raise ValueError(f"not a blob ref: {ref!r}")
        return self._root / f"{ref}.blob"

    def put(self, locator: str, text: str) -> str:
        ref = ids.evidence_id(self._key, locator)
        if self._path(ref).is_file():
            if self.get(ref) != text:
                raise ValueError("immutable evidence locator already contains different content")
            return ref
        envelope, _ = self._cipher.encrypt(text.encode("utf-8"), aad=ref.encode())
        if sys.platform == "win32":
            private_atomic_bytes(self._path(ref), envelope)
            return ref
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self._path(ref).with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(envelope)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path(ref))
        return ref

    def get(self, ref: str) -> str:
        try:
            data = self._path(ref).read_bytes()
        except FileNotFoundError as exc:
            raise BlobMissing(ref) from exc
        return self._cipher.decrypt(data, aad=ref.encode()).decode("utf-8")

    def delete(self, ref: str) -> None:
        self._path(ref).unlink(missing_ok=True)

    def sweep(self, referenced: Iterable[str]) -> list[str]:
        """Delete blobs no ledger row references; return the removed refs."""
        keep = set(referenced)
        removed: list[str] = []
        if not self._root.is_dir():
            return removed
        for path in self._root.glob("evd_*.blob"):
            ref = path.stem
            if ref not in keep:
                path.unlink(missing_ok=True)
                removed.append(ref)
        return sorted(removed)
