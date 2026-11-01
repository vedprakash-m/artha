"""Signed retirement receipts for authority transfer; synthetic hosts, keys and checkpoints only."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from action_queue import ActionQueue
from catchup_core import authority_transfer as protocol
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger, PermissionDenied

NOW = '2026-10-24T12:00:00Z'
CIPHER = EnvelopeCipher(StaticKeyProvider({1: b't' * 32}))
KEY = Ed25519PrivateKey.from_bytes(b'k' * 32) if hasattr(Ed25519PrivateKey, 'from_bytes') else \
    Ed25519PrivateKey.from_private_bytes(b'k' * 32)
FINGERPRINT = protocol.key_fingerprint(KEY.public_key())


@pytest.fixture
def host(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True)
    (repo / 'state').mkdir()
    local = tmp_path / 'local'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setenv('ARTHA_LOCAL_DB', str(local / 'actions.db'))
    monkeypatch.setenv('ARTHA_HOST', 'old-host')
    checkpoint = tmp_path / 'checkpoint.ack'
    checkpoint.write_bytes(b'synthetic encrypted preservation input')
    ledger = Ledger(local / 'catchup_core/ledger.db', id_key=HmacKey(1, b'i' * 32), cipher=CIPHER,
                    owner_id='owner', clock=lambda: NOW).open()
    ledger.migrate()
    ledger.ensure_authority_host('old-host')
    queue = ActionQueue(repo)
    monkeypatch.setattr(protocol, '_verify_checkpoint', lambda *_: {'generation': 'ck_synthetic', 'profile': 'personal'})
    yield repo, ledger, queue, checkpoint
    queue.close()
    ledger.close()


def retire(host, **over):
    repo, ledger, _, checkpoint = host
    options = dict(actor='owner', new_host='new-host', checkpoint_path=checkpoint, signer=KEY)
    options.update(over)
    return protocol.issue_retirement_receipt(ledger, repo, **options)


def promote(host, receipt_path, **over):
    repo, ledger, _, checkpoint = host
    options = dict(actor='owner', checkpoint_path=checkpoint, workspace=repo, expected_generation=1,
                   retirement_receipt=receipt_path, former_host_key_fingerprint=FINGERPRINT)
    options.update(over)
    return ledger.transfer_authority(options.pop('new_host', 'new-host'), **options)


def saved(tmp_path, receipt, name='retirement.json'):
    path = tmp_path / name
    path.write_text(json.dumps(receipt))
    return path


def test_verified_transfer_records_the_receipt_digest_and_pinned_key(host, tmp_path):
    receipt = retire(host)
    path = saved(tmp_path, receipt)
    done = promote(host, path)
    assert done['proof_kind'] == protocol.VERIFIED_PROOF
    assert done['former_host_key_fingerprint'] == FINGERPRINT
    assert done['former_host_revoked_at'] is None
    assert host[1].authority_state()['generation'] == 2
    assert promote(host, path)['transfer_id'] == done['transfer_id']          # idempotent retry


def test_unverified_attestation_is_labelled_in_the_recorded_proof(host):
    repo, ledger, _, checkpoint = host
    done = ledger.transfer_authority('new-host', actor='owner', checkpoint_path=checkpoint, workspace=repo,
                                     expected_generation=1, former_host_quiesced=True)
    assert done['proof_kind'].startswith('unverified_owner_attestation')
    assert done['retirement_receipt_sha256'] is None and done['former_host_key_fingerprint'] is None


def refused(host, tmp_path, mutate, reason, **over):
    receipt = mutate(retire(host))
    with pytest.raises(PermissionDenied, match=reason):
        promote(host, saved(tmp_path, receipt, 'bad.json'), **over)
    assert host[1].authority_state()['generation'] == 1
    assert host[2].authority_state()['generation'] == 1


def resign(receipt, key=KEY, **changes):
    receipt = {**receipt, **changes}
    receipt.pop('signature', None)
    receipt['signature'] = base64.b64encode(key.sign(protocol._signed_bytes(receipt))).decode()
    return receipt


def test_forged_signature_is_refused(host, tmp_path):
    refused(host, tmp_path, lambda r: {**r, 'signature': base64.b64encode(b'x' * 64).decode()}, 'signature is invalid')


def test_receipt_edited_after_signing_is_refused(host, tmp_path):
    refused(host, tmp_path, lambda r: {**r, 'new_host': 'other-host'}, 'signature is invalid', new_host='other-host')


def test_receipt_signed_by_an_unpinned_key_is_refused(host, tmp_path):
    attacker = Ed25519PrivateKey.from_private_bytes(b'a' * 32)
    def forged(r):
        public = base64.b64encode(attacker.public_key().public_bytes_raw()).decode()
        return resign(r, attacker, public_key=public)
    refused(host, tmp_path, forged, 'not the pinned former host key')


def test_wrong_epoch_receipt_is_refused(host, tmp_path):
    refused(host, tmp_path, lambda r: resign(r, former_generation=2), 'another authority epoch')


def test_wrong_profile_receipt_is_refused(host, tmp_path):
    refused(host, tmp_path, lambda r: resign(r, profile='work'), 'another profile')


def test_receipt_for_another_target_or_checkpoint_or_host_is_refused(host, tmp_path):
    refused(host, tmp_path, lambda r: resign(r, new_host='elsewhere'), 'another new host')
    refused(host, tmp_path, lambda r: resign(r, checkpoint_sha256='0' * 64), 'another checkpoint')
    refused(host, tmp_path, lambda r: resign(r, former_host='third-host'), 'not the current authority host')


def test_replayed_receipt_cannot_serve_a_later_epoch(host, tmp_path):
    path = saved(tmp_path, retire(host))
    promote(host, path)
    repo, ledger, queue, checkpoint = host
    ledger._clock = lambda: '2026-10-25T12:00:00Z'
    with pytest.raises(PermissionDenied):
        ledger.transfer_authority('third-host', actor='owner', checkpoint_path=checkpoint, workspace=repo,
                                  expected_generation=2, retirement_receipt=path,
                                  former_host_key_fingerprint=FINGERPRINT)
    assert ledger.authority_state()['host'] == 'new-host'


@pytest.mark.parametrize('raw', [b'', b'[]', b'{"schema": "x"}', b'{"a":1,"a":2}', b'\xff', b'x' * 20000])
def test_malformed_or_oversized_receipts_are_refused(host, tmp_path, raw):
    path = tmp_path / 'junk.json'
    path.write_bytes(raw)
    with pytest.raises(PermissionDenied):
        promote(host, path)
    assert host[1].authority_state()['generation'] == 1


def test_bad_key_material_is_refused(host, tmp_path):
    refused(host, tmp_path, lambda r: {**r, 'public_key': 'not base64!'}, 'malformed')
    refused(host, tmp_path, lambda r: {**r, 'former_generation': True}, 'malformed')


def test_receipt_needs_a_pinned_fingerprint_and_excludes_attestation(host, tmp_path):
    path = saved(tmp_path, retire(host))
    with pytest.raises(PermissionDenied, match='fingerprint'):
        promote(host, path, former_host_key_fingerprint=None)
    with pytest.raises(PermissionDenied, match='not both'):
        promote(host, path, former_host_quiesced=True)
    with pytest.raises(PermissionDenied, match='cannot be read'):
        promote(host, tmp_path / 'missing.json')


def test_retiring_requires_owner_active_host_and_no_executing_action(host, tmp_path, monkeypatch):
    with pytest.raises(PermissionDenied, match='local owner'):
        retire(host, actor='other')
    with pytest.raises(ValueError, match='new host'):
        retire(host, new_host=' ')
    with pytest.raises(PermissionDenied, match='cannot be read'):
        retire(host, checkpoint_path=tmp_path / 'absent')
    monkeypatch.setenv('ARTHA_HOST', 'someone-else')
    with pytest.raises(PermissionDenied, match='active authority host'):
        retire(host)
    monkeypatch.setenv('ARTHA_HOST', 'old-host')
    queue = host[2]
    queue._conn.execute('INSERT INTO actions(id,created_at,updated_at,action_type,domain,title,parameters,status) '
                        'VALUES(?,?,?,?,?,?,?,?)', ('a', NOW, NOW, 'reminder_create', 'kids_school', 'a', '{}',
                                                    'executing'))
    queue._conn.commit()
    with pytest.raises(PermissionDenied, match='executing'):
        retire(host)
    assert not Path(str(host[2]._conn.execute('PRAGMA database_list').fetchone()[2]) + '.retired').exists()


def test_retiring_twice_returns_the_same_receipt_but_a_different_transfer_is_refused(host):
    first = retire(host)
    assert retire(host) == first
    with pytest.raises(PermissionDenied, match='different transfer'):
        retire(host, new_host='another')


def test_retired_host_cannot_dispatch_in_a_separate_process(host):
    repo, _, queue, _ = host
    protocol.require_dispatch_authority(queue._conn, host='old-host')            # before: allowed
    retire(host)
    code = ('import sys;sys.path.insert(0,%r)\n'
            'from action_queue import ActionQueue\nfrom catchup_core import authority_transfer as p\n'
            'from pathlib import Path\nq=ActionQueue(Path(sys.argv[1]))\n'
            'try:\n    p.require_dispatch_authority(q._conn, host="old-host")\n'
            'except ValueError as e:\n    print(e); sys.exit(3)\n') % str(Path(protocol.__file__).parent.parent)
    env = {**__import__('os').environ, 'ARTHA_LOCAL_DIR': str(host[1]._path.parent.parent),
           'ARTHA_LOCAL_DB': str(host[1]._path.parent.parent / 'actions.db')}
    done = subprocess.run([sys.executable, '-c', code, str(repo)], capture_output=True, env=env, timeout=30)
    assert done.returncode == 3, done.stderr
    assert b'issued a retirement receipt' in done.stdout


def test_default_signer_is_created_once_in_the_credential_store(host, monkeypatch):
    class Backend:
        def __init__(self):
            self.items = {}
        def get_password(self, service, account):
            return self.items.get((service, account))
        def set_password(self, service, account, value):
            self.items[(service, account)] = value
    backend = Backend()
    import catchup_core.crypto as crypto
    monkeypatch.setattr(crypto, 'system_keyring', lambda: backend)
    first = protocol._keyring_signer()
    again = protocol._keyring_signer()
    assert protocol.key_fingerprint(first.public_key()) == protocol.key_fingerprint(again.public_key())
    assert list(backend.items) == [('artha-retirement-key', 'host')]
    assert retire(host, signer=None)['public_key']


def test_an_earlier_owner_pause_survives_transfer_and_resume(host, monkeypatch):
    repo, ledger, _, checkpoint = host
    pause = repo / 'config/actions.paused'
    pause.write_text('owner paused earlier\n')
    ledger.transfer_authority('new-host', actor='owner', checkpoint_path=checkpoint, workspace=repo,
                              expected_generation=1, former_host_quiesced=True)
    assert pause.read_text() == 'owner paused earlier\n'
    monkeypatch.setenv('ARTHA_HOST', 'new-host')
    protocol.resume_effects(ledger, repo, actor='owner')
    assert pause.exists() and not (repo / 'config/actions.transfer-pause.json').exists()


def test_a_pause_added_only_by_the_transfer_is_removed_on_resume(host, monkeypatch):
    repo, ledger, _, checkpoint = host
    ledger.transfer_authority('new-host', actor='owner', checkpoint_path=checkpoint, workspace=repo,
                              expected_generation=1, former_host_quiesced=True)
    assert (repo / 'config/actions.paused').exists()
    monkeypatch.setenv('ARTHA_HOST', 'new-host')
    protocol.resume_effects(ledger, repo, actor='owner')
    assert not (repo / 'config/actions.paused').exists()


def test_retire_command_writes_the_receipt_and_prints_the_key_fingerprint(host, tmp_path, monkeypatch):
    from argparse import Namespace
    from types import SimpleNamespace

    from catchup_core import commands
    monkeypatch.setattr(protocol, '_keyring_signer', lambda: KEY)
    out = tmp_path / 'out' / 'retirement.json'
    deps = SimpleNamespace(ledger=host[1], viewer='owner')
    result = commands._retire_authority(
        Namespace(new_host='new-host', checkpoint=str(host[3]), output=str(out)), deps, host[0])
    assert result['former_host_key_fingerprint'] == FINGERPRINT
    assert promote(host, out)['proof_kind'] == protocol.VERIFIED_PROOF


def test_retry_cannot_change_the_pinned_key_or_profile(host, tmp_path, monkeypatch):
    path = saved(tmp_path, retire(host))
    monkeypatch.setattr(protocol, '_phase', lambda phase: (_ for _ in ()).throw(RuntimeError('stop'))
                        if phase == 'queue_prepared' else None)
    with pytest.raises(RuntimeError):
        promote(host, path)
    monkeypatch.setattr(protocol, '_phase', lambda _: None)
    for override in ({'former_host_key_fingerprint':'0' * 64}, {'profile':'work'}):
        with pytest.raises(PermissionDenied, match='personal' if 'profile' in override else 'pending'):
            promote(host, path, **override)
    assert host[1].authority_state()['generation'] == 1


def test_retirement_requires_a_verified_checkpoint_and_a_distinct_target(host, monkeypatch):
    def invalid(*_):
        raise PermissionDenied('checkpoint invalid')
    monkeypatch.setattr(protocol, '_verify_checkpoint', invalid)
    with pytest.raises(PermissionDenied, match='checkpoint invalid'):
        retire(host)
    assert not protocol.retired_marker(host[2]._conn).exists()
    assert not (host[0] / 'config/actions.paused').exists()
    monkeypatch.setattr(protocol, '_verify_checkpoint', lambda *_: {'generation':'ck_synthetic', 'profile':'personal'})
    with pytest.raises(PermissionDenied, match='distinct'):
        retire(host, new_host='old-host')


def test_malformed_pause_provenance_cannot_remove_an_owner_pause(host, monkeypatch):
    repo, ledger, _, checkpoint = host
    ledger.transfer_authority('new-host',actor='owner',checkpoint_path=checkpoint,workspace=repo,
                              expected_generation=1,former_host_quiesced=True)
    (repo / 'config/actions.transfer-pause.json').write_text('malformed')
    monkeypatch.setenv('ARTHA_HOST','new-host')
    with pytest.raises(PermissionDenied, match='pause'):
        protocol.resume_effects(ledger,repo,actor='owner')
    assert (repo / 'config/actions.paused').exists()


def test_verified_receipt_read_is_bounded_before_parsing(host, tmp_path, monkeypatch):
    path = tmp_path / 'oversized.json'
    path.write_bytes(b'X' * 50000)
    original = Path.open
    class Bounded:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self,*_):
            self.handle.close()
        def read(self, count=-1):
            assert 0 <= count <= protocol._RETIREMENT_MAX_BYTES + 1
            return self.handle.read(count)
    def guarded(self,*args,**kwargs):
        opened = original(self,*args,**kwargs)
        return Bounded(opened) if self == path else opened
    monkeypatch.setattr(Path,'open',guarded)
    with pytest.raises(PermissionDenied,match='too large'):
        promote(host,path)


def test_retirement_serializes_with_a_real_dispatch_claim(host, monkeypatch):
    queue_path = host[2]._db_path
    source = str(Path(protocol.__file__).parent.parent)
    code = ('import sqlite3,sys;sys.path.insert(0,sys.argv[2])\n'
            'from catchup_core.authority_transfer import require_dispatch_authority\n'
            'db=sqlite3.connect(sys.argv[1],timeout=.1)\n'
            'try:\n db.execute("BEGIN IMMEDIATE"); require_dispatch_authority(db,host="old-host")\n'
            'except (ValueError,sqlite3.OperationalError) as e:\n print(e);sys.exit(3)\n')
    checked = []
    def boundary(phase):
        if phase != 'retirement_quiesced':
            return
        claimant = subprocess.run([sys.executable,'-c',code,str(queue_path),source],
                                  capture_output=True,timeout=10)
        assert claimant.returncode == 3, claimant.stderr
        assert b'locked' in claimant.stdout
        checked.append(True)
    monkeypatch.setattr(protocol,'_phase',boundary)
    retire(host)
    assert checked == [True]
    after = subprocess.run([sys.executable,'-c',code,str(queue_path),source],capture_output=True,timeout=10)
    assert after.returncode == 3 and b'issued a retirement receipt' in after.stdout


@pytest.mark.skipif(__import__('os').name == 'nt',reason='POSIX SIGKILL process-death proof')
@pytest.mark.parametrize('phase',['retirement_quiesced','retirement_paused','retirement_marker_persisted','retirement_persisted'])
def test_real_process_death_during_retirement_keeps_a_recoverable_fence(host, phase):
    import signal
    source = str(Path(protocol.__file__).parent.parent)
    code = '''
import os,signal,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from catchup_core import authority_transfer as p
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
p._verify_checkpoint = lambda *_: {'generation':'ck_synthetic', 'profile':'personal'}
def boundary(phase):
    if phase == sys.argv[5]: os.kill(os.getpid(),signal.SIGKILL)
p._phase = boundary
with Ledger(Path(sys.argv[3]),id_key=HmacKey(1,b'i'*32),cipher=EnvelopeCipher(StaticKeyProvider({1:b't'*32})),
            owner_id='owner',clock=lambda:'2026-10-24T12:00:00Z') as ledger:
    p.issue_retirement_receipt(ledger,Path(sys.argv[2]),actor='owner',new_host='new-host',
                              checkpoint_path=Path(sys.argv[4]),signer=Ed25519PrivateKey.from_private_bytes(b'k'*32))
'''
    dead = subprocess.run([sys.executable,'-c',code,source,str(host[0]),str(host[1]._path),str(host[3]),phase],
                          capture_output=True,timeout=20)
    assert dead.returncode == -signal.SIGKILL, dead.stderr
    if phase == 'retirement_persisted':
        with pytest.raises(ValueError,match='issued a retirement receipt'):
            protocol.require_dispatch_authority(host[2]._conn,host='old-host')
    recovered = retire(host)
    assert recovered == retire(host)
    with pytest.raises(ValueError,match='issued a retirement receipt'):
        protocol.require_dispatch_authority(host[2]._conn,host='old-host')


def test_retired_source_and_independent_promoted_installation_remain_separate(host,tmp_path,monkeypatch):
    import sqlite3
    from contextlib import closing
    repo, ledger, queue, checkpoint = host
    target_repo, target_local = tmp_path/'target-repo', tmp_path/'target-local'
    (target_repo/'config').mkdir(parents=True)
    (target_repo/'state').mkdir()
    (target_local/'catchup_core').mkdir(parents=True)
    ledger_path, queue_path = target_local/'catchup_core/ledger.db', target_local/'actions.db'
    with closing(sqlite3.connect(ledger_path)) as db:
        ledger.conn.backup(db)
    with closing(sqlite3.connect(queue_path)) as db:
        queue._conn.backup(db)
    receipt = saved(tmp_path,retire(host))
    monkeypatch.setenv('ARTHA_LOCAL_DIR',str(target_local))
    monkeypatch.setenv('ARTHA_LOCAL_DB',str(queue_path))
    monkeypatch.setenv('ARTHA_HOST','new-host')
    with Ledger(ledger_path,id_key=HmacKey(1,b'i'*32),cipher=CIPHER,owner_id='owner',clock=lambda:NOW) as target:
        target.transfer_authority('new-host',actor='owner',checkpoint_path=checkpoint,workspace=target_repo,
            expected_generation=1,retirement_receipt=receipt,former_host_key_fingerprint=FINGERPRINT)
        protocol.resume_effects(target,target_repo,actor='owner')
    with closing(sqlite3.connect(queue_path)) as target_queue:
        protocol.require_dispatch_authority(target_queue,host='new-host')
    with pytest.raises(ValueError,match='issued a retirement receipt'):
        protocol.require_dispatch_authority(queue._conn,host='old-host')


def test_pause_added_by_owner_after_transfer_remains_paused(host,monkeypatch):
    repo,ledger,_,checkpoint=host
    ledger.transfer_authority('new-host',actor='owner',checkpoint_path=checkpoint,workspace=repo,
                              expected_generation=1,former_host_quiesced=True)
    pause=repo/'config/actions.paused'
    pause.write_text('Pause added by owner during cutover review.\n')
    monkeypatch.setenv('ARTHA_HOST','new-host')
    protocol.resume_effects(ledger,repo,actor='owner')
    assert pause.read_text() == 'Pause added by owner during cutover review.\n'


def test_losing_retirement_marker_cannot_reactivate_the_former_host(host):
    retire(host)
    protocol.retired_marker(host[2]._conn).unlink()
    with pytest.raises(ValueError,match='retir'):
        protocol.require_dispatch_authority(host[2]._conn,host='old-host')
    assert retire(host)['former_host'] == 'old-host'
    assert protocol.retired_marker(host[2]._conn).exists()


def test_dangling_retirement_marker_cannot_authorize_dispatch(host,tmp_path):
    marker=protocol.retired_marker(host[2]._conn)
    marker.symlink_to(tmp_path/'missing-retirement')
    with pytest.raises(ValueError,match='retir'):
        protocol.require_dispatch_authority(host[2]._conn,host='old-host')


def test_work_content_checkpoint_cannot_promote_personal_authority(host,monkeypatch):
    repo,ledger,queue,checkpoint=host
    with pytest.raises(PermissionDenied,match='personal'):
        retire(host,profile='work')
    with pytest.raises(PermissionDenied,match='personal'):
        ledger.transfer_authority('new-host',actor='owner',checkpoint_path=checkpoint,workspace=repo,
                                  expected_generation=1,former_host_quiesced=True,profile='work')
    monkeypatch.setattr(protocol,'_verify_checkpoint',lambda *_: {'generation':'work-checkpoint','profile':'work'})
    with pytest.raises(PermissionDenied,match='personal'):
        retire(host)
    with pytest.raises(PermissionDenied,match='personal'):
        ledger.transfer_authority('new-host',actor='owner',checkpoint_path=checkpoint,workspace=repo,
                                  expected_generation=1,former_host_quiesced=True)
    assert ledger.authority_state()['generation'] == queue.authority_state()['generation'] == 1
    assert not (repo/'config/actions.paused').exists()
