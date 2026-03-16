"""
_bootstrap.py — Artha cross-platform venv entry helper

Provides a single importable function that scripts call at their very top
to ensure they run inside the correct Python virtual environment.

Replaces ~30 lines of copy-pasted venv boilerplate in every script.

IMPORTANT: This module MUST use only stdlib (os, sys, pathlib, subprocess)
because it runs BEFORE the venv is activated — third-party packages are not
yet importable.

Usage in scripts:
    # Minimal (standard mode): re-exec in venv if not already there
    from _bootstrap import reexec_in_venv
    reexec_in_venv()

    # Preflight mode: create venv if missing, install requirements, then re-exec
    from _bootstrap import reexec_in_venv
    reexec_in_venv(mode="preflight")

    # Lightweight mode: only set ARTHA_DIR env var, no venv re-exec
    from _bootstrap import setup_artha_dir
    ARTHA_DIR = setup_artha_dir()

Modes:
    standard   — re-exec inside venv; abort if venv missing (scripts should not run raw)
    preflight  — create venv + install scripts/requirements.txt if absent, then re-exec
    lightweight — only resolve and return ARTHA_DIR (for scripts requiring no venv libs)

Ref: standardization.md §7.3, T-1A.2.x
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Canonical project root (two levels up from scripts/_bootstrap.py)
ARTHA_DIR: Path = Path(__file__).resolve().parent.parent

# Venv locations (outside the OneDrive sync folder — never inside the repo)
_VENV_POSIX: Path = Path.home() / ".artha-venvs" / ".venv"
_VENV_WIN: Path = Path.home() / ".artha-venvs" / ".venv-win"

_REQUIREMENTS: Path = ARTHA_DIR / "scripts" / "requirements.txt"


def _in_venv() -> bool:
    """Return True if running inside a virtual environment."""
    return (
        hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    )


def _venv_python() -> Path:
    """Return the venv Python executable path for the current platform."""
    if sys.platform == "win32":
        return _VENV_WIN / "Scripts" / "python.exe"
    return _VENV_POSIX / "bin" / "python3"


def _create_venv_and_install() -> None:
    """Create the venv (if absent) and install requirements.txt."""
    venv_dir = _VENV_WIN if sys.platform == "win32" else _VENV_POSIX
    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    if not venv_dir.exists():
        print(f"Creating venv at {venv_dir} …", file=sys.stderr)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
        )

    venv_py = _venv_python()
    if not venv_py.exists():
        print(
            f"ERROR: venv created but Python not found at {venv_py}",
            file=sys.stderr,
        )
        sys.exit(1)

    if _REQUIREMENTS.exists():
        print("Installing requirements …", file=sys.stderr)
        subprocess.run(
            [str(venv_py), "-m", "pip", "install", "--quiet", "-r", str(_REQUIREMENTS)],
            check=True,
        )


def _ensure_deps_installed() -> None:
    """Verify critical project dependencies are importable. If any are missing,
    run `pip install -r requirements.txt` to repair the venv in-place.

    Called only in 'preflight' mode to avoid slowing down normal script startup.
    Uses only stdlib (importlib.util + subprocess) — safe to call pre-import.
    """
    import importlib.util

    # Minimal check set — these packages cause the most common cascade failures
    _CHECK_MODULES = ["yaml", "keyring", "bs4", "requests"]
    missing = [m for m in _CHECK_MODULES if importlib.util.find_spec(m) is None]

    if not missing:
        return  # venv is healthy

    print(
        f"[bootstrap] Missing packages detected: {missing}\n"
        f"[bootstrap] Repairing venv — running pip install -r requirements.txt ...",
        file=sys.stderr,
    )
    if _REQUIREMENTS.exists():
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(_REQUIREMENTS)],
            check=False,
        )
        if result.returncode != 0:
            print(
                "[bootstrap] WARNING: pip install failed. Some scripts may not work.\n"
                f"[bootstrap] Fix: pip install -r {_REQUIREMENTS}",
                file=sys.stderr,
            )
    else:
        print(
            f"[bootstrap] WARNING: requirements.txt not found at {_REQUIREMENTS}.\n"
            "[bootstrap] Cannot auto-repair venv. Install packages manually.",
            file=sys.stderr,
        )


def reexec_in_venv(mode: str = "standard") -> None:
    """
    Ensure this script runs inside the Artha venv.

    Args:
        mode: "standard" (default) | "preflight" | "lightweight"

    In "standard" mode:
        - If already in a venv, do nothing (assume correct venv).
        - If venv exists, os.execv into it with the same argv.
        - If venv missing, print an error and sys.exit(1).

    In "preflight" mode:
        - If already in a venv, do nothing.
        - If venv missing, create it and install requirements.txt first.
        - Then os.execv into the venv Python.

    In "lightweight" mode:
        - Never re-exec; just return ARTHA_DIR for caller's use.
        - Useful for scripts that only need ARTHA_DIR, not venv packages.
    """
    if mode == "lightweight":
        os.environ.setdefault("ARTHA_DIR", str(ARTHA_DIR))
        return

    # CI / container environments: skip venv re-exec when deps are already installed
    if os.environ.get("ARTHA_NO_REEXEC"):
        os.environ.setdefault("ARTHA_DIR", str(ARTHA_DIR))
        return

    if _in_venv():
        # Already in a venv — verify critical dependencies are installed.
        # This guards against stale venvs after a `git pull` that added new deps.
        os.environ.setdefault("ARTHA_DIR", str(ARTHA_DIR))
        if mode == "preflight":
            _ensure_deps_installed()
        return

    venv_py = _venv_python()

    if mode == "preflight":
        if not venv_py.exists():
            _create_venv_and_install()
    elif not venv_py.exists():
        print(
            f"ERROR: Artha venv not found at {venv_py}\n"
            f"  Run setup first: python scripts/preflight.py\n"
            f"  Or: python -m venv ~/.artha-venvs/.venv && "
            f"pip install -r scripts/requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    # Re-exec this script inside the venv Python, preserving all argv.
    os.execv(str(venv_py), [str(venv_py)] + sys.argv)


def setup_artha_dir() -> Path:
    """
    Lightweight helper: resolve ARTHA_DIR and set the env var.
    Does NOT trigger venv re-exec. For scripts requiring stdlib only.

    Returns:
        Resolved Path to the Artha project root.
    """
    os.environ.setdefault("ARTHA_DIR", str(ARTHA_DIR))
    return ARTHA_DIR
