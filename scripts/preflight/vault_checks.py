"""preflight/vault_checks.py — Encryption infrastructure checks (vault health, keyring, lock)."""
from __future__ import annotations

import os
import sys
import subprocess
import time
from pathlib import Path

from preflight._types import (
    ARTHA_DIR, SCRIPTS_DIR, LOCK_FILE, _SUBPROCESS_ENV, _rel, CheckResult,
)

STALE_LOCK_SECONDS = 1800   # 30 minutes

def check_keyring_backend() -> CheckResult:
    """P0: Verify keyring has a working backend before any credential operations.

    Fails early with an actionable message on headless Linux where no secret
    service is available, rather than letting vault.py fail silently later.
    """
    try:
        import keyring
        # A None result is fine (key not stored yet) — what we detect is an
        # outright backend failure raised as an exception.
        keyring.get_password("artha-keyring-probe", "preflight")
        return CheckResult("keyring backend", "P0", True, "keyring backend functional ✓")
    except Exception as exc:
        short = str(exc).splitlines()[0][:120]
        return CheckResult(
            "keyring backend", "P0", False,
            f"keyring backend unavailable: {short}",
            fix_hint=(
                "pip install secretstorage  (GNOME/KDE desktop) "
                "or  pip install keyrings.alt  (headless/server). "
                "See docs/troubleshooting.md#no-recommended-backend-was-available-linux"
            ),
        )


def check_vault_health() -> CheckResult:
    """Verify age tool installed, credential store key present, state dir writable.

    Exit code semantics from vault.py health:
      0 — fully healthy (hard + soft checks all pass)
      1 — hard failure (age not installed, key missing, state dir inaccessible) → P0 block
      2 — soft warnings only (.bak files, GFS unvalidated, key never exported)  → P1 advisory
    """
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "vault.py"), "health"],
        capture_output=True, text=True, cwd=ARTHA_DIR,
        env=_SUBPROCESS_ENV, encoding="utf-8", errors="replace",
    )
    if result.returncode == 0:
        return CheckResult("vault.py health", "P0", True, "age ✓ | credential store key ✓ | state dir ✓")
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 2:
        # Soft warnings (e.g. orphaned .bak files) — P1 advisory, not a hard block
        warn_line = next(
            (l for l in output.splitlines() if "⚠" in l and ".bak" in l), None
        ) or next(
            (l for l in output.splitlines() if "⚠" in l), output.split("\n")[0]
        )
        return CheckResult(
            "vault.py health", "P1", False,
            f"vault.py health: {warn_line.strip()}",
            fix_hint="Run: python3 scripts/vault.py encrypt  (clears .bak files and creates GFS backup)",
        )
    # returncode == 1: hard failure
    failed_line = next((l for l in output.splitlines() if "✗" in l or "FAILED" in l), output.split("\n")[0])
    return CheckResult(
        "vault.py health", "P0", False,
        f"vault.py health failed: {failed_line.strip()}",
        fix_hint="Run: python3 scripts/vault.py status — check age install and credential store key",
    )


def check_vault_lock(auto_fix: bool = False) -> CheckResult:
    """Check for active or stale session lock file.

    Stale locks (age > 30m OR locking PID no longer running) are auto-cleared
    unconditionally — they are evidence of a past crash, not an active session.
    Active locks (PID alive, age < 30m) are only cleared with --fix.
    The lock file path is always surfaced in error messages.
    """
    if not os.path.exists(LOCK_FILE):
        return CheckResult("vault lock state", "P0", True, "No lock file — state encrypted ✓")

    lock_mtime  = os.path.getmtime(LOCK_FILE)
    lock_age    = time.time() - lock_mtime
    lock_age_m  = int(lock_age / 60)

    # Determine if PID is still alive (read JSON lock if present)
    try:
        import json as _json
        lock_data = _json.loads(open(LOCK_FILE).read().strip())
        pid = lock_data.get("pid", 0)
    except Exception:
        pid = 0

    def _pid_alive(p: int) -> bool:
        if p <= 0:
            return False
        try:
            os.kill(p, 0)
            return True
        except OSError:
            return False

    is_stale = lock_age > STALE_LOCK_SECONDS or (pid > 0 and not _pid_alive(pid))

    if is_stale:
        # Auto-clear unconditionally — stale = previous crash, not live session
        try:
            os.remove(LOCK_FILE)
            reason = f"age: {lock_age_m}m" + (f", PID {pid} not running" if pid else "") 
            return CheckResult(
                "vault lock state", "P0", True,
                f"Stale lock auto-cleared ({reason}) ✓",
                auto_fixed=True,
            )
        except OSError as e:
            return CheckResult(
                "vault lock state", "P0", False,
                f"Stale lock detected ({lock_age_m}m) but could not auto-clear: {e}",
                fix_hint=f"Manually remove: rm \"{LOCK_FILE}\"",
            )

    # Active lock (PID alive, age < 30m)
    msg = f"Active session lock (age: {lock_age_m}m) — another catch-up may be running"
    if auto_fix:
        try:
            os.remove(LOCK_FILE)
            return CheckResult(
                "vault lock state", "P0", True,
                f"Active lock force-cleared via --fix (was {lock_age_m}m old) ✓",
                auto_fixed=True,
            )
        except OSError as e:
            pass
    return CheckResult(
        "vault lock state", "P0", False, msg,
        fix_hint=f"If no other session is running: rm \"{LOCK_FILE}\"  or re-run with --fix",
    )


def check_vault_watchdog() -> CheckResult:
    """RD-40: P1 advisory — verify vault watchdog daemon is installed.

    The watchdog (com.artha.vault-watchdog) re-encrypts plaintext within
    5 minutes of a SIGKILL/OOM crash.  Without it plaintext may remain
    exposed indefinitely after an unclean exit.
    """
    import platform as _platform  # noqa: PLC0415
    check_name = "vault watchdog daemon"

    if _platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["launchctl", "list", "com.artha.vault-watchdog"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                return CheckResult(check_name, "P1", True, "Vault watchdog loaded ✓")
            return CheckResult(
                check_name, "P1", False,
                "Vault watchdog not loaded — plaintext may be exposed after SIGKILL",
                fix_hint=(
                    "Install: cp scripts/service/com.artha.vault-watchdog.plist "
                    "~/Library/LaunchAgents/ && "
                    "sed -i '' \"s|{{PYTHON_EXE}}|$(which python3)|g\" "
                    "~/Library/LaunchAgents/com.artha.vault-watchdog.plist && "
                    "launchctl load ~/Library/LaunchAgents/com.artha.vault-watchdog.plist"
                ),
            )
        except Exception:  # noqa: BLE001
            return CheckResult(check_name, "P1", False, "Could not check watchdog status (launchctl unavailable)")
    elif _platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", "ArthaVaultWatchdog"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                return CheckResult(check_name, "P1", True, "Vault watchdog scheduled task found ✓")
            return CheckResult(
                check_name, "P1", False,
                "Vault watchdog scheduled task not found",
                fix_hint="See scripts/service/artha-vault-watchdog.xml for installation instructions",
            )
        except Exception:  # noqa: BLE001
            return CheckResult(check_name, "P1", False, "Could not check watchdog status (schtasks unavailable)")
    else:
        return CheckResult(check_name, "P1", True, "Platform not macOS/Windows — watchdog not applicable ✓")


