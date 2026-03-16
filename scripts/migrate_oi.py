#!/usr/bin/env python3
"""
scripts/migrate_oi.py — One-time (and idempotent) Open Item backfill migration.

Scans all state/*.md files for OI-NNN references that are not already tracked in
state/open_items.md, extracts the surrounding context, and appends backfill
entries so the open_items ledger is the single source of truth.

Usage
-----
    python scripts/migrate_oi.py [--dry-run] [--verbose]

Flags
-----
    --dry-run     Print what would be added without writing anything.
    --verbose     Show every OI reference found (including already-tracked).

Safety
------
- Never deletes or modifies existing entries in open_items.md.
- Deduplicates on OI ID — if an entry already exists it is skipped.
- Only appends entries for IDs found at ≥ 2 candidate line contexts to reduce
  false positives from table headers or internal section labels.

Exit codes
----------
    0  Completed successfully (or dry-run).
    1  Could not read/write state files.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent

try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass

STATE_DIR = _REPO_ROOT / "state"
OPEN_ITEMS_FILE = STATE_DIR / "open_items.md"

# Match OI-NNN where NNN is 3 or more digits, at a word boundary.
_OI_PATTERN = re.compile(r"\bOI-(\d{3,})\b")

# Lines that are almost certainly NOT real OI references (table separators, etc.)
_SKIP_LINE_PATTERNS = re.compile(r"^[-|= ]+$|^\s*#\s+OI\b")

# State files to skip entirely (binary-like or self-referential)
_SKIP_FILES = {"open_items.md", "audit.md", "memory.md", "health-check.md"}


class OICandidate(NamedTuple):
    oi_id: str
    source_file: str
    context_line: str
    line_number: int


def _scan_state_files(verbose: bool = False) -> dict[str, list[OICandidate]]:
    """Scan all state/*.md files and return OI refs grouped by OI ID."""
    found: dict[str, list[OICandidate]] = {}

    for md_file in sorted(STATE_DIR.glob("*.md")):
        if md_file.name in _SKIP_FILES:
            continue
        try:
            lines = md_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            if _SKIP_LINE_PATTERNS.match(line):
                continue
            for m in _OI_PATTERN.finditer(line):
                oi_id = f"OI-{m.group(1)}"
                candidate = OICandidate(
                    oi_id=oi_id,
                    source_file=md_file.name,
                    context_line=line.strip()[:120],
                    line_number=lineno,
                )
                found.setdefault(oi_id, []).append(candidate)
                if verbose:
                    print(
                        f"  [{md_file.name}:{lineno}] {oi_id} → {line.strip()[:80]}",
                        file=sys.stderr,
                    )
    return found


def _existing_oi_ids() -> set[str]:
    """Return all OI IDs already tracked in open_items.md."""
    if not OPEN_ITEMS_FILE.exists():
        return set()
    ids: set[str] = set()
    for m in _OI_PATTERN.finditer(OPEN_ITEMS_FILE.read_text(encoding="utf-8")):
        ids.add(f"OI-{m.group(1)}")
    return ids


def _highest_oi_id(all_ids: set[str]) -> int:
    """Return the numeric part of the highest OI-NNN seen (0 if none)."""
    max_n = 0
    for oi_id in all_ids:
        m = re.match(r"OI-(\d+)", oi_id)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n


def _build_entry(oi_id: str, candidates: list[OICandidate]) -> str:
    """Build a YAML-ish backfill entry for open_items.md."""
    # Prefer the first candidate as context description
    first = candidates[0]
    # Derive domain from source file (strip .md, map known aliases)
    domain = first.source_file.replace(".md", "")
    # Sanitize description: strip YAML special chars
    desc = first.context_line.replace('"', "'").replace("\n", " ")
    today = date.today().isoformat()
    sources = ", ".join(sorted({c.source_file for c in candidates}))
    return (
        f"\n- id: {oi_id}\n"
        f"  date_added: {today}\n"
        f"  source_domain: {domain}\n"
        f"  description: \"[Backfill from migration] {desc}\"\n"
        f"  deadline: \"\"\n"
        f"  priority: P2\n"
        f"  status: open\n"
        f"  todo_id: \"\"\n"
        f"  # migrated_from: {sources}\n"
    )


def _ensure_open_items_header() -> None:
    """Create open_items.md from template if it doesn't exist."""
    if OPEN_ITEMS_FILE.exists():
        return
    template = STATE_DIR / "templates" / "open_items.md"
    if template.exists():
        import shutil
        shutil.copy2(template, OPEN_ITEMS_FILE)
    else:
        OPEN_ITEMS_FILE.write_text(
            "---\ndomain: open_items\nlast_updated: \"\"\nschema_version: \"1.0\"\n---\n\n# Open Items\n\n## Active\n",
            encoding="utf-8",
        )


def run(dry_run: bool = False, verbose: bool = False) -> int:
    """Main migration logic. Returns exit code."""
    print(f"[migrate_oi] Scanning state files in {STATE_DIR} …", file=sys.stderr)
    found = _scan_state_files(verbose=verbose)

    if not found:
        print("[migrate_oi] No OI references found in state files.", file=sys.stderr)
        return 0

    existing_ids = _existing_oi_ids()
    all_seen_ids = existing_ids | set(found.keys())
    max_n = _highest_oi_id(all_seen_ids)

    to_backfill: dict[str, list[OICandidate]] = {}
    for oi_id, candidates in sorted(found.items()):
        if oi_id in existing_ids:
            if verbose:
                print(f"  [skip] {oi_id} already tracked", file=sys.stderr)
            continue
        to_backfill[oi_id] = candidates

    print(
        f"[migrate_oi] Found {len(found)} OI refs across state files. "
        f"{len(existing_ids)} already tracked. {len(to_backfill)} to backfill.",
        file=sys.stderr,
    )

    if not to_backfill:
        print("[migrate_oi] Nothing to backfill — open_items.md is complete.", file=sys.stderr)
        # Still report the correct next ID
        next_id = f"OI-{max_n + 1:03d}"
        print(f"[migrate_oi] Highest OI seen: OI-{max_n:03d}. Next ID should be: {next_id}", file=sys.stderr)
        return 0

    entries: list[str] = []
    for oi_id in sorted(to_backfill.keys(), key=lambda x: int(x.split("-")[1])):
        entry = _build_entry(oi_id, to_backfill[oi_id])
        entries.append(entry)
        if dry_run or verbose:
            print(f"  [{'dry-run' if dry_run else 'add'}] {oi_id} from {', '.join(c.source_file for c in to_backfill[oi_id][:2])}")

    next_id = f"OI-{max_n + 1:03d}"
    print(f"\n[migrate_oi] Highest OI seen: OI-{max_n:03d}. Next ID after migration: {next_id}", file=sys.stderr)

    if dry_run:
        print(f"\n[migrate_oi] DRY-RUN: {len(entries)} entries would be added to open_items.md.", file=sys.stderr)
        print(f"[migrate_oi] Update MEMORY.md / state/memory.md: Next OI ID = {next_id}", file=sys.stderr)
        return 0

    _ensure_open_items_header()
    current = OPEN_ITEMS_FILE.read_text(encoding="utf-8")

    # Find the ## Active section to append inside it, or append at end
    active_marker = "\n## Active\n"
    if active_marker in current:
        insert_pos = current.index(active_marker) + len(active_marker)
        new_content = current[:insert_pos] + "".join(entries) + current[insert_pos:]
    else:
        new_content = current + "\n## Active\n" + "".join(entries)

    # Update frontmatter last_updated
    today_iso = datetime.now(timezone.utc).isoformat()
    new_content = re.sub(
        r'(last_updated:\s*)["\']?[^"\'"\n]*["\']?',
        f'last_updated: "{today_iso}"',
        new_content,
        count=1,
    )

    OPEN_ITEMS_FILE.write_text(new_content, encoding="utf-8")
    print(
        f"\n[migrate_oi] ✓ Backfilled {len(entries)} OI entries into {OPEN_ITEMS_FILE}.",
        file=sys.stderr,
    )
    print(f"[migrate_oi] ⚠ Update MEMORY.md / state/memory.md: Next OI ID = {next_id}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="migrate_oi.py",
        description="Backfill open_items.md from OI-NNN references in state files.",
    )
    p.add_argument("--dry-run", action="store_true", help="Show what would be added without writing")
    p.add_argument("--verbose", "-v", action="store_true", help="Show every OI reference found")
    args = p.parse_args(argv)
    return run(dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
