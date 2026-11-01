from __future__ import annotations

import base64
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from catchup_core import backup, ledger, session_lock as locks
from test_user_surfaces import CIPHER, KEY, Keyring


def test_process_lock_refuses_contention_in_the_same_process(tmp_path):
    with locks.session_lock(tmp_path / "lock"):
        with pytest.raises(locks.SessionBusy):
            with locks.session_lock(tmp_path / "lock"):
                pytest.fail("must never acquire twice")


@pytest.mark.parametrize("shared", [False, True])
def test_windows_lock_contract_and_release(tmp_path, monkeypatch, shared):
    import ctypes

    events = []
    busy = False

    class NativeCall:
        def __init__(self, name):
            self.name = name

        def __call__(self, handle, *args):
            overlap = args[-1]._obj
            events.append((self.name, handle, args[:-1], overlap.Offset, overlap.OffsetHigh))
            return not (busy and self.name == "lock")

    kernel = SimpleNamespace(LockFileEx=NativeCall("lock"), UnlockFileEx=NativeCall("unlock"))
    monkeypatch.setattr(locks, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 33, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "held"), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: 12345))
    expected_lock = ("lock", 12345, (1 if shared else 3, 0, 1, 0), 0, 0)
    expected_unlock = ("unlock", 12345, (0, 1, 0), 0, 0)
    with (tmp_path / "lock").open("a+b") as handle, locks._windows_lock(handle, shared=shared):
        assert events == [expected_lock]
    assert events == [expected_lock, expected_unlock]
    with pytest.raises(RuntimeError, match="synthetic"):
        with (tmp_path / "lock").open("a+b") as handle, locks._windows_lock(handle, shared=shared):
            raise RuntimeError("synthetic")
    assert events[-2:] == [expected_lock, expected_unlock]
    busy = True
    with pytest.raises(locks.SessionBusy):
        with (tmp_path / "lock").open("a+b") as handle, locks._windows_lock(handle, shared=shared):
            pytest.fail("must not enter")
    assert events[-1] == expected_lock
    assert len(events) == 5


def test_disk_full_atomic_replace_preserves_old_ciphertext(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.ae1"
    path.write_bytes(b"old ciphertext")
    def disk_full(*_):
        raise OSError("disk full")
    monkeypatch.setattr(backup.os, "replace", disk_full)
    with pytest.raises(OSError):
        backup.atomic_bytes(path, b"new ciphertext")
    assert path.read_bytes() == b"old ciphertext" and not path.with_suffix(".ae1.tmp").exists()
    assert not list(tmp_path.glob(".snapshot.ae1.*.tmp"))


def test_atomic_bytes_does_not_follow_a_predictable_temporary_symlink(tmp_path):
    path = tmp_path / "snapshot.ae1"
    path.write_bytes(b"old ciphertext")
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"synthetic untouched sentinel")
    path.with_suffix(".ae1.tmp").symlink_to(sentinel)
    backup.atomic_bytes(path, b"new ciphertext")
    assert sentinel.read_bytes() == b"synthetic untouched sentinel"
    assert not path.is_symlink()
    assert path.read_bytes() == b"new ciphertext"
    assert path.with_suffix(".ae1.tmp").is_symlink()
    assert not list(tmp_path.glob(".snapshot.ae1.*.tmp"))


def test_atomic_bytes_replaces_destination_symlink_without_writing_its_target(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"synthetic untouched sentinel")
    path = tmp_path / "snapshot.ae1"
    path.symlink_to(sentinel)
    backup.atomic_bytes(path, b"new ciphertext")
    assert sentinel.read_bytes() == b"synthetic untouched sentinel"
    assert not path.is_symlink()
    assert path.read_bytes() == b"new ciphertext"


