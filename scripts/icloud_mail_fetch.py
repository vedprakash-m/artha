#!/usr/bin/env python3
"""
icloud_mail_fetch.py — Artha iCloud Mail fetch script
======================================================
Fetches iCloud (@icloud.com / @me.com) emails since a given timestamp via
IMAP and outputs JSONL to stdout. Designed to run in parallel with
gmail_fetch.py and msgraph_fetch.py at catch-up Step 4.

Apple does not provide an OAuth REST API for Mail — IMAP is the supported
access protocol. Auth uses an app-specific password (see setup_icloud_auth.py).

Usage:
  python scripts/icloud_mail_fetch.py --since "2026-03-06T07:00:00-08:00"
  python scripts/icloud_mail_fetch.py --since "2026-03-06T07:00:00" --max-results 200
  python scripts/icloud_mail_fetch.py --since "2026-03-06T07:00:00" --folder sent
  python scripts/icloud_mail_fetch.py --health     (IMAP connectivity + auth check)
  python scripts/icloud_mail_fetch.py --dry-run    (count matching messages, no JSONL)
  python scripts/icloud_mail_fetch.py --reauth     (re-run credential setup)

Output (JSONL, one JSON object per email on stdout):
  {"id": "...", "thread_id": "...", "subject": "...", "from": "...",
   "to": "...", "cc": "...", "date": "...", "date_iso": "...",
   "body": "...", "snippet": "...", "labels": ["inbox"], "source": "icloud"}

Schema is intentionally compatible with gmail_fetch.py and msgraph_fetch.py
output. The "source": "icloud" field identifies the feed.

iCloud IMAP server: imap.mail.me.com:993 (TLS)
Auth: Apple ID + app-specific password (from macOS Keychain via setup_icloud_auth.py)

Errors → stderr. Exit codes: 0 = success, 1 = error, 2 = quota / rate limit.

Ref: TS §3.9, T-1B.1.9
"""

from __future__ import annotations

import sys
import os as _os

# ---------------------------------------------------------------------------
# Auto-bootstrap: relaunch inside the Artha venv if not already there
# Cross-platform: ~/.artha-venvs/.venv-win on Windows, .venv on Mac
# ---------------------------------------------------------------------------
_ARTHA_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _os.name == "nt":
    _VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win", "Scripts", "python.exe")
    _VENV_PREFIX = _os.path.realpath(_os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv-win"))
else:
    # Check project-relative .venv first (symlink on Mac → ~/.artha-venvs/.venv; real dir pre-move)
    _PROJ_VENV_PY = _os.path.join(_ARTHA_DIR, ".venv", "bin", "python")
    _LOCAL_VENV_PY = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv", "bin", "python")
    _VENV_PY = _PROJ_VENV_PY if _os.path.exists(_PROJ_VENV_PY) else _LOCAL_VENV_PY
    _VENV_PREFIX = _os.path.realpath(_os.path.dirname(_os.path.dirname(_VENV_PY)))
    # Auto-create venv from requirements.txt if not found (e.g. first run in Cowork VM)
    if not _os.path.exists(_VENV_PY):
        import subprocess as _sp
        _local_venv = _os.path.join(_os.path.expanduser("~"), ".artha-venvs", ".venv")
        _sp.run([sys.executable, "-m", "venv", _local_venv], check=True, capture_output=True)
        _sp.run([_local_venv + "/bin/pip", "install", "-q", "-r",
                 _os.path.join(_ARTHA_DIR, "scripts", "requirements.txt")], capture_output=True)
        _VENV_PY = _local_venv + "/bin/python"
        _VENV_PREFIX = _os.path.realpath(_local_venv)
if _os.path.exists(_VENV_PY) and _os.path.realpath(sys.prefix) != _VENV_PREFIX:
    if _os.name == "nt":
        import subprocess as _sp; raise SystemExit(_sp.call([_VENV_PY] + sys.argv))
    else:
        _os.execv(_VENV_PY, [_VENV_PY] + sys.argv)

import argparse
import email as email_lib
import email.header
import email.utils
import html
import imaplib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from typing import Iterator, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMAP_HOST    = "imap.mail.me.com"
IMAP_PORT    = 993
SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))

