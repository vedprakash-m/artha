from __future__ import annotations

import io
import json
from datetime import date, datetime, timezone

import pytest

from catchup_core import cli
from catchup_core import goals as gl
from catchup_core.contracts import CatchupRequest, CatchupResult, CoverageEntry, CoverageStatus, ExecutionMode, RunStatus
from catchup_core.ledger import FactView
from catchup_core.scorecard import Case, score

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
GOAL = gl.AuthoredGoal("G-1", "ved", "Register on time", "exam_registration_deadline", target=2, period_days=30)


def fv(fid, value):
    return FactView(fid, "kids_school", "exam_registration_deadline", value, "kid1", "ordinary",
                    "verified_source", "current", None, None, False, "t", "t")


# ── goals ─────────────────────────────────────────────────────────────────

def test_observation_counts_only_obligations_met_on_time():
    facts = [fv("a", "2026-10-05"), fv("b", "2026-10-08"), fv("c", "2026-09-01"),
             fv("d", {"value": "2026-10-10", "lineage": "legacy_state:x@y"}), fv("e", "garbage")]
    resolved = {"a": date(2026, 10, 4), "b": date(2026, 10, 9)}      # b was late
    obs = gl.observe_on_time(GOAL, facts, resolved, NOW, last_verified=NOW)
    assert obs.value == 1 and set(obs.source_fact_ids) == {"a", "b", "d"}      # c is outside the period
    assert gl.assess(GOAL, obs).status == "at_risk"


def test_target_met_is_on_track():
    facts = [fv("a", "2026-10-05"), fv("b", "2026-10-08")]
    obs = gl.observe_on_time(GOAL, facts, {"a": date(2026, 10, 1), "b": date(2026, 10, 8)}, NOW, last_verified=NOW)
    assert gl.assess(GOAL, obs).status == "on_track"


def test_no_verified_source_means_unknown_not_zero():
    obs = gl.observe_on_time(GOAL, [fv("a", "2026-10-05")], {}, NOW, last_verified=None)
    assert obs.value is None and gl.assess(GOAL, obs).status == "unknown"


def test_stale_evidence_is_unknown():
    old = datetime(2026, 10, 1, tzinfo=timezone.utc)
    obs = gl.observe_on_time(GOAL, [], {}, NOW, last_verified=old)
    a = gl.assess(GOAL, obs)
    assert a.status == "unknown" and "h old" in a.reason


def test_authored_goal_is_immutable():
    with pytest.raises(Exception):
        GOAL.target = 99   # type: ignore[misc]


# ── scorecard ─────────────────────────────────────────────────────────────

def c(**kw):
    base = dict(case_id="c", expected=frozenset({"k1"}), surfaced=frozenset({"k1"}))
    base.update(kw)
    return Case(**base)


def test_clean_run_passes():
    s = score([c(), c(case_id="d", expected=frozenset(), surfaced=frozenset(), quiet_claim=True)])
    assert s.passes and s.recall == 1.0 and s.precision == 1.0 and s.failures() == []


def test_false_quiet_claim_fails_even_with_high_recall():
    cases = [c(case_id=str(i)) for i in range(20)] + [
        c(case_id="q", expected=frozenset({"k9"}), surfaced=frozenset(), quiet_claim=True)]
    s = score(cases)
    assert not s.passes and s.false_quiet == 1 and s.recall < 1.0


def test_quiet_over_incomplete_coverage_is_false_quiet():
    s = score([c(expected=frozenset(), surfaced=frozenset(), quiet_claim=True, coverage_complete=False)])
    assert s.false_quiet == 1 and not s.passes


def test_regression_vs_incumbent_and_citations():
    s = score([c(expected=frozenset({"k1", "k2"}), surfaced=frozenset({"k1"}),
                 incumbent_surfaced=frozenset({"k2"}), citations_resolve=False)])
    assert s.regressions_vs_incumbent == 1 and s.unresolved_citations == 1
    assert len(s.failures()) == 4 - 0 or len(s.failures()) >= 3


