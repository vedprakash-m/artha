#!/usr/bin/env python3
"""
icloud_calendar_fetch.py — Artha iCloud Calendar fetch script
==============================================================
Fetches iCloud Calendar events in a given date range via CalDAV and outputs
JSONL to stdout. Designed to run in parallel with gcal_fetch.py and
msgraph_calendar_fetch.py at catch-up Step 4.

Apple CalDAV server: caldav.icloud.com  (RFC 4791)
Auth: Apple ID + app-specific password (setup_icloud_auth.py)

Usage:
  python scripts/icloud_calendar_fetch.py --from "2026-03-07" --to "2026-03-14"
  python scripts/icloud_calendar_fetch.py --today-plus-days 7
  python scripts/icloud_calendar_fetch.py --calendars "Home,Work"  (subset; by name/URL)
  python scripts/icloud_calendar_fetch.py --health               (CalDAV check + calendar list)
  python scripts/icloud_calendar_fetch.py --list-calendars       (print all calendar names/URLs)
  python scripts/icloud_calendar_fetch.py --reauth               (re-run credential setup)

Output (JSONL, one JSON object per event on stdout):
  {"id": "...", "calendar": "Calendar Name", "summary": "...",
   "start": "2026-03-07T15:30:00+00:00", "end": "2026-03-07T16:30:00+00:00",
   "all_day": false, "location": "...", "description": "...",
   "organizer": "...", "attendees": [...], "status": "confirmed",
   "visibility": "normal", "recurring": false, "source": "icloud_calendar"}

Schema is intentionally compatible with gcal_fetch.py and
msgraph_calendar_fetch.py output. The "source": "icloud_calendar" field
identifies the feed.

Reminders (VTODO) output (with --reminders flag):
  {"id": "...", "calendar": "Reminders", "title": "...",
   "due": "2026-03-10T00:00:00+00:00", "created": "...", "modified": "...",
   "completed_at": "", "description": "...", "priority": "high",
   "status": "pending", "recurring": false, "source": "icloud_reminder"}

Deduplication note (Artha.md Step 4):
  Events matching on (summary ± minor variation) AND (start ± 5 minutes)
  across any two calendar feeds → deduplicate and set source: "both".

Requires: caldav Python package (pip install caldav)

Errors → stderr. Exit codes: 0 = success, 1 = error.

Ref: TS §3.9, T-1B.1.10
"""

from __future__ import annotations

import sys
# Ensure we run inside the Artha venv. Ref: standardization.md §7.3
from _bootstrap import reexec_in_venv; reexec_in_venv()

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Iterator, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CALDAV_URL  = "https://caldav.icloud.com"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_cal_name(cal) -> str:
    """Get a CalDAV calendar's display name using the current API (3.x)."""
    try:
        return cal.get_display_name() or str(cal.url)
    except Exception:  # noqa: BLE001
        # Fallback for older caldav versions
        return getattr(cal, "name", None) or str(cal.url)


# ---------------------------------------------------------------------------
# Date/time helpers
# ---------------------------------------------------------------------------

def _to_utc_dt(value: "date | datetime") -> datetime:
    """
    Convert a vObject dtstart/dtend value (date or datetime) to a UTC-aware
    datetime. All-day events (date) are treated as midnight UTC on that date.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # Floating datetime — assume UTC (iCloud typically uses explicit TZ)
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    else:
        # date → midnight UTC
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _format_dt(value: "date | datetime") -> str:
    """Format a date/datetime for JSONL output."""
    dt_utc = _to_utc_dt(value)
    return dt_utc.isoformat()


def _is_all_day(value: "date | datetime") -> bool:
    """Return True if the vObject value is a plain date (all-day event)."""
    return not isinstance(value, datetime)


def _parse_date_arg(s: str) -> datetime:
    """
    Parse a --from / --to argument (date or datetime string) to a UTC-aware
    datetime. Accepts: YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, ISO 8601 with offset.
    """
    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Try date-only
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# CalDAV helpers
# ---------------------------------------------------------------------------

def _get_client(apple_id: str, app_pwd: str):
    """Return an authenticated caldav.DAVClient."""
    try:
        import caldav  # noqa: PLC0415
    except ImportError:
        print("ERROR: caldav package not installed.\n"
              "Run: pip install caldav", file=sys.stderr)
        sys.exit(1)
    return caldav.DAVClient(
        url=CALDAV_URL,
        username=apple_id,
        password=app_pwd,
    )


def list_calendars(apple_id: str, app_pwd: str) -> list[dict]:
    """Return a list of calendar info dicts from the user's iCloud principal."""
    client = _get_client(apple_id, app_pwd)
    try:
        principal = client.principal()
        calendars  = principal.calendars()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: CalDAV error listing calendars: {exc}", file=sys.stderr)
        sys.exit(1)

    result: list[dict] = []
    for cal in calendars:
        result.append({
            "name":    _get_cal_name(cal),
            "url":     str(cal.url),
        })
    return result


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def _safe_get(component, name: str, default=None):
    """Safely get a vObject component attribute, returning default on error."""
    try:
        return getattr(component, name).value
    except AttributeError:
        return default


