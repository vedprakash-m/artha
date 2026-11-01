"""Synthetic namespace allocation tests; no host credentials or provider requests."""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger, SignalInput
from catchup_core.resources import (
    AdmissionStore, FileSpendSeal, MemorySpendSeal, PolicyRequired, ResourceLimit, ResourcePolicy,
)


NOW = datetime(2026, 10, 24, tzinfo=UTC)
LIMITS = dict(max_requests_per_bundle=2, max_prompt_bytes_per_bundle=100,
              max_requests_per_catchup=3, max_requests_per_month=4,
              max_attempts_per_signal=2, max_pending_signals=2, max_priority_streak=2)


def opened(path):
    return Ledger(path, id_key=HmacKey(1, b'i' * 32),
                  cipher=EnvelopeCipher(StaticKeyProvider({1: b'k' * 32}))).open()


def signal(ledger, name, connector='mail', account='one', observed='2026-10-01T00:00:00Z'):
    ident = ledger.upsert_signal(SignalInput(connector, account, name, '1', observed,
                                           name, 'synthetic-blob'))[0]
    ledger.conn.execute("UPDATE signals SET processing_state='normalized' WHERE signal_id=?", (ident,))
    return ident


@pytest.fixture
def world(tmp_path):
    with opened(tmp_path / 'synthetic.db') as ledger:
        ledger.migrate()
        yield ledger, AdmissionStore(ledger, ResourcePolicy.from_mapping(LIMITS))


@pytest.mark.parametrize('bad', [True, False, 0, -1, 1.0, '1', None, float('nan'), float('inf'), 2**63])
@pytest.mark.parametrize('field', list(LIMITS))
def test_policy_rejects_non_positive_non_integer_unbounded_values(field, bad):
    with pytest.raises(ValueError):
        ResourcePolicy.from_mapping({**LIMITS, field: bad})


@pytest.mark.parametrize('raw', [None, {}, [], {**LIMITS, 'extra': 1}])
def test_policy_requires_exact_mapping(raw):
    with pytest.raises((ValueError, PolicyRequired)):
        ResourcePolicy.from_mapping(raw)


def test_missing_policy_is_not_unlimited(world):
    ledger, _ = world
    with pytest.raises(PolicyRequired):
        AdmissionStore(ledger, None)


def test_durable_idempotency_and_exact_encrypted_cache(world):
    ledger, store = world
    ident = signal(ledger, 'first')
    assert store.admit('catchup', ident, 'hmac-one', 20, NOW) is True
    assert AdmissionStore(ledger, store.policy).admit('catchup', ident, 'hmac-one', 20, NOW) is False
    store.cache_output('catchup', ident, 'hmac-one', 'private result', NOW)
    assert store.cached_output('catchup', 'hmac-one') == 'private result'
    assert store.cached_output('catchup', 'changed-key') is None
    row = ledger.conn.execute('SELECT * FROM resource_cache').fetchone()
    assert 'private result' not in str(tuple(row))
    with pytest.raises(ValueError):
        store.admit('catchup', ident, 'hmac-one', 21, NOW)
    with pytest.raises(ValueError):
        store.cache_output('catchup', ident, 'hmac-one', 'replacement', NOW)


def test_wrong_signal_and_unreserved_key_cannot_cache(world):
    ledger, store = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    store.admit('catchup', first, 'key', 10, NOW)
    for ident, key in [(second, 'key'), (first, 'other'), ('missing', 'key')]:
        with pytest.raises(ValueError):
            store.cache_output('catchup', ident, key, 'output', NOW)
    with pytest.raises(ValueError):
        store.admit('catchup', second, 'key', 10, NOW)
    with pytest.raises(ValueError):
        store.admit('catchup', 'missing', 'new', 10, NOW)


def test_cancellation_denies_even_existing_cache_and_reservation(world):
    ledger, store = world
    ident = signal(ledger, 'first')
    store.admit('catchup', ident, 'key', 10, NOW)
    store.cache_output('catchup', ident, 'key', 'output', NOW)
    store.cancel('catchup', 'owner')
    store.cancel('catchup', 'owner')
    assert store.is_cancelled('catchup')
    assert not store.is_cancelled('other')
    with pytest.raises(ResourceLimit, match='cancelled'):
        store.cached_output('catchup', 'key')
    with pytest.raises(ResourceLimit, match='cancelled'):
        store.admit('catchup', ident, 'key', 10, NOW)
    with pytest.raises(ResourceLimit, match='cancelled'):
        store.cache_output('catchup', ident, 'key', 'output', NOW)


