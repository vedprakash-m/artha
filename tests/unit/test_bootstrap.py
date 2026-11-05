"""
Unit tests for scripts/_bootstrap.py

Tests cover:
  - _in_venv(): detection of active virtual environment
  - _venv_python(): platform-specific venv path selection
  - setup_artha_dir(): resolves ARTHA_DIR and sets env var
  - reexec_in_venv(mode="lightweight"): no-op path (no exec)
  - reexec_in_venv(mode="standard"): exits with error when venv missing
  - ARTHA_DIR constant: points to the repo root
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Ensure _bootstrap is importable from scripts/
_ARTHA_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import _bootstrap as bs


# ---------------------------------------------------------------------------
# ARTHA_DIR constant
# ---------------------------------------------------------------------------

class TestArthaDir:
    def test_artha_dir_is_repo_root(self):
        assert isinstance(bs.ARTHA_DIR, Path)
        assert bs.ARTHA_DIR.is_absolute()
        # _bootstrap.py lives in scripts/, parent is repo root
        assert (bs.ARTHA_DIR / "scripts" / "_bootstrap.py").exists()

    def test_artha_dir_has_config(self):
        assert (bs.ARTHA_DIR / "config").exists()


# ---------------------------------------------------------------------------
# _in_venv
# ---------------------------------------------------------------------------

class TestInVenv:
    def test_detects_active_venv_via_base_prefix(self, monkeypatch):
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        monkeypatch.setattr(sys, "prefix", "/home/user/.artha-venvs/.venv")
        assert bs._in_venv() is True

    def test_no_venv_when_prefixes_match(self, monkeypatch):
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        monkeypatch.setattr(sys, "prefix", "/usr")
        # also ensure no real_prefix attribute
        if hasattr(sys, "real_prefix"):
            monkeypatch.delattr(sys, "real_prefix")
        assert bs._in_venv() is False

    def test_detects_virtualenv_via_real_prefix(self, monkeypatch):
        monkeypatch.setattr(sys, "real_prefix", "/usr", raising=False)
        assert bs._in_venv() is True


# ---------------------------------------------------------------------------
# _venv_python
# ---------------------------------------------------------------------------

class TestVenvPython:
    def test_posix_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        py = bs._venv_python()
        assert "bin/python3" in py.as_posix()
        assert ".artha-venvs" in py.as_posix()

    def test_windows_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        py = bs._venv_python()
        assert "Scripts" in str(py) or "scripts" in str(py).lower()
        assert "python.exe" in str(py)


# ---------------------------------------------------------------------------
# setup_artha_dir
# ---------------------------------------------------------------------------

class TestSetupArthaDir:
    def test_returns_path(self):
        result = bs.setup_artha_dir()
        assert isinstance(result, Path)
        assert result.is_absolute()

    def test_sets_env_var(self, monkeypatch):
        monkeypatch.delenv("ARTHA_DIR", raising=False)
        bs.setup_artha_dir()
        assert "ARTHA_DIR" in os.environ
        assert os.environ["ARTHA_DIR"] == str(bs.ARTHA_DIR)

    def test_does_not_overwrite_existing_env_var(self, monkeypatch):
        monkeypatch.setenv("ARTHA_DIR", "/custom/path")
        bs.setup_artha_dir()
        assert os.environ["ARTHA_DIR"] == "/custom/path"


# ---------------------------------------------------------------------------
# reexec_in_venv — lightweight mode
# ---------------------------------------------------------------------------

class TestReexecLightweight:
    def test_lightweight_does_not_exec(self, monkeypatch):
        """In lightweight mode, reexec_in_venv should return immediately (no exec)."""
        exec_called = []
        monkeypatch.setattr(os, "execv", lambda *a: exec_called.append(a))
        bs.reexec_in_venv(mode="lightweight")
        assert exec_called == []

    def test_lightweight_sets_artha_dir(self, monkeypatch):
        monkeypatch.delenv("ARTHA_DIR", raising=False)
        monkeypatch.setattr(os, "execv", lambda *a: None)
        bs.reexec_in_venv(mode="lightweight")
        assert os.environ.get("ARTHA_DIR") == str(bs.ARTHA_DIR)


# ---------------------------------------------------------------------------
# reexec_in_venv — standard mode, already in venv
# ---------------------------------------------------------------------------

class TestReexecAlreadyInVenv:
    def test_no_exec_when_already_in_venv(self, monkeypatch):
        """If already in a venv, reexec_in_venv should not call os.execv."""
        monkeypatch.setattr(bs, "_in_venv", lambda: True)
        exec_called = []
        monkeypatch.setattr(os, "execv", lambda *a: exec_called.append(a))
        bs.reexec_in_venv(mode="standard")
        assert exec_called == []


# ---------------------------------------------------------------------------
# reexec_in_venv — standard mode, venv missing
# ---------------------------------------------------------------------------

class TestReexecMissingVenv:
    def test_exits_when_venv_missing(self, tmp_path, monkeypatch):
        """Standard mode without a venv should sys.exit(1)."""
        monkeypatch.delenv("ARTHA_NO_REEXEC", raising=False)
        monkeypatch.setattr(bs, "_in_venv", lambda: False)
        # Point venv python to a nonexistent path
        monkeypatch.setattr(bs, "_VENV_POSIX", tmp_path / "nonexistent_venv")
        monkeypatch.setattr(bs, "_VENV_WIN", tmp_path / "nonexistent_venv_win")
        monkeypatch.setattr(sys, "platform", "linux")

        with pytest.raises(SystemExit) as exc_info:
            bs.reexec_in_venv(mode="standard")
        assert exc_info.value.code == 1


class TestEnsureDepsInstalled:
    def test_does_not_install_when_all_required_modules_exist(self, monkeypatch, tmp_path):
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
        monkeypatch.setattr(bs, "_REQUIREMENTS", tmp_path / "requirements.txt")
        install = []
        monkeypatch.setattr(bs.subprocess, "run", lambda *a, **k: install.append((a, k)))

        bs._ensure_deps_installed()

        assert install == []

    def test_installs_missing_modules_and_rechecks_after_success(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(bs, "_REQUIREMENTS", tmp_path / "requirements.txt")
        bs._REQUIREMENTS.write_text("dependencies\n", encoding="utf-8")
        availability = iter([
            None,
            object(), object(), object(),
            object(), object(), object(), object(),
        ])
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: next(availability))
        install = []
        monkeypatch.setattr(
            bs.subprocess, "run",
            lambda *a, **k: (install.append((a, k)) or SimpleNamespace(returncode=0)),
        )

        bs._ensure_deps_installed()

        assert len(install) == 1

    def test_exits_if_dependency_install_fails(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(bs, "_REQUIREMENTS", tmp_path / "requirements.txt")
        bs._REQUIREMENTS.write_text("dependencies\n", encoding="utf-8")
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
        monkeypatch.setattr(bs.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))

        with pytest.raises(SystemExit) as exc_info:
            bs._ensure_deps_installed()

        assert exc_info.value.code == 1
        assert "pip install failed" in capsys.readouterr().err.lower()

    def test_exits_if_requirements_file_is_missing(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(bs, "_REQUIREMENTS", tmp_path / "missing-requirements.txt")
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)

        with pytest.raises(SystemExit) as exc_info:
            bs._ensure_deps_installed()

        assert exc_info.value.code == 1
        assert "requirements.txt not found" in capsys.readouterr().err.lower()

    def test_exits_if_install_succeeds_but_modules_remain_missing(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setattr(bs, "_REQUIREMENTS", tmp_path / "requirements.txt")
        bs._REQUIREMENTS.write_text("dependencies\n", encoding="utf-8")
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
        monkeypatch.setattr(bs.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

        with pytest.raises(SystemExit) as exc_info:
            bs._ensure_deps_installed()

        assert exc_info.value.code == 1
        assert "remain unavailable" in capsys.readouterr().err.lower()
