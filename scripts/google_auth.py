#!/usr/bin/env python3
"""
google_auth.py — Artha shared Google OAuth helper
==================================================
Credential storage layout:
  System credential store (cross-platform via `keyring` library):
    macOS → Keychain, Windows → Credential Manager
    artha / gmail-client-id       OAuth client ID
    artha / gmail-client-secret   OAuth client secret

  Local files (inside Artha folder) — OAuth tokens:
    {ARTHA_DIR}/.tokens/gmail-oauth-token.json
    {ARTHA_DIR}/.tokens/gcal-oauth-token.json

Tokens are stored as local files (not credential store) to avoid
access dialogs in headless/scripted contexts. They are
refreshable and easily revocable — not master credentials.

Usage (from other scripts):
  from google_auth import build_service
  gmail = build_service("gmail", "v1")
  calendar = build_service("calendar", "v3")

Ref: TS §3.1, T-1A.3.2, T-1A.3.3
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

import keyring

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

# ---------------------------------------------------------------------------
# OAuth scopes
# ---------------------------------------------------------------------------
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]
# Combined scope set for a single OAuth flow (simpler for initial setup)
ALL_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES

# Keychain service names (client credentials only)
_KC_CLIENT_ID     = "gmail-client-id"
_KC_CLIENT_SECRET = "gmail-client-secret"

# Local token file names (not Keychain)
_KC_GMAIL_TOKEN   = "gmail-oauth-token"
_KC_GCAL_TOKEN    = "gcal-oauth-token"

# Directory for local token files — inside Artha folder so OneDrive syncs them.
# ~/.artha-tokens is a symlink → this directory (backward compat for Mac terminal).
_ARTHA_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOKEN_DIR  = os.path.join(_ARTHA_DIR, ".tokens")


# Status flag file — lives in .tokens/ so it syncs across platforms via OneDrive
_STATUS_FILE = os.path.join(_TOKEN_DIR, "oauth-status.json")

# Client-credential mirror file — keyring is platform-local, this file syncs via OneDrive
# so running setup_google_oauth on one machine makes creds available everywhere.
_CLIENT_CREDS_FILE = os.path.join(_TOKEN_DIR, "google-client-creds.json")


def _status_get(key: str) -> bool:
    """Read a boolean flag from the non-blocking status file."""
    try:
        with open(_STATUS_FILE) as f:
            return json.load(f).get(key, False)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def _status_set(key: str, value: bool = True) -> None:
    """Persist a boolean flag to the status file."""
    try:
        try:
            with open(_STATUS_FILE) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data[key] = value
        with open(_STATUS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # Non-fatal — status file is best-effort


# ---------------------------------------------------------------------------
# Credential store helpers (cross-platform via keyring)
# macOS → Keychain, Windows → Credential Manager
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


def _keychain_read(service: str, account: str = "artha") -> Optional[str]:
    """Read a secret from the system credential store, falling back to synced file."""
    try:
        val = keyring.get_password(service, account)
        if val:
            return val
    except Exception:
        pass
    # Fall back to the OneDrive-synced mirror file for client creds
    if service in (_KC_CLIENT_ID, _KC_CLIENT_SECRET):
        file_val = _read_client_creds_file(service)
        if file_val:
            return file_val
        # Last resort: extract from existing token files (they embed client creds)
        token_key = "client_id" if service == _KC_CLIENT_ID else "client_secret"
        for token_name in (_KC_GMAIL_TOKEN, _KC_GCAL_TOKEN):
            try:
                with open(_token_path(token_name)) as f:
                    val = json.load(f).get(token_key)
                    if val:
                        return val
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass
    return None


def _keychain_write(service: str, value: str, account: str = "artha") -> None:
    """Write a secret to the system credential store and mirror to synced file."""
    try:
        keyring.set_password(service, account, value)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to write credential for service='{service}': {exc}"
        )
    # Mirror client creds to synced file so the other platform can read them
    if service in (_KC_CLIENT_ID, _KC_CLIENT_SECRET):
        _mirror_client_cred(service, value)
    # Update status flag file (no credential-store read needed later)
    _status_set(service, True)


def _keychain_delete(service: str, account: str = "artha") -> None:
    """Delete a credential store entry and clear flag file entry."""
    try:
        keyring.delete_password(service, account)
    except Exception:
        pass  # Entry may not exist — not an error
    _status_set(service, False)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _token_path(token_service: str) -> str:
    """Return the absolute path to a token JSON file."""
    return os.path.join(_TOKEN_DIR, f"{token_service}.json")


def _ensure_token_dir() -> None:
    """Create {ARTHA_DIR}/.tokens/ with restricted permissions if it doesn't exist."""
    if not os.path.exists(_TOKEN_DIR):
        os.makedirs(_TOKEN_DIR, mode=0o700, exist_ok=True)


