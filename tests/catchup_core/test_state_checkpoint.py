"""WP-18 acceptance: complete, fail-closed, restorable checkpoints (specs/features/wp-18-state-checkpoint.md)."""
from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from catchup_core import checkpoint as ck
from catchup_core import crypto
from catchup_core.session_lock import session_lock

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PASS = "correct horse battery staple"
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"k" * 32}))


def test_explicit_database_file_root_captures_wal_without_sibling_data(tmp_path):
    directory = tmp_path / 'external-store'
    directory.mkdir()
    with closing(sqlite3.connect(directory / 'actions.db')) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE audit(value)')
        db.execute('INSERT INTO audit VALUES(7)')
        db.commit()
        (directory / 'unrelated-private-file').write_text('outside installation scope')
        path = ck.create('personal', {'actions': directory / 'actions.db'}, tmp_path / 'out',
            CIPHER, {}, PASS, now=NOW, lock_path=tmp_path / 'lock')
    manifest = ck.verify(path, CIPHER)
    assert [row['path'] for row in manifest['entries']] == ['actions/actions.db']
    assert manifest['entries'][0]['kind'] == 'sqlite'
    ck.restore(path, tmp_path / 'restored', CIPHER, [directory / 'actions.db'], replay_privacy=lambda _: None)
    with closing(sqlite3.connect(tmp_path / 'restored/actions/actions.quarantined.db')) as db:
        assert db.execute('SELECT value FROM audit').fetchone() == (7,)


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    (repo / "state" / "nested").mkdir(parents=True)
    (repo / "state" / "goals.md").write_text("goals")
    (repo / "state" / "nested" / "notes.weird").write_text("odd extension")
    (repo / "state" / ".hidden").write_text("dotfile")
    (repo / "config").mkdir()
    (repo / "config" / "a.yaml").write_text("cutover: false")
    local = tmp_path / "local"
    local.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        local.chmod(0o700)
    db = sqlite3.connect(local / "ledger.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t(x)")
    db.execute("INSERT INTO t VALUES (1)")
    db.commit()
    keep = sqlite3.connect(local / "ledger.db")   # leaves committed rows in the WAL
    keep.execute("INSERT INTO t VALUES (2)")
    keep.commit()
    out = tmp_path / "out"
    roots = {"repo": repo, "local": local}

    def make(**kw):
        return ck.create("personal", roots, out, CIPHER, {"envelope": b"secret-key"}, PASS, now=NOW,
                         lock_path=tmp_path / "ck.lock", **kw)
    yield roots, out, make, tmp_path
    db.close()
    keep.close()


def test_every_file_is_captured_and_restores_byte_equal(world):                       # AC1
    roots, _, make, tmp = world
    path = make()
    manifest = ck.verify(path, CIPHER)
    names = {e["path"] for e in manifest["entries"]}
    assert {"repo/state/nested/notes.weird", "repo/state/.hidden", "repo/config/a.yaml"} <= names
    seen = []
    report = ck.restore(path, tmp / "restored", CIPHER, list(roots.values()), replay_privacy=seen.append)
    assert (tmp / "restored" / "repo" / "state" / "nested" / "notes.weird").read_text() == "odd extension"
    assert report["files"] == len(names) and seen == [tmp / "restored"]


def test_wal_committed_rows_survive(world):                                           # AC4
    roots, _, make, tmp = world
    ck.restore(make(), tmp / "r", CIPHER, list(roots.values()), replay_privacy=lambda _: None)
    rows = sqlite3.connect(tmp / "r" / "local" / "ledger.db").execute("SELECT x FROM t ORDER BY x").fetchall()
    assert rows == [(1,), (2,)]


@pytest.mark.parametrize("make_problem", ["symlink", "conflict", "unreadable"])
def test_unaccounted_paths_block_and_publish_nothing(world, make_problem, monkeypatch):  # AC2
    roots, out, make, tmp = world
    state = roots["repo"] / "state"
    if make_problem == "symlink":
        (state / "link").symlink_to(state / "goals.md")
    elif make_problem == "conflict":
        (state / "goals-conflicted copy 1.md").write_text("x")
    else:
        locked = state / "locked.md"
        locked.write_text("x")
        if os.name == "nt":
            original_open = Path.open

            def unreadable(path, *args, **kwargs):
                mode = args[0] if args else kwargs.get("mode", "r")
                if path == locked and mode == "rb":
                    raise PermissionError("synthetic unreadable file")
                return original_open(path, *args, **kwargs)

            monkeypatch.setattr(Path, "open", unreadable)
        else:
            locked.chmod(0)
    try:
        with pytest.raises(ck.CheckpointBlocked):
            make()
    finally:
        if make_problem == "unreadable" and os.name != "nt":
            (state / "locked.md").chmod(0o600)
    assert not out.exists() or not list(out.iterdir())


