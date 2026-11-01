"""Opt-in historical restore: synthetic members and local static keys only."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import sqlite3
import struct
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import checkpoint as ck
from catchup_core import recovery_quarantine as quarantine
from catchup_core.recovery_quarantine import QuarantineStore
from test_checkpoint_restore_integrity import CIPHER, GENERATION, container, restore, scan_spy


def authority():
    return {'actor': 'owner', 'owner': 'owner', 'profile': 'personal', 'privacy': {
        'personal': {'checkpoint_id': 9, 'permission_generation': 3, 'tombstone_digest': 'a' * 64}}}


def ledger_entry(name='local/catchup_core/ledger.db'):
    from catchup_core.ledger import Ledger
    from catchup_core.schema_history import record, sources
    with closing(sqlite3.connect(':memory:')) as database:
        installed = sources(Ledger.available_migrations())
        executed = set()
        for migration in installed:
            database.executescript(migration.script)
            executed.add(migration.number)
            record(database, installed, migration.number, executed, '2026-10-25T00:00:00Z')
            database.execute(f'PRAGMA user_version={migration.number}')
        database.commit()
        return name, database.serialize(), 'sqlite', 0o600


def is_ledger(entry):
    return ck._privacy_ledger(entry)


def factory_for(stores, calls=None):
    def factory(dest, manifest, digest):
        if calls is not None:
            calls.append((dest, copy.deepcopy(manifest), digest))
        store = QuarantineStore(dest / '.artha-quarantine', CIPHER, authority=authority)
        store.begin(dict(manifest, entries=[entry for entry in manifest['entries'] if not is_ledger(entry)]), digest)
        stores.append(store)
        return store
    return factory


def assert_ciphertext(dest, markers):
    for path in dest.rglob('*'):
        if path.is_file():
            raw = path.read_bytes()
            assert all(marker not in raw for marker in markers), path
            assert all(marker.decode() not in path.relative_to(dest).as_posix() for marker in markers), path
            if path.is_relative_to(dest / '.artha-quarantine'):
                if path == dest / '.artha-quarantine' / '.lock':
                    assert raw == b''
                else:
                    assert raw.startswith(b'AE1')


def chunked_container(path, entries, escrow=b''):
    recorded = []
    with path.open('wb') as handle:
        handle.write(b'ACK2' + struct.pack('>H', len(GENERATION)) + GENERATION
                     + struct.pack('>I', len(escrow)) + escrow)
        writer = ck._Writer(handle, CIPHER, GENERATION.decode())
        for name, body, kind, mode in entries:
            header = {'path': name, 'kind': kind, 'mode': mode}
            ending = {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
            writer.frame('h', json.dumps(header).encode())
            for chunk in ck._chunks(body):
                writer.frame('d', chunk)
            writer.frame('e', json.dumps(ending).encode())
            recorded.append({**header, **ending, 'source_sha256': ending['sha256']})
        writer.frame('m', json.dumps({'schema': 'artha.checkpoint.v2', 'generation': GENERATION.decode(),
            'profile': 'personal', 'escrow_sha256': hashlib.sha256(escrow).hexdigest(), 'entries': recorded,
            'excluded': {}}).encode())
        writer.frame('z', json.dumps({'members': len(recorded)}).encode())
    return path


def test_opt_in_encrypts_every_nonledger_and_preserves_authenticated_identity(tmp_path, monkeypatch):
    entries = [ledger_entry(), ('repo/private-name.md', b'PRIVATE-HISTORICAL-MARKER', 'file', 0o4755),
               ('repo/config/catchup_core.yaml', b'cutover: true\n', 'file', 0o600),
               ('repo/keys/master.key', b'SECRET-KEY-MARKER', 'file', 0o600),
               ('repo/scripts/run.py', b'SECRET-SCRIPT-MARKER', 'file', 0o755),
               ('repo/actions.db', b'not a database', 'sqlite', 0o600)]
    path = container(tmp_path / 'original.ack', entries)
    manifest = ck.verify(path, CIPHER)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    stores, calls, replayed = [], [], []
    dest = tmp_path / 'restored'
    original_write = QuarantineStore.write

    def observed_write(store, chunk):
        original_write(store, chunk)
        assert_ciphertext(dest, [b'PRIVATE-HISTORICAL-MARKER', b'SECRET-KEY-MARKER', b'SECRET-SCRIPT-MARKER'])

    monkeypatch.setattr(QuarantineStore, 'write', observed_write)

    def replay(root):
        replayed.append(root)
        assert (root / entries[0][0]).read_bytes() == entries[0][1]
        assert not (root / 'repo/config').exists()
        reopened = QuarantineStore(root / '.artha-quarantine', CIPHER, authority=authority)
        assert reopened.verify()['members'] == 5
        reopened.block_paths(['repo/private-name.md'], 'known erasure')

    report = restore(path, dest, quarantine_factory=factory_for(stores, calls), replay_privacy=replay)
    assert calls == [(dest, manifest, digest)] and replayed == [dest]
    assert report['files'] == len(entries)
    assert report['verified_bytes'] == sum(len(entry[1]) for entry in entries)
    assert report['verified_sqlite'] == 1
    assert report['quarantined_files'] == 5
    assert report['quarantined_bytes'] == sum(len(entry[1]) for entry in entries[1:])
    assert stores[0].verify()['blocked'] == 1
    assert (dest / '.artha-restored.json').is_file()
    assert {file.relative_to(dest).as_posix() for file in dest.rglob('*') if file.is_file()
            and not file.is_relative_to(stores[0].root)} == {entries[0][0], '.artha-restored.json'}
    assert all('private-name' not in str(item) for item in report['transformed'])


@pytest.mark.parametrize('entries', [[], [ledger_entry()]])
def test_empty_nonledger_subset_seals_and_verifies(tmp_path, entries):
    stores = []
    report = restore(container(tmp_path / 'empty.ack', entries), tmp_path / 'restored',
                     quarantine_factory=factory_for(stores))
    assert report['quarantined_files'] == report['quarantined_bytes'] == 0
    assert stores[0].verify()['members'] == 0


@pytest.mark.parametrize('failure', ['wrong-profile', 'late-frame', 'missing-hook'])
def test_preflight_refusals_do_not_call_factory_or_write(tmp_path, failure):
    path = container(tmp_path / 'input.ack', profile='work' if failure == 'wrong-profile' else 'personal')
    if failure == 'late-frame':
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
    calls = []
    options = {'replay_privacy': None} if failure == 'missing-hook' else {}
    with pytest.raises(ck.CheckpointError):
        restore(path, tmp_path / 'restored', quarantine_factory=factory_for([], calls), **options)
    assert not calls and not (tmp_path / 'restored').exists()


def test_privacy_exception_cleans_entire_tree(tmp_path):
    path = container(tmp_path / 'input.ack')
    original = path.read_bytes()
    stores = []

    def refuse(dest):
        assert stores[0].verify()['members'] == 2
        raise ck.CheckpointBlocked('synthetic privacy refusal')

    with pytest.raises(ck.CheckpointBlocked, match='privacy refusal'):
        restore(path, tmp_path / 'restored', quarantine_factory=factory_for(stores), replay_privacy=refuse)
    assert not (tmp_path / 'restored').exists() and path.read_bytes() == original


def test_large_member_is_split_to_store_chunks_without_plaintext(tmp_path, monkeypatch):
    marker = b'LARGE-PRIVATE-MARKER'
    body = marker * (ck._CHUNK // len(marker) + 1)
    path = chunked_container(tmp_path / 'large.ack', [('repo/large-private.bin', body, 'file', 0o600)])
    writes = []
    original = QuarantineStore.write
    dest = tmp_path / 'restored'

    def observed(store, chunk):
        writes.append(len(chunk))
        original(store, chunk)
        assert_ciphertext(dest, [marker])

    monkeypatch.setattr(QuarantineStore, 'write', observed)
    stores = []
    report = restore(path, dest, quarantine_factory=factory_for(stores))
    assert max(writes) <= quarantine.CHUNK_BYTES and sum(writes) == len(body)
    member = stores[0].inventory()[0]
    assert b''.join(stores[0].iter_member(member['member_id'])) == body
    assert report['verified_bytes'] == report['quarantined_bytes'] == len(body)


@pytest.mark.parametrize('damage', ['extra', 'extra-directory', 'raw-nonledger', 'ciphertext', 'catalog'])
def test_baseline_physical_and_catalog_mismatch_refuse_before_replay(tmp_path, monkeypatch, damage):
    path = container(tmp_path / 'input.ack', [ledger_entry(), ('repo/private.md', b'private', 'file', 0o600)])
    dest = tmp_path / 'restored'
    stores, replayed = [], []

    def mutate(sink, manifest):
        if sink is None:
            return
        if damage == 'extra':
            (dest / '.DS_Store').write_bytes(b'extra')
        elif damage == 'extra-directory':
            (dest / 'unknown').mkdir()
        elif damage == 'raw-nonledger':
            (dest / 'repo/private.md').write_bytes(b'private')
        elif damage == 'ciphertext':
            next(stores[0].root.glob('*-*.ae1')).write_bytes(b'broken')
        else:
            stores[0]._catalog['entries'][0]['mode'] = 0o644

    scan_spy(monkeypatch, on_scan=mutate)
    with pytest.raises((ck.CheckpointError, quarantine.QuarantineError)):
        restore(path, dest, quarantine_factory=factory_for(stores), replay_privacy=replayed.append)
    assert not replayed and not dest.exists()


def test_hash_includes_nonempty_escrow_and_uses_the_authenticated_handle(tmp_path, monkeypatch):
    original_body = b'ORIGINAL-PRIVATE-MARKER'
    path = chunked_container(tmp_path / 'input.ack', [('repo/private.md', original_body, 'file', 0o600)],
                             escrow=b'synthetic opaque escrow section')
    original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = ck.verify(path, CIPHER)
    other = container(tmp_path / 'other.ack', [('repo/private.md', b'SUBSTITUTED', 'file', 0o600)])
    rename_denied = False

    def substitute(sink, _manifest):
        nonlocal rename_denied
        if sink is None:
            try:
                other.replace(path)
            except PermissionError as exc:
                assert sys.platform == 'win32' and exc.winerror in (5, 32)
                rename_denied = True

    scan_spy(monkeypatch, on_scan=substitute)
    stores, calls = [], []
    restore(path, tmp_path / 'restored', quarantine_factory=factory_for(stores, calls))
    assert calls[0][1:] == (manifest, original_digest)
    member = stores[0].inventory()[0]
    assert b''.join(stores[0].iter_member(member['member_id'])) == original_body
    if sys.platform == 'win32':
        assert rename_denied and hashlib.sha256(path.read_bytes()).hexdigest() == original_digest
        other.replace(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() != original_digest


@pytest.mark.parametrize('mode', ['in-place-frame', 'extra-fingerprint', 'head'])
def test_second_pass_fingerprints_still_refuse_substitution(tmp_path, monkeypatch, mode):
    path = container(tmp_path / 'input.ack')
    other = container(tmp_path / 'other.ack', [('repo/a.txt', b'SUBSTITUTED-PRIVATE', 'file', 0o600),
                                             ('repo/z.txt', b'last baseline', 'file', 0o600)])
    replacement = other.read_bytes()
    original_scan = ck._scan
    released = []

    def intercept(source, cipher, sink=None, opened=None, **kwargs):
        def output(header, body):
            released.append(body)
            sink(header, body)
        result = original_scan(source, cipher, output if sink else None, opened, **kwargs)
        if sink is None:
            if mode == 'extra-fingerprint':
                kwargs['audit'](b'x' * 32)
            else:
                with path.open('r+b') as handle:
                    handle.write(replacement if mode == 'in-place-frame' else replacement[:8] + b'X')
                    if mode == 'in-place-frame':
                        handle.truncate()
                    handle.flush()
        return result

    monkeypatch.setattr(ck, '_scan', intercept)
    dest = tmp_path / 'restored'
    with pytest.raises(ck.CheckpointError, match='changed after authenticated preflight'):
        restore(path, dest, quarantine_factory=factory_for([]))
    assert b'SUBSTITUTED-PRIVATE' not in released and not dest.exists()


@pytest.mark.parametrize('mutation', ['extra', 'ciphertext', 'marker-missing', 'marker-enabled', 'extra-ciphertext'])
def test_post_replay_has_no_unaccounted_plaintext_or_unverified_ciphertext(tmp_path, mutation):
    path = container(tmp_path / 'input.ack')
    dest = tmp_path / 'restored'
    stores = []

    def replay(root):
        if mutation == 'extra':
            (root / 'unproven-output.txt').write_bytes(b'UNPROVEN-RAW')
        elif mutation == 'ciphertext':
            next(stores[0].root.glob('*-*.ae1')).write_bytes(b'bad ciphertext')
        elif mutation == 'extra-ciphertext':
            (stores[0].root / 'unknown.ae1').write_bytes(b'AE1unaccounted')
        elif mutation == 'marker-missing':
            (root / '.artha-restored.json').unlink()
        else:
            marker = json.loads((root / '.artha-restored.json').read_bytes())
            marker['effects'] = 'enabled'
            (root / '.artha-restored.json').write_text(json.dumps(marker))

    with pytest.raises((ck.CheckpointError, quarantine.QuarantineError)):
        restore(path, dest, quarantine_factory=factory_for(stores), replay_privacy=replay)
    assert not dest.exists()


@pytest.mark.parametrize('name,kind', [('repo/catchup_corex/ledger.db', 'sqlite'),
                                      ('repo/catchup_core/ledger.sqlite', 'sqlite'),
                                      ('repo/other/ledger.db', 'sqlite'),
                                      ('repo/catchup_core/ledger.db', 'file')])
def test_only_exact_ledger_classification_can_reach_plaintext(tmp_path, name, kind):
    path = container(tmp_path / 'input.ack', [(name, b'NOT-A-VALID-DATABASE', kind, 0o600)])
    stores = []
    dest = tmp_path / 'restored'
    report = restore(path, dest, quarantine_factory=factory_for(stores))
    assert not (dest / name).exists()
    assert report['verified_sqlite'] == 0 and report['quarantined_files'] == 1
    assert stores[0].verify()['bytes'] == len(b'NOT-A-VALID-DATABASE')


@pytest.mark.parametrize('bad_database', ['header', 'foreign-key'])
def test_real_plaintext_ledger_requires_physical_sqlite_integrity(tmp_path, bad_database):
    if bad_database == 'header':
        body = b'not SQLite'
    else:
        with closing(sqlite3.connect(':memory:')) as database:
            database.executescript('CREATE TABLE parent(id PRIMARY KEY); '
                                  'CREATE TABLE child(id REFERENCES parent(id)); INSERT INTO child VALUES(99);')
            body = database.serialize()
    path = container(tmp_path / 'input.ack', [('local/catchup_core/ledger.db', body, 'sqlite', 0o600)])
    replayed = []
    with pytest.raises(ck.CheckpointError, match='database'):
        restore(path, tmp_path / 'restored', quarantine_factory=factory_for([]), replay_privacy=replayed.append)
    assert not replayed and not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('kind', ['missing', 'directory', 'unreadable-member'])
def test_unavailable_or_unverifiable_input_never_falls_back(tmp_path, monkeypatch, kind):
    path = tmp_path / 'input.ack'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'unreadable-member':
        container(path)
        monkeypatch.setattr(CIPHER, 'decrypt', lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ck.DecryptionError('synthetic unavailable member')))
    calls = []
    with pytest.raises(ck.CheckpointError):
        restore(path, tmp_path / 'restored', quarantine_factory=factory_for([], calls))
    assert not calls and not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('returned', ['none', 'outside', 'uninitialized'])
def test_invalid_factory_refuses_without_plaintext_fallback(tmp_path, returned):
    path = container(tmp_path / 'input.ack')

    def factory(dest, _manifest, _digest):
        if returned == 'none':
            return None
        if returned == 'outside':
            return SimpleNamespace(root=tmp_path / 'unowned')
        return QuarantineStore(dest / '.artha-quarantine', CIPHER, authority=authority)

    with pytest.raises((ck.CheckpointError, quarantine.QuarantineError)):
        restore(path, tmp_path / 'restored', quarantine_factory=factory)
    assert not (tmp_path / 'restored').exists()


@pytest.mark.skipif(os.name != 'posix', reason='SIGKILL is a POSIX crash boundary')
def test_actual_sigkill_after_first_encrypted_chunk_leaves_no_private_plaintext(tmp_path):
    marker = b'SIGKILL-PRIVATE-HISTORICAL-MARKER'
    path = chunked_container(tmp_path / 'input.ack', [('repo/private-name.md', marker * 100000, 'file', 0o600)])
    dest = tmp_path / 'killed'
    original = path.read_bytes()
    script = '''import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from catchup_core import checkpoint as ck
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.recovery_quarantine import QuarantineStore
cipher = EnvelopeCipher(StaticKeyProvider({1: b'r' * 32}))
def authority():
    return {'actor': 'owner', 'owner': 'owner', 'profile': 'personal', 'privacy': {
        'personal': {'checkpoint_id': 9, 'permission_generation': 3, 'tombstone_digest': 'a' * 64}}}
def factory(dest, manifest, digest):
    store = QuarantineStore(dest / '.artha-quarantine', cipher, authority=authority)
    store.begin(manifest, digest)
    original = store.write
    def crash(chunk):
        original(chunk)
        os.kill(os.getpid(), signal.SIGKILL)
    store.write = crash
    return store
ck.restore(Path(sys.argv[2]), Path(sys.argv[3]), cipher, [],
           replay_privacy=lambda _: None, quarantine_factory=factory)
'''
    child = subprocess.run([sys.executable, '-c', script, str(Path(ck.__file__).parents[1]), str(path), str(dest)],
                           capture_output=True, timeout=15)
    assert child.returncode == -signal.SIGKILL, child.stderr.decode()
    assert marker not in child.stdout + child.stderr
    assert (dest / '.artha-restored.json').is_file()
    assert not (dest / 'repo').exists()
    assert len(list((dest / '.artha-quarantine').iterdir())) == 1
    assert_ciphertext(dest, [marker, b'private-name'])
    assert path.read_bytes() == original
    from catchup_core.session_lock import require_active_installation
    with pytest.raises(RuntimeError, match='restored installation'):
        require_active_installation(dest / 'repo')


def test_installation_6200_files_streams_without_library_member_limit(tmp_path):
    entries = [(f'repo/member-{number:05d}', b'x', 'file', 0o600) for number in range(6200)]
    path = container(tmp_path / 'installation.ack', entries)
    captured = []

    class BoundedFakeStore:
        def __init__(self, root):
            self.root = root
            root.mkdir(mode=0o700)
            self.active = None
            self.sealed = False

        def start(self, entry):
            assert self.active is None and not self.sealed
            self.active = dict(entry, state='quarantined')
            self.digest = hashlib.sha256()
            self.size = 0

        def write(self, chunk):
            assert len(chunk) <= quarantine.CHUNK_BYTES
            self.digest.update(chunk)
            self.size += len(chunk)

        def finish(self):
            assert self.size == self.active['size'] and self.digest.hexdigest() == self.active['sha256']
            captured.append(self.active)
            self.active = None

        def seal(self):
            assert self.active is None
            self.sealed = True

        def verify(self):
            assert self.sealed

        def inventory(self):
            return captured

    report = restore(path, tmp_path / 'restored',
                     quarantine_factory=lambda dest, _manifest, _digest: BoundedFakeStore(dest / '.artha-quarantine'))
    assert report['files'] == report['quarantined_files'] == len(captured) == 6200
    assert report['verified_bytes'] == report['quarantined_bytes'] == 6200


def test_real_store_accepts_6500_member_manifest_within_catalog_bound(tmp_path):
    digest = hashlib.sha256(b'').hexdigest()
    entries = [{'path': f'repo/member-{number:05d}', 'size': 0, 'sha256': digest, 'source_sha256': digest,
                'mode': 0o600, 'kind': 'file'} for number in range(6500)]
    manifest = {'profile': 'personal', 'generation': 'ck_capacity', 'entries': entries}
    assert len(json.dumps(manifest).encode()) < quarantine.MAX_CATALOG_BYTES
    store = QuarantineStore(tmp_path / 'opaque', CIPHER, authority=authority)
    store.begin(manifest, 'b' * 64)
    assert len(store._expected) == 6500


def test_generic_restore_result_does_not_gain_quarantine_fields(tmp_path):
    report = restore(container(tmp_path / 'input.ack'), tmp_path / 'restored')
    assert set(report) == {'generation', 'files', 'verified_bytes', 'verified_sqlite', 'excluded', 'transformed', 'marker'}
    assert (tmp_path / 'restored/repo/a.txt').read_bytes() == b'authorized baseline'

@pytest.mark.parametrize('damage',['header','foreign-key','extra-schema'])
def test_replay_cannot_report_success_after_corrupting_a_plaintext_ledger(tmp_path,damage):
    entry=ledger_entry('local/catchup_core/ledger.db')
    checkpoint=container(tmp_path/'original.ack',[entry])
    original=checkpoint.read_bytes()
    destination=tmp_path/'restored'
    def replay(root):
        ledger=root/entry[0]
        if damage=='header':
            ledger.write_bytes(b'corrupt after privacy replay')
        else:
            with closing(sqlite3.connect(ledger)) as db:
                if damage=='foreign-key':
                    db.executescript('CREATE TABLE parent(id PRIMARY KEY); CREATE TABLE child(id REFERENCES parent(id)); '
                                     'INSERT INTO child VALUES(99);')
                else:
                    db.executescript('CREATE TABLE unproven_historical_copy(body); '
                                     "INSERT INTO unproven_historical_copy VALUES('PRIVATE-UNPROVEN');")
                db.commit()
    with pytest.raises(ck.CheckpointError,match='database|ledger'):
        restore(checkpoint,destination,quarantine_factory=factory_for([]),replay_privacy=replay)
    assert not destination.exists() and checkpoint.read_bytes()==original


@pytest.mark.parametrize('name',['repo/catchup_core/ledger.db','local/old/catchup_core/ledger.db',
    'local/catchup_core/backup/ledger.db','local/catchup_core/shadow-copy/ledger.db',
    'repo/.artha-local/catchup_core/ledger.db'])
def test_misplaced_ledger_names_are_quarantined_instead_of_written_as_plaintext(tmp_path,name):
    entry=ledger_entry(name)
    destination=tmp_path/'restored'
    stores=[]
    report=restore(container(tmp_path/'original.ack',[entry]),destination,quarantine_factory=factory_for(stores))
    assert not (destination/name).exists()
    assert report['quarantined_files']==1 and report['verified_sqlite']==0
    assert b''.join(stores[0].iter_member(stores[0].inventory()[0]['member_id']))==entry[1]


@pytest.mark.parametrize('forgery',['arbitrary-sqlite','extra-table','changed-schema','unknown-version'])
def test_canonical_path_requires_proven_ledger_schema_before_any_destination_write(tmp_path,forgery):
    entry=ledger_entry('local/catchup_core/ledger.db')
    with closing(sqlite3.connect(':memory:')) as db:
        if forgery=='arbitrary-sqlite':
            db.executescript('CREATE TABLE private_copy(value); INSERT INTO private_copy VALUES("PRIVATE-COPY");')
        else:
            db.deserialize(entry[1])
            if forgery=='extra-table':
                db.executescript('CREATE TABLE private_copy(value); INSERT INTO private_copy VALUES("PRIVATE-COPY");')
            elif forgery=='changed-schema':
                db.execute('CREATE TABLE unexpected_schema_marker(value)')
            else:
                db.execute('PRAGMA user_version=9999')
        db.commit()
        entry=(entry[0],db.serialize(),'sqlite',0o600)
    checkpoint=container(tmp_path/'original.ack',[entry])
    calls=[]
    with pytest.raises(ck.CheckpointError,match='ledger|database'):
        restore(checkpoint,tmp_path/'restored',quarantine_factory=factory_for([],calls))
    assert not calls and not (tmp_path/'restored').exists()


def test_candidate_ledger_memory_bound_refuses_before_factory_or_plaintext_write(tmp_path,monkeypatch):
    checkpoint=container(tmp_path/'original.ack',[ledger_entry()])
    monkeypatch.setattr(ck,'_MAX_SQLITE_BYTES',128)
    calls=[]
    with pytest.raises(ck.CheckpointBlocked,match='bounded'):
        restore(checkpoint,tmp_path/'restored',quarantine_factory=factory_for([],calls))
    assert not calls and not (tmp_path/'restored').exists()


def test_canonical_ledger_foreign_key_damage_refuses_before_any_destination_write(tmp_path):
    entry=ledger_entry()
    with closing(sqlite3.connect(':memory:')) as db:
        db.deserialize(entry[1])
        db.execute("INSERT INTO fact_viewers VALUES('missing-fact','owner')")
        db.commit()
        checkpoint=container(tmp_path/'original.ack',[(entry[0],db.serialize(),entry[2],entry[3])])
    calls=[]
    with pytest.raises(ck.CheckpointError,match='foreign keys'):
        restore(checkpoint,tmp_path/'restored',quarantine_factory=factory_for([],calls))
    assert not calls and not (tmp_path/'restored').exists()


@pytest.mark.parametrize('name',['local/catchup_core/ledger.db','local/catchup_core/shadow/ledger.db'])
def test_supported_ledger_identity_allows_verified_privacy_mutation(tmp_path,name):
    entry=ledger_entry(name)
    checkpoint=container(tmp_path/'original.ack',[entry,('repo/notes.md',b'PRIVATE-NOTE','file',0o600)])
    def replay(root):
        with closing(sqlite3.connect(root/name)) as db:
            db.execute('UPDATE fact_generation SET generation=generation+1')
            db.commit()
    report=restore(checkpoint,tmp_path/'restored',quarantine_factory=factory_for([]),replay_privacy=replay)
    assert report['verified_sqlite']==1 and report['quarantined_files']==1


@pytest.mark.skipif(os.name!='posix',reason='POSIX process-death evidence')
@pytest.mark.parametrize('phase',['sealed','privacy','post-replay'])
def test_real_process_death_late_in_restore_retains_only_inactive_encrypted_historical_files(tmp_path,phase):
    marker=b'CRASH-PRIVATE-HISTORICAL-MARKER'
    checkpoint=container(tmp_path/'original.ack',[ledger_entry(),('repo/notes.md',marker,'file',0o600)])
    original=checkpoint.read_bytes()
    destination=tmp_path/'restored'
    script='''
import os,signal,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from catchup_core import checkpoint as ck
from catchup_core.crypto import EnvelopeCipher,StaticKeyProvider
from catchup_core.recovery_quarantine import QuarantineStore
cipher=EnvelopeCipher(StaticKeyProvider({1:b'r'*32}))
phase=sys.argv[4]
def authority():
    return {'actor':'owner','owner':'owner','profile':'personal','privacy':{
        'personal':{'checkpoint_id':9,'permission_generation':3,'tombstone_digest':'a'*64}}}
def crash(): os.kill(os.getpid(),signal.SIGKILL)
def factory(dest,manifest,digest):
    store=QuarantineStore(dest/'.artha-quarantine',cipher,authority=authority)
    store.begin(dict(manifest,entries=[entry for entry in manifest['entries'] if not ck._privacy_ledger(entry)]),digest)
    seal=store.seal
    def sealed():
        seal()
        if phase=='sealed': crash()
    store.seal=sealed
    return store
def replay(dest):
    if phase=='privacy': crash()
verified=ck._verify_replayed_ledgers
def after(dest,manifest):
    verified(dest,manifest)
    if phase=='post-replay': crash()
ck._verify_replayed_ledgers=after
ck.restore(Path(sys.argv[2]),Path(sys.argv[3]),cipher,[],quarantine_factory=factory,replay_privacy=replay)
'''
    dead=subprocess.run([sys.executable,'-c',script,str(Path(ck.__file__).parents[1]),str(checkpoint),
                         str(destination),phase],capture_output=True,timeout=20)
    assert dead.returncode==-signal.SIGKILL,dead.stderr.decode()
    assert marker not in dead.stdout+dead.stderr
    assert (destination/'.artha-restored.json').exists()
    assert not (destination/'repo/notes.md').exists()
    assert_ciphertext(destination,[marker])
    assert checkpoint.read_bytes()==original
    from catchup_core.session_lock import require_active_installation
    with pytest.raises(RuntimeError,match='restored installation'):
        require_active_installation(destination)


@pytest.mark.parametrize('after_replay',[False,True])
def test_raw_bytes_outside_a_known_ledger_image_are_never_privacy_proven(tmp_path,after_replay):
    entry=ledger_entry()
    marker=b'UNPROVEN-TRAILING-PRIVATE-CONTENT'
    if not after_replay:
        entry=(entry[0],entry[1]+marker,entry[2],entry[3])
    checkpoint=container(tmp_path/'original.ack',[entry])
    calls=[]
    def replay(root):
        with (root/entry[0]).open('ab') as handle:
            handle.write(marker)
    with pytest.raises(ck.CheckpointError,match='ledger|database'):
        restore(checkpoint,tmp_path/'restored',quarantine_factory=factory_for([],calls),
                replay_privacy=replay if after_replay else lambda _:None)
    if not after_replay:
        assert not calls
    assert not (tmp_path/'restored').exists()
