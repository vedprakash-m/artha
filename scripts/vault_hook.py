#!/usr/bin/env python3
"""
vault_hook.py — Claude Code hook helper for vault operations
=============================================================
Always exits 0 so hooks never block tool execution.
Called by .claude/settings.json PreToolUse and PostToolUse hooks.

Usage:
  python scripts/vault_hook.py decrypt       — silent decrypt attempt
  python scripts/vault_hook.py stray-check   — warn about unprotected plaintext

Ref: TS §3.6.2
"""

import os
import sys
import subprocess

ARTHA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ARTHA_DIR, "state")
LOCK_FILE = os.path.join(ARTHA_DIR, ".artha-decrypted")
VAULT_PY  = os.path.join(ARTHA_DIR, "scripts", "vault.py")

SENSITIVE_DOMAINS = [
    "immigration", "finance", "insurance", "estate", "health", "audit", "vehicle"
]


def hook_decrypt() -> None:
    """Attempt to decrypt vault; always succeed for hook safety."""
    try:
        subprocess.run(
            [sys.executable, VAULT_PY, "decrypt"],
            capture_output=True, timeout=60, cwd=ARTHA_DIR,
        )
    except Exception:
        print("[VAULT] Decrypt skipped (non-fatal)")


def hook_stray_check() -> None:
    """Warn if plaintext sensitive files exist without a lock file."""
    if os.path.exists(LOCK_FILE):
        return  # Active session — plaintext is expected
    for domain in SENSITIVE_DOMAINS:
        plain = os.path.join(STATE_DIR, f"{domain}.md")
        if os.path.exists(plain):
            print(f"[VAULT-WARN] Stray plaintext: {plain} (no lock file)")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "decrypt":
        hook_decrypt()
    elif cmd == "stray-check":
        hook_stray_check()
    # Always exit 0 — hooks must not block tool execution
    sys.exit(0)


if __name__ == "__main__":
    main()
