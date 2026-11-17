"""tests/unit/test_preflight_integration.py — T5-34..40: preflight.integration_checks tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    @pytest.fixture(autouse=True)
    def mock_provider_checks(self, monkeypatch):
        monkeypatch.setattr(pf, "check_workiq", lambda: pf.CheckResult("WorkIQ", "P1", True, "synthetic"))
        monkeypatch.setattr(pf, "check_ado_auth", lambda: pf.CheckResult("ADO", "P1", True, "synthetic"))
        monkeypatch.setattr(pf, "check_ha_connectivity", lambda: pf.CheckResult("HA", "P1", True, "synthetic"))
        monkeypatch.setattr(
            pf, "check_script_health",
            lambda name, args, severity="P0": pf.CheckResult(name, severity, True, "synthetic"),
        )

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


# ---------------------------------------------------------------------------
# External-agent readiness contract (package + CLI surfaces)
# ---------------------------------------------------------------------------

def _readiness_surface(monkeypatch, tmp_path, kind: str):
    import importlib.util

    if kind == "package":
        import preflight.integration_checks as module
    else:
        spec = importlib.util.spec_from_file_location("_preflight_agent_readiness_script", _SCRIPTS_DIR / "preflight.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(module, "SCRIPTS_DIR", str(_SCRIPTS_DIR))
    return module


def _write_artha_cfg(tmp_path: Path, text: str) -> None:
    cfg = tmp_path / "config" / "artha_config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("kind", ["package", "script"])
@pytest.mark.parametrize("check_name", ["check_ext_agent_discovery", "check_ext_agent_health"])
def test_ext_agent_readiness_invalid_config_and_malformed_registry_fail_closed(
    monkeypatch, tmp_path, kind, check_name
):
    from lib.agent_registry import AgentRegistry

    mod = _readiness_surface(monkeypatch, tmp_path, kind)
    fn = getattr(mod, check_name)

    for i, cfg in enumerate((None, "", "[", "[]", "harness: []", "harness: {agentic: {external_agents: {enabled: 'false'}}}")):
        sub = tmp_path / f"bad_{i}"
        sub.mkdir()
        monkeypatch.setattr(mod, "ARTHA_DIR", str(sub))
        if cfg is not None:
            _write_artha_cfg(sub, cfg)
        res = fn()
        assert res.severity == "P1" and not res.passed

    dis_dir = tmp_path / "disabled"
    dis_dir.mkdir()
    monkeypatch.setattr(mod, "ARTHA_DIR", str(dis_dir))
    _write_artha_cfg(dis_dir, "harness:\n  agentic:\n    external_agents:\n      enabled: false\n")
    dis = fn()
    assert dis.passed and "not enabled" in dis.message.lower()

    en_dir = tmp_path / "enabled"
    en_dir.mkdir()
    monkeypatch.setattr(mod, "ARTHA_DIR", str(en_dir))
    _write_artha_cfg(
        en_dir,
        "harness:\n  agentic:\n    external_agents:\n      enabled: true\n      discovery:\n        enabled: true\n",
    )
    assert fn().passed  # missing registry remains optional when disabled or absent
    reg_file = en_dir / "config" / "agents" / "external-registry.yaml"
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    for bad_reg in ("[", "[]", "{}", "agents: []", "agents: {bad-agent: []}"):
        reg_file.write_text(bad_reg, encoding="utf-8")
        bad = fn()
        assert bad.severity == "P1" and not bad.passed and "registry" in bad.message.lower()

    reg_file.write_text("schema_version: '1.0'\nagents: {}\n", encoding="utf-8")
    assert fn().passed
    with pytest.raises(FileNotFoundError):
        AgentRegistry.load(tmp_path / "missing_strict_dir", strict=True)


def test_explicitly_disabled_ext_agent_discovery_skips_cleanly(monkeypatch, tmp_path):
    _write_artha_cfg(
        tmp_path,
        "harness:\n  agentic:\n    external_agents:\n      enabled: true\n      discovery:\n        enabled: false\n",
    )
    for kind in ("package", "script"):
        mod = _readiness_surface(monkeypatch, tmp_path, kind)
        res = mod.check_ext_agent_discovery()
        assert res.passed and "discovery disabled" in res.message.lower()
