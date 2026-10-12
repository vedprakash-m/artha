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
def test_exceptions_are_unavailable_and_classified(exc, expected):
    got = ad.fetch_via_handler("gmail", "acct", gen([email(1)], exc=exc), since="x")
    assert got.status is CoverageStatus.UNAVAILABLE and got.error is expected
    assert got.records == ()                                  # partial data is not trusted as complete


def test_required_flag_is_carried():
    assert ad.fetch_via_handler("rss", "a", gen([]), since="x", required=False).required is False


def test_text_is_bounded():
    got = ad.fetch_via_handler("gmail", "a", gen([email(1, body="x" * 50_000)]), since="x")
    assert len(got.records[0].text) == ad.MAX_TEXT
