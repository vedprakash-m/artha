#!/usr/bin/env python3
"""
safe_cli.py — Artha outbound PII wrapper for external CLI calls

Pure Python port of safe_cli.sh. Cross-platform (macOS, Windows, Linux).
Imports pii_guard.scan() directly — no subprocess to pii_guard.

Usage:
    python scripts/safe_cli.py gemini "What is the current EB-2 India priority date?"
    python scripts/safe_cli.py copilot "Review this script for security issues"
    python scripts/safe_cli.py <any-cli> "<query>"

Safety model:
    - Pipes query through pii_guard.scan() BEFORE forwarding to external CLI
    - If PII detected: blocks the call, logs to audit.md, exits 1
    - If clean: executes the CLI call, logs query length (not content) to audit.md

Exit codes:
    0 — success (CLI call completed)
    1 — PII detected in query (blocked)
    2 — CLI not found in PATH

Ref: TS §8.7, §3.7.7, standardization.md §7.6.2
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve project root so this runs from any cwd
_SCRIPT_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPT_DIR.parent
_AUDIT_LOG = _ARTHA_DIR / "state" / "audit.md"

# Import pii_guard from the same scripts/ directory
sys.path.insert(0, str(_SCRIPT_DIR))
from pii_guard import scan as pii_scan  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# CLI routing: cli alias → argv prefix for subprocess
# ─────────────────────────────────────────────────────────────────────────────

_CLI_MAP: dict[str, list[str]] = {
    "gemini": ["gemini", "-p"],     # gemini -p "<query>"
    "copilot": ["gh", "copilot", "suggest"],  # gh copilot suggest "<query>"
    "gh": ["gh"],                   # gh "<query>"
}


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


def _die(message: str, exit_code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(exit_code)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "Usage: safe_cli.py <cli> <query> [additional_args...]\n"
            "\n"
            "  cli    — CLI to invoke (gemini, copilot, gh, etc.)\n"
            "  query  — The query string to send (will be PII-scanned before sending)\n"
            "\n"
            "Examples:\n"
            '  safe_cli.py gemini "What is the current EB-2 India priority date?"\n'
            '  safe_cli.py copilot "Review this script for security issues"',
            file=sys.stderr,
        )
        return 1

    cli = argv[1]
    query = argv[2]
    extra_args = argv[3:]

    # ── 1. Verify CLI is available ────────────────────────────────────────────
    # Use _CLI_MAP to find the actual executable name; fall back to cli itself
    exe_prefix = _CLI_MAP.get(cli, [cli])
    executable = exe_prefix[0]

    if shutil.which(executable) is None:
        _log(
            f"[{_now()}] CLI_UNAVAILABLE | cli: {cli} | "
            f"query_length: {len(query)}"
        )
        _die(f"CLI '{executable}' not found in PATH. Check installation.", exit_code=2)

    # ── 2. PII scan ───────────────────────────────────────────────────────────
    pii_found, pii_types = pii_scan(query)

    if pii_found:
        type_str = ",".join(sorted(pii_types.keys())) if pii_types else "unknown"
        _log(
            f"[{_now()}] OUTBOUND_PII_BLOCK | cli: {cli} | "
            f"pii_types: {type_str} | reason: PII detected in outbound query"
        )
        print("", file=sys.stderr)
        print("━" * 42, file=sys.stderr)
        print("BLOCKED: PII detected in outbound query", file=sys.stderr)
        print(f"   CLI: {cli}", file=sys.stderr)
        print(f"   Found: {type_str}", file=sys.stderr)
        print("   Action: Query blocked. Logged to audit.md.", file=sys.stderr)
        print("   Fix: Reformulate query without PII.", file=sys.stderr)
        print("━" * 42, file=sys.stderr)
        return 1

    # ── 3. Log approved call (query length only — no content) ─────────────────
    _log(
        f"[{_now()}] CLI_CALL | cli: {cli} | "
        f"query_length: {len(query)} | status: approved"
    )

    # ── 4. Execute the CLI ────────────────────────────────────────────────────
    cmd = exe_prefix + [query] + extra_args
    result = subprocess.run(cmd)

    _log(
        f"[{_now()}] CLI_RESULT | cli: {cli} | exit_code: {result.returncode}"
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
