"""
scripts/connectors/google_calendar.py — Google Calendar connector (standalone).

Fetches calendar events via the Google Calendar API and yields standardised
event dicts. All API logic is self-contained — no dependency on gcal_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Event parsing helpers (moved from gcal_fetch.py)
# ---------------------------------------------------------------------------

def _parse_event(event: dict, calendar_name: str = "primary") -> dict:
    """Convert a Calendar API event object into a clean dict."""
    start_raw = event.get("start", {})
    end_raw = event.get("end", {})
    start_dt = start_raw.get("dateTime") or start_raw.get("date", "")
    end_dt = end_raw.get("dateTime") or end_raw.get("date", "")
    all_day = "dateTime" not in start_raw
    attendees = [
        # Redact email addresses: attendee presence and name are useful for briefing
        # context; raw email addresses add no briefing value and constitute PII
        # that should not appear in terminal output or pipeline JSONL logs.
        {"name": a.get("displayName", "") or a.get("email", "").split("@")[0], "self": a.get("self", False)}
        for a in event.get("attendees", [])
    ]
    return {
        "id": event.get("id", ""),
        "calendar": calendar_name,
        "summary": event.get("summary", "(no title)"),
        "start": start_dt,
        "end": end_dt,
        "all_day": all_day,
        "location": event.get("location", ""),
        "description": (event.get("description") or "")[:500],
        "attendees": attendees,
        "status": event.get("status", "confirmed"),
        "visibility": event.get("visibility", "default"),
        "recurring": bool(event.get("recurringEventId")),
    }


def _to_rfc3339(date_str: str, end_of_day: bool = False) -> str:
    """Convert a date or datetime string to RFC 3339."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
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


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

#: Upper bound on pages followed per calendar; beyond it the scope is reported
#: ``truncated`` (never silently dropped).  re-artha §7.10.
MAX_PAGES = 20

def fetch(
    *,
    since: str,
    max_results: int = 250,
    auth_context: Dict[str, Any],
    source_tag: str = "google_calendar",
    window_days: int = 14,
    calendar_ids: Optional[list[str]] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Google Calendar events from *since* over a *window_days* window."""
    from google_auth import build_service  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]
    from lib.connector_coverage import report_scope  # type: ignore[import]

    start_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    # The window must reach into the future even after a long gap between
    # Catch-ups (since + window_days alone would end in the past).
    end_dt = max(start_dt, datetime.now(timezone.utc)) + timedelta(days=window_days)
    time_min = _to_rfc3339(start_dt.strftime("%Y-%m-%dT%H:%M:%S%z"), end_of_day=False)
    time_max = _to_rfc3339(end_dt.strftime("%Y-%m-%dT%H:%M:%S%z"), end_of_day=False)

    if calendar_ids is None:
        try:
            from profile_loader import get as _pget  # type: ignore[import]
            _ids = _pget("integrations.google_calendar.calendar_ids")
            raw_items = list(_ids.values()) if isinstance(_ids, dict) else (_ids if isinstance(_ids, list) else [_ids])
            clean_ids: list[str] = []
            for item in raw_items:
                if isinstance(item, str) and item.strip():
                    clean_ids.append(item.strip())
                elif isinstance(item, list):
                    clean_ids.extend(str(sub).strip() for sub in item if isinstance(sub, str) and sub.strip())
            calendar_ids = clean_ids or ["primary"]
        except Exception:
            calendar_ids = ["primary"]

    print(f"[google_calendar] range={time_min}→{time_max} calendars={calendar_ids}", file=sys.stderr)
    service = build_service("calendar", "v3")
    all_events: list[dict] = []

    for cal_id in calendar_ids:
        try:
            cal_meta = with_retry(
                lambda c=cal_id: service.calendars().get(calendarId=c).execute(),
                context=f"calendars.get({cal_id})",
            )
            cal_name = cal_meta.get("summary", cal_id)
        except Exception:
            cal_name = cal_id

        scope = f"calendar:{cal_id}"
        fetched = 0
        page_token: Optional[str] = None
        pages = 0
        failed = False
        truncated = False
        while True:
            try:
                response = with_retry(
                    lambda c=cal_id, t=page_token: service.events().list(
                        calendarId=c,
                        timeMin=time_min,
                        timeMax=time_max,
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                        pageToken=t,
                    ).execute(),
                    context=f"events.list({cal_id})",
                )
            except Exception as exc:
                print(f"[google_calendar] WARN: failed to fetch '{cal_id}': {exc}", file=sys.stderr)
                report_scope(scope, "failed", records=fetched, reason=str(exc))
                failed = True
                break

            pages += 1
            for evt in response.get("items", []):
                if evt.get("status") == "cancelled":
                    continue
                record = _parse_event(evt, cal_name)
                if source_tag:
                    record["source"] = source_tag
                all_events.append(record)
                fetched += 1

            page_token = response.get("nextPageToken")
            if not page_token:
                break
            if pages >= MAX_PAGES:
                truncated = True
                break

        if truncated:
            report_scope(scope, "truncated", records=fetched, reason=f"page cap {MAX_PAGES} reached")
        elif not failed:
            report_scope(scope, "complete", records=fetched)

    all_events.sort(key=lambda e: e.get("start", "9999"))
    yield from all_events


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify Google Calendar OAuth credentials are available and valid."""
    try:
        from google_auth import check_stored_credentials  # type: ignore[import]
        status = check_stored_credentials()
        return status.get("client_id_stored", False) and status.get("gcal_token_stored", False)
    except Exception as exc:
        print(f"[google_calendar] health_check failed: {exc}", file=sys.stderr)
        return False
