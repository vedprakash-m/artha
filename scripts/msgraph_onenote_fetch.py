#!/usr/bin/env python3
"""
msgraph_onenote_fetch.py — Artha Microsoft Graph OneNote fetch script
======================================================================
Fetches OneNote notebooks, sections, and pages via the MS Graph API
and outputs JSONL to stdout. Enables catch-up to incorporate structured
planning notes (finance checklists, immigration trackers, home logs) that
don't flow through email.

Usage:
  python scripts/msgraph_onenote_fetch.py --health
  python scripts/msgraph_onenote_fetch.py --list-notebooks
  python scripts/msgraph_onenote_fetch.py                         (all notebooks, all pages)
  python scripts/msgraph_onenote_fetch.py --notebook Finance
  python scripts/msgraph_onenote_fetch.py --notebook Finance --section "Accounts"
  python scripts/msgraph_onenote_fetch.py --modified-since "2026-03-01T00:00:00Z"
  python scripts/msgraph_onenote_fetch.py --dry-run              (count pages, no JSONL output)
  python scripts/msgraph_onenote_fetch.py --reauth               (force new OAuth flow)

Output (JSONL, one JSON object per page on stdout):
  {"notebook": "Finance", "section": "Accounts", "page_title": "Chase Overview",
   "last_modified": "2026-03-07T10:00:00Z", "content_text": "...", "source": "onenote"}

Requires: Notes.Read scope in MS Graph OAuth token.
  If scope is missing, run: python scripts/setup_msgraph_oauth.py --add-scope Notes.Read

Content cap: 3000 characters per page (long pages are truncated with a note).
Notebooks named "Personal Notebook" (case-insensitive) are skipped by default
unless --include-personal is specified.

Errors → stderr. Exit codes: 0 = success, 1 = error, 2 = scope missing.

Ref: TS §3.8, T-1B.1.7
"""

from __future__ import annotations

import sys
# Ensure we run inside the Artha venv. Ref: standardization.md §7.3
from _bootstrap import reexec_in_venv; reexec_in_venv()

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from lib.retry import with_retry


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_BASE   = "https://graph.microsoft.com/v1.0"
SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))

MAX_PAGE_CHARS   = 3000   # hard cap per page before truncation
_PAGE_LIST_SIZE  = 100    # pages per Graph API page (LIST endpoint max)

# Notebooks skipped by default (typically empty "Personal Notebook" OneNote creates)
_SKIP_NOTEBOOKS_DEFAULT = {"personal notebook"}


    """Execute fn() with exponential back-off on MS Graph 429 / 5xx responses."""
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
            )

            if not is_retryable or attempt == retries:
                label = f" [{context}]" if context else ""
                raise type(exc)(
                    f"[msgraph_onenote]{label} API call failed after {attempt + 1} "
                    f"attempt(s): {exc}"
                ) from exc

            retry_after = None
            match = re.search(r"retry.after[^\d]*(\d+)", exc_str)
            if match:
                retry_after = int(match.group(1))

            wait = retry_after if retry_after else min(delay, _MAX_DELAY)
            print(
                f"[msgraph_onenote] ⚠ Throttled (attempt {attempt + 1}/{retries + 1}). "
                f"Retrying in {wait:.0f}s... ({context})",
                file=sys.stderr,
            )
            time.sleep(wait)
            delay    = min(delay * _BACKOFF_MULT, _MAX_DELAY)
            last_exc = exc

    raise last_exc  # type: ignore


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _graph_get(access_token: str, path: str, params: Optional[dict] = None) -> dict:
    """GET {GRAPH_BASE}{path} with bearer auth. Returns parsed JSON dict."""
    try:
        import requests as req_lib
    except ImportError:
        print(
            "[msgraph_onenote] ERROR: 'requests' package not found.\n"
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
        raise Exception(f"{response.status_code} Server Error: {response.text[:200]}")
    if response.status_code == 401:
        raise Exception(
            "401 Unauthorized — token may be expired. "
            "Run: python scripts/setup_msgraph_oauth.py --reauth"
        )
    if response.status_code == 403:
        # Could be scope missing
        raise Exception(
            "403 Forbidden — Notes.Read scope may be missing.\n"
            "Run: python scripts/setup_msgraph_oauth.py --add-scope Notes.Read\n"
            f"Response: {response.text[:300]}"
        )

    response.raise_for_status()
    return response.json()


def _graph_get_content(access_token: str, path: str) -> str:
    """GET {GRAPH_BASE}{path} returning raw text (for OneNote page content — HTML)."""
    try:
        import requests as req_lib
    except ImportError:
        sys.exit(1)

    url = f"{GRAPH_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "text/html",
    }
    response = req_lib.get(url, headers=headers, timeout=30)

    if response.status_code == 403:
        raise Exception(
            "403 Forbidden — Notes.Read scope required.\n"
            "Add Notes.Read: python scripts/setup_msgraph_oauth.py --add-scope Notes.Read"
        )
    if response.status_code >= 400:
        raise Exception(
            f"{response.status_code} Error fetching page content: {response.text[:200]}"
        )
    return response.text


