"""WP-18 command surface: profiles, quiescence, key escrow and the restore privacy gate."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from catchup_core import checkpoint as ck
from catchup_core import checkpoint_cli as cli
from catchup_core.crypto import KeyringKeyProvider
from catchup_core.session_lock import session_lock

PASS = "correct horse battery staple"


def test_registry_is_required_before_any_secret_read(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))

    class NativeOnly:
        def get_password(self, service, account):
            pytest.fail('unreviewed registry accessed secret authority')

        def set_password(self, service, account, value):
            pytest.fail('collector must never enroll a secret')

        def delete_password(self, service, account):
            pytest.fail('collector must never delete a secret')

    with pytest.raises(ck.CheckpointBlocked, match='registry'):
        cli.collect_escrow(NativeOnly(), repo=tmp_path, local=tmp_path / 'runtime')


def deny_metadata(monkeypatch, target):
    """Model OS metadata denial, including Python versions whose convenience probes return False."""
    from pathlib import Path
    for name in ('stat', 'lstat'):
        original = getattr(os, name)
        def inaccessible(value, *args, _original=original, **kwargs):
            if isinstance(value, (str, os.PathLike)) and Path(value) == target:
                raise PermissionError('SYNTHETIC-PRIVATE-DEPENDENCY')
            return _original(value, *args, **kwargs)
        monkeypatch.setattr(os, name, inaccessible)


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        self.store.pop((service, name), None)


@pytest.fixture
def host(tmp_path, monkeypatch, request):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.delenv('ARTHA_LOCAL_DIR', raising=False)
    private = home / '.artha-local'
    with session_lock(private / 'installation.lock'):
        pass
    repo = tmp_path / "repo"
    (repo / "state" / "work").mkdir(parents=True)
    (repo / "state" / "goals.md").write_text("goals")
    (repo / "state" / "work" / "secret.md").write_text("work only")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "lib").write_text("x")
    local = tmp_path / "local"
    with session_lock(local / 'installation.lock'):
        pass
    (local / "catchup_core" / "shadow").mkdir(parents=True)
    ledger = sqlite3.connect(local / "catchup_core" / "shadow" / "ledger.db")
    ledger.execute("CREATE TABLE deletion_checkpoint(checkpoint_id INTEGER, at TEXT, d TEXT, g INTEGER)")
    ledger.execute("INSERT INTO deletion_checkpoint VALUES(1,'t','d',0)")
    ledger.commit()
    ledger.close()
    backend = FakeKeyring()
    backend.set_password('age-key', 'artha', 'AGE-SECRET-KEY-SYNTHETIC')
    for service in cli._KEY_SERVICES:
        KeyringKeyProvider(service, backend=backend).current_version()
    if getattr(request, 'param', True):
        KeyringKeyProvider('artha-work-checkpoint', backend=backend).current_version()
    monkeypatch.setattr(cli, "_REPO", repo)

    def review_inventory():
        from catchup_core import recovery_registry as registry
        for (service, account), value in list(backend.store.items()):
            if account.endswith('.pending') or '.chunk.' in account:
                continue
            profile = ('work' if account in cli._KNOWN_CREDENTIALS['work']
                       else registry.profile_for(service, account))
            if service == 'age-key':
                kind, generation = 'vault', 'legacy'
            elif account == 'current_version':
                kind, generation = 'key_head', value
            elif account.startswith('key.'):
                kind, generation = 'key', account.removeprefix('key.')
            elif service.startswith('artha.credentials.'):
                head = json.loads(value)
                kind, generation = 'credential', head.get('generation', 'deleted')
            elif service.startswith('artha-bundle-'):
                kind, generation = 'bundle', 'legacy'
            elif service.startswith('artha-privacy-'):
                kind, generation = 'privacy', 'legacy'
            else:
                kind, generation = 'client', 'legacy'
            for owner in ('personal', 'work') if kind == 'vault' else (profile,):
                document = registry.snapshot(owner, require_review=False) if (
                    home / '.artha-local/recovery-registry' / f'{owner}.json').exists() else None
                present = next((entry for entry in document['entries']
                                if (entry['kind'], entry['service'], entry['account']) == (kind, service, account)),
                               None) if document else None
                if present is None:
                    registry.declare(owner, kind, service, account, str(generation))
                if kind == 'credential' and generation == 'deleted':
                    with registry.mutation(owner, kind, service, account, 'deleted', deleted=True):
                        pass
        for profile in ('personal', 'work'):
            document = registry.snapshot(profile, require_review=False)
            registry.attest_legacy(profile, registry.registry_digest(document), 'synthetic fixture owner',
                                   datetime.now(UTC).isoformat())

    review_inventory()

    def run(*argv, passphrase=lambda _c: PASS, host_platform='win32'):
        out, err = io.StringIO(), io.StringIO()
        code = cli.run(argv, out, err, backend=backend, local=local, passphrase=passphrase,
                       host_platform=host_platform)
        return code, out.getvalue(), err.getvalue()
    return SimpleNamespace(run=run, repo=repo, local=local, tmp=tmp_path, backend=backend,
                           review=review_inventory)


def test_registry_preview_declaration_and_review_are_metadata_only(host, monkeypatch):
    from catchup_core import recovery_registry as registry
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('metadata command read a secret'))
    code, output, error = host.run('registry-declare', '--profile', 'personal', '--kind', 'client',
                                  '--service', 'custom-client', '--account', 'artha', '--generation', 'legacy')
    assert code == 0, error
    assert json.loads(output)['count'] > 0
    code, output, error = host.run('registry-preview', '--profile', 'personal')
    assert code == 0, error
    preview = json.loads(output)
    assert 'SYNTHETIC' not in output and PASS not in output and 'AGE-SECRET' not in output
    code, _, error = host.run('registry-review', '--profile', 'personal', '--sha256', '0' * 64,
                              '--reviewer', 'fixture owner', '--reviewed-at', datetime.now(UTC).isoformat())
    assert code == 20 and 'registry' in error
    code, _, error = host.run('registry-review', '--profile', 'personal', '--sha256', preview['sha256'],
                              '--reviewer', 'fixture owner', '--reviewed-at', datetime.now(UTC).isoformat())
    assert code == 0, error
    assert registry.snapshot('personal')['legacy_review'] is not None


@pytest.mark.parametrize('defect', ['missing', 'unreviewed', 'pending', 'malformed', 'cross-host'])
def test_registry_capture_refusals_precede_all_secret_access(host, monkeypatch, defect):
    from pathlib import Path
    from catchup_core import recovery_registry as registry
    path = Path.home() / '.artha-local/recovery-registry/personal.json'
    if defect == 'missing':
        path.unlink()
    elif defect == 'unreviewed':
        registry.declare('personal', 'client', 'custom-client', 'artha', 'legacy')
    elif defect == 'pending':
        with pytest.raises(ValueError), registry.mutation('personal', 'client', 'custom-client', 'artha', '1'):
            raise ValueError('synthetic interrupted publication')
    else:
        document = json.loads(path.read_text())
        document['host' if defect == 'cross-host' else 'entries'] = 'invalid'
        path.write_text(json.dumps(document))
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('registry refusal read keys'))
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'registry' in error and not output
    assert not (host.tmp / 'out').exists()


def test_registry_metadata_changes_invalidate_publication(host, monkeypatch):
    from catchup_core import recovery_registry as registry
    create = ck.create
    def capturing(*args, **kwargs):
        check = kwargs['before_publish']
        def changed():
            with registry.mutation('personal', 'client', 'new-client', 'artha', '1'):
                pass
            check()
        return create(*args, **{**kwargs, 'before_publish': changed})
    monkeypatch.setattr(ck, 'create', capturing)
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'registry changed' in error
    assert not list((host.tmp / 'out').glob('*.ack'))


def test_registry_discovers_custom_records_on_get_set_delete_only_backend(host):
    from catchup_core import recovery_registry as registry
    from catchup_core.bundles import enroll_producer
    from lib.credentials import CredentialStore
    CredentialStore(backend=host.backend, local=host.local).write('custom.oauth', {'token': 'SYNTHETIC-TOKEN'})
    enroll_producer('custom-producer', host.backend)
    host.backend.set_password('custom-client', 'artha', 'SYNTHETIC-CLIENT')
    host.backend.set_password('artha-privacy-custom', 'checkpoint', json.dumps({
        'checkpoint_id': 1, 'tombstone_digest': 'a' * 64, 'permission_generation': 0}))
    host.review()

    class NativeOnly:
        get_password = host.backend.get_password
        set_password = host.backend.set_password
        delete_password = host.backend.delete_password

    backend = NativeOnly()
    assert not hasattr(backend, 'store')
    items = cli.collect_escrow(backend, repo=host.repo, local=host.local)
    assert items['custom-client/artha'] == b'SYNTHETIC-CLIENT'
    assert 'artha.credentials.v1/custom.oauth' in items
    assert 'artha-bundle-custom-producer/secret' in items
    assert 'artha-privacy-custom/checkpoint' in items
    assert not any('orphan' in name for name in items)
    document = registry.snapshot('personal')
    cli._recovery_cipher(items, 'personal', entries=cli._registered_entries(document, 'personal'))


@pytest.mark.parametrize('profile,name', [('personal', 'custom.oauth'), ('work', 'msgraph.work')])
def test_refreshed_credentials_retain_all_generations_through_recovery(host, profile, name, monkeypatch):
    from pathlib import Path
    from lib.credentials import CredentialStore
    store = CredentialStore(backend=host.backend, local=host.local)
    store.write(name, {'token': 'SYNTHETIC-OLD-' * 180})
    store.write(name, {'token': 'SYNTHETIC-NEW-' * 100})
    host.review()
    if profile == 'work':
        _enroll_synthetic_work(host, monkeypatch)
    before = dict(host.backend.store)
    service = store._service(name)
    expected = {f'{service}/{account}': value.encode() for (owner, account), value in before.items()
                if owner == service and (account == name or account.startswith(name + '.chunk.'))}
    assert json.loads(expected[f'{service}/{name}'])['retired']
    items = cli.collect_escrow(host.backend, profile=profile, repo=host.repo, local=host.local)
    assert {key: items[key] for key in expected} == expected
    code, output, error = host.run('create', '--profile', profile, '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    path = Path(json.loads(output)['checkpoint'])
    recovered, _ = cli._authenticated_recovery(path, PASS)
    assert {key: recovered[key] for key in expected} == expected
    restored = FakeKeyring()
    for key, value in expected.items():
        restored.set_password(*key.split('/', 1), value.decode())
    assert CredentialStore(backend=restored, local=host.local).read(name) == {
        'token': 'SYNTHETIC-NEW-' * 100}
    assert host.backend.store == before


@pytest.mark.parametrize('damage', [
    'missing', 'extra-index', 'unknown-generation', 'duplicate-current', 'duplicate-retired',
    'too-many', 'bad-generation', 'missing-generation', 'bad-count', 'zero-count',
    'large-count', 'bool-count', 'missing-count', 'bad-ref', 'utf8', 'non-ascii', 'current-digest',
])
def test_retained_credential_refs_and_chunk_sets_fail_closed(host, damage):
    from lib.credentials import CredentialStore
    name, service = 'custom.oauth', 'artha.credentials.v1'
    store = CredentialStore(backend=host.backend, local=host.local)
    store.write(name, {'token': 'SYNTHETIC-OLD'})
    store.write(name, {'token': 'SYNTHETIC-NEW'})
    host.review()
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    head_key = f'{service}/{name}'
    head = json.loads(items[head_key])
    ref = head['retired'][0]
    chunk = f'{head_key}.chunk.{ref["generation"]}.0'
    if damage == 'missing':
        items.pop(chunk)
    elif damage == 'extra-index':
        items[chunk.removesuffix('.0') + '.1'] = b'extra'
    elif damage == 'unknown-generation':
        items[f'{head_key}.chunk.{"f" * 32}.0'] = b'unknown'
    elif damage == 'duplicate-current':
        ref['generation'] = head['generation']
    elif damage == 'duplicate-retired':
        head['deleted'] = True
        head['retired'].append(dict(ref))
    elif damage == 'too-many':
        head['retired'].append({'generation': 'e' * 32, 'chunks': 1})
    elif damage == 'bad-generation':
        ref['generation'] = '../unsafe'
    elif damage == 'missing-generation':
        ref.pop('generation')
    elif damage in {'bad-count', 'zero-count', 'large-count', 'bool-count'}:
        ref['chunks'] = {'bad-count': '1', 'zero-count': 0, 'large-count': 65, 'bool-count': True}[damage]
    elif damage == 'missing-count':
        ref.pop('chunks')
    elif damage == 'bad-ref':
        head['retired'] = ['not-a-ref']
    elif damage == 'utf8':
        items[chunk] = b'\xff'
    elif damage == 'non-ascii':
        items[chunk] = '\u00e9'.encode()
    else:
        head['digest'] = '0' * 64
    items[head_key] = json.dumps(head).encode()
    before = dict(host.backend.store)
    with pytest.raises(ck.CheckpointBlocked):
        cli._recovery_cipher(items, 'personal')
    if damage not in {'extra-index', 'unknown-generation'}:
        for key, value in items.items():
            if key.startswith(service + '/'):
                host.backend.store[tuple(key.split('/', 1))] = value.decode('utf-8', errors='replace')
        if damage == 'missing':
            host.backend.store.pop(tuple(chunk.split('/', 1)))
        damaged_native = dict(host.backend.store)
        with pytest.raises(ck.CheckpointBlocked):
            cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
        assert host.backend.store == damaged_native
    else:
        assert host.backend.store == before


def test_retained_credential_bytes_are_bound_by_publication_recollection(host, monkeypatch):
    from lib.credentials import CredentialStore
    store = CredentialStore(backend=host.backend, local=host.local)
    store.write('custom.oauth', {'token': 'SYNTHETIC-OLD'})
    store.write('custom.oauth', {'token': 'SYNTHETIC-NEW'})
    host.review()
    head = store._head('custom.oauth')
    chunk = f'custom.oauth.chunk.{head["retired"][0]["generation"]}.0'
    create = ck.create

    def changed_before_publication(*args, **kwargs):
        check = kwargs['before_publish']
        def changed():
            host.backend.set_password('artha.credentials.v1', chunk, '{"token":"SYNTHETIC-CHANGED"}')
            check()
        return create(*args, **{**kwargs, 'before_publish': changed})

    monkeypatch.setattr(ck, 'create', changed_before_publication)
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'changed' in error and not output
    assert not list((host.tmp / 'out').glob('*.ack'))


def test_deleted_tombstone_retains_bounded_cleanup_refs_without_mutation(host, monkeypatch):
    from pathlib import Path
    from catchup_core import recovery_registry as registry
    from lib.credentials import CredentialError, CredentialStore
    store = CredentialStore(backend=host.backend, local=Path.home() / '.artha-local')
    store.write('custom.oauth', {'token': 'SYNTHETIC-OLD'})
    store.write('custom.oauth', {'token': 'SYNTHETIC-NEW'})
    host.review()

    def interrupted_cleanup(*_):
        raise CredentialError('synthetic cleanup interruption')

    monkeypatch.setattr(store, '_clean', interrupted_cleanup)
    with pytest.raises(CredentialError, match='cleanup interruption'):
        store.delete('custom.oauth')
    with pytest.raises(ck.CheckpointBlocked, match='registry'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    pending = next(entry for entry in registry.snapshot('personal', require_review=False)['entries']
                   if entry['account'] == 'custom.oauth')
    with registry.mutation('personal', 'credential', 'artha.credentials.v1', 'custom.oauth',
                           pending['generation'], deleted=True):
        pass
    host.review()
    before = dict(host.backend.store)
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    head = json.loads(items['artha.credentials.v1/custom.oauth'])
    assert head['deleted'] is True and len(head['retired']) == 2
    expected = {f'{service}/{account}': value.encode() for (service, account), value in before.items()
                if service == 'artha.credentials.v1'}
    assert {key: items[key] for key in expected} == expected
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    recovered, _ = cli._authenticated_recovery(Path(json.loads(output)['checkpoint']), PASS)
    assert {key: recovered[key] for key in expected} == expected
    assert host.backend.store == before
    bad_head = {**head, 'retired': head['retired'] + [{'generation': 'f' * 32, 'chunks': 1}]}
    items['artha.credentials.v1/custom.oauth'] = json.dumps(bad_head).encode()
    with pytest.raises(ck.CheckpointBlocked, match='malformed'):
        cli._recovery_cipher(items, 'personal')


def test_registered_missing_custom_record_blocks_and_deleted_record_is_not_resurrected(host):
    from catchup_core import recovery_registry as registry
    with registry.mutation('personal', 'client', 'missing-client', 'artha', '1'):
        pass
    with pytest.raises(ck.CheckpointBlocked, match='missing'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    with registry.mutation('personal', 'client', 'missing-client', 'artha', '2', deleted=True):
        pass
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    assert 'missing-client/artha' not in items


@pytest.mark.parametrize('kind,service,account', [
    ('client', 'custom-client', 'artha'),
    ('bundle', 'artha-bundle-custom', 'secret'),
    ('privacy', 'artha-privacy-custom', 'checkpoint'),
    ('key', 'custom-key', 'key.1'),
    ('key_head', 'custom-key', 'current_version'),
    ('credential', 'artha.credentials.v1', 'custom.oauth'),
])
@pytest.mark.parametrize('present', [False, True])
def test_deleted_registry_entries_require_native_absence(host, kind, service, account, present):
    from catchup_core import recovery_registry as registry
    if present:
        host.backend.set_password(service, account, 'SYNTHETIC-STILL-ACTIVE')
    with registry.mutation('personal', kind, service, account, '1', deleted=True):
        pass
    if present:
        with pytest.raises(ck.CheckpointBlocked, match='deleted registry dependency'):
            cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    else:
        items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
        assert f'{service}/{account}' not in items


@pytest.mark.parametrize('defect', ['active', 'retired', 'pending'])
def test_deleted_credential_head_must_be_verified_before_capture(host, defect):
    from catchup_core import recovery_registry as registry
    from lib.credentials import CredentialStore
    store = CredentialStore(backend=host.backend, local=host.local)
    store.write('custom.oauth', {'token': 'synthetic'})
    store.delete('custom.oauth')
    host.review()
    if defect == 'active':
        store.write('custom.oauth', {'token': 'still-active'})
        with registry.mutation('personal', 'credential', 'artha.credentials.v1', 'custom.oauth', '1', deleted=True):
            pass
    elif defect == 'retired':
        host.backend.set_password('artha.credentials.v1', 'custom.oauth', json.dumps({
            'schema': 'artha.credential.v1', 'deleted': True, 'retired': ['uncollected']}))
    else:
        host.backend.set_password('artha.credentials.v1', 'custom.oauth.pending', 'pending')
    with pytest.raises(ck.CheckpointBlocked):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


def test_deleted_custom_credential_tombstone_is_captured(host):
    from lib.credentials import CredentialStore
    store = CredentialStore(backend=host.backend, local=host.local)
    store.write('custom.oauth', {'token': 'synthetic'})
    store.delete('custom.oauth')
    host.review()
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    assert json.loads(items['artha.credentials.v1/custom.oauth'])['deleted'] is True
    cli._recovery_cipher(items, 'personal', entries=cli._registered_entries(cli._registry_snapshot('personal'),
                                                                         'personal'))


@pytest.mark.parametrize('kind', ['client', 'bundle', 'privacy', 'credential'])
def test_registered_active_record_cannot_be_silently_omitted(host, monkeypatch, kind):
    from catchup_core import recovery_registry as registry
    service, account = {'client': ('custom-client', 'artha'),
                        'bundle': ('artha-bundle-custom', 'secret'),
                        'privacy': ('artha-privacy-custom', 'checkpoint'),
                        'credential': ('artha.credentials.v1', 'custom.oauth')}[kind]
    with registry.mutation('personal', kind, service, account, '1'):
        host.backend.set_password(service, account, 'synthetic-present-but-not-collected')
    monkeypatch.setattr(cli, '_collect_credential_recovery_and_records', lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, '_collect_bundle_and_privacy_records', lambda *_a, **_kw: None)
    with pytest.raises(ck.CheckpointBlocked):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


@pytest.mark.parametrize('kind,service,account', [
    ('client', 'custom-client', 'artha'),
    ('bundle', 'artha-bundle-custom', 'secret'),
    ('privacy', 'artha-privacy-custom', 'checkpoint'),
    ('credential', 'artha.credentials.v1', 'custom.oauth'),
])
def test_registered_escrow_field_must_match_native_record(host, monkeypatch, kind, service, account):
    import base64
    import hashlib
    from catchup_core import recovery_registry as registry
    raw = {'client': 'SYNTHETIC-NATIVE',
           'bundle': base64.b64encode(b'n' * 32).decode(),
           'privacy': json.dumps({'checkpoint_id': 1, 'tombstone_digest': 'a' * 64,
                                  'permission_generation': 0}),
           'credential': json.dumps({'schema': 'artha.credential.v1', 'generation': 'a' * 32,
                                     'chunks': 1, 'digest': hashlib.sha256(b'{}').hexdigest(),
                                     'retired': []})}[kind]
    generation = 'a' * 32 if kind == 'credential' else 'legacy'
    with registry.mutation('personal', kind, service, account, generation):
        host.backend.set_password(service, account, raw)

    def wrong_bytes(_backend, items, **_kwargs):
        value = {'client': b'SYNTHETIC-DIFFERENT', 'bundle': b'd' * 32,
                 'privacy': raw.replace('"checkpoint_id": 1', '"checkpoint_id": 2').encode(),
                 'credential': raw.replace('a' * 32, 'b' * 32).encode()}[kind]
        items[f'{service}/{account}'] = value
        if kind == 'credential':
            items[f'{service}/{account}.chunk.' + 'b' * 32 + '.0'] = b'{}'

    monkeypatch.setattr(cli, '_collect_credential_recovery_and_records', wrong_bytes)
    monkeypatch.setattr(cli, '_collect_bundle_and_privacy_records', lambda *_a, **_kw: None)
    with pytest.raises(ck.CheckpointBlocked, match='differs|changed'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


def test_registered_bundle_generation_is_not_arbitrary(host):
    from catchup_core import recovery_registry as registry
    from catchup_core.bundles import enroll_producer
    enroll_producer('custom', host.backend)
    with registry.mutation('personal', 'bundle', 'artha-bundle-custom', 'secret', 'unknown-generation'):
        pass
    with pytest.raises(ck.CheckpointBlocked, match='generation'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


def test_registered_key_bytes_must_match_final_native_read(host, monkeypatch):
    import base64
    original = host.backend.get_password
    reads = 0

    def changed(service, account):
        nonlocal reads
        if (service, account) == ('artha-ledger', 'key.1'):
            reads += 1
            if reads >= 4:
                return base64.b64encode(b'x' * 32).decode('ascii')
        return original(service, account)

    monkeypatch.setattr(host.backend, 'get_password', changed)
    with pytest.raises(ck.CheckpointBlocked, match='differs|changed'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


def test_work_registry_custom_client_is_captured_on_native_only_backend(host):
    from catchup_core import recovery_registry as registry
    with registry.mutation('work', 'client', 'work.custom-client', 'artha', 'legacy'):
        host.backend.set_password('work.custom-client', 'artha', 'SYNTHETIC-WORK-CLIENT')

    class NativeOnly:
        get_password = host.backend.get_password

        def set_password(self, *_args):
            pytest.fail('read-only collection attempted enrollment')

    items = cli.collect_escrow(NativeOnly(), profile='work', repo=host.repo, local=host.local)
    assert items['work.custom-client/artha'] == b'SYNTHETIC-WORK-CLIENT'
    cli._recovery_cipher(items, 'work', entries=cli._registered_entries(cli._registry_snapshot('work'), 'work'))


@pytest.mark.parametrize('service', ['artha-ledger', 'artha-credential-recovery-personal'])
def test_registered_current_version_bytes_are_captured_and_validated(host, service):
    KeyringKeyProvider(service, backend=host.backend).current_version()
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    assert items[f'{service}/current_version'] == b'1'
    entries = cli._registered_entries(cli._registry_snapshot('personal'), 'personal')
    cli._recovery_cipher(items, 'personal', entries=entries)
    for changed in (b'2', b'01'):
        items[f'{service}/current_version'] = changed
        with pytest.raises(ck.CheckpointBlocked, match='head|generation'):
            cli._recovery_cipher(items, 'personal', entries=entries)


def test_registered_privacy_generation_matches_native_checkpoint(host):
    from catchup_core import recovery_registry as registry
    host.backend.set_password('artha-privacy-custom', 'checkpoint', json.dumps({
        'checkpoint_id': 1, 'tombstone_digest': 'a' * 64, 'permission_generation': 0}))
    with registry.mutation('personal', 'privacy', 'artha-privacy-custom', 'checkpoint', '2'):
        pass
    with pytest.raises(ck.CheckpointBlocked, match='generation'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


def test_registry_is_canonical_despite_runtime_override_and_supports_explicit_injection(host, monkeypatch):
    from pathlib import Path
    from catchup_core import recovery_registry as registry
    runtime = host.tmp / 'different-runtime'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(runtime))
    assert cli.collect_escrow(host.backend, repo=host.repo, local=runtime)
    document = registry.snapshot('personal')
    root = host.tmp / 'injected-registry'
    for entry in document['entries']:
        registry.declare('personal', entry['kind'], entry['service'], entry['account'], entry['generation'], root=root)
    document = registry.snapshot('personal', root=root, require_review=False)
    registry.attest_legacy('personal', registry.registry_digest(document), 'synthetic injected owner',
                           datetime.now(UTC).isoformat(), root=root)
    (Path.home() / '.artha-local/recovery-registry/personal.json').unlink()
    assert cli.collect_escrow(host.backend, repo=host.repo, local=runtime, registry_root=root)
    with pytest.raises(ck.CheckpointBlocked, match='registry'):
        cli.collect_escrow(host.backend, repo=host.repo, local=runtime)


def test_registry_requires_all_default_key_dependencies_and_preserves_real_version_bytes(host):
    from pathlib import Path
    from catchup_core import recovery_registry as registry
    provider = KeyringKeyProvider('artha-ledger', backend=host.backend)
    provider.rotate()
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    assert items['artha-ledger/key.1'] == provider.key(1)
    assert items['artha-ledger/key.2'] == provider.key(2)
    document = registry.snapshot('personal')
    document['entries'] = [entry for entry in document['entries']
                           if (entry['service'], entry['account']) != ('artha-ledger-ids', 'key.1')]
    path = Path.home() / '.artha-local/recovery-registry/personal.json'
    path.write_text(json.dumps(document))
    with pytest.raises(ck.CheckpointBlocked, match='absent from the reviewed registry'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


def test_registry_files_are_profile_scoped_checkpoint_dependencies(host):
    from pathlib import Path
    roots, excluded = cli.profile_layout('personal', host.local)
    found, problems, _ = ck._walk(roots, excluded)
    assert not problems
    personal = Path.home() / '.artha-local/recovery-registry/personal.json'
    work = personal.with_name('work.json')
    assert personal in found.values() and work not in found.values()
    (host.local / 'work').mkdir(parents=True, exist_ok=True)
    roots, excluded = cli.profile_layout('work', host.local)
    found, problems, _ = ck._walk(roots, excluded)
    assert not problems and work in found.values() and personal not in found.values()


@pytest.mark.parametrize('field,value', [('service', '../unsafe'), ('account', 'x' * 129),
                                         ('generation', 'bad\nmetadata')])
def test_registry_declarations_bound_and_validate_names_without_native_access(host, monkeypatch, field, value):
    values = {'service': 'custom-client', 'account': 'artha', 'generation': 'legacy'}
    values[field] = value
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('declaration accessed secrets'))
    code, output, error = host.run('registry-declare', '--profile', 'personal', '--kind', 'client',
                                  '--service', values['service'], '--account', values['account'],
                                  '--generation', values['generation'])
    assert code == 20 and not output and 'invalid or unbounded' in error


def test_registered_custom_records_survive_inspection_without_native_access_or_orphan_chunks(host, monkeypatch):
    from pathlib import Path
    from lib.credentials import CredentialStore
    store = CredentialStore(backend=host.backend, local=host.local)
    store.write('custom.oauth', {'token': 'SYNTHETIC-PRIVATE-CUSTOM'})
    host.backend.set_password('artha.credentials.v1', 'custom.oauth.chunk.' + 'f' * 32 + '.0', 'orphan')
    host.backend.set_password('custom-client', 'artha', 'SYNTHETIC-CUSTOM-CLIENT')
    host.review()
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    path = Path(json.loads(output)['checkpoint'])
    items = ck.read_escrow(path, PASS)
    assert items['custom-client/artha'] == b'SYNTHETIC-CUSTOM-CLIENT'
    assert not any('f' * 32 in key for key in items)
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('inspection accessed native store'))
    monkeypatch.setattr(cli, '_registry_snapshot', lambda *_a, **_kw: pytest.fail('old review used as current'))
    code, output, error = host.run('recovery', str(path), '--inspection-only')
    assert code == 0 and json.loads(output)['activation_allowed'] is False, error
    assert 'SYNTHETIC' not in output + error


@pytest.mark.parametrize('defect', ['profile', 'entries', 'entry', 'kind', 'service', 'account',
                                   'cross-profile', 'binding', 'duplicate'])
def test_registry_record_bindings_fail_closed_before_native_access(host, monkeypatch, defect):
    entry = {'kind': 'vault', 'service': 'age-key', 'account': 'artha', 'generation': 'legacy',
             'state': 'declared', 'attempt': 'synthetic'}
    document = {'profile': 'personal', 'entries': [entry]}
    if defect == 'profile':
        document['profile'] = 'work'
    elif defect == 'entries':
        document['entries'] = {}
    elif defect == 'entry':
        document['entries'] = [None]
    elif defect in {'kind', 'service', 'account'}:
        entry[defect] = 'invalid/value'
    elif defect == 'cross-profile':
        entry.update(kind='client', service='msgraph-work-client-id')
    elif defect == 'binding':
        entry['account'] = 'wrong'
    else:
        document['entries'].append(dict(entry))
    monkeypatch.setattr(cli, '_registry_snapshot', lambda *_a, **_kw: document)
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('invalid binding read secrets'))
    with pytest.raises(ck.CheckpointBlocked, match='registry'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


@pytest.mark.parametrize('kind', ['credential', 'key', 'key_head'])
def test_registry_rejects_changed_native_generation(host, kind):
    from catchup_core import recovery_registry as registry
    from lib.credentials import CredentialStore
    if kind == 'credential':
        CredentialStore(backend=host.backend, local=host.local).write('custom.oauth', {'token': 'synthetic'})
        host.review()
        service, account, generation = 'artha.credentials.v1', 'custom.oauth', 'f' * 32
    else:
        service = 'artha-ledger'
        account = 'key.1' if kind == 'key' else 'current_version'
        generation = '2'
    with registry.mutation('personal', kind, service, account, generation):
        pass
    with pytest.raises(ck.CheckpointBlocked, match='generation'):
        cli.collect_escrow(host.backend, repo=host.repo, local=host.local)


def test_pending_registry_preview_is_read_only_and_does_not_approve_capture(host, monkeypatch):
    from pathlib import Path
    from catchup_core import recovery_registry as registry
    with pytest.raises(ValueError), registry.mutation('personal', 'client', 'custom-client', 'artha', '1'):
        raise ValueError('synthetic interrupted writer')
    path = Path.home() / '.artha-local/recovery-registry/personal.json'
    before = path.read_bytes()
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('pending preview read secrets'))
    code, output, error = host.run('registry-preview', '--profile', 'personal')
    assert code == 0 and any(entry['state'] == 'pending' for entry in json.loads(output)['records']), error
    assert path.read_bytes() == before
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'registry' in error and not output


def test_profiles_keep_work_apart_and_reject_unknown(host):
    from pathlib import Path
    with pytest.raises(ValueError):
        cli.profile_layout("other")
    personal, exclude = cli.profile_layout("personal")
    work, _ = cli.profile_layout("work")
    assert "repo/state/work" in exclude
    assert set(work) == {"work_state", "work_local", "recovery_registry"}
    assert work["work_local"] == Path.home() / '.artha-local' / "work" and "local" in personal


def test_checkpoint_uses_the_runtime_local_directory_override(tmp_path, monkeypatch, host):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'runtime-local'))
    roots, _ = cli.profile_layout('personal')
    assert roots['local'] == tmp_path / 'runtime-local'


def test_external_action_store_cannot_be_omitted(host, monkeypatch):
    queue = host.tmp / 'external' / 'actions.db'
    queue.parent.mkdir()
    with closing(sqlite3.connect(queue)) as db, db:
        db.execute('CREATE TABLE actions(status TEXT)')
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(queue))
    code, out, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    from pathlib import Path
    entries = ck.verify(Path(json.loads(out)['checkpoint']), cli._cipher(host.backend))['entries']
    assert any(row['path'] == 'actions/actions.db' for row in entries)


def test_privacy_capsules_outside_the_runtime_directory_are_preserved(host):
    from pathlib import Path
    privacy = Path.home() / '.artha-local/privacy'
    privacy.mkdir()
    capsule = cli._cipher(host.backend).encrypt(b'synthetic privacy history', aad=b'fixture')[0]
    (privacy / 'current.ae1').write_bytes(capsule)
    code, out, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    manifest = ck.verify(Path(json.loads(out)['checkpoint']), cli._cipher(host.backend))
    assert any(row['path'] == 'privacy/current.ae1' for row in manifest['entries'])


def test_privacy_override_outside_runtime_and_default_roots_is_preserved(host, monkeypatch):
    from pathlib import Path
    privacy = host.tmp / 'external-privacy'
    privacy.mkdir()
    (privacy / 'synthetic-capsule.ae1').write_bytes(b'synthetic encrypted capsule')
    monkeypatch.setenv('ARTHA_PRIVACY_DIR', str(privacy))
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    manifest = ck.verify(Path(json.loads(output)['checkpoint']), cli._cipher(host.backend))
    assert any(row['path'].endswith('/synthetic-capsule.ae1') for row in manifest['entries'])


def test_privacy_override_cannot_relabel_work_content_as_personal(host, monkeypatch):
    monkeypatch.setenv('ARTHA_PRIVACY_DIR', str(host.repo / 'state/work'))
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('Work dependency read keys'))
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'Work' in error
    assert not (host.tmp / 'out').exists()


def test_personal_checkpoint_excludes_known_work_credential_artifacts(host):
    tokens = host.repo / '.tokens'
    tokens.mkdir()
    (tokens / 'msgraph-work-token.json').write_text('SYNTHETIC-WORK-TOKEN')
    recovery = host.local / 'credential-recovery'
    recovery.mkdir()
    (recovery / 'work-synthetic.ae1').write_bytes(b'SYNTHETIC-WORK-RECOVERY')
    code, out, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    from pathlib import Path
    manifest = ck.verify(Path(json.loads(out)['checkpoint']), cli._cipher(host.backend))
    assert not any('msgraph-work-token' in row['path'] or 'work-synthetic' in row['path']
                   for row in manifest['entries'])


def test_new_external_dependency_during_capture_blocks_publication(host, monkeypatch):
    queue = host.tmp / 'external' / 'actions.db'
    queue.parent.mkdir()
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(queue))
    create = ck.create
    def appears(*a, **kw):
        check = kw.get('before_publish', lambda: None)
        def changed():
            with closing(sqlite3.connect(queue)) as db, db:
                db.execute('CREATE TABLE actions(status TEXT)')
            check()
        return create(*a, **{**kw, 'before_publish': changed})
    monkeypatch.setattr(ck, 'create', appears)
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'dependencies changed' in error
    assert not list((host.tmp / 'out').glob('*.ack'))


def test_executing_action_in_an_external_store_blocks_capture(host, monkeypatch):
    queue = host.tmp / 'external' / 'actions.db'
    queue.parent.mkdir()
    with closing(sqlite3.connect(queue)) as db, db:
        db.execute('CREATE TABLE actions(status TEXT)')
        db.execute("INSERT INTO actions VALUES('executing')")
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(queue))
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('busy capture accessed recovery keys'))
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'executing' in error
    assert not (host.tmp / 'out').exists()


def test_first_capture_of_a_new_platform_store_does_not_conflict_with_its_own_fence(host, monkeypatch):
    from catchup_core import runtime
    queue = host.tmp / 'new-platform-store' / 'actions.db'
    monkeypatch.delenv('ARTHA_LOCAL_DB', raising=False)
    monkeypatch.setattr(runtime, 'action_database_path', lambda _: queue)
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    assert queue.parent.is_dir()


@pytest.mark.parametrize('dependency', ['local', 'actions'])
@pytest.mark.parametrize('profile', ['personal', 'work'])
def test_profile_dependency_cannot_override_work_content_boundary(host, monkeypatch, dependency, profile):
    if dependency == 'local':
        with pytest.raises(ck.CheckpointBlocked):
            cli.profile_layout(profile, host.repo / 'state/work')
    else:
        monkeypatch.setenv('ARTHA_LOCAL_DB', str(host.repo / 'state/work/actions.db'))
        with pytest.raises(ck.CheckpointBlocked):
            cli.profile_layout(profile, host.local)


def test_unreadable_optional_dependency_is_not_mistaken_for_absence(host, monkeypatch):
    queue = host.tmp / 'external/actions.db'
    queue.parent.mkdir()
    queue.write_bytes(b'synthetic database')
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(queue))
    deny_metadata(monkeypatch, queue)
    code, out, error = host.run('inventory')
    assert code == 20 and 'metadata' in error
    assert 'SYNTHETIC-PRIVATE-DEPENDENCY' not in out + error


def test_quiescence_cannot_treat_unreadable_action_metadata_as_an_absent_queue(host, monkeypatch):
    queue = host.local / 'actions.db'
    with closing(sqlite3.connect(queue)) as db, db:
        db.execute('CREATE TABLE actions(status TEXT)')
    deny_metadata(monkeypatch, queue)
    assert cli.busy_reasons(host.local) == ['action state cannot be determined']


def test_action_path_alias_does_not_acquire_its_own_fence_twice(host, monkeypatch):
    (host.local / 'nested').mkdir()
    queue = host.local / 'nested/../actions.db'
    with closing(sqlite3.connect(queue)) as db, db:
        db.execute('CREATE TABLE actions(status TEXT)')
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(queue))
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error


def test_relative_action_dependency_is_refused_before_capture(host, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DB', 'relative-actions.db')
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'absolute path' in error and not (host.tmp / 'out').exists()


def test_external_dependency_link_is_not_silently_followed_or_deduplicated(host, monkeypatch):
    target = host.local / 'actions.db'
    with closing(sqlite3.connect(target)) as db, db:
        db.execute('CREATE TABLE actions(status TEXT)')
    link = host.tmp / 'action-link.db'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('creating a symbolic link requires host support/privilege')
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(link))
    code, out, _ = host.run('inventory')
    assert code == 20 and json.loads(out)['blocking'] > 0


def test_inventory_reports_counts_and_blockers(host):
    code, out, _ = host.run("inventory")
    data = json.loads(out)
    assert code == 0 and data["ready"] and data["files"] == 4 and data["excluded"] >= 2
    (host.repo / "state" / "SYNTHETIC-PRIVATE-FILENAME-link").symlink_to(host.repo / "state" / "goals.md")
    code, out, _ = host.run("inventory")
    assert code == 20 and json.loads(out)["ready"] is False and json.loads(out)['blocking'] > 0
    assert 'SYNTHETIC-PRIVATE-FILENAME' not in out
    code, out, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'SYNTHETIC-PRIVATE-FILENAME' not in out + error


def test_personal_checkpoint_round_trip_and_work_isolation(host):
    dest = host.tmp / "out"
    code, out, err = host.run("create", "--dest", str(dest))
    assert code == 0, err
    path = json.loads(out)["checkpoint"]
    manifest = ck.verify(__import__("pathlib").Path(path), cli._cipher(host.backend))
    names = {e["path"] for e in manifest["entries"]}
    assert "repo/state/goals.md" in names and not any("work/secret" in n or ".venv" in n for n in names)
    assert set(manifest["escrow_items"]) == {
        f'{service}/{account}' for service in cli._KEY_SERVICES for account in ('key.1', 'current_version')
    } | {'age-key/artha'}
    assert json.loads(host.run("verify", path)[1])["verified"] is True
    assert json.loads(host.run("recovery", path)[1])["recoverable"] is True
    (host.repo / "state" / "goals.md").write_text("changed")
    assert json.loads(host.run("diff", path)[1])["changed"] == 1
    code, _, err = host.run("restore", path, "--to", str(host.tmp / "restored"))
    assert code == 20 and "privacy history" in err and not (host.tmp / "restored").exists()


@pytest.mark.parametrize('replacement', ['pathname', 'in_place'])
def test_recovery_inspection_pins_escrow_and_payload_to_one_input(host, monkeypatch, replacement):
    from pathlib import Path
    code, output, error = host.run('create', '--dest', str(host.tmp / 'first'))
    assert code == 0, error
    checkpoint = Path(json.loads(output)['checkpoint'])
    generation = ck.verify(checkpoint, cli._cipher(host.backend))['generation']
    code, output, error = host.run('create', '--dest', str(host.tmp / 'second'))
    assert code == 0, error
    alternate = Path(json.loads(output)['checkpoint'])
    alternate_bytes = alternate.read_bytes()
    read_head = ck._read_head
    swapped = False
    rename_denied = False

    def replace_after_escrow_read(handle):
        nonlocal swapped, rename_denied
        result = read_head(handle)
        if not swapped:
            swapped = True
            if replacement == 'pathname':
                try:
                    alternate.replace(checkpoint)
                except PermissionError as exc:
                    assert sys.platform == 'win32' and exc.winerror in (5, 32)
                    rename_denied = True
            else:
                checkpoint.write_bytes(alternate_bytes)
        return result

    monkeypatch.setattr(ck, '_read_head', replace_after_escrow_read)
    if replacement == 'in_place':
        with pytest.raises(ck.CheckpointBlocked, match='changed'):
            cli._inspect_recovery(checkpoint, PASS)
    else:
        result = cli._inspect_recovery(checkpoint, PASS)
        assert result['generation'] == generation
        if sys.platform == 'win32':
            assert rename_denied and alternate.exists()
            alternate.replace(checkpoint)
            assert checkpoint.read_bytes() == alternate_bytes


def test_work_profile_stays_on_host_and_refuses_a_copy(host, monkeypatch):
    _enroll_synthetic_work(host, monkeypatch)
    dest = host.tmp / "work-out"
    code, out, err = host.run("create", "--profile", "work", "--dest", str(dest))
    assert code == 0, err
    names = {e["path"] for e in ck.verify(__import__("pathlib").Path(json.loads(out)["checkpoint"]),
                                          cli._cipher(host.backend, profile='work'))["entries"]}
    assert names == {"work_state/secret.md", "work_local/cache/index.ae1",
                     "recovery_registry/work.json"}
    code, _, err = host.run("create", "--profile", "work", "--dest", str(host.tmp / "w2"),
                            "--copy-to", str(host.tmp / "elsewhere"))
    assert code == 20 and "stays on this host" in err
    assert not (host.tmp / 'w2').exists(), 'invalid Work copying must be refused before publishing a checkpoint'


def test_work_checkpoint_has_separate_envelope_and_shared_vault_recovery_only(host, monkeypatch):
    from pathlib import Path
    _enroll_synthetic_work(host, monkeypatch)
    code, out, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    path = Path(json.loads(out)['checkpoint'])
    keys = ck.read_escrow(path, PASS)
    assert set(keys) == {'artha-work-checkpoint/key.1', 'artha-work-checkpoint/current_version', 'age-key/artha'}
    assert keys['age-key/artha'] == b'AGE-SECRET-KEY-SYNTHETIC'
    with pytest.raises(ck.CheckpointError, match='authentication'):
        ck.verify(path, cli._cipher(host.backend))
    assert json.loads(host.run('verify', str(path), '--profile', 'work')[1])['verified'] is True
    # A lost device can inspect using only the passphrase. No personal or Work OS keys are consulted/installed.
    host.backend.store.clear()
    code, out, error = host.run('recovery', str(path), '--inspection-only')
    assert code == 0, error
    assert json.loads(out)['profile'] == 'work' and json.loads(out)['activation_allowed'] is False
    assert host.backend.store == {}


@pytest.mark.parametrize('host', [False], indirect=True)
def test_first_work_checkpoint_enrolls_and_captures_exact_registry(host, monkeypatch):
    from pathlib import Path
    from catchup_core import recovery_registry as registry
    _enroll_synthetic_work(host, monkeypatch)
    before = registry.snapshot('work')
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'first-work'))
    assert code == 0, error
    path = Path(json.loads(output)['checkpoint'])
    after = registry.snapshot('work')
    assert after['legacy_review'] == before['legacy_review']
    enrolled = [entry for entry in after['entries'] if entry['service'] == 'artha-work-checkpoint']
    assert {(entry['kind'], entry['account'], entry['generation'], entry['state']) for entry in enrolled} == {
        ('key', 'key.1', '1', 'active'), ('key_head', 'current_version', '1', 'active')}
    escrow = ck.read_escrow(path, PASS)
    assert escrow['artha-work-checkpoint/current_version'] == b'1'
    assert escrow['artha-work-checkpoint/key.1'] == KeyringKeyProvider(
        'artha-work-checkpoint', backend=host.backend, create=False).key(1)
    manifest = ck.verify(path, cli._cipher(host.backend, profile='work'))
    assert manifest['context']['recovery_registry'] == after
    registry_file = Path.home() / '.artha-local/recovery-registry/work.json'
    row = next(row for row in manifest['entries'] if row['path'] == 'recovery_registry/work.json')
    import hashlib
    assert row['sha256'] == hashlib.sha256(registry_file.read_bytes()).hexdigest()
    assert host.run('recovery', str(path), '--inspection-only')[0] == 0


@pytest.mark.parametrize('host', [False], indirect=True)
def test_first_work_collection_requires_explicit_create_flag(host):
    from catchup_core import recovery_registry as registry
    before = registry.snapshot('work')
    with pytest.raises(cli.KeyUnavailableError):
        cli.collect_escrow(host.backend, profile='work', repo=host.repo, local=host.local)
    assert host.backend.get_password('artha-work-checkpoint', 'current_version') is None
    assert registry.snapshot('work') == before
    items = cli.collect_escrow(host.backend, profile='work', create=True, repo=host.repo, local=host.local)
    assert items['artha-work-checkpoint/current_version'] == b'1'


@pytest.mark.parametrize('host', [False], indirect=True)
def test_first_work_enrollment_failure_preserves_pending_registry(host, monkeypatch):
    from catchup_core import recovery_registry as registry
    _enroll_synthetic_work(host, monkeypatch)

    def interrupted(*_args):
        raise RuntimeError('synthetic enrollment interruption')

    monkeypatch.setattr(host.backend, 'set_password', interrupted)
    code, _, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'interrupted-first'))
    assert code == 20 and 'credential store' in error
    document = registry.snapshot('work', require_review=False)
    assert any(entry['state'] == 'pending' for entry in document['entries'])
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('pending enrollment read secrets'))
    code, _, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'retry-first'))
    assert code == 20 and 'registry' in error
    assert not (host.tmp / 'interrupted-first').exists() and not (host.tmp / 'retry-first').exists()


@pytest.mark.parametrize('host', [False], indirect=True)
def test_first_work_enrollment_cannot_write_outside_injected_registry(host, monkeypatch):
    from catchup_core import recovery_registry as registry
    root = host.tmp / 'injected-registry'
    registry.declare('work', 'vault', 'age-key', 'artha', 'legacy', root=root)
    before = registry.snapshot('work', root=root, require_review=False)
    registry.attest_legacy('work', registry.registry_digest(before), 'synthetic owner',
                           datetime.now(UTC).isoformat(), root=root)
    before = registry.snapshot('work', root=root)
    canonical = registry.snapshot('work')
    monkeypatch.setattr(host.backend, 'set_password', lambda *_: pytest.fail('injected enrollment wrote secrets'))
    with pytest.raises(ck.CheckpointBlocked, match='canonical recovery registry'):
        cli.collect_escrow(host.backend, profile='work', create=True, repo=host.repo,
                           local=host.local, registry_root=root)
    assert registry.snapshot('work', root=root) == before
    assert registry.snapshot('work') == canonical


@pytest.mark.parametrize('host', [False], indirect=True)
@pytest.mark.parametrize('defect', ['unreviewed', 'pending', 'orphan-key', 'deleted-key'])
def test_first_work_creation_never_bypasses_registry_or_adopts_existing_keys(host, monkeypatch, defect):
    from catchup_core import recovery_registry as registry
    if defect == 'unreviewed':
        registry.declare('work', 'client', 'work.custom-client', 'artha', 'legacy')
    elif defect == 'pending':
        with pytest.raises(ValueError), registry.mutation('work', 'key', 'artha-work-checkpoint', 'key.1', '1'):
            raise ValueError('synthetic interrupted enrollment')
    elif defect == 'orphan-key':
        host.backend.set_password('artha-work-checkpoint', 'key.1', 'orphan')
    else:
        with registry.mutation('work', 'key', 'artha-work-checkpoint', 'key.1', '1', deleted=True):
            pass
    before = registry.snapshot('work', require_review=False)
    monkeypatch.setattr(host.backend, 'set_password', lambda *_: pytest.fail('unsafe first-time enrollment'))
    code, _, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'blocked-first'))
    assert code == 20 and error.startswith('blocked:')
    assert registry.snapshot('work', require_review=False) == before
    assert not (host.tmp / 'blocked-first').exists()


@pytest.mark.parametrize('profile', ['personal', 'work'])
def test_missing_shared_vault_key_blocks_checkpoint_recovery_claim(host, profile, monkeypatch):
    host.backend.store.pop(('age-key', 'artha'))
    if profile == 'work':
        _enroll_synthetic_work(host, monkeypatch)
    code, _, error = host.run('create', '--profile', profile, '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and 'vault key' in error
    assert not (host.tmp / 'blocked').exists()


def test_vault_backend_error_cannot_disclose_private_material(host, monkeypatch):
    def unavailable(*_):
        raise RuntimeError('SYNTHETIC-PRIVATE-KEY-MATERIAL')
    monkeypatch.setattr(host.backend, 'get_password', unavailable)
    code, output, error = host.run('create', '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and 'vault key' in error
    assert 'SYNTHETIC-PRIVATE-KEY-MATERIAL' not in output + error


@pytest.mark.parametrize('defect', ['machine', 'identity', 'identity-key', 'head', 'history'])
def test_work_restore_requires_the_original_current_work_identity(host, monkeypatch, defect):
    from catchup_core import work_privacy
    protocol = _enroll_synthetic_work(host, monkeypatch)
    code, out, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    path = json.loads(out)['checkpoint']
    if defect == 'machine':
        monkeypatch.setattr(work_privacy, 'native_windows_identity',
                            lambda: {'platform': 'win32', 'machine': 'different-machine',
                                     'user': 'S-1-5-21-1001'})
    elif defect == 'identity':
        (protocol.authority_dir / 'identity').unlink()
    elif defect == 'identity-key':
        protocol.backend.delete_password(protocol.KEY_SERVICE, 'key')
    elif defect == 'head':
        protocol.backend.delete_password(protocol.HEAD_SERVICE, 'head')
    else:
        head = protocol.current()['head']
        (protocol.authority_dir / (head['capsule_sha256'] + '.ae1')).unlink()
    code, _, error = host.run('restore', path, '--profile', 'work', '--to', str(host.tmp / 'restore'))
    assert code == 20 and 'identity' in error.lower()
    assert not (host.tmp / 'restore').exists()


def test_work_restore_keeps_all_members_encrypted_in_quarantine(host, monkeypatch):
    _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    checkpoint = json.loads(output)['checkpoint']
    code, output, error = host.run('recovery', checkpoint)
    assert code == 0 and json.loads(output)['work_authority_verified'] is True, error
    destination = host.tmp / 'restored-work'
    code, output, error = host.run('restore', checkpoint, '--profile', 'work', '--to', str(destination))
    assert code == 0, error
    result = json.loads(output)
    assert result['quarantined_files'] > 0 and result['quarantined_bytes'] > 0
    assert (destination / '.artha-restored.json').is_file()
    assert (destination / '.artha-quarantine' / 'catalog.ae1').is_file()
    assert not list(destination.rglob('secret.md'))
    assert not list(destination.rglob('index.ae1'))


def test_work_restore_refuses_a_sync_destination(host, monkeypatch):
    _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    cloud = host.tmp / 'cloud'
    monkeypatch.setenv('OneDriveCommercial', str(cloud))
    destination = cloud / 'restored-work'
    code, _, error = host.run('restore', json.loads(output)['checkpoint'], '--profile', 'work',
                              '--to', str(destination))
    assert code == 20 and 'sync roots' in error
    assert not destination.exists()


def test_work_restore_refuses_the_personal_local_root(host, monkeypatch):
    from pathlib import Path
    _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    for destination in (host.local / 'restored-work', Path.home() / '.artha-local/restored-work'):
        code, _, error = host.run('restore', json.loads(output)['checkpoint'], '--profile', 'work',
                                  '--to', str(destination))
        assert code == 20 and 'personal roots' in error
        assert not destination.exists()


def test_work_quarantine_commands_refuse_a_moved_sync_destination(host, monkeypatch):
    _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    destination = host.tmp / 'restored-work'
    code, _, error = host.run('restore', json.loads(output)['checkpoint'], '--profile', 'work',
                              '--to', str(destination))
    assert code == 0, error
    cloud = host.tmp / 'cloud'
    cloud.mkdir()
    moved = cloud / destination.name
    destination.rename(moved)
    monkeypatch.setenv('OneDriveCommercial', str(cloud))
    code, _, error = host.run('quarantine-list', str(moved), '--profile', 'work')
    assert code == 20 and 'sync roots' in error


def test_work_file_review_is_bound_to_the_exact_current_head(host, monkeypatch):
    (host.repo / 'state/work/briefing.md').write_text('Synthetic historical Work note.')
    protocol = _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    destination = host.tmp / 'restored-work'
    code, _, error = host.run('restore', json.loads(output)['checkpoint'], '--profile', 'work',
                              '--to', str(destination))
    assert code == 0, error
    code, output, error = host.run('quarantine-list', str(destination), '--profile', 'work', '--details')
    assert code == 0, error
    member = next(item for item in json.loads(output)['members']
                  if item['path'] == 'work_state/briefing.md')
    code, output, error = host.run('quarantine-review', str(destination), member['member_id'],
                                   'briefing.md', '--profile', 'work')
    assert code == 0, error
    review = json.loads(output)['review_digest']
    protocol.publish({'work_state/unrelated.md': b'new current Work note'})
    code, _, error = host.run('quarantine-release', str(destination), member['member_id'],
                              'briefing.md', '--profile', 'work', '--review', review, '--attest-clean')
    assert code == 20 and 'authority' in error.lower()
    assert not (destination / 'reviewed-exports' / 'briefing.md.ae1').exists()


def test_work_file_review_releases_only_an_encrypted_export(host, monkeypatch):
    (host.repo / 'state/work/briefing.md').write_text('Synthetic historical Work note.')
    _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    destination = host.tmp / 'restored-work'
    code, _, error = host.run('restore', json.loads(output)['checkpoint'], '--profile', 'work',
                              '--to', str(destination))
    assert code == 0, error
    code, output, error = host.run('quarantine-list', str(destination), '--profile', 'work', '--details')
    assert code == 0, error
    member = next(item for item in json.loads(output)['members']
                  if item['path'] == 'work_state/briefing.md')
    code, output, error = host.run('quarantine-review', str(destination), member['member_id'],
                                   'briefing.md', '--profile', 'work')
    assert code == 0, error
    review = json.loads(output)['review_digest']
    code, output, error = host.run('quarantine-release', str(destination), member['member_id'],
                                   'briefing.md', '--profile', 'work', '--review', review, '--attest-clean')
    assert code == 0, error
    export = destination / 'reviewed-exports' / 'briefing.md.ae1'
    assert export.read_bytes().startswith(b'AE1')
    assert not (destination / 'reviewed-exports' / 'briefing.md').exists()
    assert b'Synthetic historical Work note.' not in export.read_bytes()
    code, _, error = host.run('quarantine-list', str(destination), '--profile', 'work')
    assert code == 0, error


def test_work_cache_members_stay_review_denied_but_work_state_notes_remain_reviewable(host, monkeypatch):
    from pathlib import Path

    cache = host.local / 'work/cache'
    cache.mkdir(parents=True)
    cache_members = {
        'work_local/cache/credential.md': b'benign-looking cache metadata',
        'work_local/cache/summary.txt': b'benign-looking cache summary',
    }
    for name, content in cache_members.items():
        (cache / name.rsplit('/', 1)[1]).write_bytes(content)
    (host.repo / 'state/work/ordinary.md').write_text('Synthetic historical Work note.')
    _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    checkpoint = Path(json.loads(output)['checkpoint'])
    manifest = ck.verify(checkpoint, cli._cipher(host.backend, profile='work'))
    captured = {entry['path'] for entry in manifest['entries']}
    assert set(cache_members) <= captured

    destination = host.tmp / 'restored-work'
    code, _, error = host.run('restore', str(checkpoint), '--profile', 'work', '--to', str(destination))
    assert code == 0, error
    code, output, error = host.run('quarantine-list', str(destination), '--profile', 'work', '--details')
    assert code == 0, error
    members = {entry['path']: entry for entry in json.loads(output)['members']}
    for path in cache_members:
        entry = members[path]
        output_name = 'cache-export' + Path(path).suffix
        code, result, error = host.run(
            'quarantine-review', str(destination), entry['member_id'], output_name, '--profile', 'work',
        )
        assert code == 20 and not result
        code, result, error = host.run(
            'quarantine-release', str(destination), entry['member_id'], output_name,
            '--profile', 'work', '--review', 'SYNTHETIC-REVIEW', '--attest-clean',
        )
        assert code == 20 and not result

    ordinary = members['work_state/ordinary.md']
    code, output, error = host.run(
        'quarantine-review', str(destination), ordinary['member_id'], 'ordinary.md', '--profile', 'work',
    )
    assert code == 0, error
    assert json.loads(output)['review_digest']


@pytest.mark.parametrize('change', ['correction', 'revoke'])
def test_work_history_invalidation_blocks_historical_member_before_review(host, monkeypatch, change):
    (host.repo / 'state/work/briefing.md').write_text('Synthetic historical Work note.')
    protocol = _enroll_synthetic_work(host, monkeypatch)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'))
    assert code == 0, error
    checkpoint = json.loads(output)['checkpoint']
    if change == 'correction':
        protocol.publish({'work_state/briefing.md': b'corrected Work note'})
    else:
        protocol.publish({}, revoke=['work_state/briefing.md'])
    destination = host.tmp / 'restored-work'
    code, _, error = host.run('restore', checkpoint, '--profile', 'work', '--to', str(destination))
    assert code == 0, error
    code, output, error = host.run('quarantine-list', str(destination), '--profile', 'work', '--details')
    assert code == 0, error
    member = next(item for item in json.loads(output)['members']
                  if item['path'] == 'work_state/briefing.md')
    assert member['state'] == 'blocked'
    code, _, error = host.run('quarantine-review', str(destination), member['member_id'],
                              'briefing.md', '--profile', 'work')
    assert code == 20 and 'eligible' in error.lower()


def test_work_checkpoint_cannot_bypass_privacy_gate_by_omitting_profile(host):
    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config/catchup_core.yaml').write_text(
        'sources: {calendar: {connector: google_calendar, account_scope: personal}}\n'
        'subjects: [owner]\nviewer: owner\n')
    # Older checkpoints used the personal envelope for Work. Their authenticated profile still must be checked.
    path = ck.create('work', {'work_state': host.repo / 'state' / 'work'}, host.tmp / 'old',
        cli._cipher(host.backend), {}, PASS, now=datetime(2026, 10, 24, tzinfo=UTC),
        lock_path=host.tmp / 'lock')
    code, _, error = host.run('restore', str(path), '--to', str(host.tmp / 'restore'))
    assert code == 20 and 'profile' in error and not (host.tmp / 'restore').exists()


def test_key_rotation_during_checkpoint_requires_a_new_capture(host, monkeypatch):
    create = ck.create
    def rotate_during_capture(*args, **kwargs):
        check = kwargs.get('before_publish', lambda: None)
        def changed():
            KeyringKeyProvider('artha-ledger', backend=host.backend).rotate()
            check()
        return create(*args, **{**kwargs, 'before_publish': changed})
    monkeypatch.setattr(ck, 'create', rotate_during_capture)
    code, _, error = host.run('create', '--dest', str(host.tmp / 'changed'))
    assert code == 20 and 'registry changed' in error
    assert not list((host.tmp / 'changed').glob('*.ack'))


@pytest.mark.parametrize('location', ['repo', 'onedrive'])
def test_work_primary_destination_cannot_be_synced_or_inside_the_repository(host, monkeypatch, location):
    cloud = host.tmp / 'cloud'
    monkeypatch.setenv('OneDriveCommercial', str(cloud))
    dest = (host.repo if location == 'repo' else cloud) / 'checkpoint'
    code, _, error = host.run('create', '--profile', 'work', '--dest', str(dest))
    assert code == 20 and 'destination' in error and not dest.exists()


def test_work_checkpoint_creation_requires_the_windows_host(host):
    code, _, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'work'),
                              host_platform='darwin')
    assert code == 20 and 'Windows' in error and not (host.tmp / 'work').exists()


def test_recovery_cannot_report_success_for_a_damaged_payload(host):
    from pathlib import Path
    code, out, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    path = Path(json.loads(out)['checkpoint'])
    path.write_bytes(path.read_bytes() + b'damaged trailing bytes')
    code, _, error = host.run('recovery', str(path))
    assert code == 20 and 'terminator' in error


@pytest.mark.parametrize('defect', ['missing-vault', 'missing-identity', 'short-key', 'mixed-profiles', 'wrong-profile'])
def test_inspection_rejects_incomplete_or_cross_profile_recovery(host, defect):
    escrow = cli.collect_escrow(host.backend)
    profile = 'personal'
    if defect == 'missing-vault':
        escrow.pop('age-key/artha')
    elif defect == 'missing-identity':
        escrow.pop('artha-ledger-ids/key.1')
    elif defect == 'short-key':
        escrow['artha-ledger/key.1'] = b'invalid'
    elif defect == 'mixed-profiles':
        escrow['artha-work-checkpoint/key.1'] = b'w' * 32
    else:
        profile = 'work'
    path = ck.create(profile, {'state': host.repo / 'state'}, host.tmp / 'out', cli._cipher(host.backend),
        escrow, PASS, now=datetime(2026, 10, 24, tzinfo=UTC), lock_path=host.tmp / 'lock')
    code, _, error = host.run('recovery', str(path), '--inspection-only')
    assert code == 20 and error.startswith('blocked:')


def test_copy_to_reports_unconfirmed_upload(host):
    code, out, err = host.run("create", "--dest", str(host.tmp / "o"), "--copy-to", str(host.tmp / "od"),
                              "--cloud-synced")
    assert code == 0, err
    assert "unconfirmed" in json.loads(out)["uploaded"]


def test_weak_or_mismatched_passphrase_blocks(host):
    code, _, err = host.run("create", "--dest", str(host.tmp / "o"), passphrase=lambda _c: "short")
    assert code == 20 and "weak" in err


def test_busy_installation_blocks_creation(host):
    lock = host.local / "catchup_core" / "shadow" / "ledger.session.lock"
    with session_lock(lock):
        code, _, err = host.run("create", "--dest", str(host.tmp / "o"))
    assert code == 20 and "not quiesced" in err


@pytest.mark.parametrize('location', ['local', 'external'])
def test_checkpoint_holds_the_installation_fence_in_a_real_competing_process(host, monkeypatch, location):
    from pathlib import Path
    script = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from catchup_core.session_lock import SessionBusy, session_lock
try:
    with session_lock(Path(sys.argv[2])): pass
except SessionBusy:
    sys.exit(42)
'''
    directory = host.local
    if location == 'external':
        directory = host.tmp / 'external'
        directory.mkdir()
        queue = directory / 'actions.db'
        with closing(sqlite3.connect(queue)) as db, db:
            db.execute('CREATE TABLE actions(status TEXT)')
        monkeypatch.setenv('ARTHA_LOCAL_DB', str(queue))
    args = [sys.executable, '-c', script, str(Path(cli.__file__).parents[1]), str(directory / 'installation.lock')]
    create = ck.create
    observed = []
    def capturing(*a, **kw):
        check = kw.get('before_publish', lambda: None)
        def inspect():
            check()
            observed.append(subprocess.run(args, capture_output=True, timeout=15).returncode)
        kw['before_publish'] = inspect
        return create(*a, **kw)
    monkeypatch.setattr(ck, 'create', capturing)
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    assert observed == [42]
    assert subprocess.run(args, capture_output=True).returncode == 0


def test_busy_reasons_cover_executing_and_unreadable_action_state(host):
    assert cli.busy_reasons(host.local) == []
    queue = host.local / "actions.db"
    queue.write_bytes(b"not a database")
    assert cli.busy_reasons(host.local) == ["action state cannot be determined"]
    queue.unlink()
    db = sqlite3.connect(queue)
    db.execute("CREATE TABLE actions(status TEXT)")
    db.execute("INSERT INTO actions VALUES('executing')")
    db.commit()
    db.close()
    assert cli.busy_reasons(host.local) == ["an action is executing"]


def test_escrow_fails_when_a_key_is_unreadable(host):
    with pytest.raises(Exception):
        cli.collect_escrow(FakeKeyring())


def test_recovery_detects_a_changed_key(host):
    code, out, _ = host.run("create", "--dest", str(host.tmp / "o"))
    path = json.loads(out)["checkpoint"]
    service = cli._KEY_SERVICES[0]
    host.backend.store[(service, "key.1")] = "A" * 43 + "="       # different 32-byte key
    code, out, _ = host.run('recovery', path)
    assert code == 20 and json.loads(out)['recoverable'] is False


def test_passphrase_only_inspection_needs_no_host_keys_and_cannot_activate(host):
    code, out, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    path = json.loads(out)['checkpoint']
    host.backend.store.clear()
    code, out, error = host.run('recovery', path, '--inspection-only')
    assert code == 0, error
    result = json.loads(out)
    assert result['verified'] and result['inspection_only'] and result['activation_allowed'] is False
    assert result['privacy_history'] == 'unverified'
    assert not host.backend.store and 'key.1' not in out and PASS not in out


def test_passphrase_prompt_confirms_and_never_uses_arguments(monkeypatch):
    answers = iter(["correct horse battery staple", "correct horse battery staple"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))
    assert cli._passphrase(True) == "correct horse battery staple"
    answers = iter(["correct horse battery staple", "different"])
    with pytest.raises(ck.CheckpointBlocked, match="differ"):
        cli._passphrase(True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "only once")
    assert cli._passphrase(False) == "only once"


def test_restore_without_a_trusted_privacy_history_blocks(host):
    from catchup_core import ledger as lg
    restored = host.tmp / "r" / "local" / "shadow"
    restored.mkdir(parents=True)
    sqlite3.connect(restored / "ledger.db").close()
    with pytest.raises(lg.LedgerLocked, match="no trusted privacy history"):
        cli._privacy_hook(host.backend)(host.tmp / "r")


@pytest.mark.parametrize('copied_state', [False, True])
def test_restore_replays_later_forgets_revocations_and_archived_briefings(tmp_path, monkeypatch, copied_state):
    """The headline case: erase after the checkpoint, restore, and the erased data stays erased."""
    from catchup_core import ledger as lg
    from catchup_core.blobs import EncryptedBlobStore
    from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
    from catchup_core.ids import HmacKey

    key = HmacKey(1, b"i" * 32)
    cipher = EnvelopeCipher(StaticKeyProvider({1: b"7" * 32}))
    sealed = lg.KeyringSealedStore(FakeKeyring(), "shadow", cipher=cipher, history_root=tmp_path / "privacy")
    live = tmp_path / "live" / "shadow"
    live.mkdir(parents=True)
    led = lg.Ledger(live / "ledger.db", id_key=key, cipher=cipher, sealed=sealed).open()
    led.migrate()
    led.seal_privacy()
    blobs = EncryptedBlobStore(live / "blobs", cipher, key)
    ref = blobs.put("m1", "SYNTHETIC-PRIVATE-DOCUMENT")
    sid, _ = led.upsert_signal(lg.SignalInput("mail", "a", "m1", "v1", "t", "h1", ref))
    led.put_fact(lg.FactInput(domain="health", predicate="value", value="SYNTHETIC-SECRET",
                              primary_subject_id="adult", observed_at="t", evidence=((sid, 0, 10),)))
    grant = led.grant_consent("adult", "adult", "health", ["view"], "owner")
    led.conn.execute("INSERT INTO briefings VALUES('cu_x',1,'h','t','published')")
    led.conn.execute("INSERT INTO briefing_signals VALUES('cu_x',?)", (sid,))
    led.conn.commit()
    (live / "briefings").mkdir()
    archive = live / "briefings" / "20261001-cu_x.v1.ae1"
    archive.write_bytes(b"archived briefing")
    copied_bytes = b"Curated copy: SYNTHETIC-PRIVATE-DOCUMENT\n"
    if copied_state:
        curated = tmp_path / 'live/state/curated.md'
        curated.parent.mkdir()
        curated.write_bytes(copied_bytes)
    path = ck.create("personal", {"local": tmp_path / "live"}, tmp_path / "out", cipher, {}, PASS,
                     now=datetime(2026, 10, 23, tzinfo=UTC),
                     lock_path=tmp_path / "lock")
    checkpoint_bytes = path.read_bytes()
    led.revoke_consent(grant)             # privacy actions AFTER the checkpoint
    led.forget("mail", "a", "m1")
    led.close()
    for p in (live / "blobs" / f"{ref}.blob", archive):
        p.unlink(missing_ok=True)         # live forget already purged these; the checkpoint still holds them
    control = tmp_path / "control"          # without replay the erased data would come back
    ck.restore(path, control, cipher, [tmp_path / "live"], replay_privacy=lambda _d: None)
    assert (control / "local" / "shadow" / "blobs" / f"{ref}.blob").exists()
    assert (control / "local" / "shadow" / "briefings" / archive.name).exists()
    dest = tmp_path / "restored"
    if copied_state:
        assert (control / 'local/state/curated.md').read_bytes() == copied_bytes
        monkeypatch.setattr(cli, 'profile_layout', lambda *_args, **_kwargs: ({'local': tmp_path / 'live'}, {}))
        monkeypatch.setattr(cli, '_cipher', lambda *_args, **_kwargs: cipher)
        monkeypatch.setattr(cli, '_REPO', tmp_path / 'owner-review-repo')
        output, error = io.StringIO(), io.StringIO()
        code = cli.run(['restore', str(path), '--to', str(dest)], output, error,
                       backend=FakeKeyring(), local=tmp_path / 'live', sealed_for=lambda _mode: sealed)
        assert code == 20 and not output.getvalue()
        assert 'current owner configuration' in error.getvalue()
        # This legacy fixture has no enrolled current owner; refusal precedes decryption.
        # Complete current-owner quarantine/replay is exercised in test_recovery_quarantine_cli.
        assert 'SYNTHETIC-PRIVATE-DOCUMENT' not in error.getvalue()
        assert not dest.exists()
        assert path.read_bytes() == checkpoint_bytes
        assert curated.read_bytes() == copied_bytes
        return
    report = ck.restore(path, dest, cipher, [tmp_path / "live"],
                        replay_privacy=cli._privacy_hook(None, sealed_for=lambda _mode: sealed))
    store = dest / "local" / "shadow"
    assert report["files"] > 0
    assert not (store / "blobs" / f"{ref}.blob").exists() and not (store / "briefings" / archive.name).exists()
    restored = lg.Ledger(store / "ledger.db", id_key=key, cipher=cipher, sealed=sealed).open()
    assert restored.check_restore()
    assert restored.conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    assert not restored.consent_active("adult", "view", "owner", scope="health")
    assert restored.conn.execute("SELECT status FROM briefings").fetchone()[0] == "superseded"
    restored.close()


@pytest.mark.parametrize('name', ['state/curated.md', 'blobs/untracked.blob', 'nested/store.db',
                                 'privacy/current.ae1', 'state/curated.md.age'])
def test_cli_restore_cannot_publish_unproven_nonledger_files(host, name):
    root = host.tmp / 'source'
    artifact = root / name
    artifact.parent.mkdir(parents=True)
    body = b'SYNTHETIC-RESTRICTED-HISTORICAL-COPY'
    artifact.write_bytes(body)
    path = ck.create('personal', {'repo': root}, host.tmp / 'out', cli._cipher(host.backend), {}, PASS,
                     now=datetime(2026, 10, 25, tzinfo=UTC), lock_path=host.tmp / 'lock')
    original = path.read_bytes()
    dest = host.tmp / 'restored'
    code, output, error = host.run('restore', str(path), '--to', str(dest))
    assert code == 20 and not output
    assert 'current owner configuration' in error
    # A missing current-owner context cannot be bypassed by any historical member class.
    assert body.decode() not in error
    assert not dest.exists()
    assert path.read_bytes() == original
    assert artifact.read_bytes() == body


def test_empty_restore_cannot_claim_same_host_privacy_continuity(tmp_path):
    with pytest.raises(ck.CheckpointBlocked, match='no restored ledger.*trusted privacy history'):
        cli._privacy_hook(None)(tmp_path)



def test_hostile_names_in_privacy_history_are_refused(tmp_path):
    store = tmp_path / "store"
    (store / "blobs").mkdir(parents=True)
    victim = tmp_path / "outside.txt"
    victim.write_text("keep")
    for name in ("../outside.txt", "/etc/passwd", "a\\b", "C:/outside.txt", "C:outside.txt",
                 "//server/share/file", "./outside.txt", "blobs/../outside.txt", "NUL"):
        with pytest.raises(ck.CheckpointError, match="unsafe"):
            cli._remove_artifact(store, name)
    cli._remove_artifact(store, "blobs/evd_missing.blob")        # absent artifacts are not an error
    assert victim.read_text() == "keep"
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE briefings(catchup_id TEXT, status TEXT)")
    for bad in ("../x", "a/b", ""):
        with pytest.raises(ck.CheckpointError, match="archive identity"):
            cli._purge_archives(store, db, [bad])
    db.close()


def test_work_cache_namespace_and_legacy_files_never_enter_personal_capture(host):
    artifacts = host.local / 'work' / 'cache'
    artifacts.mkdir(parents=True)
    (artifacts / 'workiq.json.age').write_bytes(b'synthetic Work ciphertext')
    legacy = host.repo / 'tmp' / '.workiq_cache.json'
    legacy.parent.mkdir()
    legacy.write_text('{"private_work":"synthetic"}')
    (legacy.parent / '.workiq_breaker.json').write_text('{}')
    roots, excluded = cli.profile_layout('personal', host.local)
    found, problems, _ = ck._walk(roots, excluded)
    assert not problems
    names = set(found)
    assert not any('workiq' in path or '/work/' in path for path in names)
    work, _ = cli.profile_layout('work', host.local)
    assert work['work_local'] == host.local / 'work'
    found, problems, _ = ck._walk(work, {})
    assert not problems and 'work_local/cache/workiq.json.age' in found


def test_work_inventory_separates_all_known_artifacts_without_disclosing_names(host, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(host.local / 'actions.db'))
    monkeypatch.delenv('ARTHA_PRIVACY_DIR', raising=False)
    work_files = {
        host.repo / name for name in (
            'state/work/SYNTHETIC-PRIVATE-NAME.md',
            '.tokens/msgraph-work-token.json',
            '.tokens/msgraph-work-mcp-token.json',
            'tmp/.workiq_cache.json',
            'tmp/.workiq_cache.json.tmp',
            'tmp/.workiq_breaker.json',
            'tmp/.workiq_breaker.json.tmp',
            'tmp/.workiq_breaker.lock',
            'tmp/..workiq_cache.json-SYNTHETIC.tmp',
        )
    } | {
        host.local / name for name in (
            'work/cache/workiq.json.age',
            'work/cache/health.json',
            'credential-recovery/work-synthetic.ae1',
        )
    }
    for path in work_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'SYNTHETIC-WORK-PRIVATE-PAYLOAD')
    cache_lock = host.local / 'work/cache/cache.lock'
    cache_lock.write_bytes(b'synthetic lock')

    inventories = {}
    for profile in ('personal', 'work'):
        roots, excluded = cli.profile_layout(profile, host.local)
        found, problems, omitted = ck._walk(roots, excluded)
        assert not problems
        inventories[profile] = set(found.values())
        code, output, error = host.run('inventory', '--profile', profile)
        assert code == 0, error
        assert json.loads(output) == {
            'files': len(found), 'excluded': len(omitted), 'blocking': 0, 'ready': True,
        }
        assert 'SYNTHETIC-PRIVATE-NAME' not in output
        assert 'SYNTHETIC-WORK-PRIVATE-PAYLOAD' not in output

    assert host.repo / 'state/goals.md' in inventories['personal']
    assert work_files.isdisjoint(inventories['personal'])
    assert work_files <= inventories['work']
    assert cache_lock not in inventories['personal'] | inventories['work']


@pytest.mark.skipif(sys.platform != 'win32', reason='requires native Windows named streams')
def test_work_inventory_blocks_named_stream_without_disclosure_or_mutation(host):
    from pathlib import Path
    source = host.repo / 'state/work/SYNTHETIC-PRIVATE-NAME.md'
    source.write_bytes(b'SYNTHETIC-WORK-PRIVATE-PAYLOAD')
    stream = Path(str(source) + ':SYNTHETIC-PRIVATE-STREAM')
    stream.write_bytes(b'SYNTHETIC-STREAM-PRIVATE-PAYLOAD')

    code, output, error = host.run('inventory', '--profile', 'work')
    result = json.loads(output)
    assert code == 20 and result['blocking'] >= 1 and result['ready'] is False
    for private in ('SYNTHETIC-PRIVATE-NAME', 'SYNTHETIC-PRIVATE-STREAM',
                    'SYNTHETIC-WORK-PRIVATE-PAYLOAD', 'SYNTHETIC-STREAM-PRIVATE-PAYLOAD'):
        assert private not in output + error
    assert source.read_bytes() == b'SYNTHETIC-WORK-PRIVATE-PAYLOAD'
    assert stream.read_bytes() == b'SYNTHETIC-STREAM-PRIVATE-PAYLOAD'

    code, output, error = host.run('inventory', '--profile', 'personal')
    assert code == 0, error
    assert json.loads(output)['ready'] is True
    assert stream.read_bytes() == b'SYNTHETIC-STREAM-PRIVATE-PAYLOAD'


def test_personal_action_store_cannot_overlap_local_work_namespace(host, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(host.local / 'work' / 'actions.db'))
    with pytest.raises(ck.CheckpointBlocked, match='Work'):
        cli.profile_layout('personal', host.local)


def test_work_checkpoint_refuses_a_concurrent_cache_writer(host, monkeypatch):
    _enroll_synthetic_work(host, monkeypatch)
    cache = host.local / 'work/cache/cache.lock'
    with session_lock(cache):
        code, _, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'not quiesced' in error
    assert not (host.tmp / 'out').exists()


def test_personal_local_root_cannot_contain_the_repository(host):
    with pytest.raises(ck.CheckpointBlocked, match='repository'):
        cli.profile_layout('personal', host.repo.parent)


def test_work_profile_preserves_known_legacy_cache_and_oauth_files(host):
    legacy = host.repo / 'tmp/.workiq_cache.json'
    legacy.parent.mkdir()
    legacy.write_text('{"synthetic":"private Work record"}')
    token = host.repo / '.tokens/msgraph-work-token.json'
    token.parent.mkdir()
    token.write_text('{"synthetic":"private token"}')
    (host.local / 'work').mkdir(parents=True, exist_ok=True)
    roots, excluded = cli.profile_layout('work', host.local)
    found, problems, _ = ck._walk(roots, excluded)
    assert not problems
    assert legacy in found.values() and token in found.values()


def test_interrupted_legacy_work_cache_temporaries_stay_in_work_profile(host):
    temporary = host.repo / 'tmp/..workiq_cache.json-SYNTHETIC.tmp'
    temporary.parent.mkdir()
    temporary.write_text('{"synthetic":"private identity probe"}')
    roots, excluded = cli.profile_layout('personal', host.local)
    found, problems, _ = ck._walk(roots, excluded)
    assert not problems and temporary not in found.values()
    (host.local / 'work').mkdir(parents=True, exist_ok=True)
    roots, excluded = cli.profile_layout('work', host.local)
    found, problems, _ = ck._walk(roots, excluded)
    assert not problems and temporary in found.values()


@pytest.mark.parametrize('damage', ['valid', 'missing', 'corrupt', 'unauthenticated'])
def test_escrow_replays_canonical_privacy_capsules_before_publication(host, monkeypatch, damage):
    import hashlib
    from catchup_core.privacy_history import ANCHOR_SCHEMA, PrivacyCapsules

    service = 'artha-privacy-shadow'
    privacy = host.local / 'privacy'
    monkeypatch.setenv('ARTHA_PRIVACY_DIR', str(privacy))
    directory = privacy / hashlib.sha256(service.encode()).hexdigest()
    capsules = PrivacyCapsules(directory, service, cli._cipher(host.backend))
    head = capsules.save(
        {'checkpoint_id': 1, 'tombstone_digest': hashlib.sha256(b'').hexdigest(), 'permission_generation': 0},
        {'tombstones': [], 'consent': []},
    )
    assert head['schema'] == ANCHOR_SCHEMA
    capsule = directory / (head['capsule_sha256'] + '.ae1')
    if damage == 'missing':
        capsule.unlink()
    elif damage == 'corrupt':
        capsule.write_bytes(b'synthetic-corrupt-capsule')
    elif damage == 'unauthenticated':
        altered = bytearray(capsule.read_bytes())
        altered[-1] ^= 1
        head['capsule_sha256'] = hashlib.sha256(altered).hexdigest()
        (directory / (head['capsule_sha256'] + '.ae1')).write_bytes(altered)
        capsule.unlink()
    host.backend.set_password(service, 'checkpoint', json.dumps(head))
    host.review()
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    if damage == 'valid':
        assert code == 0, error
    else:
        assert code == 20 and 'privacy' in error
        assert not (host.tmp / 'out').exists()


def test_escrow_and_restore_keys_cover_recovery_credentials_bundles_and_privacy_heads(host):
    from pathlib import Path
    from catchup_core.bundles import enroll_producer
    from catchup_core.crypto import EnvelopeCipher
    from catchup_core.ids import HmacKey
    from catchup_core.ledger import KeyringSealedStore, Ledger
    from lib.credentials import CredentialStore

    ext_bundles = host.tmp / 'external_bundles'
    ext_bundles.mkdir()
    (ext_bundles / 'bundle-win_school-stream-00000001-p001.json').write_text(
        json.dumps({'header': {'producer': 'win_school'}})
    )
    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config' / 'catchup_core.yaml').write_text(
        f'bundles:\n  school_email:\n    dir: "{ext_bundles.as_posix()}"\n    producer: "win_school"\n'
    )
    enroll_producer('win_school', host.backend)

    cipher = cli._cipher(host.backend)
    sealed = KeyringSealedStore(
        host.backend, 'shadow', cipher=cipher, history_root=host.local / 'privacy'
    )
    led_path = host.local / 'catchup_core/shadow/ledger.db'
    led_path.unlink(missing_ok=True)
    led = Ledger(led_path, id_key=HmacKey(1, b'i' * 32), cipher=cipher, sealed=sealed).open()
    led.migrate()
    led.seal_privacy()
    led.close()

    from lib import credential_migration

    tokens_dir = host.repo / '.tokens'
    tokens_dir.mkdir(exist_ok=True)
    (tokens_dir / 'gmail-oauth-token.json').write_text(
        json.dumps({'token': 't', 'refresh_token': 'r', 'client_id': 'cid', 'client_secret': 'csec'})
    )
    mig_plan = credential_migration.plan(tokens_dir)
    credential_migration.apply(tokens_dir, host.local, mig_plan['review_hash'], backend=host.backend)

    roots, _ = cli.profile_layout('personal', host.local)
    assert 'bundle_school_email' in roots

    host.review()
    code, out, err = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, err
    ack_path = Path(json.loads(out)['checkpoint'])
    manifest = ck.verify(ack_path, cli._cipher(host.backend))
    escrow_keys = set(manifest['escrow_items'])
    assert 'artha-credential-recovery-personal/key.1' in escrow_keys
    assert 'artha.credentials.v1/google.gmail-oauth-token' in escrow_keys
    assert 'gmail-client-secret/artha' in escrow_keys
    assert 'artha-bundle-win_school/secret' in escrow_keys
    assert 'artha-privacy-shadow/checkpoint' in escrow_keys

    host.backend.store.clear()
    code, out, err = host.run('recovery', str(ack_path), '--restore-keys')
    assert code == 20 and 'privacy' in err
    assert host.backend.store == {}  # no key/head installation before independent current privacy proof
    code, out, err = host.run('recovery', str(ack_path), '--inspection-only')
    assert code == 0, err
    assert json.loads(out)['activation_allowed'] is False
    assert host.backend.store == {}


@pytest.mark.parametrize('filename', [
    'gmail-token.json', 'gcal-token.json', 'msgraph-personal-token.json',
    'google-client-creds.json', 'msgraph-client-creds.json',
])
def test_all_migrated_source_names_require_their_recovery_key(host, filename):
    tokens_dir = host.repo / '.tokens'
    tokens_dir.mkdir()
    (tokens_dir / filename).write_bytes(b'AE1-synthetic-sealed-file-with-lost-key')
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'credential recovery key' in error
    assert not (host.tmp / 'out').exists()


def test_escrow_refuses_missing_or_uncommitted_os_records_for_on_disk_artifacts(host):
    tokens_dir = host.repo / '.tokens'
    tokens_dir.mkdir(exist_ok=True)
    (tokens_dir / 'gmail-oauth-token.json').write_bytes(b'AE1-sealed-without-recovery-key')
    code, _, err = host.run('create', '--dest', str(host.tmp / 'out1'))
    assert code == 20 and 'credential recovery key' in err
    (tokens_dir / 'gmail-oauth-token.json').unlink()

    host.backend.set_password(
        'artha.credentials.v1',
        'google.gmail-oauth-token.pending',
        json.dumps({'schema': 'artha.credential.v1', 'generation': 'a' * 32, 'chunks': 1, 'digest': 'b' * 64}),
    )
    code, _, err = host.run('create', '--dest', str(host.tmp / 'out2'))
    assert code == 20 and 'incomplete' in err
    host.backend.store.pop(('artha.credentials.v1', 'google.gmail-oauth-token.pending'))

    bundle_dir = host.local / 'catchup_core' / 'bundles'
    bundle_dir.mkdir(parents=True)
    (bundle_dir / 'bundle-missing_prod-stream-00000001-p001.json').write_text(
        json.dumps({'header': {'producer': 'missing_prod'}})
    )
    code, _, err = host.run('create', '--dest', str(host.tmp / 'out3'))
    assert code == 20 and 'bundle signing key' in err
    (bundle_dir / 'bundle-missing_prod-stream-00000001-p001.json').unlink()

    ns_hash = __import__('hashlib').sha256(b'artha-privacy-shadow').hexdigest()
    cap_dir = host.local / 'privacy' / ns_hash
    cap_dir.mkdir(parents=True)
    (cap_dir / 'c1.ae1').write_bytes(b'AE1-capsule')
    code, _, err = host.run('create', '--dest', str(host.tmp / 'out4'))
    assert code == 20 and 'privacy anchor head' in err


def test_quiescence_and_writer_fences_block_competing_writers_and_restored_trees(host, monkeypatch):
    from action_queue import ActionQueue
    import goals_writer

    # Private Work wrappers are intentionally absent from the public isolated checkout; native integration is separate.
    reflect_lock = host.repo / 'state/work/.reflect-lock'
    reflect_lock.write_text('99999')
    assert 'a Work reflection session is active' in cli.busy_reasons(host.local, repo=host.repo)
    reflect_lock.unlink()

    cred_lock = host.local / 'credential-locks/migrate.lock'
    with session_lock(cred_lock):
        assert 'a Catch-up owns credential-locks' in cli.busy_reasons(host.local)

    writer_lock = host.local / 'writer-locks/goals.lock'
    with session_lock(writer_lock):
        assert 'a Catch-up owns writer-locks' in cli.busy_reasons(host.local)

    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(host.local))

    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
os.environ['ARTHA_LOCAL_DIR'] = sys.argv[2]
from catchup_core.session_lock import SessionBusy
from action_queue import ActionQueue
import goals_writer

repo = Path(sys.argv[3])
if goals_writer.update_goal(repo / 'state/goals.md', 'G-001', {'status': 'active'}) != 3:
    sys.exit(1)
for fn in (lambda: ActionQueue(repo),):
    try:
        fn()
    except SessionBusy:
        continue
    sys.exit(1)
sys.exit(0)
"""
    from pathlib import Path
    with session_lock(host.local / 'installation.lock'):
        proc = subprocess.run(
            [sys.executable, '-c', script, str(Path(cli.__file__).parents[1]), str(host.local), str(host.repo)],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, proc.stderr

    marker = host.repo / '.artha-restored.json'
    marker.write_text('{}')
    assert goals_writer.update_goal(host.repo / 'state/goals.md', 'G-001', {'status': 'active'}) == 3
    marker.unlink()

    q = ActionQueue(host.repo)
    local_marker = host.local / '.artha-restored.json'
    local_marker.write_text('{}')
    with pytest.raises(RuntimeError, match='restored installation'):
        q.expire_stale()
    local_marker.unlink()


def test_diff_tables_flag_reports_sqlite_row_reconciliation(host):
    code, out, err = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, err
    ack_path = json.loads(out)['checkpoint']
    with closing(sqlite3.connect(host.local / 'catchup_core/shadow/ledger.db')) as db, db:
        db.execute("INSERT INTO deletion_checkpoint VALUES(2,'t2','d2',1)")
    code, out, err = host.run('diff', ack_path, '--tables')
    assert code == 0, err
    report = json.loads(out)
    assert report['changed'] == 1
    assert report['tables']['local/catchup_core/shadow/ledger.db']['deletion_checkpoint'] == {
        'added': 1, 'removed': 0, 'changed': 0,
    }


@pytest.mark.parametrize('record', [
    'artha-ledger/key.01', 'artha-ledger/key.0',
    'age-key/artha.extra', 'gmail-client-secret/artha.extra',
])
def test_recovery_rejects_noncanonical_or_unclassified_escrow_names(host, record):
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    items[record] = b'x' * 32
    with pytest.raises(ck.CheckpointBlocked):
        cli._recovery_cipher(items, 'personal')


def test_escrow_record_validation_and_bundle_layout_edges(host):
    import hashlib
    base = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    gen = 'a' * 32
    payload = b'{"token":"ok"}'
    digest = hashlib.sha256(payload).hexdigest()
    good_head = json.dumps({'schema': 'artha.credential.v1', 'generation': gen, 'chunks': 1, 'digest': digest}).encode()

    cases = [
        ({**base, 'artha-ledger/key.abc': b'x' * 32}, 'personal', 'malformed key version'),
        ({'age-key/artha': b'AGE-SECRET-KEY-SYNTHETIC', 'artha-work-checkpoint/key.1': b'w' * 32,
          'artha-bundle-p1/secret': b's' * 32}, 'work', 'Work recovery cannot contain'),
        ({**base, 'artha.credentials.v1/work.token': good_head}, 'personal', 'cross-profile'),
        ({**base, 'artha.credentials.v1/google.gmail-oauth-token.pending': good_head}, 'personal', 'pending'),
        ({**base, 'artha.credentials.v1/google.gmail-oauth-token': b'\xff'}, 'personal', 'invalid UTF-8'),
        ({**base, 'artha.credentials.v1/google.gmail-oauth-token': json.dumps(
            {'schema': 'artha.credential.v1', 'generation': gen, 'chunks': 1, 'digest': digest, 'retired': ['old']}
        ).encode()}, 'personal', 'retired'),
        ({**base, 'artha.credentials.v1/google.gmail-oauth-token': good_head}, 'personal', 'missing chunk'),
        ({**base, 'artha.credentials.v1/google.gmail-oauth-token': good_head,
          f'artha.credentials.v1/google.gmail-oauth-token.chunk.{gen}.0': b'\xff'}, 'personal', 'invalid UTF-8'),
        ({**base, 'artha.credentials.v1/google.gmail-oauth-token': good_head,
          f'artha.credentials.v1/google.gmail-oauth-token.chunk.{gen}.0': b'wrong'}, 'personal', 'digest'),
        ({**base, f'artha.credentials.v1/google.gmail-oauth-token.chunk.{gen}.0': payload}, 'personal', 'orphan'),
        ({**base, 'gmail-client-secret/artha': b'\xff'}, 'personal', 'client secret'),
        ({**base, 'gmail-client-secret/artha': b'   '}, 'personal', 'empty or invalid'),
        ({**base, 'artha-bundle-bad/secret': b'short'}, 'personal', 'bundle key'),
        ({**base, 'artha-privacy-shadow/checkpoint': b'not-json'}, 'personal', 'privacy head'),
        ({**base, 'artha-privacy-shadow/checkpoint': b'[]'}, 'personal', 'privacy head'),
        ({**base, 'unknown-service/item': b'1'}, 'personal', 'unexpected record'),
    ]
    for items, profile, expected in cases:
        with pytest.raises(ck.CheckpointBlocked, match=expected):
            cli._recovery_cipher(items, profile)

    for bad_head in ('[]', '{"schema":"artha.credential.v1","retired":"bad"}',
                     '{"schema":"artha.credential.v1","generation":"bad","chunks":1,"digest":"x"}'):
        with pytest.raises(ck.CheckpointBlocked, match='credential head is malformed'):
            cli._parse_cred_head(bad_head)

    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config/catchup_core.yaml').write_text('bundles:\n  b1:\n    dir: "state/work/bundles"\n')
    with pytest.raises(ck.CheckpointBlocked, match='overlap the Work content boundary'):
        cli.profile_layout('personal', host.local)




@pytest.mark.parametrize('configuration', ['[invalid', 'bundles: wrong-type', 'bundles:\n  school:\n    dir: 12\n'])
def test_checkpoint_cannot_ignore_unreadable_bundle_dependency_declarations(host, monkeypatch, configuration):
    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config/catchup_core.yaml').write_text(configuration)
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('invalid dependency read keys'))
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'bundle' in error
    assert not (host.tmp / 'out').exists()


