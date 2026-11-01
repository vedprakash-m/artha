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

Token stored in: verified OS credential records (separate personal and Work services)
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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from _bootstrap import setup_artha_dir as _setup_artha_dir
_ARTHA_DIR   = _setup_artha_dir()
TOKEN_DIR    = os.path.join(_ARTHA_DIR, ".tokens")
TOKEN_FILE   = os.path.join(TOKEN_DIR, "msgraph-token.json")
KC_CLIENT_ID = "msgraph-client-id"
KC_ACCOUNT   = "artha"

# Legacy mirror path, retained only to identify explicit migration inputs.
_CLIENT_CREDS_FILE = os.path.join(TOKEN_DIR, "msgraph-client-creds.json")

# ── Personal account (consumers endpoint) ──────────────────────────────────
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

# ── Work / AAD account (organizations endpoint) ─────────────────────────────
# Auth path for work accounts — uses MSAL with a **personal-tenant App Registration**
# (registered in a free personal Azure AD, not the corporate tenant). This avoids
# the "App Registration restricted" policy on Microsoft internal tenants, because
# a third-party app registered in any tenant can receive delegated Files.Read consent
# from users in any other AAD tenant — exactly how Slack, Notion, GitHub etc. work.
#
# Setup (one-time, ~5 minutes):
#   1. portal.azure.com  → sign in with your PERSONAL Microsoft account (not @microsoft.com)
#      If you have no personal Azure account: create a free tenant via
#      https://azure.microsoft.com/free/ using any personal MSA.
#   2. Azure Active Directory → App registrations → New registration
#      • Name: "Artha Work Assistant"
#      • Supported accounts: "Accounts in any organizational directory (Any Azure AD tenant)"
#      • Redirect URI: Mobile/desktop applications → http://localhost:8401
#      • API Permissions (delegated): Files.Read, Mail.Read, User.Read
#        (no admin consent needed — Files.Read is user-level delegated)
#   3. Copy Application (client) ID from the app's Overview page
#   4. Store it:
#      python -c "import keyring; keyring.set_password('msgraph-work-client-id','artha','<CLIENT_ID>')"
#   5. Run:
#      python scripts/setup_msgraph_oauth.py --work
#      (Browser opens; sign in with your work account; consent once; done.)
#
# NOTE: Azure CLI (`az account get-access-token`) only provides directory/admin scopes
#       in the Microsoft corporate tenant (AADSTS65002 blocks Files.Read). It cannot
#       be used for SharePoint/Files endpoints — only the MSAL path above works.
KC_WORK_CLIENT_ID  = "msgraph-work-client-id"
WORK_TOKEN_FILE    = os.path.join(TOKEN_DIR, "msgraph-work-token.json")
MCP_WORK_TOKEN_FILE = os.path.join(TOKEN_DIR, "msgraph-work-mcp-token.json")
_WORK_AUTHORITY    = "https://login.microsoftonline.com/organizations"
_WORK_SCOPES       = [
    "Files.Read",   # OneDrive / "shared with me" — user-level delegated, no admin consent
    "Mail.Read",    # work email (shared-with-me signals)
    "User.Read",
    # NOTE: offline_access is added automatically by MSAL.
    # NOTE: Sites.Read.All requires admin consent — omit; Files.Read covers sharedWithMe+search.
]
_MCP_WORK_SCOPES = [
    "Mail.ReadWrite",
    "Calendars.ReadWrite",
    "User.Read",
    "Tasks.ReadWrite",
    "Chat.ReadWrite",
]

# ---------------------------------------------------------------------------
# Credential store helpers (cross-platform via keyring)
# ---------------------------------------------------------------------------

def _credential_store():
    from lib.credentials import CredentialStore
    return CredentialStore()


def _client_backend():
    from lib.credentials import system_keyring
    return system_keyring()


