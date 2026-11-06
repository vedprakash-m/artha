"""Audit (2026-11-05): `preflight.py --read-only` reports health without mutating state.

The CLI script (scripts/preflight.py) is shadowed on import by the scripts/preflight/ package, so it is
loaded here directly from its file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "preflight.py"


@pytest.fixture()
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location("preflight_cli_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "preflight_cli_script", module)
    spec.loader.exec_module(module)
    return module


def _calls(monkeypatch, module):
    seen = []
    for name in ("_clear_stale_pipeline_checkpoint", "_compact_health_check", "_write_device_context"):
        monkeypatch.setattr(module, name, lambda name=name: seen.append(name))
    monkeypatch.setattr(module, "run_preflight", lambda **kwargs: [])
    return seen


def test_read_only_skips_post_run_writes(script, monkeypatch, capsys, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "user_profile.yaml").write_text("{}\n")
    monkeypatch.setattr(script, "ARTHA_DIR", str(tmp_path))
    seen = _calls(monkeypatch, script)
    monkeypatch.setattr(sys, "argv", ["preflight.py", "--read-only", "--json"])
    with pytest.raises(SystemExit):
        script.main()
    assert seen == []
    assert json.loads(capsys.readouterr().out)["pre_flight_ok"] is True


def test_default_run_keeps_its_post_run_writes(script, monkeypatch, capsys, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "user_profile.yaml").write_text("{}\n")
    monkeypatch.setattr(script, "ARTHA_DIR", str(tmp_path))
    seen = _calls(monkeypatch, script)
    monkeypatch.setattr(sys, "argv", ["preflight.py", "--json"])
    with pytest.raises(SystemExit):
        script.main()
    assert seen == ["_clear_stale_pipeline_checkpoint", "_compact_health_check", "_write_device_context"]


def test_read_only_action_check_does_not_expire_queue(script, monkeypatch, tmp_path):
    calls = []

    class Executor:
        def __init__(self, root):
            pass

        def expire_stale(self):
            calls.append("expire")
            return 3

        def run_health_checks(self):
            return {"reminder_create": True}

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "artha_config.yaml").write_text("harness:\n  actions:\n    enabled: true\n")
    monkeypatch.setattr(script, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "action_executor", types.SimpleNamespace(ActionExecutor=Executor))
    monkeypatch.setattr(script, "_READ_ONLY", True)
    result = script.check_action_handlers()
    assert calls == [] and result.passed and "expired" not in result.message
