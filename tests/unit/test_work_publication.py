"""Work producer publication adapter; native identity, keys and backend are synthetic."""
from __future__ import annotations

import contextvars
import os
import threading
from contextlib import nullcontext

import pytest

from catchup_core import work_privacy
from catchup_core.session_lock import SessionBusy
from catchup_core.work_privacy import for_installation
from lib import work_publication as wp

IDENTITY = {'platform': 'win32', 'machine': 'synthetic-machine', 'user': 'synthetic-user'}


class Backend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


@pytest.fixture
def work(tmp_path, monkeypatch):
    local = tmp_path / 'local'
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(local))
    repo = tmp_path / 'repo'
    state = repo / 'state/work'
    state.mkdir(parents=True)
    (local / 'work').mkdir(parents=True)
    (state / 'work-notes.md').write_bytes(b'synthetic notes\n')
    backend = Backend()
    make = lambda: for_installation(repo, local, backend=backend, native_identity=lambda: dict(IDENTITY))
    previous = wp.set_protocol_factory(make)
    yield {'repo': repo, 'local': local, 'state': state, 'make': make, 'backend': backend}
    wp.set_protocol_factory(previous)


def enroll(work):
    return work['make']().enroll(b'k' * 32, reviewed=True, installation_id='synthetic-installation')


def never():
    pytest.fail('legacy writer must not run after enrollment')


def _capture(errors, action):
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)


def test_default_unenrolled_probe_constructs_no_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    previous = wp.set_protocol_factory(None)
    monkeypatch.setattr(work_privacy, 'for_installation', lambda *a, **k: pytest.fail('protocol constructed'))
    try:
        calls = []
        wp.publish_text(tmp_path / 'x.md', 'text', legacy=lambda: calls.append(1))
        with wp.session():
            wp.append_text(tmp_path / 'x.md', 'text', legacy=lambda: calls.append(2))
        wp.refuse_unmediated('synthetic agent')
        assert calls == [1, 2] and wp.enrolled() is False
    finally:
        wp.set_protocol_factory(previous)


def test_unenrolled_runs_legacy_without_native_reads(work):
    work['backend'].get_password = lambda *_: pytest.fail('credential read before enrollment')
    calls = []
    wp.publish_text(work['state'] / 'a.md', 'x', legacy=lambda: calls.append('write'))
    wp.update_text(work['state'] / 'a.md', lambda _: 'y', legacy=lambda: calls.append('update'))
    assert calls == ['write', 'update']
    assert not (work['state'] / 'a.md').exists()


def test_enrolled_write_append_update_delete_are_journaled(work):
    enroll(work)
    state = work['state']
    wp.publish_text(state / 'new.md', 'one\n', legacy=never)
    wp.append_text(state / 'work-notes.md', 'two\n', legacy=never)
    wp.update_text(state / 'new.md', lambda text: text + 'three\n', legacy=never)
    wp.update_text(state / 'new.md', lambda _: None, legacy=never)
    assert wp.decode((state / 'new.md').read_bytes()) == 'one\nthree\n'
    assert wp.decode((state / 'work-notes.md').read_bytes()) == 'synthetic notes\ntwo\n'
    wp.publish({state / 'new.md': None}, legacy=never)
    current = work['make']().current()
    assert current['generation'] == 5
    assert 'work_state/new.md' not in current['members']
    assert current['invalidated']['work_state/new.md'] == 5
    assert current['invalidated']['work_state/work-notes.md'] == 3


def test_enrolled_path_outside_bound_roots_is_refused(work, tmp_path):
    enroll(work)
    with pytest.raises(wp.WorkPublicationBlocked, match='outside'):
        wp.publish_text(tmp_path / 'elsewhere.md', 'x', legacy=never)
    with pytest.raises(wp.WorkPublicationBlocked, match='outside'):
        wp.publish_text(work['repo'] / 'state/other.md', 'x', legacy=never)
    assert not (tmp_path / 'elsewhere.md').exists()
    assert work['make']().current()['generation'] == 1


