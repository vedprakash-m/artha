#!/usr/bin/env python3
# pii-guard: task content is standard sensitivity; no financial/medical data
"""
scripts/connectors/todoist.py — Todoist REST API v2 connector for Artha.

Read-only ingestion of Todoist tasks for open item tracking and goal intelligence.
Supports single-writer sync: Artha creates tasks in Todoist (write-once);
Todoist completions sync back to Artha.

ConnectorHandler protocol: module-level fetch() + health_check() functions.
Dependencies: stdlib only (urllib.request, json, time).

Ref: specs/connect.md §6.1
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_API_BASE = "https://api.todoist.com/rest/v2"
_SYNC_BASE = "https://api.todoist.com/sync/v9"
_REQUEST_TIMEOUT = 20
_RETRY_AFTER_DEFAULT = 5.0

# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _todoist_get(
    token: str,
    endpoint: str,
    params: dict[str, str] | None = None,
    base: str = _API_BASE,
    timeout: float = _REQUEST_TIMEOUT,
) -> Any:
    """GET request to Todoist API with 429 retry-after handling."""
    url = f"{base}/{endpoint.lstrip('/')}"
    if params:
        from urllib.parse import urlencode  # noqa: PLC0415
        url = f"{url}?{urlencode(params)}"

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    retries = 3
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = float(exc.headers.get("Retry-After", _RETRY_AFTER_DEFAULT))
                if attempt < retries - 1:
                    time.sleep(retry_after)
                    continue
            raise
    return None  # unreachable — raise above exits


def _load_token(auth_context: dict, credential_key: str = "artha-todoist-token") -> str | None:
    """Load Todoist API token from auth_context → keyring → env."""
    token = (auth_context or {}).get("token")
    if token:
        return token
    cred_key = (auth_context or {}).get("credential_key", credential_key)
    try:
        import keyring  # noqa: PLC0415
        token = keyring.get_password("artha", cred_key)
        if token:
            return token
    except Exception:
        pass
    import os  # noqa: PLC0415
    return os.environ.get("ARTHA_TODOIST_TOKEN")


# ---------------------------------------------------------------------------
# Project name resolution (cached per fetch() call)
# ---------------------------------------------------------------------------

def _resolve_projects(token: str) -> dict[str, str]:
    """Return {project_id: project_name} mapping."""
    try:
        projects = _todoist_get(token, "projects") or []
        return {p["id"]: p["name"] for p in projects}
    except Exception:
        return {}


def _resolve_sections(token: str) -> dict[str, str]:
    """Return {section_id: section_name} mapping."""
    try:
        sections = _todoist_get(token, "sections") or []
        return {s["id"]: s["name"] for s in sections}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Task record builder
# ---------------------------------------------------------------------------

def _build_record(
    task: dict,
    project_cache: dict[str, str],
    section_cache: dict[str, str],
    source_tag: str,
    is_completed: bool = False,
    completed_at: str = "",
) -> dict:
    """Convert a Todoist task dict to Artha connector record."""
    project_id = task.get("project_id", "")
    section_id = task.get("section_id", "")
    due = task.get("due") or {}
    return {
        "id": task.get("id", ""),
        "content": task.get("content", ""),
        "description": task.get("description", ""),
        "project_name": project_cache.get(project_id, project_id),
        "labels": task.get("labels", []),
        "priority": task.get("priority", 1),
        "due_date": due.get("date", "") if isinstance(due, dict) else "",
        "due_string": due.get("string", "") if isinstance(due, dict) else "",
        "is_completed": is_completed,
        "completed_at": completed_at,
        "created_at": task.get("created_at", ""),
        "source": source_tag,
        "section_name": section_cache.get(section_id, ""),
        "parent_id": task.get("parent_id", ""),
        "url": task.get("url", ""),
        # Artha pipeline compatibility
        "title": task.get("content", ""),
        "body": task.get("description", ""),
        "date_iso": task.get("created_at", ""),
    }


# ---------------------------------------------------------------------------
# Since / lookback parsing
# ---------------------------------------------------------------------------

def _since_to_rfc3339(since: str | None) -> str | None:
    """Convert since string ('Nd', 'Nh', 'Nm', ISO) to RFC 3339 for Todoist API."""
    if not since:
        return None
    import re  # noqa: PLC0415
    m = re.match(r"^(\d+)([dhm])$", since.strip(), re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        from datetime import timedelta  # noqa: PLC0415
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
        dt = datetime.now(timezone.utc) - delta
    else:
        try:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Public ConnectorHandler interface
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str | None = None,
    max_results: int = 500,
    auth_context: dict | None = None,
    source_tag: str = "todoist",
    project_filter: list[str] | None = None,
    sync_completed: bool = True,
    credential_key: str = "artha-todoist-token",
    **kwargs: Any,
) -> Iterator[dict]:
    """Fetch Todoist tasks and yield Artha connector records.

    Args:
        since: Lookback window ("7d", "24h", ISO datetime). Applies to
            completed tasks only; active tasks are always fetched.
        max_results: Maximum total items to yield.
        auth_context: Dict with optional 'token' or 'credential_key'.
        source_tag: Source identifier injected into each record.
        project_filter: Optional list of project names to fetch from.
            None = all projects.
        sync_completed: If True, also fetch recently completed tasks
            (feeds goal tracking).
        credential_key: keyring credential key for the Todoist API token.
    """
    token = _load_token(auth_context or {}, credential_key)
    if not token:
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).warning(
            "todoist: no token configured — skipping (run scripts/setup_todoist.py)"
        )
        return

    project_cache = _resolve_projects(token)
    section_cache = _resolve_sections(token)

    # Apply project filter if specified
    allowed_project_ids: set[str] | None = None
    if project_filter:
        pf_lower = {p.lower() for p in project_filter}
        allowed_project_ids = {
            pid for pid, name in project_cache.items()
            if name.lower() in pf_lower
        }

    yielded = 0

    # --- Active tasks ---
    try:
        params: dict[str, str] = {}
        tasks = _todoist_get(token, "tasks", params) or []
    except Exception as exc:
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).error("todoist: fetch active tasks failed: %s", exc)
        tasks = []

    for task in tasks:
        if yielded >= max_results:
            return
        if allowed_project_ids is not None and task.get("project_id") not in allowed_project_ids:
            continue
        yield _build_record(task, project_cache, section_cache, source_tag)
        yielded += 1

    # --- Completed tasks (for goal tracking) ---
    if not sync_completed or yielded >= max_results:
        return

    since_dt = _since_to_rfc3339(since)
    params_c: dict[str, str] = {"limit": str(min(200, max_results - yielded))}
    if since_dt:
        params_c["since"] = since_dt

    try:
        completed_data = _todoist_get(
            token, "tasks/completed/get_all", params_c, base=_SYNC_BASE
        ) or {}
        completed_items = completed_data.get("items", [])
    except Exception as exc:
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).warning("todoist: completed tasks fetch failed: %s", exc)
        completed_items = []

    for item in completed_items:
        if yielded >= max_results:
            return
        task_dict = item.get("task_data") or item
        if allowed_project_ids is not None and task_dict.get("project_id") not in allowed_project_ids:
            continue
        yield _build_record(
            task_dict,
            project_cache,
            section_cache,
            source_tag,
            is_completed=True,
            completed_at=item.get("completed_at", ""),
        )
        yielded += 1


def health_check(auth_context: dict | None = None) -> bool:
    """Verify Todoist API token is valid. Returns True if connection successful."""
    token = _load_token(auth_context or {})
    if not token:
        return False
    try:
        result = _todoist_get(token, "projects")
        return isinstance(result, list)
    except Exception:
        return False
