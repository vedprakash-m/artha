"""Configured cutover and the runtime opt-out fence the kernel, not only its CLI."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from catchup_core import cli, commands, policy
from catchup_core.backup import encrypted_snapshot
from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
from catchup_core.ledger import Ledger, PermissionDenied
from core_helpers import (
    CORE_KEY as KEY,
    build_cutover_configured,
    build_preservation_checkpoint,
    release_scorecard,
)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    return build_cutover_configured(tmp_path, monkeypatch)


@pytest.mark.parametrize('flag', [False, None, 'false', 'true', 0, 1])
def test_kernel_rejects_disabled_or_coerced_cutover_even_with_passing_release_evidence(configured, flag):
    config, request, _, _ = configured
    if flag is None:
        config.pop('cutover')
    else:
        config['cutover'] = flag
    with pytest.raises(PermissionDenied, match='cutover'):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'])


def test_direct_runtime_cannot_bypass_configured_false_with_environment_opt_in(configured):
    config, _, keyring, build = configured
    config['cutover'] = False
    before = dict(keyring.store)

    def _attempt():
        deps = build(environ={'ARTHA_CUTOVER': '1'})
        deps.ledger.close()

    with pytest.raises(PermissionDenied, match='cutover'):
        _attempt()
    assert keyring.store == before


def test_runtime_environment_opt_out_blocks_configured_authority(configured):
    _, _, _, build = configured

    def _attempt():
        deps = build(environ={'ARTHA_CUTOVER': '0'})
        deps.ledger.close()

    with pytest.raises(PermissionDenied, match='cutover'):
        _attempt()


def test_read_only_inspection_and_local_safety_authority_survive_disabled_cutover(configured):
    config, request, _, build = configured
    deps = build()
    deps.ledger.close()
    config['cutover'] = False
    inspection = build(inspection=True, environ={'ARTHA_CUTOVER': '0'})
    try:
        assert inspection.ledger.conn.execute('PRAGMA query_only').fetchone()[0] == 1
    finally:
        inspection.ledger.close()
    policy.validate_release(replace(request, mode=ExecutionMode.SHADOW), config,
                            viewer='owner', domains=['kids_school'])
    policy.validate_local_authority(request, config, viewer='owner')


def test_quality_evidence_is_separate_from_permission_to_enable_cutover(configured):
    config, request, _, _ = configured
    config['cutover'] = False
    policy._validate_release_evidence(request, config, domains=['kids_school'])
    with pytest.raises(PermissionDenied, match='cutover'):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'])


def test_selective_rollback_snapshot_cannot_satisfy_full_state_preservation(configured, tmp_path):
    config, request, keyring, _ = configured
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    selective_db = tmp_path / 'selective.db'
    with Ledger(selective_db, id_key=KEY, cipher=cipher) as ledger:
        ledger.migrate()
    selective = tmp_path / 'selective.ae1'
    encrypted_snapshot(selective, selective_db, cipher)
    digest = hashlib.sha256(selective.read_bytes()).hexdigest()
    config['release']['rollback_snapshot'] = {'path': str(selective), 'sha256': digest}
    with pytest.raises(PermissionDenied, match='selective rollback snapshot'):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'])
    with pytest.raises(PermissionDenied, match='selective rollback snapshot'):
        policy.verify_rollback(config, cipher)


@pytest.mark.parametrize(
    'defect',
    [
        'missing_state_entries',
        'missing_escrow_keys',
        'same_path_independent_copy',
        'mismatched_independent_copy',
        'unreconciled_rehearsal',
        'unsigned_rehearsal',
        'mismatched_rehearsal_digest',
    ],
)
def test_incomplete_preservation_or_recovery_evidence_blocks_activation(configured, tmp_path, defect):
    config, request, keyring, _ = configured
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    if defect in ('missing_state_entries', 'missing_escrow_keys'):
        bad_ck = build_preservation_checkpoint(
            tmp_path,
            cipher,
            include_state=(defect != 'missing_state_entries'),
            escrow={'age-key/artha': b'only-one'} if defect == 'missing_escrow_keys' else None,
            name_prefix=defect,
        )
        digest = hashlib.sha256(bad_ck.read_bytes()).hexdigest()
        copy_path = tmp_path / f'{defect}_copy.ack'
        copy_path.write_bytes(bad_ck.read_bytes())
        config['release']['rollback_snapshot'] = {'path': str(bad_ck), 'sha256': digest}
        config['release']['independent_copy'] = {'path': str(copy_path), 'sha256': digest}
        rehearsal_path = Path(config['release']['recovery_rehearsal']['path'])
        rehearsal_path.write_text(
            json.dumps({'passed': True, 'owner_signed_at': 'reviewed', 'reconciled': True, 'checkpoint_sha256': digest})
        )
        config['release']['recovery_rehearsal']['sha256'] = hashlib.sha256(rehearsal_path.read_bytes()).hexdigest()
        with pytest.raises(PermissionDenied, match='rollback snapshot'):
            policy.verify_rollback(config, cipher)
        return
    if defect == 'same_path_independent_copy':
        config['release']['independent_copy'] = dict(config['release']['rollback_snapshot'])
    elif defect == 'mismatched_independent_copy':
        other = tmp_path / 'other_copy.ack'
        other.write_bytes(Path(config['release']['rollback_snapshot']['path']).read_bytes() + b'x')
        config['release']['independent_copy'] = {
            'path': str(other),
            'sha256': hashlib.sha256(other.read_bytes()).hexdigest(),
        }
    else:
        rehearsal_path = Path(config['release']['recovery_rehearsal']['path'])
        payload = json.loads(rehearsal_path.read_text())
        if defect == 'unreconciled_rehearsal':
            payload['reconciled'] = False
        elif defect == 'unsigned_rehearsal':
            payload['owner_signed_at'] = ''
        elif defect == 'mismatched_rehearsal_digest':
            payload['checkpoint_sha256'] = '0' * 64
        rehearsal_path.write_text(json.dumps(payload))
        config['release']['recovery_rehearsal']['sha256'] = hashlib.sha256(rehearsal_path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'])


@pytest.mark.parametrize('observation', ['mac_personal', 'windows_work', 'personal_cloud_copy'])
@pytest.mark.parametrize('field', ['profile', 'checkpoint_sha256', 'checkpoint_generation', 'observed_at',
                                  'owner_reviewed_at', 'outcome', 'privacy'])
def test_recovery_v2_requires_each_explicit_observation_field(configured, observation, field):
    config, _, _, _ = configured
    record = config['release']['recovery_rehearsal']
    path = Path(record['path'])
    receipt = json.loads(path.read_bytes())
    receipt['observations'][observation].pop(field)
    path.write_text(json.dumps(receipt))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='recovery'):
        policy._validate_preservation_evidence(config['release'])


@pytest.mark.parametrize('observation', ['mac_personal', 'windows_work', 'personal_cloud_copy'])
@pytest.mark.parametrize('field', ['current_history_verified', 'replay_verified', 'history_sha256'])
def test_recovery_v2_requires_current_privacy_observations(configured, observation, field):
    config, _, _, _ = configured
    record = config['release']['recovery_rehearsal']
    path = Path(record['path'])
    receipt = json.loads(path.read_bytes())
    receipt['observations'][observation]['privacy'].pop(field)
    path.write_text(json.dumps(receipt))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='recovery'):
        policy._validate_preservation_evidence(config['release'])


@pytest.mark.parametrize(('keys', 'value'), [
    (('schema',), 'artha.recovery-rehearsal.v1'),
    (('owner_signed_at',), 'reviewed'),
    (('owner_signed_at',), '2026-10-25'),
    (('checkpoint_generation',), None),
    (('checkpoint_generation',), 'stale-generation'),
    (('cohort',), {}),
    (('cohort',), {'implementation': 'stale'}),
    (('work_checkpoint', 'profile'), 'personal'),
    (('work_checkpoint', 'generation'), 'stale-work-generation'),
    (('work_checkpoint', 'sha256'), '0' * 64),
    (('observations', 'mac_personal', 'os'), 'linux'),
    (('observations', 'mac_personal', 'native'), 1),
    (('observations', 'windows_work', 'os'), 'macos'),
    (('observations', 'windows_work', 'profile'), 'personal'),
    (('observations', 'windows_work', 'native'), False),
    (('observations', 'windows_work', 'outcome'), 'inspection_only'),
    (('observations', 'windows_work', 'checkpoint_sha256'), '0' * 64),
    (('observations', 'mac_personal', 'observed_at'), 'reviewed'),
    (('observations', 'mac_personal', 'observed_at'), '2026-10-26T00:00:00Z'),
    (('observations', 'mac_personal', 'owner_reviewed_at'), '2026-10-24T00:00:00Z'),
    (('observations', 'mac_personal', 'owner_reviewed_at'), '2026-10-26T00:00:00Z'),
    (('observations', 'mac_personal', 'privacy', 'current_history_verified'), False),
    (('observations', 'windows_work', 'privacy', 'replay_verified'), 1),
    (('observations', 'personal_cloud_copy', 'privacy', 'history_sha256'), 'unverified'),
    (('observations', 'personal_cloud_copy', 'privacy', 'history_sha256'), '0' * 64),
    (('observations', 'personal_cloud_copy', 'source'), 'local_sync_folder'),
    (('observations', 'personal_cloud_copy', 'profile'), 'work'),
    (('observations', 'personal_cloud_copy', 'recovered_sha256'), '0' * 64),
    (('observations', 'windows_work', 'cloud_copy'), True),
    (('former_host_retired',), True),
])
def test_recovery_v2_refuses_unbound_or_unsuccessful_acceptance(configured, keys, value):
    config, _, _, _ = configured
    record = config['release']['recovery_rehearsal']
    path = Path(record['path'])
    receipt = json.loads(path.read_bytes())
    target = receipt
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    path.write_text(json.dumps(receipt))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied):
        policy._validate_preservation_evidence(config['release'])


def test_recovery_v2_requires_no_fake_retirement_for_initial_same_host_cutover(configured):
    config, _, keyring, _ = configured
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    manifest = policy.verify_rollback(config, cipher)
    policy._validate_preservation_evidence(config['release'], verified_checkpoint=manifest)
    with pytest.raises(PermissionDenied, match='generation'):
        policy._validate_preservation_evidence(config['release'], expected_generation='other-generation')


def test_recovery_v2_refuses_duplicate_acceptance_fields(configured):
    config, _, _, _ = configured
    record = config['release']['recovery_rehearsal']
    path = Path(record['path'])
    path.write_text(path.read_text()[:-1] + ', "passed": true}')
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='recovery'):
        policy._validate_preservation_evidence(config['release'])


def test_recovery_v2_native_observation_cannot_predate_authenticated_checkpoint(configured):
    config, _, keyring, _ = configured
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    manifest = policy.verify_rollback(config, cipher)
    record = config['release']['recovery_rehearsal']
    path = Path(record['path'])
    receipt = json.loads(path.read_bytes())
    receipt['observations']['mac_personal']['observed_at'] = '2000-01-01T00:00:00Z'
    path.write_text(json.dumps(receipt))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='recovery'):
        policy._validate_preservation_evidence(config['release'], verified_checkpoint=manifest)


def test_authority_transfer_requires_quiescence_or_revocation_and_checkpoint_and_fences_old_host(
    configured, tmp_path, monkeypatch
):
    from argparse import Namespace
    from actions.base import ActionProposal
    from action_executor import ActionExecutor
    from action_queue import compute_payload_hash

    config, request, keyring, build = configured
    repo = tmp_path / 'repo'
    (repo / 'config' / 'actions.yaml').write_text(
        yaml.safe_dump(
            {
                'actions': {
                    'reminder_create': {
                        'enabled': True,
                        'handler': 'actions.reminder_create',
                        'friction': 'low',
                        'min_trust': 0,
                        'timeout_sec': 10,
                    }
                },
                'live_trial': {'held': [], 'handoff_only': []},
            }
        )
    )
    deps = build()
    try:
        assert deps.ledger.authority_state()['generation'] == 1
        assert deps.ledger.authority_state()['host'] == 'authority'
        ck_path = config['release']['rollback_snapshot']['path']

        # Missing quiescence/revocation is refused
        with pytest.raises(PermissionDenied, match='quiescence or credential revocation'):
            deps.ledger.transfer_authority('promoted', actor='owner', checkpoint_path=ck_path, workspace=repo, expected_generation=1)

        # Non-owner actor is refused
        with pytest.raises(PermissionDenied, match='owner'):
            deps.ledger.transfer_authority(
                'promoted', actor='intruder', checkpoint_path=ck_path, former_host_quiesced=True, workspace=repo, expected_generation=1
            )

        # Selective AE1 snapshot cannot authorize authority transfer
        selective = tmp_path / 'selective_transfer.ae1'
        encrypted_snapshot(selective, deps.ledger._path, deps.ledger._cipher)
        with pytest.raises(PermissionDenied, match='selective rollback snapshot'):
            deps.ledger.transfer_authority(
                'promoted', actor='owner', checkpoint_path=selective, former_host_quiesced=True, workspace=repo, expected_generation=1
            )

        # Enqueue and approve an action on the old host (generation 1) before transfer
        monkeypatch.setenv('ARTHA_HOST', 'authority')
        executor = ActionExecutor(repo)
        executor._queue. _conn.execute(
            "UPDATE queue_authority SET host='authority' WHERE singleton_key=1"
        )
        proposal = ActionProposal(
            id='act-transfer-1',
            action_type='reminder_create',
            domain='kids_school',
            title='Pack lunch',
            description='Pack school lunch',
            parameters={'title': 'Pack lunch', 'due_date': '2099-10-15'},
            friction='low',
            min_trust=0,
            sensitivity='standard',
            reversible=False,
            undo_window_sec=None,
            expires_at='2099-01-01T00:00:00+00:00',
            source_step='test',
            source_skill='test',
            linked_oi=None,
            preview_required=True,
        )
        executor.propose_direct(proposal)
        executor.queue.mark_preview_shown('act-transfer-1', 'owner', payload_hash=compute_payload_hash(proposal))
        executor.queue.transition('act-transfer-1', 'approved', actor='owner', approved_by='owner')
        assert executor.queue.get_raw('act-transfer-1')['status'] == 'approved'

        # Execute authority transfer via commands.execute
        transfer_args = Namespace(
            command='transfer-authority',
            new_host='promoted',
            checkpoint=ck_path,
            expected_generation=1,
            former_quiesced=True,
            credentials_revoked=True,
            shadow=False,
        )
        receipt = commands.execute(transfer_args, deps, repo)
        assert receipt['generation'] == 2
        assert receipt['host'] == 'promoted'
        assert receipt['former_host'] == 'authority'
        assert receipt['former_host_revoked_at']
    finally:
        deps.ledger.close()

    # 1. Pre-transfer approved action was demoted back to pending, and old host cannot approve or dispatch
    assert executor.queue.get_raw('act-transfer-1')['status'] == 'pending'
    executor.queue.mark_preview_shown('act-transfer-1', 'owner', payload_hash=compute_payload_hash(proposal))
    old_attempt = executor.approve('act-transfer-1', 'owner', reviewed_payload_hash=compute_payload_hash(proposal))
    assert old_attempt.status == 'failure'
    assert 'paused' in old_attempt.message
    assert (repo / 'config/actions.paused').exists()
    from catchup_core.authority_transfer import require_dispatch_authority
    with pytest.raises(ValueError, match='host'):
        require_dispatch_authority(executor.queue._conn, host='authority')

    # 2. Old host cannot open authoritative runtime deps even if its config still says 'authority'
    def _open_old():
        old_deps = build()
        old_deps.ledger.close()

    with pytest.raises(PermissionDenied, match='authority generation 2 belongs to'):
        _open_old()

    # 3. Promoted host can inspect generation 2 while effects remain paused pending owner resume.
    monkeypatch.setenv('ARTHA_HOST', 'promoted')
    monkeypatch.setattr(policy.socket, 'gethostname', lambda: 'promoted')
    config['release']['authority_host'] = 'promoted'
    config['release']['scorecard'] = {
        'path': config['release']['scorecard']['path'],
        'sha256': hashlib.sha256(
            Path(config['release']['scorecard']['path']).write_text(json.dumps(release_scorecard(config)))
            and Path(config['release']['scorecard']['path']).read_bytes()
            or Path(config['release']['scorecard']['path']).read_bytes()
        ).hexdigest(),
    }
    promoted_deps = build(req_override=CatchupRequest('cu_promoted', 'owner', 'cli', host='promoted'))
    try:
        assert promoted_deps.ledger.authority_state()['generation'] == 2
        assert promoted_deps.ledger.authority_state()['host'] == 'promoted'
        assert (repo / 'config/actions.paused').exists()
    finally:
        promoted_deps.ledger.close()
    assert cli. parse_args(['transfer-authority', 'next-host', '--checkpoint', ck_path, '--former-quiesced', '--expected-generation', '2']).new_host == 'next-host'
    executor.queue.close()