def _graph_get_paginated(access_token: str, path: str, params: Optional[dict] = None) -> list:
    """
    Fetch all pages of a paginated Graph endpoint.
    Follows @odata.nextLink until exhausted.
    Returns flat list of all 'value' items.
    """
    results = []
    current_url = None
    current_params = params

    while True:
        if current_url:
            data = with_retry(
                lambda u=current_url: _graph_get_full_url(access_token, u),
                context=current_url[:60]
            )
        else:
            data = with_retry(
                lambda p=path, pm=current_params: _graph_get(access_token, p, pm),
                context=path
            )

        items = data.get("value", [])
        results.extend(items)

        next_link = data.get("@odata.nextLink")
        if not next_link:
            break
        current_url   = next_link
        current_params = None  # next_link already contains query params

    return results


def _graph_get_full_url(access_token: str, full_url: str) -> dict:
    """GET an arbitrary full URL (used for @odata.nextLink pagination)."""
    try:
        import requests as req_lib
    except ImportError:
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
    }
    response = req_lib.get(full_url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# HTML → plain text stripper (OneNote pages are HTML)
# ---------------------------------------------------------------------------

class _OneNoteHTMLStripper(HTMLParser):
    """
    OneNote-specific HTML → plain text converter.
    OneNote HTML has <body> with paragraphs, tables, headings, lists.
    We convert these to clear plain text with basic structure.
    """
    _SKIP  = {"script", "style", "head", "meta", "noscript"}
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3",
              "h4", "h5", "h6", "blockquote", "section", "article",
              "table", "td", "th", "figure"}
    _LIST  = {"li"}

    def __init__(self) -> None:
        super().__init__()
        self._skip  = False
        self._depth = 0
        self._parts: list[str] = []
        self._in_table = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip = True
        if t in self._BLOCK:
            self._parts.append("\n")
        if t == "li":
            self._parts.append("• ")
        if t in {"h1", "h2", "h3"}:
            self._parts.append("\n### ")
        if t == "table":
            self._in_table = True
        if t in {"td", "th"} and self._in_table:
            self._parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip = False
        if t in {"p", "div", "tr", "li", "blockquote", "section"}:
            self._parts.append("\n")
        if t == "table":
            self._in_table = False
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        raw = html.unescape(raw)
        # Collapse runs of whitespace-only lines
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        raw = re.sub(r"[ \t]{2,}", " ", raw)
        return raw.strip()


def _onenote_html_to_text(html_content: str, max_chars: int = MAX_PAGE_CHARS) -> str:
    """Convert OneNote HTML page content to plain text. Cap at max_chars."""
    stripper = _OneNoteHTMLStripper()
    try:
        stripper.feed(html_content)
        text = stripper.get_text()
    except Exception:
        # Regex fallback
        text = re.sub(r"<[^>]+>", " ", html_content)
        text = html.unescape(re.sub(r" {2,}", " ", text)).strip()

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[... truncated — original: ~{len(text)} chars]"

    return text


