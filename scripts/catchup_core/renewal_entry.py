"""Reviewed adoption and foreground delivery front end; no background work or live auto-enrollment."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from catchup_core.backup import atomic_bytes
from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.ledger import PermissionDenied
from catchup_core.policy import _validate_release_evidence, evaluation_cohort
from catchup_core.renewal_authority import Authority, _time
from catchup_core.renewal_ci import Gate
from catchup_core.renewal_delivery import Delivery, public_receipt
from catchup_core.renewal_github import collect
from catchup_core.renewal_metadata import metadata_for
from catchup_core.renewal_runner import run as session_run
from catchup_core.renewal_store import (
    MAX_BYTES,
    REASONS,
    RenewalError,
    _chain,
    decoded,
    digest,
    encoded,
    private_directory,
)
from catchup_core.renewal_workspace import Workspace, context, source_file
from catchup_core.runtime import RuntimeConfigError, load_runtime_config
from lib.credentials import CredentialError, CredentialStore


def local_gates(root: Path, *, runner: Callable[..., None] | None = None) -> dict[str, Any]:
    runner = runner or session_run
    make, ruff = shutil.which('make'), shutil.which('ruff')
    if make is None or ruff is None:
        raise RenewalError('required local gate tools unavailable')
    base = Workspace(root).git('rev-parse', 'origin/main')
    if len(base) not in (40, 64) or any(char not in '0123456789abcdef' for char in base):
        raise RenewalError('local gate comparison baseline unavailable')
    python = shlex.quote(sys.executable)
    try:
        runner([make, 'check', f'PYTHON={python}', f'PYTEST={python} -m pytest', f'RUFF={shlex.quote(ruff)}',
                'COV_MIN=85', 'COV_BRANCH_MIN=85', f'DIFF_BASE={base}', 'DIFF_RANGE_NOTATION=...'], root, timeout=1800)
        runner([sys.executable, str(root / 'tools/check_diff_coverage.py'), str(root / 'coverage.xml'),
                f'--compare-branch={base}', '--include=scripts/catchup_core/*', '--fail-under=95'], root, timeout=60)
    except (OSError, subprocess.TimeoutExpired, RenewalError):
        raise RenewalError('local delivery gates held', reason='gates') from None
    return {'schema': 'artha.local-gates.v1', 'base_commit': base, 'checks': ['make-check', 'changed-lines-95']}


def current(root: Path, policy: dict[str, Any], authority: Authority) -> dict[str, Any]:
    config = load_runtime_config(root / 'config' / 'catchup_core.yaml')
    workspace = Workspace(root)
    source = workspace.source(tuple(file['path'] for file in policy['base_source']['files']))
    tree = workspace.tree()
    workspace.assert_tree(source, tree)
    return {'source': source,
            'cohort': evaluation_cohort(config.policy, policy['domains'], workspace=root, source_root=root),
            'tree': tree, 'context': context(root), 'authority': metadata_for(authority).snapshot()}


def base_verifier(root: Path, policy: dict[str, Any]) -> None:
    config = load_runtime_config(root / 'config' / 'catchup_core.yaml')
    release = {key: value for key, value in config.policy.get('release', {}).items() if key != 'renewal'}
    if release != policy['base_release'] or config.viewer != policy['binding']['owner']:
        raise RenewalError('release baseline approval changed')
    data = {**config.policy, 'release': release}
    request = CatchupRequest('renewal-check', config.viewer, 'cli', mode=ExecutionMode.AUTHORITATIVE,
                             host=socket.gethostname().split('.')[0])
    _validate_release_evidence(request, data, domains=policy['domains'], cohort=policy['base_cohort'])
    # This is the incumbent metadata/checksum validator (cipher=None internally),
    # never the runtime guard that decrypts every checkpoint member.


def proposal(root: Path, inventory: dict[str, Any], authority: Authority) -> dict[str, Any]:
    if set(inventory) != {'routine', 'protected', 'dependencies', 'gates'}:
        raise RenewalError('reviewed dependency and gate inventory required')
    config = load_runtime_config(root / 'config' / 'catchup_core.yaml')
    source = Workspace(root).source()
    release = config.policy.get('release')
    if not isinstance(release, dict):
        raise RenewalError('first adoption requires an exact original release')
    release = {key: value for key, value in release.items() if key != 'renewal'}
    evaluator = {file['path']: file['sha256'] for file in source['files']
                 if file['path'].startswith('scripts/') or file['path'] == 'tools/release_renewal.py'}
    result = {'schema': 'artha.renewal-delegation.v1', 'binding': authority.binding,
              'base_source': source, 'base_cohort': evaluation_cohort(config.policy, config.domains,
                                                                    workspace=root, source_root=root),
              'base_release': release, 'context': context(root), 'expires_at': None,
              'domains': list(config.domains), 'evaluator': evaluator, **inventory}
    authority._check_policy(result)
    metadata_for(authority).snapshot()
    base_verifier(root, result)
    return result


def _load(path: Path) -> dict[str, Any]:
    with path.open('rb') as handle:
        return decoded(handle.read(MAX_BYTES + 1))


def _private_write(path: Path, value: dict[str, Any], local: Path) -> None:
    if not path.absolute().is_relative_to(local.absolute()):
        raise RenewalError('private proposal must stay under machine-local release storage')
    _chain(path)
    private_directory(local, create=True)
    private_directory(path.parent, create=True)
    with path.open('xb') as handle:
        os.chmod(path, 0o600)
        handle.write(encoded(value))
        handle.flush()
        os.fsync(handle.fileno())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('proposal', 'enroll', 'replace', 'attach', 'plan', 'approve', 'deliver',
                                           'status', 'recover', 'revoke'))
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--local', type=Path, default=Path.home() / '.artha-local' / 'catchup_core')
    parser.add_argument('--repository', default='vedprakash-m/artha')
    parser.add_argument('--document', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--reviewed', default='')
    parser.add_argument('--approval', default='')
    parser.add_argument('--message', default='Validated Artha delivery')
    parser.add_argument('--quiescent', action='store_true', help='owner confirmed other source writers are stopped')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.checkout.absolute()
        config = load_runtime_config(root / 'config' / 'catchup_core.yaml')
        binding = {'owner': config.viewer, 'host': socket.gethostname().split('.')[0],
                   'repository': args.repository, 'profile': 'personal'}
        authority = Authority(args.local, binding, credentials=CredentialStore(local=args.local), checkout=root)
        return _execute(args, root, authority)
    except RenewalError as error:
        detail = REASONS[error.reason]
    except CredentialError:
        detail = REASONS['authority']
    except PermissionDenied:
        detail = 'approved release evidence is unavailable or invalid; review the baseline and its access'
    except RuntimeConfigError:
        detail = 'release configuration is unavailable or invalid; inspect the local release configuration'
    except OSError:
        detail = 'required local release input unavailable; check local access and retry'
    except ValueError:
        detail = 'release input is invalid; review the requested operation and approved evidence'
    print(f'delivery held: {detail}', file=sys.stderr)
    return 2


def _execute(args: argparse.Namespace, root: Path, authority: Authority) -> int:
    workspace = _delivery_context(args, root, authority)
    if args.command == 'proposal':
        if args.document is None or args.output is None:
            raise RenewalError('proposal needs inventory and private output')
        result = proposal(root, _load(args.document), authority)
        _private_write(args.output, result, authority.local)
        print('review exact delegation:', digest(result))
    elif args.command in {'enroll', 'replace'}:
        if args.document is None:
            raise RenewalError('enrollment needs reviewed policy')
        policy = _load(args.document)
        candidate = current(root, policy, authority)
        if (candidate['source'] != policy['base_source'] or candidate['cohort'] != policy['base_cohort']
                or context(root) != policy['context']):
            raise RenewalError('baseline changed after proposal')
        evaluator = {name: (root / name).read_bytes() for name in policy['evaluator']}
        operation = authority.enroll if args.command == 'enroll' else authority.replace
        operation(policy, expected_digest=args.reviewed, reviewed=bool(args.reviewed),
                  validate_base=lambda p: base_verifier(root, p), evaluator=evaluator)
        directory = authority.evaluator(authority.local / 'release-evaluator' / digest(policy))
        attach(root, authority, args.reviewed)
        print('enrolled; use the preserved evaluator at', directory / 'tools' / 'release_renewal.py')
    elif args.command == 'attach':
        attach(root, authority, args.reviewed)
        print('release renewal pointer verified; first validated delivery still required')
    elif args.command == 'revoke':
        authority.revoke()
        print('release delegation revoked')
    elif args.command == 'status':
        print(public_receipt(authority.progress() or {'stage': 'ENROLLED'}))
    else:
        return _operate(args, root, authority, workspace)
    return 0


def _delivery_context(args: argparse.Namespace, root: Path, authority: Authority) -> Workspace:
    workspace = Workspace(root)
    workspace.remote(authority.binding['repository'])
    effects = {'proposal', 'enroll', 'replace', 'attach', 'approve', 'deliver', 'recover'}
    if args.command in effects and not args.quiescent:
        raise RenewalError('owner-confirmed source writer quiescence required')
    if args.command in effects:
        _isolated_evaluator()
    if args.command in {'proposal', 'enroll'}:
        existing, _ = authority.credentials.read_versioned(authority.name)
        configured = load_runtime_config(root / 'config' / 'catchup_core.yaml').policy
        if existing is None and 'renewal' in configured.get('release', {}):
            raise RenewalError('missing renewal authority requires continuity recovery review')
    if args.command in {'proposal', 'replace'}:
        existing, _ = authority.credentials.read_versioned(authority.name)
        if existing is not None:
            _review_runner(authority)
    return workspace


def _review_runner(authority: Authority) -> None:
    _isolated_evaluator()
    policy = authority.review_policy()
    directory = authority.review_evaluator(authority.local / 'release-evaluator' / digest(policy))
    if Path(__file__).resolve().parents[2] != directory:
        raise RenewalError('replacement review requires the preserved approved evaluator')


def attach(root: Path, authority: Authority, reviewed: str) -> None:
    """One-time configuration effect named in adoption, with deterministic retry after interruption."""
    policy = authority.policy()
    if digest(policy) != reviewed:
        raise RenewalError('exact reviewed delegation required for attachment')
    candidate = current(root, policy, authority)
    if candidate['source'] != policy['base_source'] or candidate['cohort'] != policy['base_cohort']:
        raise RenewalError('attachment requires the exact approved baseline')
    path = root / 'config' / 'catchup_core.yaml'
    before = source_file(path, 'config/catchup_core.yaml')
    data = load_runtime_config(path).policy
    release = data.get('release', {})
    if {key: value for key, value in release.items() if key != 'renewal'} != policy['base_release']:
        raise RenewalError('attachment cannot replace an approval')
    pointer = {'schema': 'artha.release.renewal.v1', 'delegation': reviewed}
    with authority.locked():
        state, _ = authority._state()
        if authority._policy(state) != policy or source_file(path, 'config/catchup_core.yaml') != before:
            raise RenewalError('attachment inputs changed')
        if release.get('renewal') != pointer:
            data = {**data, 'release': {**release, 'renewal': pointer}}
            atomic_bytes(path, yaml.safe_dump(data, sort_keys=False).encode('utf-8'))
        if load_runtime_config(path).policy.get('release', {}).get('renewal') != pointer:
            raise RenewalError('attachment readback failed')
        current_state, _ = authority._state()
        if current_state != state:
            raise RenewalError('attachment native generation changed')


def _operate(args: argparse.Namespace, root: Path, authority: Authority, workspace: Workspace) -> int:
    _isolated_evaluator()
    policy = authority.policy()
    # The owner-selected frozen executable is mandatory after enrollment. Current
    # checkout entry points cannot act as a replacement evaluator for their own diff.
    running = Path(__file__).resolve().parents[2]
    expected = authority.evaluator(authority.local / 'release-evaluator' / digest(policy))
    if running != expected:
        raise RenewalError('use the preserved approved evaluator entry point')
    target = current(root, policy, authority)
    if args.command == 'plan':
        if args.output is None:
            raise RenewalError('plan needs private output')
        _private_write(args.output, target, authority.local)
        print('review exact task:', digest(target))
    elif args.command == 'approve':
        if args.document is None or _load(args.document) != target:
            raise RenewalError('task candidate changed since review')
        print('task approval:', authority.approve(target, expected_digest=args.reviewed,
                                                  reviewed=bool(args.reviewed)))
    elif args.command == 'recover':
        _recover_delivery(root, policy, authority, workspace)
        print('release publication recovery verified')
    else:
        gates = tuple(Gate(item['workflow'], item['definition_sha256'], tuple(item['jobs']))
                      for item in policy['gates'])
        engine = Delivery(authority, workspace, snapshot=lambda: current(root, policy, authority),
                          gates=lambda: local_gates(root),
                          ci=lambda commit, _, budget: collect(workspace, authority.binding['repository'],
                                                               commit, gates, now=authority.now(),
                                                               request_budget=budget, clock=authority.now)[0],
                          validate_base=lambda p: base_verifier(root, p))
        receipt = engine.run(args.approval or authority.routine(target), message=args.message)
        print(public_receipt(receipt))
        return 0 if receipt['status'] == 'VERIFIED' else 2
    return 0


def _isolated_evaluator() -> None:
    if not sys.flags.isolated:
        raise RenewalError('launch the approved evaluator with Python -I')


def _recover_delivery(root: Path, policy: dict[str, Any], authority: Authority, workspace: Workspace) -> None:
    pending = authority.pending()
    if pending is not None:
        # Provider and remote checks occur before acquiring the publication lock.
        commit = pending['evidence']['commit']
        if workspace.remote_tip() != commit:
            raise RenewalError('recovery delivery was superseded')
        receipt = authority.progress()
        if receipt is None or receipt.get('attempt') != pending['approval']:
            raise RenewalError('recovery delivery provenance is unavailable')
        spent = receipt.get('ci_requests', 0)
        if type(spent) is not int or not 0 <= spent < 30:
            raise RenewalError('recovery CI request budget exhausted')
        gates = tuple(Gate(item['workflow'], item['definition_sha256'], tuple(item['jobs']))
                      for item in policy['gates'])
        authority.record({**receipt, 'ci_requests': 30})
        proof, _ = collect(workspace, authority.binding['repository'], commit, gates,
                           now=authority.now(), request_budget=30 - spent, clock=authority.now)
        requests = proof['requests']
        if type(requests) is not int or not 0 < requests <= 30 - spent:
            raise RenewalError('recovery CI request accounting is invalid')
        authority.record({**receipt, 'ci_requests': spent + requests})
        age = (authority.now() - _time(pending['evidence']['local']['at'])).total_seconds()
        if proof['status'] != 'passed' or not 0 <= age <= 86400:
            raise RenewalError('recovery delivery gates are pending, stale or held')
    authority.recover(verify=lambda value, _: _recovery_check(root, policy, value, authority))


def _recovery_check(root: Path, policy: dict[str, Any], target: dict[str, Any], authority: Authority) -> None:
    if current(root, policy, authority) != target:
        raise RenewalError('recovery target source changed')
    base_verifier(root, policy)
