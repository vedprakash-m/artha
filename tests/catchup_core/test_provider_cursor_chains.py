"""Graph calendar delta, CalDAV sync-collection and mail cursor chains (.archive/specs/provider-observation-cursors-2026-11-05.md).

Provider-shaped synthetic fakes -> real handler -> validator -> adapter -> Coordinator -> Ledger.  Nothing here
talks to a provider; every provider claim is an unverified expectation until live consented parity.
"""
from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest
from test_provider_observation_lifecycle import MSG, _fact_rows, env, req  # noqa: F401  (env is a fixture)

from catchup_core import coordinator as co
from catchup_core import runtime
from catchup_core.contracts import FailureClass, RunStatus

GRAPH = 'https://graph.microsoft.com/v1.0'


def _http(status):
    exc = RuntimeError('synthetic provider text that must not be disclosed')
    exc.response = SimpleNamespace(status_code=status)
    return exc


@pytest.fixture
def run_source(env, monkeypatch, tmp_path):  # noqa: F811
    """run(handler_module, connector, kwargs, cid) through the real runtime fetcher."""
    import lib.retry as retry
    ledger, _, make, _ = env
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    monkeypatch.setattr(retry, 'with_retry', lambda fn, **kw: fn())

    def run(module, connector, kwargs, cid):
        config = SimpleNamespace(sources={'calendar': {'connector': connector, 'account_scope': 'acct-a',
            'subjects': ['kid1'], 'kwargs': dict(kwargs)}}, subjects=['kid1'], consent_required=frozenset(),
            viewer='ved')
        fetchers = runtime.build_fetchers(config, '2026-10-01T00:00:00Z', lambda name: module,
                                          lambda name: {'access_token': 'synthetic-token', 'apple_id': 'a@example.test',
                                                        'app_password': 'synthetic'}, {'calendar': True}, ledger)
        return co.run_catchup(req(cid), make({'calendar': fetchers['calendar']}))
    return ledger, run


def _calendar_ids(ledger):
    return sorted(r[0] for r in ledger.conn.execute('SELECT upstream_id FROM calendar_records'))


def _cursor(ledger):
    return json.loads(ledger.get_cursor('calendar', 'acct-a'))['collections']


# ── Graph calendar ───────────────────────────────────────────────────────────────────────────────────────

class _Graph:
    """Scripted Graph.  ``first`` serves the path call, ``pages`` serve full-URL calls, in order."""

    def __init__(self, first=(), pages=()):
        self.first, self.pages = list(first), list(pages)
        self.path_calls, self.url_calls = [], []

    def install(self, monkeypatch):
        import lib.msgraph as graph

        def pop(queue):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        monkeypatch.setattr(graph, '_graph_get', lambda token, path, params=None, **kw:
                            (self.path_calls.append((token, path, dict(params or {}))), pop(self.first))[1])
        monkeypatch.setattr(graph, '_graph_get_full_url', lambda token, url:
                            (self.url_calls.append((token, url)), pop(self.pages))[1])


def _ev(event_id, day=15, summary=None, **extra):
    return {'id': event_id, 'subject': summary or f'Class {event_id}', 'isAllDay': False,
            'start': {'dateTime': f'2026-10-{day:02d}T17:00:00.0000000', 'timeZone': 'UTC'},
            'end': {'dateTime': f'2026-10-{day:02d}T18:00:00.0000000', 'timeZone': 'UTC'}, **extra}


def _delta(*values, link=None, token=None):
    page = {'value': list(values)}
    if link:
        page['@odata.nextLink'] = link
    if token:
        page['@odata.deltaLink'] = token
    return page


@pytest.fixture
def graph_run(run_source, monkeypatch):
    import connectors.msgraph_calendar as mod
    ledger, run = run_source

    def go(api, cid, calendars=('cal1',), **extra):
        api.install(monkeypatch)
        kwargs = {'sync_changes': True, 'calendar_ids': list(calendars), 'window_days': 400, **extra}
        return run(mod, 'outlook_calendar_personal', kwargs, cid)
    return ledger, go


def _link(token, host='https://graph.microsoft.com/v1.0/me/calendars/cal1/calendarView/delta'):
    return f'{host}?$deltatoken={token}'


def test_graph_initial_delta_persists_cursor_then_removed_item_cancels_one_event(graph_run):
    ledger, go = graph_run
    first = _Graph([_delta(_ev('a'), _ev('b', 22), token=_link('t1'))])
    assert go(first, 'c-1').status is RunStatus.COMPLETE
    assert first.path_calls[0][1] == '/me/calendars/cal1/calendarView/delta'
    assert {'startDateTime', 'endDateTime'} <= set(first.path_calls[0][2])
    assert '$orderby' not in first.path_calls[0][2] and '$select' not in first.path_calls[0][2]
    assert json.loads(_cursor(ledger)['cal1'])['link'] == _link('t1')
    assert len(_calendar_ids(ledger)) == 2
    second = _Graph(pages=[_delta({'id': 'a', '@removed': {'reason': 'deleted'}}, token=_link('t2'))])
    go(second, 'c-2')
    assert second.url_calls == [('synthetic-token', _link('t1'))] and not second.path_calls
    assert _calendar_ids(ledger) == ['calendar:["cal1","b"]']
    assert json.loads(_cursor(ledger)['cal1'])['link'] == _link('t2')


def test_graph_delta_keeps_the_window_end_of_the_listing_that_built_it(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(token=_link('t1'))]), 'c-1')
    through = json.loads(_cursor(ledger)['cal1'])['through']
    go(_Graph(pages=[_delta(token=_link('t2'))]), 'c-2')
    assert json.loads(_cursor(ledger)['cal1'])['through'] == through


def test_graph_delta_accepts_a_delayed_change_to_a_past_event_and_cancelled_flag(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('a'), token=_link('t1'))]), 'c-1')
    late = _ev('old')
    late['start']['dateTime'] = '2020-01-01T10:00:00.0000000'
    late['end']['dateTime'] = '2020-01-01T11:00:00.0000000'
    go(_Graph(pages=[_delta(late, _ev('a', isCancelled=True), token=_link('t2'))]), 'c-2')
    assert _calendar_ids(ledger) == ['calendar:["cal1","old"]']


def test_graph_410_resets_cursor_then_full_resync_withdraws_only_the_omitted_occurrence(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('keep'), _ev('gone', summary=MSG), token=_link('t1'))]), 'c-1')
    facts = _fact_rows(ledger)
    assert facts
    reset = go(_Graph(pages=[_http(410)]), 'c-410')
    assert reset.status is RunStatus.DEGRADED and ledger.get_cursor('calendar', 'acct-a') is None
    assert len(_calendar_ids(ledger)) == 2                       # a reset deletes nothing
    full = _Graph([_delta(_ev('keep'), token=_link('t9'))])
    go(full, 'c-full')
    assert full.path_calls and not full.url_calls                # full resync, not the dead link
    assert _calendar_ids(ledger) == ['calendar:["cal1","keep"]'] and _fact_rows(ledger) == facts


def test_graph_delta_run_never_withdraws_by_omission(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('a'), _ev('b'), token=_link('t1'))]), 'c-1')
    go(_Graph(pages=[_delta(token=_link('t2'))]), 'c-2')
    assert len(_calendar_ids(ledger)) == 2


