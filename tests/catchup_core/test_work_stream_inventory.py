"""Synthetic Windows Work stream inventory; no live Work files are opened."""
from __future__ import annotations

import ctypes
import struct
import sys
from pathlib import Path

import pytest

from catchup_core import quarantine_fs_windows as fs


def _information(*records: tuple[str, int, int]) -> bytes:
    parts = []
    for name, size, allocation in records:
        encoded = name.encode('utf-16-le', errors='surrogatepass')
        length = 24 + len(encoded)
        aligned = (length + 7) & ~7
        parts.append(bytearray(struct.pack('<IIqq', aligned, len(encoded), size, allocation)
                               + encoded + bytes(aligned - length)))
    if parts:
        struct.pack_into('<I', parts[-1], 0, 0)
    return b''.join(parts)


def _ordinary(size: int = 0) -> tuple[str, int, int]:
    return '::$DATA', size, size


def _named(name: str, size: int = 0) -> tuple[str, int, int]:
    return f':{name}:$DATA', size, size


def test_stream_information_returns_names_types_and_sizes_without_the_ordinary_stream():
    raw = _information(_ordinary(100), _named('empty'), _named('second stream', 7))

    assert fs._parse_stream_information(raw, directory=False) == (
        fs.NamedStreamInfo('empty', '$DATA', 0),
        fs.NamedStreamInfo('second stream', '$DATA', 7),
    )


def test_directory_information_does_not_require_an_ordinary_data_stream():
    raw = _information(_named('directory stream', 3))

    assert fs._parse_stream_information(raw, directory=True) == (
        fs.NamedStreamInfo('directory stream', '$DATA', 3),
    )
    with pytest.raises(fs.QuarantineFilesystemError):
        fs._parse_stream_information(raw, directory=False)


@pytest.mark.parametrize(
    'raw',
    [
        b'',
        bytes(23),
        _information(('::$DATA', -1, 0)),
        _information(('::$DATA', 0, -1)),
        _information((':name:$OTHER', 1, 1)),
        _information((':name:other:$DATA', 1, 1)),
        _information((':name/other:$DATA', 1, 1)),
        _information((':name\\other:$DATA', 1, 1)),
        _information((':bad\x00name:$DATA', 1, 1)),
        _information((':bad\x7fname:$DATA', 1, 1)),
        _information((':bad*name:$DATA', 1, 1)),
        _information((':trailing.:$DATA', 1, 1)),
        _information((':trailing :$DATA', 1, 1)),
        _information((':.:$DATA', 1, 1)),
        _information((':..:$DATA', 1, 1)),
        _information((':invalid\ud800:$DATA', 1, 1)),
        _information(_ordinary(), _ordinary()),
        _information(_ordinary(), _named('duplicate'), _named('duplicate')),
        _information(_ordinary(), _named('Name'), _named('name')),
        _information(_ordinary(), _named('caf\u00e9'), _named('cafe\u0301')),
    ],
)
def test_malformed_unsafe_or_ambiguous_information_is_refused(raw):
    with pytest.raises(fs.QuarantineFilesystemError):
        fs._parse_stream_information(raw, directory=False)


@pytest.mark.parametrize(
    ('offset', 'value'),
    [
        (0, 8),       # next entry overlaps the first header
        (0, 26),      # next entry is not aligned
        (0, 65528),   # next entry lies outside the returned buffer
        (4, 3),       # UTF-16 names require an even byte length
        (4, 65534),   # name extends beyond its containing record
    ],
)
def test_corrupt_record_lengths_and_offsets_are_refused(offset, value):
    raw = bytearray(_information(_ordinary(), _named('next')))
    struct.pack_into('<I', raw, offset, value)

    with pytest.raises(fs.QuarantineFilesystemError):
        fs._parse_stream_information(bytes(raw), directory=False)


def test_next_record_cannot_start_after_the_buffer():
    raw = bytearray(_information(_ordinary()))
    struct.pack_into('<I', raw, 0, len(raw))

    with pytest.raises(fs.QuarantineFilesystemError, match='malformed'):
        fs._parse_stream_information(bytes(raw), directory=False)


