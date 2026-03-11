#!/usr/bin/env python3
"""
msgraph_fetch.py  --  Artha Microsoft Graph email fetch script
============================================================
Fetches Outlook/Hotmail emails since a given timestamp via the MS Graph API
and outputs JSONL to stdout. Designed to run in parallel with gmail_fetch.py
at catch-up Step 4  --  the schemas are intentionally compatible.

Usage:
  python scripts/msgraph_fetch.py --since "2026-03-06T07:00:00-08:00"
  python scripts/msgraph_fetch.py --since "2026-03-06T07:00:00" --max-results 200
  python scripts/msgraph_fetch.py --since "2026-03-06T07:00:00" --folder sentItems
  python scripts/msgraph_fetch.py --health     (token check + connectivity)
  python scripts/msgraph_fetch.py --dry-run    (count matching messages, no JSONL output)
  python scripts/msgraph_fetch.py --reauth     (force a new interactive OAuth flow)

Output (JSONL, one JSON object per email on stdout):
  {"id": "...", "thread_id": "...", "subject": "...", "from": "...",
   "to": "...", "date": "...", "date_iso": "...", "body": "...",
   "snippet": "...", "labels": ["inbox"], "source": "outlook"}

Schema is deliberately identical to gmail_fetch.py output, with the sole
addition of "source": "outlook". The catch-up pipeline can ingest both feeds
transparently and route by source for deduplication.

Errors → stderr. Exit codes: 0 = success, 1 = error, 2 = quota exhausted.

Ref: TS §3.8, T-1B.1.1
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
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_BASE   = "https://graph.microsoft.com/v1.0"
SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))

# MS Graph well-known folder name aliases → canonical Graph folder names
_FOLDER_MAP: dict[str, str] = {
    "inbox":        "inbox",
    "sent":         "sentItems",
    "sentitems":    "sentItems",
    "archive":      "archive",
    "deleted":      "deleteditems",
    "deleteditems": "deleteditems",
    "junkemail":    "junkemail",
    "junk":         "junkemail",
    "spam":         "junkemail",
    "drafts":       "drafts",
}

# OData $select  --  only fields we actually use; omitting large fields keeps
# responses lean and avoids hitting the 4MB message limit
_MSG_SELECT = ",".join([
    "id",
    "conversationId",
    "subject",
    "from",
    "toRecipients",
    "ccRecipients",
    "receivedDateTime",
    "body",
    "bodyPreview",
    "importance",
    "isRead",
    "hasAttachments",
    "inferenceClassification",  # "focused" | "other" (Focused Inbox classifier)
])

_PAGE_SIZE    = 50      # messages per Graph API page (recommended ≤50 when body is selected)
_MAX_BODY_CHARS = 8000  # hard cap on body length before truncation (same as gmail_fetch.py)

# ---------------------------------------------------------------------------
# Retry / rate-limit guard
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES   = 4
_BASE_DELAY    = 1.5    # seconds
_BACKOFF_MULT  = 2.0
_MAX_DELAY     = 60.0   # MS Graph throttle windows can be longer than Google's


def _with_retry(fn, *, retries: int = _MAX_RETRIES, context: str = ""):
    """
    Execute fn() with exponential back-off on MS Graph 429 / 5xx responses.

    MS Graph throttling docs:
      https://learn.microsoft.com/en-us/graph/throttling
    When throttled, Graph returns 429 with a Retry-After header (seconds).
    We respect the Retry-After header when present.

    Args:
        fn:       zero-argument callable that performs one API call.
        retries:  maximum retry attempts after the first failure.
        context:  label shown in log messages.

    Returns:
        Return value of fn() on eventual success.

    Raises:
        Exception re-raised after all retries exhausted, with context label.
    """
    delay    = _BASE_DELAY
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            exc_str = str(exc).lower()
            is_retryable = (
                any(str(code) in exc_str for code in _RETRYABLE_STATUS_CODES)
                or "rate limit"           in exc_str
                or "quota"                in exc_str
                or "too many requests"    in exc_str
                or "throttl"              in exc_str
                or "service unavailable"  in exc_str
                or "temporarily unavail"  in exc_str
                or "gateway timeout"      in exc_str
            )

            if not is_retryable or attempt == retries:
                label = f" [{context}]" if context else ""
                raise type(exc)(
                    f"[msgraph_fetch]{label} API call failed after {attempt + 1} "
                    f"attempt(s): {exc}"
                ) from exc

            # Try to extract Retry-After from the exception string
            retry_after = None
            match = re.search(r"retry.after[^\d]*(\d+)", exc_str)
            if match:
                retry_after = int(match.group(1))

            wait = retry_after if retry_after else min(delay, _MAX_DELAY)
            print(
                f"[msgraph_fetch] ⚠ Throttled / server error "
                f"(attempt {attempt + 1}/{retries + 1}). "
                f"Retrying in {wait:.0f}s... ({context})",
                file=sys.stderr,
            )
            time.sleep(wait)
            delay    = min(delay * _BACKOFF_MULT, _MAX_DELAY)
            last_exc = exc

    raise last_exc  # type: ignore


# ---------------------------------------------------------------------------
# HTTP helper  --  uses requests (available via msal dependency)
# ---------------------------------------------------------------------------

def _graph_get(access_token: str, path: str, params: Optional[dict] = None) -> dict:
    """
    GET {GRAPH_BASE}{path} with bearer auth. Returns parsed JSON dict.
    Raises a descriptive exception on HTTP errors (includes status code and
    Graph's error message body for easier debugging).
    """
    try:
        import requests as req_lib
    except ImportError:
        print(
            "[msgraph_fetch] ERROR: 'requests' package not found.\n"
            "Run: pip install requests",
            file=sys.stderr,
        )
        sys.exit(1)

    url = f"{GRAPH_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
    }

    response = req_lib.get(url, headers=headers, params=params, timeout=30)

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        raise Exception(
            f"429 Too Many Requests (Retry-After: {retry_after}s): {response.text[:200]}"
        )
    if response.status_code >= 500:
        raise Exception(
            f"{response.status_code} Server Error: {response.text[:200]}"
        )
    if response.status_code == 401:
        raise Exception(
            "401 Unauthorized  --  token may be expired. "
            "Run: python scripts/setup_msgraph_oauth.py --reauth"
        )
    if response.status_code == 403:
        scope_hint = "Mail.Read scope may be missing  --  run setup_msgraph_oauth.py --reauth"
        raise Exception(f"403 Forbidden: {scope_hint}")

    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# HTML → plain text stripper (stdlib only)
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML → plain text converter. No external deps."""
    _SKIP = {"script", "style", "head", "meta", "noscript"}
    _BLOCK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3",
              "h4", "h5", "h6", "blockquote", "hr", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self._skip  = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip = True
        if t in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip = False
        if t in {"p", "div", "tr", "li", "blockquote", "section", "article"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        raw = html.unescape(raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _strip_html(html_content: str) -> str:
    """Strip HTML tags and return clean plain text."""
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_content)
        return stripper.get_text()
    except Exception:
        # Fallback: regex-based strip
        text = re.sub(r"<[^>]+>", " ", html_content)
        return html.unescape(re.sub(r" {2,}", " ", text)).strip()


# ---------------------------------------------------------------------------
# Thread / footer trimming  --  mirror of gmail_fetch.py patterns
# ---------------------------------------------------------------------------

_FOOTER_MARKERS = [
    re.compile(r"^[-_*]{3,}\s*$", re.MULTILINE),
    re.compile(r"^On .+wrote:\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^From:\s+.+\nSent:\s+", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^-----Original Message-----", re.MULTILINE | re.IGNORECASE),
    re.compile(r"\nSent from my (iPhone|iPad|Android|Galaxy|Samsung|Windows Phone)", re.IGNORECASE),
    re.compile(r"\nGet Outlook for ", re.IGNORECASE),
    re.compile(r"\nMicrosoft Teams meeting", re.IGNORECASE),          # Teams invite boilerplate start
    re.compile(r"\nJoin Microsoft Teams Meeting", re.IGNORECASE),
    re.compile(r"\nTo unsubscribe .{0,80}\n", re.IGNORECASE),
    re.compile(r"\nThis (email|message) (was sent|is intended|contains confidential)", re.IGNORECASE),
    re.compile(r"\nIf you (received|believe) this (email|message) in error", re.IGNORECASE),
    re.compile(r"\nCONFIDENTIAL(ITY| NOTICE):", re.IGNORECASE),
    re.compile(r"\nPrivacy Statement.*\n", re.IGNORECASE),
]


def _remove_thread_footer(text: str, max_chars: int = _MAX_BODY_CHARS) -> str:
    """Trim reply-chain quotes and boilerplate. Cap at max_chars."""
    for pattern in _FOOTER_MARKERS:
        match = pattern.search(text)
        if match:
            text = text[: match.start()].strip()
            break

    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"

    return text


# ---------------------------------------------------------------------------
# Address formatting helpers
# ---------------------------------------------------------------------------

def _fmt_addr(addr_obj: Optional[dict]) -> str:
    """
    Format a Graph emailAddress object → "Display Name <addr@example.com>".
    Handles None and missing sub-keys gracefully.
    """
    if not addr_obj:
        return ""
    ea = addr_obj.get("emailAddress", addr_obj)  # some endpoints nest differently
    name  = ea.get("name", "")
    email = ea.get("address", "")
    if name and email and name != email:
        return f"{name} <{email}>"
    return email or name


def _fmt_addr_list(addr_list: Optional[list]) -> str:
    """Format a list of Graph emailAddress recipient objects → comma-separated string."""
    if not addr_list:
        return ""
    parts = [_fmt_addr(item) for item in addr_list if item]
    return ", ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def _parse_message(msg: dict, folder: str = "inbox") -> dict:
    """
    Convert a raw MS Graph message object into the Artha canonical email dict.

    Output schema matches gmail_fetch.py with the addition of "source": "outlook"
    so the catch-up pipeline can distinguish sources while processing them uniformly.
    """
    # --- Subject ---
    subject = msg.get("subject") or "(no subject)"

    # --- Sender ---
    from_addr = _fmt_addr(msg.get("from"))

    # --- Recipients ---
    to_addr = _fmt_addr_list(msg.get("toRecipients"))
    cc_addr = _fmt_addr_list(msg.get("ccRecipients"))

    # --- Date ---
    received_raw = msg.get("receivedDateTime", "")
    # Graph returns ISO 8601 UTC: "2026-03-06T12:34:56Z"
    date_iso = received_raw
    if received_raw.endswith("Z"):
        try:
            dt      = datetime.fromisoformat(received_raw.rstrip("Z") + "+00:00")
            date_iso = dt.isoformat()
        except ValueError:
            pass

    # --- Body ---
    body_obj      = msg.get("body", {})
    content_type  = (body_obj.get("contentType") or "text").lower()
    raw_content   = body_obj.get("content", "")

    if content_type == "html":
        body_text = _strip_html(raw_content)
    else:
        body_text = raw_content

    body_text = _remove_thread_footer(body_text)

    # --- Snippet ---
    snippet = (msg.get("bodyPreview") or "")[:500]

    # --- Labels / metadata ---
    labels = [folder]
    if not msg.get("isRead", True):
        labels.append("unread")
    if msg.get("hasAttachments", False):
        labels.append("has_attachment")
    importance = msg.get("importance", "")
    if importance and importance.lower() != "normal":
        labels.append(f"importance_{importance.lower()}")
    # Focused Inbox classification
    fc = msg.get("inferenceClassification", "")
    if fc:
        labels.append(f"focused_{fc.lower()}")

    return {
        "id":         msg.get("id", ""),
        "thread_id":  msg.get("conversationId", ""),
        "subject":    subject,
        "from":       from_addr,
        "to":         to_addr,
        "cc":         cc_addr,
        "date":       received_raw,               # raw Graph timestamp
        "date_iso":   date_iso,                   # normalized ISO 8601
        "body":       body_text,
        "snippet":    snippet,
        "labels":     labels,
        "source":     "outlook",                  # distinguishes from gmail_fetch.py output
    }


# ---------------------------------------------------------------------------
# Since-timestamp → Graph OData $filter helper
# ---------------------------------------------------------------------------

def _since_to_filter(since_iso: str) -> str:
    """
    Convert an ISO 8601 timestamp (any timezone) to an OData $filter string
    suitable for MS Graph receivedDateTime comparisons.

    MS Graph requires the datetime in the filter to be UTC with Z suffix, e.g.:
      $filter=receivedDateTime ge 2026-03-06T15:00:00Z

    Args:
        since_iso: ISO 8601 string, e.g. "2026-03-06T07:00:00-08:00" or "2026-03-06T07:00:00"

    Returns:
        OData filter string ready for the $filter query param.
    """
    try:
        dt = datetime.fromisoformat(since_iso)
        if dt.tzinfo is None:
            # Assume Pacific time if no timezone  --  mirror gmail_fetch.py behaviour
            import zoneinfo
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
        # Convert to UTC for the filter
        dt_utc = dt.astimezone(timezone.utc)
        utc_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"receivedDateTime ge {utc_str}"
    except Exception as exc:
        print(
            f"[msgraph_fetch] Warning: could not parse --since '{since_iso}': {exc}. "
            "Falling back to last 24h.",
            file=sys.stderr,
        )
        since_fallback = (datetime.now(timezone.utc) - timedelta(hours=24))
        return f"receivedDateTime ge {since_fallback.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def _before_to_filter(before_iso: str) -> str:
    """Convert ISO 8601 timestamp to an OData upper-bound filter fragment (lt)."""
    try:
        dt = datetime.fromisoformat(before_iso)
        if dt.tzinfo is None:
            import zoneinfo
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles"))
        dt_utc = dt.astimezone(timezone.utc)
        utc_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"receivedDateTime lt {utc_str}"
    except Exception as exc:
        print(f"[msgraph_fetch] Warning: could not parse --before '{before_iso}': {exc}.",
              file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _get_valid_token() -> str:
    """
    Return a valid MS Graph access token string.
    Uses ensure_valid_token() from setup_msgraph_oauth.py for auto-refresh.
    Exits with code 1 on failure.
    """
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)

    try:
        from setup_msgraph_oauth import ensure_valid_token
    except ImportError:
        print(
            "[msgraph_fetch] ERROR: setup_msgraph_oauth.py not found.\n"
            "Run from ~/OneDrive/Artha/ or set PYTHONPATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        token_data   = ensure_valid_token()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("access_token missing from token data")
        return access_token
    except RuntimeError as exc:
        print(
            f"[msgraph_fetch] ERROR: Cannot obtain valid token: {exc}\n"
            "Run: python scripts/setup_msgraph_oauth.py",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

def fetch_emails(
    since_iso: str,
    max_results: int = 200,
    folder: str = "inbox",
    dry_run: bool = False,
    before_iso: Optional[str] = None,
) -> list[dict]:
    """
    Fetch Outlook emails received after ``since_iso`` (optionally before ``before_iso``)
    from the specified folder.

    Args:
        since_iso:   ISO 8601 timestamp; fetch emails with receivedDateTime >= this.
        max_results: cap on total messages to return.
        folder:      well-known folder name (default: "inbox").
        dry_run:     if True, count matching messages but return an empty list.
        before_iso:  ISO 8601 timestamp; fetch emails with receivedDateTime < this.

    Returns:
        List of parsed email dicts (sorted oldest-first by receivedDateTime).

    The function paginates through @odata.nextLink responses automatically and
    stops as soon as max_results is reached or all results are consumed.
    If Graph returns 429 after all retries, exits with code 2 (quota exhausted).
    """
    # Resolve folder alias
    folder_canonical = _FOLDER_MAP.get(folder.lower(), folder)

    access_token = _get_valid_token()
    odata_filter = _since_to_filter(since_iso)
    if before_iso:
        before_frag = _before_to_filter(before_iso)
        if before_frag:
            odata_filter += f" and {before_frag}"

    print(
        f"[msgraph_fetch] Fetching from folder='{folder_canonical}' "
        f"filter='{odata_filter}' max={max_results}"
        + (" [dry-run]" if dry_run else ""),
        file=sys.stderr,
    )

    url    = f"/me/mailFolders/{folder_canonical}/messages"
    params = {
        "$filter":  odata_filter,
        "$select":  _MSG_SELECT,
        "$top":     min(_PAGE_SIZE, max_results),
        "$orderby": "receivedDateTime asc",
    }

    all_messages: list[dict] = []
    next_url: Optional[str]  = None     # @odata.nextLink from previous page

    while True:
        if len(all_messages) >= max_results:
            break

        try:
            if next_url:
                # nextLink already has all params baked in  --  call it as-is
                response = _with_retry(
                    lambda u=next_url: _graph_get_full_url(access_token, u),
                    context=f"messages.list (page {len(all_messages) // _PAGE_SIZE + 1})",
                )
            else:
                response = _with_retry(
                    lambda: _graph_get(access_token, url, params),
                    context="messages.list (page 1)",
                )
        except Exception as exc:
            exc_str = str(exc).lower()
            if "quota" in exc_str or "throttl" in exc_str or "too many requests" in exc_str:
                print(
                    f"[msgraph_fetch] ⛔ MS Graph quota exhausted after retries: {exc}",
                    file=sys.stderr,
                )
                print(
                    "[msgraph_fetch] ⛔ CATCH-UP HALTED: partial email data would be misleading.",
                    file=sys.stderr,
                )
                sys.exit(2)
            print(f"[msgraph_fetch] ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        page_items: list[dict] = response.get("value", [])
        remaining  = max_results - len(all_messages)
        all_messages.extend(page_items[:remaining])

        next_url = response.get("@odata.nextLink")
        if not next_url or not page_items:
            break

    print(
        f"[msgraph_fetch] Retrieved {len(all_messages)} messages.",
        file=sys.stderr,
    )

    if dry_run:
        return []

    # Parse all messages
    parsed: list[dict] = []
    for i, msg in enumerate(all_messages):
        if i > 0 and i % 50 == 0:
            print(f"[msgraph_fetch] Parsing messages: {i}/{len(all_messages)}...", file=sys.stderr)
        try:
            parsed.append(_parse_message(msg, folder=folder_canonical))
        except Exception as exc:
            print(
                f"[msgraph_fetch] Warning: could not parse message {msg.get('id', '?')}: {exc}",
                file=sys.stderr,
            )

    print(f"[msgraph_fetch] Successfully parsed {len(parsed)} emails.", file=sys.stderr)
    return parsed


def _graph_get_full_url(access_token: str, full_url: str) -> dict:
    """
    GET an arbitrary full URL (used for @odata.nextLink pagination).
    The nextLink already embeds all query params  --  do not add more.
    """
    try:
        import requests as req_lib
    except ImportError:
        print("[msgraph_fetch] ERROR: 'requests' package not found.", file=sys.stderr)
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
    }
    response = req_lib.get(full_url, headers=headers, timeout=30)

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        raise Exception(f"429 Too Many Requests (Retry-After: {retry_after}s)")
    if response.status_code >= 500:
        raise Exception(f"{response.status_code} Server Error: {response.text[:200]}")

    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check() -> None:
    """
    Verify token validity and live connectivity to MS Graph.
    Prints a structured report. Exits 0 on success, 1 on failure.
    """
    print("Outlook (MS Graph) Email Health Check")
    print("─" * 42)

    # 1. Token
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    try:
        from setup_msgraph_oauth import ensure_valid_token, _load_token, TOKEN_FILE
    except ImportError:
        print("  ✗ setup_msgraph_oauth.py not found  --  run from ~/OneDrive/Artha/")
        sys.exit(1)

    token_path = TOKEN_FILE
    if not os.path.exists(token_path):
        print(f"  ✗ Token file missing: {token_path}")
        print("  Action: python scripts/setup_msgraph_oauth.py")
        sys.exit(1)
    print(f"  Token file:     ✓ {token_path}")

    # 2. Auto-refresh if needed
    try:
        token_data   = ensure_valid_token()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("access_token field empty")
    except RuntimeError as exc:
        print(f"  ✗ Token invalid: {exc}")
        print("  Action: python scripts/setup_msgraph_oauth.py --reauth")
        sys.exit(1)
    print("  Token:          ✓ valid (auto-refreshed if needed)")

    # 3. Identity check
    try:
        profile = _with_retry(
            lambda: _graph_get(access_token, "/me"),
            context="/me",
        )
        display_name = profile.get("displayName", "unknown")
        email        = profile.get("mail") or profile.get("userPrincipalName", "")
        print(f"  Identity:       ✓ {display_name} <{email}>")
    except Exception as exc:
        print(f"  ✗ /me failed: {exc}")
        sys.exit(1)

    # 4. Inbox access check  --  just count, don't download
    try:
        result = _with_retry(
            lambda: _graph_get(
                access_token,
                "/me/mailFolders/inbox/messages",
                params={"$top": 1, "$select": "id,subject,receivedDateTime", "$count": "true"},
            ),
            context="inbox sample",
        )
        # @odata.count may be present if $count=true is accepted
        count = result.get("@odata.count", "?")
        has_items = bool(result.get("value"))
        count_str = str(count) if count != "?" else ("non-empty" if has_items else "0")
        print(f"  Inbox access:   ✓ reachable (total messages: {count_str})")
    except Exception as exc:
        print(f"  ✗ Inbox read failed: {exc}")
        sys.exit(1)

    # 5. Scope check  --  verify Mail.Read is granted
    raw_token = token_data
    scopes    = raw_token.get("scope", "")
    has_mail  = "Mail.Read" in scopes
    print(f"  Mail.Read scope: {'✓ granted' if has_mail else '⚠ not visible in scope string (may still work)'}")

    print("\nOutlook: OK")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Outlook emails via MS Graph since a timestamp. "
            "Output: JSONL to stdout (schema-compatible with gmail_fetch.py)."
        )
    )
    parser.add_argument(
        "--since",
        type=str,
        help='ISO 8601 timestamp, e.g. "2026-03-06T07:00:00-08:00"',
    )
    parser.add_argument(
        "--max-results",
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
        "--folder",
        type=str,
        default="inbox",
        help=(
            "Mail folder to read. Well-known names: inbox (default), sentItems, "
            "archive, junk, drafts, deleteditems"
        ),
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Check token validity and connectivity only (no emails fetched)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Count matching messages but produce no JSONL output",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Force a new interactive OAuth flow (relaunch setup_msgraph_oauth.py --reauth)",
    )

    args = parser.parse_args()

    if args.health:
        run_health_check()
        return

    if args.reauth:
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "setup_msgraph_oauth.py"), "--reauth"],
            cwd=os.path.dirname(SCRIPTS_DIR),
        )
        sys.exit(result.returncode)

    if not args.since:
        print(
            "ERROR: --since is required (unless using --health or --reauth).\n"
            "Example: python scripts/msgraph_fetch.py --since '2026-03-06T07:00:00-08:00'",
            file=sys.stderr,
        )
        sys.exit(1)

    emails = fetch_emails(
        since_iso=args.since,
        max_results=args.max_results,
        folder=args.folder,
        dry_run=args.dry_run,
        before_iso=args.before,
    )

    if args.dry_run:
        # Count already printed inside fetch_emails via stderr; just exit
        return

    for email_dict in emails:
        print(json.dumps(email_dict, ensure_ascii=False))


if __name__ == "__main__":
    main()
