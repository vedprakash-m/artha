import test_renewal_authority as authority_fixtures
world = authority_fixtures.world
"""Delivery orchestration never contacts a live provider in these tests."""
from copy import deepcopy
from datetime import timedelta

import pytest

from catchup_core.renewal_delivery import Delivery
from catchup_core.renewal_store import RenewalError
from test_renewal_authority import NOW, approve, target


class Workspace:
    def __init__(self):
        self.commit = ''
        self.tip = '0' * 40
        self.effects = []
        self.ambiguous = False

    def remote(self, repository):
        assert repository == 'example/project'

    def tree(self):
        return 'c' * 40

    def git(self, *args):
        self.effects.append(args[0])
        if args[0] == 'commit':
            self.commit = 'e' * 40
        elif args[0] == 'push':
            self.tip = self.commit
            if self.ambiguous:
                raise RenewalError('Git delivery operation unavailable')
        return ''

    def clean_commit(self):
        if not self.commit:
            raise RenewalError('not committed')
        return self.commit

    def remote_tip(self):
        return self.tip


@pytest.fixture
def delivery(world):
    authority, _, _ = world
    workspace = Workspace()
    current = target()
    calls = []
    ci = {'requests': 3, 'status': 'passed', 'reason': 'all required CI verified', 'runs': [], 'observed_at': NOW.isoformat()}
    engine = Delivery(authority, workspace, snapshot=lambda: deepcopy(current),
                      gates=lambda: calls.append('gates'), ci=lambda *_: deepcopy(ci),
                      validate_base=lambda _: calls.append('base'))
    return engine, authority, workspace, current, calls, ci


def test_AR01_delivery_and_repeat_have_one_commit_push_publication(delivery):
    engine, authority, workspace, _, calls, _ = delivery
    approval = approve(authority)
    receipt = engine.run(approval, message='Synthetic reviewed delivery')
    assert receipt['stage'] == 'VERIFIED' and receipt['pushed'] is True
    assert workspace.effects == ['commit', 'push']
    assert engine.run(approval, message='ignored') == receipt
    assert calls.count('gates') == 1
    assert authority.validate(target(), validate_base=lambda _: None)['approval'] == approval


def test_AR08_pending_resumes_without_local_gate_or_commit_repeat(delivery):
    engine, authority, workspace, _, calls, ci = delivery
    approval = approve(authority)
    ci['status'] = 'pending'
    first = engine.run(approval, message='Synthetic')
    assert first['stage'] == 'PUSHED' and first['status'] == 'PENDING' and first['pushed']
    ci['status'] = 'passed'
    assert engine.run(approval, message='Synthetic')['stage'] == 'VERIFIED'
    assert calls.count('gates') == 1 and workspace.effects == ['commit', 'push']


def test_AR07_ambiguous_push_reconciles_exact_tip(delivery):
    engine, authority, workspace, _, _, _ = delivery
    workspace.ambiguous = True
    assert engine.run(approve(authority), message='Synthetic')['stage'] == 'VERIFIED'
    assert workspace.effects.count('push') == 1


def test_AR06_mutation_after_gate_prevents_commit(delivery):
    engine, authority, workspace, current, _, _ = delivery
    def mutate():
        current['context'] = 'f' * 64
    engine.gates = mutate
    result = engine.run(approve(authority), message='Synthetic')
    assert result['status'] == 'HELD' and workspace.effects == []


def test_AR07_new_remote_tip_supersedes_pending(delivery):
    engine, authority, workspace, _, _, ci = delivery
    approval = approve(authority)
    ci['status'] = 'pending'
    engine.run(approval, message='Synthetic')
    workspace.tip = 'f' * 40
    assert engine.run(approval, message='Synthetic')['status'] == 'SUPERSEDED'
    assert authority.active() is None


