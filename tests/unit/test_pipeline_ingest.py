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


# ---------------------------------------------------------------------------
# Ensure scripts/ is importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"

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


# ---------------------------------------------------------------------------
# Pipeline post-fetch & post-process quality gates (WP-02)
# ---------------------------------------------------------------------------

def _gate_run(name="google_calendar", records=3, rejected=0, error=None, scopes=()):
    from lib import connector_coverage as cc
    return cc.ConnectorRun(name, records, rejected, [], error, list(scopes), 1.0)


def _failed_gates(checks):
    return [c.__name__ for c in checks if not c()]


def test_post_fetch_quality_gates():
    import pipeline as pl

    assert _failed_gates(pl._post_fetch_checks([_gate_run(), _gate_run("gmail", 40)], {}, error_count=0)) == []
    assert _failed_gates(pl._post_fetch_checks([_gate_run(records=0)], {}, error_count=0)) == []
    assert "no_degraded_or_unavailable_scope" in _failed_gates(pl._post_fetch_checks([_gate_run(records=0, rejected=9)], {}, error_count=0))

    failed_auth = _failed_gates(pl._post_fetch_checks([_gate_run(records=0, error="invalid_grant")], {}, error_count=1))
    assert "no_degraded_or_unavailable_scope" in failed_auth
    assert "at_least_one_connector_succeeded" in failed_auth

    chronic = pl._post_fetch_checks(
        [_gate_run(records=0, error="x")], {"google_calendar": {"failing_since": "2026-08-30T00:00:00Z"}},
        error_count=1, now="2026-10-11T00:00:00Z",
    )
    assert "no_chronic_connector_failure" in _failed_gates(chronic)
    recent = pl._post_fetch_checks(
        [_gate_run(records=0, error="x")], {"google_calendar": {"failing_since": "2026-10-10T00:00:00Z"}},
        error_count=1, now="2026-10-11T00:00:00Z",
    )
    assert "no_chronic_connector_failure" not in _failed_gates(recent)


def test_post_process_checks_and_gate_failure_message(tmp_path):
    import pipeline as pl
    from lib.quality_gate import evaluate_gate

    assert "no_rejected_records" in _failed_gates(pl._post_process_checks([_gate_run(rejected=2)], total_records=5, output_path=None))
    missing = tmp_path / "out.jsonl"
    assert "output_snapshot_written" in _failed_gates(pl._post_process_checks([_gate_run()], total_records=5, output_path=missing))
    missing.write_text('{"id": "1"}\n', encoding="utf-8")
    assert _failed_gates(pl._post_process_checks([_gate_run()], total_records=5, output_path=missing)) == []
    assert _failed_gates(pl._post_process_checks([_gate_run(records=0)], total_records=0, output_path=tmp_path / "x")) == []

    checks = pl._post_fetch_checks([_gate_run(records=0, rejected=3)], {}, error_count=0)
    msg = pl._gate_failure_message("post_fetch", evaluate_gate("post_fetch", checks, max_retries=0))
    assert "no_degraded_or_unavailable_scope" in msg and "connectors failed" not in msg


# ---------------------------------------------------------------------------
# Connector record schema validation
# ---------------------------------------------------------------------------

def test_connector_record_schema_validation_and_frozen():
    import pytest
    from schemas.connector_record import ConnectorRecord, validate_record

    raw = {"id": "r1", "source": "gmail", "date_iso": "2026-04-14", "subject": "Test", "body": "Hello", "labels": ["inbox"]}
    rec = validate_record(raw)
    assert isinstance(rec, ConnectorRecord) and rec.raw == raw
    with pytest.raises((AttributeError, TypeError)):
        rec.id = "mutated"  # type: ignore[misc]

    for bad, exc_t, match in [
        ({"source": "gmail", "date_iso": "2026-04-14"}, ValueError, "missing required field 'id'"),
        ({"id": "x", "date_iso": "2026-04-14"}, ValueError, "missing required field 'source'"),
        ({"id": "x", "source": "gmail"}, ValueError, "missing required field 'date_iso'"),
        ("not a dict", TypeError, "must be a dict"),
        ({"id": "", "source": "gmail", "date_iso": "2026-04-14"}, ValueError, "non-empty string"),
        ({"id": 123, "source": "gmail", "date_iso": "2026-04-14"}, ValueError, "must be str"),
    ]:
        with pytest.raises(exc_t, match=match):
            validate_record(bad)

    src = (_SCRIPTS_DIR / "pipeline.py").read_text(encoding="utf-8")
    assert '"validation_errors"' in src or "'validation_errors'" in src
    assert "_SCHEMA_VALIDATION_AVAILABLE" in src
