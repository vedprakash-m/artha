"""Cloud filter tags on named source streams grant no pathname or write authority."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import quarantine_fs_windows as fs


def native_with_tag(tag, *, links=1):
    native = object.__new__(fs._Native)
    def information(_handle, kind, buffer, _size):
        if kind == native.FILE_ATTRIBUTE_TAG_INFO:
            info = ctypes.cast(buffer, ctypes.POINTER(fs._FileAttributeTagInfo)).contents
            info.FileAttributes = native.FILE_ATTRIBUTE_REPARSE_POINT
            info.ReparseTag = tag
        elif kind == native.FILE_STANDARD_INFO:
            info = ctypes.cast(buffer, ctypes.POINTER(fs._FileStandardInfo)).contents
            info.NumberOfLinks = links
        else:
            raise AssertionError('unexpected information query')
        return True
    native.kernel32 = SimpleNamespace(GetFileInformationByHandleEx=information)
    return native


@pytest.mark.parametrize('variant', range(16))
def test_only_explicit_source_stream_mode_accepts_cloud_tag_family(variant):
    native = native_with_tag(0x9000001A | (variant << 12))
    native.verify_type(1, directory=False, source_stream=True)
    with pytest.raises(fs.QuarantineFilesystemError, match='reparse'):
        native.verify_type(1, directory=False)


@pytest.mark.parametrize('tag', [0xA0000003, 0xA000000C, 0x9000001C, 0x80000021,
                                 0x9001001A, 0xB000001A, 0, 0x12345678])
def test_source_stream_mode_still_refuses_other_reparse_tags(tag):
    with pytest.raises(fs.QuarantineFilesystemError, match='reparse'):
        native_with_tag(tag).verify_type(1, directory=False, source_stream=True)


def test_cloud_source_stream_mode_retains_hardlink_refusal():
    with pytest.raises(fs.QuarantineFilesystemError, match='linked'):
        native_with_tag(0x9000001A, links=2).verify_type(1, directory=False, source_stream=True)


def test_private_objects_cannot_select_source_stream_mode():
    with pytest.raises(fs.QuarantineFilesystemError, match='private objects'):
        fs.WindowsQuarantineFile(None, None, 'synthetic', 1, source_stream=True)


@pytest.mark.parametrize('options', [
    {'directory': True}, {'create': True}, {'access': fs._Native.GENERIC_WRITE},
    {'access': fs._Native.GENERIC_READ | fs._Native.DELETE}, {'security_descriptor': 1},
])
def test_source_stream_open_never_requests_mutation_or_directory_access(options):
    native = object.__new__(fs._Native)
    arguments = dict(directory=False, create=False, access=native.GENERIC_READ)
    arguments.update(options)
    with pytest.raises(fs.QuarantineFilesystemError, match='read-only'):
        native.create_or_open(1, 'owner.txt:synthetic:$DATA', source_stream=True, **arguments)


@pytest.mark.parametrize('name', ['owner.txt', 'owner.txt:synthetic:$OTHER', '../owner:synthetic:$DATA',
                                   'owner.txt:bad/name:$DATA', 'owner.txt:one:two:$DATA'])
def test_source_stream_open_requires_a_safe_typed_stream_name(name):
    native = object.__new__(fs._Native)
    with pytest.raises(fs.QuarantineFilesystemError):
        native.create_or_open(1, name, directory=False, create=False,
                              access=native.GENERIC_READ, source_stream=True)


@pytest.mark.skipif(os.name != 'nt', reason='native Windows named-stream capture')
def test_native_cloud_tagged_stream_is_read_without_relaxing_generic_or_private_opens(tmp_path, monkeypatch):
    owner = tmp_path / 'source.txt'
    owner.write_bytes(b'ordinary source')
    stream_path = Path(str(owner) + ':synthetic')
    stream_path.write_bytes(b'exact synthetic stream')
    native = fs._native()
    original = native.kernel32.GetFileInformationByHandleEx
    final_name = native.kernel32.GetFinalPathNameByHandleW
    final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_name.restype = wintypes.DWORD

    def cloud_information(handle, kind, buffer, size):
        result = original(handle, kind, buffer, size)
        if result and kind == native.FILE_ATTRIBUTE_TAG_INFO:
            name = ctypes.create_unicode_buffer(32768)
            count = final_name(handle, name, len(name), 0)
            assert 0 < count < len(name)
            if name.value.endswith((':synthetic', ':synthetic:$DATA')):
                info = ctypes.cast(buffer, ctypes.POINTER(fs._FileAttributeTagInfo)).contents
                info.FileAttributes |= native.FILE_ATTRIBUTE_REPARSE_POINT
                info.ReparseTag = 0x9000601A
        return result

    monkeypatch.setattr(native.kernel32, 'GetFileInformationByHandleEx', cloud_information)
    assert [(s.name, s.data) for s in fs.read_named_streams(owner, directory=False)] == [
        ('synthetic', b'exact synthetic stream')]
    with fs.WindowsQuarantineDirectory.open(owner.parent, owner_private=False) as parent:
        with pytest.raises(fs.QuarantineFilesystemError, match='reparse'):
            native.create_or_open(parent.handle, 'source.txt:synthetic:$DATA', directory=False,
                                  create=False, access=native.GENERIC_READ)
        handle = native.create_or_open(parent.handle, 'source.txt:synthetic:$DATA', directory=False,
                                       create=False, access=native.GENERIC_READ, source_stream=True)
        with fs.WindowsQuarantineFile(native, parent, 'source.txt:synthetic:$DATA', handle,
                                       private=False, source_stream=True) as opened:
            with pytest.raises(fs.QuarantineFilesystemError, match='reparse'):
                native.owner_private_check(handle, directory=False)
            for action in (lambda: opened.write_all(b'changed'), lambda: opened.truncate(0), opened.delete):
                with pytest.raises(fs.QuarantineFilesystemError, match='read-only'):
                    action()
    assert owner.read_bytes() == b'ordinary source'
    assert stream_path.read_bytes() == b'exact synthetic stream'
