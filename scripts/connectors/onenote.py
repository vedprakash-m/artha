"""
scripts/connectors/onenote.py — Microsoft OneNote connector (standalone).

Fetches OneNote page records via the MS Graph API and yields standardised
dicts directly — no stdout-capture hack, no dependency on
msgraph_onenote_fetch.py.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import html
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_MAX_PAGE_CHARS = 3_000
_PAGE_LIST_SIZE = 100
_SKIP_NOTEBOOKS = {"personal notebook"}
_CONTENT_WORKERS = 5   # parallel page-content fetches (Graph API throttle-safe)


# ---------------------------------------------------------------------------
# HTML → plain text stripper (moved from msgraph_onenote_fetch.py)
# ---------------------------------------------------------------------------

class _OneNoteHTMLStripper(HTMLParser):
    _SKIP = {"script", "style", "head", "meta", "noscript"}
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3",
              "h4", "h5", "h6", "blockquote", "section", "article",
              "table", "td", "th", "figure"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self._in_table = False
        self._parts: list[str] = []

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
        raw = html.unescape("".join(self._parts))
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        raw = re.sub(r"[ \t]{2,}", " ", raw)
        return raw.strip()


def _onenote_html_to_text(html_content: str, max_chars: int = _MAX_PAGE_CHARS) -> str:
    """Convert OneNote HTML page content to plain text. Cap at max_chars."""
    stripper = _OneNoteHTMLStripper()
    try:
        stripper.feed(html_content)
        text = stripper.get_text()
    except Exception:
        text = html.unescape(re.sub(r"<[^>]+>", " ", html_content)).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[... truncated — ~{len(text)} chars]"
    return text


# ---------------------------------------------------------------------------
# OneNote API helpers
# ---------------------------------------------------------------------------

def _list_notebooks(access_token: str, include_personal: bool = False) -> list[dict]:
    from lib.msgraph import _graph_get  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]
    data = with_retry(
        lambda: _graph_get(
            access_token, "/me/onenote/notebooks",
            {"$select": "id,displayName,lastModifiedDateTime",
             "$orderby": "lastModifiedDateTime desc"},
        ),
        context="onenote.list-notebooks",
    )
    notebooks = data.get("value", [])
    if not include_personal:
        notebooks = [nb for nb in notebooks
                     if nb.get("displayName", "").lower() not in _SKIP_NOTEBOOKS]
    return notebooks


def _list_sections(access_token: str, notebook_id: str) -> list[dict]:
    from lib.msgraph import _graph_get  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]
    data = with_retry(
        lambda: _graph_get(
            access_token,
            f"/me/onenote/notebooks/{notebook_id}/sections",
            {"$select": "id,displayName,lastModifiedDateTime"},
        ),
        context=f"onenote.sections:{notebook_id[:12]}",
    )
    return data.get("value", [])


def _list_pages(access_token: str, section_id: str, modified_since: Optional[str] = None) -> list[dict]:
    from lib.msgraph import _graph_get_paginated  # type: ignore[import]
    params: dict = {
        "$select": "id,title,lastModifiedDateTime",
        "$orderby": "lastModifiedDateTime desc",
        "$top": str(_PAGE_LIST_SIZE),
    }
    if modified_since:
        params["$filter"] = f"lastModifiedDateTime ge {modified_since}"
    return _graph_get_paginated(
        access_token,
        f"/me/onenote/sections/{section_id}/pages",
        params,
    )


def _fetch_page_content(access_token: str, page_id: str) -> str:
    from lib.msgraph import _graph_get_content  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]
    return with_retry(
        lambda: _graph_get_content(access_token, f"/me/onenote/pages/{page_id}/content"),
        context=f"onenote.content:{page_id[:12]}",
    )


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str,
    max_results: int = 500,
    auth_context: Dict[str, Any],
    source_tag: str = "onenote",
    notebook_filter: Optional[str] = None,
    section_filter: Optional[str] = None,
    include_personal: bool = False,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield OneNote page records modified since *since*.

    Parallel strategy (3 stages):
      1. List all sections across notebooks concurrently.
      2. List pages (with modified_since filter) across sections concurrently.
      3. Fetch page HTML content concurrently (up to _CONTENT_WORKERS threads).
    This turns N×serial_content_latency into ~1×max_content_latency.
    """
    access_token = auth_context.get("access_token", "")
    if not access_token:
        raise RuntimeError("[onenote] auth_context missing access_token")

    # Normalize modified_since to ISO 8601 UTC
    modified_since: Optional[str] = None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        modified_since = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    print(f"[onenote] modified_since={modified_since} notebook_filter={notebook_filter}",
          file=sys.stderr)

    notebooks = _list_notebooks(access_token, include_personal=include_personal)
    if notebook_filter:
        filt = notebook_filter.lower()
        notebooks = [nb for nb in notebooks if filt in nb.get("displayName", "").lower()]
    if not notebooks:
        print("[onenote] No notebooks found", file=sys.stderr)
        return

    # ── Stage 1: list sections across all notebooks in parallel ────────────
    nb_sections: list[tuple[dict, dict]] = []   # (notebook, section)
    with ThreadPoolExecutor(max_workers=min(len(notebooks), 4)) as pool:
        fut_to_nb = {pool.submit(_list_sections, access_token, nb["id"]): nb
                     for nb in notebooks}
        for fut in as_completed(fut_to_nb):
            nb = fut_to_nb[fut]
            nb_name = nb.get("displayName", "Unknown")
            try:
                sections = fut.result()
            except Exception as exc:
                print(f"[onenote] WARN: skipping notebook '{nb_name}': {exc}", file=sys.stderr)
                continue
            if section_filter:
                s_filt = section_filter.lower()
                sections = [s for s in sections if s_filt in s.get("displayName", "").lower()]
            nb_sections.extend((nb, s) for s in sections)

    if not nb_sections:
        return

    # ── Stage 2: list pages across all sections in parallel ────────────────
    pages_to_fetch: list[tuple[dict, dict, dict]] = []  # (notebook, section, page)
    with ThreadPoolExecutor(max_workers=min(len(nb_sections), 8)) as pool:
        fut_to_sec = {pool.submit(_list_pages, access_token, sec["id"], modified_since): (nb, sec)
                      for nb, sec in nb_sections}
        for fut in as_completed(fut_to_sec):
            nb, sec = fut_to_sec[fut]
            sec_name = sec.get("displayName", "Unknown")
            try:
                pages = fut.result()
            except Exception as exc:
                print(f"[onenote] WARN: skipping section '{sec_name}': {exc}", file=sys.stderr)
                continue
            pages_to_fetch.extend((nb, sec, page) for page in pages)

    # Respect max_results cap before spawning content threads
    pages_to_fetch = pages_to_fetch[:max_results]
    if not pages_to_fetch:
        return

    print(f"[onenote] fetching content for {len(pages_to_fetch)} pages "
          f"({_CONTENT_WORKERS} workers)", file=sys.stderr)

    # ── Stage 3: fetch page content in parallel ─────────────────────────────
    def _fetch_one(nb: dict, sec: dict, page: dict) -> Dict[str, Any]:
        raw_html = _fetch_page_content(access_token, page["id"])
        content_text = _onenote_html_to_text(raw_html)
        return {
            "notebook": nb.get("displayName", "Unknown"),
            "section": sec.get("displayName", "Unknown"),
            "page_title": page.get("title", "(no title)"),
            "last_modified": page.get("lastModifiedDateTime", ""),
            "content_text": content_text,
            "source": source_tag,
        }

    records: list[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=_CONTENT_WORKERS) as pool:
        fut_to_page = {pool.submit(_fetch_one, nb, sec, page): page
                       for nb, sec, page in pages_to_fetch}
        for fut in as_completed(fut_to_page):
            page = fut_to_page[fut]
            page_title = page.get("title", "(no title)")
            try:
                records.append(fut.result())
            except Exception as exc:
                print(f"[onenote] WARN: skipping page '{page_title}': {exc}", file=sys.stderr)

    yield from records


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify MS Graph token and OneNote API reachability."""
    try:
        from lib.msgraph import _graph_get  # type: ignore[import]
        access_token = auth_context.get("access_token", "")
        if not access_token:
            return False
        resp = _graph_get(access_token, "/me/onenote/notebooks", {"$top": "1"})
        return "value" in resp
    except Exception as exc:
        print(f"[onenote] health_check failed: {exc}", file=sys.stderr)
        return False
        return False
