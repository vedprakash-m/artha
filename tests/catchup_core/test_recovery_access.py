"""Trusted recovery authorization; synthetic stores and static keys only."""
from __future__ import annotations

import copy
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import yaml
from test_checkpoint_restore_integrity import CIPHER, container

from catchup_core import checkpoint as ck
from catchup_core import recovery_access as recovery
from catchup_core.backup import database_bytes
from catchup_core.checkpoint import CheckpointBlocked
from catchup_core.crypto import KeyUnavailableError
from catchup_core.ids import HmacKey
from catchup_core.ledger import FactInput, Ledger, SignalInput
from catchup_core.recovery_access import RecoveryAccess
from catchup_core.recovery_quarantine import QuarantineError, QuarantineStore

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def write_restored_ledger(path, data):
    if os.name == 'nt':
        from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory

        path.parent.parent.mkdir(parents=True, exist_ok=True)
        with (WindowsQuarantineDirectory.create(path.parent, lock_file=False) as parent,
              parent.create_file(path.name) as handle):
            handle.write_all(data)
            handle.flush()
    else:
        path.parent.mkdir(parents=True)
        path.write_bytes(data)


@pytest.mark.parametrize('profile', ['work', None, 'unknown'])
def test_factory_refuses_nonpersonal_before_creating_ciphertext(tmp_path, profile):
    destination = tmp_path / 'restored'
    destination.mkdir()
    access = RecoveryAccess(tmp_path / 'repo', None, CIPHER, now=lambda: NOW)
    with pytest.raises(CheckpointBlocked):
        access.factory(destination, {'profile': profile, 'entries': []}, 'a' * 64)
    assert not list(destination.iterdir())


def test_factory_requires_an_actual_manifest_privacy_ledger(tmp_path):
    destination = tmp_path / 'restored'
    destination.mkdir()
    access = RecoveryAccess(tmp_path / 'repo', None, CIPHER, now=lambda: NOW)
    with pytest.raises(CheckpointBlocked):
        access.factory(destination, {'profile': 'personal', 'entries': []}, 'a' * 64)
    assert not list(destination.iterdir())


class TrustedSealedStore:
    def __init__(self):
        self.checkpoint = None
        self.history = {'tombstones': [], 'consent': []}
        self.read_hook = None
        self.replay_hook = None

    def read(self):
        if self.read_hook:
            self.read_hook()
        return self.checkpoint

    def write(self, checkpoint):
        self.checkpoint = checkpoint

    def replay(self):
        result = copy.deepcopy(self.history)
        if self.replay_hook:
            self.replay_hook()
        return result

    def refresh(self, ledger):
        ledger.seal_privacy()
        self.history = ledger.privacy_history()


class NoNative:
    def get_password(self, service, account):
        raise AssertionError('no native key access authorized')

    def set_password(self, *args):
        raise AssertionError('no key enrollment authorized')

    def delete_password(self, *args):
        raise AssertionError('no native mutation authorized')


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True)
    config = {'viewer': 'owner', 'subjects': ['owner', 'adult'], 'consent_required': ['adult'],
              'sources': {'mail': {'connector': 'school_email', 'account_scope': 'test'}}}
    (repo / 'config/catchup_core.yaml').write_text(yaml.safe_dump(config))
    sealed = TrustedSealedStore()
    ledger = Ledger(tmp_path / 'source/catchup_core/ledger.db', id_key=HmacKey(1, bytes([81]) * 32),
                    cipher=CIPHER, sealed=sealed, owner_id='owner', clock=lambda: NOW.isoformat()).open()
    ledger.migrate()
    sealed.refresh(ledger)
    access = RecoveryAccess(repo, NoNative(), CIPHER, sealed_for=lambda mode: sealed, now=lambda: NOW)
    result = SimpleNamespace(repo=repo, config=config, sealed=sealed, ledger=ledger, access=access,
                             dest=tmp_path / 'restored', root=tmp_path,
                             ledger_name='local/catchup_core/ledger.db')
    yield result
    ledger.close()


def checkpoint(setup, *, extra=(), profile='personal', name='original.ack'):
    return container(setup.root / name, [
        (setup.ledger_name, database_bytes(setup.ledger._path), 'sqlite', 0o600),
        ('repo/notes.txt', b'SYNTHETIC-PRIVATE-NOTES', 'file', 0o600), *extra], profile=profile)


def restore(setup, original=None, destination=None):
    return ck.restore(original or checkpoint(setup), destination or setup.dest, CIPHER, [setup.repo],
                      quarantine_factory=setup.access.factory, replay_privacy=setup.access.replay)


def config_write(setup):
    (setup.repo / 'config/catchup_core.yaml').write_text(yaml.safe_dump(setup.config))


def test_real_restore_encrypts_all_raw_notes_and_replays_without_sidecars(setup):
    original = checkpoint(setup)
    raw = original.read_bytes()
    report = restore(setup, original)
    store = setup.access.open_store(setup.dest)
    assert isinstance(store, QuarantineStore)
    assert report['quarantined_files'] == 1
    assert store.authority()['privacy'] == {'authoritative': vars(setup.sealed.checkpoint)}
    assert store.authority()['policy']['configuration']
    assert original.read_bytes() == raw
    assert not (setup.dest / 'repo/notes.txt').exists()
    assert not list(setup.dest.rglob('*-wal'))
    assert not list(setup.dest.rglob('*-shm'))
    assert not list(setup.dest.rglob('*-journal'))
    for path in (setup.dest / '.artha-quarantine').iterdir():
        if path.name == '.lock':
            assert path.read_bytes() == b''
        else:
            assert path.read_bytes().startswith(b'AE1')
        assert b'SYNTHETIC-PRIVATE-NOTES' not in path.read_bytes()
    with pytest.raises(QuarantineError, match='export'):
        list(store.iter_member(store.inventory()[0]['member_id']))