def test_named_stream_size_and_count_are_bounded(monkeypatch):
    monkeypatch.setattr(fs, '_MAX_NAMED_STREAM_BYTES', 3)
    monkeypatch.setattr(fs, '_MAX_NAMED_STREAM_TOTAL_BYTES', 5)
    monkeypatch.setattr(fs, '_MAX_NAMED_STREAMS', 2)

    with pytest.raises(fs.QuarantineFilesystemError):
        fs._parse_stream_information(_information(_ordinary(), _named('big', 4)), directory=False)
    with pytest.raises(fs.QuarantineFilesystemError):
        fs._parse_stream_information(
            _information(_ordinary(), _named('one', 3), _named('two', 3)), directory=False,
        )
    with pytest.raises(fs.QuarantineFilesystemError):
        fs._parse_stream_information(
            _information(_ordinary(), _named('one'), _named('two'), _named('three')),
            directory=False,
        )
    assert fs._parse_stream_information(
        _information(_ordinary(100), _named('one', 3), _named('two', 2)), directory=False,
    ) == (fs.NamedStreamInfo('one', '$DATA', 3), fs.NamedStreamInfo('two', '$DATA', 2))


def test_refusals_do_not_disclose_a_stream_name():
    secret_name = 'confidential-synthetic-name*'
    with pytest.raises(fs.QuarantineFilesystemError) as caught:
        fs._parse_stream_information(_information(_ordinary(), _named(secret_name)), directory=False)
    assert secret_name not in str(caught.value)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
@pytest.mark.parametrize('member', ['file', 'directory', 'root', 'file-root'])
def test_native_inventory_finds_multiple_and_empty_streams(tmp_path, member):
    root = tmp_path / 'root'
    root.mkdir()
    if member in {'file', 'file-root'}:
        owner = root / 'document.txt'
        owner.write_bytes(b'ordinary synthetic bytes')
    elif member == 'directory':
        owner = root / 'folder'
        owner.mkdir()
    else:
        owner = root
    Path(str(owner) + ':empty').write_bytes(b'')
    Path(str(owner) + ':second stream').write_bytes(b'stream bytes')

    if member in {'file', 'file-root'}:
        with (fs.WindowsQuarantineDirectory.open(owner.parent, owner_private=False) as parent,
              parent.open_file(owner.name, private=False) as opened):
            streams = parent._native.named_streams(opened.handle, directory=False)
    else:
        with fs.WindowsQuarantineDirectory.open(owner, owner_private=False) as opened:
            streams = opened._native.named_streams(opened.handle, directory=True)
    assert {(item.name, item.stream_type, item.size) for item in streams} == {
        ('empty', '$DATA', 0), ('second stream', '$DATA', len(b'stream bytes')),
    }


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
def test_unavailable_native_inventory_is_not_treated_as_empty(monkeypatch):
    native = fs._Native()
    identity = fs.FileIdentity(1, bytes(16))
    snapshot = fs.FileSnapshot(identity, 0, 0, 0)
    monkeypatch.setattr(native, 'snapshot', lambda _handle: snapshot)
    monkeypatch.setattr(native, 'kernel32', type('Kernel', (), {
        'GetFileInformationByHandleEx': staticmethod(lambda *_args: False),
    })())
    monkeypatch.setattr(fs, '_last_error', lambda: 5)

    with pytest.raises(fs.QuarantineFilesystemError, match='unavailable'):
        native.named_streams(1, directory=False)
    with pytest.raises(fs.QuarantineFilesystemError, match='unavailable'):
        native.named_streams(1, directory=True)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
def test_native_inventory_refuses_owner_changes(monkeypatch):
    native = fs._Native()
    identity = fs.FileIdentity(1, bytes(16))
    snapshots = iter((fs.FileSnapshot(identity, 0, 1, 1), fs.FileSnapshot(identity, 0, 1, 2)))
    monkeypatch.setattr(native, 'snapshot', lambda _handle: next(snapshots))
    raw = _information(_ordinary())

    def copy_information(_handle, _kind, buffer, _length):
        ctypes.memmove(buffer, raw, len(raw))
        return True

    monkeypatch.setattr(native, 'kernel32', type('Kernel', (), {
        'GetFileInformationByHandleEx': staticmethod(copy_information),
    })())
    with pytest.raises(fs.QuarantineFilesystemError, match='changed'):
        native.named_streams(1, directory=False)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
