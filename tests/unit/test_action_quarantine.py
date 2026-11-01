"""RA-05: historical uncertainty must survive upgrade and block regenerated effects."""
from __future__ import annotations

import dataclasses
from contextlib import closing
import sqlite3

import pytest

from action_queue import ActionQueue
from actions.base import ActionProposal


def proposal(pid='old'):
    return ActionProposal(pid,'reminder_create','home','Inspect','',{'title':'Inspect'},
                          'standard',1,'standard',False,None,None)


def seed(root, *, status='failed', message='connection reset after request', archived=False, claim=False):
    queue=ActionQueue(root)
    queue.propose(proposal())
    with queue._conn:
        queue._conn.execute('DROP TABLE IF EXISTS action_migrations')
        queue._conn.execute('UPDATE actions SET status=?,result_status=?,result_message=?,payload_hash=NULL,'
                            'idempotency_key=NULL,idempotency_expires_at=?',
                            (status,'partial' if status=='failed' else None,message,'2000-01-01T00:00:00+00:00'))
        if claim:
            queue._conn.execute("UPDATE actions SET payload_hash='bound',approved_payload_hash='bound'")
            queue._conn.execute("INSERT INTO effect_operations VALUES('op','old','bound','executing',NULL)")
        if archived:
            queue._conn.execute('INSERT INTO actions_archive SELECT * FROM actions')
            queue._conn.execute('DELETE FROM actions')
    queue.close()


@pytest.mark.parametrize('archived',[False,True])
def test_uncertain_history_reenters_active_queue_and_blocks_regeneration(tmp_path,archived):
    seed(tmp_path,archived=archived)
    with closing(ActionQueue(tmp_path)) as queue:
        row=queue.get_raw('old')
        assert row and row['status']=='unknown'
        assert row['result_message']=='connection reset after request'
        assert queue.find_live_duplicate(dataclasses.replace(proposal(),id='new'))['id']=='old'
        with pytest.raises(ValueError,match='Duplicate'):
            queue.propose(dataclasses.replace(proposal(),id='new'))
        assert not queue._conn.execute("SELECT 1 FROM actions_archive WHERE id='old'").fetchone()
        assert queue._conn.execute("SELECT 1 FROM action_audit WHERE action_id='old' AND to_status='unknown'").fetchone()
        queue.transition('old','failed',actor='user:reconcile')
    with closing(ActionQueue(tmp_path)) as reopened:
        assert reopened.get_raw('old')['status']=='failed'
        reopened.propose(dataclasses.replace(proposal(),id='new'))


def test_unbound_approval_loses_authority_and_old_execution_becomes_unknown(tmp_path):
    seed(tmp_path,status='approved',message=None)
    with closing(ActionQueue(tmp_path)) as queue:
        assert queue.get_raw('old')['status']=='pending'
        assert queue.get_raw('old')['approved_payload_hash'] is None
    other=tmp_path/'other'
    seed(other,status='executing',message=None)
    with closing(ActionQueue(other)) as queue:
        assert queue.get_raw('old')['status']=='unknown'


def test_valid_inflight_claim_is_preserved_across_upgrade(tmp_path):
    seed(tmp_path,status='executing',message=None,claim=True)
    with closing(ActionQueue(tmp_path)) as queue:
        assert queue.get_raw('old')['status']=='executing'


def test_unknown_legacy_status_blocks_upgrade_without_silently_relabelling(tmp_path):
    seed(tmp_path,status='unrecognized',message=None)
    with pytest.raises(ValueError,match='legacy action status'):
        ActionQueue(tmp_path)
    with closing(sqlite3.connect(tmp_path/'state/actions.db')) as database, database:
        assert database.execute('SELECT status FROM actions').fetchone()[0]=='unrecognized'


def test_archive_preserves_the_original_audit_trail(tmp_path):
    with closing(ActionQueue(tmp_path)) as queue:
        queue.propose(proposal())
        queue.transition('old','rejected',actor='user:review')
        queue._conn.execute("UPDATE actions SET updated_at='2000-01-01T00:00:00+00:00'")
        queue._conn.commit()
        before=[tuple(row) for row in queue._conn.execute('SELECT * FROM action_audit ORDER BY id')]
        assert queue.archive_old_records()==1
        assert queue.get_raw('old') is None
        assert [tuple(row) for row in queue._conn.execute('SELECT * FROM action_audit ORDER BY id')]==before
        assert queue._conn.execute("SELECT status FROM actions_archive WHERE id='old'").fetchone()[0]=='rejected'


def test_upgrade_preserves_audit_rows_from_the_old_foreign_key_schema(tmp_path):
    seed(tmp_path)
    with closing(sqlite3.connect(tmp_path/'state/actions.db')) as db, db:
        db.execute('ALTER TABLE action_audit RENAME TO old_audit')
        db.execute('CREATE TABLE action_audit(id TEXT PRIMARY KEY,action_id TEXT NOT NULL REFERENCES actions(id),'
                   'timestamp TEXT NOT NULL,from_status TEXT NOT NULL,to_status TEXT NOT NULL,actor TEXT NOT NULL,context TEXT)')
        db.execute('INSERT INTO action_audit SELECT * FROM old_audit')
        db.execute('DROP TABLE old_audit')
        before=db.execute('SELECT * FROM action_audit').fetchall()
    with closing(ActionQueue(tmp_path)) as queue:
        assert not queue._conn.execute('PRAGMA foreign_key_list(action_audit)').fetchall()
        assert all(queue._conn.execute('SELECT 1 FROM action_audit WHERE id=?',(row[0],)).fetchone() for row in before)


