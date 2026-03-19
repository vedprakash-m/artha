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

    try:
        token = _get_token()
        list_id = _resolve_list_id(token, list_name)

        task_body = _build_task_body(params)
        url = f"{_GRAPH_TODO_BASE}/{list_id}/tasks"
        created = _graph_request(url, method="POST", body=task_body, access_token=token)

        task_id = created.get("id", "")

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
        return ActionResult(
            status="failure",
            message=f"Failed to create To Do task: {e}",
            data={"error": str(e), "title": title, "list_name": list_name},
            reversible=False,
            reverse_action=None,
        )


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
    """Load MS Graph access token from token file."""
    try:
        from lib.auth import get_auth_token  # noqa: PLC0415
        token = get_auth_token("msgraph")
        if token:
            return token
    except Exception:
        pass

    # Fallback: read token file directly
    import json
    import os
    token_file = os.path.join(
        str(Path(__file__).resolve().parent.parent.parent),
        ".tokens", "msgraph-token.json",
    )
    if os.path.exists(token_file):
        with open(token_file) as f:
            data = json.load(f)
        return data.get("access_token", "")
    raise RuntimeError("MS Graph token not found. Run: python scripts/setup_msgraph_oauth.py")


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
    """Find the To Do list ID by display name.  Creates the list if not found."""
    lists_resp = _graph_request(_GRAPH_TODO_BASE, access_token=token)
    for lst in lists_resp.get("value", []):
        if lst.get("displayName", "").lower() == list_name.lower():
            return lst["id"]

    # Auto-create the list if it doesn't exist
    created = _graph_request(
        _GRAPH_TODO_BASE,
        method="POST",
        body={"displayName": list_name},
        access_token=token,
    )
    return created["id"]


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
