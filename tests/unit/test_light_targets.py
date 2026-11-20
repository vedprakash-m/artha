from __future__ import annotations

from lib.state_targets import light_markdown_targets


def test_light_targets_are_explicit_and_exclude_work_and_non_markdown(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "light_mode.yaml").write_text(
        "state_files:\n"
        "  - state/goals.md\n"
        "  - state/finance.md.age\n"
        "  - state/gallery.yaml\n"
        "  - state/work/work-goals.md\n",
        encoding="utf-8",
    )
    targets = light_markdown_targets(tmp_path)
    assert targets == (
        tmp_path / "state" / "finance.md.age",
        tmp_path / "state" / "goals.md",
    )


def test_missing_allowlist_falls_back_to_narrow_registered_core(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "state_registry.yaml").write_text(
        'state_files:\n  - path: "state/goals.md"\n  - path: "state/gallery.yaml"\n'
    )
    assert light_markdown_targets(tmp_path) == (tmp_path / "state" / "goals.md",)