def _patch_stream(monkeypatch, after):
    real = ck._stream_member
    state = {"n": 0}

    def wrapped(writer, key, path, source, mode):
        if state["n"] == 0:
            after()
        state["n"] += 1
        return real(writer, key, path, source, mode)
    monkeypatch.setattr(ck, "_stream_member", wrapped)


def test_change_before_a_member_is_read_aborts(world, monkeypatch):                   # AC3
    roots, out, make, _ = world
    _patch_stream(monkeypatch, lambda: (roots["repo"] / "state" / "goals.md").write_text("changed elsewhere"))
    with pytest.raises(ck.CheckpointBlocked, match="changed during capture"):
        make()
    assert not out.exists() or not list(out.iterdir())


def test_change_after_a_member_was_read_aborts(world, monkeypatch):                   # AC3
    roots, out, make, _ = world
    real = ck._stream_member
    done = []

    def wrapped(writer, key, path, source, mode):
        entry = real(writer, key, path, source, mode)
        if key == "repo/config/a.yaml" and not done:
            done.append(1)
            path.write_text("cutover: true!")     # different size: caught by the final walk
        return entry
    monkeypatch.setattr(ck, "_stream_member", wrapped)
    with pytest.raises(ck.CheckpointBlocked, match="changed during capture"):
        make()
    assert not out.exists() or not list(out.iterdir())


def test_unreadable_directory_cannot_disappear_from_inventory(world, monkeypatch):   # AC2
    roots, out, make, _ = world
    directory = roots['repo'] / 'private'
    directory.mkdir()
    (directory / 'record').write_text('synthetic private record')

    original_scandir = os.scandir

    def unreadable(path):
        if Path(path) == directory:
            raise PermissionError("synthetic unreadable directory")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", unreadable)
    with pytest.raises(ck.CheckpointBlocked, match='inventory incomplete'):
        make()
    assert not out.exists() or not list(out.iterdir())


def test_database_change_after_member_capture_blocks_publication(world, monkeypatch):  # AC3
    roots, out, make, _ = world
    original = ck._stream_member

    def stream(writer, key, path, source, mode):
        result = original(writer, key, path, source, mode)
        if key == 'local/ledger.db':
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO t VALUES(3)')
        return result

    monkeypatch.setattr(ck, '_stream_member', stream)
    with pytest.raises(ck.CheckpointBlocked, match='state changed during capture'):
        make()
    assert not out.exists() or not list(out.iterdir())


def test_same_size_edit_with_preserved_mtime_is_not_a_stable_generation(world, monkeypatch):  # AC3
    _, out, make, _ = world
    original = ck._stream_member

    def stream(writer, key, path, source, mode):
        result = original(writer, key, path, source, mode)
        if key == 'repo/state/goals.md':
            before = path.stat()
            path.write_text('other')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(ck, '_stream_member', stream)
    with pytest.raises(ck.CheckpointBlocked, match='state changed during capture'):
        make()
    assert not out.exists() or not list(out.iterdir())


def test_foreign_key_violation_cannot_be_published_as_a_verified_database(world):
    roots, out, make, _ = world
    with sqlite3.connect(roots['local'] / 'broken.db') as db:
        db.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE children(parent INTEGER REFERENCES parents(id))')
        db.execute('INSERT INTO children VALUES(123)')
    with pytest.raises(ck.CheckpointBlocked, match='inventory incomplete'):
        make()
    assert not out.exists()


