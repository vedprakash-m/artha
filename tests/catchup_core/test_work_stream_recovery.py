"""Work stream recovery authority with synthetic local history and ciphertext."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from _work_synthetic import install_synthetic_work_creation, reconcile_reviewed
from catchup_core import checkpoint as ck
from catchup_core import checkpoint_cli
from catchup_core import quarantine_review
from catchup_core import recovery_access
from catchup_core.recovery_access import WorkRecoveryAccess
from catchup_core.recovery_quarantine import QuarantineError
from catchup_core.work_privacy import WorkPrivacy
from test_work_privacy import Backend, KEY
from test_work_stream_checkpoint import CIPHER, GENERATION, ORDINARY, _checkpoint, _identity, _key


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    install_synthetic_work_creation(monkeypatch)
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    repo = tmp_path / 'repo'
    root = repo / 'state/work'
    (root / 'folder').mkdir(parents=True)
    (root / 'note.txt').write_bytes(ORDINARY)
    protocol = WorkPrivacy(
        repo, tmp_path / 'local', {'work_state': root}, backend=Backend(),
        native_identity=lambda: {'platform': 'win32', 'machine': 'synthetic', 'user': 'S-1-5-21-1001'},
    )
    protocol.enroll(KEY, reviewed=True, installation_id='synthetic-installation')
    bodies = [
        (_identity('work_state/note.txt', 'file', 'file-stream'), b'synthetic file stream'),
        (_identity('work_state/folder', 'directory', 'directory-stream'), b'synthetic directory stream'),
        (_identity('work_state', 'directory', 'root-stream'), b'synthetic root stream'),
        (_identity('external', 'directory', 'external-stream'), b'unjournaled dependency'),
    ]
    inventory = {_key(identity): {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
                 for identity, body in bodies if identity['owner'].startswith('work_state')}
    monkeypatch.setattr(protocol, '_inventory_streams', lambda stream_policy=None: dict(inventory))
    preview = protocol.preview_stream_history()
    protocol.upgrade_stream_history(reviewed=True, expected_digest=preview['inventory_sha256'])
    binding = protocol.bind_checkpoint()

    def bind(manifest):
        manifest['roots'] = ['work_state', 'external']
        manifest['context']['work_binding'] = binding

    source, manifest = _checkpoint(tmp_path / 'work.ack', streams=bodies, edit_manifest=bind)
    assert ck.verify(source, CIPHER) == manifest
    return SimpleNamespace(repo=repo, protocol=protocol, inventory=inventory, bodies=bodies,
                           source=source, manifest=manifest, dest=tmp_path / 'restored')


def _extract(scenario):
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    access.authorize_checkpoint(scenario.manifest)
    marker = json.dumps(ck._restore_marker(GENERATION)).encode()
    if os.name == 'nt':
        from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory
        with WindowsQuarantineDirectory.create(scenario.dest, lock_file=False) as root:
            with root.create_file('.artha-restored.json') as handle:
                handle.write_all(marker)
                handle.flush()
    else:
        scenario.dest.mkdir()
        (scenario.dest / '.artha-restored.json').write_bytes(marker)
    store = access.factory(scenario.dest, scenario.manifest, 'c' * 64)
    with scenario.source.open('rb') as source:
        ck._extract(source, CIPHER, scenario.dest, hashlib.sha256().update,
                    quarantine=store, manifest=scenario.manifest)
    access.replay(scenario.dest)
    return access


def test_endpointdlp_policy_survives_encrypted_quarantine_and_reopen(scenario):
    from test_work_stream_checkpoint import _omission_context
    preview = scenario.protocol.preview_stream_policy()
    scenario.protocol.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
    def bind(manifest):
        manifest['roots'] = ['work_state', 'external']
        _omission_context(manifest)
        manifest['context']['work_binding'] = scenario.protocol.bind_checkpoint()
    scenario.source, scenario.manifest = _checkpoint(scenario.source, streams=scenario.bodies, edit_manifest=bind)
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    access.authorize_checkpoint(scenario.manifest)
    report = ck.restore(scenario.source, scenario.dest, CIPHER, [], expected_profile='work',
                        replay_privacy=access.replay, quarantine_factory=access.factory)
    expected = scenario.manifest['context']['work_stream_omissions']
    assert report['work_stream_omissions'] == expected
    reopened = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    store = reopened.open_store(scenario.dest)
    assert store.load_catalog()['work_stream_omissions'] == expected
    assert len(store.inventory()) == 5
    catalog = store.load_catalog()
    for damage in (None, dict(expected, semantics='changed')):
        altered = copy.deepcopy(catalog)
        if damage is None:
            del altered['work_stream_omissions']
        else:
            altered['work_stream_omissions'] = damage
        with pytest.raises(ck.CheckpointBlocked, match='omission report'):
            access._validate_catalog(altered, access.binding)
    legacy = copy.deepcopy(catalog)
    legacy['recovery_affinity']['work_binding']['schema'] = 'artha.work-checkpoint-binding.v2'
    with pytest.raises(ck.CheckpointBlocked, match='legacy'):
        access._validate_catalog(legacy, legacy['recovery_affinity']['work_binding'])


def test_ciphertext_capture_fragmentation_does_not_multiply_source_scans(scenario, monkeypatch):
    from test_work_stream_checkpoint import _rewrite_frames

    original = scenario.protocol._inventory
    scans = []

    def count():
        value = original()
        scans.append(value)
        return value

    monkeypatch.setattr(scenario.protocol, '_inventory', count)
    _extract(scenario)
    ordinary_scans = len(scans)
    assert ordinary_scans > 0

    def split(frames):
        frames[:] = [(kind, piece) for kind, body in frames
                     for piece in ([bytes([value]) for value in body] if kind == 'd' else [body])]

    _rewrite_frames(scenario.source, split)
    scenario.dest = scenario.dest.with_name('fragmented-restore')
    scans.clear()
    _extract(scenario)
    assert len(scans) == ordinary_scans


@pytest.mark.parametrize('policy', [False, True])
def test_ciphertext_capture_refuses_same_size_primary_drift_at_completion(scenario, monkeypatch, policy):
    if policy:
        preview = scenario.protocol.preview_stream_policy()
        scenario.protocol.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
    original = CIPHER.encrypt
    changed = []

    def mutate(body, *, aad):
        result = original(body, aad=aad)
        if body == ORDINARY and not changed:
            (scenario.repo / 'state/work/note.txt').write_bytes(b'x' * len(ORDINARY))
            changed.append(True)
        return result

    monkeypatch.setattr(CIPHER, 'encrypt', mutate)
    with pytest.raises((ck.CheckpointBlocked, QuarantineError)):
        _extract(scenario)
    assert changed


def test_ciphertext_capture_refuses_native_head_change_between_chunks(scenario, monkeypatch):
    original = CIPHER.encrypt
    changed = []

    def revoke(body, *, aad):
        result = original(body, aad=aad)
        if body == ORDINARY and not changed:
            scenario.protocol.publish({}, revoke=['work_state/note.txt'])
            changed.append(True)
        return result

    monkeypatch.setattr(CIPHER, 'encrypt', revoke)
    with pytest.raises((ck.CheckpointBlocked, QuarantineError)):
        _extract(scenario)
    assert changed


@pytest.mark.parametrize('kind', ['primary', 'stream'])
def test_ciphertext_verification_refuses_source_drift_before_success(scenario, monkeypatch, kind):
    access = _extract(scenario)
    store = access.capture_store
    original = store._verify_member
    changed = []

    def mutate(*args):
        original(*args)
        if not changed:
            if kind == 'primary':
                (scenario.repo / 'state/work/note.txt').write_bytes(b'x' * len(ORDINARY))
            else:
                identity, body = scenario.bodies[0]
                scenario.inventory[_key(identity)] = {'size': len(body), 'sha256': 'f' * 64}
            changed.append(True)

    monkeypatch.setattr(store, '_verify_member', mutate)
    with pytest.raises((ck.CheckpointBlocked, QuarantineError)):
        store.verify()
    assert changed


def test_streams_are_quarantined_with_current_authority_and_only_documents_are_reviewable(scenario):
    access = _extract(scenario)
    metadata = access.open_store(scenario.dest)
    entries = metadata.inventory()
    file_entry = next(entry for entry in entries if entry['kind'] == 'file')
    streams = {entry['name']: entry for entry in entries if entry['kind'] == 'stream'}
    assert file_entry['state'] == streams['file-stream']['state'] == 'quarantined'
    assert all(streams[name]['state'] == 'blocked'
               for name in ('directory-stream', 'root-stream', 'external-stream'))
    with pytest.raises(QuarantineError):
        next(access.capture_store.iter_member(streams['file-stream']['member_id']))
    with pytest.raises(QuarantineError):
        next(metadata.iter_member(streams['file-stream']['member_id']))
    content = access.open_store(scenario.dest, export=True)
    with pytest.raises(QuarantineError):
        next(content.iter_member(streams['file-stream']['member_id']))
    with pytest.raises(quarantine_review.ReviewBlocked):
        quarantine_review.preview(content, streams['file-stream']['member_id'], 'note.txt',
                                  now=datetime(2026, 11, 6, tzinfo=UTC))
    review = quarantine_review.preview(content, file_entry['member_id'], 'note.txt',
                                       now=datetime(2026, 11, 6, tzinfo=UTC))
    assert review['review_digest']
    ck._verify_quarantine(access.capture_store, scenario.manifest, replayed=True)
    for path in (scenario.dest / '.artha-quarantine').iterdir():
        if path.name != '.lock':
            assert b'synthetic file stream' not in path.read_bytes()
            assert b'file-stream' not in path.read_bytes()


@pytest.mark.parametrize('change', ['revoke-owner', 'change-stream'])
def test_later_work_history_blocks_affected_streams(scenario, change):
    file_key = _key(scenario.bodies[0][0])
    if change == 'revoke-owner':
        scenario.protocol.publish({}, revoke=['work_state/note.txt'])
    else:
        scenario.inventory[file_key] = {'size': 3, 'sha256': hashlib.sha256(b'new').hexdigest()}
        reconcile_reviewed(scenario.protocol)
    access = _extract(scenario)
    entries = access.open_store(scenario.dest).inventory()
    owner = next(entry for entry in entries if entry['kind'] == 'file')
    stream = next(entry for entry in entries if entry.get('name') == 'file-stream')
    assert stream['state'] == 'blocked'
    assert owner['state'] == ('blocked' if change == 'revoke-owner' else 'quarantined')


@pytest.mark.parametrize('defect', ['missing', 'changed', 'added', 'wrong-owner'])
def test_authority_refuses_stream_manifest_disagreeing_with_authenticated_binding(scenario, defect):
    manifest = copy.deepcopy(scenario.manifest)
    if defect == 'missing':
        manifest['streams'].pop(0)
    elif defect == 'changed':
        manifest['streams'][0]['sha256'] = 'f' * 64
    elif defect == 'added':
        addition = dict(manifest['streams'][0], name='extra-stream')
        manifest['streams'].append(addition)
    else:
        manifest['streams'][0]['owner'] = 'work_state/other.txt'
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    with pytest.raises(ck.CheckpointBlocked):
        access.authorize_checkpoint(manifest)
    assert not scenario.dest.exists()


def test_stream_quarantine_list_never_discloses_stream_identity(scenario, monkeypatch):
    _extract(scenario)
    monkeypatch.setattr(checkpoint_cli, '_work_destination', lambda *_: None)
    monkeypatch.setattr(checkpoint_cli, '_work_privacy', lambda *_: scenario.protocol)
    monkeypatch.setattr(checkpoint_cli, '_cipher', lambda *_args, **_kwargs: CIPHER)
    args = SimpleNamespace(command='quarantine-list', details=True, profile='work',
                           path=str(scenario.dest), actor=None)
    result = checkpoint_cli._quarantine_command(args, None, scenario.protocol.local, None, None, 'win32')
    streams = [entry for entry in result['members'] if entry['kind'] == 'stream']
    assert len(streams) == 4
    assert all(set(entry) == {'member_id', 'size', 'kind', 'state'} for entry in streams)
    assert 'file-stream' not in json.dumps(result) and 'external-stream' not in json.dumps(result)


def test_authority_change_invalidates_an_existing_content_handle(scenario):
    access = _extract(scenario)
    content = access.open_store(scenario.dest, export=True)
    file_entry = next(entry for entry in content.inventory() if entry['kind'] == 'file')
    scenario.protocol.publish({'work_state/new.txt': b'new synthetic member'})
    with pytest.raises(QuarantineError):
        next(content.iter_member(file_entry['member_id']))


def test_new_recovery_access_replays_later_owner_revocation(scenario):
    _extract(scenario)
    scenario.protocol.publish({}, revoke=['work_state/note.txt'])
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    entries = access.open_store(scenario.dest).inventory()
    assert next(entry for entry in entries if entry['kind'] == 'file')['state'] == 'blocked'
    assert next(entry for entry in entries if entry.get('name') == 'file-stream')['state'] == 'blocked'


def test_authorized_binding_is_pinned_before_factory(scenario):
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    access.authorize_checkpoint(scenario.manifest)
    scenario.manifest['context']['work_binding']['streams'].clear()
    scenario.dest.mkdir()
    with pytest.raises(ck.CheckpointBlocked):
        access.factory(scenario.dest, scenario.manifest, 'c' * 64)


@pytest.mark.parametrize('operation', ['fence', 'verify'])
@pytest.mark.parametrize('defect', ['oversized', 'hardlink'])
def test_work_marker_refuses_unsafe_input_without_path_read(scenario, monkeypatch, operation, defect):
    access = _extract(scenario)
    store = access.capture_store
    assert store is not None
    catalog = store.load_catalog()
    marker = scenario.dest / '.artha-restored.json'
    if defect == 'oversized':
        marker.write_bytes(b' ' * 65537)
    else:
        substitute = scenario.dest.parent / 'linked-marker.json'
        substitute.write_text(json.dumps(ck._restore_marker(GENERATION)))
        marker.unlink()
        os.link(substitute, marker)

    read_bytes = Path.read_bytes

    def checked_read(path):
        if path == marker:
            raise AssertionError('unsafe Work marker was read by path')
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', checked_read)
    with pytest.raises(ck.CheckpointBlocked, match='fence|marker'):
        if operation == 'fence':
            access._check_fence(scenario.dest, store, catalog)
        else:
            ck._verify_quarantine(store, scenario.manifest, replayed=True)


def test_work_fence_rejects_an_unreadable_directory(scenario, monkeypatch):
    access = _extract(scenario)
    store = access.capture_store
    assert store is not None
    catalog = store.load_catalog()

    def unavailable_walk(*_args, **kwargs):
        if onerror := kwargs.get('onerror'):
            onerror(PermissionError('synthetic traversal denial'))
        return iter(())

    monkeypatch.setattr(store, 'verify', lambda: None)
    monkeypatch.setattr(recovery_access.os, 'walk', unavailable_walk)
    with pytest.raises(ck.CheckpointBlocked, match='inventory|fence'):
        access._check_fence(scenario.dest, store, catalog)


def test_work_fence_rejects_duplicate_marker_fields(scenario):
    access = _extract(scenario)
    store = access.capture_store
    assert store is not None
    marker = scenario.dest / '.artha-restored.json'
    raw = json.dumps(ck._restore_marker(GENERATION))
    marker.write_text(raw[:-1] + ', "ingestion": "disabled"}')
    with pytest.raises(ck.CheckpointBlocked, match='fence'):
        access._check_fence(scenario.dest, store, store.load_catalog())


def test_work_fence_rechecks_marker_after_quarantine_verification(scenario, monkeypatch):
    access = _extract(scenario)
    store = access.capture_store
    assert store is not None
    verify = store.verify

    def replace_marker():
        verify()
        (scenario.dest / '.artha-restored.json').write_text('{}')

    monkeypatch.setattr(store, 'verify', replace_marker)
    with pytest.raises(ck.CheckpointBlocked, match='fence'):
        access._check_fence(scenario.dest, store, store.load_catalog())


def test_work_quarantine_rechecks_marker_after_verification(scenario, monkeypatch):
    access = _extract(scenario)
    store = access.capture_store
    assert store is not None
    verify = store.verify

    def replace_marker():
        verify()
        (scenario.dest / '.artha-restored.json').write_text('{}')

    monkeypatch.setattr(store, 'verify', replace_marker)
    with pytest.raises(ck.CheckpointBlocked, match='fence'):
        ck._verify_quarantine(store, scenario.manifest, replayed=True)


def test_public_restore_preserves_streams_only_in_current_authority_quarantine(scenario):
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    access.authorize_checkpoint(scenario.manifest)
    before = scenario.source.read_bytes()
    result = ck.restore(scenario.source, scenario.dest, CIPHER, [], expected_profile='work',
                        replay_privacy=access.replay, quarantine_factory=access.factory)
    assert result['streams'] == result['quarantined_streams'] == len(scenario.bodies)
    stream_bytes = sum(len(body) for _, body in scenario.bodies)
    assert result['quarantined_stream_bytes'] == stream_bytes
    assert result['verified_bytes'] == len(ORDINARY) + stream_bytes
    assert scenario.source.read_bytes() == before
    assert not (scenario.dest / 'work_state').exists()
    entries = access.open_store(scenario.dest).inventory()
    assert len(entries) == 1 + len(scenario.bodies)
    assert sum(entry['state'] == 'blocked' for entry in entries) == 3
    assert result['blocked_streams'] == 3


def test_public_restore_replays_stream_revocation(scenario):
    scenario.protocol.publish({}, revoke=['work_state/note.txt'])
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    access.authorize_checkpoint(scenario.manifest)
    result = ck.restore(scenario.source, scenario.dest, CIPHER, [], expected_profile='work',
                        replay_privacy=access.replay, quarantine_factory=access.factory)
    assert result['blocked_streams'] == len(scenario.bodies)
    assert all(entry['state'] == 'blocked' for entry in access.open_store(scenario.dest).inventory())


def test_public_restore_refuses_a_changed_authority_before_quarantine(scenario):
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    access.authorize_checkpoint(scenario.manifest)
    scenario.protocol.publish({}, revoke=['work_state/note.txt'])
    with pytest.raises(ck.CheckpointBlocked, match='head changed'):
        ck.restore(scenario.source, scenario.dest, CIPHER, [], expected_profile='work',
                   replay_privacy=access.replay, quarantine_factory=access.factory)
    assert not scenario.dest.exists()


def test_public_restore_requires_preverified_work_authority(scenario):
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    with pytest.raises(ck.CheckpointBlocked, match='authority was not verified'):
        ck.restore(scenario.source, scenario.dest, CIPHER, [], expected_profile='work',
                   replay_privacy=access.replay, quarantine_factory=access.factory)
    assert not scenario.dest.exists()


def test_cli_recovery_reports_quarantine_support_and_blocked_streams(scenario, monkeypatch):
    monkeypatch.setattr(checkpoint_cli, '_authenticated_recovery', lambda *_: ({}, scenario.manifest))
    monkeypatch.setattr(checkpoint_cli, '_work_privacy', lambda *_: scenario.protocol)
    monkeypatch.setattr(checkpoint_cli, '_cipher', lambda *_args, **_kwargs: CIPHER)
    monkeypatch.setattr(checkpoint_cli, 'collect_escrow', lambda *_args, **_kwargs: {})
    out, err = io.StringIO(), io.StringIO()
    code = checkpoint_cli.run(['recovery', str(scenario.source)], out, err,
                              local=scenario.protocol.local, passphrase=lambda _: 'synthetic')
    assert code == 0, err.getvalue()
    result = json.loads(out.getvalue())
    assert result['work_authority_verified'] is True
    assert result['work_streams_blocked'] == 3
    assert result['work_stream_recovery'] == 'encrypted_quarantine_only'
    assert result['stream_plaintext_available'] is False
    assert 'file-stream' not in out.getvalue() and 'root-stream' not in out.getvalue()


def test_cli_recovery_refuses_unverified_stream_binding_before_escrow(scenario, monkeypatch):
    manifest = copy.deepcopy(scenario.manifest)
    manifest['context']['work_binding']['mac'] = 'f' * 64
    monkeypatch.setattr(checkpoint_cli, '_authenticated_recovery', lambda *_: ({}, manifest))
    monkeypatch.setattr(checkpoint_cli, '_work_privacy', lambda *_: scenario.protocol)
    monkeypatch.setattr(checkpoint_cli, '_cipher', lambda *_args, **_kwargs: CIPHER)
    monkeypatch.setattr(checkpoint_cli, 'collect_escrow', lambda *_args, **_kwargs: pytest.fail('escrow read'))
    out, err = io.StringIO(), io.StringIO()
    code = checkpoint_cli.run(['recovery', str(scenario.source)], out, err,
                              local=scenario.protocol.local, passphrase=lambda _: 'synthetic')
    assert code == 20 and not out.getvalue()
    assert 'file-stream' not in err.getvalue()


def test_public_restore_cleans_up_when_final_stream_accounting_loses_authority(scenario, monkeypatch):
    access = WorkRecoveryAccess(scenario.repo, scenario.protocol, CIPHER)
    access.authorize_checkpoint(scenario.manifest)
    verify_ledgers = ck._verify_replayed_ledgers

    def revoke_after_verification(*args):
        verify_ledgers(*args)
        scenario.protocol.publish({}, revoke=['work_state/note.txt'])

    monkeypatch.setattr(ck, '_verify_replayed_ledgers', revoke_after_verification)
    with pytest.raises((ck.CheckpointBlocked, QuarantineError)):
        ck.restore(scenario.source, scenario.dest, CIPHER, [], expected_profile='work',
                   replay_privacy=access.replay, quarantine_factory=access.factory)
    assert not scenario.dest.exists()
