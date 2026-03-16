"""
tests/unit/test_channel_registry.py

Unit tests for scripts/channels/registry.py

Tests:
  1. load_channels_config() returns safe default when file missing
  2. iter_enabled_channels() yields only enabled channels
  3. load_adapter_module() raises ValueError on path outside allowed prefix
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

# Ensure repo root on path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from channels.registry import (
    load_channels_config,
    iter_enabled_channels,
    load_adapter_module,
)


class TestLoadChannelsConfig:
    def test_returns_safe_default_when_file_missing(self, tmp_path, monkeypatch):
        """No channels.yaml → returns minimal safe default with push disabled."""
        import lib.common as common
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path)

        cfg = load_channels_config()
        assert cfg["defaults"]["push_enabled"] is False
        assert cfg["channels"] == {}

    def test_reads_real_config(self, tmp_path, monkeypatch):
        """Reads and parses a valid channels.yaml."""
        import yaml
        import lib.common as common

        data = {
            "defaults": {"push_enabled": True},
            "channels": {
                "telegram": {
                    "enabled": True,
                    "adapter": "scripts/channels/telegram.py",
                    "auth": {"credential_key": "artha-telegram-bot-token"},
                    "recipients": {"primary": {"id": "123", "access_scope": "full"}},
                }
            },
        }
        (tmp_path / "channels.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path)

        cfg = load_channels_config()
        assert cfg["defaults"]["push_enabled"] is True
        assert "telegram" in cfg["channels"]


class TestIterEnabledChannels:
    def test_yields_only_enabled(self):
        """Only channels with enabled: true are yielded."""
        config = {
            "channels": {
                "telegram": {"enabled": True, "adapter": "scripts/channels/telegram.py"},
                "discord": {"enabled": False, "adapter": "scripts/channels/discord.py"},
                "slack": {"enabled": True, "adapter": "scripts/channels/slack.py"},
            }
        }
        names = [name for name, _ in iter_enabled_channels(config)]
        assert "telegram" in names
        assert "slack" in names
        assert "discord" not in names

    def test_empty_config(self):
        """Empty channels dict yields nothing."""
        names = list(iter_enabled_channels({"channels": {}}))
        assert names == []

    def test_missing_channels_key(self):
        """Missing 'channels' key yields nothing (no exception)."""
        names = list(iter_enabled_channels({}))
        assert names == []


class TestLoadAdapterModuleSecurity:
    def test_rejects_absolute_path(self):
        """Absolute path outside scripts/channels/ must raise ValueError."""
        with pytest.raises(ValueError, match="scripts/channels"):
            load_adapter_module("/etc/passwd")

    def test_rejects_traversal(self):
        """Path traversal must raise ValueError."""
        with pytest.raises(ValueError, match="scripts/channels"):
            load_adapter_module("scripts/../malicious.py")

    def test_rejects_arbitrary_module(self):
        """Arbitrary module path must raise ValueError."""
        with pytest.raises(ValueError, match="scripts/channels"):
            load_adapter_module("scripts/connectors/gmail.py")

    def test_allows_channels_prefix(self, monkeypatch):
        """Valid scripts/channels/ path is allowed through the security gate."""
        import importlib
        import channels.registry as reg

        # Mock importlib.import_module to avoid actually loading anything
        mock_module = object()
        monkeypatch.setattr(importlib, "import_module", lambda _: mock_module)

        result = load_adapter_module("scripts/channels/telegram.py")
        assert result is mock_module
