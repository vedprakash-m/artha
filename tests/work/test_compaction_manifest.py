"""tests/work/test_compaction_manifest.py — Tests for scripts/work/compaction_manifest.py

Sprint 0 acceptance criteria §3.1.4:
- Save/load/complete lifecycle
- Stale manifest detection halts pipeline (raises RuntimeError)
- Crash recovery: stale manifest survives until complete() called
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


# ---------------------------------------------------------------------------
# Helper: patch MANIFEST_PATH to use tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def manifest_path(tmp_path, monkeypatch):
    """Replace MANIFEST_PATH with a temp location for test isolation."""
    import work.compaction_manifest as cm_mod
    temp = tmp_path / ".compaction-manifest.json"
    monkeypatch.setattr(cm_mod, "MANIFEST_PATH", temp)
    return temp


# ---------------------------------------------------------------------------
# Basic lifecycle
# ---------------------------------------------------------------------------

class TestCompactionManifestLifecycle:
    def test_save_creates_file(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        m = CompactionManifest(files_to_write=["a.md", "b.md"])
        m.save()
        assert manifest_path.exists()

    def test_load_if_exists_returns_none_when_absent(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        assert CompactionManifest.load_if_exists() is None

    def test_save_then_load_roundtrip(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        m = CompactionManifest(files_to_write=["x.md"])
        m.save()
        loaded = CompactionManifest.load_if_exists()
        assert loaded is not None
        assert loaded.run_id == m.run_id
        assert loaded.files_to_write == ["x.md"]

    def test_record_written_appends_and_persists(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        m = CompactionManifest(files_to_write=["a.md", "b.md"])
        m.save()
        m.record_written("a.md")
        loaded = CompactionManifest.load_if_exists()
        assert loaded is not None
        assert "a.md" in loaded.files_written
        assert "b.md" not in loaded.files_written

    def test_complete_deletes_manifest(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        m = CompactionManifest()
        m.save()
        assert manifest_path.exists()
        m.complete()
        assert not manifest_path.exists()

    def test_complete_is_idempotent(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        m = CompactionManifest()
        m.save()
        m.complete()
        m.complete()  # Second call should not raise


# ---------------------------------------------------------------------------
# Stale detection
# ---------------------------------------------------------------------------

class TestCheckStaleCompaction:
    def test_no_manifest_does_not_raise(self, manifest_path):
        from work.compaction_manifest import check_stale_compaction
        check_stale_compaction()  # Should not raise

    def test_existing_manifest_raises_runtime_error(self, manifest_path):
        from work.compaction_manifest import CompactionManifest, check_stale_compaction
        m = CompactionManifest(files_to_write=["reflect-current.md"])
        m.save()
        with pytest.raises(RuntimeError, match="Stale compaction manifest"):
            check_stale_compaction()

    def test_error_message_includes_pending_files(self, manifest_path):
        from work.compaction_manifest import CompactionManifest, check_stale_compaction
        m = CompactionManifest(files_to_write=["a.md", "b.md"])
        m.record_written("a.md")  # Only a.md written — b.md is pending
        with pytest.raises(RuntimeError) as exc_info:
            check_stale_compaction()
        assert "b.md" in str(exc_info.value)

    def test_error_message_includes_run_id(self, manifest_path):
        from work.compaction_manifest import CompactionManifest, check_stale_compaction
        m = CompactionManifest(files_to_write=["foo.md"])
        m.save()
        with pytest.raises(RuntimeError) as exc_info:
            check_stale_compaction()
        assert m.run_id in str(exc_info.value)

    def test_all_files_written_still_raises(self, manifest_path):
        """Even if all files were written, manifest still signals incomplete compaction."""
        from work.compaction_manifest import CompactionManifest, check_stale_compaction
        m = CompactionManifest(files_to_write=["a.md"])
        m.record_written("a.md")
        with pytest.raises(RuntimeError):
            check_stale_compaction()


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    def test_manifest_survives_without_complete(self, manifest_path):
        """Simulates crash: manifest written, complete() never called."""
        from work.compaction_manifest import CompactionManifest
        m = CompactionManifest(files_to_write=["critical.md"])
        m.save()
        m.record_written("critical.md")
        # No complete() — crash simulation
        assert manifest_path.exists()

    def test_manifest_contents_valid_json(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        m = CompactionManifest(files_to_write=["a.md", "b.md"])
        m.record_written("a.md")
        data = json.loads(manifest_path.read_text())
        assert "run_id" in data
        assert "started_at" in data
        assert data["files_to_write"] == ["a.md", "b.md"]
        assert data["files_written"] == ["a.md"]

    def test_corrupt_manifest_returns_none(self, manifest_path):
        from work.compaction_manifest import CompactionManifest
        manifest_path.write_text("CORRUPT JSON{{{", encoding="utf-8")
        loaded = CompactionManifest.load_if_exists()
        assert loaded is None

    def test_corrupt_manifest_does_not_halt_check_stale(self, manifest_path):
        """Corrupt manifest is unreadable — check_stale_compaction should not raise
        RuntimeError since load returns None."""
        from work.compaction_manifest import check_stale_compaction
        manifest_path.write_text("NOTJSON", encoding="utf-8")
        # check_stale_compaction only raises if manifest is loadable
        check_stale_compaction()  # Should not raise
