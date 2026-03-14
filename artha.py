#!/usr/bin/env python3
"""
artha.py — Artha entry point.

Detects whether Artha is configured and routes accordingly:

  • Cold start (no user_profile.yaml) → show demo → offer guided setup
  • Configured + preflight OK          → print welcome + usage hint
  • Configured + preflight issues      → surface failure summary

Usage:
    python artha.py              # auto-detect and route
    python artha.py --demo       # force demo mode
    python artha.py --setup      # force guided setup
    python artha.py --preflight  # run preflight checks and exit

Ref: specs/supercharge.md §9.2
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SCRIPTS = _ROOT / "scripts"
_CONFIG = _ROOT / "config"
_STATE = _ROOT / "state"

_USER_PROFILE = _CONFIG / "user_profile.yaml"
_EXAMPLE_PROFILE = _CONFIG / "user_profile.example.yaml"


def _run(script: str, *args: str) -> int:
    """Run a script in the same Python interpreter and return exit code."""
    cmd = [sys.executable, str(_SCRIPTS / script), *args]
    return subprocess.call(cmd)


def _is_configured() -> bool:
    """Return True if a non-example user_profile.yaml exists."""
    return _USER_PROFILE.exists()


def do_demo() -> None:
    """Run demo_catchup.py to show a fictional briefing."""
    print("\n── Artha Demo ──────────────────────────────────────────────────────")
    print("Showing a sample briefing with fictional data.\n")
    rc = _run("demo_catchup.py")
    if rc != 0:
        print("\n[artha] Demo script exited with errors — see above for details.")


def do_setup() -> None:
    """Guided setup: edit user_profile.yaml + run auth setup + generate identity."""
    print("\n── Artha Setup ─────────────────────────────────────────────────────")

    if not _USER_PROFILE.exists():
        if _EXAMPLE_PROFILE.exists():
            import shutil
            shutil.copy(_EXAMPLE_PROFILE, _USER_PROFILE)
            print(f"Created {_USER_PROFILE.relative_to(_ROOT)} from example template.")
        else:
            print(f"ERROR: Example profile not found at {_EXAMPLE_PROFILE}", file=sys.stderr)
            sys.exit(2)

    print(f"\n1. Edit {_USER_PROFILE.relative_to(_ROOT)} with your real information.")
    print("   Open it in your editor and fill in the marked fields.\n")
    print("2. When ready, connect your data sources:")
    print("   python scripts/setup_google_oauth.py     # Gmail + Google Calendar")
    print("   python scripts/setup_msgraph_oauth.py    # Outlook + Teams Calendar")
    print("   python scripts/setup_icloud_auth.py      # iCloud Mail + Calendar")
    print("   python scripts/setup_todo_lists.py       # Microsoft To Do\n")
    print("3. Generate your identity file:")
    print("   python scripts/generate_identity.py\n")
    print("4. Run preflight checks:")
    print("   python scripts/preflight.py\n")
    print("5. Start an AI CLI session (Claude, Gemini, GitHub Copilot, etc.)")
    print("   and say: 'catch me up'\n")
    print("────────────────────────────────────────────────────────────────────")


def do_preflight() -> int:
    """Run preflight.py and return its exit code."""
    return _run("preflight.py")


def do_welcome() -> None:
    """Print a brief welcome for already-configured users."""
    print("\n── Artha ───────────────────────────────────────────────────────────")
    print("Configuration found.  Artha is ready.\n")
    print("Usage:")
    print("  • Open an AI CLI session and say 'catch me up' for your briefing.")
    print("  • python scripts/preflight.py      — check system health")
    print("  • python scripts/pipeline.py --health — check connector health")
    print("  • python scripts/upgrade.py        — apply any pending upgrades")
    print("\nRun 'python artha.py --demo' to see a sample briefing.")
    print("────────────────────────────────────────────────────────────────────\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="artha.py",
        description="Artha Personal Intelligence OS — entry point",
    )
    p.add_argument("--demo", action="store_true", help="Show demo briefing")
    p.add_argument("--setup", action="store_true", help="Run guided setup")
    p.add_argument(
        "--preflight", action="store_true", help="Run preflight checks and exit"
    )
    args = p.parse_args(argv)

    if args.demo:
        do_demo()
        return 0

    if args.setup:
        do_setup()
        return 0

    if args.preflight:
        return do_preflight()

    # Auto-detect
    if not _is_configured():
        print("\nWelcome to Artha!  No configuration found yet.\n")
        do_demo()
        print()
        answer = input("Want to set up Artha for your life? [yes/no]: ").strip().lower()
        if answer in ("yes", "y"):
            do_setup()
        else:
            print("\nRun 'python artha.py --setup' whenever you're ready.")
        return 0

    # Configured path: show welcome, run preflight
    do_welcome()
    return do_preflight()


if __name__ == "__main__":
    sys.exit(main())
