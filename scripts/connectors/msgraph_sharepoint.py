#!/usr/bin/env python3
# pii-guard: ignore-file — entity names / document titles from SharePoint; no personal data fields
"""scripts/connectors/msgraph_sharepoint.py — SharePoint / OneDrive document connector.

Implements the handler contract (fetch + health_check) used by sharepoint_kb_sync.py
and, optionally, pipeline.py.

Fetch strategy:
  - Configured sites: delta queries for incremental sync.
      First run → _fetch_initial (full drive scan + seed delta link).
      Subsequent runs → _fetch_delta (only changed items since last run).
  - /me/drive/sharedWithMe: cursor-based polling (last_seen_shared_at).
      Items returned newest-first; early-exit when cursor date is passed.

State is persisted atomically to state/connectors/sharepoint_docs_state.yaml.

Security ordering (R21/HIGH):
  pii_guard.scan() runs on every extracted text BEFORE yielding to callers.
  Callers must NEVER call kg.add_episode() with raw text that has not passed
  this gate. The gate is inside this module, so caller code is unconditionally safe.

Dedup (R13):
  _is_already_ingested() uses the state YAML as the early gate.
  SQLite documents table is written by sharepoint_kb_sync.py, not here.

Ref: specs/kb-graph.md §10.10–§10.12
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, urljoin, urlsplit

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR    = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_log = logging.getLogger(__name__)

_SP_STATE_FILE = _ROOT_DIR / "state" / "connectors" / "sharepoint_docs_state.yaml"
_ALLOWED_EXTS  = frozenset({".md", ".txt", ".docx"})
_MAX_PAGES = 20
_MAX_ITEMS = 1000
_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# State persistence helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    """Load connector state (dedup + delta links + shared-with-me cursor)."""
    try:
        import yaml
        if _SP_STATE_FILE.exists():
            with open(_SP_STATE_FILE, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        _log.warning("Failed to load SharePoint state: %s", exc)
    return {}


def _save_connector_state_atomic(state: dict) -> None:
    """Persist state atomically (.yaml.tmp → rename pattern)."""
    import yaml
    _SP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SP_STATE_FILE.with_suffix(".yaml.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(state, f, default_flow_style=False, allow_unicode=True)
        tmp.replace(_SP_STATE_FILE)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _item_hold_key(scope: str, item: dict) -> str:
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        return f"{scope}:{item_id}"
    payload = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
    return f"{scope}:malformed:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _record_hold(state: dict, scope: str, item: dict, reason: str) -> None:
    """Persist an explicit retryable disposition without storing document content."""
    key = _item_hold_key(scope, item)
    state.setdefault("held_items", {})[key] = {
        "reason": reason,
        "retryable": True,
        "scope": scope,
        "item_id": item.get("id", "") if isinstance(item.get("id", ""), str) else "",
        "name": item.get("name", "") if isinstance(item.get("name", ""), str) else "",
        "held_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_connector_state_atomic(state)


def _clear_hold(state: dict, scope: str, item: dict) -> None:
    held = state.get("held_items")
    if isinstance(held, dict):
        held.pop(_item_hold_key(scope, item), None)


def _is_already_ingested(drive_item_key: str, state: dict, etag: str | None = None) -> bool:
    """Check whether a drive item has been ingested. etag-first fast path.

    Returns True (skip) when drive_item_key is present AND the etag matches
    (or no etag provided). Returns False when etag changed → re-ingest.
    """
    bucket = state.get("ingested_items", {})
    if drive_item_key not in bucket:
        return False
    if etag is not None:
        old_etag = bucket[drive_item_key].get("etag")
        if old_etag and old_etag != etag:
            return False  # etag changed → must re-ingest
    return True


# ---------------------------------------------------------------------------
# Document text extraction
# ---------------------------------------------------------------------------

def _extract_text(filename: str, content_bytes: bytes) -> str:
    """Convert document bytes to plain text for KB extraction pipeline."""
    lower = filename.lower()
    if lower.endswith(".md") or lower.endswith(".txt"):
        return content_bytes.decode("utf-8", errors="replace")
    if lower.endswith(".docx"):
        return _extract_docx_bytes(content_bytes)
    return ""


def _extract_docx_bytes(content: bytes) -> str:
    """Convert .docx bytes to pseudo-markdown. Skips gracefully if python-docx absent."""
    try:
        from docx import Document  # type: ignore
    except ImportError:
        _log.warning("python-docx not installed — .docx extraction skipped")
        return ""
    import io
    try:
        doc = Document(io.BytesIO(content))
    except Exception as exc:
        _log.warning("Failed to parse .docx bytes: %s", exc)
        return ""

    lines: list[str] = []
    for para in doc.paragraphs:
        if para.style and para.style.name.startswith("Heading"):
            level = int(para.style.name[-1]) if para.style.name[-1].isdigit() else 2
            lines.append(f"{'#' * level} {para.text}")
        elif para.text.strip():
            lines.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Graph API helpers
# ---------------------------------------------------------------------------

def _drive_delta_path(drive_id: str) -> str:
    return f"/v1.0/drives/{quote(drive_id, safe='')}/root/delta"


def _expected_listing_path(initial: str) -> str:
    parts = urlsplit(initial)
    path = parts.path
    if not initial.startswith("/") or parts.netloc or parts.scheme or not path.startswith("/"):
        raise ValueError("Graph listing initial URL must be a relative API path")
    return f"/v1.0{path}"


def _validated_delta_link(delta_link: Any, drive_id: str) -> str:
    from lib.msgraph import _validate_graph_url

    return _validate_graph_url(delta_link, allowed_paths={_drive_delta_path(drive_id)})


def _download_content(token: str, drive_id: str, item_id: str) -> bytes:
    """Download raw file bytes from the Graph API /content endpoint."""
    try:
        import requests as _req
    except ImportError as exc:
        raise RuntimeError(
            "[msgraph_sharepoint] 'requests' package not installed — run: pip install requests"
        ) from exc

    from lib.msgraph import GRAPH_BASE, _get_headers, _validate_graph_url

    content_path = (
        f"/v1.0/drives/{quote(drive_id, safe='')}/items/"
        f"{quote(item_id, safe='')}/content"
    )
    url = f"https://graph.microsoft.com{content_path}"
    current = _validate_graph_url(url, allowed_paths={content_path})
    resp = _req.get(
        current,
        headers=_get_headers(token),
        timeout=60,
        allow_redirects=False,
        stream=True,
    )
    if resp.status_code in {301, 302, 303, 307, 308}:
        location = resp.headers.get("Location")
        if not isinstance(location, str) or not location:
            raise RuntimeError("SharePoint content redirect is invalid")
        current = urljoin(current, location)
        parts = urlsplit(current)
        if (
            parts.scheme != "https"
            or not parts.netloc
            or parts.username is not None
            or parts.fragment
        ):
            raise RuntimeError("SharePoint content redirect is invalid")
        # A provider storage redirect is intentionally unauthenticated.
        resp = _req.get(current, timeout=60, allow_redirects=False, stream=True)
    resp.raise_for_status()
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > _MAX_DOCUMENT_BYTES:
            raise RuntimeError("SharePoint document exceeds the bounded download size")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _fetch_initial(
    token: str,
    drive_id: str,
    paths: list[str] | None,
) -> tuple[list[dict], str | None]:
    """Full initial scan of a drive or specific folder paths.

    Returns (items, delta_link). delta_link is None when scanning specific paths
    (no delta support for path-scoped queries).
    """
    delta_link: str | None = None
    all_items: list[dict]  = []

    if paths:
        for path in paths:
            escaped = path.strip("/")
            initial = f"/drives/{quote(drive_id, safe='')}/root:/{escaped}:/children"
            items, _ = _collect_graph_pages(
                token,
                initial,
                allowed_paths={_expected_listing_path(initial)},
            )
            all_items.extend(items[:_MAX_ITEMS - len(all_items)])
            if len(all_items) >= _MAX_ITEMS:
                raise RuntimeError("SharePoint initial listing result bound reached")
    else:
        # Delta query on drive root — collect all items and seed the delta link
        try:
            all_items, delta_link = _collect_graph_pages(
                token,
                f"/drives/{quote(drive_id, safe='')}/root/delta",
                allowed_paths={_drive_delta_path(drive_id)},
            )
        except Exception as exc:
            _log.warning("Delta query failed on drive %s — falling back to /children: %s", drive_id, exc)
            initial = f"/drives/{quote(drive_id, safe='')}/root/children"
            fallback, _ = _collect_graph_pages(
                token,
                initial,
                allowed_paths={_expected_listing_path(initial)},
            )
            all_items.extend(fallback[:_MAX_ITEMS - len(all_items)])

    return all_items, delta_link


def _fetch_delta(token: str, delta_link: str, drive_id: str) -> tuple[list[dict], str | None]:
    """Incremental delta fetch using a persisted delta link.

    Returns (changed_items, new_delta_link). Caller should persist new_delta_link.
    """
    allowed_paths = {_drive_delta_path(drive_id)}
    return _collect_graph_pages(token, delta_link, allowed_paths=allowed_paths)


def _collect_graph_pages(
    token: str,
    initial: str,
    *,
    allowed_paths: set[str],
) -> tuple[list[dict], str | None]:
    from lib.msgraph import _graph_get, _graph_get_full_url, _validate_graph_url

    items: list[dict] = []
    current: str | None = initial
    first = initial.startswith("/")
    seen: set[str] = set()
    delta_link: str | None = None
    for _ in range(_MAX_PAGES):
        page = (
            _graph_get(token, current)
            if first
            else _graph_get_full_url(token, current, allowed_paths=allowed_paths)
        )
        first = False
        value = page.get("value")
        if not isinstance(value, list):
            raise ValueError("Graph listing page did not contain a value list")
        items.extend(item for item in value if isinstance(item, dict))
        if len(items) > _MAX_ITEMS:
            raise RuntimeError("SharePoint listing result bound reached")
        page_delta_link = page.get("@odata.deltaLink")
        if page_delta_link is not None:
            delta_link = _validate_graph_url(page_delta_link, allowed_paths=allowed_paths)
        current = page.get("@odata.nextLink")
        if not current:
            return items, delta_link
        if not isinstance(current, str) or current in seen:
            raise RuntimeError("SharePoint pagination is invalid or repeated")
        _validate_graph_url(current, allowed_paths=allowed_paths)
        seen.add(current)
    raise RuntimeError("SharePoint pagination limit reached")


# ---------------------------------------------------------------------------
# sharedWithMe polling (§10.11 Path B)
# ---------------------------------------------------------------------------

def _fetch_shared_with_me(
    token: str,
    state: dict,
    *,
    max_results: int = 200,
    source_tag: str = "sharepoint",
) -> Iterator[dict]:
    """Yield docs shared with the user from /me/drive/sharedWithMe.

    Uses last_seen_shared_at as an early-exit cursor. Graph returns items
    newest-first; stops paginating once item dates fall at or before the cursor.
    First run: O(N_all_time). Subsequent runs: O(N_new_since_last_run).

    R21/HIGH: pii_guard.scan() runs on extracted text BEFORE yielding.
    State mutations on ingested_items and shared_with_me cursor happen
    inside this function; caller must persist state after iteration.
    """
    import pii_guard
    scope = "shared_with_me"
    raw_cursor = state.get("shared_with_me", {}).get("last_seen_shared_at")
    cursor: datetime | None = None
    if raw_cursor is not None:
        try:
            cursor = datetime.fromisoformat(str(raw_cursor).replace("Z", "+00:00"))
            if cursor.tzinfo is None:
                raise ValueError("timestamp has no timezone")
        except (TypeError, ValueError):
            _record_hold(
                state,
                f"{scope}:cursor",
                {"id": "cursor", "name": "shared_with_me cursor"},
                "cursor_invalid",
            )
            return

    items, _ = _collect_graph_pages(
        token,
        "/me/drive/sharedWithMe",
        allowed_paths={"/v1.0/me/drive/sharedWithMe"},
    )
    yielded = 0
    newest_seen: tuple[datetime, str] | None = None
    complete = True
    for item in items:
        shared_at = item.get("shared", {}).get("sharedDateTime")
        try:
            shared_time = datetime.fromisoformat(str(shared_at).replace("Z", "+00:00"))
            if not isinstance(shared_at, str) or shared_time.tzinfo is None:
                raise ValueError("timestamp has no timezone")
        except (TypeError, ValueError):
            _record_hold(state, scope, item, "shared_time_invalid")
            complete = False
            continue
        if cursor is not None and shared_time <= cursor:
            _clear_hold(state, scope, item)
            continue
        if newest_seen is None or shared_time > newest_seen[0]:
            newest_seen = (shared_time, shared_at)

        remote = item.get("remoteItem")
        if not isinstance(remote, dict):
            _record_hold(state, scope, item, "remote_item_invalid")
            complete = False
            continue

        drive_id = remote.get("parentReference", {}).get("driveId", "")
        item_id  = remote.get("id", "")
        if not isinstance(drive_id, str) or not drive_id or not isinstance(item_id, str) or not item_id:
            _record_hold(state, scope, item, "remote_identity_invalid")
            complete = False
            continue

        name = item.get("name", "")
        if not isinstance(name, str):
            _record_hold(state, scope, item, "item_name_invalid")
            complete = False
            continue
        if not any(name.lower().endswith(ext) for ext in _ALLOWED_EXTS):
            _clear_hold(state, scope, item)
            continue

        etag = item.get("eTag", "")
        drive_item_key = f"{drive_id}:{item_id}"

        if _is_already_ingested(drive_item_key, state, etag=etag):
            _clear_hold(state, scope, item)
            continue
        if yielded >= max_results:
            _record_hold(state, scope, item, "result_limit")
            complete = False
            continue

        try:
            content_bytes = _download_content(token, drive_id, item_id)
        except Exception as exc:
            _log.warning("Failed to download shared item '%s': %s", item.get("name", "?"), exc)
            _record_hold(state, scope, item, "download_failed")
            complete = False
            continue

        try:
            content_text = _extract_text(name, content_bytes)
        except Exception as exc:
            _log.warning("Failed to extract shared item '%s': %s", item.get("name", "?"), exc)
            _record_hold(state, scope, item, "extraction_failed")
            complete = False
            continue
        if not content_text.strip():
            _record_hold(state, scope, item, "extraction_empty")
            complete = False
            continue

        # R21/HIGH: PII scan BEFORE the caller can call add_episode()
        try:
            pii_found, pii_types = pii_guard.scan(content_text)
        except Exception as exc:
            _log.warning("PII scan failed for shared item '%s': %s", item.get("name", "?"), exc)
            _record_hold(state, scope, item, "pii_scan_failed")
            complete = False
            continue
        if pii_found:
            _log.warning(
                "Holding shared doc '%s': PII detected (%s)",
                item.get("name", "?"),
                ", ".join(pii_types.keys()),
            )
            _record_hold(state, scope, item, "pii_detected")
            complete = False
            continue

        content_hash = hashlib.sha256(content_bytes).hexdigest()
        shared_by    = (
            item.get("shared", {})
                .get("sharedBy", {})
                .get("user", {})
                .get("displayName", "")
        )

        yield {
            "id":            item_id,
            "date_iso":      item.get("lastModifiedDateTime", ""),
            "subject":       item.get("name", ""),
            "body":          content_text,
            "name":          item.get("name", ""),
            "web_url":       item.get("webUrl", remote.get("webUrl", "")),
            "drive_item_id": drive_item_key,
            "content_text":  content_text,
            "content_hash":  content_hash,
            "etag":          etag,
            "shared_by":     shared_by,
            "modified_at":   item.get("lastModifiedDateTime", ""),
            "site_name":     "",
            "library_path":  remote.get("parentReference", {}).get("path", ""),
            "source":        source_tag or "sharepoint",
            "provider_source": "shared_with_me",
        }

        # Update dedup state for this item
        state.setdefault("ingested_items", {})[drive_item_key] = {
            "content_hash": content_hash,
            "ingested_at":  datetime.now(timezone.utc).isoformat(),
            "etag":         etag,
            "name":         item.get("name", ""),
        }
        _clear_hold(state, scope, item)
        _save_connector_state_atomic(state)
        yielded += 1

    if complete and newest_seen is not None:
        state.setdefault("shared_with_me", {})["last_seen_shared_at"] = newest_seen[1]


# ---------------------------------------------------------------------------
# Primary handler contract
# ---------------------------------------------------------------------------

def fetch(
    auth_context: dict,
    config: dict | None = None,
    *,
    since: str = "",
    max_results: int = 200,
    source_tag: str = "sharepoint",
    **kwargs,
) -> Iterator[dict]:
    """Fetch documents from configured SharePoint sites and sharedWithMe.

    Implements the handler contract: yields clean dicts consumed by
    sharepoint_kb_sync.py. All docs have already passed pii_guard.scan().

    Delta queries provide incremental sync — only returns documents that
    changed since the last successful fetch per drive.
    """
    import pii_guard
    from lib.msgraph import _graph_get

    if config is None:
        config = {
            "sites": kwargs.get("sites", []),
            "include_shared_with_me": kwargs.get("include_shared_with_me", True),
        }
    if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results <= 0:
        raise ValueError("max_results must be a positive integer")
    token = auth_context["access_token"]
    state = _load_state()
    delta_links: dict = state.setdefault("delta_links", {})
    yielded = 0

    # --- Configured sites ---------------------------------------------------
    for site_cfg in config.get("sites", []):
        site_path = site_cfg.get("site", "")
        if not site_path:
            continue

        try:
            site_info = _graph_get(token, f"/sites/{site_path}")
        except Exception as exc:
            _log.warning("Failed to fetch site '%s': %s", site_path, exc)
            continue

        site_id      = site_info.get("id", "")
        site_display = site_info.get("displayName", site_path)

        for lib_cfg in site_cfg.get("libraries", []):
            lib_name     = lib_cfg.get("name", "Documents")
            allowed_exts = set(lib_cfg.get("file_types", list(_ALLOWED_EXTS)))
            paths        = lib_cfg.get("paths") or []

            # Resolve the drive for this library
            try:
                drives_resp = _graph_get(token, f"/sites/{site_id}/drives")
                drive = next(
                    (d for d in drives_resp.get("value", []) if d.get("name") == lib_name),
                    None,
                )
                if drive is None:
                    _log.warning("Drive '%s' not found in site '%s'", lib_name, site_path)
                    continue
                drive_id = drive["id"]
            except Exception as exc:
                _log.warning("Failed to resolve drive '%s/%s': %s", site_path, lib_name, exc)
                continue

            delta_key  = f"{site_id}:{drive_id}"
            delta_link = delta_links.get(delta_key)

            try:
                if delta_link:
                    items, new_delta = _fetch_delta(token, delta_link, drive_id)
                else:
                    items, new_delta = _fetch_initial(token, drive_id, paths or None)
            except Exception as exc:
                _log.warning("Failed to fetch drive '%s': %s", drive_id, exc)
                continue

            scope = f"configured:{delta_key}"
            complete = True
            for item in items:
                name = item.get("name", "")
                if not isinstance(name, str):
                    _record_hold(state, scope, item, "item_name_invalid")
                    complete = False
                    continue

                # Skip deleted items (graph delta includes deletion markers)
                if item.get("deleted"):
                    _clear_hold(state, scope, item)
                    continue

                if not any(name.lower().endswith(ext) for ext in allowed_exts):
                    _clear_hold(state, scope, item)
                    continue

                etag           = item.get("eTag", "")
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    _record_hold(state, scope, item, "item_identity_invalid")
                    complete = False
                    continue
                drive_item_key = f"{drive_id}:{item_id}"

                if _is_already_ingested(drive_item_key, state, etag=etag):
                    _clear_hold(state, scope, item)
                    continue

                try:
                    content_bytes = _download_content(token, drive_id, item_id)
                except Exception as exc:
                    _log.warning("Failed to download '%s': %s", name, exc)
                    _record_hold(state, scope, item, "download_failed")
                    complete = False
                    continue

                try:
                    content_text = _extract_text(name, content_bytes)
                except Exception as exc:
                    _log.warning("Failed to extract '%s': %s", name, exc)
                    _record_hold(state, scope, item, "extraction_failed")
                    complete = False
                    continue
                if not content_text.strip():
                    _record_hold(state, scope, item, "extraction_empty")
                    complete = False
                    continue

                # R21/HIGH: PII scan BEFORE yielding
                try:
                    pii_found, pii_types = pii_guard.scan(content_text)
                except Exception as exc:
                    _log.warning("Failed to scan '%s' for PII: %s", name, exc)
                    _record_hold(state, scope, item, "pii_scan_failed")
                    complete = False
                    continue
                if pii_found:
                    _log.warning(
                        "Skipping '%s': PII detected (%s)",
                        name, ", ".join(pii_types.keys()),
                    )
                    _record_hold(state, scope, item, "pii_detected")
                    complete = False
                    continue

                content_hash = hashlib.sha256(content_bytes).hexdigest()

                doc: dict = {
                    "id":            item_id,
                    "date_iso":      item.get("lastModifiedDateTime", ""),
                    "subject":       name,
                    "body":          content_text,
                    "name":          name,
                    "site_name":     site_display,
                    "library_path":  item.get("parentReference", {}).get("path", ""),
                    "web_url":       item.get("webUrl", ""),
                    "content_text":  content_text,
                    "modified_by":   (
                        item.get("lastModifiedBy", {})
                            .get("user", {})
                            .get("displayName", "")
                    ),
                    "modified_at":   item.get("lastModifiedDateTime", ""),
                    "etag":          etag,
                    "content_hash":  content_hash,
                    "drive_item_id": drive_item_key,
                    "source":        source_tag or "sharepoint",
                }

                yield doc
                yielded += 1

                # Update dedup state and persist after each page
                state.setdefault("ingested_items", {})[drive_item_key] = {
                    "content_hash": content_hash,
                    "ingested_at":  datetime.now(timezone.utc).isoformat(),
                    "etag":         etag,
                    "name":         name,
                }
                _clear_hold(state, scope, item)
                _save_connector_state_atomic(state)
                if yielded >= max_results:
                    return

            if complete and new_delta:
                try:
                    delta_links[delta_key] = _validated_delta_link(
                        new_delta, drive_id)
                except ValueError:
                    _record_hold(
                        state,
                        f"{scope}:cursor",
                        {"id": "cursor", "name": "configured drive cursor"},
                        "cursor_invalid",
                    )
                else:
                    _save_connector_state_atomic(state)

    # --- sharedWithMe -------------------------------------------------------
    if config.get("include_shared_with_me", True):
        for document in _fetch_shared_with_me(
            token,
            state,
            max_results=max_results - yielded,
            source_tag=source_tag,
        ):
            yield document
            yielded += 1
            if yielded >= max_results:
                break
        _save_connector_state_atomic(state)

    # Update last_run timestamp
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    _save_connector_state_atomic(state)


def health_check(auth_context: dict, config: dict | None = None) -> bool:
    """Verify Graph API connectivity and sufficient authentication."""
    try:
        from lib.msgraph import _graph_get
        token = auth_context["access_token"]
        me    = _graph_get(token, "/me", params={"$select": "displayName"})
        return bool(me.get("displayName"))
    except Exception as exc:
        _log.warning("SharePoint connector health_check failed: %s", exc)
        return False