@pytest.mark.parametrize('content', ['not-json', '[]', '{}', '{"producer":"../bad"}', None])
def test_unreadable_or_malformed_bundle_artifact_blocks_dependency_inventory(
    host, monkeypatch, content
):
    artifact = host.repo / 'state' / 'bundle-synthetic-stream-00000001-p001.json'
    artifact.write_text(content or '{}', encoding='utf-8')
    if content is None:
        original_read_text = type(artifact).read_text

        def fail_artifact_read(path, *args, **kwargs):
            if path == artifact:
                raise PermissionError('synthetic inaccessible bundle')
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(type(artifact), 'read_text', fail_artifact_read)

    with pytest.raises(ck.CheckpointBlocked, match='bundle dependency artifact'):
        cli._discover_bundle_producers(host.repo, host.local)


def test_non_regular_bundle_artifact_blocks_dependency_inventory(host):
    artifact = host.repo / 'state' / 'bundle-synthetic-stream-00000001-p001.json'
    artifact.mkdir()

    with pytest.raises(ck.CheckpointBlocked, match='not a regular file'):
        cli._discover_bundle_producers(host.repo, host.local)


@pytest.mark.parametrize('directory_state', ['missing', 'file'])
def test_unavailable_or_non_directory_bundle_scan_path_blocks_inventory(host, directory_state):
    (host.repo / 'config').mkdir(exist_ok=True)
    configured_dir = host.repo / 'state' / 'bundle-source'
    if directory_state == 'file':
        configured_dir.write_text('not a directory', encoding='utf-8')
    (host.repo / 'config/catchup_core.yaml').write_text(
        'bundles:\n  synthetic:\n    dir: state/bundle-source\n',
        encoding='utf-8',
    )

    with pytest.raises(ck.CheckpointBlocked, match='bundle dependency'):
        cli._discover_bundle_producers(host.repo, host.local)


