"""tools/release_evidence.py joins measured inputs into gate-valid release evidence and refuses anything less."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import policy
from catchup_core.ledger import PermissionDenied
from test_cutover_fence import NOW, configured, synthetic_recovery_acceptance  # noqa: F401
from test_integrated_surfaces import release_scorecard

_SPEC = importlib.util.spec_from_file_location(
    'release_evidence', Path(__file__).resolve().parents[2] / 'tools' / 'release_evidence.py')
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)

SIGNED = '2026-10-26T00:00:00+00:00'


def split(scorecard):
    heldout = {k: scorecard[k] for k in ('cohort', 'evaluations', 'corpus', 'document_fields')}
    bounds = {d: {k: v for k, v in b.items() if k != 'source_precedence'} for d, b in scorecard['bounds'].items()}
    return heldout, bounds, scorecard['measurements']


def retained_outcome(heldout, live=True):
    cases = heldout['evaluations']
    signal_ids = [case['signal_id'] for case in cases]
    return {
        'catchup_id': cases[0]['catchup_id'],
        'signals': signal_ids,
        'permission_generation': 7,
        'provider_signal_generations': {signal_id: 7 for signal_id in signal_ids},
        'consented_live_signal_ids': signal_ids if live else [],
    }


def approve(path, config, field='scorecard'):
    config['release'][field] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def parts(configured, tmp_path):  # noqa: F811
    config, request, _, _ = configured
    heldout, bounds, measurements = split(release_scorecard(config))
    return config, request, tmp_path / 'repo', heldout, bounds, measurements


def test_assembled_scorecard_passes_the_runtime_release_gate(parts, tmp_path):
    config, request, repo, heldout, bounds, measurements = parts
    result = tool.assemble_release(config, repo, heldout, bounds, measurements, SIGNED, ['kids_school'],
                                   outcome=retained_outcome(heldout))
    assert result['owner_signed_at'] == SIGNED and result['live_cases'] == 6
    assert all(case['consented_live'] for case in result['evaluations'])
    out = tmp_path / 'assembled.json'
    out.write_text(json.dumps(result))
    approve(out, config)
    policy.validate_release(request, config, viewer='owner', domains=['kids_school'])


@pytest.mark.parametrize('mutate, message', [
    (lambda h, b, m: m['kids_school']['workloads'].pop('retention_2y'), 'scenario|workload'),
    (lambda h, b, m: m['kids_school'].update(cohort={'implementation': 'other'}), 'cohort'),
    (lambda h, b, m: b['kids_school'].pop('maximum_monthly_usd'), 'bounds'),
    (lambda h, b, m: m['kids_school'].update(completed_runs=18), 'complet|run'),
    (lambda h, b, m: m['kids_school']['source_age_hours'].pop('calendar'), 'freshness'),
    (lambda h, b, m: b['kids_school'].update(source_precedence={'x': ['y']}), 'precedence'),
    (lambda h, b, m: h['evaluations'][0].update(surfaced=[]), 'evidence|recall|safety'),
])
def test_assembly_refuses_incomplete_or_contradicted_evidence(parts, mutate, message):
    config, _, repo, heldout, bounds, measurements = parts
    mutate(heldout, bounds, measurements)
    with pytest.raises(PermissionDenied, match=message):
        tool.assemble_release(config, repo, heldout, bounds, measurements, SIGNED, ['kids_school'],
                              outcome=retained_outcome(heldout))


def test_edited_live_booleans_cannot_fabricate_provider_evidence(parts):
    config, _, repo, heldout, bounds, measurements = parts
    for case in heldout['evaluations']:
        case['consented_live'] = True
    outcome = retained_outcome(heldout, live=False)
    with pytest.raises(PermissionDenied, match='consented live evidence'):
        tool.assemble_release(config, repo, heldout, bounds, measurements, SIGNED, ['kids_school'],
                             outcome=outcome)


def test_assembler_recomputes_live_booleans_from_retained_outcome(parts):
    config, _, repo, heldout, bounds, measurements = parts
    for case in heldout['evaluations']:
        case['consented_live'] = False
    outcome = retained_outcome(heldout)
    result = tool.assemble_release(config, repo, heldout, bounds, measurements, SIGNED, ['kids_school'],
                                   outcome=outcome)
    assert result['live_cases'] == 6
    assert all(case['consented_live'] for case in result['evaluations'])


@pytest.mark.parametrize('mutate', [
    lambda heldout, outcome: outcome.update(catchup_id='another-catchup'),
    lambda heldout, outcome: outcome['signals'].pop(),
    lambda heldout, outcome: outcome['provider_signal_generations'].update(
        {outcome['signals'][0]: outcome['permission_generation'] + 1}),
])
def test_assembler_refuses_unbound_retained_provenance(parts, mutate):
    config, _, repo, heldout, bounds, measurements = parts
    outcome = retained_outcome(heldout)
    mutate(heldout, outcome)
    with pytest.raises(tool.EvidenceError):
        tool.assemble_release(config, repo, heldout, bounds, measurements, SIGNED, ['kids_school'],
                             outcome=outcome)


def test_load_retained_outcome_uses_canonical_inspection_ledger(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    config_path = repo / 'config' / 'custom.yaml'
    ledger_path = tmp_path / 'private' / 'ledger.db'
    outcome = {'catchup_id': 'cu_retained'}
    calls = {}

    class Ledger:
        _path = ledger_path
        closed = False

        def close(self):
            self.closed = True

    ledger = Ledger()
    deps = SimpleNamespace(ledger=ledger, maintenance=lambda: nullcontext())
    monkeypatch.setattr(tool, 'load_runtime_config',
                        lambda path: calls.update(config_path=path) or SimpleNamespace(viewer='owner'))

    def build_deps(request, **kwargs):
        calls.update(request=request, **kwargs)
        return deps

    monkeypatch.setattr(tool, 'build_deps', build_deps)
    monkeypatch.setattr(tool, 'session_lock',
                        lambda path: calls.update(lock_path=path) or nullcontext())
    monkeypatch.setattr(tool.corpus, 'latest_outcome',
                        lambda actual_ledger, catchup_id: outcome
                        if actual_ledger is ledger and catchup_id == 'cu_retained' else None)

    assert tool._load_retained_outcome(config_path, 'cu_retained') is outcome
    assert calls['config_path'] == repo / 'config' / 'catchup_core.yaml'
    assert calls['repo_root'] == repo and calls['inspection'] is True
    assert calls['request'].actor == 'owner'
    assert calls['request'].mode is tool.ExecutionMode.SHADOW
    assert calls['lock_path'] == ledger_path.with_suffix('.session.lock')
    assert ledger.closed


@pytest.mark.parametrize('missing', ['corpus', 'bounds', 'measurements', 'signature'])
def test_assembly_refuses_absent_inputs(parts, missing):
    config, _, repo, heldout, bounds, measurements = parts
    signed = SIGNED
    if missing == 'corpus':
        heldout.pop('corpus')
    elif missing == 'bounds':
        bounds.clear()
    elif missing == 'measurements':
        measurements.clear()
    else:
        signed = ' '
    with pytest.raises(tool.EvidenceError):
        tool.assemble_release(config, repo, heldout, bounds, measurements, signed, ['kids_school'],
                              outcome=retained_outcome(heldout))


def run_line(cid, seconds, *, status='complete', version='v1'):
    return {'catchup_id': cid, 'mode': 'catchup', 'status': status, 'total_seconds': seconds, 'resumed': False,
            'at': '2026-10-20T00:00:00+00:00', 'implementation_version': version, 'stages': {}}


def measure_plan(cohort_scorecard):
    template = cohort_scorecard['measurements']['kids_school']
    scenarios = {}
    for name, row in template['workloads'].items():
        scenarios[name] = {'catchup_ids': [f'{name}-{i}' for i in range(3)],
                           **{k: v for k, v in row.items() if k not in ('cohort', 'runs', 'passed', 'p95_seconds')}}
    return {'domain': 'kids_school', 'workload': template['workload'], 'review_minutes': 5, 'monthly_usd': 10,
            'source_age_hours': template['source_age_hours'], 'scenarios': scenarios}


def write_metrics(directory, plan, **overrides):
    directory.mkdir()
    lines = [run_line(cid, 2.0 + i, **overrides)
             for row in plan['scenarios'].values() for i, cid in enumerate(row['catchup_ids'])]
    (directory / '2026-10.jsonl').write_text('\n'.join(json.dumps(line) for line in lines) + '\n')


def test_measure_feeds_a_gate_valid_assembly(parts, tmp_path):
    config, request, repo, heldout, bounds, _ = parts
    plan = measure_plan(release_scorecard(config))
    write_metrics(tmp_path / 'metrics', plan)
    measured = tool.measure(config, repo, tmp_path / 'metrics', plan, ['kids_school'])
    row = measured['kids_school']
    assert row['total_runs'] == row['completed_runs'] == 21
    assert row['p95_seconds'] == 4.0 and row['workloads']['long_gap']['runs'] == 3
    result = tool.assemble_release(config, repo, heldout, bounds, measured, SIGNED, ['kids_school'],
                                   outcome=retained_outcome(heldout))
    out = tmp_path / 'scorecard.json'
    out.write_text(json.dumps(result))
    approve(out, config)
    policy.validate_release(request, config, viewer='owner', domains=['kids_school'])


def test_measure_refuses_missing_attestation_absent_run_and_mixed_versions(parts, tmp_path):
    config, _, repo, _, _, _ = parts
    plan = measure_plan(release_scorecard(config))
    write_metrics(tmp_path / 'metrics', plan)
    unattested = copy.deepcopy(plan)
    del unattested['scenarios']['provider_reset']['verified_alerts_preserved']
    with pytest.raises(tool.EvidenceError, match='attestation'):
        tool.measure(config, repo, tmp_path / 'metrics', unattested, ['kids_school'])
    unrun = copy.deepcopy(plan)
    unrun['scenarios']['long_gap']['catchup_ids'].append('never-ran')
    with pytest.raises(tool.EvidenceError, match='no metric record'):
        tool.measure(config, repo, tmp_path / 'metrics', unrun, ['kids_school'])
    missing = copy.deepcopy(plan)
    del missing['scenarios']['digest_limited']
    with pytest.raises(tool.EvidenceError, match='seven'):
        tool.measure(config, repo, tmp_path / 'metrics', missing, ['kids_school'])
    mixed = tmp_path / 'mixed'
    write_metrics(mixed, plan)
    with (mixed / '2026-10.jsonl').open('a') as stream:
        stream.write(json.dumps(run_line('normal_cold-0', 1.0, version='v2')) + '\n')
    plan['scenarios']['normal_cold']['catchup_ids'] = ['normal_cold-0']
    with pytest.raises(tool.EvidenceError, match='single-version'):
        tool.measure(config, repo, mixed, plan, ['kids_school'])


def test_recovery_receipt_binds_checkpoints_and_passes_the_recovery_gate(parts, tmp_path, configured):  # noqa: F811
    config, request, _, _, _, _ = parts
    synthetic = json.loads(Path(config['release']['recovery_rehearsal']['path']).read_text())
    work = Path(synthetic['work_checkpoint']['path'])
    observed = {name: {k: v for k, v in row.items()
                       if k not in ('profile', 'checkpoint_sha256', 'checkpoint_generation')}
                for name, row in synthetic['observations'].items()}
    receipt = tool.recovery_receipt(config, work, observed, SIGNED)
    assert receipt['work_checkpoint']['sha256'] == synthetic['work_checkpoint']['sha256']
    out = tmp_path / 'receipt.json'
    out.write_text(json.dumps(receipt))
    approve(out, config, 'recovery_rehearsal')
    policy.validate_release(request, config, viewer='owner', domains=['kids_school'])

    tampered = copy.deepcopy(observed)
    tampered['personal_cloud_copy']['recovered_sha256'] = '0' * 64
    with pytest.raises(PermissionDenied, match='OneDrive'):
        tool.recovery_receipt(config, work, tampered, SIGNED)
    with pytest.raises(PermissionDenied, match='precede'):
        tool.recovery_receipt(config, work, observed, '2026-10-01T00:00:00+00:00')
    with pytest.raises(tool.EvidenceError, match='exactly'):
        tool.recovery_receipt(config, work, {'mac_personal': observed['mac_personal']}, SIGNED)


def test_cli_writes_only_on_success_and_rejects_duplicate_keys(parts, tmp_path, monkeypatch):
    config, _, repo, heldout, bounds, measurements = parts
    monkeypatch.setattr(tool, '_load_config', lambda path: (config, repo))
    monkeypatch.setattr(tool, '_load_retained_outcome', lambda config_path, catchup_id:
                        retained_outcome(heldout))
    files = {}
    for name, value in (('h', heldout), ('b', bounds), ('m', measurements)):
        files[name] = tmp_path / f'{name}.json'
        files[name].write_text(json.dumps(value))
    out = tmp_path / 'out' / 'scorecard.json'
    argv = ['assemble-release', '--heldout', str(files['h']), '--bounds', str(files['b']),
            '--measurements', str(files['m']), '--owner-signed-at', SIGNED, '--out', str(out),
            '--domains', 'kids_school']
    assert tool.main(argv) == 0 and json.loads(out.read_text())['schema'] == 'artha.release.v4'
    out.unlink()
    files['h'].write_text('[]')
    assert tool.main(argv) == 2 and not out.exists()
    files['h'].write_text(json.dumps({**heldout, 'evaluations': [
        {**heldout['evaluations'][0], 'catchup_id': ['malformed']}, *heldout['evaluations'][1:]]}))
    assert tool.main(argv) == 2 and not out.exists()
    files['b'].write_text('{"kids_school": {}, "kids_school": {}}')
    assert tool.main(argv) == 2 and not out.exists()
    files['b'].write_text(json.dumps({}))
    assert tool.main(argv) == 2 and not out.exists()
    assert not list(out.parent.glob('.*.tmp'))

def test_config_and_domain_loading_refuse_unusable_inputs(tmp_path):
    bad = tmp_path / 'config' / 'bad.yaml'
    bad.parent.mkdir()
    bad.write_text('a: [')
    with pytest.raises(tool.EvidenceError, match='YAML'):
        tool._load_config(bad)
    bad.write_text('- 1\n')
    with pytest.raises(tool.EvidenceError, match='mapping'):
        tool._load_config(bad)
    bad.write_text('release: {domains: [kids_school]}\n')
    config, workspace = tool._load_config(bad)
    assert workspace == tmp_path.resolve() and tool._domains(config, None) == ['kids_school']
    with pytest.raises(tool.EvidenceError, match='no Domains'):
        tool._domains({}, None)


def test_write_leaves_no_temp_file_when_serialization_fails(tmp_path):
    with pytest.raises(TypeError):
        tool._write(tmp_path / 'x.json', {'bad': object()})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('mutate, message', [
    (lambda p: p.update(domain='finance'), 'not among'),
    (lambda p: p['scenarios']['long_gap'].update(catchup_ids=['a', 'a']), 'distinct'),
    (lambda p: p['scenarios']['long_gap'].pop('reference_machine'), 'reference_machine'),
    (lambda p: p.pop('monthly_usd'), 'monthly_usd'),
])
def test_measure_refuses_malformed_plans(parts, tmp_path, mutate, message):
    config, _, repo, _, _, _ = parts
    plan = measure_plan(release_scorecard(config))
    write_metrics(tmp_path / 'metrics', plan)
    mutate(plan)
    with pytest.raises(tool.EvidenceError, match=message):
        tool.measure(config, repo, tmp_path / 'metrics', plan, ['kids_school'])


def test_recovery_receipt_requires_prior_approvals(parts):
    config, _, _, _, _, _ = parts
    work = Path(json.loads(Path(config['release']['recovery_rehearsal']['path']).read_text())
                ['work_checkpoint']['path'])
    unapproved = copy.deepcopy(config)
    unapproved['release'][policy._evidence_field(unapproved['release'])] = None
    with pytest.raises(tool.EvidenceError, match='approved first'):
        tool.recovery_receipt(unapproved, work, {}, SIGNED)
    unapproved['release'].pop('rollback_snapshot')
    with pytest.raises(tool.EvidenceError, match='rollback_snapshot'):
        tool.recovery_receipt(unapproved, work, {}, SIGNED)


def test_cli_measure_and_recovery_receipt_write_their_artifacts(parts, tmp_path, monkeypatch):
    config, _, repo, _, _, _ = parts
    monkeypatch.setattr(tool, '_load_config', lambda path: (config, repo))
    plan = measure_plan(release_scorecard(config))
    write_metrics(tmp_path / 'metrics', plan)
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    out = tmp_path / 'measured.json'
    assert tool.main(['measure', '--metrics-dir', str(tmp_path / 'metrics'), '--plan', str(tmp_path / 'plan.json'),
                      '--out', str(out), '--domains', 'kids_school']) == 0
    assert json.loads(out.read_text())['kids_school']['total_runs'] == 21
    synthetic = json.loads(Path(config['release']['recovery_rehearsal']['path']).read_text())
    observed = {name: {k: v for k, v in row.items()
                       if k not in ('profile', 'checkpoint_sha256', 'checkpoint_generation')}
                for name, row in synthetic['observations'].items()}
    (tmp_path / 'obs.json').write_text(json.dumps(observed))
    receipt = tmp_path / 'receipt.json'
    assert tool.main(['recovery-receipt', '--work-checkpoint', synthetic['work_checkpoint']['path'],
                      '--observations', str(tmp_path / 'obs.json'), '--owner-signed-at', SIGNED,
                      '--out', str(receipt)]) == 0
    assert json.loads(receipt.read_text())['schema'] == 'artha.recovery-rehearsal.v2'
