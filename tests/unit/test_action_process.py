"""No real provider traffic: exercise the action process and egress boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from actions.base import ActionProposal
from lib import action_process


@pytest.fixture(autouse=True)
def offline_capability(monkeypatch):
    """Exercise the real worker protocol with an empty scope; never consult the host store or contact a provider."""
    from lib import action_credentials
    from action_queue import compute_payload_hash
    import time
    def empty(module, item, timeout, *, operation='execute', since=''):
        now = time.time()
        return {'schema': 'artha.action-credentials.v1', 'module': module, 'payload_hash': compute_payload_hash(item),
            'operation': operation, 'since': since, 'nonce': 'b' * 32, 'issued_at': now, 'expires_at': now + timeout + 2,
            'records': {}, 'api_keys': {}}
    monkeypatch.setattr(action_credentials, 'prepare', empty)


def proposal():
    return ActionProposal(
        "synthetic",
        "reminder_create",
        "home",
        "Inspect filter",
        "",
        {"title": "Inspect filter"},
        "high",
        1,
        "standard",
        False,
        None,
        None,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="native action lifetime API contract")
@pytest.mark.parametrize("outcome", [
    "deadline", "expired", "parent_exit", "wait_error", "start_error", "open_error",
])
def test_native_lifetime_closes_parent_handle_on_every_exit(monkeypatch, outcome):
    import ctypes

    calls = []
    targets = []

    class NativeCall:
        def __init__(self, name):
            self.name = name

        def __call__(self, *args):
            calls.append((self.name, args))
            if self.name == "open":
                return 0 if outcome == "open_error" else 12345
            if self.name == "wait":
                return {"parent_exit": 0, "wait_error": 0xFFFFFFFF}.get(outcome, 258)
            return True

    kernel = SimpleNamespace(
        OpenProcess=NativeCall("open"),
        WaitForSingleObject=NativeCall("wait"),
        CloseHandle=NativeCall("close"),
    )

    class Thread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            targets.append(target)

        def start(self):
            if outcome == "start_error":
                raise RuntimeError("synthetic thread start failure")

    class LifetimeExit(BaseException):
        pass

    def exit_process(code):
        assert code == 124
        raise LifetimeExit

    ticks = iter([2] if outcome == "expired" else [0, 2])
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    monkeypatch.setattr(action_process.threading, "Thread", Thread)
    monkeypatch.setattr(action_process.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(action_process.time, "sleep", lambda seconds: calls.append(("sleep", (seconds,))))
    monkeypatch.setattr(action_process.os, "_exit", exit_process)
    if outcome == "open_error":
        with pytest.raises(OSError):
            action_process._start_lifetime(9876, 1)
        assert calls == [("open", (0x00100000, False, 9876))]
        assert targets == []
        return
    if outcome == "start_error":
        with pytest.raises(RuntimeError, match="thread start"):
            action_process._start_lifetime(9876, 1)
    else:
        action_process._start_lifetime(9876, 1)
        assert len(targets) == 1
        with pytest.raises(LifetimeExit):
            targets[0]()
        assert (("wait", (12345, 0)) in calls) is (outcome != "expired")
        assert (("sleep", (0.1,)) in calls) is (outcome == "deadline")
    assert calls[0] == ("open", (0x00100000, False, 9876))
    assert calls[-1] == ("close", (12345,))
    assert sum(name == "close" for name, _ in calls) == 1


def test_reconciliation_refuses_missing_evidence_policy_binding():
    import sqlite3
    from dataclasses import replace

    item = replace(proposal(), source_skill="catchup_core")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE action_dispatch_policies(action_id TEXT,policy_root TEXT)")
        with pytest.raises(ValueError, match="no evidence policy binding"):
            action_process._authorize_reconciliation_evidence(db, item)


@pytest.fixture
def dispatch(tmp_path):
    import sqlite3
    from action_queue import compute_payload_hash
    path = tmp_path / "synthetic-actions.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE actions(id TEXT PRIMARY KEY,status TEXT,payload_hash TEXT,approved_payload_hash TEXT)")
        db.execute('CREATE TABLE queue_authority(singleton_key INTEGER PRIMARY KEY,generation INTEGER,host TEXT,'
                   'promoted_at TEXT,former_host_revoked_at TEXT,transfer_pending INTEGER,transfer_json TEXT)')
        db.execute("INSERT INTO queue_authority VALUES(1,1,NULL,'2026-10-24T00:00:00Z',NULL,0,NULL)")
        db.execute("CREATE TABLE effect_operations(operation_key TEXT PRIMARY KEY,action_id TEXT,payload_hash TEXT)")
        db.execute("CREATE TABLE action_dispatch_receipts(operation_key TEXT NOT NULL,action_id TEXT PRIMARY KEY)")
        digest = compute_payload_hash(proposal())
        db.execute("INSERT INTO actions VALUES('synthetic','executing',?,?)", (digest, digest))
        db.execute("INSERT INTO effect_operations VALUES('operation','synthetic',?)", (digest,))
    policy_root = tmp_path / 'policy'
    (policy_root / 'config').mkdir(parents=True)
    policy_file = policy_root / 'config/actions.yaml'
    policy_file.write_text('live_trial: {held: []}\nactions:\n  reminder_create: {enabled: true}\n')
    import hashlib
    from datetime import datetime, timezone
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('ALTER TABLE actions ADD COLUMN approved_by TEXT')
        db.execute('ALTER TABLE actions ADD COLUMN expires_at TEXT')
        db.execute('ALTER TABLE actions ADD COLUMN preview_shown_at TEXT')
        db.execute('ALTER TABLE actions ADD COLUMN preview_payload_hash TEXT')
        db.execute('UPDATE actions SET preview_payload_hash=payload_hash')
        db.execute("UPDATE actions SET approved_by='owner',preview_shown_at=?", (datetime.now(timezone.utc).isoformat(),))
        db.execute('CREATE TABLE action_dispatch_policies(action_id TEXT PRIMARY KEY,policy_root TEXT,policy_sha256 TEXT)')
        db.execute('INSERT INTO action_dispatch_policies VALUES(?,?,?)',
                   ('synthetic', str(policy_root), hashlib.sha256(policy_file.read_bytes()).hexdigest()))
    return path


def test_direct_process_call_cannot_dispatch_without_an_approved_queue_claim(monkeypatch):
    def forbidden(*_args, **_kw):
        pytest.fail("unapproved call started a provider-capable process")
    from lib import action_credentials
    monkeypatch.setattr(action_credentials, 'prepare', forbidden)
    result = action_process.execute("actions.reminder_create", proposal(), 5, runner=forbidden)
    assert result.status == "failure" and "claim" in result.message


def test_process_has_minimal_environment_and_reads_exact_receipt(monkeypatch, dispatch):
    monkeypatch.setenv("SYNTHETIC_SECRET", "must not enter the child")

    def runner(argv, **kwargs):
        assert "SYNTHETIC_SECRET" not in kwargs["env"]
        assert json.loads(kwargs["input"])["proposal"] == asdict(proposal())
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "success",
                    "message": "verified",
                    "data": {"id": "r1"},
                    "reversible": False,
                    "reverse_action": None,
                }
            ),
        )

    assert action_process.execute("actions.reminder_create", proposal(), 5, queue_path=dispatch, runner=runner).status == "success"
    assert action_process.execute("unknown", proposal(), 5, queue_path=dispatch).status == "failure"


@pytest.mark.parametrize("kind", ["timeout", "crash", "malformed", "wrong_shape", "invalid_status"])
def test_uncertain_child_never_claims_failure_or_authorizes_retry(kind, dispatch):
    def runner(*args, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired("child", 1)
        output = '[]' if kind == 'wrong_shape' else json.dumps({'status':'unrecognized'}) if kind == 'invalid_status' else 'not JSON'
        return SimpleNamespace(returncode=1 if kind == "crash" else 0, stdout=output)

    assert action_process.execute("actions.reminder_create", proposal(), 1, queue_path=dispatch, runner=runner).status == "unknown"


def test_egress_policy_denies_unknown_hosts_before_dns(monkeypatch):
    # Keep the audit callback local: installing a process-global hook in pytest is irreversible.
    callbacks = []
    monkeypatch.setattr(action_process.sys, "addaudithook", callbacks.append)
    monkeypatch.setattr(action_process.socket, "getaddrinfo", action_process.socket.getaddrinfo)
    action_process.restrict_network(frozenset({"provider.example"}))
    with pytest.raises(PermissionError):
        action_process.socket.getaddrinfo("unlisted.example", 443)
    with pytest.raises(PermissionError):
        callbacks[0]("subprocess.Popen", ("shell",))
    with pytest.raises(PermissionError):
        callbacks[0]("socket.connect", (None, ("127.0.0.1", 443)))


@pytest.fixture
def no_real_credentials():
    """These tests start a real child that would call the real provider if a real token were reachable."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(action_process.__file__)))
    root = os.path.dirname(root)
    if any(name.startswith("msgraph-token") for name in os.listdir(os.path.join(root, ".tokens"))
           ) if os.path.isdir(os.path.join(root, ".tokens")) else False:
        pytest.skip("a real Microsoft Graph token is present here; run the suite in the isolated workspace (make test)")