def test_outside_root_update_refuses_before_read_or_compute(work, tmp_path, monkeypatch):
    enroll(work)
    outside = tmp_path / 'outside.md'
    outside.write_bytes(b'secret')
    reads, computed = [], []
    real_read = wp._read
    monkeypatch.setattr(wp, '_read', lambda active, path: reads.append(path) or real_read(active, path))
    for update in (wp.update_bytes, wp.update_text):
        with pytest.raises(wp.WorkPublicationBlocked, match='outside'):
            update(outside, lambda current: computed.append(current), legacy=never)
    assert reads == [] and computed == []
    wp.update_text(work['state'] / 'work-notes.md', lambda text: computed.append(text), legacy=never)
    assert computed == ['synthetic notes\n']
    assert work['make']().current()['generation'] == 1


def test_local_work_root_and_cache_lock_binding(work):
    enroll(work)
    cache = work['local'] / 'work/cache'
    wp.publish_text(cache / 'blob.age', 'cipher', legacy=never)
    assert 'work_local/cache/blob.age' in work['make']().current()['members']
    with pytest.raises(wp.WorkPublicationBlocked, match='lock'):
        wp.publish_text(cache / 'cache.lock', '\0', legacy=never)


def test_session_rejects_stale_input_after_independent_publication(work):
    enroll(work)
    with wp.session():
        wp.publish_text(work['state'] / 'mine.md', 'first', legacy=never)
        work['make']().publish({'work_state/other.md': b'independent'})
        with pytest.raises(wp.WorkPublicationBlocked, match='stale'):
            wp.publish_text(work['state'] / 'mine.md', 'second', legacy=never)
    assert (work['state'] / 'mine.md').read_bytes() == wp.encode('first')
    assert (work['state'] / 'other.md').read_bytes() == b'independent'


def test_manual_drift_blocks_until_reviewed_reconciliation(work):
    enroll(work)
    (work['state'] / 'work-notes.md').write_bytes(b'manual edit')
    with pytest.raises(wp.WorkPublicationBlocked, match='drift'):
        wp.append_text(work['state'] / 'work-notes.md', 'more', legacy=never)
    assert (work['state'] / 'work-notes.md').read_bytes() == b'manual edit'
    work['make']().reconcile(reviewed=True)
    wp.append_text(work['state'] / 'work-notes.md', 'more', legacy=never)
    assert (work['state'] / 'work-notes.md').read_bytes() == b'manual edit' + wp.encode('more')


def test_contending_writer_is_refused_not_lost(work):
    enroll(work)
    errors = []
    with work['make']().transaction():
        thread = threading.Thread(target=lambda: _capture(errors, lambda: wp.append_text(
            work['state'] / 'work-notes.md', 'lost?', legacy=never)))
        thread.start()
        thread.join()
    assert errors and isinstance(errors[0], (SessionBusy, wp.WorkPublicationBlocked))
    assert (work['state'] / 'work-notes.md').read_bytes() == b'synthetic notes\n'


def test_refuse_unmediated_only_when_enrolled(work):
    wp.refuse_unmediated('synthetic agent')
    enroll(work)
    with pytest.raises(wp.WorkPublicationBlocked, match='synthetic agent'):
        wp.refuse_unmediated('synthetic agent')


def test_encoding_matches_text_mode_writer(tmp_path):
    path = tmp_path / 'x.md'
    path.write_text('a\nb\n', encoding='utf-8')
    assert wp.encode('a\nb\n') == path.read_bytes()
    assert wp.decode(path.read_bytes()) == 'a\nb\n'
    assert os.linesep.encode() in wp.encode('\n')

def test_protocol_errors_are_translated_with_cause(work):
    enroll(work)
    (work['state'] / 'work-notes.md').write_bytes(b'manual edit')
    with pytest.raises(wp.WorkPublicationBlocked) as raised:
        with wp.session():
            pass
    assert isinstance(raised.value.__cause__, work_privacy.WorkPrivacyBlocked)


def test_covers_only_enrolled_bound_roots(work, tmp_path):
    assert not wp.covers(work['state'] / 'a.md')
    enroll(work)
    assert wp.covers(work['state'] / 'a.md')
    assert wp.covers(work['local'] / 'work/cache/workiq.json.age')
    assert not wp.covers(work['repo'] / 'state/other.md')
    assert not wp.covers(work['local'] / 'work-staging/x')
    assert not wp.covers(tmp_path / 'elsewhere.md')


