from __future__ import annotations

from pathlib import Path

import pytest

from lib.sync_conflicts import ConflictScanUnknown, find_conflicts


HOSTS = ("SYNTH-HOST-AB12",)


def _touch(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic", encoding="utf-8")


def test_detects_target_anchored_plain_and_encrypted_conflicts(tmp_path):
    _touch(
        tmp_path,
        "health-check.md",
        "health-check-SYNTH-HOST-AB12-2.md",
        "finance.md.age",
        "finance.md-SYNTH-HOST-AB12.age",
        "health.md",
        "health (1).md",
        ".artha-decrypted",
        ".artha-decrypted-SYNTH-HOST-AB12",
    )

    assert find_conflicts(
        tmp_path / "health-check.md", hostnames=HOSTS
    ) == [tmp_path / "health-check-SYNTH-HOST-AB12-2.md"]
    assert find_conflicts(
        tmp_path / "finance.md.age", hostnames=HOSTS
    ) == [tmp_path / "finance.md-SYNTH-HOST-AB12.age"]
    assert find_conflicts(
        tmp_path / "health.md", hostnames=HOSTS
    ) == [tmp_path / "health (1).md"]
    assert find_conflicts(
        tmp_path / ".artha-decrypted", hostnames=HOSTS
    ) == [tmp_path / ".artha-decrypted-SYNTH-HOST-AB12"]


@pytest.mark.parametrize(
    "name",
    [
        "health-metrics.md",
        "health-check.md",
        "work-career.md.age",
        "postmortem-2026-04-09-workiq-hang.md",
    ],
)
def test_does_not_match_unrelated_files_that_share_a_prefix(tmp_path, name):
    target = tmp_path / "health.md"
    _touch(tmp_path, target.name, name, "finance.md")
    assert find_conflicts(target, hostnames=HOSTS) == []


def test_ignores_registered_targets_and_display_only_allowlist(tmp_path):
    target = tmp_path / "health.md"
    conflict = tmp_path / "health-SYNTH-HOST-AB12.md"
    registered = tmp_path / "health-metrics.md"
    _touch(tmp_path, target.name, conflict.name, registered.name)

    assert find_conflicts(
        target,
        hostnames=HOSTS,
        canonical_targets=(target, registered),
    ) == [conflict]
    assert find_conflicts(
        target,
        hostnames=HOSTS,
        canonical_targets=(target, registered),
        ignored=(conflict,),
    ) == []


def test_scan_error_is_unknown_not_clean(tmp_path, monkeypatch):
    target = tmp_path / "health.md"
    _touch(tmp_path, target.name)
    monkeypatch.setattr(
        "lib.sync_conflicts.os.scandir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("synthetic")),
    )
    with pytest.raises(ConflictScanUnknown, match="could not be scanned"):
        find_conflicts(target, hostnames=HOSTS)


def _found(tmp_path, target, *copies, hostnames=("UNRELATED-HOST",)):
    _touch(tmp_path, target, *copies)
    return {p.name for p in find_conflicts(tmp_path / target, hostnames=hostnames)}


def test_manifest_copies_use_the_stem_before_the_extension(tmp_path):
    copies = (".handoff-SYNTH-HOST-AB12.json", ".handoff (1).json", ".handoff-SYNTH-HOST-AB12-2.json")
    assert _found(tmp_path, ".handoff.json", *copies) == set(copies)


def test_a_first_copy_from_a_host_the_detector_never_heard_of_is_found(tmp_path):
    assert _found(tmp_path, "goals.md", "goals-REMOTE-HOST-Q7.md") == {"goals-REMOTE-HOST-Q7.md"}
    assert _found(tmp_path, "finance.md.age", "finance-REMOTE-HOST-Q7.md.age") == {
        "finance-REMOTE-HOST-Q7.md.age"
    }


def test_dated_and_lowercase_sibling_names_are_not_conflicts(tmp_path):
    assert _found(tmp_path, "goals.md", "goals-2026-11.md", "goals-archive.md") == set()
    assert _found(tmp_path, "finance.md.age", "finance-2026-11.md.age") == set()
    assert _found(tmp_path, "health.md", "health-check.md", "health-metrics.md") == set()