def _child_diagnostics(operation="execute"):
    """Re-run the same child by hand so a CI-only failure shows *why* (stderr of a no-credential child is safe)."""
    import os
    import sys
    envelope = json.dumps({"module": "actions.reminder_create", "proposal": asdict(proposal()), "parent": os.getpid(),
                           "timeout": 20, "operation": operation, "since": "x"})
    env = {k: v for k, v in os.environ.items() if k in action_process._CHILD_ENV}
    env["ARTHA_NO_REEXEC"] = "1"
    done = subprocess.run([sys.executable, action_process.__file__], input=envelope, capture_output=True, text=True,
                          env=env, timeout=60, check=False)
    return f"rc={done.returncode} stdout={done.stdout[-300:]!r} stderr={done.stderr[-900:]!r}"


def test_real_child_returns_a_verified_failure_offline(no_real_credentials, dispatch):
    """End to end through a real child process: no token means a clean 'failure', never a hang or 'unknown'."""
    children = []

    def runner(*args, **kwargs):
        child = subprocess.run(*args, **kwargs)
        children.append(child)
        return child

    result = action_process.execute("actions.reminder_create", proposal(), 20, queue_path=dispatch, runner=runner)
    assert result.status == "failure" and "To Do" in result.message, (
        result.message, [(child.returncode, child.stdout, child.stderr) for child in children])


