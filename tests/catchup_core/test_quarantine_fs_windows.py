from __future__ import annotations

import os
import subprocess
import sys
import ctypes
import hashlib
import sqlite3
from ctypes import wintypes
from pathlib import Path

import pytest

# Native assertion-style tests follow the project's pytest conventions.
# ruff: noqa: S101
from catchup_core.recovery_access import RecoveryAccess
from catchup_core import quarantine_review
from catchup_core import quarantine_fs_windows as windows_fs
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.quarantine_fs_windows import (
    QuarantineFilesystemError,
    WindowsQuarantineDirectory,
    open_owner_private_path,
    owner_private_handle_check,
)

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native Windows filesystem contract')


def test_owner_private_directory_and_handle_relative_write_once(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        assert owner_private_handle_check(root.handle) == root.identity
        with root.create_file('stage.ae1') as staged:
            staged.write_all(b'AE1-synthetic-ciphertext')
            staged.flush()
            identity = staged.identity
        root.publish_write_once('stage.ae1', 'catalog.ae1', expected_identity=identity)
        assert root.read_file('catalog.ae1', 1024, expected_identity=identity) == b'AE1-synthetic-ciphertext'
        assert root.list_files() == ['.lock', 'catalog.ae1']
    finally:
        root.close()


def test_owner_private_path_api_checks_handle_and_identity(tmp_path):
    root_path = tmp_path / 'quarantine'
    directory = WindowsQuarantineDirectory.create(root_path)
    try:
        with directory.create_file('record.json') as record:
            record.write_all(b'{}')
            record.flush()
            expected = record.identity
    finally:
        directory.close()

    with open_owner_private_path(root_path / 'record.json') as opened:
        assert owner_private_handle_check(opened.handle) == expected
        assert opened.identity == expected


def test_recovery_access_reads_and_updates_sqlite_through_native_handles(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'recovery')
    database = sqlite3.connect(':memory:')
    database.execute('CREATE TABLE sample (value TEXT)')
    database.execute('INSERT INTO sample VALUES (?)', ('before',))
    original = database.serialize()
    database.close()
    try:
        with root.create_file('ledger.db') as ledger:
            ledger.write_all(original)
            ledger.flush()
        recovery = RecoveryAccess.__new__(RecoveryAccess)
        with recovery._database(
                root.path, root.path / 'ledger.db',
                expected_sha256=hashlib.sha256(original).hexdigest()) as opened:
            assert opened.execute('SELECT value FROM sample').fetchone()['value'] == 'before'
        with recovery._database(root.path, root.path / 'ledger.db', update=True) as opened:
            opened.execute('UPDATE sample SET value=?', ('after',))
            opened.commit()
        updated = sqlite3.connect(':memory:')
        updated.deserialize(root.read_file('ledger.db', 1024 * 1024))
        assert updated.execute('SELECT value FROM sample').fetchone()[0] == 'after'
        updated.close()
    finally:
        root.close()


def test_review_export_publishes_encrypted_data_through_native_backend(tmp_path, monkeypatch):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    root.close()
    exports = tmp_path / 'reviewed-exports'
    output = exports / 'approved.pdf.ae1'
    binding = {'output': str(output), 'profile': 'personal', 'authority': {}}
    store = type('Store', (), {'root': tmp_path / 'quarantine'})()
    encrypted, _ = EnvelopeCipher(StaticKeyProvider({1: b'k' * 32})).encrypt(
        b'SYNTHETIC-REVIEW-EXPORT', aad=b'native-test'
    )
    monkeypatch.setattr(quarantine_review, '_same', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        quarantine_review, '_binding_published', lambda *args, **kwargs: (binding, {})
    )
    monkeypatch.setattr(quarantine_review, '_authority', lambda *args, **kwargs: {})

    quarantine_review._publish_windows(
        store, 'member', 'approved.pdf', binding, encrypted, exports, output
    )

    with WindowsQuarantineDirectory.open(exports) as directory:
        published = directory.read_file(output.name, 1024)
    assert published == encrypted
    assert published.startswith(b'AE1')
    assert b'SYNTHETIC-REVIEW-EXPORT' not in published


def test_publication_never_overwrites_and_pins_file_identity(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        with root.create_file('first.ae1') as first:
            first.write_all(b'AE1-first')
            first.flush()
            first_identity = first.identity
        root.publish_write_once('first.ae1', 'catalog.ae1', expected_identity=first_identity)

        with root.create_file('second.ae1') as second:
            second.write_all(b'AE1-second')
            second.flush()
            second_identity = second.identity
        with pytest.raises(QuarantineFilesystemError):
            root.publish_write_once('second.ae1', 'catalog.ae1', expected_identity=second_identity)
        assert root.read_file('catalog.ae1', 1024, expected_identity=first_identity) == b'AE1-first'
        with pytest.raises(QuarantineFilesystemError):
            root.read_file('catalog.ae1', 1024, expected_identity=second_identity)
    finally:
        root.close()


def test_identity_pinned_delete_and_replace(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        identities = {}
        for name in ('catalog.ae1', 'next.ae1', 'other.ae1'):
            with root.create_file(name) as staged:
                staged.write_all(name.encode())
                staged.flush()
                identities[name] = staged.identity
        with pytest.raises(QuarantineFilesystemError):
            root.replace_file('next.ae1', 'catalog.ae1', expected_identity=identities['next.ae1'],
                              replaced_identity=identities['other.ae1'])
        with pytest.raises(QuarantineFilesystemError):
            root.delete_file('other.ae1', expected_identity=identities['next.ae1'])
        root.replace_file('next.ae1', 'catalog.ae1', expected_identity=identities['next.ae1'],
                          replaced_identity=identities['catalog.ae1'])
        assert root.read_file('catalog.ae1', 64, expected_identity=identities['next.ae1']) == b'next.ae1'
        _set_everyone_readable_dacl(tmp_path / 'quarantine' / 'other.ae1')
        with pytest.raises(QuarantineFilesystemError):
            root.read_file('other.ae1', 64)
        assert root.read_file('other.ae1', 64, private=False) == b'other.ae1'
        root.delete_file('other.ae1', expected_identity=identities['other.ae1'])
        assert root.list_files() == ['.lock', 'catalog.ae1']
    finally:
        root.close()
    with WindowsQuarantineDirectory.create(tmp_path / 'exports', lock_file=False) as exports:
        assert exports.list_files() == []
        exports.flush()


def test_bounded_chunk_reads_stream_from_current_offset(tmp_path):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('blob.ae1') as staged:
            staged.write_all(b'abcdefg')
            staged.flush()
        with root.open_file('blob.ae1') as opened:
            assert [opened.read_chunk(3), opened.read_chunk(3), opened.read_chunk(3), opened.read_chunk(3)] == [
                b'abc', b'def', b'g', b'']
            for size in (0, -1, 65537, 1.5):
                with pytest.raises(QuarantineFilesystemError):
                    opened.read_chunk(size)
        _set_everyone_readable_dacl(tmp_path / 'quarantine' / 'blob.ae1')
        with pytest.raises(QuarantineFilesystemError):
            root.open_file('blob.ae1')
    inherited = tmp_path / 'inherited'
    inherited.mkdir()
    with WindowsQuarantineDirectory.open(inherited, owner_private=False, writable=True) as parent:
        with parent.create_file('stage.ae1') as created:
            created.write_all(b'AE1')
            created.flush()
            staged = created.identity
        parent.publish_write_once('stage.ae1', 'new.ae1', expected_identity=staged)
    with open_owner_private_path(inherited / 'new.ae1') as opened:
        assert opened.read_chunk(65536) == b'AE1'
    with WindowsQuarantineDirectory.open(inherited, owner_private=False) as parent:
        with parent.create_file('read-only-parent.ae1') as created:
            created.write_all(b'AE1')
            created.flush()
            staged = created.identity
        with pytest.raises(QuarantineFilesystemError):
            parent.publish_write_once('read-only-parent.ae1', 'other.ae1', expected_identity=staged)
        assert parent.list_files(validate=False) == ['new.ae1', 'read-only-parent.ae1']


def test_held_handles_pin_file_and_directory_chain_names(tmp_path):
    ancestor = tmp_path / 'ancestor'
    ancestor.mkdir()
    with WindowsQuarantineDirectory.create(ancestor / 'quarantine') as root, root.create_file('chunk.ae1') as staged:
        staged.write_all(b'AE1')
        for source in (ancestor / 'quarantine' / 'chunk.ae1', ancestor / 'quarantine', ancestor):
            with pytest.raises(PermissionError):
                source.rename(source.with_name(source.name + '-moved'))
        with pytest.raises(PermissionError):
            (ancestor / 'quarantine' / 'chunk.ae1').unlink()
        staged.flush()
        assert staged.check_path() == staged.identity
    (ancestor / 'quarantine').rename(tmp_path / 'moved')


def test_reparse_file_and_hardlink_are_refused(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        with root.create_file('content.ae1') as content:
            content.write_all(b'AE1-synthetic')
            content.flush()
        alias = tmp_path / 'outside-link.ae1'
        if not _create_hardlink(alias, root.path / 'content.ae1'):
            pytest.skip('hardlink creation is unavailable on this filesystem')
        with pytest.raises(QuarantineFilesystemError):
            root.read_file('content.ae1', 1024)

        target = tmp_path / 'outside-target.ae1'
        target.write_bytes(b'AE1-not-quarantine')
        try:
            os.symlink(target, root.path / 'linked.ae1')
        except (OSError, NotImplementedError):
            pytest.skip('symlink creation is unavailable on this host')
        with pytest.raises(QuarantineFilesystemError):
            root.read_file('linked.ae1', 1024)
    finally:
        root.close()


def test_wrong_acl_is_refused(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        _set_everyone_readable_dacl(root.path)
        with pytest.raises(QuarantineFilesystemError):
            owner_private_handle_check(root.handle)
    finally:
        root.close()


@pytest.mark.parametrize('name', ['../escape', '..', '.', r'a\\b', 'a/b', 'C:escape', '', 'x.'])
def test_path_components_are_single_safe_names(tmp_path, name):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        with pytest.raises(QuarantineFilesystemError):
            root.create_file(name)
    finally:
        root.close()


def test_directory_symlink_ancestor_is_refused(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'link'
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlink creation is unavailable on this host')
    with pytest.raises(QuarantineFilesystemError):
        WindowsQuarantineDirectory.create(link / 'quarantine')


def test_junction_ancestor_is_refused(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    junction = tmp_path / 'junction'
    result = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )  # noqa: S603
    if result.returncode:
        pytest.skip('junction creation is unavailable on this host')
    with pytest.raises(QuarantineFilesystemError):
        WindowsQuarantineDirectory.create(junction / 'quarantine')


def test_wrong_owner_is_refused_when_owner_change_is_available(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        if not _set_system_owner(root.path):
            pytest.skip('changing an object owner requires privileges unavailable to this user')
        with pytest.raises(QuarantineFilesystemError):
            owner_private_handle_check(root.handle)
    finally:
        root.close()


def test_process_lock_excludes_an_independent_writer(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    script = f"""
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[2] / 'scripts')!r})
from pathlib import Path
from catchup_core.quarantine_fs_windows import QuarantineFilesystemError, WindowsQuarantineDirectory
root = WindowsQuarantineDirectory.open(Path(sys.argv[1]))
try:
    with root.lock():
        raise SystemExit(7)
except QuarantineFilesystemError:
    raise SystemExit(0)
finally:
    root.close()
"""
    try:
        with root.lock():
            result = subprocess.run(
                [sys.executable, '-c', script, str(root.path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )  # noqa: S603
        assert result.returncode == 0, result.stderr
    finally:
        root.close()


def test_file_lock_excludes_an_independent_handle(tmp_path):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        with root.create_file('ledger.db') as first:
            first.write_all(b'synthetic ledger')
            with root.open_file('ledger.db', writable=True) as second:
                with first.lock():
                    with pytest.raises(QuarantineFilesystemError):
                        with second.lock():
                            pass
    finally:
        root.close()


@pytest.mark.parametrize(('method', 'arguments'), [
    ('snapshot', ()), ('read', (16,)), ('read_chunk', (16,)), ('seek', (0,)),
    ('write_all', (b'changed',)), ('truncate', (0,)), ('flush', ()), ('check_path', ()),
])
def test_closed_file_refuses_io_without_changing_persisted_data(tmp_path, method, arguments):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
            identity = opened.identity
        opened.close()
        with pytest.raises(QuarantineFilesystemError, match='native file handle is closed'):
            getattr(opened, method)(*arguments)
        assert root.read_file('record.ae1', 16, expected_identity=identity) == b'original'


@pytest.mark.parametrize(('method', 'arguments'), [
    ('read', (-1,)), ('read', (True,)), ('read', (1.5,)),
    ('seek', (-1,)), ('seek', (True,)), ('seek', (1.5,)),
    ('truncate', (-1,)), ('truncate', (True,)), ('truncate', (1.5,)),
    ('write_all', (bytearray(b'changed'),)),
])
def test_invalid_file_io_preserves_bytes_identity_and_offset(tmp_path, method, arguments):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
            before = opened.snapshot()
            opened.seek(2)
            with pytest.raises(QuarantineFilesystemError, match='invalid|immutable bytes'):
                getattr(opened, method)(*arguments)
            assert opened.read_chunk(2) == b'ig'
            assert opened.snapshot() == before
            assert opened.read(16) == b'original'


def test_file_lock_releases_after_body_exception(tmp_path):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('ledger.db') as first, root.open_file('ledger.db', writable=True) as second:
            with pytest.raises(ValueError, match='synthetic body failure'):
                with first.lock():
                    raise ValueError('synthetic body failure')
            with second.lock():
                second.write_all(b'recovered')
                second.flush()
            assert first.read(16) == b'recovered'
            assert first.check_path(expected_identity=second.identity) == first.identity


def test_directory_lock_created_lazily_and_released_after_body_exception(tmp_path):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine', lock_file=False) as root:
        assert root.list_files() == []
        with pytest.raises(ValueError, match='synthetic body failure'):
            with root.lock():
                raise ValueError('synthetic body failure')
        assert root.list_files() == ['.lock']
        with WindowsQuarantineDirectory.open(root.path) as independent, independent.lock():
            with independent.create_file('recovered.ae1') as opened:
                opened.write_all(b'recovered')
                opened.flush()
        assert root.read_file('recovered.ae1', 16) == b'recovered'


@pytest.mark.parametrize('operation', ['read', 'write'])
def test_zero_progress_os_io_is_refused_and_handle_remains_usable(tmp_path, monkeypatch, operation):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
            opened.seek(0)
            before = opened.snapshot()
            # A successful syscall reporting zero bytes must not loop or return partial success.
            with monkeypatch.context() as fault:
                api = 'ReadFile' if operation == 'read' else 'WriteFile'
                fault.setattr(opened._native.kernel32, api, lambda *args: 1)
                message = 'file ended during bounded read' if operation == 'read' else 'native write made no progress'
                with pytest.raises(QuarantineFilesystemError, match=message):
                    opened.read(16) if operation == 'read' else opened.write_all(b'changed')
            assert opened.snapshot() == before
            assert opened.read(16) == b'original'
            assert opened.check_path() == before.identity


def test_bounded_read_refuses_real_concurrent_truncation(tmp_path, monkeypatch):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
        with root.open_file('record.ae1') as opened, root.open_file('record.ae1', writable=True) as writer:
            read_file = opened._native.kernel32.ReadFile
            reads = []

            def read_then_truncate(*args):
                result = read_file(*args)
                reads.append(result)
                writer.truncate(2)
                writer.flush()
                return result

            with monkeypatch.context() as fault:
                fault.setattr(opened._native.kernel32, 'ReadFile', read_then_truncate)
                with pytest.raises(QuarantineFilesystemError, match='file changed during bounded read'):
                    opened.read(16)
            assert reads == [1]
            assert opened.identity == writer.identity
            assert opened.read(16) == b'or'
            assert opened.check_path() == writer.identity


def test_publication_refuses_same_name_and_wrong_staging_identity_without_mutation(tmp_path):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        identities = {}
        for name in ('stage.ae1', 'other.ae1'):
            with root.create_file(name) as opened:
                opened.write_all(name.encode())
                opened.flush()
                identities[name] = opened.identity
        with pytest.raises(QuarantineFilesystemError, match='source and destination must differ'):
            root.publish_write_once('stage.ae1', 'stage.ae1', expected_identity=identities['stage.ae1'])
        with pytest.raises(QuarantineFilesystemError, match='staging file identity changed'):
            root.publish_write_once('stage.ae1', 'published.ae1', expected_identity=identities['other.ae1'])
        assert root.list_files() == ['.lock', 'other.ae1', 'stage.ae1']
        for name, identity in identities.items():
            assert root.read_file(name, 32, expected_identity=identity) == name.encode()


def test_named_stream_refusal_releases_source_handles(tmp_path):
    source = tmp_path / 'source.ae1'
    source.write_bytes(b'ordinary source')
    windows_fs.require_ordinary_stream(source, directory=False)
    stream = Path(str(source) + ':unrepresented')
    stream.write_bytes(b'named source')
    with pytest.raises(QuarantineFilesystemError, match='named source streams cannot be preserved'):
        windows_fs.require_ordinary_stream(source, directory=False)
    assert source.read_bytes() == b'ordinary source'
    assert stream.read_bytes() == b'named source'
    moved = source.with_name('moved.ae1')
    source.rename(moved)
    assert Path(str(moved) + ':unrepresented').read_bytes() == b'named source'


def test_refused_private_directory_open_closes_leaf_and_ancestor_handles(tmp_path, monkeypatch):
    path = tmp_path / 'quarantine'
    with WindowsQuarantineDirectory.create(path, lock_file=False):
        pass
    _set_test_dacl(path, 'D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FR;;;WD)')
    native = windows_fs._native()
    create_or_open, close = native.create_or_open, native.close
    opened, closed = [], []

    def track_open(*args, **kwargs):
        handle = create_or_open(*args, **kwargs)
        opened.append(handle)
        return handle

    def track_close(handle):
        if handle in opened:
            closed.append(handle)
        close(handle)

    try:
        with monkeypatch.context() as tracking:
            tracking.setattr(native, 'create_or_open', track_open)
            tracking.setattr(native, 'close', track_close)
            with pytest.raises(QuarantineFilesystemError, match='DACL contains unexpected entries'):
                WindowsQuarantineDirectory.open(path)
        assert opened
        assert closed == list(reversed(opened)), 'refused open leaked a native directory handle'
        path.rename(tmp_path / 'released')
    finally:
        for handle in reversed(opened):
            if handle not in closed:
                close(handle)


@pytest.mark.parametrize('failure', [
    'token-size-zero', 'token-size-excessive', 'token-read', 'invalid-sid',
    'sid-encoding', 'sid-length-zero', 'sid-length-excessive',
])
def test_owner_sid_failure_closes_token_and_frees_allocated_string(monkeypatch, failure):
    native = windows_fs._native()
    expected = native.current_user_sid()
    open_token = native.advapi32.OpenProcessToken
    token_info = native.advapi32.GetTokenInformation
    encode_sid = native.advapi32.ConvertSidToStringSidW
    close, free = native.close, native.kernel32.LocalFree
    tokens, strings, closed, freed = [], [], [], []

    def track_token(*args):
        result = open_token(*args)
        if result:
            tokens.append(ctypes.cast(args[2], ctypes.POINTER(wintypes.HANDLE))[0])
        return result

    def read_token(*args):
        if args[2] is None and failure.startswith('token-size'):
            ctypes.cast(args[4], ctypes.POINTER(wintypes.DWORD))[0] = (
                0 if failure == 'token-size-zero' else 65537)
            return 0
        if args[2] is not None and failure == 'token-read':
            ctypes.set_last_error(5)
            return 0
        return token_info(*args)

    def track_string(*args):
        if failure == 'sid-encoding':
            return 0
        result = encode_sid(*args)
        if result:
            strings.append(ctypes.cast(args[1], ctypes.POINTER(ctypes.c_void_p))[0])
        return result

    def track_close(handle):
        closed.append(handle)
        close(handle)

    def track_free(pointer):
        freed.append(pointer.value if isinstance(pointer, ctypes.c_void_p) else pointer)
        return free(pointer)

    with monkeypatch.context() as fault:
        fault.setattr(native.advapi32, 'OpenProcessToken', track_token)
        fault.setattr(native.advapi32, 'GetTokenInformation', read_token)
        fault.setattr(native.advapi32, 'ConvertSidToStringSidW', track_string)
        fault.setattr(native, 'close', track_close)
        fault.setattr(native.kernel32, 'LocalFree', track_free)
        if failure == 'invalid-sid':
            fault.setattr(native.advapi32, 'IsValidSid', lambda *args: 0)
        if failure.startswith('sid-length'):
            fault.setattr(native.advapi32, 'GetLengthSid',
                          lambda *args: 0 if failure == 'sid-length-zero' else 69)
        with pytest.raises(QuarantineFilesystemError, match={
            'token-size-zero': 'current owner token is unavailable',
            'token-size-excessive': 'current owner token is unavailable',
            'token-read': 'current owner lookup failed',
            'invalid-sid': 'current owner SID is invalid',
            'sid-encoding': 'current owner SID cannot be encoded',
            'sid-length-zero': 'current owner SID length is invalid',
            'sid-length-excessive': 'current owner SID length is invalid',
        }[failure]):
            native.current_user_sid()
    assert len(tokens) == 1
    assert closed == tokens
    assert freed == strings
    assert len(strings) == (1 if failure.startswith('sid-length') else 0)
    assert native.current_user_sid() == expected


@pytest.mark.parametrize(('api', 'method', 'arguments', 'message'), [
    ('ReadFile', 'read', (16,), 'handle-relative read'),
    ('WriteFile', 'write_all', (b'changed',), 'handle-relative write'),
    ('SetFilePointerEx', 'seek', (2,), 'handle-relative seek'),
    ('SetFileInformationByHandle', 'truncate', (2,), 'handle-relative truncate'),
    ('FlushFileBuffers', 'flush', (), 'file flush'),
])
def test_native_io_failure_preserves_file_and_handle(tmp_path, monkeypatch, api, method, arguments, message):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
            before = opened.snapshot()
            opened.seek(0)
            calls = []

            def fail(*args):
                calls.append(args[0])
                ctypes.set_last_error(5)
                return 0

            with monkeypatch.context() as fault:
                fault.setattr(opened._native.kernel32, api, fail)
                with pytest.raises(QuarantineFilesystemError, match=message + r' failed \(Windows error 5\)'):
                    getattr(opened, method)(*arguments)
            assert calls == [opened.handle]
            assert opened.snapshot() == before
            assert opened.read(16) == b'original'
            assert opened.check_path(expected_identity=before.identity) == before.identity


@pytest.mark.parametrize('operation', ['create', 'open'])
def test_file_validation_failure_closes_new_handle_and_preserves_parent(tmp_path, monkeypatch, operation):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        if operation == 'open':
            with root.create_file('record.ae1') as opened:
                opened.write_all(b'original')
                opened.flush()
                identity = opened.identity
        native = root._native
        validate, close = native.owner_private_check, native.close
        absent = object()
        original_attributes = {name: native.__dict__.get(name, absent)
                               for name in ('owner_private_check', 'close')}
        refused, closed = [], []

        def refuse_file(handle, *, directory=None):
            if directory is False:
                refused.append(handle)
                raise QuarantineFilesystemError('synthetic file security inspection failure')
            return validate(handle, directory=directory)

        def track_close(handle):
            if handle in refused:
                closed.append(handle)
            close(handle)

        try:
            with monkeypatch.context() as fault:
                fault.setattr(native, 'owner_private_check', refuse_file)
                fault.setattr(native, 'close', track_close)
                with pytest.raises(QuarantineFilesystemError, match='synthetic file security inspection failure'):
                    root.create_file('record.ae1') if operation == 'create' else root.open_file('record.ae1')
        finally:
            # Undo stores inherited bound methods on the instance; restore original attribute absence.
            for name, value in original_attributes.items():
                if value is absent:
                    native.__dict__.pop(name, None)
        assert len(refused) == 1
        assert closed == refused
        assert all(native.__dict__.get(name, absent) is value
                   for name, value in original_attributes.items())
        if operation == 'open':
            assert root.read_file('record.ae1', 16, expected_identity=identity) == b'original'
        else:
            assert root.read_file('record.ae1', 0) == b''
        (root.path / 'record.ae1').rename(root.path / 'released.ae1')
        assert root.list_files() == ['.lock', 'released.ae1']


@pytest.mark.parametrize('invalid', ['pending-delete', 'negative-size'])
def test_snapshot_refuses_invalid_native_metadata_without_changing_file(tmp_path, monkeypatch, invalid):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
            before = opened.snapshot()
            native = opened._native
            get_info = native.kernel32.GetFileInformationByHandleEx
            inspected = []

            def invalid_metadata(handle, kind, output, size):
                result = get_info(handle, kind, output, size)
                if handle == opened.handle and kind == native.FILE_STANDARD_INFO and result:
                    info = ctypes.cast(output, ctypes.POINTER(windows_fs._FileStandardInfo)).contents
                    if invalid == 'pending-delete':
                        info.DeletePending = 1
                    else:
                        info.EndOfFile = -1
                    inspected.append(handle)
                return result

            with monkeypatch.context() as fault:
                fault.setattr(native.kernel32, 'GetFileInformationByHandleEx', invalid_metadata)
                with pytest.raises(QuarantineFilesystemError, match='pending deletion or has invalid size'):
                    opened.snapshot()
            assert inspected == [opened.handle]
            assert opened.snapshot() == before
            assert opened.read(16) == b'original'


@pytest.mark.parametrize('kind', ['file', 'directory'])
@pytest.mark.parametrize('phase', ['acquire', 'release'])
def test_native_lock_api_failure_denies_success_and_releases_resources(tmp_path, monkeypatch, kind, phase):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
            native = root._native
            target = opened if kind == 'file' else root
            api = 'LockFileEx' if phase == 'acquire' else 'UnlockFileEx'
            real_api = getattr(native.kernel32, api)
            calls, entered = [], []

            def fail(*args):
                calls.append(args[0])
                if phase == 'release':
                    assert real_api(*args), 'real lock release failed before injected reporting error'
                ctypes.set_last_error(5)
                return 0

            prefix = 'file' if kind == 'file' else 'quarantine'
            message = prefix + (' writer lock failed' if phase == 'acquire' else ' writer lock release failed')
            with monkeypatch.context() as fault:
                fault.setattr(native.kernel32, api, fail)
                with pytest.raises(QuarantineFilesystemError, match=message):
                    with target.lock():
                        entered.append(True)
            assert len(calls) == 1
            assert entered == ([True] if phase == 'release' else [])
            if kind == 'file':
                with root.open_file('record.ae1', writable=True) as independent, independent.lock():
                    assert independent.read(16) == b'original'
            else:
                with WindowsQuarantineDirectory.open(root.path) as independent, independent.lock():
                    assert independent.read_file('record.ae1', 16) == b'original'
            assert opened.check_path() == opened.identity


@pytest.mark.parametrize('inspection', ['attributes', 'standard', 'identity', 'timestamp'])
def test_native_metadata_api_failure_preserves_file_and_allows_retry(tmp_path, monkeypatch, inspection):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'original')
            opened.flush()
            before = opened.snapshot()
            native = opened._native
            get_info = native.kernel32.GetFileInformationByHandleEx
            info_class, message = {
                'attributes': (native.FILE_ATTRIBUTE_TAG_INFO, 'file attribute inspection'),
                'standard': (native.FILE_STANDARD_INFO, 'file snapshot inspection'),
                'identity': (native.FILE_ID_INFO, 'stable file identity inspection'),
                'timestamp': (native.FILE_BASIC_INFO, 'file timestamp inspection'),
            }[inspection]
            failures = []

            def fail_inspection(handle, kind, *args):
                if handle == opened.handle and kind == info_class:
                    failures.append(handle)
                    ctypes.set_last_error(5)
                    return 0
                return get_info(handle, kind, *args)

            with monkeypatch.context() as fault:
                fault.setattr(native.kernel32, 'GetFileInformationByHandleEx', fail_inspection)
                with pytest.raises(QuarantineFilesystemError, match=message):
                    opened.snapshot()
            assert failures == [opened.handle]
            assert opened.snapshot() == before
            assert opened.read(16) == b'original'


@pytest.mark.parametrize(('kind', 'success_without_output'), [
    ('sid', False), ('sid', True), ('descriptor', False), ('descriptor', True),
    ('token', True), ('open', True),
])
def test_native_primitive_refuses_failure_or_missing_output_and_allows_retry(
        tmp_path, monkeypatch, kind, success_without_output):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        native = root._native
        sid = native.current_user_sid()
        dll, api, message = {
            'sid': (native.advapi32, 'ConvertStringSidToSidW', 'security SID could not be created'),
            'descriptor': (native.advapi32, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
                           'security descriptor could not be built'),
            'token': (native.advapi32, 'OpenProcessToken', 'current owner token is unavailable'),
            'open': (native.ntdll, 'NtCreateFile', 'handle-relative open returned no handle'),
        }[kind]
        calls = []

        def no_output(*args):
            calls.append(True)
            return int(success_without_output) if kind != 'open' else 0

        with monkeypatch.context() as fault:
            fault.setattr(dll, api, no_output)
            with pytest.raises(QuarantineFilesystemError, match=message):
                if kind == 'sid':
                    native._sid_from_string(sid[0])
                elif kind == 'descriptor':
                    native.private_security_descriptor()
                elif kind == 'token':
                    native.current_user_sid()
                else:
                    root.open_file('.lock')
        assert calls == [True]
        assert native.current_user_sid() == sid
        assert root.list_files() == ['.lock']
        assert owner_private_handle_check(root.handle) == root.identity


@pytest.mark.parametrize(('dacl', 'message'), [
    ('D:(A;;FA;;;OW)(A;;FA;;;SY)', 'explicit and protected'),
    ('D:P(A;;FA;;;OW)', 'unexpected entries'),
    ('D:P(A;;FA;;;OW)(A;;FA;;;OW)', 'DACL is incomplete'),
    ('D:P(A;;FA;;;OW)(A;;FA;;;WD)', 'unexpected principal'),
    ('D:P(A;;FR;;;OW)(A;;FA;;;SY)', 'permissions are broader or malformed'),
    ('D:P(D;;FA;;;OW)(A;;FA;;;SY)', 'permissions are broader or malformed'),
    ('D:P(A;OI;FA;;;OW)(A;;FA;;;SY)', 'permissions are broader or malformed'),
    ('D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FR;;;WD)', 'unexpected entries'),
])
def test_real_security_descriptor_refusal_frees_validation_sids(monkeypatch, dacl, message):
    native = windows_fs._native()
    owner_string, _ = native.current_user_sid()
    descriptor = ctypes.c_void_p()
    assert native.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f'O:{owner_string}{dacl}', 1, ctypes.byref(descriptor), None)
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.GetSecurityDescriptorOwner.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    api.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    api.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    owner, acl = ctypes.c_void_p(), ctypes.c_void_p()
    present, defaulted = wintypes.BOOL(), wintypes.BOOL()
    allocated, freed = [], []
    sid_from_string, free = native._sid_from_string, native.kernel32.LocalFree

    def track_sid(value):
        pointer = sid_from_string(value)
        allocated.append(pointer)
        return pointer

    def track_free(pointer):
        freed.append(pointer.value if isinstance(pointer, ctypes.c_void_p) else pointer)
        return free(pointer)

    try:
        assert api.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted))
        assert api.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted))
        with monkeypatch.context() as tracking:
            tracking.setattr(native, '_sid_from_string', track_sid)
            tracking.setattr(native.kernel32, 'LocalFree', track_free)
            with pytest.raises(QuarantineFilesystemError, match=message):
                native._verify_private_security(owner, acl, descriptor)
        assert len(allocated) == (0 if 'explicit' in message else 3)
        assert all(freed.count(pointer) == 1 for pointer in allocated)
    finally:
        free(descriptor)


@pytest.mark.parametrize(('api', 'result', 'message'), [
    ('GetSecurityInfo', 5, 'security inspection failed'),
    ('GetSecurityDescriptorControl', 0, 'security descriptor is incomplete'),
    ('GetAclInformation', 0, 'DACL cannot be read'),
    ('GetAce', 0, 'DACL contains an invalid entry'),
    ('GetAce', 1, 'DACL contains an invalid entry'),
    ('EqualSid', 0, 'belongs to another owner'),
])
def test_security_api_refusal_frees_descriptor_and_sids(tmp_path, monkeypatch, api, result, message):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        native = root._native
        get_security = native._security_descriptor
        sid_from_string, free = native._sid_from_string, native.kernel32.LocalFree
        allocated, freed, calls = [], [], []

        def track_security(*args):
            values = get_security(*args)
            allocated.append(values[0].value)
            return values

        def track_sid(value):
            pointer = sid_from_string(value)
            allocated.append(pointer)
            return pointer

        def track_free(pointer):
            freed.append(pointer.value if isinstance(pointer, ctypes.c_void_p) else pointer)
            return free(pointer)

        def refuse(*args):
            calls.append(True)
            return result

        with monkeypatch.context() as fault:
            fault.setattr(native, '_security_descriptor', track_security)
            fault.setattr(native, '_sid_from_string', track_sid)
            fault.setattr(native.kernel32, 'LocalFree', track_free)
            fault.setattr(native.advapi32, api, refuse)
            with pytest.raises(QuarantineFilesystemError, match=message):
                owner_private_handle_check(root.handle)
        assert calls == [True]
        assert len(allocated) == (0 if api == 'GetSecurityInfo'
                                  else 1 if api == 'GetSecurityDescriptorControl' else 4)
        assert all(freed.count(pointer) == 1 for pointer in allocated)
        assert owner_private_handle_check(root.handle) == root.identity
        assert root.list_files() == ['.lock']


@pytest.mark.parametrize('failure', ['unavailable', 'extra-entry', 'odd-name-length', 'wrong-stream-name'])
def test_stream_inventory_refusal_preserves_source_and_closes_handles(tmp_path, monkeypatch, failure):
    source = tmp_path / 'source.ae1'
    source.write_bytes(b'original')
    native = windows_fs._native()
    get_info = native.kernel32.GetFileInformationByHandleEx
    calls = []

    def malformed_inventory(handle, kind, buffer, size):
        if kind != 7:
            return get_info(handle, kind, buffer, size)
        calls.append(handle)
        if failure == 'unavailable':
            ctypes.set_last_error(5)
            return 0
        assert get_info(handle, kind, buffer, size)
        if failure == 'extra-entry':
            ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0] = 40
        elif failure == 'odd-name-length':
            ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[1] = 13
        else:
            ctypes.memmove(ctypes.addressof(buffer) + 24, ':x:$DATA'.encode('utf-16-le'), 14)
        return 1

    with monkeypatch.context() as fault:
        fault.setattr(native.kernel32, 'GetFileInformationByHandleEx', malformed_inventory)
        with pytest.raises(QuarantineFilesystemError, match=(
                'inventory is unavailable' if failure == 'unavailable' else 'cannot be preserved')):
            windows_fs.require_ordinary_stream(source, directory=False)
    assert len(calls) == 1
    assert source.read_bytes() == b'original'
    source.rename(tmp_path / 'released.ae1')
    windows_fs.require_ordinary_stream(tmp_path / 'released.ae1', directory=False)


@pytest.mark.parametrize('failure', ['wrong-type', 'standard-inspection'])
def test_native_type_inspection_refusal_keeps_real_handle_usable(tmp_path, monkeypatch, failure):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        native = root._native
        if failure == 'wrong-type':
            with pytest.raises(QuarantineFilesystemError, match='unexpected filesystem object type'):
                native.verify_type(root.handle, directory=False)
        else:
            get_info = native.kernel32.GetFileInformationByHandleEx
            calls = []

            def refuse_standard(handle, kind, *args):
                if kind == native.FILE_STANDARD_INFO:
                    calls.append(handle)
                    ctypes.set_last_error(5)
                    return 0
                return get_info(handle, kind, *args)

            with monkeypatch.context() as fault:
                fault.setattr(native.kernel32, 'GetFileInformationByHandleEx', refuse_standard)
                with pytest.raises(QuarantineFilesystemError, match='file identity inspection failed'):
                    native.verify_type(root.handle, directory=True)
            assert calls == [root.handle]
        assert owner_private_handle_check(root.handle) == root.identity
        assert root.list_files() == ['.lock']


def test_private_leaf_accepts_unprivate_ancestor_with_repeated_basename(tmp_path):
    ancestor = tmp_path / 'repeated'
    ancestor.mkdir()
    leaf = ancestor / 'repeated'
    with WindowsQuarantineDirectory.create(leaf, lock_file=False) as created:
        expected = created.identity
    with WindowsQuarantineDirectory.open(ancestor, owner_private=False) as parent:
        with pytest.raises(QuarantineFilesystemError):
            owner_private_handle_check(parent.handle)
    with WindowsQuarantineDirectory.open(leaf) as reopened:
        assert reopened.identity == expected
        assert owner_private_handle_check(reopened.handle) == expected
        assert reopened.list_files() == []


def test_nested_directory_security_refusal_closes_relative_and_absolute_handles(tmp_path, monkeypatch):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        child = root.path / 'child'
        with WindowsQuarantineDirectory.create(child, lock_file=False):
            pass
        _set_test_dacl(child, 'D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FR;;;WD)')
        native = root._native
        create_or_open, close = native.create_or_open, native.close
        opened, closed = [], []

        def track_open(*args, **kwargs):
            handle = create_or_open(*args, **kwargs)
            opened.append(handle)
            return handle

        def track_close(handle):
            if handle in opened:
                closed.append(handle)
            close(handle)

        try:
            with monkeypatch.context() as tracking:
                tracking.setattr(native, 'create_or_open', track_open)
                tracking.setattr(native, 'close', track_close)
                with pytest.raises(QuarantineFilesystemError, match='DACL contains unexpected entries'):
                    root.open_directory('child')
            assert opened
            assert closed == list(reversed(opened)), 'nested-directory refusal leaked a native handle'
            child.rename(root.path / 'released')
        finally:
            for handle in reversed(opened):
                if handle not in closed:
                    close(handle)


def test_directory_security_descriptor_failure_closes_all_parent_handles(tmp_path, monkeypatch):
    native = windows_fs._native()
    parent = tmp_path / 'parent'
    parent.mkdir()
    path = parent / 'private'
    relative_open, close = native.create_or_open, native.close
    opened, closed = [], []

    def relative(*args, **kwargs):
        handle = relative_open(*args, **kwargs)
        opened.append(handle)
        return handle

    def released(handle):
        closed.append(handle)
        close(handle)

    def refused():
        raise QuarantineFilesystemError('synthetic security descriptor unavailable')

    try:
        with monkeypatch.context() as patch:
            patch.setattr(native, 'create_or_open', relative)
            patch.setattr(native, 'close', released)
            patch.setattr(native, 'private_security_descriptor', refused)
            with pytest.raises(QuarantineFilesystemError, match='security descriptor unavailable'):
                WindowsQuarantineDirectory.create(path)
        assert opened
        assert closed == list(reversed(opened)), 'security descriptor failure leaked parent handles'
        assert not path.exists()
        parent.rename(tmp_path / 'released')
    finally:
        for handle in reversed(opened):
            if handle not in closed:
                close(handle)


@pytest.mark.parametrize('owner_private', [True, False])
def test_relative_directory_open_retains_identity_and_releases_ancestry(tmp_path, owner_private):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        child_path = root.path / 'child'
        with WindowsQuarantineDirectory.create(child_path, lock_file=False) as created:
            identity = created.identity
        with root.open_directory('child', owner_private=owner_private) as child:
            assert child.identity == identity
            with child.create_file('record.ae1') as member:
                member.write_all(b'unchanged ciphertext')
                member.flush()
            assert child.read_file('record.ae1', 100) == b'unchanged ciphertext'
            child.flush()
        child_path.rename(root.path / 'released')
        assert (root.path / 'released' / 'record.ae1').read_bytes() == b'unchanged ciphertext'


def test_relative_directory_identity_mismatch_closes_every_acquired_handle(tmp_path, monkeypatch):
    from dataclasses import replace

    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        child = root.path / 'child'
        with WindowsQuarantineDirectory.create(child, lock_file=False):
            pass
        native = root._native
        original_open, original_identity, original_close = native.create_or_open, native.identity, native.close
        relative, opened, closed = [], [], []

        def open_handle(parent, name, **kwargs):
            handle = original_open(parent, name, **kwargs)
            opened.append(handle)
            if parent == root.handle and name == 'child':
                relative.append(handle)
            return handle

        def identity(handle):
            value = original_identity(handle)
            return replace(value, volume_serial=value.volume_serial + 1) if handle in relative else value

        def close(handle):
            if handle in opened:
                closed.append(handle)
            original_close(handle)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(native, 'create_or_open', open_handle)
                patch.setattr(native, 'identity', identity)
                patch.setattr(native, 'close', close)
                with pytest.raises(QuarantineFilesystemError, match='directory identity changed'):
                    root.open_directory('child')
            assert relative
            assert closed == list(reversed(opened))
            child.rename(root.path / 'released')
        finally:
            for handle in reversed(opened):
                if handle not in closed:
                    original_close(handle)


def test_native_unicode_name_refuses_unrepresentable_length_before_open():
    with pytest.raises(QuarantineFilesystemError, match='component exceeds its bound'):
        windows_fs._native()._unicode('x' * 32767)


def test_directory_creation_refuses_volume_root():
    with pytest.raises(QuarantineFilesystemError, match='cannot be a volume root'):
        WindowsQuarantineDirectory.create(Path.cwd().anchor)


def test_owner_only_validation_refuses_foreign_owner_without_changing_security(tmp_path, monkeypatch):
    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        identity = root.identity
        with monkeypatch.context() as patch:
            patch.setattr(root._native.advapi32, 'EqualSid', lambda *args: 0)
            with pytest.raises(QuarantineFilesystemError, match='belongs to another owner'):
                root._native.owner_check(root.handle, directory=True)
        assert owner_private_handle_check(root.handle) == identity
        assert root.list_files() == ['.lock']


def test_private_directory_creation_race_accepts_only_a_verified_private_winner(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'nested'
    original = WindowsQuarantineDirectory.create.__func__
    races = []

    def competing_create(cls, candidate, **kwargs):
        with original(cls, candidate, **kwargs):
            pass
        races.append(candidate)
        raise QuarantineFilesystemError('synthetic competing creator won')

    monkeypatch.setattr(WindowsQuarantineDirectory, 'create', classmethod(competing_create))
    windows_fs.ensure_private_directory(path)
    assert races == [path.parent, path]
    with WindowsQuarantineDirectory.open(path) as reopened:
        assert owner_private_handle_check(reopened.handle) == reopened.identity
        assert reopened.list_files() == []


def test_open_file_identity_checks_refuse_wrong_expected_identity_without_mutation(tmp_path):
    from dataclasses import replace

    with WindowsQuarantineDirectory.create(tmp_path / 'quarantine') as root:
        with root.create_file('record.ae1') as opened:
            opened.write_all(b'unchanged')
            opened.flush()
            identity = opened.identity
            wrong = replace(identity, volume_serial=identity.volume_serial + 1)
            with pytest.raises(QuarantineFilesystemError, match='open file identity changed'):
                opened._recheck(wrong)
            with pytest.raises(QuarantineFilesystemError, match='open file identity changed'):
                opened.check_path(expected_identity=wrong)
            assert opened.read(20) == b'unchanged'
            assert opened.check_path(expected_identity=identity) == identity


def test_ancestry_mismatch_refuses_and_releases_revalidation_handles(tmp_path):
    from dataclasses import replace

    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    try:
        original = root._identities
        wrong = replace(original[-1], volume_serial=original[-1].volume_serial + 1)
        root._identities = [*original[:-1], wrong]
        with pytest.raises(QuarantineFilesystemError, match='ancestry identity changed'):
            root._revalidate()
        root._identities = original
        root._revalidate()
    finally:
        root.close()
    root.path.rename(tmp_path / 'released')
    with pytest.raises(QuarantineFilesystemError, match='directory handle is closed'):
        root.snapshot()


@pytest.mark.parametrize('phase', ['staged', 'published'])
def test_process_termination_leaves_only_ciphertext_and_no_false_success(tmp_path, phase):
    root = WindowsQuarantineDirectory.create(tmp_path / 'quarantine')
    root.close()
    script = f"""
import os, sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[2] / 'scripts')!r})
from pathlib import Path
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory
root = WindowsQuarantineDirectory.open(Path(sys.argv[1]))
encrypted, _ = EnvelopeCipher(StaticKeyProvider({{1: b'k' * 32}})).encrypt(
    b'SYNTHETIC-SECRET-MARKER', aad=b'native-test'
)
with root.lock():
    with root.create_file('stage.ae1') as staged:
        staged.write_all(encrypted)
        staged.flush()
        identity = staged.identity
    if sys.argv[2] == 'published':
        root.publish_write_once('stage.ae1', 'catalog.ae1', expected_identity=identity)
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path / 'quarantine'), phase],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )  # noqa: S603
    assert result.returncode == 0, result.stderr
    root = WindowsQuarantineDirectory.open(tmp_path / 'quarantine')
    try:
        names = root.list_files()
        if phase == 'staged':
            assert names == ['.lock', 'stage.ae1']
            raw = root.read_file('stage.ae1', 1024)
        else:
            assert names == ['.lock', 'catalog.ae1']
            raw = root.read_file('catalog.ae1', 1024)
        assert raw.startswith(b'AE1')
        assert b'SYNTHETIC-SECRET-MARKER' not in raw
    finally:
        root.close()


def _create_hardlink(link: Path, target: Path) -> bool:
    create = ctypes.WinDLL('kernel32', use_last_error=True).CreateHardLinkW
    create.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID]
    create.restype = wintypes.BOOL
    return bool(create(str(link), str(target), None))


def _set_system_owner(path: Path) -> bool:
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    open_handle = kernel32.CreateFileW
    open_handle.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    open_handle.restype = wintypes.HANDLE
    handle = open_handle(str(path), 0x00080000 | 0x00020000, 0x1 | 0x2, None,
                         3, 0x02000000 | 0x00200000, None)
    if handle in {None, wintypes.HANDLE(-1).value}:
        return False
    sid = ctypes.c_void_p()
    convert = advapi32.ConvertStringSidToSidW
    convert.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    convert.restype = wintypes.BOOL
    if not convert('S-1-5-18', ctypes.byref(sid)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        set_security = advapi32.SetSecurityInfo
        set_security.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        set_security.restype = wintypes.DWORD
        result = set_security(handle, 1, 0x00000001, sid, None, None, None)
        return result == 0
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree(sid)
        kernel32.CloseHandle(handle)


def _set_everyone_readable_dacl(path: Path) -> None:
    _set_test_dacl(path, 'D:P(A;;FR;;;WD)')


def _set_test_dacl(path: Path, sddl: str) -> None:
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    open_handle = kernel32.CreateFileW
    open_handle.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    open_handle.restype = wintypes.HANDLE
    handle = open_handle(str(path), 0x00040000 | 0x00020000, 0x1 | 0x2, None,
                         3, 0x02000000 | 0x00200000, None)
    if handle in {None, wintypes.HANDLE(-1).value}:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        get_dacl = advapi32.GetSecurityDescriptorDacl
        get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        get_dacl.restype = wintypes.BOOL
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        set_security = advapi32.SetSecurityInfo
        set_security.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        set_security.restype = wintypes.DWORD
        result = set_security(handle, 1, 0x00000004 | 0x80000000, None, None, dacl, None)
        if result:
            raise OSError(result, 'SetSecurityInfo failed')
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree(descriptor)
        kernel32.CloseHandle(handle)
