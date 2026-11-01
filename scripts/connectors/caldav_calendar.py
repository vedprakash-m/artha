"""
scripts/connectors/caldav_calendar.py — iCloud CalDAV calendar connector (standalone).

Fetches calendar events via iCloud CalDAV and yields standardised event dicts.
All CalDAV + iCal parsing logic is self-contained — no dependency on
icloud_calendar_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_CALDAV_URL = "https://caldav.icloud.com"


# ---------------------------------------------------------------------------
# CalDAV helpers (moved from icloud_calendar_fetch.py)
# ---------------------------------------------------------------------------

def _get_client(apple_id: str, app_pwd: str):
    try:
        import caldav  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "[caldav_calendar] caldav package not installed — run: pip install caldav"
        )
    return caldav.DAVClient(url=_CALDAV_URL, username=apple_id, password=app_pwd)


def _get_cal_name(cal) -> str:
    try:
        return cal.get_display_name() or str(cal.url)
    except Exception:
        return getattr(cal, "name", None) or str(cal.url)


def _to_utc_dt(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _format_dt(value) -> str:
    return _to_utc_dt(value).isoformat()


def _safe_get(component, name: str, default=None):
    try:
        return getattr(component, name).value
    except AttributeError:
        return default


def _parse_organizer(organizer_value) -> str:
    if organizer_value is None:
        return ""
    raw = str(organizer_value)
    email_match = re.search(r"mailto:([^\s]+)", raw, re.IGNORECASE)
    email_addr = email_match.group(1) if email_match else ""
    cn_match = re.search(r'CN=([^;:]+)', raw)
    name = cn_match.group(1).strip('"').strip() if cn_match else ""
    if name and email_addr:
        return f"{name} <{email_addr}>"
    return email_addr or name or raw


def _parse_attendees(component) -> list[dict]:
    attendees: list[dict] = []
    try:
        raw_attendees = component.attendee_list
    except AttributeError:
        return []
    for att in raw_attendees:
        att_str = str(att.value) if hasattr(att, "value") else str(att)
        email_match = re.search(r"mailto:([^\s]+)", att_str, re.IGNORECASE)
        email_addr = email_match.group(1) if email_match else ""
        cn_match = re.search(r'CN=([^;:]+)', att_str)
        name = cn_match.group(1).strip('"').strip() if cn_match else ""
        if email_addr or name:
            attendees.append({"email": email_addr, "name": name, "self": False})
    return attendees


def _parse_event(event_obj, calendar_name: str) -> Optional[dict]:
    try:
        vobj = event_obj.vobject_instance
        vevent = vobj.vevent
    except Exception:
        print("[caldav_calendar] WARN: cannot parse event", file=sys.stderr)
        return None
    summary = _safe_get(vevent, "summary", "") or ""
    dtstart_val = _safe_get(vevent, "dtstart")
    dtend_val = _safe_get(vevent, "dtend")
    description = _safe_get(vevent, "description", "") or ""
    location = _safe_get(vevent, "location", "") or ""
    status_raw = (_safe_get(vevent, "status", "") or "confirmed").lower()
    uid = _safe_get(vevent, "uid", "") or ""
    instance = _safe_get(vevent, 'recurrence_id')
    identity = json.dumps([uid, _format_dt(instance)], separators=(',', ':')) if instance is not None else uid
    if "cancel" in status_raw:
        return {'id': identity, 'status': 'cancelled'} if uid else None
    if dtstart_val is None:
        return None
    all_day = not isinstance(dtstart_val, datetime)
    start_str = dtstart_val.isoformat() if all_day else _format_dt(dtstart_val)
    end_str = (dtend_val.isoformat() if all_day else _format_dt(dtend_val)) if dtend_val is not None else start_str
    organizer = _parse_organizer(_safe_get(vevent, "organizer"))
    attendees = _parse_attendees(vevent)
    is_recurring = hasattr(vevent, "rrule") or hasattr(vevent, "recurrence_id")
    class_val = (_safe_get(vevent, "class", "") or "").lower()
    visibility = "private" if "private" in class_val else "normal"
    # Online meeting detection
    url_val = _safe_get(vevent, "url", "") or ""
    meet_url_str = url_val
    combined = f"{description} {location}".lower()
    url_patterns = [
        r"https://[\w.-]*zoom\.us/[^\s\"<>]+",
        r"https://meet\.google\.com/[^\s\"<>]+",
        r"https://teams\.microsoft\.com/[^\s\"<>]+",
        r"https://whereby\.com/[^\s\"<>]+",
        r"https://[\w.-]*webex\.com/[^\s\"<>]+",
    ]
    is_online = False
    meet_url = ""
    for pattern in url_patterns:
        m = re.search(pattern, f"{description} {location}", re.IGNORECASE)
        if m:
            is_online = True
            meet_url = m.group(0)
            break
    if not is_online and meet_url_str:
        is_online = bool(re.search(r"zoom|meet\.google|teams\.microsoft|webex|whereby",
                                   meet_url_str, re.IGNORECASE))
        meet_url = meet_url_str if is_online else ""
    return {
        "id": identity,
        "calendar": calendar_name,
        "summary": summary,
        "start": start_str,
        "end": end_str,
        "all_day": all_day,
        "location": location.strip(),
        "description": description[:2000].strip(),
        "organizer": organizer,
        "attendees": attendees,
        "status": status_raw or "confirmed",
        "visibility": visibility,
        "recurring": is_recurring,
        "is_online_meeting": is_online,
        "online_meeting_url": meet_url,
        "source": "icloud_calendar",
    }


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

#: The runtime hands a committed per-calendar cursor to ``fetch(sync_tokens=...)`` for these sources.
SYNC_TOKEN_SCOPE = "discovered_calendars"


def _real_token(value: Any) -> bool:
    """A server sync token; the library's ``fake-`` fallback tokens are not provider cursors."""
    return isinstance(value, str) and bool(value.strip()) and not value.startswith("fake-")


