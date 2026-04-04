#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/state_writer.py — Canonical state write API for Artha.

ALL writes to ``state/*.md`` (and any other mutable state file outside
``tmp/``) MUST go through ``write()``.  Writes to ``tmp/`` (checkpoints,
ephemeral caches) use ``write_atomic()`` which skips middleware.

Architecture
------------
``write()`` composes the full middleware stack and performs an atomic write:

    current content → before_write pipeline → tempfile → os.replace() →
    after_write pipeline

``write_atomic()`` skips middleware — for low-risk, non-state tmp/ writes:

    tempfile → os.replace()

Thread-safety
-------------
Neither function implements per-file locking.  Callers MUST NOT invoke
``write()`` for the same target path from concurrent threads.
``tempfile → os.replace()`` ensures the file is never seen in a partially
written state, but it does NOT prevent lost updates when two threads race
on the same path.  Fan-out callers (``pipeline.py`` ThreadPoolExecutor)
must target distinct files.

Reference implementations
--------------------------
- ``health_check_writer.py`` lines 604–620: tempfile + os.replace() pattern
- ``scripts/work/compaction_manifest.py``: crash-recovery manifest pattern

Wave 0 of specs/agent-fw.md — write-path consolidation prerequisite.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class WriteResult:
    """Result from a state write operation."""

    path: Path
    success: bool
    snapshot_path: Optional[Path] = None
    middleware_log: list[str] = field(default_factory=list)


def write(
    path: Path,
    content: str,
    *,
    domain: str = "unknown",
    source: str = "system",
    snapshot: bool = True,
    pii_check: bool = True,
    ctx: Any | None = None,
) -> WriteResult:
    """Canonical state write.  ALL ``state/*.md`` mutations MUST use this.

    Guarantees: middleware composition, atomic write (tempfile + os.replace),
    optional snapshot before overwrite.

    Args:
        path: Absolute or workspace-relative path to the state file.
        content: Full new file content (UTF-8 string).
        domain: Artha domain name (e.g. ``"finance"``, ``"health"``).
            Used by middleware for audit logging and PII routing.
        source: Identifying label for the caller (e.g. ``"calendar_writer"``,
            ``"fact_extractor"``).  Stored in middleware log.
        snapshot: If ``True`` and the file already exists, create a snapshot
            in ``tmp/state_snapshots/`` before overwriting.  WriteGuard
            middleware also creates its own snapshot as a safety net.
        pii_check: If ``True``, run the PII middleware in the stack.
            Set ``False`` only for content already confirmed PII-free.
        ctx: Optional ``ArthaContext`` instance forwarded to middleware.

    Returns:
        :class:`WriteResult` with ``success=True`` on success, ``False`` on
        middleware block or OS error.  ``snapshot_path`` is set when a
        pre-write snapshot was created.
    """
    result = WriteResult(path=path, success=False)
    log = result.middleware_log
    log.append(f"write called: domain={domain} source={source} path={path}")

    # ------------------------------------------------------------------ #
    # 1. Build current content (empty string for new files)               #
    # ------------------------------------------------------------------ #
    current_content = ""
    if path.exists():
        try:
            current_content = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.append(f"WARN: could not read current content: {exc}")

    # ------------------------------------------------------------------ #
    # 2. Optional pre-write snapshot                                      #
    # ------------------------------------------------------------------ #
    if snapshot and current_content:
        snap = _create_snapshot(path, current_content)
        if snap:
            result.snapshot_path = snap
            log.append(f"snapshot created: {snap}")

    # ------------------------------------------------------------------ #
    # 3. Compose and run middleware before_write                          #
    # ------------------------------------------------------------------ #
    stack = _build_middleware_stack(pii_check=pii_check)
    approved = stack.before_write(domain, current_content, content, ctx)
    if approved is None:
        log.append("BLOCKED by middleware before_write")
        return result  # success=False

    # ------------------------------------------------------------------ #
    # 4. Atomic write: tempfile → os.replace()                           #
    # ------------------------------------------------------------------ #
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(approved)
        os.replace(tmp_path_str, path)
    except Exception as exc:  # noqa: BLE001
        log.append(f"ERROR during atomic write: {exc}")
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        return result  # success=False

    result.success = True
    log.append("write succeeded")

    # ------------------------------------------------------------------ #
    # 5. Run middleware after_write (non-fatal)                           #
    # ------------------------------------------------------------------ #
    try:
        stack.after_write(domain, path)
    except Exception as exc:  # noqa: BLE001
        log.append(f"WARN: after_write raised: {exc}")

    return result


def write_atomic(path: Path, content: str, **_kwargs: Any) -> WriteResult:
    """Low-level atomic write WITHOUT middleware.

    Use this for ``tmp/`` files (checkpoints, ephemeral caches) where
    middleware overhead is undesirable and state guarantees are not required.

    Args:
        path: Target file path.
        content: Full content to write (UTF-8 string).

    Returns:
        :class:`WriteResult` with ``success=True`` on success.
    """
    result = WriteResult(path=path, success=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path_str, path)
    except Exception as exc:  # noqa: BLE001
        result.middleware_log.append(f"ERROR during atomic write: {exc}")
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        return result

    result.success = True
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_middleware_stack(*, pii_check: bool = True):
    """Build the default middleware composition for state writes.

    Imports are deferred to avoid circular dependency during bootstrap.
    """
    try:
        # Add scripts/ directory to path so middleware imports resolve from
        # wherever state_writer.py is imported (scripts/lib/ is one level deep)
        _ensure_scripts_on_path()

        from middleware import compose_middleware  # noqa: PLC0415
        from middleware.write_guard import WriteGuardMiddleware  # noqa: PLC0415
        from middleware.audit_middleware import AuditMiddleware  # noqa: PLC0415

        middlewares: list = [WriteGuardMiddleware(), AuditMiddleware()]

        if pii_check:
            from middleware.pii_middleware import PIIMiddleware  # noqa: PLC0415
            middlewares.insert(0, PIIMiddleware())

        return compose_middleware(middlewares)
    except Exception as exc:  # noqa: BLE001
        # Middleware import failed — degrade safely to passthrough
        import warnings
        warnings.warn(
            f"[state_writer] middleware stack unavailable, using passthrough: {exc}",
            RuntimeWarning,
            stacklevel=3,
        )
        from middleware import _PassthroughMiddleware  # noqa: PLC0415
        return _PassthroughMiddleware()


def _ensure_scripts_on_path() -> None:
    """Add the ``scripts/`` directory to sys.path if not already present."""
    # state_writer.py lives at scripts/lib/state_writer.py
    # so parent.parent is scripts/
    scripts_dir = str(Path(__file__).resolve().parent.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def _create_snapshot(path: Path, content: str) -> Path | None:
    """Write a pre-write snapshot to ``tmp/state_snapshots/``.

    Returns the snapshot path on success, ``None`` on failure.
    The snapshot directory is determined relative to the Artha project root
    (two levels above ``scripts/lib/``).
    """
    try:
        # Project root: scripts/lib/ → scripts/ → project root
        artha_dir = Path(__file__).resolve().parent.parent.parent
        snap_dir = artha_dir / "tmp" / "state_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)

        import time
        ts = int(time.time())
        safe_name = path.name.replace("/", "_").replace("\\", "_")
        snap_path = snap_dir / f"{safe_name}.{ts}.bak"

        with open(snap_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return snap_path
    except Exception:  # noqa: BLE001
        return None