@pytest.mark.parametrize('failure', ['second_page_fails', 'repeated_page', 'no_delta_link', 'page_cap'])
def test_graph_partial_pages_keep_the_old_cursor_and_withdraw_nothing(graph_run, failure):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('a'), _ev('b'), token=_link('t1'))]), 'c-1')
    old = ledger.get_cursor('calendar', 'acct-a')
    page2 = 'https://graph.microsoft.com/v1.0/next?$skiptoken=p2'
    api = {
        'second_page_fails': _Graph(pages=[_delta(_ev('a'), link=page2), _http(503)]),
        'repeated_page': _Graph(pages=[_delta(_ev('a'), link=page2), _delta(link=page2)]),
        'no_delta_link': _Graph(pages=[_delta(_ev('a'))]),
        'page_cap': _Graph(pages=[_delta(link=f'{GRAPH}/n?$skiptoken={i}') for i in range(30)]),
    }[failure]
    assert go(api, 'c-partial').status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') == old and len(_calendar_ids(ledger)) == 2


def test_graph_full_listing_with_a_failed_page_withdraws_nothing(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('a'), _ev('b'), token=_link('t1'))]), 'c-1')
    go(_Graph(pages=[_http(410)]), 'c-410')
    page2 = f'{GRAPH}/next?$skiptoken=p2'
    assert go(_Graph([_delta(_ev('a'), link=page2)], [_http(503)]), 'c-bad').status is RunStatus.DEGRADED
    assert len(_calendar_ids(ledger)) == 2


def test_graph_revoked_access_is_auth_and_keeps_cursor_and_evidence(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('a'), token=_link('t1'))]), 'c-1')
    old = ledger.get_cursor('calendar', 'acct-a')
    result = go(_Graph(pages=[_http(401)]), 'c-401')
    assert FailureClass.AUTH in result.failures
    assert ledger.get_cursor('calendar', 'acct-a') == old and len(_calendar_ids(ledger)) == 1
    assert 'synthetic provider text' not in str(ledger.get_coverage('calendar', 'acct-a')['blind_spots'])


def test_graph_403_stays_a_generic_provider_failure(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('a'), token=_link('t1'))]), 'c-1')
    result = go(_Graph(pages=[_http(403)]), 'c-403')
    assert result.status is RunStatus.DEGRADED and FailureClass.AUTH not in result.failures


def test_graph_not_found_calendar_is_not_a_deletion(graph_run):
    ledger, go = graph_run
    cals = ('cal1', 'cal2')
    go(_Graph([_delta(_ev('a'), token=_link('t1')), _delta(_ev('x'), token=_link('o1', GRAPH + '/me/calendars/cal2/calendarView/delta'))]),
       'c-1', cals)
    go(_Graph(pages=[_delta(token=_link('t2')), _http(404)]), 'c-404', cals)
    assert len(_calendar_ids(ledger)) == 2


def test_graph_stored_link_for_another_host_is_never_called_and_resets(graph_run):
    ledger, go = graph_run
    bad = json.dumps({'v': 1, 'link': 'https://evil.example.test/steal?t=1', 'through': '2999-01-01T00:00:00Z'})
    ledger.set_coverage('calendar', 'acct-a', 'complete',
                        cursor_generation=json.dumps({'version': 1, 'collections': {'cal1': bad}}))
    api = _Graph([_delta(_ev('a'), token=_link('fresh'))])
    result = go(api, 'c-evil')
    assert not api.url_calls and result.status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') is None


def test_graph_next_link_for_another_host_is_not_followed(graph_run):
    ledger, go = graph_run
    api = _Graph([_delta(_ev('a'), link='https://evil.example.test/page')])
    assert go(api, 'c-evil').status is RunStatus.DEGRADED
    assert not api.url_calls and ledger.get_cursor('calendar', 'acct-a') is None


def test_graph_expired_window_takes_a_new_full_listing_without_a_reset(graph_run):
    ledger, go = graph_run
    expired = json.dumps({'v': 1, 'link': _link('old'), 'through': '2020-01-01T00:00:00Z'})
    ledger.set_coverage('calendar', 'acct-a', 'complete',
                        cursor_generation=json.dumps({'version': 1, 'collections': {'cal1': expired}}))
    api = _Graph([_delta(_ev('a'), token=_link('new'))])
    assert go(api, 'c-exp').status is RunStatus.COMPLETE
    assert api.path_calls and not api.url_calls
    assert json.loads(_cursor(ledger)['cal1'])['link'] == _link('new')


def test_graph_event_cap_is_truncation_and_writes_no_cursor(graph_run):
    ledger, go = graph_run
    api = _Graph([_delta(*[_ev(f'e{i}') for i in range(5)], token=_link('t1'))])
    assert go(api, 'c-cap', max_results=3).status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') is None


def test_graph_malformed_event_fails_the_scope_without_a_cursor(graph_run):
    ledger, go = graph_run
    assert go(_Graph([_delta(_ev('a'), {'subject': 'no id'}, token=_link('t1'))]), 'c-bad').status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') is None


def test_graph_sync_requires_explicit_calendar_ids_and_valid_options():
    import connectors.msgraph_calendar as mod
    base = dict(since='2026-10-01T00:00:00Z', auth_context={'access_token': 'x'})
    with pytest.raises(ValueError, match='explicit calendar_ids'):
        list(mod.fetch(sync_changes=True, **base))
    with pytest.raises(ValueError, match='boolean'):
        list(mod.fetch(sync_changes='yes', **base))
    with pytest.raises(ValueError, match='sync_tokens'):
        list(mod.fetch(sync_changes=True, calendar_ids=['c'], sync_tokens={'other': 'x'}, **base))
    with pytest.raises(ValueError, match='sync_tokens'):
        list(mod.fetch(sync_changes=False, calendar_ids=['c'], sync_tokens={'c': 'x'}, **base))


def test_a_quiet_delta_run_is_not_a_volume_anomaly_and_still_advances_the_cursor(graph_run):
    ledger, go = graph_run
    go(_Graph([_delta(_ev('a'), token=_link('t1'))]), 'c-1')
    for n in (2, 3):
        go(_Graph(pages=[_delta(_ev('a', summary=f'Moved {n}'), token=_link(f't{n}'))]), f'c-{n}')
    quiet = go(_Graph(pages=[_delta(token=_link('t4'))]), 'c-4')
    assert quiet.status is RunStatus.COMPLETE
    assert json.loads(_cursor(ledger)['cal1'])['link'] == _link('t4')


def test_a_full_listing_that_returns_nothing_after_busy_history_is_still_an_anomaly(graph_run):
    ledger, go = graph_run
    for n in (1, 2, 3):
        go(_Graph([_delta(_ev('a'), token=_link(f't{n}'))]), f'c-{n}')
        ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=None, reset_cursor=True)
    empty = go(_Graph([_delta(token=_link('t9'))]), 'c-empty')
    assert empty.status is not RunStatus.COMPLETE


# ── CalDAV sync-collection ───────────────────────────────────────────────────────────────────────────────

def _ics(uid, day=15, summary=None, status=None):
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//synthetic//EN', 'BEGIN:VEVENT', f'UID:{uid}',
             'DTSTAMP:20261001T000000Z', f'DTSTART:202610{day:02d}T170000Z', f'DTEND:202610{day:02d}T180000Z',
             f'SUMMARY:{summary or "Class " + uid}']
    if status:
        lines.append(f'STATUS:{status}')
    return '\r\n'.join(lines + ['END:VEVENT', 'END:VCALENDAR', ''])


class _Member:
    def __init__(self, href, ics=None, gone=False):
        self.url, self._ics, self._gone = f'https://caldav.example/cal/{href}', ics, gone

    @property
    def vobject_instance(self):
        import vobject
        return vobject.readOne(self._ics)

    def load(self):
        if self._gone:
            from caldav.lib.error import NotFoundError
            raise NotFoundError('gone')