def test_legacy_restore_hold_precedes_reads_and_destination_creation(tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("legacy restoration must not read private inputs or stage output")
    monkeypatch.setattr(backup, "read_snapshot", unexpected)
    monkeypatch.setattr(backup, "atomic_bytes", unexpected)
    privacy = SimpleNamespace(read=unexpected, replay=unexpected)
    destination = tmp_path / "missing-parent" / "restore"
    with pytest.raises(ledger.LedgerLocked, match="governed checkpoint/quarantine restore"):
        backup.restore_encrypted_snapshot(tmp_path / "missing.ae1", destination, CIPHER, privacy)
    assert not destination.parent.exists()


def test_legacy_restore_never_becomes_an_active_installation(tmp_path):
    sealed = ledger.MemorySealedStore()
    with ledger.Ledger(tmp_path / "source.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open() as led:
        led.migrate()
        led.seal_privacy()
        privacy = SimpleNamespace(read=sealed.read, replay=led.privacy_history)
        snapshot = tmp_path / "snapshot.ae1"
        backup.encrypted_snapshot(snapshot, led._path, CIPHER)
        destination = tmp_path / "restore"
        with pytest.raises(ledger.LedgerLocked, match="inactive installation fence"):
            backup.restore_encrypted_snapshot(snapshot, destination, CIPHER, privacy)
        assert not destination.exists()


def test_legacy_restore_hold_prevents_privacy_race_before_staging(tmp_path, monkeypatch):
    sealed = ledger.MemorySealedStore()
    with ledger.Ledger(tmp_path / "source.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open() as led:
        led.migrate()
        grant = led.grant_consent("adult", "adult", "health", ["view"], "owner")
        led.seal_privacy()
        privacy = SimpleNamespace(read=sealed.read, replay=led.privacy_history)
        snapshot = tmp_path / "snapshot.ae1"
        backup.encrypted_snapshot(snapshot, led._path, CIPHER)
        publish = backup.atomic_bytes
        staged = []

        def revoke_after_staging(path, data):
            staged.append(path)
            publish(path, data)
            if path.name == "ledger.db":
                led.revoke_consent(grant)

        monkeypatch.setattr(backup, "atomic_bytes", revoke_after_staging)
        destination = tmp_path / "restore"
        with pytest.raises(ledger.LedgerLocked, match="current privacy history"):
            backup.restore_encrypted_snapshot(snapshot, destination, CIPHER, privacy)
        assert not staged
        assert not destination.exists()
        assert not list(tmp_path.glob(".artha-restore-*"))


def test_snapshot_captures_actions_and_curated_data_but_legacy_restore_is_held(tmp_path):
    sealed = ledger.KeyringSealedStore(Keyring(), "restore")
    led = ledger.Ledger(tmp_path / "source.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open()
    led.migrate()
    led.seal_privacy()
    action = tmp_path / "actions.db"
    with sqlite3.connect(action) as db:
        db.execute("CREATE TABLE actions(id)")
    path = tmp_path / "snapshot.ae1"
    backup.encrypted_snapshot(path, led._path, CIPHER, action_db=action, curated={"goals.md": b"private"})
    with pytest.raises(ledger.LedgerError, match="exists"):
        backup.encrypted_snapshot(path, led._path, CIPHER)
    backup.encrypted_snapshot(path, led._path, CIPHER, idempotent=True)
    destination = tmp_path / "restore"
    files = backup.read_snapshot(path, CIPHER)
    assert "actions.db" in files and files["curated/goals.md"] == b"private"
    assert b"private" not in path.read_bytes()
    with pytest.raises(ledger.LedgerLocked, match="governed checkpoint/quarantine restore"):
        backup.restore_encrypted_snapshot(path, destination, CIPHER, sealed)
    assert not destination.exists()
    with pytest.raises(ledger.LedgerError):
        backup.encrypted_snapshot(tmp_path / "bad", led._path, CIPHER, curated={"../escape": b"bad"})
    led.close()


@pytest.mark.parametrize("problem", ["format", "unsafe_path", "missing_history", "stale_history"])
def test_restore_never_publishes_an_unsafe_or_unverifiable_generation(tmp_path, problem):
    sealed = ledger.KeyringSealedStore(Keyring(), "restore")
    led = ledger.Ledger(tmp_path / "source.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open()
    led.migrate()
    led.seal_privacy()
    files = {"ledger.db": base64.b64encode(backup.database_bytes(led._path)).decode()}
    if problem == "unsafe_path":
        files["../escape"] = base64.b64encode(b"private").decode()
    payload = {"schema": "wrong" if problem == "format" else "artha.snapshot.v1",
               "dispatchable": False, "files": files}
    path = tmp_path / "snapshot.ae1"
    path.write_bytes(CIPHER.encrypt(json.dumps(payload).encode(), aad=b"artha.snapshot.v1")[0])
    if problem == "missing_history":
        sealed = ledger.KeyringSealedStore(Keyring(), "unknown")
    if problem == "stale_history":
        sealed._backend.set_password(sealed._service, "checkpoint", '{}')
    destination = tmp_path / "restore"
    with pytest.raises((ledger.LedgerError, KeyError, ValueError)):
        backup.restore_encrypted_snapshot(path, destination, CIPHER, sealed)
    assert not destination.exists() and not (tmp_path / "escape").exists()
    led.close()


def test_legacy_restore_command_reports_safety_hold_without_a_live_ledger(tmp_path, monkeypatch):
    import io
    from catchup_core import cli, crypto, runtime
    backend = Keyring()
    cipher = crypto.EnvelopeCipher(crypto.KeyringKeyProvider("artha-ledger", backend=backend))
    sealed = ledger.KeyringSealedStore(backend, "shadow")
    with ledger.Ledger(tmp_path / "source.db", id_key=KEY, cipher=cipher, sealed=sealed).open() as led:
        led.migrate()
        led.seal_privacy()
        export = tmp_path / "export.ae1"
        backup.encrypted_snapshot(export, led._path, cipher)
    monkeypatch.setitem(sys.modules, "keyring", backend)
    monkeypatch.setattr(runtime, "load_runtime_config", lambda _: SimpleNamespace(viewer="owner"))
    destination = tmp_path / "recovered"
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["restore", str(export), str(destination), "--shadow"], out=out, err=err) == 20
    assert not destination.exists() and not out.getvalue()
    assert "governed checkpoint/quarantine restore" in err.getvalue()
    assert cli.main(["restore", str(export), str(destination), "--shadow"], out=out, err=err) == 20
    assert cli.main(["restore", str(export), str(tmp_path / "other"), "--actor", "intruder"],
                    out=out, err=err) == 20