def test_AR04_failed_ci_leaves_push_visible(delivery):
    engine, authority, _, _, _, ci = delivery
    ci['status'] = 'held'
    result = engine.run(approve(authority), message='Synthetic')
    assert result['pushed'] and result['status'] == 'HELD' and authority.active() is None


def test_AR08_cancel_never_publishes(delivery):
    engine, authority, workspace, _, _, _ = delivery
    engine.cancelled = lambda: True
    result = engine.run(approve(authority), message='Synthetic')
    assert result['status'] == 'CANCELLED' and workspace.effects == []


def test_AR05_expired_gate_receipt_requires_new_attempt(delivery):
    engine, authority, _, _, calls, ci = delivery
    approval = approve(authority)
    ci['status'] = 'pending'
    engine.run(approval, message='Synthetic')
    authority.now = lambda: NOW + timedelta(days=2)
    with pytest.raises(RenewalError):
        engine.run(approval, message='Synthetic')
    assert calls.count('gates') == 1


def test_AR02_docs_only_delivery_keeps_active_decision_and_original_timestamp(delivery):
    engine, authority, workspace, current, _, _ = delivery
    first = engine.run(approve(authority), message='Synthetic')
    active = authority.active()
    current['tree'] = 'd' * 40
    workspace.tree = lambda: 'd' * 40
    workspace.commit, workspace.tip = 'f' * 40, 'f' * 40
    approval = authority.routine(current)
    second = engine.run(approval, message='Synthetic docs')
    assert second['renewal'] == 'unchanged' and authority.active() == active
    assert first['decision'] == second['decision']
    assert engine.run(approval, message='repeat') == second


def test_AR18_public_projection_omits_private_journal_payload(delivery):
    from catchup_core.renewal_delivery import public_receipt
    engine, authority, _, _, _, _ = delivery
    result = engine.run(approve(authority), message='Synthetic')
    result['private'] = 'PRIVATE_CANARY'
    assert 'PRIVATE_CANARY' not in public_receipt(result)
    assert 'renewal renewed' in public_receipt(result)


def test_AR25_native_journal_failure_does_not_hide_completed_commit(delivery, monkeypatch):
    engine, authority, workspace, _, _, _ = delivery
    approval = approve(authority)
    original = authority.record
    def fail(receipt):
        if receipt['stage'] == 'COMMITTED':
            raise RenewalError('synthetic native write unavailable')
        original(receipt)
    monkeypatch.setattr(authority, 'record', fail)
    result = engine.run(approval, message='Synthetic')
    assert result['commit'] == workspace.commit and result['status'] == 'RECOVERY_REQUIRED'
    assert result['pushed'] is False


def test_AR08_cancellation_after_intent_is_recovery_required(delivery):
    engine, authority, _, _, _, _ = delivery
    cancelled = [False]
    engine.cancelled = lambda: cancelled[0]
    authority.observe = lambda event: cancelled.__setitem__(0, True) if event == 'intent' else None
    result = engine.run(approve(authority), message='Synthetic')
    assert result['status'] == 'RECOVERY_REQUIRED' and result['pushed'] is True
    assert authority.pending() is not None


def test_AR08_ci_request_budget_is_durable_across_pending_sessions(delivery):
    engine, authority, _, _, _, ci = delivery
    approval = approve(authority)
    ci.update(status='pending', requests=30)
    assert engine.run(approval, message='Synthetic')['ci_requests'] == 30
    engine.ci = lambda *_: pytest.fail('request budget must not reset on resume')
    assert engine.run(approval, message='Synthetic')['status'] == 'HELD'


def test_AR09_death_after_native_publication_resumes_same_completed_attempt(delivery):
    engine, authority, _, _, calls, _ = delivery
    approval = approve(authority)
    def crash(event):
        if event == 'publication':
            raise RuntimeError('synthetic process death')
    authority.observe = crash
    with pytest.raises(RuntimeError):
        engine.run(approval, message='Synthetic')
    authority.observe = lambda _: None
    assert authority.progress()['stage'] == 'VERIFIED'
    engine.ci = lambda *_: pytest.fail('completed publication must not fetch CI again')
    assert engine.run(approval, message='repeat')['renewal'] == 'renewed'
    assert calls.count('gates') == 1


