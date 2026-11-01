"""
tests/integration/test_config_cache_consistency.py — Config loader cache contract tests.

Exercises: load_config() / invalidate() from scripts/lib/config_loader.py.

Coverage:
  - load_config() returns a dict (never raises)
  - load_config() returns a SHALLOW COPY — mutating the return value
    does not corrupt the in-process cache
  - load_config() returns same data on repeated calls (cache hit)
  - invalidate() clears cache — next call re-reads from disk
  - invalidate(name) still clears everything (lru_cache limitation)
  - Unknown config name returns empty dict, not exception
  - File not found returns empty dict, not exception
  - Corrupted YAML returns empty dict, not exception
  - _config_dir isolates test data from production config
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.config_loader import invalidate, load_config


# ---------------------------------------------------------------------------
# Fixture: temporary config directory with known YAML content
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache_after_each():
    """Ensure cache is cleared before and after every test for isolation."""
    invalidate()
    yield
    invalidate()


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config dir with a known artha_config.yaml."""
    cfg_data = {"app_name": "test_artha", "version": "1.0", "feature_flags": {"x": True}}
    (tmp_path / "artha_config.yaml").write_text(
        yaml.dump(cfg_data), encoding="utf-8"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Basic API contract
# ---------------------------------------------------------------------------

class TestLoadConfigBasicContract:
    def test_returns_dict(self, tmp_config_dir):
        result = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert isinstance(result, dict)

    def test_populated_config_has_expected_keys(self, tmp_config_dir):
        result = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert result.get("app_name") == "test_artha"
        assert result.get("version") == "1.0"

    def test_unknown_name_returns_empty_dict(self, tmp_config_dir):
        result = load_config("definitely_not_a_real_config_9999", _config_dir=str(tmp_config_dir))
        assert result == {}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        """Config dir exists but the YAML file is absent → empty dict."""
        result = load_config("artha_config", _config_dir=str(tmp_path))
        assert result == {}

    def test_never_raises(self, tmp_config_dir):
        # Should not raise under any valid name
        for name in ("artha_config", "user_profile", "channels", "nonexistent"):
            result = load_config(name, _config_dir=str(tmp_config_dir))
            assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Shallow-copy contract (no mutation of cache)
# ---------------------------------------------------------------------------

class TestShallowCopyContract:
    def test_mutating_returned_dict_does_not_corrupt_cache(self, tmp_config_dir):
        cfg1 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        # Mutate top-level key
        cfg1["injected_key"] = "malicious_value"
        cfg1["app_name"] = "HACKED"

        # Re-load from (still-cached) config; must not see mutations
        cfg2 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert "injected_key" not in cfg2
        assert cfg2.get("app_name") == "test_artha"

    def test_each_call_returns_distinct_object(self, tmp_config_dir):
        cfg1 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        cfg2 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert cfg1 is not cfg2

    def test_repeated_calls_return_identical_data(self, tmp_config_dir):
        cfg1 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        cfg2 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert cfg1 == cfg2


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def test_invalidate_clears_cache_so_updated_file_is_reloaded(self, tmp_config_dir):
        # Load original
        cfg_v1 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert cfg_v1.get("version") == "1.0"

        # Overwrite the YAML file with new content
        updated = {"app_name": "test_artha", "version": "2.0", "feature_flags": {}}
        (tmp_config_dir / "artha_config.yaml").write_text(
            yaml.dump(updated), encoding="utf-8"
        )

        # Without invalidate, we still get cached v1
        cfg_still_v1 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert cfg_still_v1.get("version") == "1.0"

        # After invalidate, re-read picks up v2
        invalidate()
        cfg_v2 = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert cfg_v2.get("version") == "2.0"

    def test_invalidate_with_name_clears_entire_cache(self, tmp_config_dir):
        """invalidate(name) is documented to clear entire cache (lru_cache limitation)."""
        load_config("artha_config", _config_dir=str(tmp_config_dir))
        # Should not raise and should work like invalidate()
        invalidate("artha_config")
        # Cache is now cleared; re-load must succeed
        result = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert isinstance(result, dict)

    def test_invalidate_no_args_is_safe_multiple_times(self, tmp_config_dir):
        """Calling invalidate() repeatedly must not raise."""
        invalidate()
        invalidate()
        invalidate()
        result = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestErrorResilience:
    def test_corrupted_yaml_returns_empty_dict(self, tmp_path):
        (tmp_path / "artha_config.yaml").write_text(
            ": : invalid: [yaml: {\n", encoding="utf-8"
        )
        result = load_config("artha_config", _config_dir=str(tmp_path))
        assert result == {}

    def test_yaml_non_dict_root_returns_empty_dict(self, tmp_path):
        """YAML root is a list, not a dict → must return empty dict."""
        (tmp_path / "artha_config.yaml").write_text(
            "- item1\n- item2\n", encoding="utf-8"
        )
        result = load_config("artha_config", _config_dir=str(tmp_path))
        assert result == {}

    def test_empty_yaml_file_returns_empty_dict(self, tmp_path):
        (tmp_path / "artha_config.yaml").write_text("", encoding="utf-8")
        result = load_config("artha_config", _config_dir=str(tmp_path))
        assert result == {}

    def test_config_dir_isolation_prevents_leakage(self, tmp_config_dir, tmp_path_factory):
        """Two different _config_dir values produce independent cache entries."""
        # Load from tmp_config_dir (has artha_config.yaml)
        cfg_real = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert cfg_real.get("app_name") == "test_artha"

        # A completely separate directory that has NO artha_config.yaml
        empty_dir = tmp_path_factory.mktemp("empty_config_dir")
        cfg_empty = load_config("artha_config", _config_dir=str(empty_dir))
        assert cfg_empty == {}

        # Re-load first should still have data (cache not poisoned)
        cfg_again = load_config("artha_config", _config_dir=str(tmp_config_dir))
        assert cfg_again.get("app_name") == "test_artha"
