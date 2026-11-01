"""OS privacy anchors must stay bounded while authenticated replay history grows."""
from __future__ import annotations

import hashlib

import pytest

from catchup_core.ledger import KeyringSealedStore, Ledger, LedgerLocked, SealedCheckpoint
from test_runtime import FakeKeyring
from test_user_surfaces import CIPHER, KEY


class WindowsSizedKeyring(FakeKeyring):
    def set_password(self, service, account, value):
        if len(value.encode('utf-16-le')) > 5 * 512:
            raise OSError('synthetic Windows credential byte limit')
        super().set_password(service, account, value)


def history(count):
    return {'tombstones': [{'tombstone_id': f'tmb_{index:06}', 'connector': 'synthetic',
        'account_scope': 'synthetic', 'upstream_id': f'upstream_{index:06}', 'content_hmac': None,
        'created_at': '2026-10-22T00:00:00Z', 'reason': 'SYNTHETIC-PRIVACY-REPLAY'} for index in range(count)],
        'consent': []}


def checkpoint(data, generation=1):
    digest = hashlib.sha256('\n'.join(sorted(row['tombstone_id'] for row in data['tombstones'])).encode()).hexdigest()
    return SealedCheckpoint(generation, digest, sum(row['revocation_version'] for row in data['consent']))


def store(tmp_path, backend=None):
    backend = backend or WindowsSizedKeyring()
    import re  # noqa: PLC0415
    ns = 'capacity-' + re.sub(r'[^A-Za-z0-9_.-]', '_', tmp_path.name)[:30]
    sealed = KeyringSealedStore(backend, ns, history_root=tmp_path / 'trusted-privacy')
    # The ledger supplies the encryption context; no host keyring or real runtime is opened.
    Ledger(tmp_path / 'synthetic.db', id_key=KEY, cipher=CIPHER, sealed=sealed)
    return sealed, backend


def test_large_privacy_history_fits_bounded_windows_os_anchor_and_replays_exactly(tmp_path):
    sealed, backend = store(tmp_path)
    data = history(200)
    sealed.history = lambda: data
    expected = checkpoint(data)
    sealed.write(expected)
    assert sealed.read() == expected
    assert sealed.replay() == data
    assert max(len(value.encode('utf-16-le')) for value in backend.store.values()) <= 5 * 512


def test_same_checkpoint_cannot_be_reinterpreted_as_a_different_privacy_history(tmp_path):
    sealed, _ = store(tmp_path)
    original = history(1)
    sealed.history = lambda: original
    sealed.write(checkpoint(original))
    sealed.history = lambda: history(0)
    with pytest.raises(LedgerLocked):
        sealed.write(checkpoint(history(0)))


def test_legacy_head_reads_without_mutation_then_migrates_with_exact_history(tmp_path):
    import json
    from dataclasses import asdict
    sealed, backend = store(tmp_path)
    original = history(1)
    legacy = json.dumps({**asdict(checkpoint(original)), 'history': original})
    backend.set_password(sealed._service, 'checkpoint', legacy)
    assert sealed.replay() == original and sealed.read() == checkpoint(original)
    assert not sealed._history_root.exists()
    assert backend.get_password(sealed._service, 'checkpoint') == legacy
    current = history(2)
    sealed.history = lambda: current
    sealed.write(checkpoint(current, 2))
    assert sealed.replay() == current
    assert 'history' not in json.loads(backend.get_password(sealed._service, 'checkpoint'))


