"""Real process death and cooperating writer races with a synthetic file-backed native store."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core_helpers import (
    RENEWAL_BINDING as BINDING,
    RENEWAL_EVALUATOR as EVALUATOR,
    RENEWAL_NOW as NOW,
    RenewalWorkspace as Workspace,
    build_renewal_authority_world,
    renewal_approve as approve,
    renewal_target as target,
)
from catchup_core.renewal_authority import Authority
from catchup_core.renewal_store import RenewalError, digest
from lib.credentials import CredentialStore


@pytest.fixture
def world(tmp_path):
    return build_renewal_authority_world(tmp_path)


class FileKeyring:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(exist_ok=True)

    def path(self, service, account):
        return self.directory / hashlib.sha256((service + account).encode()).hexdigest()

    def get_password(self, service, account):
        path = self.path(service, account)
        return path.read_text() if path.exists() else None

    def set_password(self, service, account, value):
        path = self.path(service, account)
        temporary = path.with_suffix('.pending')
        temporary.write_text(value)
        temporary.replace(path)

    def delete_password(self, service, account):
        self.path(service, account).unlink(missing_ok=True)


def persistent(tmp_path, policy):
    local = tmp_path / 'durable'
    backend = FileKeyring(tmp_path / 'fake-native')
    authority = Authority(local, BINDING, credentials=CredentialStore(local=local, backend=backend), now=lambda: NOW, checkout=tmp_path / 'repo')
    authority.enroll(policy, expected_digest=digest(policy), reviewed=True, validate_base=lambda _: None,
                     evaluator=EVALUATOR)
    return authority


@pytest.mark.skipif(os.name == 'nt', reason='SIGKILL crash test; Windows CI exercises publication/readback separately')
@pytest.mark.parametrize('boundary', ['bundle', 'intent', 'publication'])
def test_AR09_real_sigkill_at_each_publication_effect(tmp_path, world, boundary):
    _, policy, _ = world
    authority = persistent(tmp_path, policy)
    approval = approve(authority)
    code = '''
import os, signal, sys
from pathlib import Path
from catchup_core.renewal_authority import Authority
from lib.credentials import CredentialStore
from test_renewal_process import FileKeyring
from test_renewal_authority import BINDING, NOW
local = Path(sys.argv[1])
a = Authority(local, BINDING, credentials=CredentialStore(local=local, backend=FileKeyring(sys.argv[2])), now=lambda: NOW, checkout=local.parent / 'repo',
 observe=lambda event: os.kill(os.getpid(), signal.SIGKILL) if event == sys.argv[4] else None)
a.activate(sys.argv[3], {'tree': 'c' * 40}, verify=lambda *_: None)
'''
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(Path(__file__).parents[2] / 'scripts'),
                                                     str(Path(__file__).parent)))}
    child = subprocess.run([sys.executable, '-c', code, str(authority.local), str(tmp_path / 'fake-native'),
                            approval, boundary], env=env, capture_output=True, timeout=15)
    assert child.returncode == -9, child.stderr.decode()
    if boundary != 'publication':
        with pytest.raises(RenewalError):
            authority.validate(target(), validate_base=lambda _: None)
    authority.recover(verify=lambda *_: None)
    if boundary == 'bundle':
        assert authority.active() is None
    else:
        assert authority.validate(target(), validate_base=lambda _: None)['approval'] == approval


@pytest.mark.skipif(os.name == 'nt', reason='POSIX pipe synchronization; native locks are separately covered on Windows')
def test_AR10_real_process_publication_has_one_cooperating_writer(tmp_path, world):
    import select
    _, policy, _ = world
    authority = persistent(tmp_path, policy)
    approval = approve(authority)
    code = '''
import sys
from pathlib import Path
from catchup_core.renewal_authority import Authority
from lib.credentials import CredentialStore
from test_renewal_process import FileKeyring
from test_renewal_authority import BINDING, NOW
def pause(event):
 if event == 'intent':
  print('intent', flush=True)
  sys.stdin.readline()
local = Path(sys.argv[1])
a = Authority(local, BINDING, credentials=CredentialStore(local=local, backend=FileKeyring(sys.argv[2])), now=lambda: NOW, checkout=local.parent / 'repo', observe=pause)
a.activate(sys.argv[3], {'tree': 'c' * 40}, verify=lambda *_: None)
'''
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(Path(__file__).parents[2] / 'scripts'),
                                                     str(Path(__file__).parent)))}
    child = subprocess.Popen([sys.executable, '-c', code, str(authority.local), str(tmp_path / 'fake-native'), approval],
                              env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == 'intent'
        with pytest.raises(RenewalError, match='busy'):
            authority.activate(approval, {'tree': 'c' * 40}, verify=lambda *_: None)
        _, error = child.communicate('continue\n', timeout=10)
        assert child.returncode == 0, error
        assert authority._state()[0]['serial'] == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX pipe synchronization; publication locks have Windows coverage')
def test_AR10_second_delivery_cannot_overwrite_a_pushed_receipt(tmp_path, world):
    import select
    from catchup_core.renewal_delivery import Delivery
    _, policy, _ = world
    authority = persistent(tmp_path, policy)
    approval = approve(authority)
    code = '''
import sys
from pathlib import Path
from catchup_core.renewal_authority import Authority
from catchup_core.renewal_delivery import Delivery
from lib.credentials import CredentialStore
from test_renewal_process import FileKeyring
from test_renewal_authority import BINDING, NOW, target
from test_renewal_delivery import Workspace
local = Path(sys.argv[1])
a = Authority(local, BINDING, credentials=CredentialStore(local=local, backend=FileKeyring(sys.argv[2])), now=lambda: NOW, checkout=local.parent / 'repo')
def ci(*args):
 print('pushed', flush=True)
 sys.stdin.readline()
 return {'status': 'pending', 'requests': 3}
Delivery(a, Workspace(), snapshot=target, gates=lambda: None, ci=ci, validate_base=lambda _: None).run(sys.argv[3], message='synthetic')
'''
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(Path(__file__).parents[2] / 'scripts'), str(Path(__file__).parent)))}
    child = subprocess.Popen([sys.executable, '-c', code, str(authority.local), str(tmp_path / 'fake-native'), approval],
                             env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == 'pushed'
        assert authority.progress()['pushed'] is True
        second = Workspace()
        with pytest.raises(RenewalError):
            Delivery(authority, second, snapshot=target, gates=lambda: None, ci=lambda *_: {},
                     validate_base=lambda _: None).run(approval, message='synthetic')
        assert not second.effects and authority.progress()['pushed'] is True
        _, error = child.communicate('continue\n', timeout=10)
        assert child.returncode == 0, error
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
