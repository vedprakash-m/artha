#!/usr/bin/env python3
"""
scripts/setup_slack.py — Interactive setup wizard for Slack integration.

Guides the user through:
  1. Creating a Slack App (displays App Manifest to copy-paste).
  2. Storing bot token (xoxb-…) in OS keyring.
  3. Optionally storing app-level token (xapp-…) for Socket Mode.
  4. Verifying tokens via auth.test.
  5. Sending a test DM.
  6. Enabling the connector in config/connectors.yaml.

Usage:
    python scripts/setup_slack.py
    python scripts/setup_slack.py --channel "#general"  # pre-set default channel
    python scripts/setup_slack.py --verify-only         # just re-verify existing tokens

Ref: specs/connect.md §7.6 (setup wizard)
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

_API_BASE = "https://slack.com/api"

# Keyring service + credential keys
_SERVICE = "artha"
_BOT_TOKEN_KEY = "artha-slack-bot-token"
_APP_TOKEN_KEY = "artha-slack-app-token"

# Slack App Manifest template (YAML format — user pastes into Slack App creation UI)
_APP_MANIFEST_YAML = textwrap.dedent("""
    display_information:
      name: Artha
      description: Personal Intelligence OS — Artha assistant bot
      background_color: "#1a1a2e"

    features:
      app_home:
        home_tab_enabled: false
        messages_tab_enabled: true
        messages_tab_read_only_enabled: false
      bot_user:
        display_name: Artha
        always_online: true

    oauth_config:
      scopes:
        bot:
          - channels:history
          - channels:read
          - chat:write
          - files:write
          - groups:history
          - groups:read
          - im:history
          - mpim:history
          - users:read

    settings:
      event_subscriptions:
        bot_events:
          - message.channels
          - message.groups
          - message.im
          - app_mention
      interactivity:
        is_enabled: true
      org_deploy_enabled: false
      socket_mode_enabled: true
      token_rotation_enabled: false
""").strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_header(text: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def _print_step(n: int, text: str) -> None:
    print(f"\n[Step {n}] {text}")
    print("-" * 50)


def _confirm(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question. Returns bool."""
    yn = "[Y/n]" if default else "[y/N]"
    reply = input(f"{prompt} {yn}: ").strip().lower()
    if not reply:
        return default
    return reply in ("y", "yes")


def _get_keyring():
    """Return the keyring module, or raise a helpful error."""
    try:
        import keyring
        return keyring
    except ImportError:
        print("\nERROR: keyring package is required.")
        print("Install with: pip install keyring")
        sys.exit(1)


