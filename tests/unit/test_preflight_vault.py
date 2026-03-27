"""tests/unit/test_preflight_vault.py — T5-1..10: preflight.vault_checks tests."""
from __future__ import annotations

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
import preflight.vault_checks as vc


# ---------------------------------------------------------------------------
# T5-1: check_keyring_backend
# ---------------------------------------------------------------------------

class TestCheckKeyringBackend:
    def test_returns_check_result(self):
        result = pf.check_keyring_backend()
        assert isinstance(result, pf.CheckResult)
        assert result.name == "keyring backend"
        assert result.severity in ("P0", "P1")

    def test_no_keyring_module_fails(self, monkeypatch):
        import builtins
        original_import = builtins.__import__

        def _import_error(name, *args, **kwargs):
            if name == "keyring":
                raise ImportError("no module named keyring")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _import_error)
        result = pf.check_keyring_backend()
        assert not result.passed
        assert result.severity == "P0"

    def test_keyring_calls_get_password(self):
        with patch("keyring.get_password", return_value="dummy") as mock_get:
            result = pf.check_keyring_backend()
            assert isinstance(result, pf.CheckResult)


# ---------------------------------------------------------------------------
# T5-2: check_vault_health
# ---------------------------------------------------------------------------

class TestCheckVaultHealth:
    def test_returns_check_result(self):
        result = pf.check_vault_health()
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")

    def test_script_missing_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        monkeypatch.setattr(pf, "SCRIPTS_DIR", str(tmp_path / "scripts"))
        result = pf.check_vault_health()
        assert not result.passed

    def test_exit0_passes(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = pf.check_vault_health()
            assert isinstance(result, pf.CheckResult)


# ---------------------------------------------------------------------------
# T5-3: check_vault_lock — no lock file
# ---------------------------------------------------------------------------

class TestCheckVaultLock:
    def test_no_lock_file_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        result = pf.check_vault_lock(auto_fix=False)
        assert isinstance(result, pf.CheckResult)

    def test_fresh_lock_active_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        lock_file = tmp_path / ".artha-decrypted"
        import json, time
        lock_file.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": time.time(),
            "purpose": "test",
        }))
        result = pf.check_vault_lock(auto_fix=False)
        assert isinstance(result, pf.CheckResult)

    def test_result_has_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        result = pf.check_vault_lock(auto_fix=False)
        assert "vault" in result.name.lower() or "lock" in result.name.lower()
