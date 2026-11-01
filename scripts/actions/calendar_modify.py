#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/calendar_modify.py — Modify an existing Google Calendar event.

Conforms to ActionHandler protocol.

SAFETY:
  - dry_run: fetches the existing event and shows a diff preview; no write.
  - execute: verifies the reviewed ETag and uses If-Match for the exact update.
  - Undo: re-patches with the original field values within undo_window_sec=300.
  - Requires explicit review; the action type remains held until live acceptance.

Ref: specs/act.md §8.2
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult

# Module-level import so tests can patch actions.calendar_modify.build_service
try:
    from google_auth import build_service, check_stored_credentials  # type: ignore[import]
except ImportError:  # pragma: no cover
    build_service = None  # type: ignore[assignment]
    check_stored_credentials = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Required parameters
# ---------------------------------------------------------------------------

_REQUIRED_PARAMS = ("event_id", "updates")

# Supported top-level fields that can be patched
_PATCHABLE_FIELDS = frozenset({
    "summary", "description", "location", "start", "end",
    "attendees", "reminders",
})


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Validate event_id and updates dict."""
    params = proposal.parameters

    for field in _REQUIRED_PARAMS:
        val = params.get(field)
        # Treat as missing if absent, None, or an empty string;
        # but NOT if it's an empty dict — that case is handled explicitly below.
        if field not in params or val is None or (isinstance(val, str) and not val):
            return False, f"Missing required parameter: '{field}'"

    updates = params.get("updates")
    if not isinstance(updates, dict):
        return False, "Parameter 'updates' must be a dict of fields to change"

    if not updates:
        return False, "Parameter 'updates' is empty — nothing to modify"

    unknown_fields = set(updates.keys()) - _PATCHABLE_FIELDS
    if unknown_fields:
        return False, (
            f"Unsupported update fields: {unknown_fields}. "
            f"Allowed: {sorted(_PATCHABLE_FIELDS)}"
        )

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Fetch the existing event and return a before/after diff preview."""
    params = proposal.parameters
    event_id: str = params.get("event_id", "")
    calendar_id: str = params.get("calendar_id", "primary")
    updates: dict = params.get("updates", {})

    try:
        service = build_service("calendar", "v3")

        current = service.events().get(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()

        if not isinstance(current.get('etag'), str) or not current['etag']:
            raise ValueError('event version unavailable; cannot prepare a safe review')
        # Build diff: show only the fields being changed
        diff: dict[str, Any] = {}
        for field, new_value in updates.items():
            old_value = current.get(field)
            diff[field] = {"before": old_value, "after": new_value}

        return ActionResult(
            status="success",
            message=f"Preview: modify event '{current.get('summary', event_id)}'",
            data={
                "preview_mode": True,
                "expected_etag": current["etag"],
                "event_id": event_id,
                "calendar_id": calendar_id,
                "current_summary": current.get("summary", ""),
                "diff": diff,
            },
            reversible=False,
            reverse_action=None,
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to fetch event for preview: {e}",
            data={"error": str(e), "event_id": event_id},
            reversible=False,
            reverse_action=None,
        )


def _read_reviewed_event(service: Any, proposal: ActionProposal) -> dict[str, Any]:
    params = proposal.parameters
    current = service.events().get(calendarId=params.get('calendar_id', 'primary'),
                                   eventId=params['event_id']).execute()
    if (not isinstance(current, dict) or current.get('id') != params['event_id']
            or current.get('etag') != params['expected_etag'] or current.get('status') == 'cancelled'):
        raise ValueError('Calendar event changed; review its current diff before applying.')
    return current


def verify_provider_version(proposal: ActionProposal) -> None:
    """Read the reviewed event and current calendar write role without changing either."""
    service = build_service('calendar', 'v3')
    calendar_id = proposal.parameters.get('calendar_id', 'primary')
    calendar = service.calendarList().get(calendarId=calendar_id).execute()
    if (not isinstance(calendar, dict) or calendar.get('accessRole') not in ('owner', 'writer')
            or not isinstance(calendar.get('id'), str) or not calendar['id']
            or (calendar['id'] != calendar_id and not (calendar_id == 'primary' and calendar.get('primary') is True))):
        raise ValueError('Calendar identity or current write authorization changed.')
    _read_reviewed_event(service, proposal)


def execute(proposal: ActionProposal) -> ActionResult:
    """Patch the calendar event with the requested updates."""
    params = proposal.parameters
    event_id: str = params.get("event_id", "")
    calendar_id: str = params.get("calendar_id", "primary")
    updates: dict = params.get("updates", {})
    expected = params.get('expected_etag')
    if not isinstance(expected, str) or not expected or any(c in expected for c in ('\r', '\n')):
        return ActionResult('failure', 'Calendar change needs a current reviewed version; run --show again.',
                            None, False, None)
    dispatched = False

    try:
        service = build_service("calendar", "v3")

        # Snapshot original values BEFORE patching (for undo)
        try:
            current = _read_reviewed_event(service, proposal)
        except ValueError:
            return ActionResult('failure', 'Calendar event changed; review its current diff before applying.',
                                None, False, None)
        original_values: dict[str, Any] = {
            field: current.get(field) for field in updates.keys()
        }

        # Build patch body
        patch_body = _build_patch_body(updates, current)

        request = service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=patch_body,
        )
        request.headers['If-Match'] = expected
        dispatched = True
        updated = request.execute()
        if updated.get('id') != event_id or not isinstance(updated.get('etag'), str) or not updated['etag']:
            return ActionResult('unknown', 'Calendar update returned no matching versioned receipt; reconcile before retrying.',
                                None, False, None)

        return ActionResult(
            status="success",
            message=f"✅ Event updated: '{updated.get('summary', event_id)}'",
            data={
                "event_id": event_id,
                "calendar_id": calendar_id,
                "html_link": updated.get("htmlLink", ""),
                "provider_version": updated["etag"],
                "summary": updated.get("summary", ""),
                # Stored for undo: original field values to restore
                "_original_values": original_values,
            },
            reversible=True,
            reverse_action=None,
        )

    except Exception as exc:
        refused = getattr(getattr(exc, 'resp', None), 'status', None) in (400, 401, 403, 404, 409, 412, 422, 429)
        status = 'failure' if not dispatched or refused else 'unknown'
        message = ('Calendar change was refused; review again.' if refused else
                   'Calendar update is unverified; reconcile before retrying.' if dispatched else
                   'Calendar event could not be read; no update was attempted.')
        return ActionResult(status, message, None, False, None)


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """Build undo proposal: re-patch with original field values."""
    import uuid

    event_id = result_data.get("event_id", "")
    calendar_id = result_data.get("calendar_id", "primary")
    original_values = result_data.get("_original_values", {})

    if not event_id:
        raise ValueError("Cannot undo: event_id not found in result data")
    if not original_values:
        raise ValueError("Cannot undo: original values not found in result data")
    version = result_data.get('provider_version')
    if not isinstance(version, str) or not version:
        raise ValueError('Cannot undo without a verified resulting event version')
    parameters = {
        "event_id": event_id,
        "calendar_id": calendar_id,
        "updates": original_values,
        "expected_etag": version,
    }
    from catchup_core.actions import normalize_preconditions
    preconditions = normalize_preconditions(original.parameters.get('preconditions'),
                                            core_derived=original.source_skill == 'catchup_core')
    if preconditions:
        # Undo is a new reviewed effect. Preserve the original evidence authority,
        # while the resulting provider version becomes the new write condition.
        parameters['preconditions'] = {**preconditions, 'provider_versions': {event_id: version}}

    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="calendar_modify",
        domain=original.domain,
        title="Undo: Revert calendar event changes",
        description=f"Revert event {event_id} to previous values",
        parameters=parameters,
        friction="standard",
        min_trust=1,
        sensitivity=original.sensitivity,
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
        status = check_stored_credentials()
        return bool(
            status.get("google_token_stored", False)
            or status.get("gmail_token_stored", False)
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_patch_body(
    updates: dict[str, Any],
    current_event: dict[str, Any],
) -> dict[str, Any]:
    """Convert the updates dict into a Google Calendar patch body.

    Handles nested fields (start/end) requiring dateTime/date format.
    """
    patch: dict[str, Any] = {}

    for field, value in updates.items():
        if field in ("start", "end"):
            # Preserve the timezone if present in the existing event
            existing_entry = current_event.get(field, {})
            tz = existing_entry.get("timeZone", "UTC")
            # Detect all-day vs datetime
            if isinstance(value, str) and len(value.strip()) == 10:
                patch[field] = {"date": value.strip()}
            else:
                patch[field] = {"dateTime": str(value).strip(), "timeZone": tz}
        elif field == "attendees" and isinstance(value, str):
            # Accept comma-separated email string in updates
            emails = [e.strip() for e in value.split(",") if e.strip()]
            patch["attendees"] = [{"email": e} for e in emails]
        else:
            patch[field] = value

    return patch
