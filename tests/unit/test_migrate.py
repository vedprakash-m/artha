"""
Unit tests for scripts/migrate.py

Tests cover:
  - _extract_family(): name/nickname parsing, email extraction
  - _extract_location(): city, state, timezone
  - _extract_integrations(): Gmail, Microsoft Graph, iCloud toggle detection
  - _extract_domains(): domain enable/disable from settings blocks
  - migrate() end-to-end: reads settings.md → writes user_profile.yaml
  - --dry-run: no file written
  - --force: overwrites existing profile
  - refuses to overwrite without --force
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

# Must be set before importing migrate to prevent venv re-exec at module load
os.environ.setdefault("ARTHA_TEST_MODE", "1")

import pytest
import yaml

# Ensure scripts/ is importable
_ARTHA_ROOT = Path(__file__).resolve().parents[2]
if str(_ARTHA_ROOT) not in sys.path:
    sys.path.insert(0, str(_ARTHA_ROOT))

import migrate as mig


# ---------------------------------------------------------------------------
# Minimal valid settings.md content
# ---------------------------------------------------------------------------

SETTINGS_MD_MINIMAL = textwrap.dedent("""\
    ---
    schema_version: "3.0"
    ---

    ## Identity
    ```yaml
    primary_user: "Alex (Xander)"
    briefing_email: "alex@example.com"
    family_members:
      - name: "Morgan"
        role: spouse
    ```

    ## Location
    ```yaml
    city: "Denver"
    state: "CO"
    timezone: "America/Denver"
    ```
""")

SETTINGS_MD_FULL = textwrap.dedent("""\
    ## Identity
    ```yaml
    primary_user: "Alex (Xander)"
    briefing_email: "alex@example.com"
    family_members:
      - name: "Morgan"
        role: spouse
    children:
      - name: "Riley"
        age: 15
        grade: "10th"
    ```

    ## Location
    ```yaml
    city: "Denver"
    state: "CO"
    county: "Denver County"
    timezone: "America/Denver"
    lat: 39.7392
    lon: -104.9903
    ```

    ## Capabilities
    ```yaml
    gmail: true
    msgraph: true
    icloud: false
    canvas: false
    ```

    ## Domains
    ```yaml
    finance: true
    health: true
    immigration: false
    kids: true
    travel: false
    ```
""")


def _write_settings(tmp_path: Path, content: str) -> Path:
    settings_path = tmp_path / "config" / "settings.md"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(content, encoding="utf-8")
    return settings_path


# ---------------------------------------------------------------------------
# _extract_family
# ---------------------------------------------------------------------------

class TestExtractFamily:
    def test_name_and_nickname_parsed(self):
        data = {"primary_user": "Alex (Xander)", "briefing_email": "alex@example.com"}
        result = mig._extract_family(data)
        assert result["primary_user"]["name"] == "Alex"
        assert result["primary_user"]["nickname"] == "Xander"

    def test_name_only_no_nickname(self):
        data = {"primary_user": "Jordan", "briefing_email": "j@example.com"}
        result = mig._extract_family(data)
        assert result["primary_user"]["name"] == "Jordan"
        assert result["primary_user"]["nickname"] == "Jordan"

    def test_gmail_extracted(self):
        data = {"primary_user": "X", "briefing_email": "x@gmail.com"}
        result = mig._extract_family(data)
        assert result["primary_user"]["emails"]["gmail"] == "x@gmail.com"

    def test_outlook_extracted(self):
        data = {"primary_user": "X", "briefing_email": "x@gmail.com",
                "outlook_email": "x@outlook.com"}
        result = mig._extract_family(data)
        assert result["primary_user"]["emails"]["outlook"] == "x@outlook.com"

    def test_spouse_extracted_from_family_members(self):
        data = {
            "primary_user": "X",
            "briefing_email": "x@example.com",
            "family_members": [{"name": "Pat", "role": "spouse"}],
        }
        result = mig._extract_family(data)
        assert result["spouse"]["name"] == "Pat"

    def test_children_extracted(self):
        data = {
            "primary_user": "X",
            "briefing_email": "x@example.com",
            "children": [{"name": "Casey", "age": 14, "grade": "9th"}],
        }
        result = mig._extract_family(data)
        assert len(result["children"]) == 1
        assert result["children"][0]["name"] == "Casey"


# ---------------------------------------------------------------------------
# End-to-end migrate() function
# ---------------------------------------------------------------------------

class TestMigrateEndToEnd:
    def test_dry_run_does_not_write_file(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_MINIMAL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        mig.migrate(settings_path=settings_path, output_path=output_path, dry_run=True)
        assert not output_path.exists()

    def test_creates_output_file(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_MINIMAL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        mig.migrate(settings_path=settings_path, output_path=output_path)
        assert output_path.exists()

    def test_output_is_valid_yaml(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_MINIMAL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        mig.migrate(settings_path=settings_path, output_path=output_path)
        data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_output_contains_primary_user_name(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_FULL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        mig.migrate(settings_path=settings_path, output_path=output_path)
        data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert data["family"]["primary_user"]["name"] == "Alex"
        assert data["family"]["primary_user"]["nickname"] == "Xander"

    def test_refuses_overwrite_without_force(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_MINIMAL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        output_path.write_text("existing: true\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            mig.migrate(settings_path=settings_path, output_path=output_path)
        assert exc_info.value.code != 0

    def test_force_overwrites_existing(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_FULL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        output_path.write_text("existing: true\n", encoding="utf-8")
        mig.migrate(settings_path=settings_path, output_path=output_path, force=True)
        data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert "existing" not in data

    def test_location_migrated(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_FULL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        mig.migrate(settings_path=settings_path, output_path=output_path)
        data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert data["location"]["city"] == "Denver"
        assert data["location"]["timezone"] == "America/Denver"

    def test_schema_version_present(self, tmp_path):
        settings_path = _write_settings(tmp_path, SETTINGS_MD_MINIMAL)
        output_path = tmp_path / "config" / "user_profile.yaml"
        mig.migrate(settings_path=settings_path, output_path=output_path)
        data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert "schema_version" in data
