"""WP-02: preflight separates capability (can I run?) from coverage (what did the last fetch deliver?)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from preflight.coverage_checks import check_connector_coverage  # noqa: E402


def _write(tmp_path, connectors):
    path = tmp_path / "cov.json"
    path.write_text(json.dumps({"connectors": connectors}))
    return path


def _entry(status, records=0, **extra):
    return {"last_run": {"status": status, "records": records, "rejected": extra.pop("rejected", 0),
                         "error": extra.pop("error", None), "at": "2026-10-11T00:00:00Z"}, **extra}


def test_no_run_recorded_is_not_a_failure(tmp_path):
    res = check_connector_coverage(tmp_path / "missing.json")
    assert res.passed and res.severity == "P1"
    assert "no fetch recorded" in res.message


def test_valid_empty_calendar_passes(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("complete", 0), "gmail": _entry("complete", 40)})
    res = check_connector_coverage(path)
    assert res.passed, res.message


def test_rejected_records_fail_the_check(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("degraded", 0, rejected=12)})
    res = check_connector_coverage(path)
    assert not res.passed
    assert "google_calendar" in res.message and "degraded" in res.message
    assert res.severity == "P1", "coverage degrades a scope; it never blocks globally"


def test_failed_auth_fails_the_check(tmp_path):
    path = _write(tmp_path, {"outlook_calendar": _entry("unavailable", 0, error="invalid_grant")})
    res = check_connector_coverage(path)
    assert not res.passed and "outlook_calendar" in res.message


def test_volume_anomaly_fails_the_check(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("anomaly", 0)})
    res = check_connector_coverage(path)
    assert not res.passed and "usually returns" in res.message


def test_chronic_failure_age_is_reported(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("unavailable", 0, error="x",
                                                       failing_since="2026-08-30T00:00:00Z")})
    res = check_connector_coverage(path, now="2026-10-11T00:00:00Z")
    assert "42 days" in res.message


# ── plaintext fetched content in the synced workspace (P1) ──────────────────

from preflight.coverage_checks import check_plaintext_in_sync_root  # noqa: E402


def test_clean_workspace_passes(tmp_path):
    (tmp_path / "tmp").mkdir()
    assert check_plaintext_in_sync_root(tmp_path).passed


def test_leftover_pipeline_output_is_flagged(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "pipeline_output.jsonl").write_text('{"body": "x"}\n')
    res = check_plaintext_in_sync_root(tmp_path)
    assert not res.passed and res.severity == "P1"
    assert "pipeline_output.jsonl" in res.message
    assert "~/.artha-local" in res.fix_hint


def test_sync_conflict_copy_of_vault_marker_is_flagged(tmp_path):
    (tmp_path / "-Some Host.artha-decrypted").write_text("{}")
    assert not check_plaintext_in_sync_root(tmp_path).passed