def test_staging_dir_is_outside_roots_and_enrolled_only(work):
    with pytest.raises(wp.WorkPublicationBlocked):
        wp.staging_dir()
    enroll(work)
    assert wp.staging_dir() == work['local'] / 'work-staging'
    assert not wp.covers(wp.staging_dir() / 'x')


def test_batch_publishes_one_generation_with_staged_reads(work):
    enroll(work)
    state = work['state']
    with wp.batch():
        wp.publish_text(state / 'a.md', 'one\n', legacy=never)
        wp.append_text(state / 'a.md', 'two\n', legacy=never)
        with wp.batch():
            wp.append_text(state / 'work-notes.md', 'more\n', legacy=never)
        assert not (state / 'a.md').exists()
    assert wp.decode((state / 'a.md').read_bytes()) == 'one\ntwo\n'
    assert wp.decode((state / 'work-notes.md').read_bytes()) == 'synthetic notes\nmore\n'
    assert work['make']().current()['generation'] == 2


def test_failed_batch_publishes_nothing(work):
    enroll(work)
    with pytest.raises(RuntimeError, match='synthetic'):
        with wp.batch():
            wp.publish_text(work['state'] / 'a.md', 'one', legacy=never)
            raise RuntimeError('synthetic failure')
    assert not (work['state'] / 'a.md').exists()
    assert work['make']().current()['generation'] == 1


def test_unenrolled_batch_runs_legacy_immediately(work):
    calls = []
    with wp.batch():
        wp.publish_text(work['state'] / 'a.md', 'x', legacy=lambda: calls.append('write'))
        assert calls == ['write']


def test_post_work_refresh_log_unenrolled_appends_directly(work):
    import post_work_refresh
    post_work_refresh._append_run_log({'run_id': 'a'}, work['repo'])
    log = work['state'] / 'eval/work-refresh-log.jsonl'
    assert log.read_text(encoding='utf-8') == '{"run_id": "a"}\n'


def test_post_work_refresh_log_enrolled_is_journaled(work):
    import post_work_refresh
    enroll(work)
    post_work_refresh._append_run_log({'run_id': 'a'}, work['repo'])
    post_work_refresh._append_run_log({'run_id': 'b'}, work['repo'])
    log = work['state'] / 'eval/work-refresh-log.jsonl'
    assert wp.decode(log.read_bytes()) == '{"run_id": "a"}\n{"run_id": "b"}\n'
    assert 'work_state/eval/work-refresh-log.jsonl' in work['make']().current()['members']


def test_post_work_refresh_main_refuses_unreconciled_drift(work, monkeypatch, capsys):
    import post_work_refresh
    enroll(work)
    (work['state'] / 'work-notes.md').write_bytes(b'manual edit')
    monkeypatch.setattr(post_work_refresh, '_is_enabled', lambda _: True)
    monkeypatch.setattr(post_work_refresh, 'run', lambda *a, **k: pytest.fail('refresh ran despite drift'))
    assert post_work_refresh.main(['--artha-dir', str(work['repo'])]) == 3
    assert 'drift' in capsys.readouterr().err
    assert not (work['state'] / 'eval').exists()


def test_inaccessible_enrollment_authority_refuses_without_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'local'))
    previous = wp.set_protocol_factory(None)
    monkeypatch.setattr(work_privacy, 'for_installation', lambda *a, **k: pytest.fail('protocol constructed'))
    real_lstat = os.lstat

    def denied(path, *args, **kwargs):
        if os.fspath(path).endswith('work-privacy'):
            raise PermissionError('synthetic denial')
        return real_lstat(path, *args, **kwargs)
    monkeypatch.setattr(os, 'lstat', denied)
    try:
        with pytest.raises(wp.WorkPublicationBlocked, match='cannot be inspected'):
            wp.publish_text(tmp_path / 'x.md', 'text', legacy=never)
        with pytest.raises(wp.WorkPublicationBlocked, match='cannot be inspected'):
            with wp.session():
                pytest.fail('session opened without inspecting enrollment')
    finally:
        wp.set_protocol_factory(previous)
    assert not (tmp_path / 'x.md').exists()


