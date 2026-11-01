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
    assert by_scope["calendar:family"].reason == 'provider request failed'


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


@pytest.mark.parametrize('malformed', [None, 'bad page', {}, [None, _evt(2)]])
def test_malformed_google_page_preserves_other_valid_calendar_data(patched, malformed):
    patched(_FakeService({'primary': [[_evt(1)], malformed], 'other': [[_evt(3)]]}))
    recs, tr = _fetch(calendar_ids=['primary', 'other'])
    assert {'e1', 'e3'} <= {r['id'] for r in recs}
    assert {s.scope: s.status for s in tr.scopes} == {'calendar:primary': 'failed', 'calendar:other': 'complete'}
    if isinstance(malformed, list):
        assert 'e2' in {r['id'] for r in recs}


@pytest.mark.parametrize('limit', [0, -1, True, 2.5])
def test_google_invalid_result_limit_is_rejected(patched, limit):
    patched(_FakeService({'primary': [[]]}))
    with pytest.raises(ValueError, match='max_results'):
        _fetch(calendar_ids=['primary'], max_results=limit)


@pytest.mark.parametrize('calendars', [[], '', {}, [''], [1], ['a', 'a']])
def test_google_invalid_explicit_scope_is_rejected(patched, calendars):
    patched(_FakeService({}))
    with pytest.raises(ValueError, match='calendar_ids'):
        _fetch(calendar_ids=calendars)


@pytest.mark.parametrize('days', [0, -1, True, 1.5])
def test_google_invalid_future_window_is_rejected(patched, days):
    patched(_FakeService({'primary': [[]]}))
    with pytest.raises(ValueError, match='window_days'):
        _fetch(calendar_ids=['primary'], window_days=days)


def test_actual_handler_cancellation_withdraws_only_its_calendar(patched, tmp_path, monkeypatch):
    from catchup_core import adapters, crypto, ids, ledger as lg
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    patched(_FakeService({'a': [[_evt(1)]], 'b': [[_evt(1)]]}))
    got = adapters.fetch_via_handler('calendar', 'personal', gcal.fetch, since='2026-10-10T00:00:00Z',
                                    fetch_kwargs={'calendar_ids': ['a', 'b']})
    assert len({r.upstream_id for r in got.records}) == 2
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b'7' * 32}))
    with lg.Ledger(tmp_path / 'ledger.db', id_key=ids.HmacKey(1, b'i' * 32), cipher=cipher).open() as led:
        led.migrate()
        facts = []
        for index, record in enumerate(got.records):
            sid, _ = led.upsert_signal(lg.SignalInput('calendar', 'personal', record.upstream_id, 'v1',
                                       '2026-10-12T00:00:00Z', f'h{index}', f'blob{index}'))
            facts.append(led.put_fact(lg.FactInput('kids_school', 'test_date', '2026-10-14', 'kid1',
                '2026-10-12T00:00:00Z', evidence=((sid, 0, 4),), obligation_key=f'obligation{index}')))
        patched(_FakeService({'a': [[{'id': 'e1', 'status': 'cancelled'}]], 'b': [[_evt(1)]]}))
        cancelled = adapters.fetch_via_handler('calendar', 'personal', gcal.fetch, since='2026-10-10T00:00:00Z',
                                              fetch_kwargs={'calendar_ids': ['a', 'b']})
        assert cancelled.cancelled == (got.records[0].upstream_id,)
        for upstream in cancelled.cancelled:
            led.cancel_calendar('calendar', 'personal', upstream)
        assert led.get_fact(facts[0], 'owner').status == 'unknown'
        assert led.get_fact(facts[1], 'owner').status == 'current'


class _SyncService(_FakeService):
    def calendarList(self):  # noqa: N802 - provider method name; no calendar is listed as deleted
        return types.SimpleNamespace(list=lambda **kwargs: _Exec(lambda: {'items': []}))

    def events(self):
        outer = self

        class _Events:
            def list(self, calendarId, pageToken=None, **kwargs):
                outer.recorder.append({'calendarId': calendarId, 'pageToken': pageToken, **kwargs})

                def execute():
                    page = outer.pages_by_cal[calendarId][int(pageToken or 0)]
                    if isinstance(page, Exception):
                        raise page
                    return page

                return _Exec(execute)

        return _Events()


def test_bounded_google_query_never_publishes_a_sync_cursor(patched):
    patched(_SyncService({'a': [{'items': [], 'nextSyncToken': 'not-a-window-cursor'}]}))
    _, tracker = _fetch(calendar_ids=['a'])
    assert tracker.cursor is None


