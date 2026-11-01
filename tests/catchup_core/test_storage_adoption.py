"""Synthetic native storage adoption; never use the installed runtime root."""
from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import subprocess
import struct
import sys
from pathlib import Path

import pytest

from catchup_core import checkpoint_cli
from catchup_core import quarantine_fs_windows as fs
from catchup_core import storage_adoption as adoption

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native Windows storage adoption')


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    root = tmp_path / 'legacy'
    root.mkdir()
    (root / 'installation.lock').write_bytes(b'SYNTHETIC-LOCK')
    (root / 'payload').mkdir()
    (root / 'payload' / 'key.ae1').write_bytes(b'SYNTHETIC-KEY-CIPHERTEXT')
    (root / 'payload' / 'inventory.json').write_bytes(b'SYNTHETIC-INVENTORY')
    with fs.WindowsQuarantineDirectory.create(root / 'private-child', lock_file=False):
        pass
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(root))
    return root


def security(path):
    native = fs._native()
    _, handles, _ = fs._open_directory_chain(
        path if path.is_dir() else path.parent, require_private_leaf=False)
    try:
        handle = handles[-1]
        if path.is_file():
            handle = native.create_or_open(
                handle, path.name, directory=False, create=False, access=native.READ_CONTROL)
            handles.append(handle)
        return native.security_bytes(handle)[0]
    finally:
        for handle in reversed(handles):
            native.close(handle)


def inventory(root):
    return {str(path.relative_to(root)): (
        security(path), hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    ) for path in root.rglob('*') if path.name != 'installation.lock'}


def ownership(descriptor):
    offsets = struct.unpack_from('<LL', descriptor, 4)
    return tuple(descriptor[offset:offset + 8 + 4 * descriptor[offset + 1]]
                 if offset else b'' for offset in offsets)


def test_native_adoption_changes_only_two_dacls_and_preserves_every_descendant(legacy):
    before = inventory(legacy)
    lock_bytes = (legacy / 'installation.lock').read_bytes()
    owners = ownership(security(legacy)), ownership(security(legacy / 'installation.lock'))
    preview = adoption.preview()
    assert preview['state'] == 'legacy'
    assert preview == adoption.preview()
    with adoption._opened(apply=True) as (native, _, root, lock, path):
        assert adoption._preview(native, root, lock, path) == preview
    result = adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    assert result == {'state': 'private', 'changed': ['root', 'installation.lock'],
                      'inventory_enrolled': False, 'writers_stopped_attested': True,
                      'writer_exclusion': 'cooperating-installation-lock-only'}
    assert inventory(legacy) == before
    assert owners == (ownership(security(legacy)), ownership(security(legacy / 'installation.lock')))
    assert (legacy / 'installation.lock').read_bytes() == lock_bytes
    assert not (legacy / 'recovery-registry').exists()
    assert adoption.preview()['state'] == 'private'


def test_native_dacl_effect_does_not_propagate_to_descendants(legacy):
    before = inventory(legacy)
    with adoption._opened(apply=True) as (native, _, root, lock, path):
        lock_before = adoption._object(native, lock, directory=False)
        query = native.advapi32.GetKernelObjectSecurity
        query.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                          ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        query.restype = ctypes.c_int
        def direct():
            length = ctypes.c_ulong()
            query(lock, 7, None, 0, ctypes.byref(length))
            buffer = ctypes.create_string_buffer(length.value)
            assert query(lock, 7, buffer, length.value, ctypes.byref(length))
            return buffer.raw
        kernel_before = direct()
        native.set_private_dacl(root)
        lock_after = adoption._object(native, lock, directory=False)
        kernel_after = direct()
        assert kernel_before == kernel_after
        assert lock_after == lock_before
    assert inventory(legacy) == before


def test_private_storage_is_verified_noop_and_old_preview_is_stale(legacy):
    old = adoption.preview()
    adoption.adopt(old['sha256'], reviewed=True, writers_stopped=True)
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.adopt(old['sha256'], reviewed=True, writers_stopped=True)
    current = adoption.preview()
    before = security(legacy), security(legacy / 'installation.lock')
    assert adoption.adopt(current['sha256'], reviewed=True, writers_stopped=True)['changed'] == []
    assert before == (security(legacy), security(legacy / 'installation.lock'))