@pytest.mark.parametrize('problem', ['missing', 'changed', 'reference', 'limit'])
def test_missing_changed_or_invalid_capsule_blocks_replay_without_mutating_head(tmp_path, monkeypatch, problem):
    import json
    from catchup_core import privacy_history
    sealed, backend = store(tmp_path)
    data = history(2)
    sealed.history = lambda: data
    sealed.write(checkpoint(data))
    head = backend.get_password(sealed._service, 'checkpoint')
    record = json.loads(head)
    path = sealed._capsules().directory / (record['capsule_sha256'] + '.ae1')
    if problem == 'missing':
        path.unlink()
    elif problem == 'changed':
        path.write_bytes(path.read_bytes() + b'changed')
    elif problem == 'reference':
        record['capsule_sha256'] = '../escape'
        backend.set_password(sealed._service, 'checkpoint', json.dumps(record))
        head = backend.get_password(sealed._service, 'checkpoint')
    else:
        monkeypatch.setattr(privacy_history, '_MAX_CAPSULE_BYTES', 1)
    with pytest.raises(LedgerLocked):
        sealed.replay()
    assert backend.get_password(sealed._service, 'checkpoint') == head


@pytest.mark.parametrize('problem', ['drop', 'rewrite'])
def test_new_checkpoint_cannot_lose_or_rewrite_a_prior_tombstone(tmp_path, problem):
    import copy
    sealed, _ = store(tmp_path)
    original = history(2)
    sealed.history = lambda: original
    sealed.write(checkpoint(original))
    current = history(1) if problem == 'drop' else copy.deepcopy(original)
    if problem == 'rewrite':
        current['tombstones'][0]['upstream_id'] = 'different-source'
    sealed.history = lambda: current
    with pytest.raises(LedgerLocked):
        sealed.write(checkpoint(current, 2))
    assert sealed.replay() == original


@pytest.mark.parametrize('problem', ['drop', 'unrevoke', 'scope', 'rollback', 'unsigned-revocation'])
def test_revoked_consent_cannot_be_lost_or_reinterpreted(tmp_path, problem):
    import copy
    sealed, _ = store(tmp_path)
    original = history(0)
    original['consent'] = [{'consent_id': 'con_original', 'subject_id': 'adult', 'scope': 'synthetic',
        'revocation_version': 1, 'revoked_at': '2026-10-22T00:00:00Z'}]
    sealed.history = lambda: original
    sealed.write(checkpoint(original))
    current = copy.deepcopy(original)
    if problem == 'drop':
        current['consent'][0]['consent_id'] = 'con_replacement'
    elif problem == 'unrevoke':
        current['consent'][0]['revoked_at'] = None
    elif problem == 'scope':
        current['consent'][0]['scope'] = 'different-scope'
    elif problem == 'unsigned-revocation':
        current['consent'][0]['revocation_version'] = 2
        current['consent'][0]['revoked_at'] = None
    else:
        current['consent'][0]['revocation_version'] = 0
    sealed.history = lambda: current
    with pytest.raises(LedgerLocked):
        sealed.write(checkpoint(current, 2))
    assert sealed.replay() == original


def test_newly_recorded_revocation_preserves_all_grant_fields(tmp_path):
    sealed, _ = store(tmp_path)
    original = history(0)
    original['consent'] = [{'consent_id': 'con_original', 'subject_id': 'adult', 'scope': 'synthetic',
        'revocation_version': 0, 'revoked_at': None}]
    sealed.history = lambda: original
    sealed.write(checkpoint(original))
    current = history(0)
    current['consent'] = [{**original['consent'][0], 'revocation_version': 1, 'revoked_at': '2026-10-22T00:00:00Z'}]
    sealed.history = lambda: current
    sealed.write(checkpoint(current, 2))
    assert sealed.replay() == current


@pytest.mark.parametrize('failure', ['reject', 'ack-lost', 'disk', 'no-persist'])
def test_failed_publication_never_erases_the_last_verified_history(tmp_path, monkeypatch, failure):
    from catchup_core import backup
    sealed, backend = store(tmp_path)
    previous = history(1)
    sealed.history = lambda: previous
    sealed.write(checkpoint(previous))
    current = history(2)
    sealed.history = lambda: current
    original_set = backend.set_password
    def fail_set(service, account, value):
        if failure == 'no-persist':
            return
        if failure == 'ack-lost':
            original_set(service, account, value)
        raise OSError('synthetic publication interruption')
    def fail_disk(*args, **kwargs):
        raise OSError('synthetic disk-full')
    if failure == 'disk':
        monkeypatch.setattr(backup, 'atomic_bytes', fail_disk)
    else:
        monkeypatch.setattr(backend, 'set_password', fail_set)
    with pytest.raises(LedgerLocked):
        sealed.write(checkpoint(current, 2))
    assert sealed.replay() == (current if failure == 'ack-lost' else previous)


