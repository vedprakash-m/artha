"""Encrypted historical members; never a restoration or release authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, cast

from .crypto import EnvelopeCipher
from .quarantine_fs_windows import (
    FileIdentity,
    QuarantineFilesystemError,
    WindowsQuarantineDirectory,
)

if sys.platform != 'win32':
    import fcntl

_LOCK = '.lock'
_Directory = int | WindowsQuarantineDirectory

CHUNK_BYTES = 1024 * 1024
MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_AUTHORITY_BYTES = 64 * 1024
MAX_ENTRIES = 16384
MAX_CHUNKS = 65536
MAX_MEMBER_BYTES = 16 * 1024**3
MAX_TOTAL_BYTES = 64 * 1024**3
_SCHEMA = 'artha.recovery-quarantine.v1'
_CATALOG = 'catalog.ae1'
_OVERHEAD = 35
_HEX = re.compile(r'[0-9a-f]{64}')
_ID = re.compile(r'[0-9a-f]{32}')
_CHUNK = re.compile(r'[0-9a-f]{32}-[0-9]{5}\.ae1')


class QuarantineError(RuntimeError):
    """The encrypted quarantine operation was refused; no release is authorized."""


def _native_identity(identity: FileIdentity) -> tuple[int, int]:
    return identity.volume_serial, int.from_bytes(identity.file_id, 'big')


def _posix_owner() -> int:
    if sys.platform == 'win32' or os.name != 'posix':
        raise QuarantineError('secure descriptor-relative quarantine IO is unavailable')
    return os.getuid()


def _json(value: Any, limit: int = MAX_CATALOG_BYTES) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) > limit:
            raise ValueError
        return raw
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise QuarantineError('quarantine metadata is invalid or exceeds its bound') from None


def _decode(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError
        _json(result)
        return result
    except (ValueError, RecursionError, UnicodeError):
        raise QuarantineError('quarantine metadata is invalid') from None


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _integer(value: Any, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _text(value: Any, maximum: int) -> bool:
    try:
        return isinstance(value, str) and bool(value) and len(value.encode('utf-8')) <= maximum
    except UnicodeError:
        return False


def _entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QuarantineError('quarantine member metadata is invalid')
    path = value.get('path')
    if (not isinstance(path, str) or not _text(path, 4096)
            or path.startswith('/') or '\\' in path or ':' in path
            or any(part in {'', '.', '..'} for part in path.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or not _integer(value.get('size'), MAX_MEMBER_BYTES)
            or not _digest(value.get('sha256')) or value.get('kind') not in {'file', 'sqlite'}
            or not _integer(value.get('mode'), 0o7777)):
        raise QuarantineError('quarantine member metadata is invalid')
    return value


class QuarantineStore:
    """Owner-authorized, bounded AE1 chunks and catalog, with no plaintext disk IO.

    Capture pins authority from begin through seal. Later operations pin fresh current
    authority, not the checkpoint's obsolete permissions. The caller must replay known
    erasures with block_paths and retain its inactive-installation marker.
    """

    def __init__(self, root: Path, cipher: EnvelopeCipher, *,
                 authority: Callable[[], Mapping[str, Any]]):
        self.root = Path(os.path.abspath(root))
        self.cipher = cipher
        self.authority = authority
        self._identity: tuple[int, int] | None = None
        self._capture: dict[str, Any] | None = None
        self._catalog: dict[str, Any] | None = None
        self._expected: dict[str, dict[str, Any]] = {}
        self._active: dict[str, Any] | None = None
        self._hasher: Any = None
        self._failed = False

    def _context(self) -> dict[str, Any]:
        try:
            value = dict(self.authority())
            result = _decode(_json(value, MAX_AUTHORITY_BYTES))
            if (not isinstance(result.get('actor'), str) or not result['actor']
                    or result.get('owner') != result['actor']
                    or result.get('profile') not in {'personal', 'work'}
                    ):
                raise ValueError
            if result['profile'] == 'work':
                work = result.get('work')
                if (not isinstance(work, dict) or set(work) != {'identity', 'generation', 'head_sha256'}
                        or not _digest(work.get('identity'))
                        or not _integer(work.get('generation'), 2**63 - 1) or work['generation'] < 1
                        or not _digest(work.get('head_sha256')) or 'privacy' in result):
                    raise ValueError
            else:
                privacy = result.get('privacy')
                if not isinstance(privacy, dict) or not privacy:
                    raise ValueError
                for namespace, head in privacy.items():
                    if (not namespace or not isinstance(head, dict)
                            or not _integer(head.get('checkpoint_id'), 2**63 - 1)
                            or not _integer(head.get('permission_generation'), 2**63 - 1)
                            or not _digest(head.get('tombstone_digest'))):
                        raise ValueError
            return result
        except Exception:
            raise QuarantineError('current owner and privacy history are required') from None

    def _check_context(self, context: dict[str, Any]) -> None:
        if self._context() != context:
            raise QuarantineError('quarantine authority changed during the operation')

    def _check_open(self, descriptor: _Directory, context: dict[str, Any]) -> None:
        if isinstance(descriptor, WindowsQuarantineDirectory):
            # Held native handles deny FILE_SHARE_DELETE, so the root chain cannot be renamed.
            self._check_context(context)
            try:
                descriptor.snapshot()
            except QuarantineFilesystemError:
                raise QuarantineError('quarantine directory changed during the operation') from None
        else:
            self._check(context)

    def _check(self, context: dict[str, Any]) -> None:
        self._check_context(context)
        if self._identity is not None:
            if os.name == 'nt':
                try:
                    with WindowsQuarantineDirectory.open(self.root) as native:
                        if _native_identity(native.identity) != self._identity:
                            raise QuarantineError('quarantine directory changed during the operation')
                except Exception:
                    raise QuarantineError('quarantine directory changed during the operation') from None
                return
            descriptor = -1
            try:
                descriptor = self._directory(self.root)
                info = os.fstat(descriptor)
                if ((info.st_dev, info.st_ino) != self._identity or info.st_uid != _posix_owner()
                        or stat.S_IMODE(info.st_mode) != 0o700):
                    raise QuarantineError('quarantine directory changed during the operation')
            except Exception:
                raise QuarantineError('quarantine directory changed during the operation') from None
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    @staticmethod
    def _directory(path: Path) -> int:
        if sys.platform == 'win32' or os.name != 'posix':
            raise QuarantineError('secure descriptor-relative quarantine IO is unavailable')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(path.anchor, flags)
        try:
            for component in path.parts[1:]:
                next_fd = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_fd
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @contextmanager
    def _opened(self, context: dict[str, Any]) -> Iterator[_Directory]:
        if os.name == 'nt':
            with self._opened_native(context) as native:
                yield native
            return
        descriptor = -1
        try:
            self._check(context)
            descriptor = self._directory(self.root)
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            if (info.st_uid != _posix_owner() or stat.S_IMODE(info.st_mode) != 0o700
                    or (self._identity is not None and self._identity != identity)):
                raise QuarantineError('quarantine directory is not private or has changed')
            if sys.platform == 'win32':
                raise QuarantineError('secure descriptor-relative quarantine IO is unavailable')
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._identity = identity
            yield descriptor
            self._check(context)
        except QuarantineError:
            raise
        except Exception:
            raise QuarantineError('quarantine filesystem operation was refused') from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @contextmanager
    def _opened_native(self, context: dict[str, Any]) -> Iterator[WindowsQuarantineDirectory]:
        try:
            self._check_context(context)
            with WindowsQuarantineDirectory.open(self.root) as native:
                identity = _native_identity(native.identity)
                if self._identity is not None and self._identity != identity:
                    raise QuarantineError('quarantine directory is not private or has changed')
                with native.lock():
                    self._identity = identity
                    yield native
                    self._check(context)
        except QuarantineError:
            raise
        except Exception:
            raise QuarantineError('quarantine filesystem operation was refused') from None

    def _catalog_aad(self, descriptor: _Directory, context: dict[str, Any]) -> bytes:
        if isinstance(descriptor, WindowsQuarantineDirectory):
            device, inode = _native_identity(descriptor.identity)
        else:
            info = os.fstat(descriptor)
            device, inode = info.st_dev, info.st_ino
        return _json([_SCHEMA, 'catalog', context['owner'], context['profile'], device, inode])

    @staticmethod
    def _chunk_aad(catalog: dict[str, Any], member: dict[str, Any], position: int) -> bytes:
        return _json([_SCHEMA, catalog['profile'], catalog['checkpoint_id'],
                      catalog['checkpoint_sha256'], member['member_id'], member['path'], position])

    def _read(self, descriptor: _Directory, name: str, maximum: int, context: dict[str, Any]) -> bytes:
        if isinstance(descriptor, WindowsQuarantineDirectory):
            self._check_context(context)
            try:
                with descriptor.open_file(name) as native:
                    size = native.snapshot().size
                    if not _OVERHEAD <= size <= maximum:
                        raise QuarantineError('quarantine ciphertext is unsafe or exceeds its bound')
                    raw = native.read(maximum)
            except QuarantineFilesystemError:
                raise QuarantineError('quarantine ciphertext changed or is unsafe') from None
            if len(raw) != size:
                raise QuarantineError('quarantine ciphertext changed during read')
            self._check_context(context)
            return raw
        self._check(context)
        if sys.platform == 'win32':
            raise QuarantineError('secure descriptor-relative quarantine IO is unavailable')
        opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(opened)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != _posix_owner() or stat.S_IMODE(before.st_mode) != 0o600
                    or not _OVERHEAD <= before.st_size <= maximum):
                raise QuarantineError('quarantine ciphertext is unsafe or exceeds its bound')
            with os.fdopen(opened, 'rb', closefd=False) as handle:
                raw = handle.read(maximum + 1)
            after = os.fstat(opened)
            physical = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (len(raw) != before.st_size or len(raw) > maximum
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or (physical.st_dev, physical.st_ino) != (before.st_dev, before.st_ino)):
                raise QuarantineError('quarantine ciphertext changed during read')
        finally:
            os.close(opened)
        self._check(context)
        return raw

    def _write(self, descriptor: _Directory, name: str, raw: bytes,
               context: dict[str, Any]) -> FileIdentity | None:
        if isinstance(descriptor, WindowsQuarantineDirectory):
            self._check_context(context)
            try:
                with descriptor.create_file(name) as native:
                    native.write_all(raw)
                    native.flush()
                    identity = native.identity
            except QuarantineFilesystemError:
                raise QuarantineError('quarantine ciphertext write was refused') from None
            self._check_context(context)
            return identity
        self._check(context)
        if sys.platform == 'win32':
            raise QuarantineError('secure descriptor-relative quarantine IO is unavailable')
        opened = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
        try:
            os.fchmod(opened, 0o600)
            with os.fdopen(opened, 'wb', closefd=False) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(opened)
        finally:
            os.close(opened)
        self._check(context)
        return None

    def _publish(self, descriptor: _Directory, catalog: dict[str, Any], context: dict[str, Any], *,
                 previous: bytes | None = None) -> None:
        raw, _ = self.cipher.encrypt(_json(catalog), aad=self._catalog_aad(descriptor, context))
        name = secrets.token_hex(16) + '.ae1'
        staged = self._write(descriptor, name, raw, context)
        if isinstance(descriptor, WindowsQuarantineDirectory):
            if staged is None:
                raise QuarantineError('quarantine catalog staging identity is unavailable')
            self._publish_native(descriptor, name, staged, raw, context, previous)
            return
        try:
            self._check(context)
            if self._read(descriptor, name, MAX_CATALOG_BYTES + _OVERHEAD, context) != raw:
                raise QuarantineError('quarantine catalog staging changed before publication')
            if previous is None:
                os.link(name, _CATALOG, src_dir_fd=descriptor, dst_dir_fd=descriptor,
                        follow_symlinks=False)
            else:
                if self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context) != previous:
                    raise QuarantineError('quarantine catalog changed during update')
                os.replace(name, _CATALOG, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            self._check(context)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=descriptor)
        if self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context) != raw:
            raise QuarantineError('quarantine catalog changed during publication')
        os.fsync(descriptor)
        self._check(context)

    def _publish_native(self, descriptor: WindowsQuarantineDirectory, name: str, staged: FileIdentity,
                        raw: bytes, context: dict[str, Any], previous: bytes | None) -> None:
        published = False
        try:
            if self._read(descriptor, name, MAX_CATALOG_BYTES + _OVERHEAD, context) != raw:
                raise QuarantineError('quarantine catalog staging changed before publication')
            if previous is None:
                descriptor.publish_write_once(name, _CATALOG, expected_identity=staged)
            else:
                with descriptor.open_file(_CATALOG) as current:
                    replaced = current.identity
                if self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context) != previous:
                    raise QuarantineError('quarantine catalog changed during update')
                descriptor.replace_file(name, _CATALOG, expected_identity=staged,
                                        replaced_identity=replaced)
            published = True
        except QuarantineFilesystemError:
            raise QuarantineError('quarantine catalog publication was refused') from None
        finally:
            if not published:
                with suppress(QuarantineFilesystemError):
                    descriptor.delete_file(name, expected_identity=staged)
        if self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context) != raw:
            raise QuarantineError('quarantine catalog changed during publication')
        self._check(context)

    def begin(self, manifest: Mapping[str, Any], checkpoint_sha256: str) -> None:
        context = self._context()
        if not isinstance(manifest, Mapping):
            raise QuarantineError('quarantine checkpoint metadata is invalid')
        value = _decode(_json(dict(manifest)))
        entries = value.get('entries')
        generation = value.get('generation')
        if (self._capture is not None or value.get('profile') != context['profile']
                or not isinstance(generation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', generation)
                or not _digest(checkpoint_sha256) or not isinstance(entries, list)
                or len(entries) > MAX_ENTRIES
                or ('privacy' in value and value['privacy'] != context['privacy'])):
            raise QuarantineError('quarantine checkpoint identity is invalid')
        expected: dict[str, dict[str, Any]] = {}
        for entry in entries:
            checked = _entry(entry)
            if checked['path'] in expected or set(checked) & {'member_id', 'state', 'chunks', 'reason'}:
                raise QuarantineError('duplicate or reserved quarantine member metadata')
            expected[checked['path']] = checked
        if sum(entry['size'] for entry in entries) > MAX_TOTAL_BYTES:
            raise QuarantineError('quarantine total exceeds its bound')
        parent = -1
        try:
            self._check(context)
            if os.name == 'nt':
                WindowsQuarantineDirectory.create(self.root).close()
            else:
                parent = self._directory(self.root.parent)
                os.mkdir(self.root.name, 0o700, dir_fd=parent)
        except Exception:
            raise QuarantineError('a fresh private quarantine directory is required') from None
        finally:
            if parent >= 0:
                os.close(parent)
        with self._opened(context):
            self._expected = expected
            self._capture = context
            self._catalog = {'schema': _SCHEMA, 'checkpoint_id': generation,
                             'checkpoint_sha256': checkpoint_sha256, 'profile': context['profile'],
                             'owner': context['owner'], 'entries': []}

    def _capturing(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._failed or self._capture is None or self._catalog is None:
            raise QuarantineError('quarantine capture is inactive')
        if os.name == 'nt':
            # Every caller then opens the root and compares its pinned identity.
            self._check_context(self._capture)
        else:
            self._check(self._capture)
        return self._capture, self._catalog

    def start(self, entry: Mapping[str, Any]) -> None:
        context, catalog = self._capturing()
        with self._opened(context):
            checked = _entry(_decode(_json(dict(entry))))
            if (self._active is not None or self._expected.get(checked['path']) != checked
                    or any(member['path'] == checked['path'] for member in catalog['entries'])):
                raise QuarantineError('quarantine member is unexpected or already captured')
            self._active = dict(checked, member_id=secrets.token_hex(16), chunks=[], state='quarantined')
            self._hasher = hashlib.sha256()

    def write(self, chunk: bytes) -> None:
        context, catalog = self._capturing()
        if self._active is None or not isinstance(chunk, bytes) or len(chunk) > CHUNK_BYTES:
            raise QuarantineError('quarantine chunk is invalid or exceeds its bound')
        if not chunk:
            with self._opened(context):
                return
        member = self._active
        length = sum(part['length'] for part in member['chunks'])
        count = sum(len(item['chunks']) for item in catalog['entries']) + len(member['chunks'])
        if length + len(chunk) > member['size'] or count >= MAX_CHUNKS:
            raise QuarantineError('quarantine member exceeds its declared bounds')
        try:
            with self._opened(context) as descriptor:
                position = len(member['chunks'])
                name = f"{member['member_id']}-{position:05d}.ae1"
                raw, _ = self.cipher.encrypt(chunk, aad=self._chunk_aad(catalog, member, position))
                record = {'name': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                          'length': len(chunk), 'ciphertext_length': len(raw)}
                candidate = dict(member, chunks=[*member['chunks'], record])
                _json(dict(catalog, entries=[*catalog['entries'], candidate]))
                self._write(descriptor, name, raw, context)
                member['chunks'].append(record)
                self._hasher.update(chunk)
        except Exception:
            self._failed = True
            raise QuarantineError('encrypted quarantine chunk capture failed') from None

    def finish(self) -> None:
        context, catalog = self._capturing()
        with self._opened(context):
            member = self._active
            if (member is None or sum(chunk['length'] for chunk in member['chunks']) != member['size']
                    or self._hasher.hexdigest() != member['sha256']):
                self._failed = True
                raise QuarantineError('quarantine member does not match the checkpoint')
            catalog['entries'].append(member)
            self._active = None
            self._hasher = None

    def set_recovery_affinity(self, metadata: Mapping[str, Any]) -> None:
        """Bind original source affinity once before the first seal; later privacy replay cannot replace it.

        The recovery boundary validates the source/index semantics. Storage authenticates and bounds this
        original metadata together with the checkpoint catalog; generic quarantine callers may omit it.
        """
        context, catalog = self._capturing()
        with self._opened(context):
            if (self._active is not None or 'recovery_affinity' in catalog
                    or not isinstance(metadata, Mapping)
                    or metadata.get('schema') != 'artha.recovery-affinity.v1'):
                raise QuarantineError('original recovery affinity requires a single capture-only binding')
            affinity = _decode(_json(dict(metadata)))
            _json(dict(catalog, recovery_affinity=affinity))
            catalog['recovery_affinity'] = affinity

    def seal(self) -> dict[str, Any]:
        context, catalog = self._capturing()
        with self._opened(context) as descriptor:
            if self._active is not None:
                raise QuarantineError('quarantine member is incomplete')
            self._validate(catalog, context)
            captured = {member['path']: {key: value for key, value in member.items()
                                        if key not in {'member_id', 'state', 'chunks'}}
                        for member in catalog['entries']}
            if captured != self._expected:
                raise QuarantineError('quarantine capture does not match the expected members')
            self._physical(descriptor, catalog, sealed=False)
            for member in catalog['entries']:
                self._verify_member(descriptor, catalog, member, context)
            self._publish(descriptor, catalog, context)
            result = _decode(_json(catalog))
            self._capture = None
            return result

    @staticmethod
    def _validate(catalog: dict[str, Any], context: dict[str, Any]) -> None:
        required = {'schema', 'checkpoint_id', 'checkpoint_sha256', 'profile', 'owner', 'entries'}
        if (not required <= set(catalog) or set(catalog) - required - {'recovery_affinity'}
                or catalog.get('schema') != _SCHEMA or catalog.get('profile') != context['profile']
                or catalog.get('owner') != context['owner'] or not _digest(catalog.get('checkpoint_sha256'))
                or not isinstance(catalog.get('checkpoint_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', catalog['checkpoint_id'])
                or not isinstance(catalog.get('entries'), list) or len(catalog['entries']) > MAX_ENTRIES):
            raise QuarantineError('quarantine catalog identity is invalid')
        if 'recovery_affinity' in catalog:
            affinity = catalog['recovery_affinity']
            if not isinstance(affinity, dict) or affinity.get('schema') != 'artha.recovery-affinity.v1':
                raise QuarantineError('original recovery affinity metadata is invalid')
            _json(affinity)
        paths: set[str] = set()
        identifiers: set[str] = set()
        total = count = 0
        for member in catalog['entries']:
            _entry(member)
            identifier = member.get('member_id')
            if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                    or member['path'] in paths or identifier in identifiers
                    or member.get('state') not in {'quarantined', 'blocked'}
                    or not isinstance(member.get('chunks'), list)):
                raise QuarantineError('quarantine catalog member is invalid')
            paths.add(member['path'])
            identifiers.add(identifier)
            size = 0
            for position, chunk in enumerate(member['chunks']):
                if (not isinstance(chunk, dict)
                        or set(chunk) != {'name', 'sha256', 'length', 'ciphertext_length'}
                        or chunk['name'] != f'{identifier}-{position:05d}.ae1'
                        or not _CHUNK.fullmatch(chunk['name']) or not _digest(chunk['sha256'])
                        or not _integer(chunk['length'], CHUNK_BYTES) or chunk['length'] == 0
                        or chunk['ciphertext_length'] != chunk['length'] + _OVERHEAD):
                    raise QuarantineError('quarantine catalog chunk is invalid')
                size += chunk['length']
                count += 1
            if size != member['size']:
                raise QuarantineError('quarantine catalog member length is invalid')
            total += size
        if count > MAX_CHUNKS or total > MAX_TOTAL_BYTES:
            raise QuarantineError('quarantine catalog exceeds its bounds')

    def _load(self, descriptor: _Directory, context: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        raw = self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context)
        try:
            catalog = _decode(self.cipher.decrypt(raw, aad=self._catalog_aad(descriptor, context)))
        except Exception:
            raise QuarantineError('quarantine catalog authentication failed') from None
        self._validate(catalog, context)
        self._check(context)
        return catalog, raw

    def load_catalog(self) -> dict[str, Any]:
        context = self._context()
        with self._opened(context) as descriptor:
            catalog, _ = self._load(descriptor, context)
            self._physical(descriptor, catalog)
            return catalog

    def inventory(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.load_catalog()['entries'])

    @staticmethod
    def _physical(descriptor: _Directory, catalog: dict[str, Any], *, sealed: bool = True
                  ) -> dict[str, tuple[int, int, int, int, int]]:
        expected = {chunk['name']: chunk['ciphertext_length']
                    for member in catalog['entries'] for chunk in member['chunks']}
        if sealed:
            expected[_CATALOG] = None
        if isinstance(descriptor, WindowsQuarantineDirectory):
            return QuarantineStore._physical_native(descriptor, expected)
        if set(os.listdir(descriptor)) != set(expected):
            raise QuarantineError('quarantine contains missing or unexpected ciphertext')
        identities = {}
        for name, length in expected.items():
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != _posix_owner() or stat.S_IMODE(info.st_mode) != 0o600
                    or (length is not None and info.st_size != length)):
                raise QuarantineError('quarantine contains linked or unsafe ciphertext')
            identities[name] = (info.st_dev, info.st_ino, info.st_size,
                                info.st_mtime_ns, info.st_ctime_ns)
        return identities

    @staticmethod
    def _physical_native(descriptor: WindowsQuarantineDirectory, expected: dict[str, int | None]
                         ) -> dict[str, tuple[int, int, int, int, int]]:
        identities = {}
        try:
            if set(descriptor.list_files(validate=False)) != set(expected) | {_LOCK}:
                raise QuarantineError('quarantine contains missing or unexpected ciphertext')
            for name, length in expected.items():
                with descriptor.open_file(name) as native:
                    info = native.snapshot()
                if length is not None and info.size != length:
                    raise QuarantineError('quarantine contains linked or unsafe ciphertext')
                identities[name] = (*_native_identity(info.identity), info.size,
                                    info.last_write_100ns, info.change_100ns)
        except QuarantineFilesystemError:
            raise QuarantineError('quarantine contains linked or unsafe ciphertext') from None
        return identities

    def _plaintext(self, descriptor: _Directory, catalog: dict[str, Any], member: dict[str, Any],
                   position: int, context: dict[str, Any]) -> bytes:
        chunk = member['chunks'][position]
        raw = self._read(descriptor, chunk['name'], CHUNK_BYTES + _OVERHEAD, context)
        if len(raw) != chunk['ciphertext_length'] or hashlib.sha256(raw).hexdigest() != chunk['sha256']:
            raise QuarantineError('quarantine ciphertext fingerprint does not match')
        try:
            plaintext = self.cipher.decrypt(raw, aad=self._chunk_aad(catalog, member, position))
        except Exception:
            raise QuarantineError('quarantine member authentication failed') from None
        if len(plaintext) != chunk['length']:
            raise QuarantineError('quarantine plaintext length does not match')
        self._check_open(descriptor, context)
        return plaintext

    def _verify_member(self, descriptor: _Directory, catalog: dict[str, Any], member: dict[str, Any],
                       context: dict[str, Any]) -> None:
        digest = hashlib.sha256()
        size = 0
        for position in range(len(member['chunks'])):
            plaintext = self._plaintext(descriptor, catalog, member, position, context)
            size += len(plaintext)
            digest.update(plaintext)
        if size != member['size'] or digest.hexdigest() != member['sha256']:
            raise QuarantineError('quarantine original member fingerprint does not match')
        self._check_open(descriptor, context)

    def iter_member(self, member_id: str) -> Iterator[bytes]:
        context = self._context()
        with self._opened(context) as descriptor:
            catalog, pinned = self._load(descriptor, context)
            physical = self._physical(descriptor, catalog)
            member = next((item for item in catalog['entries'] if item['member_id'] == member_id), None)
            if member is None or member['state'] == 'blocked':
                raise QuarantineError('quarantine member is unavailable or blocked')
            self._verify_member(descriptor, catalog, member, context)
            for position in range(len(member['chunks'])):
                if self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context) != pinned:
                    raise QuarantineError('quarantine catalog changed during read')
                plaintext = self._plaintext(descriptor, catalog, member, position, context)
                if self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context) != pinned:
                    raise QuarantineError('quarantine catalog changed during read')
                if self._physical(descriptor, catalog) != physical:
                    raise QuarantineError('quarantine ciphertext changed during read')
                self._check(context)
                yield plaintext
                self._check(context)
            self._physical(descriptor, catalog)

    def verify(self) -> dict[str, Any]:
        context = self._context()
        with self._opened(context) as descriptor:
            catalog, pinned = self._load(descriptor, context)
            self._physical(descriptor, catalog)
            for member in catalog['entries']:
                self._verify_member(descriptor, catalog, member, context)
            if self._read(descriptor, _CATALOG, MAX_CATALOG_BYTES + _OVERHEAD, context) != pinned:
                raise QuarantineError('quarantine catalog changed during verification')
            self._physical(descriptor, catalog)
            return {'members': len(catalog['entries']),
                    'chunks': sum(len(item['chunks']) for item in catalog['entries']),
                    'bytes': sum(item['size'] for item in catalog['entries']),
                    'blocked': sum(item['state'] == 'blocked' for item in catalog['entries'])}

    def block_paths(self, paths: Sequence[str], reason: str) -> None:
        context = self._context()
        if (isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence)
                or len(paths) > MAX_ENTRIES or not all(isinstance(path, str) for path in paths)
            or not _text(reason, 1024)):
            raise QuarantineError('quarantine denial metadata is invalid')
        with self._opened(context) as descriptor:
            catalog, pinned = self._load(descriptor, context)
            self._physical(descriptor, catalog)
            known = {entry['path'] for entry in catalog['entries']}
            if not set(paths) <= known:
                raise QuarantineError('quarantine denial contains unknown members')
            for member in catalog['entries']:
                if member['path'] in paths:
                    member['state'] = 'blocked'
                    member['reason'] = reason
            self._publish(descriptor, catalog, context, previous=pinned)
