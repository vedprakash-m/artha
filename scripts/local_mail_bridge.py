#!/usr/bin/env python3
"""
local_mail_bridge.py — Artha zero-auth local email reader
==========================================================
Reads email from local mail stores without OAuth or credentials:
  - macOS Apple Mail: ~/Library/Mail/**/*.emlx
  - macOS mbox files: ~/Library/Mail/**/*.mbox (used by Mail.app v10+)
  - Standard UNIX mbox: ~/mbox, /var/mail/$USER, etc.

This is a read-only bridge intended for privacy-first users who do not want
to grant Artha cloud OAuth access. No credentials are ever requested.
All data stays on-device.

Usage:
  python scripts/local_mail_bridge.py                      — scan default paths
  python scripts/local_mail_bridge.py --since 2026-03-01   — limit date range
  python scripts/local_mail_bridge.py --limit 100          — cap message count
  python scripts/local_mail_bridge.py --source apple_mail  — explicit source
  python scripts/local_mail_bridge.py --dry-run            — show what would be read

Output: JSONL to stdout (same schema as gmail_fetch.py / msgraph_fetch.py)
  {"id": "...", "subject": "...", "sender": "...", "date": "...",
   "body_text": "...", "source": "local_mail"}

Limitations:
  - Encrypted S/MIME or PGP messages will show as empty body
  - Large attachments are stripped (bodies only)
  - Apple Mail emlx format: bodies are extracted from the raw RFC-822 content
    embedded in the .emlx XML envelope

Ref: standardization.md §8 (Phase 3)
"""

from __future__ import annotations

import argparse
import email
import email.policy
import glob
import hashlib
import json
import mailbox
import os
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Source path discovery
# ---------------------------------------------------------------------------

def discover_apple_mail_paths() -> list[Path]:
    """Return all .emlx and .mbox paths under ~/Library/Mail."""
    base = Path.home() / "Library" / "Mail"
    if not base.exists():
        return []
    paths: list[Path] = []
    paths.extend(base.rglob("*.emlx"))
    paths.extend(base.rglob("*.mbox"))
    return paths


def discover_mbox_paths() -> list[Path]:
    """Return standard UNIX mbox file paths that exist on this machine."""
    candidates = [
        Path.home() / "mbox",
        Path.home() / "Maildir",
        Path(f"/var/mail/{os.getenv('USER', '')}"),
        Path(f"/var/spool/mail/{os.getenv('USER', '')}"),
    ]
    return [p for p in candidates if p.exists()]


# ---------------------------------------------------------------------------
# emlx parsing (Apple Mail proprietary format)
# ---------------------------------------------------------------------------

def _parse_emlx(path: Path) -> dict | None:
    """
    Parse a single .emlx file.

    Apple emlx format: first line is byte-count of the RFC-822 message,
    then the raw message bytes, then an XML plist with metadata.
    """
    try:
        raw = path.read_bytes()
        # First line is the byte length of the embedded RFC-822 message
        newline_idx = raw.index(b"\n")
        msg_length = int(raw[:newline_idx].strip())
        msg_bytes = raw[newline_idx + 1 : newline_idx + 1 + msg_length]
        msg = email.message_from_bytes(msg_bytes, policy=email.policy.default)
        return _email_to_dict(msg, source="apple_mail", path=str(path))
    except Exception:
        return None


def _email_to_dict(msg: email.message.Message, *, source: str, path: str) -> dict | None:
    """Convert a parsed email.Message to the Artha JSONL schema."""
    subject = str(msg.get("Subject", "")) or "(no subject)"
    sender = str(msg.get("From", "")) or ""
    date_str = msg.get("Date", "")
    message_id = msg.get("Message-ID", "") or path

    # Stable ID from message-id or path hash
    stable_id = hashlib.sha1(message_id.encode()).hexdigest()[:16]

    # Parse date
    try:
        dt = parsedate_to_datetime(date_str)
        iso_date = dt.astimezone(timezone.utc).isoformat()
    except Exception:
        iso_date = ""

    # Extract plain-text body
    body_text = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body_text = part.get_content()
                    break
        else:
            if msg.get_content_type() == "text/plain":
                body_text = msg.get_content()
    except Exception:
        body_text = ""

    return {
        "id": stable_id,
        "subject": subject,
        "sender": sender,
        "date": iso_date,
        "body_text": body_text.strip(),
        "source": source,
    }


# ---------------------------------------------------------------------------
# mbox parsing
# ---------------------------------------------------------------------------

def iter_mbox(path: Path, *, since: datetime | None = None) -> Iterator[dict]:
    """Yield parsed messages from an mbox or Maildir path."""
    try:
        if path.is_dir():
            mbox = mailbox.Maildir(str(path), factory=None, create=False)
        else:
            mbox = mailbox.mbox(str(path), factory=None, create=False)
    except Exception:
        return

    for key in mbox.keys():
        try:
            raw_msg = mbox.get(key)
            if raw_msg is None:
                continue
            msg = email.message_from_bytes(raw_msg.as_bytes(), policy=email.policy.default)
            record = _email_to_dict(msg, source="local_mail", path=str(path))
            if record is None:
                continue
            if since and record.get("date"):
                try:
                    msg_dt = datetime.fromisoformat(record["date"])
                    if msg_dt.replace(tzinfo=timezone.utc) < since.replace(tzinfo=timezone.utc):
                        continue
                except Exception:
                    pass
            yield record
        except Exception:
            continue


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read local email (Apple Mail .emlx / mbox) and output JSONL."
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help='Only include messages after this date, e.g. "2026-03-01"',
    )
    parser.add_argument(
        "--limit", type=int, default=500,
        help="Maximum number of messages to emit (default: 500)",
    )
    parser.add_argument(
        "--source", choices=["apple_mail", "mbox", "auto"], default="auto",
        help="Force a specific mail source (default: auto-detect)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print discovered paths without reading messages",
    )
    args = parser.parse_args()

    since: datetime | None = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Invalid --since date: {args.since}", file=sys.stderr)
            sys.exit(1)

    # Discover sources
    apple_paths: list[Path] = []
    mbox_paths: list[Path] = []

    if args.source in ("apple_mail", "auto"):
        apple_paths = discover_apple_mail_paths()
    if args.source in ("mbox", "auto"):
        mbox_paths = discover_mbox_paths()

    if args.dry_run:
        print(f"Apple Mail paths ({len(apple_paths)}):")
        for p in apple_paths[:20]:
            print(f"  {p}")
        if len(apple_paths) > 20:
            print(f"  ... and {len(apple_paths) - 20} more")
        print(f"mbox paths ({len(mbox_paths)}):")
        for p in mbox_paths:
            print(f"  {p}")
        return

    count = 0

    # Apple Mail .emlx
    for path in apple_paths:
        if count >= args.limit:
            break
        if path.suffix == ".emlx":
            record = _parse_emlx(path)
            if record is None:
                continue
            if since and record.get("date"):
                try:
                    msg_dt = datetime.fromisoformat(record["date"])
                    if msg_dt.replace(tzinfo=timezone.utc) < since:
                        continue
                except Exception:
                    pass
            print(json.dumps(record))
            count += 1

    # mbox / Maildir sources
    for path in mbox_paths:
        if count >= args.limit:
            break
        for record in iter_mbox(path, since=since):
            if count >= args.limit:
                break
            print(json.dumps(record))
            count += 1


if __name__ == "__main__":
    main()
