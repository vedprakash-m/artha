#!/usr/bin/env python3
"""
scripts/vault_watchdog.py
=========================
Cross-platform vault watchdog — recovers from stale lock files after abnormal
process exit (SIGKILL, OOM, crash).

Meant to be invoked by:
  - macOS: com.artha.vault-watchdog.plist (LaunchAgent, every 2 min)
  - Windows: Task Scheduler job (vault_watchdog_win.ps1 wraps this)
  - Linux: artha-vault-watchdog.timer / .service (systemd user units)

Environment variables:
  ARTHA_WATCHDOG_INTERVAL_SECS  — override stale-lock threshold for testing
                                   (default: vault_ttl_minutes * 60 from user_profile.yaml,
                                    or 1800 seconds / 30 minutes)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_ARTHA_DIR  = _SCRIPT_DIR.parent  # project root

# ---------------------------------------------------------------------------
# Resolve lock file path
# ---------------------------------------------------------------------------
def _find_lock_file() -> Path:
    """Return the vault lock file path (.artha-decrypted in ARTHA_DIR)."""
    # Import foundation config at runtime so this script works standalone.
    try:
        sys.path.insert(0, str(_SCRIPT_DIR))
        from foundation import LOCK_FILE  # type: ignore[import]
        return LOCK_FILE
    except Exception:
        # Fallback: lock file is always ARTHA_DIR / ".artha-decrypted"
        return _ARTHA_DIR / ".artha-decrypted"

# ---------------------------------------------------------------------------
# Resolve stale-lock TTL
# ---------------------------------------------------------------------------
def _stale_lock_seconds() -> int:
    """Return the stale-lock threshold in seconds.

    Priority order:
    1. ARTHA_WATCHDOG_INTERVAL_SECS env var (for CI / fast tests)
    2. user_profile.yaml vault_ttl_minutes field
    3. Default: 1800s (30 minutes)
    """
    env_val = os.environ.get("ARTHA_WATCHDOG_INTERVAL_SECS")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass

    try:
        import yaml  # type: ignore[import]
        profile_path = _ARTHA_DIR / "config" / "user_profile.yaml"
        with open(profile_path, encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
        ttl = profile.get("vault_ttl_minutes", 30)
        return int(ttl) * 60
    except Exception:
        pass

    return 1800  # 30 minutes default

# ---------------------------------------------------------------------------
# Main watchdog logic
# ---------------------------------------------------------------------------
def _run_watchdog() -> None:
    lock_file = _find_lock_file()
    threshold = _stale_lock_seconds()

    if not lock_file.exists():
        # No lock file → vault is encrypted, nothing to do.
        return

    lock_age = time.time() - lock_file.stat().st_mtime
    if lock_age < threshold:
        # Lock file is fresh — active session in progress.
        return

    # Lock file is stale — abnormal exit detected.  Force encrypt.
    print(
        f"[ArthaWatchdog] Stale lock file detected "
        f"(age {lock_age:.0f}s > threshold {threshold}s). "
        f"Triggering vault encrypt...",
        flush=True,
    )

    vault_py  = _SCRIPT_DIR / "vault.py"
    python_exe = sys.executable  # same interpreter as this watchdog

    try:
        result = subprocess.run(
            [python_exe, str(vault_py), "encrypt"],
            cwd=str(_ARTHA_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print("[ArthaWatchdog] Vault encrypted successfully.", flush=True)
        else:
            print(
                f"[ArthaWatchdog] Vault encrypt failed (rc={result.returncode}): "
                f"{result.stderr[:400]}",
                flush=True,
            )
    except subprocess.TimeoutExpired:
        print("[ArthaWatchdog] Vault encrypt timed out (120s).", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[ArthaWatchdog] Unexpected error: {exc}", flush=True)


if __name__ == "__main__":
    _run_watchdog()
