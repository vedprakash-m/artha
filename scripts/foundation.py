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
        # 12 entries — (domain, extension) tuples.
        # Legacy plain strings are auto-coerced to (domain, ".md") by
        # _normalize_sensitive_files() for backward compatibility.
        # gallery + gallery_memory are NOT vaulted — they contain public
        # social-media drafts, not PII or financial data.
        # DEBT-034: kids added — child activity, school, health data = high PII.
        # DEBT-006: employment added — salary, RSU, comp data = high PII.
        "SENSITIVE_FILES": [
            ("immigration", ".md"),
            ("finance", ".md"),
            ("insurance", ".md"),
            ("estate", ".md"),
            ("health", ".md"),
            ("audit", ".md"),
            ("vehicle", ".md"),
            ("contacts", ".md"),
            ("occasions", ".md"),
            ("transactions", ".md"),
            ("kids", ".md"),
            ("employment", ".md"),
        ],
        "ARTHA_LOCAL_DIR": Path.home() / ".artha-local",
        "KC_SERVICE":      "age-key",
        "KC_ACCOUNT":      "artha",
        "STALE_THRESHOLD": 300,   # 5 min — soft TTL (stale if PID no longer running)
        "LOCK_TTL":        1800,  # 30 min — hard TTL (stale regardless of PID)
    })


_init_config()


def _normalize_sensitive_files(entries: list) -> list[tuple[str, str]]:
    """Coerce SENSITIVE_FILES entries to (domain, extension) tuples.

    Accepts both the new tuple format and legacy plain strings (which are
    coerced to ``(domain, ".md")`` for backward compatibility).
    Allows test fixtures and external code to pass either format safely.
    """
    result = []
    for entry in entries:
        if isinstance(entry, tuple) and len(entry) == 2:
            result.append(entry)
        elif isinstance(entry, str):
            result.append((entry, ".md"))
        else:
            raise ValueError(f"SENSITIVE_FILES entry must be str or (str, str) tuple, got: {entry!r}")
    return result


# ---------------------------------------------------------------------------
# Module-level aliases — frozen at import time.
# ---------------------------------------------------------------------------
# Config accessor — always use this inside function bodies.
# ---------------------------------------------------------------------------

def get_config() -> dict[str, Any]:
    """Return the mutable config dict.

    Always use this inside function bodies instead of module-level aliases.
    The module-level aliases (ARTHA_DIR, STATE_DIR, etc.) are frozen at
    import time. Inside functions, call ``get_config()["STATE_DIR"]`` to
    get the current (possibly test-patched) value.

    Returns the same mutable dict that test fixtures patch via
    ``monkeypatch.setitem(foundation._config, ...)``.
    """
    return _config


# ---------------------------------------------------------------------------
# Provided for backward-compatible external usage ONLY.
# NEVER use these inside function bodies — use get_config()["KEY"] instead.
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
            with open(audit_log, "a", encoding="utf-8") as f:
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
    """Retrieve age private key: keyring first, ARTHA_AGE_KEY env-var fallback.

    Fallback chain:
      1. System keyring (macOS Keychain / Windows Credential Manager)
      2. ARTHA_AGE_KEY environment variable (Cowork VM, CI, non-interactive envs)

    The env-var fallback enables vault operations in ephemeral environments
    (Linux sandbox VMs, GitHub Actions) where no system keychain is available.
    """
    # Env-var check first — cheap, no keyring call needed in CI/VM environments
    env_key = os.environ.get("ARTHA_AGE_KEY", "").strip()
    if env_key and env_key.startswith("AGE-SECRET-KEY-"):
        return env_key

    svc = _config["KC_SERVICE"]
    acct = _config["KC_ACCOUNT"]
    try:
        key = keyring.get_password(svc, acct)
    except Exception as exc:
        die(f"Cannot access credential store: {exc}")
    if not key:
        die(
            "Cannot retrieve age private key from credential store.\n"
            "Options:\n"
            f'  1. Store in keyring: python -c "import keyring; keyring.set_password(\'{svc}\',\'{acct}\',\'<AGE-SECRET-KEY>\')"\n'
            "  2. Set env var:     export ARTHA_AGE_KEY=AGE-SECRET-KEY-..."
        )
    return key  # type: ignore[return-value]


def get_public_key() -> str:
    """Read age recipient public key from user_profile.yaml → encryption.age_recipient."""
    # Preferred: read from user_profile.yaml via profile_loader
    try:
        from profile_loader import get as _profile_get
        key = _profile_get("encryption.age_recipient", "")
        if key and key.startswith("age1"):
            return key
    except Exception:
        pass  # profile_loader may not be available (pre-venv) — fall through

    die("age_recipient not found. Set encryption.age_recipient in config/user_profile.yaml")


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


