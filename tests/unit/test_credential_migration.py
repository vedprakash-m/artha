"""An explicitly reviewed local migration preserves recovery and never writes plaintext copies."""
import json
from pathlib import Path

import pytest
from test_credentials import MemoryKeyring

from lib import credential_migration as migration
from lib.credentials import CredentialStore


@pytest.fixture
def legacy(tmp_path):
    source = tmp_path / 'legacy'
    source.mkdir()
    payload = {'token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
               'client_id': 'synthetic-client', 'client_secret': 'synthetic-secret'}
    (source / 'gmail-oauth-token.json').write_text(json.dumps(payload))
    (source / 'msgraph-work-token.json').write_text(json.dumps({'access_token': 'synthetic-work'}))
    return source, tmp_path / 'local', payload, MemoryKeyring()


def test_preview_is_secret_free_and_personal_migration_excludes_work(legacy):
    source, local, payload, backend = legacy
    preview = migration.plan(source)
    assert preview['records'] == ['google.gmail-oauth-token']
    assert 'synthetic' not in json.dumps(preview)
    result = migration.apply(source, local, preview['review_hash'], backend=backend)
    assert result['sealed'] == ['gmail-oauth-token.json']
    assert (source / 'gmail-oauth-token.json').read_bytes().startswith(b'AE1')
    assert b'synthetic-work' in (source / 'msgraph-work-token.json').read_bytes()
    assert CredentialStore(backend=backend, local=local).read('google.gmail-oauth-token') == payload
    assert all(b'synthetic-' not in p.read_bytes() for p in local.rglob('*') if p.is_file())
    assert Path(result['backup']).is_file()
    assert migration.plan(source)['records'] == []


def test_changed_review_and_existing_different_platform_record_are_refused(legacy):
    source, local, payload, backend = legacy
    preview = migration.plan(source)
    original = (source / 'gmail-oauth-token.json').read_bytes()
    with pytest.raises(migration.MigrationError, match='changed'):
        migration.apply(source, local, 'not-the-reviewed-hash', backend=backend)
    vault = CredentialStore(backend=backend, local=local)
    vault.write('google.gmail-oauth-token', {'token': 'newer-synthetic'})
    with pytest.raises(migration.MigrationError, match='different'):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    assert (source / 'gmail-oauth-token.json').read_bytes() == original
    assert vault.read('google.gmail-oauth-token') == {'token': 'newer-synthetic'}


def test_failed_keyring_does_not_seal_the_only_recovery_copy(legacy, monkeypatch):
    source, local, _, backend = legacy
    original = (source / 'gmail-oauth-token.json').read_bytes()
    preview = migration.plan(source)
    monkeypatch.setattr(backend, 'set_password', lambda *args: None)
    with pytest.raises(RuntimeError):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    assert (source / 'gmail-oauth-token.json').read_bytes() == original


def test_work_migration_requires_its_own_scope_and_keeps_separate_recovery(legacy):
    source, local, _, backend = legacy
    preview = migration.plan(source, work=True)
    assert preview['records'] == ['msgraph.work']
    result = migration.apply(source, local, preview['review_hash'], backend=backend, work=True)
    assert result['scope'] == 'work'
    assert (source / 'msgraph-work-token.json').read_bytes().startswith(b'AE1')
    assert b'synthetic-access' in (source / 'gmail-oauth-token.json').read_bytes()


def test_conflicting_legacy_aliases_and_symlink_sources_need_explicit_resolution(legacy):
    source, _, _, _ = legacy
    (source / 'gmail-token.json').write_text(json.dumps({'token': 'different-synthetic'}))
    with pytest.raises(migration.MigrationError, match='conflicting'):
        migration.plan(source)
    (source / 'gmail-token.json').unlink()
    destination = source.parent / 'outside.json'
    destination.write_text('{}')
    (source / 'gcal-oauth-token.json').symlink_to(destination)
    with pytest.raises(migration.MigrationError, match='symlink'):
        migration.plan(source)