def test_unreadable_nested_bundle_scan_directory_blocks_inventory(host, monkeypatch):
    from pathlib import Path

    nested = host.repo / 'state' / 'nested'
    nested.mkdir(parents=True)
    original_scandir = cli.os.scandir

    def unreadable(path):
        if Path(path) == nested:
            raise PermissionError('synthetic unreadable bundle directory')
        return original_scandir(path)

    monkeypatch.setattr(cli.os, 'scandir', unreadable)
    with pytest.raises(ck.CheckpointBlocked, match='bundle dependency directory is unavailable'):
        cli._discover_bundle_producers(host.repo, host.local)


def test_bundle_artifact_listing_failure_after_walk_blocks_inventory(host, monkeypatch):
    from pathlib import Path

    nested = host.repo / 'state' / 'nested'
    nested.mkdir(parents=True)
    original_scandir = cli.os.scandir
    reads = 0

    def unreadable_second_scan(path):
        nonlocal reads
        if Path(path) == nested:
            reads += 1
            if reads == 2:
                raise PermissionError('synthetic changed directory permissions')
        return original_scandir(path)

    monkeypatch.setattr(cli.os, 'scandir', unreadable_second_scan)
    with pytest.raises(ck.CheckpointBlocked, match='bundle dependency directory is unavailable'):
        cli._discover_bundle_producers(host.repo, host.local)