def test_AR08_cancelled_attempt_is_stable_on_repeat(delivery):
    engine, authority, workspace, _, _, _ = delivery
    approval = approve(authority)
    engine.cancelled = lambda: True
    first = engine.run(approval, message='Synthetic')
    assert engine.run(approval, message='repeat') == first and not workspace.effects


def test_AR07_unresolved_push_preserves_commit_without_claiming_publication(delivery):
    engine, authority, workspace, _, _, _ = delivery
    original = workspace.git
    def git(*args):
        if args[0] == 'push':
            raise RenewalError('synthetic lost network')
        return original(*args)
    workspace.git = git
    result = engine.run(approve(authority), message='Synthetic')
    assert result['stage'] == 'COMMITTED' and result['commit'] and not result['pushed']


def test_AR07_changed_local_commit_during_ci_pending_refuses(delivery):
    engine, authority, workspace, _, _, ci = delivery
    approval = approve(authority)
    ci['status'] = 'pending'
    engine.run(approval, message='Synthetic')
    workspace.commit = 'f' * 40
    result = engine.run(approval, message='Synthetic')
    assert result['status'] == 'HELD' and result['pushed']


def test_AR07_remote_advancement_after_ci_refuses_activation(delivery):
    engine, authority, workspace, _, _, _ = delivery
    def ci(*_):
        workspace.tip = 'f' * 40
        return {'status': 'passed', 'requests': 3}
    engine.ci = ci
    assert engine.run(approve(authority), message='Synthetic')['status'] == 'SUPERSEDED'
    assert authority.active() is None


def test_AR05_future_local_gate_receipt_is_never_reused(delivery):
    engine, authority, _, _, _, ci = delivery
    approval = approve(authority)
    ci['status'] = 'pending'
    first = engine.run(approval, message='Synthetic')
    first['validated_at'] = (NOW + timedelta(hours=1)).isoformat()
    authority.record(first)
    assert engine.run(approval, message='Synthetic')['status'] == 'HELD'


def test_AR13_invalid_provider_budget_accounting_refuses(delivery):
    engine, authority, _, _, _, ci = delivery
    ci['requests'] = 'PRIVATE_CANARY'
    assert engine.run(approve(authority), message='Synthetic')['status'] == 'HELD'
    assert authority.active() is None


def test_AR17_completed_publication_does_not_survive_replacement(delivery):
    engine, authority, _, _, _, _ = delivery
    approval = approve(authority)
    engine.run(approval, message='Synthetic')
    authority.active = lambda: None
    with pytest.raises(RenewalError, match='active publication'):
        engine.run(approval, message='Synthetic')


def test_AR07_verified_attempt_cannot_claim_current_remote_after_supersession(delivery):
    engine, authority, workspace, _, _, _ = delivery
    approval = approve(authority)
    engine.run(approval, message='Synthetic')
    workspace.tip = 'f' * 40
    assert engine.run(approval, message='Synthetic')['status'] == 'HELD'


def test_AR21_docs_only_delivery_with_invalid_existing_approval_reports_hold(delivery):
    engine, authority, workspace, current, _, _ = delivery
    engine.run(approve(authority), message='Synthetic')
    active = authority.active()
    current['tree'] = 'd' * 40
    workspace.tree = lambda: 'd' * 40
    approval = authority.routine(current)
    def invalid(_):
        raise RenewalError('approved release evidence expired')
    engine.validate_base = invalid
    result = engine.run(approval, message='Synthetic docs')
    assert result['status'] == 'HELD' and result['renewal'] == 'held' and result['reason'] == 'evidence'
    assert authority.active() == active and 'unchanged' not in result['renewal']
