"""
scripts/connectors/msgraph_email.py — Microsoft Graph email connector (standalone).

Fetches Outlook/M365 email via MS Graph API and yields standardized dicts.
All parsing logic is self-contained — no dependency on legacy msgraph_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import re
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterator, Optional

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
from scripts.lib.html_processing import trim_body as _trim_body  # noqa: E402


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

def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any],
    source_tag: str = "outlook",
    before: str = "",
    folder: str = "inbox",
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield Outlook/M365 emails since *since* timestamp."""
    from lib.msgraph import _graph_get, _graph_get_full_url  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]

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

    params = {
        "$filter": odata_filter,
        "$select": _MSG_SELECT,
        "$top": min(_PAGE_SIZE, max_results),
        "$orderby": "receivedDateTime asc",
    }
    url = f"/me/mailFolders/{folder_canonical}/messages"
    all_msgs: list[dict] = []
    next_url: Optional[str] = None

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
            break

        page = resp.get("value", [])
        remaining = max_results - len(all_msgs)
        all_msgs.extend(page[:remaining])
        next_url = resp.get("@odata.nextLink")
        if not next_url or not page:
            break

    for msg in all_msgs:
        record = _parse_message(msg, folder=folder_canonical)
        if source_tag:
            record["source"] = source_tag
        yield record


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
