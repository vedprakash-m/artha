#!/usr/bin/env python3
"""
google_auth.py — Artha shared Google OAuth helper
==================================================
Credential storage layout:
  System credential store (cross-platform via `keyring` library):
    macOS → Keychain, Windows → Credential Manager
    artha / gmail-client-id       OAuth client ID
    artha / gmail-client-secret   OAuth client secret

  OAuth tokens: verified, bounded records in the platform credential store
    (lib.credentials). No secret file or implicit legacy-file fallback.

Legacy .tokens JSON is input to the explicit credential migration only. A missing
or locked OS credential store blocks authentication rather than widening access.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict


class CredentialStatus(TypedDict):
    """Return type of check_stored_credentials()."""
    client_id_stored: bool
    client_secret_stored: bool
    gmail_token_stored: bool
    gcal_token_stored: bool
    gdrive_token_stored: bool


from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

from catchup_core.session_lock import default_installation_dir, installation_fence

# ---------------------------------------------------------------------------
# OAuth scopes
# ---------------------------------------------------------------------------
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
GMAIL_READ_ONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]
CALENDAR_READ_ONLY_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
]
# Combined scope set for a single OAuth flow (simpler for initial setup)
ALL_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES + DRIVE_SCOPES
_TOKEN_URI = 'https://oauth2.googleapis.com/token'

# Keychain service names (client credentials only)
_KC_CLIENT_ID     = "gmail-client-id"
_KC_CLIENT_SECRET = "gmail-client-secret"

# Stable token record identifiers (legacy file names are migration inputs only).
_KC_GMAIL_TOKEN   = "gmail-oauth-token"
_KC_GCAL_TOKEN    = "gcal-oauth-token"
_KC_GDRIVE_TOKEN  = "gdrive-oauth-token"

# Legacy paths identify migration inputs only. Normal reads and writes use the OS credential store.
_ARTHA_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOKEN_DIR  = os.path.join(_ARTHA_DIR, ".tokens")


# Status flag file — lives in .tokens/ so it syncs across platforms via OneDrive
_STATUS_FILE = os.path.join(_TOKEN_DIR, "oauth-status.json")

# Legacy mirror path; never consulted by normal authentication.
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

def _credential_store():
    from lib.credentials import CredentialStore
    return CredentialStore()


def _client_backend():
    from lib.credentials import system_keyring
    return system_keyring()


def _keychain_read(service: str, account: str = "artha") -> Optional[str]:
    """Read only the OS store; legacy synced mirrors require explicit migration."""
    try:
        value = _client_backend().get_password(service, account)
    except Exception:
        raise RuntimeError("OS credential store unavailable; no plaintext fallback") from None
    return value if isinstance(value, str) and value else None


def _keychain_write(service: str, value: str, account: str = "artha") -> None:
    """Write and verify a client credential in the OS store."""
    import uuid
    from catchup_core.recovery_registry import mutation, profile_for

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
        # Update status flag file (no credential-store read needed later)
        _status_set(service, True)


def _keychain_delete(service: str, account: str = "artha") -> None:
    """Delete a credential store entry and clear flag file entry."""
    import uuid
    from catchup_core.recovery_registry import mutation, profile_for

    with installation_fence(default_installation_dir()), mutation(
            profile_for(service, account), 'client', service, account, uuid.uuid4().hex, deleted=True):
        try:
            backend = _client_backend()
            if backend.get_password(service, account) is not None:
                backend.delete_password(service, account)
            if backend.get_password(service, account) is not None:
                raise RuntimeError('deletion unverified')
        except Exception:
            raise RuntimeError('OS client credential deletion could not be confirmed') from None
        _status_set(service, False)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def load_stored_token(token_service: str) -> Optional[dict]:
    """Read a token record without refresh, network access or plaintext-file fallback."""
    return _credential_store().read("google." + token_service)


def _load_token(token_service: str) -> Optional[Credentials]:
    """Load Credentials from the verified platform credential store."""
    return _credentials_from_info(load_stored_token(token_service))


def _credentials_from_info(info: dict | None) -> Optional[Credentials]:
    if info is None:
        return None
    # Imported metadata must never redirect a refresh grant to another endpoint.
    if info.get('token_uri', _TOKEN_URI) != _TOKEN_URI:
        return None
    try:
        creds = Credentials(
            token=info.get("token"),
            refresh_token=info.get("refresh_token"),
            token_uri=_TOKEN_URI,
            client_id=info.get("client_id"),
            client_secret=info.get("client_secret"),
            scopes=info.get("scopes"),
        )
        # Restore expiry so creds.expired / creds.valid work correctly without an API call.
        try:
            expiry_dt = datetime.fromisoformat(info.get('expiry', '').replace('Z', '+00:00'))
            if expiry_dt.tzinfo is not None:
                expiry_dt = expiry_dt.astimezone(timezone.utc).replace(tzinfo=None)
            # google-auth compares expiry to naive UTC, including in before_request.
            creds.expiry = expiry_dt
        except (ValueError, AttributeError, TypeError):
            # None means "never expires" to google-auth. Unknown freshness instead
            # requires silent refresh; an absent refresh grant then fails closed.
            creds.expiry = datetime(1970, 1, 1)
        return creds
    except (ValueError, TypeError):
        print('[google_auth] Stored token metadata is invalid', file=sys.stderr)
        return None


def _save_token(creds: Credentials, token_service: str, *, expected_revision: str | None = None) -> None:
    """Persist Credentials to the verified platform credential store."""
    # Store expiry as UTC ISO string so preflight.py and validate_token_freshness()
    # can detect near-expiry without making an API call.
    expiry_str = ""
    if creds.expiry is not None:
        from datetime import timezone as _tz
        expiry_dt = creds.expiry
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
        expiry_str = expiry_dt.isoformat()
    scopes = creds.granted_scopes if creds.granted_scopes is not None else creds.scopes
    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri or _TOKEN_URI,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(scopes) if scopes is not None else [],
        "expiry":        expiry_str,
    }
    _credential_store().write("google." + token_service, token_data, expected_revision=expected_revision)
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


def _seconds_left(creds: Credentials) -> float | None:
    if creds.expiry is None:
        return None
    expiry = creds.expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return (expiry - datetime.now(timezone.utc)).total_seconds()


def _refresh_existing(token_service: str, warn_seconds: int = 0) -> tuple[Credentials, bool]:
    """Only one process spends a refresh grant; stale results cannot undo login or revocation."""
    def fresh(creds: Credentials | None) -> bool:
        seconds = _seconds_left(creds) if creds else None
        return bool(creds and creds.valid and seconds is not None and seconds > warn_seconds)
    creds = _load_token(token_service)
    if fresh(creds):
        return creds, False
    vault = _credential_store()
    name = 'google.' + token_service
    try:
        with vault.refresh_lock(name):
            info, revision = vault.read_versioned(name)
            creds = _credentials_from_info(info)
            if fresh(creds):
                return creds, False
            if creds and creds.refresh_token:
                transport = Request()
                def request(*args, **kwargs):
                    kwargs['timeout'] = 10
                    return transport(*args, **kwargs)
                creds.refresh(request)
                if creds.expiry is not None and creds.valid:
                    _save_token(creds, token_service, expected_revision=revision)
                    return creds, True
    except Exception:
        raise RuntimeError('Google credential refresh unavailable or changed; retry or run python scripts/setup_google_oauth.py') from None
    raise RuntimeError('Google token freshness is unverified. Run: python scripts/setup_google_oauth.py')


def _require_read_only_scopes(creds: Credentials | None, scopes: list[str]) -> None:
    granted = creds.granted_scopes if creds is not None else None
    if granted is None:
        granted = creds.scopes if creds is not None else None
    if granted is None or set(granted) != set(scopes):
        raise RuntimeError(
            "Google read-only credential scopes do not match. "
            "Run: python scripts/setup_google_oauth.py --read-only"
        )


def _get_or_refresh_credentials(
    token_service: str,
    scopes: list[str],
    force_reauth: bool = False,
    *,
    read_only: bool = False,
) -> Credentials:
    """
    Return valid Credentials for the given scope set.
    Flow:
      1. Load from Keychain
      2. If expired but has refresh_token → refresh silently
      3. If missing or un-refreshable → require explicit setup
         (interactive OAuth runs only with force_reauth=True)
      4. Persist updated token back to Keychain
    """
    if not force_reauth:
        if read_only:
            _require_read_only_scopes(_load_token(token_service), scopes)
        creds = _refresh_existing(token_service)[0]
        if read_only:
            _require_read_only_scopes(creds, scopes)
        return creds

    # Need a fresh OAuth flow
    client_config = _get_client_config()
    flow = InstalledAppFlow.from_client_config(client_config, scopes)
    # run_local_server opens the browser and handles the redirect
    creds = flow.run_local_server(port=0, open_browser=True)
    if creds.expiry is None or not creds.valid:
        raise RuntimeError('Google setup did not return a verified fresh token')
    if read_only:
        _require_read_only_scopes(creds, scopes)
    _save_token(creds, token_service)
    return creds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_gmail_credentials(force_reauth: bool = False, *, read_only: bool = False) -> Credentials:
    """Return valid Gmail credentials (refreshes automatically if needed)."""
    scopes = GMAIL_READ_ONLY_SCOPES if read_only else GMAIL_SCOPES
    return _get_or_refresh_credentials(_KC_GMAIL_TOKEN, scopes, force_reauth, read_only=read_only)


def get_calendar_credentials(force_reauth: bool = False, *, read_only: bool = False) -> Credentials:
    """Return valid Calendar credentials (refreshes automatically if needed)."""
    scopes = CALENDAR_READ_ONLY_SCOPES if read_only else CALENDAR_SCOPES
    return _get_or_refresh_credentials(_KC_GCAL_TOKEN, scopes, force_reauth, read_only=read_only)


def get_drive_credentials(force_reauth: bool = False) -> Credentials:
    """Return valid Drive credentials (refreshes automatically if needed)."""
    return _get_or_refresh_credentials(_KC_GDRIVE_TOKEN, DRIVE_SCOPES, force_reauth)


def build_service(api: str, version: str, force_reauth: bool = False, *, read_only: bool = False) -> Resource:
    """
    Build and return an authenticated Google API service resource.

    Args:
        api:          "gmail" or "calendar" or "drive"
        version:      "v1" (gmail) or "v3" (calendar, drive)
        force_reauth: if True, discard cached token and re-run OAuth flow
        read_only:    require exact read-only scopes; Gmail and Calendar only

    Returns:
        Authenticated googleapiclient Resource object
    """
    if read_only and api not in {"gmail", "calendar"}:
        raise ValueError("Google read-only access supports only 'gmail' and 'calendar'.")
    if api == "gmail":
        creds = get_gmail_credentials(force_reauth, read_only=read_only)
    elif api == "calendar":
        creds = get_calendar_credentials(force_reauth, read_only=read_only)
    elif api == "drive":
        creds = get_drive_credentials(force_reauth)
    else:
        raise ValueError(f"Unknown API '{api}'. Use 'gmail', 'calendar', or 'drive'.")

    return build(api, version, credentials=creds, cache_discovery=False)


def revoke_tokens() -> None:
    """Revoke local token access in the OS store (does NOT revoke at Google)."""
    for svc in (_KC_GMAIL_TOKEN, _KC_GCAL_TOKEN, _KC_GDRIVE_TOKEN):
        _credential_store().delete("google." + svc)
        _status_set(svc, False)
    print("[google_auth] Stored tokens deleted.")


def check_stored_credentials() -> CredentialStatus:
    """Return a status dict for /health checks.
    Checks only verified OS credential records."""
    return {
        "client_id_stored":     bool(_keychain_read(_KC_CLIENT_ID)),
        "client_secret_stored": bool(_keychain_read(_KC_CLIENT_SECRET)),
        # A synced legacy file never establishes a configured token.
        "gmail_token_stored":   load_stored_token(_KC_GMAIL_TOKEN) is not None,
        "gcal_token_stored":    load_stored_token(_KC_GCAL_TOKEN) is not None,
        "gdrive_token_stored":  load_stored_token(_KC_GDRIVE_TOKEN) is not None,
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

    creds = _load_token(token_service)
    if creds is None:
        return {"ok": False, "message": "OS credential token missing or invalid; migrate legacy credentials or authenticate", "expires_in_sec": None, "refreshed": False}

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
                creds, refreshed = _refresh_existing(token_service, warn_seconds)
                expires_in = _seconds_left(creds)
            except Exception:
                return {
                    "ok":             False,
                    "message":        "Proactive refresh could not be confirmed; run python scripts/setup_google_oauth.py",
                    "expires_in_sec": expires_in,
                    "refreshed":      False,
                }

    # --- Final status ---
    if expires_in is None:
        return {"ok": False, "message": "Token freshness unverified; authenticate again", "expires_in_sec": None, "refreshed": refreshed}

    if expires_in < 0:
        if not creds.refresh_token:
            return {"ok": False, "message": f"Token expired {int(-expires_in/60)}m ago, no refresh token", "expires_in_sec": expires_in, "refreshed": refreshed}
        return {"ok": False, "message": "Token expired; silent refresh is required before use", "expires_in_sec": expires_in, "refreshed": refreshed}

    suffix = " (proactively refreshed)" if refreshed else ""
    return {
        "ok":             True,
        "message":        f"Valid for {int(expires_in / 60)}m{suffix}",
        "expires_in_sec": expires_in,
        "refreshed":      refreshed,
    }