def _parse_organizer(organizer_value) -> str:
    """Convert a vObject organizer value to a human string."""
    if organizer_value is None:
        return ""
    raw = str(organizer_value)
    # vObject format: "mailto:user@example.com" or "CN=Name:mailto:email"
    email_match = re.search(r"mailto:([^\s]+)", raw, re.IGNORECASE)
    email_addr  = email_match.group(1) if email_match else ""
    cn_match    = re.search(r'CN=([^;:]+)', raw)
    name        = cn_match.group(1).strip('"').strip() if cn_match else ""
    if name and email_addr:
        return f"{name} <{email_addr}>"
    return email_addr or name or raw


def _parse_attendees(component) -> list[dict]:
    """Extract attendee list from a VEVENT component."""
    attendees: list[dict] = []
    try:
        raw_attendees = component.attendee_list
    except AttributeError:
        return []

    for att in raw_attendees:
        att_str = str(att.value) if hasattr(att, "value") else str(att)
        email_match = re.search(r"mailto:([^\s]+)", att_str, re.IGNORECASE)
        email_addr  = email_match.group(1) if email_match else ""
        cn_match    = re.search(r'CN=([^;:]+)', att_str)
        name        = cn_match.group(1).strip('"').strip() if cn_match else ""
        if email_addr or name:
            attendees.append({"email": email_addr, "name": name, "self": False})
    return attendees


def _has_online_meeting_url(description: str, location: str) -> tuple[bool, str]:
    """Detect a video-conferencing URL in event fields."""
    combined = f"{description} {location}".lower()
    url_patterns = [
        r"https://[\w.-]*zoom\.us/[^\s\"<>]+",
        r"https://meet\.google\.com/[^\s\"<>]+",
        r"https://teams\.microsoft\.com/[^\s\"<>]+",
        r"https://whereby\.com/[^\s\"<>]+",
        r"https://[\w.-]*webex\.com/[^\s\"<>]+",
    ]
    text = f"{description} {location}"
    for pattern in url_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return True, m.group(0)
    return False, ""


def _parse_event(event_obj, calendar_name: str) -> Optional[dict]:
    """Parse a caldav Event object into an Artha JSONL record."""
    try:
        vobj    = event_obj.vobject_instance
        vevent  = vobj.vevent
    except Exception as exc:  # noqa: BLE001
        print(f"[icloud_calendar_fetch] WARN: cannot parse event: {exc}",
              file=sys.stderr)
        return None

    summary     = _safe_get(vevent, "summary",     "") or ""
    dtstart_val = _safe_get(vevent, "dtstart")
    dtend_val   = _safe_get(vevent, "dtend")
    description = _safe_get(vevent, "description", "") or ""
    location    = _safe_get(vevent, "location",    "") or ""
    status_raw  = (_safe_get(vevent, "status",     "") or "confirmed").lower()
    uid         = _safe_get(vevent, "uid",         "") or ""
    url_val     = _safe_get(vevent, "url",         "") or ""

    if dtstart_val is None:
        return None

    # Skip cancelled events
    if "cancel" in status_raw:
        return None

    all_day = _is_all_day(dtstart_val)
    start_str = _format_dt(dtstart_val)
    end_str   = _format_dt(dtend_val) if dtend_val is not None else start_str

    organizer  = _parse_organizer(_safe_get(vevent, "organizer"))
    attendees  = _parse_attendees(vevent)

    # Recurring: RRULE present on the master, or RECURRENCE-ID on an instance
    is_recurring = hasattr(vevent, "rrule") or hasattr(vevent, "recurrence_id")

    # Visibility: CLASS field
    class_val   = (_safe_get(vevent, "class", "") or "").lower()
    visibility  = "private" if "private" in class_val else "normal"

    # Online meeting detection
    meet_url_str = url_val or ""
    is_online, meet_url = _has_online_meeting_url(description, location)
    if not is_online and meet_url_str:
        is_online = bool(re.search(r"zoom|meet\.google|teams\.microsoft|webex|whereby",
                                   meet_url_str, re.IGNORECASE))
        meet_url  = meet_url_str if is_online else ""

    # Truncate description (same as msg body; CalDAV events can carry large blobs)
    description = description[:2000].strip()

    return {
        "id":               uid,
        "calendar":         calendar_name,
        "summary":          summary,
        "start":            start_str,
        "end":              end_str,
        "all_day":          all_day,
        "location":         location.strip(),
        "description":      description,
        "organizer":        organizer,
        "attendees":        attendees,
        "status":           status_raw or "confirmed",
        "visibility":       visibility,
        "recurring":        is_recurring,
        "is_online_meeting": is_online,
        "online_meeting_url": meet_url,
        "source":           "icloud_calendar",
    }


