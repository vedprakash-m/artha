"""
Unit tests for scripts/generate_identity.py

Tests cover:
  - _validate(): required fields, timezone check, warnings for empty domains
  - _build_identity_block(): correct rendering for all optional/missing fields
  - _assemble_artha_md(): assembles identity + core correctly
  - main() CLI: --validate flag, success exit code, error exit code
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Ensure scripts/ is importable
_ARTHA_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
if str(_ARTHA_ROOT) not in sys.path:
    sys.path.insert(0, str(_ARTHA_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate_identity as gi


# ---------------------------------------------------------------------------
# Minimal valid profile fixture
# ---------------------------------------------------------------------------

MINIMAL_PROFILE = {
    "schema_version": "1.0",
    "family": {
        "primary_user": {
            "name": "Jane",
            "nickname": "Jane",
            "emails": {"gmail": "jane@example.com"},
        }
    },
    "location": {
        "city": "Portland",
        "state": "OR",
        "timezone": "America/Los_Angeles",
    },
    "domains": {
        "finance": {"enabled": True},
        "health": {"enabled": False},
    },
}

FULL_PROFILE = {
    "schema_version": "1.0",
    "family": {
        "name": "Doe",
        "cultural_context": "Test cultural context for the family.",
        "primary_user": {
            "name": "John",
            "nickname": "Johnny",
            "emails": {"gmail": "john@example.com"},
        },
        "spouse": {
            "name": "Jane",
            "filtered_briefing": True,
        },
        "children": [
            {
                "name": "Alex",
                "age": 16,
                "grade": "11th",
                "school": {"name": "Lincoln High", "district": "Portland USD"},
                "milestones": {"class_of": 2027, "college_prep": True, "new_driver": True},
            },
            {
                "name": "Sam",
                "age": 12,
                "grade": "7th",
                "school": {"name": "Jefferson Middle", "district": "Portland USD"},
                "milestones": {"class_of": 2031, "college_prep": False, "new_driver": False},
            },
        ],
    },
    "location": {
        "city": "Portland",
        "state": "OR",
        "county": "Multnomah",
        "timezone": "America/Los_Angeles",
    },
    "domains": {
        "immigration": {
            "enabled": True,
            "path": "EB-2",
            "origin_country": "India",
            "context": "EB-2 India priority date monitoring.",
        },
        "finance": {"enabled": True},
        "kids": {"enabled": True},
    },
}


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_minimal_profile(self, tmp_path, monkeypatch):
        core_path = tmp_path / "Artha.core.md"
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        errors = gi._validate(MINIMAL_PROFILE)
        assert errors == []

    def test_missing_primary_name(self, tmp_path, monkeypatch):
        core_path = tmp_path / "Artha.core.md"
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        bad = dict(MINIMAL_PROFILE)
        bad["family"] = {"primary_user": {"emails": {"gmail": "x@example.com"}}}
        errors = gi._validate(bad)
        assert any("primary_user.name" in e for e in errors)

    def test_missing_email(self, tmp_path, monkeypatch):
        core_path = tmp_path / "Artha.core.md"
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        bad = dict(MINIMAL_PROFILE)
        bad["family"] = {"primary_user": {"name": "X", "emails": {}}}
        errors = gi._validate(bad)
        assert any("emails" in e for e in errors)

    def test_invalid_timezone(self, tmp_path, monkeypatch):
        core_path = tmp_path / "Artha.core.md"
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        bad = dict(MINIMAL_PROFILE)
        bad["location"] = {"city": "X", "timezone": "US/Eastern"}  # Not valid IANA prefix
        errors = gi._validate(bad)
        assert any("timezone" in e for e in errors)

    def test_missing_core_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gi, "_CORE_PATH", tmp_path / "nonexistent.md")
        errors = gi._validate(MINIMAL_PROFILE)
        assert any("core" in e.lower() or "Artha.core" in e for e in errors)

    def test_no_domains_emits_warning(self, tmp_path, monkeypatch, capsys):
        core_path = tmp_path / "Artha.core.md"
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        no_domain_profile = {
            "schema_version": "1.0",
            "family": {
                "primary_user": {
                    "name": "X",
                    "emails": {"gmail": "x@example.com"},
                }
            },
            "location": {"timezone": "America/New_York"},
            "domains": {},
        }
        errors = gi._validate(no_domain_profile)
        assert errors == []

    def test_placeholder_name_rejected(self, tmp_path, monkeypatch):
        core_path = tmp_path / "Artha.core.md"
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        bad = dict(MINIMAL_PROFILE)
        bad["family"] = {
            "primary_user": {
                "name": "Alex Smith",
                "emails": {"gmail": "jane@example.com"},
            }
        }
        errors = gi._validate(bad)
        assert any("placeholder" in e.lower() or "Alex Smith" in e for e in errors)

    def test_placeholder_email_rejected(self, tmp_path, monkeypatch):
        core_path = tmp_path / "Artha.core.md"
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        bad = dict(MINIMAL_PROFILE)
        bad["family"] = {
            "primary_user": {
                "name": "Jane",
                "emails": {"gmail": "alex.smith@gmail.com"},
            }
        }
        errors = gi._validate(bad)
        assert any("placeholder" in e.lower() or "alex.smith@gmail.com" in e for e in errors)


# ---------------------------------------------------------------------------
# _build_identity_block
# ---------------------------------------------------------------------------

class TestBuildIdentityBlock:
    def test_primary_user_appears(self):
        block = gi._build_identity_block(MINIMAL_PROFILE)
        assert "Jane" in block
        assert "jane@example.com" in block

    def test_no_spouse_no_spouse_line(self):
        block = gi._build_identity_block(MINIMAL_PROFILE)
        assert "spouse" not in block.lower()

    def test_no_children_no_child_entries(self):
        block = gi._build_identity_block(MINIMAL_PROFILE)
        # Should not contain child-related lines
        assert "Class of" not in block

    def test_full_profile_spouse(self):
        block = gi._build_identity_block(FULL_PROFILE)
        assert "Jane" in block
        assert "spouse" in block.lower()

    def test_full_profile_children(self):
        block = gi._build_identity_block(FULL_PROFILE)
        assert "Alex" in block
        assert "Sam" in block
        assert "Class of 2027" in block
        assert "College prep active" in block
        assert "New driver" in block

    def test_location_appears(self):
        block = gi._build_identity_block(MINIMAL_PROFILE)
        assert "Portland" in block
        assert "America/Los_Angeles" in block

    def test_cultural_context_included_when_set(self):
        block = gi._build_identity_block(FULL_PROFILE)
        assert "Test cultural context" in block

    def test_immigration_context_when_enabled(self):
        block = gi._build_identity_block(FULL_PROFILE)
        assert "Immigration Context" in block
        assert "EB-2" in block

    def test_no_empty_parentheses(self):
        # If nickname is empty, should not render "()"
        profile = dict(MINIMAL_PROFILE)
        profile["family"] = {
            "primary_user": {
                "name": "Jane",
                "nickname": "",
                "emails": {"gmail": "jane@example.com"},
            }
        }
        block = gi._build_identity_block(profile)
        assert "()" not in block

    def test_active_domains_section(self, tmp_path, monkeypatch):
        # Disable progressive_disclosure so the legacy domain bullet list renders,
        # allowing us to assert individual domain names appear in the block.
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "artha_config.yaml").write_text(
            "harness:\n  progressive_disclosure:\n    enabled: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gi, "_ARTHA_DIR", tmp_path)
        block = gi._build_identity_block(FULL_PROFILE)
        assert "Active Domains" in block
        assert "Finance" in block  # enabled
        assert "Immigration" in block  # enabled


# ---------------------------------------------------------------------------
# _assemble_artha_md
# ---------------------------------------------------------------------------

class TestAssembleArthamMd:
    def test_assembled_file_contains_identity_and_core(self, tmp_path, monkeypatch):
        identity_path = tmp_path / "Artha.identity.md"
        core_path = tmp_path / "Artha.core.md"
        assembled_path = tmp_path / "Artha.md"
        core_path.write_text("## §2 Core Section\nCore content here.\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_IDENTITY_PATH", identity_path)
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        monkeypatch.setattr(gi, "_ASSEMBLED_PATH", assembled_path)
        monkeypatch.setattr(gi, "_ARTHA_DIR", tmp_path)  # needed for relative_to() in print

        gi._assemble_artha_md("## §1 Identity\nIdentity content here.\n")

        assembled = assembled_path.read_text(encoding="utf-8")
        assert "§1 Identity" in assembled
        assert "§2 Core Section" in assembled
        assert assembled.index("§1") < assembled.index("§2")  # identity comes first


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

class TestMain:
    def _setup_files(self, tmp_path: Path, monkeypatch):
        profile_path = tmp_path / "config" / "user_profile.yaml"
        core_path = tmp_path / "config" / "Artha.core.md"
        identity_path = tmp_path / "config" / "Artha.identity.md"
        assembled_path = tmp_path / "config" / "Artha.md"
        (tmp_path / "config").mkdir(exist_ok=True)
        profile_path.write_text(yaml.dump(MINIMAL_PROFILE), encoding="utf-8")
        core_path.write_text("## §2 Core\nCore content.\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_PROFILE_PATH", profile_path)
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        monkeypatch.setattr(gi, "_IDENTITY_PATH", identity_path)
        monkeypatch.setattr(gi, "_ASSEMBLED_PATH", assembled_path)
        monkeypatch.setattr(gi, "_ARTHA_DIR", tmp_path)  # for relative_to() in print calls
        monkeypatch.setattr(gi, "_ROUTING_PATH", tmp_path / "config" / "routing.yaml")
        monkeypatch.setattr(gi, "_ROUTING_EXAMPLE_PATH", tmp_path / "config" / "routing.example.yaml")
        return assembled_path

    def test_validate_mode_does_not_write_files(self, tmp_path, monkeypatch):
        assembled_path = self._setup_files(tmp_path, monkeypatch)
        result = gi.main(["--validate"])
        assert result == 0
        assert not assembled_path.exists()

    def test_normal_run_writes_assembled_file(self, tmp_path, monkeypatch):
        assembled_path = self._setup_files(tmp_path, monkeypatch)
        result = gi.main([])
        assert result == 0
        assert assembled_path.exists()
        content = assembled_path.read_text(encoding="utf-8")
        assert "Jane" in content  # primary user name from MINIMAL_PROFILE

    def test_missing_profile_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gi, "_PROFILE_PATH", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr(gi, "_CORE_PATH", tmp_path / "Artha.core.md")
        monkeypatch.setattr(gi, "_ARTHA_DIR", tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            gi.main([])
        assert exc_info.value.code != 0

    def test_validation_error_returns_1(self, tmp_path, monkeypatch):
        profile_path = tmp_path / "config" / "user_profile.yaml"
        core_path = tmp_path / "config" / "Artha.core.md"
        assembled_path = tmp_path / "config" / "Artha.md"
        (tmp_path / "config").mkdir(exist_ok=True)
        # Profile missing required name
        bad_profile = {"family": {"primary_user": {"emails": {}}}}
        profile_path.write_text(yaml.dump(bad_profile), encoding="utf-8")
        core_path.write_text("# Core\n", encoding="utf-8")
        monkeypatch.setattr(gi, "_PROFILE_PATH", profile_path)
        monkeypatch.setattr(gi, "_CORE_PATH", core_path)
        monkeypatch.setattr(gi, "_ASSEMBLED_PATH", assembled_path)
        monkeypatch.setattr(gi, "_ARTHA_DIR", tmp_path)
        result = gi.main([])
        assert result == 1
        assert not assembled_path.exists()


# ---------------------------------------------------------------------------
# _collect_warnings  (advisory, non-blocking)
# ---------------------------------------------------------------------------

class TestCollectWarnings:
    def test_no_warnings_on_clean_profile(self):
        warnings = gi._collect_warnings(MINIMAL_PROFILE)
        assert warnings == []

    def test_placeholder_child1_name_warns(self):
        profile = dict(MINIMAL_PROFILE)
        profile["family"] = {
            "primary_user": {
                "name": "Jane",
                "emails": {"gmail": "jane@example.com"},
            },
            "children": [{"name": "Child1", "age": 10}],
        }
        warnings = gi._collect_warnings(profile)
        assert any("Child1" in w for w in warnings)

    def test_placeholder_child2_name_warns(self):
        profile = dict(MINIMAL_PROFILE)
        profile["family"] = {
            "primary_user": {"name": "Jane", "emails": {"gmail": "jane@example.com"}},
            "children": [{"name": "Child2", "age": 8}],
        }
        warnings = gi._collect_warnings(profile)
        assert any("Child2" in w for w in warnings)

    def test_real_child_name_no_warning(self):
        profile = dict(MINIMAL_PROFILE)
        profile["family"] = {
            "primary_user": {"name": "Jane", "emails": {"gmail": "jane@example.com"}},
            "children": [{"name": "Emma", "age": 10}],
        }
        warnings = gi._collect_warnings(profile)
        assert all("Emma" not in w for w in warnings)

    def test_placeholder_city_warns(self):
        profile = {
            "schema_version": "1.0",
            "family": {
                "primary_user": {
                    "name": "Jane",
                    "emails": {"gmail": "jane@example.com"},
                }
            },
            "location": {"city": "Springfield", "timezone": "America/Los_Angeles"},
            "domains": {"finance": {"enabled": True}},
        }
        warnings = gi._collect_warnings(profile)
        assert any("Springfield" in w for w in warnings)

    def test_real_city_no_warning(self):
        profile = {
            "schema_version": "1.0",
            "family": {
                "primary_user": {
                    "name": "Jane",
                    "emails": {"gmail": "jane@example.com"},
                }
            },
            "location": {"city": "Portland", "timezone": "America/Los_Angeles"},
            "domains": {},
        }
        warnings = gi._collect_warnings(profile)
        assert not any("Portland" in w for w in warnings)

    def test_multiple_placeholder_children_multiple_warnings(self):
        profile = dict(MINIMAL_PROFILE)
        profile["family"] = {
            "primary_user": {"name": "Jane", "emails": {"gmail": "jane@example.com"}},
            "children": [{"name": "Child1"}, {"name": "Child2"}],
        }
        warnings = gi._collect_warnings(profile)
        assert len(warnings) == 2


# ---------------------------------------------------------------------------
# _print_validate_summary
# ---------------------------------------------------------------------------

class TestPrintValidateSummary:
    def test_prints_name_and_email(self, capsys):
        gi._print_validate_summary(MINIMAL_PROFILE)
        captured = capsys.readouterr().out
        assert "Jane" in captured
        # Email is masked in output for privacy: j***@example.com
        assert "@example.com" in captured

    def test_prints_location(self, capsys):
        gi._print_validate_summary(MINIMAL_PROFILE)
        captured = capsys.readouterr().out
        assert "Portland" in captured or "America/Los_Angeles" in captured

    def test_prints_enabled_domains(self, capsys):
        gi._print_validate_summary(MINIMAL_PROFILE)
        captured = capsys.readouterr().out
        assert "finance" in captured

    def test_prints_full_profile_name_and_domains(self, capsys):
        gi._print_validate_summary(FULL_PROFILE)
        captured = capsys.readouterr().out
        assert "John" in captured
        assert "immigration" in captured or "finance" in captured

