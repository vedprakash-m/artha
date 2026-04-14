"""
tests/unit/test_vault_signal_handlers.py
==========================================
DEBT-001: Verify atexit and signal handler cleanup functions are correctly
structured and integrated in vault.py.

These tests use the module-level functions directly without actually running
the vault decrypt flow (which requires age credentials).
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


class TestVaultSignalHandlers:
    """A1–A5 from DEBT-001."""

    def test_functions_exist(self):
        """All cleanup-related functions are exported from vault.py."""
        import vault
        assert callable(getattr(vault, "_register_cleanup_handlers", None)), \
            "_register_cleanup_handlers missing from vault.py"
        assert callable(getattr(vault, "_atexit_encrypt", None)), \
            "_atexit_encrypt missing from vault.py"
        assert callable(getattr(vault, "_signal_encrypt_handler", None)), \
            "_signal_encrypt_handler missing from vault.py"

    def test_reentrancy_guard_exists(self):
        """Module-level _ENCRYPTING flag prevents double-encrypt."""
        import vault
        assert hasattr(vault, "_ENCRYPTING"), "_ENCRYPTING flag missing"
        assert isinstance(vault._ENCRYPTING, bool)

    def test_atexit_noops_when_no_lock(self, tmp_path, monkeypatch):
        """_atexit_encrypt is a no-op when LOCK_FILE does not exist (A3)."""
        import vault, foundation
        monkeypatch.setitem(foundation._config, "LOCK_FILE", tmp_path / "nonexistent_lock")
        monkeypatch.setattr(vault, "_ENCRYPTING", False)
        # Should not raise and should not call do_encrypt
        with patch.object(vault, "do_encrypt") as mock_enc:
            vault._atexit_encrypt()
            mock_enc.assert_not_called()

    def test_atexit_calls_encrypt_when_locked(self, tmp_path, monkeypatch):
        """_atexit_encrypt calls do_encrypt() when LOCK_FILE exists (A1)."""
        import vault, foundation
        lock = tmp_path / ".artha-decrypted"
        lock.write_text("{}")
        monkeypatch.setitem(foundation._config, "LOCK_FILE", lock)
        monkeypatch.setattr(vault, "_ENCRYPTING", False)
        with patch.object(vault, "do_encrypt") as mock_enc:
            vault._atexit_encrypt()
            mock_enc.assert_called_once()

    def test_reentrancy_guard_prevents_double_encrypt(self, tmp_path, monkeypatch):
        """A5: _ENCRYPTING = True prevents atexit handler from re-entering do_encrypt."""
        import vault, foundation
        lock = tmp_path / ".artha-decrypted"
        lock.write_text("{}")
        monkeypatch.setitem(foundation._config, "LOCK_FILE", lock)
        monkeypatch.setattr(vault, "_ENCRYPTING", True)  # simulate already-encrypting
        with patch.object(vault, "do_encrypt") as mock_enc:
            vault._atexit_encrypt()
            mock_enc.assert_not_called()  # re-entrancy guard fired

    def test_signal_handler_calls_encrypt_when_locked(self, tmp_path, monkeypatch):
        """A2: _signal_encrypt_handler encrypts and calls sys.exit on SIGTERM."""
        import vault, foundation
        lock = tmp_path / ".artha-decrypted"
        lock.write_text("{}")
        monkeypatch.setitem(foundation._config, "LOCK_FILE", lock)
        monkeypatch.setattr(vault, "_ENCRYPTING", False)
        with patch.object(vault, "do_encrypt") as mock_enc:
            with pytest.raises(SystemExit) as exc_info:
                vault._signal_encrypt_handler(signal.SIGTERM, None)
            mock_enc.assert_called_once()
            # sys.exit(128 + SIGTERM) = sys.exit(143) on POSIX
            assert exc_info.value.code == 128 + signal.SIGTERM

    def test_register_cleanup_handlers_registers_atexit(self, tmp_path, monkeypatch):
        """_register_cleanup_handlers registers the atexit callback."""
        import vault, foundation
        lock = tmp_path / ".artha-decrypted"
        monkeypatch.setitem(foundation._config, "LOCK_FILE", lock)

        registered: list[str] = []
        original_register = atexit.register

        def _track_register(fn, *args, **kwargs):
            registered.append(fn.__name__)
            return original_register(fn, *args, **kwargs)

        monkeypatch.setattr(atexit, "register", _track_register)
        vault._register_cleanup_handlers()
        assert "_atexit_encrypt" in registered, \
            "_atexit_encrypt not registered with atexit"


class TestVaultWatchdogPlist:
    """Verify watchdog interval was reduced to 120s (DEBT-001)."""

    def test_watchdog_interval_is_120s(self):
        plist_path = Path(_SCRIPTS_DIR) / "com.artha.vault-watchdog.plist"
        assert plist_path.exists(), "Watchdog plist not found"
        content = plist_path.read_text()
        # Must NOT have 300 as interval; MUST have 120
        assert "<integer>120</integer>" in content, \
            "Watchdog StartInterval must be 120 (2 minutes) per DEBT-001"
        assert "<integer>300</integer>" not in content, \
            "Old 300s interval still present in watchdog plist"
