"""
Unit tests for scripts/profile_loader.py

Tests cover:
  - load_profile() with valid, empty, and missing files
  - get() dot-notation access (happy path, missing keys, type mismatches)
  - children(), enabled_domains(), has_profile(), schema_version()
  - require_profile() exit behavior
  - reload_profile() cache invalidation
  - artha_dir() returns a resolved Path
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Ensure the scripts package is importable
_ARTHA_ROOT = Path(__file__).resolve().parents[2]
if str(_ARTHA_ROOT) not in sys.path:
    sys.path.insert(0, str(_ARTHA_ROOT))

import profile_loader as pl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_profile(tmp_path: Path, data: dict) -> Path:
    """Write a YAML profile to tmp_path/config/user_profile.yaml and return path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    profile_path = config_dir / "user_profile.yaml"
    profile_path.write_text(yaml.dump(data), encoding="utf-8")
    return profile_path


def _patch_profile_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect profile_loader to use tmp_path/config/user_profile.yaml."""
    profile_path = tmp_path / "config" / "user_profile.yaml"
    monkeypatch.setattr(pl, "_PROFILE_PATH", profile_path)
    pl.load_profile.cache_clear()
    return profile_path


# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------

class TestLoadProfile:
    def test_returns_empty_dict_when_file_missing(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        # file does not exist — no mkdir, no write
        assert pl.load_profile() == {}

    def test_returns_empty_dict_when_file_is_empty(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "user_profile.yaml").write_text("", encoding="utf-8")
        pl.load_profile.cache_clear()
        assert pl.load_profile() == {}

    def test_loads_valid_profile(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {"schema_version": "1.0", "family": {"name": "Doe"}})
        pl.load_profile.cache_clear()
        result = pl.load_profile()
        assert result["schema_version"] == "1.0"
        assert result["family"]["name"] == "Doe"

    def test_result_is_cached(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {"schema_version": "1.0"})
        pl.load_profile.cache_clear()
        first = pl.load_profile()
        second = pl.load_profile()
        assert first is second  # same object — from cache


# ---------------------------------------------------------------------------
# reload_profile
# ---------------------------------------------------------------------------

class TestReloadProfile:
    def test_reload_picks_up_updated_file(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {"schema_version": "1.0"})
        pl.load_profile.cache_clear()
        assert pl.load_profile()["schema_version"] == "1.0"

        # Update file on disk
        _write_profile(tmp_path, {"schema_version": "2.0"})
        result = pl.reload_profile()
        assert result["schema_version"] == "2.0"


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

class TestGet:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {
            "schema_version": "1.0",
            "family": {
                "name": "Doe",
                "primary_user": {
                    "name": "John",
                    "emails": {"gmail": "john@example.com"},
                },
            },
            "location": {"timezone": "America/New_York", "lat": 40.7},
            "domains": {
                "finance": {"enabled": True},
                "health": {"enabled": False},
            },
        })
        pl.load_profile.cache_clear()

    def test_top_level_key(self):
        assert pl.get("schema_version") == "1.0"

    def test_nested_key(self):
        assert pl.get("family.name") == "Doe"

    def test_deeply_nested_key(self):
        assert pl.get("family.primary_user.emails.gmail") == "john@example.com"

    def test_missing_key_returns_default(self):
        assert pl.get("nonexistent", "fallback") == "fallback"

    def test_missing_nested_returns_default(self):
        assert pl.get("family.nonexistent.deep", 42) == 42

    def test_default_is_none_when_not_specified(self):
        assert pl.get("nonexistent") is None

    def test_numeric_value(self):
        assert pl.get("location.lat") == 40.7

    def test_mid_path_is_not_dict(self):
        # "family.name.nested" — name is a str, not dict → default
        assert pl.get("family.name.nested", "x") == "x"


# ---------------------------------------------------------------------------
# children()
# ---------------------------------------------------------------------------

class TestChildren:
    def test_no_children_returns_empty_list(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {"family": {}})
        pl.load_profile.cache_clear()
        assert pl.children() == []

    def test_returns_children_list(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {
            "family": {
                "children": [
                    {"name": "Alex", "age": 16},
                    {"name": "Sam", "age": 12},
                ]
            }
        })
        pl.load_profile.cache_clear()
        kids = pl.children()
        assert len(kids) == 2
        assert kids[0]["name"] == "Alex"
        assert kids[1]["age"] == 12


# ---------------------------------------------------------------------------
# enabled_domains()
# ---------------------------------------------------------------------------

class TestEnabledDomains:
    def test_only_enabled_domains_returned(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {
            "domains": {
                "finance": {"enabled": True},
                "health": {"enabled": False},
                "immigration": {"enabled": True},
                "kids": {},  # no 'enabled' key — treated as disabled
            }
        })
        pl.load_profile.cache_clear()
        enabled = pl.enabled_domains()
        assert set(enabled) == {"finance", "immigration"}

    def test_no_domains_returns_empty(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {})
        pl.load_profile.cache_clear()
        assert pl.enabled_domains() == []


# ---------------------------------------------------------------------------
# has_profile()
# ---------------------------------------------------------------------------

class TestHasProfile:
    def test_false_when_missing(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        assert pl.has_profile() is False

    def test_true_when_present(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {"schema_version": "1.0"})
        pl.load_profile.cache_clear()
        assert pl.has_profile() is True


# ---------------------------------------------------------------------------
# schema_version()
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_returns_version_from_profile(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {"schema_version": "1.0"})
        pl.load_profile.cache_clear()
        assert pl.schema_version() == "1.0"

    def test_returns_fallback_when_missing(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        # no file
        assert pl.schema_version() == "0.0"


# ---------------------------------------------------------------------------
# require_profile()
# ---------------------------------------------------------------------------

class TestRequireProfile:
    def test_exits_when_profile_missing(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as exc_info:
            pl.require_profile()
        assert exc_info.value.code == 1

    def test_returns_profile_when_present(self, tmp_path, monkeypatch):
        _patch_profile_path(tmp_path, monkeypatch)
        _write_profile(tmp_path, {"schema_version": "1.0", "family": {"name": "Doe"}})
        pl.load_profile.cache_clear()
        result = pl.require_profile()
        assert result["schema_version"] == "1.0"


# ---------------------------------------------------------------------------
# artha_dir()
# ---------------------------------------------------------------------------

class TestArthaDir:
    def test_returns_path(self):
        d = pl.artha_dir()
        assert isinstance(d, Path)
        assert d.is_absolute()

    def test_points_to_repo_root(self):
        # profile_loader.py lives in scripts/, so parent of scripts/ is repo root
        d = pl.artha_dir()
        assert (d / "scripts" / "profile_loader.py").exists()
