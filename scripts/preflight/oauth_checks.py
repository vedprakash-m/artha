"""preflight/oauth_checks.py — OAuth token validation and proactive refresh checks."""
from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from pathlib import Path

from preflight._types import (
    ARTHA_DIR, SCRIPTS_DIR, TOKEN_DIR, TOKEN_EXPIRY_WARN_SECONDS,
    _SUBPROCESS_ENV, _rel, CheckResult,
)

def check_oauth_token(service_name: str, token_filename: str) -> CheckResult:
    """Verify a Google OAuth token file exists and has required fields."""
    token_path = os.path.join(TOKEN_DIR, token_filename)
    check_name = f"{service_name} OAuth token"

    if not os.path.exists(token_path):
        return CheckResult(
            check_name, "P0", False,
            f"Token file missing: {_rel(token_path)}",
            fix_hint="Run: python scripts/setup_google_oauth.py",
        )

    try:
        with open(token_path) as f:
            token_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult(
            check_name, "P0", False,
            f"Token file unreadable or corrupt: {exc}",
            fix_hint="Run: python scripts/setup_google_oauth.py",
        )

    # Verify required fields are present
    required = ["refresh_token", "token_uri", "client_id", "client_secret"]
    missing  = [f for f in required if not token_data.get(f)]
    if missing:
        return CheckResult(
            check_name, "P0", False,
            f"Token file missing fields: {missing}",
            fix_hint="Re-authenticate: python scripts/setup_google_oauth.py",
        )

    # Permissions check (POSIX only — no-op on Windows)
    if os.name != "nt":
        stat = os.stat(token_path)
        perms = oct(stat.st_mode)[-3:]
        if perms != "600":
            os.chmod(token_path, 0o600)  # auto-fix permissions

    return CheckResult(check_name, "P0", True, f"Token file valid ✓ ({_rel(token_path)})")


def check_token_freshness(service_name: str, token_filename: str) -> CheckResult:
    """P1: Warn if token is close to expiry. Proactively refresh via google_auth."""
    check_name = f"{service_name} token freshness"
    token_path = os.path.join(TOKEN_DIR, token_filename)

    if not os.path.exists(token_path):
        return CheckResult(check_name, "P1", False, "Token file missing — skip freshness check")

    # Derive the token_service name from the filename (strip .json extension)
    token_service = token_filename.replace(".json", "")

    try:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from google_auth import validate_token_freshness
        result = validate_token_freshness(
            token_service=token_service,
            warn_seconds=TOKEN_EXPIRY_WARN_SECONDS,
            proactive_refresh=True,
        )
    except Exception as exc:
        return CheckResult(check_name, "P1", True, f"Could not check token freshness ({exc}) — OK ✓")

    msg = result["message"]
    expires_in = result.get("expires_in_sec")
    refreshed   = result.get("refreshed", False)

    if result["ok"]:
        suffix = " (just refreshed)" if refreshed else " ✓"
        return CheckResult(check_name, "P1", True, f"{msg}{suffix}")

    # Not ok — surface the problem as a P1 warning
    fix = (
        "Run: python scripts/setup_google_oauth.py --reauth"
        if expires_in is not None and expires_in < -3600  # expired over an hour ago
        else None
    )
    return CheckResult(check_name, "P1", False, msg, fix_hint=fix)


