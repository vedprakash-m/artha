#!/usr/bin/env python3
"""
setup_google_oauth.py — Artha one-time Google OAuth setup wizard
=================================================================
Run this once to configure Gmail + Calendar API access.

Steps:
  1. Prompts you to paste the Google Cloud OAuth client ID + secret
     (from Google Cloud Console → Credentials → OAuth 2.0 Client IDs)
  2. Stores credentials in system credential store
     (macOS Keychain / Windows Credential Manager via `keyring`)
  3. Opens your browser to complete the OAuth consent flow
  4. Stores the resulting token locally
  5. Tests the connection (profile fetch + event count)

Requirements (before running):
  a. Go to https://console.cloud.google.com/
  b. Create a project (or select existing)
  c. Enable APIs: Gmail API, Google Calendar API
  d. Credentials → Create OAuth 2.0 Client ID → Desktop app
  e. Download the JSON; you'll need client_id and client_secret from it

Usage:
  python scripts/setup_google_oauth.py
  python scripts/setup_google_oauth.py --status    (check current creds)
  python scripts/setup_google_oauth.py --revoke    (remove all tokens)

Ref: T-1A.3.1, T-1A.3.2, T-1A.3.3
"""

from __future__ import annotations

# Auto-relaunch inside the Artha venv if not already running there
# Cross-platform: ~/.artha-venvs/.venv-win on Windows, .venv on Mac
import os as _os
import sys
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

import getpass
import json
import os
import argparse
from typing import Optional

import keyring


INSTRUCTIONS = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ARTHA — Google OAuth Setup
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before this step you need a Google Cloud OAuth client.

1. Go to: https://console.cloud.google.com/
2. Create a new project (e.g. "artha-personal")
3. Go to "APIs & Services" → "Enable APIs and Services"
4. Enable: Gmail API, Google Calendar API
5. Go to "APIs & Services" → "Credentials"
6. Click "Create Credentials" → "OAuth client ID"
7. Application type: Desktop app
8. Name it (e.g. "Artha Desktop")
9. Download the JSON file — open it in a text editor
10. Copy the client_id and client_secret values below

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ---------------------------------------------------------------------------
# Credential store helpers (cross-platform via keyring)
# ---------------------------------------------------------------------------

def _keychain_set(service: str, account: str, secret: str) -> bool:
    """Store a secret in the system credential store. Returns True on success."""
    try:
        keyring.set_password(service, account, secret)
        return True
    except Exception:
        return False


def _keychain_get(service: str, account: str) -> Optional[str]:
    """Retrieve a secret from the system credential store."""
    try:
        val = keyring.get_password(service, account)
        return val if val else None
    except Exception:
        return None


def _keychain_delete(service: str, account: str) -> bool:
    """Delete a credential store entry."""
    try:
        keyring.delete_password(service, account)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _import_from_json(json_path: str) -> tuple[str, str]:
    """
    Parse a Google OAuth 2.0 client secrets JSON file and return (client_id, client_secret).
    Works with both 'installed' and 'web' credential types.
    If a directory is given, auto-selects the first matching JSON file inside it.
    """
    # If a directory was given, find the JSON file inside it
    if os.path.isdir(json_path):
        candidates = [
            f for f in os.listdir(json_path)
            if f.endswith(".json") and "client" in f.lower()
        ]
        if not candidates:
            # Broaden search to any JSON
            candidates = [f for f in os.listdir(json_path) if f.endswith(".json")]
        if not candidates:
            raise ValueError(f"No JSON files found in directory: {json_path}")
        if len(candidates) > 1:
            print(f"\n  Multiple JSON files found in {json_path}:")
            for i, c in enumerate(candidates):
                print(f"    [{i}] {c}")
            idx = input("  Enter number to select: ").strip()
            json_path = os.path.join(json_path, candidates[int(idx)])
        else:
            json_path = os.path.join(json_path, candidates[0])
        print(f"\n  Using: {json_path}")

    with open(json_path) as f:
        data = json.load(f)

    key = "installed" if "installed" in data else "web"
    if key not in data:
        raise ValueError(
            "Unrecognized JSON format. Expected Google OAuth client secrets file "
            "(should have 'installed' or 'web' key)."
        )

    client_id     = data[key]["client_id"]
    client_secret = data[key]["client_secret"]

    # Offer to delete the file — credentials are being stored in credential store
    print(f"\n  ⚠️  The JSON file contains your client_secret in plaintext.")
    print(f"  Once stored in the credential store, you no longer need it.")
    delete = input("  Delete the JSON file now? [Y/n]: ").strip().lower()
    if delete in ("", "y", "yes"):
        os.remove(json_path)
        print(f"  ✓ Deleted: {json_path}")
    else:
        print(f"  Kept. Consider deleting it manually: rm \"{json_path}\"")

    return client_id, client_secret