def test_google_change_feed_preserves_per_calendar_tokens_and_query_contract(patched, tmp_path, monkeypatch):
    import json
    from catchup_core.adapters import fetch_via_handler

    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    service = _SyncService({'a': [{'items': [_evt(1)], 'nextSyncToken': ' next-a '}],
                            'b': [{'items': [{'id': 'e2', 'status': 'cancelled'}], 'nextSyncToken': 'next-b'}]})
    patched(service)
    result = fetch_via_handler('calendar', 'personal', gcal.fetch, since='2026-10-10T00:00:00Z',
        fetch_kwargs={'calendar_ids': ['a', 'b'], 'sync_changes': True,
                      'sync_tokens': {'a': ' old-a ', 'b': 'old-b'}})
    assert result.status.value == 'complete'
    assert json.loads(result.cursor) == {'version': 1, 'collections': {'a': ' next-a ', 'b': 'next-b'}}
    assert result.cancelled == ('calendar:["b","e2"]',)
    assert [call['syncToken'] for call in service.recorder] == [' old-a ', 'old-b']
    assert all(not {'timeMin', 'timeMax', 'orderBy'} & call.keys() for call in service.recorder)


@pytest.mark.parametrize('last_page', [
    {'items': [None], 'nextSyncToken': 'bad'},
    {'items': [], 'nextPageToken': '1'},
    RuntimeError('secret-native-error'),
])
def test_google_incomplete_change_feed_keeps_partial_evidence_without_cursor(patched, last_page, capsys):
    service = _SyncService({'a': [{'items': [_evt(1), {'id': 'gone', 'status': 'cancelled'}],
                                  'nextPageToken': '1'}, last_page]})
    patched(service)
    records, tracker = _fetch(calendar_ids=['a'], sync_changes=True)
    assert {record['id'] for record in records} == {'e1', 'gone'}
    assert tracker.cursor is None
    assert any(scope.status in {'failed', 'truncated'} for scope in tracker.scopes)
    assert 'secret-native-error' not in capsys.readouterr().err
    assert 'secret-native-error' not in str(tracker.scopes)


def test_google_410_is_explicit_reset_not_deletion(patched):
    error = RuntimeError('private provider response')
    error.resp = types.SimpleNamespace(status=410)
    patched(_SyncService({'a': [error]}))
    records, tracker = _fetch(calendar_ids=['a'], sync_changes=True, sync_tokens={'a': 'expired'})
    assert not records and tracker.cursor_reset and not tracker.deleted_collections


@pytest.mark.parametrize('kwargs', [
    {'sync_changes': 'true'},
    {'sync_changes': True, 'sync_tokens': {'other': 'token'}},
    {'sync_changes': True, 'sync_tokens': {'a': ''}},
    {'sync_tokens': {'a': 'token'}},
])
def test_google_invalid_sync_scope_is_rejected_before_provider(patched, kwargs):
    patched(_SyncService({}))
    with pytest.raises(ValueError, match='sync'):
        _fetch(calendar_ids=['a'], **kwargs)


def test_google_404_cannot_prove_collection_deletion(patched):
    error = RuntimeError('secret-native-error')
    error.resp = types.SimpleNamespace(status=404)
    patched(_SyncService({'a': [error]}))
    _, tracker = _fetch(calendar_ids=['a'], sync_changes=True)
    assert not tracker.deleted_collections and tracker.cursor is None
    assert tracker.scopes[0].status == 'failed'


def test_google_validator_rejection_cannot_publish_completed_cursor(patched, tmp_path, monkeypatch):
    from catchup_core.adapters import fetch_via_handler

    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    bad = _evt(2)
    bad['start']['dateTime'] = 'not-an-instant'
    patched(_SyncService({'a': [{'items': [_evt(1), bad], 'nextSyncToken': 'uncommittable'}]}))
    result = fetch_via_handler('calendar', 'personal', gcal.fetch, since='2026-10-10T00:00:00Z',
        fetch_kwargs={'calendar_ids': ['a'], 'sync_changes': True})
    assert result.status.value == 'degraded' and len(result.records) == 1
    assert result.cursor is None


def test_google_later_calendar_failure_invalidates_earlier_calendar_cursor(patched):
    patched(_SyncService({'a': [{'items': [_evt(1)], 'nextSyncToken': 'next-a'}],
                         'b': [RuntimeError('secret-native-error')]}))
    records, tracker = _fetch(calendar_ids=['a', 'b'], sync_changes=True)
    assert len(records) == 1 and tracker.cursor is None


def test_google_initial_change_feed_requires_terminal_cursor(patched):
    patched(_SyncService({'a': [{'items': [_evt(1)]}]}))
    records, tracker = _fetch(calendar_ids=['a'], sync_changes=True)
    assert len(records) == 1 and tracker.cursor is None
    assert tracker.scopes[0].status == 'failed'
