"""Native-anchored release decisions. Reviewed adoption and task approval are explicit effects.

Artifacts are immutable and encrypted; the OS credential head is the sole publication
pointer and intent journal. Candidate data never selects the credential service or key.
"""
from __future__ import annotations

import base64
import secrets
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from catchup_core.renewal_ci import Gate, verify_ci
from catchup_core.renewal_custody import preserve, restore
from catchup_core.renewal_inputs import SourceManifest, compare_sources
from catchup_core.renewal_store import ArtifactStore, RenewalError, digest, encoded, is_digest
from catchup_core.session_lock import SessionBusy, installation_fence, require_active_installation, session_lock
from lib.credentials import CredentialError, CredentialStore

_COHORT = frozenset({'implementation', 'configuration', 'prompts', 'packs', 'model', 'contract'})
_FIELDS = frozenset({'schema', 'binding', 'base_source', 'base_cohort', 'base_release', 'protected',
                     'dependencies', 'context', 'expires_at', 'gates', 'domains', 'routine', 'evaluator'})
_BOOTSTRAP = frozenset({'scripts/catchup_core/policy.py', 'scripts/catchup_core/runtime.py',
                       'scripts/catchup_core/crypto.py', 'scripts/catchup_core/session_lock.py',
                       'scripts/catchup_core/ledger.py', 'scripts/catchup_core/privacy_history.py',
                       'scripts/catchup_core/schema_history.py', 'scripts/catchup_core/fact_access.py',
                       'scripts/lib/credentials.py', 'scripts/sitecustomize.py', 'scripts/usercustomize.py',
                       'tools/release_renewal.py', 'Makefile', 'pyproject.toml', '.gitattributes', '.gitignore',
                       '.github/copilot-instructions.md', 'config/workflow/development.md'})


def _cohort(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == _COHORT
            and all(isinstance(part, str) and 0 < len(part) <= 128 for part in value.values())
            and all(is_digest(part) for name, part in value.items() if name != 'contract')
            and value.get('model') != 'unverified')


def _time(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError):
        raise RenewalError('release time is invalid') from None


