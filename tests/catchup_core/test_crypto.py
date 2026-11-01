from __future__ import annotations

import pytest

from catchup_core import crypto

K1 = b"\x01" * 32
K2 = b"\x02" * 32


def cipher(keys=None, current=None):
    return crypto.EnvelopeCipher(crypto.StaticKeyProvider(keys or {1: K1}, current))


def test_round_trip_bytes_and_json():
    c = cipher()
    env, ver = c.encrypt(b"secret", b"fact:1")
    assert ver == 1 and env.startswith(b"AE1") and b"secret" not in env
    assert c.decrypt(env, b"fact:1") == b"secret"
    text, ver = c.encrypt_json({"amount": 120, "who": "x"}, b"a")
    assert c.decrypt_json(text, b"a") == {"amount": 120, "who": "x"}


def test_nonce_is_fresh_each_time():
    c = cipher()
    assert c.encrypt(b"same")[0] != c.encrypt(b"same")[0]


def test_aad_binds_ciphertext_to_its_record():
    c = cipher()
    env, _ = c.encrypt(b"v", b"fact:1")
    with pytest.raises(crypto.DecryptionError):
        c.decrypt(env, b"fact:2")


def test_tamper_detected():
    c = cipher()
    env = bytearray(c.encrypt(b"value")[0])
    env[-1] ^= 1
    with pytest.raises(crypto.DecryptionError):
        c.decrypt(bytes(env))


@pytest.mark.parametrize("blob", [b"", b"short", b"XXX" + b"\x00" * 40])
def test_malformed_envelope(blob):
    with pytest.raises(crypto.DecryptionError):
        cipher().decrypt(blob)


def test_bad_base64_json():
    with pytest.raises(crypto.DecryptionError):
        cipher().decrypt_json("%%%not-base64%%%")


def test_old_key_version_still_decrypts_after_rotation():
    old = cipher({1: K1})
    env, _ = old.encrypt(b"history")
    rotated = cipher({1: K1, 2: K2})
    assert rotated.encrypt(b"x")[1] == 2
    assert rotated.decrypt(env) == b"history"


def test_missing_key_version_is_unavailable_not_plaintext():
    env, _ = cipher({1: K1}).encrypt(b"v")
    with pytest.raises(crypto.KeyUnavailableError):
        cipher({2: K2}).decrypt(env)


def test_static_provider_validation():
    with pytest.raises(ValueError):
        crypto.StaticKeyProvider({})
    with pytest.raises(ValueError):
        crypto.StaticKeyProvider({1: b"short"})
    with pytest.raises(ValueError):
        crypto.StaticKeyProvider({1: K1}, current=5)


class FakeKeyring:
    def __init__(self, fail=False, drop_writes=False):
        self.store, self.fail, self.drop = {}, fail, drop_writes

    def get_password(self, service, name):
        if self.fail:
            raise RuntimeError("locked")
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.fail:
            raise RuntimeError("locked")
        if not self.drop:
            self.store[(service, name)] = value


def test_keyring_provider_creates_and_reuses_key():
    kr = FakeKeyring()
    p = crypto.KeyringKeyProvider(backend=kr)
    assert p.current_version() == 1
    k = p.key(1)
    assert len(k) == 32
    assert crypto.KeyringKeyProvider(backend=kr).key(1) == k       # persisted


def test_keyring_rotation_keeps_old_versions():
    kr = FakeKeyring()
    p = crypto.KeyringKeyProvider(backend=kr)
    p.current_version()
    old = p.key(1)
    assert p.rotate() == 2
    assert p.current_version() == 2
    assert p.key(1) == old and len(p.key(2)) == 32 and p.key(2) != old


def test_keyring_failure_never_degrades_to_plaintext():
    p = crypto.KeyringKeyProvider(backend=FakeKeyring(fail=True))
    with pytest.raises(crypto.KeyUnavailableError):
        p.current_version()
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.EnvelopeCipher(p).encrypt(b"x")


def test_keyring_that_drops_writes_is_detected():
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.KeyringKeyProvider(backend=FakeKeyring(drop_writes=True)).current_version()


