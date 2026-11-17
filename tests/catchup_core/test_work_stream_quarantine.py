"""Synthetic Work stream quarantine; no live Work files or credentials."""
from __future__ import annotations

import copy
import hashlib

import pytest

from catchup_core import recovery_quarantine as quarantine
from catchup_core.checkpoint import CheckpointBlocked
from catchup_core.recovery_access import WorkRecoveryAccess
from catchup_core.recovery_quarantine import QuarantineError, QuarantineStore
from catchup_core.work_privacy import WorkPrivacyBlocked
from core_helpers import (
    STREAM_CIPHER as CIPHER,
    STREAM_ORDINARY as ORDINARY,
    stream_checkpoint as _checkpoint,
    stream_identity as _identity,
    stream_key as _key,
)


def _authority():
    return {'actor': 'S-1-5-21-1001', 'owner': 'S-1-5-21-1001', 'profile': 'work',
            'work': {'identity': 'a' * 64, 'generation': 1, 'head_sha256': 'b' * 64}}


@pytest.mark.parametrize('defect', ['legacy', 'bad-entry', 'bad-report'])
def test_endpointdlp_catalog_rejects_malformed_or_legacy_omission_reports(defect):
    catalog = {'schema': quarantine._STREAM_SCHEMA, 'checkpoint_id': 'synthetic',
               'checkpoint_sha256': 'c' * 64, 'profile': 'work', 'owner': _authority()['owner'],
               'entries': [], 'work_stream_omissions': {}}
    if defect == 'legacy':
        catalog['schema'] = quarantine._SCHEMA
    elif defect == 'bad-entry':
        catalog['entries'] = ['invalid']
    with pytest.raises(QuarantineError, match='omission report'):
        QuarantineStore._validate(catalog, _authority())


def _capture(tmp_path):
    bodies = [
        (_identity('work_state/note.txt', 'file', 'synthetic secret name'), b'file stream'),
        (_identity('work_state/folder', 'directory', 'directory stream'), b'directory stream'),
        (_identity('work_state', 'directory', 'root stream'), b'root stream'),
    ]
    _, manifest = _checkpoint(tmp_path / 'source.ack', streams=bodies)
    store = QuarantineStore(tmp_path / 'quarantine', CIPHER, authority=_authority)
    store.begin(manifest, 'c' * 64)
    for entry, data in zip([*manifest['entries'], *manifest['streams']],
                           [ORDINARY, *(body for _, body in bodies)], strict=True):
        store.start(entry)
        store.write(data)
        store.finish()
    return store, store.seal(), manifest, bodies


def test_typed_streams_remain_separate_from_paths_and_only_encrypted_on_disk(tmp_path):
    store, catalog, manifest, bodies = _capture(tmp_path)
    assert catalog['schema'] == 'artha.recovery-quarantine.v2'
    assert catalog['profile'] == 'work'
    assert [entry['kind'] for entry in catalog['entries']] == ['file', 'stream', 'stream', 'stream']
    assert all('path' not in entry for entry in catalog['entries'][1:])
    for member, (_, expected) in zip(catalog['entries'][1:], bodies, strict=True):
        assert b''.join(store.iter_member(member['member_id'])) == expected
    reopened = QuarantineStore(store.root, CIPHER, authority=_authority)
    assert reopened.verify()['members'] == 4
    assert reopened.inventory()[1]['owner'] == manifest['streams'][0]['owner']
    for path in store.root.iterdir():
        if path.name != '.lock':
            raw = path.read_bytes()
            assert raw.startswith(b'AE1')
            assert b'synthetic secret name' not in raw
            assert b'file stream' not in raw
        assert 'synthetic secret name' not in path.name


@pytest.mark.parametrize('which', ['owner', 'owner_kind', 'name', 'stream_type'])
def test_stream_identity_is_authenticated_in_each_chunk(tmp_path, which):
    store, _, _, _ = _capture(tmp_path)
    with store._opened(store._context()) as descriptor:
        catalog, raw = store._load(descriptor, store._context())
        stream = catalog['entries'][1]
        replacements = {'owner': 'work_state/other.txt', 'owner_kind': 'directory',
                        'name': 'other name', 'stream_type': '$OTHER'}
        stream[which] = replacements[which]
        store._publish(descriptor, catalog, store._context(), previous=raw)
    with pytest.raises(QuarantineError):
        store.verify()


