"""Every legacy script that writes installation files shares the capture fence (re-artha recovery, unfenced writers).

Each test runs the real script entry point in its own process with a synthetic installation.  Given a capture held by
another process, the writer must be refused, report it, and leave its files untouched.  Given a restored installation,
the writer must be refused the same way.  Once the capture ends, the identical run must succeed, which shows the fence
and nothing else caused the refusal.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'

_HOLD = '''import sys
from pathlib import Path
from catchup_core.session_lock import installation_fence
with installation_fence(Path(sys.argv[1]), exclusive=True):
    print('held', flush=True)
    sys.stdin.readline()
'''

_PRELUDE = '''import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, %r)
sys.path.insert(0, %r)
import lib.common
lib.common.ARTHA_DIR = root     # the audit middleware and other writers resolve the installation from here
''' % (str(SCRIPTS), str(SCRIPTS / 'lib'))


def _environment(local):
    return {**os.environ, 'PYTHONPATH': str(SCRIPTS), 'ARTHA_LOCAL_DIR': str(local)}


def _capture(local):
    child = subprocess.Popen([sys.executable, '-c', _HOLD, str(local)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env=_environment(local))
    assert child.stdout.readline().strip() == b'held'
    return child


def _run(body, root, local, stdin=''):
    return subprocess.run([sys.executable, '-X', 'utf8', '-c', _PRELUDE + textwrap.dedent(body), str(root)],
        env=_environment(local), input=stdin, capture_output=True, text=True, encoding='utf-8',
        timeout=90, cwd=str(root))


def _snapshot(root, names):
    return {name: (root / name).read_bytes() if (root / name).exists() else None for name in names}


def exercise(tmp_path, body, watched, *, stdin='', refused=(3,), seed=None, changed):
    """Refused during capture and in a restored tree with nothing touched; then the same run succeeds."""
    root = tmp_path / 'artha'
    root.mkdir()
    (root / 'state').mkdir()
    (root / 'config').mkdir()
    if seed:
        seed(root)
    local = tmp_path / 'local'
    before = _snapshot(root, watched)

    holder = _capture(local)
    try:
        during = _run(body, root, local, stdin)
    finally:
        holder.communicate(b'\n', timeout=10)
    assert during.returncode in refused, (during.returncode, during.stdout, during.stderr)
    assert 'another Catch-up owns this ledger' in during.stdout + during.stderr
    assert _snapshot(root, watched) == before
    assert not (root / 'remote-calls').exists()

    (local / '.artha-restored.json').write_text('{}')
    restored = _run(body, root, local, stdin)
    assert restored.returncode in refused, (restored.returncode, restored.stdout, restored.stderr)
    assert 'restored installation' in restored.stdout + restored.stderr
    assert _snapshot(root, watched) == before
    assert not (root / 'remote-calls').exists()
    (local / '.artha-restored.json').unlink()

    after = _run(body, root, local, stdin)
    assert after.returncode == 0, (after.stdout, after.stderr)
    assert changed(root), (after.stdout, after.stderr)
    return during, restored, after


def _calendar_seed(root):
    (root / 'in.jsonl').write_text(json.dumps({'type': 'event', 'title': 'Synthetic review',
        'start': '2030-01-02T10:00:00', 'source': 'gcal'}) + '\n')


def test_connector_coverage_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    from catchup_core.session_lock import SessionBusy
    from lib.connector_coverage import ConnectorRun, CoverageStore
    try:
        run = ConnectorRun(connector='synthetic', records=1, rejected=0,
                           rejection_reasons=[], error=None, scopes=[], elapsed=0)
        CoverageStore(root / 'coverage.json').record(run)
    except SessionBusy as exc:
        print(exc)
        raise SystemExit(3)
    '''
    exercise(tmp_path, body, ['coverage.json'], changed=lambda root: (
        json.loads((root / 'coverage.json').read_text())['connectors']['synthetic']['last_run']['records'] == 1))


def test_calendar_writer_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import calendar_writer as m
    m.STATE_DIR = root / 'state'
    m.CALENDAR_FILE = root / 'state' / 'calendar.md'
    sys.exit(m.main(['--input', str(root / 'in.jsonl')]))
    '''
    exercise(tmp_path, body, ['state/calendar.md'], seed=_calendar_seed,
        changed=lambda root: 'Synthetic review' in (root / 'state/calendar.md').read_text())


def test_health_check_writer_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import health_check_writer as m
    m.STATE_DIR = root / 'state'
    m.TMP_DIR = root / 'tmp'
    m.CONFIG_DIR = root / 'config'
    m.HEALTH_CHECK_FILE = m.STATE_DIR / 'health-check.md'
    m.CATCH_UP_RUNS_FILE = m.STATE_DIR / 'catch_up_runs.yaml'
    m.LOCK_FILE = m.STATE_DIR / '.artha-lock'
    sys.exit(m.main(['--last-catch-up', '2030-01-02T10:00:00+00:00', '--email-count', '3', '--config-hash', 'abc']))
    '''
    exercise(tmp_path, body, ['state/health-check.md', 'state/catch_up_runs.yaml', 'state/.artha-lock'],
        changed=lambda root: (root / 'state/health-check.md').exists())


def test_health_check_writer_skill_config_write_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import health_check_writer as m
    m.STATE_DIR = root / 'state'
    m.CONFIG_DIR = root / 'config'
    sys.exit(m.main(['--disable-skill', 'synthetic_skill']))
    '''
    def seed(root):
        (root / 'config/skills.yaml').write_text('skills:\n  synthetic_skill:\n    enabled: true\n')
    exercise(tmp_path, body, ['config/skills.yaml', 'state/audit.md'], seed=seed,
        changed=lambda root: 'enabled: false' in (root / 'config/skills.yaml').read_text())


def test_self_model_writer_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import lib.common
    lib.common.ARTHA_DIR = root
    import self_model_writer as m
    m._load_flag = lambda *args, **kwargs: True
    sys.exit(m.main())
    '''
    def seed(root):
        runs = ''.join(f'  - timestamp: "2030-01-0{n}T00:00:00Z"\n    briefing_format: standard\n' for n in range(1, 7))
        (root / 'state/health-check.md').write_text('---\ncatch_up_runs:\n' + runs + '---\n')
    exercise(tmp_path, body, ['state/self_model.md'], seed=seed,
        changed=lambda root: 'Artha Self-Model' in (root / 'state/self_model.md').read_text())


def test_decision_tracker_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import decision_tracker as m
    from catchup_core.session_lock import SessionBusy
    proposal = m.DecisionProposal(title='Synthetic decision', options=['a', 'b'], deadline='2030-02-01',
        visibility='private', source='test')
    try:
        m.DecisionTracker().persist_proposal(proposal, root / 'state' / 'decisions.md')
    except SessionBusy as exc:
        print('REFUSED', exc, file=sys.stderr)
        sys.exit(3)
    '''
    def seed(root):
        (root / 'state/decisions.md').write_text('| id | title |\n')
    exercise(tmp_path, body, ['state/decisions.md'], seed=seed,
        changed=lambda root: 'Synthetic decision' in (root / 'state/decisions.md').read_text())


def test_pr_manager_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import pr_manager as m
    m._PR_STATE_FILE = root / 'state' / 'pr_manager.md'
    sys.exit(m.main(['--log-post', 'linkedin', 'Synthetic topic', 'thread-1']))
    '''
    def seed(root):
        (root / 'state/pr_manager.md').write_text(
            '---\ndomain: pr_manager\n---\n## Post History\n| Date | Platform | Thread | Topic | Score | Result | Notes |\n'
            '|---|---|---|---|---|---|---|\n')
    exercise(tmp_path, body, ['state/pr_manager.md'], seed=seed,
        changed=lambda root: 'Synthetic topic' in (root / 'state/pr_manager.md').read_text())


def test_pr_manager_derived_snapshot_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import pr_manager as m
    from catchup_core.session_lock import SessionBusy
    path = root / 'state' / 'pr_manager.md'
    try:
        ok = m.write_derived_snapshot('2030-03-01', 4, pr_state_path=path)
    except SessionBusy as exc:
        print('REFUSED', exc, file=sys.stderr)
        sys.exit(3)
    sys.exit(0 if ok else 1)
    '''
    def seed(root):
        (root / 'state/pr_manager.md').write_text(
            '---\nderived_snapshot:\n  next_occasion_date: null\n  pending_draft_count: 0\n  last_scan_at: null\n---\nbody\n')
    exercise(tmp_path, body, ['state/pr_manager.md'], seed=seed,
        changed=lambda root: '2030-03-01' in (root / 'state/pr_manager.md').read_text())


@pytest.mark.parametrize('writer', ['decision', 'post', 'snapshot'])
def test_legacy_writer_serializes_the_entire_update(tmp_path, writer):
    root, local = tmp_path / 'artha', tmp_path / 'local'
    (root / 'state').mkdir(parents=True)
    name = 'decisions.md' if writer == 'decision' else 'pr_manager.md'
    path = root / 'state' / name
    path.write_text(
        '---\nderived_snapshot:\n  next_occasion_date: null\n  pending_draft_count: 0\n---\n'
        '## Post History\n| Date | Platform | Thread | Topic | Score | Result | Notes |\n'
        '|---|---|---|---|---|---|---|\n', encoding='utf-8')
    if writer == 'decision':
        operation = '''
        import decision_tracker as m
        m.DecisionTracker().persist_proposal(
            m.DecisionProposal(title='Synthetic decision', source='test'), path)
        '''
    elif writer == 'post':
        operation = '''
        import pr_manager as m
        m._PR_STATE_FILE = path
        assert m.log_post('linkedin', 'Synthetic topic', 'thread-1')
        '''
    else:
        # The explicit positional path must determine both the fence and the writer lock.
        operation = '''
        import pr_manager as m
        assert m.write_derived_snapshot('2030-03-01', 4, path)
        '''
    body = f"path = root / 'state' / {name!r}\n" + textwrap.dedent(operation)
    held_body = '''
    original_read = Path.read_text
    def paused_read(self, *args, **kwargs):
        if self == path:
            print('reading', flush=True)
            sys.stdin.readline()
        return original_read(self, *args, **kwargs)
    Path.read_text = paused_read
    '''
    prefix, operation = body.split('\n', 1)
    holder = subprocess.Popen(
        [sys.executable, '-c', _PRELUDE + prefix + '\n' + textwrap.dedent(held_body) + operation, str(root)],
        env=_environment(local), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(root))
    before = path.read_bytes()
    try:
        assert holder.stdout.readline().strip() == b'reading'
        contender = _run(body, root, local)
        assert contender.returncode != 0, (contender.stdout, contender.stderr)
        assert 'another Catch-up owns this ledger' in contender.stdout + contender.stderr
        assert path.read_bytes() == before
        assert not path.with_suffix('.tmp').exists()
    finally:
        output, errors = holder.communicate(b'\n', timeout=30)
    assert holder.returncode == 0, (output, errors)
    assert path.read_bytes() != before
    after = _run(body, root, local)
    assert after.returncode == 0, (after.stdout, after.stderr)
    (root / '.artha-restored.json').write_text('{}')
    before = path.read_bytes()
    restored = _run(body, root, local)
    assert restored.returncode != 0
    assert 'restored installation' in restored.stdout + restored.stderr
    assert path.read_bytes() == before


def test_todo_sync_is_excluded_by_capture_and_restore_before_any_remote_call(tmp_path):
    body = '''
    import types
    import todo_sync as m
    m.OPEN_ITEMS_FILE = str(root / 'state' / 'open_items.md')
    m.AUDIT_FILE = str(root / 'state' / 'audit.md')
    m.CONFIG_FILE = str(root / 'config' / 'artha_config.yaml')
    def remote(*args, **kwargs):
        (root / 'remote-calls').write_text('called')
        return {'id': 'synthetic-task-id'}
    m._graph_request = remote
    oauth = types.ModuleType('setup_msgraph_oauth')
    def token():
        (root / 'remote-calls').write_text('token')
        return {'access_token': 'synthetic'}
    oauth.ensure_valid_token = token
    sys.modules['setup_msgraph_oauth'] = oauth
    sys.argv = ['todo_sync.py', '--push']
    m.main()
    '''
    def seed(root):
        (root / 'state/open_items.md').write_text(
            '- id: OI-900\n  description: Synthetic item\n  source_domain: general\n  priority: P1\n'
            '  status: open\n  todo_id: \n')
        (root / 'config/artha_config.yaml').write_text('todo_lists:\n  general: list-1\n')
    def changed(root):
        (root / 'remote-calls').unlink()
        return 'synthetic-task-id' in (root / 'state/open_items.md').read_text()
    exercise(tmp_path, body, ['state/open_items.md', 'state/audit.md'], seed=seed, refused=(4,), changed=changed)


def test_import_transactions_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    from datetime import date
    import import_transactions as m
    from lib import sealed_state
    m.STATE_DIR = root / 'state'
    def vault(verb, domain, stdin=None):
        (root / 'state' / (domain + '.md')).write_text(stdin)
        return ''
    sealed_state._vault = vault
    m.parse_all_files = lambda directory: [m.Txn(date(2030, 1, 5), 'Synthetic grocer', 12.5, 'Groceries', '0000',
        'Synthetic Bank', 'Owner', 'debit', 'synthetic.csv')]
    m.main()
    '''
    def seed(root):
        (root / 'state/finance.md').write_text('---\ndomain: finance\n---\n# Finance\n## Archive\nold\n')
    exercise(tmp_path, body, ['state/finance.md', 'state/transactions.md'], stdin='y\n', seed=seed,
        changed=lambda root: 'Synthetic grocer' in (root / 'state/transactions.md').read_text())


def test_sealed_state_write_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    from lib import sealed_state
    from catchup_core.session_lock import SessionBusy
    def vault(verb, domain, stdin=None):
        (root / 'state' / (domain + '.md')).write_text(stdin)
        return ''
    sealed_state._vault = vault
    try:
        sealed_state.write_domain('finance', 'synthetic body', root / 'state')
    except SessionBusy as exc:
        print('REFUSED', exc, file=sys.stderr)
        sys.exit(3)
    '''
    exercise(tmp_path, body, ['state/finance.md'],
        changed=lambda root: (root / 'state/finance.md').read_text() == 'synthetic body')


@pytest.mark.skipif(not (SCRIPTS / 'work_bootstrap.py').exists(), reason='work_bootstrap.py is not part of this checkout')
def test_work_bootstrap_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import work_bootstrap as m
    m._WORK_STATE_DIR = root / 'state' / 'work'
    sys.exit(m.main(['--answers', '{"org": "Synthetic Org"}', '--profile', str(root / 'config' / 'user_profile.yaml')]))
    '''
    exercise(tmp_path, body, ['config/user_profile.yaml'],
        changed=lambda root: 'Synthetic Org' in (root / 'config/user_profile.yaml').read_text())


@pytest.mark.skipif(not (SCRIPTS / 'work_loop.py').exists(), reason='work_loop.py is not part of this checkout')
def test_work_loop_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import work_loop as m
    base = root / 'state' / 'work'
    m._REPO_ROOT = root
    m._BRIDGE_DIR = root / 'state' / 'bridge'
    m._WORK_STATE_DIR = base
    m._AUDIT_FILE = base / 'work-audit.md'
    m._SUMMARY_FILE = base / 'work-summary.md'
    m._METRICS_FILE = base / 'eval' / 'work-metrics.json'
    m._AGENCY_TRACKER_FILE = base / '.agency_tracker.json'
    sys.exit(m.main(['--mode', 'read', '--state-dir', str(base)]))
    '''
    def seed(root):
        (root / 'state/work').mkdir()
    exercise(tmp_path, body, ['state/work/work-audit.md', 'state/work/work-summary.md'], seed=seed, refused=(1,),
        changed=lambda root: (root / 'state/work/work-audit.md').exists())


def test_credential_setup_scripts_fence_on_the_environment_local_directory(tmp_path, monkeypatch):
    """Given ARTHA_LOCAL_DIR and a capture there, when a client credential is written, then it is refused untouched."""
    import google_auth
    from catchup_core.session_lock import SessionBusy
    local = tmp_path / 'local'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setattr(google_auth, '_client_backend', lambda: pytest.fail('backend touched during capture'))
    holder = _capture(local)
    try:
        with pytest.raises(SessionBusy):
            google_auth._keychain_write('synthetic-service', 'synthetic-value')
    finally:
        holder.communicate(b'\n', timeout=10)


def test_credential_and_state_writers_do_not_fence_on_a_home_relative_path():
    """Every fence holder in this set must use the directory capture uses, so none may hard-code the home directory."""
    names = ['google_auth.py', 'setup_msgraph_oauth.py', 'setup_icloud_auth.py', 'todo_sync.py', 'pr_manager.py',
        'health_check_writer.py', 'self_model_writer.py', 'decision_tracker.py', 'calendar_writer.py',
        'import_transactions.py', 'lib/state_writer.py', 'lib/sealed_state.py', 'work_bootstrap.py', 'work_loop.py']
    offenders = [name for name in names if (SCRIPTS / name).exists()
        and "installation_fence(Path.home()" in (SCRIPTS / name).read_text(encoding='utf-8')]
    assert offenders == []


def test_pipeline_checkpoint_writer_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import pipeline as m
    from catchup_core.session_lock import SessionBusy
    m._REPO_ROOT = root
    m._CHECKPOINT_JSON = root / 'state' / 'checkpoint.json'
    try:
        m._write_checkpoint('synthetic-session', 'fetch', {})
    except SessionBusy as exc:
        print('REFUSED', exc, file=sys.stderr)
        sys.exit(3)
    '''
    exercise(tmp_path, body, ['state/checkpoint.json'],
        changed=lambda root: 'synthetic-session' in (root / 'state/checkpoint.json').read_text())


@pytest.mark.parametrize('accepted', [False, True])
def test_pipeline_buffer_reads_and_truncation_share_the_configured_local_root(tmp_path, monkeypatch, accepted):
    import pipeline
    import lib.state_writer
    from datetime import UTC, datetime
    from types import SimpleNamespace
    local, home, repo = tmp_path / 'local', tmp_path / 'home', tmp_path / 'repo'
    local.mkdir()
    (home / '.artha-local').mkdir(parents=True)
    (repo / 'state').mkdir(parents=True)
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    expected = json.dumps({'ts': datetime.now(UTC).isoformat(), 'event': 'synthetic-configured-local'}) + '\n'
    (local / 'home_events_buffer.jsonl').write_text(expected)
    unrelated = home / '.artha-local/home_events_buffer.jsonl'
    unrelated.write_text('canonical-home-buffer-must-remain-untouched\n')
    observed = []
    def write(path, text, **kwargs):
        observed.append((path, text))
        return SimpleNamespace(success=accepted)
    monkeypatch.setattr(lib.state_writer, 'write', write)
    pipeline._merge_home_events_buffer(repo)
    assert len(observed) == 1 and 'synthetic-configured-local' in observed[0][1]
    assert (local / 'home_events_buffer.jsonl').read_text() == ('' if accepted else expected)
    assert unrelated.read_text() == 'canonical-home-buffer-must-remain-untouched\n'


def test_pipeline_bridge_health_reads_the_configured_local_dlq(tmp_path, monkeypatch):
    import pipeline
    local, home, repo = tmp_path / 'local', tmp_path / 'home', tmp_path / 'repo'
    local.mkdir()
    (home / '.artha-local').mkdir(parents=True)
    (repo / 'state').mkdir(parents=True)
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    (local / 'bridge_dlq.yaml').write_text('- synthetic one\n- synthetic two\n')
    (home / '.artha-local/bridge_dlq.yaml').write_text('- unrelated\n')
    health = repo / 'state/health-check.md'
    health.write_text('# Synthetic health\n')
    pipeline._write_bridge_health_section(repo)
    assert '| DLQ depth | `2`' in health.read_text(encoding='utf-8')


def test_post_work_refresh_is_excluded_before_loop_and_log(tmp_path):
    body = '''
    import types
    import post_work_refresh as m
    loop = types.ModuleType('work_loop')
    class WorkLoop:
        def __init__(self, **kwargs):
            (root / 'remote-calls').write_text('loop-entered')
        def run(self):
            return types.SimpleNamespace(errors=[], stages_completed=['synthetic'])
    loop.WorkLoop = WorkLoop
    loop.LoopMode = types.SimpleNamespace(READ='read')
    sys.modules['work_loop'] = loop
    sys.exit(m.main(['--artha-dir', str(root)]))
    '''
    def changed(root):
        return (root / 'remote-calls').exists() and (root / 'state/work/eval/work-refresh-log.jsonl').exists()
    exercise(tmp_path, body, ['state/work/eval/work-refresh-log.jsonl'], changed=changed)


@pytest.mark.skipif(not (SCRIPTS / 'work_domain_writers.py').exists(), reason='private Work source is absent')
@pytest.mark.parametrize('writer', ['atomic', 'append'])
def test_work_domain_writers_are_excluded_by_capture_and_restore(tmp_path, writer):
    call = ("m.write_calendar_state([], {}, root / 'state/work-calendar.md')" if writer == 'atomic' else
            "m.add_source({'title': 'Synthetic source', 'url': 'https://example.invalid'}, root / 'state/work-calendar.md')")
    body = '''
    import work_domain_writers as m
    from catchup_core.session_lock import SessionBusy
    try:
        CALL
    except SessionBusy as exc:
        print('REFUSED', exc, file=sys.stderr)
        sys.exit(3)
    '''.replace('CALL', call)
    exercise(tmp_path, body, ['state/work-calendar.md'],
        changed=lambda root: (root / 'state/work-calendar.md').exists())


@pytest.mark.skipif(not (SCRIPTS / 'work_notes.py').exists(), reason='private Work source is absent')
@pytest.mark.parametrize('capture', ['notes', 'org-calendar', 'meeting'])
def test_work_notes_are_excluded_before_any_capture_write(tmp_path, capture):
    call = ("m.NotesWriter(root / 'state').write(m.NotesCapture.from_dict({'meeting_title': 'Synthetic review'}))"
            if capture == 'meeting' else
            f"m.cmd_remember({'org-calendar: Synthetic review' if capture == 'org-calendar' else 'Synthetic note'!r}, "
            "state_dir=root / 'state')")
    body = '''
    import work_notes as m
    from catchup_core.session_lock import SessionBusy
    try:
        CALL
    except SessionBusy as exc:
        print('REFUSED', exc, file=sys.stderr)
        sys.exit(3)
    '''.replace('CALL', call)
    exercise(tmp_path, body, ['state/work-notes.md', 'state/work-org-calendar.md'],
        changed=lambda root: any((root / 'state' / name).exists() for name in ['work-notes.md', 'work-org-calendar.md']))


@pytest.mark.skipif(not (SCRIPTS / 'work_warm_start.py').exists(), reason='private Work source is absent')
def test_work_warm_start_writer_is_excluded_by_capture_and_restore(tmp_path):
    body = '''
    import work_warm_start as m
    from catchup_core.session_lock import SessionBusy
    try:
        m.write_work_people(m.WarmStartAggregator(), root / 'state')
    except SessionBusy as exc:
        print('REFUSED', exc, file=sys.stderr)
        sys.exit(3)
    '''
    exercise(tmp_path, body, ['state/work-people.md'],
        changed=lambda root: (root / 'state/work-people.md').exists())
