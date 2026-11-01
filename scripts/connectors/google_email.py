"""
scripts/connectors/google_email.py — Gmail connector handler (standalone).

Fetches Gmail messages via the Google API. All MIME parsing and API logic
is self-contained — no dependency on the legacy gmail_fetch.py script.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import base64
import json
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
    body = _extract_body(payload)
    def attachments(part):
        found = [part.get("filename")] if part.get("filename") else []
        for child in part.get("parts", []):
            found.extend(attachments(child))
        return found
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
        "attachments": attachments(payload),
        "version": str(msg.get("historyId", "")),
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

#: The runtime hands a committed mailbox cursor to ``fetch(sync_cursor=...)`` for these sources.
SYNC_TOKEN_SCOPE = "mailbox"
MAX_PAGES = 20


def _http_status(exc: BaseException) -> Optional[int]:
    current: Optional[BaseException] = exc
    for _ in range(8):
        if current is None:
            break
        status = getattr(getattr(current, "resp", None), "status", None)
        if type(status) is int:
            return status
        current = current.__cause__ or current.__context__
    return None


def _failure_reason(exc: BaseException) -> str:
    status = _http_status(exc)
    return f"provider request failed ({status})" if status is not None else "provider request failed"


def _cursor_text(history_id: Any) -> str:
    return json.dumps({"v": 1, "kind": "gmail", "history_id": str(history_id)}, sort_keys=True,
                      separators=(",", ":"))


def _load_cursor(raw: str) -> str:
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"v", "kind", "history_id"} or data["v"] != 1
            or data["kind"] != "gmail" or not isinstance(data["history_id"], str)
            or not data["history_id"].isdigit()):
        raise ValueError("mailbox cursor is invalid")
    return str(data["history_id"])


def _fetch_history(service: Any, with_retry: Any, cursor: str, max_results: int,
                   source_tag: str) -> Iterator[Dict[str, Any]]:
    """New messages since a stored history id.  Deleted, trashed or relabelled mail is ignored, never a signal."""
    from lib.connector_coverage import (  # type: ignore[import]  # noqa: PLC0415
        report_cursor,
        report_cursor_reset,
        report_delta,
        report_scope,
    )
    try:
        start = _load_cursor(cursor)
    except (ValueError, TypeError):
        report_cursor_reset()
        report_scope("mailbox", "failed", reason="stored mailbox cursor is unusable")
        return
    report_delta()
    ids: list[str] = []
    page_token: Optional[str] = None
    seen_pages: set[str] = set()
    new_history: Any = None
    while True:
        extra = {"pageToken": page_token} if page_token else {}
        try:
            resp = with_retry(
                lambda e=extra: service.users().history().list(
                    userId="me", startHistoryId=start, historyTypes=["messageAdded"], maxResults=500, **e).execute(),
                context="gmail.history",
            )
            records = resp.get("history", [])
            if not isinstance(records, list):
                raise ValueError("history page must contain a list")
        except Exception as exc:
            if _http_status(exc) == 404:
                report_cursor_reset()
            print("[google_email] WARN: history request failed", file=sys.stderr)
            report_scope("mailbox", "failed", reason=_failure_reason(exc))
            return
        for entry in records:
            for added in (entry.get("messagesAdded") or []) if isinstance(entry, dict) else []:
                message_id = (added.get("message") or {}).get("id") if isinstance(added, dict) else None
                if isinstance(message_id, str) and message_id and message_id not in ids:
                    ids.append(message_id)
        new_history = resp.get("historyId")
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        if not isinstance(page_token, str) or page_token in seen_pages or len(seen_pages) >= MAX_PAGES:
            report_scope("mailbox", "truncated", reason="history pagination is incomplete")
            return
        seen_pages.add(page_token)
    if new_history in (None, "") or not str(new_history).isdigit():
        report_scope("mailbox", "failed", reason="complete sync cursor unavailable")
        return
    failures = fetched = 0
    for message_id in ids[:max_results]:
        try:
            msg = with_retry(
                lambda mid=message_id: service.users().messages().get(userId="me", id=mid, format="full").execute(),
                context=f"gmail.get.{message_id[:8]}",
            )
        except Exception as exc:
            if _http_status(exc) != 404:        # gone before we read it: deletion is not a signal
                failures += 1
            continue
        try:
            if {"TRASH", "SPAM"} & set(msg.get("labelIds") or []):
                continue
            record = _parse_message(msg)
        except Exception:
            failures += 1
            continue
        if source_tag:
            record["source"] = source_tag
        yield record
        fetched += 1
    if failures or len(ids) > max_results:
        report_scope("mailbox", "truncated", records=fetched,
                     reason=f"{failures} messages failed; {max(0, len(ids) - max_results)} not read")
    else:
        report_scope("mailbox", "complete", records=fetched)
        report_cursor(_cursor_text(new_history))


def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any],
    source_tag: str = "",
    before: str = "",
    label_filter: str = "",
    query: str = "",
    sync_changes: bool = False,
    sync_cursor: Optional[str] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Gmail messages since *since* timestamp (or since a stored history id when ``sync_changes``)."""
    from google_auth import build_service  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]
    from lib.connector_coverage import report_cursor, report_scope

    if type(sync_changes) is not bool:
        raise ValueError("sync_changes must be a boolean")
    if sync_cursor is not None and (not sync_changes or not isinstance(sync_cursor, str) or not sync_cursor.strip()):
        raise ValueError("sync_cursor must be a native cursor for a sync source")
    # history.list cannot express a query, a time bound or a label name: such a source stays windowed.
    use_history = sync_changes and not (query or before or label_filter)
    service = build_service("gmail", "v1")
    if use_history and sync_cursor is not None:
        yield from _fetch_history(service, with_retry, sync_cursor, max_results, source_tag)
        return
    baseline: Any = None
    if use_history:
        try:     # taken BEFORE listing, so mail arriving during the listing is read by the next history call
            baseline = service.users().getProfile(userId="me").execute().get("historyId")
        except Exception:
            baseline = None
    extra_query = query
    query = _since_to_query(since)
    if extra_query:
        query += f" ({extra_query})"
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
    seen_pages: set[str] = set()
    pages = 0
    listing_failed = False
    dropped = False
    while len(all_refs) < max_results:
        batch = min(max_results - len(all_refs), 100)
        kw: dict = {"userId": "me", "q": query, "maxResults": batch,
                    "includeSpamTrash": False}
        if page_token:
            kw["pageToken"] = page_token
        try:
            resp = with_retry(
                lambda k=kw: service.users().messages().list(**k).execute(),
                context="gmail.list",
            )
            refs = resp.get("messages", [])
            if not isinstance(refs, list):
                raise ValueError("message page must contain a list")
        except Exception:
            listing_failed = True
            break
        pages += 1
        remaining = max_results - len(all_refs)
        dropped = dropped or len(refs) > remaining
        all_refs.extend(refs[:remaining])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        if not isinstance(page_token, str) or page_token in seen_pages or pages >= MAX_PAGES:
            break
        seen_pages.add(page_token)

    failures = 0
    fetched = 0
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
            fetched += 1
        except Exception as exc:
            failures += 1
            print(f"[google_email] skipping {ref.get('id', '?')}: {exc}", file=sys.stderr)
    if listing_failed:
        report_scope("mailbox", "failed", records=fetched, reason="provider listing failed")
    elif failures or page_token or dropped:
        report_scope("mailbox", "truncated", records=fetched,
                     reason=f"{failures} messages failed; remaining pages={bool(page_token)}")
    else:
        report_scope("mailbox", "complete", records=fetched)
        if baseline is not None and str(baseline).isdigit():
            report_cursor(_cursor_text(baseline))


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify Gmail auth and connectivity."""
    try:
        from google_auth import check_stored_credentials  # type: ignore[import]
        status = check_stored_credentials()
        return status.get("client_id_stored", False) and status.get("gmail_token_stored", False)
    except Exception as exc:
        print(f"[google_email] health_check failed: {exc}", file=sys.stderr)
        return False
