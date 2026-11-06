"""RA-03/11: one owner-reviewed completion follows the canonical item through every projection."""
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
import os
import subprocess
import sys

import pytest

from catchup_core import commands
from catchup_core import runtime
from catchup_core.curated import read_authorized_state, verify_completion
from catchup_core.legacy_state import LegacyStateAdapter
from catchup_core.ledger import PermissionDenied
import test_integrated_surfaces


@pytest.fixture
def app(tmp_path):
    yield from test_integrated_surfaces.app.__wrapped__(tmp_path)


def authored(root, *, owner='owner', subject='kid1'):
    path = root / 'state/open_items.md'
    owner_line = f'  owner: {owner}\n' if owner else ''
    path.write_text(f'- id: OI-1\n{owner_line}  subject: {subject}\n  source_domain: kids_school\n'
                    '  status: open\n  description: Gather the synthetic records\n  deadline: 2026-11-01\n')
    return path


def review(deps, root, *, reopen=False):
    args = Namespace(command='complete', record='open_item:OI-1', apply=None, reopen=reopen)
    args.apply = commands.execute(args, deps, root)['apply_token']
    return args


def items(deps, root):
    return commands.execute(Namespace(command='items'), deps, root)['items']


@pytest.mark.parametrize('owner', ['owner', 'kid1', ''])
def test_open_item_completion_is_shared_by_items_and_briefing_without_authored_writes(app, owner):
    root, deps, _, _ = app
    path = authored(root, owner=owner)
    before = path.read_bytes()
    args = review(deps, root)
    assert commands.execute(Namespace(command='items'), deps, root)['accountable_owner'] == 'owner'
    assert len(items(deps, root)) == 1
    receipt = commands.execute(args, deps, root)
    assert receipt['completed'] == 'open_item:OI-1' and items(deps, root) == []
    reader = lambda name: read_authorized_state(root / 'state', name, deps.ledger, deps.viewer, deps.subjects)
    adapter = LegacyStateAdapter(root / 'state', ['kids_school'], deps.now(), reader=reader)
    assert not any(row.kind == 'open_item' for row in adapter.run([], budget_seconds=1).conclusions)
    assert path.read_bytes() == before


def test_reopening_cannot_be_undone_by_an_old_completion_review(app):
    root, deps, _, _ = app
    authored(root)
    old = review(deps, root)
    commands.execute(old, deps, root)
    commands.execute(review(deps, root, reopen=True), deps, root)
    assert len(items(deps, root)) == 1
    with pytest.raises(ValueError, match='changed'):
        commands.execute(old, deps, root)


def test_changed_open_item_content_invalidates_preview_and_past_completion(app):
    root, deps, _, _ = app
    path = authored(root)
    old = review(deps, root)
    path.write_text(path.read_text().replace('synthetic records', 'different synthetic records'))
    with pytest.raises(ValueError, match='changed'):
        commands.execute(old, deps, root)
    commands.execute(review(deps, root), deps, root)
    assert items(deps, root) == []
    path.write_text(path.read_text().replace('different synthetic records', 'additional synthetic records'))
    assert len(items(deps, root)) == 1


def test_changed_authored_owner_still_invalidates_a_completion_preview(app):
    root, deps, _, _ = app
    path = authored(root)
    args = review(deps, root)
    path.write_text(path.read_text().replace('  owner: owner\n', '  owner: kid1\n'))
    with pytest.raises(ValueError, match='changed'):
        commands.execute(args, deps, root)


@pytest.mark.parametrize('owner', ['kid1', ''])
def test_primary_user_owns_open_items_without_per_item_accountability_metadata(app, owner):
    root, deps, _, _ = app
    path = authored(root, owner=owner)
    before = path.read_bytes()
    preview = commands.execute(Namespace(command='complete', record='open_item:OI-1',
                                         apply=None, reopen=False), deps, root)
    assert preview['accountable_owner'] == deps.ledger._owner
    result = commands.execute(review(deps, root), deps, root)
    assert items(deps, root) == []
    fid = result['completion_reference'][5:].split('@')[0]
    assert deps.ledger.get_fact(fid, 'owner').value['actor'] == 'owner'
    commands.execute(review(deps, root, reopen=True), deps, root)
    assert len(items(deps, root)) == 1
    assert path.read_bytes() == before


def test_primary_accountability_does_not_resolve_an_unknown_privacy_owner(app):
    root, deps, _, _ = app
    deps.ledger._known_subjects = frozenset({'owner', 'kid1'})
    authored(root, owner='another_owner')
    with pytest.raises(PermissionDenied):
        review(deps, root)


