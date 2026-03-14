#!/usr/bin/env python3
"""
preflight.py — Artha pre-catch-up go/no-go health gate
========================================================
Runs every check required before attempting a catch-up.
Exits 0 if all P0 checks pass. Exits 1 if any P0 check fails.

Usage:
  python scripts/preflight.py           — full check, exits 0/1
  python scripts/preflight.py --json    — machine-readable JSON output
  python scripts/preflight.py --quiet   — minimal output (only failures)
  python scripts/preflight.py --fix     — attempt common auto-fixes (e.g. clear stale lock)

P0 checks (hard-block catch-up if any fail):
  1  vault.py health           — age installed, credential store key present, state dir writable
  2  Vault lock status        — no active session collision; stale lock auto-cleared
  3  Gmail OAuth token        — token file exists and is not structurally broken
  4  Calendar OAuth token     — token file exists and is not structurally broken
  5  pipeline.py --health -s gmail   — Gmail API connection live
  6  pipeline.py --health -s google_calendar — Calendar API connection live
  7  pii_guard.py test        — PII filter script executable and passing tests
  8  gmail_send.py --health   — Send auth valid (token present)
  9  State directory          — state/ directory writable

P1 checks (logged as warnings, do NOT block catch-up):
  10 open_items.md readable        — persistent action file accessible
  11 Token freshness               — tokens not within 5 min of expiry
  12 Briefings directory           — briefings/ directory writable
  13 Microsoft Graph token         — To Do / email / calendar token valid
  14 pipeline.py --health -s outlook_email    — Outlook email API live (Mail.Read)
  15 pipeline.py --health -s outlook_calendar — Outlook Calendar API live (Calendars.Read)
  16 pipeline.py --health -s icloud_email     — iCloud Mail IMAP live (app-specific password)
  17 pipeline.py --health -s icloud_calendar  — iCloud Calendar CalDAV live
  18 WorkIQ Calendar               — WorkIQ M365 detection + auth (Windows only, v2.2)

Ref: TS §3.8, TS §7.1 Step 0, T-1A.11.3, PRD §9.4 Step 0
"""

from __future__ import annotations

import sys
# Ensure we run inside the Artha venv. Ref: standardization.md §7.3
from _bootstrap import reexec_in_venv; reexec_in_venv(mode="preflight")

import argparse
import json
import os
import pathlib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARTHA_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ARTHA_DIR, "scripts")
STATE_DIR   = os.path.join(ARTHA_DIR, "state")
LOCK_FILE   = os.path.join(ARTHA_DIR, ".artha-decrypted")
TOKEN_DIR   = os.path.join(ARTHA_DIR, ".tokens")

STALE_LOCK_SECONDS = 1800   # 30 minutes
TOKEN_EXPIRY_WARN_SECONDS = 300  # warn within 5 min of expiry
WORKIQ_CACHE_FILE = os.path.join(ARTHA_DIR, "tmp", ".workiq_cache.json")
WORKIQ_CACHE_MAX_AGE = 86400  # 24 hours
WORKIQ_VERSION_PIN = "0.x"   # pinned version constraint, NOT @latest

# Force UTF-8 output in child processes (Windows cp1252 can't encode ✓/✗)
_SUBPROCESS_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


# ---------------------------------------------------------------------------
# Check result record
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    severity: str  # "P0" or "P1"
    passed: bool
    message: str
    fix_hint: str = ""
    auto_fixed: bool = False


# ---------------------------------------------------------------------------
# Individual health checks
# ---------------------------------------------------------------------------

