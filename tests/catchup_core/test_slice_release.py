"""SLICE-RELEASE (.archive/specs/slice-release-2026-11-05.md): the slice profile of the authoritative-run gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import test_cutover_fence

from catchup_core import policy
from catchup_core.ledger import PermissionDenied

WAIVABLE = ['cross_domain_and_goal_cases', 'document_field_accuracy', 'held_out_labeling',
            'other_platform_observations', 'windows_work_recovery', 'workload_scenarios']


def approved(path: Path) -> dict:
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def sliced(tmp_path, monkeypatch):
    config, request, keyring, build = test_cutover_fence.configured.__wrapped__(tmp_path, monkeypatch)
    release = config['release']
    release['profile'] = 'slice'
    release.pop('scorecard')
    cohort = policy.evaluation_cohort(config, ['kids_school'])
    waiver = {'schema': 'artha.release.slice.v1', 'domains': ['kids_school'], 'waived': list(WAIVABLE),
              'not_run': True, 'owner_signed_at': '2026-10-29T00:00:00-07:00', 'cohort': cohort,
              'observation_window_days': 14}

    def publish(document):
        path = tmp_path / 'slice_waiver.json'
        path.write_text(json.dumps(document))
        release['slice_waiver'] = approved(path)
    publish(waiver)
    rehearsal = Path(release['recovery_rehearsal']['path'])
    receipt = json.loads(rehearsal.read_text())
    receipt['schema'] = 'artha.recovery-rehearsal.slice.v1'
    receipt.pop('work_checkpoint')
    receipt['observations'].pop('windows_work')
    rehearsal.write_text(json.dumps(receipt))
    release['recovery_rehearsal'] = approved(rehearsal)

    def check(domains=('kids_school',)):
        policy.validate_release(request, config, viewer='owner', domains=list(domains), hostname='authority')
    return config, check, waiver, publish, build


def test_the_full_profile_is_unchanged_and_still_needs_its_scorecard(tmp_path, monkeypatch):
    config, request, _, _ = test_cutover_fence.configured.__wrapped__(tmp_path, monkeypatch)
    policy.validate_release(request, config, viewer='owner', domains=['kids_school'], hostname='authority')
    config['release'].pop('scorecard')
    with pytest.raises(PermissionDenied, match='scorecard'):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'], hostname='authority')


def test_a_complete_slice_release_passes(sliced):
    _, check, _, _, _ = sliced
    check()


@pytest.mark.parametrize('field', ['stage1_clearance', 'rollback_snapshot', 'independent_copy',
                                   'recovery_rehearsal', 'owner_approved_at', 'slice_waiver'])
def test_the_kept_gates_and_the_waiver_are_still_required(sliced, field):
    config, check, _, _, _ = sliced
    config['release'].pop(field)
    with pytest.raises(PermissionDenied):
        check()


@pytest.mark.parametrize('change', [
    lambda w: w['waived'].remove('held_out_labeling'),
    lambda w: w['waived'].append('invented_gate'),
    lambda w: w['waived'].append('recovery_rehearsal'),
    lambda w: w['waived'].append('independent_copy'),
    lambda w: w.update(waived=[]),
    lambda w: w.update(not_run=False),
    lambda w: w.pop('not_run'),
    lambda w: w.update(owner_signed_at=''),
    lambda w: w.pop('owner_signed_at'),
    lambda w: w.update(cohort={'implementation': 'other'}),
    lambda w: w.update(observation_window_days=13),
    lambda w: w.update(observation_window_days=True),
    lambda w: w.update(schema='artha.release.v4'),
    lambda w: w.update(domains=['finance']),
    lambda w: w.update(domains=[]),
])
def test_a_waiver_that_is_not_exactly_the_approved_set_is_refused(sliced, change):
    _, check, waiver, publish, _ = sliced
    change(waiver)
    publish(waiver)
    with pytest.raises(PermissionDenied, match='slice waiver|cohort'):
        check()


def two_domain_release(sliced):
    config, check, waiver, publish, _ = sliced
    domains = ['kids_school', 'finance']
    config['release']['domains'] = domains
    cohort = policy.evaluation_cohort(config, domains)
    waiver.update(domains=domains, cohort=cohort)
    publish(waiver)
    rehearsal = Path(config['release']['recovery_rehearsal']['path'])
    receipt = json.loads(rehearsal.read_text())
    receipt['cohort'] = cohort
    rehearsal.write_text(json.dumps(receipt))
    config['release']['recovery_rehearsal'] = approved(rehearsal)
    return config, check, waiver, publish, domains


def test_a_slice_may_release_several_named_domains(sliced):                                          # SR-10
    _, check, _, _, domains = two_domain_release(sliced)
    check(domains)


def test_a_multi_domain_slice_needs_the_requested_and_waived_sets_to_match(sliced):                   # SR-10
    config, check, waiver, publish, domains = two_domain_release(sliced)
    with pytest.raises(PermissionDenied, match='Domain'):
        check(['kids_school'])
    with pytest.raises(PermissionDenied, match='Domain'):
        check(list(reversed(domains)))
    waiver.update(domains=['kids_school'])
    publish(waiver)
    with pytest.raises(PermissionDenied, match='slice waiver'):
        check(domains)


@pytest.mark.parametrize('released', [[], ['kids_school', 'kids_school'], ['kids_school', '']])
def test_released_domains_must_be_distinct_and_explicit(sliced, released):                            # SR-06
    config, check, _, _, _ = sliced
    config['release']['domains'] = released
    with pytest.raises(PermissionDenied, match='Domain'):
        check()


def test_an_unknown_profile_is_refused(sliced):
    config, check, _, _, _ = sliced
    config['release']['profile'] = 'partial'
    with pytest.raises(PermissionDenied, match='profile'):
        check()


def test_waiver_bytes_must_match_the_approved_digest(sliced):
    config, check, _, _, _ = sliced
    path = Path(config['release']['slice_waiver']['path'])
    path.write_text(path.read_text() + ' ')
    with pytest.raises(PermissionDenied):
        check()


def test_runtime_rechecks_the_waiver_bytes_after_construction(sliced):
    config, _, _, _, build = sliced
    deps = build()
    try:
        deps.validate_inputs()
        path = Path(config['release']['slice_waiver']['path'])
        path.write_text(path.read_text() + ' ')
        with pytest.raises(PermissionDenied):
            deps.validate_inputs()
    finally:
        deps.ledger.close()


def test_a_waiver_for_a_changed_implementation_is_refused_at_revalidation(sliced, monkeypatch):
    config, _, waiver, publish, build = sliced
    deps = build()
    try:
        waiver['cohort'] = {**waiver['cohort'], 'implementation': 'changed-after-approval'}
        publish(waiver)
        with pytest.raises(PermissionDenied):
            deps.validate_inputs()
    finally:
        deps.ledger.close()


def _receipt(config):
    path = Path(config['release']['recovery_rehearsal']['path'])
    return path, json.loads(path.read_text())


@pytest.mark.parametrize('change', [
    lambda r: r['observations'].update(windows_work=r['observations']['personal_cloud_copy']),
    lambda r: r.update(work_checkpoint={'path': 'x', 'sha256': 'y', 'profile': 'work', 'generation': 'g'}),
    lambda r: r.update(schema='artha.recovery-rehearsal.v2'),
    lambda r: r['observations'].pop('personal_cloud_copy'),
    lambda r: r['observations'].pop('mac_personal'),
    lambda r: r['observations']['personal_cloud_copy'].update(source='local_copy'),
])
def test_the_slice_recovery_receipt_waives_only_windows_work(sliced, change):
    config, check, _, _, _ = sliced
    path, receipt = _receipt(config)
    change(receipt)
    path.write_text(json.dumps(receipt))
    config['release']['recovery_rehearsal'] = approved(path)
    with pytest.raises(PermissionDenied):
        check()


def test_the_full_profile_still_demands_windows_work_recovery(tmp_path, monkeypatch):
    config, request, _, _ = test_cutover_fence.configured.__wrapped__(tmp_path, monkeypatch)
    path = Path(config['release']['recovery_rehearsal']['path'])
    receipt = json.loads(path.read_text())
    receipt['schema'] = 'artha.recovery-rehearsal.slice.v1'
    receipt.pop('work_checkpoint')
    receipt['observations'].pop('windows_work')
    path.write_text(json.dumps(receipt))
    config['release']['recovery_rehearsal'] = approved(path)
    with pytest.raises(PermissionDenied):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'], hostname='authority')
