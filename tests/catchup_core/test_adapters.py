from __future__ import annotations

import pytest

from catchup_core import adapters as ad
from catchup_core.contracts import CoverageStatus, FailureClass
from lib.connector_coverage import report_scope


def email(i, **kw):
    r = {"id": f"m{i}", "source": "gmail", "date_iso": "2026-10-11", "subject": f"Subject {i}", "body": "body text"}
    r.update(kw)
    return r


def cal(i, **kw):
    r = {"id": f"e{i}", "source": "google_calendar", "summary": "Soccer", "start": "2026-10-14T15:30:00-07:00",
         "end": "2026-10-14T16:30:00-07:00", "location": "Field 2", "all_day": False}
    r.update(kw)
    return r


def gen(records, scopes=(), exc=None):
    def fetch(**_kw):
        for scope, status, kw in scopes:
            report_scope(scope, status, **kw)
        yield from records
        if exc:
            raise exc
    return fetch


def test_complete_fetch_converts_records_and_text():
    got = ad.fetch_via_handler("gmail", "acct", gen([email(1), email(2, etag="W/7")]), since="2026-10-01T00:00:00Z")
    assert got.status is CoverageStatus.COMPLETE and got.error is None and len(got.records) == 2
    assert got.records[0].text == "Subject 1\nbody text"
    assert got.records[1].provider_version == "W/7" and got.records[0].provider_version is None
    assert got.records[0].occurred_at == "2026-10-11"


def test_calendar_record_text_and_native_start_accepted():
    got = ad.fetch_via_handler("google_calendar", "acct", gen([cal(1)]), since="2026-10-01T00:00:00Z")
    assert got.status is CoverageStatus.COMPLETE
    assert "Soccer" in got.records[0].text and "Start: 2026-10-14T15:30:00-07:00" in got.records[0].text


def test_valid_empty_is_complete():
    got = ad.fetch_via_handler("gmail", "acct", gen([]), since="x")
    assert got.status is CoverageStatus.COMPLETE and got.records == ()


