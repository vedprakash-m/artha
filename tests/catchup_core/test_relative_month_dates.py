"""Relative month-day grounding: "the 25th of this month" resolves against the Signal's observation date."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from catchup_core import domain_pack as dp
from catchup_core import extract as ex

PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "finance.yaml")
PRED = PACK.predicates["bill_due_date"]
OBSERVED = "2026-10-12T09:00:00Z"


def grounded(value: date, quote: str, observed_at: str | None = OBSERVED) -> bool:
    return ex.supports_value(PRED, value, quote, observed_at=observed_at, message=quote)


@pytest.mark.parametrize("quote,value", [
    ("Your bill is due on the 25th of this month.", date(2026, 10, 25)),
    ("Payment due the 1st of next month.", date(2026, 11, 1)),
    ("Due on the 2nd this month", date(2026, 10, 2)),
    ("Due on the 3rd of next month", date(2026, 11, 3)),
    ("Amount due: the 31st of this month", date(2026, 10, 31)),
    ("Due on the 22nd of THIS MONTH", date(2026, 10, 22)),
])
def test_relative_phrase_grounds_exact_date(quote, value):
    assert grounded(value, quote)


@pytest.mark.parametrize("quote,value", [
    ("Your bill is due on the 25th of this month.", date(2026, 10, 24)),
    ("Your bill is due on the 25th of this month.", date(2026, 11, 25)),
    ("Your bill is due on the 25th of next month.", date(2026, 10, 25)),
    ("Your bill is due on the 25th of this month.", date(2025, 10, 25)),
])
def test_relative_phrase_rejects_any_other_date(quote, value):
    assert not grounded(value, quote)


def test_next_month_rolls_over_december_to_january():
    assert grounded(date(2027, 1, 5), "Due on the 5th of next month.", "2026-12-20T10:00:00Z")
    assert not grounded(date(2026, 1, 5), "Due on the 5th of next month.", "2026-12-20T10:00:00Z")
    assert not grounded(date(2026, 12, 5), "Due on the 5th of next month.", "2026-12-20T10:00:00Z")


def test_leap_year_february_29():
    assert grounded(date(2028, 2, 29), "Due the 29th of this month.", "2028-02-03T08:00:00Z")
    assert grounded(date(2028, 2, 29), "Due the 29th of next month.", "2028-01-15T08:00:00Z")


@pytest.mark.parametrize("quote,observed,clamped", [
    ("Due on the 31st of this month.", "2026-11-04T00:00:00Z", date(2026, 11, 30)),
    ("Due on the 31st of next month.", "2026-10-04T00:00:00Z", date(2026, 11, 30)),
    ("Due on the 29th of this month.", "2027-02-04T00:00:00Z", date(2027, 2, 28)),
    ("Due on the 30th of next month.", "2027-01-04T00:00:00Z", date(2027, 2, 28)),
])
def test_invalid_day_does_not_ground_or_clamp(quote, observed, clamped):
    assert not grounded(clamped, quote, observed)
    nxt = date(clamped.year + clamped.month // 12, clamped.month % 12 + 1, 1)
    assert not grounded(nxt, quote, observed)


@pytest.mark.parametrize("quote", [
    "Your bill is due on the 25th.",
    "Due the 25th",
    "Due on the 25th of the month.",
    "Due on the 25th of last month.",
    "Due on the 25th of every month.",
    "Due on the 32nd of this month.",
    "Due on the 0th of this month.",
    "Due on the 25st of this month.",
    "Due on the 125th of this month.",
])
def test_ambiguous_or_malformed_phrases_stay_ungrounded(quote):
    assert not grounded(date(2026, 10, 25), quote)
    assert not grounded(date(2026, 11, 25), quote)


def test_needs_observation_time_never_wall_clock():
    assert not grounded(date(2026, 10, 25), "Due on the 25th of this month.", None)
    assert not grounded(date(2026, 10, 25), "Due on the 25th of this month.", "")
    assert not grounded(date(2026, 10, 25), "Due on the 25th of this month.", "not-a-date")


def test_deterministic_for_fixed_observation_time():
    results = {grounded(date(2026, 10, 25), "Due on the 25th of this month.") for _ in range(5)}
    assert results == {True}
    assert grounded(date(2031, 6, 25), "Due on the 25th of this month.", "2031-06-01T00:00:00+05:30")


def _parse(text: str, value: str, quote: str, observed_at: str | None = OBSERVED) -> ex.ExtractionResult:
    start = text.encode("utf-8").index(quote.encode("utf-8"))
    sig = ex.NormalizedSignal("sig_0000000000000009", "bill_email", text, source_class="bill_statement",
                              observed_at=observed_at)
    item = {"predicate": "bill_due_date", "value": value, "subject_id": "acct1", "quote": quote,
            "span": [start, start + len(quote.encode("utf-8"))]}
    return ex.parse_output(json.dumps({"complete": True, "candidates": [item]}), PACK, sig, ["acct1"])


def test_extraction_accepts_relative_month_due_date():
    text = "Your statement is ready. Your payment is due on the 25th of this month. Thanks."
    r = _parse(text, "2026-10-25", "due on the 25th of this month")
    assert r.ok and not r.rejections
    assert r.candidates[0].value == date(2026, 10, 25)


def test_extraction_rejects_bare_ordinal_and_negated_relative_date():
    bare = _parse("Your payment is due on the 25th. Thanks.", "2026-10-25", "due on the 25th")
    assert not bare.candidates and "does not support" in bare.rejections[0].reason
    negated = _parse("Your payment is not due on the 25th of this month. Thanks.", "2026-10-25",
                     "the 25th of this month")
    assert not negated.candidates and "does not support" in negated.rejections[0].reason
