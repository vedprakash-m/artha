"""Credential records never use synced plaintext or accept an unverified keyring write."""

import pytest

from lib import credentials


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.events = []

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.events.append(('set', service, account))
        self.values[service, account] = value

    def delete_password(self, service, account):
        self.events.append(('delete', service, account))
        self.values.pop((service, account), None)


@pytest.fixture
def store(tmp_path):
    backend = MemoryKeyring()
    return credentials.CredentialStore(backend=backend, local=tmp_path / 'local'), backend


def test_large_token_roundtrip_uses_only_bounded_keyring_values(store, tmp_path):
    vault, backend = store
    record = {'refresh_token': 'synthetic-' + 'z' * 12000, 'access_token': 'synthetic-access'}
    assert vault.read('google.personal') is None
    vault.write('google.personal', record)
    assert vault.read('google.personal') == record
    assert all(len(value.encode('utf-16-le')) <= 2400 for value in backend.values.values())
    assert all(b'synthetic' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())


def test_credential_updates_and_revocation_cannot_fall_back_to_old_chunks(store):
    vault, backend = store
    vault.write('msgraph.personal', {'access_token': 'old-synthetic'})
    vault.write('msgraph.personal', {'access_token': 'new-synthetic'})
    assert vault.read('msgraph.personal') == {'access_token': 'new-synthetic'}
    vault.delete('msgraph.personal')
    assert vault.read('msgraph.personal') is None
    vault.delete('msgraph.personal')


def test_failed_chunk_write_does_not_replace_acknowledged_token(store, monkeypatch):
    vault, backend = store
    vault.write('google.personal', {'token': 'before-synthetic'})
    original = backend.set_password
    def fail(service, account, value):
        if '.chunk.' in account:
            raise OSError('synthetic store failure')
        original(service, account, value)
    monkeypatch.setattr(backend, 'set_password', fail)
    with pytest.raises(credentials.CredentialError):
        vault.write('google.personal', {'token': 'after-synthetic'})
    assert vault.read('google.personal') == {'token': 'before-synthetic'}


def test_unverified_keyring_persistence_fails_closed(store, monkeypatch):
    vault, backend = store
    monkeypatch.setattr(backend, 'set_password', lambda *args: None)
    with pytest.raises(credentials.CredentialError, match='persist'):
        vault.write('google.personal', {'token': 'synthetic'})
    assert vault.read('google.personal') is None


@pytest.mark.parametrize('bad', ['not-json', '{}', '{"chunks":0}', '{"schema":"unknown"}'])
def test_corrupt_head_never_falls_back_to_files_or_old_tokens(store, bad):
    vault, backend = store
    backend.values[credentials.SERVICE, 'google.personal'] = bad
    with pytest.raises(credentials.CredentialError):
        vault.read('google.personal')


def test_missing_or_changed_chunk_is_unverifiable(store):
    vault, backend = store
    vault.write('google.personal', {'token': 'synthetic'})
    chunk = next(key for key in backend.values if '.chunk.' in key[1])
    backend.values[chunk] = 'changed'
    with pytest.raises(credentials.CredentialError):
        vault.read('google.personal')
    del backend.values[chunk]
    with pytest.raises(credentials.CredentialError):
        vault.read('google.personal')


@pytest.mark.parametrize('name', ['../token', 'token\nsecret', '', 'x' * 100])
def test_credential_names_are_bounded(store, name):
    vault, _ = store
    with pytest.raises(credentials.CredentialError):
        vault.write(name, {'token': 'synthetic'})


def test_payload_shape_size_and_secret_safe_errors(store, monkeypatch):
    vault, backend = store
    for payload in ([], {'token': 'x' * 100000}):
        with pytest.raises(credentials.CredentialError):
            vault.write('google.personal', payload)
    monkeypatch.setattr(backend, 'get_password', lambda *args: (_ for _ in ()).throw(OSError('sensitive-error-text')))
    with pytest.raises(credentials.CredentialError) as error:
        vault.read('google.personal')
    assert 'sensitive-error-text' not in str(error.value)


