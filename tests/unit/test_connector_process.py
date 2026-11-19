from __future__ import annotations

import subprocess
import sys
import time

import pytest

from connectors.bounded_process import run_bounded


def test_process_output_is_bounded_while_process_completes():
    result = run_bounded(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 10000)"],
        timeout=5,
        stdout_limit=128,
    )
    assert result.returncode == 0
    assert len(result.stdout) == 128
    assert result.stdout_truncated is True


def test_noncooperative_process_expires_with_bounded_timeout():
    started = __import__("time").monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.25,
            cleanup_grace=1.0,
        )
    assert __import__("time").monotonic() - started < 3.0


def test_normal_parent_exit_cannot_hide_a_pipe_holding_descendant():
    parent_code = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-c',"
        "'import time; time.sleep(10)'],"
        "stdout=sys.stdout,stderr=sys.stderr)"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(
            [sys.executable, "-c", parent_code],
            timeout=0.25,
            cleanup_grace=1.0,
        )
    assert time.monotonic() - started < 3.0


def test_normal_parent_exit_terminates_a_grandchild_without_pipe_output():
    child_code = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "time.sleep(10)"
    )
    parent_code = (
        "import subprocess,sys;"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(
            [sys.executable, "-c", parent_code],
            timeout=0.3,
            cleanup_grace=1.0,
        )
    assert time.monotonic() - started < 3.0
