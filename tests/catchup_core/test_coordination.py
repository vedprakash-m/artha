"""RA-19: owner-approved assumed acceptance, verified completion and scoped visibility."""
from __future__ import annotations

import pytest

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


@pytest.mark.parametrize('status', [None, 'proposed', 'accepted'])
def test_assigned_work_is_accepted_by_convention_without_a_member_workflow(status):
    record = task(assigned_on='2026-10-01', escalate_after_days=1)
    if status is None:
        record.pop('status')
    else:
        record['status'] = status
    result = states(record)['H1']
    assert result.state == 'accepted'
    assert 'household convention' in result.detail
    assert coordination_lines([result]) == []


def test_refusal_needs_a_new_owner_and_a_missing_owner_is_visible():
    assert states(task(status="refused"))["H1"].state == "refused"
    nobody = states(task(owner=""))["H1"]
    assert nobody.state == "awaiting" and "no accountable owner" in nobody.detail


def test_done_closes_only_with_evidence():
    assert evaluate_tasks([task(status="done", completion_evidence="confirmation sent")], TODAY,
                          verify=lambda ref, key: ref == "confirmation sent") == []
    assert states(task(status="done"))["H1"].state == "unverified"


def test_accepted_tasks_are_in_hand_and_stay_quiet():
    accepted = evaluate_tasks([task(status="accepted")], TODAY)
    assert accepted[0].state == "accepted" and coordination_lines(accepted) == []


def test_two_open_tasks_for_the_same_obligation_are_reported_as_a_duplicate():
    result = states(task(), task(id="H2", obligation="school rsvp", subject="KID1"))
    assert result["H1"].state == "accepted" and result["H2"].state == "duplicate"
    assert "same obligation as H1" in result["H2"].detail
    other_child = states(task(), task(id="H3", subject="kid2"))
    assert other_child["H3"].state == "accepted"


def test_a_task_is_shown_only_to_the_people_it_lists():
    private = task(visible_to=["owner", "spouse"])
    assert "H1" in states(private, viewer="spouse") and "H1" not in states(private, viewer="kid1")
    default = task()
    assert "H1" in states(default, viewer="owner") and "H1" in states(default, viewer="spouse")
    assert "H1" not in states(default, viewer="stranger")


def test_attention_lines_show_actual_gaps_without_acceptance_escalation():
    lines = coordination_lines(evaluate_tasks([task(id='H9', assigned_on='2026-10-01'), task(id='H2', obligation='Return book', status='refused'),
                                               {'id': '', 'obligation': 'x'}, {}], TODAY))
    assert len(lines) == 1 and lines[0].startswith('H2:') and 'reported' in lines[0]
    assert evaluate_tasks([task(assigned_on='not a date')], TODAY)[0].state == 'accepted'
    assert len(evaluate_tasks([task(id=f'H{i}', obligation=f'o{i}') for i in range(150)], TODAY)) == 101


def test_runtime_reads_household_tasks_for_the_configured_viewer():
    text = ("---\nhousehold_tasks:\n  - {id: H1, obligation: RSVP, owner: spouse, status: refused, "
            "assigned_on: '2026-10-01', visible_to: [owner]}\n---\n")
    got = rt._coordination_lines(Path("."), reader=lambda _n: text, today=TODAY, viewer="owner")
    assert got.lines and got.lines[0].startswith("H1: RSVP")
    assert rt._coordination_lines(Path("."), reader=lambda _n: text, today=TODAY, viewer="stranger").lines == []

    def broken(_n):
        raise OSError("locked")
    with pytest.raises(OSError, match="locked"):
        rt._coordination_lines(Path("."), reader=broken, today=TODAY, viewer="owner")


@pytest.mark.parametrize('visible', [[], ['spouse']])
def test_explicit_visibility_never_falls_back_to_the_primary_user(visible):
    assert evaluate_tasks([task(visible_to=visible)], TODAY, viewer='owner') == []


@pytest.mark.parametrize('visible', ['owner', None, [3], ['']])
def test_malformed_visibility_requires_review(visible):
    with pytest.raises(ValueError, match='visibility'):
        evaluate_tasks([task(visible_to=visible)], TODAY)


def test_invisible_records_cannot_defer_visible_work_or_disclose_their_count():
    hidden = [task(id=f'private{i}', visible_to=['spouse']) for i in range(150)]
    visible = task(id='visible', owner='', visible_to=['owner'])
    result = evaluate_tasks(hidden + [visible], TODAY)
    assert [row.task_id for row in result] == ['visible']
    assert 'private' not in str(coordination_lines(result))


def test_unknown_authored_status_cannot_silently_suppress_attention():
    assert states(task(status='accepeted'))['H1'].state == 'unverified'
