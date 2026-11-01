from __future__ import annotations

import copy
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider, StaticKeyProvider
from catchup_core import recovery_quarantine as quarantine
from catchup_core import quarantine_fs_windows as windows_fs
from catchup_core.quarantine_fs_windows import QuarantineFilesystemError
from catchup_core.recovery_quarantine import QuarantineError, QuarantineStore


@pytest.mark.parametrize('path', [
    '', b'C:\\synthetic', 'relative', 'C:relative', '\\\\server\\share\\file',
    'C:\\one\\..\\two', 'C:\\one\\.\\two', 'C:\\trailing.',
])
def test_native_absolute_path_parser_refuses_ambiguous_or_remote_paths(path):
    with pytest.raises(QuarantineFilesystemError):
        windows_fs._absolute_components(path)


def trusted():
    return {'actor': 'owner', 'owner': 'owner', 'profile': 'personal', 'privacy': {
        'personal': {'checkpoint_id': 9, 'permission_generation': 3, 'tombstone_digest': 'a' * 64}}}


@pytest.fixture
def setup(tmp_path):
    context = trusted()
    cipher = EnvelopeCipher(StaticKeyProvider({1: b'q' * 32}))
    store = QuarantineStore(tmp_path / 'opaque', cipher, authority=lambda: copy.deepcopy(context))
    return store, context


def _ciphertext_files(root):
    return [path for path in root.rglob('*') if not (os.name == 'nt' and path.name == '.lock')]


def _assert_private(path, directory=False):
    if os.name == 'nt':
        from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory
        if directory:
            with WindowsQuarantineDirectory.open(path):
                pass
        else:
            with WindowsQuarantineDirectory.open(path.parent) as parent, parent.open_file(path.name):
                pass
    else:
        assert path.stat().st_mode & 0o777 == (0o700 if directory else 0o600)


def _share(path, mode):
    if os.name == 'nt':
        subprocess.run(['icacls', str(path), '/grant', '*S-1-1-0:(R)'], check=True, capture_output=True)
    else:
        path.chmod(mode)


def _unshare(path, mode):
    if os.name == 'nt':
        subprocess.run(['icacls', str(path), '/remove:g', '*S-1-1-0'], check=True, capture_output=True)
    else:
        path.chmod(mode)


def entry_for(data, path='state/private-name.md'):
    return {'path': path, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'kind': 'file', 'mode': 0o600}


def capture(store, data, path='state/private-name.md'):
    entry = entry_for(data, path)
    store.begin({'profile': 'personal', 'generation': 'ck_synthetic', 'entries': [entry]}, 'b' * 64)
    store.start(entry)
    for offset in range(0, len(data), 777777):
        store.write(data[offset:offset + 777777])
    store.finish()
    return store.seal()


@pytest.mark.parametrize('data', [b'', b'FORBIDDEN_PRIVATE_MARKER' * 100000])
def test_ciphertext_only_roundtrip(setup, data):
    store, _ = setup
    catalog = capture(store, data)
    assert catalog['schema'] == 'artha.recovery-quarantine.v1'
    member = store.inventory()[0]
    assert b''.join(store.iter_member(member['member_id'])) == data
    assert store.verify()['members'] == 1
    for path in _ciphertext_files(store.root):
        assert path.read_bytes().startswith(b'AE1')
        assert b'FORBIDDEN_PRIVATE_MARKER' not in path.read_bytes()
        assert b'private-name' not in path.read_bytes()
        assert 'private-name' not in path.name
        _assert_private(path)
    _assert_private(store.root, directory=True)


def test_corrupt_late_chunk_yields_nothing(setup):
    store, _ = setup
    catalog = capture(store, b'x' * 2200000)
    chunk = catalog['entries'][0]['chunks'][-1]
    path = store.root / chunk['name']
    path.write_bytes(path.read_bytes()[:-1])
    iterator = store.iter_member(catalog['entries'][0]['member_id'])
    with pytest.raises(QuarantineError):
        next(iterator)


def test_current_context_can_advance_but_block_is_permanent(setup):
    store, context = setup
    catalog = capture(store, b'private')
    member_id = catalog['entries'][0]['member_id']
    context['privacy']['personal']['permission_generation'] += 1
    assert b''.join(store.iter_member(member_id)) == b'private'
    store.block_paths(['state/private-name.md'], 'erased')
    assert store.load_catalog()['entries'][0]['state'] == 'blocked'
    with pytest.raises(QuarantineError):
        next(store.iter_member(member_id))
    assert store.verify()['blocked'] == 1