# ---------------------------------------------------------------------------
# Token loading / validation
# ---------------------------------------------------------------------------

def _get_valid_token() -> str:
    """
    Load and refresh the MS Graph token via setup_msgraph_oauth.py helper.
    Returns a valid access_token string.
    """
    setup_dir = SCRIPTS_DIR
    if setup_dir not in sys.path:
        sys.path.insert(0, setup_dir)

    try:
        from setup_msgraph_oauth import ensure_valid_token  # type: ignore
    except ImportError as exc:
        print(
            "[msgraph_onenote] ERROR: Cannot import setup_msgraph_oauth.\n"
            f"  Error: {exc}\n"
            "  Ensure scripts/setup_msgraph_oauth.py exists and is importable.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        token_data   = ensure_valid_token()
        access_token = token_data.get("access_token") or token_data.get("accessToken")
        if not access_token:
            raise ValueError(f"No access_token key in returned data. Keys: {list(token_data.keys())}")
        return access_token
    except SystemExit:
        raise
    except Exception as exc:
        print(
            f"[msgraph_onenote] ERROR: Token refresh failed: {exc}\n"
            "  Try: python scripts/setup_msgraph_oauth.py --reauth",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Core OneNote fetch functions
# ---------------------------------------------------------------------------

def list_notebooks(access_token: str, include_personal: bool = False) -> list[dict]:
    """
    Returns list of {id, displayName, lastModifiedDateTime} for all notebooks.
    Skips 'Personal Notebook' unless include_personal=True.
    """
    data = with_retry(
        lambda: _graph_get(
            access_token,
            "/me/onenote/notebooks",
            params={"$select": "id,displayName,lastModifiedDateTime",
                    "$orderby": "lastModifiedDateTime desc"}
        ),
        context="list-notebooks"
    )
    notebooks = data.get("value", [])

    if not include_personal:
        notebooks = [
            nb for nb in notebooks
            if nb.get("displayName", "").lower() not in _SKIP_NOTEBOOKS_DEFAULT
        ]

    return notebooks


def list_sections(access_token: str, notebook_id: str) -> list[dict]:
    """Returns all sections in a notebook: {id, displayName, lastModifiedDateTime}."""
    data = with_retry(
        lambda: _graph_get(
            access_token,
            f"/me/onenote/notebooks/{notebook_id}/sections",
            params={"$select": "id,displayName,lastModifiedDateTime"}
        ),
        context=f"list-sections:{notebook_id[:12]}"
    )
    return data.get("value", [])


def list_pages(
    access_token: str,
    section_id: str,
    modified_since: Optional[str] = None
) -> list[dict]:
    """
    Returns pages in a section: {id, title, lastModifiedDateTime}.
    Filters to modified_since if provided (ISO 8601 UTC timestamp).
    """
    params: dict = {
        "$select": "id,title,lastModifiedDateTime",
        "$orderby": "lastModifiedDateTime desc",
        "$top": str(_PAGE_LIST_SIZE),
    }
    if modified_since:
        # OData filter: pages modified after timestamp
        params["$filter"] = f"lastModifiedDateTime ge {modified_since}"

    return _graph_get_paginated(
        access_token,
        f"/me/onenote/sections/{section_id}/pages",
        params=params,
    )


def fetch_page_content(access_token: str, page_id: str) -> str:
    """Fetch the raw HTML content of a single page. May raise on scope error."""
    return with_retry(
        lambda: _graph_get_content(access_token, f"/me/onenote/pages/{page_id}/content"),
        context=f"page-content:{page_id[:12]}"
    )


# ---------------------------------------------------------------------------
# JSONL output builder
# ---------------------------------------------------------------------------

def _make_record(
    notebook_name: str,
    section_name: str,
    page_title: str,
    last_modified: str,
    content_text: str,
) -> dict:
    return {
        "notebook":       notebook_name,
        "section":        section_name,
        "page_title":     page_title,
        "last_modified":  last_modified,
        "content_text":   content_text,
        "source":         "onenote",
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check(access_token: str) -> bool:
    """
    Connectivity + scope verification.
    Returns True if healthy, False otherwise.
    Prints detailed status to stdout.
    """
    print("OneNote (MS Graph) Health Check")
    print("─" * 42)

    # 1. Token format check
    if not access_token or len(access_token) < 20:
        print("  Token:          ✗ invalid or missing")
        return False
    print("  Token:          ✓ present and valid format")

    # 2. Identity check
    try:
        me = with_retry(
            lambda: _graph_get(access_token, "/me", {"$select": "displayName,userPrincipalName"}),
            context="identity"
        )
        display_name = me.get("displayName", "unknown")
        upn          = me.get("userPrincipalName", "unknown")
        print(f"  Identity:       ✓ {display_name} <{upn}>")
    except Exception as exc:
        print(f"  Identity:       ✗ {exc}")
        return False

    # 3. Notes.Read scope check — try listing notebooks
    try:
        data = with_retry(
            lambda: _graph_get(
                access_token,
                "/me/onenote/notebooks",
                {"$select": "id,displayName", "$top": "5"}
            ),
            context="scope-check"
        )
        notebooks = data.get("value", [])
        count = len(notebooks)
        names = ", ".join(nb.get("displayName", "?") for nb in notebooks[:3])
        suffix = "..." if count > 3 else ""
        print(f"  Notes.Read:     ✓ granted")
        print(f"  Notebooks:      ✓ {count} visible — {names}{suffix}")
    except Exception as exc:
        exc_str = str(exc)
        # Notes.Read gives 401 or 403 depending on tenant type when scope is missing
        if "403" in exc_str or "401" in exc_str or "Notes.Read" in exc_str or "Unauthorized" in exc_str:
            print(f"  Notes.Read:     ✗ scope not yet in token")
            print()
            print("  ACTION REQUIRED: Add Notes.Read scope by running:")
            print("    python scripts/setup_msgraph_oauth.py --reauth")
            print("  (Notes.Read was added to _SCOPES — re-auth will grant it)")
        else:
            print(f"  Notebooks:      ✗ {exc_str[:100]}")
        return False

    print()
    print("OneNote: OK")
    return True


# ---------------------------------------------------------------------------
# Main fetch routine
# ---------------------------------------------------------------------------

def fetch_onenote(
    access_token:     str,
    notebook_filter:  Optional[str] = None,
    section_filter:   Optional[str] = None,
    modified_since:   Optional[str] = None,
    include_personal: bool = False,
    dry_run:          bool = False,
    verbose:          bool = False,
) -> int:
    """
    Fetch OneNote content and output JSONL to stdout.
    Returns page count fetched.
    """
    # --- Normalize modified_since to ISO 8601 UTC ---
    if modified_since:
        try:
            # Attempt to parse and normalize
            dt = datetime.fromisoformat(modified_since.replace("Z", "+00:00"))
            modified_since = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            print(
                f"[msgraph_onenote] ⚠ Could not parse --modified-since '{modified_since}'. "
                "Ignoring filter.",
                file=sys.stderr,
            )
            modified_since = None

    # --- List notebooks ---
    if verbose:
        print("[msgraph_onenote] Enumerating notebooks...", file=sys.stderr)

    notebooks = with_retry(
        lambda: list_notebooks(access_token, include_personal=include_personal),
        context="list-notebooks"
    )

    if not notebooks:
        print("[msgraph_onenote] No notebooks found (check Notes.Read scope or --include-personal).",
              file=sys.stderr)
        return 0

    # Apply notebook filter (partial, case-insensitive match)
    if notebook_filter:
        filt = notebook_filter.lower()
        notebooks = [nb for nb in notebooks if filt in nb.get("displayName", "").lower()]
        if not notebooks:
            print(
                f"[msgraph_onenote] No notebooks match '{notebook_filter}'. "
                f"Use --list-notebooks to see available notebooks.",
                file=sys.stderr,
            )
            return 0

    page_count = 0
    skipped_errors = 0

    for notebook in notebooks:
        nb_id   = notebook["id"]
        nb_name = notebook.get("displayName", "Unknown")

        if verbose:
            print(f"[msgraph_onenote] Notebook: '{nb_name}'", file=sys.stderr)

        # --- List sections ---
        try:
            sections = with_retry(
                lambda i=nb_id: list_sections(access_token, i),
                context=f"sections:{nb_name}"
            )
        except Exception as exc:
            print(f"[msgraph_onenote] ⚠ Skipping notebook '{nb_name}': {exc}", file=sys.stderr)
            skipped_errors += 1
            continue

        # Apply section filter
        if section_filter:
            s_filt = section_filter.lower()
            sections = [s for s in sections if s_filt in s.get("displayName", "").lower()]

        for section in sections:
            sec_id   = section["id"]
            sec_name = section.get("displayName", "Unknown")

            if verbose:
                print(f"[msgraph_onenote]   Section: '{sec_name}'", file=sys.stderr)

            # --- List pages in section ---
            try:
                pages = with_retry(
                    lambda i=sec_id: list_pages(access_token, i, modified_since),
                    context=f"pages:{nb_name}/{sec_name}"
                )
            except Exception as exc:
                print(
                    f"[msgraph_onenote] ⚠ Skipping section '{nb_name}/{sec_name}': {exc}",
                    file=sys.stderr,
                )
                skipped_errors += 1
                continue

            for page in pages:
                page_id    = page["id"]
                page_title = page.get("title", "(Untitled)")
                page_mtime = page.get("lastModifiedDateTime", "")

                if dry_run:
                    page_count += 1
                    if verbose:
                        print(f"[msgraph_onenote]     [dry-run] page: {page_title}", file=sys.stderr)
                    continue

                # --- Fetch page content ---
                try:
                    html_content = with_retry(
                        lambda i=page_id: fetch_page_content(access_token, i),
                        context=f"content:{page_title[:30]}"
                    )
                    plain_text = _onenote_html_to_text(html_content, max_chars=MAX_PAGE_CHARS)
                except Exception as exc:
                    exc_str = str(exc)
                    if "Notes.Read" in exc_str or "scope" in exc_str.lower():
                        # Scope error — halt immediately
                        print(
                            f"[msgraph_onenote] ✗ Notes.Read scope missing: {exc}",
                            file=sys.stderr,
                        )
                        sys.exit(2)
                    print(
                        f"[msgraph_onenote] ⚠ Skipping page '{page_title}': {exc}",
                        file=sys.stderr,
                    )
                    skipped_errors += 1
                    continue

                record = _make_record(nb_name, sec_name, page_title, page_mtime, plain_text)
                print(json.dumps(record, ensure_ascii=False))
                page_count += 1

    if verbose and skipped_errors:
        print(
            f"[msgraph_onenote] Done. {page_count} pages output, {skipped_errors} skipped due to errors.",
            file=sys.stderr,
        )
    elif verbose:
        print(f"[msgraph_onenote] Done. {page_count} pages output.", file=sys.stderr)

    return page_count


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Microsoft OneNote pages as JSONL via MS Graph API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Health check (requires Notes.Read scope):
  python scripts/msgraph_onenote_fetch.py --health

  # List all accessible notebook names:
  python scripts/msgraph_onenote_fetch.py --list-notebooks

  # Fetch all notebooks (catch-up integration):
  python scripts/msgraph_onenote_fetch.py --modified-since "2026-03-01T00:00:00Z"

  # Fetch specific notebook, incremental:
  python scripts/msgraph_onenote_fetch.py --notebook Finance --modified-since "2026-03-06T07:00:00Z"

  # Fetch specific section only:
  python scripts/msgraph_onenote_fetch.py --notebook Immigration --section "Checklist"

  # Count pages without fetching content:
  python scripts/msgraph_onenote_fetch.py --dry-run

  # Re-authenticate (after adding Notes.Read scope to setup_msgraph_oauth.py):
  python scripts/msgraph_onenote_fetch.py --reauth
        """,
    )

    parser.add_argument(
        "--notebook", metavar="NAME",
        help="Restrict to notebooks matching NAME (partial, case-insensitive)."
    )
    parser.add_argument(
        "--section", metavar="NAME",
        help="Restrict to sections matching NAME (partial, case-insensitive)."
    )
    parser.add_argument(
        "--modified-since", metavar="TIMESTAMP",
        help="Only fetch pages modified after this ISO 8601 timestamp (idempotency filter)."
    )
    parser.add_argument(
        "--include-personal", action="store_true",
        help="Include notebooks named 'Personal Notebook' (skipped by default)."
    )
    parser.add_argument(
        "--health", action="store_true",
        help="Run connectivity + scope check only, no fetch."
    )
    parser.add_argument(
        "--list-notebooks", action="store_true",
        help="Print all available notebook names and IDs, then exit."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count matching pages without fetching content or writing JSONL."
    )
    parser.add_argument(
        "--reauth", action="store_true",
        help="Force a new interactive OAuth flow (re-authenticate)."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print progress to stderr during fetch."
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    # --reauth: delegate to setup_msgraph_oauth --reauth
    if args.reauth:
        import subprocess
        reauth_script = os.path.join(SCRIPTS_DIR, "setup_msgraph_oauth.py")
        if os.name == "nt":
            raise SystemExit(subprocess.call([sys.executable, reauth_script, "--reauth"]))
        else:
            os.execv(sys.executable, [sys.executable, reauth_script, "--reauth"])
        return  # unreachable

    # Load / refresh token
    access_token = _get_valid_token()

    # --health
    if args.health:
        ok = run_health_check(access_token)
        sys.exit(0 if ok else 1)

    # --list-notebooks
    if args.list_notebooks:
        print("Available OneNote Notebooks")
        print("─" * 44)
        try:
            notebooks = list_notebooks(access_token, include_personal=args.include_personal)
            if not notebooks:
                print("  (none — check Notes.Read scope or use --include-personal)")
                print()
                print("If Notes.Read scope is missing, add it:")
                print("  python scripts/setup_msgraph_oauth.py --add-scope Notes.Read")
                sys.exit(0)
            for nb in notebooks:
                name  = nb.get("displayName", "?")
                nb_id = nb.get("id", "?")[:24]
                mtime = nb.get("lastModifiedDateTime", "")[:10]
                print(f"  {name:<35}  id:{nb_id}...  modified:{mtime}")
        except Exception as exc:
            exc_str = str(exc)
            if "403" in exc_str or "Notes.Read" in exc_str:
                print()
                print("⚠ Notes.Read scope is required for OneNote access.")
                print("  Add it by editing setup_msgraph_oauth.py:")
                print("    1. Uncomment '# \"Notes.Read\",' in the _SCOPES list")
                print("    2. Re-run the OAuth flow:")
                print("       python scripts/setup_msgraph_oauth.py --reauth")
                sys.exit(2)
            else:
                print(f"  Error: {exc}")
                sys.exit(1)
        sys.exit(0)

    # Main fetch
    try:
        count = fetch_onenote(
            access_token     = access_token,
            notebook_filter  = args.notebook,
            section_filter   = args.section,
            modified_since   = getattr(args, "modified_since", None),
            include_personal = args.include_personal,
            dry_run          = args.dry_run,
            verbose          = args.verbose or args.dry_run,
        )

        if args.dry_run:
            print(f"[dry-run] {count} pages would be fetched.")
        elif args.verbose:
            print(f"[msgraph_onenote] Fetch complete: {count} pages.", file=sys.stderr)

    except SystemExit:
        raise
    except Exception as exc:
        exc_str = str(exc)
        if "Notes.Read" in exc_str or "403" in exc_str:
            print(
                "\n[msgraph_onenote] ✗ Notes.Read scope required.\n"
                "  Add it: edit setup_msgraph_oauth.py → uncomment 'Notes.Read' → run --reauth\n"
                f"  Error: {exc}",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"[msgraph_onenote] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
