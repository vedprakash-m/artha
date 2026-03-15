#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/checkpoint.py — Step checkpoint tracking for crash recovery.

Writes a lightweight JSON marker to ``tmp/.checkpoint.json`` after each
major workflow step.  On session start, the workflow reads this file to
determine whether a previous session can be resumed from the last
successful step.

Checkpoint file schema:
    {
        "last_step": 4,
        "timestamp": "2026-03-15T09:00:00+00:00",
        "email_count": 42,          # optional per-step metadata
        ...
    }

Checkpoints are ephemeral (stored in ``tmp/``), cleaned at Step 18 by the
``clear_checkpoint()`` call in ``config/workflow/finalize.md``.  Stale
checkpoints older than ``_MAX_AGE_HOURS`` are silently ignored so that
a new session always starts fresh after a reasonable delay.

Inspired by LangGraph's ``StateGraph`` checkpoint — each node writes state
after execution.  On failure, ``graph.resume(checkpoint_id)`` restarts
from the last successful node without re-executing completed steps.  We
use the simpler "implicit checkpoint" pattern: if ``tmp/.checkpoint.json``
exists and is fresh, prompt the user to resume.

Phase 4 of the Agentic Intelligence Improvement Plan (specs/agentic-improve.md).

Usage:
    from checkpoint import read_checkpoint, write_checkpoint, clear_checkpoint
    from pathlib import Path

    # After Step 4 completes:
    write_checkpoint(Path("."), 4, email_count=42)

    # At session start:
    cp = read_checkpoint(Path("."))
    if cp:
        print(f"Resumable session found — last completed Step {cp['last_step']}")

Config flag: harness.agentic.checkpoints.enabled (default: true)
When disabled, write_checkpoint() is a no-op and read_checkpoint() always
returns None.

Ref: specs/agentic-improve.md Phase 4
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CHECKPOINT_FILE = "tmp/.checkpoint.json"
_MAX_AGE_HOURS = 4  # Stale checkpoints are ignored after this many hours


def _is_enabled(artha_dir: Path) -> bool:
    """Check the harness.agentic.checkpoints.enabled feature flag."""
    try:
        import sys
        scripts_dir = str(artha_dir / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from context_offloader import load_harness_flag  # noqa: PLC0415
        return load_harness_flag("agentic.checkpoints.enabled")
    except Exception:  # noqa: BLE001
        return True  # Default: enabled


def read_checkpoint(artha_dir: Path) -> dict[str, Any] | None:
    """Read the current checkpoint state, or None if absent or stale.

    Args:
        artha_dir: Artha project root (e.g. ``Path(".")``).

    Returns:
        Checkpoint dict (always contains ``"last_step"`` and
        ``"timestamp"`` keys, plus any metadata written at checkpoint
        time), or ``None`` when:
        - The checkpoint file does not exist
        - The file is older than ``_MAX_AGE_HOURS``
        - The file contains invalid JSON
        - The feature flag is disabled
    """
    if not _is_enabled(artha_dir):
        return None

    path = artha_dir / _CHECKPOINT_FILE
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    timestamp_str = data.get("timestamp")
    if not timestamp_str:
        return None

    try:
        ts = datetime.fromisoformat(timestamp_str)
        # Ensure timezone-aware comparison
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_hours = (now - ts).total_seconds() / 3600
        if age_hours > _MAX_AGE_HOURS:
            return None
    except (ValueError, OverflowError):
        return None

    return data


def write_checkpoint(
    artha_dir: Path,
    last_step: int | float,
    **metadata: Any,
) -> None:
    """Write a checkpoint marker after a successful step.

    Creates ``tmp/.checkpoint.json`` with the step number, a UTC timestamp,
    and any additional keyword arguments as step-specific metadata
    (e.g. ``email_count=42``, ``domains_processed=["finance"]``).

    Args:
        artha_dir: Artha project root.
        last_step: The step number that just completed successfully.
        **metadata: Optional per-step metadata stored alongside the
            step number.  Values must be JSON-serialisable.
    """
    if not _is_enabled(artha_dir):
        return

    path = artha_dir / _CHECKPOINT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "last_step": last_step,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def clear_checkpoint(artha_dir: Path) -> None:
    """Remove the checkpoint file.

    Called at Step 18 cleanup so the next session starts fresh.
    Safe to call even when the file does not exist.

    Args:
        artha_dir: Artha project root.
    """
    path = artha_dir / _CHECKPOINT_FILE
    path.unlink(missing_ok=True)
