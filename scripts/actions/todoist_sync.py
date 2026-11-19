#!/usr/bin/env python3
# pii-guard: task content is standard sensitivity; no financial/medical data
"""
scripts/actions/todoist_sync.py — Todoist single-writer sync action handler.

ActionHandler protocol: module-level validate(), dry_run(), execute(), health_check().

Single-writer model: Artha creates tasks in Todoist (write-once); Todoist owns
the task thereafter. Only completion status flows back via the connector.

Ref: specs/connect.md §6.1.3
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import (  # type: ignore[import]
    ActionProposal,
    ActionResult,
    is_definite_provider_refusal,
)

_API_BASE = "https://api.todoist.com/rest/v2"
_DEFAULT_CREDENTIAL_KEY = "artha-todoist-token"


class _TodoistRejected(RuntimeError):
    pass

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_token() -> str | None:
    """Load Todoist API token from keyring → env (no arguments)."""
    try:
        import keyring  # noqa: PLC0415
        token = keyring.get_password("artha", _DEFAULT_CREDENTIAL_KEY)
        if token:
            return token
    except Exception:
        pass
    import os  # noqa: PLC0415
    return os.environ.get("ARTHA_TODOIST_TOKEN")


def _create_task(
    token: str,
    content: str,
    description: str = "",
    project_id: str | None = None,
    labels: list[str] | None = None,
    priority: int = 1,
    due_string: str | None = None,
) -> dict | None:
    """Create a task via Todoist REST API v2. Returns the created task dict."""
    payload: dict[str, Any] = {
        "content": content,
        "description": description,
        "priority": priority,
    }
    if project_id:
        payload["project_id"] = project_id
    if labels:
        payload["labels"] = labels
    if due_string:
        payload["due_string"] = due_string

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_API_BASE}/tasks",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if is_definite_provider_refusal(exc):
            raise _TodoistRejected(f"Todoist refused the task (HTTP {exc.code})") from exc
        raise


def _resolve_project_id(token: str, project_name: str) -> str | None:
    """Resolve a project name to its Todoist ID."""
    req = urllib.request.Request(
        f"{_API_BASE}/projects",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            projects = json.loads(resp.read().decode())
        for p in projects:
            if p.get("name", "").lower() == project_name.lower():
                return p["id"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# ActionHandler protocol
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Validate todoist_sync proposal. Returns (ok, reason).

    Required parameters:
        content (str): Task title (non-empty, ≤500 chars)
    Optional:
        description (str): Task description
        project_name (str): Todoist project to add task to
        labels (list[str]): Labels to apply
        priority (int): 1-4 (Todoist scale)
        due_string (str): Human-readable due date
        linked_oi (str): OI-NNN reference
    """
    params = proposal.parameters or {}
    content = params.get("content", "")
    if not content or not content.strip():
        return False, "content is required and must be non-empty"
    if len(content) > 500:
        return False, f"content exceeds 500 chars (got {len(content)})"
    priority = params.get("priority", 1)
    if not isinstance(priority, int) or priority not in (1, 2, 3, 4):
        return False, f"priority must be 1–4, got {priority!r}"
    return True, "valid"


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Return a preview of what execute() would do without making any API call."""
    params = proposal.parameters or {}
    content = params.get("content", "")
    project_name = params.get("project_name", "(default project)")
    labels = params.get("labels", [])
    due_string = params.get("due_string", "")
    priority = params.get("priority", 1)
    linked_oi = params.get("linked_oi", "")

    preview_lines = [
        f"Task: {content}",
        f"Project: {project_name}",
    ]
    if due_string:
        preview_lines.append(f"Due: {due_string}")
    if labels:
        preview_lines.append(f"Labels: {', '.join(labels)}")
    if priority > 1:
        priority_labels = {2: "medium", 3: "high", 4: "urgent"}
        preview_lines.append(f"Priority: {priority_labels.get(priority, priority)}")
    if linked_oi:
        preview_lines.append(f"Artha OI: {linked_oi}")

    return ActionResult(
        status="success",
        message="[DRY RUN] Task not created — preview only",
        data={"preview": "\n".join(preview_lines), "content": content},
        reversible=False,
        reverse_action=None,
        undo_deadline=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Create the task in Todoist. Single-writer: Todoist owns task after creation."""
    token = _load_token()
    if not token:
        return ActionResult(
            status="failure",
            message="Todoist token not configured — run scripts/setup_todoist.py",
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
    content = params["content"]
    description = params.get("description", "")
    project_name = params.get("project_name")
    labels = params.get("labels", [])
    priority = params.get("priority", 1)
    due_string = params.get("due_string")
    linked_oi = params.get("linked_oi", "")

    # Append OI reference to description for reverse lookup
    if linked_oi:
        oi_ref = f"\n\n[{linked_oi}]" if description else f"[{linked_oi}]"
        description = description + oi_ref

    project_id = None
    if project_name:
        project_id = _resolve_project_id(token, project_name)

    try:
        task = _create_task(
            token,
            content=content,
            description=description,
            project_id=project_id,
            labels=labels,
            priority=priority,
            due_string=due_string,
        )
    except _TodoistRejected as exc:
        return ActionResult(
            status="failure",
            message=str(exc)[:300],
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )
    except Exception as exc:
        return ActionResult(
            status="unknown",
            message="Todoist did not return a verified task receipt; reconcile before retrying.",
            data={"exception": type(exc).__name__},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

    task_id = task.get("id") if isinstance(task, dict) else None
    if not isinstance(task_id, str) or not task_id:
        return ActionResult(
            status="unknown",
            message="Todoist returned no verified task receipt; reconcile before retrying.",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )
    task_url = task.get("url", "") if task else ""

    return ActionResult(
        status="success",
        message=f"Task created in Todoist: {content[:60]}",
        data={"task_id": task_id, "task_url": task_url, "linked_oi": linked_oi},
        reversible=False,
        reverse_action=None,
        undo_deadline=None,
    )


def health_check() -> bool:
    """Verify Todoist API token is valid. Takes NO arguments (ActionHandler protocol)."""
    token = _load_token()
    if not token:
        return False
    req = urllib.request.Request(
        f"{_API_BASE}/projects",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False