def test_volume_history_is_scoped_to_account_and_filter(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    query = {'query': 'label:school', 'max_results': 20}
    for _ in range(3):
        assert ad.fetch_via_handler('gmail', 'account-a', gen([email(1)]), since='old',
                                    fetch_kwargs=query).status is CoverageStatus.COMPLETE
    assert ad.fetch_via_handler('gmail', 'account-b', gen([]), since='new',
                               fetch_kwargs=query).status is CoverageStatus.COMPLETE
    assert ad.fetch_via_handler('gmail', 'account-a', gen([]), since='new',
                               fetch_kwargs={'query': 'label:other'}).status is CoverageStatus.COMPLETE
    # Dictionary order and advancing the incremental overlap do not reset the same source's history.
    assert ad.fetch_via_handler('gmail', 'account-a', gen([]), since='new',
                               fetch_kwargs=dict(reversed(list(query.items())))).status is CoverageStatus.ANOMALY


def test_incumbent_history_is_not_a_core_account_baseline(tmp_path, monkeypatch):
    from lib.connector_coverage import ConnectorRun, CoverageStore
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    for _ in range(3):
        CoverageStore().record(ConnectorRun('gmail', 20, 0, [], None, [], 0))
    assert ad.fetch_via_handler('gmail', 'account-a', gen([]), since='x').status is CoverageStatus.COMPLETE


def test_rejected_record_degrades_and_is_not_passed_on():
    bad = email(3)
    bad.pop("subject")
    got = ad.fetch_via_handler("gmail", "acct", gen([email(1), bad]), since="x")
    assert got.status is CoverageStatus.DEGRADED and len(got.records) == 1
    assert got.error is FailureClass.SCHEMA_REJECTED and any("rejected" in b for b in got.blind_spots)


def test_failed_scope_degrades_even_with_records():
    got = ad.fetch_via_handler("google_calendar", "acct", gen(
        [cal(1)], scopes=[("calendar:primary", "complete", {"records": 1}),
                          ("calendar:family", "failed", {"reason": "403"})]), since="x")
    assert got.status is CoverageStatus.DEGRADED and got.error is FailureClass.PROVIDER
    assert got.blind_spots == ("calendar:family: failed (403)",)


def test_truncated_scope_is_degraded():
    got = ad.fetch_via_handler("google_calendar", "acct", gen(
        [], scopes=[("calendar:primary", "truncated", {"reason": "page cap"})]), since="x")
    assert got.status is CoverageStatus.DEGRADED


@pytest.mark.parametrize("exc,expected", [
    (RuntimeError("invalid_grant: token revoked"), FailureClass.AUTH),
    (RuntimeError("HTTP 403 Forbidden"), FailureClass.AUTH),
    (TimeoutError("read timed out"), FailureClass.NETWORK),
    (ConnectionError("connection refused"), FailureClass.NETWORK),
    (ValueError("weird"), FailureClass.PROVIDER),
])
@pytest.mark.parametrize('useful', [False, True])
def test_iterator_failure_retains_validated_positives_without_claiming_completeness(exc, expected, useful):
    got = ad.fetch_via_handler("gmail", "acct", gen([email(1)] if useful else [], exc=exc), since="x")
    assert got.status is (CoverageStatus.DEGRADED if useful else CoverageStatus.UNAVAILABLE)
    assert got.error is expected and len(got.records) == int(useful)
    assert got.completed_at is None


def test_cancellation_before_iterator_failure_is_not_lost():
    got = ad.fetch_via_handler('calendar', 'acct', gen([
        {'id': 'cancelled', 'calendar_id': 'family', 'status': 'cancelled'}],
        exc=TimeoutError('PRIVATE provider text')), since='x')
    assert got.status is CoverageStatus.DEGRADED and got.error is FailureClass.NETWORK
    assert got.cancelled == ('calendar:["family","cancelled"]',) and not got.records
    assert 'PRIVATE' not in str(got.blind_spots) and got.completed_at is None


@pytest.mark.parametrize('bad', [None, 'not an object', [], 1,
                                {'id': '', 'status': 'cancelled'}, {'id': '   ', 'status': 'cancelled'}])
def test_invalid_record_does_not_discard_later_valid_data(bad):
    got = ad.fetch_via_handler('school_email', 'acct', gen([bad, email(1)]), since='x')
    assert got.status is CoverageStatus.DEGRADED and got.error is FailureClass.SCHEMA_REJECTED
    assert [record.upstream_id for record in got.records] == ['m1'] and not got.cancelled


def test_required_flag_is_carried():
    assert ad.fetch_via_handler("rss", "a", gen([]), since="x", required=False).required is False


def test_text_is_bounded():
    got = ad.fetch_via_handler("gmail", "a", gen([email(1, body="x" * 50_000)]), since="x")
    assert len(got.records[0].text) > ad.MAX_TEXT  # preserve content; extraction owns explicit chunking


@pytest.mark.parametrize('reset', [False, True])
def test_deleted_collection_before_late_failure_survives_without_cursor_or_full_listing(reset):
    from lib.connector_coverage import (
        report_cursor,
        report_cursor_reset,
        report_deleted_collection,
        report_full_listing,
    )

    def fetch(**_kwargs):
        report_deleted_collection('family')
        report_cursor('uncommitted-next-cursor')
        report_full_listing('primary')
        if reset:
            report_cursor_reset('invalid cursor')
        yield from ()
        raise TimeoutError('PRIVATE sibling provider failure')

    got = ad.fetch_via_handler('calendar', 'account', fetch, since='x')
    assert got.deleted_collections == ('family',)
    assert got.status is CoverageStatus.DEGRADED and got.error is FailureClass.NETWORK
    assert got.cursor is None and got.full_listings == () and got.completed_at is None
    assert got.cursor_reset is reset
    assert not got.records and not got.cancelled
    assert 'PRIVATE' not in str(got.blind_spots)