def test_a_non_primary_actor_cannot_complete_even_an_explicitly_owned_open_item(app):
    root, deps, _, _ = app
    authored(root, owner='kid1')
    with pytest.raises(PermissionDenied):
        review(replace(deps, viewer='kid1'), root)


def test_missing_accountability_does_not_bypass_explicit_subject_consent(app):
    root, deps, _, _ = app
    deps.ledger._consent_subjects = frozenset({'kid1'})
    authored(root, owner='')
    with pytest.raises((PermissionDenied, ValueError)):
        review(deps, root)
    grant = deps.ledger.grant_consent('kid1', 'kid1', '*',
                                    ['ingest', 'disclose', 'view', 'correct'], 'owner')
    args = review(deps, root)
    deps.ledger.revoke_consent(grant)
    with pytest.raises((PermissionDenied, ValueError)):
        commands.execute(args, deps, root)


def test_authored_privacy_owner_is_preserved_when_no_subject_is_present(app):
    root, deps, _, _ = app
    path = authored(root, owner='kid1')
    path.write_text(path.read_text().replace('  subject: kid1\n', ''))
    result = commands.execute(review(deps, root), deps, root)
    fid = result['completion_reference'][5:].split('@')[0]
    fact = deps.ledger.get_fact(fid, 'owner')
    assert fact.primary_subject_id == 'kid1'
    assert fact.value['actor'] == 'owner'


def test_authored_owner_privacy_is_not_lost_when_a_different_subject_is_explicit(app):
    root, deps, _, _ = app
    deps.ledger._consent_subjects = frozenset({'adult'})
    authored(root, owner='adult')
    grant = deps.ledger.grant_consent('adult', 'adult', '*',
                                    ['ingest', 'disclose', 'view', 'correct'], 'owner')
    result = commands.execute(review(deps, root), deps, root)
    fid = result['completion_reference'][5:].split('@')[0]
    fact = deps.ledger.get_fact(fid, 'owner')
    assert fact.primary_subject_id == 'kid1'
    deps.ledger.revoke_consent(grant)
    with pytest.raises(PermissionDenied):
        deps.ledger.get_fact(fid, 'owner')
    assert not verify_completion(deps.ledger, 'owner', result['completion_reference'], fact.value['record_key'])


def test_duplicate_open_item_id_cannot_choose_a_commitment_to_complete(app):
    root, deps, _, _ = app
    path = authored(root)
    path.write_text(path.read_text() * 2)
    with pytest.raises(ValueError, match='ambiguous'):
        review(deps, root)


def test_explicit_subject_id_is_retained_in_completion_permissions(app):
    root, deps, _, _ = app
    path = authored(root)
    path.write_text(path.read_text().replace('subject: kid1', 'subject_id: kid1'))
    result = commands.execute(review(deps, root), deps, root)
    fid = result['completion_reference'][5:].split('@')[0]
    assert deps.ledger.get_fact(fid, 'owner').primary_subject_id == 'kid1'


def test_conflicting_primary_subjects_require_review(app):
    root, deps, _, _ = app
    path = authored(root)
    path.write_text(path.read_text() + '  subject_id: another_subject\n')
    with pytest.raises(ValueError, match='subject'):
        review(deps, root)


def linked_goal(root, *, link='OI-1', owner='owner', domain='kids_school', subject='kid1'):
    path = root / 'state/goals.md'
    path.write_text(f'goals:\n- id: G-1\n  owner: {owner}\n  subject: {subject}\n  domain: {domain}\n'
        '  status: active\n  title: Prepare the synthetic records\n  metric: {kind: milestones, target: 1}\n'
        f'  milestones:\n  - {{id: m1, title: Gather records, open_item_ref: {link}}}\n')
    return path


def goal_lines(deps, root):
    result = commands.execute(Namespace(command='goals', weekly=False), deps, root)
    return result['observations']


def test_linked_goal_and_plan_share_the_current_open_item_completion_version(app):
    root, deps, _, _ = app
    authored(root)
    goal = linked_goal(root)
    before = goal.read_bytes()
    assert any('0 of 1' in line for line in goal_lines(deps, root))
    result = commands.execute(review(deps, root), deps, root)
    fid, version = result['completion_reference'][5:].split('@', 1)
    assert any('1 of 1' in line for line in goal_lines(deps, root))
    context = runtime._plan_lines(root / 'state', today=deps.now().date(), ledger=deps.ledger, viewer='owner')
    assert context.lines == [] and context.fact_versions == {fid: version}
    commands.execute(review(deps, root, reopen=True), deps, root)
    assert any('0 of 1' in line for line in goal_lines(deps, root))
    assert goal.read_bytes() == before


