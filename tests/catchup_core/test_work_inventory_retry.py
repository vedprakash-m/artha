"""Bounded Work inventory retries discard a metadata-only unstable read."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
import stat

import pytest

from catchup_core import quarantine_fs_windows as fs
from catchup_core import work_privacy as wp

RAW = b'synthetic source'
SNAPSHOT = fs.FileSnapshot(fs.FileIdentity(1, b'x' * 16), len(RAW), 100, 200)


class Reader:
    def __init__(self, snapshots, payloads=(RAW, RAW)):
        self.snapshots = iter(snapshots)
        self.payloads = iter(payloads)
        self.reads = 0
        self.seeks = 0

    def snapshot(self):
        return next(self.snapshots)

    def seek(self, offset):
        assert offset == 0
        self.seeks += 1

    def read_chunk(self, bound):
        assert 0 < bound <= 65536
        self.reads += 1
        return next(self.payloads)


def test_one_fresh_read_accepts_a_settled_metadata_only_transition():
    settled = replace(SNAPSHOT, change_100ns=201)
    reader = Reader([SNAPSHOT, settled, settled, settled])
    assert wp._native_member_digest(reader, len(RAW)) == {
        'size': len(RAW), 'sha256': hashlib.sha256(RAW).hexdigest()}
    assert reader.reads == reader.seeks == 2


def test_stable_native_inventory_remains_single_pass():
    reader = Reader([SNAPSHOT, SNAPSHOT])
    assert wp._native_member_digest(reader, len(RAW))['size'] == len(RAW)
    assert reader.reads == reader.seeks == 1


@pytest.mark.parametrize('field,value', [
    ('identity', fs.FileIdentity(2, b'y' * 16)), ('size', len(RAW) + 1), ('last_write_100ns', 101),
])
@pytest.mark.parametrize('between_attempts', [False, True])
def test_identity_size_or_write_time_drift_never_gets_a_fresh_read(field, value, between_attempts):
    changed = replace(SNAPSHOT, **{field: value}, change_100ns=201)
    snapshots = ([SNAPSHOT, replace(SNAPSHOT, change_100ns=201), changed]
                 if between_attempts else [SNAPSHOT, changed])
    reader = Reader(snapshots)
    with pytest.raises(wp.WorkPrivacyBlocked, match='changed during inventory read'):
        wp._native_member_digest(reader, len(RAW) + 1)
    assert reader.reads == 1


@pytest.mark.parametrize('repeated_metadata_drift', [False, True])
def test_changed_retry_content_or_continued_metadata_drift_refuses(repeated_metadata_drift):
    settled = replace(SNAPSHOT, change_100ns=201)
    final = replace(settled, change_100ns=202) if repeated_metadata_drift else settled
    payload = RAW if repeated_metadata_drift else b'changed contents'
    assert len(payload) == len(RAW)
    reader = Reader([SNAPSHOT, settled, settled, final], (RAW, payload))
    with pytest.raises(wp.WorkPrivacyBlocked, match='changed during inventory read'):
        wp._native_member_digest(reader, len(RAW))
    assert reader.reads == reader.seeks == 2


def test_partial_initial_read_refuses_without_retry():
    reader = Reader([SNAPSHOT], (b'',))
    with pytest.raises(wp.WorkPrivacyBlocked, match='changed during inventory read'):
        wp._native_member_digest(reader, len(RAW))
    assert reader.reads == reader.seeks == 1


def test_oversized_initial_snapshot_refuses_without_reading():
    reader = Reader([SNAPSHOT])
    with pytest.raises(wp.WorkPrivacyBlocked, match='byte bound'):
        wp._native_member_digest(reader, len(RAW) - 1)
    assert reader.reads == reader.seeks == 0


@pytest.mark.skipif(os.name != 'nt', reason='native Windows attribute transition')
def test_native_metadata_only_transition_settles_through_the_same_handle(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic.bin'
    path.write_bytes(RAW)
    original = fs.WindowsQuarantineFile.read_chunk
    changed = False

    def read_then_change_metadata(opened, bound):
        nonlocal changed
        raw = original(opened, bound)
        if not changed:
            changed = True
            path.chmod(stat.S_IREAD)
        return raw

    monkeypatch.setattr(fs.WindowsQuarantineFile, 'read_chunk', read_then_change_metadata)
    try:
        with fs.WindowsQuarantineDirectory.open(path.parent, owner_private=False) as parent:
            with parent.open_file(path.name, private=False) as opened:
                identity = opened.identity
                result = wp._native_member_digest(opened, len(RAW))
                assert opened.identity == identity
        assert result == {'size': len(RAW), 'sha256': hashlib.sha256(RAW).hexdigest()}
        assert path.read_bytes() == RAW
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
