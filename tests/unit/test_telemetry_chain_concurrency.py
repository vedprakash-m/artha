"""WP-06 / §10.6: the telemetry hash chain stays valid when several processes append.

Observed in the live file: 54 prev-link breaks in 14k lines, every one a valid
self-hash — no tampering, but each process kept a stale in-memory ``_prev_hash``.
"""
from __future__ import annotations

import json
import subprocess
import sys
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


def test_parallel_processes_keep_one_valid_chain(tmp_path):
    path = tmp_path / "t.jsonl"
    code = (
        "import sys; sys.path.insert(0, %r); from lib import telemetry; from pathlib import Path; "
        "p = Path(%r); [telemetry.emit('p.%%s' %% i, _path=p) for i in range(40)]" % (str(SCRIPTS), str(path))
    )
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(4)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    assert len(path.read_text().splitlines()) == 160
    assert telemetry.verify_integrity(path) is True


def test_large_file_tail_read_finds_last_hash(tmp_path):
    path = tmp_path / "t.jsonl"
    for i in range(300):
        telemetry.emit(f"bulk.{i}", _path=path, extra={"pad": "x" * 400})
    assert telemetry._read_last_hash(path) == json.loads(path.read_text().splitlines()[-1])["entry_hash"]
