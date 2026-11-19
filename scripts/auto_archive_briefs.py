#!/usr/bin/env python3
"""Compatibility Stop helper for the canonical staged Claude briefing."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DRAFT = ROOT / "tmp" / "briefing_incoming_claude.md"


def main():
    if not DRAFT.exists():
        return  # Nothing to archive

    from catchup_core.session_lock import publication_fence  # noqa: PLC0415
    from session_stop import archive_staged_briefing  # noqa: PLC0415

    expected = DRAFT.read_bytes()
    with publication_fence(DRAFT):
        archive_staged_briefing(ROOT, DRAFT, expected)
    print("[auto_archive_briefs] archived staged Claude briefing")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[auto_archive_briefs] warning: {e}", file=sys.stderr)
    sys.exit(0)  # Never block session exit
