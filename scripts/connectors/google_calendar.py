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
        "description": event.get("description") or "",
        "version": event.get("etag") or event.get("updated"),
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


def _http_status(exc: Exception) -> int | None:
    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            break
        status = getattr(getattr(current, 'resp', None), 'status', None)
        if type(status) is int:
            return status
        current = current.__cause__ or current.__context__
    return None


def _failure_reason(exc: Exception) -> str:
    status = _http_status(exc)
    return f'provider request failed ({status})' if status is not None else 'provider request failed'


def _provider_deleted_calendars(service: Any, calendar_ids: list[str], with_retry: Any) -> set[str]:
    """Configured calendars the provider lists with ``deleted: true``; failure degrades coverage, never deletes."""
    from lib.connector_coverage import report_scope  # type: ignore[import]  # noqa: PLC0415

    found: set[str] = set()
    page_token: Optional[str] = None
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        try:
            response = with_retry(
                lambda t=page_token: service.calendarList().list(
                    showDeleted=True, maxResults=250, pageToken=t).execute(),
                context="calendarList.list",
            )
            items = response.get("items", [])
            if not isinstance(items, list):
                raise ValueError("calendar list page must contain a list")
        except Exception as exc:
            report_scope("calendars:list", "failed", reason=_failure_reason(exc))
            return found
        found.update(i["id"] for i in items if isinstance(i, dict) and i.get("deleted") is True
                     and i.get("id") in calendar_ids)
        page_token = response.get("nextPageToken")
        if not page_token:
            return found
        if not isinstance(page_token, str) or page_token in seen:
            break
        seen.add(page_token)
    report_scope("calendars:list", "truncated", reason=f"page cap {MAX_PAGES} reached")
    return found


def fetch(
    *,
    since: str,
    max_results: int = 250,
    auth_context: Dict[str, Any],
    source_tag: str = "google_calendar",
    window_days: int = 14,
    calendar_ids: Optional[list[str]] = None,
    sync_changes: bool = False,
    sync_tokens: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Google Calendar events from *since* over a *window_days* window."""
    from google_auth import build_service  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]
    from lib.connector_coverage import (  # type: ignore[import]
        report_cursor,
        report_cursor_reset,
        report_deleted_collection,
        report_delta,
        report_full_listing,
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
    deleted_ids = _provider_deleted_calendars(service, calendar_ids, with_retry) if sync_changes else set()

    for cal_id in calendar_ids:
        if cal_id in deleted_ids:
            # An explicit provider deletion marker; a 404 or a missing entry is never treated as one.
            report_deleted_collection(cal_id)
            report_scope(f"calendar:{cal_id}", "complete", records=0, reason="provider reports the calendar deleted")
            continue
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
        seen_pages: set[str] = set()
        final_sync_token = None
        query: dict[str, Any] = {'maxResults': max_results, 'singleEvents': True, 'showDeleted': True}
        if sync_changes:
            if sync_tokens and cal_id in sync_tokens:
                query['syncToken'] = sync_tokens[cal_id]
                report_delta()
        else:
            query.update(timeMin=time_min, timeMax=time_max, orderBy='startTime')
        while True:
            try:
                response = with_retry(
                    lambda c=cal_id, t=page_token: service.events().list(
                        calendarId=c,
                        pageToken=t,
                        **query,
                    ).execute(),
                    context=f"events.list({cal_id})",
                )
                values = response.get('items', [])
                if not isinstance(values, list):
                    raise ValueError('calendar events page must contain a list')
            except Exception as exc:
                reason = _failure_reason(exc)
                print(f"[google_calendar] WARN: {reason}", file=sys.stderr)
                if sync_changes and _http_status(exc) == 410:
                    report_cursor_reset()
                report_scope(scope, "failed", records=fetched, reason=reason)
                failed = True
                break

            pages += 1
            for evt in values:
                try:
                    if not isinstance(evt, dict) or not isinstance(evt.get('id'), str) or not evt['id']:
                        raise ValueError('calendar event identity is invalid')
                    record = None if evt.get('status') == 'cancelled' else _parse_event(evt, cal_name)
                except (ValueError, KeyError, TypeError, AttributeError):
                    if not failed:
                        report_scope(scope, 'failed', records=fetched, reason='calendar event is malformed')
                    failed = True
                    continue
                if evt.get("status") == "cancelled":
                    all_events.append({"id": evt["id"], "calendar_id": cal_id, "status": "cancelled",
                                       "source": source_tag or "google_calendar"})
                    continue
                record["calendar_id"] = cal_id
                if source_tag:
                    record["source"] = source_tag
                all_events.append(record)
                fetched += 1

            page_token = response.get("nextPageToken")
            if not page_token:
                final_sync_token = response.get('nextSyncToken')
                break
            if not isinstance(page_token, str) or page_token in seen_pages or pages >= MAX_PAGES:
                truncated = True
                break
            seen_pages.add(page_token)

        if truncated:
            report_scope(scope, "truncated", records=fetched, reason=f"page cap {MAX_PAGES} reached")
        elif not failed:
            if sync_changes and (not isinstance(final_sync_token, str) or not final_sync_token.strip()):
                report_scope(scope, 'failed', records=fetched, reason='complete sync cursor unavailable')
            else:
                report_scope(scope, "complete", records=fetched)
                if sync_changes:
                    report_cursor(final_sync_token, collection_id=cal_id)
                    if not (sync_tokens and cal_id in sync_tokens):
                        report_full_listing(cal_id)       # no token + no time bounds = the whole calendar

    all_events.sort(key=lambda e: e.get("start", "9999"))
    yield from all_events


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify Google Calendar OAuth credentials are available and valid."""
    try:
        from google_auth import check_stored_credentials  # type: ignore[import]
        status = check_stored_credentials()
        return status.get("client_id_stored", False) and status.get("gcal_token_stored", False)
    except Exception:
        print("[google_calendar] health_check failed", file=sys.stderr)
        return False
