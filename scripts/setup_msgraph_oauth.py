#!/usr/bin/env python3
"""
setup_msgraph_oauth.py — Artha Microsoft Graph API OAuth setup
==============================================================
One-time interactive OAuth 2.0 Authorization Code + PKCE flow to acquire a
long-lived Graph API token covering Microsoft To Do and Outlook.

Usage:
  python scripts/setup_msgraph_oauth.py          # First-time setup
  python scripts/setup_msgraph_oauth.py --reauth  # Force new token
  python scripts/setup_msgraph_oauth.py --health  # Check stored token

Token stored at:  {ARTHA_DIR}/.tokens/msgraph-token.json
Client ID stored: System credential store (service: msgraph-client-id, account: artha)

Scopes requested:
  Tasks.ReadWrite          — create / read / update / delete To Do tasks
  Tasks.ReadWrite.Shared   — access shared task lists
  Mail.Read                — read Outlook email (msgraph_fetch.py, T-1B.1.1)
  User.Read                — basic profile for /me endpoint
  offline_access           — refresh token (added automatically by MSAL, do not list explicitly)

  Calendars.Read is implied for personal accounts by the User.Read scope.

  NOT YET ADDED (Phase 2 — add to _SCOPES list below, then run --reauth):
  Notes.Read               — read OneNote notebooks, sections, pages (T-1B.1.7)

Prerequisites:
  1. Azure portal (portal.azure.com) → App registrations → New registration
     - Name: "Artha Personal Assistant"
     - Supported account type: "Personal Microsoft accounts only"
     - Redirect URI: Mobile and desktop applications → http://localhost:8400
  2. Copy Application (client) ID
  3. Store it (the setup wizard will prompt you, or use keyring CLI):
     python -c "import keyring; keyring.set_password('msgraph-client-id','artha','<CLIENT_ID>')"

Ref: T-1B.6.1, TS §3.1
"""

from __future__ import annotations

import sys
# Ensure we run inside the Artha venv. Ref: standardization.md §7.3
from _bootstrap import reexec_in_venv; reexec_in_venv()

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Optional

import keyring

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from _bootstrap import setup_artha_dir as _setup_artha_dir
_ARTHA_DIR   = _setup_artha_dir()
TOKEN_DIR    = os.path.join(_ARTHA_DIR, ".tokens")
TOKEN_FILE   = os.path.join(TOKEN_DIR, "msgraph-token.json")
KC_CLIENT_ID = "msgraph-client-id"
KC_ACCOUNT   = "artha"

# Client-credential mirror file — keyring is platform-local, this file syncs
# via OneDrive so running setup on one machine makes creds available everywhere.
_CLIENT_CREDS_FILE = os.path.join(TOKEN_DIR, "msgraph-client-creds.json")

# Microsoft identity platform — personal accounts endpoint
_AUTHORITY   = "https://login.microsoftonline.com/consumers"
_SCOPES      = [
    "Tasks.ReadWrite",
    "Tasks.ReadWrite.Shared",
    "Mail.Read",
    "Calendars.Read",       # required for msgraph_calendar_fetch.py
    "User.Read",
    "Notes.Read",           # required for msgraph_onenote_fetch.py (T-1B.1.7)
    # NOTE: offline_access is a reserved OIDC scope — MSAL adds it automatically.
    # Do NOT include it explicitly or the token request will fail.
]

# ---------------------------------------------------------------------------
# Credential store helpers (cross-platform via keyring)
# ---------------------------------------------------------------------------

def _read_client_creds_file(service: str) -> Optional[str]:
    """Read a client credential from the synced mirror file (.tokens/)."""
    try:
        with open(_CLIENT_CREDS_FILE) as f:
            return json.load(f).get(service) or None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _mirror_client_cred(service: str, value: str) -> None:
    """Write a client credential to the synced mirror file for cross-platform access."""
    _ensure_token_dir()
    try:
        with open(_CLIENT_CREDS_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data[service] = value
    with open(_CLIENT_CREDS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    if os.name != "nt":
        os.chmod(_CLIENT_CREDS_FILE, 0o600)


def _keychain_read(service: str, account: str = KC_ACCOUNT) -> Optional[str]:
    """Read a secret from the system credential store, falling back to synced file."""
    try:
        val = keyring.get_password(service, account)
        if val:
            return val
    except Exception:
        pass
    # Fall back to OneDrive-synced mirror file
    if service == KC_CLIENT_ID:
        # Also try the token file's embedded client_id
        file_val = _read_client_creds_file(service)
        if file_val:
            return file_val
        token = _load_token()
        if token and token.get("_artha_client_id"):
            return token["_artha_client_id"]
    return None


def _keychain_write(service: str, value: str, account: str = KC_ACCOUNT) -> None:
    """Write or overwrite a credential store entry and mirror to synced file."""
    try:
        keyring.set_password(service, account, value)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to write credential for service='{service}': {exc}"
        )
    # Mirror to synced file so the other platform can read it
    if service == KC_CLIENT_ID:
        _mirror_client_cred(service, value)


# ---------------------------------------------------------------------------
# Token file helpers
# ---------------------------------------------------------------------------

def _ensure_token_dir() -> None:
    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)