def test_empty_scorecard_cannot_authorize_a_release():
    s = score([])
    assert not s.passes and s.cases == 0


# ── cli ───────────────────────────────────────────────────────────────────

def test_authoritative_default_factory_is_disabled_until_cutover(monkeypatch):
    monkeypatch.setenv("ARTHA_CUTOVER", "0")
    err = io.StringIO()
    assert cli.main(["catchup", "--json", "--host", "mac"], out=io.StringIO(), err=err) == 30
    assert "cut over" in err.getvalue()


def test_shadow_default_factory_reports_missing_configuration(monkeypatch):
    from catchup_core import runtime
    monkeypatch.setattr(runtime, "build_deps", lambda request: (_ for _ in ()).throw(
        runtime.RuntimeConfigError("config/catchup_core.yaml missing")))
    err = io.StringIO()
    assert cli.main(["catchup", "--json", "--shadow"], out=io.StringIO(), err=err) == 30
    assert "catchup_core.yaml" in err.getvalue()


def test_authoritative_without_host_is_a_usage_error():
    err = io.StringIO()
    assert cli.main(["catchup"], out=io.StringIO(), err=err) == 30
    assert "host" in err.getvalue()


def test_bad_arguments_exit_30():
    assert cli.main(["catchup", "--format", "nonsense"], out=io.StringIO(), err=io.StringIO()) == 30
    assert cli.main([], out=io.StringIO(), err=io.StringIO()) == 30


def test_json_output_and_exit_code_follow_the_result(monkeypatch):
    def fake_run(request, deps):
        return CatchupResult(request.catchup_id, request.mode, RunStatus.DEGRADED,
                             coverage=(CoverageEntry("calendar", "a", CoverageStatus.UNAVAILABLE),),
                             reason_unpublished="shadow")
    monkeypatch.setattr(cli, "run_catchup", fake_run)
    out = io.StringIO()
    code = cli.main(["catchup", "--json", "--shadow", "--id", "cu_x"], deps_factory=lambda r: object(),
                    out=out, err=io.StringIO())
    assert code == 10
    data = json.loads(out.getvalue())
    assert data["catchup_id"] == "cu_x" and data["status"] == "degraded" and data["mode"] == "shadow"
    text = io.StringIO()
    assert cli.main(["catchup", "--shadow"], deps_factory=lambda r: object(),
                    out=text, err=io.StringIO()) == 10
    assert "degraded" in text.getvalue()


@pytest.fixture
def goal_world(tmp_path):
    from test_release_regressions import world
    yield from world.__wrapped__(tmp_path)


def test_milestone_goal_counts_only_current_bound_completion_evidence(goal_world):
    from catchup_core.curated import completion_key, milestone_record
    from catchup_core.contracts import Provenance, FactStatus
    from catchup_core.ledger import FactInput
    ledger = goal_world[0]
    row = {'id':'G', 'owner':'owner', 'title':'Finish application', 'status':'active',
           'metric':{'kind':'milestones'}, 'milestones':[{'id':'a','title':'Gather'},
             {'id':'b','title':'Submit','status':'done','evidence':'unverified label'}]}
    key = completion_key('milestone', milestone_record(row, row['milestones'][0]))
    fid = ledger.put_fact(FactInput('kids_school', 'commitment_completed',
        {'record_key':key,'completed':True,'actor':'owner'}, 'owner', NOW.isoformat(),
        provenance=Provenance.USER_CONFIRMED, obligation_key=key))
    lines = gl.observe_goals(ledger, [row], 'owner', NOW)
    assert '1 of 2 milestones' in lines[0]
    ledger.set_fact_status(fid, FactStatus.RETRACTED)
    assert '0 of 2 milestones' in gl.observe_goals(ledger, [row], 'owner', NOW)[0]
    row['milestones'][0]['title'] = 'Gather a different set'
    assert '0 of 2 milestones' in gl.observe_goals(ledger, [row], 'owner', NOW)[0]