@pytest.mark.parametrize('scan_root', ['repo', 'local'])
def test_bundle_dependency_scan_root_metadata_denial_blocks_inventory(host, monkeypatch, scan_root):
    root = getattr(host, scan_root)
    original_lstat = type(root).lstat

    def denied(path, *args, **kwargs):
        if path == root:
            raise PermissionError('synthetic scan-root metadata denial')
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(type(root), 'lstat', denied)
    with pytest.raises(ck.CheckpointBlocked, match='bundle dependency directory is unavailable'):
        cli._discover_bundle_producers(host.repo, host.local)


def test_bundle_dependency_discovery_preserves_windows_case_insensitive_matching(host):
    artifact = host.repo / 'state' / 'BUNDLE-synthetic-stream.JSON'
    artifact.write_text(json.dumps({'producer': 'synthetic'}), encoding='utf-8')

    assert 'synthetic' in cli._discover_bundle_producers(host.repo, host.local)


def test_bundle_dependency_cannot_relabel_work_content_as_personal(host):
    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config/catchup_core.yaml').write_text(
        'bundles:\n  school:\n    dir: "' + (host.repo / 'state/work').as_posix() + '"\n')
    with pytest.raises(ck.CheckpointBlocked, match='Work'):
        cli.profile_layout('personal', host.local)


