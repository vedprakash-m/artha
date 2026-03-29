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

    archetype = {
        "single": "Solo",
        "couple": "Couple",
        "family": "Family",
    }.get(household, "Solo")
    if email_type == "outlook" and household == "single":
        # single + Outlook more likely Professional tier
        archetype = "Professional"

    lines += [
        "",
        "setup:",
        f"  archetype: \"{archetype}\"",
        "  completeness: 0.15   # rises as you connect integrations",
        "  next_suggested: \"connect your email\"",
        "  completed_steps:",
        "    - name_email",
        "  remaining_steps:",
        "    - gmail_oauth",
        "    - encryption",
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


# ---------------------------------------------------------------------------
# Doctor command (I-12) — unified diagnostic
# ---------------------------------------------------------------------------

def do_doctor() -> int:
    """
    Run all diagnostic checks and print a human-readable pass/warn/fail report.

    Checks:
      1  Python version ≥ 3.11
      2  Virtual environment active
      3  Core packages installed (PyYAML, keyring, jsonschema)
      4  age binary in PATH
      5  Encryption key in system keyring
      6  age_recipient configured in user_profile.yaml
      7  Gmail OAuth token present and valid
      8  Outlook OAuth token (optional — not-configured is a warning)
      9  State directory exists and is writable
      10 PII git pre-commit hook installed
      11 Last catch-up date (from state/health-check.md)

    Exit codes: 0 = all pass/warn, 1 = at least one failure.

    Ref: specs/improve.md §8 I-12
    """
    import shutil
    import importlib

    results: list[tuple[str, str, str]] = []  # (icon, check_name, message)
    failures = 0

    def _pass(name: str, msg: str) -> None:
        results.append((_GREEN + "  ✓" + _RST, name, msg))

    def _warn(name: str, msg: str) -> None:
        results.append((_YELLOW + "  ⚠" + _RST, name, msg))

    def _fail(name: str, msg: str) -> None:
        nonlocal failures
        failures += 1
        results.append((_RED + "  ✗" + _RST, name, msg))

    # ── 1. Python version ────────────────────────────────────────────────────
    vi = sys.version_info
    ver_str = f"{vi.major}.{vi.minor}.{vi.micro}"
    if vi >= (3, 11):
        _pass("Python version", ver_str)
    else:
        _fail("Python version", f"{ver_str} — Python 3.11+ required")

    # ── 2. Virtual environment ───────────────────────────────────────────────
    in_venv = (
        hasattr(sys, "real_prefix")                  # virtualenv
        or (sys.base_prefix != sys.prefix)           # venv / pyvenv
    )
    if in_venv:
        _pass("Virtual environment", f"active ({sys.prefix})")
    else:
        _fail("Virtual environment", "not active — run from inside the venv")

    # ── 3. Core packages ─────────────────────────────────────────────────────
    required_packages = ["yaml", "keyring", "jsonschema"]
    missing_pkgs: list[str] = []
    for pkg in required_packages:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing_pkgs.append(pkg)
    if not missing_pkgs:
        _pass("Core packages", "PyYAML ✓  keyring ✓  jsonschema ✓")
    else:
        _fail("Core packages", f"missing: {', '.join(missing_pkgs)}")

    # ── 4. age binary ────────────────────────────────────────────────────────
    age_path = shutil.which("age")
    if age_path:
        # Try to get version
        try:
            import subprocess as sp
            r = sp.run(["age", "--version"], capture_output=True, text=True, timeout=5)
            age_ver = r.stdout.strip() or r.stderr.strip() or "found"
            _pass("age binary", age_ver)
        except Exception:
            _pass("age binary", "found (version unknown)")
    else:
        _warn("age binary", "not found — encryption unavailable (brew install age)")

    # ── 5. Encryption key in keyring ─────────────────────────────────────────
    try:
        import keyring as kr
        key = kr.get_password("artha", "age-key")
        if key:
            _pass("Encryption key", "age private key in keyring ✓")
        else:
            _warn("Encryption key", "not in keyring — vault features unavailable")
    except Exception as exc:
        _warn("Encryption key", f"keyring error: {exc}")

    # ── 6. age_recipient in user_profile.yaml ───────────────────────────────
    profile = _CONFIG / "user_profile.yaml"
    if profile.exists():
        try:
            import sys as _sys
            if str(_SCRIPTS) not in _sys.path:
                _sys.path.insert(0, str(_SCRIPTS))
            import yaml
            with open(profile, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            recipient = (
                data.get("encryption", {}).get("age_recipient", "")
                or data.get("age_recipient", "")
            )
            if recipient and recipient.startswith("age1"):
                _pass("age_recipient", f"{recipient[:20]}…")
            elif recipient:
                _warn("age_recipient", f"configured but unexpected format: {recipient[:30]}")
            else:
                _warn("age_recipient", "not set in user_profile.yaml — run: age-keygen")
        except Exception as exc:
            _warn("age_recipient", f"could not read user_profile.yaml: {exc}")
    else:
        _warn("age_recipient", "user_profile.yaml not found — run: python artha.py --setup")

    # ── 7. Gmail OAuth token ─────────────────────────────────────────────────
    gmail_token = _ROOT / ".tokens" / "gmail-token.json"
    if gmail_token.exists():
        try:
            import json
            with open(gmail_token, encoding="utf-8") as fh:
                tok = json.load(fh)
            has_fields = all(tok.get(f) for f in ["refresh_token", "token_uri", "client_id"])
            if has_fields:
                expiry = tok.get("expiry", "unknown")
                _pass("Gmail OAuth token", f"valid (expires ~{expiry[:10] if expiry != 'unknown' else 'unknown'})")
            else:
                _warn("Gmail OAuth token", "token file incomplete — re-run: python scripts/setup_google_oauth.py")
        except Exception:
            _warn("Gmail OAuth token", "token file unreadable — re-run: python scripts/setup_google_oauth.py")
    else:
        _warn("Gmail OAuth token", "not configured (run scripts/setup_google_oauth.py to enable)")

    # ── 8. Outlook OAuth token ───────────────────────────────────────────────
    ms_token = _ROOT / ".tokens" / "msgraph-token.json"
    if ms_token.exists():
        _pass("Outlook OAuth token", "token file present ✓")
    else:
        _warn("Outlook OAuth token", "not configured (run scripts/setup_msgraph_oauth.py to enable)")

    # ── 9. State directory ───────────────────────────────────────────────────
    state_dir = _ROOT / "state"
    if state_dir.is_dir():
        md_count = len(list(state_dir.glob("*.md"))) + len(list(state_dir.glob("*.md.age")))
        # Verify writable
        try:
            test_file = state_dir / ".doctor_write_test"
            test_file.write_text("ok")
            test_file.unlink()
            _pass("State directory", f"OK ({md_count} domain files)")
        except OSError as exc:
            _fail("State directory", f"not writable: {exc}")
    else:
        _fail("State directory", "state/ directory missing — run: python scripts/preflight.py --fix")

    # ── 10. PII git hook ─────────────────────────────────────────────────────
    hooks_dir = _ROOT / ".git" / "hooks"
    hook_file = hooks_dir / "pre-commit"
    if not (_ROOT / ".git").is_dir():
        _warn("PII git hook", ".git not found — not a git repo (skipped)")
    elif hook_file.exists():
        content = hook_file.read_text(errors="replace")
        if "pii" in content.lower() or "vault_hook" in content.lower():
            _pass("PII git hook", "pre-commit hook installed ✓")
        else:
            _warn("PII git hook", "pre-commit hook exists but may not include PII scan")
    else:
        _warn("PII git hook", "not installed — run: make pii-scan (or see CONTRIBUTING.md)")

    # ── 11. Last catch-up date ───────────────────────────────────────────────
    health_check = _STATE / "health-check.md"
    if health_check.exists():
        import re as _re
        content = health_check.read_text(errors="replace")
        # Look for "Last catch-up: YYYY-MM-DD" patterns
        m = _re.search(r"(?:last[_\s]catch[_\-]?up|last[_\s]run)[:\s]+(\d{4}-\d{2}-\d{2})", content, _re.IGNORECASE)
        if m:
            from datetime import date
            try:
                last_date = date.fromisoformat(m.group(1))
                delta = (date.today() - last_date).days
                if delta == 0:
                    _pass("Last catch-up", f"today ({last_date})")
                elif delta <= 2:
                    _pass("Last catch-up", f"{last_date} ({delta}d ago)")
                elif delta <= 7:
                    _warn("Last catch-up", f"{last_date} ({delta}d ago)")
                else:
                    _warn("Last catch-up", f"{last_date} ({delta}d ago — consider running catch-up)")
            except ValueError:
                _warn("Last catch-up", f"date found but unparseable: {m.group(1)}")
        else:
            _warn("Last catch-up", "health-check.md exists but no date found")
    else:
        _warn("Last catch-up", "health-check.md not found — run catch-up to initialise")

    # ── Render report ────────────────────────────────────────────────────────
    warnings = sum(1 for icon, _, _ in results if "⚠" in icon)
    passed   = sum(1 for icon, _, _ in results if "✓" in icon)

    print(f"\n{_BOLD}━━ ARTHA DOCTOR ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RST}")
    for icon, name, msg in results:
        print(f"{icon}  {_BOLD}{name}{_RST}: {msg}")
    print(f"{_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RST}")
    summary_parts = []
    if passed:
        summary_parts.append(f"{_GREEN}{passed} passed{_RST}")
    if warnings:
        summary_parts.append(f"{_YELLOW}{warnings} warning{'s' if warnings > 1 else ''}{_RST}")
    if failures:
        summary_parts.append(f"{_RED}{failures} failed{_RST}")
    print(f"  {'  ·  '.join(summary_parts)}")
    print()

    return 1 if failures > 0 else 0


def do_welcome() -> None:
    """Print a brief welcome for already-configured users."""
    print(f"\n{_BOLD}┌─────────────────────────────────────────────────────────────────┐{_RST}")
    print(f"{_BOLD}│  {_GREEN}✓{_RST}{_BOLD}  Artha is configured and ready.                              │{_RST}")
    print(f"{_BOLD}└─────────────────────────────────────────────────────────────────┘{_RST}")
    print()
    _print_ai_cli_status()
    print()
    print(f"{_DIM}Other commands:{_RST}")
    print(f"  {_CYAN}•{_RST} python artha.py --doctor              — unified diagnostic (recommended)")
    print(f"  {_CYAN}•{_RST} python scripts/preflight.py        — detailed preflight checks")
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
    p.add_argument("--doctor",    action="store_true", help="Run unified diagnostic and exit")
    args = p.parse_args(argv)

    if args.demo:
        do_demo()
        return 0

    if args.setup:
        do_setup(skip_wizard=args.no_wizard)
        return 0

    if args.preflight:
        return do_preflight()

    if args.doctor:
        return do_doctor()

    # Auto-detect
    if not _is_configured():
        print()
        print(f"Welcome to Artha. Let me show you what I can do.")
        print()
        do_demo()
        print()
        print(f"{_DIM}That was a demo. Want to set up your real data?{_RST}")
        print(f"Just tell me your name and email to get started.")
        print()
        try:
            raw = input("  Name and email (e.g. Vandana, vandana@gmail.com): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            print(f"  Run {_BOLD}'python artha.py --setup'{_RST} whenever you're ready.")
            return 0
        if not raw:
            print()
            print(f"  Run {_BOLD}'python artha.py --setup'{_RST} whenever you're ready.")
            return 0
        # Parse "Name, email" or "Name email" or just email
        import re as _re2
        parts = _re2.split(r"[,\s]+", raw, maxsplit=1)
        if len(parts) == 2 and "@" in parts[1]:
            name, email = parts[0].strip(), parts[1].strip()
        elif "@" in raw:
            email = raw.strip()
            name = email.split("@")[0].replace(".", " ").title()
        else:
            name, email = raw.strip(), ""
        if not email or "@" not in email:
            email = _prompt("  Your email address")
        if not name:
            name = _prompt("  Your name")
        # Infer timezone from system; fall back to ET
        import time as _time
        try:
            tz_offset = -_time.timezone // 3600
            _tz_by_offset = {-5: "ET", -6: "CT", -7: "MT", -8: "PT", 5: "IST"}
            raw_tz = _tz_by_offset.get(tz_offset, "ET")
        except Exception:
            raw_tz = "ET"
        tz = _resolve_timezone(raw_tz)
        _write_profile_from_wizard(name, email, tz, "single", [])
        print(f"\n  {_GREEN}✓{_RST}  Profile created for {name} ({email})")
        rc = subprocess.call(
            [sys.executable, str(_SCRIPTS / "generate_identity.py")],
            cwd=str(_ROOT),
        )
        if rc == 0:
            print(f"  {_GREEN}✓{_RST}  Artha configured — open your AI CLI and say: {_BOLD}catch me up{_RST}")
        else:
            print(f"  {_YELLOW}!{_RST}  Run 'python scripts/generate_identity.py' to finish setup.")
        print(f"\n  Optional: connect Gmail/Calendar with: python scripts/setup_google_oauth.py\n")
        return 0

    # Configured path: show welcome only — no auto-preflight (avoids cognitive whiplash
    # showing ⛔ NO-GO failures for OAuth that just hasn't been configured yet).
    # Users can run: python scripts/preflight.py  when they want a health check.
    do_welcome()
    return 0


if __name__ == "__main__":
    sys.exit(main())

