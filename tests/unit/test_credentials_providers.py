"""Provider credential consumers use the verified OS store, never synced JSON fallback."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from lib.credentials import CredentialStore
from test_credentials import MemoryKeyring


@pytest.fixture
def icloud(tmp_path, monkeypatch):
    import setup_icloud_auth as module
    from lib import credentials
    backend = MemoryKeyring()
    monkeypatch.setattr(credentials, 'system_keyring', lambda: backend)
    monkeypatch.setattr(module, 'CRED_FILE', str(tmp_path / 'icloud-credentials.json'), raising=False)
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: pytest.fail('unexpected credential subprocess'))
    monkeypatch.setattr(module, '_test_imap', lambda *args: (True, 'synthetic IMAP OK'))
    monkeypatch.setattr(module, '_test_caldav', lambda *args: (True, 'synthetic CalDAV OK'))
    return module, backend


def test_icloud_native_roundtrip_matches_consumer_without_secret_files(icloud, tmp_path):
    module, backend = icloud
    from lib.auth import load_icloud_credentials
    module._kc_set(module.KC_APPLE_ID_SERVICE, module.KC_ACCOUNT, 'synthetic@example.invalid')
    module._kc_set(module.KC_APP_PWD_SERVICE, module.KC_ACCOUNT, 'synthetic-app-password')
    assert backend.get_password('icloud-app-password', 'artha') == 'synthetic-app-password'
    assert load_icloud_credentials() == {'provider': 'icloud', 'apple_id': 'synthetic@example.invalid',
                                        'app_password': 'synthetic-app-password'}
    assert not list(tmp_path.rglob('*'))


def test_icloud_missing_security_cannot_succeed_via_plaintext(icloud, monkeypatch, tmp_path):
    module, backend = icloud
    from lib import credentials
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('synthetic unavailable store')
    monkeypatch.setattr('subprocess.run', unavailable)
    monkeypatch.setattr(credentials, 'system_keyring', unavailable)
    with pytest.raises(RuntimeError, match='OS credential'):
        module._kc_set(module.KC_APP_PWD_SERVICE, module.KC_ACCOUNT, 'synthetic-app-password')
    assert not backend.values and not list(tmp_path.rglob('*'))


@pytest.mark.parametrize('mode', ['missing', 'unavailable', 'corrupt'])
def test_icloud_legacy_file_is_never_read_or_changed(icloud, monkeypatch, tmp_path, mode):
    module, backend = icloud
    from pathlib import Path
    legacy = Path(module.CRED_FILE)
    original = json.dumps({'icloud-apple-id': {'artha': 'legacy@example.invalid'},
                           'icloud-app-password': {'artha': 'synthetic-legacy-password'}}).encode()
    legacy.write_bytes(original)
    if mode == 'unavailable':
        def unavailable(*args):
            raise OSError('synthetic-private-store-detail')
        monkeypatch.setattr(backend, 'get_password', unavailable)
    elif mode == 'corrupt':
        backend.values['icloud-apple-id', 'artha'] = {'not': 'a string'}
    with pytest.raises(RuntimeError) as error:
        module.ensure_valid_credentials()
    assert 'synthetic-' not in str(error.value)
    assert legacy.read_bytes() == original
    assert list(tmp_path.iterdir()) == [legacy]


@pytest.mark.parametrize('failure', ['raise', 'discard'])
def test_icloud_unconfirmed_write_fails_without_leaking_password(icloud, monkeypatch, failure):
    module, backend = icloud
    def failed_write(*args):
        if failure == 'raise':
            raise OSError('synthetic-app-password')
    monkeypatch.setattr(backend, 'set_password', failed_write)
    with pytest.raises(RuntimeError, match='OS credential') as error:
        module._kc_set(module.KC_APP_PWD_SERVICE, module.KC_ACCOUNT, 'synthetic-app-password')
    assert 'synthetic-app-password' not in str(error.value)


def test_icloud_explicit_setup_prompts_and_health_remain_private(icloud, monkeypatch, capsys):
    module, backend = icloud
    prompts = []
    monkeypatch.setattr('builtins.input', lambda prompt: prompts.append(prompt) or 'synthetic@example.invalid')
    monkeypatch.setattr(module.getpass, 'getpass',
                        lambda prompt: prompts.append(prompt) or 'synthetic-app-password')
    module.run_setup()
    assert len(prompts) == 2 and 'App-specific password' in prompts[1]
    assert backend.get_password('icloud-app-password', 'artha') == 'synthetic-app-password'
    module.run_health_check()
    output = str(capsys.readouterr())
    assert 'synthetic-app-password' not in output and 'credential file' not in output
    module.run_setup()
    assert len(prompts) == 2
    module.run_setup(force=True)
    assert len(prompts) == 4


def test_icloud_setup_write_failure_exits_without_success_or_provider_call(icloud, monkeypatch, capsys):
    module, backend = icloud
    monkeypatch.setattr('builtins.input', lambda prompt: 'synthetic@example.invalid')
    monkeypatch.setattr(module.getpass, 'getpass', lambda prompt: 'synthetic-app-password')
    def failed_write(*args):
        raise OSError('synthetic-app-password')
    monkeypatch.setattr(backend, 'set_password', failed_write)
    monkeypatch.setattr(module, '_test_imap', lambda *args: pytest.fail('provider called after write failure'))
    with pytest.raises(SystemExit) as error:
        module.run_setup(force=True)
    assert error.value.code == 1
    output = str(capsys.readouterr())
    assert 'synthetic-app-password' not in output and 'Setup complete' not in output


@pytest.mark.parametrize('command', ['setup', 'health'])
def test_icloud_unavailable_store_exits_before_prompt_or_provider(icloud, monkeypatch, capsys, command):
    module, backend = icloud
    def unavailable(*args):
        raise OSError('synthetic-private-store-detail')
    monkeypatch.setattr(backend, 'get_password', unavailable)
    monkeypatch.setattr('builtins.input', lambda *args: pytest.fail('prompted without a usable store'))
    monkeypatch.setattr(module, '_test_imap', lambda *args: pytest.fail('provider called without credentials'))
    with pytest.raises(SystemExit) as error:
        (module.run_setup if command == 'setup' else module.run_health_check)()
    assert error.value.code == 1
    assert 'synthetic-private' not in str(capsys.readouterr())


@pytest.mark.parametrize('apple_id,password', [('', 'synthetic-password'),
    ('invalid', 'synthetic-password'), ('synthetic@example.invalid', '')])
def test_icloud_invalid_setup_input_never_writes(icloud, monkeypatch, apple_id, password):
    module, backend = icloud
    monkeypatch.setattr('builtins.input', lambda prompt: apple_id)
    monkeypatch.setattr(module.getpass, 'getpass', lambda prompt: password)
    with pytest.raises(SystemExit) as error:
        module.run_setup()
    assert error.value.code == 1 and not backend.events


def test_icloud_incomplete_native_pair_requires_explicit_setup_not_legacy_fallback(icloud, monkeypatch):
    module, backend = icloud
    backend.set_password('icloud-apple-id', 'artha', 'synthetic@example.invalid')
    with pytest.raises(RuntimeError, match='setup_icloud_auth'):
        module.ensure_valid_credentials()
    monkeypatch.setattr('builtins.input', lambda prompt: 'synthetic@example.invalid')
    monkeypatch.setattr(module.getpass, 'getpass', lambda prompt: 'synthetic-app-password')
    module.run_setup()
    assert module.ensure_valid_credentials() == ('synthetic@example.invalid', 'synthetic-app-password')
    assert not any(event[0] == 'delete' for event in backend.events)


def test_icloud_readback_error_is_private_and_does_not_fall_back(icloud, monkeypatch, tmp_path):
    module, backend = icloud
    def unavailable(*args):
        raise OSError('synthetic-app-password')
    monkeypatch.setattr(backend, 'get_password', unavailable)
    with pytest.raises(RuntimeError, match='OS credential') as error:
        module._kc_set('icloud-app-password', 'artha', 'synthetic-app-password')
    assert 'synthetic-app-password' not in str(error.value)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('imap_ok,caldav_ok', [(False, True), (True, False)])
def test_icloud_health_failure_remains_nonzero(icloud, monkeypatch, capsys, imap_ok, caldav_ok):
    module, backend = icloud
    backend.set_password('icloud-apple-id', 'artha', 'synthetic@example.invalid')
    backend.set_password('icloud-app-password', 'artha', 'synthetic-app-password')
    monkeypatch.setattr(module, '_test_imap', lambda *args: (imap_ok, 'synthetic IMAP result'))
    monkeypatch.setattr(module, '_test_caldav', lambda *args: (caldav_ok, 'synthetic CalDAV result'))
    with pytest.raises(SystemExit) as error:
        module.run_health_check()
    assert error.value.code == 1 and 'DEGRADED' in capsys.readouterr().err


@pytest.mark.parametrize('outcome', ['ok', 'empty', 'select-failed', 'auth-failed', 'connection-failed'])
def test_icloud_imap_health_uses_fake_provider_and_sanitizes_errors(monkeypatch, outcome):
    import setup_icloud_auth as module
    class FakeMail:
        def login(self, *args):
            if outcome == 'auth-failed':
                raise module.imaplib.IMAP4.error('synthetic-app-password')
            if outcome == 'connection-failed':
                raise OSError('synthetic-app-password')
        def select(self, *args, **kwargs):
            return ('NO' if outcome == 'select-failed' else 'OK',
                    [b'' if outcome == 'empty' else b'3'])
        def logout(self):
            pass
    monkeypatch.setattr(module.imaplib, 'IMAP4_SSL', lambda *args: FakeMail())
    ok, message = module._test_imap('synthetic@example.invalid', 'synthetic-app-password')
    assert ok == (outcome in {'ok', 'empty'}) and 'synthetic-app-password' not in message


@pytest.mark.parametrize('outcome', ['ok', 'error', 'missing-package'])
def test_icloud_caldav_health_uses_fake_provider_and_sanitizes_errors(monkeypatch, outcome):
    import sys
    from types import SimpleNamespace
    import setup_icloud_auth as module
    class FakeClient:
        def principal(self):
            if outcome == 'error':
                raise RuntimeError('synthetic-app-password')
            return self
        def calendars(self):
            return [SimpleNamespace(name='Synthetic calendar')]
    monkeypatch.setitem(sys.modules, 'caldav', None if outcome == 'missing-package' else
                        SimpleNamespace(DAVClient=lambda **kwargs: FakeClient()))
    ok, message = module._test_caldav('synthetic@example.invalid', 'synthetic-app-password')
    assert ok == (outcome == 'ok') and 'synthetic-app-password' not in message


@pytest.mark.parametrize('flags,expected', [([], False), (['--reauth'], True), (['--health'], 'health')])
def test_icloud_cli_keeps_explicit_setup_and_health_routes(monkeypatch, flags, expected):
    import sys
    import setup_icloud_auth as module
    seen = []
    monkeypatch.setattr(sys, 'argv', ['setup_icloud_auth.py', *flags])
    monkeypatch.setattr(module, 'run_setup', lambda *, force: seen.append(force))
    monkeypatch.setattr(module, 'run_health_check', lambda: seen.append('health'))
    module.main()
    assert seen == [expected]


def test_checkpoint_blocks_icloud_write_before_backend_selection(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from catchup_core.session_lock import installation_fence

    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / '.artha-local'))
    script = '''import sys
import setup_icloud_auth as module
calls = []
class Backend:
    def set_password(self, *args):
        calls.append('write')
    def get_password(self, *args):
        calls.append('read')
        return 'synthetic-password'
def backend():
    calls.append('select')
    return Backend()
module.credentials.system_keyring = backend
try:
    module._kc_set('icloud-app-password', 'artha', 'synthetic-password')
except RuntimeError:
    sys.exit(23 if not calls else 1)
sys.exit(1)
'''
    with installation_fence(tmp_path / '.artha-local', exclusive=True):
        child = subprocess.run([sys.executable, '-c', script],
            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')},
            capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr.decode()


def test_icloud_setup_holds_one_capture_fence_across_both_credentials(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from pathlib import Path
    import setup_icloud_auth as module

    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    backend = MemoryKeyring()
    monkeypatch.setattr(module.credentials, 'system_keyring', lambda: backend)
    monkeypatch.setattr('builtins.input', lambda prompt: 'synthetic@example.invalid')
    monkeypatch.setattr(module.getpass, 'getpass', lambda prompt: 'synthetic-password')
    monkeypatch.setattr(module, '_test_imap', lambda *args: (True, 'offline'))
    monkeypatch.setattr(module, '_test_caldav', lambda *args: (True, 'offline'))
    script = '''from pathlib import Path
from catchup_core.session_lock import default_installation_dir, installation_fence, SessionBusy
try:
    with installation_fence(default_installation_dir(), exclusive=True):
        pass
except SessionBusy:
    raise SystemExit(23)
'''
    seen = []
    original = module._kc_set
    def checked(*args):
        for phase in ('before', 'after'):
            if phase == 'after':
                original(*args)
            child = subprocess.run([sys.executable, '-c', script],
                env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')},
                capture_output=True, timeout=15)
            seen.append(child.returncode)
    monkeypatch.setattr(module, '_kc_set', checked)
    module.run_setup(force=True)
    assert seen == [23, 23, 23, 23]
    assert module.ensure_valid_credentials() == ('synthetic@example.invalid', 'synthetic-password')


@pytest.fixture
def providers(tmp_path, monkeypatch):
    import google_auth as google
    import setup_msgraph_oauth as graph
    backend = MemoryKeyring()
    vault = CredentialStore(backend=backend, local=tmp_path / 'local')
    for module in (google, graph):
        monkeypatch.setattr(module, '_credential_store', lambda: vault, raising=False)
        monkeypatch.setattr(module, '_client_backend', lambda: backend)
    monkeypatch.setattr(google, '_TOKEN_DIR', str(tmp_path / 'legacy'))
    monkeypatch.setattr(google, '_STATUS_FILE', str(tmp_path / 'status.json'))
    monkeypatch.setattr(google, '_CLIENT_CREDS_FILE', str(tmp_path / 'google-client-creds.json'))
    monkeypatch.setattr(graph, 'TOKEN_DIR', str(tmp_path / 'legacy'))
    monkeypatch.setattr(graph, 'TOKEN_FILE', str(tmp_path / 'legacy' / 'msgraph-token.json'))
    monkeypatch.setattr(graph, 'WORK_TOKEN_FILE', str(tmp_path / 'legacy' / 'msgraph-work-token.json'))
    monkeypatch.setattr(graph, '_CLIENT_CREDS_FILE', str(tmp_path / 'msgraph-client-creds.json'))
    return google, graph, vault, backend


def test_google_roundtrip_and_revocation_have_no_plaintext_secret_files(providers, tmp_path):
    google, _, vault, _ = providers
    creds = google.Credentials(token='synthetic-access', refresh_token='synthetic-refresh',
        token_uri='https://oauth2.googleapis.com/token', client_id='synthetic-client', client_secret='synthetic-secret',
        scopes=['read'])
    creds.expiry = datetime.now(UTC) + timedelta(hours=1)
    google._save_token(creds, google._KC_GMAIL_TOKEN)
    assert vault.read('google.gmail-oauth-token')['refresh_token'] == 'synthetic-refresh'
    assert google._load_token(google._KC_GMAIL_TOKEN).token == 'synthetic-access'
    assert google.check_stored_credentials()['gmail_token_stored'] is True
    assert google.validate_token_freshness(google._KC_GMAIL_TOKEN, proactive_refresh=False)['ok'] is True
    assert all(b'synthetic-' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())
    google.revoke_tokens()
    assert google._load_token(google._KC_GMAIL_TOKEN) is None


def test_graph_personal_work_roundtrip_is_separate(providers, tmp_path):
    _, graph, vault, _ = providers
    graph._save_token({'access_token': 'synthetic-personal', 'refresh_token': 'synthetic-personal-refresh'},
                       client_id='synthetic-client')
    graph._save_token({'access_token': 'synthetic-work', 'refresh_token': 'synthetic-work-refresh'}, work=True)
    assert vault.read('msgraph.personal')['access_token'] == 'synthetic-personal'
    assert vault.read('msgraph.work')['access_token'] == 'synthetic-work'
    assert graph._load_token()['access_token'] == 'synthetic-personal'
    assert graph._load_token(work=True)['access_token'] == 'synthetic-work'
    assert all(b'synthetic-' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())


def test_client_secrets_are_not_mirrored_and_old_files_are_never_a_fallback(providers, tmp_path):
    google, graph, _, backend = providers
    for module, key in ((google, google._KC_CLIENT_SECRET), (graph, graph.KC_CLIENT_ID)):
        module._keychain_write(key, 'synthetic-client-value')
        assert module._keychain_read(key) == 'synthetic-client-value'
        assert not __import__('pathlib').Path(module._CLIENT_CREDS_FILE).exists()
        backend.delete_password(key, 'artha')
        __import__('pathlib').Path(module._CLIENT_CREDS_FILE).write_text(json.dumps({key: 'stale-synthetic-value'}))
        assert module._keychain_read(key) is None


def test_legacy_oauth_files_do_not_authorize_use_without_explicit_migration(providers, tmp_path):
    google, graph, _, _ = providers
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    (legacy / 'gmail-oauth-token.json').write_text(json.dumps({'token': 'legacy-synthetic-token'}))
    (legacy / 'msgraph-token.json').write_text(json.dumps({'access_token': 'legacy-synthetic-token'}))
    assert google._load_token(google._KC_GMAIL_TOKEN) is None
    assert graph._load_token() is None


def test_sharepoint_uses_only_work_record(providers):
    _, graph, _, _ = providers
    import sharepoint_kb_sync
    graph._save_token({'access_token': 'synthetic-personal'})
    assert sharepoint_kb_sync._load_token() is None
    graph._save_token({'access_token': 'synthetic-work'}, work=True)
    assert sharepoint_kb_sync._load_token() == 'synthetic-work'


def test_sharepoint_does_not_accept_expired_work_record_or_use_personal_fallback(providers):
    _, graph, _, _ = providers
    import sharepoint_kb_sync
    graph._save_token({'access_token': 'synthetic-personal'})
    graph._save_token({'access_token': 'synthetic-expired-work', 'expires_in': -1}, work=True)
    assert sharepoint_kb_sync._load_token() is None


def test_client_revocation_does_not_report_success_when_os_delete_fails(providers, monkeypatch):
    google, _, _, backend = providers
    google._keychain_write(google._KC_CLIENT_SECRET, 'synthetic-client')
    monkeypatch.setattr(backend, 'delete_password', lambda *args: None)
    with pytest.raises(RuntimeError, match='deletion could not be confirmed'):
        google._keychain_delete(google._KC_CLIENT_SECRET)
    assert google._keychain_read(google._KC_CLIENT_SECRET) == 'synthetic-client'


def test_shared_graph_loader_honors_work_scope_without_personal_fallback(providers, monkeypatch):
    _, graph, _, _ = providers
    from lib.auth import load_msgraph_token
    seen = []
    def valid(*, work=False):
        seen.append(work)
        return {'access_token': 'synthetic-work' if work else 'synthetic-personal'}
    monkeypatch.setattr(graph, 'ensure_valid_token', valid)
    assert load_msgraph_token('.tokens/msgraph-work-token.json')['access_token'] == 'synthetic-work'
    assert load_msgraph_token()['access_token'] == 'synthetic-personal'
    assert seen == [True, False]
    with pytest.raises(RuntimeError, match='unsupported'):
        load_msgraph_token('.tokens/unknown-token.json')


@pytest.mark.parametrize('operation', ['write', 'delete'])
def test_capture_blocks_google_client_changes_before_backend_access(tmp_path, monkeypatch, operation):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from catchup_core.session_lock import installation_fence

    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'other-runtime'))
    script = '''import sys
import google_auth as google
calls = []
class Backend:
    def get_password(self, *args):
        calls.append('get')
        return 'synthetic-client'
    def set_password(self, *args):
        calls.append('set')
    def delete_password(self, *args):
        calls.append('delete')
def backend():
    calls.append('select')
    return Backend()
google._client_backend = backend
google._status_set = lambda *args: calls.append('status')
try:
    if sys.argv[1] == 'write':
        google._keychain_write(google._KC_CLIENT_SECRET, 'synthetic-client')
    else:
        google._keychain_delete(google._KC_CLIENT_SECRET)
except RuntimeError:
    sys.exit(23 if not calls else 1)
sys.exit(1)
'''
    with installation_fence(tmp_path / 'other-runtime', exclusive=True):
        child = subprocess.run([sys.executable, '-c', script, operation],
            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')},
            capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr.decode()


@pytest.mark.parametrize('operation', ['write', 'delete'])
def test_google_client_changes_hold_capture_fence_through_verification_and_status(
        providers, tmp_path, monkeypatch, operation):
    import os
    import subprocess
    import sys
    from pathlib import Path

    google, _, _, _ = providers
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'other-runtime'))
    script = '''from pathlib import Path
from catchup_core.session_lock import default_installation_dir, installation_fence, SessionBusy
try:
    with installation_fence(default_installation_dir(), exclusive=True):
        pass
except SessionBusy:
    raise SystemExit(23)
'''
    def capture():
        return subprocess.run([sys.executable, '-c', script],
            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')},
            capture_output=True, timeout=15)
    observed = []
    class CheckedKeyring(MemoryKeyring):
        def set_password(self, *args):
            observed.append(('set', capture().returncode))
            super().set_password(*args)

        def get_password(self, *args):
            observed.append(('get', capture().returncode))
            return super().get_password(*args)

        def delete_password(self, *args):
            observed.append(('delete', capture().returncode))
            super().delete_password(*args)
    backend = CheckedKeyring()
    MemoryKeyring.set_password(backend, google._KC_CLIENT_SECRET, 'artha', 'synthetic-client')
    selected = []
    def select_backend():
        selected.append(True)
        return backend
    monkeypatch.setattr(google, '_client_backend', select_backend)
    monkeypatch.setattr(google, '_status_set', lambda *args: observed.append(('status', capture().returncode)))
    if operation == 'write':
        google._keychain_write(google._KC_CLIENT_SECRET, 'synthetic-new-client')
        expected = ['set', 'get', 'status']
        assert MemoryKeyring.get_password(backend, google._KC_CLIENT_SECRET, 'artha') == 'synthetic-new-client'
    else:
        google._keychain_delete(google._KC_CLIENT_SECRET)
        expected = ['get', 'delete', 'get', 'status']
        assert MemoryKeyring.get_password(backend, google._KC_CLIENT_SECRET, 'artha') is None
    assert observed == [(stage, 23) for stage in expected]
    assert selected == [True]
    assert capture().returncode == 0
    assert (tmp_path / 'other-runtime/installation.lock').exists()  # the directory capture uses, named by the environment


def test_work_loop_never_reuses_personal_tokens_or_unknown_expiry(providers):
    _, graph, vault, _ = providers
    WorkLoop = pytest.importorskip('work_loop', reason='Work overlay is intentionally excluded from public checkouts').WorkLoop
    loop = WorkLoop.__new__(WorkLoop)
    graph._save_token({'access_token': 'synthetic-personal'})
    assert loop._load_graph_auth() == {}
    assert not loop._check_graph_available() and not loop._check_m365_mcp_available()
    graph._save_token({'access_token': 'synthetic-work'}, work=True)
    assert loop._load_graph_auth() == {'access_token': 'synthetic-work'}
    assert loop._check_graph_available() and not loop._check_m365_mcp_available()
    vault.write('msgraph.work-mcp', {'access_token': 'synthetic-mcp', 'expiry': '2099-01-01T00:00:00+00:00'})
    assert loop._check_m365_mcp_available()
    for expiry in (None, 'invalid', '2000-01-01T00:00:00+00:00'):
        vault.write('msgraph.work', {'access_token': 'synthetic-expired', 'expiry': expiry})
        assert loop._load_graph_auth() == {} and not loop._check_graph_available()


def test_shared_graph_loader_uses_the_actual_refresh_helper(providers):
    _, graph, _, _ = providers
    from lib.auth import load_msgraph_token
    graph._save_token({'access_token': 'synthetic-personal'})
    graph._save_token({'access_token': 'synthetic-work'}, work=True)
    assert load_msgraph_token()['access_token'] == 'synthetic-personal'
    assert load_msgraph_token('.tokens/msgraph-work-token.json')['access_token'] == 'synthetic-work'


def test_work_refresh_updates_only_the_work_record(providers, monkeypatch):
    _, graph, vault, _ = providers
    graph._save_token({'access_token': 'synthetic-personal'})
    graph._save_token({'access_token': 'synthetic-old-work', 'refresh_token': 'synthetic-refresh',
                       'expires_in': -10}, client_id='synthetic-work-client', work=True)
    seen = []
    def silent(client_id, data, *, work=False):
        seen.append((client_id, work, data['access_token']))
        return {'access_token': 'synthetic-new-work', 'expires_in': 3600}
    monkeypatch.setattr(graph, '_acquire_token_silent', silent)
    assert graph.ensure_valid_token(work=True)['access_token'] == 'synthetic-new-work'
    assert seen == [('synthetic-work-client', True, 'synthetic-old-work')]
    assert vault.read('msgraph.personal')['access_token'] == 'synthetic-personal'
    assert vault.read('msgraph.work')['access_token'] == 'synthetic-new-work'


def test_unknown_or_expired_graph_freshness_never_returns_a_usable_token_after_failed_refresh(providers, monkeypatch):
    _, graph, vault, _ = providers
    monkeypatch.setattr(graph, '_acquire_token_silent', lambda *args, **kwargs: None)
    for expiry in ('invalid', None, '2000-01-01T00:00:00+00:00'):
        vault.write('msgraph.personal', {'access_token': 'synthetic-expired', 'expiry': expiry,
                    '_artha_client_id': 'synthetic-client'})
        with pytest.raises(RuntimeError, match='freshness'):
            graph.ensure_valid_token()


def test_work_mcp_interactive_helper_selects_the_correct_existing_app_interface(providers, monkeypatch):
    _, graph, _, _ = providers
    seen = []
    class FakeApp:
        def acquire_token_interactive(self, **kwargs):
            seen.append(kwargs)
            return {'access_token': 'synthetic-mcp'}
    def app(client_id, *, work=False):
        assert client_id == 'synthetic-client' and work
        return FakeApp()
    monkeypatch.setattr(graph, '_build_app', app)
    assert graph._acquire_token_interactive('synthetic-client', work=True, mcp=True)['access_token'] == 'synthetic-mcp'
    assert seen[0]['scopes'] == graph._MCP_WORK_SCOPES


def test_google_loaded_expiry_works_with_the_actual_credentials_library(providers):
    google, _, _, _ = providers
    creds = google.Credentials(token='synthetic-access')
    creds.expiry = datetime.now(UTC) + timedelta(hours=1)
    google._save_token(creds, google._KC_GMAIL_TOKEN)
    assert google.get_gmail_credentials().valid


@pytest.mark.parametrize('expiry', [None, '', 'invalid', '2000-01-01T00:00:00+00:00'])
def test_google_unknown_or_expired_credentials_never_open_interactive_login(providers, monkeypatch, expiry):
    google, _, vault, _ = providers
    vault.write('google.gmail-oauth-token', {'token': 'synthetic-access', 'expiry': expiry})
    monkeypatch.setattr(google, '_get_client_config', lambda: pytest.fail('background auth opened interactive setup'))
    with pytest.raises(RuntimeError, match='setup_google_oauth'):
        google.get_gmail_credentials()
    assert not google.validate_token_freshness(google._KC_GMAIL_TOKEN, proactive_refresh=False)['ok']


def test_google_missing_credentials_require_explicit_setup(providers, monkeypatch):
    google, _, _, _ = providers
    monkeypatch.setattr(google, '_get_client_config', lambda: pytest.fail('background auth opened interactive setup'))
    with pytest.raises(RuntimeError, match='setup_google_oauth'):
        google.get_gmail_credentials()


def test_google_failed_refresh_is_private_and_never_opens_login(providers, monkeypatch, capsys):
    google, _, vault, _ = providers
    vault.write('google.gmail-oauth-token', {'token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
        'expiry': '2000-01-01T00:00:00+00:00'})
    def fail(*args):
        raise RuntimeError('synthetic-private-provider-detail')
    monkeypatch.setattr(google.Credentials, 'refresh', fail)
    monkeypatch.setattr(google, '_get_client_config', lambda: pytest.fail('background auth opened interactive setup'))
    with pytest.raises(RuntimeError, match='setup_google_oauth'):
        google.get_gmail_credentials()
    result = google.validate_token_freshness(google._KC_GMAIL_TOKEN)
    assert not result['ok'] and 'synthetic-private' not in result['message']
    assert 'synthetic-private' not in str(capsys.readouterr())


def test_google_imported_refresh_endpoint_cannot_receive_a_refresh_grant(providers, monkeypatch):
    google, _, vault, _ = providers
    vault.write('google.gmail-oauth-token', {'token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
        'token_uri': 'https://example.invalid/token', 'expiry': '2000-01-01T00:00:00+00:00'})
    monkeypatch.setattr(google.Credentials, 'refresh', lambda *args: pytest.fail('unapproved token endpoint'))
    with pytest.raises(RuntimeError, match='setup_google_oauth'):
        google.get_gmail_credentials()


def test_graph_silent_refresh_retains_grant_when_server_does_not_rotate_it(providers, monkeypatch):
    _, graph, vault, _ = providers
    graph._save_token({'access_token': 'synthetic-old', 'refresh_token': 'synthetic-grant', 'expires_in': -1},
        client_id='synthetic-client')
    monkeypatch.setattr(graph, '_acquire_token_silent', lambda *args, **kwargs:
        {'access_token': 'synthetic-new', 'expires_in': 3600})
    assert graph.ensure_valid_token()['refresh_token'] == 'synthetic-grant'
    assert vault.read('msgraph.personal')['refresh_token'] == 'synthetic-grant'


@pytest.mark.parametrize('response', [{ 'expires_in': 3600}, {'access_token': 'synthetic-new', 'expires_in': -1},
    {'access_token': 'synthetic-new', 'expires_in': 'bad'}, {'access_token': 'synthetic-new'}])
def test_invalid_graph_refresh_cannot_replace_a_verified_record(providers, monkeypatch, response):
    _, graph, vault, _ = providers
    graph._save_token({'access_token': 'synthetic-old', 'refresh_token': 'synthetic-grant', 'expires_in': -1},
        client_id='synthetic-client')
    old = vault.read('msgraph.personal')
    monkeypatch.setattr(graph, '_acquire_token_silent', lambda *args, **kwargs: dict(response))
    with pytest.raises(RuntimeError, match='freshness'):
        graph.ensure_valid_token()
    assert vault.read('msgraph.personal') == old


def test_explicit_google_setup_still_runs_and_persists_the_flow(providers, monkeypatch):
    google, _, vault, _ = providers
    creds = google.Credentials(token='synthetic-interactive', refresh_token='synthetic-grant')
    creds.expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
    seen = []
    class Flow:
        def run_local_server(self, **kwargs):
            seen.append(kwargs)
            return creds
    monkeypatch.setattr(google, '_get_client_config', lambda: {'synthetic': 'config'})
    monkeypatch.setattr(google.InstalledAppFlow, 'from_client_config', lambda config, scopes: Flow())
    assert google.get_gmail_credentials(force_reauth=True) is creds
    assert seen == [{'port': 0, 'open_browser': True}]
    assert vault.read('google.gmail-oauth-token')['refresh_token'] == 'synthetic-grant'


def test_google_unknown_expiry_silently_refreshes_and_persists_real_library_credentials(providers, monkeypatch):
    google, _, vault, _ = providers
    vault.write('google.gmail-oauth-token', {'token': 'synthetic-old', 'refresh_token': 'synthetic-grant'})
    seen = []
    def refresh(creds, request):
        seen.append(request)
        creds.token = 'synthetic-new'
        creds.expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
    monkeypatch.setattr(google.Credentials, 'refresh', refresh)
    assert google.get_gmail_credentials().token == 'synthetic-new'
    assert len(seen) == 1
    assert vault.read('google.gmail-oauth-token')['refresh_token'] == 'synthetic-grant'
    assert google.validate_token_freshness(google._KC_GMAIL_TOKEN, proactive_refresh=False)['ok']


@pytest.mark.parametrize('provider', ['google', 'google-preflight', 'graph'])
@pytest.mark.parametrize('change', ['login', 'revoke'])
def test_delayed_refresh_cannot_replace_a_new_login_or_revive_a_revocation(providers, monkeypatch, provider, change):
    google, graph, vault, _ = providers
    name = 'msgraph.personal' if provider == 'graph' else 'google.gmail-oauth-token'
    original = {'access_token': 'synthetic-old', 'refresh_token': 'synthetic-grant', 'expires_in': -1}
    if provider == 'graph':
        graph._save_token(original, client_id='synthetic-client')
    else:
        vault.write(name, {'token': 'synthetic-old', 'refresh_token': 'synthetic-grant',
                          'expiry': '2000-01-01T00:00:00+00:00'})
    def change_during_refresh(*args, **kwargs):
        if change == 'revoke':
            vault.delete(name)
        else:
            vault.write(name, {'token': 'synthetic-new-login', 'access_token': 'synthetic-new-login'})
        if provider == 'graph':
            return {'access_token': 'synthetic-delayed', 'expires_in': 3600}
        args[0].token = 'synthetic-delayed'
        args[0].expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
    if provider == 'graph':
        monkeypatch.setattr(graph, '_acquire_token_silent', change_during_refresh)
        with pytest.raises(RuntimeError):
            graph.ensure_valid_token()
    else:
        monkeypatch.setattr(google.Credentials, 'refresh', change_during_refresh)
        if provider == 'google-preflight':
            assert not google.validate_token_freshness(google._KC_GMAIL_TOKEN)['ok']
        else:
            with pytest.raises(RuntimeError):
                google.get_gmail_credentials()
    current = vault.read(name)
    assert current is None if change == 'revoke' else current['token'] == 'synthetic-new-login'


@pytest.mark.parametrize('provider', ['google', 'graph', 'graph-work'])
@pytest.mark.parametrize('change', ['login', 'revoke'])
def test_actual_refresh_lock_and_revision_survive_an_independent_login_or_revocation(tmp_path, provider, change):
    import subprocess
    import sys
    from pathlib import Path
    from test_credentials import SQLiteKeyring
    from lib.credentials import CredentialError
    backend = SQLiteKeyring(tmp_path / 'fake-keyring.db')
    local = tmp_path / 'local'
    vault = CredentialStore(backend=backend, local=local)
    name = ('msgraph.work' if provider == 'graph-work' else 'msgraph.personal') if provider != 'google' else 'google.gmail-oauth-token'
    vault.write(name, {'token': 'synthetic-old', 'access_token': 'synthetic-old', 'refresh_token': 'synthetic-grant',
                      'expiry': '2000-01-01T00:00:00+00:00', '_artha_client_id': 'synthetic-client'})
    code = r'''
import sys
from pathlib import Path
from datetime import datetime,UTC,timedelta
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from test_credentials import SQLiteKeyring
from lib.credentials import CredentialStore
vault=CredentialStore(backend=SQLiteKeyring(sys.argv[3]),local=Path(sys.argv[4]))
def refresh(*args,**kwargs):
 print('refresh-started',flush=True)
 sys.stdin.readline()
 if sys.argv[5]!='google':return {'access_token':'synthetic-delayed','expires_in':3600}
 args[0].token='synthetic-delayed'
 args[0].expiry=(datetime.now(UTC)+timedelta(hours=1)).replace(tzinfo=None)
if sys.argv[5]=='google':
 import google_auth as provider
 provider._credential_store=lambda:vault
 provider.Credentials.refresh=refresh
 call=provider.get_gmail_credentials
else:
 import setup_msgraph_oauth as provider
 provider._credential_store=lambda:vault
 provider._keychain_read=lambda *args:None
 provider._acquire_token_silent=refresh
 call=lambda:provider.ensure_valid_token(work=sys.argv[5]=='graph-work')
try:call()
except RuntimeError:print('stale-result-refused',flush=True)
else:raise AssertionError('stale refresh returned a credential')
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(Path(__file__).parent),
        str(Path(__file__).resolve().parents[2] / 'scripts'), str(backend.path), str(local), provider],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'refresh-started'
        with pytest.raises(CredentialError, match='refresh'):
            with vault.refresh_lock(name):
                pytest.fail('a second process spent the same refresh grant')
        if change == 'revoke':
            vault.delete(name)
        else:
            vault.write(name, {'token': 'synthetic-new-login'})
        output, error = process.communicate('\n', timeout=15)
        assert process.returncode == 0 and output.strip() == 'stale-result-refused', error
        expected = None if change == 'revoke' else {'token': 'synthetic-new-login'}
        assert vault.read(name) == expected
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_google_silent_refresh_has_a_bounded_http_request_timeout(providers, monkeypatch):
    google, _, vault, _ = providers
    vault.write('google.gmail-oauth-token', {'token': 'synthetic-old', 'refresh_token': 'synthetic-grant'})
    calls = []
    monkeypatch.setattr(google, 'Request', lambda: lambda **kwargs: calls.append(kwargs))
    def refresh(creds, request):
        request(url='https://oauth2.googleapis.com/token', method='POST')
        creds.token = 'synthetic-new'
        creds.expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
    monkeypatch.setattr(google.Credentials, 'refresh', refresh)
    assert google.get_gmail_credentials().token == 'synthetic-new'
    assert calls == [{'url': 'https://oauth2.googleapis.com/token', 'method': 'POST', 'timeout': 10}]


def test_graph_app_has_a_bounded_http_request_timeout(providers, monkeypatch):
    _, graph, _, _ = providers
    import msal
    calls = []
    monkeypatch.setattr(msal, 'PublicClientApplication', lambda *args, **kwargs: calls.append(kwargs))
    graph._build_app('synthetic-client')
    assert calls[0]['timeout'] == 10