def test_killed_capture_leaves_no_published_artifact_and_next_start_reports_the_orphan(world):
    import subprocess
    import time
    roots, out, make, tmp = world
    marker = tmp / 'captured'
    script = '''
import sys, time
from pathlib import Path
from datetime import datetime, UTC
sys.path.insert(0, sys.argv[1])
from catchup_core import checkpoint as ck
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
def pause():
    Path(sys.argv[6]).touch()
    while True: time.sleep(.01)
ck.create('personal', {'repo': Path(sys.argv[2]), 'local': Path(sys.argv[3])}, Path(sys.argv[4]),
    EnvelopeCipher(StaticKeyProvider({1: b'k'*32})), {}, 'correct horse battery staple',
    now=datetime.now(UTC), lock_path=Path(sys.argv[5]), before_publish=pause)
'''
    with subprocess.Popen([sys.executable, '-c', script, str(Path(ck.__file__).parents[1]), str(roots['repo']),
                           str(roots['local']), str(out), str(tmp / 'ck.lock'), str(marker)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            assert marker.exists(), child.communicate(timeout=1)
        finally:
            child.kill()
            child.communicate(timeout=5)
    assert not list(out.glob('*.ack')) and list(out.glob('.*.part'))
    with pytest.raises(ck.CheckpointBlocked, match='interrupted checkpoint'):
        make()
    assert list(out.glob('.*.part'))  # Recovery evidence is not silently discarded.


def test_busy_installation_blocks(world):
    with pytest.raises(ck.CheckpointBlocked, match="not quiesced"):
        world[2](quiesce=lambda: ["an action is EXECUTING"])


def test_crash_before_publish_leaves_nothing(world):                                  # AC5
    _, out, make, _ = world

    def die():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        make(before_publish=die)
    assert not out.exists() or not list(out.iterdir())


def test_generations_are_write_once_and_destination_outside_tree(world):
    roots, _, make, tmp = world
    first = make()
    assert first.exists()
    with pytest.raises(ck.CheckpointBlocked, match="inside"):
        ck.create("personal", roots, roots["repo"] / "state", CIPHER, {}, PASS, now=NOW, lock_path=tmp / "l2")


def test_copy_to_requires_other_device_unless_cloud_synced(world, monkeypatch):       # AC6
    roots, _, make, tmp = world
    path = make()
    with pytest.raises(ck.CheckpointBlocked, match="same device"):
        ck.copy_to(path, tmp / "same-disk", list(roots.values()))
    copied = ck.copy_to(path, tmp / "onedrive", list(roots.values()), cloud_synced=True)
    assert copied.read_bytes() == path.read_bytes()
    with pytest.raises(ck.CheckpointBlocked, match="write-once"):
        ck.copy_to(path, tmp / "onedrive", list(roots.values()), cloud_synced=True)
    with pytest.raises(ck.CheckpointBlocked, match="inside"):
        ck.copy_to(path, roots["repo"] / "state" / "x", list(roots.values()), cloud_synced=True)


def test_restore_refuses_live_nonempty_and_missing_privacy_replay(world):             # AC7/AC8
    roots, _, make, tmp = world
    path = make()
    live = list(roots.values())
    with pytest.raises(ck.CheckpointBlocked, match="live"):
        ck.restore(path, roots["repo"] / "state" / "again", CIPHER, live, replay_privacy=lambda _: None)
    busy = tmp / "busy"
    busy.mkdir()
    (busy / "x").write_text("x")
    with pytest.raises(ck.CheckpointBlocked, match="new or empty"):
        ck.restore(path, busy, CIPHER, live, replay_privacy=lambda _: None)
    with pytest.raises(ck.CheckpointBlocked, match="privacy"):
        ck.restore(path, tmp / "fresh", CIPHER, live)


def test_privacy_replay_failure_is_not_a_success(world):                              # AC7
    roots, _, make, tmp = world

    def fail(_):
        raise RuntimeError("privacy history missing")
    with pytest.raises(RuntimeError):
        ck.restore(make(), tmp / "r", CIPHER, list(roots.values()), replay_privacy=fail)


def test_escrow_recovers_only_with_the_passphrase(world):                             # AC9
    path = world[2]()
    assert ck.read_escrow(path, PASS) == {"envelope": b"secret-key"}
    with pytest.raises(ck.CheckpointError):
        ck.read_escrow(path, "a different passphrase!!")
    assert b"secret-key" not in path.read_bytes()


@pytest.mark.parametrize("weak", ["short", "aaaaaaaaaaaaaaaa", ""])
def test_weak_passphrase_is_refused(world, weak):
    roots, out, _, tmp = world
    with pytest.raises(ck.CheckpointBlocked, match="passphrase"):
        ck.create("personal", roots, out, CIPHER, {}, weak, now=NOW, lock_path=tmp / "l")


def test_tampering_truncation_and_trailing_data_are_detected(world):
    path = world[2]()
    blob = path.read_bytes()
    for mutated in (blob[:-1], blob[:-40], blob + b"x", blob[:200] + bytes([blob[200] ^ 1]) + blob[201:],
                    b"nope", b"ACK2\xff\xff", b"ACK2\x00\x08ck_12345\xff\xff\xff\xffxx"):
        path.write_bytes(mutated)
        with pytest.raises(ck.CheckpointError):
            ck.verify(path, CIPHER)


def test_diff_lists_exactly_post_checkpoint_changes(world):                           # AC10
    roots, _, make, _ = world
    path = make()
    (roots["repo"] / "state" / "goals.md").write_text("corrected")
    (roots["repo"] / "state" / "new.md").write_text("new evidence")
    (roots["repo"] / "config" / "a.yaml").unlink()
    result = ck.diff(path, CIPHER, roots)
    assert result["changed"] == ["repo/state/goals.md"]
    assert result["added"] == ["repo/state/new.md"]
    assert result["removed"] == ["repo/config/a.yaml"]


def test_restore_marks_actions_quarantined_and_effects_disabled(world):               # AC11
    roots, _, make, tmp = world
    report = ck.restore(make(), tmp / "r", CIPHER, list(roots.values()), replay_privacy=lambda _: None)
    assert report["marker"]["actions"] == "quarantined" and report["marker"]["effects"] == "disabled"


def test_restore_quarantines_real_action_database_and_configuration(world):           # AC11
    import json
    roots, _, make, tmp = world
    with sqlite3.connect(roots['local'] / 'actions.db') as db:
        db.execute('CREATE TABLE actions(id TEXT, status TEXT)')
        db.execute("INSERT INTO actions VALUES('previously-approved','approved')")
    (roots['repo'] / 'config/catchup_core.yaml').write_text('cutover: true\nsources: {}\n')
    dest = tmp / 'restored'
    report = ck.restore(make(), dest, CIPHER, list(roots.values()), replay_privacy=lambda _: None)
    assert not (dest / 'local/actions.db').exists()
    assert (dest / 'local/actions.quarantined.db').is_file()
    assert (dest / 'repo/config/actions.paused').is_file()
    import yaml
    assert yaml.safe_load((dest / 'repo/config/catchup_core.yaml').read_text())['cutover'] is False
    assert report['transformed']
    assert json.loads((dest / '.artha-restored.json').read_text())['effects'] == 'disabled'


def test_checkpoint_hides_plaintext_and_keys(world):                                  # AC12
    roots, _, make, _ = world
    blob = make().read_bytes()
    for needle in (b"goals", b"odd extension", b"cutover", b"secret-key", b"b" * 32):
        assert needle not in blob


def test_excluded_paths_are_recorded_with_reasons_and_caches_skipped(world):
    roots, _, make, _ = world
    (roots["repo"] / "__pycache__").mkdir()
    (roots["repo"] / "__pycache__" / "m.pyc").write_bytes(b"x")
    (roots["repo"] / "tmp").mkdir()
    (roots["repo"] / "tmp" / "scratch").write_text("x")
    manifest = ck.verify(make(exclude={"repo/tmp": "reviewed: reproducible output"}), CIPHER)
    assert manifest["excluded"]["repo/__pycache__"] and manifest["excluded"]["repo/tmp"].startswith("reviewed")
    assert all(not e["path"].startswith(("repo/tmp", "repo/__pycache__")) for e in manifest["entries"])


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range session lock")
def test_active_windows_lock_file_is_captured_from_its_owning_handle(world):
    roots, _, make, _ = world
    active_lock = roots["local"] / "installation.lock"
    ordinary_lock = roots["local"] / "ordinary.lock"
    ordinary_lock.write_bytes(b"ordinary data")
    with session_lock(active_lock):
        script = '''import sys
from pathlib import Path
from catchup_core.session_lock import SessionBusy, session_lock
try:
    with session_lock(Path(sys.argv[1])):
        sys.exit(1)
except SessionBusy:
    sys.exit(23)
'''
        child = subprocess.run(
            [sys.executable, "-c", script, str(active_lock)],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "scripts")},
            capture_output=True,
            timeout=10,
        )
        assert child.returncode == 23, child.stderr.decode(errors="replace")
        manifest = ck.verify(make(), CIPHER)
    assert active_lock.read_bytes() == b"\0"
    entries = {entry["path"]: entry for entry in manifest["entries"]}
    assert entries["local/installation.lock"]["size"] == 1
    assert "local/ordinary.lock" in entries


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range session lock")
def test_active_windows_lock_on_payload_still_blocks_checkpoint(world):
    roots, out, make, _ = world
    payload = roots["local"] / "payload.db"
    payload.write_bytes(b"not a lock file")
    with session_lock(payload):
        with pytest.raises(ck.CheckpointBlocked, match="inventory incomplete"):
            make()
    assert not out.exists() or not list(out.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range session lock")
def test_external_windows_lock_still_blocks_capture(world):
    roots, _, make, _ = world
    active_lock = roots["local"] / "installation.lock"
    script = '''import sys
from pathlib import Path
from catchup_core.session_lock import session_lock
with session_lock(Path(sys.argv[1])):
    print("ready", flush=True)
    sys.stdin.readline()
'''
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(active_lock)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "scripts")},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline() == "ready\n"
        with pytest.raises(ck.CheckpointBlocked, match="inventory incomplete"):
            make()
    finally:
        if child.poll() is None and child.stdin is not None:
            child.stdin.write("release\n")
            child.stdin.flush()
        _, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, stderr
    manifest = ck.verify(make(), CIPHER)
    assert "local/installation.lock" in {entry["path"] for entry in manifest["entries"]}


