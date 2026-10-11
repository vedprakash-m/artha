#!/usr/bin/env python3
"""
scripts/stale_banner.py — stale-on-open banner (re-artha §6.7, RA-17, WP-02)
============================================================================
Nothing runs in the background to notice that Catch-ups stopped, so every
user-started interaction opens with how old the last completed Catch-up and
each source's last successful fetch are (PULL over PUSH, §1.4).

* Always prints one line with the Catch-up age.
* Adds a warning past the threshold (default 48 h, §12.3) and lists sources
  whose last successful fetch is older than the threshold or that never
  succeeded.
* Stdlib only; never raises; exit code 0 so a hook can never block a session.

Usage: python3 scripts/stale_banner.py [--health-check PATH] [--coverage PATH]
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `lib.*` when run as a script
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THRESHOLD_HOURS = 48
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_LAST_RE = re.compile(r"^last_catch_up:\s*[\"']?([^\"'\n]+)[\"']?\s*$", re.MULTILINE)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age(delta_hours: float) -> str:
    if delta_hours < 1:
        return "under 1h ago"
    if delta_hours < 48:
        return f"{int(delta_hours)}h ago"
    return f"{int(delta_hours // 24)} days ago"


def read_last_catch_up(health_check: Path) -> str | None:
    try:
        text = Path(health_check).read_text(encoding="utf-8")
    except OSError:
        return None
    fm = _FRONTMATTER_RE.match(text)
    if not fm:
        return None
    m = _LAST_RE.search(fm.group(1))
    return m.group(1).strip() if m else None


def build_banner(
    *, now: str | None, last_catch_up: str | None, entries: dict[str, Any],
    threshold_hours: float = THRESHOLD_HOURS,
) -> str:
    current = _parse(now) if now else datetime.now(timezone.utc)
    lines: list[str] = []
    if not last_catch_up:
        lines.append("⚠ Last Catch-up: no completed Catch-up on record — say `brief` to start one.")
    else:
        try:
            hours = max(0.0, (current - _parse(last_catch_up)).total_seconds() / 3600)
        except ValueError:
            hours = None
        if hours is None:
            lines.append("⚠ Last Catch-up: unreadable timestamp in health-check.md.")
        elif hours > threshold_hours:
            lines.append(f"⚠ Last Catch-up: {_age(hours)} — anything new since then is unseen. "
                         "Say `brief` to refresh.")
        else:
            lines.append(f"Last Catch-up: {_age(hours)}.")

    stale: list[str] = []
    for name, entry in sorted((entries or {}).items()):
        success = (entry or {}).get("last_success_at")
        if success:
            try:
                age_h = (current - _parse(success)).total_seconds() / 3600
            except ValueError:
                continue
            if age_h > threshold_hours:
                stale.append(f"{name} {_age(age_h)}")
        elif (entry or {}).get("failing_since"):
            stale.append(f"{name} never fetched successfully")
    if stale:
        lines[-1] = lines[-1].rstrip(".") + ". Sources last fetched: " + "; ".join(stale) + "."
        if not lines[-1].startswith("⚠"):
            lines[-1] = "⚠ " + lines[-1]
    return "\n".join(lines)


def _default_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parent.parent
    local = os.environ.get("ARTHA_LOCAL_DIR") or str(Path.home() / ".artha-local")
    return root / "state" / "health-check.md", Path(local).expanduser() / "connector_coverage.json"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    hc, cov = _default_paths()
    now: str | None = None
    it = iter(argv)
    for arg in it:
        if arg == "--health-check":
            hc = Path(next(it, str(hc)))
        elif arg == "--coverage":
            cov = Path(next(it, str(cov)))
        elif arg == "--now":
            now = next(it, None)
        elif arg == "--coverage-line":
            try:
                from lib.connector_coverage import CoverageStore, line_from_store  # noqa: PLC0415
                print(line_from_store(CoverageStore(cov), now=now))
            except Exception as exc:  # noqa: BLE001
                print(f"Coverage: unknown ({type(exc).__name__}).")
            return 0
    try:
        entries = json.loads(Path(cov).read_text(encoding="utf-8")).get("connectors", {})
    except (OSError, ValueError, AttributeError):
        entries = {}
    try:
        print(build_banner(now=now, last_catch_up=read_last_catch_up(hc), entries=entries))
    except Exception as exc:  # noqa: BLE001 — a banner must never break a session
        print(f"Last Catch-up: unknown ({type(exc).__name__}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