def test_keyring_corrupt_records():
    kr = FakeKeyring()
    kr.store[("artha-ledger", "current_version")] = "abc"
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.KeyringKeyProvider(backend=kr).current_version()
    kr.store[("artha-ledger", "key.1")] = "!!!"
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.KeyringKeyProvider(backend=kr).key(1)
    import base64
    kr.store[("artha-ledger", "key.1")] = base64.b64encode(b"short").decode()
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.KeyringKeyProvider(backend=kr).key(1)
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.KeyringKeyProvider(backend=kr).key(9)


def test_keyring_provider_uses_platform_backend_by_default(monkeypatch):
    import keyring  # noqa: PLC0415
    class PlatformKeyring(FakeKeyring):
        pass
    PlatformKeyring.__module__ = 'keyring.backends.macOS'
    monkeypatch.setattr(keyring, 'get_keyring', lambda: PlatformKeyring())
    p = crypto.KeyringKeyProvider(service="artha-test")
    assert p.current_version() == 1 and len(p.key(1)) == 32


@pytest.mark.parametrize('module', ['keyring.backends.null', 'keyrings.alt.file', 'unknown.backend'])
def test_default_key_provider_refuses_non_platform_backends(monkeypatch, module):
    import keyring
    class UnsafeBackend(FakeKeyring):
        def get_password(self, *args):
            pytest.fail('unapproved backend must never receive a read')
    UnsafeBackend.__module__ = module
    monkeypatch.setattr(keyring, 'get_keyring', lambda: UnsafeBackend())
    with pytest.raises(crypto.KeyUnavailableError, match='OS credential-store backend'):
        crypto.KeyringKeyProvider().current_version()


def test_chainer_selects_only_the_highest_priority_platform_backend(monkeypatch):
    import keyring
    class PlatformKeyring(FakeKeyring):
        pass
    class Chain:
        backends = [FakeKeyring(), PlatformKeyring(), PlatformKeyring()]
    PlatformKeyring.__module__ = 'keyring.backends.SecretService'
    Chain.__module__ = 'keyring.backends.chainer'
    monkeypatch.setattr(keyring, 'get_keyring', Chain)
    assert crypto.system_keyring() is Chain.backends[1]
    Chain.backends = [FakeKeyring()]
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.system_keyring()


def test_backend_discovery_errors_do_not_disclose_details(monkeypatch):
    import keyring
    def unavailable():
        raise RuntimeError('private path or backend detail')
    monkeypatch.setattr(keyring, 'get_keyring', unavailable)
    with pytest.raises(crypto.KeyUnavailableError) as error:
        crypto.system_keyring()
    assert 'private' not in str(error.value)


def test_keyring_set_failure_is_unavailable():
    class ReadOnly(FakeKeyring):
        def get_password(self, service, name):
            return None

        def set_password(self, service, name, value):
            raise RuntimeError("read-only")
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.KeyringKeyProvider(backend=ReadOnly()).current_version()


