"""RA-05/24: provider-version preconditions are one registry; no action type silently skips them."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from action_queue import ActionQueue, compute_payload_hash
from actions import _HANDLER_MAP
from catchup_core.actions import ClaimRefused
from lib import action_evidence
from lib.action_policy import verify_action_preconditions
from test_action_process import proposal

VERSIONED = {'provider_versions': {'e1': '"v1"'}}
CORE = {'core_derived': True, 'fact_versions': {'f1': datetime.now(UTC).isoformat()}, 'subjects': ['adult'],
        'permission_generation': 1, 'fresh_until': (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
CALENDAR = {'event_id': 'e1', 'expected_etag': '"v1"', 'updates': {'summary': 'x'}}


def calendar_item(**extra):
    return replace(proposal(), action_type='calendar_modify', parameters={**CALENDAR, **extra})


@pytest.fixture
def queue(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'config').mkdir(parents=True)
    (root / 'config/actions.yaml').write_text(yaml.safe_dump({'live_trial': {'held': []}, 'actions': {
        name: {'enabled': True} for name in _HANDLER_MAP}}))
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(tmp_path / 'actions.db'))
    q = ActionQueue(root)
    yield q
    q.close()


def test_given_a_reviewed_event_version_when_approved_and_claimed_then_the_queue_accepts_it(queue):
    item = calendar_item(preconditions=VERSIONED)
    queue.propose(item)
    queue.mark_preview_shown(item.id, shown_by='owner', payload_hash=compute_payload_hash(item))
    queue.transition(item.id, 'approved', actor='owner', approved_by='owner')
    queue.transition(item.id, 'executing', actor='executor', expected_payload_hash=compute_payload_hash(item),
                     operation_key='operation')
    assert queue.get_raw(item.id)['status'] == 'executing'


@pytest.mark.parametrize('action_type', sorted(set(_HANDLER_MAP) - {'calendar_modify'}))
def test_given_a_type_with_no_conditional_write_when_a_version_is_declared_then_it_refuses(action_type):
    item = replace(proposal(), action_type=action_type, parameters={'preconditions': VERSIONED})
    with pytest.raises(ClaimRefused, match='unsupported'):
        action_evidence.require_provider_scope(item)
    ok, reason = verify_action_preconditions(item, root=None)
    assert not ok


@pytest.mark.parametrize('action_type', sorted(set(_HANDLER_MAP) - {'calendar_modify'}))
def test_given_a_queue_row_when_an_unsupported_type_declares_versions_then_approval_refuses(queue, action_type):
    item = replace(proposal(), action_type=action_type, parameters={'preconditions': VERSIONED})
    # Proposal time sees the exact parameters and refuses before anything is stored.
    with pytest.raises(ValueError, match='unsupported'):
        queue.propose(item)


def test_given_a_core_derived_calendar_change_when_no_version_is_bound_then_it_refuses():
    item = replace(calendar_item(preconditions=CORE), source_skill='catchup_core')
    with pytest.raises(ClaimRefused, match='must bind'):
        action_evidence.require_provider_scope(item)


def test_given_a_row_without_readable_parameters_when_checked_then_only_the_type_is_validated():
    assert action_evidence.require_provider_type('calendar_modify') == 'actions.calendar_modify'
    for name in ('slack_send', 'nonexistent'):
        with pytest.raises(ClaimRefused):
            action_evidence.require_provider_type(name)


def test_given_two_processes_racing_for_one_versioned_claim_when_the_winner_dies_then_no_replay(queue):
    import subprocess
    import sys
    from pathlib import Path
    item = calendar_item(preconditions=VERSIONED)
    queue.propose(item)
    queue.mark_preview_shown(item.id, shown_by='owner', payload_hash=compute_payload_hash(item))
    queue.transition(item.id, 'approved', actor='owner', approved_by='owner')
    code = '''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from action_queue import ActionQueue,compute_payload_hash
q=ActionQueue(Path(sys.argv[2]))
item=q.get(sys.argv[3])
print('ready',flush=True)
sys.stdin.readline()
try:
    q.transition(item.id,'executing',actor='executor',expected_payload_hash=compute_payload_hash(item),
                 operation_key='operation-'+sys.argv[4])
except ValueError:
    print('refused',flush=True)
    sys.exit(0)
print('claimed',flush=True)
os._exit(23)
'''
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    children = [subprocess.Popen([sys.executable, '-c', code, str(scripts), str(queue._artha_dir), item.id, name],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for name in ('a', 'b')]
    try:
        for child in children:
            assert child.stdout.readline().strip() == 'ready', child.stderr.read()
        for child in children:
            child.stdin.write('go\n')
            child.stdin.flush()
        results = sorted(child.stdout.readline().strip() for child in children)
        for child in children:
            child.communicate(timeout=15)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    assert results == ['claimed', 'refused']
    assert queue.get_raw(item.id)['status'] == 'executing'
    with pytest.raises(ValueError):
        queue.transition(item.id, 'executing', actor='executor',
                         expected_payload_hash=compute_payload_hash(item), operation_key='operation-replay')
    assert queue._conn.execute('SELECT count(*) FROM effect_operations WHERE action_id=?', (item.id,)).fetchone()[0] == 1
