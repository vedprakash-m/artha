"""WP-06 / §10.6: the telemetry hash chain stays valid when several processes append.

Observed in the live file: 54 prev-link breaks in 14k lines, every one a valid
self-hash — no tampering, but each process kept a stale in-memory ``_prev_hash``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import telemetry  # noqa: E402


def test_stale_in_memory_hash_does_not_break_the_chain(tmp_path):
    path = tmp_path / "t.jsonl"
    telemetry.emit("a.one", _path=path)
    # another process appended in the meantime:
    other = tmp_path / "o.jsonl"
    telemetry._prev_hash = "f" * 64            # this process now holds a stale hash
    telemetry.emit("a.two", _path=path)
    telemetry.emit("a.three", _path=path)
    assert telemetry.verify_integrity(path) is True
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert lines[1]["prev_hash"] == lines[0]["entry_hash"]
    assert lines[2]["prev_hash"] == lines[1]["entry_hash"]


def test_first_entry_uses_genesis_and_survives_garbage_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("not json\n\n")
    telemetry.emit("x.first", _path=path)
    rec = json.loads(path.read_text().splitlines()[-1])
    assert rec["prev_hash"] == "0" * 64
    telemetry.emit("x.second", _path=path)
    recs = [json.loads(x) for x in path.read_text().splitlines() if x.startswith("{")]
    assert recs[1]["prev_hash"] == recs[0]["entry_hash"]
    # the strict verifier still reports the corrupt line, which is the right policy
    assert telemetry.verify_integrity(path) is False


@pytest.mark.parametrize("attempt", range(3))
def test_parallel_processes_keep_one_valid_chain(tmp_path, attempt):
    path = tmp_path / "t.jsonl"
    code = (
        "import sys; sys.path.insert(0, %r); from lib import telemetry; from pathlib import Path; "
        "p = Path(%r); "
        "[telemetry.emit('p.' + sys.argv[1] + '.' + str(i), _path=p) for i in range(40)]"
        % (str(SCRIPTS), str(path))
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(worker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for worker in range(4)
    ]
    for p in procs:
        stdout, stderr = p.communicate(timeout=60)
        assert p.returncode == 0, (stdout, stderr)
        assert not stderr, stderr
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 160
    assert {record["event"] for record in records} == {
        f"p.{worker}.{i}" for worker in range(4) for i in range(40)
    }
    assert telemetry.verify_integrity(path) is True


def test_large_file_tail_read_finds_last_hash(tmp_path):
    path = tmp_path / "t.jsonl"
    for i in range(300):
        telemetry.emit(f"bulk.{i}", _path=path, extra={"pad": "x" * 400})
    assert telemetry._read_last_hash(path) == json.loads(path.read_text().splitlines()[-1])["entry_hash"]


def test_threads_keep_every_record_and_one_valid_chain(tmp_path):
    path = tmp_path / "threads.jsonl"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: telemetry.emit(f"thread.{i}", _path=path), range(160)))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 160
    assert {record["event"] for record in records} == {f"thread.{i}" for i in range(160)}
    assert telemetry.verify_integrity(path) is True


def test_cached_writer_reopens_after_rotation_and_deletion(tmp_path):
    path = tmp_path / "nested" / "t.jsonl"
    rotated = tmp_path / "nested" / "old.jsonl"
    telemetry.emit("before.rotation", _path=path)
    path.rename(rotated)
    telemetry.emit("after.rotation", _path=path)
    assert json.loads(rotated.read_text())["event"] == "before.rotation"
    assert json.loads(path.read_text())["event"] == "after.rotation"
    assert telemetry.verify_integrity(rotated) is True
    assert telemetry.verify_integrity(path) is True
    path.unlink()
    telemetry.emit("after.deletion", _path=path)
    assert json.loads(path.read_text())["event"] == "after.deletion"
    assert telemetry.verify_integrity(path) is True


def test_switching_targets_closes_previous_handle(tmp_path):
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    telemetry.emit("first", _path=first)
    previous = telemetry._writer
    telemetry.emit("second", _path=second)
    assert previous.closed
    telemetry.emit("first.again", _path=first)
    assert telemetry.verify_integrity(first) is True
    assert telemetry.verify_integrity(second) is True
    assert len(first.read_text().splitlines()) == 2


def test_held_lock_blocks_another_process_until_release(tmp_path):
    path = tmp_path / "held.jsonl"
    setup = (
        f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); "
        "from lib import telemetry; from pathlib import Path; "
        f"p = Path({str(path)!r}); "
    )
    holder_code = (
        setup + "telemetry.emit('before', _path=p); telemetry._lock(telemetry._writer); "
        "print('locked', flush=True); input(); telemetry._unlock(telemetry._writer)"
    )
    contender_code = (
        setup + "print('started', flush=True); telemetry.emit('after', _path=p)"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    contender = None
    try:
        assert holder.stdout.readline().strip() == "locked"
        contender = subprocess.Popen(
            [sys.executable, "-c", contender_code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert contender.stdout.readline().strip() == "started"
        with pytest.raises(subprocess.TimeoutExpired):
            contender.wait(timeout=0.2)
        stdout, stderr = holder.communicate(input="\n", timeout=10)
        assert holder.returncode == 0, (stdout, stderr)
        assert not stderr, stderr
        stdout, stderr = contender.communicate(timeout=10)
        assert contender.returncode == 0, (stdout, stderr)
        assert not stderr, stderr
    finally:
        for process in (holder, contender):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()
    assert [json.loads(line)["event"] for line in path.read_text().splitlines()] == ["before", "after"]
    assert telemetry.verify_integrity(path) is True


def test_failed_lock_never_appends_unlocked_and_next_emit_recovers(tmp_path, monkeypatch, capsys):
    path = tmp_path / "t.jsonl"
    telemetry.emit("before", _path=path)
    previous = telemetry._writer

    def fail_lock(fh):
        raise OSError("lock unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(telemetry, "_lock", fail_lock)
        telemetry.emit("must.not.append", _path=path)
    assert "lock unavailable" in capsys.readouterr().err
    assert previous.closed
    assert len(path.read_text().splitlines()) == 1
    telemetry.emit("after", _path=path)
    assert len(path.read_text().splitlines()) == 2
    assert telemetry.verify_integrity(path) is True


def test_windows_lock_retries_contention_but_not_other_errors(tmp_path, monkeypatch):
    import errno
    from types import SimpleNamespace

    path = tmp_path / "t.jsonl"
    telemetry.emit("before", _path=path)
    fh = telemetry._writer
    attempts = []

    def contend_once(fd, mode, size):
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        attempts.append(mode)
        if len(attempts) == 1:
            raise OSError(errno.EACCES, "contended")

    msvcrt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=contend_once)
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        patch.setitem(sys.modules, "msvcrt", msvcrt)
        telemetry._lock(fh)
        telemetry._unlock(fh)
    assert attempts == [msvcrt.LK_NBLCK, msvcrt.LK_NBLCK, msvcrt.LK_UNLCK]

    def invalid_lock(fd, mode, size):
        raise OSError(errno.EBADF, "invalid handle")

    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        patch.setitem(sys.modules, "msvcrt", msvcrt)
        patch.setattr(msvcrt, "locking", invalid_lock)
        with pytest.raises(OSError, match="invalid handle"):
            telemetry._lock(fh)


def test_tail_read_expands_past_garbage_and_keeps_unicode_hash(tmp_path):
    path = tmp_path / "tail.jsonl"
    telemetry.emit("unicode.\u2603", _path=path, extra={"pad": "\u00e9" * 70_000})
    expected = json.loads(path.read_text(encoding="utf-8"))["entry_hash"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n" * 10_000 + "[]\n" + '{"entry_hash": "short"}\n')
    assert telemetry._read_last_hash(path) == expected
    assert telemetry._read_last_hash(tmp_path / "missing.jsonl") == telemetry._GENESIS_HASH


@pytest.mark.parametrize("fail_conversion", [False, True])
def test_windows_open_errors_close_native_handles(tmp_path, monkeypatch, fail_conversion):
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    path = tmp_path / "failed.jsonl"
    closed = []

    def create(*args):
        return 123 if fail_conversion else wintypes.HANDLE(-1).value

    def close(handle):
        closed.append(handle)
        return True

    def convert(handle, flags):
        assert handle == 123
        assert flags == os.O_RDWR | os.O_BINARY
        raise OSError("conversion rejected")

    kernel = SimpleNamespace(CreateFileW=create, CloseHandle=close)
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        patch.setattr(os, "O_BINARY", getattr(os, "O_BINARY", 0x8000), raising=False)
        patch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
        patch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
        patch.setattr(ctypes, "WinError", lambda code: OSError("open rejected"), raising=False)
        patch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=convert))
        with pytest.raises(OSError, match="conversion rejected" if fail_conversion else "open rejected"):
            telemetry._open_writer(path)
    assert closed == ([123] if fail_conversion else [])
    assert telemetry._writer is None
    assert not path.exists()


def test_posix_writer_keeps_flock_locking_and_fork_reset(tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = tmp_path / "posix.jsonl"
    calls = []
    fcntl = SimpleNamespace(LOCK_EX=2, LOCK_UN=8, flock=lambda fd, mode: calls.append((fd, mode)))
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "posix")
        patch.setitem(sys.modules, "fcntl", fcntl)
        fh = telemetry._open_writer(path)
        fd = fh.fileno()
        telemetry._lock(fh)
        telemetry._unlock(fh)
        previous_lock = telemetry._writer_lock
        telemetry._after_fork()
    assert calls == [(fd, 2), (fd, 8)]
    assert fh.closed
    assert telemetry._writer is None
    assert telemetry._writer_lock is not previous_lock
