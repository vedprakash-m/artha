"""
tests/unit/test_goals_writer.py — Tests for scripts/goals_writer.py

Coverage:
  - create_goal: adds new goal to empty goals list
  - create_goal: adds new goal to existing goals list
  - create_goal: assigns correct fields
  - create_goal: adds metric sub-block when --metric-current/target provided
  - create_goal: rejects duplicate ID
  - update_goal: updates a scalar field (status)
  - update_goal: updates metric.current
  - update_goal: parks a goal (sets status=parked, parked_since, parked_reason)
  - update_goal: returns False for unknown goal ID
  - write uses atomic write (file is valid YAML after write)
  - Schema preserved: other goals untouched when updating one
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import goals_writer  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_goals_file(goals: list[dict], tmp_path: Path) -> Path:
    """Write a minimal goals.md with given goals list, return path."""
    fm = {
        "schema_version": "2.0",
        "domain": "goals",
        "last_updated": "2026-03-28",
        "goals": goals,
    }
    p = tmp_path / "goals.md"
    p.write_text("---\n" + yaml.dump(fm) + "---\n\n# Goals\n")
    return p


def _read_goals(p: Path) -> list[dict]:
    import re
    content = p.read_text()
    fm_m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
    if not fm_m:
        return []
    fm = yaml.safe_load(fm_m.group(1)) or {}
    return fm.get("goals", [])


BASE_GOAL = {
    "id": "G-001",
    "title": "Summit Mailbox Peak",
    "type": "milestone",
    "category": "fitness",
    "status": "active",
    "next_action": "Register for hike",
    "last_progress": "2026-03-15",
    "created": "2026-01-01",
    "target_date": "2026-08-15",
    "leading_indicators": [],
}


# ---------------------------------------------------------------------------
# create_goal tests
# ---------------------------------------------------------------------------

class TestCreateGoal:
    def test_creates_goal_in_empty_list(self, tmp_path):
        p = _write_goals_file([], tmp_path)
        result = goals_writer.create_goal(p, {
            "id": "G-001",
            "title": "New Goal",
            "type": "milestone",
            "category": "fitness",
            "status": "active",
            "created": "2026-03-28",
        })
        assert result == 0
        goals = _read_goals(p)
        assert len(goals) == 1
        assert goals[0]["id"] == "G-001"
        assert goals[0]["title"] == "New Goal"

    def test_creates_goal_alongside_existing(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.create_goal(p, {
            "id": "G-002",
            "title": "Lose Weight",
            "type": "outcome",
            "category": "health",
            "status": "active",
            "created": "2026-03-28",
        })
        assert result == 0
        goals = _read_goals(p)
        assert len(goals) == 2
        assert goals[1]["id"] == "G-002"

    def test_creates_goal_with_metric(self, tmp_path):
        p = _write_goals_file([], tmp_path)
        goals_writer.create_goal(p, {
            "id": "G-002",
            "title": "Lose Weight",
            "type": "outcome",
            "category": "health",
            "status": "active",
            "created": "2026-03-28",
            "metric_current": 183.0,
            "metric_target": 160.0,
            "metric_unit": "lb",
            "metric_direction": "down",
        })
        goals = _read_goals(p)
        assert goals[0]["metric"]["current"] == 183.0
        assert goals[0]["metric"]["target"] == 160.0
        assert goals[0]["metric"]["direction"] == "down"

    def test_rejects_duplicate_id(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.create_goal(p, {
            "id": "G-001",
            "title": "Duplicate",
            "type": "milestone",
            "category": "other",
            "status": "active",
            "created": "2026-03-28",
        })
        assert result != 0
        goals = _read_goals(p)
        assert len(goals) == 1  # unchanged


# ---------------------------------------------------------------------------
# update_goal tests
# ---------------------------------------------------------------------------

class TestUpdateGoal:
    def test_updates_status(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.update_goal(p, "G-001", {"status": "done"})
        assert result == 0
        goals = _read_goals(p)
        assert goals[0]["status"] == "done"

    def test_updates_metric_current(self, tmp_path):
        goal = dict(BASE_GOAL)
        goal["metric"] = {"current": 183.0, "target": 160, "unit": "lb", "direction": "down"}
        p = _write_goals_file([goal], tmp_path)
        result = goals_writer.update_goal(p, "G-001", {"metric_current": 179.5})
        assert result == 0
        goals = _read_goals(p)
        assert goals[0]["metric"]["current"] == 179.5

    def test_parks_goal_with_reason(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.update_goal(p, "G-001", {
            "status": "parked",
            "parked_reason": "Schedule conflict",
        })
        assert result == 0
        goals = _read_goals(p)
        assert goals[0]["status"] == "parked"
        assert goals[0]["parked_reason"] == "Schedule conflict"
        assert "parked_since" in goals[0]

    def test_returns_false_for_unknown_id(self, tmp_path):
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        result = goals_writer.update_goal(p, "G-999", {"status": "done"})
        assert result != 0

    def test_other_goals_untouched(self, tmp_path):
        g2 = dict(BASE_GOAL)
        g2["id"] = "G-002"
        g2["title"] = "Other Goal"
        p = _write_goals_file([dict(BASE_GOAL), g2], tmp_path)
        goals_writer.update_goal(p, "G-001", {"status": "done"})
        goals = _read_goals(p)
        other = next(g for g in goals if g["id"] == "G-002")
        assert other["title"] == "Other Goal"
        assert other["status"] == "active"


# ---------------------------------------------------------------------------
# Atomic write test
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_file_valid_yaml_after_write(self, tmp_path):
        import re
        p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
        goals_writer.update_goal(p, "G-001", {"status": "done"})
        content = p.read_text()
        fm_m = re.match(r"---\s*\n(.*?)\n---", content, re.DOTALL)
        assert fm_m is not None, "File must have valid YAML frontmatter after write"
        fm = yaml.safe_load(fm_m.group(1))
        assert isinstance(fm, dict)
        assert isinstance(fm.get("goals"), list)


def _dated_definition():
    return {'kind':'outcome','domain':'goals','predicate':'goal_quantity','subject':'owner','sources':['capture:owner'],
            'target':'150.00','baseline':'100.00','unit':'USD','comparison':'at_least','period_days':30,'freshness_hours':48}


def test_source_metric_definition_uses_canonical_writer_and_preserves_body(tmp_path):
    p=_write_goals_file([],tmp_path)
    original_body=goals_writer._split_frontmatter(p.read_text())[1]
    definition=_dated_definition()
    assert goals_writer.create_goal(p,{'id':'G1','title':'Save','type':'outcome','category':'finance',
        'owner':'owner','metric_definition':definition})==0
    goal=_read_goals(p)[0]
    assert goal['owner']=='owner' and goal['metric']==definition
    assert goals_writer._split_frontmatter(p.read_text())[1]==original_body
    assert goals_writer.update_goal(p,'G1',{'metric_definition':dict(definition,target='175.00')})==0
    assert _read_goals(p)[0]['metric']['target']=='175.00'
    assert goals_writer._split_frontmatter(p.read_text())[1]==original_body


@pytest.mark.parametrize('change',[{'owner':''},{'metric_definition':{'kind':'outcome','target':True}},
    {'metric_definition':{'kind':'outcome','unit':'USD','surprise':True}}])
def test_invalid_source_metric_definition_never_changes_authored_goal(tmp_path,change):
    p=_write_goals_file([dict(BASE_GOAL,owner='owner')],tmp_path)
    before=p.read_bytes()
    fields={'metric_definition':_dated_definition(),**change}
    assert goals_writer.update_goal(p,'G-001',fields)==2
    assert p.read_bytes()==before


def test_goal_title_with_frontmatter_characters_is_valid_data(tmp_path):
    p=_write_goals_file([],tmp_path)
    assert goals_writer.create_goal(p,{'id':'G1','title':'A plan: --- keep the body','type':'outcome','category':'learning'})==0
    assert _read_goals(p)[0]['title']=='A plan: --- keep the body'


def test_malformed_frontmatter_cannot_be_overwritten_as_an_empty_goal_list(tmp_path):
    p=tmp_path/'goals.md';p.write_text('---\ngoals: [unterminated\n---\n# Keep this body\n')
    original=p.read_bytes()
    assert goals_writer.create_goal(p,{'id':'G1','title':'New','type':'outcome','category':'learning'})==2
    assert p.read_bytes()==original

@pytest.fixture(autouse=True)
def private_writer_locks(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))


@pytest.mark.parametrize('change', [
    {'kind': 'other'}, {'domain': ''}, {'subject': ''}, {'sources': []}, {'sources': ['']},
])
def test_full_source_definition_is_validated_before_publication(tmp_path, change):
    p = _write_goals_file([], tmp_path)
    original = p.read_bytes()
    definition = dict(_dated_definition(), **change)
    assert goals_writer.create_goal(p, {'id': 'G1', 'title': 'Save', 'type': 'outcome', 'category': 'finance',
                                       'owner': 'owner', 'metric_definition': definition}) == 2
    assert p.read_bytes() == original


def test_source_metric_requires_owner_and_cannot_accept_manual_current(tmp_path):
    p = _write_goals_file([], tmp_path)
    fields = {'id': 'G1', 'title': 'Save', 'type': 'outcome', 'category': 'finance',
              'metric_definition': _dated_definition()}
    assert goals_writer.create_goal(p, fields) == 2
    assert goals_writer.create_goal(p, dict(fields, owner='owner')) == 0
    original = p.read_bytes()
    assert goals_writer.update_goal(p, 'G1', {'metric_current': '125'}) == 2
    assert p.read_bytes() == original


def test_cli_saves_source_definition_and_refuses_unknown_flags(tmp_path):
    import json
    import subprocess
    p = _write_goals_file([], tmp_path)
    argv = [sys.executable, str(_SCRIPTS / 'goals_writer.py'), '--file', str(p), '--create', '--id', 'G1',
            '--title', 'Save', '--type', 'outcome', '--category', 'finance', '--owner', 'owner',
            '--metric-definition', json.dumps(_dated_definition())]
    result = subprocess.run(argv, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert _read_goals(p)[0]['metric'] == _dated_definition()
    assert _read_goals(p)[0]['owner'] == 'owner'
    original = p.read_bytes()
    result = subprocess.run([sys.executable, str(_SCRIPTS / 'goals_writer.py'), '--file', str(p), '--update',
                             'G1', '--next-action', 'Read', '--typo', 'ignored'], capture_output=True)
    assert result.returncode == 2 and p.read_bytes() == original


def test_write_flushes_before_replace_and_refuses_intervening_edit(tmp_path, monkeypatch):
    p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
    original = goals_writer._read_text(p)
    calls = []
    sync = goals_writer.os.fsync
    monkeypatch.setattr(goals_writer.os, 'fsync', lambda fd: (calls.append('sync'), sync(fd))[-1])
    goals_writer.write_state_atomic(p, original, expected_content=original)
    assert len(calls) >= 1
    p.write_text(original + '\nmanual edit\n')
    with pytest.raises(ValueError, match='changed'):
        goals_writer.write_state_atomic(p, original, expected_content=original)
    assert p.read_text().endswith('manual edit\n')


def test_independent_writer_refuses_live_lock_then_retry_preserves_both_changes(tmp_path):
    import subprocess
    p = _write_goals_file([dict(BASE_GOAL)], tmp_path)
    child = r'''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import goals_writer as g
write=g.write_state_atomic
def paused(*a,**kw):
 print('ready',flush=True)
 sys.stdin.readline()
 return write(*a,**kw)
g.write_state_atomic=paused
raise SystemExit(g.update_goal(Path(sys.argv[2]),'G-001',{'next_action':'first change'}))
'''
    proc = subprocess.Popen([sys.executable, '-c', child, str(_SCRIPTS), str(p)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'ready'
        assert goals_writer.update_goal(p, 'G-001', {'review_date': '2026-11-01'}) == 3
        out, err = proc.communicate('\n', timeout=10)
        assert proc.returncode == 0, (out, err)
    finally:
        if proc.poll() is None:
            proc.kill(); proc.communicate()
    assert goals_writer.update_goal(p, 'G-001', {'review_date': '2026-11-01'}) == 0
    saved = _read_goals(p)[0]
    assert saved['next_action'] == 'first change' and saved['review_date'] == '2026-11-01'


# ── authored competes_with and per-Sprint tolerance ──────────────────────────────────────────────────────────────────

def _two_goals(tmp_path):
    return _write_goals_file([BASE_GOAL, {**BASE_GOAL, "id": "G-002", "title": "Rest more"}], tmp_path)


def test_given_two_goals_when_competes_with_is_set_then_it_is_stored_as_an_authored_list(tmp_path):
    p = _two_goals(tmp_path)
    assert goals_writer.update_goal(p, "G-001", {"competes_with": "G-002"}) == 0
    assert _read_goals(p)[0]["competes_with"] == ["G-002"]
    assert goals_writer.update_goal(p, "G-001", {"competes_with": ""}) == 0
    assert "competes_with" not in _read_goals(p)[0]


@pytest.mark.parametrize("value", ["G-001", "G-404", "G-002,G-002", "G-002,,", 7])
def test_given_a_bad_competes_with_when_updated_then_nothing_changes(tmp_path, value):
    p = _two_goals(tmp_path)
    before = p.read_bytes()
    assert goals_writer.update_goal(p, "G-001", {"competes_with": value}) == 2
    assert p.read_bytes() == before


def test_given_a_new_goal_when_created_with_competes_with_then_it_must_name_an_existing_goal(tmp_path):
    p = _write_goals_file([BASE_GOAL], tmp_path)
    base = {"id": "G-009", "title": "New", "type": "milestone", "category": "x"}
    assert goals_writer.create_goal(p, {**base, "competes_with": "G-404"}) == 2
    assert goals_writer.create_goal(p, {**base, "competes_with": "G-001"}) == 0
    assert _read_goals(p)[1]["competes_with"] == ["G-001"]


def _sprint(**extra):
    return {"id": "s1", "goal_ref": "G-001", "start": "2026-06-01", "end": "2026-06-30", "target": "x", **extra}


def test_given_a_sprint_tolerance_when_added_then_it_is_kept_as_authored_text(tmp_path):
    p = _two_goals(tmp_path)
    assert goals_writer.add_sprint(p, _sprint(tolerance="0.10")) == 0
    assert yaml.safe_load(p.read_text().split("---")[1])["sprints"][0]["tolerance"] == "0.10"


@pytest.mark.parametrize("value", ["abc", "-0.1", "1.5", "nan", "inf"])
def test_given_a_bad_sprint_tolerance_when_added_then_the_file_is_unchanged(tmp_path, value):
    p = _two_goals(tmp_path)
    before = p.read_bytes()
    assert goals_writer.add_sprint(p, _sprint(tolerance=value)) == 2
    assert p.read_bytes() == before


def test_given_no_tolerance_when_a_sprint_is_added_then_none_is_invented(tmp_path):
    p = _two_goals(tmp_path)
    assert goals_writer.add_sprint(p, _sprint()) == 0
    assert "tolerance" not in yaml.safe_load(p.read_text().split("---")[1])["sprints"][0]


def test_given_the_command_line_when_competes_with_and_tolerance_are_passed_then_they_are_saved(tmp_path):
    import subprocess
    p = _two_goals(tmp_path)
    run = lambda *a: subprocess.run([sys.executable, str(_SCRIPTS / "goals_writer.py"), "--file", str(p), *a],  # noqa: E731
                                    capture_output=True, text=True)
    assert run("--update", "G-001", "--competes-with", "G-002").returncode == 0
    assert run("--create", "--id", "G-003", "--title", "T", "--type", "milestone", "--category", "c",
               "--competes-with", "G-001,G-002").returncode == 0
    assert run("--add-sprint", "--id", "s1", "--goal-ref", "G-001", "--start", "2026-06-01", "--end", "2026-06-30",
               "--target", "x", "--tolerance", "0.2").returncode == 0
    data = yaml.safe_load(p.read_text().split("---")[1])
    assert data["goals"][0]["competes_with"] == ["G-002"] and data["goals"][2]["competes_with"] == ["G-001", "G-002"]
    assert data["sprints"][0]["tolerance"] == "0.2"
