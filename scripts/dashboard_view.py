#!/usr/bin/env python3
"""
dashboard_view.py — Script-backed /dashboard command renderer
=============================================================
Deterministic dashboard renderer. Reads state files with automatic vault
lifecycle management (decrypt → read → re-encrypt). The LLM calls this
script; it never reads sensitive state files directly.

Usage:
  python scripts/dashboard_view.py
  python scripts/dashboard_view.py --format flash
  python scripts/dashboard_view.py --format standard   (default)
  python scripts/dashboard_view.py --format digest

Output: Markdown formatted dashboard ready for display.

Exit codes:
  0 — success
  1 — partial failure (vault unavailable, some domains skipped)
  2 — fatal error (state directory missing)

Ref: specs/enhance.md §10.0.1a
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

# Domains to include in dashboard, in display order
_DASHBOARD_DOMAINS = [
    "immigration", "finance", "kids", "health", "calendar",
    "comms", "goals", "home", "employment", "travel",
    "digital", "learning", "social", "boundary", "decisions",
]

_SENSITIVE_DOMAINS = {
    "immigration", "finance", "insurance", "estate",
    "health", "audit", "vehicle", "contacts", "occasions",
}


def _is_vault_unlocked() -> bool:
    return _LOCK_FILE.exists()


def _decrypt_vault() -> bool:
    """Attempt to decrypt vault. Returns True if successful or already unlocked."""
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
    """Re-encrypt vault. Called in finally block — always attempt."""
    try:
        subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "vault.py"), "encrypt"],
            capture_output=True, text=True, timeout=120, cwd=_ARTHA_DIR,
        )
    except Exception:
        pass  # Best-effort: vault watchdog will catch stragglers


def _read_state_file(domain: str) -> str:
    """Read a state file, returning its content or an error message."""
    path = _STATE_DIR / f"{domain}.md"
    if not path.exists():
        return f"_No state file found for {domain}._"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        return f"_Error reading {domain}: {e}_"


def _extract_frontmatter_value(content: str, key: str) -> str:
    """Extract a value from YAML frontmatter (key: value)."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith(f"{key}:"):
            value = line[len(f"{key}:"):].strip().strip('"\'')
            return value
    return ""


def _format_flash(domain_data: dict[str, str]) -> str:
    """Flash format: just the life pulse summary (1-2 lines per domain)."""
    lines = ["# Artha Dashboard — Flash\n"]
    has_alerts = False
    for domain, content in domain_data.items():
        status = _extract_frontmatter_value(content, "status") or "grey"
        if status in ("red", "yellow"):
            has_alerts = True
            icon = "🔴" if status == "red" else "🟡"
            lines.append(f"{icon} **{domain.title()}** — check full briefing")
    if not has_alerts:
        lines.append("✅ All domains nominal. No active alerts.")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_standard(domain_data: dict[str, str]) -> str:
    """Standard format: dashboard.md content + per-domain last_updated."""
    # First show the dashboard state file if it exists
    dashboard_content = _read_state_file("dashboard")
    lines = [dashboard_content, "\n---\n", "## Domain Status\n"]
    for domain, content in domain_data.items():
        status = _extract_frontmatter_value(content, "status") or "grey"
        last_updated = _extract_frontmatter_value(content, "last_updated") or "never"
        icon = {"green": "✅", "yellow": "🟡", "red": "🔴", "grey": "⬜"}.get(status, "⬜")
        lines.append(f"{icon} **{domain.title()}** · last updated: {last_updated}")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_digest(domain_data: dict[str, str]) -> str:
    """Digest format: full content of dashboard.md + all domain summaries."""
    dashboard_content = _read_state_file("dashboard")
    lines = [dashboard_content, "\n---\n"]
    for domain, content in domain_data.items():
        lines.append(f"\n### {domain.title()}\n")
        # Extract first non-frontmatter section (up to 10 lines)
        in_frontmatter = False
        section_lines = []
        for line in content.split("\n"):
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            if section_lines or line.strip():
                section_lines.append(line)
            if len(section_lines) >= 10:
                section_lines.append("_...truncated for digest_")
                break
        lines.extend(section_lines)
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Artha Dashboard Viewer")
    parser.add_argument(
        "--format", choices=["flash", "standard", "digest"], default="standard",
        help="Output density (default: standard)"
    )
    args = parser.parse_args()

    if not _STATE_DIR.exists():
        print("ERROR: state/ directory not found. Is this an Artha directory?", file=sys.stderr)
        return 2

    # Determine if we need to decrypt
    needs_decrypt = any(
        (_STATE_DIR / f"{d}.md").exists() is False and (_STATE_DIR / f"{d}.md.age").exists()
        for d in _SENSITIVE_DOMAINS
    ) or not _is_vault_unlocked()

    decrypted_here = False
    vault_available = _is_vault_unlocked()

    if needs_decrypt:
        vault_available = _decrypt_vault()
        decrypted_here = vault_available

    try:
        # Read all domain state files
        domain_data: dict[str, str] = {}
        for domain in _DASHBOARD_DOMAINS:
            if (_STATE_DIR / f"{domain}.md").exists():
                domain_data[domain] = _read_state_file(domain)

        if not domain_data:
            print("_No domain state files found. Run a catch-up first._")
            return 0

        # Render in requested format
        if args.format == "flash":
            output = _format_flash(domain_data)
        elif args.format == "digest":
            output = _format_digest(domain_data)
        else:
            output = _format_standard(domain_data)

        print(output)
        return 0

    finally:
        # Always re-encrypt if WE decrypted (leave vault state as-we-found-it)
        if decrypted_here and vault_available:
            _reencrypt_vault()


if __name__ == "__main__":
    sys.exit(main())
