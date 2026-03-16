#!/usr/bin/env python3
"""
scripts/upgrade.py — Artha upgrade helper.

Detects version drift between the installed codebase and the state stored
in state/health-check.md, then applies non-destructive upgrades:

  1. Detects version drift: compares pyproject.toml version with stored
     artha_version in state/health-check.md
  2. If Artha.core.md changed: re-runs generate_identity.py
  3. If connectors.yaml has new connectors: prints diff, user can enable
  4. If new domain prompts added: lists them (they auto-activate)
  5. Preserves all user data, config, and state — fully non-destructive

Usage:
    python scripts/upgrade.py          # check and apply upgrades
    python scripts/upgrade.py --check  # check only, no changes
    python scripts/upgrade.py --force  # force regenerate identity

Exit codes:
    0  up-to-date (or upgrade applied)
    1  upgrade available but not applied (use --force or follow prompts)
    2  error (missing files, YAML syntax error, etc.)

Ref: specs/supercharge.md §9.3
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_STATE_DIR = _REPO_ROOT / "state"
_CONFIG_DIR = _REPO_ROOT / "config"

_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_HEALTH_CHECK = _STATE_DIR / "health-check.md"
_ARTHA_CORE = _CONFIG_DIR / "Artha.core.md"
_ARTHA_MD = _CONFIG_DIR / "Artha.md"
_CONNECTORS_YAML = _CONFIG_DIR / "connectors.yaml"
_GENERATE_IDENTITY = _SCRIPTS_DIR / "generate_identity.py"


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> tuple:
    """Parse a version string into a comparable tuple of ints.
    Handles: '5.1.0', '5.1', '5', 'unknown' (returns (0,))."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


def _current_version() -> str:
    """Read version from pyproject.toml."""
    if not _PYPROJECT.exists():
        return "unknown"
    text = _PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "unknown"


def _stored_version() -> str:
    """Read artha_version from state/health-check.md."""
    if not _HEALTH_CHECK.exists():
        return ""
    text = _HEALTH_CHECK.read_text(encoding="utf-8")
    m = re.search(r"artha_version:\s*([^\s]+)", text)
    return m.group(1) if m else ""


def _write_stored_version(version: str) -> None:
    """Write or update artha_version in state/health-check.md."""
    if not _HEALTH_CHECK.exists():
        return
    text = _HEALTH_CHECK.read_text(encoding="utf-8")
    if re.search(r"artha_version:", text):
        text = re.sub(r"artha_version:\s*\S+", f"artha_version: {version}", text)
    else:
        text += f"\nartha_version: {version}\n"
    _HEALTH_CHECK.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Hash-based change detection
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _stored_core_hash() -> str:
    if not _HEALTH_CHECK.exists():
        return ""
    text = _HEALTH_CHECK.read_text(encoding="utf-8")
    m = re.search(r"artha_core_hash:\s*(\S+)", text)
    return m.group(1) if m else ""


def _write_core_hash(h: str) -> None:
    if not _HEALTH_CHECK.exists():
        return
    text = _HEALTH_CHECK.read_text(encoding="utf-8")
    if re.search(r"artha_core_hash:", text):
        text = re.sub(r"artha_core_hash:\s*\S+", f"artha_core_hash: {h}", text)
    else:
        text += f"\nartha_core_hash: {h}\n"
    _HEALTH_CHECK.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Connector diff
# ---------------------------------------------------------------------------

def _list_connector_names() -> list[str]:
    """Return list of connector names from connectors.yaml."""
    if not _CONNECTORS_YAML.exists():
        return []
    try:
        import yaml  # type: ignore[import]
        cfg = yaml.safe_load(_CONNECTORS_YAML.read_text(encoding="utf-8")) or {}
        return [c["name"] for c in cfg.get("connectors", []) if isinstance(c, dict)]
    except Exception:
        return []


def _stored_connector_names() -> list[str]:
    if not _HEALTH_CHECK.exists():
        return []
    text = _HEALTH_CHECK.read_text(encoding="utf-8")
    m = re.search(r"artha_connectors:\s*\[([^\]]*)\]", text)
    if not m:
        return []
    raw = m.group(1)
    return [n.strip().strip("'\"") for n in raw.split(",") if n.strip()]


