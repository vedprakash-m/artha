"""
scripts/connectors/msgraph_calendar.py — Microsoft Graph calendar connector (standalone).

Fetches Outlook Calendar events via the MS Graph API and yields standardised
event dicts. All API logic is self-contained — no dependency on
msgraph_calendar_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urlsplit

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
        # Normalise fractional seconds
        if start_dt and not re.search(r"(Z|[+-]\d{2}:\d{2})$", start_dt):
            start_dt = re.sub(r"\.\d+$", "", start_dt) + "Z"
        if end_dt and not re.search(r"(Z|[+-]\d{2}:\d{2})$", end_dt):
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

#: The runtime hands a committed per-calendar cursor to ``fetch(sync_tokens=...)`` for these sources.
SYNC_TOKEN_SCOPE = "configured_calendars"
_GRAPH_HOST = "graph.microsoft.com"
MAX_PAGES = 20


def _http_status(exc: BaseException) -> int | None:
    """Status from a ``requests`` error, else from a leading three-digit code in the helper's message."""
    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            break
        status = getattr(getattr(current, "response", None), "status_code", None)
        if type(status) is int:
            return status
        current = current.__cause__ or current.__context__
    match = re.match(r"\s*(\d{3})\b", str(exc))
    return int(match.group(1)) if match else None


def _graph_link(value: Any) -> bool:
    """A provider link is followed (with a bearer token) only when it is https on the Graph API host."""
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and parts.hostname == _GRAPH_HOST and not parts.username and parts.port is None


def _load_cursor(raw: str, now: datetime) -> tuple[str | None, str | None]:
    """(link, through) for a usable cursor, (None, None) for an expired one; ValueError when unusable."""
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"v", "link", "through"} or data["v"] != 1
            or not _graph_link(data["link"]) or not isinstance(data["through"], str)):
        raise ValueError("calendar delta cursor is invalid")
    through = datetime.fromisoformat(data["through"].replace("Z", "+00:00"))
    if through.tzinfo is None:
        raise ValueError("calendar delta cursor is invalid")
    return (data["link"], data["through"]) if through > now else (None, None)


def _sync_calendar(access_token: str, cal: dict, *, start_utc: str, end_utc: str, token: Optional[str],
                   max_results: int, source_tag: str) -> Iterator[Dict[str, Any]]:
    """One calendar through ``calendarView/delta`` (.archive/specs/provider-observation-cursors-2026-11-05.md)."""
    from lib.connector_coverage import (  # type: ignore[import]  # noqa: PLC0415
        report_cursor,
        report_cursor_reset,
        report_delta,
        report_full_listing,
        report_scope,
    )
    from lib.msgraph import _graph_get, _graph_get_full_url  # type: ignore[import]  # noqa: PLC0415
    from lib.retry import with_retry  # type: ignore[import]  # noqa: PLC0415

    cal_id = cal["id"]
    scope = f"calendar:{cal_id}"
    link: Optional[str] = None
    through = end_utc
    if token is not None:
        try:
            link, kept = _load_cursor(token, datetime.now(timezone.utc))
        except (ValueError, TypeError):
            report_cursor_reset()
            report_scope(scope, "failed", reason="stored calendar cursor is unusable")
            return
        through = kept or end_utc
    full = link is None
    if not full:
        report_delta()
    params = {"startDateTime": start_utc, "endDateTime": end_utc}
    count = pages = 0
    seen: set[str] = set()
    delta_link: Any = None
    while True:
        try:
            if link is not None:
                resp = with_retry(lambda u=link: _graph_get_full_url(access_token, u), context="calendarView.delta.page")
            else:
                resp = with_retry(lambda: _graph_get(access_token, f"/me/calendars/{cal_id}/calendarView/delta",
                                                     params, prefer_utc=True), context="calendarView.delta")
            values = resp.get("value")
            if not isinstance(values, list):
                raise ValueError("calendar events page must contain a list")
        except Exception as exc:
            if _http_status(exc) == 410:
                report_cursor_reset()
            print("[msgraph_calendar] WARN: calendar delta request failed", file=sys.stderr)
            report_scope(scope, "failed", records=count, reason=f"provider request failed ({_http_status(exc)})"
                         if _http_status(exc) is not None else "provider request failed")
            return
        pages += 1
        for evt in values:
            if count >= max_results:
                report_scope(scope, "truncated", records=count, reason=f"event limit {max_results} reached")
                return
            try:
                if not isinstance(evt, dict) or not isinstance(evt.get("id"), str) or not evt["id"]:
                    raise ValueError("calendar event identity is invalid")
                gone = evt.get("isCancelled") is True or evt.get("@removed") is not None
                record = {"id": evt["id"], "status": "cancelled"} if gone else _parse_event(evt, cal["name"])
            except (ValueError, KeyError, TypeError, AttributeError):
                report_scope(scope, "failed", records=count, reason="calendar event is malformed")
                return
            record["calendar_id"] = cal_id
            if source_tag:
                record["source"] = source_tag
            count += 1
            yield record
        link, delta_link = resp.get("@odata.nextLink"), resp.get("@odata.deltaLink")
        if not link:
            break
        if not _graph_link(link) or link in seen or pages >= MAX_PAGES:
            report_scope(scope, "truncated", records=count, reason="event pagination is incomplete")
            return
        seen.add(link)
    if not _graph_link(delta_link):
        report_scope(scope, "failed", records=count, reason="complete sync cursor unavailable")
        return
    report_scope(scope, "complete", records=count)
    report_cursor(json.dumps({"v": 1, "link": delta_link, "through": through}, sort_keys=True,
                             separators=(",", ":")), collection_id=cal_id)
    if full:
        report_full_listing(cal_id, window_start=start_utc, window_end=end_utc)


