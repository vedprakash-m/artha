"""Synthetic Work cache storage, migration and isolation; never OS credentials/providers."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from catchup_core import work_cache as wc
from catchup_core.vault import AgeVault


@pytest.fixture
def store(tmp_path):
    keygen = shutil.which('age-keygen')
    if not keygen:
        for candidate in ('/opt/homebrew/bin/age-keygen', '/usr/local/bin/age-keygen'):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                keygen = candidate
                break
    if not keygen:
        pytest.skip('age-keygen unavailable')
    result = subprocess.run([keygen], capture_output=True, check=True)
    identity = result.stdout.decode()
    recipient = subprocess.run([keygen, '-y'], input=result.stdout, capture_output=True, check=True).stdout.decode().strip()
    root = tmp_path / 'local' / 'work' / 'cache'
    vault = AgeVault(root, identity=lambda: identity, recipient=recipient)
    return wc.WorkCache(root, legacy=tmp_path / 'repo' / 'tmp' / '.workiq_cache.json', vault=vault)


def test_encrypted_round_trip_and_merge_without_source_text_on_disk(store):
    store.update({'first': {'data': ['private narrative α']}})
    store.update({'second': {'data': ['another work source']}})
    assert set(store.load()) == {'first', 'second'}
    assert store.load()['first']['data'] == ['private narrative α']
    for path in store.root.rglob('*'):
        if path.is_file():
            assert b'private narrative' not in path.read_bytes()
    assert store.path.name.endswith('.age')
    if sys.platform == 'win32':
        from catchup_core.quarantine_fs_windows import open_owner_private_path
        with open_owner_private_path(store.path) as opened:
            assert opened.snapshot().size == store.path.stat().st_size
    else:
        assert store.path.stat().st_mode & 0o077 == 0


def test_legacy_content_blocks_without_reusing_or_modifying_it(store):
    store.legacy.parent.mkdir(parents=True)
    original = b'{"private":"source body"}'
    store.legacy.write_bytes(original)
    with pytest.raises(wc.WorkCacheBlocked, match='legacy'):
        store.load()
    with pytest.raises(wc.WorkCacheBlocked, match='legacy'):
        store.update({'entry': {}})
    assert store.legacy.read_bytes() == original
    assert not store.path.exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows DACL publication')
def test_native_publication_refuses_unsafe_existing_ciphertext_without_migration(store):
    store.root.mkdir(parents=True)
    original = b'synthetic historical ciphertext'
    store.path.write_bytes(original)
    with pytest.raises(wc.WorkCacheBlocked, match='owner-private'):
        store._publish(store.path, '{}')
    assert store.path.read_bytes() == original
    assert not list(store.root.glob('.cipher-*'))


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows publication failure')
def test_native_publication_failure_preserves_cache_and_removes_cipher_stage(store, monkeypatch):
    from catchup_core import blobs
    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError
    store.update({'first': {}})
    original = store.path.read_bytes()
    publish = blobs.private_atomic_bytes

    def fail_target(path, data):
        if path == store.path:
            raise QuarantineFilesystemError('synthetic native failure')
        publish(path, data)

    monkeypatch.setattr(blobs, 'private_atomic_bytes', fail_target)
    with pytest.raises(wc.WorkCacheBlocked, match='owner-private'):
        store._publish(store.path, '{}')
    assert store.path.read_bytes() == original
    assert not list(store.root.glob('.cipher-*'))


def test_migration_preserves_verified_ciphertext_then_retires_legacy(store):
    store.legacy.parent.mkdir(parents=True)
    original = b'{"workiq_people_sensitive_name":{"data":["sensitive narrative"]}}'
    store.legacy.write_bytes(original)
    plan = store.migration_plan()
    assert plan == {'bytes': len(original), 'sha256': hashlib.sha256(original).hexdigest()}
    assert 'sensitive' not in json.dumps(plan)
    with pytest.raises(wc.WorkCacheBlocked, match='stopped'):
        store.migrate(plan['sha256'], writers_stopped=False)
    archive = store.migrate(plan['sha256'], writers_stopped=True)
    assert store.vault.read_artifact(archive).encode() == original
    assert b'sensitive' not in store.legacy.read_bytes()
    assert store.legacy.stat().st_mode & 0o222 == 0
    assert store.load() == {}  # migration never declares old source content current
    assert store.migrate(plan['sha256'], writers_stopped=True) == archive
    store.update({'fresh': {'data': []}})
    assert 'fresh' in store.load()


def test_stale_migration_digest_keeps_plaintext_and_cache_unchanged(store):
    store.legacy.parent.mkdir(parents=True)
    store.legacy.write_bytes(b'old')
    plan = store.migration_plan()
    store.legacy.write_bytes(b'new')
    with pytest.raises(wc.WorkCacheBlocked, match='changed'):
        store.migrate(plan['sha256'], writers_stopped=True)
    assert store.legacy.read_bytes() == b'new'
    assert not store.path.exists()


def test_key_failure_never_retires_legacy_or_persists_plaintext(store, monkeypatch):
    store.legacy.parent.mkdir(parents=True)
    store.legacy.write_bytes(b'synthetic private payload')
    plan = store.migration_plan()
    monkeypatch.setattr(store.vault, '_identity', lambda: '')
    with pytest.raises(wc.WorkCacheBlocked):
        store.migrate(plan['sha256'], writers_stopped=True)
    assert store.legacy.read_bytes() == b'synthetic private payload'
    assert not list(store.root.glob('*.age'))


def test_corrupt_ciphertext_blocks_instead_of_becoming_empty_cache(store):
    store.update({'valid': {}})
    store.path.write_bytes(b'invalid age ciphertext')
    with pytest.raises(wc.WorkCacheBlocked):
        store.load()


def test_source_and_cache_symlinks_refused(store, tmp_path):
    target = tmp_path / 'target'
    target.write_text('private')
    store.legacy.parent.mkdir(parents=True)
    store.legacy.symlink_to(target)
    with pytest.raises(wc.WorkCacheBlocked):
        store.migration_plan()
    store.legacy.unlink()
    store.root.mkdir(parents=True, exist_ok=True)
    store.path.symlink_to(target)
    with pytest.raises(wc.WorkCacheBlocked):
        store.load()
    assert target.read_text() == 'private'


def test_bounds_and_schema_enforced(store, monkeypatch):
    monkeypatch.setattr(wc, 'MAX_CACHE_BYTES', 128)
    with pytest.raises(wc.WorkCacheBlocked, match='bound'):
        store.update({'large': {'data': ['x' * 256]}})
    assert not store.path.exists()
    store.root.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(b'x' * 256)
    with pytest.raises(wc.WorkCacheBlocked, match='bound'):
        store.load()


def test_restored_installation_blocks_before_key_access(store, monkeypatch):
    store.root.parent.mkdir(parents=True)
    (store.root.parent / '.artha-restored.json').write_text('{}')
    monkeypatch.setattr(store.vault, '_identity', lambda: pytest.fail('must not read key'))
    with pytest.raises(wc.WorkCacheBlocked, match='quarantined'):
        store.update({'x': {}})


def test_process_lock_blocks_competing_cache_update(store):
    store.root.mkdir(parents=True)
    from catchup_core.session_lock import session_lock
    with session_lock(store.root / 'cache.lock'):
        script = '''from pathlib import Path
from catchup_core.work_cache import WorkCache, WorkCacheBlocked
import sys
try:
    WorkCache(Path(sys.argv[1]), legacy=Path(sys.argv[2])).update({'x':{}})
except WorkCacheBlocked:
    sys.exit(23)
sys.exit(0)
'''
        result = subprocess.run([sys.executable, '-c', script, str(store.root), str(store.legacy)],
                                env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')},
                                capture_output=True, timeout=10)
    assert result.returncode == 23, result.stderr.decode()


def test_local_paths_are_pure_and_reject_repo_and_known_sync_roots(tmp_path, monkeypatch):
    from lib.local_paths import work_cache_dir
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'new-local'))
    assert work_cache_dir() == tmp_path / 'new-local/work/cache'
    assert not (tmp_path / 'new-local').exists()
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(Path(wc.__file__).resolve().parents[2] / 'tmp/local'))
    with pytest.raises(ValueError):
        work_cache_dir()
    monkeypatch.setenv('OneDrive', str(tmp_path / 'sync'))
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'sync/local'))
    with pytest.raises(ValueError):
        work_cache_dir()


def test_process_death_after_archive_keeps_legacy_and_retry_recovers(store):
    # POSIX: SIGKILL. Windows: os.kill(pid, 9) calls TerminateProcess (no cleanup, exit code 9).
    store.legacy.parent.mkdir(parents=True)
    original = b'{"synthetic":"private Work body"}'
    store.legacy.write_bytes(original)
    plan = store.migration_plan()
    script = '''import json, os, signal, sys
from pathlib import Path
from catchup_core import work_cache as wc
from catchup_core.vault import AgeVault
params=json.loads(sys.stdin.read())
root=Path(params['root'])
vault=AgeVault(root, identity=lambda: params['identity'], recipient=params['recipient'])
cache=wc.WorkCache(root, legacy=Path(params['legacy']), vault=vault)
wc.atomic_bytes=lambda *args: os.kill(os.getpid(), getattr(signal, 'SIGKILL', 9))
cache.migrate(params['digest'], writers_stopped=True)
'''
    parameters = {'root': str(store.root), 'legacy': str(store.legacy), 'digest': plan['sha256'],
                  'identity': store.vault._identity(), 'recipient': store.vault._recipient}
    result = subprocess.run([sys.executable, '-c', script], input=json.dumps(parameters).encode(),
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')},
                            capture_output=True, timeout=10)
    assert result.returncode == (9 if os.name == 'nt' else -9), result.stderr.decode()
    assert store.legacy.read_bytes() == original
    archive = store.root / f"legacy-{plan['sha256']}.json.age"
    assert store.vault.read_artifact(archive).encode() == original
    assert store.migrate(plan['sha256'], writers_stopped=True) == archive


def test_independent_process_updates_merge_without_lost_entry(store):
    store.update({'initial': {'data': []}})
    script = '''import json, sys, time
from pathlib import Path
from catchup_core.work_cache import WorkCache, WorkCacheBlocked
from catchup_core.vault import AgeVault
p=json.loads(sys.stdin.readline())
vault=AgeVault(Path(p['root']), identity=lambda: p['identity'], recipient=p['recipient'])
cache=WorkCache(Path(p['root']), legacy=Path(p['legacy']), vault=vault)
for attempt in range(100):
    try:
        cache.update({p['key']: {'data': []}})
        sys.exit(0)
    except WorkCacheBlocked:
        time.sleep(.01)
sys.exit(24)
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')}
    children = []
    for key in ['first', 'second']:
        child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env)
        parameters = {'root': str(store.root), 'legacy': str(store.legacy), 'identity': store.vault._identity(),
                      'recipient': store.vault._recipient, 'key': key}
        child.stdin.write((json.dumps(parameters) + '\n').encode())
        child.stdin.flush()
        children.append(child)
    for child in children:
        _, errors = child.communicate(timeout=10)
        assert child.returncode == 0, errors.decode()
    assert set(store.load()) == {'initial', 'first', 'second'}


def test_default_location_and_lazy_vault_use_the_work_namespace(store, monkeypatch):
    from lib import local_paths
    monkeypatch.setattr(local_paths, 'work_cache_dir', lambda: store.root)
    monkeypatch.setattr(wc, '_vault', lambda root: store.vault)
    default = wc.WorkCache(legacy=store.legacy)
    default.update({'new': {}})
    assert default.load() == {'new': {}}
    monkeypatch.setattr(local_paths, 'work_cache_dir', lambda: (_ for _ in ()).throw(ValueError('private path')))
    with pytest.raises(wc.WorkCacheBlocked, match='machine-local'):
        wc.WorkCache()


def test_os_identity_and_recipient_selection_never_use_environment_secrets(store, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('ARTHA_AGE_KEY', 'AGE-SECRET-KEY-ENVIRONMENT')
    monkeypatch.setattr(wc, 'system_keyring', lambda: SimpleNamespace(get_password=lambda service, account:
        'AGE-SECRET-KEY-SYNTHETIC' if (service, account) == ('age-key', 'artha') else None))
    assert wc._identity() == 'AGE-SECRET-KEY-SYNTHETIC'
    monkeypatch.setattr(wc, 'system_keyring', lambda: SimpleNamespace(get_password=lambda *_: None))
    with pytest.raises(wc.WorkCacheBlocked, match='OS age identity'):
        wc._identity()
    monkeypatch.setattr(wc, 'system_keyring', lambda: (_ for _ in ()).throw(RuntimeError('secret backend text')))
    with pytest.raises(wc.WorkCacheBlocked) as error:
        wc._identity()
    assert 'secret backend' not in str(error.value)
    monkeypatch.setitem(sys.modules, 'profile_loader', SimpleNamespace(get=lambda *_: store.vault._recipient))
    assert isinstance(wc._vault(store.root), AgeVault)
    monkeypatch.setitem(sys.modules, 'profile_loader', SimpleNamespace(get=lambda *_: 'invalid'))
    with pytest.raises(wc.WorkCacheBlocked, match='recipient'):
        wc._vault(store.root)


def test_invalid_schema_and_false_encryption_verification_preserve_existing_cache(store, monkeypatch):
    store.update({'first': {}})
    before = store.path.read_bytes()
    monkeypatch.setattr(store.vault, 'read_artifact', lambda _: 'wrong plaintext')
    with pytest.raises(wc.WorkCacheBlocked):
        store._publish(store.path, '{}')
    assert store.path.read_bytes() == before
    monkeypatch.undo()
    with store._locked():
        store._publish(store.path, '{"schema":"wrong","entries":{}}')
    with pytest.raises(wc.WorkCacheBlocked, match='schema'):
        store.load()


def test_oversized_ciphertext_refused_before_publication(store, monkeypatch):
    monkeypatch.setattr(wc, 'MAX_CACHE_BYTES', 65540)
    monkeypatch.setattr(store.vault, 'encrypt_artifact', lambda _: b'x' * 65541)
    with pytest.raises(wc.WorkCacheBlocked, match='bound'):
        store._publish(store.path, '{}')
    assert not store.path.exists()


def test_growth_after_metadata_check_still_respects_read_bound(store, monkeypatch):
    store.root.mkdir(parents=True)
    store.path.write_bytes(b'x' * 256)
    original = Path.lstat
    from types import SimpleNamespace
    monkeypatch.setattr(wc, 'MAX_CACHE_BYTES', 128)
    monkeypatch.setattr(Path, 'lstat', lambda path, *a, **kw:
                        SimpleNamespace(st_size=0, st_mode=0o100600) if path == store.path else original(path, *a, **kw))
    with pytest.raises(wc.WorkCacheBlocked, match='bound'):
        wc._bounded(store.path)


def test_directory_and_lock_links_block_before_any_key_read(store, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    store.root.parent.mkdir(parents=True)
    store.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(wc.WorkCacheBlocked, match='directory links'):
        store.load()
    store.root.unlink()
    store.root.mkdir()
    (store.root / 'cache.lock').symlink_to(outside / 'lock')
    with pytest.raises(wc.WorkCacheBlocked, match='lock'):
        store.load()


def test_missing_legacy_or_retired_archive_refuses_migration(store):
    with pytest.raises(wc.WorkCacheBlocked, match='no legacy'):
        store.migration_plan()
    with pytest.raises(wc.WorkCacheBlocked, match='missing'):
        store.migrate('0' * 64, writers_stopped=True)
    store.legacy.parent.mkdir(parents=True)
    store.legacy.write_bytes(b'old')
    digest = store.migration_plan()['sha256']
    archive = store.migrate(digest, writers_stopped=True)
    with pytest.raises(wc.WorkCacheBlocked, match='already retired'):
        store.migration_plan()
    archive.unlink()
    with pytest.raises(wc.WorkCacheBlocked, match='archive is missing'):
        store.load()


def test_archive_collision_or_mid_migration_source_change_never_retires_source(store, monkeypatch):
    store.legacy.parent.mkdir(parents=True)
    store.legacy.write_bytes(b'original source')
    digest = store.migration_plan()['sha256']
    archive = store.root / f'legacy-{digest}.json.age'
    store._publish(archive, 'conflicting content')
    with pytest.raises(wc.WorkCacheBlocked, match='does not match'):
        store.migrate(digest, writers_stopped=True)
    assert store.legacy.read_bytes() == b'original source'
    archive.unlink()
    publish = store._publish
    def changed(target, text):
        publish(target, text)
        store.legacy.write_bytes(b'new source')
    monkeypatch.setattr(store, '_publish', changed)
    with pytest.raises(wc.WorkCacheBlocked, match='during migration'):
        store.migrate(digest, writers_stopped=True)
    assert store.legacy.read_bytes() == b'new source'
    assert store.vault.read_artifact(archive) == 'original source'


def test_cli_reports_only_plan_metadata_and_bounded_outcomes(store, monkeypatch, capsys):
    store.legacy.parent.mkdir(parents=True)
    store.legacy.write_bytes(b'private synthetic source')
    monkeypatch.setattr(wc, 'WorkCache', lambda: store)
    assert wc.main(['plan']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert set(plan) == {'bytes', 'sha256'}
    assert wc.main(['apply', '--sha256', plan['sha256']]) == 20
    assert 'private synthetic' not in capsys.readouterr().err
    assert wc.main(['apply', '--sha256', plan['sha256'], '--writers-stopped']) == 0
    assert 'archived and retired' in capsys.readouterr().out


def test_retirement_retry_detects_archive_corruption(store):
    store.legacy.parent.mkdir(parents=True)
    store.legacy.write_bytes(b'original')
    digest = store.migration_plan()['sha256']
    archive = store.migrate(digest, writers_stopped=True)
    store._publish(archive, 'different')
    with pytest.raises(wc.WorkCacheBlocked, match='does not match'):
        store.migrate(digest, writers_stopped=True)


def test_legacy_directory_link_cannot_retire_a_file_outside_the_reviewed_tree(store, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = outside / store.legacy.name
    original.write_bytes(b'outside source must remain untouched')
    store.legacy.parent.parent.mkdir(parents=True)
    store.legacy.parent.symlink_to(outside, target_is_directory=True)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    with pytest.raises(wc.WorkCacheBlocked, match='directory links'):
        store.migrate(digest, writers_stopped=True)
    assert original.read_bytes() == b'outside source must remain untouched'


def test_windows_reparse_directory_is_refused_before_cache_or_key_access(store, monkeypatch):
    from types import SimpleNamespace
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        if path == store.legacy.parent:
            return SimpleNamespace(st_mode=0o040700, st_file_attributes=0x400, st_reparse_tag=0xA0000003)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', reparse)
    monkeypatch.setattr(store.vault, '_identity', lambda: pytest.fail('reparse point accessed a key'))
    with pytest.raises(wc.WorkCacheBlocked, match='reparse'):
        store.update({'x': {}})


def test_windows_cloud_filter_reparse_on_legacy_checkout_succeeds_while_root_reparse_blocks(store, monkeypatch):
    original = Path.lstat
    store.legacy.parent.mkdir(parents=True)
    store.legacy.write_bytes(b'{"cloud_work":"synthetic"}')

    class CloudStat:
        def __init__(self, st):
            self.st_mode = st.st_mode
            self.st_size = st.st_size
            self.st_file_attributes = 0x400
            self.st_reparse_tag = 0x9000001A  # IO_REPARSE_TAG_CLOUD

    def cloud_lstat(path, *args, **kwargs):
        st = original(path, *args, **kwargs)
        if path in (store.legacy.parent, store.legacy):
            return CloudStat(st)
        return st

    monkeypatch.setattr(Path, 'lstat', cloud_lstat)
    plan = store.migration_plan()
    store.migrate(plan['sha256'], writers_stopped=True)
    store.update({'item': {'data': ['ok']}})
    assert store.load() == {'item': {'data': ['ok']}}

    def root_cloud_lstat(path, *args, **kwargs):
        st = original(path, *args, **kwargs)
        if path == store.root:
            return CloudStat(st)
        return st

    monkeypatch.setattr(Path, 'lstat', root_cloud_lstat)
    with pytest.raises(wc.WorkCacheBlocked, match='reparse'):
        store.load()


class _SyntheticVault:
    """Reversible stand-in for age; enrolled tests assert journal routing, not cryptography."""
    def encrypt_artifact(self, text):
        return b'SYN1' + text.encode()[::-1]

    def read_artifact(self, path):
        raw = path.read_bytes()
        assert raw.startswith(b'SYN1')
        return raw[4:][::-1].decode()


class _Backend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


@pytest.fixture
def journaled(tmp_path, monkeypatch):
    from catchup_core.work_privacy import for_installation
    from lib import work_publication as wp
    local, repo = tmp_path / 'local', tmp_path / 'repo'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    (repo / 'state' / 'work').mkdir(parents=True)
    (local / 'work').mkdir(parents=True)
    backend = _Backend()
    identity = {'platform': 'win32', 'machine': 'synthetic-machine', 'user': 'synthetic-user'}
    make = lambda: for_installation(repo, local, backend=backend, native_identity=lambda: dict(identity))
    previous = wp.set_protocol_factory(make)
    make().enroll(b'k' * 32, reviewed=True, installation_id='synthetic-installation')
    cache = wc.WorkCache(local / 'work' / 'cache', legacy=repo / 'tmp' / '.workiq_cache.json', vault=_SyntheticVault())
    yield {'cache': cache, 'make': make, 'local': local}
    wp.set_protocol_factory(previous)


def test_enrolled_update_publishes_through_work_journal(journaled):
    cache, make = journaled['cache'], journaled['make']
    cache.update({'first': {'data': [1]}})
    cache.update({'second': {'data': [2]}})
    assert set(cache.load()) == {'first', 'second'}
    current = make().current()
    assert current['generation'] == 3
    assert 'work_local/cache/workiq.json.age' in current['members']
    assert 'work_local/cache/cache.lock' not in current['members']
    assert sorted(p.name for p in cache.root.iterdir()) == ['cache.lock', 'workiq.json.age']
    staging = journaled['local'] / 'work-staging'
    assert not staging.exists() or not any(staging.iterdir())


def test_enrolled_update_refuses_manual_cache_drift(journaled):
    cache, make = journaled['cache'], journaled['make']
    cache.update({'first': {}})
    manual = _SyntheticVault().encrypt_artifact('{"schema": "x", "entries": {}}')
    cache.path.write_bytes(manual)
    with pytest.raises(wc.WorkCacheBlocked, match='journal'):
        cache.update({'second': {}})
    assert cache.path.read_bytes() == manual
    make().reconcile(reviewed=True)
    assert make().current()['generation'] == 3


def test_enrolled_verification_failure_publishes_nothing(journaled):
    cache, make = journaled['cache'], journaled['make']
    cache.vault.read_artifact = lambda path: 'different'
    with pytest.raises(wc.WorkCacheBlocked, match='verification'):
        cache.update({'first': {}})
    assert not cache.path.exists()
    assert make().current()['generation'] == 1


def test_enrolled_migration_journals_archive_before_retirement_marker(journaled):
    cache, make = journaled['cache'], journaled['make']
    cache.legacy.parent.mkdir(parents=True)
    data = b'{"legacy":"synthetic"}'
    cache.legacy.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    archive = cache.migrate(digest, writers_stopped=True)
    assert f'work_local/cache/legacy-{digest}.json.age' in make().current()['members']
    assert json.loads(cache.legacy.read_bytes())['sha256'] == digest
    assert cache.migrate(digest, writers_stopped=True) == archive
    assert make().current()['generation'] == 2


def test_enrolled_cache_outside_work_local_root_is_refused(journaled, tmp_path):
    other = wc.WorkCache(tmp_path / 'elsewhere', legacy=tmp_path / 'repo' / 'tmp' / '.workiq_cache.json',
                         vault=_SyntheticVault())
    with pytest.raises(wc.WorkCacheBlocked, match='journal'):
        other.update({'first': {}})
    assert not other.path.exists()