def test_real_child_cannot_open_sockets_spawn_processes_or_listen():
    """The audit hook only works per process, so prove it in a throwaway interpreter."""
    import os
    import sys
    scripts = os.path.join(os.path.dirname(action_process.__file__), "..")
    code = (
        "import sys, socket, subprocess\n"
        f"sys.path.insert(0, {scripts!r})\n"
        "from lib import action_process\n"
        "action_process.restrict_network(frozenset({'provider.example'}))\n"
        "out = []\n"
        "for name, call in (('dns', lambda: socket.getaddrinfo('evil.example', 443)),\n"
        "                   ('ip', lambda: socket.create_connection(('127.0.0.1', 443), 1)),\n"
        "                   ('spawn', lambda: subprocess.run(['true'])),\n"
        "                   ('listen', lambda: socket.socket().bind(('127.0.0.1', 0)))):\n"
        "    try:\n"
        "        call(); out.append(name + ':allowed')\n"
        "    except PermissionError:\n"
        "        out.append(name + ':denied')\n"
        "    except OSError as exc:\n"
        "        out.append(name + ':os-' + type(exc).__name__)\n"
        "print(','.join(out))\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                          env={k: v for k, v in os.environ.items() if k in action_process._CHILD_ENV}, check=False)
    assert done.stdout.strip() == "dns:denied,ip:denied,spawn:denied,listen:denied", done.stdout + done.stderr


def test_expired_child_exit_code_is_reported_as_unknown(dispatch):
    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=124, stdout="")
    assert action_process.execute("actions.reminder_create", proposal(), 1, queue_path=dispatch, runner=runner).status == "unknown"


def test_reconcile_runs_in_the_same_bounded_child_and_passes_the_approval_time(dispatch):
    import sqlite3
    with closing(sqlite3.connect(dispatch)) as db, db:
        db.execute("UPDATE actions SET status='unknown'")
    seen = {}

    def runner(argv, **kwargs):
        seen.update(json.loads(kwargs["input"]))
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"status": "unknown", "message": "no match", "data": None, "reversible": False, "reverse_action": None}))

    result = action_process.execute("actions.reminder_create", proposal(), 5, queue_path=dispatch, runner=runner,
                                    operation="reconcile", since="2026-10-20T10:00:00Z")
    assert result.status == "unknown" and seen["operation"] == "reconcile" and seen["since"] == "2026-10-20T10:00:00Z"
    assert set(seen["env"]) <= set(action_process._CHILD_ENV) | {"ARTHA_NO_REEXEC"}
    assert not any(k.endswith(("TOKEN", "SECRET", "KEY", "PASSWORD")) for k in seen["env"])
    with pytest.raises(ValueError):
        action_process.execute("actions.reminder_create", proposal(), 5, queue_path=dispatch, operation="delete")


def test_real_child_reconcile_without_a_token_stays_unknown(no_real_credentials, dispatch):
    import sqlite3
    with closing(sqlite3.connect(dispatch)) as db, db:
        db.execute("UPDATE actions SET status='unknown'")
    result = action_process.execute("actions.reminder_create", proposal(), 20, queue_path=dispatch, operation="reconcile", since="x")
    assert result.status == "unknown" and "Could not check the provider" in result.message, (
        result.message, _child_diagnostics("reconcile"))


def test_a_child_without_a_receipt_names_its_error_class_but_never_its_message(dispatch):
    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr=(
            "Traceback (most recent call last):\n  File \"x\", line 1\nPermissionError: /Users/secret/token.json"))
    result = action_process.execute("actions.reminder_create", proposal(), 1, queue_path=dispatch, runner=runner)
    assert result.status == "unknown" and "(PermissionError)" in result.message and "/Users" not in result.message
    silent = action_process.execute("actions.reminder_create", proposal(), 1, queue_path=dispatch,
                                    runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert "(no error class)" in silent.message
    assert action_process._child_failure_class("SystemExit: 1") == "SystemExit"


def test_the_child_environment_keeps_interpreter_loading_variables_and_nothing_secret(monkeypatch, dispatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/python/lib")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-pass")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "must-not-pass")
    seen = {}

    def runner(argv, **kwargs):
        seen.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"status": "success", "message": "ok", "data": None, "reversible": False, "reverse_action": None}))
    action_process.execute("actions.reminder_create", proposal(), 1, queue_path=dispatch, runner=runner)
    assert seen.get("LD_LIBRARY_PATH") == "/opt/python/lib" and "GITHUB_TOKEN" not in seen
    assert "MSGRAPH_CLIENT_SECRET" not in seen


def test_child_environment_stops_the_venv_relaunch_guard_from_killing_a_correct_interpreter(dispatch):
    seen = {}

    def runner(argv, **kwargs):
        seen.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"status": "success", "message": "ok", "data": None, "reversible": False, "reverse_action": None}))
    action_process.execute("actions.reminder_create", proposal(), 1, queue_path=dispatch, runner=runner)
    assert seen["ARTHA_NO_REEXEC"] == "1"


def test_worker_consumes_each_approved_operation_once(dispatch):
    import sqlite3
    action_process._authorize(dispatch, proposal(), "execute", consume=True)
    with pytest.raises(sqlite3.IntegrityError):
        action_process._authorize(dispatch, proposal(), "execute", consume=True)