class _Sync:
    def __init__(self, token, members=()):
        self.sync_token, self._members = token, list(members)

    def __iter__(self):
        return iter(self._members)


class _DavCal:
    """``syncs`` and ``searches`` are queues of scripted results (an Exception is raised)."""

    def __init__(self, syncs=(), searches=(), name='home'):
        self.name, self.url = name, f'https://caldav.example/{name}/'
        self.syncs, self.searches = list(syncs), list(searches)
        self.sync_calls, self.search_calls = [], []

    def get_objects_by_sync_token(self, token=None, load_objects=False, disable_fallback=False):
        self.sync_calls.append((token, load_objects, disable_fallback))
        item = self.syncs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def search(self, **kw):
        self.search_calls.append(kw)
        item = self.searches.pop(0)
        if isinstance(item, Exception):
            raise item
        return list(item)


@pytest.fixture
def dav_run(run_source, monkeypatch):
    import connectors.caldav_calendar as mod
    ledger, run = run_source

    def go(cals, cid, **extra):
        client = SimpleNamespace(principal=lambda: SimpleNamespace(calendars=lambda: list(cals)))
        monkeypatch.setattr(mod, '_get_client', lambda a, p: client)
        monkeypatch.setattr(mod, '_get_cal_name', lambda c: c.name)
        return run(mod, 'icloud_calendar', {'sync_changes': True, **extra}, cid)
    return ledger, go


def _dav_cursor(ledger, url='https://caldav.example/home/'):
    return json.loads(_cursor(ledger)[url])


def _ev_ics(uid, day=15, **kw):
    return _Member(f'{uid}.ics', _ics(uid, day, **kw))


def test_caldav_full_listing_takes_the_token_first_then_lists_and_remembers_members(dav_run):
    ledger, go = dav_run
    cal = _DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a'), _ev_ics('b', 16)]])
    assert go([cal], 'c-1').status is RunStatus.COMPLETE
    assert cal.sync_calls == [(None, False, True)]
    cursor = _dav_cursor(ledger)
    assert cursor['token'] == 'tok-1' and cursor['members'] == {
        'https://caldav.example/cal/a.ics': ['a'], 'https://caldav.example/cal/b.ics': ['b']}
    assert len(_calendar_ids(ledger)) == 2


def test_caldav_removed_member_cancels_only_its_mapped_identities(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a'), _ev_ics('b', 16)]])], 'c-1')
    cal = _DavCal(syncs=[_Sync('tok-2', [_Member('a.ics', gone=True)])])
    assert go([cal], 'c-2').status is RunStatus.COMPLETE
    assert cal.sync_calls == [('tok-1', False, True)] and not cal.search_calls
    assert len(_calendar_ids(ledger)) == 1
    cursor = _dav_cursor(ledger)
    assert cursor['token'] == 'tok-2' and list(cursor['members']) == ['https://caldav.example/cal/b.ics']


def test_caldav_changed_member_triggers_a_full_listing_with_the_new_token(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a'), _ev_ics('b', 16)]])], 'c-1')
    cal = _DavCal(syncs=[_Sync('tok-2', [_ev_ics('a')])], searches=[[_ev_ics('a', summary='Moved')]])
    go([cal], 'c-2')
    assert len(cal.sync_calls) == 1 and cal.search_calls                  # token reused, not re-requested
    assert _dav_cursor(ledger)['token'] == 'tok-2'
    assert _calendar_ids(ledger) == ['calendar:["https://caldav.example/home/","a"]']   # b omitted by a COMPLETE full listing


def test_caldav_no_change_yields_nothing_and_never_withdraws(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a')]])], 'c-1')
    cal = _DavCal(syncs=[_Sync('tok-1')])
    assert go([cal], 'c-2').status is RunStatus.COMPLETE
    assert len(_calendar_ids(ledger)) == 1 and not cal.search_calls


def test_caldav_invalid_sync_token_reports_a_reset_keeps_evidence_then_full_resync_withdraws_omission(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('keep'), _ev_ics('gone', 16, summary=MSG)]])], 'c-1')
    facts = _fact_rows(ledger)
    assert facts
    bad = RuntimeError('<D:error><D:valid-sync-token/></D:error> synthetic provider text')
    result = go([_DavCal(syncs=[bad])], 'c-reset')
    assert result.status is RunStatus.DEGRADED and ledger.get_cursor('calendar', 'acct-a') is None
    assert len(_calendar_ids(ledger)) == 2
    go([_DavCal(syncs=[_Sync('tok-9')], searches=[[_ev_ics('keep')]])], 'c-full')
    assert len(_calendar_ids(ledger)) == 1 and _fact_rows(ledger) == facts
    assert _dav_cursor(ledger)['token'] == 'tok-9'