@pytest.mark.parametrize('change', ['owner', 'profile', 'privacy'])
def test_unauthorized_catalog_refused(setup, change):
    store, context = setup
    capture(store, b'private')
    context[change] = {} if change == 'privacy' else 'other'
    with pytest.raises(QuarantineError):
        store.load_catalog()


def test_authority_change_during_capture_refuses_seal(setup):
    store, context = setup
    entry = entry_for(b'data')
    store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)
    store.start(entry)
    store.write(b'data')
    context['privacy']['personal']['permission_generation'] += 1
    with pytest.raises(QuarantineError):
        store.finish()
    assert not (store.root / 'catalog.ae1').exists()


def test_no_root_reuse_and_no_duplicate_paths(setup):
    store, _ = setup
    capture(store, b'private')
    with pytest.raises(QuarantineError):
        capture(store, b'private')
    second = QuarantineStore(store.root.parent / 'other', store.cipher, authority=trusted)
    entry = entry_for(b'')
    with pytest.raises(QuarantineError):
        second.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry, entry]}, 'b' * 64)


def test_linked_root_ancestry_refused(setup, tmp_path):
    store, _ = setup
    (tmp_path / 'linked').symlink_to(tmp_path, target_is_directory=True)
    linked = QuarantineStore(tmp_path / 'linked' / 'new', store.cipher, authority=trusted)
    with pytest.raises(QuarantineError):
        capture(linked, b'private')


@pytest.mark.parametrize('attack', ['extra', 'symlink', 'hardlink'])
def test_physical_inventory_refuses_extra_and_links(setup, attack):
    store, _ = setup
    catalog = capture(store, b'data')
    chunk_path = store.root / catalog['entries'][0]['chunks'][0]['name']
    if attack == 'extra':
        (store.root / 'extra').write_bytes(b'AE1')
    elif attack == 'symlink':
        original = chunk_path.read_bytes()
        chunk_path.unlink()
        target = store.root.parent / 'external'
        target.write_bytes(original)
        chunk_path.symlink_to(target)
    else:
        (store.root.parent / 'alias').hardlink_to(chunk_path)
    with pytest.raises(QuarantineError):
        store.verify()


def test_reopened_store_and_wrong_key(setup):
    store, _ = setup
    capture(store, b'data')
    reopened = QuarantineStore(store.root, store.cipher, authority=trusted)
    assert reopened.verify()['members'] == 1
    wrong = EnvelopeCipher(StaticKeyProvider({1: b'w' * 32}))
    with pytest.raises(QuarantineError):
        QuarantineStore(store.root, wrong, authority=trusted).load_catalog()


def rewrite_catalog(store, mutate):
    context = store._context()
    with store._opened(context) as descriptor:
        catalog, raw = store._load(descriptor, context)
        mutate(catalog)
        store._publish(descriptor, catalog, context, previous=raw)


@pytest.mark.parametrize('mutation', [
    lambda catalog: catalog.update(profile='work'),
    lambda catalog: catalog.update(checkpoint_id='ck_other'),
    lambda catalog: catalog.update(checkpoint_sha256='d' * 64),
    lambda catalog: catalog['entries'][0].update(sha256='d' * 64),
    lambda catalog: catalog['entries'][0].update(size=0),
    lambda catalog: catalog['entries'][0].update(member_id='z' * 32),
    lambda catalog: catalog['entries'][0].update(state='released'),
    lambda catalog: catalog['entries'].append(copy.deepcopy(catalog['entries'][0])),
    lambda catalog: catalog['entries'][0]['chunks'][0].update(name='../escape'),
    lambda catalog: catalog['entries'][0]['chunks'][0].update(length=0),
    lambda catalog: catalog['entries'][0]['chunks'][0].update(ciphertext_length=0),
])
def test_authenticated_but_inconsistent_catalog_refused(setup, mutation):
    store, _ = setup
    capture(store, b'data')
    rewrite_catalog(store, mutation)
    with pytest.raises(QuarantineError):
        store.verify()