def _parse_todo(todo_obj, calendar_name: str) -> Optional[dict]:
    """Parse a caldav Todo (VTODO) object into an Artha JSONL record."""
    try:
        vobj  = todo_obj.vobject_instance
        vtodo = vobj.vtodo
    except Exception as exc:  # noqa: BLE001
        print(f"[icloud_calendar_fetch] WARN: cannot parse reminder: {exc}",
              file=sys.stderr)
        return None

    summary       = _safe_get(vtodo, "summary",     "") or ""
    description   = _safe_get(vtodo, "description", "") or ""
    uid           = _safe_get(vtodo, "uid",         "") or ""
    due_val       = _safe_get(vtodo, "due")
    created_val   = _safe_get(vtodo, "created")
    modified_val  = _safe_get(vtodo, "last_modified")
    status_raw    = (_safe_get(vtodo, "status", "") or "NEEDS-ACTION").upper()
    priority_raw  = _safe_get(vtodo, "priority")
    completed_val = _safe_get(vtodo, "completed")

    # Status mapping (RFC 5545)
    status = {
        "NEEDS-ACTION": "pending",
        "COMPLETED":    "completed",
        "IN-PROCESS":   "in_process",
        "CANCELLED":    "cancelled",
    }.get(status_raw, "pending")

    # Priority mapping: 1-4=high, 5=medium, 6-9=low, 0=none (RFC 5545 §3.8.1.9)
    priority = "none"
    if priority_raw is not None:
        try:
            p = int(priority_raw)
            if 1 <= p <= 4:
                priority = "high"
            elif p == 5:
                priority = "medium"
            elif 6 <= p <= 9:
                priority = "low"
        except (ValueError, TypeError):
            pass

    due_str       = _format_dt(due_val)       if due_val       is not None else ""
    created_str   = _format_dt(created_val)   if created_val   is not None else ""
    modified_str  = _format_dt(modified_val)  if modified_val  is not None else ""
    completed_str = _format_dt(completed_val) if completed_val is not None else ""

    description = description[:2000].strip()

    return {
        "id":           uid,
        "calendar":     calendar_name,
        "title":        summary,
        "due":          due_str,
        "created":      created_str,
        "modified":     modified_str,
        "completed_at": completed_str,
        "description":  description,
        "priority":     priority,
        "status":       status,
        "recurring":    hasattr(vtodo, "rrule"),
        "source":       "icloud_reminder",
    }


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

