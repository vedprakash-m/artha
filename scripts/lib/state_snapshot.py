#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""Encrypted, machine-local pre-write snapshots for State Files."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.common import ARTHA_DIR

# ---------------------------------------------------------------------------
# Retention policy constants
# ---------------------------------------------------------------------------

_DEFAULT_MAX_KEEP: int = 5
_DEFAULT_MAX_AGE_HOURS: float = 24.0

# Opaque domain/workspace prefix, UTC timestamp and write-once identity.
_TS_FORMAT = "%Y%m%dT%H%M%S"
_SNAP_RE = re.compile(
    r"^([0-9a-f]{32})_(\d{8}T\d{6})_([0-9a-f]{32})\.snap\.age$"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _workspace_root(artha_dir: Optional[Path] = None) -> Path:
    return (artha_dir or ARTHA_DIR).resolve()


def _machine_local_root(artha_dir: Optional[Path] = None) -> Path:
    from lib.local_paths import local_dir  # noqa: PLC0415

    root = local_dir().resolve()
    workspace = _workspace_root(artha_dir)
    home = Path.home().resolve()
    forbidden = [
        workspace,
        home / "OneDrive",
        home / "Dropbox",
        home / "Library" / "CloudStorage",
        home / "Library" / "Mobile Documents",
    ]
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(name)
        if value:
            forbidden.append(Path(value).expanduser().resolve())
    if any(root == candidate or root.is_relative_to(candidate.resolve())
           for candidate in forbidden):
        raise ValueError("State snapshot storage must be machine-local and outside the workspace")
    return root


def _snap_dir(artha_dir: Optional[Path] = None) -> Path:
    local = _machine_local_root(artha_dir)
    workspace = hashlib.sha256(str(_workspace_root(artha_dir)).encode("utf-8")).hexdigest()[:16]
    return local / "state-snapshots" / workspace


def _ensure_private_directory(path: Path) -> None:
    if sys.platform == "win32":
        from catchup_core.quarantine_fs_windows import ensure_private_directory

        ensure_private_directory(path)
        return
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ValueError("State snapshot directory is not owner-private")


def _snapshot_prefix(domain: str, artha_dir: Optional[Path] = None) -> str:
    identity = f"{_workspace_root(artha_dir)}\0{domain}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:32]


def _ts_now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


def _vault_for_snapshots(directory: Path):
    from foundation import get_age_vault  # noqa: PLC0415

    return get_age_vault(state_dir=directory)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def snapshot(
    domain: str,
    content: str,
    artha_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Encrypt *content* in owner-private machine-local snapshot storage.

    Every successful publication is retained and write-once. Retention changes
    require an explicit authorized call to :func:`prune`.

    Args:
        domain: Domain name, bound into an opaque filename prefix.
        content: Current file content to preserve.  Must be non-empty.
        artha_dir: Artha project root (default: :data:`~lib.common.ARTHA_DIR`).
    Returns:
        Path to new ciphertext, or ``None`` if unavailable. No plaintext
        fallback is written.
    """
    if not content or not content.strip():
        return None
    snap_path: Path | None = None
    phase = "root"
    try:
        sdir = _snap_dir(artha_dir)
        phase = "directory"
        _ensure_private_directory(sdir)
        phase = "vault"
        vault = _vault_for_snapshots(sdir)
        ciphertext = vault.encrypt_artifact(content)
        snap_path = sdir / (
            f"{_snapshot_prefix(domain, artha_dir)}_{_ts_now()}_"
            f"{uuid.uuid4().hex}.snap.age"
        )
        from catchup_core.blobs import private_atomic_bytes  # noqa: PLC0415

        phase = "publish"
        private_atomic_bytes(snap_path, ciphertext)
        phase = "verify"
        if vault.read_artifact(snap_path) != content:
            raise ValueError("encrypted State snapshot verification failed")
        return snap_path
    except Exception as exc:  # noqa: BLE001
        print(
            f"[state_snapshot] encrypted snapshot unavailable: "
            f"{phase}:{type(exc).__name__}",
            file=sys.stderr,
        )
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
    try:
        sdir = _snap_dir(artha_dir)
    except (OSError, ValueError):
        return []
    if not sdir.exists():
        return []
    prefix = _snapshot_prefix(domain, artha_dir)
    matches = [p for p in sdir.glob(f"{prefix}_*.snap.age") if _SNAP_RE.match(p.name)]
    return sorted(
        matches,
        key=lambda p: (
            _SNAP_RE.match(p.name).group(2),
            p.stat().st_mtime_ns,
            p.name,
        ),
        reverse=True,
    )


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
        vault = _vault_for_snapshots(snaps[0].parent)
        return vault.read_artifact(snaps[0])
    except Exception:  # noqa: BLE001
        return None


def prune(
    domain: str,
    artha_dir: Optional[Path] = None,
    max_keep: int = _DEFAULT_MAX_KEEP,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    *,
    authorized: bool = False,
) -> int:
    """Delete excess and expired snapshots for *domain*.

    This is an explicitly invoked maintenance operation. A caller must pass
    ``authorized=True`` after obtaining the applicable retention authority.

    A snapshot is deleted if it is beyond position ``max_keep`` in the
    newest-first list, OR if its embedded timestamp is older than
    ``max_age_hours`` hours from now.

    Args:
        domain: Domain name.
        artha_dir: Artha project root.
        max_keep: Maximum snapshots to retain.
        max_age_hours: Age limit in hours.
        authorized: Must be explicitly true for any deletion to occur.

    Returns:
        Number of snapshots deleted.

    Raises:
        PermissionError: If pruning was not explicitly authorized.
    """
    if authorized is not True:
        raise PermissionError("State snapshot pruning requires explicit authorization")

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
