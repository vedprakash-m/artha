"""One foreground delivery with durable, bounded resume; publication stays in Authority."""
from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any, cast

from catchup_core.ledger import PermissionDenied
from catchup_core.renewal_authority import Authority, _time
from catchup_core.renewal_store import REASONS, RenewalError, digest
from catchup_core.renewal_workspace import Workspace
from catchup_core.session_lock import SessionBusy, session_lock


class Delivery:
    def __init__(self, authority: Authority, workspace: Workspace, *,
                 snapshot: Callable[[], dict[str, Any]], gates: Callable[[], dict[str, Any] | None],
                 ci: Callable[[str, dict[str, Any], int], dict[str, Any]],
                 validate_base: Callable[[dict[str, Any]], None],
                 cancelled: Callable[[], bool] | None = None):
        self.authority = authority
        self.workspace = workspace
        self.snapshot = snapshot
        self.gates = gates
        self.ci = ci
        self.validate_base = validate_base
        self.cancelled = cancelled or (lambda: False)

    def _check(self, target: dict[str, Any], policy: dict[str, Any] | None = None) -> None:
        if self.cancelled():
            raise InterruptedError
        try:
            if self.snapshot() != target or self.workspace.tree() != target['tree']:
                raise RenewalError('validated delivery inputs changed')
        except RenewalError:
            raise RenewalError('validated delivery inputs changed or unavailable', reason='inputs') from None
        try:
            self.validate_base(policy if policy is not None else self.authority.policy())
        except (RenewalError, PermissionDenied, OSError, ValueError):
            raise RenewalError('release baseline evidence is held', reason='evidence') from None

    def _record(self, receipt: dict[str, Any], **updates: Any) -> None:
        receipt.update(updates)
        self.authority.record(receipt)

    def run(self, approval: str, *, message: str) -> dict[str, Any]:
        # One foreground delivery per installation; unlike the publication lock, this
        # spans gates, Git and CI so a concurrent run cannot overwrite the journal.
        try:
            with session_lock(self.authority.local / 'release-delivery.lock'):
                return self._run(approval, message=message)
        except SessionBusy:
            raise RenewalError('another release delivery is active') from None

    def _run(self, approval: str, *, message: str) -> dict[str, Any]:
        started = monotonic()
        # Refresh task/current native authority even on an idempotent or resumed request.
        active = None
        previous = self.authority.progress()
        if previous is not None:
            _check_journal(previous)
        policy = self._current_policy(previous)
        if policy is None:
            return cast(dict[str, Any], previous)
        if previous and previous.get('attempt') == approval and previous.get('stage') == 'VERIFIED':
            active = self.authority.active()
            if active is None or digest(active) != previous.get('decision'):
                raise RenewalError('completed delivery no longer names the active publication')
            target = previous['target']
            if any(target[key] != active['target'][key] for key in ('source', 'cohort', 'context')):
                raise RenewalError('completed delivery source differs from native publication')
        else:
            target = self.authority.task(approval)['target']
            if previous and previous['attempt'] == approval and previous['target'] != target:
                raise RenewalError('delivery journal differs from the exact task approval')
        receipt = (previous if previous and previous.get('attempt') == approval else
                   {'schema': 'artha.delivery.v1', 'attempt': approval, 'stage': 'PREPARED',
                    'status': 'ACTIVE', 'pushed': False, 'commit': '', 'ci': 'pending',
                    'renewal': 'held', 'reason': '', 'validated_at': '', 'target': target})
        if receipt['status'] in {'SUPERSEDED', 'CANCELLED'}:
            return receipt
        try:
            self.workspace.remote(self.authority.binding['repository'])
            self._check(target)
            if receipt['stage'] == 'VERIFIED':
                self.authority.validate(cast(dict[str, Any], active)['target'], validate_base=self.validate_base)
                if self.workspace.remote_tip() != receipt['commit']:
                    raise RenewalError('verified delivery has been superseded')
                return receipt
            reused = bool(receipt['validated_at'])
            result = self._continue(receipt, target, policy, message)
            self._record(result, elapsed_ms=receipt.get('elapsed_ms', 0) + int((monotonic() - started) * 1000),
                         gate_cache_reused=reused)
            return result
        except InterruptedError:
            self._stop(receipt, 'CANCELLED')
        except RenewalError as error:
            # Adapter exceptions are intentionally not copied into a public receipt.
            self._stop(receipt, 'HELD', reason=error.reason)
        return receipt

    def _current_policy(self, previous: dict[str, Any] | None) -> dict[str, Any] | None:
        try:
            return self.authority.policy()
        except RenewalError:
            if previous is None:
                raise
            previous.update(status='HELD', renewal='held', reason='authority')
            return None

    def _stop(self, receipt: dict[str, Any], status: str, *, reason: str = 'verification') -> None:
        try:
            if self.authority.pending() is not None:
                status, reason = 'RECOVERY_REQUIRED', 'journal'
            self._record(receipt, status=status, reason=reason)
        except RenewalError:
            receipt.update(status='RECOVERY_REQUIRED', reason='journal')

    def _continue(self, receipt: dict[str, Any], target: dict[str, Any],
                  policy: dict[str, Any], message: str) -> dict[str, Any]:
        if not receipt['validated_at']:
            self._record(receipt)
            local_proof = self.gates()
            self._check(target)
            self._record(receipt, stage='LOCAL_VALIDATED', validated_at=self.authority.now().isoformat(),
                         local_gates=local_proof)
        elif not 0 <= (self.authority.now() - _time(receipt['validated_at'])).total_seconds() <= 86400:
            raise RenewalError('local gate receipt expired', reason='gates')
        if not receipt['commit']:
            try:
                commit = self.workspace.clean_commit()
            except RenewalError:
                self.workspace.git('commit', '-m', message)
                commit = self.workspace.clean_commit()
            self._check(target)
            self._record(receipt, stage='COMMITTED', commit=commit)
        if self.workspace.clean_commit() != receipt['commit']:
            raise RenewalError('committed candidate changed')
        if not receipt['pushed']:
            try:
                self.workspace.git('push', 'origin', 'main')
            except RenewalError:
                if self.workspace.remote_tip() != receipt['commit']:
                    raise RenewalError('push outcome unresolved; inspect remote main') from None
            self._record(receipt, stage='PUSHED', pushed=True)
        if self.workspace.remote_tip() != receipt['commit']:
            self._record(receipt, status='SUPERSEDED', reason='remote main differs from this exact delivery')
            return receipt
        self._check(target)
        proof = self._collect_ci(receipt, policy)
        if proof.get('status') != 'passed':
            self._record(receipt, status='PENDING' if proof.get('status') == 'pending' else 'HELD',
                         ci=proof.get('status', 'held'), reason='ci')
            return receipt
        return self._publish(receipt, target, policy, proof)

    def _collect_ci(self, receipt: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        spent = receipt.get('ci_requests', 0)
        remaining = 30 - spent
        if remaining <= 0:
            raise RenewalError('CI request budget exhausted; exact attempt review required', reason='budget')
        # Reserve before network access: process death cannot reset the request budget.
        self._record(receipt, ci_requests=30)
        try:
            proof = self.ci(receipt['commit'], policy, remaining)
        except RenewalError:
            raise RenewalError('required CI evidence unavailable', reason='ci') from None
        requests = proof.get('requests', remaining)
        if type(requests) is not int or not 0 < requests <= remaining:
            raise RenewalError('CI provider request accounting is invalid')
        self._record(receipt, ci_requests=spent + requests)
        return proof

    def _publish(self, receipt: dict[str, Any], target: dict[str, Any],
                 policy: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any]:
        self._record(receipt, stage='CI_VERIFIED', status='ACTIVE', ci='passed')
        if self.workspace.remote_tip() != receipt['commit']:
            self._record(receipt, status='SUPERSEDED', reason='remote main advanced before activation')
            return receipt
        evidence = {'commit': receipt['commit'], 'tree': target['tree'], 'ci': proof,
                    'local': {'target': digest(target), 'at': receipt['validated_at'],
                              'gates': receipt['local_gates']}}
        self._record(receipt, stage='ELIGIBLE')
        active = self.authority.active()
        if active and all(active['target'][key] == target[key] for key in ('source', 'cohort', 'context')):
            self.authority.validate(active['target'], validate_base=self.validate_base)
            self._record(receipt, stage='VERIFIED', status='VERIFIED', renewal='unchanged',
                         decision=digest(active), reason='delivery verified; runtime authorization unchanged')
            return receipt
        self._record(receipt, stage='ACTIVATING')
        decision = self.authority.activate(receipt['attempt'], evidence,
                                           verify=lambda approved, _: self._check(approved, policy))
        self.authority.validate(target, validate_base=self.validate_base)
        self._record(receipt, stage='VERIFIED', status='VERIFIED', renewal='renewed',
                     decision=decision, reason='exact delivery and renewed runtime authorization verified')
        return receipt


def public_receipt(receipt: dict[str, Any]) -> str:
    """A technical receipt is a projection, never a dump of a private journal."""
    commit = receipt.get('commit', '')
    if (not isinstance(commit, str) or len(commit) not in (40, 64)
            or any(char not in '0123456789abcdef' for char in commit)):
        commit = 'uncommitted'
    ci = receipt.get('ci')
    if ci not in ('passed', 'pending', 'held'):
        ci = 'held'
    renewal = receipt.get('renewal')
    if renewal not in ('renewed', 'unchanged'):
        renewal = 'held'
    stage = receipt.get('stage')
    if stage not in ('PREPARED', 'LOCAL_VALIDATED', 'COMMITTED', 'PUSHED', 'CI_VERIFIED',
                     'ELIGIBLE', 'ACTIVATING', 'VERIFIED', 'ENROLLED'):
        stage = 'HELD'
    status = receipt.get('status')
    if status not in ('ACTIVE', 'VERIFIED', 'PENDING', 'HELD', 'CANCELLED', 'SUPERSEDED', 'RECOVERY_REQUIRED'):
        status = 'HELD'
    reason = receipt.get('reason')
    detail = REASONS.get(reason, REASONS['verification']) if isinstance(reason, str) else REASONS['verification']
    if status == 'SUPERSEDED':
        detail = 'remote main differs; review the newer tip before starting another attempt'
    elif status == 'CANCELLED':
        detail = 'session cancelled; inspect the last completed stage before starting another attempt'
    elif status == 'VERIFIED':
        detail = ''
    return (f"main {commit} {'pushed' if receipt.get('pushed') is True else 'not pushed'} | "
            f'CI {ci} | renewal {renewal} | stage {stage} | {status}' + (f' | {detail}' if detail else ''))


def _check_journal(receipt: dict[str, Any]) -> None:
    required = {'schema', 'attempt', 'stage', 'status', 'pushed', 'commit', 'ci', 'renewal', 'reason',
                'validated_at', 'target'}
    optional = {'decision', 'elapsed_ms', 'gate_cache_reused', 'ci_requests', 'local_gates'}
    if (not required <= receipt.keys() or not receipt.keys() <= required | optional
            or receipt['schema'] != 'artha.delivery.v1' or type(receipt['pushed']) is not bool
            or not isinstance(receipt['target'], dict) or not isinstance(receipt['commit'], str)
            or set(receipt['target']) != {'source', 'cohort', 'tree', 'context', 'authority'}
            or not isinstance(receipt['validated_at'], str)
            or type(receipt.get('ci_requests', 0)) is not int or not 0 <= receipt.get('ci_requests', 0) <= 30
            or receipt['stage'] not in ('PREPARED', 'LOCAL_VALIDATED', 'COMMITTED', 'PUSHED',
                                        'CI_VERIFIED', 'ELIGIBLE', 'ACTIVATING', 'VERIFIED')
            or receipt['status'] not in ('ACTIVE', 'PENDING', 'HELD', 'SUPERSEDED', 'CANCELLED',
                                         'RECOVERY_REQUIRED', 'VERIFIED')):
        raise RenewalError('delivery journal version or fields are invalid')
