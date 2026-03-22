#!/usr/bin/env python3
"""
scripts/setup_discord.py — Interactive Discord bot integration setup wizard.

Stores the bot token in the system keyring under:
  service="artha-discord-bot-token", username="token"

Usage:
  python scripts/setup_discord.py              # full interactive setup
  python scripts/setup_discord.py --verify-only # check existing token
  python scripts/setup_discord.py --reset       # clear stored token + re-run
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_KEYRING_SERVICE = "artha-discord-bot-token"
_KEYRING_USERNAME = "token"
_CONFIG_YAML = Path(__file__).resolve().parent.parent / "config" / "channels.yaml"
_DISCORD_API_BASE = "https://discord.com/api/v10"


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
    """Return (ok, bot-username-or-error)."""
    url = f"{_DISCORD_API_BASE}/users/@me"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "ArthaBot/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
            username = data.get("username", "unknown")
            bot_id = data.get("id", "unknown")
            return True, f"Bot: {username} (ID: {bot_id})"
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "Invalid token (401 Unauthorized)"
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


def _get_gateway_info(token: str) -> Optional[str]:
    """Fetch Gateway bot info to confirm intent support."""
    url = f"{_DISCORD_API_BASE}/gateway/bot"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bot {token}", "User-Agent": "ArthaBot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
            shards = data.get("shards", 1)
            sessions_remaining = data.get("session_start_limit", {}).get("remaining", "?")
            return f"{shards} shard(s), {sessions_remaining} session(s) remaining"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# channels.yaml update
# ---------------------------------------------------------------------------

def _update_channels_yaml() -> None:
    if not _CONFIG_YAML.exists():
        return
    content = _CONFIG_YAML.read_text()
    # Check if discord entry already has credential_key configured
    if "artha-discord-bot-token" in content:
        return  # Already configured
    # Look for the discord stub and update it
    discord_stub = "discord:"
    if discord_stub not in content:
        print("  [WARN] discord entry not found in config/channels.yaml — skipping update.")
        return
    # The stub likely has: credential_key: '' — fill it in
    old = "credential_key: ''"
    new = "credential_key: artha-discord-bot-token"
    updated = content.replace(old, new, 1)
    if updated != content:
        _CONFIG_YAML.write_text(updated)
        print("  Updated config/channels.yaml with Discord credential key.")
    else:
        print("  [INFO] channels.yaml already configured or has different format.")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def _print_banner() -> None:
    print()
    print("=" * 60)
    print("  Artha × Discord Setup Wizard")
    print("=" * 60)
    print()


def _print_instructions() -> None:
    print("To create a Discord bot and get its token:")
    print("  1. Go to https://discord.com/developers/applications")
    print("  2. New Application → give it a name (e.g. Artha)")
    print("  3. Bot tab → Add Bot → Reset Token → Copy token")
    print("  4. Enable: SERVER MEMBERS INTENT, MESSAGE CONTENT INTENT")
    print("  5. OAuth2 → Bot → Permissions: Read Messages, Send Messages, Attach Files")
    print("  6. Invite the bot to your server using the generated URL")
    print()
    print("Required Gateway Intents (in Bot settings):")
    print("  - Message Content Intent (privileged)")
    print("  - Server Members Intent (for DMs)")
    print()


def run_verify_only() -> int:
    token = _keyring_get()
    if not token:
        print("[ERROR] No Discord bot token found in keyring.")
        print("Run without --verify-only to set it up.")
        return 1
    print("Verifying stored Discord bot token…")
    ok, msg = _verify_token(token)
    if ok:
        print(f"  [OK] {msg}")
        gw = _get_gateway_info(token)
        if gw:
            print(f"  [OK] Gateway: {gw}")
        return 0
    else:
        print(f"  [FAIL] {msg}")
        return 1


def run_setup(reset: bool = False) -> int:
    _print_banner()

    if reset:
        print("Clearing stored Discord bot token…")
        _keyring_delete()
        print("  Done.")
        print()

    # Check for existing token
    existing = _keyring_get()
    if existing and not reset:
        print("A Discord bot token is already stored. Verifying…")
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
    for attempt in range(3):
        token = getpass.getpass("Enter Discord bot token: ").strip()
        if not token:
            print("  Token cannot be empty.")
            continue
        if len(token) < 20:
            print("  Token looks too short — double-check.")
            continue

        print("Verifying token…")
        ok, msg = _verify_token(token)
        if ok:
            print(f"  [OK] {msg}")
            gw = _get_gateway_info(token)
            if gw:
                print(f"  [OK] Gateway: {gw}")
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

    # Optional: collect default channel ID
    print()
    channel_id = input(
        "Default channel ID for Artha→Discord messages (leave blank to skip): "
    ).strip()

    # Store token
    if _keyring_set(token):
        print("  Token stored securely in system keyring.")
    else:
        print("  [WARN] keyring unavailable — token NOT stored.")
        print("  Set ARTHA_DISCORD_BOT_TOKEN= in your environment as a fallback.")

    # Update channels.yaml
    _update_channels_yaml()

    if channel_id:
        print(f"  [INFO] Set discord.channel_id: '{channel_id}' in config/channels.yaml")

    print()
    print("═" * 60)
    print("  Discord bot integration configured!")
    print()
    print("  Next steps:")
    print("    1. In config/channels.yaml, set discord.enabled: true")
    if not channel_id:
        print("    2. Set discord.channel_id to your channel's Snowflake ID")
    print("    3. Run: python artha.py --channel discord --health-check")
    print("═" * 60)
    print()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Discord bot integration for Artha.",
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
