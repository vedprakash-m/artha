"""Fresh worker metadata cannot reuse reviewed evidence after a correction/revocation.

All databases, privacy heads and credentials are synthetic; subprocesses never
consult a native credential store or import a provider handler.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from action_queue import compute_payload_hash
from actions.base import ActionProposal
from catchup_core.actions import ClaimRefused
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import FactInput, Ledger, SignalInput
from lib import action_evidence as evidence


@pytest.fixture
def case(tmp_path):
    root = tmp_path / 'installation'
    (root / 'config').mkdir(parents=True)
    pack = Path(__file__).resolve().parents[2] / 'scripts/catchup_core/packs/kids_school.yaml'
    dest = root / 'scripts/catchup_core/packs/kids_school.yaml'
    dest.parent.mkdir(parents=True)
    dest.write_bytes(pack.read_bytes())
    (root / 'config/catchup_core.yaml').write_text('synthetic: true\n')
    path = tmp_path / 'private/catchup_core/ledger.db'
    now = datetime.now(UTC)
    clock = lambda: now.isoformat()
    cipher = EnvelopeCipher(StaticKeyProvider({1: b'e' * 32}))
    with Ledger(path, id_key=HmacKey(1, b'i' * 32), cipher=cipher, owner_id='owner',
            consent_subjects=frozenset({'adult'}), known_subjects=frozenset({'owner', 'adult'}), clock=clock) as ledger:
        ledger.migrate()
        ledger.grant_consent('adult', 'adult', '*', ['view', 'export'], 'owner')
        sid, _ = ledger.upsert_signal(SignalInput('school_email', 'personal', 'mail-1', 'v1',
                                                 clock(), 'synthetic-hmac', 'synthetic-blob'))
        fact = FactInput('kids_school', 'deadline', 'SYNTHETIC_PRIVATE_VALUE', 'adult', clock(),
                         evidence=((sid, 0, 5),))
        fid = ledger.put_fact(fact)
        ledger.conn.execute('INSERT INTO connector_coverage(connector,account_scope,completed_at,status) '
                            "VALUES('school_email','personal',?,'complete')", (clock(),))
        checkpoint = ledger.seal_privacy()
        pre = {'core_derived': True, 'fact_versions': {fid: ledger.get_fact(fid, 'owner').recorded_at},
            'subjects': ['adult'], 'permission_generation': ledger._permission_generation(),
            'fresh_until': (now + timedelta(hours=1)).isoformat()}
        proposal = ActionProposal('synthetic', 'instruction_sheet', 'kids_school', 'Synthetic instruction', '',
            {'preconditions': pre}, 'high', 1, 'standard', False, None, None, source_skill='catchup_core')
        config = SimpleNamespace(domains=['kids_school'], viewer='owner', subjects=['owner', 'adult'],
            consent_required=frozenset({'adult'}), sources={'school_email': {'account_scope': 'personal'}})
        class Backend:
            def get_password(self, service, account):
                assert (service, account) == ('artha-privacy-authoritative', 'checkpoint')
                return json.dumps(asdict(checkpoint))
        context = evidence._metadata_context(root, ledger, config, Backend(), compute_payload_hash(proposal))
        queue = tmp_path / 'actions.db'
        sqlite3.connect(queue).close()
        evidence.bind(queue, proposal, context)
        yield SimpleNamespace(root=root, ledger=ledger, fact=fact, fid=fid, sid=sid, pre=pre, proposal=proposal,
                              context=context, queue=queue, config=config, now=now)


def verify(case, context=None):
    with closing(sqlite3.connect(case.queue)) as db:
        evidence.verify_worker(case.proposal, case.root, case.pre, context or case.context, db,
                               lambda: case.context['head'])


@pytest.fixture
def authority(case, tmp_path, monkeypatch):
    from catchup_core import crypto, runtime
    from catchup_core.contracts import CatchupRequest, ExecutionMode
    from tests.catchup_core.test_runtime import FakeKeyring, write_cfg

    backend = FakeKeyring()
    monkeypatch.setattr(crypto, 'system_keyring', lambda: backend)
    monkeypatch.setattr(runtime, 'system_keyring', lambda: backend)
    monkeypatch.setattr('socket.gethostname', lambda: 'synthetic-host')
    base = tmp_path / 'canonical'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(base))
    (case.root / 'state').mkdir()
    write_cfg(case.root, subjects=['owner', 'adult'], consent_required=['adult'], viewer='owner',
              authority_host='synthetic-host')

    def no_provider(*args):
        pytest.fail('authority inspection attempted provider or credential access')

    deps = runtime.build_deps(
        CatchupRequest('synthetic-authority', 'owner', 'cli', host='synthetic-host',
                       mode=ExecutionMode.AUTHORITATIVE),
        repo_root=case.root, environ={'ARTHA_LOCAL_DIR': str(base)}, keyring_backend=backend,
        handler_loader=no_provider, auth_loader=no_provider, safety_control=True)
    try:
        case.ledger = deps.ledger
        case.ledger.grant_consent('adult', 'adult', '*', ['view', 'export'], 'owner')
        case.sid, _ = case.ledger.upsert_signal(SignalInput(
            'school_email', 'personal', 'mail-1', 'v1', case.now.isoformat(), 'synthetic-hmac', 'synthetic-blob'))
        case.fact = replace(case.fact, evidence=((case.sid, 0, 5),))
        case.fid = case.ledger.put_fact(case.fact)
        case.ledger.set_coverage('school_email', 'personal', 'complete', completed_at=case.now.isoformat())
        case.ledger.seal_privacy()
        case.pre['fact_versions'] = {case.fid: case.ledger.get_fact_metadata(case.fid, 'owner').recorded_at}
        case.pre['permission_generation'] = case.ledger._permission_generation()
        case.backend, case.base = backend, base
        yield case
    finally:
        deps.ledger.close()


def test_real_authority_prepares_bound_metadata_without_writes_or_decryption(authority, monkeypatch):
    before = dict(authority.backend.store)
    monkeypatch.setattr(Ledger, '_decode_value', lambda *_: pytest.fail('authority decrypted a value'))
    evidence.verify_parent(authority.proposal, authority.root, authority.pre)
    context = evidence.prepare(authority.proposal, authority.root, authority.pre)
    assert context['ledger_path'] == str(authority.ledger._path.resolve())
    assert context['owner'] == 'owner'
    assert context['known_subjects'] == ['adult', 'owner']
    assert context['consent_subjects'] == ['adult']
    assert 'SYNTHETIC_PRIVATE_VALUE' not in json.dumps(context)
    evidence.bind(authority.queue, authority.proposal, context)
    reader = evidence.native_head_reader()
    assert reader() == context['head']
    with closing(sqlite3.connect(authority.queue)) as queue:
        evidence.verify_worker(authority.proposal, authority.root, authority.pre, context, queue, reader)
    assert authority.backend.store == before


@pytest.mark.parametrize('failure', [False, True])
def test_real_authority_is_read_only_and_closes_on_exit(authority, failure):
    connection = None
    try:
        with evidence.authorized_ledger(authority.root) as (ledger, config, backend):
            connection = ledger.conn
            assert ledger._read_only
            assert config.viewer == 'owner' and backend is authority.backend
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                connection.execute('DELETE FROM facts')
            if failure:
                raise RuntimeError('synthetic caller failure')
    except RuntimeError as error:
        assert failure and str(error) == 'synthetic caller failure'
    assert connection is not None
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


@pytest.mark.parametrize('phase', ['load', 'exit'])
def test_real_authority_refuses_config_swaps(authority, monkeypatch, phase):
    from catchup_core import runtime
    config_path = authority.root / 'config/catchup_core.yaml'

    def swap():
        config_path.write_bytes(config_path.read_bytes() + b'\n# synthetic policy swap\n')

    if phase == 'load':
        original = runtime.load_runtime_config

        def load_then_swap(path):
            config = original(path)
            swap()
            return config

        monkeypatch.setattr(runtime, 'load_runtime_config', load_then_swap)
    with pytest.raises(ClaimRefused, match='evidence policy changed'):
        with evidence.authorized_ledger(authority.root):
            assert phase == 'exit'
            swap()


@pytest.mark.parametrize('location', ['missing', 'inside_root'])
def test_authority_refuses_unknown_canonical_ledger(authority, monkeypatch, location):
    base = authority.root / 'private' if location == 'inside_root' else authority.base / 'unknown'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(base))
    before = dict(authority.backend.store)
    with pytest.raises(ClaimRefused, match='canonical authoritative evidence ledger is unavailable'):
        with evidence.authorized_ledger(authority.root):
            pytest.fail('unknown canonical ledger was authorized')
    assert not (base / 'catchup_core/ledger.db').exists()
    assert authority.backend.store == before


@pytest.mark.parametrize('privacy', ['pending', 'missing', 'ahead'])
def test_real_authority_refuses_unverified_privacy_history(authority, privacy):
    if privacy == 'pending':
        authority.ledger.conn.execute('INSERT INTO deletion_journal(tombstone_id,requested_at,state) '
                                     "VALUES('synthetic-tombstone',?,'requested')", (authority.now.isoformat(),))
    elif privacy == 'missing':
        del authority.backend.store['artha-privacy-authoritative', 'checkpoint']
    else:
        head = evidence._head(authority.backend)
        head['checkpoint_id'] += 1
        authority.backend.store['artha-privacy-authoritative', 'checkpoint'] = json.dumps(head)
    before = dict(authority.backend.store)
    with pytest.raises(ClaimRefused, match='privacy history or schema requires maintenance'):
        with evidence.authorized_ledger(authority.root):
            pytest.fail('unverified privacy history was authorized')
    assert authority.backend.store == before


def test_missing_privacy_head_is_refused_without_key_enrollment(authority):
    del authority.backend.store['artha-privacy-authoritative', 'checkpoint']
    before = dict(authority.backend.store)
    with pytest.raises(ClaimRefused, match='privacy checkpoint is missing'):
        evidence._head(authority.backend)
    assert authority.backend.store == before


def test_real_authority_refuses_pending_schema_without_migration(authority, monkeypatch):
    base = authority.base / 'older'
    path = base / 'catchup_core/ledger.db'
    migrations = Ledger.available_migrations()
    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(Ledger, 'available_migrations', staticmethod(lambda: migrations[:1]))
        with Ledger(path, id_key=HmacKey(1, b'i' * 32),
                    cipher=EnvelopeCipher(StaticKeyProvider({1: b'e' * 32}))) as ledger:
            assert ledger.migrate() == 1
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(base))
    before = path.read_bytes(), dict(authority.backend.store)
    with pytest.raises(ClaimRefused, match='privacy history or schema requires maintenance'):
        with evidence.authorized_ledger(authority.root):
            pytest.fail('pending schema was authorized')
    assert (path.read_bytes(), authority.backend.store) == before


@pytest.mark.parametrize('initialized', [False, True])
def test_real_household_authority_uses_existing_ids_without_enrollment(authority, initialized):
    from catchup_core.runtime import RuntimeConfigError
    from tests.catchup_core.test_runtime import write_cfg
    from zoneinfo import ZoneInfo

    write_cfg(authority.root, household_from_profile=True, subjects=[], consent_required=[], viewer='owner')
    profile = authority.root / 'config/user_profile.yaml'
    profile.write_text('family:\n  primary_user: {name: Synthetic Owner}\n'
                       '  spouse: {name: Synthetic Adult, dob: "2000-01-01"' +
                       (', id: adult' if initialized else '') + '}\n')
    before = profile.read_bytes(), dict(authority.backend.store)
    if not initialized:
        with pytest.raises(RuntimeConfigError, match='explicit initialization'):
            evidence.prepare(authority.proposal, authority.root, authority.pre)
    else:
        context = evidence.prepare(authority.proposal, authority.root, authority.pre)
        assert context['known_subjects'] == ['adult', 'owner']
        assert context['consent_subjects'] == ['adult']
        assert context['household_day'] == datetime.now(UTC).astimezone(
            ZoneInfo(context['household_timezone'])).date().isoformat()
        evidence.bind(authority.queue, authority.proposal, context)
        with closing(sqlite3.connect(authority.queue)) as queue:
            evidence.verify_worker(authority.proposal, authority.root, authority.pre, context, queue,
                                   evidence.native_head_reader())
    assert (profile.read_bytes(), authority.backend.store) == before


def test_metadata_authorization_has_no_plaintext_or_ledger_key(case, monkeypatch):
    monkeypatch.setattr(case.ledger, '_decode_value', lambda *_: pytest.fail('metadata decrypted a value'))
    metadata = case.ledger.get_fact_metadata(case.fid, 'owner', operation='export')
    assert not hasattr(metadata, 'value')
    assert 'SYNTHETIC_PRIVATE_VALUE' not in json.dumps(case.context)
    assert set(case.context['sources']['school_email']) == {'account_scope'}
    verify(case)


@pytest.mark.parametrize('mutation', ['version', 'revocation', 'subject', 'cancel', 'privacy_head',
    'config', 'profile', 'pack', 'alternate_db', 'expiry', 'provider', 'freshness', 'coverage'])
def test_worker_rejects_changed_or_untrusted_evidence(case, mutation):
    if mutation == 'version':
        case.ledger.put_fact(replace(case.fact, value='corrected'), fact_id=case.fid)
    elif mutation == 'revocation':
        case.ledger.conn.execute("UPDATE consent SET revoked_at=?,revocation_version=revocation_version+1", (case.now.isoformat(),))
    elif mutation == 'subject':
        case.ledger.conn.execute("UPDATE facts SET primary_subject_id='unknown' WHERE fact_id=?", (case.fid,))
    elif mutation == 'cancel':
        case.ledger.conn.execute("INSERT INTO calendar_cancellations(connector,account_scope,upstream_id,pending) "
            "VALUES('school_email','personal','mail-1',1)")
    elif mutation == 'privacy_head':
        case.context['head']['permission_generation'] += 1
    elif mutation in {'config', 'profile', 'pack'}:
        filename = {'config': 'config/catchup_core.yaml', 'profile': 'config/user_profile.yaml',
                    'pack': 'scripts/catchup_core/packs/kids_school.yaml'}[mutation]
        with (case.root / filename).open('a') as handle:
            handle.write('\n# altered policy\n')
    elif mutation == 'alternate_db':
        case.context['ledger_path'] = str(case.queue)
    elif mutation == 'expiry':
        case.context['expires_at'] = time.time() - 1
    elif mutation == 'provider':
        case.pre['provider_versions'] = {'event': 'v1'}
    elif mutation == 'freshness':
        case.pre['fresh_until'] = (case.now + timedelta(hours=49)).isoformat()
    else:
        case.ledger.conn.execute("UPDATE connector_coverage SET status='unavailable'")
    with pytest.raises(ClaimRefused):
        verify(case)


def test_new_source_version_invalidates_old_fact_before_extraction(case):
    case.ledger.upsert_signal(SignalInput('school_email', 'personal', 'mail-1', 'v2',
        (case.now + timedelta(seconds=1)).isoformat(), 'changed-hmac', 'new-blob'))
    with pytest.raises(ClaimRefused, match='source version'):
        verify(case)


def test_current_fact_cannot_self_declare_indefinite_freshness(case):
    case.pre['fresh_until'] = (case.now + timedelta(days=365)).isoformat()
    case.context['payload_hash'] = compute_payload_hash(case.proposal)
    evidence.bind(case.queue, case.proposal, case.context)
    with pytest.raises(ClaimRefused, match='freshness'):
        verify(case)


@pytest.mark.parametrize(('mutation', 'reason'), [
    ('household_day', 'household policy day'), ('path', 'namespace changed'),
    ('provider', 'fresh provider version'), ('expiry', 'freshness limit expired'),
    ('subjects', 'subject scope changed'), ('privacy', 'privacy generation'),
])
def test_bound_worker_reaches_each_evidence_refusal(case, mutation, reason):
    if mutation == 'household_day':
        case.context.update(household_day='2000-01-01', household_timezone='UTC')
    elif mutation == 'path':
        case.context['ledger_path'] = str(case.queue)
    elif mutation == 'provider':
        case.pre['provider_versions'] = {'event': 'v1'}
    elif mutation == 'expiry':
        case.pre['fresh_until'] = (case.now - timedelta(seconds=1)).isoformat()
    elif mutation == 'subjects':
        case.pre['subjects'] = ['owner']
    else:
        case.context['head']['permission_generation'] += 1
    case.context['payload_hash'] = compute_payload_hash(case.proposal)
    evidence.bind(case.queue, case.proposal, case.context)
    with pytest.raises(ClaimRefused, match=reason):
        verify(case)


@pytest.mark.parametrize('reader', ['missing', 'changed_before', 'changed_after'])
def test_worker_requires_current_privacy_head_on_both_sides(case, reader):
    heads = iter([case.context['head'], {}])
    read_head = {'missing': None, 'changed_before': lambda: {},
                 'changed_after': lambda: next(heads)}[reader]
    with closing(sqlite3.connect(case.queue)) as db, pytest.raises(ClaimRefused, match='privacy history'):
        evidence.verify_worker(case.proposal, case.root, case.pre, case.context, db, read_head)


@pytest.mark.parametrize(('mutation', 'reason'), [
    ('missing_fact', 'Domain is not configured'), ('domain', 'Domain is not configured'),
    ('no_evidence', 'declared, fresh source'), ('connector', 'outside declared Domain policy'),
    ('scope', 'outside declared Domain policy'), ('source_spec', 'outside declared Domain policy'),
    ('missing_coverage', 'coverage is incomplete'), ('coverage_time', 'coverage is incomplete'),
    ('future_fact', 'freshness exceeds'), ('future_signal', 'freshness exceeds'),
    ('old_coverage', 'freshness exceeds'),
])
def test_freshness_refuses_undeclared_missing_or_stale_sources(case, mutation, reason):
    if mutation == 'missing_fact':
        case.pre['fact_versions'] = {'unknown': case.now.isoformat()}
    elif mutation == 'domain':
        case.config.domains = []
    elif mutation == 'no_evidence':
        case.ledger.conn.execute('DELETE FROM fact_evidence')
    elif mutation == 'connector':
        case.ledger.conn.execute("UPDATE signals SET connector='undeclared'")
    elif mutation == 'scope':
        case.config.sources['school_email']['account_scope'] = 'other'
    elif mutation == 'source_spec':
        case.config.sources['school_email'] = None
    elif mutation == 'missing_coverage':
        case.ledger.conn.execute('DELETE FROM connector_coverage')
    elif mutation == 'coverage_time':
        case.ledger.conn.execute('UPDATE connector_coverage SET completed_at=NULL')
    elif mutation == 'old_coverage':
        case.ledger.conn.execute('UPDATE connector_coverage SET completed_at=?',
                                 ((case.now - timedelta(days=3)).isoformat(),))
    else:
        table = 'facts' if mutation == 'future_fact' else 'signals'
        case.ledger.conn.execute(f'UPDATE {table} SET observed_at=?',
                                 ((case.now + timedelta(minutes=1)).isoformat(),))
    with pytest.raises(ClaimRefused, match=reason):
        evidence.require_freshness(case.ledger.conn, case.root, case.config, case.pre, case.now)


@pytest.mark.parametrize('provenance', ['model_hypothesis', 'legacy_unverified', 'disputed'])
def test_worker_refuses_unverified_fact_provenance(case, provenance):
    case.ledger.conn.execute('UPDATE facts SET provenance_class=? WHERE fact_id=?', (provenance, case.fid))
    with pytest.raises(ClaimRefused, match='provenance'):
        verify(case)


def test_worker_refuses_correction_during_freshness_validation(case, monkeypatch):
    original = evidence.require_freshness

    def correct_after_metadata(*args):
        original(*args)
        case.ledger.put_fact(replace(case.fact, value='corrected during validation'), fact_id=case.fid)

    monkeypatch.setattr(evidence, 'require_freshness', correct_after_metadata)
    with pytest.raises(ClaimRefused, match='changed'):
        verify(case)


@pytest.mark.parametrize('boundary', ['verify_parent', 'prepare'])
def test_parent_refuses_correction_during_freshness_validation(case, monkeypatch, boundary):
    from contextlib import contextmanager
    original = evidence.require_freshness

    @contextmanager
    def synthetic_authority(root):
        backend = SimpleNamespace(get_password=lambda *_: json.dumps(case.context['head']))
        yield case.ledger, case.config, backend

    def correct_after_metadata(*args):
        original(*args)
        with closing(sqlite3.connect(case.ledger._path)) as competing, competing:
            competing.execute('UPDATE facts SET recorded_at=? WHERE fact_id=?',
                              ((case.now + timedelta(microseconds=1)).isoformat(), case.fid))

    monkeypatch.setattr(evidence, 'authorized_ledger', synthetic_authority)
    monkeypatch.setattr(evidence, 'require_freshness', correct_after_metadata)
    with pytest.raises(ClaimRefused, match='changed'):
        getattr(evidence, boundary)(case.proposal, case.root, case.pre)


def test_queue_claim_validates_evidence_under_write_transaction(case, monkeypatch):
    from action_queue import ActionQueue
    from lib import action_policy
    monkeypatch.setattr(ActionQueue, '_resolve_db_path', staticmethod(lambda _: case.queue))
    queue = ActionQueue(case.root)
    item = replace(case.proposal, source_skill=None, parameters={'title': 'Synthetic instruction'})
    original = action_policy.require_action_preconditions
    checks = []

    def require_transaction(*args, **kwargs):
        checks.append(queue._conn.in_transaction)
        original(*args, **kwargs)

    try:
        queue.propose(item)
        monkeypatch.setattr(action_policy, 'require_action_preconditions', require_transaction)
        queue.transition(item.id, 'approved', actor='owner')
        queue.transition(item.id, 'executing', actor='executor',
                         expected_payload_hash=compute_payload_hash(item), operation_key='synthetic-operation')
        assert checks == [True, True]
    finally:
        queue.close()


@pytest.mark.parametrize('mutation', ['correction', 'revocation'])
def test_real_process_rechecks_metadata_after_parent_preparation(case, tmp_path, mutation):
    """The child starts with a previously verified capability; mutation wins before its fresh read."""
    verify(case)
    ready, go = tmp_path / 'ready', tmp_path / 'go'
    payload = {'proposal': asdict(case.proposal), 'context': case.context, 'pre': case.pre,
               'root': str(case.root), 'queue': str(case.queue), 'ready': str(ready), 'go': str(go)}
    script = '''
import json,sqlite3,sys,time
from pathlib import Path
from actions.base import ActionProposal
from lib.action_evidence import verify_worker
value=json.loads(sys.stdin.read())
Path(value['ready']).touch()
deadline=time.monotonic()+10
while not Path(value['go']).exists():
    if time.monotonic()>deadline: raise RuntimeError('test synchronization timed out')
    time.sleep(.01)
try:
    with sqlite3.connect(value['queue']) as db:
        verify_worker(ActionProposal(**value['proposal']),Path(value['root']),value['pre'],value['context'],db,
                      lambda: value['context']['head'])
except Exception as exc:
    print(type(exc).__name__)
else:
    raise RuntimeError('changed evidence was authorized')
'''
    import os
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'scripts'))
    child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
    child.stdin.write(json.dumps(payload))
    child.stdin.close()
    deadline = time.monotonic() + 10
    try:
        while not ready.exists():
            assert child.poll() is None, child.stderr.read()
            assert time.monotonic() < deadline
            time.sleep(.01)
        if mutation == 'correction':
            case.ledger.put_fact(replace(case.fact, value='correction after parent check'), fact_id=case.fid)
        else:
            case.ledger.conn.execute('UPDATE consent SET revocation_version=revocation_version+1')
        go.touch()
        assert child.wait(timeout=10) == 0, child.stderr.read()
        assert child.stdout.read().strip() == 'ClaimRefused'
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def dispatch_queue(case):
    policy = case.root / 'config/actions.yaml'
    policy.write_text('live_trial: {held: []}\nactions:\n  instruction_sheet: {enabled: true}\n')
    digest = compute_payload_hash(case.proposal)
    with closing(sqlite3.connect(case.queue)) as db, db:
        db.execute('CREATE TABLE actions(id TEXT PRIMARY KEY,status TEXT,payload_hash TEXT,approved_payload_hash TEXT,'
                   'approved_by TEXT,expires_at TEXT,preview_shown_at TEXT,preview_payload_hash TEXT)')
        db.execute("INSERT INTO actions VALUES(?,?,?,?, 'owner',NULL,NULL,NULL)",
                   (case.proposal.id, 'executing', digest, digest))
        db.execute('CREATE TABLE effect_operations(operation_key TEXT PRIMARY KEY,action_id TEXT,payload_hash TEXT)')
        db.execute('INSERT INTO effect_operations VALUES(?,?,?)', ('synthetic-op', case.proposal.id, digest))
        db.execute('CREATE TABLE action_dispatch_receipts(operation_key TEXT NOT NULL,action_id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE queue_authority(singleton_key INTEGER PRIMARY KEY,generation INTEGER,host TEXT,'
                   'promoted_at TEXT,former_host_revoked_at TEXT,transfer_pending INTEGER,transfer_json TEXT)')
        db.execute("INSERT INTO queue_authority VALUES(1,1,NULL,'2026-10-24T00:00:00Z',NULL,0,NULL)")
        db.execute('CREATE TABLE action_dispatch_policies(action_id TEXT PRIMARY KEY,policy_root TEXT,policy_sha256 TEXT)')
        db.execute('INSERT INTO action_dispatch_policies VALUES(?,?,?)',
                   (case.proposal.id, str(case.root), hashlib.sha256(policy.read_bytes()).hexdigest()))


def worker_input(case):
    now = time.time()
    credentials = {'schema': 'artha.action-credentials.v1', 'module': 'actions.instruction_sheet',
        'payload_hash': compute_payload_hash(case.proposal), 'operation': 'execute', 'since': '',
        'nonce': 'b' * 32, 'issued_at': now, 'expires_at': now + 12, 'records': {}, 'api_keys': {}}
    import os
    return {'module': 'actions.instruction_sheet', 'proposal': asdict(case.proposal), 'parent': os.getpid(),
            'timeout': 10, 'operation': 'execute', 'since': '', 'queue_path': str(case.queue),
            'credentials': credentials, 'evidence': case.context}


def run_worker(value):
    import os
    script = '''
import json,sys
from types import SimpleNamespace
from lib import action_evidence,action_process
from actions.base import ActionResult
value=json.loads(sys.stdin.read())
# Synthetic privacy metadata only. No native backend or ledger key is available to this process.
action_evidence.native_head_reader=lambda: lambda: value['evidence']['head']
original=action_process.importlib.import_module
def load(name):
    if name=='actions.instruction_sheet':
        def execute(proposal):
            from catchup_core.crypto import system_keyring
            assert system_keyring().get_password('artha-ledger','key.1') is None
            return ActionResult('success','synthetic receipt',None,False,None)
        return SimpleNamespace(execute=execute, reconcile=lambda proposal, since='': execute(proposal))
    return original(name)
action_process.importlib.import_module=load
from io import StringIO
sys.stdin=StringIO(json.dumps(value))
action_process._worker()
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'scripts'))
    return subprocess.run([sys.executable, '-c', script], input=json.dumps(value), capture_output=True,
                          text=True, env=env, timeout=15, check=False)


def test_real_worker_checks_evidence_before_and_after_provider_only_install(case):
    dispatch_queue(case)
    value = worker_input(case)
    first = run_worker(value)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)['status'] == 'success'
    second = run_worker(value)
    assert second.returncode != 0
    with closing(sqlite3.connect(case.queue)) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 1


def test_real_worker_refuses_pending_authority_without_consuming_dispatch(case):
    dispatch_queue(case)
    with closing(sqlite3.connect(case.queue)) as db, db:
        db.execute('UPDATE queue_authority SET transfer_pending=1')
    result = run_worker(worker_input(case))
    assert result.returncode != 0
    with closing(sqlite3.connect(case.queue)) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


def bind_calendar(case):
    case.pre['provider_versions'] = {'event1': '"v1"'}
    case.proposal = replace(case.proposal, action_type='calendar_modify', parameters={
        'preconditions': case.pre, 'calendar_id': 'synthetic@example.test',
        'event_id': 'event1', 'expected_etag': '"v1"', 'updates': {'summary': 'Synthetic'}})
    case.context['payload_hash'] = compute_payload_hash(case.proposal)
    evidence.bind(case.queue, case.proposal, case.context)


def test_parent_provider_binding_defers_reads_but_keeps_real_fact_authority(authority, monkeypatch):
    from actions import calendar_modify
    bind_calendar(authority)
    monkeypatch.setattr(calendar_modify, 'build_service', lambda *args: pytest.fail('parent read a provider'))
    evidence.verify_parent(authority.proposal, authority.root, authority.pre)
    context = evidence.prepare(authority.proposal, authority.root, authority.pre)
    assert context['payload_hash'] == compute_payload_hash(authority.proposal)
    authority.ledger.put_fact(replace(authority.fact, value='Corrected'), fact_id=authority.fid)
    with pytest.raises(ClaimRefused):
        evidence.verify_parent(authority.proposal, authority.root, authority.pre)


@pytest.mark.parametrize('mutation', ['none', 'correction', 'permission', 'privacy_head'])
def test_provider_read_does_not_replace_current_worker_evidence_authority(case, monkeypatch, mutation):
    from actions import calendar_modify
    bind_calendar(case)
    verify(case)
    calls = []

    def read_event():
        calls.append('event')
        if mutation == 'correction':
            case.ledger.put_fact(replace(case.fact, value='Corrected during provider read'), fact_id=case.fid)
        elif mutation == 'permission':
            case.ledger.conn.execute('UPDATE consent SET revocation_version=revocation_version+1')
        elif mutation == 'privacy_head':
            case.context['head'] = {**case.context['head'], 'checkpoint_id': case.context['head']['checkpoint_id'] + 1}
        return {'id': 'event1', 'etag': '"v1"'}

    client = SimpleNamespace(
        calendarList=lambda: SimpleNamespace(get=lambda **kwargs: SimpleNamespace(execute=lambda: {
            'id': 'synthetic@example.test', 'accessRole': 'writer'})),
        events=lambda: SimpleNamespace(get=lambda **kwargs: SimpleNamespace(execute=read_event)))
    monkeypatch.setattr(calendar_modify, 'build_service', lambda *args: client)
    evidence.verify_provider(case.proposal, 'actions.calendar_modify')
    assert calls == ['event']
    if mutation == 'none':
        verify(case)
    else:
        with pytest.raises(ClaimRefused):
            verify(case)


def test_real_reconciliation_query_cannot_redisclose_revoked_evidence(case):
    dispatch_queue(case)
    with sqlite3.connect(case.queue) as db:
        db.execute("UPDATE actions SET status='unknown'")
    # The privacy change races after preparation of a previously valid capability.
    case.ledger.conn.execute('UPDATE consent SET revocation_version=revocation_version+1')
    value = worker_input(case)
    value['operation'] = value['credentials']['operation'] = 'reconcile'
    result = run_worker(value)
    assert result.returncode != 0
    assert 'evidence' in result.stderr.lower() or 'permission' in result.stderr.lower()
    with sqlite3.connect(case.queue) as db:
        assert db.execute('SELECT status FROM actions').fetchone()[0] == 'unknown'
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


def test_real_core_worker_requires_metadata_capability_before_loading_authority(case):
    dispatch_queue(case)
    value = worker_input(case)
    value['evidence'] = None
    result = run_worker(value)
    assert result.returncode != 0
    assert 'requires scoped evidence capability' in result.stderr
    with sqlite3.connect(case.queue) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


def test_current_core_reconciliation_is_allowed_while_new_effects_are_paused(case):
    dispatch_queue(case)
    with sqlite3.connect(case.queue) as db:
        db.execute("UPDATE actions SET status='unknown'")
    (case.root / 'config/actions.paused').touch()
    value = worker_input(case)
    value['operation'] = value['credentials']['operation'] = 'reconcile'
    result = run_worker(value)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'success'
    with sqlite3.connect(case.queue) as db:
        assert db.execute('SELECT status FROM actions').fetchone()[0] == 'unknown'
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


def test_parent_refuses_revoked_core_reconciliation_before_provider_credentials(authority, monkeypatch):
    from lib import action_credentials, action_process
    dispatch_queue(authority)
    with sqlite3.connect(authority.queue) as db:
        db.execute("UPDATE actions SET status='unknown'")
    consent = authority.ledger.conn.execute('SELECT consent_id FROM consent LIMIT 1').fetchone()[0]
    authority.ledger.revoke_consent(consent)
    monkeypatch.setattr(action_credentials, 'prepare',
                        lambda *args, **kwargs: pytest.fail('revoked query captured provider credentials'))
    result = action_process.execute('actions.instruction_sheet', authority.proposal, 5, operation='reconcile',
        queue_path=authority.queue, runner=lambda *args, **kwargs: pytest.fail('revoked query dispatched a child'))
    assert result.status == 'unknown'
    with sqlite3.connect(authority.queue) as db:
        assert db.execute('SELECT status FROM actions').fetchone()[0] == 'unknown'
