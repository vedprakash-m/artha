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
  3  pii_guard.py test        — PII filter script executable and passing tests
  4  pipeline.py --health -s google_calendar — Calendar API connection live
  5  gmail_send.py --health   — Send auth valid (token present)
  6  State directory          — state/ directory writable

P1 checks (logged as warnings, do NOT block catch-up):
  7  Gmail OAuth token        — token file exists and is not structurally broken
  8  Calendar OAuth token     — token file exists and is not structurally broken
  9  pipeline.py --health -s gmail   — Gmail API connection live (warn, skip Gmail data)
  10 open_items.md readable        — persistent action file accessible
  11 Token freshness               — tokens not within 5 min of expiry
  12 Briefings directory           — briefings/ directory writable
  13 Microsoft Graph token         — To Do / email / calendar token valid
  14 pipeline.py --health -s outlook_email    — Outlook email API live (Mail.Read)
  15 pipeline.py --health -s outlook_calendar — Outlook Calendar API live (Calendars.Read)
  16 pipeline.py --health -s icloud_email     — iCloud Mail IMAP live (app-specific password)
  17 pipeline.py --health -s icloud_calendar  — iCloud Calendar CalDAV live
  18 WorkIQ Calendar               — WorkIQ M365 detection + auth (Windows only, v2.2)
  19 Bridge health                 — peer machine health + bridge dir writable (dual-setup.md)

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


def _rel(path: str) -> str:
    """Return path relative to ARTHA_DIR (uses $ARTHA_DIR prefix for display).

    Avoids leaking username / absolute directory structure in console output.
    Falls back to the basename if the path isn't under ARTHA_DIR.
    """
    try:
        rel = os.path.relpath(path, ARTHA_DIR)
        # relpath on a different drive (Windows) may return an absolute path
        if rel.startswith("..") or os.path.isabs(rel):
            return os.path.basename(path)
        return "$ARTHA_DIR/" + rel.replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


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

    @property
    def ok(self) -> bool:
        """Alias for .passed — used by tests."""
        return self.passed


# ---------------------------------------------------------------------------
# Individual health checks
# ---------------------------------------------------------------------------

def check_keyring_backend() -> CheckResult:
    """P0: Verify keyring has a working backend before any credential operations.

    Fails early with an actionable message on headless Linux where no secret
    service is available, rather than letting vault.py fail silently later.
    """
    try:
        import keyring
        # A None result is fine (key not stored yet) — what we detect is an
        # outright backend failure raised as an exception.
        keyring.get_password("artha-keyring-probe", "preflight")
        return CheckResult("keyring backend", "P0", True, "keyring backend functional ✓")
    except Exception as exc:
        short = str(exc).splitlines()[0][:120]
        return CheckResult(
            "keyring backend", "P0", False,
            f"keyring backend unavailable: {short}",
            fix_hint=(
                "pip install secretstorage  (GNOME/KDE desktop) "
                "or  pip install keyrings.alt  (headless/server). "
                "See docs/troubleshooting.md#no-recommended-backend-was-available-linux"
            ),
        )


def check_vault_health() -> CheckResult:
    """Verify age tool installed, credential store key present, state dir writable.

    Exit code semantics from vault.py health:
      0 — fully healthy (hard + soft checks all pass)
      1 — hard failure (age not installed, key missing, state dir inaccessible) → P0 block
      2 — soft warnings only (.bak files, GFS unvalidated, key never exported)  → P1 advisory
    """
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "vault.py"), "health"],
        capture_output=True, text=True, cwd=ARTHA_DIR,
        env=_SUBPROCESS_ENV, encoding="utf-8", errors="replace",
    )
    if result.returncode == 0:
        return CheckResult("vault.py health", "P0", True, "age ✓ | credential store key ✓ | state dir ✓")
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 2:
        # Soft warnings (e.g. orphaned .bak files) — P1 advisory, not a hard block
        warn_line = next(
            (l for l in output.splitlines() if "⚠" in l and ".bak" in l), None
        ) or next(
            (l for l in output.splitlines() if "⚠" in l), output.split("\n")[0]
        )
        return CheckResult(
            "vault.py health", "P1", False,
            f"vault.py health: {warn_line.strip()}",
            fix_hint="Run: python3 scripts/vault.py encrypt  (clears .bak files and creates GFS backup)",
        )
    # returncode == 1: hard failure
    failed_line = next((l for l in output.splitlines() if "✗" in l or "FAILED" in l), output.split("\n")[0])
    return CheckResult(
        "vault.py health", "P0", False,
        f"vault.py health failed: {failed_line.strip()}",
        fix_hint="Run: python3 scripts/vault.py status — check age install and credential store key",
    )


def check_vault_lock(auto_fix: bool = False) -> CheckResult:
    """Check for active or stale session lock file.

    Stale locks (age > 30m OR locking PID no longer running) are auto-cleared
    unconditionally — they are evidence of a past crash, not an active session.
    Active locks (PID alive, age < 30m) are only cleared with --fix.
    The lock file path is always surfaced in error messages.
    """
    if not os.path.exists(LOCK_FILE):
        return CheckResult("vault lock state", "P0", True, "No lock file — state encrypted ✓")

    lock_mtime  = os.path.getmtime(LOCK_FILE)
    lock_age    = time.time() - lock_mtime
    lock_age_m  = int(lock_age / 60)

    # Determine if PID is still alive (read JSON lock if present)
    try:
        import json as _json
        lock_data = _json.loads(open(LOCK_FILE).read().strip())
        pid = lock_data.get("pid", 0)
    except Exception:
        pid = 0

    def _pid_alive(p: int) -> bool:
        if p <= 0:
            return False
        try:
            os.kill(p, 0)
            return True
        except OSError:
            return False

    is_stale = lock_age > STALE_LOCK_SECONDS or (pid > 0 and not _pid_alive(pid))

    if is_stale:
        # Auto-clear unconditionally — stale = previous crash, not live session
        try:
            os.remove(LOCK_FILE)
            reason = f"age: {lock_age_m}m" + (f", PID {pid} not running" if pid else "") 
            return CheckResult(
                "vault lock state", "P0", True,
                f"Stale lock auto-cleared ({reason}) ✓",
                auto_fixed=True,
            )
        except OSError as e:
            return CheckResult(
                "vault lock state", "P0", False,
                f"Stale lock detected ({lock_age_m}m) but could not auto-clear: {e}",
                fix_hint=f"Manually remove: rm \"{LOCK_FILE}\"",
            )

    # Active lock (PID alive, age < 30m)
    msg = f"Active session lock (age: {lock_age_m}m) — another catch-up may be running"
    if auto_fix:
        try:
            os.remove(LOCK_FILE)
            return CheckResult(
                "vault lock state", "P0", True,
                f"Active lock force-cleared via --fix (was {lock_age_m}m old) ✓",
                auto_fixed=True,
            )
        except OSError as e:
            pass
    return CheckResult(
        "vault lock state", "P0", False, msg,
        fix_hint=f"If no other session is running: rm \"{LOCK_FILE}\"  or re-run with --fix",
    )


