"""
scripts/connectors/msgraph_calendar.py — Microsoft Graph calendar connector (standalone).

Fetches Outlook Calendar events via the MS Graph API and yields standardised
event dicts. All API logic is self-contained — no dependency on
msgraph_calendar_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_PAGE_SIZE = 100
_LOCAL_TZ = "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Helpers (moved from msgraph_calendar_fetch.py)
# ---------------------------------------------------------------------------

def _fmt_addr(ea: Optional[dict]) -> str:
    if not ea:
        return ""
    if "emailAddress" in ea:
        ea = ea["emailAddress"]
    name = ea.get("name", "")
    email = ea.get("address", "")
    if name and email and name.lower() != email.lower():
        return f"{name} <{email}>"
    return email or name


def _parse_date_arg(date_str: str, end_of_day: bool = False) -> str:
    """Convert YYYY-MM-DD or ISO datetime → UTC ISO 8601 with Z suffix."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo(_LOCAL_TZ)
    if len(date_str) == 10 and "T" not in date_str:
        if end_of_day:
            dt = datetime.fromisoformat(date_str + "T23:59:59").replace(tzinfo=tz)
        else:
            dt = datetime.fromisoformat(date_str + "T00:00:00").replace(tzinfo=tz)
    else:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_event(event: dict, calendar_name: str = "Calendar") -> dict:
    """Convert a raw MS Graph event object into the Artha canonical event dict."""
    summary = event.get("subject") or "(no title)"
    is_all_day = event.get("isAllDay", False)
    start_obj = event.get("start", {})
    end_obj = event.get("end", {})
    if is_all_day:
        start_dt = (start_obj.get("dateTime") or "")[:10]
        end_dt = (end_obj.get("dateTime") or "")[:10]
    else:
        start_dt = start_obj.get("dateTime", "")
        end_dt = end_obj.get("dateTime", "")
        for val in (start_dt, end_dt):
            pass
        # Normalise fractional seconds
        if start_dt and not start_dt.endswith("Z") and "+" not in start_dt[-6:]:
            start_dt = re.sub(r"\.\d+$", "", start_dt) + "Z"
        if end_dt and not end_dt.endswith("Z") and "+" not in end_dt[-6:]:
            end_dt = re.sub(r"\.\d+$", "", end_dt) + "Z"
    location_obj = event.get("location", {})
    location = location_obj.get("displayName", "") if isinstance(location_obj, dict) else ""
    description = (event.get("bodyPreview") or "")[:500]
    organizer = _fmt_addr(event.get("organizer"))
    attendees = [
        {
            "email": (att.get("emailAddress") or {}).get("address", ""),
            "name": (att.get("emailAddress") or {}).get("name", ""),
            "self": False,
        }
        for att in event.get("attendees", [])
    ]
    if event.get("isCancelled", False):
        status = "cancelled"
    elif (event.get("responseStatus") or {}).get("response") == "declined":
        status = "declined"
    else:
        status = "confirmed"
    sensitivity = event.get("sensitivity", "normal")
    visibility = "private" if sensitivity in ("private", "confidential", "personal") else "default"
    recurring = event.get("recurrence") is not None or bool(event.get("seriesMasterId"))
    is_online = event.get("isOnlineMeeting", False)
    online_url = event.get("onlineMeetingUrl", "") or ""
    return {
        "id": event.get("id", ""),
        "calendar": calendar_name,
        "summary": summary,
        "start": start_dt,
        "end": end_dt,
        "all_day": is_all_day,
        "location": location,
        "description": description,
        "organizer": organizer,
        "attendees": attendees,
        "status": status,
        "visibility": visibility,
        "recurring": recurring,
        "is_online_meeting": is_online,
        "online_meeting_url": online_url,
        "source": "outlook_calendar",
    }


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str,
    max_results: int = 250,
    auth_context: Dict[str, Any],
    source_tag: str = "outlook_calendar",
    window_days: int = 14,
    calendar_ids: Optional[list[str]] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Outlook Calendar events from *since* over a *window_days* window."""
    from lib.msgraph import _graph_get, _graph_get_full_url  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]

    access_token = auth_context.get("access_token", "")
    if not access_token:
        raise RuntimeError("[msgraph_calendar] auth_context missing access_token")

    start_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    end_dt = start_dt + timedelta(days=window_days)
    start_utc = _parse_date_arg(start_dt.strftime("%Y-%m-%dT%H:%M:%S%z"), end_of_day=False)
    end_utc = _parse_date_arg(end_dt.strftime("%Y-%m-%dT%H:%M:%S%z"), end_of_day=False)

    print(f"[msgraph_calendar] range={start_utc}→{end_utc}", file=sys.stderr)

    # Enumerate calendars if no IDs provided
    if not calendar_ids:
        try:
            cal_resp = with_retry(
                lambda: _graph_get(access_token, "/me/calendars",
                                   {"$select": "id,name,isDefaultCalendar", "$top": "50"},
                                   prefer_utc=True),
                context="calendars.list",
            )
            all_cals = cal_resp.get("value", [])
        except Exception as exc:
            print(f"[msgraph_calendar] WARN: could not enumerate calendars: {exc}", file=sys.stderr)
            all_cals = [{"id": "primary", "name": "Calendar"}]
    else:
        all_cals = [{"id": cid, "name": cid} for cid in calendar_ids]

    for cal in all_cals:
        cal_id = cal.get("id", "")
        cal_name = cal.get("name", cal_id)
        url = f"/me/calendars/{cal_id}/calendarView"
        params = {
            "startDateTime": start_utc,
            "endDateTime": end_utc,
            "$select": "id,subject,start,end,isAllDay,location,bodyPreview,organizer,attendees,isCancelled,responseStatus,sensitivity,recurrence,seriesMasterId,isOnlineMeeting,onlineMeetingUrl",
            "$top": str(min(_PAGE_SIZE, max_results)),
            "$orderby": "start/dateTime asc",
        }
        count = 0
        next_url: Optional[str] = None
        while count < max_results:
            try:
                if next_url:
                    resp = with_retry(
                        lambda u=next_url: _graph_get_full_url(access_token, u),
                        context=f"calendarView.page {cal_id[:8]}",
                    )
                else:
                    resp = with_retry(
                        lambda: _graph_get(access_token, url, params, prefer_utc=True),
                        context=f"calendarView {cal_id[:8]}",
                    )
            except Exception as exc:
                print(f"[msgraph_calendar] WARN: error fetching '{cal_name}': {exc}", file=sys.stderr)
                break
            for evt in resp.get("value", []):
                if count >= max_results:
                    break
                if evt.get("isCancelled", False):
                    continue
                record = _parse_event(evt, cal_name)
                if source_tag:
                    record["source"] = source_tag
                yield record
                count += 1
            next_url = resp.get("@odata.nextLink")
            if not next_url or not resp.get("value"):
                break


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify MS Graph auth and /me/calendars is reachable."""
    try:
        from lib.msgraph import _graph_get  # type: ignore[import]
        access_token = auth_context.get("access_token", "")
        if not access_token:
            return False
        _graph_get(access_token, "/me/calendars", {"$top": "1"})
        return True
    except Exception as exc:
        print(f"[msgraph_calendar] health_check failed: {exc}", file=sys.stderr)
        return False
