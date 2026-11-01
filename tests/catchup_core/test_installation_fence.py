"""Shared writer/exclusive capture ownership, including nested sessions and process isolation."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from catchup_core.session_lock import SessionBusy, installation_fence, require_active_installation, session_lock


def test_existing_file_input_checks_ancestor_markers_without_false_refusal(tmp_path):
    database = tmp_path / 'ledger.db'
    database.write_bytes(b'synthetic database')
    require_active_installation(database)
    (tmp_path / '.artha-restored.json').write_text('{}')
    with pytest.raises(SessionBusy, match='restored installation'):
        require_active_installation(database)


def test_dangling_restore_marker_cannot_activate_an_installation(tmp_path):
    marker = tmp_path / '.artha-restored.json'
    marker.symlink_to(tmp_path / 'missing-marker-target')
    with pytest.raises(SessionBusy, match='restored installation'):
        require_active_installation(tmp_path / 'nested' / 'store.db')
    assert marker.is_symlink()


def test_unreadable_restore_marker_metadata_cannot_activate_an_installation(tmp_path, monkeypatch):
    marker = tmp_path / '.artha-restored.json'
    original_stat, original_lstat = Path.stat, Path.lstat

    def denied(path, *args, **kwargs):
        if path == marker:
            raise PermissionError('SYNTHETIC-PRIVATE-MARKER')
        return original_stat(path, *args, **kwargs)

    def denied_lstat(path, *args, **kwargs):
        if path == marker:
            raise PermissionError('SYNTHETIC-PRIVATE-MARKER')
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', denied)
    monkeypatch.setattr(Path, 'lstat', denied_lstat)
    with pytest.raises(SessionBusy, match='restored installation') as error:
        require_active_installation(tmp_path / 'nested')
    assert 'SYNTHETIC-PRIVATE-MARKER' not in str(error.value)


def test_nested_writers_and_exclusive_owner_do_not_lock_themselves(tmp_path):
    for exclusive in (False, True):
        with installation_fence(tmp_path, exclusive=exclusive), installation_fence(tmp_path):
            with installation_fence(tmp_path):
                pass
    with installation_fence(tmp_path, exclusive=True):
        pass


@pytest.mark.skipif(os.name == 'nt', reason='POSIX creation-mode contract')
def test_new_lock_directory_is_private_under_normal_umask(tmp_path):
    previous = os.umask(0o022)
    try:
        path = tmp_path / 'private-local' / 'installation.lock'
        with session_lock(path):
            assert path.parent.stat().st_mode & 0o777 == 0o700
            assert path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(previous)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX link and mode fixture')
def test_linked_lock_target_is_not_modified_or_opened(tmp_path):
    target = tmp_path / 'untouched'
    target.write_bytes(b'synthetic target')
    target.chmod(0o644)
    linked = tmp_path / 'installation.lock'
    linked.symlink_to(target)
    with pytest.raises(SessionBusy):
        with session_lock(linked):
            pytest.fail('linked lock was opened')
    assert target.read_bytes() == b'synthetic target'
    assert target.stat().st_mode & 0o777 == 0o644


def test_shared_owner_cannot_upgrade_to_capture_during_its_write(tmp_path):
    with installation_fence(tmp_path):
        with pytest.raises(SessionBusy, match='upgrade'):
            with installation_fence(tmp_path, exclusive=True):
                pytest.fail('must not upgrade an active writer into capture')


def test_raw_capture_lock_still_blocks_a_new_writer(tmp_path):
    with session_lock(tmp_path / 'installation.lock'):
        with pytest.raises(SessionBusy):
            with installation_fence(tmp_path):
                pytest.fail('capture exclusion was bypassed')


def test_exception_releases_ownership_and_kernel_lock(tmp_path):
    with pytest.raises(ValueError):
        with installation_fence(tmp_path, exclusive=True):
            raise ValueError('synthetic')
    with session_lock(tmp_path / 'installation.lock'):
        pass


def test_competing_process_cannot_enter_during_capture(tmp_path):
    script = '''import sys
from pathlib import Path
from catchup_core.session_lock import installation_fence, SessionBusy
try:
    with installation_fence(Path(sys.argv[1])):
        sys.exit(1)
except SessionBusy:
    sys.exit(23)
'''
    with installation_fence(tmp_path, exclusive=True):
        child = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')},
            capture_output=True, timeout=10)
    assert child.returncode == 23, child.stderr.decode()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork inheritance; native Windows acceptance is separate')
def test_forked_process_cannot_inherit_logical_owner_privileges(tmp_path):
    with installation_fence(tmp_path, exclusive=True):
        child = os.fork()
        if child == 0:
            try:
                with installation_fence(tmp_path):
                    os._exit(1)
            except SessionBusy:
                os._exit(23)
            except BaseException:
                os._exit(24)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 23


_HOLD = '''import sys, time
from pathlib import Path
from catchup_core.session_lock import installation_fence
with installation_fence(Path(sys.argv[1]), exclusive=True):
    print('held', flush=True)
    sys.stdin.readline()
'''


def _capture_in_another_process(local):
    child = subprocess.Popen([sys.executable, '-c', _HOLD, str(local)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'scripts')})
    assert child.stdout.readline().strip() == b'held'
    return child


def _release(child):
    child.communicate(b'\n', timeout=10)


def test_encrypted_state_write_waits_out_a_capture_in_another_process(tmp_path, monkeypatch):
    """Given a capture in another process, when a State File is written, then the write is refused and nothing changes."""
    from catchup_core import vault as vault_module
    local = tmp_path / 'local'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setattr(vault_module.AgeVault, '_encrypt', lambda self, text: b'ciphertext')
    vault = vault_module.AgeVault(tmp_path / 'state', identity=lambda: 'k', recipient='age1x')
    child = _capture_in_another_process(local)
    try:
        with pytest.raises(SessionBusy):
            vault.write('goals', '---\ndomain: goals\n---\nbody\n')
        assert not (tmp_path / 'state/goals.md.age').exists()
    finally:
        _release(child)
    vault.write('goals', '---\ndomain: goals\n---\nbody\n')
    assert (tmp_path / 'state/goals.md.age').read_bytes() == b'ciphertext'


def test_encrypted_artifact_replacement_in_a_restored_installation_is_refused(tmp_path, monkeypatch):
    from catchup_core import vault as vault_module
    local = tmp_path / 'local'
    local.mkdir()
    (local / '.artha-restored.json').write_text('{}')
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setattr(vault_module.AgeVault, '_encrypt', lambda self, text: b'ciphertext')
    vault = vault_module.AgeVault(tmp_path / 'state', identity=lambda: 'k', recipient='age1x')
    with pytest.raises(SessionBusy, match='restored installation'):
        vault.write('goals', '---\ndomain: goals\n---\nbody\n')


def test_legacy_vault_cli_operations_are_excluded_by_capture(tmp_path, monkeypatch):
    """Given a capture in another process, when the legacy vault takes its operation lock, then it is refused."""
    import vault as legacy
    local = tmp_path / 'local'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    monkeypatch.setitem(legacy._config, 'ARTHA_DIR', tmp_path / 'artha')
    child = _capture_in_another_process(local)
    try:
        assert legacy._acquire_op_lock() is False
    finally:
        _release(child)
    assert legacy._acquire_op_lock() is True
    legacy._release_op_lock()
    assert legacy._acquire_op_lock() is True
    legacy._release_op_lock()
