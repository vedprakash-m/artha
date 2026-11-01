"""Reviewed source changes never grant consent, load providers or enable release."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from catchup_core import user_config
from test_user_surfaces import CIPHER, KEY

KNOWN = frozenset({'owner', 'kid', 'adult'})
REQUIRED = frozenset({'adult'})


def review_context():
    return {'actor': 'owner', 'owner': 'owner', 'permission_generation': 0,
            'cohort': {'configuration': 'synthetic-reviewed-configuration'}}


@pytest.fixture
def config(tmp_path):
    root = tmp_path / 'installation'
    (root / 'config').mkdir(parents=True)
    code = Path(__file__).resolve().parents[2]
    (root / 'config/connectors.yaml').write_text(yaml.safe_dump({'connectors': {
        'gmail': {'enabled': True, 'fetch': {'handler': 'scripts/connectors/google_email.py'}},
        'google_calendar': {'enabled': True, 'fetch': {'handler': 'scripts/connectors/google_calendar.py'}}}}))
    packs = root / 'scripts/catchup_core/packs'
    packs.mkdir(parents=True)
    shutil.copy(code / 'scripts/catchup_core/packs/kids_school.yaml', packs / 'kids_school.yaml')
    path = root / 'config/catchup_core.yaml'
    path.write_text('''# Preserve authored surrounding comments.
viewer: owner
subjects: [owner, kid, adult]
consent_required: [adult]
domains: [kids_school]
cutover: false
sources:
  calendar:
    connector: google_calendar
    account_scope: family
    subjects: [kid]
release: {owner_approved_at: old-approval}
''')
    return path


def source(**overrides):
    return {'connector': 'gmail', 'account_scope': 'personal', 'subjects': ['kid', 'adult'],
            'kwargs': {'query': 'label:school', 'max_results': 50}, **overrides}


def preview(config, value, key='source.school_email', **kwargs):
    kwargs.setdefault('review_context', review_context)
    return user_config.propose(config, key, json.dumps(value), known_subjects=KNOWN,
                               consent_subjects=REQUIRED, **kwargs)


def apply(config, value, digest, key='source.school_email', **kwargs):
    kwargs.setdefault('review_context', review_context)
    return user_config.apply(config, key, json.dumps(value), digest, known_subjects=KNOWN,
                             consent_subjects=REQUIRED, **kwargs)


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_source_add_update_remove_preview_exact_change_and_preserve_release(config, newline):
    config.write_bytes(config.read_text().replace('\n', newline).encode())
    before = config.read_text()
    result = preview(config, source())
    assert result['before'] is None and result['after'] == source() and not result['applied']
    assert 'school_email' in result['diff'] and result['release_invalidated'] is True
    assert result['consent_required_subjects'] == ['adult']
    assert result['consent_granted'] is False and config.read_text() == before
    assert apply(config, source(), result['apply_digest'])['applied']
    current = yaml.safe_load(config.read_text())
    assert current['sources']['school_email'] == source()
    assert current['cutover'] is False and current['release']['owner_approved_at'] == 'old-approval'
    assert config.read_text().startswith('# Preserve authored surrounding comments.\nviewer: owner\n')
    assert config.read_bytes().startswith(
        ('# Preserve authored surrounding comments.' + newline + 'viewer: owner' + newline).encode())
    changed = source(kwargs={'query': 'label:family'})
    result = preview(config, changed)
    assert result['before'] == source()
    apply(config, changed, result['apply_digest'])
    result = preview(config, None)
    assert result['after'] is None
    apply(config, None, result['apply_digest'])
    assert 'school_email' not in yaml.safe_load(config.read_text())['sources']


@pytest.mark.parametrize('changed', [
    {'connector': 'outlook_email'}, {'namespace': 'work'}, {'connector': 'invented'},
    {'subjects': ['stranger']}, {'subjects': []}, {'subjects': ['kid', 'kid']},
    {'account_scope': 'work'}, {'access_token': 'synthetic'}, {'kwargs': {'server': 'example.test'}},
    {'kwargs': {'url': 'https://example.test'}}, {'kwargs': {'command': 'echo synthetic'}},
    {'kwargs': {'sync_cursor': 'opaque'}}, {'kwargs': {'max_results': True}}, {'required': 'yes'},
    {'class': 'unregistered'}, {'calendar_kinds': {'primary': 'school'}},
])
def test_unknown_work_credential_and_unbounded_source_settings_refuse(config, changed):
    before = config.read_bytes()
    with pytest.raises(ValueError):
        preview(config, source(**changed))
    assert config.read_bytes() == before


def test_last_source_and_unknown_domain_source_cannot_be_removed_or_enrolled(config):
    with pytest.raises(ValueError, match='last|nonempty'):
        preview(config, None, key='source.calendar')
    with pytest.raises(ValueError, match='Domain|declared'):
        preview(config, source(), key='source.made_up')


def test_source_review_requires_resolved_current_household(config):
    with pytest.raises(ValueError, match='resolved'):
        user_config.propose(config, 'source.school_email', json.dumps(source()), review_context=review_context)


@pytest.mark.parametrize('context', [None, {}, lambda: {}, lambda: {'actor': 'adult', 'owner': 'owner',
    'permission_generation': 0, 'cohort': {'configuration': 'synthetic'}}])
def test_source_review_requires_current_owner_callback(config, context):
    before = config.read_bytes()
    with pytest.raises(ValueError, match='context|owner|authority'):
        preview(config, source(), review_context=context)
    assert config.read_bytes() == before


@pytest.mark.parametrize('field,value', [('actor', ''), ('owner', ''), ('permission_generation', True),
    ('permission_generation', -1), ('permission_generation', '0'), ('cohort', {}),
    ('cohort', {'configuration': ''}), ('cohort', {'configuration': 1}), ('cohort', {'': 'value'})])
def test_review_context_schema_refuses_invalid_authority(config, field, value):
    context = {**review_context(), field: value}
    with pytest.raises(ValueError, match='context|owner|authority|generation|cohort'):
        preview(config, source(), review_context=lambda: context)


@pytest.mark.parametrize('field,value', [('actor', 'adult'), ('permission_generation', 1),
    ('cohort', {'configuration': 'changed'})])
@pytest.mark.parametrize('during_write', [False, True])
def test_source_authority_changes_refuse_without_replacing_bytes(config, monkeypatch, field, value, during_write):
    context = review_context()
    callback = lambda: context
    result = preview(config, source(), review_context=callback)
    before = config.read_bytes()
    if during_write:
        original_fsync = user_config.os.fsync

        def changed_fsync(descriptor):
            original_fsync(descriptor)
            context[field] = value

        monkeypatch.setattr(user_config.os, 'fsync', changed_fsync)
    else:
        context[field] = value
    with pytest.raises(ValueError, match='review|changed|owner|authority'):
        apply(config, source(), result['apply_digest'], review_context=callback)
    assert config.read_bytes() == before
    assert not list(config.parent.glob('.artha-config-*'))


def test_reviewed_source_sync_changes_remains_held_but_false_is_allowed(config):
    with pytest.raises(ValueError, match='held|parity'):
        preview(config, source(kwargs={'sync_changes': True}))
    assert preview(config, source(kwargs={'sync_changes': False}))['after']['kwargs']['sync_changes'] is False


def test_review_context_requires_exact_schema_and_copies_mutable_cohort(config):
    context = review_context()
    with pytest.raises(ValueError, match='context'):
        preview(config, source(), review_context=lambda: {**context, 'unexpected': 'authority'})
    with pytest.raises(ValueError, match='context'):
        preview(config, source(), review_context=lambda: None)
    shown = preview(config, source(), review_context=lambda: context)
    context['cohort']['configuration'] = 'changed-in-place'
    with pytest.raises(ValueError, match='review|changed'):
        apply(config, source(), shown['apply_digest'], review_context=lambda: context)


def test_changed_owner_and_actor_pair_invalidates_review(config):
    shown = preview(config, source())
    context = {**review_context(), 'actor': 'adult', 'owner': 'adult'}
    before = config.read_bytes()
    with pytest.raises(ValueError, match='review|changed'):
        apply(config, source(), shown['apply_digest'], review_context=lambda: context)
    assert config.read_bytes() == before


def test_source_replacement_flushes_directory_after_rename(config, monkeypatch):
    import os
    import stat

    shown = preview(config, source())
    operations = []
    original_fsync, original_replace = user_config.os.fsync, user_config.os.replace

    def flushed(descriptor):
        operations.append('directory' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file')
        original_fsync(descriptor)

    def replaced(temporary, destination):
        operations.append('replace')
        original_replace(temporary, destination)

    monkeypatch.setattr(user_config.os, 'fsync', flushed)
    monkeypatch.setattr(user_config.os, 'replace', replaced)
    apply(config, source(), shown['apply_digest'])
    assert operations == (['file', 'replace'] if os.name == 'nt' else ['file', 'replace', 'directory'])


@pytest.mark.parametrize('value', ['{', '{"connector":"gmail","connector":"gmail"}', 'NaN',
                                  '"' + 'x' * 65536 + '"'])
def test_source_json_is_strict_and_bounded(config, value):
    with pytest.raises(ValueError, match='JSON|duplicate'):
        user_config.propose(config, 'source.school_email', value, known_subjects=KNOWN,
                            consent_subjects=REQUIRED, review_context=review_context)


@pytest.mark.parametrize('options', [{'sync_changes': 'false'}, {'query': '\ninvalid'},
    {'calendar_ids': []}, {'calendar_ids': ['school', 'school']}, {'calendar_ids': ['a/b']},
    {'calendar_ids': ['https://example.test']}, {'window_days': 367}])
def test_source_option_validation_is_not_bypassed_by_review_context(config, options):
    calendar = 'calendar_ids' in options or 'window_days' in options
    with pytest.raises(ValueError):
        preview(config, source(connector='google_calendar' if calendar else 'gmail', kwargs=options))


def test_calendar_selectors_and_kinds_require_exact_review(config):
    value = source(connector='google_calendar', kwargs={'calendar_ids': ['school'], 'window_days': 7},
                   calendar_kinds={'school': 'school'})
    shown = preview(config, value, key='source.calendar')
    assert apply(config, value, shown['apply_digest'], key='source.calendar')['applied']
    for kinds in ({'unknown': 'school'}, {'school': 'invalid'}):
        with pytest.raises(ValueError, match='calendar'):
            preview(config, {**value, 'calendar_kinds': kinds}, key='source.calendar')


def test_mailbox_folder_is_a_plain_name_and_registry_must_be_enabled(config):
    registry_path = config.parent / 'connectors.yaml'
    registry = yaml.safe_load(registry_path.read_text())
    registry['connectors']['gmail']['enabled'] = False
    registry_path.write_text(yaml.safe_dump(registry))
    with pytest.raises(ValueError, match='disabled'):
        preview(config, source())
    registry['connectors']['icloud_email'] = {'enabled': True,
        'fetch': {'handler': 'scripts/connectors/imap_email.py'}}
    registry_path.write_text(yaml.safe_dump(registry))
    assert preview(config, source(connector='icloud_email', kwargs={'folder': 'INBOX'}))['after']['kwargs']
    with pytest.raises(ValueError, match='folder'):
        preview(config, source(connector='icloud_email', kwargs={'folder': 'inbox/subfolder'}))


def test_missing_declared_source_and_invalid_source_key_refuse(config):
    with pytest.raises(ValueError, match='does not exist'):
        preview(config, None)
    with pytest.raises(ValueError, match='identifier'):
        preview(config, source(), key='source.Invalid')


def test_unavailable_oversized_and_malformed_configuration_refuse(config):
    before = config.read_bytes()
    with pytest.raises(ValueError, match='missing|unavailable'):
        preview(config.with_name('missing.yaml'), source())
    config.write_text('sources: [\n')
    with pytest.raises(ValueError, match='YAML'):
        preview(config, source())
    config.write_bytes(b'#' + b'x' * user_config._MAX_BYTES)
    with pytest.raises(ValueError, match='size'):
        preview(config, source())
    config.write_bytes(before)


@pytest.mark.parametrize('change', ['file', 'registry', 'pack', 'subjects', 'value', 'replay'])
def test_exact_preview_binding_rechecks_every_dependency(config, change):
    value = source()
    result = preview(config, value)
    if change == 'file':
        config.write_text(config.read_text() + '# changed\n')
    elif change in {'registry', 'pack'}:
        path = config.parent / 'connectors.yaml' if change == 'registry' else (
            config.parent.parent / 'scripts/catchup_core/packs/kids_school.yaml')
        path.write_bytes(path.read_bytes() + b'\n# changed\n')
    elif change == 'subjects':
        with pytest.raises(ValueError, match='review|changed'):
            user_config.apply(config, 'source.school_email', json.dumps(value), result['apply_digest'],
                              known_subjects=KNOWN | {'new-person'}, consent_subjects=REQUIRED,
                              review_context=review_context)
        return
    elif change == 'value':
        value = source(kwargs={'query': 'different'})
    else:
        apply(config, value, result['apply_digest'])
    with pytest.raises(ValueError, match='review|changed'):
        apply(config, value, result['apply_digest'])


@pytest.mark.parametrize('unsafe', ['target_symlink', 'parent_symlink', 'target_hardlink', 'duplicate', 'alias'])
def test_unsafe_config_shapes_refuse_without_altering_target(config, tmp_path, unsafe):
    target = config
    if unsafe == 'target_symlink':
        target = config.with_name('linked.yaml')
        target.symlink_to(config)
    elif unsafe == 'parent_symlink':
        linked = tmp_path / 'linked'
        linked.symlink_to(config.parent)
        target = linked / config.name
    elif unsafe == 'target_hardlink':
        import os
        os.link(config, config.with_name('another.yaml'))
    elif unsafe == 'duplicate':
        config.write_text(config.read_text() + '\nsources: {}\n')
    else:
        config.write_text(config.read_text() + '\nanchor: &shared [kid]\ncopy: *shared\n')
    before = config.read_bytes()
    with pytest.raises(ValueError):
        preview(target, source())
    assert config.read_bytes() == before


def test_two_real_processes_can_apply_a_review_only_once(config):
    result = preview(config, source())
    script = '''
import json,sys
from pathlib import Path
from catchup_core.user_config import apply
try:
    apply(Path(sys.argv[1]),'source.school_email',sys.argv[2],sys.argv[3],
            known_subjects=frozenset({'owner','kid','adult'}),consent_subjects=frozenset({'adult'}),
            review_context=lambda: json.loads(sys.argv[4]))
except (ValueError,RuntimeError):
    sys.exit(2)
'''
    import os
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'scripts'))
    children = [subprocess.Popen([sys.executable, '-c', script, str(config), json.dumps(source()),
                    result['apply_digest'], json.dumps(review_context())], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    results = [child.wait(timeout=15) for child in children]
    assert sorted(results) == [0, 2]
    assert yaml.safe_load(config.read_text())['sources']['school_email'] == source()


def test_bounded_url_text_in_gmail_query_is_search_text_not_a_destination(config):
    value = source(kwargs={'query': 'https://example.test/school'})
    assert preview(config, value)['after'] == value


@pytest.mark.parametrize('existing', [source(subjects=['stranger']), source(account_scope='work')])
def test_source_removal_revalidates_the_removed_declaration(config, existing):
    document = yaml.safe_load(config.read_text())
    document['sources']['school_email'] = existing
    config.write_text(yaml.safe_dump(document))
    before = config.read_bytes()
    with pytest.raises(ValueError, match='subjects|Work|scope'):
        preview(config, None)
    assert config.read_bytes() == before


def test_source_semantic_noop_cannot_be_approved_as_formatting_change(config):
    document = yaml.safe_load(config.read_text())
    document['sources']['school_email'] = source()
    config.write_text(yaml.safe_dump(document))
    before = config.read_bytes()
    with pytest.raises(ValueError, match='no change'):
        preview(config, source())
    assert config.read_bytes() == before


def test_source_apply_preserves_an_intervening_write_and_removes_temporary_file(config, monkeypatch):
    result = preview(config, source())
    before = config.read_text()
    original_read = user_config._read
    reads = 0

    def changed_read(path):
        nonlocal reads
        if path == config:
            reads += 1
            if reads == 2:
                config.write_text(before + '# concurrent authored change\n')
        return original_read(path)

    monkeypatch.setattr(user_config, '_read', changed_read)
    with pytest.raises(ValueError, match='changed|review'):
        apply(config, source(), result['apply_digest'])
    assert config.read_text() == before + '# concurrent authored change\n'
    assert not list(config.parent.glob('.artha-config-*'))


def test_source_apply_private_file_and_failed_replace_leave_no_temporary_payload(config, monkeypatch):
    import os
    import stat

    result = preview(config, source())
    before = config.read_bytes()
    original_replace = user_config.os.replace

    def failed_replace(temporary, destination):
        raise OSError('synthetic replacement failure')

    monkeypatch.setattr(user_config.os, 'replace', failed_replace)
    with pytest.raises(OSError, match='synthetic'):
        apply(config, source(), result['apply_digest'])
    assert config.read_bytes() == before
    assert not list(config.parent.glob('.artha-config-*'))
    monkeypatch.setattr(user_config.os, 'replace', original_replace)
    apply(config, source(), result['apply_digest'])
    if os.name != 'nt':
        assert stat.S_IMODE(config.stat().st_mode) == 0o600


@pytest.fixture
def command_deps(config):
    from catchup_core.blobs import EncryptedBlobStore
    from catchup_core.coordinator import Deps
    from catchup_core.domain_pack import load_pack_file
    from catchup_core.ledger import Ledger, LedgerError

    root = config.parent.parent
    (config.parent / 'user_profile.yaml').write_text('household: {type: family}\n')
    led = Ledger(root / 'ledger.db', id_key=KEY, cipher=CIPHER, known_subjects=KNOWN,
                 consent_subjects=REQUIRED, owner_id='owner').open()
    led.migrate()
    reads = []
    validity = {'valid': True}

    def validate_inputs():
        reads.append(True)
        if not validity['valid']:
            raise LedgerError('synthetic runtime inputs changed')

    deps = Deps(ledger=led, pack=load_pack_file(root / 'scripts/catchup_core/packs/kids_school.yaml'),
                blobs=EncryptedBlobStore(root / 'blobs', CIPHER, KEY), id_key=KEY, fetchers={},
                extractor=lambda _: '', subjects=tuple(sorted(KNOWN)), viewer='owner',
                now=lambda: datetime(2026, 10, 25, tzinfo=UTC), validate_inputs=validate_inputs)
    yield deps, reads, validity
    led.close()


def configure(config, deps, digest=None, *, key='source.school_email', value=None):
    from catchup_core.commands import execute

    return execute(Namespace(command='configure', key=key, value=json.dumps(source()) if value is None else value,
                             apply=digest), deps, config.parent.parent)


def test_real_configure_binds_every_authority_read_without_granting_consent(config, command_deps):
    deps, reads, _ = command_deps
    before = deps.ledger.conn.execute('SELECT * FROM consent').fetchall()
    previewed = configure(config, deps)
    assert len(reads) == 2
    assert configure(config, deps, previewed['apply_digest'])['applied']
    assert len(reads) == 5
    assert deps.ledger.conn.execute('SELECT * FROM consent').fetchall() == before
    assert not deps.ledger.consent_active('adult', 'ingest', 'owner', scope='school_email')


@pytest.mark.parametrize('change', ['revoke', 'actor', 'owner', 'cohort', 'inputs'])
@pytest.mark.parametrize('during_write', [False, True])
def test_real_configure_rechecks_live_authority_before_replacement(config, command_deps, monkeypatch,
                                                                  change, during_write):
    from catchup_core.ledger import LedgerError

    deps, _, validity = command_deps
    grant = deps.ledger.grant_consent('adult', 'adult', 'kids_school', ['ingest', 'view'], 'owner')
    previewed = configure(config, deps)
    before = config.read_bytes()

    def mutate():
        if change == 'revoke':
            deps.ledger.revoke_consent(grant)
        elif change == 'actor':
            deps.viewer = 'adult'
        elif change == 'owner':
            deps.ledger._owner = 'adult'
        elif change == 'cohort':
            (config.parent / 'user_profile.yaml').write_text('household: {type: single}\n')
        else:
            validity['valid'] = False

    if during_write:
        original_fsync = user_config.os.fsync

        def changed_fsync(descriptor):
            original_fsync(descriptor)
            mutate()

        monkeypatch.setattr(user_config.os, 'fsync', changed_fsync)
    else:
        mutate()
    with pytest.raises((ValueError, LedgerError), match='review|changed|owner|authority'):
        configure(config, deps, previewed['apply_digest'])
    assert config.read_bytes() == before
    assert not list(config.parent.glob('.artha-config-*'))


@pytest.mark.parametrize('key,value', [('source.school_email', None), ('bootstrap_days', '45')])
def test_configure_scalar_and_source_commands_both_bind_current_context(config, command_deps, key, value):
    deps, _, _ = command_deps
    previewed = configure(config, deps, key=key, value=value)
    before = config.read_bytes()
    grant = deps.ledger.grant_consent('adult', 'adult', 'kids_school', ['view'], 'owner')
    deps.ledger.revoke_consent(grant)
    with pytest.raises(ValueError, match='review|changed'):
        configure(config, deps, previewed['apply_digest'], key=key, value=value)
    assert config.read_bytes() == before