def test_client_only_mirror_migrates_and_work_preview_ignores_personal_keys(legacy):
    source, local, _, backend = legacy
    (source / 'gmail-oauth-token.json').unlink()
    mirror = source / 'msgraph-client-creds.json'
    mirror.write_text(json.dumps({'msgraph-client-id': 'synthetic-client'}))
    assert migration.plan(source, work=True)['client_credentials'] == []
    preview = migration.plan(source)
    assert preview['client_credentials'] == ['msgraph-client-id']
    migration.apply(source, local, preview['review_hash'], backend=backend)
    assert backend.get_password('msgraph-client-id', 'artha') == 'synthetic-client'
    assert mirror.read_bytes().startswith(b'AE1')


@pytest.mark.parametrize('raw', [b'not JSON', b'[]', b'0', b'x' * 1_000_001],
                         ids=['malformed-json', 'array', 'scalar', 'oversized'])
def test_unreadable_or_oversized_legacy_inputs_never_reach_the_os_store(legacy, raw):
    source, _, _, backend = legacy
    (source / 'gmail-oauth-token.json').write_bytes(raw)
    with pytest.raises(migration.MigrationError):
        migration.plan(source)
    assert backend.values == {}


@pytest.mark.parametrize('work', [False, True], ids=['personal', 'work'])
def test_all_ae1_garbage_cannot_report_authenticated_migration_success(legacy, work):
    source, local, _, backend = legacy
    for path in source.iterdir():
        path.write_bytes(b'AE1garbage')
    (source / 'msgraph-client-creds.json').write_bytes(b'AE1garbage')
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    with pytest.raises(RuntimeError):
        preview = migration.plan(source, work=work)
        migration.apply(source, local, preview['review_hash'], backend=backend, work=work)
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
    assert backend.values == {} and backend.events == []
    assert not list(local.rglob('*.ae1'))


@pytest.mark.parametrize('work', [False, True], ids=['personal', 'work'])
@pytest.mark.parametrize('mixed', [False, True], ids=['sealed-only', 'mixed-plaintext'])
@pytest.mark.parametrize('defect', ['none', 'garbage', 'missing-key', 'wrong-key', 'filename', 'scope', 'tampered'])
def test_presealed_sources_require_existing_scope_key_and_filename_aad(legacy, work, mixed, defect):
    import base64
    from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider, StaticKeyProvider

    source, local, _, backend = legacy
    for path in source.iterdir():
        path.unlink()
    scope = 'work' if work else 'personal'
    name = 'msgraph-work-token.json' if work else 'gmail-oauth-token.json'
    service = f'artha-credential-recovery-{scope}'
    backend.values[service, 'current_version'] = '1'
    backend.values[service, 'key.1'] = base64.b64encode(b'k' * 32).decode()
    cipher = EnvelopeCipher(StaticKeyProvider({1: b'k' * 32}))
    aad_name = 'msgraph-work-mcp-token.json' if work else 'gcal-oauth-token.json'
    aad = ('artha.credential.source:' + (aad_name if defect == 'filename' else name)).encode()
    envelope = cipher.encrypt(b'{"token":"synthetic-presealed"}', aad=aad)[0]
    if defect == 'garbage':
        envelope = b'AE1garbage'
    elif defect == 'missing-key':
        backend.values.clear()
    elif defect == 'wrong-key':
        backend.values[service, 'key.1'] = base64.b64encode(b'w' * 32).decode()
    elif defect == 'scope':
        other = 'personal' if work else 'work'
        backend.values = {(f'artha-credential-recovery-{other}', account): value
                          for (_, account), value in backend.values.items()}
    elif defect == 'tampered':
        envelope = envelope[:-1] + bytes([envelope[-1] ^ 1])
    (source / name).write_bytes(envelope)
    if mixed:
        (source / 'msgraph-client-creds.json').write_text(json.dumps({
            'msgraph-work-client-id' if work else 'msgraph-client-id': 'synthetic-client'}))
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    values = dict(backend.values)
    preview = migration.plan(source, work=work)
    if defect != 'none':
        with pytest.raises(RuntimeError):
            migration.apply(source, local, preview['review_hash'], backend=backend, work=work)
        assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
        assert backend.values == values and backend.events == []
        assert not list(local.rglob('*.ae1'))
    else:
        result = migration.apply(source, local, preview['review_hash'], backend=backend, work=work)
        assert (source / name).read_bytes() == envelope
        assert {key: backend.values[key] for key in values} == values
        if mixed:
            assert result['sealed'] == ['msgraph-client-creds.json']
            existing = EnvelopeCipher(KeyringKeyProvider(service, backend=backend, create=False))
            assert existing.decrypt((source / 'msgraph-client-creds.json').read_bytes(),
                aad=b'artha.credential.source:msgraph-client-creds.json') == originals['msgraph-client-creds.json']
        else:
            assert result['already_sealed'] == [name] and result['backup'] is None
            assert backend.values == values and backend.events == []
            assert not list(local.rglob('*.ae1'))