def test_process_death_rolls_back_quarantine_and_upgrade_marker(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    seed(tmp_path)
    scripts=Path(__file__).resolve().parents[2]/'scripts'
    code='''import os,sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
import action_queue
opening=action_queue._open_db
def open_db(path):
    db=opening(path)
    db.create_function('die',0,lambda:os._exit(23))
    db.execute('CREATE TEMP TRIGGER kill_quarantine BEFORE UPDATE OF status ON actions BEGIN SELECT die(); END')
    return db
action_queue._open_db=open_db
action_queue.ActionQueue(Path(sys.argv[2]))
'''
    child=subprocess.run([sys.executable,'-c',code,str(scripts),str(tmp_path)],capture_output=True,timeout=15)
    assert child.returncode==23,child.stderr.decode()
    with closing(sqlite3.connect(tmp_path/'state/actions.db')) as database, database:
        assert database.execute('SELECT status FROM actions').fetchone()[0]=='failed'
        assert not database.execute('SELECT 1 FROM action_migrations').fetchone()
        assert not database.execute("SELECT 1 FROM action_audit WHERE actor='system:legacy_quarantine'").fetchone()
    with closing(ActionQueue(tmp_path)) as recovered:
        assert recovered.get_raw('old')['status']=='unknown'


def test_two_startups_commit_one_quarantine_audit(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    seed(tmp_path)
    scripts=Path(__file__).resolve().parents[2]/'scripts'
    code='''import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from action_queue import ActionQueue
print('ready',flush=True)
sys.stdin.readline()
q=ActionQueue(Path(sys.argv[2]))
q.close()
'''
    children=[subprocess.Popen([sys.executable,'-c',code,str(scripts),str(tmp_path)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    try:
        for child in children:
            assert child.stdout.readline().strip()=='ready'
        for child in children:
            child.stdin.write('go\n');child.stdin.flush()
        for child in children:
            out,err=child.communicate(timeout=15)
            assert child.returncode==0,out+err
        with closing(ActionQueue(tmp_path)) as queue:
            assert queue.get_raw('old')['status']=='unknown'
            assert queue._conn.execute("SELECT count(*) FROM action_audit WHERE actor='system:legacy_quarantine'").fetchone()[0]==1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill();child.communicate(timeout=5)


def test_upgrade_creates_a_verified_private_recovery_snapshot(tmp_path):
    seed(tmp_path)
    with closing(ActionQueue(tmp_path)) as queue:
        snapshots=list((tmp_path/'state').glob('actions.db.pre-quarantine-*.db'))
        assert len(snapshots)==1
        with closing(sqlite3.connect(snapshots[0])) as old, old:
            assert old.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert old.execute('SELECT status FROM actions').fetchone()[0]=='failed'
        assert queue.get_raw('old')['status']=='unknown'
    with closing(ActionQueue(tmp_path)):
        assert len(list((tmp_path/'state').glob('actions.db.pre-quarantine-*.db')))==1


def test_failed_backup_blocks_any_quarantine_mutation(tmp_path,monkeypatch):
    import action_queue
    seed(tmp_path)
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(action_queue,'_migrate_legacy_database',fail)
    with pytest.raises(OSError,match='synthetic disk failure'):
        ActionQueue(tmp_path)
    with closing(sqlite3.connect(tmp_path/'state/actions.db')) as database, database:
        assert database.execute('SELECT status FROM actions').fetchone()[0]=='failed'
        assert not database.execute('SELECT 1 FROM action_migrations').fetchone()


def test_conclusive_archived_failure_stays_historical(tmp_path):
    seed(tmp_path,status='rejected',message=None,archived=True)
    with closing(ActionQueue(tmp_path)) as queue:
        assert queue.get_raw('old') is None
        assert queue._conn.execute("SELECT status FROM actions_archive WHERE id='old'").fetchone()[0]=='rejected'


@pytest.mark.parametrize('outcome',['partial','unrecognized'])
def test_live_uncertain_result_preserves_duplicate_protection(tmp_path,outcome):
    from actions.base import ActionResult
    with closing(ActionQueue(tmp_path)) as queue:
        queue.propose(proposal())
        queue.transition('old','approved',actor='user:terminal')
        queue.transition('old','executing',actor='system:executor')
        queue.record_result('old',ActionResult(outcome,'some effects are unverified',None,False,None),
                            '2026-10-21T00:00:00+00:00')
        assert queue.get_raw('old')['status']=='unknown'
        with pytest.raises(ValueError,match='Duplicate'):
            queue.propose(dataclasses.replace(proposal(),id='regenerated'))


def test_partial_sync_is_never_reported_as_full_success(monkeypatch):
    from actions import todo_sync_action
    def sync(args):
        if '--pull' in args:
            raise OSError('synthetic partial sync')
        return 'confirmed push'
    monkeypatch.setattr(todo_sync_action,'_run_todo_sync',sync)
    result=todo_sync_action.execute(dataclasses.replace(proposal(),action_type='todo_sync',parameters={'mode':'both'}))
    assert result.status=='partial' and result.data['errors']
