"""Durable queue-first authority transfer over synthetic stores; no providers or native credentials."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from action_queue import ActionQueue
from catchup_core import authority_transfer as protocol
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger, PermissionDenied

NOW = '2026-10-24T12:00:00Z'
CIPHER = EnvelopeCipher(StaticKeyProvider({1: b't' * 32}))


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True)
    (repo / 'state').mkdir()
    local = tmp_path / 'local'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(local / 'actions.db'))
    monkeypatch.setenv('ARTHA_HOST', 'old-host')
    checkpoint = tmp_path / 'checkpoint.ack'
    checkpoint.write_bytes(b'synthetic encrypted preservation input')
    ledger = Ledger(local / 'catchup_core/ledger.db', id_key=HmacKey(1, b'i' * 32), cipher=CIPHER,
                    owner_id='owner', clock=lambda: NOW).open()
    ledger.migrate()
    ledger.ensure_authority_host('old-host')
    queue = ActionQueue(repo)
    monkeypatch.setattr(protocol, '_verify_checkpoint', lambda *_: {'generation': 'ck_synthetic', 'profile': 'personal'})
    def perform(**kwargs):
        return ledger.transfer_authority('new-host', actor='owner', checkpoint_path=checkpoint,
            workspace=repo, expected_generation=1, former_host_quiesced=True, **kwargs)
    yield repo, ledger, queue, checkpoint, perform
    queue.close()
    ledger.close()


def test_success_commits_matching_epochs_and_keeps_effects_paused(transfer):
    repo, ledger, queue, _, perform = transfer
    receipt = perform()
    assert ledger.authority_state()['generation'] == 2
    assert ledger.authority_state()['host'] == 'new-host'
    assert queue.authority_state()['generation'] == 2
    assert queue.authority_state()['transfer_pending'] is False
    assert receipt['former_host_revoked_at'] is None
    assert receipt['proof_kind'] == 'unverified_owner_attestation_quiescence'
    assert (repo / 'config/actions.paused').exists()


@pytest.mark.parametrize('bad', ['wrong-owner', 'wrong-generation', 'boolean-generation', 'coerced-proof'])
def test_invalid_transfer_cannot_pause_or_promote(transfer, bad):
    repo, ledger, queue, checkpoint, _ = transfer
    options = dict(actor='owner', checkpoint_path=checkpoint, workspace=repo,
                   expected_generation=1, former_host_quiesced=True)
    if bad == 'wrong-owner':
        options['actor'] = 'other'
    elif bad == 'wrong-generation':
        options['expected_generation'] = 4
    elif bad == 'boolean-generation':
        options['expected_generation'] = True
    else:
        options['former_host_quiesced'] = 'false'
    with pytest.raises((PermissionDenied, ValueError)):
        ledger.transfer_authority('new-host', **options)
    assert ledger.authority_state()['generation'] == 1
    assert queue.authority_state()['generation'] == 1
    assert not (repo / 'config/actions.paused').exists()


@pytest.mark.parametrize('phase', ['paused', 'queue_prepared', 'ledger_promoted', 'queue_finalized'])
def test_interrupted_transfer_resumes_idempotently_and_never_removes_pause(transfer, monkeypatch, phase):
    repo, ledger, queue, _, perform = transfer
    def interrupted(name):
        if name == phase:
            raise RuntimeError('synthetic interruption')
    monkeypatch.setattr(protocol, '_phase', interrupted)
    with pytest.raises(RuntimeError, match='interruption'):
        perform()
    assert (repo / 'config/actions.paused').exists()
    if phase == 'queue_prepared':
        assert ledger.authority_state()['generation'] == 1
        assert queue.authority_state()['transfer_pending']
        with pytest.raises(ValueError, match='pending'):
            protocol.require_dispatch_authority(queue._conn, host='new-host')
    monkeypatch.setattr(protocol, '_phase', lambda _: None)
    receipt = perform()
    again = perform()
    assert receipt['transfer_id'] == again['transfer_id']
    assert ledger.conn.execute('SELECT COUNT(*) FROM authority_generation').fetchone()[0] == 2
    assert (repo / 'config/actions.paused').exists()


def test_queue_failure_does_not_promote_ledger(transfer, monkeypatch):
    repo, ledger, queue, _, perform = transfer
    monkeypatch.setattr(ActionQueue, 'prepare_authority_transfer', lambda *_: (_ for _ in ()).throw(ValueError('blocked queue')))
    with pytest.raises(ValueError, match='blocked queue'):
        perform()
    assert ledger.authority_state()['generation'] == 1
    assert queue.authority_state()['generation'] == 1
    assert (repo / 'config/actions.paused').exists()


def test_resume_refuses_unpromoted_transfer_and_only_owner_may_resume(transfer, monkeypatch):
    repo, ledger, queue, _, perform = transfer
    monkeypatch.setattr(protocol, '_phase', lambda name: (_ for _ in ()).throw(RuntimeError('stop'))
                        if name == 'queue_prepared' else None)
    with pytest.raises(RuntimeError):
        perform()
    with pytest.raises(PermissionDenied, match='incomplete'):
        protocol.resume_effects(ledger, repo, actor='owner')
    assert (repo / 'config/actions.paused').exists()
    monkeypatch.setattr(protocol, '_phase', lambda _: None)
    perform()
    with pytest.raises(PermissionDenied, match='owner'):
        protocol.resume_effects(ledger, repo, actor='other')
    monkeypatch.setenv('ARTHA_HOST', 'new-host')
    protocol.resume_effects(ledger, repo, actor='owner')
    assert not (repo / 'config/actions.paused').exists()
    protocol.require_dispatch_authority(queue._conn, host='new-host')


@pytest.mark.parametrize(('column', 'value'), [
    ('generation', 0),
    ('generation', -1),
    ('generation', 'not-an-integer'),
    ('transfer_pending', -1),
    ('transfer_pending', 2),
    ('transfer_pending', 'not-a-boolean'),
])
def test_dispatch_rejects_invalid_persisted_metadata(transfer, column, value):
    _, ledger, queue, _, _ = transfer
    queue._conn.execute(f'UPDATE queue_authority SET {column}=?', (value,))
    with pytest.raises(ValueError, match='invalid authority metadata'):
        protocol.require_dispatch_authority(queue._conn, host='old-host')
    assert ledger.authority_state()['generation'] == 1


def test_dispatch_refuses_missing_singleton_without_recreating_it(transfer):
    _, ledger, queue, _, _ = transfer
    queue._conn.execute('DELETE FROM queue_authority')
    with pytest.raises(ValueError, match='authority metadata missing'):
        protocol.require_dispatch_authority(queue._conn, host='old-host')
    assert queue._conn.execute('SELECT COUNT(*) FROM queue_authority').fetchone()[0] == 0
    assert ledger.authority_state()['generation'] == 1


def test_initial_epoch_dispatch_needs_no_transfer_receipt_and_does_not_promote(transfer):
    repo, ledger, queue, _, _ = transfer
    before = ledger.authority_state(), queue.authority_state()
    protocol.require_dispatch_authority(queue._conn, host='old-host')
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert queue.authority_state()['transfer'] is None
    assert not (repo / 'config/actions.paused').exists()


@pytest.mark.parametrize(('payload', 'reason'), [
    (b'AE1synthetic selective rollback', 'selective rollback snapshot'),
    (b'ACK2', 'could not be decrypted and verified'),
])
def test_real_checkpoint_verifier_refuses_selective_or_truncated_evidence(tmp_path, payload, reason):
    checkpoint = tmp_path / 'invalid-checkpoint.ack'
    checkpoint.write_bytes(payload)
    with pytest.raises(PermissionDenied, match=reason):
        protocol._verify_checkpoint(checkpoint, CIPHER, hashlib.sha256(payload).hexdigest())
    assert checkpoint.read_bytes() == payload


def test_real_process_retry_refuses_changed_checkpoint_without_rewriting_pending_intent(transfer):
    import signal
    import subprocess
    repo, ledger, queue, checkpoint, perform = transfer
    original = checkpoint.read_bytes()
    dead = subprocess.run(_child_command(repo, ledger, checkpoint, 'queue_prepared'),
                          capture_output=True, timeout=20)
    expected_exit = signal.SIGTERM if os.name == 'nt' else -signal.SIGKILL
    assert dead.returncode == expected_exit, dead.stderr.decode()
    before = ledger.authority_state(), queue.authority_state()
    checkpoint.write_bytes(b'changed synthetic checkpoint')
    refused = subprocess.run(_child_command(repo, ledger, checkpoint, 'unused'),
                             capture_output=True, timeout=20)
    assert refused.returncode != 0
    assert b'conflicting pending or stale authority transfer' in refused.stderr
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert queue.authority_state()['transfer_pending']
    assert ledger.authority_state()['generation'] == 1
    assert (repo / 'config/actions.paused').exists()
    checkpoint.write_bytes(original)
    receipt = perform()
    assert receipt == before[1]['transfer']
    assert ledger.authority_state()['generation'] == queue.authority_state()['generation'] == 2
    protocol.require_dispatch_authority(queue._conn, host='new-host')
    assert (repo / 'config/actions.paused').exists()


def test_tampered_or_stale_ledger_epoch_cannot_authorize_dispatch(transfer):
    _, ledger, queue, _, perform = transfer
    perform()
    with pytest.raises(ValueError, match='host'):
        protocol.require_dispatch_authority(queue._conn, host='old-host')
    ledger.conn.execute("UPDATE authority_generation SET host='different' WHERE generation=2")
    with pytest.raises(ValueError, match='epoch'):
        protocol.require_dispatch_authority(queue._conn, host='new-host')


def test_conflicting_pending_target_or_checkpoint_refuses_without_rewrite(transfer, monkeypatch, tmp_path):
    repo, ledger, queue, checkpoint, perform = transfer
    monkeypatch.setattr(protocol, '_phase', lambda name: (_ for _ in ()).throw(RuntimeError('stop'))
                        if name == 'queue_prepared' else None)
    with pytest.raises(RuntimeError):
        perform()
    original = queue.authority_state()
    monkeypatch.setattr(protocol, '_phase', lambda _: None)
    with pytest.raises(PermissionDenied, match='pending'):
        ledger.transfer_authority('another-host', actor='owner', checkpoint_path=checkpoint,
            workspace=repo, expected_generation=1, former_host_quiesced=True)
    checkpoint.write_bytes(b'different checkpoint')
    with pytest.raises(PermissionDenied, match='pending'):
        perform()
    assert queue.authority_state() == original
    assert ledger.authority_state()['generation'] == 1


_CHILD = '''
import os, signal, sys, time
from pathlib import Path
kill_signal = signal.SIGTERM if os.name == 'nt' else signal.SIGKILL
from catchup_core import authority_transfer as protocol
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger
repo, path, cp, phase, marker, release = map(Path,sys.argv[1:])
protocol._verify_checkpoint = lambda *_: {'generation':'ck_synthetic', 'profile':'personal'}
def boundary(name):
    if name != str(phase):
        return
    if str(marker) == 'kill':
        os.kill(os.getpid(),kill_signal)
    marker.write_text(name)
    deadline = time.monotonic() + 15
    while not release.exists():
        if time.monotonic() > deadline:
            raise RuntimeError('release timeout')
        time.sleep(.01)
protocol._phase = boundary
with Ledger(path,id_key=HmacKey(1,b'i'*32),cipher=EnvelopeCipher(StaticKeyProvider({1:b't'*32})),
            owner_id='owner',clock=lambda:'2026-10-24T12:00:00Z') as ledger:
    ledger.transfer_authority('new-host',actor='owner',checkpoint_path=cp,workspace=repo,
                              expected_generation=1,former_host_quiesced=True)
'''


def _child_command(repo, ledger, checkpoint, phase, marker='kill', release='unused'):
    import sys
    source = 'import sys;sys.path.insert(0,' + repr(str(Path(protocol.__file__).parent.parent)) + ')\n' + _CHILD
    return [sys.executable, '-c', source, str(repo), str(ledger._path), str(checkpoint), phase, str(marker), str(release)]


@pytest.mark.parametrize('phase', ['paused', 'queue_prepared', 'ledger_promoted', 'queue_finalized'])
def test_real_process_death_at_every_committed_effect_step(transfer, phase):
    import signal
    import subprocess
    repo, ledger, queue, checkpoint, perform = transfer
    dead = subprocess.run(_child_command(repo, ledger, checkpoint, phase), capture_output=True, timeout=20)
    expected_exit = signal.SIGTERM if os.name == 'nt' else -signal.SIGKILL
    assert dead.returncode == expected_exit, dead.stderr.decode()
    assert (repo / 'config/actions.paused').exists()
    if phase in ('paused', 'queue_prepared'):
        assert ledger.authority_state()['generation'] == 1
    else:
        assert ledger.authority_state()['generation'] == 2
    if phase in ('queue_prepared', 'ledger_promoted'):
        with pytest.raises(ValueError, match='pending'):
            protocol.require_dispatch_authority(queue._conn, host='new-host')
    receipt = perform()
    assert perform() == receipt
    assert ledger.conn.execute('SELECT COUNT(*) FROM authority_generation').fetchone()[0] == 2
    protocol.require_dispatch_authority(queue._conn, host='new-host')


def test_concurrent_real_process_transfer_loses_without_rewriting_intent(transfer, tmp_path):
    import subprocess
    import time
    repo, ledger, queue, checkpoint, perform = transfer
    marker, release = tmp_path / 'prepared', tmp_path / 'release'
    first = subprocess.Popen(_child_command(repo, ledger, checkpoint, 'queue_prepared', marker, release),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert first.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        intent = queue.authority_state()
        second = subprocess.run(_child_command(repo, ledger, checkpoint, 'unused'), capture_output=True, timeout=10)
        assert second.returncode != 0
        assert b'another Catch-up owns this ledger' in second.stderr
        assert queue.authority_state() == intent
        assert ledger.authority_state()['generation'] == 1
    finally:
        release.touch()
        stdout, stderr = first.communicate(timeout=20)
    assert first.returncode == 0, (stdout, stderr)
    assert perform()['transfer_id'] == intent['transfer']['transfer_id']


def _row(queue, identity, status):
    queue._conn.execute('INSERT INTO actions(id,created_at,updated_at,action_type,domain,title,parameters,status) '
                        'VALUES(?,?,?,?,?,?,?,?)', (identity,NOW,NOW,'reminder_create','kids_school',identity,'{}',status))
    queue._conn.commit()


def test_quiescence_refuses_running_actions_and_revocation_preserves_uncertainty(transfer):
    _, ledger, queue, _, perform = transfer
    for status in ('approved', 'executing', 'unknown', 'succeeded', 'failed', 'cancelled'):
        _row(queue, status, status)
    with pytest.raises(ValueError, match='contradict quiescence'):
        perform()
    assert ledger.authority_state()['generation'] == 1
    receipt = perform(credentials_revoked=True)
    assert receipt['proof_kind'] == 'unverified_owner_attestation_credential_revocation'
    assert receipt['former_host_revoked_at'] == NOW
    assert queue.get_raw('approved')['status'] == 'pending'
    assert queue.get_raw('executing')['status'] == 'unknown'
    for status in ('unknown', 'succeeded', 'failed', 'cancelled'):
        assert queue.get_raw(status)['status'] == status
    assert queue._conn.execute('SELECT COUNT(*) FROM action_audit').fetchone()[0] == 2


def test_uncoordinated_queue_promotion_is_disabled(transfer):
    _, ledger, queue, _, _ = transfer
    with pytest.raises(ValueError, match='uncoordinated'):
        queue.transfer_authority('new-host', 2)
    assert ledger.authority_state()['generation'] == queue.authority_state()['generation'] == 1


def test_metadata_fails_closed_when_missing_malformed_or_divergent(transfer):
    _, _, queue, _, perform = transfer
    from contextlib import closing
    with closing(sqlite3.connect(':memory:')) as missing, pytest.raises(ValueError, match='metadata'):
        protocol.require_dispatch_authority(missing)
    perform()
    original = queue.authority_state()['transfer']
    for changed in ({}, {**original,'host':'tampered'}, {**original,'checkpoint_sha256':123}):
        queue._conn.execute('UPDATE queue_authority SET transfer_json=?', (json.dumps(changed),))
        with pytest.raises(ValueError, match='invalid'):
            protocol.require_dispatch_authority(queue._conn, host='new-host')
    queue._conn.execute('UPDATE queue_authority SET transfer_json=?', (json.dumps(original),))
    queue._conn.execute("UPDATE queue_authority SET promoted_at='different'")
    with pytest.raises(ValueError, match='epoch'):
        protocol.require_dispatch_authority(queue._conn, host='new-host')


@pytest.mark.parametrize(('field', 'value', 'reason'), [
    ('host', '', 'identity'),
    ('former_host', ' old-host', 'identity'),
    ('ledger_path', None, 'identity'),
    ('promoted_at', 123, 'identity'),
    ('checkpoint_generation', ' ', 'identity'),
    ('actor', 'owner ', 'identity'),
    ('generation', True, 'epoch'),
    ('former_generation', True, 'epoch'),
    ('former_generation', 0, 'epoch'),
    ('generation', 4, 'epoch'),
    ('host', 'old-host', 'epoch'),
    ('ledger_path', 'relative/ledger.db', 'epoch'),
    ('proof_kind', 'native_credential_revocation', 'attestation'),
    ('former_host_revoked_at', NOW, 'revocation attestation'),
    ('proof_kind', 'unverified_owner_attestation_credential_revocation', 'revocation attestation'),
    ('promoted_at', 'not-a-time', 'timestamp or checkpoint digest'),
    ('promoted_at', '2026-10-24T12:00:00', 'timestamp or checkpoint digest'),
    ('checkpoint_sha256', None, 'timestamp or checkpoint digest'),
    ('checkpoint_sha256', 123, 'timestamp or checkpoint digest'),
    ('checkpoint_sha256', 'a' * 63, 'timestamp or checkpoint digest'),
    ('checkpoint_sha256', 'z' * 64, 'timestamp or checkpoint digest'),
    ('checkpoint_sha256', 'aa ' * 21 + ' ', 'timestamp or checkpoint digest'),
])
def test_malformed_receipt_fields_refuse_even_with_matching_binding(transfer, field, value, reason):
    _, _, _, _, perform = transfer
    receipt = {**perform(), field: value}
    receipt['transfer_id'] = protocol._digest(receipt)
    with pytest.raises(ValueError, match=reason):
        protocol.validate_receipt(receipt)


@pytest.mark.parametrize('shape', ['null', 'array', 'missing-field', 'extra-field', 'schema', 'binding'])
def test_malformed_receipt_shape_or_binding_cannot_authorize_dispatch(transfer, shape):
    _, _, queue, _, perform = transfer
    receipt = perform()
    if shape == 'null':
        receipt = None
    elif shape == 'array':
        receipt = []
    elif shape == 'missing-field':
        receipt.pop('actor')
    elif shape == 'extra-field':
        receipt['native_proof'] = True
    elif shape == 'schema':
        receipt['schema'] = 'artha.authority-transfer.v999'
    else:
        receipt['transfer_id'] = '0' * 64
    queue._conn.execute('UPDATE queue_authority SET transfer_json=?', (json.dumps(receipt),))
    with pytest.raises(ValueError, match='invalid authority transfer'):
        protocol.require_dispatch_authority(queue._conn, host='new-host')


@pytest.mark.parametrize(('options', 'reason'), [
    ({'new_host': None}, 'host is required'),
    ({'new_host': 42}, 'host is required'),
    ({'new_host': '  '}, 'host is required'),
    ({'expected_generation': 0}, 'positive integer'),
    ({'expected_generation': -1}, 'positive integer'),
    ({'expected_generation': '1'}, 'positive integer'),
    ({'former_host_quiesced': False}, 'signed retirement receipt'),
    ({'credentials_revoked': 'true'}, 'must be booleans'),
    ({'credentials_revoked': 1}, 'must be booleans'),
])
def test_refused_transfer_inputs_leave_authority_and_pause_unchanged(transfer, options, reason):
    repo, ledger, queue, checkpoint, _ = transfer
    before = ledger.authority_state(), queue.authority_state()
    arguments = dict(new_host='new-host', actor='owner', checkpoint_path=checkpoint, workspace=repo,
                     expected_generation=1, former_host_quiesced=True)
    arguments.update(options)
    with pytest.raises((PermissionDenied, ValueError), match=reason):
        ledger.transfer_authority(**arguments)
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert not (repo / 'config/actions.paused').exists()


def test_missing_cipher_refuses_before_reading_checkpoint_or_writing(transfer, monkeypatch):
    repo, ledger, queue, _, perform = transfer
    before = ledger.authority_state(), queue.authority_state()
    monkeypatch.setattr(ledger, '_cipher', None)
    with pytest.raises(PermissionDenied, match='encryption key'):
        perform()
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert not (repo / 'config/actions.paused').exists()


@pytest.mark.parametrize('failure', ['missing', 'directory', 'unreadable', 'verification'])
def test_checkpoint_failure_cannot_prepare_queue_or_promote_ledger(transfer, monkeypatch, failure):
    repo, ledger, queue, checkpoint, perform = transfer
    before = ledger.authority_state(), queue.authority_state()
    if failure == 'missing':
        checkpoint.unlink()
    elif failure == 'directory':
        checkpoint.unlink()
        checkpoint.mkdir()
    elif failure == 'unreadable':
        original_open = Path.open
        def denied(path, *args, **kwargs):
            if path == checkpoint:
                raise PermissionError('synthetic checkpoint permission denied')
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', denied)
    else:
        def invalid_checkpoint(*_):
            raise ValueError('synthetic invalid preservation checkpoint')
        monkeypatch.setattr(protocol, '_verify_checkpoint', invalid_checkpoint)
    error = ValueError if failure == 'verification' else PermissionDenied
    reason = 'invalid preservation' if failure == 'verification' else 'checkpoint cannot be read'
    with pytest.raises(error, match=reason):
        perform()
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert not (repo / 'config/actions.paused').exists()


@pytest.mark.parametrize('mismatch', ['stale', 'unknown-former', 'same-host', 'queue-generation', 'queue-host'])
def test_initial_epoch_mismatch_refuses_without_writes(transfer, mismatch):
    repo, ledger, queue, checkpoint, _ = transfer
    if mismatch == 'unknown-former':
        ledger.conn.execute("UPDATE authority_generation SET host='' WHERE generation=1")
    elif mismatch == 'same-host':
        ledger.conn.execute("UPDATE authority_generation SET host='new-host' WHERE generation=1")
    elif mismatch == 'queue-generation':
        queue._conn.execute('UPDATE queue_authority SET generation=2')
    elif mismatch == 'queue-host':
        queue._conn.execute("UPDATE queue_authority SET host='unexpected-host'")
    queue._conn.commit()
    before = ledger.authority_state(), queue.authority_state()
    reason = 'stale' if mismatch == 'stale' else 'former host' if mismatch in (
        'unknown-former', 'same-host') else 'epochs differ'
    with pytest.raises(PermissionDenied, match=reason):
        ledger.transfer_authority('new-host', actor='owner', checkpoint_path=checkpoint, workspace=repo,
            expected_generation=2 if mismatch == 'stale' else 1, former_host_quiesced=True)
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert not (repo / 'config/actions.paused').exists()


@pytest.mark.parametrize('mismatch', ['completed-epoch', 'stale-request'])
def test_completed_transfer_refuses_inconsistent_or_stale_retry(transfer, mismatch):
    repo, ledger, queue, checkpoint, perform = transfer
    perform()
    if mismatch == 'completed-epoch':
        ledger.conn.execute("UPDATE authority_generation SET host='unexpected-host' WHERE generation=2")
    before = ledger.authority_state(), queue.authority_state()
    with pytest.raises(PermissionDenied, match='inconsistent' if mismatch == 'completed-epoch' else 'stale'):
        ledger.transfer_authority('new-host' if mismatch == 'completed-epoch' else 'third-host',
            actor='owner', checkpoint_path=checkpoint, workspace=repo,
            expected_generation=1, former_host_quiesced=True)
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert (repo / 'config/actions.paused').exists()


def test_subsequent_transfer_requires_and_advances_the_current_epoch(transfer):
    repo, ledger, queue, checkpoint, perform = transfer
    first = perform()
    second = ledger.transfer_authority('third-host', actor='owner', checkpoint_path=checkpoint,
        workspace=repo, expected_generation=2, former_host_quiesced=True)
    assert second['former_generation'] == first['generation'] == 2
    assert second['former_host'] == first['host'] == 'new-host'
    assert second['transfer_id'] != first['transfer_id']
    assert ledger.authority_state()['generation'] == queue.authority_state()['generation'] == 3
    assert not queue.authority_state()['transfer_pending']
    protocol.require_dispatch_authority(queue._conn, host='third-host')
    assert (repo / 'config/actions.paused').exists()


def test_epoch_change_after_queue_prepare_refuses_promotion_and_retains_pending_intent(transfer, monkeypatch):
    repo, ledger, queue, _, perform = transfer
    def change_epoch(name):
        if name == 'queue_prepared':
            ledger.conn.execute("UPDATE authority_generation SET host='unexpected-host' WHERE generation=1")
    monkeypatch.setattr(protocol, '_phase', change_epoch)
    with pytest.raises(PermissionDenied, match='epoch changed'):
        perform()
    assert ledger.authority_state()['generation'] == 1
    assert ledger.authority_state()['host'] == 'unexpected-host'
    assert queue.authority_state()['transfer_pending']
    assert queue.authority_state()['transfer']['former_host'] == 'old-host'
    assert ledger.conn.execute('SELECT COUNT(*) FROM authority_generation').fetchone()[0] == 1
    with pytest.raises(ValueError, match='pending'):
        protocol.require_dispatch_authority(queue._conn, host='new-host')
    assert (repo / 'config/actions.paused').exists()


@pytest.mark.parametrize('failure', ['dispatch', 'finalize'])
def test_resume_failure_keeps_pause_and_allows_retry_after_repair(transfer, monkeypatch, failure):
    repo, ledger, queue, _, perform = transfer
    if failure == 'finalize':
        def interrupted(name):
            if name == 'ledger_promoted':
                raise RuntimeError('synthetic interruption')
        monkeypatch.setattr(protocol, '_phase', interrupted)
        with pytest.raises(RuntimeError, match='interruption'):
            perform()
        monkeypatch.setattr(protocol, '_phase', lambda _: None)
    else:
        perform()
    before = ledger.authority_state(), queue.authority_state()
    with monkeypatch.context() as fault:
        if failure == 'finalize':
            fault.setenv('ARTHA_HOST', 'new-host')
            def finalize_failure(*_):
                raise ValueError('synthetic finalize failure')
            fault.setattr(ActionQueue, 'finalize_authority_transfer', finalize_failure)
        with pytest.raises(ValueError if failure == 'finalize' else PermissionDenied,
                           match='finalize failure' if failure == 'finalize' else 'host'):
            protocol.resume_effects(ledger, repo, actor='owner')
    assert (ledger.authority_state(), queue.authority_state()) == before
    assert (repo / 'config/actions.paused').exists()
    monkeypatch.setenv('ARTHA_HOST', 'new-host')
    protocol.resume_effects(ledger, repo, actor='owner')
    assert not queue.authority_state()['transfer_pending']
    assert not (repo / 'config/actions.paused').exists()
    protocol.require_dispatch_authority(queue._conn, host='new-host')


def test_resume_refuses_a_queue_left_behind_the_ledger(transfer,monkeypatch):
    repo,ledger,_,_,_=transfer
    (repo/'config/actions.paused').write_text('Owner pause\n')
    ledger.conn.execute('INSERT INTO authority_generation VALUES(?,?,?,?)',(2,'new-host',NOW,None))
    monkeypatch.setenv('ARTHA_HOST','new-host')
    with pytest.raises(PermissionDenied,match='epochs'):
        protocol.resume_effects(ledger,repo,actor='owner')
    assert (repo/'config/actions.paused').exists()
