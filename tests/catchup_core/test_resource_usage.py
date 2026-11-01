"""Usage receipts, monthly token bound and restore spend seal; synthetic data, no provider requests."""
from __future__ import annotations

import multiprocessing
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.ids import HmacKey
from catchup_core.ledger import Ledger, SignalInput
from catchup_core.resources import (
    ADOPTED_LIMITS,
    AdmissionStore,
    FileSpendSeal,
    MemorySpendSeal,
    ResourceLimit,
    ResourcePolicy,
    SpendRecord,
    SpendSealError,
)

NOW = datetime(2026, 10, 24, tzinfo=UTC)
NEXT_MONTH = datetime(2026, 11, 2, tzinfo=UTC)
BASE = dict(max_requests_per_month=50, max_pending_signals=4)


def opened(path):
    return Ledger(path, id_key=HmacKey(1, b'i' * 32),
                  cipher=EnvelopeCipher(StaticKeyProvider({1: b'k' * 32}))).open()


def signal(ledger, name, observed='2026-10-01T00:00:00Z'):
    ident = ledger.upsert_signal(SignalInput('mail', 'one', name, '1', observed, name, 'synthetic-blob'))[0]
    ledger.conn.execute("UPDATE signals SET processing_state='normalized' WHERE signal_id=?", (ident,))
    return ident


def policy(**extra):
    return ResourcePolicy.from_mapping({**BASE, **extra})


@pytest.fixture
def world(tmp_path):
    with opened(tmp_path / 'synthetic.db') as ledger:
        ledger.migrate()
        yield ledger


def test_adopted_limits_are_the_defaults_and_month_and_pending_stay_explicit():
    adopted = policy()
    assert (adopted.max_requests_per_bundle, adopted.max_prompt_bytes_per_bundle,
            adopted.max_requests_per_catchup, adopted.max_attempts_per_signal,
            adopted.max_priority_streak) == (16, 512 * 1024, 64, 3, 3)
    assert adopted.max_tokens_per_month is None
    assert ADOPTED_LIMITS['max_requests_per_bundle'] == 16
    for missing in BASE:
        with pytest.raises(ValueError, match='explicit'):
            ResourcePolicy.from_mapping({k: v for k, v in BASE.items() if k != missing})
    with pytest.raises(ValueError):
        ResourcePolicy.from_mapping({**BASE, 'unknown_limit': 1})


@pytest.mark.parametrize('field,bad', [('max_tokens_per_month', 0), ('bytes_per_token', 0),
                                       ('output_tokens_reserve', -1), ('max_tokens_per_month', True),
                                       ('bytes_per_token', 1.5)])
def test_token_policy_fields_fail_closed(field, bad):
    with pytest.raises(ValueError):
        policy(**{field: bad})


def test_admission_records_a_size_based_estimate_that_is_not_usage(world):
    store = AdmissionStore(world, policy(output_tokens_reserve=10))
    ident = signal(world, 'a')
    store.admit('c1', ident, 'k1', 101, NOW)
    row = world.conn.execute('SELECT estimated_tokens FROM resource_admissions').fetchone()
    assert row[0] == 26 + 10
    month = store.month_usage('2026-10')
    assert (month.requests, month.reported_requests, month.unknown_requests) == (1, 0, 1)
    assert month.actual_tokens == 0 and month.estimated_tokens == 36
    assert month.counted_tokens == 36


def test_monthly_token_bound_counts_estimates_until_usage_is_reported(world):
    store = AdmissionStore(world, policy(max_tokens_per_month=100))
    ident = signal(world, 'a')
    store.admit('c1', ident, 'k1', 240, NOW)            # 60 estimated
    with pytest.raises(ResourceLimit, match='token'):
        store.admit('c1', ident, 'k2', 200, NOW)          # 50 more would exceed 100
    store.record_usage('c1', 'k1', 5, 5, 'assistant-session-report', NOW)
    assert store.admit('c1', ident, 'k2', 200, NOW) is True      # actual 10 + 50 <= 100
    month = store.month_usage('2026-10')
    assert (month.actual_tokens, month.estimated_tokens, month.unknown_requests) == (10, 50, 1)


