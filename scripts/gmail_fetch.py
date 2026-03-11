#!/usr/bin/env python3
"""
gmail_fetch.py — Artha Gmail fetch script
==========================================
Fetches emails since a given timestamp and outputs JSONL to stdout.
Claude reads this output to run domain processing.

Usage:
  python scripts/gmail_fetch.py --since "2026-03-06T07:00:00-08:00"
  python scripts/gmail_fetch.py --since "2026-03-06T07:00:00-08:00" --max 150
  python scripts/gmail_fetch.py --health   (check auth + connectivity)
  python scripts/gmail_fetch.py --reauth   (force new OAuth flow)

Output (JSONL, one JSON object per email on stdout):
  {"id": "...", "thread_id": "...", "subject": "...", "from": "...",
   "to": "...", "date": "...", "date_iso": "...", "body": "...",
   "labels": [...], "snippet": "..."}

Errors go to stderr. Exit code 0 = success, 1 = error.

Ref: TS §3.1, T-1A.3.2
"""

from __future__ import annotations

import sys, os as _os
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
import base64
import email as email_lib
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional


# ---------------------------------------------------------------------------
# Retry / rate-limit guard
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES   = 3
_BASE_DELAY    = 1.0   # seconds
_BACKOFF_MULT  = 2.0
_MAX_DELAY     = 30.0  # seconds cap per wait


def _with_retry(fn, *, retries: int = _MAX_RETRIES, context: str = ""):
    """
    Execute fn() with exponential back-off on HTTP 429 / 5xx responses.

    Args:
        fn:       zero-argument callable that performs one API call and returns a result.
        retries:  maximum number of *retry* attempts after the first failure.
        context:  label shown in log messages to identify which call failed.

    Returns:
        The return value of fn() on success.

    Raises:
        Exception re-raised after all retries exhausted, with context in the message.
    """
    delay = _BASE_DELAY
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            # Check if this is a retryable HTTP error
            exc_str = str(exc).lower()
            is_retryable = (
                any(str(code) in exc_str for code in _RETRYABLE_STATUS_CODES)
                or "rate limit" in exc_str
                or "quota" in exc_str
                or "too many requests" in exc_str
                or "service unavailable" in exc_str
                or "backend error" in exc_str
            )

            if not is_retryable or attempt == retries:
                label = f" [{context}]" if context else ""
                raise type(exc)(
                    f"[gmail_fetch]{label} API call failed after {attempt + 1} "
                    f"attempt(s): {exc}"
                ) from exc

            wait = min(delay, _MAX_DELAY)
            print(
                f"[gmail_fetch] ⚠ Rate-limited or server error (attempt {attempt + 1}/{retries + 1}). "
                f"Retrying in {wait:.0f}s... ({context})",
                file=sys.stderr,
            )
            time.sleep(wait)
            delay = min(delay * _BACKOFF_MULT, _MAX_DELAY)
            last_exc = exc

    # Should not reach here
    raise last_exc  # type: ignore

