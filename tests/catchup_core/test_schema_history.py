"""Migration history and actual schema must agree; synthetic databases only."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import ledger as lg
from test_ledger import KEY

LATEST = lg.Ledger.available_migrations()[-1][0]


@pytest.fixture
def scripts(tmp_path, monkeypatch):
    directory = tmp_path / 'migrations'
    shutil.copytree(lg._MIGRATIONS_DIR, directory)
    monkeypatch.setattr(lg, '_MIGRATIONS_DIR', directory)
    return directory


def backup(path):
    target = path.with_suffix('.backup')
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    return target


def legacy(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for number, source in lg.Ledger.available_migrations():
            if number <= 14:
                db.executescript(source.read_text())
                db.execute(f'PRAGMA user_version={number}')
        db.execute('CREATE TABLE unrelated_legacy(value TEXT)')
        db.execute("INSERT INTO unrelated_legacy VALUES('preserve')")
        db.commit()


def test_fresh_migrations_record_exact_files_and_are_idempotent(tmp_path, scripts):
    with lg.Ledger(tmp_path / 'fresh.db', id_key=KEY) as led:
        latest = led.migrate()
        rows = [dict(row) for row in led.conn.execute('SELECT * FROM schema_history ORDER BY number')]
        assert len(rows) == latest and all(row['provenance'] == 'applied' for row in rows)
        import hashlib
        for row in rows:
            assert row['sha256'] == hashlib.sha256((scripts / row['filename']).read_bytes()).hexdigest()
            assert row['verified_at']
        assert led.migrate() == latest
        assert [dict(row) for row in led.conn.execute('SELECT * FROM schema_history ORDER BY number')] == rows


def test_legacy_adoption_verifies_shape_without_claiming_historical_execution(tmp_path, scripts):
    path = tmp_path / 'legacy.db'
    legacy(path)
    with lg.Ledger(path, id_key=KEY) as led:
        with pytest.raises(lg.MigrationRefused, match='backup'):
            led.migrate()
        assert led.schema_version() == 14
        led.migrate(backup=backup)
        rows = led.conn.execute('SELECT number,provenance FROM schema_history ORDER BY number').fetchall()
        assert [(row[0], row[1]) for row in rows[:14]] == [(n, 'legacy_schema_verified') for n in range(1, 15)]
        assert all(row[1] == 'applied' for row in rows[14:])
        assert led.conn.execute('SELECT value FROM unrelated_legacy').fetchone()[0] == 'preserve'


@pytest.mark.parametrize('change', ['bytes', 'syntax', 'encoding', 'rename', 'remove', 'duplicate'])
def test_changed_applied_migration_cannot_be_hidden_by_the_version_number(tmp_path, scripts, change):
    path = tmp_path / 'journaled.db'
    with lg.Ledger(path, id_key=KEY) as led:
        led.migrate()
        source = scripts / '0002_pipeline.sql'
        if change == 'bytes':
            source.write_text(source.read_text() + '\n-- different bytes\n')
        elif change == 'syntax':
            source.write_text('invalid SQL;')
        elif change == 'encoding':
            source.write_bytes(b'\xff')
        elif change == 'rename':
            source.rename(scripts / '0002_renamed.sql')
        elif change == 'remove':
            source.unlink()
        else:
            shutil.copyfile(source, scripts / '0002_duplicate.sql')
        with pytest.raises(lg.MigrationRefused):
            led.migrate()
    with pytest.raises(lg.MigrationRefused):
        lg.Ledger(path, id_key=KEY).open(read_only=True)


@pytest.mark.parametrize('journaled', [False, True])
@pytest.mark.parametrize('change', [
    'DROP TRIGGER fact_generation_insert', 'DROP INDEX facts_lookup',
    'ALTER TABLE facts ADD COLUMN unreviewed TEXT',
    'CREATE TRIGGER unexpected AFTER INSERT ON facts BEGIN DELETE FROM facts; END',
    'CREATE UNIQUE INDEX unexpected ON facts(predicate)',
])
def test_changed_core_schema_refuses_adoption_or_journaled_reopen(tmp_path, scripts, journaled, change):
    path = tmp_path / 'drift.db'
    legacy(path)
    if journaled:
        with lg.Ledger(path, id_key=KEY) as led:
            led.migrate(backup=backup)
    with closing(sqlite3.connect(path)) as db:
        db.execute(change)
        db.commit()
    with pytest.raises(lg.MigrationRefused, match='schema'):
        with lg.Ledger(path, id_key=KEY) as led:
            led.migrate(backup=backup)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == (LATEST if journaled else 14)


@pytest.mark.parametrize('change', ['delete', 'future', 'checksum', 'provenance', 'missing-table'])
def test_incomplete_or_inconsistent_history_refuses_read_only_open(tmp_path, scripts, change):
    path = tmp_path / 'broken.db'
    with lg.Ledger(path, id_key=KEY) as led:
        led.migrate()
    with closing(sqlite3.connect(path)) as db:
        statement = {
            'delete': 'DELETE FROM schema_history WHERE number=1',
            'future': 'PRAGMA user_version=999',
            'checksum': "UPDATE schema_history SET sha256='wrong' WHERE number=1",
            'provenance': "UPDATE schema_history SET provenance='legacy_schema_verified' WHERE number=15",
            'missing-table': 'DROP TABLE schema_history',
        }[change]
        db.execute(statement)
        db.commit()
    if change == 'provenance':
        # The journal's own creation is never an inherited pre-journal assertion.
        match = 'history'
    else:
        match = 'history|migration|schema'
    with pytest.raises(lg.MigrationRefused, match=match):
        lg.Ledger(path, id_key=KEY).open(read_only=True)


def test_read_only_verification_never_repairs_missing_history(tmp_path, scripts):
    path = tmp_path / 'missing.db'
    with lg.Ledger(path, id_key=KEY) as led:
        led.migrate()
        led.conn.execute('DELETE FROM schema_history WHERE number=1')
    with pytest.raises(lg.MigrationRefused):
        lg.Ledger(path, id_key=KEY).open(read_only=True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT count(*) FROM schema_history WHERE number=1').fetchone()[0] == 0


def test_integrity_preflight_detects_schema_drift_after_dependency_construction(tmp_path, scripts):
    with lg.Ledger(tmp_path / 'preflight.db', id_key=KEY) as led:
        led.migrate()
        led.conn.execute('DROP TRIGGER fact_generation_insert')
        assert led.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not led.integrity_check()


@pytest.mark.parametrize('case', ['none', 'missing', 'premature-journal'])
def test_missing_sources_or_premature_journal_cannot_be_adopted(tmp_path, scripts, monkeypatch, case):
    with lg.Ledger(tmp_path / 'invalid.db', id_key=KEY) as led:
        if case == 'none':
            monkeypatch.setattr(lg.Ledger, 'available_migrations', staticmethod(lambda: []))
        elif case == 'missing':
            monkeypatch.setattr(lg.Ledger, 'available_migrations',
                                staticmethod(lambda: [(1, scripts / 'missing.sql')]))
        else:
            led.conn.execute('CREATE TABLE schema_history(x)')
        with pytest.raises(lg.MigrationRefused):
            led.migrate(backup=backup)
        assert led.schema_version() == 0


@pytest.mark.parametrize('condition', ['transient', 'persistent', 'io', 'unsupported'])
def test_wal_startup_retries_only_bounded_contention_and_closes_failed_handles(tmp_path, monkeypatch, condition):
    connect = sqlite3.connect
    handles, attempts, busy_limits = [], [], []
    class Connection(sqlite3.Connection):
        was_closed = False
        def execute(self, statement, *args, **kwargs):
            if statement == 'PRAGMA journal_mode=WAL':
                attempts.append(statement)
                busy_limits.append(super().execute('PRAGMA busy_timeout').fetchone()[0])
                if condition == 'unsupported':
                    return super().execute('PRAGMA journal_mode=DELETE')
                if condition != 'transient' or len(attempts) == 1:
                    error = sqlite3.OperationalError('synthetic setup failure')
                    error.sqlite_errorcode = sqlite3.SQLITE_IOERR if condition == 'io' else sqlite3.SQLITE_BUSY
                    raise error
            return super().execute(statement, *args, **kwargs)
        def close(self):
            self.was_closed = True
            return super().close()
    def factory(*args, **kwargs):
        handle = connect(*args, **kwargs, factory=Connection)
        handles.append(handle)
        return handle
    monkeypatch.setattr(lg.sqlite3, 'connect', factory)
    if condition == 'persistent':
        stamps = iter([0, 0, 10, 10])
        monkeypatch.setattr(lg, 'time', SimpleNamespace(monotonic=lambda: next(stamps), sleep=lambda _: None),
                            raising=False)
    ledger = lg.Ledger(tmp_path / 'startup.db', id_key=KEY)
    if condition == 'transient':
        with ledger:
            assert ledger.conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            assert ledger.conn.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
        assert len(attempts) == 2
    else:
        with pytest.raises(lg.LedgerError if condition == 'unsupported' else sqlite3.OperationalError):
            ledger.open()
        with pytest.raises(lg.LedgerError, match='not open'):
            ledger.conn
        assert len(attempts) == (2 if condition == 'persistent' else 1)
    assert handles and all(handle.was_closed for handle in handles)
    assert all(limit <= 50 for limit in busy_limits)


_WORKER = '''
import os, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[5])
from catchup_core import ledger as lg, ids
dbpath, marker, phase, scripts = map(str, sys.argv[1:5])
lg._MIGRATIONS_DIR = Path(scripts)
connect = sqlite3.connect
class Connection(sqlite3.Connection):
    armed = False
    def execute(self, sql, *args, **kwargs):
        result = super().execute(sql, *args, **kwargs)
        if sql == 'PRAGMA user_version=15':
            self.armed = True
        if self.armed and ((phase == 'before' and sql == 'PRAGMA user_version=15')
                           or (phase == 'after' and sql == 'COMMIT')):
            Path(marker).write_text(phase)
            while True: time.sleep(0.1)
        return result
def factory(*args, **kwargs):
    kwargs['factory'] = Connection
    return connect(*args, **kwargs)
sqlite3.connect = factory
def backup(path):
    destination = path.with_suffix('.backup-' + str(os.getpid()))
    with connect(path) as source, connect(destination) as target:
        source.backup(target)
    if phase == 'race':
        Path(marker + '.' + str(os.getpid())).touch()
        deadline = time.monotonic() + 10
        while len(list(Path(marker).parent.glob(Path(marker).name + '.*'))) < 2:
            if time.monotonic() > deadline: raise RuntimeError('missing migration peer')
            time.sleep(0.01)
    return destination
with lg.Ledger(Path(dbpath), id_key=ids.HmacKey(1, b'i' * 32)) as led:
    led.migrate(backup=backup)
'''


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_process_death_keeps_schema_history_and_version_atomic(tmp_path, scripts, phase):
    path, marker = tmp_path / 'crash.db', tmp_path / 'paused'
    legacy(path)
    with subprocess.Popen([sys.executable, '-c', _WORKER, str(path), str(marker), phase, str(scripts),
                           str(Path(lg.__file__).parents[1])],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert marker.exists(), process.communicate(timeout=1)
        finally:
            process.kill()
            process.communicate(timeout=5)
        assert process.returncode != 0
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA user_version').fetchone()[0] == (14 if phase == 'before' else 15)
        journal = db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_history'").fetchone()
        assert bool(journal) == (phase == 'after')
        if journal:
            assert db.execute('SELECT count(*) FROM schema_history').fetchone()[0] == 15
    with lg.Ledger(path, id_key=KEY) as led:
        assert led.migrate(backup=backup) == LATEST
        assert led.conn.execute('SELECT count(*) FROM schema_history').fetchone()[0] == LATEST
        assert led.conn.execute('SELECT value FROM unrelated_legacy').fetchone()[0] == 'preserve'


@pytest.mark.parametrize('_attempt', range(4))
def test_two_real_migration_processes_converge_without_duplicate_history(tmp_path, scripts, _attempt):
    path = tmp_path / 'race.db'
    legacy(path)
    children = [subprocess.Popen([sys.executable, '-c', _WORKER, str(path), str(tmp_path / 'rendezvous'),
                                 'race', str(scripts), str(Path(lg.__file__).parents[1])],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        for process in children:
            _, error = process.communicate(timeout=20)
            assert process.returncode == 0, error.decode()
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    with lg.Ledger(path, id_key=KEY) as led:
        assert led.schema_version() == LATEST
        assert led.conn.execute('SELECT count(*) FROM schema_history').fetchone()[0] == LATEST


def test_source_time_upgrade_preserves_unknown_history_instead_of_copying_current_time(tmp_path, monkeypatch):
    available = lg.Ledger.available_migrations()
    path = tmp_path / 'prior.db'
    with monkeypatch.context() as patch:
        patch.setattr(lg.Ledger, 'available_migrations', staticmethod(lambda: available[:15]))
        with lg.Ledger(path, id_key=KEY) as led:
            led.migrate()
            led.conn.execute("INSERT INTO facts(fact_id,domain,predicate,typed_value,primary_subject_id,"
                "confidentiality,observed_at,occurred_at,recorded_at,provenance_class,status) "
                "VALUES('fct_old','kids_school','test_date','1','kid','ordinary','2026-10-13T00:00:00Z',"
                "'2026-10-12T00:00:00Z','2026-10-14T00:00:00Z','verified_source','current')")
            for stamp in ('2026-10-11T00:00:00Z', '2026-10-14T00:00:00Z'):
                led.conn.execute('INSERT INTO fact_versions SELECT fact_id,?,value_encrypted,key_version,'
                    "valid_from,valid_to,validity_unknown,provenance_class,'digest',typed_value,status,rule_version "
                    'FROM facts', (stamp,))
    with lg.Ledger(path, id_key=KEY) as led:
        led.migrate(backup=backup)
        history = led.fact_history('fct_old', 'owner')
        assert history[0]['observed_at'] is None and history[0]['occurred_at'] is None
        assert history[1]['observed_at'] == '2026-10-13T00:00:00Z'
        assert history[1]['occurred_at'] == '2026-10-12T00:00:00Z'