class Authority:
    def __init__(self, local: Path, binding: Mapping[str, str], *, credentials: CredentialStore,
                 now: Callable[[], datetime] | None = None, observe: Callable[[str], None] | None = None,
                 checkout: Path):
        self.local = local.absolute()
        self.checkout = str(checkout.absolute())
        if (self.local.is_relative_to(checkout.absolute()) or checkout.absolute().is_relative_to(self.local)
                or any(part.casefold() == 'cloudstorage' or part.casefold().startswith('onedrive')
                       for part in self.local.parts)):
            raise RenewalError('release authority must stay outside the checkout and cloud storage')
        self.binding = dict(binding)
        if (set(self.binding) != {'owner', 'host', 'repository', 'profile'}
                or any(not isinstance(v, str) or not v or len(v) > 512 for v in self.binding.values())
                or self.binding['profile'] not in {'personal', 'work'}):
            raise RenewalError('release installation binding is invalid')
        self.credentials = credentials
        self.name = 'work.release.renewal' if self.binding['profile'] == 'work' else 'release.renewal'
        self.root = self.local / 'release-renewal' / self.binding['profile']
        self.now = now or (lambda: datetime.now(UTC))
        self.observe = observe or (lambda _: None)
        self._metadata: object | None = None
        self._lineage_head: tuple[Any, ...] = ()
        self._lineage_files: dict[str, tuple[int, ...]] = {}

    @contextmanager
    def locked(self) -> Iterator[None]:
        try:
            require_active_installation(self.local)
            with installation_fence(self.local), session_lock(self.local / 'release-renewal.lock'):
                yield
        except InterruptedError:
            raise
        except (CredentialError, SessionBusy, OSError):
            raise RenewalError('release native authority is unavailable or busy') from None

    def _state(self) -> tuple[dict[str, Any], str]:
        state, revision = self.credentials.read_versioned(self.name)
        if (not isinstance(state, dict) or set(state) != {'schema', 'binding', 'local', 'checkout', 'secret', 'epoch',
                                                       'serial', 'policy', 'active', 'pending', 'phase',
                                                       'attempt', 'custody', 'predecessor', 'adopted_at'}
                or state['schema'] != 'artha.renewal-head.v1' or state['binding'] != self.binding
                or state['local'] != str(self.local) or state['checkout'] != self.checkout
                or type(state['epoch']) is not int or state['epoch'] < 1
                or type(state['serial']) is not int or not 0 <= state['serial'] <= 32
                or not isinstance(state['phase'], str)
                or state['phase'] not in {'enrolling', 'ready', 'prepared', 'revoked'}
                or not is_digest(state['policy'])
                or any(not isinstance(value, str) or (value and not is_digest(value))
                       for value in (state['active'], state['pending'], state['attempt'], state['custody'],
                                     state['predecessor']))):
            raise RenewalError('release native authority is missing or invalid')
        _time(state['adopted_at'])
        if ((state['phase'] == 'prepared' and not state['pending'])
                or (state['phase'] == 'ready' and state['pending'])
                or (state['phase'] == 'ready' and not state['custody'])):
            raise RenewalError('release native generation is unresolved')
        return state, revision

    def _store(self, state: dict[str, Any]) -> ArtifactStore:
        try:
            key = base64.b64decode(state['secret'], validate=True)
            if len(key) != 32:
                raise ValueError
        except (ValueError, TypeError):
            raise RenewalError('release native key is invalid') from None
        return ArtifactStore(self.root, key)

    def _save(self, state: dict[str, Any], revision: str) -> None:
        self.credentials.write(self.name, state, expected_revision=revision)
        saved, _ = self._state()
        if saved != state:
            raise RenewalError('release native publication readback failed')

    def _policy(self, state: dict[str, Any]) -> dict[str, Any]:
        if state['phase'] in {'revoked', 'enrolling'}:
            raise RenewalError('release delegation is not active')
        policy = self._store(state).read(state['policy'])
        self._check_policy(policy)
        if policy['expires_at'] is not None and self.now() >= _time(policy['expires_at']):
            raise RenewalError('release delegation expired')
        return policy

    def _check_policy(self, policy: dict[str, Any]) -> None:
        if (set(policy) != _FIELDS or policy['schema'] != 'artha.renewal-delegation.v1'
                or policy['binding'] != self.binding or not _cohort(policy['base_cohort'])
                or not isinstance(policy['base_release'], dict) or 'renewal' in policy['base_release']
                or not isinstance(policy['domains'], list) or not policy['domains']
                or policy['domains'] != policy['base_release'].get('domains')
                or not is_digest(policy['context']) or not isinstance(policy['protected'], list)
                or not isinstance(policy['dependencies'], dict) or not policy['dependencies']
                or not isinstance(policy['gates'], list)
                or not isinstance(policy['routine'], list) or not policy['routine']
                or not isinstance(policy['evaluator'], dict) or not policy['evaluator']):
            raise RenewalError('release delegation contract is invalid')
        source = SourceManifest.parse(encoded(policy['base_source']))
        paths = {entry.path for entry in source.files}
        hashes = {entry.path: entry.sha256 for entry in source.files}
        mandatory = (paths & _BOOTSTRAP) | {path for path in paths if path.startswith(
            ('scripts/catchup_core/renewal_', '.github/workflows/', '.githooks/'))}
        if (any(not isinstance(path, str) for path in policy['protected'])
                or not set(policy['protected']) <= paths
                or not mandatory <= set(policy['protected'])
                or any(not isinstance(value, list) or not value
                       or any(not isinstance(path, str) for path in value) or not set(value) <= paths
                       for value in policy['dependencies'].values())):
            raise RenewalError('release evidence dependencies are incomplete')
        if (any(not isinstance(path, str) for path in policy['routine'])
                or not set(policy['routine']) <= paths
                or set(policy['routine']) & set(policy['protected'])
                or set(policy['routine']) & {path for values in policy['dependencies'].values() for path in values}
                or any(path not in paths or hashes[path] != value or not is_digest(value)
                       for path, value in policy['evaluator'].items())
                or policy['evaluator'] != {path: value for path, value in hashes.items()
                                           if path.startswith('scripts/') or path == 'tools/release_renewal.py'}):
            raise RenewalError('release evaluator or routine scope is invalid')
        try:
            gates = tuple(Gate(item['workflow'], item['definition_sha256'], tuple(item['jobs']))
                          for item in policy['gates'])
            verify_ci(repository=self.binding['repository'], commit='0' * 40, gates=gates, runs=(), now=self.now())
            if any(hashes.get(gate.workflow) != gate.definition_sha256
                   or gate.workflow not in policy['protected'] for gate in gates):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise RenewalError('release gate policy is invalid') from None
        if policy['expires_at'] is not None:
            _time(policy['expires_at'])

    def enroll(self, policy: dict[str, Any], *, expected_digest: str, reviewed: bool,
               validate_base: Callable[[dict[str, Any]], None],
               evaluator: Mapping[str, bytes]) -> str:
        self._check_policy(policy)
        identity = digest(policy)
        if reviewed is not True or identity != expected_digest:
            raise RenewalError('exact reviewed delegation digest required')
        if policy['expires_at'] is not None and self.now() >= _time(policy['expires_at']):
            raise RenewalError('reviewed delegation already expired')
        validate_base(policy)
        with self.locked():
            current, revision = self.credentials.read_versioned(self.name)
            if current is None:
                if revision or self.root.exists():
                    raise RenewalError('release authority recovery review required')
                state = {'schema': 'artha.renewal-head.v1', 'binding': self.binding, 'local': str(self.local),
                         'checkout': self.checkout, 'attempt': '', 'custody': '', 'predecessor': '',
                         'adopted_at': self.now().isoformat(),
                         'secret': base64.b64encode(secrets.token_bytes(32)).decode(), 'epoch': 1,
                         'serial': 0, 'policy': identity, 'active': '', 'pending': '', 'phase': 'enrolling'}
                self._save(state, revision)
            else:
                state, revision = self._state()
                if state['phase'] != 'enrolling' or state['policy'] != identity:
                    raise RenewalError('release authority already exists; explicit replacement required')
            self.observe('enroll-intent')
            store = self._store(state)
            store.put(policy)
            custody = preserve(store, evaluator, policy['evaluator'])
            state, revision = self._state()
            state['custody'] = custody
            state['phase'] = 'ready'
            self._save(state, revision)
            return identity

    def policy(self) -> dict[str, Any]:
        with self.locked():
            state, _ = self._state()
            return self._policy(state)

    def _target(self, policy: dict[str, Any], target: dict[str, Any], *, exact_task: bool = False) -> None:
        if (set(target) != {'source', 'cohort', 'tree', 'context', 'authority'} or not _cohort(target['cohort'])
                or not isinstance(target['tree'], str) or len(target['tree']) not in (40, 64)
                or any(char not in '0123456789abcdef' for char in target['tree'])
                or target['context'] != policy['context'] or not is_digest(target['authority'])
                or any(target['cohort'][name] != policy['base_cohort'][name]
                       for name in _COHORT - {'implementation'})):
            raise RenewalError('release target is outside delegated scope')
        root = SourceManifest.parse(encoded(policy['base_source']))
        current = SourceManifest.parse(encoded(target['source']))
        protected = set(policy['protected']) | {path for paths in policy['dependencies'].values() for path in paths}
        allowed = frozenset(entry.path for entry in root.files) if exact_task else frozenset(policy['routine'])
        result = compare_sources(root, current, routine=allowed,
                                 protected=frozenset(protected))
        if result.held:
            raise RenewalError('release changes require new authority or evidence')

    def approve(self, target: dict[str, Any], *, expected_digest: str, reviewed: bool) -> str:
        if reviewed is not True or digest(target) != expected_digest:
            raise RenewalError('exact reviewed task digest required')
        with self.locked():
            state, _ = self._state()
            if state['phase'] != 'ready':
                raise RenewalError('release publication recovery required')
            policy = self._policy(state)
            self._target(policy, target, exact_task=True)
            return self._store(state).put({'schema': 'artha.renewal-task.v1', 'policy': state['policy'],
                                          'epoch': state['epoch'], 'target': target, 'at': self.now().isoformat()})

    def _approval(self, state: dict[str, Any], identity: str) -> dict[str, Any]:
        approval = self._store(state).read(identity)
        if (set(approval) != {'schema', 'policy', 'epoch', 'target', 'at'}
                or approval['schema'] != 'artha.renewal-task.v1' or approval['policy'] != state['policy']
                or approval['epoch'] != state['epoch']
                or not 0 <= (self.now() - _time(approval['at'])).total_seconds() <= 86400):
            raise RenewalError('release task approval is stale or revoked')
        self._target(self._policy(state), approval['target'], exact_task=True)
        return approval

    def activate(self, approval_id: str, evidence: dict[str, Any], *,
                 verify: Callable[[dict[str, Any], dict[str, Any]], None]) -> str:
        with self.locked():
            state, revision = self._state()
            if state['phase'] != 'ready':
                raise RenewalError('release publication is held or requires recovery')
            approval = self._approval(state, approval_id)
            if state['active']:
                previous = self._store(state).read(state['active'])
                if previous['approval'] == approval_id and previous['evidence'] == evidence:
                    verify(approval['target'], evidence)
                    return str(state['active'])
            if state['serial'] >= 32 or evidence.get('tree') != approval['target']['tree']:
                raise RenewalError('release publication bound or tree mismatch')
            verify(approval['target'], evidence)
            policy = self._policy(state)
            self._parents(state)
            decision = {'schema': 'artha.renewal-decision.v1', 'policy': state['policy'], 'epoch': state['epoch'],
                        'serial': state['serial'] + 1, 'previous': state['active'], 'approval': approval_id,
                        'target': approval['target'], 'evidence': evidence, 'base': digest(policy['base_release']),
                        'at': self.now().isoformat()}
            identity = self._store(state).put(decision)
            self.observe('bundle')
            state.update(pending=identity, phase='prepared')
            self._save(state, revision)
            self.observe('intent')
            verify(approval['target'], evidence)
            self._policy(state)
            self._finish(state, identity)
            return identity

    def _finish(self, state: dict[str, Any], identity: str) -> None:
        decision = self._store(state).read(identity)
        current, revision = self._state()
        if (current != state or decision['epoch'] != state['epoch'] or decision['policy'] != state['policy']
                or decision['schema'] != 'artha.renewal-decision.v1'
                or decision['serial'] != state['serial'] + 1 or decision['previous'] != state['active']):
            raise RenewalError('release authority changed during publication')
        current.update(active=identity, pending='', phase='ready', serial=decision['serial'])
        if current['attempt']:
            journal = self._store(state).read(current['attempt'])
            receipt = journal.get('receipt', {})
            if (receipt.get('schema') == 'artha.delivery.v1'
                    and receipt.get('attempt') == decision['approval']):
                receipt.update(stage='VERIFIED', status='VERIFIED', renewal='renewed', decision=identity)
                journal['receipt'] = receipt
                current['attempt'] = self._store(state).put(journal)
        self._save(current, revision)
        self.observe('publication')

    def recover(self, *, verify: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
        with self.locked():
            state, _ = self._state()
            self._policy(state)
            if state['phase'] == 'ready':
                return
            decision = self._store(state).read(state['pending'])
            self._parents(state)
            approval = self._approval(state, decision['approval'])
            verify(approval['target'], decision['evidence'])
            self._policy(state)
            self._finish(state, state['pending'])

    def pending(self) -> dict[str, Any] | None:
        with self.locked():
            state, _ = self._state()
            self._policy(state)
            return self._store(state).read(state['pending']) if state['pending'] else None

    def validate(self, target: dict[str, Any], *,
                 validate_base: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.locked():
            state, _ = self._state()
            policy = self._policy(state)
            if state['phase'] != 'ready' or not state['active']:
                raise RenewalError('release renewal is unavailable or requires recovery')
            decision = self._store(state).read(state['active'])
            self._parents(state)
            if (decision['schema'] != 'artha.renewal-decision.v1'
                    or decision['base'] != digest(policy['base_release'])
                    or decision['target'] != target or decision['policy'] != state['policy']
                    or decision['epoch'] != state['epoch'] or decision['serial'] != state['serial']):
                raise RenewalError('release source or authority changed')
            self._target(policy, target, exact_task=True)
            validate_base(policy)
            return decision

    def revoke(self) -> None:
        with self.locked():
            state, revision = self._state()
            if state['phase'] == 'revoked':
                return
            predecessor = self._retire(state, 'revoked')
            state.update(epoch=state['epoch'] + 1, phase='revoked', active='', pending='', predecessor=predecessor)
            self._save(state, revision)

    def replace(self, policy: dict[str, Any], *, expected_digest: str, reviewed: bool,
                validate_base: Callable[[dict[str, Any]], None], evaluator: Mapping[str, bytes]) -> str:
        """Explicit new baseline review advances native generation and retains its predecessor."""
        self._check_policy(policy)
        identity = digest(policy)
        if reviewed is not True or identity != expected_digest:
            raise RenewalError('exact reviewed replacement digest required')
        if policy['expires_at'] is not None and self.now() >= _time(policy['expires_at']):
            raise RenewalError('reviewed replacement already expired')
        validate_base(policy)
        with self.locked():
            state, revision = self._state()
            if state['phase'] not in ('ready', 'revoked') or state['epoch'] >= 33:
                raise RenewalError('replacement needs resolved authority within its bound')
            store = self._store(state)
            store.put(policy)
            custody = preserve(store, evaluator, policy['evaluator'])
            predecessor = self._retire(state, 'replaced')
            state.update(epoch=state['epoch'] + 1, serial=0, policy=identity, custody=custody,
                         predecessor=predecessor, active='', pending='', attempt='', phase='ready',
                         adopted_at=self.now().isoformat())
            self._save(state, revision)
            return identity

    def _retire(self, state: dict[str, Any], reason: str) -> str:
        return self._store(state).put({'schema': 'artha.renewal-retirement.v1', 'reason': reason,
            **{key: state[key] for key in ('policy', 'epoch', 'active', 'pending', 'custody', 'attempt',
                                           'serial', 'phase', 'adopted_at')}, 'previous': state['predecessor']})

    def _parents(self, state: dict[str, Any]) -> None:
        """Materialize bounded provenance once, then recheck immutable artifact identities.

        New baselines retain retirement records rather than replaying every retired
        root's decision chain. Current permissions are always compared to the current
        approved root, never inferred from intermediate decisions.
        """
        head = tuple(state[key] for key in ('policy', 'active', 'predecessor', 'custody', 'epoch', 'serial'))
        def revision(identity: str) -> tuple[int, ...]:
            info = (self.root / (identity + '.ae1')).lstat()
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                    info.st_mode, info.st_nlink, info.st_uid)
        try:
            if (sys.platform != 'win32' and head == self._lineage_head
                    and all(revision(identity) == value for identity, value in self._lineage_files.items())):
                return
            store = self._store(state)
            versions = {}
            def load(identity: str) -> dict[str, Any]:
                if not is_digest(identity):
                    raise RenewalError('release provenance identity is invalid')
                before = revision(identity)
                value = store.read(identity)
                if before != revision(identity):
                    raise RenewalError('release provenance changed during verification')
                versions[identity] = before
                return value
            cursor = state['active']
            for serial in range(state['serial'], 0, -1):
                record = load(cursor)
                if (record.get('schema') != 'artha.renewal-decision.v1'
                        or record.get('policy') != state['policy'] or record.get('epoch') != state['epoch']
                        or record.get('serial') != serial or not isinstance(record.get('previous'), str)):
                    raise RenewalError('release decision provenance is inconsistent')
                _time(record.get('at'))
                cursor = record['previous']
            if cursor:
                raise RenewalError('release decision ancestry exceeds its bound')
            load(state['custody'])
            cursor, epoch = state['predecessor'], state['epoch']
            for _ in range(32):
                if not cursor:
                    break
                record = load(cursor)
                if (record.get('schema') != 'artha.renewal-retirement.v1'
                        or type(record.get('epoch')) is not int or not 1 <= record['epoch'] < epoch
                        or not isinstance(record.get('previous'), str)):
                    raise RenewalError('release baseline provenance is inconsistent')
                for field in ('policy', 'active', 'pending', 'custody', 'attempt'):
                    if record.get(field):
                        load(record[field])
                cursor, epoch = record['previous'], record['epoch']
            if cursor:
                raise RenewalError('release baseline ancestry exceeds its bound')
            self._lineage_head, self._lineage_files = head, versions
        except OSError:
            raise RenewalError('release provenance is unavailable') from None

    def task(self, identity: str) -> dict[str, Any]:
        with self.locked():
            state, _ = self._state()
            return self._approval(state, identity)

    def active(self) -> dict[str, Any] | None:
        with self.locked():
            state, _ = self._state()
            self._policy(state)
            if state['phase'] != 'ready':
                raise RenewalError('release publication recovery required')
            return self._store(state).read(state['active']) if state['active'] else None

    def record(self, receipt: dict[str, Any]) -> None:
        with self.locked():
            state, revision = self._state()
            self._policy(state)
            document = {'schema': 'artha.renewal-attempt.v1', 'epoch': state['epoch'],
                        'policy': state['policy'], 'receipt': receipt}
            state['attempt'] = self._store(state).put(document)
            self._save(state, revision)

    def progress(self) -> dict[str, Any] | None:
        with self.locked():
            state, _ = self._state()
            if not state['attempt']:
                return None
            document = self._store(state).read(state['attempt'])
            if (set(document) != {'schema', 'epoch', 'policy', 'receipt'}
                    or document['schema'] != 'artha.renewal-attempt.v1' or not isinstance(document['receipt'], dict)
                    or type(document['epoch']) is not int
                    or document['policy'] != state['policy'] or document['epoch'] > state['epoch']):
                raise RenewalError('delivery progress belongs to an obsolete delegation')
            if document['epoch'] != state['epoch'] or state['phase'] == 'revoked':
                return {**document['receipt'], 'status': 'HELD', 'renewal': 'held',
                        'reason': 'delegation revoked; completed Git effects retained'}
            return dict(document['receipt'])

    def evaluator(self, directory: Path) -> Path:
        with self.locked():
            state, _ = self._state()
            policy = self._policy(state)
            return restore(self._store(state), state['custody'], policy['evaluator'], directory)

    def review_policy(self) -> dict[str, Any]:
        """Read an inactive baseline for explicit replacement review; grants no runtime permission."""
        with self.locked():
            state, _ = self._state()
            policy = self._store(state).read(state['policy'])
            self._check_policy(policy)
            return policy

    def review_evaluator(self, directory: Path) -> Path:
        with self.locked():
            state, _ = self._state()
            policy = self._store(state).read(state['policy'])
            self._check_policy(policy)
            return restore(self._store(state), state['custody'], policy['evaluator'], directory)

    def routine(self, target: dict[str, Any]) -> str:
        """The enrolled exact routine scope supplies approval; no new owner prompt."""
        self._target(self.policy(), target)
        progress = self.progress()
        if progress and isinstance(progress.get('attempt'), str):
            if progress.get('stage') == 'VERIFIED':
                active = self.active()
                if (active and digest(active) == progress.get('decision')
                        and progress.get('target', active['target']) == target):
                    return str(progress['attempt'])
            previous = self.task(progress['attempt'])
            if previous['target'] == target:
                return str(progress['attempt'])
        return self.approve(target, expected_digest=digest(target), reviewed=True)
