"""Current, record-scoped curated retrieval using synthetic files and credentials only."""
from __future__ import annotations

from argparse import Namespace
from copy import deepcopy

import pytest
import yaml

from catchup_core import commands, curated, runtime
from catchup_core.contracts import CatchupRequest, CatchupResult, CoverageStatus, ExecutionMode, RunStatus
from catchup_core.ledger import LedgerError, PermissionDenied
from test_runtime import FakeKeyring, write_cfg


@pytest.fixture
def scoped(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'state').mkdir(parents=True)
    write_cfg(repo, subjects=['kid1', 'adult1', 'adult2'], consent_required=['adult1', 'adult2'])
    deps = runtime.build_deps(CatchupRequest('cu_scoped', 'ved', 'cli', mode=ExecutionMode.SHADOW),
        repo_root=repo, environ={'ARTHA_LOCAL_DIR': str(tmp_path / 'private')},
        keyring_backend=FakeKeyring(), handler_loader=lambda _: None, auth_loader=lambda _: {})
    yield repo, deps
    deps.ledger.close()


def write(repo, kind, rows, **extra):
    text = yaml.safe_dump({kind: rows, **extra}, sort_keys=False)
    (repo / 'state' / f'{kind}.md').write_text(text)


def read(repo, deps, kind='goals'):
    return curated.read_authorized_state(repo / 'state', kind, deps.ledger, deps.viewer, deps.subjects)


def grant(deps, subject='adult1', kind='goals', operations=('ingest', 'disclose', 'view')):
    return deps.ledger.grant_consent(subject, subject, f'curated:{kind}', operations, deps.viewer)


def test_runtime_and_command_keep_allowed_goals_without_unrelated_adult_consent(scoped):
    repo, deps = scoped
    write(repo, 'goals', [
        {'id': 'allowed', 'owner': 'ved', 'subject': 'adult1', 'title': 'Allowed progress', 'status': 'active'},
        {'id': 'private', 'owner': 'ved', 'subject': 'adult2', 'title': 'PRIVATE TEXT', 'status': 'active'}])
    grant(deps)
    got = deps.goal_lines()
    assert any('Allowed progress' in line for line in got.lines) and not got.gaps
    result = commands.execute(Namespace(command='goals'), deps, repo)
    assert [row['id'] for row in result['goals']] == ['allowed']
    assert 'PRIVATE' not in str(result) and 'private' not in str(result)


@pytest.mark.parametrize('extra', [
    {'co_subjects': ['adult2']}, {'subject_id': 'adult2'},
    {'metric': {'subject': 'adult2'}}, {'milestones': [{'id': 'm', 'subject': 'adult2'}]},
    {'milestones': [{'id': 'm', 'co_subjects': ['adult2']}]}, {'owner': 'adult2'},
    {'milestones': [{'id': 'm', 'visible_to': ['adult2']}]}, {'visible_to': []}])
def test_strictest_nested_subject_and_visibility_withhold_the_entire_record(scoped, extra):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'secret', 'owner': 'ved', 'subject': 'adult1', **deepcopy(extra)}])
    grant(deps)
    assert curated.records(read(repo, deps), 'goals') == []


@pytest.mark.parametrize('extra', [
    {'subject': 'unknown'}, {'owner': 1}, {'subject': ''}, {'co_subjects': 'adult1'},
    {'co_subjects': [False]}, {'visible_to': 'ved'}, {'visible_to': [1]},
    {'metric': {'subject': 'unknown'}}, {'milestones': [{'id': 'm', 'owner': False}]}])
def test_malformed_or_unresolved_scope_needs_generic_review(scoped, extra):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'PRIVATE-ID', 'owner': 'ved', **deepcopy(extra)}])
    with pytest.raises((ValueError, PermissionDenied), match='scope needs review') as exc:
        read(repo, deps)
    assert 'PRIVATE-ID' not in str(exc.value) and 'unknown' not in str(exc.value)


def test_current_revocation_and_operation_scope_are_checked_on_every_read(scoped):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'subject': 'adult1'}])
    grant(deps, operations=('view',))
    assert curated.records(read(repo, deps), 'goals') == []
    permission = grant(deps)
    assert curated.records(read(repo, deps), 'goals')[0]['id'] == 'G'
    deps.ledger.revoke_consent(permission)
    assert curated.records(read(repo, deps), 'goals') == []


def test_legacy_unscoped_records_keep_conservative_file_authorization(scoped):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'legacy', 'title': 'Unscoped'}, {'id': 'self', 'owner': 'ved'}])
    grant(deps)
    with pytest.raises(PermissionDenied):
        read(repo, deps)
    grant(deps, 'adult2')
    assert len(curated.records(read(repo, deps), 'goals')) == 2


