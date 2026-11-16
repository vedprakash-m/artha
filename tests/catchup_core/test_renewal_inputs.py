"""Synthetic input contracts; eligibility comparison never grants release authority."""
from __future__ import annotations

import json

import pytest

from catchup_core.renewal_inputs import InputRejected, SourceFile, SourceManifest, compare_sources


def encoded(entries=None, **changes):
    document = {
        "schema": "artha.renewal-source.v1",
        "files": entries if entries is not None else [
            {"path": "scripts/example.py", "sha256": "a" * 64, "mode": "100644"},
        ],
    }
    document.update(changes)
    return json.dumps(document).encode()


def test_AR06_exact_source_changes_are_compared_against_the_root():
    root = SourceManifest.parse(encoded())
    changed = SourceManifest.parse(encoded([
        {"path": "scripts/example.py", "sha256": "b" * 64, "mode": "100644"},
    ]))
    assert compare_sources(root, root, routine=frozenset(), protected=frozenset()).changed == ()
    result = compare_sources(root, changed, routine=frozenset({"scripts/example.py"}), protected=frozenset())
    assert result.changed == ("scripts/example.py",)
    assert result.held == ()
    assert root.digest != changed.digest


def test_AR12_prior_renewal_cannot_hide_cumulative_protected_changes():
    root = SourceManifest.parse(encoded())
    target = SourceManifest.parse(encoded([
        {"path": "scripts/example.py", "sha256": "b" * 64, "mode": "100644"},
    ]))
    result = compare_sources(root, target, routine=frozenset({"scripts/example.py"}),
                             protected=frozenset({"scripts/example.py"}))
    assert result.held == ("scripts/example.py",)


@pytest.mark.parametrize("mode", ["100755", "120000", "160000"])
def test_AR06_mode_or_object_kind_is_not_an_ordinary_content_edit(mode):
    root = SourceManifest.parse(encoded())
    if mode != "100755":
        with pytest.raises(InputRejected):
            SourceManifest.parse(encoded([{"path": "scripts/example.py", "sha256": "a" * 64, "mode": mode}]))
        return
    target = SourceManifest.parse(encoded([{"path": "scripts/example.py", "sha256": "a" * 64, "mode": mode}]))
    assert compare_sources(root, target, routine=frozenset({"scripts/example.py"}),
                           protected=frozenset()).held == ("scripts/example.py",)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../b", "a//b", "a\\b", "a/./b", "a\x00b"])
def test_AR13_unsafe_paths_refuse_without_payload_text(path):
    with pytest.raises(InputRejected, match="^invalid renewal source manifest$"):
        SourceManifest.parse(encoded([{"path": path, "sha256": "a" * 64, "mode": "100644"}]))


@pytest.mark.parametrize("raw", [
    b'{"schema":"artha.renewal-source.v1","schema":"x","files":[]}',
    b'{"schema":"artha.renewal-source.v1","files":[],"extra":"CANARY"}',
    b'{"schema":"artha.renewal-source.v1","files":NaN}',
    b'\xff', b'null', b'[]', b'{', b'x' * (1024 * 1024 + 1),
])
def test_AR13_malformed_or_oversized_objects_refuse(raw):
    with pytest.raises(InputRejected, match="^invalid renewal source manifest$"):
        SourceManifest.parse(raw)


@pytest.mark.parametrize("files", [
    [{"path": "a.py", "sha256": "a" * 64, "mode": "100644"}] * 2,
    [{"path": "A.py", "sha256": "a" * 64, "mode": "100644"},
     {"path": "a.py", "sha256": "b" * 64, "mode": "100644"}],
    [{"path": "a.py", "sha256": "a" * 63, "mode": "100644"}],
    [{"path": "a.py", "sha256": "A" * 64, "mode": "100644"}],
    [{"path": "a.py", "sha256": "a" * 64, "mode": True}],
    [{"path": "a.py", "sha256": "a" * 64, "mode": "100644", "extra": "CANARY"}],
    ["CANARY"],
])
def test_AR13_duplicate_or_invalid_file_records_refuse(files):
    with pytest.raises(InputRejected, match="^invalid renewal source manifest$"):
        SourceManifest.parse(encoded(files))


def test_AR06_addition_and_deletion_require_review_even_in_a_routine_component():
    root = SourceManifest.parse(encoded())
    empty = SourceManifest.parse(encoded([]))
    options = {"routine": frozenset({"scripts/example.py"}), "protected": frozenset()}
    assert compare_sources(root, empty, **options).held == ("scripts/example.py",)
    assert compare_sources(empty, root, **options).held == ("scripts/example.py",)


def test_AR13_canonical_encoding_is_stable_and_immutable():
    a = {"path": "b.py", "sha256": "b" * 64, "mode": "100644"}
    b = {"path": "a.py", "sha256": "a" * 64, "mode": "100644"}
    first, second = SourceManifest.parse(encoded([a, b])), SourceManifest.parse(encoded([b, a]))
    assert first.digest == second.digest
    assert SourceManifest.parse(first.encode()) == first
    with pytest.raises((AttributeError, TypeError)):
        first.files[0].path = "modified"


def test_AR03_unknown_changes_remain_held():
    root = SourceManifest.parse(encoded())
    target = SourceManifest.parse(encoded([
        {"path": "scripts/example.py", "sha256": "b" * 64, "mode": "100644"},
    ]))
    assert compare_sources(root, target, routine=frozenset(), protected=frozenset()).held


def test_AR13_direct_construction_obeys_encoding_and_policy_bounds():
    with pytest.raises(InputRejected):
        SourceManifest(tuple(SourceFile(f"{number:04}/" + "a" * 1000, "a" * 64, "100644")
                             for number in range(1000)))
    root = SourceManifest.parse(encoded())
    with pytest.raises(InputRejected):
        compare_sources(root, root, routine=frozenset({"../unsafe"}), protected=frozenset())