def test_reported_usage_above_the_estimate_consumes_more_allowance(world):
    store = AdmissionStore(world, policy(max_tokens_per_month=100))
    ident = signal(world, 'a')
    store.admit('c1', ident, 'k1', 40, NOW)             # 10 estimated
    store.record_usage('c1', 'k1', 80, 15, 'report', NOW)
    with pytest.raises(ResourceLimit, match='token'):
        store.admit('c1', ident, 'k2', 40, NOW)           # 95 + 10 > 100


def test_missing_usage_is_unknown_never_zero_and_the_bound_is_off_without_a_limit(world):
    store = AdmissionStore(world, policy())
    ident = signal(world, 'a')
    store.admit('c1', ident, 'k1', 4000, NOW)
    month = store.month_usage('2026-10')
    assert month.unknown_requests == 1 and month.actual_tokens == 0 and month.counted_tokens == 1000
    assert store.admit('c1', ident, 'k2', 400000, NOW) is True


def test_usage_receipts_are_immutable_idempotent_and_need_an_admission(world):
    store = AdmissionStore(world, policy())
    ident = signal(world, 'a')
    store.admit('c1', ident, 'k1', 4, NOW)
    store.record_usage('c1', 'k1', 3, 4, 'report', NOW)
    store.record_usage('c1', 'k1', 3, 4, 'report', NOW)
    with pytest.raises(ValueError, match='different'):
        store.record_usage('c1', 'k1', 3, 5, 'report', NOW)
    with pytest.raises(ValueError, match='admission'):
        store.record_usage('c1', 'never-admitted', 1, 1, 'report', NOW)
    for bad in (-1, True, 1.5, 2**40):
        with pytest.raises(ValueError):
            store.record_usage('c1', 'k1', bad, 1, 'report', NOW)
    with pytest.raises(sqlite3.IntegrityError, match='immutable'), world.transaction() as db:
        db.execute('UPDATE resource_usage SET input_tokens=0')
    assert world.conn.execute('SELECT count(*) FROM resource_usage').fetchone()[0] == 1


def test_cancellation_keeps_spend_lists_uncertain_requests_and_still_accepts_usage(world):
    store = AdmissionStore(world, policy(max_requests_per_month=2))
    first, second = signal(world, 'a'), signal(world, 'b')
    store.admit('c1', first, 'k1', 4, NOW)
    store.admit('c1', second, 'k2', 4, NOW)
    store.record_usage('c1', 'k2', 1, 1, 'report', NOW)
    assert store.uncertain_requests('c1') == ['k1']
    store.require_active('c1')
    store.cancel('c1', 'owner')
    with pytest.raises(ResourceLimit, match='cancelled'):
        store.require_active('c1')
    store.record_usage('c1', 'k1', 2, 2, 'late-report', NOW)
    assert store.uncertain_requests('c1') == []
    with pytest.raises(ResourceLimit, match='month'):
        store.admit('c2', first, 'k3', 4, NOW)            # the cancelled Catch-up's spend is not refunded


# ── restore cannot refund spend or grant fresh disclosure ───────────────────

def restored_copy(ledger, target):
    with closing(sqlite3.connect(target)) as copy:
        ledger.conn.backup(copy)
    return opened(target)


