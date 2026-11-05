"""tests/unit/test_preflight_integration.py — T5-34..40: preflight.integration_checks tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import preflight as pf


# ---------------------------------------------------------------------------
# T5-36: check_dep_freshness
# ---------------------------------------------------------------------------

class TestCheckDepFreshness:
    def test_all_deps_present_passes(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = pf.check_dep_freshness()
            assert isinstance(result, pf.CheckResult)

    def test_missing_dep_fails(self):
        import importlib.util as _ilu
        original_find = _ilu.find_spec

        def _missing_spec(name, *args, **kwargs):
            if name in ("httpx", "yaml", "requests"):
                return None
            return original_find(name, *args, **kwargs)

        with patch.object(_ilu, "find_spec", side_effect=_missing_spec):
            result = pf.check_dep_freshness()
            assert isinstance(result, pf.CheckResult)
            # should not crash even when deps are missing


# ---------------------------------------------------------------------------
# T5-37: check_action_handlers
# ---------------------------------------------------------------------------

class TestCheckActionHandlers:
    def test_returns_check_result(self):
        result = pf.check_action_handlers()
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")

    def test_subprocess_exit0_passes(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = pf.check_action_handlers()
            assert isinstance(result, pf.CheckResult)


# ---------------------------------------------------------------------------
# T5-38..40: run_preflight integration — advisory / full / cold-start
# ---------------------------------------------------------------------------

class TestRunPreflightIntegration:
    def test_run_preflight_returns_results_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        results = pf.run_preflight()
        assert isinstance(results, list)
        assert all(isinstance(r, pf.CheckResult) for r in results)

    def test_format_results_advisory_returns_str(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        results = pf.run_preflight()
        output, all_passed = pf.format_results(results, advisory=True)
        assert isinstance(output, str)
        assert isinstance(all_passed, bool)

    def test_format_results_strict_returns_str(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        results = pf.run_preflight()
        output, all_passed = pf.format_results(results, advisory=False)
        assert isinstance(output, str)


class TestHeldHandlersAreNotWarnings:
    """A held action type cannot run, so its handler health is not a warning (audit H2, 2026-11-05)."""

    def _workspace(self, tmp_path, monkeypatch, health):
        import types
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "artha_config.yaml").write_text("harness:\n  actions:\n    enabled: true\n")
        (tmp_path / "config" / "actions.yaml").write_text("live_trial:\n  trial: [reminder_create]\n  held: [slack_send]\n")
        import lib.config_loader as config_loader
        import preflight.integration_checks as checks
        monkeypatch.setattr(checks, "ARTHA_DIR", str(tmp_path))
        real_load = config_loader.load_config

        def load(name, *args, **kwargs):
            if name == "artha_config":
                return {"harness": {"actions": {"enabled": True}}}
            return real_load(name, *args, **kwargs)

        monkeypatch.setattr(config_loader, "load_config", load)

        class Executor:
            def __init__(self, root):
                pass

            def expire_stale(self):
                return 0

            def run_health_checks(self):
                return dict(health)

        monkeypatch.setitem(sys.modules, "action_executor", types.SimpleNamespace(ActionExecutor=Executor))

    def test_degraded_held_handler_passes(self, tmp_path, monkeypatch):
        self._workspace(tmp_path, monkeypatch, {"reminder_create": True, "slack_send": False})
        result = pf.check_action_handlers()
        assert result.passed and "slack_send" in result.message and "held" in result.message

    def test_degraded_unheld_handler_still_warns(self, tmp_path, monkeypatch):
        self._workspace(tmp_path, monkeypatch, {"slack_send": False, "apple_reminders_sync": False})
        result = pf.check_action_handlers()
        assert not result.passed and "apple_reminders_sync" in result.message
        assert "1 degraded" in result.message