def test_plaintext_never_enters_capsules_and_only_current_previous_are_retained(tmp_path):
    sealed, backend = store(tmp_path)
    for count in range(1, 5):
        data = history(count)
        sealed.history = lambda: data
        sealed.write(checkpoint(data, count))
    files = list(sealed._capsules().directory.glob('*.ae1'))
    assert len(files) == 2
    assert all(b'SYNTHETIC-PRIVACY-REPLAY' not in path.read_bytes() for path in files)
    assert all('SYNTHETIC-PRIVACY-REPLAY' not in value for value in backend.store.values())
    before = {path.name: path.read_bytes() for path in files}
    sealed.write(checkpoint(data, 4))  # Same checkpoint/history is idempotent.
    assert {path.name: path.read_bytes() for path in files} == before


def test_capsule_limit_refuses_publication_without_truncating_or_changing_the_head(tmp_path, monkeypatch):
    from catchup_core import privacy_history
    sealed, backend = store(tmp_path)
    data = history(2)
    sealed.history = lambda: data
    monkeypatch.setattr(privacy_history, '_MAX_HISTORY_BYTES', 64)
    with pytest.raises(LedgerLocked):
        sealed.write(checkpoint(data))
    assert backend.get_password(sealed._service, 'checkpoint') is None


@pytest.mark.parametrize('limit_delta', [-1, 0], ids=['over-limit', 'at-limit'])
def test_replay_plaintext_limit_precedes_json_parsing_without_mutation(tmp_path, monkeypatch, limit_delta):
    import json
    from catchup_core import privacy_history
    sealed, backend = store(tmp_path)
    data = history(2)
    data['consent'] = [{'consent_id': 'con_original', 'subject_id': 'adult', 'scope': 'synthetic',
        'revocation_version': 1, 'revoked_at': '2026-10-22T00:00:00Z'}]
    sealed.history = lambda: data
    expected = checkpoint(data)
    sealed.write(expected)
    assert sealed.replay() == data
    os_records = dict(backend.store)
    record = json.loads(backend.get_password(sealed._service, 'checkpoint'))
    capsules = sealed._capsules()
    capsule_bytes = {path.name: path.read_bytes() for path in capsules.directory.glob('*.ae1')}
    plain = CIPHER.decrypt(capsule_bytes[record['capsule_sha256'] + '.ae1'], aad=capsules._aad)
    original_loads = json.loads
    parsed_plaintext = []

    def track_loads(value, *args, **kwargs):
        if isinstance(value, bytes) and value == plain:
            parsed_plaintext.append(value)
        return original_loads(value, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(privacy_history, '_MAX_HISTORY_BYTES', len(plain) + limit_delta)
            patch.setattr(privacy_history.json, 'loads', track_loads)
            if limit_delta < 0:
                with pytest.raises(LedgerLocked):
                    sealed.replay()
                assert parsed_plaintext == []
            else:
                assert sealed.replay() == data
                assert parsed_plaintext == [plain]
        assert sealed.read() == expected
        assert sealed.replay() == data
    finally:
        assert backend.store == os_records
        assert {path.name: path.read_bytes() for path in capsules.directory.glob('*.ae1')} == capsule_bytes


class ProcessKeyring:
    """Synthetic persisted OS-record interface, shared by actual test processes only."""
    def __init__(self, path):
        self.path = path
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(path)) as db:
            with db:
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
        if len(value.encode('utf-16-le')) > 2560:
            raise OSError('synthetic OS record limit')
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (service, account, value))