# iCloud well-known folder aliases → IMAP mailbox names
# Note: iCloud uses "Sent Messages" (not "Sent Items" like Outlook)
_FOLDER_MAP: dict[str, str] = {
    "inbox":        "INBOX",
    "sent":         "Sent Messages",
    "sentitems":    "Sent Messages",
    "sentmessages": "Sent Messages",
    "drafts":       "Drafts",
    "trash":        "Deleted Messages",
    "deleted":      "Deleted Messages",
    "deleteditems": "Deleted Messages",
    "junk":         "Junk",
    "junkemail":    "Junk",
    "spam":         "Junk",
    "archive":      "Archive",
}

# Default results per fetch session
_DEFAULT_MAX = 200

# Chunk size for UID FETCH (keeps individual IMAP commands from ballooning)
_CHUNK_SIZE  = 50

# Common email thread footer patterns to strip (same style as msgraph_fetch.py)
_FOOTER_MARKERS = [
    "get outlook for ios",
    "get outlook for android",
    "sent from my iphone",
    "sent from my ipad",
    "sent from my mac",
    "sent from iphone",
    "sent from ipad",
    "unsubscribe",
    "you received this email because",
    "to unsubscribe from this list",
    "privacy policy",
    "view in browser",
]

# Maximum body characters kept (prevents huge JSONL from newsletter content)
_MAX_BODY_CHARS = 8_000