def test_mixed_scope_and_nonstring_client_records_are_refused(legacy):
    source, _, _, _ = legacy
    (source / 'msgraph-client-creds.json').write_text(json.dumps({
        'msgraph-client-id': 'synthetic-personal', 'msgraph-work-client-id': 'synthetic-work'}))
    with pytest.raises(migration.MigrationError, match='mixed-scope'):
        migration.plan(source)
    (source / 'msgraph-client-creds.json').unlink()
    (source / 'google-client-creds.json').write_text(json.dumps({'gmail-client-id': 3}))
    with pytest.raises(migration.MigrationError, match='conflicting|invalid type'):
        migration.plan(source)


def test_existing_different_client_is_not_overwritten(legacy):
    source, local, _, backend = legacy
    backend.set_password('gmail-client-id', 'artha', 'different-synthetic')
    preview = migration.plan(source)
    with pytest.raises(migration.MigrationError, match='different client'):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    assert backend.get_password('gmail-client-id', 'artha') == 'different-synthetic'


def test_interrupted_sealing_reuses_verified_recovery_and_os_records(legacy, monkeypatch):
    source, local, _, backend = legacy
    preview = migration.plan(source)
    publish = migration.atomic_bytes
    def interrupt(path, data):
        if path.parent == source:
            raise OSError('interrupted source publication')
        publish(path, data)
    monkeypatch.setattr(migration, 'atomic_bytes', interrupt)
    with pytest.raises(OSError):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    backups = list((local / 'credential-recovery').glob('*.ae1'))
    assert len(backups) == 1 and not (source / 'gmail-oauth-token.json').read_bytes().startswith(b'AE1')
    monkeypatch.setattr(migration, 'atomic_bytes', publish)
    result = migration.apply(source, local, preview['review_hash'], backend=backend)
    assert result['backup'] == str(backups[0])
    already = migration.apply(source, local, migration.plan(source)['review_hash'], backend=backend)
    assert already['sealed'] == [] and already['backup'] is None


def test_client_persistence_failure_and_input_race_preserve_originals(legacy, monkeypatch):
    source, local, _, backend = legacy
    original = (source / 'gmail-oauth-token.json').read_bytes()
    preview = migration.plan(source)
    write = backend.set_password
    def refuse_client(service, account, value):
        if service != 'gmail-client-secret':
            write(service, account, value)
    monkeypatch.setattr(backend, 'set_password', refuse_client)
    with pytest.raises(migration.MigrationError, match='persistence'):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    assert (source / 'gmail-oauth-token.json').read_bytes() == original
    monkeypatch.setattr(backend, 'set_password', write)
    read = CredentialStore.read
    def raced(vault, name):
        data = read(vault, name)
        if data is not None:
            (source / 'gmail-oauth-token.json').write_text('{"token":"synthetic-raced"}')
        return data
    monkeypatch.setattr(CredentialStore, 'read', raced)
    with pytest.raises(migration.MigrationError, match='changed before sealing'):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    assert b'synthetic-raced' in (source / 'gmail-oauth-token.json').read_bytes()


