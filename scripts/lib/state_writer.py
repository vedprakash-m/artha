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
``write()`` holds an installation fence and a machine-local target lock through
comparison, staging and publication.  Read/modify/write callers bind the
original ``read_versioned()`` token with ``expected_token``; the token is
rechecked immediately before replacement.  ``write_atomic()`` remains a
low-level no-lock utility for non-authoritative temporary data.

Reference implementations
--------------------------
- ``health_check_writer.py`` lines 604–620: tempfile + os.replace() pattern
- ``scripts/work/compaction_manifest.py``: crash-recovery manifest pattern

Wave 0 of specs/agent-fw.md — write-path consolidation prerequisite.
"""
from __future__ import annotations

import functools
import hashlib
import os
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from .state_frontmatter import field_value, split_frontmatter, with_frontmatter_fields
except ImportError:  # scripts/lib is imported directly by legacy callers
    from state_frontmatter import field_value, split_frontmatter, with_frontmatter_fields


# ---------------------------------------------------------------------------
# Conflict tokens
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StateWriteToken:
    """The target, native file generation and content read before a state update."""

    target: str
    revision: str
    native_identity: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0)


class StateWriteConflict(RuntimeError):
    """The file no longer matches the original read or create-if-absent token."""


# ---------------------------------------------------------------------------
# Migration-compatible frontmatter helpers (private)
# ---------------------------------------------------------------------------

def _extract_occ_version(content: str) -> int:
    """Extract a frontmatter version; absent, empty and malformed values are 0."""
    parts = split_frontmatter(content)
    if parts is None:
        return 0
    value = field_value(parts.metadata, "version")
    if not value:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _inject_occ_fields(content: str, version: int, source: str) -> str:
    """Losslessly update the legacy migration metadata for ``migrate_occ``."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return with_frontmatter_fields(
        content,
        {
            "version": str(version),
            "last_written_by": source,
            "last_written_at": now_iso,
        },
    )


def _target_identity(path: Path) -> str:
    return os.path.normcase(str(path.absolute()))


def _native_file_identity(
    metadata: os.stat_result | None,
) -> tuple[int, int, int, int, int, int]:
    if metadata is None:
        return (0, 0, 0, 0, 0, 0)
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(getattr(metadata, "st_birthtime_ns", 0) or 0),
        int(metadata.st_ctime_ns),
        int(metadata.st_mtime_ns),
        int(metadata.st_size),
    )


