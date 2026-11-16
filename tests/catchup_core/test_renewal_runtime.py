import test_renewal_authority as authority_fixtures
world = authority_fixtures.world
"""The actual release validator retains historical evidence and independent controls."""
from copy import deepcopy

import pytest
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def synthetic_metadata(monkeypatch):
    monkeypatch.setattr(renewal_runtime, "metadata_for", lambda _: SimpleNamespace(snapshot=lambda **kw: "0" * 64))


from catchup_core import policy, renewal_runtime
from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.ledger import PermissionDenied
from catchup_core.renewal_store import RenewalError
from test_renewal_authority import COHORT, approve, target


def test_AR19_unconfigured_legacy_does_not_open_native_authority(monkeypatch):
    monkeypatch.setattr(renewal_runtime, 'authority_for', lambda *_: pytest.fail('unexpected native access'))
    assert renewal_runtime.resolve({'release': {}}, COHORT, viewer='owner') == COHORT


def test_AR01_actual_validator_uses_original_cohort_without_rewriting_it(world, monkeypatch):
    authority, delegation, _ = world
    authority.activate(approve(authority), {'tree': 'c' * 40}, verify=lambda *_: None)
    monkeypatch.setattr(renewal_runtime, 'authority_for', lambda *_: authority)
    monkeypatch.setattr(renewal_runtime, 'snapshot', lambda *_: target())
    monkeypatch.setattr(renewal_runtime, 'context', lambda *_: 'd' * 64)
    release = deepcopy(delegation['base_release'])
    release['renewal'] = {'schema': 'artha.release.renewal.v1', 'delegation': authority._state()[0]['policy']}
    config = {'cutover': True, 'release': release}
    seen = []
    monkeypatch.setattr(policy, '_validate_release_evidence', lambda *args, **kw: seen.append(kw['cohort']))
    request = CatchupRequest(catchup_id='synthetic', channel='cli', actor='owner', host='synthetic', mode=ExecutionMode.AUTHORITATIVE)
    policy.validate_release(request, config, viewer='owner', domains=['kids_school'], cohort=target()['cohort'])
    assert seen == [COHORT]
    assert delegation['base_release']['owner_approved_at'] == 'original'
    authority.revoke()
    with pytest.raises(PermissionDenied):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'], cohort=target()['cohort'])


def test_AR11_pointer_and_base_approval_cannot_be_forged(world, monkeypatch):
    authority, delegation, _ = world
    monkeypatch.setattr(renewal_runtime, 'authority_for', lambda *_: authority)
    for pointer in ({}, {'schema': 'unsupported'}, {'schema': 'artha.release.renewal.v1', 'delegation': 'f' * 64}):
        with pytest.raises(PermissionDenied):
            renewal_runtime.resolve({'release': {**delegation['base_release'], 'renewal': pointer}}, COHORT, viewer='owner')


def test_AR24_shadow_never_reads_renewal_or_writable_ledger(monkeypatch):
    monkeypatch.setattr(renewal_runtime, 'authority_for', lambda *_: pytest.fail('unexpected native access'))
    request = CatchupRequest(catchup_id='synthetic', channel='cli', actor='owner', host='synthetic', mode=ExecutionMode.SHADOW)
    policy.validate_release(request, {'release': {'renewal': {}}}, viewer='owner', domains=[])


def test_AR05_frozen_runtime_lease_checks_native_epoch_without_source_rescan(world, monkeypatch):
    authority, _, _ = world
    authority.activate(approve(authority), {'tree': 'c' * 40}, verify=lambda *_: None)
    lease = renewal_runtime.RenewalLease(authority, target(), COHORT)
    monkeypatch.setattr(renewal_runtime, 'context', lambda *_: 'd' * 64)
    monkeypatch.setattr(renewal_runtime, 'snapshot', lambda *_: pytest.fail('per-use source rescan'))
    assert lease.validate() == COHORT
    monkeypatch.setattr(renewal_runtime, 'context', lambda *_: 'f' * 64)
    with pytest.raises(PermissionDenied):
        lease.validate()


def test_AR20_authority_factory_uses_existing_native_binding_only(world, monkeypatch):
    authority, _, credentials = world
    monkeypatch.setattr(renewal_runtime, 'CredentialStore', lambda **_: credentials)
    monkeypatch.setattr(renewal_runtime.socket, 'gethostname', lambda: 'synthetic')
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(authority.local.parent))
    monkeypatch.setattr(renewal_runtime.Workspace, 'remote', lambda *_: None)
    factory = renewal_runtime.authority_for('owner')
    assert factory.binding == authority.binding
    with pytest.raises(RenewalError) as held:
        renewal_runtime.authority_for('someone-else')
    assert 'release owner' in str(held.value)
    credentials.delete(authority.name)
    with pytest.raises(RenewalError) as held:
        renewal_runtime.authority_for('owner')
    assert 'enrollment' in str(held.value)


def test_AR20_pointer_without_active_decision_refuses(world, monkeypatch):
    from catchup_core.renewal_store import digest
    authority, delegation, _ = world
    monkeypatch.setattr(renewal_runtime, 'authority_for', lambda *_: authority)
    with pytest.raises(PermissionDenied):
        renewal_runtime.resolve({'release': {**delegation['base_release'], 'renewal': {
            'schema': 'artha.release.renewal.v1', 'delegation': digest(delegation)}}}, COHORT, viewer='owner')


def test_AR14_renewed_cohort_does_not_lift_existing_release_holds(world, monkeypatch):
    authority, delegation, _ = world
    monkeypatch.setattr(policy, 'resolve', lambda *_, **__: COHORT)
    def held(*_, **__):
        raise PermissionDenied('personal migration hold')
    monkeypatch.setattr(policy, '_validate_release_evidence', held)
    release = {**deepcopy(delegation['base_release']),
               'renewal': {'schema': 'artha.release.renewal.v1', 'delegation': authority._state()[0]['policy']}}
    request = CatchupRequest(catchup_id='synthetic', channel='cli', actor='owner', host='synthetic', mode=ExecutionMode.AUTHORITATIVE)
    with pytest.raises(PermissionDenied, match='migration hold'):
        policy.validate_release(request, {'cutover': True, 'release': release}, viewer='owner',
                                domains=['kids_school'], cohort=target()['cohort'])


def test_AR26_lease_refuses_when_current_privacy_authority_changes(world, monkeypatch):
    authority, _, _ = world
    authority.activate(approve(authority), {'tree': 'c' * 40}, verify=lambda *_: None)
    monkeypatch.setattr(renewal_runtime, 'context', lambda *_: 'd' * 64)
    lease = renewal_runtime.RenewalLease(authority, target(), COHORT)
    assert lease.validate() == COHORT
    def changed(**_):
        raise RenewalError('privacy history differs from its native authority', reason='authority')
    monkeypatch.setattr(renewal_runtime, 'metadata_for', lambda _: SimpleNamespace(snapshot=changed))
    with pytest.raises(PermissionDenied):
        lease.validate()
