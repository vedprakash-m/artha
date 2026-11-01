#!/usr/bin/env python3
"""
setup_icloud_auth.py — Artha Apple iCloud credential setup
===========================================================
Store and retrieve iCloud credentials (Apple ID + app-specific password)
in the native OS credential store. Used by lib.auth.load_icloud_credentials().

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

Credentials stored in the native OS credential store (Keychain on macOS):
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
# Ensure we run inside the Artha venv. Ref: standardization.md §7.3
from _bootstrap import reexec_in_venv; reexec_in_venv()

import argparse
import getpass
import imaplib
from pathlib import Path
from typing import Optional

from catchup_core.session_lock import default_installation_dir, installation_fence
from lib import credentials

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
# Native OS credential helpers
# ---------------------------------------------------------------------------

def _kc_get(service: str, account: str) -> Optional[str]:
    """Read only from a native OS credential backend; never from legacy files."""
    try:
        value = credentials.system_keyring().get_password(service, account)
    except Exception:
        raise RuntimeError("OS credential store is unavailable.") from None
    if value is not None and not isinstance(value, str):
        raise RuntimeError("OS credential record has an invalid type.")
    return value


def _kc_set(service: str, account: str, password: str) -> None:
    """Upsert and verify a native OS record without a subprocess or file mirror."""
    import uuid
    from catchup_core.recovery_registry import mutation, profile_for

    with installation_fence(default_installation_dir()), mutation(
            profile_for(service, account), 'client', service, account, uuid.uuid4().hex):
        try:
            backend = credentials.system_keyring()
            backend.set_password(service, account, password)
            persisted = backend.get_password(service, account)
        except Exception:
            raise RuntimeError("OS credential write could not be confirmed.") from None
        if persisted != password:
            raise RuntimeError("OS credential store did not persist the record.")


# ---------------------------------------------------------------------------
# Public API — used by fetch scripts
# ---------------------------------------------------------------------------

def ensure_valid_credentials() -> tuple[str, str]:
    """
    Return (apple_id, app_specific_password) from the native OS credential store.

    Raises RuntimeError if credentials are not stored yet — caller should
    prompt the user to run: python scripts/setup_icloud_auth.py
    """
    apple_id = _kc_get(KC_APPLE_ID_SERVICE, KC_ACCOUNT)
    app_pwd  = _kc_get(KC_APP_PWD_SERVICE,  KC_ACCOUNT)

    if not apple_id or not app_pwd:
        raise RuntimeError(
            "iCloud credentials not found in the OS credential store.\n"
            "Run: python scripts/setup_icloud_auth.py"
        )
    return apple_id, app_pwd


# ---------------------------------------------------------------------------
# Setup / interactive credential entry
# ---------------------------------------------------------------------------

def run_setup(*, force: bool = False) -> None:
    """Interactively prompt for and store iCloud credentials."""
    try:
        existing_id = _kc_get(KC_APPLE_ID_SERVICE, KC_ACCOUNT)
        existing_pwd = _kc_get(KC_APP_PWD_SERVICE, KC_ACCOUNT)
    except RuntimeError:
        print("ERROR: Native OS credential store is unavailable or invalid.", file=sys.stderr)
        sys.exit(1)
    if existing_id and existing_pwd and not force:
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

    print("\nStoring credentials in the native OS credential store ... ", end="", flush=True)
    try:
        with installation_fence(default_installation_dir()):
            _kc_set(KC_APPLE_ID_SERVICE, KC_ACCOUNT, apple_id)
            _kc_set(KC_APP_PWD_SERVICE,  KC_ACCOUNT, app_pwd)
        print("✓")
    except RuntimeError:
        print("\nERROR: OS credential write could not be confirmed.", file=sys.stderr)
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
    except imaplib.IMAP4.error:
        return False, "IMAP authentication failed."
    except OSError:
        return False, "IMAP connection failed."


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
    except Exception:  # noqa: BLE001
        return False, "CalDAV connection or authentication failed."


def run_health_check() -> None:
    """Full health check printed to stdout. Exits 1 on any failure."""
    print("iCloud health check")
    print("-" * 40)

    try:
        apple_id, app_pwd = ensure_valid_credentials()
    except RuntimeError:
        print("✗ OS credentials unavailable, missing or invalid.", file=sys.stderr)
        print("  Run: python scripts/setup_icloud_auth.py", file=sys.stderr)
        sys.exit(1)

    print("  Credentials : native OS credential store")
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