def test_milestone_metric_with_invalid_graph_is_unknown(goal_world):
    row = {'id':'G','owner':'owner','title':'Plan','metric':{'kind':'milestones'},
           'milestones':[{'id':'a','prerequisites':['a']}]}
    assert 'unknown' in gl.observe_goals(goal_world[0], [row], 'owner', NOW)[0]


def test_unrelated_source_failure_does_not_erase_verified_goal_progress(goal_world):
    from catchup_core.ledger import FactInput
    ledger = goal_world[0]
    ledger.put_fact(FactInput('kids_school','exam_registration_deadline','2026-10-05','kid1',NOW.isoformat()))
    ledger.set_coverage('school_email','acct','complete', completed_at=NOW.isoformat(), catchup_id='c')
    ledger.set_coverage('unrelated','other','unavailable', catchup_id='c')
    row = {'id':'G','owner':'owner','title':'Register','metric':{'kind':'on_time_obligations',
           'predicate':'exam_registration_deadline','domain':'kids_school','target':2,'sources':['school_email']}}
    assert '0 of 2 obligations' in gl.observe_goals(ledger, [row], 'owner', NOW)[0]


@pytest.mark.parametrize('sprints', [
    [{'id':'s1','goal_ref':'G','status':'active','start':'2026-10-01','end':'2026-10-10'},
     {'id':'s2','goal_ref':'G','status':'active','start':'2026-10-11','end':'2026-10-20'}],
    [{'id':'s','goal_ref':'missing','status':'active','start':'2026-10-01','end':'2026-10-10'}],
    [{'id':'s','goal_ref':'G','status':'active','start':'bad','end':'2026-10-10'}],
    [{'id':'s','goal_ref':'G','status':'active','start':'2026-10-20','end':'2026-10-10'}],
])
def test_conflicting_or_invalid_sprints_are_visible_gaps(goal_world, tmp_path, sprints):
    import yaml
    from catchup_core.runtime import _goal_lines
    text = yaml.safe_dump({'goals':[{'id':'G','owner':'owner','title':'Learn','status':'active'}], 'sprints':sprints})
    got = _goal_lines(tmp_path, reader=lambda _:text, today=NOW.date(), ledger=goal_world[0], now=NOW, persist=False)
    assert got.gaps and any('Sprint' in line for line in got.lines)


def test_goal_source_scopes_do_not_share_unrelated_failures(goal_world):
    from catchup_core.ledger import FactInput
    ledger = goal_world[0]
    ledger.put_fact(FactInput('kids_school','exam_registration_deadline','2026-10-05','kid1',NOW.isoformat()))
    ledger.set_coverage('school_email','allowed','complete', completed_at=NOW.isoformat(), catchup_id='c')
    ledger.set_coverage('school_email','unrelated','unavailable', catchup_id='c')
    row = {'id':'G','title':'Register','metric':{'kind':'on_time_obligations','predicate':'exam_registration_deadline',
           'domain':'kids_school','target':2,'sources':['school_email:allowed']}}
    assert '0 of 2 obligations' in gl.observe_goals(ledger, [row], 'owner', NOW)[0]


def test_duplicate_goal_identity_cannot_overwrite_progress(goal_world, tmp_path):
    import yaml
    from catchup_core.runtime import _goal_lines
    row = {'id':'G','title':'Plan','status':'active','metric':{'kind':'milestones'}, 'milestones':[{'id':'a'}]}
    text = yaml.safe_dump({'goals':[row, dict(row,title='Different')]})
    got = _goal_lines(tmp_path, reader=lambda _:text, ledger=goal_world[0], now=NOW)
    assert got.gaps and not goal_world[0].conn.execute('SELECT 1 FROM goal_observations').fetchone()


