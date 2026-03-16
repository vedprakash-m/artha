"""
scripts/connectors/google_email.py — Gmail connector handler (standalone).

Fetches Gmail messages via the Google API. All MIME parsing and API logic
is self-contained — no dependency on the legacy gmail_fetch.py script.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import base64
import sys
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# MIME helpers (moved from gmail_fetch.py)
# ---------------------------------------------------------------------------

def _decode_b64(data: str) -> str:
    """Decode URL-safe base64 from Gmail API payload."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _get_header(headers: list, name: str) -> str:
    """Extract a header value by name (case-insensitive)."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _extract_body(payload: dict) -> str:
    """Recursively extract best plain-text body from a Gmail message payload."""
    from lib.html_processing import strip_html  # type: ignore[import]
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")
    if mime_type == "text/plain" and body_data:
        return _decode_b64(body_data)
    if mime_type == "text/html" and body_data:
        return strip_html(_decode_b64(body_data))
    if mime_type.startswith("multipart/"):
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain":
                d = part.get("body", {}).get("data", "")
                if d:
                    return _decode_b64(d)
        for part in parts:
            if part.get("mimeType") == "text/html":
                d = part.get("body", {}).get("data", "")
                if d:
                    return strip_html(_decode_b64(d))
        for part in parts:
            result = _extract_body(part)
            if result:
                return result
    return ""


# Body trimming delegated to shared lib
from lib.html_processing import trim_body as _trim_body  # noqa: E402


def _parse_message(msg: dict) -> dict:
    """Convert a raw Gmail API message object into a clean JSONL record."""
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    subject = _get_header(headers, "Subject") or "(no subject)"
    sender = _get_header(headers, "From")
    to = _get_header(headers, "To")
    date_str = _get_header(headers, "Date")
    date_iso = date_str
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        date_iso = dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    body = _trim_body(_extract_body(payload))
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "subject": subject,
        "from": sender,
        "to": to,
        "date": date_str,
        "date_iso": date_iso,
        "body": body,
        "snippet": msg.get("snippet", ""),
        "labels": msg.get("labelIds", []),
    }


def _since_to_query(since_iso: str) -> str:
    """Convert ISO 8601 timestamp to Gmail 'after:EPOCH' query fragment."""
    try:
        dt = datetime.fromisoformat(since_iso)
        if dt.tzinfo is None:
            import zoneinfo
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
        return f"after:{int(dt.timestamp())}"
    except Exception:
        import time
        return f"after:{int(time.time()) - 86400}"


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any],
    source_tag: str = "",
    before: str = "",
    label_filter: str = "",
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Gmail messages since *since* timestamp."""
    from google_auth import build_service  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]

    service = build_service("gmail", "v1")
    query = _since_to_query(since)
    if before:
        try:
            dt = datetime.fromisoformat(before)
            if dt.tzinfo is None:
                import zoneinfo
                dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
            query += f" before:{int(dt.timestamp())}"
        except Exception:
            pass
    if label_filter:
        query += f" label:{label_filter}"
    print(f"[google_email] query='{query}' max={max_results}", file=sys.stderr)

    all_refs: list[dict] = []
    page_token: Optional[str] = None
    while len(all_refs) < max_results:
        batch = min(max_results - len(all_refs), 100)
        kw: dict = {"userId": "me", "q": query, "maxResults": batch,
                    "includeSpamTrash": False}
        if page_token:
            kw["pageToken"] = page_token
        resp = with_retry(
            lambda k=kw: service.users().messages().list(**k).execute(),
            context="gmail.list",
        )
        all_refs.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    for ref in all_refs[:max_results]:
        try:
            msg = with_retry(
                lambda mid=ref["id"]: service.users().messages().get(
                    userId="me", id=mid, format="full"
                ).execute(),
                context=f"gmail.get.{ref['id'][:8]}",
            )
            record = _parse_message(msg)
            if source_tag:
                record["source"] = source_tag
            yield record
        except Exception as exc:
            print(f"[google_email] skipping {ref.get('id', '?')}: {exc}", file=sys.stderr)


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify Gmail auth and connectivity."""
    try:
        from google_auth import check_stored_credentials  # type: ignore[import]
        status = check_stored_credentials()
        return status.get("client_id_stored", False) and status.get("gmail_token_stored", False)
    except Exception as exc:
        print(f"[google_email] health_check failed: {exc}", file=sys.stderr)
        return False