@pytest.mark.parametrize(('reviewed', 'stopped'), [(False, False), (False, True), (True, False)])
def test_attestations_required_before_any_native_effect(legacy, reviewed, stopped):
    before = security(legacy), security(legacy / 'installation.lock')
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.adopt(adoption.preview()['sha256'], reviewed=reviewed, writers_stopped=stopped)
    assert before == (security(legacy), security(legacy / 'installation.lock'))


@pytest.mark.parametrize('missing', ['root', 'lock'])
def test_missing_storage_refuses_without_creating_anything(legacy, missing):
    if missing == 'lock':
        (legacy / 'installation.lock').unlink()
    else:
        legacy.rename(legacy.with_name('away'))
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    assert not legacy.exists() if missing == 'root' else not (legacy / 'installation.lock').exists()


def test_changed_lock_identity_and_security_make_preview_stale(legacy):
    before = adoption.preview()
    lock = legacy / 'installation.lock'
    lock.rename(legacy / 'old-lock')
    lock.write_bytes(b'SYNTHETIC-REPLACEMENT')
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.adopt(before['sha256'], reviewed=True, writers_stopped=True)
    assert adoption.preview()['state'] == 'legacy'


def test_hardlinked_lock_refuses_without_mutation(legacy):
    before = security(legacy)
    os.link(legacy / 'installation.lock', legacy / 'other-lock')
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    assert security(legacy) == before


def test_cli_is_explicit_bounded_and_never_opens_a_backend(legacy):
    class Forbidden:
        def __getattr__(self, name):
            pytest.fail('storage operation accessed a secret backend')

    out, err = io.StringIO(), io.StringIO()
    assert checkpoint_cli.run(['storage-preview'], out, err, backend=Forbidden()) == 0
    preview = json.loads(out.getvalue())
    out = io.StringIO()
    assert checkpoint_cli.run(
        ['storage-adopt', '--sha256', preview['sha256'], '--reviewed', '--writers-stopped'],
        out, err, backend=Forbidden()) == 0
    assert json.loads(out.getvalue())['inventory_enrolled'] is False
    out = io.StringIO()
    assert checkpoint_cli.run(
        ['storage-adopt', '--sha256', preview['sha256']], out, err, backend=Forbidden()) == 20
    assert out.getvalue() == ''


def test_cli_rejects_arbitrary_path_arguments(legacy):
    assert checkpoint_cli.run(
        ['storage-preview', '--path', str(legacy)], io.StringIO(), io.StringIO()) == 30