def _prompt_credentials() -> tuple[str, str]:
    """Prompt user to enter client_id and client_secret interactively."""
    print(INSTRUCTIONS)

    json_path = input(
        "Option A: Enter path to downloaded client JSON file\n"
        "  (press Enter to skip and type credentials manually): "
    ).strip()

    if json_path:
        json_path = os.path.expanduser(json_path)
        if not os.path.exists(json_path):
            print(f"\nFile not found: {json_path}")
            sys.exit(1)
        print("\nParsing OAuth JSON file...")
        client_id, client_secret = _import_from_json(json_path)
        print(f"  client_id:     {client_id[:30]}...")
        print("  client_secret: [loaded]")
        return client_id, client_secret

    print("\nOption B: Enter credentials manually")
    client_id = input("  client_id (ends in .apps.googleusercontent.com): ").strip()
    if not client_id:
        print("ERROR: client_id cannot be empty.")
        sys.exit(1)

    # Use getpass so the secret is not shown in terminal
    client_secret = getpass.getpass("  client_secret (hidden): ")
    if not client_secret:
        print("ERROR: client_secret cannot be empty.")
        sys.exit(1)

    return client_id, client_secret


def _store_credentials(client_id: str, client_secret: str) -> None:
    """Store client credentials in system credential store."""
    _store_label = "Keychain" if os.name != "nt" else "Credential Manager"
    print(f"\n  Storing credentials in {_store_label}...")
    ok_id     = _keychain_set("gmail-client-id",     "artha", client_id)
    ok_secret = _keychain_set("gmail-client-secret", "artha", client_secret)

    if ok_id and ok_secret:
        print(f"  ✓ Credentials stored in {_store_label}.")
        # Update status flag file so check_stored_credentials() is non-blocking
        try:
            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from google_auth import _status_set
            _status_set("gmail-client-id",     True)
            _status_set("gmail-client-secret", True)
        except Exception:
            pass  # Non-fatal
    else:
        print("  ✗ Failed to store credentials. Check credential store access.")
        sys.exit(1)


def _run_oauth_flow(service_name: str) -> None:
    """Run the OAuth browser consent flow for Gmail or Calendar."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        from google_auth import build_service
    except ImportError as e:
        print(f"ERROR: Could not import google_auth: {e}")
        print("Run: pip install -r scripts/requirements.txt")
        sys.exit(1)

    print(f"\n  Opening browser for {service_name} authorization...")
    print("  (If no browser opens, check for a URL printed above)")
    try:
        build_service(service_name, "v3" if service_name == "calendar" else "v1",
                      force_reauth=True)
        print(f"  ✓ {service_name} authorized and token stored.")
    except Exception as exc:
        print(f"\n  ✗ OAuth flow failed: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Status / revoke
# ---------------------------------------------------------------------------

def show_status() -> None:
    """Print current credential status."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        from google_auth import check_stored_credentials
    except ImportError as e:
        print(f"ERROR: Could not import google_auth: {e}")
        print("Run: pip install -r scripts/requirements.txt")
        sys.exit(1)

    print("Artha Google OAuth Status")
    print("─" * 40)
    creds = check_stored_credentials()
    print(f"  client_id stored:     {'✓' if creds.get('client_id_stored') else '✗'}")
    print(f"  client_secret stored: {'✓' if creds.get('client_secret_stored') else '✗'}")
    print(f"  Gmail token stored:   {'✓' if creds.get('gmail_token_stored') else '✗'}")
    print(f"  Calendar token stored:{'✓' if creds.get('gcal_token_stored') else '✗'}")

    all_good = all([
        creds.get("client_id_stored"),
        creds.get("client_secret_stored"),
        creds.get("gmail_token_stored"),
        creds.get("gcal_token_stored"),
    ])
    print()
    if all_good:
        print("Status: CONFIGURED ✓")
        print("\nTest with:")
        print("  python scripts/gmail_fetch.py --health")
        print("  python scripts/gcal_fetch.py --health")
    else:
        print("Status: INCOMPLETE — run setup_google_oauth.py to configure")