def _load_token(token_service: str) -> Optional[Credentials]:
    """Load and return Credentials from local token file, or None if missing."""
    path = _token_path(token_service)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            info = json.load(f)
        creds = Credentials(
            token=info.get("token"),
            refresh_token=info.get("refresh_token"),
            token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=info.get("client_id"),
            client_secret=info.get("client_secret"),
            scopes=info.get("scopes"),
        )
        # Restore expiry so creds.expired / creds.valid work correctly without an API call.
        expiry_str = info.get("expiry", "")
        if expiry_str:
            try:
                from datetime import datetime as _dt, timezone as _tz
                expiry_dt = _dt.fromisoformat(expiry_str.rstrip("Z"))
                if expiry_dt.tzinfo is None:
                    expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
                creds.expiry = expiry_dt
            except (ValueError, AttributeError):
                pass  # bad stored value — will refresh on first use
        return creds
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        print(f"[google_auth] WARNING: Could not parse stored token: {exc}",
              file=sys.stderr)
        return None


def _save_token(creds: Credentials, token_service: str) -> None:
    """Persist Credentials to local token file (chmod 600)."""
    _ensure_token_dir()
    # Store expiry as UTC ISO string so preflight.py and validate_token_freshness()
    # can detect near-expiry without making an API call.
    expiry_str = ""
    if creds.expiry is not None:
        from datetime import timezone as _tz
        expiry_dt = creds.expiry
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
        expiry_str = expiry_dt.isoformat()
    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes) if creds.scopes else [],
        "expiry":        expiry_str,
    }
    path = _token_path(token_service)
    with open(path, "w") as f:
        json.dump(token_data, f, indent=2)
    if os.name != "nt":
        os.chmod(path, 0o600)
    _status_set(token_service, True)


def _get_client_config() -> dict:
    """Build the client_config dict from Keychain credentials."""
    client_id = _keychain_read(_KC_CLIENT_ID)
    client_secret = _keychain_read(_KC_CLIENT_SECRET)

    if not client_id:
        raise RuntimeError(
            "Gmail client_id not found in credential store.\n"
            "Run: python scripts/setup_google_oauth.py"
        )
    if not client_secret:
        raise RuntimeError(
            "Gmail client_secret not found in credential store.\n"
            "Run: python scripts/setup_google_oauth.py"
        )

    return {
        "installed": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _get_or_refresh_credentials(
    token_service: str,
    scopes: list[str],
    force_reauth: bool = False,
) -> Credentials:
    """
    Return valid Credentials for the given scope set.
    Flow:
      1. Load from Keychain
      2. If expired but has refresh_token → refresh silently
      3. If missing or un-refreshable → run interactive OAuth flow
      4. Persist updated token back to Keychain
    """
    creds = None if force_reauth else _load_token(token_service)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_service)
            return creds
        except Exception as exc:
            print(f"[google_auth] Token refresh failed ({exc}). Re-authenticating.",
                  file=sys.stderr)
            creds = None

    if creds and creds.valid:
        return creds

    # Need a fresh OAuth flow
    client_config = _get_client_config()
    flow = InstalledAppFlow.from_client_config(client_config, scopes)
    # run_local_server opens the browser and handles the redirect
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_token(creds, token_service)
    return creds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_gmail_credentials(force_reauth: bool = False) -> Credentials:
    """Return valid Gmail credentials (refreshes automatically if needed)."""
    return _get_or_refresh_credentials(_KC_GMAIL_TOKEN, GMAIL_SCOPES, force_reauth)


def get_calendar_credentials(force_reauth: bool = False) -> Credentials:
    """Return valid Calendar credentials (refreshes automatically if needed)."""
    return _get_or_refresh_credentials(_KC_GCAL_TOKEN, CALENDAR_SCOPES, force_reauth)


