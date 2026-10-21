"""Performance guard rails (re-artha 10.4): generous bounds that catch a quadratic regression, not noise."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import catchup_core.ledger as lg
from catchup_core import crypto, ids
from catchup_core.contracts import FactStatus

TOOL = Path(__file__).resolve().parents[2] / "tools" / "benchmark_core.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("benchmark_core", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_core_cost_scales_roughly_linearly_and_stays_far_inside_the_latency_gate(bench):
    rows = bench.benchmark([30, 240])
    assert all(r["cold"]["status"] == "complete" and r["warm"]["status"] == "complete" for r in rows)
    assert rows[1]["cold"]["seconds"] < 30                                  # the whole gate is 180 s including the model
    verdict = bench.scaling(rows)
    assert verdict["ratio"] is not None and verdict["ratio"] < 4, verdict    # 8x the signals; quadratic would be ~8
    assert rows[1]["cold"]["alerts"] > rows[0]["cold"]["alerts"] > 0        # the work really happened


def test_benchmark_data_is_synthetic_and_deterministic(bench):
    first, second = bench.make_records(40), bench.make_records(40)
    assert [r.text for r in first] == [r.text for r in second]
    assert sum("registration closes" in r.text for r in first) == 10          # one in four carries an obligation
    assert bench.scaling([{"cold_ms_per_signal": 1}])["ratio"] is None


def test_facts_matching_filters_by_obligation_in_the_database(tmp_path):
    key = ids.HmacKey(1, b"k" * 32)
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x05" * 32}))
    ledger = lg.Ledger(tmp_path / "l.db", id_key=key, cipher=cipher, owner_id="owner").open()
    ledger.migrate()
    try:
        for index, obligation in enumerate(("a", "b", "b", "")):
            ledger.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value=f"2026-10-{10 + index}",
                                         observed_at="t", primary_subject_id="kid1", obligation_key=obligation))
        every = ledger.facts_matching("kids_school", "test_date", "kid1")
        only_b = ledger.facts_matching("kids_school", "test_date", "kid1", obligation_key="b")
        blank = ledger.facts_matching("kids_school", "test_date", "kid1", obligation_key="")
        assert len(every) == 4 and {f.obligation_key for f in only_b} == {"b"} and len(only_b) == 2
        assert len(blank) == 1 and blank[0].obligation_key == ""
        assert ledger.facts_matching("kids_school", "test_date", "kid1", obligation_key="zzz") == []
        assert ledger.facts_matching("kids_school", "test_date", "kid2") == []
        ledger.set_fact_status(every[0].fact_id, FactStatus.RETRACTED)
        assert len(ledger.facts_matching("kids_school", "test_date", "kid1")) == 3      # default statuses exclude retracted
    finally:
        ledger.close()


def test_reconciling_one_obligation_decodes_only_that_obligations_facts(tmp_path):
    """Deterministic guard for the quadratic bug: the cost of a lookup must not grow with unrelated obligations."""
    key = ids.HmacKey(1, b"k" * 32)
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x05" * 32}))
    ledger = lg.Ledger(tmp_path / "l.db", id_key=key, cipher=cipher, owner_id="owner").open()
    ledger.migrate()
    try:
        for index in range(100):
            ledger.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
                                         observed_at="t", primary_subject_id="kid1", obligation_key=f"o{index}"))
        calls = []
        original = ledger.get_fact
        ledger.get_fact = lambda *a, **k: calls.append(a[0]) or original(*a, **k)
        found = ledger.facts_matching("kids_school", "test_date", "kid1", obligation_key="o7")
        assert len(found) == 1 and len(calls) == 1                           # not 100
    finally:
        ledger.close()
