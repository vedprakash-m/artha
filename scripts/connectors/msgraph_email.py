"""
scripts/connectors/msgraph_email.py — Microsoft Graph email connector (standalone).

Fetches Outlook/M365 email via MS Graph API and yields standardized dicts.
All parsing logic is self-contained — no dependency on legacy msgraph_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import json
import re
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urlsplit

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_PAGE_SIZE = 50

_FOLDER_MAP: dict[str, str] = {
    "inbox": "inbox",
    "sent": "sentItems",
    "sentitems": "sentItems",
    "archive": "archive",
    "deleted": "deleteditems",
    "deleteditems": "deleteditems",
    "junkemail": "junkemail",
    "junk": "junkemail",
    "spam": "junkemail",
    "drafts": "drafts",
}

_MSG_SELECT = ",".join([
    "id", "conversationId", "subject", "from", "toRecipients", "ccRecipients",
    "receivedDateTime", "body", "bodyPreview", "importance",
    "isRead", "hasAttachments", "inferenceClassification",
])

# Body trimming delegated to shared lib
from lib.html_processing import trim_body as _trim_body  # noqa: E402


def _fmt_addr(addr_obj: Optional[dict]) -> str:
    if not addr_obj:
        return ""
    ea = addr_obj.get("emailAddress", addr_obj)
    name = ea.get("name", "")
    email = ea.get("address", "")
    if name and email and name != email:
        return f"{name} <{email}>"
    return email or name


def _fmt_addr_list(addr_list: Optional[list]) -> str:
    if not addr_list:
        return ""
    return ", ".join(_fmt_addr(item) for item in addr_list if item and _fmt_addr(item))


def _parse_message(msg: dict, folder: str = "inbox") -> dict:
    from lib.html_processing import strip_html  # type: ignore[import]
    subject = msg.get("subject") or "(no subject)"
    from_addr = _fmt_addr(msg.get("from"))
    to_addr = _fmt_addr_list(msg.get("toRecipients"))
    cc_addr = _fmt_addr_list(msg.get("ccRecipients"))
    received_raw = msg.get("receivedDateTime", "")
    date_iso = received_raw
    if received_raw.endswith("Z"):
        try:
            dt = datetime.fromisoformat(received_raw.rstrip("Z") + "+00:00")
            date_iso = dt.isoformat()
        except ValueError:
            pass
    body_obj = msg.get("body", {})
    content_type = (body_obj.get("contentType") or "text").lower()
    raw_content = body_obj.get("content", "")
    body_text = strip_html(raw_content) if content_type == "html" else raw_content
    body_text = _trim_body(body_text)
    snippet = (msg.get("bodyPreview") or "")[:500]
    labels = [folder]
    if not msg.get("isRead", True):
        labels.append("unread")
    if msg.get("hasAttachments", False):
        labels.append("has_attachment")
    importance = msg.get("importance", "")
    if importance and importance.lower() != "normal":
        labels.append(f"importance_{importance.lower()}")
    fc = msg.get("inferenceClassification", "")
    if fc:
        labels.append(f"focused_{fc.lower()}")
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("conversationId", ""),
        "subject": subject,
        "from": from_addr,
        "to": to_addr,
        "cc": cc_addr,
        "date": received_raw,
        "date_iso": date_iso,
        "body": body_text,
        "snippet": snippet,
        "labels": labels,
        "source": "outlook",
    }


def _since_to_filter(since_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(since_iso)
        if dt.tzinfo is None:
            import zoneinfo
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
        utc_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"receivedDateTime ge {utc_str}"
    except Exception:
        since_fallback = datetime.now(timezone.utc) - timedelta(hours=24)
        return f"receivedDateTime ge {since_fallback.strftime('%Y-%m-%dT%H:%M:%SZ')}"


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

#: The runtime hands a committed mailbox cursor to ``fetch(sync_cursor=...)`` for these sources.
SYNC_TOKEN_SCOPE = "mailbox"
MAX_PAGES = 20
_GRAPH_HOST = "graph.microsoft.com"


def _http_status(exc: BaseException) -> Optional[int]:
    current: Optional[BaseException] = exc
    for _ in range(8):
        if current is None:
            break
        status = getattr(getattr(current, "response", None), "status_code", None)
        if type(status) is int:
            return status
        current = current.__cause__ or current.__context__
    match = re.match(r"\s*(\d{3})\b", str(exc))
    return int(match.group(1)) if match else None


def _graph_link(value: Any) -> bool:
    """A provider link is followed (with a bearer token) only when it is https on the Graph API host."""
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and parts.hostname == _GRAPH_HOST and not parts.username and parts.port is None


def _cursor_text(folder: str, link: str) -> str:
    return json.dumps({"v": 1, "kind": "graph", "folder": folder, "link": link}, sort_keys=True,
                      separators=(",", ":"))


def _load_cursor(raw: str, folder: str) -> str:
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"v", "kind", "folder", "link"} or data["v"] != 1
            or data["kind"] != "graph" or data["folder"] != folder or not _graph_link(data["link"])):
        raise ValueError("mailbox cursor is invalid")
    return str(data["link"])


def _fetch_delta(access_token: str, folder: str, odata_filter: str, cursor: Optional[str], max_results: int,
                 source_tag: str) -> Iterator[Dict[str, Any]]:
    """Messages from ``messages/delta``.  A removed item is counted and ignored: it never completes anything."""
    from lib.connector_coverage import (  # type: ignore[import]  # noqa: PLC0415
        report_cursor,
        report_cursor_reset,
        report_delta,
        report_scope,
    )
    from lib.msgraph import _graph_get, _graph_get_full_url  # type: ignore[import]  # noqa: PLC0415
    from lib.retry import with_retry  # type: ignore[import]  # noqa: PLC0415

    link: Optional[str] = None
    if cursor is not None:
        try:
            link = _load_cursor(cursor, folder)
        except (ValueError, TypeError):
            report_cursor_reset()
            report_scope("mailbox", "failed", reason="stored mailbox cursor is unusable")
            return
        report_delta()
    count = pages = 0
    seen: set[str] = set()
    delta_link: Any = None
    while True:
        try:
            if link is not None:
                resp = with_retry(lambda u=link: _graph_get_full_url(access_token, u), context="msgraph_email.delta.page")
            else:
                resp = with_retry(lambda: _graph_get(access_token, f"/me/mailFolders/{folder}/messages/delta",
                                                     {"$filter": odata_filter, "$select": _MSG_SELECT}),
                                  context="msgraph_email.delta")
            values = resp.get("value")
            if not isinstance(values, list):
                raise ValueError("message page must contain a list")
        except Exception as exc:
            status = _http_status(exc)
            if status == 410:
                report_cursor_reset()
            print("[msgraph_email] WARN: delta request failed", file=sys.stderr)
            report_scope("mailbox", "failed", records=count,
                         reason=f"provider request failed ({status})" if status is not None else "provider request failed")
            return
        pages += 1
        for msg in values:
            if not isinstance(msg, dict) or not isinstance(msg.get("id"), str) or not msg["id"]:
                report_scope("mailbox", "failed", records=count, reason="message is malformed")
                return
            if msg.get("@removed") is not None:
                continue
            if count >= max_results:
                report_scope("mailbox", "truncated", records=count, reason=f"message limit {max_results} reached")
                return
            record = _parse_message(msg, folder=folder)
            if source_tag:
                record["source"] = source_tag
            count += 1
            yield record
        link, delta_link = resp.get("@odata.nextLink"), resp.get("@odata.deltaLink")
        if not link:
            break
        if not _graph_link(link) or link in seen or pages >= MAX_PAGES:
            report_scope("mailbox", "truncated", records=count, reason="message pagination is incomplete")
            return
        seen.add(link)
    if not _graph_link(delta_link):
        report_scope("mailbox", "failed", records=count, reason="complete sync cursor unavailable")
    else:
        report_scope("mailbox", "complete", records=count)
        report_cursor(_cursor_text(folder, delta_link))


def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any],
    source_tag: str = "outlook",
    before: str = "",
    folder: str = "inbox",
    sync_changes: bool = False,
    sync_cursor: Optional[str] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Outlook/M365 emails since *since* timestamp (or via ``messages/delta`` when ``sync_changes``)."""
    from lib.msgraph import _graph_get, _graph_get_full_url  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]

    if type(sync_changes) is not bool:
        raise ValueError("sync_changes must be a boolean")
    if sync_cursor is not None and (not sync_changes or not isinstance(sync_cursor, str) or not sync_cursor.strip()):
        raise ValueError("sync_cursor must be a native cursor for a sync source")
    access_token = auth_context.get("access_token", "")
    if not access_token:
        raise RuntimeError("[msgraph_email] auth_context missing access_token")

    folder_canonical = _FOLDER_MAP.get(folder.lower(), folder)
    odata_filter = _since_to_filter(since)
    if before:
        try:
            dt = datetime.fromisoformat(before)
            if dt.tzinfo is None:
                import zoneinfo
                dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
            utc_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            odata_filter += f" and receivedDateTime lt {utc_str}"
        except Exception:
            pass

    print(f"[msgraph_email] folder={folder_canonical} filter='{odata_filter}' max={max_results}",
          file=sys.stderr)
    if sync_changes and not before:     # an upper time bound cannot be expressed in a delta query: stay windowed
        yield from _fetch_delta(access_token, folder_canonical, odata_filter, sync_cursor, max_results, source_tag)
        return

    params = {
        "$filter": odata_filter,
        "$select": _MSG_SELECT,
        "$top": min(_PAGE_SIZE, max_results),
        "$orderby": "receivedDateTime asc",
    }
    url = f"/me/mailFolders/{folder_canonical}/messages"
    all_msgs: list[dict] = []
    next_url: Optional[str] = None
    failed = False
    dropped = False

    while len(all_msgs) < max_results:
        try:
            if next_url:
                resp = with_retry(
                    lambda u=next_url: _graph_get_full_url(access_token, u),
                    context="msgraph_email.list",
                )
            else:
                resp = with_retry(
                    lambda: _graph_get(access_token, url, params),
                    context="msgraph_email.list.p1",
                )
        except Exception as exc:
            print(f"[msgraph_email] fetch error: {exc}", file=sys.stderr)
            failed = True
            break

        page = resp.get("value", [])
        remaining = max_results - len(all_msgs)
        dropped = dropped or len(page) > remaining
        all_msgs.extend(page[:remaining])
        next_url = resp.get("@odata.nextLink")
        if not next_url or not page:
            break

    for msg in all_msgs:
        record = _parse_message(msg, folder=folder_canonical)
        if source_tag:
            record["source"] = source_tag
        yield record
    # A listing that stopped early is never "complete": the Coordinator must not read it as an empty mailbox.
    from lib.connector_coverage import report_scope  # type: ignore[import]  # noqa: PLC0415
    if failed:
        report_scope("mailbox", "failed", records=len(all_msgs), reason="provider request failed")
    elif dropped or next_url:
        report_scope("mailbox", "truncated", records=len(all_msgs), reason="message listing is incomplete")
    else:
        report_scope("mailbox", "complete", records=len(all_msgs))


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify MS Graph auth and connectivity."""
    try:
        from lib.msgraph import _graph_get  # type: ignore[import]
        access_token = auth_context.get("access_token", "")
        if not access_token:
            return False
        _graph_get(access_token, "/me")
        return True
    except Exception as exc:
        print(f"[msgraph_email] health_check failed: {exc}", file=sys.stderr)
        return False