def test_independent_first_use_cannot_overwrite_an_acknowledged_data_key(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'unit'))
    from test_credentials import SQLiteKeyring
    backend = SQLiteKeyring(tmp_path / 'fake-keyring.db')
    code = r'''
import sys,time
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from test_credentials import SQLiteKeyring
from catchup_core.crypto import KeyringKeyProvider,EnvelopeCipher
backend=SQLiteKeyring(sys.argv[3])
root,label=Path(sys.argv[4]),sys.argv[5]
read=backend.get_password
first=True
def stale_read(service,name):
 global first
 value=read(service,name)
 if name=='current_version' and first:
  first=False
  (root/('ready-'+label)).touch()
  until=time.monotonic()+10
  while not (root/('release-'+label)).exists():
   if time.monotonic()>until:raise RuntimeError('test synchronization expired')
   time.sleep(.01)
 return value
backend.get_password=stale_read
cipher=EnvelopeCipher(KeyringKeyProvider('synthetic-key-race',backend=backend))
(root/('ack-'+label)).write_bytes(cipher.encrypt(('synthetic-'+label).encode())[0])
'''
    workers = [subprocess.Popen([sys.executable, '-c', code, str(Path(crypto.__file__).resolve().parents[1]),
        str(Path(__file__).resolve().parents[1] / 'unit'), str(backend.path), str(tmp_path), label],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for label in ('one', 'two')]
    try:
        until = time.monotonic() + 10
        while not all((tmp_path / ('ready-' + label)).exists() for label in ('one', 'two')):
            assert time.monotonic() < until, 'key initialization workers did not reach the barrier'
            time.sleep(.01)
        (tmp_path / 'release-one').touch()
        assert workers[0].wait(timeout=10) == 0
        (tmp_path / 'release-two').touch()
        assert workers[1].wait(timeout=10) == 0
        cipher = crypto.EnvelopeCipher(crypto.KeyringKeyProvider('synthetic-key-race', backend=backend))
        for label in ('one', 'two'):
            assert cipher.decrypt((tmp_path / ('ack-' + label)).read_bytes()) == ('synthetic-' + label).encode()
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize('raw', ['0', '-1', '4294967296', '01', ' 1', '+1'])
def test_key_versions_must_fit_the_envelope(raw):
    backend = FakeKeyring()
    backend.store['artha-ledger', 'current_version'] = raw
    with pytest.raises(crypto.KeyUnavailableError, match='version'):
        crypto.KeyringKeyProvider(backend=backend).current_version()


@pytest.mark.parametrize('version', [0, -1, 4294967296, True, '1'])
def test_key_lookup_refuses_invalid_versions(version):
    with pytest.raises(crypto.KeyUnavailableError, match='version'):
        crypto.KeyringKeyProvider(backend=FakeKeyring()).key(version)


def test_unpublished_key_is_reused_and_invalid_material_cannot_be_published():
    import base64
    backend = FakeKeyring()
    backend.store['artha-ledger', 'key.1'] = base64.b64encode(K1).decode()
    provider = crypto.KeyringKeyProvider(backend=backend)
    assert provider.current_version() == 1 and provider.key(1) == K1
    backend.store['artha-ledger', 'key.2'] = 'corrupt'
    with pytest.raises(crypto.KeyUnavailableError):
        provider.rotate()
    assert provider.current_version() == 1 and provider.key(1) == K1


def test_key_material_and_version_publication_are_both_verified():
    class PartialKeyring(FakeKeyring):
        dropped = 'key.1'
        def set_password(self, service, name, value):
            if name != self.dropped:
                super().set_password(service, name, value)
    backend = PartialKeyring()
    provider = crypto.KeyringKeyProvider(backend=backend)
    with pytest.raises(crypto.KeyUnavailableError, match='data key'):
        provider.current_version()
    assert backend.store == {}
    backend.dropped = 'current_version'
    with pytest.raises(crypto.KeyUnavailableError, match='key version'):
        provider.current_version()
    saved = backend.store['artha-ledger', 'key.1']
    backend.dropped = ''
    assert provider.current_version() == 1
    assert backend.store['artha-ledger', 'key.1'] == saved
    backend.store['artha-ledger', 'current_version'] = '4294967295'
    with pytest.raises(crypto.KeyUnavailableError, match='limit'):
        provider.rotate()


def test_service_lock_refuses_competing_updates_without_blocking_reads(tmp_path, monkeypatch):
    import hashlib
    from catchup_core.session_lock import session_lock
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / '.artha-local'))
    provider = crypto.KeyringKeyProvider(backend=FakeKeyring())
    lock = tmp_path / '.artha-local/key-locks' / (hashlib.sha256(b'artha-ledger').hexdigest() + '.lock')
    with session_lock(lock):
        with pytest.raises(crypto.KeyUnavailableError, match='retry'):
            provider.current_version()
    assert provider.current_version() == 1
    with session_lock(lock):
        assert provider.current_version() == 1
        with pytest.raises(crypto.KeyUnavailableError, match='retry'):
            provider.rotate()


