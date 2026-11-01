"""Owner-attested, encrypted inert exports; never activation or lineage authority."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .crypto import EnvelopeCipher
from .quarantine_fs_windows import (
    FileIdentity,
    QuarantineFilesystemError,
    WindowsQuarantineDirectory,
    open_owner_private_path,
)
from .session_lock import session_lock

MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_EXPORT_JSON_BYTES = 24 * 1024 * 1024
_EXPORT_AAD = b'artha.quarantine-export.v2'
_EXPORT_OVERHEAD = 3 + 4 + 12 + 16
MAX_VIEW_BYTES = 256 * 1024
REVIEW_LIFETIME = timedelta(minutes=15)
_TTL_MICROSECONDS = int(REVIEW_LIFETIME.total_seconds()) * 1_000_000
_AAD = b'artha.quarantine-clean-file-review.v1'
_SUFFIXES = {'.md', '.txt', '.pdf', '.csv'}
_CODE_SUFFIXES = {'.py', '.pyw', '.js', '.jsx', '.ts', '.tsx', '.sh', '.bash', '.zsh', '.ps1',
                  '.exe', '.dll', '.so', '.dylib', '.bat', '.cmd', '.com', '.vbs', '.rb', '.php',
                  '.html', '.htm', '.svg', '.wasm', '.ipynb', '.toml', '.yaml', '.yml', '.ini', '.cfg'}
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,119}\Z')
_RESERVED = {'con', 'prn', 'aux', 'nul', 'clock$', 'conin$', 'conout$',
             *(f'com{number}' for number in range(10)), *(f'lpt{number}' for number in range(10))}
_SENSITIVE_PATH = re.compile(
    r'config|\.claude|\.github|\.vscode|\.tokens|recovery|privacy|credential|secret|token|'
    r'keyring|keychain|(^|[-_.])keys?($|[-_.])|actions?|history|runtime|release|harness|'
    r'consent|tombstone|permission|registry|\.env', re.I)
_SENSITIVE_CONTENT = re.compile(
    rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:access_token|refresh_token|client_secret|'
    rb'api[_-]?key|password|authorization|bearer|forgotten|revoked|tombstone|'
    rb'permission_generation)\b|gh[pousr]_[A-Za-z0-9]+|eyJ[A-Za-z0-9_-]+\.'
    rb'[A-Za-z0-9_-]+\.|<script|javascript:|/JavaScript|/JS\b|/Launch\b|/EmbeddedFile|'
    rb'/OpenAction|/AA\b|/RichMedia|/XFA\b|/URI\b|/SubmitForm|/GoToR|/Encrypt\b', re.I)


class ReviewBlocked(ValueError):
    """No clean export was authorized; messages never contain historical content."""


class ReviewStore(Protocol):
    root: Path
    cipher: EnvelopeCipher
    authority: Callable[[], Mapping[str, Any]]

    def load_catalog(self) -> dict[str, Any]: ...

    def iter_member(self, member_id: str) -> Iterator[bytes]: ...


@dataclass(frozen=True)
class _NativeMetadata:
    st_dev: int
    st_ino: int
    st_mode: int
    st_nlink: int
    st_size: int
    st_mtime_ns: int
    st_ctime_ns: int
    st_file_attributes: int = 0


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                          allow_nan=False).encode('ascii')
    except (ValueError, TypeError, OverflowError):
        raise ReviewBlocked('invalid review schema') from None


def _digest(value: bytes | bytearray) -> str:
    return hashlib.sha256(value).hexdigest()


def export_aad(binding: dict[str, Any], review_digest: str) -> bytes:
    """Compatibility helper; v2 authenticates the review binding inside ciphertext."""
    if not isinstance(binding, dict) or not isinstance(review_digest, str) or not review_digest.isascii():
        raise ReviewBlocked('invalid export binding or review token')
    _canonical(binding)
    return _EXPORT_AAD


def _clock(now: datetime) -> int:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ReviewBlocked('an explicit trusted UTC clock is required')
    elapsed = now - datetime(1970, 1, 1, tzinfo=UTC)
    return (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds


def _posix_flag(name: str) -> int:
    flag = getattr(os, name, None)
    if not isinstance(flag, int):
        raise ReviewBlocked('secure descriptor-relative review IO is unavailable')
    return flag


def _ordinary(path: Path, *, directory: bool = False, owner: bool = True
              ) -> os.stat_result | _NativeMetadata:
    if os.name == 'nt':
        try:
            if directory:
                with WindowsQuarantineDirectory.open(path, owner_private=owner) as opened:
                    snapshot = opened.snapshot()
            else:
                with open_owner_private_path(path) as opened:
                    snapshot = opened.snapshot()
            return _NativeMetadata(
                snapshot.identity.volume_serial,
                int.from_bytes(snapshot.identity.file_id, 'big'),
                stat.S_IFDIR if directory else stat.S_IFREG,
                1,
                snapshot.size,
                snapshot.last_write_100ns * 100,
                snapshot.change_100ns * 100,
            )
        except QuarantineFilesystemError:
            raise ReviewBlocked('linked or unsafe review path') from None
    metadata = path.lstat()
    valid_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if (not valid_type or getattr(metadata, 'st_file_attributes', 0) & 0x400
            or (not directory and metadata.st_nlink != 1)):
        raise ReviewBlocked('linked or unsafe review path')
    if owner and os.name != 'nt':
        uid_reader = getattr(os, 'getuid')  # noqa: B009
        if getattr(metadata, 'st_uid', None) != uid_reader():
            raise ReviewBlocked('review path belongs to another local owner')
    return metadata


def _ancestry(path: Path) -> None:
    if os.name == 'nt':
        for parent in reversed((path, *path.parents)):
            try:
                with WindowsQuarantineDirectory.open(parent, owner_private=False):
                    pass
            except QuarantineFilesystemError:
                raise ReviewBlocked('linked or unsafe review ancestry') from None
        return
    for parent in reversed((path, *path.parents)):
        _ordinary(parent, directory=True, owner=False)


def _directory(path: Path) -> int:
    if os.name != 'posix' or os.open not in os.supports_dir_fd or '..' in path.parts:
        raise ReviewBlocked('secure descriptor-relative review IO is unavailable')
    flags = os.O_RDONLY | _posix_flag('O_DIRECTORY') | _posix_flag('O_NOFOLLOW')
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            opened = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = opened
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read(path: Path, limit: int) -> bytes:
    if os.name == 'nt':
        native_path = _ordinary(path)
        try:
            with open_owner_private_path(path) as native_file:
                native_before = native_file.snapshot()
                if ((native_before.identity.volume_serial, int.from_bytes(native_before.identity.file_id, 'big'))
                        != (native_path.st_dev, native_path.st_ino)):
                    raise ReviewBlocked('review input changed')
                data = native_file.read(limit + 1)
                native_after = native_file.snapshot()
                if (native_after != native_before
                        or native_file.check_path(native_before.identity) != native_before.identity):
                    raise ReviewBlocked('review input changed during read')
        except QuarantineFilesystemError:
            raise ReviewBlocked('review input changed during read') from None
        if len(data) > limit:
            raise ReviewBlocked('review input exceeds its bounded size')
        return data
    if os.name != 'posix':
        raise ReviewBlocked('secure descriptor-relative review IO is unavailable')
    posix_before = _ordinary(path)
    parent = _directory(path.absolute().parent)
    try:
        flags = os.O_RDONLY | _posix_flag('O_NOFOLLOW') | _posix_flag('O_NONBLOCK')
        descriptor = os.open(path.name, flags, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as handle:
            posix_opened = os.fstat(handle.fileno())
            if (posix_opened.st_dev, posix_opened.st_ino) != (posix_before.st_dev, posix_before.st_ino):
                raise ReviewBlocked('review input changed')
            data = handle.read(limit + 1)
            posix_after = os.fstat(handle.fileno())
            physical = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if ((posix_opened.st_size, posix_opened.st_mtime_ns, posix_opened.st_ctime_ns)
                    != (posix_after.st_size, posix_after.st_mtime_ns, posix_after.st_ctime_ns)
                    or (physical.st_dev, physical.st_ino) != (posix_opened.st_dev, posix_opened.st_ino)):
                raise ReviewBlocked('review input changed during read')
    finally:
        os.close(parent)
    if len(data) > limit:
        raise ReviewBlocked('review input exceeds its bounded size')
    return data


def _fence(root: Path) -> dict[str, str]:
    if os.name == 'nt':
        for parent in (root, *root.parents):
            try:
                with WindowsQuarantineDirectory.open(parent, owner_private=False) as opened:
                    if '.artha-restored.json' not in opened.list_files(validate=False):
                        continue
                    raw = opened.read_file('.artha-restored.json', 65536, private=False)
            except QuarantineFilesystemError:
                raise ReviewBlocked('invalid restored-installation fence') from None
            return _validate_fence(raw, parent / '.artha-restored.json')
        raise ReviewBlocked('restored-installation fence required')
    for parent in (root, *root.parents):
        marker = parent / '.artha-restored.json'
        if not os.path.lexists(marker):
            continue
        raw = _read(marker, 65536)
        return _validate_fence(raw, marker)
    raise ReviewBlocked('restored-installation fence required')


def _validate_fence(raw: bytes, marker: Path) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ReviewBlocked('invalid restored-installation fence') from None
    if not isinstance(value, dict):
        raise ReviewBlocked('invalid restored-installation fence')
    for field in ('ingestion', 'effects'):
        if value.get(field) != 'disabled' and value.get(field + '_disabled') is not True:
            raise ReviewBlocked('restored ingestion and effects must remain disabled')
        if field in value and value[field] != 'disabled':
            raise ReviewBlocked('contradictory restored-installation fence')
        if field + '_disabled' in value and value[field + '_disabled'] is not True:
            raise ReviewBlocked('contradictory restored-installation fence')
    return {'path': str(marker), 'sha256': _digest(raw)}


def _authority(store: ReviewStore, profile: str) -> dict[str, Any]:
    raw = store.authority()
    if not isinstance(raw, Mapping):
        raise ReviewBlocked('current recovery authority must be a mapping')
    value = dict(raw)
    _canonical(value)
    if (not isinstance(value, dict) or not isinstance(value.get('actor'), str) or not value['actor']
            or value.get('owner') != value['actor'] or value.get('profile') != profile):
        raise ReviewBlocked('current local owner and matching profile required')
    if profile == 'work':
        work = value.get('work')
        if (not isinstance(work, dict) or set(work) != {'identity', 'generation', 'head_sha256'}
                or not isinstance(work.get('identity'), str) or not _HASH.fullmatch(work['identity'])
                or type(work.get('generation')) is not int or not 1 <= work['generation'] < 2**63
                or not isinstance(work.get('head_sha256'), str) or not _HASH.fullmatch(work['head_sha256'])
                or 'privacy' in value):
            raise ReviewBlocked('invalid current Work privacy head')
    else:
        privacy = value.get('privacy')
        if not isinstance(privacy, dict) or not privacy:
            raise ReviewBlocked('current privacy heads required')
        for namespace, head in privacy.items():
            if (not isinstance(namespace, str) or not namespace or not isinstance(head, dict)
                    or type(head.get('checkpoint_id')) is not int or not 0 <= head['checkpoint_id'] < 2**63
                    or type(head.get('permission_generation')) is not int or head['permission_generation'] < 0
                    or not isinstance(head.get('tombstone_digest'), str)
                    or not _HASH.fullmatch(head['tombstone_digest'])):
                raise ReviewBlocked('invalid current privacy head')
    return value


def _output(store: ReviewStore, output_name: str, *, published: bool = False) -> Path:
    if (not isinstance(output_name, str) or not _NAME.fullmatch(output_name)
            or '..' in output_name or output_name.endswith('.')
            or output_name.split('.', maxsplit=1)[0] in _RESERVED or Path(output_name).suffix not in _SUFFIXES):
        raise ReviewBlocked('output requires a lowercase ASCII document basename of at most 120 characters')
    directory = store.root.absolute().parent / 'reviewed-exports'
    encrypted_name = output_name + '.ae1'
    if os.name == 'nt':
        try:
            with WindowsQuarantineDirectory.open(directory.parent, owner_private=False) as parent:
                matches = [name for name in parent.list_files(validate=False)
                           if name.casefold() == 'reviewed-exports']
                if not matches:
                    return directory / encrypted_name
                if matches != ['reviewed-exports']:
                    raise ReviewBlocked('export directory has a case-alias')
                with parent.open_directory(matches[0]) as exports:
                    names = exports.list_files(validate=False)
        except QuarantineFilesystemError:
            raise ReviewBlocked('export directory must be owner-private and unlinked') from None
        if any(name.casefold() in {output_name.casefold(), encrypted_name.casefold()}
               and (not published or name != encrypted_name) for name in names):
            raise ReviewBlocked('export already exists or aliases another export')
        return directory / encrypted_name
    if os.path.lexists(directory):
        _ancestry(directory)
        if os.name != 'nt' and directory.stat().st_mode & 0o077:
            raise ReviewBlocked('export directory must be owner-private')
        _ordinary(directory, directory=True)
        if any(path.name.casefold() in {output_name.casefold(), encrypted_name.casefold()}
               and (not published or path.name != encrypted_name) for path in directory.iterdir()):
            raise ReviewBlocked('export already exists or aliases another export')
    return directory / encrypted_name


def _binding(store: ReviewStore, member_id: str, output_name: str, *,
             _published: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    root = store.root.absolute()
    _ancestry(root)
    root_metadata = _ordinary(root, directory=True)
    parent_metadata = _ordinary(root.parent, directory=True, owner=os.name != 'nt')
    fence = _fence(root)
    catalog_bytes = _read(root / 'catalog.ae1', 8 * 1024 * 1024)
    catalog = store.load_catalog()
    if (not isinstance(catalog, dict) or catalog.get('schema') != 'artha.recovery-quarantine.v1'
            or catalog.get('profile') not in {'personal', 'work'}
            or not isinstance(catalog.get('checkpoint_id'), str) or not catalog['checkpoint_id']
            or not isinstance(catalog.get('checkpoint_sha256'), str)
            or not _HASH.fullmatch(catalog['checkpoint_sha256']) or not isinstance(catalog.get('entries'), list)):
        raise ReviewBlocked('invalid quarantine catalog')
    entries = catalog['entries']
    if any(not isinstance(entry, dict) for entry in entries):
        raise ReviewBlocked('invalid quarantine entry')
    matches = [entry for entry in entries if entry.get('member_id') == member_id]
    if not isinstance(member_id, str) or not member_id or len(matches) != 1:
        raise ReviewBlocked('one exact quarantine member required')
    entry = matches[0]
    source = entry.get('path')
    if not isinstance(source, str) or not source or '\\' in source:
        raise ReviewBlocked('invalid document source path')
    parts = source.split('/')
    if (any(part in {'', '.', '..'} or part.startswith('.') or _SENSITIVE_PATH.search(part) for part in parts)
            or (catalog['profile'] == 'personal' and any(
                part.casefold() in {'work', 'work_state', 'work_local'}
                or part.casefold().startswith('work_artifact_') for part in parts))
            or PurePosixPath(source).suffix.lower() not in _SUFFIXES
            or {suffix.lower() for suffix in PurePosixPath(source).suffixes} & _CODE_SUFFIXES
            or entry.get('kind') != 'file' or entry.get('state') != 'quarantined'
            or type(entry.get('mode')) is not int or not 0 <= entry['mode'] <= 0o100777
            or entry['mode'] & 0o7111 or type(entry.get('size')) is not int
            or not 0 <= entry['size'] <= MAX_EXPORT_BYTES
            or not isinstance(entry.get('sha256'), str) or not _HASH.fullmatch(entry['sha256'])
            or not isinstance(entry.get('chunks'), list)
            or (not entry['chunks'] and (entry['size'] != 0 or entry['sha256'] != _digest(b'')))
            or any(not isinstance(chunk, dict) or not isinstance(chunk.get('sha256'), str)
                   or not _HASH.fullmatch(chunk['sha256']) for chunk in entry['chunks'])):
        raise ReviewBlocked('member is not an eligible inert document')
    output = _output(store, output_name, published=_published)
    if Path(output_name).suffix != PurePosixPath(source).suffix.lower():
        raise ReviewBlocked('export must retain the document type')
    authority = _authority(store, catalog['profile'])
    binding = {'schema': 'artha.quarantine-review.v1', 'root': str(root), 'output': str(output),
               'root_identity': [root_metadata.st_dev, root_metadata.st_ino],
               'parent_identity': [parent_metadata.st_dev, parent_metadata.st_ino],
               'profile': catalog['profile'], 'checkpoint_id': catalog['checkpoint_id'],
               'checkpoint_sha256': catalog['checkpoint_sha256'], 'member_id': member_id,
               'member_sha256': entry['sha256'], 'catalog_sha256': _digest(_canonical(catalog)),
               'catalog_cipher_sha256': _digest(catalog_bytes), 'authority': authority, 'fence': fence}
    return binding, entry


def _same(store: ReviewStore, member_id: str, output_name: str, binding: dict[str, Any], *,
          reading: bool = False) -> None:
    if reading:
        _ancestry(store.root.absolute())
        root_metadata = _ordinary(store.root, directory=True)
        parent_metadata = _ordinary(store.root.parent, directory=True, owner=os.name != 'nt')
        if (_canonical(_authority(store, binding['profile'])) != _canonical(binding['authority'])
            or [root_metadata.st_dev, root_metadata.st_ino] != binding['root_identity']
            or [parent_metadata.st_dev, parent_metadata.st_ino] != binding['parent_identity']
                or _fence(store.root.absolute()) != binding['fence']
                or _digest(_read(store.root / 'catalog.ae1', 8 * 1024 * 1024))
                != binding['catalog_cipher_sha256']):
            raise ReviewBlocked('review authority or ciphertext changed during read')
        _output(store, output_name)
        return
    current, _ = _binding(store, member_id, output_name)
    if _canonical(current) != _canonical(binding):
        raise ReviewBlocked('review authority or encrypted quarantine changed')


@contextmanager
def _locked(store: ReviewStore) -> Iterator[None]:
    try:
        _ancestry(store.root.absolute())
        with session_lock(store.root.parent / '.quarantine-clean-file-review.lock'):
            yield
    except ReviewBlocked:
        raise
    except Exception:
        raise ReviewBlocked('quarantine review unavailable or unsafe') from None


def preview(store: ReviewStore, member_id: str, output_name: str, *, now: datetime) -> dict[str, Any]:
    """Return metadata and an AE1 token; expires is Unix microseconds, valid for <15 minutes."""
    with _locked(store):
        issued = _clock(now)
        binding, entry = _binding(store, member_id, output_name)
        payload = {'binding': binding, 'issued': issued, 'expires': issued + _TTL_MICROSECONDS}
        encrypted, _ = store.cipher.encrypt(_canonical(payload), aad=_AAD)
        _same(store, member_id, output_name, binding)
        return {'review_digest': base64.b64encode(encrypted).decode('ascii'),
                'expires': issued + _TTL_MICROSECONDS, 'clock_unit': 'unix-microseconds',
                'size': entry['size'], 'sha256': entry['sha256'], 'max_export_bytes': MAX_EXPORT_BYTES,
                'output': binding['output'], 'encrypted': True, 'plaintext_written': False,
                'attestation_required': 'human-review-not-lineage-proof'}


def _content(data: bytes | bytearray, suffix: str) -> None:
    if _SENSITIVE_CONTENT.search(data) or data.startswith(b'SQLite format 3\x00'):
        raise ReviewBlocked('content classification holds this document encrypted')
    if suffix == '.pdf':
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:
            raise ReviewBlocked('invalid PDF document')
        if re.search(rb'/Filter|/ObjStm|/Metadata|/[A-Za-z0-9]*#', data, re.I):
            raise ReviewBlocked('opaque PDF content cannot be classified for clean export')
    else:
        try:
            text = data.decode('utf-8')
        except UnicodeError:
            raise ReviewBlocked('text export must be UTF-8') from None
        if any(ord(character) < 32 and character not in '\n\r\t' for character in text):
            raise ReviewBlocked('binary content is not a text document')
        if text.startswith('#!') or re.search(r'(?m)^\s*(?:<\?php|import \w|from \w+ import |def \w+\(|'
                                              r'function\s+\w+\(|(?:[=+@]|-\w).*,)', text):
            raise ReviewBlocked('executable content is not a clean document')
        if suffix == '.csv':
            for row in csv.reader(io.StringIO(text), strict=True):
                if any(cell.lstrip().startswith(('=', '+', '-', '@')) for cell in row):
                    raise ReviewBlocked('spreadsheet formula cells are not inert exports')


def _sync_directory(descriptor: int | WindowsQuarantineDirectory) -> None:
    if isinstance(descriptor, WindowsQuarantineDirectory):
        descriptor.flush()
    else:
        os.fsync(descriptor)


def _publish(store: ReviewStore, member_id: str, output_name: str, binding: dict[str, Any],
             data: bytes) -> None:
    output = Path(binding['output'])
    directory = output.parent
    _same(store, member_id, output_name, binding)
    if os.name == 'nt':
        _publish_windows(store, member_id, output_name, binding, data, directory, output)
        return
    _publish_posix(store, member_id, output_name, binding, data, directory, output)


def _publish_posix(store: ReviewStore, member_id: str, output_name: str, binding: dict[str, Any],
                   data: bytes, directory: Path, output: Path) -> None:
    _same(store, member_id, output_name, binding)
    directory.mkdir(mode=0o700, exist_ok=True)
    _ancestry(directory)
    if os.name == 'nt' or os.open not in os.supports_dir_fd or os.link not in os.supports_dir_fd:
        raise ReviewBlocked('private descriptor-relative publication is unavailable on this host')
    descriptor = _directory(directory)
    temporary = '.private-inactive-export-temporary-' + secrets.token_hex(16) + '.ae1'
    owned: tuple[int, int] | None = None
    published = False
    try:
        _same(store, member_id, output_name, binding)
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _posix_flag('O_NOFOLLOW'),
                         0o600, dir_fd=descriptor)
        with os.fdopen(handle, 'wb') as stream:
            metadata = os.fstat(stream.fileno())
            owned = (metadata.st_dev, metadata.st_ino)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _same(store, member_id, output_name, binding)
        metadata = os.fstat(descriptor)
        current = _ordinary(directory, directory=True)
        if (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
            raise ReviewBlocked('export directory changed')
        os.link(temporary, output.name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        published = True
        os.unlink(temporary, dir_fd=descriptor)
        _sync_directory(descriptor)
        authority = _authority(store, binding['profile'])
        if _canonical(authority) != _canonical(binding['authority']):
            raise ReviewBlocked('privacy changed at publication')
        metadata = os.stat(output.name, dir_fd=descriptor, follow_symlinks=False)
        if ((metadata.st_dev, metadata.st_ino) != owned or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ReviewBlocked('published export changed')
        if _digest(_read(output, len(data))) != _digest(data):
            raise ReviewBlocked('published encrypted export does not match the reviewed envelope')
        current_binding, _ = _binding_published(store, member_id, output_name)
        if _canonical(current_binding) != _canonical(binding):
            raise ReviewBlocked('quarantine changed at publication')
    except BaseException:
        if published:
            _unlink_owned(descriptor, output.name, owned)
            _sync_directory(descriptor)
        raise
    finally:
        _unlink_owned(descriptor, temporary, owned)
        os.close(descriptor)


def _publish_windows(store: ReviewStore, member_id: str, output_name: str, binding: dict[str, Any],
                     data: bytes, directory: Path, output: Path) -> None:
    try:
        try:
            exports = WindowsQuarantineDirectory.create(directory, lock_file=False)
        except QuarantineFilesystemError:
            exports = WindowsQuarantineDirectory.open(directory)
        with exports:
            _ancestry(directory)
            temporary = '.private-inactive-export-temporary-' + secrets.token_hex(16) + '.ae1'
            owned: FileIdentity | None = None
            published = False
            try:
                _same(store, member_id, output_name, binding)
                with exports.create_file(temporary) as staged:
                    owned = staged.identity
                    staged.write_all(data)
                    staged.flush()
                _same(store, member_id, output_name, binding)
                current = _ordinary(directory, directory=True)
                if (current.st_dev, current.st_ino) != (
                        exports.identity.volume_serial, int.from_bytes(exports.identity.file_id, 'big')):
                    raise ReviewBlocked('export directory changed')
                exports.publish_write_once(temporary, output.name, expected_identity=owned)
                published = True
                _sync_directory(exports)
                authority = _authority(store, binding['profile'])
                if _canonical(authority) != _canonical(binding['authority']):
                    raise ReviewBlocked('privacy changed at publication')
                with exports.open_file(output.name) as final:
                    if final.identity != owned:
                        raise ReviewBlocked('published export changed')
                if _digest(_read(output, len(data))) != _digest(data):
                    raise ReviewBlocked('published encrypted export does not match the reviewed envelope')
                current_binding, _ = _binding_published(store, member_id, output_name)
                if _canonical(current_binding) != _canonical(binding):
                    raise ReviewBlocked('quarantine changed at publication')
            except BaseException:
                if published and owned is not None:
                    with suppress(QuarantineFilesystemError):
                        exports.delete_file(output.name, expected_identity=owned)
                        _sync_directory(exports)
                raise
            finally:
                if not published and owned is not None:
                    with suppress(QuarantineFilesystemError):
                        exports.delete_file(temporary, expected_identity=owned)
    except ReviewBlocked:
        raise
    except QuarantineFilesystemError:
        raise ReviewBlocked('private encrypted export publication was refused') from None


def _unlink_owned(descriptor: int, name: str, owned: tuple[int, int] | None) -> None:
    try:
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if (metadata.st_dev, metadata.st_ino) == owned:
            os.unlink(name, dir_fd=descriptor)
    except FileNotFoundError:
        pass


def _binding_published(store: ReviewStore, member_id: str, output_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _binding(store, member_id, output_name, _published=True)


def _verify_review(store: ReviewStore, member_id: str, output_name: str, review_digest: str, *,
                   now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    instant = _clock(now)
    if not isinstance(review_digest, str) or len(review_digest) > 131072:
        raise ReviewBlocked('invalid review token')
    try:
        payload = json.loads(store.cipher.decrypt(base64.b64decode(review_digest, validate=True), aad=_AAD))
    except Exception:
        raise ReviewBlocked('invalid authenticated review token') from None
    if (not isinstance(payload, dict) or set(payload) != {'binding', 'issued', 'expires'}
            or type(payload['issued']) is not int or type(payload['expires']) is not int
            or payload['expires'] - payload['issued'] != _TTL_MICROSECONDS
            or not payload['issued'] <= instant < payload['expires']):
        raise ReviewBlocked('future, expired or malformed review token')
    binding, entry = _binding(store, member_id, output_name)
    if _canonical(payload['binding']) != _canonical(binding):
        raise ReviewBlocked('review does not match current authority and requested document')
    return binding, entry


def _verified_content(store: ReviewStore, member_id: str, output_name: str,
                      binding: dict[str, Any], entry: dict[str, Any]) -> bytearray:
    content = bytearray()
    for chunk in store.iter_member(member_id):
        _same(store, member_id, output_name, binding, reading=True)
        if not isinstance(chunk, bytes) or len(chunk) > entry['size'] - len(content):
            raise ReviewBlocked('member exceeds its authenticated size or export limit')
        content.extend(chunk)
        _same(store, member_id, output_name, binding, reading=True)
    _same(store, member_id, output_name, binding)
    if len(content) != entry['size'] or _digest(content) != entry['sha256']:
        raise ReviewBlocked('member does not match its original size and hash')
    _content(content, Path(output_name).suffix)
    return content


def view(store: ReviewStore, member_id: str, output_name: str, review_digest: str, *,
         now: datetime) -> dict[str, Any]:
    """Explicit human review only: never invoke automatically or record a disposition.

    Return exact UTF-8 source, not verified prose or instructions. The caller must
    print JSON with literal escapes to transient untrusted stdout, never render
    Markdown/HTML, load images/URLs, execute source, or persist content/receipts.
    Store authority enforces current recovery-view/export grants for protected
    subjects. Viewing grants no clean attestation; release still requires one.
    Only the shared lock's technical metadata may be written, never plaintext.
    """
    with _locked(store):
        binding, entry = _verify_review(store, member_id, output_name, review_digest, now=now)
        if Path(output_name).suffix not in {'.md', '.txt', '.csv'}:
            raise ReviewBlocked('view requires a UTF-8 text document')
        if entry['size'] > MAX_VIEW_BYTES:
            raise ReviewBlocked('view exceeds its bounded size')
        content = _verified_content(store, member_id, output_name, binding, entry)
        text = content.decode('utf-8')
        _same(store, member_id, output_name, binding)
        return {'member_id': member_id, 'sha256': entry['sha256'], 'untrusted': True,
                'view_only': True, 'plaintext_written': False, 'format': 'untrusted_text', 'text': text}


def release(store: ReviewStore, member_id: str, output_name: str, review_digest: str, *,
            attest_clean: bool, now: datetime) -> dict[str, Any]:
    """Release one AE1 document on exact owner attestation; never write plaintext."""
    with _locked(store):
        if attest_clean is not True:
            raise ReviewBlocked('explicit owner clean-content attestation required')
        binding, entry = _verify_review(store, member_id, output_name, review_digest, now=now)
        content = _verified_content(store, member_id, output_name, binding, entry)
        aad = export_aad(binding, review_digest)
        payload = _canonical({'schema': 'artha.quarantine-export.v2', 'binding': binding,
                              'review_sha256': _digest(review_digest.encode('ascii')),
                              'metadata': {'attestation': 'human-review-not-lineage-proof'},
                              'sha256': entry['sha256'], 'size': len(content),
                              'data_base64': base64.b64encode(content).decode('ascii')})
        if len(payload) > MAX_EXPORT_JSON_BYTES:
            raise ReviewBlocked('export metadata exceeds its bounded size')
        encrypted, _ = store.cipher.encrypt(payload, aad=aad)
        _publish(store, member_id, output_name, binding, encrypted)
        authority = binding['authority']
        receipt = {'schema': 'artha.quarantine-export-receipt.v2',
                   'review_sha256': _digest(review_digest.encode()),
                   'checkpoint_id': binding['checkpoint_id'], 'checkpoint_sha256': binding['checkpoint_sha256'],
                   'member_sha256': entry['sha256'], 'authority_sha256': _digest(_canonical(authority)),
                   'attestation': 'human-review-not-lineage-proof', 'inactive': True, 'size': len(content),
                   'output': binding['output'], 'encrypted': True, 'plaintext_written': False,
                   'export_sha256': _digest(encrypted), 'export_aad_sha256': _digest(aad)}
        if binding['profile'] == 'work':
            receipt.update(work_generation=authority['work']['generation'],
                           work_head_sha256=authority['work']['head_sha256'])
        else:
            receipt['permission_versions'] = {
                namespace: head['permission_generation'] for namespace, head in authority['privacy'].items()
            }
        return receipt


def _export_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ReviewBlocked('duplicate export metadata field')
        result[name] = value
    return result


def read_export(store: ReviewStore, output_name: str, *, now: datetime) -> dict[str, Any]:
    """Read inert bytes under current owner/privacy authority, without an approval token.

    Fixed v2 AAD permits cipher-level recovery from the single file even if its
    catalog is lost. That portability is not application authorization: this
    reader still requires the current store, exact binding and current grants.
    The retained human attestation is not lineage proof, a new approval, or an
    import instruction. Callers must not serialize returned bytes by default.
    """
    with _locked(store):
        _clock(now)
        output = _output(store, output_name, published=True)
        catalog = store.load_catalog()
        if not isinstance(catalog, dict):
            raise ReviewBlocked('invalid quarantine catalog')
        profile = catalog.get('profile')
        if not isinstance(profile, str):
            raise ReviewBlocked('invalid quarantine profile')
        _authority(store, profile)
        if os.name != 'nt' and stat.S_IMODE(_ordinary(output).st_mode) != 0o600:
            raise ReviewBlocked('export file must be owner-private')
        encrypted = _read(output, MAX_EXPORT_JSON_BYTES + _EXPORT_OVERHEAD)
        raw = store.cipher.decrypt(encrypted, aad=_EXPORT_AAD)
        if len(raw) > MAX_EXPORT_JSON_BYTES:
            raise ReviewBlocked('export metadata exceeds its bounded size')
        try:
            payload = json.loads(raw, object_pairs_hook=_export_object)
        except (ValueError, UnicodeError, RecursionError):
            raise ReviewBlocked('invalid authenticated export metadata') from None
        fields = {'schema', 'binding', 'review_sha256', 'metadata', 'sha256', 'size', 'data_base64'}
        if (not isinstance(payload, dict) or set(payload) != fields
                or payload['schema'] != 'artha.quarantine-export.v2'
                or not isinstance(payload['binding'], dict)
                or payload['metadata'] != {'attestation': 'human-review-not-lineage-proof'}
                or not isinstance(payload['review_sha256'], str)
                or not _HASH.fullmatch(payload['review_sha256'])
                or type(payload['size']) is not int or not 0 <= payload['size'] <= MAX_EXPORT_BYTES
                or not isinstance(payload['sha256'], str) or not _HASH.fullmatch(payload['sha256'])
                or not isinstance(payload['data_base64'], str)
                or len(payload['data_base64']) != 4 * ((payload['size'] + 2) // 3)):
            raise ReviewBlocked('invalid authenticated export schema')
        member_id = payload['binding'].get('member_id')
        if not isinstance(member_id, str):
            raise ReviewBlocked('invalid export member identity')
        binding, entry = _binding(store, member_id, output_name, _published=True)
        if (_canonical(payload['binding']) != _canonical(binding)
                or payload['sha256'] != entry['sha256'] or payload['size'] != entry['size']):
            raise ReviewBlocked('export does not match current quarantine and authority')
        try:
            content = base64.b64decode(payload['data_base64'], validate=True)
        except (ValueError, UnicodeError):
            raise ReviewBlocked('invalid export document encoding') from None
        if (len(content) != payload['size'] or _digest(content) != payload['sha256']
                or base64.b64encode(content).decode('ascii') != payload['data_base64']):
            raise ReviewBlocked('export does not match original document size and hash')
        if (_read(output, MAX_EXPORT_JSON_BYTES + _EXPORT_OVERHEAD) != encrypted
                or (os.name != 'nt' and stat.S_IMODE(_ordinary(output).st_mode) != 0o600)):
            raise ReviewBlocked('export or current authority changed during read')
        current, _ = _binding(store, binding['member_id'], output_name, _published=True)
        if _canonical(current) != _canonical(binding):
            raise ReviewBlocked('export or current authority changed during read')
        return {'data': content, 'sha256': payload['sha256'], 'size': payload['size'],
                'member_id': binding['member_id'], 'review_sha256': payload['review_sha256'],
                'attestation': payload['metadata']['attestation'], 'inactive': True,
                'encrypted': True, 'plaintext_written': False}