def test_bundle_bounds_catchup_attempts_and_month_are_distinct(world):
    ledger, store = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    with pytest.raises(ResourceLimit, match='prompt'):
        store.admit('catchup', first, 'large', 101, NOW)
    store.admit('catchup', first, 'one', 10, NOW)
    store.admit('catchup', first, 'two', 10, NOW)
    with pytest.raises(ResourceLimit, match='bundle request'):
        store.admit('catchup', first, 'extra', 10, NOW)
    store.admit('catchup', second, 'three', 10, NOW)
    with pytest.raises(ResourceLimit, match='catchup'):
        store.admit('catchup', second, 'four', 10, NOW)
    store.admit('other', second, 'four', 10, NOW)
    third = signal(ledger, 'third')
    with pytest.raises(ResourceLimit, match='namespace.*month'):
        store.admit('next', third, 'five', 10, NOW)
    assert store.admit('next', third, 'five', 10, datetime(2026, 11, 1, tzinfo=UTC))


def test_month_is_utc_and_allocation_is_not_machinewide(world, tmp_path):
    ledger, store = world
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_month=1))
    ident = signal(ledger, 'first')
    stamp = datetime.fromisoformat('2026-11-01T00:30:00+02:00')
    store.admit('one', ident, 'key', 1, stamp)
    with pytest.raises(ResourceLimit, match='namespace'):
        store.admit('two', ident, 'next', 1, NOW)
    with opened(tmp_path / 'separate-namespace.db') as other:
        other.migrate()
        other_id = signal(other, 'first')
        assert AdmissionStore(other, store.policy).admit('one', other_id, 'key', 1, NOW)


def test_scheduler_is_bounded_rotates_accounts_and_serves_old_unknown(world):
    ledger, store = world
    oldest = signal(ledger, 'old', 'z-source', 'z-account', '2026-09-01T00:00:00Z')
    priorities = [signal(ledger, str(index), 'mail', 'busy') for index in range(6)]
    other = signal(ledger, 'other', 'mail', 'quiet')
    batches = [store.select_pending(priorities) for _ in range(3)]
    assert all(len(batch) == 2 and all(isinstance(row, sqlite3.Row) for row in batch) for batch in batches)
    chosen = [row['signal_id'] for batch in batches for row in batch]
    assert oldest in chosen[:3]
    assert other in chosen
    assert len(set(chosen)) == len(chosen)


def test_scheduler_cursor_survives_reopen_and_skips_finished_and_exhausted(world):
    ledger, store = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    store = AdmissionStore(ledger, replace(store.policy, max_pending_signals=1))
    assert store.select_pending()[0]['signal_id'] == first
    assert AdmissionStore(ledger, store.policy).select_pending()[0]['signal_id'] == second
    ledger.conn.execute("UPDATE signals SET processing_state='normalized' WHERE signal_id=?", (second,))
    ledger.conn.execute("UPDATE signals SET processing_state='reconciled' WHERE signal_id=?", (first,))
    store.admit('one', second, 'a', 1, NOW)
    store.admit('two', second, 'b', 1, NOW)
    assert store.select_pending() == []


def test_large_signal_finishes_across_two_invocation_bundles(world):
    ledger, store = world
    ident = signal(ledger, 'twenty-chunks')
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_bundle=16,
        max_prompt_bytes_per_bundle=16, max_requests_per_catchup=20,
        max_requests_per_month=20, max_attempts_per_signal=1))
    for index in range(16):
        assert store.admit('one', ident, str(index), 1, NOW, bundle_id='metric-1')
    with pytest.raises(ResourceLimit, match='bundle request'):
        store.admit('one', ident, '16', 1, NOW, bundle_id='metric-1')
    for index in range(16, 20):
        assert AdmissionStore(ledger, store.policy).admit(
            'one', ident, str(index), 1, NOW, bundle_id='metric-2')
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 20


def test_attempts_count_distinct_catchups_not_chunks(world):
    ledger, store = world
    ident = signal(ledger, 'many-chunks')
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_bundle=10,
        max_requests_per_catchup=10, max_requests_per_month=30))
    for catchup in ('one', 'two'):
        for index in range(4):
            assert store.admit(catchup, ident, str(index), 1, NOW)
    with pytest.raises(ResourceLimit, match='attempt'):
        store.admit('three', ident, '0', 1, NOW)


