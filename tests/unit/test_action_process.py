"""No real provider traffic: exercise the action process and egress boundary."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from actions.base import ActionProposal
from lib import action_process


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


def test_process_has_minimal_environment_and_reads_exact_receipt(monkeypatch):
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

    assert action_process.execute("actions.reminder_create", proposal(), 5, runner=runner).status == "success"
    assert action_process.execute("unknown", proposal(), 5).status == "failure"


@pytest.mark.parametrize("kind", ["timeout", "crash", "malformed"])
def test_uncertain_child_never_claims_failure_or_authorizes_retry(kind):
    def runner(*args, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired("child", 1)
        return SimpleNamespace(returncode=1 if kind == "crash" else 0, stdout="not JSON")

    assert action_process.execute("actions.reminder_create", proposal(), 1, runner=runner).status == "unknown"


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
    done = subprocess.run([sys.executable, action_process.__file__], input=envelope, capture_output=True, text=True,
                          env=env, timeout=60, check=False)
    return f"rc={done.returncode} stdout={done.stdout[-300:]!r} stderr={done.stderr[-900:]!r}"


def test_real_child_returns_a_verified_failure_offline(no_real_credentials):
    """End to end through a real child process: no token means a clean 'failure', never a hang or 'unknown'."""
    result = action_process.execute("actions.reminder_create", proposal(), 20)
    assert result.status == "failure" and "To Do" in result.message, (result.message, _child_diagnostics())


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
                          env={"HOME": os.environ.get("HOME", "")}, check=False)
    assert done.stdout.strip() == "dns:denied,ip:denied,spawn:denied,listen:denied", done.stdout + done.stderr


def test_parent_exit_or_deadline_kills_the_child_and_reports_unknown():
    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=124, stdout="")
    assert action_process.execute("actions.reminder_create", proposal(), 1, runner=runner).status == "unknown"


def test_reconcile_runs_in_the_same_bounded_child_and_passes_the_approval_time():
    seen = {}

    def runner(argv, **kwargs):
        seen.update(json.loads(kwargs["input"]))
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"status": "unknown", "message": "no match", "data": None, "reversible": False, "reverse_action": None}))

    result = action_process.execute("actions.reminder_create", proposal(), 5, runner=runner,
                                    operation="reconcile", since="2026-10-20T10:00:00Z")
    assert result.status == "unknown" and seen["operation"] == "reconcile" and seen["since"] == "2026-10-20T10:00:00Z"
    assert set(seen["env"]) <= set(action_process._CHILD_ENV)
    assert not any(k.endswith(("TOKEN", "SECRET", "KEY", "PASSWORD")) for k in seen["env"])
    with pytest.raises(ValueError):
        action_process.execute("actions.reminder_create", proposal(), 5, operation="delete")


def test_real_child_reconcile_without_a_token_stays_unknown(no_real_credentials):
    result = action_process.execute("actions.reminder_create", proposal(), 20, operation="reconcile", since="x")
    assert result.status == "unknown" and "Could not check the provider" in result.message, (
        result.message, _child_diagnostics("reconcile"))


def test_a_child_without_a_receipt_names_its_error_class_but_never_its_message():
    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr=(
            "Traceback (most recent call last):\n  File \"x\", line 1\nPermissionError: /Users/secret/token.json"))
    result = action_process.execute("actions.reminder_create", proposal(), 1, runner=runner)
    assert result.status == "unknown" and "(PermissionError)" in result.message and "/Users" not in result.message
    silent = action_process.execute("actions.reminder_create", proposal(), 1,
                                    runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert "(no error class)" in silent.message
    assert action_process._child_failure_class("SystemExit: 1") == "SystemExit"


def test_the_child_environment_keeps_interpreter_loading_variables_and_nothing_secret(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/python/lib")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-pass")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "must-not-pass")
    seen = {}

    def runner(argv, **kwargs):
        seen.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"status": "success", "message": "ok", "data": None, "reversible": False, "reverse_action": None}))
    action_process.execute("actions.reminder_create", proposal(), 1, runner=runner)
    assert seen.get("LD_LIBRARY_PATH") == "/opt/python/lib" and "GITHUB_TOKEN" not in seen
    assert "MSGRAPH_CLIENT_SECRET" not in seen
