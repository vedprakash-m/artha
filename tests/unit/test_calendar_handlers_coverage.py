"""WP-02: msgraph and caldav handlers report scope outcomes; failure is never 'empty'."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import connector_coverage as cc  # noqa: E402
import connectors.caldav_calendar as caldav  # noqa: E402
import connectors.msgraph_calendar as graph  # noqa: E402

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:u{n}@example.invalid\r\n"
    "DTSTAMP:20261001T000000Z\r\nDTSTART:20261014T230000Z\r\nDTEND:20261015T000000Z\r\n"
    "SUMMARY:E{n}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def _graph_event(i):
    return {"id": f"g{i}", "subject": f"E{i}", "isAllDay": False, "isCancelled": False,
            "start": {"dateTime": "2026-10-14T16:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-10-14T17:00:00.0000000", "timeZone": "UTC"},
            "location": {}, "attendees": [], "sensitivity": "normal"}


@pytest.fixture
def graph_env(monkeypatch):
    import lib.msgraph as lm  # noqa: PLC0415
    import lib.retry as retry  # noqa: PLC0415
    monkeypatch.setattr(retry, "with_retry", lambda fn, **kw: fn())
    return lm


def _run(fetch, **kw):
    coll = cc.CoverageCollector()
    with coll.track("x") as tr:
        recs = list(fetch(since="2026-10-10T00:00:00Z", **kw))
    return recs, tr


def test_graph_failed_calendar_reported(graph_env, monkeypatch):
    def fake_get(token, url, params=None, prefer_utc=False):
        if "bad" in url:
            raise RuntimeError("401 unauthorized")
        return {"value": [_graph_event(1)]}
    monkeypatch.setattr(graph_env, "_graph_get", fake_get)
    monkeypatch.setattr(graph_env, "_graph_get_full_url", lambda t, u: {"value": []})
    recs, tr = _run(graph.fetch, auth_context={"access_token": "t"}, calendar_ids=["good", "bad"])
    assert len(recs) == 1
    status = {s.scope: s.status for s in tr.scopes}
    assert status == {"calendar:good": "complete", "calendar:bad": "failed"}


def test_graph_pagination_complete(graph_env, monkeypatch):
    calls = {"n": 0}

    def fake_get(token, url, params=None, prefer_utc=False):
        return {"value": [_graph_event(1)], "@odata.nextLink": "https://graph.example/next"}

    def fake_full(token, url):
        calls["n"] += 1
        return {"value": [_graph_event(2)]}
    monkeypatch.setattr(graph_env, "_graph_get", fake_get)
    monkeypatch.setattr(graph_env, "_graph_get_full_url", fake_full)
    recs, tr = _run(graph.fetch, auth_context={"access_token": "t"}, calendar_ids=["c"])
    assert len(recs) == 2 and tr.scopes[0].status == "complete"


def test_graph_truncation_reported(graph_env, monkeypatch):
    monkeypatch.setattr(graph_env, "_graph_get", lambda *a, **k: {
        "value": [_graph_event(1), _graph_event(2)], "@odata.nextLink": "https://graph.example/next"})
    monkeypatch.setattr(graph_env, "_graph_get_full_url", lambda t, u: {"value": []})
    recs, tr = _run(graph.fetch, auth_context={"access_token": "t"}, calendar_ids=["c"], max_results=2)
    assert len(recs) == 2 and tr.scopes[0].status == "truncated"


def test_graph_window_reaches_future_after_long_gap(graph_env, monkeypatch):
    seen = {}

    def fake_get(token, url, params=None, prefer_utc=False):
        seen.update(params or {})
        return {"value": []}
    monkeypatch.setattr(graph_env, "_graph_get", fake_get)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    coll = cc.CoverageCollector()
    with coll.track("g"):
        list(graph.fetch(since=long_ago, auth_context={"access_token": "t"}, calendar_ids=["c"]))
    end = datetime.fromisoformat(seen["endDateTime"].replace("Z", "+00:00"))
    assert end > datetime.now(timezone.utc) + timedelta(days=10)


class _FakeCal:
    def __init__(self, name, ics_list=None, raises=None):
        self.name, self._ics, self._raises = name, ics_list or [], raises
        self.url = f"https://caldav.example/{name}"

    def search(self, **kw):
        if self._raises:
            raise self._raises
        import vobject  # noqa: PLC0415
        return [types.SimpleNamespace(vobject_instance=vobject.readOne(x)) for x in self._ics]


class _FakeClient:
    def __init__(self, cals=None, principal_error=None):
        self._cals, self._err = cals or [], principal_error

    def principal(self):
        if self._err:
            raise self._err
        return types.SimpleNamespace(calendars=lambda: self._cals)


def _caldav(monkeypatch, client, **kw):
    monkeypatch.setattr(caldav, "_get_client", lambda a, p: client)
    monkeypatch.setattr(caldav, "_get_cal_name", lambda c: c.name)
    return _run(caldav.fetch, auth_context={"apple_id": "a@example.invalid", "app_password": "x"}, **kw)


def test_caldav_principal_failure_is_unavailable_not_empty(monkeypatch):
    with pytest.raises(RuntimeError, match="principal"):
        _caldav(monkeypatch, _FakeClient(principal_error=OSError("dns")))


def test_caldav_failed_calendar_reported(monkeypatch):
    cals = [_FakeCal("Home", [ICS.format(n=1)]), _FakeCal("Work", raises=RuntimeError("500"))]
    recs, tr = _caldav(monkeypatch, _FakeClient(cals))
    assert len(recs) == 1
    status = {s.scope: s.status for s in tr.scopes}
    assert status["calendar:Home"] == "complete" and status["calendar:Work"] == "failed"


def test_caldav_valid_empty_is_complete(monkeypatch):
    recs, tr = _caldav(monkeypatch, _FakeClient([_FakeCal("Home", [])]))
    assert recs == [] and tr.scopes[0].status == "complete"


def test_caldav_result_cap_reported(monkeypatch):
    cal = _FakeCal("Home", [ICS.format(n=i) for i in range(3)])
    recs, tr = _caldav(monkeypatch, _FakeClient([cal]), max_results=2)
    assert len(recs) == 2
    assert any(s.status == "truncated" for s in tr.scopes)
