#!/usr/bin/env python3
"""
auto_archive_briefs.py — Stop hook: archive tmp/briefing_draft_claude.md
if present. Always exits 0 so it never blocks session exit.
"""
import sys
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
DRAFT = ROOT / "tmp" / "briefing_draft_claude.md"
BRIEFINGS = ROOT / "briefings"


def main():
    if not DRAFT.exists():
        return  # Nothing to archive

    BRIEFINGS.mkdir(exist_ok=True)

    today = date.today().isoformat()
    dest = BRIEFINGS / f"{today}.md"

    # If today's file already exists and has real content, use a suffix
    if dest.exists() and dest.stat().st_size > 50:
        dest = BRIEFINGS / f"{today}-claude.md"

    shutil.copy2(DRAFT, dest)
    DRAFT.unlink()
    print(f"[auto_archive_briefs] archived → briefings/{dest.name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[auto_archive_briefs] warning: {e}", file=sys.stderr)
    sys.exit(0)  # Never block session exit
