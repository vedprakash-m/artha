"""RA-11: quantity and habit progress are derived only from scoped dated evidence."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from catchup_core import goals
from catchup_core.contracts import FactStatus, ProcessingState, Provenance
from catchup_core.ledger import FactInput, SignalInput
from test_release_regressions import NOW


@pytest.fixture
def world(tmp_path):
    from test_release_regressions import world as fixture

    yield from fixture.__wrapped__(tmp_path)


def metric(kind="outcome", **changes):
    definition = {
        "kind": kind,
        "domain": "goals",
        "predicate": "goal_quantity" if kind == "outcome" else "goal_checkin",
        "subject": "owner",
        "sources": ["capture:owner"],
        "period_days": 7,
        "freshness_hours": 48,
        "target": "150.00" if kind == "outcome" else 3,
    }
    if kind == "outcome":
        definition.update(unit="USD", baseline="100.00", comparison="at_least")
    definition.update(changes)
    return {"id": "G1", "owner": "owner", "title": "Practice or save", "status": "active", "metric": definition}


def evidence(ledger, text, *, predicate="goal_quantity", scope="owner", status=FactStatus.CURRENT, subject="owner",
             provenance=Provenance.VERIFIED_SOURCE, observed_at=None):
    sid, _ = ledger.upsert_signal(
        SignalInput(
            "capture",
            scope,
            "entry-" + str(ledger.conn.total_changes),
            "v1",
            NOW.isoformat(),
            "h-" + str(ledger.conn.total_changes),
            "synthetic",
        )
    )
    fid = ledger.put_fact(
        FactInput(
            "goals", predicate, text, subject, observed_at or NOW.isoformat(),
            evidence=((sid, 0, len(text.encode())),), status=status,
            provenance=provenance
        )
    )
    ledger.set_processing_state(sid, ProcessingState.RECONCILED)
    return fid


def coverage(ledger, **changes):
    ledger.set_coverage(
        "capture",
        "owner",
        changes.get("status", "complete"),
        catchup_id="c",
        completed_at=changes.get("completed_at", NOW.isoformat()),
    )


def payload(ledger):
    row = ledger.conn.execute("SELECT payload_encrypted FROM goal_observations WHERE goal_id='G1'").fetchone()
    assert row
    return ledger._cipher.decrypt_json(row[0], aad=b"goal:G1")


@pytest.mark.parametrize('due,expected', [
    ('2026-10-12', 'due today'), ('2026-10-11', 'overdue by 1 day'),
    ('2026-10-14', 'due in 2 days'), (NOW.date(), 'due today'),
    ('bad date', 'date unreadable'), ('2026-10-12junk', 'date unreadable'),
    (20261012, 'date unreadable'), (True, 'date unreadable'), ('', 'date unreadable'), (None, '')])
def test_metric_goal_keeps_its_authored_next_action_and_counts_one_goal(world, tmp_path, due, expected):
    import yaml
    from catchup_core.runtime import _goal_lines
    ledger = world[0]
    coverage(ledger)
    fid = evidence(ledger, 'G1 2026-10-12 125.25 USD')
    row = metric()
    row.update(next_action='Review the plan', next_action_date=due)
    lines = _goal_lines(tmp_path, reader=lambda _: yaml.safe_dump({'goals': [row]}),
                        ledger=ledger, viewer='owner', now=NOW, today=NOW.date(), persist=False)
    assert any('125.25 USD' in line for line in lines.lines)
    action = next(line for line in lines.lines if 'Review the plan' in line)
    assert expected in action and 'authored next action' in action
    assert lines.commitment_count == 1 and lines.fact_versions == {fid: ledger.get_fact(fid, 'owner').recorded_at}
    if expected == 'date unreadable':
        assert lines.gaps


@pytest.mark.parametrize('value', [True, 21, {}, ['Review']])
def test_malformed_metric_next_action_is_a_review_gap(world, tmp_path, value):
    import yaml
    from catchup_core.runtime import _goal_lines
    coverage(world[0])
    evidence(world[0], 'G1 2026-10-12 125.25 USD')
    row = metric()
    row['next_action'] = value
    lines = _goal_lines(tmp_path, reader=lambda _: yaml.safe_dump({'goals': [row]}),
                        ledger=world[0], viewer='owner', now=NOW, today=NOW.date(), persist=False)
    assert lines.gaps and any('next action needs review' in line for line in lines.lines)
    assert lines.commitment_count == 1


def test_missing_metric_next_action_is_explicit_in_the_shared_goals_command(world, tmp_path):
    from argparse import Namespace
    import yaml
    from catchup_core.commands import execute
    ledger = world[0]
    coverage(ledger)
    evidence(ledger, 'G1 2026-10-12 125.25 USD')
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'goals.md').write_text(yaml.safe_dump({'goals': [metric()]}))
    result = execute(Namespace(command='goals'), world[2](), tmp_path)
    assert any('next action unknown' in line for line in result['observations'])
    assert any('125.25 USD' in line for line in result['observations'])


def test_briefing_keeps_verified_metric_and_authored_action_with_exact_dependencies(world, tmp_path):
    import yaml
    from catchup_core import coordinator, runtime
    from test_release_regressions import request
    ledger, blobs, make, _ = world
    text = 'G1 2026-10-12 125.25 USD'
    coverage(ledger)
    fid = evidence(ledger, text)
    ref = blobs.put('goal-metric-example', text)
    ledger.conn.execute('UPDATE signals SET evidence_blob_ref=? WHERE signal_id IN '
                        '(SELECT signal_id FROM fact_evidence WHERE fact_id=?)', (ref, fid))
    row = metric()
    row.update(next_action='Review the plan', next_action_date='2026-10-12')
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'goals.md').write_text(yaml.safe_dump({'goals': [row]}))
    deps = make()
    deps.goal_lines = lambda: runtime._goal_lines(state, ledger=ledger, viewer='owner', now=NOW, today=NOW.date())
    result = coordinator.run_catchup(request(), deps)
    assert result.briefing_id and '125.25 USD' in result.briefing_text
    assert 'Review the plan (due today)' in result.briefing_text
    assert 'authored next action' in result.briefing_text
    assert ledger.conn.execute('SELECT 1 FROM briefing_facts WHERE fact_id=?', (fid,)).fetchone()


def test_nonmetric_goal_uses_the_same_strict_date_and_action_review(tmp_path):
    import yaml
    from catchup_core.runtime import _goal_lines
    row = {'id': 'G', 'title': 'Plan', 'status': 'active', 'next_action': True,
           'next_action_date': '2026-10-12junk'}
    result = _goal_lines(tmp_path, reader=lambda _: yaml.safe_dump({'goals': [row]}), today=NOW.date())
    assert 'next action needs review (date unreadable)' in result.lines[0]
    assert len(result.gaps) == 2 and result.commitment_count == 1


def test_outcome_progress_preserves_precision_unit_and_authored_baseline(world):
    ledger = world[0]
    coverage(ledger)
    fid = evidence(ledger, "G1 2026-10-12 125.25 USD")
    row = metric()
    lines = goals.observe_goals(ledger, [row], "owner", NOW)
    assert "125.25 USD" in lines[0] and "50.5%" in lines[0] and "in progress" in lines[0]
    stored = payload(ledger)
    assert Decimal(stored["observation"]["value"]) == Decimal("125.25")
    assert stored["observation"]["unit"] == "USD" and stored["observation"]["baseline"] == "100.00"
    assert stored["fact_versions"] == {fid: ledger.get_fact(fid, "owner").recorded_at}
    assert row["metric"]["target"] == "150.00" and row["metric"]["baseline"] == "100.00"


@pytest.mark.parametrize("kind", ["outcome", "habit"])
def test_future_observation_cannot_establish_goal_progress(world, kind):
    ledger = world[0]
    coverage(ledger)
    row = metric(kind, period_days=1, target="150" if kind == "outcome" else 1)
    evidence(ledger, "G1 2026-10-12 " + ("200 USD" if kind == "outcome" else "done"),
             predicate=row["metric"]["predicate"], observed_at=(NOW + timedelta(seconds=1)).isoformat())
    lines = goals.observe_goals(ledger, [row], "owner", NOW, weekly=True)
    assert "unknown" in lines[0]
    stored = payload(ledger)
    assert stored["observation"]["value"] is None
    assert stored["observation"]["weekly"]["delta"] is None


def test_future_observation_in_an_unrelated_goal_does_not_hide_verified_progress(world):
    ledger = world[0]
    coverage(ledger)
    evidence(ledger, "G2 2026-10-12 200 USD", observed_at=(NOW + timedelta(seconds=1)).isoformat())
    evidence(ledger, "G1 2026-10-12 150 USD")
    assert "on track" in goals.observe_goals(ledger, [metric()], "owner", NOW)[0]


@pytest.mark.parametrize("kind", ["outcome", "habit"])
def test_future_coverage_cannot_hide_behind_an_older_complete_source(world, kind):
    ledger = world[0]
    coverage(ledger)
    ledger.set_coverage("capture", "second", "complete", catchup_id="c",
                        completed_at=(NOW + timedelta(seconds=1)).isoformat())
    row = metric(kind, period_days=1, target="150" if kind == "outcome" else 1,
                 sources=["capture:owner", "capture:second"])
    evidence(ledger, "G1 2026-10-12 " + ("200 USD" if kind == "outcome" else "done"),
             predicate=row["metric"]["predicate"])
    assert "unknown" in goals.observe_goals(ledger, [row], "owner", NOW)[0]


def test_future_observation_stays_unknown_through_commands_and_briefing_until_its_time(world, tmp_path):
    from argparse import Namespace

    import yaml

    from catchup_core import commands, coordinator, runtime
    from test_release_regressions import request

    ledger, blobs, make, _ = world
    coverage(ledger)
    text = "G1 2026-10-12 200 USD"
    fid = evidence(ledger, text, observed_at=(NOW + timedelta(seconds=1)).isoformat())
    ref = blobs.put("future-goal-observation", text)
    ledger.conn.execute("UPDATE signals SET evidence_blob_ref=? WHERE signal_id IN "
                        "(SELECT signal_id FROM fact_evidence WHERE fact_id=?)", (ref, fid))
    state = tmp_path / "state"
    state.mkdir()
    path = state / "goals.md"
    path.write_text(yaml.safe_dump({"goals": [metric()]}))
    before = path.read_bytes()
    deps = make()
    deps.goal_lines = lambda: runtime._goal_lines(state, ledger=ledger, viewer="owner",
                                                  now=deps.now(), today=deps.now().date())
    first = commands.execute(Namespace(command="goals", weekly=True), deps, tmp_path)
    assert any("unknown" in line for line in first["observations"])
    result = coordinator.run_catchup(request("future_goal"), deps)
    assert result.briefing_id and "Practice or save: unknown" in result.briefing_text
    assert "200 USD verified" not in result.briefing_text
    deps.now = lambda: NOW + timedelta(seconds=2)
    later = coordinator.run_catchup(request("observed_goal"), deps)
    assert later.briefing_id and "200 USD verified" in later.briefing_text
    assert path.read_bytes() == before


def test_reduced_quantity_and_overdue_target_are_assessed_without_invented_pace(world):
    ledger = world[0]
    coverage(ledger)
    evidence(ledger, "G1 2026-10-12 82.5 kg")
    row = metric(unit="kg", baseline="90", target="80", comparison="at_most")
    assert "75.0%" in goals.observe_goals(ledger, [row], "owner", NOW)[0]
    row["target_date"] = "2026-10-01"
    assert "at risk" in goals.observe_goals(ledger, [row], "owner", NOW)[0]
    row["metric"]["target"] = "85"
    assert "on track" in goals.observe_goals(ledger, [row], "owner", NOW)[0]


@pytest.mark.parametrize(
    "text",
    [
        "G1 2026-10-12 125 EUR",
        "G1 2026-10-12 NaN USD",
        "G1 2026-10-12 1e999999 USD",
        "G1 2026-10-13 200 USD",
        "G1 2026-10-12 maybe 200 USD",
    ],
)
def test_invalid_ambiguous_or_future_quantity_never_marks_goal_on_track(world, text):
    ledger = world[0]
    coverage(ledger)
    evidence(ledger, text)
    assert "unknown" in goals.observe_goals(ledger, [metric()], "owner", NOW)[0]


def test_missing_stale_unselected_or_conflicting_quantity_is_unknown(world):
    ledger = world[0]
    coverage(ledger)
    assert "unknown" in goals.observe_goals(ledger, [metric()], "owner", NOW)[0]
    evidence(ledger, "G1 2026-10-12 200 USD", scope="unselected")
    assert "unknown" in goals.observe_goals(ledger, [metric()], "owner", NOW)[0]
    first = evidence(ledger, "G1 2026-10-12 150 USD")
    evidence(ledger, "G1 2026-10-12 160 USD")
    assert "unknown" in goals.observe_goals(ledger, [metric()], "owner", NOW)[0]
    ledger.set_fact_status(first, FactStatus.RETRACTED)
    assert "on track" in goals.observe_goals(ledger, [metric()], "owner", NOW)[0]
    coverage(ledger, completed_at=(NOW - timedelta(days=3)).isoformat())
    assert "unknown" in goals.observe_goals(ledger, [metric()], "owner", NOW)[0]


def test_habit_requires_every_expected_day_and_deduplicates_same_day(world):
    ledger = world[0]
    coverage(ledger)
    row = metric("habit", period_days=3, target=2)
    evidence(ledger, "G1 2026-10-10 done", predicate="goal_checkin")
    evidence(ledger, "G1 2026-10-10 done", predicate="goal_checkin")
    evidence(ledger, "G1 2026-10-11 skipped", predicate="goal_checkin")
    assert "unknown" in goals.observe_goals(ledger, [row], "owner", NOW)[0]
    evidence(ledger, "G1 2026-10-12 done", predicate="goal_checkin")
    assert "on track" in goals.observe_goals(ledger, [row], "owner", NOW)[0]
    assert payload(ledger)["observation"]["value"] == 2
    evidence(ledger, "G1 2026-10-12 skipped", predicate="goal_checkin")
    assert "unknown" in goals.observe_goals(ledger, [row], "owner", NOW)[0]


def test_habit_explicit_skips_are_verified_zero_and_other_goal_is_excluded(world):
    ledger = world[0]
    coverage(ledger)
    evidence(ledger, "G1 2026-10-12 skipped", predicate="goal_checkin")
    evidence(ledger, "G2 2026-10-12 done", predicate="goal_checkin")
    assert "at risk" in goals.observe_goals(ledger, [metric("habit", period_days=1, target=1)], "owner", NOW)[0]
    assert payload(ledger)["observation"]["value"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"comparison": "guess"},
        {"unit": ""},
        {"target": True},
        {"target": "NaN"},
        {"baseline": "150.00"},
        {"freshness_hours": float("inf")},
        {"period_days": False},
    ],
)
def test_invalid_outcome_definition_needs_review(world, change):
    with pytest.raises(ValueError):
        goals.observe_goals(world[0], [metric(**change)], "owner", NOW)


def test_source_scope_revocation_hides_quantity_and_derived_progress(world):
    from catchup_core.ledger import PermissionDenied
    ledger=world[0];coverage(ledger)
    ledger._consent_subjects=frozenset({'adult'})
    ledger.grant_consent('adult','adult','goals',['view'],'owner')
    grant=ledger.grant_consent('adult','adult','capture:owner',['view'],'owner')
    fid=evidence(ledger,'G1 2026-10-12 200 USD',subject='adult')
    row=metric(subject='adult')
    assert 'on track' in goals.observe_goals(ledger,[row],'owner',NOW)[0]
    ledger.revoke_consent(grant)
    with pytest.raises(PermissionDenied):
        ledger.get_fact(fid,'owner')
    assert 'unknown' in goals.observe_goals(ledger,[row],'owner',NOW)[0]


def test_future_coverage_timestamp_cannot_make_a_quantity_current(world):
    ledger=world[0];coverage(ledger,completed_at=(NOW+timedelta(days=1)).isoformat())
    evidence(ledger,'G1 2026-10-12 200 USD')
    assert 'unknown' in goals.observe_goals(ledger,[metric()],'owner',NOW)[0]


def test_goal_pack_capture_correction_and_forget_update_the_briefing(world,tmp_path):
    import json
    import yaml
    from pathlib import Path
    from catchup_core import coordinator,domain_pack,runtime,views
    from test_release_regressions import request
    ledger,_,make,published=world
    pack=domain_pack.load_pack_file(Path(domain_pack.__file__).parent/'packs/goals.yaml')
    text='G1 2026-10-12 125.25 USD'
    row=metric()
    authored=yaml.safe_dump({'goals':[row]})
    deps=make()
    deps.pack=pack
    deps.subjects=['owner']
    deps.source_class={'capture':'parent_note'}
    deps.fetchers={'capture':lambda:coordinator.ConnectorFetch('capture','owner',
        (coordinator.ConnectorRecord('m1',text,'v1'),))}
    deps.extractor=lambda _:json.dumps({'complete':True,'candidates':[{'predicate':'goal_quantity',
        'subject_id':'owner','value':text,'quote':text,'span':[0,len(text.encode())]}]})
    deps.goal_lines=lambda:runtime._goal_lines(tmp_path,reader=lambda _:authored,ledger=ledger,viewer='owner',now=NOW)
    first=coordinator.run_catchup(request('quantity_first'),deps)
    assert '125.25 USD' in first.briefing_text and first.artifact_ids==()
    fact=ledger.facts_for('goals','goal_quantity','owner')[0]
    assert ledger.conn.execute("SELECT 1 FROM briefing_facts WHERE catchup_id='quantity_first' AND fact_id=?",
                               (fact.fact_id,)).fetchone()
    views.correct(ledger,'owner',fact.fact_id,'G1 2026-10-12 150.00 USD',fact.recorded_at)
    second=coordinator.run_catchup(request('quantity_corrected'),deps)
    assert 'on track' in second.briefing_text and '150.00 USD' in second.briefing_text
    assert ledger.conn.execute("SELECT 1 FROM correction_notices WHERE catchup_id='quantity_first'").fetchone()
    ledger.forget('capture','owner','m1')
    assert not ledger.conn.execute('SELECT 1 FROM goal_observations').fetchone()
    assert 'unknown' in goals.observe_goals(ledger,[row],'owner',NOW)[0]


def test_goal_progress_has_a_resolvable_compact_citation(world,tmp_path):
    import re
    import yaml
    from catchup_core import coordinator,runtime
    from test_release_regressions import request
    ledger,_,make,_=world
    coverage(ledger)
    evidence(ledger,'G1 2026-10-12 125.25 USD')
    text=yaml.safe_dump({'goals':[metric()]})
    deps=make()
    deps.goal_lines=lambda:runtime._goal_lines(tmp_path,reader=lambda _:text,ledger=ledger,now=NOW)
    result=coordinator.run_catchup(request('quantity_cited'),deps)
    progress=next(line for line in result.briefing_text.splitlines() if '125.25 USD' in line)
    assert re.search(r'\[\d+\]',progress) and '\nEvidence\n' in result.briefing_text


def test_large_precise_decimal_does_not_round_through_binary_float(world):
    ledger=world[0];coverage(ledger)
    quantity='15000000000000000000.0000000001'
    evidence(ledger,f'G1 2026-10-12 {quantity} USD')
    row=metric(baseline='10000000000000000000.0000000000',target='20000000000000000000.0000000000')
    goals.observe_goals(ledger,[row],'owner',NOW)
    assert payload(ledger)['observation']['value']==quantity


def test_untrusted_goal_title_cannot_invent_a_resolving_citation(world,tmp_path):
    import yaml
    from catchup_core import coordinator,runtime
    from test_release_regressions import request
    ledger=world[0];coverage(ledger);evidence(ledger,'G1 2026-10-12 125 USD')
    row=metric();row['title']='A misleading reference [999]'
    deps=world[2]()
    deps.goal_lines=lambda:runtime._goal_lines(tmp_path,reader=lambda _:yaml.safe_dump({'goals':[row]}),ledger=ledger,now=NOW)
    result=coordinator.run_catchup(request('untrusted_goal_reference'),deps)
    assert '[999]' not in result.briefing_text and '(reference 999)' in result.briefing_text


@pytest.mark.parametrize('change', [
    {'kind': 'unsupported'}, {'domain': ''}, {'predicate': ''}, {'subject': ''}, {'sources': []},
    {'sources': ['']}, {'sources': ['x' * 257]}, {'sources': 'capture'},
])
def test_canonical_metric_definition_rejects_incomplete_scopes(change):
    from catchup_core.goal_metrics import validate_definition
    row = metric(**change)
    with pytest.raises(ValueError):
        validate_definition(row)


def test_canonical_metric_definition_requires_owner_and_accepts_outcome_and_habit():
    from catchup_core.goal_metrics import validate_definition
    validate_definition(metric())
    validate_definition(metric('habit'))
    row = metric()
    row['owner'] = ''
    with pytest.raises(ValueError, match='owner'):
        validate_definition(row)
