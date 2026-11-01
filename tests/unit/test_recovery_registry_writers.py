"""Writer enrollment uses metadata only and never native credential discovery."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from catchup_core.crypto import KeyringKeyProvider
from lib.credentials import CredentialStore, WORK_SERVICE


class NativeRecords:
    def __init__(self):
        self._values = {}
        self.before_write = lambda service, account: None

    def get_password(self, service, account):
        return self._values.get((service, account))

    def set_password(self, service, account, value):
        self.before_write(service, account)
        self._values[service, account] = value

    def delete_password(self, service, account):
        self.before_write(service, account)
        self._values.pop((service, account), None)


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    return NativeRecords(), tmp_path / '.artha-local'


def entries(root, profile='personal'):
    return json.loads((root / 'recovery-registry' / f'{profile}.json').read_text())['entries']


def test_key_publication_retains_immutable_generations_and_head(native):
    backend, root = native
    provider = KeyringKeyProvider('artha-ledger', backend=backend)
    assert provider.current_version() == 1
    first = provider.key(1)
    assert provider.rotate() == 2
    assert provider.key(1) == first
    registered = {(entry['kind'], entry['account'], entry['generation'], entry['state'])
                  for entry in entries(root)}
    assert ('key', 'key.1', '1', 'active') in registered
    assert ('key', 'key.2', '2', 'active') in registered
    assert ('key_head', 'current_version', '2', 'active') in registered


def test_credential_head_enrollment_is_profile_scoped_and_secret_free(native):
    from catchup_core.recovery_registry import RegistryError, snapshot

    backend, root = native
    store = CredentialStore(backend=backend, local=root)
    store.write('msgraph.work', {'token': 'synthetic-private-token'})
    records = entries(root, 'work')
    assert len(records) == 1
    entry = records[0]
    assert (entry['kind'], entry['service'], entry['account'], entry['state']) == (
        'credential', WORK_SERVICE, 'msgraph.work', 'active')
    head = json.loads(backend.get_password(WORK_SERVICE, 'msgraph.work'))
    assert entry['generation'] == head['generation']
    assert not (root / 'recovery-registry' / 'personal.json').exists()
    assert 'synthetic-private-token' not in json.dumps(records)
    assert head['digest'] not in json.dumps(records)
    assert snapshot('work', root=root, require_review=False)['legacy_review'] is None
    with pytest.raises(RegistryError):
        snapshot('work', root=root)


def test_key_inspection_does_not_enroll_cached_or_existing_keys(native):
    backend, root = native
    backend.set_password('artha-ledger', 'key.1', base64.b64encode(b's' * 32).decode('ascii'))
    backend.set_password('artha-ledger', 'current_version', '1')
    provider = KeyringKeyProvider('artha-ledger', backend=backend, create=False)
    assert provider.current_version() == 1
    assert len(provider.key(1)) == 32
    assert provider.key(1) == provider.key(1)
    assert not (root / 'recovery-registry').exists()


def test_privacy_head_is_pending_before_publication_and_read_does_not_enroll(native):
    import hashlib
    from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
    from catchup_core.ledger import KeyringSealedStore, SealedCheckpoint
    from catchup_core.recovery_registry import snapshot

    backend, root = native
    anchor = KeyringSealedStore(backend, 'shadow', history_root=root / 'privacy',
        cipher=EnvelopeCipher(StaticKeyProvider({1: b'p' * 32})))
    anchor.history = lambda: {'tombstones': [], 'consent': []}
    assert anchor.read() is None and not root.exists()
    seen = []
    def pending(service, account):
        entry = entries(root)[0]
        seen.append((service, account, entry['state'], entry['generation']))
    backend.before_write = pending
    checkpoint = SealedCheckpoint(1, hashlib.sha256(b'').hexdigest(), 0)
    anchor.write(checkpoint)
    assert seen == [('artha-privacy-shadow', 'checkpoint', 'pending', '1')]
    assert entries(root)[0]['kind'] == 'privacy'
    assert entries(root)[0]['state'] == 'active'
    prior = snapshot('personal', root=root, require_review=False)
    assert anchor.read() == checkpoint
    assert anchor.replay() == {'tombstones': [], 'consent': []}
    anchor.write(checkpoint)
    assert snapshot('personal', root=root, require_review=False) == prior


def test_privacy_head_write_failure_retains_pending_dependency(native):
    import hashlib
    from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
    from catchup_core.ledger import KeyringSealedStore, SealedCheckpoint, LedgerLocked

    backend, root = native
    anchor = KeyringSealedStore(backend, 'shadow', history_root=root / 'privacy',
        cipher=EnvelopeCipher(StaticKeyProvider({1: b'p' * 32})))
    anchor.history = lambda: {'tombstones': [], 'consent': []}
    def failed(service, account):
        raise OSError('synthetic native failure')
    backend.before_write = failed
    with pytest.raises(LedgerLocked):
        anchor.write(SealedCheckpoint(1, hashlib.sha256(b'').hexdigest(), 0))
    assert entries(root)[0]['state'] == 'pending'
    assert backend._values == {}


def test_privacy_retry_completes_verified_head_after_registry_acknowledgement_failure(native, monkeypatch):
    import hashlib
    from catchup_core import recovery_registry as registry
    from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
    from catchup_core.ledger import KeyringSealedStore, SealedCheckpoint, LedgerLocked

    backend, root = native
    anchor = KeyringSealedStore(backend, 'shadow', history_root=root / 'privacy',
        cipher=EnvelopeCipher(StaticKeyProvider({1: b'p' * 32})))
    anchor.history = lambda: {'tombstones': [], 'consent': []}
    checkpoint = SealedCheckpoint(1, hashlib.sha256(b'').hexdigest(), 0)
    original = registry._save
    def fail_acknowledgement(local, path, lock, document):
        if any(entry['kind'] == 'privacy' and entry['state'] == 'active' for entry in document['entries']):
            raise OSError('synthetic acknowledgement failure')
        original(local, path, lock, document)
    monkeypatch.setattr(registry, '_save', fail_acknowledgement)
    with pytest.raises(LedgerLocked):
        anchor.write(checkpoint)
    assert anchor.read() == checkpoint and entries(root)[0]['state'] == 'pending'
    native_bytes = backend.get_password('artha-privacy-shadow', 'checkpoint')
    monkeypatch.setattr(registry, '_save', original)
    anchor.write(checkpoint)
    assert backend.get_password('artha-privacy-shadow', 'checkpoint') == native_bytes
    assert entries(root)[0]['state'] == 'active'


def test_key_failures_remain_pending_and_retry_reuses_immutable_material(native, monkeypatch):
    backend, root = native
    provider = KeyringKeyProvider('artha-ledger', backend=backend)
    original = backend.set_password

    def fail_head(service, account, value):
        if account == 'current_version':
            raise OSError('synthetic failure')
        original(service, account, value)

    monkeypatch.setattr(backend, 'set_password', fail_head)
    with pytest.raises(RuntimeError):
        provider.current_version()
    first = backend.get_password('artha-ledger', 'key.1')
    assert next(entry for entry in entries(root) if entry['kind'] == 'key_head')['state'] == 'pending'
    monkeypatch.setattr(backend, 'set_password', original)
    assert provider.current_version() == 1
    assert backend.get_password('artha-ledger', 'key.1') == first
    assert all(entry['state'] == 'active' for entry in entries(root))


def fail_final_registry_save(monkeypatch, kind):
    from catchup_core import recovery_registry

    original = recovery_registry._save

    def save(local, path, lock, document):
        previous = json.loads(path.read_text())['entries'] if path.exists() else []
        if any(entry['kind'] == kind and entry['state'] == 'active'
               and any(prior['attempt'] == entry['attempt'] and prior['state'] == 'pending'
                   for prior in previous) for entry in document['entries']):
            raise OSError('synthetic final registry save failure')
        original(local, path, lock, document)

    monkeypatch.setattr(recovery_registry, '_save', save)
    return original


@pytest.mark.parametrize('kind', ['key', 'key_head'])
@pytest.mark.parametrize('rotate', [False, True])
def test_key_final_registry_save_retry_finishes_verified_pending(native, monkeypatch, rotate, kind):
    from catchup_core import recovery_registry

    backend, root = native
    provider = KeyringKeyProvider('artha-ledger', backend=backend)
    if rotate:
        provider.current_version()
    original = fail_final_registry_save(monkeypatch, kind)
    with pytest.raises(RuntimeError):
        (provider.rotate if rotate else provider.current_version)()
    version = 2 if rotate else 1
    expected_head = str(version) if kind == 'key_head' else ('1' if rotate else None)
    assert backend.get_password('artha-ledger', 'current_version') == expected_head
    before_native = dict(backend._values)
    before_registry = (root / 'recovery-registry' / 'personal.json').read_bytes()
    inspector = KeyringKeyProvider('artha-ledger', backend=backend, create=False)
    if expected_head is None:
        with pytest.raises(RuntimeError):
            inspector.current_version()
    else:
        assert inspector.current_version() == int(expected_head)
    assert len(inspector.key(version)) == 32
    assert (root / 'recovery-registry' / 'personal.json').read_bytes() == before_registry
    assert any(entry['kind'] == kind and entry['state'] == 'pending' for entry in entries(root))
    monkeypatch.setattr(recovery_registry, '_save', original)
    assert (provider.rotate if rotate else provider.current_version)() == version
    before_native[('artha-ledger', 'current_version')] = str(version)
    assert backend._values == before_native
    assert all(entry['state'] == 'active' for entry in entries(root))
    assert recovery_registry.snapshot('personal', root=root, require_review=False)['legacy_review'] is None


@pytest.mark.parametrize('alteration', ['missing-key', 'corrupt-key', 'changed-head'])
def test_key_pending_retry_refuses_unverified_native_material(native, monkeypatch, alteration):
    from catchup_core import recovery_registry

    backend, root = native
    provider = KeyringKeyProvider('artha-ledger', backend=backend)
    original = fail_final_registry_save(monkeypatch, 'key_head')
    with pytest.raises(RuntimeError):
        provider.current_version()
    assert len(provider.key(1)) == 32
    monkeypatch.setattr(recovery_registry, '_save', original)
    if alteration == 'missing-key':
        backend.delete_password('artha-ledger', 'key.1')
    elif alteration == 'corrupt-key':
        backend.set_password('artha-ledger', 'key.1', 'invalid-base64')
    else:
        backend.set_password('artha-ledger', 'current_version', '2')
    before_native = dict(backend._values)
    before_registry = (root / 'recovery-registry' / 'personal.json').read_bytes()
    with pytest.raises(RuntimeError):
        provider.current_version()
    assert backend._values == before_native
    assert (root / 'recovery-registry' / 'personal.json').read_bytes() == before_registry


def test_mutating_existing_legacy_keys_do_not_enroll_or_attest(native):
    backend, root = native
    backend.set_password('artha-ledger', 'key.1', base64.b64encode(b's' * 32).decode('ascii'))
    backend.set_password('artha-ledger', 'current_version', '1')
    before_native = dict(backend._values)
    assert KeyringKeyProvider('artha-ledger', backend=backend).current_version() == 1
    assert backend._values == before_native
    assert not (root / 'recovery-registry').exists()


def test_credential_final_registry_save_migration_retry_finishes_same_generation(native, monkeypatch):
    from catchup_core import recovery_registry

    backend, root = native
    store = CredentialStore(backend=backend, local=root)
    payload = {'token': 'synthetic-migration-token'}
    original = fail_final_registry_save(monkeypatch, 'credential')
    with pytest.raises(RuntimeError):
        store.write('google.personal', payload, replace_existing=False)
    generation = entries(root)[0]['generation']
    assert json.loads(backend.get_password('artha.credentials.v1', 'google.personal'))['generation'] == generation
    before_native = dict(backend._values)
    before_registry = (root / 'recovery-registry' / 'personal.json').read_bytes()
    assert store.read('google.personal') == payload
    assert (root / 'recovery-registry' / 'personal.json').read_bytes() == before_registry
    monkeypatch.setattr(recovery_registry, '_save', original)
    store.write('google.personal', payload, replace_existing=False)
    assert backend._values == before_native
    assert entries(root)[0]['generation'] == generation
    assert entries(root)[0]['state'] == 'active'
    assert recovery_registry.snapshot('personal', root=root, require_review=False)['legacy_review'] is None


@pytest.mark.parametrize('alteration', ['generation', 'payload', 'corrupt-metadata'])
def test_credential_pending_retry_refuses_changed_native_or_metadata(native, monkeypatch, alteration):
    import hashlib
    from catchup_core import recovery_registry
    from lib.credentials import SERVICE

    backend, root = native
    store = CredentialStore(backend=backend, local=root)
    payload = {'token': 'synthetic-migration-token'}
    original = fail_final_registry_save(monkeypatch, 'credential')
    with pytest.raises(RuntimeError):
        store.write('google.personal', payload, replace_existing=False)
    monkeypatch.setattr(recovery_registry, '_save', original)
    head = json.loads(backend.get_password(SERVICE, 'google.personal'))
    if alteration == 'generation':
        previous_generation = head['generation']
        head['generation'] = 'f' * 32 if previous_generation != 'f' * 32 else 'e' * 32
        for index in range(head['chunks']):
            backend.set_password(SERVICE, store._account('google.personal', head['generation'], index),
                                 backend.get_password(SERVICE, store._account('google.personal', previous_generation, index)))
        backend.set_password(SERVICE, 'google.personal', json.dumps(head))
        assert store.read('google.personal') == payload
    elif alteration == 'payload':
        altered = json.dumps({'token': 'synthetic-altered-token'}, sort_keys=True)
        backend.set_password(SERVICE, store._account('google.personal', head['generation'], 0), altered)
        head['digest'] = hashlib.sha256(altered.encode()).hexdigest()
        backend.set_password(SERVICE, 'google.personal', json.dumps(head))
    else:
        (root / 'recovery-registry' / 'personal.json').write_text('{malformed')
    before_native = dict(backend._values)
    before_registry = (root / 'recovery-registry' / 'personal.json').read_bytes()
    with pytest.raises(RuntimeError):
        store.write('google.personal', payload, replace_existing=False)
    assert backend._values == before_native
    assert (root / 'recovery-registry' / 'personal.json').read_bytes() == before_registry


def test_identical_credential_without_registry_does_not_enroll(native):
    backend, root = native
    store = CredentialStore(backend=backend, local=root)
    payload = {'token': 'synthetic-legacy-token'}
    store.write('google.personal', payload)
    path = root / 'recovery-registry' / 'personal.json'
    path.unlink()
    before_native = dict(backend._values)
    store.write('google.personal', payload, replace_existing=False)
    assert backend._values == before_native
    assert not path.exists()


def test_credential_mutations_register_before_chunks_and_cleanup(native, monkeypatch):
    backend, root = native
    store = CredentialStore(backend=backend, local=root)
    observed = []

    def pending(service, account):
        records = entries(root)
        assert len(records) == 1
        assert records[0]['account'] == 'google.personal'
        assert records[0]['state'] == 'pending'
        observed.append(account)

    backend.before_write = pending
    store.write('google.personal', {'token': 'synthetic-token'})
    assert observed and entries(root)[0]['state'] == 'active'
    original_delete = backend.delete_password
    monkeypatch.setattr(backend, 'delete_password', lambda *args: None)
    with pytest.raises(RuntimeError, match='confirmed'):
        store.delete('google.personal')
    assert store.read('google.personal') is None
    assert entries(root)[0]['state'] == 'pending'
    monkeypatch.setattr(backend, 'delete_password', original_delete)
    store.delete('google.personal')
    assert entries(root)[0]['state'] == 'deleted'
    assert store.read('google.personal') is None


def test_failed_credential_generation_can_be_replaced_without_enrolling_chunks(native, monkeypatch):
    backend, root = native
    store = CredentialStore(backend=backend, local=root)
    original = backend.set_password

    def fail_chunk(service, account, value):
        if '.chunk.' in account:
            raise OSError('synthetic failure')
        original(service, account, value)

    monkeypatch.setattr(backend, 'set_password', fail_chunk)
    with pytest.raises(RuntimeError):
        store.write('google.personal', {'token': 'synthetic-token'})
    failed_generation = entries(root)[0]['generation']
    assert entries(root)[0]['state'] == 'pending'
    monkeypatch.setattr(backend, 'set_password', original)
    store.write('google.personal', {'token': 'synthetic-token'})
    assert entries(root)[0]['state'] == 'active'
    assert entries(root)[0]['generation'] != failed_generation
    assert len(entries(root)) == 1


def client_writer(module_name, backend, monkeypatch):
    import importlib

    module = importlib.import_module(module_name)
    if module_name == 'setup_icloud_auth':
        monkeypatch.setattr(module.credentials, 'system_keyring', lambda: backend)
        return lambda service, value: module._kc_set(service, 'artha', value)
    monkeypatch.setattr(module, '_client_backend', lambda: backend)
    if module_name == 'google_auth':
        monkeypatch.setattr(module, '_status_set', lambda *args: None)
    return module._keychain_write


@pytest.mark.parametrize('module_name,service,profile', [
    ('google_auth', 'gmail-client-id', 'personal'),
    ('google_auth', 'gmail-client-secret', 'personal'),
    ('setup_msgraph_oauth', 'msgraph-client-id', 'personal'),
    ('setup_msgraph_oauth', 'msgraph-work-client-id', 'work'),
    ('setup_icloud_auth', 'icloud-apple-id', 'personal'),
    ('setup_icloud_auth', 'icloud-app-password', 'personal'),
])
def test_client_writers_register_pending_then_verified_active(native, monkeypatch, module_name, service, profile):
    backend, root = native
    write = client_writer(module_name, backend, monkeypatch)

    def pending(actual_service, account):
        entry = entries(root, profile)[0]
        assert (entry['service'], entry['account'], entry['state']) == (actual_service, account, 'pending')

    backend.before_write = pending
    write(service, 'synthetic-private-client-value')
    first = entries(root, profile)[0]['generation']
    write(service, 'synthetic-private-replacement')
    entry = entries(root, profile)[0]
    assert entry['kind'] == 'client'
    assert entry['state'] == 'active'
    assert entry['generation'] != first
    assert 'synthetic-private' not in json.dumps(entry)


@pytest.mark.parametrize('module_name,service', [
    ('google_auth', 'gmail-client-secret'),
    ('setup_msgraph_oauth', 'msgraph-client-id'),
    ('setup_icloud_auth', 'icloud-app-password'),
])
def test_client_unconfirmed_write_leaves_pending(native, monkeypatch, module_name, service):
    backend, root = native
    write = client_writer(module_name, backend, monkeypatch)
    monkeypatch.setattr(backend, 'set_password', lambda *args: None)
    with pytest.raises(RuntimeError, match='persist'):
        write(service, 'synthetic-private-value')
    assert entries(root)[0]['state'] == 'pending'


def test_google_delete_is_a_verified_tombstone(native, monkeypatch):
    import google_auth

    backend, root = native
    write = client_writer('google_auth', backend, monkeypatch)
    write('gmail-client-secret', 'synthetic-private-value')
    original = backend.delete_password
    monkeypatch.setattr(backend, 'delete_password', lambda *args: None)
    with pytest.raises(RuntimeError, match='deletion'):
        google_auth._keychain_delete('gmail-client-secret')
    assert entries(root)[0]['state'] == 'pending'
    monkeypatch.setattr(backend, 'delete_password', original)
    google_auth._keychain_delete('gmail-client-secret')
    assert entries(root)[0]['state'] == 'deleted'


def test_bundle_enrollment_metadata_is_stable_and_loading_never_enrolls(native):
    from catchup_core.bundles import BUNDLE_SCHEMA, enroll_producer, load_producer_key

    backend, root = native
    assert load_producer_key('synthetic', backend) is None
    assert not (root / 'recovery-registry').exists()
    encoded = enroll_producer('synthetic', backend)
    entry = entries(root)[0]
    assert (entry['kind'], entry['service'], entry['account'], entry['generation'], entry['state']) == (
        'bundle', 'artha-bundle-synthetic', 'secret', BUNDLE_SCHEMA, 'active')
    assert encoded not in json.dumps(entry)
    before = (root / 'recovery-registry' / 'personal.json').read_bytes()
    assert load_producer_key('synthetic', backend) is not None
    assert (root / 'recovery-registry' / 'personal.json').read_bytes() == before


def test_client_credential_and_shared_age_inspection_never_enroll(native, monkeypatch):
    import foundation
    import google_auth
    import setup_icloud_auth
    import setup_msgraph_oauth

    backend, root = native
    for module_name in ('google_auth', 'setup_icloud_auth', 'setup_msgraph_oauth'):
        client_writer(module_name, backend, monkeypatch)
    for service in ('gmail-client-secret', 'msgraph-work-client-id', 'icloud-app-password', 'age-key'):
        backend.set_password(service, 'artha', 'synthetic-inspection-value')
    monkeypatch.delenv('ARTHA_AGE_KEY', raising=False)
    monkeypatch.setattr(foundation.keyring, 'get_password', backend.get_password)
    monkeypatch.setitem(foundation._config, 'KC_SERVICE', 'age-key')
    monkeypatch.setitem(foundation._config, 'KC_ACCOUNT', 'artha')
    assert google_auth._keychain_read('gmail-client-secret') == 'synthetic-inspection-value'
    assert setup_msgraph_oauth._keychain_read('msgraph-work-client-id') == 'synthetic-inspection-value'
    assert setup_icloud_auth._kc_get('icloud-app-password', 'artha') == 'synthetic-inspection-value'
    assert foundation.get_private_key() == 'synthetic-inspection-value'
    assert CredentialStore(backend=backend, local=root).read('google.personal') is None
    assert not (root / 'recovery-registry').exists()


@pytest.mark.parametrize('boundary', ['before', 'after', 'registry-save'])
def test_process_death_at_native_head_publication_leaves_pending(tmp_path, boundary):
    from test_credentials import SQLiteKeyring
    from catchup_core.recovery_registry import RegistryError, attest_legacy, registry_digest, snapshot

    backend = SQLiteKeyring(tmp_path / 'synthetic-keyring.db')
    local = tmp_path / 'local'
    store = CredentialStore(backend=backend, local=local)
    store.write('google.personal', {'token': 'synthetic-before'})
    document = snapshot('personal', root=local, require_review=False)
    reviewed = attest_legacy('personal', registry_digest(document), 'synthetic-reviewer',
                            '2026-10-23T00:00:00+00:00', root=local)
    code = '''
import sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from test_credentials import SQLiteKeyring
from lib.credentials import CredentialStore
from catchup_core import recovery_registry
backend = SQLiteKeyring(sys.argv[3])
original = backend.set_password
original_save = recovery_registry._save
def save(local, path, lock, document):
    if sys.argv[5] == 'registry-save' and any(entry['kind'] == 'credential' and entry['state'] == 'active'
                                             for entry in document['entries']):
        print('ready', flush=True)
        sys.stdin.readline()
    original_save(local, path, lock, document)
recovery_registry._save = save
def publication(service, account, value):
    if account == 'google.personal' and sys.argv[5] == 'before':
        print('ready', flush=True)
        sys.stdin.readline()
    original(service, account, value)
    if account == 'google.personal' and sys.argv[5] == 'after':
        print('ready', flush=True)
        sys.stdin.readline()
backend.set_password = publication
CredentialStore(backend=backend, local=Path(sys.argv[4])).write('google.personal', {'token': 'synthetic-after'})
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(Path(__file__).parent),
                               str(Path(__file__).resolve().parents[2] / 'scripts'),
                               str(backend.path), str(local), boundary],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        process.kill()
        process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert entries(local)[0]['state'] == 'pending'
    with pytest.raises(RegistryError):
        snapshot('personal', root=local)
    expected = 'synthetic-before' if boundary == 'before' else 'synthetic-after'
    assert store.read('google.personal') == {'token': expected}
    if boundary == 'registry-save':
        generation = entries(local)[0]['generation']
        head = backend.get_password('artha.credentials.v1', 'google.personal')
        assert backend.get_password('artha.credentials.v1', 'google.personal.pending') is None
        store.write('google.personal', {'token': 'synthetic-after'}, replace_existing=False)
        assert entries(local)[0]['generation'] == generation
        assert backend.get_password('artha.credentials.v1', 'google.personal') == head
        assert snapshot('personal', root=local)['legacy_review'] == reviewed['legacy_review']
    else:
        store.write('google.personal', {'token': 'synthetic-retry'})
    assert entries(local)[0]['state'] == 'active'


@pytest.mark.parametrize('writer', ['key-create', 'key-rotate', 'credential-write', 'credential-delete',
                                    'google-write', 'google-delete', 'graph-write', 'icloud-write', 'bundle'])
def test_native_writers_are_excluded_by_process_capture_fence(native, monkeypatch, writer):
    from catchup_core.bundles import enroll_producer

    backend, root = native
    if writer.startswith('key-'):
        provider = KeyringKeyProvider('artha-ledger', backend=backend)
        if writer == 'key-rotate':
            provider.current_version()
            operation = provider.rotate
        else:
            operation = provider.current_version
    elif writer.startswith('credential-'):
        store = CredentialStore(backend=backend, local=root)
        if writer == 'credential-delete':
            store.write('google.personal', {'token': 'synthetic-token'})
            operation = lambda: store.delete('google.personal')
        else:
            operation = lambda: store.write('google.personal', {'token': 'synthetic-token'})
    elif writer.startswith('google-'):
        import google_auth

        write = client_writer('google_auth', backend, monkeypatch)
        if writer == 'google-delete':
            write('gmail-client-secret', 'synthetic-value')
            operation = lambda: google_auth._keychain_delete('gmail-client-secret')
        else:
            operation = lambda: write('gmail-client-secret', 'synthetic-value')
    elif writer == 'graph-write':
        write = client_writer('setup_msgraph_oauth', backend, monkeypatch)
        operation = lambda: write('msgraph-work-client-id', 'synthetic-value')
    elif writer == 'icloud-write':
        write = client_writer('setup_icloud_auth', backend, monkeypatch)
        operation = lambda: write('icloud-app-password', 'synthetic-value')
    else:
        operation = lambda: enroll_producer('synthetic', backend)
    before = dict(backend._values)
    code = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from catchup_core.session_lock import installation_fence
with installation_fence(Path(sys.argv[2]), exclusive=True):
    print('ready', flush=True)
    sys.stdin.readline()
'''
    process = subprocess.Popen([sys.executable, '-c', code,
                                str(Path(__file__).resolve().parents[2] / 'scripts'), str(root)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        with pytest.raises(RuntimeError):
            operation()
        assert backend._values == before
    finally:
        try:
            process.communicate('\n', timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    assert process.returncode == 0


def test_icloud_pair_retains_capture_fence_between_native_writes(native, monkeypatch):
    import setup_icloud_auth

    backend, root = native
    client_writer('setup_icloud_auth', backend, monkeypatch)
    monkeypatch.setattr('builtins.input', lambda *args: 'synthetic@example.invalid')
    monkeypatch.setattr(setup_icloud_auth.getpass, 'getpass', lambda *args: 'synthetic-password')
    monkeypatch.setattr(setup_icloud_auth, '_test_imap', lambda *args: (True, 'synthetic'))
    monkeypatch.setattr(setup_icloud_auth, '_test_caldav', lambda *args: (True, 'synthetic'))
    probes = []

    def probe(service, account):
        if service != setup_icloud_auth.KC_APP_PWD_SERVICE:
            return
        assert entries(root)[0]['state'] == 'active'
        code = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from catchup_core.session_lock import SessionBusy, installation_fence
try:
    with installation_fence(Path(sys.argv[2]), exclusive=True):
        pass
except SessionBusy:
    sys.exit(7)
'''
        result = subprocess.run([sys.executable, '-c', code,
                                 str(Path(__file__).resolve().parents[2] / 'scripts'), str(root)],
                                capture_output=True, text=True, timeout=10)
        probes.append(result.returncode)

    backend.before_write = probe
    setup_icloud_auth.run_setup(force=True)
    assert probes == [7]
    assert len(entries(root)) == 2
    assert all(entry['state'] == 'active' for entry in entries(root))


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission refusal')
def test_unsafe_installation_metadata_refuses_before_native_writes(native):
    from catchup_core.recovery_registry import RegistryError

    backend, root = native
    root.mkdir(mode=0o700)
    lock = root / 'installation.lock'
    lock.touch(mode=0o644)
    lock.chmod(0o644)
    with pytest.raises(RegistryError, match='owner-private'):
        CredentialStore(backend=backend, local=root).write('google.personal', {'token': 'synthetic'})
    assert backend._values == {}