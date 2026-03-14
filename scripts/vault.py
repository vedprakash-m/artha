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
  - Public key is read from config/settings.md (safe to sync)
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

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import keyring

# ---------------------------------------------------------------------------
# Foundation — shared constants, logging, and cryptographic primitives
# ---------------------------------------------------------------------------

from scripts.foundation import (
    _config,
    ARTHA_DIR, STATE_DIR, CONFIG_DIR, AUDIT_LOG, LOCK_FILE,
    SENSITIVE_FILES, KC_SERVICE, KC_ACCOUNT, STALE_THRESHOLD, LOCK_TTL,
    log, die,
    get_private_key, get_public_key,
    check_age_installed, age_decrypt, age_encrypt,
)


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
        LOCK_FILE.unlink(missing_ok=True)
        log(f"STALE_LOCK_CLEARED | age: {lock_age_m}m | reason: {reason} | pid: {pid} | action: auto-cleared")
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
    """
    if not age_file.exists():
        return True # New file, no baseline to compare

    new_size = plain_file.stat().st_size
    # age files have header/metadata, so they are slightly larger than plaintext.
    # We estimate based on file size. If new plaintext is < 80% of current .age,
    # it might be a truncated write unless confirmed by user.
    old_size = age_file.stat().st_size
    
    # 20% loss threshold
    if new_size < (old_size * 0.8):
        print(f"  ⚠ INTEGRITY ALERT: {plain_file.name} is significantly smaller than previous version.")
        print(f"    New size: {new_size} bytes | Old size: {old_size} bytes")
        return False
    
    return True


# ---------------------------------------------------------------------------
# Decrypt
# ---------------------------------------------------------------------------

def do_decrypt() -> None:
    """Decrypt all sensitive .age files to plaintext .md."""
    if not check_age_installed():
        die("'age' encryption tool not found. Install it:\n"
            "  macOS: brew install age\n"
            "  Windows: winget install FiloSottile.age  (or scoop install age)")

    lock_state = check_lock_state()
    if lock_state == 2:
        sys.exit(1)

    privkey = get_private_key()
    errors = 0

    for domain in SENSITIVE_FILES:
        age_file = STATE_DIR / f"{domain}.md.age"
        plain_file = STATE_DIR / f"{domain}.md"

        if age_file.exists():
            # Layer 1: Pre-decrypt backup (atomic: copy to .bak.tmp then rename)
            if plain_file.exists():
                bak_tmp = Path(str(plain_file) + ".bak.tmp")
                bak = Path(str(plain_file) + ".bak")
                shutil.copy2(str(plain_file), str(bak_tmp))
                os.replace(str(bak_tmp), str(bak))
                log(f"INTEGRITY_BACKUP | file: {domain}.md | layer: 1_pre_decrypt")

            print(f"  Decrypting {domain}.md.age ...")
            # Atomic decrypt: write to temp file, validate, then rename
            tmp_plain = Path(str(plain_file) + ".tmp")
            if age_decrypt(privkey, age_file, tmp_plain):
                # Post-decrypt validation: empty?
                if not tmp_plain.exists() or tmp_plain.stat().st_size == 0:
                    print(f"  ERROR: Decrypted {domain}.md is empty — restoring backup", file=sys.stderr)
                    tmp_plain.unlink(missing_ok=True)
                    _restore_bak(plain_file, domain, "empty_decrypt")
                    errors += 1
                    continue

                # Post-decrypt validation: YAML frontmatter?
                with open(tmp_plain, encoding="utf-8", errors="replace") as f:
                    first_line = f.readline()
                if not first_line.startswith("---"):
                    print(f"  ERROR: Decrypted {domain}.md missing YAML frontmatter — restoring backup",
                          file=sys.stderr)
                    tmp_plain.unlink(missing_ok=True)
                    _restore_bak(plain_file, domain, "invalid_yaml")
                    errors += 1
                    continue

                # Atomic rename: tmp -> final (POSIX-atomic on same filesystem)
                os.replace(str(tmp_plain), str(plain_file))

                # Bootstrap detection
                text = plain_file.read_text(encoding="utf-8", errors="replace")
                if "updated_by: bootstrap" in text:
                    log(f"BOOTSTRAP_DETECTED | file: {domain}.md | note: placeholder data — run /bootstrap {domain}")

                log(f"DECRYPT_OK | file: {domain}.md")
            else:
                tmp_plain.unlink(missing_ok=True)
                print(f"  ERROR: Failed to decrypt {domain}.md.age", file=sys.stderr)
                _restore_bak(plain_file, domain, "decrypt_failed")
                errors += 1
        elif plain_file.exists():
            print(f"  {domain}.md already exists as plaintext (no .age file). Leaving as-is.")

    if errors > 0:
        die(f"{errors} file(s) failed to decrypt. Aborting catch-up.")

    # Create lock file with PID + timestamp + operation metadata
    lock_data = {
        "pid":       os.getpid(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operation": "decrypt",
    }
    LOCK_FILE.write_text(json.dumps(lock_data) + "\n", encoding="utf-8")
    print("vault.py: Decrypt complete. Lock file created.")
    log(f"SESSION_START | lock_file: created | pid: {lock_data['pid']}")


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

def do_encrypt() -> None:
    """Encrypt all sensitive plaintext .md files back to .age and remove plaintext."""
    if not check_age_installed():
        die("'age' encryption tool not found. Install it:\n"
            "  macOS: brew install age\n"
            "  Windows: winget install FiloSottile.age  (or scoop install age)")

    pubkey = get_public_key()
    errors = 0
    encrypted_count = 0

    for domain in SENSITIVE_FILES:
        plain_file = STATE_DIR / f"{domain}.md"
        age_file = STATE_DIR / f"{domain}.md.age"

        if plain_file.exists():
            # Layer 3: Net-Negative Write Guard (P0)
            if not is_integrity_safe(plain_file, age_file):
                print(f"  ERROR: Integrity check failed for {domain}.md. Skipping encryption to prevent data loss.", file=sys.stderr)
                errors += 1
                continue

            print(f"  Encrypting {domain}.md ...")
            tmp_file = Path(str(age_file) + ".tmp")
            if age_encrypt(pubkey, plain_file, tmp_file):
                shutil.move(str(tmp_file), str(age_file))
                plain_file.unlink()
                # Clean up backup
                bak = Path(str(plain_file) + ".bak")
                bak.unlink(missing_ok=True)
                encrypted_count += 1
                log(f"ENCRYPT_OK | file: {domain}.md")
            else:
                tmp_file.unlink(missing_ok=True)
                print(f"  ERROR: Failed to encrypt {domain}.md", file=sys.stderr)
                errors += 1

    if errors > 0:
        die(f"{errors} file(s) failed to encrypt. CRITICAL: plaintext may remain on disk.")

    # Remove lock file only after all files are safely encrypted
    LOCK_FILE.unlink(missing_ok=True)
    print(f"vault.py: Encrypt complete. {encrypted_count} files secured. Lock file removed.")
    log(f"SESSION_END | lock_file: removed | files_encrypted: {encrypted_count}")

    # GFS backup snapshot (§8.5.2) — delegate to backup.py
    from scripts.backup import backup_snapshot, load_backup_registry
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
    for domain in SENSITIVE_FILES:
        plain_file = STATE_DIR / f"{domain}.md"
        age_file = STATE_DIR / f"{domain}.md.age"
        if plain_file.exists():
            print(f"  [PLAINTEXT] {domain}.md  ⚠ NOT encrypted")
        elif age_file.exists():
            print(f"  [ENCRYPTED] {domain}.md.age ✓")
        else:
            print(f"  [MISSING]   {domain} — no .md or .age found")

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
    """Exit 0 if vault is healthy; exit 1 otherwise."""
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

    # 2. Private key retrievable
    try:
        key = keyring.get_password(KC_SERVICE, KC_ACCOUNT)
        if key:
            print("  Credential store key: ✓ present")
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
        print("  Public key:   ✗ NOT found in config/settings.md")
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

    # 6. Orphaned .bak files
    bak_count = sum(1 for d in SENSITIVE_FILES if (STATE_DIR / f"{d}.md.bak").exists())
    if bak_count > 0:
        print(f"  Backup files: ⚠ {bak_count} orphaned .bak file(s) — stale plaintext")
        ok = False
    else:
        print("  Backup files: ✓ none (clean)")

    # 7. GFS backup catalog
    from scripts.backup import get_health_summary
    backup_count, last_validate = get_health_summary()
    if backup_count == 0:
        print("  GFS backups:  ⚠ none — run vault.py encrypt to create first backup")
    else:
        if last_validate:
            try:
                last_dt    = datetime.fromisoformat(last_validate.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - last_dt).days
                if days_since > 35:
                    print(f"  GFS backups:  ⚠ {backup_count} snapshot(s) but validation overdue ({days_since}d)")
                    ok = False
                else:
                    print(f"  GFS backups:  ✓ {backup_count} snapshot(s), validated {days_since}d ago")
            except ValueError:
                print(f"  GFS backups:  ✓ {backup_count} snapshot(s) (validation: {last_validate})")
        else:
            print(f"  GFS backups:  ⚠ {backup_count} snapshot(s) but never validated — run vault.py validate-backup")
            ok = False

    if ok:
        print("vault.py health: OK")
        sys.exit(0)
    else:
        print("vault.py health: FAILED")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: vault.py {decrypt|encrypt|status|health|release-lock|auto-lock}")
        print("       backup commands → python scripts/backup.py {snapshot|status|validate|restore|install|…}")
        print()
        print("  decrypt      — unlock sensitive state files for a catch-up session")
        print("  encrypt      — lock sensitive state files after catch-up + GFS backup")
        print("  status       — show current encryption state (read-only)")
        print("  health       — exit 0 if vault is healthy; exit 1 otherwise (for preflight)")
        print("  release-lock — force-clear a stale session lock (manual recovery)")
        print("  auto-lock    — encrypt if lock TTL exceeded (called by watchdog/cron)")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "decrypt":
        do_decrypt()
    elif cmd == "encrypt":
        do_encrypt()
    elif cmd == "status":
        do_status()
    elif cmd == "health":
        do_health()
    elif cmd in ("backup-status", "backup_status"):
        from scripts.backup import do_backup_status as _bkp_status
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
        from scripts.backup import do_validate_backup as _bkp_validate
        _bkp_validate(domain=domain, date_str=date_str)
    elif cmd == "restore":
        args      = sys.argv[2:]
        domain    = None
        date_str  = None
        dry_run   = False
        data_only = False
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
            else:
                i += 1
        from scripts.backup import do_restore as _bkp_restore
        _bkp_restore(date_str=date_str, domain=domain, dry_run=dry_run, data_only=data_only)
    elif cmd == "install":
        args      = sys.argv[2:]
        dry_run   = False
        data_only = False
        zip_arg   = None
        i = 0
        while i < len(args):
            if args[i] == "--dry-run":
                dry_run = True; i += 1
            elif args[i] == "--data-only":
                data_only = True; i += 1
            else:
                zip_arg = args[i]; i += 1
        if not zip_arg:
            print("Usage: vault.py install <zipfile> [--data-only] [--dry-run]")
            sys.exit(1)
        from scripts.backup import do_install as _bkp_install
        _bkp_install(zip_arg, dry_run=dry_run, data_only=data_only)
    elif cmd in ("release-lock", "release_lock", "--release-lock"):
        do_release_lock()
    elif cmd in ("auto-lock", "auto_lock"):
        sys.exit(do_auto_lock())
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: vault.py {decrypt|encrypt|status|health|release-lock}")
        sys.exit(1)


if __name__ == "__main__":
    main()