def test_cached_replay_in_new_bundle_does_not_charge_any_limit(world):
    ledger, store = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_bundle=1,
        max_prompt_bytes_per_bundle=10, max_attempts_per_signal=1))
    assert store.admit('one', first, 'cached', 10, NOW, bundle_id='metric-1')
    store.cache_output('one', first, 'cached', 'output', NOW)
    assert not store.admit('one', first, 'cached', 10, NOW, bundle_id='metric-2')
    assert store.cached_output('one', 'cached') == 'output'
    assert store.admit('one', second, 'new', 10, NOW, bundle_id='metric-2')
    with pytest.raises(ResourceLimit, match='bundle request'):
        store.admit('one', first, 'another', 0, NOW, bundle_id='metric-2')
    assert ledger.conn.execute('SELECT bundle_id FROM resource_admissions WHERE request_key=?',
                               ('cached',)).fetchone()[0] == 'metric-1'
    with pytest.raises(ValueError, match='already bound'):
        store.admit('one', second, 'cached', 10, NOW, bundle_id='metric-3')
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 2


def test_bundle_bytes_are_shared_across_signals_but_scoped_to_catchup(world):
    ledger, store = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    assert store.admit('one', first, 'a', 60, NOW, bundle_id='metric-1')
    with pytest.raises(ResourceLimit, match='prompt byte'):
        store.admit('one', second, 'b', 41, NOW, bundle_id='metric-1')
    assert store.admit('two', second, 'b', 100, NOW, bundle_id='metric-1')
    assert store.admit('one', second, 'b', 40, NOW, bundle_id='metric-1')


def test_replay_at_all_limits_does_not_charge_new_bundle_or_month(world):
    ledger, store = world
    ident = signal(ledger, 'first')
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_bundle=1,
        max_requests_per_catchup=1, max_requests_per_month=1, max_attempts_per_signal=1))
    assert store.admit('one', ident, 'a', 100, NOW, bundle_id='metric-1')
    next_month = datetime(2026, 11, 1, tzinfo=UTC)
    assert not store.admit('one', ident, 'a', 100, NOW, bundle_id='metric-2')
    assert not store.admit('one', ident, 'a', 100, next_month, bundle_id='metric-2')
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 1
    assert ledger.conn.execute('SELECT count(*) FROM resource_admissions WHERE month_utc=?',
                               ('2026-11',)).fetchone()[0] == 0


@pytest.mark.parametrize('value', ['', ' ', 'x' * 513, 1])
def test_bundle_and_resume_identifiers_fail_closed(world, value):
    ledger, store = world
    ident = signal(ledger, 'first')
    with pytest.raises(ValueError, match='bundle_id'):
        store.admit('one', ident, 'a', 1, NOW, bundle_id=value)
    with pytest.raises(ValueError, match='current_catchup_id'):
        store.select_pending(current_catchup_id=value)


@pytest.mark.parametrize('value', ['', ' ', 'x' * 513, None])
def test_sql_rejects_empty_missing_or_unbounded_bundle(world, value):
    ledger, _ = world
    ident = signal(ledger, 'first')
    with pytest.raises(sqlite3.IntegrityError):
        ledger.conn.execute('INSERT INTO resource_admissions(catchup_id,request_key,signal_id,prompt_bytes,month_utc,'
                            'admitted_at,bundle_id) VALUES(?,?,?,?,?,?,?)',
                            ('one', 'a', ident, 1, '2026-10', NOW.isoformat(), value))


def test_resume_at_attempt_limit_selects_only_normalized_matching_catchup(world):
    ledger, store = world
    ident = signal(ledger, 'resume')
    ledger.conn.execute("UPDATE signals SET processing_state='normalized' WHERE signal_id=?", (ident,))
    store = AdmissionStore(ledger, replace(store.policy, max_attempts_per_signal=1))
    store.admit('one', ident, 'a', 1, NOW)
    store.cache_output('one', ident, 'a', 'output', NOW)
    assert store.select_pending() == []
    assert store.select_pending(current_catchup_id='other') == []
    assert [row['signal_id'] for row in store.select_pending(current_catchup_id='one')] == [ident]
    assert store.admit('one', ident, 'b', 1, NOW)


