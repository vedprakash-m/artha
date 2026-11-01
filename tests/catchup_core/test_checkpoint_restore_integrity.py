"""Authenticated restore boundaries; synthetic ciphertext, files and databases only."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from catchup_core import checkpoint as ck
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider

CIPHER = EnvelopeCipher(StaticKeyProvider({1: b'r' * 32}))
GENERATION = b'ck_restore_guard'


def container(path, entries=None, *, profile='personal'):
    entries = entries if entries is not None else [('repo/a.txt', b'authorized baseline', 'file', 0o600),
                                                   ('repo/z.txt', b'last baseline', 'file', 0o600)]
    recorded = []
    with path.open('wb') as handle:
        handle.write(b'ACK2' + struct.pack('>H', len(GENERATION)) + GENERATION + struct.pack('>I', 0))
        writer = ck._Writer(handle, CIPHER, GENERATION.decode())
        for name, body, kind, mode in entries:
            header = {'path': name, 'kind': kind, 'mode': mode}
            ending = {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
            writer.frame('h', json.dumps(header).encode())
            writer.frame('d', body)
            writer.frame('e', json.dumps(ending).encode())
            recorded.append({**header, **ending, 'source_sha256': ending['sha256']})
        writer.frame('m', json.dumps({'schema': 'artha.checkpoint.v2', 'generation': GENERATION.decode(),
            'profile': profile, 'escrow_sha256': hashlib.sha256(b'').hexdigest(), 'entries': recorded,
            'excluded': {}}).encode())
        writer.frame('z', json.dumps({'members': len(recorded)}).encode())
    return path


def restore(path, dest, **kwargs):
    return ck.restore(path, dest, CIPHER, [], replay_privacy=kwargs.pop('replay_privacy', lambda _: None), **kwargs)


def scan_spy(monkeypatch, *, on_scan=lambda _sink, _result: None):
    original = ck._scan
    released = []
    def observed(source, cipher, sink=None, opened=None, **kwargs):
        def output(header, body):
            released.append(body)
            sink(header, body)
        result = original(source, cipher, output if sink else None, opened, **kwargs)
        on_scan(sink, result)
        return result
    monkeypatch.setattr(ck, '_scan', observed)
    return released


@pytest.mark.parametrize('explicit', [True, False])
def test_wrong_profile_never_creates_destination_or_releases_plaintext(tmp_path, monkeypatch, explicit):
    path = container(tmp_path / 'work.ack', profile='work')  # legacy Work with the personal envelope
    released = scan_spy(monkeypatch)
    options = {'expected_profile': 'personal'} if explicit else {}
    with pytest.raises(ck.CheckpointBlocked, match='profile'):
        restore(path, tmp_path / 'restored', **options)
    assert not released
    assert not (tmp_path / 'restored').exists()


def test_late_authentication_failure_writes_no_destination_bytes(tmp_path, monkeypatch):
    path = container(tmp_path / 'broken.ack')
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 1
    path.write_bytes(blob)
    released = scan_spy(monkeypatch)
    with pytest.raises(ck.CheckpointError):
        restore(path, tmp_path / 'restored')
    assert released == []
    assert not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('damage', ['changed', 'missing', 'extra', 'symlink', 'hardlink'])
def test_physical_output_must_match_before_privacy_replay(tmp_path, monkeypatch, damage):
    path = container(tmp_path / 'baseline.ack')
    dest = tmp_path / 'restored'
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'authorized baseline')
    def mutate(sink, _manifest):
        if not sink:
            return
        target = dest / 'repo/a.txt'  # closed before the last member
        if damage == 'changed':
            target.write_bytes(b'same-size bad bytes')
        elif damage == 'missing':
            target.unlink()
        elif damage == 'extra':
            (dest / '.DS_Store').write_bytes(b'unaccounted')  # no cache exclusions during physical verification
        else:
            target.unlink()
            if damage == 'symlink':
                target.symlink_to(outside)
            else:
                os.link(outside, target)
    scan_spy(monkeypatch, on_scan=mutate)
    replayed = []
    with pytest.raises(ck.CheckpointError, match='restored'):
        restore(path, dest, replay_privacy=replayed.append)
    assert replayed == [] and not dest.exists()
    assert outside.read_bytes() == b'authorized baseline'


@pytest.mark.parametrize('bad_database', ['header', 'foreign-key'])
def test_authenticated_sqlite_still_needs_physical_integrity_checks(tmp_path, bad_database):
    if bad_database == 'header':
        body = b'not a SQLite database'
    else:
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE parent(id PRIMARY KEY); CREATE TABLE child(id REFERENCES parent(id)); '
                             'INSERT INTO child VALUES(99);')
            body = db.serialize()
    path = container(tmp_path / 'db.ack', [('repo/store.db', body, 'sqlite', 0o600)])
    seen = []
    with pytest.raises(ck.CheckpointError, match='database'):
        restore(path, tmp_path / 'restored', replay_privacy=seen.append)
    assert not seen and not (tmp_path / 'restored').exists()


def test_valid_database_and_files_are_verified_without_sidecars_before_replay(tmp_path):
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('CREATE TABLE records(value)')
        db.execute('INSERT INTO records VALUES(7)')
        db.commit()
        body = db.serialize()
    path = container(tmp_path / 'db.ack', [('repo/store.db', body, 'sqlite', 0o600)])
    def replay(dest):
        assert (dest / 'repo/store.db').read_bytes() == body
        assert not list(dest.rglob('*-wal')) and not list(dest.rglob('*-shm'))
    report = restore(path, tmp_path / 'restored', replay_privacy=replay)
    assert report['verified_bytes'] == len(body)
    assert report['verified_sqlite'] == 1


def test_safe_permission_normalization_is_reported_after_baseline_verification(tmp_path):
    path = container(tmp_path / 'mode.ack', [('repo/program', b'bytes', 'file', 0o4755)])
    report = restore(path, tmp_path / 'restored')
    restored = tmp_path / 'restored/repo/program'
    if sys.platform == 'win32':
        from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory
        with WindowsQuarantineDirectory.open(tmp_path / 'restored') as root:
            with root.open_directory('repo', owner_private=False) as repo:
                with repo.open_file('program', private=False) as opened:
                    assert opened.read(5) == b'bytes'
    else:
        assert restored.stat().st_mode & 0o7777 == 0o755
    assert any(entry['path'] == 'repo/program' and 'permission' in entry['reason'] for entry in report['transformed'])


def test_process_privacy_refusal_removes_staged_plaintext_and_preserves_ciphertext(tmp_path):
    body = b'SYNTHETIC-RESTRICTED-HISTORICAL-COPY'
    path = container(tmp_path / 'restricted.ack', [('repo/state/curated.md', body, 'file', 0o600)])
    original = path.read_bytes()
    dest = tmp_path / 'refused'
    script = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from catchup_core import checkpoint as ck, checkpoint_cli as cli
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
cipher = EnvelopeCipher(StaticKeyProvider({1: b'r' * 32}))
try:
    ck.restore(Path(sys.argv[2]), Path(sys.argv[3]), cipher, [], replay_privacy=cli._privacy_hook(None))
except ck.CheckpointBlocked as error:
    print(str(error))
    sys.exit(20)
'''
    child = subprocess.run([sys.executable, '-c', script, str(Path(ck.__file__).parents[1]), str(path), str(dest)],
                           capture_output=True, timeout=10)
    assert child.returncode == 20, child.stderr.decode()
    assert b'nonledger' in child.stdout and b'owner review' in child.stdout
    assert body not in child.stdout + child.stderr
    assert not dest.exists()
    assert path.read_bytes() == original
    assert ck.verify(path, CIPHER)['entries'][0]['sha256'] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize('name', ['repo/NUL', 'repo/con.txt', 'repo/COM1.log', 'repo/LPT²', 'repo/aux .txt',
                                 'repo/trailing.', 'repo/trailing ', 'repo/quest?ion', 'repo/a\x00b',
                                 'repo/line\nbreak', '.artha-restored.json', '.artha-restored.json.tmp/entry',
                                 'repo/CONIN$', 'repo/conout$.txt', 'repo/stream:data', 'repo/back\\slash'])
def test_unsafe_portable_names_refuse_before_any_plaintext_write(tmp_path, monkeypatch, name):
    path = container(tmp_path / 'names.ack', [(name, b'private bytes', 'file', 0o600)])
    released = scan_spy(monkeypatch)
    with pytest.raises(ck.CheckpointError, match='path'):
        restore(path, tmp_path / 'restored')
    assert not released and not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('names', [('repo/a', 'repo/a/b'), ('repo/a/b', 'repo/a'),
                                  ('repo/Folder/a', 'repo/folder/b'), ('repo/Café/a', 'repo/Cafe\u0301/b')])
def test_ambiguous_directory_tree_refuses_before_any_plaintext_write(tmp_path, monkeypatch, names):
    path = container(tmp_path / 'tree.ack', [(name, b'private bytes', 'file', 0o600) for name in names])
    released = scan_spy(monkeypatch)
    with pytest.raises(ck.CheckpointError, match='path'):
        restore(path, tmp_path / 'restored')
    assert not released and not (tmp_path / 'restored').exists()


def test_destination_directory_link_refuses_without_touching_its_target(tmp_path):
    path = container(tmp_path / 'baseline.ack')
    actual = tmp_path / 'actual'
    actual.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ck.CheckpointBlocked, match='destination'):
        restore(path, alias)
    assert alias.is_symlink() and not list(actual.iterdir())


def test_destination_cannot_be_an_ancestor_of_a_missing_live_store(tmp_path):
    path = container(tmp_path / 'baseline.ack')
    with pytest.raises(ck.CheckpointBlocked, match='live'):
        ck.restore(path, tmp_path / 'restored', CIPHER, [tmp_path / 'restored/future/live'], replay_privacy=lambda _: None)
    assert not (tmp_path / 'restored').exists()


def test_preflight_rechecks_destination_and_preserves_a_competing_file(tmp_path, monkeypatch):
    path = container(tmp_path / 'baseline.ack')
    dest = tmp_path / 'restored'
    def competitor(sink, _manifest):
        if sink is None:
            dest.mkdir()
            (dest / 'competitor.txt').write_text('preserve me')
    scan_spy(monkeypatch, on_scan=competitor)
    with pytest.raises(ck.CheckpointBlocked, match='new or empty'):
        restore(path, dest)
    assert (dest / 'competitor.txt').read_text() == 'preserve me'


def test_pathname_replacement_keeps_original_handle_and_ignores_substituted_file(tmp_path, monkeypatch):
    path = container(tmp_path / 'baseline.ack')
    evil = container(tmp_path / 'evil.ack', [('repo/a.txt', b'SUBSTITUTED-SECRET', 'file', 0o600),
                                             ('repo/z.txt', b'last baseline', 'file', 0o600)])
    original = path.read_bytes()
    attacks = []
    script = '''import sys
from pathlib import Path
try:
    Path(sys.argv[1]).replace(sys.argv[2])
except OSError as error:
    if sys.platform != "win32" or error.winerror not in (5, 32):
        raise
    print("sharing-denied", error.winerror)
    sys.exit(32)
print("replaced")
'''
    def swap_path(sink, _manifest):
        if sink is None:
            result = subprocess.run([sys.executable, '-c', script, str(evil), str(path)],
                                    capture_output=True, text=True, timeout=10)
            attacks.append(result)
            if result.returncode == 32:
                assert sys.platform == 'win32'
                assert result.stdout.strip() in ('sharing-denied 5', 'sharing-denied 32')
                assert path.read_bytes() == original and evil.exists()
            else:
                assert result.returncode == 0, result.stderr
                assert result.stdout.strip() == 'replaced' and not evil.exists()
    scan_spy(monkeypatch, on_scan=swap_path)
    dest = tmp_path / 'restored'
    restore(path, dest)
    assert (dest / 'repo/a.txt').read_bytes() == b'authorized baseline'
    assert len(attacks) == 1
    if attacks[0].returncode == 32:
        evil.replace(path)  # The identical attack works once restore releases the input handle.
        assert path.read_bytes() != original and not evil.exists()


@pytest.mark.parametrize('mode', ['in_place_frame', 'extra_fingerprint'])
def test_in_place_rewrite_after_preflight_refuses_before_releasing_substituted_plaintext(tmp_path, monkeypatch, mode):
    path = container(tmp_path / 'baseline.ack')
    evil = container(tmp_path / 'evil.ack', [('repo/a.txt', b'SUBSTITUTED-SECRET', 'file', 0o600),
                                             ('repo/z.txt', b'last baseline', 'file', 0o600)])
    evil_bytes = evil.read_bytes()
    released = []
    original = ck._scan
    def intercept(source, cipher, sink=None, opened=None, **kwargs):
        def output(header, body):
            released.append(body)
            sink(header, body)
        result = original(source, cipher, output if sink else None, opened, **kwargs)
        if sink is None and mode == 'in_place_frame':
            with path.open('r+b') as handle:
                handle.write(evil_bytes)
                handle.truncate()
                handle.flush()
        elif sink is None and mode == 'extra_fingerprint':
            kwargs['audit'](b'x' * 32)
        return result
    monkeypatch.setattr(ck, '_scan', intercept)
    dest = tmp_path / 'restored'
    with pytest.raises(ck.CheckpointError, match='changed after authenticated preflight'):
        restore(path, dest)
    assert b'SUBSTITUTED-SECRET' not in released
    assert not dest.exists()


def test_corrupt_sqlite_pages_with_valid_header_and_unreadable_restored_tree_refuse(tmp_path, monkeypatch):
    bad_sqlite = b'SQLite format 3\x00' + b'\xff' * 4080
    path = container(tmp_path / 'corrupt_pages.ack', [('repo/store.db', bad_sqlite, 'sqlite', 0o600)])
    with pytest.raises(ck.CheckpointError, match='database integrity'):
        restore(path, tmp_path / 'restored_db')
    assert not (tmp_path / 'restored_db').exists()

    valid = container(tmp_path / 'valid.ack')
    dest = tmp_path / 'restored_unreadable'
    scandir = os.scandir
    denied = []

    def unreadable(directory):
        if Path(directory) == dest / 'repo':
            denied.append(directory)
            raise PermissionError('synthetic native unreadable directory')
        return scandir(directory)

    def lock_subdir(sink, _manifest):
        if sink is not None:
            if sys.platform == 'win32':
                monkeypatch.setattr(os, 'scandir', unreadable)
            else:
                (dest / 'repo').chmod(0)
    scan_spy(monkeypatch, on_scan=lock_subdir)
    try:
        with pytest.raises(ck.CheckpointError, match='restored inventory cannot be read'):
            restore(valid, dest)
    finally:
        monkeypatch.setattr(os, 'scandir', scandir)
        if (dest / 'repo').exists():
            (dest / 'repo').chmod(0o700)
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
    if sys.platform == 'win32':
        assert denied


def test_invalid_inputs_and_competing_restore_marker_refuse_cleanly(tmp_path, monkeypatch):
    with pytest.raises(ck.CheckpointError, match='unavailable'):
        restore(tmp_path / 'nonexistent.ack', tmp_path / 'out')
    directory_input = tmp_path / 'dir.ack'
    directory_input.mkdir()
    with pytest.raises(ck.CheckpointBlocked, match='regular checkpoint file'):
        restore(directory_input, tmp_path / 'out')
    inside_dest = tmp_path / 'dest'
    inside_dest.mkdir()
    nested_ack = container(inside_dest / 'in.ack')
    with pytest.raises(ck.CheckpointBlocked, match='cannot contain its input'):
        restore(nested_ack, inside_dest)

    path = container(tmp_path / 'baseline.ack')
    dest = tmp_path / 'raced'
    monkeypatch.setattr(ck, '_restore_destination', lambda *_a: None)
    dest.mkdir()
    (dest / '.artha-restored.json').write_text('{}')
    with pytest.raises(ck.CheckpointBlocked, match='another restore owns'):
        restore(path, dest)


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows restore-directory custody')
def test_restore_refuses_an_existing_unsafe_directory_without_repairing_it(tmp_path):
    path = container(tmp_path / 'baseline.ack')
    original = path.read_bytes()
    dest = tmp_path / 'existing'
    dest.mkdir()
    with pytest.raises(ck.CheckpointBlocked, match='filesystem is unavailable or unsafe'):
        restore(path, dest)
    assert dest.is_dir() and not list(dest.iterdir())
    assert path.read_bytes() == original


def test_killed_restore_process_leaves_only_inactive_marked_tree(tmp_path):
    import time
    from catchup_core.session_lock import require_active_installation
    path = container(tmp_path / 'baseline.ack')
    dest = tmp_path / 'killed_restore'
    ready = tmp_path / 'ready'
    script = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from catchup_core import checkpoint as ck
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
cipher = EnvelopeCipher(StaticKeyProvider({1: b'r' * 32}))
orig_extract = ck._extract
def slow_extract(source, cipher, dest, audit):
    Path(sys.argv[4]).touch()
    while True:
        time.sleep(0.01)
ck._extract = slow_extract
ck.restore(Path(sys.argv[2]), Path(sys.argv[3]), cipher, [], replay_privacy=lambda _: None)
"""
    with subprocess.Popen([sys.executable, '-c', script, str(Path(ck.__file__).parents[1]),
                           str(path), str(dest), str(ready)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), child.communicate(timeout=1)
        finally:
            child.kill()
            child.communicate(timeout=5)
    assert (dest / '.artha-restored.json').is_file()
    assert not (dest / 'repo/a.txt').exists()
    with pytest.raises(RuntimeError, match='restored installation'):
        require_active_installation(dest / 'repo')


def test_multiframe_manifest_and_size_bounds_round_trip_and_refuse_oversized(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    root = tmp_path / 'src'
    root.mkdir()
    for idx in range(6):
        (root / f'file_{idx:02d}.txt').write_text(f'payload-{idx}')
    monkeypatch.setattr(ck, '_CHUNK', 64)
    ack = ck.create('personal', {'repo': root}, tmp_path / 'out', CIPHER, {},
                    'correct horse battery staple', now=datetime(2026, 10, 24, tzinfo=UTC),
                    lock_path=tmp_path / 'lock')
    manifest = ck.verify(ack, CIPHER)
    assert len(manifest['entries']) == 6
    dest = tmp_path / 'restored'
    report = restore(ack, dest)
    assert report['files'] == 6

    monkeypatch.setattr(ck, '_MAX_MANIFEST_BYTES', 128)
    with pytest.raises(ck.CheckpointError, match='manifest exceeds'):
        ck.verify(ack, CIPHER)
    with pytest.raises(ck.CheckpointBlocked, match='manifest exceeds'):
        ck.create('personal', {'repo': root}, tmp_path / 'out2', CIPHER, {},
                  'correct horse battery staple', now=datetime(2026, 10, 24, tzinfo=UTC),
                  lock_path=tmp_path / 'lock')


def test_sqlite_streaming_refuses_oversized_database_and_reconciles_tables(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    root = tmp_path / 'src'
    root.mkdir()
    db_path = root / 'ledger.db'
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('CREATE TABLE items(id TEXT PRIMARY KEY, status TEXT, score INTEGER)')
        db.execute("INSERT INTO items VALUES('a', 'open', 1)")
        db.execute("INSERT INTO items VALUES('b', 'open', 2)")
        db.execute('CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID')
        db.execute("INSERT INTO kv VALUES('k1', 'v1')")

    ack = ck.create('personal', {'local': root}, tmp_path / 'out', CIPHER, {},
                    'correct horse battery staple', now=datetime(2026, 10, 24, tzinfo=UTC),
                    lock_path=tmp_path / 'lock')
    clean = ck.reconcile(ack, CIPHER, {'local': root})
    assert clean['changed'] == [] and clean['tables'] == {}

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("UPDATE items SET status='done' WHERE id='a'")
        db.execute("DELETE FROM items WHERE id='b'")
        db.execute("INSERT INTO items VALUES('c', 'new', 3)")
        db.execute("UPDATE kv SET v='v2' WHERE k='k1'")

    drift = ck.reconcile(ack, CIPHER, {'local': root})
    assert drift['changed'] == ['local/ledger.db']
    tables = drift['tables']['local/ledger.db']
    assert tables['items'] == {'added': 1, 'removed': 1, 'changed': 1}
    assert tables['kv'] == {'added': 0, 'removed': 0, 'changed': 1}

    monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 64)
    # A complete main file streams in chunks, so the image limit no longer blocks it.
    assert ck._sqlite_sha(db_path)
    assert len(ck.create('personal', {'local': root}, tmp_path / 'out_big', CIPHER, {},
                         'correct horse battery staple', now=datetime(2026, 10, 24, tzinfo=UTC),
                         lock_path=tmp_path / 'lock').name) > 0
    assert ck.reconcile(ack, CIPHER, {'local': root})['tables_unavailable'] == ['local/ledger.db']



def test_committed_wal_growth_is_bounded_before_backup_serialization(tmp_path, monkeypatch):
    path = tmp_path / 'wal.db'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE content(body BLOB)')
        writer.commit()
        writer.execute('INSERT INTO content VALUES(?)', (b'x' * 65536,))
        writer.commit()
        assert path.stat().st_size < 16384
        monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 16384)
        original = sqlite3.connect
        class NoOversizedSerialization(sqlite3.Connection):
            def serialize(self, *args, **kwargs):
                pytest.fail('oversized committed WAL image reached serialization')
        def connect(database, *args, **kwargs):
            if database == ':memory:':
                kwargs['factory'] = NoOversizedSerialization
            return original(database, *args, **kwargs)
        monkeypatch.setattr(ck.sqlite3, 'connect', connect)
        with pytest.raises(ck.CheckpointBlocked, match='online backup limit'):
            ck._sqlite_sha(path)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX open-inode replacement; native Windows sharing is separate')
@pytest.mark.parametrize('mutation', ['replace', 'in-place'])
def test_real_process_cannot_substitute_unapproved_restore_input(tmp_path, monkeypatch, mutation):
    path = container(tmp_path / 'baseline.ack')
    alternate = container(tmp_path / 'alternate.ack', [('repo/a.txt', b'UNAPPROVED WORK BODY', 'file', 0o600),
                                                     ('repo/z.txt', b'last baseline', 'file', 0o600)], profile='work')
    # Preserve the exact header frame, forcing the in-place test to reach the changed data frame.
    original_bytes = path.read_bytes()
    first = 4 + 2 + len(GENERATION) + 4
    after_header = first + 5 + int.from_bytes(original_bytes[first + 1:first + 5], 'big')
    alternate.write_bytes(original_bytes[:after_header] + alternate.read_bytes()[after_header:])
    assert ck.verify(alternate, CIPHER)['profile'] == 'work'
    script = '''import os, sys
from pathlib import Path
source, target = Path(sys.argv[1]), Path(sys.argv[2])
if sys.argv[3] == 'replace':
    os.replace(source, target)
else:
    with target.open('r+b') as handle:
        handle.write(source.read_bytes())
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
'''
    def mutate(sink, _manifest):
        if sink is None:
            child = subprocess.run([sys.executable, '-c', script, str(alternate), str(path), mutation],
                                   capture_output=True, timeout=10)
            assert child.returncode == 0, child.stderr.decode()
    released = scan_spy(monkeypatch, on_scan=mutate)
    destination = tmp_path / 'restored'
    if mutation == 'replace':
        restore(path, destination)
        assert (destination / 'repo/a.txt').read_bytes() == b'authorized baseline'
    else:
        with pytest.raises(ck.CheckpointError, match='changed after authenticated preflight'):
            restore(path, destination)
        assert not destination.exists()
    assert b'UNAPPROVED WORK BODY' not in released


def test_process_wal_growth_during_backup_refuses_before_serialization(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / 'growing.db'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE content(body BLOB)')
        writer.commit()
        original = sqlite3.connect
        grown = []
        class GrowingSource(sqlite3.Connection):
            def execute(self, query, *args, **kwargs):
                cursor = super().execute(query, *args, **kwargs)
                if query == 'PRAGMA page_count' and not grown:
                    previous = cursor.fetchone()
                    cursor.close()
                    child = subprocess.run([sys.executable, '-c',
                        "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); "
                        "db.execute('INSERT INTO content VALUES(?)',(b'x'*65536,)); db.commit(); db.close()", str(path)],
                        capture_output=True, timeout=10)
                    assert child.returncode == 0, child.stderr.decode()
                    grown.append(True)
                    return SimpleNamespace(fetchone=lambda: previous)
                return cursor
        class NoSerialization(sqlite3.Connection):
            def serialize(self, *args, **kwargs):
                pytest.fail('growing WAL reached unbounded serialization')
        def connect(database, *args, **kwargs):
            kwargs['factory'] = NoSerialization if database == ':memory:' else GrowingSource
            return original(database, *args, **kwargs)
        monkeypatch.setattr(ck.sqlite3, 'connect', connect)
        monkeypatch.setattr(ck, '_MAX_SQLITE_BYTES', 16384)
        with pytest.raises(ck.CheckpointBlocked, match='online backup limit'):
            ck._sqlite_sha(path)
        assert grown == [True]


def test_work_checkpoint_under_work_keys_never_writes_plaintext_under_personal_restore_keys(tmp_path, monkeypatch):
    """Given a Work checkpoint sealed with Work keys, when restored with personal keys, then no byte is released."""
    work_cipher = EnvelopeCipher(StaticKeyProvider({1: b'w' * 32}))
    root = tmp_path / 'work_state'
    root.mkdir()
    (root / 'note.md').write_text('SYNTHETIC-WORK-PLAINTEXT')
    from datetime import UTC, datetime
    ack = ck.create('work', {'work_state': root}, tmp_path / 'out', work_cipher, {},
                    'correct horse battery staple', now=datetime(2026, 10, 24, tzinfo=UTC), lock_path=tmp_path / 'lock')
    released = scan_spy(monkeypatch)
    with pytest.raises(ck.CheckpointError, match='authentication'):
        restore(ack, tmp_path / 'restored', expected_profile='personal')
    assert not released and not (tmp_path / 'restored').exists()