def test_empty_directory_eof_is_checked_for_stability(monkeypatch):
    native = fs._Native()
    identity = fs.FileIdentity(1, bytes(16))
    snapshot = fs.FileSnapshot(identity, 0, 1, 1)
    monkeypatch.setattr(native, 'snapshot', lambda _handle: snapshot)
    monkeypatch.setattr(native, 'kernel32', type('Kernel', (), {
        'GetFileInformationByHandleEx': staticmethod(lambda *_args: False),
    })())
    monkeypatch.setattr(fs, '_last_error', lambda: 38)

    assert native.named_streams(1, directory=True) == ()
    with pytest.raises(fs.QuarantineFilesystemError):
        native.named_streams(1, directory=False)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
@pytest.mark.parametrize('member', ['file', 'directory', 'root', 'file-root'])
def test_native_read_returns_exact_named_bytes(tmp_path, member):
    root = tmp_path / 'root'
    root.mkdir()
    if member in {'file', 'file-root'}:
        owner = root / 'document.txt'
        owner.write_bytes(b'ordinary synthetic bytes')
    elif member == 'directory':
        owner = root / 'folder'
        owner.mkdir()
    else:
        owner = root
    Path(str(owner) + ':empty').write_bytes(b'')
    Path(str(owner) + ':second stream').write_bytes(b'precise synthetic bytes')

    streams = fs.read_named_streams(owner, directory=owner.is_dir())

    assert {(item.name, item.stream_type, item.data) for item in streams} == {
        ('empty', '$DATA', b''), ('second stream', '$DATA', b'precise synthetic bytes'),
    }


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
@pytest.mark.parametrize('directory', [False, True])
def test_native_read_returns_the_verified_owner_snapshot(tmp_path, directory):
    owner = tmp_path / ('folder' if directory else 'document.txt')
    if directory:
        owner.mkdir()
    else:
        owner.write_bytes(b'ordinary synthetic bytes')
    Path(str(owner) + ':synthetic').write_bytes(b'exact synthetic stream bytes')

    snapshot, streams = fs.read_named_streams_with_snapshot(owner, directory=directory)

    with fs.WindowsQuarantineDirectory.open(
            owner if directory else owner.parent, owner_private=False) as opened:
        if directory:
            expected = opened.snapshot()
        else:
            with opened.open_file(owner.name, private=False) as member:
                expected = member.snapshot()
    assert snapshot == expected
    assert [(stream.name, stream.stream_type, stream.data) for stream in streams] == [
        ('synthetic', '$DATA', b'exact synthetic stream bytes'),
    ]


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS omission reading')
def test_endpointdlp_omission_never_opens_payload_and_retains_other_streams(tmp_path, monkeypatch):
    owner = tmp_path / 'document.txt'
    owner.write_bytes(b'primary bytes')
    Path(str(owner) + ':sec.endpointdlp').write_bytes(b'volatile metadata')
    Path(str(owner) + ':sec.endpointdlp.extra').write_bytes(b'mandatory metadata')
    native = fs._native()
    original = native.create_or_open
    def open_checked(parent, name, **kwargs):
        assert not name.endswith(':sec.endpointdlp:$DATA'), 'omitted payload was opened'
        return original(parent, name, **kwargs)
    monkeypatch.setattr(native, 'create_or_open', open_checked)
    omitted = []
    _, streams = fs.read_named_streams_with_snapshot(owner, directory=False,
                                                    omit_endpointdlp=True, omitted=omitted)
    assert [(item.name, item.data) for item in streams] == [('sec.endpointdlp.extra', b'mandatory metadata')]
    assert [(item.name, item.size) for item in omitted] == [('sec.endpointdlp', 17)]
    assert owner.read_bytes() == b'primary bytes'


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
@pytest.mark.parametrize(('max_bytes', 'max_streams'), [(3, 1), (4, 0)])
def test_native_read_refuses_remaining_budget_before_opening_streams(tmp_path, monkeypatch, max_bytes, max_streams):
    owner = tmp_path / 'document.txt'
    owner.write_bytes(b'ordinary')
    Path(str(owner) + ':synthetic').write_bytes(b'data')
    native = fs._native()
    original = native.create_or_open
    opened_streams = []

    def track_stream(parent, name, **kwargs):
        if name.endswith(':synthetic:$DATA'):
            opened_streams.append(name)
        return original(parent, name, **kwargs)

    monkeypatch.setattr(native, 'create_or_open', track_stream)
    with pytest.raises(fs.QuarantineFilesystemError, match='bound'):
        fs.read_named_streams_with_snapshot(
            owner, directory=False, max_total_bytes=max_bytes, max_streams=max_streams,
        )
    assert not opened_streams


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
def test_native_read_rejects_an_unsupported_owner(tmp_path):
    with pytest.raises(fs.QuarantineFilesystemError, match='unsupported'):
        fs.read_named_streams(tmp_path, directory=1)
    with pytest.raises(fs.QuarantineFilesystemError, match='unsupported'):
        fs.read_named_streams(Path(tmp_path.anchor), directory=True)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
