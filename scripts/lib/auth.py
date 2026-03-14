# pii-guard: ignore-file — auth module; tokens are in system credential store
"""
scripts/lib/auth.py — Unified auth token loader for Artha connectors.

Consolidates token loading across all providers so connector handlers don't
repeat the same token-load + refresh logic.

Supported auth methods:
  oauth2       — JSON token file (Google, Microsoft Graph)
  app_password — System keyring credential (iCloud, Fastmail, etc.)
  api_key      — System keyring API key (Canvas, custom providers)

Security guarantees:
  - All tokens loaded from system credential store (keyring) or files in .tokens/
  - Never reads credentials from environment variables or config files
  - Token files accessed read-only; writes only for refresh cache
  - No credentials are logged or included in exception messages

Ref: supercharge.md §5.6
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _artha_dir() -> Path:
    """Return the Artha project root (two levels up from scripts/lib/)."""
    return Path(__file__).parent.parent.parent.resolve()


def _token_path(relative_token_file: str) -> Path:
    """Resolve a token file path relative to ARTHA_DIR."""
    return _artha_dir() / relative_token_file


# ---------------------------------------------------------------------------
# OAuth2 (Google + Microsoft Graph)
# ---------------------------------------------------------------------------

def load_google_token(token_file: str = ".tokens/gmail-token.json") -> dict:
    """
    Load a Google OAuth2 token from disk and refresh if expired.

    Delegates to google_auth.build_service() which handles token state
    internally. Returns a dict with {service, token_path} for handlers that
    need the Google API service object.

    Raises RuntimeError if google_auth is not available or token is invalid.
    """
    scripts_dir = str(_artha_dir() / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from google_auth import build_service, check_stored_credentials  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"[auth] google_auth.py not found — run from Artha root directory: {exc}"
        ) from exc

    creds_ok, _ = check_stored_credentials()
    if not creds_ok:
        raise RuntimeError(
            "[auth] Google credentials not configured. "
            "Run: python scripts/setup_google_oauth.py"
        )
    return {"provider": "google", "scripts_dir": scripts_dir}


def load_msgraph_token(token_file: str = ".tokens/msgraph-token.json") -> dict:
    """
    Load and refresh a Microsoft Graph OAuth2 access token.

    Delegates to setup_msgraph_oauth.ensure_valid_token() which handles
    refresh internally.

    Returns dict with {access_token: str, ...}.
    Raises RuntimeError if token file is missing or refresh fails.
    """
    scripts_dir = str(_artha_dir() / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from setup_msgraph_oauth import ensure_valid_token  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"[auth] setup_msgraph_oauth.py not found: {exc}"
        ) from exc
    try:
        token_data = ensure_valid_token()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("[auth] MS Graph token data missing access_token field")
        return {"provider": "msgraph", "access_token": access_token}
    except Exception as exc:
        raise RuntimeError(
            f"[auth] Cannot obtain MS Graph token: {exc}\n"
            "Fix: python scripts/setup_msgraph_oauth.py"
        ) from exc


# ---------------------------------------------------------------------------
# App password (iCloud IMAP / CalDAV)
# ---------------------------------------------------------------------------

def load_icloud_credentials() -> dict:
    """
    Load Apple ID and app-specific password from system credential store.

    Delegates to setup_icloud_auth.ensure_valid_credentials().

    Returns dict with {apple_id: str, app_password: str}.
    Raises RuntimeError if credentials are not configured.
    """
    scripts_dir = str(_artha_dir() / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from setup_icloud_auth import ensure_valid_credentials  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"[auth] setup_icloud_auth.py not found: {exc}"
        ) from exc
    try:
        apple_id, app_password = ensure_valid_credentials()
        return {
            "provider": "icloud",
            "apple_id": apple_id,
            "app_password": app_password,
        }
    except Exception as exc:
        raise RuntimeError(
            f"[auth] Cannot load iCloud credentials: {exc}\n"
            "Fix: python scripts/setup_icloud_auth.py"
        ) from exc


# ---------------------------------------------------------------------------
# API key (Canvas LMS, generic services)
# ---------------------------------------------------------------------------

def load_api_key(credential_key: str, service_name: str = "artha") -> str:
    """
    Load an API key from the system credential store.

    Args:
        credential_key: The keyring service name (e.g. "artha-canvas-token-parth")
        service_name: The keyring account name (default: "artha")

    Returns the API key string.
    Raises RuntimeError if the key is not in the credential store.
    """
    try:
        import keyring  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"[auth] keyring not installed: {exc}. Run: pip install keyring"
        ) from exc
    value = keyring.get_password(credential_key, service_name)
    if not value:
        raise RuntimeError(
            f"[auth] API key '{credential_key}' not found in system credential store.\n"
            f"Store it with: python -c \"import keyring; "
            f"keyring.set_password('{credential_key}', 'artha', 'YOUR_KEY')\""
        )
    return value


# ---------------------------------------------------------------------------
# Generic dispatcher — reads connector config and dispatches to right loader
# ---------------------------------------------------------------------------

def load_auth_context(connector_config: dict) -> dict:
    """
    Load auth context for a connector based on its config block.

    Args:
        connector_config: The connector's YAML config dict (from connectors.yaml)

    Returns a dict suitable for passing as `auth_context` to handler.fetch().

    Raises RuntimeError with a user-actionable message on any auth failure.
    """
    auth_cfg = connector_config.get("auth", {})
    method = auth_cfg.get("method", "")
    provider = connector_config.get("provider", "")

    if method == "oauth2":
        if provider == "google":
            return load_google_token(auth_cfg.get("token_file", ".tokens/gmail-token.json"))
        elif provider == "microsoft":
            return load_msgraph_token(auth_cfg.get("token_file", ".tokens/msgraph-token.json"))
        else:
            raise RuntimeError(f"[auth] Unknown OAuth2 provider: {provider!r}")

    elif method == "app_password":
        if provider in ("imap", "icloud", "caldav"):
            return load_icloud_credentials()
        else:
            # Generic app password: load from keyring
            cred_key = auth_cfg.get("credential_key", "")
            if not cred_key:
                raise RuntimeError(
                    f"[auth] connector {connector_config.get('description', '')!r} "
                    "is missing auth.credential_key"
                )
            password = load_api_key(cred_key)
            return {"provider": provider, "password": password}

    elif method == "api_key":
        # Canvas and similar: key is per-item (e.g. per-child), loaded by handler
        return {"provider": provider, "method": "api_key"}

    else:
        raise RuntimeError(
            f"[auth] Unknown auth method: {method!r} "
            f"(valid: oauth2, app_password, api_key)"
        )
