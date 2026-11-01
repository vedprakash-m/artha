"""Real process lock compatibility and lifetime checks on synthetic paths."""
import multiprocessing
import sys
from contextlib import contextmanager

import pytest

from catchup_core.session_lock import SessionBusy, session_lock, session_lock_reader


@pytest.mark.skipif(sys.platform != 'win32', reason='native protected DACL publication')
def test_new_lock_and_parent_are_owner_private(tmp_path):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory, open_owner_private_path

    path = tmp_path / 'local' / 'nested' / 'installation.lock'
    with session_lock(path):
        with WindowsQuarantineDirectory.open(path.parent):
            pass
        with WindowsQuarantineDirectory.open(path.parent.parent):
            pass
    with open_owner_private_path(path):
        pass


def _holder(path, shared, connection, legacy=False):
    try:
        with (_legacy_lock(path) if legacy else session_lock(path, shared=shared)):
            connection.send("held")
            if connection.poll(30):
                connection.recv()
    except SessionBusy:
        connection.send("busy")
    finally:
        connection.close()


@contextmanager
def _legacy_lock(path):
    import msvcrt
    with path.open("r+b") as stream:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def held_process(path, shared, *, legacy=False):
    context = multiprocessing.get_context("spawn")
    parent, connection = context.Pipe()
    child = context.Process(target=_holder, args=(path, shared, connection, legacy))
    child.start()
    try:
        connection.close()
        assert parent.poll(15)
        assert parent.recv() == "held"
        yield child
    finally:
        if child.is_alive():
            parent.send("release")
        child.join(10)
        if child.is_alive():
            child.kill()
            child.join(10)
        parent.close()


@pytest.mark.parametrize(("holder_shared", "contender_shared", "accepted"), [
    (True, True, True), (True, False, False),
    (False, True, False), (False, False, False),
])
def test_process_shared_exclusive_compatibility(tmp_path, holder_shared, contender_shared, accepted):
    path = tmp_path / "session.lock"
    with held_process(path, holder_shared):
        if accepted:
            with session_lock(path, shared=contender_shared):
                with session_lock_reader(path) as stream:
                    assert stream.read() in (b"", b"\0")
                with pytest.raises(SessionBusy), session_lock(path):
                    pass
        else:
            with pytest.raises(SessionBusy), session_lock(path, shared=contender_shared):
                pass
    with session_lock(path):
        pass


@pytest.mark.parametrize("shared", [False, True])
def test_process_death_releases_shared_and_exclusive_locks(tmp_path, shared):
    path = tmp_path / "session.lock"
    with held_process(path, shared) as child:
        with pytest.raises(SessionBusy), session_lock(path):
            pass
        child.kill()
        child.join(10)
        assert not child.is_alive()
        with session_lock(path):
            pass


def test_owning_handle_reader_keeps_exclusive_lock_and_position(tmp_path):
    path = tmp_path / "session.lock"
    path.write_bytes(b"synthetic lock content")
    with session_lock(path):
        with session_lock_reader(path) as stream:
            assert stream.read(9) == b"synthetic"
            position = stream.tell()
            with session_lock_reader(path) as again:
                assert again.read() == b"synthetic lock content"
            assert stream.tell() == position
        with pytest.raises(SessionBusy), session_lock(path, shared=True):
            pass


def test_nested_shared_handles_remain_registered_until_the_last_release(tmp_path):
    path = tmp_path / "session.lock"
    path.write_bytes(b"synthetic")
    with session_lock(path, shared=True):
        with session_lock(path, shared=True):
            with session_lock_reader(path) as stream:
                assert stream.read() == b"synthetic"
        with session_lock_reader(path) as stream:
            assert stream.read() == b"synthetic"
        with pytest.raises(SessionBusy), session_lock(path):
            pass
    with session_lock(path):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows legacy byte lock interoperability")
@pytest.mark.parametrize("shared", [False, True])
def test_legacy_process_lock_excludes_native_shared_and_exclusive(tmp_path, shared):
    path = tmp_path / "session.lock"
    path.write_bytes(b"\0")
    with held_process(path, False, legacy=True):
        with pytest.raises(SessionBusy), session_lock(path, shared=shared):
            pass
    with session_lock(path, shared=shared):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows legacy byte lock interoperability")
@pytest.mark.parametrize("shared", [False, True])
def test_native_process_lock_excludes_legacy_exclusive(tmp_path, shared):
    path = tmp_path / "session.lock"
    path.write_bytes(b"\0")
    with held_process(path, shared):
        with pytest.raises(OSError), _legacy_lock(path):
            pass
    with _legacy_lock(path):
        pass


@pytest.mark.parametrize("kind", ["directory", "hardlink"])
def test_nonordinary_lock_refuses_without_changing_target(tmp_path, kind):
    path = tmp_path / "session.lock"
    if kind == "directory":
        path.mkdir()
    else:
        original = tmp_path / "original"
        original.write_bytes(b"unchanged")
        path.hardlink_to(original)
    with pytest.raises(SessionBusy, match="ordinary unlinked file"), session_lock(path):
        pytest.fail("unsafe lock acquired")
    if kind == "directory":
        assert path.is_dir()
        assert list(path.iterdir()) == []
    else:
        assert original.read_bytes() == path.read_bytes() == b"unchanged"
        assert path.stat().st_nlink == 2


