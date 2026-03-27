"""tests/unit/test_foundation_hardening.py — Phase 1: TD-5, TD-10, TD-11 tests.

Ref: specs/pay-debt.md §5.2
"""
from __future__ import annotations

import sys
import io
from pathlib import Path
from unittest.mock import patch
import pytest


# ---------------------------------------------------------------------------
# TD-5: get_config() accessor
# ---------------------------------------------------------------------------

def test_get_config_returns_dict():
    """get_config() returns the mutable _config dict."""
    import foundation
    cfg = foundation.get_config()
    assert isinstance(cfg, dict)
    assert len(cfg) > 0


def test_get_config_is_same_object_as_config():
    """get_config() returns the same object as foundation._config."""
    import foundation
    assert foundation.get_config() is foundation._config


def test_get_config_monkeypatch_visible(monkeypatch):
    """Patching _config is visible through get_config()."""
    import foundation
    monkeypatch.setitem(foundation._config, "_test_sentinel_key", "sentinel_value")
    assert foundation.get_config()["_test_sentinel_key"] == "sentinel_value"


def test_module_level_artha_dir_is_path():
    """Module-level ARTHA_DIR is a Path (backward compat)."""
    import foundation
    assert isinstance(foundation.ARTHA_DIR, Path)


def test_module_level_state_dir_exists_or_is_path():
    """Module-level STATE_DIR is a Path (backward compat)."""
    import foundation
    assert isinstance(foundation.STATE_DIR, Path)


def test_get_config_state_dir_matches_config_dict():
    """get_config()['STATE_DIR'] matches foundation._config['STATE_DIR']."""
    import foundation
    assert foundation.get_config()["STATE_DIR"] == foundation._config["STATE_DIR"]


# ---------------------------------------------------------------------------
# TD-10: health_check_updater — new module
# ---------------------------------------------------------------------------

def test_health_check_updater_importable():
    """health_check_updater can be imported without error."""
    import health_check_updater
    assert callable(health_check_updater.update_channel_health_md)


def test_health_check_updater_creates_new_section(tmp_path):
    """update_channel_health_md creates channel_health section if missing."""
    import health_check_updater
    health_md = tmp_path / "health-check.md"
    health_md.write_text("# Health\n\nSome content.\n", encoding="utf-8")

    with patch("health_check_updater.STATE_DIR", tmp_path):
        health_check_updater.update_channel_health_md("telegram", healthy=True)

    content = health_md.read_text(encoding="utf-8")
    assert "channel_health:" in content
    assert "  telegram:" in content
    assert "healthy: true" in content


def test_health_check_updater_updates_existing_entry(tmp_path):
    """update_channel_health_md updates an existing channel entry."""
    import health_check_updater
    health_md = tmp_path / "health-check.md"
    health_md.write_text(
        "# Health\n\n## Channel Health (Structured)\n```yaml\nchannel_health:\n"
        "  telegram:\n    last_check: \"2020-01-01T00:00:00Z\"\n    healthy: false\n```\n",
        encoding="utf-8",
    )

    with patch("health_check_updater.STATE_DIR", tmp_path):
        health_check_updater.update_channel_health_md("telegram", healthy=True)

    content = health_md.read_text(encoding="utf-8")
    assert "healthy: true" in content
    assert "healthy: false" not in content


def test_lib_common_deprecated_reexport():
    """lib.common.update_channel_health_md is still importable (deprecated re-export)."""
    from lib.common import update_channel_health_md
    assert callable(update_channel_health_md)


# ---------------------------------------------------------------------------
# TD-11: AuditMiddleware OSError → stderr
# ---------------------------------------------------------------------------

def test_audit_middleware_oserror_prints_to_stderr(tmp_path, capsys):
    """AuditMiddleware._append prints to stderr when audit write fails."""
    import importlib, importlib.util, types

    # Load audit_middleware with a patched path pointing to an unwritable location
    spec = importlib.util.spec_from_file_location(
        "audit_middleware_test",
        "scripts/middleware/audit_middleware.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Point the middleware at a non-existent artha_dir (state/audit.md won't exist)
    am = mod.AuditMiddleware(artha_dir=tmp_path / "nonexistent_dir")
    am._append("test line\n")

    captured = capsys.readouterr()
    assert "[WARN] audit write failed" in captured.err


def test_audit_middleware_oserror_does_not_raise(tmp_path):
    """AuditMiddleware._append does not raise even when write fails."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "audit_middleware_no_raise",
        "scripts/middleware/audit_middleware.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    am = mod.AuditMiddleware(artha_dir=tmp_path / "no_dir")
    # Must not raise
    am._append("test line\n")


def test_audit_middleware_happy_path(tmp_path):
    """AuditMiddleware._append writes successfully when file is writable."""
    import importlib.util

    # AuditMiddleware writes to <artha_dir>/state/audit.md
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    audit_file = state_dir / "audit.md"
    spec = importlib.util.spec_from_file_location(
        "audit_middleware_happy",
        "scripts/middleware/audit_middleware.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    am = mod.AuditMiddleware(artha_dir=tmp_path)
    am._append("hello world\n")
    assert audit_file.read_text(encoding="utf-8") == "hello world\n"
