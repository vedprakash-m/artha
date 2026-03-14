#!/usr/bin/env python3
"""
foundation.py — Artha shared foundation
=========================================
Shared constants, logging, and cryptographic primitives used by vault.py,
backup.py, and any future Artha script.

Architecture:
  - _config dict: mutable source-of-truth for all path constants.
    All functions read paths from _config at call time, not import time.
    Test fixtures patch _config ONCE and all modules see the same values.
  - Module-level aliases: frozen at import time for backward-compatible
    external usage (e.g. `from scripts.foundation import ARTHA_DIR`).
    These aliases MUST NOT be used inside function bodies.

Exports:
  _config, ARTHA_DIR, STATE_DIR, CONFIG_DIR, AUDIT_LOG, LOCK_FILE,
  SENSITIVE_FILES, KC_SERVICE, KC_ACCOUNT, STALE_THRESHOLD, LOCK_TTL,
  log, die, get_private_key, get_public_key,
  check_age_installed, age_decrypt, age_encrypt

Ref: TS §8.5, specs/bkp-rst.md §3.3
"""

from __future__ import annotations

# Auto-relaunch inside the Artha venv if not already running there
import sys, os as _os
_scripts_dir = _os.path.dirname(_os.path.abspath(__file__))
_artha_dir   = _os.path.dirname(_scripts_dir)  # project root (parent of scripts/)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
if _artha_dir not in sys.path:
    sys.path.insert(0, _artha_dir)
from _bootstrap import reexec_in_venv
reexec_in_venv()

import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml  # PyYAML — in requirements.txt
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]

# Ensure UTF-8 stdout/stderr on Windows (avoids cp1252 encoding errors with ✓/✗)
if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import keyring

# ---------------------------------------------------------------------------
# Mutable config dict — THE source of truth.
# Test fixtures patch individual keys via monkeypatch.setitem(foundation._config, ...).
# All functions read from _config at call time so patches propagate correctly.
# ---------------------------------------------------------------------------

_config: dict[str, Any] = {}


def _init_config() -> None:
    """Populate _config with default values derived from this file's location."""
    artha_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _config.update({
        "ARTHA_DIR":    artha_dir,
        "STATE_DIR":    artha_dir / "state",
        "CONFIG_DIR":   artha_dir / "config",
        "AUDIT_LOG":    artha_dir / "state" / "audit.md",
        "LOCK_FILE":    artha_dir / ".artha-decrypted",
        # 9 entries — skills_cache removed (never existed on disk; see bkp-rst.md §2.3)
        "SENSITIVE_FILES": [
            "immigration",
            "finance",
            "insurance",
            "estate",
            "health",
            "audit",
            "vehicle",
            "contacts",
            "occasions",
        ],
        "KC_SERVICE":      "age-key",
        "KC_ACCOUNT":      "artha",
        "STALE_THRESHOLD": 300,   # 5 min — soft TTL (stale if PID no longer running)
        "LOCK_TTL":        1800,  # 30 min — hard TTL (stale regardless of PID)
    })


_init_config()

# ---------------------------------------------------------------------------
# Module-level aliases — frozen at import time.
# Provided for backward-compatible external usage ONLY.
# NEVER use these inside function bodies — use _config["KEY"] instead.
# ---------------------------------------------------------------------------
ARTHA_DIR        = _config["ARTHA_DIR"]
STATE_DIR        = _config["STATE_DIR"]
CONFIG_DIR       = _config["CONFIG_DIR"]
AUDIT_LOG        = _config["AUDIT_LOG"]
LOCK_FILE        = _config["LOCK_FILE"]
SENSITIVE_FILES  = _config["SENSITIVE_FILES"]
KC_SERVICE       = _config["KC_SERVICE"]
KC_ACCOUNT       = _config["KC_ACCOUNT"]
STALE_THRESHOLD  = _config["STALE_THRESHOLD"]
LOCK_TTL         = _config["LOCK_TTL"]


