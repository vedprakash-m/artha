"""WP-02: Google Calendar handler reports scope outcomes and fetches the upcoming window."""
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
import connectors.google_calendar as gcal  # noqa: E402


def _evt(i, day="2026-10-14"):
    return {"id": f"e{i}", "status": "confirmed", "summary": f"Event {i}",
            "start": {"dateTime": f"{day}T10:00:00-07:00"}, "end": {"dateTime": f"{day}T11:00:00-07:00"}}


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _FakeService:
    """calendars().get / events().list with scripted per-calendar behaviour."""

    def __init__(self, pages_by_cal, failing=(), recorder=None):
        self.pages_by_cal = pages_by_cal
        self.failing = set(failing)
        self.recorder = recorder if recorder is not None else []

    def calendars(self):
        outer = self

        class _C:
            def get(self, calendarId):
                return _Exec(lambda: {"summary": f"name-{calendarId}"})
        return _C()

    def events(self):
        outer = self

        class _E:
            def list(self, calendarId, pageToken=None, **kw):
                outer.recorder.append({"calendarId": calendarId, "pageToken": pageToken, **kw})

                def run():
                    if calendarId in outer.failing:
                        raise RuntimeError("403 forbidden")
                    pages = outer.pages_by_cal[calendarId]
                    idx = 0 if pageToken is None else int(pageToken)
                    page = {"items": pages[idx]}
                    if idx + 1 < len(pages):
                        page["nextPageToken"] = str(idx + 1)
                    return page
                return _Exec(run)
        return _E()


@pytest.fixture
def patched(monkeypatch):
    def install(service):
        mod = types.ModuleType("google_auth")
        mod.build_service = lambda *a, **k: service
        monkeypatch.setitem(sys.modules, "google_auth", mod)
        import lib.retry as retry  # noqa: PLC0415
        monkeypatch.setattr(retry, "with_retry", lambda fn, **kw: fn())
    return install


def _fetch(**kw):
    coll = cc.CoverageCollector()
    with coll.track("google_calendar") as tr:
        recs = list(gcal.fetch(since=kw.pop("since", "2026-10-10T00:00:00Z"), auth_context={}, **kw))
    return recs, tr


def test_failed_calendar_is_reported_and_others_still_yield(patched):
    svc = _FakeService({"primary": [[_evt(1)]], "family": [[_evt(2)]]}, failing={"family"})
    patched(svc)
    recs, tr = _fetch(calendar_ids=["primary", "family"])
    assert [r["id"] for r in recs] == ["e1"]
    by_scope = {s.scope: s for s in tr.scopes}
    assert by_scope["calendar:primary"].status == "complete"
    assert by_scope["calendar:family"].status == "failed"
    assert "403" in by_scope["calendar:family"].reason


def test_all_pages_are_followed(patched):
    svc = _FakeService({"primary": [[_evt(1), _evt(2)], [_evt(3)], [_evt(4)]]})
    patched(svc)
    recs, tr = _fetch(calendar_ids=["primary"])
    assert sorted(r["id"] for r in recs) == ["e1", "e2", "e3", "e4"]
    assert tr.scopes[0].status == "complete" and tr.scopes[0].records == 4


def test_page_cap_marks_scope_truncated(patched, monkeypatch):
    monkeypatch.setattr(gcal, "MAX_PAGES", 2)
    svc = _FakeService({"primary": [[_evt(1)], [_evt(2)], [_evt(3)]]})
    patched(svc)
    recs, tr = _fetch(calendar_ids=["primary"])
    assert len(recs) == 2
    assert tr.scopes[0].status == "truncated"


def test_long_gap_still_covers_the_upcoming_window(patched):
    rec = []
    svc = _FakeService({"primary": [[]]}, recorder=rec)
    patched(svc)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _fetch(since=long_ago, calendar_ids=["primary"], window_days=14)
    time_max = datetime.fromisoformat(rec[0]["timeMax"])
    assert time_max > datetime.now(timezone.utc) + timedelta(days=10), "window must reach into the future"


def test_empty_calendar_is_a_complete_scope(patched):
    svc = _FakeService({"primary": [[]]})
    patched(svc)
    recs, tr = _fetch(calendar_ids=["primary"])
    assert recs == [] and tr.scopes[0].status == "complete" and tr.scopes[0].records == 0
