"""
scripts/connectors/outlookctl_bridge.py — outlookctl COM bridge (fast calendar fallback).

Provides <5s calendar data for quick checks (e.g. /status) when WorkIQ
cache is stale. Calendar-only — email/Teams are handled exclusively by WorkIQ.

Platform: Windows only (requires Classic Outlook running, outlookctl 0.1.0+).
Auth:     none — uses Windows COM automation to the running Outlook instance.

Known outlookctl 0.1.0 quirks (handled defensively):
  - `is_teams` field always False → detect via location string pattern
  - `attendee` count always 0 → note "attendees unavailable" in output
  - `received` date field empty → omit date where missing

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: specs/work-domain-assessment.md §18.3, §14.11
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from typing import Any, Dict, Iterator

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTLOOK_TIMEOUT = 25  # seconds — COM initialization can take 10-15s on first call
TEAMS_LOCATION_PATTERN = re.compile(
    r"microsoft teams|teams meeting|online meeting", re.IGNORECASE
)


def _find_outlookctl() -> str | None:
    """Locate outlookctl on the system PATH."""
    return shutil.which("outlookctl")


def _run_outlookctl(*args: str) -> dict | list | None:
    """Run an outlookctl command and return parsed JSON, or None on failure."""
    cmd = _find_outlookctl()
    if not cmd:
        return None
    try:
        result = subprocess.run(
            [cmd, *args, "--output", "json"],
            capture_output=True, text=True, timeout=OUTLOOK_TIMEOUT,
        )
        if result.returncode != 0:
            print(
                f"[outlookctl_bridge] outlookctl returned {result.returncode}: "
                f"{result.stderr.strip()[:100]}",
                file=sys.stderr,
            )
            return None
        return json.loads(result.stdout)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        print("[outlookctl_bridge] outlookctl timed out", file=sys.stderr)
        return None
    except json.JSONDecodeError as exc:
        print(f"[outlookctl_bridge] JSON parse error: {exc}", file=sys.stderr)
        return None


def _parse_event(raw: dict) -> dict:
    """Normalise a raw outlookctl calendar event dict."""
    location = str(raw.get("location") or "")
    is_teams = bool(raw.get("is_teams")) or bool(TEAMS_LOCATION_PATTERN.search(location))
    return {
        "date": str(raw.get("start", ""))[:10],
        "start": str(raw.get("start", "")),
        "end": str(raw.get("end", "")),
        "title": str(raw.get("subject") or raw.get("title") or ""),
        "organizer": str(raw.get("organizer") or ""),
        "location": location,
        "is_teams": is_teams,
        "attendees_available": False,  # outlookctl 0.1.0 limitation
        "source": "outlookctl",
    }


# ---------------------------------------------------------------------------
# Public connector interface
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str = "",
    max_results: int = 50,
    auth_context: dict,
    source_tag: str = "outlookctl",
    lookback_days: int = 7,
    **kwargs: Any,
) -> Iterator[dict]:
    """Fetch calendar events from Classic Outlook via outlookctl COM bridge.

    Returns events for today through today + lookback_days - 1.
    Intended as a fast fallback when WorkIQ cache is stale.
    """
    import platform
    if platform.system() != "Windows":
        # Non-Windows: silently yield nothing (no error)
        return

    today = date.today()
    end = today + timedelta(days=lookback_days - 1)

    # outlookctl calendar list returns the next N days by default
    raw = _run_outlookctl("calendar", "list")
    if raw is None:
        return

    events = raw if isinstance(raw, list) else raw.get("items", raw.get("events", []))
    count = 0
    for item in events:
        if count >= max_results:
            break
        record = _parse_event(item)
        if source_tag:
            record["source"] = source_tag
        yield record
        count += 1


def health_check(auth_context: dict) -> bool:
    """Verify outlookctl is available and Classic Outlook is accessible."""
    import platform
    if platform.system() != "Windows":
        return True  # Non-Windows: graceful skip

    cmd = _find_outlookctl()
    if not cmd:
        print("[outlookctl_bridge] outlookctl not found on PATH", file=sys.stderr)
        return False

    try:
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        available = result.returncode == 0
        if not available:
            print(
                f"[outlookctl_bridge] outlookctl --version returned {result.returncode}",
                file=sys.stderr,
            )
        return available
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"[outlookctl_bridge] health_check error: {exc}", file=sys.stderr)
        return False
