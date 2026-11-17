"""Synthetic recoverability bounds for authenticated Work stream checkpoints."""
from __future__ import annotations

import copy
import io
import json
from unittest.mock import Mock

import pytest

from catchup_core import checkpoint as ck
from catchup_core import recovery_quarantine as quarantine
from catchup_core.recovery_quarantine import QuarantineStore
from core_helpers import (
    STREAM_CIPHER as CIPHER,
    STREAM_ORDINARY as ORDINARY,
    STREAM_OWNER as OWNER,
    stream_checkpoint as _checkpoint,
    stream_identity as _identity,
    stream_key as _key,
    stream_rewrite_frames as _rewrite_frames,
    work_stream_quarantine_authority as _authority,
)

DENIAL = 'current Work invalidation or unjournaled dependency'


def _fragment_ordinary(path):
    def fragment(frames):
        index = next(index for index, (kind, _) in enumerate(frames) if kind == 'd')
        _, body = frames[index]
        frames[index:index + 1] = [('d', bytes([byte])) for byte in body]

    _rewrite_frames(path, fragment)


def test_fragmented_frames_are_counted_before_a_restore_destination_is_created(tmp_path, monkeypatch):
    path, _ = _checkpoint(tmp_path / 'work.ack', streams=[])
    _fragment_ordinary(path)
    monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY) - 1)
    destination = tmp_path / 'restored'
    calls = []

    with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
        ck.restore(path, destination, CIPHER, [], expected_profile='work',
                   replay_privacy=lambda _dest: calls.append('replay'),
                   quarantine_factory=lambda *_args: calls.append('factory'))

    assert not destination.exists()
    assert calls == []


def test_fragment_count_accepts_the_exact_limit_and_ignores_empty_frames(tmp_path, monkeypatch):
    path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[])
    _fragment_ordinary(path)

    def empty_frame(frames):
        index = next(index for index, (kind, _) in enumerate(frames) if kind == 'd')
        frames.insert(index, ('d', b''))

    _rewrite_frames(path, empty_frame)
    monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY))
    assert ck.verify(path, CIPHER) == manifest
    monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY) - 1)
    with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize(('limit', 'value'), [
    ('MAX_ENTRIES', 1),
    ('MAX_MEMBER_BYTES', len(ORDINARY) - 1),
    ('MAX_TOTAL_BYTES', len(ORDINARY)),
])
def test_quarantine_inventory_limits_are_checked_during_work_preflight(tmp_path, monkeypatch, limit, value):
    path, _ = _checkpoint(tmp_path / 'work.ack',
                          streams=[(_identity(OWNER, 'file', 'synthetic'), b'x')])
    monkeypatch.setattr(quarantine, limit, value)
    with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
        ck.verify(path, CIPHER)


def test_work_manifest_must_fit_the_quarantine_json_bound(tmp_path):
    path, _ = _checkpoint(tmp_path / 'work.ack', streams=[])

    def enlarge_manifest(frames):
        index = next(index for index, (kind, _) in enumerate(frames) if kind == 'm')
        manifest = json.loads(frames[index][1])
        manifest['padding'] = 'x' * quarantine.MAX_CATALOG_BYTES
        data = json.dumps(manifest, separators=(',', ':')).encode()
        frames[index:index + 1] = [('m', data[offset:offset + ck._CHUNK])
                                   for offset in range(0, len(data), ck._CHUNK)]

    _rewrite_frames(path, enlarge_manifest)
    with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
        ck.verify(path, CIPHER)


def test_capacity_accounts_for_real_catalog_affinity_and_denial_shape(tmp_path, monkeypatch):
    body = b'synthetic stream'
    identity = _identity(OWNER, 'file', 'synthetic')
    path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[(identity, body)])
    store = QuarantineStore(tmp_path / 'quarantine', CIPHER, authority=_authority)
    store.begin(manifest, 'c' * 64)
    for entry, content in zip([*manifest['entries'], *manifest['streams']], [ORDINARY, body], strict=True):
        store.start(entry)
        store.write(content)
        store.finish()
    store.set_recovery_affinity({'schema': 'artha.recovery-affinity.v1',
                                 'work_binding': manifest['context']['work_binding']})
    store.seal()
    store.block_paths([OWNER], DENIAL)
    store.block_streams([_key(identity)], DENIAL)
    catalog = store.load_catalog()
    actual_size = len(json.dumps(catalog, sort_keys=True, separators=(',', ':'),
                                 allow_nan=False).encode())
    manifest_size = len(json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                                   allow_nan=False).encode())
    assert manifest_size < actual_size

    monkeypatch.setattr(quarantine, 'MAX_AUTHORITY_BYTES', len(_authority()['owner']))
    monkeypatch.setattr(quarantine, 'MAX_CATALOG_BYTES', actual_size)
    assert ck.verify(path, CIPHER) == manifest
    monkeypatch.setattr(quarantine, 'MAX_CATALOG_BYTES', actual_size - 1)
    with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
        ck.verify(path, CIPHER)