def _thread(target):
    errors = []
    thread = threading.Thread(target=lambda: _capture(errors, target), daemon=True)
    thread.start()
    return thread, errors


def test_concurrent_thread_session_serializes_instead_of_losing_update(work):
    enroll(work)
    notes = work['state'] / 'work-notes.md'
    a_inside, b_entered = threading.Event(), threading.Event()

    def other():
        a_inside.wait(5)
        with wp.session():
            b_entered.set()
            wp.append_text(notes, 'B\n', legacy=never)
    thread, errors = _thread(other)
    with wp.session():
        prepared = wp.decode(notes.read_bytes()) + 'A\n'
        a_inside.set()
        assert not b_entered.wait(0.5), 'second thread joined an active session'
        wp.publish_text(notes, prepared, legacy=never)
    thread.join(10)
    assert not thread.is_alive() and errors == []
    assert wp.decode(notes.read_bytes()) == 'synthetic notes\nA\nB\n'


def test_failed_batch_cannot_swallow_another_threads_write(work):
    enroll(work)
    a_inside, b_started = threading.Event(), threading.Event()

    def other():
        a_inside.wait(5)
        b_started.set()
        wp.publish_text(work['state'] / 'theirs.md', 'kept', legacy=never)
    thread, errors = _thread(other)
    with pytest.raises(RuntimeError, match='synthetic'):
        with wp.batch():
            wp.publish_text(work['state'] / 'mine.md', 'dropped', legacy=never)
            a_inside.set()
            b_started.wait(5)
            thread.join(0.5)
            assert thread.is_alive(), 'bare write did not wait for the active batch'
            raise RuntimeError('synthetic failure')
    thread.join(10)
    assert not thread.is_alive() and errors == []
    assert not (work['state'] / 'mine.md').exists()
    assert (work['state'] / 'theirs.md').read_bytes() == wp.encode('kept')
    assert work['make']().current()['generation'] == 2


def test_worker_with_copied_context_joins_session_without_deadlock(work):
    enroll(work)
    with wp.session():
        context = contextvars.copy_context()  # copied by the owner, as work_loop does at submit
        thread, errors = _thread(lambda: context.run(
            wp.append_text, work['state'] / 'work-notes.md', 'worker\n', legacy=never))
        thread.join(10)
        assert not thread.is_alive() and errors == []
        wp.append_text(work['state'] / 'work-notes.md', 'owner\n', legacy=never)
    assert wp.decode((work['state'] / 'work-notes.md').read_bytes()) == 'synthetic notes\nworker\nowner\n'


@pytest.mark.parametrize('failed', [False, True])
def test_batch_exit_serializes_with_copied_context_worker(work, monkeypatch, failed):
    enroll(work)
    notes = work['state'] / 'work-notes.md'
    owner = threading.get_ident()
    computing, exiting = threading.Event(), threading.Event()
    lock = wp._guard

    class ObservedLock:
        def __enter__(self):
            if threading.get_ident() == owner and computing.is_set():
                exiting.set()
            return lock.__enter__()

        def __exit__(self, *args):
            return lock.__exit__(*args)

    monkeypatch.setattr(wp, '_guard', ObservedLock())

    def append(current):
        computing.set()
        assert exiting.wait(5), 'owner did not attempt batch publication'
        return current + 'worker\n'

    with pytest.raises(RuntimeError, match='synthetic failure') if failed else nullcontext():
        with wp.batch():
            wp.append_text(notes, 'owner\n', legacy=never)
            context = contextvars.copy_context()
            thread, errors = _thread(lambda: context.run(wp.update_text, notes, append, legacy=never))
            assert computing.wait(5), 'worker did not begin its staged update'
            if failed:
                raise RuntimeError('synthetic failure')
    thread.join(10)
    assert not thread.is_alive() and errors == []
    expected = 'synthetic notes\n' if failed else 'synthetic notes\nowner\nworker\n'
    assert wp.decode(notes.read_bytes()) == expected
    assert work['make']().current()['generation'] == (1 if failed else 2)
