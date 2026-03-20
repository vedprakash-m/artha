"""
scripts/connectors/whatsapp_local.py — WhatsApp local database connector.

Reads the WhatsApp ChatStorage.sqlite (macOS) or Chromium IndexedDB
(Windows WhatsApp Desktop) and yields recent messages as JSONL records for
inclusion in the catch-up pipeline.

Platform paths:
  macOS:   ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
  Windows: %LOCALAPPDATA%/Packages/5319275A.WhatsAppDesktop_cv1g1gvanyjgm/
           LocalCache/EBWebView/Default/IndexedDB/
           https_web.whatsapp.com_0.indexeddb.leveldb

On Windows the message *body text* is encrypted at rest (AES, local key).
The connector extracts plaintext metadata: sender, recipient, timestamp,
direction, group name, and message type.  This is enough for a catch-up
summary ("N new messages in group X, last from Y at HH:MM").

On macOS the full message text is available from ChatStorage.sqlite.

Output schema (per record):
  {id, contact_name, phone, timestamp, date_iso, direction, snippet,
   source, group_name, is_group, msg_type}

This connector is read-only and never modifies the WhatsApp database.
Ref: config/connectors.yaml → whatsapp_local
"""
from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator

logger = logging.getLogger(__name__)

# ── macOS paths ──────────────────────────────────────────────────────────
_APPLE_EPOCH_OFFSET = 978_307_200
_MACOS_DB = Path.home() / "Library" / "Group Containers" / \
    "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"

# ── Windows paths (Store / UWP WhatsApp Desktop) ────────────────────────
_WIN_WA_PKG = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"

_WIN_IDB_DIR: Path | None = None
_WIN_BLOB_DIR: Path | None = None
if platform.system() == "Windows":
    _local = Path(os.environ.get("LOCALAPPDATA", ""))
    _pkg_base = _local / "Packages" / _WIN_WA_PKG
    _idb_parent = _pkg_base / "LocalCache" / "EBWebView" / "Default" / "IndexedDB"
    _idb_dir = _idb_parent / "https_web.whatsapp.com_0.indexeddb.leveldb"
    _blob_dir = _idb_parent / "https_web.whatsapp.com_0.indexeddb.blob"
    if _idb_dir.exists():
        _WIN_IDB_DIR = _idb_dir
        _WIN_BLOB_DIR = _blob_dir if _blob_dir.exists() else None

_TMP_DB = Path(os.environ.get("TEMP", "/tmp")) / "artha_wa_chat_snap.sqlite"

# WhatsApp IndexedDB layout (DB 3 is the main store)
_WA_DB_ID = 3
_STORE_CONTACT = 4
_STORE_CHAT = 7
_STORE_MESSAGE = 8
_STORE_GROUP_META = 21


# ─────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────
def _parse_since(since: str) -> datetime:
    if since.endswith("d"):
        return datetime.now(timezone.utc) - timedelta(days=int(since[:-1]))
    if since.endswith("h"):
        return datetime.now(timezone.utc) - timedelta(hours=int(since[:-1]))
    return datetime.fromisoformat(since).astimezone(timezone.utc)


def _jid_phone(jid_str: str) -> str:
    """Extract raw phone number from a JID string."""
    return re.sub(r"@(s\.whatsapp\.net|c\.us|lid)$", "", jid_str)


# ─────────────────────────────────────────────────────────────────────────
# Windows: Chromium IndexedDB via ccl_chromium_reader
# ─────────────────────────────────────────────────────────────────────────
def _build_name_map(idb) -> Dict[str, str]:
    """Build JID/LID → human-readable name map from contacts, chats, groups."""
    names: Dict[str, str] = {}

    # Contacts (Store 4): pushname, name, shortName, displayNameLID
    for rec in idb.iterate_records(_WA_DB_ID, _STORE_CONTACT, live_only=True):
        if not isinstance(rec.value, dict):
            continue
        jid = str(rec.key).strip("<>").replace("IdbKey ", "")
        name = (
            rec.value.get("name")
            or rec.value.get("shortName")
            or rec.value.get("pushname")
            or rec.value.get("verifiedName")
        )
        if name and name != "<Undefined>":
            names[jid] = name

    # Chats (Store 7): group names
    for rec in idb.iterate_records(_WA_DB_ID, _STORE_CHAT, live_only=True):
        if not isinstance(rec.value, dict):
            continue
        jid = str(rec.key).strip("<>").replace("IdbKey ", "")
        name = rec.value.get("name") or rec.value.get("formattedTitle")
        if name and name != "<Undefined>" and jid not in names:
            names[jid] = name

    # Group metadata (Store 21): subject
    for rec in idb.iterate_records(_WA_DB_ID, _STORE_GROUP_META, live_only=True):
        if not isinstance(rec.value, dict):
            continue
        jid = str(rec.key).strip("<>").replace("IdbKey ", "")
        subj = rec.value.get("subject")
        if subj:
            names[jid] = subj

    return names