def test_known_forget_removes_fact_and_blocks_exact_artifact_and_rerestore(setup):
    signal_id, _ = setup.ledger.upsert_signal(SignalInput(
        'school_email', 'test', 'message1', 'v1', NOW.isoformat(), 'content1', 'evd_test'))
    fact_id = setup.ledger.put_fact(FactInput(domain='kids_school', predicate='deadline', value='synthetic',
        primary_subject_id='owner', observed_at=NOW.isoformat(), evidence=((signal_id, 0, 1),)))
    artifact = 'local/catchup_core/blobs/evd_test.blob'
    original = checkpoint(setup, extra=[(artifact, b'SYNTHETIC-FORGOTTEN', 'file', 0o600)])
    setup.ledger.forget('school_email', 'test', 'message1')
    setup.sealed.history = setup.ledger.privacy_history()
    for destination in (setup.dest, setup.root / 'restored-again'):
        restore(setup, original, destination)
        store = setup.access.open_store(destination)
        entries = {entry['path']: entry for entry in store.inventory()}
        assert entries[artifact]['state'] == 'blocked'
        assert entries[artifact]['reason'] == 'current erasure'
        assert entries['repo/notes.txt']['state'] == 'quarantined'
        with closing(sqlite3.connect(destination / setup.ledger_name)) as db:
            assert db.execute('SELECT 1 FROM facts WHERE fact_id=?', (fact_id,)).fetchone() is None
        reopened = setup.access.open_store(destination)
        assert next(entry for entry in reopened.inventory() if entry['path'] == artifact)['state'] == 'blocked'


@pytest.mark.parametrize('operations,scope,audience,grantor,expiry', [
    (['view'], 'recovery', 'owner', 'adult', None),
    (['export'], 'recovery', 'owner', 'adult', None),
    (['view', 'export'], 'kids_school', 'owner', 'adult', None),
    (['view', 'export'], 'recovery', 'stranger', 'adult', None),
    (['view', 'export'], 'recovery', 'owner', 'owner', None),
    (['view', 'export'], 'recovery', 'owner', 'adult', '2026-10-24T00:00:00Z'),
])
def test_export_requires_current_self_granted_recovery_view_and_export(
        setup, operations, scope, audience, grantor, expiry):
    setup.ledger.grant_consent('adult', grantor, scope, operations, audience, expires_at=expiry)
    setup.sealed.refresh(setup.ledger)
    restore(setup)
    assert setup.access.open_store(setup.dest).inventory()
    with pytest.raises(CheckpointBlocked):
        setup.access.open_store(setup.dest, export=True)


@pytest.mark.parametrize('scope,audience', [('recovery', 'owner'), ('*', '*')])
def test_export_current_self_consent_allows_in_memory_content_and_revocation_blocks_old_store(setup, scope, audience):
    consent_id = setup.ledger.grant_consent('adult', 'adult', scope, ['view', 'export'], audience)
    setup.sealed.refresh(setup.ledger)
    restore(setup)
    store = setup.access.open_store(setup.dest, export=True)
    member_id = store.inventory()[0]['member_id']
    assert b''.join(store.iter_member(member_id)) == b'SYNTHETIC-PRIVATE-NOTES'
    setup.ledger.revoke_consent(consent_id)
    setup.sealed.history = setup.ledger.privacy_history()
    with pytest.raises(QuarantineError):
        list(store.iter_member(member_id))
    setup.access.replay(setup.dest)
    with pytest.raises(CheckpointBlocked):
        setup.access.open_store(setup.dest, export=True)
    assert setup.access.open_store(setup.dest).inventory()


def test_missing_current_privacy_refuses_before_any_historical_extraction(setup):
    original = checkpoint(setup)
    setup.sealed.checkpoint = None
    with pytest.raises(CheckpointBlocked, match='privacy'):
        restore(setup, original)
    assert not setup.dest.exists()


def test_wrong_explicit_actor_cannot_restore_or_reopen(setup):
    original = checkpoint(setup)
    setup.access.actor = 'adult'
    with pytest.raises(CheckpointBlocked, match='owner'):
        restore(setup, original)
    setup.access.actor = None
    restore(setup, original)
    setup.access.actor = 'adult'
    with pytest.raises(CheckpointBlocked, match='owner'):
        setup.access.open_store(setup.dest)


def test_unknown_historical_subject_cannot_export_even_with_self_consent(setup):
    setup.ledger.grant_consent('stranger', 'stranger', 'recovery', ['view', 'export'], 'owner')
    setup.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner')
    setup.sealed.refresh(setup.ledger)
    restore(setup)
    with pytest.raises(CheckpointBlocked, match='unknown'):
        setup.access.open_store(setup.dest, export=True)


def test_historical_consent_still_protects_subject_removed_from_required_config(setup):
    setup.ledger.grant_consent('adult', 'adult', 'kids_school', ['view'], 'owner')
    setup.sealed.refresh(setup.ledger)
    setup.config['consent_required'] = []
    config_write(setup)
    restore(setup)
    with pytest.raises(CheckpointBlocked):
        setup.access.open_store(setup.dest, export=True)


def test_config_digest_invalidates_an_open_store_without_privacy_change(setup):
    setup.config['consent_required'] = []
    config_write(setup)
    restore(setup)
    store = setup.access.open_store(setup.dest, export=True)
    member = store.inventory()[0]['member_id']
    setup.config['bootstrap_days'] = 3
    config_write(setup)
    with pytest.raises(QuarantineError):
        list(store.iter_member(member))