@pytest.mark.parametrize('mutation', [
    lambda context: context.update(actor=''),
    lambda context: context.update(owner='stranger'),
    lambda context: context.update(profile='wrong'),
    lambda context: context.update(privacy={}),
    lambda context: context['privacy']['personal'].update(checkpoint_id=True),
    lambda context: context['privacy']['personal'].update(permission_generation=-1),
    lambda context: context['privacy']['personal'].update(tombstone_digest='invalid'),
    lambda context: context.update(policy='x' * (64 * 1024)),
])
def test_invalid_authority_never_creates_output(setup, mutation):
    store, context = setup
    mutation(context)
    with pytest.raises(QuarantineError):
        capture(store, b'data')
    assert not store.root.exists()


@pytest.mark.parametrize('operation', ['seal', 'block'])
def test_catalog_temporary_substitution_cannot_report_success(setup, monkeypatch, operation):
    store, _ = setup
    entry = entry_for(b'original')
    if operation == 'block':
        capture(store, b'original')
    else:
        store.begin({'profile': 'personal', 'generation': 'ck_original', 'entries': [entry]}, 'b' * 64)
        store.start(entry)
        store.write(b'original')
        store.finish()
    original = store._write
    def substitute(descriptor, name, raw, context):
        original(descriptor, name, raw, context)
        if '-' not in name:
            subprocess.run([sys.executable, '-c',
                'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b"AE1-substituted-ciphertext")',
                str(store.root / name)], check=True)
    monkeypatch.setattr(store, '_write', substitute)
    with pytest.raises(QuarantineError):
        if operation == 'seal':
            store.seal()
        else:
            store.block_paths([entry['path']], 'known erasure')


@pytest.mark.parametrize('field,value', [
    ('path', '../outside'), ('path', '/absolute'), ('path', 'state//file'),
    ('path', 'state\\file'), ('path', 'state/file\n'), ('path', 'C:file'),
    ('path', 'x' * 4097), ('size', -1), ('size', True),
    ('size', 16 * 1024**3 + 1), ('sha256', 'invalid'), ('kind', 'symlink'), ('mode', 0o10000),
])
def test_invalid_manifest_member_refused(setup, field, value):
    store, _ = setup
    entry = entry_for(b'data')
    entry[field] = value
    with pytest.raises(QuarantineError):
        store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)
    assert not store.root.exists()


@pytest.mark.parametrize('manifest', [
    {}, {'profile': 'work', 'generation': 'ck_test', 'entries': []},
    {'profile': 'personal', 'generation': '../bad', 'entries': []},
    {'profile': 'personal', 'generation': 'ck_test', 'entries': {}},
    {'profile': 'personal', 'generation': 'ck_test', 'entries': [], 'privacy': {}},
])
def test_invalid_manifest_identity_refused(setup, manifest):
    store, _ = setup
    with pytest.raises(QuarantineError):
        store.begin(manifest, 'b' * 64)


def test_ciphertext_swap_and_catalog_copy_refused(setup):
    store, _ = setup
    catalog = capture(store, b'a' * 777777 + b'b' * 777777)
    chunks = catalog['entries'][0]['chunks']
    first, second = (store.root / item['name'] for item in chunks)
    original = first.read_bytes()
    first.write_bytes(second.read_bytes())
    second.write_bytes(original)
    with pytest.raises(QuarantineError):
        next(store.iter_member(catalog['entries'][0]['member_id']))
    other = QuarantineStore(store.root.parent / 'different', store.cipher, authority=trusted)
    capture(other, b'other')
    (other.root / 'catalog.ae1').write_bytes((store.root / 'catalog.ae1').read_bytes())
    with pytest.raises(QuarantineError):
        other.load_catalog()


def test_substitution_between_passes_releases_no_new_bytes(setup, monkeypatch):
    store, _ = setup
    catalog = capture(store, b'original')
    member = catalog['entries'][0]
    original = store._verify_member

    def replace_after_verification(*args):
        original(*args)
        raw, _ = store.cipher.encrypt(b'EVIL_NEW', aad=store._chunk_aad(catalog, member, 0))
        (store.root / member['chunks'][0]['name']).write_bytes(raw)

    monkeypatch.setattr(store, '_verify_member', replace_after_verification)
    with pytest.raises(QuarantineError):
        next(store.iter_member(member['member_id']))


@pytest.mark.parametrize('operation', ['encrypt', 'decrypt'])
def test_authority_changes_inside_cipher_refuse(setup, monkeypatch, operation):
    store, context = setup
    if operation == 'decrypt':
        catalog = capture(store, b'data')
    else:
        entry = entry_for(b'data')
        store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)
        store.start(entry)
    original = getattr(store.cipher, operation)

    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        context['privacy']['personal']['permission_generation'] += 1
        return result

    monkeypatch.setattr(store.cipher, operation, revoke)
    with pytest.raises(QuarantineError):
        if operation == 'decrypt':
            next(store.iter_member(catalog['entries'][0]['member_id']))
        else:
            store.write(b'data')
    assert operation == 'decrypt' or not (store.root / 'catalog.ae1').exists()