def test_caldav_other_sync_error_fails_the_scope_and_keeps_the_cursor(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a')]])], 'c-1')
    old = ledger.get_cursor('calendar', 'acct-a')
    assert go([_DavCal(syncs=[RuntimeError('503')])], 'c-2').status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') == old and len(_calendar_ids(ledger)) == 1


def test_caldav_member_load_error_other_than_not_found_fails_the_scope(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a')]])], 'c-1')
    broken = _Member('a.ics')
    broken.load = lambda: (_ for _ in ()).throw(RuntimeError('timeout'))
    assert go([_DavCal(syncs=[_Sync('tok-2', [broken])])], 'c-2').status is RunStatus.DEGRADED
    assert _dav_cursor(ledger)['token'] == 'tok-1'


@pytest.mark.parametrize('failure', ['search_fails', 'malformed_event', 'result_cap'])
def test_caldav_partial_full_listing_keeps_the_old_cursor_and_withdraws_nothing(dav_run, failure):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a'), _ev_ics('b', 16)]])], 'c-1')
    go([_DavCal(syncs=[RuntimeError('<valid-sync-token/>')])], 'c-reset')
    extra = {}
    if failure == 'search_fails':
        searches = [RuntimeError('503')]
    elif failure == 'malformed_event':
        searches = [[_ev_ics('a'), _Member('bad.ics', 'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n')]]
    else:
        searches, extra = [[_ev_ics('a'), _ev_ics('b', 16)]], {'max_results': 1}
    assert go([_DavCal(syncs=[_Sync('tok-2')], searches=searches)], 'c-partial', **extra).status is RunStatus.DEGRADED
    assert len(_calendar_ids(ledger)) == 2 and ledger.get_cursor('calendar', 'acct-a') is None


def test_caldav_server_without_real_sync_tokens_stays_windowed_with_no_cursor(dav_run):
    ledger, go = dav_run
    for n, first in enumerate((_Sync('fake-abc'), RuntimeError('REPORT not supported'))):
        cal = _DavCal(syncs=[first], searches=[[_ev_ics('a')]])
        assert go([cal], f'c-x{n}').status is RunStatus.COMPLETE and len(cal.sync_calls) == 1
        assert ledger.get_cursor('calendar', 'acct-a') is None and len(_calendar_ids(ledger)) == 1


def test_caldav_fake_token_from_a_delta_call_fails_the_scope(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a')]])], 'c-1')
    assert go([_DavCal(syncs=[_Sync('fake-zzz')])], 'c-2').status is RunStatus.DEGRADED
    assert _dav_cursor(ledger)['token'] == 'tok-1'


def test_caldav_expired_cursor_takes_a_full_listing_without_a_reset(dav_run):
    ledger, go = dav_run
    url = 'https://caldav.example/home/'
    old = json.dumps({'v': 1, 'token': 'old', 'through': '2020-01-01T00:00:00+00:00', 'members': {}})
    ledger.set_coverage('calendar', 'acct-a', 'complete',
                        cursor_generation=json.dumps({'version': 1, 'collections': {url: old}}))
    cal = _DavCal(syncs=[_Sync('tok-new')], searches=[[_ev_ics('a')]])
    assert go([cal], 'c-exp').status is RunStatus.COMPLETE
    assert cal.sync_calls == [(None, False, True)] and _dav_cursor(ledger)['token'] == 'tok-new'


def test_caldav_unusable_stored_cursor_resets_and_nothing_is_deleted(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a')]])], 'c-1')
    url = 'https://caldav.example/home/'
    ledger.set_coverage('calendar', 'acct-a', 'complete',
                        cursor_generation=json.dumps({'version': 1, 'collections': {url: 'not json'}}))
    assert go([_DavCal()], 'c-bad').status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') is None and len(_calendar_ids(ledger)) == 1


def test_caldav_revoked_access_is_auth_and_keeps_cursor_and_evidence(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a')]])], 'c-1')
    result = go([_DavCal(syncs=[RuntimeError('401 Unauthorized')])], 'c-401')
    assert FailureClass.AUTH in result.failures and _dav_cursor(ledger)['token'] == 'tok-1'
    assert 'Unauthorized' not in str(ledger.get_coverage('calendar', 'acct-a')['blind_spots'])


def test_caldav_calendar_without_a_stable_url_cannot_use_a_cursor(dav_run):
    ledger, go = dav_run
    cal = _DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a')]])
    cal.url = ''
    assert go([cal], 'c-nourl').status is RunStatus.DEGRADED and ledger.get_cursor('calendar', 'acct-a') is None


def test_caldav_sync_option_validation():
    import connectors.caldav_calendar as mod
    base = dict(since='2026-10-01T00:00:00Z', auth_context={'apple_id': 'a', 'app_password': 'p'})
    with pytest.raises(ValueError, match='boolean'):
        list(mod.fetch(sync_changes='yes', **base))
    with pytest.raises(ValueError, match='sync_tokens'):
        list(mod.fetch(sync_changes=False, sync_tokens={'u': 'x'}, **base))
    with pytest.raises(ValueError, match='sync_tokens'):
        list(mod.fetch(sync_changes=True, sync_tokens={'u': ''}, **base))


# ── Gmail history cursor ─────────────────────────────────────────────────────────────────────────────────

def _b64(text):
    import base64
    return base64.urlsafe_b64encode(text.encode()).decode()


def _gmsg(mid, body='Hello', labels=('INBOX',)):
    return {'id': mid, 'threadId': 't' + mid, 'historyId': '1', 'labelIds': list(labels), 'snippet': body[:20],
            'payload': {'mimeType': 'text/plain', 'body': {'data': _b64(body)}, 'headers': [
                {'name': 'Subject', 'value': f'Note {mid}'}, {'name': 'From', 'value': 'school@example.test'},
                {'name': 'To', 'value': 'family@example.test'}, {'name': 'Date', 'value': 'Wed, 14 Oct 2026 09:00:00 +0000'}]}}


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _Gmail:
    """Scripted Gmail: ``profile`` id, ``listing`` message ids, ``history`` pages, ``messages`` by id."""

    def __init__(self, profile='100', listing=(), history=(), messages=None, profile_error=None, listing_pages=None):
        self.profile, self.listing, self.history = profile, list(listing), list(history)
        self.messages, self.profile_error, self.calls = messages or {}, profile_error, []
        self.listing_pages = list(listing_pages) if listing_pages is not None else None

    def users(self):
        outer = self

        def run(name, kwargs, fn):
            outer.calls.append((name, kwargs))
            return _Exec(fn)

        class _Messages:
            def list(_s, **kw):
                def fn():
                    item = (outer.listing_pages.pop(0) if outer.listing_pages is not None
                            else {'messages': [{'id': i} for i in outer.listing]})
                    if isinstance(item, Exception):
                        raise item
                    return item
                return run('messages.list', kw, fn)

            def get(_s, **kw):
                def fn():
                    item = outer.messages[kw['id']]
                    if isinstance(item, Exception):
                        raise item
                    return item
                return run('messages.get', kw, fn)

        class _History:
            def list(_s, **kw):
                def fn():
                    item = outer.history.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                return run('history.list', kw, fn)

        class _Users:
            def getProfile(_s, **kw):  # noqa: N802 - provider method name
                def fn():
                    if outer.profile_error:
                        raise outer.profile_error
                    return {'historyId': outer.profile}
                return run('getProfile', kw, fn)

            def messages(_s):
                return _Messages()

            def history(_s):
                return _History()
        return _Users()


def _gcalls(api, name):
    return [kw for n, kw in api.calls if n == name]


@pytest.fixture
def gmail_run(run_source, monkeypatch):
    import connectors.google_email as mod
    ledger, run = run_source

    def go(api, cid, **extra):
        module = types.ModuleType('google_auth')
        module.build_service = lambda *a, **k: api
        monkeypatch.setitem(sys.modules, 'google_auth', module)
        return run(mod, 'gmail', {'sync_changes': True, **extra}, cid)
    return ledger, go


def _cursor_text(ledger):
    return ledger.get_cursor('calendar', 'acct-a')


def _signals(ledger):
    return sorted(r[0] for r in ledger.conn.execute('SELECT upstream_id FROM signals'))


GMAIL_CURSOR = '{"history_id":"%s","kind":"gmail","v":1}'


def test_gmail_window_run_takes_the_history_id_before_listing_and_stores_it(gmail_run):
    ledger, go = gmail_run
    api = _Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')})
    assert go(api, 'c-1').status is RunStatus.COMPLETE
    names = [n for n, _ in api.calls]
    assert names.index('getProfile') < names.index('messages.list')
    assert _cursor_text(ledger) == GMAIL_CURSOR % '100' and _signals(ledger) == ['m1']


@pytest.mark.parametrize('failure', ['repeat', 'page_cap', 'failed_page'])
def test_gmail_initial_listing_is_bounded_and_retains_partial_evidence(gmail_run, monkeypatch, failure):
    import connectors.google_email as gmail

    ledger, go = gmail_run
    monkeypatch.setattr(gmail, 'MAX_PAGES', 2)
    first = {'messages': [{'id': 'm1'}], 'nextPageToken': 'opaque/+=='}
    second = (_Gmail_error(503) if failure == 'failed_page' else
              {'messages': [], 'nextPageToken': 'opaque/+==' if failure == 'repeat' else 'different/+=='})
    api = _Gmail(listing_pages=[first, second, {'messages': []}], messages={'m1': _gmsg('m1')})
    assert go(api, 'gmail-incomplete-listing').status is RunStatus.DEGRADED
    assert _signals(ledger) == ['m1']
    assert _cursor_text(ledger) is None
    assert len(_gcalls(api, 'messages.list')) == 2


def test_gmail_history_run_reads_only_new_messages_and_advances_the_cursor(gmail_run):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    api = _Gmail(history=[
        {'history': [{'messagesAdded': [{'message': {'id': 'm2'}}]}], 'nextPageToken': 'p2', 'historyId': '110'},
        {'history': [{'messagesAdded': [{'message': {'id': 'm3'}}, {'message': {'id': 'm2'}}]}], 'historyId': '120'}],
        messages={'m2': _gmsg('m2'), 'm3': _gmsg('m3')})
    assert go(api, 'c-2').status is RunStatus.COMPLETE
    first, second = _gcalls(api, 'history.list')
    assert first['startHistoryId'] == '100' and first['historyTypes'] == ['messageAdded']
    assert second['pageToken'] == 'p2' and not _gcalls(api, 'messages.list')
    assert _signals(ledger) == ['m1', 'm2', 'm3'] and _cursor_text(ledger) == GMAIL_CURSOR % '120'


def test_gmail_404_resets_the_cursor_keeps_evidence_and_the_next_run_is_windowed(gmail_run):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    result = go(_Gmail(history=[_Gmail_error(404)]), 'c-404')
    assert result.status is RunStatus.DEGRADED and _cursor_text(ledger) is None and _signals(ledger) == ['m1']
    again = _Gmail('200', listing=['m1', 'm2'], messages={'m1': _gmsg('m1'), 'm2': _gmsg('m2')})
    go(again, 'c-full')
    assert _gcalls(again, 'messages.list') and _cursor_text(ledger) == GMAIL_CURSOR % '200'


def _Gmail_error(status):  # noqa: N802
    exc = RuntimeError('synthetic provider text that must not be disclosed')
    exc.resp = SimpleNamespace(status=status)
    return exc


def test_gmail_deleted_trashed_or_vanished_mail_changes_nothing_and_completes_nothing(gmail_run):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1', MSG)}), 'c-1')
    facts = _fact_rows(ledger)
    assert facts
    api = _Gmail(history=[{'history': [
        {'messagesDeleted': [{'message': {'id': 'm1'}}]},
        {'messagesAdded': [{'message': {'id': 'trashed'}}, {'message': {'id': 'vanished'}}]}], 'historyId': '130'}],
        messages={'trashed': _gmsg('trashed', labels=('TRASH',)), 'vanished': _Gmail_error(404)})
    go(api, 'c-2')
    assert ledger.get_coverage('calendar', 'acct-a')['status'] == 'complete'
    assert _signals(ledger) == ['m1'] and _fact_rows(ledger) == facts
    assert _signal_state(ledger, 'm1') != 'excluded' and _cursor_text(ledger) == GMAIL_CURSOR % '130'


def _signal_state(ledger, upstream):
    return ledger.conn.execute('SELECT processing_state FROM signals WHERE upstream_id=?', (upstream,)).fetchone()[0]


@pytest.mark.parametrize('failure', ['second_page_fails', 'repeated_page', 'no_history_id', 'message_get_fails'])
def test_gmail_partial_history_keeps_the_old_cursor(gmail_run, failure):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    added = [{'messagesAdded': [{'message': {'id': 'm2'}}]}]
    api = {
        'second_page_fails': _Gmail(history=[{'history': added, 'nextPageToken': 'p2', 'historyId': '1'},
                                             _Gmail_error(503)], messages={'m2': _gmsg('m2')}),
        'repeated_page': _Gmail(history=[{'history': added, 'nextPageToken': 'p2', 'historyId': '1'},
                                         {'nextPageToken': 'p2', 'historyId': '1'}], messages={'m2': _gmsg('m2')}),
        'no_history_id': _Gmail(history=[{'history': added}], messages={'m2': _gmsg('m2')}),
        'message_get_fails': _Gmail(history=[{'history': added, 'historyId': '150'}],
                                    messages={'m2': _Gmail_error(500)}),
    }[failure]
    assert go(api, 'c-partial').status is RunStatus.DEGRADED
    assert _cursor_text(ledger) == GMAIL_CURSOR % '100' and _signals(ledger) in (['m1'], ['m1', 'm2'])


def test_gmail_revoked_access_is_auth_and_keeps_cursor(gmail_run):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    result = go(_Gmail(history=[_Gmail_error(401)]), 'c-401')
    assert FailureClass.AUTH in result.failures and _cursor_text(ledger) == GMAIL_CURSOR % '100'
    assert 'synthetic provider text' not in str(ledger.get_coverage('calendar', 'acct-a')['blind_spots'])


def test_gmail_more_new_messages_than_the_cap_is_truncation_without_a_cursor_advance(gmail_run):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    api = _Gmail(history=[{'history': [{'messagesAdded': [{'message': {'id': f'n{i}'}} for i in range(3)]}],
                           'historyId': '160'}], messages={f'n{i}': _gmsg(f'n{i}') for i in range(3)})
    assert go(api, 'c-cap', max_results=2).status is RunStatus.DEGRADED
    assert _cursor_text(ledger) == GMAIL_CURSOR % '100'


@pytest.mark.parametrize('option', [{'query': 'from:school'}, {'before': '2026-12-01T00:00:00'},
                                    {'label_filter': 'school'}])
def test_gmail_sources_with_listing_options_stay_windowed_and_store_no_cursor(gmail_run, option):
    ledger, go = gmail_run
    api = _Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')})
    assert go(api, 'c-opt', **option).status is RunStatus.COMPLETE
    assert not _gcalls(api, 'getProfile') and _cursor_text(ledger) is None


def test_gmail_profile_failure_means_windowed_without_a_cursor(gmail_run):
    ledger, go = gmail_run
    api = _Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}, profile_error=_Gmail_error(500))
    assert go(api, 'c-1').status is RunStatus.COMPLETE and _cursor_text(ledger) is None


@pytest.mark.parametrize('bad', ['not json', '{"v":2}', '{"history_id":"x","kind":"gmail","v":1}'])
def test_gmail_unusable_stored_cursor_resets_and_deletes_nothing(gmail_run, bad):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=bad)
    assert go(_Gmail(), 'c-bad').status is RunStatus.DEGRADED
    assert _cursor_text(ledger) is None and _signals(ledger) == ['m1']


def test_gmail_sync_option_validation():
    import connectors.google_email as mod
    base = dict(since='2026-10-01T00:00:00Z', auth_context={})
    with pytest.raises(ValueError, match='boolean'):
        list(mod.fetch(sync_changes='yes', **base))
    with pytest.raises(ValueError, match='sync_cursor'):
        list(mod.fetch(sync_changes=False, sync_cursor='x', **base))
    with pytest.raises(ValueError, match='sync_cursor'):
        list(mod.fetch(sync_changes=True, sync_cursor=' ', **base))


# ── Graph mail delta ─────────────────────────────────────────────────────────────────────────────────────

def _mail(mid, body='Hello'):
    return {'id': mid, 'conversationId': 'c' + mid, 'subject': f'Note {mid}',
            'from': {'emailAddress': {'name': 'School', 'address': 'school@example.test'}},
            'toRecipients': [], 'ccRecipients': [], 'receivedDateTime': '2026-10-14T09:00:00Z',
            'body': {'contentType': 'text', 'content': body}, 'bodyPreview': body[:20], 'isRead': True}


def _mlink(token):
    return f'{GRAPH}/me/mailFolders/inbox/messages/delta?$deltatoken={token}'


def _mcursor(token):
    return json.dumps({'folder': 'inbox', 'kind': 'graph', 'link': _mlink(token), 'v': 1},
                      sort_keys=True, separators=(',', ':'))


@pytest.fixture
def gmail_graph_run(run_source, monkeypatch):
    import connectors.msgraph_email as mod
    ledger, run = run_source

    def go(api, cid, **extra):
        api.install(monkeypatch)
        return run(mod, 'outlook_email', {'sync_changes': True, **extra}, cid)
    return ledger, go


def test_graph_mail_first_delta_stores_cursor_then_delta_reads_changes_and_ignores_removals(gmail_graph_run):
    ledger, go = gmail_graph_run
    first = _Graph([_delta(_mail('m1', MSG), token=_mlink('t1'))])
    go(first, 'c-1')
    path, params = first.path_calls[0][1], first.path_calls[0][2]
    assert path == '/me/mailFolders/inbox/messages/delta' and params['$filter'].startswith('receivedDateTime ge ')
    assert _cursor_text(ledger) == _mcursor('t1')
    facts = _fact_rows(ledger)
    assert facts
    second = _Graph(pages=[_delta({'id': 'm1', '@removed': {'reason': 'deleted'}}, _mail('m2'), token=_mlink('t2'))])
    go(second, 'c-2')
    assert second.url_calls == [('synthetic-token', _mlink('t1'))] and not second.path_calls
    assert _signals(ledger) == ['m1', 'm2'] and _fact_rows(ledger) == facts      # removal: nothing withdrawn
    assert _signal_state(ledger, 'm1') != 'excluded' and _cursor_text(ledger) == _mcursor('t2')


def test_graph_mail_410_resets_and_the_next_run_is_a_fresh_delta_listing(gmail_graph_run):
    ledger, go = gmail_graph_run
    go(_Graph([_delta(_mail('m1'), token=_mlink('t1'))]), 'c-1')
    assert go(_Graph(pages=[_http(410)]), 'c-410').status is RunStatus.DEGRADED
    assert _cursor_text(ledger) is None and _signals(ledger) == ['m1']
    fresh = _Graph([_delta(_mail('m1'), _mail('m2'), token=_mlink('t9'))])
    go(fresh, 'c-full')
    assert fresh.path_calls and _cursor_text(ledger) == _mcursor('t9')


@pytest.mark.parametrize('failure', ['second_page_fails', 'repeated_page', 'no_delta_link', 'foreign_next_link',
                                     'malformed', 'over_cap'])
def test_graph_mail_partial_delta_keeps_the_old_cursor(gmail_graph_run, failure):
    ledger, go = gmail_graph_run
    go(_Graph([_delta(_mail('m1'), token=_mlink('t1'))]), 'c-1')
    p2 = f'{GRAPH}/next?$skiptoken=p2'
    api = {
        'second_page_fails': _Graph(pages=[_delta(_mail('m2'), link=p2), _http(503)]),
        'repeated_page': _Graph(pages=[_delta(link=p2), _delta(link=p2)]),
        'no_delta_link': _Graph(pages=[_delta(_mail('m2'))]),
        'foreign_next_link': _Graph(pages=[_delta(_mail('m2'), link='https://evil.example.test/x')]),
        'malformed': _Graph(pages=[_delta({'subject': 'no id'}, token=_mlink('t2'))]),
        'over_cap': _Graph(pages=[_delta(_mail('m2'), _mail('m3'), token=_mlink('t2'))]),
    }[failure]
    extra = {'max_results': 1} if failure == 'over_cap' else {}
    assert go(api, 'c-partial', **extra).status is RunStatus.DEGRADED
    assert _cursor_text(ledger) == _mcursor('t1')
    assert not [c for c in api.url_calls if 'evil' in c[1]]
    if failure == 'over_cap':
        assert _signals(ledger) == ['m1', 'm2']


def test_graph_mail_revoked_access_is_auth_and_keeps_cursor(gmail_graph_run):
    ledger, go = gmail_graph_run
    go(_Graph([_delta(_mail('m1'), token=_mlink('t1'))]), 'c-1')
    result = go(_Graph(pages=[_http(401)]), 'c-401')
    assert FailureClass.AUTH in result.failures and _cursor_text(ledger) == _mcursor('t1')


@pytest.mark.parametrize('bad', ['not json', json.dumps({'v': 1, 'kind': 'graph', 'folder': 'sentItems',
                                                          'link': _mlink('x')}),
                                 json.dumps({'v': 1, 'kind': 'graph', 'folder': 'inbox',
                                             'link': 'https://evil.example.test/steal'})])
def test_graph_mail_unusable_or_foreign_cursor_is_never_called(gmail_graph_run, bad):
    ledger, go = gmail_graph_run
    go(_Graph([_delta(_mail('m1'), token=_mlink('t1'))]), 'c-1')
    ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=bad)
    api = _Graph()
    assert go(api, 'c-bad').status is RunStatus.DEGRADED
    assert not api.url_calls and not api.path_calls and _cursor_text(ledger) is None


def test_graph_mail_before_option_stays_windowed_without_cursor(gmail_graph_run):
    ledger, go = gmail_graph_run
    api = _Graph([{'value': [_mail('m1')]}])
    assert go(api, 'c-before', before='2026-12-01T00:00:00').status is RunStatus.COMPLETE
    assert api.path_calls[0][1] == '/me/mailFolders/inbox/messages' and _cursor_text(ledger) is None


def test_graph_mail_windowed_listing_failure_or_cap_is_not_complete(gmail_graph_run):
    ledger, go = gmail_graph_run
    failing = _Graph([_http(503)])
    assert go(failing, 'c-fail', sync_changes=False).status is RunStatus.DEGRADED
    capped = _Graph([{'value': [_mail('m1'), _mail('m2')]}])
    assert go(capped, 'c-cap', sync_changes=False, max_results=1).status is RunStatus.DEGRADED
    more = _Graph([{'value': [_mail('m1')], '@odata.nextLink': f'{GRAPH}/n'}])
    assert go(more, 'c-more', sync_changes=False, max_results=1).status is RunStatus.DEGRADED
    whole = _Graph([{'value': [_mail('m3')]}])
    assert go(whole, 'c-ok', sync_changes=False).status is RunStatus.COMPLETE


def test_graph_mail_sync_option_validation():
    import connectors.msgraph_email as mod
    base = dict(since='2026-10-01T00:00:00Z', auth_context={'access_token': 'x'})
    with pytest.raises(ValueError, match='boolean'):
        list(mod.fetch(sync_changes='yes', **base))
    with pytest.raises(ValueError, match='sync_cursor'):
        list(mod.fetch(sync_changes=False, sync_cursor='x', **base))


# ── IMAP UIDVALIDITY + UID cursor ────────────────────────────────────────────────────────────────────────

def _raw(uid, body='Hello', date='Wed, 14 Oct 2026 09:00:00 +0000'):
    return (f'From: School <school@example.test>\r\nTo: family@example.test\r\nSubject: Note {uid}\r\n'
            f'Date: {date}\r\nMessage-ID: <{uid}@example.test>\r\n\r\n{body}\r\n').encode()


class _Imap:
    """One fake mailbox: ``uids`` maps UID -> raw bytes.  A class-level script drives each connection."""

    def __init__(self, uids, uidvalidity=7, select_status='OK', search_status='OK', fetch_status='OK',
                 untagged=True):
        self.uids, self.uidvalidity = dict(uids), uidvalidity
        self.select_status, self.search_status, self.fetch_status = select_status, search_status, fetch_status
        self.untagged, self.searches = untagged, []

    def factory(self, *_a, **_k):
        return self

    def login(self, *_a):
        return 'OK', []

    def select(self, *_a, **_k):
        return self.select_status, [b'1']

    def response(self, name):
        if not self.untagged:
            return name, [None]
        value = {'UIDVALIDITY': self.uidvalidity, 'UIDNEXT': max(self.uids, default=0) + 1}[name]
        return name, [str(value).encode()]

    def uid(self, command, *args):
        if command == 'SEARCH':
            criteria = args[1]
            self.searches.append(criteria)
            if self.search_status != 'OK':
                return self.search_status, []
            if criteria.startswith('UID '):
                low = int(criteria.split()[1].split(':')[0])
                found = [u for u in self.uids if u >= low] or ([max(self.uids)] if self.uids else [])
            else:
                found = list(self.uids)
            return 'OK', [' '.join(str(u) for u in sorted(found)).encode()]
        if self.fetch_status != 'OK':
            return self.fetch_status, []
        data = []
        for u in args[0].split(','):
            data.extend([(f'{u} (RFC822'.encode(), self.uids[int(u)]), b')'])
        return 'OK', data

    def logout(self):
        return 'BYE', []


@pytest.fixture
def imap_run(run_source, monkeypatch):
    import connectors.imap_email as mod
    ledger, run = run_source

    def go(box, cid, **extra):
        monkeypatch.setattr(mod.imaplib, 'IMAP4_SSL', box.factory)
        return run(mod, 'icloud_email', {'sync_changes': True, **extra}, cid)
    return ledger, go


IMAP_CURSOR = '{"folder":"INBOX","kind":"imap","last_uid":%d,"uidvalidity":%d,"v":1}'


def test_imap_window_run_stores_uidvalidity_and_the_last_uid_taken_at_select(imap_run):
    ledger, go = imap_run
    box = _Imap({1: _raw(1), 2: _raw(2)})
    assert go(box, 'c-1').status is RunStatus.COMPLETE
    assert box.searches[0].startswith('SINCE ') and _cursor_text(ledger) == IMAP_CURSOR % (2, 7)
    assert _signals(ledger) == ['1', '2']


def test_imap_delta_reads_only_higher_uids_and_never_the_boundary_message_twice(imap_run):
    ledger, go = imap_run
    go(_Imap({1: _raw(1), 2: _raw(2)}), 'c-1')
    box = _Imap({1: _raw(1), 2: _raw(2), 3: _raw(3), 4: _raw(4)})
    assert go(box, 'c-2').status is RunStatus.COMPLETE
    assert box.searches == ['UID 3:*'] and _signals(ledger) == ['1', '2', '3', '4']
    assert _cursor_text(ledger) == IMAP_CURSOR % (4, 7)
    quiet = _Imap({1: _raw(1), 2: _raw(2), 3: _raw(3), 4: _raw(4)})
    assert go(quiet, 'c-3').status is RunStatus.COMPLETE and _cursor_text(ledger) == IMAP_CURSOR % (4, 7)


def test_imap_uidvalidity_change_resets_and_keeps_evidence(imap_run):
    ledger, go = imap_run
    go(_Imap({1: _raw(1, MSG)}), 'c-1')
    facts = _fact_rows(ledger)
    assert facts
    box = _Imap({1: _raw(9)}, uidvalidity=8)
    assert go(box, 'c-2').status is RunStatus.DEGRADED
    assert _cursor_text(ledger) is None and _signals(ledger) == ['1'] and _fact_rows(ledger) == facts
    assert not box.searches                                   # no UID of the old numbering is trusted
    again = _Imap({1: _raw(9)}, uidvalidity=8)
    go(again, 'c-3')
    assert again.searches[0].startswith('SINCE ') and _cursor_text(ledger) == IMAP_CURSOR % (1, 8)


def test_imap_vanished_mail_is_not_observable_and_changes_nothing(imap_run):
    ledger, go = imap_run
    go(_Imap({1: _raw(1, MSG), 2: _raw(2)}), 'c-1')
    facts = _fact_rows(ledger)
    go(_Imap({2: _raw(2)}), 'c-2')                            # UID 1 was expunged
    assert _signals(ledger) == ['1', '2'] and _fact_rows(ledger) == facts and _signal_state(ledger, '1') != 'excluded'


@pytest.mark.parametrize('bad', ['not json', '{"v":2}', json.dumps({'v': 1, 'kind': 'imap', 'folder': 'Junk',
                                                                     'uidvalidity': 7, 'last_uid': 1})])
def test_imap_unusable_or_other_folder_cursor_resets(imap_run, bad):
    ledger, go = imap_run
    go(_Imap({1: _raw(1)}), 'c-1')
    ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=bad)
    assert go(_Imap({1: _raw(1)}), 'c-bad').status is RunStatus.DEGRADED and _cursor_text(ledger) is None