def test_unsafe_member_paths_are_rejected():
    for bad in ("/etc/passwd", "../x", "a/../b", "a\\b", "C:/x", "a//b"):
        with pytest.raises(ck.CheckpointError):
            ck._safe(bad)


def test_missing_root_and_bad_profile_block(world):
    roots, out, _, tmp = world
    with pytest.raises(ck.CheckpointBlocked, match="missing"):
        ck.create("personal", {**roots, "gone": tmp / "gone"}, out, CIPHER, {}, PASS, now=NOW, lock_path=tmp / "l")
    with pytest.raises(ck.CheckpointError, match="profile"):
        ck.create("Bad Name", roots, out, CIPHER, {}, PASS, now=NOW, lock_path=tmp / "l")


@pytest.mark.skipif(os.name == "nt", reason="uses fork-free subprocess with POSIX modes")
def test_separate_process_change_is_detected(world):                                  # AC3, real process
    import subprocess
    roots, out, _, tmp = world
    target = roots["repo"] / "state" / "goals.md"
    code = f"import pathlib; pathlib.Path({str(target)!r}).write_text('other process')"
    original = ck._stream_member
    fired = []

    def stream(*args):
        if not fired:
            fired.append(1)
            subprocess.run([sys.executable, "-c", code], check=True)
        return original(*args)
    ck._stream_member = stream
    try:
        with pytest.raises(ck.CheckpointBlocked, match="changed during capture"):
            ck.create("personal", roots, out, CIPHER, {}, PASS, now=NOW, lock_path=tmp / "l")
    finally:
        ck._stream_member = original