def test_restored_older_ledger_cannot_refund_spend_or_repeat_a_disclosed_request(tmp_path):
    seal = MemorySpendSeal()
    limits = policy(max_requests_per_month=3)
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        first, second = signal(live, 'a'), signal(live, 'b')
        store = AdmissionStore(live, limits, seal)
        store.admit('c1', first, 'before-backup', 4, NOW)
        with closing(sqlite3.connect(tmp_path / 'backup.db')) as backup:
            live.conn.backup(backup)
        assert store.admit('c1', second, 'after-backup', 4, NOW) is True
        assert store.admit('c1', second, 'also-after-backup', 4, NOW) is True
    with opened(tmp_path / 'backup.db') as restored:
        again = AdmissionStore(restored, limits, seal)
        assert again.admit('c1', second, 'after-backup', 4, NOW) is False     # already disclosed once
        assert again.admit('c1', second, 'also-after-backup', 4, NOW) is False
        with pytest.raises(ResourceLimit, match='month'):
            again.admit('c1', second, 'brand-new', 4, NOW)                    # 3 used before the restore
        month = again.month_usage('2026-10')
        assert month.requests == 3 and month.uncertain_requests == 2


def test_restore_keeps_estimated_tokens_as_uncertain_spend(tmp_path):
    seal = MemorySpendSeal()
    limits = policy(max_tokens_per_month=100)
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        ident = signal(live, 'a')
        with closing(sqlite3.connect(tmp_path / 'backup.db')) as backup:
            live.conn.backup(backup)
        AdmissionStore(live, limits, seal).admit('c1', ident, 'lost', 320, NOW)      # 80 tokens
    with opened(tmp_path / 'backup.db') as restored:
        store = AdmissionStore(restored, limits, seal)
        with pytest.raises(ResourceLimit, match='token'):
            store.admit('c2', ident, 'new', 120, NOW)                                # 30 more would pass 100
        assert store.month_usage('2026-10').uncertain_tokens == 80


def test_restore_detection_is_idempotent_and_does_not_charge_twice(tmp_path):
    seal = MemorySpendSeal()
    limits = policy()
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        ident = signal(live, 'a')
        with closing(sqlite3.connect(tmp_path / 'backup.db')) as backup:
            live.conn.backup(backup)
        AdmissionStore(live, limits, seal).admit('c1', ident, 'lost', 4, NOW)
    with opened(tmp_path / 'backup.db') as restored:
        store = AdmissionStore(restored, limits, seal)
        store.admit('c2', ident, 'one', 4, NOW)
        store.admit('c2', ident, 'two', 4, NOW)
        assert store.month_usage('2026-10').requests == 3
        assert restored.conn.execute('SELECT count(*) FROM resource_uncertain_spend').fetchone()[0] == 1


def test_a_new_month_is_not_affected_by_last_months_seal(tmp_path):
    seal = MemorySpendSeal()
    limits = policy(max_requests_per_month=1)
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        ident = signal(live, 'a')
        store = AdmissionStore(live, limits, seal)
        store.admit('c1', ident, 'october', 4, NOW)
        assert store.admit('c1', ident, 'november', 4, NEXT_MONTH) is True
        with pytest.raises(ResourceLimit, match='month'):
            store.admit('c1', ident, 'november-2', 4, NEXT_MONTH)


def test_file_seal_is_monotonic_and_fails_closed_when_unreadable(tmp_path):
    path = tmp_path / 'private' / 'spend.seal'
    seal = FileSpendSeal(path)
    assert seal.read() is None
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        ident = signal(live, 'a')
        store = AdmissionStore(live, policy(), seal)
        store.admit('c1', ident, 'one', 4, NOW)
        store.admit('c1', ident, 'two', 4, NOW)
        newest = seal.read()
        assert newest is not None and newest.serial == 2 and newest.requests == 2
        older = type(newest)(1, newest.month, 1, 1, newest.keys)
        seal.advance(older)
        assert seal.read() == newest
        if sys.platform == 'win32':
            from catchup_core.quarantine_fs_windows import open_owner_private_path
            with open_owner_private_path(path) as native_file:
                assert native_file.snapshot().size == path.stat().st_size
        else:
            assert oct(path.stat().st_mode & 0o777) == '0o600'
        path.write_text('not json')
        with pytest.raises(ResourceLimit, match='seal'):
            store.admit('c1', ident, 'three', 4, NOW)


