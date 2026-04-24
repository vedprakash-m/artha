"""
partial_writer.py — S-08 Partial-Write + Assembly for pipeline resilience.
specs/steal.md §15.4.1

When a connector fetch fails or times out, the rest of the pipeline can still
produce a partial briefing from the connectors that succeeded.  This module
provides three public functions:

  write_partial(artha_dir, result)    → atomically persist one partial result
  assemble_partials(artha_dir, run_id) → merge all ok partials for a run
  cleanup_partials(artha_dir, ...)    → delete aged partial files

Design constraints:
  - R8: dataclasses only, no Pydantic
  - R3: write to artha_dir/tmp/ (OneDrive-backed), NOT ~/.artha-local
  - Atomic write via tempfile + os.replace to prevent torn writes
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class PartialResult:
    """One connector's output for a single pipeline run."""
    run_id: str
    provider: str
    timestamp: str          # ISO-8601 UTC
    status: str             # "ok" | "error" | "timeout"
    data: dict[str, Any]
    error: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_partial(artha_dir: Path, result: PartialResult) -> Path:
    """Atomically write *result* to tmp/partial_{run_id}_{provider}.json.

    Returns the final path.  Raises ValueError for invalid status values.
    """
    valid_statuses = {"ok", "error", "timeout"}
    if result.status not in valid_statuses:
        raise ValueError(f"Invalid status {result.status!r}; must be one of {valid_statuses}")

    tmp_dir = artha_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    dest = tmp_dir / f"partial_{result.run_id}_{result.provider}.json"
    payload = asdict(result)

    # Atomic write: write to .tmp first, then replace
    fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    os.replace(tmp_name, dest)
    return dest


def assemble_partials(artha_dir: Path, run_id: str) -> tuple[dict[str, Any], list[str]]:
    """Merge all ok-status partials for *run_id*.

    Returns (merged_data, warnings) where:
      - merged_data maps provider → data dict (only status="ok" partials)
      - warnings lists skipped/problematic files
    """
    tmp_dir = artha_dir / "tmp"
    merged: dict[str, Any] = {}
    warnings: list[str] = []

    if not tmp_dir.exists():
        warnings.append(f"No partial files found for run_id={run_id!r}: tmp directory not found")
        return merged, warnings

    partials = sorted(tmp_dir.glob(f"partial_{run_id}_*.json"))

    if not partials:
        warnings.append(f"No partial files found for run_id={run_id!r}")
        return merged, warnings

    _required_keys = {"run_id", "provider", "timestamp", "status", "data"}

    for path in partials:
        try:
            payload: dict = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"Could not parse {path.name}: {exc}")
            continue

        missing = _required_keys - set(payload.keys())
        if missing:
            warnings.append(
                f"Skipped {path.name}: missing required keys {sorted(missing)}"
            )
            continue

        if payload["status"] != "ok":
            warnings.append(
                f"Skipped provider {payload['provider']!r} (status={payload['status']!r})"
            )
            continue

        merged[payload["provider"]] = payload["data"]

    return merged, warnings


def cleanup_partials(artha_dir: Path, max_age_hours: int = 24) -> int:
    """Delete partial_*.json files in tmp/ that are older than *max_age_hours*.

    Returns the count of deleted files.  Never raises; logs internally.
    """
    tmp_dir = artha_dir / "tmp"
    if not tmp_dir.exists():
        return 0

    cutoff = time.time() - (max_age_hours * 3600)
    deleted = 0

    for path in tmp_dir.glob("partial_*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                deleted += 1
        except OSError:
            pass  # Best-effort — skip unreadable/already-deleted files

    return deleted