def test_revocation_survives_cleanup_failure_and_replays(store, monkeypatch):
    vault, backend = store
    vault.write('google.personal', {'token': 'synthetic-old'})
    vault.write('google.personal', {'token': 'synthetic-current'})
    delete = backend.delete_password
    monkeypatch.setattr(backend, 'delete_password', lambda *args: (_ for _ in ()).throw(OSError('offline')))
    with pytest.raises(credentials.CredentialError, match='cleanup'):
        vault.delete('google.personal')
    assert vault.read('google.personal') is None
    monkeypatch.setattr(backend, 'delete_password', delete)
    vault.delete('google.personal')
    assert vault.read('google.personal') is None
    assert not any('.chunk.' in account for _, account in backend.values)


def test_writer_cleans_previous_generation_before_retaining_another(store):
    vault, backend = store
    for index in range(5):
        vault.write('google.personal', {'token': 'synthetic-' + str(index)})
    assert len([account for _, account in backend.values if '.chunk.' in account]) == 2


def test_work_credentials_use_a_distinct_os_service(store):
    vault, backend = store
    vault.write('msgraph.work', {'token': 'synthetic-work'})
    assert all(service == credentials.WORK_SERVICE for service, _ in backend.values)
    assert vault.read('msgraph.personal') is None


def test_pending_partial_generation_is_cleaned_on_retry(store, monkeypatch):
    vault, backend = store
    vault.write('google.personal', {'token': 'synthetic-before'})
    original = backend.set_password
    fired = []
    def fail_once(service, account, value):
        if '.chunk.' in account and not fired:
            fired.append(True)
            raise OSError('interrupted')
        original(service, account, value)
    monkeypatch.setattr(backend, 'set_password', fail_once)
    with pytest.raises(credentials.CredentialError):
        vault.write('google.personal', {'token': 'synthetic-after'})
    assert vault.read('google.personal') == {'token': 'synthetic-before'}
    vault.write('google.personal', {'token': 'synthetic-after'})
    assert vault.read('google.personal') == {'token': 'synthetic-after'}
    assert not any(account.endswith('.pending') for _, account in backend.values)


def test_default_backend_refuses_null_or_plaintext_fallback(tmp_path, monkeypatch):
    import keyring
    monkeypatch.setattr(keyring, 'get_keyring', lambda: MemoryKeyring())
    with pytest.raises(credentials.CredentialError, match='OS credential-store'):
        credentials.CredentialStore(local=tmp_path)
    class FakeOSBackend(MemoryKeyring):
        pass
    FakeOSBackend.__module__ = 'keyring.backends.macOS'
    backend = FakeOSBackend()
    monkeypatch.setattr(keyring, 'get_keyring', lambda: backend)
    assert credentials.CredentialStore(local=tmp_path).read('google.personal') is None


@pytest.mark.parametrize('head', [
    {'schema': 'artha.credential.v1', 'retired': 'bad'},
    {'schema': 'artha.credential.v1', 'retired': [], 'generation': 'bad', 'chunks': 1},
    {'schema': 'artha.credential.v1', 'retired': [], 'generation': 'a' * 32, 'chunks': True},
    {'schema': 'artha.credential.v1', 'retired': [], 'generation': 'a' * 32, 'chunks': 1, 'digest': 'bad'},
])
def test_invalid_generation_envelopes_are_denied(store, head):
    import json
    vault, backend = store
    backend.values[credentials.SERVICE, 'google.personal'] = json.dumps(head)
    with pytest.raises(credentials.CredentialError):
        vault.read('google.personal')


@pytest.mark.parametrize('text', ['not JSON', '[]', '\u00f1'])
def test_invalid_authenticated_payload_is_not_a_credential_record(store, text):
    import hashlib
    import json
    vault, backend = store
    vault.write('google.personal', {'token': 'synthetic'})
    head = json.loads(backend.values[credentials.SERVICE, 'google.personal'])
    head['digest'] = hashlib.sha256(text.encode()).hexdigest()
    backend.values[credentials.SERVICE, 'google.personal'] = json.dumps(head)
    backend.values[credentials.SERVICE, vault._account('google.personal', head['generation'], 0)] = text
    with pytest.raises(credentials.CredentialError):
        vault.read('google.personal')


