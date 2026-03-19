#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/calendar_create.py — Create a Google Calendar event.

Conforms to ActionHandler protocol.

SAFETY:
  - dry_run: returns event preview JSON; no API write.
  - execute: calls events.insert() on the requested calendar.
  - Undo: events.delete() within undo_window_sec=300 (5 min).
  - autonomy_floor: false — can be auto-executed at Trust Level 2.

Ref: specs/act.md §8.2
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult


# ---------------------------------------------------------------------------
# Required parameters
# ---------------------------------------------------------------------------

_REQUIRED_PARAMS = ("summary", "start", "end")

# Expected format: "2026-03-20T14:00:00" or "2026-03-20" (all-day)
_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Validate required fields and date/time formats."""
    params = proposal.parameters

    for field in _REQUIRED_PARAMS:
        if not params.get(field, "").strip():
            return False, f"Missing required parameter: '{field}'"

    # Validate date formats
    for dt_field in ("start", "end"):
        raw = params.get(dt_field, "")
        if not _try_parse_datetime(raw):
            return False, (
                f"Parameter '{dt_field}' has unrecognised format: '{raw}'. "
                "Expected ISO 8601: '2026-03-20T14:00:00' or '2026-03-20'"
            )

    # Ensure start < end
    start_dt = _try_parse_datetime(params["start"])
    end_dt = _try_parse_datetime(params["end"])
    if start_dt and end_dt and start_dt >= end_dt:
        return False, "Event start must be before end"

    # Summary length
    if len(params.get("summary", "")) > 1024:
        return False, "Event summary too long (max 1024 chars)"

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Return a preview of the calendar event without creating it."""
    params = proposal.parameters
    event_body = _build_event_body(params)

    return ActionResult(
        status="success",
        message=f"Preview: create event '{params.get('summary')}' on {params.get('start')}",
        data={
            "preview_mode": True,
            "event": event_body,
            "calendar_id": params.get("calendar_id", "primary"),
        },
        reversible=False,
        reverse_action=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Create the calendar event via Google Calendar API."""
    params = proposal.parameters
    calendar_id = params.get("calendar_id", "primary")
    event_body = _build_event_body(params)

    try:
        from google_auth import build_service  # noqa: PLC0415
        service = build_service("calendar", "v3")

        created = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
        ).execute()

        event_id = created.get("id", "")
        html_link = created.get("htmlLink", "")

        return ActionResult(
            status="success",
            message=f"✅ Event created: {created.get('summary', '')} — {html_link}",
            data={
                "event_id": event_id,
                "html_link": html_link,
                "calendar_id": calendar_id,
                "summary": created.get("summary", ""),
                "start": params.get("start"),
                "end": params.get("end"),
                # Original event body stored for undo
                "_original_event_body": event_body,
            },
            reversible=True,
            reverse_action=None,
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to create calendar event: {e}",
            data={"error": str(e), "calendar_id": calendar_id},
            reversible=False,
            reverse_action=None,
        )


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """Build undo proposal: delete the created event."""
    import uuid

    event_id = result_data.get("event_id", "")
    calendar_id = result_data.get("calendar_id", "primary")
    if not event_id:
        raise ValueError("Cannot undo: event_id not found in result data")

    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="calendar_create_undo",
        domain=original.domain,
        title=f"Undo: Delete calendar event",
        description=f"Delete event {event_id} from calendar {calendar_id}",
        parameters={"event_id": event_id, "calendar_id": calendar_id},
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=None,
        source_step=original.source_step,
        source_skill=original.source_skill,
        linked_oi=original.linked_oi,
    )


def health_check() -> bool:
    """Verify Google Calendar credentials are available."""
    try:
        from google_auth import check_stored_credentials  # noqa: PLC0415
        status = check_stored_credentials()
        # Calendar uses google_token_stored (same OAuth flow)
        return bool(
            status.get("google_token_stored", False)
            or status.get("gmail_token_stored", False)
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_parse_datetime(value: str) -> datetime | None:
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _is_all_day(value: str) -> bool:
    return len(value.strip()) == 10  # "YYYY-MM-DD"


def _build_event_body(params: dict[str, Any]) -> dict[str, Any]:
    """Construct the Google Calendar event resource dict from action params."""
    start_raw = params.get("start", "")
    end_raw = params.get("end", "")
    all_day = _is_all_day(start_raw)

    if all_day:
        start_entry = {"date": start_raw.strip()}
        end_entry = {"date": end_raw.strip()}
    else:
        tz = params.get("timezone", "UTC")
        start_entry = {"dateTime": start_raw.strip(), "timeZone": tz}
        end_entry = {"dateTime": end_raw.strip(), "timeZone": tz}

    event: dict[str, Any] = {
        "summary": params.get("summary", ""),
        "start": start_entry,
        "end": end_entry,
    }

    if params.get("description"):
        event["description"] = params["description"]
    if params.get("location"):
        event["location"] = params["location"]

    # Attendees: comma-separated email list
    if params.get("attendees"):
        emails = [e.strip() for e in params["attendees"].split(",") if e.strip()]
        event["attendees"] = [{"email": e} for e in emails]

    # Reminders
    if params.get("reminders"):
        # Expected: "10m" or "60m" — simple integer-minutes
        raw = params["reminders"]
        try:
            minutes = int(str(raw).rstrip("m"))
            event["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": minutes}],
            }
        except (ValueError, TypeError):
            event["reminders"] = {"useDefault": True}
    else:
        event["reminders"] = {"useDefault": True}

    return event
