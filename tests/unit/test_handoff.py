from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import handoff


NOW = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)


def _targets(root: Path) -> tuple[Path, ...]:
    paths = (root / "state" / "goals.md", root / "state" / "finance.md.age")
    for index, path in enumerate(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"synthetic-{index}".encode())
    return paths


def test_seal_then_verify_matches_as_far_as_this_laptop_can_see(tmp_path):
    targets = _targets(tmp_path)
    result = handoff.seal(
        tmp_path,
        targets=targets,
        owner_confirmed_sync=True,
        now=NOW,
        hostname="SYNTH-HOST-A",
    )
    assert result.status == "sealed"

    verified = handoff.verify(tmp_path, targets=targets, now=NOW)
    assert verified.status == "verified"
    assert verified.changed == ()
    assert "as far as this laptop can see" in verified.message


def test_seal_refuses_without_owner_confirmation(tmp_path):
    targets = _targets(tmp_path)
    result = handoff.seal(
        tmp_path,
        targets=targets,
        owner_confirmed_sync=False,
        now=NOW,
        hostname="SYNTH-HOST-A",
    )
    assert result.status == "unverified"
    assert not (tmp_path / "state" / ".handoff.json").exists()


def test_verify_detects_changed_missing_and_stale_manifest(tmp_path):
    targets = _targets(tmp_path)
    handoff.seal(
        tmp_path,
        targets=targets,
        owner_confirmed_sync=True,
        now=NOW,
        hostname="SYNTH-HOST-A",
    )
    targets[0].write_text("changed", encoding="utf-8")
    targets[1].unlink()

    result = handoff.verify(tmp_path, targets=targets, now=NOW)
    assert result.status == "unverified"
    assert "state/goals.md" in result.changed
    assert "state/finance.md.age" in result.missing

    manifest = json.loads((tmp_path / "state" / ".handoff.json").read_text())
    manifest["sealed_at"] = "2026-11-10T00:00:00+00:00"
    (tmp_path / "state" / ".handoff.json").write_text(json.dumps(manifest))
    stale = handoff.verify(tmp_path, targets=targets, now=NOW)
    assert stale.status == "unverified"
    assert "stale" in stale.message.lower()


def test_accepted_handoff_remains_active_for_local_changes(tmp_path):
    targets = _targets(tmp_path)
    handoff.seal(
        tmp_path,
        targets=targets,
        owner_confirmed_sync=True,
        now=NOW,
        hostname="SYNTH-HOST-A",
    )
    assert handoff.verify(tmp_path, targets=targets, now=NOW).status == "verified"
    targets[0].write_text("local change", encoding="utf-8")
    result = handoff.verify(tmp_path, targets=targets, now=NOW)
    assert result.status == "verified"
    assert "active" in result.message


def test_seal_refuses_visible_conflict_copy(tmp_path):
    targets = _targets(tmp_path)
    (tmp_path / "state" / "goals-SYNTH-HOST-A.md").write_text("conflict")
    result = handoff.seal(
        tmp_path,
        targets=targets,
        owner_confirmed_sync=True,
        now=NOW,
        hostname="SYNTH-HOST-A",
    )
    assert result.status == "refused"
    assert "conflict" in result.message.lower()


def test_seal_and_clear_commands_need_an_owner_terminal(tmp_path, capsys, monkeypatch):
    import sys

    import state_restore
    import write_refusals

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert handoff.main(["seal", "--owner-confirmed", "--root", str(tmp_path)]) == 20
    assert not (tmp_path / "state" / ".handoff.json").exists()
    assert write_refusals.main(["clear"]) == 20
    assert state_restore.main(["apply", "state/goals.md", "x"]) == 20
    assert "owner terminal" in capsys.readouterr().err


def test_a_weekend_old_manifest_still_verifies_when_the_files_match(tmp_path):
    targets = _targets(tmp_path)
    handoff.seal(tmp_path, targets=targets, owner_confirmed_sync=True, now=NOW, hostname="SYNTH-HOST-A")
    monday = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)  # three days later
    assert handoff.verify(tmp_path, targets=targets, now=monday).status == "verified"


def test_verify_without_record_does_not_write_the_local_acceptance(tmp_path, monkeypatch):
    from lib.local_paths import local_dir

    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))

    targets = _targets(tmp_path)
    handoff.seal(tmp_path, targets=targets, owner_confirmed_sync=True, now=NOW, hostname="SYNTH-HOST-A")
    assert handoff.verify(tmp_path, targets=targets, now=NOW, record=False).status == "verified"
    assert not list((local_dir() / "light-handoff").glob("*.json"))