def _write_connector_names(names: list[str]) -> None:
    if not _HEALTH_CHECK.exists():
        return
    text = _HEALTH_CHECK.read_text(encoding="utf-8")
    val = "[" + ", ".join(f"'{n}'" for n in names) + "]"
    if re.search(r"artha_connectors:", text):
        text = re.sub(r"artha_connectors:\s*\[.*?\]", f"artha_connectors: {val}", text)
    else:
        text += f"\nartha_connectors: {val}\n"
    _HEALTH_CHECK.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# New domain prompt detection
# ---------------------------------------------------------------------------

def _list_prompts() -> list[str]:
    prompts_dir = _REPO_ROOT / "prompts"
    if not prompts_dir.exists():
        return []
    return sorted(
        p.name for p in prompts_dir.glob("*.md") if p.name != "README.md"
    )


# ---------------------------------------------------------------------------
# Main upgrade logic
# ---------------------------------------------------------------------------

def upgrade(*, check_only: bool = False, force: bool = False, verbose: bool = False) -> int:
    """Detect and apply upgrades. Returns 0 (up-to-date), 1 (needs action), 2 (error)."""
    current_ver = _current_version()
    stored_ver = _stored_version()
    current_core_hash = _file_hash(_ARTHA_CORE)
    stored_core_hash = _stored_core_hash()
    current_connectors = _list_connector_names()
    stored_connectors = _stored_connector_names()

    new_connectors = [c for c in current_connectors if c not in stored_connectors]

    needs_identity_regen = (
        force
        or (current_core_hash != stored_core_hash and stored_core_hash != "")
    )
    version_changed = _parse_version(current_ver) != _parse_version(stored_ver)
    has_updates = bool(new_connectors) or needs_identity_regen or version_changed

    if verbose or not has_updates:
        print(f"[upgrade] Artha version:  {current_ver} (stored: {stored_ver or 'none'})")
        print(f"[upgrade] Artha.core.md:  {'changed' if needs_identity_regen else 'unchanged'}")
        print(f"[upgrade] New connectors: {new_connectors or 'none'}")

    if not has_updates and not force:
        print("[upgrade] ✓ Artha is up to date.")
        return 0

    if check_only:
        if new_connectors:
            print(f"[upgrade] New connectors available: {new_connectors}")
            print("          Enable them in config/connectors.yaml (set enabled: true)")
        if needs_identity_regen:
            print("[upgrade] Artha.core.md changed — run 'python scripts/upgrade.py' to rebuild Artha.md")
        return 1

    # Apply upgrades
    any_error = False

    if needs_identity_regen or force:
        print("[upgrade] Artha.core.md changed — regenerating Artha.md ...")
        rc = subprocess.call([sys.executable, str(_GENERATE_IDENTITY)])
        if rc != 0:
            print("[upgrade] ERROR: generate_identity.py failed (see above)", file=sys.stderr)
            any_error = True
        else:
            print("[upgrade] ✓ Artha.md rebuilt.")
            _write_core_hash(current_core_hash)

    if new_connectors:
        print(f"\n[upgrade] New connectors available:")
        for name in new_connectors:
            print(f"  + {name}")
        print("\nTo enable: open config/connectors.yaml and set 'enabled: true' for each.")
        _write_connector_names(current_connectors)

    _write_stored_version(current_ver)

    # Run state schema migrations if any are pending
    try:
        from migrate_state import apply_migrations, check_needs_migration  # noqa: PLC0415
        if check_needs_migration():
            print("[upgrade] State schema migration needed — applying ...")
            mig_results = apply_migrations(verbose=False)
            migrated = sum(1 for log in mig_results.values() if "no changes" not in "\n".join(log))
            if migrated:
                print(f"[upgrade] ✓ Migrated {migrated} state file(s) to latest schema.")
    except ImportError:
        pass  # migrate_state not yet available (first run)
    except Exception as exc:
        print(f"[upgrade] WARNING: State migration skipped: {exc}", file=sys.stderr)

    if any_error:
        return 2

    print("[upgrade] ✓ Upgrade complete.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="upgrade.py",
        description="Detect and apply Artha upgrades non-destructively.",
    )
    p.add_argument(
        "--check", action="store_true", help="Check for updates without applying"
    )
    p.add_argument(
        "--force", action="store_true", help="Force regenerate Artha.md"
    )
    p.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose output"
    )
    args = p.parse_args(argv)
    return upgrade(check_only=args.check, force=args.force, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