def test_work_v3_refuses_the_legacy_plaintext_privacy_ledger_exception(tmp_path):
    def edit_manifest(manifest):
        manifest['roots'] = ['local']
        manifest['entries'][0]['kind'] = 'sqlite'
        manifest['context']['work_binding']['members'].clear()

    path, _ = _checkpoint(tmp_path / 'work.ack', streams=[],
                          member_path='local/catchup_core/ledger.db', edit_manifest=edit_manifest)

    def edit_header(frames):
        index = next(index for index, (kind, _) in enumerate(frames) if kind == 'h')
        header = json.loads(frames[index][1])
        header['kind'] = 'sqlite'
        frames[index] = ('h', json.dumps(header).encode())

    _rewrite_frames(path, edit_header)
    with pytest.raises(ck.CheckpointBlocked, match='ledger|quarantine'):
        ck.verify(path, CIPHER)


def test_personal_legacy_verification_does_not_acquire_work_quarantine_limits(tmp_path, monkeypatch):
    path, manifest = _checkpoint(tmp_path / 'personal.ack', magic=b'ACK2',
                                 schema='artha.checkpoint.v2', profile='personal', streams=[])
    monkeypatch.setattr(quarantine, 'MAX_CHUNKS', 0)
    monkeypatch.setattr(quarantine, 'MAX_MEMBER_BYTES', 0)
    assert ck.verify(path, CIPHER) == manifest


@pytest.mark.parametrize('provide_manifest', [False, True])
def test_work_extraction_without_quarantine_never_writes_plaintext(tmp_path, provide_manifest):
    path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[])
    destination = tmp_path / 'restored'
    destination.mkdir()

    with path.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='Work'):
        ck._extract(source, CIPHER, destination, lambda _digest: None,
                    manifest=manifest if provide_manifest else None)

    assert list(destination.iterdir()) == []


@pytest.mark.parametrize('manifest_kind', ['missing', 'legacy'])
def test_work_extraction_refuses_a_missing_or_wrong_manifest_before_quarantine(tmp_path, manifest_kind):
    path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[])
    supplied = None if manifest_kind == 'missing' else {**manifest, 'schema': 'artha.checkpoint.v2'}
    store = Mock()

    with path.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='Work'):
        ck._extract(source, CIPHER, tmp_path / 'restored', lambda _digest: None,
                    quarantine=store, manifest=supplied)

    assert store.mock_calls == []


def test_work_extraction_checks_capacity_before_quarantine_effects(tmp_path, monkeypatch):
    path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[])
    _fragment_ordinary(path)
    monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY) - 1)
    store = Mock()

    with path.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='quarantine'):
        ck._extract(source, CIPHER, tmp_path / 'restored', lambda _digest: None,
                    quarantine=store, manifest=manifest)

    assert store.mock_calls == []


def test_work_extraction_requires_the_exact_authenticated_manifest_before_effects(tmp_path):
    path, manifest = _checkpoint(
        tmp_path / 'work.ack', streams=[],
        edit_manifest=lambda value: value.update(synthetic_marker=1),
    )
    supplied = copy.deepcopy(manifest)
    supplied['synthetic_marker'] = True
    store = Mock()

    with path.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='manifest'):
        ck._extract(source, CIPHER, tmp_path / 'restored', lambda _digest: None,
                    quarantine=store, manifest=supplied)

    assert store.mock_calls == []


def test_work_extraction_rejects_replaced_authenticated_frames_before_effects(tmp_path):
    first, manifest = _checkpoint(tmp_path / 'first.ack', streams=[])
    replacement, replacement_manifest = _checkpoint(tmp_path / 'replacement.ack', streams=[])
    assert replacement_manifest == manifest
    assert replacement.read_bytes() != first.read_bytes()

    class SwapOnRewind(io.BytesIO):
        swapped = False

        def seek(self, offset, whence=0):
            if not self.swapped and offset == 0 and whence == 0 and self.tell():
                self.swapped = True
                super().seek(0)
                super().truncate()
                super().write(replacement.read_bytes())
            return super().seek(offset, whence)

    store = Mock()
    with SwapOnRewind(first.read_bytes()) as source, pytest.raises(ck.CheckpointError, match='changed'):
        ck._extract(source, CIPHER, tmp_path / 'restored', lambda _digest: None,
                    quarantine=store, manifest=manifest)

    assert source.swapped
    assert store.mock_calls == []


def test_work_extraction_uses_the_verified_manifest_after_caller_mutation(tmp_path):
    path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[])
    expected = copy.deepcopy(manifest['entries'][0])
    store = Mock()

    def mutate_caller_manifest(_digest):
        manifest['entries'][0]['size'] = 1

    with path.open('rb') as source:
        ck._extract(source, CIPHER, tmp_path / 'restored', mutate_caller_manifest,
                    quarantine=store, manifest=manifest)

    assert store.start.call_args.args[0] == expected


def test_personal_legacy_extraction_still_accepts_nonseekable_input(tmp_path):
    path, manifest = _checkpoint(tmp_path / 'personal.ack', magic=b'ACK2',
                                 schema='artha.checkpoint.v2', profile='personal', streams=[])
    destination = tmp_path / 'restored'
    destination.mkdir()

    class Nonseekable(io.BytesIO):
        def seek(self, *_args):
            raise io.UnsupportedOperation('not seekable')

        def tell(self):
            raise io.UnsupportedOperation('not seekable')

    with Nonseekable(path.read_bytes()) as source:
        assert ck._extract(source, CIPHER, destination, lambda _digest: None) == manifest
    assert (destination / 'work_state' / 'note.txt').read_bytes() == ORDINARY