def revoke_tokens() -> None:
    """Remove all Artha Google tokens from credential store."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        from google_auth import revoke_tokens as _revoke
    except ImportError:
        print("ERROR: google_auth.py not found.")
        sys.exit(1)

    confirm = input(
        "This will delete all Artha Google tokens from the credential store.\n"
        "You will need to re-run setup to restore access.\n"
        "Type YES to confirm: "
    ).strip()

    if confirm != "YES":
        print("Aborted.")
        return

    _revoke()
    print("Done. Tokens removed from credential store.")
    print("Also deleting client credentials...")
    _keychain_delete("gmail-client-id",     "artha")
    _keychain_delete("gmail-client-secret", "artha")
    print("All Google credentials removed.")


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------

def _test_connections() -> None:
    """Verify that both Gmail and Calendar APIs respond correctly."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        from google_auth import build_service
    except ImportError:
        print("ERROR: google_auth.py not found.")
        sys.exit(1)

    print("\nTesting Gmail connection...")
    try:
        gmail   = build_service("gmail", "v1")
        profile = gmail.users().getProfile(userId="me").execute()
        email   = profile.get("emailAddress", "unknown")
        msgs    = profile.get("messagesTotal", 0)
        print(f"  ✓ Gmail OK: {email} ({msgs:,} messages)")
    except Exception as exc:
        print(f"  ✗ Gmail test failed: {exc}")

    print("\nTesting Calendar connection...")
    try:
        cal  = build_service("calendar", "v3")
        res  = cal.calendarList().list().execute()
        cals = res.get("items", [])
        print(f"  ✓ Calendar OK: {len(cals)} calendars visible")
        for c in cals[:3]:
            print(f"    • {c.get('summary', 'unknown')}")
    except Exception as exc:
        print(f"  ✗ Calendar test failed: {exc}")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def run_setup() -> None:
    """Full interactive setup wizard."""
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  ARTHA — Google OAuth Setup Wizard")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from google_auth import check_stored_credentials, _status_set

    status = check_stored_credentials()

    # Step 1: credentials (skip if already stored)
    if status.get("client_id_stored") and status.get("client_secret_stored"):
        print("STEP 1 — OAuth Client Credentials")
        print("  ✓ Already stored in Keychain — skipping.")
    else:
        print("STEP 1 — OAuth Client Credentials")
        client_id, client_secret = _prompt_credentials()
        _store_credentials(client_id, client_secret)

    # Step 2: Gmail OAuth (skip if token already stored)
    if status.get("gmail_token_stored"):
        print("\nSTEP 2 — Authorize Gmail Access")
        print("  ✓ Token already stored — skipping. (Use --reauth to force.)")
    else:
        print("\nSTEP 2 — Authorize Gmail Access")
        _run_oauth_flow("gmail")

    # Step 3: Calendar OAuth (skip if token already stored)
    if status.get("gcal_token_stored"):
        print("\nSTEP 3 — Authorize Calendar Access")
        print("  ✓ Token already stored — skipping. (Use --reauth to force.)")
    else:
        print("\nSTEP 3 — Authorize Calendar Access")
        _run_oauth_flow("calendar")

    # Step 4: test
    print("\nSTEP 4 — Verifying Connections")
    _test_connections()

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Setup complete!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    print("Next steps:")
    print("  1. Update config/settings.md with your Gmail address")
    print("  2. Run a test catch-up: just ask Claude to run Artha")
    print()
    print("Quick health checks:")
    print("  python scripts/gmail_fetch.py --health")
    print("  python scripts/gcal_fetch.py  --health")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Artha one-time Google OAuth setup wizard."
    )
    parser.add_argument("--status", action="store_true",
                        help="Show current credential status")
    parser.add_argument("--revoke", action="store_true",
                        help="Remove all Artha Google tokens from Keychain")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.revoke:
        revoke_tokens()
    else:
        run_setup()


if __name__ == "__main__":
    main()
