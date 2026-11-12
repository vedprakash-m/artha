"""Local Work recovery restrictions, not source permissions or a consent ledger.

Callers inject a trusted Windows machine/user identity resolver and a user/machine
protected native credential backend. Neither is discovered or enrolled implicitly.
The identity key, native head and this module's authority directory must NEVER enter
checkpoint members, escrow or sync. All historical prose still requires file review.

Publication uses cooperative process locks and exact predecessor comparison. Native
credential stores provide no compare-and-swap. An interrupted intent is fail-closed
until explicit roll-forward; it is never evidence that a replacement was rolled back.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from copy import copy
from pathlib import Path
from typing import Any

from . import quarantine_fs_windows
from .backup import atomic_bytes
from .crypto import DecryptionError, EnvelopeCipher, StaticKeyProvider, system_keyring
from .quarantine_fs_windows import QuarantineFilesystemError, read_named_streams, validate_named_stream_name
from .session_lock import default_installation_dir, installation_fence, require_active_installation, session_lock

_MAX_MEMBERS = 10000
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
# Existing assets use the encrypted quarantine bounds; staged edits stay small.
_MAX_INVENTORY_MEMBER_BYTES = 16 * 1024 ** 3
_MAX_INVENTORY_TOTAL_BYTES = 64 * 1024 ** 3
_MAX_STREAM_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_BYTES = 64 * 1024 * 1024
_MAX_GENERATIONS = 10000
_MAX_STREAM_OWNERS = 2 * _MAX_MEMBERS
_MAX_STREAMS_PER_OWNER = 1024
_HEX = re.compile(r'[0-9a-f]{64}')
_ALIAS = re.compile(r'[a-z][a-z0-9_]{0,63}')
_DEVICE = re.compile(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', re.IGNORECASE)


class WorkPrivacyBlocked(RuntimeError):
    """Local identity/currentness cannot be proven; no implicit adoption."""


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _stream_key(owner: str, kind: str, name: str, stream_type: str) -> str:
    return json.dumps([owner, kind, name, stream_type], separators=(',', ':'), ensure_ascii=True)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkPrivacyBlocked('duplicate Work authority fields')
        result[key] = value
    return result


def _decoded(raw: bytes) -> dict[str, Any]:
    try:
        result = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise WorkPrivacyBlocked('malformed Work authority') from None
    if not isinstance(result, dict):
        raise WorkPrivacyBlocked('malformed Work authority')
    return result


def _chain(path: Path) -> None:
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        # Cloud file tags are not name-surrogate links; the native open still
        # verifies actual handles. Junctions and other reparse points refuse.
        cloud = getattr(info, 'st_reparse_tag', 0) & 0xFFFF0000 == 0x90000000
        if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400 and not cloud):
            raise WorkPrivacyBlocked('linked Work protocol path')


def _read(path: Path, bound: int = _MAX_BYTES) -> bytes:
    _chain(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise WorkPrivacyBlocked('required local Work member or authority is missing') from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > bound:
        raise WorkPrivacyBlocked('unsafe or oversized Work protocol member')
    if os.name == 'nt':
        with (quarantine_fs_windows.WindowsQuarantineDirectory.open(path.parent, owner_private=False) as parent,
              parent.open_file(path.name, private=False) as handle):
            raw = handle.read(bound + 1)
            handle.check_path()
    else:
        descriptor = os.open(path, os.O_RDONLY | _posix_flag('O_NOFOLLOW') | _posix_flag('O_NONBLOCK'))
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read(bound + 1)
            after = os.fstat(handle.fileno())
            current = path.lstat()
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
                raise WorkPrivacyBlocked('Work protocol member changed during read')
    if len(raw) != info.st_size or len(raw) > bound:
        raise WorkPrivacyBlocked('Work protocol member changed during read')
    return raw


def _hash_chunks(read: Callable[[int], bytes], size: int) -> dict[str, Any]:
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        raw = read(min(remaining, 65536))
        if not raw or len(raw) > min(remaining, 65536):
            raise WorkPrivacyBlocked('Work member changed during inventory read')
        digest.update(raw)
        remaining -= len(raw)
    return {'size': size, 'sha256': digest.hexdigest()}


def _native_member_digest(opened: Any, bound: int) -> dict[str, Any]:
    before = opened.snapshot()
    if before.size > bound:
        raise WorkPrivacyBlocked('Work inventory exceeds its byte bound')
    opened.seek(0)
    result = _hash_chunks(opened.read_chunk, before.size)
    after = opened.snapshot()
    if after == before:
        return result
    if (after.identity != before.identity or after.size != before.size
            or after.last_write_100ns != before.last_write_100ns):
        raise WorkPrivacyBlocked('Work member changed during inventory read')
    # Discard a ChangeTime-only unstable read; one fresh read must independently
    # stabilize on the same pinned object and reproduce its complete content hash.
    retry_before = opened.snapshot()
    if (retry_before.identity != before.identity or retry_before.size != before.size
            or retry_before.last_write_100ns != before.last_write_100ns):
        raise WorkPrivacyBlocked('Work member changed during inventory read')
    opened.seek(0)
    repeated = _hash_chunks(opened.read_chunk, retry_before.size)
    if opened.snapshot() != retry_before or repeated != result:
        raise WorkPrivacyBlocked('Work member changed during inventory read')
    return repeated


def _member_digest(path: Path, bound: int) -> dict[str, Any]:
    _chain(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > bound:
            raise WorkPrivacyBlocked('unsafe or oversized Work inventory member')
        if os.name == 'nt':
            with (quarantine_fs_windows.WindowsQuarantineDirectory.open(path.parent, owner_private=False) as parent,
                  parent.open_file(path.name, private=False) as opened):
                result = _native_member_digest(opened, bound)
                opened.check_path()
        else:
            descriptor = os.open(path, os.O_RDONLY | _posix_flag('O_NOFOLLOW') | _posix_flag('O_NONBLOCK'))
            with os.fdopen(descriptor, 'rb') as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > bound:
                    raise WorkPrivacyBlocked('unsafe or oversized Work inventory member')
                result = _hash_chunks(handle.read, before.st_size)
                after = os.fstat(handle.fileno())
                current = path.lstat()
                if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or after.st_nlink != 1
                        or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
                    raise WorkPrivacyBlocked('Work member changed during inventory read')
        if result['size'] != info.st_size:
            raise WorkPrivacyBlocked('Work member changed during inventory read')
        return result
    except (OSError, QuarantineFilesystemError) as exc:
        raise WorkPrivacyBlocked('Work inventory member is unavailable, unsafe or changed') from exc


def _mkdir(path: Path, *, private: bool = True) -> None:
    _chain(path)
    if os.name == 'nt':
        if path.exists() and not private:
            with quarantine_fs_windows.WindowsQuarantineDirectory.open(path, owner_private=False):
                pass
        else:
            quarantine_fs_windows.ensure_private_directory(path)
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)


def _atomic(path: Path, raw: bytes) -> None:
    _chain(path)
    _mkdir(path.parent, private=False)
    if path.exists():
        _read(path)
    if os.name != 'nt':
        atomic_bytes(path, raw)
        return

    class _PublicationDirectory(quarantine_fs_windows.WindowsQuarantineDirectory):
        def open_file(self, name: str, *, writable: bool = False, rename: bool = False,
                      private: bool = True, deny_write: bool = False) -> Any:
            # Existing Work files predate private native publication. Pin their
            # identity without treating their inherited ACL as recovery authority.
            return super().open_file(name, writable=writable, rename=rename,
                                     private=private and name != path.name, deny_write=deny_write)

    with _PublicationDirectory.open(path.parent, owner_private=False, writable=True) as parent:
        temporary = '.work-' + uuid.uuid4().hex
        with parent.create_file(temporary) as staged:
            staged.write_all(raw)
            staged.flush()
            identity = staged.identity
        if path.exists():
            with parent.open_file(path.name, private=False) as previous:
                replaced = previous.identity
            parent.replace_file(temporary, path.name, expected_identity=identity, replaced_identity=replaced)
        else:
            parent.publish_write_once(temporary, path.name, expected_identity=identity)


def _native_creation_available() -> bool:
    return os.name == 'nt'


def _require_native_creation() -> None:
    if not _native_creation_available():
        raise WorkPrivacyBlocked('native write-once creation is unavailable on this platform')


def _create_member(path: Path, raw: bytes) -> None:
    """Publish a new member without replacing any concurrent destination."""
    _require_native_creation()
    _chain(path)
    _mkdir(path.parent, private=False)
    try:
        with quarantine_fs_windows.WindowsQuarantineDirectory.open(
            path.parent, owner_private=False, writable=True,
        ) as parent:
            temporary = '.work-' + uuid.uuid4().hex
            identity = None
            published = False
            try:
                with parent.create_file(temporary) as staged:
                    identity = staged.identity
                    staged.write_all(raw)
                    staged.flush()
                parent.publish_write_once(temporary, path.name, expected_identity=identity)
                published = True
            finally:
                if identity is not None and not published:
                    try:
                        parent.delete_file(temporary, expected_identity=identity)
                    except QuarantineFilesystemError as exc:
                        raise WorkPrivacyBlocked(
                            'Work staging cleanup could not be verified; pending publication requires review',
                        ) from exc
    except QuarantineFilesystemError as exc:
        raise WorkPrivacyBlocked(
            'write-once Work creation was refused; pending publication and any staging require review',
        ) from exc


def _mutate_member(path: Path, raw: bytes | None, before: Mapping[str, Any] | None,
                   after: Mapping[str, Any] | None) -> None:
    """Change primary bytes on the pinned object; deletion removes the whole object."""
    if os.name != 'nt':
        raise WorkPrivacyBlocked('native Work member mutation is unavailable')
    _chain(path)
    try:
        try:
            path.lstat()
        except FileNotFoundError:
            if raw is None:
                return
            raise WorkPrivacyBlocked('pending Work member is missing; review required') from None
        with quarantine_fs_windows.WindowsQuarantineDirectory.open(
                path.parent, owner_private=False, writable=True) as parent:
            with (
                parent.open_file(path.name, writable=raw is not None, rename=raw is None,
                                 private=False, deny_write=True) as opened,
                opened.lock() if raw is not None else nullcontext(),
            ):
                actual = _native_member_digest(opened, _MAX_INVENTORY_MEMBER_BYTES)
                if raw is not None and actual == after:
                    return
                if actual != before:
                    raise WorkPrivacyBlocked('pending Work member drift requires review')
                if raw is None:
                    opened.delete()
                    parent._flush_directory()
                else:
                    opened.seek(0)
                    opened.write_all(raw)
                    opened.truncate(len(raw))
                    opened.flush()
                    if opened.read(_MAX_MEMBER_BYTES) != raw:
                        raise WorkPrivacyBlocked('Work member update readback differs from intent')
            parent._revalidate()
    except (OSError, QuarantineFilesystemError) as exc:
        raise WorkPrivacyBlocked('native Work member mutation refused; pending intent requires review') from exc


def _delete(path: Path) -> None:
    _chain(path)
    if os.name == 'nt':
        with quarantine_fs_windows.WindowsQuarantineDirectory.open(
            path.parent, owner_private=False, writable=True,
        ) as parent:
            with parent.open_file(path.name, private=False) as handle:
                identity = handle.identity
            parent.delete_file(path.name, expected_identity=identity)
    else:
        descriptor = os.open(path.parent, os.O_RDONLY | _posix_flag('O_DIRECTORY') | _posix_flag('O_NOFOLLOW'))
        try:
            info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkPrivacyBlocked('unsafe Work member deletion')
            os.unlink(path.name, dir_fd=descriptor)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _posix_flag(name: str) -> int:
    value = getattr(os, name, None)
    if not isinstance(value, int):
        raise WorkPrivacyBlocked('secure Work filesystem operation is unavailable')
    return value


class _Transaction:
    def __init__(self, protocol: WorkPrivacy, pinned: dict[str, Any]):
        self.protocol = protocol
        self.pinned = pinned
        self.active = True
        self.published = False

    def check_current(self) -> dict[str, Any]:
        """Recheck pinned capture authority without reacquiring the transaction's locks."""
        if not self.active or self.published:
            raise WorkPrivacyBlocked('Work transaction is closed')
        current = self.protocol._load_current()
        if current != self.pinned:
            raise WorkPrivacyBlocked('Work transaction authority changed')
        return current

    def publish(self, changes: Mapping[str, bytes | None], *, revoke: Sequence[str] = ()) -> dict[str, Any]:
        if not self.active or self.published:
            raise WorkPrivacyBlocked('Work transaction is closed')
        self.published = True
        return self.protocol._publish(self.pinned, changes, revoke)


