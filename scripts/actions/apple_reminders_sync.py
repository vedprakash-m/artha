#!/usr/bin/env python3
# pii-guard: reminder content is standard sensitivity; no financial/medical data
"""
scripts/actions/apple_reminders_sync.py — Apple Reminders sync action handler.

ActionHandler protocol: module-level validate(), dry_run(), execute(), health_check().
macOS only — gracefully returns failure on other platforms.

Single-writer model: Artha creates reminders in Apple Reminders (write-once);
Apple Reminders owns the item thereafter. Only completion status flows back via connector.

Requires pyobjc-framework-EventKit: pip install 'artha[apple]'

Ref: specs/connect.md §6.2
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult  # type: ignore[import]

_IS_MACOS = platform.system() == "Darwin"

# ---------------------------------------------------------------------------
# ActionHandler protocol
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Validate apple_reminders_sync proposal. Returns (ok, reason).

    Required parameters:
        title (str): Reminder title (non-empty, ≤500 chars)
    Optional:
        notes (str): Additional notes
        due_date (str): ISO-8601 date or human-readable string
        list_name (str): Reminders list to add to (default: Reminders)
        priority (int): 0 (none), 1 (low), 5 (medium), 9 (high)
        linked_oi (str): OI-NNN reference
    """
    params = proposal.parameters or {}
    title = params.get("title", "")
    if not title or not title.strip():
        return False, "title is required and must be non-empty"
    if len(title) > 500:
        return False, f"title exceeds 500 chars (got {len(title)})"
    priority = params.get("priority", 0)
    if priority not in (0, 1, 5, 9):
        return False, f"priority must be 0 (none), 1 (low), 5 (medium), or 9 (high), got {priority}"
    return True, "valid"


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Return a preview of what execute() would do without creating a reminder."""
    params = proposal.parameters or {}
    title = params.get("title", "")
    list_name = params.get("list_name", "Reminders")
    due_date = params.get("due_date", "")
    notes = params.get("notes", "")
    linked_oi = params.get("linked_oi", "")
    priority = params.get("priority", 0)

    priority_labels = {0: "none", 1: "low", 5: "medium", 9: "high"}
    preview_lines = [
        f"Reminder: {title}",
        f"List: {list_name}",
    ]
    if due_date:
        preview_lines.append(f"Due: {due_date}")
    if notes:
        preview_lines.append(f"Notes: {notes[:100]}")
    if priority:
        preview_lines.append(f"Priority: {priority_labels.get(priority, priority)}")
    if linked_oi:
        preview_lines.append(f"Artha OI: {linked_oi}")
    if not _IS_MACOS:
        preview_lines.append("[NOTE: macOS only — would fail on execute]")

    return ActionResult(
        status="success",
        message="[DRY RUN] Reminder not created — preview only",
        data={"preview": "\n".join(preview_lines), "title": title},
        reversible=True,
        reverse_action=None,
        undo_deadline=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Create the reminder in Apple Reminders via EventKit (macOS only)."""
    if not _IS_MACOS:
        return ActionResult(
            status="failure",
            message=f"apple_reminders_sync is macOS only (running on {platform.system()})",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

    ok, reason = validate(proposal)
    if not ok:
        return ActionResult(
            status="failure",
            message=f"Validation failed: {reason}",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

    params = proposal.parameters or {}
    title = params["title"]
    notes = params.get("notes", "")
    due_date_str = params.get("due_date", "")
    list_name = params.get("list_name", "Reminders")
    priority = params.get("priority", 0)
    linked_oi = params.get("linked_oi", "")

    if linked_oi:
        oi_ref = f"\n\n[{linked_oi}]" if notes else f"[{linked_oi}]"
        notes = notes + oi_ref

    try:
        from EventKit import (  # type: ignore[import]
            EKEventStore, EKReminder, EKEntityTypeReminder, EKCalendar,
            EKAlarm,
        )
        import Foundation  # type: ignore[import]
    except ImportError:
        return ActionResult(
            status="failure",
            message="pyobjc-framework-EventKit not installed. Run: pip install 'artha[apple]'",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

    try:
        from connectors.apple_reminders import _get_event_store  # type: ignore[import]
        store = _get_event_store()
    except PermissionError as exc:
        return ActionResult(
            status="failure",
            message=str(exc),
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )
    except Exception as exc:
        return ActionResult(
            status="failure",
            message=f"EventKit store initialization failed: {exc}",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

    # Find the target calendar (list)
    calendars = store.calendarsForEntityType_(EKEntityTypeReminder)
    target_calendar = None
    for cal in calendars:
        if cal.title().lower() == list_name.lower():
            target_calendar = cal
            break
    if not target_calendar:
        target_calendar = store.defaultCalendarForNewReminders()

    # Create the reminder
    reminder = EKReminder.reminderWithEventStore_(store)
    reminder.setTitle_(title)
    reminder.setCalendar_(target_calendar)
    reminder.setPriority_(priority)
    if notes:
        reminder.setNotes_(notes)

    # Set due date if provided
    if due_date_str:
        try:
            from datetime import datetime, timezone  # noqa: PLC0415
            dt = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
            ns_date = Foundation.NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())
            components = Foundation.NSCalendar.currentCalendar().components_fromDate_(
                (1 << 2) | (1 << 4) | (1 << 5) | (1 << 10) | (1 << 14),  # era+year+month+day+hour+min
                ns_date,
            )
            reminder.setDueDateComponents_(components)
        except Exception:
            pass  # Invalid date string — create reminder without due date

    # Save to store
    error_ptr = Foundation.NSError.errorWithDomain_code_userInfo_(None, 0, None)
    saved = store.saveReminder_commit_error_(reminder, True, None)
    if not saved:
        return ActionResult(
            status="failure",
            message="EventKit failed to save reminder",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

    reminder_id = str(reminder.calendarItemIdentifier())
    return ActionResult(
        status="success",
        message=f"Reminder created: {title[:60]}",
        data={"reminder_id": reminder_id, "list_name": list_name, "linked_oi": linked_oi},
        reversible=True,
        reverse_action=None,
        undo_deadline=None,
    )


def health_check() -> bool:
    """Verify EventKit access. Takes NO arguments (ActionHandler protocol)."""
    if not _IS_MACOS:
        return False
    try:
        from connectors.apple_reminders import _get_event_store  # type: ignore[import]
        _get_event_store()
        return True
    except ImportError:
        return False
    except PermissionError:
        return False
    except Exception:
        return False