@pytest.mark.parametrize('owner', ['', 'kid1'])
def test_linked_goal_uses_primary_accountability_without_rewriting_item_ownership(app, owner):
    root, deps, _, _ = app
    path = authored(root, owner=owner)
    linked_goal(root)
    before = path.read_bytes()
    assert any('0 of 1' in line for line in goal_lines(deps, root))
    result = commands.execute(review(deps, root), deps, root)
    fid, version = result['completion_reference'][5:].split('@', 1)
    assert any('1 of 1' in line for line in goal_lines(deps, root))
    context = runtime._plan_lines(root / 'state', today=deps.now().date(), ledger=deps.ledger, viewer='owner')
    assert context.lines == [] and context.fact_versions == {fid: version}
    commands.execute(review(deps, root, reopen=True), deps, root)
    assert any('0 of 1' in line for line in goal_lines(deps, root))
    assert path.read_bytes() == before


def test_linked_goal_cannot_drop_a_distinct_authored_privacy_owner(app):
    root, deps, _, _ = app
    deps.ledger._consent_subjects = frozenset({'adult'})
    authored(root, owner='adult')
    goal = linked_goal(root)
    deps.ledger.grant_consent('adult', 'adult', '*',
                             ['ingest', 'disclose', 'view', 'correct'], 'owner')
    commands.execute(review(deps, root), deps, root)
    assert any(': unknown' in line for line in goal_lines(deps, root))
    goal.write_text(goal.read_text().replace('  subject: kid1\n', '  subject: kid1\n  co_subjects: [adult]\n'))
    assert any('1 of 1' in line for line in goal_lines(deps, root))


@pytest.mark.parametrize('mismatch', ['missing-item', 'owner', 'domain', 'subject', 'duplicate', 'authored-done'])
def test_unverified_or_mismatched_open_item_links_cannot_establish_goal_progress(app, mismatch):
    root, deps, _, _ = app
    path = authored(root)
    options = {'link': 'missing'} if mismatch == 'missing-item' else (
        {mismatch: 'different'} if mismatch in {'owner', 'domain', 'subject'} else {})
    linked_goal(root, **options)
    if mismatch == 'duplicate':
        path.write_text(path.read_text() * 2)
    elif mismatch == 'authored-done':
        path.write_text(path.read_text().replace('status: open', 'status: done'))
    else:
        commands.execute(review(deps, root), deps, root)
    lines = [line for line in goal_lines(deps, root) if line.startswith('Prepare the synthetic records:')]
    assert any(': unknown' in line or 'needs review' in line for line in lines)
    assert not any('1 of 1' in line for line in lines)


def test_repeated_open_item_link_cannot_double_count_one_completion(app):
    root, deps, _, _ = app
    authored(root)
    path = linked_goal(root)
    path.write_text(path.read_text() + '  - {id: m2, title: Duplicate link, open_item_ref: OI-1}\n')
    commands.execute(review(deps, root), deps, root)
    assert any(': unknown' in line for line in goal_lines(deps, root))


def test_changed_linked_item_during_query_blocks_a_stale_goal_answer(app):
    root, deps, _, _ = app
    path = authored(root)
    linked_goal(root)
    commands.execute(review(deps, root), deps, root)
    calls = []
    def change():
        calls.append(True)
        if len(calls) == 2:
            path.write_text(path.read_text().replace('synthetic records', 'different records'))
    deps.validate_inputs = change
    with pytest.raises(PermissionDenied, match='changed'):
        goal_lines(deps, root)


def test_completion_checks_reviewed_permissions_inside_its_write_transaction(app, monkeypatch):
    root, deps, _, _ = app
    authored(root)
    grant = deps.ledger.grant_consent('adult', 'adult', 'kids_school', ['view'], 'owner')
    args = review(deps, root)
    write = deps.ledger.put_fact
    def revoke_before_write(*a, **kw):
        script = '''
import sys
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger
with Ledger(sys.argv[1], id_key=HmacKey(1, b'i' * 32),
            cipher=EnvelopeCipher(StaticKeyProvider({1: b'7' * 32}))).open() as ledger:
    ledger.revoke_consent(sys.argv[2])
'''
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')}
        subprocess.run([sys.executable, '-c', script, str(deps.ledger._path), grant],
                       env=env, capture_output=True, check=True, timeout=15)
        return write(*a, **kw)
    monkeypatch.setattr(deps.ledger, 'put_fact', revoke_before_write)
    with pytest.raises(PermissionDenied, match='permissions changed'):
        commands.execute(args, deps, root)
    assert len(items(deps, root)) == 1


