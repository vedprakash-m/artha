"""tests/unit/test_foundation.py — Config accessors and lazy AgeVault factory contracts."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_get_config_returns_mutable_dict_and_paths():
    import foundation

    cfg = foundation.get_config()
    assert isinstance(cfg, dict) and len(cfg) > 0
    assert cfg is foundation._config
    assert isinstance(foundation.ARTHA_DIR, Path)
    assert isinstance(foundation.STATE_DIR, Path)
    assert cfg["STATE_DIR"] == foundation._config["STATE_DIR"]


def test_get_config_monkeypatch_visible(monkeypatch):
    import foundation

    monkeypatch.setitem(foundation._config, "_test_sentinel_key", "sentinel_value")
    assert foundation.get_config()["_test_sentinel_key"] == "sentinel_value"


def test_age_vault_factory_uses_current_config_and_defers_private_key(monkeypatch, tmp_path):
    import foundation

    calls = []
    monkeypatch.setitem(foundation._config, "STATE_DIR", tmp_path / "configured")
    monkeypatch.setattr(foundation, "get_public_key", lambda: "age1synthetic")
    monkeypatch.setattr(foundation, "get_private_key", lambda: calls.append("private") or "synthetic")
    vault = foundation.get_age_vault()
    assert vault.path_for("finance") == tmp_path / "configured" / "finance.md.age"
    assert calls == []
    assert vault._identity() == "synthetic"
    assert calls == ["private"]


def test_age_vault_factory_honors_explicit_state_directory(monkeypatch, tmp_path):
    import foundation

    monkeypatch.setitem(foundation._config, "STATE_DIR", tmp_path / "configured")
    monkeypatch.setattr(foundation, "get_public_key", lambda: "age1synthetic")
    vault = foundation.get_age_vault(state_dir=tmp_path / "selected")
    assert vault.path_for("finance") == tmp_path / "selected" / "finance.md.age"


def test_age_vault_factory_missing_keys_raise_vault_error(monkeypatch, tmp_path):
    import foundation
    from catchup_core.vault import VaultUnavailable

    def unavailable():
        raise SystemExit(1)

    monkeypatch.setattr(foundation, "get_public_key", unavailable)
    with pytest.raises(VaultUnavailable, match="recipient"):
        foundation.get_age_vault()

    monkeypatch.setattr(foundation, "get_public_key", lambda: "age1synthetic")
    monkeypatch.setattr(foundation, "get_private_key", unavailable)
    vault = foundation.get_age_vault(state_dir=tmp_path)
    with pytest.raises(VaultUnavailable, match="private key"):
        vault._identity()
