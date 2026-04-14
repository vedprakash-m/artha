#!/usr/bin/env python3
"""
domain_view.py — Script-backed /domain <name> command renderer
===============================================================
Reads a specific domain's state file with automatic vault lifecycle management.
Sensitive domains (finance, immigration, etc.) are decrypted only for the
duration of this read, then immediately re-encrypted.

Usage:
  python scripts/domain_view.py finance
  python scripts/domain_view.py immigration --format flash
  python scripts/domain_view.py health --format standard   (default)
  python scripts/domain_view.py goals --format digest

Exit codes:
  0 — success
  1 — domain not found or vault error
  2 — invalid domain name

Ref: specs/enhance.md §10.0.1b
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"
_CONFIG_DIR = _ARTHA_DIR / "config"
_LOCK_FILE = _ARTHA_DIR / ".artha-decrypted"

# DEBT-002: Derive from foundation.py single source of truth.
try:
    import sys as _sys
    _scripts_dir = str(Path(__file__).resolve().parent)
    if _scripts_dir not in _sys.path:
        _sys.path.insert(0, _scripts_dir)
    from foundation import get_sensitive_domains as _get_sensitive_domains
    _SENSITIVE_DOMAINS = _get_sensitive_domains()
except Exception:  # noqa: BLE001
    _SENSITIVE_DOMAINS = {
        "immigration", "finance", "insurance", "estate",
        "health", "audit", "vehicle", "contacts", "occasions",
        "transactions", "kids",
    }

_ALL_DOMAINS = {
    "immigration", "finance", "kids", "health", "calendar",
    "comms", "goals", "home", "employment", "travel",
    "digital", "learning", "social", "boundary", "decisions",
    "insurance", "estate", "vehicle", "contacts", "occasions",
    "audit", "dashboard", "open_items", "memory",
    # Phase 1b new domains
    "pets", "caregiving", "business", "wellness", "community",
}


def _is_vault_unlocked() -> bool:
    return _LOCK_FILE.exists()


def _decrypt_vault() -> bool:
    if _is_vault_unlocked():
        return True
    try:
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "vault.py"), "decrypt"],
            capture_output=True, text=True, timeout=60, cwd=_ARTHA_DIR,
        )
        return result.returncode == 0
    except Exception:
        return False


def _reencrypt_vault() -> None:
    try:
        subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "vault.py"), "encrypt"],
            capture_output=True, text=True, timeout=120, cwd=_ARTHA_DIR,
        )
    except Exception:
        pass


def _read_domain_file(domain: str) -> tuple[str, bool]:
    """Read domain state file. Returns (content, is_encrypted_warning)."""
    path = _STATE_DIR / f"{domain}.md"
    age_path = _STATE_DIR / f"{domain}.md.age"

    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            if content.strip().startswith(("age-encryption", "-> X25519")):
                return "_⚠ File appears to contain encrypted data. Vault not unlocked._", True
            return content, False
        except OSError as e:
            return f"_Error reading {domain}: {e}_", False

    if age_path.exists():
        return f"_🔒 {domain}.md is encrypted. Vault is locked — run vault.py decrypt first._", True

    return f"_No state file found for domain '{domain}'._\n\nRun a catch-up to populate this domain.", False


def _extract_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter key-value pairs."""
    result: dict[str, str] = {}
    in_fm = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            if not in_fm:
                in_fm = True
                continue
            else:
                break  # End of frontmatter
        if in_fm and ":" in stripped:
            key, _, value = stripped.partition(":")
            result[key.strip()] = value.strip().strip("\"'")
    return result


def _format_flash(domain: str, content: str, fm: dict) -> str:
    """Flash: key stats only (status + top alert or last_updated)."""
    status = fm.get("status", "grey")
    last_updated = fm.get("last_updated", "never")
    icon = {"green": "✅", "yellow": "🟡", "red": "🔴", "grey": "⬜"}.get(status, "⬜")
    lines = [f"# {domain.title()} — Flash\n", f"{icon} Status: **{status}** · Last updated: {last_updated}"]
    # Find first alert line (starts with 🔴 🟠 🟡)
    for line in content.split("\n"):
        if line.startswith(("🔴", "🟠", "🟡")):
            lines.append(f"\n**Top alert:** {line.strip()}")
            break
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_standard(domain: str, content: str, fm: dict) -> str:
    """Standard: full state file content."""
    return content + f"\n\n---\n_Rendered by domain_view.py · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"


def _format_digest(domain: str, content: str, fm: dict) -> str:
    """Digest: full content + metadata summary."""
    status = fm.get("status", "grey")
    last_updated = fm.get("last_updated", "never")
    header = (
        f"# {domain.title()} — Full View\n"
        f"Status: `{status}` · Last updated: `{last_updated}`\n\n---\n"
    )
    return header + content + f"\n\n---\n_Rendered: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"


def main() -> int:
    parser = argparse.ArgumentParser(description="Artha Domain State Viewer")
    parser.add_argument("domain", help="Domain name (e.g., finance, immigration, health)")
    parser.add_argument(
        "--format", choices=["flash", "standard", "digest"], default="standard",
        help="Output density (default: standard)"
    )
    args = parser.parse_args()

    domain = args.domain.lower().strip()

    # Validate domain name to prevent path traversal
    if not domain.replace("_", "").replace("-", "").isalnum():
        print(f"ERROR: Invalid domain name '{domain}'", file=sys.stderr)
        return 2

    is_sensitive = domain in _SENSITIVE_DOMAINS
    decrypted_here = False
    vault_available = _is_vault_unlocked()

    if is_sensitive and not vault_available:
        vault_available = _decrypt_vault()
        decrypted_here = vault_available
        if not vault_available:
            print(f"⚠ Could not decrypt vault. Attempting to read {domain} anyway...", file=sys.stderr)

    try:
        content, has_encrypted_warning = _read_domain_file(domain)

        if has_encrypted_warning and not vault_available:
            print(f"🔒 Domain '{domain}' is encrypted and vault could not be unlocked.", file=sys.stderr)
            print("Run: python scripts/vault.py decrypt", file=sys.stderr)
            return 1

        fm = _extract_frontmatter(content)

        if args.format == "flash":
            output = _format_flash(domain, content, fm)
        elif args.format == "digest":
            output = _format_digest(domain, content, fm)
        else:
            output = _format_standard(domain, content, fm)

        print(output)
        return 0

    finally:
        if decrypted_here and vault_available:
            _reencrypt_vault()


if __name__ == "__main__":
    sys.exit(main())