# ---------------------------------------------------------------------------
# HTML → plain text stripper (stdlib only — no external deps)
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML → plain text converter."""
    def __init__(self) -> None:
        super().__init__()
        self._skip_tags = {"script", "style", "head", "meta", "noscript"}
        self._skip = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in self._skip_tags:
            self._skip = True
        # Add whitespace around block elements
        if tag.lower() in {"p", "br", "div", "tr", "li", "h1", "h2", "h3",
                            "h4", "h5", "h6", "blockquote", "hr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._skip_tags:
            self._skip = False
        if tag.lower() in {"p", "div", "tr", "li", "blockquote"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        # Unescape HTML entities
        raw = html.unescape(raw)
        # Collapse 3+ consecutive blank lines into 2
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _strip_html(html_content: str) -> str:
    """Strip HTML tags and return plain text."""
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_content)
        return stripper.get_text()
    except Exception:
        # Fallback: regex strip
        text = re.sub(r"<[^>]+>", " ", html_content)
        return html.unescape(text).strip()


# ---------------------------------------------------------------------------
# Email thread footer removal
# ---------------------------------------------------------------------------

_FOOTER_MARKERS = [
    # Common reply/forward separators
    re.compile(r"^[-_*]{3,}\s*$", re.MULTILINE),
    re.compile(r"^On .+wrote:\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^From:\s+.+\nSent:\s+", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^-----Original Message-----", re.MULTILINE | re.IGNORECASE),
    # Email signatures
    re.compile(r"\nSent from my (iPhone|iPad|Android|Galaxy|Samsung)", re.IGNORECASE),
    re.compile(r"\nGet Outlook for ", re.IGNORECASE),
    # Unsubscribe / disclaimer boilerplate
    re.compile(r"\nTo unsubscribe .{0,80}\n", re.IGNORECASE),
    re.compile(r"\nThis email (was sent|is intended|contains confidential)", re.IGNORECASE),
    re.compile(r"\nIf you (received|believe) this (email|message) in error", re.IGNORECASE),
    re.compile(r"\nCONFIDENTIAL(ITY| NOTICE):", re.IGNORECASE),
]


def _remove_thread_footer(text: str, max_chars: int = 8000) -> str:
    """Truncate reply chains and remove boilerplate footers. Cap at max_chars."""
    for pattern in _FOOTER_MARKERS:
        match = pattern.search(text)
        if match:
            text = text[:match.start()].strip()
            break
    # Hard cap to keep Claude's context window healthy
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"
    return text


# ---------------------------------------------------------------------------
# MIME body extraction
# ---------------------------------------------------------------------------

def _decode_base64_safe(data: str) -> str:
    """Decode URL-safe base64 from Gmail API payload."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    """
    Recursively extract the best plain-text body from a Gmail message payload.
    Preference order: text/plain > text/html (stripped)
    """
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return _decode_base64_safe(body_data)

    if mime_type == "text/html" and body_data:
        return _strip_html(_decode_base64_safe(body_data))

    # Recurse into multipart
    if mime_type.startswith("multipart/"):
        parts = payload.get("parts", [])
        # First pass: prefer text/plain
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return _decode_base64_safe(data)
        # Second pass: try text/html
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return _strip_html(_decode_base64_safe(data))
        # Third pass: recurse into nested multipart
        for part in parts:
            if part.get("mimeType", "").startswith("multipart/"):
                result = _extract_body(part)
                if result:
                    return result

    return ""


# ---------------------------------------------------------------------------
# Gmail API helpers
# ---------------------------------------------------------------------------

def _get_header(headers: list[dict], name: str) -> str:
    """Extract a named header value (case-insensitive)."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _parse_message(msg: dict) -> dict:
    """Convert a Gmail API message object into a clean dict for JSONL output."""
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    subject = _get_header(headers, "Subject") or "(no subject)"
    sender  = _get_header(headers, "From")
    to      = _get_header(headers, "To")
    date_str = _get_header(headers, "Date")

    # Parse date to ISO 8601 (best-effort)
    date_iso = ""
    try:
        # email.utils.parsedate_to_datetime handles most RFC 2822 formats
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        date_iso = dt.astimezone(timezone.utc).isoformat()
    except Exception:
        date_iso = date_str  # fall back to raw string

    body = _extract_body(payload)
    body = _remove_thread_footer(body)

    return {
        "id":        msg["id"],
        "thread_id": msg.get("threadId", ""),
        "subject":   subject,
        "from":      sender,
        "to":        to,
        "date":      date_str,
        "date_iso":  date_iso,
        "body":      body,
        "snippet":   msg.get("snippet", ""),
        "labels":    msg.get("labelIds", []),
    }


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

def _datetime_to_gmail_query(since_iso: str) -> str:
    """
    Convert ISO 8601 timestamp to Gmail 'after:' query (Unix epoch seconds).
    Gmail's after: operator takes Unix seconds (integer).
    """
    try:
        dt = datetime.fromisoformat(since_iso)
        if dt.tzinfo is None:
            # Assume Pacific time if no timezone specified
            import zoneinfo
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
        epoch = int(dt.timestamp())
        return f"after:{epoch}"
    except Exception as exc:
        print(f"[gmail_fetch] Warning: could not parse --since timestamp '{since_iso}': {exc}. "
              f"Fetching last 24h.", file=sys.stderr)
        import time
        epoch = int(time.time()) - 86400
        return f"after:{epoch}"


def _datetime_to_gmail_before(before_iso: str) -> str:
    """Convert ISO 8601 timestamp to Gmail 'before:' query (Unix epoch seconds)."""
    try:
        dt = datetime.fromisoformat(before_iso)
        if dt.tzinfo is None:
            import zoneinfo
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
        epoch = int(dt.timestamp())
        return f"before:{epoch}"
    except Exception as exc:
        print(f"[gmail_fetch] Warning: could not parse --before timestamp '{before_iso}': {exc}.",
              file=sys.stderr)
        return ""


