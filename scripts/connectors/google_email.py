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
import math
import sys
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_ADMISSION_MAX_PAGES = 20
_ADMISSION_MAX_RAW_BYTES = 16 * 1024 * 1024
GMAIL_REQUEST_TIMEOUT_SECONDS = 15.0
GMAIL_ADMISSION_TIMEOUT_SECONDS = 90.0
_REQUEST_WORKER_FLAG = "--bounded-request-worker"
_REQUEST_OUTPUT_LIMIT = 32 * 1024 * 1024


@dataclass(frozen=True)
class MessageIdListing:
    message_ids: list[str]
    cursor: str | None
    complete: bool
    truncation_reason: str | None = None

    def __iter__(self):
        yield self.message_ids
        yield self.cursor


def _admission_deadline(deadline: float | None) -> float:
    if deadline is None:
        return time.monotonic() + GMAIL_ADMISSION_TIMEOUT_SECONDS
    if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)):
        raise ValueError("Gmail admission deadline must be a monotonic timestamp")
    return float(deadline)


def _set_request_timeout(request: Any, timeout: float) -> None:
    transport = getattr(request, "http", None)
    if transport is None:
        return
    for candidate in (transport, getattr(transport, "http", None)):
        if candidate is not None:
            try:
                setattr(candidate, "timeout", timeout)
            except (AttributeError, TypeError):
                pass


def _request_worker_command() -> list[str]:
    return [sys.executable, os.path.abspath(__file__), _REQUEST_WORKER_FLAG]


def _provider_request(service: Any, operation: str, arguments: dict[str, Any]) -> Any:
    if operation == "getProfile":
        return service.users().getProfile(**arguments)
    if operation == "messages.list":
        return service.users().messages().list(**arguments)
    if operation == "messages.get":
        return service.users().messages().get(**arguments)
    if operation == "history.list":
        return service.users().history().list(**arguments)
    raise ValueError("Gmail request operation is not supported")


def _injected_service() -> Any | None:
    """Return a synthetic preloaded provider service for isolated unit tests."""
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return None
    module = sys.modules.get("google_auth")
    factory = getattr(module, "build_service", None)
    if (module is None or not callable(factory) or getattr(module, "__file__", None)
            or getattr(factory, "__module__", None) == "google_auth"):
        return None
    return factory("gmail", "v1")


class _GmailProviderError(Exception):
    def __init__(self, status: int | None) -> None:
        super().__init__("Gmail provider request failed")
        if status is not None:
            self.resp = SimpleNamespace(status=status)


