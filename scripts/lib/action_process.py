"""Short-lived action process with a minimal environment and fixed provider egress.

The parent retains approval/operation authority. The child accepts one exact proposal,
cannot start other processes, and dies on expiry or parent loss. An uncertain child
exit never authorizes retry. This is containment for trusted handlers, not an OS sandbox.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from actions.base import ActionProposal, ActionResult

PROVIDERS = {
    "actions.reminder_create": frozenset({"graph.microsoft.com", "login.microsoftonline.com"}),
    "actions.todo_sync_action": frozenset({"graph.microsoft.com", "login.microsoftonline.com"}),
    "actions.email_send": frozenset({"gmail.googleapis.com", "www.googleapis.com", "oauth2.googleapis.com"}),
    "actions.email_reply": frozenset({"gmail.googleapis.com", "www.googleapis.com", "oauth2.googleapis.com"}),
    "actions.calendar_create": frozenset({"www.googleapis.com", "oauth2.googleapis.com"}),
    "actions.calendar_modify": frozenset({"www.googleapis.com", "oauth2.googleapis.com"}),
    "actions.slack_send": frozenset({"slack.com"}),
    "actions.todoist_sync": frozenset({"api.todoist.com"}),
    "actions.instruction_sheet": frozenset(),
}


def execute(module: str, proposal: ActionProposal, timeout: int, *, runner=subprocess.run) -> ActionResult:
    if module not in PROVIDERS:
        return ActionResult(
            "failure", "This action has no isolated provider policy; use an instruction sheet.", None, False, None
        )
    environment = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "WINDIR", "LANG", "HOME")}
    envelope = {"module": module, "proposal": asdict(proposal), "parent": os.getpid(), "timeout": timeout}
    try:
        result = runner(
            [sys.executable, str(Path(__file__).resolve())],
            input=json.dumps(envelope),
            capture_output=True,
            text=True,
            env=environment,
            cwd=str(_SCRIPTS.parent),
            timeout=timeout + 2,
            check=False,
        )
        if result.returncode or len(result.stdout) > 1_000_000:
            raise ValueError("no committed provider receipt")
        outcome = json.loads(result.stdout)
        if isinstance(outcome.get("reverse_action"), dict):
            outcome["reverse_action"] = ActionProposal(**outcome["reverse_action"])
        return ActionResult(**outcome)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return ActionResult(
            "unknown",
            "The isolated action did not return a verified receipt; reconcile before retrying.",
            None,
            False,
            None,
        )


def restrict_network(hosts: frozenset[str]) -> None:
    original = socket.getaddrinfo
    addresses = set()

    def resolve(host, port, *args, **kwargs):
        if host not in hosts or int(port) != 443:
            raise PermissionError("action provider is outside its egress policy")
        found = original(host, port, *args, **kwargs)
        addresses.update(info[4][0] for info in found)
        return found

    socket.getaddrinfo = resolve

    def audit(event, args):
        if event == "socket.connect" and (args[1][0] not in addresses or args[1][1] != 443):
            raise PermissionError("action connection is outside its egress policy")
        if event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "socket.bind"):
            raise PermissionError("action child cannot launch processes or listen")

    sys.addaudithook(audit)


def _worker() -> None:
    envelope = json.loads(sys.stdin.read(2_000_000))
    module = envelope["module"]
    if module not in PROVIDERS:
        raise ValueError("unregistered handler")
    deadline = time.monotonic() + min(120, max(1, envelope["timeout"]))
    parent = envelope["parent"]

    def lifetime():
        while time.monotonic() < deadline and os.getppid() == parent:
            time.sleep(0.1)
        os._exit(124)

    threading.Thread(target=lifetime, daemon=True).start()
    restrict_network(PROVIDERS[module])
    proposal = ActionProposal(**envelope["proposal"])
    handler = importlib.import_module(module)
    # Handler diagnostics cannot corrupt the exact result protocol or leak to parent logs.
    from contextlib import redirect_stdout
    import io

    with redirect_stdout(io.StringIO()):
        result = handler.execute(proposal)
    print(json.dumps(asdict(result)))


if __name__ == "__main__":
    _worker()