def test_stream_ciphertext_cannot_be_substituted_between_typed_identities(tmp_path):
    store, _, _, _ = _capture(tmp_path)
    with store._opened(store._context()) as descriptor:
        catalog, raw = store._load(descriptor, store._context())
        first = catalog['entries'][1]['chunks'][0]
        second = catalog['entries'][3]['chunks'][0]
        first_path = store.root / first['name']
        second_path = store.root / second['name']
        first_bytes, second_bytes = first_path.read_bytes(), second_path.read_bytes()
        assert len(first_bytes) == len(second_bytes)
        first_path.write_bytes(second_bytes)
        second_path.write_bytes(first_bytes)
        first['sha256'] = hashlib.sha256(second_bytes).hexdigest()
        second['sha256'] = hashlib.sha256(first_bytes).hexdigest()
        store._publish(descriptor, catalog, store._context(), previous=raw)
    with pytest.raises(QuarantineError, match='authentication'):
        store.verify()


def test_stream_denial_is_permanent_and_does_not_block_ordinary_owner(tmp_path):
    store, catalog, _, bodies = _capture(tmp_path)
    identity = _key(bodies[0][0])
    store.block_streams([identity], 'current Work stream invalidation')
    entries = store.inventory()
    assert entries[0]['state'] == 'quarantined'
    assert entries[1]['state'] == 'blocked'
    assert b''.join(store.iter_member(entries[0]['member_id'])) == ORDINARY
    with pytest.raises(QuarantineError):
        next(store.iter_member(catalog['entries'][1]['member_id']))
    reopened = QuarantineStore(store.root, CIPHER, authority=_authority)
    assert reopened.inventory()[1]['state'] == 'blocked'


def test_file_denial_does_not_treat_streams_as_colon_paths(tmp_path):
    store, _, _, _ = _capture(tmp_path)
    store.block_paths(['work_state/note.txt'], 'current Work invalidation')
    entries = store.inventory()
    assert entries[0]['state'] == 'blocked'
    assert all(entry['state'] == 'quarantined' for entry in entries[1:])
    with pytest.raises(QuarantineError):
        store.block_paths(['work_state/note.txt:synthetic secret name'], 'invalid path')


@pytest.mark.parametrize('defect', ['personal', 'legacy', 'no-stream-list', 'duplicate',
                                   'case-alias', 'orphan', 'invalid-name', 'extra-field'])
def test_invalid_stream_manifest_refused_before_creating_quarantine(tmp_path, defect):
    _, manifest = _checkpoint(tmp_path / 'source.ack')
    manifest = copy.deepcopy(manifest)
    context = _authority()
    if defect == 'personal':
        manifest['profile'] = 'personal'
        context = {'actor': 'owner', 'owner': 'owner', 'profile': 'personal', 'privacy': {
            'personal': {'checkpoint_id': 1, 'permission_generation': 0, 'tombstone_digest': 'd' * 64}}}
    elif defect == 'legacy':
        manifest['schema'] = 'artha.checkpoint.v2'
    elif defect == 'no-stream-list':
        del manifest['streams']
    elif defect == 'duplicate':
        manifest['streams'].append(copy.deepcopy(manifest['streams'][0]))
    elif defect == 'case-alias':
        alias = copy.deepcopy(manifest['streams'][0])
        alias['name'] = alias['name'].upper()
        manifest['streams'].append(alias)
    elif defect == 'orphan':
        manifest['streams'][0]['owner'] = 'work_state/missing.txt'
    elif defect == 'invalid-name':
        manifest['streams'][0]['name'] = 'synthetic:invalid'
    else:
        manifest['streams'][0]['path'] = 'synthetic:invalid'
    store = QuarantineStore(tmp_path / 'quarantine', CIPHER, authority=lambda: context)
    with pytest.raises(QuarantineError) as caught:
        store.begin(manifest, 'c' * 64)
    assert 'synthetic:invalid' not in str(caught.value)
    assert not store.root.exists()


def test_old_catalog_reader_refuses_work_stream_schema(tmp_path, monkeypatch):
    store, _, _, _ = _capture(tmp_path)
    monkeypatch.setattr(quarantine, '_STREAM_SCHEMA', 'not-the-supported-schema')
    with pytest.raises(QuarantineError):
        store.load_catalog()


def test_stream_checkpoint_requires_authenticated_work_authority(tmp_path):
    class UntrustedAuthority:
        def check_checkpoint(self, _binding):
            raise WorkPrivacyBlocked('untrusted synthetic binding')

    _, manifest = _checkpoint(tmp_path / 'source.ack')
    access = WorkRecoveryAccess(tmp_path / 'repo', UntrustedAuthority(), CIPHER)
    with pytest.raises(CheckpointBlocked, match='history'):
        access.authorize_checkpoint(manifest)


def _fragment_ordinary(path):
    from core_helpers import stream_rewrite_frames as _rewrite_frames

    def fragment(frames):
        index = next(index for index, (kind, _) in enumerate(frames) if kind == 'd')
        _, body = frames[index]
        frames[index:index + 1] = [('d', bytes([byte])) for byte in body]

    _rewrite_frames(path, fragment)