def fetch_emails(
    since_iso: str,
    max_results: int = 200,
    label_filter: Optional[str] = None,
    before_iso: Optional[str] = None,
) -> list[dict]:
    """
    Fetch all emails since `since_iso` timestamp (optionally before `before_iso`).
    Returns list of parsed email dicts.
    """
    # Import here to avoid import errors if google packages not yet installed
    try:
        from google_auth import build_service
    except ImportError:
        print("[gmail_fetch] ERROR: google_auth.py not found. "
              "Run from ~/OneDrive/Artha/ or set PYTHONPATH.", file=sys.stderr)
        sys.exit(1)

    print(f"[gmail_fetch] Connecting to Gmail API...", file=sys.stderr)
    service = build_service("gmail", "v1")

    query = _datetime_to_gmail_query(since_iso)
    if before_iso:
        query += " " + _datetime_to_gmail_before(before_iso)
    if label_filter:
        query += f" label:{label_filter}"

    print(f"[gmail_fetch] Query: '{query}' (max {max_results})", file=sys.stderr)

    # Paginate through results
    all_message_refs: list[dict] = []
    page_token: Optional[str] = None
    fetched = 0

    while fetched < max_results:
        batch_size = min(max_results - fetched, 100)  # Gmail API max per page = 100
        kwargs: dict = {
            "userId": "me",
            "q": query,
            "maxResults": batch_size,
            "includeSpamTrash": False,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        try:
            response = _with_retry(
                lambda: service.users().messages().list(**kwargs).execute(),
                context="messages.list",
            )
        except Exception as exc:
            # Quota exhausted after retries — hard halt as per TS §7.2
            print(
                f"[gmail_fetch] ⛔ Gmail API quota exceeded after {_MAX_RETRIES} retries: {exc}",
                file=sys.stderr,
            )
            print(
                "[gmail_fetch] ⛔ CATCH-UP HALTED: partial email data would be misleading.",
                file=sys.stderr,
            )
            sys.exit(2)  # Exit code 2 = quota exhausted (distinct from generic error)

        messages = response.get("messages", [])
        all_message_refs.extend(messages)
        fetched += len(messages)

        page_token = response.get("nextPageToken")
        if not page_token or not messages:
            break

    print(f"[gmail_fetch] Found {len(all_message_refs)} messages.", file=sys.stderr)

    # Fetch full message details (in batches for efficiency)
    emails: list[dict] = []
    total = len(all_message_refs)

    for i, ref in enumerate(all_message_refs):
        if i % 20 == 0 and i > 0:
            print(f"[gmail_fetch] Fetching messages: {i}/{total}...", file=sys.stderr)
        try:
            msg = _with_retry(
                lambda ref=ref: service.users().messages().get(
                    userId="me",
                    id=ref["id"],
                    format="full",
                ).execute(),
                context=f"messages.get({ref['id'][:8]})",
            )
            parsed = _parse_message(msg)
            emails.append(parsed)
        except Exception as exc:
            print(f"[gmail_fetch] Warning: could not fetch message {ref['id']}: {exc}",
                  file=sys.stderr)
            continue

    print(f"[gmail_fetch] Successfully fetched {len(emails)} emails.", file=sys.stderr)
    return emails


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check() -> None:
    """Test authentication and connectivity. Exit 0 on success, 1 on failure."""
    try:
        from google_auth import build_service, check_stored_credentials
    except ImportError:
        print("ERROR: google_auth.py not found. Run from ~/OneDrive/Artha/.")
        sys.exit(1)

    print("Gmail Health Check")
    print("─" * 40)

    creds_status = check_stored_credentials()
    print(f"  Client ID stored:      {'✓' if creds_status['client_id_stored'] else '✗ MISSING'}")
    print(f"  Client secret stored:  {'✓' if creds_status['client_secret_stored'] else '✗ MISSING'}")
    print(f"  Gmail token stored:    {'✓' if creds_status['gmail_token_stored'] else '✗ MISSING — run setup'}")

    if not all([creds_status["client_id_stored"],
                creds_status["client_secret_stored"],
                creds_status["gmail_token_stored"]]):
        print("\nAction required: python scripts/setup_google_oauth.py")
        sys.exit(1)

    print("\n  Testing Gmail API connection...")
    try:
        service = build_service("gmail", "v1")
        profile = service.users().getProfile(userId="me").execute()
        print(f"  ✓ Connected as: {profile.get('emailAddress')}")
        print(f"  ✓ Total messages: {profile.get('messagesTotal', 'unknown')}")
        print("\nGmail: OK")
    except Exception as exc:
        print(f"\n  ✗ Gmail connection failed: {exc}")
        print("\nTry: python scripts/setup_google_oauth.py --reauth")
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Gmail emails since a given timestamp. Output: JSONL to stdout."
    )
    parser.add_argument(
        "--since",
        type=str,
        help='ISO 8601 timestamp, e.g. "2026-03-06T07:00:00-08:00"',
    )
    parser.add_argument(
        "--max",
        type=int,
        default=200,
        dest="max_results",
        help="Maximum number of emails to fetch (default: 200)",
    )
    parser.add_argument(
        "--before",
        type=str,
        default=None,
        help='ISO 8601 upper-bound timestamp (exclusive), e.g. "2026-01-01T00:00:00"',
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Optional Gmail label filter (e.g. 'inbox')",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Check authentication and connectivity only",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Force a new OAuth flow (re-authenticate)",
    )

    args = parser.parse_args()

    if args.health:
        run_health_check()
        return

    if args.reauth:
        try:
            from google_auth import build_service
            build_service("gmail", "v1", force_reauth=True)
            print("Re-authentication complete.", file=sys.stderr)
        except ImportError:
            print("ERROR: google_auth.py not found.")
            sys.exit(1)
        return

    if not args.since:
        print("ERROR: --since is required (unless using --health or --reauth).", file=sys.stderr)
        print("Example: python gmail_fetch.py --since '2026-03-06T07:00:00-08:00'", file=sys.stderr)
        sys.exit(1)

    # Add scripts/ dir to path so google_auth imports work
    import os
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    emails = fetch_emails(
        since_iso=args.since,
        max_results=args.max_results,
        label_filter=args.label,
        before_iso=args.before,
    )

    # Output JSONL — one line per email
    for email_dict in emails:
        print(json.dumps(email_dict, ensure_ascii=False))


if __name__ == "__main__":
    main()
