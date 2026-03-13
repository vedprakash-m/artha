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
_ARTHA_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _os.name == "nt":
    _VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win", "Scripts", "python.exe")
    _VENV_PREFIX = _os.path.realpath(_os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win"))
else:
    _PROJ_VENV_PY = _os.path.join(_ARTHA_DIR, ".venv", "bin", "python")
    _LOCAL_VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv", "bin", "python")
    _VENV_PY = _PROJ_VENV_PY if _os.path.exists(_PROJ_VENV_PY) else _LOCAL_VENV_PY
    _VENV_PREFIX = _os.path.realpath(_os.path.dirname(_os.path.dirname(_VENV_PY)))
    if not _os.path.exists(_VENV_PY):
        import subprocess as _sp
        _local_venv = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv")
        _sp.run([sys.executable, "-m", "venv", _local_venv], check=True, capture_output=True)
        _sp.run([_local_venv + "/bin/pip", "install", "-q", "-r",
                 _os.path.join(_ARTHA_DIR, "scripts", "requirements.txt")], capture_output=True)
        _VENV_PY = _local_venv + "/bin/python"
        _VENV_PREFIX = _os.path.realpath(_local_venv)
if _os.path.exists(_VENV_PY) and _os.path.realpath(sys.prefix) != _VENV_PREFIX:
    if _os.name == "nt":
        import subprocess as _sp; raise SystemExit(_sp.call([_VENV_PY] + sys.argv))
    else:
        _os.execv(_VENV_PY, [_VENV_PY] + sys.argv)

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
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
]

STALE_THRESHOLD = 1800  # 30 minutes in seconds

KC_SERVICE = "age-key"
KC_ACCOUNT = "artha"


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
    """Read age recipient public key from settings.md."""
    settings_file = CONFIG_DIR / "settings.md"
    if not settings_file.exists():
        die("config/settings.md not found. Cannot read age_recipient.")
    text = settings_file.read_text()
    match = re.search(r"age_recipient:\s*\"?(age1[a-z0-9]+)", text)
    if not match:
        die("Cannot read age_recipient from config/settings.md. Populate the file first.")
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

