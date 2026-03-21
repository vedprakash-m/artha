#!/usr/bin/env python3
# pii-guard: ignore-file — processes briefing files; no personal data in code
"""scripts/bootstrap_memory.py — Seed state/memory.md from existing briefings.

One-time script that jumpstarts the memory pipeline by processing all
historical briefings.  Safe to run multiple times — deduplication prevents
duplicate facts from being persisted.

Usage:
    python3 scripts/bootstrap_memory.py [--dry-run]

Batch extraction pattern (required): collects ALL facts first, then calls
persist_facts() once.  Calling persist_facts() per-briefing would trigger
AR-1 consolidation between briefings, causing premature eviction of earlier
facts — the result would be order-dependent.  Batch extraction + single RMW
cycle ensures consolidation fires exactly once and is order-independent.

Also seeds state/learned_procedures/ from scripts/bootstrap_seeds.yaml.

Ref: specs/mem.md Phase 4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _seed_procedures(artha_dir: Path, dry_run: bool = False) -> int:
    """Write procedure files from scripts/bootstrap_seeds.yaml.

    Returns the count of newly written procedures (existing files are skipped
    — idempotent).
    """
    seeds_path = _SCRIPTS_DIR / "bootstrap_seeds.yaml"
    if not seeds_path.exists():
        return 0
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(seeds_path.read_text(encoding="utf-8")) or {}
        procedures = data.get("procedures", [])
        proc_dir = artha_dir / "state" / "learned_procedures"
        written = 0
        for proc in procedures:
            domain = proc.get("domain", "general")
            trigger_slug = proc.get("trigger_slug", "unknown")
            content = proc.get("content", "").strip()
            if not content:
                continue
            filename = f"{domain}-{trigger_slug}.md"
            dest = proc_dir / filename
            if dest.exists():
                continue  # Idempotent — never overwrite existing procedures
            if dry_run:
                print(f"  [dry-run] would write {filename}")
            else:
                proc_dir.mkdir(parents=True, exist_ok=True)
                dest.write_text(content + "\n", encoding="utf-8")
            written += 1
        return written
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Procedure seeding failed: {exc}", file=sys.stderr)
        return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Seed state/memory.md from existing briefing files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be extracted without writing to any state files.",
    )
    parser.add_argument(
        "--artha-dir",
        metavar="DIR",
        default=str(_ARTHA_DIR),
        help="Artha project root (default: auto-detected from script location).",
    )
    args = parser.parse_args()
    artha_dir = Path(args.artha_dir).resolve()
    dry_run = args.dry_run

    from fact_extractor import extract_facts_from_summary, persist_facts  # noqa: PLC0415

    briefings_dir = artha_dir / "briefings"
    if not briefings_dir.exists():
        print("⚠️  briefings/ directory not found.", file=sys.stderr)
        return 1

    # Sort chronologically (oldest first so earliest occurrence wins dedup)
    briefing_files = sorted(briefings_dir.glob("*.md"))
    if not briefing_files:
        print("ℹ️  No briefing files found.", file=sys.stderr)
        return 0

    print(f"📂 Processing {len(briefing_files)} briefing(s)...")
    all_facts = []
    seen_ids: set[str] = set()

    for bf in briefing_files:
        try:
            facts = extract_facts_from_summary(bf, artha_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  {bf.name}: extraction failed ({exc})", file=sys.stderr)
            continue

        # Batch-level deduplication — earliest occurrence wins
        new_facts = [f for f in facts if f.id not in seen_ids]
        for f in new_facts:
            seen_ids.add(f.id)
        all_facts.extend(new_facts)
        if new_facts:
            print(f"  ✅ {bf.name}: {len(new_facts)} fact(s)")

    print(f"\n📊 Total unique facts extracted: {len(all_facts)}")

    if dry_run:
        print("🔍 [dry-run] — not writing to state/memory.md")
        for f in all_facts:
            print(f"  [{f.type}/{f.domain}] {f.statement[:90]}")
        # Still seed procedures in dry-run (shows what would happen)
        _seed_procedures(artha_dir, dry_run=True)
        return 0

    # Single persist_facts() call — AR-1 consolidation fires exactly once
    persisted = persist_facts(all_facts, artha_dir) if all_facts else 0
    print(f"✅ Persisted {persisted} new fact(s) to state/memory.md")

    proc_count = _seed_procedures(artha_dir, dry_run=False)
    if proc_count:
        print(f"✅ Seeded {proc_count} procedure(s) to state/learned_procedures/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
