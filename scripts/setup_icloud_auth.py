#!/usr/bin/env python3
"""
setup_icloud_auth.py — Artha Apple iCloud credential setup
===========================================================
Store and retrieve iCloud credentials (Apple ID + app-specific password)
in the macOS Keychain. Used by icloud_mail_fetch.py and icloud_calendar_fetch.py.

Apple does not offer an OAuth 2.0 REST API for third-party data access.
Instead, it uses two open, well-established protocols:
  - IMAP  (imap.mail.me.com:993)   — iCloud Mail
  - CalDAV (caldav.icloud.com)       — iCloud Calendar + Reminders

Both protocols authenticate via an app-specific password, which is a
16-character token generated at account.apple.com. It is static (does not
expire unless revoked or Apple ID password changes). No refresh flow needed.

Usage:
  python scripts/setup_icloud_auth.py           # First-time setup (interactive)
  python scripts/setup_icloud_auth.py --reauth  # Replace stored credentials
  python scripts/setup_icloud_auth.py --health  # Test IMAP + CalDAV connectivity

Credentials stored in macOS Keychain:
  Apple ID:         service "icloud-apple-id",       account "artha"
  App password:     service "icloud-app-password",   account "artha"

Prerequisites:
  1. Go to account.apple.com → Sign-In and Security → App-Specific Passwords
  2. Click "Generate an app-specific password"
  3. Label it "Artha" (or similar) — Apple gives you a 16-char xxxx-xxxx-xxxx-xxxx token
  4. Have your Apple ID email (e.g., yourname@icloud.com or yourname@me.com) ready
  5. Run: python scripts/setup_icloud_auth.py

Ref: TS §3.9, T-1B.1.8
"""

from __future__ import annotations

import sys
import os as _os

