"""Approved recovery commands over synthetic checkpoints and current privacy history."""
from __future__ import annotations

import io
import json
from datetime import timedelta

import pytest

from catchup_core import checkpoint_cli as cli
from catchup_core import checkpoint as ck
from test_checkpoint_restore_integrity import CIPHER
import test_recovery_access as access_support

SYNTHETIC_PASSPHRASE = 'synthetic checkpoint passphrase'


@pytest.fixture
def context(tmp_path, monkeypatch):
    fixture = access_support.setup.__wrapped__(tmp_path)
    value = next(fixture)
    monkeypatch.setattr(cli, '_REPO', value.repo)
    monkeypatch.setattr(cli, '_cipher', lambda *args, **kwargs: CIPHER)
    monkeypatch.setattr(cli, 'profile_layout', lambda *args, **kwargs: ({'repo': value.repo}, {}))
    try:
        yield value
    finally:
        fixture.close()


def run(context, *args, now=access_support.NOW, passphrase=lambda _confirm: SYNTHETIC_PASSPHRASE,
        host_platform=None):
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(args, out, err, backend=context.access.backend,
                   sealed_for=lambda mode: context.sealed, local=context.root / 'private',
                   passphrase=passphrase, now=lambda: now, host_platform=host_platform)
    return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()


def restored(context):
    original = access_support.checkpoint(context)
    before = original.read_bytes()
    code, report, error = run(context, 'restore', str(original), '--to', str(context.dest))
    assert code == 0, error
    assert report['quarantined_files'] == 1
    assert original.read_bytes() == before
    return original


def member(context):
    code, result, error = run(context, 'quarantine-list', str(context.dest))
    assert code == 0, error
    assert result['activation_allowed'] is False
    assert len(result['members']) == 1
    assert 'path' not in result['members'][0]
    assert 'SYNTHETIC-PRIVATE-NOTES' not in json.dumps(result)
    return result['members'][0]['member_id']


def grant(context):
    context.ledger.grant_consent('adult', 'adult', 'recovery', ['view', 'export'], 'owner')
    context.sealed.refresh(context.ledger)


def test_restore_and_metadata_inventory_never_write_historical_plaintext(context):
    restored(context)
    member(context)
    assert not (context.dest / 'repo/notes.txt').exists()
    assert (context.dest / '.artha-restored.json').is_file()
    for path in context.dest.rglob('*'):
        if path.is_file():
            assert b'SYNTHETIC-PRIVATE-NOTES' not in path.read_bytes()


def test_review_view_and_encrypted_release_require_current_export_consent(context):
    restored(context)
    identity = member(context)
    args = ('quarantine-review', str(context.dest), identity, 'notes.txt')
    assert run(context, *args)[0] == 20
    grant(context)
    code, preview, error = run(context, *args)
    assert code == 0, error
    token = preview['review_digest']
    code, viewed, error = run(context, 'quarantine-view', str(context.dest), identity, 'notes.txt',
                              '--review', token)
    assert code == 0, error
    assert viewed['text'] == 'SYNTHETIC-PRIVATE-NOTES'
    assert viewed['untrusted'] is True and viewed['view_only'] is True
    release = ('quarantine-release', str(context.dest), identity, 'notes.txt', '--review', token)
    assert run(context, *release)[0] == 20
    code, receipt, error = run(context, *release, '--attest-clean')
    assert code == 0, error
    assert receipt['encrypted'] is True and receipt['plaintext_written'] is False
    output = context.dest / 'reviewed-exports/notes.txt.ae1'
    assert output.read_bytes().startswith(b'AE1')
    assert b'SYNTHETIC-PRIVATE-NOTES' not in output.read_bytes()
    assert run(context, *release, '--attest-clean')[0] == 20


@pytest.mark.parametrize('command', ['quarantine-list', 'quarantine-review', 'quarantine-view',
                                    'quarantine-release'])
def test_wrong_actor_is_refused_without_releasing_content(context, command):
    restored(context)
    identity = member(context)
    extra = () if command == 'quarantine-list' else (identity, 'notes.txt')
    if command in {'quarantine-view', 'quarantine-release'}:
        extra += ('--review', 'not-an-approval')
    code, result, error = run(context, command, str(context.dest), *extra, '--actor', 'other')
    assert code == 20 and result is None
    assert 'SYNTHETIC-PRIVATE-NOTES' not in error
    assert not (context.dest / 'reviewed-exports').exists()


def test_withdrawn_consent_and_expired_review_cannot_publish(context):
    restored(context)
    identity = member(context)
    grant(context)
    code, preview, error = run(context, 'quarantine-review', str(context.dest), identity, 'notes.txt')
    assert code == 0, error
    args = ('quarantine-release', str(context.dest), identity, 'notes.txt',
            '--review', preview['review_digest'], '--attest-clean')
    assert run(context, *args, now=access_support.NOW + timedelta(minutes=16))[0] == 20
    consent = context.ledger.conn.execute("SELECT consent_id FROM consent WHERE subject_id='adult'").fetchone()[0]
    context.ledger.revoke_consent(consent)
    context.sealed.history = context.ledger.privacy_history()
    assert run(context, *args)[0] == 20
    assert not (context.dest / 'reviewed-exports/notes.txt.ae1').exists()