def test_valid_sprint_is_read_once_and_does_not_create_a_gap(goal_world, tmp_path):
    import yaml
    from catchup_core.runtime import _goal_lines
    calls = []
    text = yaml.safe_dump({'goals':[{'id':'G','title':'Learn','status':'active','target_date':'2026-11-01'}],
        'sprints':[{'id':'s','goal_ref':'G','status':'active','start':'2026-10-01','end':'2026-10-20'}]})
    def read(name):
        calls.append(name)
        return text
    assert not _goal_lines(tmp_path, reader=read, ledger=goal_world[0], now=NOW, persist=False).gaps
    assert calls == ['goals']


@pytest.mark.parametrize('field,value', [('target',True),('target',2.5),('period_days',False),
    ('freshness_hours',float('nan')),('freshness_hours',-1),('sources','school_email')])
def test_invalid_metric_bounds_never_produce_progress(goal_world, field, value):
    metric = {'kind':'on_time_obligations','predicate':'exam_registration_deadline',
              'domain':'kids_school','target':2,'sources':['school_email']}
    metric[field] = value
    with pytest.raises(ValueError):
        gl.observe_goals(goal_world[0], [{'id':'G','metric':metric}], 'owner', NOW)


def test_milestone_goal_target_met_and_confirmation_versions_are_retained(goal_world):
    from catchup_core.curated import completion_key, milestone_record
    from catchup_core.contracts import Provenance
    from catchup_core.ledger import FactInput
    ledger = goal_world[0]
    row = {'id':'G','owner':'owner','title':'Finish','metric':{'kind':'milestones'},'milestones':[{'id':'a'}]}
    key = completion_key('milestone', milestone_record(row,row['milestones'][0]))
    fid = ledger.put_fact(FactInput('kids_school','commitment_completed',
        {'record_key':key,'completed':True}, 'owner', NOW.isoformat(),
        provenance=Provenance.USER_CONFIRMED, obligation_key=key))
    assert 'on track' in gl.observe_goals(ledger,[row],'owner',NOW)[0]
    stored = ledger.conn.execute('SELECT payload_encrypted FROM goal_observations').fetchone()[0]
    data = ledger._cipher.decrypt_json(stored,aad=b'goal:G')
    assert data['fact_versions'] == {fid:ledger.get_fact(fid,'owner').recorded_at}
    assert data['observation']['value'] == 1
    before = ledger.conn.execute('SELECT count(*) FROM goal_observations').fetchone()[0]
    gl.observe_goals(ledger,[dict(row,id='read-only')],'owner',NOW,persist=False)
    assert ledger.conn.execute('SELECT count(*) FROM goal_observations').fetchone()[0] == before


def test_corrected_completion_updates_goal_and_marks_archived_briefing(goal_world, tmp_path):
    from argparse import Namespace
    from catchup_core import commands, runtime, coordinator
    from catchup_core.contracts import FactStatus
    from test_release_regressions import request
    ledger, _, make, _ = goal_world
    state = tmp_path / 'state'; state.mkdir()
    (state / 'goals.md').write_text('goals:\n- id: G\n  owner: owner\n  title: Finish\n  status: active\n'
        '  metric: {kind: milestones}\n  milestones:\n  - {id: a, title: Gather}\n  - {id: b, title: Submit}\n')
    deps = make()
    args = Namespace(command='complete',record='milestone:G:a',apply=None)
    args.apply = commands.execute(args,deps,tmp_path)['apply_token']
    receipt = commands.execute(args,deps,tmp_path)
    fid = receipt['completion_reference'][5:].split('@')[0]
    deps.goal_lines = lambda: runtime._goal_lines(state,ledger=ledger,viewer='owner',now=NOW)
    first = coordinator.run_catchup(request('goal_first'),deps)
    assert '1 of 2 milestones' in first.briefing_text
    ledger.set_fact_status(fid,FactStatus.RETRACTED)
    assert ledger.conn.execute("SELECT 1 FROM correction_notices WHERE catchup_id='goal_first'").fetchone()
    second = coordinator.run_catchup(request('goal_second'),deps)
    assert '0 of 2 milestones' in second.briefing_text
    assert '1 of 2 milestones' in first.briefing_text


