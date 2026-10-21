"""RA-25: a written duty is never permission; checks come due at the next Catch-up, not in the background."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from catchup_core import runtime as rt
from catchup_core.responsibilities import evaluate_responsibilities, responsibility_lines

TODAY = date(2026, 10, 20)


def rec(**kw):
    base = {"id": "R1", "outcome": "Renew registration", "allowed_operations": ["read", "draft"], "status": "active"}
    base.update(kw)
    return base


def only(records):
    return evaluate_responsibilities(records, TODAY)


def test_never_checked_cadence_elapsed_and_explicit_next_check_are_due():
    assert only([rec()])[0].state == "due" and only([rec()])[0].detail == "never checked"
    elapsed = only([rec(last_checked="2026-10-01", cadence_days=14)])[0]
    assert elapsed.state == "due" and elapsed.next_check == date(2026, 10, 15)
    assert only([rec(last_checked="2026-10-19", cadence_days=14, next_check="2026-10-20")])[0].state == "due"


def test_a_duty_that_is_not_yet_due_stays_quiet():
    states = only([rec(last_checked="2026-10-15", cadence_days=14)])
    assert states[0].state == "waiting" and responsibility_lines(states) == []


def test_operations_beyond_read_draft_propose_are_reported_and_never_granted():
    state = only([rec(allowed_operations=["read", "send", "pay"], last_checked="2026-10-19", cadence_days=30)])
    invalid = [s for s in state if s.state == "invalid"]
    assert invalid and "send, pay" in invalid[0].detail and invalid[0].allowed == ("read",)
    line = responsibility_lines(only([rec(allowed_operations=["send"])]))
    assert any("may only read, draft or propose" in item for item in line)
    assert not any("may send" in item for item in line)


def test_completion_needs_evidence_and_stopped_duties_vanish():
    assert only([rec(status="completed", completion_evidence="confirmation #42")]) == []
    unverified = only([rec(status="completed")])[0]
    assert unverified.state == "unverified" and "no completion evidence" in unverified.detail
    assert only([rec(status="stopped")]) == []


def test_unusable_records_and_dates_never_raise():
    assert only([{"id": "", "outcome": "x"}, {"id": "R", "outcome": ""}, {}]) == []
    bad = only([rec(last_checked="yesterday", cadence_days="often")])[0]
    assert bad.state == "due" and bad.detail == "never checked"
    assert len(evaluate_responsibilities([rec(id=f"R{i}") for i in range(80)], TODAY)) == 50


def test_lines_put_due_first_and_name_what_is_allowed():
    states = only([rec(id="R2", status="completed"), rec(id="R1")])
    lines = responsibility_lines(states)
    assert lines[0].startswith("R1: check now") and "(may read, draft)" in lines[0] and lines[1].startswith("R2:")


def test_runtime_reads_the_responsibilities_file_through_the_reader():
    text = ("---\nresponsibilities:\n  - {id: R1, outcome: Pay the bill, allowed_operations: [read], "
            "last_checked: '2026-10-01', cadence_days: 7}\n---\n")
    assert rt._responsibility_lines(Path("."), reader=lambda _n: text, today=TODAY)[0].startswith("R1: check now")

    def broken(_n):
        raise OSError("locked")
    assert rt._responsibility_lines(Path("."), reader=broken, today=TODAY) == []
    assert rt._responsibility_lines(Path("."), reader=lambda _n: "", today=TODAY) == []
