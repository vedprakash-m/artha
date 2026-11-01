"""Spend-seal history can require existing custody; missing metadata is never mistaken for corrupt metadata."""
import pytest

from catchup_core import recovery_registry as registry


def test_absent_registry_or_unregistered_seal_does_not_claim_prior_seal_custody(tmp_path):
    assert not registry.spend_seal_required(root=tmp_path / 'absent')
    registry.declare('personal', 'vault', 'age-key', 'artha', 'historical', root=tmp_path / 'present')
    assert not registry.spend_seal_required(root=tmp_path / 'present')


@pytest.mark.parametrize('deleted', [False, True])
def test_active_and_retired_seals_always_require_existing_spend_history(tmp_path, deleted):
    local = tmp_path / 'authority'
    with registry.mutation('personal', 'spend_seal', *registry.SPEND_SEAL, '1', root=local, deleted=deleted):
        pass
    assert registry.spend_seal_required(root=local)


@pytest.mark.parametrize('state', ['pending', 'declared', 'wrong-kind', 'corrupt'])
def test_uncertain_or_corrupt_seal_registry_never_grants_fresh_budget(tmp_path, state):
    local = tmp_path / 'authority'
    if state == 'pending':
        with pytest.raises(RuntimeError), registry.enroll_spend_seal(root=local):
            raise RuntimeError('synthetic interrupted publication')
    else:
        registry.declare('personal', 'key' if state == 'wrong-kind' else 'spend_seal',
                         *registry.SPEND_SEAL, '1', root=local)
        if state == 'corrupt':
            (local / 'recovery-registry/personal.json').write_text('{broken')
    with pytest.raises(registry.RegistryError):
        registry.spend_seal_required(root=local)


def test_unsafe_registry_is_not_an_absent_registry(tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    local = tmp_path / 'local'
    local.mkdir(mode=0o700)
    (local / 'recovery-registry').symlink_to(other, target_is_directory=True)
    with pytest.raises(registry.RegistryError):
        registry.spend_seal_required(root=local)
    assert list(other.iterdir()) == []


def test_first_shadow_seal_does_not_claim_authoritative_budget_custody(tmp_path):
    local = tmp_path / 'authority'
    with registry.enroll_spend_seal(root=local, mode='shadow'):
        pass
    assert registry.spend_seal_required(root=local, mode='shadow')
    assert not registry.spend_seal_required(root=local, mode='authoritative')
    assert registry.spend_seal_enrolled(root=local)
    with registry.enroll_spend_seal(root=local, mode='authoritative'):
        pass
    assert registry.spend_seal_required(root=local, mode='authoritative')


@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
def test_legacy_seal_custody_cannot_be_reset_by_choosing_a_mode(tmp_path, mode):
    local = tmp_path / 'authority'
    with registry.enroll_spend_seal(root=local):
        pass
    assert registry.spend_seal_required(root=local, mode=mode)


@pytest.mark.parametrize('mode', ['', 'work', 'SHADOW', '../shadow'])
def test_unrecognized_spend_seal_namespace_refuses(tmp_path, mode):
    with pytest.raises(registry.RegistryError):
        registry.spend_seal_required(root=tmp_path, mode=mode)


def test_capture_classifies_each_mode_instead_of_hiding_an_unregistered_seal(tmp_path):
    from catchup_core import checkpoint, checkpoint_cli
    local, metadata = tmp_path / 'local', tmp_path / 'registry'
    seal_dir = local / 'spend-seal'
    seal_dir.mkdir(parents=True)
    (seal_dir / 'authoritative.json').write_text('{}')
    with registry.enroll_spend_seal(root=metadata, mode='shadow'):
        pass
    with pytest.raises(checkpoint.CheckpointBlocked):
        checkpoint_cli._classify_spend_seal({'local': local}, {}, local, metadata)
    with registry.enroll_spend_seal(root=metadata, mode='authoritative'):
        pass
    excluded = {}
    checkpoint_cli._classify_spend_seal({'local': local}, excluded, local, metadata)
    assert 'local/spend-seal' in excluded
    (seal_dir / 'unexpected.json').write_text('{}')
    with pytest.raises(checkpoint.CheckpointBlocked):
        checkpoint_cli._classify_spend_seal({'local': local}, {}, local, metadata)
