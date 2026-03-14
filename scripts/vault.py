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
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
from _bootstrap import reexec_in_venv
reexec_in_venv()

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date as _date_type, datetime, timedelta, timezone
from pathlib import Path

# Ensure UTF-8 stdout/stderr on Windows (avoids cp1252 encoding errors with ✓/✗)
if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import keyring

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARTHA_DIR   = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCK_FILE   = ARTHA_DIR / ".artha-decrypted"
STATE_DIR   = ARTHA_DIR / "state"
CONFIG_DIR  = ARTHA_DIR / "config"
AUDIT_LOG   = STATE_DIR / "audit.md"

# Sensitive files in state/ that must be encrypted at rest
SENSITIVE_FILES = [
    "immigration",
    "finance",
    "insurance",
    "estate",
    "health",
    "audit",
    "vehicle",
    "skills_cache",
    "occasions",
    "contacts",
]

STALE_THRESHOLD = 300   # 5 minutes — soft TTL: stale IF the locking PID is no longer running
LOCK_TTL        = 1800  # 30 minutes — hard TTL: stale regardless of PID status (session crash ceiling)

KC_SERVICE = "age-key"
KC_ACCOUNT = "artha"

# GFS Backup — directory layout and retention policy (§8.5.2)
BACKUP_DIR      = STATE_DIR / "backups"
BACKUP_MANIFEST = BACKUP_DIR / "manifest.json"
GFS_RETENTION: dict = {"daily": 7, "weekly": 4, "monthly": 12, "yearly": None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    """Log a vault event to audit.md (if it exists as plaintext) and stdout."""
    entry = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] VAULT | {msg}"
    if AUDIT_LOG.exists():
        try:
            with open(AUDIT_LOG, "a") as f:
                f.write(entry + "\n")
        except OSError:
            pass
    print(entry)