@pytest.mark.parametrize('stage', ['backup', 'seal'])
def test_failed_encryption_verification_never_replaces_the_original(legacy, monkeypatch, stage):
    source, local, _, backend = legacy
    original = (source / 'gmail-oauth-token.json').read_bytes()
    preview = migration.plan(source)
    real_cipher = migration.EnvelopeCipher
    class FaultyCipher:
        def __init__(self, provider):
            self.real = real_cipher(provider)
        def encrypt(self, *args, **kwargs):
            return self.real.encrypt(*args, **kwargs)
        def decrypt(self, envelope, *, aad):
            plaintext = self.real.decrypt(envelope, aad=aad)
            if (stage == 'backup' and aad.startswith(b'artha.credentials.migration')) or (
                    stage == 'seal' and aad.startswith(b'artha.credential.source')):
                return b'unverified'
            return plaintext
    monkeypatch.setattr(migration, 'EnvelopeCipher', FaultyCipher)
    with pytest.raises(migration.MigrationError, match='verification'):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    assert (source / 'gmail-oauth-token.json').read_bytes() == original


def test_migration_cli_preview_and_changed_review_are_secret_free(legacy, monkeypatch, capsys):
    import sys
    import migrate_credentials
    source, local, _, _ = legacy
    argv = ['migrate_credentials.py', '--source', str(source), '--local', str(local)]
    monkeypatch.setattr(sys, 'argv', argv)
    assert migrate_credentials.main() == 0
    output = capsys.readouterr().out
    assert 'synthetic-' not in output and json.loads(output)['records'] == ['google.gmail-oauth-token']
    monkeypatch.setattr(sys, 'argv', argv + ['--apply', 'changed'])
    assert migrate_credentials.main() == 1
    assert 'changed' in capsys.readouterr().err


def test_embedded_graph_client_and_missing_optional_google_fields_are_preserved(legacy):
    source, local, _, backend = legacy
    (source / 'msgraph-token.json').write_text(json.dumps({'access_token': 'synthetic-graph',
                                                        '_artha_client_id': 'synthetic-graph-client'}))
    (source / 'gmail-oauth-token.json').write_text(json.dumps({'token': 'synthetic-google'}))
    (source / 'google-client-creds.json').write_text(json.dumps({'gmail-client-id': ''}))
    preview = migration.plan(source)
    migration.apply(source, local, preview['review_hash'], backend=backend)
    assert backend.get_password('msgraph-client-id', 'artha') == 'synthetic-graph-client'


def test_nonstring_client_is_refused_without_a_conflicting_token(legacy):
    source, _, _, _ = legacy
    (source / 'gmail-oauth-token.json').unlink()
    (source / 'google-client-creds.json').write_text(json.dumps({'gmail-client-id': 3}))
    with pytest.raises(migration.MigrationError, match='invalid type'):
        migration.plan(source)


def test_recovery_mismatch_and_unverified_token_cannot_seal_sources(legacy, monkeypatch):
    source, local, _, backend = legacy
    preview = migration.plan(source)
    original = (source / 'gmail-oauth-token.json').read_bytes()
    publish = migration.atomic_bytes
    def stop_before_seal(path, data):
        if path.parent == source:
            raise OSError('interrupted')
        publish(path, data)
    monkeypatch.setattr(migration, 'atomic_bytes', stop_before_seal)
    with pytest.raises(OSError):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    real = migration.EnvelopeCipher
    class MismatchedRecovery:
        def __init__(self, provider):
            self.real = real(provider)
        def decrypt(self, *args, **kwargs):
            self.real.decrypt(*args, **kwargs)
            return b'different-but-readable'
    monkeypatch.setattr(migration, 'EnvelopeCipher', MismatchedRecovery)
    with pytest.raises(migration.MigrationError, match='snapshot differs'):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    monkeypatch.setattr(migration, 'EnvelopeCipher', real)
    read = CredentialStore.read
    calls = []
    def unverified(vault, name):
        calls.append(True)
        return read(vault, name) if len(calls) <= 2 else None
    monkeypatch.setattr(CredentialStore, 'read', unverified)
    with pytest.raises(migration.MigrationError, match='persistence is unverified'):
        migration.apply(source, local, preview['review_hash'], backend=backend)
    assert (source / 'gmail-oauth-token.json').read_bytes() == original