def test_history_head_change_during_read_refuses_and_preserves_original(setup):
    original = checkpoint(setup)
    raw = original.read_bytes()
    setup.sealed.replay_hook = lambda: setup.ledger.seal_privacy()
    with pytest.raises(CheckpointBlocked, match='head changed'):
        restore(setup, original)
    assert not setup.dest.exists() and original.read_bytes() == raw


def test_inconsistent_history_refuses_before_extraction(setup):
    original = checkpoint(setup)
    setup.sealed.history['tombstones'] = [{'tombstone_id': 'tmb_unknown'}]
    with pytest.raises(ValueError, match='checkpoint'):
        restore(setup, original)
    assert not setup.dest.exists()


@pytest.mark.parametrize('damage', ['missing', 'ingestion', 'effects', 'contradictory', 'profile'])
def test_fence_and_catalog_profile_are_required_on_reopen(setup, damage):
    restore(setup)
    marker = setup.dest / '.artha-restored.json'
    if damage == 'missing':
        marker.unlink()
    elif damage == 'profile':
        other = RecoveryAccess(setup.repo, NoNative(), CIPHER, sealed_for=lambda mode: setup.sealed,
                               now=lambda: NOW)
        original = ck.verify(checkpoint(setup, profile='work', name='work.ack'), CIPHER)
        with pytest.raises(CheckpointBlocked):
            other.factory(setup.dest, original, 'b' * 64)
        return
    else:
        value = json.loads(marker.read_bytes())
        field = 'effects_disabled' if damage == 'contradictory' else damage
        value[field] = False if damage == 'contradictory' else 'enabled'
        marker.write_text(json.dumps(value))
    with pytest.raises((CheckpointBlocked, OSError)):
        setup.access.open_store(setup.dest)


def test_shadow_namespace_comes_from_actual_ledger_parent(setup):
    setup.ledger_name = 'local/catchup_core/shadow/ledger.db'
    modes = []

    def sealed_for(mode):
        modes.append(mode)
        return setup.sealed

    setup.access.sealed_for = sealed_for
    restore(setup)
    assert set(modes) == {'shadow'}
    assert set(setup.access.open_store(setup.dest).authority()['privacy']) == {'shadow'}


def test_owner_only_config_allows_export_but_unknown_fact_subject_refuses(setup):
    setup.config['subjects'] = ['owner']
    setup.config['consent_required'] = []
    config_write(setup)
    setup.ledger.put_fact(FactInput(domain='kids_school', predicate='deadline', value='synthetic',
        primary_subject_id='unknown', observed_at=NOW.isoformat()))
    restore(setup)
    with pytest.raises(CheckpointBlocked, match='unknown'):
        setup.access.open_store(setup.dest, export=True)


def test_no_enrollment_when_household_identity_key_is_missing(setup):
    setup.config['household_from_profile'] = True
    config_write(setup)
    with pytest.raises(KeyUnavailableError):
        restore(setup)


def test_household_uses_read_only_identity_key_and_pack_local_day(setup):
    class IdentityBackend(NoNative):
        def get_password(self, service, account):
            assert service == 'artha-ledger-ids'
            if account == 'current_version':
                return '1'
            if account == 'key.1':
                import base64
                return base64.b64encode(bytes([82]) * 32).decode()
            raise AssertionError(account)

    setup.config['household_from_profile'] = True
    config_write(setup)
    (setup.repo / 'config/user_profile.yaml').write_text(yaml.safe_dump({'family': {
        'primary_user': {'name': 'Synthetic Owner'},
        'spouse': {'name': 'Synthetic Adult', 'id': 'adult'}, 'children': []}}))
    setup.access.backend = IdentityBackend()
    restore(setup)
    with pytest.raises(CheckpointBlocked):
        setup.access.open_store(setup.dest, export=True)


def test_unknown_plaintext_copy_added_after_restore_refuses_reopen(setup):
    restore(setup)
    (setup.dest / 'raw-copy.txt').write_bytes(b'SYNTHETIC-RAW-COPY')
    with pytest.raises(CheckpointBlocked):
        setup.access.open_store(setup.dest)


def test_all_briefing_versions_for_forgotten_signal_are_blocked(setup, monkeypatch):
    signal_id, _ = setup.ledger.upsert_signal(SignalInput(
        'school_email', 'test', 'archive-message', 'v1', NOW.isoformat(), 'archive-content', 'evd_archive'))
    setup.ledger.conn.execute("INSERT INTO briefings VALUES('cu_example',1,'hash',?,'published')", (NOW.isoformat(),))
    setup.ledger.conn.execute("INSERT INTO briefings VALUES('cu_example',2,'hash',?,'published')", (NOW.isoformat(),))
    setup.ledger.conn.execute("INSERT INTO briefing_signals VALUES('cu_example',?)", (signal_id,))
    archives = [f'local/catchup_core/briefings/2026-10-25-cu_example.v{version}.ae1' for version in (1, 2)]
    unrelated = 'local/catchup_core/briefings/2026-10-25-cu_other.v1.ae1'
    original = checkpoint(setup, extra=[(name, b'synthetic archive', 'file', 0o600) for name in [*archives, unrelated]])
    setup.ledger.forget('school_email', 'test', 'archive-message')
    setup.sealed.history = setup.ledger.privacy_history()
    original_apply = recovery.apply_privacy_history

    def with_plaintext_artifacts(db, remove, head, history, purge):
        directory = setup.dest / 'local/catchup_core/briefings'
        directory.mkdir()
        archive = directory / '2026-10-25-cu_example.v1.ae1'
        archive.write_bytes(b'SYNTHETIC-HISTORICAL-ARCHIVE')
        original_apply(db, remove, head, history, purge)
        assert not archive.exists()
        directory.rmdir()

    monkeypatch.setattr(recovery, 'apply_privacy_history', with_plaintext_artifacts)
    restore(setup, original)
    monkeypatch.setattr(recovery, 'apply_privacy_history', original_apply)
    entries = {entry['path']: entry for entry in setup.access.open_store(setup.dest).inventory()}
    assert all(entries[name]['state'] == 'blocked' for name in archives)
    assert entries[unrelated]['state'] == 'quarantined'
    with closing(sqlite3.connect(setup.dest / setup.ledger_name)) as db:
        assert db.execute('SELECT status FROM briefings').fetchall() == [('superseded',), ('superseded',)]