def test_authority_change_between_yields_and_catalog_update_refused(setup):
    store, context = setup
    catalog = capture(store, b'x' * 1500000)
    iterator = store.iter_member(catalog['entries'][0]['member_id'])
    assert len(next(iterator)) == 777777
    context['privacy']['personal']['tombstone_digest'] = 'f' * 64
    with pytest.raises(QuarantineError):
        next(iterator)
    store.block_paths(['state/private-name.md'], 'revoked')
    assert store.verify()['blocked'] == 1


def test_chunk_and_member_limits_and_invalid_lifecycle(setup, monkeypatch):
    store, _ = setup
    with pytest.raises(QuarantineError):
        store.finish()
    entry = entry_for(b'data')
    store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)
    with pytest.raises(QuarantineError):
        store.write(b'data')
    store.start(entry)
    with pytest.raises(QuarantineError):
        store.start(entry)
    with pytest.raises(QuarantineError):
        store.seal()
    for invalid in [b'x' * (quarantine.CHUNK_BYTES + 1), 'data', b'longer']:
        with pytest.raises(QuarantineError):
            store.write(invalid)
    monkeypatch.setattr(quarantine, 'MAX_CHUNKS', 0)
    with pytest.raises(QuarantineError):
        store.write(b'data')


def test_incomplete_or_wrong_hash_never_seals(setup):
    store, _ = setup
    entry = entry_for(b'data')
    store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)
    store.start(entry)
    store.write(b'evil')
    with pytest.raises(QuarantineError):
        store.finish()
    with pytest.raises(QuarantineError):
        store.seal()
    assert not (store.root / 'catalog.ae1').exists()


@pytest.mark.parametrize('paths,reason', [(['missing'], 'erased'), ('path', 'erased'),
                                         ([1], 'erased'), ([], ''), ([], 'x' * 1025)])
def test_denial_input_refused(setup, paths, reason):
    store, _ = setup
    capture(store, b'data')
    before = (store.root / 'catalog.ae1').read_bytes()
    with pytest.raises(QuarantineError):
        store.block_paths(paths, reason)
    assert (store.root / 'catalog.ae1').read_bytes() == before