@pytest.mark.parametrize('reference', [None, '', True, ['OI-1']])
def test_malformed_open_item_reference_is_a_visible_goal_review_gap(app, reference):
    import yaml
    root, deps, _, _ = app
    authored(root)
    path = linked_goal(root)
    data = yaml.safe_load(path.read_text())
    data['goals'][0]['milestones'][0]['open_item_ref'] = reference
    path.write_text(yaml.safe_dump(data))
    assert any(': unknown' in line for line in goal_lines(deps, root))


def test_unreadable_linked_record_stays_unknown(app):
    root, deps, _, _ = app
    path = authored(root)
    linked_goal(root)
    path.write_text('- id: [malformed synthetic record')
    assert any(': unknown' in line for line in goal_lines(deps, root))


def test_conflicting_linked_milestone_subjects_cannot_reuse_completion(app):
    root, deps, _, _ = app
    authored(root)
    path = linked_goal(root)
    path.write_text(path.read_text().replace('open_item_ref: OI-1',
        'open_item_ref: OI-1, subject: kid1, subject_id: other'))
    commands.execute(review(deps, root), deps, root)
    assert any(': unknown' in line for line in goal_lines(deps, root))


def test_revocation_removes_linked_completion_from_public_queries(app):
    root, deps, _, _ = app
    deps.ledger._consent_subjects = frozenset({'adult'})
    authored(root, subject='adult')
    linked_goal(root, subject='adult')
    grant = deps.ledger.grant_consent('adult', 'adult', '*', ['ingest', 'disclose', 'view', 'correct'], 'owner')
    commands.execute(review(deps, root), deps, root)
    assert any('1 of 1' in line for line in goal_lines(deps, root))
    deps.ledger.revoke_consent(grant)
    result = commands.execute(Namespace(command='goals'), deps, root)
    assert result['goals'] == [] and not any('1 of 1' in line for line in result['observations'])


def test_goal_subject_id_alias_uses_the_same_strict_link_scope(app):
    root, deps, _, _ = app
    authored(root)
    path = linked_goal(root)
    path.write_text(path.read_text().replace('subject: kid1', 'subject_id: kid1'))
    commands.execute(review(deps, root), deps, root)
    assert any('1 of 1' in line for line in goal_lines(deps, root))


def test_milestone_confirmation_retains_its_parent_goal_subject(app):
    root, deps, _, _ = app
    path = linked_goal(root)
    path.write_text(path.read_text().replace('open_item_ref: OI-1', 'subject: kid2'))
    args = Namespace(command='complete', record='milestone:G-1:m1', apply=None)
    args.apply = commands.execute(args, deps, root)['apply_token']
    result = commands.execute(args, deps, root)
    fid = result['completion_reference'][5:].split('@')[0]
    row = deps.ledger.get_fact(fid, 'owner')
    assert row.primary_subject_id == 'kid2'
    assert [row[0] for row in deps.ledger.conn.execute(
        'SELECT subject_id FROM fact_subjects WHERE fact_id=?', (fid,))] == ['kid1']


def test_parent_goal_revocation_applies_to_child_completion_evidence(app):
    root, deps, _, _ = app
    deps.ledger._consent_subjects = frozenset({'adult'})
    path = linked_goal(root, subject='adult')
    path.write_text(path.read_text().replace('open_item_ref: OI-1', 'subject: kid2'))
    grant = deps.ledger.grant_consent('adult', 'adult', '*', ['ingest', 'disclose', 'view', 'correct'], 'owner')
    args = Namespace(command='complete', record='milestone:G-1:m1', apply=None)
    args.apply = commands.execute(args, deps, root)['apply_token']
    result = commands.execute(args, deps, root)
    fid = result['completion_reference'][5:].split('@')[0]
    deps.ledger.revoke_consent(grant)
    with pytest.raises(PermissionDenied):
        deps.ledger.get_fact(fid, 'owner')


def test_conflicting_parent_goal_subject_aliases_cannot_be_confirmed(app):
    root, deps, _, _ = app
    path = linked_goal(root)
    path.write_text(path.read_text().replace('subject: kid1', 'subject: kid1\n  subject_id: kid2'))
    with pytest.raises(ValueError, match='subjects conflict'):
        commands.execute(Namespace(command='complete', record='milestone:G-1:m1', apply=None), deps, root)
