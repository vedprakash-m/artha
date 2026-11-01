"""tests/unit/test_pipeline_ingest.py — Unit tests for _ingest_pending_briefs().

Verifies:
  1. Staged briefing_incoming_<runtime>.md is consumed and deleted on success.
  2. File is renamed to .failed when briefing_archive.save() raises.
  3. No-op when tmp/ contains no staged files.

Ref: specs/rebrief.md §3.2, §3.6
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure scripts/ is importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

os = __import__("os")
os.environ.setdefault("ARTHA_NO_REEXEC", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_ingest():
    """Import _ingest_pending_briefs from pipeline, isolated per test."""
    import importlib
    # Ensure pipeline can be imported without heavy connector deps
    import pipeline as _pl
    importlib.reload(_pl)
    return _pl._ingest_pending_briefs, _pl


def _make_fake_archive_module(*, status: str = "ok", raise_exc: Exception | None = None):
    """Return a fake lib.briefing_archive module with a controlled save()."""
    mod = types.ModuleType("lib.briefing_archive")

    def _save(*_a, **_kw):
        if raise_exc is not None:
            raise raise_exc
        return {"status": status}

    mod.save = _save  # type: ignore[attr-defined]
    return mod


def _patch_archive_module(monkeypatch, fake_archive) -> None:
    """Patch lib.briefing_archive in both sys.modules AND the lib package attribute.

    After importlib.reload() in test_briefing_archive.py tests, Python caches the
    real briefing_archive module as an attribute on the lib package object. A bare
    sys.modules patch alone is not sufficient because `from lib import briefing_archive`
    resolves via the package attribute first. Both must be patched for test isolation.
    """
    monkeypatch.setitem(sys.modules, "lib.briefing_archive", fake_archive)
    lib_pkg = sys.modules.get("lib")
    if lib_pkg is not None:
        monkeypatch.setattr(lib_pkg, "briefing_archive", fake_archive, raising=False)


# ---------------------------------------------------------------------------
# T-RI-01: staged file is consumed (unlinked) on successful save()
# ---------------------------------------------------------------------------

def test_ingest_picks_up_staged_file(tmp_path, monkeypatch):
    """_ingest_pending_briefs() ingests briefing_incoming_gemini.md → deleted on ok."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True)
    staging = tmp_dir / "briefing_incoming_gemini.md"
    staging.write_text("Gemini catch-up content.", encoding="utf-8")

    fake_archive = _make_fake_archive_module(status="ok")

    import pipeline as pl
    monkeypatch.setattr(pl, "_REPO_ROOT", tmp_path)
    _patch_archive_module(monkeypatch, fake_archive)
    # Suppress telemetry
    monkeypatch.setitem(sys.modules, "lib.telemetry", types.ModuleType("lib.telemetry"))

    pl._ingest_pending_briefs()

    assert not staging.exists(), "Staged file should be unlinked after successful ingest"


def test_ingest_picks_up_staged_file_skipped(tmp_path, monkeypatch):
    """_ingest_pending_briefs() also unlinks file when save() returns 'skipped'."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True)
    staging = tmp_dir / "briefing_incoming_vscode.md"
    staging.write_text("VS Code duplicate content.", encoding="utf-8")

    fake_archive = _make_fake_archive_module(status="skipped")

    import pipeline as pl
    monkeypatch.setattr(pl, "_REPO_ROOT", tmp_path)
    _patch_archive_module(monkeypatch, fake_archive)
    monkeypatch.setitem(sys.modules, "lib.telemetry", types.ModuleType("lib.telemetry"))

    pl._ingest_pending_briefs()

    assert not staging.exists(), "Staged file should be unlinked on skipped (dedup)"


# ---------------------------------------------------------------------------
# T-RI-02: file is renamed to .failed when save() raises
# ---------------------------------------------------------------------------

def test_ingest_renames_to_failed_on_save_error(tmp_path, monkeypatch):
    """_ingest_pending_briefs() renames to .failed if save() raises an exception."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True)
    staging = tmp_dir / "briefing_incoming_claude.md"
    staging.write_text("Claude briefing content.", encoding="utf-8")

    fake_archive = _make_fake_archive_module(raise_exc=RuntimeError("Injection detector unavailable"))

    import pipeline as pl
    monkeypatch.setattr(pl, "_REPO_ROOT", tmp_path)
    _patch_archive_module(monkeypatch, fake_archive)
    monkeypatch.setitem(sys.modules, "lib.telemetry", types.ModuleType("lib.telemetry"))

    pl._ingest_pending_briefs()

    failed = tmp_dir / "briefing_incoming_claude.failed"
    assert failed.exists(), ".failed file should exist after save() exception"
    assert not staging.exists(), "Original .md should be renamed, not both present"


# ---------------------------------------------------------------------------
# T-RI-03: no-op when tmp/ contains no staged files
# ---------------------------------------------------------------------------

def test_ingest_noop_when_no_staged_files(tmp_path, monkeypatch):
    """_ingest_pending_briefs() silently returns when tmp/ has no staged files."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True)
    # Place an unrelated file to ensure glob is selective
    (tmp_dir / "pipeline_metrics.json").write_text("[]", encoding="utf-8")

    fake_archive = _make_fake_archive_module(status="ok")

    import pipeline as pl
    monkeypatch.setattr(pl, "_REPO_ROOT", tmp_path)
    _patch_archive_module(monkeypatch, fake_archive)
    monkeypatch.setitem(sys.modules, "lib.telemetry", types.ModuleType("lib.telemetry"))

    # Should not raise
    pl._ingest_pending_briefs()


def test_ingest_noop_when_tmp_missing(tmp_path, monkeypatch):
    """_ingest_pending_briefs() silently returns when tmp/ does not exist."""
    # tmp_path exists but no tmp/ sub-dir
    import pipeline as pl
    monkeypatch.setattr(pl, "_REPO_ROOT", tmp_path)

    pl._ingest_pending_briefs()  # no exception
