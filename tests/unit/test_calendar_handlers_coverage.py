"""WP-02: msgraph and caldav handlers report scope outcomes; failure is never 'empty'."""
from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone
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


def test_graph_calendar_discovery_pages_are_part_of_source_coverage(graph_env, monkeypatch):
    def first(token, url, params=None, prefer_utc=False):
        if url == '/me/calendars':
            return {'value': [{'id': 'first', 'name': 'First'}], '@odata.nextLink': 'https://graph.example/cals2'}
        return {'value': [_graph_event(url)]}

    monkeypatch.setattr(graph_env, '_graph_get', first)
    monkeypatch.setattr(graph_env, '_graph_get_full_url', lambda *_: {'value': [{'id': 'second', 'name': 'Second'}]})
    recs, tr = _run(graph.fetch, auth_context={'access_token': 'synthetic'})
    assert {r['calendar_id'] for r in recs} == {'first', 'second'}
    assert {s.scope: s.status for s in tr.scopes} == {
        'calendars:list': 'complete', 'calendar:first': 'complete', 'calendar:second': 'complete'}


@pytest.mark.parametrize('failure', ['exception', 'loop', 'malformed'])
def test_graph_incomplete_discovery_preserves_known_calendars_without_reassurance(graph_env, monkeypatch, failure):
    monkeypatch.setattr(graph_env, '_graph_get', lambda token, url, *a, **kw: (
        {'value': [{'id': 'first', 'name': 'First'}], '@odata.nextLink': 'https://graph.example/cals2'}
        if url == '/me/calendars' else {'value': [_graph_event(1)]}))

    def next_page(*_):
        if failure == 'exception':
            raise RuntimeError('provider unavailable')
        if failure == 'loop':
            return {'value': [], '@odata.nextLink': 'https://graph.example/cals2'}
        return {'value': [{'name': 'missing identity'}]}

    monkeypatch.setattr(graph_env, '_graph_get_full_url', next_page)
    recs, tr = _run(graph.fetch, auth_context={'access_token': 'synthetic'})
    assert len(recs) == 1 and recs[0]['calendar_id'] == 'first'
    assert any(s.scope == 'calendars:list' and s.status in {'failed', 'truncated'} for s in tr.scopes)


def test_graph_cancellations_reach_the_core_as_tombstones(graph_env, monkeypatch):
    event = {**_graph_event(1), "isCancelled": True}
    monkeypatch.setattr(graph_env, "_graph_get", lambda *a, **kw: {"value": [event]})
    recs, tr = _run(graph.fetch, auth_context={"access_token": "t"}, calendar_ids=["c"])
    assert len(recs) == 1 and recs[0]["status"] == "cancelled" and tr.scopes[0].status == "complete"


@pytest.mark.parametrize('marker', [{'isCancelled': True}, {'@removed': {'reason': 'deleted'}}])
def test_graph_sparse_tombstone_keeps_collection_identity(graph_env, monkeypatch, marker, tmp_path):
    from catchup_core.adapters import fetch_via_handler

    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    monkeypatch.setattr(graph_env, '_graph_get', lambda *args, **kwargs: {
        'value': [{'id': 'gone', **marker}], '@odata.deltaLink': 'not-a-calendar-view-cursor'})
    result = fetch_via_handler('calendar', 'personal', graph.fetch, since='2026-10-10T00:00:00Z',
        auth_context={'access_token': 'synthetic'}, fetch_kwargs={'calendar_ids': ['a']})
    assert result.cancelled == ('calendar:["a","gone"]',)
    assert result.status.value == 'complete' and result.cursor is None