class TestWorkStreamCapacity:
    """Synthetic recoverability bounds for authenticated Work stream checkpoints."""

    def test_fragmented_frames_and_exact_chunk_limit(self, tmp_path, monkeypatch):
        from catchup_core import checkpoint as ck
        from core_helpers import stream_rewrite_frames as _rewrite_frames

        path, _ = _checkpoint(tmp_path / 'work.ack', streams=[])
        _fragment_ordinary(path)
        monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY) - 1)
        destination = tmp_path / 'restored'
        calls = []

        with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
            ck.restore(path, destination, CIPHER, [], expected_profile='work',
                       replay_privacy=lambda _dest: calls.append('replay'),
                       quarantine_factory=lambda *_args: calls.append('factory'))
        assert not destination.exists() and calls == []

        path2, manifest = _checkpoint(tmp_path / 'exact.ack', streams=[])
        _fragment_ordinary(path2)

        def empty_frame(frames):
            index = next(index for index, (kind, _) in enumerate(frames) if kind == 'd')
            frames.insert(index, ('d', b''))

        _rewrite_frames(path2, empty_frame)
        monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY))
        assert ck.verify(path2, CIPHER) == manifest
        monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY) - 1)
        with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
            ck.verify(path2, CIPHER)

    @pytest.mark.parametrize(('limit', 'value'), [
        ('MAX_ENTRIES', 1),
        ('MAX_MEMBER_BYTES', len(ORDINARY) - 1),
        ('MAX_TOTAL_BYTES', len(ORDINARY)),
    ])
    def test_quarantine_inventory_limits_are_checked_during_work_preflight(self, tmp_path, monkeypatch, limit, value):
        from catchup_core import checkpoint as ck
        from core_helpers import STREAM_OWNER as OWNER

        path, _ = _checkpoint(tmp_path / 'work.ack', streams=[(_identity(OWNER, 'file', 'synthetic'), b'x')])
        monkeypatch.setattr(quarantine, limit, value)
        with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
            ck.verify(path, CIPHER)

    def test_manifest_catalog_bound_and_legacy_checks(self, tmp_path, monkeypatch):
        import json
        from catchup_core import checkpoint as ck
        from core_helpers import (
            STREAM_OWNER as OWNER,
            stream_rewrite_frames as _rewrite_frames,
            work_stream_quarantine_authority as _cap_authority,
        )

        path, _ = _checkpoint(tmp_path / 'bound.ack', streams=[])

        def enlarge_manifest(frames):
            index = next(index for index, (kind, _) in enumerate(frames) if kind == 'm')
            manifest = json.loads(frames[index][1])
            manifest['padding'] = 'x' * quarantine.MAX_CATALOG_BYTES
            data = json.dumps(manifest, separators=(',', ':')).encode()
            frames[index:index + 1] = [('m', data[offset:offset + ck._CHUNK]) for offset in range(0, len(data), ck._CHUNK)]

        _rewrite_frames(path, enlarge_manifest)
        with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
            ck.verify(path, CIPHER)

        body = b'synthetic stream'
        identity = _identity(OWNER, 'file', 'synthetic')
        path2, manifest = _checkpoint(tmp_path / 'cap.ack', streams=[(identity, body)])
        store = QuarantineStore(tmp_path / 'quarantine', CIPHER, authority=_cap_authority)
        store.begin(manifest, 'c' * 64)
        for entry, content in zip([*manifest['entries'], *manifest['streams']], [ORDINARY, body], strict=True):
            store.start(entry)
            store.write(content)
            store.finish()
        store.set_recovery_affinity({'schema': 'artha.recovery-affinity.v1',
                                     'work_binding': manifest['context']['work_binding']})
        store.seal()
        denial = 'current Work invalidation or unjournaled dependency'
        store.block_paths([OWNER], denial)
        store.block_streams([_key(identity)], denial)
        actual_size = len(json.dumps(store.load_catalog(), sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
        monkeypatch.setattr(quarantine, 'MAX_AUTHORITY_BYTES', len(_cap_authority()['owner']))
        monkeypatch.setattr(quarantine, 'MAX_CATALOG_BYTES', actual_size)
        assert ck.verify(path2, CIPHER) == manifest
        monkeypatch.setattr(quarantine, 'MAX_CATALOG_BYTES', actual_size - 1)
        with pytest.raises(ck.CheckpointBlocked, match='quarantine'):
            ck.verify(path2, CIPHER)

    def test_v3_sqlite_denial_and_personal_legacy_verification(self, tmp_path, monkeypatch):
        import json
        from catchup_core import checkpoint as ck
        from core_helpers import stream_rewrite_frames as _rewrite_frames

        def edit_manifest(manifest):
            manifest['roots'] = ['local']
            manifest['entries'][0]['kind'] = 'sqlite'
            manifest['context']['work_binding']['members'].clear()

        path, _ = _checkpoint(tmp_path / 'sql.ack', streams=[],
                              member_path='local/catchup_core/ledger.db', edit_manifest=edit_manifest)

        def edit_header(frames):
            index = next(index for index, (kind, _) in enumerate(frames) if kind == 'h')
            header = json.loads(frames[index][1])
            header['kind'] = 'sqlite'
            frames[index] = ('h', json.dumps(header).encode())

        _rewrite_frames(path, edit_header)
        with pytest.raises(ck.CheckpointBlocked, match='ledger|quarantine'):
            ck.verify(path, CIPHER)

        p_path, p_manifest = _checkpoint(tmp_path / 'personal.ack', magic=b'ACK2',
                                         schema='artha.checkpoint.v2', profile='personal', streams=[])
        monkeypatch.setattr(quarantine, 'MAX_CHUNKS', 0)
        monkeypatch.setattr(quarantine, 'MAX_MEMBER_BYTES', 0)
        assert ck.verify(p_path, CIPHER) == p_manifest

    @pytest.mark.parametrize('provide_manifest', [False, True])
    def test_work_extraction_without_quarantine_never_writes_plaintext(self, tmp_path, provide_manifest):
        from catchup_core import checkpoint as ck

        path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[])
        destination = tmp_path / 'restored'
        destination.mkdir()
        with path.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='Work'):
            ck._extract(source, CIPHER, destination, lambda _digest: None,
                        manifest=manifest if provide_manifest else None)
        assert list(destination.iterdir()) == []

    def test_work_extraction_prechecks_swap_mutation_and_legacy_nonseekable(self, tmp_path, monkeypatch):
        import io
        from unittest.mock import Mock
        from catchup_core import checkpoint as ck

        path, manifest = _checkpoint(tmp_path / 'work.ack', streams=[])
        store = Mock()
        for bad in (None, {**manifest, 'schema': 'artha.checkpoint.v2'}):
            with path.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='Work'):
                ck._extract(source, CIPHER, tmp_path / 'r1', lambda _digest: None, quarantine=store, manifest=bad)

        _fragment_ordinary(path)
        monkeypatch.setattr(quarantine, 'MAX_CHUNKS', len(ORDINARY) - 1)
        with path.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='quarantine'):
            ck._extract(source, CIPHER, tmp_path / 'r2', lambda _digest: None, quarantine=store, manifest=manifest)
        monkeypatch.undo()

        p_mut, m_mut = _checkpoint(tmp_path / 'mut.ack', streams=[], edit_manifest=lambda v: v.update(synthetic_marker=1))
        supplied = copy.deepcopy(m_mut)
        supplied['synthetic_marker'] = True
        with p_mut.open('rb') as source, pytest.raises(ck.CheckpointBlocked, match='manifest'):
            ck._extract(source, CIPHER, tmp_path / 'r3', lambda _digest: None, quarantine=store, manifest=supplied)
        assert store.mock_calls == []

        first, f_man = _checkpoint(tmp_path / 'first.ack', streams=[])
        repl, r_man = _checkpoint(tmp_path / 'repl.ack', streams=[])
        assert r_man == f_man and repl.read_bytes() != first.read_bytes()

        class SwapOnRewind(io.BytesIO):
            swapped = False

            def seek(self, offset, whence=0):
                if not self.swapped and offset == 0 and whence == 0 and self.tell():
                    self.swapped = True
                    super().seek(0)
                    super().truncate()
                    super().write(repl.read_bytes())
                return super().seek(offset, whence)

        with SwapOnRewind(first.read_bytes()) as source, pytest.raises(ck.CheckpointError, match='changed'):
            ck._extract(source, CIPHER, tmp_path / 'r4', lambda _digest: None, quarantine=store, manifest=f_man)
        assert source.swapped and store.mock_calls == []

        p_ok, m_ok = _checkpoint(tmp_path / 'ok.ack', streams=[])
        expected = copy.deepcopy(m_ok['entries'][0])
        with p_ok.open('rb') as source:
            ck._extract(source, CIPHER, tmp_path / 'r5', lambda _d: m_ok['entries'][0].__setitem__('size', 1),
                        quarantine=store, manifest=m_ok)
        assert store.start.call_args.args[0] == expected

        leg, m_leg = _checkpoint(tmp_path / 'leg.ack', magic=b'ACK2', schema='artha.checkpoint.v2', profile='personal', streams=[])
        dest = tmp_path / 'restored_leg'
        dest.mkdir()

        class Nonseekable(io.BytesIO):
            def seek(self, *_args):
                raise io.UnsupportedOperation('not seekable')

            def tell(self):
                raise io.UnsupportedOperation('not seekable')

        with Nonseekable(leg.read_bytes()) as source:
            assert ck._extract(source, CIPHER, dest, lambda _digest: None) == m_leg
        assert (dest / 'work_state' / 'note.txt').read_bytes() == ORDINARY