def _execute_request(
        operation: str, arguments: dict[str, Any], *, deadline: float,
        context: str) -> Any:
    if not isinstance(operation, str) or not isinstance(arguments, dict):
        raise ValueError("Gmail request description is invalid")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Gmail admission deadline exhausted")
    timeout = min(GMAIL_REQUEST_TIMEOUT_SECONDS, remaining)
    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError("Gmail admission request timeout is unavailable")
    service = _injected_service()
    if service is not None:
        request = _provider_request(service, operation, arguments)
        _set_request_timeout(request, timeout)
        return request.execute()

    from lib.bounded_process import run_bounded  # type: ignore[import]
    request_data = json.dumps(
        {"operation": operation, "arguments": arguments},
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    try:
        completed = run_bounded(
            _request_worker_command(),
            timeout=timeout,
            cleanup_grace=1.0,
            input=request_data,
            stdout_limit=_REQUEST_OUTPUT_LIMIT,
            stderr_limit=128 * 1024,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Gmail admission request timed out: {context}") from exc
    if completed.returncode != 0 or completed.stdout_truncated or completed.stderr_truncated:
        raise RuntimeError("Gmail admission request worker failed")
    try:
        response = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Gmail admission request worker returned an invalid response") from exc
    if not isinstance(response, dict) or set(response) not in ({"ok", "result"}, {"ok", "error"}):
        raise RuntimeError("Gmail admission request worker returned an invalid envelope")
    if response["ok"] is True and set(response) == {"ok", "result"}:
        return response["result"]
    error = response["error"] if set(response) == {"ok", "error"} else None
    if response["ok"] is not False or not isinstance(error, dict):
        raise RuntimeError("Gmail admission request worker returned an invalid envelope")
    status = error.get("status")
    if isinstance(status, bool) or (status is not None and not isinstance(status, int)):
        raise RuntimeError("Gmail admission request worker returned an invalid status")
    raise _GmailProviderError(status)


def _request_with_retry(
        operation: str, arguments: dict[str, Any], *,
        deadline: float, context: str) -> Any:
    from lib.retry import with_retry  # type: ignore[import]
    return with_retry(
        lambda: _execute_request(
            operation, arguments, deadline=deadline, context=context),
        context=context, deadline=deadline)


def _request_worker_main() -> int:
    try:
        raw = sys.stdin.buffer.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("Gmail request description is too large")
        envelope = json.loads(raw.decode("utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {"operation", "arguments"}:
            raise ValueError("Gmail request description is invalid")
        operation = envelope["operation"]
        arguments = envelope["arguments"]
        if not isinstance(operation, str) or not isinstance(arguments, dict):
            raise ValueError("Gmail request description is invalid")
        from google_auth import build_service  # type: ignore[import]
        service = build_service("gmail", "v1")
        request = _provider_request(service, operation, arguments)
        _set_request_timeout(request, GMAIL_REQUEST_TIMEOUT_SECONDS)
        result = request.execute()
        response = {"ok": True, "result": result}
    except BaseException as exc:
        response = {"ok": False, "error": {"status": _http_status(exc)}}
    encoded = json.dumps(
        response, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


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


def account_identity(
        auth_context: Dict[str, Any], *, deadline: float | None = None) -> str:
    """Return the authenticated mailbox identity without reading message content."""
    del auth_context  # credentials are owned by the existing Google auth adapter
    deadline = _admission_deadline(deadline)
    profile = _request_with_retry(
        "getProfile", {"userId": "me"},
        deadline=deadline, context="gmail.admission.identity")
    identity = profile.get("emailAddress")
    if not isinstance(identity, str) or "@" not in identity:
        raise ValueError("Gmail profile did not provide an authenticated mailbox identity")
    return identity.casefold()


def list_message_ids(
        auth_context: Dict[str, Any], *, query: str, since: str,
        max_results: int, deadline: float | None = None) -> MessageIdListing:
    """List IDs for an enrolled bounded query; this path never requests bodies."""
    del auth_context
    if type(max_results) is not int or not 1 <= max_results <= 1000:
        raise ValueError("Gmail max_results is outside its bound")
    deadline = _admission_deadline(deadline)
    full_query = f"{_since_to_query(since)} ({query})"
    ids_: list[str] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    truncation_reason: str | None = None
    pages = 0
    while pages < _ADMISSION_MAX_PAGES:
        arguments: dict[str, Any] = {
            "userId": "me", "q": full_query, "maxResults": min(100, max_results - len(ids_)),
            "includeSpamTrash": False,
        }
        if token:
            arguments["pageToken"] = token
        response = _request_with_retry(
            "messages.list", arguments,
            deadline=deadline, context="gmail.admission.list")
        pages += 1
        messages = response.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError("Gmail message listing must be a list")
        for item in messages:
            message_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(message_id, str) and message_id and message_id not in ids_:
                ids_.append(message_id)
        next_token = response.get("nextPageToken")
        if not next_token:
            token = None
            break
        if not isinstance(next_token, str) or len(next_token) > 4096 \
                or next_token in seen_tokens:
            raise ValueError("Gmail continuation token is invalid")
        seen_tokens.add(next_token)
        token = next_token
        if len(ids_) >= max_results:
            truncation_reason = "max_results"
            break
        if pages >= _ADMISSION_MAX_PAGES:
            truncation_reason = "page_limit"
    if pages >= _ADMISSION_MAX_PAGES and token and truncation_reason is None:
        truncation_reason = "page_limit"
    if len(ids_) > max_results and truncation_reason is None:
        truncation_reason = "max_results"
    complete = truncation_reason is None
    if not complete:
        from lib.connector_coverage import report_scope  # type: ignore[import]
        report_scope("gmail:admission-list", "truncated", records=len(ids_),
                     reason=truncation_reason or "listing incomplete")
    cursor = None
    if complete:
        try:
            profile = _request_with_retry(
                "getProfile", {"userId": "me"},
                deadline=deadline, context="gmail.admission.profile")
            history_id = profile.get("historyId")
            if isinstance(history_id, str) and history_id.isdigit():
                cursor = history_id
        except Exception:
            cursor = None
    return MessageIdListing(ids_[:max_results], cursor, complete, truncation_reason)


def message_metadata(
        auth_context: Dict[str, Any], message_id: str, *,
        deadline: float | None = None) -> dict[str, Any]:
    """Fetch only the immutable identity/classification metadata needed before raw bytes."""
    del auth_context
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", message_id):
        raise ValueError("Gmail message ID is invalid")
    deadline = _admission_deadline(deadline)
    message = _request_with_retry(
        "messages.get", {
            "userId": "me", "id": message_id, "format": "metadata",
            "metadataHeaders": ["From", "To", "Cc", "Bcc", "Delivered-To", "Date"],
        },
        context=f"gmail.admission.metadata.{message_id[:8]}", deadline=deadline)
    payload = message.get("payload") or {}
    headers = payload.get("headers") if isinstance(payload, dict) else []
    headers = headers if isinstance(headers, list) else []
    labels = message.get("labelIds")
    if message.get("id") != message_id:
        raise ValueError("Gmail metadata response identity changed")
    size = int(message.get("sizeEstimate") or 0)
    if size < 0:
        raise ValueError("Gmail message size is outside its bound")
    return {
        "id": str(message.get("id") or ""),
        "thread_id": str(message.get("threadId") or ""),
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "cc": _get_header(headers, "Cc"),
        "bcc": _get_header(headers, "Bcc"),
        "delivered_to": _get_header(headers, "Delivered-To"),
        "date": _get_header(headers, "Date"),
        "labels": list(labels) if isinstance(labels, list) else [],
        "size": size,
        "history_id": str(message.get("historyId") or ""),
    }


def raw_message(
        auth_context: Dict[str, Any], message_id: str, *,
        deadline: float | None = None) -> bytes:
    """Fetch raw MIME only after metadata classification has succeeded."""
    del auth_context
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", message_id):
        raise ValueError("Gmail message ID is invalid")
    deadline = _admission_deadline(deadline)
    message = _request_with_retry(
        "messages.get", {"userId": "me", "id": message_id, "format": "raw"},
        context=f"gmail.admission.raw.{message_id[:8]}", deadline=deadline)
    raw = message.get("raw")
    if message.get("id") != message_id:
        raise ValueError("Gmail raw response identity changed")
    if not isinstance(raw, str):
        raise ValueError("Gmail raw message is missing")
    if len(raw) > 4 * ((_ADMISSION_MAX_RAW_BYTES + 2) // 3):
        raise ValueError("Gmail raw message is outside its bound")
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(decoded) > _ADMISSION_MAX_RAW_BYTES:
        raise ValueError("Gmail raw message is outside its bound")
    return decoded


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


def _fetch_history(cursor: str, max_results: int, source_tag: str,
                   deadline: float) -> Iterator[Dict[str, Any]]:
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
            resp = _request_with_retry(
                "history.list", {
                    "userId": "me", "startHistoryId": start,
                    "historyTypes": ["messageAdded"], "maxResults": 500, **extra,
                },
                context="gmail.history", deadline=deadline)
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
            msg = _request_with_retry(
                "messages.get", {
                    "userId": "me", "id": message_id, "format": "full",
                },
                context=f"gmail.get.{message_id[:8]}", deadline=deadline)
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
    from lib.connector_coverage import report_cursor, report_scope

    if type(sync_changes) is not bool:
        raise ValueError("sync_changes must be a boolean")
    if sync_cursor is not None and (not sync_changes or not isinstance(sync_cursor, str) or not sync_cursor.strip()):
        raise ValueError("sync_cursor must be a native cursor for a sync source")
    # history.list cannot express a query, a time bound or a label name: such a source stays windowed.
    use_history = sync_changes and not (query or before or label_filter)
    deadline = _admission_deadline(None)
    if use_history and sync_cursor is not None:
        yield from _fetch_history(sync_cursor, max_results, source_tag, deadline)
        return
    baseline: Any = None
    if use_history:
        try:     # taken BEFORE listing, so mail arriving during the listing is read by the next history call
            baseline = _request_with_retry(
                "getProfile", {"userId": "me"},
                context="gmail.profile", deadline=deadline).get("historyId")
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
            resp = _request_with_retry(
                "messages.list", kw,
                context="gmail.list", deadline=deadline)
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
            msg = _request_with_retry(
                "messages.get", {
                    "userId": "me", "id": ref["id"], "format": "full",
                },
                context=f"gmail.get.{ref['id'][:8]}", deadline=deadline)
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


if __name__ == "__main__" and sys.argv[1:] == [_REQUEST_WORKER_FLAG]:
    raise SystemExit(_request_worker_main())