def _status_hint(exc: BaseException) -> str:
    match = re.search(r"\b(40[1-9]|41\d|50\d)\b", str(exc))
    return f"provider request failed ({match.group(1)})" if match else "provider request failed"


def _load_cursor(raw: str, now: datetime) -> Optional[dict]:
    """The cursor, None when its window has passed; ValueError when it cannot be used."""
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"v", "token", "through", "members"} or data["v"] != 1
            or not _real_token(data["token"]) or not isinstance(data["through"], str)
            or not isinstance(data["members"], dict)
            or any(not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(i, str) for i in v)
                   for k, v in data["members"].items())):
        raise ValueError("calendar sync cursor is invalid")
    through = datetime.fromisoformat(data["through"])
    if through.tzinfo is None:
        raise ValueError("calendar sync cursor is invalid")
    return data if through > now else None


def _envelope(token: str, through: str, members: dict) -> str:
    return json.dumps({"v": 1, "token": token, "through": through, "members": members},
                      sort_keys=True, separators=(",", ":"))


def _take_token(cal: Any) -> Optional[str]:
    """Token of the collection's state, requested BEFORE listing; None when the server has no real tokens."""
    try:
        token = cal.get_objects_by_sync_token(None, load_objects=False, disable_fallback=True).sync_token
    except Exception:
        return None
    return token if _real_token(token) else None


def _delta_step(cal: Any, scope: str, cursor: dict) -> tuple[Optional[str], list[str], bool]:
    """New token (None on failure), verified removals and whether a member changed."""
    from lib.connector_coverage import report_cursor_reset, report_scope  # type: ignore[import]  # noqa: PLC0415
    from caldav.lib.error import NotFoundError  # type: ignore[import]  # noqa: PLC0415
    removed: list[str] = []
    changed = False
    try:
        updates = cal.get_objects_by_sync_token(cursor["token"], load_objects=False, disable_fallback=True)
        token = updates.sync_token
        if not _real_token(token):
            raise ValueError("sync token unavailable")
        for member in updates:
            try:
                member.load()
            except NotFoundError:
                removed.append(str(member.url))
            else:
                changed = True
    except Exception as exc:
        if "valid-sync-token" in str(exc).lower():
            report_cursor_reset()
        print("[caldav_calendar] WARN: sync request failed", file=sys.stderr)
        report_scope(scope, "failed", reason=_status_hint(exc))
        return None, removed, changed
    return token, removed, changed


