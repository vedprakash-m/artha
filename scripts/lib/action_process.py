"""Short-lived action process with a minimal environment and fixed provider egress.

The parent retains approval/operation authority. The child accepts one exact proposal,
cannot start other processes, and dies on expiry or parent loss. An uncertain child
exit never authorizes retry. This is containment for trusted handlers, not an OS sandbox.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from actions.base import ActionProposal, ActionResult, VALID_RESULT_STATUSES

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


# Interpreter plumbing only, never credentials: a Python that is not on the default library path (for example the CI
# toolcache build or a framework install) cannot even start without these.  Provider secrets stay out of the child.
_CHILD_ENV = ("SYSTEMROOT", "WINDIR", "LANG", "HOME", "TMPDIR", "TEMP", "TMP", "PYTHONHOME",
              "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")
_ERROR_LINE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b")


def _child_failure_class(stderr: str) -> str:
    """Exception class of a crashed child (never its message, which could carry paths or values)."""
    lines = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
    match = _ERROR_LINE.match(lines[-1]) if lines else None
    return match.group(1) if match else "no error class"


def execute(module: str, proposal: ActionProposal, timeout: int, *, runner=subprocess.run,
            operation: str = "execute", since: str = "", queue_path: Path | None = None) -> ActionResult:
    """Run one handler operation (``execute`` or ``reconcile``) in the bounded child.

    Reconciliation asks the provider, so it gets the same minimal environment and egress policy as execution; it can
    only ever *confirm* an effect, and a child that does not return a receipt leaves the action ``unknown``.
    """
    if operation not in ("execute", "reconcile"):
        raise ValueError("unsupported action operation")
    if module not in PROVIDERS:
        return ActionResult(
            "failure", "This action has no isolated provider policy; use an instruction sheet.", None, False, None
        )
    from actions import _HANDLER_MAP
    if _HANDLER_MAP.get(proposal.action_type) != module:
        return ActionResult('failure', 'The handler does not match the approved action type.', None, False, None)
    try:
        _authorize(queue_path, proposal, operation, consume=False)
    except _NoReceipt:
        return ActionResult('unknown', 'An action child already consumed this claim; reconcile before retrying.',
                            None, False, None)
    except (OSError, ValueError, sqlite3.Error):
        return _pre_dispatch_failure(queue_path, proposal, operation, 'An approved queue claim is required.')
    from lib.action_credentials import CredentialCapabilityError, prepare
    try:
        evidence = _prepare_evidence(queue_path, proposal)
    except Exception:
        return _pre_dispatch_failure(queue_path, proposal, operation,
                                     'Current evidence authorization is unavailable; review again.')
    try:
        credentials = prepare(module, proposal, timeout, operation=operation, since=since)
    except CredentialCapabilityError:
        return _pre_dispatch_failure(queue_path, proposal, operation,
                                     'Action credentials unavailable; authenticate through setup.')
    try:
        _authorize(queue_path, proposal, operation, consume=False)
    except _NoReceipt:
        return ActionResult('unknown', 'An action child already consumed this claim; reconcile before retrying.',
                            None, False, None)
    except (OSError, ValueError, sqlite3.Error):
        return _pre_dispatch_failure(queue_path, proposal, operation,
                                     'Dispatch authority changed during credential preparation; review again.')
    environment = {k: v for k, v in os.environ.items() if k in _CHILD_ENV}
    environment["ARTHA_NO_REEXEC"] = "1"          # the child already runs on the right interpreter; never re-exec into a venv
    envelope = {"module": module, "proposal": asdict(proposal), "parent": os.getpid(), "timeout": timeout,
                "operation": operation, "since": since, "queue_path": str(queue_path), "credentials": credentials, "evidence": evidence}
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
            raise _NoReceipt(_child_failure_class(getattr(result, "stderr", "") or ""))
        outcome = json.loads(result.stdout)
        if (not isinstance(outcome, dict) or not isinstance(outcome.get('status'), str)
                or outcome['status'] not in VALID_RESULT_STATUSES or not isinstance(outcome.get('message'), str)
                or type(outcome.get('reversible')) is not bool
                or (outcome.get('data') is not None and not isinstance(outcome['data'], dict))):
            raise ValueError('invalid action receipt')
        if isinstance(outcome.get("reverse_action"), dict):
            outcome["reverse_action"] = ActionProposal(**outcome["reverse_action"])
        return ActionResult(**outcome)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        why = exc.reason if isinstance(exc, _NoReceipt) else type(exc).__name__
        return ActionResult(
            "unknown",
            f"The isolated action did not return a verified receipt ({why}); reconcile before retrying.",
            None,
            False,
            None,
        )


class _NoReceipt(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _pre_dispatch_failure(queue_path: Path | None, proposal: ActionProposal, operation: str,
                          message: str) -> ActionResult:
    uncertain = operation == 'reconcile'
    try:
        if queue_path is not None and queue_path.is_file():
            with closing(sqlite3.connect(queue_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                uncertain = uncertain or db.execute(
                    'SELECT 1 FROM action_dispatch_receipts WHERE action_id=?', (proposal.id,)).fetchone() is not None
    except (OSError, sqlite3.Error):
        uncertain = True
    if uncertain:
        message += ' A prior dispatch cannot be ruled out; reconcile before retrying.'
    return ActionResult('unknown' if uncertain else 'failure', message, None, False, None)


def _authorize(queue_path: Path | None, proposal: ActionProposal, operation: str, *, consume: bool,
               evidence: Any = None, read_head: Any = None) -> None:
    """Require the executor's durable approval and claim before loading any provider code.

    The worker consumes each execution once. A crash after consumption leaves the existing claim uncertain;
    neither a diagnostic invocation nor a second child can replay it. This protects trusted local entry points,
    not an attacker who can rewrite the queue or Python code under the same OS account.
    """
    from action_queue import compute_payload_hash
    from actions import _HANDLER_MAP
    from catchup_core.session_lock import require_active_installation
    if queue_path is not None:
        require_active_installation(queue_path.parent)
    if queue_path is None or not Path(queue_path).is_file() or operation not in ("execute", "reconcile"):
        raise ValueError("missing dispatch authority")
    mode = "rw" if consume and operation == "execute" else "ro"
    with closing(sqlite3.connect(Path(queue_path).resolve().as_uri() + f"?mode={mode}", uri=True)) as db:
        if consume and operation == 'execute':
            db.execute('BEGIN IMMEDIATE')
        from catchup_core.authority_transfer import require_dispatch_authority
        require_dispatch_authority(db)
        expected = compute_payload_hash(proposal)
        row = db.execute("SELECT a.status,a.payload_hash,a.approved_payload_hash,o.operation_key "
            "FROM actions a JOIN effect_operations o ON o.action_id=a.id "
            "WHERE a.id=? AND o.payload_hash=?", (proposal.id, expected)).fetchone()
        wanted = "unknown" if operation == "reconcile" else "executing"
        if not row or row[0] != wanted or row[1] != expected or row[2] != expected:
            raise ValueError("queue claim does not authorize this exact payload")
        if proposal.action_type not in _HANDLER_MAP:
            raise ValueError("unregistered action type")
        if operation == 'execute' and not consume and db.execute(
                'SELECT 1 FROM action_dispatch_receipts WHERE action_id=?', (proposal.id,)).fetchone():
            raise _NoReceipt('dispatch already consumed')
        if operation == 'execute':
            _authorize_current_policy(db, proposal, evidence=evidence, read_head=read_head)
        else:
            _authorize_reconciliation_evidence(db, proposal, evidence=evidence, read_head=read_head)
        if consume and operation == "execute":
            with db:
                db.execute("INSERT INTO action_dispatch_receipts(operation_key,action_id) VALUES(?,?)",
                           (row[3], proposal.id))


def _authorize_reconciliation_evidence(db: sqlite3.Connection, proposal: ActionProposal, *,
                                       evidence: Any = None, read_head: Any = None) -> None:
    """An outcome query may redisclose reviewed data and therefore needs current consent.

    Action enablement/pause/approval expiry govern new effects; they do not prevent
    a currently authorized owner from resolving an existing uncertain effect.
    """
    pre = proposal.parameters.get('preconditions')
    if not pre and proposal.source_skill != 'catchup_core':
        return
    from lib.action_policy import require_action_preconditions
    db.row_factory = sqlite3.Row
    binding = db.execute('SELECT policy_root FROM action_dispatch_policies WHERE action_id=?',
                         (proposal.id,)).fetchone()
    if binding is None:
        raise ValueError('reconciliation has no evidence policy binding')
    row = db.execute('SELECT * FROM actions WHERE id=?', (proposal.id,)).fetchone()
    require_action_preconditions(proposal, Path(binding['policy_root']), raw_row=dict(row),
        evidence_context=evidence, evidence_queue=db, evidence_head_reader=read_head)


def _authorize_current_policy(db: sqlite3.Connection, proposal: ActionProposal, *,
                              evidence: Any = None, read_head: Any = None) -> None:
    from lib.action_policy import authorize_dispatch_policy, load_dispatch_policy
    db.row_factory = sqlite3.Row
    binding = db.execute('SELECT policy_root,policy_sha256 FROM action_dispatch_policies WHERE action_id=?',
                         (proposal.id,)).fetchone()
    if not binding:
        raise ValueError('execution claim has no current policy binding')
    policy = load_dispatch_policy(Path(binding['policy_root']), proposal.action_type)
    if policy.sha256 != binding['policy_sha256']:
        raise ValueError('action policy changed after approval')
    row = db.execute('SELECT * FROM actions WHERE id=?', (proposal.id,)).fetchone()
    now = datetime.now(timezone.utc)
    authorize_dispatch_policy(
        policy,
        replace(proposal, expires_at=row['expires_at']),
        row['approved_by'],
        now,
        raw_row=dict(row), evidence_context=evidence, evidence_queue=db, evidence_head_reader=read_head,
    )
    if proposal.action_type != 'instruction_sheet' or proposal.preview_required or policy.config.get('preview_required'):
        shown = datetime.fromisoformat(row['preview_shown_at']) if isinstance(row['preview_shown_at'], str) else None
        from action_queue import compute_payload_hash
        if (shown is None or shown.tzinfo is None or not 0 <= (now - shown).total_seconds() <= 1800
                or row['preview_payload_hash'] != compute_payload_hash(proposal)):
            raise ValueError('action preview expired; review again')


def _prepare_evidence(queue_path: Path, proposal: ActionProposal) -> Any:
    from catchup_core.actions import normalize_preconditions
    from lib.action_evidence import bind, prepare
    pre = proposal.parameters.get('preconditions')
    if pre is None:
        return None
    normalized = normalize_preconditions(pre, core_derived=proposal.source_skill == 'catchup_core')
    if not normalized.get('core_derived'):
        return None
    with closing(sqlite3.connect(queue_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT policy_root FROM action_dispatch_policies WHERE action_id=?', (proposal.id,)).fetchone()
        if not row:
            raise ValueError('missing evidence policy binding')
    context = prepare(proposal, Path(row[0]), normalized)
    bind(queue_path, proposal, context)
    return context


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


def _start_lifetime(parent: int, deadline: float) -> None:
    """Watch the actual parent, not the transient Windows venv launcher."""
    alive = lambda: os.getppid() == parent
    close = lambda: None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # A stable, non-inherited SYNCHRONIZE handle prevents PID reuse from extending authority.
        handle = kernel.OpenProcess(0x00100000, False, parent)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        def alive():
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 258):  # signalled / WAIT_TIMEOUT
                raise ctypes.WinError(ctypes.get_last_error())
            return result == 258

        def close():
            kernel.CloseHandle(handle)

    def lifetime():
        try:
            while time.monotonic() < deadline and alive():
                time.sleep(0.1)
        finally:
            close()
            os._exit(124)

    try:
        threading.Thread(target=lifetime, daemon=True).start()
    except BaseException:
        close()
        raise


def _worker() -> None:
    envelope = json.loads(sys.stdin.read(2_000_000))
    module = envelope["module"]
    if module not in PROVIDERS:
        raise ValueError("unregistered handler")
    proposal = ActionProposal(**envelope["proposal"])
    from actions import _HANDLER_MAP
    if _HANDLER_MAP.get(proposal.action_type) != module:
        raise ValueError("handler does not match the approved action type")
    queue_path = Path(envelope["queue_path"]) if envelope.get("queue_path") else None
    operation, since = envelope.get('operation', 'execute'), envelope.get('since', '')
    evidence = envelope.get('evidence') or None
    from catchup_core.actions import normalize_preconditions
    normalized = normalize_preconditions(proposal.parameters.get('preconditions'),
                                         core_derived=proposal.source_skill == 'catchup_core')
    if normalized.get('core_derived') and evidence is None:
        raise ValueError('core-derived worker requires scoped evidence capability')
    read_head = None
    if evidence:
        from lib.action_evidence import native_head_reader
        read_head = native_head_reader()
    _authorize(queue_path, proposal, operation, consume=False, evidence=evidence, read_head=read_head)
    from lib.action_credentials import install, redact, validate
    install(envelope.get('credentials'), module, proposal, operation, since)
    remaining = envelope['credentials']['expires_at'] - time.time()
    deadline = time.monotonic() + min(remaining, min(120, max(1, envelope["timeout"])))
    _start_lifetime(envelope["parent"], deadline)
    restrict_network(PROVIDERS[module])
    # Handler diagnostics cannot corrupt the exact result protocol or leak to parent logs.
    from contextlib import redirect_stderr, redirect_stdout
    import io

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        if operation == 'execute':
            from lib.action_evidence import verify_provider
            verify_provider(proposal, module)
        validate(envelope['credentials'], module, proposal, operation, since)
        _authorize(queue_path, proposal, operation, consume=True, evidence=evidence, read_head=read_head)
        del read_head
        handler = importlib.import_module(module)
        try:
            if operation == 'reconcile':
                result = handler.reconcile(proposal, since=since)
            else:
                result = handler.execute(proposal)
        except Exception:
            result = ActionResult('unknown', 'The handler returned no verified receipt; reconcile before retrying.',
                                  None, False, None)
    print(json.dumps(redact(asdict(result), envelope['credentials'])))


if __name__ == "__main__":
    _worker()
