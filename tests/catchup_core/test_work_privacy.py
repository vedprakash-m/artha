"""Local Work recovery restrictions; native identity and credentials are synthetic."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _work_synthetic import install_synthetic_work_creation, reconcile_reviewed
from catchup_core.work_privacy import WorkPrivacy, WorkPrivacyBlocked
from catchup_core import work_privacy as module


IDENTITY = {'platform': 'win32', 'machine': 'synthetic-machine', 'user': 'S-1-5-21-synthetic'}
KEY = b'x' * 32


@pytest.mark.parametrize('backend_module', ['keyring.backends.Windows', 'keyring.backends.macOS'])
def test_deferred_work_backend_pins_windows_writes_to_this_machine(monkeypatch, backend_module):
    class Native:
        persist = 3

        def __init__(self):
            self.records = {}
            self.writes = []

        def get_password(self, service, account):
            return self.records.get((service, account))

        def set_password(self, service, account, value):
            self.writes.append((self.persist, service, account))
            self.records[service, account] = value

    Native.__module__ = backend_module
    selected = Native()
    selected.records['synthetic-work', 'key'] = 'existing synthetic value'
    monkeypatch.setattr(module, 'system_keyring', lambda: selected)
    deferred = module._DeferredNativeBackend()
    assert deferred.get_password('synthetic-work', 'key') == 'existing synthetic value'
    deferred.set_password('synthetic-work', 'head', 'synthetic head')
    expected = 2 if backend_module.endswith('Windows') else 3
    assert selected.writes == [(expected, 'synthetic-work', 'head')]
    assert selected.persist == 3
    assert deferred.get_password('synthetic-work', 'head') == 'synthetic head'


@pytest.mark.skipif(os.name != 'nt', reason='installed Windows keyring configuration')
def test_installed_windows_backend_is_copied_without_native_credential_calls(monkeypatch):
    from keyring.backends.Windows import WinVaultKeyring

    selected = WinVaultKeyring()
    selected.persist = 3
    writes = []
    monkeypatch.setattr(WinVaultKeyring, 'get_password', lambda *_: 'synthetic existing value')
    monkeypatch.setattr(WinVaultKeyring, 'set_password', lambda backend, *_: writes.append(backend.persist))
    monkeypatch.setattr(module, 'system_keyring', lambda: selected)
    deferred = module._DeferredNativeBackend()
    assert deferred.get_password('synthetic-work', 'key') == 'synthetic existing value'
    deferred.set_password('synthetic-work', 'head', 'synthetic head')
    assert writes == [2] and selected.persist == 3
    assert deferred._native() is not selected and type(deferred._native()) is WinVaultKeyring


class Backend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    install_synthetic_work_creation(monkeypatch)
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    repo = tmp_path / 'repo'
    state = repo / 'state/work'
    state.mkdir(parents=True)
    (state / 'notes.md').write_bytes(b'old synthetic notes')
    return WorkPrivacy(repo, tmp_path / 'local', {'work_state': state},
                       backend=Backend(), native_identity=lambda: dict(IDENTITY))


def enroll(protocol):
    return protocol.enroll(KEY, reviewed=True, installation_id='synthetic-installation')


def test_no_implicit_enrollment_or_native_reads(protocol):
    protocol.backend.get_password = lambda *_: pytest.fail('unenrolled probe read credentials')
    assert protocol.is_enrolled() is False
    with pytest.raises(WorkPrivacyBlocked, match='enroll'):
        protocol.current()
    assert not protocol.authority_dir.exists()


def test_ciphertext_head_guard_does_not_claim_source_inventory_verification(protocol, monkeypatch):
    enrolled = enroll(protocol)
    (protocol.roots['work_state'] / 'notes.md').write_bytes(b'changed synthetic notes')
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.current()
    monkeypatch.setattr(protocol, '_inventory', lambda: pytest.fail('head guard scanned source bytes'))
    assert protocol.current_head()['head'] == enrolled['head']


@pytest.mark.parametrize('damage', ['pending', 'complete', 'head', 'identity'])
def test_ciphertext_head_guard_requires_acknowledged_native_history(protocol, damage):
    enroll(protocol)
    if damage == 'pending':
        (protocol.authority_dir / 'pending').write_bytes(b'synthetic pending intent')
    elif damage == 'complete':
        (protocol.authority_dir / 'complete').write_bytes(b'synthetic damaged acknowledgement')
    elif damage == 'head':
        protocol.backend.values[protocol.HEAD_SERVICE, 'head'] = 'synthetic invalid head'
    else:
        protocol.native_identity = lambda: dict(IDENTITY, machine='different-synthetic-machine')
    with pytest.raises(WorkPrivacyBlocked):
        protocol.current_head()


def test_stream_history_inventory_passes_remaining_budgets_to_native_reader(protocol, monkeypatch):
    observed = []
    monkeypatch.setattr(module, '_MAX_STREAM_TOTAL_BYTES', 5)
    monkeypatch.setattr(module, '_MAX_MEMBERS', 2)
    monkeypatch.setattr(module, '_MAX_STREAMS_PER_OWNER', 2)

    def read(_path, *, directory, max_total_bytes, max_streams):
        observed.append((directory, max_total_bytes, max_streams))
        data = b'one' if directory else b'tw'
        return (SimpleNamespace(name='tag', stream_type='$DATA', data=data),)

    monkeypatch.setattr(module, 'read_named_streams', read)
    assert len(protocol._inventory_streams()) == 2
    assert observed == [(True, 5, 2), (False, 2, 1)]


def test_enrollment_requires_review_and_existing_history_is_not_clean(protocol):
    with pytest.raises(WorkPrivacyBlocked, match='review'):
        protocol.enroll(KEY, reviewed=False)
    assert not protocol.is_enrolled()
    initial = enroll(protocol)
    assert initial['generation'] == 1
    assert initial['profile'] == 'work'
    assert initial['members']['work_state/notes.md']['size'] == len(b'old synthetic notes')
    binding = protocol.bind_checkpoint()
    assert protocol.check_checkpoint(binding) == protocol.current()
    assert protocol.denied_members(binding, ['work_state/notes.md']) == set()
    assert binding['plaintext_allowed'] is False
    assert 'identity_key' not in json.dumps(binding)
    with pytest.raises(WorkPrivacyBlocked, match='enrolled'):
        enroll(protocol)


@pytest.mark.parametrize('fault', ['identity', 'key', 'missing-key', 'missing-head', 'corrupt-head', 'capsule'])
def test_identity_key_and_history_loss_fail_closed(protocol, fault):
    enroll(protocol)
    if fault == 'identity':
        protocol.native_identity = lambda: dict(IDENTITY, user='different-user')
    elif fault == 'key':
        protocol.backend.set_password(protocol.KEY_SERVICE, 'key', base64.b64encode(b'y' * 32).decode())
    elif fault == 'missing-key':
        protocol.backend.values.pop((protocol.KEY_SERVICE, 'key'))
    elif fault == 'missing-head':
        protocol.backend.values.pop((protocol.HEAD_SERVICE, 'head'))
    elif fault == 'corrupt-head':
        protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', '{}')
    else:
        next(protocol.authority_dir.glob('*.ae1')).write_bytes(b'corrupt')
    with pytest.raises(WorkPrivacyBlocked):
        protocol.current()


@pytest.mark.parametrize('artifact', ['identity', 'complete', 'capsule'])
def test_missing_local_authority_is_a_protocol_refusal(protocol, artifact):
    enroll(protocol)
    path = (next(protocol.authority_dir.glob('*.ae1')) if artifact == 'capsule'
            else protocol.authority_dir / artifact)
    path.unlink()
    assert protocol.is_enrolled()
    with pytest.raises(WorkPrivacyBlocked, match='missing'):
        protocol.current()


@pytest.mark.parametrize('change', [
    {'machine': 'other-machine'}, {'user': 'other-user'}, {'platform': 'darwin'},
])
def test_original_machine_and_profile_are_required(protocol, change):
    enroll(protocol)
    protocol.native_identity = lambda: dict(IDENTITY, **change)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.bind_checkpoint()


@pytest.mark.parametrize('changes', [
    {'work_state/notes.md': b'updated'},
    {'work_state/notes.md': None},
    {'work_state/notes.md': b'updated', 'work_state/new.md': b'new'},
    {'work_state/new.md': b'new', 'work_state/notes.md': None},
    {'work_state/notes.md': b'old synthetic notes', 'work_state/new.md': b'new'},
    {'work_state/absent.md': None, 'work_state/new.md': b'new'},
])
@pytest.mark.skipif(os.name == 'nt', reason='native Windows member mutation is supported')
def test_non_windows_hold_refuses_existing_member_changes_before_intent(protocol, changes):
    enroll(protocol)
    before = protocol.current()
    authority = {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()}
    protocol.observe = lambda stage: pytest.fail(f'unexpected publication stage: {stage}')

    with pytest.raises(WorkPrivacyBlocked, match='temporarily paused'):
        protocol.publish(changes)

    assert protocol.current() == before
    assert {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()} == authority
    assert (protocol.roots['work_state'] / 'notes.md').read_bytes() == b'old synthetic notes'
    assert not (protocol.roots['work_state'] / 'new.md').exists()
    assert not (protocol.authority_dir / 'pending').exists()


@pytest.mark.parametrize('operation', ['replace', 'delete', 'absent-delete'])
@pytest.mark.parametrize('already_applied', [False, True])
@pytest.mark.skipif(os.name == 'nt', reason='native Windows pending member mutation is supported')
def test_non_windows_hold_refuses_legacy_pending_member_mutations(protocol, operation, already_applied):
    if operation == 'absent-delete' and already_applied:
        pytest.skip('an absent deletion has no member effect to apply')
    enroll(protocol)
    before = protocol.current()
    members = dict(before['members'])
    name = 'work_state/absent.md' if operation == 'absent-delete' else 'work_state/notes.md'
    value = b'legacy update' if operation == 'replace' else None
    if value is None:
        members.pop(name, None)
    else:
        import hashlib
        members[name] = {'sha256': hashlib.sha256(value).hexdigest(), 'size': len(value)}
    pending = protocol._prepare(before, members, {name: value}, ('work_state/notes.md',))
    module._atomic(protocol.authority_dir / 'pending', protocol._encrypt(pending, 'pending'))
    path = protocol.roots['work_state'] / 'notes.md'
    if already_applied:
        if value is None:
            path.unlink()
        else:
            path.write_bytes(value)
    authority = {item.name: item.read_bytes() for item in protocol.authority_dir.iterdir()}
    expected = path.read_bytes() if path.exists() else None
    protocol.observe = lambda stage: pytest.fail(f'unexpected recovery stage: {stage}')

    with pytest.raises(WorkPrivacyBlocked, match='temporarily paused'):
        protocol.recover_pending()

    assert {item.name: item.read_bytes() for item in protocol.authority_dir.iterdir()} == authority
    assert (path.read_bytes() if path.exists() else None) == expected
    assert protocol.backend.get_password(protocol.HEAD_SERVICE, 'head') == json.dumps(
        before['head'], sort_keys=True, separators=(',', ':'), allow_nan=False)


@pytest.mark.parametrize('pending', [False, True])
def test_unavailable_native_creation_refuses_before_new_effects(protocol, monkeypatch, pending):
    enroll(protocol)
    before = protocol.current()
    name = 'work_state/new.md'
    raw = b'new synthetic member'
    if pending:
        import hashlib
        members = dict(before['members'])
        members[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        intent = protocol._prepare(before, members, {name: raw}, ())
        module._atomic(protocol.authority_dir / 'pending', protocol._encrypt(intent, 'pending'))
    authority = {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()}
    native = dict(protocol.backend.values)
    monkeypatch.setattr(module, '_native_creation_available', lambda: False)

    def _attempt():
        if pending:
            protocol.recover_pending()
        else:
            protocol.publish({name: raw})

    with pytest.raises(WorkPrivacyBlocked, match='native write-once creation'):
        _attempt()

    assert not (protocol.roots['work_state'] / 'new.md').exists()
    assert {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()} == authority
    assert protocol.backend.values == native


def test_unavailable_native_creation_does_not_block_history_only_revocation(protocol, monkeypatch):
    enroll(protocol)
    monkeypatch.setattr(module, '_native_creation_available', lambda: False)
    result = protocol.publish({}, revoke=['work_state/notes.md'])
    assert result['generation'] == 2
    assert 'work_state/notes.md' in result['revoked']


@pytest.mark.skipif(sys.platform != 'win32', reason='requires Windows named streams')
def test_new_member_racing_with_another_process_is_never_replaced(protocol):
    enroll(protocol)
    path = protocol.roots['work_state'] / 'new.md'
    stream = Path(str(path) + ':synthetic')
    head = protocol.backend.get_password(protocol.HEAD_SERVICE, 'head')

    def create_racing_member(stage):
        if stage == 'capsule':
            subprocess.run(
                [sys.executable, '-c',
                 "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                 "p.write_bytes(b'new synthetic bytes'); "
                 "pathlib.Path(str(p)+':synthetic').write_bytes(b'external stream')",
                 str(path)],
                check=True,
                timeout=30,
            )

    protocol.observe = create_racing_member
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({'work_state/new.md': b'new synthetic bytes'})
    assert path.read_bytes() == b'new synthetic bytes'
    assert stream.read_bytes() == b'external stream'
    assert protocol.backend.get_password(protocol.HEAD_SERVICE, 'head') == head
    assert (protocol.authority_dir / 'pending').exists()
    assert not list(protocol.roots['work_state'].glob('.work-*'))

    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked):
        protocol.recover_pending()
    assert path.read_bytes() == b'new synthetic bytes'
    assert stream.read_bytes() == b'external stream'


@pytest.mark.skipif(sys.platform != 'win32', reason='requires Windows native publication')
@pytest.mark.parametrize('fault', ['cleanup', 'after-rename'])
def test_new_member_publication_surfaces_unverifiable_staging_cleanup(protocol, monkeypatch, fault):
    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError, WindowsQuarantineDirectory

    enroll(protocol)
    rename = WindowsQuarantineDirectory.publish_write_once
    delete = WindowsQuarantineDirectory.delete_file

    def fail_publication(parent, source, destination, *, expected_identity):
        if destination != 'new.md':
            return rename(parent, source, destination, expected_identity=expected_identity)
        if fault == 'after-rename':
            rename(parent, source, destination, expected_identity=expected_identity)
        raise QuarantineFilesystemError('synthetic publication failure')

    def fail_cleanup(parent, name, *, expected_identity):
        if fault == 'cleanup' and name.startswith('.work-'):
            raise QuarantineFilesystemError('synthetic cleanup failure')
        return delete(parent, name, expected_identity=expected_identity)

    monkeypatch.setattr(WindowsQuarantineDirectory, 'publish_write_once', fail_publication)
    monkeypatch.setattr(WindowsQuarantineDirectory, 'delete_file', fail_cleanup)
    with pytest.raises(WorkPrivacyBlocked, match='cleanup.*review'):
        protocol.publish({'work_state/new.md': b'new synthetic member'})
    assert (protocol.authority_dir / 'pending').exists()
    destination = protocol.roots['work_state'] / 'new.md'
    if fault == 'after-rename':
        assert destination.read_bytes() == b'new synthetic member'
    else:
        assert not destination.exists()
        assert len(list(protocol.roots['work_state'].glob('.work-*'))) == 1


def test_native_creation_capability_matches_the_running_platform():
    assert module._native_creation_available() is (os.name == 'nt')


@pytest.mark.parametrize('stage', ['intent', 'capsule', 'member', 'head', 'readback', 'complete'])
def test_interrupted_new_member_recovery_requires_unambiguous_destination(protocol, stage):
    enroll(protocol)
    path = protocol.roots['work_state'] / 'new.md'

    def interrupt(actual):
        if actual == stage:
            raise RuntimeError('synthetic interruption')

    protocol.observe = interrupt
    with pytest.raises(RuntimeError, match='interruption'):
        protocol.publish({'work_state/new.md': b'new synthetic member'})
    protocol.observe = lambda _: None

    if stage in {'intent', 'capsule'}:
        assert not path.exists()
        protocol.recover_pending()
        assert protocol.current()['generation'] == 2
        assert path.read_bytes() == b'new synthetic member'
        assert not (protocol.authority_dir / 'pending').exists()
    else:
        assert path.read_bytes() == b'new synthetic member'
        authority = {item.name: item.read_bytes() for item in protocol.authority_dir.iterdir()}
        native = dict(protocol.backend.values)
        with pytest.raises(WorkPrivacyBlocked, match='review'):
            protocol.recover_pending()
        assert path.read_bytes() == b'new synthetic member'
        assert {item.name: item.read_bytes() for item in protocol.authority_dir.iterdir()} == authority
        assert protocol.backend.values == native


def test_partially_published_new_batch_does_not_create_remaining_members_during_recovery(protocol):
    enroll(protocol)
    protocol.observe = lambda stage: (_ for _ in ()).throw(RuntimeError('interrupted')) if stage == 'member' else None
    with pytest.raises(RuntimeError, match='interrupted'):
        protocol.publish({'work_state/first.md': b'first', 'work_state/second.md': b'second'})
    protocol.observe = lambda _: None

    with pytest.raises(WorkPrivacyBlocked, match='review'):
        protocol.recover_pending()
    assert (protocol.roots['work_state'] / 'first.md').read_bytes() == b'first'
    assert not (protocol.roots['work_state'] / 'second.md').exists()
    assert (protocol.authority_dir / 'pending').exists()


def test_changed_deleted_and_revoked_paths_deny_all_older_members(protocol):
    enroll(protocol)
    historical = protocol.bind_checkpoint()
    protocol.publish({'work_state/new.md': b'new'})
    notes = protocol.roots['work_state'] / 'notes.md'
    notes.write_bytes(b'corrected')
    reconcile_reviewed(protocol)
    assert protocol.denied_members(historical, ['work_state/notes.md']) == {'work_state/notes.md'}
    fresh = protocol.bind_checkpoint()
    assert protocol.denied_members(fresh, ['work_state/notes.md']) == set()
    notes.unlink()
    reconcile_reviewed(protocol)
    protocol.publish({}, revoke=['work_state/new.md'])
    assert protocol.denied_members(fresh, ['work_state/notes.md', 'work_state/new.md']) == {
        'work_state/notes.md', 'work_state/new.md'}
    current = protocol.bind_checkpoint()
    assert protocol.denied_members(current, ['work_state/new.md']) == {'work_state/new.md'}
    (protocol.roots['work_state'] / 'new.md').write_bytes(b'rewritten')
    reconcile_reviewed(protocol)
    assert protocol.denied_members(protocol.bind_checkpoint(), ['work_state/new.md']) == {'work_state/new.md'}


@pytest.mark.parametrize('drift', ['edit', 'unknown', 'delete'])
def test_unjournaled_drift_blocks_capture_and_writer_until_review(protocol, drift):
    enroll(protocol)
    original = protocol.bind_checkpoint()
    path = protocol.roots['work_state'] / ('unknown.md' if drift == 'unknown' else 'notes.md')
    if drift == 'delete':
        path.unlink()
    else:
        path.write_bytes(b'unknown historical prose')
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.bind_checkpoint()
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.publish({'work_state/next.md': b'next'})
    with pytest.raises(WorkPrivacyBlocked, match='review'):
        protocol.reconcile(reviewed=False)
    reconcile_reviewed(protocol)
    assert protocol.current()['generation'] == 2
    assert protocol.denied_members(original, [path.relative_to(protocol.repo).as_posix().replace('state/work/', 'work_state/')])


@pytest.mark.parametrize('name', ['../escape', 'work_state/../escape', 'work_state/a\\b',
                                  'work_state/a:b', 'work_state/AUX', 'unknown/a', 'work_state/'])
def test_unsafe_member_names_refuse_before_intent(protocol, name):
    enroll(protocol)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({name: b'bad'})
    assert not (protocol.authority_dir / 'pending').exists()


def test_checkpoint_binding_is_installation_specific_and_authenticated(protocol, tmp_path):
    enroll(protocol)
    binding = protocol.bind_checkpoint()
    altered = dict(binding, generation=0)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.check_checkpoint(altered)
    other = WorkPrivacy(protocol.repo, tmp_path / 'other-local', protocol.roots,
                        backend=protocol.backend, native_identity=protocol.native_identity)
    with pytest.raises(WorkPrivacyBlocked):
        other.check_checkpoint(binding)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.check_checkpoint({'profile': 'work'})


@pytest.mark.parametrize('stage', ['intent', 'capsule', 'head', 'readback', 'complete'])
def test_interrupted_history_only_revocation_rolls_forward(protocol, stage):
    enroll(protocol)
    old = protocol.bind_checkpoint()

    def interrupt(actual):
        if actual == stage:
            raise RuntimeError('synthetic interruption')

    protocol.observe = interrupt
    with pytest.raises(RuntimeError, match='interruption'):
        protocol.publish({}, revoke=['work_state/notes.md'])
    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked, match='pending'):
        protocol.current()
    protocol.recover_pending()
    assert protocol.current()['generation'] == 2
    assert (protocol.roots['work_state'] / 'notes.md').read_bytes() == b'old synthetic notes'
    assert protocol.denied_members(old, ['work_state/notes.md']) == {'work_state/notes.md'}
    assert not (protocol.authority_dir / 'pending').exists()


def test_pending_and_capsules_never_contain_plaintext(protocol):
    enroll(protocol)
    protocol.observe = lambda stage: (_ for _ in ()).throw(RuntimeError('interrupted')) if stage == 'intent' else None
    with pytest.raises(RuntimeError, match='interrupted'):
        protocol.publish({'work_state/new.md': b'UNIQUE-SYNTHETIC-PRIVATE-BODY'})
    assert (protocol.authority_dir / 'pending').exists()
    for path in protocol.authority_dir.iterdir():
        if path.is_file():
            assert b'UNIQUE-SYNTHETIC-PRIVATE-BODY' not in path.read_bytes()


@pytest.mark.parametrize('stage', ['member', 'head', 'readback', 'complete'])
def test_external_head_change_blocks_success_and_recovery(protocol, stage):
    enroll(protocol)
    reached = []

    def change(actual):
        if actual == stage:
            reached.append(actual)
            protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', '{"changed":true}')

    protocol.observe = change
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({'work_state/new.md': b'new'})
    assert reached == [stage]
    assert (protocol.authority_dir / 'pending').exists()
    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked):
        protocol.recover_pending()


def test_rollback_of_native_head_or_acknowledged_capsule_refuses(protocol):
    enroll(protocol)
    head = protocol.backend.get_password(protocol.HEAD_SERVICE, 'head')
    protocol.publish({'work_state/new.md': b'new'})
    protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', head)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.current()


def test_pending_recovery_never_adopts_an_unknown_intervening_edit(protocol):
    enroll(protocol)
    protocol.observe = lambda stage: (_ for _ in ()).throw(RuntimeError('interrupted')) if stage == 'intent' else None
    with pytest.raises(RuntimeError, match='interrupted'):
        protocol.publish({'work_state/new.md': b'new'})
    (protocol.roots['work_state'] / 'notes.md').write_bytes(b'unknown')
    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.recover_pending()
    assert (protocol.roots['work_state'] / 'notes.md').read_bytes() == b'unknown'
    assert not (protocol.roots['work_state'] / 'new.md').exists()
    assert (protocol.authority_dir / 'pending').exists()


def test_inventory_does_not_silently_omit_an_unreadable_directory(protocol, monkeypatch):
    real = os.scandir
    blocked = protocol.roots['work_state'] / 'blocked'
    blocked.mkdir()
    (blocked / 'secret.md').write_bytes(b'synthetic')

    def denied(path):
        if Path(path) == blocked:
            raise PermissionError('synthetic unavailable directory')
        return real(path)

    monkeypatch.setattr(os, 'scandir', denied)
    with pytest.raises(PermissionError):
        enroll(protocol)
    assert not protocol.is_enrolled()


def test_transaction_cannot_publish_after_exit_or_publish_twice(protocol):
    enroll(protocol)
    with protocol.transaction() as transaction:
        transaction.publish({'work_state/one.md': b'one'})
        with pytest.raises(WorkPrivacyBlocked, match='closed'):
            transaction.publish({'work_state/two.md': b'two'})
    with pytest.raises(WorkPrivacyBlocked, match='closed'):
        transaction.publish({'work_state/three.md': b'three'})


def test_transaction_current_recheck_does_not_reacquire_locks(protocol):
    enroll(protocol)
    with protocol.transaction() as transaction:
        assert transaction.check_current() == transaction.pinned
    with pytest.raises(WorkPrivacyBlocked, match='closed'):
        transaction.check_current()


@pytest.mark.parametrize('fault', ['drift', 'identity', 'head', 'history'])
def test_transaction_current_recheck_refuses_changed_authority(protocol, fault):
    enroll(protocol)
    with protocol.transaction() as transaction:
        if fault == 'drift':
            (protocol.roots['work_state'] / 'notes.md').write_bytes(b'manual edit')
        elif fault == 'identity':
            protocol.native_identity = lambda: dict(IDENTITY, machine='different')
        elif fault == 'head':
            protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', '{}')
        else:
            next(protocol.authority_dir.glob('*.ae1')).write_bytes(b'changed history')
        with pytest.raises(WorkPrivacyBlocked):
            transaction.check_current()


def test_reconciliation_retains_prior_revocation(protocol):
    enroll(protocol)
    protocol.publish({}, revoke=['work_state/notes.md'])
    (protocol.roots['work_state'] / 'notes.md').write_bytes(b'unknown rewritten prose')
    reconcile_reviewed(protocol)
    binding = protocol.bind_checkpoint()
    assert protocol.denied_members(binding, ['work_state/notes.md']) == {'work_state/notes.md'}


def test_key_changed_at_member_publication_blocks_before_head_advance(protocol):
    enroll(protocol)
    head = protocol.backend.get_password(protocol.HEAD_SERVICE, 'head')
    reached = []

    def change(stage):
        if stage == 'member':
            reached.append(stage)
            protocol.backend.set_password(protocol.KEY_SERVICE, 'key', base64.b64encode(b'z' * 32).decode())

    protocol.observe = change
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({'work_state/new.md': b'changed'})
    assert reached == ['member']
    assert protocol.backend.get_password(protocol.HEAD_SERVICE, 'head') == head


def test_restore_fence_prevents_protocol_operation(protocol):
    enroll(protocol)
    (protocol.repo / '.artha-restored.json').write_text('{}')
    from catchup_core.session_lock import SessionBusy

    with pytest.raises(SessionBusy):
        protocol.publish({'work_state/notes.md': b'changed'})


def test_canonical_factory_defers_all_native_discovery_until_explicit_enrollment(protocol, monkeypatch):
    def forbidden():
        pytest.fail('implicit native discovery')

    monkeypatch.setattr(module, 'system_keyring', forbidden)
    monkeypatch.setattr(module, 'native_windows_identity', forbidden)
    canonical = module.for_installation(protocol.repo, protocol.local)
    assert canonical.roots == {'work_state': protocol.repo / 'state/work',
                               'work_local': protocol.local / 'work'}
    assert not canonical.is_enrolled()
    with pytest.raises(WorkPrivacyBlocked, match='enroll'):
        canonical.current()
    with pytest.raises(WorkPrivacyBlocked, match='review'):
        canonical.enroll_generated(reviewed=False)


def test_explicit_generated_enrollment_uses_injected_native_sources(protocol, monkeypatch):
    calls = []
    (protocol.local / 'work').mkdir(parents=True)
    monkeypatch.setattr(module, 'system_keyring', lambda: calls.append('backend') or protocol.backend)
    monkeypatch.setattr(module, 'native_windows_identity', lambda: calls.append('identity') or dict(IDENTITY))
    monkeypatch.setattr(module.secrets, 'token_bytes', lambda count: calls.append(count) or KEY)
    canonical = module.for_installation(protocol.repo, protocol.local)
    assert calls == []
    result = canonical.enroll_generated(reviewed=True, installation_id='generated-test')
    assert result['generation'] == 1
    assert calls.count(32) == 1
    assert calls.count('backend') == 1
    assert calls.count('identity') > 0


def test_only_exact_cache_lock_is_ephemeral_not_the_cache_payload(protocol):
    cache = protocol.local / 'work/cache'
    cache.mkdir(parents=True)
    canonical = module.for_installation(protocol.repo, protocol.local,
                                        backend=protocol.backend, native_identity=protocol.native_identity)
    enroll(canonical)
    (cache / 'cache.lock').write_bytes(b'lock bytes')
    assert canonical.current()['members'] == canonical.bind_checkpoint()['members']
    with pytest.raises(WorkPrivacyBlocked, match='ephemeral'):
        canonical.publish({'work_local/cache/cache.lock': b'not a member'})
    canonical.publish({'work_local/cache/workiq.json.age': b'encrypted cache'})
    assert 'work_local/cache/workiq.json.age' in canonical.current()['members']
    (cache / 'unexpected.lock').write_bytes(b'unknown')
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        canonical.current()


def test_identical_empty_and_repeated_revocation_are_exact_noops_at_capacity(protocol, monkeypatch):
    enroll(protocol)
    protocol.publish({}, revoke=['work_state/notes.md'])
    current = protocol.current()
    monkeypatch.setattr(module, '_MAX_GENERATIONS', current['generation'])
    files = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
             for path in protocol.authority_dir.iterdir()}
    native = dict(protocol.backend.values)
    member = protocol.roots['work_state'] / 'notes.md'
    metadata = member.stat()
    protocol.observe = lambda _: pytest.fail('no-op published a transaction')
    for changes, revoke in [
        ({}, ()),
        ({'work_state/notes.md': member.read_bytes()}, ()),
        ({'work_state/absent.md': None}, ()),
        ({}, ['work_state/notes.md']),
    ]:
        assert protocol.publish(changes, revoke=revoke) == current
    assert protocol.backend.values == native
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in protocol.authority_dir.iterdir()} == files
    assert member.stat().st_mtime_ns == metadata.st_mtime_ns
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({}, revoke=['work_state/new.md'])
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({'work_state/notes.md': b'changed'})


@pytest.mark.parametrize('bad', [
    ({'work_state/../escape': None}, ()),
    ({'work_state/notes.md': 'not bytes'}, ()),
    ({}, ['../escape']),
    ({'work_local/cache/cache.lock': None}, ()),
])
def test_noop_does_not_skip_member_or_revocation_validation(protocol, bad):
    enroll(protocol)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish(bad[0], revoke=bad[1])


@pytest.mark.parametrize('fault', ['drift', 'identity', 'head'])
def test_noop_still_requires_current_authority(protocol, fault):
    enroll(protocol)
    with protocol.transaction() as transaction:
        if fault == 'drift':
            (protocol.roots['work_state'] / 'notes.md').write_bytes(b'manual edit')
        elif fault == 'identity':
            protocol.native_identity = lambda: dict(IDENTITY, machine='different')
        else:
            protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', '{}')
        with pytest.raises(WorkPrivacyBlocked):
            transaction.publish({})


def _stream_key(owner, kind, name):
    return json.dumps([owner, kind, name, '$DATA'], separators=(',', ':'), ensure_ascii=True)


@pytest.fixture
def stream_inventory(protocol, monkeypatch):
    """Provide deterministic synthetic stream bytes without touching Work data."""
    inventory = {}
    monkeypatch.setattr(protocol, '_inventory_streams', lambda: dict(inventory), raising=False)
    return inventory


def _stream_record(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _upgrade_streams(protocol):
    preview = protocol.preview_stream_history()
    return protocol.upgrade_stream_history(reviewed=True, expected_digest=preview['inventory_sha256'])


@pytest.fixture
def policy_streams(protocol, monkeypatch):
    streams = {}
    def inventory(stream_policy=None):
        return {key: value for key, value in streams.items()
                if not (stream_policy and json.loads(key)[2:] == ['sec.endpointdlp', '$DATA'])}
    monkeypatch.setattr(protocol, '_inventory_streams', inventory)
    return streams


def test_reconcile_review_preview_is_metadata_only_and_adopts_exact_changes(protocol):
    root = protocol.roots['work_state']
    (root / 'remove-private.md').write_bytes(b'remove synthetic content')
    enroll(protocol)
    old_binding = protocol.bind_checkpoint()
    before = protocol.current()
    authority = {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()}
    native = dict(protocol.backend.values)
    (root / 'notes.md').write_bytes(b'changed synthetic content')
    (root / 'remove-private.md').unlink()
    (root / 'added-private.md').write_bytes(b'added synthetic content')

    review = protocol.preview_reconciliation()
    detail = protocol.preview_reconciliation(details=True)
    assert review['inventory_sha256'] == detail['inventory_sha256']
    assert len(review['inventory_sha256']) == 64
    assert review['member_changes'] == {'added': 1, 'changed': 1, 'removed': 1}
    assert review['generation'] == before['generation']
    assert review['next_generation'] == before['generation'] + 1
    assert review['advances_generation'] is True
    assert review['stream_history'] is False
    assert {item['member']: item['change'] for item in detail['details']['members']} == {
        'work_state/added-private.md': 'added',
        'work_state/notes.md': 'changed',
        'work_state/remove-private.md': 'removed',
    }
    assert not any(text in json.dumps(review) for text in
                   ('notes.md', 'remove-private.md', 'added-private.md', 'synthetic content'))
    assert {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()} == authority
    assert protocol.backend.values == native

    after = protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    assert after['generation'] == before['generation'] + 1
    assert set(after['members']) == {'work_state/notes.md', 'work_state/added-private.md'}
    assert protocol.denied_members(old_binding, ['work_state/notes.md', 'work_state/remove-private.md']) == {
        'work_state/notes.md', 'work_state/remove-private.md'}


def test_reconcile_review_no_drift_still_advances_generation(protocol):
    before = enroll(protocol)
    review = protocol.preview_reconciliation()
    assert review['member_changes'] == {'added': 0, 'changed': 0, 'removed': 0}
    assert review['advances_generation'] is True
    after = protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    assert after['generation'] == before['generation'] + 1
    assert after['members'] == before['members']


def test_reconcile_review_v1_does_not_invent_stream_authority(protocol, monkeypatch):
    enroll(protocol)
    monkeypatch.setattr(protocol, '_inventory_streams', lambda: pytest.fail('v1 has no stream inventory'))
    review = protocol.preview_reconciliation()
    assert review['stream_scope'] == 'not_enrolled'
    assert review['stream_count'] is None and review['stream_bytes'] is None
    assert review['stream_changes'] is None and review['prior_file_streams_affected'] is None
    after = protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    assert after['schema'] == 'artha.work-member-history.v1' and 'streams' not in after


@pytest.mark.parametrize('digest', [None, '', 'not-a-digest', 'A' * 64, '0' * 64, 42])
def test_reconcile_review_refuses_missing_invalid_or_unmatched_digest_before_intent(protocol, digest):
    enroll(protocol)
    (protocol.roots['work_state'] / 'notes.md').write_bytes(b'unreviewed synthetic drift')
    authority = {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()}
    native = dict(protocol.backend.values)

    with pytest.raises(WorkPrivacyBlocked):
        protocol.reconcile(reviewed=True, expected_digest=digest)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.reconcile(reviewed=True)
    assert not (protocol.authority_dir / 'pending').exists()
    assert {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()} == authority
    assert protocol.backend.values == native


@pytest.mark.parametrize('change', ['member', 'addition', 'deletion', 'stream', 'head', 'identity', 'policy'])
def test_reconcile_review_refuses_stale_inventory_or_authority(protocol, policy_streams, change):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'synthetic-private-stream')
    policy_streams[key] = _stream_record(b'original')
    _upgrade_streams(protocol)
    review = protocol.preview_reconciliation()
    member = protocol.roots['work_state'] / 'notes.md'
    if change == 'member':
        member.write_bytes(b'changed since review')
    elif change == 'addition':
        (member.parent / 'added.md').write_bytes(b'added since review')
    elif change == 'deletion':
        member.unlink()
        del policy_streams[key]
    elif change == 'stream':
        policy_streams[key] = _stream_record(b'changed since review')
    elif change == 'head':
        protocol.publish({'work_state/new.md': b'changed authority'})
    elif change == 'identity':
        protocol.native_identity = lambda: dict(IDENTITY, machine='different-synthetic-machine')
    else:
        policy_review = protocol.preview_stream_policy()
        protocol.adopt_stream_policy(reviewed=True, expected_digest=policy_review['inventory_sha256'])
    authority = {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()}
    native = dict(protocol.backend.values)

    with pytest.raises(WorkPrivacyBlocked):
        protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    assert not (protocol.authority_dir / 'pending').exists()
    assert {path.name: path.read_bytes() for path in protocol.authority_dir.iterdir()} == authority
    assert protocol.backend.values == native


def test_reconcile_review_rechecks_inventory_before_returning_digest(protocol, monkeypatch):
    enroll(protocol)
    original = protocol._inventory
    calls = 0

    def unstable_inventory():
        nonlocal calls
        snapshot = original()
        calls += 1
        if calls == 1:
            (protocol.roots['work_state'] / 'notes.md').write_bytes(b'changed during preview')
        return snapshot

    monkeypatch.setattr(protocol, '_inventory', unstable_inventory)
    with pytest.raises(WorkPrivacyBlocked, match='changed|drift'):
        protocol.preview_reconciliation()
    assert not (protocol.authority_dir / 'pending').exists()


@pytest.mark.parametrize('change', ['head', 'identity'])
def test_reconcile_review_rechecks_authority_before_returning_digest(protocol, monkeypatch, change):
    enroll(protocol)
    original = protocol._inventory
    calls = 0

    def changed_authority():
        nonlocal calls
        snapshot = original()
        calls += 1
        if calls == 1:
            if change == 'head':
                protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', '{}')
            else:
                protocol.native_identity = lambda: dict(IDENTITY, machine='changed-synthetic-machine')
        return snapshot

    monkeypatch.setattr(protocol, '_inventory', changed_authority)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.preview_reconciliation()
    assert not (protocol.authority_dir / 'pending').exists()


def test_reconcile_review_refuses_stream_changes_during_preview(protocol, policy_streams, monkeypatch):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'synthetic-stream')
    policy_streams[key] = _stream_record(b'original')
    _upgrade_streams(protocol)
    original = protocol._inventory_streams
    calls = 0

    def changed_streams(stream_policy=None):
        nonlocal calls
        snapshot = original(stream_policy)
        calls += 1
        if calls == 1:
            policy_streams[key] = _stream_record(b'changed during preview')
        return snapshot

    monkeypatch.setattr(protocol, '_inventory_streams', changed_streams)
    with pytest.raises(WorkPrivacyBlocked, match='stream inventory changed'):
        protocol.preview_reconciliation()
    assert not (protocol.authority_dir / 'pending').exists()


def test_reconcile_review_reports_unchanged_owned_streams_and_preserves_revocation(protocol, stream_inventory):
    root = protocol.roots['work_state']
    (root / 'revoked.md').write_bytes(b'synthetic revoked content')
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'synthetic-private-stream')
    stream_inventory[key] = _stream_record(b'unchanged')
    _upgrade_streams(protocol)
    protocol.publish({}, revoke=['work_state/revoked.md'])
    binding = protocol.bind_checkpoint()
    (root / 'notes.md').write_bytes(b'changed primary')

    review = protocol.preview_reconciliation()
    detail = protocol.preview_reconciliation(details=True)
    assert review['stream_history'] is True
    assert review['stream_changes'] == {'added': 0, 'changed': 0, 'removed': 0}
    assert review['prior_file_streams_affected'] == 1
    assert review['revocations_retained'] == 1
    assert 'synthetic-private-stream' not in json.dumps(review)
    assert any(item['owner'] == 'work_state/notes.md' and item['name'] == 'synthetic-private-stream'
               for item in detail['details']['owner_invalidated_streams'])

    after = protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    assert after['revoked'] == ['work_state/revoked.md']
    assert protocol.denied_members(binding, ['work_state/notes.md', 'work_state/revoked.md']) == {
        'work_state/notes.md', 'work_state/revoked.md'}
    assert protocol.denied_streams(binding, [key]) == {key}


@pytest.mark.parametrize('kind,change,affected', [
    ('file', 'added', 0), ('file', 'changed', 1), ('file', 'removed', 1),
    ('directory', 'changed', 0),
])
def test_reconcile_review_reports_stream_changes_and_prior_file_effects(
        protocol, stream_inventory, kind, change, affected):
    enroll(protocol)
    owner = 'work_state/notes.md' if kind == 'file' else 'work_state'
    key = _stream_key(owner, kind, 'synthetic-stream')
    if change != 'added':
        stream_inventory[key] = _stream_record(b'original')
    _upgrade_streams(protocol)
    binding = protocol.bind_checkpoint()
    if change == 'removed':
        del stream_inventory[key]
    else:
        stream_inventory[key] = _stream_record(b'new')

    review = protocol.preview_reconciliation()
    detail = protocol.preview_reconciliation(details=True)
    assert review['stream_scope'] == 'all'
    assert review['stream_changes'] == {name: int(name == change) for name in ('added', 'changed', 'removed')}
    assert review['prior_file_streams_affected'] == affected
    assert detail['details']['streams'] == [
        {'owner': owner, 'kind': kind, 'name': 'synthetic-stream', 'stream_type': '$DATA', 'change': change}]
    assert 'synthetic-stream' not in json.dumps(review)
    after = protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    assert after['streams'] == stream_inventory
    if kind == 'file' and change != 'added':
        assert protocol.denied_streams(binding, [key]) == {key}


def test_reconcile_review_v3_preserves_only_the_approved_stream_omission(protocol, policy_streams):
    enroll(protocol)
    omitted = _stream_key('work_state/notes.md', 'file', 'sec.endpointdlp')
    required = _stream_key('work_state/notes.md', 'file', 'sec.endpointdlp.extra')
    policy_streams.update({omitted: _stream_record(b'label'), required: _stream_record(b'required')})
    _upgrade_streams(protocol)
    policy = protocol.preview_stream_policy()
    protocol.adopt_stream_policy(reviewed=True, expected_digest=policy['inventory_sha256'])
    before = protocol.preview_reconciliation()
    assert before['stream_scope'] == 'endpointdlp_omitted' and before['stream_count'] == 1

    policy_streams[omitted] = _stream_record(b'new label')
    assert protocol.preview_reconciliation()['inventory_sha256'] == before['inventory_sha256']
    policy_streams[required] = _stream_record(b'changed required stream')
    with pytest.raises(WorkPrivacyBlocked, match='no longer matches'):
        protocol.reconcile(reviewed=True, expected_digest=before['inventory_sha256'])
    assert not (protocol.authority_dir / 'pending').exists()
    review = protocol.preview_reconciliation()
    assert review['stream_changes'] == {'added': 0, 'changed': 1, 'removed': 0}
    after = protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    assert after['schema'] == 'artha.work-member-history.v3'
    assert after['stream_policy'] == module.STREAM_POLICY
    assert after['streams'] == {required: policy_streams[required]}


def test_reconcile_review_intervening_drift_after_intent_stays_pending(protocol):
    initial = enroll(protocol)
    member = protocol.roots['work_state'] / 'notes.md'
    member.write_bytes(b'reviewed synthetic drift')
    review = protocol.preview_reconciliation()

    def mutate(stage):
        if stage == 'intent':
            member.write_bytes(b'unreviewed synthetic drift')

    protocol.observe = mutate
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
    protocol.observe = lambda _: None
    assert (protocol.authority_dir / 'pending').exists()
    assert protocol._native_head() == initial['head']
    with pytest.raises(WorkPrivacyBlocked, match='pending'):
        protocol.preview_reconciliation()
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.recover_pending()
    member.write_bytes(b'reviewed synthetic drift')
    assert protocol.recover_pending()['generation'] == initial['generation'] + 1


def test_endpointdlp_policy_transition_retains_history_and_invalidates_omitted_stream(protocol, policy_streams):
    enroll(protocol)
    omitted = _stream_key('work_state/notes.md', 'file', 'sec.endpointdlp')
    retained = _stream_key('work_state/notes.md', 'file', 'important')
    policy_streams.update({omitted: _stream_record(b'old label'), retained: _stream_record(b'keep')})
    _upgrade_streams(protocol)
    old = protocol.bind_checkpoint()
    previous = protocol.current()
    policy_streams[omitted] = _stream_record(b'changing label')
    preview = protocol.preview_stream_policy()
    after = protocol.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
    assert after['schema'] == 'artha.work-member-history.v3'
    assert after['generation'] == previous['generation'] + 1
    assert after['events'][:-1] == previous['events']
    assert after['streams'] == {retained: policy_streams[retained]}
    assert protocol.denied_streams(old, [omitted, retained]) == {omitted}
    binding = protocol.bind_checkpoint()
    assert binding['schema'] == 'artha.work-checkpoint-binding.v3'
    assert binding['stream_policy'] == 'artha.work-stream-policy.endpointdlp.v1'
    assert protocol.check_checkpoint(binding)['generation'] == after['generation']
    policy_streams[omitted] = _stream_record(b'changed again')
    assert protocol.current()['generation'] == after['generation']
    protocol.publish({'work_state/new.md': b'new primary'})
    assert protocol.current()['stream_policy'] == binding['stream_policy']


@pytest.mark.parametrize('fault', ['review', 'digest', 'primary', 'other-stream'])
def test_endpointdlp_transition_refuses_unreviewed_or_unrelated_drift(protocol, policy_streams, fault):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'mandatory')
    policy_streams[key] = _stream_record(b'keep')
    _upgrade_streams(protocol)
    preview = protocol.preview_stream_policy()
    if fault == 'primary':
        (protocol.roots['work_state'] / 'notes.md').write_bytes(b'unjournaled primary')
    elif fault == 'other-stream':
        policy_streams[key] = _stream_record(b'unjournaled stream')
    with pytest.raises(WorkPrivacyBlocked):
        protocol.adopt_stream_policy(reviewed=fault != 'review',
            expected_digest='0' * 64 if fault == 'digest' else preview['inventory_sha256'])
    assert not (protocol.authority_dir / 'pending').exists()
    assert protocol._load_current(drift=False)['schema'] == 'artha.work-member-history.v2'


@pytest.mark.parametrize('stage', ['intent', 'capsule', 'head', 'readback', 'complete'])
def test_endpointdlp_policy_transition_recovers_interruption(protocol, policy_streams, stage):
    enroll(protocol)
    policy_streams[_stream_key('work_state/notes.md', 'file', 'sec.endpointdlp')] = _stream_record(b'label')
    _upgrade_streams(protocol)
    preview = protocol.preview_stream_policy()
    def interrupt(observed):
        if observed == stage:
            raise RuntimeError('synthetic interruption')
    protocol.observe = interrupt
    with pytest.raises(RuntimeError, match='synthetic interruption'):
        protocol.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
    protocol.observe = lambda _: None
    policy_streams[_stream_key('work_state/notes.md', 'file', 'sec.endpointdlp')] = _stream_record(b'new label')
    after = protocol.recover_pending()
    assert after['generation'] == 3 and after['schema'] == 'artha.work-member-history.v3'
    assert not (protocol.authority_dir / 'pending').exists()


@pytest.mark.parametrize('name', ['SEC.ENDPOINTDLP', 'sec.endpointdlp.extra', 'MSIP_Label', 'unknown'])
def test_endpointdlp_policy_does_not_exempt_other_names(protocol, policy_streams, name):
    enroll(protocol)
    _upgrade_streams(protocol)
    preview = protocol.preview_stream_policy()
    protocol.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
    policy_streams[_stream_key('work_state/notes.md', 'file', name)] = _stream_record(b'new stream')
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.current()


@pytest.mark.parametrize('field,value', [
    ('stream_policy', 'unknown'), ('stream_policy_since', True), ('stream_policy_since', 2),
    ('stream_since', None), ('streams', []),
])
def test_endpointdlp_history_rejects_malformed_policy_fields(protocol, policy_streams, field, value):
    enroll(protocol)
    _upgrade_streams(protocol)
    preview = protocol.preview_stream_policy()
    current = protocol.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
    history = {key: item for key, item in current.items() if key != 'head'}
    history[field] = value
    with pytest.raises(WorkPrivacyBlocked):
        protocol._validate_history(history)


def test_endpointdlp_policy_rejects_downgrade_early_binding_and_implicit_read_policy(protocol, policy_streams):
    enroll(protocol)
    _upgrade_streams(protocol)
    old = protocol.bind_checkpoint()
    preview = protocol.preview_stream_policy()
    current = protocol.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
    new = protocol.bind_checkpoint()
    early = {key: value for key, value in old.items() if key != 'mac'}
    early.update(schema='artha.work-checkpoint-binding.v3', stream_policy=module.STREAM_POLICY)
    with pytest.raises(WorkPrivacyBlocked, match='predates'):
        protocol._check_checkpoint(protocol._sign(early, 'checkpoint'), current)
    downgraded = {key: value for key, value in new.items() if key not in {'mac', 'stream_policy'}}
    downgraded['schema'] = 'artha.work-checkpoint-binding.v2'
    with pytest.raises(WorkPrivacyBlocked, match='policy'):
        protocol._check_checkpoint(protocol._sign(downgraded, 'checkpoint'), current)
    with pytest.raises(WorkPrivacyBlocked, match='policy'):
        WorkPrivacy._inventory_streams(protocol, 'unknown')
    with pytest.raises(WorkPrivacyBlocked, match='requires existing'):
        protocol.preview_stream_policy()
    with pytest.raises(WorkPrivacyBlocked, match='unavailable'):
        protocol._prepare(current, current['members'], {}, (), streams={}, adopt_policy=True)
    after = dict(current, schema='artha.work-member-history.v2', generation=current['generation'] + 1,
                 events=[*current['events'], {}])
    with pytest.raises(WorkPrivacyBlocked, match='downgraded'):
        protocol._continuity(current, after)


def test_stream_history_upgrade_requires_matching_explicit_review(protocol, stream_inventory):
    enroll(protocol)
    legacy = protocol.bind_checkpoint()
    legacy_event = protocol.current()['events'][0]
    stream_inventory[_stream_key('work_state/notes.md', 'file', 'synthetic-stream')] = _stream_record(b'body')
    stream_inventory[_stream_key('work_state', 'directory', 'root-stream')] = _stream_record(b'')
    preview = protocol.preview_stream_history()

    assert preview['stream_count'] == 2 and preview['stream_bytes'] == 4
    assert 'synthetic-stream' not in json.dumps(preview)
    with pytest.raises(WorkPrivacyBlocked, match='review'):
        protocol.upgrade_stream_history(reviewed=False, expected_digest=preview['inventory_sha256'])
    with pytest.raises(WorkPrivacyBlocked, match='review|inventory'):
        protocol.upgrade_stream_history(reviewed=True, expected_digest='0' * 64)
    assert protocol.current()['schema'] == 'artha.work-member-history.v1'
    assert not (protocol.authority_dir / 'pending').exists()

    upgraded = protocol.upgrade_stream_history(reviewed=True, expected_digest=preview['inventory_sha256'])
    assert upgraded['schema'] == 'artha.work-member-history.v2'
    assert upgraded['generation'] == 2 and upgraded['stream_since'] == 2
    assert upgraded['streams'] == stream_inventory
    assert upgraded['events'][0] == legacy_event
    assert protocol.check_checkpoint(legacy)['generation'] == 2
    current = protocol.bind_checkpoint()
    assert current['schema'] == 'artha.work-checkpoint-binding.v2'
    assert current['streams'] == stream_inventory
    assert protocol.check_checkpoint(current)['generation'] == 2
    assert not (protocol.authority_dir / 'pending').exists()
    for path in protocol.authority_dir.iterdir():
        if path.is_file():
            assert b'synthetic-stream' not in path.read_bytes()
            assert b'root-stream' not in path.read_bytes()


def test_stream_only_drift_requires_review_and_invalidates_old_binding(protocol, stream_inventory):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'tracked')
    stream_inventory[key] = _stream_record(b'old')
    _upgrade_streams(protocol)
    old = protocol.bind_checkpoint()
    assert protocol.denied_streams(old, [key]) == set()

    stream_inventory[key] = _stream_record(b'new')
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.current()
    with pytest.raises(WorkPrivacyBlocked, match='review'):
        protocol.reconcile(reviewed=False)
    reconcile_reviewed(protocol)
    assert protocol.denied_streams(old, [key]) == {key}
    fresh = protocol.bind_checkpoint()
    assert protocol.denied_streams(fresh, [key]) == set()

    del stream_inventory[key]
    reconcile_reviewed(protocol)
    assert protocol.denied_streams(fresh, [key]) == {key}
    stream_inventory[key] = _stream_record(b'old')
    reconcile_reviewed(protocol)
    assert protocol.denied_streams(old, [key]) == {key}


def test_changed_owner_invalidates_unchanged_old_stream(protocol, stream_inventory):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'tracked')
    stream_inventory[key] = _stream_record(b'unchanged')
    _upgrade_streams(protocol)
    binding = protocol.bind_checkpoint()

    (protocol.roots['work_state'] / 'notes.md').write_bytes(b'corrected owner')
    reconcile_reviewed(protocol)

    assert protocol.denied_streams(binding, [key]) == {key}
    assert protocol.denied_streams(protocol.bind_checkpoint(), [key]) == set()


def test_stream_binding_holds_directory_and_root_and_propagates_file_revocation(protocol, stream_inventory):
    enroll(protocol)
    (protocol.roots['work_state'] / 'folder').mkdir()
    file_stream = _stream_key('work_state/notes.md', 'file', 'file-stream')
    directory_stream = _stream_key('work_state/folder', 'directory', 'directory-stream')
    root_stream = _stream_key('work_state', 'directory', 'root-stream')
    missing = _stream_key('work_state/notes.md', 'file', 'not-in-checkpoint')
    stream_inventory.update({key: _stream_record(b'') for key in (file_stream, directory_stream, root_stream)})
    _upgrade_streams(protocol)
    binding = protocol.bind_checkpoint()

    assert protocol.denied_streams(binding, [file_stream, directory_stream, root_stream, missing]) == {
        directory_stream, root_stream, missing}
    protocol.publish({}, revoke=['work_state/notes.md'])
    assert protocol.denied_streams(binding, [file_stream]) == {file_stream}
    assert protocol.denied_streams(protocol.bind_checkpoint(), [file_stream]) == {file_stream}


def test_stream_binding_is_checked_against_authenticated_history(protocol, stream_inventory):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'tracked')
    stream_inventory[key] = _stream_record(b'old')
    _upgrade_streams(protocol)
    binding = protocol.bind_checkpoint()
    body = {name: value for name, value in binding.items() if name != 'mac'}
    forged = protocol._sign(dict(body, streams={key: _stream_record(b'other')}), 'checkpoint')
    with pytest.raises(WorkPrivacyBlocked, match='history'):
        protocol.check_checkpoint(forged)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.denied_streams(binding, ['not-a-stream-identity'])


def test_legacy_binding_cannot_omit_a_new_stream_generation(protocol, stream_inventory):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'tracked')
    stream_inventory[key] = _stream_record(b'content')
    _upgrade_streams(protocol)
    binding = protocol.bind_checkpoint()
    downgraded = {name: value for name, value in binding.items() if name not in {'mac', 'streams'}}
    downgraded['schema'] = 'artha.work-checkpoint-binding.v1'

    with pytest.raises(WorkPrivacyBlocked, match='stream|binding'):
        protocol.check_checkpoint(protocol._sign(downgraded, 'checkpoint'))


@pytest.mark.parametrize('bad_streams', [
    {'not-json': _stream_record(b'')},
    {_stream_key('work_state', 'file', 'name'): _stream_record(b'')},
    {_stream_key('work_state/missing.md', 'file', 'name'): _stream_record(b'')},
    {_stream_key('work_state/notes.md', 'file', 'unsafe:name'): _stream_record(b'')},
    {_stream_key('work_state/notes.md', 'file', 'name'): {'size': True, 'sha256': '0' * 64}},
    {_stream_key('work_state/notes.md', 'file', 'name'): {'size': 0, 'sha256': 'bad'}},
    {
        _stream_key('work_state/notes.md', 'file', 'Name'): _stream_record(b''),
        _stream_key('work_state/notes.md', 'file', 'name'): _stream_record(b''),
    },
    {
        _stream_key('work_state/notes.md', 'file', 'caf\u00e9'): _stream_record(b''),
        _stream_key('work_state/notes.md', 'file', 'cafe\u0301'): _stream_record(b''),
    },
])
def test_stream_migration_refuses_invalid_or_ambiguous_inventories(protocol, stream_inventory, bad_streams):
    enroll(protocol)
    stream_inventory.update(bad_streams)

    with pytest.raises(WorkPrivacyBlocked):
        protocol.preview_stream_history()

    assert protocol.current()['schema'] == 'artha.work-member-history.v1'
    assert not (protocol.authority_dir / 'pending').exists()


def test_stream_migration_refuses_inventory_over_total_bound(protocol, stream_inventory, monkeypatch):
    enroll(protocol)
    stream_inventory[_stream_key('work_state/notes.md', 'file', 'name')] = _stream_record(b'abc')
    monkeypatch.setattr(module, '_MAX_STREAM_TOTAL_BYTES', 2)

    with pytest.raises(WorkPrivacyBlocked, match='bound'):
        protocol.preview_stream_history()

    assert not (protocol.authority_dir / 'pending').exists()


def test_stream_migration_refuses_too_many_streams_for_one_owner(protocol, stream_inventory, monkeypatch):
    enroll(protocol)
    stream_inventory.update({
        _stream_key('work_state/notes.md', 'file', f'item-{index}'): _stream_record(b'')
        for index in range(3)
    })
    monkeypatch.setattr(module, '_MAX_STREAMS_PER_OWNER', 2)

    with pytest.raises(WorkPrivacyBlocked, match='bound'):
        protocol.preview_stream_history()

    assert not (protocol.authority_dir / 'pending').exists()


@pytest.mark.parametrize('stage', ['intent', 'capsule', 'head', 'readback', 'complete'])
def test_stream_history_upgrade_recovers_from_interruption(protocol, stream_inventory, stage):
    enroll(protocol)
    stream_inventory[_stream_key('work_state/notes.md', 'file', 'tracked')] = _stream_record(b'old')
    preview = protocol.preview_stream_history()

    def interrupt(actual):
        if actual == stage:
            raise RuntimeError('synthetic stream-upgrade interruption')

    protocol.observe = interrupt
    with pytest.raises(RuntimeError, match='stream-upgrade interruption'):
        protocol.upgrade_stream_history(reviewed=True, expected_digest=preview['inventory_sha256'])
    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked, match='pending'):
        protocol.current()
    protocol.recover_pending()
    assert protocol.current()['schema'] == 'artha.work-member-history.v2'
    assert protocol.current()['streams'] == stream_inventory
    assert not (protocol.authority_dir / 'pending').exists()


def test_stream_change_during_upgrade_fails_before_capsule_and_keeps_intent(protocol, stream_inventory):
    enroll(protocol)
    key = _stream_key('work_state/notes.md', 'file', 'tracked')
    stream_inventory[key] = _stream_record(b'old')
    preview = protocol.preview_stream_history()
    stages = []

    def mutate(stage):
        stages.append(stage)
        if stage == 'intent':
            stream_inventory[key] = _stream_record(b'changed')

    protocol.observe = mutate
    with pytest.raises(WorkPrivacyBlocked, match='stream|drift'):
        protocol.upgrade_stream_history(reviewed=True, expected_digest=preview['inventory_sha256'])
    assert stages == ['intent']
    assert (protocol.authority_dir / 'pending').exists()
    with pytest.raises(WorkPrivacyBlocked, match='stream|drift'):
        protocol.recover_pending()
    assert (protocol.authority_dir / 'pending').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream history')
def test_native_stream_history_tracks_file_directory_and_root_streams(protocol):
    enroll(protocol)
    root = protocol.roots['work_state']
    directory = root / 'folder'
    directory.mkdir()
    owner = root / 'notes.md'
    Path(str(owner) + ':file-stream').write_bytes(b'exact synthetic bytes')
    Path(str(directory) + ':directory-stream').write_bytes(b'directory')
    Path(str(root) + ':root-stream').write_bytes(b'')
    _upgrade_streams(protocol)
    binding = protocol.bind_checkpoint()
    file_stream = _stream_key('work_state/notes.md', 'file', 'file-stream')
    directory_stream = _stream_key('work_state/folder', 'directory', 'directory-stream')
    root_stream = _stream_key('work_state', 'directory', 'root-stream')

    assert binding['streams'] == {
        file_stream: _stream_record(b'exact synthetic bytes'),
        directory_stream: _stream_record(b'directory'),
        root_stream: _stream_record(b''),
    }
    assert protocol.denied_streams(binding, [file_stream, directory_stream, root_stream]) == {
        directory_stream, root_stream}
    Path(str(owner) + ':file-stream').write_bytes(b'changed')
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.current()
    reconcile_reviewed(protocol)
    assert protocol.denied_streams(binding, [file_stream]) == {file_stream}


def test_windows_identity_uses_64bit_machine_guid_and_existing_token_sid(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from catchup_core import quarantine_fs_windows

    calls = []
    registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, KEY_READ=2, KEY_WOW64_64KEY=4, REG_SZ=1)
    registry.OpenKey = lambda *args: calls.append(args) or nullcontext('native-key')
    registry.QueryValueEx = lambda key, name: ('12345678-1234-1234-1234-123456789abc', 1)
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(quarantine_fs_windows, '_native', lambda: SimpleNamespace(
        current_user_sid=lambda: ('S-1-5-21-1234', b'synthetic-SID')))
    assert module.native_windows_identity() == {
        'platform': 'win32', 'machine': '12345678-1234-1234-1234-123456789abc', 'user': 'S-1-5-21-1234'}
    assert calls == [(1, r'SOFTWARE\Microsoft\Cryptography', 0, 6)]
    registry.QueryValueEx = lambda *_: ('hostname-is-not-an-identity', 1)
    with pytest.raises(WorkPrivacyBlocked):
        module.native_windows_identity()


CHILD = r"""
import base64, hashlib, json, os, sys, time
from pathlib import Path
from catchup_core import work_privacy
from catchup_core.work_privacy import WorkPrivacy
if os.name != 'nt':
    from _work_synthetic import synthetic_member_creation
    work_privacy._native_creation_available = lambda: True
    work_privacy._create_member = synthetic_member_creation
