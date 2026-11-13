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
from test_work_stream_checkpoint import CIPHER, ORDINARY, _checkpoint, _identity, _key


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