def fetch_events(
    apple_id: str,
    app_pwd: str,
    start_dt: datetime,
    end_dt: datetime,
    *,
    calendar_filter: Optional[list[str]] = None,
) -> Iterator[dict]:
    """
    Yield event dicts (JSONL records) from iCloud Calendar between start_dt and end_dt.

    calendar_filter: optional list of calendar names or URL substrings to limit fetch.
    """
    client = _get_client(apple_id, app_pwd)
    try:
        principal = client.principal()
        all_cals  = principal.calendars()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: CalDAV principal/calendars error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Filter calendars if requested
    if calendar_filter:
        cf_lower = [f.lower() for f in calendar_filter]
        calendars = [
            c for c in all_cals
            if any(
                f in _get_cal_name(c).lower()
                or f in str(c.url).lower()
                for f in cf_lower
            )
        ]
        if not calendars:
            print(f"WARN: no calendars matched filter {calendar_filter!r}. "
                  "Use --list-calendars to see available names.", file=sys.stderr)
            return
    else:
        calendars = all_cals

    all_events: list[dict] = []

    for cal in calendars:
        cal_name = _get_cal_name(cal)
        try:
            events_raw = cal.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,   # expand recurring events into individual instances
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[icloud_calendar_fetch] WARN: error fetching from '{cal_name}': {exc}",
                  file=sys.stderr)
            continue

        for event_obj in events_raw:
            rec = _parse_event(event_obj, cal_name)
            if rec is not None:
                all_events.append(rec)

    # Sort by start time
    all_events.sort(key=lambda r: r.get("start", ""))

    for rec in all_events:
        yield rec