@pytest.mark.parametrize('sprints', [
    [{'id':'s','goal_ref':'G','status':'completed'}],
    [{'id':'s','goal_ref':'G','status':'active','start':'2026-10-01','end':'2026-10-02'}],
])
def test_valid_sprint_states_do_not_invent_conflicts(sprints):
    assert not gl.validate_sprints([{'id':'G','status':'active'}],sprints)


@pytest.mark.parametrize('sprints', [
    [{'id':'s','goal_ref':'G','status':'active','start':'2026-10-01','end':'2027-01-01'}],
    [{'goal_ref':'G','status':'active'}], [{'id':'s'},{'id':'s'}],
])
def test_sprint_target_and_identity_boundaries_are_checked(sprints):
    assert gl.validate_sprints([{'id':'G','status':'active','target_date':'2026-12-01'}],sprints)


def test_goal_evidence_cannot_change_mid_evaluation(goal_world):
    from catchup_core.ledger import FactInput, SignalInput
    ledger=goal_world[0]
    sid,_=ledger.upsert_signal(SignalInput('school_email','acct','m','v1',NOW.isoformat(),'h','ref'))
    fid=ledger.put_fact(FactInput('kids_school','exam_registration_deadline','2026-10-05','kid1',NOW.isoformat(), evidence=((sid,0,1),)))
    row={'id':'G','metric':{'kind':'on_time_obligations','predicate':'exam_registration_deadline',
         'domain':'kids_school','target':2,'sources':['school_email']}}
    with pytest.raises(ValueError,match='changed during evaluation'):
        gl.observe_goals(ledger,[row],'owner',NOW,dependencies={fid:'obsolete'})


def test_owner_can_withdraw_and_reconfirm_completion_without_replaying_old_approval(goal_world,tmp_path):
    from argparse import Namespace
    from catchup_core import commands
    from catchup_core.curated import verify_completion,completion_key,milestone_record
    state=tmp_path/'state';state.mkdir()
    row={'id':'G','owner':'owner','title':'Finish','metric':{'kind':'milestones'},'milestones':[{'id':'a'}]}
    import yaml
    (state/'goals.md').write_text(yaml.safe_dump({'goals':[row]}))
    deps=goal_world[2]()
    original=Namespace(command='complete',record='milestone:G:a',apply=None,reopen=False)
    original.apply=commands.execute(original,deps,tmp_path)['apply_token']
    commands.execute(original,deps,tmp_path)
    reopened=Namespace(command='complete',record='milestone:G:a',apply=None,reopen=True)
    reopened.apply=commands.execute(reopened,deps,tmp_path)['apply_token']
    first=commands.execute(reopened,deps,tmp_path)
    assert commands.execute(reopened,deps,tmp_path)==first
    assert not verify_completion(goal_world[0],'owner',None,completion_key('milestone',milestone_record(row,row['milestones'][0])))
    assert '0 of 1 milestones' in gl.observe_goals(goal_world[0],[row],'owner',NOW)[0]
    with pytest.raises(ValueError,match='changed'):
        commands.execute(original,deps,tmp_path)
    original.apply=None
    original.apply=commands.execute(original,deps,tmp_path)['apply_token']
    commands.execute(original,deps,tmp_path)
    assert '1 of 1 milestones' in gl.observe_goals(goal_world[0],[row],'owner',NOW)[0]


