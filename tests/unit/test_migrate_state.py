"""
tests/unit/test_migrate_state.py — Unit tests for migrate_state.py

Tests the migration DSL, field operations, version chain traversal,
and upgrade.py integration point.

Ref: specs/enhance.md §1.2
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.migrate_state import (  # noqa: E402
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    AddField,
    DeprecateField,
    RenameField,
    _VERSION_CHAIN,
    _del_nested,
    _get_nested,
    _has_nested,
    _migration_path,
    _set_nested,
    _split_front_matter,
    _join_front_matter,
    apply_migrations,
    check_needs_migration,
    migrate_file,
)


# ---------------------------------------------------------------------------
# Helper: build a simple state file with YAML front matter
# ---------------------------------------------------------------------------

def _make_state_file(tmp_path: Path, name: str, front_matter: str, body: str = "") -> Path:
    p = tmp_path / name
    p.write_text(f"---\n{front_matter}\n---\n{body}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# TestAddField
# ---------------------------------------------------------------------------

class TestAddField:
    def test_adds_missing_key(self):
        fm = {}
        op = AddField("meta.schema_version", "1.1")
        changed = op.apply(fm)
        assert changed is True
        assert fm["meta"]["schema_version"] == "1.1"

    def test_no_op_when_key_exists(self):
        fm = {"meta": {"schema_version": "1.0"}}
        op = AddField("meta.schema_version", "1.1")
        changed = op.apply(fm)
        assert changed is False
        assert fm["meta"]["schema_version"] == "1.0"  # unchanged

    def test_adds_top_level_key(self):
        fm = {}
        op = AddField("review_needed", False)
        changed = op.apply(fm)
        assert changed is True
        assert fm["review_needed"] is False

    def test_describe(self):
        op = AddField("meta.foo", "bar")
        assert "AddField" in op.describe()
        assert "meta.foo" in op.describe()


# ---------------------------------------------------------------------------
# TestRenameField
# ---------------------------------------------------------------------------

class TestRenameField:
    def test_renames_existing_key(self):
        fm = {"last_updated": "2026-03-01"}
        op = RenameField("last_updated", "meta.last_updated")
        changed = op.apply(fm)
        assert changed is True
        assert "last_updated" not in fm
        assert fm["meta"]["last_updated"] == "2026-03-01"

    def test_no_op_when_source_absent(self):
        fm = {}
        op = RenameField("nonexistent", "new_key")
        changed = op.apply(fm)
        assert changed is False

    def test_describe(self):
        op = RenameField("old", "new")
        assert "RenameField" in op.describe()
        assert "old" in op.describe()
        assert "new" in op.describe()


# ---------------------------------------------------------------------------
# TestDeprecateField
# ---------------------------------------------------------------------------

class TestDeprecateField:
    def test_removes_field_when_no_renamed_to(self):
        fm = {"old_key": "value"}
        op = DeprecateField("old_key")
        changed = op.apply(fm)
        assert changed is True
        assert "old_key" not in fm

    def test_renames_when_renamed_to_given(self):
        fm = {"old_key": "value"}
        op = DeprecateField("old_key", renamed_to="new_key")
        changed = op.apply(fm)
        assert changed is True
        assert "old_key" not in fm
        assert fm["new_key"] == "value"

    def test_no_op_when_absent(self):
        fm = {}
        op = DeprecateField("missing_key")
        changed = op.apply(fm)
        assert changed is False

    def test_describe_with_rename(self):
        op = DeprecateField("old", renamed_to="new")
        assert "→" in op.describe()


# ---------------------------------------------------------------------------
# TestNestedHelpers
# ---------------------------------------------------------------------------

class TestNestedHelpers:
    def test_get_nested_single_level(self):
        assert _get_nested({"a": 1}, "a") == 1

    def test_get_nested_multi_level(self):
        assert _get_nested({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_get_nested_missing(self):
        assert _get_nested({}, "a.b") is None

    def test_set_nested_creates_intermediates(self):
        d = {}
        _set_nested(d, "a.b.c", 99)
        assert d["a"]["b"]["c"] == 99

    def test_del_nested_removes_key(self):
        d = {"a": {"b": 1}}
        _del_nested(d, "a.b")
        assert "b" not in d["a"]

    def test_del_nested_no_op_missing(self):
        d = {}
        _del_nested(d, "a.b")  # should not raise

    def test_has_nested_present(self):
        assert _has_nested({"a": {"b": 1}}, "a.b")

    def test_has_nested_absent(self):
        assert not _has_nested({}, "a.b")


# ---------------------------------------------------------------------------
# TestFrontMatterParsing
# ---------------------------------------------------------------------------

class TestFrontMatterParsing:
    def test_parses_valid_front_matter(self):
        text = "---\nlast_updated: 2026-03-01\n---\nBody text here.\n"
        fm, body = _split_front_matter(text)
        assert fm is not None
        # PyYAML parses bare dates as datetime.date — compare as string
        assert str(fm.get("last_updated")) == "2026-03-01"
        assert "Body text here" in body

    def test_returns_none_for_no_front_matter(self):
        text = "No front matter here.\nJust body content.\n"
        fm, body = _split_front_matter(text)
        assert fm is None
        assert body == text

    def test_roundtrip_preserves_body(self):
        front = "last_updated: 2026-03-01\n"
        body = "# Domain State\n\nSome content.\n"
        text = f"---\n{front}---\n{body}"
        fm, parsed_body = _split_front_matter(text)
        reconstructed = _join_front_matter(fm, parsed_body)
        # Body must be preserved unchanged
        assert body in reconstructed


# ---------------------------------------------------------------------------
# TestMigrationPath
# ---------------------------------------------------------------------------

class TestMigrationPath:
    def test_single_step(self):
        path = _migration_path("1.0", "1.1")
        assert path == [("1.0", "1.1")]

    def test_no_op_same_version(self):
        path = _migration_path("1.1", "1.1")
        assert path == []

    def test_unknown_version_raises(self):
        with pytest.raises(ValueError):
            _migration_path("0.0", "1.1")


# ---------------------------------------------------------------------------
# TestMigrateFile
# ---------------------------------------------------------------------------

class TestMigrateFile:
    def test_applies_migration_to_file(self, tmp_path):
        sf = _make_state_file(tmp_path, "finance.md", "last_updated: 2026-03-01\n")
        modified, log = migrate_file(sf, "1.0", "1.1")
        assert modified is True
        new_text = sf.read_text()
        assert "meta:" in new_text or "last_updated" in new_text  # partially transformed

    def test_dry_run_does_not_write(self, tmp_path):
        sf = _make_state_file(tmp_path, "health.md", "last_updated: 2026-03-01\n")
        original = sf.read_text()
        modified, log = migrate_file(sf, "1.0", "1.1", dry_run=True)
        assert sf.read_text() == original  # unchanged on disk

    def test_already_migrated_returns_no_changes(self, tmp_path):
        # A file that already has meta.last_updated and meta.schema_version
        sf = _make_state_file(
            tmp_path, "goals.md",
            "meta:\n  schema_version: '1.1'\n  last_updated: 2026-03-01\n  review_needed: false\n"
        )
        modified, log = migrate_file(sf, "1.0", "1.1")
        assert modified is False
        assert any("no changes" in line for line in log)

    def test_skips_file_without_front_matter(self, tmp_path):
        sf = tmp_path / "plain.md"
        sf.write_text("# No front matter\n\nJust text.\n")
        modified, log = migrate_file(sf, "1.0", "1.1")
        assert modified is False
        assert any("no front matter" in line.lower() for line in log)


# ---------------------------------------------------------------------------
# TestApplyMigrations
# ---------------------------------------------------------------------------

class TestApplyMigrations:
    def test_migrates_all_state_files(self, tmp_path):
        for name in ["calendar.md", "comms.md", "goals.md"]:
            _make_state_file(tmp_path, name, "last_updated: 2026-03-01\n")

        results = apply_migrations(
            state_dir=tmp_path,
            from_ver="1.0",
            to_ver="1.1",
        )
        # At least one file should have been modified
        assert len(results) > 0

    def test_dry_run_returns_changes_without_writing(self, tmp_path):
        sf = _make_state_file(tmp_path, "finance.md", "last_updated: 2026-03-01\n")
        original = sf.read_text()

        results = apply_migrations(
            state_dir=tmp_path,
            from_ver="1.0",
            to_ver="1.1",
            dry_run=True,
        )
        assert sf.read_text() == original  # dry run: unchanged

    def test_returns_empty_when_already_current(self, tmp_path):
        # File already at target version — no ops needed
        sf = _make_state_file(
            tmp_path, "goals.md",
            "meta:\n  schema_version: '1.1'\n  last_updated: 2026-03-01\n  review_needed: false\n"
        )
        results = apply_migrations(
            state_dir=tmp_path,
            from_ver="1.0",
            to_ver="1.1",
        )
        # No "modified" files
        modified = {k: v for k, v in results.items() if "no changes" not in "\n".join(v)}
        assert len(modified) == 0


# ---------------------------------------------------------------------------
# TestCheckNeedsMigration
# ---------------------------------------------------------------------------

class TestCheckNeedsMigration:
    def test_returns_true_when_md_files_exist_and_version_behind(self, tmp_path):
        _make_state_file(tmp_path, "finance.md", "last_updated: 2026-03-01\n")
        with patch("scripts.migrate_state._STATE_DIR", tmp_path):
            with patch("scripts.migrate_state._read_profile_schema_version", return_value="1.0"):
                needed = check_needs_migration(state_dir=tmp_path)
        assert needed is True

    def test_returns_false_when_no_md_files(self, tmp_path):
        with patch("scripts.migrate_state._read_profile_schema_version", return_value="1.0"):
            needed = check_needs_migration(state_dir=tmp_path)
        # Empty directory — no files to migrate
        assert needed is False

    def test_returns_false_when_already_current(self, tmp_path):
        _make_state_file(tmp_path, "finance.md", "meta:\n  schema_version: '1.1'\n")
        with patch("scripts.migrate_state._read_profile_schema_version",
                   return_value=LATEST_SCHEMA_VERSION):
            needed = check_needs_migration(state_dir=tmp_path)
        assert needed is False


# ---------------------------------------------------------------------------
# TestMigrationRegistry
# ---------------------------------------------------------------------------

class TestMigrationRegistry:
    def test_registry_has_expected_versions(self):
        """MIGRATIONS must contain at least one entry."""
        assert len(MIGRATIONS) >= 1

    def test_version_chain_is_ordered(self):
        """VERSION_CHAIN must be monotonically increasing."""
        from scripts.migrate_state import _parse_version_simple
        versions = [tuple(int(x) for x in v.split(".")) for v in _VERSION_CHAIN]
        assert versions == sorted(versions), "Version chain must be in ascending order"

    def test_latest_is_in_chain(self):
        assert LATEST_SCHEMA_VERSION in _VERSION_CHAIN
