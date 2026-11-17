"""Native enrolled Work updates preserve the existing file and its streams."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from _work_synthetic import reconcile_reviewed
from core_helpers import FileBackend, STREAM_CIPHER as CIPHER, WORK_IDENTITY as IDENTITY, WORK_PRIVACY_KEY as KEY

from catchup_core import quarantine_fs_windows as fs
from catchup_core import work_privacy as privacy
from catchup_core.session_lock import SessionBusy
from catchup_core.work_privacy import WorkPrivacy, WorkPrivacyBlocked
from lib import work_publication as wp

windows_only = pytest.mark.skipif(os.name != 'nt', reason='native Windows Work publication')


@pytest.fixture
def legacy_enrolled(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    repo = tmp_path / 'repo'
    root = repo / 'state/work'
    root.mkdir(parents=True)
    member = root / 'notes.md'
    member.write_bytes(b'old synthetic notes')
    p = WorkPrivacy(repo, tmp_path / 'local', {'work_state': root},
                    backend=FileBackend(tmp_path / 'keys'), native_identity=lambda: dict(IDENTITY))
    p.enroll(KEY, reviewed=True, installation_id='synthetic-installation')
    Path(str(member) + ':synthetic').write_bytes(b'keep these stream bytes')
    Path(str(member) + ':empty').write_bytes(b'')
    Path(str(root) + ':root-stream').write_bytes(b'root bytes')
    return p, member


@pytest.fixture
def enrolled(legacy_enrolled):
    p, member = legacy_enrolled
    review = p.preview_stream_history()
    p.upgrade_stream_history(reviewed=True, expected_digest=review['inventory_sha256'])
    return p, member


@pytest.fixture
def health_receipt_protocol(tmp_path, monkeypatch):
    local = tmp_path / 'local'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    repo = tmp_path / 'repo'
    state = repo / 'state/work'
    cache = local / 'work/cache'
    state.mkdir(parents=True)
    cache.mkdir(parents=True)
    member = cache / 'health.json'
    member.write_text(json.dumps({'available': False, 'auth_valid': False, 'platform': 'Windows',
                                  'checked_at': '2000-01-01T00:00:00+00:00'}))
    p = WorkPrivacy(repo, local, {'work_state': state, 'work_local': local / 'work'},
                    backend=FileBackend(tmp_path / 'keys'), native_identity=lambda: dict(IDENTITY))
    previous = wp.set_protocol_factory(lambda: p)
    try:
        yield p, member
    finally:
        wp.set_protocol_factory(previous)


@pytest.fixture
def enrolled_health_receipt(health_receipt_protocol):
    p, member = health_receipt_protocol
    p.enroll(KEY, reviewed=True, installation_id='synthetic-installation')
    Path(str(member) + ':synthetic').write_bytes(b'keep this stream')
    review = p.preview_stream_history()
    p.upgrade_stream_history(reviewed=True, expected_digest=review['inventory_sha256'])
    return p, member


def native_metadata(member):
    with fs.WindowsQuarantineDirectory.open(member.parent, owner_private=False) as parent:
        with parent.open_file(member.name, private=False) as opened:
            return opened.identity, opened._native.security_bytes(opened.handle)


@pytest.mark.parametrize('raw', [b'new', b'longer synthetic notes' * 12, b''])
@windows_only
def test_v1_update_preserves_streams_without_migration_or_stream_authority(legacy_enrolled, monkeypatch, raw):
    p, member = legacy_enrolled
    binding = p.bind_checkpoint()
    metadata = native_metadata(member)
    monkeypatch.setattr(p, '_inventory_streams', lambda: pytest.fail('v1 writer invented stream history'))
    result = p.publish({'work_state/notes.md': raw})
    assert result['schema'] == 'artha.work-member-history.v1' and 'streams' not in result
    assert member.read_bytes() == raw and native_metadata(member) == metadata
    assert Path(str(member) + ':synthetic').read_bytes() == b'keep these stream bytes'
    assert Path(str(member) + ':empty').read_bytes() == b''
    assert Path(str(member.parent) + ':root-stream').read_bytes() == b'root bytes'
    assert p.denied_members(binding, ['work_state/notes.md']) == {'work_state/notes.md'}


@windows_only
def test_v1_delete_invalidates_member_without_inventing_stream_history(legacy_enrolled):
    p, member = legacy_enrolled
    binding = p.bind_checkpoint()
    result = p.publish({'work_state/notes.md': None})
    assert not member.exists() and 'work_state/notes.md' not in result['members']
    assert result['schema'] == 'artha.work-member-history.v1' and 'streams' not in result
    assert p.denied_members(binding, ['work_state/notes.md']) == {'work_state/notes.md'}
    assert Path(str(member.parent) + ':root-stream').read_bytes() == b'root bytes'


@pytest.mark.parametrize('schema', ['artha.work-member-history.v1', 'artha.work-member-history.v2'])
def test_non_windows_member_mutation_gate_remains_held(monkeypatch, schema):
    monkeypatch.setattr(privacy, 'os', SimpleNamespace(name='posix'))
    with pytest.raises(WorkPrivacyBlocked, match='temporarily paused'):
        WorkPrivacy._hold_member_mutations({'schema': schema, 'members': {'work_state/notes.md': {}}},
                                           {'work_state/notes.md': b'update'})


@windows_only
def test_large_asset_history_uses_bounded_reads_and_allows_small_publication(legacy_enrolled, monkeypatch):
    p, member = legacy_enrolled
    asset = member.parent / 'synthetic.bin'
    size = (32 << 20) + 123
    with asset.open('wb') as out:
        remaining = size
        while remaining:
            piece = bytes(min(remaining, 65536))
            out.write(piece)
            remaining -= len(piece)
        out.flush()
        os.fsync(out.fileno())
    Path(str(asset) + ':synthetic').write_bytes(b'preserve asset stream')
    reads = []
    original = fs.WindowsQuarantineFile.read_chunk

    def bounded(opened, count):
        if opened.name == asset.name:
            reads.append(count)
            assert 0 < count <= 65536
        return original(opened, count)

    monkeypatch.setattr(fs.WindowsQuarantineFile, 'read_chunk', bounded)
    current = reconcile_reviewed(p)
    digest = hashlib.sha256()
    block = bytes(65536)
    remaining = size
    while remaining:
        digest.update(block[:min(len(block), remaining)])
        remaining -= min(len(block), remaining)
    expected = {'size': size, 'sha256': digest.hexdigest()}
    assert current['members']['work_state/synthetic.bin'] == expected
    assert reads and max(reads) <= 65536
    result = p.publish({'work_state/notes.md': b'small edit'})
    assert result['members']['work_state/synthetic.bin'] == expected
    assert Path(str(asset) + ':synthetic').read_bytes() == b'preserve asset stream'


@pytest.mark.parametrize('raw', [None, b'small replacement'])
@windows_only
def test_large_existing_member_can_be_deleted_or_replaced_without_buffering(legacy_enrolled, raw):
    p, member = legacy_enrolled
    with member.open('r+b') as out:
        out.truncate((8 << 20) + 17)
        out.flush()
        os.fsync(out.fileno())
    reconcile_reviewed(p)
    metadata = native_metadata(member)
    result = p.publish({'work_state/notes.md': raw})
    if raw is None:
        assert not member.exists() and 'work_state/notes.md' not in result['members']
    else:
        assert member.read_bytes() == raw and native_metadata(member) == metadata
        assert Path(str(member) + ':synthetic').read_bytes() == b'keep these stream bytes'


@pytest.mark.parametrize('mutation', ['grow', 'truncate', 'same-size', 'early-end', 'short-read'])
@windows_only
def test_inventory_detects_mutation_and_handles_partial_native_reads(legacy_enrolled, monkeypatch, mutation):
    p, member = legacy_enrolled
    original = fs.WindowsQuarantineFile.read_chunk
    changed = False

    def read(opened, count):
        nonlocal changed
        if opened.name != member.name:
            return original(opened, count)
        if mutation == 'early-end':
            return b''
        if mutation == 'short-read':
            return original(opened, min(count, 3))
        raw = original(opened, count)
        if not changed:
            changed = True
            with member.open('r+b') as writer:
                if mutation == 'grow':
                    writer.seek(0, 2)
                    writer.write(b'growth')
                elif mutation == 'truncate':
                    writer.truncate(1)
                else:
                    writer.write(b'X')
                writer.flush()
                os.fsync(writer.fileno())
        return raw

    monkeypatch.setattr(fs.WindowsQuarantineFile, 'read_chunk', read)
    if mutation == 'short-read':
        assert p.current()['members']['work_state/notes.md']['sha256'] == hashlib.sha256(member.read_bytes()).hexdigest()
    else:
        with pytest.raises(WorkPrivacyBlocked, match='changed'):
            p.current()


@windows_only
def test_reconcile_review_detects_real_process_drift_during_preview(enrolled, tmp_path, monkeypatch):
    p, member = enrolled
    ready, proceed = tmp_path / 'child-ready', tmp_path / 'child-proceed'
    program = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "member, ready, proceed = map(Path, sys.argv[1:])\n"
        "ready.write_text('ready')\n"
        "for _ in range(1000):\n"
        "    if proceed.exists():\n"
        "        member.write_bytes(b'changed by synthetic child')\n"
        "        break\n"
        "    time.sleep(.02)\n"
        "else:\n"
        "    sys.exit(9)\n"
    )
    process = subprocess.Popen([sys.executable, '-c', program, str(member), str(ready), str(proceed)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        original = p._inventory
        started = False

        def race_inventory():
            nonlocal started
            snapshot = original()
            if not started:
                started = True
                proceed.write_text('proceed')
                out, err = process.communicate(timeout=20)
                assert process.returncode == 0, (out, err)
            return snapshot

        monkeypatch.setattr(p, '_inventory', race_inventory)
        with pytest.raises(WorkPrivacyBlocked, match='changed|drift'):
            p.preview_reconciliation()
        assert not (p.authority_dir / 'pending').exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize('limit', ['member', 'total'])
@windows_only
def test_inventory_bounds_refuse_before_reading_over_budget(legacy_enrolled, monkeypatch, limit):
    p, member = legacy_enrolled
    if limit == 'member':
        monkeypatch.setattr(privacy, '_MAX_INVENTORY_MEMBER_BYTES', member.stat().st_size - 1)
    else:
        (member.parent / 'extra.bin').write_bytes(b'extra')
        monkeypatch.setattr(privacy, '_MAX_INVENTORY_TOTAL_BYTES', member.stat().st_size)
    reads = []
    original = fs.WindowsQuarantineFile.read_chunk

    def read(opened, count):
        reads.append(count)
        return original(opened, count)

    monkeypatch.setattr(fs.WindowsQuarantineFile, 'read_chunk', read)
    with pytest.raises(WorkPrivacyBlocked, match='oversized'):
        p._inventory()
    assert sum(reads) <= (0 if limit == 'member' else member.stat().st_size)


@pytest.mark.parametrize('limit', ['member', 'total'])
@windows_only
def test_large_inventory_does_not_expand_staged_write_limits(legacy_enrolled, monkeypatch, limit):
    p, member = legacy_enrolled
    before = p.current()
    if limit == 'member':
        monkeypatch.setattr(privacy, '_MAX_MEMBER_BYTES', 3)
        changes = {'work_state/notes.md': b'four'}
    else:
        monkeypatch.setattr(privacy, '_MAX_TOTAL_BYTES', 5)
        changes = {'work_state/notes.md': b'four', 'work_state/new.md': b'four'}
    with pytest.raises(WorkPrivacyBlocked, match='staged'):
        p.publish(changes)
    assert p.current() == before and member.read_bytes() == b'old synthetic notes'
    assert not (p.authority_dir / 'pending').exists()


@pytest.mark.parametrize('limit', ['member', 'decoded', 'total'])
@windows_only
def test_pending_replay_retains_small_staged_byte_limits(legacy_enrolled, monkeypatch, limit):
    p, _ = legacy_enrolled
    before = p.current()
    changes = {'work_state/notes.md': b'four'}
    if limit == 'member':
        monkeypatch.setattr(privacy, '_MAX_MEMBER_BYTES', 3)
    elif limit == 'decoded':
        monkeypatch.setattr(privacy, '_MAX_MEMBER_BYTES', 5)
        changes['work_state/notes.md'] = b'sixsix'
    else:
        changes['work_state/new.md'] = b'four'
        monkeypatch.setattr(privacy, '_MAX_TOTAL_BYTES', 5)
    after = {**before, 'members': {**before['members'], **{
        name: {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in changes.items()}}}
    encoded = {name: base64.b64encode(raw).decode() for name, raw in changes.items()}
    with pytest.raises(WorkPrivacyBlocked, match='staged'):
        p._pending_members(before, after, encoded)


@pytest.mark.parametrize('limit', ['member', 'total'])
@windows_only
def test_authenticated_history_retains_inventory_size_bounds(legacy_enrolled, monkeypatch, limit):
    p, member = legacy_enrolled
    history = {key: value for key, value in p.current().items() if key != 'head'}
    monkeypatch.setattr(privacy, '_MAX_INVENTORY_MEMBER_BYTES' if limit == 'member'
                        else '_MAX_INVENTORY_TOTAL_BYTES', member.stat().st_size - 1)
    with pytest.raises(WorkPrivacyBlocked, match='digest|bound'):
        p._validate_history(history)


@pytest.mark.parametrize('piece', [b'', b'too much'])
def test_inventory_digest_refuses_incomplete_or_excess_chunks(piece):
    with pytest.raises(WorkPrivacyBlocked, match='changed'):
        privacy._hash_chunks(lambda _: piece, 1)


@pytest.mark.parametrize('change', ['grow', 'shrink'])
@windows_only
def test_inventory_rechecks_file_size_after_native_open(tmp_path, monkeypatch, change):
    member = tmp_path / 'synthetic.bin'
    member.write_bytes(b'initial')
    original = fs.WindowsQuarantineDirectory.open_file

    def open_file(parent, name, **kwargs):
        if name == member.name:
            with member.open('r+b') as out:
                out.truncate(8 if change == 'grow' else 1)
        return original(parent, name, **kwargs)

    monkeypatch.setattr(fs.WindowsQuarantineDirectory, 'open_file', open_file)
    with pytest.raises(WorkPrivacyBlocked, match='bound|changed'):
        privacy._member_digest(member, 7)


def test_inventory_refuses_missing_and_hardlinked_inputs(tmp_path):
    member = tmp_path / 'synthetic.bin'
    with pytest.raises(WorkPrivacyBlocked, match='unavailable'):
        privacy._member_digest(member, 10)
    member.write_bytes(b'synthetic')
    os.link(member, tmp_path / 'linked.bin')
    with pytest.raises(WorkPrivacyBlocked, match='unsafe'):
        privacy._member_digest(member, 10)


def test_portable_inventory_checks_exact_bytes_and_identity(tmp_path, monkeypatch):
    # Exercise portable hashing on Windows too; native POSIX no-follow evidence is separate.
    portable_os = SimpleNamespace(**vars(os))
    portable_os.name = 'posix'
    monkeypatch.setattr(privacy, 'os', portable_os)
    monkeypatch.setattr(privacy, '_posix_flag', lambda _: 0)
    member = tmp_path / 'synthetic.bin'
    member.write_bytes(b'synthetic bytes')
    expected = {'size': 15, 'sha256': hashlib.sha256(b'synthetic bytes').hexdigest()}
    assert privacy._member_digest(member, 15) == expected
    assert privacy._hash_chunks(lambda _: pytest.fail('empty file read'), 0) == {
        'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
    before = member.stat()
    fstat = portable_os.fstat
    calls = 0

    def mutate(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            member.write_bytes(b'changed content')
            os.utime(member, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000_000))
        return fstat(fd)

    monkeypatch.setattr(portable_os, 'fstat', mutate)
    with pytest.raises(WorkPrivacyBlocked, match='changed'):
        privacy._member_digest(member, 15)


@windows_only
def test_large_native_asset_round_trips_through_public_encrypted_recovery(legacy_enrolled, tmp_path):
    from datetime import UTC, datetime

    from catchup_core import checkpoint as ck
    from catchup_core.recovery_access import WorkRecoveryAccess

    p, member = legacy_enrolled
    asset = member.parent / 'synthetic.bin'
    size = (8 << 20) + 123
    with asset.open('wb') as out:
        out.truncate(size)
        out.flush()
        os.fsync(out.fileno())
    Path(str(asset) + ':synthetic').write_bytes(b'large asset stream')
    reconcile_reviewed(p)
    preview = p.preview_stream_history()
    p.upgrade_stream_history(reviewed=True, expected_digest=preview['inventory_sha256'])
    binding = p.bind_checkpoint()
    source = ck.create('work', p.roots, tmp_path / 'archives', CIPHER, {},
                       'synthetic passphrase long enough', now=datetime.now(UTC),
                       lock_path=tmp_path / 'checkpoint.lock', context={'work_binding': binding})
    manifest = ck.verify(source, CIPHER)
    entry = next(item for item in manifest['entries'] if item['path'] == 'work_state/synthetic.bin')
    assert entry['size'] == size and entry['sha256'] == binding['members'][entry['path']]['sha256']
    access = WorkRecoveryAccess(p.repo, p, CIPHER)
    access.authorize_checkpoint(manifest)
    dest = tmp_path / 'restored'
    ck.restore(source, dest, CIPHER, list(p.roots.values()), expected_profile='work',
               replay_privacy=access.replay, quarantine_factory=access.factory)
    store = access.open_store(dest, export=True)
    item = next(item for item in store.inventory() if item.get('path') == entry['path'])
    digest, recovered = hashlib.sha256(), 0
    for chunk in store.iter_member(item['member_id']):
        digest.update(chunk)
        recovered += len(chunk)
    assert recovered == size and digest.hexdigest() == entry['sha256']
    assert not (dest / 'work_state/synthetic.bin').exists()
    assert any(item['kind'] == 'stream' for item in store.inventory())
    assert asset.stat().st_size == size and Path(str(asset) + ':synthetic').read_bytes() == b'large asset stream'


@pytest.mark.parametrize('raw', [b'new', b'longer synthetic notes' * 12, b''])
@windows_only
def test_enrolled_update_preserves_identity_security_and_all_streams(enrolled, raw):
    p, member = enrolled
    before = p.current()
    binding = p.bind_checkpoint()
    metadata = native_metadata(member)
    result = p.publish({'work_state/notes.md': raw})
    assert result['generation'] == before['generation'] + 1
    assert member.read_bytes() == raw
    assert native_metadata(member) == metadata
    assert Path(str(member) + ':synthetic').read_bytes() == b'keep these stream bytes'
    assert Path(str(member) + ':empty').read_bytes() == b''
    assert result['streams'] == before['streams']
    assert result['members']['work_state/notes.md'] == {
        'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    file_streams = [key for key in binding['streams'] if 'notes.md' in key]
    assert p.denied_streams(binding, file_streams) == set(file_streams)
    assert not (p.authority_dir / 'pending').exists()


@windows_only
def test_enrolled_delete_invalidates_owned_streams_and_preserves_root_stream(enrolled):
    p, member = enrolled
    binding = p.bind_checkpoint()
    file_streams = {key for key in binding['streams'] if 'notes.md' in key}
    result = p.publish({'work_state/notes.md': None})
    assert not member.exists()
    assert 'work_state/notes.md' not in result['members']
    assert not file_streams.intersection(result['streams'])
    assert all(result['stream_invalidated'][key] == result['generation'] for key in file_streams)
    assert Path(str(member.parent) + ':root-stream').read_bytes() == b'root bytes'
    assert p.denied_streams(binding, list(file_streams)) == file_streams


@windows_only
def test_late_stream_is_preserved_and_keeps_publication_pending(enrolled):
    p, member = enrolled
    metadata = native_metadata(member)
    def add_stream(stage):
        if stage == 'member':
            Path(str(member) + ':late').write_bytes(b'late synthetic data')
    p.observe = add_stream
    with pytest.raises(WorkPrivacyBlocked, match='stream drift'):
        p.publish({'work_state/notes.md': b'authorized new primary'})
    assert member.read_bytes() == b'authorized new primary'
    assert native_metadata(member) == metadata
    assert Path(str(member) + ':synthetic').read_bytes() == b'keep these stream bytes'
    assert Path(str(member) + ':late').read_bytes() == b'late synthetic data'
    assert (p.authority_dir / 'pending').exists()
    with pytest.raises(WorkPrivacyBlocked):
        p.current()
    with pytest.raises(WorkPrivacyBlocked):
        p.recover_pending()


@windows_only
def test_native_update_handle_excludes_competing_primary_writers(enrolled):
    _, member = enrolled
    code = 'import sys;open(sys.argv[1],"r+b").write(b"other")'
    with fs.WindowsQuarantineDirectory.open(member.parent, owner_private=False) as parent:
        with parent.open_file(member.name, writable=True, private=False, deny_write=True):
            blocked = subprocess.run([sys.executable, '-c', code, str(member)],
                                     capture_output=True, timeout=20)
            assert blocked.returncode != 0
    allowed = subprocess.run([sys.executable, '-c', code, str(member)], capture_output=True, timeout=20)
    assert allowed.returncode == 0


@windows_only
def test_update_and_creation_publish_as_one_reviewed_generation(enrolled):
    p, member = enrolled
    before = p.current()
    result = p.publish({'work_state/notes.md': b'updated', 'work_state/new.md': b'new',
                        'work_state/absent.md': None})
    assert result['generation'] == before['generation'] + 1
    assert member.read_bytes() == b'updated'
    assert (member.parent / 'new.md').read_bytes() == b'new'
    assert result['streams'] == before['streams']


@windows_only
def test_unjournaled_primary_change_refuses_before_intent(enrolled):
    p, member = enrolled
    with p.transaction() as tx:
        member.write_bytes(b'outside writer')
        with pytest.raises(WorkPrivacyBlocked, match='drift'):
            tx.publish({'work_state/notes.md': b'new'})
    assert member.read_bytes() == b'outside writer'
    assert not (p.authority_dir / 'pending').exists()


@windows_only
def test_producer_read_modify_write_and_delete_follow_v2_history(enrolled):
    p, member = enrolled
    previous = wp.set_protocol_factory(lambda: p)
    try:
        def legacy():
            pytest.fail('enrolled producer used a legacy writer')
        wp.update_text(member, lambda text: text + '\nnew note', legacy=legacy)
        assert wp.decode(member.read_bytes()) == 'old synthetic notes\nnew note'
        assert Path(str(member) + ':synthetic').read_bytes() == b'keep these stream bytes'
        wp.publish({member: None}, legacy=legacy)
        assert not member.exists() and 'work_state/notes.md' not in p.current()['members']
    finally:
        wp.set_protocol_factory(previous)


@windows_only
def test_health_receipt_publication_preserves_work_authority_and_streams(enrolled_health_receipt):
    from lib.workiq_health import read_receipt, write_receipt

    p, member = enrolled_health_receipt
    before = p.current()
    binding = p.bind_checkpoint()
    metadata = native_metadata(member)
    write_receipt(member, available=True, auth_valid=True)
    after = p.current()
    assert after['generation'] == before['generation'] + 1
    assert read_receipt(member, max_age=86400)['available'] is True
    assert native_metadata(member) == metadata
    assert Path(str(member) + ':synthetic').read_bytes() == b'keep this stream'
    assert p.denied_members(binding, ['work_local/cache/health.json']) == {'work_local/cache/health.json'}
    assert not (p.authority_dir / 'pending').exists()


@windows_only
def test_health_receipt_refuses_when_work_authority_has_drift(enrolled_health_receipt):
    from lib.workiq_health import write_receipt

    p, member = enrolled_health_receipt
    member.write_bytes(b'unreviewed synthetic drift')
    with pytest.raises(wp.WorkPublicationBlocked):
        write_receipt(member, available=True, auth_valid=True)
    assert member.read_bytes() == b'unreviewed synthetic drift'
    assert not (p.authority_dir / 'pending').exists()


@windows_only
def test_health_receipt_refuses_if_enrollment_wins_legacy_race(health_receipt_protocol, monkeypatch):
    from lib.workiq_health import write_receipt

    p, member = health_receipt_protocol
    before = member.read_bytes()
    probe = wp._probe

    def enroll_after_probe():
        active = probe()
        if active is None:
            p.enroll(KEY, reviewed=True, installation_id='synthetic-installation')
        return active

    monkeypatch.setattr(wp, '_probe', enroll_after_probe)
    with pytest.raises(wp.WorkPublicationBlocked, match='enrollment changed'):
        write_receipt(member, available=True, auth_valid=True)
    assert member.read_bytes() == before
    assert p.current()['generation'] == 1


@windows_only
@pytest.mark.parametrize('write_fails', [False, True])
def test_legacy_health_receipt_write_and_failure(health_receipt_protocol, monkeypatch, write_fails):
    from lib.workiq_health import read_receipt, write_receipt

    p, member = health_receipt_protocol
    before = member.read_bytes()
    if write_fails:
        monkeypatch.setattr('lib.state_writer.write_atomic', lambda *_args: SimpleNamespace(success=False))
        with pytest.raises(OSError, match='could not be persisted'):
            write_receipt(member, available=True, auth_valid=True)
        assert member.read_bytes() == before
    else:
        write_receipt(member, available=True, auth_valid=True)
        assert read_receipt(member, max_age=86400)['available'] is True
    assert not p.is_enrolled()


@windows_only
@pytest.mark.parametrize('lock_name', ['work-privacy.lock', 'installation.lock'])
def test_legacy_health_receipt_respects_enrollment_and_capture_locks(health_receipt_protocol, lock_name):
    from catchup_core.session_lock import session_lock

    p, member = health_receipt_protocol
    before = member.read_bytes()
    code = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); '
            'from lib.workiq_health import write_receipt; '
            'write_receipt(Path(sys.argv[1]), available=True, auth_valid=True)')
    with session_lock(p.local / lock_name):
        child = subprocess.run([sys.executable, '-c', code, str(member),
                                str(Path(privacy.__file__).resolve().parents[1])],
                               capture_output=True, text=True, timeout=20)
    assert child.returncode != 0 and 'SessionBusy' in child.stderr
    assert member.read_bytes() == before
    assert not p.is_enrolled()


@windows_only
@pytest.mark.parametrize('entry', ['package', 'script'])
def test_preflight_reports_health_receipt_publication_refusal(enrolled_health_receipt, monkeypatch, entry):
    import importlib.util

    p, member = enrolled_health_receipt
    member.write_bytes(b'unreviewed synthetic drift')
    if entry == 'package':
        from preflight import integration_checks as checks
    else:
        name = '_preflight_receipt_regression'
        spec = importlib.util.spec_from_file_location(name, Path(privacy.__file__).resolve().parents[1] / 'preflight.py')
        assert spec is not None and spec.loader is not None
        checks = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, checks)
        spec.loader.exec_module(checks)

    def registry_unavailable(*_args):
        raise OSError('synthetic registry unavailable')

    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(HKEY_LOCAL_MACHINE=1, HKEY_CURRENT_USER=2,
                                                             OpenKey=registry_unavailable))
    monkeypatch.setattr('platform.system', lambda: 'Windows')
    monkeypatch.setattr('shutil.which', lambda *_args, **_kwargs: 'synthetic-npx')
    monkeypatch.setattr(subprocess, 'run', lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=0, stdout='synthetic identity', stderr=''))
    monkeypatch.setattr(checks, 'ARTHA_DIR', str(p.repo))
    monkeypatch.setattr(checks, 'WORKIQ_CACHE_FILE', str(member))
    monkeypatch.setattr(checks, '_SUBPROCESS_ENV', {'PATH': ''})
    result = checks.check_workiq()
    assert not result.passed and result.message == 'WorkIQ probe receipt could not be persisted'
    assert member.read_bytes() == b'unreviewed synthetic drift'
    assert not (p.authority_dir / 'pending').exists()


CHILD = r'''
import sys, threading
from pathlib import Path
from core_helpers import FileBackend, WORK_IDENTITY as IDENTITY
from catchup_core import work_privacy, quarantine_fs_windows as fs
base, phase = Path(sys.argv[1]), sys.argv[2]
def pause():
    (base/'ready').write_text('paused')
    threading.Event().wait()
p = work_privacy.WorkPrivacy(base/'repo',base/'local',{'work_state':base/'repo/state/work'},
    backend=FileBackend(base/'keys'),native_identity=lambda:dict(IDENTITY),
    observe=lambda stage:pause() if stage==phase else None)
if phase=='partial':
    original = fs.WindowsQuarantineFile.write_all
    def interrupted(opened, raw):
        if opened.name=='notes.md':
            original(opened,raw[:2]);opened.flush();pause()
        else:
            original(opened,raw)
    fs.WindowsQuarantineFile.write_all = interrupted
p.publish({'work_state/notes.md':None if sys.argv[3]=='delete' else b'replacement primary'})
'''


@pytest.mark.parametrize('phase', ['intent', 'partial', 'member', 'head', 'complete'])
@pytest.mark.parametrize('operation', ['update', 'delete'])
@pytest.mark.parametrize('history_fixture', ['legacy_enrolled', 'enrolled'])
@windows_only
def test_real_interruption_replays_only_exact_member_states(request, history_fixture, phase, operation):
    if operation == 'delete' and phase == 'partial':
        pytest.skip('native deletion has no partial-primary write step')
    p, member = request.getfixturevalue(history_fixture)
    base = p.repo.parent
    marker = base / 'ready'
    project = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        (str(project / 'scripts'), str(project / 'tests'), str(project / 'tests/catchup_core'))))
    child = subprocess.Popen([sys.executable, '-c', CHILD, str(base), phase, operation], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 45
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), 'synthetic mutation did not reach its interruption point'
        with pytest.raises((WorkPrivacyBlocked, SessionBusy)):
            p.current()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=15)
        _, errors = child.communicate(timeout=15)
        assert b'Traceback' not in errors
    assert (p.authority_dir / 'pending').exists()
    with pytest.raises(WorkPrivacyBlocked, match='pending'):
        p.current()
    if operation == 'update' or phase == 'intent':
        assert Path(str(member) + ':synthetic').read_bytes() == b'keep these stream bytes'
    if phase == 'partial':
        damaged = member.read_bytes()
        with pytest.raises(WorkPrivacyBlocked, match='drift'):
            p.recover_pending()
        assert member.read_bytes() == damaged
        assert (p.authority_dir / 'pending').exists()
    else:
        p.recover_pending()
        assert not (p.authority_dir / 'pending').exists()
        if operation == 'delete':
            assert not member.exists() and 'work_state/notes.md' not in p.current()['members']
            assert Path(str(member.parent) + ':root-stream').read_bytes() == b'root bytes'
        else:
            assert member.read_bytes() == b'replacement primary'
            assert p.current()['members']['work_state/notes.md']['size'] == 19