@pytest.mark.parametrize('box', [
    {'select_status': 'NO'}, {'search_status': 'NO'}, {'fetch_status': 'NO'}], ids=['select', 'search', 'fetch'])
def test_imap_failures_are_never_complete_and_keep_the_old_cursor(imap_run, box):
    ledger, go = imap_run
    go(_Imap({1: _raw(1)}), 'c-1')
    assert go(_Imap({1: _raw(1), 2: _raw(2)}, **box), 'c-bad').status is RunStatus.DEGRADED
    assert _cursor_text(ledger) == IMAP_CURSOR % (1, 7)


def test_imap_more_new_mail_than_the_cap_is_truncation_without_a_cursor_advance(imap_run):
    ledger, go = imap_run
    go(_Imap({1: _raw(1)}), 'c-1')
    assert go(_Imap({n: _raw(n) for n in range(1, 6)}), 'c-cap', max_results=2).status is RunStatus.DEGRADED
    assert _cursor_text(ledger) == IMAP_CURSOR % (1, 7)


def test_imap_unreadable_message_is_a_gap_not_a_quiet_success(imap_run):
    ledger, go = imap_run
    box = _Imap({1: _raw(1)})
    import connectors.imap_email as mod
    original = mod._parse_imap_message
    mod._parse_imap_message = lambda *a: None
    try:
        assert go(box, 'c-1').status is RunStatus.DEGRADED
    finally:
        mod._parse_imap_message = original
    assert _cursor_text(ledger) is None


