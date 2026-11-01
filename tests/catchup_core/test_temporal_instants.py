"""RA-02: historical answers compare instants, including legacy timestamp representations."""
from dataclasses import replace
from datetime import datetime

import pytest

from catchup_core import crypto, ids, ledger as lg


@pytest.fixture
def temporal(tmp_path):
    clock = ['2026-10-24T10:00:00Z']
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b't' * 32}))
    with lg.Ledger(tmp_path / 'ledger.db', id_key=ids.HmacKey(1, b'i' * 32), cipher=cipher,
                   clock=lambda: clock[0]).open() as ledger:
        ledger.migrate()
        yield ledger, clock


def original():
    return lg.FactInput(domain='kids_school', predicate='test_date', value='2026-10-25',
                        observed_at='2026-10-24T10:00:00Z', primary_subject_id='child')


@pytest.mark.parametrize('as_of', ['2026-10-24T03:00:00-07:00', '2026-10-24T10:00:00.000000Z'])
def test_historical_cutoff_accepts_equivalent_instants(temporal, as_of):
    ledger, _ = temporal
    fid = ledger.put_fact(original())
    assert [row['value'] for row in ledger.fact_history(fid, 'owner', as_of)] == ['2026-10-25']


def test_subsecond_writes_keep_history_and_review_order(temporal):
    ledger, clock = temporal
    first = original()
    fid = ledger.put_fact(first)
    clock[0] = '2026-10-24T10:00:00.100000Z'
    ledger.put_fact(replace(first, value='2026-10-26'), fid)
    clock[0] = '2026-10-24T10:00:00.200000Z'
    prior = ledger.get_fact(fid, 'owner').recorded_at
    ledger.put_fact(replace(first, value='2026-10-27'), fid, expected_recorded_at=prior)
    assert [row['value'] for row in ledger.fact_history(fid, 'owner')] == [
        '2026-10-25', '2026-10-26', '2026-10-27']
    assert [row['value'] for row in ledger.fact_history(fid, 'owner', '2026-10-24T10:00:00.150000Z')] == [
        '2026-10-25', '2026-10-26']


def test_clock_offset_and_rollback_do_not_move_version_backwards(temporal):
    ledger, clock = temporal
    first = original()
    clock[0] = '2026-10-24T12:00:00+02:00'
    fid = ledger.put_fact(first)
    before = ledger.get_fact(fid, 'owner').recorded_at
    clock[0] = '2026-10-24T09:59:00Z'
    ledger.put_fact(replace(first, value='2026-10-26'), fid)
    after = ledger.get_fact(fid, 'owner').recorded_at
    assert datetime.fromisoformat(after) > datetime.fromisoformat(before)
    assert [row['value'] for row in ledger.fact_history(fid, 'owner', '2026-10-24T10:00:00Z')] == ['2026-10-25']


@pytest.mark.parametrize('as_of', ['', '2026-10-24', '2026-10-24T10:00:00', 'not-a-time'])
def test_ambiguous_historical_cutoff_is_rejected(temporal, as_of):
    ledger, _ = temporal
    fid = ledger.put_fact(original())
    with pytest.raises(ValueError, match='offset'):
        ledger.fact_history(fid, 'owner', as_of)
