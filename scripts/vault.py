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
import zipfile
from datetime import date as _date_type, datetime, timedelta, timezone
from pathlib import Path

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
BACKUP_DIR      = ARTHA_DIR / "backups"      # root-level backups/ — syncs via OneDrive
BACKUP_MANIFEST = BACKUP_DIR / "manifest.json"
GFS_RETENTION: dict = {"daily": 7, "weekly": 4, "monthly": 12, "yearly": None}


# ---------------------------------------------------------------------------
# Backup registry
# ---------------------------------------------------------------------------

def _load_backup_registry() -> list:
    """Return the authoritative list of files to include in each backup snapshot.

    Reads config/user_profile.yaml → backup section.  Falls back to
    SENSITIVE_FILES (all state_encrypted) when the section is absent or
    unreadable so that existing behaviour is preserved.

    Each entry is a dict with:
      name         — unique slug used as the manifest 'domain' field
      source_type  — 'state_encrypted' | 'state_plain' | 'config'
      source_path  — absolute Path to the live file
      restore_path — path relative to ARTHA_DIR for restoration
    """
    backup_cfg = None
    if _yaml is not None:
        try:
            profile_path = CONFIG_DIR / "user_profile.yaml"
            profile      = _yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            backup_cfg   = (profile or {}).get("backup")
        except Exception:
            backup_cfg = None

    entries: list = []

    if backup_cfg:
        for sf in backup_cfg.get("state_files", []):
            name      = sf["name"]
            sensitive = sf.get("sensitive", False)
            if sensitive:
                entries.append({
                    "name":         name,
                    "source_type":  "state_encrypted",
                    "source_path":  STATE_DIR / f"{name}.md.age",
                    "restore_path": f"state/{name}.md.age",
                })
            else:
                entries.append({
                    "name":         name,
                    "source_type":  "state_plain",
                    "source_path":  STATE_DIR / f"{name}.md",
                    "restore_path": f"state/{name}.md",
                })
        for cfg_rel in backup_cfg.get("config_files", []):
            # cfg_rel is relative to ARTHA_DIR e.g. "config/user_profile.yaml"
            slug = "cfg__" + cfg_rel.replace("/", "__").replace(".", "_")
            entries.append({
                "name":         slug,
                "source_type":  "config",
                "source_path":  ARTHA_DIR / cfg_rel,
                "restore_path": cfg_rel,
            })

    if not entries:
        # Fallback: SENSITIVE_FILES list — all state_encrypted
        for name in SENSITIVE_FILES:
            entries.append({
                "name":         name,
                "source_type":  "state_encrypted",
                "source_path":  STATE_DIR / f"{name}.md.age",
                "restore_path": f"state/{name}.md.age",
            })

    return entries


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
    """Load backups/manifest.json; return empty structure on missing or corrupt file."""
    if BACKUP_MANIFEST.exists():
        try:
            return json.loads(BACKUP_MANIFEST.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"last_validate": None, "snapshots": {}}


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


def _prune_backups(tier: str, keep_n: int) -> None:
    """Remove oldest ZIP snapshots in tier_dir beyond keep_n retention limit."""
    tier_dir = BACKUP_DIR / tier
    if not tier_dir.exists():
        return
    zips = sorted(tier_dir.glob("*.zip"), key=lambda p: p.stem)  # stem = YYYY-MM-DD
    if len(zips) <= keep_n:
        return
    manifest = _load_manifest()
    for old_zip in zips[: len(zips) - keep_n]:
        key = f"{tier}/{old_zip.name}"
        try:
            old_zip.unlink()
            manifest["snapshots"].pop(key, None)
            log(f"BACKUP_PRUNED | zip: {old_zip.name} | tier: {tier} | retention: {keep_n}")
        except OSError as exc:
            log(f"BACKUP_PRUNE_FAILED | zip: {old_zip.name} | error: {exc}")
    _save_manifest(manifest)


