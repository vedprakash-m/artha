"""Synthetic end-to-end adoption/delivery with real Git and real release evidence validation.

Only native credentials and GitHub responses are replaced. Historical observations
are explicitly fabricated test fixtures, never live host acceptance.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from catchup_core import policy, renewal_entry, renewal_github, renewal_runtime
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
from catchup_core.ledger import PermissionDenied, Ledger, KeyringSealedStore
from catchup_core.renewal_metadata import AuthorityMetadata
from test_user_surfaces import KEY
from catchup_core.renewal_authority import Authority
from catchup_core.renewal_ci import Gate
from catchup_core.renewal_delivery import Delivery
from catchup_core.renewal_store import RenewalError, digest
from catchup_core.renewal_workspace import Workspace
from lib.credentials import CredentialStore
import test_cutover_fence as cutover_fixtures
configured = cutover_fixtures.configured
from test_renewal_authority import Keyring
from test_renewal_workspace import git


def _copy_listed_scripts(original: Path, root: Path) -> None:
    # Copy only files Git would list, so gitignored local-only files (e.g. scripts/work/)
    # never enter the synthetic release. Without .git (isolated runner copy) the tree is pre-filtered.
    if not (original / '.git').exists():
        shutil.copytree(original / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        return
    listed = git(original, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', 'scripts')
    for name in filter(None, listed.split('\0')):
        source = original / name
        if '__pycache__' in source.parts or not source.is_file():
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


@pytest.mark.parametrize('approval_mode', ['routine', 'task_only'])
def test_AR01_real_git_delivery_actual_release_reader_and_protected_negative(
        configured, tmp_path, monkeypatch, approval_mode):
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    config, request, keyring, _ = configured
    root = tmp_path / 'repo'
    original = Path(__file__).parents[2]
    _copy_listed_scripts(original, root)
    (root / '.github/workflows').mkdir(parents=True)
    workflow = b'name: Synthetic\non: push\njobs: {}\n'
    (root / '.github/workflows/ci.yml').write_bytes(workflow)
    (root / 'config/catchup_core.yaml').write_text(yaml.safe_dump(config))
    (root / '.gitignore').write_text('config/catchup_core.yaml\n')
    git(root, 'init', '-b', 'main')
    for name, value in (('user.name', 'Synthetic'), ('user.email', 'synthetic@example.invalid'),
                        ('commit.gpgsign', 'false'), ('core.hooksPath', str(tmp_path / 'empty-hooks'))):
        git(root, 'config', name, value)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'synthetic baseline')
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--bare', '-b', 'main', str(remote))
    git(root, 'remote', 'add', 'origin', str(remote))
    git(root, 'push', 'origin', 'main')

    class SyntheticRemote(Workspace):
        def remote(self, repository):
            assert repository == 'example/project'
            assert self.git('remote', 'get-url', 'origin') == str(remote)

    workspace = SyntheticRemote(root)
    local = tmp_path / 'authority-local'
    authority = Authority(local, {'owner': 'owner', 'host': 'authority', 'repository': 'example/project',
                                  'profile': 'personal'}, credentials=CredentialStore(local=local, backend=Keyring()),
                          checkout=root)
    authority.credentials._backend.store.update(keyring.store)
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring))
    sealed = KeyringSealedStore(authority.credentials._backend, 'authoritative', cipher=cipher,
                                history_root=tmp_path / 'privacy')
    local.mkdir(mode=0o700, exist_ok=True)
    with Ledger(local / 'ledger.db', id_key=KEY, cipher=cipher, sealed=sealed) as live:
        live.migrate()
        live.ensure_authority_host('authority')
        live.seal_privacy()
    authority._metadata = AuthorityMetadata(local, authority.credentials._backend, 'authority', cipher=cipher,
                                            history_root=tmp_path / 'privacy')
    source = workspace.source()
    routine = 'scripts/catchup_core/compose.py'
    protected = [entry['path'] for entry in source['files'] if entry['path'] != routine]
    inventory = {'routine': [routine] if approval_mode == 'routine' else [], 'protected': protected,
                 'dependencies': {'synthetic-reuse-rule': protected},
                 'gates': [{'workflow': '.github/workflows/ci.yml',
                            'definition_sha256': hashlib.sha256(workflow).hexdigest(), 'jobs': ['synthetic test']} ]}
    delegation = renewal_entry.proposal(root, inventory, authority)
    baseline = {name: (root / name).read_bytes() for name in delegation['evaluator']}
    authority.enroll(delegation, expected_digest=digest(delegation), reviewed=True,
                     validate_base=lambda p: renewal_entry.base_verifier(root, p), evaluator=baseline)
    assert authority.evaluator(tmp_path / 'preserved-evaluator').is_dir()
    renewal_entry.attach(root, authority, digest(delegation))
    with (root / routine).open('a') as handle:
        handle.write('\n# Synthetic candidate for exact-byte publication testing.\n')
    git(root, 'add', '.')
    target = renewal_entry.current(root, delegation, authority)
    assert target['cohort']['implementation'] != delegation['base_cohort']['implementation']
    if approval_mode == 'routine':
        approval = authority.routine(target)
    else:
        with pytest.raises(RenewalError):
            authority.routine(target)
        document = local / 'synthetic-task.json'
        renewal_entry._private_write(document, target, local)
        reviewed = digest(renewal_entry._load(document))
        with pytest.raises(RenewalError):
            authority.approve(target, expected_digest='0' * 64, reviewed=True)
        approval = authority.approve(target, expected_digest=reviewed, reviewed=True)
    calls = []
    def ci(commit, _policy, budget):
        created = authority.now().isoformat()
        run = {'id': 1, 'path': '.github/workflows/ci.yml', 'head_sha': commit, 'head_branch': 'main',
               'head_repository': {'full_name': 'example/project'}, 'run_attempt': 1, 'event': 'push',
               'created_at': created, 'status': 'completed', 'conclusion': 'success'}
        def response(path, **kwargs):
            if '/jobs' in path:
                return {'total_count': 1, 'jobs': [{'run_id': 1, 'name': 'synthetic test', 'status': 'completed',
                                                   'conclusion': 'success'}]}
            return run if '/attempts/' in path else {'total_count': 1, 'workflow_runs': [run]}
        monkeypatch.setattr(renewal_github, '_object', response)
        return renewal_github.collect(workspace, 'example/project', commit,
                     (Gate('.github/workflows/ci.yml', hashlib.sha256(workflow).hexdigest(), ('synthetic test',)),),
                     now=authority.now(), request_budget=budget)[0]
    delivery = Delivery(authority, workspace, snapshot=lambda: renewal_entry.current(root, delegation, authority),
                        gates=lambda: calls.append('synthetic-local-gate'), ci=ci,
                        validate_base=lambda p: renewal_entry.base_verifier(root, p))
    result = delivery.run(approval, message='synthetic candidate delivery')
    assert result['status'] == 'VERIFIED', result
    assert workspace.remote_tip() == result['commit']
    assert delivery.run(approval, message='repeat') == result
    assert calls == ['synthetic-local-gate']
    config['release']['renewal'] = {'schema': 'artha.release.renewal.v1', 'delegation': digest(delegation)}
    monkeypatch.setattr(renewal_runtime, 'authority_for', lambda _: authority)
    policy.validate_release(request, config, viewer='owner', domains=['kids_school'], cohort=target['cohort'])
    guard = policy.ReleaseEvidenceGuard(config, EnvelopeCipher(KeyringKeyProvider('artha-ledger', backend=keyring)),
                                        target['cohort'])
    guard.validate()
    protected_file = root / 'scripts/catchup_core/renewal_authority.py'
    protected_file.write_bytes(protected_file.read_bytes() + b'\n# synthetic forbidden modification\n')
    with pytest.raises(PermissionDenied):
        policy.validate_release(request, config, viewer='owner', domains=['kids_school'], cohort=target['cohort'])
    # The immutable historical approval was never repinned or retimestamped.
    stored = json.loads(Path(config['release']['scorecard']['path']).read_bytes())
    assert stored['cohort'] == delegation['base_cohort']
