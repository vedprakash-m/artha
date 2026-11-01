"""
scripts/connectors/imap_email.py — IMAP email connector (standalone).

Fetches email via IMAP SSL (iCloud, Fastmail, Yahoo, ProtonMail Bridge, etc.)
and yields standardized dicts. All IMAP + MIME logic is self-contained —
no dependency on the legacy icloud_mail_fetch.py script.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import email as email_lib
import email.header
import email.utils
import imaplib
import json
import re
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_MAX_BODY_CHARS = 8_000
_CHUNK_SIZE = 50

_FOLDER_MAP: dict[str, str] = {
    "inbox": "INBOX",
    "sent": "Sent Messages",
    "sentitems": "Sent Messages",
    "drafts": "Drafts",
    "trash": "Deleted Messages",
    "deleted": "Deleted Messages",
    "junk": "Junk",
    "spam": "Junk",
    "archive": "Archive",
}

# Footer markers delegated to shared lib
from lib.html_processing import SIMPLE_FOOTER_MARKERS as _FOOTER_MARKERS  # noqa: E402


# ---------------------------------------------------------------------------
# MIME / IMAP helpers
# ---------------------------------------------------------------------------

def _decode_header_value(header_value: Optional[str]) -> str:
    if not header_value:
        return ""
    parts = email.header.decode_header(header_value)
    decoded: list[str] = []
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            try:
                decoded.append(fragment.decode(charset or "utf-8", errors="replace"))
            except (LookupError, ValueError):
                decoded.append(fragment.decode("utf-8", errors="replace"))
        else:
            decoded.append(str(fragment))
    return " ".join(decoded).strip()


def _parse_address_list(header_value: Optional[str]) -> str:
    if not header_value:
        return ""
    decoded = _decode_header_value(header_value)
    addresses = email.utils.getaddresses([decoded])
    items: list[str] = []
    for name, addr in addresses:
        if name and addr:
            items.append(f"{name} <{addr}>")
        elif addr:
            items.append(addr)
        elif name:
            items.append(name)
    return ", ".join(items)


def _parse_date(msg: email_lib.message.Message) -> tuple[str, str]:
    date_str = msg.get("Date", "")
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        dt_utc = dt.astimezone(timezone.utc)
        return date_str, dt_utc.isoformat()
    except Exception:
        return date_str, ""


def _get_body(msg: email_lib.message.Message) -> str:
    from lib.html_processing import strip_html  # type: ignore[import]
    plain_body: Optional[str] = None
    html_body: Optional[str] = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get("Content-Disposition", ""))
            if "attachment" in cdisp:
                continue
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            text = payload.decode(charset, errors="replace")
            if ctype == "text/plain" and plain_body is None:
                plain_body = text
            elif ctype == "text/html" and html_body is None:
                html_body = text
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = text
            else:
                plain_body = text
    body = plain_body or (strip_html(html_body) if html_body else "")
    return body.strip()


def _strip_footer(body: str) -> str:
    lines: list[str] = []
    for line in body.splitlines():
        line_lower = line.lower().strip()
        if any(marker in line_lower for marker in _FOOTER_MARKERS):
            break
        if line.startswith(">") or (line.startswith("On ") and "wrote:" in line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _parse_imap_message(uid: str, raw_bytes: bytes, folder: str) -> Optional[dict]:
    try:
        msg = email_lib.message_from_bytes(raw_bytes)
    except Exception as exc:
        print(f"[imap_email] WARN: could not parse UID {uid}: {exc}", file=sys.stderr)
        return None
    subject = _decode_header_value(msg.get("Subject"))
    from_hdr = _parse_address_list(msg.get("From"))
    to_hdr = _parse_address_list(msg.get("To"))
    cc_hdr = _parse_address_list(msg.get("Cc"))
    msg_id = (msg.get("Message-ID") or "").strip()
    date_h, date_iso = _parse_date(msg)
    body = _strip_footer(_get_body(msg))[:_MAX_BODY_CHARS]
    snippet = re.sub(r"\s+", " ", body)[:200].strip()
    labels = [folder.lower().replace(" ", "_")]
    return {
        "id": uid,
        "thread_id": msg_id or uid,
        "subject": subject,
        "from": from_hdr,
        "to": to_hdr,
        "cc": cc_hdr,
        "date": date_h,
        "date_iso": date_iso,
        "body": body,
        "snippet": snippet,
        "labels": labels,
        "source": "icloud",
    }


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

#: The runtime hands a committed mailbox cursor to ``fetch(sync_cursor=...)`` for these sources.
SYNC_TOKEN_SCOPE = "mailbox"


def _untagged_int(mail: Any, name: str) -> Optional[int]:
    """A numeric untagged response kept from SELECT (UIDVALIDITY, UIDNEXT), None when absent or odd."""
    try:
        _typ, data = mail.response(name)
        value = data[0] if data else None
        return int(value) if value is not None and int(value) >= 0 else None
    except Exception:
        return None


def _cursor_text(folder: str, uidvalidity: int, last_uid: int) -> str:
    return json.dumps({"v": 1, "kind": "imap", "folder": folder, "uidvalidity": uidvalidity, "last_uid": last_uid},
                      sort_keys=True, separators=(",", ":"))


def _load_cursor(raw: str) -> dict:
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"v", "kind", "folder", "uidvalidity", "last_uid"}
            or data["v"] != 1 or data["kind"] != "imap" or not isinstance(data["folder"], str)
            or type(data["uidvalidity"]) is not int or type(data["last_uid"]) is not int
            or data["uidvalidity"] < 0 or data["last_uid"] < 0):
        raise ValueError("mailbox cursor is invalid")
    return data


def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any],
    source_tag: str = "icloud",
    server: str = "imap.mail.me.com",
    port: int = 993,
    folder: str = "inbox",
    sync_changes: bool = False,
    sync_cursor: Optional[str] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield IMAP emails since *since* (or with a UID above the stored cursor when ``sync_changes``)."""
    from lib.connector_coverage import (  # type: ignore[import]
        report_cursor,
        report_cursor_reset,
        report_delta,
        report_scope,
    )

    if type(sync_changes) is not bool:
        raise ValueError("sync_changes must be a boolean")
    if sync_cursor is not None and (not sync_changes or not isinstance(sync_cursor, str) or not sync_cursor.strip()):
        raise ValueError("sync_cursor must be a native cursor for a sync source")
    apple_id = auth_context.get("apple_id", "")
    app_password = auth_context.get("app_password") or auth_context.get("password", "")
    if not apple_id or not app_password:
        raise RuntimeError("[imap_email] auth_context missing apple_id or app_password")

    folder_name = _FOLDER_MAP.get(folder.lower(), folder.upper())

    # Parse since_iso → datetime for exact filtering
    try:
        since_dt = datetime.fromisoformat(since)
        if since_dt.tzinfo is None:
            import zoneinfo
            since_dt = since_dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
    except Exception:
        since_dt = datetime.now(timezone.utc) - timedelta(hours=24)

    # IMAP SINCE date (back off 1 day as IMAP is date-granular and inclusive)
    buffer_date = since_dt.astimezone(timezone.utc) - timedelta(days=1)
    imap_since = buffer_date.strftime("%d-%b-%Y")

    print(f"[imap_email] folder={folder_name} since={imap_since} max={max_results}",
          file=sys.stderr)

    mail = imaplib.IMAP4_SSL(server, port)
    try:
        mail.login(apple_id, app_password)
        status, _ = mail.select(f'"{folder_name}"', readonly=True)
        if status != "OK":
            print(f"[imap_email] WARN: could not SELECT {folder_name}", file=sys.stderr)
            report_scope("mailbox", "failed", reason="folder could not be selected")
            return
        uidvalidity = _untagged_int(mail, "UIDVALIDITY")
        uidnext = _untagged_int(mail, "UIDNEXT")
        criteria = f"SINCE {imap_since}"
        delta = False
        if sync_changes and sync_cursor is not None:
            try:
                cursor = _load_cursor(sync_cursor)
                if cursor["folder"] != folder_name or cursor["uidvalidity"] != uidvalidity:
                    raise ValueError("mailbox changed identity")
            except (ValueError, TypeError):
                # A different UIDVALIDITY means every stored UID may now name another message.
                report_cursor_reset()
                report_scope("mailbox", "failed", reason="mailbox UIDVALIDITY changed or cursor unusable")
                return
            delta = True
            report_delta()
            criteria = f"UID {cursor['last_uid'] + 1}:*"

        search_status, uid_data = mail.uid("SEARCH", None, criteria)
        if search_status != "OK":
            report_scope("mailbox", "failed", reason="search failed")
            return
        uid_list = uid_data[0].split() if uid_data and uid_data[0] else []
        if delta:           # "N:*" always includes the last message, even when it is older than N
            uid_list = [u for u in uid_list if int(u) > cursor["last_uid"]]
        over_cap = len(uid_list) > max_results
        uid_list = uid_list[-max_results:] if over_cap else uid_list

        yielded = 0
        skipped = 0
        for i in range(0, len(uid_list), _CHUNK_SIZE):
            chunk = uid_list[i : i + _CHUNK_SIZE]
            uid_str = b",".join(chunk).decode()
            fetch_status, fetch_data = mail.uid("FETCH", uid_str, "(RFC822)")
            if fetch_status != "OK":
                skipped += len(chunk)
                continue
            for j in range(0, len(fetch_data), 2):
                if yielded >= max_results:
                    break
                item = fetch_data[j]
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                uid = chunk[j // 2].decode()
                raw_bytes = item[1]
                record = _parse_imap_message(uid, raw_bytes, folder_name)
                if record is None:
                    skipped += 1
                    continue
                # Exact datetime filter (IMAP SINCE is date-only)
                if not delta:
                    try:
                        msg_dt = datetime.fromisoformat(record["date_iso"])
                        if msg_dt < since_dt:
                            continue
                    except Exception:
                        pass
                if source_tag:
                    record["source"] = source_tag
                yield record
                yielded += 1
        if over_cap or skipped:
            report_scope("mailbox", "truncated", records=yielded,
                         reason=f"{skipped} messages unreadable; results capped={over_cap}")
        else:
            report_scope("mailbox", "complete", records=yielded)
            if sync_changes and uidvalidity is not None and uidnext is not None and uidnext >= 1:
                # UIDNEXT was read at SELECT: anything that arrives later has a higher UID and is read next time.
                report_cursor(_cursor_text(folder_name, uidvalidity, uidnext - 1))
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify IMAP auth and connectivity."""
    try:
        apple_id = auth_context.get("apple_id", "")
        app_password = auth_context.get("app_password") or auth_context.get("password", "")
        if not apple_id or not app_password:
            return False
        server = auth_context.get("server", "imap.mail.me.com")
        port = int(auth_context.get("port", 993))
        with imaplib.IMAP4_SSL(server, port) as imap:
            imap.login(apple_id, app_password)
        return True
    except Exception as exc:
        print(f"[imap_email] health_check failed: {exc}", file=sys.stderr)
        return False
