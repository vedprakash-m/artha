"""tests/unit/test_state_writer.py — Unit tests for scripts/lib/state_writer.py

Wave 0 verification suite (specs/agent-fw.md §7).

Coverage:
  - write() creates a file atomically (tempfile → os.replace pattern)
  - write() returns WriteResult with success=True on success
  - write() creates a snapshot when file already exists (snapshot=True)
  - write() skips snapshot for new files
  - write() returns success=False when middleware blocks the write
  - write() cleans up tempfile on OS error (rollback)
  - write() calls middleware before_write and after_write
  - write_atomic() creates file without middleware
  - write_atomic() returns WriteResult with success=True on success
  - write_atomic() is crash-safe (atomic write)
  - WriteResult fields: path, success, snapshot_path, middleware_log
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest adds scripts/ and project root to sys.path
from lib.state_writer import WriteResult, write, write_atomic


# ---------------------------------------------------------------------------
# WriteResult dataclass
# ---------------------------------------------------------------------------


class TestWriteResult:
    def test_default_fields(self, tmp_path):
        r = WriteResult(path=tmp_path / "state.md", success=True)
        assert r.snapshot_path is None
        assert r.middleware_log == []

    def test_fields_assigned(self, tmp_path):
        snap = tmp_path / "snap.bak"
        r = WriteResult(
            path=tmp_path / "state.md",
            success=False,
            snapshot_path=snap,
            middleware_log=["a", "b"],
        )
        assert r.success is False
        assert r.snapshot_path == snap
        assert r.middleware_log == ["a", "b"]


# ---------------------------------------------------------------------------
# write_atomic
# ---------------------------------------------------------------------------


class TestWriteAtomic:
    def test_creates_file(self, tmp_path):
        target = tmp_path / "checkpoint.json"
        result = write_atomic(target, '{"step": 1}')
        assert result.success is True
        assert target.exists()
        assert target.read_text(encoding="utf-8") == '{"step": 1}'

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "checkpoint.json"
        target.write_text("old", encoding="utf-8")
        result = write_atomic(target, "new")
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "new"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "dir" / "checkpoint.json"
        result = write_atomic(target, "data")
        assert result.success is True
        assert target.exists()

    def test_result_path_matches(self, tmp_path):
        target = tmp_path / "f.json"
        result = write_atomic(target, "x")
        assert result.path == target

    def test_no_leftover_tmp_file_on_success(self, tmp_path):
        target = tmp_path / "f.json"
        write_atomic(target, "x")
        tmp_files = list(tmp_path.glob(".f.json-*.tmp"))
        assert tmp_files == [], "Temp file leaked after successful write"

    def test_accepts_kwargs_gracefully(self, tmp_path):
        """write_atomic accepts **kwargs for forward-compat — they are ignored."""
        target = tmp_path / "f.json"
        result = write_atomic(target, "data", domain="test", source="test")
        assert result.success is True


# ---------------------------------------------------------------------------
# write() — atomic write
# ---------------------------------------------------------------------------


class TestWriteAtomic_Core:
    """Test that write() uses an atomic tempfile-then-replace pattern."""

    def test_creates_new_file(self, tmp_path):
        target = tmp_path / "state" / "memory.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Use passthrough middleware by patching _build_middleware_stack
        with _passthrough_middleware():
            result = write(target, "# Memory\n", domain="memory", source="test")
        assert result.success is True
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "# Memory\n"

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "state.md"
        target.write_text("old content", encoding="utf-8")
        with _passthrough_middleware():
            result = write(target, "new content", domain="test", source="test")
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "new content"

    def test_no_leftover_tmp_on_success(self, tmp_path):
        target = tmp_path / "state.md"
        with _passthrough_middleware():
            write(target, "content", domain="test", source="test")
        tmp_files = list(tmp_path.glob(".state.md-*.tmp"))
        assert tmp_files == [], "Temp file leaked after successful write"

    def test_result_path_matches(self, tmp_path):
        target = tmp_path / "state.md"
        with _passthrough_middleware():
            result = write(target, "x", domain="test", source="test")
        assert result.path == target

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "state.md"
        with _passthrough_middleware():
            result = write(target, "x", domain="test", source="test")
        assert result.success is True
        assert target.exists()


# ---------------------------------------------------------------------------
# write() — snapshot behaviour
# ---------------------------------------------------------------------------


class TestWriteSnapshot:
    def test_snapshot_created_for_existing_file(self, tmp_path):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        with _passthrough_middleware():
            result = write(
                target, "updated", domain="test", source="test", snapshot=True
            )
        assert result.success is True
        assert result.snapshot_path is not None
        assert result.snapshot_path.exists()
        assert result.snapshot_path.read_text(encoding="utf-8") == "original"

    def test_no_snapshot_for_new_file(self, tmp_path):
        target = tmp_path / "new_state.md"
        with _passthrough_middleware():
            result = write(
                target, "content", domain="test", source="test", snapshot=True
            )
        assert result.snapshot_path is None

    def test_snapshot_skipped_when_disabled(self, tmp_path):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        with _passthrough_middleware():
            result = write(
                target, "updated", domain="test", source="test", snapshot=False
            )
        assert result.snapshot_path is None


# ---------------------------------------------------------------------------
# write() — middleware
# ---------------------------------------------------------------------------


class TestWriteMiddleware:
    def test_middleware_before_write_called(self, tmp_path):
        target = tmp_path / "state.md"
        mock_mw = _make_mock_middleware(return_value="approved")
        with _mock_middleware(mock_mw):
            write(target, "proposed", domain="finance", source="test", snapshot=False)
        mock_mw.before_write.assert_called_once()

    def test_middleware_after_write_called_on_success(self, tmp_path):
        target = tmp_path / "state.md"
        mock_mw = _make_mock_middleware(return_value="approved")
        with _mock_middleware(mock_mw):
            write(target, "proposed", domain="finance", source="test", snapshot=False)
        mock_mw.after_write.assert_called_once()

    def test_middleware_blocked_write_returns_failure(self, tmp_path):
        target = tmp_path / "state.md"
        mock_mw = _make_mock_middleware(return_value=None)  # block
        with _mock_middleware(mock_mw):
            result = write(
                target, "blocked", domain="test", source="test", snapshot=False
            )
        assert result.success is False
        assert not target.exists()

    def test_blocked_write_leaves_no_file(self, tmp_path):
        target = tmp_path / "to_block.md"
        mock_mw = _make_mock_middleware(return_value=None)
        with _mock_middleware(mock_mw):
            write(target, "x", domain="test", source="test", snapshot=False)
        assert not target.exists()

    def test_middleware_can_modify_content(self, tmp_path):
        target = tmp_path / "state.md"
        mock_mw = _make_mock_middleware(return_value="MODIFIED CONTENT")
        with _mock_middleware(mock_mw):
            write(target, "original", domain="test", source="test", snapshot=False)
        assert target.read_text(encoding="utf-8") == "MODIFIED CONTENT"

    def test_after_write_exception_does_not_propagate(self, tmp_path):
        """after_write failures must never fail the write."""
        target = tmp_path / "state.md"
        mock_mw = _make_mock_middleware(return_value="ok")
        mock_mw.after_write.side_effect = RuntimeError("after_write boom")
        with _mock_middleware(mock_mw):
            result = write(
                target, "ok", domain="test", source="test", snapshot=False
            )
        assert result.success is True  # write still succeeded

    def test_pii_check_false_skips_pii_middleware(self, tmp_path):
        """pii_check=False should produce a stack without PIIMiddleware."""
        target = tmp_path / "state.md"
        with patch("lib.state_writer._build_middleware_stack") as mock_build:
            from middleware import _PassthroughMiddleware
            mock_build.return_value = _PassthroughMiddleware()
            write(target, "x", domain="test", source="test",
                  pii_check=False, snapshot=False)
        mock_build.assert_called_once_with(pii_check=False)


# ---------------------------------------------------------------------------
# write() — error handling / rollback
# ---------------------------------------------------------------------------


class TestWriteErrorHandling:
    def test_tempfile_cleaned_up_on_write_error(self, tmp_path):
        """If os.replace() fails, the tempfile must be removed."""
        target = tmp_path / "state.md"
        with _passthrough_middleware():
            with patch("os.replace", side_effect=OSError("disk full")):
                result = write(
                    target, "content", domain="test", source="test", snapshot=False
                )
        assert result.success is False
        tmp_files = list(tmp_path.glob(".state.md-*.tmp"))
        assert tmp_files == [], "Tempfile was not cleaned up after OS error"

    def test_middleware_log_contains_error_info(self, tmp_path):
        target = tmp_path / "state.md"
        with _passthrough_middleware():
            with patch("os.replace", side_effect=OSError("disk full")):
                result = write(
                    target, "content", domain="test", source="test", snapshot=False
                )
        assert any("ERROR" in entry for entry in result.middleware_log)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passthrough_middleware():
    """Patch _build_middleware_stack to return a no-op passthrough."""
    from middleware import _PassthroughMiddleware

    return patch(
        "lib.state_writer._build_middleware_stack",
        return_value=_PassthroughMiddleware(),
    )


def _make_mock_middleware(return_value):
    mock = MagicMock()
    mock.before_write.return_value = return_value
    mock.after_write.return_value = None
    return mock


def _mock_middleware(mock_mw):
    return patch(
        "lib.state_writer._build_middleware_stack",
        return_value=mock_mw,
    )