def test_worker_claim_and_receipt_serialize_approval_withdrawal(dispatch, monkeypatch):
    import sqlite3
    import sys
    original = action_process._authorize_current_policy
    blocked = []

    def withdraw_after_check(db, item, **kwargs):
        original(db, item, **kwargs)
        script = '''
import sqlite3,sys
with sqlite3.connect(sys.argv[1],timeout=0) as competing:
    try:
        competing.execute("UPDATE actions SET status='cancelled'")
    except sqlite3.OperationalError as exc:
        if 'locked' not in str(exc): raise
        print('blocked')
    else:
        print('withdrawn')
'''
        result = subprocess.run([sys.executable, '-c', script, str(dispatch)],
                                capture_output=True, text=True, timeout=5, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() in {'blocked', 'withdrawn'}
        if result.stdout.strip() == 'blocked':
            blocked.append(True)

    monkeypatch.setattr(action_process, '_authorize_current_policy', withdraw_after_check)
    refused = False
    try:
        action_process._authorize(dispatch, proposal(), 'execute', consume=True)
    except ValueError:
        refused = True
    assert blocked or refused, 'withdrawn approval was consumed after its authorization check'
    with closing(sqlite3.connect(dispatch)) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == (0 if refused else 1)


def test_consumed_dispatch_never_becomes_a_pre_dispatch_failure(dispatch, monkeypatch):
    from lib import action_credentials
    action_process._authorize(dispatch, proposal(), 'execute', consume=True)

    def unavailable(*args, **kwargs):
        raise action_credentials.CredentialCapabilityError('synthetic unavailable credentials')

    monkeypatch.setattr(action_credentials, 'prepare', unavailable)
    result = action_process.execute('actions.reminder_create', proposal(), 1, queue_path=dispatch)
    assert result.status == 'unknown'
    assert 'reconcile' in result.message.lower()


@pytest.mark.parametrize('boundary', ['evidence', 'credentials'])
def test_competing_dispatch_during_preparation_preserves_unknown(dispatch, monkeypatch, boundary):
    from lib import action_credentials

    def competing_dispatch(*args, **kwargs):
        action_process._authorize(dispatch, proposal(), 'execute', consume=True)
        if boundary == 'credentials':
            raise action_credentials.CredentialCapabilityError('synthetic unavailable credentials')
        raise ValueError('synthetic changed evidence')

    if boundary == 'evidence':
        monkeypatch.setattr(action_process, '_prepare_evidence', competing_dispatch)
    else:
        monkeypatch.setattr(action_credentials, 'prepare', competing_dispatch)
    result = action_process.execute('actions.reminder_create', proposal(), 1, queue_path=dispatch)
    assert result.status == 'unknown'
    assert 'reconcile' in result.message.lower()


def test_manual_worker_envelope_is_rejected_before_provider_loading():
    diagnostics = _child_diagnostics()
    assert "missing dispatch authority" in diagnostics
    assert "To Do" not in diagnostics


def test_new_approved_attempt_after_verified_failure_can_dispatch_once(tmp_path, monkeypatch):
    from dataclasses import replace
    from action_queue import ActionQueue, compute_payload_hash
    from actions.base import ActionResult
    monkeypatch.setattr(ActionQueue, "_resolve_db_path", staticmethod(lambda _: tmp_path / "actions.db"))
    queue = ActionQueue(tmp_path)
    (tmp_path / 'config').mkdir(exist_ok=True)
    (tmp_path / 'config/actions.yaml').write_text('live_trial: {held: []}\nactions:\n  reminder_create: {enabled: true}\n')
    from lib.action_policy import load_dispatch_policy
    policy = load_dispatch_policy(tmp_path, 'reminder_create')
    try:
        for identity in ("first", "retry"):
            item = replace(proposal(), id=identity)
            queue.propose(item)
            queue.mark_preview_shown(identity, shown_by="owner", payload_hash=compute_payload_hash(item))
            queue.transition(identity, "approved", actor="owner", approved_by="owner")
            queue.transition(identity, "executing", actor="executor",
                             expected_payload_hash=compute_payload_hash(item), operation_key="same-operation",
                             dispatch_policy=(policy.root, policy.sha256))
            action_process._authorize(queue._db_path, item, "execute", consume=True)
            queue.record_result(identity, ActionResult("failure", "verified no effect", None, False, None),
                                "2026-10-21T00:00:00Z")
        assert queue._conn.execute("SELECT count(*) FROM action_dispatch_receipts").fetchone()[0] == 2
    finally:
        queue.close()


def test_parent_refuses_mismatched_handler_before_credential_capture(monkeypatch, dispatch):
    from lib import action_credentials
    monkeypatch.setattr(action_credentials, 'prepare', lambda *a, **k: pytest.fail('wrong provider credential captured'))
    assert action_process.execute('actions.email_send', proposal(), 5, queue_path=dispatch).status == 'failure'


@pytest.mark.parametrize('operation', ['execute', 'reconcile'])
def test_unavailable_credentials_do_not_start_a_child_or_convert_existing_uncertainty_to_failure(monkeypatch, dispatch, operation):
    import sqlite3
    from lib import action_credentials
    if operation == 'reconcile':
        with closing(sqlite3.connect(dispatch)) as db, db:
            db.execute("UPDATE actions SET status='unknown'")
    def unavailable(*a, **k):
        raise action_credentials.CredentialCapabilityError('synthetic-private-error')
    monkeypatch.setattr(action_credentials, 'prepare', unavailable)
    result = action_process.execute('actions.reminder_create', proposal(), 5, queue_path=dispatch, operation=operation,
        runner=lambda *a, **k: pytest.fail('missing credential started a child'))
    assert result.status == ('unknown' if operation == 'reconcile' else 'failure')
    assert 'synthetic-private' not in result.message


@pytest.mark.parametrize('operation,error', [('execute', False), ('reconcile', False), ('execute', True)])
def test_worker_installs_scope_before_handler_and_never_returns_secret_diagnostics(monkeypatch, dispatch, capsys, operation, error):
    import io
    import os
    import sqlite3
    import sys
    import time
    import keyring
    from datetime import UTC, datetime, timedelta
    from catchup_core import crypto
    from actions.base import ActionResult
    from lib.action_credentials import prepare
    if operation == 'reconcile':
        with closing(sqlite3.connect(dispatch)) as db, db:
            db.execute("UPDATE actions SET status='unknown'")
    scoped = prepare('actions.reminder_create', proposal(), 5, operation=operation, since='synthetic-time')
    scoped['records'] = {'msgraph.personal': {'access_token': 'synthetic-probe-secret',
        'expiry': (datetime.now(UTC) + timedelta(hours=1)).isoformat()}}
    envelope = {'module': 'actions.reminder_create', 'proposal': asdict(proposal()), 'parent': os.getpid(),
        'timeout': 5, 'operation': operation, 'since': 'synthetic-time', 'queue_path': str(dispatch), 'credentials': scoped}
    monkeypatch.setattr(action_process.sys, 'stdin', io.StringIO(json.dumps(envelope)))
    monkeypatch.setattr(action_process, '_start_lifetime', lambda *args: None)
    monkeypatch.setattr(action_process, 'restrict_network', lambda hosts: None)
    from keyring.backends.null import Keyring
    previous = Keyring()
    monkeypatch.setattr(keyring.core, '_keyring_backend', previous)
    monkeypatch.setattr(crypto, '_action_keyring', None)
    original = action_process.importlib.import_module
    def invoke(item, **kwargs):
        from lib.credentials import CredentialStore
        assert CredentialStore().read('msgraph.personal')['access_token'] == 'synthetic-probe-secret'
        assert CredentialStore().read('msgraph.work') is None
        print('synthetic-probe-secret')
        print('synthetic-probe-secret', file=sys.stderr)
        if error:
            raise RuntimeError('synthetic-probe-secret')
        return ActionResult('success', 'Result: synthetic-probe-secret', {'checked': True}, False, None)
    def handler(name, *args, **kwargs):
        if name == 'actions.reminder_create':
            assert crypto._action_keyring is not None
            return SimpleNamespace(execute=invoke, reconcile=invoke)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(action_process.importlib, 'import_module', handler)
    try:
        action_process._worker()
        output = capsys.readouterr()
        assert 'synthetic-probe-secret' not in output.out + output.err
        result = json.loads(output.out)
        assert result['status'] == ('unknown' if error else 'success')
    finally:
        keyring.set_keyring(previous)


@pytest.mark.parametrize('module', ['unregistered', 'actions.email_send', 'actions.reminder_create'])
def test_worker_rejects_invalid_identity_or_capability_before_consuming_claim(monkeypatch, dispatch, module):
    import io
    import sqlite3
    envelope = {'module': module, 'proposal': asdict(proposal()), 'queue_path': str(dispatch), 'credentials': {}}
    monkeypatch.setattr(action_process.sys, 'stdin', io.StringIO(json.dumps(envelope)))
    with pytest.raises((ValueError, RuntimeError)):
        action_process._worker()
    with closing(sqlite3.connect(dispatch)) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


def test_parent_decodes_a_valid_reverse_proposal(dispatch):
    reverse = asdict(proposal())
    result = action_process.execute('actions.reminder_create', proposal(), 5, queue_path=dispatch,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({'status': 'success', 'message': 'verified',
            'data': {}, 'reversible': True, 'reverse_action': reverse})))
    assert result.reverse_action == proposal()


