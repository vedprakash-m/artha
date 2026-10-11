"""WP-02 / WP02.AC3: quality gates verify artifacts; they cannot pass unconditionally."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pipeline  # noqa: E402
from lib import connector_coverage as cc  # noqa: E402


def _run(name="google_calendar", records=3, rejected=0, error=None, scopes=()):
    return cc.ConnectorRun(name, records, rejected, [], error, list(scopes), 1.0)


def _names(checks):
    return [c.__name__ for c in checks]


def _failed(checks):
    return [c.__name__ for c in checks if not c()]


def test_all_complete_passes_every_post_fetch_check():
    checks = pipeline._post_fetch_checks([_run(), _run("gmail", 40)], {}, error_count=0)
    assert _failed(checks) == []


def test_valid_empty_result_passes():
    checks = pipeline._post_fetch_checks([_run(records=0)], {}, error_count=0)
    assert _failed(checks) == []


def test_rejected_records_fail_the_gate_by_name():
    checks = pipeline._post_fetch_checks([_run(records=0, rejected=9)], {}, error_count=0)
    assert "no_degraded_or_unavailable_scope" in _failed(checks)


def test_failed_auth_fails_the_gate():
    checks = pipeline._post_fetch_checks([_run(records=0, error="invalid_grant")], {}, error_count=1)
    failed = _failed(checks)
    assert "no_degraded_or_unavailable_scope" in failed
    assert "at_least_one_connector_succeeded" in failed


def test_chronic_failure_fails_staleness_check():
    entries = {"google_calendar": {"failing_since": "2026-08-30T00:00:00Z"}}
    checks = pipeline._post_fetch_checks([_run(records=0, error="x")], entries, error_count=1,
                                         now="2026-10-11T00:00:00Z")
    assert "no_chronic_connector_failure" in _failed(checks)


def test_recent_failure_is_not_chronic():
    entries = {"google_calendar": {"failing_since": "2026-10-10T00:00:00Z"}}
    checks = pipeline._post_fetch_checks([_run(records=0, error="x")], entries, error_count=1,
                                         now="2026-10-11T00:00:00Z")
    assert "no_chronic_connector_failure" not in _failed(checks)


def test_post_process_fails_on_rejections(tmp_path):
    checks = pipeline._post_process_checks([_run(rejected=2)], total_records=5, output_path=None)
    assert "no_rejected_records" in _failed(checks)


def test_post_process_requires_snapshot_when_records_exist(tmp_path):
    missing = tmp_path / "out.jsonl"
    checks = pipeline._post_process_checks([_run()], total_records=5, output_path=missing)
    assert "output_snapshot_written" in _failed(checks)
    missing.write_text('{"id": "1"}\n')
    checks = pipeline._post_process_checks([_run()], total_records=5, output_path=missing)
    assert _failed(checks) == []


def test_post_process_no_records_needs_no_snapshot(tmp_path):
    checks = pipeline._post_process_checks([_run(records=0)], total_records=0, output_path=tmp_path / "x")
    assert _failed(checks) == []


def test_gate_message_lists_failing_checks():
    from lib.quality_gate import evaluate_gate
    checks = pipeline._post_fetch_checks([_run(records=0, rejected=3)], {}, error_count=0)
    result = evaluate_gate("post_fetch", checks, max_retries=0)
    msg = pipeline._gate_failure_message("post_fetch", result)
    assert "no_degraded_or_unavailable_scope" in msg
    assert "connectors failed" not in msg
