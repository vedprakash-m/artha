"""Dispatch must use current policy, fresh review and the durable queue approver; no provider traffic."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from action_queue import ActionQueue, compute_payload_hash
from lib import action_process
from test_action_process import proposal


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'config').mkdir(parents=True)
    path = root / 'config/actions.yaml'
    path.write_text(yaml.safe_dump({'live_trial': {'held': [], 'handoff_only': []},
        'actions': {'reminder_create': {'enabled': True}}}))
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(tmp_path / 'actions.db'))
    queue = ActionQueue(root)
    item = proposal()
    queue.propose(item)
    queue.mark_preview_shown(item.id, shown_by='owner', payload_hash=compute_payload_hash(item))
    queue.transition(item.id, 'approved', actor='owner', approved_by='owner')
    queue.transition(item.id, 'executing', actor='executor', expected_payload_hash=compute_payload_hash(item),
                     operation_key='synthetic-operation')
    # Also describes a pre-upgrade queue: absence of a dispatch-policy record must fail closed.
    queue._conn.execute('CREATE TABLE IF NOT EXISTS action_dispatch_policies('
                        'action_id TEXT PRIMARY KEY,policy_root TEXT NOT NULL,policy_sha256 TEXT NOT NULL)')
    queue._conn.execute('INSERT INTO action_dispatch_policies VALUES(?,?,?)',
                        (item.id, str(root), hashlib.sha256(path.read_bytes()).hexdigest()))
    queue._conn.commit()
    yield root, path, queue, item
    queue.close()


@pytest.mark.parametrize('change', ['rewrite', 'delete', 'pause', 'missing-binding', 'expired', 'old-preview',
                                   'automatic-approver', 'wrong-preview', 'future-preview'])
def test_worker_refuses_stale_dispatch_authority_before_consuming_it(claimed, change):
    root, path, queue, item = claimed
    if change == 'rewrite':
        path.write_text(path.read_text() + '\n# withdrawn revision\n')
    elif change == 'delete':
        path.unlink()
    elif change == 'pause':
        (root / 'config/actions.paused').touch()
    elif change == 'missing-binding':
        queue._conn.execute('DELETE FROM action_dispatch_policies')
    elif change == 'expired':
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        queue._conn.execute('UPDATE actions SET expires_at=?', (stamp,))
    elif change == 'old-preview':
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
        queue._conn.execute('UPDATE actions SET preview_shown_at=?', (stamp,))
    elif change == 'wrong-preview':
        queue._conn.execute("UPDATE actions SET preview_payload_hash='changed'")
    elif change == 'future-preview':
        stamp = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        queue._conn.execute('UPDATE actions SET preview_shown_at=?', (stamp,))
    else:
        queue._conn.execute("UPDATE actions SET approved_by='system:auto'")
    queue._conn.commit()
    with pytest.raises((ValueError, OSError, sqlite3.Error)):
        action_process._authorize(queue._db_path, item, 'execute', consume=True)
    assert queue._conn.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


def test_unchanged_dispatch_policy_can_be_consumed_once(claimed):
    _, _, queue, item = claimed
    action_process._authorize(queue._db_path, item, 'execute', consume=True)
    with pytest.raises(sqlite3.IntegrityError):
        action_process._authorize(queue._db_path, item, 'execute', consume=True)


def test_policy_withdrawal_does_not_prevent_read_only_outcome_reconciliation(claimed):
    root, path, queue, item = claimed
    path.unlink()
    (root / 'config/actions.paused').touch()
    queue._conn.execute("UPDATE actions SET status='unknown'")
    queue._conn.commit()
    action_process._authorize(queue._db_path, item, 'reconcile', consume=False)


@pytest.mark.parametrize('change', ['pause', 'policy'])
def test_parent_rechecks_after_credential_capture_before_starting_child(claimed, monkeypatch, change):
    from lib import action_credentials
    root, path, queue, item = claimed
    def capture(*args, **kwargs):
        if change == 'pause':
            (root / 'config/actions.paused').touch()
        else:
            path.write_text(path.read_text() + '\n# changed during capture\n')
        return {}
    monkeypatch.setattr(action_credentials, 'prepare', capture)
    def forbidden(*args, **kwargs):
        pytest.fail('withdrawn policy started a provider-capable child')
    result = action_process.execute('actions.reminder_create', item, 5, queue_path=queue._db_path, runner=forbidden)
    assert result.status == 'failure'
    assert queue._conn.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


@pytest.mark.parametrize('value', ['false', 0, None, [], {}])
def test_action_enablement_cannot_be_coerced(claimed, value):
    from lib.action_policy import authorize_dispatch_policy, load_dispatch_policy
    root, path, _, item = claimed
    document = yaml.safe_load(path.read_text())
    document['actions']['reminder_create']['enabled'] = value
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        policy = load_dispatch_policy(root, item.action_type)
        authorize_dispatch_policy(policy, item, 'owner', datetime.now(timezone.utc))


@pytest.mark.parametrize('field,value', [('held', 'reminder_create'), ('held', [False]),
                                        ('handoff_only', 'reminder_create')])
def test_trial_lists_cannot_be_coerced(claimed, field, value):
    from lib.action_policy import load_dispatch_policy
    root, path, _, item = claimed
    document = yaml.safe_load(path.read_text())
    document['live_trial'][field] = value
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        load_dispatch_policy(root, item.action_type)


def _claim(queue, item, policy):
    return queue.transition(item.id, 'executing', actor='executor',
        expected_payload_hash=compute_payload_hash(item), operation_key='operation-' + item.id,
        dispatch_policy=(policy.root, policy.sha256))


def test_current_rate_limit_and_claim_share_one_queue_transaction(claimed):
    from dataclasses import replace
    from actions.base import ActionResult
    from lib.action_policy import load_dispatch_policy
    root, path, queue, item = claimed
    queue.record_result(item.id, ActionResult('success', 'synthetic', None, False, None),
                        datetime.now(timezone.utc).isoformat())
    document = yaml.safe_load(path.read_text())
    document['actions']['reminder_create']['rate_limit'] = {'max_per_hour': 1, 'max_per_day': 2}
    path.write_text(yaml.safe_dump(document))
    policy = load_dispatch_policy(root, item.action_type)
    other = replace(item, id='second', parameters={'title': 'Another distinct instruction'})
    queue.propose(other)
    queue.mark_preview_shown(other.id, shown_by='owner', payload_hash=compute_payload_hash(other))
    queue.transition(other.id, 'approved', actor='owner', approved_by='owner')
    with pytest.raises(ValueError, match='limit'):
        _claim(queue, other, policy)
    assert queue.get_raw(other.id)['status'] == 'approved'
    assert not queue._conn.execute('SELECT 1 FROM effect_operations WHERE action_id=?', (other.id,)).fetchone()
    assert not queue._conn.execute('SELECT 1 FROM action_dispatch_policies WHERE action_id=?', (other.id,)).fetchone()


def test_executing_and_success_audit_entries_count_one_attempt(claimed):
    from dataclasses import replace
    from actions.base import ActionResult
    from lib.action_policy import load_dispatch_policy
    root, path, queue, item = claimed
    queue.record_result(item.id, ActionResult('success', 'synthetic', None, False, None),
                        datetime.now(timezone.utc).isoformat())
    document = yaml.safe_load(path.read_text())
    document['actions']['reminder_create']['rate_limit'] = {'max_per_hour': 2}
    path.write_text(yaml.safe_dump(document))
    policy = load_dispatch_policy(root, item.action_type)
    other = replace(item, id='second', parameters={'title': 'Another distinct instruction'})
    queue.propose(other)
    queue.transition(other.id, 'approved', actor='owner', approved_by='owner')
    _claim(queue, other, policy)
    assert queue.get_raw(other.id)['status'] == 'executing'


def test_real_child_rechecks_policy_before_loading_any_provider(claimed, monkeypatch):
    import subprocess
    import test_action_process
    test_action_process.offline_capability.__wrapped__(monkeypatch)
    _, path, queue, item = claimed
    def withdraw_then_run(*args, **kwargs):
        path.write_text(path.read_text() + '\n# withdrawn before child entry\n')
        return subprocess.run(*args, **kwargs)
    result = action_process.execute('actions.reminder_create', item, 5,
        queue_path=queue._db_path, runner=withdraw_then_run)
    assert result.status == 'unknown' and 'ValueError' in result.message
    assert queue._conn.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


@pytest.mark.parametrize('field,value', [('timeout_sec', True), ('timeout_sec', 121),
    ('preview_required', 'false'), ('pii_check', 'false'), ('named_recipients', 'all@example.test'),
    ('rate_limit', {'max_per_hour': True}), ('rate_limit', {'max_per_hour': -1}),
    ('rate_limit', {'max_per_week': 4}), ('rate_limit', None)])
def test_dispatch_controls_reject_coerced_or_unbounded_values(claimed, field, value):
    from lib.action_policy import load_dispatch_policy
    root, path, _, item = claimed
    document = yaml.safe_load(path.read_text())
    document['actions']['reminder_create'][field] = value
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        load_dispatch_policy(root, item.action_type)


@pytest.mark.parametrize('document', [b'[', b'\xff', b'[]', b'{}', b'actions: {}', b'x' * 1_000_001])
def test_unreadable_or_unbounded_policy_cannot_authorize_dispatch(claimed, document):
    from lib.action_policy import load_dispatch_policy
    root, path, _, item = claimed
    path.write_bytes(document)
    with pytest.raises(ValueError):
        load_dispatch_policy(root, item.action_type)


@pytest.mark.parametrize('control', ['held', 'handoff_only'])
def test_current_policy_can_withhold_an_enabled_type(claimed, control):
    from lib.action_policy import authorize_dispatch_policy, load_dispatch_policy
    root, path, _, item = claimed
    document = yaml.safe_load(path.read_text())
    document['live_trial'][control] = [item.action_type]
    path.write_text(yaml.safe_dump(document))
    policy = load_dispatch_policy(root, item.action_type)
    with pytest.raises(ValueError):
        authorize_dispatch_policy(policy, item, 'owner', datetime.now(timezone.utc))
    with pytest.raises(ValueError):
        authorize_dispatch_policy(policy, item, '', datetime.now(timezone.utc))


def test_current_policy_revision_is_rechecked_inside_the_winning_claim(claimed):
    from dataclasses import replace
    from lib.action_policy import load_dispatch_policy
    root, path, queue, item = claimed
    policy = load_dispatch_policy(root, item.action_type)
    other = replace(item, id='new', parameters={'title': 'Distinct item'})
    queue.propose(other)
    queue.transition(other.id, 'approved', actor='owner', approved_by='owner')
    path.write_text(path.read_text() + '\n# changed before claim\n')
    with pytest.raises(ValueError, match='policy changed'):
        _claim(queue, other, policy)
    assert queue.get_raw(other.id)['status'] == 'approved'
    assert not queue._conn.execute('SELECT 1 FROM effect_operations WHERE action_id=?', (other.id,)).fetchone()


def test_two_processes_cannot_both_claim_the_last_current_policy_rate_slot(claimed):
    import subprocess
    import sys
    from dataclasses import replace
    from pathlib import Path
    root, path, queue, item = claimed
    # Keep the earlier synthetic claim outside the measured window.
    queue._conn.execute("UPDATE action_audit SET timestamp='2000-01-01T00:00:00+00:00'")
    queue._conn.commit()
    document = yaml.safe_load(path.read_text())
    document['actions']['reminder_create']['rate_limit'] = {'max_per_hour': 1}
    path.write_text(yaml.safe_dump(document))
    for identity in ('first-racer', 'second-racer'):
        other = replace(item, id=identity, parameters={'title': identity})
        queue.propose(other)
        queue.transition(identity, 'approved', actor='owner', approved_by='owner')
    code = '''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from action_queue import ActionQueue,compute_payload_hash
from lib.action_policy import load_dispatch_policy
q=ActionQueue(Path(sys.argv[2]))
try:
    policy=load_dispatch_policy(Path(sys.argv[3]),'reminder_create')
    item=q.get(sys.argv[4])
    print('ready',flush=True)
    sys.stdin.readline()
    try:
        q.transition(item.id,'executing',actor='executor',expected_payload_hash=compute_payload_hash(item),
                     operation_key='operation-'+item.id,dispatch_policy=(policy.root,policy.sha256))
        print('claimed',flush=True)
    except ValueError:
        print('refused',flush=True)
finally:
    q.close()
'''
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    children = []
    try:
        for identity in ('first-racer', 'second-racer'):
            children.append(subprocess.Popen([sys.executable, '-c', code, str(scripts), str(queue._db_path),
                str(root), identity], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for child in children:
            assert child.stdout.readline().strip() == 'ready'
        for child in children:
            child.stdin.write('claim\n')
            child.stdin.flush()
        results = []
        for child in children:
            out, err = child.communicate(timeout=15)
            assert child.returncode == 0, err
            results.append(out.strip())
        assert sorted(results) == ['claimed', 'refused']
        states = [queue.get_raw(identity)['status'] for identity in ('first-racer', 'second-racer')]
        assert sorted(states) == ['approved', 'executing']
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


@pytest.mark.parametrize('after_commit', [False, True])
def test_process_death_keeps_policy_operation_audit_and_claim_atomic(claimed, after_commit):
    import subprocess
    import sys
    from dataclasses import replace
    from pathlib import Path
    root, _, queue, item = claimed
    other = replace(item, id='death-boundary', parameters={'title': 'Distinct death-boundary item'})
    queue.propose(other)
    queue.transition(other.id, 'approved', actor='owner', approved_by='owner')
    code = '''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from action_queue import ActionQueue,compute_payload_hash
from lib.action_policy import load_dispatch_policy
q=ActionQueue(Path(sys.argv[2]))
item=q.get('death-boundary')
policy=load_dispatch_policy(Path(sys.argv[3]),'reminder_create')
def die(sql):
    if sql.startswith('INSERT INTO action_dispatch_policies') and sys.argv[4]=='before':
        os._exit(23)
q._conn.set_trace_callback(die)
q.transition(item.id,'executing',actor='executor',expected_payload_hash=compute_payload_hash(item),
             operation_key='death-operation',dispatch_policy=(policy.root,policy.sha256))
os._exit(23)
'''
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    child = subprocess.run([sys.executable, '-c', code, str(scripts), str(queue._db_path),
        str(root), 'after' if after_commit else 'before'], capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr.decode()
    assert queue.get_raw(other.id)['status'] == ('executing' if after_commit else 'approved')
    for table in ('action_dispatch_policies', 'effect_operations'):
        assert bool(queue._conn.execute(f'SELECT 1 FROM {table} WHERE action_id=?', (other.id,)).fetchone()) is after_commit
    assert bool(queue._conn.execute("SELECT 1 FROM action_audit WHERE action_id=? AND to_status='executing'",
                                    (other.id,)).fetchone()) is after_commit
    assert queue._conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('limits', [{'max_per_hour': 0}, {'max_per_day': 1}])
def test_zero_and_daily_limits_fail_closed_without_consuming_a_claim(claimed, limits):
    from dataclasses import replace
    from lib.action_policy import load_dispatch_policy
    root, path, queue, item = claimed
    document = yaml.safe_load(path.read_text())
    document['actions']['reminder_create']['rate_limit'] = limits
    path.write_text(yaml.safe_dump(document))
    policy = load_dispatch_policy(root, item.action_type)
    other = replace(item, id='limited', parameters={'title': 'Limited item'})
    queue.propose(other)
    queue.transition(other.id, 'approved', actor='owner', approved_by='owner')
    with pytest.raises(ValueError, match='limit'):
        _claim(queue, other, policy)
    assert queue.get_raw(other.id)['status'] == 'approved'


def test_policy_snapshot_currentness_and_approver_are_explicit(claimed):
    from lib.action_policy import authorize_dispatch_policy, load_dispatch_policy, require_current_policy
    root, _, _, item = claimed
    policy = load_dispatch_policy(root, item.action_type)
    require_current_policy(root, policy.sha256)
    with pytest.raises(ValueError, match='changed'):
        require_current_policy(root, '0' * 64)
    for approver in ('', None):
        with pytest.raises(ValueError, match='approver'):
            authorize_dispatch_policy(policy, item, approver, datetime.now(timezone.utc))


def test_archived_attempts_remain_in_current_rate_limit_history(claimed):
    from actions.base import ActionResult
    _, _, queue, item = claimed
    queue.record_result(item.id, ActionResult('success', 'synthetic', None, False, None),
                        datetime.now(timezone.utc).isoformat())
    with queue._conn:
        queue._conn.execute('INSERT INTO actions_archive SELECT * FROM actions WHERE id=?', (item.id,))
        queue._conn.execute('DELETE FROM actions WHERE id=?', (item.id,))
    # One start and one success still represent one attempt after the action is archived.
    queue._check_dispatch_limits(item.action_type, {'max_per_hour': 1})
    with pytest.raises(ValueError, match='limit'):
        queue._check_dispatch_limits(item.action_type, {'max_per_hour': 0})
