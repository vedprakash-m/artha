#!/usr/bin/env python3
"""scripts/inbox_process.py — CLI for Artha inbox drop-folder processor.

Usage:
    python scripts/inbox_process.py                    # process all pending files
    python scripts/inbox_process.py --dry-run          # preview without writes
    python scripts/inbox_process.py --file inbox/work/notes.md  # single file
    python scripts/inbox_process.py --status           # show queue stats

Ref: specs/kb-graph.md §10.7
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT    = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="inbox_process.py",
        description="Process Artha inbox drop-folder files into the KB graph",
    )
    p.add_argument("--dry-run",  action="store_true", help="Preview only; no writes to KB or archive")
    p.add_argument("--file",     metavar="PATH",      help="Process a single file (relative or absolute)")
    p.add_argument("--status",   action="store_true", help="Show inbox queue stats and exit")
    p.add_argument("--verbose",  action="store_true", help="Enable debug logging")
    args = p.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from lib.knowledge_graph import get_kb
    from lib.inbox_processor import InboxProcessor

    kg        = get_kb()
    processor = InboxProcessor(_ROOT, kg, dry_run=args.dry_run)

    if args.status:
        stats = processor.stats()
        print(f"Inbox queue: {stats.get('pending', 0)} pending file(s)")
        return 0

    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = _ROOT / target
        if not target.exists():
            print(f"Error: file not found: {target}", file=sys.stderr)
            return 1
        ok = processor.process_file(target)
        return 0 if ok else 1

    # Process all pending files
    stats = processor.process_all()
    print(
        f"Inbox: processed={stats['processed']}, "
        f"skipped_dup={stats['skipped_dup']}, "
        f"skipped_pii={stats['skipped_pii']}, "
        f"skipped_size={stats['skipped_size']}, "
        f"failed={stats['failed']}"
    )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