def test_bundle_root_cannot_include_an_existing_required_installation_root(host):
    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config/catchup_core.yaml').write_text(
        'bundles:\n  school:\n    dir: "' + host.tmp.as_posix() + '"\n')
    with pytest.raises(ck.CheckpointBlocked, match='overlap'):
        cli.profile_layout('personal', host.local)


def test_missing_configured_bundle_store_blocks_before_escrow_access(host, monkeypatch):
    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config/catchup_core.yaml').write_text(
        'bundles:\n  school:\n    dir: "' + (host.tmp / 'missing-bundles').as_posix() + '"\n')
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('missing dependency read keys'))
    code, _, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 20 and 'bundle' in error


def test_cutover_verifier_accepts_the_actual_checkpoint_cli_layout(host):
    from pathlib import Path
    from catchup_core.ids import HmacKey
    from catchup_core.ledger import Ledger, KeyringSealedStore
    from catchup_core.policy import verify_preservation_checkpoint
    cipher = cli._cipher(host.backend)
    path = host.local / 'catchup_core/shadow/ledger.db'
    path.unlink()
    sealed = KeyringSealedStore(host.backend, 'shadow', cipher=cipher, history_root=host.local / 'privacy')
    with Ledger(path, id_key=HmacKey(1, b'i' * 32), cipher=cipher, sealed=sealed) as ledger:
        ledger.migrate()
        ledger.seal_privacy()
    (host.repo / 'config').mkdir(exist_ok=True)
    (host.repo / 'config/catchup_core.yaml').write_text('cutover: false\n')
    host.review()
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    checkpoint = Path(json.loads(output)['checkpoint'])
    manifest = verify_preservation_checkpoint(checkpoint.read_bytes(), cipher)
    assert manifest['context']['preservation']['schema'] == 'artha.installation-preservation.v1'


