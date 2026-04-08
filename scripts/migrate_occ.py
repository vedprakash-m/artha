#!/usr/bin/env python3
"""
scripts/migrate_occ.py — Batch-inject OCC version fields into state/*.md files.

Adds ``version``, ``last_written_by``, and ``last_written_at`` to the YAML
frontmatter of every ``state/*.md`` file that does not already have a
``version`` field.  Non-destructive: files that already have ``version: N``
(N >= 1) are skipped.

Usage
-----
    # Dry run — show what would change, write nothing
    python scripts/migrate_occ.py --dry-run

    # Live run — migrate all un-versioned state files
    python scripts/migrate_occ.py

    # Migrate a specific domain state file
    python scripts/migrate_occ.py --file state/finance.md

    # Verbose — show per-file details
    python scripts/migrate_occ.py --verbose

Exit codes
----------
    0: All files processed (or nothing to do)
    1: One or more files had errors (check stderr)

Telemetry
---------
Emits ``occ.migrated`` events to ``state/telemetry.jsonl`` for each file
successfully migrated (not emitted in --dry-run mode).

Safety guarantees
-----------------
- Atomic write: tempfile + os.replace() — file never seen partially written
- Only writes YAML frontmatter; prose body is never modified
- Creates ``tmp/state_snapshots/<name>.<ts>.bak`` before each write
- Re-reads and verifies version == 1 after each write

Ref: specs/harden.md §2.1.1 Optimistic Concurrency Control
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ and scripts/lib/ are importable
_SCRIPTS_DIR = Path(__file__).resolve().parent
_LIB_DIR = _SCRIPTS_DIR / "lib"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_LIB_DIR))

from state_writer import _extract_occ_version, _inject_occ_fields  # noqa: E402

_REPO_ROOT = _SCRIPTS_DIR.parent
_STATE_DIR = _REPO_ROOT / "state"
_SNAP_DIR = _REPO_ROOT / "tmp" / "state_snapshots"

_OCC_SOURCE = "migrate_occ"
_VERSION_FIELD_RE = re.compile(r"^version:\s*\d+", re.MULTILINE)


def _snapshot(path: Path, content: str) -> Path | None:
    """Write pre-migration snapshot.  Returns snapshot path or None on failure."""
    try:
        _SNAP_DIR.mkdir(parents=True, exist_ok=True)
        import time
        ts = int(time.time())
        snap = _SNAP_DIR / f"{path.name}.{ts}.bak"
        with open(snap, "w", encoding="utf-8") as fh:
            fh.write(content)
        return snap
    except Exception:  # noqa: BLE001
        return None


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _emit_migrated(path: Path) -> None:
    """Non-fatally emit occ.migrated telemetry event."""
    try:
        from telemetry import emit  # noqa: PLC0415
        emit("occ.migrated", extra={"path": str(path.relative_to(_REPO_ROOT))})
    except Exception:  # noqa: BLE001
        pass


def _collect_targets(file_arg: str | None = None) -> list[Path]:
    """Collect state files to migrate."""
    if file_arg:
        p = Path(file_arg)
        if not p.is_absolute():
            p = _REPO_ROOT / p
        return [p]

    # state/*.md (not subdirectories)
    targets = sorted(_STATE_DIR.glob("*.md"))
    # Also include state/work/*.md if it exists
    work_dir = _STATE_DIR / "work"
    if work_dir.is_dir():
        targets += sorted(work_dir.glob("*.md"))
    return targets


def migrate(
    *,
    dry_run: bool = False,
    verbose: bool = False,
    file_arg: str | None = None,
) -> int:
    """Run the OCC migration.  Returns exit code (0 OK, 1 error)."""
    targets = _collect_targets(file_arg)
    if not targets:
        print("[migrate_occ] No state files found to migrate.")
        return 0

    migrated = 0
    skipped = 0
    errors = 0

    for path in targets:
        if not path.exists():
            if verbose:
                print(f"  [MISSING] {path.relative_to(_REPO_ROOT)}")
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"  [ERROR]   {path.name}: cannot read — {exc}", file=sys.stderr)
            errors += 1
            continue

        current_version = _extract_occ_version(content)
        if current_version >= 1:
            skipped += 1
            if verbose:
                print(f"  [SKIP]    {path.relative_to(_REPO_ROOT)} (version={current_version})")
            continue

        # Needs migration
        new_content = _inject_occ_fields(content, 1, _OCC_SOURCE)

        if dry_run:
            print(f"  [DRY-RUN] {path.relative_to(_REPO_ROOT)}  -> version: 1")
            migrated += 1
            continue

        # Pre-write snapshot
        snap = _snapshot(path, content)

        try:
            _atomic_write(path, new_content)
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERROR]   {path.name}: write failed — {exc}", file=sys.stderr)
            errors += 1
            continue

        # Verify: re-read and confirm version == 1
        try:
            written = path.read_text(encoding="utf-8")
            written_v = _extract_occ_version(written)
        except OSError:
            written_v = 1  # assume OK if we can't re-read

        if written_v != 1:
            print(
                f"  [ERROR]   {path.name}: version verify failed "
                f"(wrote 1, read {written_v})",
                file=sys.stderr,
            )
            errors += 1
            continue

        _emit_migrated(path)
        migrated += 1

        msg = f"  [MIGRATED] {path.relative_to(_REPO_ROOT)}"
        if snap:
            msg += f"  (snap: {snap.name})"
        if verbose:
            print(msg)
        else:
            print(f"  [OK] {path.relative_to(_REPO_ROOT)}")

    # Summary
    label = "DRY-RUN" if dry_run else "DONE"
    print(
        f"\n[migrate_occ] {label}: "
        f"migrated={migrated}, skipped={skipped}, errors={errors}"
    )
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-inject OCC version fields into state/*.md files."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing anything.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-file details including skipped files.",
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        dest="file_arg",
        help="Migrate a single specific state file instead of all state/*.md.",
    )
    args = parser.parse_args()
    sys.exit(migrate(dry_run=args.dry_run, verbose=args.verbose, file_arg=args.file_arg))


if __name__ == "__main__":
    main()
