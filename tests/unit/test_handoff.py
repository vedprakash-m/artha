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
