"""RF-17 command matrix and generated mapping parity."""
from __future__ import annotations

from pathlib import Path

from command_manifest import check_command_matrix, load_command_matrix, render_router_table
import generate_identity

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_matrix_contains_current_command_surface():
    rows = load_command_matrix()
    bare = {row.bare for row in rows}
    assert any("brief" in value and "catch me up" in value for value in bare)
    assert any("scope-review" in value for value in bare)
    assert any("show everything" in value for value in bare)
    assert check_command_matrix() == []


def test_colon_commands_and_deprecated_slash_forms_are_separate():
    rows = load_command_matrix()
    assert all(not row.bare.lstrip().startswith("/") for row in rows)
    slash = {row.slash for row in rows}
    assert any("/brief" in value for value in slash)
    colon = {row.colon for row in rows}
    assert any("brief:flash" in value for value in colon)
    assert any("items:done" in value for value in colon)


def test_core_is_a_pointer_to_the_single_matrix_and_preserves_flags():
    core = (ROOT / "config" / "Artha.core.md").read_text(encoding="utf-8")
    assert "config/commands.md` is the single command matrix" in core
    assert "--read-only --advisory" in core
    assert "--read-only" in core
    assert "owner-terminal-only" not in core or "scope-review" in core


def test_generated_router_is_rendered_from_the_matrix():
    rendered = render_router_table()
    assert rendered == generate_identity._command_router_table()
    assert "`scope-review <source>`" in rendered
    assert "`show everything`" in rendered
    assert "/brief" not in rendered