class WorkPrivacy:
    KEY_SERVICE = 'artha-work-local-identity'
    HEAD_SERVICE = 'artha-work-local-privacy'
    EXCLUDED_SERVICES = frozenset({KEY_SERVICE, HEAD_SERVICE})
    EPHEMERAL_MEMBER = 'work_local/cache/cache.lock'

    def __init__(self, repo: Path, local: Path, roots: Mapping[str, Path], *,
                 backend: Any, native_identity: Callable[[], Mapping[str, str]],
                 observe: Callable[[str], None] | None = None):
        self.repo = repo.absolute()
        self.local = local.absolute()
        self.roots = {name: path.absolute() for name, path in roots.items()}
        self.backend = backend
        self.native_identity = native_identity
        self.observe = observe or (lambda _: None)
        self.authority_dir = self.local / 'work-privacy'
        if not self.roots or any(not _ALIAS.fullmatch(name) for name in self.roots):
            raise WorkPrivacyBlocked('explicit Work member roots are required')
        paths = list(self.roots.values())
        if any(a.resolve().is_relative_to(b.resolve()) for i, a in enumerate(paths)
               for j, b in enumerate(paths) if i != j):
            raise WorkPrivacyBlocked('Work member roots overlap')
        if any(self.authority_dir.resolve().is_relative_to(path.resolve())
               or path.resolve().is_relative_to(self.authority_dir.resolve()) for path in paths):
            raise WorkPrivacyBlocked('local Work authority must be outside checkpoint member roots')

    def is_enrolled(self) -> bool:
        """Filesystem-only probe. Partial/corrupt enrollment must never enable legacy fallback."""
        _chain(self.authority_dir)
        return self.authority_dir.exists()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        require_active_installation(self.repo, self.local, *self.roots.values())
        with ExitStack() as stack:
            for path in sorted({self.local.resolve(), default_installation_dir().resolve()}):
                stack.enter_context(installation_fence(path))
            stack.enter_context(session_lock(default_installation_dir() / 'work-privacy.lock'))
            yield

    def _configuration(self) -> dict[str, Any]:
        native = dict(self.native_identity())
        if (set(native) != {'platform', 'machine', 'user'} or native['platform'] != 'win32'
                or any(not isinstance(value, str) or not value.strip() or len(value) > 1024
                       for value in native.values())):
            raise WorkPrivacyBlocked('trusted Windows machine and user identity required')
        for path in (self.repo, self.local, *self.roots.values(), self.authority_dir):
            _chain(path)
        def canonical(path: Path) -> str:
            return os.path.normcase(str(path.resolve()))

        return {'native': native, 'repo': canonical(self.repo), 'local': canonical(self.local),
                'roots': {name: canonical(path) for name, path in self.roots.items()}}

    def _key(self) -> bytes:
        raw = self.backend.get_password(self.KEY_SERVICE, 'key')
        if not isinstance(raw, str):
            raise WorkPrivacyBlocked('installed local Work identity key required')
        try:
            key = base64.b64decode(raw, validate=True)
        except ValueError:
            raise WorkPrivacyBlocked('invalid local Work identity key') from None
        if len(key) != 32:
            raise WorkPrivacyBlocked('invalid local Work identity key')
        return key

    def _cipher(self) -> EnvelopeCipher:
        key = hmac.digest(self._key(), b'artha.work-local.encryption.v1', 'sha256')
        return EnvelopeCipher(StaticKeyProvider({1: key}))

    def _encrypt(self, value: Mapping[str, Any], purpose: str) -> bytes:
        raw = _encoded(value)
        if len(raw) > _MAX_BYTES:
            raise WorkPrivacyBlocked('Work authority exceeds its supported bound')
        return self._cipher().encrypt(raw, aad=('artha.work-local:' + purpose).encode())[0]

    def _decrypt(self, raw: bytes, purpose: str) -> dict[str, Any]:
        try:
            plain = self._cipher().decrypt(raw, aad=('artha.work-local:' + purpose).encode())
        except DecryptionError:
            raise WorkPrivacyBlocked('local Work identity/key or ciphertext mismatch') from None
        if len(plain) > _MAX_BYTES:
            raise WorkPrivacyBlocked('Work authority exceeds its supported bound')
        return _decoded(plain)

    def _sign(self, body: Mapping[str, Any], purpose: str) -> dict[str, Any]:
        return {**body, 'mac': hmac.digest(self._key(), purpose.encode() + b'\0' + _encoded(body),
                                         'sha256').hex()}

    def _verify(self, signed: Mapping[str, Any], purpose: str) -> dict[str, Any]:
        body = {key: value for key, value in signed.items() if key != 'mac'}
        mac = signed.get('mac')
        if not isinstance(mac, str) or not hmac.compare_digest(self._sign(body, purpose)['mac'], mac):
            raise WorkPrivacyBlocked('Work authority authentication failed')
        return body

    def _identity(self) -> str:
        if not self.is_enrolled():
            raise WorkPrivacyBlocked('explicit reviewed Work enrollment is required')
        identity = self._decrypt(_read(self.authority_dir / 'identity'), 'identity')
        if (set(identity) != {'schema', 'installation_id', 'configuration'}
                or identity['schema'] != 'artha.work-installation.v1'
                or not isinstance(identity['installation_id'], str) or not identity['installation_id']
                or identity['configuration'] != self._configuration()):
            raise WorkPrivacyBlocked('original Work host/profile/installation identity mismatch')
        return _digest(identity)

    def _member(self, name: str) -> Path:
        if not isinstance(name, str) or len(name) > 1024:
            raise WorkPrivacyBlocked('invalid Work member name')
        parts = name.split('/')
        if len(parts) < 2 or parts[0] not in self.roots:
            raise WorkPrivacyBlocked('unknown Work member root')
        if any(not part or part in {'.', '..'} or part.endswith((' ', '.'))
               or _DEVICE.fullmatch(part) or any(char in '<>:"\\|?*' or ord(char) < 32 for char in part)
               for part in parts[1:]):
            raise WorkPrivacyBlocked('unsafe Work member name')
        path = self.roots[parts[0]].joinpath(*parts[1:])
        _chain(path)
        return path

    def _inventory(self) -> dict[str, Any]:
        members: dict[str, Any] = {}
        folded: set[str] = set()
        total = 0
        for alias, root in sorted(self.roots.items()):
            _chain(root)
            if not root.is_dir():
                raise WorkPrivacyBlocked('Work member root is missing')
            def unavailable(error: OSError) -> None:
                raise error

            for directory, directories, files in os.walk(root, followlinks=False, onerror=unavailable):
                for name in directories:
                    _chain(Path(directory) / name)
                for name in files:
                    path = Path(directory) / name
                    member = alias + '/' + path.relative_to(root).as_posix()
                    self._member(member)
                    if member == self.EPHEMERAL_MEMBER:
                        info = path.lstat()
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise WorkPrivacyBlocked('unsafe ephemeral Work cache lock')
                        continue
                    if member.casefold() in folded or len(members) >= _MAX_MEMBERS:
                        raise WorkPrivacyBlocked('ambiguous or oversized Work inventory')
                    value = _member_digest(path, min(_MAX_INVENTORY_MEMBER_BYTES, _MAX_INVENTORY_TOTAL_BYTES - total))
                    total += value['size']
                    folded.add(member.casefold())
                    members[member] = value
        return members

    def _stream_parts(self, key: str) -> tuple[str, str, str, str]:
        try:
            parts = json.loads(key)
            if (not isinstance(parts, list) or len(parts) != 4
                    or any(not isinstance(part, str) for part in parts)):
                raise ValueError
            owner, kind, name, stream_type = parts
            if (_stream_key(owner, kind, name, stream_type) != key
                    or kind not in {'file', 'directory'} or stream_type != '$DATA'):
                raise ValueError
            if owner in self.roots:
                if kind != 'directory':
                    raise ValueError
            else:
                self._member(owner)
                if owner == self.EPHEMERAL_MEMBER:
                    raise ValueError
            validate_named_stream_name(name)
            return owner, kind, name, stream_type
        except (ValueError, TypeError, RecursionError, UnicodeError, QuarantineFilesystemError):
            raise WorkPrivacyBlocked('invalid Work stream identity') from None

    def _validate_stream_inventory(self, streams: Any, members: Mapping[str, Any]) -> None:
        if not isinstance(streams, dict) or len(streams) > _MAX_MEMBERS:
            raise WorkPrivacyBlocked('invalid Work stream inventory')
        seen: set[tuple[str, str, str]] = set()
        owners: dict[str, tuple[str, str]] = {}
        counts: dict[str, int] = {}
        total = 0
        for key, value in streams.items():
            owner, kind, name, _ = self._stream_parts(key)
            folded_owner = unicodedata.normalize('NFC', owner).casefold()
            folded_name = unicodedata.normalize('NFC', name).casefold()
            identity = (folded_owner, kind, folded_name)
            if (identity in seen or owners.get(folded_owner, (owner, kind)) != (owner, kind)
                    or (kind == 'file' and owner not in members)
                    or not isinstance(value, dict) or set(value) != {'sha256', 'size'}
                    or not isinstance(value['sha256'], str) or not _HEX.fullmatch(value['sha256'])
                    or type(value['size']) is not int or not 0 <= value['size'] <= _MAX_MEMBER_BYTES):
                raise WorkPrivacyBlocked('invalid or ambiguous Work stream inventory')
            seen.add(identity)
            owners[folded_owner] = (owner, kind)
            counts[folded_owner] = counts.get(folded_owner, 0) + 1
            if counts[folded_owner] > _MAX_STREAMS_PER_OWNER:
                raise WorkPrivacyBlocked('Work stream owner exceeds its stream count bound')
            total += value['size']
            if total > _MAX_STREAM_TOTAL_BYTES:
                raise WorkPrivacyBlocked('Work stream inventory exceeds its byte bound')

    def _inventory_streams(self) -> dict[str, Any]:
        streams: dict[str, Any] = {}
        owners: set[str] = set()
        total = 0

        def capture(owner: str, path: Path, kind: str) -> None:
            nonlocal total
            identity = unicodedata.normalize('NFC', owner).casefold()
            if identity in owners or len(owners) >= _MAX_STREAM_OWNERS:
                raise WorkPrivacyBlocked('ambiguous or oversized Work stream owners')
            owners.add(identity)
            for stream in read_named_streams(
                path, directory=kind == 'directory', max_total_bytes=_MAX_STREAM_TOTAL_BYTES - total,
                max_streams=min(_MAX_STREAMS_PER_OWNER, _MAX_MEMBERS - len(streams)),
            ):
                key = _stream_key(owner, kind, stream.name, stream.stream_type)
                if key in streams or len(streams) >= _MAX_MEMBERS:
                    raise WorkPrivacyBlocked('ambiguous or oversized Work stream inventory')
                total += len(stream.data)
                if total > _MAX_STREAM_TOTAL_BYTES:
                    raise WorkPrivacyBlocked('Work stream inventory exceeds its byte bound')
                streams[key] = {'sha256': hashlib.sha256(stream.data).hexdigest(), 'size': len(stream.data)}

        try:
            for alias, root in sorted(self.roots.items()):
                _chain(root)
                if not root.is_dir():
                    raise WorkPrivacyBlocked('Work member root is missing')

                def unavailable(error: OSError) -> None:
                    raise error

                for directory, directories, files in os.walk(root, followlinks=False, onerror=unavailable):
                    folder = Path(directory)
                    relative = folder.relative_to(root)
                    owner = alias if folder == root else alias + '/' + relative.as_posix()
                    if folder != root:
                        self._member(owner)
                    capture(owner, folder, 'directory')
                    for name in sorted(directories):
                        _chain(folder / name)
                    for name in sorted(files):
                        path = folder / name
                        member = alias + '/' + path.relative_to(root).as_posix()
                        self._member(member)
                        if member != self.EPHEMERAL_MEMBER:
                            capture(member, path, 'file')
        except (OSError, QuarantineFilesystemError) as exc:
            raise WorkPrivacyBlocked('Work stream inventory is unavailable, incomplete or changed') from exc
        self._validate_stream_inventory(streams, self._inventory())
        return streams

    def _history(self, head: Mapping[str, Any]) -> dict[str, Any]:
        body = self._verify(head, 'head')
        if (set(body) != {'schema', 'identity', 'generation', 'capsule_sha256', 'previous'}
                or body['schema'] != 'artha.work-privacy-head.v1'
                or body['identity'] != self._identity()
                or not isinstance(body['capsule_sha256'], str) or not _HEX.fullmatch(body['capsule_sha256'])):
            raise WorkPrivacyBlocked('invalid current Work head')
        raw = _read(self.authority_dir / (body['capsule_sha256'] + '.ae1'))
        if hashlib.sha256(raw).hexdigest() != body['capsule_sha256']:
            raise WorkPrivacyBlocked('Work capsule differs from current trusted head')
        history = self._decrypt(raw, 'history')
        self._validate_history(history)
        if (history['generation'], history['identity']) != (body['generation'], body['identity']):
            raise WorkPrivacyBlocked('Work history differs from current trusted head')
        return history

    def _validate_history(self, history: dict[str, Any]) -> None:  # noqa: PLR0912 - explicit history validation
        schema = history.get('schema')
        fields = {'schema', 'profile', 'identity', 'generation', 'members', 'invalidated', 'revoked', 'events'}
        if schema == 'artha.work-member-history.v2':
            fields |= {'streams', 'stream_invalidated', 'stream_since'}
        if (set(history) != fields
                or schema not in {'artha.work-member-history.v1', 'artha.work-member-history.v2'}
                or history['profile'] != 'work'
                or type(history['generation']) is not int or not 1 <= history['generation'] <= _MAX_GENERATIONS
                or not isinstance(history['members'], dict) or len(history['members']) > _MAX_MEMBERS
                or not isinstance(history['invalidated'], dict) or len(history['invalidated']) > _MAX_MEMBERS
                or not isinstance(history['revoked'], list) or len(history['revoked']) > _MAX_MEMBERS
                or len(set(history['revoked'])) != len(history['revoked'])
                or not isinstance(history['events'], list) or len(history['events']) != history['generation']):
            raise WorkPrivacyBlocked('invalid Work member history')
        total = 0
        for name, value in history['members'].items():
            self._member(name)
            if (not isinstance(value, dict) or set(value) != {'sha256', 'size'}
                    or not isinstance(value['sha256'], str) or not _HEX.fullmatch(value['sha256'])
                    or type(value['size']) is not int or not 0 <= value['size'] <= _MAX_INVENTORY_MEMBER_BYTES):
                raise WorkPrivacyBlocked('invalid Work member digest')
            total += value['size']
            if total > _MAX_INVENTORY_TOTAL_BYTES:
                raise WorkPrivacyBlocked('Work inventory exceeds its byte bound')
        for name, generation in history['invalidated'].items():
            self._member(name)
            if type(generation) is not int or not 1 <= generation <= history['generation']:
                raise WorkPrivacyBlocked('invalid Work invalidation')
        for name in history['revoked']:
            self._member(name)
        if schema == 'artha.work-member-history.v2':
            self._validate_stream_inventory(history['streams'], history['members'])
            if (type(history['stream_since']) is not int
                    or not 2 <= history['stream_since'] <= history['generation']
                    or not isinstance(history['stream_invalidated'], dict)
                    or len(history['stream_invalidated']) > _MAX_MEMBERS):
                raise WorkPrivacyBlocked('invalid Work stream history')
            for key, generation in history['stream_invalidated'].items():
                self._stream_parts(key)
                if type(generation) is not int or not history['stream_since'] <= generation <= history['generation']:
                    raise WorkPrivacyBlocked('invalid Work stream invalidation')
        for generation, event in enumerate(history['events'], 1):
            event_fields = {'generation', 'members_sha256', 'previous_head_sha256'}
            stream_event = schema == 'artha.work-member-history.v2' and generation >= history['stream_since']
            if stream_event:
                event_fields.add('streams_sha256')
            if (not isinstance(event, dict) or set(event) != event_fields
                    or event['generation'] != generation
                    or not isinstance(event['members_sha256'], str) or not _HEX.fullmatch(event['members_sha256'])
                    or (generation == 1 and event['previous_head_sha256'] is not None)
                    or (generation > 1 and (not isinstance(event['previous_head_sha256'], str)
                                           or not _HEX.fullmatch(event['previous_head_sha256'])))
                    or (stream_event and (not isinstance(event['streams_sha256'], str)
                                          or not _HEX.fullmatch(event['streams_sha256'])))):
                raise WorkPrivacyBlocked('invalid Work history continuity')
        if history['events'][-1]['members_sha256'] != _digest(history['members']):
            raise WorkPrivacyBlocked('Work member inventory differs from history')
        if (schema == 'artha.work-member-history.v2'
                and history['events'][-1]['streams_sha256'] != _digest(history['streams'])):
            raise WorkPrivacyBlocked('Work stream inventory differs from history')

    def _native_head(self) -> dict[str, Any]:
        raw = self.backend.get_password(self.HEAD_SERVICE, 'head')
        if not isinstance(raw, str) or len(raw) > 65536:
            raise WorkPrivacyBlocked('current native Work head is unavailable')
        return _decoded(raw.encode())

    def _expect_head(self, expected: Mapping[str, Any]) -> None:
        if self._native_head() != expected:
            raise WorkPrivacyBlocked('current Work head changed; publication cannot succeed')
        self._identity()

    def _load_current(self, *, drift: bool = True) -> dict[str, Any]:
        self._identity()
        if (self.authority_dir / 'pending').exists():
            raise WorkPrivacyBlocked('pending Work publication requires explicit recovery')
        head = self._native_head()
        history = self._history(head)
        complete = self._decrypt(_read(self.authority_dir / 'complete'), 'complete')
        if complete != {'head': head}:
            raise WorkPrivacyBlocked('Work head differs from acknowledged history; rollback or interrupted publication')
        if drift and self._inventory() != history['members']:
            raise WorkPrivacyBlocked('unjournaled Work member drift requires reviewed reconciliation')
        if (drift and history['schema'] == 'artha.work-member-history.v2'
                and (self._inventory_streams() != history['streams'] or self._inventory() != history['members'])):
            raise WorkPrivacyBlocked('unjournaled Work stream drift requires reviewed reconciliation')
        self._expect_head(head)
        return {**history, 'head': head}

    def current(self) -> dict[str, Any]:
        with self._locked():
            return self._load_current()

    def enroll(self, identity_key: bytes, *, reviewed: bool, installation_id: str | None = None) -> dict[str, Any]:
        if reviewed is not True:
            raise WorkPrivacyBlocked('explicit inventory review required before Work enrollment')
        if not isinstance(identity_key, bytes) or len(identity_key) != 32:
            raise WorkPrivacyBlocked('explicit local identity key must contain 32 bytes')
        with self._locked():
            if (self.is_enrolled() or self.backend.get_password(self.KEY_SERVICE, 'key') is not None
                    or self.backend.get_password(self.HEAD_SERVICE, 'head') is not None):
                raise WorkPrivacyBlocked('Work identity is already enrolled or has incomplete enrollment')
            identity = {'schema': 'artha.work-installation.v1',
                        'installation_id': installation_id or uuid.uuid4().hex,
                        'configuration': self._configuration()}
            if not isinstance(identity['installation_id'], str) or not 1 <= len(identity['installation_id']) <= 128:
                raise WorkPrivacyBlocked('invalid explicit installation identity')
            members = self._inventory()
            _mkdir(self.authority_dir)
            text = base64.b64encode(identity_key).decode()
            self.backend.set_password(self.KEY_SERVICE, 'key', text)
            if self._key() != identity_key:
                raise WorkPrivacyBlocked('local identity key publication could not be verified')
            _atomic(self.authority_dir / 'identity', self._encrypt(identity, 'identity'))
            history = {'schema': 'artha.work-member-history.v1', 'profile': 'work',
                       'identity': _digest(identity), 'generation': 1, 'members': members,
                       'invalidated': {}, 'revoked': [], 'events': [
                           {'generation': 1, 'members_sha256': _digest(members), 'previous_head_sha256': None}]}
            head, raw = self._capsule(history, None)
            _atomic(self.authority_dir / (head['capsule_sha256'] + '.ae1'), raw)
            if self._inventory() != members or self._identity() != history['identity']:
                raise WorkPrivacyBlocked('Work identity/inventory changed during enrollment')
            self.backend.set_password(self.HEAD_SERVICE, 'head', _encoded(head).decode())
            self._expect_head(head)
            _atomic(self.authority_dir / 'complete', self._encrypt({'head': head}, 'complete'))
            return self._load_current()

    def enroll_generated(self, *, reviewed: bool, installation_id: str | None = None) -> dict[str, Any]:
        """Explicit native enrollment; callers must separately obtain live-operation authorization."""
        if reviewed is not True:
            raise WorkPrivacyBlocked('explicit inventory review required before Work enrollment')
        return self.enroll(secrets.token_bytes(32), reviewed=True, installation_id=installation_id)

    def _capsule(self, history: dict[str, Any], previous: Mapping[str, Any] | None) -> tuple[dict[str, Any], bytes]:
        self._validate_history(history)
        raw = self._encrypt(history, 'history')
        head = self._sign({'schema': 'artha.work-privacy-head.v1', 'identity': history['identity'],
                           'generation': history['generation'], 'capsule_sha256': hashlib.sha256(raw).hexdigest(),
                           'previous': _digest(previous) if previous is not None else None}, 'head')
        return head, raw

    @contextmanager
    def transaction(self) -> Iterator[_Transaction]:
        with self._locked():
            transaction = _Transaction(self, self._load_current())
            try:
                yield transaction
            finally:
                transaction.active = False

    def publish(self, changes: Mapping[str, bytes | None], *, revoke: Sequence[str] = ()) -> dict[str, Any]:
        with self.transaction() as transaction:
            return transaction.publish(changes, revoke=revoke)

    @staticmethod
    def _continuity(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
        if (after['generation'] != before['generation'] + 1 or before['identity'] != after['identity']
                or after['events'][:-1] != before['events']
                or not set(before['revoked']) <= set(after['revoked'])
                or any(after['invalidated'].get(name, 0) < version for name, version in before['invalidated'].items())):
            raise WorkPrivacyBlocked('Work history cannot roll back invalidations or identity')
        if before['schema'] == 'artha.work-member-history.v2':
            if (after['schema'] != before['schema'] or after['stream_since'] != before['stream_since']):
                raise WorkPrivacyBlocked('Work stream history cannot be downgraded')
            changed = {key for key in set(before['streams']) | set(after['streams'])
                       if before['streams'].get(key) != after['streams'].get(key)}
            expected = dict(before['stream_invalidated'])
            expected.update(dict.fromkeys(changed, after['generation']))
            if after['stream_invalidated'] != expected:
                raise WorkPrivacyBlocked('Work stream history cannot roll back invalidations')
        elif after['schema'] == 'artha.work-member-history.v2':
            if (after['stream_since'] != after['generation']
                    or after['stream_invalidated'] != dict.fromkeys(after['streams'], after['generation'])):
                raise WorkPrivacyBlocked('invalid initial Work stream history')
        elif after['schema'] != before['schema']:
            raise WorkPrivacyBlocked('invalid Work history transition')

    def _prepare(self, before: dict[str, Any], members: dict[str, Any], changes: Mapping[str, bytes | None],
                 revoke: Sequence[str], *, streams: dict[str, Any] | None = None,
                 upgrade_streams: bool = False) -> dict[str, Any]:
        generation = before['generation'] + 1
        invalidated = dict(before['invalidated'])
        changed = {name for name in set(before['members']) | set(members)
                   if before['members'].get(name) != members.get(name)}
        invalidated.update(dict.fromkeys(changed, generation))
        after = {key: value for key, value in before.items() if key != 'head'}
        event = {'generation': generation, 'members_sha256': _digest(members),
                 'previous_head_sha256': _digest(before['head'])}
        if upgrade_streams:
            if before['schema'] != 'artha.work-member-history.v1' or streams is None:
                raise WorkPrivacyBlocked('explicit Work stream migration is unavailable')
            after.update(schema='artha.work-member-history.v2', streams=streams,
                         stream_since=generation, stream_invalidated=dict.fromkeys(streams, generation))
        elif before['schema'] == 'artha.work-member-history.v2':
            next_streams = before['streams'] if streams is None else streams
            if streams is None:
                removed = {(name, 'file') for name, value in changes.items() if value is None}
                next_streams = {key: value for key, value in next_streams.items()
                                if self._stream_parts(key)[:2] not in removed}
            stream_invalidated = dict(before['stream_invalidated'])
            stream_changed = {key for key in set(before['streams']) | set(next_streams)
                              if before['streams'].get(key) != next_streams.get(key)}
            stream_invalidated.update(dict.fromkeys(stream_changed, generation))
            after.update(streams=next_streams, stream_invalidated=stream_invalidated)
        elif streams is not None:
            raise WorkPrivacyBlocked('Work stream history requires an explicit migration')
        if after['schema'] == 'artha.work-member-history.v2':
            event['streams_sha256'] = _digest(after['streams'])
        after.update(generation=generation, members=members, invalidated=invalidated,
                     revoked=sorted(set(before['revoked']) | set(revoke)),
                     events=[*before['events'], event])
        self._validate_history(after)
        self._continuity(before, after)
        head, capsule = self._capsule(after, before['head'])
        return {'schema': 'artha.work-pending.v1', 'before': before, 'after': after, 'head': head,
                'capsule': base64.b64encode(capsule).decode(),
                'changes': {name: None if value is None else base64.b64encode(value).decode()
                            for name, value in changes.items()}}

    @staticmethod
    def _hold_member_mutations(before: Mapping[str, Any], changes: Mapping[str, Any]) -> None:
        existing = {name.casefold() for name in before['members']}
        if any(value is None or name.casefold() in existing for name, value in changes.items()):
            if os.name == 'nt':
                return
            raise WorkPrivacyBlocked('Work member replacement and deletion are temporarily paused')

    @staticmethod
    def _pending_value(value: Any, expected: Mapping[str, Any] | None) -> bytes | None:
        if value is None:
            if expected is not None:
                raise WorkPrivacyBlocked('deleted Work member remains in intent')
            return None
        if not isinstance(value, str):
            raise WorkPrivacyBlocked('invalid pending Work member bytes')
        if len(value) > 4 * ((_MAX_MEMBER_BYTES + 2) // 3):
            raise WorkPrivacyBlocked('oversized staged Work member in pending intent')
        try:
            raw = base64.b64decode(value, validate=True)
        except ValueError:
            raise WorkPrivacyBlocked('invalid pending Work member bytes') from None
        if (len(raw) > _MAX_MEMBER_BYTES
                or {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)} != expected):
            raise WorkPrivacyBlocked('staged Work bytes differ from intent')
        return raw

    def _pending_members(self, before: Mapping[str, Any], after: Mapping[str, Any],
                         changes: Any) -> list[tuple[str, Path, bytes | None]]:
        if not isinstance(changes, dict) or any(not isinstance(name, str) for name in changes):
            raise WorkPrivacyBlocked('invalid pending Work member changes')
        self._hold_member_mutations(before, changes)
        if changes:
            _require_native_creation()
        members: list[tuple[str, Path, bytes | None]] = []
        staged_bytes = 0
        for name, value in changes.items():
            path = self._member(name)
            raw = self._pending_value(value, after['members'].get(name))
            staged_bytes += len(raw) if raw is not None else 0
            if staged_bytes > _MAX_TOTAL_BYTES:
                raise WorkPrivacyBlocked('staged Work pending intent exceeds its byte bound')
            if name in before['members'] or raw is None:
                members.append((name, path, raw))
                continue
            try:
                path.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise WorkPrivacyBlocked('pending Work destination cannot be checked; review required') from exc
            else:
                raise WorkPrivacyBlocked('pending Work destination already exists; review required')
            members.append((name, path, raw))
        return members

    def _publish(self, before: dict[str, Any], changes: Mapping[str, bytes | None],
                 revoke: Sequence[str]) -> dict[str, Any]:
        if self._load_current() != before:
            raise WorkPrivacyBlocked('Work transaction changed before publication')
        members = dict(before['members'])
        folded = {name.casefold(): name for name in members}
        staged_bytes = 0
        for name, raw in changes.items():
            self._member(name)
            if name == self.EPHEMERAL_MEMBER:
                raise WorkPrivacyBlocked('ephemeral Work cache lock cannot become a journal member')
            if name.casefold() in folded and folded[name.casefold()] != name:
                raise WorkPrivacyBlocked('ambiguous Work member name')
            folded[name.casefold()] = name
            if raw is None:
                members.pop(name, None)
            elif isinstance(raw, bytes) and len(raw) <= _MAX_MEMBER_BYTES:
                staged_bytes += len(raw)
                members[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
            else:
                raise WorkPrivacyBlocked('invalid or oversized staged Work member')
        for name in revoke:
            self._member(name)
        if (staged_bytes > _MAX_TOTAL_BYTES or len(members) > _MAX_MEMBERS
                or sum(value['size'] for value in members.values()) > _MAX_INVENTORY_TOTAL_BYTES):
            raise WorkPrivacyBlocked('Work inventory or staged publication exceeds its supported bound')
        if members == before['members'] and set(revoke) <= set(before['revoked']):
            current = self._load_current()
            if current != before:
                raise WorkPrivacyBlocked('Work transaction changed before no-op acknowledgement')
            return current
        self._hold_member_mutations(before, changes)
        if changes:
            _require_native_creation()
        pending = self._prepare(before, members, changes, revoke)
        _atomic(self.authority_dir / 'pending', self._encrypt(pending, 'pending'))
        self.observe('intent')
        return self._finish(pending)

    def _finish(self, pending: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912 - ordered publication checks
        before, after, head = pending['before'], pending['after'], pending['head']
        self._continuity(before, after)
        self._validate_history(after)
        if self._history(before['head']) != {key: value for key, value in before.items() if key != 'head'}:
            raise WorkPrivacyBlocked('pending Work predecessor differs from authenticated history')
        capsule = base64.b64decode(pending['capsule'], validate=True)
        if (head != self._sign(self._verify(head, 'head'), 'head')
                or hashlib.sha256(capsule).hexdigest() != head.get('capsule_sha256')
                or self._decrypt(capsule, 'history') != after
                or head.get('previous') != _digest(before['head'])
                or after['events'][-1]['previous_head_sha256'] != _digest(before['head'])):
            raise WorkPrivacyBlocked('pending Work authority differs from authenticated history')
        native = self._native_head()
        if native not in (before['head'], head):
            raise WorkPrivacyBlocked('current Work head changed during pending publication')
        complete = self._decrypt(_read(self.authority_dir / 'complete'), 'complete')
        if complete not in ({'head': before['head']}, {'head': head}):
            raise WorkPrivacyBlocked('pending Work completion identity changed')
        members = self._pending_members(before, after, pending.get('changes'))
        current = self._inventory()
        for name in set(current) | set(before['members']) | set(after['members']):
            if current.get(name) not in (before['members'].get(name), after['members'].get(name)):
                raise WorkPrivacyBlocked('unjournaled Work drift during pending publication')
        if after['schema'] == 'artha.work-member-history.v2':
            streams = self._inventory_streams()
            previous = before.get('streams', after['streams'])
            if any(streams.get(key) not in (previous.get(key), after['streams'].get(key))
                   for key in set(streams) | set(previous) | set(after['streams'])):
                raise WorkPrivacyBlocked('unjournaled Work stream drift during pending publication')
        _atomic(self.authority_dir / (head['capsule_sha256'] + '.ae1'), capsule)
        self.observe('capsule')
        for name, path, raw in members:
            self._expect_head(native)
            if name in before['members'] or raw is None:
                _mutate_member(path, raw, before['members'].get(name), after['members'].get(name))
            else:
                _create_member(path, raw)
            self.observe('member')
        if self._inventory() != after['members']:
            raise WorkPrivacyBlocked('Work inventory drift at publication')
        if (after['schema'] == 'artha.work-member-history.v2'
                and self._inventory_streams() != after['streams']):
            raise WorkPrivacyBlocked('Work stream drift at publication')
        self._expect_head(native)
        if native == before['head']:
            self.backend.set_password(self.HEAD_SERVICE, 'head', _encoded(head).decode())
        self.observe('head')
        self._expect_head(head)
        self.observe('readback')
        self._expect_head(head)
        _atomic(self.authority_dir / 'complete', self._encrypt({'head': head}, 'complete'))
        self.observe('complete')
        self._expect_head(head)
        if self._inventory() != after['members']:
            raise WorkPrivacyBlocked('Work inventory drift after publication')
        if (after['schema'] == 'artha.work-member-history.v2'
                and self._inventory_streams() != after['streams']):
            raise WorkPrivacyBlocked('Work stream drift after publication')
        _delete(self.authority_dir / 'pending')
        return self._load_current()

    def recover_pending(self) -> dict[str, Any]:
        """Explicit deterministic roll-forward only; never adopt unrelated live edits."""
        with self._locked():
            self._identity()
            pending = self._decrypt(_read(self.authority_dir / 'pending'), 'pending')
            if pending.get('schema') != 'artha.work-pending.v1':
                raise WorkPrivacyBlocked('invalid Work pending intent')
            return self._finish(pending)

    def reconcile(self, *, reviewed: bool) -> dict[str, Any]:
        if reviewed is not True:
            raise WorkPrivacyBlocked('explicit review required for Work drift reconciliation')
        with self._locked():
            before = self._load_current(drift=False)
            members = self._inventory()
            streams = self._inventory_streams() if before['schema'] == 'artha.work-member-history.v2' else None
            pending = self._prepare(before, members, {}, (), streams=streams)
            _atomic(self.authority_dir / 'pending', self._encrypt(pending, 'pending'))
            self.observe('intent')
            return self._finish(pending)

    @staticmethod
    def _stream_review(before: Mapping[str, Any], streams: Mapping[str, Any]) -> dict[str, Any]:
        inventory = {'schema': 'artha.work-stream-review.v1', 'identity': before['identity'],
                     'generation': before['generation'], 'head_sha256': _digest(before['head']),
                     'members': before['members'], 'streams': streams}
        return {'inventory_sha256': _digest(inventory), 'stream_count': len(streams),
                'stream_bytes': sum(value['size'] for value in streams.values())}

    def preview_stream_history(self) -> dict[str, Any]:
        """Review aggregate stream inventory without publishing a migration."""
        with self._locked():
            before = self._load_current()
            if before['schema'] != 'artha.work-member-history.v1':
                raise WorkPrivacyBlocked('Work stream history is already enrolled')
            streams = self._inventory_streams()
            self._validate_stream_inventory(streams, before['members'])
            if self._inventory() != before['members'] or self._inventory_streams() != streams:
                raise WorkPrivacyBlocked('Work stream inventory changed during review')
            self._expect_head(before['head'])
            return self._stream_review(before, streams)

    def upgrade_stream_history(self, *, reviewed: bool, expected_digest: str) -> dict[str, Any]:
        """Explicitly migrate the exact reviewed inventory; never run implicitly."""
        if reviewed is not True:
            raise WorkPrivacyBlocked('explicit Work stream inventory review is required')
        if not isinstance(expected_digest, str) or not _HEX.fullmatch(expected_digest):
            raise WorkPrivacyBlocked('the reviewed Work stream inventory digest is required')
        with self._locked():
            before = self._load_current()
            if before['schema'] != 'artha.work-member-history.v1':
                raise WorkPrivacyBlocked('Work stream history is already enrolled')
            streams = self._inventory_streams()
            self._validate_stream_inventory(streams, before['members'])
            review = self._stream_review(before, streams)
            if not hmac.compare_digest(expected_digest, review['inventory_sha256']):
                raise WorkPrivacyBlocked('reviewed Work stream inventory no longer matches')
            pending = self._prepare(before, before['members'], {}, (), streams=streams, upgrade_streams=True)
            _atomic(self.authority_dir / 'pending', self._encrypt(pending, 'pending'))
            self.observe('intent')
            return self._finish(pending)

    def bind_checkpoint(self) -> dict[str, Any]:
        """Authenticated provenance only. Every surviving member still needs human review."""
        with self._locked():
            current = self._load_current()
            body = {'schema': 'artha.work-checkpoint-binding.v1', 'profile': 'work',
                    'identity': current['identity'], 'generation': current['generation'],
                    'head_sha256': _digest(current['head']), 'members': current['members'],
                    'plaintext_allowed': False}
            if current['schema'] == 'artha.work-member-history.v2':
                body.update(schema='artha.work-checkpoint-binding.v2', streams=current['streams'])
            return self._sign(body, 'checkpoint')

    def _check_checkpoint(self, binding: Mapping[str, Any], current: dict[str, Any]) -> None:
        body = self._verify(binding, 'checkpoint')
        fields = {'schema', 'profile', 'identity', 'generation', 'head_sha256', 'members', 'plaintext_allowed'}
        if body.get('schema') == 'artha.work-checkpoint-binding.v2':
            fields.add('streams')
        if (set(body) != fields
                or body['schema'] not in {'artha.work-checkpoint-binding.v1', 'artha.work-checkpoint-binding.v2'}
                or body['profile'] != 'work'
                or body['identity'] != current['identity'] or body['plaintext_allowed'] is not False
                or type(body['generation']) is not int or not 1 <= body['generation'] <= current['generation']):
            raise WorkPrivacyBlocked('checkpoint lacks original Work installation binding')
        generation = body['generation']
        if (body['schema'] == 'artha.work-checkpoint-binding.v1'
                and current['schema'] == 'artha.work-member-history.v2'
                and generation >= current['stream_since']):
            raise WorkPrivacyBlocked('checkpoint binding omits required stream history')
        event = current['events'][generation - 1]
        head = (_digest(current['head']) if generation == current['generation']
                else current['events'][generation]['previous_head_sha256'])
        if event['members_sha256'] != _digest(body['members']) or body['head_sha256'] != head:
            raise WorkPrivacyBlocked('checkpoint history is not retained in current Work authority')
        if body['schema'] == 'artha.work-checkpoint-binding.v2':
            if (current['schema'] != 'artha.work-member-history.v2'
                    or generation < current['stream_since']
                    or event.get('streams_sha256') != _digest(body['streams'])):
                raise WorkPrivacyBlocked('checkpoint stream history is not retained in current Work authority')
            self._validate_stream_inventory(body['streams'], body['members'])

    def check_checkpoint(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        with self._locked():
            current = self._load_current()
            self._check_checkpoint(binding, current)
            return current

    def denied_members(self, binding: Mapping[str, Any], members: Sequence[str]) -> set[str]:
        with self._locked():
            current = self._load_current()
            self._check_checkpoint(binding, current)
            for name in members:
                self._member(name)
            return {name for name in members if name not in binding['members']
                    or name in current['revoked']
                    or current['invalidated'].get(name, 0) > binding['generation']}

    def denied_streams(self, binding: Mapping[str, Any], streams: Sequence[str]) -> set[str]:
        with self._locked():
            current = self._load_current()
            self._check_checkpoint(binding, current)
            identities = {key: self._stream_parts(key) for key in streams}
            if binding['schema'] != 'artha.work-checkpoint-binding.v2':
                return set(identities)
            return {key for key, (owner, kind, _, _) in identities.items()
                    if kind != 'file' or key not in binding['streams']
                    or owner not in binding['members'] or owner in current['revoked']
                    or current['invalidated'].get(owner, 0) > binding['generation']
                    or current['stream_invalidated'].get(key, 0) > binding['generation']
                    or current['streams'].get(key) != binding['streams'].get(key)}


class _DeferredNativeBackend:
    def __init__(self) -> None:
        self._backend: Any = None

    def _native(self) -> Any:
        if self._backend is None:
            backend = system_keyring()
            if type(backend).__module__.lower() == 'keyring.backends.windows':
                backend = copy(backend)
                backend.persist = 2  # CRED_PERSIST_LOCAL_MACHINE; Work authority must not roam.
            self._backend = backend
        return self._backend

    def get_password(self, service: str, account: str) -> str | None:
        value = self._native().get_password(service, account)
        if value is not None and not isinstance(value, str):
            raise WorkPrivacyBlocked('native Work credential record is malformed')
        return value

    def set_password(self, service: str, account: str, value: str) -> None:
        self._native().set_password(service, account, value)


def native_windows_identity() -> dict[str, str]:
    """Resolve native identity only when explicitly invoked; never use a hostname fallback."""
    if sys.platform != 'win32':
        raise WorkPrivacyBlocked('local Work recovery requires the original Windows installation')
    import winreg  # noqa: PLC0415

    from .quarantine_fs_windows import _native  # noqa: PLC0415

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        value, kind = winreg.QueryValueEx(key, 'MachineGuid')
    if kind != winreg.REG_SZ or not isinstance(value, str):
        raise WorkPrivacyBlocked('native Windows machine identity is unavailable')
    try:
        machine = str(uuid.UUID(value))
    except ValueError:
        raise WorkPrivacyBlocked('native Windows machine identity is invalid') from None
    user, _ = _native().current_user_sid()
    if not isinstance(user, str) or not re.fullmatch(r'S-[0-9]+(?:-[0-9]+)+', user):
        raise WorkPrivacyBlocked('native Windows user identity is invalid')
    return {'platform': 'win32', 'machine': machine, 'user': user}


def for_installation(repo: Path, local: Path | None = None, *, backend: Any = None,
                     native_identity: Callable[[], Mapping[str, str]] | None = None,
                     observe: Callable[[str], None] | None = None) -> WorkPrivacy:
    """Canonical Work roots only; outside-root credentials remain checkpoint dependencies."""
    local = default_installation_dir() if local is None else local
    return WorkPrivacy(repo, local, {'work_state': repo / 'state/work', 'work_local': local / 'work'},
                       backend=_DeferredNativeBackend() if backend is None else backend,
                       native_identity=native_windows_identity if native_identity is None else native_identity,
                       observe=observe)
