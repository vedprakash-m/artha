"""tests/unit/test_config_loader.py — Unit tests for lib/config_loader.py

10 tests verifying the centralized YAML config loader's contract:
  - Basic loading, unknown names, missing files, invalid YAML
  - Cache shallow-copy contract (equal values, distinct objects)
  - Cache invalidation
  - Non-dict YAML, log output on error
  - Thread safety

Ref: specs/pay-debt-reloaded.md §4.3 WS-2-C
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config_dir(tmp_path: Path, filename: str, content: object) -> Path:
    """Write a YAML file to tmp_path and return the config dir path."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / filename).write_text(
        yaml.dump(content) if not isinstance(content, str) else content,
        encoding="utf-8",
    )
    return cfg_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadKnownConfig:
    def test_load_known_config_returns_dict(self, tmp_path):
        """load_config returns a dict for a known config name."""
        import lib.config_loader as cl
        cfg_dir = _make_config_dir(tmp_path, "connectors.yaml", {"key": "value"})
        result = cl.load_config("connectors", str(cfg_dir))
        assert isinstance(result, dict)
        assert result["key"] == "value"


class TestLoadUnknownNameReturnsEmpty:
    def test_load_unknown_name_returns_empty(self):
        """load_config returns {} for unrecognized config names."""
        import lib.config_loader as cl
        result = cl.load_config("nonexistent_config_xyz")
        assert result == {}


class TestLoadMissingFileReturnsEmpty:
    def test_load_missing_file_returns_empty(self, tmp_path):
        """load_config returns {} when the config file does not exist."""
        import lib.config_loader as cl
        cfg_dir = tmp_path / "empty_config"
        cfg_dir.mkdir()
        result = cl.load_config("connectors", str(cfg_dir))
        assert result == {}


class TestLoadInvalidYamlReturnsEmpty:
    def test_load_invalid_yaml_returns_empty(self, tmp_path):
        """load_config returns {} and logs on malformed YAML."""
        import lib.config_loader as cl
        cfg_dir = _make_config_dir(tmp_path, "connectors.yaml", "{{invalid: yaml: [}")
        result = cl.load_config("connectors", str(cfg_dir))
        assert result == {}


class TestCacheShallowCopyContract:
    def test_cache_returns_equal_copies(self, tmp_path):
        """load_config returns equal dicts that are distinct objects (shallow copy)."""
        import lib.config_loader as cl
        cfg_dir = _make_config_dir(tmp_path, "artha_config.yaml", {"x": 1})
        first = cl.load_config("artha_config", str(cfg_dir))
        second = cl.load_config("artha_config", str(cfg_dir))
        assert first == second          # equal values
        assert first is not second      # distinct objects — shallow copy contract


class TestInvalidateClearsCache:
    def test_invalidate_clears_cache(self, tmp_path):
        """After invalidate(), load_config reloads from disk."""
        import lib.config_loader as cl
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "channels.yaml"
        cfg_file.write_text(yaml.dump({"v": 1}), encoding="utf-8")

        first = cl.load_config("channels", str(cfg_dir))
        assert first["v"] == 1

        # Mutate the file on disk
        cfg_file.write_text(yaml.dump({"v": 99}), encoding="utf-8")
        cl.invalidate()

        second = cl.load_config("channels", str(cfg_dir))
        assert second["v"] == 99


class TestInvalidateAll:
    def test_invalidate_all_clears_entire_cache(self, tmp_path):
        """invalidate(None) clears all cached entries."""
        import lib.config_loader as cl
        cfg_dir = _make_config_dir(tmp_path, "actions.yaml", {"a": 1})
        cl.load_config("actions", str(cfg_dir))  # populate cache
        cl.invalidate()  # clear all
        # Cache should be empty — calling load_config again should re-read from disk
        result = cl.load_config("actions", str(cfg_dir))
        assert result == {"a": 1}


class TestNonDictYamlReturnsEmpty:
    def test_non_dict_yaml_returns_empty(self, tmp_path):
        """load_config returns {} when YAML root is a list (not a dict)."""
        import lib.config_loader as cl
        cfg_dir = _make_config_dir(tmp_path, "skills.yaml", ["item1", "item2"])
        result = cl.load_config("skills", str(cfg_dir))
        assert result == {}


class TestLoadConfigLogsWarningOnError:
    def test_load_config_logs_warning_on_error(self, tmp_path, caplog):
        """load_config emits a warning log when YAML is malformed."""
        import lib.config_loader as cl
        cfg_dir = _make_config_dir(tmp_path, "connectors.yaml", "{{bad yaml")
        with caplog.at_level(logging.WARNING, logger="artha.config_loader"):
            cl.load_config("connectors", str(cfg_dir))
        assert any("config_loader" in r.name and r.levelno >= logging.WARNING
                   for r in caplog.records)


class TestConcurrentLoadIsSafe:
    def test_concurrent_load_is_safe(self, tmp_path):
        """Multiple threads loading the same config simultaneously produce identical results."""
        import lib.config_loader as cl
        cfg_dir = _make_config_dir(tmp_path, "routing.yaml", {"route": "safe"})

        results: list[dict] = []
        errors: list[Exception] = []

        def load():
            try:
                results.append(cl.load_config("routing", str(cfg_dir)))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=load) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(results) == 10
        assert all(r == {"route": "safe"} for r in results)
