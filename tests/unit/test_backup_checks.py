"""The preflight wrapper turns state_guard results into a loud P0."""
from __future__ import annotations

import state_guard
from preflight.backup_checks import check_state_backup


def test_a_failing_guard_is_a_p0_failure(monkeypatch):
    monkeypatch.setattr(state_guard, "check", lambda **kw: (1, "🔴 STATE SHRANK: 3 files"))
    r = check_state_backup()
    assert r.severity == "P0" and not r.passed and "STATE SHRANK" in r.message and r.fix_hint


def test_a_healthy_guard_passes_and_read_only_never_snapshots(monkeypatch):
    import backup
    monkeypatch.setattr(backup, "load_backup_registry", lambda: [])
    seen = {}
    monkeypatch.setattr(state_guard, "check", lambda **kw: seen.update(kw) or (0, "fresh"))
    assert check_state_backup(read_only=True).passed
    assert seen["snapshot_if_stale"] is False


def test_a_stale_gfs_registry_is_a_p0_failure(monkeypatch, tmp_path):
    import backup
    monkeypatch.setattr(state_guard, "check", lambda **kw: (0, "fresh"))
    gone = tmp_path / "state" / "employment.md"
    monkeypatch.setattr(backup, "load_backup_registry", lambda: [{"name": "employment", "source_path": gone}])
    r = check_state_backup()
    assert not r.passed and r.severity == "P0" and "GFS BACKUP WILL ABORT" in r.message and "employment" in r.message


def test_a_complete_gfs_registry_passes(monkeypatch, tmp_path):
    import backup
    here = tmp_path / "a.age"
    here.write_text("x")
    monkeypatch.setattr(state_guard, "check", lambda **kw: (0, "fresh"))
    monkeypatch.setattr(backup, "load_backup_registry", lambda: [{"name": "a", "source_path": here}])
    assert check_state_backup().passed


def test_a_crashing_guard_is_loud_not_silent(monkeypatch):
    def boom(**kw):
        raise RuntimeError("disk")
    monkeypatch.setattr(state_guard, "check", boom)
    r = check_state_backup()
    assert not r.passed and r.severity == "P0" and "state_guard failed" in r.message