def test_egress_accepts_only_dns_verified_provider_addresses(monkeypatch):
    import socket
    callbacks = []
    monkeypatch.setattr(action_process.sys, 'addaudithook', callbacks.append)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443))])
    action_process.restrict_network(frozenset({'provider.example'}))
    assert socket.getaddrinfo('provider.example', 443)[0][4] == ('192.0.2.1', 443)
    callbacks[0]('socket.connect', (None, ('192.0.2.1', 443)))
    callbacks[0]('synthetic.non-network-event', ())
    with pytest.raises(PermissionError):
        socket.getaddrinfo('provider.example', 80)
    with pytest.raises(PermissionError):
        callbacks[0]('socket.connect', (None, ('192.0.2.1', 80)))


def test_exact_payload_and_registered_type_are_required_for_queue_authority(dispatch, monkeypatch):
    from dataclasses import replace
    from actions import _HANDLER_MAP
    with pytest.raises(ValueError, match='exact payload'):
        action_process._authorize(dispatch, replace(proposal(), title='changed'), 'execute', consume=False)
    monkeypatch.delitem(_HANDLER_MAP, 'reminder_create')
    with pytest.raises(ValueError, match='unregistered'):
        action_process._authorize(dispatch, proposal(), 'execute', consume=False)


@pytest.mark.parametrize('cause', ['deadline', 'parent-loss'])
def test_real_worker_death_keeps_the_operation_uncertain_and_consumed_once(dispatch, cause):
    import os
    import sqlite3
    import sys
    from pathlib import Path
    code = r'''
import sys,time
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from lib import action_process
original=action_process.importlib.import_module
def wait_forever(*args,**kwargs):
 if sys.argv[2]=='parent-loss' and sys.platform!='win32':action_process.os.getppid=lambda:-1
 while True:time.sleep(.02)
def module(name,*args,**kwargs):
 if name=='actions.reminder_create':return SimpleNamespace(execute=wait_forever)
 return original(name,*args,**kwargs)
action_process.importlib.import_module=module
action_process._worker()
'''
    children = []

    def runner(argv, **kwargs):
        parent = None
        if cause == 'parent-loss' and os.name == 'nt':
            # A real disposable process stays alive until the worker consumes its claim, then exits.
            parent_code = '''
import sqlite3,sys,time
from contextlib import closing
while True:
 with closing(sqlite3.connect(sys.argv[1])) as db:
  if db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0]:break
 time.sleep(.02)
'''
            parent = subprocess.Popen([sys.executable, '-c', parent_code, str(dispatch)])
            # The venv redirector itself remains alive until its Python process exits.
            envelope = json.loads(kwargs['input'])
            envelope['parent'] = parent.pid
            envelope['timeout'] = 20
            envelope['credentials']['expires_at'] = envelope['credentials']['issued_at'] + 22
            kwargs['input'] = json.dumps(envelope)
            kwargs['timeout'] = 10
        try:
            child = subprocess.run([sys.executable, '-c', code,
                str(Path(action_process.__file__).resolve().parents[1]), cause], **kwargs)
            children.append(child)
            return child
        finally:
            if parent is not None:
                if parent.poll() is None:
                    parent.kill()
                parent.wait(timeout=5)
    result = action_process.execute('actions.reminder_create', proposal(), 1, queue_path=dispatch, runner=runner)
    assert result.status == 'unknown' and 'reconcile' in result.message
    assert len(children) == 1 and children[0].returncode == 124
    with closing(sqlite3.connect(dispatch)) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 1
    second = action_process.execute('actions.reminder_create', proposal(), 1, queue_path=dispatch, runner=runner)
    assert second.status == 'unknown' and 'consumed' in second.message and 'reconcile' in second.message