def test_native_read_refuses_a_directory_with_a_changed_parent_identity(tmp_path, monkeypatch):
    owner = tmp_path / 'directory'
    owner.mkdir()
    original_open = fs.WindowsQuarantineDirectory.open.__func__

    def changed_parent(cls, path, **kwargs):
        opened = original_open(cls, path, **kwargs)
        if Path(path) == owner:
            opened._identities[-2] = fs.FileIdentity(0, bytes(16))
        return opened

    monkeypatch.setattr(fs.WindowsQuarantineDirectory, 'open', classmethod(changed_parent))
    with pytest.raises(fs.QuarantineFilesystemError, match='identity'):
        fs.read_named_streams(owner, directory=True)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
@pytest.mark.parametrize('mutation', ['add', 'remove', 'change'])
def test_native_read_refuses_stream_mutation_during_capture(tmp_path, monkeypatch, mutation):
    owner = tmp_path / 'document.txt'
    owner.write_bytes(b'ordinary')
    stream = Path(str(owner) + ':one')
    stream.write_bytes(b'original')
    original = fs._Native.named_streams
    called = False

    def mutate_after_enumeration(native, handle, *, directory, **kwargs):
        nonlocal called
        result = original(native, handle, directory=directory, **kwargs)
        if not called:
            called = True
            if mutation == 'add':
                Path(str(owner) + ':another').write_bytes(b'new')
            elif mutation == 'remove':
                stream.unlink()
            else:
                stream.write_bytes(b'modified')
        return result

    original_snapshot = fs._Native.snapshot

    def stable_timestamps(native, handle):
        snapshot = original_snapshot(native, handle)
        return fs.FileSnapshot(snapshot.identity, snapshot.size, 1, 1)

    monkeypatch.setattr(fs._Native, 'named_streams', mutate_after_enumeration)
    monkeypatch.setattr(fs._Native, 'snapshot', stable_timestamps)
    with pytest.raises(fs.QuarantineFilesystemError):
        fs.read_named_streams(owner, directory=False)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
def test_native_read_does_not_treat_an_unreadable_stream_as_empty(tmp_path, monkeypatch):
    owner = tmp_path / 'document.txt'
    owner.write_bytes(b'ordinary')
    Path(str(owner) + ':private').write_bytes(b'present')
    native = fs._native()
    original = native.create_or_open

    def unavailable(parent, name, **kwargs):
        if name.endswith(':private:$DATA'):
            raise fs.QuarantineFilesystemError('synthetic stream is unavailable')
        return original(parent, name, **kwargs)

    monkeypatch.setattr(native, 'create_or_open', unavailable)
    with pytest.raises(fs.QuarantineFilesystemError, match='unavailable'):
        fs.read_named_streams(owner, directory=False)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
