"""
Unit tests for Phase 2 preflight.py additions:
  - Advisory mode (--advisory flag, exit codes, JSON output)
  - check_profile_completeness() (near-empty vs populated)
  - health-check.md template seeding (only seeds when missing or unstructured)

Ref: specs/vm-hardening.md Phase 2
"""

from __future__ import annotations

import json
import os
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
# check_channel_config
# ---------------------------------------------------------------------------

class TestCheckChannelConfig:
    """check_channel_config catches the three Telegram silent-failure misconfigs."""

    def _write_channels_yaml(self, config_dir, content: str) -> None:
        (config_dir / "channels.yaml").write_text(content, encoding="utf-8")

    def test_no_channels_yaml_passes(self, tmp_path):
        """Missing channels.yaml is not an error — channel push is optional."""
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert result.passed
        assert result.severity == "P1"

    def test_no_enabled_channels_passes(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        self._write_channels_yaml(config_dir, "defaults:\n  push_enabled: true\nchannels:\n  telegram:\n    enabled: false\n")
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert result.passed

    def test_empty_primary_id_fails(self, tmp_path):
        """Empty recipients.primary.id must be caught — causes CHANNEL_REJECT."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        self._write_channels_yaml(config_dir,
            "defaults:\n  push_enabled: true\n  listener_host: my-host\n"
            "channels:\n  telegram:\n    enabled: true\n    recipients:\n      primary:\n        id: ''\n"
        )
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert not result.passed
        assert result.severity == "P1"
        assert "recipients.primary.id" in result.fix_hint
        assert "setup_channel.py" in result.fix_hint

    def test_placeholder_listener_host_fails(self, tmp_path):
        """listener_host=NOT-THIS-HOST-XYZ causes listener to skip every machine."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        self._write_channels_yaml(config_dir,
            "defaults:\n  push_enabled: true\n  listener_host: NOT-THIS-HOST-XYZ\n"
            "channels:\n  telegram:\n    enabled: true\n    recipients:\n      primary:\n        id: '12345'\n"
        )
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert not result.passed
        assert "listener_host" in result.fix_hint
        assert "set-listener-host" in result.fix_hint

    def test_empty_listener_host_fails(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        self._write_channels_yaml(config_dir,
            "defaults:\n  push_enabled: true\n  listener_host: ''\n"
            "channels:\n  telegram:\n    enabled: true\n    recipients:\n      primary:\n        id: '12345'\n"
        )
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert not result.passed
        assert "listener_host" in result.fix_hint

    def test_push_disabled_fails(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        self._write_channels_yaml(config_dir,
            "defaults:\n  push_enabled: false\n  listener_host: my-host\n"
            "channels:\n  telegram:\n    enabled: true\n    recipients:\n      primary:\n        id: '12345'\n"
        )
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert not result.passed
        assert "push_enabled" in result.fix_hint

    def test_multiple_issues_combines_hints(self, tmp_path):
        """All three problems present — hint should mention all three."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        self._write_channels_yaml(config_dir,
            "defaults:\n  push_enabled: false\n  listener_host: ''\n"
            "channels:\n  telegram:\n    enabled: true\n    recipients:\n      primary:\n        id: ''\n"
        )
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert not result.passed
        assert "3 channel misconfiguration" in result.message
        assert "listener_host" in result.fix_hint
        assert "push_enabled" in result.fix_hint
        assert "recipients.primary.id" in result.fix_hint

    def test_fully_configured_passes(self, tmp_path):
        """All three fields properly set — check passes cleanly."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        self._write_channels_yaml(config_dir,
            "defaults:\n  push_enabled: true\n  listener_host: my-laptop\n"
            "channels:\n  telegram:\n    enabled: true\n    recipients:\n      primary:\n        id: '8679396255'\n"
        )
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_channel_config()
        assert result.passed
        assert "valid" in result.message


# ---------------------------------------------------------------------------
# check_profile_completeness
# ---------------------------------------------------------------------------

class TestCheckProfileCompleteness:
    """Profile completeness check fires on near-empty, passes silently on full."""

    def test_missing_profile_passes_silently(self, tmp_path):
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_profile_completeness()
        assert result.passed
        assert result.severity == "P1"

    def test_near_empty_profile_fails_with_hint(self, tmp_path):
        """A 4-line skeleton (schema_version + age_recipient only) should fail."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        profile = config_dir / "user_profile.yaml"
        profile.write_text("schema_version: '1.0'\nage_recipient: 'age1abc'\n")
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_profile_completeness()
        assert not result.passed
        assert result.severity == "P1"
        assert "near-empty" in result.message
        assert result.fix_hint

    def test_populated_profile_passes_silently(self, tmp_path):
        """A profile with >10 keys should pass without comment."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        profile = config_dir / "user_profile.yaml"
        # Write a profile with plenty of keys
        profile.write_text(
            "schema_version: '1.0'\n"
            "family:\n"
            "  primary_user:\n"
            "    name: Jane\n"
            "    nickname: Jane\n"
            "    emails:\n"
            "      gmail: jane@example.com\n"
            "location:\n"
            "  city: Portland\n"
            "  state: OR\n"
            "  timezone: America/Los_Angeles\n"
            "household:\n"
            "  type: single\n"
            "domains:\n"
            "  finance:\n"
            "    enabled: true\n"
        )
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_profile_completeness()
        assert result.passed
        assert "populated" in result.message

    def test_missing_required_fields_listed_in_hint(self, tmp_path):
        """Missing name/email/timezone surfaced in fix_hint."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        profile = config_dir / "user_profile.yaml"
        profile.write_text("schema_version: '1.0'\n")
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_profile_completeness()
        assert not result.passed
        # Required fields should be in hint
        assert "family.primary_user.name" in result.fix_hint

    def test_unreadable_yaml_fails_gracefully(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        profile = config_dir / "user_profile.yaml"
        profile.write_text(":\t: invalid yaml ]]]\n")
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)):
            result = pf.check_profile_completeness()
        assert not result.passed
        assert result.severity == "P1"


# ---------------------------------------------------------------------------
# Advisory mode — format_results
# ---------------------------------------------------------------------------

class TestAdvisoryMode:
    """Advisory mode reclassifies P0 failures as non-blocking ADVISORY."""

    def _make_p0_failure(self, name: str = "test check") -> pf.CheckResult:
        return pf.CheckResult(name, "P0", False, "something failed", "run this fix")

    def _make_p0_pass(self) -> pf.CheckResult:
        return pf.CheckResult("passing check", "P0", True, "all good")

    def test_advisory_exits_0_with_p0_failure(self):
        checks = [self._make_p0_failure()]
        _, all_passed = pf.format_results(checks, advisory=True)
        assert all_passed is True

    def test_strict_exits_1_with_p0_failure(self):
        checks = [self._make_p0_failure()]
        _, all_passed = pf.format_results(checks, advisory=False)
        assert all_passed is False

    def test_advisory_output_contains_advisory_prefix(self):
        checks = [self._make_p0_failure()]
        output, _ = pf.format_results(checks, advisory=True)
        assert "ADVISORY" in output

    def test_advisory_output_contains_warning_mode_header(self):
        checks = [self._make_p0_failure()]
        output, _ = pf.format_results(checks, advisory=True)
        assert "ADVISORY MODE" in output

    def test_non_advisory_output_shows_no_go(self):
        checks = [self._make_p0_failure()]
        output, _ = pf.format_results(checks, advisory=False)
        assert "NO-GO" in output

    def test_advisory_go_when_all_pass(self):
        checks = [self._make_p0_pass()]
        output, all_passed = pf.format_results(checks, advisory=True)
        assert all_passed is True

    def test_advisory_mode_note_in_summary_when_p0_failures(self):
        checks = [self._make_p0_failure()]
        output, _ = pf.format_results(checks, advisory=True)
        assert "advisory" in output.lower()


# ---------------------------------------------------------------------------
# Advisory mode — JSON output
# ---------------------------------------------------------------------------

class TestAdvisoryJsonOutput:
    """JSON output includes advisory_mode and degradation_list."""

    def _run_main_json_advisory(self, tmp_path, monkeypatch):
        """Run main() with --json --advisory and capture stdout."""
        import io
        from contextlib import ExitStack, redirect_stdout

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Minimal valid profile
        (config_dir / "user_profile.yaml").write_text(
            "schema_version: '1.0'\n"
            "family:\n  primary_user:\n    name: Jane\n    emails:\n      gmail: j@ex.com\n"
            "location:\n  timezone: America/Los_Angeles\n"
        )
        monkeypatch.chdir(tmp_path)

        _ok = lambda name, pri="P1": pf.CheckResult(name, pri, True, "ok")  # noqa: E731
        patches = [
            patch.object(pf, "ARTHA_DIR", str(tmp_path)),
            patch.object(pf, "STATE_DIR",  str(tmp_path / "state")),
            patch.object(pf, "TOKEN_DIR",  str(tmp_path / ".tokens")),
            patch.object(pf, "SCRIPTS_DIR", str(tmp_path / "scripts")),
            patch("preflight.check_keyring_backend",   return_value=_ok("keyring backend", "P0")),
            patch("preflight.check_vault_health",
                  return_value=pf.CheckResult("vault.py health", "P0", False, "age missing")),
            patch("preflight.check_vault_lock",        return_value=_ok("vault lock state", "P0")),
            patch("preflight.check_oauth_token",       return_value=_ok("OAuth token", "P0")),
            patch("preflight.check_pii_guard",         return_value=_ok("pii_guard.py test", "P0")),
            patch("preflight.check_state_directory",   return_value=_ok("state directory", "P0")),
            patch("preflight.check_state_templates",   return_value=_ok("state templates")),
            patch("preflight.check_token_freshness",   return_value=_ok("token freshness")),
            patch("preflight.check_open_items",        return_value=_ok("open_items.md")),
            patch("preflight.check_briefings_directory", return_value=_ok("briefings directory")),
            patch("preflight.check_msgraph_token",     return_value=_ok("Microsoft Graph token")),
            patch("preflight.check_workiq",
                  return_value=pf.CheckResult("WorkIQ Calendar", "P1", True, "skipped")),
            patch("preflight.check_channel_config",    return_value=_ok("channel config")),
            patch("preflight.check_channel_health",    return_value=_ok("channel health")),
            patch("preflight.check_dep_freshness",     return_value=_ok("venv dependencies")),
            patch("preflight.check_profile_completeness",
                  return_value=_ok("user_profile completeness")),
            patch("sys.argv", ["preflight.py", "--json", "--advisory"]),
        ]

        buf = io.StringIO()
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with pytest.raises(SystemExit) as exc_info:
                with redirect_stdout(buf):
                    pf.main()

        return exc_info.value.code, buf.getvalue()

    def test_advisory_json_exits_zero_with_p0_failure(self, tmp_path, monkeypatch):
        code, output = self._run_main_json_advisory(tmp_path, monkeypatch)
        assert code == 0

    def test_advisory_json_contains_advisory_mode_true(self, tmp_path, monkeypatch):
        _, output = self._run_main_json_advisory(tmp_path, monkeypatch)
        data = json.loads(output)
        assert data["advisory_mode"] is True

    def test_advisory_json_contains_degradation_list(self, tmp_path, monkeypatch):
        _, output = self._run_main_json_advisory(tmp_path, monkeypatch)
        data = json.loads(output)
        assert "degradation_list" in data
        assert isinstance(data["degradation_list"], list)
        # vault.py health failed → should appear in degradation_list
        assert "vault.py health" in data["degradation_list"]


# ---------------------------------------------------------------------------
# Health-check.md template seeding
# ---------------------------------------------------------------------------

class TestHealthCheckTemplateSeeding:
    """check_state_templates respects special health-check.md seeding logic."""

    def _make_templates_dir(self, tmp_path) -> Path:
        templates = tmp_path / "state" / "templates"
        templates.mkdir(parents=True)
        # Create health-check.md template
        (templates / "health-check.md").write_text(
            "---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 0\n---\n"
        )
        return templates

    def test_seeds_health_check_when_absent(self, tmp_path):
        """Template is copied when state/health-check.md doesn't exist."""
        self._make_templates_dir(tmp_path)
        state_dir = tmp_path / "state"
        with patch.object(pf, "STATE_DIR", str(state_dir)):
            result = pf.check_state_templates(auto_fix=True)
        assert "health-check.md" in result.message or result.passed
        assert (state_dir / "health-check.md").exists()

    def test_does_not_overwrite_structured_health_check(self, tmp_path):
        """Existing health-check.md with last_catch_up: field is NOT overwritten."""
        self._make_templates_dir(tmp_path)
        state_dir = tmp_path / "state"
        # Create a "real" health-check with existing catch-up data
        existing_content = (
            "---\nschema_version: '1.1'\nlast_catch_up: 2026-03-14T22:00:00Z\n"
            "catch_up_count: 5\n---\n## History\n- Run 1\n"
        )
        (state_dir / "health-check.md").write_text(existing_content)
        with patch.object(pf, "STATE_DIR", str(state_dir)):
            pf.check_state_templates(auto_fix=True)
        # Original content should be preserved
        assert (state_dir / "health-check.md").read_text() == existing_content


# ---------------------------------------------------------------------------
# check_vault_health — exit code 2 (soft warning) is P1, not P0
# ---------------------------------------------------------------------------

class TestVaultHealthExitCodes:
    """Verify preflight maps vault.py exit codes to correct severity."""

    def test_vault_health_exit2_is_p1_not_p0(self, tmp_path):
        """vault.py health exit 2 (soft warnings) must produce a P1 result, not a P0 block."""
        mock_proc = MagicMock()
        mock_proc.returncode = 2
        mock_proc.stdout = "⚠  Backup files: 3 orphaned .bak file(s) — run: python3 scripts/vault.py encrypt\nvault.py health: OK (warnings present)"
        mock_proc.stderr = ""
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)), \
             patch("subprocess.run", return_value=mock_proc):
            result = pf.check_vault_health()
        assert result.severity == "P1", "exit 2 must be P1, not P0"
        assert not result.passed
        assert "python3 scripts/vault.py encrypt" in (result.fix_hint or "")

    def test_vault_health_exit1_is_p0(self, tmp_path):
        """vault.py health exit 1 (hard failure) must produce a P0 block."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "✗  age binary not found\nvault.py health: FAILED"
        mock_proc.stderr = ""
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)), \
             patch("subprocess.run", return_value=mock_proc):
            result = pf.check_vault_health()
        assert result.severity == "P0"
        assert not result.passed

    def test_vault_health_exit0_is_p0_pass(self, tmp_path):
        """vault.py health exit 0 (fully healthy) must produce a P0 pass."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "vault.py health: OK"
        mock_proc.stderr = ""
        with patch.object(pf, "ARTHA_DIR", str(tmp_path)), \
             patch("subprocess.run", return_value=mock_proc):
            result = pf.check_vault_health()
        assert result.severity == "P0"
        assert result.passed


