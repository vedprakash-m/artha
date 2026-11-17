"""Encrypted release metadata only; no production credentials or State Files."""
import os

import pytest

from catchup_core.renewal_store import ArtifactStore, RenewalError


def test_AR18_encrypted_write_once_roundtrip(tmp_path):
    store = ArtifactStore(tmp_path / "release", b"k" * 32)
    document = {"secret": "SYNTHETIC_PRIVATE_CANARY"}
    identity = store.put(document)
    assert store.read(identity) == document
    assert store.put(document) == identity
    assert all(b"SYNTHETIC_PRIVATE_CANARY" not in p.read_bytes() for p in store.root.iterdir())


def test_AR13_tampering_and_bad_identity_refuse(tmp_path):
    store = ArtifactStore(tmp_path / "release", b"k" * 32)
    identity = store.put({"schema": "synthetic"})
    path = store.root / (identity + ".ae1")
    path.write_bytes(b"bad ciphertext")
    for identity in (identity, "../CANARY", "x" * 64):
        with pytest.raises(RenewalError):
            store.read(identity)


@pytest.mark.skipif(os.name == "nt", reason="POSIX aliases; native Windows tests exercise protected handles")
@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_AR13_aliases_refuse(tmp_path, link):
    store = ArtifactStore(tmp_path / "release", b"k" * 32)
    identity = store.put({"value": "synthetic"})
    path = store.root / (identity + ".ae1")
    alias = tmp_path / "alias"
    if link == "symlink":
        path.rename(alias)
        path.symlink_to(alias)
    else:
        os.link(path, alias)
    with pytest.raises(RenewalError):
        store.read(identity)


def test_AR13_record_limits_and_nonfinite_metadata_refuse(tmp_path):
    store = ArtifactStore(tmp_path / "release", b"k" * 32)
    for document in ({"v": "x" * (1024 * 1024)}, {"v": float("nan")}):
        with pytest.raises(RenewalError):
            store.put(document)


@pytest.mark.parametrize('raw', [b'[]', b'{"a":1,"a":2}', b'not json', b'{ "a":1}', b'{"a":NaN}', b'x' * (1024*1024+1)])
def test_AR13_canonical_decoding_refuses_untrusted_encodings(raw):
    from catchup_core.renewal_store import decoded
    with pytest.raises(RenewalError):
        decoded(raw)


def test_AR13_owner_private_storage_and_missing_artifacts(tmp_path):
    store = ArtifactStore(tmp_path / 'release', b'k' * 32)
    with pytest.raises(RenewalError):
        store.read('a' * 64)
    store.root.mkdir(mode=0o755)
    if os.name != 'nt':
        with pytest.raises(RenewalError):
            store.put({'v': 1})


def test_AR13_non_directory_ancestor_and_failed_publication(tmp_path, monkeypatch):
    from catchup_core.renewal_store import _chain
    parent = tmp_path / 'file'
    parent.write_text('x')
    with pytest.raises(RenewalError):
        _chain(parent / 'child')
    store = ArtifactStore(tmp_path / 'release', b'k' * 32)
    if os.name == 'nt':
        from catchup_core import renewal_store
        def refused(*_a, **_kw):
            raise renewal_store.QuarantineFilesystemError('PRIVATE_CANARY')
        monkeypatch.setattr(renewal_store.WindowsQuarantineDirectory, 'publish_write_once', refused)
    else:
        monkeypatch.setattr(os, 'link', lambda *_: (_ for _ in ()).throw(OSError('PRIVATE_CANARY')))
    with pytest.raises(RenewalError, match='^release artifact publication unavailable$'):
        store.put({'v': 1})


def test_AR13_digest_cannot_be_rebound_with_valid_ciphertext(tmp_path):
    from catchup_core.renewal_store import encoded
    store = ArtifactStore(tmp_path / 'release', b'k' * 32)
    identity = store.put({'v': 1})
    path = store.root / (identity + '.ae1')
    path.write_bytes(store.cipher.encrypt(encoded({'v': 2}), aad=('artha.renewal:' + identity).encode())[0])
    with pytest.raises(RenewalError, match='identity mismatch'):
        store.read(identity)


def test_AR25_posix_substitution_during_read_is_refused(tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('POSIX descriptor boundary')
    from types import SimpleNamespace
    import catchup_core.renewal_store as module
    store = ArtifactStore(tmp_path / 'release', b'k' * 32)
    identity = store.put({'v': 1})
    original = module.os.fstat
    count = [0]
    def changed(fd):
        value = original(fd)
        count[0] += 1
        if count[0] == 2:
            return SimpleNamespace(st_size=value.st_size + 1, st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns)
        return value
    monkeypatch.setattr(module.os, 'fstat', changed)
    with pytest.raises(RenewalError, match='changed during read'):
        store.read(identity)


def test_AR19_windows_adapter_contract_uses_protected_directory_and_handles(tmp_path, monkeypatch):
    """API routing only; native DACL/reparse acceptance is a separate Windows observation."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    import catchup_core.renewal_store as module
    class Directory:
        @classmethod
        @contextmanager
        def open(cls, root):
            yield cls(root)
        def __init__(self, root):
            self.root = root
        @contextmanager
        def create_file(self, name):
            with (self.root / name).open('xb') as handle:
                yield SimpleNamespace(write_all=handle.write, flush=handle.flush, identity='synthetic')
        def publish_write_once(self, source, target, **_):
            (self.root / source).replace(self.root / target)
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    monkeypatch.setattr(module, 'require_active_installation', lambda *_: None)
    monkeypatch.setattr(module, 'WindowsQuarantineDirectory', Directory)
    monkeypatch.setattr(module, 'ensure_private_directory', lambda root: root.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(module, 'open_owner_private_path', lambda path: path.open('rb'))
    store = ArtifactStore(tmp_path / 'release', b'k' * 32)
    identity = store.put({'v': 1})
    assert store.read(identity) == {'v': 1}


def test_AR25_artifact_publication_readback_mismatch_refuses(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'release', b'k' * 32)
    monkeypatch.setattr(store, 'read', lambda _: {'v': 2})
    with pytest.raises(RenewalError, match='readback'):
        store.put({'v': 1})
