"""tests/integration/test_undo_state_change.py — AFW-6 Session Rewind / Undo.

Integration test for the complete undo flow.  Validates that:

1.  ``state_snapshot.snapshot()`` persists content before a state write.
2.  ``state_snapshot.restore_latest()`` returns the pre-write content.
3.  ``state_writer.write_atomic()`` restores the snapshot content into the
    state file.
4.  The file after undo matches the state before the write that was undone.
5.  Multiple sequential writes produce independently restorable snapshots.
6.  The undo components are correctly integrated (snapshot → restore → write).

Spec: specs/agent-fw.md §7.2 — ``test_undo_state_change``
Validates: AFW-6 (Session Rewind / Undo)
"""
from __future__ import annotations

import sys
import time
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

def _snap_dir(artha_dir: Path) -> Path:
    return artha_dir / "tmp" / "state_snapshots"


def _write_state(path: Path, content: str) -> None:
    """Direct write — simulates a state mutation (without middleware)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUndoStateChange:
    """Full integration suite for the AFW-6 undo / session-rewind flow."""

    def test_undo_restores_previous_state(self, tmp_path):
        """Core undo contract: write v1, snapshot v1, write v2, undo → file = v1.

        Simulates what WriteGuardMiddleware does before each write:
        1.  snapshot() captures v1 before v2 is applied.
        2.  restore_latest() returns the v1 snapshot content.
        3.  write_atomic() persists v1 back to the state file.
        """
        from lib.state_snapshot import restore_latest, snapshot
        from lib.state_writer import write_atomic

        ORIGINAL = "# Finance\n\nv1: balance $1,000.00\n"
        UPDATED = "# Finance\n\nv2: balance $2,500.00\n"

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "finance.md"

        # --- Step 1: Initial state write ---
        _write_state(state_file, ORIGINAL)
        assert state_file.read_text(encoding="utf-8") == ORIGINAL

        # --- Step 2: Snapshot v1 before overwriting (what WriteGuardMiddleware does) ---
        snap_path = snapshot("finance", ORIGINAL, artha_dir=tmp_path)
        assert snap_path is not None, "snapshot() must return a Path on success"
        assert snap_path.exists(), "Snapshot file must exist on disk"
        assert snap_path.suffix == ".snap"
        assert snap_path.read_text(encoding="utf-8") == ORIGINAL

        # --- Step 3: Second write (state update with v2) ---
        _write_state(state_file, UPDATED)
        assert state_file.read_text(encoding="utf-8") == UPDATED

        # --- Step 4: Undo — restore_latest() + write_atomic() ---
        restored_content = restore_latest("finance", artha_dir=tmp_path)
        assert restored_content is not None, "restore_latest() must return content when snapshot exists"
        assert restored_content == ORIGINAL, "Restored content must equal v1"

        result = write_atomic(state_file, restored_content)
        assert result.success, "write_atomic() must succeed for undo restore"

        # --- Step 5: Final verification ---
        final = state_file.read_text(encoding="utf-8")
        assert final == ORIGINAL, "State file after undo must match original v1 content"

    def test_snapshot_directory_created(self, tmp_path):
        """snapshot() creates tmp/state_snapshots/ when it does not exist."""
        from lib.state_snapshot import snapshot

        snap_dir = _snap_dir(tmp_path)
        assert not snap_dir.exists(), "Precondition: snapshot dir must not exist"

        result = snapshot("health", "# Health\n\nstatus: ok\n", artha_dir=tmp_path)
        assert result is not None
        assert snap_dir.is_dir(), "snapshot() must create the snapshot directory"

    def test_restore_latest_returns_none_when_no_snapshots(self, tmp_path):
        """restore_latest() returns None for a domain with no snapshots."""
        from lib.state_snapshot import restore_latest

        result = restore_latest("nonexistent_domain", artha_dir=tmp_path)
        assert result is None

    def test_multiple_writes_restore_most_recent_previous(self, tmp_path):
        """With two sequential snapshots, undo returns the most recent (last-before-current).

        Timeline:
            snapshot(v1) → write v2 → snapshot(v2) → write v3
                                                       undo → restore v2 (latest snap)
        """
        from lib.state_snapshot import restore_latest, snapshot
        from lib.state_writer import write_atomic

        V1 = "# Goals\n\nv1: sprint goal: ship feature\n"
        V2 = "# Goals\n\nv2: sprint goal: ship + write tests\n"
        V3 = "# Goals\n\nv3: sprint goal: ship + tests + docs\n"

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "goals.md"

        # First write + snapshot (v1 → will be snapshot before v2 write)
        _write_state(state_file, V1)
        s1 = snapshot("goals", V1, artha_dir=tmp_path)
        assert s1 is not None

        # Small delay to ensure distinct timestamps on filesystem
        time.sleep(1.05)

        # Second write + snapshot (v2 → will be snapshot before v3 write)
        _write_state(state_file, V2)
        s2 = snapshot("goals", V2, artha_dir=tmp_path)
        assert s2 is not None

        # Third write (no new snapshot yet)
        _write_state(state_file, V3)
        assert state_file.read_text(encoding="utf-8") == V3

        # Undo: restore_latest() returns V2 snapshot (most recent)
        restored = restore_latest("goals", artha_dir=tmp_path)
        assert restored == V2, "restore_latest() must return the most recent snapshot (V2)"

        result = write_atomic(state_file, restored)
        assert result.success
        assert state_file.read_text(encoding="utf-8") == V2

    def test_write_atomic_produces_correct_file_content(self, tmp_path):
        """write_atomic() correctly persists snapshot content to the state file."""
        from lib.state_snapshot import restore_latest, snapshot
        from lib.state_writer import write_atomic

        CONTENT = "# Immigration\n\nstatus: H1B — active\n"
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "immigration.md"

        snap = snapshot("immigration", CONTENT, artha_dir=tmp_path)
        assert snap is not None

        restored = restore_latest("immigration", artha_dir=tmp_path)
        assert restored == CONTENT

        result = write_atomic(state_file, restored)
        assert result.success
        assert result.path == state_file
        assert state_file.read_text(encoding="utf-8") == CONTENT

    def test_undo_is_idempotent_safe(self, tmp_path):
        """Calling undo twice does not crash — second restore returns same snapshot."""
        from lib.state_snapshot import restore_latest, snapshot
        from lib.state_writer import write_atomic

        CONTENT = "# Health\n\nv1: metrics nominal\n"
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "health.md"

        snap = snapshot("health", CONTENT, artha_dir=tmp_path)
        assert snap is not None

        # First undo
        r1 = restore_latest("health", artha_dir=tmp_path)
        assert r1 == CONTENT
        res1 = write_atomic(state_file, r1)
        assert res1.success

        # Second undo (snapshot still exists — not consumed by restore)
        r2 = restore_latest("health", artha_dir=tmp_path)
        assert r2 == CONTENT
        res2 = write_atomic(state_file, r2)
        assert res2.success

        assert state_file.read_text(encoding="utf-8") == CONTENT