# ---------------------------------------------------------------------------
# Auto-bootstrap: relaunch inside the Artha venv if not already there
# Cross-platform: ~/.artha-venvs/.venv-win on Windows, .venv on Mac
# ---------------------------------------------------------------------------
_ARTHA_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _os.name == "nt":
    _VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win", "Scripts", "python.exe")
    _VENV_PREFIX = _os.path.realpath(_os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win"))
else:
    # Check project-relative .venv first (symlink on Mac → ~/.artha-venvs/.venv; real dir pre-move)
    _PROJ_VENV_PY = _os.path.join(_ARTHA_DIR, ".venv", "bin", "python")
    _LOCAL_VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv", "bin", "python")
    _VENV_PY = _PROJ_VENV_PY if _os.path.exists(_PROJ_VENV_PY) else _LOCAL_VENV_PY
    _VENV_PREFIX = _os.path.realpath(_os.path.dirname(_os.path.dirname(_VENV_PY)))
    # Auto-create venv from requirements.txt if not found (e.g. first run in Cowork VM)
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

import argparse
import getpass
import imaplib
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KC_APPLE_ID_SERVICE  = "icloud-apple-id"
KC_APP_PWD_SERVICE   = "icloud-app-password"
KC_ACCOUNT           = "artha"

IMAP_HOST            = "imap.mail.me.com"
IMAP_PORT            = 993
CALDAV_URL           = "https://caldav.icloud.com"

# ---------------------------------------------------------------------------
# File-based credential store (fallback when macOS Keychain is unavailable,
# e.g. in the Cowork Linux VM).  Stored alongside OAuth tokens in .tokens/.
# Written by setup_icloud_auth.py on Mac; read-only in the VM.
# ---------------------------------------------------------------------------
import json as _json

_ARTHA_DIR_ICLOUD = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
CRED_FILE = _os.path.join(_ARTHA_DIR_ICLOUD, ".tokens", "icloud-credentials.json")

# ---------------------------------------------------------------------------
# Keychain helpers
# ---------------------------------------------------------------------------

def _kc_get(service: str, account: str) -> Optional[str]:
    """Read a password from the macOS Keychain.
    Falls back to CRED_FILE when running outside macOS (e.g. Linux VM)."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Keychain unavailable — try file-based fallback
        try:
            with open(CRED_FILE) as f:
                data = _json.load(f)
            return data.get(service, {}).get(account)
        except (OSError, _json.JSONDecodeError, KeyError):
            return None


def _kc_set(service: str, account: str, password: str) -> None:
    """Write/update a password in the macOS Keychain (upserts).
    Also mirrors credentials to CRED_FILE for cross-platform access."""
    # macOS Keychain write
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
        )
        subprocess.run(
            ["security", "add-generic-password", "-s", service, "-a", account, "-w", password],
            check=True, capture_output=True,
        )
    except FileNotFoundError:
        pass  # not on macOS — file-only write below

    # Mirror to CRED_FILE so Cowork VM can read without Keychain
    _os.makedirs(_os.path.dirname(CRED_FILE), mode=0o700, exist_ok=True)
    try:
        with open(CRED_FILE) as f:
            data = _json.load(f)
    except (OSError, _json.JSONDecodeError):
        data = {}
    data.setdefault(service, {})[account] = password
    with open(CRED_FILE, "w") as f:
        _json.dump(data, f, indent=2)
    _os.chmod(CRED_FILE, 0o600)


# ---------------------------------------------------------------------------
# Public API — used by fetch scripts
# ---------------------------------------------------------------------------

def ensure_valid_credentials() -> tuple[str, str]:
    """
    Return (apple_id, app_specific_password) from the macOS Keychain.

    Raises RuntimeError if credentials are not stored yet — caller should
    prompt the user to run: python scripts/setup_icloud_auth.py
    """
    apple_id = _kc_get(KC_APPLE_ID_SERVICE, KC_ACCOUNT)
    app_pwd  = _kc_get(KC_APP_PWD_SERVICE,  KC_ACCOUNT)

    if not apple_id or not app_pwd:
        raise RuntimeError(
            "iCloud credentials not found (Keychain unavailable and credential file missing).\n"
            f"Run on Mac: python scripts/setup_icloud_auth.py  (writes {CRED_FILE})"
        )
    return apple_id, app_pwd


# ---------------------------------------------------------------------------
# Setup / interactive credential entry
# ---------------------------------------------------------------------------

def run_setup(*, force: bool = False) -> None:
    """Interactively prompt for and store iCloud credentials."""
    existing_id = _kc_get(KC_APPLE_ID_SERVICE, KC_ACCOUNT)
    if existing_id and not force:
        print(f"✓ iCloud credentials already stored (Apple ID: {existing_id})")
        print("  Use --reauth to replace them.")
        return

    print("=" * 60)
    print("  Artha — iCloud Credential Setup")
    print("=" * 60)
    print()
    print("You need an app-specific password (NOT your main Apple ID password).")
    print("Generate one at: account.apple.com → Sign-In and Security")
    print("                 → App-Specific Passwords → Generate")
    print("Label the password 'Artha' so you can revoke it later.")
    print()

    apple_id = input("Apple ID (e.g. yourname@icloud.com): ").strip()
    if not apple_id or "@" not in apple_id:
        print("ERROR: Invalid Apple ID format.", file=sys.stderr)
        sys.exit(1)

    app_pwd = getpass.getpass("App-specific password (xxxx-xxxx-xxxx-xxxx): ").strip()
    if not app_pwd:
        print("ERROR: Password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    print("\nStoring credentials in macOS Keychain + credential file ... ", end="", flush=True)
    try:
        _kc_set(KC_APPLE_ID_SERVICE, KC_ACCOUNT, apple_id)
        _kc_set(KC_APP_PWD_SERVICE,  KC_ACCOUNT, app_pwd)
        print("✓")
        print(f"  Credential file: {CRED_FILE}")
    except subprocess.CalledProcessError as exc:
        print(f"\nERROR: Keychain write failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\nTesting IMAP connection ... ", end="", flush=True)
    ok, msg = _test_imap(apple_id, app_pwd)
    print("✓" if ok else f"✗ — {msg}")

    print("Testing CalDAV connection ... ", end="", flush=True)
    ok, msg = _test_caldav(apple_id, app_pwd)
    print("✓" if ok else f"✗ — {msg}")
    print()

    print("Setup complete.")
    print("Run preflight to verify: python scripts/preflight.py")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _test_imap(apple_id: str, app_pwd: str) -> tuple[bool, str]:
    """Return (success, message) for IMAP connectivity + auth test."""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(apple_id, app_pwd)
        status, counts = mail.select("INBOX", readonly=True)
        if status != "OK":
            mail.logout()
            return False, f"INBOX select returned {status}"
        inbox_count = int(counts[0].decode() if counts[0] else 0)
        mail.logout()
        return True, f"IMAP OK — {inbox_count} messages in INBOX"
    except imaplib.IMAP4.error as exc:
        return False, f"IMAP auth error: {exc}"
    except OSError as exc:
        return False, f"IMAP connection error: {exc}"


def _test_caldav(apple_id: str, app_pwd: str) -> tuple[bool, str]:
    """Return (success, message) for CalDAV connectivity + auth test."""
    try:
        import caldav  # noqa: PLC0415 — lazy import; caldav is an optional dep
        client = caldav.DAVClient(
            url=CALDAV_URL,
            username=apple_id,
            password=app_pwd,
        )
        principal = client.principal()
        calendars = principal.calendars()
        names = [getattr(c, "name", "?") or "?" for c in calendars]
        return True, f"CalDAV OK — {len(calendars)} calendars: {', '.join(names[:5])}"
    except ImportError:
        return False, "caldav package not installed — run: pip install caldav"
    except Exception as exc:  # noqa: BLE001
        return False, f"CalDAV error: {exc}"


def run_health_check() -> None:
    """Full health check printed to stdout. Exits 1 on any failure."""
    print("iCloud health check")
    print("-" * 40)

    apple_id = _kc_get(KC_APPLE_ID_SERVICE, KC_ACCOUNT)
    app_pwd  = _kc_get(KC_APP_PWD_SERVICE,  KC_ACCOUNT)

    if not apple_id or not app_pwd:
        print("✗ Credentials not found (Keychain unavailable; credential file missing or incomplete)", file=sys.stderr)
        print(f"  Run on Mac: python scripts/setup_icloud_auth.py  (writes {CRED_FILE})", file=sys.stderr)
        sys.exit(1)

    # Show where credentials came from
    cred_source = "macOS Keychain"
    if not _os.path.exists("/usr/bin/security"):
        cred_source = f"file ({CRED_FILE})"
    print(f"  Credentials : {cred_source}")
    print(f"  Apple ID : {apple_id}")

    imap_ok, imap_msg = _test_imap(apple_id, app_pwd)
    print(f"  IMAP     : {'✓' if imap_ok else '✗'} {imap_msg}")

    caldav_ok, caldav_msg = _test_caldav(apple_id, app_pwd)
    print(f"  CalDAV   : {'✓' if caldav_ok else '✗'} {caldav_msg}")

    if imap_ok and caldav_ok:
        print(f"\niCloud: OK ({apple_id})")
    else:
        print("\niCloud: DEGRADED — one or more checks failed", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up or verify iCloud (IMAP + CalDAV) credentials for Artha.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Replace stored credentials with new ones (interactive).",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Test stored credentials against IMAP and CalDAV. Exits 1 on failure.",
    )
    args = parser.parse_args()

    if args.health:
        run_health_check()
    else:
        run_setup(force=args.reauth)


if __name__ == "__main__":
    main()