@pytest.mark.parametrize('value', [True, 7, b'SYNTHETIC-PRIVATE-RECORD', {'token': 'synthetic'}])
def test_escrow_boundary_rejects_non_string_os_credentials_without_writes(host, value):
    host.backend.store[('gmail-client-secret', 'artha')] = value
    before = dict(host.backend.store)
    destination = host.tmp / 'blocked'
    code, output, error = host.run('create', '--dest', str(destination))
    assert code == 20 and 'OS credential record gmail-client-secret is malformed' in error
    assert output == '' and 'SYNTHETIC-PRIVATE-RECORD' not in error
    assert not destination.exists() and host.backend.store == before


@pytest.mark.parametrize('field,value', [
    ('schema', 'unknown'), ('retired', {}), ('generation', None), ('generation', 'A' * 32),
    ('chunks', True), ('chunks', 0), ('chunks', 65), ('chunks', '1'),
    ('digest', None), ('digest', 'G' * 64),
])
def test_escrow_boundary_rejects_malformed_live_heads_before_publication(host, field, value):
    head = {'schema': 'artha.credential.v1', 'generation': 'a' * 32, 'chunks': 1, 'digest': 'b' * 64}
    head[field] = value
    host.backend.set_password('artha.credentials.v1', 'google.gmail-oauth-token', json.dumps(head))
    before = dict(host.backend.store)
    destination = host.tmp / 'blocked'
    code, output, error = host.run('create', '--dest', str(destination))
    assert code == 20 and 'credential head is malformed' in error
    assert output == '' and not destination.exists() and host.backend.store == before