def _save_token(token_response: dict, client_id: Optional[str] = None) -> None:
    """Persist msal token response + metadata to {ARTHA_DIR}/.tokens/msgraph-token.json.
    Embeds client_id in the file so Cowork VM can refresh without needing credential store.
    """
    _ensure_token_dir()
    # Add our own expiry field in ISO format for easy freshness checks
    expires_in = token_response.get("expires_in", 3600)
    expiry_dt  = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta
    expiry_dt  = expiry_dt + timedelta(seconds=int(expires_in))

    data = {**token_response, "expiry": expiry_dt.isoformat()}
    # Embed client_id so it survives without credential store access (e.g. Cowork VM)
    if client_id:
        data["_artha_client_id"] = client_id
    elif "_artha_client_id" not in data:
        # Carry forward from previous save if not provided
        existing = _load_token()
        if existing and existing.get("_artha_client_id"):
            data["_artha_client_id"] = existing["_artha_client_id"]
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600) if os.name != "nt" else None
    print(f"[msgraph] Token saved → {TOKEN_FILE}", file=sys.stderr)


def _load_token() -> Optional[dict]:
    """Load token dict from disk. Returns None if missing or unreadable."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# MSAL helpers
# ---------------------------------------------------------------------------

def _build_app(client_id: str):
    """Build an msal.PublicClientApplication for interactive / silent auth."""
    import msal
    return msal.PublicClientApplication(
        client_id,
        authority=_AUTHORITY,
        token_cache=None,  # we manage our own token file
    )


def _acquire_token_interactive(client_id: str) -> dict:
    """Run the interactive OAuth browser flow and return the token response."""
    app   = _build_app(client_id)
    result = app.acquire_token_interactive(
        scopes=_SCOPES,
        port=8400,
        timeout=120,
    )
    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "unknown error"))
        raise RuntimeError(f"OAuth flow failed: {error}")
    return result


def _acquire_token_silent(client_id: str, token_data: dict) -> Optional[dict]:
    """
    Attempt a silent refresh using the stored refresh_token.
    Returns a new token dict on success, None on failure.
    """
    import msal
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return None

    app = _build_app(client_id)
    # MSAL PublicClientApplication can refresh via acquire_token_by_refresh_token
    result = app.acquire_token_by_refresh_token(
        refresh_token=refresh_token,
        scopes=_SCOPES,
    )
    if "access_token" in result:
        return result
    return None


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check() -> None:
    """Check stored Graph token status and print summary."""
    print("Microsoft Graph Health Check")
    print("─" * 40)

    # 1. Client ID
    client_id = _keychain_read(KC_CLIENT_ID)
    print(f"  Client ID in credential store: {'✓' if client_id else '✗ MISSING'}")
    if not client_id:
        print("\n  Action: store your Azure app client_id:")
        print('    python -c "import keyring; keyring.set_password(\'msgraph-client-id\',\'artha\',\'<CLIENT_ID>\')"')
        sys.exit(1)

    # 2. Token file
    if not os.path.exists(TOKEN_FILE):
        print(f"  Token file:            ✗ MISSING ({TOKEN_FILE})")
        print("\n  Action: run python scripts/setup_msgraph_oauth.py")
        sys.exit(1)
    print(f"  Token file:            ✓ {TOKEN_FILE}")

    # 3. Expiry
    token_data = _load_token()
    if token_data is None:
        print("  Token file:            ✗ Unreadable")
        sys.exit(1)

    expiry_str = token_data.get("expiry", "")
    if expiry_str:
        try:
            expiry_dt   = datetime.fromisoformat(expiry_str.rstrip("Z"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            secs_left   = (expiry_dt - datetime.now(timezone.utc)).total_seconds()
            if secs_left < 0:
                print(f"  Token expiry:          ⚠ Expired {int(-secs_left/60)}m ago (auto-refresh on next use)")
            else:
                print(f"  Token expiry:          ✓ Valid for {int(secs_left/60)}m")
        except ValueError:
            print("  Token expiry:          ? Could not parse expiry")
    else:
        print("  Token expiry:          ? No expiry stored")

    # 4. Scopes
    scopes_granted = token_data.get("scope", "")
    print(f"  Scopes:                {scopes_granted or '(not recorded)' }")

    # 5. Live connectivity check
    print("\n  Testing Graph API connectivity...")
    access_token = token_data.get("access_token")
    if not access_token:
        print("  ✗ No access token in file")
        sys.exit(1)

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            profile = json.load(resp)
        print(f"  ✓ Connected as: {profile.get('displayName', 'unknown')} ({profile.get('mail') or profile.get('userPrincipalName', '')})")
    except Exception as exc:
        print(f"  ✗ Graph API call failed: {exc}")
        print("  Token may be expired — run: python scripts/setup_msgraph_oauth.py --reauth")
        sys.exit(1)

    print("\nMicrosoft Graph: OK")


# ---------------------------------------------------------------------------
# Proactive token refresh
# ---------------------------------------------------------------------------

def ensure_valid_token(warn_seconds: int = 300) -> dict:
    """
    Return a valid token dict (proactively refresh if near expiry).
    Raises RuntimeError if no token exists or refresh fails.
    Called by todo_sync.py and preflight.py.
    """
    token_data = _load_token()
    if token_data is None:
        raise RuntimeError(
            "Microsoft Graph token missing. Run: python scripts/setup_msgraph_oauth.py"
        )

    # Check expiry
    expiry_str = token_data.get("expiry", "")
    needs_refresh = False
    if expiry_str:
        try:
            expiry_dt = datetime.fromisoformat(expiry_str.rstrip("Z"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            secs_left = (expiry_dt - datetime.now(timezone.utc)).total_seconds()
            needs_refresh = secs_left < warn_seconds
        except ValueError:
            pass  # Can't parse — try refresh to be safe
    else:
        needs_refresh = True  # No expiry stored — attempt refresh

    if needs_refresh:
        # Try credential store first; fall back to client_id embedded in the token file
        client_id = _keychain_read(KC_CLIENT_ID) or token_data.get("_artha_client_id")
        if not client_id:
            print("[msgraph] ⚠ client_id not in credential store or token file — skipping refresh", file=sys.stderr)
        else:
            new_token = _acquire_token_silent(client_id, token_data)
            if new_token:
                # --- Layer 2: Track refresh token usage (vm-hardening.md Phase 2.4) ---
                new_token["_last_refresh_success"] = datetime.now(timezone.utc).isoformat()
                _save_token(new_token, client_id=client_id)
                return new_token
            # Silent refresh failed — token may still work, return it
            print("[msgraph] ⚠ Silent refresh failed — using existing token", file=sys.stderr)

    return token_data


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up or check Microsoft Graph API OAuth token for Artha."
    )
    parser.add_argument("--reauth", action="store_true",
                        help="Force a new interactive OAuth flow (discards existing token)")
    parser.add_argument("--health", action="store_true",
                        help="Check stored token status and connectivity")
    args = parser.parse_args()

    if args.health:
        run_health_check()
        return

    # Read client ID from Keychain
    client_id = _keychain_read(KC_CLIENT_ID)
    if not client_id:
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║  Microsoft Graph OAuth Setup — Artha                        ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        print()
        print("Client ID not found in credential store.")
        print("Complete the pre-requisites first:")
        print()
        print("  1. Go to https://portal.azure.com → App registrations → New registration")
        print("     • Name: 'Artha Personal Assistant'")
        print("     • Supported accounts: 'Personal Microsoft accounts only'")
        print("     • Redirect URI: Mobile/desktop → http://localhost:8400")
        print()
        print("  2. Copy the Application (client) ID")
        print()
        print("  3. Run:")
        print('     python -c "import keyring; keyring.set_password(\'msgraph-client-id\',\'artha\',\'<CLIENT_ID>\')"')
        print()
        print("  4. Re-run this script.")
        sys.exit(1)

    # Check if token already exists (and not force-reauth)
    if not args.reauth and os.path.exists(TOKEN_FILE):
        token_data = _load_token()
        if token_data and token_data.get("access_token"):
            print("[msgraph] Token already stored. Use --reauth to force a new flow.")
            print(f"          Token file: {TOKEN_FILE}")
            # Quick connectivity check
            try:
                token = ensure_valid_token()
                print("[msgraph] ✓ Token valid")
            except Exception as exc:
                print(f"[msgraph] ⚠ Token check failed: {exc}")
            return

    if args.reauth:
        print("[msgraph] Forcing new OAuth flow (--reauth)...")

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Microsoft Graph OAuth — Browser will open for login        ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("  This will request the following scopes:")
    for s in _SCOPES:
        print(f"    • {s}")
    print()
    print("  Sign in with your personal Microsoft account.")
    print("  (The browser will open automatically in a moment...)")
    print()

    try:
        token_response = _acquire_token_interactive(client_id)
        _save_token(token_response, client_id=client_id)
        print()
        print("✓ Authentication successful!")
        print(f"  Token saved to: {TOKEN_FILE}")

        # Quick verify
        access_token = token_response.get("access_token")
        if access_token:
            try:
                import urllib.request
                req = urllib.request.Request(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    profile = json.load(resp)
                print(f"  Authenticated as: {profile.get('displayName', 'unknown')}")
                print(f"  Account: {profile.get('mail') or profile.get('userPrincipalName', '')}")
            except Exception as exc:
                print(f"  ⚠ Could not verify identity: {exc}")

        print()
        print("Next step: run python scripts/setup_todo_lists.py to create domain task lists.")

    except Exception as exc:
        print(f"\n✗ Authentication failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
