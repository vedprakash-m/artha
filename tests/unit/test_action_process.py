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