def fetch_reminders(
    apple_id: str,
    app_pwd: str,
    *,
    calendar_filter: Optional[list[str]] = None,
    include_completed: bool = False,
) -> Iterator[dict]:
    """
    Yield reminder dicts (JSONL records) from iCloud Reminders/VTODO calendars.

    calendar_filter: optional list of calendar names or URL substrings.
    include_completed: if True, also yield completed reminders.
    """
    client = _get_client(apple_id, app_pwd)
    try:
        principal = client.principal()
        all_cals  = principal.calendars()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: CalDAV principal/calendars error: {exc}", file=sys.stderr)
        sys.exit(1)

    if calendar_filter:
        cf_lower = [f.lower() for f in calendar_filter]
        calendars = [
            c for c in all_cals
            if any(
                f in _get_cal_name(c).lower() or f in str(c.url).lower()
                for f in cf_lower
            )
        ]
        if not calendars:
            print(f"WARN: no calendars matched filter {calendar_filter!r}.",
                  file=sys.stderr)
            return
    else:
        calendars = all_cals

    all_todos: list[dict] = []

    for cal in calendars:
        cal_name = _get_cal_name(cal)
        try:
            todos_raw = cal.search(todo=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[icloud_calendar_fetch] WARN: error fetching reminders from "
                  f"'{cal_name}': {exc}", file=sys.stderr)
            continue

        for todo_obj in todos_raw:
            rec = _parse_todo(todo_obj, cal_name)
            if rec is None:
                continue
            if not include_completed and rec["status"] == "completed":
                continue
            all_todos.append(rec)

    # Sort by due date (no due → sort last)
    all_todos.sort(key=lambda r: r.get("due") or "9999")

    for rec in all_todos:
        yield rec


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check(apple_id: str, app_pwd: str) -> None:
    """Print health status and exit 0/1."""
    print("iCloud Calendar health check")
    print("-" * 40)
    print(f"  CalDAV URL : {CALDAV_URL}")
    print(f"  Apple ID   : {apple_id}")

    try:
        cals = list_calendars(apple_id, app_pwd)
        print(f"  Calendars  : ✓ {len(cals)} found")
        for c in cals:
            print(f"               - {c['name']}")

        # Fetch today's events as a connectivity smoke test
        today_utc = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        tomorrow_utc = today_utc + timedelta(days=1)
        today_events = list(fetch_events(apple_id, app_pwd, today_utc, tomorrow_utc))
        print(f"  Today      : ✓ {len(today_events)} event(s)")

        # Check Reminders (VTODO) — count pending
        try:
            pending = list(fetch_reminders(apple_id, app_pwd, include_completed=False))
            print(f"  Reminders  : ✓ {len(pending)} pending reminder(s)")
        except Exception as exc:  # noqa: BLE001
            print(f"  Reminders  : ⚠ could not fetch ({exc})", file=sys.stderr)

        print(f"\niCloud Calendar: OK ({apple_id}, {len(cals)} calendars)")

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"  Check      : ✗ {exc}", file=sys.stderr)
        print("\niCloud Calendar: FAILED", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch iCloud Calendar events via CalDAV and output JSONL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--today-plus-days",
        type=int,
        metavar="N",
        help="Fetch events from today through today+N days (UTC midnight boundaries).",
    )
    group.add_argument(
        "--from",
        dest="from_date",
        metavar="DATE",
        help="Start of the event window (YYYY-MM-DD or ISO 8601 datetime).",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        metavar="DATE",
        help="End of the event window (YYYY-MM-DD or ISO 8601 datetime). "
             "Required with --from.",
    )
    parser.add_argument(
        "--calendars",
        metavar="NAMES",
        help="Comma-separated calendar names (or URL substrings) to fetch from. "
             "Default: all calendars.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Test CalDAV connectivity + auth, list calendars, fetch today. Exit 1 on failure.",
    )
    parser.add_argument(
        "--list-calendars",
        action="store_true",
        help="Print all available iCloud calendar names and URLs then exit.",
    )
    parser.add_argument(
        "--reminders",
        action="store_true",
        help="Fetch iCloud Reminders (VTODO) instead of calendar events. "
             "Outputs JSONL with source=\"icloud_reminder\".",
    )
    parser.add_argument(
        "--include-completed",
        action="store_true",
        help="When used with --reminders, also output completed reminders.",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Re-run iCloud credential setup interactively.",
    )
    args = parser.parse_args()

    # ── --reauth: delegate to setup_icloud_auth.py ─────────────────────────
    if args.reauth:
        setup_script = os.path.join(SCRIPTS_DIR, "setup_icloud_auth.py")
        os.execv(sys.executable, [sys.executable, setup_script, "--reauth"])

    # ── Load credentials ────────────────────────────────────────────────────
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        from setup_icloud_auth import ensure_valid_credentials
        apple_id, app_pwd = ensure_valid_credentials()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError:
        print("ERROR: setup_icloud_auth.py not found in scripts/", file=sys.stderr)
        sys.exit(1)

    # ── --health ─────────────────────────────────────────────────────────────
    if args.health:
        run_health_check(apple_id, app_pwd)
        return

    # ── --list-calendars ─────────────────────────────────────────────────────
    if args.list_calendars:
        cals = list_calendars(apple_id, app_pwd)
        print(f"{'Name':<35}  URL")
        print("-" * 80)
        for c in cals:
            name = c["name"][:34]
            print(f"{name:<35}  {c['url']}")
        return

    # ── --reminders ───────────────────────────────────────────────────────────
    if args.reminders:
        cal_filter: Optional[list[str]] = None
        if args.calendars:
            cal_filter = [c.strip() for c in args.calendars.split(",") if c.strip()]
        count = 0
        try:
            for rec in fetch_reminders(
                apple_id, app_pwd,
                calendar_filter=cal_filter,
                include_completed=args.include_completed,
            ):
                print(json.dumps(rec, ensure_ascii=False))
                count += 1
        except KeyboardInterrupt:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        status_label = "pending" if not args.include_completed else "total"
        print(f"[icloud_calendar_fetch] fetched {count} {status_label} reminder(s)",
              file=sys.stderr)
        return

    # ── Build date range ─────────────────────────────────────────────────────
    if args.today_plus_days is not None:
        start_dt = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = start_dt + timedelta(days=args.today_plus_days)
    elif args.from_date:
        if not args.to_date:
            parser.error("--from requires --to")
        start_dt = _parse_date_arg(args.from_date)
        end_dt   = _parse_date_arg(args.to_date)
        # If --to is date-only, extend to end of that day
        to_raw = args.to_date.strip()
        if len(to_raw) == 10:  # "YYYY-MM-DD"
            end_dt = end_dt + timedelta(days=1)
    else:
        parser.error("Provide --today-plus-days N or --from DATE --to DATE")

    # ── Calendar filter ───────────────────────────────────────────────────────
    cal_filter: Optional[list[str]] = None
    if args.calendars:
        cal_filter = [c.strip() for c in args.calendars.split(",") if c.strip()]

    # ── Fetch and output ──────────────────────────────────────────────────────
    count = 0
    try:
        for rec in fetch_events(apple_id, app_pwd, start_dt, end_dt,
                                calendar_filter=cal_filter):
            print(json.dumps(rec, ensure_ascii=False))
            count += 1
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[icloud_calendar_fetch] fetched {count} events "
          f"({start_dt.date()} → {end_dt.date()})", file=sys.stderr)


if __name__ == "__main__":
    main()
