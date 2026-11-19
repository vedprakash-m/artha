"""state_guard: off-repo snapshots, shrink refusal, and the loud freshness check."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import state_guard as sg


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    for n in ("goals.md", "memory.md", "finance.md.age", "open_items.md", "kids.md.age"):
        (state / n).write_text("---\ndomain: x\n---\nbody\n")
    (state / "telemetry.jsonl").write_text("noise")
    monkeypatch.setenv("ARTHA_STATE_BACKUP_DIR", str(tmp_path / "backups"))
    return state


def test_snapshot_copies_state_files_outside_the_checkout(env):
    code, _ = sg.snapshot(env)
    assert code == 0
    snap = sg.list_snapshots()[-1]
    man = json.loads((snap / "manifest.json").read_text())
    assert set(man["files"]) == {"goals.md", "memory.md", "finance.md.age", "open_items.md", "kids.md.age"}
    assert env not in snap.parents


def test_empty_state_is_never_snapshotted(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHA_STATE_BACKUP_DIR", str(tmp_path / "b"))
    empty = tmp_path / "state"
    empty.mkdir()
    assert sg.snapshot(empty)[0] == 3
    assert sg.list_snapshots() == []


def test_a_wiped_state_cannot_replace_a_good_backup(env):
    assert sg.snapshot(env)[0] == 0
    for p in list(env.glob("*.md"))[:3]:
        p.unlink()
    code, msg = sg.snapshot(env)
    assert code == 3 and "REFUSED" in msg
    assert len(sg.list_snapshots()) == 1
    assert sg.snapshot(env, force=True)[0] == 0


def test_check_is_loud_when_no_backup_exists(env):
    code, msg = sg.check(env)
    assert code == 1 and "NO STATE BACKUP" in msg


def test_check_self_heals_when_asked_and_state_is_healthy(env):
    code, _ = sg.check(env, snapshot_if_stale=True)
    assert code == 0 and len(sg.list_snapshots()) == 1
    assert sg.check(env)[0] == 0


def test_check_fails_when_state_shrinks(env):
    sg.snapshot(env)
    for p in list(env.glob("*.md"))[:3]:
        p.unlink()
    code, msg = sg.check(env, snapshot_if_stale=True)
    assert code == 1 and "STATE SHRANK" in msg
    assert len(sg.list_snapshots()) == 1


def test_check_fails_when_snapshot_is_too_old(env):
    sg.snapshot(env)
    mf = sg.list_snapshots()[-1] / "manifest.json"
    man = json.loads(mf.read_text())
    man["created"] = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat(timespec="seconds")
    mf.write_text(json.dumps(man))
    code, msg = sg.check(env)
    assert code == 1 and "NO STATE BACKUP SINCE" in msg
    assert sg.check(env, snapshot_if_stale=True)[0] == 0


def test_verify_detects_tampering_and_passes_when_clean(env):
    sg.snapshot(env)
    assert sg.verify(decrypt=False)[0] == 0
    (sg.list_snapshots()[-1] / "goals.md").write_text("tampered")
    code, msg = sg.verify(decrypt=False)
    assert code == 1 and "goals.md" in msg


def test_restore_refuses_a_non_empty_destination_and_restores_into_an_empty_one(env, tmp_path):
    sg.snapshot(env)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.md").write_text("keep me")
    code, msg = sg.restore(busy)
    assert code == 1 and "REFUSED" in msg and (busy / "x.md").read_text() == "keep me"
    code, msg = sg.restore(tmp_path / "fresh")
    assert code == 0 and (tmp_path / "fresh" / "goals.md").read_text() == (env / "goals.md").read_text()


def test_restore_with_no_snapshot_fails(env, tmp_path):
    assert sg.restore(tmp_path / "d")[0] == 1


def test_drill_passes_and_reports_drift(env):
    sg.snapshot(env)
    assert sg.drill(env, decrypt=False)[0] == 0
    (env / "goals.md").write_text("changed after the snapshot")
    (env / "memory.md").unlink()
    code, msg = sg.drill(env, decrypt=False)
    assert code == 0 and "1 changed" in msg and "1 absent" in msg


def test_drill_fails_loudly_when_the_snapshot_is_corrupt(env):
    sg.snapshot(env)
    (sg.list_snapshots()[-1] / "goals.md").write_text("corrupted")
    code, msg = sg.drill(env, decrypt=False)
    assert code == 1 and "RESTORE DRILL FAILED" in msg and "goals.md" in msg


def test_drill_with_no_snapshot_fails_loudly(env):
    code, msg = sg.drill(env, decrypt=False)
    assert code == 1 and "RESTORE DRILL FAILED" in msg


def test_mirror_gfs_copies_new_files_only_and_never_deletes(tmp_path, monkeypatch):
    fake = tmp_path / "repo"
    (fake / "backups" / "daily").mkdir(parents=True)
    (fake / "backups" / "daily" / "a.zip").write_text("1")
    monkeypatch.setattr(sg, "ARTHA_DIR", fake)
    mirror = tmp_path / "mirror"
    assert sg.mirror_gfs(mirror) == (0, f"gfs mirror: 1 new files copied to {mirror}")
    (fake / "backups" / "daily" / "a.zip").unlink()
    (fake / "backups" / "daily" / "b.zip").write_text("2")
    assert sg.mirror_gfs(mirror)[1].startswith("gfs mirror: 1 new")
    assert (mirror / "daily" / "a.zip").exists() and (mirror / "daily" / "b.zip").exists()


def test_nested_files_are_snapshotted_restored_and_drilled_but_big_and_binary_files_are_not(env, tmp_path):
    (env / "work").mkdir()
    (env / "work" / "notes.md").write_text("---\ndomain: x\n---\nwork notes\n")
    (env / "traces").mkdir()
    (env / "traces" / "t.md").write_text("ephemeral")
    (env / "work" / "deck.pptx").write_bytes(b"binary")
    (env / "work" / "huge.md").write_bytes(b"x" * (sg.MAX_FILE_BYTES + 1))
    sg.snapshot(env)
    man = json.loads((sg.list_snapshots()[-1] / "manifest.json").read_text())
    assert "work/notes.md" in man["files"]
    assert not {"traces/t.md", "work/deck.pptx", "work/huge.md"} & set(man["files"])
    assert sg.restore(tmp_path / "out")[0] == 0 and (tmp_path / "out" / "work" / "notes.md").is_file()
    code, msg = sg.drill(env, decrypt=False)
    assert code == 0 and "0 changed and 0 absent" in msg
    (env / "work" / "notes.md").unlink()
    assert "STATE SHRANK" not in sg.check(env)[1]   # one nested file of seven is under the 20% limit


def test_old_snapshots_are_pruned(env, monkeypatch):
    monkeypatch.setattr(sg, "KEEP_SNAPSHOTS", 2)
    for _ in range(4):
        sg.snapshot(env)
    assert len(sg.list_snapshots()) == 2