_PROCESS = '''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
sys.path.insert(0,sys.argv[2])
from test_privacy_anchor_capacity import ProcessKeyring,store,history,checkpoint
from catchup_core.ledger import LedgerLocked
backend=ProcessKeyring(Path(sys.argv[3]))
sealed,_=store(Path(sys.argv[4]),backend)
data=history(1)
mode=sys.argv[5]
if mode=='race':
    data['tombstones'][0]['tombstone_id']='tmb_'+sys.argv[6]
else:
    publish=backend.set_password
    def interrupted(*args):
        if mode=='before':
            os._exit(23)
        publish(*args)
        os._exit(23)
    backend.set_password=interrupted
sealed.history=lambda:data
if mode=='race':
    print('ready',flush=True)
    sys.stdin.readline()
try:
    sealed.write(checkpoint(data,2))
    print('accepted',flush=True)
except LedgerLocked:
    print('refused',flush=True)
'''


def _process_args(tmp_path, database, mode, identity=''):
    import sys
    from pathlib import Path
    tests = Path(__file__).resolve().parent
    return [sys.executable, '-c', _PROCESS, str(tests.parents[1] / 'scripts'), str(tests),
            str(database), str(tmp_path), mode, identity]


@pytest.mark.parametrize('after_publication', [False, True])
def test_process_death_before_or_after_os_head_keeps_an_authenticated_replay(tmp_path, after_publication):
    import subprocess
    database = tmp_path / 'synthetic-os-records.db'
    sealed, _ = store(tmp_path, ProcessKeyring(database))
    original = history(0)
    sealed.history = lambda: original
    sealed.write(checkpoint(original))
    child = subprocess.run(_process_args(tmp_path, database, 'after' if after_publication else 'before'),
                           capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr.decode()
    assert sealed.replay() == (history(1) if after_publication else original)
    current = history(1)
    sealed.history = lambda: current
    sealed.write(checkpoint(current, 2))
    assert sealed.replay() == current
    assert len(list(sealed._capsules().directory.glob('*.ae1'))) <= (2 if after_publication else 3)


def test_competing_processes_cannot_reinterpret_the_same_generation(tmp_path):
    import subprocess
    database = tmp_path / 'synthetic-os-records.db'
    sealed, _ = store(tmp_path, ProcessKeyring(database))
    original = history(0)
    sealed.history = lambda: original
    sealed.write(checkpoint(original))
    children = []
    try:
        for identity in ('left', 'right'):
            children.append(subprocess.Popen(_process_args(tmp_path, database, 'race', identity),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for child in children:
            assert child.stdout.readline().strip() == 'ready'
        for child in children:
            child.stdin.write('publish\n')
            child.stdin.flush()
        results = []
        for child in children:
            out, err = child.communicate(timeout=15)
            assert child.returncode == 0, err
            results.append(out.strip())
        assert sorted(results) == ['accepted', 'refused']
        replay = sealed.replay()
        assert replay['tombstones'][0]['tombstone_id'] in {'tmb_left', 'tmb_right'}
        assert sealed.read() == checkpoint(replay, 2)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


@pytest.mark.parametrize('problem', ['shape', 'schema', 'id-type', 'id-bound', 'digest', 'permission', 'reference'])
def test_invalid_os_anchor_cannot_establish_a_restore_checkpoint(tmp_path, problem):
    import json
    from dataclasses import asdict
    sealed, backend = store(tmp_path)
    head = asdict(checkpoint(history(0)))
    if problem == 'shape':
        head = []
    elif problem == 'schema':
        head['schema'] = 'unsupported'
    elif problem == 'id-type':
        head['checkpoint_id'] = True
    elif problem == 'id-bound':
        head['checkpoint_id'] = 2**63
    elif problem == 'digest':
        head['tombstone_digest'] = 'not-a-digest'
    elif problem == 'permission':
        head['permission_generation'] = -1
    else:
        head['schema'] = 'artha.privacy-anchor.v2'
        head['capsule_sha256'] = '../escape'
    backend.set_password(sealed._service, 'checkpoint', json.dumps(head))
    with pytest.raises(LedgerLocked):
        sealed.read()
    assert not sealed._history_root.exists()


@pytest.mark.parametrize('problem', ['shape', 'group', 'row', 'duplicate', 'unknown', 'digest', 'revocation'])
def test_invalid_history_is_refused_without_publishing_partial_metadata(tmp_path, problem):
    sealed, backend = store(tmp_path)
    data = history(1)
    expected = checkpoint(data)
    if problem == 'shape':
        data = []
    elif problem == 'group':
        data['tombstones'] = {}
    elif problem == 'row':
        data['tombstones'] = [1]
    elif problem == 'duplicate':
        data['tombstones'] *= 2
    elif problem == 'unknown':
        data['unexpected'] = True
    elif problem == 'digest':
        data['tombstones'] = []
    else:
        data['consent'] = [{'consent_id': 'con_bad', 'revocation_version': True}]
    sealed.history = lambda: data
    with pytest.raises(LedgerLocked):
        sealed.write(expected)
    assert backend.get_password(sealed._service, 'checkpoint') is None


def test_read_only_empty_store_does_not_initialize_anything_and_write_requires_encryption(tmp_path):
    sealed = KeyringSealedStore(WindowsSizedKeyring(), 'no-key', history_root=tmp_path / 'privacy')
    assert sealed.read() is None and not sealed._history_root.exists()
    with pytest.raises(LedgerLocked):
        sealed.replay()
    with pytest.raises(LedgerLocked):
        sealed.write(checkpoint(history(0)))
    assert not list(sealed._history_root.rglob('*.ae1'))


def test_ciphertext_from_another_privacy_service_cannot_supply_replay_history(tmp_path):
    import json
    sealed, backend = store(tmp_path)
    data = history(1)
    sealed.history = lambda: data
    sealed.write(checkpoint(data))
    raw = backend.get_password(sealed._service, 'checkpoint')
    record = json.loads(raw)
    other = KeyringSealedStore(backend, 'other', cipher=CIPHER, history_root=tmp_path / 'trusted-privacy')
    source = sealed._capsules().directory / (record['capsule_sha256'] + '.ae1')
    destination = other._capsules().directory / source.name
    destination.parent.mkdir(parents=True)
    destination.write_bytes(source.read_bytes())
    backend.set_password(other._service, 'checkpoint', raw)
    with pytest.raises(LedgerLocked):
        other.replay()


def test_capsule_checkpoint_must_match_the_exact_os_anchor(tmp_path):
    import json
    sealed, backend = store(tmp_path)
    data = history(1)
    sealed.history = lambda: data
    sealed.write(checkpoint(data))
    head = json.loads(backend.get_password(sealed._service, 'checkpoint'))
    head['checkpoint_id'] += 1
    backend.set_password(sealed._service, 'checkpoint', json.dumps(head))
    with pytest.raises(LedgerLocked):
        sealed.replay()


def test_os_read_failure_does_not_look_like_a_new_empty_privacy_store(tmp_path, monkeypatch):
    sealed, backend = store(tmp_path)
    def unavailable(*args):
        raise OSError('synthetic locked OS store')
    monkeypatch.setattr(backend, 'get_password', unavailable)
    with pytest.raises(LedgerLocked):
        sealed.read()
    with pytest.raises(LedgerLocked):
        sealed.replay()


def test_cleanup_never_deletes_an_unattributed_encrypted_recovery_artifact(tmp_path):
    sealed, _ = store(tmp_path)
    original = history(1)
    sealed.history = lambda: original
    sealed.write(checkpoint(original))
    orphan = sealed._capsules().directory / ('f' * 64 + '.ae1')
    orphan.write_bytes(b'AE1-synthetic-unattributed-recovery-artifact')
    current = history(2)
    sealed.history = lambda: current
    sealed.write(checkpoint(current, 2))
    assert orphan.read_bytes() == b'AE1-synthetic-unattributed-recovery-artifact'
