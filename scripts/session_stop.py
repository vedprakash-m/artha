#!/usr/bin/env python3
"""
scripts/session_stop.py — Stop hook: archive once, check the health record, never block exit
=============================================================================================
re-artha §6.6.6 / §6.15.  A hook cannot make an agent follow instructions, so it checks
what a Catch-up must leave behind and records any omission in a minimal local fallback
file that ``stale_banner.py`` shows (once) at the next session open.

1. If ``tmp/briefing_draft_claude.md`` exists, archive it (``auto_archive_briefs``).
2. If a draft existed (a Catch-up ran) and ``state/health-check.md`` ``last_catch_up`` is
   still older than ``--max-age-hours`` (default 6), record that the health record was not written.

Exit code is always 0.  Stdlib only.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stale_banner  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def fallback_path(local: Path | None = None) -> Path:
    base = local or Path(os.environ.get("ARTHA_LOCAL_DIR") or Path.home() / ".artha-local").expanduser()
    return base / "stop-hook-failures.log"


def record(message: str, local: Path | None = None, now: datetime | None = None) -> None:
    path = fallback_path(local)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {message}\n")


def check(root: Path = ROOT, *, local: Path | None = None, now: datetime | None = None,
          max_age_hours: float = 6.0, archive=None) -> list[str]:
    """Run the once-only checks; return the problems recorded."""
    now = now or datetime.now(timezone.utc)
    draft = root / "tmp" / "briefing_draft_claude.md"
    ran = draft.exists()
    problems: list[str] = []
    if ran:
        try:
            (archive or _archive)()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Briefing archive failed: {type(exc).__name__}")
        if draft.exists():
            problems.append("Briefing draft is still in tmp/ after the archive step")
    if ran:
        last = stale_banner.read_last_catch_up(root / "state" / "health-check.md")
        try:
            age = (now - stale_banner._parse(last)).total_seconds() / 3600 if last else None
        except ValueError:
            age = None
        if age is None or age > max_age_hours:
            problems.append("state/health-check.md was not updated by this Catch-up")
    for problem in problems:
        record(problem, local, now)
    return problems


def _archive() -> None:
    import auto_archive_briefs  # noqa: PLC0415
    auto_archive_briefs.main()


def main() -> int:
    try:
        check()
    except Exception:  # noqa: BLE001 — never block session exit
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