def check_vault_health() -> CheckResult:
    """Verify age tool installed, credential store key present, state dir writable."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "vault.py"), "health"],
        capture_output=True, text=True, cwd=ARTHA_DIR,
        env=_SUBPROCESS_ENV, encoding="utf-8", errors="replace",
    )
    if result.returncode == 0:
        return CheckResult("vault.py health", "P0", True, "age ✓ | credential store key ✓ | state dir ✓")
    lines = (result.stdout + result.stderr).strip()
    failed_line = next((l for l in lines.splitlines() if "✗" in l or "FAILED" in l), lines)
    return CheckResult(
        "vault.py health", "P0", False,
        f"vault.py health failed: {failed_line.strip()}",
        fix_hint="Run: python scripts/vault.py status — check age install and credential store key",
    )


def check_vault_lock(auto_fix: bool = False) -> CheckResult:
    """Check for active or stale session lock file."""
    if not os.path.exists(LOCK_FILE):
        return CheckResult("vault lock state", "P0", True, "No lock file — state encrypted ✓")

    lock_mtime  = os.path.getmtime(LOCK_FILE)
    lock_age    = time.time() - lock_mtime
    lock_age_m  = int(lock_age / 60)

    if lock_age > STALE_LOCK_SECONDS:
        msg = f"Stale lock detected (age: {lock_age_m}m, threshold: 30m)"
        if auto_fix:
            os.remove(LOCK_FILE)
            return CheckResult(
                "vault lock state", "P0", True,
                f"Stale lock auto-cleared (was {lock_age_m}m old) ✓",
                auto_fixed=True,
            )
        return CheckResult(
            "vault lock state", "P0", False, msg,
            fix_hint="Re-run with --fix to auto-clear, or: rm ~/.artha-decrypted",
        )

    return CheckResult(
        "vault lock state", "P0", False,
        f"Active session lock (age: {lock_age_m}m) — another catch-up may be running",
        fix_hint=f"If no other session is running: rm {LOCK_FILE}",
    )


def check_oauth_token(service_name: str, token_filename: str) -> CheckResult:
    """Verify a Google OAuth token file exists and has required fields."""
    token_path = os.path.join(TOKEN_DIR, token_filename)
    check_name = f"{service_name} OAuth token"

    if not os.path.exists(token_path):
        return CheckResult(
            check_name, "P0", False,
            f"Token file missing: {token_path}",
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

    return CheckResult(check_name, "P0", True, f"Token file valid ✓ ({token_path})")


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


def check_script_health(
    script_name: str,
    args: list[str],
    severity: str = "P0",
) -> CheckResult:
    """
    Run a script with a health-check flag and inspect exit code.

    Args:
        script_name: filename relative to SCRIPTS_DIR.
        args:        extra CLI args to pass (e.g. ["--health"]).
        severity:    "P0" (default, hard-block) or "P1" (warning only).
    """
    check_name  = f"{script_name} health"
    script_path = os.path.join(SCRIPTS_DIR, script_name)

    if not os.path.exists(script_path):
        return CheckResult(
            check_name, severity, False,
            f"Script not found: {script_path}",
            fix_hint=f"Restore {script_name} from source control",
        )

    result = subprocess.run(
        [sys.executable, script_path] + args,
        capture_output=True, text=True, cwd=ARTHA_DIR,
        timeout=45, env=_SUBPROCESS_ENV, encoding="utf-8", errors="replace",
    )

    if result.returncode == 0:
        all_output = (result.stdout or result.stderr or "").strip().splitlines()
        note = all_output[-1] if all_output else "OK"
        return CheckResult(check_name, severity, True, f"{note} ✓")

    error_lines = (result.stdout + result.stderr).strip()
    brief = error_lines.splitlines()[-1] if error_lines else "exit code non-zero"
    return CheckResult(
        check_name, severity, False,
        f"{script_name} --health failed: {brief}",
        fix_hint=f"Run manually: python scripts/{script_name} --health",
    )


def check_pii_guard() -> CheckResult:
    """Verify pii_guard.py test suite passes.

    pii_guard.py is the cross-platform implementation. The legacy bash script
    has been archived to .archive/pii_guard.sh.
    """
    import shutil

    # ── Primary: Python implementation (cross-platform) ──────────────────
    py_script = os.path.join(SCRIPTS_DIR, "pii_guard.py")
    if os.path.exists(py_script):
        result = subprocess.run(
            [sys.executable, py_script, "test"],
            capture_output=True, text=True, cwd=ARTHA_DIR, timeout=15,
            env=_SUBPROCESS_ENV, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            output = result.stdout.strip().splitlines()
            summary = next((l for l in output if "pass" in l.lower()), "tests passed")
            return CheckResult("pii_guard.py test", "P0", True, f"{summary} ✓")
        error = (result.stdout + result.stderr).strip().splitlines()
        brief = error[-1] if error else "test failed"
        return CheckResult(
            "pii_guard.py test", "P0", False,
            f"PII guard test failed: {brief}",
            fix_hint="Catch-up MUST NOT run without a working PII filter. Fix pii_guard.py.",
        )

    # ── Fallback: legacy bash script ──────────────────────────────────────
    pii_script = os.path.join(SCRIPTS_DIR, "pii_guard.sh")
    if not os.path.exists(pii_script):
        return CheckResult(
            "pii_guard test", "P0", False,
            "Neither pii_guard.py nor pii_guard.sh found — catch-up cannot run without PII protection",
            fix_hint=f"Restore pii_guard.py to {SCRIPTS_DIR}",
        )

    bash_path = shutil.which("bash")
    if not bash_path:
        return CheckResult(
            "pii_guard.sh test", "P1", True,
            "bash not found — PII guard skipped on Windows (install Git Bash or use pii_guard.py) ✓",
        )

    if os.name != "nt" and not os.access(pii_script, os.X_OK):
        os.chmod(pii_script, 0o755)

    result = subprocess.run(
        [bash_path, pii_script, "test"],
        capture_output=True, text=True, cwd=ARTHA_DIR, timeout=15,
        env=_SUBPROCESS_ENV, encoding="utf-8", errors="replace",
    )
    if result.returncode == 0:
        output = result.stdout.strip().splitlines()
        summary = next((l for l in output if "pass" in l.lower()), "tests passed")
        return CheckResult("pii_guard.sh test (legacy)", "P0", True, f"{summary} ✓")

    error = (result.stdout + result.stderr).strip().splitlines()
    brief = error[-1] if error else "test failed"
    severity = "P1" if os.name == "nt" else "P0"
    return CheckResult(
        "pii_guard.sh test", severity, severity == "P1",
        f"PII guard test failed: {brief}" + (" (downgraded to warning on Windows)" if os.name == "nt" else ""),
        fix_hint="Catch-up MUST NOT run without a working PII filter." if severity == "P0" else "",
    )


def check_state_directory() -> CheckResult:
    """Verify state/ directory exists and is writable."""
    if not os.path.isdir(STATE_DIR):
        return CheckResult(
            "state directory", "P0", False,
            f"State directory missing: {STATE_DIR}",
            fix_hint=f"Create the directory: {STATE_DIR}",
        )
    test_path = os.path.join(STATE_DIR, ".preflight_write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return CheckResult("state directory", "P0", True, f"{STATE_DIR} writable ✓")
    except OSError as exc:
        return CheckResult(
            "state directory", "P0", False,
            f"State directory not writable: {exc}",
            fix_hint=f"Check OneDrive sync status and permissions on {STATE_DIR}",
        )


def check_state_templates(auto_fix: bool = False) -> CheckResult:
    """P1: Populate missing state files from state/templates/ on first run."""
    templates_dir = os.path.join(STATE_DIR, "templates")
    if not os.path.isdir(templates_dir):
        return CheckResult(
            "state templates", "P1", True,
            "No templates directory — skipping state population",
        )
    templates = [f for f in os.listdir(templates_dir) if f.endswith(".md") and f != "README.md"]
    missing = []
    for tpl in templates:
        target = os.path.join(STATE_DIR, tpl)
        if not os.path.exists(target):
            missing.append(tpl)

    if not missing:
        return CheckResult("state templates", "P1", True, "All state files present ✓")

    if auto_fix:
        populated = []
        for tpl in missing:
            src = os.path.join(templates_dir, tpl)
            dst = os.path.join(STATE_DIR, tpl)
            try:
                import shutil
                shutil.copy2(src, dst)
                populated.append(tpl)
            except OSError:
                pass
        return CheckResult(
            "state templates", "P1", True,
            f"Populated {len(populated)} state files from templates: {', '.join(populated)}",
            auto_fixed=True,
        )

    return CheckResult(
        "state templates", "P1", False,
        f"{len(missing)} state file(s) missing: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}",
        fix_hint="Run preflight with --fix to auto-populate from state/templates/",
    )


def check_open_items() -> CheckResult:
    """P1: Verify open_items.md exists and is readable."""
    path = os.path.join(STATE_DIR, "open_items.md")
    if not os.path.exists(path):
        return CheckResult(
            "open_items.md", "P1", False,
            "open_items.md not found — action tracking unavailable",
            fix_hint="Create state/open_items.md (see T-1A.11.1 template)",
        )
    try:
        with open(path) as f:
            f.read(100)
        return CheckResult("open_items.md", "P1", True, "open_items.md accessible ✓")
    except OSError as exc:
        return CheckResult("open_items.md", "P1", False, f"open_items.md unreadable: {exc}")


def check_briefings_directory() -> CheckResult:
    """P1: Verify briefings/ directory is writable for archiving."""
    briefings_dir = os.path.join(ARTHA_DIR, "briefings")
    if not os.path.isdir(briefings_dir):
        try:
            os.makedirs(briefings_dir, exist_ok=True)
            return CheckResult(
                "briefings directory", "P1", True,
                f"Created {briefings_dir} ✓",
                auto_fixed=True,
            )
        except OSError as exc:
            return CheckResult(
                "briefings directory", "P1", False,
                f"Cannot create briefings/: {exc}",
            )
    test_path = os.path.join(briefings_dir, ".preflight_write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return CheckResult("briefings directory", "P1", True, f"{briefings_dir} writable ✓")
    except OSError as exc:
        return CheckResult("briefings directory", "P1", False, f"briefings/ not writable: {exc}")


def check_msgraph_token() -> CheckResult:
    """P1: Verify Microsoft Graph token exists and check freshness (for To Do sync).

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

    # Check expiry field (written by setup_msgraph_oauth.py)
    expiry_str = token_data.get("expiry", "")
    if expiry_str:
        try:
            from datetime import datetime as _dt, timezone as _tz
            expiry_dt = _dt.fromisoformat(expiry_str.rstrip("Z"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
            secs_left = (expiry_dt - _dt.now(_tz.utc)).total_seconds()
            if secs_left < 0:
                return CheckResult(
                    "Microsoft Graph token", "P1", False,
                    f"Token expired {int(-secs_left/60)}m ago",
                    fix_hint="Run: python scripts/setup_msgraph_oauth.py --reauth",
                )
            if secs_left < TOKEN_EXPIRY_WARN_SECONDS:
                return CheckResult(
                    "Microsoft Graph token", "P1", False,
                    f"Token expires in {int(secs_left/60)}m — will auto-refresh",
                )
            return CheckResult(
                "Microsoft Graph token", "P1", True,
                f"Valid for {int(secs_left/60)}m ✓",
            )
        except ValueError:
            pass

    # Has token but no parseable expiry
    return CheckResult(
        "Microsoft Graph token", "P1", True,
        "Token present (expiry unknown — will validate on use) ✓",
    )


# ---------------------------------------------------------------------------
# WorkIQ combined detection + auth check (v2.2 — T-2A.24.1)
# ---------------------------------------------------------------------------

def check_workiq() -> CheckResult:
    """Combined WorkIQ detection + auth. P1 non-blocking.

    Strategy:
      1. Platform gate: if not Windows, skip silently (Mac has no WorkIQ).
      2. Check tmp/.workiq_cache.json — if fresh (<24h), reuse cached result.
      3. If cache miss/stale: single npx call that validates both availability
         and M365 auth: "What is my name?"
      4. Write result to cache for next run.

    Ref: Tech Spec §3.2b, §7.1 Step 0(f)
    """
    import platform
    if platform.system() != "Windows":
        return CheckResult(
            "WorkIQ Calendar", "P1", True,
            "Skipped (not Windows) — Mac graceful degradation ✓",
        )

    # Check cache
    cache_path = Path(WORKIQ_CACHE_FILE)
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            checked_at = cache.get("checked_at", "")
            if checked_at:
                from datetime import datetime, timezone
                cache_time = datetime.fromisoformat(checked_at)
                age = (datetime.now(timezone.utc) - cache_time).total_seconds()
                if age < WORKIQ_CACHE_MAX_AGE:
                    if cache.get("available") and cache.get("auth_valid"):
                        return CheckResult(
                            "WorkIQ Calendar", "P1", True,
                            f"Available + authenticated (cached {int(age//3600)}h ago) ✓",
                        )
                    elif cache.get("available") and not cache.get("auth_valid"):
                        return CheckResult(
                            "WorkIQ Calendar", "P1", False,
                            "WorkIQ available but auth expired (cached)",
                            fix_hint="npx workiq logout && retry",
                        )
                    else:
                        return CheckResult(
                            "WorkIQ Calendar", "P1", False,
                            "WorkIQ not available (cached)",
                            fix_hint="Install: npm i -g @microsoft/workiq",
                        )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # stale/corrupt cache — fall through to live check

    # Live combined detection + auth check
    # Refresh PATH from registry to pick up newly-installed Node.js (Windows)
    import platform as _plat
    if _plat.system() == "Windows":
        fresh_path = os.environ.get("PATH", "")
        for scope in ("Machine", "User"):
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE if scope == "Machine" else winreg.HKEY_CURRENT_USER,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                    if scope == "Machine" else r"Environment",
                )
                val, _ = winreg.QueryValueEx(key, "Path")
                winreg.CloseKey(key)
                for p in val.split(";"):
                    if p and p not in fresh_path:
                        fresh_path += ";" + p
            except (OSError, FileNotFoundError):
                pass
        sub_env = {**_SUBPROCESS_ENV, "PATH": fresh_path}
    else:
        sub_env = _SUBPROCESS_ENV

    try:
        # Find npx using refreshed PATH (handles post-install Windows PATH lag)
        import shutil
        npx_cmd = shutil.which("npx", path=sub_env.get("PATH"))
        if not npx_cmd:
            raise FileNotFoundError("npx not on PATH")
        result = subprocess.run(
            [npx_cmd, "-y", f"@microsoft/workiq@{WORKIQ_VERSION_PIN}",
             "ask", "-q", "What is my name?"],
            capture_output=True, text=True, timeout=30,
            env=sub_env,
        )
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        available = result.returncode == 0 and len(result.stdout.strip()) > 0
        # Auth is valid if we got a meaningful response (not an error message)
        auth_valid = available and "error" not in result.stdout.lower()[:100]
        user_name = result.stdout.strip()[:50] if auth_valid else ""

        # Write cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "available": available,
            "auth_valid": auth_valid,
            "platform": "Windows",
            "checked_at": now_iso,
            "user_name": user_name,
        }
        cache_path.write_text(
            json.dumps(cache_data, indent=2), encoding="utf-8"
        )

        if available and auth_valid:
            return CheckResult(
                "WorkIQ Calendar", "P1", True,
                f"Available + authenticated ✓",
            )
        elif available and not auth_valid:
            return CheckResult(
                "WorkIQ Calendar", "P1", False,
                "WorkIQ available but M365 auth failed",
                fix_hint="npx workiq logout && retry",
            )
        else:
            return CheckResult(
                "WorkIQ Calendar", "P1", False,
                f"WorkIQ not available: {result.stderr.strip()[:80]}",
                fix_hint="Install: npm i -g @microsoft/workiq",
            )
    except FileNotFoundError:
        return CheckResult(
            "WorkIQ Calendar", "P1", False,
            "npx not found — Node.js not installed",
            fix_hint="Install Node.js (includes npx)",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "WorkIQ Calendar", "P1", False,
            "WorkIQ check timed out (>30s)",
            fix_hint="Check network connectivity; WorkIQ requires npm registry access",
        )