def test_imap_server_without_uid_numbers_stays_windowed_with_no_cursor(imap_run):
    ledger, go = imap_run
    assert go(_Imap({1: _raw(1)}, untagged=False), 'c-1').status is RunStatus.COMPLETE
    assert _cursor_text(ledger) is None


def test_imap_old_message_is_filtered_by_exact_time_only_in_windowed_mode(imap_run):
    ledger, go = imap_run
    old = _raw(1, date='Mon, 01 Jan 2024 09:00:00 +0000')
    go(_Imap({1: old, 2: _raw(2)}), 'c-1')
    assert _signals(ledger) == ['2']


def test_imap_sync_option_validation():
    import connectors.imap_email as mod
    base = dict(since='2026-10-01T00:00:00Z', auth_context={'apple_id': 'a', 'app_password': 'p'})
    with pytest.raises(ValueError, match='boolean'):
        list(mod.fetch(sync_changes='yes', **base))
    with pytest.raises(ValueError, match='sync_cursor'):
        list(mod.fetch(sync_changes=False, sync_cursor='x', **base))


# ── Runtime hand-off of committed cursors ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(('scope', 'cursor', 'expected_kw', 'expected'), [
    ('mailbox', '{"opaque":true}', 'sync_cursor', '{"opaque":true}'),
    ('discovered_calendars', json.dumps({'version': 1, 'collections': {'https://x/y/': 'tok'}}), 'sync_tokens',
     {'https://x/y/': 'tok'}),
    ('configured_calendars', json.dumps({'version': 1, 'collections': {'cal1': 'tok'}}), 'sync_tokens',
     {'cal1': 'tok'}),
])
def test_runtime_hands_each_handler_kind_its_committed_cursor(env, scope, cursor, expected_kw, expected):  # noqa: F811
    ledger, _, _, _ = env
    ledger.set_coverage('src', 'acct-a', 'complete', cursor_generation=cursor)
    calls = []
    handler = SimpleNamespace(SYNC_TOKEN_SCOPE=scope, fetch=lambda **kw: (calls.append(kw), iter(()))[1])
    config = SimpleNamespace(sources={'src': {'connector': 'whatever', 'account_scope': 'acct-a', 'subjects': ['kid1'],
        'kwargs': {'sync_changes': True, 'calendar_ids': ['cal1']}}}, subjects=['kid1'],
        consent_required=frozenset(), viewer='ved')
    fetchers = runtime.build_fetchers(config, '2026-10-01T00:00:00Z', lambda name: handler, lambda name: {},
                                      {'src': True}, ledger)
    fetchers['src']()
    assert calls[0][expected_kw] == expected


