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
from scripts.lib.html_processing import SIMPLE_FOOTER_MARKERS as _FOOTER_MARKERS  # noqa: E402


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

def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any],
    source_tag: str = "icloud",
    server: str = "imap.mail.me.com",
    port: int = 993,
    folder: str = "inbox",
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield IMAP emails since *since* timestamp."""
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
            return

        _status, uid_data = mail.uid("SEARCH", None, f"SINCE {imap_since}")
        if not uid_data or not uid_data[0]:
            return
        uid_list = uid_data[0].split()
        uid_list = uid_list[-max_results:] if len(uid_list) > max_results else uid_list

        yielded = 0
        for i in range(0, len(uid_list), _CHUNK_SIZE):
            chunk = uid_list[i : i + _CHUNK_SIZE]
            uid_str = b",".join(chunk).decode()
            _s, fetch_data = mail.uid("FETCH", uid_str, "(RFC822)")
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
                    continue
                # Exact datetime filter (IMAP SINCE is date-only)
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