def test_scheduler_only_selects_normalized_signals_in_observed_order(world):
    ledger, store = world
    for state in ('fetched', 'extracted', 'excluded', 'reconciled'):
        ident = signal(ledger, state, observed='2026-09-01T00:00:00Z')
        ledger.conn.execute('UPDATE signals SET processing_state=? WHERE signal_id=?', (state, ident))
    later = signal(ledger, 'later', observed='2026-10-01T00:00:00Z')
    earlier = signal(ledger, 'earlier', observed='2026-10-01T01:00:00+02:00')
    for ident in (later, earlier):
        ledger.conn.execute("UPDATE signals SET processing_state='normalized' WHERE signal_id=?", (ident,))
    assert [row['signal_id'] for row in store.select_pending()] == [earlier, later]


def test_scheduler_rotates_ordinary_source_accounts_without_hash_order(world):
    ledger, store = world
    oldest = signal(ledger, 'old', 'mail', 'busy', '2026-09-01T00:00:00Z')
    for index in range(6):
        signal(ledger, f'busy-{index}', 'mail', 'busy')
    quiet = signal(ledger, 'quiet', 'calendar', 'quiet')
    chosen = [row['signal_id'] for row in store.select_pending()]
    assert chosen == [oldest, quiet]


def test_bundle_request_and_cumulative_bytes(world):
    ledger, store = world
    ident = signal(ledger, 'first')
    store = AdmissionStore(ledger, replace(store.policy, max_attempts_per_signal=10))
    store.admit('one', ident, 'a', 60, NOW)
    with pytest.raises(ResourceLimit, match='prompt byte'):
        store.admit('one', ident, 'b', 41, NOW)
    assert store.admit('one', ident, 'b', 40, NOW)
    with pytest.raises(ResourceLimit, match='bundle request'):
        store.admit('one', ident, 'c', 0, NOW)


def test_cache_replay_erasure_and_non_refundable_allocation(world):
    ledger, store = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_month=1))
    store.admit('one', first, 'a', 0, NOW)
    store.cache_output('one', first, 'a', 'secret', NOW)
    store.cache_output('one', first, 'a', 'secret', NOW)
    store.select_pending()
    ledger.conn.execute('DELETE FROM signals WHERE signal_id=?', (first,))
    assert store.cached_output('one', 'a') is None
    assert ledger.conn.execute('SELECT count(*) FROM resource_schedule_signals WHERE signal_id=?',
                               (first,)).fetchone()[0] == 0
    with pytest.raises(ResourceLimit, match='month'):
        store.admit('two', second, 'b', 1, NOW)


@pytest.mark.parametrize('operation', ['insert', 'update'])
def test_sql_rejects_cache_for_wrong_signal(world, operation):
    ledger, store = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    store.admit('one', first, 'a', 1, NOW)
    with pytest.raises(sqlite3.IntegrityError, match='Signal mismatch'):
        if operation == 'insert':
            ledger.conn.execute('INSERT INTO resource_cache VALUES(?,?,?,?,?,?)',
                                ('one', 'a', second, 'encrypted', 1, NOW.isoformat()))
        else:
            store.cache_output('one', first, 'a', 'output', NOW)
            ledger.conn.execute('UPDATE resource_cache SET signal_id=?', (second,))


@pytest.mark.parametrize('value', ['', ' ', 'x' * 513, None, 1])
def test_identifiers_fail_closed(world, value):
    ledger, store = world
    ident = signal(ledger, 'first')
    for operation in (lambda: store.admit(value, ident, 'key', 1, NOW),
                      lambda: store.cached_output('one', value),
                      lambda: store.cancel('one', value),
                      lambda: store.is_cancelled(value),
                      lambda: store.select_pending([value])):
        with pytest.raises(ValueError):
            operation()


def test_naive_clock_bad_output_priority_shape_missing_cipher_and_lock(world):
    ledger, store = world
    ident = signal(ledger, 'first')
    with pytest.raises(ValueError, match='timezone'):
        store.admit('one', ident, 'a', 1, NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match='text'):
        store.cache_output('one', ident, 'a', 1, NOW)
    with pytest.raises(ValueError, match='sequence'):
        store.select_pending('not-a-list')
    store.admit('one', ident, 'a', 1, NOW)
    cipher = ledger._cipher
    ledger._cipher = None
    with pytest.raises(PolicyRequired, match='cipher'):
        store.cache_output('one', ident, 'a', 'result', NOW)
    ledger._cipher = cipher
    store.cache_output('one', ident, 'a', 'result', NOW)
    ledger._cipher = None
    with pytest.raises(PolicyRequired, match='cipher'):
        store.cached_output('one', 'a')
    ledger.locked = True
    with pytest.raises(RuntimeError, match='checkpoint'):
        store.select_pending()


