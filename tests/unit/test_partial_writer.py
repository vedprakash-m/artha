"""tests/unit/test_partial_writer.py — Tests for partial_writer.py. specs/steal.md §15.4.1"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

from partial_writer import (
    PartialResult,
    assemble_partials,
    cleanup_partials,
    write_partial,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(run_id: str, provider: str, status: str = "ok", data=None, error=None):
    return PartialResult(
        run_id=run_id,
        provider=provider,
        timestamp="2026-01-01T00:00:00+00:00",
        status=status,
        data=data or {"signals": [f"sig-{provider}"]},
        error=error,
    )


# ---------------------------------------------------------------------------
# write_partial
# ---------------------------------------------------------------------------

class TestWritePartial:
    def test_write_partial_creates_file(self, tmp_path):
        result = _make_result("run-001", "ado")
        path = write_partial(tmp_path, result)

        assert path.exists()
        assert path.name == "partial_run-001_ado.json"

    def test_write_partial_content_is_valid_json(self, tmp_path):
        result = _make_result("run-001", "ado")
        path = write_partial(tmp_path, result)

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["run_id"] == "run-001"
        assert payload["provider"] == "ado"
        assert payload["status"] == "ok"
        assert "signals" in payload["data"]

    def test_write_partial_creates_tmp_dir_if_missing(self, tmp_path):
        artha_dir = tmp_path / "nested" / "artha"
        result = _make_result("run-001", "ado")
        path = write_partial(artha_dir, result)
        assert path.exists()

    def test_write_partial_rejects_invalid_status(self, tmp_path):
        result = _make_result("run-001", "ado", status="bad_status")
        with pytest.raises(ValueError, match="Invalid status"):
            write_partial(tmp_path, result)

    def test_write_partial_preserves_error_field(self, tmp_path):
        result = _make_result("run-001", "ado", status="error", error="auth failed")
        path = write_partial(tmp_path, result)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["error"] == "auth failed"


# ---------------------------------------------------------------------------
# assemble_partials
# ---------------------------------------------------------------------------

class TestAssemblePartials:
    def test_assemble_merges_ok_partials(self, tmp_path):
        write_partial(tmp_path, _make_result("run-001", "ado", data={"ado": [1]}))
        write_partial(tmp_path, _make_result("run-001", "email", data={"email": [2]}))

        merged, warnings = assemble_partials(tmp_path, "run-001")

        assert "ado" in merged
        assert "email" in merged
        assert merged["ado"] == {"ado": [1]}
        assert merged["email"] == {"email": [2]}

    def test_assemble_skips_error_partials_with_warning(self, tmp_path):
        write_partial(tmp_path, _make_result("run-001", "ado", data={"ado": [1]}))
        write_partial(tmp_path, _make_result("run-001", "email", status="error", data={}))
        write_partial(tmp_path, _make_result("run-001", "teams", status="timeout", data={}))

        merged, warnings = assemble_partials(tmp_path, "run-001")

        assert "ado" in merged
        assert "email" not in merged
        assert "teams" not in merged
        assert any("email" in w for w in warnings)
        assert any("teams" in w for w in warnings)

    def test_assemble_handles_missing_partials(self, tmp_path):
        merged, warnings = assemble_partials(tmp_path, "nonexistent-run")

        assert merged == {}
        assert any("nonexistent-run" in w for w in warnings)

    def test_assemble_only_returns_matching_run_id(self, tmp_path):
        write_partial(tmp_path, _make_result("run-001", "ado"))
        write_partial(tmp_path, _make_result("run-999", "ado", data={"other": True}))

        merged, _ = assemble_partials(tmp_path, "run-001")

        assert "ado" in merged
        assert merged["ado"].get("other") is not True

    def test_assemble_handles_corrupt_file(self, tmp_path):
        (tmp_path / "tmp").mkdir()
        (tmp_path / "tmp" / "partial_run-001_bad.json").write_text("NOT JSON", encoding="utf-8")

        merged, warnings = assemble_partials(tmp_path, "run-001")

        assert any("bad.json" in w or "partial_run-001_bad" in w for w in warnings)


# ---------------------------------------------------------------------------
# cleanup_partials
# ---------------------------------------------------------------------------

class TestCleanupPartials:
    def test_cleanup_deletes_aged_files(self, tmp_path):
        # Write a partial, then backdate its mtime to 25h ago
        result = _make_result("run-old", "ado")
        path = write_partial(tmp_path, result)
        old_time = time.time() - (25 * 3600)
        os.utime(path, (old_time, old_time))

        deleted = cleanup_partials(tmp_path, max_age_hours=24)
        assert deleted == 1
        assert not path.exists()

    def test_cleanup_spares_recent_files(self, tmp_path):
        result = _make_result("run-new", "ado")
        path = write_partial(tmp_path, result)

        deleted = cleanup_partials(tmp_path, max_age_hours=24)
        assert deleted == 0
        assert path.exists()

    def test_cleanup_returns_zero_when_no_tmp_dir(self, tmp_path):
        empty_dir = tmp_path / "no-tmp-here"
        empty_dir.mkdir()
        deleted = cleanup_partials(empty_dir, max_age_hours=24)
        assert deleted == 0

    def test_cleanup_only_affects_partial_files(self, tmp_path):
        result = _make_result("run-old", "ado")
        path = write_partial(tmp_path, result)
        old_time = time.time() - (25 * 3600)
        os.utime(path, (old_time, old_time))

        # A non-partial file in tmp/ should NOT be deleted
        other = tmp_path / "tmp" / "other_file.json"
        other.write_text("{}", encoding="utf-8")
        os.utime(other, (old_time, old_time))

        deleted = cleanup_partials(tmp_path, max_age_hours=24)
        assert deleted == 1
        assert other.exists()  # preserved
        assert not path.exists()  # partial deleted