def _process(script, *arguments):
    bootstrap = f"import sys; sys.path.insert(0, {str(Path(__file__).parents[2] / 'scripts')!r})\n"
    return subprocess.Popen(
        [sys.executable, '-c', bootstrap + script, *map(str, arguments)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )


def _stop(process):
    if process.poll() is None:
        process.kill()
    _, errors = process.communicate(timeout=20)
    return errors


def test_real_existing_installation_writer_blocks_exclusive_adoption(legacy):
    preview = adoption.preview()
    before = security(legacy), security(legacy / 'installation.lock')
    child = _process("""
from pathlib import Path
from catchup_core.session_lock import session_lock
with session_lock(Path(sys.argv[1]) / 'installation.lock', shared=True):
    print('held', flush=True)
    sys.stdin.readline()
""", legacy)
    try:
        assert child.stdout.readline().strip() == 'held'
        with pytest.raises(adoption.StorageAdoptionError):
            adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
        assert before == (security(legacy), security(legacy / 'installation.lock'))
    finally:
        _stop(child)
    assert adoption.adopt(adoption.preview()['sha256'], reviewed=True, writers_stopped=True)['state'] == 'private'


@pytest.mark.parametrize(('phase', 'state'), [
    ('before', 'legacy'), ('between', 'partial'), ('after', 'private'), ('ack', 'private'),
])
def test_real_process_death_exposes_partial_state_and_requires_fresh_review(legacy, phase, state):
    before = inventory(legacy)
    preview = adoption.preview()
    child = _process("""
from catchup_core import storage_adoption as adoption
from catchup_core import quarantine_fs_windows as fs
phase, digest = sys.argv[1:]
original_effect = fs._Native.set_private_dacl
effects = 0
def pause():
    print('paused', flush=True)
    sys.stdin.readline()
def effect(self, handle):
    global effects
    if phase == 'before' and effects == 0:
        pause()
    original_effect(self, handle)
    effects += 1
    if (phase == 'between' and effects == 1) or (phase == 'after' and effects == 2):
        pause()
fs._Native.set_private_dacl = effect
original_preview = adoption._preview
previews = 0
def inspected(*args):
    global previews
    result = original_preview(*args)
    previews += 1
    if phase == 'ack' and previews == 2:
        pause()
    return result
adoption._preview = inspected
adoption.adopt(digest, reviewed=True, writers_stopped=True)
print('acknowledged', flush=True)
""", phase, preview['sha256'])
    try:
        assert child.stdout.readline().strip() == 'paused'
        with pytest.raises(adoption.StorageAdoptionError):
            adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    finally:
        assert 'Traceback' not in _stop(child)
    fresh = adoption.preview()
    assert fresh['state'] == state
    assert inventory(legacy) == before
    if state != 'legacy':
        with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
            adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    result = adoption.adopt(fresh['sha256'], reviewed=True, writers_stopped=True)
    assert result['changed'] == (
        ['root', 'installation.lock'] if state == 'legacy' else
        ['installation.lock'] if state == 'partial' else [])


def test_native_security_change_invalidates_review(legacy):
    preview = adoption.preview()
    with adoption._opened(apply=True) as (native, _, root, _, _):
        native.set_private_dacl(root)
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)


def test_reparse_ancestor_refuses_without_effect(legacy, tmp_path, monkeypatch):
    junction = tmp_path / 'junction'
    result = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(junction), str(legacy)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(junction))
    before = security(legacy)
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    assert security(legacy) == before


def test_restored_marker_refuses_without_effect(legacy):
    (legacy / '.artha-restored.json').write_bytes(b'SYNTHETIC')
    before = security(legacy)
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    assert security(legacy) == before


@pytest.mark.parametrize('bad', ['', 'f' * 63, 'F' * 64, None, 1, True])
def test_invalid_digest_refuses_before_native_access(monkeypatch, bad):
    monkeypatch.setattr(adoption, '_opened', lambda **kwargs: pytest.fail('opened native storage'))
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.adopt(bad, reviewed=True, writers_stopped=True)


def test_current_owner_does_not_imply_write_dac_authority(legacy):
    from test_quarantine_fs_windows import _set_test_dacl

    _set_test_dacl(legacy / 'installation.lock', 'D:P(A;;0x001b01ff;;;OW)(A;;FA;;;SY)')
    before = security(legacy), security(legacy / 'installation.lock')
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    assert before == (security(legacy), security(legacy / 'installation.lock'))


def test_native_owner_comparison_refuses_mismatched_authenticated_principal(legacy, monkeypatch):
    native = fs._native()
    pointer = native._sid_from_string('S-1-5-18')
    try:
        sid = ctypes.string_at(pointer, native.advapi32.GetLengthSid(pointer))
    finally:
        native.kernel32.LocalFree(pointer)
    before = security(legacy)
    monkeypatch.setattr(native, 'current_user_sid', lambda: ('S-1-5-18', sid))
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    assert security(legacy) == before


def test_failed_second_native_effect_stays_partial_without_rollback(legacy, monkeypatch):
    before = inventory(legacy)
    preview = adoption.preview()
    original = fs._Native.set_private_dacl
    effects = []

    def fail_second(native, handle):
        effects.append(handle)
        if len(effects) == 2:
            raise fs.QuarantineFilesystemError('synthetic native effect failure')
        original(native, handle)

    with monkeypatch.context() as patch:
        patch.setattr(fs._Native, 'set_private_dacl', fail_second)
        with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
            adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    assert adoption.preview()['state'] == 'partial'
    assert inventory(legacy) == before
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)