def _resolve_name(jid: str, names: Dict[str, str]) -> str:
    if jid in names:
        return names[jid]
    # Try without LID suffix
    bare = _jid_phone(jid)
    return names.get(bare, bare or "Unknown")


def _fetch_windows_idb(
    *,
    since_dt: datetime,
    max_results: int,
    source_tag: str,
    include_groups: bool,
) -> Iterator[Dict[str, Any]]:
    """Read WhatsApp messages from Windows Chromium IndexedDB."""
    try:
        import ccl_chromium_reader.ccl_chromium_indexeddb as idb_mod
    except ImportError:
        logger.warning(
            "ccl_chromium_reader not installed — run: "
            "pip install git+https://github.com/cclgroupltd/ccl_chromium_reader.git"
        )
        return

    idb = idb_mod.IndexedDb(str(_WIN_IDB_DIR), str(_WIN_BLOB_DIR) if _WIN_BLOB_DIR else None)
    try:
        names = _build_name_map(idb)
        since_ts = since_dt.timestamp()
        collected: list[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        _SKIP_TYPES = {
            "e2e_notification", "gp2", "notification_template",
            "ciphertext", "revoked",
        }

        for rec in idb.iterate_records(_WA_DB_ID, _STORE_MESSAGE, live_only=True):
            if not isinstance(rec.value, dict):
                continue

            msg = rec.value
            t = msg.get("t")
            if not isinstance(t, (int, float)) or t < since_ts:
                continue

            msg_type = msg.get("type", "")
            if msg_type in _SKIP_TYPES:
                continue

            from_jid = msg.get("from", "")
            from_str = str(from_jid) if not isinstance(from_jid, dict) else from_jid.get("_serialized", str(from_jid))

            # Skip status broadcasts
            if "status@broadcast" in from_str:
                continue

            is_group = "@g.us" in from_str
            if is_group and not include_groups:
                continue

            # Determine direction
            msg_id_str = msg.get("id", "")
            is_from_me = str(msg_id_str).startswith("true_")

            # Author (for group messages)
            author_jid = ""
            author_obj = msg.get("author")
            if isinstance(author_obj, dict):
                author_jid = author_obj.get("_serialized", "")
            elif isinstance(author_obj, str) and author_obj != "<Undefined>":
                author_jid = author_obj

            # Resolve names
            group_name = _resolve_name(from_str, names) if is_group else ""
            if is_group and author_jid:
                contact_name = _resolve_name(author_jid, names)
            else:
                contact_name = _resolve_name(from_str, names)

            msg_dt = datetime.fromtimestamp(t, tz=timezone.utc)

            record: Dict[str, Any] = {
                "id": f"wa-{msg.get('rowId', len(collected))}",
                "contact_name": contact_name,
                "phone": _jid_phone(author_jid) if author_jid else _jid_phone(from_str),
                "timestamp": msg_dt.isoformat(),
                "date_iso": msg_dt.isoformat(),
                "direction": "sent" if is_from_me else "received",
                "snippet": f"[{msg_type}]",  # body encrypted; type is best we have
                "source": source_tag,
                "group_name": group_name,
                "is_group": is_group,
                "msg_type": msg_type,
            }
            if record["id"] not in seen_ids:
                seen_ids.add(record["id"])
                collected.append(record)

        # Sort by timestamp descending and yield top N
        collected.sort(key=lambda r: r["timestamp"], reverse=True)
        for rec in collected[:max_results]:
            yield rec
    finally:
        idb.close()


# ─────────────────────────────────────────────────────────────────────────
# macOS: ChatStorage.sqlite
# ─────────────────────────────────────────────────────────────────────────
def _ts_to_datetime(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts + _APPLE_EPOCH_OFFSET, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _fetch_macos_sqlite(
    *,
    since_dt: datetime,
    max_results: int,
    source_tag: str,
    include_groups: bool,
    snippet_max_chars: int,
) -> Iterator[Dict[str, Any]]:
    """Read WhatsApp messages from macOS ChatStorage.sqlite."""
    try:
        shutil.copy2(_MACOS_DB, _TMP_DB)
    except (OSError, PermissionError) as exc:
        logger.warning("Cannot copy WhatsApp DB to temp: %s — skipping", exc)
        return

    since_ts = since_dt.timestamp() - _APPLE_EPOCH_OFFSET
    con = sqlite3.connect(str(_TMP_DB))
    try:
        cur = con.cursor()
        session_filter = "s.ZSESSIONTYPE IN (0, 1)" if include_groups else "s.ZSESSIONTYPE = 0"
        cur.execute(f"""
            SELECT m.Z_PK, s.ZPARTNERNAME,
                   REPLACE(REPLACE(s.ZCONTACTJID, '@s.whatsapp.net', ''), '@lid', '') AS phone,
                   m.ZMESSAGEDATE, m.ZISFROMME,
                   SUBSTR(m.ZTEXT, 1, ?) AS snippet, s.ZSESSIONTYPE
            FROM ZWAMESSAGE m
            JOIN ZWACHATSESSION s ON m.ZCHATSESSION = s.Z_PK
            WHERE m.ZMESSAGEDATE > ?
              AND m.ZTEXT IS NOT NULL AND LENGTH(m.ZTEXT) > 0
              AND {session_filter}
            ORDER BY m.ZMESSAGEDATE DESC
            LIMIT ?
        """, (snippet_max_chars, since_ts, max_results))

        for row_pk, partner_name, phone, msg_ts, is_from_me, snippet, session_type in cur.fetchall():
            msg_dt = _ts_to_datetime(msg_ts)
            if msg_dt is None:
                continue
            is_group = session_type == 1
            yield {
                "id": f"wa-{row_pk}",
                "contact_name": partner_name or phone or "Unknown",
                "phone": phone if not is_group else "",
                "timestamp": msg_dt.isoformat(),
                "date_iso": msg_dt.isoformat(),
                "direction": "sent" if is_from_me else "received",
                "snippet": (snippet or "").strip()[:snippet_max_chars],
                "source": source_tag,
                "group_name": partner_name if is_group else "",
                "is_group": is_group,
                "msg_type": "chat",
            }
    except sqlite3.OperationalError as exc:
        logger.warning("WhatsApp DB query failed: %s", exc)
    finally:
        con.close()
        try:
            _TMP_DB.unlink(missing_ok=True)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────
# Public API (ConnectorHandler protocol)
# ─────────────────────────────────────────────────────────────────────────
def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any] | None = None,
    source_tag: str = "whatsapp",
    include_groups: bool = True,
    snippet_max_chars: int = 300,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield recent WhatsApp messages as standardized records."""
    since_dt = _parse_since(since)
    system = platform.system()

    if system == "Darwin" and _MACOS_DB.exists():
        yield from _fetch_macos_sqlite(
            since_dt=since_dt,
            max_results=max_results,
            source_tag=source_tag,
            include_groups=include_groups,
            snippet_max_chars=snippet_max_chars,
        )
    elif system == "Windows" and _WIN_IDB_DIR is not None:
        yield from _fetch_windows_idb(
            since_dt=since_dt,
            max_results=max_results,
            source_tag=source_tag,
            include_groups=include_groups,
        )
    else:
        logger.info(
            "WhatsApp DB not found — connector is platform-dependent "
            "(macOS: ChatStorage.sqlite, Windows: IndexedDB). Skipping."
        )


def health_check(auth_context: Dict[str, Any] | None = None) -> bool:
    """Check if the WhatsApp database is accessible."""
    system = platform.system()
    if system == "Darwin":
        return _MACOS_DB.exists()
    if system == "Windows":
        return _WIN_IDB_DIR is not None
    return False