def _keychain_read(service: str, account: str = KC_ACCOUNT) -> Optional[str]:
    """Read client credentials only from the platform store."""
    try:
        value = _client_backend().get_password(service, account)
    except Exception:
        raise RuntimeError("OS credential store unavailable; no plaintext fallback") from None
    return value if isinstance(value, str) and value else None


def _keychain_write(service: str, value: str, account: str = KC_ACCOUNT) -> None:
    """Write and verify a platform-local client credential."""
    import uuid
    from pathlib import Path
    from catchup_core.recovery_registry import mutation, profile_for
    from catchup_core.session_lock import default_installation_dir, installation_fence

    with installation_fence(default_installation_dir()), mutation(
            profile_for(service, account), 'client', service, account, uuid.uuid4().hex):
        try:
            backend = _client_backend()
            backend.set_password(service, account, value)
            persisted = backend.get_password(service, account)
        except Exception:
            raise RuntimeError("OS client credential write could not be confirmed") from None
        if persisted != value:
            raise RuntimeError("OS credential store did not persist the client credential")


# ---------------------------------------------------------------------------
# Platform token record helpers
# ---------------------------------------------------------------------------

def _save_token(token_response: dict, client_id: Optional[str] = None, *, work: bool = False,
                expected_revision: str | None = None) -> None:
    """Persist MSAL token response and metadata to the platform credential store."""
    # Add our own expiry field in ISO format for easy freshness checks
    expires_in = token_response.get("expires_in", 3600)
    expiry_dt  = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta
    expiry_dt  = expiry_dt + timedelta(seconds=int(expires_in))

    data = {**token_response, "expiry": expiry_dt.isoformat()}
    # Keep the client binding with this token inside its OS record.
    if client_id:
        data["_artha_client_id"] = client_id
    elif "_artha_client_id" not in data:
        # Carry forward from previous save if not provided
        existing = _load_token(work=work)
        if existing and existing.get("_artha_client_id"):
            data["_artha_client_id"] = existing["_artha_client_id"]
    _credential_store().write("msgraph.work" if work else "msgraph.personal", data, expected_revision=expected_revision)
    print("[msgraph] Token saved to the OS credential store", file=sys.stderr)


def _load_token(*, work: bool = False) -> Optional[dict]:
    """Load only the verified platform-local token; never read legacy JSON implicitly."""
    return _credential_store().read("msgraph.work" if work else "msgraph.personal")


# ---------------------------------------------------------------------------
# MSAL helpers
# ---------------------------------------------------------------------------

def _build_app(client_id: str, *, work: bool = False):
    """Build an msal.PublicClientApplication for interactive / silent auth."""
    import msal
    authority = _WORK_AUTHORITY if work else _AUTHORITY
    return msal.PublicClientApplication(
        client_id,
        authority=authority,
        token_cache=None,  # verified OS record persistence is handled below
        timeout=10,  # bound each authority/token HTTP request, including action credential capture
    )


def _acquire_token_interactive(client_id: str, *, work: bool = False, mcp: bool = False) -> dict:
    """Run the interactive OAuth browser flow and return the token response."""
    app    = _build_app(client_id, work=work)
    if mcp and not work:
        raise ValueError('Work MCP requires the Work authority')
    scopes = _MCP_WORK_SCOPES if mcp else (_WORK_SCOPES if work else _SCOPES)
    port   = 8401 if work else 8400
    result = app.acquire_token_interactive(
        scopes=scopes,
        port=port,
        timeout=120,
    )
    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "unknown error"))
        raise RuntimeError(f"OAuth flow failed: {error}")
    return result


