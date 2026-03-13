#!/usr/bin/env python3
"""
msgraph_calendar_fetch.py — Artha Microsoft Graph calendar fetch script
========================================================================
Fetches Outlook Calendar events in a given date range via the MS Graph API
and outputs JSONL to stdout. Designed to run in parallel with gcal_fetch.py
at catch-up Step 4 — the schemas are intentionally compatible.

Usage:
  python scripts/msgraph_calendar_fetch.py --from "2026-03-07" --to "2026-03-14"
  python scripts/msgraph_calendar_fetch.py --today-plus-days 7
  python scripts/msgraph_calendar_fetch.py --from "2026-03-07T00:00:00" --to "2026-03-14T23:59:59"
  python scripts/msgraph_calendar_fetch.py --calendars "id1,id2"   (subset of calendars)
  python scripts/msgraph_calendar_fetch.py --health                (token check + connectivity)
  python scripts/msgraph_calendar_fetch.py --list-calendars        (print all available calendar IDs/names)
  python scripts/msgraph_calendar_fetch.py --reauth                (force new OAuth flow)

Output (JSONL, one JSON object per event on stdout):
  {"id": "...", "calendar": "Calendar Name", "summary": "...",
   "start": "2026-03-07T15:30:00Z", "end": "2026-03-07T16:30:00Z",
   "all_day": false, "location": "...", "description": "...",
   "attendees": [{"email": "...", "name": "...", "self": false}],
   "status": "confirmed", "visibility": "normal", "recurring": false,
   "is_online_meeting": false, "source": "outlook_calendar"}

Schema is deliberately close to gcal_fetch.py output, with the additions:
  - "source": "outlook_calendar"    — identifies the feed
  - "is_online_meeting": bool       — true for Teams meetings
  - "organizer": "Name <email>"     — event organizer (Graph always provides this)

Deduplication note (for Artha.md Step 4):
  If an event's summary + start time (±5 min) matches an event from gcal_fetch.py,
  treat as the same event and keep one copy — mark source: "both".

All datetimes are returned in UTC (Prefer: outlook.timezone="UTC" header).

Errors → stderr. Exit codes: 0 = success, 1 = error, 2 = quota exhausted.

Ref: TS §3.8, T-1B.1.6
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
import time
from datetime import datetime, timezone, timedelta, date
from typing import Optional
from lib.retry import with_retry


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_BASE  = "https://graph.microsoft.com/v1.0"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# MS Graph calendarView query params
_PAGE_SIZE  = 100   # events per page (no body content → can go higher than email)



    """
    Execute fn() with exponential back-off on MS Graph 429 / 5xx responses.
    Respects the Retry-After header when present.
    """
    delay    = _BASE_DELAY
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            exc_str = str(exc).lower()
            is_retryable = (
                any(str(code) in exc_str for code in _RETRYABLE_STATUS_CODES)
                or "rate limit"           in exc_str
                or "quota"                in exc_str
                or "too many requests"    in exc_str
                or "throttl"              in exc_str
                or "service unavailable"  in exc_str
                or "gateway timeout"      in exc_str
            )

            if not is_retryable or attempt == retries:
                label = f" [{context}]" if context else ""
                raise type(exc)(
                    f"[msgraph_calendar_fetch]{label} failed after "
                    f"{attempt + 1} attempt(s): {exc}"
                ) from exc

            retry_after = None
            match = re.search(r"retry.after[^\d]*(\d+)", exc_str)
            if match:
                retry_after = int(match.group(1))

            wait = retry_after if retry_after else min(delay, _MAX_DELAY)
            print(
                f"[msgraph_calendar_fetch] ⚠ Throttled / server error "
                f"(attempt {attempt + 1}/{retries + 1}). "
                f"Retrying in {wait:.0f}s... ({context})",
                file=sys.stderr,
            )
            time.sleep(wait)
            delay    = min(delay * _BACKOFF_MULT, _MAX_DELAY)
            last_exc = exc

    raise last_exc  # type: ignore


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_headers(access_token: str) -> dict:
    """
    Standard request headers for all Graph calls in this script.
    The 'Prefer: outlook.timezone="UTC"' header normalises all event datetimes
    to UTC, avoiding Windows timezone name→IANA translation issues.
    """
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
        "Prefer":        'outlook.timezone="UTC"',
    }


def _graph_get(access_token: str, path: str, params: Optional[dict] = None) -> dict:
    """GET {GRAPH_BASE}{path} and return parsed JSON. Raises on HTTP errors."""
    try:
        import requests as req_lib
    except ImportError:
        print("[msgraph_calendar_fetch] ERROR: 'requests' not found. Run: pip install requests",
              file=sys.stderr)
        sys.exit(1)

    url      = f"{GRAPH_BASE}{path}"
    response = req_lib.get(url, headers=_get_headers(access_token), params=params, timeout=30)

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        raise Exception(f"429 Too Many Requests (Retry-After: {retry_after}s): {response.text[:200]}")
    if response.status_code >= 500:
        raise Exception(f"{response.status_code} Server Error: {response.text[:200]}")
    if response.status_code == 401:
        raise Exception("401 Unauthorized — token expired. Run: python scripts/setup_msgraph_oauth.py --reauth")
    if response.status_code == 403:
        raise Exception("403 Forbidden — Calendars.Read scope may be missing (run --reauth)")

    response.raise_for_status()
    return response.json()


def _graph_get_full_url(access_token: str, full_url: str) -> dict:
    """GET an absolute URL (used for @odata.nextLink pagination)."""
    try:
        import requests as req_lib
    except ImportError:
        print("[msgraph_calendar_fetch] ERROR: 'requests' not found.", file=sys.stderr)
        sys.exit(1)

    response = req_lib.get(full_url, headers=_get_headers(access_token), timeout=30)

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        raise Exception(f"429 Too Many Requests (Retry-After: {retry_after}s)")
    if response.status_code >= 500:
        raise Exception(f"{response.status_code} Server Error: {response.text[:200]}")

    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _get_valid_token() -> str:
    """Return a valid MS Graph access token. Exits on failure."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)

    try:
        from setup_msgraph_oauth import ensure_valid_token
    except ImportError:
        print(
            "[msgraph_calendar_fetch] ERROR: setup_msgraph_oauth.py not found.\n"
            "Run from ~/OneDrive/Artha/ or set PYTHONPATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        token_data   = ensure_valid_token()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("access_token field empty")
        return access_token
    except RuntimeError as exc:
        print(
            f"[msgraph_calendar_fetch] ERROR: Cannot obtain valid token: {exc}\n"
            "Run: python scripts/setup_msgraph_oauth.py",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Date / datetime helpers
# ---------------------------------------------------------------------------

_LOCAL_TZ_NAME = "America/Los_Angeles"


def _parse_date_arg(date_str: str, end_of_day: bool = False) -> str:
    """
    Accept 'YYYY-MM-DD' or ISO datetime string → UTC ISO 8601 with Z suffix.
    Used to build startDateTime / endDateTime params for the calendarView API.

    MS Graph requires UTC or offset for these params. We normalise to UTC using
    the Prefer header, so sending UTC params is cleanest.
    """
    import zoneinfo
    tz = zoneinfo.ZoneInfo(_LOCAL_TZ_NAME)

    if len(date_str) == 10 and "T" not in date_str:
        # Plain date
        if end_of_day:
            dt = datetime.fromisoformat(date_str + "T23:59:59").replace(tzinfo=tz)
        else:
            dt = datetime.fromisoformat(date_str + "T00:00:00").replace(tzinfo=tz)
    else:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)

    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    import zoneinfo
    return str(datetime.now(tz=zoneinfo.ZoneInfo(_LOCAL_TZ_NAME)).date())


# ---------------------------------------------------------------------------
# Address formatting (same pattern as msgraph_fetch.py)
# ---------------------------------------------------------------------------

def _fmt_addr(ea: Optional[dict]) -> str:
    if not ea:
        return ""
    if "emailAddress" in ea:
        ea = ea["emailAddress"]
    name  = ea.get("name", "")
    email = ea.get("address", "")
    if name and email and name.lower() != email.lower():
        return f"{name} <{email}>"
    return email or name


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def _parse_event(event: dict, calendar_name: str = "Calendar") -> dict:
    """
    Convert a raw MS Graph event object into the Artha canonical event dict.

    Schema mirrors gcal_fetch.py with additions for Outlook-specific fields.
    All datetimes are UTC (normalised via the Prefer header).
    """
    # --- Summary (subject) ---
    summary = event.get("subject") or "(no title)"

    # --- Start / End ---
    start_obj = event.get("start", {})
    end_obj   = event.get("end", {})

    is_all_day = event.get("isAllDay", False)
    if is_all_day:
        # All-day events have date-only values in the dateTime field when UTC-normalised
        start_dt = (event.get("start") or {}).get("dateTime", "")
        end_dt   = (event.get("end")   or {}).get("dateTime", "")
        # Strip the time part — keep it consistent with gcal all-day format
        start_dt = start_dt[:10] if start_dt else ""
        end_dt   = end_dt[:10]   if end_dt   else ""
    else:
        start_dt = start_obj.get("dateTime", "")
        end_dt   = end_obj.get("dateTime",   "")
        # Normalise "2026-03-07T15:30:00.0000000" → "2026-03-07T15:30:00Z"
        if start_dt and not start_dt.endswith("Z") and "+" not in start_dt[-6:]:
            start_dt = re.sub(r"\.\d+$", "", start_dt) + "Z"
        if end_dt and not end_dt.endswith("Z") and "+" not in end_dt[-6:]:
            end_dt = re.sub(r"\.\d+$", "", end_dt) + "Z"

    # --- Location ---
    location_obj = event.get("location", {})
    location     = location_obj.get("displayName", "") if isinstance(location_obj, dict) else ""

    # --- Description (bodyPreview is sufficient for catch-up; no huge HTML) ---
    description = (event.get("bodyPreview") or "")[:500]

    # --- Organizer ---
    organizer = _fmt_addr(event.get("organizer"))

    # --- Attendees ---
    raw_attendees = event.get("attendees", [])
    attendees: list[dict] = []
    for att in raw_attendees:
        email_addr = att.get("emailAddress", {})
        name  = email_addr.get("name", "")
        email = email_addr.get("address", "")
        # "self" flag: did this attendee add themselves (i.e. is it the account owner)?
        # Graph doesn't provide an explicit self flag — we set it if email matches organizer
        is_self = False  # can be enriched downstream with profile email
        attendees.append({"email": email, "name": name, "self": is_self})

    # --- Status ---
    # Graph uses showAs for the block type; responseStatus for acceptance
    response_status = (event.get("responseStatus") or {}).get("response", "")
    if event.get("isCancelled", False):
        status = "cancelled"
    elif response_status in ("declined",):
        status = "declined"
    else:
        status = "confirmed"

    # --- Visibility ---
    sensitivity = event.get("sensitivity", "normal")
    vis_map  = {"private": "private", "confidential": "private", "personal": "private"}
    visibility = vis_map.get(sensitivity, "default")

    # --- Recurrence ---
    recurring = event.get("recurrence") is not None or bool(event.get("seriesMasterId"))

    # --- Teams / online meeting ---
    is_online = event.get("isOnlineMeeting", False)
    online_url = event.get("onlineMeetingUrl", "") or ""

    return {
        "id":               event.get("id", ""),
        "calendar":         calendar_name,
        "summary":          summary,
        "start":            start_dt,
        "end":              end_dt,
        "all_day":          is_all_day,
        "location":         location,
        "description":      description,
        "organizer":        organizer,
        "attendees":        attendees,
        "status":           status,
        "visibility":       visibility,
        "recurring":        recurring,
        "is_online_meeting": is_online,
        "online_meeting_url": online_url,
        "source":           "outlook_calendar",
    }


# ---------------------------------------------------------------------------
# Calendar enumeration
# ---------------------------------------------------------------------------

def list_calendars(access_token: str) -> list[dict]:
    """
    Return all calendars visible to the authenticated user.
    Each entry: {id, name, is_default, can_edit, color, owner_email}
    """
    response = with_retry(
        lambda: _graph_get(access_token, "/me/calendars",
                           params={"$select": "id,name,isDefaultCalendar,canEdit,color,owner",
                                   "$top": 50}),
        context="calendars.list",
    )
    calendars = []
    for cal in response.get("value", []):
        owner = cal.get("owner", {})
        calendars.append({
            "id":           cal.get("id", ""),
            "name":         cal.get("name", "Calendar"),
            "is_default":   cal.get("isDefaultCalendar", False),
            "can_edit":     cal.get("canEdit", False),
            "color":        cal.get("color", ""),
            "owner_email":  owner.get("address", ""),
        })
    return calendars


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

def fetch_events(
    from_str: str,
    to_str:   str,
    calendar_ids: Optional[list[str]] = None,
    max_per_calendar: int = 250,
) -> list[dict]:
    """
    Fetch Outlook Calendar events in the given date range.

    Args:
        from_str:           start date/datetime (ISO 8601 or YYYY-MM-DD)
        to_str:             end date/datetime
        calendar_ids:       optional list of calendar IDs to restrict to;
                            default is ALL calendars returned by /me/calendars
        max_per_calendar:   maximum events per calendar (default 250)

    Returns:
        List of parsed event dicts sorted by start time.

    Cancelled events are excluded. If Graph returns 429 after all retries,
    exits with code 2 (quota exhausted — mirrors gcal_fetch.py behaviour).
    """
    access_token = _get_valid_token()

    # Build UTC datetime params for calendarView
    start_utc = _parse_date_arg(from_str, end_of_day=False)
    end_utc   = _parse_date_arg(to_str,   end_of_day=True)

    print(f"[msgraph_calendar_fetch] Range: {start_utc} → {end_utc}", file=sys.stderr)

    # Enumerate calendars (unless specific IDs were requested)
    if calendar_ids:
        # Build minimal calendar metadata for the requested IDs
        all_cals = [{"id": cid, "name": cid, "is_default": False} for cid in calendar_ids]
        # Try to resolve names
        try:
            known = list_calendars(access_token)
            name_map = {c["id"]: c["name"] for c in known}
            for cal in all_cals:
                cal["name"] = name_map.get(cal["id"], cal["id"])
        except Exception:
            pass
    else:
        print("[msgraph_calendar_fetch] Enumerating calendars...", file=sys.stderr)
        try:
            all_cals = with_retry(
                lambda: list_calendars(access_token),
                context="calendars.enumerate",
            )
        except Exception as exc:
            print(f"[msgraph_calendar_fetch] ERROR enumerating calendars: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"[msgraph_calendar_fetch] {len(all_cals)} calendar(s) to fetch:", file=sys.stderr)
    for cal in all_cals:
        marker = " ← default" if cal.get("is_default") else ""
        print(f"  • {cal['name']}{marker} ({cal['id'][:16]}...)", file=sys.stderr)

    all_events: list[dict] = []

    for cal in all_cals:
        cal_id   = cal["id"]
        cal_name = cal["name"]

        print(f"[msgraph_calendar_fetch] Fetching '{cal_name}'...", file=sys.stderr)

        url    = f"/me/calendars/{cal_id}/calendarView"
        params: dict = {
            "startDateTime": start_utc,
            "endDateTime":   end_utc,
            "$select":       ",".join([
                "id", "subject", "start", "end", "isAllDay",
                "location", "bodyPreview", "organizer", "attendees",
                "isCancelled", "responseStatus", "sensitivity",
                "showAs", "recurrence", "seriesMasterId",
                "isOnlineMeeting", "onlineMeetingUrl",
            ]),
            "$top":          min(_PAGE_SIZE, max_per_calendar),
            "$orderby":      "start/dateTime asc",
        }

        cal_events: list[dict] = []
        next_url: Optional[str] = None

        while True:
            if len(cal_events) >= max_per_calendar:
                break

            try:
                if next_url:
                    response = with_retry(
                        lambda u=next_url: _graph_get_full_url(access_token, u),
                        context=f"{cal_name} (page {len(cal_events) // _PAGE_SIZE + 1})",
                    )
                else:
                    response = with_retry(
                        lambda: _graph_get(access_token, url, params),
                        context=f"{cal_name} page 1",
                    )
            except Exception as exc:
                exc_str = str(exc).lower()
                if "quota" in exc_str or "throttl" in exc_str or "too many requests" in exc_str:
                    print(
                        f"[msgraph_calendar_fetch] ⛔ Quota exhausted for '{cal_name}': {exc}",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                print(
                    f"[msgraph_calendar_fetch] Warning: failed to fetch '{cal_name}': {exc}",
                    file=sys.stderr,
                )
                break

            page_items = response.get("value", [])
            remaining  = max_per_calendar - len(cal_events)
            cal_events.extend(page_items[:remaining])

            next_url = response.get("@odata.nextLink")
            if not next_url or not page_items:
                break

        # Parse and filter
        added = 0
        for evt in cal_events:
            if evt.get("isCancelled", False):
                continue   # skip cancelled instances
            parsed = _parse_event(evt, cal_name)
            all_events.append(parsed)
            added += 1

        print(f"[msgraph_calendar_fetch]   → {added} events from '{cal_name}'", file=sys.stderr)

    # Sort across all calendars by start time
    def _sort_key(evt: dict) -> str:
        return evt.get("start", "9999")

    all_events.sort(key=_sort_key)
    print(
        f"[msgraph_calendar_fetch] Total: {len(all_events)} events across "
        f"{len(all_cals)} calendar(s).",
        file=sys.stderr,
    )
    return all_events


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check() -> None:
    """Verify token and connectivity to the calendar API. Exits 0/1."""
    print("Outlook Calendar (MS Graph) Health Check")
    print("─" * 42)

    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    try:
        from setup_msgraph_oauth import ensure_valid_token, TOKEN_FILE
    except ImportError:
        print("  ✗ setup_msgraph_oauth.py not found — run from ~/OneDrive/Artha/")
        sys.exit(1)

    if not os.path.exists(TOKEN_FILE):
        print(f"  ✗ Token missing: {TOKEN_FILE}")
        print("  Action: python scripts/setup_msgraph_oauth.py")
        sys.exit(1)
    print(f"  Token file:    ✓ {TOKEN_FILE}")

    try:
        token_data   = ensure_valid_token()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("empty access_token")
        print("  Token:         ✓ valid (auto-refreshed if needed)")
    except RuntimeError as exc:
        print(f"  ✗ Token error: {exc}")
        sys.exit(1)

    # Identity
    try:
        profile  = with_retry(lambda: _graph_get(access_token, "/me"), context="/me")
        email    = profile.get("mail") or profile.get("userPrincipalName", "")
        print(f"  Identity:      ✓ {profile.get('displayName', '?')} <{email}>")
    except Exception as exc:
        print(f"  ✗ /me failed: {exc}")
        sys.exit(1)

    # Calendar enumeration
    try:
        cals = with_retry(lambda: list_calendars(access_token), context="calendars.list")
        print(f"  Calendars:     ✓ {len(cals)} visible")
        for cal in cals[:6]:
            marker = " ← default" if cal.get("is_default") else ""
            print(f"    • {cal['name']}{marker}")
        if len(cals) > 6:
            print(f"    ... and {len(cals) - 6} more")
    except Exception as exc:
        print(f"  ✗ Calendar list failed: {exc}")
        sys.exit(1)

    # Quick event fetch — today only as sanity check
    try:
        import zoneinfo
        today = str(datetime.now(tz=zoneinfo.ZoneInfo(_LOCAL_TZ_NAME)).date())
        events = fetch_events(today, today, max_per_calendar=5)
        print(f"  Today's events: ✓ {len(events)} fetched (sample OK)")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"  ✗ Event fetch failed: {exc}")
        sys.exit(1)

    print("\nOutlook Calendar: OK")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

_LOCAL_TZ_NAME = "America/Los_Angeles"  # redeclare after module-level use in helpers


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Outlook Calendar events via MS Graph for a date range. "
            "Output: JSONL to stdout (schema-compatible with gcal_fetch.py)."
        )
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
        "--today-plus-days", type=int, default=None, dest="today_plus_days",
        help="Shorthand: fetch from today to today+N days (overrides --from/--to)",
    )
    parser.add_argument(
        "--calendars", type=str, default=None,
        help=(
            "Comma-separated Outlook calendar IDs to restrict to "
            "(default: all calendars). Use --list-calendars to discover IDs."
        ),
    )
    parser.add_argument(
        "--max", type=int, default=250, dest="max_results",
        help="Max events per calendar (default: 250)",
    )
    parser.add_argument(
        "--health", action="store_true",
        help="Check token and connectivity only (no events fetched)",
    )
    parser.add_argument(
        "--list-calendars", action="store_true", dest="list_calendars",
        help="Print all available Outlook calendar IDs and names, then exit",
    )
    parser.add_argument(
        "--reauth", action="store_true",
        help="Force a new interactive OAuth flow",
    )

    args = parser.parse_args()

    if args.health:
        run_health_check()
        return

    if args.reauth:
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "setup_msgraph_oauth.py"), "--reauth"],
            cwd=os.path.dirname(SCRIPTS_DIR),
        )
        sys.exit(result.returncode)

    if args.list_calendars:
        access_token = _get_valid_token()
        try:
            cals = with_retry(lambda: list_calendars(access_token), context="list-calendars")
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"{'Name':<35} {'Default':<8} {'ID'}")
        print("─" * 90)
        for cal in cals:
            default = "✓" if cal["is_default"] else ""
            print(f"{cal['name']:<35} {default:<8} {cal['id']}")
        return

    # Handle --today-plus-days shorthand
    if args.today_plus_days is not None:
        from_date = _today_str()
        to_date   = str((datetime.now(timezone.utc).date() +
                        timedelta(days=args.today_plus_days)))
    else:
        from_date = args.from_date
        to_date   = args.to_date

    if not from_date or not to_date:
        print(
            "ERROR: Provide --from and --to dates, or use --today-plus-days.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse calendar IDs if provided
    calendar_ids: Optional[list[str]] = None
    if args.calendars:
        calendar_ids = [c.strip() for c in args.calendars.split(",") if c.strip()]

    events = fetch_events(
        from_str=from_date,
        to_str=to_date,
        calendar_ids=calendar_ids,
        max_per_calendar=args.max_results,
    )

    for evt in events:
        print(json.dumps(evt, ensure_ascii=False))


if __name__ == "__main__":
    main()
