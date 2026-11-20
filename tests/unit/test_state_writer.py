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

import base64
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# conftest adds scripts/ and project root to sys.path
from lib.state_writer import (
    StateWriteConflict,
    WriteResult,
    create_if_absent_token,
    read_versioned,
    write as _raw_write,
    write_atomic,
)


def write(path, content, **kwargs):
    if "expected_token" not in kwargs:
        token = read_versioned(path)[1] if path.exists() else create_if_absent_token(path)
        kwargs["expected_token"] = token
    return _raw_write(path, content, **kwargs)


class _FakeSnapshotVault:
    def encrypt_artifact(self, text):
        return b"sealed:" + base64.b64encode(text.encode("utf-8"))

    def read_artifact(self, path):
        raw = path.read_bytes()
        return base64.b64decode(raw[len(b"sealed:"):]).decode("utf-8")


@pytest.fixture(autouse=True)
def encrypted_snapshot_storage(tmp_path, monkeypatch):
    from lib import state_snapshot

    monkeypatch.setenv(
        "ARTHA_LOCAL_DIR", str(tmp_path.parent / f"{tmp_path.name}-local")
    )
    monkeypatch.setattr(
        state_snapshot,
        "_vault_for_snapshots",
        lambda _directory: _FakeSnapshotVault(),
    )


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
        assert _FakeSnapshotVault().read_artifact(result.snapshot_path) == "original"

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