# ── two processes racing the last token allowance ─────────────────────────────

@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows spend-seal custody')
def test_native_seal_refuses_unsafe_historical_target_without_overwriting(tmp_path):
    from catchup_core.resources import SpendRecord, SpendSealError
    path = tmp_path / 'spend.seal'
    original = '{"version":1,"serial":1,"month":"2026-10","requests":1,"tokens":1,"keys":[]}'
    path.write_text(original)
    with pytest.raises(SpendSealError):
        FileSpendSeal(path).advance(SpendRecord(2, '2026-10', 2, 2, frozenset()))
    assert path.read_text() == original
    assert not list(tmp_path.glob('*.tmp'))


def token_contender(path, seal_path, ident, key, ready, start, result):
    with opened(Path(path)) as ledger:
        store = AdmissionStore(ledger, policy(max_tokens_per_month=100), FileSpendSeal(Path(seal_path)))
        ready.put(True)
        start.wait(10)
        try:
            result.put(('admitted' if store.admit('race', ident, key, 320, NOW) else 'repeat'))
        except ResourceLimit:
            result.put('denied')


def test_two_real_processes_racing_the_last_token_allowance_admit_exactly_one(world, tmp_path):
    first, second = signal(world, 'a'), signal(world, 'b')
    context = multiprocessing.get_context('spawn')
    ready, result, start = context.Queue(), context.Queue(), context.Event()
    seal = tmp_path / 'seal' / 'spend.seal'
    workers = [context.Process(target=token_contender, args=(str(world._path), str(seal), ident, str(i),
                                                             ready, start, result))
               for i, ident in enumerate([first, second])]
    for worker in workers:
        worker.start()
    try:
        for _ in workers:
            assert ready.get(timeout=30)
        start.set()
        assert sorted(result.get(timeout=30) for _ in workers) == ['admitted', 'denied']
    finally:
        for worker in workers:
            worker.join(30)
            if worker.is_alive():
                worker.kill()
                worker.join()
    assert all(worker.exitcode == 0 for worker in workers)
    sealed = FileSpendSeal(seal).read()
    assert sealed is not None and sealed.serial == 1 and sealed.tokens == 80 and len(sealed.keys) == 1


def test_adopted_fairness_never_gives_more_than_three_priority_slots_in_a_row(world):
    store = AdmissionStore(world, policy(max_pending_signals=8))
    urgent = [signal(world, f'urgent-{i}', observed=f'2026-10-0{3 + i}T00:00:00Z') for i in range(5)]
    unknown = [signal(world, f'unknown-{i}', observed=f'2026-09-0{1 + i}T00:00:00Z') for i in range(3)]
    chosen = [row['signal_id'] for row in store.select_pending(urgent)]
    assert len(chosen) == 8
    kinds = ['p' if ident in urgent else 'u' for ident in chosen]
    assert 'pppp' not in ''.join(kinds)
    assert [i for i in chosen if i in unknown] == unknown          # unknown content advances oldest first
    assert 'u' in kinds[:4]


@pytest.mark.parametrize('file_seal', [False, True])
def test_seal_keeps_lifetime_keys_and_serial_but_only_newest_month_counters(tmp_path, file_seal):
    seal = FileSpendSeal(tmp_path / 'spend.seal') if file_seal else MemorySpendSeal()
    seal.advance(SpendRecord(5, '2026-11', 2, 20, frozenset({'a'})))
    seal.advance(SpendRecord(9, '2026-10', 9, 90, frozenset({'old'})))
    seal.advance(SpendRecord(4, '2026-11', 1, 10, frozenset({'a'})))
    assert seal.read() == SpendRecord(9, '2026-11', 2, 20, frozenset({'a', 'old'}))
    seal.advance(SpendRecord(1, '2026-12', 1, 1, frozenset({'z'})))
    seal.advance(SpendRecord(2, '2026-10', 99, 999, frozenset({'stale'})))
    assert seal.read() == SpendRecord(9, '2026-12', 1, 1, frozenset({'a', 'old', 'z', 'stale'}))


