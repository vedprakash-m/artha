#!/usr/bin/env python3
"""
vault_guard.py — Vault-aware pre-read file validator
====================================================
Lightweight script any AI CLI can call before reading a state file.
Returns JSON indicating whether the file is genuinely readable or is
a locked placeholder (empty/missing .age-backed file).

Usage:
  python scripts/vault_guard.py state/finance.md
  python scripts/vault_guard.py --all          # check all sensitive state files

Output (stdout JSON):
  {"readable": true,  "path": "state/finance.md"}
  {"readable": false, "path": "state/finance.md", "reason": "locked_placeholder",
   "hint": "Run: python scripts/vault.py decrypt"}

Exit codes:
  0 — file is readable (or not a sensitive domain)
  2 — file is a locked placeholder (decrypt required)
  1 — file does not exist

Integration (add to Artha.core.md §11 and CLI-specific instruction files):
  Before reading ANY file in state/, call this script. If readable=false,
  run vault.py decrypt first. Reading a locked placeholder produces empty or
  44-byte garbage content — and hallucinated output.

Ref: specs/enhance.md §10.0.2
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve Artha root without importing foundation (keep this script stdlib-only
# so it can run in ANY environment before the venv is activated).
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"
_LOCK_FILE = _ARTHA_DIR / ".artha-decrypted"

# Minimum size (bytes) for a state file to be considered non-placeholder.
# A real state file has YAML frontmatter + at least a few lines.
# An encrypted .age ciphertext stub is typically 44-100 bytes.
_MIN_READABLE_BYTES = 64

# Sensitive domains — derived at runtime from domain_registry.yaml if available,
# otherwise falls back to the static list from foundation._config["SENSITIVE_FILES"].
_STATIC_SENSITIVE = [
    "immigration", "finance", "insurance", "estate",
    "health", "audit", "vehicle", "contacts", "occasions",
]


def _load_sensitive_domains() -> list[str]:
    """Load sensitive domain list from domain_registry.yaml (preferred) or static fallback."""
    registry_path = _ARTHA_DIR / "config" / "domain_registry.yaml"
    if registry_path.exists():
        try:
            import yaml  # type: ignore[import]
            with open(registry_path, encoding="utf-8") as f:
                reg = yaml.safe_load(f) or {}
            sensitive = [
                name
                for name, cfg in reg.get("domains", {}).items()
                if isinstance(cfg, dict) and cfg.get("sensitivity") in ("high", "critical")
            ]
            if sensitive:
                return sensitive
        except Exception:
            pass  # Fall through to static list
    return _STATIC_SENSITIVE


def check_file_readable(filepath: str) -> dict:
    """Check if a state file is genuinely readable or a locked placeholder.

    Args:
        filepath: Path string (absolute or relative to ARTHA_DIR).

    Returns:
        dict with keys:
          - readable (bool)
          - path (str)
          - reason (str, only when not readable)
          - hint (str, only when not readable)
    """
    path = Path(filepath)
    if not path.is_absolute():
        path = _ARTHA_DIR / path

    result_path = str(path.relative_to(_ARTHA_DIR)) if path.is_relative_to(_ARTHA_DIR) else str(path)

    if not path.exists():
        return {
            "readable": False,
            "path": result_path,
            "reason": "file_missing",
            "hint": "File does not exist. Check vault status: python scripts/vault.py status",
        }

    # If this path is not a sensitive domain, skip the placeholder check.
    sensitive_domains = _load_sensitive_domains()
    stem = path.stem.replace(".md", "")  # handles both "finance.md" and "finance"
    # Normalise: strip .md from stem in case path is "finance.md"
    if stem.endswith(".md"):
        stem = stem[:-3]

    domain_name = stem
    is_sensitive = any(domain_name == d or path.stem == f"{d}.md" for d in sensitive_domains)

    size = path.stat().st_size

    if not is_sensitive:
        # Non-sensitive file: only check existence
        return {"readable": True, "path": result_path}

    # Sensitive file checks
    # 1. Empty file — clear placeholder
    if size == 0:
        return {
            "readable": False,
            "path": result_path,
            "reason": "empty_placeholder",
            "hint": "File is empty. Vault may be locked. Run: python scripts/vault.py decrypt",
        }

    # 2. Very small file — likely a stub/placeholder written by vault.py when locked
    if size < _MIN_READABLE_BYTES:
        # Check if the vault lock file exists — if it does, state is locked
        if not _LOCK_FILE.exists():
            return {
                "readable": False,
                "path": result_path,
                "reason": "locked_placeholder",
                "hint": (
                    f"File is {size} bytes (too small to be meaningful). "
                    "Vault appears locked. Run: python scripts/vault.py decrypt"
                ),
            }

    # 3. If vault is unlocked but file is tiny, flag as suspicious
    if size < _MIN_READABLE_BYTES and _LOCK_FILE.exists():
        return {
            "readable": False,
            "path": result_path,
            "reason": "suspicious_size",
            "hint": (
                f"File is {size} bytes even though vault is unlocked. "
                "This may indicate a write error. Check state integrity."
            ),
        }

    # 4. File is large enough — check it starts with YAML frontmatter (---) or markdown (#)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            first_line = f.readline().strip()
        if first_line and not first_line.startswith(("---", "#", ">")):
            # Could be encrypted ciphertext (age files start with "age-encryption...")
            if first_line.startswith(("age-encryption", "-> X25519")):
                return {
                    "readable": False,
                    "path": result_path,
                    "reason": "encrypted_ciphertext",
                    "hint": "File contains encrypted data (not decrypted). Run: python scripts/vault.py decrypt",
                }
    except OSError:
        pass  # File readable check: if we can't read it, report not readable

    return {"readable": True, "path": result_path}


def check_all_sensitive() -> list[dict]:
    """Check all sensitive domain state files. Returns list of results."""
    sensitive_domains = _load_sensitive_domains()
    results = []
    for domain in sensitive_domains:
        path = _STATE_DIR / f"{domain}.md"
        results.append(check_file_readable(str(path)))
    return results


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "Usage: vault_guard.py <filepath> | --all"}))
        sys.exit(1)

    if args[0] == "--all":
        results = check_all_sensitive()
        not_readable = [r for r in results if not r["readable"]]
        print(json.dumps({"files": results, "all_readable": len(not_readable) == 0}))
        sys.exit(0 if not not_readable else 2)
    else:
        result = check_file_readable(args[0])
        print(json.dumps(result))
        if not result["readable"]:
            reason = result.get("reason", "unknown")
            if reason == "file_missing":
                sys.exit(1)
            sys.exit(2)
        sys.exit(0)


if __name__ == "__main__":
    main()
