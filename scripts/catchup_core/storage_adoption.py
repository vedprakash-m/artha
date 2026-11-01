"""Explicit two-object adoption; stopped writers are attested, not host-confined."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from catchup_core import quarantine_fs_windows as fs
from catchup_core.checkpoint import _outside
from catchup_core.session_lock import SessionBusy, default_installation_dir, require_active_installation


class StorageAdoptionError(RuntimeError):
    pass


_LEGACY_DACL = frozenset({
    'owner-private DACL must be explicit and protected',
    'owner-private DACL contains unexpected entries',
    'owner-private DACL is incomplete',
    'owner-private DACL permissions are broader or malformed',
    'owner-private DACL grants an unexpected principal',
})


def _check_root(root: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    user_directory = Path.home()
    protected = [repository, user_directory / 'OneDrive', user_directory / 'Dropbox',
                 user_directory / 'Library/CloudStorage', user_directory / 'Library/Mobile Documents']
    protected.extend(Path(value).expanduser() for name in (
        'OneDrive', 'OneDriveConsumer', 'OneDriveCommercial') if (value := os.environ.get(name)))
    protected.extend(parent for parent in repository.parents
                     if parent.name.casefold().startswith('onedrive') or parent.name == 'CloudStorage')
    if root.resolve() == user_directory.resolve() or not _outside(root, protected):
        raise StorageAdoptionError('storage adoption requires a machine-local root outside repository/sync roots')


@contextmanager
def _opened(*, apply: bool) -> Iterator[tuple[fs._Native, list[int], int, int, str]]:
    if sys.platform != 'win32':
        raise StorageAdoptionError('storage adoption requires native Windows')
    root = default_installation_dir()
    drive, components, normalized = fs._absolute_components(root)
    if not components:
        raise StorageAdoptionError('storage adoption cannot target a volume root')
    fs._native().require_local_storage_drive(drive)
    _check_root(root)
    require_active_installation(root)
    native, handles, _ = fs._open_directory_chain(root, require_private_leaf=False)
    try:
        root_handle = native.create_or_open(
            handles[-2], components[-1], directory=True, create=False,
            access=native.READ_CONTROL | 0x00040000,  # WRITE_DAC, never WRITE_OWNER.
        )
        handles.append(root_handle)
        native.owner_check(root_handle, directory=True)
        lock_handle = native.create_or_open(
            root_handle, 'installation.lock', directory=False, create=False,
            access=(native.GENERIC_READ | native.GENERIC_WRITE | 0x00040000
                    if apply else native.READ_CONTROL | 0x00040000),
            exclusive=apply,
        )
        handles.append(lock_handle)
        native.owner_check(lock_handle, directory=False)
        yield native, handles, root_handle, lock_handle, normalized
    finally:
        for handle in reversed(handles):
            native.close(handle)


def _object(native: fs._Native, handle: int, *, directory: bool) -> dict[str, Any]:
    identity = native.owner_check(handle, directory=directory)
    raw, control = native.security_bytes(handle)
    snapshot = native.snapshot(handle)
    try:
        native.owner_private_check(handle, directory=directory)
    except fs.QuarantineFilesystemError as error:
        if str(error) not in _LEGACY_DACL:
            raise
        private = False
    else:
        private = True
    if native.security_bytes(handle) != (raw, control):
        raise StorageAdoptionError('storage metadata changed; obtain a fresh storage-preview')
    return {
        'volume': identity.volume_serial, 'identity': identity.file_id.hex(),
        'security_sha256': hashlib.sha256(raw).hexdigest(),
        'control': control, 'owner_matches_current_user': True, 'private': private,
        'size': snapshot.size, 'last_write_100ns': snapshot.last_write_100ns,
        'change_100ns': snapshot.change_100ns,
    }


def _preview(native: fs._Native, root: int, lock: int, path: str) -> dict[str, Any]:
    _, owner = native.current_user_sid()
    objects = {'root': _object(native, root, directory=True),
               'installation.lock': _object(native, lock, directory=False)}
    count = sum(value['private'] for value in objects.values())
    result = {
        'schema': 'artha.storage-adoption.v1', 'root': path,
        'current_owner_sha256': hashlib.sha256(owner).hexdigest(),
        'state': 'private' if count == 2 else 'partial' if count else 'legacy',
        'objects': objects, 'eligible': True, 'inventory_enrolled': False,
        'writer_exclusion': 'cooperating-installation-lock-only',
    }
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {**result, 'sha256': digest}


def preview() -> dict[str, Any]:
    """Inspect only the existing configured root and lock; never create either."""
    try:
        with _opened(apply=False) as (native, _, root, lock, path):
            return _preview(native, root, lock, path)
    except (OSError, SessionBusy, fs.QuarantineFilesystemError) as error:
        raise StorageAdoptionError('existing storage is missing, unsafe or unavailable') from error


def adopt(expected_sha256: str, *, reviewed: bool, writers_stopped: bool) -> dict[str, Any]:
    """Apply one exactly reviewed pair of DACLs under exclusive existing-lock access."""
    if reviewed is not True or writers_stopped is not True:
        raise StorageAdoptionError('explicit review and stopped-writer attestation are required')
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise StorageAdoptionError('an exact storage-preview sha256 is required')
    try:
        with _opened(apply=True) as (native, _, root, lock, path):
            before = _preview(native, root, lock, path)
            if before['sha256'] != expected_sha256:
                raise StorageAdoptionError('storage metadata changed; obtain a fresh storage-preview')
            changed = []
            completed = {}
            for name, handle in (('root', root), ('installation.lock', lock)):
                expected = before['objects'][name]
                if _object(native, handle, directory=name == 'root') != expected:
                    raise StorageAdoptionError('storage metadata changed; obtain a fresh storage-preview')
                if not expected['private']:
                    native.set_private_dacl(handle)
                    changed.append(name)
                actual = _object(native, handle, directory=name == 'root')
                if not actual['private'] or any(actual[key] != expected[key] for key in (
                        'volume', 'identity', 'size', 'last_write_100ns')):
                    raise StorageAdoptionError('storage adoption readback failed; obtain a fresh storage-preview')
                completed[name] = actual
            after = _preview(native, root, lock, path)
            if after['state'] != 'private' or after['objects'] != completed:
                raise StorageAdoptionError('storage adoption incomplete; obtain a fresh storage-preview')
            return {'state': 'private', 'changed': changed, 'inventory_enrolled': False,
                    'writers_stopped_attested': True,
                    'writer_exclusion': 'cooperating-installation-lock-only'}
    except (OSError, SessionBusy, fs.QuarantineFilesystemError) as error:
        raise StorageAdoptionError('storage adoption refused or incomplete; obtain a fresh storage-preview') from error
