"""RA-25: plans are evaluated at Catch-up only; completion needs evidence; nothing advances on its own."""
from __future__ import annotations

from datetime import date

from catchup_core import runtime as rt
from catchup_core.plans import evaluate_plans, plan_lines

TODAY = date(2026, 10, 20)
GOAL = {"id": "G1", "milestones": [
    {"id": "m1", "title": "Gather documents", "status": "done", "evidence": "receipt 2026-10-02"},
    {"id": "m2", "title": "File the form", "prerequisites": ["m1"], "target_date": "2026-11-15"},
    {"id": "m3", "title": "Pay the fee", "prerequisites": ["m2"], "target_date": "2026-10-01"},
    {"id": "m4", "title": "Book interview", "status": "done"},
]}


def by_id(states):
    return {s.milestone_id: s for s in states}


def test_prerequisite_with_evidence_makes_the_next_milestone_ready():
    s = by_id(evaluate_plans([GOAL], TODAY))
    assert s["m1"].state == "done" and s["m2"].state == "ready"
    assert s["m3"].state == "blocked" and s["m3"].waiting_for == ("File the form",) and s["m3"].overdue


def test_done_without_evidence_is_unverified_and_still_blocks_dependants():
    goal = {"id": "G2", "milestones": [{"id": "a", "title": "A", "status": "done"},
                                       {"id": "b", "title": "B", "prerequisites": ["a"]}]}
    s = by_id(evaluate_plans([goal], TODAY))
    assert s["a"].state == "unverified" and s["b"].state == "blocked" and s["b"].waiting_for == ("A",)


def test_unknown_prerequisite_blocks_and_bad_structure_yields_no_claims():
    goal = {"id": "G3", "milestones": [{"id": "x", "title": "X", "prerequisites": ["ghost"]}]}
    assert by_id(evaluate_plans([goal], TODAY))["x"].waiting_for == ("ghost",)
    assert evaluate_plans([{"id": "G", "milestones": "nope"}, {"id": "H"}, {"milestones": [{"title": "no id"}]}], TODAY) == []
    bad_date = {"id": "G", "milestones": [{"id": "d", "title": "D", "target_date": "soon"}]}
    assert by_id(evaluate_plans([bad_date], TODAY))["d"].target_date is None


def test_lines_put_ready_first_and_keep_done_quiet():
    lines = plan_lines(evaluate_plans([GOAL], TODAY))
    assert lines[0].startswith("G1: ready") and "File the form" in lines[0] and "target Nov 15" in lines[0]
    assert "waits for File the form" in lines[1] and "overdue" in lines[1]
    assert "no evidence, so it is not counted" in lines[2]
    assert not any("Gather documents" in line for line in lines)


def test_runtime_reads_milestones_from_active_goals_only():
    text = ("---\ngoals:\n  - {id: G1, status: active, milestones: [{id: m, title: Next, target_date: '2026-10-25'}]}\n"
            "  - {id: G2, status: done, milestones: [{id: z, title: Hidden}]}\n---\n")
    lines = rt._plan_lines(__import__("pathlib").Path("."), reader=lambda _n: text, today=TODAY)
    assert lines == ["G1: ready — next step: Next (target Oct 25)"]

    def broken(_n):
        raise OSError("locked")
    assert rt._plan_lines(__import__("pathlib").Path("."), reader=broken, today=TODAY) == []