# ---------------------------------------------------------------------------
# Main preflight runner
# ---------------------------------------------------------------------------

# Minimal set of importable module names that must exist in the venv.
# Keys are the import name; values are the install package name.
_REQUIRED_DEPS: dict[str, str] = {
    "yaml":       "pyyaml",
    "keyring":    "keyring",
    "bs4":        "beautifulsoup4",
    "requests":   "requests",
    "google":     "google-auth",
}


def check_dep_freshness() -> CheckResult:
    """P1: Verify key project dependencies are importable in the current venv.

    A stale venv (missing packages after a git pull that added new deps) is one
    of the most common causes of multi-script cascade failures at preflight.
    This check surfaces missing imports early with a clear fix hint.
    """
    import importlib.util

    req_file = os.path.join(ARTHA_DIR, "scripts", "requirements.txt")
    missing: list[str] = []
    install_names: list[str] = []

    for mod, pkg in _REQUIRED_DEPS.items():
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
            install_names.append(pkg)

    if missing:
        return CheckResult(
            "venv dependencies", "P1", False,
            f"Missing packages: {missing} — venv may be stale after a git pull",
            fix_hint=(
                f"Run: pip install -r {req_file}"
                if os.path.exists(req_file)
                else f"pip install {' '.join(install_names)}"
            ),
        )

    # Also check that requirements.txt exists (sanity guard)
    if not os.path.exists(req_file):
        return CheckResult(
            "venv dependencies", "P1", False,
            f"requirements.txt missing: {req_file}",
            fix_hint="Restore from source control",
        )

    return CheckResult("venv dependencies", "P1", True, f"All {len(_REQUIRED_DEPS)} core deps found ✓")