def build_service(api: str, version: str, force_reauth: bool = False) -> Resource:
    """
    Build and return an authenticated Google API service resource.

    Args:
        api:          "gmail" or "calendar"
        version:      "v1" (gmail) or "v3" (calendar)
        force_reauth: if True, discard cached token and re-run OAuth flow

    Returns:
        Authenticated googleapiclient Resource object
    """
    if api == "gmail":
        creds = get_gmail_credentials(force_reauth)
    elif api == "calendar":
        creds = get_calendar_credentials(force_reauth)
    else:
        raise ValueError(f"Unknown API '{api}'. Use 'gmail' or 'calendar'.")

    return build(api, version, credentials=creds, cache_discovery=False)


def revoke_tokens() -> None:
    """Delete stored OAuth token files (does NOT revoke at Google)."""
    for svc in (_KC_GMAIL_TOKEN, _KC_GCAL_TOKEN):
        path = _token_path(svc)
        if os.path.exists(path):
            os.remove(path)
        _status_set(svc, False)
    print("[google_auth] Stored tokens deleted.")


def check_stored_credentials() -> dict:
    """Return a status dict for /health checks.
    Checks both keyring and the OneDrive-synced mirror file."""
    return {
        "client_id_stored":     bool(_keychain_read(_KC_CLIENT_ID)),
        "client_secret_stored": bool(_keychain_read(_KC_CLIENT_SECRET)),
        # Token presence checked via local files
        "gmail_token_stored":   os.path.exists(_token_path(_KC_GMAIL_TOKEN)),
        "gcal_token_stored":    os.path.exists(_token_path(_KC_GCAL_TOKEN)),
    }


def validate_token_freshness(
    token_service: str,
    warn_seconds: int = 300,
    proactive_refresh: bool = True,
) -> dict:
    """
    Proactively check token health and optionally refresh before expiry.

    T-1A.11.4: Called by preflight.py and directly before API use so we never
    discover a stale token mid-operation.

    Args:
        token_service:     "gmail-oauth-token" or "gcal-oauth-token"
        warn_seconds:      seconds-before-expiry threshold for a warning (default 5 min)
        proactive_refresh: if True, attempt silent refresh when token is near/past expiry

    Returns dict:
        {
            "ok":             bool,
            "message":        str,
            "expires_in_sec": float | None,   # None if expiry unknown
            "refreshed":      bool,
        }
    """
    from datetime import datetime, timezone as _tz

    path = _token_path(token_service)
    if not os.path.exists(path):
        return {"ok": False, "message": "Token file missing", "expires_in_sec": None, "refreshed": False}

    creds = _load_token(token_service)
    if creds is None:
        return {"ok": False, "message": "Token file unreadable", "expires_in_sec": None, "refreshed": False}

    # --- Determine seconds until expiry ---
    expires_in: Optional[float] = None
    if creds.expiry is not None:
        expiry_dt = creds.expiry
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
        expires_in = (expiry_dt - datetime.now(_tz.utc)).total_seconds()

    # --- Proactive refresh if near/past expiry ---
    refreshed = False
    if proactive_refresh and creds.refresh_token:
        needs_refresh = (
            creds.expired
            or (expires_in is not None and expires_in < warn_seconds)
        )
        if needs_refresh:
            try:
                creds.refresh(Request())
                _save_token(creds, token_service)  # persists updated expiry
                refreshed = True
                # Recalculate expires_in after refresh
                if creds.expiry is not None:
                    expiry_dt = creds.expiry
                    if expiry_dt.tzinfo is None:
                        expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
                    expires_in = (expiry_dt - datetime.now(_tz.utc)).total_seconds()
            except Exception as exc:
                return {
                    "ok":             False,
                    "message":        f"Proactive refresh failed: {exc}",
                    "expires_in_sec": expires_in,
                    "refreshed":      False,
                }

    # --- Final status ---
    if expires_in is None:
        # No expiry stored — token is active but freshness unknown (will refresh on first API call)
        return {"ok": True, "message": "No expiry stored — will refresh on first use", "expires_in_sec": None, "refreshed": refreshed}

    if expires_in < 0:
        if not creds.refresh_token:
            return {"ok": False, "message": f"Token expired {int(-expires_in/60)}m ago, no refresh token", "expires_in_sec": expires_in, "refreshed": refreshed}
        # Still has refresh token — should recover on first use
        return {"ok": True, "message": f"Token expired but has refresh_token (will auto-refresh on first call)", "expires_in_sec": expires_in, "refreshed": refreshed}

    suffix = " (proactively refreshed)" if refreshed else ""
    return {
        "ok":             True,
        "message":        f"Valid for {int(expires_in / 60)}m{suffix}",
        "expires_in_sec": expires_in,
        "refreshed":      refreshed,
    }