def fetch(
    *,
    since: str,
    max_results: int = 500,
    auth_context: Dict[str, Any],
    source_tag: str = "icloud_calendar",
    window_days: int = 14,
    calendar_filter: Optional[list[str]] = None,
    sync_changes: bool = False,
    sync_tokens: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield iCloud CalDAV events from *since* over a *window_days* window."""
    from lib.connector_coverage import (  # type: ignore[import]
        report_cursor,
        report_cursor_reset,
        report_delta,
        report_full_listing,
        report_scope,
    )

    if type(sync_changes) is not bool:
        raise ValueError('sync_changes must be a boolean')
    if sync_tokens is not None and (not sync_changes or not isinstance(sync_tokens, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                   for k, v in sync_tokens.items())):
        raise ValueError('sync_tokens must contain native tokens for the sync scope')

    if calendar_filter is not None and (not isinstance(calendar_filter, list) or not calendar_filter
            or any(not isinstance(item, str) or not item.strip() for item in calendar_filter)
            or len({item.lower() for item in calendar_filter}) != len(calendar_filter)):
        raise ValueError('calendar_filter must contain distinct nonempty calendar filters')

    apple_id = auth_context.get("apple_id", "")
    app_password = auth_context.get("app_password") or auth_context.get("password", "")
    if not apple_id or not app_password:
        raise RuntimeError("[caldav_calendar] auth_context missing apple_id or app_password")

    start_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    # Window must reach into the future even after a long gap (see google_calendar).
    end_dt = max(start_dt, datetime.now(timezone.utc)) + timedelta(days=window_days)

    print(f"[caldav_calendar] range={start_dt.date()}→{end_dt.date()}", file=sys.stderr)

    client = _get_client(apple_id, app_password)
    try:
        principal = client.principal()
        all_cals = principal.calendars()
    except Exception as exc:
        print("[caldav_calendar] ERROR: CalDAV principal error", file=sys.stderr)
        report_scope("principal", "failed", reason=type(exc).__name__)
        # An unreachable account is *unavailable*, not an empty calendar.
        raise RuntimeError("[caldav_calendar] CalDAV principal error") from exc

    if calendar_filter is not None:
        cf_lower = [f.lower() for f in calendar_filter]
        calendars = [c for c in all_cals if any(
            f in _get_cal_name(c).lower() or f in str(c.url).lower() for f in cf_lower
        )]
        if any(not any(f in _get_cal_name(c).lower() or f in str(c.url).lower()
                       for c in calendars) for f in cf_lower):
            report_scope('calendar-filter', 'failed', reason='requested calendar filter did not match any calendar')
    else:
        calendars = all_cals

    all_events: list[dict] = []
    now = datetime.now(timezone.utc)
    for cal in calendars:
        cal_name = _get_cal_name(cal)
        calendar_id = str(getattr(cal, 'url', '') or '')
        scope = f"calendar:{cal_name}"
        token: Optional[str] = None
        members: dict[str, list[str]] = {}
        if sync_changes:
            if not calendar_id:
                report_scope(scope, 'failed', reason='stable calendar identity unavailable')
                continue
            raw_cursor = (sync_tokens or {}).get(calendar_id)
            cursor: Optional[dict] = None
            if raw_cursor is not None:
                try:
                    cursor = _load_cursor(raw_cursor, now)
                except (ValueError, TypeError):
                    report_cursor_reset()
                    report_scope(scope, 'failed', reason='stored calendar cursor is unusable')
                    continue
            if cursor is not None:
                token, removed, changed = _delta_step(cal, scope, cursor)
                kept = dict(cursor["members"])
                for href in removed:
                    for identity in kept.pop(href, []):
                        all_events.append({"id": identity, "status": "cancelled", "calendar_id": calendar_id,
                                           "source": source_tag})
                if token is None:
                    continue
                if not changed:
                    report_scope(scope, "complete", records=len(removed))
                    report_cursor(_envelope(token, cursor["through"], kept), collection_id=calendar_id)
                    report_delta()
                    continue
            else:
                token = _take_token(cal)
        try:
            events_raw = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
        except Exception as exc:
            print("[caldav_calendar] WARN: calendar request failed", file=sys.stderr)
            report_scope(scope, "failed", reason=type(exc).__name__)
            continue
        cal_count = 0
        failed = False
        for event_obj in events_raw:
            try:
                rec = _parse_event(event_obj, cal_name)
            except (ValueError, KeyError, TypeError, AttributeError):
                rec = None
            if rec is None:
                failed = True
                continue
            if not calendar_id:
                report_scope(scope, 'failed', reason='stable calendar identity unavailable')
                failed = True
                continue
            href = str(getattr(event_obj, 'url', '') or '')
            if token is not None and rec.get('status') != 'cancelled':
                if not href:
                    failed = True
                    continue
                members.setdefault(href, []).append(rec['id'])
            rec['calendar_id'] = calendar_id
            all_events.append(rec)
            cal_count += 1
        report_scope(scope, "failed" if failed else "complete", records=cal_count,
                     reason='calendar event is malformed or lacks stable identity' if failed else '')
        if sync_changes and not failed:
            if token is not None:
                report_cursor(_envelope(token, end_dt.isoformat(), members), collection_id=calendar_id)
            report_full_listing(calendar_id, window_start=start_dt.isoformat(), window_end=end_dt.isoformat())

    all_events.sort(key=lambda r: r.get("start", ""))
    if len(all_events) > max_results:
        report_scope("result-cap", "truncated", records=max_results,
                     reason=f"{len(all_events)} events exceed max_results {max_results}")
    for rec in all_events[:max_results]:
        if source_tag:
            rec["source"] = source_tag
        yield rec


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify iCloud CalDAV credentials and connectivity."""
    try:
        apple_id = auth_context.get("apple_id", "")
        app_password = auth_context.get("app_password") or auth_context.get("password", "")
        if not apple_id or not app_password:
            return False
        client = _get_client(apple_id, app_password)
        client.principal()
        return True
    except Exception:
        print("[caldav_calendar] health_check failed", file=sys.stderr)
        return False