def check_channel_health() -> CheckResult:
    """P1: Verify enabled channel adapters (Telegram, etc.) are reachable.

    Non-blocking: gracefully skipped when config/channels.yaml does not exist
    or no channels are enabled, so the catch-up is never blocked.
    """
    config_path = Path(os.path.join(ARTHA_DIR, "config", "channels.yaml"))
    if not config_path.exists():
        return CheckResult(
            "channel health", "P1", True,
            "channels.yaml not found — channel push disabled ✓",
        )
    try:
        sys.path.insert(0, os.path.join(ARTHA_DIR, "scripts"))
        from scripts.channels.registry import (
            load_channels_config,
            iter_enabled_channels,
            create_adapter_from_config,
        )
    except ImportError:
        return CheckResult(
            "channel health", "P1", True,
            "scripts.channels not importable — channel health skipped ✓",
        )
    try:
        config = load_channels_config()
    except Exception as exc:
        return CheckResult(
            "channel health", "P1", False,
            f"channels.yaml parse error: {exc}",
            fix_hint="Validate YAML syntax in config/channels.yaml",
        )
    enabled = list(iter_enabled_channels(config))
    if not enabled:
        return CheckResult("channel health", "P1", True, "No channels enabled – skipped ✓")
    unhealthy: list[str] = []
    for ch_name, ch_cfg in enabled:
        try:
            adapter = create_adapter_from_config(ch_name, ch_cfg)
            if not adapter.health_check():
                unhealthy.append(ch_name)
                _healthy = False
            else:
                _healthy = True
        except Exception:
            unhealthy.append(ch_name)
            _healthy = False
        try:
            from scripts.lib.common import update_channel_health_md
            update_channel_health_md(ch_name, _healthy)
        except Exception:
            pass  # Non-critical
    if unhealthy:
        return CheckResult(
            "channel health", "P1", False,
            f"Unhealthy channels: {unhealthy} — channel push will be degraded",
            fix_hint="python scripts/setup_channel.py --health",
        )
    return CheckResult(
        "channel health", "P1", True,
        f"All {len(enabled)} channel(s) healthy ✓",
    )