def age_encrypt_string(pubkey: str, plaintext: str) -> str:
    """Encrypt an arbitrary string using age.

    Writes plaintext to a temp file, encrypts via age, returns the
    base64-encoded ciphertext string.  Both temp files are removed
    in the ``finally`` block regardless of success or failure to
    prevent key-material leakage on disk.

    Raises:
        RuntimeError: if age is not installed or encryption fails.
    """
    import base64
    if not check_age_installed():
        raise RuntimeError("age CLI not found on PATH; cannot encrypt string")

    in_fd, in_path_str = tempfile.mkstemp(prefix="artha_age_in_", suffix=".txt")
    out_path = Path(in_path_str).with_suffix(".age")
    try:
        with os.fdopen(in_fd, "w", encoding="utf-8") as f:
            f.write(plaintext)
        ok = age_encrypt(pubkey, Path(in_path_str), out_path)
        if not ok:
            raise RuntimeError("age_encrypt_string: encryption failed")
        return base64.b64encode(out_path.read_bytes()).decode("ascii")
    finally:
        try:
            os.unlink(in_path_str)
        except OSError:
            pass
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass


def age_decrypt_string(privkey: str, ciphertext_b64: str) -> str:
    """Decrypt a base64-encoded age ciphertext back to a plaintext string.

    Writes the decoded bytes to a temp file, decrypts via age, reads the
    plaintext, and removes both temp files in the ``finally`` block.

    Raises:
        RuntimeError: if age is not installed or decryption fails.
    """
    import base64
    if not check_age_installed():
        raise RuntimeError("age CLI not found on PATH; cannot decrypt string")

    raw = base64.b64decode(ciphertext_b64)
    in_fd, in_path_str = tempfile.mkstemp(prefix="artha_age_in_", suffix=".age")
    out_path = Path(in_path_str).with_suffix(".txt")
    try:
        with os.fdopen(in_fd, "wb") as f:
            f.write(raw)
        ok = age_decrypt(privkey, Path(in_path_str), out_path)
        if not ok:
            raise RuntimeError("age_decrypt_string: decryption failed")
        return out_path.read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(in_path_str)
        except OSError:
            pass
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Age file validation
# ---------------------------------------------------------------------------

# Minimum size for a valid age-encrypted file (header + key stanza + body).
_AGE_MIN_FILE_SIZE = 100
_AGE_HEADER_PREFIX = b"age-encryption.org"


def is_valid_age_file(age_file: Path) -> bool:
    """Return True if *age_file* looks like a valid age-encrypted file.

    Checks:
      1. File is at least _AGE_MIN_FILE_SIZE bytes (header + key stanza + body).
      2. First line starts with the age header prefix.

    Used by vault.py (decrypt pre-validation) and backup.py (snapshot ingress
    gate) to reject corrupt stubs before they propagate.
    """
    try:
        size = age_file.stat().st_size
        if size < _AGE_MIN_FILE_SIZE:
            return False
        with open(age_file, "rb") as f:
            header = f.read(len(_AGE_HEADER_PREFIX))
        return header == _AGE_HEADER_PREFIX
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_sensitive_domains() -> frozenset[str]:
    """Return the set of domain names that require vault protection.

    DEBT-002 / DEBT-034: Single source of truth for all vault protection
    consumers.  Derived dynamically from _config["SENSITIVE_FILES"] so that
    adding a new domain to _init_config() is the ONLY change required.
    """
    entries = _config.get("SENSITIVE_FILES", [])
    return frozenset(domain for domain, _ext in _normalize_sensitive_files(entries))


__all__ = [
    # Config dict (single patch point for test fixtures)
    "_config",
    # Module-level aliases (read-only; frozen at import time)
    "ARTHA_DIR", "STATE_DIR", "CONFIG_DIR", "AUDIT_LOG", "LOCK_FILE",
    "SENSITIVE_FILES", "KC_SERVICE", "KC_ACCOUNT",
    "STALE_THRESHOLD", "LOCK_TTL",
    # Domain helpers
    "get_sensitive_domains",
    # Internal normalizer (used by vault.py and tests)
    "_normalize_sensitive_files",
    # Utilities
    "log", "die",
    # Key management
    "get_private_key", "get_public_key",
    # Encryption (file-based)
    "check_age_installed", "age_decrypt", "age_encrypt",
    # Encryption (string-based wrappers — for action queue sensitive fields)
    "age_encrypt_string", "age_decrypt_string",
    # Validation
    "is_valid_age_file",
]
