"""RF-17 synthetic CLI and read-only contracts."""
from __future__ import annotations

import json
import os
import sys
import time
import types
from unittest.mock import Mock

import pytest

import preflight as pf
import preflight.integration_checks as integration_checks
import preflight.oauth_checks as oauth_checks
import preflight.runtime_checks as runtime_checks
import preflight.vault_checks as vault_checks


def _run_main(tmp_path, monkeypatch, arguments, checks):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "user_profile.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "run_preflight", Mock(return_value=checks))
    writes = []
    monkeypatch.setattr(pf, "_clear_stale_pipeline_checkpoint", lambda: writes.append("checkpoint"))
    monkeypatch.setattr(pf, "_compact_health_check", lambda: writes.append("health"))
    monkeypatch.setattr(pf, "_write_device_context", lambda: writes.append("device"))
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit) as exc:
        pf.main()
    return exc.value.code, writes


def test_advisory_downgrades_p0_exit_only(monkeypatch, capsys, tmp_path):
    code, writes = _run_main(
        tmp_path,
        monkeypatch,
        ["preflight.py", "--advisory"],
        [pf.CheckResult("private storage", "P0", False, "blocked")],
    )
    assert code == 0
    assert "ADVISORY" in capsys.readouterr().out
    assert writes == ["checkpoint", "health", "device"]


def test_strict_p0_failure_exits_one(monkeypatch, tmp_path):
    code, _ = _run_main(
        tmp_path,
        monkeypatch,
        ["preflight.py", "--quiet"],
        [pf.CheckResult("private storage", "P0", False, "blocked")],
    )
    assert code == 1


def test_read_only_json_runs_no_post_write_and_reports_no_enrollment(monkeypatch, capsys, tmp_path):
    code, writes = _run_main(
        tmp_path,
        monkeypatch,
        ["preflight.py", "--read-only", "--json"],
        [pf.CheckResult("storage", "P1", True, "write probe suppressed")],
    )
    assert code == 0
    assert writes == []
    output = json.loads(capsys.readouterr().out)
    assert output["pre_flight_ok"] is True
    assert output["read_only"] is True


def test_read_only_directory_checks_do_not_open_writes(monkeypatch, tmp_path):
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "STATE_DIR", str(tmp_path / "state"))
    original_open = open

    def deny_write(path, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError(f"unexpected write: {path}")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(pf, "_READ_ONLY", True)
    monkeypatch.setattr("builtins.open", deny_write)
    assert pf.check_state_directory().passed
    assert "suppressed" in pf.check_briefings_directory().message
    assert not (tmp_path / "state" / ".preflight_write_test").exists()


def test_read_only_stale_lock_is_reported_without_removal(monkeypatch, tmp_path):
    lock = tmp_path / ".artha-decrypted"
    lock.write_text('{"pid": 0}', encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    monkeypatch.setattr(vault_checks, "LOCK_FILE", str(lock))
    monkeypatch.setattr(vault_checks, "_READ_ONLY", True)
    result = vault_checks.check_vault_lock()
    assert not result.passed
    assert lock.exists()
    assert "suppressed" in result.message


def test_read_only_token_checks_do_not_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(oauth_checks, "_READ_ONLY", True)
    ensure = Mock(side_effect=AssertionError("must not refresh credentials"))
    monkeypatch.setitem(
        sys.modules,
        "setup_msgraph_oauth",
        types.SimpleNamespace(
            _load_token=lambda: {"expires_in": 1},
            _token_lifetime=lambda token: -1,
            ensure_valid_token=ensure,
        ),
    )
    result = oauth_checks.check_msgraph_token()
    assert not result.passed
    ensure.assert_not_called()

    observed = {}
    monkeypatch.setitem(
        sys.modules,
        "google_auth",
        types.SimpleNamespace(
            validate_token_freshness=lambda **kwargs: observed.update(kwargs) or {
                "ok": True,
                "message": "valid",
            }
        ),
    )
    assert oauth_checks.check_token_freshness("Gmail", "gmail-oauth-token.json").passed
    assert observed["proactive_refresh"] is False


def test_read_only_runtime_checks_skip_inventory_and_cleanup_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_checks, "_READ_ONLY", True)
    monkeypatch.setattr(runtime_checks, "ARTHA_DIR", str(tmp_path))
    discovery_writer = Mock(side_effect=AssertionError("must not write inventory"))
    skill_writer = Mock(side_effect=AssertionError("must not write inventory"))
    monkeypatch.setitem(
        sys.modules,
        "mcp_discovery",
        types.SimpleNamespace(
            discover_mcp=lambda root: {"counts": {"servers": 0, "config_files": 0, "errors": 0}},
            write_discovery=discovery_writer,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "skill_index",
        types.SimpleNamespace(
            build_skill_index=lambda root: {
                "counts": {"enabled": 0, "configured": 0},
                "skills": [],
                "enabled_missing_modules": [],
            },
            write_skill_index=skill_writer,
        ),
    )
    assert runtime_checks.check_mcp_discovery().passed
    assert runtime_checks.check_skill_index().passed
    assert "suppressed" in runtime_checks.check_cleanup_partials().message
    assert "suppressed" in runtime_checks.check_evidence_lake_ttl().message
    discovery_writer.assert_not_called()
    skill_writer.assert_not_called()


def test_read_only_action_check_does_not_expire(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "artha_config.yaml").write_text(
        "harness:\n  actions:\n    enabled: true\n",
        encoding="utf-8",
    )
    expired = Mock(return_value=1)

    class Executor:
        def __init__(self, root):
            pass

        expire_stale = expired

        def run_health_checks(self):
            return {"reminder_create": True}

    monkeypatch.setattr(integration_checks, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(integration_checks, "_READ_ONLY", True)
    monkeypatch.setitem(
        sys.modules,
        "action_executor",
        types.SimpleNamespace(ActionExecutor=Executor),
    )
    result = integration_checks.check_action_handlers()
    assert result.passed
    expired.assert_not_called()
