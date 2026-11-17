import test_renewal_authority as authority_fixtures
world = authority_fixtures.world
import pytest

from catchup_core import renewal_entry
from catchup_core.renewal_store import RenewalError


def test_cli_help_and_argument_limits(capsys):
    with pytest.raises(SystemExit) as raised:
        renewal_entry.main(['--help'])
    assert raised.value.code == 0
    assert 'deliver' in capsys.readouterr().out


def test_local_gates_never_pass_native_keys_to_child(monkeypatch, tmp_path):
    from types import SimpleNamespace
    seen = []
    monkeypatch.setattr(renewal_entry.shutil, 'which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(renewal_entry, 'Workspace', lambda _: SimpleNamespace(git=lambda *_: 'a' * 40))
    proof = renewal_entry.local_gates(tmp_path, runner=lambda args, root, **kw: seen.append((args, root, kw)))
    assert seen[0][0][1] == 'check' and seen[0][1] == tmp_path
    assert seen[1][0][-1] == '--fail-under=95' and proof['base_commit'] == 'a' * 40
    assert all('env' not in call[2] for call in seen)


def test_local_gate_failure_is_bounded(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(renewal_entry.shutil, 'which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(renewal_entry, 'Workspace', lambda _: SimpleNamespace(git=lambda *_: 'a' * 40))
    def held(*_a, **_kw):
        raise RenewalError('local delivery gates held')
    with pytest.raises(RenewalError, match='local delivery gates held'):
        renewal_entry.local_gates(tmp_path, runner=held)


@pytest.fixture
def cli_world(world, monkeypatch):
    from catchup_core.renewal_entry import _parser
    from test_renewal_authority import target
    from test_renewal_delivery import Workspace
    authority, delegation, _ = world
    from types import SimpleNamespace
    monkeypatch.setattr(renewal_entry, "metadata_for", lambda _: SimpleNamespace(snapshot=lambda **kw: "0" * 64))
    monkeypatch.setattr(renewal_entry, "load_runtime_config", lambda _: SimpleNamespace(
        policy={"release": delegation["base_release"]}, viewer="owner"))
    monkeypatch.setattr(renewal_entry, "_isolated_evaluator", lambda: None)
    root = __import__('pathlib').Path(renewal_entry.__file__).parents[2]
    monkeypatch.setattr(authority, 'evaluator', lambda _: root)
    monkeypatch.setattr(renewal_entry, 'current', lambda *_: target())
    args = _parser().parse_args(['status', '--checkout', authority.checkout, '--quiescent'])
    workspace = Workspace()
    monkeypatch.setattr(renewal_entry, "Workspace", lambda _: workspace)
    return authority, workspace, args


def test_AR16_plan_exact_review_approve_and_delivery(cli_world, monkeypatch, capsys):
    from catchup_core.renewal_store import digest
    from test_renewal_authority import target
    authority, workspace, args = cli_world
    args.command = 'plan'
    args.output = authority.local / 'task.json'
    renewal_entry._operate(args, __import__('pathlib').Path(authority.checkout), authority, workspace)
    assert renewal_entry._load(args.output) == target()
    args.command, args.document, args.reviewed = 'approve', args.output, digest(target())
    renewal_entry._operate(args, __import__('pathlib').Path(authority.checkout), authority, workspace)
    assert 'task approval:' in capsys.readouterr().out
    args.command = 'deliver'
    monkeypatch.setattr(renewal_entry, 'local_gates', lambda _: None)
    monkeypatch.setattr(renewal_entry, 'base_verifier', lambda *_: None)
    monkeypatch.setattr(renewal_entry, 'collect', lambda *_a, **_kw: ({'status': 'passed', 'requests': 3}, ()))
    assert renewal_entry._operate(args, __import__('pathlib').Path(authority.checkout), authority, workspace) == 0
    assert 'renewal renewed' in capsys.readouterr().out
    args.command = 'recover'
    assert renewal_entry._operate(args, __import__('pathlib').Path(authority.checkout), authority, workspace) == 0


@pytest.mark.parametrize('command', ['plan', 'approve'])
def test_AR13_cli_requires_exact_private_review_documents(cli_world, command):
    authority, workspace, args = cli_world
    args.command = command
    with pytest.raises(RenewalError):
        renewal_entry._operate(args, __import__('pathlib').Path(authority.checkout), authority, workspace)


def test_AR23_current_candidate_cannot_replace_frozen_evaluator(cli_world, monkeypatch, tmp_path):
    authority, workspace, args = cli_world
    monkeypatch.setattr(authority, 'evaluator', lambda _: tmp_path)
    with pytest.raises(RenewalError, match='preserved approved evaluator'):
        renewal_entry._operate(args, tmp_path, authority, workspace)


def test_AR18_private_proposal_and_receipt_never_publish_raw_values(cli_world, capsys, tmp_path):
    from catchup_core.renewal_delivery import public_receipt
    authority, workspace, args = cli_world
    with pytest.raises(RenewalError):
        renewal_entry._private_write(tmp_path / 'public.json', {'secret': 'PRIVATE_CANARY'}, authority.local)
    assert 'PRIVATE_CANARY' not in public_receipt({'commit': 'PRIVATE_CANARY', 'ci': 'PRIVATE_CANARY',
        'stage': 'PRIVATE_CANARY', 'renewal': 'PRIVATE_CANARY', 'reason': 'PRIVATE_CANARY'})
    assert renewal_entry._execute(args, tmp_path, authority) == 0
    args.command = 'revoke'
    assert renewal_entry._execute(args, tmp_path, authority) == 0
    assert 'revoked' in capsys.readouterr().out


def test_AR08_quiescence_is_a_real_required_input(cli_world):
    authority, workspace, args = cli_world
    args.command, args.quiescent = 'deliver', False
    with pytest.raises(RenewalError, match='quiescence'):
        renewal_entry._execute(args, __import__('pathlib').Path(authority.checkout), authority)


def test_AR13_recovery_rechecks_current_source(cli_world):
    authority, _, _ = cli_world
    with pytest.raises(RenewalError):
        renewal_entry._recovery_check(__import__('pathlib').Path(authority.checkout), authority.policy(), {}, authority)


def test_AR20_attachment_requires_exact_reviewed_policy_and_original_baseline(cli_world, tmp_path, monkeypatch):
    import yaml
    from catchup_core.renewal_store import digest
    authority, _, _ = cli_world
    policy = authority.policy()
    root = tmp_path / 'attachment'
    (root / 'config').mkdir(parents=True)
    data = {'viewer': 'owner', 'release': policy['base_release']}
    (root / 'config/catchup_core.yaml').write_text(yaml.safe_dump(data))
    monkeypatch.setattr(renewal_entry, 'load_runtime_config', lambda path:
                        type('C', (), {'policy': yaml.safe_load(path.read_text())})())
    monkeypatch.setattr(renewal_entry, 'current', lambda *_: {'source': policy['base_source'],
                                                           'cohort': policy['base_cohort']})
    with pytest.raises(RenewalError):
        renewal_entry.attach(root, authority, 'f' * 64)
    renewal_entry.attach(root, authority, digest(policy))
    result = yaml.safe_load((root / 'config/catchup_core.yaml').read_text())
    assert result['release']['renewal'] == {'schema': 'artha.release.renewal.v1', 'delegation': digest(policy)}
    assert {k: v for k, v in result['release'].items() if k != 'renewal'} == policy['base_release']


def test_AR20_main_status_success_and_invalid_configuration_are_bounded(cli_world, monkeypatch, capsys):
    from types import SimpleNamespace
    from catchup_core.runtime import RuntimeConfigError
    authority, _, _ = cli_world
    monkeypatch.setattr(renewal_entry, 'load_runtime_config', lambda _: SimpleNamespace(viewer='owner'))
    monkeypatch.setattr(renewal_entry, 'CredentialStore', lambda **_: authority.credentials)
    monkeypatch.setattr(renewal_entry.socket, 'gethostname', lambda: 'synthetic')
    arguments = ['status', '--checkout', authority.checkout, '--local', str(authority.local),
                 '--repository', 'example/project']
    assert renewal_entry.main(arguments) == 0
    def invalid(_):
        raise RuntimeConfigError('PRIVATE_CANARY')
    monkeypatch.setattr(renewal_entry, 'load_runtime_config', invalid)
    assert renewal_entry.main(arguments) == 2
    assert 'PRIVATE_CANARY' not in capsys.readouterr().err


def test_AR20_missing_inventory_and_unreviewed_initial_enrollment_hold(cli_world):
    authority, _, args = cli_world
    args.command = 'enroll'
    with pytest.raises(RenewalError):
        renewal_entry._execute(args, __import__('pathlib').Path(authority.checkout), authority)
    args.command = 'proposal'
    with pytest.raises(RenewalError):
        renewal_entry._execute(args, __import__('pathlib').Path(authority.checkout), authority)


def test_AR23_review_uses_preserved_bytes_for_inactive_policy(cli_world, monkeypatch, tmp_path):
    authority, _, _ = cli_world
    monkeypatch.setattr(authority, 'review_evaluator', lambda _: tmp_path)
    with pytest.raises(RenewalError):
        renewal_entry._review_runner(authority)
    monkeypatch.setattr(authority, 'review_evaluator', lambda _: __import__('pathlib').Path(renewal_entry.__file__).parents[2])
    renewal_entry._review_runner(authority)


def test_AR08_gate_timeout_and_recovery_source_check_are_bounded(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace
    monkeypatch.setattr(renewal_entry, "Workspace", lambda _: SimpleNamespace(git=lambda *_: "a" * 40))
    def timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired('make', 1800)
    with pytest.raises(RenewalError):
        renewal_entry.local_gates(tmp_path, runner=timeout)
    monkeypatch.setattr(renewal_entry, 'current', lambda *_: {'exact': True})
    seen = []
    monkeypatch.setattr(renewal_entry, 'base_verifier', lambda *_: seen.append(True))
    renewal_entry._recovery_check(tmp_path, {}, {'exact': True}, None)
    assert seen == [True]


@pytest.fixture
def adoption_cli(cli_world, monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace
    import yaml
    from catchup_core.renewal_authority import Authority
    from catchup_core.renewal_store import encoded
    from lib.credentials import CredentialStore
    from test_renewal_authority import Keyring
    old, workspace, args = cli_world
    root = tmp_path / 'initial-checkout'
    (root / 'config').mkdir(parents=True)
    (root / 'scripts').mkdir()
    (root / 'scripts/policy.py').write_bytes(b'synthetic evaluator')
    (root / 'scripts/presentation.py').write_bytes(b'presentation a')
    policy = old.policy()
    (root / 'config/catchup_core.yaml').write_text(yaml.safe_dump({'viewer': 'owner', 'release': policy['base_release']}))
    local = tmp_path / 'initial-local'
    authority_fixtures.private_directory(local)
    authority = Authority(local, old.binding, credentials=CredentialStore(local=local, backend=Keyring()),
                          checkout=root, now=old.now)
    document = local / 'policy.json'
    document.write_bytes(encoded(policy))
    args.command, args.document, args.reviewed = 'enroll', document, __import__('catchup_core.renewal_store', fromlist=['digest']).digest(policy)
    def load(path):
        data = yaml.safe_load(path.read_text())
        return SimpleNamespace(policy=data, viewer=data['viewer'], domains=tuple(data['release']['domains']))
    monkeypatch.setattr(renewal_entry, 'load_runtime_config', load)
    monkeypatch.setattr(renewal_entry, 'context', lambda *_: policy['context'])
    monkeypatch.setattr(renewal_entry, 'current', lambda *_: {'source': policy['base_source'],
                        'cohort': policy['base_cohort'], 'tree': 'c' * 40, 'context': policy['context'], 'authority': '0' * 64})
    monkeypatch.setattr(renewal_entry, 'base_verifier', lambda *_: None)
    workspace.source = lambda *_: policy['base_source']
    monkeypatch.setattr(renewal_entry, 'evaluation_cohort', lambda *_a, **_kw: policy['base_cohort'])
    return authority, root, args, policy


def test_AR20_initial_cli_enrollment_attachment_retry_and_explicit_replacement(adoption_cli, monkeypatch, capsys):
    from catchup_core.renewal_store import digest, encoded
    authority, root, args, policy = adoption_cli
    assert renewal_entry._execute(args, root, authority) == 0
    assert authority._state()[0]['epoch'] == 1
    args.command = 'attach'
    assert renewal_entry._execute(args, root, authority) == 0
    # An exact replacement still has to be reviewed by the prior preserved evaluator.
    monkeypatch.setattr(renewal_entry, '_review_runner', lambda _: None)
    replacement = {**policy, 'expires_at': '2027-01-01T00:00:00+00:00'}
    args.document.write_bytes(encoded(replacement))
    args.command, args.reviewed = 'replace', digest(replacement)
    assert renewal_entry._execute(args, root, authority) == 0
    assert authority._state()[0]['epoch'] == 2
    assert 'enrolled' in capsys.readouterr().out


def test_AR20_proposal_is_private_and_baseline_drift_prevents_enrollment(adoption_cli, monkeypatch):
    from catchup_core.renewal_store import encoded
    authority, root, args, policy = adoption_cli
    monkeypatch.setattr(renewal_entry, 'current', lambda *_: {'source': {}, 'cohort': {}})
    with pytest.raises(RenewalError, match='baseline changed'):
        renewal_entry._execute(args, root, authority)
    assert authority.credentials.read_versioned(authority.name)[0] is None
    args.command = 'proposal'
    args.output = authority.local / 'proposal.json'
    inventory = {name: policy[name] for name in ('routine', 'protected', 'dependencies', 'gates')}
    args.document.write_bytes(encoded(inventory))
    assert renewal_entry._execute(args, root, authority) == 0
    assert renewal_entry._load(args.output)['base_release'] == policy['base_release']
    with pytest.raises(RenewalError):
        renewal_entry.proposal(root, {}, authority)


def test_AR23_isolated_interpreter_ignores_candidate_startup_code(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    canary = tmp_path / 'executed'
    (tmp_path / 'sitecustomize.py').write_text('from pathlib import Path\nPath(' + repr(str(canary)) + ').touch()\n')
    tool = Path(renewal_entry.__file__).parents[2] / 'tools/release_renewal.py'
    child = subprocess.run([sys.executable, '-I', str(tool), '--help'], capture_output=True, timeout=15,
                            env={**os.environ, 'PYTHONPATH': str(tmp_path)})
    assert child.returncode == 0 and not canary.exists()
    if not sys.flags.isolated:
        with pytest.raises(RenewalError):
            renewal_entry._isolated_evaluator()


@pytest.fixture
def pending_cli(cli_world, monkeypatch):
    from catchup_core.renewal_delivery import Delivery
    from test_renewal_authority import approve, target
    authority, workspace, args = cli_world
    def crash(event):
        if event == 'intent':
            raise RuntimeError('synthetic process death')
    authority.observe = crash
    engine = Delivery(authority, workspace, snapshot=target, gates=lambda: None,
                      ci=lambda *_: {'status': 'passed', 'requests': 3}, validate_base=lambda _: None)
    with pytest.raises(RuntimeError):
        engine.run(approve(authority), message='Synthetic')
    authority.observe = lambda _: None
    args.command = 'recover'
    monkeypatch.setattr(renewal_entry, 'base_verifier', lambda *_: None)
    return authority, workspace, args


@pytest.mark.parametrize('status', ['passed', 'pending', 'held'])
def test_AR09_pending_cli_recovery_rechecks_ci_outside_publication_lock(pending_cli, monkeypatch, status):
    from pathlib import Path
    authority, workspace, args = pending_cli
    def collect(*_a, **_kw):
        with authority.locked():
            pass
        return {'status': status, 'requests': 3}, ()
    monkeypatch.setattr(renewal_entry, 'collect', collect)
    if status == 'passed':
        assert renewal_entry._operate(args, Path(authority.checkout), authority, workspace) == 0
        assert authority.active() is not None
        assert authority.progress()['ci_requests'] == 6
    else:
        with pytest.raises(RenewalError):
            renewal_entry._operate(args, Path(authority.checkout), authority, workspace)
        assert authority.pending() is not None


@pytest.mark.parametrize('failure', ['superseded', 'budget', 'accounting', 'missing-journal', 'stale'])
def test_AR07_recovery_refuses_superseded_stale_or_exhausted_attempt(pending_cli, monkeypatch, failure):
    from datetime import timedelta
    from pathlib import Path
    authority, workspace, args = pending_cli
    if failure == 'superseded':
        workspace.tip = 'f' * 40
    elif failure == 'budget':
        authority.record({**authority.progress(), 'ci_requests': 30})
    elif failure == 'missing-journal':
        monkeypatch.setattr(authority, 'progress', lambda: None)
    elif failure == 'stale':
        previous = authority.now()
        authority.now = lambda: previous + timedelta(days=2)
    monkeypatch.setattr(renewal_entry, 'collect', lambda *_a, **_kw:
                        ({'status': 'passed', 'requests': 'invalid' if failure == 'accounting' else 3}, ()))
    with pytest.raises(RenewalError):
        renewal_entry._operate(args, Path(authority.checkout), authority, workspace)
    assert authority.pending() is not None


def test_AR20_attachment_refuses_changed_baseline_and_wrong_approval(adoption_cli, monkeypatch):
    import yaml
    from catchup_core.renewal_store import digest
    authority, root, args, policy = adoption_cli
    renewal_entry._execute(args, root, authority)
    monkeypatch.setattr(renewal_entry, 'current', lambda *_: {'source': {}, 'cohort': {}})
    with pytest.raises(RenewalError):
        renewal_entry.attach(root, authority, digest(policy))
    monkeypatch.setattr(renewal_entry, 'current', lambda *_: {'source': policy['base_source'], 'cohort': policy['base_cohort']})
    path = root / 'config/catchup_core.yaml'
    changed = yaml.safe_load(path.read_text())
    changed['release']['owner_approved_at'] = 'forged replacement'
    path.write_text(yaml.safe_dump(changed))
    with pytest.raises(RenewalError):
        renewal_entry.attach(root, authority, digest(policy))


def test_AR25_attachment_readback_and_generation_races_hold(adoption_cli, monkeypatch):
    import yaml
    from catchup_core.renewal_store import digest
    authority, root, args, policy = adoption_cli
    renewal_entry._execute(args, root, authority)
    path = root / 'config/catchup_core.yaml'
    data = yaml.safe_load(path.read_text())
    data['release'].pop('renewal')
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(renewal_entry, 'atomic_bytes', lambda *_: None)
    with pytest.raises(RenewalError, match='readback'):
        renewal_entry.attach(root, authority, digest(policy))
    original = authority._policy
    count = [0]
    def drift(state):
        value = original(state)
        count[0] += 1
        return value if count[0] == 1 else {**value, 'context': 'f' * 64}
    monkeypatch.setattr(authority, '_policy', drift)
    with pytest.raises(RenewalError, match='inputs changed'):
        renewal_entry.attach(root, authority, digest(policy))
