#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/state_snapshot.py — Pre-write state snapshots and undo for Artha.

Before each ``state/*.md`` write, ``WriteGuardMiddleware`` calls
:func:`snapshot` to persist the current file content into
``tmp/state_snapshots/``.  The ``undo`` command calls :func:`restore_latest`
to roll back the most recent write to a domain.

Filename convention::

    tmp/state_snapshots/<domain>_<UTC-YYYYMMDDTHHmmss>.snap

Retention policy:
- Keep the last 5 snapshots per domain (``max_keep=5``).
- Auto-prune snapshots older than 24 hours (``max_age_hours=24.0``).
- ``tmp/`` is gitignored — snapshots are never committed.

Encrypted files (``*.md.age``) MUST only be snapshotted in their encrypted
form.  This module operates on the pre-write content string regardless of
whether the file is encrypted downstream — the caller is responsible for
passing only encrypted bytes for vault-protected domains.

Spec: specs/agent-fw.md §AFW-6 — Session Rewind / Undo
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.common import ARTHA_DIR

# ---------------------------------------------------------------------------
# Retention policy constants
# ---------------------------------------------------------------------------

_DEFAULT_MAX_KEEP: int = 5
_DEFAULT_MAX_AGE_HOURS: float = 24.0

# Snapshot filename pattern: <safe_domain>_<YYYYMMDDTHHmmss>.snap
_TS_FORMAT = "%Y%m%dT%H%M%S"
_SNAP_RE = re.compile(r"^(.+)_(\d{8}T\d{6})\.snap$")

# Characters allowed in domain name component of filename
_SAFE_DOMAIN_RE = re.compile(r"[^a-zA-Z0-9_\-]")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _snap_dir(artha_dir: Optional[Path] = None) -> Path:
    root = artha_dir if artha_dir is not None else ARTHA_DIR
    return root / "tmp" / "state_snapshots"


def _safe_domain(domain: str) -> str:
    return _SAFE_DOMAIN_RE.sub("_", domain)


def _ts_now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def snapshot(
    domain: str,
    content: str,
    artha_dir: Optional[Path] = None,
    max_keep: int = _DEFAULT_MAX_KEEP,
) -> Optional[Path]:
    """Create a timestamped snapshot of *content* for *domain*.

    Prunes excess/expired snapshots after writing.

    Args:
        domain: Domain name (e.g. ``"finance"``).  Used as the filename prefix.
        content: Current file content to preserve.  Must be non-empty.
        artha_dir: Artha project root (default: :data:`~lib.common.ARTHA_DIR`).
        max_keep: Maximum snapshots to retain per domain.

    Returns:
        Path to the new snapshot, or ``None`` if *content* is empty or on any
        filesystem error.
    """
    if not content or not content.strip():
        return None
    try:
        sdir = _snap_dir(artha_dir)
        sdir.mkdir(parents=True, exist_ok=True)
        snap_path = sdir / f"{_safe_domain(domain)}_{_ts_now()}.snap"
        snap_path.write_text(content, encoding="utf-8")
        prune(domain, artha_dir=artha_dir, max_keep=max_keep)
        return snap_path
    except Exception:  # noqa: BLE001
        return None


def list_snapshots(
    domain: str,
    artha_dir: Optional[Path] = None,
) -> list[Path]:
    """Return all snapshots for *domain*, newest first.

    Args:
        domain: Domain name.
        artha_dir: Artha project root.

    Returns:
        Sorted list of snapshot paths, newest first.  Empty list if the
        snapshot directory does not exist.
    """
    sdir = _snap_dir(artha_dir)
    if not sdir.exists():
        return []
    prefix = _safe_domain(domain)
    matches = [p for p in sdir.glob(f"{prefix}_*.snap") if _SNAP_RE.match(p.name)]
    # Lexicographic sort on filename works because timestamp is zero-padded ISO
    return sorted(matches, key=lambda p: p.name, reverse=True)


def restore_latest(
    domain: str,
    artha_dir: Optional[Path] = None,
) -> Optional[str]:
    """Return the content of the most recent snapshot for *domain*.

    Args:
        domain: Domain name.
        artha_dir: Artha project root.

    Returns:
        Content string, or ``None`` if no snapshot exists or on read error.
    """
    snaps = list_snapshots(domain, artha_dir=artha_dir)
    if not snaps:
        return None
    try:
        return snaps[0].read_text(encoding="utf-8")
    except OSError:
        return None


def prune(
    domain: str,
    artha_dir: Optional[Path] = None,
    max_keep: int = _DEFAULT_MAX_KEEP,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
) -> int:
    """Delete excess and expired snapshots for *domain*.

    A snapshot is deleted if it is beyond position ``max_keep`` in the
    newest-first list, OR if its embedded timestamp is older than
    ``max_age_hours`` hours from now.

    Args:
        domain: Domain name.
        artha_dir: Artha project root.
        max_keep: Maximum snapshots to retain.
        max_age_hours: Age limit in hours.

    Returns:
        Number of snapshots deleted.
    """
    snaps = list_snapshots(domain, artha_dir=artha_dir)
    now = datetime.now(timezone.utc)
    deleted = 0

    for i, snap in enumerate(snaps):
        remove = i >= max_keep  # beyond retention limit
        if not remove:
            m = _SNAP_RE.match(snap.name)
            if m:
                try:
                    ts = datetime.strptime(m.group(2), _TS_FORMAT).replace(tzinfo=timezone.utc)
                    if (now - ts).total_seconds() / 3600.0 > max_age_hours:
                        remove = True
                except ValueError:
                    pass
        if remove:
            try:
                snap.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                pass

    return deleted