def test_reviewed_recovery_rebuilds_os_records_without_plaintext_files(legacy):
    source, local, payload, backend = legacy
    result = migration.apply(source, local, migration.plan(source)['review_hash'], backend=backend)
    backup = Path(result['backup'])
    vault = CredentialStore(backend=backend, local=local)
    vault.delete('google.gmail-oauth-token')
    backend.delete_password('gmail-client-secret', 'artha')
    preview = migration.recovery_plan(backup, backend=backend)
    assert 'synthetic' not in json.dumps(preview)
    assert preview['records'] == ['google.gmail-oauth-token']
    with pytest.raises(migration.MigrationError, match='changed'):
        migration.recover(backup, local, 'stale-hash', backend=backend)
    recovered = migration.recover(backup, local, preview['review_hash'], backend=backend)
    assert recovered['records'] == preview['records']
    assert vault.read('google.gmail-oauth-token') == payload
    assert backend.get_password('gmail-client-secret', 'artha') == 'synthetic-secret'
    assert all(b'synthetic-' not in p.read_bytes() for p in local.rglob('*') if p.is_file())
    assert (source / 'gmail-oauth-token.json').read_bytes().startswith(b'AE1')
    vault.write('google.gmail-oauth-token', {'token': 'synthetic-newer'})
    with pytest.raises(migration.MigrationError, match='different'):
        migration.recover(backup, local, preview['review_hash'], backend=backend)


def test_recovery_rejects_wrong_scope_missing_key_and_tampering(legacy):
    source, local, _, backend = legacy
    result = migration.apply(source, local, migration.plan(source)['review_hash'], backend=backend)
    backup = Path(result['backup'])
    with pytest.raises(migration.MigrationError):
        migration.recovery_plan(backup, work=True, backend=backend)
    with pytest.raises(RuntimeError):
        migration.recovery_plan(backup, backend=MemoryKeyring())
    raw = backup.read_bytes()
    backup.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(RuntimeError):
        migration.recovery_plan(backup, backend=backend)


@pytest.mark.parametrize('defect', ['schema', 'scope', 'encoding', 'unknown-file', 'sealed'])
def test_authenticated_recovery_still_requires_valid_scope_and_original_records(legacy, defect):
    import base64
    from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
    source, local, _, backend = legacy
    result = migration.apply(source, local, migration.plan(source)['review_hash'], backend=backend)
    backup = Path(result['backup'])
    cipher = EnvelopeCipher(KeyringKeyProvider('artha-credential-recovery-personal', backend=backend))
    aad = ('artha.credentials.migration.v1:personal:' + backup.stem.removeprefix('personal-')).encode()
    data = json.loads(cipher.decrypt(backup.read_bytes(), aad=aad))
    if defect in {'schema', 'scope'}:
        data[defect] = 'invalid'
    elif defect == 'encoding':
        data['files']['gmail-oauth-token.json'] = '@@@'
    elif defect == 'unknown-file':
        data['files']['msgraph-work-token.json'] = base64.b64encode(b'{}').decode()
    else:
        data['files']['gmail-oauth-token.json'] = base64.b64encode(b'AE1not-an-original').decode()
    backup.write_bytes(cipher.encrypt(json.dumps(data).encode(), aad=aad)[0])
    with pytest.raises(migration.MigrationError):
        migration.recovery_plan(backup, backend=backend)


def _kill_boundary_worker(process, worker_pid):
    import os
    import signal

    if os.name == 'nt':
        import _winapi

        # Pin the actual worker, not a venv redirector, until termination completes.
        handle = _winapi.OpenProcess(0x00100001, False, worker_pid)  # SYNCHRONIZE | PROCESS_TERMINATE
        try:
            _winapi.TerminateProcess(handle, 1)
            assert _winapi.WaitForSingleObject(handle, 10000) == _winapi.WAIT_OBJECT_0
        finally:
            _winapi.CloseHandle(handle)
    else:
        os.kill(worker_pid, signal.SIGKILL)
    # Keep stdin open until the worker is dead and its launcher has reaped it.
    process.wait(timeout=10)
    process.communicate(timeout=10)


