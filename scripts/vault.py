#!/usr/bin/env python3
"""
vault.py — Artha sensitive state encrypt/decrypt helper (cross-platform)
========================================================================
Python replacement for vault.sh — works on macOS, Windows, and Linux.
Uses `keyring` for credential storage and `age` CLI for encryption.

Usage:
  python scripts/vault.py decrypt   — decrypt all .age files to plaintext .md
  python scripts/vault.py encrypt   — encrypt all .md back to .age and remove plaintext
  python scripts/vault.py status    — show current encryption state
  python scripts/vault.py health    — exit 0 if vault is healthy; exit 1 otherwise

Stale lock handling:
  Lock file > 30 min old: previous session crashed uncleanly.
  Auto-cleared and logged. Lock < 30 min: treated as active session.

Security model:
  - Private key lives in system credential store (macOS Keychain / Windows Credential Manager)
    (service: age-key, account: artha)
  - Public key is read from user_profile.yaml → encryption.age_recipient
  - Lock file .artha-decrypted signals an active session

Ref: TS §8.5, T-1A.1.3
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

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import keyring

# ---------------------------------------------------------------------------
# DEBT-001: Re-entrancy guard for cleanup handlers
# Prevents double-encrypt if both atexit and signal handler fire (e.g. SIGTERM
# received during normal shutdown).  Module-level singleton — set to True
# before encrypting, checked at handler entry.
# ---------------------------------------------------------------------------
_ENCRYPTING: bool = False

# Advisory lock support (POSIX: fcntl, Windows: msvcrt)
try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None  # type: ignore[assignment]
try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Foundation — shared constants, logging, and cryptographic primitives
# ---------------------------------------------------------------------------

from foundation import (
    _config,
    ARTHA_DIR, STATE_DIR, CONFIG_DIR, AUDIT_LOG, LOCK_FILE,
    SENSITIVE_FILES, KC_SERVICE, KC_ACCOUNT, STALE_THRESHOLD, LOCK_TTL,
    log, die,
    get_private_key, get_public_key,
    check_age_installed, age_decrypt, age_encrypt,
    _normalize_sensitive_files,
)

# Transparent first-run encryption setup (Part VII — auto_vault).
# Graceful fallback if lib not importable during bootstrapping.
try:
    from lib.auto_vault import ensure_encryption_ready as _ensure_encryption_ready
except ImportError:
    def _ensure_encryption_ready() -> bool:  # type: ignore[misc]
        return True


def _iter_sensitive_files():
    """Yield (domain, extension, plain_path, age_path) for each sensitive file.

    Handles both the new tuple format and legacy plain-string format from
    SENSITIVE_FILES.  This is the single canonical iteration helper — all
    vault loops MUST use this instead of building paths directly.
    """
    state_dir = _config["STATE_DIR"]
    for domain, ext in _normalize_sensitive_files(_config["SENSITIVE_FILES"]):
        plain_file = state_dir / f"{domain}{ext}"
        age_file   = state_dir / f"{domain}{ext}.age"
        yield domain, ext, plain_file, age_file


# ---------------------------------------------------------------------------
# Lock file management
# ---------------------------------------------------------------------------

def _read_lock_data() -> dict:
    """Read JSON lock data from LOCK_FILE.  Returns {} on read/parse errors
    (handles legacy empty lock files created by vault.sh and older vault.py)."""
    try:
        content = LOCK_FILE.read_text(encoding="utf-8").strip()
        if content:
            return json.loads(content)
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _pid_running(pid: int) -> bool:
    """Return True if a process with *pid* is currently running on this machine.
    Uses POSIX os.kill(pid, 0) on Unix; falls back to subprocess on Windows."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: use tasklist to check if PID exists
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True,
            )
            return str(pid) in result.stdout
        else:
            # POSIX: signal 0 tests existence without sending a real signal
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, PermissionError):
        # ProcessLookupError: process does not exist
        # PermissionError: process exists but not owned by us — still running
        return not isinstance(sys.exc_info()[1], (ProcessLookupError,))


def check_lock_state() -> int:
    """Check for active or stale session lock file.

    Returns:
        0 — no lock (proceed normally)
        1 — stale lock (auto-cleared; proceed with warning)
        2 — active lock (halt — another session is running)

    Staleness criteria (OR):
        a) Lock is older than LOCK_TTL (30 min) regardless of PID status.
        b) Lock is older than STALE_THRESHOLD (5 min) AND the locking PID
           is no longer running.
    """
    if not LOCK_FILE.exists():
        return 0

    lock_data  = _read_lock_data()
    lock_mtime = os.path.getmtime(LOCK_FILE)
    now        = time.time()
    lock_age   = int(now - lock_mtime)
    lock_age_m = lock_age // 60
    pid        = lock_data.get("pid", 0)

    # Hard TTL: 30 min — stale regardless of PID
    hard_stale = lock_age > LOCK_TTL
    # Soft TTL: 5 min — stale IF the locking PID is no longer alive
    soft_stale = lock_age > STALE_THRESHOLD and pid > 0 and not _pid_running(pid)
    # Legacy empty lock (no JSON): treat as stale after STALE_THRESHOLD
    legacy_stale = lock_age > STALE_THRESHOLD and pid == 0

    if hard_stale or soft_stale or legacy_stale:
        reason = "hard TTL" if hard_stale else ("PID not running" if soft_stale else "legacy lock")
        print(f"  ⚠ Stale lock file detected (age: {lock_age_m}m, reason: {reason}).")
        print("  Previous session exited uncleanly. Auto-clearing lock and proceeding.")
        if pid:
            print(f"  (locking PID was {pid})")
        # RD-40: Attempt re-encryption BEFORE clearing the lock — ensures plaintext
        # is not left exposed when a session terminates uncleanly (SIGKILL, OOM, etc.).
        # do_encrypt() is safe to call multiple times (re-entrancy guard in place).
        # Catch BaseException (including SystemExit) so failures never block lock cleanup.
        _watchdog_encrypt_attempted = False
        try:
            print("  Attempting re-encryption of any plaintext domain files before clearing lock...")
            do_encrypt()
            _watchdog_encrypt_attempted = True
            print("  Re-encryption complete.")
        except BaseException as _enc_exc:  # noqa: BLE001 — best-effort; must not block lock cleanup
            print(f"  ⚠ Re-encryption failed: {_enc_exc} — clearing lock anyway.")
        LOCK_FILE.unlink(missing_ok=True)
        log(
            f"STALE_LOCK_CLEARED | age: {lock_age_m}m | reason: {reason} | pid: {pid} "
            f"| reencrypt: {'ok' if _watchdog_encrypt_attempted else 'failed'} | action: auto-cleared"
        )
        return 1

    # Active lock
    pid_str = f", locking PID: {pid}" if pid else ""
    print(f"⛔ vault.py: Active session lock detected (age: {lock_age_m}m{pid_str}).")
    print("  Another catch-up session may be in progress.")
    print("  Halt: running a duplicate session could corrupt state.")
    if pid and _pid_running(pid):
        print(f"  Process {pid} is still running.")
    elif pid:
        print(f"  Process {pid} is no longer running — lock may be stale.")
        print(f"  To force-clear: python scripts/vault.py release-lock")
    else:
        print(f"  To force-clear: python scripts/vault.py release-lock")
    log(f"DECRYPT_BLOCKED | reason: active_lock | age: {lock_age_m}m | pid: {pid}")
    return 2


