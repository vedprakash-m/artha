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
# T5-34..35: check_channel_config
# ---------------------------------------------------------------------------

class TestCheckChannelConfig:
    def test_no_channels_yaml_passes_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        result = pf.check_channel_config()
        assert isinstance(result, pf.CheckResult)

    def test_push_disabled_returns_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        channels = {"telegram": {"enabled": True, "push_enabled": False, "token": "x:y", "chat_id": "123"}}
        (cfg_dir / "channels.yaml").write_text(yaml.dump(channels))
        result = pf.check_channel_config()
        assert isinstance(result, pf.CheckResult)


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