def test_revocation_between_chunks_stops_reader_before_next_chunk(setup):
    consent_id = setup.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner')
    setup.sealed.refresh(setup.ledger)
    body = bytes([97]) * (1024 * 1024 + 25)
    restore(setup, checkpoint(setup, extra=[('repo/large.txt', body, 'file', 0o600)]))
    store = setup.access.open_store(setup.dest, export=True)
    member = next(entry for entry in store.inventory() if entry['path'] == 'repo/large.txt')
    stream = store.iter_member(member['member_id'])
    assert len(next(stream)) == 1024 * 1024
    setup.ledger.revoke_consent(consent_id)
    setup.sealed.history = setup.ledger.privacy_history()
    with pytest.raises(QuarantineError):
        next(stream)


def test_replay_requires_a_sealed_catalog(setup):
    manifest = ck.verify(checkpoint(setup), CIPHER)
    setup.dest.mkdir()
    (setup.dest / '.artha-restored.json').write_text(json.dumps(ck._restore_marker('ck_test')))
    ledger = setup.dest / setup.ledger_name
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(database_bytes(setup.ledger._path))
    setup.access.factory(setup.dest, manifest, 'a' * 64)
    with pytest.raises(QuarantineError):
        setup.access.replay(setup.dest)


def test_each_actual_namespace_requires_current_history(setup):
    original = checkpoint(setup, extra=[('local/catchup_core/shadow/ledger.db',
        database_bytes(setup.ledger._path), 'sqlite', 0o600)])
    setup.access.sealed_for = lambda mode: setup.sealed if mode == 'authoritative' else TrustedSealedStore()
    with pytest.raises(CheckpointBlocked):
        restore(setup, original)
    assert not setup.dest.exists()


def test_head_change_after_replay_begins_never_returns_success(setup, monkeypatch):
    original_apply = recovery.apply_privacy_history

    def changing_apply(*args, **kwargs):
        original_apply(*args, **kwargs)
        setup.ledger.seal_privacy()

    monkeypatch.setattr(recovery, 'apply_privacy_history', changing_apply)
    with pytest.raises(CheckpointBlocked):
        restore(setup)
    assert not setup.dest.exists()


@pytest.mark.parametrize('field,value', [('checkpoint_id', 999), ('permission_generation', 99),
                                        ('tombstone_digest', 'b' * 64)])
def test_open_store_requires_restored_checkpoint_to_still_equal_current_head(setup, field, value):
    restore(setup)
    store = setup.access.open_store(setup.dest)
    with closing(sqlite3.connect(setup.dest / setup.ledger_name)) as db:
        db.execute(f'UPDATE deletion_checkpoint SET {field}=?', (value,))
        db.commit()
    with pytest.raises(CheckpointBlocked, match='replay'):
        store.authority()


@pytest.mark.parametrize('damage', ['file-link', 'directory-link', 'hardlink', 'unknown-ledger',
                                   'no-ledger', 'broken-database'])
def test_unsafe_or_missing_restored_ledger_refuses_without_touching_source(setup, damage):
    restore(setup)
    saved_source = database_bytes(setup.ledger._path)
    ledger = setup.dest / setup.ledger_name
    if damage == 'file-link':
        ledger.unlink()
        ledger.symlink_to(setup.ledger._path)
    elif damage == 'directory-link':
        (setup.dest / 'linked-store').symlink_to(setup.ledger._path.parent, target_is_directory=True)
    elif damage == 'hardlink':
        import os
        ledger.unlink()
        os.link(setup.ledger._path, ledger)
    elif damage == 'unknown-ledger':
        (setup.dest / 'ledger.db').write_bytes(saved_source)
    elif damage == 'no-ledger':
        ledger.unlink()
    else:
        ledger.write_bytes(b'not a database')
    with pytest.raises((CheckpointBlocked, sqlite3.Error)):
        setup.access.open_store(setup.dest)
    assert database_bytes(setup.ledger._path) == saved_source


@pytest.mark.parametrize('marker', ['[]', '{', 'x' * 65537])
def test_malformed_or_oversized_fence_refuses(setup, marker):
    restore(setup)
    (setup.dest / '.artha-restored.json').write_text(marker)
    with pytest.raises((CheckpointBlocked, ValueError)):
        setup.access.open_store(setup.dest)


def test_recovery_never_overlaps_live_repository(setup):
    manifest = ck.verify(checkpoint(setup), CIPHER)
    destination = setup.repo / 'restored'
    destination.mkdir()
    with pytest.raises(CheckpointBlocked, match='live'):
        setup.access.factory(destination, manifest, 'a' * 64)
    assert not list(destination.iterdir())


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'documents\\escape', 'bad:name', 'a//b', './a'])
def test_privacy_artifact_paths_cannot_escape_restored_store(setup, name):
    with pytest.raises(CheckpointBlocked, match='artifact'):
        setup.access._artifact(setup.dest, setup.dest / setup.ledger_name, name)


def test_oversized_restored_database_refuses_before_read(setup, monkeypatch):
    restore(setup)
    monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 1)
    with pytest.raises(CheckpointBlocked, match='bound'):
        setup.access.open_store(setup.dest)


