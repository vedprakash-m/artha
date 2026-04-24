"""Unit tests for pipeline.py --archive-brief subcommand.

Tests: ok, skipped-duplicate, failed-path-missing, path-traversal-rejected,
       early-exit (no connector machinery loaded).

Execution log coverage:
    [Step 5] | pipeline.py --archive-brief | early-exit subcommand validation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_archive_brief(raw_path: str, artha_dir: Path) -> tuple[str, int]:
    """Run _cmd_archive_brief() with _REPO_ROOT patched to artha_dir.

    Returns (stdout_json_str, return_code).
    """
    import io
    import lib.briefing_archive as _ba_module

    # Patch _ba module paths to artha_dir
    briefings = artha_dir / "briefings"
    briefings.mkdir(exist_ok=True)
    tmp = artha_dir / "tmp"
    tmp.mkdir(exist_ok=True)

    import pipeline as _pipeline

    captured_output = []

    def fake_print(obj):
        captured_output.append(str(obj))

    original_repo_root = _pipeline._REPO_ROOT

    try:
        # Patch _REPO_ROOT and print in _cmd_archive_brief's scope
        _pipeline._REPO_ROOT = artha_dir
        # Also patch briefing_archive paths
        _ba_module._ARTHA_DIR = artha_dir
        _ba_module._BRIEFINGS_DIR = briefings
        _ba_module._TMP_DIR = tmp
        _ba_module._DRAFT_PATH = tmp / "briefing_draft.md"
        state = artha_dir / "state"
        state.mkdir(exist_ok=True)
        _ba_module._STATE_DIR = state
        _ba_module._AUDIT_LOG = state / "audit.md"
        _ba_module._HEALTH_CHECK = state / "health-check.md"
        # Disable security checks
        _ba_module._run_injection_check = lambda text: False  # type: ignore[assignment]
        _ba_module._run_pii_warning = lambda text, source: None  # type: ignore[assignment]

        import builtins
        original_print = builtins.print

        def capturing_print(*args, **kwargs):
            if not kwargs.get("file"):
                captured_output.append(" ".join(str(a) for a in args))
            else:
                original_print(*args, **kwargs)

        builtins.print = capturing_print
        try:
            rc = _pipeline._cmd_archive_brief(raw_path)
        finally:
            builtins.print = original_print
    finally:
        _pipeline._REPO_ROOT = original_repo_root

    output = captured_output[-1] if captured_output else "{}"
    return output, rc


# ---------------------------------------------------------------------------
# Path traversal guard
# ---------------------------------------------------------------------------

class TestPathTraversalGuard:
    def test_rejects_path_outside_tmp(self, tmp_path):
        out, rc = _run_archive_brief("../state/secrets.md", tmp_path)
        data = json.loads(out)
        assert rc == 2
        assert data["status"] == "failed"
        assert "tmp/" in data["error"]

    def test_rejects_absolute_path(self, tmp_path):
        out, rc = _run_archive_brief("/etc/passwd", tmp_path)
        data = json.loads(out)
        assert rc == 2
        assert data["status"] == "failed"

    def test_rejects_dotdot_in_subpath(self, tmp_path):
        out, rc = _run_archive_brief("tmp/../../state/malicious.md", tmp_path)
        data = json.loads(out)
        assert rc == 2
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# Path missing handling
# ---------------------------------------------------------------------------

class TestPathMissing:
    def test_missing_draft_no_today_entry_returns_failed(self, tmp_path):
        # No draft file, no today's briefing entry
        out, rc = _run_archive_brief("tmp/briefing_draft.md", tmp_path)
        data = json.loads(out)
        assert rc == 1
        assert data["status"] == "failed"

    def test_missing_draft_but_today_entry_exists_returns_skipped(self, tmp_path):
        import datetime
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        briefings = tmp_path / "briefings"
        briefings.mkdir(exist_ok=True)
        (briefings / f"{today}.md").write_text("existing entry")

        out, rc = _run_archive_brief("tmp/briefing_draft.md", tmp_path)
        data = json.loads(out)
        assert rc == 0
        assert data["status"] == "skipped"
        assert data["reason"] == "path_missing_but_today_exists"


# ---------------------------------------------------------------------------
# Successful archive
# ---------------------------------------------------------------------------

class TestSuccessfulArchive:
    def test_ok_result_on_valid_draft(self, tmp_path):
        draft = tmp_path / "tmp"
        draft.mkdir(exist_ok=True)
        (draft / "briefing_draft.md").write_text("Today's complete briefing")
        out, rc = _run_archive_brief("tmp/briefing_draft.md", tmp_path)
        data = json.loads(out)
        assert rc == 0
        assert data["status"] == "ok"
        assert data["bytes_written"] > 0

    def test_draft_deleted_after_success(self, tmp_path):
        draft_file = tmp_path / "tmp" / "briefing_draft.md"
        draft_file.parent.mkdir(exist_ok=True)
        draft_file.write_text("Briefing content to be archived")
        _run_archive_brief("tmp/briefing_draft.md", tmp_path)
        assert not draft_file.exists()

    def test_briefing_file_created_in_briefings_dir(self, tmp_path):
        import datetime
        draft_file = tmp_path / "tmp" / "briefing_draft.md"
        draft_file.parent.mkdir(exist_ok=True)
        draft_file.write_text("Full briefing for today")
        _run_archive_brief("tmp/briefing_draft.md", tmp_path)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        assert (tmp_path / "briefings" / f"{today}.md").exists()


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_second_identical_archive_returns_skipped(self, tmp_path):
        draft_dir = tmp_path / "tmp"
        draft_dir.mkdir(exist_ok=True)

        # First archive
        (draft_dir / "briefing_draft.md").write_text("Identical briefing content")
        out1, rc1 = _run_archive_brief("tmp/briefing_draft.md", tmp_path)
        data1 = json.loads(out1)
        assert rc1 == 0
        assert data1["status"] == "ok"

        # Second archive — same content
        (draft_dir / "briefing_draft.md").write_text("Identical briefing content")
        out2, rc2 = _run_archive_brief("tmp/briefing_draft.md", tmp_path)
        data2 = json.loads(out2)
        assert rc2 == 0
        assert data2["status"] == "skipped"
        assert data2["reason"] == "duplicate"


# ---------------------------------------------------------------------------
# Early-exit guard: run_pipeline must NOT be called
# ---------------------------------------------------------------------------

class TestEarlyExitNoPipelineLoad:
    def test_run_pipeline_not_called(self, tmp_path, monkeypatch):
        """--archive-brief must not invoke the full pipeline machinery."""
        import pipeline as _pipeline

        called = []

        original_run = _pipeline.run_pipeline

        def spy_run_pipeline(*args, **kwargs):
            called.append(True)
            return original_run(*args, **kwargs)

        monkeypatch.setattr(_pipeline, "run_pipeline", spy_run_pipeline)

        draft = tmp_path / "tmp" / "briefing_draft.md"
        draft.parent.mkdir(exist_ok=True)
        draft.write_text("briefing text")

        _run_archive_brief("tmp/briefing_draft.md", tmp_path)

        assert not called, "run_pipeline was invoked — early-exit failed!"
