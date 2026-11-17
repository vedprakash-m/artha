"""Read-only Work comparison includes native streams without disclosing identities."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from catchup_core import checkpoint as ck
from core_helpers import (
    STREAM_CIPHER as CIPHER,
    STREAM_ORDINARY as ORDINARY,
    stream_checkpoint as _checkpoint,
    stream_public_tree as _public_tree,
)

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='native Windows streams')


@pytest.mark.parametrize('change', ['none', 'stream', 'add', 'remove', 'root', 'directory', 'file'])
def test_work_diff_compares_primary_and_stream_bytes(tmp_path, change):
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


def test_work_diff_rechecks_the_complete_native_inventory(tmp_path, monkeypatch):
    archive, _ = _checkpoint(tmp_path / 'work.ack')
    root, _ = _public_tree(tmp_path)
    recheck = ck._recheck_work_streams

    def changed(captured, roots, exclude):
        Path(str(root) + ':late stream').write_bytes(b'late')
        recheck(captured, roots, exclude)

    monkeypatch.setattr(ck, '_recheck_work_streams', changed)
    with pytest.raises(ck.CheckpointBlocked, match='changed'):
        ck.diff(archive, CIPHER, {'work_state': root})


def test_work_diff_rejects_primary_replacement_after_stream_capture(tmp_path, monkeypatch):
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
def test_work_diff_includes_ordinary_members(tmp_path, change):
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


def test_work_diff_refuses_unsupported_sqlite_member_before_claiming_a_comparison(tmp_path):
    archive, _ = _checkpoint(tmp_path / 'work.ack')
    root, document = _public_tree(tmp_path)
    with document.open('r+b') as output:
        output.write(b'SQLite format 3\x00')
    with pytest.raises(ck.CheckpointBlocked, match='SQLite'):
        ck.diff(archive, CIPHER, {'work_state': root}, include_tables=True)


def test_work_diff_refuses_native_read_failure(tmp_path, monkeypatch):
    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError, WindowsQuarantineFile
    archive, _ = _checkpoint(tmp_path / 'work.ack')
    root, _ = _public_tree(tmp_path)
    capture = ck._capture_work_streams

    def unavailable(*_args):
        raise QuarantineFilesystemError('synthetic read failure')

    def capture_then_refuse(roots, exclude):
        captured = capture(roots, exclude)
        monkeypatch.setattr(WindowsQuarantineFile, 'read_chunk', unavailable)
        return captured

    monkeypatch.setattr(ck, '_capture_work_streams', capture_then_refuse)
    with pytest.raises(ck.CheckpointBlocked, match='could not be verified'):
        ck.diff(archive, CIPHER, {'work_state': root})


def test_work_diff_refuses_a_profile_mismatch_before_source_capture(tmp_path, monkeypatch):
    archive, _ = _checkpoint(tmp_path / 'work.ack')
    monkeypatch.setattr(ck, '_capture_work_streams', lambda *_: pytest.fail('cross-profile source access'))
    with pytest.raises(ck.CheckpointBlocked, match='profile'):
        ck.diff(archive, CIPHER, {}, expected_profile='personal')


def test_work_diff_rechecks_identity_after_a_completed_primary_read(tmp_path, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile
    archive, _ = _checkpoint(tmp_path / 'work.ack')
    root, document = _public_tree(tmp_path)
    capture, read = ck._capture_work_streams, WindowsQuarantineFile.read_chunk

    def changed(member, bound):
        raw = read(member, bound)
        with document.open('ab') as output:
            output.write(b'late change')
        return raw

    def capture_then_change(roots, exclude):
        captured = capture(roots, exclude)
        monkeypatch.setattr(WindowsQuarantineFile, 'read_chunk', changed)
        return captured

    monkeypatch.setattr(ck, '_capture_work_streams', capture_then_change)
    with pytest.raises(ck.CheckpointBlocked, match='changed during inventory'):
        ck.diff(archive, CIPHER, {'work_state': root})
