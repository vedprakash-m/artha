#!/usr/bin/env python3
"""
gcal_fetch.py — Artha Google Calendar fetch script
====================================================
Fetches calendar events in a given date range and outputs JSONL to stdout.

Usage:
  python scripts/gcal_fetch.py --from "2026-03-07" --to "2026-03-14"
  python scripts/gcal_fetch.py --from "2026-03-07T00:00:00" --to "2026-03-14T23:59:59"
  python scripts/gcal_fetch.py --health   (check auth + connectivity)
  python scripts/gcal_fetch.py --reauth   (force new OAuth flow)

Output (JSONL, one JSON object per event on stdout):
  {"id": "...", "calendar": "primary", "summary": "...",
   "start": "2026-03-07T15:30:00-08:00", "end": "...",
   "all_day": false, "location": "...", "description": "...",
   "attendees": [...], "status": "confirmed"}

Ref: TS §3.2, T-1A.3.3
"""

from __future__ import annotations

import sys, os as _os
_ARTHA_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _os.name == "nt":
    _VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win", "Scripts", "python.exe")
    _VENV_PREFIX = _os.path.realpath(_os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win"))
else:
    # Check project-relative .venv first (symlink on Mac → ~/.artha-venvs/.venv; real dir pre-move)
    _PROJ_VENV_PY = _os.path.join(_ARTHA_DIR, ".venv", "bin", "python")
    _LOCAL_VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv", "bin", "python")
    _VENV_PY = _PROJ_VENV_PY if _os.path.exists(_PROJ_VENV_PY) else _LOCAL_VENV_PY
    _VENV_PREFIX = _os.path.realpath(_os.path.dirname(_os.path.dirname(_VENV_PY)))
    # Auto-create venv from requirements.txt if not found (e.g. first run in Cowork VM)
    if not _os.path.exists(_VENV_PY):
        import subprocess as _sp
        _local_venv = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv")
        _sp.run([sys.executable, "-m", "venv", _local_venv], check=True, capture_output=True)
        _sp.run([_local_venv + "/bin/pip", "install", "-q", "-r",
                 _os.path.join(_ARTHA_DIR, "scripts", "requirements.txt")], capture_output=True)
        _VENV_PY = _local_venv + "/bin/python"
        _VENV_PREFIX = _os.path.realpath(_local_venv)
if _os.path.exists(_VENV_PY) and _os.path.realpath(sys.prefix) != _VENV_PREFIX:
    if _os.name == "nt":
        import subprocess as _sp; raise SystemExit(_sp.call([_VENV_PY] + sys.argv))
    else:
        _os.execv(_VENV_PY, [_VENV_PY] + sys.argv)

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Retry / back-off helper
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES  = 3
_BASE_DELAY   = 1.0
_BACKOFF_MULT = 2.0
_MAX_DELAY    = 30.0


def _with_retry(fn, *, retries: int = _MAX_RETRIES, context: str = ""):
    """
    Call fn() and retry on transient Google API errors (rate-limit / quota / 5xx).
    Raises on the final attempt or on non-retryable errors.
    """
    delay    = _BASE_DELAY
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            exc_str = str(exc).lower()
            is_retryable = (
                any(str(code) in exc_str for code in _RETRYABLE_STATUS_CODES)
                or "rate limit"       in exc_str
                or "quota"            in exc_str
                or "too many requests" in exc_str
                or "service unavailable" in exc_str
            )
            if not is_retryable or attempt == retries:
                raise type(exc)(
                    f"[gcal_fetch][{context}] failed after {attempt + 1} attempt(s): {exc}"
                ) from exc
            wait = min(delay, _MAX_DELAY)
            print(
                f"[gcal_fetch] ⚠ Rate-limited ({context}, attempt {attempt + 1}/{retries + 1})."
                f" Retrying in {wait:.0f}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
            delay     = min(delay * _BACKOFF_MULT, _MAX_DELAY)
            last_exc  = exc
    raise last_exc  # unreachable but satisfies type checkers


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def _parse_event(event: dict, calendar_name: str = "primary") -> dict:
    """Convert a Calendar API event object into a clean dict."""
    start_raw = event.get("start", {})
    end_raw   = event.get("end", {})

    # Events can be "dateTime" (timed) or "date" (all-day)
    start_dt  = start_raw.get("dateTime") or start_raw.get("date", "")
    end_dt    = end_raw.get("dateTime")   or end_raw.get("date", "")
    all_day   = "dateTime" not in start_raw

    # Attendees — extract email and name only (no response status stored)
    raw_attendees = event.get("attendees", [])
    attendees = [
        {
            "email": a.get("email", ""),
            "name":  a.get("displayName", ""),
            "self":  a.get("self", False),
        }
        for a in raw_attendees
    ]

    return {
        "id":           event.get("id", ""),
        "calendar":     calendar_name,
        "summary":      event.get("summary", "(no title)"),
        "start":        start_dt,
        "end":          end_dt,
        "all_day":      all_day,
        "location":     event.get("location", ""),
        "description":  (event.get("description") or "")[:500],  # cap description length
        "attendees":    attendees,
        "status":       event.get("status", "confirmed"),
        "visibility":   event.get("visibility", "default"),
        "recurring":    bool(event.get("recurringEventId")),
    }


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

def _to_rfc3339(date_str: str, end_of_day: bool = False) -> str:
    """
    Convert a date or datetime string to RFC 3339 format required by Calendar API.
    Handles both 'YYYY-MM-DD' and ISO datetime strings.
    """
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")

    # If it's just a date (YYYY-MM-DD), make it a full datetime
    if len(date_str) == 10 and "T" not in date_str:
        if end_of_day:
            dt = datetime.fromisoformat(date_str + "T23:59:59").replace(tzinfo=tz)
        else:
            dt = datetime.fromisoformat(date_str + "T00:00:00").replace(tzinfo=tz)
    else:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)

    return dt.isoformat()


def fetch_events(
    from_str: str,
    to_str: str,
    calendars: Optional[list[str]] = None,
    max_results: int = 250,
) -> list[dict]:
    """
    Fetch calendar events in the given date range.

    Args:
        from_str:    start date/datetime (ISO 8601 or YYYY-MM-DD)
        to_str:      end date/datetime
        calendars:   list of calendar IDs (default: primary + family + US holidays)
        max_results: maximum events per calendar

    Returns:
        list of parsed event dicts, sorted by start time
    """
    if calendars is None:
        calendars = [
            "primary",
            "family11404897395673522332@group.calendar.google.com",
            "en.usa#holiday@group.v.calendar.google.com",
        ]

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        from google_auth import build_service
    except ImportError:
        print("[gcal_fetch] ERROR: google_auth.py not found.", file=sys.stderr)
        sys.exit(1)

    print("[gcal_fetch] Connecting to Calendar API...", file=sys.stderr)
    service = build_service("calendar", "v3")

    time_min = _to_rfc3339(from_str, end_of_day=False)
    time_max = _to_rfc3339(to_str,   end_of_day=True)

    print(f"[gcal_fetch] Range: {time_min} → {time_max}", file=sys.stderr)

    all_events: list[dict] = []

    for cal_id in calendars:
        try:
            # Get calendar display name for labeling events
            cal_meta = _with_retry(
                lambda: service.calendars().get(calendarId=cal_id).execute(),
                context=f"calendars.get({cal_id})",
            )
            cal_name = cal_meta.get("summary", cal_id)
        except Exception:
            cal_name = cal_id

        print(f"[gcal_fetch] Fetching from calendar: '{cal_name}'", file=sys.stderr)

        try:
            response = _with_retry(
                lambda: service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,   # expand recurring events into individual instances
                    orderBy="startTime",
                ).execute(),
                context=f"events.list({cal_id})",
            )
        except Exception as exc:
            exc_str = str(exc).lower()
            if "quota" in exc_str or "rate limit" in exc_str or "too many requests" in exc_str:
                print(
                    f"[gcal_fetch] ERROR: quota exhausted for calendar '{cal_id}' after all retries.",
                    file=sys.stderr,
                )
                sys.exit(2)  # hard halt — quota exhaustion means all downstream data is unreliable
            print(f"[gcal_fetch] Warning: failed to fetch calendar '{cal_id}': {exc}",
                  file=sys.stderr)
            continue

        events = response.get("items", [])
        print(f"[gcal_fetch] {len(events)} events from '{cal_name}'", file=sys.stderr)

        for evt in events:
            # Skip cancelled events
            if evt.get("status") == "cancelled":
                continue
            all_events.append(_parse_event(evt, cal_name))

    # Sort by start time across all calendars
    def sort_key(evt: dict) -> str:
        return evt.get("start", "9999")

    all_events.sort(key=sort_key)
    print(f"[gcal_fetch] Total events: {len(all_events)}", file=sys.stderr)
    return all_events


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check() -> None:
    """Test Calendar authentication and connectivity."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        from google_auth import build_service, check_stored_credentials
    except ImportError:
        print("ERROR: google_auth.py not found.")
        sys.exit(1)

    print("Calendar Health Check")
    print("─" * 40)

    creds = check_stored_credentials()
    print(f"  Google token stored:   {'✓' if creds['gcal_token_stored'] else '✗ MISSING — run setup'}")

    if not creds["gcal_token_stored"]:
        print("\nAction required: python scripts/setup_google_oauth.py")
        sys.exit(1)

    print("\n  Testing Calendar API connection...")
    try:
        service = build_service("calendar", "v3")
        result  = service.calendarList().list().execute()
        cals    = result.get("items", [])
        print(f"  ✓ Connected. Calendars visible: {len(cals)}")
        for cal in cals[:5]:
            primary = " (primary)" if cal.get("primary") else ""
            print(f"    • {cal.get('summary', 'unknown')}{primary}")
        if len(cals) > 5:
            print(f"    ... and {len(cals) - 5} more")
        print("\nCalendar: OK")
    except Exception as exc:
        print(f"\n  ✗ Calendar connection failed: {exc}")
        print("\nTry: python scripts/gcal_fetch.py --reauth")
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Google Calendar events in a date range. Output: JSONL to stdout."
    )
    parser.add_argument(
        "--from", dest="from_date", type=str,
        help='Start date/datetime, e.g. "2026-03-07" or "2026-03-07T00:00:00"',
    )
    parser.add_argument(
        "--to", dest="to_date", type=str,
        help='End date/datetime, e.g. "2026-03-14" or "2026-03-14T23:59:59"',
    )
    parser.add_argument(
        "--calendars", type=str,
        default="primary,family11404897395673522332@group.calendar.google.com,en.usa#holiday@group.v.calendar.google.com",
        help="Comma-separated calendar IDs (default: primary + Mishra family + US holidays)",
    )
    parser.add_argument(
        "--max", type=int, default=250, dest="max_results",
        help="Max events per calendar (default: 250)",
    )
    parser.add_argument(
        "--health", action="store_true",
        help="Check authentication and calendar connectivity only",
    )
    parser.add_argument(
        "--reauth", action="store_true",
        help="Force a new OAuth flow",
    )
    parser.add_argument(
        "--today-plus-days", type=int, default=None,
        help="Shorthand: fetch from today to today+N days (overrides --from/--to)",
    )

    args = parser.parse_args()

    if args.health:
        run_health_check()
        return

    if args.reauth:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        try:
            from google_auth import build_service
            build_service("calendar", "v3", force_reauth=True)
            print("Re-authentication complete.", file=sys.stderr)
        except ImportError:
            print("ERROR: google_auth.py not found.")
            sys.exit(1)
        return

    # Handle --today-plus-days shorthand
    if args.today_plus_days is not None:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        today = datetime.now(tz=tz).date()
        from_date = str(today)
        to_date   = str(today + timedelta(days=args.today_plus_days))
    else:
        from_date = args.from_date
        to_date   = args.to_date

    if not from_date or not to_date:
        print("ERROR: Provide --from and --to dates, or use --today-plus-days.",
              file=sys.stderr)
        sys.exit(1)

    calendar_list = [c.strip() for c in args.calendars.split(",") if c.strip()]

    events = fetch_events(
        from_str=from_date,
        to_str=to_date,
        calendars=calendar_list,
        max_results=args.max_results,
    )

    for evt in events:
        print(json.dumps(evt, ensure_ascii=False))


if __name__ == "__main__":
    main()