@pytest.mark.parametrize('damage', ['missing', 'digest', 'type', 'retired', 'pending'])
def test_escrow_boundary_refuses_incomplete_live_credential_generations(host, damage):
    from lib.credentials import CredentialStore
    name = 'google.gmail-oauth-token'
    service = 'artha.credentials.v1'
    CredentialStore(backend=host.backend, local=host.local).write(name, {'token': 'SYNTHETIC-PRIVATE-TOKEN'})
    head = json.loads(host.backend.get_password(service, name))
    chunk = f'{name}.chunk.{head["generation"]}.0'
    if damage == 'missing':
        host.backend.store.pop((service, chunk))
    elif damage == 'digest':
        host.backend.set_password(service, chunk, '{"token":"SYNTHETIC-TAMPERED-TOKEN"}')
    elif damage == 'type':
        host.backend.store[(service, chunk)] = 12
    elif damage == 'retired':
        head['retired'] = [{'generation': 'b' * 32, 'chunks': 1}]
        host.backend.set_password(service, name, json.dumps(head))
    else:
        host.backend.set_password(service, name + '.pending', json.dumps(head))
    before = dict(host.backend.store)
    destination = host.tmp / 'blocked'
    code, output, error = host.run('create', '--dest', str(destination))
    assert code == 20 and ('incomplete' in error or 'retired' in error)
    assert output == '' and 'SYNTHETIC-PRIVATE-TOKEN' not in error
    assert 'SYNTHETIC-TAMPERED-TOKEN' not in error
    assert host.backend.store == before and not destination.exists()


@pytest.fixture
def migrated_escrow(host):
    from pathlib import Path
    from lib import credential_migration
    tokens = host.repo / '.tokens'
    tokens.mkdir()
    (tokens / 'gmail-oauth-token.json').write_text(json.dumps({
        'token': 'synthetic-token', 'client_id': 'synthetic-client', 'client_secret': 'synthetic-secret',
    }))
    plan = credential_migration.plan(tokens)
    result = credential_migration.apply(tokens, host.local, plan['review_hash'], backend=host.backend)
    host.recovery_backup = Path(result['backup'])
    return host


@pytest.mark.parametrize('damage,expected', [
    ('backup', 'recovery backup cannot be decrypted'),
    ('sealed-token', 'sealed token file cannot be decrypted'),
    ('missing-record', 'migrated credential google.gmail-oauth-token is missing'),
    ('deleted-record', 'migrated credential google.gmail-oauth-token was deleted'),
    ('missing-client', 'migrated client credential gmail-client-secret is missing'),
    ('key-history', 'credential recovery key'),
])
def test_escrow_boundary_rejects_damaged_migration_dependencies(migrated_escrow, damage, expected):
    host = migrated_escrow
    if damage == 'backup':
        host.recovery_backup.write_bytes(b'AE1-synthetic-damaged-backup')
    elif damage == 'sealed-token':
        (host.repo / '.tokens/gmail-oauth-token.json').write_bytes(b'AE1-synthetic-damaged-token')
    elif damage == 'missing-record':
        host.backend.store.pop(('artha.credentials.v1', 'google.gmail-oauth-token'))
    elif damage == 'deleted-record':
        host.backend.set_password('artha.credentials.v1', 'google.gmail-oauth-token', json.dumps({
            'schema': 'artha.credential.v1', 'deleted': True, 'retired': [],
        }))
    elif damage == 'missing-client':
        host.backend.store.pop(('gmail-client-secret', 'artha'))
    else:
        host.backend.set_password('artha-credential-recovery-personal', 'current_version', '2')
    before = dict(host.backend.store)
    backup = host.recovery_backup.read_bytes()
    destination = host.tmp / 'blocked'
    code, output, error = host.run('create', '--dest', str(destination))
    assert code == 20 and expected in error
    assert output == '' and not destination.exists() and host.backend.store == before
    assert host.recovery_backup.read_bytes() == backup


@pytest.mark.parametrize('secret', ['', '  ', 'SYNTHETIC-PRIVATE-CLIENT\n'])
def test_escrow_boundary_blocks_invalid_client_secrets(host, secret):
    host.backend.set_password('gmail-client-secret', 'artha', secret)
    before = dict(host.backend.store)
    code, output, error = host.run('create', '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and 'client credential gmail-client-secret' in error
    assert output == '' and 'SYNTHETIC-PRIVATE-CLIENT' not in error
    assert host.backend.store == before and not (host.tmp / 'blocked').exists()


@pytest.mark.parametrize('profile', ['personal', 'work'])
def test_escrow_boundary_ignores_other_profile_pending_and_live_records(host, profile):
    from lib.credentials import CredentialStore
    store = CredentialStore(backend=host.backend, local=host.local)
    for name in ('google.gmail-oauth-token', 'msgraph.work', 'work.custom'):
        store.write(name, {'token': 'synthetic-' + name})
    other_names = ('msgraph.work', 'work.custom') if profile == 'personal' else ('google.gmail-oauth-token',)
    for name in other_names:
        service = store._service(name)
        host.backend.set_password(service, name + '.pending', 'synthetic-incomplete')
        host.backend.set_password('artha.credentials.v1', name + '.pending', 'synthetic-legacy-incomplete')
    if profile == 'work':
        KeyringKeyProvider('artha-work-checkpoint', backend=host.backend).current_version()
    host.review()
    before = dict(host.backend.store)
    items = cli.collect_escrow(host.backend, profile=profile, repo=host.repo, local=host.local)
    assert not any(name in key for name in other_names for key in items)
    expected = ('google.gmail-oauth-token',) if profile == 'personal' else ('msgraph.work', 'work.custom')
    assert all(any(key.endswith('/' + name) for key in items) for name in expected)
    cli._recovery_cipher(items, profile)
    assert host.backend.store == before


@pytest.mark.parametrize('damage', ['none', 'missing-chunk', 'invalid-chunk-type'])
def test_escrow_boundary_validates_legacy_work_service_chunks(host, damage, monkeypatch):
    from lib.credentials import CredentialStore
    CredentialStore(backend=host.backend, local=host.local).write('msgraph.work', {'token': 'synthetic'})
    for (service, name), value in list(host.backend.store.items()):
        if service == 'artha.credentials.work.v1':
            host.backend.store[('artha.credentials.v1', name)] = value
            del host.backend.store[(service, name)]
    chunk = next(name for service, name in host.backend.store if '.chunk.' in name)
    if damage == 'missing-chunk':
        host.backend.store.pop(('artha.credentials.v1', chunk))
    elif damage == 'invalid-chunk-type':
        host.backend.store[('artha.credentials.v1', chunk)] = False
    KeyringKeyProvider('artha-work-checkpoint', backend=host.backend).current_version()
    host.review()
    _enroll_synthetic_work(host, monkeypatch)
    before = dict(host.backend.store)
    code, output, error = host.run('create', '--profile', 'work', '--dest', str(host.tmp / 'out'))
    if damage == 'none':
        assert code == 0, error
        from pathlib import Path
        items = ck.read_escrow(Path(json.loads(output)['checkpoint']), PASS)
        assert f'artha.credentials.v1/{chunk}' in items
        assert 'artha.credentials.v1/msgraph.work' in items
    else:
        assert code == 20 and ('chunk' in error or 'malformed' in error)
        assert output == '' and not (host.tmp / 'out').exists()
    assert host.backend.store == before


@pytest.mark.parametrize('record', [
    'artha-bundle-school/secret.extra', 'artha-privacy-shadow/checkpoint.extra',
    'artha-credential-recovery-personal/key.01', 'artha-credential-recovery-personal/key.4294967296',
    'artha-credential-recovery-personal/key.2', 'artha-credential-recovery-work/key.1',
    'artha.credentials.work.v1/msgraph.work', 'msgraph-work-client-id/artha',
])
def test_escrow_boundary_rejects_unknown_suffixes_and_cross_profile_recovery(host, record):
    from pathlib import Path
    items = cli.collect_escrow(host.backend, repo=host.repo, local=host.local)
    items[record] = b'x' * 32
    checkpoint = ck.create('personal', {'state': host.repo / 'state'}, host.tmp / 'out',
        cli._cipher(host.backend), items, PASS, now=datetime(2026, 10, 24, tzinfo=UTC),
        lock_path=host.tmp / 'lock')
    before = dict(host.backend.store)
    code, output, error = host.run('recovery', str(checkpoint), '--restore-keys')
    assert code == 20 and error.startswith('blocked:') and output == ''
    assert host.backend.store == before and Path(checkpoint).is_file()


@pytest.mark.parametrize('target', ['config', 'recovery', 'tokens'])
def test_escrow_boundary_fails_closed_on_dependency_metadata_denial(host, monkeypatch, target):
    paths = {'config': host.repo / 'config/catchup_core.yaml',
             'recovery': host.local / 'credential-recovery', 'tokens': host.repo / '.tokens'}
    path = paths[target]
    path.parent.mkdir(parents=True, exist_ok=True)
    if target == 'config':
        path.write_text('bundles: {}\n')
    else:
        path.mkdir()
    deny_metadata(monkeypatch, path)
    before = dict(host.backend.store)
    code, output, error = host.run('create', '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and output == '' and 'SYNTHETIC-PRIVATE-DEPENDENCY' not in error
    assert not (host.tmp / 'blocked').exists() and host.backend.store == before


@pytest.mark.parametrize('configuration', [
    '[]\n', 'bundles:\n  bad/name:\n    dir: state\n', 'bundles:\n  school: []\n',
    'bundles:\n  school:\n    producer: 12\n    dir: state\n',
    'bundles:\n  school:\n    producer: bad/name\n    dir: state\n',
    'bundles:\n  school:\n    dir: "  "\n',
])
def test_escrow_boundary_rejects_invalid_bundle_bindings_before_key_access(host, monkeypatch, configuration):
    config = host.repo / 'config/catchup_core.yaml'
    config.parent.mkdir()
    config.write_text(configuration)
    before = dict(host.backend.store)
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('invalid binding accessed keys'))
    code, output, error = host.run('create', '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and 'bundle' in error and output == ''
    assert not (host.tmp / 'blocked').exists() and host.backend.store == before


@pytest.mark.parametrize('target', ['config', 'work-artifacts', 'bundle'])
def test_escrow_boundary_rejects_wrong_dependency_file_types(host, monkeypatch, target):
    config = host.repo / 'config/catchup_core.yaml'
    if target == 'config':
        config.mkdir(parents=True)
    elif target == 'work-artifacts':
        (host.repo / 'tmp').write_text('synthetic-not-a-directory')
    else:
        config.parent.mkdir()
        bundle = host.tmp / 'bundle-file'
        bundle.write_text('synthetic-not-a-directory')
        config.write_text(f'bundles:\n  school:\n    dir: "{bundle.as_posix()}"\n')
    before = dict(host.backend.store)
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('invalid file type accessed keys'))
    code, output, error = host.run('create', '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and output == ''
    assert 'regular file' in error or 'directory' in error
    assert not (host.tmp / 'blocked').exists() and host.backend.store == before


def test_escrow_boundary_redacts_selective_credential_backend_read_failure(host, monkeypatch):
    read = host.backend.get_password
    def unavailable(service, name):
        if service == 'gmail-client-secret':
            raise RuntimeError('SYNTHETIC-PRIVATE-BACKEND-DETAIL')
        return read(service, name)
    monkeypatch.setattr(host.backend, 'get_password', unavailable)
    before = dict(host.backend.store)
    code, output, error = host.run('create', '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and 'OS credential store failed while reading gmail-client-secret' in error
    assert output == '' and 'SYNTHETIC-PRIVATE-BACKEND-DETAIL' not in error
    assert not (host.tmp / 'blocked').exists() and host.backend.store == before


def test_escrow_boundary_unreadable_reflection_state_blocks_before_key_access(host, monkeypatch):
    lock = host.repo / 'state/work/.reflect-lock'
    lock.write_text('synthetic-owner')
    deny_metadata(monkeypatch, lock)
    monkeypatch.setattr(host.backend, 'get_password', lambda *_: pytest.fail('unknown reflection read keys'))
    code, output, error = host.run('create', '--dest', str(host.tmp / 'blocked'))
    assert code == 20 and 'Work reflection state cannot be determined' in error
    assert output == '' and 'SYNTHETIC-PRIVATE-DEPENDENCY' not in error
    assert not (host.tmp / 'blocked').exists()


def test_escrow_boundary_deleted_optional_record_is_not_resurrected(host):
    from pathlib import Path
    from lib.credentials import CredentialStore
    name = 'google.gmail-oauth-token'
    store = CredentialStore(backend=host.backend, local=host.local)
    store.write(name, {'token': 'SYNTHETIC-DELETED-TOKEN'})
    store.delete(name)
    host.review()
    before = dict(host.backend.store)
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    checkpoint = Path(json.loads(output)['checkpoint'])
    items = ck.read_escrow(checkpoint, PASS)
    head_key = f'artha.credentials.v1/{name}'
    assert json.loads(items[head_key])['deleted'] is True
    assert not any(key.startswith(head_key + '.chunk.') for key in items)
    code, output, error = host.run('recovery', str(checkpoint), '--inspection-only')
    assert code == 0 and json.loads(output)['activation_allowed'] is False, error
    assert 'SYNTHETIC-DELETED-TOKEN' not in output + error
    assert host.backend.store == before


def test_escrow_boundary_recovery_profile_mismatch_never_installs_keys(host):
    from pathlib import Path
    code, output, error = host.run('create', '--dest', str(host.tmp / 'out'))
    assert code == 0, error
    checkpoint = Path(json.loads(output)['checkpoint'])
    host.backend.store.clear()
    with pytest.raises(ck.CheckpointBlocked, match='profile does not match'):
        cli.recover_escrow(checkpoint, PASS, host.backend, expected_profile='work',
                           repo=host.repo, local=host.local)
    assert host.backend.store == {}


def test_decrypted_vault_state_blocks_capture_until_reencrypted(host):
    """Given State Files decrypted beside their ciphertext, when capture starts, then it is not quiesced."""
    marker = host.repo / '.artha-decrypted'
    marker.write_text('{}')
    assert 'State Files are decrypted; re-encrypt before capture' in cli.busy_reasons(host.local, repo=host.repo)
    code, _, err = host.run('create', '--dest', str(host.tmp / 'out_decrypted'))
    assert code == 20 and 'not quiesced' in err and 'decrypted' in err
    marker.unlink()
    assert 'State Files are decrypted; re-encrypt before capture' not in cli.busy_reasons(host.local, repo=host.repo)


def test_spend_seal_is_classified_by_the_registry_and_kept_out_of_snapshots(host):
    """Given an enrolled seal, then capture lists it as excluded; an unnamed seal blocks capture."""
    from pathlib import Path

    from catchup_core import recovery_registry as registry
    seal = host.local / 'spend-seal' / 'authoritative.json'
    seal.parent.mkdir(mode=0o700)
    seal.write_text('{}')
    home_registry = Path.home() / '.artha-local'
    with pytest.raises(ck.CheckpointBlocked, match='does not classify'):
        cli.profile_layout('personal', host.local, registry_root=home_registry)
    with registry.enroll_spend_seal('personal', root=home_registry):
        with pytest.raises(ck.CheckpointBlocked, match='does not classify'):          # still pending
            cli.profile_layout('personal', host.local, registry_root=home_registry)
    roots, excluded = cli.profile_layout('personal', host.local, registry_root=home_registry)
    assert any(key.endswith('spend-seal') and 'never restored' in why for key, why in excluded.items())
    found, problems, _ = ck._walk(roots, excluded)
    assert not problems and seal not in found.values()
    document = registry.snapshot('personal', root=home_registry, require_review=False)
    assert [e['state'] for e in document['entries'] if e['kind'] == 'spend_seal'] == ['active']
    assert cli._registered_entries(document, 'personal') == [
        e for e in document['entries'] if e['kind'] != 'spend_seal']


def test_missing_active_spend_seal_blocks_personal_inventory(host):
    from pathlib import Path

    from catchup_core import recovery_registry as registry
    seal = host.local / 'spend-seal' / 'authoritative.json'
    seal.parent.mkdir(mode=0o700)
    seal.write_text('{}')
    home_registry = Path.home() / '.artha-local'
    with registry.enroll_spend_seal('personal', root=home_registry):
        pass
    seal.unlink()

    with pytest.raises(ck.CheckpointBlocked, match='registered spend seal is missing'):
        cli.profile_layout('personal', host.local, registry_root=home_registry)


def test_spend_seal_lock_files_do_not_block_capture_but_other_artifacts_do(host):
    """Given the empty lock files an authoritative or shadow run leaves, then capture is not blocked by them."""
    from pathlib import Path

    seal_dir = host.local / 'spend-seal'
    seal_dir.mkdir(mode=0o700)
    for name in ('authoritative.json.lock', 'shadow.json.lock'):
        (seal_dir / name).write_text('')
    home_registry = Path.home() / '.artha-local'
    _, excluded = cli.profile_layout('personal', host.local, registry_root=home_registry)
    assert any(key.endswith('spend-seal') and 'never restored' in why for key, why in excluded.items())
    (seal_dir / 'stray.lock').write_text('')
    with pytest.raises(ck.CheckpointBlocked, match='unclassified or unsafe'):
        cli.profile_layout('personal', host.local, registry_root=home_registry)


def test_spend_seal_record_class_is_personal_only_and_bound_to_its_identity(host):
    from catchup_core import recovery_registry as registry
    with pytest.raises(registry.RegistryError):
        registry.declare('work', 'spend_seal', 'artha-spend-seal', 'seal', '1')
    document = {'profile': 'personal', 'entries': [
        {'kind': 'spend_seal', 'service': 'artha-spend-seal', 'account': 'other', 'generation': '1',
         'state': 'active', 'attempt': 'a'}]}
    with pytest.raises(ck.CheckpointBlocked, match='binding'):
        cli._registered_entries(document, 'personal')


def test_key_update_lock_and_fence_use_the_environment_local_directory(tmp_path, monkeypatch):
    """Given ARTHA_LOCAL_DIR, when a key update takes its lock, then the lock and fence are under that directory."""
    from catchup_core import crypto
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "runtime"))
    provider = crypto.KeyringKeyProvider("synthetic-service", backend=object(), create=False)
    with provider._mutation_lock():
        pass
    assert (tmp_path / "runtime" / "installation.lock").exists() and (tmp_path / "runtime" / "key-locks").is_dir()
    assert not (tmp_path / "home" / ".artha-local").exists()


def _enroll_synthetic_work(host, monkeypatch, *, user="S-1-5-21-1001"):
    from catchup_core import work_privacy
    identity = {"platform": "win32", "machine": "00000000-0000-0000-0000-000000000001", "user": user}
    monkeypatch.setattr(work_privacy, "native_windows_identity", lambda: dict(identity))
    cache = host.local / "work" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "index.ae1").write_bytes(b"SYNTHETIC-WORK-CACHE")
    (cache / "cache.lock").write_bytes(b"\0")
    protocol = work_privacy.for_installation(host.repo, host.local, backend=host.backend)
    protocol.enroll_generated(reviewed=True, installation_id="synthetic-installation")
    return protocol


