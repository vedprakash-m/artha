"""Current, record-scoped curated retrieval using synthetic files and credentials only."""
from __future__ import annotations

import os
import stat
import sys
from argparse import Namespace
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from catchup_core import commands, curated, ids, runtime
from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.ledger import Ledger, LedgerError, PermissionDenied
from test_runtime import FakeKeyring, write_cfg

_UNVERIFIED_PERSONAL_STATE_IDENTITY = curated._personal_state_identity


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(curated, '_personal_state_identity', _legacy_stat_identity_for_projection_tests)
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


def _legacy_stat_identity_for_projection_tests(
        directory: Path, name: str, ledger: Ledger) -> tuple[Path, str, str]:
    """Exercise post-admission projection logic only; production rejects stat-only versions."""
    path = directory / f'{name}.md'
    info = path.lstat()
    version = f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}'
    return path, version, ids.content_hmac(ledger._id_key, version)


def review_personal_state(repo, deps, kind):
    _, version, digest = _legacy_stat_identity_for_projection_tests(repo / 'state', kind, deps.ledger)
    if not deps.ledger._has_personal_binding(
            'curated', kind, version, 'state', deps.ledger._owner, digest):
        with deps.ledger.transaction() as db:
            deps.ledger._record_personal_binding(
                db, 'curated', kind, version, 'state', deps.ledger._owner, digest,
                provenance='source_review')


def read(repo, deps, kind='goals'):
    if kind in {'goals', 'responsibilities', 'household_tasks', 'open_items'}:
        review_personal_state(repo, deps, kind)
    return curated.read_authorized_state(repo / 'state', kind, deps.ledger, deps.viewer, deps.subjects)


def grant(deps, subject='adult1', kind='goals', operations=('ingest', 'disclose', 'view')):
    return deps.ledger.grant_consent(subject, subject, f'curated:{kind}', operations, deps.viewer)


def test_encrypted_open_items_remain_unavailable_without_decryption(scoped, monkeypatch):
    repo, deps = scoped
    directory = repo / 'state'
    monkeypatch.setattr(curated, '_personal_state_identity', _UNVERIFIED_PERSONAL_STATE_IDENTITY)
    (directory / 'open_items.md.age').write_bytes(b'synthetic encrypted fixture')
    monkeypatch.setitem(sys.modules, 'foundation', SimpleNamespace(
        get_age_vault=lambda **_: pytest.fail('encrypted curated State was decrypted')))
    with pytest.raises(PermissionDenied, match='encrypted personal scope unavailable'):
        curated.read_authorized_state(directory, 'open_items', deps.ledger, deps.viewer, deps.subjects)
    assert not (directory / 'open_items.md').exists()
    with pytest.raises(ValueError, match='invalid State File'):
        curated.read_state(directory, '../open_items')


def test_personal_state_identity_rejects_unreviewed_state_kinds(scoped):
    repo, deps = scoped
    with pytest.raises(PermissionDenied, match='personal scope unavailable'):
        _UNVERIFIED_PERSONAL_STATE_IDENTITY(repo / 'state', 'decisions', deps.ledger)


def test_personal_state_identity_denies_parent_lstat_errors_before_payload(scoped, monkeypatch):
    repo, deps = scoped
    directory = repo / 'state'
    original_lstat = Path.lstat

    def denied_parent_lstat(path):
        if path == directory:
            raise OSError('synthetic filesystem denial')
        return original_lstat(path)

    monkeypatch.setattr(Path, 'lstat', denied_parent_lstat)
    with pytest.raises(PermissionDenied, match='personal scope unavailable'):
        _UNVERIFIED_PERSONAL_STATE_IDENTITY(directory, 'goals', deps.ledger)


def test_personal_state_identity_denies_encrypted_path_lstat_errors(scoped, monkeypatch):
    repo, deps = scoped
    directory = repo / 'state'
    encrypted = directory / 'goals.md.age'
    original_lstat = Path.lstat

    def denied_encrypted_lstat(path):
        if path == encrypted:
            raise OSError('synthetic filesystem denial')
        return original_lstat(path)

    monkeypatch.setattr(Path, 'lstat', denied_encrypted_lstat)
    with pytest.raises(PermissionDenied, match='personal scope unavailable'):
        _UNVERIFIED_PERSONAL_STATE_IDENTITY(directory, 'goals', deps.ledger)


