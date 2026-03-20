"""
scripts/connectors/imessage_local.py — iMessage local database connector (macOS only).

Reads the iMessage chat.db SQLite database and yields recent messages as JSONL
records for inclusion in the catch-up pipeline.

Platform: macOS ONLY. iMessage is not available on Windows or Linux.
Path:     ~/Library/Messages/chat.db

Access requirements:
  - macOS Full Disk Access must be granted to the terminal app
    (System Settings → Privacy & Security → Full Disk Access → Terminal / iTerm)
  - The DB is read-only; this connector never modifies it.

Output schema (per record):
  {id, contact_name, phone, timestamp, date_iso, direction, snippet, source, group_name, is_group}

Ref: config/connectors.yaml → imessage_local
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator

logger = logging.getLogger(__name__)

# iMessage DB location (macOS only)
_IMESSAGE_DB = Path.home() / "Library" / "Messages" / "chat.db"

# Apple NSDate epoch offset (seconds between 1970-01-01 and 2001-01-01)
_APPLE_EPOCH_OFFSET = 978_307_200

# Some iMessage timestamps are in nanoseconds (macOS 10.13+)
_NANOSECOND_THRESHOLD = 1_000_000_000_000

# Temp copy to avoid WAL locking
_TMP_DB = Path(os.environ.get("TMPDIR", "/tmp")) / "artha_imessage_snap.sqlite"


def _ts_to_datetime(ts: int | float | None) -> datetime | None:
    """Convert iMessage timestamp to UTC datetime.

    iMessage uses Apple epoch (2001-01-01). macOS 10.13+ stores timestamps
    in nanoseconds; older versions use seconds.
    """
    if ts is None or ts == 0:
        return None
    try:
        # Detect nanosecond vs second precision
        if abs(ts) > _NANOSECOND_THRESHOLD:
            ts = ts / 1_000_000_000  # convert ns → s
        return datetime.fromtimestamp(ts + _APPLE_EPOCH_OFFSET, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _parse_since(since: str) -> datetime:
    """Parse ISO-8601 or relative offset into a UTC datetime."""
    if since.endswith("d"):
        days = int(since[:-1])
        return datetime.now(timezone.utc) - timedelta(days=days)
    if since.endswith("h"):
        hours = int(since[:-1])
        return datetime.now(timezone.utc) - timedelta(hours=hours)
    return datetime.fromisoformat(since).astimezone(timezone.utc)


def _resolve_contact_name(phone_or_email: str) -> str:
    """Best-effort contact name resolution from the identifier.

    On macOS, the Contacts framework would be ideal, but we keep this
    dependency-free. Returns the raw identifier; the comms domain prompt
    can enrich from state/contacts.md later.
    """
    if not phone_or_email:
        return "Unknown"
    return phone_or_email


def fetch(
    *,
    since: str,
    max_results: int = 200,
    auth_context: Dict[str, Any] | None = None,
    source_tag: str = "imessage",
    include_groups: bool = True,
    snippet_max_chars: int = 300,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield recent iMessage/SMS messages as standardized records.

    Args:
        since:            ISO-8601 timestamp or relative offset ("7d", "48h")
        max_results:      Maximum number of messages to yield
        auth_context:     Not used (local DB, no auth needed)
        source_tag:       Value for the 'source' field
        include_groups:   Whether to include group chat messages
        snippet_max_chars: Max characters for message snippet
    """
    if platform.system() != "Darwin":
        logger.info("iMessage connector is macOS-only — skipping on %s", platform.system())
        return

    if not _IMESSAGE_DB.exists():
        logger.info(
            "iMessage DB not found at %s — ensure Full Disk Access is granted "
            "to this terminal app (System Settings → Privacy & Security → Full Disk Access)",
            _IMESSAGE_DB,
        )
        return

    # Copy to temp to avoid WAL locking the live DB
    try:
        shutil.copy2(_IMESSAGE_DB, _TMP_DB)
    except (OSError, PermissionError) as exc:
        logger.warning(
            "Cannot copy iMessage DB to temp: %s — "
            "grant Full Disk Access to terminal in System Settings",
            exc,
        )
        return

    since_dt = _parse_since(since)
    # Convert since to Apple epoch timestamp (seconds)
    since_ts = since_dt.timestamp() - _APPLE_EPOCH_OFFSET
    # iMessage uses nanoseconds on modern macOS
    since_ts_ns = int(since_ts * 1_000_000_000)

    con = sqlite3.connect(str(_TMP_DB))
    try:
        cur = con.cursor()

        # Detect timestamp precision by checking a sample row
        cur.execute("SELECT date FROM message ORDER BY date DESC LIMIT 1")
        sample = cur.fetchone()
        use_nanoseconds = sample and sample[0] and abs(sample[0]) > _NANOSECOND_THRESHOLD
        since_param = since_ts_ns if use_nanoseconds else since_ts

        # Build group filter
        group_filter = "" if include_groups else "AND c.group_id IS NULL"

        cur.execute(f"""
            SELECT
                m.ROWID,
                m.text,
                m.date,
                m.is_from_me,
                h.id AS handle_id,
                c.display_name,
                c.group_id,
                c.chat_identifier
            FROM message m
            LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            LEFT JOIN chat c ON c.ROWID = cmj.chat_id
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.date > ?
              AND m.text IS NOT NULL
              AND LENGTH(m.text) > 0
              {group_filter}
            ORDER BY m.date DESC
            LIMIT ?
        """, (since_param, max_results))

        for rowid, text, msg_ts, is_from_me, handle_id, display_name, group_id, chat_id in cur.fetchall():
            msg_dt = _ts_to_datetime(msg_ts)
            if msg_dt is None:
                continue

            is_group = bool(group_id)
            contact = handle_id or chat_id or "Unknown"
            snippet = (text or "").strip()[:snippet_max_chars]

            # Extract phone number (strip tel: prefix if present)
            phone = ""
            if handle_id and not handle_id.startswith("chat"):
                phone = handle_id.lstrip("tel:").strip()

            record: Dict[str, Any] = {
                "id": f"imsg-{rowid}",
                "contact_name": display_name or _resolve_contact_name(contact),
                "phone": phone,
                "timestamp": msg_dt.isoformat(),
                "date_iso": msg_dt.isoformat(),
                "direction": "sent" if is_from_me else "received",
                "snippet": snippet,
                "source": source_tag,
                "group_name": display_name if is_group else "",
                "is_group": is_group,
            }
            yield record

    except sqlite3.OperationalError as exc:
        logger.warning("iMessage DB query failed: %s — check Full Disk Access permissions", exc)
    finally:
        con.close()
        try:
            _TMP_DB.unlink(missing_ok=True)
        except OSError:
            pass


def health_check(auth_context: Dict[str, Any] | None = None) -> bool:
    """Check if iMessage DB is accessible (macOS only)."""
    if platform.system() != "Darwin":
        return False
    return _IMESSAGE_DB.exists()