def check_msgraph_token() -> CheckResult:
    """P1: Verify Microsoft Graph token exists, proactively refresh if near expiry.

    Three-layer fix (vm-hardening.md Phase 2.4):
      1. Proactive refresh via ensure_valid_token() — parity with Google tokens
      2. Refresh token age tracking — 60-day warning before 90-day cliff
      3. Dual message when BOTH token expired AND network blocked

    This is P1 (not P0) because To Do sync is non-blocking — catch-up can
    complete without it. A missing token surfaces a clear actionable warning.
    """
    token_path = os.path.join(TOKEN_DIR, "msgraph-token.json")

    if not os.path.exists(token_path):
        return CheckResult(
            "Microsoft Graph token", "P1", False,
            "msgraph-token.json missing — Microsoft To Do sync disabled",
            fix_hint="Run: python scripts/setup_msgraph_oauth.py",
        )

    try:
        with open(token_path) as f:
            token_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            "Microsoft Graph token", "P1", False,
            "msgraph-token.json unreadable",
            fix_hint="Run: python scripts/setup_msgraph_oauth.py --reauth",
        )

    from datetime import datetime as _dt, timezone as _tz

    # --- Layer 2: Refresh token age tracking (warn at 60 days, cliff at 90) ---
    REFRESH_TOKEN_WARN_DAYS = 60
    last_refresh_str = token_data.get("_last_refresh_success", "")
    if last_refresh_str:
        try:
            last_dt = _dt.fromisoformat(last_refresh_str.rstrip("Z"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=_tz.utc)
            days_since_refresh = (_dt.now(_tz.utc) - last_dt).days
            if days_since_refresh > REFRESH_TOKEN_WARN_DAYS:
                return CheckResult(
                    "Microsoft Graph token", "P1", False,
                    f"⚠️ Refresh token last used {days_since_refresh}d ago — "
                    f"expires at 90d (cliff in ~{90 - days_since_refresh}d). "
                    f"Run catch-up from Mac to keep it alive.",
                    fix_hint="Run a full catch-up from Mac terminal to reset the 90-day refresh window",
                )
        except ValueError:
            pass

    # Check expiry field
    expiry_str = token_data.get("expiry", "")
    secs_left   = float("inf")
    if expiry_str:
        try:
            expiry_dt = _dt.fromisoformat(expiry_str.rstrip("Z"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
            secs_left = (expiry_dt - _dt.now(_tz.utc)).total_seconds()
        except ValueError:
            secs_left = float("inf")

    # --- Layer 1: Proactive refresh if near / past expiry (including already expired) ---
    # Attempt refresh whenever within warn window OR already expired (secs_left < 0).
    # Previously this branch was skipped when secs_left < 0 causing expired tokens
    # to fall straight to the error path without attempting auto-recovery.
    if secs_left < TOKEN_EXPIRY_WARN_SECONDS:
        refresh_succeeded = False
        try:
            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from setup_msgraph_oauth import ensure_valid_token
            refreshed = ensure_valid_token(warn_seconds=TOKEN_EXPIRY_WARN_SECONDS)
            new_expiry_str = refreshed.get("expiry", "")
            if new_expiry_str:
                new_dt = _dt.fromisoformat(new_expiry_str.rstrip("Z"))
                if new_dt.tzinfo is None:
                    new_dt = new_dt.replace(tzinfo=_tz.utc)
                new_secs = (new_dt - _dt.now(_tz.utc)).total_seconds()
                if new_secs > 0:
                    refresh_succeeded = True
                    return CheckResult(
                        "Microsoft Graph token", "P1", True,
                        f"Valid for {int(new_secs/60)}m ✓ (just refreshed)",
                    )
        except Exception:
            pass  # Fall through to expiry/network reporting

        # Refresh failed — report expiry AND possibly network block
        checks_to_return: list[CheckResult] = []

        if secs_left < 0:
            expired_msg = f"Token expired {int(-secs_left/60)}m ago"
        else:
            expired_msg = f"Token expires in {int(secs_left/60)}m"

        # --- Layer 3: Dual message — check network block ---
        network_blocked = False
        try:
            from detect_environment import detect as _detect_env
            manifest = _detect_env(skip_network=False)
            network_blocked = not manifest.capabilities.get("network_microsoft", True)
        except ImportError:
            pass  # detect_environment not available — skip network check

        if network_blocked:
            # Return two separate results: but CheckResult is a single value.
            # We embed both messages in one result with a dual-hint.
            return CheckResult(
                "Microsoft Graph token", "P1", False,
                f"{expired_msg} | graph.microsoft.com network-blocked in this environment",
                fix_hint=(
                    "Token: python scripts/setup_msgraph_oauth.py --reauth (run from Mac). "
                    "Network block: expected in Cowork VM — not fixable from this environment."
                ),
            )
        return CheckResult(
            "Microsoft Graph token", "P1", False,
            expired_msg,
            fix_hint="Run: python scripts/setup_msgraph_oauth.py --reauth",
        )

    if secs_left == float("inf"):
        return CheckResult(
            "Microsoft Graph token", "P1", True,
            "Token present (expiry unknown — will validate on use) ✓",
        )

    # 48h advance warning — prompts re-auth before the token expires mid-session
    TOKEN_ADVANCE_WARN_SECONDS = 172800  # 48 hours
    if 0 < secs_left < TOKEN_ADVANCE_WARN_SECONDS:
        hours_left = int(secs_left // 3600)
        return CheckResult(
            "Microsoft Graph token", "P1", True,
            f"Valid for {int(secs_left/60)}m ✓ — ⚠ expires in ~{hours_left}h, run setup_msgraph_oauth.py --reauth before next session",
        )

    return CheckResult(
        "Microsoft Graph token", "P1", True,
        f"Valid for {int(secs_left/60)}m ✓",
    )


