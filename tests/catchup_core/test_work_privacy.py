"""Local Work recovery restrictions; native identity and credentials are synthetic."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from catchup_core.work_privacy import WorkPrivacy, WorkPrivacyBlocked
from catchup_core import work_privacy as module


IDENTITY = {'platform': 'win32', 'machine': 'synthetic-machine', 'user': 'S-1-5-21-synthetic'}
KEY = b'x' * 32


class Backend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


@pytest.fixture
def protocol(tmp_path, monkeypatch):
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


def test_changed_deleted_and_revoked_paths_deny_all_older_members(protocol):
    enroll(protocol)
    historical = protocol.bind_checkpoint()
    protocol.publish({'work_state/notes.md': b'corrected', 'work_state/new.md': b'new'})
    assert protocol.denied_members(historical, ['work_state/notes.md']) == {'work_state/notes.md'}
    fresh = protocol.bind_checkpoint()
    assert protocol.denied_members(fresh, ['work_state/notes.md']) == set()
    protocol.publish({'work_state/notes.md': None}, revoke=['work_state/new.md'])
    assert protocol.denied_members(fresh, ['work_state/notes.md', 'work_state/new.md']) == {
        'work_state/notes.md', 'work_state/new.md'}
    current = protocol.bind_checkpoint()
    assert protocol.denied_members(current, ['work_state/new.md']) == {'work_state/new.md'}
    protocol.publish({'work_state/new.md': b'rewritten'})
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
    protocol.reconcile(reviewed=True)
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


@pytest.mark.parametrize('stage', ['intent', 'capsule', 'member', 'head', 'readback', 'complete'])
def test_interrupted_publication_never_succeeds_and_rolls_forward(protocol, stage):
    enroll(protocol)
    old = protocol.bind_checkpoint()

    def interrupt(actual):
        if actual == stage:
            raise RuntimeError('synthetic interruption')

    protocol.observe = interrupt
    with pytest.raises(RuntimeError, match='interruption'):
        protocol.publish({'work_state/notes.md': b'new synthetic notes'})
    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked, match='pending'):
        protocol.current()
    protocol.recover_pending()
    assert protocol.current()['generation'] == 2
    assert (protocol.roots['work_state'] / 'notes.md').read_bytes() == b'new synthetic notes'
    assert protocol.denied_members(old, ['work_state/notes.md']) == {'work_state/notes.md'}
    assert not (protocol.authority_dir / 'pending').exists()


def test_pending_and_capsules_never_contain_plaintext(protocol):
    enroll(protocol)
    protocol.observe = lambda stage: (_ for _ in ()).throw(RuntimeError()) if stage == 'intent' else None
    with pytest.raises(RuntimeError):
        protocol.publish({'work_state/notes.md': b'UNIQUE-SYNTHETIC-PRIVATE-BODY'})
    for path in protocol.authority_dir.iterdir():
        if path.is_file():
            assert b'UNIQUE-SYNTHETIC-PRIVATE-BODY' not in path.read_bytes()


@pytest.mark.parametrize('stage', ['member', 'head', 'readback', 'complete'])
def test_external_head_change_blocks_success_and_recovery(protocol, stage):
    enroll(protocol)

    def change(actual):
        if actual == stage:
            protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', '{"changed":true}')

    protocol.observe = change
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({'work_state/notes.md': b'new'})
    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked):
        protocol.recover_pending()


def test_rollback_of_native_head_or_acknowledged_capsule_refuses(protocol):
    enroll(protocol)
    head = protocol.backend.get_password(protocol.HEAD_SERVICE, 'head')
    protocol.publish({'work_state/notes.md': b'new'})
    protocol.backend.set_password(protocol.HEAD_SERVICE, 'head', head)
    with pytest.raises(WorkPrivacyBlocked):
        protocol.current()


def test_pending_recovery_never_adopts_an_unknown_intervening_edit(protocol):
    enroll(protocol)
    protocol.observe = lambda stage: (_ for _ in ()).throw(RuntimeError()) if stage == 'member' else None
    with pytest.raises(RuntimeError):
        protocol.publish({'work_state/notes.md': b'new'})
    (protocol.roots['work_state'] / 'notes.md').write_bytes(b'unknown')
    protocol.observe = lambda _: None
    with pytest.raises(WorkPrivacyBlocked, match='drift'):
        protocol.recover_pending()


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
        transaction.publish({'work_state/notes.md': b'one'})
        with pytest.raises(WorkPrivacyBlocked, match='closed'):
            transaction.publish({'work_state/notes.md': b'two'})
    with pytest.raises(WorkPrivacyBlocked, match='closed'):
        transaction.publish({'work_state/notes.md': b'three'})


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
    protocol.reconcile(reviewed=True)
    binding = protocol.bind_checkpoint()
    assert protocol.denied_members(binding, ['work_state/notes.md']) == {'work_state/notes.md'}


def test_key_changed_at_member_publication_blocks_before_head_advance(protocol):
    enroll(protocol)
    head = protocol.backend.get_password(protocol.HEAD_SERVICE, 'head')

    def change(stage):
        if stage == 'member':
            protocol.backend.set_password(protocol.KEY_SERVICE, 'key', base64.b64encode(b'z' * 32).decode())

    protocol.observe = change
    with pytest.raises(WorkPrivacyBlocked):
        protocol.publish({'work_state/notes.md': b'changed'})
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
import base64, json, os, sys, time
from pathlib import Path
from catchup_core.work_privacy import WorkPrivacy
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
        if sys.argv[2] == 'die':
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
else:
    p.publish({'work_state/notes.md': b'from separate process'})
"""


def child(protocol, mode, stage='none'):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'scripts'))
    return subprocess.Popen([sys.executable, '-c', CHILD, str(protocol.repo.parent), mode, stage],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def finish(process, expected=0):
    out, err = process.communicate(timeout=30)
    assert process.returncode == expected, (out.decode(), err.decode())


@pytest.mark.parametrize('stage', ['intent', 'capsule', 'member', 'head', 'readback', 'complete'])
def test_real_process_death_retains_pending_and_recovers(protocol, stage):
    finish(child(protocol, 'enroll'))
    finish(child(protocol, 'die', stage), 73)
    finish(child(protocol, 'recover'))
    assert (protocol.roots['work_state'] / 'notes.md').read_bytes() == b'from separate process'
    assert not (protocol.authority_dir / 'pending').exists()


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
