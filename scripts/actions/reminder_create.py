#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/reminder_create.py — Create a Microsoft To Do reminder task.

Uses the MS Graph Tasks API (same credentials as todo_sync.py).

SAFETY:
  - dry_run: returns task preview JSON; no API write.
  - execute: creates task in the specified To Do list.
  - No undo: task deletion is trivial for the user; undo window not applicable.
  - autonomy_floor: false — can be auto-executed at Trust Level 2.

Ref: specs/act.md §8.3
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult

# Graph API base
_GRAPH_TODO_BASE = "https://graph.microsoft.com/v1.0/me/todo/lists"

# Priority mapping: Artha → Graph importance
_IMPORTANCE_MAP = {"P0": "high", "P1": "normal", "P2": "low", "high": "high", "normal": "normal", "low": "low"}


# ---------------------------------------------------------------------------
# Required parameters
# ---------------------------------------------------------------------------

_REQUIRED_PARAMS = ("title",)


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Check required params."""
    params = proposal.parameters

    for field in _REQUIRED_PARAMS:
        if not params.get(field, "").strip():
            return False, f"Missing required parameter: '{field}'"

    # Validate due_date format if provided
    due_date = params.get("due_date", "")
    if due_date:
        try:
            from datetime import datetime
            datetime.strptime(due_date.strip(), "%Y-%m-%d")
        except ValueError:
            return False, f"Parameter 'due_date' has invalid format: '{due_date}'. Expected: YYYY-MM-DD"

    # Validate reminder_datetime format if provided
    remind_dt = params.get("reminder_datetime", "")
    if remind_dt:
        _parsed = False
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                from datetime import datetime
                datetime.strptime(remind_dt.strip(), fmt)
                _parsed = True
                break
            except ValueError:
                continue
        if not _parsed:
            return False, (
                f"Parameter 'reminder_datetime' has invalid format: '{remind_dt}'. "
                "Expected: '2026-03-20T14:00:00'"
            )

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Return task preview without creating it."""
    params = proposal.parameters
    body = _build_task_body(params)

    return ActionResult(
        status="success",
        message=f"Preview: create To Do task '{params.get('title')}'",
        data={
            "preview_mode": True,
            "task": body,
            "list_name": params.get("list_name", "Artha"),
        },
        reversible=False,
        reverse_action=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Create the task in Microsoft To Do."""
    params = proposal.parameters
    list_name: str = params.get("list_name", "Artha")
    title: str = params.get("title", "")
    dispatch_started = False

    try:
        token = _get_token()
        list_id = _resolve_list_id(token, list_name)

        task_body = _build_task_body(params)
        url = f"{_GRAPH_TODO_BASE}/{list_id}/tasks"
        dispatch_started = True
        created = _graph_request(url, method="POST", body=task_body, access_token=token)

        task_id = created.get("id", "")
        if not task_id:
            raise RuntimeError("provider did not return a verifiable task receipt")

        return ActionResult(
            status="success",
            message=f"✅ Reminder created: '{title}' in '{list_name}'",
            data={
                "task_id": task_id,
                "list_id": list_id,
                "list_name": list_name,
                "title": title,
                "due_date": params.get("due_date", ""),
            },
            reversible=False,  # deletion is trivial; undo not implemented
            reverse_action=None,
        )

    except Exception as e:
        rejected = isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500
        uncertain = dispatch_started and not rejected
        return ActionResult(
            status="unknown" if uncertain else "failure",
            message=("Reminder outcome is unknown; verify with the provider before retrying."
                     if uncertain else f"Failed to create To Do task: {e}"),
            data={"error": str(e), "title": title, "list_name": list_name},
            reversible=False,
            reverse_action=None,
        )


def reconcile(proposal: ActionProposal, *, since: str) -> ActionResult:
    """Look for the task an ``unknown`` attempt may have created (re-artha RA-05).

    The provider has no idempotency key for tasks, so only *presence* is conclusive: exactly one task with the exact
    title, created at or after the approval, confirms the effect.  Anything else (none, several, or a lookup that
    fails) stays ``unknown`` for a person to resolve.  Absence never proves that nothing happened: the task may have
    been created and then deleted.
    """
    params = proposal.parameters
    title = str(params.get("title", ""))
    list_name = str(params.get("list_name", "Artha"))
    try:
        token = _get_token()
        list_id = _resolve_list_id(token, list_name)
        escaped = title.replace("'", "''")
        found = _graph_request(f"{_GRAPH_TODO_BASE}/{list_id}/tasks?$filter=title eq '{escaped}'&$top=10",
                               access_token=token)
        tasks = [t for t in found.get("value", []) if str(t.get("createdDateTime", "")) >= since]
    except Exception as exc:  # noqa: BLE001 — a failed lookup is not evidence either way
        return ActionResult(status="unknown", message=f"Could not check the provider: {type(exc).__name__}",
                            data={"title": title}, reversible=False, reverse_action=None)
    if len(tasks) == 1 and tasks[0].get("id"):
        return ActionResult(status="success", message=f"Confirmed with the provider: '{title}' exists",
                            data={"task_id": tasks[0]["id"], "list_id": list_id, "list_name": list_name, "title": title,
                                  "reconciled": True}, reversible=False, reverse_action=None)
    reason = "several matching tasks" if tasks else "no matching task found"
    return ActionResult(status="unknown", message=f"Still unknown ({reason}); a person must confirm.",
                        data={"title": title, "candidates": len(tasks)}, reversible=False, reverse_action=None)


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """build_reverse_proposal is not supported for reminder_create (not reversible)."""
    raise NotImplementedError("reminder_create does not support undo")


def health_check() -> bool:
    """Verify MS Graph credentials are available."""
    try:
        token = _get_token()
        # Ping the To Do lists endpoint
        _graph_request(_GRAPH_TODO_BASE, access_token=token)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_token() -> str:
    """A *refreshed* MS Graph access token.

    The previous implementation called a function that does not exist and then fell back to a stale token file, which
    produced HTTP 401 at execution time.  ``load_msgraph_token`` refreshes through ``ensure_valid_token``; if that
    fails the action fails before dispatch with a message that says how to fix it.
    """
    from lib.auth import load_msgraph_token  # noqa: PLC0415
    token = load_msgraph_token().get("access_token", "")
    if not token:
        raise RuntimeError("MS Graph token not found. Run: python scripts/setup_msgraph_oauth.py")
    return str(token)


def _graph_request(
    url: str,
    method: str = "GET",
    body: Optional[dict] = None,
    access_token: str = "",
) -> dict:
    """Make an authenticated Graph API request. Returns parsed JSON dict."""
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _resolve_list_id(token: str, list_name: str) -> str:
    """Require one existing list. Creating a list is a separate, unapproved effect."""
    lists_resp = _graph_request(_GRAPH_TODO_BASE, access_token=token)
    matches = [lst["id"] for lst in lists_resp.get("value", [])
               if lst.get("displayName", "").casefold() == list_name.casefold()]
    if len(matches) != 1:
        raise ValueError("Choose one existing, unambiguous To Do list before approving this reminder")
    return str(matches[0])


def _build_task_body(params: dict[str, Any]) -> dict[str, Any]:
    """Construct the Graph API task resource from action params."""
    title: str = params.get("title", "")
    body_text: str = params.get("body", "")
    due_date: str = params.get("due_date", "")
    remind_dt: str = params.get("reminder_datetime", "")
    priority_raw: str = params.get("priority", "P1")

    importance = _IMPORTANCE_MAP.get(priority_raw, "normal")

    task: dict[str, Any] = {
        "title": title,
        "importance": importance,
        "status": "notStarted",
    }

    if body_text:
        task["body"] = {"content": body_text, "contentType": "text"}

    if due_date:
        # Graph API expects dueDateTime as { dateTime, timeZone }
        task["dueDateTime"] = {"dateTime": f"{due_date}T00:00:00", "timeZone": "UTC"}

    if remind_dt:
        task["reminderDateTime"] = {"dateTime": remind_dt, "timeZone": "UTC"}
        task["isReminderOn"] = True

    return task
