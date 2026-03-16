#!/usr/bin/env python3
"""
scripts/migrate_state.py — YAML-front-matter state schema migration tool.

Applies forward migrations to ``state/*.md`` files when the profile schema
version advances. Each migration is a small, declarative operation on the
YAML front matter block at the top of a state file.

Migration DSL
-------------
AddField(path, default)
    Insert a dotted-path key with the given default value if it does not
    already exist.  Example: AddField("meta.reviewed", False)

RenameField(old_path, new_path)
    Move a key to a new path. Data is copied then the old key is removed.
    No-op if old key is absent (idempotent).

DeprecateField(old_path, renamed_to=None)
    Mark a key as deprecated: if renamed_to is provided, the key is renamed
    (same as RenameField) and the old path is removed.
    If renamed_to is None, the key is simply removed (no-op if absent).

Migration registry
------------------
MIGRATIONS maps  ("from_version", "to_version") → [list of operations].
apply_migrations() walks the chain automatically from current to target.

Usage
-----
    python scripts/migrate_state.py              # migrate to latest
    python scripts/migrate_state.py --dry-run    # show changes, no writes
    python scripts/migrate_state.py --from 1.0 --to 1.1
    python scripts/migrate_state.py --check      # exit 1 if migration needed

Ref: specs/enhance.md §1.2
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPTS_DIR.parent
_STATE_DIR = _ROOT / "state"
_PROFILE_PATH = _ROOT / "config" / "user_profile.yaml"

# ---------------------------------------------------------------------------
# YAML front-matter helpers
# ---------------------------------------------------------------------------

_FM_PATTERN = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def _split_front_matter(text: str) -> tuple[dict | None, str]:
    """Return (front_matter_dict, body) or (None, text) if no front matter.

    The front matter must be a ``---``-delimited YAML block at the top of the
    file.  We use a minimal pure-stdlib parser for the simple key-value subset
    used in Artha state files (no nested structures needed for migration).
    """
    m = _FM_PATTERN.match(text)
    if not m:
        return None, text
    yaml_block = m.group(1)
    body = text[m.end():]
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(yaml_block) or {}
    except Exception:
        data = {}
    return data, body


def _join_front_matter(fm: dict, body: str) -> str:
    """Serialize front matter back to YAML + body."""
    try:
        import yaml  # noqa: PLC0415
        serialised = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception:
        # Fallback: naive key: value serialisation (no nested dicts)
        serialised = "\n".join(f"{k}: {v}" for k, v in fm.items()) + "\n"
    return f"---\n{serialised}---\n{body}"


def _get_nested(d: dict, path: str) -> Any:
    """Get a dotted-path value. Returns None if any part missing."""
    parts = path.split(".")
    node = d
    for p in parts:
        if isinstance(node, dict):
            node = node.get(p)
        else:
            return None
    return node


def _set_nested(d: dict, path: str, value: Any) -> None:
    """Set a dotted-path value, creating intermediate dicts as needed."""
    parts = path.split(".")
    node = d
    for p in parts[:-1]:
        if p not in node or not isinstance(node[p], dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value


def _del_nested(d: dict, path: str) -> None:
    """Delete a dotted-path key. No-op if absent."""
    parts = path.split(".")
    node = d
    for p in parts[:-1]:
        if isinstance(node, dict) and p in node:
            node = node[p]
        else:
            return  # path doesn't exist
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]


def _has_nested(d: dict, path: str) -> bool:
    return _get_nested(d, path) is not None


# ---------------------------------------------------------------------------
# Migration operation types
# ---------------------------------------------------------------------------

class AddField:
    """Add a field with a default value if it does not already exist."""

    def __init__(self, path: str, default: Any) -> None:
        self.path = path
        self.default = default

    def apply(self, fm: dict) -> bool:
        """Return True if fm was modified."""
        if _has_nested(fm, self.path):
            return False
        _set_nested(fm, self.path, self.default)
        return True

    def describe(self) -> str:
        return f"AddField({self.path!r}, {self.default!r})"


class RenameField:
    """Rename (move) a field from one path to another."""

    def __init__(self, old_path: str, new_path: str) -> None:
        self.old_path = old_path
        self.new_path = new_path

    def apply(self, fm: dict) -> bool:
        old_val = _get_nested(fm, self.old_path)
        if old_val is None:
            return False  # nothing to rename
        _set_nested(fm, self.new_path, old_val)
        _del_nested(fm, self.old_path)
        return True

    def describe(self) -> str:
        return f"RenameField({self.old_path!r} → {self.new_path!r})"


class DeprecateField:
    """Remove or rename a deprecated field."""

    def __init__(self, old_path: str, renamed_to: str | None = None) -> None:
        self.old_path = old_path
        self.renamed_to = renamed_to

    def apply(self, fm: dict) -> bool:
        if self.renamed_to:
            old_val = _get_nested(fm, self.old_path)
            if old_val is None:
                return False
            _set_nested(fm, self.renamed_to, old_val)
            _del_nested(fm, self.old_path)
            return True
        else:
            if not _has_nested(fm, self.old_path):
                return False
            _del_nested(fm, self.old_path)
            return True

    def describe(self) -> str:
        if self.renamed_to:
            return f"DeprecateField({self.old_path!r} → {self.renamed_to!r})"
        return f"DeprecateField(remove {self.old_path!r})"


# ---------------------------------------------------------------------------
# Migration registry
# Version key format: ("from_version", "to_version")
# ---------------------------------------------------------------------------

MIGRATIONS: dict[tuple[str, str], list] = {
    ("1.0", "1.1"): [
        # Add schema_version to front matter if missing
        AddField("meta.schema_version", "1.1"),
        # Rename 'last_updated' → 'meta.last_updated' for consistency
        RenameField("last_updated", "meta.last_updated"),
        # Add review_needed flag for change-tracking workflows
        AddField("meta.review_needed", False),
    ],
}

# Ordered chain of known versions
_VERSION_CHAIN = ["1.0", "1.1"]

LATEST_SCHEMA_VERSION = _VERSION_CHAIN[-1]


def _parse_version_simple(v: str) -> tuple:
    """Parse a version string to a comparable int tuple."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