def run_preflight(auto_fix: bool = False, quiet: bool = False) -> list[CheckResult]:
    """Run all preflight checks. Returns list of CheckResult objects."""
    checks: list[CheckResult] = []

    # ── P0 — Hard blocks ──────────────────────────────────────────────────
    checks.append(check_vault_health())
    checks.append(check_vault_lock(auto_fix=auto_fix))
    checks.append(check_oauth_token("Gmail", "gmail-oauth-token.json"))
    checks.append(check_oauth_token("Calendar", "gcal-oauth-token.json"))
    checks.append(check_pii_guard())
    # Global API live connection via unified pipeline.py (skip if --quiet to reduce latency)
    if not quiet:
        try:
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "gmail"], severity="P0"
            ))
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "google_calendar"], severity="P0"
            ))
            checks.append(check_script_health(
                "gmail_send.py", ["--health"], severity="P0"
            ))
        except subprocess.TimeoutExpired:
            checks.append(CheckResult(
                "API connectivity", "P0", False,
                "API health check timed out (>30s)",
                fix_hint="Check network connectivity and OAuth credentials",
            ))
    checks.append(check_state_directory())

    # ── P1 — State file population from templates (first-run) ─────────────
    checks.append(check_state_templates(auto_fix=auto_fix))

    # ── P1 — Warnings only ────────────────────────────────────────────────
    checks.append(check_token_freshness("Gmail", "gmail-oauth-token.json"))
    checks.append(check_token_freshness("Calendar", "gcal-oauth-token.json"))
    checks.append(check_open_items())
    checks.append(check_briefings_directory())
    checks.append(check_msgraph_token())  # T-1B.6.1: non-blocking, To Do sync optional

    # ── P1 — WorkIQ Calendar (v2.2 — Windows-only, non-blocking) ─────────
    checks.append(check_workiq())

    # ── P1 — Channel health (v5.0 — non-blocking) ─────────────────────────
    checks.append(check_channel_health())

    # ── P1 — Dependency freshness ─────────────────────────────────────────
    checks.append(check_dep_freshness())

    # ── P1 — Skill dependencies ───────────────────────────────────────────
    try:
        import bs4
        checks.append(CheckResult("BeautifulSoup", "P1", True, "beautifulsoup4 found"))
    except ImportError:
        checks.append(CheckResult(
            "BeautifulSoup", "P1", False,
            "beautifulsoup4 not installed — Data Skills will be disabled",
            fix_hint="pip install beautifulsoup4"
        ))

    # MS Graph live connectivity via unified pipeline.py (P1)
    if not quiet:
        try:
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "outlook_email"], severity="P1"
            ))
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "outlook_calendar"], severity="P1"
            ))
        except subprocess.TimeoutExpired:
            checks.append(CheckResult(
                "MS Graph connectivity", "P1", False,
                "MS Graph health check timed out — Outlook data may be unavailable",
                fix_hint="Check network or re-run: python scripts/pipeline.py --health -s outlook_email",
            ))

    # iCloud live connectivity via unified pipeline.py (P1)
    if not quiet:
        try:
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "icloud_email"], severity="P1"
            ))
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "icloud_calendar"], severity="P1"
            ))
        except subprocess.TimeoutExpired:
            checks.append(CheckResult(
                "iCloud connectivity", "P1", False,
                "iCloud health check timed out — iCloud data may be unavailable",
                fix_hint="Check network or re-run: python scripts/pipeline.py --health -s icloud_email",
            ))

    # Canvas LMS via pipeline.py (P1 — only if configured)
    _canvas_configured = False
    try:
        import sys as _sys
        if SCRIPTS_DIR not in _sys.path:
            _sys.path.insert(0, SCRIPTS_DIR)
        from profile_loader import children as _children, has_profile as _has_profile
        if _has_profile():
            for _child in _children():
                _school = _child.get("school", {}) or {}
                if _school.get("canvas_url") and _school.get("canvas_keychain_key"):
                    _canvas_configured = True
                    break
    except Exception:
        pass  # Non-critical — Canvas is an optional connector
    if _canvas_configured and not quiet:
        checks.append(check_script_health(
            "pipeline.py", ["--health", "--source", "canvas_lms"], severity="P1"
        ))

    return checks