def _verify_bot_token(token: str) -> dict | None:
    """Call auth.test with the given bot token. Returns response dict or None."""
    req = urllib.request.Request(
        f"{_API_BASE}/auth.test",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return data
        print(f"  ERROR: Slack returned error: {data.get('error', 'unknown')}")
        return None
    except urllib.error.HTTPError as exc:
        print(f"  ERROR: HTTP {exc.code} from auth.test")
        return None
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return None


def _verify_app_token(app_token: str) -> bool:
    """Call apps.connections.open to verify the app-level token. Returns True/False."""
    req = urllib.request.Request(
        f"{_API_BASE}/apps.connections.open",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return True
        print(f"  ERROR: Slack returned error: {data.get('error', 'unknown')}")
        return False
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return False


def _send_test_message(token: str, channel: str) -> bool:
    """Send a test DM/message to confirm send permissions."""
    payload = {
        "channel": channel,
        "text": (
            ":white_check_mark: *Artha Slack integration is working!*\n"
            "I can now send you notifications and briefings here."
        ),
        "mrkdwn": True,
    }
    req = urllib.request.Request(
        f"{_API_BASE}/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            print(f"  Test message sent to {channel}")
            return True
        print(f"  ERROR sending test message: {data.get('error', 'unknown')}")
        return False
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return False


def _enable_in_connectors_yaml(workspace_slug: str, channels: list[str]) -> None:
    """Update config/connectors.yaml to set slack.enabled = true."""
    connectors_path = _ARTHA_DIR / "config" / "connectors.yaml"
    if not connectors_path.exists():
        print(f"  WARNING: {connectors_path} not found — skipping auto-enable")
        return

    content = connectors_path.read_text(encoding="utf-8")

    # Find the slack: block and flip enabled: false → true
    if "  enabled: false\n" in content and "slack:" in content:
        # Only flip the first occurrence after the slack: marker
        slack_pos = content.find("slack:")
        if slack_pos >= 0:
            after_slack = content[slack_pos:]
            patched = after_slack.replace("  enabled: false\n", "  enabled: true\n", 1)
            new_content = content[:slack_pos] + patched
            connectors_path.write_text(new_content, encoding="utf-8")
            print("  config/connectors.yaml: slack.enabled → true")

    # Set workspace_slug if provided
    if workspace_slug and "workspace_slug: ''" in content:
        new_content = connectors_path.read_text(encoding="utf-8")
        new_content = new_content.replace(
            "workspace_slug: ''",
            f"workspace_slug: '{workspace_slug}'",
        )
        connectors_path.write_text(new_content, encoding="utf-8")
        print(f"  config/connectors.yaml: workspace_slug → {workspace_slug}")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def _run_wizard(args: argparse.Namespace) -> int:
    kr = _get_keyring()

    _print_header("Artha Slack Setup Wizard")
    print("This wizard will connect Artha to your Slack workspace.")
    print("You'll need to create a Slack App first (takes ~2 minutes).\n")

    # Step 1: Show App Manifest
    _print_step(1, "Create a Slack App")
    print("Go to: https://api.slack.com/apps → 'Create New App' → 'From an app manifest'")
    print("\nPaste the following manifest YAML:\n")
    print("─" * 60)
    print(_APP_MANIFEST_YAML)
    print("─" * 60)
    print("\nAfter creating the app:")
    print("  • Go to 'Install App' → 'Install to Workspace' → copy the Bot User OAuth Token")
    print("  • Go to 'Basic Information' → 'App-Level Tokens' → generate a token with")
    print("    'connections:write' scope → copy the App-Level Token (xapp-…)")
    input("\nPress Enter when you have your tokens ready… ")

    # Step 2: Bot Token
    _print_step(2, "Enter Bot Token (xoxb-…)")

    existing_bot = kr.get_password(_SERVICE, _BOT_TOKEN_KEY) or ""
    if existing_bot and not args.reset:
        print(f"  Found existing bot token (…{existing_bot[-8:]})")
        if not _confirm("  Replace existing token?", default=False):
            bot_token = existing_bot
        else:
            bot_token = ""
    else:
        bot_token = ""

    if not bot_token:
        while True:
            bot_token = input("  Bot token (xoxb-…): ").strip()
            if not bot_token:
                print("  Token cannot be empty.")
                continue
            if not bot_token.startswith("xoxb-"):
                print("  WARNING: Expected token starting with 'xoxb-'. Continuing anyway…")
            break

    print("  Verifying bot token…")
    auth_info = _verify_bot_token(bot_token)
    if not auth_info:
        print("\nERROR: Bot token verification failed. Check the token and try again.")
        return 1

    team = auth_info.get("team", "unknown")
    bot_user = auth_info.get("user", "unknown")
    print(f"  ✓ Bot token valid — workspace: {team}, bot user: {bot_user}")

    kr.set_password(_SERVICE, _BOT_TOKEN_KEY, bot_token)
    print(f"  Stored in keyring under '{_BOT_TOKEN_KEY}'")

    # Step 3: App Token (optional — for Socket Mode)
    _print_step(3, "Enter App-Level Token (xapp-…) — optional, enables Socket Mode")
    print("  Socket Mode allows Artha to receive messages from Slack in real-time.")
    print("  Skip this step if you only need outbound notifications.")

    existing_app = kr.get_password(_SERVICE, _APP_TOKEN_KEY) or ""
    setup_socket_mode = False

    if existing_app and not args.reset:
        print(f"  Found existing app token (…{existing_app[-8:]})")
        if _confirm("  Replace existing app token?", default=False):
            app_token = ""
        else:
            app_token = existing_app
            setup_socket_mode = True
    else:
        app_token = ""

    if not app_token:
        if _confirm("  Set up Socket Mode (app-level token)?", default=True):
            while True:
                app_token = input("  App token (xapp-…): ").strip()
                if not app_token:
                    print("  Skipping Socket Mode setup.")
                    break
                if not app_token.startswith("xapp-"):
                    print("  WARNING: Expected token starting with 'xapp-'. Continuing anyway…")
                print("  Verifying app token…")
                if _verify_app_token(app_token):
                    print("  ✓ App token valid")
                    kr.set_password(_SERVICE, _APP_TOKEN_KEY, app_token)
                    print(f"  Stored in keyring under '{_APP_TOKEN_KEY}'")
                    setup_socket_mode = True
                    break
                print("  App token verification failed. Try again (or press Enter to skip).")
        else:
            print("  Socket Mode not configured. Outbound-only mode.")

    # Step 4: Workspace slug (for message deep-links)
    _print_step(4, "Workspace slug (optional — for message deep-links)")
    print("  Your workspace URL: https://<slug>.slack.com")
    workspace_slug_default = args.workspace_slug or ""
    workspace_slug = input(
        f"  Workspace slug{f' [{workspace_slug_default}]' if workspace_slug_default else ''}: "
    ).strip() or workspace_slug_default

    # Step 5: Test message
    _print_step(5, "Send test message")
    default_channel = args.channel or auth_info.get("user_id", "")
    channel = input(
        f"  Channel or user ID to send test message to [{default_channel}]: "
    ).strip() or default_channel

    if channel:
        if _confirm(f"  Send test message to {channel}?", default=True):
            _send_test_message(bot_token, channel)
    else:
        print("  Skipping test message (no channel specified).")

    # Step 6: Update config
    _print_step(6, "Update Artha configuration")
    channels_list = [c.strip() for c in (args.channel or "").split(",") if c.strip()]
    _enable_in_connectors_yaml(workspace_slug, channels_list)

    # Summary
    _print_header("Setup Complete")
    print(f"  Workspace  : {team}")
    print(f"  Bot user   : {bot_user}")
    print(f"  Bot token  : stored ✓")
    print(f"  App token  : {'stored ✓' if setup_socket_mode else 'not configured (outbound only)'}")
    print(f"  Deep-links : {'enabled (' + workspace_slug + ')' if workspace_slug else 'disabled'}")
    print()
    print("Next steps:")
    print("  • Add channel names to config/connectors.yaml under slack.channels")
    print("  • Run: python artha.py status   to verify the integration")
    if not setup_socket_mode:
        print("  • Re-run this wizard with --reset to add Socket Mode later")
    print()
    return 0


def _run_verify_only() -> int:
    """Just verify existing tokens without re-entering them."""
    kr = _get_keyring()
    _print_header("Verify Existing Slack Tokens")

    bot_token = kr.get_password(_SERVICE, _BOT_TOKEN_KEY)
    if not bot_token:
        print("No bot token found in keyring. Run setup_slack.py (without --verify-only).")
        return 1

    print(f"Bot token (…{bot_token[-8:]}): verifying…")
    auth_info = _verify_bot_token(bot_token)
    if auth_info:
        print(f"  ✓ Valid — workspace: {auth_info.get('team')}, bot: {auth_info.get('user')}")
    else:
        print("  ✗ Invalid — re-run setup_slack.py to update token")
        return 1

    app_token = kr.get_password(_SERVICE, _APP_TOKEN_KEY)
    if app_token:
        print(f"App token (…{app_token[-8:]}): verifying…")
        if _verify_app_token(app_token):
            print("  ✓ Valid — Socket Mode enabled")
        else:
            print("  ✗ Invalid — re-run setup_slack.py --reset to update token")
            return 1
    else:
        print("App token: not configured (outbound only)")

    print("\nAll tokens valid. Slack integration is healthy.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Artha Slack setup wizard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify existing tokens without re-entering them",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Force re-entry of all tokens (replaces existing keyring entries)",
    )
    parser.add_argument(
        "--channel",
        default="",
        help="Default Slack channel for test message and connector config",
    )
    parser.add_argument(
        "--workspace-slug",
        default="",
        dest="workspace_slug",
        help="Slack workspace slug for deep-link URLs (e.g. 'mycompany')",
    )
    args = parser.parse_args()

    if args.verify_only:
        sys.exit(_run_verify_only())
    else:
        sys.exit(_run_wizard(args))


if __name__ == "__main__":
    main()