def test_failed_pending_cleanup_after_publication_does_not_delete_the_published_generation(store, monkeypatch):
    vault, backend = store
    remove = backend.delete_password
    failed = []
    def fail_once(service, account):
        if account.endswith('.pending') and not failed:
            failed.append(True)
            raise OSError('interrupted cleanup')
        remove(service, account)
    monkeypatch.setattr(backend, 'delete_password', fail_once)
    with pytest.raises(credentials.CredentialError):
        vault.write('google.personal', {'token': 'synthetic-current'})
    assert vault.read('google.personal') == {'token': 'synthetic-current'}
    vault.write('google.personal', {'token': 'synthetic-next'})
    assert vault.read('google.personal') == {'token': 'synthetic-next'}


def test_delete_refuses_to_claim_cleanup_when_backend_did_not_remove_chunks(store, monkeypatch):
    vault, backend = store
    vault.write('google.personal', {'token': 'synthetic'})
    monkeypatch.setattr(backend, 'delete_password', lambda *args: None)
    with pytest.raises(credentials.CredentialError, match='confirmed'):
        vault.delete('google.personal')
    assert vault.read('google.personal') is None


def test_pending_unpublished_record_is_erased_even_without_a_head(store, monkeypatch):
    vault, backend = store
    set_password = backend.set_password
    def fail_head(service, account, value):
        if account == 'google.personal':
            raise OSError('head publication failed')
        set_password(service, account, value)
    monkeypatch.setattr(backend, 'set_password', fail_head)
    with pytest.raises(credentials.CredentialError):
        vault.write('google.personal', {'token': 'synthetic'})
    assert vault.read('google.personal') is None
    vault.delete('google.personal')
    assert backend.values == {}


class SQLiteKeyring:
    """Synthetic, process-shared stand-in for individually durable OS credential operations."""
    def __init__(self, path):
        import sqlite3
        self.path = path
        from contextlib import closing
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS records(service TEXT,account TEXT,value TEXT,PRIMARY KEY(service,account))')

    def get_password(self, service, account):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT value FROM records WHERE service=? AND account=?', (service, account)).fetchone()
            return row[0] if row else None

    def set_password(self, service, account, value):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (service, account, value))

    def delete_password(self, service, account):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('DELETE FROM records WHERE service=? AND account=?', (service, account))