def test_native_api_success_without_effect_fails_strict_readback(legacy, monkeypatch):
    before = security(legacy), security(legacy / 'installation.lock')
    preview = adoption.preview()
    monkeypatch.setattr(fs._Native, 'set_private_dacl', lambda *args: None)
    with pytest.raises(adoption.StorageAdoptionError, match='readback'):
        adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    assert before == (security(legacy), security(legacy / 'installation.lock'))


def test_native_metadata_failure_refuses_without_effect(legacy, monkeypatch):
    before = security(legacy), security(legacy / 'installation.lock')
    preview = adoption.preview()
    def fail(*args):
        raise fs.QuarantineFilesystemError('synthetic unavailable metadata')
    with monkeypatch.context() as patch:
        patch.setattr(fs._Native, 'security_bytes', fail)
        with pytest.raises(adoption.StorageAdoptionError):
            adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    assert before == (security(legacy), security(legacy / 'installation.lock'))


def test_preview_and_adoption_never_read_or_write_payload_streams(legacy, monkeypatch):
    native = fs._native()
    before = inventory(legacy)
    def forbidden(*args, **kwargs):
        pytest.fail('payload stream access')
    with monkeypatch.context() as patch:
        patch.setattr(native.kernel32, 'ReadFile', forbidden)
        patch.setattr(native.kernel32, 'WriteFile', forbidden)
        patch.setattr(Path, 'read_bytes', forbidden)
        patch.setattr(Path, 'write_bytes', forbidden)
        preview = adoption.preview()
        adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    assert inventory(legacy) == before


def test_nonwindows_refuses_without_attempting_native_access(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(adoption, 'sys', SimpleNamespace(platform='linux'))
    with pytest.raises(adoption.StorageAdoptionError, match='native Windows'):
        adoption.preview()


def test_volume_root_refuses_before_native_access(legacy, monkeypatch):
    monkeypatch.setattr(adoption, 'default_installation_dir', lambda: Path(legacy.anchor))
    with pytest.raises(adoption.StorageAdoptionError, match='volume root'):
        adoption.preview()


@pytest.mark.parametrize('prior_instance_patch', [False, True])
def test_unrecognized_native_security_failure_is_not_legacy_eligibility(
        legacy, monkeypatch, prior_instance_patch):
    native = fs._native()
    if prior_instance_patch:
        original = native.owner_private_check
        with monkeypatch.context() as prior:
            prior.setattr(native, 'owner_private_check', lambda *args, **kwargs: None)
        assert native.__dict__['owner_private_check'].__self__ is native
        assert native.owner_private_check == original
    calls = []
    failure = fs.QuarantineFilesystemError('synthetic native security query failure')
    def fail(*args, **kwargs):
        calls.append(True)
        raise failure
    monkeypatch.setattr(native, 'owner_private_check', fail)
    assert fs._native() is native
    assert native.owner_private_check is fail
    with pytest.raises(adoption.StorageAdoptionError) as refused:
        adoption.preview()
    assert calls == [True]
    assert refused.value.__cause__ is failure


def test_real_security_change_between_metadata_reads_refuses(legacy, monkeypatch):
    original = fs._Native.security_bytes
    reads = []
    def raced(native, handle):
        reads.append(handle)
        if len(reads) == 2:
            native.set_private_dacl(handle)
        return original(native, handle)
    monkeypatch.setattr(fs._Native, 'security_bytes', raced)
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.preview()


def test_changed_security_before_first_effect_requires_fresh_review(legacy, monkeypatch):
    preview = adoption.preview()
    original = adoption._object
    calls = []
    def raced(native, handle, **kwargs):
        calls.append(handle)
        if len(calls) == 3:
            native.set_private_dacl(handle)
        return original(native, handle, **kwargs)
    monkeypatch.setattr(adoption, '_object', raced)
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)


def test_unmediated_directory_change_before_acknowledgement_is_not_success(legacy, monkeypatch):
    preview = adoption.preview()
    original = adoption._preview
    calls = []
    def raced(*args):
        calls.append(True)
        if len(calls) == 2:
            (legacy / 'synthetic-unmediated-change').write_bytes(b'SYNTHETIC')
        return original(*args)
    monkeypatch.setattr(adoption, '_preview', raced)
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)


