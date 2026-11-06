"""
scripts/lib/local_paths.py — machine-local locations that must never sync (WP-03)
================================================================================
Plaintext working data (fetched email bodies, derived JSONL) belongs outside the
cloud-synced workspace: ``~/.artha-local`` (or ``$ARTHA_LOCAL_DIR``).  This module
is the one place that decides where, so the pipeline's ``--output`` validator and
every consumer change together (re-artha §12.2 item 6, P1).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PIPELINE_OUTPUT_NAME = "pipeline_output.jsonl"


def work_cache_dir() -> Path:
    """Pure Work cache location; reject the repository and known cloud-sync roots."""
    root = Path(os.environ.get("ARTHA_LOCAL_DIR") or Path.home() / ".artha-local").expanduser()
    repository = Path(__file__).resolve().parents[2]
    forbidden = [repository, Path.home() / "Library/CloudStorage", Path.home() / "Library/Mobile Documents",
                 Path.home() / "OneDrive", Path.home() / "Dropbox"]
    forbidden.extend(Path(value).expanduser() for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
                     if (value := os.environ.get(name)))
    candidate = root / "work/cache"
    if any(candidate.resolve().is_relative_to(path.resolve()) for path in forbidden):
        raise ValueError("Work cache must be machine-local, outside the repository and known sync roots")
    return candidate


def local_dir() -> Path:
    """The owner-private machine-local directory, created securely on first use."""
    root = Path(os.environ.get("ARTHA_LOCAL_DIR") or Path.home() / ".artha-local").expanduser()
    if sys.platform == 'win32':
        from catchup_core.quarantine_fs_windows import (  # noqa: PLC0415
            QuarantineFilesystemError,
            ensure_private_directory,
        )
        try:
            ensure_private_directory(root)
        except QuarantineFilesystemError:
            raise ValueError('machine-local directory is unsafe or unavailable') from None
        return root
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def pipeline_output_path() -> Path:
    return local_dir() / PIPELINE_OUTPUT_NAME


def resolve_local_output(raw: str | os.PathLike[str]) -> Path:
    """Resolve a requested output path and require it to be inside :func:`local_dir`.

    Relative paths are taken relative to the local directory.  Raises ``ValueError``
    for anything that resolves elsewhere (traversal, workspace, sync folders).
    """
    base = local_dir().resolve()
    candidate = Path(raw).expanduser()
    target = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if not target.is_relative_to(base):
        raise ValueError(
            f"--output must be inside the machine-local directory {base} (got {str(raw)!r}); "
            "plaintext pipeline output must not live in the synced workspace."
        )
    return target


_REPOSITORY = Path(__file__).resolve().parents[2]


def audit_log_path(artha_dir: str | os.PathLike[str] | None = None) -> Path:
    """Where Artha appends its operational audit log.

    For the installed workspace the log is machine-local (``$ARTHA_LOCAL_DIR/audit.md``, default
    ``~/.artha-local/audit.md``) so plaintext audit lines never land in the cloud-synced ``state/``
    folder; the encrypted ``state/audit.md.age`` keeps the history from before 2026-11-05. An explicit
    ``artha_dir`` other than this installation (a test or scratch workspace) keeps ``<artha_dir>/state/audit.md``.
    """
    if artha_dir is not None and Path(artha_dir).expanduser().resolve() != _REPOSITORY:
        return Path(artha_dir) / "state" / "audit.md"
    root = Path(os.environ.get("ARTHA_LOCAL_DIR") or Path.home() / ".artha-local").expanduser()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        pass  # writers report their own failure when the path cannot be opened
    return root / "audit.md"