def test_competing_process_update_cannot_overwrite_reviewed_fact(goal_world):
    import subprocess
    import sys
    from pathlib import Path
    from catchup_core.ledger import FactInput, LedgerError
    ledger = goal_world[0]
    fact = FactInput('kids_school', 'exam_registration_deadline', '2026-10-05', 'kid1', NOW.isoformat())
    fid = ledger.put_fact(fact)
    reviewed = ledger.get_fact(fid, 'owner').recorded_at
    root = Path(__file__).resolve().parents[2]
    code = """import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from catchup_core.ledger import Ledger, FactInput
from test_release_regressions import KEY, CIPHER, NOW
with Ledger(sys.argv[3], id_key=KEY, cipher=CIPHER).open() as ledger:
    ledger.put_fact(FactInput('kids_school', 'exam_registration_deadline', '2026-10-06', 'kid1', NOW.isoformat()), sys.argv[4])
"""
    child = subprocess.run([sys.executable, '-c', code, str(root/'scripts'), str(root/'tests/catchup_core'),
                            str(ledger._path), fid], capture_output=True, timeout=15)
    assert child.returncode == 0, child.stderr.decode()
    with pytest.raises(LedgerError, match='changed since review'):
        ledger.put_fact(fact, fid, expected_recorded_at=reviewed)
    assert ledger.get_fact(fid, 'owner').value == '2026-10-06'
    assert ledger.conn.execute('SELECT count(*) FROM fact_versions WHERE fact_id=?', (fid,)).fetchone()[0] == 2


def test_rolling_goal_window_contains_exactly_the_authored_number_of_dates():
    goal = gl.AuthoredGoal('G','owner','Today','exam_registration_deadline',1,period_days=1)
    facts = [fv('yesterday', '2026-10-11'), fv('today', '2026-10-12')]
    observation = gl.observe_on_time(goal, facts, {'yesterday':date(2026,10,11)}, NOW, last_verified=NOW)
    assert observation.period_start == observation.period_end == NOW.date()
    assert observation.value == 0 and observation.source_fact_ids == ('today',)


def test_goal_cannot_count_completion_from_an_unselected_account(goal_world):
    from catchup_core.ledger import FactInput, SignalInput
    ledger = goal_world[0]
    sid, _ = ledger.upsert_signal(SignalInput('school_email','unselected','m','v1',NOW.isoformat(),'h','ref'))
    fid = ledger.put_fact(FactInput('kids_school','exam_registration_deadline','2026-10-05','kid1',NOW.isoformat(),
                                    evidence=((sid,0,1),)))
    alert, _ = ledger.upsert_alert('unselected-obligation',domain='kids_school',primary_subject_id='kid1',
                                    severity='P2',fact_ids=(fid,))
    ledger.put_open_item('oi', owner='kid1', description={'title':'Register'}, state='done', source_alert_id=alert)
    ledger.conn.execute("UPDATE open_items SET resolution_evidence=?,updated_at=? WHERE item_id='oi'",
                        ('verified','2026-10-04T00:00:00+00:00'))
    ledger.set_coverage('school_email','allowed','complete',completed_at=NOW.isoformat(),catchup_id='c')
    goal = {'id':'G','metric':{'kind':'on_time_obligations','predicate':'exam_registration_deadline',
                              'domain':'kids_school','target':1,'sources':['school_email:allowed']}}
    assert '0 of 1 obligations' in gl.observe_goals(ledger,[goal],'owner',NOW)[0]


def test_goal_domain_change_requires_fresh_milestone_confirmation(goal_world):
    from catchup_core.curated import completion_key, milestone_record
    from catchup_core.contracts import Provenance
    from catchup_core.ledger import FactInput
    ledger = goal_world[0]
    goal = {'id':'G','owner':'owner','domain':'kids_school','metric':{'kind':'milestones'},'milestones':[{'id':'a'}]}
    key = completion_key('milestone',milestone_record(goal,goal['milestones'][0]))
    ledger.put_fact(FactInput('kids_school','commitment_completed',{'record_key':key,'completed':True},
        'owner',NOW.isoformat(),provenance=Provenance.USER_CONFIRMED,obligation_key=key))
    assert '1 of 1 milestones' in gl.observe_goals(ledger,[goal],'owner',NOW)[0]
    goal['domain']='finance'
    assert '0 of 1 milestones' in gl.observe_goals(ledger,[goal],'owner',NOW)[0]


