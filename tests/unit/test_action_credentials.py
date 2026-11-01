"""RA-24: trusted action children receive only their exact short-lived provider credential."""
import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from lib import action_credentials as broker
from test_action_process import proposal
from test_credentials import MemoryKeyring


@pytest.fixture
def sources(monkeypatch):
    import google_auth
    import setup_msgraph_oauth as graph
    from types import SimpleNamespace
    seen = []
    def google(name, warn_seconds=0):
        seen.append(('google', name, warn_seconds))
        return SimpleNamespace(token='synthetic-google-access', refresh_token='synthetic-never-pass-grant',
            client_secret='synthetic-never-pass-secret', expiry=datetime.now(UTC) + timedelta(hours=1)), False
    def microsoft(warn_seconds=300, *, work=False):
        seen.append(('graph', work, warn_seconds))
        return {'access_token': 'synthetic-graph-access', 'expiry': (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                'refresh_token': 'synthetic-never-pass-grant', '_artha_client_id': 'synthetic-private-client'}
    backend = MemoryKeyring()
    backend.set_password('artha', 'artha-slack-bot-token', 'synthetic-slack-access')
    backend.set_password('artha', 'artha-todoist-token', 'synthetic-todoist-access')
    monkeypatch.setattr(google_auth, '_refresh_existing', google)
    monkeypatch.setattr(graph, 'ensure_valid_token', microsoft)
    monkeypatch.setattr(broker, 'system_keyring', lambda: backend)
    return seen


def capability(module='actions.reminder_create', *, records=None):
    from action_queue import compute_payload_hash
    now = time.time()
    return {'schema': 'artha.action-credentials.v1', 'module': module, 'payload_hash': compute_payload_hash(proposal()),
        'operation': 'execute', 'since': '', 'nonce': 'a' * 32, 'issued_at': now, 'expires_at': now + 30,
        'records': records or {}, 'api_keys': {}}


@pytest.mark.parametrize('module,record', [
    ('actions.email_send', 'google.gmail-oauth-token'), ('actions.email_reply', 'google.gmail-oauth-token'),
    ('actions.calendar_create', 'google.gcal-oauth-token'), ('actions.calendar_modify', 'google.gcal-oauth-token'),
    ('actions.reminder_create', 'msgraph.personal'), ('actions.todo_sync_action', 'msgraph.personal'),
    ('actions.slack_send', None), ('actions.todoist_sync', None), ('actions.instruction_sheet', None)])
def test_parent_capture_selects_one_provider_and_never_passes_refresh_or_client_secrets(sources, module, record):
    from dataclasses import replace
    from actions import _HANDLER_MAP
    action_type = next(name for name, handler in _HANDLER_MAP.items() if handler == module)
    item = replace(proposal(), action_type=action_type)
    captured = broker.prepare(module, item, 30)
    assert set(captured['records']) == ({record} if record else set())
    assert len(captured['api_keys']) == (1 if module in {'actions.slack_send', 'actions.todoist_sync'} else 0)
    assert 'synthetic-never-pass' not in json.dumps(captured) and 'synthetic-private-client' not in json.dumps(captured)
    assert captured['module'] == module and captured['operation'] == 'execute'
    if record and record.startswith('google.'):
        assert sources == [('google', record.removeprefix('google.'), 335)]
    elif record:
        assert sources == [('graph', False, 335)]
    else:
        assert sources == []


@pytest.mark.parametrize('change', ['module', 'payload_hash', 'operation', 'since', 'expired', 'future', 'nonce',
                                    'long-lived', 'extra-provider', 'refresh-grant', 'api-key', 'extra-field'])
def test_invalid_capabilities_fail_before_scoped_backend_installation(change):
    item = capability()
    if change in {'module', 'payload_hash', 'operation', 'since', 'nonce'}:
        item[change] = 'wrong'
    elif change == 'expired':
        item['expires_at'] = time.time() - 1
    elif change == 'future':
        item['issued_at'] = time.time() + 60
    elif change == 'long-lived':
        item['expires_at'] += 1000
    elif change == 'extra-provider':
        item['records']['msgraph.work'] = {'access_token': 'synthetic-wrong'}
    elif change == 'refresh-grant':
        item['records']['msgraph.personal'] = {'access_token': 'synthetic-token', 'refresh_token': 'synthetic-never-pass'}
    elif change == 'api-key':
        item['api_keys']['artha-slack-bot-token'] = 'synthetic-wrong'
    else:
        item['private'] = 'synthetic-never-pass'
    with pytest.raises(broker.CredentialCapabilityError):
        broker.validate(item, 'actions.reminder_create', proposal(), 'execute', '')


def test_child_normal_and_raw_credential_apis_are_scoped_and_read_only(monkeypatch, tmp_path):
    import keyring
    from catchup_core import crypto
    from lib.credentials import CredentialStore, CredentialError
    item = capability(records={'msgraph.personal': {'access_token': 'synthetic-graph-access',
        'expiry': (datetime.now(UTC) + timedelta(hours=1)).isoformat()}})
    previous = keyring.get_keyring()
    monkeypatch.setattr(crypto, '_action_keyring', None)
    try:
        broker.install(item, 'actions.reminder_create', proposal(), 'execute', '')
        scoped = CredentialStore(local=tmp_path)
        assert scoped.read('msgraph.personal')['access_token'] == 'synthetic-graph-access'
        for name in ('msgraph.work', 'google.gmail-oauth-token', 'google.gcal-oauth-token'):
            assert scoped.read(name) is None
        assert keyring.get_password('artha', 'artha-slack-bot-token') is None
        assert crypto.system_keyring() is keyring.get_keyring()
        for call in (lambda: scoped.write('msgraph.personal', {'access_token': 'synthetic-replacement'}),
                     lambda: scoped.delete('msgraph.personal'),
                     lambda: keyring.set_password('artha', 'arbitrary', 'synthetic'),
                     lambda: keyring.delete_password('artha', 'arbitrary')):
            with pytest.raises((CredentialError, PermissionError)):
                call()
        assert not list(tmp_path.rglob('*'))
    finally:
        keyring.set_keyring(previous)


def test_parent_refuses_a_handler_that_does_not_match_the_approved_type_before_capture(sources):
    with pytest.raises(broker.CredentialCapabilityError):
        broker.prepare('actions.email_send', proposal(), 30)
    assert sources == []


def test_credential_values_cannot_enter_handler_receipts(monkeypatch):
    item = capability(records={'msgraph.personal': {'access_token': 'synthetic-sensitive-access',
        'expiry': (datetime.now(UTC) + timedelta(hours=1)).isoformat()}})
    value = {'message': 'HTTP failed with synthetic-sensitive-access', 'data': ['synthetic-sensitive-access']}
    redacted = broker.redact(value, item)
    assert 'synthetic-sensitive-access' not in json.dumps(redacted)
    assert value['data'][0] == 'synthetic-sensitive-access'


@pytest.mark.parametrize('module', ['actions.email_send', 'actions.calendar_create', 'actions.reminder_create', 'actions.slack_send'])
def test_independent_child_can_read_only_its_brokered_provider_through_real_credential_helpers(module):
    import subprocess
    import sys
    from pathlib import Path
    from dataclasses import asdict, replace
    from actions import _HANDLER_MAP
    from action_queue import compute_payload_hash
    item = replace(proposal(), action_type=next(name for name, handler in _HANDLER_MAP.items() if handler == module))
    scope = capability(module)
    scope['payload_hash'] = compute_payload_hash(item)
    name = broker._RECORDS.get(module)
    if name:
        field = 'token' if name.startswith('google.') else 'access_token'
        scope['records'][name] = {field: 'synthetic-child-access',
            'expiry': (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    else:
        scope['api_keys']['artha-slack-bot-token'] = 'synthetic-child-access'
    code = r'''
import sys,json
sys.path.insert(0,sys.argv[1])
from actions.base import ActionProposal
from lib.action_credentials import install
from lib.action_process import restrict_network
from lib.credentials import CredentialStore,CredentialError
import keyring
value=json.loads(sys.stdin.read())
install(value['scope'],value['scope']['module'],ActionProposal(**value['proposal']),'execute','')
restrict_network(frozenset())
vault=CredentialStore()
names=['google.gmail-oauth-token','google.gcal-oauth-token','msgraph.personal','msgraph.work','msgraph.work-mcp']
present=[name for name in names if vault.read(name) is not None]
assert keyring.get_password('artha-ledger','key.1') is None
assert keyring.get_password('gmail-client-secret','artha') is None
assert keyring.get_password('artha','artha-todoist-token') is None
try:vault.write('msgraph.personal',{'access_token':'synthetic-replacement'})
except CredentialError:pass
else:raise AssertionError('credential write was admitted')
if present and present[0].startswith('google.'):
 import google_auth
 creds=google_auth._get_or_refresh_credentials(present[0].removeprefix('google.'),[])
 assert creds.token=='synthetic-child-access' and creds.refresh_token is None and creds.client_secret is None
elif present:
 import setup_msgraph_oauth
 assert setup_msgraph_oauth.ensure_valid_token()['access_token']=='synthetic-child-access'
else:assert keyring.get_password('artha','artha-slack-bot-token')=='synthetic-child-access'
print(json.dumps({'present':present,'read_only':True}))
'''
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve().parents[2] / 'scripts')],
        input=json.dumps({'scope': scope, 'proposal': asdict(item)}), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'synthetic-child-access' not in result.stdout + result.stderr
    assert json.loads(result.stdout) == {'present': [name] if name else [], 'read_only': True}


@pytest.mark.parametrize('timeout', [0, -1, 121, True, 1.5])
def test_parent_rejects_invalid_action_lifetime_before_capture(sources, timeout):
    with pytest.raises(broker.CredentialCapabilityError):
        broker.prepare('actions.reminder_create', proposal(), timeout)
    assert sources == []


def test_expired_scoped_backend_cannot_be_read(monkeypatch):
    backend = broker._ScopedKeyring(capability())
    monkeypatch.setattr(broker.time, 'time', lambda: backend._expires + 1)
    with pytest.raises(broker.CredentialCapabilityError):
        backend.get_password('artha', 'unrelated')


def test_short_lived_provider_token_is_not_passed_to_a_child(sources, monkeypatch):
    import setup_msgraph_oauth as graph
    monkeypatch.setattr(graph, 'ensure_valid_token', lambda **kwargs: {
        'access_token': 'synthetic-short', 'expiry': (datetime.now(UTC) + timedelta(seconds=30)).isoformat()})
    with pytest.raises(broker.CredentialCapabilityError):
        broker.prepare('actions.reminder_create', proposal(), 30)


@pytest.mark.parametrize('expiry', [None, 'invalid', '2026-01-01T12:00:00'])
def test_provider_expiry_must_be_unambiguous_and_valid(expiry):
    item = capability(records={'msgraph.personal': {'access_token': 'synthetic-token', 'expiry': expiry}})
    with pytest.raises(broker.CredentialCapabilityError):
        broker.validate(item, 'actions.reminder_create', proposal(), 'execute', '')


def test_google_capture_normalizes_sdk_utc_and_refuses_missing_expiry(sources, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    import google_auth
    item = replace(proposal(), action_type='email_send')
    creds = SimpleNamespace(token='synthetic-access', expiry=(datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None))
    monkeypatch.setattr(google_auth, '_refresh_existing', lambda *a, **k: (creds, False))
    assert broker.prepare('actions.email_send', item, 30)['records']['google.gmail-oauth-token']['expiry'].endswith('+00:00')
    creds.expiry = None
    with pytest.raises(broker.CredentialCapabilityError):
        broker.prepare('actions.email_send', item, 30)


def test_unsupported_provider_and_missing_api_keys_never_capture_credentials(sources):
    from dataclasses import replace
    with pytest.raises(broker.CredentialCapabilityError):
        broker.prepare('actions.whatsapp_send', replace(proposal(), action_type='whatsapp_send'), 30)
    item = capability('actions.slack_send')
    item['api_keys']['artha-slack-bot-token'] = None
    from action_queue import compute_payload_hash
    proposal_slack = replace(proposal(), action_type='slack_send')
    item['payload_hash'] = compute_payload_hash(proposal_slack)
    with pytest.raises(broker.CredentialCapabilityError):
        broker.validate(item, 'actions.slack_send', proposal_slack, 'execute', '')


def test_worker_scope_cannot_be_replaced_after_installation(monkeypatch):
    from catchup_core import crypto
    monkeypatch.setattr(crypto, '_action_keyring', object())
    with pytest.raises(broker.CredentialCapabilityError):
        broker.install(capability(), 'actions.reminder_create', proposal(), 'execute', '')
    assert broker.redact({'count': 2, 'nothing': None}, capability()) == {'count': 2, 'nothing': None}