@pytest.mark.parametrize('launcher', [False, True], ids=['direct', 'descendant'])
def test_boundary_worker_dies_before_stdin_barrier_is_released(tmp_path, launcher):
    import subprocess
    import sys

    resumed = tmp_path / 'resumed'
    code = r'''
import os, sys
from pathlib import Path
print(f'ready {os.getpid()}', flush=True)
sys.stdin.readline()
Path(sys.argv[1]).write_text('worker resumed after barrier')
'''
    command = [sys._base_executable, '-c', code, str(resumed)]
    if launcher:
        command = [sys._base_executable, '-c',
                   'import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))', *command]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    worker_pid = None
    try:
        line = process.stdout.readline().strip()
        assert line, process.stderr.read()
        ready, pid = line.split()
        assert ready == 'ready'
        worker_pid = int(pid)
        assert (worker_pid != process.pid) == launcher
    finally:
        if worker_pid is not None:
            _kill_boundary_worker(process, worker_pid)
        else:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    assert not resumed.exists(), 'stdin EOF released a surviving boundary worker'


@pytest.mark.parametrize('boundary', ['backup', 'before-first-seal', 'after-first-seal'])
def test_migration_process_death_retains_recoverable_generations(tmp_path, boundary):
    import subprocess
    import sys
    from test_credentials import SQLiteKeyring
    source, local = tmp_path / 'legacy', tmp_path / 'local'
    source.mkdir()
    for name in ('gmail-oauth-token.json', 'gcal-oauth-token.json'):
        (source / name).write_text(json.dumps({'token': 'synthetic-' + name}))
    backend = SQLiteKeyring(tmp_path / 'fake-keyring.db')
    code = r'''
import os, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from test_credentials import SQLiteKeyring
from lib import credential_migration as m
source, local = Path(sys.argv[3]), Path(sys.argv[4])
backend = SQLiteKeyring(sys.argv[5])
write = m.atomic_bytes
count = 0
def boundary(path, data):
 global count
 if path.parent == source:
  count += 1
  if count == 1 and sys.argv[6] == 'before-first-seal':
   print(f'ready {os.getpid()}', flush=True); sys.stdin.readline()
 write(path, data)
 if (path.parent != source and sys.argv[6] == 'backup') or (
     path.parent == source and count == 1 and sys.argv[6] == 'after-first-seal'):
  print(f'ready {os.getpid()}', flush=True); sys.stdin.readline()
m.atomic_bytes = boundary
m.apply(source, local, m.plan(source)['review_hash'], backend=backend)
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(Path(__file__).parent),
        str(Path(migration.__file__).resolve().parents[1]), str(source), str(local), str(backend.path), boundary],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    worker_pid = None
    try:
        line = process.stdout.readline().strip()
        assert line, process.stderr.read()
        ready, pid = line.split()
        worker_pid = int(pid)
        assert ready == 'ready'
    finally:
        if worker_pid is not None:
            _kill_boundary_worker(process, worker_pid)
        else:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    assert sum(path.read_bytes().startswith(b'AE1') for path in source.iterdir()) == (
        1 if boundary == 'after-first-seal' else 0)
    backups = list((local / 'credential-recovery').glob('*.ae1'))
    assert len(backups) == 1
    preview = migration.recovery_plan(backups[0], backend=backend)
    migration.recover(backups[0], local, preview['review_hash'], backend=backend)
    vault = CredentialStore(backend=backend, local=local)
    assert vault.read('google.gmail-oauth-token')['token'] == 'synthetic-gmail-oauth-token.json'
    assert vault.read('google.gcal-oauth-token')['token'] == 'synthetic-gcal-oauth-token.json'
    migration.apply(source, local, migration.plan(source)['review_hash'], backend=backend)
    assert all(path.read_bytes().startswith(b'AE1') for path in source.iterdir())