def test_owner_correction_keeps_original_source_scope_for_goal_recomputation(goal_world):
    from catchup_core import views
    from catchup_core.ledger import FactInput, SignalInput
    ledger = goal_world[0]
    sid,_ = ledger.upsert_signal(SignalInput('school_email','allowed','m','v1',NOW.isoformat(),'h','ref'))
    fid = ledger.put_fact(FactInput('kids_school','exam_registration_deadline','2026-10-05','kid1',NOW.isoformat(),
                                    evidence=((sid,0,1),)))
    ledger.set_coverage('school_email','allowed','complete',completed_at=NOW.isoformat(),catchup_id='c')
    fact = ledger.get_fact(fid,'owner')
    views.correct(ledger,'owner',fid,'2026-10-06',fact.recorded_at)
    goal = {'id':'G','metric':{'kind':'on_time_obligations','predicate':'exam_registration_deadline',
                              'domain':'kids_school','target':1,'sources':['school_email:allowed']}}
    dependencies = {}
    gl.observe_goals(ledger,[goal],'owner',NOW,dependencies=dependencies)
    assert dependencies == {fid:ledger.get_fact(fid,'owner').recorded_at}


def test_reopen_without_completion_requires_evidence_and_expired_preview_cannot_apply(goal_world,tmp_path):
    from argparse import Namespace
    from datetime import timedelta
    from catchup_core import commands
    state=tmp_path/'state';state.mkdir()
    (state/'goals.md').write_text('goals:\n- id: G\n  owner: owner\n  milestones: [{id: a}]\n')
    deps=goal_world[2]()
    args=Namespace(command='complete',record='milestone:G:a',reopen=True,apply=None)
    with pytest.raises(ValueError,match='no completion'):
        commands.execute(args,deps,tmp_path)
    args.reopen=False
    args.apply=commands.execute(args,deps,tmp_path)['apply_token']
    deps.now=lambda: NOW+timedelta(minutes=31)
    with pytest.raises(ValueError,match='expired'):
        commands.execute(args,deps,tmp_path)
    args.apply='bad-token'
    with pytest.raises(ValueError,match='review again'):
        commands.execute(args,deps,tmp_path)
    assert not goal_world[0].conn.execute('SELECT 1 FROM facts').fetchone()


def test_derived_goal_observation_cannot_persist_without_encryption(goal_world):
    ledger=goal_world[0]
    cipher=ledger._cipher
    ledger._cipher=None
    try:
        row={'id':'G','metric':{'kind':'milestones'},'milestones':[{'id':'a'}]}
        with pytest.raises(ValueError,match='require encryption'):
            gl.observe_goals(ledger,[row],'owner',NOW)
        assert not ledger.conn.execute('SELECT 1 FROM goal_observations').fetchone()
    finally:
        ledger._cipher=cipher


def test_invalid_metric_is_a_visible_gap_without_a_saved_observation(goal_world,tmp_path):
    import yaml
    from catchup_core.runtime import _goal_lines
    text=yaml.safe_dump({'goals':[{'id':'G','title':'Register','status':'active',
        'metric':{'kind':'on_time_obligations','domain':'kids_school','predicate':'exam_registration_deadline','target':False}}]})
    result=_goal_lines(tmp_path,reader=lambda _:text,ledger=goal_world[0],viewer='owner',now=NOW)
    assert result.gaps and any('observation unknown' in line for line in result.lines)
    assert not goal_world[0].conn.execute('SELECT 1 FROM goal_observations').fetchone()
