"""check_script_health must report the script's failure, not a trailing Python warning."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import preflight.api_checks as api_checks  # noqa: E402


def _load_cli():
    spec = importlib.util.spec_from_file_location("preflight_cli_reason_test", _SCRIPTS_DIR / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["package", "cli"])
def checker(request):
    return api_checks if request.param == "package" else _load_cli()


def _run_with(checker, monkeypatch, tmp_path, returncode, stdout, stderr):
    (tmp_path / "probe.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(checker, "SCRIPTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        checker.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, stderr),
    )
    return checker.check_script_health("probe.py", ["--health"])


def test_failure_reason_skips_trailing_warning(checker, monkeypatch, tmp_path):
    stderr = (
        "C:\\x\\agent_health.py:28: DeprecationWarning: Importing from this module is deprecated\n"
        "  from lib.metrics_writer import write_invocation_metric\n"
    )
    result = _run_with(checker, monkeypatch, tmp_path, 1, '{"status": "not_configured"}\n', stderr)
    assert not result.passed
    assert result.message.endswith('{"status": "not_configured"}')


def test_failure_reason_falls_back_when_only_warnings(checker, monkeypatch, tmp_path):
    result = _run_with(checker, monkeypatch, tmp_path, 1, "", "x.py:1: UserWarning: w\n  code\n")
    assert result.message.endswith("exit code non-zero")


def test_traceback_reason_is_kept(checker, monkeypatch, tmp_path):
    stderr = "Traceback (most recent call last):\n  File \"x.py\", line 1\nRuntimeError: token missing\n"
    result = _run_with(checker, monkeypatch, tmp_path, 1, "", stderr)
    assert result.message.endswith("RuntimeError: token missing")
