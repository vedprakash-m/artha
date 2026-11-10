"""Native enrolled Work updates preserve the existing file and its streams."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_work_privacy import IDENTITY, KEY

from catchup_core import quarantine_fs_windows as fs
from catchup_core import work_privacy as privacy
from catchup_core.session_lock import SessionBusy
from catchup_core.work_privacy import WorkPrivacy, WorkPrivacyBlocked
from lib import work_publication as wp

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native Windows Work publication')


class FileBackend:
    """Synthetic credentials shared only by this test's parent and child."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(exist_ok=True)

    def _path(self, service, account):
        return self.directory / hashlib.sha256((service + '\0' + account).encode()).hexdigest()

    def get_password(self, service, account):
        path = self._path(service, account)
        return path.read_text() if path.exists() else None

    def set_password(self, service, account, value):
        self._path(service, account).write_text(value)


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


def native_metadata(member):
    with fs.WindowsQuarantineDirectory.open(member.parent, owner_private=False) as parent:
        with parent.open_file(member.name, private=False) as opened:
            return opened.identity, opened._native.security_bytes(opened.handle)


@pytest.mark.parametrize('raw', [b'new', b'longer synthetic notes' * 12, b''])
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


@pytest.mark.parametrize('raw', [b'new', b'longer synthetic notes' * 12, b''])
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


def test_update_and_creation_publish_as_one_reviewed_generation(enrolled):
    p, member = enrolled
    before = p.current()
    result = p.publish({'work_state/notes.md': b'updated', 'work_state/new.md': b'new',
                        'work_state/absent.md': None})
    assert result['generation'] == before['generation'] + 1
    assert member.read_bytes() == b'updated'
    assert (member.parent / 'new.md').read_bytes() == b'new'
    assert result['streams'] == before['streams']


def test_unjournaled_primary_change_refuses_before_intent(enrolled):
    p, member = enrolled
    with p.transaction() as tx:
        member.write_bytes(b'outside writer')
        with pytest.raises(WorkPrivacyBlocked, match='drift'):
            tx.publish({'work_state/notes.md': b'new'})
    assert member.read_bytes() == b'outside writer'
    assert not (p.authority_dir / 'pending').exists()


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


CHILD = r'''
import sys, threading
from pathlib import Path
from test_work_member_mutations import FileBackend, IDENTITY
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
