"""scripts/lib/auto_vault.py — Transparent encryption setup.

Handles key generation and keyring storage silently during first use.
Called by vault.py before any encrypt/decrypt operation.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent  # Artha root
_CONFIG = _ROOT / "config"
_KEYRING_SERVICE = "artha"
_KEYRING_KEY = "age-key"


def _age_installed() -> bool:
    """Return True if the age binary is on PATH."""
    return shutil.which("age") is not None


def _age_keygen_installed() -> bool:
    """Return True if age-keygen is on PATH."""
    return shutil.which("age-keygen") is not None


def _get_keyring_key() -> str | None:
    """Return the stored age private key from keyring, or None."""
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY)
    except Exception as exc:
        _log.debug("keyring unavailable: %s", exc)
        return None


def _store_keyring_key(private_key: str) -> bool:
    """Store an age private key in keyring. Returns True on success."""
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY, private_key)
        return True
    except Exception as exc:
        _log.warning("Could not store key in keyring: %s", exc)
        return False


def _write_recipient_to_profile(public_key: str) -> None:
    """Write the age public key (recipient) into user_profile.yaml if present."""
    profile = _CONFIG / "user_profile.yaml"
    if not profile.exists():
        return
    try:
        text = profile.read_text(encoding="utf-8")
        if "age_recipient" in text:
            import re
            updated = re.sub(
                r'(age_recipient:\s*")[^"]*(")',
                rf'\g<1>{public_key}\g<2>',
                text,
            )
            if updated != text:
                profile.write_text(updated, encoding="utf-8")
    except Exception as exc:
        _log.warning("Could not write age_recipient to profile: %s", exc)


def ensure_encryption_ready() -> bool:
    """Check if encryption is configured. If not, set it up silently.

    Returns True if encryption is available, False if it cannot be set up
    (in which case state files remain unencrypted with a logged warning).
    """
    # 1. Check if age binary is installed
    if not _age_installed():
        _log.info(
            "age binary not found — encryption unavailable. "
            "Install with: brew install age  (macOS) or scoop install age (Windows)"
        )
        return False

    # 2. Check if private key already in keyring
    existing_key = _get_keyring_key()
    if existing_key:
        return True

    # 3. age is installed but no key — generate one silently
    if not _age_keygen_installed():
        _log.warning("age-keygen not found — cannot generate keypair")
        return False

    try:
        result = subprocess.run(
            ["age-keygen"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _log.warning("age-keygen failed: %s", exc)
        return False

    if result.returncode != 0:
        _log.warning("age-keygen exited %d: %s", result.returncode, result.stderr.strip())
        return False

    # Parse output — age-keygen prints:
    #   # created: <timestamp>
    #   # public key: age1...
    #   AGE-SECRET-KEY-1...
    lines = result.stdout.strip().splitlines()
    private_key: str | None = None
    public_key:  str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("AGE-SECRET-KEY-"):
            private_key = stripped
        if stripped.startswith("# public key: "):
            public_key = stripped.removeprefix("# public key: ").strip()

    if not private_key:
        _log.warning("age-keygen output did not contain a private key")
        return False

    # 4. Store private key in keyring
    if not _store_keyring_key(private_key):
        _log.warning(
            "keyring unavailable — encryption key generated but not stored. "
            "Run 'python scripts/vault.py --init' to configure manually."
        )
        return False

    # 5. Write public key (recipient) to profile
    if public_key:
        _write_recipient_to_profile(public_key)

    _log.info("Encryption key generated and stored in keyring ✓")
    return True