def test_runtime_rejects_a_collection_cursor_for_an_unconfigured_calendar_only_when_calendars_are_configured(env):  # noqa: F811
    ledger, _, _, _ = env
    ledger.set_coverage('src', 'acct-a', 'complete',
                        cursor_generation=json.dumps({'version': 1, 'collections': {'other': 'tok'}}))
    handler = SimpleNamespace(SYNC_TOKEN_SCOPE='configured_calendars', fetch=lambda **kw: iter(()))
    config = SimpleNamespace(sources={'src': {'connector': 'c', 'account_scope': 'acct-a', 'subjects': ['kid1'],
        'kwargs': {'sync_changes': True, 'calendar_ids': ['cal1']}}}, subjects=['kid1'],
        consent_required=frozenset(), viewer='ved')
    result = runtime.build_fetchers(config, '2026-10-01T00:00:00Z', lambda n: handler, lambda n: {}, {'src': True},
                                    ledger)['src']()
    assert 'collection cursor requires explicit reset' in result.blind_spots


def test_runtime_does_not_hand_a_cursor_to_a_source_that_has_not_opted_in(env):  # noqa: F811
    ledger, _, _, _ = env
    ledger.set_coverage('src', 'acct-a', 'complete', cursor_generation='{"opaque":true}')
    calls = []
    handler = SimpleNamespace(SYNC_TOKEN_SCOPE='mailbox', fetch=lambda **kw: (calls.append(kw), iter(()))[1])
    config = SimpleNamespace(sources={'src': {'connector': 'c', 'account_scope': 'acct-a', 'subjects': ['kid1'],
        'kwargs': {}}}, subjects=['kid1'], consent_required=frozenset(), viewer='ved')
    runtime.build_fetchers(config, '2026-10-01T00:00:00Z', lambda n: handler, lambda n: {}, {'src': True}, ledger)['src']()
    assert 'sync_cursor' not in calls[0] and 'sync_tokens' not in calls[0]


