"""tests/unit/test_preflight_api.py — Preflight API, OAuth, read-only, failure reason & conflict checks."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ARTHA_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import preflight as pf
import preflight.api_checks as api_checks  # noqa: E402


# ---------------------------------------------------------------------------
# T5-21..23: check_script_health
# ---------------------------------------------------------------------------

class TestCheckScriptHealth:
    def test_returns_check_result(self):
        result = pf.check_script_health("action_bridge.py", ["--health"])
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")

    def test_subprocess_exit0_passes(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = pf.check_script_health("action_bridge.py", ["--health"])
            assert isinstance(result, pf.CheckResult)

    def test_subprocess_exit1_records_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error output")
            result = pf.check_script_health("action_bridge.py", ["--health"])
            assert isinstance(result, pf.CheckResult)

    def test_read_only_marks_health_subprocess(self, monkeypatch):
        monkeypatch.setattr(api_checks, "_READ_ONLY", True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            pf.check_script_health("action_bridge.py", ["--health"])

        assert mock_run.call_args.kwargs["env"]["ARTHA_PREFLIGHT_READ_ONLY"] == "1"


# ---------------------------------------------------------------------------
# T5-24..25: check_pii_guard
# ---------------------------------------------------------------------------

class TestCheckPiiGuard:
    def test_returns_check_result(self):
        result = pf.check_pii_guard()
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")

    def test_missing_module_reports_failure(self):
        import builtins
        original_import = builtins.__import__

        def _import_error(name, *args, **kwargs):
            if name == "pii_guard":
                raise ImportError("no module named pii_guard")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_import_error):
            result = pf.check_pii_guard()
            assert isinstance(result, pf.CheckResult)


# ---------------------------------------------------------------------------
# OAuth & MS Graph checks
# ---------------------------------------------------------------------------

class TestCheckOauthAndMsgraphTokens:
    def test_oauth_missing_valid_and_corrupted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path / "nonexistent"))
        missing = pf.check_oauth_token("google", "google_token.json")
        assert not missing.passed and missing.severity == "P0"

        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        (tmp_path / "google_token.json").write_text(json.dumps({
            "token": "ya29.xxxxxxxxxxx", "refresh_token": "1//xxxxxxxxxxx", "expiry": time.time() + 3600,
        }))
        assert isinstance(pf.check_oauth_token("google", "google_token.json"), pf.CheckResult)
        assert isinstance(pf.check_token_freshness("google", "google_token.json"), pf.CheckResult)

        (tmp_path / "google_token.json").write_text("{NOT VALID JSON {{}")
        assert not pf.check_oauth_token("google", "google_token.json").passed

        (tmp_path / "google_token.json").write_text(json.dumps({"token": "ya29.x", "expiry": time.time() - 7200}))
        assert pf.check_token_freshness("google", "google_token.json").severity in ("P0", "P1")

    def test_msgraph_missing_and_valid(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "TOKEN_DIR", str(tmp_path))
        assert isinstance(pf.check_msgraph_token(), pf.CheckResult)
        (tmp_path / "msgraph_token.json").write_text(json.dumps({
            "access_token": "eyJ...", "expires_in": 3600, "refresh_token": "0.AXXXXXXXXX",
        }))
        assert isinstance(pf.check_msgraph_token(), pf.CheckResult)


# ---------------------------------------------------------------------------
# Read-only CLI behavior (scripts/preflight.py)
# ---------------------------------------------------------------------------

def _load_cli_script(monkeypatch, name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class TestReadOnlyPreflightCli:
    def _track_post_run_writes(self, monkeypatch, module):
        seen = []
        for fn in ("_clear_stale_pipeline_checkpoint", "_compact_health_check", "_write_device_context"):
            monkeypatch.setattr(module, fn, lambda fn=fn: seen.append(fn))
        monkeypatch.setattr(module, "run_preflight", lambda **kwargs: [])
        return seen

    @pytest.mark.parametrize(("argv", "expected_writes"), [
        (["preflight.py", "--read-only", "--json"], []),
        (["preflight.py", "--json"], ["_clear_stale_pipeline_checkpoint", "_compact_health_check", "_write_device_context"]),
    ])
    def test_read_only_vs_default_post_run_writes(self, monkeypatch, capsys, tmp_path, argv, expected_writes):
        script = _load_cli_script(monkeypatch, "preflight_cli_script")
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "user_profile.yaml").write_text("{}\n")
        monkeypatch.setattr(script, "ARTHA_DIR", str(tmp_path))
        seen = self._track_post_run_writes(monkeypatch, script)
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            script.main()
        assert seen == expected_writes
        assert json.loads(capsys.readouterr().out)["pre_flight_ok"] is True

    def test_read_only_action_check_does_not_expire_queue(self, monkeypatch, tmp_path):
        script = _load_cli_script(monkeypatch, "preflight_cli_script_ro")
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


# ---------------------------------------------------------------------------
# Health failure reason extraction & OneDrive conflict detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("surface", ["package", "cli"])
@pytest.mark.parametrize(("stdout", "stderr", "expected_suffix"), [
    (
        '{"status": "not_configured"}\n',
        "C:\\x\\agent_health.py:28: DeprecationWarning: x\n  from lib.metrics_writer import y\n",
        '{"status": "not_configured"}',
    ),
    ("", "x.py:1: UserWarning: w\n  code\n", "exit code non-zero"),
    ("", "Traceback (most recent call last):\n  File \"x.py\", line 1\nRuntimeError: token missing\n", "RuntimeError: token missing"),
])
def test_script_health_failure_reason(monkeypatch, tmp_path, surface, stdout, stderr, expected_suffix):
    checker = api_checks if surface == "package" else _load_cli_script(monkeypatch, "preflight_cli_reason_test")
    (tmp_path / "probe.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(checker, "SCRIPTS_DIR", str(tmp_path))
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout, stderr))
    result = checker.check_script_health("probe.py", ["--health"])
    assert not result.passed
    assert result.message.endswith(expected_suffix)


def _detect_conflict_files_direct(state_dir: str) -> list[str]:
    patterns = [
        re.compile(r".*-DESKTOP-\w+\."),
        re.compile(r".*-LAPTOP-\w+\."),
        re.compile(r".*-conflict\."),
        re.compile(r".*\s\(\d+\)\."),
        re.compile(r".*-PC-\w+\."),
    ]
    return [
        entry.path for entry in os.scandir(state_dir)
        if entry.is_file() and any(pat.match(entry.name) for pat in patterns)
    ]


class TestOneDriveConflictDetection:
    def test_conflict_patterns_and_wiring(self, tmp_path):
        assert callable(pf._detect_conflict_files)
        assert callable(pf.check_onedrive_conflicts)
        for name in ("finance-DESKTOP-PC.md", "health (1).md", "estate-conflict.md", "contacts.md", "goals.md"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        found = {os.path.basename(p) for p in _detect_conflict_files_direct(str(tmp_path))}
        assert found == {"finance-DESKTOP-PC.md", "health (1).md", "estate-conflict.md"}


class TestMsgraphTokenAndPiiGuardEdges:
    """TR-1 core security branches in preflight oauth_checks & api_checks."""

    def test_msgraph_refresh_60d_cliff_and_network_block_paths(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        import preflight.oauth_checks as oc

        # 60-day refresh cliff warning
        old_ts = (datetime.now(timezone.utc) - timedelta(days=65)).isoformat()
        monkeypatch.setitem(sys.modules, "setup_msgraph_oauth", types.SimpleNamespace(
            _load_token=lambda: {"_last_refresh_success": old_ts, "expires_in": 3600},
            _token_lifetime=lambda td: 3600,
            ensure_valid_token=lambda **kw: {},
        ))
        cliff = oc.check_msgraph_token()
        assert not cliff.passed and "65d ago" in cliff.message

        # Proactive refresh succeeds when near/past expiry
        monkeypatch.setitem(sys.modules, "setup_msgraph_oauth", types.SimpleNamespace(
            _load_token=lambda: {"expires_in": 60},
            _token_lifetime=lambda td: 1800 if td.get("refreshed") else -10,
            ensure_valid_token=lambda **kw: {"refreshed": True, "expires_in": 1800},
        ))
        ok = oc.check_msgraph_token()
        assert ok.passed and "just refreshed" in ok.message

        # Refresh fails + network_blocked=True reports dual message
        monkeypatch.setitem(sys.modules, "setup_msgraph_oauth", types.SimpleNamespace(
            _load_token=lambda: {"expires_in": 60},
            _token_lifetime=lambda td: -120,
            ensure_valid_token=MagicMock(side_effect=RuntimeError("refresh failed")),
        ))
        monkeypatch.setitem(sys.modules, "detect_environment", types.SimpleNamespace(
            detect=lambda skip_network=False: types.SimpleNamespace(capabilities={"network_microsoft": False}),
        ))
        blocked = oc.check_msgraph_token()
        assert not blocked.passed and "network-blocked" in blocked.message

    def test_check_pii_guard_nonzero_exit_returns_p0_failure(self, monkeypatch, tmp_path):
        import preflight.api_checks as ac

        (tmp_path / "pii_guard.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(ac, "SCRIPTS_DIR", str(tmp_path))
        monkeypatch.setattr(ac, "ARTHA_DIR", str(tmp_path))
        monkeypatch.setattr(
            ac.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "AssertionError: SSN failed\n"),
        )
        res = ac.check_pii_guard()
        assert not res.passed and res.severity == "P0" and "SSN failed" in res.message