def test_special_files_and_links_block(world, monkeypatch):
    roots, _, make, _ = world
    state = roots["repo"] / "state"
    (state / ".DS_Store").write_text("x")                      # excluded file, not a directory
    (state / "dirlink").symlink_to(state / "nested", target_is_directory=True)
    with pytest.raises(ck.CheckpointBlocked, match="symbolic link"):
        make()
    (state / "dirlink").unlink()
    special = state / "pipe"
    if hasattr(os, "mkfifo"):
        os.mkfifo(special)
    else:
        special.write_bytes(b"synthetic special file")
        original_lstat = Path.lstat

        def fifo_metadata(path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            if path == special:
                fields = list(info)
                fields[0] = stat.S_IFIFO | stat.S_IMODE(info.st_mode)
                return os.stat_result(fields)
            return info

        monkeypatch.setattr(Path, "lstat", fifo_metadata)
    with pytest.raises(ck.CheckpointBlocked, match="not a regular file"):
        make()


def test_malformed_escrow_and_manifest_mismatch_are_errors(world, monkeypatch):
    path = world[2]()
    blob = path.read_bytes()
    generation_length = int.from_bytes(blob[4:6], "big")
    cut = 6 + generation_length
    escrow_length = int.from_bytes(blob[cut:cut + 4], "big")
    path.write_bytes(blob[:cut] + (3).to_bytes(4, "big") + b"abc" + blob[cut + 4 + escrow_length:])
    with pytest.raises(ck.CheckpointError, match="malformed"):
        ck.read_escrow(path, PASS)
    path.write_bytes(blob[:cut + 2])
    with pytest.raises(ck.CheckpointError, match="truncated"):
        ck.read_escrow(path, PASS)
    path.write_bytes(blob)
    real = ck._members

    def with_extra_entry(*args):
        for kind, header, chunks in real(*args):
            if kind == "manifest":
                header = {**header, "entries": [*header["entries"], {"path": "repo/extra"}]}
            yield kind, header, chunks
    monkeypatch.setattr(ck, "_members", with_extra_entry)
    with pytest.raises(ck.CheckpointError, match="do not match"):
        ck.verify(path, CIPHER)


def test_escrow_header_is_bounded_before_payload_allocation(tmp_path):
    import struct
    path = tmp_path / 'oversized.ack'
    path.write_bytes(b'ACK2' + struct.pack('>H', 7) + b'ck_test' + struct.pack('>I', 2**32 - 1))
    with pytest.raises(ck.CheckpointError, match='size limit'):
        ck.verify(path, CIPHER)


def test_checkpoint_authentication_also_covers_recovery_escrow(world):
    import struct
    path = world[2]()
    data = bytearray(path.read_bytes())
    generation_size = struct.unpack('>H', data[4:6])[0]
    size_at = 6 + generation_size
    escrow_size = struct.unpack('>I', data[size_at:size_at + 4])[0]
    data[size_at + 4 + escrow_size - 1] ^= 1
    path.write_bytes(data)
    with pytest.raises(ck.CheckpointError, match='escrow'):
        ck.verify(path, CIPHER)


def test_copy_never_deletes_another_processes_partial_ciphertext(world):
    roots, _, make, tmp = world
    path = make()
    dest = tmp / 'cloud'
    dest.mkdir()
    partial = dest / ('.' + path.name + '.part')
    partial.write_bytes(b'other process recovery evidence')
    with pytest.raises(FileExistsError):
        ck.copy_to(path, dest, list(roots.values()), cloud_synced=True)
    assert partial.read_bytes() == b'other process recovery evidence'


def test_member_ordering_and_headers_are_enforced(world, monkeypatch):
    path = world[2]()
    real = ck._members

    def data_first(*args):
        yield "data", {}, iter((b"x",))
        yield from real(*args)
    monkeypatch.setattr(ck, "_members", data_first)
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)
    monkeypatch.setattr(ck, "_members", lambda *_: iter([("end", {"path": "x"}, iter(()))]))
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)