def test_graph_notfound_does_not_delete_collection_or_disclose_error(graph_env, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError('404 notfound secret-native-error')

    monkeypatch.setattr(graph_env, '_graph_get', fail)
    _, tracker = _run(graph.fetch, auth_context={'access_token': 'synthetic'}, calendar_ids=['a'])
    assert not tracker.deleted_collections and tracker.cursor is None
    assert 'secret-native-error' not in capsys.readouterr().err + str(tracker.scopes)


def test_caldav_provider_error_is_not_disclosed(monkeypatch, capsys):
    _, tracker = _caldav(monkeypatch, _FakeClient([
        _FakeCal('Home', raises=RuntimeError('secret-native-error'))]))
    assert tracker.scopes[0].status == 'failed'
    assert 'secret-native-error' not in capsys.readouterr().err + str(tracker.scopes)


def test_graph_truncation_reported(graph_env, monkeypatch):
    monkeypatch.setattr(graph_env, "_graph_get", lambda *a, **k: {
        "value": [_graph_event(1), _graph_event(2)], "@odata.nextLink": "https://graph.example/next"})
    monkeypatch.setattr(graph_env, "_graph_get_full_url", lambda t, u: {"value": []})
    recs, tr = _run(graph.fetch, auth_context={"access_token": "t"}, calendar_ids=["c"], max_results=2)
    assert len(recs) == 2 and tr.scopes[0].status == "truncated"


def test_graph_empty_intermediate_page_cannot_hide_later_events(graph_env, monkeypatch, tmp_path):
    from catchup_core.adapters import fetch_via_handler
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    monkeypatch.setattr(graph_env, '_graph_get', lambda *a, **kw: {
        'value': [], '@odata.nextLink': 'https://graph.example/events2'})
    monkeypatch.setattr(graph_env, '_graph_get_full_url', lambda *a: {'value': [_graph_event(2)]})
    result = fetch_via_handler('calendar', 'family', graph.fetch, since='2026-10-10T00:00:00Z',
                               auth_context={'access_token': 'synthetic'}, fetch_kwargs={'calendar_ids': ['c']})
    assert len(result.records) == 1 and result.status.value == 'complete'


def test_graph_empty_repeated_page_is_incomplete(graph_env, monkeypatch):
    page = {'value': [], '@odata.nextLink': 'https://graph.example/loop'}
    monkeypatch.setattr(graph_env, '_graph_get', lambda *a, **kw: page)
    monkeypatch.setattr(graph_env, '_graph_get_full_url', lambda *a: page)
    recs, tr = _run(graph.fetch, auth_context={'access_token': 'synthetic'}, calendar_ids=['c'])
    assert recs == [] and tr.scopes[0].status == 'truncated'


@pytest.mark.parametrize('value', [None, {}, 'not a list', [None]])
def test_graph_malformed_later_page_keeps_valid_positives(graph_env, monkeypatch, value):
    monkeypatch.setattr(graph_env, '_graph_get', lambda *a, **kw: {
        'value': [_graph_event(1)], '@odata.nextLink': 'https://graph.example/bad'})
    monkeypatch.setattr(graph_env, '_graph_get_full_url', lambda *a: {'value': value})
    recs, tr = _run(graph.fetch, auth_context={'access_token': 'synthetic'}, calendar_ids=['c'])
    assert [r['id'] for r in recs] == ['g1'] and tr.scopes[0].status == 'failed'


def test_graph_cap_within_final_page_is_truncated(graph_env, monkeypatch):
    monkeypatch.setattr(graph_env, '_graph_get', lambda *a, **kw: {'value': [_graph_event(1), _graph_event(2)]})
    recs, tr = _run(graph.fetch, auth_context={'access_token': 'synthetic'}, calendar_ids=['c'], max_results=1)
    assert len(recs) == 1 and tr.scopes[0].status == 'truncated'


def test_graph_delta_result_cap_bounds_emitted_records(graph_env, monkeypatch):
    monkeypatch.setattr(graph_env, '_graph_get', lambda *args, **kwargs: {
        'value': [_graph_event(1), _graph_event(2)],
        '@odata.deltaLink': 'https://graph.microsoft.com/delta?opaque=cursor'})
    recs, tracker = _run(graph.fetch, auth_context={'access_token': 'synthetic'},
                         calendar_ids=['c'], sync_changes=True, max_results=1)
    assert [record['id'] for record in recs] == ['g1']
    assert tracker.scopes[-1].status == 'truncated'
    assert tracker.cursor is None and not tracker.full_listings


@pytest.mark.parametrize('limit', [0, -1, True, 2.5])
def test_graph_invalid_result_limit_cannot_be_complete_empty(graph_env, limit):
    with pytest.raises(ValueError, match='max_results'):
        _run(graph.fetch, auth_context={'access_token': 'synthetic'}, calendar_ids=['c'], max_results=limit)


@pytest.mark.parametrize('calendars', [[], '', {}, [''], [1], ['a', 'a']])
def test_graph_invalid_explicit_scope_cannot_expand_to_every_calendar(graph_env, calendars):
    with pytest.raises(ValueError, match='calendar_ids'):
        _run(graph.fetch, auth_context={'access_token': 'synthetic'}, calendar_ids=calendars)


@pytest.mark.parametrize('days', [0, -1, True, 1.5])
def test_graph_invalid_future_window_is_rejected(graph_env, days):
    with pytest.raises(ValueError, match='window_days'):
        _run(graph.fetch, auth_context={'access_token': 'synthetic'}, calendar_ids=['c'], window_days=days)


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


class _FakeEvent:
    def __init__(self, ics):
        self._ics = ics

    @property
    def vobject_instance(self):
        import vobject  # noqa: PLC0415
        return vobject.readOne(self._ics)


class _FakeCal:
    def __init__(self, name, ics_list=None, raises=None):
        self.name, self._ics, self._raises = name, ics_list or [], raises
        self.url = f"https://caldav.example/{name}"

    def search(self, **kw):
        if self._raises:
            raise self._raises
        return [_FakeEvent(ics) for ics in self._ics]


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


def test_caldav_scope_survives_handler_validator_adapter_chain(monkeypatch, tmp_path):
    from catchup_core.adapters import fetch_via_handler
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    monkeypatch.setattr(caldav, '_get_client', lambda *a: _FakeClient([
        _FakeCal('School', [ICS.format(n=1)]), _FakeCal('Travel', [ICS.format(n=1)])]))
    monkeypatch.setattr(caldav, '_get_cal_name', lambda c: c.name)
    got = fetch_via_handler('calendar', 'family', caldav.fetch, since='2026-10-10',
        auth_context={'apple_id':'a@example.invalid','app_password':'x'}, calendar_kinds={
            'https://caldav.example/School':'school', 'https://caldav.example/Travel':'travel'})
    assert len({r.upstream_id for r in got.records}) == 2
    assert {r.calendar.kind for r in got.records} == {'school','travel'}


def test_caldav_cancelled_event_without_dates_is_a_scoped_tombstone(monkeypatch):
    cancelled = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:cancelled-id\r\n'
                 'STATUS:CANCELLED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
    recs, _ = _caldav(monkeypatch, _FakeClient([_FakeCal('School', [cancelled])]))
    assert recs == [{'id':'cancelled-id','status':'cancelled','calendar_id':'https://caldav.example/School',
                     'source':'icloud_calendar'}]


def test_caldav_expanded_instances_have_distinct_stable_identity(monkeypatch):
    first = ICS.format(n=1).replace('SUMMARY:', 'RECURRENCE-ID:20261014T230000Z\r\nSUMMARY:')
    second = first.replace('RECURRENCE-ID:20261014', 'RECURRENCE-ID:20261021')
    recs, _ = _caldav(monkeypatch, _FakeClient([_FakeCal('School', [first, second])]))
    assert len({r['id'] for r in recs}) == 2


def _caldav_via_adapter(monkeypatch, tmp_path, calendars, **kwargs):
    from catchup_core.adapters import fetch_via_handler
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    monkeypatch.setattr(caldav, '_get_client', lambda *args: _FakeClient(calendars))
    monkeypatch.setattr(caldav, '_get_cal_name', lambda calendar: calendar.name)
    return fetch_via_handler('calendar', 'family', caldav.fetch, since='2026-10-10',
        auth_context={'apple_id': 'a@example.invalid', 'app_password': 'synthetic'},
        fetch_kwargs=kwargs)


@pytest.mark.parametrize('failure', ['search', 'malformed', 'delta'])
def test_caldav_mixed_delta_keeps_explicit_removals_when_refresh_fails(monkeypatch, tmp_path, failure):
    import json
    from caldav.lib.error import NotFoundError

    calendar = _FakeCal('Home', ['invalid ical'] if failure == 'malformed' else [],
                        raises=RuntimeError('503') if failure == 'search' else None)
    removed = types.SimpleNamespace(url='https://caldav.example/Home/removed')

    def missing():
        raise NotFoundError('removed')

    removed.load = missing

    def load_changed():
        if failure == 'delta':
            raise RuntimeError('503')

    changed = types.SimpleNamespace(load=load_changed)

    class Updates(list):
        sync_token = 'next-opaque-token'

    monkeypatch.setattr(calendar, 'get_objects_by_sync_token', lambda *args, **kwargs: Updates([removed, changed]),
                        raising=False)
    cursor = json.dumps({'v': 1, 'token': 'old-opaque-token',
        'through': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        'members': {removed.url: ['removed-instance']}})
    result = _caldav_via_adapter(monkeypatch, tmp_path, [calendar], sync_changes=True,
                                sync_tokens={calendar.url: cursor})
    assert result.cancelled == ('calendar:["https://caldav.example/Home","removed-instance"]',)
    assert result.status.value == 'degraded'
    assert result.cursor is None and not result.full_listings


@pytest.mark.parametrize('end', ['20261015', '20261017'])
def test_caldav_all_day_dates_survive_handler_validator_adapter(monkeypatch, tmp_path, end):
    ics = ICS.format(n=1).replace('DTSTART:20261014T230000Z', 'DTSTART;VALUE=DATE:20261014')
    ics = ics.replace('DTEND:20261015T000000Z', f'DTEND;VALUE=DATE:{end}')
    result = _caldav_via_adapter(monkeypatch, tmp_path, [_FakeCal('Home', [ics])])
    assert result.status.value == 'complete' and not result.blind_spots
    assert len(result.records) == 1
    calendar = result.records[0].calendar
    assert calendar is not None and calendar.all_day
    assert type(calendar.start) is date and calendar.start == date(2026, 10, 14)
    assert type(calendar.end) is date and calendar.end == date.fromisoformat(end)


@pytest.mark.parametrize('malformed', ['missing_start', 'invalid_ics'])
def test_caldav_malformed_sibling_retains_positives_with_degraded_coverage(monkeypatch, tmp_path, malformed):
    bad = ICS.format(n='bad').replace('DTSTART:20261014T230000Z\r\n', '')
    if malformed == 'invalid_ics':
        bad = ICS.format(n='bad').replace('DTSTART:20261014T230000Z', 'DTSTART:invalid')
    result = _caldav_via_adapter(monkeypatch, tmp_path, [
        _FakeCal('Home', [ICS.format(n=1), bad, ICS.format(n=2)])])
    assert result.status.value == 'degraded'
    assert len(result.records) == 2
    assert {record.calendar.start for record in result.records} == {
        datetime(2026, 10, 14, 23, tzinfo=timezone.utc)}
    assert any('calendar:Home: failed' in gap for gap in result.blind_spots)


@pytest.mark.parametrize('filters', [['Missing'], ['Home', 'Missing']])
def test_caldav_unmatched_filter_is_not_complete_empty_or_widened(monkeypatch, tmp_path, filters):
    home, work = _FakeCal('Home', [ICS.format(n=1)]), _FakeCal('Work', [ICS.format(n=2)])
    monkeypatch.setattr(work, 'search', lambda **kwargs: pytest.fail('unselected calendar searched'))
    if filters == ['Missing']:
        monkeypatch.setattr(home, 'search', lambda **kwargs: pytest.fail('unselected calendar searched'))
    result = _caldav_via_adapter(monkeypatch, tmp_path, [home, work], calendar_filter=filters)
    assert result.status.value == 'degraded' and result.blind_spots
    assert len(result.records) == (1 if 'Home' in filters else 0)


@pytest.mark.parametrize('filters, count', [(['hOm'], 1), (['https://caldav.example/Home'], 1), (['Home'], 0)])
def test_caldav_valid_filter_retains_selected_scope(monkeypatch, tmp_path, filters, count):
    home = _FakeCal('Home', [ICS.format(n=1)] if count else [])
    work = _FakeCal('Work', [ICS.format(n=2)])
    monkeypatch.setattr(work, 'search', lambda **kwargs: pytest.fail('unselected calendar searched'))
    result = _caldav_via_adapter(monkeypatch, tmp_path, [home, work], calendar_filter=filters)
    assert result.status.value == 'complete' and not result.blind_spots
    assert len(result.records) == count


@pytest.mark.parametrize('filters', [[], '', {}, [''], [' '], [1], ['Home', 'Home'], ['Home', 'home']])
def test_caldav_invalid_filter_refused_before_discovery(monkeypatch, tmp_path, filters):
    home = _FakeCal('Home', [ICS.format(n=1)])
    monkeypatch.setattr(home, 'search', lambda **kwargs: pytest.fail('invalid filter searched a calendar'))
    result = _caldav_via_adapter(monkeypatch, tmp_path, [home], calendar_filter=filters)
    assert result.status.value == 'unavailable' and not result.records
    assert result.blind_spots == ('fetch failed: ValueError',)
    monkeypatch.setattr(caldav, '_get_client', lambda *args: pytest.fail('invalid filter opened a client'))
    with pytest.raises(ValueError, match='calendar_filter'):
        _run(caldav.fetch, auth_context={'apple_id': 'a@example.invalid', 'app_password': 'synthetic'},
             calendar_filter=filters)