@pytest.mark.parametrize('file_seal', [False, True])
def test_old_month_seal_with_newer_ledger_keeps_all_keys_and_current_month_spend(tmp_path, file_seal):
    seal = MemorySpendSeal()
    limits = policy(max_requests_per_month=2, max_tokens_per_month=100)
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        ident = signal(live, 'a')
        with restored_copy(live, tmp_path / 'older.db') as older:
            store = AdmissionStore(live, limits, seal)
            assert store.admit('october', ident, 'first', 40, NOW)
            old_record = seal.read()
            assert store.admit('october', ident, 'second', 40, NOW)
            assert store.admit('november', ident, 'current', 40, NEXT_MONTH)
            stale = FileSpendSeal(tmp_path / 'stale.seal') if file_seal else MemorySpendSeal()
            stale.advance(old_record)
            current = AdmissionStore(live, limits, stale)
            current.record_usage('october', 'first', 80, 10, 'late receipt', NEXT_MONTH)
            current.record_usage('october', 'first', 80, 10, 'late receipt', NEXT_MONTH)
            assert stale.read().month == '2026-11'
            assert stale.read().serial == 4
            assert (stale.read().requests, stale.read().tokens, len(stale.read().keys)) == (1, 10, 3)
            restored = AdmissionStore(older, limits, stale)
            assert not restored.admit('october', ident, 'second', 40, NEXT_MONTH)
            assert not restored.admit('november', ident, 'current', 40, NEXT_MONTH)
            assert restored.admit('fresh', ident, 'last-slot', 40, NEXT_MONTH)
            with pytest.raises(ResourceLimit, match='month'):
                restored.admit('fresh', ident, 'over-cap', 4, NEXT_MONTH)
            usage = restored.month_usage('2026-11')
            assert (usage.requests, usage.counted_tokens, usage.uncertain_requests) == (2, 20, 1)


@pytest.mark.parametrize('operation', ['read', 'receipt'])
@pytest.mark.parametrize('limit', ['request', 'token'])
def test_restored_read_or_old_receipt_cannot_hide_current_month_spend(tmp_path, operation, limit):
    seal = MemorySpendSeal()
    limits = policy(max_requests_per_month=1 if limit == 'request' else 50, max_tokens_per_month=100)
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        ident = signal(live, 'a')
        store = AdmissionStore(live, limits, seal)
        assert store.admit('october', ident, 'before-backup', 4, NOW)
        with restored_copy(live, tmp_path / 'older.db') as older:
            assert store.admit('november', ident, 'lost', 320, NEXT_MONTH)
            restored = AdmissionStore(older, limits, seal)
            if operation == 'receipt':
                restored.record_usage('october', 'before-backup', 1, 1, 'receipt', NEXT_MONTH)
                serial = seal.read().serial
                restored.record_usage('october', 'before-backup', 1, 1, 'receipt', NEXT_MONTH)
                assert seal.read().serial == serial
                assert seal.read().month == '2026-11'
                assert (seal.read().requests, seal.read().tokens) == (1, 80)
            usage = restored.month_usage('2026-11')
            assert (usage.requests, usage.counted_tokens, usage.uncertain_requests) == (1, 80, 1)
            with pytest.raises(ResourceLimit, match='month.*' + ('reservation' if limit == 'request' else 'token')):
                restored.admit('fresh', ident, 'over-cap', 120, NEXT_MONTH)
            assert not restored.admit('november', ident, 'lost', 320, NEXT_MONTH)
            assert restored.month_usage('2026-11') == usage


@pytest.mark.parametrize('content', ['{"version":2,"serial":1,"month":"2026-10","requests":1,"tokens":1,"keys":[]}',
                                     '{"version":1,"serial":1,"month":"2026-10","requests":1,"tokens":1,"keys":"x"}',
                                     '{"version":1,"serial":1,"month":"2026","requests":1,"tokens":1,"keys":[]}',
                                     '{"version":1,"serial":-1,"month":"2026-10","requests":1,"tokens":1,"keys":[]}',
                                     '{"version":1}', '[]', 'not json'])
