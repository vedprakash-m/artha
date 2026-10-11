"""WP-02 / WP02.AC1-AC2: calendar records from every provider pass the real validator.

Each test runs a real-shaped *provider payload* through the connector's own parser
and then through ``schemas.connector_record.validate_record`` — the chain the
pipeline executes.  Removing a required field must fail the chain (the contract
is tested in both directions).  Re-artha §12.2 item 4, §11.7 "Contract chain".
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from schemas.connector_record import validate_record  # noqa: E402

# ── real-shaped provider payloads (synthetic content, genuine field structure) ──

GOOGLE_TIMED = {
    "kind": "calendar#event", "etag": '"3411"', "id": "q1w2e3r4t5y6u7i8o9p0",
    "status": "confirmed", "summary": "Parent-teacher conference",
    "start": {"dateTime": "2026-10-14T15:30:00-07:00", "timeZone": "America/Los_Angeles"},
    "end": {"dateTime": "2026-10-14T16:00:00-07:00", "timeZone": "America/Los_Angeles"},
    "attendees": [{"email": "teacher@school.example", "displayName": "A. Teacher"}],
}
GOOGLE_ALL_DAY = {
    "kind": "calendar#event", "id": "allday0001", "status": "confirmed",
    "summary": "School holiday", "start": {"date": "2026-10-19"}, "end": {"date": "2026-10-20"},
}
GRAPH_TIMED = {
    "id": "AAMkAGI2THVSAAA=", "subject": "Dentist", "isAllDay": False, "isCancelled": False,
    "start": {"dateTime": "2026-10-14T16:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2026-10-14T17:00:00.0000000", "timeZone": "UTC"},
    "location": {"displayName": "Main St Dental"}, "attendees": [], "sensitivity": "normal",
}
GRAPH_ALL_DAY = {
    "id": "AAMkAGI2THVSBBB=", "subject": "Conference day", "isAllDay": True, "isCancelled": False,
    "start": {"dateTime": "2026-10-21T00:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2026-10-22T00:00:00.0000000", "timeZone": "UTC"},
    "location": {}, "attendees": [], "sensitivity": "normal",
}

ICS_TIMED = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\n"
    "UID:caldav-evt-0001@example.invalid\r\nDTSTAMP:20261001T000000Z\r\n"
    "DTSTART:20261014T230000Z\r\nDTEND:20261015T000000Z\r\nSUMMARY:Soccer practice\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)


class _FakeCalDavEvent:
    def __init__(self, ics: str):
        import vobject  # noqa: PLC0415
        self.vobject_instance = vobject.readOne(ics)


def _google(payload):
    import connectors.google_calendar as m  # noqa: PLC0415
    rec = m._parse_event(payload, "Family")
    rec["source"] = "google_calendar"            # set by fetch() from source_tag
    return rec


def _graph(payload):
    import connectors.msgraph_calendar as m  # noqa: PLC0415
    return m._parse_event(payload, "Calendar")   # sets source itself


def _caldav(ics):
    import connectors.caldav_calendar as m  # noqa: PLC0415
    return m._parse_event(_FakeCalDavEvent(ics), "Home")


CASES = [
    ("google_timed", lambda: _google(GOOGLE_TIMED)),
    ("google_all_day", lambda: _google(GOOGLE_ALL_DAY)),
    ("graph_timed", lambda: _graph(GRAPH_TIMED)),
    ("graph_all_day", lambda: _graph(GRAPH_ALL_DAY)),
    ("caldav_timed", lambda: _caldav(ICS_TIMED)),
]


@pytest.mark.parametrize("name,build", CASES, ids=[c[0] for c in CASES])
def test_native_calendar_record_passes_real_validator(name, build):
    record = build()
    assert "date_iso" not in record, "connectors keep native fields (D-21)"
    validated = validate_record(record)
    assert validated.date_iso == record["start"]
    assert validated.source == record["source"]


@pytest.mark.parametrize("name,build", CASES, ids=[c[0] for c in CASES])
def test_calendar_record_without_start_is_rejected(name, build):
    record = copy.deepcopy(build())
    record.pop("start")
    with pytest.raises(ValueError, match="start"):
        validate_record(record)


@pytest.mark.parametrize("name,build", CASES, ids=[c[0] for c in CASES])
def test_calendar_record_with_unparsable_start_is_rejected(name, build):
    record = copy.deepcopy(build())
    record["start"] = "next tuesday"
    with pytest.raises(ValueError, match="start"):
        validate_record(record)


@pytest.mark.parametrize("name,build", CASES, ids=[c[0] for c in CASES])
def test_calendar_record_without_id_is_rejected(name, build):
    record = copy.deepcopy(build())
    record.pop("id")
    with pytest.raises(ValueError, match="id"):
        validate_record(record)


def test_email_records_still_require_date_iso():
    rec = {"id": "m1", "source": "gmail", "subject": "s", "body": "b"}
    with pytest.raises(ValueError, match="date_iso"):
        validate_record(rec)


def test_calendar_shaped_record_from_unknown_source_is_validated_as_calendar():
    rec = {"id": "x1", "source": "custom_ics", "summary": "S", "start": "2026-10-14T09:00:00Z",
           "end": "2026-10-14T10:00:00Z", "all_day": False, "calendar": "c"}
    assert validate_record(rec).date_iso == "2026-10-14T09:00:00Z"


def test_legacy_calendar_record_with_valid_date_iso_still_accepted():
    rec = {"id": "1", "source": "outlook_calendar", "date_iso": "2026-10-14T09:00:00Z"}
    assert validate_record(rec).date_iso == "2026-10-14T09:00:00Z"