def _zip_archive_path(entry: dict) -> str:
    """Return the path of this entry's file inside the backup ZIP archive.

    state_encrypted: restore_path is already '.age' — use as-is.
    state_plain / config: restore_path ends in '.md' or has no '.age' suffix —
        append '.age' because we encrypt on-the-fly before storing.
    """
    if entry["source_type"] == "state_encrypted":
        return entry["restore_path"]          # e.g. 'state/finance.md.age'
    return entry["restore_path"] + ".age"     # e.g. 'state/goals.md.age'


def _backup_snapshot(today: "_date_type | None" = None) -> int:
    """Create one ZIP snapshot containing ALL registered files for today's GFS tier.

    Called automatically at the end of a successful vault.py encrypt cycle.

    ZIP layout (root-level backups/ directory):
      backups/{tier}/YYYY-MM-DD.zip
        manifest.json           ← internal metadata + SHA-256 per archived file
        state/finance.md.age    ← state_encrypted: copied directly
        state/goals.md.age      ← state_plain: encrypted on-the-fly
        config/user_profile.yaml.age  ← config: encrypted on-the-fly

    The ZIP is written atomically via .tmp then os.replace().
    Returns the number of files packed into the ZIP (0 on failure).
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

    registry = _load_backup_registry()

    need_encrypt = any(e["source_type"] != "state_encrypted" for e in registry)
    pubkey: "str | None" = None
    if need_encrypt:
        try:
            pubkey = get_public_key()
        except SystemExit:
            log("BACKUP_PUBKEY_MISSING | plain/config files will be skipped this run")

    zip_name = f"{date_str}.zip"
    dest     = tier_dir / zip_name
    dest_tmp = Path(str(dest) + ".tmp")

    internal_files: dict = {}
    backed_up = 0

    with tempfile.TemporaryDirectory(prefix="artha_backup_") as staging:
        staging_path = Path(staging)

        for entry in registry:
            name         = entry["name"]
            source_type  = entry["source_type"]
            source_path  = entry["source_path"]
            restore_path = entry["restore_path"]
            arc_path     = _zip_archive_path(entry)

            if not source_path.exists():
                continue

            staged = staging_path / arc_path
            staged.parent.mkdir(parents=True, exist_ok=True)

            try:
                if source_type == "state_encrypted":
                    shutil.copy2(str(source_path), str(staged))
                else:
                    if not pubkey:
                        log(f"BACKUP_SKIP | name: {name} | reason: no_pubkey")
                        continue
                    if not age_encrypt(pubkey, source_path, staged):
                        staged.unlink(missing_ok=True)
                        log(f"BACKUP_ENCRYPT_FAILED | name: {name}")
                        continue
                    if not staged.exists():
                        log(f"BACKUP_ENCRYPT_FAILED | name: {name} | reason: no_output")
                        continue
            except OSError as exc:
                staged.unlink(missing_ok=True)
                log(f"BACKUP_STAGE_FAILED | name: {name} | error: {exc}")
                continue

            sha256 = _file_sha256(staged)
            internal_files[arc_path] = {
                "name":         name,
                "restore_path": restore_path,
                "sha256":       sha256,
                "size":         staged.stat().st_size,
                "source_type":  source_type,
            }
            backed_up += 1
            log(f"BACKUP_OK | name: {name} | tier: {tier} | date: {date_str} | sha256: {sha256[:16]}...")

        if backed_up == 0:
            return 0

        # Write internal manifest.json into staging dir
        internal_manifest = {
            "artha_backup_version": "2",
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "date":    date_str,
            "tier":    tier,
            "files":   internal_files,
        }
        (staging_path / "manifest.json").write_text(
            json.dumps(internal_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Pack staging dir into ZIP atomically
        try:
            with zipfile.ZipFile(str(dest_tmp), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for abs_p in sorted(staging_path.rglob("*")):
                    if abs_p.is_file():
                        zf.write(str(abs_p), str(abs_p.relative_to(staging_path)))
            os.replace(str(dest_tmp), str(dest))
        except OSError as exc:
            dest_tmp.unlink(missing_ok=True)
            log(f"BACKUP_ZIP_FAILED | tier: {tier} | date: {date_str} | error: {exc}")
            return 0

    # Update outer manifest catalog
    zip_sha256 = _file_sha256(dest)
    outer = _load_manifest()
    outer.setdefault("snapshots", {})
    outer["snapshots"][f"{tier}/{zip_name}"] = {
        "created":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date":       date_str,
        "file_count": backed_up,
        "sha256":     zip_sha256,
        "size":       dest.stat().st_size,
        "tier":       tier,
    }
    _save_manifest(outer)

    # Prune old ZIPs per tier
    for t, keep_n in GFS_RETENTION.items():
        if keep_n is not None:
            _prune_backups(t, keep_n)

    print(f"  GFS backup: {backed_up} file(s) → {tier}/{zip_name}")
    return backed_up


def _select_backup_zip(date_str: "str | None", manifest: dict) -> "str | None":
    """Return the outer-manifest key (e.g. 'daily/2026-03-14.zip') to use.

    date_str given → find first matching ZIP across any tier.
    Otherwise      → newest ZIP by date field.
    """
    snapshots = manifest.get("snapshots", {})
    if not snapshots:
        return None
    if date_str:
        matches = [k for k, v in snapshots.items() if v.get("date") == date_str]
        return matches[0] if matches else None
    return max(snapshots, key=lambda k: snapshots[k].get("date", ""))


def _restore_from_zip(zip_path: Path, domain: "str | None", dry_run: bool) -> None:
    """Core restore logic shared by do_restore() and do_install().

    For each file in the ZIP's internal manifest:
      state_encrypted → write .age file directly to restore_path
      state_plain     → age_decrypt to restore_path (plain .md)
      config          → age_decrypt to restore_path (original config file)

    SHA-256 verified before writing. Existing files overwritten.
    """
    if not check_age_installed():
        die("'age' not installed.")
    privkey = get_private_key()

    if not zip_path.exists():
        die(f"Backup ZIP not found: {zip_path}")

    try:
        zf_handle = zipfile.ZipFile(str(zip_path), "r")
    except zipfile.BadZipFile as exc:
        die(f"Corrupt ZIP file {zip_path.name}: {exc}")

    with zf_handle as zf:
        try:
            with zf.open("manifest.json") as mf:
                internal = json.load(mf)
        except Exception as exc:
            die(f"Cannot read internal manifest in {zip_path.name}: {exc}")

        files = internal.get("files", {})
        if domain:
            files = {k: v for k, v in files.items() if v.get("name") == domain}
            if not files:
                print(f"No files for domain {domain!r} in {zip_path.name}.")
                sys.exit(1)

        snap_date = internal.get("date", "unknown")
        snap_tier = internal.get("tier", "unknown")
        if dry_run:
            print(f"DRY RUN — snapshot {snap_tier}/{snap_date} ({zip_path.name}) — no files will be written\n")

        errors   = 0
        restored = 0

        with tempfile.TemporaryDirectory(prefix="artha_restore_") as tmpdir:
            tmppath = Path(tmpdir)

            for arc_path, meta in sorted(files.items()):
                restore_path = meta.get("restore_path", "")
                source_type  = meta.get("source_type", "state_encrypted")
                dest         = ARTHA_DIR / restore_path

                # Read from ZIP and verify SHA-256
                try:
                    zip_data = zf.read(arc_path)
                except KeyError:
                    print(f"  ✗ MISSING in ZIP:  {arc_path}")
                    log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: missing_in_zip")
                    errors += 1
                    continue

                actual_sha256   = hashlib.sha256(zip_data).hexdigest()
                expected_sha256 = meta.get("sha256", "")
                if expected_sha256 and actual_sha256 != expected_sha256:
                    print(f"  ✗ CHECKSUM FAIL:   {arc_path}")
                    log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: checksum_mismatch")
                    errors += 1
                    continue

                if dry_run:
                    action = "copy" if source_type == "state_encrypted" else "decrypt"
                    print(f"  would {action}: {arc_path!s:<55} → {restore_path}")
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                dest_tmp = Path(str(dest) + ".tmp")

                try:
                    if source_type == "state_encrypted":
                        dest_tmp.write_bytes(zip_data)
                        os.replace(str(dest_tmp), str(dest))
                    else:
                        extracted = tmppath / Path(arc_path).name
                        extracted.write_bytes(zip_data)
                        if not age_decrypt(privkey, extracted, dest_tmp):
                            dest_tmp.unlink(missing_ok=True)
                            print(f"  ✗ DECRYPT FAILED:  {arc_path}")
                            log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: decrypt_failed")
                            errors += 1
                            continue
                        os.replace(str(dest_tmp), str(dest))
                except OSError as exc:
                    dest_tmp.unlink(missing_ok=True)
                    print(f"  ✗ WRITE FAILED:    {arc_path}: {exc}")
                    log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: write_error")
                    errors += 1
                    continue

                print(f"  ✓ {restore_path}")
                log(f"RESTORE_OK | zip: {zip_path.name} | file: {arc_path} | dest: {restore_path} | type: {source_type}")
                restored += 1

    print()
    if not dry_run:
        if errors == 0:
            print(f"Restore complete: {restored} file(s) restored from {zip_path.name}.")
        else:
            print(f"Restore: {restored} restored, {errors} failed.")
            sys.exit(1)


def do_validate_backup(
    domain: "str | None" = None,
    date_str: "str | None" = None,
) -> None:
    """Validate backup ZIP — never touches live state.

    Opens the newest ZIP (or the one matching --date). For each file:
      1. SHA-256 inside ZIP matches internal manifest (bit-rot detection)
      2. age_decrypt succeeds
      3. Decrypted output non-empty
      4. YAML frontmatter (for state files)
      5. Word count >= 30 (for state files)

    Updates manifest.last_validate on full success.
    """
    if not check_age_installed():
        die("'age' not installed.")
    privkey  = get_private_key()
    manifest = _load_manifest()

    if not manifest.get("snapshots"):
        print("No backups found. Run vault.py encrypt first to create initial backups.")
        return

    zip_key = _select_backup_zip(date_str, manifest)
    if not zip_key:
        print(f"No matching backup snapshot found (date={date_str!r}).")
        sys.exit(1)

    zip_path = BACKUP_DIR / zip_key

    # Verify outer ZIP checksum first
    expected_zip_sha256 = manifest["snapshots"][zip_key].get("sha256", "")
    if expected_zip_sha256 and _file_sha256(zip_path) != expected_zip_sha256:
        print(f"  ✗ ZIP CHECKSUM FAIL: {zip_key}")
        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | reason: zip_checksum_mismatch")
        sys.exit(1)

    errors    = 0
    validated = 0

    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            try:
                with zf.open("manifest.json") as mf:
                    internal = json.load(mf)
            except Exception as exc:
                print(f"  ✗ MANIFEST READ FAIL: {zip_key}: {exc}")
                sys.exit(1)

            files_to_check = internal.get("files", {})
            if domain:
                files_to_check = {k: v for k, v in files_to_check.items()
                                  if v.get("name") == domain}
                if not files_to_check:
                    print(f"No files for domain {domain!r} in {zip_key}.")
                    sys.exit(1)

            with tempfile.TemporaryDirectory(prefix="artha_validate_") as tmpdir:
                tmppath = Path(tmpdir)

                for arc_path, meta in sorted(files_to_check.items()):
                    source_type = meta.get("source_type", "state_encrypted")

                    # Read from ZIP
                    try:
                        zip_data = zf.read(arc_path)
                    except KeyError:
                        print(f"  ✗ MISSING in ZIP:  {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: missing_in_zip")
                        errors += 1
                        continue

                    # 1. SHA-256
                    actual_sha256   = hashlib.sha256(zip_data).hexdigest()
                    expected_sha256 = meta.get("sha256", "")
                    if expected_sha256 and actual_sha256 != expected_sha256:
                        print(f"  ✗ CHECKSUM FAIL:   {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: checksum_mismatch")
                        errors += 1
                        continue

                    # 2. Decrypt
                    extracted  = tmppath / (Path(arc_path).name + ".enc")
                    decrypted  = tmppath / (Path(arc_path).stem + ".dec")
                    extracted.write_bytes(zip_data)
                    if not age_decrypt(privkey, extracted, decrypted):
                        print(f"  ✗ DECRYPT FAIL:    {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: decrypt_failed")
                        errors += 1
                        continue

                    # 3. Non-empty
                    if not decrypted.exists() or decrypted.stat().st_size == 0:
                        print(f"  ✗ EMPTY:           {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: empty_content")
                        errors += 1
                        continue

                    content    = decrypted.read_text(encoding="utf-8", errors="replace")
                    word_count = len(content.split())

                    # Config files don't need YAML/word-count checks
                    if source_type == "config":
                        print(f"  ✓ {arc_path:<55} type=config words={word_count} sha256={actual_sha256[:14]}...")
                        log(f"BACKUP_VALIDATE_OK | zip: {zip_key} | file: {arc_path} | type: config | words: {word_count}")
                        validated += 1
                        continue

                    # 4. YAML frontmatter
                    if not content.lstrip().startswith("---"):
                        print(f"  ✗ NO YAML:         {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: missing_yaml_frontmatter")
                        errors += 1
                        continue

                    # 5. Word count >= 30
                    if word_count < 30:
                        print(f"  ✗ TOO SHORT:       {arc_path} ({word_count} words)")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: content_too_short | words: {word_count}")
                        errors += 1
                        continue

                    print(f"  ✓ {arc_path:<55} type={source_type:<17} words={word_count} sha256={actual_sha256[:14]}...")
                    log(f"BACKUP_VALIDATE_OK | zip: {zip_key} | file: {arc_path} | type: {source_type} | words: {word_count} | sha256: {actual_sha256[:16]}")
                    validated += 1

    except zipfile.BadZipFile as exc:
        print(f"  ✗ CORRUPT ZIP: {zip_key}: {exc}")
        sys.exit(1)

    print()
    if errors == 0 and validated > 0:
        manifest["last_validate"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save_manifest(manifest)
        print(f"Backup validation: ✓ {validated} file(s) valid  [{zip_key}]")
    else:
        print(f"Backup validation: ✗ {errors} failure(s), {validated} passed  [{zip_key}]")
        if errors > 0:
            sys.exit(1)


def do_backup_status() -> None:
    """Show GFS backup catalog: ZIP names, tier counts, and last validation date."""
    manifest      = _load_manifest()
    snapshots     = manifest.get("snapshots", {})
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
    if not snapshots:
        print("  No backups found. Run vault.py encrypt to create the first backup.")
    else:
        for tier in ("yearly", "monthly", "weekly", "daily"):
            tier_snaps = {k: v for k, v in snapshots.items() if v.get("tier") == tier}
            if not tier_snaps:
                continue
            keep_n = GFS_RETENTION.get(tier)
            label  = str(keep_n) if keep_n else "∞"
            print(f"  {tier.upper():<8}  {len(tier_snaps)} snapshot(s), keep={label}")
            for key in sorted(tier_snaps, key=lambda k: tier_snaps[k].get("date", ""), reverse=True)[:3]:
                meta = tier_snaps[key]
                size_kb = meta.get("size", 0) / 1024
                print(f"            {meta['date']}  {Path(key).name:<30} {meta.get('file_count', '?')} files  {size_kb:.1f} KB")
            if len(tier_snaps) > 3:
                print(f"            … and {len(tier_snaps) - 3} older snapshot(s)")
    print("━" * 60)


# ---------------------------------------------------------------------------
# GFS restore — catalog-based and ZIP file based
# ---------------------------------------------------------------------------

def do_restore(
    date_str: "str | None" = None,
    domain:   "str | None" = None,
    dry_run:  bool = False,
) -> None:
    """Restore from the GFS backup catalog — finds the right ZIP automatically.

    vault.py restore [--date YYYY-MM-DD] [--domain DOMAIN] [--dry-run]
    """
    if not check_age_installed():
        die("'age' not installed.")
    manifest = _load_manifest()

    if not manifest.get("snapshots"):
        print("No backups found. Run vault.py encrypt first to create initial backups.")
        return

    zip_key = _select_backup_zip(date_str, manifest)
    if not zip_key:
        print(f"No matching backup snapshot (date={date_str!r}).")
        sys.exit(1)

    zip_path = BACKUP_DIR / zip_key
    print(f"Restoring from {zip_key} ...")
    _restore_from_zip(zip_path, domain, dry_run)


def do_install(zip_path_str: str, dry_run: bool = False) -> None:
    """Restore from an explicit backup ZIP file — for cold-start on a new machine.

    vault.py install <path/to/YYYY-MM-DD.zip> [--dry-run]

    The ZIP is self-contained: it carries its own internal manifest with SHA-256
    checksums and restore paths for every file.  No catalog access needed.
    """
    zip_path = Path(zip_path_str).expanduser().resolve()
    print(f"Installing from backup: {zip_path.name} ...")
    _restore_from_zip(zip_path, domain=None, dry_run=dry_run)


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
    backup_count  = len(manifest.get("snapshots", {}))
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
        print("Usage: vault.py {decrypt|encrypt|status|health|backup-status|validate-backup|restore|release-lock}")
        print()
        print("  decrypt          — unlock sensitive state files for a catch-up session")
        print("  encrypt          — lock sensitive state files after catch-up + GFS backup")
        print("  status           — show current encryption state (read-only)")
        print("  health           — exit 0 if vault is healthy; exit 1 otherwise (for preflight)")
        print("  backup-status    — show GFS backup catalog and last validation date")
        print("  validate-backup  — decrypt newest backup per domain, validate, log result")
        print("                       [--domain DOMAIN]  validate one domain only")
        print("                       [--date YYYY-MM-DD] validate a specific date snapshot")
        print("  restore          — restore files from a GFS snapshot (fresh-install rebuild)")
        print("                       [--date YYYY-MM-DD] snapshot date (default: newest per domain)")
        print("                       [--domain DOMAIN]   restore one domain only")
        print("                       [--dry-run]         preview without writing files")
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
    elif cmd == "restore":
        args     = sys.argv[2:]
        domain   = None
        date_str = None
        dry_run  = False
        i = 0
        while i < len(args):
            if args[i] == "--domain" and i + 1 < len(args):
                domain = args[i + 1]; i += 2
            elif args[i] == "--date" and i + 1 < len(args):
                date_str = args[i + 1]; i += 2
            elif args[i] == "--dry-run":
                dry_run = True; i += 1
            else:
                i += 1
        do_restore(date_str=date_str, domain=domain, dry_run=dry_run)
    elif cmd == "install":
        args    = sys.argv[2:]
        dry_run = False
        zip_arg = None
        i = 0
        while i < len(args):
            if args[i] == "--dry-run":
                dry_run = True; i += 1
            else:
                zip_arg = args[i]; i += 1
        if not zip_arg:
            print("Usage: vault.py install <zipfile> [--dry-run]")
            sys.exit(1)
        do_install(zip_arg, dry_run=dry_run)
    elif cmd in ("release-lock", "release_lock", "--release-lock"):
        do_release_lock()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: vault.py {decrypt|encrypt|status|health|backup-status|validate-backup|restore|install|release-lock}")
        sys.exit(1)


if __name__ == "__main__":
    main()