def test_oversized_replay_output_refuses_before_writing(setup, monkeypatch):
    destination = setup.root / 'isolated'
    ledger = destination / 'catchup_core/ledger.db'
    original = database_bytes(setup.ledger._path)
    write_restored_ledger(ledger, original)
    with pytest.raises(CheckpointBlocked, match='exceeds its bound'):
        with setup.access._database(destination, ledger, update=True) as db:
            db.execute('CREATE TABLE synthetic_growth(value BLOB)')
            db.execute('INSERT INTO synthetic_growth VALUES(?)', (bytes(8192),))
            assert len(db.serialize()) > len(original)
            monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', len(original))
    assert ledger.read_bytes() == original


def test_replay_output_at_exact_size_bound_is_written(setup, monkeypatch):
    destination = setup.root / 'isolated'
    ledger = destination / 'catchup_core/ledger.db'
    original = database_bytes(setup.ledger._path)
    write_restored_ledger(ledger, original)
    with setup.access._database(destination, ledger, update=True) as db:
        db.execute('CREATE TABLE synthetic_growth(value BLOB)')
        db.execute('INSERT INTO synthetic_growth VALUES(?)', (bytes(8192),))
        serialized = db.serialize()
        assert len(serialized) > len(original)
        monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', len(serialized))
    assert ledger.read_bytes() == serialized


def test_restored_consent_content_cannot_expand_an_existing_read_authority(setup):
    setup.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner')
    setup.sealed.refresh(setup.ledger)
    restore(setup)
    store = setup.access.open_store(setup.dest, export=True)
    with closing(sqlite3.connect(setup.dest / setup.ledger_name)) as db:
        db.execute("UPDATE consent SET operations='[]'")
        db.commit()
    with pytest.raises(CheckpointBlocked):
        store.authority()


def test_household_unknown_age_and_missing_stable_ids_cannot_bypass_consent(setup, monkeypatch):
    setup.config['household_from_profile'] = True
    setup.config['consent_required'] = []
    config_write(setup)
    (setup.repo / 'config/user_profile.yaml').write_text(yaml.safe_dump({'family': {
        'primary_user': {'name': 'Synthetic Owner'},
        'children': [{'name': 'Synthetic Child'}]}}))
    monkeypatch.setattr(recovery.runtime, '_id_key', lambda provider: HmacKey(1, bytes([83]) * 32))
    raw = (setup.repo / 'config/user_profile.yaml').read_bytes()
    with pytest.raises(recovery.runtime.RuntimeConfigError, match='initialization'):
        restore(setup)
    assert (setup.repo / 'config/user_profile.yaml').read_bytes() == raw


def test_policy_or_household_bytes_change_invalidates_existing_authority(setup):
    restore(setup)
    store = setup.access.open_store(setup.dest)
    (setup.repo / 'config/user_profile.yaml').write_text('family: {}\n')
    with pytest.raises(CheckpointBlocked):
        store.authority()


def test_inventory_change_requires_fresh_open_not_just_matching_old_heads(setup):
    restore(setup)
    store = setup.access.open_store(setup.dest)
    additional = setup.dest / 'local/catchup_core/shadow/ledger.db'
    additional.parent.mkdir()
    additional.write_bytes(database_bytes(setup.ledger._path))
    with pytest.raises(CheckpointBlocked, match='inventory'):
        store.authority()


def test_checked_history_is_pinned_before_replay_reads(setup):
    pinned = setup.access._heads({'authoritative'})
    setup.ledger.seal_privacy()
    with pytest.raises(CheckpointBlocked, match='before replay'):
        setup.access._history({'authoritative'}, pinned)


def test_ledger_link_swap_after_path_check_cannot_be_read(setup, monkeypatch):
    restore(setup)
    ledger = setup.dest / setup.ledger_name
    outside = setup.root / 'outside.db'
    outside.write_bytes(database_bytes(setup.ledger._path))
    original_safe = setup.access._safe

    def swapped(root, path, **kwargs):
        original_safe(root, path, **kwargs)
        if path == ledger:
            ledger.unlink()
            ledger.symlink_to(outside)

    monkeypatch.setattr(setup.access, '_safe', swapped)
    with pytest.raises((CheckpointBlocked, OSError)):
        with setup.access._database(setup.dest, ledger):
            pass


def test_ledger_hardlink_swap_after_path_check_cannot_be_read(setup, monkeypatch):
    import os
    ledger = setup.root / 'isolated/catchup_core/ledger.db'
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(database_bytes(setup.ledger._path))
    outside = setup.root / 'outside.db'
    outside.write_bytes(ledger.read_bytes())
    original_safe = setup.access._safe

    def swapped(root, path, **kwargs):
        original_safe(root, path, **kwargs)
        ledger.unlink()
        os.link(outside, ledger)

    monkeypatch.setattr(setup.access, '_safe', swapped)
    with pytest.raises(CheckpointBlocked, match='unsafe'):
        with setup.access._database(setup.root / 'isolated', ledger):
            pass


def test_file_mutation_during_memory_replay_refuses_publication(setup):
    destination = setup.root / 'isolated'
    ledger = destination / 'catchup_core/ledger.db'
    write_restored_ledger(ledger, database_bytes(setup.ledger._path))
    if os.name == 'nt':
        with pytest.raises(CheckpointBlocked, match='changed during replay'):
            with setup.access._database(destination, ledger, update=True) as db:
                db.execute('DELETE FROM deletion_checkpoint')
                with pytest.raises(PermissionError):
                    ledger.write_bytes(b'competing replacement')
        assert ledger.read_bytes() == b''
        ledger.write_bytes(b'competing replacement')
        assert ledger.read_bytes() == b'competing replacement'
        return
    with pytest.raises(CheckpointBlocked, match='changed during replay'):
        with setup.access._database(destination, ledger, update=True) as db:
            db.execute('DELETE FROM deletion_checkpoint')
            ledger.write_bytes(b'competing replacement')
    assert ledger.read_bytes() == b'competing replacement'


