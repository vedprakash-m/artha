"""Unit tests for scripts/skill_runner.py — cadence enforcement and delta detection."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def skill_config():
    """Minimal skills config."""
    return {
        "skills": {
            "weather": {"enabled": True, "cadence": "every_run", "priority": "P2"},
            "uscis": {"enabled": True, "cadence": "daily", "priority": "P0"},
            "tax": {"enabled": True, "cadence": "weekly", "priority": "P1"},
            "disabled_skill": {"enabled": False, "cadence": "daily"},
        }
    }


@pytest.fixture
def empty_cache():
    return {}


# ── should_run ───────────────────────────────────────────────────────────────

class TestShouldRun:
    def test_disabled_skill_never_runs(self, skill_config, empty_cache):
        from skill_runner import should_run

        assert should_run("disabled_skill", skill_config, empty_cache) is False

    def test_every_run_always_runs(self, skill_config, empty_cache):
        from skill_runner import should_run

        assert should_run("weather", skill_config, empty_cache) is True

    def test_daily_cold_start(self, skill_config, empty_cache):
        from skill_runner import should_run

        assert should_run("uscis", skill_config, empty_cache) is True

    def test_daily_too_soon(self, skill_config):
        from skill_runner import should_run

        recent = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        cache = {"uscis": {"last_run": recent}}
        assert should_run("uscis", skill_config, cache) is False

    def test_daily_past_due(self, skill_config):
        from skill_runner import should_run

        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        cache = {"uscis": {"last_run": old}}
        assert should_run("uscis", skill_config, cache) is True

    def test_weekly_too_soon(self, skill_config):
        from skill_runner import should_run

        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        cache = {"tax": {"last_run": recent}}
        assert should_run("tax", skill_config, cache) is False

    def test_weekly_past_due(self, skill_config):
        from skill_runner import should_run

        old = (datetime.now(timezone.utc) - timedelta(weeks=2)).isoformat()
        cache = {"tax": {"last_run": old}}
        assert should_run("tax", skill_config, cache) is True

    def test_unknown_skill_returns_false(self, skill_config, empty_cache):
        from skill_runner import should_run

        assert should_run("nonexistent", skill_config, empty_cache) is False


# ── get_delta ────────────────────────────────────────────────────────────────

class TestGetDelta:
    def test_delta_detected_on_field_change(self):
        from skill_runner import get_delta

        current = {"status": "approved", "date": "2026-03-01"}
        prev_cache = {"uscis": {"current": {"data": {"status": "pending", "date": "2026-03-01"}}}}
        assert get_delta("uscis", current, prev_cache, ["status"]) is True

    def test_no_delta_when_same(self):
        from skill_runner import get_delta

        data = {"status": "pending", "date": "2026-03-01"}
        cache = {"uscis": {"current": {"data": data}}}
        assert get_delta("uscis", data, cache, ["status", "date"]) is False

    def test_delta_on_missing_prev(self):
        from skill_runner import get_delta

        current = {"status": "approved"}
        assert get_delta("uscis", current, {}, ["status"]) is True


class TestPlatformRouting:
    @pytest.mark.parametrize(
        ("run_on", "host", "expected"),
        [
            ("darwin", "Windows", False),
            ("windows", "Darwin", False),
            ("darwin", "Linux", False),
            ("darwin", "Darwin", True),
            ("windows", "Windows", True),
            ("all", "Windows", True),
            (["darwin", "windows"], "Windows", True),
            (["darwin", "windows"], "Linux", False),
        ],
    )
    def test_platform_restriction_precedes_cadence(self, monkeypatch, run_on, host, expected):
        import platform
        import skill_runner

        monkeypatch.setattr(platform, "system", lambda: host)
        config = {"skills": {"native": {
            "enabled": True, "priority": "P0", "run_on": run_on, "cadence": "every_run",
        }}}

        assert skill_runner.should_run("native", config, {}) is expected

    def test_force_bypasses_cadence_without_bypassing_enabled_or_platform(self, monkeypatch):
        import platform
        import skill_runner

        monkeypatch.setattr(platform, "system", lambda: "Windows")
        recent = datetime.now(timezone.utc).isoformat()
        cache = {"native": {"last_run": recent}}
        settings = {"enabled": True, "run_on": "windows", "cadence": "weekly"}
        config = {"skills": {"native": settings}}

        assert skill_runner.should_run("native", config, cache) is False
        assert skill_runner.should_run("native", config, cache, force=True) is True
        settings["run_on"] = "darwin"
        assert skill_runner.should_run("native", config, cache, force=True) is False
        settings.update(run_on="windows", enabled=False)
        assert skill_runner.should_run("native", config, cache, force=True) is False

    def test_single_skill_force_never_dispatches_a_foreign_platform_skill(self, monkeypatch):
        import platform
        import skill_runner

        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(sys, "argv", ["skill_runner.py", "--skill", "imessage_appointments", "--force"])
        monkeypatch.setattr(skill_runner, "_TRACE_AVAILABLE", False)
        monkeypatch.setattr(skill_runner, "load_config", lambda: {"skills": {
            "imessage_appointments": {"enabled": True, "priority": "P0", "run_on": "darwin"},
        }})
        monkeypatch.setattr(skill_runner, "load_cache", lambda: {})
        executor = MagicMock(side_effect=AssertionError("foreign-platform skill was dispatched"))
        monkeypatch.setattr(skill_runner, "ThreadPoolExecutor", executor)

        skill_runner.main()

        executor.assert_not_called()


# ── load_config / load_cache ────────────────────────────────────────────────

class TestLoadFunctions:
    def test_load_config_missing_file(self, tmp_path, monkeypatch):
        from skill_runner import load_config
        import skill_runner

        monkeypatch.setattr(skill_runner, "SKILLS_CONFIG", tmp_path / "missing.yaml")
        result = load_config()
        assert result == {"skills": {}}

    def test_load_cache_missing_file(self, tmp_path, monkeypatch):
        from skill_runner import load_cache
        import skill_runner

        monkeypatch.setattr(skill_runner, "CACHE_FILE", tmp_path / "missing.json")
        result = load_cache()
        assert result == {}

    def test_load_cache_corrupt_json(self, tmp_path, monkeypatch):
        from skill_runner import load_cache
        import skill_runner

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all")
        monkeypatch.setattr(skill_runner, "CACHE_FILE", bad_file)
        result = load_cache()
        assert result == {}


# ── Entrypoint guard ───────────────────────────────────────────────────────────────────────────

class TestEntrypointGuard:
    """skill_runner.py must be directly executable (the Gemini CLI ran it as a script)."""

    def test_main_block_executes_without_error(self, tmp_path, monkeypatch):
        """Running skill_runner with no enabled skills must exit 0, not NameError."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "skill_runner.py")],
            capture_output=True, text=True,
            env={**__import__("os").environ, "ARTHA_NO_REEXEC": "1"},
        )
        # No skills enabled = exits cleanly (0 or non-zero for P0 failures is ok,
        # but we must NOT get an uncaught ImportError or NameError)
        assert "NameError" not in result.stderr
        assert "ImportError" not in result.stderr

    def test_importlib_util_accessible_at_module_scope(self):
        """importlib.util must be a module-level import, not scoped inside run_skill().

        Gemini bug: 'import importlib.util' was inside run_skill(), creating a
        local binding that shadowed the global 'importlib' and caused
        UnboundLocalError when spec_from_file_location was called.
        """
        import skill_runner
        # If importlib.util is at module scope, skill_runner.importlib.util is accessible
        assert hasattr(skill_runner, "importlib")
        assert hasattr(skill_runner.importlib, "util")
        assert callable(getattr(skill_runner.importlib.util, "spec_from_file_location", None))