@pytest.mark.parametrize('boundary', ['before', 'after'])
def test_process_death_at_head_publication_preserves_complete_generations(tmp_path, boundary):
    import subprocess
    import sys
    from pathlib import Path
    backend = SQLiteKeyring(tmp_path / 'fake-keyring.db')
    local = tmp_path / 'local'
    vault = credentials.CredentialStore(backend=backend, local=local)
    vault.write('google.personal', {'token': 'synthetic-before'})
    code = r'''
import sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from pathlib import Path
from test_credentials import SQLiteKeyring
from lib.credentials import CredentialStore
vault=CredentialStore(backend=SQLiteKeyring(sys.argv[3]),local=Path(sys.argv[4]))
write=vault._set
def publication(account,value):
 if account=='google.personal' and sys.argv[5]=='before':
  print('ready',flush=True);sys.stdin.readline()
 write(account,value)
 if account=='google.personal' and sys.argv[5]=='after':
  print('ready',flush=True);sys.stdin.readline()
vault._set=publication
vault.write('google.personal',{'token':'synthetic-after'})
'''
    proc = subprocess.Popen([sys.executable, '-c', code, str(Path(__file__).parent),
        str(Path(credentials.__file__).resolve().parents[1]), str(backend.path), str(local), boundary],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'ready'
        proc.kill(); proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill(); proc.communicate()
    expected = 'synthetic-before' if boundary == 'before' else 'synthetic-after'
    assert vault.read('google.personal') == {'token': expected}
    vault.write('google.personal', {'token': 'synthetic-final'})
    assert vault.read('google.personal') == {'token': 'synthetic-final'}


def test_read_retries_a_retired_generation_and_bounds_repeated_changes(store, monkeypatch):
    import json
    vault, backend = store
    vault.write('google.personal', {'token': 'synthetic-old'})
    old = backend.values[credentials.SERVICE, 'google.personal']
    vault.write('google.personal', {'token': 'synthetic-new'})
    current = backend.values[credentials.SERVICE, 'google.personal']
    old_head = json.loads(old)
    del backend.values[credentials.SERVICE, vault._account('google.personal', old_head['generation'], 0)]
    get = backend.get_password
    reads = []
    def first_old(service, account):
        if account == 'google.personal':
            reads.append(True)
            return old if len(reads) == 1 else current
        return get(service, account)
    monkeypatch.setattr(backend, 'get_password', first_old)
    assert vault.read('google.personal') == {'token': 'synthetic-new'}
    sequence = []
    def changing(service, account):
        if account == 'google.personal':
            sequence.append(True)
            return old if len(sequence) % 2 else current
        return None
    monkeypatch.setattr(backend, 'get_password', changing)
    with pytest.raises(credentials.CredentialError, match='changed'):
        vault.read('google.personal')
    assert len(sequence) == 4


def test_invalid_keyring_types_and_nonserializable_payloads_are_refused(store):
    vault, backend = store
    backend.values[credentials.SERVICE, 'google.personal'] = 3
    with pytest.raises(credentials.CredentialError, match='type'):
        vault.read('google.personal')
    for payload in ({'token': float('nan')}, {'token': object()}):
        with pytest.raises(credentials.CredentialError, match='payload'):
            vault.write('google.other', payload)


def test_import_never_overwrites_a_token_published_after_its_preview(store):
    vault, _ = store
    assert vault.read('google.personal') is None
    vault.write('google.personal', {'token': 'synthetic-newer'})
    with pytest.raises(credentials.CredentialError, match='different'):
        vault.write('google.personal', {'token': 'synthetic-legacy'}, replace_existing=False)
    vault.write('google.personal', {'token': 'synthetic-newer'}, replace_existing=False)
    assert vault.read('google.personal') == {'token': 'synthetic-newer'}
    vault.write('google.empty', {'token': 'synthetic-imported'}, replace_existing=False)
    assert vault.read('google.empty') == {'token': 'synthetic-imported'}


def test_refresh_publication_requires_the_record_revision_that_was_read(store):
    vault, _ = store
    assert vault.read_versioned('google.personal') == (None, '')
    vault.write('google.personal', {'token': 'synthetic-original'}, expected_revision='')
    old, revision = vault.read_versioned('google.personal')
    assert old == {'token': 'synthetic-original'} and len(revision) == 64
    vault.write('google.personal', {'token': 'synthetic-new-login'})
    with pytest.raises(credentials.CredentialError, match='changed'):
        vault.write('google.personal', {'token': 'synthetic-delayed-refresh'}, expected_revision=revision)
    assert vault.read('google.personal') == {'token': 'synthetic-new-login'}
    _, current = vault.read_versioned('google.personal')
    vault.delete('google.personal')
    with pytest.raises(credentials.CredentialError, match='changed'):
        vault.write('google.personal', {'token': 'synthetic-revived-refresh'}, expected_revision=current)
    assert vault.read('google.personal') is None
    _, deleted = vault.read_versioned('google.personal')
    assert len(deleted) == 64
    vault.write('google.personal', {'token': 'synthetic-explicit-login'}, expected_revision=deleted)


@pytest.mark.parametrize('revision', ['bad', 1, True])
def test_invalid_refresh_revision_never_writes_a_record(store, revision):
    vault, backend = store
    with pytest.raises(credentials.CredentialError, match='revision'):
        vault.write('google.personal', {'token': 'synthetic'}, expected_revision=revision)
    assert backend.values == {}


def test_default_credential_locks_are_independent_of_runtime_root(store, tmp_path, monkeypatch):
    from catchup_core.session_lock import session_lock
    _, backend = store
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'one'))
    first = credentials.CredentialStore(backend=backend)
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'two'))
    second = credentials.CredentialStore(backend=backend)
    with first.refresh_lock('google.personal'):
        with pytest.raises(credentials.CredentialError, match='refresh'):
            with second.refresh_lock('google.personal'):
                pytest.fail('competing refresh was admitted')
        # Publication and explicit login remain possible while another refresh is in flight.
        second.write('google.personal', {'token': 'synthetic-new-login'})
    with session_lock(first._lock_path('google.personal')):
        with pytest.raises(credentials.CredentialError, match='update'):
            second.write('google.personal', {'token': 'synthetic-other'})
        with pytest.raises(credentials.CredentialError, match='update'):
            second.delete('google.personal')
    assert first.read('google.personal') == {'token': 'synthetic-new-login'}