def test_restored_marker_metadata_failure_refuses_fence(tmp_path, monkeypatch):
    from pathlib import Path

    from catchup_core.session_lock import require_active_installation

    original = Path.lstat
    marker = tmp_path / ".artha-restored.json"

    def unavailable(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("synthetic unavailable marker")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", unavailable)
    with pytest.raises(SessionBusy, match="marker metadata is unavailable"):
        require_active_installation(tmp_path)
    assert not (tmp_path / "installation.lock").exists()


def test_installation_fence_refuses_upgrade_and_releases_after_error(tmp_path):
    from catchup_core.session_lock import installation_fence

    directory = tmp_path / "local"
    with installation_fence(directory):
        with pytest.raises(SessionBusy, match="cannot upgrade"):
            with installation_fence(directory, exclusive=True):
                pytest.fail("writer upgraded to capture")
    with pytest.raises(RuntimeError, match="synthetic body failure"):
        with installation_fence(directory, exclusive=True):
            raise RuntimeError("synthetic body failure")
    with installation_fence(directory, exclusive=True):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="native owning-handle reader")
def test_reader_restores_position_after_body_failure(tmp_path):
    path = tmp_path / "session.lock"
    path.write_bytes(b"synthetic")
    with session_lock(path):
        with session_lock_reader(path) as stream:
            stream.seek(4)
            with pytest.raises(RuntimeError, match="synthetic body failure"):
                with session_lock_reader(path) as nested:
                    assert nested.read(2) == b"sy"
                    raise RuntimeError("synthetic body failure")
            assert stream.tell() == 4
    with session_lock(path):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="native owning-handle reader")
@pytest.mark.parametrize("kind", ["unavailable", "identity", "reparse", "links"])
def test_reader_refuses_changed_or_unavailable_metadata(tmp_path, monkeypatch, kind):
    from pathlib import Path
    from types import SimpleNamespace

    path = tmp_path / "session.lock"
    path.write_bytes(b"synthetic")
    original = Path.lstat
    with session_lock(path):
        metadata = original(path)

        def changed(candidate, *args, **kwargs):
            if candidate != path:
                return original(candidate, *args, **kwargs)
            if kind == "unavailable":
                raise PermissionError("synthetic unavailable metadata")
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_nlink=2 if kind == "links" else metadata.st_nlink,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino + (kind == "identity"),
                st_file_attributes=0x400 if kind == "reparse" else 0,
            )

        with monkeypatch.context() as patch:
            patch.setattr(Path, "lstat", changed)
            with pytest.raises(SessionBusy, match="metadata is unavailable|changed or is unsafe"):
                with session_lock_reader(path):
                    pytest.fail("unsafe lock reader opened")
        with session_lock_reader(path) as stream:
            assert stream.read() == b"synthetic"
    with session_lock(path):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="native secure lock descriptor lifecycle")
@pytest.mark.parametrize("failure", ["open", "metadata", "type", "fdopen"])
def test_native_lock_descriptor_failures_refuse_and_close_owned_descriptor(tmp_path, monkeypatch, failure):
    import os
    import stat
    from types import SimpleNamespace

    from catchup_core import session_lock as locks

    path = tmp_path / "session.lock"
    path.write_bytes(b"unchanged")
    descriptor = os.open(path, os.O_RDWR)
    original_fstat = os.fstat
    original_fdopen = os.fdopen
    original_close = os.close
    closed = []

    def open_lock(*args, **kwargs):
        if failure == "open":
            raise PermissionError("synthetic descriptor open failure")
        return descriptor

    def metadata(fd):
        if fd == descriptor:
            if failure == "metadata":
                raise PermissionError("synthetic descriptor metadata failure")
            if failure == "type":
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_nlink=1)
        return original_fstat(fd)

    def fdopen(fd, *args, **kwargs):
        if fd == descriptor and failure == "fdopen":
            raise OSError("synthetic stream creation failure")
        return original_fdopen(fd, *args, **kwargs)

    def close(fd):
        if fd == descriptor:
            closed.append(fd)
        original_close(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(locks, "_windows_open_lock", open_lock)
            patch.setattr(os, "fstat", metadata)
            patch.setattr(os, "fdopen", fdopen)
            patch.setattr(os, "close", close)
            expected = SessionBusy if failure in {"open", "type"} else OSError
            with pytest.raises(expected):
                with session_lock(path):
                    pytest.fail("failed descriptor admitted")
        assert closed == ([] if failure == "open" else [descriptor])
        if failure == "open":
            original_close(descriptor)
        assert path.read_bytes() == b"unchanged"
        with session_lock(path):
            pass
    finally:
        if not closed and failure != "open":
            original_close(descriptor)


@pytest.mark.skipif(sys.platform != "win32", reason="native private lock directory refusal")
def test_native_lock_directory_failure_refuses_without_creating_file(tmp_path, monkeypatch):
    from catchup_core import quarantine_fs_windows as native

    path = tmp_path / "local" / "session.lock"

    def refused(directory):
        raise native.QuarantineFilesystemError("synthetic directory refusal")

    monkeypatch.setattr(native, "ensure_private_directory", refused)
    with pytest.raises(SessionBusy, match="lock directory is unavailable or unsafe"):
        with session_lock(path):
            pytest.fail("unsafe directory admitted")
    assert not path.parent.exists()
