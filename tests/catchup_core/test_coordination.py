"""RA-19: one accountable owner, acceptance, escalation, no duplicate reminders, permission-filtered."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from catchup_core import runtime as rt
from catchup_core.coordination import coordination_lines, evaluate_tasks

TODAY = date(2026, 10, 20)


def task(**kw):
    base = {"id": "H1", "obligation": "School RSVP", "subject": "kid1", "owner": "spouse", "status": "proposed",
            "assigned_on": "2026-10-19"}
    base.update(kw)
    return base


def states(*records, viewer="owner"):
    return {s.task_id: s for s in evaluate_tasks(records, TODAY, viewer=viewer)}


def test_an_unaccepted_assignment_waits_then_escalates():
    assert states(task())["H1"].state == "awaiting" and "waiting for spouse" in states(task())["H1"].detail
    late = states(task(assigned_on="2026-10-10"))["H1"]
    assert late.state == "escalate" and "10 days" in late.detail
    custom = states(task(assigned_on="2026-10-17", escalate_after_days=2))["H1"]
    assert custom.state == "escalate"


def test_refusal_needs_a_new_owner_and_a_missing_owner_is_visible():
    assert states(task(status="refused"))["H1"].state == "refused"
    nobody = states(task(owner=""))["H1"]
    assert nobody.state == "awaiting" and "no accountable owner" in nobody.detail


def test_done_closes_only_with_evidence():
    assert states(task(status="done", completion_evidence="confirmation sent")) == {}
    assert states(task(status="done"))["H1"].state == "unverified"


def test_accepted_tasks_are_in_hand_and_stay_quiet():
    accepted = evaluate_tasks([task(status="accepted")], TODAY)
    assert accepted[0].state == "accepted" and coordination_lines(accepted) == []


def test_two_open_tasks_for_the_same_obligation_are_reported_as_a_duplicate():
    result = states(task(), task(id="H2", obligation="school rsvp", subject="KID1"))
    assert result["H1"].state == "awaiting" and result["H2"].state == "duplicate"
    assert "same obligation as H1" in result["H2"].detail
    other_child = states(task(), task(id="H3", subject="kid2"))
    assert other_child["H3"].state == "awaiting"


def test_a_task_is_shown_only_to_the_people_it_lists():
    private = task(visible_to=["owner", "spouse"])
    assert "H1" in states(private, viewer="spouse") and "H1" not in states(private, viewer="kid1")
    default = task()
    assert "H1" in states(default, viewer="owner") and "H1" in states(default, viewer="spouse")
    assert "H1" not in states(default, viewer="stranger")


def test_lines_order_escalations_first_and_bad_records_are_ignored():
    lines = coordination_lines(evaluate_tasks([task(id="H9", assigned_on="2026-10-01"), task(id="H2", status="refused"),
                                               {"id": "", "obligation": "x"}, {}], TODAY))
    assert lines[0].startswith("H9:") and lines[1].startswith("H2:") and len(lines) == 2
    assert evaluate_tasks([task(assigned_on="not a date")], TODAY)[0].state == "awaiting"
    assert len(evaluate_tasks([task(id=f"H{i}", obligation=f"o{i}") for i in range(150)], TODAY)) == 100


def test_runtime_reads_household_tasks_for_the_configured_viewer():
    text = ("---\nhousehold_tasks:\n  - {id: H1, obligation: RSVP, owner: spouse, status: proposed, "
            "assigned_on: '2026-10-01', visible_to: [owner]}\n---\n")
    got = rt._coordination_lines(Path("."), reader=lambda _n: text, today=TODAY, viewer="owner")
    assert got and got[0].startswith("H1: RSVP")
    assert rt._coordination_lines(Path("."), reader=lambda _n: text, today=TODAY, viewer="stranger") == []

    def broken(_n):
        raise OSError("locked")
    assert rt._coordination_lines(Path("."), reader=broken, today=TODAY, viewer="owner") == []
