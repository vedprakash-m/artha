"""Kids/School pack 0.2.0: attendance notices, bring-an-item requests, sign-up alerts and month-first digit dates."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from catchup_core import domain_pack as dp
from catchup_core import extract as ex

PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "kids_school.yaml")
BULLETIN = ("Weekly Bulletin. 11/9/26-11/20/26 - Registration open until 11/4 at noon (Girls Flag Football). "
            "11/23/26-12/4/26 - Registration open until 11/18 at noon.")
OBSERVED = "2026-10-10T01:18:54+00:00"


def severity(predicate, days, state="outstanding"):
    return PACK.evaluate(predicate, {"days_until": days, "obligation_state": state}).severity


# --- pack rules --------------------------------------------------------------------------------------------------

def test_the_pack_declares_the_new_predicates_with_hints():
    assert PACK.version == "0.3.0"
    assert PACK.predicates["attendance_absence"].type == "date" and PACK.predicates["item_needed_by"].type == "date"
    assert "explain" in PACK.predicates["attendance_absence"].hint and PACK.predicates["item_needed_by"].hint


@pytest.mark.parametrize(("days", "expected"), [(8, None), (7, "P2"), (3, "P2"), (2, "P1"), (0, "P1")])
def test_signup_deadlines_are_p2_within_a_week_and_p1_within_two_days(days, expected):
    got = severity("signup_deadline", days)
    assert (got.value if got else None) == expected


@pytest.mark.parametrize(("days", "expected"), [(6, None), (5, "P2"), (2, "P2"), (1, "P1"), (0, "P1")])
def test_bring_an_item_is_p2_within_five_days_and_p1_within_one(days, expected):
    got = severity("item_needed_by", days)
    assert (got.value if got else None) == expected


@pytest.mark.parametrize(("days", "state", "expected"), [
    (0, "outstanding", "P1"), (-1, "outstanding", "P1"), (-3, "outstanding", "P1"), (-4, "outstanding", None),
    (1, "outstanding", None), (-1, "resolved", None)])
def test_an_unexplained_absence_is_p1_for_three_days_after_it(days, state, expected):
    got = severity("attendance_absence", days, state)
    assert (got.value if got else None) == expected


# --- extraction prompt -------------------------------------------------------------------------------------------

def test_the_prompt_lists_the_new_predicates_with_their_hints():
    signal = ex.NormalizedSignal("sig_1", "school_email", "x", observed_at=OBSERVED)
    prompt = ex.build_request(PACK, signal, ["kid"]).prompt
    assert "attendance_absence: date (YYYY-MM-DD)" in prompt and "item_needed_by: date (YYYY-MM-DD)" in prompt
    assert "absent" in prompt and "needed" in prompt
    assert "digits" in prompt and "other date" in prompt            # rule 9 allows proven month-first digit dates


# --- month-first digit dates -------------------------------------------------------------------------------------

SIGNUP = PACK.predicates["signup_deadline"]


def grounded(value, quote, message=BULLETIN, observed=OBSERVED):
    return ex.supports_value(SIGNUP, value, quote, observed_at=observed, message=message)


def test_a_digit_date_is_accepted_when_the_message_proves_month_first_order():
    assert grounded(date(2026, 11, 4), "Registration open until 11/4 at noon")
    assert grounded(date(2026, 11, 18), "Registration open until 11/18 at noon")
    assert grounded(date(2026, 11, 20), "11/9/26-11/20/26")                         # explicit two-digit year


def test_a_digit_date_is_refused_without_proof_or_with_contradicting_evidence():
    assert not grounded(date(2026, 11, 4), "open until 11/4 at noon", message="open until 11/4 at noon")
    assert not grounded(date(2026, 4, 11), "open until 11/4 at noon")               # that reads April 11 day-first
    assert not grounded(date(2026, 11, 4), "open until 11/4", message=BULLETIN + " Also 25/3 is due.")  # day-first seen
    assert not grounded(date(2025, 11, 4), "open until 11/4 at noon")               # wrong year for the observation
    assert not grounded(date(2027, 11, 18), "11/9/26-11/20/26")                      # explicit year disagrees


def test_month_name_and_iso_dates_are_unchanged():
    assert grounded(date(2026, 10, 26), "entries due by October 26, 2026", message="")
    assert grounded(date(2026, 10, 26), "due 2026-10-26", message="")
    assert not grounded(date(2026, 10, 26), "due 11/4", message=BULLETIN)


def test_extraction_accepts_a_proven_digit_deadline_end_to_end():
    quote = "Registration open until 11/4 at noon"
    start = BULLETIN.index(quote)
    signal = ex.NormalizedSignal("sig_1", "school_email", BULLETIN, observed_at=OBSERVED)
    out = '{"complete": true, "candidates": [{"predicate": "signup_deadline", "value": "2026-11-04", ' \
          f'"subject_id": "kid", "quote": "{quote}", "span": [{start}, {start + len(quote)}], ' \
          '"obligation_ref": "registration", "confidence": 0.8}]}'
    parsed = ex.parse_output(out, PACK, signal, ["kid"])
    assert [c.value for c in parsed.candidates] == [date(2026, 11, 4)] and not parsed.rejections


def test_a_synthetic_attendance_notice_and_item_request_ground_with_their_dates():
    absence = "Sam was absent from 2nd period today, October 20th. Please send a note."
    item = "On October 30 we hold a trick or treat festival. Your child will need tiny treats."
    a = ex.supports_value(PACK.predicates["attendance_absence"], date(2026, 10, 20), absence[:50],
                          observed_at="2026-10-20T17:20:37+00:00", message=absence)
    b = ex.supports_value(PACK.predicates["item_needed_by"], date(2026, 10, 30), "On October 30",
                          observed_at="2026-10-15T00:00:00+00:00", message=item)
    assert a and b