@pytest.mark.skipif(action_process.os.name != 'nt', reason='native Windows process handle')
def test_windows_refuses_an_unobservable_parent_before_consuming(dispatch):
    import os
    import sys
    import time
    from action_queue import compute_payload_hash
    envelope = {'module': 'actions.reminder_create', 'proposal': asdict(proposal()),
        'parent': 0, 'timeout': 5, 'operation': 'execute', 'since': '', 'queue_path': str(dispatch),
        'credentials': {'schema': 'artha.action-credentials.v1', 'module': 'actions.reminder_create',
            'payload_hash': compute_payload_hash(proposal()),
            'operation': 'execute', 'since': '', 'nonce': 'b' * 32, 'issued_at': time.time(),
            'expires_at': time.time() + 7, 'records': {}, 'api_keys': {}}}
    child = subprocess.run([sys.executable, action_process.__file__], input=json.dumps(envelope),
        capture_output=True, text=True, env={**{k: v for k, v in os.environ.items()
            if k in action_process._CHILD_ENV}, 'ARTHA_NO_REEXEC': '1'}, timeout=15)
    assert child.returncode not in (0, 124) and not child.stdout
    assert 'OSError' in child.stderr or 'PermissionError' in child.stderr
    import sqlite3
    with closing(sqlite3.connect(dispatch)) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == 0


def test_script_entry_restores_its_scripts_import_path(monkeypatch):
    import runpy
    scripts = str(action_process._SCRIPTS)
    monkeypatch.setattr(action_process.sys, 'path', [entry for entry in action_process.sys.path if entry != scripts])
    namespace = runpy.run_path(action_process.__file__, run_name='synthetic_action_process')
    assert action_process.sys.path[0] == scripts
    assert namespace['PROVIDERS'] == action_process.PROVIDERS


def test_claim_consumed_during_credential_preparation_never_starts_another_child(dispatch, monkeypatch):
    from lib import action_credentials

    def capture(*args, **kwargs):
        action_process._authorize(dispatch, proposal(), 'execute', consume=True)
        return {}

    monkeypatch.setattr(action_credentials, 'prepare', capture)
    result = action_process.execute('actions.reminder_create', proposal(), 5, queue_path=dispatch,
        runner=lambda *args, **kwargs: pytest.fail('consumed claim started another child'))
    assert result.status == 'unknown' and 'consumed' in result.message and 'reconcile' in result.message


def test_unreadable_receipt_history_during_preparation_remains_unknown(dispatch, monkeypatch):
    import sqlite3
    from lib import action_credentials

    def unavailable(*args):
        with closing(sqlite3.connect(dispatch)) as db, db:
            db.execute('DROP TABLE action_dispatch_receipts')
        raise ValueError('synthetic-sensitive-evidence-error')

    monkeypatch.setattr(action_process, '_prepare_evidence', unavailable)
    monkeypatch.setattr(action_credentials, 'prepare', lambda *args, **kwargs: pytest.fail('credentials captured'))
    result = action_process.execute('actions.reminder_create', proposal(), 5, queue_path=dispatch,
        runner=lambda *args, **kwargs: pytest.fail('unverified receipt history started a child'))
    assert result.status == 'unknown' and 'prior dispatch cannot be ruled out' in result.message
    assert 'synthetic-sensitive' not in result.message


def test_evidence_preparation_refusal_prevents_credentials_and_child(dispatch, monkeypatch):
    from lib import action_credentials

    def unavailable(*args):
        raise ValueError('synthetic-sensitive-evidence-error')

    monkeypatch.setattr(action_process, '_prepare_evidence', unavailable)
    monkeypatch.setattr(action_credentials, 'prepare', lambda *args, **kwargs: pytest.fail('credentials captured'))
    result = action_process.execute('actions.reminder_create', proposal(), 5, queue_path=dispatch,
        runner=lambda *args, **kwargs: pytest.fail('refused evidence started a child'))
    assert result.status == 'failure' and 'Current evidence authorization is unavailable' in result.message
    assert 'synthetic-sensitive' not in result.message


def test_noncore_preconditions_do_not_capture_or_bind_core_evidence(dispatch, monkeypatch):
    from dataclasses import replace
    from lib import action_evidence

    def forbidden(*args):
        pytest.fail('noncore action acquired core evidence authority')

    monkeypatch.setattr(action_evidence, 'prepare', forbidden)
    monkeypatch.setattr(action_evidence, 'bind', forbidden)
    item = replace(proposal(), parameters={'preconditions': {'provider_versions': {'synthetic': 'v1'}}})
    assert action_process._prepare_evidence(dispatch, item) is None