def test_malformed_seal_fails_closed(tmp_path, content):
    path = tmp_path / 'spend.seal'
    path.write_text(content)
    with pytest.raises(SpendSealError):
        FileSpendSeal(path).read()


def test_unreadable_busy_or_unwritable_seal_fails_closed_without_losing_the_reservation(tmp_path, monkeypatch):
    from catchup_core import resources
    with pytest.raises(SpendSealError, match='unreadable'):
        FileSpendSeal(tmp_path).read()                                          # a directory, not a file
    record = SpendRecord(1, '2026-10', 1, 1, frozenset())
    blocker = tmp_path / 'blocker'
    blocker.write_text('a file where a directory is needed')
    with pytest.raises(SpendSealError, match='written'):
        FileSpendSeal(blocker / 'spend.seal').advance(record)

    def busy(_path):
        raise resources.SessionBusy('held')

    clock = iter([0.0, 0.0, 100.0, 100.0])
    with monkeypatch.context() as patched:
        patched.setattr(resources, 'session_lock', busy)
        patched.setattr(resources.time, 'monotonic', lambda: next(clock))
        patched.setattr(resources.time, 'sleep', lambda _seconds: None)
        with pytest.raises(SpendSealError, match='busy'):
            FileSpendSeal(tmp_path / 'late.seal').advance(record)
    with opened(tmp_path / 'live.db') as ledger:
        ledger.migrate()
        ident = signal(ledger, 'a')
        store = AdmissionStore(ledger, policy(), FileSpendSeal(tmp_path / 'ok' / 'spend.seal'))
        with monkeypatch.context() as patched:
            patched.setattr(FileSpendSeal, '_write', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
            with pytest.raises(ResourceLimit, match='seal could not be updated'):
                store.admit('c1', ident, 'k1', 4, NOW)
        assert ledger.conn.execute('SELECT count(*) FROM resource_admissions').fetchone()[0] == 1
        assert store.admit('c1', ident, 'k1', 4, NOW) is False       # the committed reservation is not repeated


def test_cached_output_must_be_text(world):
    store = AdmissionStore(world, policy())
    ident = signal(world, 'a')
    store.admit('c1', ident, 'k1', 4, NOW)
    aad = store._aad('c1', ident, 'k1')
    encrypted, version = world._cipher.encrypt_json({'not': 'text'}, aad)
    world.conn.execute("INSERT INTO resource_cache VALUES('c1','k1',?,?,?,'2026-10-24T00:00:00+00:00')",
                       (ident, encrypted, version))
    with pytest.raises(ValueError, match='not text'):
        store.cached_output('c1', 'k1')


def test_month_usage_and_usage_inputs_fail_closed(world):
    store = AdmissionStore(world, policy())
    with pytest.raises(ValueError):
        store.month_usage('')
    with pytest.raises(ValueError):
        store.uncertain_requests('')
    with pytest.raises(ValueError):
        store.require_active(' ')
    assert store.month_usage('2026-10').counted_tokens == 0


def test_retry_after_a_failed_seal_write_seals_the_prior_reservation_before_returning(tmp_path, monkeypatch):
    """Given a reservation committed but not sealed, when admit is retried, then the seal catches up or it refuses."""
    seal = FileSpendSeal(tmp_path / 'ok' / 'spend.seal')
    with opened(tmp_path / 'live.db') as ledger:
        ledger.migrate()
        ident = signal(ledger, 'a')
        store = AdmissionStore(ledger, policy(), seal)
        with monkeypatch.context() as patched:
            patched.setattr(FileSpendSeal, '_write', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
            with pytest.raises(ResourceLimit, match='seal could not be updated'):
                store.admit('c1', ident, 'k1', 4, NOW)
        assert seal.read() is None
        assert store.admit('c1', ident, 'k1', 4, NOW) is False          # prior row, but now sealed
        assert seal.read() is not None and seal.read().requests == 1
        with monkeypatch.context() as patched:
            other = signal(ledger, 'b')
            patched.setattr(FileSpendSeal, '_write', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
            with pytest.raises(ResourceLimit):
                store.admit('c1', other, 'k2', 4, NOW)
            with pytest.raises(ResourceLimit, match='seal could not be updated'):
                store.admit('c1', other, 'k2', 4, NOW)                  # retry still cannot return False unsealed


def test_first_seal_write_happens_inside_registry_enrollment_and_later_writes_do_not(tmp_path):
    from contextlib import contextmanager
    events = []
    path = tmp_path / 'seal' / 'spend.json'

    @contextmanager
    def enroll():
        events.append(('pending', path.exists()))
        yield
        events.append(('active', path.exists()))

    seal = FileSpendSeal(path, enroll=enroll)
    seal.advance(SpendRecord(1, '2026-10', 1, 1, frozenset()))
    seal.advance(SpendRecord(2, '2026-10', 2, 2, frozenset()))
    assert events == [('pending', False), ('active', True)]


def test_seal_is_not_written_when_the_registry_refuses_enrollment(tmp_path):
    from catchup_core.recovery_registry import RegistryError
    path = tmp_path / 'spend.json'

    def enroll():
        raise RegistryError('registry busy')

    with pytest.raises(SpendSealError, match='registry'):
        FileSpendSeal(path, enroll=enroll).advance(SpendRecord(1, '2026-10', 1, 1, frozenset()))
    assert not path.exists()


def test_missing_previously_registered_spend_seal_is_not_a_fresh_budget(tmp_path):
    seal = FileSpendSeal(tmp_path / 'missing.json', require_existing=lambda: True)
    with pytest.raises(SpendSealError, match='missing'):
        seal.read()


def test_unregistered_new_installation_can_create_its_first_spend_seal(tmp_path):
    seal = FileSpendSeal(tmp_path / 'new.json', require_existing=lambda: False)
    assert seal.read() is None
    seal.advance(SpendRecord(1, '2026-10', 1, 1, frozenset()))
    assert seal.read().requests == 1


def test_usage_above_estimate_survives_restoring_an_older_ledger(tmp_path):
    seal = MemorySpendSeal()
    limits = policy(max_tokens_per_month=50)
    with opened(tmp_path / 'live.db') as live:
        live.migrate()
        sid = signal(live, 'a')
        store = AdmissionStore(live, limits, seal)
        store.admit('c1', sid, 'estimated', 40, NOW)
        with restored_copy(live, tmp_path / 'older.db') as restored:
            store.record_usage('c1', 'estimated', 80, 20, 'synthetic-receipt', NOW)
            again = AdmissionStore(restored, limits, seal)
            with pytest.raises(ResourceLimit, match='token'):
                again.admit('c2', sid, 'new', 4, NOW)
            assert again.month_usage('2026-10').counted_tokens >= 100


def test_usage_seal_failure_is_repaired_by_exact_receipt_retry(world):
    class FlakySeal(MemorySpendSeal):
        failing = False

        def advance(self, record):
            if self.failing:
                raise SpendSealError('synthetic seal failure')
            super().advance(record)

    seal = FlakySeal()
    store = AdmissionStore(world, policy(), seal)
    sid = signal(world, 'a')
    store.admit('c1', sid, 'r1', 40, NOW)
    seal.failing = True
    with pytest.raises(ResourceLimit, match='seal could not be updated'):
        store.record_usage('c1', 'r1', 90, 10, 'receipt', NOW)
    assert store.month_usage('2026-10').actual_tokens == 100
    seal.failing = False
    store.record_usage('c1', 'r1', 90, 10, 'receipt', NOW)
    assert seal.read().tokens == 100