class Backend:
    def get_password(self, service, account):
        p = root / ('native-' + service + '-' + account)
        return p.read_text() if p.exists() else None
    def set_password(self, service, account, value):
        p = root / ('native-' + service + '-' + account)
        p.write_text(value)
root = Path(sys.argv[1])
def observe(stage):
    if stage == sys.argv[3]:
        (root / 'ready').write_text(stage)
        if sys.argv[2] in {'die', 'stream-die', 'policy-die'}:
            os._exit(73)
        while not (root / 'release').exists():
            time.sleep(.02)
p = WorkPrivacy(root / 'repo', root / 'local', {'work_state': root / 'repo/state/work'},
    backend=Backend(), native_identity=lambda: {
        'platform': 'win32', 'machine': 'synthetic-machine', 'user': 'S-1-5-21-synthetic'}, observe=observe)
if sys.argv[2] == 'enroll':
    p.enroll(b'x' * 32, reviewed=True, installation_id='synthetic-installation')
elif sys.argv[2] == 'recover':
    p.recover_pending()
elif sys.argv[2] == 'stream-die':
    preview = p.preview_stream_history()
    p.upgrade_stream_history(reviewed=True, expected_digest=preview['inventory_sha256'])
elif sys.argv[2] == 'stream-check':
    current = p.current()
    key = json.dumps(['work_state/notes.md', 'file', 'tracked', '$DATA'], separators=(',', ':'))
    assert current['schema'] == 'artha.work-member-history.v2'
    assert current['streams'][key] == {'size': 16, 'sha256': hashlib.sha256(b'synthetic stream').hexdigest()}
