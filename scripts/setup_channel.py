#!/usr/bin/env python3
"""
setup_channel.py — Interactive setup wizard for Artha Channel Bridge.

Covers the full setup lifecycle for each channel adapter:
  • Walks through credential storage (keyring)
  • Writes config/channels.yaml from channels.example.yaml template
  • Tests the adapter (send_message + health_check)
  • Optionally installs the OS service for the background listener
  • Sets/validates listener_host for multi-machine safety

Usage:
    python scripts/setup_channel.py --channel telegram
    python scripts/setup_channel.py --channel telegram --test
    python scripts/setup_channel.py --install-service
    python scripts/setup_channel.py --set-listener-host
    python scripts/setup_channel.py --health

Ref: specs/conversational-bridge.md §10–§11, docs/channels.md
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

# Ensure Artha root on sys.path
_ARTHA_DIR = Path(__file__).resolve().parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

_CONFIG_DIR = _ARTHA_DIR / "config"
_CHANNELS_YAML = _CONFIG_DIR / "channels.yaml"
_CHANNELS_EXAMPLE = _CONFIG_DIR / "channels.example.yaml"
_SERVICE_DIR = _ARTHA_DIR / "scripts" / "service"


def _print_ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _print_warn(msg: str) -> None:
    print(f"  ⚠  {msg}")


def _print_err(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # yaml not available — minimal TOML-less fallback (won't be needed in practice)
        _print_err("PyYAML not found. Install: pip install pyyaml")
        sys.exit(1)
    except Exception as exc:
        _print_err(f"Could not read {path}: {exc}")
        sys.exit(1)


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    try:
        import yaml
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
    except ImportError:
        _print_err("PyYAML not required at install time but needed here. pip install pyyaml")
        sys.exit(1)


def _store_credential(service: str, key: str, value: str) -> None:
    """Store a credential in the system keyring."""
    try:
        import keyring
        keyring.set_password(service, key, value)
        _print_ok(f"Stored in keyring: {service}/{key}")
    except ImportError:
        _print_warn(
            "keyring not installed — storing as environment variable only. "
            "Install keyring for secure storage: pip install keyring"
        )


def _get_credential(service: str, key: str) -> str | None:
    """Retrieve a credential from keyring."""
    try:
        import keyring
        return keyring.get_password(service, key)
    except ImportError:
        return os.environ.get(key.upper().replace("-", "_"))


# ── Channel setup wizards ──────────────────────────────────────────────────────

def setup_telegram() -> None:
    """Interactive Telegram channel setup."""
    print("\n── Telegram Setup ───────────────────────────────────────────────")
    print("You need a Telegram Bot Token from @BotFather.")
    print("  1. Open Telegram → search @BotFather")
    print("  2. /newbot → follow prompts → copy the token")
    print()

    cred_key = "artha-telegram-bot-token"
    existing = _get_credential("artha", cred_key)
    if existing:
        use_existing = input("Found existing bot token in keyring. Use it? [Y/n]: ").strip().lower()
        if use_existing not in ("n", "no"):
            token = existing
            print()
        else:
            token = input("Paste your bot token: ").strip()
    else:
        token = input("Paste your bot token: ").strip()

    if not token:
        _print_err("No token provided. Aborting.")
        return

    _store_credential("artha", cred_key, token)

    print()
    print("Now send any message to your bot in Telegram, then press Enter.")
    print("(We'll use getUpdates to discover your chat ID automatically)")
    input("Press Enter when ready…")

    # Auto-discover chat IDs via getUpdates
    chat_ids: dict[str, str] = {}
    try:
        import urllib.request  # noqa: PLC0415
        import json as _json
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = _json.loads(resp.read())
        messages = data.get("result", [])
        for upd in messages:
            msg = upd.get("message") or upd.get("edited_message")
            if msg:
                chat = msg.get("chat", {})
                cid = str(chat.get("id", ""))
                cname = (
                    chat.get("username")
                    or chat.get("first_name")
                    or chat.get("title", "")
                )
                if cid:
                    chat_ids[cname] = cid
        if chat_ids:
            print(f"\nFound chat(s): {list(chat_ids.items())}")
        else:
            _print_warn("No messages found. Enter chat IDs manually below.")
    except Exception as exc:
        _print_warn(f"Auto-discovery failed: {exc} — enter chat IDs manually")

    print()
    primary_id = input(
        "Primary recipient chat ID (your personal chat, e.g. 123456789): "
    ).strip()
    if not primary_id:
        _print_err("No chat ID provided. Aborting.")
        return

    primary_scope = input(
        "Access scope for primary (full/family/standard) [full]: "
    ).strip().lower() or "full"

    family_id = input(
        "Family recipient chat ID (leave blank to skip): "
    ).strip()

    # Build channels.yaml
    _ensure_channels_yaml_exists()
    cfg = _load_yaml(_CHANNELS_YAML)
    cfg.setdefault("channels", {})
    cfg["channels"]["telegram"] = {
        "enabled": True,
        "adapter": "scripts/channels/telegram.py",
        "auth": {"credential_key": cred_key},
        "recipients": {
            "primary": {
                "id": primary_id,
                "access_scope": primary_scope,
            }
        },
        "features": {
            "push": True,
            "interactive": True,
        },
        "health_check": {"interval_minutes": 60},
        "retry": {"max_attempts": 3, "base_delay": 1, "max_delay": 30},
    }
    if family_id:
        family_scope = input(
            "Access scope for family (full/family/standard) [family]: "
        ).strip().lower() or "family"
        cfg["channels"]["telegram"]["recipients"]["family"] = {
            "id": family_id,
            "access_scope": family_scope,
        }

    enable_push = input(
        "Enable automatic post-catch-up push? [Y/n]: "
    ).strip().lower()
    cfg.setdefault("defaults", {})
    cfg["defaults"]["push_enabled"] = enable_push not in ("n", "no")
    cfg["defaults"].setdefault("redact_pii", True)
    cfg["defaults"].setdefault("max_push_length", 500)

    _save_yaml(_CHANNELS_YAML, cfg)
    _print_ok(f"Saved: {_CHANNELS_YAML.name}")

    # Test the connection
    print("\nTesting connection…")
    _test_channel("telegram", cfg)


def _ensure_channels_yaml_exists() -> None:
    if not _CHANNELS_YAML.exists():
        if _CHANNELS_EXAMPLE.exists():
            shutil.copy(_CHANNELS_EXAMPLE, _CHANNELS_YAML)
            _print_ok(f"Created channels.yaml from example template")
        else:
            _print_warn("channels.example.yaml not found — creating minimal channels.yaml")
            _save_yaml(_CHANNELS_YAML, {
                "defaults": {
                    "push_enabled": False,
                    "redact_pii": True,
                    "max_push_length": 500,
                    "listener_host": "",
                },
                "channels": {},
            })


def _test_channel(channel_name: str, config: dict[str, Any]) -> None:
    """Send a test message to verify the adapter works."""
    sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
    try:
        from channels.registry import create_adapter_from_config
        ch_cfg = config.get("channels", {}).get(channel_name, {})
        adapter = create_adapter_from_config(channel_name, ch_cfg)

        healthy = adapter.health_check()
        if healthy:
            _print_ok("health_check() passed")
        else:
            _print_err("health_check() failed — check your bot token")
            return

        # Send test message to the primary recipient
        recipients = ch_cfg.get("recipients", {})
        primary = recipients.get("primary", {})
        rid = str(primary.get("id", ""))
        if not rid:
            _print_warn("No primary recipient configured — skipping test message")
            return

        from channels.base import ChannelMessage
        msg = ChannelMessage(
            text=(
                "✅ *Artha Channel Bridge test*\n"
                "Your channel is configured correctly.\n"
                "Send /help to see available commands."
            ),
            recipient_id=rid,
        )
        ok = adapter.send_message(msg)
        if ok:
            _print_ok("Test message sent!")
        else:
            _print_err("Test message failed — check your chat ID and bot permissions")
    except Exception as exc:
        _print_err(f"Test failed: {exc}")


# ── Service installation ───────────────────────────────────────────────────────

def install_service() -> None:
    """Install the channel listener as an OS background service."""
    system = platform.system()
    print(f"\n── Service Installation ({system}) ──────────────────────────────")

    if system == "Windows":
        _install_windows_service()
    elif system == "Darwin":
        _install_macos_launchagent()
    elif system == "Linux":
        _install_linux_systemd()
    else:
        _print_err(f"Unsupported platform: {system}")


def _install_windows_service() -> None:
    tpl = _SERVICE_DIR / "artha-listener.xml"
    if not tpl.exists():
        _print_err(f"Service template not found: {tpl}")
        return

    python_exe = sys.executable
    artha_root = str(_ARTHA_DIR).replace("\\", "/")
    content = tpl.read_text(encoding="utf-8")
    content = content.replace("{{PYTHON_EXE}}", python_exe)
    content = content.replace("{{ARTHA_ROOT}}", artha_root)

    out_path = _ARTHA_DIR / "tmp" / "artha-listener.xml"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    _print_ok(f"Written: {out_path.name}")
    print()
    print("To register the service (requires NSSM — non-sucking service manager):")
    print(f'  nssm install ArthaChanListener "{python_exe}" "{_ARTHA_DIR / "scripts" / "channel_listener.py"}"')
    print("  nssm start ArthaChanListener")
    print()
    print("Or use Task Scheduler:")
    print("  • Action: Start a program")
    print(f'  • Program: {sys.executable}')
    print(f'  • Arguments: "{_ARTHA_DIR / "scripts" / "channel_listener.py"}"')
    print("  • Trigger: At log on, repeat every 1 minute")


def _install_macos_launchagent() -> None:
    tpl = _SERVICE_DIR / "com.artha.channel-listener.plist"
    if not tpl.exists():
        _print_err(f"Service template not found: {tpl}")
        return

    python_exe = sys.executable
    artha_root = str(_ARTHA_DIR)
    content = tpl.read_text(encoding="utf-8")
    content = content.replace("{{PYTHON_EXE}}", python_exe)
    content = content.replace("{{ARTHA_ROOT}}", artha_root)

    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    out_path = launch_agents_dir / "com.artha.channel-listener.plist"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    _print_ok(f"Written: {out_path}")
    print()
    print(f"To load: launchctl load {out_path}")
    print(f"To start: launchctl start com.artha.channel-listener")
    print(f"Logs: ~/Library/Logs/artha-channel-listener.log")


def _install_linux_systemd() -> None:
    tpl = _SERVICE_DIR / "artha-listener.service"
    if not tpl.exists():
        _print_err(f"Service template not found: {tpl}")
        return

    python_exe = sys.executable
    artha_root = str(_ARTHA_DIR)
    user = os.environ.get("USER", os.environ.get("LOGNAME", "artha"))
    content = tpl.read_text(encoding="utf-8")
    content = content.replace("{{PYTHON_EXE}}", python_exe)
    content = content.replace("{{ARTHA_ROOT}}", artha_root)
    content = content.replace("{{USER}}", user)

    service_dir = Path.home() / ".config" / "systemd" / "user"
    out_path = service_dir / "artha-channel-listener.service"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    _print_ok(f"Written: {out_path}")
    print()
    print("To enable:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable artha-channel-listener")
    print("  systemctl --user start artha-channel-listener")
    print("  systemctl --user status artha-channel-listener")


# ── Listener host configuration ────────────────────────────────────────────────

def set_listener_host() -> None:
    """Interactively set the listener_host in channels.yaml."""
    current = socket.gethostname()
    print(f"\n── Set Listener Host ────────────────────────────────────────────")
    print(f"Current machine hostname: {current}")
    print()
    print("For multi-machine OneDrive setups, only ONE machine should run")
    print("the interactive listener. Set listener_host in channels.yaml.")
    print()
    choice = input(
        f"Make THIS machine the designated listener host? ({current}) [Y/n]: "
    ).strip().lower()
    if choice in ("n", "no"):
        custom = input("Enter hostname to designate (or leave blank to allow any): ").strip()
        designated = custom if custom else ""
    else:
        designated = current

    _ensure_channels_yaml_exists()
    cfg = _load_yaml(_CHANNELS_YAML)
    cfg.setdefault("defaults", {})
    cfg["defaults"]["listener_host"] = designated
    _save_yaml(_CHANNELS_YAML, cfg)

    if designated:
        _print_ok(f"listener_host set to: {designated}")
    else:
        _print_warn("listener_host cleared — any machine can run the listener")


# ── Health check ──────────────────────────────────────────────────────────────

def run_health_check() -> int:
    """Run health checks on all configured channels."""
    print("\n── Channel Health Check ─────────────────────────────────────────")
    if not _CHANNELS_YAML.exists():
        _print_warn("config/channels.yaml not found — no channels configured")
        return 0

    cfg = _load_yaml(_CHANNELS_YAML)
    channels = {
        k: v for k, v in cfg.get("channels", {}).items()
        if isinstance(v, dict) and v.get("enabled", False)
    }

    if not channels:
        _print_warn("No enabled channels found in channels.yaml")
        return 0

    all_ok = True
    for ch_name, ch_cfg in channels.items():
        print(f"\n  [{ch_name}]")
        try:
            import time as _time
            sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
            from channels.registry import create_adapter_from_config
            adapter = create_adapter_from_config(ch_name, ch_cfg)
            _t0 = _time.perf_counter()
            ok = adapter.health_check()
            latency_ms = int((_time.perf_counter() - _t0) * 1000)
            if ok:
                _print_ok(f"Healthy ({latency_ms}ms)")
            else:
                _print_err("Health check failed")
                all_ok = False
        except Exception as exc:
            _print_err(f"Error: {exc}")
            latency_ms = -1
            all_ok = False

    return 0 if all_ok else 1


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Artha Channel Bridge setup wizard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/setup_channel.py --channel telegram
  python scripts/setup_channel.py --channel telegram --test
  python scripts/setup_channel.py --install-service
  python scripts/setup_channel.py --set-listener-host
  python scripts/setup_channel.py --health
""",
    )
    parser.add_argument("--channel", choices=["telegram", "discord", "slack"],
                        help="Channel to configure")
    parser.add_argument("--test", action="store_true",
                        help="Send test message after setup")
    parser.add_argument("--install-service", action="store_true",
                        help="Install OS background service for the listener")
    parser.add_argument("--set-listener-host", action="store_true",
                        help="Set designated listener host for multi-machine setups")
    parser.add_argument("--health", action="store_true",
                        help="Run health checks on all configured channels")
    args = parser.parse_args()

    if args.health:
        return run_health_check()

    if args.set_listener_host:
        set_listener_host()
        return 0

    if args.install_service:
        install_service()
        return 0

    if args.channel == "telegram":
        setup_telegram()
        if args.test and _CHANNELS_YAML.exists():
            cfg = _load_yaml(_CHANNELS_YAML)
            _test_channel("telegram", cfg)
        return 0

    if args.channel in ("discord", "slack"):
        print(f"\n{args.channel.title()} adapter not yet implemented.")
        print("Track progress: specs/conversational-bridge.md §14")
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
