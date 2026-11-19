#!/usr/bin/env python3
"""
scripts/session_stop.py — Stop hook: archive once, check the health record, never block exit
=============================================================================================
re-artha §6.6.6 / §6.15.  A hook cannot make an agent follow instructions, so it checks
what a Catch-up must leave behind and records any omission in a minimal local fallback
file that ``stale_banner.py`` shows (once) at the next session open.

1. If ``tmp/briefing_incoming_claude.md`` exists, archive it through the
   canonical ``briefing_archive`` owner.
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


def archive_staged_briefing(
    root: Path,
    draft: Path,
    expected_bytes: bytes,
) -> None:
    """Publish one bound staged draft through the canonical briefing owner."""
    from lib import briefing_archive  # noqa: PLC0415

    expected = (root / "tmp" / "briefing_incoming_claude.md").resolve()
    if draft.resolve() != expected:
        raise RuntimeError("unexpected briefing draft")
    if not draft.exists() or draft.read_bytes() != expected_bytes:
        raise RuntimeError("briefing draft changed before archive")
    result = briefing_archive.save(
        expected_bytes.decode("utf-8"),
        source="interactive_cli",
        runtime="claude",
    )
    if result.get("status") not in ("ok", "skipped"):
        raise RuntimeError(f"briefing archive rejected draft: {result.get('error', 'unknown')}")
    if not draft.exists() or draft.read_bytes() != expected_bytes:
        raise RuntimeError("briefing draft changed after archive")
    draft.unlink()


def check(root: Path = ROOT, *, local: Path | None = None, now: datetime | None = None,
          max_age_hours: float = 6.0, archive=None) -> list[str]:
    """Run the once-only checks; return the problems recorded."""
    now = now or datetime.now(timezone.utc)
    draft = root / "tmp" / "briefing_incoming_claude.md"
    legacy = root / "tmp" / "briefing_draft_claude.md"
    draft_unreadable = False
    try:
        draft_bytes = draft.read_bytes() if draft.exists() else None
    except OSError:
        draft_bytes = None
        draft_unreadable = draft.exists()
    ran = draft_bytes is not None or draft_unreadable or legacy.exists()
    problems: list[str] = []
    if draft_unreadable:
        problems.append("Briefing draft could not be read; publication is held")
    if legacy.exists():
        problems.append(
            "Legacy briefing draft is held: "
            "stage it as tmp/briefing_incoming_claude.md"
        )
    if draft_bytes is not None:
        try:
            from catchup_core.session_lock import publication_fence  # noqa: PLC0415
            with publication_fence(draft):
                if not draft.exists() or draft.read_bytes() != draft_bytes:
                    raise RuntimeError("briefing draft changed before archive")
                if archive is None:
                    archive_staged_briefing(root, draft, draft_bytes)
                else:
                    archive()
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


def main() -> int:
    try:
        check()
    except Exception:  # noqa: BLE001 — never block session exit
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