def test_foreign_key_invalid_ledger_is_not_replayed(setup):
    destination = setup.root / 'isolated'
    ledger = destination / 'catchup_core/ledger.db'
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript('CREATE TABLE parent(id PRIMARY KEY); CREATE TABLE child(id REFERENCES parent(id)); '
                         'INSERT INTO child VALUES(1);')
        write_restored_ledger(ledger, db.serialize())
    with pytest.raises(CheckpointBlocked, match='invalid'):
        with setup.access._database(destination, ledger):
            pass


@pytest.mark.skipif(os.name != 'nt', reason='native restored database bounded IO')
def test_short_native_database_read_refuses_before_deserialization(setup, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile

    destination = setup.root / 'isolated'
    ledger = destination / 'catchup_core/ledger.db'
    original = database_bytes(setup.ledger._path)
    write_restored_ledger(ledger, original)
    read = WindowsQuarantineFile.read

    def short(opened, limit):
        value = read(opened, limit)
        return value[:-1] if opened.name == ledger.name else value

    monkeypatch.setattr(WindowsQuarantineFile, 'read', short)
    with pytest.raises(CheckpointBlocked, match='changed during read'):
        with setup.access._database(destination, ledger):
            pytest.fail('short database admitted')
    assert ledger.read_bytes() == original
    ledger.rename(ledger.with_suffix('.released'))


@pytest.mark.skipif(os.name != 'nt', reason='native restored database bounded IO')
def test_oversized_native_replay_output_refuses_before_writing(setup, monkeypatch):
    destination = setup.root / 'isolated'
    ledger = destination / 'catchup_core/ledger.db'
    original = database_bytes(setup.ledger._path)
    write_restored_ledger(ledger, original)
    with pytest.raises(CheckpointBlocked, match='exceeds its bound'):
        with setup.access._database(destination, ledger, update=True) as db:
            db.execute('CREATE TABLE synthetic_growth(value BLOB)')
            db.execute('INSERT INTO synthetic_growth VALUES(?)', (bytes(8192),))
            monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', len(original))
    assert ledger.read_bytes() == original
    ledger.rename(ledger.with_suffix('.released'))


@pytest.mark.skipif(os.name != 'nt', reason='native restored database reread verification')
def test_native_replay_reread_mismatch_never_returns_success(setup, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile

    destination = setup.root / 'isolated'
    ledger = destination / 'catchup_core/ledger.db'
    write_restored_ledger(ledger, database_bytes(setup.ledger._path))
    read = WindowsQuarantineFile.read
    reads = []

    def corrupt_reread(opened, limit):
        value = read(opened, limit)
        if opened.name == ledger.name:
            reads.append(value)
            if len(reads) == 2:
                return b'corrupted verification bytes'
        return value

    monkeypatch.setattr(WindowsQuarantineFile, 'read', corrupt_reread)
    with pytest.raises(CheckpointBlocked, match='changed during replay'):
        with setup.access._database(destination, ledger, update=True) as db:
            db.execute('CREATE TABLE synthetic_replay(value TEXT)')
    assert len(reads) == 2
    with closing(sqlite3.connect(ledger)) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    ledger.rename(ledger.with_suffix('.released'))


def test_unresolved_config_owner_refuses_without_native_operations(setup):
    setup.config['viewer'] = 'not-a-household-member'
    config_write(setup)
    with pytest.raises(CheckpointBlocked, match='owner'):
        setup.access._configuration()


def test_safe_paths_cannot_escape_destination(setup):
    with pytest.raises(CheckpointBlocked, match='escapes'):
        setup.access._safe(setup.dest, setup.ledger._path)


def test_invalid_catchup_identity_from_privacy_replay_refuses(setup, monkeypatch):
    def invalid_archive(db, remove, head, history, purge):
        purge(['../untrusted'])

    monkeypatch.setattr(recovery, 'apply_privacy_history', invalid_archive)
    with pytest.raises(CheckpointBlocked, match='archive identity'):
        restore(setup)
    assert not setup.dest.exists()


def test_second_authority_check_detects_policy_change(setup, monkeypatch):
    restore(setup)
    store = setup.access.open_store(setup.dest)
    original_base = setup.access._base
    calls = 0

    def changing(modes):
        nonlocal calls
        calls += 1
        if calls == 2:
            setup.config['bootstrap_days'] = 5
            config_write(setup)
        return original_base(modes)

    monkeypatch.setattr(setup.access, '_base', changing)
    with pytest.raises(CheckpointBlocked, match='authority changed'):
        store.authority()


@pytest.mark.parametrize('body,allowed', [(b'AE1' + bytes([0]) * 40, True), (b'plaintext', False)])
def test_reviewed_outputs_remain_opaque_not_catalog_members(setup, body, allowed):
    restore(setup)
    directory = setup.dest / 'reviewed-exports'
    directory.mkdir(mode=0o700)
    (directory / 'notes.txt.ae1').write_bytes(body)
    if allowed:
        entries = setup.access.open_store(setup.dest).inventory()
        assert all(not entry['path'].startswith('reviewed-exports/') for entry in entries)
    else:
        with pytest.raises(CheckpointBlocked, match='plaintext'):
            setup.access.open_store(setup.dest)


@pytest.mark.parametrize('origin', ['checkpoint', 'restored_copy'])
def test_recovery_export_grant_must_exist_in_independently_current_history(setup, origin):
    """A zero-revocation historical grant cannot turn an unchanged privacy head into export authority."""
    if origin == 'checkpoint':
        # This grant is in historical bytes, but has never entered the trusted current capsule.
        setup.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner')
    restore(setup)
    if origin == 'restored_copy':
        with closing(sqlite3.connect(setup.dest / setup.ledger_name)) as db, db:
            db.execute('INSERT INTO consent(consent_id,subject_id,grantor_id,scope,operations,audience,granted_at) '
                       "VALUES('con_untrusted','adult','adult','recovery','[\"view\",\"export\"]','owner',?)",
                       (NOW.isoformat(),))
    # Privacy metadata has the same tombstones and revocation sum, so metadata-only inspection remains possible.
    assert setup.access.open_store(setup.dest).inventory()
    with pytest.raises(CheckpointBlocked, match='current.*history|trusted.*consent'):
        setup.access.open_store(setup.dest, export=True)


@pytest.mark.parametrize('expired', [False, True])
def test_metadata_store_cannot_read_a_previously_authorized_export(setup, expired):
    from datetime import timedelta
    from catchup_core.quarantine_review import ReviewBlocked, preview, read_export, release
    setup.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner',
                               expires_at=(NOW + timedelta(hours=1)).isoformat())
    setup.sealed.refresh(setup.ledger)
    restore(setup)
    content = setup.access.open_store(setup.dest, export=True)
    member = content.inventory()[0]['member_id']
    review = preview(content, member, 'notes.txt', now=NOW)
    release(content, member, 'notes.txt', review['review_digest'], attest_clean=True, now=NOW)
    assert read_export(content, 'notes.txt', now=NOW)['data'] == b'SYNTHETIC-PRIVATE-NOTES'
    if expired:
        setup.access.now = lambda: NOW + timedelta(hours=2)
    metadata = setup.access.open_store(setup.dest)
    with pytest.raises(ReviewBlocked):
        read_export(metadata, 'notes.txt', now=NOW + timedelta(hours=2) if expired else NOW)


def test_original_catalog_rollback_cannot_resurrect_erased_artifact_after_sql_purge(setup):
    setup.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner')
    sid, _ = setup.ledger.upsert_signal(SignalInput(
        'school_email', 'test', 'message1', 'v1', NOW.isoformat(), 'content1', 'evd_original'))
    setup.sealed.refresh(setup.ledger)
    artifact = 'local/catchup_core/blobs/evd_original.blob'
    restore(setup, checkpoint(setup, extra=[(artifact, b'SYNTHETIC-ERASED-ARTIFACT', 'file', 0o600)]))
    catalog_path = setup.dest / '.artha-quarantine/catalog.ae1'
    original_catalog = catalog_path.read_bytes()
    original = setup.access.open_store(setup.dest).load_catalog()
    assert original['recovery_affinity']['ledgers'][setup.ledger_name]['sources'][0]['artifacts'] == [artifact]
    setup.ledger.forget('school_email', 'test', 'message1')
    setup.sealed.history = setup.ledger.privacy_history()
    setup.access.replay(setup.dest)
    with closing(sqlite3.connect(setup.dest / setup.ledger_name)) as db:
        assert not db.execute('SELECT 1 FROM signals WHERE signal_id=?', (sid,)).fetchone()
    # Reopening must derive denial from original capture lineage even though SQL refs are gone.
    catalog_path.write_bytes(original_catalog)
    reopened = setup.access.open_store(setup.dest, export=True)
    member = next(entry for entry in reopened.inventory() if entry['path'] == artifact)
    assert member['state'] == 'blocked'
    # A second rollback after opening also cannot bypass the per-member content guard.
    catalog_path.write_bytes(original_catalog)
    with pytest.raises(QuarantineError, match='erasure'):
        list(reopened.iter_member(member['member_id']))


def test_recovery_never_backfills_missing_original_affinity_from_purged_sql(setup, monkeypatch):
    restore(setup)
    original = QuarantineStore.load_catalog
    def missing(store):
        catalog = original(store)
        catalog.pop('recovery_affinity', None)
        return catalog
    monkeypatch.setattr(QuarantineStore, 'load_catalog', missing)
    with pytest.raises(CheckpointBlocked, match='original recovery provenance'):
        setup.access.open_store(setup.dest)


def test_document_tombstone_still_denies_content_after_postopen_catalog_rollback(setup):
    setup.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner')
    setup.sealed.refresh(setup.ledger)
    path = 'local/catchup_core/documents/document1.ae1'
    restore(setup, checkpoint(setup, extra=[(path, b'SYNTHETIC-DOCUMENT', 'file', 0o600)]))
    catalog_path = setup.dest / '.artha-quarantine/catalog.ae1'
    original = catalog_path.read_bytes()
    setup.ledger.forget('documents', 'manual', 'document1:page1')
    setup.sealed.history = setup.ledger.privacy_history()
    store = setup.access.open_store(setup.dest, export=True)
    member = next(entry for entry in store.inventory() if entry['path'] == path)
    assert member['state'] == 'blocked'
    catalog_path.write_bytes(original)
    with pytest.raises(QuarantineError, match='erasure'):
        list(store.iter_member(member['member_id']))


@pytest.mark.parametrize('damage', ['missing', 'schema', 'ledger', 'record', 'sha', 'sources',
    'source', 'field', 'artifact', 'duplicate-artifact', 'duplicate-source'])
def test_original_affinity_schema_cannot_invent_or_lose_artifact_authority(setup, damage):
    path = 'local/catchup_core/blobs/evd_original.blob'
    source = {'connector': 'school_email', 'account_scope': 'test', 'upstream_id': 'mail1',
              'content_hmac': 'synthetic-keyed-hash', 'artifacts': [path]}
    record = {'sha256': 'a' * 64, 'sources': [source]}
    affinity = {'schema': 'artha.recovery-affinity.v1', 'ledgers': {setup.ledger_name: record}}
    catalog = {'entries': [{'path': path}], 'recovery_affinity': affinity}
    if damage == 'missing':
        catalog.pop('recovery_affinity')
    elif damage == 'schema':
        affinity['schema'] = 'future'
    elif damage == 'ledger':
        affinity['ledgers'] = {}
    elif damage == 'record':
        record['unreviewed'] = True
    elif damage == 'sha':
        record['sha256'] = 'bad'
    elif damage == 'sources':
        record['sources'] = None
    elif damage == 'source':
        record['sources'] = [None]
    elif damage == 'field':
        source['connector'] = None
    elif damage == 'artifact':
        source['artifacts'] = ['not-an-original-member']
    elif damage == 'duplicate-artifact':
        source['artifacts'] = [path, path]
    else:
        record['sources'] = [source, copy.deepcopy(source)]
    with pytest.raises(CheckpointBlocked, match='provenance'):
        setup.access._affinity(catalog, setup.dest, [setup.dest / setup.ledger_name])


def test_original_source_affinity_requires_exact_authenticated_ledger_bytes(setup):
    destination = setup.root / 'affinity-source'
    ledger = destination / setup.ledger_name
    write_restored_ledger(ledger, database_bytes(setup.ledger._path))
    manifest = {'entries': [{'path': setup.ledger_name, 'kind': 'sqlite', 'sha256': '0' * 64}]}
    with pytest.raises(CheckpointBlocked, match='authenticated checkpoint'):
        setup.access._capture_affinity(destination, manifest)


@pytest.mark.parametrize(('connector', 'scope', 'upstream', 'artifact'), [
    ('documents', 'manual', 'document1:page2', 'documents/document1.ae1'),
    ('capture', 'owner', 'capture1', 'inbox/capture1.ae1'),
    ('retention', 'replay', 'feedback1', 'replay/feedback1.ae1'),
])
@pytest.mark.parametrize('mode', ['authoritative', 'shadow'])
def test_current_artifact_erasure_blocks_only_known_profile_scoped_historical_member(
        connector, scope, upstream, artifact, mode):
    from pathlib import Path

    parent = 'shadow' if mode == 'shadow' else 'catchup_core'
    name = f'local/{parent}/ledger.db'
    member = f'local/{parent}/{artifact}'
    unrelated = f'local/{parent}/documents/unrelated.ae1'
    affinity = {'ledgers': {name: {'sources': []}}}
    tombstones = [
        {'connector': connector, 'account_scope': scope, 'upstream_id': upstream},
        {'connector': 'capture', 'account_scope': 'owner', 'upstream_id': 'not-retained'},
        {'connector': 'capture', 'account_scope': 'owner', 'upstream_id': None},
    ]
    histories = {mode: {'tombstones': tombstones}}
    assert RecoveryAccess._mode(Path(name)) == mode
    assert RecoveryAccess._denied_affinity(affinity, histories, {member, unrelated}) == {member}


@pytest.mark.parametrize('match', ['source', 'content'])
def test_current_source_erasure_blocks_original_affinity_without_backfilling_from_live_rows(match):
    artifact = 'local/catchup_core/blobs/evd_original.blob'
    source = {'connector': 'school_email', 'account_scope': 'test', 'upstream_id': 'original',
              'content_hmac': 'original-keyed-hash', 'artifacts': [artifact]}
    tombstone = (
        {'connector': 'school_email', 'account_scope': 'test', 'upstream_id': 'original'}
        if match == 'source' else
        {'connector': 'different', 'account_scope': 'other', 'upstream_id': 'other',
         'content_hmac': 'original-keyed-hash'}
    )
    affinity = {'ledgers': {'local/catchup_core/ledger.db': {'sources': [source]}}}
    histories = {'authoritative': {'tombstones': [tombstone]}}
    assert RecoveryAccess._denied_affinity(affinity, histories, {artifact}) == {artifact}
    assert source['artifacts'] == [artifact]


@pytest.mark.parametrize('directory', ['shadow', 'shadow.run1-20261021', 'shadow.backup-pre-upgrade-20261022T235013'])
def test_renamed_shadow_run_directories_use_the_shadow_privacy_history(directory):
    from pathlib import Path

    assert RecoveryAccess._mode(Path(f'local/catchup_core/{directory}/ledger.db')) == 'shadow'
    assert RecoveryAccess._mode(Path('local/catchup_core/ledger.db')) == 'authoritative'


def test_history_digest_is_the_hash_of_the_verified_current_history_and_changes_with_it(setup):
    import hashlib, json  # noqa: E401, PLC0415
    first = setup.access.history_digest({'authoritative'})
    history = setup.access._history({'authoritative'}, setup.access._heads({'authoritative'}))
    expected = hashlib.sha256(json.dumps({'authoritative': history['authoritative']}, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert first == expected and setup.access.history_digest({'authoritative'}) == first
    setup.ledger.grant_consent('adult', 'adult', 'mail:test', ['view'], 'owner')
    setup.ledger.revoke_consent(setup.ledger.grant_consent('adult', 'adult', 'mail:test', ['view'], 'owner'))
    setup.sealed.refresh(setup.ledger)
    assert setup.access.history_digest({'authoritative'}) != first


def test_history_digest_refuses_when_the_trusted_history_is_missing(setup):
    setup.sealed.checkpoint = None
    with pytest.raises(CheckpointBlocked, match='trusted privacy history'):
        setup.access.history_digest({'authoritative'})