def test_native_read_refuses_a_mismatched_stream_owner(tmp_path, monkeypatch):
    owner = tmp_path / 'document.txt'
    owner.write_bytes(b'ordinary')
    Path(str(owner) + ':private').write_bytes(b'present')
    original = fs.WindowsQuarantineFile._validate

    def wrong_identity(opened):
        identity = original(opened)
        if ':private:$DATA' in opened.name:
            return fs.FileIdentity(identity.volume_serial, bytes(16))
        return identity

    monkeypatch.setattr(fs.WindowsQuarantineFile, '_validate', wrong_identity)
    with pytest.raises(fs.QuarantineFilesystemError, match='identity'):
        fs.read_named_streams(owner, directory=False)


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
def test_native_read_closes_a_stream_when_validation_fails(tmp_path, monkeypatch):
    owner = tmp_path / 'document.txt'
    owner.write_bytes(b'ordinary')
    Path(str(owner) + ':private').write_bytes(b'present')
    native = fs._native()
    original_open = native.create_or_open
    original_close = native.close
    original_validate = fs.WindowsQuarantineFile._validate
    opened_streams = []
    closed = []

    def remember_open(parent, name, **kwargs):
        handle = original_open(parent, name, **kwargs)
        if name.endswith(':private:$DATA'):
            opened_streams.append(handle)
        return handle

    def remember_close(handle):
        closed.append(handle)
        return original_close(handle)

    def reject_stream(opened):
        if opened.name.endswith(':private:$DATA'):
            raise fs.QuarantineFilesystemError('synthetic stream validation failed')
        return original_validate(opened)

    monkeypatch.setattr(native, 'create_or_open', remember_open)
    monkeypatch.setattr(native, 'close', remember_close)
    monkeypatch.setattr(fs.WindowsQuarantineFile, '_validate', reject_stream)
    with pytest.raises(fs.QuarantineFilesystemError, match='validation failed'):
        fs.read_named_streams(owner, directory=False)
    assert len(opened_streams) == 1
    assert opened_streams[0] in closed


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream reading')
def test_native_read_refuses_a_short_stream_read(tmp_path, monkeypatch):
    owner = tmp_path / 'document.txt'
    owner.write_bytes(b'ordinary')
    Path(str(owner) + ':private').write_bytes(b'present')
    original = fs.WindowsQuarantineFile.read

    def short_stream(opened, bound):
        data = original(opened, bound)
        return data[:-1] if opened.name.endswith(':private:$DATA') else data

    monkeypatch.setattr(fs.WindowsQuarantineFile, 'read', short_stream)
    with pytest.raises(fs.QuarantineFilesystemError, match='size changed'):
        fs.read_named_streams(owner, directory=False)


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows streams')
class TestWorkStreamDiff:
    """Read-only Work comparison includes native streams without disclosing identities."""

    @pytest.mark.parametrize('change', ['none', 'stream', 'add', 'remove', 'root', 'directory', 'file'])
    def test_work_diff_compares_primary_and_stream_bytes(self, tmp_path, change):
        import json
        from catchup_core import checkpoint as ck
        from core_helpers import STREAM_CIPHER as CIPHER, stream_checkpoint as _checkpoint, stream_public_tree as _public_tree

        archive, _ = _checkpoint(tmp_path / 'work.ack')
        root, document = _public_tree(tmp_path)
        if change == 'stream':
            Path(str(document) + ':synthetic secret name').write_bytes(b'changed stream')
        elif change == 'add':
            Path(str(document) + ':new secret name').write_bytes(b'new stream')
        elif change == 'remove':
            Path(str(document) + ':empty').unlink()
        elif change == 'root':
            Path(str(root) + ':root stream').write_bytes(b'changed root')
        elif change == 'directory':
            Path(str(root / 'folder') + ':directory stream').write_bytes(b'changed directory')
        elif change == 'file':
            with document.open('ab') as output:
                output.write(b'changed')
        result = ck.diff(archive, CIPHER, {'work_state': root}, include_tables=True)
        assert result['added'] == result['removed'] == result['unreadable'] == []
        assert result['changed'] == (['work_state/note.txt'] if change == 'file' else [])
        assert result['streams_added'] == int(change == 'add')
        assert result['streams_removed'] == int(change == 'remove')
        assert result['streams_changed'] == int(change in {'stream', 'root', 'directory'})
        assert result['tables'] == {} and result['tables_unreadable'] == []
        assert 'secret name' not in json.dumps(result) and 'root stream' not in json.dumps(result)

    def test_work_diff_rechecks_the_complete_native_inventory(self, tmp_path, monkeypatch):
        from catchup_core import checkpoint as ck
        from core_helpers import STREAM_CIPHER as CIPHER, stream_checkpoint as _checkpoint, stream_public_tree as _public_tree

        archive, _ = _checkpoint(tmp_path / 'work.ack')
        root, _ = _public_tree(tmp_path)
        recheck = ck._recheck_work_streams

        def changed(captured, roots, exclude):
            Path(str(root) + ':late stream').write_bytes(b'late')
            recheck(captured, roots, exclude)

        monkeypatch.setattr(ck, '_recheck_work_streams', changed)
        with pytest.raises(ck.CheckpointBlocked, match='changed'):
            ck.diff(archive, CIPHER, {'work_state': root})

    def test_work_diff_rejects_primary_replacement_after_stream_capture(self, tmp_path, monkeypatch):
        from catchup_core import checkpoint as ck
        from core_helpers import STREAM_CIPHER as CIPHER, STREAM_ORDINARY as ORDINARY, stream_checkpoint as _checkpoint, stream_public_tree as _public_tree

        archive, _ = _checkpoint(tmp_path / 'work.ack')
        root, document = _public_tree(tmp_path)
        capture = ck._capture_work_streams

        def replace(roots, exclude):
            captured = capture(roots, exclude)
            document.unlink()
            document.write_bytes(ORDINARY)
            return captured

        monkeypatch.setattr(ck, '_capture_work_streams', replace)
        with pytest.raises(ck.CheckpointBlocked, match='changed|verified'):
            ck.diff(archive, CIPHER, {'work_state': root})

    @pytest.mark.parametrize('change', ['add', 'remove'])
    def test_work_diff_includes_ordinary_members(self, tmp_path, change):
        from catchup_core import checkpoint as ck
        from core_helpers import STREAM_CIPHER as CIPHER, stream_checkpoint as _checkpoint, stream_public_tree as _public_tree

        archive, _ = _checkpoint(tmp_path / 'work.ack')
        root, document = _public_tree(tmp_path)
        if change == 'add':
            (root / 'extra.txt').write_bytes(b'new synthetic member')
        else:
            document.unlink()
        result = ck.diff(archive, CIPHER, {'work_state': root})
        assert result['added'] == (['work_state/extra.txt'] if change == 'add' else [])
        assert result['removed'] == (['work_state/note.txt'] if change == 'remove' else [])
        assert 'tables' not in result

    def test_work_diff_refuses_unsupported_sqlite_and_read_failure(self, tmp_path, monkeypatch):
        from catchup_core import checkpoint as ck
        from core_helpers import STREAM_CIPHER as CIPHER, stream_checkpoint as _checkpoint, stream_public_tree as _public_tree

        archive, _ = _checkpoint(tmp_path / 'work.ack')
        root, document = _public_tree(tmp_path)
        with document.open('r+b') as output:
            output.write(b'SQLite format 3\x00')
        with pytest.raises(ck.CheckpointBlocked, match='SQLite'):
            ck.diff(archive, CIPHER, {'work_state': root}, include_tables=True)

        archive2, _ = _checkpoint(tmp_path / 'work2.ack')
        sub = tmp_path / 'sub'
        sub.mkdir()
        root2, _ = _public_tree(sub)
        capture = ck._capture_work_streams

        def unavailable(*_args):
            raise fs.QuarantineFilesystemError('synthetic read failure')

        def capture_then_refuse(roots, exclude):
            captured = capture(roots, exclude)
            monkeypatch.setattr(fs.WindowsQuarantineFile, 'read_chunk', unavailable)
            return captured

        monkeypatch.setattr(ck, '_capture_work_streams', capture_then_refuse)
        with pytest.raises(ck.CheckpointBlocked, match='could not be verified'):
            ck.diff(archive2, CIPHER, {'work_state': root2})

    def test_work_diff_refuses_profile_mismatch_and_late_primary_change(self, tmp_path, monkeypatch):
        from catchup_core import checkpoint as ck
        from core_helpers import STREAM_CIPHER as CIPHER, stream_checkpoint as _checkpoint, stream_public_tree as _public_tree

        archive, _ = _checkpoint(tmp_path / 'work.ack')
        monkeypatch.setattr(ck, '_capture_work_streams', lambda *_: pytest.fail('cross-profile source access'))
        with pytest.raises(ck.CheckpointBlocked, match='profile'):
            ck.diff(archive, CIPHER, {}, expected_profile='personal')
        monkeypatch.undo()

        root, document = _public_tree(tmp_path)
        capture, read = ck._capture_work_streams, fs.WindowsQuarantineFile.read_chunk

        def changed(member, bound):
            raw = read(member, bound)
            with document.open('ab') as output:
                output.write(b'late change')
            return raw

        def capture_then_change(roots, exclude):
            captured = capture(roots, exclude)
            monkeypatch.setattr(fs.WindowsQuarantineFile, 'read_chunk', changed)
            return captured

        monkeypatch.setattr(ck, '_capture_work_streams', capture_then_change)
        with pytest.raises(ck.CheckpointBlocked, match='changed during inventory'):
            ck.diff(archive, CIPHER, {'work_state': root})