# ---------------------------------------------------------------------------
# Migration execution
# ---------------------------------------------------------------------------

def _migration_path(from_ver: str, to_ver: str) -> list[tuple[str, str]]:
    """Return list of (from, to) steps to walk from from_ver to to_ver."""
    try:
        start = _VERSION_CHAIN.index(from_ver)
        end = _VERSION_CHAIN.index(to_ver)
    except ValueError as exc:
        raise ValueError(f"Unknown version in chain: {exc}") from exc
    if start >= end:
        return []
    return [(str(_VERSION_CHAIN[i]), str(_VERSION_CHAIN[i + 1])) for i in range(start, end)]


def migrate_file(
    path: Path,
    from_ver: str,
    to_ver: str,
    *,
    dry_run: bool = False,
) -> tuple[bool, list[str]]:
    """Apply all migrations from from_ver to to_ver to a single state file.

    Returns:
        (modified: bool, change_log: list[str])
        modified is True if any operation made a change.
    """
    text = path.read_text(encoding="utf-8")
    fm, body = _split_front_matter(text)
    if fm is None:
        return False, [f"  {path.name}: no front matter — skipped"]

    steps = _migration_path(from_ver, to_ver)
    change_log: list[str] = []
    modified = False

    for step_from, step_to in steps:
        ops = MIGRATIONS.get((step_from, step_to), [])
        for op in ops:
            fm_copy = copy.deepcopy(fm)
            changed = op.apply(fm_copy)
            if changed:
                change_log.append(f"  {path.name}: {op.describe()}")
                fm = fm_copy
                modified = True

    if modified and not dry_run:
        new_text = _join_front_matter(fm, body)
        path.write_text(new_text, encoding="utf-8")

    if not change_log:
        change_log.append(f"  {path.name}: already at v{to_ver} — no changes")

    return modified, change_log