def test_independent_rotations_keep_every_acknowledged_ciphertext(tmp_path, monkeypatch):
    import base64
    import json
    import subprocess
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'unit'))
    from test_credentials import SQLiteKeyring
    backend = SQLiteKeyring(tmp_path / 'fake-keyring.db')
    service = 'synthetic-key-rotations'
    crypto.KeyringKeyProvider(service, backend=backend).current_version()
    code = r'''
import sys,time,json,base64
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from test_credentials import SQLiteKeyring
from catchup_core.crypto import KeyringKeyProvider,EnvelopeCipher,KeyUnavailableError
provider=KeyringKeyProvider('synthetic-key-rotations',backend=SQLiteKeyring(sys.argv[3]))
rows=[]
until=time.monotonic()+10
while len(rows)<6:
 try:version=provider.rotate()
 except KeyUnavailableError:
  if time.monotonic()>until:raise
  time.sleep(.005)
  continue
 encrypted,_=EnvelopeCipher(provider).encrypt(b'synthetic-rotation')
 rows.append([version,base64.b64encode(encrypted).decode()])
Path(sys.argv[4]).write_text(json.dumps(rows))
'''
    workers = [subprocess.Popen([sys.executable, '-c', code, str(Path(crypto.__file__).resolve().parents[1]),
        str(Path(__file__).resolve().parents[1] / 'unit'), str(backend.path), str(tmp_path / str(index))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for index in range(2)]
    try:
        for process in workers:
            _, error = process.communicate(timeout=15)
            assert process.returncode == 0, error
        rows = [row for index in range(2) for row in json.loads((tmp_path / str(index)).read_text())]
        assert sorted(row[0] for row in rows) == list(range(2, 14))
        cipher = crypto.EnvelopeCipher(crypto.KeyringKeyProvider(service, backend=backend))
        for _, encoded in rows:
            assert cipher.decrypt(base64.b64decode(encoded)) == b'synthetic-rotation'
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize('rotate', [False, True])
@pytest.mark.parametrize('checkpoint', ['material', 'version'])
def test_process_death_during_key_publication_preserves_recovery(tmp_path, monkeypatch, rotate, checkpoint):
    import base64
    import subprocess
    import sys
    import time
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'unit'))
    from test_credentials import SQLiteKeyring
    backend = SQLiteKeyring(tmp_path / 'fake-keyring.db')
    provider = crypto.KeyringKeyProvider('synthetic-key-death', backend=backend)
    old = crypto.EnvelopeCipher(provider).encrypt(b'synthetic-history')[0] if rotate else None
    code = r'''
import sys,time
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from test_credentials import SQLiteKeyring
from catchup_core.crypto import KeyringKeyProvider
backend=SQLiteKeyring(sys.argv[3])
root=Path(sys.argv[4])
write=backend.set_password
def pause(service,name,value):
 write(service,name,value)
 target=('key.2' if sys.argv[5]=='True' else 'key.1') if sys.argv[6]=='material' else 'current_version'
 if name==target:
  (root/'ready').touch()
  while True:time.sleep(.02)
backend.set_password=pause
provider=KeyringKeyProvider('synthetic-key-death',backend=backend)
provider.rotate() if sys.argv[5]=='True' else provider.current_version()
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(Path(crypto.__file__).resolve().parents[1]),
        str(Path(__file__).resolve().parents[1] / 'unit'), str(backend.path), str(tmp_path), str(rotate), checkpoint],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until = time.monotonic() + 10
        while not (tmp_path / 'ready').exists():
            assert process.poll() is None, process.communicate()[1]
            assert time.monotonic() < until, 'publication worker did not reach checkpoint'
            time.sleep(.01)
        version = 2 if rotate else 1
        material = backend.get_password('synthetic-key-death', f'key.{version}')
        process.kill()
        process.communicate(timeout=10)
        recovered = crypto.KeyringKeyProvider('synthetic-key-death', backend=backend)
        if rotate and checkpoint == 'material':
            assert recovered.rotate() == 2
        assert recovered.current_version() == version
        assert recovered.key(version) == base64.b64decode(material)
        cipher = crypto.EnvelopeCipher(recovered)
        if old is not None:
            assert cipher.decrypt(old) == b'synthetic-history'
        assert cipher.decrypt(cipher.encrypt(b'synthetic-recovered')[0]) == b'synthetic-recovered'
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_invalid_keyring_type_cannot_be_treated_as_missing():
    backend = FakeKeyring()
    backend.store['artha-ledger', 'current_version'] = 1
    with pytest.raises(crypto.KeyUnavailableError, match='invalid type'):
        crypto.KeyringKeyProvider(backend=backend).current_version()


def test_key_update_lock_filesystem_failure_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / '.artha-local'))
    (tmp_path / '.artha-local').write_text('synthetic-blocked-directory')
    with pytest.raises(crypto.KeyUnavailableError, match='update unavailable'):
        crypto.KeyringKeyProvider(backend=FakeKeyring()).current_version()