def test_unclassified_curated_state_is_denied_before_payload_read(scoped, monkeypatch):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved'}])
    monkeypatch.setattr(curated, 'read_state',
                        lambda *_args, **_kwargs: pytest.fail('unclassified State payload was read'))
    with pytest.raises(PermissionDenied, match='personal scope'):
        curated.read_authorized_state(repo / 'state', 'goals', deps.ledger, deps.viewer, deps.subjects)


def test_same_size_same_mtime_rewrite_remains_unavailable_before_payload_read(scoped, monkeypatch):
    repo, deps = scoped
    path = repo / 'state' / 'goals.md'
    original = 'goals:\n- id: G\n  owner: ved\n'
    path.write_text(original)
    info = path.stat()
    version = f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}'
    digest = ids.content_hmac(deps.ledger._id_key, version)
    with deps.ledger.transaction() as db:
        deps.ledger._record_personal_binding(
            db, 'curated', 'goals', version, 'state', deps.ledger._owner, digest,
            provenance='source_review')

    path.write_text(original.replace('ved', 'bob'))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    changed = path.stat()
    assert (changed.st_dev, changed.st_ino, changed.st_size, changed.st_mtime_ns) == (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    monkeypatch.setattr(curated, '_personal_state_identity', _UNVERIFIED_PERSONAL_STATE_IDENTITY)
    monkeypatch.setattr(curated, 'read_state',
                        lambda *_args, **_kwargs: pytest.fail('unverified State payload was read'))
    with pytest.raises(PermissionDenied, match='personal scope unavailable'):
        curated.read_authorized_state(repo / 'state', 'goals', deps.ledger, deps.viewer, deps.subjects)


@pytest.mark.parametrize('text', ['- id: 1\n', '- id: ""\n', '- id: null\n'])
def test_missing_open_item_identity_remains_a_review_error(text):
    with pytest.raises(ValueError, match='identity needs review'):
        curated.records(text, 'open_items')


@pytest.mark.parametrize('record', [
    {'co_subjects': 'kid1'}, {'co_subjects': [None]}, {'goal_co_subjects': 'kid1'},
    {'goal_subject': 1}, {'subject': 1}])
def test_primary_accountability_cannot_repair_malformed_completion_subjects(record):
    with pytest.raises(ValueError, match='resolved identities'):
        curated.open_item_subjects(record, 'owner')


def test_shared_record_reader_retains_legacy_shapes_and_bounds():
    assert curated.records('---\ngoals: {G-1: {owner: owner}}\n---\n', 'goals') == [
        {'id': 'G-1', 'owner': 'owner'}]
    assert curated.records(' \n', 'goals') == []
    for text in ['goals: [invalid]\n', '- not_a_record\n']:
        with pytest.raises(ValueError, match='State File'):
            curated.records(text, 'goals')
    with pytest.raises(ValueError, match='review limit'):
        curated.records('x' * (2 * 1024 * 1024 + 1), 'open_items')


def test_unclassified_other_state_kinds_remain_unavailable_after_consent(scoped, monkeypatch):
    repo, deps = scoped
    text = '# Synthetic decisions\n'
    (repo / 'state' / 'decisions.md').write_text(text)
    monkeypatch.setattr(curated, 'read_state',
                        lambda *_args, **_kwargs: pytest.fail('unclassified State payload was read'))
    with pytest.raises(PermissionDenied, match='personal scope'):
        read(repo, deps, 'decisions')
    grant(deps, 'adult1', 'decisions')
    grant(deps, 'adult2', 'decisions')
    with pytest.raises(PermissionDenied, match='personal scope'):
        read(repo, deps, 'decisions')


def test_runtime_and_command_keep_allowed_goals_without_unrelated_adult_consent(scoped):
    repo, deps = scoped
    write(repo, 'goals', [
        {'id': 'allowed', 'owner': 'ved', 'subject': 'adult1', 'title': 'Allowed progress', 'status': 'active'},
        {'id': 'private', 'owner': 'ved', 'subject': 'adult2', 'title': 'PRIVATE TEXT', 'status': 'active'}])
    review_personal_state(repo, deps, 'goals')
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


def test_untagged_open_items_require_consent_until_tagged_or_consented(scoped, monkeypatch):
    repo, deps = scoped
    rows = [{'id': 'OI-1', 'description': 'PRIVATE TEXT', 'deadline': '2026-11-02', 'status': 'open'}]
    (repo / 'state' / 'open_items.md').write_text(yaml.safe_dump(rows, sort_keys=False))
    assert issubclass(PermissionDenied, RuntimeError)
    with pytest.raises(PermissionDenied, match='consent'):
        read(repo, deps, 'open_items')
    assert deps.incumbent is None
    monkeypatch.setattr(deps.ledger, 'require_personal_fact_admission', lambda: None)
    # Owner action 1: tag each record with its owner; no unrelated consent is then needed.
    (repo / 'state' / 'open_items.md').write_text(yaml.safe_dump([{**rows[0], 'owner': 'ved'}], sort_keys=False))
    review_personal_state(repo, deps, 'open_items')
    assert [row['id'] for row in curated.records(read(repo, deps, 'open_items'), 'open_items')] == ['OI-1']
    # Owner action 2: each consent-required adult grants scoped consent for curated:open_items.
    (repo / 'state' / 'open_items.md').write_text(yaml.safe_dump(rows, sort_keys=False))
    review_personal_state(repo, deps, 'open_items')
    grant(deps, 'adult1', 'open_items')
    with pytest.raises(PermissionDenied, match='consent'):
        read(repo, deps, 'open_items')
    grant(deps, 'adult2', 'open_items')
    assert [row['id'] for row in curated.records(read(repo, deps, 'open_items'), 'open_items')] == ['OI-1']


def test_open_items_hybrid_respects_unscoped_and_co_subject_consent(scoped, monkeypatch):
    repo, deps = scoped
    monkeypatch.setattr(deps.ledger, 'require_personal_fact_admission', lambda: None)
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
    assert deps.incumbent is None

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


def test_withheld_records_cannot_consume_bounds_or_inject_validation_gaps(scoped, monkeypatch):
    repo, deps = scoped
    monkeypatch.setattr(deps.ledger, 'require_personal_fact_admission', lambda: None)
    private = [{'id': f'PRIVATE-{i}', 'owner': 'ved', 'subject': 'adult2',
                'metric': {'subject': 'unknown'}, 'milestones': [{'visible_to': False}]}
               for i in range(110)]
    write(repo, 'responsibilities', private + [{'id': 'allowed', 'owner': 'ved', 'outcome': 'Allowed'}])
    review_personal_state(repo, deps, 'responsibilities')
    got = deps.responsibility_lines()
    assert not got.gaps and len(got.lines) == 1 and 'allowed' in got.lines[0]
    write(repo, 'goals', private)
    review_personal_state(repo, deps, 'goals')
    assert deps.goal_lines().lines == [] and not deps.goal_lines().gaps
    (repo / 'state' / 'household_tasks.md.age').write_bytes(b'synthetic ciphertext, never decrypted')
    assert deps.incumbent is None


def test_orphan_sprint_remains_a_review_gap_after_conservative_authorization(scoped):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'status': 'active'}],
        sprints=[{'id': 'orphan', 'goal_ref': 'absent', 'status': 'active'}])
    review_personal_state(repo, deps, 'goals')
    grant(deps)
    grant(deps, 'adult2')
    assert any('Sprint review needed' in line for line in deps.goal_lines().lines)


