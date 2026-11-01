"""Approved file bytes remain current through runtime disclosure/publication boundaries."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from catchup_core import coordinator, policy
from catchup_core.contracts import RunStatus, Stage
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
from catchup_core.ledger import PermissionDenied
import test_cutover_fence


@pytest.fixture
def configured(tmp_path, monkeypatch):
    return test_cutover_fence.configured.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    'field',
    ['scorecard', 'stage1_clearance', 'rollback_snapshot', 'independent_copy', 'recovery_rehearsal'],
)
@pytest.mark.parametrize('change', ['write', 'delete'])
def test_real_runtime_rechecks_approved_artifacts_after_construction(configured, field, change):
    config, _, _, build = configured
    deps = build()
    try:
        deps.validate_inputs()
        path = Path(config['release'][field]['path'])
        if change == 'write':
            path.write_bytes(path.read_bytes() + b' ')
        else:
            path.unlink()
        with pytest.raises(PermissionDenied):
            deps.validate_inputs()
    finally:
        deps.ledger.close()


@pytest.mark.parametrize('replacement', ['in-place', 'rename'])
def test_rollback_verification_decodes_the_one_approved_read(configured, monkeypatch, replacement):
    config, _, keyring, _ = configured
    record = config['release']['rollback_snapshot']
    path = Path(record['path'])
    valid = path.read_bytes()
    broken = b'AE1-approved-but-not-a-valid-encrypted-snapshot'
    path.write_bytes(broken)
    record['sha256'] = hashlib.sha256(broken).hexdigest()
    incoming = path.with_suffix('.replacement')
    incoming.write_bytes(valid)
    opened = Path.open
    mutations = []
    def replace_after_open(target, *args, **kwargs):
        handle = opened(target, *args, **kwargs)
        if target == path and args == ('rb',):
            operation = 'dst.write_bytes(src.read_bytes())' if replacement == 'in-place' else 'src.replace(dst)'
            script = (
                "import pathlib,sys\n"
                "dst,src=map(pathlib.Path,sys.argv[1:])\n"
                "try:\n"
                f" {operation}\n"
                "except PermissionError as exc:\n"
                f" if sys.platform != 'win32' or {replacement!r} != 'rename' or exc.winerror not in (5,32):\n"
                "  raise\n"
                " print('rename-denied')\n"
                "else:\n"
                " print('replaced')\n"
            )
            result = subprocess.run([sys.executable, '-c', script, str(path), str(incoming)],
                                    check=True, capture_output=True, text=True, timeout=15)
            mutations.append(result.stdout.strip())
        return handle
    monkeypatch.setattr(Path, 'open', replace_after_open)
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    with pytest.raises(PermissionDenied, match='rollback'):
        policy.verify_rollback(config, cipher)
    if sys.platform == 'win32' and replacement == 'rename':
        assert mutations == ['rename-denied']
        with opened(path, 'rb') as handle:
            assert handle.read() == broken
        incoming.replace(path)  # The same operation must succeed once verification closes its handle.
    else:
        assert mutations == ['replaced']
    with opened(path, 'rb') as handle:
        assert handle.read() == valid


def test_unchanged_preservation_baseline_and_copy_are_not_read_on_every_guard_call(configured, monkeypatch):
    config, _, keyring, _ = configured
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    guard = policy.ReleaseEvidenceGuard(config, cipher)
    guard.validate()
    paths = {Path(config['release'][field]['path']) for field in ('rollback_snapshot', 'independent_copy')}
    opened = Path.open
    def refuse_large_reads(path, *args, **kwargs):
        assert path not in paths, 'unchanged preservation baseline was reread'
        return opened(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', refuse_large_reads)
    guard.validate()


def test_initial_guard_authenticates_and_hashes_baseline_in_a_single_open(configured, monkeypatch):
    config, _, keyring, _ = configured
    path = Path(config['release']['rollback_snapshot']['path'])
    opened, reads = Path.open, []
    def counted(target, *args, **kwargs):
        if target == path:
            reads.append(True)
        return opened(target, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', counted)
    guard = policy.ReleaseEvidenceGuard(config, EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring)))
    guard.validate()
    assert len(reads) == 1


def test_withdrawing_reviewed_cloud_recovery_proof_invalidates_cached_preservation(configured):
    config, _, keyring, _ = configured
    guard = policy.ReleaseEvidenceGuard(config, EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring)))
    guard.validate()
    config['release']['independent_copy'].pop('owner_verified_at')
    with pytest.raises(PermissionDenied, match='reviewed cloud recovery proof'):
        guard.validate()


@pytest.mark.parametrize('change', ['write', 'delete'])
def test_work_recovery_artifact_withdrawal_invalidates_cached_preservation(configured, change):
    config, _, keyring, _ = configured
    guard = policy.ReleaseEvidenceGuard(config, EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring)))
    guard.validate()
    receipt = json.loads(Path(config['release']['recovery_rehearsal']['path']).read_bytes())
    path = Path(receipt['work_checkpoint']['path'])
    if change == 'write':
        path.write_bytes(path.read_bytes() + b'changed')
    else:
        path.unlink()
    with pytest.raises(PermissionDenied):
        guard.validate()


@pytest.mark.parametrize(('observation', 'field'), [
    ('mac_personal', 'native'), ('mac_personal', 'os'), ('windows_work', 'native'), ('windows_work', 'os'),
    ('mac_personal', 'privacy'), ('windows_work', 'privacy'), ('personal_cloud_copy', 'privacy'),
    ('personal_cloud_copy', 'source'), ('personal_cloud_copy', 'recovered_sha256'),
])
def test_withdrawing_native_or_privacy_observations_invalidates_cached_acceptance(configured, observation, field):
    config, _, keyring, _ = configured
    guard = policy.ReleaseEvidenceGuard(config, EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring)))
    guard.validate()
    record = config['release']['recovery_rehearsal']
    path = Path(record['path'])
    receipt = json.loads(path.read_bytes())
    receipt['observations'][observation].pop(field)
    path.write_text(json.dumps(receipt))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='recovery'):
        guard.validate()


def test_unchanged_rollback_is_verified_once_but_a_new_revision_is_rechecked(configured, monkeypatch):
    config, _, keyring, _ = configured
    calls = []
    verify = policy.verify_rollback
    def counted(*args):
        calls.append(True)
        return verify(*args)
    monkeypatch.setattr(policy, 'verify_rollback', counted)
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    guard = policy.ReleaseEvidenceGuard(config, cipher)
    guard.validate()
    guard.validate()
    assert len(calls) == 1
    Path(config['release']['rollback_snapshot']['path']).touch()
    guard.validate()
    guard.validate()
    assert len(calls) == 2


def test_rollback_replacement_during_verification_is_not_cached_as_verified(configured, monkeypatch):
    config, _, keyring, _ = configured
    verify = policy.verify_rollback
    path = Path(config['release']['rollback_snapshot']['path'])
    def changed(*args):
        manifest = verify(*args)
        path.write_bytes(path.read_bytes() + b' ')
        return manifest
    monkeypatch.setattr(policy, 'verify_rollback', changed)
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    with pytest.raises(PermissionDenied, match='changed'):
        policy.ReleaseEvidenceGuard(config, cipher).validate()


def test_cached_snapshot_is_bound_to_the_approved_digest_as_well_as_its_file_revision(configured):
    config, _, keyring, _ = configured
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    guard = policy.ReleaseEvidenceGuard(config, cipher)
    guard.validate()
    config['release']['rollback_snapshot']['sha256'] = '0' * 64
    with pytest.raises(PermissionDenied):
        guard.validate()


@pytest.mark.parametrize('component', ['implementation', 'prompts', 'packs', 'configuration', 'model'])
def test_approved_scorecard_from_a_different_runtime_cohort_cannot_enable_cutover(configured, component):
    import json
    config, _, _, build = configured
    record = config['release']['scorecard']
    path = Path(record['path'])
    result = json.loads(path.read_bytes())
    result['cohort'] = {**result.get('cohort', {}), component: 'old-reviewed-generation'}
    path.write_text(json.dumps(result))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='cohort'):
        deps = build()
        deps.ledger.close()


def test_a_mixed_case_cohort_is_rejected_even_when_aggregate_bounds_pass(configured):
    import json
    config, _, _, build = configured
    record = config['release']['scorecard']
    path = Path(record['path'])
    result = json.loads(path.read_bytes())
    result['evaluations'][0]['cohort'] = {'implementation': 'old'}
    path.write_text(json.dumps(result))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='cohort'):
        deps = build()
        deps.ledger.close()


@pytest.mark.parametrize('change', ['measurement', 'missing-catchup', 'anonymous-model'])
def test_release_cohort_requires_versioned_measurements_and_observed_model(configured, change):
    import json
    from test_integrated_surfaces import release_scorecard
    config, _, _, build = configured
    if change == 'anonymous-model':
        config.pop('model_identity')
    result = release_scorecard(config)
    if change == 'measurement':
        result['measurements']['kids_school']['cohort'] = {'model': 'old'}
    elif change == 'missing-catchup':
        result['evaluations'][0].pop('catchup_id')
    record = config['release']['scorecard']
    path = Path(record['path'])
    path.write_text(json.dumps(result))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionDenied, match='cohort'):
        deps = build()
        deps.ledger.close()


def test_approving_evidence_or_enabling_cutover_does_not_change_the_evaluated_cohort(configured):
    config, _, _, _ = configured
    before = policy.evaluation_cohort(config, ['kids_school'])
    config['cutover'] = False
    config['release'] = {'different': 'approval artifact bytes'}
    assert policy.evaluation_cohort(config, ['kids_school']) == before
    config['model_identity'] = {**config['model_identity'], 'revision': 'changed'}
    assert policy.evaluation_cohort(config, ['kids_school']) != before


@pytest.mark.parametrize('problem', ['boolean-floor', 'zero-budget', 'duplicate-identity'])
def test_release_scorecard_rejects_ambiguous_case_identity_and_invalid_floors(problem):
    from test_integrated_surfaces import release_scorecard
    result = release_scorecard()
    if problem == 'boolean-floor':
        result['bounds']['kids_school']['minimum_precision'] = True
    elif problem == 'zero-budget':
        result['bounds']['kids_school']['maximum_monthly_usd'] = 0
    else:
        result['evaluations'][1]['case_id'] = result['evaluations'][0]['case_id']
    with pytest.raises(PermissionDenied):
        policy._validate_domains(result, ['kids_school'])


def test_actual_publication_stops_when_release_evidence_is_withdrawn_at_commit(configured):
    config, request, _, build = configured
    deps = build()
    published = []
    deps.preflight = lambda: None  # Synthetic test; never inventory the developer's scheduler.
    deps.finalizer = lambda _: None
    deps.fetchers = {}
    deps.publisher = lambda *args: published.append(args)
    path = Path(config['release']['scorecard']['path'])
    deps.fault = lambda stage: path.unlink() if stage is Stage.COMMIT else None
    try:
        result = coordinator.run_catchup(request, deps)
        assert result.status is RunStatus.BLOCKED and not result.briefing_id and not published
    finally:
        deps.ledger.close()


@pytest.mark.parametrize('safety', [False, True])
def test_release_artifact_revocation_does_not_block_inspection_or_owner_safety(configured, safety):
    config, _, _, build = configured
    deps = build()
    deps.ledger.close()
    Path(config['release']['scorecard']['path']).unlink()
    inspection = build(inspection=True, mutation=safety, safety_control=safety)
    try:
        inspection.validate_inputs()
        assert inspection.ledger.conn.execute('PRAGMA query_only').fetchone()[0] == (0 if safety else 1)
    finally:
        inspection.ledger.close()
