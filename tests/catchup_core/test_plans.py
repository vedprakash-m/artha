"""RA-25: plans are evaluated at Catch-up only; completion needs evidence; nothing advances on its own."""
from __future__ import annotations

import pytest

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
    s = by_id(evaluate_plans([GOAL], TODAY, verify=lambda ref, key: ref == "receipt 2026-10-02"))
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
    invalid = evaluate_plans([{"id": "G", "milestones": "nope"}, {"id": "H"}, {"milestones": [{"title": "no id"}]}], TODAY)
    assert len(invalid) == 2 and all(s.state == "invalid" for s in invalid)
    bad_date = {"id": "G", "milestones": [{"id": "d", "title": "D", "target_date": "soon"}]}
    assert by_id(evaluate_plans([bad_date], TODAY))["d"].state == "invalid"


def test_lines_put_ready_first_and_keep_done_quiet():
    lines = plan_lines(evaluate_plans([GOAL], TODAY, verify=lambda ref, key: ref == "receipt 2026-10-02"))
    assert lines[0].startswith("G1: ready") and "File the form" in lines[0] and "target Nov 15" in lines[0]
    assert any("waits for File the form" in line and "overdue" in line for line in lines)
    assert any("no evidence, so it is not counted" in line for line in lines)
    assert not any("Gather documents" in line for line in lines)


def test_runtime_reads_milestones_from_active_goals_only():
    text = ("---\ngoals:\n  - {id: G1, status: active, milestones: [{id: m, title: Next, target_date: '2026-10-25'}]}\n"
            "  - {id: G2, status: done, milestones: [{id: z, title: Hidden}]}\n---\n")
    lines = rt._plan_lines(__import__("pathlib").Path("."), reader=lambda _n: text, today=TODAY)
    assert lines.lines == ["G1: ready — next step: Next (target Oct 25)",
                          "G1/m: instruction sheet for unassigned; review needed", "Step 1: Next"]
    assert not lines.gaps

    def broken(_n):
        raise OSError("locked")
    with pytest.raises(OSError, match="locked"):
        rt._plan_lines(__import__("pathlib").Path("."), reader=broken, today=TODAY)


def test_ready_milestone_has_an_inert_instruction_sheet_from_authored_steps():
    goal = {'id': 'G', 'owner': 'owner', 'milestones': [
        {'id': 'm', 'title': 'Prepare the form', 'instructions': ['Collect the required documents', 'Review the draft']} ]}
    states = evaluate_plans([goal], TODAY)
    lines = plan_lines(states)
    assert any('instruction sheet' in line and 'G/m' in line for line in lines)
    assert any('Collect the required documents' in line for line in lines)
    assert any('Review the draft' in line for line in lines)
    assert not any('approved' in line or 'executed' in line for line in lines)
    goal['milestones'][0]['prerequisites'] = ['missing']
    assert not any('instruction sheet' in line for line in plan_lines(evaluate_plans([goal], TODAY)))
    goal['milestones'][0]['instructions'] = 'send the form'
    assert evaluate_plans([goal], TODAY)[0].state == 'invalid'


def test_instruction_sheet_is_retained_in_the_encrypted_catchup_artifact(tmp_path):
    import test_release_regressions as regressions
    from catchup_core.coordinator import run_catchup
    from catchup_core.contracts import RunStatus
    import yaml
    world = regressions.world.__wrapped__(tmp_path)
    led, _, dependencies, published = next(world)
    try:
        text = yaml.safe_dump({'goals': [{'id': 'G', 'owner': 'owner', 'status': 'active', 'milestones': [
            {'id': 'm', 'title': 'Prepare the form', 'instructions': ['Collect documents', 'Review the draft']}]}]})
        deps = dependencies()
        deps.plan_lines = lambda: rt._plan_lines(tmp_path, reader=lambda _: text, today=TODAY,
                                                 ledger=led, viewer='owner')
        result = run_catchup(regressions.request(), deps)
        assert result.status is RunStatus.COMPLETE and published
        assert '1 planned commitment(s)' in result.briefing_text
        assert 'instruction sheet' in result.briefing_text and 'Review the draft' in result.briefing_text
        draft = led.draft('cu_a')
        assert 'Collect documents' in draft['text']
        row = led.conn.execute("SELECT payload_encrypted FROM briefing_drafts WHERE catchup_id='cu_a'").fetchone()
        assert 'Collect documents' not in str(row[0]) and 'Review the draft' not in str(row[0])
        assert not led.conn.execute("SELECT 1 FROM facts WHERE predicate='commitment_completed'").fetchone()
    finally:
        with __import__('contextlib').suppress(StopIteration):
            next(world)
