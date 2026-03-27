"""preflight/_types.py — Shared dataclass, helpers, constants for the preflight package."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ARTHA_DIR   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(ARTHA_DIR, "scripts")
STATE_DIR   = os.path.join(ARTHA_DIR, "state")
TOKEN_DIR   = os.path.join(ARTHA_DIR, ".tokens")
LOCK_FILE          = os.path.join(ARTHA_DIR, ".artha-decrypted")
WORKIQ_CACHE_FILE  = os.path.join(ARTHA_DIR, "tmp", ".workiq_cache.json")

TOKEN_EXPIRY_WARN_SECONDS = 300  # Warn when token expires within 5 minutes

_SUBPROCESS_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

def _rel(path: str) -> str:
    """Return path relative to ARTHA_DIR (uses $ARTHA_DIR prefix for display).

    Avoids leaking username / absolute directory structure in console output.
    Falls back to the basename if the path isn't under ARTHA_DIR.
    """
    try:
        rel = os.path.relpath(path, ARTHA_DIR)
        # relpath on a different drive (Windows) may return an absolute path
        if rel.startswith("..") or os.path.isabs(rel):
            return os.path.basename(path)
        return "$ARTHA_DIR/" + rel.replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


@dataclass
class CheckResult:
    name: str
    severity: str  # "P0" or "P1"
    passed: bool
    message: str
    fix_hint: str = ""
    auto_fixed: bool = False


_REQUIRED_DEPS: dict[str, str] = {
    "yaml":       "pyyaml",
    "keyring":    "keyring",
    "bs4":        "beautifulsoup4",
    "requests":   "requests",
    "google":     "google-auth",
}