def test_work_restore_and_key_installation_remain_held(context, monkeypatch):
    from catchup_core import recovery_registry
    from catchup_core import work_privacy

    registry_root = context.root / 'private'
    for kind, service, account, generation in (
        ('vault', 'age-key', 'artha', 'legacy'),
        ('key_head', 'artha-work-checkpoint', 'current_version', '1'),
        ('key', 'artha-work-checkpoint', 'key.1', '1'),
    ):
        recovery_registry.declare('work', kind, service, account, generation, root=registry_root)
    registry = recovery_registry.snapshot('work', root=registry_root, require_review=False)
    registry = recovery_registry.attest_legacy(
        'work', recovery_registry.registry_digest(registry), 'synthetic test reviewer',
        access_support.NOW.isoformat(), root=registry_root,
    )
    escrow = {
        'age-key/artha': b'AGE-SECRET-KEY-SYNTHETIC',
        'artha-work-checkpoint/current_version': b'1',
        'artha-work-checkpoint/key.1': b'r' * 32,
    }
    original = ck.create(
        'work', {'repo': context.repo}, context.root / 'synthetic-checkpoints', CIPHER, escrow,
        SYNTHETIC_PASSPHRASE, now=access_support.NOW, lock_path=context.root / 'checkpoint.lock',
        context={'host_os': 'win32', 'recovery_registry': registry},
    )

    monkeypatch.setattr(work_privacy, 'native_windows_identity',
                        lambda: pytest.fail('unbound checkpoint reached native Work identity'))
    code, inspected, error = run(context, 'recovery', str(original), '--inspection-only')
    assert code == 0, error
    assert inspected['work_binding'] == 'unbound_inspection_only'
    assert inspected['inspection_only'] is True and inspected['activation_allowed'] is False

    code, result, error = run(context, 'restore', str(original), '--to', str(context.dest), '--profile', 'work',
                              host_platform='win32')
    assert code == 20 and result is None and 'unbound Work checkpoints are inspection-only' in error
    assert not context.dest.exists()
    code, result, error = run(context, 'recovery', str(original), '--restore-keys')
    assert code == 20 and result is None and 'OS key installation requires' in error
    assert not context.dest.exists()


def test_wrong_restore_actor_is_checked_before_checkpoint_decryption(context, monkeypatch):
    original = access_support.checkpoint(context)
    def refuse_scan(*args, **kwargs):
        pytest.fail('checkpoint read before owner authorization')
    monkeypatch.setattr(ck, '_scan', refuse_scan)
    code, result, error = run(context, 'restore', str(original), '--to', str(context.dest), '--actor', 'other')
    assert code == 20 and result is None and 'owner' in error
    assert not context.dest.exists()

def test_reviewed_export_can_be_viewed_after_review_token_expiry_with_current_consent(context):
    restored(context)
    grant(context)
    identity = member(context)
    code, preview, error = run(context, 'quarantine-review', str(context.dest), identity, 'notes.txt')
    assert code == 0, error
    code, _, error = run(context, 'quarantine-release', str(context.dest), identity, 'notes.txt',
                         '--review', preview['review_digest'], '--attest-clean')
    assert code == 0, error
    output = context.dest / 'reviewed-exports/notes.txt.ae1'
    encrypted = output.read_bytes()
    code, viewed, error = run(context, 'quarantine-export-view', str(context.dest), 'notes.txt',
                              now=access_support.NOW + timedelta(minutes=31))
    assert code == 0, error
    assert viewed['text'] == 'SYNTHETIC-PRIVATE-NOTES'
    assert viewed['untrusted'] and viewed['view_only'] and not viewed['plaintext_written']
    assert output.read_bytes() == encrypted
    code, _, _ = run(context, 'quarantine-export-view', str(context.dest), 'notes.txt', '--actor', 'other')
    assert code == 20
    context.ledger.revoke_consent(context.ledger.conn.execute(
        "SELECT consent_id FROM consent WHERE scope='recovery'").fetchone()[0])
    context.sealed.refresh(context.ledger)
    code, value, error = run(context, 'quarantine-export-view', str(context.dest), 'notes.txt')
    assert code == 20 and value is None and 'SYNTHETIC-PRIVATE-NOTES' not in error


def test_personal_restore_uses_the_system_keyring_when_no_backend_is_injected(context, monkeypatch):
    from catchup_core import recovery_access
    native, seen = object(), []

    class Spy:
        def __init__(self, repo, backend, *args, **kwargs):
            seen.append(backend)

        def authorize(self):
            raise ck.CheckpointBlocked('spy stops before any restore')

    monkeypatch.setattr(cli, 'system_keyring', lambda: native)
    monkeypatch.setattr(recovery_access, 'RecoveryAccess', Spy)
    original = access_support.checkpoint(context)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(['restore', str(original), '--to', str(context.dest)], out, err,
                   local=context.root / 'private', passphrase=lambda _confirm: SYNTHETIC_PASSPHRASE,
                   now=lambda: access_support.NOW)
    assert code == 20 and seen == [native]


def test_privacy_digest_command_authorizes_then_prints_the_verified_history_digest(context, monkeypatch):
    from catchup_core import recovery_access
    calls = []

    class Spy:
        def __init__(self, repo, backend, *args, **kwargs):
            calls.append('built')

        def authorize(self):
            calls.append('authorized')

        def history_digest(self, modes):
            calls.append(sorted(modes))
            return 'a' * 64

    monkeypatch.setattr(cli, 'system_keyring', lambda: object())
    monkeypatch.setattr(recovery_access, 'RecoveryAccess', Spy)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(['privacy-digest', '--mode', 'shadow'], out, err, local=context.root / 'private',
                   now=lambda: access_support.NOW)
    assert code == 0, err.getvalue()
    assert json.loads(out.getvalue()) == {'modes': ['shadow'], 'history_sha256': 'a' * 64}
    assert calls == ['built', 'authorized', ['shadow']]
