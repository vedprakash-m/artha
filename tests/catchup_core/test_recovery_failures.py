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


def test_windows_lock_contract_and_release(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(locks, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2,
        locking=lambda fd, op, count: events.append((op, count))))
    with locks.session_lock(tmp_path / "lock"):
        assert events == [(1, 1)]
    assert events == [(1, 1), (2, 1)]
    def busy(*_):
        raise OSError("held")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=1, locking=busy))
    with pytest.raises(locks.SessionBusy):
        with locks.session_lock(tmp_path / "lock"):
            pytest.fail("must not enter")


def test_disk_full_atomic_replace_preserves_old_ciphertext(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.ae1"
    path.write_bytes(b"old ciphertext")
    def disk_full(*_):
        raise OSError("disk full")
    monkeypatch.setattr(backup.os, "replace", disk_full)
    with pytest.raises(OSError):
        backup.atomic_bytes(path, b"new ciphertext")
    assert path.read_bytes() == b"old ciphertext" and not path.with_suffix(".ae1.tmp").exists()


def test_snapshot_quarantines_actions_and_curated_data(tmp_path):
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
    backup.restore_encrypted_snapshot(path, destination, CIPHER, sealed)
    assert (destination / "actions.quarantined.db").exists() and not (destination / "actions.db").exists()
    assert b"private" not in (destination / "curated/goals.md.ae1").read_bytes()
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


def test_restore_command_works_without_a_live_ledger(tmp_path, monkeypatch):
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
    assert cli.main(["restore", str(export), str(destination), "--shadow"], out=out, err=err) == 0
    assert (destination / "ledger.db").is_file() and not err.getvalue()
    assert cli.main(["restore", str(export), str(destination), "--shadow"], out=out, err=err) == 20
    assert cli.main(["restore", str(export), str(tmp_path / "other"), "--actor", "intruder"],
                    out=out, err=err) == 20