@pytest.mark.parametrize('kind', [
    'repository', 'repository-child', 'contains-repository', 'home', 'onedrive',
    'onedrive-child', 'dropbox', 'cloudstorage', 'mobile-documents',
    'configured-sync', 'configured-sync-child', 'contains-configured-sync',
])
def test_configured_root_policy_refuses_broad_repository_and_sync_roots_before_open(
        legacy, tmp_path, monkeypatch, kind):
    repository = Path(adoption.__file__).resolve().parents[2]
    home = tmp_path / 'synthetic-home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    synced = tmp_path / 'configured-cloud' / 'synced'
    monkeypatch.setenv('OneDriveCommercial', str(synced))
    targets = {
        'repository': repository, 'repository-child': repository / 'runtime',
        'contains-repository': repository.parent, 'home': home,
        'onedrive': home / 'OneDrive', 'onedrive-child': home / 'OneDrive' / 'runtime',
        'dropbox': home / 'Dropbox', 'cloudstorage': home / 'Library' / 'CloudStorage',
        'mobile-documents': home / 'Library' / 'Mobile Documents',
        'configured-sync': synced, 'configured-sync-child': synced / 'runtime',
        'contains-configured-sync': synced.parent,
    }
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(targets[kind]))
    monkeypatch.setattr(fs, '_open_directory_chain',
                        lambda *args, **kwargs: pytest.fail('unsafe root reached native open'))
    with pytest.raises(adoption.StorageAdoptionError, match='machine-local'):
        adoption.preview()
    with pytest.raises(adoption.StorageAdoptionError, match='machine-local'):
        adoption.adopt('f' * 64, reviewed=True, writers_stopped=True)


def test_configured_root_change_cannot_reuse_another_roots_review(legacy, tmp_path, monkeypatch):
    preview = adoption.preview()
    replacement = tmp_path / 'replacement-local'
    replacement.mkdir()
    (replacement / 'installation.lock').write_bytes(b'SYNTHETIC')
    before = security(replacement)
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(replacement))
    with pytest.raises(adoption.StorageAdoptionError, match='fresh'):
        adoption.adopt(preview['sha256'], reviewed=True, writers_stopped=True)
    assert security(replacement) == before


@pytest.mark.parametrize('root', [
    r'\\synthetic-server\synthetic-share\artha',
    r'\\?\C:\synthetic-artha',
    r'\\.\C:\synthetic-artha',
    r'\??\C:\synthetic-artha',
    r'\\?\UNC\synthetic-server\synthetic-share\artha',
])
def test_remote_and_device_namespaces_refuse_before_native_binding(monkeypatch, root):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', root)
    monkeypatch.setattr(fs, '_native', lambda: pytest.fail('native binding reached for a namespace path'))
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.adopt('f' * 64, reviewed=True, writers_stopped=True)


@pytest.mark.parametrize('drive_type', [0, 1, 4, 5])
def test_nonlocal_drive_refuses_before_resolution_or_native_target_open(legacy, monkeypatch, drive_type):
    native = fs._native()
    calls = []
    def classify(drive):
        calls.append(drive)
        return drive_type
    monkeypatch.setattr(native.kernel32, 'GetDriveTypeW', classify)
    monkeypatch.setattr(adoption, '_check_root',
                        lambda *args: pytest.fail('path resolution preceded drive classification'))
    monkeypatch.setattr(fs, '_open_directory_chain',
                        lambda *args, **kwargs: pytest.fail('nonlocal native target was opened'))
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.preview()
    with pytest.raises(adoption.StorageAdoptionError):
        adoption.adopt('f' * 64, reviewed=True, writers_stopped=True)
    assert calls == [legacy.anchor, legacy.anchor]


def _projected_security(path):
    native = fs._native()
    _, handles, _ = fs._open_directory_chain(
        path if path.is_dir() else path.parent, require_private_leaf=False)
    descriptor = None
    try:
        handle = handles[-1]
        if path.is_file():
            handle = native.create_or_open(
                handle, path.name, directory=False, create=False, access=native.READ_CONTROL)
            handles.append(handle)
        descriptor = ctypes.c_void_p()
        assert native.advapi32.GetSecurityInfo(
            handle, native.SE_FILE_OBJECT, 7, None, None, None, None, ctypes.byref(descriptor)) == 0
        length = native.advapi32.GetSecurityDescriptorLength
        length.argtypes = [ctypes.c_void_p]
        length.restype = ctypes.c_ulong
        return ctypes.string_at(descriptor, length(descriptor))
    finally:
        if descriptor is not None:
            native.kernel32.LocalFree(descriptor)
        for handle in reversed(handles):
            native.close(handle)


