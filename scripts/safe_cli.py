#!/usr/bin/env python3
"""
safe_cli.py — Artha outbound PII wrapper for external CLI calls

Pure Python port of safe_cli.sh. Cross-platform (macOS, Windows, Linux).
Imports pii_guard.scan() directly — no subprocess to pii_guard.

Usage:
    python scripts/safe_cli.py gemini "What is the current EB-2 India priority date?"
    python scripts/safe_cli.py gemini "Review this public requirement"

Safety model:
    - Pipes query through pii_guard.scan() BEFORE forwarding to external CLI
    - If PII detected: blocks the call, logs to audit.md, exits 1
    - If clean: executes one exact, bounded typed CLI call and logs only its length

Exit codes:
    0 — success (CLI call completed)
    1 — PII detected in query (blocked)
    2 — invocation or CLI is unavailable

Ref: TS §8.7, §3.7.7, standardization.md §7.6.2
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve project root so this runs from any cwd
_SCRIPT_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPT_DIR.parent
from lib.local_paths import audit_log_path
_AUDIT_LOG = audit_log_path(_ARTHA_DIR)

# Import pii_guard from the same scripts/ directory
sys.path.insert(0, str(_SCRIPT_DIR))
from pii_guard import scan as pii_scan  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# CLI routing: cli alias → argv prefix for subprocess
# ─────────────────────────────────────────────────────────────────────────────

_CLI_MAP: dict[str, list[str]] = {"gemini": ["gemini", "-p"]}
_TIMEOUT_SECONDS = 120
_MAX_QUERY_CHARS = 32_000
_MAX_OUTPUT_BYTES = 1_000_000
_ENV_KEYS = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
    "LANG", "LC_ALL", "NO_COLOR", "TERM",
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _log(entry: str) -> None:
    """Append entry to audit.md and echo to stderr."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass
    print(entry, file=sys.stderr)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _minimal_environment() -> dict[str, str]:
    environment = {key: value for key in _ENV_KEYS if (value := os.environ.get(key))}
    environment["NO_COLOR"] = "1"
    environment.setdefault("PYTHONUTF8", "1")
    return environment


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "Usage: safe_cli.py gemini <query>\n"
            "\n"
            "Only the registered gemini invocation is supported; extra arguments are refused.\n"
            "\n"
            'Example: safe_cli.py gemini "What is the current EB-2 India priority date?"',
            file=sys.stderr,
        )
        return 2

    cli = argv[1]
    query = argv[2]

    exe_prefix = _CLI_MAP.get(cli)
    if exe_prefix is None:
        _log(f"[{_now()}] CLI_UNAVAILABLE | reason: unsupported_typed_invocation")
        print("ERROR: The requested CLI is unavailable or unsupported. No process was started.",
              file=sys.stderr)
        return 2
    if not isinstance(query, str) or not query.strip() or len(query) > _MAX_QUERY_CHARS:
        print("ERROR: Query is empty or exceeds the configured input limit.", file=sys.stderr)
        return 1

    try:
        pii_found, pii_types = pii_scan(query)
    except Exception:
        _log(f"[{_now()}] OUTBOUND_GUARD_FAILED | cli: {cli} | reason: pii scanner unavailable")
        return 1

    if pii_found:
        type_str = ",".join(sorted(pii_types.keys())) if pii_types else "unknown"
        _log(
            f"[{_now()}] OUTBOUND_PII_BLOCK | cli: {cli} | "
            f"pii_types: {type_str} | reason: PII detected in outbound query"
        )
        print("BLOCKED: PII detected in outbound query.", file=sys.stderr)
        return 1

    # Only resolve an executable after every content-bearing input has passed.
    executable = exe_prefix[0]
    resolved = shutil.which(executable)

    if resolved is None:
        _log(
            f"[{_now()}] CLI_UNAVAILABLE | cli: {cli} | "
            f"query_length: {len(query)}"
        )
        print(f"ERROR: CLI '{executable}' not found in PATH. Check installation.", file=sys.stderr)
        return 2

    _log(
        f"[{_now()}] CLI_CALL | cli: {cli} | "
        f"query_length: {len(query)} | status: approved"
    )

    cmd = [resolved, *exe_prefix[1:], query]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_ARTHA_DIR),
            env=_minimal_environment(),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log(f"[{_now()}] CLI_RESULT | cli: {cli} | exit_code: timeout")
        print(f"ERROR: CLI '{cli}' exceeded {_TIMEOUT_SECONDS}s and was terminated.", file=sys.stderr)
        return 124
    except OSError as exc:
        _log(f"[{_now()}] CLI_RESULT | cli: {cli} | exit_code: launch_error:{type(exc).__name__}")
        print(f"ERROR: CLI '{cli}' could not be started.", file=sys.stderr)
        return 2

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if len(stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES or len(stderr.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        _log(f"[{_now()}] CLI_RESULT | cli: {cli} | exit_code: output_limit")
        print("ERROR: CLI output exceeded the configured limit.", file=sys.stderr)
        return 1

    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if result.returncode and not stdout:
        print(f"ERROR: CLI '{cli}' exited with code {result.returncode}.", file=sys.stderr)

    _log(
        f"[{_now()}] CLI_RESULT | cli: {cli} | exit_code: {result.returncode}"
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
