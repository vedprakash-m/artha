"""WP-03 / RA-05 durability: a power loss must not erase an acknowledged claim or outcome."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import action_queue  # noqa: E402

SYNCHRONOUS_FULL = 2   # SQLite: 0 OFF, 1 NORMAL, 2 FULL, 3 EXTRA


def test_connections_use_synchronous_full(tmp_path):
    conn = action_queue._open_db(tmp_path / "a.db")
    try:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == SYNCHRONOUS_FULL
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_legacy_local_migration_includes_committed_wal_rows(tmp_path, monkeypatch):
    import sqlite3
    root = tmp_path/'workspace'
    initial = action_queue.ActionQueue(root)
    initial.close()
    source = root/'state/actions.db'
    destination = tmp_path/'local/actions.db'
    writer = sqlite3.connect(source)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE migration_probe(value TEXT)')
        writer.execute("INSERT INTO migration_probe VALUES('committed in WAL')")
        writer.commit()
        assert source.with_name('actions.db-wal').stat().st_size > 0
        monkeypatch.setattr(action_queue.ActionQueue,'_resolve_db_path',staticmethod(lambda _: destination))
        queue = action_queue.ActionQueue(root)
        try:
            assert queue._conn.execute('SELECT value FROM migration_probe').fetchone()[0] == 'committed in WAL'
            assert writer.execute('SELECT value FROM migration_probe').fetchone()[0] == 'committed in WAL'
        finally:
            queue.close()
    finally:
        writer.close()


@pytest.mark.parametrize('after_publish', [False, True])
def test_interrupted_migration_never_publishes_a_partial_destination(tmp_path, after_publish):
    import sqlite3
    import subprocess
    source, destination = tmp_path/'legacy.db', tmp_path/'local.db'
    with sqlite3.connect(source) as database:
        database.execute('CREATE TABLE migration_probe(value TEXT)')
        database.execute("INSERT INTO migration_probe VALUES('retained')")
    code = '''import os,sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
import action_queue
link=action_queue.os.link
def crash(*args,**kwargs):
    if sys.argv[4]=='published':
        link(*args,**kwargs)
    os._exit(23)
action_queue.os.link=crash
action_queue._migrate_legacy_database(Path(sys.argv[2]),Path(sys.argv[3]))
'''
    child = subprocess.run([sys.executable,'-c',code,str(SCRIPTS),str(source),str(destination),
                            'published' if after_publish else 'unpublished'],capture_output=True,timeout=15)
    assert child.returncode == 23, child.stderr.decode()
    assert destination.exists() is after_publish
    action_queue._migrate_legacy_database(source,destination)
    with sqlite3.connect(destination) as database:
        assert database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert database.execute('SELECT value FROM migration_probe').fetchone()[0] == 'retained'
    if sys.platform != 'win32':
        assert destination.stat().st_mode & 0o077 == 0


def test_racing_migration_preserves_the_already_published_database(tmp_path):
    import sqlite3
    import subprocess
    source, destination = tmp_path/'legacy.db', tmp_path/'local.db'
    with sqlite3.connect(source) as database:
        database.execute('CREATE TABLE migration_probe(value TEXT)')
        database.execute("INSERT INTO migration_probe VALUES('original')")
    code = '''import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
import action_queue
link=action_queue.os.link
def publish(*args,**kwargs):
    print('ready',flush=True)
    sys.stdin.readline()
    return link(*args,**kwargs)
action_queue.os.link=publish
action_queue._migrate_legacy_database(Path(sys.argv[2]),Path(sys.argv[3]))
'''
    loser = subprocess.Popen([sys.executable,'-c',code,str(SCRIPTS),str(source),str(destination)],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert loser.stdout.readline().strip() == 'ready'
        action_queue._migrate_legacy_database(source,destination)
        with sqlite3.connect(destination) as winner:
            winner.execute("UPDATE migration_probe SET value='concurrent winner'")
        out, err = loser.communicate('publish\n',timeout=15)
        assert loser.returncode == 0, out+err
        with sqlite3.connect(destination) as winner:
            assert winner.execute('SELECT value FROM migration_probe').fetchone()[0] == 'concurrent winner'
        assert not list(tmp_path.glob('.actions-migrate-*'))
    finally:
        if loser.poll() is None:
            loser.kill()
            loser.communicate(timeout=5)


def test_invalid_legacy_database_leaves_no_migration_destination(tmp_path):
    import sqlite3
    source, destination = tmp_path/'invalid.db', tmp_path/'local.db'
    source.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError):
        action_queue._migrate_legacy_database(source,destination)
    assert not destination.exists() and not list(tmp_path.glob('.actions-migrate-*'))
    assert source.read_bytes() == b'not a SQLite database'


def test_migration_flushes_a_writable_snapshot_before_publication(tmp_path, monkeypatch):
    import os
    import sqlite3
    source, destination = tmp_path/'legacy.db', tmp_path/'local.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE probe(value TEXT)')
    original_open = Path.open
    opened = []
    original_link = os.link

    def snapshot_open(path, mode='r', *args, **kwargs):
        if path.name.startswith('.actions-migrate-'):
            opened.append(mode)
            assert '+' in mode, 'Windows fsync requires a writable file descriptor'
        return original_open(path, mode, *args, **kwargs)

    def publish(*args, **kwargs):
        assert opened == ['rb+']
        return original_link(*args, **kwargs)

    monkeypatch.setattr(Path, 'open', snapshot_open)
    monkeypatch.setattr(os, 'link', publish)
    action_queue._migrate_legacy_database(source, destination)
    assert destination.is_file()


def test_failed_snapshot_flush_refuses_publication(tmp_path, monkeypatch):
    import os
    import sqlite3
    source, destination = tmp_path/'legacy.db', tmp_path/'local.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE probe(value TEXT)')

    def refuse(*args):
        raise OSError('synthetic flush failure')

    monkeypatch.setattr(os, 'fsync', refuse)
    with pytest.raises(OSError, match='synthetic flush failure'):
        action_queue._migrate_legacy_database(source, destination)
    assert not destination.exists() and not list(tmp_path.glob('.actions-migrate-*'))