def test_permission_change_during_projection_refuses_the_entire_read(scoped, monkeypatch):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'subject': 'adult1'}])
    review_personal_state(repo, deps, 'goals')
    permission = grant(deps)
    dump = curated.yaml.safe_dump
    def revoke(*args, **kwargs):
        text = dump(*args, **kwargs)
        deps.ledger.revoke_consent(permission)
        return text
    monkeypatch.setattr(curated.yaml, 'safe_dump', revoke)
    with pytest.raises(PermissionDenied, match='permissions changed'):
        read(repo, deps)


def test_state_swap_during_projection_refuses_the_entire_read(scoped, monkeypatch):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'title': 'synthetic original'}])
    review_personal_state(repo, deps, 'goals')
    grant(deps)
    original_read = curated.read_state

    def swap_after_read(directory, name):
        text = original_read(directory, name)
        write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'title': 'synthetic replacement'}])
        return text

    monkeypatch.setattr(curated, 'read_state', swap_after_read)
    with pytest.raises(PermissionDenied, match='changed during retrieval'):
        read(repo, deps)


@pytest.mark.parametrize(('target', 'mode', 'attributes'), [
    ('directory', stat.S_IFLNK, 0),
    ('file', stat.S_IFLNK, 0),
    ('file', stat.S_IFREG, 0x400),
])
def test_linked_or_reparse_curated_state_is_denied_before_payload_read(
        scoped, monkeypatch, target, mode, attributes):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved'}])
    review_personal_state(repo, deps, 'goals')
    directory = repo / 'state'
    path = directory if target == 'directory' else directory / 'goals.md'
    original_lstat = Path.lstat

    def linked_lstat(candidate):
        if candidate == path:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
        return original_lstat(candidate)

    monkeypatch.setattr(Path, 'lstat', linked_lstat)
    monkeypatch.setattr(curated, '_personal_state_identity', _UNVERIFIED_PERSONAL_STATE_IDENTITY)
    monkeypatch.setattr(curated, 'read_state',
                        lambda *_args, **_kwargs: pytest.fail('linked State payload was read'))
    with pytest.raises(PermissionDenied, match='personal scope unavailable'):
        curated.read_authorized_state(directory, 'goals', deps.ledger, deps.viewer, deps.subjects)