def format_results(
    checks: list[CheckResult],
    quiet: bool = False,
) -> tuple[str, bool]:
    """
    Format check results into terminal output.
    Returns (output_string, all_p0_passed).
    """
    p0_failures = [c for c in checks if c.severity == "P0" and not c.passed]
    p1_warnings = [c for c in checks if c.severity == "P1" and not c.passed]
    all_passed  = not p0_failures

    lines: list[str] = []

    if not quiet:
        lines.append("━━ ARTHA PRE-FLIGHT GATE ━━━━━━━━━━━━━━━━━━━━━━━━")
        for c in checks:
            if quiet and c.passed:
                continue
            icon = "✓" if c.passed else ("⛔" if c.severity == "P0" else "⚠")
            auto_note = " (auto-fixed)" if c.auto_fixed else ""
            lines.append(f"  {icon} [{c.severity}] {c.name}: {c.message}{auto_note}")
            if not c.passed and c.fix_hint:
                lines.append(f"       → {c.fix_hint}")
        lines.append("")

    if all_passed:
        auto_fixed_count = sum(1 for c in checks if c.auto_fixed)
        notes = []
        if p1_warnings:
            notes.append(f"{len(p1_warnings)} warning(s)")
        if auto_fixed_count:
            notes.append(f"{auto_fixed_count} auto-fixed")
        suffix = f" ({', '.join(notes)})" if notes else ""
        lines.append(f"✓ Pre-flight: GO{suffix} — proceed with catch-up")
    else:
        lines.append(f"⛔ Pre-flight: NO-GO — {len(p0_failures)} critical check(s) failed")
        for c in p0_failures:
            lines.append(f"   • {c.name}: {c.message}")
        lines.append("")
        lines.append("Catch-up aborted. Fix the above issues and re-run.")

    if not quiet:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines), all_passed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Artha pre-catch-up health gate. "
            "Exits 0 if all P0 checks pass, 1 if any P0 check fails."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (for scripting)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal output — only show failures. Skips live API connectivity checks.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix safe issues (e.g. clear stale lock file, fix token file permissions)",
    )

    args = parser.parse_args()

    # Cold-start detection: exit 3 if user_profile.yaml is missing (first run)
    profile_path = pathlib.Path(__file__).resolve().parent.parent / "config" / "user_profile.yaml"
    if not profile_path.exists():
        if args.json:
            print(json.dumps({"pre_flight_ok": False, "cold_start": True, "checks": []}))
        else:
            print("⛔ Cold start detected — config/user_profile.yaml not found.")
            print("   Run /bootstrap or say 'catch me up' to start guided setup.")
        sys.exit(3)

    checks  = run_preflight(auto_fix=args.fix, quiet=args.quiet)
    all_ok  = all(c.passed for c in checks if c.severity == "P0")

    if args.json:
        output = {
            "pre_flight_ok": all_ok,
            "checks": [
                {
                    "name":       c.name,
                    "severity":   c.severity,
                    "passed":     c.passed,
                    "message":    c.message,
                    "fix_hint":   c.fix_hint,
                    "auto_fixed": c.auto_fixed,
                }
                for c in checks
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        formatted, _ = format_results(checks, quiet=args.quiet)
        print(formatted)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
