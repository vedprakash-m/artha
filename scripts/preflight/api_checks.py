"""preflight/api_checks.py — API connectivity and PII guard checks."""
from __future__ import annotations

import os
import sys
import subprocess
import time
from pathlib import Path

from preflight._types import (
    ARTHA_DIR, SCRIPTS_DIR, _SUBPROCESS_ENV, _rel, CheckResult,
)

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


