"""tests/integration/test_catchup_checkpoint_resume.py — AFW-5 Checkpoint Resume.

Integration test for the checkpoint kill-and-resume flow.  Simulates a
pipeline that is killed mid-way (at a configurable step), then resumed from
the checkpoint without re-running completed steps.

Validates:
1.  ``write_checkpoint()`` persists step metadata to ``tmp/.checkpoint.json``.
2.  ``read_checkpoint()`` returns a valid dict when the file is fresh.
3.  A "killed" pipeline (checkpoint present) can resume from last_step+1.
4.  A resumed pipeline skips all steps before the checkpoint step.
5.  Steps after the checkpoint step execute normally.
6.  ``clear_checkpoint()`` removes the file; subsequent ``read_checkpoint()`` → None.
7.  A stale checkpoint (beyond TTL) returns None.
8.  Corrupt JSON in the checkpoint file returns None (safe fallback).

Spec: specs/agent-fw.md §7.2 — ``test_catchup_checkpoint_resume``
Validates: AFW-5 (Workflow Checkpointing)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure scripts/ and scripts/lib/ are importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
_SCRIPTS_LIB = _SCRIPTS / "lib"
for _p in (_SCRIPTS, _SCRIPTS_LIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _checkpoint_path(artha_dir: Path) -> Path:
    return artha_dir / "tmp" / ".checkpoint.json"


def _run_pipeline_with_checkpoint(
    artha_dir: Path,
    total_steps: int,
    kill_at_step: int | None = None,
    resume_from: int | None = None,
) -> list[int]:
    """Simulate a pipeline with checkpoint write/read.

    Each step writes a checkpoint.  If ``kill_at_step`` is set, the loop
    stops after that step (simulating a crash).  If ``resume_from`` is set,
    the loop starts at that step (simulating resume from checkpoint).

    Returns the list of step numbers that actually executed.
    """
    from checkpoint import clear_checkpoint, read_checkpoint, write_checkpoint

    executed: list[int] = []
    start_step = (resume_from or 1)

    for step in range(1, total_steps + 1):
        if step < start_step:
            continue  # Skip already-completed steps (resume behaviour)

        # Simulate step work
        executed.append(step)
        write_checkpoint(artha_dir, step, phase="test", step_label=f"step_{step}")

        if kill_at_step is not None and step == kill_at_step:
            break  # Simulate crash/kill here

    return executed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCatchupCheckpointResume:
    """Full integration suite for AFW-5 workflow checkpointing."""

    def test_checkpoint_written_and_readable(self, tmp_path):
        """Checkpoint written after a step is immediately readable."""
        from checkpoint import read_checkpoint, write_checkpoint

        write_checkpoint(tmp_path, 5, phase="fetch", email_count=42)

        cp = read_checkpoint(tmp_path)
        assert cp is not None
        assert cp["last_step"] == 5
        assert cp["phase"] == "fetch"
        assert cp["email_count"] == 42
        assert "timestamp" in cp

    def test_pipeline_killed_at_step_10_creates_checkpoint(self, tmp_path):
        """Pipeline killed at step 10 leaves a checkpoint at step 10."""
        from checkpoint import read_checkpoint

        _run_pipeline_with_checkpoint(tmp_path, total_steps=21, kill_at_step=10)

        cp = read_checkpoint(tmp_path)
        assert cp is not None
        assert cp["last_step"] == 10

    def test_resume_skips_completed_steps(self, tmp_path):
        """Pipeline resumed from step 10 executes steps 10–21 only."""
        from checkpoint import read_checkpoint

        # First run: kill at step 10
        first_run = _run_pipeline_with_checkpoint(tmp_path, total_steps=21, kill_at_step=10)
        assert 1 in first_run
        assert 10 in first_run
        assert 11 not in first_run

        # Second run: resume from step 11 (last_step + 1)
        cp = read_checkpoint(tmp_path)
        assert cp is not None
        resume_from = cp["last_step"] + 1

        second_run = _run_pipeline_with_checkpoint(
            tmp_path, total_steps=21, resume_from=resume_from
        )

        # Only steps >= resume_from executed
        assert all(s >= resume_from for s in second_run), (
            f"All resumed steps must be >= {resume_from}, got: {second_run}"
        )
        assert 11 in second_run
        assert 21 in second_run

    def test_full_pipeline_without_kill_clears_cleanly(self, tmp_path):
        """A complete pipeline run leaves a checkpoint at the last step."""
        from checkpoint import read_checkpoint

        _run_pipeline_with_checkpoint(tmp_path, total_steps=21)

        cp = read_checkpoint(tmp_path)
        assert cp is not None
        assert cp["last_step"] == 21

    def test_clear_checkpoint_removes_file(self, tmp_path):
        """After clear_checkpoint(), read_checkpoint() returns None."""
        from checkpoint import clear_checkpoint, read_checkpoint, write_checkpoint

        write_checkpoint(tmp_path, 7, phase="process")
        assert read_checkpoint(tmp_path) is not None

        clear_checkpoint(tmp_path)
        assert read_checkpoint(tmp_path) is None
        assert not _checkpoint_path(tmp_path).exists()

    def test_stale_checkpoint_returns_none(self, tmp_path):
        """A checkpoint older than TTL is treated as absent."""
        from checkpoint import read_checkpoint

        # Write a checkpoint manually with a stale timestamp (6 hours ago)
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        cp_path = _checkpoint_path(tmp_path)
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(
            json.dumps({"last_step": 5, "timestamp": stale_ts}), encoding="utf-8"
        )

        result = read_checkpoint(tmp_path)
        assert result is None, "Stale checkpoint must return None"

    def test_corrupt_checkpoint_returns_none(self, tmp_path):
        """A non-JSON checkpoint file returns None without raising."""
        from checkpoint import read_checkpoint

        cp_path = _checkpoint_path(tmp_path)
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text("this is not { valid json }", encoding="utf-8")

        result = read_checkpoint(tmp_path)
        assert result is None, "Corrupt checkpoint must return None, not raise"

    def test_checkpoint_metadata_preserved_on_resume(self, tmp_path):
        """Connector results and domain signals survive a resume cycle."""
        from checkpoint import read_checkpoint, write_checkpoint

        connector_data = {"outlook": {"emails": 15}, "gcal": {"events": 3}}
        domain_data = {"finance": ["tax_due", "property_tax"], "home": ["hoa"]}

        write_checkpoint(
            tmp_path,
            8,
            phase="fetch",
            connector_results=connector_data,
            domain_signals=domain_data,
        )

        cp = read_checkpoint(tmp_path)
        assert cp is not None
        assert cp["connector_results"] == connector_data
        assert cp["domain_signals"] == domain_data
        assert cp["phase"] == "fetch"
