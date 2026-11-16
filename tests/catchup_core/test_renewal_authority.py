"""Native authority and interruption tests use only synthetic keys and metadata."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from catchup_core.renewal_authority import Authority
from catchup_core.renewal_inputs import SourceFile, SourceManifest
from catchup_core.renewal_store import RenewalError, digest
from lib.credentials import CredentialStore
from test_runtime import FakeKeyring


class Keyring(FakeKeyring):
    def delete_password(self, service, account):
        self.store.pop((service, account), None)


NOW = datetime(2026, 1, 2, tzinfo=UTC)
COHORT = {name: 'a' * 64 for name in ('implementation', 'configuration', 'prompts', 'packs', 'model', 'contract')}
EVALUATOR = {'scripts/policy.py': b'synthetic evaluator', 'scripts/presentation.py': b'presentation a'}
BINDING = {'owner': 'owner', 'host': 'synthetic', 'repository': 'example/project', 'profile': 'personal'}


def manifest(value='a'):
    return {'schema': 'artha.renewal-source.v1', 'files': [
        asdict(SourceFile('scripts/presentation.py', hashlib.sha256(('presentation ' + value).encode()).hexdigest(), '100644')),
        asdict(SourceFile('scripts/policy.py', hashlib.sha256(b'synthetic evaluator').hexdigest(), '100644')),
        asdict(SourceFile('.github/workflows/ci.yml', 'a' * 64, '100644'))]}


@pytest.fixture
def world(tmp_path):
    local = tmp_path / 'local'
    credentials = CredentialStore(backend=Keyring(), local=local)
    authority = Authority(local, BINDING, credentials=credentials, now=lambda: NOW, checkout=tmp_path / 'repo')
    delegation = {'schema': 'artha.renewal-delegation.v1', 'binding': BINDING,
                  'base_source': manifest(), 'base_cohort': COHORT,
                  'base_release': {'profile': 'slice', 'domains': ['kids_school'], 'owner_approved_at': 'original'},
                  'protected': ['scripts/policy.py', '.github/workflows/ci.yml'],
                  'dependencies': {'quality': ['scripts/policy.py'], 'recovery': ['scripts/policy.py']},
                  'context': 'd' * 64, 'expires_at': None,
                  'gates': [{'workflow': '.github/workflows/ci.yml', 'definition_sha256': 'a' * 64,
                             'jobs': ['test']}], 'domains': ['kids_school'],
                  'routine': ['scripts/presentation.py'],
                  'evaluator': {path: hashlib.sha256(raw).hexdigest() for path, raw in EVALUATOR.items()}}
    authority.enroll(delegation, expected_digest=digest(delegation), reviewed=True, validate_base=lambda _: None,
                     evaluator=EVALUATOR)
    return authority, delegation, credentials


def target():
    return {'source': manifest('b'), 'cohort': {**COHORT, 'implementation': 'b' * 64},
            'tree': 'c' * 40, 'context': 'd' * 64, 'authority': '0' * 64}


def approve(authority, value=None):
    value = value or target()
    return authority.approve(value, expected_digest=digest(value), reviewed=True)


def test_AR01_exact_approval_publication_and_readback(world):
    authority, delegation, _ = world
    approval = approve(authority)
    evidence = {'commit': 'e' * 40, 'tree': 'c' * 40, 'ci': {'status': 'passed'}}
    identity = authority.activate(approval, evidence, verify=lambda *_: None)
    decision = authority.validate(target(), validate_base=lambda _: None)
    assert decision['approval'] == approval
    assert authority.activate(approval, evidence, verify=lambda *_: None) == identity
    assert delegation['base_release']['owner_approved_at'] == 'original'


def test_AR05_revocation_invalidates_existing_approvals(world):
    authority, _, _ = world
    approval = approve(authority)
    authority.revoke()
    with pytest.raises(RenewalError):
        authority.activate(approval, {'commit': 'e' * 40, 'tree': 'c' * 40}, verify=lambda *_: None)


@pytest.mark.parametrize('stage', ['intent', 'publication'])
def test_AR09_interruption_is_recoverable_or_held(world, stage):
    authority, _, _ = world
    approval = approve(authority)
    def crash(value):
        if value == stage:
            raise RuntimeError('synthetic crash')
    authority.observe = crash
    with pytest.raises(RuntimeError, match='synthetic crash'):
        authority.activate(approval, {'commit': 'e' * 40, 'tree': 'c' * 40}, verify=lambda *_: None)
    authority.observe = lambda _: None
    if stage == 'intent':
        with pytest.raises(RenewalError):
            authority.validate(target(), validate_base=lambda _: None)
    authority.recover(verify=lambda *_: None)
    assert authority.validate(target(), validate_base=lambda _: None)['approval'] == approval


@pytest.mark.parametrize('field', ['configuration', 'model', 'packs', 'prompts', 'contract'])
def test_AR15_nonimplementation_cohort_changes_are_not_carry_forward(world, field):
    authority, _, _ = world
    value = target()
    value['cohort'][field] = 'f' * 64
    with pytest.raises(RenewalError):
        approve(authority, value)


def test_AR12_protected_or_historical_dependency_changes_refuse(world):
    authority, _, _ = world
    value = target()
    value['source']['files'][1]['sha256'] = 'f' * 64
    with pytest.raises(RenewalError):
        approve(authority, value)


def test_AR06_task_approval_cannot_be_used_for_different_source(world):
    authority, _, _ = world
    approval = approve(authority)
    authority.activate(approval, {'commit': 'e' * 40, 'tree': 'c' * 40}, verify=lambda *_: None)
    changed = target()
    changed['source'] = manifest('f')
    with pytest.raises(RenewalError):
        authority.validate(changed, validate_base=lambda _: None)


def test_AR11_missing_native_head_does_not_reenroll_from_files(world):
    authority, _, credentials = world
    credentials.delete('release.renewal')
    with pytest.raises(RenewalError):
        authority.policy()


@pytest.mark.parametrize('value', ['bad', '2026-01-01', 3])
def test_AR13_invalid_times_refuse(value):
    from catchup_core.renewal_authority import _time
    with pytest.raises(RenewalError):
        _time(value)


def test_AR16_task_only_delegation_holds_routine_and_accepts_exact_task(tmp_path, world):
    _, delegation, _ = world
    local = tmp_path / 'task-only'
    authority = Authority(local, BINDING, credentials=CredentialStore(backend=Keyring(), local=local),
                          now=lambda: NOW, checkout=tmp_path / 'repo')
    policy = {**delegation, 'routine': []}
    authority.enroll(policy, expected_digest=digest(policy), reviewed=True, validate_base=lambda _: None,
                     evaluator=EVALUATOR)
    with pytest.raises(RenewalError):
        authority.routine(target())
    approval = approve(authority)
    authority.activate(approval, {'commit': 'e' * 40, 'tree': 'c' * 40}, verify=lambda *_: None)
    assert authority.validate(target(), validate_base=lambda _: None)['approval'] == approval


@pytest.mark.parametrize('field,value', [('routine', ['unknown.py']),
    ('routine', ['scripts/policy.py']), ('protected', ['unknown.py']), ('protected', [3]),
    ('dependencies', {'x': []}), ('dependencies', {'x': [3]}), ('gates', []), ('gates', [{}]),
    ('expires_at', 'invalid'), ('evaluator', {'scripts/policy.py': 'f' * 64})])
def test_AR13_invalid_enrollment_inventory_refuses_without_native_change(world, field, value):
    from copy import deepcopy
    authority, policy, credentials = world
    candidate = deepcopy(policy)
    candidate[field] = value
    before = dict(credentials._backend.store)
    with pytest.raises((RenewalError, ValueError)):
        authority._check_policy(candidate)
    assert credentials._backend.store == before


def test_AR05_expiry_and_task_review_binding(world):
    from datetime import timedelta
    authority, policy, _ = world
    with pytest.raises(RenewalError):
        authority.approve(target(), expected_digest='f' * 64, reviewed=True)
    with pytest.raises(RenewalError):
        authority.enroll(policy, expected_digest='f' * 64, reviewed=True, validate_base=lambda _: None, evaluator={})
    approval = approve(authority)
    authority.now = lambda: NOW + timedelta(days=2)
    with pytest.raises(RenewalError):
        authority.task(approval)


def test_AR16_routine_reuses_exact_pending_and_completed_task(world):
    authority, _, _ = world
    identity = authority.routine(target())
    authority.record({'attempt': identity, 'stage': 'PUSHED'})
    assert authority.routine(target()) == identity
    authority.activate(identity, {'tree': 'c' * 40}, verify=lambda *_: None)
    authority.record({'attempt': identity, 'stage': 'VERIFIED'})
    assert authority.routine(target()) == identity
    assert authority.evaluator(authority.local / 'baseline').is_dir()


def test_AR25_prepared_generation_refuses_new_approval_and_active_read(world):
    authority, _, credentials = world
    state, revision = authority._state()
    state.update(phase='prepared', pending='f' * 64)
    credentials.write(authority.name, state, expected_revision=revision)
    with pytest.raises(RenewalError):
        approve(authority)
    with pytest.raises(RenewalError):
        authority.active()
    with pytest.raises(RenewalError):
        authority.activate('a' * 64, {}, verify=lambda *_: None)


@pytest.mark.parametrize('changes', [{'phase': []}, {'phase': 'ready', 'custody': ''}, {'secret': 'bad'},
    {'epoch': 0}, {'serial': 33}, {'phase': 'ready', 'pending': 'f' * 64}])
def test_AR13_corrupt_native_generation_or_key_refuses(world, changes):
    authority, _, credentials = world
    state, revision = authority._state()
    state.update(changes)
    credentials.write(authority.name, state, expected_revision=revision)
    with pytest.raises(RenewalError):
        authority.policy()


def test_AR10_other_declared_host_cannot_use_primary_native_head(world):
    authority, _, credentials = world
    other = Authority(authority.local, {**BINDING, 'host': 'secondary'}, credentials=credentials, now=lambda: NOW,
                      checkout=Path(authority.checkout))
    with pytest.raises(RenewalError):
        other.policy()
    with pytest.raises(RenewalError):
        Authority(authority.local, {}, credentials=credentials, checkout=Path(authority.checkout))


def test_AR20_existing_native_authority_never_implicitly_reenrolls(world):
    authority, policy, _ = world
    with pytest.raises(RenewalError):
        authority.enroll(policy, expected_digest=digest(policy), reviewed=True, validate_base=lambda _: None,
                         evaluator=EVALUATOR)


def test_AR09_interrupted_enrollment_retries_only_same_reviewed_policy(tmp_path, world):
    _, policy, _ = world
    local = tmp_path / 'enrolling'
    credentials = CredentialStore(local=local, backend=Keyring())
    def interrupted(event):
        if event == 'enroll-intent':
            raise RuntimeError('synthetic crash')
    authority = Authority(local, BINDING, credentials=credentials, now=lambda: NOW, observe=interrupted, checkout=tmp_path / 'repo')
    with pytest.raises(RuntimeError):
        authority.enroll(policy, expected_digest=digest(policy), reviewed=True, validate_base=lambda _: None, evaluator={})
    with pytest.raises(RenewalError):
        authority.policy()
    authority.observe = lambda _: None
    authority.enroll(policy, expected_digest=digest(policy), reviewed=True, validate_base=lambda _: None,
                     evaluator=EVALUATOR)
    assert authority.policy() == policy


def test_AR17_missing_native_head_with_preserved_files_cannot_bootstrap(world):
    authority, policy, credentials = world
    credentials.delete(authority.name)
    with pytest.raises(RenewalError):
        authority.enroll(policy, expected_digest=digest(policy), reviewed=True, validate_base=lambda _: None, evaluator={})


def test_AR13_tree_and_publication_limits(world):
    authority, _, credentials = world
    identity = approve(authority)
    with pytest.raises(RenewalError):
        authority.activate(identity, {'tree': 'f' * 40}, verify=lambda *_: None)
    state, revision = authority._state()
    state['serial'] = 32
    credentials.write(authority.name, state, expected_revision=revision)
    with pytest.raises(RenewalError):
        authority.activate(identity, {'tree': 'c' * 40}, verify=lambda *_: None)


def test_AR05_delegation_expiry_is_checked_on_every_read(world):
    from datetime import timedelta
    authority, policy, credentials = world
    policy['expires_at'] = (NOW + timedelta(hours=1)).isoformat()
    state, revision = authority._state()
    state['policy'] = authority._store(state).put(policy)
    credentials.write(authority.name, state, expected_revision=revision)
    assert authority.policy() == policy
    authority.now = lambda: NOW + timedelta(hours=2)
    with pytest.raises(RenewalError):
        authority.policy()


def test_AR17_replacement_preserves_predecessor_and_cannot_replay_old_approval(world):
    from copy import deepcopy
    authority, policy, _ = world
    old = approve(authority)
    old_epoch = authority._state()[0]['epoch']
    replacement = deepcopy(policy)
    replacement['expires_at'] = '2026-02-01T00:00:00+00:00'
    authority.replace(replacement, expected_digest=digest(replacement), reviewed=True, validate_base=lambda _: None,
                      evaluator=EVALUATOR)
    state, _ = authority._state()
    assert state['epoch'] == old_epoch + 1 and state['predecessor']
    assert authority.active() is None
    with pytest.raises(RenewalError):
        authority.task(old)
    with pytest.raises(RenewalError):
        authority.replace(replacement, expected_digest='f' * 64, reviewed=True, validate_base=lambda _: None, evaluator={})


def test_AR13_short_native_key_and_unacknowledged_save_refuse(world, monkeypatch):
    authority, _, _ = world
    state, revision = authority._state()
    with pytest.raises(RenewalError):
        authority._store({**state, 'secret': 'YQ=='})
    monkeypatch.setattr(authority.credentials, 'write', lambda *_a, **_kw: None)
    with pytest.raises(RenewalError, match='readback'):
        authority._save({**state, 'epoch': 2}, revision)


def test_AR03_gate_digest_must_match_protected_baseline_bytes(world):
    from copy import deepcopy
    authority, policy, _ = world
    candidate = deepcopy(policy)
    candidate['gates'][0]['definition_sha256'] = 'f' * 64
    with pytest.raises(RenewalError):
        authority._check_policy(candidate)


def test_AR05_native_generation_changed_at_intent_cannot_publish(world):
    authority, _, credentials = world
    approval = approve(authority)
    def advance(event):
        if event == 'intent':
            state, revision = authority._state()
            credentials.write(authority.name, {**state, 'epoch': 2}, expected_revision=revision)
    authority.observe = advance
    with pytest.raises(RenewalError, match='changed during publication'):
        authority.activate(approval, {'tree': 'c' * 40}, verify=lambda *_: None)
    assert authority._state()[0]['phase'] == 'prepared'


def test_AR17_revocation_remains_available_at_replacement_limit(world):
    authority, policy, credentials = world
    state, revision = authority._state()
    credentials.write(authority.name, {**state, 'epoch': 33}, expected_revision=revision)
    with pytest.raises(RenewalError):
        authority.replace(policy, expected_digest=digest(policy), reviewed=True, validate_base=lambda _: None, evaluator={})
    authority.revoke()
    epoch = authority._state()[0]['epoch']
    authority.revoke()
    assert authority._state()[0]['epoch'] == epoch == 34


def test_AR17_progress_retains_pushed_fact_after_revocation(world):
    authority, _, _ = world
    authority.record({'stage': 'PUSHED', 'pushed': True})
    authority.revoke()
    assert authority.progress()['pushed'] is True
    assert authority.progress()['renewal'] == 'held'
    assert authority.review_policy()
    assert authority.review_evaluator(authority.local / 'review-baseline').is_dir()


def test_AR13_future_epoch_journal_refuses(world):
    authority, _, credentials = world
    state, revision = authority._state()
    state['attempt'] = authority._store(state).put({'epoch': 2, 'policy': state['policy'], 'receipt': {}})
    credentials.write(authority.name, state, expected_revision=revision)
    with pytest.raises(RenewalError):
        authority.progress()


def test_AR12_two_permitted_diffs_keep_root_and_previous_provenance(world):
    from copy import deepcopy
    authority, _, _ = world
    first = authority.activate(approve(authority), {'tree': 'c' * 40}, verify=lambda *_: None)
    value = deepcopy(target())
    value['tree'] = 'f' * 40
    value['source'] = manifest('f')
    value['cohort']['implementation'] = 'f' * 64
    second = authority.routine(value)
    authority.activate(second, {'tree': 'f' * 40}, verify=lambda *_: None)
    assert authority.active()['previous'] == first
    assert authority.active()['serial'] == 2


def test_AR16_new_routine_candidate_does_not_reuse_different_pending_task(world):
    authority, _, _ = world
    first = authority.routine(target())
    authority.record({'attempt': first, 'stage': 'PUSHED'})
    value = target()
    value['tree'] = 'f' * 40
    assert authority.routine(value) != first


def test_AR20_cloud_or_checkout_local_authority_is_never_enrolled(tmp_path):
    credentials = CredentialStore(local=tmp_path, backend=Keyring())
    for local in (tmp_path / 'repo' / 'local', tmp_path / 'OneDrive-test' / 'local'):
        with pytest.raises(RenewalError):
            Authority(local, BINDING, credentials=credentials, checkout=tmp_path / 'repo')
    assert not credentials._backend.store


def test_AR23_incomplete_evaluator_custody_is_not_a_valid_delegation(world):
    from copy import deepcopy
    authority, policy, _ = world
    policy = deepcopy(policy)
    policy['evaluator'].pop('scripts/presentation.py')
    with pytest.raises(RenewalError):
        authority._check_policy(policy)


def test_AR11_missing_ancestor_refuses_even_on_cached_current_decision(world):
    from copy import deepcopy
    authority, _, _ = world
    first = authority.activate(approve(authority), {'tree': 'c' * 40}, verify=lambda *_: None)
    current = deepcopy(target())
    current['tree'] = 'f' * 40
    current['source'] = manifest('f')
    current['cohort']['implementation'] = 'f' * 64
    second = approve(authority, current)
    authority.activate(second, {'tree': 'f' * 40}, verify=lambda *_: None)
    authority.validate(current, validate_base=lambda _: None)
    (authority.root / (first + '.ae1')).unlink()
    with pytest.raises(RenewalError):
        authority.validate(current, validate_base=lambda _: None)