def fetch(
    *,
    since: str,
    max_results: int = 250,
    auth_context: Dict[str, Any],
    source_tag: str = "outlook_calendar",
    window_days: int = 14,
    calendar_ids: Optional[list[str]] = None,
    sync_changes: bool = False,
    sync_tokens: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Outlook Calendar events from *since* over a *window_days* window."""
    from lib.msgraph import _graph_get, _graph_get_full_url  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]
    from lib.connector_coverage import (  # type: ignore[import]
        report_scope,
    )

    if type(max_results) is not int or max_results <= 0:
        raise ValueError('max_results must be a positive integer')
    if type(window_days) is not int or window_days <= 0:
        raise ValueError('window_days must be a positive integer')
    if calendar_ids is not None and (not isinstance(calendar_ids, list) or not calendar_ids
            or any(not isinstance(item, str) or not item.strip() for item in calendar_ids)
            or len(set(calendar_ids)) != len(calendar_ids)):
        raise ValueError('calendar_ids must contain distinct nonempty calendar identifiers')
    if type(sync_changes) is not bool:
        raise ValueError('sync_changes must be a boolean')
    if sync_changes and calendar_ids is None:
        raise ValueError('sync_changes requires explicit calendar_ids')
    if sync_tokens is not None and (not sync_changes or not isinstance(sync_tokens, dict)
            or any(key not in (calendar_ids or []) or not isinstance(value, str) or not value.strip()
                   for key, value in sync_tokens.items())):
        raise ValueError('sync_tokens must contain native tokens for the explicit sync scope')
    access_token = auth_context.get("access_token", "")
    if not access_token:
        raise RuntimeError("[msgraph_calendar] auth_context missing access_token")

    start_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    # Window must reach into the future even after a long gap (see google_calendar).
    end_dt = max(start_dt, datetime.now(timezone.utc)) + timedelta(days=window_days)
    start_utc = _parse_date_arg(start_dt.strftime("%Y-%m-%dT%H:%M:%S%z"), end_of_day=False)
    end_utc = _parse_date_arg(end_dt.strftime("%Y-%m-%dT%H:%M:%S%z"), end_of_day=False)

    print(f"[msgraph_calendar] range={start_utc}→{end_utc}", file=sys.stderr)

    # Enumerate calendars if no IDs provided
    if not calendar_ids:
        all_cals = []
        seen_pages: set[str] = set()
        seen_calendars: set[str] = set()
        try:
            cal_resp = with_retry(
                lambda: _graph_get(access_token, "/me/calendars",
                                   {"$select": "id,name,isDefaultCalendar", "$top": "50"},
                                   prefer_utc=True),
                context="calendars.list",
            )
            while True:
                page = cal_resp.get('value')
                if not isinstance(page, list):
                    raise ValueError('calendar discovery returned an invalid page')
                for entry in page:
                    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']:
                        raise ValueError('calendar discovery returned an invalid identity')
                    if entry['id'] not in seen_calendars:
                        all_cals.append(entry)
                        seen_calendars.add(entry['id'])
                link = cal_resp.get('@odata.nextLink')
                if not link:
                    report_scope('calendars:list', 'complete', records=len(all_cals))
                    break
                if not isinstance(link, str) or link in seen_pages or len(seen_pages) >= 20:
                    report_scope('calendars:list', 'truncated', records=len(all_cals),
                                 reason='calendar discovery pagination is incomplete')
                    break
                seen_pages.add(link)
                cal_resp = with_retry(lambda u=link: _graph_get_full_url(access_token, u),
                                      context='calendars.list.page')
        except Exception as exc:
            print("[msgraph_calendar] WARN: could not enumerate calendars", file=sys.stderr)
            report_scope("calendars:list", "failed", records=len(all_cals), reason=type(exc).__name__)
            if not all_cals:
                all_cals = [{"id": "primary", "name": "Calendar"}]
    else:
        all_cals = [{"id": cid, "name": cid} for cid in calendar_ids]

    for cal in all_cals:
        cal_id = cal.get("id", "")
        cal_name = cal.get("name", cal_id)
        if sync_changes:
            yield from _sync_calendar(access_token, {"id": cal_id, "name": cal_name}, start_utc=start_utc,
                                      end_utc=end_utc, token=(sync_tokens or {}).get(cal_id),
                                      max_results=max_results, source_tag=source_tag)
            continue
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
        scope = f"calendar:{cal_id}"
        scope_failed = False
        scope_truncated = False
        seen_event_pages: set[str] = set()
        pages = 0
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
                values = resp.get('value')
                if not isinstance(values, list):
                    raise ValueError('calendar events page must contain a list')
            except Exception as exc:
                print("[msgraph_calendar] WARN: calendar request failed", file=sys.stderr)
                report_scope(scope, "failed", records=count, reason='provider request failed')
                scope_failed = True
                break
            pages += 1
            scope_truncated = len(values) > max_results - count
            for evt in values:
                if count >= max_results:
                    break
                try:
                    if not isinstance(evt, dict) or not isinstance(evt.get('id'), str) or not evt['id']:
                        raise ValueError('calendar event identity is invalid')
                    record = ({'id': evt['id'], 'status': 'cancelled'}
                              if evt.get('isCancelled') is True or evt.get('@removed') is not None
                              else _parse_event(evt, cal_name))
                except (ValueError, KeyError, TypeError, AttributeError):
                    if not scope_failed:
                        report_scope(scope, 'failed', records=count, reason='calendar event is malformed')
                    scope_failed = True
                    continue
                record["calendar_id"] = cal_id
                if source_tag:
                    record["source"] = source_tag
                yield record
                count += 1
            next_url = resp.get("@odata.nextLink")
            if not next_url:
                break
            if not isinstance(next_url, str) or next_url in seen_event_pages or pages >= 20:
                scope_truncated = True
                break
            seen_event_pages.add(next_url)
        if not scope_failed:
            if scope_truncated or next_url:
                report_scope(scope, "truncated", records=count, reason='event pagination is incomplete')
            else:
                report_scope(scope, "complete", records=count)


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify MS Graph auth and /me/calendars is reachable."""
    try:
        from lib.msgraph import _graph_get  # type: ignore[import]
        access_token = auth_context.get("access_token", "")
        if not access_token:
            return False
        _graph_get(access_token, "/me/calendars", {"$top": "1"})
        return True
    except Exception:
        print("[msgraph_calendar] health_check failed", file=sys.stderr)
        return False