# ---------------------------------------------------------------------------
# HTML → plain text stripper (mirrors gmail_fetch.py / msgraph_fetch.py)
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Lightweight HTML → plain text converter."""

    _BLOCK_TAGS = {"p", "div", "br", "li", "tr", "blockquote", "h1", "h2",
                   "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&#{name};"))

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Collapse excessive whitespace / blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _strip_html(raw: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(raw)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Header decode helpers
# ---------------------------------------------------------------------------

def _decode_header_value(header_value: Optional[str]) -> str:
    """RFC 2047-decode an email header value to a plain Unicode string."""
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
    """Parse an address list header into 'Name <email>, ...' format."""
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


# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------

def _connect(apple_id: str, app_pwd: str) -> imaplib.IMAP4_SSL:
    """Open and authenticate an IMAP SSL connection. Caller must logout()."""
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(apple_id, app_pwd)
    return mail


def _since_to_imap_date(since_dt: datetime) -> str:
    """
    Convert a timezone-aware datetime to an IMAP SINCE search criterion date.
    Returns "DD-Mon-YYYY" in the server's local date (UTC backed off by 1 day
    as a buffer since IMAP SINCE is date-granular and inclusive).
    """
    # IMAP SINCE is inclusive at start-of-day, so we subtract 1 day to avoid
    # missing messages received early on the since_dt day in UTC.
    buffer_date = since_dt.astimezone(timezone.utc) - timedelta(days=1)
    return buffer_date.strftime("%d-%b-%Y")   # e.g. "06-Mar-2026"


def _parse_date(msg: email_lib.message.Message) -> tuple[str, str]:
    """
    Return (date_human, date_iso) from a parsed email message.
    date_human: "Sat, 07 Mar 2026 10:15:00 -0800"
    date_iso:   "2026-03-07T18:15:00+00:00"  (UTC)
    """
    date_str = msg.get("Date", "")
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        dt_utc = dt.astimezone(timezone.utc)
        return date_str, dt_utc.isoformat()
    except Exception:  # noqa: BLE001
        return date_str, ""


def _get_body(msg: email_lib.message.Message) -> str:
    """
    Extract the best plain-text representation of a MIME email body.
    Prefers text/plain; falls back to HTML-stripped text/html.
    """
    plain_body: Optional[str] = None
    html_body:  Optional[str] = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype    = part.get_content_type()
            cdisp    = str(part.get("Content-Disposition", ""))
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

    body = plain_body or (_strip_html(html_body) if html_body else "")
    return body.strip()


def _strip_footer(body: str) -> str:
    """Remove thread quoted history and common footers."""
    lines: list[str] = []
    for line in body.splitlines():
        line_lower = line.lower().strip()
        if any(marker in line_lower for marker in _FOOTER_MARKERS):
            break
        # Stop at quoted reply markers
        if line.startswith(">") or line.startswith("On ") and "wrote:" in line:
            break
        lines.append(line)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def _parse_message(uid: str, raw_bytes: bytes, folder: str) -> Optional[dict]:
    """Parse raw IMAP RFC822 bytes into an Artha email JSONL record."""
    try:
        msg = email_lib.message_from_bytes(raw_bytes)
    except Exception as exc:  # noqa: BLE001
        print(f"[icloud_mail_fetch] WARN: could not parse UID {uid}: {exc}",
              file=sys.stderr)
        return None

    subject   = _decode_header_value(msg.get("Subject"))
    from_hdr  = _parse_address_list(msg.get("From"))
    to_hdr    = _parse_address_list(msg.get("To"))
    cc_hdr    = _parse_address_list(msg.get("Cc"))
    msg_id    = (msg.get("Message-ID") or "").strip()
    date_h, date_iso = _parse_date(msg)

    body = _get_body(msg)
    body = _strip_footer(body)
    body = body[:_MAX_BODY_CHARS]

    snippet = re.sub(r"\s+", " ", body)[:200].strip()

    labels = [folder.lower().replace(" ", "_")]

    return {
        "id":        uid,
        "thread_id": msg_id or uid,
        "subject":   subject,
        "from":      from_hdr,
        "to":        to_hdr,
        "cc":        cc_hdr,
        "date":      date_h,
        "date_iso":  date_iso,
        "body":      body,
        "snippet":   snippet,
        "labels":    labels,
        "source":    "icloud",
    }


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

def fetch_emails(
    apple_id: str,
    app_pwd: str,
    since_dt: datetime,
    *,
    folder: str = "INBOX",
    max_results: int = _DEFAULT_MAX,
    dry_run: bool = False,
) -> Iterator[dict]:
    """
    Yield email dicts (JSONL records) from iCloud IMAP since since_dt.

    Fetches UIDs matching SINCE <date>, then filters to exact datetime in Python.
    """
    mail = _connect(apple_id, app_pwd)
    try:
        status, _ = mail.select(f'"{folder}"', readonly=True)
        if status != "OK":
            # Some iCloud folders have no quotes needed
            status, _ = mail.select(folder, readonly=True)
        if status != "OK":
            print(f"[icloud_mail_fetch] ERROR: Cannot select folder '{folder}'",
                  file=sys.stderr)
            return

        imap_since = _since_to_imap_date(since_dt)
        since_utc  = since_dt.astimezone(timezone.utc)

        # UID SEARCH is more stable than sequence-number SEARCH across reconnects
        status, uid_data = mail.uid("search", None, f"SINCE {imap_since}")  # type: ignore[call-overload]
        if status != "OK" or not uid_data[0]:
            return

        all_uids: list[bytes] = uid_data[0].split()
        # IMAP returns UIDs oldest-first; reverse to get newest first for --max-results
        all_uids.reverse()

        if dry_run:
            print(f"[dry-run] {len(all_uids)} messages found since {imap_since} "
                  f"in {folder} (before datetime filter)", file=sys.stderr)
            return

        yielded = 0
        for chunk_start in range(0, len(all_uids), _CHUNK_SIZE):
            if yielded >= max_results:
                break
            chunk = all_uids[chunk_start : chunk_start + _CHUNK_SIZE]
            uid_list = b",".join(chunk).decode()
            status, fetch_data = mail.uid("fetch", uid_list, "(RFC822)")  # type: ignore[call-overload]
            if status != "OK" or not fetch_data:
                continue

            # fetch_data alternates: (b'UID RFC822 ...', b'raw bytes'), b')'
            for item in fetch_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                # The first element of the tuple is the header line; extract UID
                header_line = item[0].decode(errors="replace")
                uid_match = re.search(r"UID (\d+)", header_line)
                uid_str = uid_match.group(1) if uid_match else "?"
                raw_bytes = item[1]

                rec = _parse_message(uid_str, raw_bytes, folder)
                if rec is None:
                    continue

                # Precise datetime filter (IMAP SINCE is date-only)
                if rec["date_iso"]:
                    try:
                        msg_dt = datetime.fromisoformat(rec["date_iso"])
                        if msg_dt < since_utc:
                            continue
                    except ValueError:
                        pass  # keep if we can't parse the date

                yield rec
                yielded += 1
                if yielded >= max_results:
                    break
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check(apple_id: str, app_pwd: str) -> None:
    """Print health status and exit 0/1."""
    print("iCloud Mail health check")
    print("-" * 40)
    print(f"  Server   : {IMAP_HOST}:{IMAP_PORT} (SSL)")
    print(f"  Apple ID : {apple_id}")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(apple_id, app_pwd)
        print("  Auth     : ✓ login successful")

        status, counts = mail.select("INBOX", readonly=True)
        inbox_count = int(counts[0].decode() if counts[0] else 0)
        print(f"  INBOX    : ✓ {inbox_count} messages")

        # List available folders for diagnostics
        status, folders = mail.list()
        folder_names: list[str] = []
        if status == "OK" and folders:
            for f in folders[:8]:
                if isinstance(f, bytes):
                    parts = f.decode(errors="replace").split('"/"')
                    if parts:
                        name = parts[-1].strip().strip('"')
                        folder_names.append(name)
        if folder_names:
            print(f"  Folders  : {', '.join(folder_names)}")

        mail.logout()
        print(f"\niCloud Mail: OK ({apple_id}, {inbox_count} inbox messages)")

    except imaplib.IMAP4.error as exc:
        print(f"  Auth     : ✗ {exc}", file=sys.stderr)
        print("\niCloud Mail: FAILED — authentication error", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"  Connect  : ✗ {exc}", file=sys.stderr)
        print("\niCloud Mail: FAILED — connection error", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch iCloud Mail via IMAP and output JSONL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--since",
        metavar="DATETIME",
        help='Fetch emails on or after this ISO 8601 datetime, e.g. "2026-03-07T07:00:00-08:00"',
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=_DEFAULT_MAX,
        metavar="N",
        help=f"Maximum emails to output (default: {_DEFAULT_MAX}).",
    )
    parser.add_argument(
        "--folder",
        default="inbox",
        metavar="NAME",
        help=(
            "iCloud folder to fetch from. Aliases: inbox, sent, drafts, trash, "
            "junk, archive. Default: inbox."
        ),
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Test IMAP connectivity + auth. Exits 1 on failure.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print count of matching messages but do not output JSONL.",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Re-run iCloud credential setup interactively.",
    )
    args = parser.parse_args()

    # ── --reauth: delegate to setup_icloud_auth.py ─────────────────────────
    if args.reauth:
        setup_script = os.path.join(SCRIPTS_DIR, "setup_icloud_auth.py")
        os.execv(sys.executable, [sys.executable, setup_script, "--reauth"])

    # ── Load credentials ────────────────────────────────────────────────────
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        from setup_icloud_auth import ensure_valid_credentials
        apple_id, app_pwd = ensure_valid_credentials()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── --health ─────────────────────────────────────────────────────────────
    if args.health:
        run_health_check(apple_id, app_pwd)
        return

    # ── Main fetch ───────────────────────────────────────────────────────────
    if not args.since:
        parser.error("--since is required unless using --health or --dry-run")

    # Parse --since to datetime
    try:
        since_str = args.since.replace(" ", "T")
        if since_str.endswith("Z"):
            since_str = since_str[:-1] + "+00:00"
        since_dt = datetime.fromisoformat(since_str)
        if since_dt.tzinfo is None:
            # Assume local time; attach system timezone offset via UTC offset
            since_dt = since_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"ERROR: Cannot parse --since value '{args.since}'. "
              "Use ISO 8601, e.g. '2026-03-07T07:00:00-08:00'", file=sys.stderr)
        sys.exit(1)

    # Resolve folder alias
    folder_key = args.folder.lower().replace(" ", "")
    folder     = _FOLDER_MAP.get(folder_key, args.folder)

    count = 0
    try:
        for rec in fetch_emails(
            apple_id, app_pwd, since_dt,
            folder=folder,
            max_results=args.max_results,
            dry_run=args.dry_run,
        ):
            print(json.dumps(rec, ensure_ascii=False))
            count += 1
    except imaplib.IMAP4.error as exc:
        print(f"ERROR: IMAP error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR: Connection error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass

    if args.dry_run:
        print(f"[dry-run] would output 0 JSONL records (fetch skipped in dry-run mode)",
              file=sys.stderr)
    else:
        print(f"[icloud_mail_fetch] fetched {count} messages from {folder}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
