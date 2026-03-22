#!/usr/bin/env python3
"""
scripts/setup_todoist.py — Interactive Todoist integration setup wizard.

Stores the API token in the system keyring under:
  service="artha-todoist", username="token"

Usage:
  python scripts/setup_todoist.py              # full interactive setup
  python scripts/setup_todoist.py --verify-only # check existing token
  python scripts/setup_todoist.py --reset       # clear stored token + re-run
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
import json
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_KEYRING_SERVICE = "artha-todoist"
_KEYRING_USERNAME = "token"
_CONFIG_YAML = Path(__file__).resolve().parent.parent / "config" / "connectors.yaml"
_TODOIST_REST_BASE = "https://api.todoist.com/rest/v2"


# ---------------------------------------------------------------------------
# Keyring helpers
# ---------------------------------------------------------------------------

def _keyring_get() -> Optional[str]:
    try:
        import keyring  # type: ignore[import]
        val = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        return val if val else None
    except Exception:
        return None


def _keyring_set(token: str) -> bool:
    try:
        import keyring  # type: ignore[import]
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, token)
        return True
    except Exception:
        return False


def _keyring_delete() -> None:
    try:
        import keyring  # type: ignore[import]
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _verify_token(token: str) -> tuple[bool, str]:
    """Return (ok, user_display_name_or_error)."""
    url = f"{_TODOIST_REST_BASE}/projects"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "Artha/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
            project_count = len(data) if isinstance(data, list) else 0
            return True, f"OK — {project_count} project(s) found"
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "Invalid token (401 Unauthorized)"
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# connectors.yaml update
# ---------------------------------------------------------------------------

def _update_connectors_yaml(enabled: bool = False) -> None:
    if not _CONFIG_YAML.exists():
        return
    content = _CONFIG_YAML.read_text()
    # Check if todoist entry already exists
    if "todoist" in content:
        return  # Already present; don't overwrite
    todoist_block = (
        "\n  todoist:\n"
        "    enabled: false  # Set to true after setup\n"
        "    cadence: every_4_hours\n"
        "    auth_context: keyring:artha-todoist:token\n"
        "    max_results: 100\n"
        "    # project_filter: []  # Optional: list of project names to sync\n"
    )
    content = content.rstrip() + todoist_block + "\n"
    _CONFIG_YAML.write_text(content)
    print("  Updated config/connectors.yaml with todoist entry.")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def _print_banner() -> None:
    print()
    print("=" * 60)
    print("  Artha × Todoist Setup Wizard")
    print("=" * 60)
    print()


def _print_instructions() -> None:
    print("To get your Todoist API token:")
    print("  1. Log in at https://app.todoist.com")
    print("  2. Settings → Integrations → Developer → API Token")
    print("  3. Copy the token (40 hex chars)")
    print()


def run_verify_only() -> int:
    token = _keyring_get()
    if not token:
        print("[ERROR] No Todoist token found in keyring.")
        print("Run without --verify-only to set it up.")
        return 1
    print("Verifying stored Todoist token…")
    ok, msg = _verify_token(token)
    if ok:
        print(f"  [OK] {msg}")
        return 0
    else:
        print(f"  [FAIL] {msg}")
        return 1


def run_setup(reset: bool = False) -> int:
    _print_banner()

    if reset:
        print("Clearing stored Todoist token…")
        _keyring_delete()
        print("  Done.")
        print()

    # Check for existing token
    existing = _keyring_get()
    if existing and not reset:
        print("A Todoist token is already stored. Verifying…")
        ok, msg = _verify_token(existing)
        if ok:
            print(f"  [OK] {msg}")
            print()
            answer = input("Token is valid. Re-configure anyway? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print("Setup skipped — using existing token.")
                return 0
        else:
            print(f"  [FAIL] Stored token is invalid: {msg}")
            print("  Proceeding to re-configure…")
        print()

    _print_instructions()

    # Prompt for token
    import getpass  # noqa: PLC0415
    for attempt in range(3):
        token = getpass.getpass("Enter Todoist API token: ").strip()
        if not token:
            print("  Token cannot be empty.")
            continue
        if len(token) < 8:
            print("  Token looks too short — double-check.")
            continue

        print("Verifying token…")
        ok, msg = _verify_token(token)
        if ok:
            print(f"  [OK] {msg}")
            break
        else:
            print(f"  [FAIL] {msg}")
            if attempt < 2:
                retry = input("Try again? [Y/n]: ").strip().lower()
                if retry in ("n", "no"):
                    return 1
    else:
        print("Too many failed attempts. Exiting.")
        return 1

    # Store token
    if _keyring_set(token):
        print("  Token stored securely in system keyring.")
    else:
        print("  [WARN] keyring unavailable — token NOT stored.")
        print("  Set ARTHA_TODOIST_TOKEN= in your environment as a fallback.")

    # Update connectors.yaml
    _update_connectors_yaml()

    print()
    print("═" * 60)
    print("  Todoist integration configured!")
    print()
    print("  Next steps:")
    print("    1. In config/connectors.yaml, set todoist.enabled: true")
    print("    2. Optionally restrict to specific projects via project_filter")
    print("    3. Run: python artha.py --skill todoist_sync --dry-run")
    print("═" * 60)
    print()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Todoist integration for Artha.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check stored token without re-configuring",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear stored token and re-run full setup",
    )
    args = parser.parse_args()

    if args.verify_only:
        sys.exit(run_verify_only())
    else:
        sys.exit(run_setup(reset=args.reset))


if __name__ == "__main__":
    main()
