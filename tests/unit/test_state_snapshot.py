"""tests/unit/test_state_snapshot.py — Unit tests for scripts/lib/state_snapshot.py

Wave 1 verification suite (specs/agent-fw.md §AFW-6).

Coverage:
  - snapshot(): creates .snap file, returns Path, handles empty content → None
  - snapshot(): filesystem errors return None without raising
  - list_snapshots(): returns newest-first, empty dir → empty list
  - restore_latest(): returns content string, no snapshots → None
  - prune(): max_keep limit, max_age_hours pruning, returns deleted count
  - _safe_domain: special chars replaced with underscore in filenames
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

# conftest adds scripts/ to sys.path
from lib.state_snapshot import (
    _SNAP_RE,
    list_snapshots,
    prune,
    restore_latest,
    snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap_dir(tmp_path: Path) -> Path:
    return tmp_path / "tmp" / "state_snapshots"


# ---------------------------------------------------------------------------
# snapshot()
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_creates_file(self, tmp_path):
        """snapshot() writes a .snap file and returns its Path."""
        result = snapshot("finance", "content here", artha_dir=tmp_path)
        assert result is not None
        assert result.exists()
        assert result.suffix == ".snap"

    def test_file_contains_content(self, tmp_path):
        """Snapshot file content matches the input content."""
        result = snapshot("finance", "hello world", artha_dir=tmp_path)
        assert result is not None
        assert result.read_text(encoding="utf-8") == "hello world"

    def test_returns_path_object(self, tmp_path):
        """Return value is a pathlib.Path."""
        result = snapshot("finance", "data", artha_dir=tmp_path)
        assert isinstance(result, Path)

    def test_empty_content_returns_none(self, tmp_path):
        """Empty string returns None — nothing to snapshot."""
        assert snapshot("finance", "", artha_dir=tmp_path) is None

    def test_whitespace_only_returns_none(self, tmp_path):
        """Whitespace-only string returns None."""
        assert snapshot("finance", "   \n\t  ", artha_dir=tmp_path) is None

    def test_creates_snap_dir(self, tmp_path):
        """snapshot() creates the tmp/state_snapshots directory if absent."""
        result = snapshot("health", "content", artha_dir=tmp_path)
        assert _snap_dir(tmp_path).is_dir()

    def test_domain_in_filename(self, tmp_path):
        """Snapshot filename starts with the sanitized domain name."""
        result = snapshot("immigration", "content", artha_dir=tmp_path)
        assert result is not None
        assert result.name.startswith("immigration_")

    def test_special_chars_sanitized(self, tmp_path):
        """Special characters in domain name are replaced with underscores."""
        result = snapshot("my/domain:test", "content", artha_dir=tmp_path)
        assert result is not None
        # Sanitized name should have no / or :
        assert "/" not in result.stem
        assert ":" not in result.stem
        assert re.match(r"^[a-zA-Z0-9_\-]+_\d{8}T\d{6}$", result.stem)

    def test_filename_matches_pattern(self, tmp_path):
        """Snapshot filename matches expected _SNAP_RE pattern."""
        result = snapshot("finance", "data", artha_dir=tmp_path)
        assert result is not None
        assert _SNAP_RE.match(result.name)

    def test_multiple_same_domain(self, tmp_path):
        """Multiple snapshots for same domain create distinct files."""
        r1 = snapshot("finance", "v1", artha_dir=tmp_path)
        time.sleep(1.1)  # ensure distinct timestamps
        r2 = snapshot("finance", "v2", artha_dir=tmp_path)
        assert r1 != r2

    def test_returns_none_on_io_error(self, tmp_path, monkeypatch):
        """IO error during write returns None rather than raising."""
        original_write = Path.write_text

        def fail_snap_write(self_path, *args, **kwargs):
            if str(self_path).endswith(".snap"):
                raise OSError("simulated disk full")
            return original_write(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_snap_write)
        result = snapshot("finance", "content", artha_dir=tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# list_snapshots()
# ---------------------------------------------------------------------------

class TestListSnapshots:
    def test_empty_dir_returns_empty(self, tmp_path):
        """No snapshots returns empty list."""
        assert list_snapshots("finance", artha_dir=tmp_path) == []

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        """Nonexistent snap_dir returns empty list without error."""
        result = list_snapshots("finance", artha_dir=tmp_path / "no_such_dir")
        assert result == []

    def test_newest_first(self, tmp_path):
        """Snapshots are returned newest-first (reverse lexicographic by timestamp)."""
        r1 = snapshot("finance", "old", artha_dir=tmp_path)
        time.sleep(1.1)
        r2 = snapshot("finance", "new", artha_dir=tmp_path)
        snaps = list_snapshots("finance", artha_dir=tmp_path)
        assert snaps[0] == r2  # newest first
        assert snaps[1] == r1

    def test_only_matching_domain(self, tmp_path):
        """list_snapshots returns only files for the specified domain."""
        snapshot("finance", "f", artha_dir=tmp_path)
        snapshot("health", "h", artha_dir=tmp_path)
        finance_snaps = list_snapshots("finance", artha_dir=tmp_path)
        health_snaps = list_snapshots("health", artha_dir=tmp_path)
        assert all("finance" in p.name for p in finance_snaps)
        assert all("health" in p.name for p in health_snaps)
        assert len(finance_snaps) == 1
        assert len(health_snaps) == 1

    def test_returns_path_objects(self, tmp_path):
        """All returned values are pathlib.Path instances."""
        snapshot("finance", "data", artha_dir=tmp_path)
        snaps = list_snapshots("finance", artha_dir=tmp_path)
        assert all(isinstance(p, Path) for p in snaps)


# ---------------------------------------------------------------------------
# restore_latest()
# ---------------------------------------------------------------------------

class TestRestoreLatest:
    def test_returns_content(self, tmp_path):
        """restore_latest returns the content of the most recent snapshot."""
        snapshot("finance", "v1\ncontent", artha_dir=tmp_path)
        content = restore_latest("finance", artha_dir=tmp_path)
        assert content == "v1\ncontent"

    def test_returns_latest_content(self, tmp_path):
        """When multiple snapshots exist, returns the newest content."""
        snapshot("finance", "version1", artha_dir=tmp_path)
        time.sleep(1.1)
        snapshot("finance", "version2", artha_dir=tmp_path)
        content = restore_latest("finance", artha_dir=tmp_path)
        assert content == "version2"

    def test_no_snapshots_returns_none(self, tmp_path):
        """No snapshots available → returns None."""
        assert restore_latest("finance", artha_dir=tmp_path) is None

    def test_unicode_content_preserved(self, tmp_path):
        """Unicode content (em dash, CJK, etc.) roundtrips correctly."""
        text = "Balance: $5,000 — paid on 2026\u5e74"
        snapshot("finance", text, artha_dir=tmp_path)
        assert restore_latest("finance", artha_dir=tmp_path) == text


# ---------------------------------------------------------------------------
# prune()
# ---------------------------------------------------------------------------

class TestPrune:
    def test_prunes_beyond_max_keep(self, tmp_path):
        """Excess snapshots beyond max_keep are deleted."""
        snap_dir = _snap_dir(tmp_path)
        snap_dir.mkdir(parents=True, exist_ok=True)
        # Create 6 files with distinct second-resolution timestamps
        for i in range(6):
            ts = f"2026010{i + 1}T100000"
            (snap_dir / f"finance_{ts}.snap").write_text(f"v{i}", encoding="utf-8")
        deleted = prune("finance", artha_dir=tmp_path, max_keep=3, max_age_hours=9999.0)
        remaining = list_snapshots("finance", artha_dir=tmp_path)
        assert deleted == 3
        assert len(remaining) == 3

    def test_keeps_newest(self, tmp_path):
        """Prune keeps the most recent snapshots."""
        for i in range(4):
            snapshot("finance", f"v{i}", artha_dir=tmp_path, max_keep=10)
            time.sleep(0.05)
        snaps_before = list_snapshots("finance", artha_dir=tmp_path)
        prune("finance", artha_dir=tmp_path, max_keep=2, max_age_hours=9999.0)
        remaining = list_snapshots("finance", artha_dir=tmp_path)
        # The 2 newest should be retained
        for snap in remaining:
            assert snap in snaps_before[:2]

    def test_prune_by_age(self, tmp_path):
        """snapshot() calls prune() ; prune() removes age-expired snapshots."""
        # Create a snapshot and manually backdate its timestamp by renaming
        r = snapshot("health", "old content", artha_dir=tmp_path, max_keep=100)
        if r is None:
            pytest.skip("snapshot() returned None unexpectedly")
        snap_dir = _snap_dir(tmp_path)
        # Rename to a timestamp 48 hours ago
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y%m%dT%H%M%S")
        old_path = snap_dir / f"health_{old_ts}.snap"
        r.rename(old_path)
        deleted = prune("health", artha_dir=tmp_path, max_keep=100, max_age_hours=24.0)
        assert deleted == 1
        assert not old_path.exists()

    def test_no_snapshots_returns_zero(self, tmp_path):
        """prune() on empty domain returns 0 deleted."""
        assert prune("finance", artha_dir=tmp_path, max_keep=5) == 0

    def test_max_keep_zero_deletes_all(self, tmp_path):
        """max_keep=0 deletes all snapshots for the domain."""
        snapshot("finance", "v1", artha_dir=tmp_path, max_keep=10)
        snapshot("finance", "v2", artha_dir=tmp_path, max_keep=10)
        prune("finance", artha_dir=tmp_path, max_keep=0, max_age_hours=9999.0)
        assert list_snapshots("finance", artha_dir=tmp_path) == []