def test_untagged_open_items_without_adult_consent_degrade_the_run_until_tagged_or_consented(scoped):
    repo, deps = scoped
    rows = [{'id': 'OI-1', 'description': 'PRIVATE TEXT', 'deadline': '2026-11-02', 'status': 'open'}]
    (repo / 'state' / 'open_items.md').write_text(yaml.safe_dump(rows, sort_keys=False))
    assert issubclass(PermissionDenied, RuntimeError)
    with pytest.raises(PermissionDenied, match='consent'):
        read(repo, deps, 'open_items')
    out = deps.incumbent.run([])
    assert [(c.connector, c.account_scope, c.status, c.required) for c in out.coverage] == [
        ('incumbent', 'open_items', CoverageStatus.UNAVAILABLE, True)]
    assert CatchupResult.derive_status(out.coverage, ()) is RunStatus.DEGRADED
    assert 'PRIVATE' not in str(out) and 'adult' not in str(out)
    # Owner action 1: tag each record with its owner; no unrelated consent is then needed.
    (repo / 'state' / 'open_items.md').write_text(yaml.safe_dump([{**rows[0], 'owner': 'ved'}], sort_keys=False))
    tagged = deps.incumbent.run([])
    assert not tagged.coverage and any('OI-1' in c.text for c in tagged.conclusions)
    # Owner action 2: each consent-required adult grants scoped consent for curated:open_items.
    (repo / 'state' / 'open_items.md').write_text(yaml.safe_dump(rows, sort_keys=False))
    grant(deps, 'adult1', 'open_items')
    assert deps.incumbent.run([]).coverage
    grant(deps, 'adult2', 'open_items')
    consented = deps.incumbent.run([])
    assert not consented.coverage and any('OI-1' in c.text for c in consented.conclusions)


def test_open_items_hybrid_respects_unscoped_and_co_subject_consent(scoped):
    repo, deps = scoped
    rows = [
        {'id': 'self', 'description': 'Owner item', 'subject': 'ved', 'status': 'open'},
        {'id': 'shared', 'description': 'Shared item', 'owner': 'ved', 'subject': 'adult1',
         'co_subjects': ['adult2'], 'status': 'open'},
        {'id': 'unscoped', 'description': 'Unscoped item', 'status': 'open'},
    ]
    path = repo / 'state' / 'open_items.md'
    path.write_text(yaml.safe_dump(rows, sort_keys=False))

    with pytest.raises(PermissionDenied, match='consent'):
        read(repo, deps, 'open_items')
    first = grant(deps, 'adult1', 'open_items')
    with pytest.raises(PermissionDenied, match='consent'):
        read(repo, deps, 'open_items')
    second = grant(deps, 'adult2', 'open_items')
    assert {row['id'] for row in curated.records(read(repo, deps, 'open_items'), 'open_items')} == {
        'self', 'shared', 'unscoped'}

    deps.ledger.revoke_consent(second)
    with pytest.raises(PermissionDenied, match='consent'):
        read(repo, deps, 'open_items')
    out = deps.incumbent.run([])
    assert CatchupResult.derive_status(out.coverage, ()) is RunStatus.DEGRADED
    assert not out.conclusions

    path.write_text(yaml.safe_dump(rows[:-1], sort_keys=False))
    assert [row['id'] for row in curated.records(read(repo, deps, 'open_items'), 'open_items')] == ['self']
    grant(deps, 'adult2', 'open_items')
    assert {row['id'] for row in curated.records(read(repo, deps, 'open_items'), 'open_items')} == {'self', 'shared'}
    deps.ledger.revoke_consent(first)
    assert [row['id'] for row in curated.records(read(repo, deps, 'open_items'), 'open_items')] == ['self']


def test_explicitly_invisible_legacy_record_does_not_require_unrelated_consent(scoped):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'hidden-legacy', 'visible_to': []}, {'id': 'G', 'owner': 'ved'}])
    assert [row['id'] for row in curated.records(read(repo, deps), 'goals')] == ['G']


def test_sprint_of_an_explicitly_invisible_goal_cannot_block_allowed_goals(scoped):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'hidden', 'visible_to': []}, {'id': 'G', 'owner': 'ved'}],
          sprints=[{'id': 'private-sprint', 'goal_ref': 'hidden'}])
    text = read(repo, deps)
    assert [row['id'] for row in curated.records(text, 'goals')] == ['G']
    assert curated.records(text, 'sprints') == []


def test_sprints_inherit_their_goal_scope_and_free_prose_is_excluded(scoped):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved'}, {'id': 'private', 'subject': 'adult2'}],
        sprints=[{'id': 'S', 'goal_ref': 'G'}, {'id': 'hidden', 'goal_ref': 'private'},
                 {'id': 'private-child', 'goal_ref': 'G', 'subject': 'adult2'}], notes='PRIVATE PROSE')
    text = read(repo, deps)
    assert [row['id'] for row in curated.records(text, 'sprints')] == ['S']
    assert 'PRIVATE' not in text and 'hidden' not in text and 'private' not in text


