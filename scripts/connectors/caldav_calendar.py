"""
scripts/connectors/caldav_calendar.py — iCloud CalDAV calendar connector (standalone).

Fetches calendar events via iCloud CalDAV and yields standardised event dicts.
All CalDAV + iCal parsing logic is self-contained — no dependency on
icloud_calendar_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone, timedelta, date
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
    except Exception as exc:
        print(f"[caldav_calendar] WARN: cannot parse event: {exc}", file=sys.stderr)
        return None
    summary = _safe_get(vevent, "summary", "") or ""
    dtstart_val = _safe_get(vevent, "dtstart")
    dtend_val = _safe_get(vevent, "dtend")
    description = _safe_get(vevent, "description", "") or ""
    location = _safe_get(vevent, "location", "") or ""
    status_raw = (_safe_get(vevent, "status", "") or "confirmed").lower()
    uid = _safe_get(vevent, "uid", "") or ""
    if dtstart_val is None or "cancel" in status_raw:
        return None
    all_day = not isinstance(dtstart_val, datetime)
    start_str = _format_dt(dtstart_val)
    end_str = _format_dt(dtend_val) if dtend_val is not None else start_str
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
        "id": uid,
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

def fetch(
    *,
    since: str,
    max_results: int = 500,
    auth_context: Dict[str, Any],
    source_tag: str = "icloud_calendar",
    window_days: int = 14,
    calendar_filter: Optional[list[str]] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield iCloud CalDAV events from *since* over a *window_days* window."""
    apple_id = auth_context.get("apple_id", "")
    app_password = auth_context.get("app_password") or auth_context.get("password", "")
    if not apple_id or not app_password:
        raise RuntimeError("[caldav_calendar] auth_context missing apple_id or app_password")

    start_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    end_dt = start_dt + timedelta(days=window_days)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    print(f"[caldav_calendar] range={start_dt.date()}→{end_dt.date()}", file=sys.stderr)

    client = _get_client(apple_id, app_password)
    try:
        principal = client.principal()
        all_cals = principal.calendars()
    except Exception as exc:
        print(f"[caldav_calendar] ERROR: CalDAV principal error: {exc}", file=sys.stderr)
        return

    if calendar_filter:
        cf_lower = [f.lower() for f in calendar_filter]
        calendars = [c for c in all_cals if any(
            f in _get_cal_name(c).lower() or f in str(c.url).lower() for f in cf_lower
        )]
    else:
        calendars = all_cals

    all_events: list[dict] = []
    for cal in calendars:
        cal_name = _get_cal_name(cal)
        try:
            events_raw = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
        except Exception as exc:
            print(f"[caldav_calendar] WARN: error fetching '{cal_name}': {exc}", file=sys.stderr)
            continue
        for event_obj in events_raw:
            rec = _parse_event(event_obj, cal_name)
            if rec is not None:
                all_events.append(rec)

    all_events.sort(key=lambda r: r.get("start", ""))
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
    except Exception as exc:
        print(f"[caldav_calendar] health_check failed: {exc}", file=sys.stderr)
        return False