def _acquire_token_via_azcli() -> dict | None:
    """Get a Graph API access token from Azure CLI (no App Registration needed).

    Returns a token dict compatible with the MSAL token-file format on success,
    or None if AZ CLI is not installed / user is not logged in.
    """
    import shutil
    import subprocess
    az_cmd = shutil.which("az")  # finds az.cmd on Windows, az on Linux/macOS
    if not az_cmd:
        return None
    try:
        result = subprocess.run(
            [az_cmd, "account", "get-access-token",
             "--resource", "https://graph.microsoft.com",
             "--output", "json"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            _err = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown"
            print(f"[msgraph] az CLI auth failed: {_err}", file=sys.stderr)
            return None
        data = json.loads(result.stdout)
        access_token = data.get("accessToken")
        if not access_token:
            return None
        # Build a token dict that matches what _save_token expects
        from datetime import timezone as _tz
        import time as _time
        return {
            "access_token":  access_token,
            "token_type":    "Bearer",
            "expires_in":    int(data.get("expiresOn") and
                                 (datetime.fromisoformat(data["expiresOn"].replace(" ", "T"))
                                  .replace(tzinfo=_tz.utc).timestamp() - _time.time())
                                 or 3600),
            "scope":         "Files.Read Mail.Read User.Read",
            "_artha_auth":   "azcli",
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _acquire_token_silent(client_id: str, token_data: dict, *, work: bool = False) -> Optional[dict]:
    """
    Attempt a silent refresh using the stored refresh_token.
    Returns a new token dict on success, None on failure.
    """
    import msal
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return None

    app    = _build_app(client_id, work=work)
    scopes = _WORK_SCOPES if work else _SCOPES
    # MSAL PublicClientApplication can refresh via acquire_token_by_refresh_token
    result = app.acquire_token_by_refresh_token(
        refresh_token=refresh_token,
        scopes=scopes,
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

    # 2. OS token record
    if _load_token() is None:
        print("  OS credential token:   ✗ MISSING; migrate legacy credentials or authenticate")
        print("\n  Action: run python scripts/setup_msgraph_oauth.py")
        sys.exit(1)
    print("  OS credential token:   ✓")

    # 3. Expiry
    token_data = _load_token()
    if token_data is None:
        print("  OS token record:      ✗ Unreadable")
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
        print("  ✗ No access token in the OS record")
        sys.exit(1)

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            profile = json.load(resp)
        email_raw = profile.get("mail") or profile.get("userPrincipalName", "unknown")
        # Mask for privacy: t***@example.com
        masked = email_raw if "@" not in email_raw else f"{email_raw[0]}***@{email_raw.split('@')[-1]}"
        print(f"  ✓ Connected as: {profile.get('displayName', 'unknown')} ({masked})")
    except Exception as exc:
        print(f"  ✗ Graph API call failed: {exc}")
        print("  Token may be expired — run: python scripts/setup_msgraph_oauth.py --reauth")
        sys.exit(1)

    print("\nMicrosoft Graph: OK")


# ---------------------------------------------------------------------------
# Proactive token refresh
# ---------------------------------------------------------------------------

def _token_lifetime(token: dict) -> float | None:
    if not isinstance(token.get('access_token'), str) or not token['access_token']:
        return None
    try:
        expiry_dt = datetime.fromisoformat(str(token.get('expiry', '')).replace('Z', '+00:00'))
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        return (expiry_dt - datetime.now(timezone.utc)).total_seconds()
    except ValueError:
        return None


def ensure_valid_token(warn_seconds: int = 300, *, work: bool = False) -> dict:
    """
    Return a valid token dict (proactively refresh if near expiry).
    Raises RuntimeError if no unexpired token can be confirmed after refresh.
    Called by todo_sync.py and preflight.py.
    """
    if type(warn_seconds) is not int or not 0 <= warn_seconds <= 86400:
        raise ValueError('refresh warning window must be a bounded nonnegative integer')
    token_data = _load_token(work=work)
    if token_data is None:
        raise RuntimeError(
            "Microsoft Graph token missing. Run: python scripts/setup_msgraph_oauth.py" + (' --work' if work else '')
        )

    # Check expiry
    secs_left = _token_lifetime(token_data)
    needs_refresh = secs_left is None or secs_left <= warn_seconds

    if needs_refresh:
        vault = _credential_store()
        name = 'msgraph.work' if work else 'msgraph.personal'
        with vault.refresh_lock(name):
            token_data, revision = vault.read_versioned(name)
            if token_data is None:
                raise RuntimeError('Microsoft Graph token freshness is unverified; authenticate again')
            secs_left = _token_lifetime(token_data)
            if secs_left is not None and secs_left > warn_seconds:
                return token_data
            # Client metadata may also be embedded in the same OS token record.
            client_id = _keychain_read(KC_WORK_CLIENT_ID if work else KC_CLIENT_ID) or token_data.get("_artha_client_id")
            if not client_id:
                print("[msgraph] ⚠ client_id missing from OS credentials — skipping refresh", file=sys.stderr)
            else:
                new_token = _acquire_token_silent(client_id, token_data, work=work)
                lifetime = new_token.get('expires_in') if isinstance(new_token, dict) else None
                if (isinstance(new_token, dict) and isinstance(new_token.get('access_token'), str)
                        and new_token['access_token'] and type(lifetime) in (int, float)
                        and 0 < lifetime <= 86400):
                    if not new_token.get('refresh_token') and token_data.get('refresh_token'):
                        new_token['refresh_token'] = token_data['refresh_token']
                    # --- Layer 2: Track refresh token usage (vm-hardening.md Phase 2.4) ---
                    new_token["_last_refresh_success"] = datetime.now(timezone.utc).isoformat()
                    _save_token(new_token, client_id=client_id, work=work, expected_revision=revision)
                    saved = _load_token(work=work)
                    remaining = _token_lifetime(saved) if saved else None
                    if remaining is not None and remaining > 0:
                        return saved
                    raise RuntimeError('Microsoft Graph token freshness is unverified; authenticate again')
                # Silent refresh failed — token may still work, return it
                print("[msgraph] ⚠ Silent refresh failed", file=sys.stderr)

    if secs_left is None or secs_left <= 0:
        raise RuntimeError('Microsoft Graph token freshness is unverified; authenticate again')

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
    parser.add_argument("--work", action="store_true",
                        help=(
                            "Authenticate with a work/school (AAD) account instead of "
                            "personal. Uses a separate app registration and writes to "
                            ".tokens/msgraph-work-token.json. Required for SharePoint."
                        ))
    parser.add_argument("--work-mcp", action="store_true",
                        help=(
                            "Acquire M365 MCP write tokens (Mail.ReadWrite, Calendars.ReadWrite, "
                            "User.Read, Tasks.ReadWrite, Chat.ReadWrite). Writes to "
                            ".tokens/msgraph-work-mcp-token.json. Required for M15 action types."
                        ))
    parser.add_argument("--health", action="store_true",
                        help="Check stored token status and connectivity")
    args = parser.parse_args()

    if args.health:
        run_health_check()
        return

    work = args.work

    # --work-mcp: acquire M365 MCP write tokens (M15 action types, C1/F11)
    if getattr(args, "work_mcp", False):
        print()
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║  M365 MCP Write Token Setup — Artha Work (M15)              ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        print()
        print("  Scopes:", ", ".join(_MCP_WORK_SCOPES))
        print("  Token storage: separate OS credential record for Work MCP")
        print()
        client_id = _keychain_read(KC_WORK_CLIENT_ID)
        if not client_id:
            print("Work client ID not found in credential store.")
            print("Run: python -c \"import keyring; "
                  f"keyring.set_password('{KC_WORK_CLIENT_ID}','artha','<CLIENT_ID>')\"")
            sys.exit(1)
        token = _acquire_token_interactive(client_id, work=True, mcp=True)
        if not token:
            print("✗ Token acquisition failed.")
            sys.exit(1)
        # Work MCP uses a separate OS credential record
        from datetime import timedelta as _td
        expires_in = token.get("expires_in", 3600)
        expiry_dt = datetime.now(timezone.utc).replace(microsecond=0) + _td(seconds=int(expires_in))
        mcp_data = {**token, "expiry": expiry_dt.isoformat(), "_artha_client_id": client_id}
        _credential_store().write("msgraph.work-mcp", mcp_data)
        print("\n✓ MCP write token saved to the OS credential store")
        return

    # Work flow — Microsoft Graph PowerShell (primary, no App Registration) or MSAL (advanced)
    if work:
        import shutil as _shutil
        import subprocess as _subp

        # ── Microsoft Graph PowerShell (Connect-MgGraph) ───────────────────────
        # Uses Microsoft's own pre-registered PS app (14d82eec-204b-4c2f-b7e8-296a70dab67e).
        # No custom App Registration needed. Caches tokens (MSAL/WAM) across sessions.
        # Works in the Microsoft corporate tenant.
        pwsh_cmd = _shutil.which("pwsh") or _shutil.which("powershell")
        if pwsh_cmd:
            # Check if already authenticated (silent Connect-MgGraph attempt)
            check_script = (
                'try { '
                'Import-Module Microsoft.Graph.Authentication -ErrorAction Stop; '
                'Connect-MgGraph -Scopes "Files.Read","Mail.Read","User.Read" '
                '-NoWelcome -ErrorAction Stop 2>$null | Out-Null; '
                '"authenticated" '
                '} catch { "unauthenticated" }'
            )
            try:
                chk = _subp.run(
                    [pwsh_cmd, "-NonInteractive", "-NoProfile", "-Command", check_script],
                    capture_output=True, text=True, timeout=20,
                )
                already_authed = chk.returncode == 0 and "authenticated" in chk.stdout
            except (OSError, _subp.TimeoutExpired):
                already_authed = False

            if not already_authed or args.reauth:
                print()
                print("╔══════════════════════════════════════════════════════════════╗")
                print("║  Microsoft Graph PowerShell — Device Code Login             ║")
                print("╚══════════════════════════════════════════════════════════════╝")
                print()
                print("  Running: Connect-MgGraph -UseDeviceAuthentication")
                print("  (No App Registration required — uses Microsoft's own PS app)")
                print()
                login_script = (
                    'Import-Module Microsoft.Graph.Authentication; '
                    'Connect-MgGraph -Scopes "Files.Read","Mail.Read","User.Read" '
                    '-UseDeviceAuthentication -NoWelcome'
                )
                try:
                    _subp.run(
                        [pwsh_cmd, "-NoProfile", "-Command", login_script],
                        check=True, timeout=300,
                    )
                    print("\n✓ Connect-MgGraph authentication successful.")
                except (_subp.CalledProcessError, _subp.TimeoutExpired) as exc:
                    print(f"\n✗ Connect-MgGraph failed: {exc}")
                    sys.exit(1)

            # Extract a snapshot token for the separate Work OS record.
            tok_script = (
                'Connect-MgGraph -Scopes "Files.Read","Mail.Read","User.Read" '
                '-NoWelcome 2>$null | Out-Null; '
                '$resp = Invoke-MgGraphRequest -Uri "https://graph.microsoft.com/v1.0/me" '
                '-OutputType HttpResponseMessage; '
                '$resp.RequestMessage.Headers.Authorization.Parameter'
            )
            try:
                tok_result = _subp.run(
                    [pwsh_cmd, "-NonInteractive", "-NoProfile", "-Command", tok_script],
                    capture_output=True, text=True, timeout=30,
                )
                if tok_result.returncode == 0:
                    raw_token = tok_result.stdout.strip()
                    if raw_token and len(raw_token) > 50:
                        snap = {"access_token": raw_token, "mggraph_managed": True}
                        _save_token(snap, work=True)
                        # Identity check
                        try:
                            import urllib.request as _req
                            req = _req.Request(
                                "https://graph.microsoft.com/v1.0/me",
                                headers={"Authorization": f"Bearer {raw_token}"},
                            )
                            with _req.urlopen(req, timeout=10) as resp:
                                profile = json.load(resp)
                            name = profile.get("displayName", "?")
                            upn  = profile.get("userPrincipalName", "?")
                            print(f"\n✓ Connected as: {name} ({upn})")
                        except Exception:
                            print("\n✓ Token snapshot saved (identity check skipped).")
                        print("  Token saved to the OS credential store")
                        print()
                        print("  SharePoint tools will call Connect-MgGraph silently at runtime.")
                        return
            except (OSError, _subp.TimeoutExpired):
                pass
            print("⚠  Token extraction failed. Run 'Connect-MgGraph' in PowerShell to refresh.")
            sys.exit(1)

        else:
            # PowerShell not found (unusual on Windows)
            print()
            print("╔══════════════════════════════════════════════════════════════╗")
            print("║  Work SharePoint Auth — PowerShell Required                 ║")
            print("╚══════════════════════════════════════════════════════════════╝")
            print()
            print("  PowerShell (pwsh) not found. Install from:")
            print("    https://aka.ms/powershell")
            print()
            print("  Then run once in PowerShell:")
            print("    Connect-MgGraph -Scopes 'Files.Read','Mail.Read','User.Read' -UseDeviceAuthentication")
            print()
            print("  Microsoft Graph PowerShell uses Microsoft's own pre-registered app.")
            print("  No custom App Registration required.")
            sys.exit(1)

        client_id  = None   # unreachable — all paths above return or sys.exit
        scopes     = _WORK_SCOPES

    else:
        client_id  = _keychain_read(KC_CLIENT_ID)
        scopes     = _SCOPES
        acct_label = "personal Microsoft account"
        port_label = "http://localhost:8400"
        kc_cmd     = f"keyring.set_password('{KC_CLIENT_ID}','artha','<CLIENT_ID>')"
        app_note   = (
            "     • Supported accounts: 'Personal Microsoft accounts only'\n"
            "     • Redirect URI: Mobile/desktop → http://localhost:8400"
        )

    if not client_id:
        print("╔══════════════════════════════════════════════════════════════╗")
        tier = "Work (AAD)" if work else "Personal"
        print(f"║  Microsoft Graph OAuth Setup — Artha [{tier:18s}]  ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        print()
        print("Client ID not found in credential store.")
        print("Complete the pre-requisites first:")
        print()
        print("  1. Go to https://portal.azure.com → App registrations → New registration")
        print(f"     • Name: 'Artha{' Work' if work else ' Personal'} Assistant'")
        print(app_note)
        print()
        print("  2. Copy the Application (client) ID")
        print()
        print("  3. Run:")
        print(f'     python -c "import keyring; {kc_cmd}"')
        print()
        print("  4. Re-run this script" + (" with --work" if work else "") + ".")
        sys.exit(1)

    # Check if token already exists (and not force-reauth)
    if not args.reauth:
        token_data = _load_token(work=work)
        if token_data and token_data.get("access_token"):
            label = "work" if work else "personal"
            print(f"[msgraph] {label.capitalize()} token already stored. Use --reauth to force a new flow.")
            print("          Storage: OS credential store")
            return

    if args.reauth:
        tier = "work" if work else "personal"
        print(f"[msgraph] Forcing new OAuth flow (--reauth, {tier})...")

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Microsoft Graph OAuth — Browser will open for login        ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("  This will request the following scopes:")
    for s in scopes:
        print(f"    • {s}")
    print()
    print(f"  Sign in with your {acct_label}.")
    print("  (The browser will open automatically in a moment...)")
    print()

    try:
        token_response = _acquire_token_interactive(client_id, work=work)
        _save_token(token_response, client_id=client_id, work=work)
        print()
        print("✓ Authentication successful!")
        print("  Token saved to the OS credential store")

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
        if work:
            print("Next step: run python scripts/sharepoint_kb_sync.py --dry-run --verbose")
        else:
            print("Next step: run python scripts/setup_todo_lists.py to create domain task lists.")

    except Exception as exc:
        print(f"\n✗ Authentication failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