def die(msg: str) -> None:
    """Print error and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def get_private_key() -> str:
    """Retrieve private key from system credential store."""
    try:
        key = keyring.get_password(KC_SERVICE, KC_ACCOUNT)
    except Exception as exc:
        die(f"Cannot access credential store: {exc}")
    if not key:
        die(
            "Cannot retrieve age private key from credential store.\n"
            "Store it with:\n"
            f'  python -c "import keyring; keyring.set_password(\'{KC_SERVICE}\',\'{KC_ACCOUNT}\',\'<AGE-SECRET-KEY>\')"'
        )
    return key


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
    settings_file = CONFIG_DIR / "settings.md"
    if not settings_file.exists():
        die("age_recipient not found in user_profile.yaml or config/settings.md.")
    text = settings_file.read_text()
    match = re.search(r"age_recipient:\s*\"?(age1[a-z0-9]+)", text)
    if not match:
        die("Cannot read age_recipient from user_profile.yaml or config/settings.md. Populate encryption.age_recipient in your profile.")
    pubkey = match.group(1)
    if not pubkey.startswith("age1"):
        die(f"Invalid age public key (must start with 'age1'). Got: {pubkey}")
    return pubkey


def check_age_installed() -> bool:
    """Check if `age` encryption tool is on PATH."""
    return shutil.which("age") is not None


def age_decrypt(privkey: str, input_path: Path, output_path: Path) -> bool:
    """Decrypt a file using age with the private key.
    Writes key to a temp file (avoids process substitution which is bash-only).
    Returns True on success.
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
    """Encrypt a file using age with the public key. Returns True on success."""
    result = subprocess.run(
        ["age", "--recipient", pubkey,
         "--output", str(output_path), str(input_path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0


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
# GFS Vault Backup (§8.5.2)
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    """Streaming SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    """Load state/backups/manifest.json; return empty structure on missing or corrupt file."""
    if BACKUP_MANIFEST.exists():
        try:
            return json.loads(BACKUP_MANIFEST.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"files": {}, "last_validate": None}


def _save_manifest(manifest: dict) -> None:
    """Atomically overwrite manifest.json via a .tmp sibling."""
    BACKUP_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(BACKUP_MANIFEST) + ".tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(BACKUP_MANIFEST))
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        log(f"MANIFEST_SAVE_FAILED | error: {exc}")


def _get_backup_tier(d: "_date_type") -> str:
    """Return the GFS tier for date *d*.

    Priority (highest first):
      yearly  — December 31
      monthly — last day of any month (next calendar day is a different month)
      weekly  — Sunday
      daily   — everything else
    """
    if d.month == 12 and d.day == 31:
        return "yearly"
    if (d + timedelta(days=1)).month != d.month:
        return "monthly"
    if d.weekday() == 6:  # Sunday == 6
        return "weekly"
    return "daily"


def _prune_backups(domain: str, tier: str, keep_n: int) -> None:
    """Remove oldest backup files for domain/tier beyond keep_n retention limit."""
    tier_dir = BACKUP_DIR / tier
    if not tier_dir.exists():
        return
    # Glob produces YYYY-MM-DD sorted alphabetically = chronologically
    files = sorted(tier_dir.glob(f"{domain}-*.md.age"))
    if len(files) <= keep_n:
        return
    manifest = _load_manifest()
    for old_file in files[: len(files) - keep_n]:
        key = f"{tier}/{old_file.name}"
        try:
            old_file.unlink()
            manifest["files"].pop(key, None)
            log(f"BACKUP_PRUNED | file: {old_file.name} | tier: {tier} | retention: {keep_n}")
        except OSError as exc:
            log(f"BACKUP_PRUNE_FAILED | file: {old_file.name} | error: {exc}")
    _save_manifest(manifest)


def _backup_snapshot(today: "_date_type | None" = None) -> int:
    """Snapshot all .age files into the GFS hierarchy.

    Called automatically at the end of a successful vault.py encrypt cycle.
    Reads from .age files — plaintext has already been deleted at this point.
    Each file is written atomically via a .tmp sibling then os.replace().
    Returns the number of files successfully backed up.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    tier     = _get_backup_tier(today)
    date_str = today.isoformat()
    tier_dir = BACKUP_DIR / tier
    try:
        tier_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"BACKUP_MKDIR_FAILED | tier: {tier} | error: {exc}")
        return 0

    manifest  = _load_manifest()
    backed_up = 0

    for domain in SENSITIVE_FILES:
        age_file = STATE_DIR / f"{domain}.md.age"
        if not age_file.exists():
            continue
        dest     = tier_dir / f"{domain}-{date_str}.md.age"
        dest_tmp = Path(str(dest) + ".tmp")
        try:
            shutil.copy2(str(age_file), str(dest_tmp))
            os.replace(str(dest_tmp), str(dest))
        except OSError as exc:
            dest_tmp.unlink(missing_ok=True)
            log(f"BACKUP_COPY_FAILED | file: {domain}.md.age | tier: {tier} | error: {exc}")
            continue

        sha256 = _file_sha256(dest)
        key    = f"{tier}/{domain}-{date_str}.md.age"
        manifest["files"][key] = {
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "date":    date_str,
            "domain":  domain,
            "sha256":  sha256,
            "size":    dest.stat().st_size,
            "tier":    tier,
        }
        backed_up += 1
        log(f"BACKUP_OK | file: {domain}.md.age | tier: {tier} | date: {date_str} | sha256: {sha256[:16]}...")

    _save_manifest(manifest)

    # Prune old files after saving new manifest
    for domain in SENSITIVE_FILES:
        for t, keep_n in GFS_RETENTION.items():
            if keep_n is not None:
                _prune_backups(domain, t, keep_n)

    if backed_up:
        print(f"  GFS backup: {backed_up} file(s) → {tier}/ ({date_str})")
    return backed_up


def _select_backup_files(domain: "str | None", date_str: "str | None", manifest: dict) -> dict:
    """Return the subset of manifest entries to validate.

    Rules (first match wins):
      date_str supplied  → all domains for that date
      domain supplied    → newest entry for that domain
      neither            → newest entry per domain (default for full validation)
    """
    all_files = manifest.get("files", {})
    if date_str:
        return {k: v for k, v in all_files.items() if v.get("date") == date_str}
    if domain:
        subset = {k: v for k, v in all_files.items() if v.get("domain") == domain}
        if not subset:
            return {}
        newest_key = max(subset, key=lambda k: subset[k].get("date", ""))
        return {newest_key: subset[newest_key]}
    # Default: newest per domain
    newest: dict = {}
    for key, meta in all_files.items():
        d = meta.get("domain", "")
        if d not in newest or meta.get("date", "") > newest[d][1].get("date", ""):
            newest[d] = (key, meta)
    return {k: v for k, v in newest.values()}


def do_validate_backup(
    domain: "str | None" = None,
    date_str: "str | None" = None,
) -> None:
    """Decrypt backup file(s) to a temp directory and validate content.

    Never touches live state.  Checks (in order):
      1. SHA-256 matches manifest entry           (bit-rot detection)
      2. age_decrypt succeeds
      3. Output is non-empty
      4. First non-blank line starts with '---'   (YAML frontmatter)
      5. Word count >= 30                         (non-trivial content)

    Updates manifest.last_validate on full success.
    Exits 1 if any file fails validation.
    """
    if not check_age_installed():
        die("'age' not installed. Install:\n"
            "  macOS: brew install age\n"
            "  Windows: winget install FiloSottile.age")
    privkey  = get_private_key()
    manifest = _load_manifest()

    if not manifest.get("files"):
        print("No backups found. Run vault.py encrypt first to create initial backups.")
        return

    to_validate = _select_backup_files(domain, date_str, manifest)
    if not to_validate:
        print(f"No matching backup files found (domain={domain!r}, date={date_str!r}).")
        sys.exit(1)

    errors    = 0
    validated = 0

    with tempfile.TemporaryDirectory(prefix="artha_validate_") as tmpdir:
        tmppath = Path(tmpdir)
        for key, meta in sorted(to_validate.items()):
            backup_file = BACKUP_DIR / key

            # File must exist on disk
            if not backup_file.exists():
                print(f"  ✗ MISSING:         {key}")
                log(f"BACKUP_VALIDATE_FAIL | key: {key} | reason: file_missing")
                errors += 1
                continue

            # 1. SHA-256 checksum
            actual_sha256   = _file_sha256(backup_file)
            expected_sha256 = meta.get("sha256", "")
            if expected_sha256 and actual_sha256 != expected_sha256:
                print(f"  ✗ CHECKSUM FAIL:   {key}")
                log(f"BACKUP_VALIDATE_FAIL | key: {key} | reason: checksum_mismatch"
                    f" | expected: {expected_sha256[:16]} | actual: {actual_sha256[:16]}")
                errors += 1
                continue

            # 2. Decrypt to temp dir
            tmp_plain = tmppath / f"{meta['domain']}-{meta['date']}.md"
            if not age_decrypt(privkey, backup_file, tmp_plain):
                print(f"  ✗ DECRYPT FAIL:    {key}")
                log(f"BACKUP_VALIDATE_FAIL | key: {key} | reason: decrypt_failed")
                errors += 1
                continue

            # 3. Non-empty
            if not tmp_plain.exists() or tmp_plain.stat().st_size == 0:
                print(f"  ✗ EMPTY:           {key}")
                log(f"BACKUP_VALIDATE_FAIL | key: {key} | reason: empty_content")
                errors += 1
                continue

            content = tmp_plain.read_text(encoding="utf-8", errors="replace")

            # 4. YAML frontmatter
            if not content.lstrip().startswith("---"):
                print(f"  ✗ NO YAML:         {key}")
                log(f"BACKUP_VALIDATE_FAIL | key: {key} | reason: missing_yaml_frontmatter")
                errors += 1
                continue

            # 5. Sanity: at least 30 words
            word_count = len(content.split())
            if word_count < 30:
                print(f"  ✗ TOO SHORT:       {key} ({word_count} words)")
                log(f"BACKUP_VALIDATE_FAIL | key: {key} | reason: content_too_short | words: {word_count}")
                errors += 1
                continue

            print(f"  ✓ {key:<55} tier={meta['tier']:<7} date={meta['date']}"
                  f" words={word_count} sha256={actual_sha256[:12]}...")
            log(f"BACKUP_VALIDATE_OK | key: {key} | tier: {meta['tier']} | date: {meta['date']}"
                f" | words: {word_count} | sha256: {actual_sha256[:16]}")
            validated += 1

    if errors == 0 and validated > 0:
        manifest["last_validate"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save_manifest(manifest)
        print(f"\nBackup validation: ✓ {validated} file(s) valid")
    else:
        print(f"\nBackup validation: ✗ {errors} failure(s), {validated} passed")
        if errors > 0:
            sys.exit(1)


def do_backup_status() -> None:
    """Show GFS backup catalog, tier counts, and last validation date."""
    manifest      = _load_manifest()
    files         = manifest.get("files", {})
    last_validate = manifest.get("last_validate")

    print("━" * 60)
    print(f"VAULT BACKUP STATUS — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("━" * 60)

    if last_validate:
        try:
            last_dt    = datetime.fromisoformat(last_validate.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_dt).days
            if days_since <= 35:
                status = f"✓ {days_since}d ago"
            else:
                status = f"⚠ {days_since}d ago (overdue — run: vault.py validate-backup)"
        except ValueError:
            status = last_validate
        print(f"  Last validation : {status}")
    else:
        print("  Last validation : ⚠ NEVER — run: vault.py validate-backup")

    print()
    if not files:
        print("  No backups found. Run vault.py encrypt to create the first backup.")
    else:
        for tier in ("yearly", "monthly", "weekly", "daily"):
            tier_files = {k: v for k, v in files.items() if v.get("tier") == tier}
            if not tier_files:
                continue
            dates  = sorted({v.get("date", "") for v in tier_files.values()}, reverse=True)
            keep_n = GFS_RETENTION.get(tier)
            label  = str(keep_n) if keep_n else "∞"
            print(f"  {tier.upper():<8}  {len(dates)} snapshot(s), keep={label}")
            for d in dates[:3]:
                doms   = sorted(v["domain"] for k, v in tier_files.items() if v.get("date") == d)
                suffix = f" +{len(doms) - 4} more" if len(doms) > 4 else ""
                print(f"            {d}  {len(doms)} file(s) ({', '.join(doms[:4])}{suffix})")
            if len(dates) > 3:
                print(f"            … and {len(dates) - 3} older snapshot(s)")
    print("━" * 60)


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

    # GFS backup snapshot (§8.5.2): copy encrypted .age files into rotation
    _backup_snapshot()


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
    manifest      = _load_manifest()
    last_validate = manifest.get("last_validate")
    backup_count  = len({v.get("date") for v in manifest.get("files", {}).values()})
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
        print("Usage: vault.py {decrypt|encrypt|status|health|backup-status|validate-backup|release-lock}")
        print()
        print("  decrypt          — unlock sensitive state files for a catch-up session")
        print("  encrypt          — lock sensitive state files after catch-up + GFS backup")
        print("  status           — show current encryption state (read-only)")
        print("  health           — exit 0 if vault is healthy; exit 1 otherwise (for preflight)")
        print("  backup-status    — show GFS backup catalog and last validation date")
        print("  validate-backup  — decrypt newest backup per domain, validate, log result")
        print("                       [--domain DOMAIN]  validate one domain only")
        print("                       [--date YYYY-MM-DD] validate a specific date snapshot")
        print("  release-lock     — force-clear a stale session lock (manual recovery)")
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
        do_backup_status()
    elif cmd in ("validate-backup", "validate_backup"):
        # Parse optional --domain and --date flags
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
        do_validate_backup(domain=domain, date_str=date_str)
    elif cmd in ("release-lock", "release_lock", "--release-lock"):
        do_release_lock()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: vault.py {decrypt|encrypt|status|health|backup-status|validate-backup|release-lock}")
        sys.exit(1)


if __name__ == "__main__":
    main()