def test_caldav_cursor_without_a_timezone_or_with_bad_members_is_unusable(dav_run):
    ledger, go = dav_run
    url = 'https://caldav.example/home/'
    for n, bad in enumerate((
            {'v': 1, 'token': 't', 'through': '2999-01-01T00:00:00', 'members': {}},
            {'v': 1, 'token': 'fake-1', 'through': '2999-01-01T00:00:00+00:00', 'members': {}},
            {'v': 1, 'token': 't', 'through': '2999-01-01T00:00:00+00:00', 'members': {'h': 'not a list'}})):
        ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=json.dumps(
            {'version': 1, 'collections': {url: json.dumps(bad)}}))
        assert go([_DavCal()], f'c-bad{n}').status is RunStatus.DEGRADED
        assert ledger.get_cursor('calendar', 'acct-a') is None


def test_caldav_unparseable_event_in_a_full_listing_fails_the_scope(dav_run):
    ledger, go = dav_run
    cal = _DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a'), SimpleNamespace(url='https://x/bad.ics')]])
    assert go([cal], 'c-bad').status is RunStatus.DEGRADED and ledger.get_cursor('calendar', 'acct-a') is None


def test_caldav_listed_event_without_an_href_cannot_be_tracked_so_the_scope_fails(dav_run):
    ledger, go = dav_run
    nameless = _ev_ics('a')
    nameless.url = ''
    assert go([_DavCal(syncs=[_Sync('tok-1')], searches=[[nameless]])], 'c-nohref').status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') is None


def test_caldav_cancelled_event_in_a_full_listing_is_an_explicit_cancellation_and_not_a_member(dav_run):
    ledger, go = dav_run
    go([_DavCal(syncs=[_Sync('tok-1')], searches=[[_ev_ics('a'), _ev_ics('b', 16)]])], 'c-1')
    go([_DavCal(syncs=[RuntimeError('<valid-sync-token/>')])], 'c-reset')
    go([_DavCal(syncs=[_Sync('tok-2')], searches=[[_ev_ics('a'), _Member('b.ics', _ics('b', 16, status='CANCELLED'))]])],
       'c-full')
    assert len(_calendar_ids(ledger)) == 1 and list(_dav_cursor(ledger)['members']) == ['https://caldav.example/cal/a.ics']


def test_graph_cursor_without_a_timezone_or_with_extra_keys_is_unusable(graph_run):
    ledger, go = graph_run
    for n, bad in enumerate((
            {'v': 1, 'link': _link('x'), 'through': '2999-01-01T00:00:00'},
            {'v': 1, 'link': _link('x'), 'through': '2999-01-01T00:00:00Z', 'extra': 1})):
        ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=json.dumps(
            {'version': 1, 'collections': {'cal1': json.dumps(bad)}}))
        assert go(_Graph(), f'c-bad{n}').status is RunStatus.DEGRADED
        assert ledger.get_cursor('calendar', 'acct-a') is None


def test_graph_delta_page_that_is_not_a_list_fails_and_status_is_read_from_the_error_text(graph_run):
    ledger, go = graph_run
    assert go(_Graph([{'value': 'nope'}]), 'c-1').status is RunStatus.DEGRADED
    assert go(_Graph([RuntimeError('401 Unauthorized - token may be expired')]), 'c-2').status is RunStatus.DEGRADED


def test_graph_mail_status_is_read_from_the_error_text_and_a_bad_page_fails(gmail_graph_run):
    ledger, go = gmail_graph_run
    assert go(_Graph([{'value': 'nope'}]), 'c-1').status is RunStatus.DEGRADED
    result = go(_Graph([RuntimeError('401 Unauthorized - token may be expired')]), 'c-2')
    assert FailureClass.AUTH in result.failures


def test_gmail_history_page_that_is_not_a_list_fails_and_a_garbled_entry_is_ignored(gmail_run):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    assert go(_Gmail(history=[{'history': 'nope'}]), 'c-bad').status is RunStatus.DEGRADED
    ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=GMAIL_CURSOR % '100')
    api = _Gmail(history=[{'history': ['junk', {'messagesAdded': ['junk', {'message': {}}]}], 'historyId': '140'}])
    go(api, 'c-junk')
    assert _cursor_text(ledger) == GMAIL_CURSOR % '140'


def test_gmail_unparseable_new_message_is_a_gap(gmail_run):
    ledger, go = gmail_run
    go(_Gmail('100', listing=['m1'], messages={'m1': _gmsg('m1')}), 'c-1')
    api = _Gmail(history=[{'history': [{'messagesAdded': [{'message': {'id': 'bad'}}]}], 'historyId': '150'}],
                 messages={'bad': {'labelIds': None, 'payload': 'garbage'}})
    assert go(api, 'c-bad').status is RunStatus.DEGRADED and _cursor_text(ledger) == GMAIL_CURSOR % '100'
