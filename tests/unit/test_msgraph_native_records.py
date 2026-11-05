"""Native Graph fields retain their provider meaning for Work consumers."""
from datetime import datetime

import pytest

from connectors import msgraph_calendar, msgraph_email


@pytest.mark.parametrize("stamp", [
    "2026-11-03T09:00:00-08:00", "2026-11-03T09:00:00+05:30",
    "2026-11-03T17:00:00Z", "2026-11-03T17:00:00.1234567",
])
def test_calendar_preserves_real_timestamp_offsets(stamp):
    event = msgraph_calendar._parse_event({
        "id": "synthetic", "subject": "Synthetic meeting",
        "start": {"dateTime": stamp, "timeZone": "UTC"},
        "end": {"dateTime": stamp, "timeZone": "UTC"},
    })
    parsed = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
    expected = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if expected.tzinfo is not None:
        assert parsed == expected
        assert parsed.utcoffset() == expected.utcoffset()
    else:
        assert parsed.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("read", [True, False])
def test_email_exposes_native_read_importance_folder_and_received_fields(read):
    record = msgraph_email._parse_message({
        "id": "synthetic", "subject": "Synthetic message",
        "receivedDateTime": "2026-11-03T17:00:00Z",
        "isRead": read, "importance": "high",
        "body": {"contentType": "text", "content": "Synthetic body"},
    }, folder="inbox")
    assert record["is_read"] is read
    assert record["importance"] == "high"
    assert record["folder"] == "inbox"
    assert record["received"] == record["date_iso"]
    assert "needs_response" not in record


def test_missing_email_read_status_is_unknown_not_unread():
    record = msgraph_email._parse_message({"id": "synthetic"})
    assert record["is_read"] is None