def test_unenrolled_work_capture_stays_held_without_native_discovery(host, monkeypatch):
    from catchup_core import work_privacy
    before = dict(host.backend.store)
    monkeypatch.setattr(work_privacy, "native_windows_identity",
                        lambda: pytest.fail("unenrolled inspection must not discover native identity"))
    destination = host.tmp / "unbound-work"
    code, _, error = host.run("create", "--profile", "work", "--dest", str(destination))
    assert code == 20 and "explicit reviewed Work enrollment" in error
    assert not destination.exists()
    assert not (host.local / "work-privacy").exists()
    assert host.backend.store == before


def test_enrolled_work_checkpoint_binds_exact_protocol_inventory(host, monkeypatch):
    from pathlib import Path
    from catchup_core import work_privacy
    protocol = _enroll_synthetic_work(host, monkeypatch)
    staging = host.local / "work-staging"
    staging.mkdir()
    (staging / "plaintext.tmp").write_bytes(b"SYNTHETIC-PLAINTEXT-STAGING")
    (staging / ".cipher-0123456789abcdef.age").write_bytes(b"SYNTHETIC-CIPHERTEXT-STAGING")
    roots, exclude = cli.profile_layout("work", host.local)
    assert roots["work_local"] == host.local / "work"
    assert staging not in roots.values()
    assert exclude == {"work_local/cache/cache.lock": "ephemeral Work cache process lock"}
    code, output, error = host.run("create", "--profile", "work", "--dest", str(host.tmp / "bound-work"))
    assert code == 0, error
    path = Path(json.loads(output)["checkpoint"])
    manifest = ck.verify(path, cli._cipher(host.backend, profile="work"))
    binding = manifest["context"]["work_binding"]
    assert binding["schema"] == "artha.work-checkpoint-binding.v1"
    assert binding["profile"] == "work" and binding["plaintext_allowed"] is False
    protocol.check_checkpoint(binding)
    members = {entry["path"]: entry for entry in manifest["entries"]
               if entry["path"].startswith(("work_state/", "work_local/"))}
    assert set(members) == set(binding["members"])
    assert "work_local/cache/cache.lock" not in members
    assert all(members[name]["size"] == value["size"]
               for name, value in binding["members"].items())
    escrow = ck.read_escrow(path, PASS)
    assert not any(name.startswith(tuple(work_privacy.WorkPrivacy.EXCLUDED_SERVICES))
                   for name in escrow)
    assert not any("work-privacy" in entry["path"] or "work-staging" in entry["path"]
                   for entry in manifest["entries"])
    assert not any(value in escrow.values() for value in (
        b"SYNTHETIC-PLAINTEXT-STAGING", b"SYNTHETIC-CIPHERTEXT-STAGING"))
    assert any(entry["path"] == "work_artifact_0" or entry["path"].startswith("recovery_registry/")
               for entry in manifest["entries"])


def test_work_capture_rechecks_journaled_bytes_before_publication(host, monkeypatch):
    _enroll_synthetic_work(host, monkeypatch)
    original = ck.create
    member = host.repo / 'state/work/secret.md'

    def mutate_before_publish(*args, **kwargs):
        check = kwargs['before_publish']
        def mutate():
            member.write_text('uncaptured Work drift')
            check()
        return original(*args, **{**kwargs, 'before_publish': mutate})

    monkeypatch.setattr(ck, 'create', mutate_before_publish)
    destination = host.tmp / 'drifted-work'
    code, _, error = host.run('create', '--profile', 'work', '--dest', str(destination))
    assert code == 20 and 'current Work identity' in error
    assert not list(destination.glob('*.ack'))


def test_work_capture_holds_unsupported_sqlite_members(host, monkeypatch):
    database = host.local / 'work' / 'state' / 'history.db'
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE records(value TEXT)')
        db.execute("INSERT INTO records VALUES('synthetic Work prose')")
    _enroll_synthetic_work(host, monkeypatch)
    destination = host.tmp / 'sqlite-work'
    code, _, error = host.run('create', '--profile', 'work', '--dest', str(destination))
    assert code == 20 and 'SQLite member images' in error
    assert not list(destination.glob('*.ack'))


def test_unbound_work_checkpoint_is_inspection_only(host, monkeypatch):
    from catchup_core import work_privacy
    (host.local / 'work').mkdir(parents=True, exist_ok=True)
    roots, exclude = cli.profile_layout('work', host.local)
    escrow = cli.collect_escrow(host.backend, profile='work', repo=host.repo, local=host.local)
    registry = cli._registry_snapshot('work')
    entries = cli._registered_entries(registry, 'work')
    cipher = cli._recovery_cipher(escrow, 'work', entries=entries)
    path = ck.create('work', roots, host.tmp / 'unbound-work', cipher, escrow, PASS,
                     now=datetime.now(UTC), lock_path=host.tmp / 'unbound-work.lock',
                     exclude=exclude, context={'host_os': 'win32', 'recovery_registry': registry})
    monkeypatch.setattr(work_privacy, 'native_windows_identity',
                        lambda: pytest.fail('inspection must not discover native Work identity'))
    code, output, error = host.run('recovery', str(path), '--inspection-only')
    assert code == 0 and json.loads(output)['work_binding'] == 'unbound_inspection_only', error
    destination = host.tmp / 'unbound-restore'
    code, _, error = host.run('restore', str(path), '--profile', 'work', '--to', str(destination))
    assert code == 20 and 'unbound' in error.lower()
    assert not destination.exists()


def test_personal_capture_excludes_work_authority_staging_and_services(host, monkeypatch):
    from pathlib import Path

    from catchup_core import work_privacy
    protocol = _enroll_synthetic_work(host, monkeypatch)
    (protocol.authority_dir / "synthetic-private").write_bytes(b"SYNTHETIC-AUTHORITY")
    staging = host.local / "work-staging"
    staging.mkdir()
    (staging / "plaintext.tmp").write_bytes(b"SYNTHETIC-PLAINTEXT-STAGING")
    (staging / ".cipher-0123456789abcdef.age").write_bytes(b"SYNTHETIC-CIPHERTEXT-STAGING")
    calls = []
    get_password = host.backend.get_password

    def checked_get(service, account):
        if service in work_privacy.WorkPrivacy.EXCLUDED_SERVICES:
            calls.append(service)
        return get_password(service, account)

    monkeypatch.setattr(host.backend, "get_password", checked_get)
    roots, exclude = cli.profile_layout("personal", host.local)
    found, problems, _ = ck._walk(roots, exclude)
    assert not problems
    assert not any("work-privacy" in name or "work-staging" in name for name in found)
    assert any("work-staging" in name for name in exclude)
    items = cli.collect_escrow(host.backend, profile="personal", repo=host.repo, local=host.local)
    assert not calls
    assert not any(name.startswith(tuple(work_privacy.WorkPrivacy.EXCLUDED_SERVICES)) for name in items)
    assert not any(value in items.values() for value in (
        b"SYNTHETIC-PLAINTEXT-STAGING", b"SYNTHETIC-CIPHERTEXT-STAGING"))
    code, output, error = host.run("create", "--dest", str(host.tmp / "personal-with-staging"))
    assert code == 0, error
    manifest = ck.verify(Path(json.loads(output)["checkpoint"]), cli._cipher(host.backend))
    assert not any("work-staging" in entry["path"] for entry in manifest["entries"])
    assert not any(value in ck.read_escrow(Path(json.loads(output)["checkpoint"]), PASS).values()
                   for value in (b"SYNTHETIC-PLAINTEXT-STAGING", b"SYNTHETIC-CIPHERTEXT-STAGING"))


@pytest.mark.parametrize("argv", [
    ("work-privacy-enroll",),
    ("work-privacy-reconcile",),
    ("work-privacy-revoke", "--member", "work_state/secret.md"),
])
def test_work_privacy_mutations_require_review_before_authority_access(host, monkeypatch, argv):
    before = dict(host.backend.store)
    monkeypatch.setattr(
        cli, "_work_privacy",
        lambda *_: pytest.fail("unreviewed Work privacy command accessed the protocol"),
    )
    code, output, error = host.run(*argv)
    assert code == 20 and "review" in error.lower()
    assert not output
    assert host.backend.store == before
    assert not (host.local / "work-privacy").exists()


def test_work_privacy_cli_is_metadata_only_and_revoke_requires_current_member(host, monkeypatch):
    protocol = _enroll_synthetic_work(host, monkeypatch)
    monkeypatch.setattr(cli, "_work_privacy", lambda *_: protocol)
    code, output, error = host.run("work-privacy-status")
    assert code == 0, error
    status = json.loads(output)
    assert status == {"enrolled": True, "current": True, "generation": 1, "member_count": 2}
    assert all(value not in output for value in ("secret.md", "work only", "index.ae1", "identity_key"))

    code, _, error = host.run(
        "work-privacy-revoke", "--member", "work_state/secret.md", "--reviewed",
    )
    assert code == 0, error
    current = protocol.current()
    assert "work_state/secret.md" in current["revoked"]
    assert "secret.md" not in error

    code, _, error = host.run(
        "work-privacy-revoke", "--member", "work_state/not-bound.md", "--reviewed",
    )
    assert code == 20 and "current Work member" in error


def test_work_privacy_enroll_cli_reports_only_metadata(host, monkeypatch):
    from catchup_core import work_privacy

    (host.local / "work/cache").mkdir(parents=True)
    (host.local / "work/cache/index.ae1").write_bytes(b"SYNTHETIC-WORK-CACHE")
    identity = {"platform": "win32", "machine": "00000000-0000-0000-0000-000000000001",
                "user": "S-1-5-21-1001"}
    monkeypatch.setattr(work_privacy, "native_windows_identity", lambda: dict(identity))
    monkeypatch.setattr(
        cli, "_work_privacy",
        lambda repo, local, backend: work_privacy.for_installation(
            repo, local, backend=backend, native_identity=lambda: dict(identity),
        ),
    )
    code, output, error = host.run("work-privacy-enroll", "--reviewed")
    assert code == 0, error
    result = json.loads(output)
    assert result == {"enrolled": True, "current": True, "generation": 1, "member_count": 2}
    assert "secret.md" not in output and "work only" not in output


def test_work_privacy_status_does_not_repair_pending_publication(host, monkeypatch):
    protocol = _enroll_synthetic_work(host, monkeypatch)
    monkeypatch.setattr(cli, "_work_privacy", lambda *_: protocol)
    pending = protocol.authority_dir / "pending"
    pending.write_bytes(b"SYNTHETIC-PENDING")
    before = pending.read_bytes()

    code, output, error = host.run("work-privacy-status")
    assert code == 20 and "current Work privacy status" in error
    assert not output
    assert pending.read_bytes() == before


def test_work_privacy_reconcile_and_recover_pending_are_explicit_cli_operations(host, monkeypatch):
    from pathlib import Path
    from catchup_core import work_privacy

    protocol = _enroll_synthetic_work(host, monkeypatch)
    monkeypatch.setattr(cli, "_work_privacy", lambda *_: protocol)
    member = host.repo / "state/work/secret.md"
    member.write_text("reviewed reconciliation")
    code, output, error = host.run("work-privacy-reconcile", "--reviewed")
    assert code == 0, error
    assert json.loads(output)["generation"] == 2
    assert "secret.md" not in output

    def interrupt(stage):
        if stage == "intent":
            raise work_privacy.WorkPrivacyBlocked("synthetic process interruption")

    protocol.observe = interrupt
    with pytest.raises(work_privacy.WorkPrivacyBlocked):
        protocol.publish({"work_state/another.md": b"synthetic"})
    assert (protocol.authority_dir / "pending").exists()
    protocol.observe = lambda _: None
    code, output, error = host.run("work-privacy-recover-pending")
    assert code == 0, error
    assert json.loads(output)["generation"] == 3
    assert not (protocol.authority_dir / "pending").exists()
    assert "another.md" not in output


def test_work_recovery_translates_authority_failures_but_not_programmer_errors(host, monkeypatch):
    from catchup_core import work_privacy
    from catchup_core.recovery_access import WorkRecoveryAccess

    protocol = _enroll_synthetic_work(host, monkeypatch)
    access = WorkRecoveryAccess(host.repo, protocol, cli._cipher(host.backend, profile="work"))
    access.actor = "S-1-5-21-1001"
    access.pinned = {"identity": "synthetic", "head": {}, "generation": 1}
    protocol.current = lambda: (_ for _ in ()).throw(
        work_privacy.WorkPrivacyBlocked("SYNTHETIC-PRIVATE-CURRENTNESS-DETAIL")
    )
    with pytest.raises(ck.CheckpointBlocked, match="current Work privacy history is unavailable") as refused:
        access._authority()
    assert isinstance(refused.value.__cause__, work_privacy.WorkPrivacyBlocked)
    assert "SYNTHETIC-PRIVATE-CURRENTNESS-DETAIL" not in str(refused.value)

    protocol.native_identity = lambda: (_ for _ in ()).throw(
        work_privacy.WorkPrivacyBlocked("SYNTHETIC-PRIVATE-IDENTITY-DETAIL")
    )
    with pytest.raises(ck.CheckpointBlocked, match="native Work user identity is unavailable") as refused:
        access._resolve_actor()
    assert isinstance(refused.value.__cause__, work_privacy.WorkPrivacyBlocked)
    assert "SYNTHETIC-PRIVATE-IDENTITY-DETAIL" not in str(refused.value)

    protocol.native_identity = lambda: (_ for _ in ()).throw(AssertionError("synthetic programming error"))
    with pytest.raises(AssertionError, match="synthetic programming error"):
        access._resolve_actor()