def test_withheld_records_cannot_consume_bounds_or_inject_validation_gaps(scoped):
    repo, deps = scoped
    private = [{'id': f'PRIVATE-{i}', 'owner': 'ved', 'subject': 'adult2',
                'metric': {'subject': 'unknown'}, 'milestones': [{'visible_to': False}]}
               for i in range(110)]
    write(repo, 'responsibilities', private + [{'id': 'allowed', 'owner': 'ved', 'outcome': 'Allowed'}])
    got = deps.responsibility_lines()
    assert not got.gaps and len(got.lines) == 1 and 'allowed' in got.lines[0]
    write(repo, 'goals', private)
    assert deps.goal_lines().lines == [] and not deps.goal_lines().gaps
    (repo / 'state' / 'household_tasks.md.age').write_bytes(b'synthetic ciphertext, never decrypted')
    incumbent = deps.incumbent.run([])
    assert not incumbent.conclusions and not incumbent.coverage


def test_orphan_sprint_remains_a_review_gap_after_conservative_authorization(scoped):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'status': 'active'}],
        sprints=[{'id': 'orphan', 'goal_ref': 'absent', 'status': 'active'}])
    grant(deps)
    grant(deps, 'adult2')
    assert any('Sprint review needed' in line for line in deps.goal_lines().lines)


def test_permission_change_during_projection_refuses_the_entire_read(scoped, monkeypatch):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'subject': 'adult1'}])
    permission = grant(deps)
    dump = curated.yaml.safe_dump
    def revoke(*args, **kwargs):
        text = dump(*args, **kwargs)
        deps.ledger.revoke_consent(permission)
        return text
    monkeypatch.setattr(curated.yaml, 'safe_dump', revoke)
    with pytest.raises(PermissionDenied, match='permissions changed'):
        read(repo, deps)


def test_command_rechecks_permission_after_goal_observation(scoped, monkeypatch):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'subject': 'adult1'}])
    permission = grant(deps)
    def observe(*args, **kwargs):
        deps.ledger.revoke_consent(permission)
        return runtime.ContextLines(())
    monkeypatch.setattr(runtime, '_goal_lines', observe)
    with pytest.raises(PermissionDenied, match='permissions changed'):
        commands.execute(Namespace(command='goals'), deps, repo)


@pytest.mark.parametrize('kind', ['responsibilities', 'household_tasks', 'open_items'])
def test_other_curated_consumers_use_the_same_record_scope(scoped, kind):
    repo, deps = scoped
    rows = [{'id': 'allowed', 'owner': 'ved', 'subject': 'kid1',
             'status': 'active' if kind == 'responsibilities' else 'done',
             'outcome': 'Check', 'obligation': 'Check', 'description': 'Check'},
            {'id': 'private', 'owner': 'ved', 'subject': 'adult2', 'outcome': 'PRIVATE',
             'obligation': 'PRIVATE', 'description': 'PRIVATE'}]
    if kind == 'open_items':
        (repo / 'state' / f'{kind}.md').write_text(yaml.safe_dump(rows, sort_keys=False))
        # Closed Open Items remain excluded by the existing parser.
        assert curated.records(read(repo, deps, kind), kind) == []
        result = commands.execute(Namespace(command='items'), deps, repo)
        assert result['items'] == []
    else:
        write(repo, kind, rows)
        lines = deps.responsibility_lines() if kind == 'responsibilities' else deps.coordination_lines()
        assert 'allowed' in str(lines.lines) and 'PRIVATE' not in str(lines.lines) and not lines.gaps


def test_unassigned_task_keeps_its_subject_scope_and_missing_owner_message(scoped):
    repo, deps = scoped
    write(repo, 'household_tasks', [{'id': 'H', 'subject': 'kid1', 'owner': '', 'obligation': 'Assign this'}])
    got = deps.coordination_lines()
    assert not got.gaps and any('no accountable owner' in line for line in got.lines)


def test_completion_keeps_exact_record_binding_and_checks_scope_again(scoped):
    repo, deps = scoped
    row = {'id': 'R', 'owner': 'ved', 'subject': 'adult1', 'outcome': 'Allowed'}
    write(repo, 'responsibilities', [row, {'id': 'private', 'subject': 'adult2'}])
    permission = grant(deps, kind='responsibilities')
    deps.ledger.grant_consent('adult1', 'adult1', deps.pack.domain, ['correct', 'view'], deps.viewer)
    args = Namespace(command='complete', record='responsibility:R', apply=None)
    preview = commands.execute(args, deps, repo)
    assert preview['apply_token']
    selected = commands._completion_record(args, deps, repo)[1]
    assert curated.completion_key('responsibility', selected) == curated.completion_key('responsibility', row)
    args.apply = preview['apply_token']
    deps.ledger.revoke_consent(permission)
    with pytest.raises((ValueError, PermissionDenied)):
        commands.execute(args, deps, repo)
    assert not deps.ledger.conn.execute('SELECT 1 FROM facts').fetchone()


def test_commands_refuse_dependencies_from_an_earlier_runtime_policy(scoped):
    repo, deps = scoped
    path = repo / 'config' / 'catchup_core.yaml'
    path.write_text(path.read_text() + '\n# changed\n')
    with pytest.raises(LedgerError, match='inputs changed'):
        commands.execute(Namespace(command='goals'), deps, repo)