# ---------------------------------------------------------------------------
# check_vault_lock — stale locks are auto-cleared without --fix
# ---------------------------------------------------------------------------

class TestVaultLockAutoClean:
    """Stale locks are cleared unconditionally; active locks require --fix."""

    def _write_lock(self, path: Path, pid: int = 0, age_seconds: int = 0) -> None:
        import json, time
        past_ts = time.time() - age_seconds
        path.write_text(json.dumps({"pid": pid, "ts": past_ts}))
        # Backdate the file's mtime so os.path.getmtime() matches the JSON ts
        os.utime(path, (past_ts, past_ts))

    def test_stale_lock_by_age_auto_cleared_no_fix_flag(self, tmp_path):
        """A lock older than 30m is removed unconditionally (auto_fix=False)."""
        lock_path = tmp_path / ".artha-decrypted"
        self._write_lock(lock_path, pid=0, age_seconds=3600)  # 60m old
        with patch.object(pf, "LOCK_FILE", str(lock_path)):
            result = pf.check_vault_lock(auto_fix=False)
        assert result.passed, "stale lock should auto-clear even without --fix"
        assert result.auto_fixed
        assert not lock_path.exists()

    def test_stale_lock_by_dead_pid_auto_cleared(self, tmp_path):
        """A lock held by a dead PID is removed unconditionally."""
        lock_path = tmp_path / ".artha-decrypted"
        self._write_lock(lock_path, pid=999999999, age_seconds=10)  # recent but dead pid
        with patch.object(pf, "LOCK_FILE", str(lock_path)):
            result = pf.check_vault_lock(auto_fix=False)
        assert result.passed
        assert result.auto_fixed
        assert not lock_path.exists()

    def test_no_lock_file_passes(self, tmp_path):
        """No lock file → immediate P0 pass."""
        lock_path = tmp_path / ".artha-decrypted"
        with patch.object(pf, "LOCK_FILE", str(lock_path)):
            result = pf.check_vault_lock()
        assert result.passed
        assert result.severity == "P0"