def _validate_state_target(path: Path, metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise StateWriteConflict(f"State File target is not an ordinary unlinked file: {path}")
    if path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise StateWriteConflict(f"State File target is a link or reparse point: {path}")


def _token_for_bytes(
    path: Path,
    data: bytes | None,
    metadata: os.stat_result | None = None,
) -> StateWriteToken:
    if data is None:
        revision = ""
    else:
        digest = hashlib.sha256(_target_identity(path).encode("utf-8") + b"\0" + data)
        revision = digest.hexdigest()
    return StateWriteToken(
        _target_identity(path),
        revision,
        _native_file_identity(metadata),
    )


def _read_bytes_and_token(path: Path) -> tuple[bytes | None, StateWriteToken]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None, _token_for_bytes(path, None)
    _validate_state_target(path, metadata)
    data = path.read_bytes()
    try:
        finished = path.lstat()
    except FileNotFoundError as exc:
        raise StateWriteConflict(f"State File changed while it was read: {path}") from exc
    _validate_state_target(path, finished)
    if _native_file_identity(metadata) != _native_file_identity(finished):
        raise StateWriteConflict(f"State File changed while it was read: {path}")
    return data, _token_for_bytes(path, data, finished)


def read_versioned(path: Path) -> tuple[str, StateWriteToken]:
    """Read exact UTF-8 bytes and return the token required by a later write."""
    path = Path(path)
    data, token = _read_bytes_and_token(path)
    if data is None:
        raise FileNotFoundError(f"State File is absent; use create_if_absent_token(): {path}")
    try:
        return data.decode("utf-8"), token
    except UnicodeDecodeError as exc:
        raise StateWriteConflict(f"State File is not valid UTF-8: {path}") from exc


def create_if_absent_token(path: Path) -> StateWriteToken:
    """Return an explicit create token only while the target is absent."""
    path = Path(path)
    data, token = _read_bytes_and_token(path)
    if data is not None:
        raise StateWriteConflict(f"State File already exists; explicit create token unavailable: {path}")
    return token


@dataclass
class WriteResult:
    """Result from a state write operation."""

    path: Path
    success: bool
    snapshot_path: Optional[Path] = None
    middleware_log: list[str] = field(default_factory=list)
    uncertain: bool = False


def _write_unfenced(
    path: Path,
    content: str,
    *,
    domain: str = "unknown",
    source: str = "system",
    snapshot: bool = True,
    pii_check: bool = True,
    ctx: Any | None = None,
    expected_token: StateWriteToken | None = None,
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
        snapshot: If ``True`` and the file already exists, create one snapshot
            in ``tmp/state_snapshots/`` before overwriting.
        pii_check: If ``True``, run the PII middleware in the stack.
            Set ``False`` only for content already confirmed PII-free.
        ctx: Optional ``ArthaContext`` instance forwarded to middleware.
        expected_token: Exact ``read_versioned()`` token or create token.

    Returns:
        :class:`WriteResult` with ``success=True`` on success, ``False`` on
        middleware block or OS error.  ``snapshot_path`` is set when a
        pre-write snapshot was created.
    """
    if expected_token is None:
        raise TypeError(
            "expected_token is required; use read_versioned() for updates "
            "or create_if_absent_token() for creation"
        )
    if not isinstance(expected_token, StateWriteToken):
        raise TypeError(
            "expected_token must be returned by read_versioned() "
            "or create_if_absent_token()"
        )

    result = WriteResult(path=path, success=False)
    log = result.middleware_log
    log.append(f"write called: domain={domain} source={source} path={path}")

    # ------------------------------------------------------------------ #
    # 1. Build current content (empty string for new files)               #
    # ------------------------------------------------------------------ #
    try:
        current_bytes, current_token = _read_bytes_and_token(path)
        current_content = "" if current_bytes is None else current_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.append(f"ERROR reading current content: {exc}")
        return result

    if expected_token.target != current_token.target:
        raise StateWriteConflict(f"State File token belongs to another target: {path}")
    if (
        expected_token.native_identity != current_token.native_identity
        or expected_token.revision != current_token.revision
    ):
        state = "created" if not expected_token.revision and current_bytes is not None else "changed"
        raise StateWriteConflict(f"State File {state} after its original read: {path}")

    try:
        stack = _build_middleware_stack(pii_check=pii_check)
    except Exception as exc:  # noqa: BLE001
        log.append(f"BLOCKED: mandatory middleware unavailable: {exc}")
        return result
    approved = stack.before_write(domain, current_content, content, ctx)
    if approved is None:
        log.append("BLOCKED by middleware before_write")
        return result  # success=False

    if snapshot and current_content:
        snap = _snapshot_current(path, domain, current_content)
        if snap is None:
            log.append("BLOCKED: pre-write snapshot failed")
            return result
        result.snapshot_path = snap
        log.append(f"snapshot created: {snap}")

    _assert_expected_token(path, expected_token)

    # ------------------------------------------------------------------ #
    # 2. Atomic write: tempfile → os.replace()                            #
    # ------------------------------------------------------------------ #
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    published = False
    try:
        proposed_bytes = approved.encode("utf-8")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(approved)
            fh.flush()
            os.fsync(fh.fileno())
        staged_bytes, staged_token = _read_bytes_and_token(Path(tmp_path_str))
        if staged_bytes != proposed_bytes:
            raise OSError("staged State File bytes changed before publication")
        staged_object_identity = staged_token.native_identity[:3]
        _assert_expected_token(path, expected_token)
        os.replace(tmp_path_str, path)
        published = True
        _fsync_directory(path.parent)
        published_bytes, published_token = _read_bytes_and_token(path)
        if published_token.native_identity[:3] != staged_object_identity:
            result.uncertain = True
            log.append(
                "ERROR: post-publication native identity did not match the staged object"
            )
            return result
        if published_bytes != proposed_bytes:
            result.uncertain = True
            log.append("ERROR: post-publication readback did not match proposed bytes")
            return result
    except Exception as exc:  # noqa: BLE001
        if published:
            result.uncertain = True
            log.append(f"ERROR: publication outcome uncertain: {exc}")
        else:
            if isinstance(exc, StateWriteConflict):
                raise
            log.append(f"ERROR during atomic write: {exc}")
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        return result  # success=False

    result.success = True
    log.append("write succeeded")

    # ------------------------------------------------------------------ #
    # 3. Run middleware after_write (non-fatal)                           #
    # ------------------------------------------------------------------ #
    try:
        stack.after_write(domain, path)
    except Exception as exc:  # noqa: BLE001
        log.append(f"WARN: after_write raised: {exc}")

    return result


@functools.wraps(_write_unfenced)
def write(path: Path, content: str, **kwargs: Any) -> WriteResult:
    """Fenced State File write: refused while a checkpoint is captured and in a restored installation."""
    _ensure_scripts_on_path()
    from catchup_core.session_lock import publication_fence  # noqa: PLC0415
    with publication_fence(Path(path)):
        return _write_unfenced(path, content, **kwargs)


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

        return compose_middleware(middlewares, mandatory=True)
    except Exception:
        raise RuntimeError("required write middleware could not be loaded") from None


def _ensure_scripts_on_path() -> None:
    """Add the ``scripts/`` directory to sys.path if not already present."""
    # state_writer.py lives at scripts/lib/state_writer.py
    # so parent.parent is scripts/
    scripts_dir = str(Path(__file__).resolve().parent.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def _snapshot_current(path: Path, domain: str, content: str) -> Path | None:
    from lib.state_snapshot import snapshot

    repository = Path(__file__).resolve().parents[2]
    try:
        path.resolve().relative_to(repository)
    except ValueError:
        root = path.parent
    else:
        root = repository
    return snapshot(domain, content, artha_dir=root)


def _assert_expected_token(path: Path, expected: StateWriteToken) -> None:
    _, current = _read_bytes_and_token(path)
    if current != expected:
        state = "created" if not expected.revision and current.revision else "changed"
        raise StateWriteConflict(f"State File {state} after its original read: {path}")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