def apply_migrations(
    state_dir: Path = _STATE_DIR,
    from_ver: str | None = None,
    to_ver: str = LATEST_SCHEMA_VERSION,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, list[str]]:
    """Apply migrations to all ``*.md`` state files.

    Args:
        state_dir: Directory containing state files.
        from_ver: Current schema version. Reads from profile if None.
        to_ver: Target version. Defaults to LATEST_SCHEMA_VERSION.
        dry_run: If True, compute changes but do not write files.
        verbose: If True, log files with no changes too.

    Returns:
        Dict mapping file path → list of change descriptions.
    """
    if from_ver is None:
        from_ver = _read_profile_schema_version()

    steps = _migration_path(from_ver, to_ver)
    if not steps:
        if verbose:
            print(f"[migrate] Already at schema v{to_ver} — nothing to do.")
        return {}

    state_files = sorted(state_dir.glob("*.md"))
    results: dict[str, list[str]] = {}

    for sf in state_files:
        modified, log = migrate_file(sf, from_ver, to_ver, dry_run=dry_run)
        if modified or verbose:
            results[str(sf)] = log

    if not dry_run and results:
        _update_profile_schema_version(to_ver)

    return results


def _read_profile_schema_version() -> str:
    """Read schema_version from user_profile.yaml, default to '1.0'."""
    if not _PROFILE_PATH.exists():
        return "1.0"
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(_PROFILE_PATH.read_text(encoding="utf-8")) or {}
        return str(data.get("schema_version", "1.0"))
    except Exception:
        return "1.0"


def _update_profile_schema_version(version: str) -> None:
    """Write schema_version to user_profile.yaml."""
    if not _PROFILE_PATH.exists():
        return
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(_PROFILE_PATH.read_text(encoding="utf-8")) or {}
        data["schema_version"] = version
        _PROFILE_PATH.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def check_needs_migration(state_dir: Path = _STATE_DIR) -> bool:
    """Return True if any state file needs migration."""
    from_ver = _read_profile_schema_version()
    to_ver = LATEST_SCHEMA_VERSION
    try:
        steps = _migration_path(from_ver, to_ver)
    except ValueError:
        return False
    if not steps:
        return False
    return bool(list(state_dir.glob("*.md")))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="migrate_state.py",
        description="Apply schema migrations to Artha state files.",
    )
    p.add_argument("--dry-run", action="store_true", help="Show changes without writing files")
    p.add_argument("--check", action="store_true", help="Exit 1 if migration is needed (no changes)")
    p.add_argument("--from", dest="from_ver", metavar="VERSION", help="Override source schema version")
    p.add_argument("--to", dest="to_ver", metavar="VERSION", default=LATEST_SCHEMA_VERSION,
                   help=f"Target version (default: {LATEST_SCHEMA_VERSION})")
    p.add_argument("--verbose", "-v", action="store_true", help="Show unchanged files too")
    args = p.parse_args(argv)

    if args.check:
        needed = check_needs_migration()
        if needed:
            print("[migrate] Migration needed — run 'python scripts/migrate_state.py' to apply.")
            return 1
        print("[migrate] No migration needed.")
        return 0

    from_ver = args.from_ver or _read_profile_schema_version()
    to_ver = args.to_ver

    print(f"[migrate] {from_ver} → {to_ver}" + (" (dry run)" if args.dry_run else ""))

    results = apply_migrations(
        from_ver=from_ver,
        to_ver=to_ver,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    if not results:
        print("[migrate] ✓ All state files already up to date.")
        return 0

    total_modified = sum(1 for k in results if "no changes" not in "\n".join(results[k]))
    for path, log in results.items():
        for line in log:
            print(line)

    if args.dry_run:
        print(f"\n[migrate] Dry run: {total_modified} file(s) would be modified.")
    else:
        print(f"\n[migrate] ✓ Migrated {total_modified} file(s) to v{to_ver}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