def test_publication_and_copy_failures_leave_nothing(world, monkeypatch):
    roots, out, make, tmp = world
    monkeypatch.setattr(ck.secrets, "token_hex", lambda _n: "deadbeef")
    first = make()
    with pytest.raises(ck.CheckpointBlocked, match="write-once"):
        make()
    first.unlink()
    real_verify = ck.verify
    monkeypatch.setattr(ck, "verify", lambda *_: (_ for _ in ()).throw(ck.CheckpointError("bad")))
    with pytest.raises(ck.CheckpointError):
        make()
    assert not list(out.glob("*.ack")) and not list(out.glob(".*.part"))
    monkeypatch.setattr(ck, "verify", real_verify)
    path = make()
    monkeypatch.setattr(ck.shutil, "copyfileobj", lambda _s, dst, _n: dst.write(b"corrupt"))
    with pytest.raises(ck.CheckpointError, match="does not match"):
        ck.copy_to(path, tmp / "cloud", list(roots.values()), cloud_synced=True)
    assert not list((tmp / "cloud").glob("*")) 


def test_corrupt_checkpoint_restore_removes_the_partial_destination(world):
    roots, _, make, tmp = world
    path = make()
    blob = bytearray(path.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    path.write_bytes(bytes(blob))
    with pytest.raises(ck.CheckpointError):
        ck.restore(path, tmp / "r", CIPHER, list(roots.values()), replay_privacy=lambda _: None)
    assert not (tmp / "r").exists()

def _stream(tmp_path, frames):
    """A correctly encrypted stream with caller-chosen frame contents, to reach logical inconsistencies."""
    import json as _json
    import hashlib
    import struct

    path = tmp_path / "crafted.ack"
    gen = "ck_test"
    with path.open("wb") as handle:
        handle.write(b"ACK2" + struct.pack(">H", len(gen)) + gen.encode() + struct.pack(">I", 0))
        writer = ck._Writer(handle, CIPHER, gen)
        for kind, body in frames:
            if kind == 'm' and isinstance(body, dict):
                body = {'schema': 'artha.checkpoint.v2', 'generation': gen, 'profile': 'personal',
                        'excluded': {}, 'escrow_sha256': hashlib.sha256(b'').hexdigest(), **body}
            writer.frame(kind, body if isinstance(body, bytes) else _json.dumps(body).encode())
    return path


@pytest.mark.parametrize("frames,message", [
    ([("h", {"path": "a/b", "kind": "file", "mode": 384}), ("d", b"abc"),
      ("e", {"size": 3, "sha256": "0" * 64}), ("m", {"entries": [{}]}), ("z", {"members": 1})],
     "does not match the manifest"),
    ([("z", {"members": 0})], "members do not match"),
    ([("m", {"entries": []}), ("z", {"members": 0}), ], None),
    ([("h", {"path": "../escape", "kind": "file", "mode": 384}), ("z", {"members": 0})], "unsafe"),
    ([("e", {"size": 0, "sha256": ""}), ("z", {"members": 0})], "without a header"),
])
def test_crafted_inconsistent_streams_are_refused(tmp_path, frames, message):
    path = _stream(tmp_path, frames)
    if message is None:
        assert ck.verify(path, CIPHER)['entries'] == []
        return
    with pytest.raises(ck.CheckpointError, match=message):
        ck.verify(path, CIPHER)


def test_stream_without_manifest_is_refused(tmp_path, monkeypatch):
    path = _stream(tmp_path, [("m", {"entries": []}), ("z", {"members": 0})])
    monkeypatch.setattr(ck, "_members", lambda *_: iter([("terminator", {"members": 0}, iter(()))]))
    # Terminator with no manifest is a mismatch; a stream that ends with none at all has no manifest.
    with pytest.raises(ck.CheckpointError):
        ck.verify(path, CIPHER)
    monkeypatch.setattr(ck, "_members", lambda *_: iter(()))
    with pytest.raises(ck.CheckpointError, match="no manifest"):
        ck.verify(path, CIPHER)


def test_verified_member_metadata_must_equal_the_manifest_not_only_its_count(world, monkeypatch):
    path = world[2]()
    original = ck._members
    def changed(*args):
        for kind, header, chunks in original(*args):
            if kind == 'manifest':
                header = {**header, 'entries': [{**entry, 'path': entry['path'] + '-different'}
                                               for entry in header['entries']]}
            yield kind, header, chunks
    monkeypatch.setattr(ck, '_members', changed)
    with pytest.raises(ck.CheckpointError, match='manifest'):
        ck.verify(path, CIPHER)


def test_duplicate_member_cannot_overwrite_an_earlier_restored_file(tmp_path):
    import hashlib
    digest = hashlib.sha256(b'abc').hexdigest()
    member = [('h', {'path': 'repo/a', 'kind': 'file', 'mode': 384}), ('d', b'abc'),
              ('e', {'size': 3, 'sha256': digest})]
    entry = {'path': 'repo/a', 'kind': 'file', 'mode': 384, 'size': 3, 'sha256': digest,
             'source_sha256': digest}
    path = _stream(tmp_path, [*member, *member, ('m', {'entries': [entry, entry]}), ('z', {'members': 2})])
    with pytest.raises(ck.CheckpointError, match='duplicate'):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize('metadata', [[], None, 1, 'text', b'not json'])
def test_untrusted_frame_metadata_needs_an_object(tmp_path, metadata):
    path = _stream(tmp_path, [('h', metadata), ('z', {'members': 0})])
    with pytest.raises(ck.CheckpointError, match='metadata'):
        ck.verify(path, CIPHER)


@pytest.mark.parametrize('prefix', [b'ACK2\x00', b'ACK2\x00\x07ck_test\x00\x00\x00\x08bad'])
def test_interrupted_headers_are_refused_without_recovery_or_publication(tmp_path, prefix):
    path = tmp_path / 'partial.ack'
    path.write_bytes(prefix)
    with pytest.raises(ck.CheckpointError, match='truncated'):
        ck.verify(path, CIPHER)


def test_oversized_key_inventory_is_not_published(world, monkeypatch):
    monkeypatch.setattr(ck, '_MAX_ESCROW', 64)
    with pytest.raises(ck.CheckpointBlocked, match='size limit'):
        world[2]()
    assert not list(world[1].glob('*.ack'))


@pytest.mark.parametrize('problem', ['nested-member', 'invalid-mode', 'changed-end-identity', 'wrong-generation'])
def test_authenticated_but_inconsistent_stream_metadata_is_not_recovery_evidence(tmp_path, problem):
    import hashlib
    digest = hashlib.sha256(b'abc').hexdigest()
    header = {'path': 'repo/a', 'kind': 'file', 'mode': 384}
    ending = {'size': 3, 'sha256': digest}
    entry = {**header, **ending, 'source_sha256': digest}
    frames = [('h', dict(header)), ('d', b'abc'), ('e', dict(ending)),
              ('m', {'entries': [entry]}), ('z', {'members': 1})]
    if problem == 'nested-member':
        frames.insert(1, ('h', dict(header)))
    elif problem == 'invalid-mode':
        frames[0][1]['mode'] = True
    elif problem == 'changed-end-identity':
        frames[2][1]['path'] = 'repo/other'
    else:
        frames[3][1]['generation'] = 'ck_other'
    with pytest.raises(ck.CheckpointError):
        ck.verify(_stream(tmp_path, frames), CIPHER)


@pytest.mark.parametrize('problem', ['quarantine-collision', 'invalid-config'])
def test_restore_cannot_overwrite_quarantined_history_or_activate_invalid_configuration(world, problem):
    roots, _, make, tmp = world
    if problem == 'quarantine-collision':
        (roots['local'] / 'actions.db').write_bytes(b'synthetic historical action bytes')
        (roots['local'] / 'actions.quarantined.db').write_bytes(b'preserved older history')
    else:
        (roots['repo'] / 'config/catchup_core.yaml').write_text('- invalid policy shape')
    with pytest.raises(ck.CheckpointBlocked):
        ck.restore(make(), tmp / 'restored', CIPHER, list(roots.values()), replay_privacy=lambda _: None)
    assert not (tmp / 'restored').exists()


@pytest.mark.parametrize('value', [[], {'key': 1}, {'key': '!not-base64'}])
def test_authenticated_escrow_still_needs_a_valid_key_map(tmp_path, value):
    import json
    import struct
    salt, nonce = b's'*16, b'n'*12
    sealed = b'ES1' + salt + nonce + ck.AESGCM(ck._kdf(PASS, salt)).encrypt(nonce, json.dumps(value).encode(), b'ES1')
    path = tmp_path / 'invalid-key-map.ack'
    path.write_bytes(b'ACK2' + struct.pack('>H', 7) + b'ck_test' + struct.pack('>I', len(sealed)) + sealed)
    with pytest.raises(ck.CheckpointError, match='malformed'):
        ck.read_escrow(path, PASS)


def test_a_competing_writer_cannot_be_overwritten(world):
    roots, out, make, tmp = world
    import secrets as _s
    fixed = "cafef00d"
    real = _s.token_hex
    _s.token_hex = lambda _n: fixed
    try:
        def squat():
            for name in out.glob("*.ack"):
                name.unlink()
            (out / f"artha-personal-ck_20261023T120000Z_{fixed}.ack").write_bytes(b"squatter")
        with pytest.raises(ck.CheckpointBlocked, match="write-once"):
            make(before_publish=squat)
        assert (out / f"artha-personal-ck_20261023T120000Z_{fixed}.ack").read_bytes() == b"squatter"
        assert not list(out.glob(".*.part"))
    finally:
        _s.token_hex = real