@pytest.mark.parametrize('change', ['none', 'pause', 'withdraw', 'version', 'expired_credentials', 'lost_response'])
def test_worker_checks_registered_provider_under_scope_before_consuming(dispatch, monkeypatch, capsys, change):
    import io
    import os
    import sqlite3
    import time
    import keyring
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from pathlib import Path
    from action_queue import compute_payload_hash
    from actions import calendar_modify
    from catchup_core import crypto
    from lib.action_policy import load_dispatch_policy
    from lib.action_credentials import CredentialCapabilityError
    from unittest.mock import MagicMock

    item = replace(proposal(), action_type='calendar_modify', parameters={
        'event_id': 'event1', 'calendar_id': 'synthetic@example.test', 'expected_etag': '"v1"',
        'updates': {'summary': 'Changed'}, 'preconditions': {'provider_versions': {'event1': '"v1"'}}})
    with closing(sqlite3.connect(dispatch)) as db, db:
        root = Path(db.execute('SELECT policy_root FROM action_dispatch_policies').fetchone()[0])
        policy_file = root / 'config/actions.yaml'
        policy_file.write_text('live_trial: {held: []}\nactions:\n  calendar_modify: {enabled: true}\n')
        policy = load_dispatch_policy(root, item.action_type)
        digest = compute_payload_hash(item)
        db.execute('UPDATE actions SET payload_hash=?,approved_payload_hash=?,preview_payload_hash=?',
                   (digest, digest, digest))
        db.execute('UPDATE effect_operations SET payload_hash=?', (digest,))
        db.execute('UPDATE action_dispatch_policies SET policy_sha256=?', (policy.sha256,))
    scoped = offline_calendar_capability(item)
    now = datetime.now(UTC)
    scoped['records'] = {'google.gcal-oauth-token': {'token': 'synthetic-calendar-token',
        'expiry': (now + timedelta(hours=1)).isoformat()}}
    envelope = {'module': 'actions.calendar_modify', 'proposal': asdict(item), 'parent': os.getpid(),
        'timeout': 5, 'operation': 'execute', 'since': '', 'queue_path': str(dispatch), 'credentials': scoped}
    monkeypatch.setattr(action_process.sys, 'stdin', io.StringIO(json.dumps(envelope)))
    monkeypatch.setattr(action_process, '_start_lifetime', lambda *args: None)
    restricted = []
    monkeypatch.setattr(action_process, 'restrict_network', lambda hosts: restricted.append(hosts))
    from keyring.backends.null import Keyring
    monkeypatch.setattr(keyring.core, '_keyring_backend', Keyring())
    monkeypatch.setattr(crypto, '_action_keyring', None)
    api = MagicMock()
    api.calendarList.return_value.get.return_value.execute.return_value = {
        'id': 'synthetic@example.test', 'accessRole': 'writer'}
    api.events.return_value.patch.return_value.headers = {}
    api.events.return_value.patch.return_value.execute.return_value = {'id': 'event1', 'etag': '"v2"'}
    if change == 'lost_response':
        api.events.return_value.patch.return_value.execute.side_effect = ConnectionError('synthetic lost response')

    def event_read():
        assert restricted == [action_process.PROVIDERS['actions.calendar_modify']]
        from lib.credentials import CredentialStore
        assert CredentialStore().read('google.gcal-oauth-token')['token'] == 'synthetic-calendar-token'
        assert CredentialStore().read('msgraph.personal') is None
        with closing(sqlite3.connect(dispatch)) as db, db:
            assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] <= 1
            if change == 'withdraw':
                db.execute("UPDATE actions SET status='cancelled'")
        if change == 'pause':
            (root / 'config/actions.paused').touch()
        elif change == 'expired_credentials':
            monkeypatch.setattr(time, 'time', lambda: scoped['expires_at'] + 1)
        return {'id': 'event1', 'etag': '"changed"' if change == 'version' else '"v1"'}

    api.events.return_value.get.return_value.execute.side_effect = event_read
    monkeypatch.setattr(calendar_modify, 'build_service', lambda *args: api)
    if change in {'none', 'lost_response'}:
        action_process._worker()
        result = json.loads(capsys.readouterr().out)
        assert result['status'] == ('unknown' if change == 'lost_response' else 'success')
        assert api.events.return_value.patch.return_value.headers['If-Match'] == '"v1"'
    else:
        with pytest.raises((ValueError, RuntimeError, CredentialCapabilityError)):
            action_process._worker()
        api.events.return_value.patch.assert_not_called()
    with closing(sqlite3.connect(dispatch)) as db:
        assert db.execute('SELECT count(*) FROM action_dispatch_receipts').fetchone()[0] == (
            1 if change in {'none', 'lost_response'} else 0)


def offline_calendar_capability(item):
    import time
    from action_queue import compute_payload_hash
    now = time.time()
    return {'schema': 'artha.action-credentials.v1', 'module': 'actions.calendar_modify',
        'payload_hash': compute_payload_hash(item), 'operation': 'execute', 'since': '', 'nonce': 'b' * 32,
        'issued_at': now, 'expires_at': now + 7, 'records': {}, 'api_keys': {}}


