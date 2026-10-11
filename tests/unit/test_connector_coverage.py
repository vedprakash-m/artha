"""WP-02: per-run connector coverage (re-artha §6.7, §6.13, RA-16)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import connector_coverage as cc  # noqa: E402


def _run(records=3, rejected=0, error=None, scopes=(), reasons=()):
    return cc.ConnectorRun(
        connector="google_calendar", records=records, rejected=rejected,
        rejection_reasons=list(reasons), error=error, scopes=list(scopes), elapsed=1.0,
    )


# ── status table (§6.13) ───────────────────────────────────────────────────

def test_valid_empty_result_is_complete_and_allows_scoped_quiet_claim():
    run = _run(records=0)
    assert run.status() == "complete"
    assert run.quiet_claim_allowed() is True


def test_rejected_record_degrades_scope_regardless_of_count():
    run = _run(records=10, rejected=1, reasons=["start missing"])
    assert run.status() == "degraded"
    assert run.quiet_claim_allowed() is False


def test_all_records_rejected_is_degraded_not_empty():
    run = _run(records=0, rejected=40, reasons=["missing 'date_iso'"])
    assert run.status() == "degraded"
    assert "40" in run.describe()


def test_fetch_error_is_unavailable_and_blocks_quiet_claim():
    run = _run(records=0, error="invalid_grant")
    assert run.status() == "unavailable"
    assert run.quiet_claim_allowed() is False


def test_failed_scope_degrades_connector_even_when_others_succeed():
    scopes = [cc.ScopeResult("calendar:primary", "complete", records=4),
              cc.ScopeResult("calendar:family", "failed", reason="403 forbidden")]
    run = _run(records=4, scopes=scopes)
    assert run.status() == "degraded"
    assert "calendar:family" in run.describe()


def test_truncated_scope_is_degraded():
    scopes = [cc.ScopeResult("calendar:primary", "truncated", records=250, reason="max_results reached")]
    assert _run(records=250, scopes=scopes).status() == "degraded"


# ── volume anomaly ──────────────────────────────────────────────────────────

def test_zero_records_when_history_usually_returns_data_is_anomaly():
    history = [{"records": 12, "status": "complete"}] * 4 + [{"records": 0, "status": "complete"}]
    run = _run(records=0)
    assert cc.volume_anomaly(run, history) is True
    assert run.with_history(history).status() == "anomaly"
    assert run.with_history(history).quiet_claim_allowed() is False


def test_zero_records_when_history_also_empty_is_not_anomaly():
    history = [{"records": 0, "status": "complete"}] * 5
    assert cc.volume_anomaly(_run(records=0), history) is False


def test_anomaly_needs_three_of_last_five_nonzero():
    history = [{"records": 5, "status": "complete"}] * 2 + [{"records": 0, "status": "complete"}] * 3
    assert cc.volume_anomaly(_run(records=0), history) is False


def test_nonzero_run_is_never_anomaly():
    history = [{"records": 12, "status": "complete"}] * 5
    assert cc.volume_anomaly(_run(records=1), history) is False


# ── collector / context ─────────────────────────────────────────────────────

def test_report_scope_without_collector_is_noop():
    cc.report_scope("calendar:x", "failed", reason="boom")  # must not raise


def test_collector_captures_scopes_reported_by_handler():
    coll = cc.CoverageCollector()
    with coll.track("google_calendar") as tracker:
        cc.report_scope("calendar:primary", "complete", records=3)
        cc.report_scope("calendar:family", "failed", reason="403")
    assert [s.scope for s in tracker.scopes] == ["calendar:primary", "calendar:family"]


def test_collector_nested_connectors_do_not_leak_scopes():
    coll = cc.CoverageCollector()
    with coll.track("a") as ta:
        cc.report_scope("s1", "complete")
    with coll.track("b") as tb:
        cc.report_scope("s2", "failed", reason="x")
    assert [s.scope for s in ta.scopes] == ["s1"]
    assert [s.scope for s in tb.scopes] == ["s2"]


# ── persistence ─────────────────────────────────────────────────────────────

def test_store_roundtrip_and_history_cap(tmp_path):
    store = cc.CoverageStore(tmp_path / "coverage.json")
    for i in range(14):
        store.record(_run(records=i + 1), at=f"2026-10-{i + 1:02d}T00:00:00Z")
    data = json.loads((tmp_path / "coverage.json").read_text())
    hist = data["connectors"]["google_calendar"]["history"]
    assert len(hist) == cc.HISTORY_LIMIT
    assert hist[-1]["records"] == 14
    assert store.history("google_calendar")[-1]["records"] == 14


def test_store_last_success_only_advances_on_complete(tmp_path):
    store = cc.CoverageStore(tmp_path / "c.json")
    store.record(_run(records=2), at="2026-10-01T00:00:00Z")
    store.record(_run(records=0, error="timeout"), at="2026-10-05T00:00:00Z")
    entry = store.load()["connectors"]["google_calendar"]
    assert entry["last_success_at"] == "2026-10-01T00:00:00Z"
    assert entry["last_run"]["status"] == "unavailable"
    assert entry["failing_since"] == "2026-10-05T00:00:00Z"


def test_store_failing_since_clears_after_success(tmp_path):
    store = cc.CoverageStore(tmp_path / "c.json")
    store.record(_run(records=0, error="x"), at="2026-10-02T00:00:00Z")
    store.record(_run(records=3), at="2026-10-03T00:00:00Z")
    assert "failing_since" not in store.load()["connectors"]["google_calendar"]


def test_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    store = cc.CoverageStore(path)
    assert store.load() == {"connectors": {}}
    store.record(_run(records=1), at="2026-10-01T00:00:00Z")
    assert json.loads(path.read_text())["connectors"]["google_calendar"]["last_run"]["records"] == 1


# ── rendering ───────────────────────────────────────────────────────────────

def test_coverage_line_all_complete():
    runs = [_run(records=3), cc.ConnectorRun("gmail", 40, 0, [], None, [], 2.0)]
    line = cc.coverage_line(runs)
    assert line.startswith("Coverage:")
    assert "all required sources checked" in line


def test_coverage_line_names_gaps_and_withholds_quiet_claim():
    runs = [_run(records=0, rejected=7, reasons=["start missing"]),
            cc.ConnectorRun("gmail", 40, 0, [], None, [], 2.0)]
    line = cc.coverage_line(runs)
    assert "google_calendar" in line and "degraded" in line
    assert "no all-clear" in line


def test_coverage_line_chronic_failure_age():
    last = {"google_calendar": {"failing_since": "2026-09-01T00:00:00Z"}}
    runs = [_run(records=0, error="auth")]
    line = cc.coverage_line(runs, entries=last, now="2026-10-11T00:00:00Z")
    assert "40 days" in line


# ── persisted view (Briefing line comes from what the last fetch recorded) ──

def test_line_from_store_reports_age_and_gaps(tmp_path):
    store = cc.CoverageStore(tmp_path / "c.json")
    store.record(cc.ConnectorRun("google_calendar", 0, 6, ["start missing"], None, [], 1.0),
                 at="2026-10-11T08:00:00Z")
    store.record(cc.ConnectorRun("gmail", 40, 0, [], None, [], 1.0), at="2026-10-11T08:00:00Z")
    line = cc.line_from_store(store, now="2026-10-11T20:00:00Z")
    assert "12h ago" in line
    assert "google_calendar degraded" in line and "no all-clear" in line


def test_line_from_store_clean(tmp_path):
    store = cc.CoverageStore(tmp_path / "c.json")
    store.record(cc.ConnectorRun("gmail", 40, 0, [], None, [], 1.0), at="2026-10-11T19:30:00Z")
    line = cc.line_from_store(store, now="2026-10-11T20:00:00Z")
    assert line.startswith("Coverage")
    assert "all required sources checked" in line


def test_line_from_store_without_any_fetch_never_claims_coverage(tmp_path):
    line = cc.line_from_store(cc.CoverageStore(tmp_path / "none.json"), now="2026-10-11T20:00:00Z")
    assert "no fetch recorded" in line
    assert "all required sources checked" not in line


def test_ensure_coverage_line_inserted_once():
    from lib.briefing_archive import ensure_coverage_line
    text = "ARTHA · Sunday\n\nNothing urgent.\n"
    out = ensure_coverage_line(text, "Coverage: all required sources checked.")
    assert out.splitlines()[0].startswith("Coverage:")
    assert ensure_coverage_line(out, "Coverage: other.") == out


def test_ensure_coverage_line_keeps_frontmatter_first():
    from lib.briefing_archive import ensure_coverage_line
    text = "---\ndate: 2026-10-11\n---\n\nBody\n"
    out = ensure_coverage_line(text, "Coverage: x.")
    assert out == "---\ndate: 2026-10-11\n---\nCoverage: x.\n\nBody\n"