def test_seal_refuses_while_write_refusals_are_uninspected(tmp_path, monkeypatch):
    from lib import write_refusals

    targets = _targets(tmp_path)
    monkeypatch.setattr(write_refusals, "list_refusals", lambda **_k: {"state/goals.md": {"reason": "stale"}})
    result = handoff.seal(tmp_path, targets=targets, owner_confirmed_sync=True, now=NOW, hostname="SYNTH-HOST-A")
    assert result.status == "refused" and "refusal" in result.message
    assert not (tmp_path / "state" / ".handoff.json").exists()


def _auto(tmp_path, targets, monkeypatch, **kwargs):
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))
    return handoff.auto_seal(tmp_path, targets=targets, now=NOW, hostname="SYNTH-HOST-A", **kwargs)


def test_auto_seal_creates_the_first_manifest_and_this_laptop_accepts_it(tmp_path, monkeypatch):
    targets = _targets(tmp_path)
    assert _auto(tmp_path, targets, monkeypatch).status == "sealed"
    targets[0].write_text("later local edit", encoding="utf-8")
    # The sealing laptop accepted its own manifest, so its later edits are not a mismatch.
    assert handoff.verify(tmp_path, targets=targets, now=NOW, record=False).status == "verified"


def test_auto_seal_is_quiet_when_nothing_changed(tmp_path, monkeypatch):
    targets = _targets(tmp_path)
    _auto(tmp_path, targets, monkeypatch)
    before = (tmp_path / "state" / ".handoff.json").read_bytes()
    result = _auto(tmp_path, targets, monkeypatch)
    assert result.status == "skipped"
    assert (tmp_path / "state" / ".handoff.json").read_bytes() == before


def test_auto_seal_reseals_after_this_laptop_changes_files(tmp_path, monkeypatch):
    targets = _targets(tmp_path)
    _auto(tmp_path, targets, monkeypatch)
    targets[0].write_text("work done here", encoding="utf-8")
    assert _auto(tmp_path, targets, monkeypatch).status == "sealed"
    manifest = json.loads((tmp_path / "state" / ".handoff.json").read_text(encoding="utf-8"))
    assert manifest["files"]["state/goals.md"] != "x"
    # A second laptop with the same bytes verifies.
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "other"))
    assert handoff.verify(tmp_path, targets=targets, now=NOW).status == "verified"


def test_auto_seal_never_seals_for_a_laptop_that_has_not_accepted_the_manifest(tmp_path, monkeypatch):
    targets = _targets(tmp_path)
    handoff.seal(tmp_path, targets=targets, owner_confirmed_sync=True, now=NOW, hostname="SYNTH-HOST-B")
    targets[0].write_text("diverged", encoding="utf-8")
    before = (tmp_path / "state" / ".handoff.json").read_bytes()
    result = _auto(tmp_path, targets, monkeypatch)
    assert result.status == "skipped"
    assert "not the active laptop" in result.message
    assert (tmp_path / "state" / ".handoff.json").read_bytes() == before


def test_auto_seal_does_nothing_when_the_engine_is_enabled(tmp_path, monkeypatch):
    targets = _targets(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "catchup_core.yaml").write_text("cutover: true\n", encoding="utf-8")
    result = _auto(tmp_path, targets, monkeypatch)
    assert result.status == "skipped"
    assert not (tmp_path / "state" / ".handoff.json").exists()


def test_auto_seal_still_refuses_a_conflict_copy(tmp_path, monkeypatch):
    targets = _targets(tmp_path)
    (tmp_path / "state" / "goals-REMOTE-HOST-Q7.md").write_text("copy", encoding="utf-8")
    assert _auto(tmp_path, targets, monkeypatch).status == "refused"


def test_auto_seal_command_never_fails_the_session_and_needs_no_terminal(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setattr(handoff, "_targets", lambda root, targets: _targets(tmp_path))
    assert handoff.main(["auto-seal", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "state" / ".handoff.json").exists()
    (tmp_path / "state" / "goals-REMOTE-HOST-Q7.md").write_text("copy", encoding="utf-8")
    (tmp_path / "state" / "goals.md").write_text("changed", encoding="utf-8")
    assert handoff.main(["auto-seal", "--root", str(tmp_path)]) == 0
    assert "refused" in capsys.readouterr().err