def check_lock_state() -> int:
    """Check for active or stale session lock file.
    Returns: 0 = no lock, 1 = stale (auto-cleared), 2 = active (halt)
    """
    if not LOCK_FILE.exists():
        return 0

    lock_mtime = os.path.getmtime(LOCK_FILE)
    now = time.time()
    lock_age_sec = int(now - lock_mtime)
    lock_age_min = lock_age_sec // 60

    if lock_age_sec > STALE_THRESHOLD:
        print(f"  ⚠ Stale lock file detected (age: {lock_age_min}m — threshold: 30m).")
        print("  Previous session exited uncleanly. Auto-clearing lock and proceeding.")
        LOCK_FILE.unlink(missing_ok=True)
        log(f"STALE_LOCK_CLEARED | age: {lock_age_min}m | action: auto-cleared")
        return 1
    else:
        print(f"⛔ vault.py: Active session lock detected (age: {lock_age_min}m).")
        print("  Another catch-up session may be in progress.")
        print("  Halt: duplicate catch-up would corrupt state.")
        print(f"  To force-clear: delete {LOCK_FILE}")
        log(f"DECRYPT_BLOCKED | reason: active_lock | age: {lock_age_min}m")
        return 2


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
            # Layer 1: Pre-decrypt backup
            if plain_file.exists():
                shutil.copy2(str(plain_file), str(plain_file) + ".bak")
                log(f"INTEGRITY_BACKUP | file: {domain}.md | layer: 1_pre_decrypt")

            print(f"  Decrypting {domain}.md.age ...")
            if age_decrypt(privkey, age_file, plain_file):
                # Post-decrypt validation: empty?
                if not plain_file.exists() or plain_file.stat().st_size == 0:
                    print(f"  ERROR: Decrypted {domain}.md is empty — restoring backup", file=sys.stderr)
                    bak = Path(str(plain_file) + ".bak")
                    if bak.exists():
                        shutil.move(str(bak), str(plain_file))
                        log(f"INTEGRITY_RESTORE | file: {domain}.md | reason: empty_decrypt | layer: 1")
                    errors += 1
                    continue

                # Post-decrypt validation: YAML frontmatter?
                with open(plain_file, encoding="utf-8", errors="replace") as f:
                    first_line = f.readline()
                if not first_line.startswith("---"):
                    print(f"  ERROR: Decrypted {domain}.md missing YAML frontmatter — restoring backup",
                          file=sys.stderr)
                    bak = Path(str(plain_file) + ".bak")
                    if bak.exists():
                        shutil.move(str(bak), str(plain_file))
                        log(f"INTEGRITY_RESTORE | file: {domain}.md | reason: invalid_yaml | layer: 1")
                    errors += 1
                    continue

                # Bootstrap detection
                text = plain_file.read_text(encoding="utf-8", errors="replace")
                if "updated_by: bootstrap" in text:
                    log(f"BOOTSTRAP_DETECTED | file: {domain}.md | note: placeholder data — run /bootstrap {domain}")

                log(f"DECRYPT_OK | file: {domain}.md")
            else:
                print(f"  ERROR: Failed to decrypt {domain}.md.age", file=sys.stderr)
                bak = Path(str(plain_file) + ".bak")
                if bak.exists():
                    shutil.move(str(bak), str(plain_file))
                    log(f"INTEGRITY_RESTORE | file: {domain}.md | reason: decrypt_failed | layer: 1")
                errors += 1
        elif plain_file.exists():
            print(f"  {domain}.md already exists as plaintext (no .age file). Leaving as-is.")

    # contacts.md lives in config/
    contacts_age = CONFIG_DIR / "contacts.md.age"
    contacts_plain = CONFIG_DIR / "contacts.md"
    if contacts_age.exists():
        if contacts_plain.exists():
            shutil.copy2(str(contacts_plain), str(contacts_plain) + ".bak")
            log("INTEGRITY_BACKUP | file: contacts.md | layer: 1_pre_decrypt")

        print("  Decrypting contacts.md.age ...")
        if age_decrypt(privkey, contacts_age, contacts_plain):
            if (not contacts_plain.exists() or contacts_plain.stat().st_size == 0
                    or not contacts_plain.read_text().startswith("---")):
                print("  ERROR: Decrypted contacts.md is empty or invalid — restoring backup",
                      file=sys.stderr)
                bak = Path(str(contacts_plain) + ".bak")
                if bak.exists():
                    shutil.move(str(bak), str(contacts_plain))
                    log("INTEGRITY_RESTORE | file: contacts.md | reason: invalid_content | layer: 1")
                errors += 1
            else:
                log("DECRYPT_OK | file: contacts.md")
        else:
            print("  ERROR: Failed to decrypt contacts.md.age", file=sys.stderr)
            bak = Path(str(contacts_plain) + ".bak")
            if bak.exists():
                shutil.move(str(bak), str(contacts_plain))
                log("INTEGRITY_RESTORE | file: contacts.md | reason: decrypt_failed | layer: 1")
            errors += 1

    if errors > 0:
        die(f"{errors} file(s) failed to decrypt. Aborting catch-up.")

    # Create lock file
    LOCK_FILE.touch()
    print("vault.py: Decrypt complete. Lock file created.")
    log("SESSION_START | lock_file: created")


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

    # contacts.md in config/
    contacts_plain = CONFIG_DIR / "contacts.md"
    contacts_age = CONFIG_DIR / "contacts.md.age"
    if contacts_plain.exists():
        print("  Encrypting contacts.md ...")
        tmp_file = Path(str(contacts_age) + ".tmp")
        if age_encrypt(pubkey, contacts_plain, tmp_file):
            shutil.move(str(tmp_file), str(contacts_age))
            contacts_plain.unlink()
            Path(str(contacts_plain) + ".bak").unlink(missing_ok=True)
            encrypted_count += 1
            log("ENCRYPT_OK | file: contacts.md")
        else:
            tmp_file.unlink(missing_ok=True)
            print("  ERROR: Failed to encrypt contacts.md", file=sys.stderr)
            errors += 1

    if errors > 0:
        die(f"{errors} file(s) failed to encrypt. CRITICAL: plaintext may remain on disk.")

    # Remove lock file only if everything succeeded
    LOCK_FILE.unlink(missing_ok=True)
    print(f"vault.py: Encrypt complete. {encrypted_count} files secured. Lock file removed.")
    log(f"SESSION_END | lock_file: removed | files_encrypted: {encrypted_count}")


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

    contacts_plain = CONFIG_DIR / "contacts.md"
    contacts_age = CONFIG_DIR / "contacts.md.age"
    if contacts_plain.exists():
        print("  [PLAINTEXT] contacts.md  ⚠ NOT encrypted")
    elif contacts_age.exists():
        print("  [ENCRYPTED] contacts.md.age ✓")
    else:
        print("  [MISSING]   contacts — no .md or .age found")

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
    if (CONFIG_DIR / "contacts.md.bak").exists():
        bak_count += 1
    if bak_count > 0:
        print(f"  Backup files: ⚠ {bak_count} orphaned .bak file(s) — stale plaintext")
        ok = False
    else:
        print("  Backup files: ✓ none (clean)")

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
        print("Usage: vault.py {decrypt|encrypt|status|health}")
        print()
        print("  decrypt  — unlock sensitive state files for a catch-up session")
        print("  encrypt  — lock sensitive state files after catch-up")
        print("  status   — show current encryption state (read-only)")
        print("  health   — exit 0 if vault is healthy; exit 1 otherwise (for preflight)")
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
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: vault.py {decrypt|encrypt|status|health}")
        sys.exit(1)


if __name__ == "__main__":
    main()