def do_release_lock(force: bool = True) -> None:
    """Force-clear a stale session lock file (manual recovery command).

    Used when a previous catch-up session crashed while holding the lock,
    leaving a lock file whose owning PID is no longer running.

    Writes an audit log entry and exits 0 on success, 1 if no lock exists.
    """
    if not LOCK_FILE.exists():
        print("vault.py release-lock: No lock file present — nothing to clear.")
        sys.exit(0)

    lock_data  = _read_lock_data()
    lock_mtime = os.path.getmtime(LOCK_FILE)
    lock_age_m = int((time.time() - lock_mtime) / 60)
    pid        = lock_data.get("pid", 0)
    ts         = lock_data.get("timestamp", "unknown")

    if pid and _pid_running(pid):
        print(f"WARNING: Process {pid} (started {ts}) is still running.")
        print("  Releasing the lock while a live session is active could corrupt state.")
        print("  Only proceed if you are certain that session is defunct.")
        if not force:
            print("  Re-run with release-lock to confirm force-release.")
            sys.exit(1)

    print(f"Releasing vault lock (age: {lock_age_m}m, pid: {pid}, ts: {ts}) ...")
    LOCK_FILE.unlink(missing_ok=True)
    log(f"LOCK_RELEASED | manual | age: {lock_age_m}m | pid: {pid} | ts: {ts}")
    print("vault.py release-lock: Lock cleared. State files remain in current form.")
    print("  If catch-up data was being written, verify state files before proceeding.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Store private key in OS credential store
# ---------------------------------------------------------------------------

def do_store_key(keyfile: str) -> None:
    """Read an age private key from a file and store it in the OS credential store.

    Simplifies Step 4 of the quickstart:
      python scripts/vault.py store-key ~/age-key.txt
    """
    path = Path(os.path.expanduser(keyfile))
    if not path.exists():
        print(f"Error: key file not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"Error reading key file: {exc}", file=sys.stderr)
        sys.exit(1)
    if not key:
        print("Error: key file is empty.", file=sys.stderr)
        sys.exit(1)
    try:
        keyring.set_password(KC_SERVICE, KC_ACCOUNT, key)
    except Exception as exc:
        print(f"Error storing key in credential store: {exc}", file=sys.stderr)
        sys.exit(1)
    log(f"STORE_KEY | keyfile: {path.name}")
    print(f"Key stored in credential store ({KC_SERVICE}/{KC_ACCOUNT}) ✓")
    print("You may now delete the key file:  rm " + str(path))


# ---------------------------------------------------------------------------
# Session-level backup helper — restore from GFS when live .age is corrupt
# ---------------------------------------------------------------------------

def _restore_bak(plain_file: Path, domain: str, reason: str) -> bool:
    """Restore .bak to plain_file if valid.  Returns True if restore succeeded.

    Guards against restoring a corrupt or partial backup by checking:
      - the .bak file exists
      - the .bak is non-empty
      - the .bak begins with YAML frontmatter ('---')
    If any check fails the failure is audit-logged and the function returns False
    so the caller can still count the error correctly.
    """
    bak = Path(str(plain_file) + ".bak")
    if not bak.exists():
        log(f"INTEGRITY_RESTORE_FAILED | file: {domain}.md | reason: {reason} | detail: no_backup")
        print(f"  WARNING: No backup available for {domain}.md — original .age file is intact",
              file=sys.stderr)
        return False
    if bak.stat().st_size == 0:
        log(f"INTEGRITY_RESTORE_FAILED | file: {domain}.md | reason: {reason} | detail: backup_empty")
        print(f"  WARNING: Backup for {domain}.md is empty — skipping restore", file=sys.stderr)
        return False
    try:
        with open(bak, encoding="utf-8", errors="replace") as f:
            first_line = f.readline()
        if not first_line.startswith("---"):
            log(f"INTEGRITY_RESTORE_FAILED | file: {domain}.md | reason: {reason} | detail: backup_invalid_yaml")
            print(f"  WARNING: Backup for {domain}.md has invalid YAML — skipping restore", file=sys.stderr)
            return False
    except OSError as exc:
        log(f"INTEGRITY_RESTORE_FAILED | file: {domain}.md | reason: {reason} | detail: unreadable ({exc})")
        print(f"  WARNING: Cannot read backup for {domain}.md: {exc}", file=sys.stderr)
        return False
    shutil.move(str(bak), str(plain_file))
    log(f"INTEGRITY_RESTORE | file: {domain}.md | reason: {reason} | layer: 1")
    return True


def is_integrity_safe(plain_file: Path, age_file: Path) -> bool:
    """
    Net-Negative Write Guard (TS §8.5.1):
    Prevent data loss by checking if the new plaintext is significantly
    smaller than the existing encrypted version.
    Returns True if safe, False if potentially corrupted/truncated.

    RD-27: Per-domain thresholds read from config/guardrails.yaml
    (net_negative_write_guard.domain_thresholds). Domains like finance
    and health may have lower thresholds (e.g., 0.6) to reduce false positives
    for legitimate summarization.

    Override: set ARTHA_FORCE_SHRINK=1 (all domains) or
    ARTHA_FORCE_SHRINK=<domain> to accept a legitimate shrink.
    The old .age is pinned to .age.pre-shrink for recovery (#5).
    """
    if not age_file.exists():
        return True # New file, no baseline to compare

    new_size = plain_file.stat().st_size
    # age files have header/metadata, so they are slightly larger than plaintext.
    # We estimate based on file size. If new plaintext is < threshold of current .age,
    # it might be a truncated write unless confirmed by user.
    old_size = age_file.stat().st_size
    domain = plain_file.stem  # e.g. "finance"

    # RD-27: Read per-domain threshold from guardrails.yaml; fall back to 0.8
    threshold = 0.8
    try:
        import yaml as _yaml  # noqa: PLC0415
        _guardrails_path = CONFIG_DIR / "guardrails.yaml"
        if _guardrails_path.exists():
            _gr = _yaml.safe_load(_guardrails_path.read_text(encoding="utf-8")) or {}
            _wr = _gr.get("net_negative_write_guard", {})
            threshold = float(
                _wr.get("domain_thresholds", {}).get(domain)
                or _wr.get("default_threshold", 0.8)
            )
    except Exception:
        threshold = 0.8

    if new_size < (old_size * threshold):
        print(f"  ⚠ INTEGRITY ALERT: {plain_file.name} is significantly smaller than previous version.")
        print(f"    New size: {new_size} bytes | Old size: {old_size} bytes | Threshold: {threshold:.0%}")

        # Check for explicit override via environment variable
        force = os.environ.get("ARTHA_FORCE_SHRINK", "").strip()
        if force and (force == "1" or force == domain):
            pre_shrink = Path(str(age_file) + ".pre-shrink")
            if not pre_shrink.exists():
                shutil.copy2(str(age_file), str(pre_shrink))
            log(f"INTEGRITY_OVERRIDE | file: {domain}.md | new_size: {new_size} | old_size: {old_size} | pinned: {pre_shrink.name}")
            print(f"    Override accepted (ARTHA_FORCE_SHRINK). Old .age pinned to {pre_shrink.name}")
            return True

        print(f"    To override: ARTHA_FORCE_SHRINK=1 python scripts/vault.py encrypt")
        return False
    
    return True


# Re-export for backward compat and local use
from foundation import is_valid_age_file as _is_valid_age_file


# ---------------------------------------------------------------------------
# Advisory file lock — prevents concurrent vault operations (#10)
# ---------------------------------------------------------------------------

_op_lock_fd = None


def _acquire_op_lock() -> bool:
    """Acquire OS-level advisory lock. Returns True if acquired.

    Uses fcntl.flock() on POSIX or msvcrt.locking() on Windows.
    The lock is automatically released when the file descriptor is closed
    (including on process exit or crash).
    """
    global _op_lock_fd
    lock_path = _config["ARTHA_DIR"] / ".artha-op-lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = open(lock_path, "w")
        if _msvcrt is not None:
            _msvcrt.locking(fd.fileno(), _msvcrt.LK_NBLCK, 1)
        elif _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        else:
            # No advisory lock support — proceed without lock
            fd.close()
            return True
        _op_lock_fd = fd
        return True
    except (IOError, OSError):
        try:
            fd.close()
        except Exception:
            pass
        return False


def _release_op_lock() -> None:
    """Release the advisory file lock."""
    global _op_lock_fd
    if _op_lock_fd is not None:
        try:
            _op_lock_fd.close()
        except Exception:
            pass
        _op_lock_fd = None


def _with_op_lock(func):
    """Decorator: acquire advisory lock before func, release after (even on exception)."""
    def wrapper(*args, **kwargs):
        if not _acquire_op_lock():
            print("\u26d4 Another vault operation is in progress. Aborting.", file=sys.stderr)
            log("OP_BLOCKED | reason: advisory_lock_held")
            sys.exit(1)
        try:
            return func(*args, **kwargs)
        finally:
            _release_op_lock()
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# Sync-fence check — detect cloud sync in flight (#2)
# ---------------------------------------------------------------------------

_CLOUD_SYNC_MARKERS = ("CloudStorage", "OneDrive", "Dropbox", "Google Drive", "iCloud")


def _is_cloud_synced() -> bool:
    """Return True if the Artha workspace is inside a cloud-synced folder."""
    return any(m in str(_config["ARTHA_DIR"]) for m in _CLOUD_SYNC_MARKERS)


def _check_sync_fence() -> bool:
    """Wait until cloud-synced .age files are quiescent before decrypt.

    RD-06: Replaces the 2-second fixed sleep with a quiescence detection loop
    that polls mtime stability. Returns True if quiescent (safe to decrypt),
    False if the timeout elapses before stability is reached (warn but proceed).

    No-op (returns True immediately) for non-cloud-synced workspaces.
    """
    if not _is_cloud_synced():
        return True

    def _snapshot() -> dict[str, float]:
        return {
            str(age_f): age_f.stat().st_mtime
            for _, _, _, age_f in _iter_sensitive_files()
            if age_f.exists()
        }

    current = _snapshot()
    if not current:
        return True  # no .age files — nothing to fence

    timeout_sec = int(os.environ.get("ARTHA_SYNC_FENCE_TIMEOUT", "45"))
    poll_interval = 2    # seconds between samples
    stable_target = 3    # consecutive identical snapshots = quiescent
    stable_count = 0
    deadline = time.monotonic() + timeout_sec

    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        new = _snapshot()
        if new == current:
            stable_count += 1
            if stable_count >= stable_target:
                return True  # quiescent — safe to proceed
        else:
            stable_count = 0
            current = new

    # Timed out — log and proceed (best-effort; don't block decrypt)
    try:
        import datetime as _dt
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as _af:
            _af.write(
                f"\nSYNC_FENCE_TIMEOUT: {ts} | "
                f"timeout_sec={timeout_sec} | "
                f"files_monitored={len(current)}\n"
            )
    except OSError:
        pass
    print(
        f"[VAULT-WARN] OneDrive sync appears incomplete after {timeout_sec}s. "
        "Proceeding — decrypt may fail if sync is in-flight. "
        "Re-run `python scripts/vault.py decrypt` if errors occur.",
        file=sys.stderr,
    )
    return False


# ---------------------------------------------------------------------------
# RD-35 Phase 1: OneDrive selective-sync exclusion preflight
# ---------------------------------------------------------------------------

def _verify_sync_exclusion(path) -> bool:
    """Return True if *path* has OneDrive selective-sync exclusion set (macOS).

    Uses xattr to check for ``com.microsoft.OneDrive-Selective-Sync``.
    Returns False on any error or if the attribute is absent.
    Always returns True on non-macOS platforms (nothing to check).
    """
    import platform
    if platform.system() != "Darwin":
        return True  # Not macOS — no xattr check possible
    try:
        import subprocess
        result = subprocess.run(
            ["xattr", "-p", "com.microsoft.OneDrive-Selective-Sync", str(path)],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False  # conservative: assume not excluded


def _warn_if_sync_not_excluded(state_dir) -> None:
    """Emit a warning if state/ is not protected from OneDrive sync.

    Non-blocking — we warn but do not abort the decrypt. This follows Phase 1
    of the RD-35 fix: surface the configuration gap without breaking existing
    workflows. Phase 2 (moving plaintext out of state/) requires an ADR.
    """
    if not _verify_sync_exclusion(state_dir):
        print(
            "[VAULT-WARN] state/ does not have OneDrive selective-sync exclusion set. "
            "Decrypted sensitive files may sync to the cloud during the decrypt window. "
            "Run setup.sh to configure selective sync exclusion on state/.",
            file=sys.stderr,
        )
        log("VAULT_SYNC_EXCLUSION_MISSING | path: state/ | risk: cloud_sync_window")


# ---------------------------------------------------------------------------
# Encrypt failure lockdown — restrict permissions on exposed plaintext (#9)
# ---------------------------------------------------------------------------

def _lockdown_plaintext() -> None:
    """Remove read permissions on remaining plaintext files after encrypt failure.

    Prevents Spotlight indexing, cloud sync, and casual access while the user
    fixes the encrypt issue. Permissions are restored on next successful decrypt.
    """
    locked = 0
    for _domain, _ext, plain, _age in _iter_sensitive_files():
        if plain.exists():
            try:
                if os.name != "nt":
                    os.chmod(plain, 0o000)
                else:
                    # Windows: set hidden + system attributes
                    subprocess.run(
                        ["attrib", "+H", "+S", str(plain)],
                        capture_output=True, timeout=5,
                    )
                locked += 1
            except OSError:
                pass
    if locked:
        log(f"LOCKDOWN | files_locked: {locked}")
        print(f"  \u26a0 {locked} plaintext file(s) locked down (permissions removed).")
        print(f"    Fix the encrypt issue, then re-run: python scripts/vault.py encrypt")


def _unlock_plaintext() -> None:
    """Restore read/write permissions on plaintext files (called at decrypt start)."""
    for _domain, _ext, plain, _age in _iter_sensitive_files():
        if plain.exists():
            try:
                if os.name != "nt":
                    os.chmod(plain, 0o600)
                else:
                    subprocess.run(
                        ["attrib", "-H", "-S", str(plain)],
                        capture_output=True, timeout=5,
                    )
            except OSError:
                pass


def _quarantine_file(file_path: Path, domain: str, reason: str) -> None:
    """Move a corrupt file to state/.quarantine/ for manual inspection."""
    quarantine_dir = file_path.parent / ".quarantine"
    quarantine_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = quarantine_dir / f"{file_path.name}.{ts}"
    shutil.move(str(file_path), str(dest))
    log(f"QUARANTINE | file: {domain} | reason: {reason} | dest: {dest.name}")
    print(f"  ⚠ Quarantined corrupt file: {file_path.name} → .quarantine/{dest.name}")


# ---------------------------------------------------------------------------
# Decrypt
# ---------------------------------------------------------------------------

@_with_op_lock
def do_decrypt() -> None:
    """Decrypt all sensitive .age files to plaintext .md.

    Corrupt .age files (invalid header / tiny stubs) are quarantined and
    skipped — they do not block decryption of healthy files.
    """
    _ensure_encryption_ready()  # transparent first-run setup (Part VII)
    if not check_age_installed():
        die("'age' encryption tool not found. Install it:\n"
            "  macOS: brew install age\n"
            "  Windows: winget install FiloSottile.age  (or scoop install age)")

    lock_state = check_lock_state()
    if lock_state == 2:
        sys.exit(1)

    # Sync-fence: detect cloud sync in flight before touching .age files (#2)
    if not _check_sync_fence():
        print("  ⚠ Cloud sync in progress — .age file(s) changed during fence check.", file=sys.stderr)
        print("    Wait for sync to complete and retry.", file=sys.stderr)
        log("SYNC_FENCE_FAILED | reason: mtime_changed_during_fence")
        sys.exit(1)

    # RD-35 Phase 1: warn if state/ is not excluded from OneDrive sync
    _warn_if_sync_not_excluded(_config["STATE_DIR"])

    # Restore permissions if a prior encrypt failure locked down plaintext (#9)
    _unlock_plaintext()

    privkey = get_private_key()
    errors = 0
    quarantined = 0

    for domain, ext, plain_file, age_file in _iter_sensitive_files():
        filename = f"{domain}{ext}"
        age_filename = f"{domain}{ext}.age"

        if age_file.exists():
            # Pre-validation: detect corrupt stubs before wasting time on age CLI
            if not _is_valid_age_file(age_file):
                size = age_file.stat().st_size
                print(f"  ⚠ {age_filename} is corrupt (size={size}B, missing age header) — quarantining", file=sys.stderr)
                _quarantine_file(age_file, domain, f"invalid_age_file (size={size})")
                quarantined += 1
                continue

            # Layer 1: Pre-decrypt backup (atomic: copy to .bak.tmp then rename)
            if plain_file.exists():
                bak_tmp = Path(str(plain_file) + ".bak.tmp")
                bak = Path(str(plain_file) + ".bak")
                shutil.copy2(str(plain_file), str(bak_tmp))
                os.replace(str(bak_tmp), str(bak))
                log(f"INTEGRITY_BACKUP | file: {filename} | layer: 1_pre_decrypt")

            print(f"  Decrypting {age_filename} ...")
            # Atomic decrypt: write to temp file, validate, then rename
            tmp_plain = Path(str(plain_file) + ".tmp")
            if age_decrypt(privkey, age_file, tmp_plain):
                # Post-decrypt validation: empty?
                if not tmp_plain.exists() or tmp_plain.stat().st_size == 0:
                    print(f"  ERROR: Decrypted {filename} is empty — restoring backup", file=sys.stderr)
                    tmp_plain.unlink(missing_ok=True)
                    _restore_bak(plain_file, domain, "empty_decrypt")
                    errors += 1
                    continue

                # Post-decrypt validation: YAML frontmatter?
                # .md files start with '---'; .yaml files start with '#' or a YAML key.
                # For .yaml files we just check non-empty (already checked above).
                if ext == ".md":
                    with open(tmp_plain, encoding="utf-8", errors="replace") as f:
                        first_line = f.readline()
                    if not first_line.startswith("---"):
                        print(f"  ERROR: Decrypted {filename} missing YAML frontmatter — restoring backup",
                              file=sys.stderr)
                        tmp_plain.unlink(missing_ok=True)
                        _restore_bak(plain_file, domain, "invalid_yaml")
                        errors += 1
                        continue

                # Atomic rename: tmp -> final (POSIX-atomic on same filesystem)
                os.replace(str(tmp_plain), str(plain_file))

                # Bootstrap detection (only relevant for .md state files)
                if ext == ".md":
                    text = plain_file.read_text(encoding="utf-8", errors="replace")
                    if "updated_by: bootstrap" in text:
                        log(f"BOOTSTRAP_DETECTED | file: {filename} | note: placeholder data — run /bootstrap {domain}")

                log(f"DECRYPT_OK | file: {filename}")
            else:
                tmp_plain.unlink(missing_ok=True)
                print(f"  ERROR: Failed to decrypt {age_filename}", file=sys.stderr)
                _restore_bak(plain_file, domain, "decrypt_failed")
                errors += 1
        elif plain_file.exists():
            print(f"  {filename} already exists as plaintext (no .age file). Leaving as-is.")

    if quarantined > 0:
        print(f"\n  ⚠ {quarantined} corrupt .age file(s) quarantined to state/.quarantine/")
        print(f"    Reconstruct the source data and re-encrypt to resolve.")

    if errors > 0:
        die(f"{errors} file(s) failed to decrypt. Aborting catch-up.")

    # Create lock file with PID + timestamp + operation metadata
    # DEBT-025: Use atomic tempfile+rename to prevent partial JSON on crash
    import tempfile as _tempfile  # noqa: PLC0415
    import socket as _socket  # noqa: PLC0415
    lock_data = {
        "pid":       os.getpid(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operation": "decrypt",
        "machine":   _socket.gethostname(),  # RD-14: machine identity for conflict detection
    }
    lock_content = json.dumps(lock_data) + "\n"
    lock_dir = LOCK_FILE.parent
    try:
        with _tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(lock_dir),
            prefix=".artha-decrypted-tmp-",
            delete=False,
        ) as _tmp:
            _tmp.write(lock_content)
            _tmp_path = _tmp.name
        os.replace(_tmp_path, str(LOCK_FILE))  # atomic rename on POSIX + Windows
    except Exception:  # noqa: BLE001
        # Fallback to non-atomic write on unexpected filesystem error
        LOCK_FILE.write_text(lock_content, encoding="utf-8")
    print("vault.py: Decrypt complete. Lock file created.")
    log(f"SESSION_START | lock_file: created | pid: {lock_data['pid']}")
    _register_cleanup_handlers()


# ---------------------------------------------------------------------------
# DEBT-001: Cleanup handlers — re-encrypt on abnormal exit
# ---------------------------------------------------------------------------

def _atexit_encrypt() -> None:
    """Re-encrypt vault on normal Python exit if a lock file is present.

    Registered via atexit.register() after a successful decrypt so that
    normal process exit (sys.exit, end-of-script) triggers re-encryption.
    SIGKILL cannot be caught \u2014 the external watchdog handles that case.
    """
    global _ENCRYPTING  # noqa: PLW0603
    if _ENCRYPTING:
        return  # re-entrancy guard: another handler already encrypting
    lock_file = _config["LOCK_FILE"]
    if not lock_file.exists():
        return  # nothing to do \u2014 already encrypted or no active session
    _ENCRYPTING = True
    try:
        log("ATEXIT_ENCRYPT | trigger: normal_exit")
        do_encrypt()
    except Exception as exc:  # noqa: BLE001
        print(f"[VAULT] atexit encrypt failed: {exc}", file=sys.stderr)
    finally:
        _ENCRYPTING = False


def _signal_encrypt_handler(signum: int, frame: object) -> None:  # noqa: ARG001
    """Re-encrypt vault on SIGTERM/SIGINT then exit with the conventional code.

    Registered via signal.signal() after a successful decrypt.
    """
    global _ENCRYPTING  # noqa: PLW0603
    if _ENCRYPTING:
        sys.exit(128 + signum)
    lock_file = _config["LOCK_FILE"]
    if lock_file.exists():
        _ENCRYPTING = True
        try:
            log(f"SIGNAL_ENCRYPT | trigger: signal={signum}")
            do_encrypt()
        except Exception as exc:  # noqa: BLE001
            print(f"[VAULT] signal encrypt failed: {exc}", file=sys.stderr)
        finally:
            _ENCRYPTING = False
    sys.exit(128 + signum)


def _register_cleanup_handlers() -> None:
    """Register atexit + signal handlers after a successful decrypt.

    Called at the END of do_decrypt() (after the lock file is written) so the
    handlers are registered only when an active session exists.
    signal.signal() is only valid on the main thread \u2014 do_decrypt() is always
    called from a CLI main thread, so this is safe.
    """
    atexit.register(_atexit_encrypt)
    try:
        signal.signal(signal.SIGTERM, _signal_encrypt_handler)
        signal.signal(signal.SIGINT, _signal_encrypt_handler)
    except (OSError, ValueError):
        # Non-main thread (shouldn't happen for CLI) or unsupported platform
        pass


# ---------------------------------------------------------------------------
# Failure helper — records a backup miss in health-check.md
# ---------------------------------------------------------------------------

def _mark_backup_failure() -> None:
    """Append a BACKUP_FAILED sentinel line to health-check.md (non-fatal)."""
    hc_path = _config["STATE_DIR"] / "health-check.md"
    try:
        lines = hc_path.read_text(encoding="utf-8").splitlines(keepends=True) if hc_path.exists() else []
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines.append(f"\nBACKUP_FAILED: {ts}\n")
        hc_path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        pass  # non-fatal


# ---------------------------------------------------------------------------
# Encrypt
# ---------------------------------------------------------------------------

@_with_op_lock
def do_encrypt() -> None:
    """Encrypt all sensitive plaintext .md files back to .age and remove plaintext.

    Protections:
      - Post-encrypt size verification detects truncation from disk-full (#8)
      - Plaintext deletion is deferred until ALL encrypts succeed (#1)
      - On failure, remaining plaintext is locked down (permissions removed) (#9)
      - RD-14: Machine conflict detection — warns if another machine holds the lock
    """
    _ensure_encryption_ready()  # transparent first-run setup (Part VII)
    if not check_age_installed():
        die("'age' encryption tool not found. Install it:\n"
            "  macOS: brew install age\n"
            "  Windows: winget install FiloSottile.age  (or scoop install age)")

    # RD-14: Machine conflict detection — check if lock was created by this machine
    try:
        import socket as _socket  # noqa: PLC0415
        lock_data = _read_lock_data()
        lock_machine = lock_data.get("machine", "")
        current_machine = _socket.gethostname()
        if lock_machine and lock_machine != current_machine:
            log(
                f"CONCURRENT_VAULT_WARNING | lock_machine:{lock_machine} | "
                f"current_machine:{current_machine} | pid:{os.getpid()} | "
                f"risk:concurrent_write_last_writer_wins"
            )
            print(
                f"[VAULT-WARN] Concurrent write detected: lock was created by machine "
                f"'{lock_machine}', but this is '{current_machine}'. "
                f"If both machines modified state/, the last to encrypt wins — "
                f"manual reconciliation may be required. See audit.md for details.",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001
        pass  # Machine check is best-effort; never block encrypt

    try:
        pubkey = get_public_key()
    except SystemExit:
        # Public key missing — cannot encrypt. Do NOT remove lock file.
        plaintext_count = sum(1 for _, _, plain, _ in _iter_sensitive_files() if plain.exists())
        print(f"\n  CRITICAL: Cannot encrypt — age_recipient public key not configured.", file=sys.stderr)
        print(f"  {plaintext_count} sensitive file(s) remain in PLAINTEXT.", file=sys.stderr)
        print(f"  Fix: Set encryption.age_recipient in config/user_profile.yaml", file=sys.stderr)
        print(f"  Then re-run: python scripts/vault.py encrypt", file=sys.stderr)
        log(f"ENCRYPT_BLOCKED | reason: no_public_key | plaintext_files: {plaintext_count}")
        _lockdown_plaintext()
        sys.exit(1)

    errors = 0
    encrypted_count = 0
    encrypted_files: list[tuple[str, str, Path, Path]] = []  # deferred cleanup list (#1)

    for domain, ext, plain_file, age_file in _iter_sensitive_files():
        filename = f"{domain}{ext}"

        if plain_file.exists():
            # Layer 3: Net-Negative Write Guard (P0)
            if not is_integrity_safe(plain_file, age_file):
                print(f"  ERROR: Integrity check failed for {filename}. Skipping encryption to prevent data loss.", file=sys.stderr)
                errors += 1
                continue

            print(f"  Encrypting {filename} ...")
            tmp_file = Path(str(age_file) + ".tmp")
            if age_encrypt(pubkey, plain_file, tmp_file):
                # Post-encrypt verification: detect truncation from disk-full (#8)
                try:
                    tmp_size = tmp_file.stat().st_size
                    plain_size = plain_file.stat().st_size
                except OSError:
                    tmp_size = 0
                    plain_size = 1
                if tmp_size < plain_size:
                    print(f"  ERROR: {filename}.age appears truncated "
                          f"({tmp_size}B < {plain_size}B plaintext) — possible disk full",
                          file=sys.stderr)
                    log(f"ENCRYPT_TRUNCATED | file: {filename} | age_size: {tmp_size} | plain_size: {plain_size}")
                    tmp_file.unlink(missing_ok=True)
                    errors += 1
                    continue

                shutil.move(str(tmp_file), str(age_file))
                encrypted_files.append((domain, ext, plain_file, age_file))
                encrypted_count += 1
                log(f"ENCRYPT_OK | file: {filename}")
            else:
                tmp_file.unlink(missing_ok=True)
                print(f"  ERROR: Failed to encrypt {filename}", file=sys.stderr)
                errors += 1

    if errors > 0:
        _lockdown_plaintext()
        die(f"{errors} file(s) failed to encrypt. CRITICAL: plaintext may remain on disk.")

    # Deferred cleanup: remove plaintext + .bak only after ALL encrypts verified (#1)
    for domain, ext, plain_file, age_file in encrypted_files:
        plain_file.unlink(missing_ok=True)
        bak = Path(str(plain_file) + ".bak")
        bak.unlink(missing_ok=True)

    # Clean up orphaned plaintext stubs leftover from interrupted encrypt cycles.
    for domain, ext, plain_file, age_file in _iter_sensitive_files():
        if plain_file.exists() and age_file.exists() and _is_valid_age_file(age_file):
            plain_file.unlink()
            filename = f"{domain}{ext}"
            log(f"ORPHAN_CLEANUP | file: {filename} | reason: plaintext_alongside_valid_age")
            print(f"  Cleaned orphaned plaintext: {filename}")

    # Remove lock file only after all files are safely encrypted
    LOCK_FILE.unlink(missing_ok=True)
    print(f"vault.py: Encrypt complete. {encrypted_count} files secured. Lock file removed.")
    log(f"SESSION_END | lock_file: removed | files_encrypted: {encrypted_count}")

    # GFS backup snapshot (§8.5.2) — delegate to backup.py
    from backup import backup_snapshot, load_backup_registry
    registry = load_backup_registry()
    count = backup_snapshot(registry)
    if count == 0:
        print("  ⚠ GFS backup FAILED — no files archived.")
        print("    Encryption was successful, but no backup was created.")
        print("    Fix: python scripts/backup.py status")
        log("BACKUP_FAILED | post_encrypt | file_count: 0")
        _mark_backup_failure()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def do_status() -> None:
    """Show current encryption state without changing anything."""
    print("━" * 43)
    print(f"VAULT STATUS — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("━" * 43)

    if LOCK_FILE.exists():
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(LOCK_FILE))
            print(f"SESSION: ACTIVE (lock file present, {mtime.strftime('%Y-%m-%d %H:%M:%S')})")
        except OSError:
            print("SESSION: ACTIVE (lock file present)")
    else:
        print("SESSION: INACTIVE (no lock file — state is encrypted)")

    print()
    print("State files:")
    for domain, ext, plain_file, age_file in _iter_sensitive_files():
        filename = f"{domain}{ext}"
        age_filename = f"{domain}{ext}.age"
        if plain_file.exists():
            print(f"  [PLAINTEXT] {filename}  ⚠ NOT encrypted")
        elif age_file.exists():
            print(f"  [ENCRYPTED] {age_filename} ✓")
        else:
            print(f"  [MISSING]   {domain} — no {ext} or {ext}.age found")

    print()
    if check_age_installed():
        result = subprocess.run(["age", "--version"], capture_output=True, text=True)
        version = result.stdout.strip() or result.stderr.strip() or "unknown"
        print(f"age: ✓ {version}")
    else:
        print("age: ✗ NOT INSTALLED")

    try:
        key = keyring.get_password(KC_SERVICE, KC_ACCOUNT)
        if key:
            print("Credential store key: ✓ present")
        else:
            print("Credential store key: ✗ NOT found")
    except Exception:
        print("Credential store key: ✗ Cannot access credential store")

    print("━" * 43)


# ---------------------------------------------------------------------------
# Auto-lock (called by watchdog/cron — inactivity-based TTL enforce)
# ---------------------------------------------------------------------------

def do_auto_lock() -> int:
    """Encrypt vault if lock file is older than LOCK_TTL. Used by watchdog.

    Called by the LaunchAgent/watchdog when the AI session process check is
    inconclusive. Provides a software-level safety net independent of the
    OS watchdog's process detection.

    Returns:
        0 — locked (either already locked or successfully encrypted)
        1 — lock file not present (nothing to do)
        2 — encryption failed
    """
    if not LOCK_FILE.exists():
        print("vault.py auto-lock: No lock file — vault already locked. Nothing to do.")
        return 1

    lock_mtime = os.path.getmtime(LOCK_FILE)
    lock_age = int(time.time() - lock_mtime)
    ttl = LOCK_TTL  # 30 min by default

    if lock_age <= ttl:
        remaining = ttl - lock_age
        print(f"vault.py auto-lock: Lock age {lock_age}s ≤ TTL {ttl}s. {remaining}s remaining. No action.")
        return 0

    print(f"vault.py auto-lock: Lock age {lock_age}s > TTL {ttl}s. Auto-encrypting...")
    log(f"AUTO_LOCK_TRIGGER | lock_age: {lock_age}s | ttl: {ttl}s")

    # Mtime guard: don't auto-lock if state files are being actively written (#4)
    _ACTIVE_WRITE_THRESHOLD = 60  # seconds
    now = time.time()
    for domain, ext, plain_file, _age_file in _iter_sensitive_files():
        filename = f"{domain}{ext}"
        if plain_file.exists():
            try:
                md_age = now - os.path.getmtime(plain_file)
            except OSError:
                continue
            if md_age < _ACTIVE_WRITE_THRESHOLD:
                print(f"vault.py auto-lock: {filename} modified {int(md_age)}s ago — deferring.")
                log(f"AUTO_LOCK_DEFERRED | reason: active_write | file: {filename} | mod_age: {int(md_age)}s")
                # Refresh lock file mtime to extend the TTL
                LOCK_FILE.touch()
                return 0

    # Reuse do_encrypt() logic
    try:
        do_encrypt()
        return 0
    except SystemExit as e:
        return int(e.code or 2)


# ---------------------------------------------------------------------------
# Health check (used by preflight.py)
# ---------------------------------------------------------------------------

def do_health() -> None:
    """Exit 0 if vault is healthy (all hard checks pass, no warnings).
    Exit 1 if a hard failure: age not installed, credential key missing, or
    state dir inaccessible — these block catch-up.
    Exit 2 if soft warnings only (e.g. orphaned .bak files, GFS never
    validated, key never exported) — preflight treats these as P1 advisories,
    NOT hard P0 blocks.
    """
    ok = True

    # 1. age tool installed
    if check_age_installed():
        result = subprocess.run(["age", "--version"], capture_output=True, text=True)
        version = (result.stdout.strip() or result.stderr.strip() or "unknown")
        print(f"  age: ✓ {version}")
    else:
        print("  age: ✗ NOT installed")
        if os.name == "nt":
            print("        Install: winget install FiloSottile.age  (or scoop install age)")
        else:
            print("        Install: brew install age")
        ok = False

    # 2. Private key retrievable and well-formed (#3)
    try:
        key = keyring.get_password(KC_SERVICE, KC_ACCOUNT)
        if key:
            if key.strip().startswith("AGE-SECRET-KEY-"):
                print("  Credential store key: ✓ present (valid format)")
            else:
                print("  Credential store key: ⚠ present but INVALID FORMAT (expected AGE-SECRET-KEY-...)")
                ok = False
        else:
            print("  Credential store key: ✗ NOT found")
            ok = False
    except Exception as exc:
        print(f"  Credential store key: ✗ Error: {exc}")
        ok = False

    # 3. Public key readable
    try:
        pubkey = get_public_key()
        print(f"  Public key:   ✓ {pubkey[:20]}...")
    except SystemExit:
        print("  Public key:   ✗ NOT found — set encryption.age_recipient in config/user_profile.yaml")
        ok = False

    # 4. State directory accessible
    if STATE_DIR.is_dir() and os.access(STATE_DIR, os.W_OK):
        print(f"  State dir:    ✓ {STATE_DIR}")
    else:
        print(f"  State dir:    ✗ NOT accessible: {STATE_DIR}")
        ok = False

    # 5. Lock file status
    if LOCK_FILE.exists():
        lock_mtime = os.path.getmtime(LOCK_FILE)
        lock_age_min = int((time.time() - lock_mtime) / 60)
        print(f"  Lock file:    ⚠ present (age: {lock_age_min}m)")
    else:
        print("  Lock file:    ✓ absent (state encrypted)")

    # 6. Orphaned .bak files — soft warning only (cleanup concern, not corruption)
    bak_count = sum(1 for _, _, p, _ in _iter_sensitive_files() if Path(str(p) + ".bak").exists())
    if bak_count > 0:
        print(f"  Backup files: ⚠ {bak_count} orphaned .bak file(s) — run: python3 scripts/vault.py encrypt")
        soft_warn = True
    else:
        soft_warn = False
        print("  Backup files: ✓ none (clean)")

    # 7. GFS backup catalog
    from backup import get_health_summary
    backup_count, last_validate, validate_errors = get_health_summary()
    if backup_count == 0:
        print("  GFS backups:  ⚠ none — run vault.py encrypt to create first backup")
        soft_warn = True
    else:
        if last_validate:
            try:
                last_dt    = datetime.fromisoformat(last_validate.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - last_dt).days
                err_note   = f" ({validate_errors} file(s) failed)" if validate_errors else ""
                if days_since > 35:
                    print(f"  GFS backups:  ⚠ {backup_count} snapshot(s) but validation overdue ({days_since}d){err_note}")
                    soft_warn = True
                else:
                    print(f"  GFS backups:  ✓ {backup_count} snapshot(s), validated {days_since}d ago{err_note}")
            except ValueError:
                print(f"  GFS backups:  ✓ {backup_count} snapshot(s) (validation: {last_validate})")
        else:
            print(f"  GFS backups:  ⚠ {backup_count} snapshot(s) but never validated — run vault.py validate-backup")
            soft_warn = True

    # 8. Key backup status — warn if private key has never been exported (#3)
    from backup import _load_manifest as _bkp_load_manifest
    bkp_manifest = _bkp_load_manifest()
    last_export = bkp_manifest.get("last_key_export")
    if last_export:
        print(f"  Key backup:   ✓ exported {last_export[:10]}")
    else:
        print("  Key backup:   ⚠ NEVER exported — run: backup.py export-key and store securely")
        soft_warn = True

    if ok and not soft_warn:
        print("vault.py health: OK")
        sys.exit(0)
    elif ok and soft_warn:
        # Hard capabilities intact — only cleanup/advisory items need attention
        print("vault.py health: OK (warnings present)")
        sys.exit(2)
    else:
        print("vault.py health: FAILED")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_usage(exit_code: int = 1) -> None:
    print("Usage: vault.py {decrypt|encrypt|status|health|store-key|release-lock|auto-lock}")
    print("       backup commands → python scripts/backup.py {snapshot|status|validate|restore|install|…}")
    print()
    print("  decrypt      — unlock sensitive state files for a catch-up session")
    print("  encrypt      — lock sensitive state files after catch-up + GFS backup")
    print("  status       — show current encryption state (read-only)")
    print("  health       — exit 0 if vault is healthy; exit 1 otherwise (for preflight)")
    print("  store-key    — store age private key from a file into the OS credential store")
    print("  release-lock — force-clear a stale session lock (manual recovery)")
    print("  auto-lock    — encrypt if lock TTL exceeded (called by watchdog/cron)")
    sys.exit(exit_code)


def main() -> None:
    if len(sys.argv) < 2:
        _print_usage(exit_code=1)

    cmd = sys.argv[1].lower()
    if cmd in ("--help", "-h", "help"):
        _print_usage(exit_code=0)
    if cmd == "decrypt":
        do_decrypt()
    elif cmd == "encrypt":
        do_encrypt()
    elif cmd == "status":
        do_status()
    elif cmd == "health":
        do_health()
    elif cmd in ("store-key", "store_key"):
        if len(sys.argv) < 3:
            print("Usage: vault.py store-key <keyfile>", file=sys.stderr)
            sys.exit(1)
        do_store_key(sys.argv[2])
    elif cmd in ("backup-status", "backup_status"):
        from backup import do_backup_status as _bkp_status
        _bkp_status()
    elif cmd in ("validate-backup", "validate_backup"):
        args     = sys.argv[2:]
        domain   = None
        date_str = None
        i = 0
        while i < len(args):
            if args[i] == "--domain" and i + 1 < len(args):
                domain = args[i + 1]; i += 2
            elif args[i] == "--date" and i + 1 < len(args):
                date_str = args[i + 1]; i += 2
            else:
                i += 1
        from backup import do_validate_backup as _bkp_validate
        _bkp_validate(domain=domain, date_str=date_str)
    elif cmd == "restore":
        args      = sys.argv[2:]
        domain    = None
        date_str  = None
        dry_run   = False
        data_only = False
        confirm   = False
        i = 0
        while i < len(args):
            if args[i] == "--domain" and i + 1 < len(args):
                domain = args[i + 1]; i += 2
            elif args[i] == "--date" and i + 1 < len(args):
                date_str = args[i + 1]; i += 2
            elif args[i] == "--dry-run":
                dry_run = True; i += 1
            elif args[i] == "--data-only":
                data_only = True; i += 1
            elif args[i] == "--confirm":
                confirm = True; i += 1
            else:
                i += 1
        from backup import do_restore as _bkp_restore
        _bkp_restore(date_str=date_str, domain=domain, dry_run=dry_run,
                     data_only=data_only, confirm=confirm)
    elif cmd == "install":
        args      = sys.argv[2:]
        dry_run   = False
        data_only = False
        confirm   = False
        zip_arg   = None
        i = 0
        while i < len(args):
            if args[i] == "--dry-run":
                dry_run = True; i += 1
            elif args[i] == "--data-only":
                data_only = True; i += 1
            elif args[i] == "--confirm":
                confirm = True; i += 1
            else:
                zip_arg = args[i]; i += 1
        if not zip_arg:
            print("Usage: vault.py install <zipfile> [--data-only] [--dry-run] [--confirm]")
            sys.exit(1)
        from backup import do_install as _bkp_install
        _bkp_install(zip_arg, dry_run=dry_run, data_only=data_only, confirm=confirm)
    elif cmd in ("release-lock", "release_lock", "--release-lock"):
        do_release_lock()
    elif cmd in ("auto-lock", "auto_lock"):
        sys.exit(do_auto_lock())
    elif cmd in ("watchdog",):
        # RD-40: Vault watchdog — run by launchd every 5 minutes.
        # Checks for stale locks and re-encrypts plaintext if the locking PID
        # is no longer running. This bounds post-crash plaintext exposure to ≤5 min.
        result = _check_stale_lock()
        if result == 1:
            # Stale lock was found and cleared; _check_stale_lock already called do_encrypt()
            log("VAULT_WATCHDOG_ENCRYPT | trigger: stale_lock_cleared")
            sys.exit(0)
        elif result == 0:
            # No lock — vault is clean (no active or stale session)
            sys.exit(0)
        else:
            # result == 2: active lock (session in progress) — nothing to do
            sys.exit(0)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        _print_usage(exit_code=1)


if __name__ == "__main__":
    main()
