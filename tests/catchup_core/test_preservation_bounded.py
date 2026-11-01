"""Bounded-memory capture and restore of large stores; synthetic data only (RA-20, completion row 1)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tracemalloc
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from catchup_core import checkpoint as ck
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider

CIPHER = EnvelopeCipher(StaticKeyProvider({1: b'b' * 32}))
NOW = datetime(2026, 10, 24, tzinfo=UTC)
PASSPHRASE = 'correct horse battery staple'


def big_database(path: Path, megabytes: int, *, wal: bool = False) -> None:
    with closing(sqlite3.connect(path)) as db:
        if wal:
            db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE content(id INTEGER PRIMARY KEY, body BLOB)')
        block = os.urandom(1 << 20)
        for index in range(megabytes):
            db.execute('INSERT INTO content(body) VALUES(?)', (block[:-8] + index.to_bytes(8, 'big'),))
        db.commit()


def capture(root: Path, out: Path) -> Path:
    return ck.create('personal', {'local': root}, out, CIPHER, {}, PASSPHRASE, now=NOW, lock_path=root.parent / 'lock')


def test_large_database_is_captured_and_restored_without_a_whole_image_in_memory(tmp_path, monkeypatch):
    """Given a database larger than the in-memory image limit, when captured and restored, then memory stays bounded."""
    root = tmp_path / 'src'
    root.mkdir()
    big_database(root / 'ledger.db', 24)
    monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 2 << 20)
    monkeypatch.setattr(ck, '_CHUNK', 1 << 20)
    tracemalloc.start()
    try:
        ack = capture(root, tmp_path / 'out')
        report = ck.restore(ack, tmp_path / 'restored', CIPHER, [], replay_privacy=lambda _: None)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 10 << 20  # the database is 24 MiB
    assert report['verified_sqlite'] == 1
    with closing(sqlite3.connect(tmp_path / 'restored/local/ledger.db')) as restored:
        assert restored.execute('SELECT count(*) FROM content').fetchone() == (24,)


def test_checkpointed_wal_database_streams_and_normalizes_journal_mode(tmp_path, monkeypatch):
    """Given a WAL database with an empty log, when captured, then the restored copy is a plain database."""
    root = tmp_path / 'src'
    root.mkdir()
    big_database(root / 'ledger.db', 3, wal=True)
    with closing(sqlite3.connect(root / 'ledger.db')) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 1 << 20)
    ack = capture(root, tmp_path / 'out')
    ck.restore(ack, tmp_path / 'restored', CIPHER, [], replay_privacy=lambda _: None)
    with closing(sqlite3.connect(tmp_path / 'restored/local/ledger.db')) as restored:
        assert restored.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        assert restored.execute('SELECT count(*) FROM content').fetchone() == (3,)


def test_writer_commit_during_streaming_blocks_instead_of_publishing_a_torn_image(tmp_path, monkeypatch):
    """Given a WAL writer in another process, when it commits mid-stream, then the capture is refused."""
    path = tmp_path / 'live.db'
    big_database(path, 3, wal=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    monkeypatch.setattr(ck, '_CHUNK', 1 << 16)
    chunks = ck._sqlite_chunks(path)
    next(chunks)
    child = subprocess.run([sys.executable, '-c',
        "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA wal_autocheckpoint=0'); "
        "db.execute('INSERT INTO content(body) VALUES(x\\'00\\')'); db.commit(); db.close()", str(path)],
        capture_output=True, timeout=20)
    assert child.returncode == 0, child.stderr.decode()
    with pytest.raises(ck.CheckpointBlocked, match='changed during capture'):
        for _ in chunks:
            pass


def test_uncheckpointed_log_falls_back_to_the_bounded_image_and_refuses_oversized(tmp_path, monkeypatch):
    """Given committed pages only in the log, when over the image limit, then capture refuses before allocation."""
    path = tmp_path / 'wal.db'
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('CREATE TABLE content(body BLOB)')
    writer.execute('INSERT INTO content VALUES(?)', (b'x' * 65536,))
    writer.commit()
    try:
        monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 16384)
        with pytest.raises(ck.CheckpointBlocked, match='online backup limit'):
            ck._sqlite_sha(path)
    finally:
        writer.close()


def test_damaged_database_is_refused_while_streaming(tmp_path):
    """Given a database failing its integrity check, when captured, then nothing is published."""
    root = tmp_path / 'src'
    root.mkdir()
    big_database(root / 'ledger.db', 2)
    with (root / 'ledger.db').open('r+b') as handle:
        handle.seek(4096 * 3)
        handle.write(b'\xff' * 4096)
    with pytest.raises((ck.CheckpointBlocked, sqlite3.DatabaseError)):
        capture(root, tmp_path / 'out')
    assert not list((tmp_path / 'out').glob('*.ack')) if (tmp_path / 'out').exists() else True


def test_reconcile_reports_oversized_table_comparison_as_unavailable_not_clean(tmp_path, monkeypatch):
    """Given a changed database over the table-comparison limit, then file change is shown and tables stay unknown."""
    root = tmp_path / 'src'
    root.mkdir()
    big_database(root / 'ledger.db', 2)
    ack = capture(root, tmp_path / 'out')
    with closing(sqlite3.connect(root / 'ledger.db')) as db, db:
        db.execute('DELETE FROM content WHERE id=1')
    monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 1 << 20)
    drift = ck.reconcile(ack, CIPHER, {'local': root})
    assert drift['changed'] == ['local/ledger.db']
    assert drift['tables'] == {}
    assert drift['tables_unavailable'] == ['local/ledger.db']


@pytest.mark.parametrize('name', ['clock: 9.txt', 'trailing.', 'con.txt', 'COM¹.log', 'pipe|name', 'trailing '])
def test_names_a_restore_platform_cannot_hold_block_capture_before_any_ciphertext(tmp_path, name):
    """Given a live file a Windows restore would mangle, when captured, then inventory blocks and writes nothing."""
    root = tmp_path / 'src'
    root.mkdir()
    (root / 'ok.txt').write_text('fine')
    try:
        path = root / name
        if sys.platform == 'win32' and name.endswith(('.', ' ')):
            path = Path('\\\\?\\' + str(path))
        path.write_text('x')
    except OSError:
        pytest.skip('this host cannot create the name')
    with pytest.raises(ck.CheckpointBlocked, match='inventory incomplete'):
        capture(root, tmp_path / 'out')
    assert not (tmp_path / 'out').exists() or not any((tmp_path / 'out').iterdir())


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
@pytest.mark.parametrize('member', ['file', 'directory', 'root', 'file-root'])
def test_named_streams_block_capture_before_any_ciphertext(tmp_path, member):
    root = tmp_path / 'src'
    root.mkdir()
    path = root / 'notes.txt' if member in {'file', 'file-root'} else root / 'folder'
    if member in {'file', 'file-root'}:
        path.write_text('ordinary content')
    elif member == 'directory':
        path.mkdir()
    else:
        path = root
    stream = Path(str(path) + ':private')
    stream.write_bytes(b'historical content outside the ordinary directory listing')
    assert stream.read_bytes().startswith(b'historical content')
    source = path if member == 'file-root' else root
    with pytest.raises(ck.CheckpointBlocked, match='inventory incomplete'):
        capture(source, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
def test_stream_added_before_publication_blocks_capture(tmp_path):
    root = tmp_path / 'src'
    root.mkdir()
    path = root / 'notes.txt'
    path.write_text('ordinary content')
    with pytest.raises(ck.CheckpointBlocked, match='state changed during capture'):
        ck.create('personal', {'local': root}, tmp_path / 'out', CIPHER, {}, PASSPHRASE,
                  now=NOW, lock_path=tmp_path / 'lock',
                  before_publish=lambda: Path(str(path) + ':late').write_bytes(b''))
    assert not list((tmp_path / 'out').iterdir())


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
def test_unavailable_stream_inventory_blocks_capture_without_fallback(tmp_path, monkeypatch):
    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError, _Native

    def unavailable(*_args, **_kwargs):
        raise QuarantineFilesystemError('synthetic unavailable stream metadata')

    root = tmp_path / 'src'
    root.mkdir()
    (root / 'notes.txt').write_text('ordinary content')
    monkeypatch.setattr(_Native, 'require_ordinary_stream', unavailable)
    with pytest.raises(ck.CheckpointBlocked, match='source stream inventory could not be verified'):
        capture(root, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='native NTFS stream inventory')
def test_oversized_stream_inventory_blocks_capture(tmp_path):
    root = tmp_path / 'src'
    root.mkdir()
    path = root / 'notes.txt'
    path.write_text('ordinary content')
    for index in range(150):
        Path(str(path) + ':' + str(index) + 'x' * 240).write_bytes(b'')
    with pytest.raises(ck.CheckpointBlocked, match='inventory incomplete'):
        capture(root, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('names', [
    ['Notes.txt', 'notes.txt'],                                   # case alias
    ['café.txt', 'café.txt'],                          # NFC and NFD of one name
    ['Docs/a.txt', 'docs/b.txt'],                                 # two spellings of one directory
    ['same', 'SAME/inner.txt'],                                   # file and directory alias
    ['straße.txt', 'STRASSE.txt'],                           # full case folding
])
def test_alias_paths_are_found_without_depending_on_the_host_file_system(names):
    """Given path spellings that collide on a case- or Unicode-insensitive restore, then the inventory names them."""
    found = {f'repo/{name}': Path(name) for name in names}
    problems = ck._portability_problems(found)
    assert len(problems) == 1 and 'alias' in problems[0]


def test_distinct_portable_names_pass_the_inventory_check():
    found = {f'repo/{name}': Path(name) for name in ['a/b.txt', 'a/c.txt', 'd.txt', '.hidden', 'x-1_2.md']}
    assert ck._portability_problems(found) == []


def test_log_appearing_after_the_read_transaction_starts_blocks_streaming(tmp_path, monkeypatch):
    """Given a log that gains committed pages between the first check and the read lock, then capture is refused."""
    path = tmp_path / 'late.db'
    big_database(path, 1)
    answers = iter([False, True])
    monkeypatch.setattr(ck, '_has_pending_log', lambda _path: next(answers))
    with pytest.raises(ck.CheckpointBlocked, match='changed during capture'):
        list(ck._sqlite_chunks(path))
