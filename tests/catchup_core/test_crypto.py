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


def test_keyring_provider_uses_real_keyring_module_by_default(monkeypatch):
    import keyring  # noqa: PLC0415
    store = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    p = crypto.KeyringKeyProvider(service="artha-test")
    assert p.current_version() == 1 and len(p.key(1)) == 32


def test_keyring_set_failure_is_unavailable():
    class ReadOnly(FakeKeyring):
        def get_password(self, service, name):
            return None

        def set_password(self, service, name, value):
            raise RuntimeError("read-only")
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.KeyringKeyProvider(backend=ReadOnly()).current_version()
