"""
scripts/connectors/ado_workitems.py — Azure DevOps work item connector.

Fetches work items assigned to the current user from Azure DevOps using
WIQL (Work Item Query Language) + batch item fetch via ADO REST API.

Auth waterfall:
  1. Azure CLI bearer token (primary — no pat expiry, no manual rotation)
     az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798
  2. PAT from keyring (fallback — key: "artha-ado-pat")
  3. Both fail → raise RuntimeError with actionable message

Handler contract: implements fetch() and health_check() per connectors/base.py.

Runtime config (read from user_profile.yaml):
  integrations.azure_devops.organization_url  — e.g. "https://dev.azure.com/myorg"
  integrations.azure_devops.project           — e.g. "MyProject"
  integrations.azure_devops.auth_method       — "az_cli" (default) or "pat"

Ref: specs/work-domain-assessment.md §18.2
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests  # type: ignore[import]

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.connector_coverage import report_scope

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"
ADO_API_VERSION = "7.1"
MAX_BATCH_SIZE = 200
KEYRING_KEY_PAT = "artha-ado-pat"
KEYRING_SERVICE = "artha"
REQUEST_TIMEOUT = 20  # seconds per individual HTTP request

# Fields extracted from every work item
_WORK_ITEM_FIELDS = [
    "System.Id",
    "System.Title",
    "System.State",
    "System.WorkItemType",
    "Microsoft.VSTS.Common.Priority",
    "Microsoft.VSTS.Scheduling.TargetDate",
    "System.ChangedDate",
    "System.IterationPath",
    "System.AreaPath",
    "System.AssignedTo",
    "One_custom.PM_Owner_Identity",
]

# Human-readable output field names (maps API field → record key)
_FIELD_MAP = {
    "System.Id":                              "id",
    "System.Title":                           "title",
    "System.State":                           "state",
    "System.WorkItemType":                    "type",
    "Microsoft.VSTS.Common.Priority":         "priority",
    "Microsoft.VSTS.Scheduling.TargetDate":   "target_date",
    "System.ChangedDate":                     "changed_date",
    "System.IterationPath":                   "iteration_path",
    "System.AreaPath":                        "area_path",
    "System.AssignedTo":                      "assigned_to",
    "One_custom.PM_Owner_Identity":            "pm_owner",
}


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def _profile_value(path: str, default=None):
    """Read a dot-notation path from user_profile.yaml."""
    try:
        from profile_loader import load_profile  # type: ignore[import]
        profile = load_profile() or {}
        parts = path.split(".")
        node = profile
        for p in parts:
            if not isinstance(node, dict):
                return default
            node = node.get(p, default)
            if node is None:
                return default
        return node
    except Exception:
        return default


def _ado_config() -> tuple[str, str, str]:
    """Return (organization_url, project, auth_method) from user_profile.yaml."""
    org_url = _profile_value("integrations.azure_devops.organization_url", "")
    project = _profile_value("integrations.azure_devops.project", "")
    auth_method = _profile_value("integrations.azure_devops.auth_method", "az_cli")
    return str(org_url).rstrip("/"), str(project), str(auth_method)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_headers(auth_context: dict) -> dict:
    """Build HTTP Authorization header from auth_context."""
    method = auth_context.get("method", "")
    if method == "az_cli":
        token = auth_context.get("access_token", "")
        if not token:
            raise RuntimeError("[ado_workitems] az_cli auth_context missing access_token")
        return {"Authorization": f"Bearer {token}"}
    elif method == "api_key":
        # PAT auth: encode as username:PAT base64
        import base64
        pat = auth_context.get("password", "")
        if not pat:
            raise RuntimeError("[ado_workitems] api_key auth_context missing password")
        encoded = base64.b64encode(f":{pat}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    else:
        raise RuntimeError(
            f"[ado_workitems] Unsupported auth method: {method!r}. "
            "Use az_cli or api_key (PAT)."
        )


# ---------------------------------------------------------------------------
# ADO REST API helpers
# ---------------------------------------------------------------------------

def _wiql_query(org_url: str, project: str, headers: dict, since: str = "") -> list[int]:
    """Return active assigned/PM-owned items and, when requested, recent changes.

    Convert ISO-8601 timestamps and relative offsets to WIQL's date-only syntax.
    """
    wiql_url = f"{org_url}/{project}/_apis/wit/wiql?api-version={ADO_API_VERSION}"
    active_filter = "[System.State] NOT IN ('Closed', 'Resolved', 'Done', 'Removed')"
    recent_filter = ""
    if since:
        relative = re.fullmatch(r"(\d+)([dh])", since)
        if relative:
            amount = int(relative[1])
            delta = timedelta(days=amount) if relative[2] == "d" else timedelta(hours=amount)
            since_date = (datetime.now(timezone.utc) - delta).date()
        else:
            try:
                since_date = datetime.fromisoformat(since.replace("Z", "+00:00")).date()
            except ValueError:
                raise ValueError(
                    "[ado_workitems] since must be an ISO-8601 date/timestamp or a relative offset (7d, 24h)"
                ) from None
        recent_filter = f"({active_filter} OR [System.ChangedDate] >= '{since_date.isoformat()}')"

    owner_results: list[list[int]] = []
    for owner_filter in (
        "[System.AssignedTo] = @Me",
        "[One_custom.PM_Owner_Identity] = @Me",
    ):
        where_clauses = [owner_filter, recent_filter or active_filter]
        wiql = {
            "query": (
                "SELECT [System.Id] FROM WorkItems "
                "WHERE " + " AND ".join(where_clauses) + " "
                "ORDER BY [System.ChangedDate] DESC"
            )
        }
        resp = requests.post(wiql_url, json=wiql, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("workItems"), list):
            raise ValueError("[ado_workitems] WIQL response missing a workItems list")
        owner_results.append([item["id"] for item in data["workItems"]])

    item_ids: list[int] = []
    seen_ids: set[int] = set()
    for index in range(max(map(len, owner_results), default=0)):
        for owner_ids in owner_results:
            if index < len(owner_ids) and owner_ids[index] not in seen_ids:
                seen_ids.add(owner_ids[index])
                item_ids.append(owner_ids[index])
    return item_ids


def _batch_fetch(
    org_url: str, item_ids: list[int], headers: dict
) -> list[dict]:
    """Batch-fetch work item details for the given IDs."""
    if not item_ids:
        return []

    fields_param = ",".join(_WORK_ITEM_FIELDS)
    # Process in chunks of MAX_BATCH_SIZE
    all_items: list[dict] = []
    for i in range(0, len(item_ids), MAX_BATCH_SIZE):
        chunk = item_ids[i : i + MAX_BATCH_SIZE]
        ids_param = ",".join(str(x) for x in chunk)
        url = (
            f"{org_url}/_apis/wit/workitems"
            f"?ids={ids_param}&fields={fields_param}"
            f"&api-version={ADO_API_VERSION}"
        )
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise ValueError("[ado_workitems] batch response missing a value list")
        items = data["value"]
        if len(items) != len(chunk) or {item["id"] for item in items} != set(chunk):
            raise RuntimeError("[ado_workitems] incomplete or unexpected batch response")
        all_items.extend(items)
    return all_items


def _normalise_item(raw_item: dict) -> dict:
    """Convert a raw ADO work item dict to a clean record."""
    fields = raw_item.get("fields", {})
    record: dict[str, Any] = {}
    for api_field, record_key in _FIELD_MAP.items():
        value = fields.get(api_field)
        # AssignedTo comes as a dict with a displayName
        if record_key in ("assigned_to", "pm_owner") and isinstance(value, dict):
            value = value.get("displayName", "")
        # Dates: trim to YYYY-MM-DD
        if record_key in ("target_date", "changed_date") and isinstance(value, str):
            value = value[:10] if value else ""
        record[record_key] = value
    if record["id"] is not None:
        record["id"] = str(record["id"])
    record["date_iso"] = fields.get("System.ChangedDate")
    record["source"] = "ado"
    record["_ado_url"] = raw_item.get("url", "")
    return record


# ---------------------------------------------------------------------------
# Public connector interface
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str = "",
    max_results: int = MAX_BATCH_SIZE,
    auth_context: dict,
    source_tag: str = "ado",
    **kwargs: Any,
) -> Iterator[dict]:
    """Fetch active assigned/PM-owned items and recent changes for the current user.

    Reads org_url and project from user_profile.yaml at runtime.
    """
    if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1:
        raise ValueError("[ado_workitems] max_results must be a positive integer")
    org_url, project, _ = _ado_config()

    if not org_url or not project:
        raise RuntimeError(
            "[ado_workitems] organization_url or project not configured. "
            "Run: work bootstrap"
        )

    headers = _get_headers(auth_context)

    try:
        item_ids = _wiql_query(org_url, project, headers, since=since)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 401:
            message = (
                "[ado_workitems] ADO auth error (401). "
                "Run: az login to refresh Azure CLI session."
            )
        elif status == 403:
            message = (
                "[ado_workitems] ADO permission denied (403). "
                "Ensure the account has access to the ADO project."
            )
        else:
            message = f"[ado_workitems] WIQL query failed (HTTP {status}): {exc}"
        raise RuntimeError(message) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"[ado_workitems] WIQL query error: {exc}") from exc

    if not item_ids:
        return

    # Respect max_results cap
    if len(item_ids) > max_results:
        report_scope("workitems", "truncated", records=max_results, reason="max_results reached")
    item_ids = item_ids[:max_results]

    try:
        raw_items = _batch_fetch(org_url, item_ids, headers)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        raise RuntimeError(f"[ado_workitems] batch fetch failed (HTTP {status}): {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"[ado_workitems] batch fetch error: {exc}") from exc

    for raw_item in raw_items:
        record = _normalise_item(raw_item)
        if source_tag:
            record["source"] = source_tag
        yield record


def health_check(auth_context: dict) -> bool:
    """Verify ADO connectivity and auth by fetching a single work item page via WIQL."""
    org_url, project, _ = _ado_config()

    if not org_url or not project:
        print(
            "[ado_workitems] health_check: organization_url or project not configured",
            file=sys.stderr,
        )
        return False

    try:
        headers = _get_headers(auth_context)
    except RuntimeError as exc:
        print(f"[ado_workitems] health_check auth error: {exc}", file=sys.stderr)
        return False

    wiql_url = f"{org_url}/{project}/_apis/wit/wiql?api-version={ADO_API_VERSION}"
    wiql = {"query": "SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me"}
    try:
        resp = requests.post(wiql_url, json=wiql, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return True
        print(
            f"[ado_workitems] health_check: HTTP {resp.status_code}",
            file=sys.stderr,
        )
        return False
    except requests.RequestException as exc:
        print(f"[ado_workitems] health_check error: {exc}", file=sys.stderr)
        return False