@pytest.mark.parametrize('missing_binding', [False, True])
def test_core_evidence_preparation_uses_the_claimed_policy_and_binds_before_return(dispatch, monkeypatch, missing_binding):
    import sqlite3
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from lib import action_evidence
    from catchup_core.actions import normalize_preconditions
    now = datetime.now(timezone.utc)
    pre = {'fact_versions': {'synthetic-fact': now.isoformat()}, 'subjects': ['owner', 'owner'],
        'permission_generation': 0, 'fresh_until': (now + timedelta(hours=1)).isoformat()}
    item = replace(proposal(), source_skill='catchup_core', parameters={'preconditions': pre})
    context = {'synthetic': 'bound-evidence'}
    calls = []
    with closing(sqlite3.connect(dispatch)) as db, db:
        root = Path(db.execute('SELECT policy_root FROM action_dispatch_policies').fetchone()[0])
        if missing_binding:
            db.execute('DELETE FROM action_dispatch_policies')

    def prepare(received, policy_root, normalized):
        assert received is item and policy_root == root
        assert normalized == normalize_preconditions(pre, core_derived=True)
        calls.append(normalized)
        return context

    monkeypatch.setattr(action_evidence, 'prepare', prepare)
    if missing_binding:
        with pytest.raises(ValueError, match='missing evidence policy binding'):
            action_process._prepare_evidence(dispatch, item)
        assert not calls
    else:
        from action_queue import compute_payload_hash
        assert action_process._prepare_evidence(dispatch, item) is context
        assert len(calls) == 1
        with closing(sqlite3.connect(dispatch)) as db:
            digest, bound = db.execute('SELECT payload_hash,context_json FROM action_evidence_contexts '
                                      'WHERE action_id=?', (item.id,)).fetchone()
        assert digest == compute_payload_hash(item) and json.loads(bound) == context


def test_instruction_sheet_without_required_preview_uses_current_policy(dispatch):
    import hashlib
    import sqlite3
    from dataclasses import replace
    from pathlib import Path
    item = replace(proposal(), action_type='instruction_sheet', preview_required=False)
    with closing(sqlite3.connect(dispatch)) as db, db:
        root = Path(db.execute('SELECT policy_root FROM action_dispatch_policies').fetchone()[0])
        policy = root / 'config/actions.yaml'
        policy.write_text('live_trial: {held: []}\nactions:\n  instruction_sheet: {enabled: true}\n')
        db.execute('UPDATE action_dispatch_policies SET policy_sha256=?', (hashlib.sha256(policy.read_bytes()).hexdigest(),))
        db.execute('UPDATE actions SET preview_shown_at=NULL,preview_payload_hash=NULL')
        action_process._authorize_current_policy(db, item)      # no preview needed: returns without refusing
        # the same row is refused once the current policy requires a preview, so the check above is not vacuous
        policy.write_text('live_trial: {held: []}\nactions:\n  instruction_sheet: {enabled: true, preview_required: true}\n')
        db.execute('UPDATE action_dispatch_policies SET policy_sha256=?', (hashlib.sha256(policy.read_bytes()).hexdigest(),))
        with pytest.raises(ValueError, match='preview expired'):
            action_process._authorize_current_policy(db, item)
        policy.write_text('live_trial: {held: []}\nactions:\n  instruction_sheet: {enabled: true}\n# edited\n')
        with pytest.raises(ValueError, match='policy changed'):
            action_process._authorize_current_policy(db, item)


@pytest.mark.parametrize('with_evidence', [False, True])
def test_worker_captures_native_head_before_scoped_credentials_and_forwards_both_checks(
        dispatch, monkeypatch, capsys, with_evidence):
    import io
    import os
    import keyring.core
    from keyring.backends.null import Keyring
    from catchup_core import crypto
    from lib import action_credentials, action_evidence
    from actions.base import ActionResult
    item = proposal()
    context = {'synthetic': 'metadata'} if with_evidence else None
    reader = lambda: {'synthetic': 'head'}
    credentials = action_credentials.prepare('actions.reminder_create', item, 5)
    envelope = {'module': 'actions.reminder_create', 'proposal': asdict(item), 'parent': os.getpid(),
        'timeout': 5, 'queue_path': str(dispatch), 'credentials': credentials, 'evidence': context}
    monkeypatch.setattr(action_process.sys, 'stdin', io.StringIO(json.dumps(envelope)))
    monkeypatch.setattr(crypto, '_action_keyring', None)
    monkeypatch.setattr(keyring.core, '_keyring_backend', Keyring())
    monkeypatch.setattr(action_process, '_start_lifetime', lambda *args: None)
    monkeypatch.setattr(action_process, 'restrict_network', lambda hosts: None)
    events = []
    original_authorize = action_process._authorize
    original_install = action_credentials.install
    original_import = action_process.importlib.import_module

    def capture_head():
        assert with_evidence and crypto._action_keyring is None
        events.append('head')
        return reader

    def authorize(path, received, operation, *, consume, evidence, read_head):
        assert evidence == context and read_head is (reader if with_evidence else None)
        assert (crypto._action_keyring is not None) == consume
        events.append('consume' if consume else 'check')
        original_authorize(path, received, operation, consume=consume, evidence=evidence, read_head=read_head)

    def install(*args):
        events.append('install')
        original_install(*args)

    def execute(received):
        assert received == item and crypto._action_keyring is not None
        events.append('handler')
        return ActionResult('success', 'synthetic verified receipt', None, False, None)

    def load(name, *args, **kwargs):
        if name == 'actions.reminder_create':
            return SimpleNamespace(execute=execute)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(action_evidence, 'native_head_reader', capture_head)
    monkeypatch.setattr(action_process, '_authorize', authorize)
    monkeypatch.setattr(action_credentials, 'install', install)
    monkeypatch.setattr(action_process.importlib, 'import_module', load)
    action_process._worker()
    assert json.loads(capsys.readouterr().out)['status'] == 'success'
    assert events == (['head'] if with_evidence else []) + ['check', 'install', 'consume', 'handler']