def check_oauth_token(service_name: str, token_filename: str, severity: str = "P0") -> CheckResult:
    """Verify a Google OAuth token file exists and has required fields."""
    token_path = os.path.join(TOKEN_DIR, token_filename)
    check_name = f"{service_name} OAuth token"

    if not os.path.exists(token_path):
        return CheckResult(
            check_name, severity, False,
            f"Token file missing: {_rel(token_path)}",
            fix_hint="Run: python scripts/setup_google_oauth.py",
        )

    try:
        with open(token_path) as f:
            token_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult(
            check_name, severity, False,
            f"Token file unreadable or corrupt: {exc}",
            fix_hint="Run: python scripts/setup_google_oauth.py",
        )

    # Verify required fields are present
    required = ["refresh_token", "token_uri", "client_id", "client_secret"]
    missing  = [f for f in required if not token_data.get(f)]
    if missing:
        return CheckResult(
            check_name, severity, False,
            f"Token file missing fields: {missing}",
            fix_hint="Re-authenticate: python scripts/setup_google_oauth.py",
        )

    # Permissions check (POSIX only — no-op on Windows)
    if os.name != "nt":
        stat = os.stat(token_path)
        perms = oct(stat.st_mode)[-3:]
        if perms != "600":
            os.chmod(token_path, 0o600)  # auto-fix permissions

    return CheckResult(check_name, severity, True, f"Token file valid ✓ ({_rel(token_path)})")


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
            f"Script not found: {_rel(script_path)}",
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
            f"State directory missing: {_rel(STATE_DIR)}",
            fix_hint="Run: python scripts/preflight.py --fix",
        )
    test_path = os.path.join(STATE_DIR, ".preflight_write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return CheckResult("state directory", "P0", True, f"{_rel(STATE_DIR)} writable ✓")
    except OSError as exc:
        return CheckResult(
            "state directory", "P0", False,
            f"State directory not writable: {exc}",
            fix_hint=f"Check OneDrive sync status and permissions on {_rel(STATE_DIR)}",
        )


def check_state_directory() -> CheckResult:
    """Verify state/ directory exists and is writable."""
    if not os.path.isdir(STATE_DIR):
        return CheckResult(
            "state directory", "P0", False,
            f"State directory missing: {_rel(STATE_DIR)}",
            fix_hint="Run: python scripts/preflight.py --fix",
        )
    test_path = os.path.join(STATE_DIR, ".preflight_write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return CheckResult("state directory", "P0", True, f"{_rel(STATE_DIR)} writable ✓")
    except OSError as exc:
        return CheckResult(
            "state directory", "P0", False,
            f"State directory not writable: {exc}",
            fix_hint=f"Check OneDrive sync status and permissions on {_rel(STATE_DIR)}",
        )


def check_guardrails_yaml(force_no_guardrails: bool = False) -> CheckResult:
    """P0 (DEBT-004): Verify guardrails.yaml is present and parseable.

    Guardrails provide PII scanning, injection detection, and rate limiting.
    A missing or corrupt guardrails.yaml disables ALL safety chains — this is
    a hard-block, not a warning.  Use --force-no-guardrails only if you have
    a peer-reviewed reason and accept the audit trail entry.
    """
    cfg_path = os.path.join(ARTHA_DIR, "config", "guardrails.yaml")
    if force_no_guardrails:
        _audit_guardrail_force_override(cfg_path)
        return CheckResult(
            "guardrails.yaml", "P0", True,
            "FORCE_NO_GUARDRAILS override active — safety chains bypassed (audit logged)",
            fix_hint="Remove --force-no-guardrails to re-enable safety chains",
        )

    if not os.path.isfile(cfg_path):
        return CheckResult(
            "guardrails.yaml", "P0", False,
            f"guardrails.yaml missing: {_rel(cfg_path)}",
            fix_hint="Restore from git: git checkout config/guardrails.yaml",
        )
    try:
        import yaml as _yaml  # noqa: PLC0415
        with open(cfg_path, encoding="utf-8") as f:
            data = _yaml.safe_load(f)
        if not data or not isinstance(data, dict):
            return CheckResult(
                "guardrails.yaml", "P0", False,
                "guardrails.yaml parsed as empty/non-dict — all safety chains would be inactive",
                fix_hint="Restore from git: git checkout config/guardrails.yaml",
            )
        # Verify at least one enabled guardrail exists
        chains_found = sum(
            1 for section in ("input", "tool", "output")
            for spec in data.get(section, [])
            if spec.get("enabled", True)
        )
        if chains_found == 0:
            return CheckResult(
                "guardrails.yaml", "P0", False,
                "guardrails.yaml has no enabled guardrails — all safety chains inactive",
                fix_hint="Restore from git: git checkout config/guardrails.yaml",
            )
        return CheckResult(
            "guardrails.yaml", "P0", True,
            f"guardrails.yaml valid — {chains_found} enabled guardrail(s) ✓",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "guardrails.yaml", "P0", False,
            f"guardrails.yaml parse error: {exc}",
            fix_hint="Restore from git: git checkout config/guardrails.yaml",
        )


def _audit_guardrail_force_override(cfg_path: str) -> None:
    """Write FORCE_NO_GUARDRAILS entry to state/audit.md (best-effort)."""
    try:
        from datetime import datetime, timezone  # noqa: PLC0415
        audit_log = os.path.join(ARTHA_DIR, "state", "audit.md")
        if os.path.isfile(audit_log):
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry = f"[{ts}] FORCE_NO_GUARDRAILS | config: {cfg_path}\n"
            with open(audit_log, "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:  # noqa: BLE001
        pass



    """Return True if the file is an unpopulated bootstrap placeholder.

    Bootstrap stubs are created by setup.sh/setup.ps1 and contain the exact
    two-line body ``# Content\\nsome: value`` inside the YAML frontmatter.
    Any file that has been genuinely populated will have different frontmatter.
    We match only the exact fingerprint to avoid false positives on real data.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read(256)  # Only need the first few lines
        # Exact stub fingerprint: frontmatter starts with ---\n# Content\nsome: value
        return "# Content\nsome: value" in raw
    except OSError:
        return False


def _detect_conflict_files(state_dir: str) -> list[str]:
    """DEBT-005: Scan state_dir for OneDrive conflict copies.

    OneDrive conflict naming patterns (varies by OS locale and version):
      - ``finance-DESKTOP-ABC.md``   (machine-name suffix)
      - ``health (1).md``            (numbered copy)
      - ``estate-conflict.md``       (explicit 'conflict' keyword)
      - ``immigration-LAPTOP-XYZ.md``

    Returns a list of absolute paths to detected conflict files.
    """
    import re  # noqa: PLC0415
    # Patterns that reliably indicate a OneDrive conflict copy
    _CONFLICT_PATTERNS = [
        re.compile(r".*-DESKTOP-\w+\."),       # finance-DESKTOP-PC.md
        re.compile(r".*-LAPTOP-\w+\."),         # health-LAPTOP-XYZ.md
        re.compile(r".*-conflict\."),           # estate-conflict.md
        re.compile(r".*\s\(\d+\)\."),           # health (1).md
        re.compile(r".*-PC-\w+\."),             # misc Windows machine names
    ]
    conflicts: list[str] = []
    try:
        for entry in os.scandir(state_dir):
            if not entry.is_file():
                continue
            name = entry.name
            for pat in _CONFLICT_PATTERNS:
                if pat.match(name):
                    conflicts.append(entry.path)
                    break
    except OSError:
        pass
    return conflicts


def check_onedrive_conflicts() -> CheckResult:
    """P1 (DEBT-005): Detect OneDrive conflict copies in state/ directory.

    Two machines writing simultaneously produce conflict copies
    (e.g. ``finance-DESKTOP-ABC.md``).  These are NOT auto-merged; the
    user must diff and resolve manually.  See docs/sync-conflicts.md.
    """
    conflicts = _detect_conflict_files(STATE_DIR)
    if not conflicts:
        return CheckResult(
            "OneDrive conflicts", "P1", True,
            "No OneDrive conflict copies detected in state/ ✓",
        )
    names = [os.path.basename(p) for p in conflicts]
    return CheckResult(
        "OneDrive conflicts", "P1", False,
        f"⚠ {len(conflicts)} OneDrive conflict file(s) in state/: {', '.join(names)}",
        fix_hint=(
            "Diff the files, keep the one with the later updated_at timestamp, "
            "merge unique entries, then delete the conflict copy. "
            "See docs/sync-conflicts.md for step-by-step instructions."
        ),
    )


def check_state_templates(auto_fix: bool = False) -> CheckResult:
    """P1: Populate missing state files from state/templates/ on first run."""
    templates_dir = os.path.join(STATE_DIR, "templates")
    if not os.path.isdir(templates_dir):
        return CheckResult(
            "state templates", "P1", False,
            "state/templates/ not found — state files cannot be auto-populated",
            fix_hint="Run: python scripts/preflight.py --fix  (or use /bootstrap in your AI CLI)",
        )
    templates = [f for f in os.listdir(templates_dir) if f.endswith(".md") and f != "README.md"]
    missing = []
    stubs = []
    for tpl in templates:
        target = os.path.join(STATE_DIR, tpl)
        if not os.path.exists(target):
            missing.append(tpl)
        elif _is_bootstrap_stub(target):
            stubs.append(tpl)

    if not missing and not stubs:
        return CheckResult("state templates", "P1", True, "All state files present ✓")

    if auto_fix:
        populated = []
        for tpl in list(missing) + list(stubs):
            src = os.path.join(templates_dir, tpl)
            dst = os.path.join(STATE_DIR, tpl)
            # Special rule for health-check.md: only populate if file is truly absent
            # OR exists but has no structured YAML frontmatter (last_catch_up field).
            if tpl == "health-check.md" and os.path.exists(dst):
                try:
                    with open(dst, encoding="utf-8") as f:
                        header_lines = [f.readline() for _ in range(10)]
                    if any("last_catch_up" in line for line in header_lines):
                        continue  # Structured health-check already present — skip
                except OSError:
                    pass  # Unreadable — let the copy proceed
            try:
                import shutil
                shutil.copy2(src, dst)
                populated.append(tpl)
            except OSError:
                pass
        msg_parts = []
        if any(t in populated for t in missing):
            msg_parts.append(f"created {sum(1 for t in missing if t in populated)}")
        if any(t in populated for t in stubs):
            msg_parts.append(f"replaced {sum(1 for t in stubs if t in populated)} bootstrap stubs")
        msg = f"Populated {len(populated)} state files ({', '.join(msg_parts)}): {', '.join(populated)}"
        return CheckResult(
            "state templates", "P1", True,
            msg,
            auto_fixed=True,
        )

    all_needing_fix = missing + stubs
    stub_note = f" ({len(stubs)} are bootstrap stubs)" if stubs else ""
    return CheckResult(
        "state templates", "P1", False,
        f"{len(all_needing_fix)} state file(s) need population{stub_note}: {', '.join(all_needing_fix[:5])}{'…' if len(all_needing_fix) > 5 else ''}",
        fix_hint="Run preflight with --fix to auto-populate from state/templates/",
    )


def check_open_items(auto_fix: bool = False) -> CheckResult:
    """P1: Verify open_items.md exists and is readable. Auto-creates from template with --fix."""
    path = os.path.join(STATE_DIR, "open_items.md")
    if not os.path.exists(path):
        template_path = os.path.join(STATE_DIR, "templates", "open_items.md")
        if auto_fix and os.path.exists(template_path):
            try:
                import shutil
                shutil.copy2(template_path, path)
                return CheckResult(
                    "open_items.md", "P1", True,
                    "Created state/open_items.md from template ✓",
                    auto_fixed=True,
                )
            except OSError as exc:
                return CheckResult(
                    "open_items.md", "P1", False,
                    f"Could not create open_items.md: {exc}",
                    fix_hint="Create state/open_items.md manually",
                )
        return CheckResult(
            "open_items.md", "P1", False,
            "open_items.md not found — action tracking unavailable",
            fix_hint="Run: python scripts/preflight.py --fix  (auto-creates from template)",
        )
    try:
        with open(path, encoding="utf-8") as f:
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
                f"Created {_rel(briefings_dir)} ✓",
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
        return CheckResult("briefings directory", "P1", True, f"{_rel(briefings_dir)} writable ✓")
    except OSError as exc:
        return CheckResult("briefings directory", "P1", False, f"briefings/ not writable: {exc}")


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
            # Token expired — try silent refresh FIRST before alarming the user
            try:
                from setup_msgraph_oauth import ensure_valid_token  # type: ignore[import]
                refreshed = ensure_valid_token(warn_seconds=60)
                if refreshed and refreshed.get("access_token"):
                    new_expiry = refreshed.get("expiry", "")
                    return CheckResult(
                        "Microsoft Graph token", "P1", True,
                        f"Token was expired but auto-refreshed successfully ✓ (new expiry: {new_expiry})",
                    )
            except Exception:
                pass  # silent refresh failed — fall through to manual reauth message
            expired_msg = f"Token expired {int(-secs_left/60)}m ago (silent refresh failed)"
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


# ---------------------------------------------------------------------------
# Bridge health check (dual-setup.md — non-blocking P1)
# ---------------------------------------------------------------------------

def check_bridge_health() -> CheckResult:
    """P1 non-blocking bridge health check.

    Skipped silently if multi_machine.bridge_enabled is false.
    When enabled:
      - Verifies bridge directory exists and is writable
      - Checks peer machine health file for staleness (default 48 h)

    Ref: specs/dual-setup.md §5
    """
    import sys as _sys
    if SCRIPTS_DIR not in _sys.path:
        _sys.path.insert(0, SCRIPTS_DIR)

    try:
        import yaml as _yaml  # noqa: PLC0415
        config_path = Path(ARTHA_DIR) / "config" / "artha_config.yaml"
        if not config_path.exists():
            return CheckResult("Bridge", "P1", True, "Bridge: config absent — skipped")
        with open(config_path, encoding="utf-8") as _f:
            artha_config = _yaml.safe_load(_f) or {}

        mm = artha_config.get("multi_machine", {})
        if not mm.get("bridge_enabled", False):
            return CheckResult("Bridge", "P1", True, "Bridge: disabled (multi_machine.bridge_enabled=false)")

        from action_bridge import (  # noqa: PLC0415
            get_bridge_dir, detect_role, check_health_staleness, load_artha_config,
        )
        channels_path = Path(ARTHA_DIR) / "config" / "channels.yaml"
        channels_config: dict = {}
        if channels_path.exists():
            with open(channels_path, encoding="utf-8") as _f:
                channels_config = _yaml.safe_load(_f) or {}

        role = detect_role(channels_config)
        peer_role = "windows" if role == "proposer" else "mac"
        bridge_dir = get_bridge_dir(Path(ARTHA_DIR))

        # Check bridge directory accessible + writable
        if not bridge_dir.exists():
            try:
                bridge_dir.mkdir(parents=True, exist_ok=True)
                (bridge_dir / "proposals").mkdir(exist_ok=True)
                (bridge_dir / "results").mkdir(exist_ok=True)
            except OSError as exc:
                return CheckResult(
                    "Bridge", "P1", False,
                    f"Bridge dir not writable: {exc}",
                    fix_hint="Ensure OneDrive is syncing state/.action_bridge/",
                )

        stale_hours = int(mm.get("health_stale_hours", 48))
        is_stale, elapsed_h = check_health_staleness(bridge_dir, peer_role, stale_hours)

        if is_stale and elapsed_h == float("inf"):
            return CheckResult(
                "Bridge", "P1", False,
                f"Bridge: peer machine ({peer_role}) has never written a health file",
                fix_hint=f"Start Artha on the {peer_role} machine to initialise bridge",
            )
        if is_stale:
            return CheckResult(
                "Bridge", "P1", False,
                f"Bridge: peer machine ({peer_role}) last seen {elapsed_h:.0f}h ago (threshold: {stale_hours}h)",
                fix_hint=f"Ensure Artha is running on the {peer_role} machine",
            )

        return CheckResult(
            "Bridge", "P1", True,
            f"Bridge: OK — peer ({peer_role}) seen {elapsed_h:.1f}h ago",
        )

    except ImportError as exc:
        return CheckResult("Bridge", "P1", False, f"Bridge module unavailable: {exc}",
                           fix_hint="Ensure scripts/action_bridge.py is present")
    except Exception as exc:
        return CheckResult("Bridge", "P1", False, f"Bridge health check error: {exc}")


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
# ADO auth check (Work Domains — opt-in, non-blocking)
# ---------------------------------------------------------------------------

def check_ado_auth() -> CheckResult:
    """P1 non-blocking: Verify Azure CLI is available and has an active ADO session.

    Only runs if azure_devops.enabled is true in user_profile.yaml.
    Silently passes if the ADO integration is not configured.
    """
    # Check if azure_devops integration is configured and enabled
    ado_enabled = False
    ado_org = ""
    try:
        _sys = sys
        if SCRIPTS_DIR not in _sys.path:
            _sys.path.insert(0, SCRIPTS_DIR)
        import yaml as _yaml
        _profile_path = os.path.join(ARTHA_DIR, "config", "user_profile.yaml")
        if os.path.exists(_profile_path):
            with open(_profile_path, encoding="utf-8") as _f:
                _profile = _yaml.safe_load(_f) or {}
            _ado = (_profile.get("integrations") or {}).get("azure_devops") or {}
            ado_enabled = bool(_ado.get("enabled", False))
            ado_org = str(_ado.get("organization_url", "")).strip()
    except Exception:
        pass

    if not ado_enabled:
        return CheckResult(
            "Azure DevOps auth", "P1", True,
            "ADO integration not enabled — skipped ✓",
        )

    if not ado_org:
        return CheckResult(
            "Azure DevOps auth", "P1", False,
            "ADO enabled but organization_url not set",
            fix_hint="Add integrations.azure_devops.organization_url to user_profile.yaml",
        )

    # Try to get an ADO bearer token from Azure CLI
    ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
    az_candidates = [
        "az",
        r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
    ]
    for az_cmd in az_candidates:
        try:
            result = subprocess.run(
                [az_cmd, "account", "get-access-token",
                 "--resource", ADO_RESOURCE, "--output", "json"],
                capture_output=True, text=True, timeout=15,
                env=_SUBPROCESS_ENV,
            )
            if result.returncode == 0:
                import json as _json
                data = _json.loads(result.stdout)
                expires = data.get("expiresOn", "")[:16]
                return CheckResult(
                    "Azure DevOps auth", "P1", True,
                    f"Azure CLI ADO token valid (expires {expires}) ✓",
                )
            # Non-zero return: auth failure
            return CheckResult(
                "Azure DevOps auth", "P1", False,
                "Azure CLI is available but not authenticated",
                fix_hint="Run: az login --tenant <your-tenant-id>",
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return CheckResult(
                "Azure DevOps auth", "P1", False,
                "Azure CLI token request timed out",
                fix_hint="Check network connectivity and Azure CLI installation",
            )

    return CheckResult(
        "Azure DevOps auth", "P1", False,
        "Azure CLI not found — ADO connector requires az CLI",
        fix_hint=(
            "Install Azure CLI: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
        ),
    )


# ---------------------------------------------------------------------------
# Home Assistant connectivity check (ARTHA-IOT Wave 1 — P1 non-blocking)
# ---------------------------------------------------------------------------

def check_ha_connectivity() -> CheckResult:
    """P1 non-blocking: Verify HA is reachable and the token is valid.

    Only runs if homeassistant.enabled is true in connectors.yaml.
    Silently passes (skip message) if the connector is disabled.
    Returns a warning (not failure) when off-LAN — normal for travel/work.
    """
    _name = "Home Assistant"

    # Load connector config
    _connectors_path = os.path.join(ARTHA_DIR, "config", "connectors.yaml")
    if not os.path.exists(_connectors_path):
        return CheckResult(_name, "P1", True, "connectors.yaml not found — skipped ✓")

    try:
        import yaml as _yaml
        with open(_connectors_path, encoding="utf-8") as _f:
            _cfg = _yaml.safe_load(_f) or {}
        _ha = (_cfg.get("connectors") or {}).get("homeassistant") or {}
    except Exception as exc:
        return CheckResult(_name, "P1", True, f"Could not read connectors.yaml ({exc}) — skipped ✓")

    if not _ha.get("enabled", False):
        return CheckResult(_name, "P1", True, "HA connector not enabled — skipped ✓")

    _ha_url = ((_ha.get("fetch") or {}).get("ha_url") or "").rstrip("/")
    if not _ha_url:
        return CheckResult(
            _name, "P1", False,
            "HA connector enabled but ha_url is empty",
            fix_hint="Run: python scripts/setup_ha_token.py",
        )

    # Load token from keyring
    try:
        import keyring as _keyring
        _token = _keyring.get_password("artha-ha-token", "artha") or ""
    except Exception:
        _token = ""

    if not _token:
        return CheckResult(
            _name, "P1", False,
            "HA token not found in system keyring",
            fix_hint="Run: python scripts/setup_ha_token.py",
        )

    # Attempt health check via the connector's own health_check()
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from connectors.homeassistant import health_check as _ha_health  # type: ignore[import]
        _fetch_cfg = _ha.get("fetch") or {}
        _ok = _ha_health(
            {"provider": "homeassistant", "method": "api_key", "api_key": _token},
            **{k: v for k, v in _fetch_cfg.items() if k != "handler"},
        )
        if _ok:
            return CheckResult(_name, "P1", True, f"HA reachable at {_ha_url} ✓")
        return CheckResult(
            _name, "P1", False,
            f"HA health check returned False for {_ha_url}",
            fix_hint=(
                "Check HA is running and on home network. "
                "Re-run token setup if needed: python scripts/setup_ha_token.py"
            ),
        )
    except Exception as exc:
        _msg = str(exc)
        if "LAN gate" in _msg or "not reachable" in _msg or "Cannot connect" in _msg:
            return CheckResult(
                _name, "P1", True,
                f"HA not on current network (off-LAN) — skipped ✓ ({_ha_url})",
            )
        return CheckResult(
            _name, "P1", False,
            f"HA check error: {_msg[:120]}",
            fix_hint="Run: python scripts/pipeline.py --health --source homeassistant",
        )




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


def check_ext_agent_discovery() -> CheckResult:
    """P1 non-blocking: Scan config/agents/external/ for unregistered .agent.md files.

    Discovers new agent definitions dropped into the file-drop folder that have
    not yet been registered via agent-manager register. Only runs when the AR-9
    external agents feature is enabled in artha_config.yaml.
    """
    # Gate: only run if external agents feature is enabled
    try:
        import yaml as _yaml
        _cfg_path = os.path.join(ARTHA_DIR, "config", "artha_config.yaml")
        if not os.path.exists(_cfg_path):
            return CheckResult("ext agent discovery", "P1", True,
                               "artha_config.yaml absent — ext agent check skipped ✓")
        with open(_cfg_path, encoding="utf-8") as _f:
            _artha_cfg = _yaml.safe_load(_f) or {}
        _ea_block = (
            _artha_cfg.get("harness", {})
            .get("agentic", {})
            .get("external_agents", {})
        )
        if not _ea_block.get("enabled", False):
            return CheckResult("ext agent discovery", "P1", True,
                               "External agents not enabled — skipped ✓")
        if not _ea_block.get("discovery", {}).get("enabled", True):
            return CheckResult("ext agent discovery", "P1", True,
                               "Agent discovery disabled in config — skipped ✓")
    except Exception as exc:
        return CheckResult("ext agent discovery", "P1", True,
                           f"Could not read artha_config.yaml ({exc}) — skipped ✓")

    # Load registry to know already-registered agent names
    registered_names: set[str] = set()
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from lib.agent_registry import AgentRegistry  # noqa: PLC0415
        _registry_path = os.path.join(ARTHA_DIR, "config", "agents", "external-registry.yaml")
        if os.path.exists(_registry_path):
            _reg = AgentRegistry.load(os.path.join(ARTHA_DIR, "config"))
            registered_names = {a.name for a in (_reg.active_agents() if hasattr(_reg, 'active_agents') else [])}
    except Exception:
        pass  # Registry missing or not yet built — treat all drops as unregistered

    # Scan drop folder
    drop_dir = os.path.join(ARTHA_DIR, "config", "agents", "external")
    if not os.path.isdir(drop_dir):
        return CheckResult("ext agent discovery", "P1", True,
                           "config/agents/external/ not found — skipped ✓")

    agent_files = [
        f for f in os.listdir(drop_dir)
        if f.endswith(".agent.md") and f != "README.md"
    ]
    if not agent_files:
        return CheckResult("ext agent discovery", "P1", True,
                           "config/agents/external/ — no .agent.md files ✓")

    # Identify unregistered agents
    unregistered = []
    for fname in agent_files:
        agent_name = fname[:-len(".agent.md")]
        if agent_name not in registered_names:
            unregistered.append(agent_name)

    if not unregistered:
        return CheckResult(
            "ext agent discovery", "P1", True,
            f"config/agents/external/: {len(agent_files)} agent(s) all registered ✓",
        )

    names_str = ", ".join(unregistered[:5]) + ("…" if len(unregistered) > 5 else "")
    return CheckResult(
        "ext agent discovery", "P1", False,
        f"📡 Discovered {len(unregistered)} unregistered agent(s) in config/agents/external/: {names_str}",
        fix_hint=(
            "Run: python scripts/agent_manager.py register  "
            "(registers all unregistered .agent.md files from the drop folder)"
        ),
    )


def check_ext_agent_health() -> CheckResult:
    """P1 non-blocking: Report degraded or suspended registered external agents.

    Only runs when the AR-9 external agents feature is enabled.
    Surfaces agents in 'degraded' or 'suspended' state so operators can take action.

    EA-11b
    """
    # Gate: only run if external agents feature is enabled
    try:
        import yaml as _yaml
        _cfg_path = os.path.join(ARTHA_DIR, "config", "artha_config.yaml")
        if not os.path.exists(_cfg_path):
            return CheckResult("ext agent health", "P1", True,
                               "artha_config.yaml absent — ext agent health check skipped ✓")
        with open(_cfg_path, encoding="utf-8") as _f:
            _artha_cfg = _yaml.safe_load(_f) or {}
        _ea_block = (
            _artha_cfg.get("harness", {})
            .get("agentic", {})
            .get("external_agents", {})
        )
        if not _ea_block.get("enabled", False):
            return CheckResult("ext agent health", "P1", True,
                               "External agents not enabled — skipped ✓")
    except Exception as exc:
        return CheckResult("ext agent health", "P1", True,
                           f"Could not read artha_config.yaml ({exc}) — skipped ✓")

    # Load registry
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from lib.agent_registry import AgentRegistry  # noqa: PLC0415
        _registry_path = os.path.join(ARTHA_DIR, "config", "agents", "external-registry.yaml")
        if not os.path.exists(_registry_path):
            return CheckResult("ext agent health", "P1", True,
                               "No agent registry found — skipped ✓")
        _reg = AgentRegistry.load(os.path.join(ARTHA_DIR, "config"))
        _all_agents = list(_reg.all_agents()) if hasattr(_reg, "all_agents") else []
    except Exception as exc:
        return CheckResult("ext agent health", "P1", True,
                           f"Could not load agent registry ({exc}) — skipped ✓")

    if not _all_agents:
        return CheckResult("ext agent health", "P1", True,
                           "No registered agents ✓")

    degraded = [a.name for a in _all_agents if a.health.status == "degraded"]
    suspended = [a.name for a in _all_agents if a.health.status == "suspended"]

    if not degraded and not suspended:
        return CheckResult(
            "ext agent health", "P1", True,
            f"{len(_all_agents)} agent(s) all healthy ✓",
        )

    parts = []
    if degraded:
        parts.append(f"degraded: {', '.join(degraded[:3])}{'…' if len(degraded) > 3 else ''}")
    if suspended:
        parts.append(f"suspended: {', '.join(suspended[:3])}{'…' if len(suspended) > 3 else ''}")

    return CheckResult(
        "ext agent health", "P1", False,
        f"⚠️ {len(degraded) + len(suspended)} agent(s) need attention — {'; '.join(parts)}",
        fix_hint=(
            "Run: python scripts/agent_manager.py status  "
            "to review and reinstate suspended agents"
        ),
    )


def check_state_registry_staleness() -> CheckResult:
    """P1 non-blocking: Warn when state_registry entries exceed their max_staleness_hours.

    Reads config/state_registry.yaml and checks os.path.getmtime() for each entry
    that has a max_staleness_hours value.  Returns a single aggregate result.

    EAR-3 / PRD §3.5 (A2).
    """
    from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415

    registry_path = Path(os.path.join(ARTHA_DIR, "config", "state_registry.yaml"))
    if not registry_path.exists():
        return CheckResult(
            "state registry staleness", "P1", True,
            "config/state_registry.yaml absent — staleness check skipped ✓",
        )

    try:
        import yaml as _yaml  # noqa: PLC0415
        reg_data = _yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        entries = reg_data.get("state_files", []) or []
    except Exception as exc:
        return CheckResult(
            "state registry staleness", "P1", True,
            f"Could not read state_registry.yaml ({exc}) — skipped ✓",
        )

    if not entries:
        return CheckResult(
            "state registry staleness", "P1", True,
            "state_registry.yaml has no entries ✓",
        )

    now_ts = _dt.now(_tz.utc).timestamp()
    stale: list[str] = []
    missing: list[str] = []

    for entry in entries:
        path_str = entry.get("path", "")
        max_hours = entry.get("max_staleness_hours")
        if not path_str or not max_hours:
            continue

        resolved = Path(path_str).expanduser()
        if not resolved.is_absolute():
            resolved = Path(ARTHA_DIR) / path_str

        if not resolved.exists():
            missing.append(path_str)
            continue

        try:
            age_hours = (now_ts - resolved.stat().st_mtime) / 3600
            if age_hours > max_hours:
                stale.append(f"{path_str} ({age_hours:.0f}h > {max_hours}h)")
        except OSError:
            missing.append(path_str)

    if not stale and not missing:
        return CheckResult(
            "state registry staleness", "P1", True,
            f"{len(entries)} tracked state file(s) within freshness bounds ✓",
        )

    parts: list[str] = []
    if stale:
        preview = stale[:3]
        extra = len(stale) - 3
        parts.append(
            f"{len(stale)} stale: {', '.join(preview)}"
            + (f" … +{extra} more" if extra > 0 else "")
        )
    if missing:
        preview = missing[:3]
        extra = len(missing) - 3
        parts.append(
            f"{len(missing)} missing: {', '.join(preview)}"
            + (f" … +{extra} more" if extra > 0 else "")
        )

    return CheckResult(
        "state registry staleness", "P1", False,
        f"⚠️ State freshness issues — {'; '.join(parts)}",
        fix_hint="Run scheduled agents: python scripts/agent_scheduler.py --tick",
    )


def check_heartbeat_zero_records() -> CheckResult:
    """P1 non-blocking: Warn when a domain heartbeat reports records_written: 0.

    Checks tmp/{domain}_last_run.json files written by agent_scheduler.py.
    records_written: 0 means the scheduler wrote the stub but the agent script
    never updated it with actual data (cold-start or agent-side failure).

    EAR-3 / PRD §3.2.
    """
    import json as _json  # noqa: PLC0415

    tmp_dir = Path(os.path.join(ARTHA_DIR, "tmp"))
    if not tmp_dir.exists():
        return CheckResult(
            "agent heartbeat", "P1", True,
            "No tmp/ directory — heartbeat check skipped ✓",
        )

    heartbeat_files = sorted(tmp_dir.glob("*_last_run.json"))
    if not heartbeat_files:
        return CheckResult(
            "agent heartbeat", "P1", True,
            "No agent heartbeat files found — pre-compute agents not yet run ✓",
        )

    zero_records: list[str] = []
    failure_states: list[str] = []

    for hb_path in heartbeat_files:
        try:
            data = _json.loads(hb_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        domain = data.get("domain", hb_path.stem.replace("_last_run", ""))
        status = data.get("status", "")
        records_written = data.get("records_written", -1)

        if status == "failure":
            failure_states.append(domain)
        elif records_written == 0:
            zero_records.append(domain)

    if not zero_records and not failure_states:
        return CheckResult(
            "agent heartbeat", "P1", True,
            f"{len(heartbeat_files)} agent heartbeat(s) healthy ✓",
        )

    parts: list[str] = []
    if failure_states:
        parts.append(f"failed: {', '.join(failure_states)}")
    if zero_records:
        parts.append(f"zero records written: {', '.join(zero_records)}")

    return CheckResult(
        "agent heartbeat", "P1", False,
        f"⚠️ Domain agent issues — {'; '.join(parts)}",
        fix_hint=(
            "Check agent logs or re-run: python scripts/agent_scheduler.py --tick"
        ),
    )


def check_channel_config() -> CheckResult:
    """P1: Validate channels.yaml has no incomplete placeholder values.


    Catches the three misconfigs that silently blocked Telegram responses:
      1. recipients.primary.id is empty or missing (CHANNEL_REJECT unknown_sender)
      2. push_enabled is False (outbound push blocked)
      3. listener_host is empty or a placeholder value (listener skips this host)

    Pure YAML parsing — no network calls, no adapter imports. Non-blocking.
    Only applicable when channels.yaml exists and at least one channel is enabled.
    """
    config_path = Path(os.path.join(ARTHA_DIR, "config", "channels.yaml"))
    if not config_path.exists():
        return CheckResult(
            "channel config", "P1", True,
            "channels.yaml not found — channel push disabled ✓",
        )

    try:
        import yaml as _yaml
        with open(config_path, encoding="utf-8") as _f:
            cfg = _yaml.safe_load(_f) or {}
    except Exception as exc:
        return CheckResult(
            "channel config", "P1", False,
            f"channels.yaml parse error: {exc}",
            fix_hint="Validate YAML syntax in config/channels.yaml",
        )

    enabled_channels = {
        k: v for k, v in cfg.get("channels", {}).items()
        if isinstance(v, dict) and v.get("enabled", False)
    }
    if not enabled_channels:
        return CheckResult("channel config", "P1", True, "No channels enabled — skipped ✓")

    issues: list[str] = []
    _PLACEHOLDER_HOSTS = {"", "NOT-THIS-HOST-XYZ", "your-hostname-here"}

    # Check 1 — listener_host not a placeholder
    listener_host = str(cfg.get("defaults", {}).get("listener_host", "")).strip()
    if listener_host in _PLACEHOLDER_HOSTS:
        issues.append(
            "defaults.listener_host is empty/placeholder — listener will skip every machine; "
            "run: python scripts/setup_channel.py --set-listener-host"
        )

    # Check 2 — push_enabled
    push_enabled = cfg.get("defaults", {}).get("push_enabled", False)
    if not push_enabled:
        issues.append(
            "defaults.push_enabled is false — post-catch-up push disabled; "
            "set push_enabled: true in config/channels.yaml"
        )

    # Check 3 — each enabled channel has a non-empty primary recipient ID
    for ch_name, ch_cfg in enabled_channels.items():
        primary_id = str(
            (ch_cfg.get("recipients") or {}).get("primary", {}).get("id", "")
        ).strip()
        if not primary_id:
            issues.append(
                f"{ch_name}: recipients.primary.id is empty — all inbound messages will be "
                f"rejected (unknown_sender); run: python scripts/setup_channel.py --channel {ch_name}"
            )

    if issues:
        return CheckResult(
            "channel config", "P1", False,
            f"{len(issues)} channel misconfiguration(s) detected",
            fix_hint=" | ".join(issues),
        )

    return CheckResult(
        "channel config", "P1", True,
        f"Channel config valid ({len(enabled_channels)} channel(s)) ✓",
    )


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
        from channels.registry import (
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
            from lib.common import update_channel_health_md
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


def check_profile_completeness() -> CheckResult:
    """P1: Verify user_profile.yaml has minimum viable fields populated.

    Only fires on near-empty profiles (≤10 YAML keys total). Users with
    intentionally partial configs (>10 keys) are not warned.
    Ref: vm-hardening.md Phase 2.2
    """
    profile_path = os.path.join(ARTHA_DIR, "config", "user_profile.yaml")
    if not os.path.exists(profile_path):
        return CheckResult(
            "user_profile completeness", "P1", True,
            "user_profile.yaml not found — cold start (handled by preflight gate) ✓",
        )

    try:
        import yaml  # type: ignore
        with open(profile_path, encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
    except Exception as exc:
        return CheckResult(
            "user_profile completeness", "P1", False,
            f"user_profile.yaml unreadable: {exc}",
        )

    def _count_keys(d: dict) -> int:
        """Recursively count all keys in a nested dict."""
        if not isinstance(d, dict):
            return 0
        total = len(d)
        for v in d.values():
            total += _count_keys(v)
        return total

    total_keys = _count_keys(profile)

    # Silent pass for profiles that have been meaningfully filled in
    if total_keys > 10:
        return CheckResult(
            "user_profile completeness", "P1", True,
            f"Profile populated ({total_keys} keys) ✓",
        )

    # Near-empty profile — surface actionable warnings
    missing: list[str] = []

    def _get(d: dict, path: str):
        parts = path.split(".")
        node = d
        for part in parts:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
            if node is None:
                return None
        return node

    if not _get(profile, "family.primary_user.name"):
        missing.append("family.primary_user.name")
    emails = _get(profile, "family.primary_user.emails") or {}
    if not any((emails or {}).values()):
        missing.append("family.primary_user.emails")
    if not _get(profile, "location.timezone"):
        missing.append("location.timezone")

    domains = _get(profile, "domains") or {}
    enabled = [d for d, v in (domains if isinstance(domains, dict) else {}).items()
               if isinstance(v, dict) and v.get("enabled")]
    if not enabled:
        missing.append("domains.<at least one>.enabled: true")

    recommendations: list[str] = []
    if not _get(profile, "integrations.google_calendar.calendar_ids"):
        recommendations.append("integrations.google_calendar.calendar_ids")
    if not _get(profile, "household.type"):
        recommendations.append("household.type")

    hint_parts = []
    if missing:
        hint_parts.append(f"Required missing: {', '.join(missing)}")
    if recommendations:
        hint_parts.append(f"Recommended: {', '.join(recommendations)}")
    hint_parts.append("Run /bootstrap or edit config/user_profile.yaml")

    return CheckResult(
        "user_profile completeness", "P1", False,
        f"Profile near-empty ({total_keys} keys) — catch-up will have limited context",
        fix_hint=" | ".join(hint_parts),
    )


def check_action_handlers() -> CheckResult:
    """P1 (Step 0c): Run action handler health checks and expire stale queue entries.

    Disabled handlers are excluded for the current session only (non-blocking).
    Ref: specs/act.md §5.2 Step 0c
    """
    try:
        import importlib
        # Guard: only run if actions feature is enabled in artha_config.yaml
        config_path = os.path.join(ARTHA_DIR, "config", "artha_config.yaml")
        actions_enabled = False
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as _f:
                _content = _f.read()
            # Quick YAML check without full parser dependency
            actions_enabled = "actions:" in _content and "enabled: true" in _content

        if not actions_enabled:
            return CheckResult(
                "action handlers", "P1", True,
                "Action layer not enabled — skipping handler health checks",
            )

        # Import ActionExecutor
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from action_executor import ActionExecutor  # noqa: PLC0415

        executor = ActionExecutor(Path(ARTHA_DIR))

        # Sweep expired stale actions
        expired_count = executor.expire_stale()

        # Run handler health checks
        health: dict[str, bool] = executor.run_health_checks()

        failed = [k for k, v in health.items() if not v]
        passed = [k for k, v in health.items() if v]

        if not health:
            return CheckResult(
                "action handlers", "P1", True,
                f"Action layer enabled, no handlers loaded{' | ' + str(expired_count) + ' stale actions expired' if expired_count else ''}",
            )

        if failed:
            return CheckResult(
                "action handlers", "P1", False,
                (
                    f"Action handlers: {len(passed)} ok, {len(failed)} degraded "
                    f"({', '.join(failed)}) — those action types disabled this session"
                    + (f" | {expired_count} stale actions expired" if expired_count else "")
                ),
                fix_hint="Check connector credentials for degraded handlers",
            )

        return CheckResult(
            "action handlers", "P1", True,
            (
                f"All {len(passed)} action handlers healthy ✓"
                + (f" | {expired_count} stale actions expired" if expired_count else "")
            ),
        )

    except ImportError:
        return CheckResult(
            "action handlers", "P1", True,
            "Action layer modules not installed — skipping (run: make install)",
        )
    except Exception as exc:
        return CheckResult(
            "action handlers", "P1", False,
            f"Action handler check failed: {exc}",
            fix_hint="Review state/actions.db and action executor logs",
        )


def check_eval_alerts() -> CheckResult:
    """P0: Block catch-up if any unacked P0 eval alerts are present.

    Reads state/eval_alerts.yaml and fails hard if unacked P0 alerts exist.
    P1/P2 alerts are informational only (do not block).

    Ref: specs/eval.md EV-14
    """
    alerts_path = pathlib.Path(ARTHA_DIR) / "state" / "eval_alerts.yaml"
    if not alerts_path.exists():
        # Also check root-level path (used by tests and some deployments)
        root_alerts = pathlib.Path(ARTHA_DIR) / "eval_alerts.yaml"
        if root_alerts.exists():
            alerts_path = root_alerts
        else:
            return CheckResult("eval alerts", "P0", True, "No eval alerts file — eval layer clean")

    try:
        import yaml  # type: ignore[import]
        raw = yaml.safe_load(alerts_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return CheckResult(
            "eval alerts", "P1", False,
            f"Could not read state/eval_alerts.yaml: {exc}",
            fix_hint="Delete or repair state/eval_alerts.yaml",
        )

    alert_list = raw.get("alerts", [])
    if not isinstance(alert_list, list):
        return CheckResult("eval alerts", "P1", True, "eval_alerts.yaml has no alerts list")

    p0_active = [
        a for a in alert_list
        if isinstance(a, dict)
        and a.get("severity") == "P0"
        and not a.get("acked", False)
    ]
    if p0_active:
        names = ", ".join(a.get("code", "?") for a in p0_active[:3])
        return CheckResult(
            "eval alerts", "P0", False,
            f"{len(p0_active)} unacked P0 eval alert(s): {names}",
            fix_hint=(
                "Resolve or ack P0 alerts in state/eval_alerts.yaml "
                "(set acked: true) then re-run preflight"
            ),
        )

    # Count P1/P2 for informational suffix
    p1_count = sum(
        1 for a in alert_list
        if isinstance(a, dict) and a.get("severity") == "P1" and not a.get("acked", False)
    )
    suffix = f" | {p1_count} unacked P1 alert(s)" if p1_count else ""
    return CheckResult(
        "eval alerts", "P0", True,
        f"No blocking P0 eval alerts{suffix}",
    )


def check_eval_p1_alerts() -> "CheckResult | None":
    """P1 warning: surface unacked P1 eval alerts as a separate check row.

    Returns None if no unacked P1 alerts (caller skips appending).
    Unlike check_eval_alerts() this never blocks catch-up.

    Ref: specs/eval.md EV-14
    """
    alerts_path = pathlib.Path(ARTHA_DIR) / "state" / "eval_alerts.yaml"
    if not alerts_path.exists():
        return None
    try:
        import yaml  # type: ignore[import]
        raw = yaml.safe_load(alerts_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None

    alert_list = raw.get("alerts", [])
    if not isinstance(alert_list, list):
        return None

    p1_active = [
        a for a in alert_list
        if isinstance(a, dict)
        and a.get("severity") == "P1"
        and not a.get("acked", False)
    ]
    if not p1_active:
        return None

    names = ", ".join(
        a.get("code", a.get("type", "?")) for a in p1_active[:3]
    )
    return CheckResult(
        "eval alerts p1", "P1", False,
        f"{len(p1_active)} unacked P1 eval alert(s): {names}",
        fix_hint="Ack P1 alerts in state/eval_alerts.yaml (set acked: true) or resolve",
    )


def run_preflight(auto_fix: bool = False, quiet: bool = False, force_no_guardrails: bool = False) -> list[CheckResult]:
    """Run all preflight checks. Returns list of CheckResult objects."""
    checks: list[CheckResult] = []

    # ── P0 — Hard blocks ──────────────────────────────────────────────────
    checks.append(check_keyring_backend())
    checks.append(check_vault_health())
    checks.append(check_vault_lock(auto_fix=auto_fix))
    checks.append(check_pii_guard())
    checks.append(check_guardrails_yaml(force_no_guardrails=force_no_guardrails))  # DEBT-004
    checks.append(check_eval_alerts())  # EV-14: block on unacked P0 eval alerts
    p1_alert_result = check_eval_p1_alerts()
    if p1_alert_result is not None:
        checks.append(p1_alert_result)
    # Global API live connection via unified pipeline.py (skip if --quiet to reduce latency)
    if not quiet:
        try:
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

    # ── P1 — Gmail / Calendar OAuth tokens (non-blocking: warn, skip source) ──
    checks.append(check_oauth_token("Gmail", "gmail-oauth-token.json", severity="P1"))
    checks.append(check_oauth_token("Calendar", "gcal-oauth-token.json", severity="P1"))
    if not quiet:
        try:
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "gmail"], severity="P1"
            ))
        except subprocess.TimeoutExpired:
            checks.append(CheckResult(
                "Gmail API connectivity", "P1", False,
                "Gmail API health check timed out — continuing without Gmail data",
            ))

    # ── P1 — State file population from templates (first-run) ─────────────
    checks.append(check_state_templates(auto_fix=auto_fix))
    # ── P1 — OneDrive conflict detection (DEBT-005) ────────────────────────
    checks.append(check_onedrive_conflicts())

    # ── P1 — Warnings only ────────────────────────────────────────────────
    checks.append(check_token_freshness("Gmail", "gmail-oauth-token.json"))
    checks.append(check_token_freshness("Calendar", "gcal-oauth-token.json"))
    checks.append(check_open_items(auto_fix=auto_fix))
    checks.append(check_briefings_directory())
    checks.append(check_msgraph_token())  # T-1B.6.1: non-blocking, To Do sync optional

    # ── P1 — Profile completeness (vm-hardening.md Phase 2.2) ─────────────
    checks.append(check_profile_completeness())

    # ── P1 — Action handler health checks + expiry sweep (Step 0c) ───────
    checks.append(check_action_handlers())

    # ── P1 — WorkIQ Calendar (v2.2 — Windows-only, non-blocking) ─────────
    checks.append(check_workiq())

    # ── P1 — Bridge health (dual-setup.md — non-blocking; skipped if disabled) ──
    checks.append(check_bridge_health())

    # ── P1 — Home Assistant (ARTHA-IOT Wave 1 — non-blocking) ────────────
    checks.append(check_ha_connectivity())

    # ── P1 — ADO auth (work-projects domain, opt-in, non-blocking) ────────
    checks.append(check_ado_auth())

    # ── P1 — External agent discovery (AR-9 — non-blocking) ───────────────
    checks.append(check_ext_agent_discovery())
    # EA-11b: external agent health status
    checks.append(check_ext_agent_health())

    # ── P1 — EAR-3 state registry staleness (non-blocking) ────────────────
    checks.append(check_state_registry_staleness())
    # EAR-3 domain heartbeat: warn on zero-record writes
    checks.append(check_heartbeat_zero_records())

    # ── P1 — Channel config (v5.1 — non-blocking) ──────────────────────────
    checks.append(check_channel_config())

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

    # Career PDF feature — Playwright + Chromium (P1, non-blocking)
    # Spec FR-CS-3: "Preflight check validates Chromium binary availability"
    try:
        import playwright  # noqa: F401
        # Chromium browser cache location (cross-platform)
        if sys.platform == "darwin":
            _pw_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
        elif sys.platform == "win32":
            _pw_cache = Path.home() / "AppData" / "Local" / "ms-playwright"
        else:
            _pw_cache = Path.home() / ".cache" / "ms-playwright"
        _chromium_dirs = sorted(_pw_cache.glob("chromium-*")) if _pw_cache.exists() else []
        if _chromium_dirs:
            checks.append(CheckResult(
                "Playwright Chromium", "P1", True,
                f"playwright + Chromium ready ({_chromium_dirs[-1].name}) ✓",
            ))
        else:
            checks.append(CheckResult(
                "Playwright Chromium", "P1", False,
                "playwright installed but Chromium browser not found — career PDF generation will fail",
                fix_hint="Run: playwright install chromium  (or: make install-playwright)",
            ))
    except ImportError:
        checks.append(CheckResult(
            "Playwright", "P1", False,
            "playwright not installed — career PDF generation unavailable (non-blocking)",
            fix_hint="Run: make install-playwright  (or: pip install playwright && playwright install chromium)",
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
    first_run: bool = False,
    advisory: bool = False,
) -> tuple[str, bool]:
    """
    Format check results into terminal output.
    Returns (output_string, all_p0_passed).

    first_run=True: softer "Setup Checklist" display — OAuth failures shown
    as ○ (not yet configured) rather than ⛔ (hard block).

    advisory=True: P0 failures reclassified to ADVISORY in output; exit always 0.
    Use ONLY in sandboxed/VM environments where hard blocks are known and accepted.
    NEVER use on local machines.
    """
    _FIRST_RUN_OAUTH_HINTS = (
        "setup_google_oauth.py",
        "setup_msgraph_oauth.py",
        "setup_icloud_auth.py",
    )

    def _is_expected_on_first_run(c: CheckResult) -> bool:
        """True when a check failure is normal on a fresh install."""
        hint = c.fix_hint or ""
        msg  = c.message  or ""
        if any(h in hint for h in _FIRST_RUN_OAUTH_HINTS):
            return True
        if "Token file missing" in msg or "token file missing" in msg.lower():
            return True
        if c.name.startswith("pipeline.py") and not c.passed:
            return True
        if c.name == "gmail_send.py health" and not c.passed:
            return True
        return False

    if first_run:
        p0_failures = [
            c for c in checks
            if c.severity == "P0" and not c.passed and not _is_expected_on_first_run(c)
        ]
    else:
        p0_failures = [c for c in checks if c.severity == "P0" and not c.passed]

    p1_warnings = [c for c in checks if c.severity == "P1" and not c.passed]
    # In advisory mode, P0 failures become advisories — exit always 0
    all_passed  = advisory or not p0_failures

    lines: list[str] = []

    if not quiet:
        if advisory:
            lines.append("━━ ARTHA PRE-FLIGHT GATE (ADVISORY MODE) ━━━━━━━━━")
            lines.append("  ⚠️  Advisory mode active — P0 failures are non-blocking")
            lines.append("  ⚠️  Use ONLY in sandboxed/VM environments. NEVER on local Mac.")
        elif first_run:
            lines.append("━━ ARTHA SETUP CHECKLIST ━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("  first-run mode — OAuth failures are expected and not blocking")
        else:
            lines.append("━━ ARTHA PRE-FLIGHT GATE ━━━━━━━━━━━━━━━━━━━━━━━━")
        for c in checks:
            if quiet and c.passed:
                continue
            if first_run and not c.passed and _is_expected_on_first_run(c):
                lines.append(f"  ○ [{c.severity}] {c.name}: not yet configured")
                if c.fix_hint:
                    lines.append(f"       → {c.fix_hint}")
            elif advisory and not c.passed and c.severity == "P0":
                lines.append(f"  ⚠️  [ADVISORY] {c.name}: {c.message}")
                if c.fix_hint:
                    lines.append(f"       → {c.fix_hint}")
            else:
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
        if advisory and p0_failures:
            notes.append(f"{len(p0_failures)} advisory (non-blocking in this environment)")
        suffix = f" ({', '.join(notes)})" if notes else ""
        if advisory:
            lines.append(f"⚠️  Pre-flight: ADVISORY GO{suffix} — proceed with degraded catch-up")
        elif first_run:
            lines.append(f"✓ Setup checklist: ready{suffix} — open your AI CLI and say: catch me up")
        else:
            lines.append(f"✓ Pre-flight: GO{suffix} — proceed with catch-up")
    else:
        if first_run:
            lines.append(f"⛔ {len(p0_failures)} critical setup issue(s) need attention:")
        else:
            lines.append(f"⛔ Pre-flight: NO-GO — {len(p0_failures)} critical check(s) failed")
        for c in p0_failures:
            lines.append(f"   • {c.name}: {c.message}")
        lines.append("")
        if first_run:
            lines.append("Fix the above issues to proceed.")
        else:
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
    parser.add_argument(
        "--first-run",
        action="store_true",
        help=(
            "First-run / setup-checklist mode: OAuth and connector failures are shown as "
            "'not yet configured' (\u25cb) rather than hard blocks (⛔). "
            "Exits 0 when only expected setup steps remain."
        ),
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help=(
            "Advisory mode: run all checks but report P0 failures as ADVISORY instead of "
            "NO-GO. Exits 0 regardless of P0 failures. "
            "Use ONLY in sandboxed/VM environments where hard blocks are known and accepted. "
            "NEVER use on local machines."
        ),
    )
    parser.add_argument(
        "--force-no-guardrails",
        action="store_true",
        help=(
            "DEBT-004: Override the guardrails P0 check. Exits 0 and writes "
            "FORCE_NO_GUARDRAILS to audit log. FOR EXPERT USE ONLY — disables all "
            "safety chain validation. Never use on production machines."
        ),
    )

    args = parser.parse_args()

    # Cold-start detection: exit 3 if user_profile.yaml is missing (first run)
    profile_path = pathlib.Path(ARTHA_DIR) / "config" / "user_profile.yaml"
    if not profile_path.exists():
        if args.json:
            print(json.dumps({"pre_flight_ok": False, "cold_start": True, "checks": []}))
        else:
            print("⛔ Cold start detected — config/user_profile.yaml not found.")
            print("   Run /bootstrap or say 'catch me up' to start guided setup.")
        sys.exit(3)

    advisory = getattr(args, "advisory", False)
    force_no_guardrails = getattr(args, "force_no_guardrails", False)
    checks   = run_preflight(auto_fix=args.fix, quiet=args.quiet, force_no_guardrails=force_no_guardrails)
    all_ok   = advisory or all(c.passed for c in checks if c.severity == "P0")

    if args.json:
        output = {
            "pre_flight_ok":  all_ok,
            "advisory_mode":  advisory,
            "degradation_list": [
                c.name for c in checks if c.severity == "P0" and not c.passed
            ] if advisory else [],
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
        formatted, first_run_ok = format_results(
            checks, quiet=args.quiet, first_run=args.first_run, advisory=advisory
        )
        print(formatted)
        # In first-run mode use the softer exit-code logic
        if args.first_run:
            all_ok = first_run_ok

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
