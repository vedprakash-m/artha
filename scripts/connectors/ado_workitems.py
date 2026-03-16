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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests  # type: ignore[import]

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

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

def _wiql_query(org_url: str, project: str, headers: dict) -> list[int]:
    """Run a WIQL query and return a list of work item IDs assigned to @Me."""
    wiql_url = f"{org_url}/{project}/_apis/wit/wiql?api-version={ADO_API_VERSION}"
    wiql = {
        "query": (
            "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.AssignedTo] = @Me "
            "AND [System.State] NOT IN ('Closed', 'Resolved', 'Done', 'Removed') "
            "ORDER BY [System.ChangedDate] DESC"
        )
    }
    resp = requests.post(wiql_url, json=wiql, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return [item["id"] for item in data.get("workItems", [])]


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
        all_items.extend(resp.json().get("value", []))
    return all_items


def _normalise_item(raw_item: dict) -> dict:
    """Convert a raw ADO work item dict to a clean record."""
    fields = raw_item.get("fields", {})
    record: dict[str, Any] = {}
    for api_field, record_key in _FIELD_MAP.items():
        value = fields.get(api_field)
        # AssignedTo comes as a dict with a displayName
        if record_key == "assigned_to" and isinstance(value, dict):
            value = value.get("displayName", "")
        # Dates: trim to YYYY-MM-DD
        if record_key in ("target_date", "changed_date") and isinstance(value, str):
            value = value[:10] if value else ""
        record[record_key] = value
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
    """Fetch ADO work items assigned to the current user.

    Reads org_url and project from user_profile.yaml at runtime.
    """
    org_url, project, configured_auth = _ado_config()

    if not org_url or not project:
        print(
            "[ado_workitems] organization_url or project not configured. "
            "Run: /bootstrap work",
            file=sys.stderr,
        )
        return

    try:
        headers = _get_headers(auth_context)
    except RuntimeError as exc:
        print(f"[ado_workitems] auth failed: {exc}", file=sys.stderr)
        return

    try:
        item_ids = _wiql_query(org_url, project, headers)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 401:
            print(
                "[ado_workitems] ADO auth error (401). "
                "Run: az login  to refresh Azure CLI session.",
                file=sys.stderr,
            )
        elif status == 403:
            print(
                "[ado_workitems] ADO permission denied (403). "
                "Ensure the account has access to the ADO project.",
                file=sys.stderr,
            )
        else:
            print(f"[ado_workitems] WIQL query failed (HTTP {status}): {exc}", file=sys.stderr)
        return
    except requests.RequestException as exc:
        print(f"[ado_workitems] WIQL query error: {exc}", file=sys.stderr)
        return

    if not item_ids:
        return

    # Respect max_results cap
    item_ids = item_ids[:max_results]

    try:
        raw_items = _batch_fetch(org_url, item_ids, headers)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(
            f"[ado_workitems] batch fetch failed (HTTP {status}): {exc}",
            file=sys.stderr,
        )
        return
    except requests.RequestException as exc:
        print(f"[ado_workitems] batch fetch error: {exc}", file=sys.stderr)
        return

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