def test_command_rechecks_permission_after_goal_observation(scoped, monkeypatch):
    repo, deps = scoped
    write(repo, 'goals', [{'id': 'G', 'owner': 'ved', 'subject': 'adult1'}])
    permission = grant(deps)
    review_personal_state(repo, deps, 'goals')
    def observe(*args, **kwargs):
        deps.ledger.revoke_consent(permission)
        return runtime.ContextLines(())
    monkeypatch.setattr(runtime, '_goal_lines', observe)
    with pytest.raises(PermissionDenied):
        commands.execute(Namespace(command='goals'), deps, repo)


@pytest.mark.parametrize('kind', ['responsibilities', 'household_tasks', 'open_items'])
def test_other_curated_consumers_use_the_same_record_scope(scoped, kind, monkeypatch):
    repo, deps = scoped
    monkeypatch.setattr(deps.ledger, 'require_personal_fact_admission', lambda: None)
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
        review_personal_state(repo, deps, kind)
        lines = deps.responsibility_lines() if kind == 'responsibilities' else deps.coordination_lines()
        assert 'allowed' in str(lines.lines) and 'PRIVATE' not in str(lines.lines) and not lines.gaps


def test_unassigned_task_keeps_its_subject_scope_and_missing_owner_message(scoped, monkeypatch):
    repo, deps = scoped
    monkeypatch.setattr(deps.ledger, 'require_personal_fact_admission', lambda: None)
    write(repo, 'household_tasks', [{'id': 'H', 'subject': 'kid1', 'owner': '', 'obligation': 'Assign this'}])
    review_personal_state(repo, deps, 'household_tasks')
    got = deps.coordination_lines()
    assert not got.gaps and any('no accountable owner' in line for line in got.lines)


def test_completion_keeps_exact_record_binding_and_checks_scope_again(scoped):
    repo, deps = scoped
    row = {'id': 'R', 'owner': 'ved', 'subject': 'adult1', 'outcome': 'Allowed'}
    write(repo, 'responsibilities', [row, {'id': 'private', 'subject': 'adult2'}])
    review_personal_state(repo, deps, 'responsibilities')
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
