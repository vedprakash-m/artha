"""Synthetic coverage of the Windows named-stream integrity boundary."""
from __future__ import annotations

import ctypes
import hashlib
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from catchup_core import quarantine_fs_windows as fs


IDENTITY = fs.FileIdentity(1, bytes(16))
SNAPSHOT = fs.FileSnapshot(IDENTITY, 4, 1, 1)


class _Pinned:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


def _native_inventory(name, size):
    encoded = f':{name}:$DATA'.encode('utf-16-le')
    raw = struct.pack('<IIqq', 0, len(encoded), size, size) + encoded
    native = object.__new__(fs._Native)
    native.snapshot = lambda _handle: SNAPSHOT

    def copy_information(_handle, _kind, buffer, _length):
        ctypes.memmove(buffer, raw, len(raw))
        return True

    native.kernel32 = SimpleNamespace(GetFileInformationByHandleEx=copy_information)
    return native


@pytest.mark.parametrize(
    ('name', 'omit', 'fingerprinted'),
    [('metadata', True, True), ('sec.endpointdlp', True, False), ('sec.endpointdlp', False, True)],
)
def test_inventory_fingerprints_mandatory_streams_only(monkeypatch, name, omit, fingerprinted):
    native = _native_inventory(name, 4)
    parent = SimpleNamespace(handle=1, _native=native)
    fingerprint = Mock(return_value=b'f' * 32)
    monkeypatch.setattr(fs, '_fingerprint_named_stream', fingerprint)

    result = native.named_streams(2, directory=True, parent=parent, owner_name='owner', omit_endpointdlp=omit)

    assert result == (fs.NamedStreamInfo(name, '$DATA', 4, b'f' * 32 if fingerprinted else None),)
    assert fingerprint.call_count == int(fingerprinted)
    if fingerprinted:
        fingerprint.assert_called_once_with(native, parent, 'owner', IDENTITY, fs.NamedStreamInfo(name, '$DATA', 4))


@pytest.mark.parametrize('limits', [{'max_total_bytes': 3}, {'max_streams': 0}])
def test_inventory_refuses_exhausted_budget_before_fingerprinting(monkeypatch, limits):
    native = _native_inventory('metadata', 4)
    parent = SimpleNamespace(handle=1, _native=native)
    fingerprint = Mock()
    monkeypatch.setattr(fs, '_fingerprint_named_stream', fingerprint)

    with pytest.raises(fs.QuarantineFilesystemError, match='bound'):
        native.named_streams(2, directory=True, parent=parent, owner_name='owner', **limits)
    fingerprint.assert_not_called()


@pytest.mark.parametrize(
    'options',
    [
        {'owner_name': 'owner'},
        {'max_total_bytes': -1},
        {'max_total_bytes': True},
        {'max_streams': -1},
        {'max_streams': False},
        {'omit_endpointdlp': 1},
    ],
)
def test_inventory_rejects_invalid_fingerprint_options(options):
    native = object.__new__(fs._Native)
    with pytest.raises(fs.QuarantineFilesystemError, match='options'):
        native.named_streams(2, directory=True, **options)


@pytest.mark.parametrize(
    ('identity', 'size', 'data', 'error'),
    [
        (IDENTITY, 4, b'data', None),
        (fs.FileIdentity(2, bytes(16)), 4, b'data', 'identity or size'),
        (IDENTITY, 3, b'data', 'identity or size'),
        (IDENTITY, 4, b'dat', 'size changed'),
    ],
)
def test_fingerprint_requires_the_expected_owner_and_exact_bytes(monkeypatch, identity, size, data, error):
    native = SimpleNamespace(GENERIC_READ=1, create_or_open=Mock(return_value=4), close=Mock())
    parent = SimpleNamespace(handle=1)

    class Stream(_Pinned):
        def __init__(self, *_args, **_kwargs):
            self.identity = identity

        def snapshot(self):
            return SimpleNamespace(size=size)

        def read(self, _limit):
            return data

    monkeypatch.setattr(fs, 'WindowsQuarantineFile', Stream)
    info = fs.NamedStreamInfo('metadata', '$DATA', 4)
    if error is not None:
        with pytest.raises(fs.QuarantineFilesystemError, match=error):
            fs._fingerprint_named_stream(native, parent, 'owner', IDENTITY, info)
    else:
        assert fs._fingerprint_named_stream(native, parent, 'owner', IDENTITY, info) == hashlib.sha256(data).digest()
    native.create_or_open.assert_called_once_with(
        1, 'owner:metadata:$DATA', directory=False, create=False, access=1, source_stream=True,
    )


def test_fingerprint_closes_the_native_handle_when_stream_validation_fails(monkeypatch):
    native = SimpleNamespace(GENERIC_READ=1, create_or_open=Mock(return_value=4), close=Mock())
    parent = SimpleNamespace(handle=1)
    factory = Mock(side_effect=fs.QuarantineFilesystemError('synthetic validation failure'))
    monkeypatch.setattr(fs, 'WindowsQuarantineFile', factory)

    with pytest.raises(fs.QuarantineFilesystemError, match='validation failure'):
        fs._fingerprint_named_stream(native, parent, 'owner', IDENTITY, fs.NamedStreamInfo('metadata', '$DATA', 4))
    native.close.assert_called_once_with(4)


@pytest.mark.parametrize('change', ['none', 'during_capture', 'after_capture', 'missing_fingerprint'])
def test_capture_compares_bytes_with_both_inventories(monkeypatch, tmp_path, change):
    original = b'original'
    modified = b'modified'
    original_fingerprint = hashlib.sha256(original).digest()
    initial = fs.NamedStreamInfo('metadata', '$DATA', len(original),
                                 None if change == 'missing_fingerprint' else original_fingerprint)
    final = fs.NamedStreamInfo('metadata', '$DATA', len(original),
                               hashlib.sha256(modified).digest() if change == 'after_capture'
                               else original_fingerprint)
    native = SimpleNamespace(
        GENERIC_READ=1, create_or_open=Mock(return_value=3), close=Mock(),
        named_streams=Mock(side_effect=[(initial,), (final,)]),
    )

    class Owner(_Pinned):
        handle = 2
        snapshot = Mock(return_value=SNAPSHOT)
        check_path = Mock()

    class Parent(_Pinned):
        handle = 1
        _native = native
        _revalidate = Mock()

        def open_file(self, _name, **_kwargs):
            return owner

    class Stream(_Pinned):
        identity = IDENTITY

        def __init__(self, *_args, **_kwargs):
            pass

        def snapshot(self):
            return SimpleNamespace(size=len(original))

        def read(self, _limit):
            return modified if change == 'during_capture' else original

    owner = Owner()
    parent = Parent()
    monkeypatch.setattr(fs.WindowsQuarantineDirectory, 'open', lambda *_args, **_kwargs: parent)
    monkeypatch.setattr(fs, 'WindowsQuarantineFile', Stream)

    if change == 'none':
        snapshot, streams = fs.read_named_streams_with_snapshot(tmp_path / 'owner', directory=False)
        assert snapshot == SNAPSHOT
        assert streams == (fs.NamedStreamData('metadata', '$DATA', original),)
        owner.check_path.assert_called_once_with(expected_identity=IDENTITY)
    else:
        error = 'streams changed' if change == 'after_capture' else 'content changed'
        with pytest.raises(fs.QuarantineFilesystemError, match=error):
            fs.read_named_streams_with_snapshot(tmp_path / 'owner', directory=False)