def contender(path, ident, key, ready, start, result, die):
    with opened(Path(path)) as ledger:
        store = AdmissionStore(ledger, ResourcePolicy.from_mapping({**LIMITS, 'max_requests_per_month': 1}))
        if die == 'before':
            transaction = ledger.transaction

            @contextmanager
            def interrupted():
                with transaction() as connection:
                    yield connection
                    os._exit(24)

            ledger.transaction = interrupted
        ready.put(True)
        start.wait(10)
        try:
            admitted = store.admit('race', ident, key, 1, NOW)
            if die:
                os._exit(23)
            result.put(('admitted', admitted))
        except ResourceLimit:
            result.put(('denied', False))


def test_real_process_last_slot_serializes(world):
    ledger, _ = world
    first, second = signal(ledger, 'first'), signal(ledger, 'second')
    context = multiprocessing.get_context('spawn')
    ready, result, start = context.Queue(), context.Queue(), context.Event()
    workers = [context.Process(target=contender, args=(str(ledger._path), ident, str(index),
               ready, start, result, False)) for index, ident in enumerate([first, second])]
    for worker in workers:
        worker.start()
    try:
        for _ in workers:
            assert ready.get(timeout=20)
        start.set()
        assert sorted(result.get(timeout=20)[0] for _ in workers) == ['admitted', 'denied']
    finally:
        for worker in workers:
            worker.join(20)
            if worker.is_alive():
                worker.kill()
                worker.join()
    assert all(worker.exitcode == 0 for worker in workers)


def test_real_process_death_after_admission_does_not_refund_or_duplicate(world):
    ledger, store = world
    ident = signal(ledger, 'first')
    context = multiprocessing.get_context('spawn')
    ready, result, start = context.Queue(), context.Queue(), context.Event()
    worker = context.Process(target=contender, args=(str(ledger._path), ident, 'durable',
                             ready, start, result, True))
    worker.start()
    try:
        assert ready.get(timeout=20)
        start.set()
        worker.join(20)
        assert worker.exitcode == 23
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join()
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_month=1))
    assert store.admit('race', ident, 'durable', 1, NOW) is False
    with pytest.raises(ResourceLimit):
        store.admit('other', ident, 'new', 1, NOW)


def test_real_process_death_before_commit_rolls_back_admission(world):
    ledger, store = world
    ident = signal(ledger, 'first')
    context = multiprocessing.get_context('spawn')
    ready, result, start = context.Queue(), context.Queue(), context.Event()
    worker = context.Process(target=contender, args=(str(ledger._path), ident, 'rolled-back',
                             ready, start, result, 'before'))
    worker.start()
    try:
        assert ready.get(timeout=20)
        start.set()
        worker.join(20)
        assert worker.exitcode == 24
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join()
    store = AdmissionStore(ledger, replace(store.policy, max_requests_per_month=1))
    assert store.admit('race', ident, 'rolled-back', 1, NOW) is True


@pytest.mark.parametrize('file_seal', [False, True])
def test_month_rollover_restore_cannot_repeat_a_lost_prior_month_request(tmp_path, file_seal):
    seal = FileSpendSeal(tmp_path / 'spend.seal') if file_seal else MemorySpendSeal()
    policy = ResourcePolicy.from_mapping(LIMITS)
    next_month = datetime(2026, 11, 1, tzinfo=UTC)
    backup_path = tmp_path / 'backup.db'
    with opened(tmp_path / 'live.db') as ledger:
        ledger.migrate()
        ident = signal(ledger, 'before-snapshot')
        with closing(sqlite3.connect(backup_path)) as backup:
            ledger.conn.backup(backup)
        store = AdmissionStore(ledger, policy, seal)
        assert store.admit('october', ident, 'lost-october', 4, NOW)
        assert store.admit('november', ident, 'lost-november', 4, next_month)
    with opened(backup_path) as restored:
        store = AdmissionStore(restored, policy, seal)
        assert not store.admit('october', ident, 'lost-october', 4, next_month)
        assert not store.admit('november', ident, 'lost-november', 4, next_month)
        assert store.admit('fresh', ident, 'new-november', 4, next_month)
        assert not store.admit('october', ident, 'lost-october', 4, next_month)
        assert store.month_usage('2026-11').requests == 2
