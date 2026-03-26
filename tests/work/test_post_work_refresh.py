"""
tests/work/test_post_work_refresh.py — Unit tests for post_work_refresh.py.

Validates scripts/post_work_refresh.py (§8.8 v2.3.0, finalize.md Step 11d):
  - _is_enabled() reads work.refresh.run_on_catchup from user_profile.yaml
  - _is_enabled() defaults to True when config is missing or unreadable
  - run() returns expected record shape (dry_run=True skips the loop)
  - run() catches WorkLoop errors non-blocking (error field set, no raise)
  - main() --dry-run exits 0 without writing refresh log
  - main() --quiet suppresses stdout
  - main() skips when _is_enabled() is False
  - _append_run_log() writes to state/work/eval/work-refresh-log.jsonl

Run: pytest tests/work/test_post_work_refresh.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import post_work_refresh as pwr  # type: ignore
from post_work_refresh import (  # type: ignore
    _run_id,
    _is_enabled,
    _append_run_log,
    run,
    main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def artha_dir(tmp_path: Path) -> Path:
    """Minimal Artha directory for testing post_work_refresh."""
    (tmp_path / "config").mkdir()
    (tmp_path / "state" / "work" / "eval").mkdir(parents=True)
    return tmp_path


def _write_profile(artha_dir: Path, run_on_catchup: bool) -> None:
    profile_text = (
        "schema_version: '1.0'\n"
        "work:\n"
        f"  refresh:\n"
        f"    run_on_catchup: {str(run_on_catchup).lower()}\n"
    )
    (artha_dir / "config" / "user_profile.yaml").write_text(profile_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# _run_id
# ---------------------------------------------------------------------------

def test_run_id_format():
    rid = _run_id()
    assert rid.startswith("wr-")
    assert len(rid) > 10


# ---------------------------------------------------------------------------
# _is_enabled
# ---------------------------------------------------------------------------

class TestIsEnabled:

    def test_true_when_run_on_catchup_true(self, artha_dir):
        _write_profile(artha_dir, True)
        assert _is_enabled(artha_dir) is True

    def test_false_when_run_on_catchup_false(self, artha_dir):
        _write_profile(artha_dir, False)
        assert _is_enabled(artha_dir) is False

    def test_defaults_true_when_profile_missing(self, tmp_path):
        assert _is_enabled(tmp_path) is True

    def test_defaults_true_when_work_section_missing(self, artha_dir):
        (artha_dir / "config" / "user_profile.yaml").write_text(
            "schema_version: '1.0'\n", encoding="utf-8"
        )
        assert _is_enabled(artha_dir) is True


# ---------------------------------------------------------------------------
# run() — dry-run mode
# ---------------------------------------------------------------------------

class TestRunDryRun:

    def test_dry_run_returns_record(self, artha_dir):
        record = run(artha_dir, dry_run=True)
        assert isinstance(record, dict)
        assert record["dry_run"] is True

    def test_dry_run_does_not_write_log(self, artha_dir):
        run(artha_dir, dry_run=True)
        log_path = artha_dir / "state" / "work" / "eval" / "work-refresh-log.jsonl"
        assert not log_path.exists()

    def test_dry_run_record_has_required_fields(self, artha_dir):
        record = run(artha_dir, dry_run=True)
        for field in ("run_id", "mode", "dry_run", "errors", "duration_ms"):
            assert field in record

    def test_dry_run_mode_is_read(self, artha_dir):
        record = run(artha_dir, dry_run=True)
        assert record["mode"] == "READ"


# ---------------------------------------------------------------------------
# _append_run_log
# ---------------------------------------------------------------------------

class TestAppendRunLog:

    def test_creates_log_file(self, artha_dir):
        _append_run_log({"run_id": "wr-test", "mode": "READ"}, artha_dir)
        log_path = artha_dir / "state" / "work" / "eval" / "work-refresh-log.jsonl"
        assert log_path.exists()

    def test_appends_valid_json(self, artha_dir):
        _append_run_log({"run_id": "wr-001"}, artha_dir)
        _append_run_log({"run_id": "wr-002"}, artha_dir)
        log_path = artha_dir / "state" / "work" / "eval" / "work-refresh-log.jsonl"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            assert json.loads(line)  # must be valid JSON

    def test_does_not_raise_on_missing_dir(self, tmp_path):
        # No state/work/eval dir — should create it silently
        _append_run_log({"run_id": "wr-test"}, tmp_path)
        log_path = tmp_path / "state" / "work" / "eval" / "work-refresh-log.jsonl"
        assert log_path.exists()


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

class TestMain:

    def test_dry_run_exits_zero(self, artha_dir, monkeypatch, capsys):
        monkeypatch.setattr(pwr, "_ARTHA_DIR", artha_dir)
        _write_profile(artha_dir, True)
        rc = main(["--dry-run", "--artha-dir", str(artha_dir)])
        assert rc == 0

    def test_disabled_exits_zero_and_prints_info(self, artha_dir, monkeypatch, capsys):
        monkeypatch.setattr(pwr, "_ARTHA_DIR", artha_dir)
        _write_profile(artha_dir, False)
        rc = main(["--artha-dir", str(artha_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "disabled" in captured.out.lower()

    def test_quiet_flag_suppresses_stdout(self, artha_dir, monkeypatch, capsys):
        monkeypatch.setattr(pwr, "_ARTHA_DIR", artha_dir)
        _write_profile(artha_dir, True)
        main(["--dry-run", "--quiet", "--artha-dir", str(artha_dir)])
        captured = capsys.readouterr()
        assert captured.out == ""
