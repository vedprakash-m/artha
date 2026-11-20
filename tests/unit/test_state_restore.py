from __future__ import annotations

import base64

import state_restore
from lib import state_snapshot


class _FakeSnapshotVault:
    def encrypt_artifact(self, text):
        return b"sealed:" + base64.b64encode(text.encode())

    def read_artifact(self, path):
        return base64.b64decode(path.read_bytes()[len(b"sealed:"):]).decode()


def test_plain_restore_is_target_addressed_and_uses_safe_writer(
    tmp_path, monkeypatch
):
    root = tmp_path / "artha"
    state = root / "state"
    state.mkdir(parents=True)
    target = state / "goals.md"
    previous = (
        "---\nschema_version: '1.0'\ndomain: goals\nlast_updated: '2026-11-19'\n---\n"
        "# Previous\n"
    )
    current = previous.replace("# Previous", "# Current")
    target.write_text(current, encoding="utf-8")
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setattr(state_snapshot, "_vault_for_snapshots", lambda _dir: _FakeSnapshotVault())
    monkeypatch.setattr(state_restore, "ROOT", root)
    monkeypatch.setattr(state_restore, "STATE_DIR", state)
    monkeypatch.setattr(state_restore, "get_age_vault", lambda **_kwargs: _FakeSnapshotVault())

    snapshot_path = state_snapshot.snapshot(
        "goals", previous, artha_dir=root, target=target
    )
    assert snapshot_path is not None

    snapshots = state_restore.snapshots_for(target)
    assert snapshots == [snapshot_path]
    assert state_restore.apply_snapshot(target, snapshot_path) == 0
    assert target.read_text(encoding="utf-8") == previous


def test_corrupt_encrypted_current_is_preserved_before_restore(
    tmp_path, monkeypatch
):
    root = tmp_path / "artha"
    state = root / "state"
    state.mkdir(parents=True)
    target = state / "finance.md.age"
    target.write_bytes(b"corrupt-current")
    snapshot = target.with_name(target.name + ".v1")
    snapshot.write_bytes(b"previous-ciphertext")

    class FakeVault:
        def read_artifact(self, path):
            assert path == snapshot
            return "---\ndomain: finance\n---\n# Previous\n"

        def revision_for(self, name):
            assert name == "finance"
            return True, "a" * 64

        def read(self, name):
            raise RuntimeError("corrupt")

        def write(self, name, text, **kwargs):
            self.written = (name, text, kwargs)

    fake = FakeVault()
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setattr(state_restore, "ROOT", root)
    monkeypatch.setattr(state_restore, "STATE_DIR", state)
    monkeypatch.setattr(state_restore, "get_age_vault", lambda **_kwargs: fake)

    assert state_restore.apply_snapshot(target, snapshot) == 0
    assert fake.written[2]["expected_revision"] == "a" * 64
    quarantined = list((tmp_path / "local" / "state-restore-quarantine").iterdir())
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"corrupt-current"
