"""Session child lifetime uses real processes and inert synthetic commands."""
import os
import signal
import subprocess
import sys

import pytest

from catchup_core import renewal_runner
from catchup_core.renewal_store import RenewalError


def test_local_success_and_failure_are_bounded(tmp_path):
    renewal_runner.run([sys.executable, '-c', 'pass'], tmp_path, timeout=5)
    with pytest.raises(RenewalError):
        renewal_runner.run([sys.executable, '-c', 'raise SystemExit(1)'], tmp_path, timeout=5)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process group observation; Windows job case runs in Windows CI')
def test_parent_pipe_loss_terminates_child_group(tmp_path):
    import time
    pid = tmp_path / 'pid'
    command = [sys.executable, '-c', 'import os,time; from pathlib import Path; Path(' + repr(str(pid)) + ').write_text(str(os.getpid())); time.sleep(30)']
    child = renewal_runner.start(command, tmp_path, timeout=10)
    try:
        deadline = time.monotonic() + 5
        while not pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid.exists()
        actual_pid = int(pid.read_text())
        child.stdin.close()
        child.wait(timeout=5)
        # A zombie is terminated too; it cannot execute or renew and is reaped by its parent/OS.
        result = subprocess.run(['ps', '-o', 'stat=', '-p', str(actual_pid)], capture_output=True, text=True)
        assert not result.stdout.strip() or result.stdout.strip().startswith('Z')
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_timeout_does_not_leave_session_child_running(tmp_path):
    with pytest.raises(RenewalError):
        renewal_runner.run([sys.executable, '-c', 'import time; time.sleep(30)'], tmp_path, timeout=1)


@pytest.mark.parametrize('command,timeout', [('invalid', 1), ([], 1), (['bad\0arg'], 1), (['x'], 0), (['x'], True), (['x' * 20000], 1)])
def test_invalid_worker_inputs_never_launch(command, timeout, tmp_path):
    with pytest.raises(RenewalError):
        renewal_runner.start(command, tmp_path, timeout=timeout,
                             launcher=lambda *_a, **_kw: pytest.fail('invalid worker launched'))


class Function:
    def __init__(self, result=1):
        self.result, self.calls = result, []
    def __call__(self, *args):
        self.calls.append(args)
        return self.result


@pytest.mark.parametrize('failure', [None, 'CreateJobObjectW', 'SetInformationJobObject', 'AssignProcessToJobObject', 'CloseHandle'])
def test_windows_job_api_contract_refuses_unavailable_containment(failure):
    from types import SimpleNamespace
    api = SimpleNamespace(**{name: Function(0 if name == failure else 1) for name in (
        'CreateJobObjectW', 'SetInformationJobObject', 'AssignProcessToJobObject', 'GetCurrentProcess', 'CloseHandle')})
    if failure:
        with pytest.raises(RenewalError):
            renewal_runner._windows_job(api)()
    else:
        renewal_runner._windows_job(api)()
        assert api.SetInformationJobObject.calls[0][1] == 9
        assert api.SetInformationJobObject.calls[0][2]._obj.basic.flags == 0x2000
        assert api.AssignProcessToJobObject.calls and api.CloseHandle.calls


def test_windows_loader_and_platform_cleanup_routes(monkeypatch):
    from types import SimpleNamespace
    api = SimpleNamespace(**{name: Function() for name in (
        'CreateJobObjectW', 'SetInformationJobObject', 'AssignProcessToJobObject', 'GetCurrentProcess', 'CloseHandle')})
    monkeypatch.setattr(renewal_runner.ctypes, 'WinDLL', None, raising=False)
    with pytest.raises(RenewalError):
        renewal_runner._windows_job()
    monkeypatch.setattr(renewal_runner.ctypes, 'WinDLL', lambda *_a, **_kw: api)
    renewal_runner._windows_job()()
    monkeypatch.setattr(renewal_runner, 'sys', SimpleNamespace(platform='win32'))
    renewal_runner._containment()()
    calls = []
    monkeypatch.setattr(renewal_runner, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(renewal_runner.os, 'killpg', lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(renewal_runner.os, 'getpgrp', getattr(os, 'getpgrp', lambda: 4242), raising=False)
    monkeypatch.setattr(renewal_runner.signal, 'SIGKILL', getattr(signal, 'SIGKILL', 9), raising=False)
    renewal_runner._containment()()
    assert calls[0][0] == renewal_runner.os.getpgrp()


def test_worker_environment_excludes_credentials_and_gate_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_PRIVATE_CANARY', 'SYNTHETIC_SECRET_CANARY')
    monkeypatch.setenv('MAKEFLAGS', '--eval=unapproved')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nothing')
    calls = []
    renewal_runner.start(['make', 'check'], tmp_path, launcher=lambda *args, **kw: calls.append((args, kw)))
    environment = calls[0][1]['env']
    assert not {'ARTHA_PRIVATE_CANARY', 'MAKEFLAGS', 'PYTEST_ADDOPTS'} & environment.keys()
    assert 'SYNTHETIC_SECRET_CANARY' not in repr(calls)
    def unavailable(*_a, **_kw):
        raise OSError('PRIVATE_CANARY')
    with pytest.raises(RenewalError, match='^session gate process unavailable$'):
        renewal_runner.start(['make', 'check'], tmp_path, launcher=unavailable)


@pytest.mark.parametrize('outcome', ['passed', 'failed', 'timeout'])
def test_worker_completion_and_parent_loss_always_request_cleanup(tmp_path, monkeypatch, capsys, outcome):
    import io
    from types import SimpleNamespace
    cleaned, watchers = [], []
    monkeypatch.setattr(renewal_runner, '_containment', lambda: lambda: cleaned.append(True))
    monkeypatch.setattr(renewal_runner.threading, 'Thread', lambda target, **_: watchers.append(target) or
                        SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(renewal_runner, 'sys', SimpleNamespace(stdin=SimpleNamespace(buffer=io.BytesIO())))
    def run(*_a, **_kw):
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired('synthetic', 1)
        return SimpleNamespace(returncode=0 if outcome == 'passed' else 1)
    monkeypatch.setattr(renewal_runner.subprocess, 'run', run)
    renewal_runner._worker(['synthetic'], tmp_path, 1, os.getppid())
    assert capsys.readouterr().out == ('PASS\n' if outcome == 'passed' else 'FAIL\n')
    watchers[0]()
    assert cleaned == [True, True]
    with pytest.raises(RenewalError):
        renewal_runner._worker(['synthetic'], tmp_path, 1, -1)


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
def test_parent_timeout_requests_native_tree_cleanup(tmp_path, monkeypatch, platform):
    import io
    from types import SimpleNamespace
    class Child:
        pid, count = 987654321, 0
        stdin, stdout = io.BytesIO(), io.BytesIO()
        def wait(self, timeout):
            self.count += 1
            if self.count < 3:
                raise subprocess.TimeoutExpired('synthetic', timeout)
            return 0
        def kill(self):
            killed.append(self.pid)
    child, killed = Child(), []
    monkeypatch.setattr(renewal_runner, 'start', lambda *_a, **_kw: child)
    monkeypatch.setattr(renewal_runner, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(renewal_runner.os, 'killpg', lambda pid, _: killed.append(pid), raising=False)
    monkeypatch.setattr(renewal_runner.signal, 'SIGKILL', getattr(signal, 'SIGKILL', 9), raising=False)
    with pytest.raises(RenewalError):
        renewal_runner.run(['synthetic'], tmp_path, timeout=1)
    assert killed == [child.pid]
