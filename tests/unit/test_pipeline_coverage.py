"""WP-02: the pipeline turns validator rejections into degraded coverage (WP02.AC1-AC4)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pipeline  # noqa: E402
from lib import connector_coverage as cc  # noqa: E402


class _Handler:
    """Minimal connector handler: yields the configured records, or raises."""

    def __init__(self, records: list[dict[str, Any]], raises: Exception | None = None,
                 scopes: list[tuple] | None = None):
        self._records = records
        self._raises = raises
        self._scopes = scopes or []

    def fetch(self, **_kw: Any) -> Iterator[dict[str, Any]]:
        for scope, status, kw in self._scopes:
            cc.report_scope(scope, status, **kw)
        for rec in self._records:
            yield rec
        if self._raises:
            raise self._raises


def _cal(i: int, **over: Any) -> dict[str, Any]:
    rec = {"id": f"evt{i}", "calendar": "Family", "summary": "Soccer", "source": "google_calendar",
           "start": "2026-10-14T15:30:00-07:00", "end": "2026-10-14T16:30:00-07:00", "all_day": False}
    rec.update(over)
    return rec


def _run(handler, name="google_calendar"):
    coll = cc.CoverageCollector()
    out = pipeline._fetch_one(name, handler, {}, {}, {"max_attempts": 1}, since="2026-10-10T00:00:00Z",
                              coverage=coll)
    return out, coll


def test_native_calendar_records_pass_and_gain_derived_date_iso():
    (name, lines, _el, err), coll = _run(_Handler([_cal(1), _cal(2)]))
    assert err is None and len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["date_iso"] == rec["start"]            # derived for consumers (D-21)
    run = coll.runs[0]
    assert run.status() == "complete" and run.records == 2 and run.rejected == 0


def test_rejected_record_degrades_scope_and_is_not_emitted():
    bad = _cal(3)
    bad.pop("start")
    (_n, lines, _e, err), coll = _run(_Handler([_cal(1), bad]))
    assert err is None
    assert len(lines) == 1
    run = coll.runs[0]
    assert run.rejected == 1 and run.status() == "degraded"
    assert run.quiet_claim_allowed() is False
    assert any("start" in r for r in run.rejection_reasons)


def test_every_record_rejected_is_degraded_never_complete_empty():
    """The live defect: all calendar records dropped yet the run looked fine."""
    old_shape = [{"id": "e1", "source": "google_calendar"}, {"id": "e2", "source": "google_calendar"}]
    (_n, lines, _e, err), coll = _run(_Handler(old_shape))
    assert lines == []
    assert coll.runs[0].status() == "degraded"
    assert coll.runs[0].rejected == 2


def test_valid_empty_result_is_complete():
    (_n, lines, _e, err), coll = _run(_Handler([]))
    assert lines == [] and err is None
    assert coll.runs[0].status() == "complete"
    assert coll.runs[0].quiet_claim_allowed() is True


def test_fetch_exception_is_unavailable():
    (_n, _lines, _e, err), coll = _run(_Handler([], raises=RuntimeError("invalid_grant")))
    assert err and "invalid_grant" in err
    assert coll.runs[0].status() == "unavailable"


def test_handler_reported_failed_scope_degrades():
    handler = _Handler([_cal(1)], scopes=[
        ("calendar:primary", "complete", {"records": 1}),
        ("calendar:family", "failed", {"reason": "403"}),
    ])
    _out, coll = _run(handler)
    run = coll.runs[0]
    assert run.status() == "degraded"
    assert "calendar:family" in run.describe()


def test_without_collector_behaviour_is_unchanged():
    name, lines, _el, err = pipeline._fetch_one(
        "google_calendar", _Handler([_cal(1)]), {}, {}, {"max_attempts": 1}, since="2026-10-10T00:00:00Z")
    assert err is None and len(lines) == 1


def test_record_runs_persists_history_and_returns_line(tmp_path):
    coll = cc.CoverageCollector()
    coll.add(cc.ConnectorRun("google_calendar", 0, 5, ["start missing"], None, [], 1.0))
    coll.add(cc.ConnectorRun("gmail", 40, 0, [], None, [], 2.0))
    store = cc.CoverageStore(tmp_path / "c.json")
    line = pipeline._record_coverage(coll, store, at="2026-10-11T00:00:00Z")
    assert "google_calendar" in line and "no all-clear" in line
    assert store.load()["connectors"]["gmail"]["last_success_at"] == "2026-10-11T00:00:00Z"


# ── --exclude-source (re-artha §6.14 prerequisite) ─────────────────────────

def _cfg():
    return {"connectors": {
        "gmail": {"enabled": True, "type": "email", "fetch": {"handler": "x"}},
        "google_calendar": {"enabled": True, "type": "calendar", "fetch": {"handler": "x"}},
        "rss": {"enabled": True, "type": "feed", "fetch": {"handler": "x"}},
    }}


def test_exclude_source_removes_named_connectors_after_include_filter():
    names = lambda c: sorted(x["name"] for x in c)    # noqa: E731
    assert names(pipeline._enabled_connectors(_cfg(), None)) == ["gmail", "google_calendar", "rss"]
    assert names(pipeline._enabled_connectors(_cfg(), None, ["google_calendar"])) == ["gmail", "rss"]
    assert names(pipeline._enabled_connectors(_cfg(), ["gmail", "rss"], ["rss"])) == ["gmail"]
    assert names(pipeline._enabled_connectors(_cfg(), None, [])) == ["gmail", "google_calendar", "rss"]


def test_exclude_source_cli_flag_is_parsed(monkeypatch):
    seen = {}

    def fake_run(cfg, **kw):
        seen.update(kw)
        return 0
    monkeypatch.setattr(pipeline, "run_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "load_connectors_config", lambda: _cfg())
    rc = pipeline.main(["--exclude-source", "google_calendar", "--exclude-source", "rss", "--since", "2026-10-01T00:00:00Z"])
    assert rc == 0 and seen["exclude_sources"] == ["google_calendar", "rss"]
