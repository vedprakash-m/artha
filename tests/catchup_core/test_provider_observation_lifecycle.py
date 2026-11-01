"""Complete provider observation lifecycle tests (Row 4, RA-01, RA-08, RA-17, re-artha §6.7, §7.10).

Covers:
- Opaque provider cursor persists only after scoped local evidence commit
- Failure/crash before local commit leaves previous cursor unchanged
- Provider 410 reset clears cursor and demands full resync without gap-free quiet claim
- Deleted calendar collection purges and cancels its records
- Recurrence removal cancels specific instance while leaving master intact
- Delayed/out-of-window changes via delta cursors are accepted and preserved
- Mid-stream page failure does not advance cursor
- Revoked OAuth access retains previous cursor and existing evidence
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from catchup_core import coordinator as co
from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import (
    CatchupRequest,
    CoverageStatus,
    ExecutionMode,
    FailureClass,
    RunStatus,
    Stage,
)
from catchup_core.triage import CalendarEvent

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
NOW = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)
PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "kids_school.yaml")
MSG = "Families: SAT registration closes October 14. Please register online."
QUOTE = "registration closes October 14"


def dummy_extract(prompt: str) -> str:
    message = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
    items = []
    if QUOTE in message:
        i = message.index(QUOTE)
        items.append({
            "predicate": "exam_registration_deadline",
            "value": "2026-10-14",
            "subject_id": "kid1",
            "quote": QUOTE,
            "span": [i, i + len(QUOTE)],
        })
    return json.dumps({"complete": True, "candidates": items})


@pytest.fixture
def env(tmp_path):
    ledger = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    published: list[tuple[str, int]] = []

    def make(fetchers, **kw):
        all_fetchers = {
            "school_email": lambda: co.ConnectorFetch("school_email", "acct-a"),
            "calendar": lambda: co.ConnectorFetch("calendar", "acct-a"),
        }
        all_fetchers.update(fetchers)
        return co.Deps(
            ledger=ledger,
            pack=PACK,
            blobs=blobs,
            id_key=KEY,
            fetchers=all_fetchers,
            extractor=dummy_extract,
            subjects=["kid1", "kid2"],
            viewer="ved",
            now=lambda: NOW,
            source_class={"school_email": "school_email", "calendar": "calendar"},
            publisher=lambda cid, text, v: published.append((cid, v)),
            coverage_line=lambda: "Coverage: test.",
            **kw,
        )

    yield ledger, blobs, make, published
    ledger.close()


def req(cid: str = "c1") -> CatchupRequest:
    return CatchupRequest(cid, actor="ved", channel="full", mode=ExecutionMode.AUTHORITATIVE, host="localhost")


def test_opaque_provider_cursor_persists_only_after_scoped_local_commit(env):
    ledger, _, make, _ = env
    rec = co.ConnectorRecord("m1", MSG, provider_version="v1", occurred_at="2026-10-14T09:00:00Z")
    committed_hooks = []

    def fetch_email():
        return co.ConnectorFetch(
            "school_email",
            "acct-a",
            records=(rec,),
            status=CoverageStatus.COMPLETE,
            cursor="delta-token-001",
            commit=lambda: committed_hooks.append("hook_called"),
        )

    deps = make({"school_email": fetch_email})
    result = co.run_catchup(req("run-1"), deps)

    assert result.status == RunStatus.COMPLETE
    assert committed_hooks == ["hook_called"]
    assert ledger.get_cursor("school_email", "acct-a") == "delta-token-001"
    row = ledger.get_coverage("school_email", "acct-a")
    assert row["status"] == "complete"
    assert row["cursor_generation"] == "delta-token-001"


def test_crash_before_local_commit_leaves_previous_cursor_unchanged(env):
    ledger, _, make, _ = env
    ledger.set_coverage("school_email", "acct-a", "complete", cursor_generation="previous-cursor-999")
    assert ledger.get_cursor("school_email", "acct-a") == "previous-cursor-999"

    rec = co.ConnectorRecord("m2", MSG, provider_version="v2")

    def fetch_email():
        return co.ConnectorFetch(
            "school_email",
            "acct-a",
            records=(rec,),
            status=CoverageStatus.COMPLETE,
            cursor="uncommitted-new-cursor-1000",
        )

    def fault_at_fetch(stage: Stage):
        if stage == Stage.FETCH:
            raise co.StageFault("Simulated process crash during fetch commit")

    deps = make({"school_email": fetch_email}, fault=fault_at_fetch)
    with pytest.raises(co.StageFault):
        co.run_catchup(req("run-crash"), deps)

    # The cursor must NOT have advanced to the new cursor!
    assert ledger.get_cursor("school_email", "acct-a") == "previous-cursor-999"


def test_local_evidence_failure_rolls_back_batch_and_does_not_acknowledge(env, monkeypatch):
    ledger, _, make, _ = env
    ledger.set_coverage('school_email', 'acct-a', 'complete', cursor_generation='old')
    acknowledgements = []
    original = ledger.upsert_signal
    def fail_second(value):
        if value.upstream_id == 'second':
            raise lg.LedgerError('synthetic local write failure')
        return original(value)
    monkeypatch.setattr(ledger, 'upsert_signal', fail_second)
    fetch = co.ConnectorFetch('school_email', 'acct-a', records=(
        co.ConnectorRecord('first', MSG), co.ConnectorRecord('second', MSG)), cursor='new',
        commit=lambda: acknowledgements.append(True))
    result = co.run_catchup(req('batch-rollback'), make({'school_email': lambda: fetch}))
    assert result.status is RunStatus.BLOCKED
    assert ledger.conn.execute('SELECT count(*) FROM signals').fetchone()[0] == 0
    assert ledger.get_cursor('school_email', 'acct-a') == 'old'
    assert acknowledgements == []


def test_bundle_acknowledgement_runs_after_local_commit(env):
    import sqlite3
    from contextlib import closing
    ledger, _, make, _ = env
    seen = []
    def acknowledge():
        with closing(sqlite3.connect(ledger._path)) as other:
            seen.append(other.execute('SELECT count(*) FROM signals').fetchone()[0])
    fetch = co.ConnectorFetch('school_email', 'acct-a', records=(co.ConnectorRecord('first', MSG),),
                              commit=acknowledge)
    co.run_catchup(req('ack-after-commit'), make({'school_email': lambda: fetch}))
    assert seen == [1]


def test_runtime_loads_committed_google_collection_tokens_without_changing_them(env):
    from types import SimpleNamespace
    from catchup_core import runtime
    ledger, _, _, _ = env
    cursor = json.dumps({'version': 1, 'collections': {'primary': 'opaque token /+=='}})
    ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=cursor)
    calls = []
    def fetch(**kwargs):
        calls.append(kwargs)
        return iter(())
    config = SimpleNamespace(sources={'calendar': {'connector': 'google_calendar', 'account_scope': 'acct-a',
        'subjects': ['kid1'], 'kwargs': {'sync_changes': True, 'calendar_ids': ['primary']}}},
        subjects=['kid1'], consent_required=frozenset(), viewer='ved')
    fetchers = runtime.build_fetchers(config, '2026-10-01T00:00:00Z',
        lambda name: SimpleNamespace(fetch=fetch), lambda name: {}, {'calendar': True}, ledger)
    fetchers['calendar']()
    assert calls[0]['sync_tokens'] == {'primary': 'opaque token /+=='}


def test_provider_410_reset_clears_cursor_and_demands_full_resync_without_gap_free_claim(env):
    ledger, _, make, _ = env
    ledger.set_coverage("school_email", "acct-a", "complete", cursor_generation="stale-sync-token")
    assert ledger.get_cursor("school_email", "acct-a") == "stale-sync-token"

    def fetch_with_410_reset():
        return co.ConnectorFetch(
            "school_email",
            "acct-a",
            records=(),
            status=CoverageStatus.DEGRADED,
            error=FailureClass.PROVIDER,
            blind_spots=("provider cursor reset: full resync required",),
            cursor_reset=True,
        )

    deps = make({"school_email": fetch_with_410_reset})
    result = co.run_catchup(req("run-reset"), deps)

    assert result.status == RunStatus.DEGRADED
    # Cursor is cleared to NULL
    assert ledger.get_cursor("school_email", "acct-a") is None
    cov = ledger.get_coverage("school_email", "acct-a")
    assert cov["status"] == "degraded"
    assert "provider cursor reset: full resync required" in cov["blind_spots"]


def test_deleted_calendar_collection_purges_and_cancels_its_records(env):
    ledger, _, make, _ = env

    start = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
    ev_primary = CalendarEvent('calendar:["primary","ev1"]', start, end, False, "school")
    ev_secondary = CalendarEvent('calendar:["secondary","ev2"]', start, end, False, "other")

    rec1 = co.ConnectorRecord('calendar:["primary","ev1"]', "Event 1", calendar=ev_primary)
    rec2 = co.ConnectorRecord('calendar:["secondary","ev2"]', "Event 2", calendar=ev_secondary)

    # First fetch: both calendars exist and populate calendar_records
    def fetch_initial():
        return co.ConnectorFetch("calendar", "acct-a", records=(rec1, rec2), status=CoverageStatus.COMPLETE)

    deps1 = make({"calendar": fetch_initial})
    co.run_catchup(req("run-init"), deps1)

    cal_rows = ledger.conn.execute("SELECT upstream_id FROM calendar_records ORDER BY upstream_id").fetchall()
    assert len(cal_rows) == 2

    # Second fetch: user removed 'secondary' calendar upstream, so handler reports deleted_collections=('secondary',)
    def fetch_with_deleted_collection():
        return co.ConnectorFetch(
            "calendar",
            "acct-a",
            records=(rec1,),
            deleted_collections=("secondary",),
            status=CoverageStatus.COMPLETE,
        )

    deps2 = make({"calendar": fetch_with_deleted_collection})
    co.run_catchup(req("run-del-coll"), deps2)

    cal_after = ledger.conn.execute("SELECT upstream_id FROM calendar_records ORDER BY upstream_id").fetchall()
    assert len(cal_after) == 1
    assert cal_after[0][0] == 'calendar:["primary","ev1"]'

    # The purged event's source_heads entry has NULL signal_id (cancelled)
    head = ledger.conn.execute(
        "SELECT signal_id FROM source_heads WHERE upstream_id=?",
        ('calendar:["secondary","ev2"]',),
    ).fetchone()
    assert head[0] is None


def test_recurrence_removal_cancels_specific_instance_leaving_master_intact(env):
    ledger, _, make, _ = env

    start1 = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end1 = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
    start2 = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    end2 = datetime(2026, 10, 22, 11, 0, tzinfo=timezone.utc)

    inst1 = CalendarEvent('calendar:["primary","recur_1_20261015"]', start1, end1, False, "school")
    inst2 = CalendarEvent('calendar:["primary","recur_1_20261022"]', start2, end2, False, "school")

    rec1 = co.ConnectorRecord('calendar:["primary","recur_1_20261015"]', "Math Class Inst 1", calendar=inst1)
    rec2 = co.ConnectorRecord('calendar:["primary","recur_1_20261022"]', "Math Class Inst 2", calendar=inst2)

    deps1 = make({"calendar": lambda: co.ConnectorFetch("calendar", "acct-a", records=(rec1, rec2))})
    co.run_catchup(req("run-rec-init"), deps1)

    assert len(ledger.conn.execute("SELECT upstream_id FROM calendar_records").fetchall()) == 2

    # Provider reports inst1 cancelled (e.g. exception to recurring series)
    cancelled_id = 'calendar:["primary","recur_1_20261015"]'
    deps2 = make({"calendar": lambda: co.ConnectorFetch("calendar", "acct-a", records=(rec2,), cancelled=(cancelled_id,))})
    co.run_catchup(req("run-rec-cancel"), deps2)

    active_cals = [r[0] for r in ledger.conn.execute("SELECT upstream_id FROM calendar_records").fetchall()]
    assert active_cals == ['calendar:["primary","recur_1_20261022"]']

    # Cancelled instance is marked withdrawn in source_heads
    head = ledger.conn.execute("SELECT signal_id FROM source_heads WHERE upstream_id=?", (cancelled_id,)).fetchone()
    assert head[0] is None


def test_delayed_out_of_window_changes_are_accepted_and_recorded(env):
    ledger, _, make, _ = env

    old_dt = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)
    ev_old = CalendarEvent('calendar:["primary","historical-retro-fix"]', old_dt, old_dt, False, "school")
    rec_old = co.ConnectorRecord(
        'calendar:["primary","historical-retro-fix"]',
        "Historical Event Corrected",
        occurred_at="2020-01-01T12:00:00Z",
        calendar=ev_old,
    )

    deps = make({"calendar": lambda: co.ConnectorFetch("calendar", "acct-a", records=(rec_old,))})
    result = co.run_catchup(req("run-retro"), deps)

    assert result.status == RunStatus.COMPLETE
    found = ledger.conn.execute(
        "SELECT upstream_id, occurred_at FROM signals WHERE upstream_id=?",
        ('calendar:["primary","historical-retro-fix"]',),
    ).fetchone()
    assert found is not None
    assert found[1] == "2020-01-01T12:00:00Z"


def test_mid_stream_page_failure_does_not_advance_cursor(env):
    ledger, _, make, _ = env
    ledger.set_coverage("school_email", "acct-a", "complete", cursor_generation="stable-cursor-0")

    rec1 = co.ConnectorRecord("m1", MSG)

    # Multi-page fetch: page 1 yielded rec1, but page 2 failed, so status is DEGRADED
    def fetch_broken_pagination():
        return co.ConnectorFetch(
            "school_email",
            "acct-a",
            records=(rec1,),
            status=CoverageStatus.DEGRADED,
            error=FailureClass.NETWORK,
            blind_spots=("page 2 failed: ReadTimeout",),
            cursor="page-2-unverified-cursor",
        )

    deps = make({"school_email": fetch_broken_pagination})
    result = co.run_catchup(req("run-mid-page-fail"), deps)

    assert result.status == RunStatus.DEGRADED
    # Page 1 record is captured as partial evidence
    assert ledger.conn.execute("SELECT COUNT(*) FROM signals WHERE upstream_id='m1'").fetchone()[0] == 1
    # BUT cursor has NOT advanced to page 2's unverified cursor!
    assert ledger.get_cursor("school_email", "acct-a") == "stable-cursor-0"
    cov = ledger.get_coverage("school_email", "acct-a")
    assert cov["status"] == "degraded"


def test_revoked_oauth_access_retains_previous_cursor_and_existing_evidence(env):
    ledger, _, make, _ = env
    rec = co.ConnectorRecord("m1", MSG)

    # First fetch succeeds and establishes baseline evidence and cursor
    deps1 = make({"school_email": lambda: co.ConnectorFetch(
        "school_email", "acct-a", records=(rec,), cursor="oauth-cursor-established"
    )})
    co.run_catchup(req("run-auth-ok"), deps1)
    assert ledger.get_cursor("school_email", "acct-a") == "oauth-cursor-established"
    assert ledger.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1

    # Next fetch fails with invalid_grant / 401
    def fetch_revoked():
        return co.ConnectorFetch(
            "school_email",
            "acct-a",
            records=(),
            status=CoverageStatus.UNAVAILABLE,
            error=FailureClass.AUTH,
            blind_spots=("fetch failed: invalid_grant: Token revoked",),
        )

    deps2 = make({"school_email": fetch_revoked})
    co.run_catchup(req("run-auth-revoked"), deps2)

    # Cursor is retained (not wiped or moved forward) so re-auth can resume
    assert ledger.get_cursor("school_email", "acct-a") == "oauth-cursor-established"
    # Historical signals are NOT erased
    assert ledger.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    cov = ledger.get_coverage("school_email", "acct-a")
    assert cov["status"] == "unavailable"


# ── Calendar chain: provider-shaped service -> real handler -> validator -> adapter -> ledger ──────────

class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _GoogleService:
    """Scripted Google Calendar service.  ``script[calendar]`` is a list of responses, one per events.list call;
    an Exception instance is raised.  ``deleted_entries`` feeds calendarList.list(showDeleted=True)."""

    def __init__(self, script, deleted_entries=None, list_error=None):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.deleted_entries = deleted_entries or []
        self.list_error = list_error
        self.list_calls = []

    def calendars(self):
        return type('C', (), {'get': lambda _s, calendarId: _Exec(lambda: {'summary': calendarId})})()

    def calendarList(self):  # noqa: N802 - provider method name
        outer = self

        class _L:
            def list(self, **kwargs):
                outer.list_calls.append(kwargs)

                def run():
                    if outer.list_error is not None:
                        raise outer.list_error
                    return {'items': outer.deleted_entries}
                return _Exec(run)
        return _L()

    def events(self):
        outer = self

        class _E:
            def list(self, calendarId, pageToken=None, **kwargs):
                outer.calls.append({'calendarId': calendarId, 'pageToken': pageToken, **kwargs})

                def run():
                    item = outer.script[calendarId].pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                return _Exec(run)
        return _E()


def _http(status):
    from types import SimpleNamespace
    exc = RuntimeError('synthetic provider text that must not be disclosed')
    exc.resp = SimpleNamespace(status=status)
    return exc


def _g_event(event_id, day=15, status='confirmed', summary=None):
    if status == 'cancelled':
        return {'id': event_id, 'status': 'cancelled'}
    return {'id': event_id, 'status': 'confirmed', 'summary': summary or f'Class {event_id}', 'etag': f'"{event_id}-1"',
            'start': {'dateTime': f'2026-10-{day:02d}T10:00:00-07:00'},
            'end': {'dateTime': f'2026-10-{day:02d}T11:00:00-07:00'}}


@pytest.fixture
def chain(env, monkeypatch, tmp_path):
    """Run Catch-up through the real runtime fetcher, Google handler, validator and adapter."""
    import sys
    import types
    from types import SimpleNamespace

    import lib.retry as retry
    from catchup_core import runtime
    import connectors.google_calendar as gcal

    ledger, _, make, _ = env
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    monkeypatch.setattr(retry, 'with_retry', lambda fn, **kw: fn())
    state = {}

    def run(service, cid, calendar_ids=('primary',)):
        module = types.ModuleType('google_auth')
        module.build_service = lambda *a, **k: service
        monkeypatch.setitem(sys.modules, 'google_auth', module)
        config = SimpleNamespace(sources={'calendar': {'connector': 'google_calendar', 'account_scope': 'acct-a',
            'subjects': ['kid1'], 'kwargs': {'sync_changes': True, 'calendar_ids': list(calendar_ids)}}},
            subjects=['kid1'], consent_required=frozenset(), viewer='ved')
        fetchers = runtime.build_fetchers(config, '2026-10-01T00:00:00Z', lambda name: gcal,
                                          lambda name: {}, {'calendar': True}, ledger)
        return co.run_catchup(req(cid), make({'calendar': fetchers['calendar']}))

    state['run'] = run
    yield ledger, run


def _calendar_ids(ledger):
    return sorted(r[0] for r in ledger.conn.execute('SELECT upstream_id FROM calendar_records'))


def _cursor(ledger):
    return json.loads(ledger.get_cursor('calendar', 'acct-a'))['collections']


def test_chain_initial_sync_persists_collection_token_then_delta_withdraws_one_recurrence_instance(chain):
    ledger, run = chain
    first = _GoogleService({'primary': [{'items': [_g_event('series_20261015'), _g_event('series_20261022', 22)],
                                         'nextSyncToken': 'tok/1+=='}]})
    assert run(first, 'c-init').status is RunStatus.COMPLETE
    assert _cursor(ledger) == {'primary': 'tok/1+=='}
    assert 'syncToken' not in first.calls[0]
    assert len(_calendar_ids(ledger)) == 2

    delta = _GoogleService({'primary': [{'items': [_g_event('series_20261015', status='cancelled')],
                                         'nextSyncToken': 'tok/2'}]})
    run(delta, 'c-delta')
    assert delta.calls[0]['syncToken'] == 'tok/1+=='
    assert _calendar_ids(ledger) == ['calendar:["primary","series_20261022"]']
    assert _cursor(ledger) == {'primary': 'tok/2'}


def test_chain_delayed_change_to_a_past_event_is_accepted_through_the_delta_feed(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [], 'nextSyncToken': 't1'}]}), 'c-1')
    late = _g_event('old', 1)
    late['start'] = {'dateTime': '2020-01-01T10:00:00-07:00'}
    late['end'] = {'dateTime': '2020-01-01T11:00:00-07:00'}
    run(_GoogleService({'primary': [{'items': [late], 'nextSyncToken': 't2'}]}), 'c-2')
    assert _calendar_ids(ledger) == ['calendar:["primary","old"]'] and _cursor(ledger) == {'primary': 't2'}


def test_chain_410_resets_cursor_then_full_resync_restores_a_cursor_and_withdraws_the_omitted_occurrence(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('keep'), _g_event('silent')], 'nextSyncToken': 't1'}]}), 'c-1')
    reset = run(_GoogleService({'primary': [_http(410)]}), 'c-410')
    assert reset.status is RunStatus.DEGRADED
    assert ledger.get_cursor('calendar', 'acct-a') is None
    assert 'provider cursor reset: full resync required' in ledger.get_coverage('calendar', 'acct-a')['blind_spots']
    assert len(_calendar_ids(ledger)) == 2          # a reset deletes nothing

    full = _GoogleService({'primary': [{'items': [_g_event('keep')], 'nextSyncToken': 't9'}]})
    run(full, 'c-full')
    assert 'syncToken' not in full.calls[0]         # full reconciliation, not a delta
    assert _cursor(ledger) == {'primary': 't9'}
    # A COMPLETE full listing withdraws the omitted occurrence from the calendar (owner ruling 1).
    assert _calendar_ids(ledger) == ['calendar:["primary","keep"]']


def _fact_rows(ledger):
    return [tuple(r) for r in ledger.conn.execute("SELECT fact_id,status FROM facts ORDER BY fact_id")]


def _signal_states(ledger, upstream):
    return [r[0] for r in ledger.conn.execute(
        "SELECT processing_state FROM signals WHERE upstream_id=?", (upstream,))]


def test_chain_full_resync_omission_withdraws_the_occurrence_only_and_facts_survive(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('keep'), _g_event('gone', summary=MSG)],
                                     'nextSyncToken': 't1'}]}), 'c-1')
    before = _fact_rows(ledger)
    assert before, 'the omitted event must have produced a Fact for this test to mean anything'
    run(_GoogleService({'primary': [_http(410)]}), 'c-410')
    run(_GoogleService({'primary': [{'items': [_g_event('keep')], 'nextSyncToken': 't2'}]}), 'c-full')
    assert _calendar_ids(ledger) == ['calendar:["primary","keep"]']
    assert _fact_rows(ledger) == before                      # Facts and obligations are never withdrawn
    assert 'excluded' not in _signal_states(ledger, 'calendar:["primary","gone"]')


def test_chain_omitted_occurrence_returns_when_the_provider_lists_it_again(chain):
    ledger, run = chain
    both = [_g_event('keep'), _g_event('flap')]
    run(_GoogleService({'primary': [{'items': both, 'nextSyncToken': 't1'}]}), 'c-1')
    run(_GoogleService({'primary': [_http(410)]}), 'c-410')
    run(_GoogleService({'primary': [{'items': [_g_event('keep')], 'nextSyncToken': 't2'}]}), 'c-2')
    run(_GoogleService({'primary': [_http(410)]}), 'c-410b')
    run(_GoogleService({'primary': [{'items': both, 'nextSyncToken': 't3'}]}), 'c-3')
    assert len(_calendar_ids(ledger)) == 2


@pytest.mark.parametrize('failure', ['second_page_fails', 'repeated_page', 'malformed_event'])
def test_chain_partial_or_failed_full_resync_withdraws_nothing(chain, failure):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a'), _g_event('b')], 'nextSyncToken': 't1'}]}), 'c-1')
    run(_GoogleService({'primary': [_http(410)]}), 'c-410')
    script = {
        'second_page_fails': [{'items': [_g_event('a')], 'nextPageToken': 'p2'}, _http(503)],
        'repeated_page': [{'items': [_g_event('a')], 'nextPageToken': 'p2'},
                          {'items': [], 'nextPageToken': 'p2'}],
        'malformed_event': [{'items': [_g_event('a'), {'summary': 'no id'}], 'nextSyncToken': 't9'}],
    }[failure]
    assert run(_GoogleService({'primary': script}), 'c-partial').status is RunStatus.DEGRADED
    assert len(_calendar_ids(ledger)) == 2


def test_chain_delta_run_never_withdraws_by_omission(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a'), _g_event('b')], 'nextSyncToken': 't1'}]}), 'c-1')
    run(_GoogleService({'primary': [{'items': [], 'nextSyncToken': 't2'}]}), 'c-delta')
    assert len(_calendar_ids(ledger)) == 2


def test_chain_one_calendar_failing_withdraws_nothing_in_the_other_calendar(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a')], 'nextSyncToken': 'p1'}],
                        'other': [{'items': [_g_event('x')], 'nextSyncToken': 'o1'}]}), 'c-1', ('primary', 'other'))
    run(_GoogleService({'primary': [_http(410)], 'other': [_http(410)]}), 'c-410', ('primary', 'other'))
    run(_GoogleService({'primary': [{'items': [], 'nextSyncToken': 'p2'}], 'other': [_http(503)]}),
        'c-mixed', ('primary', 'other'))
    assert len(_calendar_ids(ledger)) == 2          # the whole source degraded, so nothing is withdrawn


def test_chain_repeated_page_token_keeps_partial_evidence_and_old_cursor(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a')], 'nextSyncToken': 'stable'}]}), 'c-1')
    loop = _GoogleService({'primary': [
        {'items': [_g_event('b')], 'nextPageToken': 'p2', 'nextSyncToken': 'ignored'},
        {'items': [_g_event('c')], 'nextPageToken': 'p2', 'nextSyncToken': 'ignored'}]})
    assert run(loop, 'c-loop').status is RunStatus.DEGRADED
    assert _cursor(ledger) == {'primary': 'stable'}
    assert {'calendar:["primary","b"]', 'calendar:["primary","c"]'} <= set(_calendar_ids(ledger))


def test_chain_failed_second_page_does_not_advance_cursor(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [], 'nextSyncToken': 'stable'}]}), 'c-1')
    broken = _GoogleService({'primary': [{'items': [_g_event('p1')], 'nextPageToken': 'p2'}, _http(503)]})
    assert run(broken, 'c-page2').status is RunStatus.DEGRADED
    assert _cursor(ledger) == {'primary': 'stable'}
    assert _calendar_ids(ledger) == ['calendar:["primary","p1"]']


def test_chain_revoked_access_is_classified_as_auth_and_keeps_cursor_and_evidence(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a')], 'nextSyncToken': 'stable'}]}), 'c-1')
    result = run(_GoogleService({'primary': [_http(401)]}), 'c-401')
    assert result.status is RunStatus.DEGRADED
    assert FailureClass.AUTH in result.failures
    assert _cursor(ledger) == {'primary': 'stable'} and _calendar_ids(ledger) == ['calendar:["primary","a"]']
    assert 'synthetic provider text' not in str(ledger.get_coverage('calendar', 'acct-a')['blind_spots'])


def test_chain_not_found_calendar_is_not_a_deletion(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a')], 'nextSyncToken': 's'}],
                        'other': [{'items': [_g_event('x')], 'nextSyncToken': 'o'}]}), 'c-1', ('primary', 'other'))
    run(_GoogleService({'primary': [{'items': [], 'nextSyncToken': 's2'}], 'other': [_http(404)]}),
        'c-404', ('primary', 'other'))
    assert len(_calendar_ids(ledger)) == 2
    assert _cursor(ledger) == {'primary': 's', 'other': 'o'}


def test_chain_provider_deleted_marker_withdraws_the_collection_and_its_facts_only(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a')], 'nextSyncToken': 'p1'}],
                        'team_cal': [{'items': [_g_event('x')], 'nextSyncToken': 't1'}],
                        'teamXcal': [{'items': [_g_event('y')], 'nextSyncToken': 'y1'}]}),
        'c-1', ('primary', 'team_cal', 'teamXcal'))
    assert len(_calendar_ids(ledger)) == 3
    service = _GoogleService({'primary': [{'items': [], 'nextSyncToken': 'p2'}],
                              'teamXcal': [{'items': [], 'nextSyncToken': 'y2'}]},
                             deleted_entries=[{'id': 'team_cal', 'deleted': True}])
    result = run(service, 'c-del', ('primary', 'team_cal', 'teamXcal'))
    assert result.status is RunStatus.COMPLETE
    assert service.list_calls[0]['showDeleted'] is True
    assert _calendar_ids(ledger) == ['calendar:["primary","a"]', 'calendar:["teamXcal","y"]']
    assert [c['calendarId'] for c in service.calls] == ['primary', 'teamXcal']   # deleted collection not queried
    assert _cursor(ledger) == {'primary': 'p2', 'teamXcal': 'y2'}
    row = ledger.conn.execute("SELECT s.processing_state FROM signals s WHERE s.upstream_id=?",
                              ('calendar:["team_cal","x"]',)).fetchone()
    assert row is not None and row[0] == 'excluded'


def test_chain_deleted_collection_list_failure_degrades_without_deleting(chain):
    ledger, run = chain
    run(_GoogleService({'primary': [{'items': [_g_event('a')], 'nextSyncToken': 'p1'}]}), 'c-1')
    result = run(_GoogleService({'primary': [{'items': [], 'nextSyncToken': 'p2'}]}, list_error=_http(500)), 'c-2')
    assert result.status is RunStatus.DEGRADED
    assert _calendar_ids(ledger) == ['calendar:["primary","a"]'] and _cursor(ledger) == {'primary': 'p1'}


def test_collection_deletion_matches_exact_identity_not_sql_wildcards_or_json_escapes(env):
    ledger, _, make, _ = env
    start = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)
    ids_ = [co_id for co_id in (
        'calendar:' + json.dumps([cal, 'e'], separators=(',', ':')) for cal in ('a_b', 'axb', 'q"uote', 'é'))]
    recs = tuple(co.ConnectorRecord(i, i, calendar=CalendarEvent(i, start, start, False, 'other')) for i in ids_)
    co.run_catchup(req('c-1'), make({'calendar': lambda: co.ConnectorFetch('calendar', 'acct-a', records=recs)}))
    assert len(_calendar_ids(ledger)) == 4
    co.run_catchup(req('c-2'), make({'calendar': lambda: co.ConnectorFetch(
        'calendar', 'acct-a', deleted_collections=('a_b', 'q"uote', 'é'))}))
    assert _calendar_ids(ledger) == [ids_[1]]


@pytest.mark.parametrize('source', ['google_calendar', 'outlook_calendar_personal', 'icloud_calendar'])
def test_known_pipeline_calendar_records_without_date_iso_pass_the_validator_with_derived_date(source):
    """Regression for the 2026-09-01 and 2026-10-11 drops: native calendar records carry start, not date_iso."""
    from schemas.connector_record import validate_record
    record = {'id': 'e1', 'source': source, 'summary': 'Class', 'start': '2026-10-15T10:00:00-07:00',
              'end': '2026-10-15T11:00:00-07:00'}
    assert 'date_iso' not in record
    assert validate_record(record).date_iso == '2026-10-15T10:00:00-07:00'


def _cal_rec(cal, ev, start, end, all_day=False):
    ident = 'calendar:' + json.dumps([cal, ev], separators=(',', ':'))
    return co.ConnectorRecord(ident, ident, calendar=CalendarEvent(ident, start, end, all_day, 'other'))


def test_windowed_full_listing_only_judges_occurrences_wholly_inside_its_window(env):
    from datetime import date
    ledger, _, make, _ = env
    utc = timezone.utc
    recs = (_cal_rec('p', 'inside', datetime(2026, 10, 15, 10, tzinfo=utc), datetime(2026, 10, 15, 11, tzinfo=utc)),
            _cal_rec('p', 'before', datetime(2026, 9, 1, 10, tzinfo=utc), datetime(2026, 9, 1, 11, tzinfo=utc)),
            _cal_rec('p', 'edge', datetime(2026, 10, 20, 23, tzinfo=utc), datetime(2026, 10, 21, 2, tzinfo=utc)),
            _cal_rec('p', 'day', date(2026, 10, 16), date(2026, 10, 16), True),
            _cal_rec('p', 'edgeday', date(2026, 10, 10), date(2026, 10, 10), True),
            _cal_rec('q', 'other', datetime(2026, 10, 15, 10, tzinfo=utc), datetime(2026, 10, 15, 11, tzinfo=utc)))
    co.run_catchup(req('c-1'), make({'calendar': lambda: co.ConnectorFetch('calendar', 'acct-a', records=recs)}))
    assert len(_calendar_ids(ledger)) == 6
    listing = (('p', '2026-10-10T00:00:00+00:00', '2026-10-21T00:00:00+00:00'),)
    co.run_catchup(req('c-2'), make({'calendar': lambda: co.ConnectorFetch(
        'calendar', 'acct-a', full_listings=listing)}))
    left = {i.split(',')[0].split('"')[1] + ':' + i.split(',')[1].strip('"]') for i in _calendar_ids(ledger)}
    assert left == {'p:before', 'p:edge', 'p:edgeday', 'q:other'}   # only inside + day are withdrawn
    assert ledger.withdraw_unlisted_calendar_occurrences('calendar', 'acct-a', 'p', set()) == 3


def test_full_listing_on_a_degraded_fetch_is_ignored(env):
    ledger, _, make, _ = env
    rec = _cal_rec('p', 'a', datetime(2026, 10, 15, 10, tzinfo=timezone.utc), datetime(2026, 10, 15, 11, tzinfo=timezone.utc))
    co.run_catchup(req('c-1'), make({'calendar': lambda: co.ConnectorFetch('calendar', 'acct-a', records=(rec,))}))
    co.run_catchup(req('c-2'), make({'calendar': lambda: co.ConnectorFetch(
        'calendar', 'acct-a', status=CoverageStatus.DEGRADED, error=FailureClass.PROVIDER,
        full_listings=(('p', None, None),))}))
    assert len(_calendar_ids(ledger)) == 1


def test_caldav_mixed_delta_failure_commits_explicit_cancellation_without_advancing_cursor(env, monkeypatch, tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace
    from caldav.lib.error import NotFoundError
    from catchup_core import runtime
    import connectors.caldav_calendar as caldav

    ledger, _, make, _ = env
    collection = 'https://caldav.example/home/'
    recs = tuple(_cal_rec(collection, identity, NOW, NOW) for identity in ('removed', 'unlisted'))
    co.run_catchup(req('dav-initial'), make({'calendar': lambda: co.ConnectorFetch('calendar', 'acct-a', records=recs)}))
    token = json.dumps({'v': 1, 'token': 'old-opaque-token',
        'through': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        'members': {'removed.ics': ['removed'], 'unlisted.ics': ['unlisted']}})
    cursor = json.dumps({'version': 1, 'collections': {collection: token}})
    ledger.set_coverage('calendar', 'acct-a', 'complete', cursor_generation=cursor)

    def missing():
        raise NotFoundError('removed')

    class Updates(list):
        sync_token = 'next-opaque-token'

    calls = []

    def sync(token, **kwargs):
        calls.append(token)
        return Updates([SimpleNamespace(url='removed.ics', load=missing), SimpleNamespace(load=lambda: None)])

    def failed_search(**kwargs):
        raise RuntimeError('503')

    calendar = SimpleNamespace(url=collection, get_objects_by_sync_token=sync, search=failed_search)
    client = SimpleNamespace(principal=lambda: SimpleNamespace(calendars=lambda: [calendar]))
    monkeypatch.setattr(caldav, '_get_client', lambda *args: client)
    monkeypatch.setattr(caldav, '_get_cal_name', lambda calendar: 'Home')
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    config = SimpleNamespace(sources={'calendar': {'connector': 'icloud_calendar', 'account_scope': 'acct-a',
        'subjects': ['kid1'], 'kwargs': {'sync_changes': True}}},
        subjects=['kid1'], consent_required=frozenset(), viewer='ved')
    for index in range(2):
        fetchers = runtime.build_fetchers(config, '2026-10-01T00:00:00Z', lambda name: caldav,
            lambda name: {'apple_id': 'synthetic@example.invalid', 'app_password': 'synthetic'},
            {'calendar': True}, ledger)
        result = co.run_catchup(req(f'dav-failure-{index}'), make({'calendar': fetchers['calendar']}))
        assert result.status is RunStatus.DEGRADED
        assert ledger.get_cursor('calendar', 'acct-a') == cursor
        assert _calendar_ids(ledger) == [recs[1].upstream_id]
    assert calls == ['old-opaque-token', 'old-opaque-token']
    head = ledger.conn.execute('SELECT signal_id FROM source_heads WHERE upstream_id=?',
                               (recs[0].upstream_id,)).fetchone()
    assert head[0] is None
