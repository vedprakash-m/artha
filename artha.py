#!/usr/bin/env python3
"""
artha.py — Artha entry point.

Detects whether Artha is configured and routes accordingly:

  • Cold start (no user_profile.yaml) → show demo → offer guided setup
  • Configured                         → print welcome + usage hint
  • --setup                            → run interactive wizard
  • --preflight                        → run preflight checks and exit

Usage:
    python artha.py              # auto-detect and route
    python artha.py --demo       # force demo mode
    python artha.py --setup      # run interactive setup wizard
    python artha.py --preflight  # run preflight checks and exit

Ref: specs/supercharge.md §9.2
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ANSI color helpers — only applied when stdout is a real terminal
_TTY = sys.stdout.isatty()
_BOLD  = "\033[1m"  if _TTY else ""
_GREEN = "\033[32m" if _TTY else ""
_CYAN  = "\033[36m" if _TTY else ""
_YELLOW = "\033[33m" if _TTY else ""
_DIM   = "\033[2m"  if _TTY else ""
_RED   = "\033[31m" if _TTY else ""
_RST   = "\033[0m"  if _TTY else ""

_ROOT = Path(__file__).resolve().parent
_SCRIPTS = _ROOT / "scripts"
_CONFIG = _ROOT / "config"
_STATE = _ROOT / "state"

_USER_PROFILE = _CONFIG / "user_profile.yaml"
_EXAMPLE_PROFILE = _CONFIG / "user_profile.example.yaml"
_STARTER_PROFILE = _CONFIG / "user_profile.starter.yaml"

# Timezone shortcuts → full IANA names
_TZ_SHORTCUTS: dict[str, str] = {
    "et": "America/New_York",
    "ct": "America/Chicago",
    "mt": "America/Denver",
    "pt": "America/Los_Angeles",
    "ist": "Asia/Kolkata",
    "utc": "UTC",
    "gmt": "GMT",
    "bst": "Europe/London",
    "cet": "Europe/Paris",
    "jst": "Asia/Tokyo",
    "aest": "Australia/Sydney",
}

# Known AI CLI commands to detect
_AI_CLIS = [
    ("claude",  "Claude Code",      "https://docs.anthropic.com/en/docs/claude-code"),
    ("gemini",  "Gemini CLI",        "https://github.com/google-gemini/gemini-cli"),
]


def _detect_ai_clis() -> list[tuple[str, str, bool]]:
    """Return list of (name, install_url, is_installed) for known AI CLIs."""
    import shutil
    return [
        (name, url, shutil.which(cmd) is not None)
        for cmd, name, url in _AI_CLIS
    ]


def _print_ai_cli_status() -> None:
    """Print a tailored 'your next step' block based on installed AI CLIs."""
    clis = _detect_ai_clis()
    installed = [(name, url) for name, url, found in clis if found]
    missing   = [(name, url) for name, url, found in clis if not found]

    # Copilot lives inside VS Code — check separately
    import shutil
    has_code = shutil.which("code") is not None

    if installed or has_code:
        detected_parts = [name for name, _ in installed]
        if has_code:
            detected_parts.append("GitHub Copilot (VS Code)")
        print(f"  {_GREEN}Detected:{_RST}  {', '.join(detected_parts)}")
        print()
        print(f"  {_BOLD}Your next step:{_RST}")
        if installed:
            cmd_name = installed[0][0].lower().split()[0]  # e.g. 'claude'
            print(f"    → Run: {_BOLD}{cmd_name}{_RST}  (then say: {_BOLD}catch me up{_RST})")
        if has_code:
            print(f"    → Open VS Code and ask Copilot: {_BOLD}catch me up{_RST}")
    else:
        print(f"  {_YELLOW}No AI CLI detected.{_RST}  Install one to use Artha:")
        for name, url in missing:
            print(f"    {_CYAN}•{_RST} {name}: {url}")
        print(f"    {_CYAN}•{_RST} GitHub Copilot: install VS Code + the Copilot extension")
        print()
        print(f"  After installing, open your CLI and say: {_BOLD}catch me up{_RST}")


def _run(script: str, *args: str) -> int:
    """Run a script in the same Python interpreter and return exit code."""
    cmd = [sys.executable, str(_SCRIPTS / script), *args]
    return subprocess.call(cmd)


def _is_configured() -> bool:
    """Return True if a non-example user_profile.yaml exists."""
    return _USER_PROFILE.exists()


def _prompt(question: str, default: str = "") -> str:
    """Prompt the user with an optional default value."""
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {question}{suffix}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)
    return val if val else default


def _detect_email_type(email: str) -> str:
    """Detect the provider type from an email address."""
    em = email.lower()
    if em.endswith("@gmail.com") or em.endswith(".gmail.com"):
        return "gmail"
    if any(em.endswith(d) for d in ("@outlook.com", "@hotmail.com", "@live.com", "@msn.com")):
        return "outlook"
    if any(em.endswith(d) for d in ("@me.com", "@icloud.com", "@mac.com")):
        return "icloud"
    return "gmail"  # default — user can override later


def _resolve_timezone(raw: str) -> str:
    """Resolve a timezone shortcut (ET/CT/...) or return the raw value."""
    normalized = raw.strip().lower()
    return _TZ_SHORTCUTS.get(normalized, raw.strip())


def _write_profile_from_wizard(
    name: str,
    email: str,
    tz: str,
    household: str,
    children: list[dict],
) -> None:
    """Write a minimal user_profile.yaml from wizard inputs."""
    email_type = _detect_email_type(email)

    gmail_val   = email if email_type == "gmail"   else ""
    outlook_val = email if email_type == "outlook" else ""
    icloud_val  = email if email_type == "icloud"  else ""

    gmail_enabled   = "true" if email_type == "gmail"   else "false"
    outlook_enabled = "true" if email_type == "outlook" else "false"
    icloud_enabled  = "true" if email_type == "icloud"  else "false"

    kids_enabled = "true" if (household == "family" and children) else "false"

    lines: list[str] = [
        "# config/user_profile.yaml — created by Artha setup wizard",
        "# Edit to update your information.",
        "# Full reference: config/user_profile.example.yaml",
        "schema_version: \"1.0\"",
        "",
        "family:",
        "  primary_user:",
        f"    name: \"{name}\"",
        "    emails:",
        f"      gmail:   \"{gmail_val}\"",
        f"      outlook: \"{outlook_val}\"",
        f"      icloud:  \"{icloud_val}\"",
    ]

    if household in ("couple", "family"):
        lines += [
            "  spouse:",
            "    enabled: true",
            "    filtered_briefing: true",
        ]

    if household == "family" and children:
        lines.append("  children:")
        for child in children:
            lines.append(f"    - name: \"{child['name']}\"")
            if child.get("age"):
                lines.append(f"      age: {child['age']}")
            if child.get("grade"):
                lines.append(f"      grade: \"{child['grade']}\"")
            lines += [
                "      milestones:",
                "        college_prep: false",
                "        new_driver: false",
            ]

    lines += [
        "",
        "household:",
        f"  type: \"{household}\"",
        "  tenure: \"owner\"     # change to renter if applicable",
        "",
        "location:",
        f"  timezone: \"{tz}\"",
        "",
        "domains:",
        "  finance:",
        "    enabled: true",
        "  health:",
        "    enabled: true",
        "  home:",
        "    enabled: true",
        "  goals:",
        "    enabled: true",
        "  calendar:",
        "    enabled: true",
        "  comms:",
        "    enabled: true",
        "  digital:",
        "    enabled: true",
        "  kids:",
        f"    enabled: {kids_enabled}",
        "  immigration:",
        "    enabled: false",
        "",
        "integrations:",
        "  gmail:",
        f"    enabled: {gmail_enabled}",
        f"    account: \"{gmail_val}\"",
        "  google_calendar:",
        f"    enabled: {gmail_enabled}",
        "  microsoft_graph:",
        f"    enabled: {outlook_enabled}",
        f"    account: \"{outlook_val}\"",
        "  icloud:",
        f"    enabled: {icloud_enabled}",
        f"    account: \"{icloud_val}\"",
        "",
        "briefing:",
        "  default_format: \"standard\"",
        "  archive_enabled: true",
        "",
        "encryption:",
        "  age_recipient: \"\"   # paste your age1... key here after: age-keygen",
    ]

    _USER_PROFILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def do_demo() -> None:
    """Run demo_catchup.py to show a fictional briefing."""
    print(f"\n{_BOLD}── Artha Demo ──────────────────────────────────────────────────────{_RST}")
    print(f"{_DIM}Showing a sample briefing with fictional data. No real accounts needed.{_RST}\n")
    rc = _run("demo_catchup.py")
    if rc != 0:
        print("\n[artha] Demo script exited with errors — see above for details.")


def do_setup(skip_wizard: bool = False) -> None:
    """
    Guided setup:
      - Interactive wizard collects name/email/timezone/household.
      - Writes a minimal user_profile.yaml (no 234-line YAML to parse).
      - Auto-runs generate_identity.py to build config/Artha.md.
      - If skip_wizard=True: copies the minimal starter profile instead.
    """
    print(f"\n{_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RST}")
    print(f"{_BOLD}  Artha Quick Setup  — takes about 2 minutes{_RST}")
    print(f"{_BOLD}  You can change any of this later by editing config/user_profile.yaml.{_RST}")
    print(f"{_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RST}\n")

    if skip_wizard:
        _do_setup_minimal()
        return

    # ── Collect inputs ───────────────────────────────────────────────────────
    name = ""
    while not name:
        name = _prompt("Your name (first name is fine)")
        if not name:
            print(f"  {_YELLOW}!{_RST}  Name is required.")

    email = ""
    while not email or "@" not in email:
        email = _prompt("Your primary email (Gmail, Outlook, or iCloud)")
        if not email or "@" not in email:
            print(f"  {_YELLOW}!{_RST}  Enter a valid email address.")
    email_type = _detect_email_type(email)
    email_label = {"gmail": "Google (Gmail + Calendar)", "outlook": "Microsoft (Outlook + Calendar)",
                   "icloud": "Apple (iCloud Mail + Calendar)"}.get(email_type, "email")
    print(f"       {_GREEN}→{_RST}  {email_label} integration ready")

    print()
    print(f"  Timezone shortcuts:  ET  CT  MT  PT  IST  UTC")
    print(f"  Or type a full IANA name: America/Chicago, Europe/London, Asia/Kolkata …")
    raw_tz = _prompt("Your timezone", default="ET")
    tz = _resolve_timezone(raw_tz)
    if tz != raw_tz:
        print(f"       {_GREEN}→{_RST}  {tz}")

    print()
    household_raw = _prompt("Household type  [single / couple / family]", default="single")
    household = household_raw.strip().lower()
    if household not in ("single", "couple", "family"):
        household = "single"
        print(f"       {_YELLOW}→{_RST}  Unrecognized, defaulting to 'single'")

    children: list[dict] = []
    if household == "family":
        print()
        try:
            n_str = _prompt("Number of children", default="0")
            n_children = int(n_str) if n_str.isdigit() else 0
        except ValueError:
            n_children = 0

        for i in range(min(n_children, 6)):  # cap at 6 to prevent runaway input
            print()
            c_name = _prompt(f"  Child {i + 1} name")
            if not c_name:
                break
            c_age_raw = _prompt(f"  Child {i + 1} age", default="")
            c_grade   = _prompt(f"  Child {i + 1} grade (e.g. 9th — press Enter to skip)", default="")
            c_age = int(c_age_raw) if c_age_raw.isdigit() else None
            child: dict = {"name": c_name}
            if c_age:
                child["age"] = c_age
            if c_grade:
                child["grade"] = c_grade
            children.append(child)

    # ── Write profile ────────────────────────────────────────────────────────
    print()
    _write_profile_from_wizard(name, email, tz, household, children)
    print(f"  {_GREEN}✓{_RST}  Profile written to config/user_profile.yaml")

    # ── Auto-generate Artha.md ───────────────────────────────────────────────
    print(f"  Generating config/Artha.md …")
    rc = subprocess.call(
        [sys.executable, str(_SCRIPTS / "generate_identity.py")],
        cwd=str(_ROOT),
    )
    if rc == 0:
        print(f"  {_GREEN}✓{_RST}  config/Artha.md ready")
    else:
        print(f"  {_YELLOW}!{_RST}  Identity generation had warnings — run manually if needed:")
        print(f"     python scripts/generate_identity.py")

    print()
    print(f"{_BOLD}┌─────────────────────────────────────────────────────────────────────┐{_RST}")
    print(f"{_BOLD}│  {_GREEN}✓  Artha knows who you are now.{_RST}{_BOLD}                                  │{_RST}")
    print(f"{_BOLD}│                                                                     │{_RST}")
    print(f"{_BOLD}│  {_RST}Next: open your AI assistant and say:{_BOLD}                            │{_RST}")
    print(f"{_BOLD}│        {_GREEN}catch me up{_RST}{_BOLD}                                               │{_RST}")
    print(f"{_BOLD}│                                                                     │{_RST}")
    print(f"{_BOLD}│  {_DIM}🔒  Your data stays on this machine. Artha never phones home.{_RST}{_BOLD}  │{_RST}")
    print(f"{_BOLD}└─────────────────────────────────────────────────────────────────────┘{_RST}")
    print()
    _print_ai_cli_status()
    print()
    print(f"  {_DIM}Optional next steps:{_RST}")
    print(f"    Connect Gmail/Calendar:  python scripts/setup_google_oauth.py")
    print(f"    Set up encryption:       brew install age  (then: age-keygen)")
    print(f"    Full guided setup:       /bootstrap  (inside your AI CLI)")
    print()


def _do_setup_minimal() -> None:
    """Copy the minimal starter profile for manual editing."""
    if not _USER_PROFILE.exists():
        import shutil
        src = _STARTER_PROFILE if _STARTER_PROFILE.exists() else _EXAMPLE_PROFILE
        if not src.exists():
            print(f"ERROR: No profile template found at {src}", file=sys.stderr)
            sys.exit(2)
        shutil.copy(src, _USER_PROFILE)
        print(f"  {_GREEN}✓{_RST}  Minimal profile template copied → config/user_profile.yaml")
    else:
        print(f"  Profile already exists at config/user_profile.yaml")

    print()
    print(f"  Next steps:")
    print(f"    1. Edit config/user_profile.yaml with your details")
    print(f"    2. python scripts/generate_identity.py")
    print(f"    3. Open your AI CLI and say: catch me up")


def do_preflight() -> int:
    """Run preflight.py and return its exit code."""
    return _run("preflight.py")


def do_welcome() -> None:
    """Print a brief welcome for already-configured users."""
    print(f"\n{_BOLD}┌─────────────────────────────────────────────────────────────────┐{_RST}")
    print(f"{_BOLD}│  {_GREEN}✓{_RST}{_BOLD}  Artha is configured and ready.                              │{_RST}")
    print(f"{_BOLD}└─────────────────────────────────────────────────────────────────┘{_RST}")
    print()
    _print_ai_cli_status()
    print()
    print(f"{_DIM}Other commands:{_RST}")
    print(f"  {_CYAN}•{_RST} python scripts/preflight.py        — system health check")
    print(f"  {_CYAN}•{_RST} python scripts/pipeline.py --health — connector health")
    print(f"  {_CYAN}•{_RST} python artha.py --demo             — replay the demo briefing")
    print(f"  {_CYAN}•{_RST} python artha.py --setup            — re-run setup wizard")
    print(f"  {_DIM}🔒  Your data stays on this machine. See docs/security.md{_RST}")
    print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="artha.py",
        description="Artha Personal Intelligence OS — entry point",
    )
    p.add_argument("--demo",      action="store_true", help="Show demo briefing")
    p.add_argument("--setup",     action="store_true", help="Run interactive setup wizard")
    p.add_argument("--no-wizard", action="store_true", help="With --setup: skip wizard, copy minimal profile")
    p.add_argument("--preflight", action="store_true", help="Run preflight checks and exit")
    args = p.parse_args(argv)

    if args.demo:
        do_demo()
        return 0

    if args.setup:
        do_setup(skip_wizard=args.no_wizard)
        return 0

    if args.preflight:
        return do_preflight()

    # Auto-detect
    if not _is_configured():
        print()
        print(f"{_BOLD}┌─────────────────────────────────────────────────────────────────┐{_RST}")
        print(f"{_BOLD}│  👋  Welcome to Artha — Personal Intelligence OS                │{_RST}")
        print(f"{_BOLD}│                                                                 │{_RST}")
        print(f"{_BOLD}│  No profile found yet.  Let's show you what Artha does first.  │{_RST}")
        print(f"{_BOLD}└─────────────────────────────────────────────────────────────────┘{_RST}")
        print()
        do_demo()
        print()
        print(f"{_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RST}")
        print(f"{_BOLD}  Ready to set this up for YOUR life?{_RST}")
        print(f"{_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RST}")
        print()
        answer = input("  Start guided setup now? [yes/no]: ").strip().lower()
        if answer in ("yes", "y"):
            do_setup()
        else:
            print()
            print(f"  Run {_BOLD}'python artha.py --setup'{_RST} whenever you're ready.")
            print(f"  Or run {_BOLD}'bash setup.sh'{_RST} for the automated quick-start.")
        return 0

    # Configured path: show welcome only — no auto-preflight (avoids cognitive whiplash
    # showing ⛔ NO-GO failures for OAuth that just hasn't been configured yet).
    # Users can run: python scripts/preflight.py  when they want a health check.
    do_welcome()
    return 0


if __name__ == "__main__":
    sys.exit(main())