def _acl_view(raw):
    offset = struct.unpack_from('<L', raw, 16)[0]
    revision = raw[offset]
    count = struct.unpack_from('<H', raw, offset + 4)[0]
    start = offset + 8
    aces = []
    for _ in range(count):
        kind, flags, size = struct.unpack_from('<BBH', raw, start)
        aces.append((kind, flags, raw[start + 4:start + size]))
        start += size
    return ownership(raw), struct.unpack_from('<H', raw, 2)[0], revision, aces


def _maximum_access(raw):
    native = fs._native()
    duplicate = native.advapi32.DuplicateToken
    duplicate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    duplicate.restype = ctypes.c_int
    access = native.advapi32.AccessCheck
    access.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
    ]
    access.restype = ctypes.c_int
    token = ctypes.c_void_p()
    impersonation = ctypes.c_void_p()
    assert native.advapi32.OpenProcessToken(
        native.kernel32.GetCurrentProcess(), 0x8 | 0x2, ctypes.byref(token))
    try:
        assert duplicate(token, 2, ctypes.byref(impersonation))
        try:
            descriptor = ctypes.create_string_buffer(raw)
            mapping = (ctypes.c_ulong * 4)(0x120089, 0x120116, 0x1200a0, 0x1f01ff)
            privileges = ctypes.create_string_buffer(4096)
            length = ctypes.c_ulong(len(privileges))
            granted = ctypes.c_ulong()
            allowed = ctypes.c_int()
            assert access(descriptor, impersonation, 0x02000000, mapping, privileges,
                          ctypes.byref(length), ctypes.byref(granted), ctypes.byref(allowed)), ctypes.get_last_error()
            return bool(allowed.value), granted.value
        finally:
            native.close(impersonation.value)
    finally:
        native.close(token.value)


def test_native_stored_acl_projection_effective_access_and_inheritance_preservation(legacy):
    inherited_before = legacy / 'payload' / 'inheritance-before'
    inherited_before.write_bytes(b'SYNTHETIC')
    paths = sorted(path for path in legacy.rglob('*') if path.name != 'installation.lock')
    stored = {path: security(path) for path in paths}
    projected = {path: _projected_security(path) for path in paths}
    effective = {path: (_maximum_access(stored[path]), _maximum_access(projected[path]))
                 for path in paths}
    adoption.adopt(adoption.preview()['sha256'], reviewed=True, writers_stopped=True)
    differences = []
    for path in paths:
        current_stored = security(path)
        current_projected = _projected_security(path)
        assert current_stored == stored[path]
        before_view = _acl_view(projected[path])
        after_view = _acl_view(current_projected)
        assert before_view[:3] == after_view[:3]  # Owner/group/control/revision unchanged.
        before_aces, after_aces = before_view[3], after_view[3]
        assert len(before_aces) == len(after_aces)
        for before, after in zip(before_aces, after_aces):
            assert before[0] == after[0] and before[2] == after[2]  # Type, SID and mask bytes.
            assert (before[1] ^ after[1]) in (0, 0x10)  # Only INHERITED_ACE presentation.
        now = _maximum_access(current_stored), _maximum_access(current_projected)
        assert now == effective[path]
        differences.append({
            'object': path.relative_to(legacy).as_posix(),
            'stored_equal': True,
            'projected_flags_before': [ace[1] for ace in before_aces],
            'projected_flags_after': [ace[1] for ace in after_aces],
            'access_before_after': now,
        })
    inherited_after = legacy / 'payload' / 'inheritance-after'
    inherited_after.write_bytes(b'SYNTHETIC')
    assert security(inherited_after) == stored[inherited_before]
    print(json.dumps({'native_acl_measurement': differences,
                      'existing_descendant_child_inheritance_equal': True}))