def test_declared_bounds_are_enforced_before_disk(setup, monkeypatch):
    store, _ = setup
    entry = entry_for(b'data')
    manifest = {'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}
    monkeypatch.setattr(quarantine, 'MAX_ENTRIES', 0)
    with pytest.raises(QuarantineError):
        store.begin(manifest, 'b' * 64)
    monkeypatch.setattr(quarantine, 'MAX_ENTRIES', 16384)
    monkeypatch.setattr(quarantine, 'MAX_TOTAL_BYTES', 3)
    with pytest.raises(QuarantineError):
        store.begin(manifest, 'b' * 64)
    assert not store.root.exists()


def test_only_get_set_fake_credential_backend(tmp_path):
    import base64

    class FakeKeys:
        def __init__(self):
            self.records = {'current_version': '1', 'key.1': base64.b64encode(b'q' * 32).decode()}

        def get_password(self, service, account):
            return self.records.get(account)

        def set_password(self, service, account, value):
            self.records[account] = value

    backend = FakeKeys()
    before = dict(backend.records)
    cipher = EnvelopeCipher(KeyringKeyProvider(backend=backend, create=False))
    store = QuarantineStore(tmp_path / 'fake-key-store', cipher, authority=trusted)
    capture(store, b'data')
    assert store.verify()['bytes'] == 4
    assert backend.records == before


@pytest.mark.parametrize('phase', ['begin', 'write', 'finish', 'catalog'])
def test_real_process_death_leaves_only_ciphertext(tmp_path, phase):
    root = tmp_path / phase
    script = '''
import hashlib, os, signal, sys
from pathlib import Path
KILL = signal.SIGTERM if os.name == 'nt' else signal.SIGKILL
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.recovery_quarantine import QuarantineStore
root, phase = Path(sys.argv[1]), sys.argv[2]
data = b'FORBIDDEN_PRIVATE_MARKER_IN_PROCESS' * 1000
context = {'actor':'owner', 'owner':'owner', 'profile':'personal', 'privacy': {
 'personal': {'checkpoint_id':9, 'permission_generation':3, 'tombstone_digest':'a'*64}}}
cipher = EnvelopeCipher(StaticKeyProvider({1:b'q'*32}))
store = QuarantineStore(root, cipher, authority=lambda:context)
entry = {'path':'state/private-name.md', 'size':len(data),
 'sha256':hashlib.sha256(data).hexdigest(), 'kind':'file', 'mode':384}
store.begin({'profile':'personal', 'generation':'ck_killed', 'entries':[entry]}, 'b'*64)
if phase == 'begin': os.kill(os.getpid(), KILL)
store.start(entry)
store.write(data)
if phase == 'write': os.kill(os.getpid(), KILL)
store.finish()
if phase == 'finish': os.kill(os.getpid(), KILL)
original = cipher.encrypt
def stop_before_catalog(plaintext, aad=b''):
 if b'catalog' in aad: os.kill(os.getpid(), KILL)
 return original(plaintext, aad=aad)
cipher.encrypt = stop_before_catalog
store.seal()
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'scripts'))
    process = subprocess.run([sys.executable, '-c', script, str(root), phase],
                             env=environment, capture_output=True, timeout=20)
    # Windows os.kill(SIGTERM) is TerminateProcess(exit 15): no handlers or finally blocks run.
    assert process.returncode == (15 if os.name == 'nt' else -9), process.stderr.decode()
    assert not (root / 'catalog.ae1').exists()
    for path in _ciphertext_files(root):
        assert path.is_file()
        assert path.read_bytes().startswith(b'AE1')
        assert b'FORBIDDEN_PRIVATE_MARKER' not in path.read_bytes()
        assert b'private-name' not in path.read_bytes()


def test_root_replacement_during_decryption_refuses_before_yield(setup, monkeypatch):
    store, _ = setup
    catalog = capture(store, b'original')
    original = store.cipher.decrypt
    replaced = False

    def replace_root(raw, aad=b''):
        nonlocal replaced
        plaintext = original(raw, aad=aad)
        if b'"catalog"' not in aad and not replaced:
            replaced = True
            store.root.rename(store.root.parent / 'moved')
            store.root.mkdir(mode=0o700)
        return plaintext

    monkeypatch.setattr(store.cipher, 'decrypt', replace_root)
    with pytest.raises(QuarantineError):
        next(store.iter_member(catalog['entries'][0]['member_id']))


def test_empty_write_is_safe_stream_noop(setup):
    store, _ = setup
    entry = entry_for(b'')
    store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)
    store.start(entry)
    store.write(b'')
    store.finish()
    store.seal()
    assert store.verify() == {'members': 1, 'bytes': 0, 'chunks': 0, 'blocked': 0}


@pytest.mark.parametrize('entry', [[], entry_for(b'', 'state/\ud800')])
def test_non_object_and_invalid_unicode_members_refused(setup, entry):
    store, _ = setup
    with pytest.raises(QuarantineError):
        store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'[]', b'invalid', b'{"x":NaN}'])
def test_invalid_json_refused(raw):
    with pytest.raises(QuarantineError):
        quarantine._decode(raw)


@pytest.mark.parametrize('value', [float('nan'), object(), 'x' * 100])
def test_invalid_or_oversized_json_refused(value):
    with pytest.raises(QuarantineError):
        quarantine._json(value, 32)


@pytest.mark.parametrize('attack', ['mode', 'truncate', 'directory', 'missing'])
def test_unsafe_ciphertext_refused(setup, attack):
    store, _ = setup
    capture(store, b'data')
    path = store.root / 'catalog.ae1'
    if attack == 'mode':
        _share(path, 0o644)
    elif attack == 'truncate':
        path.write_bytes(b'AE1')
    else:
        path.unlink()
        if attack == 'directory':
            path.mkdir()
    with pytest.raises(QuarantineError):
        store.load_catalog()


def test_root_mode_and_callback_failure_are_sanitized(setup):
    store, _ = setup
    capture(store, b'data')
    _share(store.root, 0o755)
    with pytest.raises(QuarantineError):
        store.verify()
    _unshare(store.root, 0o700)

    def fail():
        raise RuntimeError('private-name-and-content')

    store.authority = fail
    with pytest.raises(QuarantineError) as failure:
        store.load_catalog()
    assert 'private-name' not in str(failure.value)


def test_unknown_member_and_duplicate_capture_refused(setup):
    store, _ = setup
    entry = entry_for(b'data')
    store.begin({'profile': 'personal', 'generation': 'ck_test', 'entries': [entry]}, 'b' * 64)
    with pytest.raises(QuarantineError):
        store.start(entry_for(b'other'))
    store.start(entry)
    store.write(b'data')
    store.finish()
    with pytest.raises(QuarantineError):
        store.start(entry)
    store.seal()
    with pytest.raises(QuarantineError):
        next(store.iter_member('0' * 32))


def test_large_member_is_streamed_in_fixed_bounds(setup):
    store, _ = setup
    chunk = b'FORBIDDEN' * 131072
    chunk = chunk[:quarantine.CHUNK_BYTES]
    digest = hashlib.sha256()
    for _ in range(20):
        digest.update(chunk)
    entry = dict(entry_for(b''), size=len(chunk) * 20, sha256=digest.hexdigest())
    store.begin({'profile': 'personal', 'generation': 'ck_large', 'entries': [entry]}, 'b' * 64)
    store.start(entry)
    for _ in range(20):
        store.write(chunk)
    store.finish()
    catalog = store.seal()
    assert store.verify()['bytes'] == len(chunk) * 20
    assert all(len(piece) == quarantine.CHUNK_BYTES
               for piece in store.iter_member(catalog['entries'][0]['member_id']))


@pytest.mark.parametrize('captured', [0, 1])
def test_seal_refuses_missing_expected_members(setup, captured):
    store, _ = setup
    entries = [entry_for(b'data', f'state/{index}.md') for index in range(2)]
    store.begin({'profile': 'personal', 'generation': 'ck_missing', 'entries': entries}, 'b' * 64)
    for entry in entries[:captured]:
        store.start(entry)
        store.write(b'data')
        store.finish()
    with pytest.raises(QuarantineError):
        store.seal()
    assert not (store.root / 'catalog.ae1').exists()


@pytest.mark.parametrize('field,value', [('kind', 'sqlite'), ('mode', 0o400),
                                        ('path', 'state/other.md'), ('sha256', 'd' * 64),
                                        ('size', 0), ('extra', 'unexpected')])
def test_seal_requires_exact_expected_metadata(setup, field, value):
    store, _ = setup
    entry = entry_for(b'data')
    store.begin({'profile': 'personal', 'generation': 'ck_metadata', 'entries': [entry]}, 'b' * 64)
    store.start(entry)
    store.write(b'data')
    store.finish()
    store._catalog['entries'][0][field] = value
    with pytest.raises(QuarantineError):
        store.seal()
    assert not (store.root / 'catalog.ae1').exists()


def test_expected_empty_manifest_seals(setup):
    store, _ = setup
    store.begin({'profile': 'personal', 'generation': 'ck_empty', 'entries': []}, 'b' * 64)
    assert store.seal()['entries'] == []
    assert store.verify() == {'members': 0, 'chunks': 0, 'bytes': 0, 'blocked': 0}


@pytest.mark.parametrize('attack', ['catalog', 'late_chunk', 'same_size_chunk', 'root', 'extra'])
def test_second_pass_decrypt_mutation_refuses_before_yield(setup, monkeypatch, attack):
    store, context = setup
    catalog = capture(store, b'a' * 777777 + b'b')
    member = catalog['entries'][0]
    original = store.cipher.decrypt
    calls = 0
    with store._opened(store._context()) as descriptor:
        catalog_aad = store._catalog_aad(descriptor, store._context())

    def substitute(raw, aad=b''):
        nonlocal calls
        plaintext = original(raw, aad=aad)
        if b'"catalog"' not in aad:
            calls += 1
            if calls == len(member['chunks']) + 1:
                if attack == 'catalog':
                    changed = copy.deepcopy(catalog)
                    changed['entries'][0].update(state='blocked', reason='erased')
                    encrypted, _ = store.cipher.encrypt(quarantine._json(changed), aad=catalog_aad)
                    (store.root / 'catalog.ae1').write_bytes(encrypted)
                elif attack == 'late_chunk':
                    (store.root / member['chunks'][-1]['name']).write_bytes(b'AE1')
                elif attack == 'same_size_chunk':
                    encrypted, _ = store.cipher.encrypt(b'X', aad=store._chunk_aad(catalog, member, 1))
                    (store.root / member['chunks'][-1]['name']).write_bytes(encrypted)
                elif attack == 'root':
                    store.root.rename(store.root.parent / 'moved')
                    store.root.mkdir(mode=0o700)
                else:
                    (store.root / 'extra').write_bytes(b'AE1')
        return plaintext

    monkeypatch.setattr(store.cipher, 'decrypt', substitute)
    iterator = store.iter_member(member['member_id'])
    with pytest.raises(QuarantineError):
        next(iterator)
    assert context == trusted()


def test_6500_tiny_members_seal_and_verify_schema_v1(setup):
    store, _ = setup
    entries = [entry_for(b'x', f'state/{index}.md') for index in range(6500)]
    store.begin({'profile': 'personal', 'generation': 'ck_many', 'entries': entries}, 'b' * 64)
    for entry in entries:
        store.start(entry)
        store.write(b'x')
        store.finish()
    catalog = store.seal()
    assert catalog['schema'] == 'artha.recovery-quarantine.v1'
    assert len(catalog['entries']) == 6500
    reopened = QuarantineStore(store.root, store.cipher, authority=trusted)
    assert reopened.verify() == {'members': 6500, 'chunks': 6500, 'bytes': 6500, 'blocked': 0}
    assert quarantine.MAX_ENTRIES == 16384
    assert quarantine.MAX_CATALOG_BYTES == 8 * 1024 * 1024


def test_wire_storage_has_only_catalog_and_hashed_chunk_references(setup):
    store, _ = setup
    catalog = capture(store, b'a' * 1500000)
    store.block_paths([catalog['entries'][0]['path']], 'known erasure')
    reopened = QuarantineStore(store.root, store.cipher, authority=trusted)
    loaded = reopened.load_catalog()
    assert loaded['entries'][0]['state'] == 'blocked'
    assert loaded['entries'][0]['reason'] == 'known erasure'
    expected = {'catalog.ae1', '.lock'} if os.name == 'nt' else {'catalog.ae1'}
    for member in loaded['entries']:
        for chunk in member['chunks']:
            expected.add(chunk['name'])
            raw = (store.root / chunk['name']).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == chunk['sha256']
            assert len(raw) == chunk['ciphertext_length']
    assert {path.name for path in store.root.iterdir()} == expected
    with pytest.raises(QuarantineError):
        next(reopened.iter_member(loaded['entries'][0]['member_id']))


def test_loading_catalog_refuses_loose_ciphertext(setup):
    store, _ = setup
    capture(store, b'data')
    (store.root / 'extra.ae1').write_bytes(b'AE1')
    reopened = QuarantineStore(store.root, store.cipher, authority=trusted)
    with pytest.raises(QuarantineError):
        reopened.load_catalog()


@pytest.mark.parametrize('same_instance', [True, False])
def test_iterator_lock_refuses_concurrent_catalog_mutation(setup, same_instance):
    store, _ = setup
    catalog = capture(store, b'x' * 1500000)
    before = (store.root / 'catalog.ae1').read_bytes()
    contender = store if same_instance else QuarantineStore(store.root, store.cipher, authority=trusted)
    iterator = store.iter_member(catalog['entries'][0]['member_id'])
    try:
        assert next(iterator) == b'x' * 777777
        with pytest.raises(QuarantineError):
            contender.block_paths([catalog['entries'][0]['path']], 'erased')
        assert (store.root / 'catalog.ae1').read_bytes() == before
    finally:
        iterator.close()
    contender.block_paths([catalog['entries'][0]['path']], 'erased')
    assert store.verify()['blocked'] == 1


def test_independent_process_holds_record_lock(setup):
    store, _ = setup
    catalog = capture(store, b'data')
    if os.name == 'nt':
        script = '''
import sys
from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory
with WindowsQuarantineDirectory.open(sys.argv[1]) as directory, directory.lock():
    print('locked', flush=True)
    sys.stdin.readline()
'''
    else:
        script = '''
import fcntl, os, sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
print('locked', flush=True)
sys.stdin.readline()
os.close(descriptor)
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'scripts'))
    process = subprocess.Popen([sys.executable, '-c', script, str(store.root)], env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(QuarantineError):
            store.block_paths([catalog['entries'][0]['path']], 'erased')
    finally:
        process.communicate('\n', timeout=20)
    assert process.returncode == 0
    store.block_paths([catalog['entries'][0]['path']], 'erased')
    assert store.verify()['blocked'] == 1


@pytest.mark.parametrize('attack', ['owner', 'root'])
def test_publication_callback_mutation_cannot_replace_catalog(setup, monkeypatch, attack):
    store, context = setup
    catalog = capture(store, b'data')
    before = (store.root / 'catalog.ae1').read_bytes()
    original = store.cipher.encrypt
    moved = store.root.parent / 'moved'

    def mutate_during_publish(*args, **kwargs):
        result = original(*args, **kwargs)
        if attack == 'owner':
            context['owner'] = 'stranger'
        elif os.name == 'nt':
            # Held native handles omit FILE_SHARE_DELETE, so the pinned root cannot be renamed.
            with pytest.raises(PermissionError):
                store.root.rename(moved)
            raise PermissionError('root rename refused')
        else:
            store.root.rename(moved)
            store.root.mkdir(mode=0o700)
        return result

    monkeypatch.setattr(store.cipher, 'encrypt', mutate_during_publish)
    with pytest.raises(QuarantineError):
        store.block_paths([catalog['entries'][0]['path']], 'erased')
    original_root = moved if attack == 'root' and os.name != 'nt' else store.root
    assert (original_root / 'catalog.ae1').read_bytes() == before
    if attack == 'root' and os.name == 'nt':
        assert not moved.exists()
    elif attack == 'root':
        assert list(store.root.iterdir()) == []


def test_non_posix_descriptor_io_explicitly_unsupported(monkeypatch, tmp_path):
    monkeypatch.setattr(quarantine.os, 'name', 'nt')
    with pytest.raises(QuarantineError, match='secure descriptor-relative quarantine IO is unavailable'):
        QuarantineStore._directory(tmp_path)


def test_schema_v1_catalog_entry_bound_is_enforced():
    member = dict(entry_for(b''), member_id='0' * 32, state='quarantined', chunks=[])
    catalog = {'schema': 'artha.recovery-quarantine.v1', 'checkpoint_id': 'ck_bound',
               'checkpoint_sha256': 'b' * 64, 'profile': 'personal', 'owner': 'owner',
               'entries': [member] * (16384 + 1)}
    with pytest.raises(QuarantineError, match='catalog identity'):
        QuarantineStore._validate(catalog, trusted())


def test_catalog_plaintext_still_has_eight_mib_bound(setup):
    store, _ = setup
    entry = dict(entry_for(b''), metadata='x' * quarantine.MAX_CATALOG_BYTES)
    with pytest.raises(QuarantineError, match='exceeds its bound'):
        store.begin({'profile': 'personal', 'generation': 'ck_large', 'entries': [entry]}, 'b' * 64)
    assert not store.root.exists()


def test_original_recovery_affinity_is_capture_only_and_survives_catalog_updates(setup):
    store, _ = setup
    entry = entry_for(b'original')
    affinity = {'schema': 'artha.recovery-affinity.v1', 'source_artifacts': {'source-key': [entry['path']]}}
    store.begin({'profile': 'personal', 'generation': 'ck_original', 'entries': [entry]}, 'b' * 64)
    store.start(entry)
    with pytest.raises(QuarantineError):
        store.set_recovery_affinity(affinity)
    store.write(b'original')
    store.finish()
    store.set_recovery_affinity(affinity)
    affinity['source_artifacts'].clear()  # caller mutation cannot alter the pinned original metadata.
    with pytest.raises(QuarantineError):
        store.set_recovery_affinity(affinity)
    catalog = store.seal()
    assert catalog['recovery_affinity']['source_artifacts'] == {'source-key': [entry['path']]}
    store.block_paths([entry['path']], 'known erasure')
    assert store.load_catalog()['recovery_affinity'] == catalog['recovery_affinity']
    with pytest.raises(QuarantineError):
        store.set_recovery_affinity(affinity)
    reopened = QuarantineStore(store.root, store.cipher, authority=trusted)
    assert reopened.load_catalog()['recovery_affinity'] == catalog['recovery_affinity']


@pytest.mark.parametrize('affinity', [None, [], {}, {'schema': 'unknown'},
    {'schema': 'artha.recovery-affinity.v1', 'oversized': 'x' * quarantine.MAX_CATALOG_BYTES}])
def test_unproven_or_oversized_affinity_cannot_be_added_to_capture(setup, affinity):
    store, _ = setup
    store.begin({'profile': 'personal', 'generation': 'ck_empty', 'entries': []}, 'b' * 64)
    with pytest.raises(QuarantineError):
        store.set_recovery_affinity(affinity)