class TestConflictAwarePublication:
    def test_post_publication_conflict_returns_uncertain(
        self, tmp_path, monkeypatch
    ):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        real_replace = os.replace

        def replace_then_create_conflict(source, destination):
            real_replace(source, destination)
            (tmp_path / "state-HOST-2.md").write_text("other", encoding="utf-8")

        monkeypatch.setattr(os, "replace", replace_then_create_conflict)
        monkeypatch.setattr(
            "lib.sync_conflicts.runtime_hostnames",
            lambda: ("HOST-2",),
        )
        with _passthrough_middleware():
            result = write(target, "proposed", domain="test", source="test", snapshot=False)
        assert result.success is False
        assert result.uncertain is True
        assert result.conflicts_after_write

    def test_conflict_copy_refuses_write_before_snapshot_or_replace(
        self, tmp_path, monkeypatch
    ):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        conflict = tmp_path / "state-SYNTH-HOST-AB12.md"
        conflict.write_text("other writer", encoding="utf-8")
        _, token = read_versioned(target)
        monkeypatch.setattr(
            "lib.sync_conflicts.runtime_hostnames",
            lambda: ("SYNTH-HOST-AB12",),
        )

        with _passthrough_middleware(), pytest.raises(StateWriteConflict, match="conflict"):
            write(
                target,
                "proposed",
                expected_token=token,
                domain="test",
                source="test",
                snapshot=True,
            )

        assert target.read_text(encoding="utf-8") == "original"
        assert conflict.read_text(encoding="utf-8") == "other writer"

    def test_omitted_token_refuses_existing_target_without_minting(self, tmp_path):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")

        with _passthrough_middleware(), pytest.raises(TypeError, match="expected_token"):
            _raw_write(target, "proposed", domain="test", source="test", snapshot=False)

        assert target.read_text(encoding="utf-8") == "original"

    def test_omitted_token_refuses_create_without_minting(self, tmp_path):
        target = tmp_path / "state.md"
        with _passthrough_middleware(), pytest.raises(TypeError, match="expected_token"):
            _raw_write(target, "proposed", domain="test", source="test", snapshot=False)
        assert not target.exists()

    def test_read_versioned_requires_existing_target(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_versioned(tmp_path / "missing.md")

    def test_stale_original_token_refuses_overwrite(self, tmp_path):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        original, token = read_versioned(target)
        target.write_text("another writer", encoding="utf-8")

        with _passthrough_middleware(), pytest.raises(StateWriteConflict, match="changed"):
            write(
                target,
                original + " proposed",
                expected_token=token,
                domain="test",
                source="test",
                snapshot=False,
            )

        assert target.read_text(encoding="utf-8") == "another writer"

    def test_replacing_target_with_identical_bytes_refuses_stale_token(self, tmp_path):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        _, token = read_versioned(target)
        replacement = tmp_path / "replacement.md"
        replacement.write_text("original", encoding="utf-8")
        replacement.replace(target)

        with _passthrough_middleware(), pytest.raises(StateWriteConflict, match="changed"):
            write(
                target,
                "proposed",
                expected_token=token,
                domain="test",
                source="test",
                snapshot=False,
            )

        assert target.read_text(encoding="utf-8") == "original"

    def test_identical_bytes_substitution_after_replace_is_uncertain(
        self, tmp_path, monkeypatch
    ):
        import os

        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        replacement = tmp_path / "replacement.md"
        real_replace = os.replace

        def replace_then_substitute(source, destination):
            real_replace(source, destination)
            if Path(destination) == target:
                replacement.write_bytes(target.read_bytes())
                real_replace(replacement, target)

        monkeypatch.setattr(os, "replace", replace_then_substitute)
        with _passthrough_middleware():
            result = write(
                target,
                "proposed",
                domain="test",
                source="test",
                snapshot=False,
            )

        assert result.success is False
        assert result.uncertain is True
        assert target.read_bytes() == b"proposed"
        assert any("identity" in entry for entry in result.middleware_log)

    def test_mutated_staged_object_after_replace_is_uncertain(
        self, tmp_path, monkeypatch
    ):
        import os

        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        real_replace = os.replace

        def replace_then_mutate(source, destination):
            real_replace(source, destination)
            if Path(destination) == target:
                target.write_bytes(b"substituted")

        monkeypatch.setattr(os, "replace", replace_then_mutate)
        with _passthrough_middleware():
            result = write(
                target,
                "proposed",
                domain="test",
                source="test",
                snapshot=False,
            )

        assert result.success is False
        assert result.uncertain is True
        assert target.read_bytes() == b"substituted"
        assert any("readback" in entry for entry in result.middleware_log)

    def test_create_token_refuses_target_created_after_read(self, tmp_path):
        target = tmp_path / "state.md"
        token = create_if_absent_token(target)
        target.write_text("created elsewhere", encoding="utf-8")

        with _passthrough_middleware(), pytest.raises(StateWriteConflict, match="created"):
            write(
                target,
                "proposed",
                expected_token=token,
                domain="test",
                source="test",
                snapshot=False,
            )

        assert target.read_text(encoding="utf-8") == "created elsewhere"

    def test_change_during_middleware_is_refused_before_replace(self, tmp_path):
        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        _, token = read_versioned(target)
        middleware = _make_mock_middleware(return_value="proposed")

        def before_write(*_args, **_kwargs):
            target.write_text("concurrent", encoding="utf-8")
            return "proposed"

        middleware.before_write.side_effect = before_write
        with _mock_middleware(middleware), pytest.raises(StateWriteConflict, match="changed"):
            write(
                target,
                "proposed",
                expected_token=token,
                domain="test",
                source="test",
                snapshot=False,
            )
        assert target.read_text(encoding="utf-8") == "concurrent"

    def test_snapshot_failure_blocks_publication(self, tmp_path, monkeypatch):
        from lib import state_snapshot

        target = tmp_path / "state.md"
        target.write_text("original", encoding="utf-8")
        monkeypatch.setattr(state_snapshot, "snapshot", lambda *_args, **_kwargs: None)
        with _passthrough_middleware():
            result = write(
                target,
                "proposed",
                domain="test",
                source="test",
                snapshot=True,
            )
        assert result.success is False
        assert target.read_text(encoding="utf-8") == "original"


class TestStructuralFrontmatter:
    def test_retired_occ_write_entry_point_is_absent(self):
        from lib import state_writer

        assert not hasattr(state_writer, "write_occ")

    def test_frontmatter_injection_preserves_crlf_body_and_delimiter_like_text(self):
        from lib.state_writer import _inject_occ_fields

        body = "# Heading\r\n---\r\nThis body delimiter is not frontmatter.\r\n"
        source = "---\r\n# retained\r\ndomain: finance\r\n---\r\n" + body
        with patch("lib.state_writer.datetime") as clock:
            clock.now.return_value.isoformat.return_value = "2026-11-18T00:00:00+00:00"
            changed = _inject_occ_fields(source, 2, "test")

        assert changed.endswith(body)
        assert changed.count("domain: finance") == 1
        assert "# retained\r\n" in changed
        assert "\r\nlast_written_at: 2026-11-18T00:00:00+00:00\r\n" in changed
        assert changed.startswith("---\r\n# retained\r\ndomain: finance\r\nversion: 2\r\n")

    def test_version_lookup_is_limited_to_frontmatter(self):
        from lib.state_writer import _extract_occ_version

        text = "---\ndomain: finance\n---\nversion: 99\nbody\n"
        assert _extract_occ_version(text) == 0

    def test_closing_delimiter_at_eof_round_trips(self):
        from lib.state_writer import _inject_occ_fields

        changed = _inject_occ_fields("---\ndomain: finance\n---", 1, "test")
        assert changed.startswith("---\ndomain: finance\nversion: 1\n")
        assert changed.endswith("---")

    def test_duplicate_occ_field_is_rejected(self):
        from lib.state_writer import _inject_occ_fields

        text = "---\ndomain: finance\nversion: 1\nversion: 2\n---\nbody\n"
        with pytest.raises(ValueError, match="duplicate"):
            _inject_occ_fields(text, 3, "test")


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
