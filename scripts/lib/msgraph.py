# pii-guard: ignore-file — utility code only; no personal data
"""
scripts/lib/msgraph.py — Shared Microsoft Graph HTTP helpers.

Used by connectors/msgraph_email.py, connectors/msgraph_calendar.py,
and connectors/onenote.py to avoid duplicating the same Graph API
boilerplate across three handler modules.

Security:
  - All tokens come from auth_context["access_token"] injected by lib/auth.py
  - No token caching here; each connector call uses the latest token
  - 429 / 5xx responses raise exceptions so lib/retry can back off

Ref: supercharge-reloaded.md §1.4, §3.2
"""
from __future__ import annotations

from typing import Optional

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _get_headers(access_token: str) -> dict:
    """Standard authorization headers for all Graph API calls."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def _graph_get(
    access_token: str,
    path: str,
    params: Optional[dict] = None,
    *,
    prefer_utc: bool = False,
) -> dict:
    """GET {GRAPH_BASE}{path} with bearer auth. Returns parsed JSON dict.

    Args:
        access_token: MS Graph OAuth2 access token.
        path:         API path starting with "/" (e.g. "/me/messages").
        params:       Optional OData query parameters.
        prefer_utc:   If True, adds Prefer: outlook.timezone="UTC" header
                      (needed for calendar event datetime normalisation).
    Raises:
        Exception on 4xx / 5xx with actionable message (caller may retry).
    """
    try:
        import requests as _req
    except ImportError as exc:
        raise RuntimeError(
            "[msgraph] 'requests' package not installed — run: pip install requests"
        ) from exc

    headers = _get_headers(access_token)
    if prefer_utc:
        headers["Prefer"] = 'outlook.timezone="UTC"'

    url = f"{GRAPH_BASE}{path}"
    resp = _req.get(url, headers=headers, params=params, timeout=30)

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "?")
        raise Exception(
            f"429 Too Many Requests (Retry-After: {retry_after}s): {resp.text[:200]}"
        )
    if resp.status_code >= 500:
        raise Exception(f"{resp.status_code} Server Error: {resp.text[:200]}")
    if resp.status_code == 401:
        raise Exception(
            "401 Unauthorized — token may be expired. "
            "Run: python scripts/setup_msgraph_oauth.py --reauth"
        )
    if resp.status_code == 403:
        raise Exception(
            f"403 Forbidden — required scope may be missing. Response: {resp.text[:300]}"
        )
    resp.raise_for_status()
    return resp.json()


def _graph_get_full_url(access_token: str, full_url: str) -> dict:
    """GET an arbitrary full URL (used for @odata.nextLink pagination)."""
    try:
        import requests as _req
    except ImportError as exc:
        raise RuntimeError("[msgraph] 'requests' package not installed") from exc

    resp = _req.get(full_url, headers=_get_headers(access_token), timeout=30)

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "?")
        raise Exception(f"429 Too Many Requests (Retry-After: {retry_after}s)")
    if resp.status_code >= 500:
        raise Exception(f"{resp.status_code} Server Error: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()


def _graph_get_content(access_token: str, path: str) -> str:
    """GET page content as raw HTML text (for OneNote pages)."""
    try:
        import requests as _req
    except ImportError as exc:
        raise RuntimeError("[msgraph] 'requests' package not installed") from exc

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "text/html",
    }
    resp = _req.get(f"{GRAPH_BASE}{path}", headers=headers, timeout=30)
    if resp.status_code == 403:
        raise Exception(
            "403 Forbidden — Notes.Read scope required. "
            "Run: python scripts/setup_msgraph_oauth.py --reauth"
        )
    if resp.status_code >= 400:
        raise Exception(f"{resp.status_code} Error fetching content: {resp.text[:200]}")
    return resp.text


def _graph_get_paginated(
    access_token: str,
    path: str,
    params: Optional[dict] = None,
    *,
    prefer_utc: bool = False,
) -> list:
    """Fetch all pages of a paginated Graph endpoint; follow @odata.nextLink.

    Returns flat list of all 'value' items across all pages.
    """
    results: list = []
    current_url: Optional[str] = None

    while True:
        if current_url:
            data = _graph_get_full_url(access_token, current_url)
        else:
            data = _graph_get(access_token, path, params, prefer_utc=prefer_utc)

        results.extend(data.get("value", []))

        next_link = data.get("@odata.nextLink")
        if not next_link:
            break
        current_url = next_link
        params = None  # already embedded in next_link

    return results


def get_org_chart(access_token: str) -> dict:
    """Return a compact org-chart snapshot for the signed-in user.

    Calls three confirmed-working Graph endpoints:
      GET /me/manager          → 1 record (manager)
      GET /me/directReports    → 0-N records (direct reports)
      GET /me/                 → self record for display name / title

    Returns a dict:
        {
          "self":    {"displayName": ..., "jobTitle": ..., "department": ...},
          "manager": {"displayName": ..., "jobTitle": ..., "department": ...} | None,
          "direct_reports": [ {"displayName": ..., "jobTitle": ..., "department": ...}, ... ]
        }

    If a call returns 404 or 403 the corresponding key is None / [].
    Used by work-people domain for org-hierarchy enrichment.
    """
    _PERSON_FIELDS = "$select=displayName,jobTitle,department,mail,userPrincipalName"

    def _safe_get(path: str) -> Optional[dict]:
        try:
            return _graph_get(access_token, path + "?" + _PERSON_FIELDS)
        except Exception:
            return None

    def _safe_list(path: str) -> list:
        try:
            data = _graph_get(access_token, path + "?" + _PERSON_FIELDS)
            return data.get("value", [])
        except Exception:
            return []

    def _trim(raw: Optional[dict]) -> Optional[dict]:
        if not raw:
            return None
        return {
            "displayName": raw.get("displayName", ""),
            "jobTitle": raw.get("jobTitle", ""),
            "department": raw.get("department", ""),
        }

    me_raw = _safe_get("/me")
    manager_raw = _safe_get("/me/manager")
    reports_raw = _safe_list("/me/directReports")

    return {
        "self": _trim(me_raw),
        "manager": _trim(manager_raw),
        "direct_reports": [_trim(r) for r in reports_raw if r],
    }