# ---------------------------------------------------------------------------
# Logging and exit utilities
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    """Log a vault event to audit.md (if it exists as plaintext) and stdout."""
    entry = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] VAULT | {msg}"
    audit_log = _config["AUDIT_LOG"]
    if audit_log.exists():
        try:
            with open(audit_log, "a") as f:
                f.write(entry + "\n")
        except OSError:
            pass
    print(entry)


def die(msg: str) -> None:
    """Print error message to stderr and exit with code 1."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def get_private_key() -> str:
    """Retrieve age private key from the system credential store."""
    svc = _config["KC_SERVICE"]
    acct = _config["KC_ACCOUNT"]
    try:
        key = keyring.get_password(svc, acct)
    except Exception as exc:
        die(f"Cannot access credential store: {exc}")
    if not key:
        die(
            "Cannot retrieve age private key from credential store.\n"
            "Store it with:\n"
            f'  python -c "import keyring; keyring.set_password(\'{svc}\',\'{acct}\',\'<AGE-SECRET-KEY>\')"'
        )
    return key  # type: ignore[return-value]


def get_public_key() -> str:
    """Read age recipient public key from user_profile.yaml (preferred) or settings.md (legacy)."""
    # Preferred: read from user_profile.yaml via profile_loader
    try:
        from scripts.profile_loader import get as _profile_get
        key = _profile_get("encryption.age_recipient", "")
        if key and key.startswith("age1"):
            return key
    except Exception:
        pass  # profile_loader may not be available (pre-venv) — fall through

    # Legacy fallback: parse settings.md
    settings_file = _config["CONFIG_DIR"] / "settings.md"
    if not settings_file.exists():
        die("age_recipient not found in user_profile.yaml or config/settings.md.")
    text = settings_file.read_text()
    match = re.search(r"age_recipient:\s*\"?(age1[a-z0-9]+)", text)
    if not match:
        die("Cannot read age_recipient from user_profile.yaml or config/settings.md. "
            "Populate encryption.age_recipient in your profile.")
    pubkey = match.group(1)
    if not pubkey.startswith("age1"):
        die(f"Invalid age public key (must start with 'age1'). Got: {pubkey}")
    return pubkey


# ---------------------------------------------------------------------------
# Encryption primitives
# ---------------------------------------------------------------------------

def check_age_installed() -> bool:
    """Return True if the `age` CLI is available on PATH."""
    return shutil.which("age") is not None


def age_decrypt(privkey: str, input_path: Path, output_path: Path) -> bool:
    """Decrypt *input_path* with *privkey* and write plaintext to *output_path*.

    Writes the private key to a temp file to avoid shell process substitution
    (which is bash-only). Returns True on success.
    """
    tmpfd, tmppath = tempfile.mkstemp(prefix="artha_age_", suffix=".key")
    try:
        with os.fdopen(tmpfd, "w") as f:
            f.write(privkey)
        result = subprocess.run(
            ["age", "--decrypt", "--identity", tmppath,
             "--output", str(output_path), str(input_path)],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    finally:
        try:
            os.unlink(tmppath)
        except OSError:
            pass


def age_encrypt(pubkey: str, input_path: Path, output_path: Path) -> bool:
    """Encrypt *input_path* for *pubkey* and write ciphertext to *output_path*.

    Returns True on success.
    """
    result = subprocess.run(
        ["age", "--recipient", pubkey,
         "--output", str(output_path), str(input_path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Config dict (single patch point for test fixtures)
    "_config",
    # Module-level aliases (read-only; frozen at import time)
    "ARTHA_DIR", "STATE_DIR", "CONFIG_DIR", "AUDIT_LOG", "LOCK_FILE",
    "SENSITIVE_FILES", "KC_SERVICE", "KC_ACCOUNT",
    "STALE_THRESHOLD", "LOCK_TTL",
    # Utilities
    "log", "die",
    # Key management
    "get_private_key", "get_public_key",
    # Encryption
    "check_age_installed", "age_decrypt", "age_encrypt",
]