elif sys.argv[2] == 'policy-die':
    preview = p.preview_stream_policy()
    p.adopt_stream_policy(reviewed=True, expected_digest=preview['inventory_sha256'])
elif sys.argv[2] == 'policy-check':
    current = p.current()
    assert current['schema'] == 'artha.work-member-history.v3'
    assert current['generation'] == 3 and not current['streams']
else:
    p.publish({'work_state/new.md': b'from separate process'})
"""


def child(protocol, mode, stage='none'):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / 'scripts'), str(root / 'tests'))))
    return subprocess.Popen([sys.executable, '-c', CHILD, str(protocol.repo.parent), mode, stage],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def finish(process, expected=0):
    out, err = process.communicate(timeout=30)
    assert process.returncode == expected, (out.decode(), err.decode())


@pytest.mark.parametrize('stage', ['intent', 'capsule', 'member', 'head', 'readback', 'complete'])
def test_real_process_death_retains_pending_and_recovers_only_when_unambiguous(protocol, stage):
    finish(child(protocol, 'enroll'))
    finish(child(protocol, 'die', stage), 73)
    destination = protocol.roots['work_state'] / 'new.md'
    if stage in {'intent', 'capsule'}:
        assert not destination.exists()
        finish(child(protocol, 'recover'))
        assert destination.read_bytes() == b'from separate process'
        assert not (protocol.authority_dir / 'pending').exists()
    else:
        assert destination.read_bytes() == b'from separate process'
        recovery = child(protocol, 'recover')
        _, error = recovery.communicate(timeout=30)
        assert recovery.returncode != 0 and b'review' in error
        assert destination.read_bytes() == b'from separate process'
        assert (protocol.authority_dir / 'pending').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='requires native Windows named streams')
@pytest.mark.parametrize('stage', ['intent', 'capsule', 'head', 'readback', 'complete'])
def test_stream_history_survives_real_process_death(protocol, stage):
    finish(child(protocol, 'enroll'))
    stream = Path(str(protocol.roots['work_state'] / 'notes.md') + ':tracked')
    stream.write_bytes(b'synthetic stream')

    finish(child(protocol, 'stream-die', stage), 73)
    assert (protocol.authority_dir / 'pending').exists()
    finish(child(protocol, 'recover'))
    finish(child(protocol, 'stream-check'))
    assert not (protocol.authority_dir / 'pending').exists()
    assert stream.read_bytes() == b'synthetic stream'


@pytest.mark.skipif(sys.platform != 'win32', reason='requires native Windows named streams')
@pytest.mark.parametrize('stage', ['intent', 'capsule', 'head', 'readback', 'complete'])
def test_endpointdlp_policy_survives_real_process_death(protocol, stage):
    finish(child(protocol, 'enroll'))
    stream = Path(str(protocol.roots['work_state'] / 'notes.md') + ':sec.endpointdlp')
    stream.write_bytes(b'synthetic stream')
    finish(child(protocol, 'stream-die', 'none'))
    finish(child(protocol, 'policy-die', stage), 73)
    assert (protocol.authority_dir / 'pending').exists()
    stream.write_bytes(b'changed volatile metadata')
    finish(child(protocol, 'recover'))
    finish(child(protocol, 'policy-check'))
    assert not (protocol.authority_dir / 'pending').exists()
    assert stream.read_bytes() == b'changed volatile metadata'


def test_independent_writer_is_excluded_while_first_is_pending(protocol):
    import time

    finish(child(protocol, 'enroll'))
    first = child(protocol, 'hold', 'intent')
    ready = protocol.repo.parent / 'ready'
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        second = child(protocol, 'write')
        out, err = second.communicate(timeout=30)
        assert second.returncode != 0
        assert b'SessionBusy' in err, (out, err)
    finally:
        (protocol.repo.parent / 'release').write_text('release')
        finish(first)
