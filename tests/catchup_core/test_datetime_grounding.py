"""Natural-language grounding of datetime-typed predicates (synthetic fixtures only)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from catchup_core import domain_pack as dp
from catchup_core import extract as ex

PACKS = Path(dp.__file__).parent / "packs"
HEALTH = dp.load_pack_file(PACKS / "health.yaml")
KIDS = dp.load_pack_file(PACKS / "kids_school.yaml")
PRED = HEALTH.predicates["doctor_appointment"]
OBSERVED = "2026-10-30T09:00:00Z"
VALUE = datetime.fromisoformat("2026-11-04T15:30:00-08:00")   # 2026-11-04 is a Wednesday


def grounded(quote, value=VALUE, observed_at=OBSERVED, message=None):
    return ex.supports_value(PRED, value, quote, observed_at=observed_at, message=message or quote)


@pytest.mark.parametrize("quote", [
    "Wednesday, November 4, 2026 at 3:30 PM",
    "Nov 4 at 3:30pm",
    "4 Nov 2026, 3:30 p.m.",
    "Your visit: Wed Nov. 4th @ 3:30 PM with Dr. Example",
    "3:30 PM on Wednesday, November 4",
    "the 4th of November at 15:30",
    "Nov 4 at 3:30 PM PST",
    "Nov 4 at 3:30 PM (PT)",
    "Nov 4, 2026 at 3:30 PM UTC-8",
    "2026-11-04t15:30:00-08:00",
])
def test_natural_language_date_and_time_ground_the_value(quote):
    assert grounded(quote)


def test_noon_and_midnight_meridiem():
    assert grounded("Nov 4 at 12 PM", datetime.fromisoformat("2026-11-04T12:00:00-08:00"))
    assert grounded("Nov 4 at 12:15 am", datetime.fromisoformat("2026-11-04T00:15:00-08:00"))
    assert not grounded("Nov 4 at 12 AM", datetime.fromisoformat("2026-11-04T12:00:00-08:00"))


def test_missing_year_uses_nearest_future_occurrence():
    later = datetime.fromisoformat("2027-11-04T15:30:00-08:00")
    assert grounded("Nov 4 at 3:30 PM", observed_at="2026-11-10T00:00:00Z", value=later)
    assert not grounded("Nov 4 at 3:30 PM", observed_at="2026-11-10T00:00:00Z")
    assert grounded("Nov 4 at 3:30 PM", observed_at="2026-11-04T00:00:00Z")


def test_missing_year_weekday_selects_the_consistent_year():
    # 2027-11-04 is a Thursday: the weekday moves the nearest occurrence a year out.
    assert grounded("Thursday, Nov 4 at 3:30 PM", datetime.fromisoformat("2027-11-04T15:30:00-08:00"))
    assert not grounded("Thursday, Nov 4 at 3:30 PM")


@pytest.mark.parametrize("quote", [
    "Tuesday, November 4, 2026 at 3:30 PM",    # weekday contradicts the date
    "November 4, 2026",                         # date only
    "Wednesday, Nov 4",
    "3:30 PM",                                  # time only
    "Nov 4 at 4:30 PM",
    "Nov 4 at 3:30 AM",
    "Nov 4 at 3:31 PM",
    "Nov 5 at 3:30 PM",
    "Nov 4, 2025 at 3:30 PM",
    "Nov 4 at 3:30 PM EST",                     # zone conflicts with the -08:00 offset
    "Nov 4 at 3:30 PM CET",                     # zone that cannot be checked
    "Nov 4 at 3:30 PM UTC",
    "Nov 4 at 3:30 PM UTC+5:30",
    "Nov 4 at 3:30",                            # ambiguous 12-hour reading of 03:30
    "Nov 4 at 43:30",
])
def test_unsupported_or_contradicting_text_does_not_ground(quote):
    assert not grounded(quote)


def test_missing_year_without_observation_date_does_not_ground():
    assert not grounded("Nov 4 at 3:30 PM", observed_at=None)
    assert grounded("Nov 4, 2026 at 3:30 PM", observed_at=None)


def test_seconds_must_match_the_wall_clock():
    assert not grounded("Nov 4 at 3:30 PM", datetime.fromisoformat("2026-11-04T15:30:05-08:00"))
    assert grounded("Nov 4 at 3:30:05 PM", datetime.fromisoformat("2026-11-04T15:30:05-08:00"))


MONTH_FIRST = "Moved from 10/28/2026. New visit: 11/04/2026 15:30"   # 10/28 cannot be day-first


def test_numeric_date_grounds_only_when_the_message_proves_month_first():
    assert grounded("11/04/2026 15:30", message=MONTH_FIRST)
    assert grounded("Wed 11/04 at 3:30 PM", message=MONTH_FIRST)
    assert not grounded("11/04/2026 15:30")                      # ambiguous: same rule as date-only grounding
    assert not grounded("11/04/2026 15:30", message="Visit: 11/04/2026 15:30, follow-up 12/01/2026")


@pytest.mark.parametrize("quote", ["Tue 11/04/2026 15:30", "11/04/27 15:30", "11/05/2026 15:30"])
def test_numeric_date_with_proven_order_still_rejects_mismatches(quote):
    assert not grounded(quote, message=MONTH_FIRST)


def test_numeric_date_is_never_read_day_first():
    message = "Clinic closed 13/04/2026. New visit: 11/04/2026 15:30"
    april = datetime.fromisoformat("2026-04-11T15:30:00-07:00")
    assert not grounded("11/04/2026 15:30", april, message=message)
    assert not grounded("11/04/2026 15:30", message=message)
    mixed = MONTH_FIRST + " Old: 13/04/2026"
    assert not grounded("11/04/2026 15:30", message=mixed)


def test_existing_iso_literal_still_grounds():
    assert grounded(f"Appointment at {VALUE.isoformat()}")


TEXT = ("Reminder for families. Kid1 has a dental check on Wednesday, November 4, 2026 at 3:30 PM. "
        "The old visit on Nov 2 at 9:00 AM is cancelled.")


def _parse(value, quote, text=TEXT):
    start = len(text[:text.index(quote)].encode("utf-8"))
    item = {"predicate": "appointment_time", "value": value, "subject_id": "kid1", "quote": quote,
            "span": [start, start + len(quote.encode("utf-8"))]}
    signal = ex.NormalizedSignal("sig_dt", "school_email", text, observed_at=OBSERVED)
    return ex.parse_output(json.dumps({"complete": True, "candidates": [item]}), KIDS, signal, ["kid1"])


def test_extraction_accepts_natural_language_appointment_time():
    result = _parse("2026-11-04T15:30:00-08:00", "Wednesday, November 4, 2026 at 3:30 PM")
    assert not result.rejections
    assert result.candidates[0].value == VALUE


def test_extraction_still_rejects_a_cancelled_appointment_time():
    result = _parse("2026-11-02T09:00:00-08:00", "Nov 2 at 9:00 AM")
    assert not result.candidates
    assert "negated, cancelled, or replaced" in result.rejections[0].reason


def test_cancelled_numeric_appointment_is_rejected_when_proof_is_outside_the_sentence():
    text = "Moved from 10/28/2026.\nThe visit on 11/04/2026 15:30 is cancelled."
    result = _parse("2026-11-04T15:30:00-08:00", "11/04/2026 15:30", text)
    assert not result.candidates
    assert "negated, cancelled, or replaced" in result.rejections[0].reason
